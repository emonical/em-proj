"""Session registry core module for `em-proj session` — no typer imports.

Implements the Phase 8 session registry. Each calling process registers itself
under a machine-global key (state:session:<session_id>) stored as a Redis HASH
with 9 fields.

Key namespace:
  state:session:<session_id>  — machine-global (NOT project- or upstream-scoped).
  This enables cross-project session listing required for Phase 10 messaging
  scope filtering (which selects on project_hash/upstream_identity fields
  already present in each record).

Storage shape (9 fields per SESS-01):
  session_id        — CLAUDE_CODE_SESSION_ID or pid-<pid> fallback
  project_hash      — cwd-as-dashes (same convention as lock/claim)
  upstream_identity — canonical host:owner/repo string or project_hash fallback
  pid               — os.getpid() at registration time
  proc_start_epoch  — psutil.Process().create_time() at registration time
  boot_id           — 16-hex-char boot identifier from identity.py
  cwd               — os.getcwd() at registration time
  registered_at     — time.time() at first registration (preserved on upsert, D5)
  last_heartbeat    — time.time() at each register/heartbeat call

Lua atomicity rationale:
  LUA_SESSION_UPSERT and LUA_SESSION_HEARTBEAT run server-side in one Redis
  command slot, eliminating TOCTOU races between read and write (same rationale
  as LUA_CLAIM_REFRESH_OR_TAKE in claim.py and LUA_RESERVE_REFRESH_OR_TAKE in
  reserve.py).

Design decisions from 08-CONTEXT.md:
  D1 — session list returns per-session held-resource COUNTS; show returns full dicts.
  D2 — is_holder_stale() composite probe is the primary liveness gate; TTL (~5 min)
       is a backstop only.
  D3 — lazy read-time eviction: list/show detect stale sessions, exclude, and DEL them.
  D4 — machine-global key scope; enrichment join via a single broad scan across
       state:claim:*, state:lock:*, state:reserve:*.
  D5 — upsert idempotency: re-registering the same session_id preserves registered_at
       and refreshes last_heartbeat + TTL.

Prohibited imports (enforced by tests/unit/test_session.py):
  typer, multiprocessing, subprocess, threading
"""
from __future__ import annotations

import json
import os
import time

from em_proj.identity import (
    current_process_composite,
    is_holder_stale,
    resolve_session_id,
    resolve_upstream_identity,
)
from em_proj.redis_client import get_client


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Machine-global key prefix for session records.
#: Full key: "state:session:<session_id>"
KEY_PREFIX: str = "state:session:"

#: SESS-02 — default session TTL in seconds (5-minute heartbeat backstop per D2).
#: Shorter than claim's 1800; the auto-heartbeat daemon (Phase 11) will keep this alive.
TTL_DEFAULT: int = 300

#: Maximum allowed TTL in seconds (24 hours).
MAX_TTL: int = 86400


# ---------------------------------------------------------------------------
# Lua scripts — server-side atomicity
# ---------------------------------------------------------------------------

#: Atomic upsert script for session_register (mirrors LUA_CLAIM_REFRESH_OR_TAKE).
#:
#: KEYS[1] = full Redis key (state:session:<session_id>)
#: ARGV[1]  = session_id
#: ARGV[2]  = project_hash
#: ARGV[3]  = upstream_identity
#: ARGV[4]  = pid (int as string)
#: ARGV[5]  = proc_start_epoch (float as string)
#: ARGV[6]  = boot_id
#: ARGV[7]  = cwd
#: ARGV[8]  = registered_at (float as string)
#: ARGV[9]  = last_heartbeat (float as string)
#: ARGV[10] = ttl (int as string, for EXPIRE)
#:
#: Returns:
#:   "registered" — key was absent; HSET all 9 fields + EXPIRE
#:   "refreshed"  — same session_id present; HSET only last_heartbeat + EXPIRE
#:                  (preserves registered_at per D5 idempotency)
#:   "conflict"   — key present with a different session_id (should not occur
#:                  with UUID session_ids; defensive guard)
LUA_SESSION_UPSERT: str = """
local exists = redis.call('EXISTS', KEYS[1])
if exists == 0 then
  redis.call('HSET', KEYS[1],
    'session_id', ARGV[1],
    'project_hash', ARGV[2],
    'upstream_identity', ARGV[3],
    'pid', ARGV[4],
    'proc_start_epoch', ARGV[5],
    'boot_id', ARGV[6],
    'cwd', ARGV[7],
    'registered_at', ARGV[8],
    'last_heartbeat', ARGV[9]
  )
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[10]))
  return 'registered'
end
local sid = redis.call('HGET', KEYS[1], 'session_id')
if sid == ARGV[1] then
  redis.call('HSET', KEYS[1], 'last_heartbeat', ARGV[9])
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[10]))
  return 'refreshed'
end
return 'conflict'
"""

