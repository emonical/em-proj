"""Mailbox ops core for `em-proj message` — no typer imports.

Implements the Phase 9 per-session durable mailbox transport using Redis Streams.

Key namespace:
  mbox:<session_id>  — machine-global, mirrors state:session: scope convention.
  Distinct from state:* prefixes (state:session:, state:claim:, state:lock:,
  state:reserve:); no namespace collision.

Storage shape (Redis Stream):
  One stream per session mailbox. Each entry has a single field 'payload'
  whose value is a JSON-encoded string. Single-field JSON (mirrors lock.py
  pattern) rather than multi-field stream entries — easier to evolve, decoded
  once in _decode_entry.

MBOX-04 record fields injected at read time by _decode_entry:
  msg_id        — Redis stream entry ID (e.g. '1717500000000-0'); injected at
                  read time from the XRANGE tuple, NOT stored in the payload.
  from_session  — session_id of the sender
  pattern       — message pattern ('direct', 'broadcast', 'topic')
  scope         — delivery scope ('project', 'upstream', 'machine')
  topic         — topic string or None (None for non-topic patterns)
  body          — message body string
  sent_at       — float epoch at send time
  ttl           — int seconds; uniform mailbox TTL for MINID trim (Phase 9)

Rationale for no consumer groups:
  Single consumer per mailbox (the owning session). Consumer groups (XREADGROUP
  + XACK + PEL management) solve multi-consumer fan-out with at-least-once delivery;
  that complexity has no benefit for a single-consumer mailbox. Consumer groups can
  be layered in Phase 11+ if multi-process at-least-once semantics are needed.
  Per 09-RESEARCH.md section "Consumption / Ack Model".

Prohibited imports (enforced by tests/unit/test_mailbox.py and structural tests):
  typer, multiprocessing, threading
"""
from __future__ import annotations

import json
import time

from em_proj.redis_client import get_client
from em_proj.state.kv import ValidationError

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Key prefix for all session mailboxes. Full key: "mbox:<session_id>".
MBOX_KEY_PREFIX: str = "mbox:"

#: Approximate count bound applied via XADD maxlen on every write.
#: Approximate trim (approximate=True) is more efficient; ~10% overage allowed.
MBOX_MAXLEN: int = 500

#: Key-level EXPIRE in seconds for the stream key. Refreshed on each XADD.
#: Also used as the age cutoff for MINID trim in mailbox_inbox.
MBOX_TTL_SECONDS: int = 3600

#: Maximum allowed body length in characters. Mirrors claim.py MAX_REASON_CHARS
#: pattern. Raised as ValidationError before any Redis call (T-09-02-01).
MAX_BODY_CHARS: int = 4096


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MailboxError(Exception):
    """Raised for mailbox-level errors (e.g. write to non-existent mailbox guard).

    code = "mailbox_error" — machine-readable, for future verb-layer dispatch.
    Currently unused in Phase 9; reserved for Phase 10+ guard conditions.
    """

    code: str = "mailbox_error"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_mbox_key(session_id: str) -> str:
    """Build the full Redis key for a session mailbox.

    Key shape: MBOX_KEY_PREFIX + session_id
    Example: "mbox:550e8400-e29b-41d4-a716-446655440000"
    """
    return MBOX_KEY_PREFIX + session_id


def _decode_entry(entry_id: str, fields: dict) -> dict:  # type: ignore[type-arg]
    """Decode one stream entry tuple into the MBOX-04 record dict.

    Deserializes the 'payload' JSON field and injects msg_id from the stream
    entry ID. msg_id is NOT stored in the payload at write time (Option A from
    09-RESEARCH.md msg_id Generation section) — it is injected here at read time
    from the XRANGE tuple's first element.

    Args:
        entry_id: The Redis stream entry ID string (e.g. "1717500000000-0").
                  This becomes the msg_id field in the returned dict.
        fields:   The stream entry's field-value dict from XRANGE/XREAD.
                  Must contain a 'payload' key with JSON-encoded message data.

    Returns:
        dict with all 8 MBOX-04 fields: msg_id, from_session, pattern, scope,
        topic, body, sent_at, ttl.
    """
    payload = json.loads(fields["payload"])
    payload["msg_id"] = entry_id  # inject stream ID as the canonical msg_id
    return payload


