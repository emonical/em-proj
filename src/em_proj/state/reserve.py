"""Pure area-reservation operations for `em-proj state reserve/release/check` — no typer imports.

Per D-17 this module owns ALL reservation business logic; verb wiring in
`em_proj/state/__init__.py` (Plan 07-02) is a thin translation layer:
parse argv → call a function here → call `emit_*`. Nothing in this file
imports `typer`.

Per D-18 / Phase 2 D-18 every Redis handle comes from
`em_proj.redis_client.get_client()` — the single chokepoint. This module
NEVER constructs a Redis client directly and NEVER catches
`redis.ConnectionError`; the wrapper owns connection-error translation and
the verb layer calls `die_if_redis_unreachable(client)` before any op here.

Reservation key namespacing:
  - Every reservation key is stored in Redis as
    ``state:reserve:<upstream_identity>:<area>`` (KEY_PREFIX).
  - Reservations are upstream-scoped — all sibling clones of the same
    upstream repo share the same reservation namespace.
  - The user-typed area name is the bare suffix (validated by validate_key per D-09).
  - Two-namespace invariant (Pitfall #8): this module's Redis keys use
    ``state:reserve:<upstream_identity>:<area>``; the disjoint claim namespace
    lives in claim.py with its own KEY_PREFIX constant. These two namespaces
    MUST NOT share a KEY_PREFIX — each module owns its own constant.

Holder HASH record shape (RESERVE-02 — 7 fields):
  {
    "session_id":        "<UUID or pid-<int>>",
    "project_hash":      "<cwd-as-dashes>",
    "upstream_identity": "<host:owner/repo canonical form>",
    "workstream":        "<name resolved by the verb layer>",
    "reason":            "<str or null>",
    "claimed_at":        <float epoch>,
    "expires_at":        <float epoch>,
  }
  Stored as a Redis HASH (HSET/HGETALL) rather than a single JSON string.
  RATIONALE: Mirrors claim.py's design choice. HASH enables targeted field
  updates (refresh: just update expires_at) and clean Lua scripts.
  upstream_identity and workstream are string pass-throughs (no coercion).

Atomicity via Lua (Plan 07-01 / T-07-12 mitigation):
  Lua scripts run server-side, held within a single Redis server command slot.
  Three scripts mirror claim.py with three named deltas:
    - LUA_RESERVE_REFRESH_OR_TAKE   — compare on (session_id, upstream_identity)
                                      NOT (session_id, project_hash); 7-field HSET
    - LUA_RESERVE_COMPARE_AND_DELETE — ARGV[2] is upstream_identity, not project_hash
    - LUA_RESERVE_CHECK             — verbatim copy of LUA_CLAIM_CHECK (read-only)

Novel refresh semantics (CLAIM-01 carry / RESERVE-01):
  Same-holder repeat call to reserve_take extends the TTL rather than raising
  HeldByAnother. "Same holder" = matching session_id AND upstream_identity (not
  project_hash), which enables same-session cross-clone refresh — Phase 7's key
  semantic extension over Phase 4 claims.

No blocking poll. No is_holder_stale probe. No RefresherThread. No --hold mode.
This module MUST NOT import: typer, multiprocessing, subprocess, threading.
"""
from __future__ import annotations

import time

from em_proj.identity import (
    resolve_session_id,
    resolve_project_hash,
    resolve_upstream_identity,  # imported for grep traceability; verb layer calls and passes result
)
from em_proj.redis_client import get_client
from em_proj.state.kv import validate_key, ValidationError  # noqa: F401

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Phase 7 analog — two-segment prefix + upstream_identity + area.
#: Full key: "state:reserve:<upstream_identity>:<area>"
#: TWO-NAMESPACE INVARIANT: this must NEVER equal claim.py's KEY_PREFIX.
KEY_PREFIX: str = "state:reserve:"

#: RESERVE-01 — default reservation TTL in seconds (30 minutes).
TTL_DEFAULT: int = 1800

#: Minimum allowed TTL in seconds.
MIN_TTL: int = 60

#: Maximum allowed TTL in seconds (7 days). T-4-01-05 carry: caps EXPIRE argument.
#: Reservations may be long-lived (e.g. cross-clone migration-version holds), so the
#: ceiling is a full week — wider than the claim/lock caps by design.
MAX_TTL: int = 604800