#: Atomic heartbeat refresh script for session_heartbeat.
#:
#: KEYS[1] = full Redis key (state:session:<session_id>)
#: ARGV[1] = session_id (caller's; used for identity check)
#: ARGV[2] = last_heartbeat (float as string; the new value)
#: ARGV[3] = ttl (int as string, for EXPIRE)
#:
#: Returns:
#:   "refreshed" — session_id matches; HSET last_heartbeat + EXPIRE
#:   "not_found" — key absent
#:   "conflict"  — key present but session_id doesn't match
LUA_SESSION_HEARTBEAT: str = """
local exists = redis.call('EXISTS', KEYS[1])
if exists == 0 then return 'not_found' end
local sid = redis.call('HGET', KEYS[1], 'session_id')
if sid ~= ARGV[1] then return 'conflict' end
redis.call('HSET', KEYS[1], 'last_heartbeat', ARGV[2])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return 'refreshed'
"""


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SessionNotFound(Exception):
    """Raised when a session is absent, stale, or mismatched.

    Raised by:
      - session_heartbeat: key absent or session_id mismatch
      - session_show: session_id not found or session is stale

    code = "not_found" — machine-readable, maps to output.py emit_not_found.
    """

    #: Machine-readable code (maps to output.py emit_not_found code arg).
    code: str = "not_found"

    def __init__(self, message: str | None = None) -> None:
        if message is None:
            message = "session not found"
        super().__init__(message)


# ---------------------------------------------------------------------------
# Private helpers (MODULE-PRIVATE — verb code must NOT import these)
# ---------------------------------------------------------------------------


def _build_session_key(session_id: str) -> str:
    """Build the full Redis key for a session record.

    Key shape: KEY_PREFIX + session_id
    Example: "state:session:550e8400-e29b-41d4-a716-446655440000"
    """
    return KEY_PREFIX + session_id


def _hgetall_to_session(raw: dict) -> dict:  # type: ignore[type-arg]
    """Convert Redis HGETALL string-string dict to typed session record dict.

    Redis stores all HASH field values as strings; this function coerces
    numeric fields to proper Python types.

    Type coercions:
      pid               → int(raw["pid"])
      proc_start_epoch  → float(raw["proc_start_epoch"])
      registered_at     → float(raw["registered_at"])
      last_heartbeat    → float(raw["last_heartbeat"])

    String fields (no coercion):
      session_id, project_hash, upstream_identity, boot_id, cwd

    Missing required field → KeyError propagates (caller handles by skipping).
    Invalid numeric string → ValueError propagates (caller handles by skipping).
    """
    return {
        "session_id": raw["session_id"],
        "project_hash": raw["project_hash"],
        "upstream_identity": raw["upstream_identity"],
        "pid": int(raw["pid"]),
        "proc_start_epoch": float(raw["proc_start_epoch"]),
        "boot_id": raw["boot_id"],
        "cwd": raw["cwd"],
        "registered_at": float(raw["registered_at"]),
        "last_heartbeat": float(raw["last_heartbeat"]),
    }


# ---------------------------------------------------------------------------
# Public operations — Task 2
# ---------------------------------------------------------------------------