def _validate_body(body: str) -> None:
    """Raise ValidationError if body exceeds MAX_BODY_CHARS.

    Mirrors _validate_reason in claim.py. Enforces T-09-02-01 (body length cap).
    Called before any Redis operation in mbox_write.
    """
    if len(body) > MAX_BODY_CHARS:
        raise ValidationError(
            code="validation_error",
            message=f"body exceeds {MAX_BODY_CHARS} characters",
        )


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def mbox_write(session_id: str, msg: dict) -> str:  # type: ignore[type-arg]
    """Write a message to the session's mailbox. Returns the msg_id (stream entry ID).

    Validates body length, writes to Redis Stream mbox:<session_id> using XADD
    with MAXLEN approximate trim, then refreshes the key EXPIRE. The XADD return
    value IS the msg_id — it is not stored in the payload (injected at read time
    by _decode_entry per 09-RESEARCH.md Option A).

    Bounded growth: MAXLEN=MBOX_MAXLEN (~500 entries) applied on every write.
    TTL refresh: EXPIRE called after every XADD (Pitfall 5 — XADD does NOT
    extend the TTL automatically).

    Connection errors are NOT caught here (D-18 carry from claim.py). The verb
    layer calls die_if_redis_unreachable(client) before any ops call.

    Args:
        session_id: The recipient session's mailbox ID (bare suffix for mbox: key).
        msg: Message dict with keys: from_session, pattern, scope, topic, body,
             sent_at, ttl. msg_id is NOT expected here — it is returned.

    Returns:
        str: The Redis stream entry ID, which is the canonical msg_id for this
             message (e.g. "1717500000000-0").

    Raises:
        ValidationError: If msg["body"] exceeds MAX_BODY_CHARS characters.
    """
    _validate_body(msg["body"])

    client = get_client()
    key = _build_mbox_key(session_id)

    # Build payload without msg_id (Option A: inject at read time in _decode_entry)
    payload = {
        "from_session": msg["from_session"],
        "pattern": msg["pattern"],
        "scope": msg["scope"],
        "topic": msg.get("topic"),  # None for non-topic patterns
        "body": msg["body"],
        "sent_at": msg["sent_at"],
        "ttl": msg["ttl"],
    }

    entry_id = client.xadd(
        key,
        fields={"payload": json.dumps(payload, default=str)},
        maxlen=MBOX_MAXLEN,
        approximate=True,
    )
    # Always refresh EXPIRE after XADD to keep active mailboxes alive (Pitfall 5).
    client.expire(key, MBOX_TTL_SECONDS)
    return entry_id  # This IS the msg_id


def mailbox_inbox(
    session_id: str,
    since: str | None = None,
    peek: bool = False,
) -> list:  # type: ignore[type-arg]
    """Read messages from the session's mailbox.

    Applies lazy MINID age trim first (approximate — does not guarantee exact
    age expiry, Pitfall 2), then XRANGE, then optionally XDEL.

    peek=True: XRANGE without XDEL; stream unchanged (MBOX-02 --peek).
    peek=False: XRANGE then XDEL consumed IDs (consume-ack pattern).
    since=<id>: XRANGE min='(<id)' for exclusive range; returns entries strictly
                after <id> (MBOX-02 --since). See implementation note below.

    Non-atomicity of XRANGE + XDEL: at-most-once on crash (fire-and-forget
    semantics accepted for Phase 9). If exactly-once is needed for Phase 11,
    wrap read+delete in a Lua script.

    Args:
        session_id: The session whose mailbox to read.
        since:      Optional stream entry ID. If provided, returns only entries
                    strictly after this ID (exclusive lower bound).
        peek:       If True, do not consume (XDEL) the returned entries.

    Returns:
        list[dict]: Ordered list of MBOX-04 record dicts, ascending by msg_id.
                    Empty list if mailbox is absent or has no qualifying entries.
    """
    client = get_client()
    key = _build_mbox_key(session_id)

    # Lazy age-based trim: remove entries older than MBOX_TTL_SECONDS.
    # Uses stream entry ID timestamp prefix for age comparison (Pitfall 2: approximate).
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - (MBOX_TTL_SECONDS * 1000)
    client.xtrim(key, minid=f"{cutoff_ms}-0", approximate=True)

    # Build min_id for XRANGE.
    # '(' exclusive prefix path: redis-py 7.4.0 passes '(' through to Redis XRANGE
    # unmodified. Confirmed GREEN by test_since_excludes_already_seen probe per
    # 09-RESEARCH.md Open Question 1. If that test fails, the Lua fallback below
    # would be used instead:
    #   LUA_MBOX_XRANGE_SINCE = "return redis.call('XRANGE', KEYS[1], '(' .. ARGV[1], '+')"
    #   entries = client.eval(LUA_MBOX_XRANGE_SINCE, 1, key, since)
    if since is None:
        min_id = "-"  # from the beginning
    else:
        min_id = f"({since}"  # exclusive '(' prefix — Redis 6.2+ XRANGE feature

    entries = client.xrange(key, min=min_id, max="+")

    messages = [_decode_entry(eid, fields) for eid, fields in entries]

    if not peek and entries:
        ids_to_delete = [eid for eid, _ in entries]
        client.xdel(key, *ids_to_delete)

    return messages


def mbox_blocking_read(
    session_id: str,
    last_id: str,
    block_ms: int = 5000,
) -> list:  # type: ignore[type-arg]
    """Block until new entries arrive after last_id. Used by Phase 11 listener daemon.

    Returns immediately if entries exist after last_id. Returns [] on timeout
    (block_ms elapsed with no entries).

    Does NOT consume (no XDEL) — the daemon is responsible for drain-to-mailbox
    semantics. This is a non-destructive tail read for Phase 11's DAEMON-01 use case.

    Args:
        session_id: The session whose mailbox to tail.
        last_id:    The last stream entry ID seen by the caller. XREAD returns
                    entries strictly after this ID (exclusive by protocol).
        block_ms:   Milliseconds to block waiting for new entries. 0 = block
                    indefinitely; non-zero = timeout (returns [] on expiry).

    Returns:
        list[dict]: List of new MBOX-04 record dicts since last_id.
                    Empty list on timeout.
    """
    client = get_client()
    key = _build_mbox_key(session_id)
    result = client.xread(
        streams={key: last_id},
        count=10,
        block=block_ms,
    )
    if not result:
        return []
    # result shape: [[stream_name, [(id, fields), ...]]]
    _, entries = result[0]
    return [_decode_entry(eid, fields) for eid, fields in entries]
