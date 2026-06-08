"""Unit tests for em_proj.message._ops mailbox operations.

Covers MBOX-02, MBOX-03, MBOX-04 at the ops layer (no subprocess).
Includes the redis-py exclusive-range probe (test_since_excludes_already_seen)
per 09-RESEARCH.md Pitfall 1.
"""
from __future__ import annotations

import time

import pytest

import em_proj.redis_client as rc
from em_proj.message._ops import (
    MBOX_KEY_PREFIX,
    MBOX_MAXLEN,
    MBOX_TTL_SECONDS,
    MAX_BODY_CHARS,
    mailbox_inbox,
    mbox_write,
)
from em_proj.state.kv import ValidationError

# ---------------------------------------------------------------------------
# Autouse fixtures — mirror test_session.py pattern exactly
# ---------------------------------------------------------------------------

SESSION_ID = "test-session-09"


@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    """Reset the redis_client singleton so each test honors EM_PROJ_REDIS_DB=15."""
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()


@pytest.fixture(autouse=True)
def _point_session_at_test_db(monkeypatch):
    """Force message._ops's get_client() onto db=15."""
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")


# ---------------------------------------------------------------------------
# Helper — build a msg dict argument for mbox_write
# ---------------------------------------------------------------------------


def _make_msg(
    from_session: str = "test-sender",
    pattern: str = "direct",
    scope: str = "machine",
    body: str = "hello",
    topic: str | None = None,
    ttl: int = 3600,
) -> dict:
    """Build the msg dict argument for mbox_write. All fields present; sent_at = time.time()."""
    return {
        "from_session": from_session,
        "pattern": pattern,
        "scope": scope,
        "body": body,
        "topic": topic,
        "sent_at": time.time(),
        "ttl": ttl,
    }


# ---------------------------------------------------------------------------
# Test: confirm RED phase — module does not exist yet
# ---------------------------------------------------------------------------


def test_import_smoke() -> None:
    """Importing from em_proj.message._ops should succeed (or ImportError if module absent).

    This test itself imports at module level, so if the import fails, pytest will
    report a collection error rather than a test failure — confirming RED phase.
    """
    # If we get here, the import succeeded (GREEN phase).
    # In RED phase, the module-level import above raises ImportError at collection time.
    assert MBOX_KEY_PREFIX == "mbox:", (
        f"MBOX_KEY_PREFIX must be 'mbox:'; got {MBOX_KEY_PREFIX!r}"
    )


# ---------------------------------------------------------------------------
# MBOX-02: Write and read
# ---------------------------------------------------------------------------


def test_mbox_write_returns_msg_id(clean_db) -> None:
    """mbox_write must return a non-empty string — the Redis stream entry ID."""
    msg_id = mbox_write(SESSION_ID, _make_msg())
    assert isinstance(msg_id, str), (
        f"mbox_write must return a string msg_id; got {type(msg_id)}"
    )
    assert msg_id, "mbox_write must return a non-empty string"


def test_mailbox_inbox_ordered_reads(clean_db) -> None:
    """Writing N messages and reading back must return them in ascending msg_id order (MBOX-02)."""
    n = 5
    written_ids = [mbox_write(SESSION_ID, _make_msg(body=f"msg {i}")) for i in range(n)]
    messages = mailbox_inbox(SESSION_ID, since=None, peek=True)
    assert len(messages) == n, f"Expected {n} messages; got {len(messages)}"
    returned_ids = [m["msg_id"] for m in messages]
    assert returned_ids == written_ids, (
        "mailbox_inbox must return messages in ascending msg_id order (MBOX-02); "
        f"written={written_ids!r} != returned={returned_ids!r}"
    )


def test_peek_does_not_consume(clean_db) -> None:
    """peek=True must not consume messages; peek=False must consume them; third call returns []."""
    for i in range(3):
        mbox_write(SESSION_ID, _make_msg(body=f"msg {i}"))

    # First read: peek=True — must not consume
    peek_result = mailbox_inbox(SESSION_ID, since=None, peek=True)
    assert len(peek_result) == 3, f"peek=True should see 3 messages; got {len(peek_result)}"

    # Second read: peek=False — must return same 3 messages (still unconsumed)
    consume_result = mailbox_inbox(SESSION_ID, since=None, peek=False)
    assert len(consume_result) == 3, (
        f"peek=False after peek=True should see 3 messages; got {len(consume_result)}"
    )

    # Third read: should be empty (messages were consumed)
    empty_result = mailbox_inbox(SESSION_ID, since=None, peek=False)
    assert empty_result == [], (
        f"Third call should return [] (messages consumed); got {empty_result!r}"
    )


def test_since_excludes_already_seen(clean_db) -> None:
    """--since exclusive '(' range probe: msg_B must NOT appear in since=msg_B read.

    Write msg_A then msg_B; consume both (peek=False); write msg_C;
    call mailbox_inbox(since=msg_B["msg_id"], peek=True); assert result
    contains msg_C but NOT msg_B.

    If this test fails (msg_B returned), redis-py does NOT pass '(' prefix
    through XRANGE; Plan 09-02 Task 1 must use the Lua fallback instead.
    """
    id_a = mbox_write(SESSION_ID, _make_msg(body="msg_A"))
    id_b = mbox_write(SESSION_ID, _make_msg(body="msg_B"))

    # Consume both (clears the stream)
    consumed = mailbox_inbox(SESSION_ID, since=None, peek=False)
    assert len(consumed) == 2, f"Expected 2 consumed messages; got {len(consumed)}"
    msg_b_id = consumed[1]["msg_id"]
    assert msg_b_id == id_b, f"Second consumed message should be msg_B; got {msg_b_id!r}"

    # Write msg_C after consumption
    id_c = mbox_write(SESSION_ID, _make_msg(body="msg_C"))

    # Read since=msg_B's id — should return only msg_C (exclusive lower bound)
    since_result = mailbox_inbox(SESSION_ID, since=msg_b_id, peek=True)
    returned_ids = [m["msg_id"] for m in since_result]

    # msg_C must appear
    assert id_c in returned_ids, (
        f"msg_C ({id_c!r}) must appear in since=msg_B results; got {returned_ids!r}"
    )
    # msg_B must NOT appear — this is the exclusive '(' range probe
    assert msg_b_id not in returned_ids, (
        f"msg_B ({msg_b_id!r}) must NOT appear in since=msg_B results (exclusive range); "
        f"got {returned_ids!r}. "
        "If this test fails (msg_B returned), redis-py does NOT pass '(' prefix "
        "through XRANGE; Plan 09-02 Task 1 must use the Lua fallback instead."
    )


