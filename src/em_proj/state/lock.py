"""Pure advisory-lock operations for `em-proj state lock/unlock` — no typer imports.

Per D-17 this module owns ALL lock business logic; verb wiring in
`em_proj/state/__init__.py` (Plan 03-04) is a thin translation layer:
parse argv → call a function here → call `emit_*`. Nothing in this file
imports `typer`.

Per D-18 / Phase 2 D-18 every Redis handle comes from
`em_proj.redis_client.get_client()` — the single chokepoint. This module
NEVER constructs a Redis client directly and NEVER catches
`redis.ConnectionError`; the wrapper owns connection-error translation and
the verb layer calls `die_if_redis_unreachable(client)` before any op here.

Lock key namespacing (Phase 2 D-06):
  - Every lock key is stored in Redis as ``state:lock:<name>`` (KEY_PREFIX).
  - The user-typed name is the bare suffix (validated by validate_key per D-09).

Lock JSON value shape (D-02 — wire format shared with Phase 4 claims):
  {
    "pid":              <int>,
    "proc_start_epoch": <float>,
    "boot_id":          "<16-hex-char string>",
    "session_id":       "<UUID or pid-<int>>",
    "project_hash":     "<cwd-as-dashes>",
    "reason":           "<str or null>",
    "acquired_at":      <float epoch>,
    "expires_at":       <float epoch>,
  }
  Stored compact (sort_keys=True, no whitespace) so Lua compare-and-swap can
  match by raw byte-string equality (see LUA_COMPARE_AND_SWAP_IF_STALE notes).

Atomicity via Lua (Phase 1 D-09 / Phase 3 D-06):
  Lua scripts run server-side, held within a single Redis server command slot.
  This eliminates TOCTOU races between read and write without requiring
  WATCH/MULTI/EXEC. Three scripts:
    - LUA_COMPARE_AND_DELETE      — release (D-06)
    - LUA_COMPARE_AND_SWAP_IF_STALE — opportunistic stale-takeover (D-10)
    - LUA_FORCE_DISPLACE            — unconditional atomic override (--warn path D-07)

Block-poll (LOCK-02 / D-07):
  On collision with a LIVE holder, lock_acquire polls (SET NX EX) every
  DEFAULT_BLOCK_POLL_MS ms up to DEFAULT_BLOCK_SECONDS (1 second default).
  After timeout, raises HeldByAnother. This is the 1s-timeout half of LOCK-02;
  the --warn override half lands in Plan 03-04 via lock_force_displace.

D-14/D-17 thin-verb-shell discipline:
  lock_force_displace lets Plan 03-04's verb body stay thin — no private-import
  leakage into verb code. _encode_holder and _decode_holder are MODULE-PRIVATE
  (underscore prefix). Verb code in state/__init__.py MUST NOT import them;
  the public surface for displacement is lock_force_displace. Plan 03-06's
  structural test will assert this invariant.
"""
from __future__ import annotations

import json
import time

from em_proj.identity import current_process_composite, is_holder_stale
from em_proj.redis_client import get_client
from em_proj.state.kv import validate_key
from em_proj.state.kv import ValidationError

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: D-06 / Phase 2 D-08 — two-segment prefix for the lock verb family.
#: The user-typed name is the bare suffix appended to this.
KEY_PREFIX: str = "state:lock:"

#: D-04 — default lock TTL in seconds. Short enough that an abandoned lock
#: auto-releases within a minute (PROJECT.md "TTL is the final backstop");
#: long enough to span most short-lived advisory operations without --hold.
DEFAULT_TTL: int = 60

#: D-04 range floor — minimum allowed TTL in seconds.
MIN_TTL: int = 1

#: D-04 range ceiling — maximum allowed TTL in seconds (1 hour).
MAX_TTL: int = 3600

#: D-12 discretion — reason field max character length.
MAX_REASON_CHARS: int = 256

#: LOCK-02 / D-07 — default block-poll wall-time budget before HeldByAnother.
DEFAULT_BLOCK_SECONDS: float = 1.0

#: Poll interval during the 1s block (milliseconds → converted to seconds in loop).
DEFAULT_BLOCK_POLL_MS: int = 50


# ---------------------------------------------------------------------------
# Lua scripts (D-06 / D-10 / D-07 — server-side atomicity, Phase 1 D-09 carry)
# ---------------------------------------------------------------------------