def session_register() -> dict:  # type: ignore[type-arg]
    """Register the current process as a live session (upsert, D5).

    Calls current_process_composite() to get identity fields, then atomically
    writes or refreshes the session record in Redis using LUA_SESSION_UPSERT.

    On "registered": new key written; returns the 9-field session record.
    On "refreshed": last_heartbeat updated, registered_at preserved (D5);
      reads back from Redis to return the actual preserved registered_at.
    On "conflict": raises SessionNotFound (should not occur with UUID session_ids).

    Returns:
      dict with all 9 session fields (session_id, project_hash, upstream_identity,
      pid, proc_start_epoch, boot_id, cwd, registered_at, last_heartbeat).

    Raises:
      SessionNotFound — if a conflicting session_id is detected (defensive guard).
    """
    composite = current_process_composite()
    session_id = composite["session_id"]
    project_hash = composite["project_hash"]
    upstream_identity = resolve_upstream_identity()
    cwd = os.getcwd()
    now = time.time()

    key = _build_session_key(session_id)
    client = get_client()

    result = client.eval(
        LUA_SESSION_UPSERT,
        1,
        key,
        session_id,
        project_hash,
        upstream_identity,
        str(composite["pid"]),
        str(composite["proc_start_epoch"]),
        composite["boot_id"],
        cwd,
        str(now),   # registered_at (used only on first registration)
        str(now),   # last_heartbeat
        str(TTL_DEFAULT),
    )

    if result == "registered":
        return {
            "session_id": session_id,
            "project_hash": project_hash,
            "upstream_identity": upstream_identity,
            "pid": composite["pid"],
            "proc_start_epoch": composite["proc_start_epoch"],
            "boot_id": composite["boot_id"],
            "cwd": cwd,
            "registered_at": now,
            "last_heartbeat": now,
        }

    if result == "refreshed":
        # D5: must preserve the original registered_at from Redis.
        # Read back the full HASH so we return the actual stored registered_at.
        raw = client.hgetall(key)
        if not raw:
            # Key expired in the tiny window between EVAL and HGETALL — treat as new
            return {
                "session_id": session_id,
                "project_hash": project_hash,
                "upstream_identity": upstream_identity,
                "pid": composite["pid"],
                "proc_start_epoch": composite["proc_start_epoch"],
                "boot_id": composite["boot_id"],
                "cwd": cwd,
                "registered_at": now,
                "last_heartbeat": now,
            }
        return _hgetall_to_session(raw)

    # result == "conflict" — another session_id at this key (defensive guard)
    raise SessionNotFound(
        f"session_id conflict at key {key!r} — "
        "a different session_id occupies this slot (should not occur with UUID session_ids)"
    )


def session_heartbeat() -> dict:  # type: ignore[type-arg]
    """Refresh last_heartbeat and re-arm the TTL for the current session.

    Resolves the current session_id via resolve_session_id(), then atomically
    updates last_heartbeat + TTL via LUA_SESSION_HEARTBEAT.

    Raises:
      SessionNotFound — key absent ("session not registered — call session register first")
                        OR session_id mismatch ("session_id mismatch")

    Returns:
      dict with all 9 session fields (read from Redis after heartbeat update).
    """
    session_id = resolve_session_id()
    key = _build_session_key(session_id)
    now = time.time()
    client = get_client()

    result = client.eval(
        LUA_SESSION_HEARTBEAT,
        1,
        key,
        session_id,
        str(now),
        str(TTL_DEFAULT),
    )

    if result == "not_found":
        raise SessionNotFound("session not registered — call session register first")

    if result == "conflict":
        raise SessionNotFound("session_id mismatch")

    # result == "refreshed"
    raw = client.hgetall(key)
    if not raw:
        raise SessionNotFound("session expired immediately after heartbeat")
    return _hgetall_to_session(raw)


