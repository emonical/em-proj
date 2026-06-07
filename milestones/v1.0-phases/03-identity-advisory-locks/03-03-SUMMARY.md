---
phase: 03-identity-advisory-locks
plan: "03"
subsystem: lock-primitive
tags: [lock, redis, lua, atomicity, output-envelope, held-by-another, tdd]
dependency_graph:
  requires:
    - 03-01  # identity.py: current_process_composite, resolve_session_id, resolve_project_hash
    - 03-02  # identity.py: is_holder_stale, probe_pid_alive, probe_proc_start_matches, current_boot_id
  provides:
    - lock.py public surface: lock_acquire, lock_release, lock_force_displace, HeldByAnother, KEY_PREFIX, DEFAULT_TTL, MIN_TTL, MAX_TTL, LUA_*
    - output.py additions: emit_held_by_another, _HOLDER_DISCLOSURE_KEYS
  affects:
    - 03-04  # verb wiring (lock/unlock) calls lock_acquire, lock_release, lock_force_displace, HeldByAnother
    - 03-06  # structural tests assert lock.py exports and _encode_holder/_decode_holder module-private invariant
tech_stack:
  added:
    - Lua scripts (EVAL via redis-py): LUA_COMPARE_AND_DELETE, LUA_COMPARE_AND_SWAP_IF_STALE, LUA_FORCE_DISPLACE
    - HeldByAnother exception class with holder dict attribute
  patterns:
    - Sort-keys-stable JSON encoding for Lua byte-string compare
    - Module-private helpers (_encode_holder, _decode_holder) with public displacement surface (lock_force_displace)
    - _HOLDER_DISCLOSURE_KEYS pinned tuple as single source of truth for holder disclosure
key_files:
  created:
    - src/em_proj/state/lock.py
    - tests/unit/test_lock_kv.py
  modified:
    - src/em_proj/output.py
    - tests/unit/test_output.py
decisions:
  - "lock_release on absent key raises HeldByAnother(holder=None) — displaced-then-expired flow (D-09)"
  - "_encode_holder/_decode_holder remain MODULE-PRIVATE; lock_force_displace is the public displacement surface (D-14/D-17)"
  - "sort_keys=True in _encode_holder is LOAD-BEARING for LUA_COMPARE_AND_SWAP_IF_STALE byte-string comparison"
  - "_HOLDER_DISCLOSURE_KEYS excludes boot_id (machine-identifier leakage) and proc_start_epoch (correlation surface) per T-3-XX-02"
  - "lock_force_displace NX guard is defensive: EVAL holds Redis single-threaded, NX failure is theoretical but cheap to handle"
metrics:
  duration_minutes: 45
  completed: "2026-05-23T20:59:47Z"
  tasks_completed: 3
  tasks_total: 3
  files_created: 2
  files_modified: 2
  tests_added: 22
---

# Phase 3 Plan 03: Lock Primitive + Output Extension Summary

Lock primitive layer for advisory locks: pure lock ops + Lua atomicity scripts + HeldByAnother exception + emit_held_by_another output helper with pinned _HOLDER_DISCLOSURE_KEYS constant.

## What Was Built

### src/em_proj/state/lock.py (469 lines, NEW)

Pure lock-ops module — sibling to `kv.py`, no typer imports, D-18 chokepoint preserved.