#: D-06 — compare-and-delete script for lock_release.
#:
#: ARGV: [1] = pid (int), [2] = proc_start_epoch (float as string)
#: KEYS: [1] = full Redis key (e.g. "state:lock:foo")
#:
#: Returns:
#:   1   — deleted (we were the holder; success)
#:   0   — mismatch (someone else holds; raise HeldByAnother)
#:  -1   — key absent (already expired/released; raise HeldByAnother with holder=None)
#:
#: The script verifies BOTH pid AND proc_start_epoch match (D-03 / T-3-03-04:
#: PID alone is insufficient because PIDs are recycled on long-running machines).
LUA_COMPARE_AND_DELETE: str = """
local v = redis.call('GET', KEYS[1])
if not v then return -1 end
local h = cjson.decode(v)
if h.pid == tonumber(ARGV[1]) and h.proc_start_epoch == tonumber(ARGV[2]) then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

#: D-10 — compare-and-swap-if-stale script for opportunistic takeover.
#:
#: ARGV: [1] = old_raw_json (exact byte sequence we read), [2] = new_json, [3] = ttl
#: KEYS: [1] = full Redis key
#:
#: The script compares against the EXACT raw JSON string we read (ARGV[1]).
#: This protects against a TOCTOU race: if a live process updates the holder between
#: our probe (is_holder_stale) and our swap, the value changes and the comparison
#: fails — the script no-ops and we fall through to block-poll. This is why
#: sort_keys=True on encode is load-bearing: the byte sequence must be deterministic
#: so the same logical holder always encodes to the same byte string.
#:
#: Returns:
#:   new_json string — swap succeeded; we are the new holder
#:   0               — value changed (another process took over); fall through
LUA_COMPARE_AND_SWAP_IF_STALE: str = """
local v = redis.call('GET', KEYS[1])
if not v then
  local ok = redis.call('SET', KEYS[1], ARGV[2], 'NX', 'EX', tonumber(ARGV[3]))
  if ok then return ARGV[2] end
  return 0
end
if v == ARGV[1] then
  redis.call('SET', KEYS[1], ARGV[2], 'EX', tonumber(ARGV[3]))
  return ARGV[2]
