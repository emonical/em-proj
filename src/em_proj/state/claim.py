"""Pure area-claim operations for `em-proj state claim/release/check` — no typer imports.

Per D-17 this module owns ALL claim business logic; verb wiring in
`em_proj/state/__init__.py` (Plan 04-02) is a thin translation layer:
parse argv → call a function here → call `emit_*`. Nothing in this file
imports `typer`.

Per D-18 / Phase 2 D-18 every Redis handle comes from
`em_proj.redis_client.get_client()` — the single chokepoint. This module
NEVER constructs a Redis client directly and NEVER catches
`redis.ConnectionError`; the wrapper owns connection-error translation and
the verb layer calls `die_if_redis_unreachable(client)` before any op here.

Claim key namespacing:
  - Every claim key is stored in Redis as ``state:claim:<project_hash>:<area>`` (KEY_PREFIX).
  - Claims are project-scoped (project_hash in the key) unlike locks (user-global).
  - The user-typed area name is the bare suffix (validated by validate_key per D-09).

Holder HASH record shape (CLAIM-02 — 5 fields):
  {
    "session_id":   "<UUID or pid-<int>>",
    "project_hash": "<cwd-as-dashes>",
    "reason":       "<str or null>",
    "claimed_at":   <float epoch>,
    "expires_at":   <float epoch>,
  }
  Stored as a Redis HASH (HSET/HGETALL) rather than a single JSON string.
  RATIONALE: Lock.py uses a single JSON string for byte-stable Lua comparison
  (LUA_COMPARE_AND_SWAP_IF_STALE needs exact byte match). Claims do NOT need
  stale-takeover; refresh is same-holder only. Using a Redis HASH makes
  individual field updates (refresh: just update expires_at) simpler and Lua
  cleaner. Numeric fields (claimed_at, expires_at) stored as floats; reason
  as string or "".

Atomicity via Lua (Plan 04-01 / T-4-01-02 + T-4-01-03 mitigations):
  Lua scripts run server-side, held within a single Redis server command slot.
  This eliminates TOCTOU races between read and write without requiring
  WATCH/MULTI/EXEC. Three scripts:
    - LUA_CLAIM_REFRESH_OR_TAKE   — atomic take-or-refresh (T-4-01-02)
    - LUA_CLAIM_COMPARE_AND_DELETE — release guarded by session_id + project_hash (T-4-01-03)
    - LUA_CLAIM_CHECK             — atomic exists + HGETALL read

Novel refresh semantics (T-4-01-05 / CLAIM-01):
  Same-holder repeat call to claim_take extends the TTL rather than raising
  HeldByAnother. The Lua script checks BOTH session_id AND project_hash before
  the refresh path. MAX_TTL = 86400 caps the EXPIRE argument, preventing infinite
  extension by the holder.

No blocking poll. No is_holder_stale probe. No RefresherThread. No --hold mode.
This module MUST NOT import: typer, multiprocessing, subprocess, threading.
"""
from __future__ import annotations

import time

from em_proj.identity import resolve_session_id, resolve_project_hash
from em_proj.redis_client import get_client
from em_proj.state.kv import validate_key, ValidationError  # noqa: F401

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Phase 2 D-06 analog — two-segment prefix + project_hash + area.
#: Full key: "state:claim:<project_hash>:<area>"
KEY_PREFIX: str = "state:claim:"

#: CLAIM-01 — default claim TTL in seconds (30 minutes).
#: Long enough for a multi-command work session; auto-expires if abandoned.
TTL_DEFAULT: int = 1800

#: Minimum allowed TTL in seconds. Less than 1 minute would behave like a lock.
MIN_TTL: int = 60

#: Maximum allowed TTL in seconds (24 hours). T-4-01-05: caps EXPIRE argument.
MAX_TTL: int = 86400

#: Reason field max character length (mirror lock.py MAX_REASON_CHARS).
MAX_REASON_CHARS: int = 256


# ---------------------------------------------------------------------------
# Lua scripts — server-side atomicity
# ---------------------------------------------------------------------------