**Constants:**
- `KEY_PREFIX = "state:lock:"` (D-06 / Phase 2 D-08)
- `DEFAULT_TTL = 60`, `MIN_TTL = 1`, `MAX_TTL = 3600` (D-04)
- `MAX_REASON_CHARS = 256` (D-12 Claude's discretion)
- `DEFAULT_BLOCK_SECONDS = 1.0`, `DEFAULT_BLOCK_POLL_MS = 50` (LOCK-02 / D-07)

**Lua scripts (module-level string constants):**
- `LUA_COMPARE_AND_DELETE` — compare pid+proc_start_epoch then DEL; returns 1/0/-1
- `LUA_COMPARE_AND_SWAP_IF_STALE` — raw-byte-compare old JSON then SET; returns new_json/0
- `LUA_FORCE_DISPLACE` — unconditional DEL + SET NX EX; returns new_json/0

**Exception:**
- `HeldByAnother(holder=None, message=None)` — code="held_by_another", carries holder dict or None

**Public ops:**
- `lock_acquire(name, ttl=60, reason=None) -> dict` — validate + SET NX EX + stale-takeover + 1s block-poll
- `lock_release(name) -> None` — Lua compare-and-delete; raises HeldByAnother on mismatch or absent
- `lock_force_displace(name, ttl=60, reason=None) -> dict` — Lua DEL+SET unconditional replacement

**Private helpers (MODULE-PRIVATE — not for verb code):**
- `_make_holder(reason, ttl) -> dict` — builds D-02 eight-field holder record
- `_encode_holder(holder) -> str` — `json.dumps(sort_keys=True)` (byte-stable for Lua compare)
- `_decode_holder(blob) -> dict` — `json.loads`
- `_validate_reason(reason) -> None` — enforces MAX_REASON_CHARS cap
- `_validate_ttl(ttl) -> None` — enforces MIN_TTL..MAX_TTL bounds

### src/em_proj/output.py (265 lines, MODIFIED — +71 lines)

**Added:**
- `_HOLDER_DISCLOSURE_KEYS: tuple[str, ...] = ("pid", "session_id", "project_hash", "acquired_at", "expires_at", "reason")`
  — Excludes `boot_id` (machine-identifier leakage) and `proc_start_epoch` (correlation/fingerprinting surface); T-3-XX-02 mitigations.
- `emit_held_by_another(code, message, *, holder=None, json_mode=None) -> NoReturn`
  — exits SystemExit(3), writes held_by_another envelope to stderr; sanitizes holder via _HOLDER_DISCLOSURE_KEYS if holder is not None.

### tests/unit/test_lock_kv.py (419 lines, NEW) — 17 tests

| # | Test | D-XX |
|---|------|------|
| 1 | Acquire on empty returns 8-field dict | D-02/LOCK-01 |
| 2 | Persisted JSON round-trips | D-02/D-03 |
| 3 | TTL honored (ttl=2 → redis TTL 1-2) | D-04 |
| 4 | Stale dead-PID takeover via Lua swap | D-10 |
| 5 | Live holder: HeldByAnother with holder dict | LOCK-02 |
| 6 | Release by holder deletes key | D-06 |
| 7 | Release by non-holder: HeldByAnother; key preserved | D-09/T-3-03-04 |
| 8 | Release on absent key: HeldByAnother(holder=None) | D-09 |
| 9 | Colon in name raises ValidationError | D-09 carry |
| 10 | TTL < MIN or > MAX raises ValidationError | D-04 |
| 11 | Reason 257 chars raises; 256 chars succeeds | D-12 |
| 12 | Re-acquire after release works | LOCK-01 |
| 13 | Block-poll timing 0.9-1.5s window | LOCK-02 |
| 14 | force_displace on absent key acquires | D-07 |
| 15 | force_displace on held replaces holder unconditionally | D-07/D-14 |
| 16 | force_displace ttl + reason stored correctly | D-04/D-12 |
| 17 | force_displace validation: name/ttl/reason all checked | D-09 carry/D-12 |

### tests/unit/test_output.py (299 lines, MODIFIED — +100 lines) — 5 new tests

| Test | Asserts |
|------|---------|
| test_emit_held_by_another_json_envelope | exit 3, status=held_by_another, error block, goes to stderr |
| test_emit_held_by_another_plain_mode | exit 3, "em-proj: <message>" to stderr |
| test_emit_held_by_another_with_holder_includes_sanitized_subset | exactly _HOLDER_DISCLOSURE_KEYS keys; boot_id and proc_start_epoch explicitly absent |
| test_emit_held_by_another_schema_version | schema_version == "1" (additive, no bump) |
| test_holder_disclosure_keys_constant_is_pinned_tuple | structural pin asserting exact tuple value |

## Design Decisions

### TTL bounds (D-04 range finalization)
- `MIN_TTL = 1` second (floor — avoids sub-second locks that expire before any use)
- `MAX_TTL = 3600` seconds (ceiling — 1 hour; prevents accidentally parking a lock forever)
- Applied uniformly to `lock_acquire` and `lock_force_displace` via `_validate_ttl`

### Reason validation policy (D-12)
- Max length: 256 characters (`MAX_REASON_CHARS = 256`)
- Allowed characters: any printable Unicode (permissive — reason is free-form metadata for human display)
- Validated by `_validate_reason(reason)` before any Redis call in both `lock_acquire` and `lock_force_displace`

### lock_release on absent key (D-09 discretion)
- `lock_release("foo")` when `state:lock:foo` has never existed (or has expired) raises `HeldByAnother(holder=None)`.
- Rationale: this is the "displaced-then-expired" flow described in D-09. The holder must learn they were displaced. Silent return would mask the racy displacement. `holder=None` indicates the displacer's value is also gone.

### lock_force_displace is exported; _encode_holder/_decode_holder are MODULE-PRIVATE
- `lock_force_displace` is the public surface for Plan 03-04's --warn override path
- `_encode_holder` and `_decode_holder` are underscore-prefixed and documented as MODULE-PRIVATE
- Plan 03-06's structural test will assert that `state/__init__.py` does NOT import `_encode_holder` or `_decode_holder`
- This preserves D-14/D-17 thin-verb-shell discipline

### _HOLDER_DISCLOSURE_KEYS — exact tuple in output.py
```python
_HOLDER_DISCLOSURE_KEYS: tuple[str, ...] = (
    "pid",
    "session_id",
    "project_hash",
    "acquired_at",
    "expires_at",
    "reason",
)
```
Excludes `boot_id` (T-3-XX-02: machine-identifier leakage) and `proc_start_epoch` (T-3-XX-02: correlation/fingerprinting surface via process start times).

## Invariants Preserved

- D-17: `grep -c 'import typer' src/em_proj/state/lock.py` → 0
- D-18: `grep -c 'redis.Redis(' src/em_proj/state/lock.py` → 0 (only `get_client()` used)
- D-19: `grep -cE 'except redis\.' src/em_proj/state/lock.py` → 0 (no ConnectionError catch)
- Phase 2 D-09 carry: `grep -c 'from em_proj.state.kv import validate_key' src/em_proj/state/lock.py` → 1

## Test Counts

- `test_lock_kv.py`: 17 tests (all passing)
- `test_output.py` new tests: 5 tests (all passing)
- Total test suite: 175 tests, 0 failed (from 158 before this plan)

## Lua Script Edge Cases

- **LUA_COMPARE_AND_SWAP_IF_STALE key-absent branch**: if the key expires between `lock_acquire`'s initial `SET NX EX` failure and the Lua `GET`, the script does a `SET NX EX` directly. This was an untested edge case discovered during review; it's handled by the `if not v then` branch returning a fresh acquire.
- **sort_keys=True is LOAD-BEARING**: the compare-and-swap Lua script compares against the raw byte string we read. If the same holder could encode to different byte sequences, the comparison would fail spuriously. `sort_keys=True` guarantees deterministic key ordering.
- **LUA_FORCE_DISPLACE NX guard**: the `NX` flag on the `SET` after `DEL` is defensive against a theoretical sub-microsecond race. Since `EVAL` runs the Lua script atomically on the Redis server, this race path is practically impossible. The `if ok then return ARGV[1] end; return 0` path is implemented but is expected to never fire in practice.

## Commits

| Commit | Description |
|--------|-------------|
| `97ab47a` | feat(03-03): add emit_held_by_another + _HOLDER_DISCLOSURE_KEYS to output.py |
| `86586d9` | feat(03-03): create em_proj/state/lock.py with acquire/release/force-displace + Lua |
| `88ab20b` | test(03-03): unit tests for lock_acquire / lock_release / lock_force_displace |

## Known Stubs

None — all lock ops wire to live Redis via `get_client()`, all holder fields are populated from real process data via `current_process_composite()`, and all Lua scripts execute server-side.

## Threat Flags

No new trust-boundary surface introduced beyond what is documented in the plan's threat model (T-3-03-01 through T-3-03-08 mitigations all implemented).

## Self-Check

Files created/modified:
- src/em_proj/state/lock.py: FOUND
- src/em_proj/output.py: MODIFIED (emit_held_by_another + _HOLDER_DISCLOSURE_KEYS)
- tests/unit/test_lock_kv.py: FOUND
- tests/unit/test_output.py: MODIFIED (+5 tests)

Commits present in git log:
- 97ab47a: FOUND
- 86586d9: FOUND
- 88ab20b: FOUND

Test suite: 175 passed, 0 failed

## Self-Check: PASSED