end
return 0
"""

#: D-07 / --warn override — force-displace script for lock_force_displace.
#:
#: ARGV: [1] = new_json, [2] = ttl
#: KEYS: [1] = full Redis key
#:
#: Atomically DEL any existing value (no-op if absent) then SET NX EX our holder.
#: The NX is a defensive guard against the theoretical sub-microsecond race where
#: another caller wins between our DEL and our SET in the same Lua execution context.
#: In practice EVAL holds the Redis server single-threaded for the script's duration,
#: so the NX failure path is theoretical but cheap to defend against (T-3-03-08).
#:
#: Returns:
#:   new_json string — displacement succeeded; we are the new holder
#:   0               — theoretical race: another holder appeared after our DEL
#:                     (raise HeldByAnother in Python; caller surfaces sensibly)
LUA_FORCE_DISPLACE: str = """
redis.call('DEL', KEYS[1])
local ok = redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', tonumber(ARGV[2]))
if ok then return ARGV[1] end
return 0
"""


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HeldByAnother(Exception):
    """Raised when a lock operation fails due to another holder.

    Raised by:
      - lock_acquire: after the 1s block-poll, when a LIVE holder still holds
      - lock_release: when the compare-and-delete script finds a different holder
        (or the key is absent, meaning we were displaced and it expired)
      - lock_force_displace: theoretical race where the NX guard fails after DEL

    Carries the conflicting/displacing holder dict for the verb layer to render
    in the error envelope (D-09 displaced-holder-learns principle).
    holder may be None when the lock was absent (displaced-then-expired flow).

    D-09: The displaced holder learns they were displaced via this exception's
    holder attribute. The verb layer in Plan 03-04 translates it to exit 3 via
    emit_held_by_another.
    """

    #: Machine-readable code (maps to output.py emit_held_by_another code arg).
    code: str = "held_by_another"

    def __init__(self, holder: dict | None = None, message: str | None = None) -> None:  # type: ignore[type-arg]
        self.holder = holder
        if message is None:
            if holder is not None:
                pid = holder.get("pid", "?")
                sid = holder.get("session_id", "?")
                message = f"lock held by pid {pid} session {sid}"
            else:
                message = "lock is held (holder info unavailable)"
        super().__init__(message)


# ---------------------------------------------------------------------------
# Private helpers (MODULE-PRIVATE — verb code must NOT import these)
# ---------------------------------------------------------------------------


def _make_holder(reason: str | None, ttl: int) -> dict:  # type: ignore[type-arg]
    """Build the eight-field D-02 holder record for the current process.

    Combines current_process_composite() (five fields: pid, proc_start_epoch,
    boot_id, session_id, project_hash) with reason, acquired_at, and expires_at.
    The expires_at field is informational — actual expiry is enforced by Redis EX
    (T-3-03-06: expires_at is for display, not for the takeover decision).
    """
    composite = current_process_composite()
    now = time.time()
    return {
        **composite,
        "reason": reason,
        "acquired_at": now,
        "expires_at": now + ttl,
    }


def _encode_holder(holder: dict) -> str:  # type: ignore[type-arg]
    """Encode a holder dict to compact, byte-stable JSON.

    sort_keys=True is LOAD-BEARING for LUA_COMPARE_AND_SWAP_IF_STALE: the Lua
    script compares the raw JSON byte sequence we read against the current value
    in Redis. Both must encode the same logical holder to the same byte sequence,
    which only holds if key ordering is deterministic.

    MODULE-PRIVATE. Verb code in state/__init__.py MUST NOT import this.
    The public surface for displacement is lock_force_displace.
    """
    return json.dumps(holder, separators=(",", ":"), sort_keys=True)


def _decode_holder(blob: str) -> dict:  # type: ignore[type-arg]
    """Decode a raw JSON string from Redis to a holder dict.

    MODULE-PRIVATE. Verb code in state/__init__.py MUST NOT import this.
    """
    return json.loads(blob)


def _validate_reason(reason: str | None) -> None:
    """Enforce the MAX_REASON_CHARS cap on the reason field.

    Raises ValidationError(code="validation_error") if reason exceeds the cap.
    Allows any printable Unicode (permissive per D-12 Claude's discretion).
    Called by lock_acquire and lock_force_displace before any Redis call.
    """
    if reason is not None and len(reason) > MAX_REASON_CHARS:
        raise ValidationError(
            code="validation_error",
            message=f"reason exceeds {MAX_REASON_CHARS} characters",
        )


def _validate_ttl(ttl: int) -> None:
    """Enforce TTL bounds (MIN_TTL..MAX_TTL inclusive).

    Raises ValidationError(code="validation_error") on out-of-range values.
    Called by lock_acquire and lock_force_displace before any Redis call.
    """
    if ttl < MIN_TTL or ttl > MAX_TTL:
        raise ValidationError(
            code="validation_error",
            message=f"ttl must be between {MIN_TTL} and {MAX_TTL} seconds",
        )


# ---------------------------------------------------------------------------
# Public operations (LOCK-01 + LOCK-02-partial)
# ---------------------------------------------------------------------------


def lock_acquire(name: str, ttl: int = DEFAULT_TTL, reason: str | None = None) -> dict:  # type: ignore[type-arg]
    """Acquire state:lock:<name> for the current process composite.

    Flow:
      1. validate_key(name) — Phase 2 D-09 carry (same regex, same ValidationError)
      2. validate reason and ttl bounds
      3. SET NX EX <ttl> with the holder JSON
      4. On collision: read existing holder, run is_holder_stale probe
         - Stale: attempt LUA_COMPARE_AND_SWAP_IF_STALE
           - Swap succeeded → acquired via stale-takeover; return holder
           - Swap lost to another taker → fall through to block-poll
         - Live: block-poll (LOCK-02 1s default)
      5. Block-poll: sleep DEFAULT_BLOCK_POLL_MS ms, retry SET NX EX, up to
         DEFAULT_BLOCK_SECONDS (1 second). After timeout → raise HeldByAnother.

    Returns the persisted holder dict (parsed from the stored JSON) on success.

    Raises:
      ValidationError — invalid name, reason too long, or ttl out of range
      HeldByAnother   — after the 1s block-poll expires with a live holder

    Note: This function does NOT catch redis.ConnectionError. The verb layer
    must call die_if_redis_unreachable(client) before invoking this (D-18/D-19).
    """
    validate_key(name)
    _validate_reason(reason)
    _validate_ttl(ttl)

    redis_key = KEY_PREFIX + name
    holder = _make_holder(reason, ttl)
    encoded = _encode_holder(holder)
    client = get_client()

    # Attempt 1: optimistic SET NX EX
    if client.set(redis_key, encoded, nx=True, ex=ttl):
        return holder

    # Collision: read the existing holder
    existing_raw = client.get(redis_key)
    if existing_raw is None:
        # Key expired between our SET NX and our GET — try once more
        if client.set(redis_key, encoded, nx=True, ex=ttl):
            return holder
        existing_raw = client.get(redis_key)
        if existing_raw is None:
            # Race: another process acquired between our retries — give up to poll
            pass

    if existing_raw is not None:
        try:
            existing_holder = _decode_holder(existing_raw)
        except (ValueError, KeyError):
            # Malformed holder JSON — treat as absent; try to acquire
            pass
        else:
            # D-10: opportunistic stale-takeover
            if is_holder_stale(existing_holder):
                # Rebuild our holder with fresh timestamps
                holder = _make_holder(reason, ttl)
                encoded = _encode_holder(holder)
                result = client.eval(LUA_COMPARE_AND_SWAP_IF_STALE, 1, redis_key, existing_raw, encoded, ttl)
                if result and result != 0:
                    # Swap succeeded — we are the new holder
                    return holder
                # Another taker beat us — fall through to block-poll

    # LOCK-02: block-poll up to DEFAULT_BLOCK_SECONDS before raising HeldByAnother
    deadline = time.monotonic() + DEFAULT_BLOCK_SECONDS
    while True:
        time.sleep(DEFAULT_BLOCK_POLL_MS / 1000.0)
        holder = _make_holder(reason, ttl)
        encoded = _encode_holder(holder)
        if client.set(redis_key, encoded, nx=True, ex=ttl):
            return holder
        if time.monotonic() >= deadline:
            break

    # Final read to surface the holder in the exception (D-09)
    final_raw = client.get(redis_key)
    final_holder = None
    if final_raw is not None:
        try:
            final_holder = _decode_holder(final_raw)
        except (ValueError, KeyError):
            pass
    raise HeldByAnother(holder=final_holder)


def lock_release(name: str) -> None:
    """Release state:lock:<name> via Lua compare-and-delete on (pid, proc_start_epoch).

    The Lua script verifies BOTH pid AND proc_start_epoch match before deleting
    (T-3-03-04 / D-03: PID alone is insufficient due to PID recycling on
    long-running machines).

    Raises:
      ValidationError — invalid name
      HeldByAnother   — when the compare-and-delete script finds a different holder
                        (holder=displacer_dict_or_None; D-09 displaced-holder-learns)
                        OR when the key is already absent (expired/forced-unlocked;
                        holder=None per D-09 displaced-then-expired flow).

    Note: This function does NOT catch redis.ConnectionError (D-18/D-19 carry).
    """
    validate_key(name)

    redis_key = KEY_PREFIX + name
    composite = current_process_composite()
    pid = composite["pid"]
    proc_start_epoch = composite["proc_start_epoch"]

    client = get_client()
    result = client.eval(LUA_COMPARE_AND_DELETE, 1, redis_key, pid, proc_start_epoch)

    if result == 1:
        # Successfully deleted — we were the holder
        return

    if result == -1:
        # Key was absent (never existed, already expired, or already released)
        # D-09: displaced-then-expired flow — raise with holder=None
        raise HeldByAnother(holder=None, message="lock is no longer held (may have expired or been displaced)")

    # result == 0: key exists but we are not the holder
    # Read the current holder for disclosure (D-09)
    current_raw = client.get(redis_key)
    current_holder = None
    if current_raw is not None:
        try:
            current_holder = _decode_holder(current_raw)
        except (ValueError, KeyError):
            pass
    raise HeldByAnother(holder=current_holder)


def lock_force_displace(name: str, ttl: int = DEFAULT_TTL, reason: str | None = None) -> dict:  # type: ignore[type-arg]
    """Atomically replace any current holder of state:lock:<name> with a fresh holder
    JSON for the calling process.

    Used by the --warn override path (Plan 03-04) to keep the displacement Lua
    server-side and the verb body thin (D-14 / D-17). The caller is responsible
    for the human-confirmation gate (--warn TTY check) BEFORE invoking this function.
    This function performs no consent checks — it is a "do it" primitive (T-3-03-08).

    Flow:
      1. validate_key(name) + validate reason + validate ttl bounds
      2. Build a fresh holder via _make_holder
      3. EVAL LUA_FORCE_DISPLACE: atomic DEL of any existing value + SET NX EX
      4. If the script returns the encoded JSON, decode and return
      5. If the script returns 0 (theoretical NX race after DEL), raise HeldByAnother

    Behaves as unconditional acquire — works whether the key is currently held
    or absent (see LUA_FORCE_DISPLACE notes for the NX guard rationale).

    Returns the persisted holder dict (same shape as lock_acquire returns).

    Raises:
      ValidationError — invalid name, reason too long, or ttl out of range
      HeldByAnother   — theoretical NX-guard race (practically impossible; caller
                        surfaces it as "displacement failed due to race")

    Note: This function does NOT catch redis.ConnectionError (D-18/D-19 carry).
    """
    validate_key(name)
    _validate_reason(reason)
    _validate_ttl(ttl)

    redis_key = KEY_PREFIX + name
    holder = _make_holder(reason, ttl)
    encoded = _encode_holder(holder)
    client = get_client()

    result = client.eval(LUA_FORCE_DISPLACE, 1, redis_key, encoded, ttl)

    if result and result != 0:
        # Displacement succeeded — return the holder we wrote
        return holder

    # Theoretical NX-guard race: another holder appeared in the sub-microsecond
    # window between our DEL and SET. Raise HeldByAnother with holder=None
    # (we have no info about the winner; caller surfaces as edge-case displacement failure).
    raise HeldByAnother(
        holder=None,
        message="force-displace failed due to race (another holder acquired immediately after DEL)",
    )