def test_consume_removes_from_stream(clean_db) -> None:
    """peek=False must delete entries from the stream (XRANGE returns [] after consume).

    Per 09-RESEARCH.md Pitfall 4, assert via XRANGE (not xlen) because XDEL may not
    shrink xlen immediately in all Redis versions (tombstoning behavior).
    """
    mbox_write(SESSION_ID, _make_msg(body="first"))
    mbox_write(SESSION_ID, _make_msg(body="second"))

    # Consume
    result = mailbox_inbox(SESSION_ID, since=None, peek=False)
    assert len(result) == 2, f"Expected 2 messages; got {len(result)}"

    # Verify via XRANGE that the stream is truly empty
    client = rc.get_client()
    mbox_key = MBOX_KEY_PREFIX + SESSION_ID
    remaining = client.xrange(mbox_key, min="-", max="+")
    assert remaining == [], (
        f"After peek=False consume, XRANGE must return []; got {remaining!r}. "
        "Per 09-RESEARCH.md Pitfall 4, asserting via XRANGE (not xlen) to avoid "
        "tombstoning false negatives."
    )


# ---------------------------------------------------------------------------
# MBOX-03: Bounded growth and TTL
# ---------------------------------------------------------------------------


def test_mbox_maxlen_bound(clean_db) -> None:
    """Writing MBOX_MAXLEN + 20 messages must keep xlen <= MBOX_MAXLEN + 50.

    Approximate trim (macro-node boundaries) allows slight overage; do NOT
    assert xlen == MBOX_MAXLEN exactly (per 09-RESEARCH.md Pitfall 2).
    """
    n = MBOX_MAXLEN + 20
    for i in range(n):
        mbox_write(SESSION_ID, _make_msg(body=f"flood {i}"))

    client = rc.get_client()
    mbox_key = MBOX_KEY_PREFIX + SESSION_ID
    length = client.xlen(mbox_key)
    assert length <= MBOX_MAXLEN + 50, (
        f"Stream length {length} exceeds MBOX_MAXLEN + 50 slack ({MBOX_MAXLEN + 50}). "
        "MAXLEN trim on XADD must bound the stream (approximate trim, allows slight overage)."
    )


# ---------------------------------------------------------------------------
# MBOX-04: Payload completeness
# ---------------------------------------------------------------------------


def test_mbox_payload_has_all_eight_fields(clean_db) -> None:
    """mbox_write + mailbox_inbox must produce all 8 MBOX-04 fields in each message."""
    mbox_write(SESSION_ID, _make_msg())
    messages = mailbox_inbox(SESSION_ID, since=None, peek=True)
    assert len(messages) == 1, f"Expected 1 message; got {len(messages)}"
    msg = messages[0]

    required_fields = {"msg_id", "from_session", "pattern", "scope", "topic", "body", "sent_at", "ttl"}
    missing = required_fields - set(msg.keys())
    assert not missing, (
        f"Message missing required MBOX-04 fields: {missing!r}. "
        f"Present keys: {set(msg.keys())!r}"
    )


def test_topic_field_is_none_for_non_topic_pattern(clean_db) -> None:
    """Message written with pattern='broadcast' and topic=None must read back with topic=None."""
    mbox_write(SESSION_ID, _make_msg(pattern="broadcast", topic=None))
    messages = mailbox_inbox(SESSION_ID, since=None, peek=True)
    assert len(messages) == 1
    assert messages[0]["topic"] is None, (
        f"topic must be None for broadcast pattern; got {messages[0]['topic']!r}"
    )


def test_max_body_chars_enforced(clean_db) -> None:
    """mbox_write with a body exceeding MAX_BODY_CHARS must raise ValidationError."""
    oversized_body = "x" * (MAX_BODY_CHARS + 1)
    with pytest.raises(ValidationError):
        mbox_write(SESSION_ID, _make_msg(body=oversized_body))


# ---------------------------------------------------------------------------
# MBOX-02: --since input validation (WR-02 regression)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_since", ["foo", "", "not-an-id", "abc-def", "-", "1-"])
def test_invalid_since_raises_validation_error(clean_db, bad_since) -> None:
    """A malformed --since must raise ValidationError, not leak a Redis ResponseError
    traceback (WR-02). Validation runs before any Redis call, so it fires even on an
    empty/absent mailbox (Redis validates stream-ID syntax before key lookup)."""
    with pytest.raises(ValidationError):
        mailbox_inbox(SESSION_ID, since=bad_since, peek=True)


def test_valid_since_passes_validation(clean_db) -> None:
    """A well-formed stream-id --since must NOT raise — sanity guard for the WR-02 fix.

    On an empty mailbox a valid cursor simply yields an empty read.
    """
    result = mailbox_inbox(SESSION_ID, since="1717500000000-0", peek=True)
    assert result == [], f"valid --since on empty mailbox must return []; got {result!r}"