#: Reason field max character length (mirror claim.py MAX_REASON_CHARS).
MAX_REASON_CHARS: int = 256

#: Pitfall #3 (07-RESEARCH): ARGV-index drift between claim.py and reserve.py
#: is silent and damaging. This tuple documents the ARGV positions used by
#: LUA_RESERVE_REFRESH_OR_TAKE; the unit test in test_reserve.py asserts that
#: after a take, client.hgetall(key) returns EXACTLY these 7 fields.
_RESERVE_ARGV_ORDER: tuple[str, ...] = (
    "session_id",          # ARGV[1]
    "project_hash",        # ARGV[2]
    "upstream_identity",   # ARGV[3]
    "workstream",          # ARGV[4]
    "reason",              # ARGV[5]
    "claimed_at",          # ARGV[6]
    "expires_at",          # ARGV[7]
    # ARGV[8] is the integer TTL passed to EXPIRE — not a holder field.
)


# ---------------------------------------------------------------------------
# Lua scripts — server-side atomicity
# ---------------------------------------------------------------------------

#: T-07-12 — Refresh-or-take script for reserve_take.
#:
#: KEYS[1] = full Redis key (state:reserve:<upstream_identity>:<area>)
#: ARGV[1] = session_id (caller's)
#: ARGV[2] = project_hash (caller's local cwd-derived value)
#: ARGV[3] = upstream_identity (caller's resolved canonical)
#: ARGV[4] = workstream (caller's resolved name)
#: ARGV[5] = reason (string or "")
#: ARGV[6] = claimed_at (float as string)
#: ARGV[7] = expires_at (float as string)
#: ARGV[8] = ttl (int as string, for EXPIRE)
#:
#: Returns:
#:   "taken"     — key was absent; HSET (7 fields) + EXPIRE succeeded; we are the holder
#:   "refreshed" — same session_id + upstream_identity; expires_at updated; TTL refreshed
#:   "conflict"  — different holder present; caller reads existing holder via HGETALL
#:
#: Delta from LUA_CLAIM_REFRESH_OR_TAKE:
#:   1. HSET writes 7 fields (upstream_identity and workstream inserted before reason).
#:   2. ARGV grows from [1..6] to [1..8].
#:   3. Refresh-guard compares (session_id, upstream_identity) NOT (session_id, project_hash).
#:      This enables same-session cross-clone refresh — Phase 7's core semantic extension.
LUA_RESERVE_REFRESH_OR_TAKE: str = """
local exists = redis.call('EXISTS', KEYS[1])
if exists == 0 then
  redis.call('HSET', KEYS[1],
    'session_id',        ARGV[1],
    'project_hash',      ARGV[2],
    'upstream_identity', ARGV[3],
    'workstream',        ARGV[4],
    'reason',            ARGV[5],
    'claimed_at',        ARGV[6],
    'expires_at',        ARGV[7]
  )
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[8]))
  return 'taken'
end
local sid = redis.call('HGET', KEYS[1], 'session_id')
local upstream = redis.call('HGET', KEYS[1], 'upstream_identity')
if sid == ARGV[1] and upstream == ARGV[3] then
  redis.call('HSET', KEYS[1], 'expires_at', ARGV[7])
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[8]))
  return 'refreshed'
end
return 'conflict'
"""