def _scan_all_holders_by_session_id() -> dict:  # type: ignore[type-arg]
    """Scan claim, lock, and reserve namespaces; group live holders by session_id.

    Performs a single broad scan over:
      - state:claim:*   (HASH; decode via hgetall)
      - state:lock:*    (JSON string; decode via json.loads)
      - state:reserve:* (HASH; decode via hgetall)

    Returns a dict mapping session_id → {"claims": [...], "locks": [...], "reserves": [...]}.
    Only includes active (non-stale per is_holder_stale) holders.
    Stale holders are skipped silently.
    Malformed entries (KeyError, ValueError, json.JSONDecodeError) are skipped silently.

    Per D4: this is the cross-namespace lister needed for session enrichment.
    Called once before the session scan loop in session_list/session_show to
    keep enrichment O(1) per session lookup after one full scan.
    """
    # Local imports to avoid circular-import risk (state submodules import from identity
    # which is clean, but importing at module level could introduce import ordering issues
    # if state modules were ever to import session).
    from em_proj.state.claim import KEY_PREFIX as CLAIM_PREFIX
    from em_proj.state.lock import KEY_PREFIX as LOCK_PREFIX
    from em_proj.state.reserve import KEY_PREFIX as RESERVE_PREFIX

    client = get_client()
    result: dict = {}  # type: ignore[type-arg]

    def _ensure_session_entry(sid: str) -> None:
        if sid not in result:
            result[sid] = {"claims": [], "locks": [], "reserves": []}

    # --- Claim namespace (HASH) ---
    for key in client.scan_iter(match=CLAIM_PREFIX + "*", count=100):
        raw = client.hgetall(key)
        if not raw:
            continue
        try:
            holder = {
                "session_id": raw["session_id"],
                "project_hash": raw["project_hash"],
                "reason": raw.get("reason") or None,
                "claimed_at": float(raw["claimed_at"]),
                "expires_at": float(raw["expires_at"]),
            }
            sid = holder["session_id"]
        except (KeyError, ValueError):
            continue

        # Apply stale probe only if the claim HASH carries the required identity
        # fields (pid, proc_start_epoch, boot_id). Standard v1.0 CLAIM-02 records
        # do NOT include these (claim HASH is 5 fields: session_id, project_hash,
        # reason, claimed_at, expires_at). Records that lack these fields are
        # included without stale probing (they may be claimed by a live session;
        # session TTL expiry is the backstop). Records that DO include pid fields
        # (e.g. future extended records or test-written records) are stale-probed.
        if "pid" in raw and "proc_start_epoch" in raw and "boot_id" in raw:
            try:
                stale_check = {
                    "pid": int(raw["pid"]),
                    "proc_start_epoch": float(raw["proc_start_epoch"]),
                    "boot_id": raw["boot_id"],
                }
                if is_holder_stale(stale_check):
                    continue
            except (KeyError, ValueError):
                pass  # malformed stale fields; include the holder anyway

        _ensure_session_entry(sid)
        result[sid]["claims"].append(holder)

    # --- Lock namespace (JSON string) ---
    for key in client.scan_iter(match=LOCK_PREFIX + "*", count=100):
        raw_str = client.get(key)
        if raw_str is None:
            continue
        try:
            holder = json.loads(raw_str)
            sid = holder["session_id"]
            # Lock holders DO carry pid, proc_start_epoch, boot_id (from
            # current_process_composite written at acquire time in lock.py).
            if is_holder_stale(holder):
                continue
        except (KeyError, ValueError, json.JSONDecodeError):
            continue

        _ensure_session_entry(sid)
        result[sid]["locks"].append(holder)

    # --- Reserve namespace (HASH) ---
    for key in client.scan_iter(match=RESERVE_PREFIX + "*", count=100):
        raw = client.hgetall(key)
        if not raw:
            continue
        try:
            holder = {
                "session_id": raw["session_id"],
                "project_hash": raw["project_hash"],
                "upstream_identity": raw["upstream_identity"],
                "workstream": raw["workstream"],
                "reason": raw.get("reason") or None,
                "claimed_at": float(raw["claimed_at"]),
                "expires_at": float(raw["expires_at"]),
            }
            sid = holder["session_id"]
        except (KeyError, ValueError):
            continue

        # Apply stale probe if reserve HASH carries identity fields (same reasoning
        # as for claim records — standard RESERVE-02 is 7 fields without pid).
        if "pid" in raw and "proc_start_epoch" in raw and "boot_id" in raw:
            try:
                stale_check = {
                    "pid": int(raw["pid"]),
                    "proc_start_epoch": float(raw["proc_start_epoch"]),
                    "boot_id": raw["boot_id"],
                }
                if is_holder_stale(stale_check):
                    continue
            except (KeyError, ValueError):
                pass

        _ensure_session_entry(sid)
        result[sid]["reserves"].append(holder)

    return result