#: T-4-01-02 — Refresh-or-take script for claim_take.
#:
#: KEYS[1] = full Redis key (state:claim:<project_hash>:<area>)
#: ARGV[1] = session_id (caller's)
#: ARGV[2] = project_hash (caller's)
#: ARGV[3] = reason (string or "")
#: ARGV[4] = claimed_at (float as string)
#: ARGV[5] = expires_at (float as string)
#: ARGV[6] = ttl (int as string, for EXPIRE)
#:
#: Returns:
#:   "taken"     — key was absent; HSET + EXPIRE succeeded; we are the holder
#:   "refreshed" — same session_id + project_hash; expires_at updated; TTL refreshed
#:   "conflict"  — different holder present; caller reads existing holder via HGETALL
#:
#: Atomicity: EXISTS + conditional HSET + EXPIRE (or HSET expires_at + EXPIRE) all
#: execute in one Lua script slot — no TOCTOU possible (T-4-01-02).
LUA_CLAIM_REFRESH_OR_TAKE: str = """
local exists = redis.call('EXISTS', KEYS[1])
if exists == 0 then
  redis.call('HSET', KEYS[1],
    'session_id', ARGV[1],
    'project_hash', ARGV[2],
    'reason', ARGV[3],
    'claimed_at', ARGV[4],
    'expires_at', ARGV[5]
  )
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[6]))
  return 'taken'
end
local sid = redis.call('HGET', KEYS[1], 'session_id')
local phash = redis.call('HGET', KEYS[1], 'project_hash')
if sid == ARGV[1] and phash == ARGV[2] then
  redis.call('HSET', KEYS[1], 'expires_at', ARGV[5])
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[6]))
  return 'refreshed'
end
return 'conflict'
"""