#: Compare-and-delete script for reserve_release.
#:
#: KEYS[1] = full Redis key
#: ARGV[1] = session_id (caller's)
#: ARGV[2] = upstream_identity (caller's) — NOT project_hash (Phase 7 delta)
#:
#: Returns:
#:   1   — deleted (we were the holder; success)
#:   0   — mismatch (different holder; raise HeldByAnother with current holder)
#:  -1   — key absent (expired or never existed; raise HeldByAnother with holder=None)
#:
#: Dual-field check: session_id AND upstream_identity both must match before DEL.
#: A non-holder caller cannot release another session's reservation (T-4-01-03 carry).
LUA_RESERVE_COMPARE_AND_DELETE: str = """
local exists = redis.call('EXISTS', KEYS[1])
if exists == 0 then return -1 end
local sid = redis.call('HGET', KEYS[1], 'session_id')
local upstream = redis.call('HGET', KEYS[1], 'upstream_identity')
if sid == ARGV[1] and upstream == ARGV[2] then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""

#: Atomic read script for reserve_check.
#:
#: KEYS[1] = full Redis key
#: No ARGV.
#:
#: Returns: HGETALL result as Redis bulk reply (alternating field/value pairs)
#:          or false (nil) if the key is absent.
#:
#: Verbatim copy of LUA_CLAIM_CHECK — no changes (read-only EXISTS + HGETALL).
LUA_RESERVE_CHECK: str = """
local exists = redis.call('EXISTS', KEYS[1])
if exists == 0 then return false end
return redis.call('HGETALL', KEYS[1])
"""


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HeldByAnother(Exception):
    """Raised when a reservation operation fails due to another holder.

    Raised by:
      - reserve_take: when a different session_id + upstream_identity holds the reservation
      - reserve_release: when the compare-and-delete script finds a different holder
        or the key is absent (holder=None — expired-or-never-held path)

    Carries the conflicting holder dict for the verb layer to render.
    holder may be None when the reservation was absent (expired-or-never-held flow).

    code = "held_by_another" — machine-readable, maps to exit code 3.
    """

    #: Machine-readable code (maps to output.py emit_held_by_another code arg).
    code: str = "held_by_another"

    def __init__(self, holder: dict | None = None, message: str | None = None) -> None:  # type: ignore[type-arg]
        self.holder = holder
        if message is None:
            if holder is not None:
                sid = holder.get("session_id", "?")
                message = f"reservation held by session {sid}"
            else:
                message = "reservation is not held (may have expired)"
        super().__init__(message)


class ReserveNotHeld(Exception):
    """Raised by reserve_check when the area has no active reservation.

    Distinct from HeldByAnother: this represents the "nothing there" state
    for a read-only check, not a conflict during a write attempt.

    code = "not_held" — machine-readable, maps to exit code 2.
    """

    #: Machine-readable code (maps to output.py emit_not_found).
    code: str = "not_held"

    def __init__(self, message: str | None = None) -> None:
        if message is None:
            message = "area is not reserved"
        super().__init__(message)


# ---------------------------------------------------------------------------
# Private helpers (MODULE-PRIVATE — verb code must NOT import these)
# ---------------------------------------------------------------------------


def _build_redis_key(upstream_identity: str, area: str) -> str:
    """Build the full Redis key for a reservation.

    Key shape: KEY_PREFIX + upstream_identity + ":" + area
    Example: "state:reserve:github.com:emonical/em-proj:migrations.v200"
    """
    return KEY_PREFIX + upstream_identity + ":" + area


def _make_holder(area: str, upstream_identity: str, workstream: str, reason: str | None, ttl: int) -> dict:  # type: ignore[type-arg]
    """Build the seven-field RESERVE-02 holder record for the current session.

    Calls resolve_session_id() and resolve_project_hash() from identity.py.
    upstream_identity and workstream are passed in by the verb layer (Plan 07-02 owns
    resolution per RESEARCH §Architectural Responsibility Map lines 84-86).

    The returned dict has exactly seven keys:
      session_id, project_hash, upstream_identity, workstream,
      reason, claimed_at, expires_at.

    claimed_at = time.time()
    expires_at = claimed_at + ttl
    """
    now = time.time()
    return {
        "session_id": resolve_session_id(),
        "project_hash": resolve_project_hash(),
        "upstream_identity": upstream_identity,
        "workstream": workstream,
        "reason": reason,
        "claimed_at": now,
        "expires_at": now + ttl,
    }


def _hgetall_to_holder(raw: dict) -> dict:  # type: ignore[type-arg]
    """Convert Redis HGETALL string-string dict to typed 7-field holder dict.

    Redis stores all HASH field values as strings; this function coerces
    the numeric fields to float and normalizes empty reason to None.

    claimed_at       → float(raw["claimed_at"])
    expires_at       → float(raw["expires_at"])
    reason           → raw.get("reason") or None (empty string → None)
    session_id       → raw["session_id"]         (string — no coercion)
    project_hash     → raw["project_hash"]        (string — no coercion)
    upstream_identity → raw["upstream_identity"]  (string — no coercion)
    workstream       → raw["workstream"]          (string — no coercion)
    """
    return {
        "session_id": raw["session_id"],
        "project_hash": raw["project_hash"],
        "upstream_identity": raw["upstream_identity"],
        "workstream": raw["workstream"],
        "reason": raw.get("reason") or None,
        "claimed_at": float(raw["claimed_at"]),
        "expires_at": float(raw["expires_at"]),
    }


def _validate_reason(reason: str | None) -> None:
    """Enforce the MAX_REASON_CHARS cap on the reason field.

    Raises ValidationError(code="validation_error") if reason exceeds the cap.
    Called by reserve_take before any Redis call.
    """
    if reason is not None and len(reason) > MAX_REASON_CHARS:
        raise ValidationError(
            code="validation_error",
            message=f"reason exceeds {MAX_REASON_CHARS} characters",
        )


def _validate_ttl(ttl: int) -> None:
    """Enforce TTL bounds (MIN_TTL..MAX_TTL inclusive).

    Raises ValidationError(code="validation_error") on out-of-range values.
    Called by reserve_take before any Redis call.
    """
    if ttl < MIN_TTL or ttl > MAX_TTL:
        raise ValidationError(
            code="validation_error",
            message=f"ttl must be between {MIN_TTL} and {MAX_TTL} seconds",
        )


# ---------------------------------------------------------------------------
# Public operations (RESERVE-01 + RESERVE-02)
# ---------------------------------------------------------------------------


def reserve_take(
    area: str,
    *,
    upstream_identity: str,
    workstream: str,
    ttl: int = TTL_DEFAULT,
    reason: str | None = None,
) -> dict:  # type: ignore[type-arg]
    """Take or refresh the reservation on area for the current session.

    The ``*`` forces ``upstream_identity`` and ``workstream`` to be keyword-only,
    preventing positional argument swaps (Pitfall #3-adjacent argument-order bug).

    Flow:
      1. validate_key(area) — Phase 2 D-09 carry (same regex, same ValidationError)
      2. _validate_reason(reason)
      3. _validate_ttl(ttl)
      4. Build holder via _make_holder(area, upstream_identity, workstream, reason, ttl)
      5. Build redis_key via _build_redis_key(upstream_identity, area)
      6. EVAL LUA_RESERVE_REFRESH_OR_TAKE with ARGV per _RESERVE_ARGV_ORDER + ARGV[8]=ttl
      7. If result == "taken": return holder
      8. If result == "refreshed": return holder directly (race-free — locally-built
         holder has correct expires_at; claimed_at not mutated on refresh)
      9. If result == "conflict": HGETALL existing; if raw is empty (holder expired
         between EVAL and HGETALL), pass holder=None to HeldByAnother rather than
         crashing with KeyError.

    Raises:
      ValidationError — invalid area name, reason too long, or ttl out of range
      HeldByAnother   — when a different session_id holds the reservation

    Note: Does NOT catch redis.ConnectionError (D-18/D-19 carry).
    """
    validate_key(area)
    _validate_reason(reason)
    _validate_ttl(ttl)

    holder = _make_holder(area, upstream_identity, workstream, reason, ttl)
    redis_key = _build_redis_key(upstream_identity, area)
    client = get_client()

    result = client.eval(
        LUA_RESERVE_REFRESH_OR_TAKE,
        1,
        redis_key,
        holder["session_id"],         # ARGV[1]
        holder["project_hash"],       # ARGV[2]
        upstream_identity,            # ARGV[3]
        workstream,                   # ARGV[4]
        reason or "",                 # ARGV[5]
        str(holder["claimed_at"]),    # ARGV[6]
        str(holder["expires_at"]),    # ARGV[7]
        str(ttl),                     # ARGV[8] — for EXPIRE
    )

    if result == "taken":
        return holder

    if result == "refreshed":
        # Return the locally-built holder — it has the correct session_id,
        # upstream_identity, workstream, reason, claimed_at, and freshly-computed
        # expires_at.  A separate HGETALL here would be a TOCTOU race.
        return holder

    # result == "conflict" — different holder present.
    # Guard against an empty dict: if the conflicting holder's key expires
    # (or is released) between the Lua EVAL and this HGETALL, hgetall()
    # returns {} — passing that to _hgetall_to_holder raises KeyError.
    # Treat an empty result as "holder vanished" and pass None.
    raw = client.hgetall(redis_key)
    existing = _hgetall_to_holder(raw) if raw else None
    raise HeldByAnother(holder=existing)


def reserve_release(area: str, *, upstream_identity: str) -> None:
    """Release the reservation on area via Lua compare-and-delete on (session_id, upstream_identity).

    The Lua script verifies BOTH session_id AND upstream_identity match before deleting
    (T-4-01-03 carry: dual-field check prevents a different session from releasing
    another session's reservation).

    Raises:
      ValidationError  — invalid area name
      HeldByAnother    — when compare-and-delete finds a different holder (holder=current)
                         OR when the key is absent (holder=None — expired or never held)

    Note: Does NOT catch redis.ConnectionError (D-18/D-19 carry).
    """
    validate_key(area)

    session_id = resolve_session_id()
    redis_key = _build_redis_key(upstream_identity, area)
    client = get_client()

    result = client.eval(LUA_RESERVE_COMPARE_AND_DELETE, 1, redis_key, session_id, upstream_identity)

    if result == 1:
        # Successfully deleted — we were the holder
        return

    if result == -1:
        # Key was absent — expired or never reserved
        raise HeldByAnother(holder=None, message="reservation not held (may have expired)")

    # result == 0: key exists but we are not the holder
    raw = client.hgetall(redis_key)
    current_holder = _hgetall_to_holder(raw) if raw else None
    raise HeldByAnother(holder=current_holder)


def reserve_check(area: str, *, upstream_identity: str) -> dict:  # type: ignore[type-arg]
    """Check the current reservation on area.

    Atomically reads the reservation state via LUA_RESERVE_CHECK (EXISTS + HGETALL
    in one Lua script slot — no TOCTOU between existence check and field read).

    Raises:
      ValidationError   — invalid area name
      ReserveNotHeld    — when the area has no active reservation

    Note: Does NOT catch redis.ConnectionError (D-18/D-19 carry).
    """
    validate_key(area)

    redis_key = _build_redis_key(upstream_identity, area)
    client = get_client()

    # Lua returns false (Python None via redis-py decode_responses=True) when
    # the key is absent, or a flat list of alternating field/value pairs when
    # the HASH exists.
    raw_result = client.eval(LUA_RESERVE_CHECK, 1, redis_key)

    if raw_result is None:
        raise ReserveNotHeld(message=f"area '{area}' is not reserved")

    # HGETALL returns alternating [field, value, field, value, ...] via Lua.
    # redis-py with decode_responses=True returns a list of strings.
    raw_dict: dict = {}
    items = list(raw_result)
    for i in range(0, len(items), 2):
        raw_dict[items[i]] = items[i + 1]

    return _hgetall_to_holder(raw_dict)


def reserve_list_by_prefix(
    *,
    upstream_identity: str,
    mine: bool = False,
    active: bool = False,
    stale: bool = False,
) -> list[dict]:  # type: ignore[type-arg]
    """Enumerate active reservations scoped to the given upstream_identity.

    Scopes to the given upstream_identity only — cross-upstream listing is out of scope.
    Does NOT catch redis.ConnectionError (D-18 carry).

    upstream_identity is a required keyword argument — the verb layer resolves it
    (via resolve_upstream_identity()) and passes it in. This module does not call
    resolve_upstream_identity() directly.

    Filters (AND logic — all specified filters must match):
      mine   — only reservations where holder["session_id"] == resolve_session_id()
      active — only reservations with Redis TTL > 0 (key has an active expiry)
      stale  — only reservations with Redis TTL <= 0 (persistent key, no active expiry)

    Note: active and stale are mutually exclusive at the caller's discretion.
    If both are set, results will be empty (no key can have TTL both > 0 and <= 0).

    Keys that fail HGETALL decode (empty dict or missing required fields) are silently
    skipped — malformed HASH entries cannot cause data corruption.

    Returns empty list when no reservations exist under the given upstream_identity prefix.

    Return type: list[dict] — each dict has the 7-field shape plus an injected "area" field.
    """
    scan_prefix = KEY_PREFIX + upstream_identity + ":"
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

        # Inject the reservation area (key suffix after upstream_identity) so callers
        # can match on it. The key has the shape KEY_PREFIX + upstream_identity +
        # ":" + <area>; strip the scan prefix to get area.
        holder["area"] = key[len(scan_prefix):]

        # Filter: mine — only current session's reservations
        if mine and holder["session_id"] != resolve_session_id():
            continue

        # Lazy TTL fetch: only call client.ttl() when active or stale filter is set
        _ttl = None
        if active or stale:
            _ttl = client.ttl(key)

            # key-not-found sentinel (-2): key expired between HGETALL and TTL.
            # Skip this entry entirely — it no longer exists in Redis.
            # CR-01 guard — copy verbatim from claim.py lines 472-475.
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