# ---------------------------------------------------------------------------
# Public operations — Task 3
# ---------------------------------------------------------------------------


def session_list() -> list:  # type: ignore[type-arg]
    """List all live sessions with held-resource counts.

    Scans state:session:* and returns each live (non-stale) session record
    enriched with counts of held claims, locks, and reservations (D1).

    Stale sessions (dead pid / proc_start mismatch / boot_id change) are
    excluded from results and opportunistically DELed (D3 lazy reaping).
    Sessions that expire mid-scan (empty HGETALL) are silently skipped.
    Malformed HASH entries are silently skipped.

    Calls _scan_all_holders_by_session_id() ONCE before the session scan loop
    so enrichment is O(1) per session lookup after one full scan (D4).

    Returns:
      list of dicts, each with:
        "session": {9-field session record}
        "held": {"claims": N, "locks": N, "reserves": N}  (integer counts per D1)
    """
    client = get_client()

    # Build enrichment map ONCE before outer session loop (D4 optimization).
    enrichment_map = _scan_all_holders_by_session_id()

    results = []
    for key in client.scan_iter(match=KEY_PREFIX + "*", count=100):
        raw = client.hgetall(key)
        if not raw:
            continue

        try:
            session_record = _hgetall_to_session(raw)
        except (KeyError, ValueError):
            continue

        # Apply is_holder_stale using the session record as the "holder"
        # (session record carries pid, proc_start_epoch, boot_id — same triple).
        if is_holder_stale(session_record):
            client.delete(key)  # D3 lazy reap
            continue

        sid = session_record["session_id"]
        held = enrichment_map.get(sid, {"claims": [], "locks": [], "reserves": []})
        results.append({
            "session": session_record,
            "held": {
                "claims": len(held["claims"]),
                "locks": len(held["locks"]),
                "reserves": len(held["reserves"]),
            },
        })

    return results


def session_show(session_id: str) -> dict:  # type: ignore[type-arg]
    """Return one session's full record plus its full held-resource dicts.

    Raises:
      SessionNotFound — if the session_id is not found in Redis
      SessionNotFound — if the session is stale (and has been DELed)

    Returns:
      {
        "session": {9-field session record},
        "held": {
          "claims":   [list of full claim holder dicts],
          "locks":    [list of full lock holder dicts],
          "reserves": [list of full reserve holder dicts],
        }
      }

    Per D1: session_show returns full held dicts (not counts).
    Per D3: stale session raises SessionNotFound and is DELed.
    """
    key = _build_session_key(session_id)
    client = get_client()

    raw = client.hgetall(key)
    if not raw:
        raise SessionNotFound(f"session {session_id!r} not found")

    try:
        session_record = _hgetall_to_session(raw)
    except (KeyError, ValueError):
        raise SessionNotFound(f"session {session_id!r} record is malformed")

    if is_holder_stale(session_record):
        client.delete(key)  # D3 lazy reap
        raise SessionNotFound(f"session {session_id!r} is stale and has been reaped")

    # Build enrichment map and look up this session's holders.
    enrichment_map = _scan_all_holders_by_session_id()
    held = enrichment_map.get(session_id, {"claims": [], "locks": [], "reserves": []})

    return {
        "session": session_record,
        "held": held,
    }