#: T-4-01-03 — Compare-and-delete script for claim_release.
#:
#: KEYS[1] = full Redis key
#: ARGV[1] = session_id (caller's)
#: ARGV[2] = project_hash (caller's)
#:
#: Returns:
#:   1   — deleted (we were the holder; success)
#:   0   — mismatch (different holder; raise HeldByAnother with current holder)
#:  -1   — key absent (expired or never existed; raise HeldByAnother with holder=None)
#:
#: Dual-field check: session_id AND project_hash both must match before DEL.
#: A non-holder caller cannot release another session's claim (T-4-01-03).
LUA_CLAIM_COMPARE_AND_DELETE: str = """
local exists = redis.call('EXISTS', KEYS[1])
if exists == 0 then return -1 end
local sid = redis.call('HGET', KEYS[1], 'session_id')
local phash = redis.call('HGET', KEYS[1], 'project_hash')
if sid == ARGV[1] and phash == ARGV[2] then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""

#: Atomic read script for claim_check.
#:
#: KEYS[1] = full Redis key
#: No ARGV.
#:
#: Returns: HGETALL result as Redis bulk reply (alternating field/value pairs)
#:          or false (nil) if the key is absent.
#:
#: Read-only Lua script. Atomic EXISTS + HGETALL eliminates TOCTOU between
#: a separate EXISTS check and the HGETALL call.
LUA_CLAIM_CHECK: str = """
local exists = redis.call('EXISTS', KEYS[1])
if exists == 0 then return false end
return redis.call('HGETALL', KEYS[1])
"""


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HeldByAnother(Exception):
    """Raised when a claim operation fails due to another holder.

    Raised by:
      - claim_take: when a different session_id + project_hash holds the claim
      - claim_release: when the compare-and-delete script finds a different holder
        or the key is absent (holder=None — expired-or-never-held path)

    Carries the conflicting holder dict for the verb layer to render.
    holder may be None when the claim was absent (expired-or-never-held flow).

    code = "held_by_another" — machine-readable, maps to exit code 3.
    """

    #: Machine-readable code (maps to output.py emit_held_by_another code arg).
    code: str = "held_by_another"

    def __init__(self, holder: dict | None = None, message: str | None = None) -> None:  # type: ignore[type-arg]
        self.holder = holder
        if message is None:
            if holder is not None:
                sid = holder.get("session_id", "?")
                message = f"claim held by session {sid}"
            else:
                message = "claim is not held (may have expired)"
        super().__init__(message)


class ClaimNotHeld(Exception):
    """Raised by claim_check when the area has no active claim.

    Distinct from HeldByAnother: this represents the "nothing there" state
    for a read-only check, not a conflict during a write attempt.

    code = "not_held" — machine-readable, maps to exit code 2.
    """

    #: Machine-readable code (maps to output.py emit_not_found).
    code: str = "not_held"

    def __init__(self, message: str | None = None) -> None:
        if message is None:
            message = "area is not claimed"
        super().__init__(message)


# ---------------------------------------------------------------------------
# Private helpers (MODULE-PRIVATE — verb code must NOT import these)
# ---------------------------------------------------------------------------


def _build_redis_key(project_hash: str, area: str) -> str:
    """Build the full Redis key for a claim.

    Key shape: KEY_PREFIX + project_hash + ":" + area
    Example: "state:claim:-Users-emonical-projects-myrepo:docs/api"
    """
    return KEY_PREFIX + project_hash + ":" + area


def _make_holder(area: str, reason: str | None, ttl: int) -> dict:  # type: ignore[type-arg]
    """Build the five-field CLAIM-02 holder record for the current session.

    Calls resolve_session_id() and resolve_project_hash() from identity.py.
    The returned dict has exactly five keys: session_id, project_hash, reason,
    claimed_at, expires_at.

    claimed_at = time.time()
    expires_at = claimed_at + ttl

    reason is stored as the string or None (empty string and None are both
    treated as "no reason" at the holder level; _hgetall_to_holder normalizes
    empty strings back to None on read-back).
    """
    now = time.time()
    return {
        "session_id": resolve_session_id(),
        "project_hash": resolve_project_hash(),
        "reason": reason,
        "claimed_at": now,
        "expires_at": now + ttl,
    }


def _hgetall_to_holder(raw: dict) -> dict:  # type: ignore[type-arg]
    """Convert Redis HGETALL string-string dict to typed holder dict.

    Redis stores all HASH field values as strings; this function coerces
    the numeric fields to float and normalizes empty reason to None.

    claimed_at → float(raw["claimed_at"])
    expires_at → float(raw["expires_at"])
    reason     → raw.get("reason") or None (empty string → None)
    session_id → raw["session_id"]  (string — no coercion)
    project_hash → raw["project_hash"]  (string — no coercion)
    """
    return {
        "session_id": raw["session_id"],
        "project_hash": raw["project_hash"],
        "reason": raw.get("reason") or None,
        "claimed_at": float(raw["claimed_at"]),
        "expires_at": float(raw["expires_at"]),
    }


def _validate_reason(reason: str | None) -> None:
    """Enforce the MAX_REASON_CHARS cap on the reason field.

    Raises ValidationError(code="validation_error") if reason exceeds the cap.
    Called by claim_take before any Redis call.
    """
    if reason is not None and len(reason) > MAX_REASON_CHARS:
        raise ValidationError(
            code="validation_error",
            message=f"reason exceeds {MAX_REASON_CHARS} characters",
        )


def _validate_ttl(ttl: int) -> None:
    """Enforce TTL bounds (MIN_TTL..MAX_TTL inclusive).

    Raises ValidationError(code="validation_error") on out-of-range values.
    Called by claim_take before any Redis call.
    """
    if ttl < MIN_TTL or ttl > MAX_TTL:
        raise ValidationError(
            code="validation_error",
            message=f"ttl must be between {MIN_TTL} and {MAX_TTL} seconds",
        )


# ---------------------------------------------------------------------------
# Public operations (CLAIM-01 + CLAIM-02)
# ---------------------------------------------------------------------------


def claim_take(area: str, ttl: int = TTL_DEFAULT, reason: str | None = None) -> dict:  # type: ignore[type-arg]
    """Take or refresh the claim on area for the current session.

    Flow:
      1. validate_key(area) — Phase 2 D-09 carry (same regex, same ValidationError)
      2. _validate_reason(reason)
      3. _validate_ttl(ttl)
      4. Build holder via _make_holder(area, reason, ttl)
      5. Build redis_key via _build_redis_key(holder["project_hash"], area)
      6. EVAL LUA_CLAIM_REFRESH_OR_TAKE with all ARGV values
      7. If result == "taken": return holder
      8. If result == "refreshed": return holder directly (race-free — the
         locally-built holder already has the correct expires_at; claimed_at
         is never mutated on refresh, so a separate HGETALL would be TOCTOU).
      9. If result == "conflict": HGETALL existing; if raw is empty (holder
         expired between EVAL and HGETALL), pass holder=None to HeldByAnother
         rather than crashing with KeyError.

    Raises:
      ValidationError — invalid area name, reason too long, or ttl out of range
      HeldByAnother   — when a different session_id holds the claim

    Note: This function does NOT catch redis.ConnectionError. The verb layer
    must call die_if_redis_unreachable(client) before invoking this (D-18/D-19).
    """
    validate_key(area)
    _validate_reason(reason)
    _validate_ttl(ttl)

    holder = _make_holder(area, reason, ttl)
    redis_key = _build_redis_key(holder["project_hash"], area)
    client = get_client()

    result = client.eval(
        LUA_CLAIM_REFRESH_OR_TAKE,
        1,
        redis_key,
        holder["session_id"],
        holder["project_hash"],
        reason or "",
        str(holder["claimed_at"]),
        str(holder["expires_at"]),
        str(ttl),
    )

    if result == "taken":
        return holder

    if result == "refreshed":
        # Return the locally-built holder — it has the correct session_id,
        # project_hash, reason, claimed_at, and freshly-computed expires_at.
        # A separate HGETALL here would be a TOCTOU race (the key can expire
        # between the Lua EVAL and this round-trip). claimed_at is NOT mutated
        # by a refresh, so the local holder value is authoritative.
        return holder

    # result == "conflict" — different holder present.
    # Guard against an empty dict: if the conflicting holder's key expires
    # (or is released) between the Lua EVAL and this HGETALL, hgetall()
    # returns {} — passing that to _hgetall_to_holder raises KeyError.
    # Treat an empty result as "holder vanished" and pass None.
    raw = client.hgetall(redis_key)
    existing = _hgetall_to_holder(raw) if raw else None
    raise HeldByAnother(holder=existing)


def claim_release(area: str) -> None:
    """Release the claim on area via Lua compare-and-delete on (session_id, project_hash).

    The Lua script verifies BOTH session_id AND project_hash match before deleting
    (T-4-01-03: dual-field check prevents a different session from releasing another
    session's claim).

    Raises:
      ValidationError  — invalid area name
      HeldByAnother    — when compare-and-delete finds a different holder (holder=current)
                         OR when the key is absent (holder=None — expired or never held)

    Note: This function does NOT catch redis.ConnectionError (D-18/D-19 carry).
    """
    validate_key(area)

    session_id = resolve_session_id()
    project_hash = resolve_project_hash()
    redis_key = _build_redis_key(project_hash, area)
    client = get_client()

    result = client.eval(LUA_CLAIM_COMPARE_AND_DELETE, 1, redis_key, session_id, project_hash)

    if result == 1:
        # Successfully deleted — we were the holder
        return

    if result == -1:
        # Key was absent — expired or never claimed
        raise HeldByAnother(holder=None, message="claim not held (may have expired)")

    # result == 0: key exists but we are not the holder
    # Read the current holder for disclosure
    raw = client.hgetall(redis_key)
    current_holder = _hgetall_to_holder(raw) if raw else None
    raise HeldByAnother(holder=current_holder)


def claim_list_by_prefix(
    mine: bool = False,
    active: bool = False,
    stale: bool = False,
) -> list[dict]:  # type: ignore[type-arg]
    """Enumerate active claims scoped to the current project_hash.

    MODULE-PUBLIC: wired by Plan 05-03's claim_list verb.
    Scopes to current project_hash only — cross-project listing is out of scope (M2).
    Does NOT catch redis.ConnectionError (D-18 carry).

    Filters (AND logic — all specified filters must match):
      mine   — only claims where holder["session_id"] == resolve_session_id()
      active — only claims with Redis TTL > 0 (key has an active expiry)
      stale  — only claims where Redis TTL <= 0 (persistent key: ttl=-1, or already 0)

    Note: active and stale are mutually exclusive at the caller's discretion.
    If both are set, results will be empty (no key can have TTL both > 0 and <= 0).
    mine may be combined with active or stale (AND logic).

    Keys that fail HGETALL decode (empty dict or missing required fields) are silently
    skipped — malformed HASH entries cannot cause data corruption (T-5-02-03 accept).

    Returns empty list when no claims exist under the current project_hash prefix.

    Return type: list[dict] — each dict has the same 5-field shape as claim_take return
    value: session_id, project_hash, reason, claimed_at, expires_at.
    """
    project_hash = resolve_project_hash()
    scan_prefix = KEY_PREFIX + project_hash + ":"
    client = get_client()

    results = []
    for key in client.scan_iter(match=scan_prefix + "*", count=100):
        # Fetch HASH fields; empty dict means key expired mid-scan — skip
        raw = client.hgetall(key)
        if not raw:
            continue

        # Decode HASH fields to typed holder dict; skip malformed entries
        try:
            holder = _hgetall_to_holder(raw)
        except (KeyError, ValueError):
            continue

        # Inject the claim area (key suffix after project_hash) so callers
        # can match on it. The key has the shape KEY_PREFIX + project_hash +
        # ":" + <area>; strip the scan prefix to get area.
        holder["area"] = key[len(scan_prefix):]

        # Filter: mine — only current session's claims
        if mine and holder["session_id"] != resolve_session_id():
            continue

        # Lazy TTL fetch: only call client.ttl() when active or stale filter is set
        _ttl = None
        if active or stale:
            _ttl = client.ttl(key)

            # key-not-found sentinel (-2): key expired between HGETALL and TTL.
            # Skip this entry entirely — it no longer exists in Redis.
            if _ttl == -2:
                continue

        # Filter: active — only keys with TTL > 0 (live expiry set)
        if active and _ttl <= 0:
            continue

        # Filter: stale — only keys with TTL <= 0 (persistent key, no active expiry)
        if stale and _ttl > 0:
            continue

        results.append(holder)

    return results


def claim_check(area: str) -> dict:  # type: ignore[type-arg]
    """Check the current claim on area.

    Atomically reads the claim state via LUA_CLAIM_CHECK (EXISTS + HGETALL in
    one Lua script slot — no TOCTOU between existence check and field read).

    Raises:
      ValidationError — invalid area name
      ClaimNotHeld    — when the area has no active claim

    Note: This function does NOT catch redis.ConnectionError (D-18/D-19 carry).
    """
    validate_key(area)

    project_hash = resolve_project_hash()
    redis_key = _build_redis_key(project_hash, area)
    client = get_client()

    # Lua returns false (Python None via redis-py decode_responses=True) when
    # the key is absent, or a flat list of alternating field/value pairs when
    # the HASH exists. Test for None explicitly — `if not raw_result` would
    # also be truthy for an empty list [], masking corrupted-HASH state.
    raw_result = client.eval(LUA_CLAIM_CHECK, 1, redis_key)

    if raw_result is None:
        raise ClaimNotHeld(message=f"area '{area}' is not claimed")

    # HGETALL returns alternating [field, value, field, value, ...] via Lua.
    # redis-py with decode_responses=True returns a list of strings.
    # Convert to dict for _hgetall_to_holder.
    raw_dict: dict = {}
    items = list(raw_result)
    for i in range(0, len(items), 2):
        raw_dict[items[i]] = items[i + 1]

    return _hgetall_to_holder(raw_dict)
