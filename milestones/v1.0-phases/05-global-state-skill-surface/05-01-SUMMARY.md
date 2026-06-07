---
phase: 05-global-state-skill-surface
plan: "01"
subsystem: state/lock
tags: [lock, list, pure-ops, tdd, phase-05]
dependency_graph:
  requires:
    - 03-03  # lock.py foundation (lock_acquire, lock_release, HeldByAnother, KEY_PREFIX)
    - 03-02  # identity.py is_holder_stale + resolve_session_id
  provides:
    - lock_list_by_prefix  # consumed by 05-03 lock_list verb wiring
  affects:
    - src/em_proj/state/lock.py
    - tests/unit/test_lock_list.py
tech_stack:
  added: []
  patterns:
    - TDD red/green on pure-ops function
    - scan_iter cursor-based SCAN (same pattern as kv_list)
    - D-17 thin-ops discipline (no typer, no emit_*)
    - D-18 carry: no redis.ConnectionError catch in pure op
key_files:
  created:
    - tests/unit/test_lock_list.py
  modified:
    - src/em_proj/state/lock.py
decisions:
  - "resolve_session_id imported locally inside lock_list_by_prefix to keep identity.py import at function scope only — avoids any risk of circular import in the future; consistent with existing lock.py design"
  - "mine= filter uses holder.get('session_id') (not holder['session_id']) to gracefully handle any edge-case dict that survived JSON decode but is missing the field"
metrics:
  duration: "~15 minutes"
  completed: "2026-05-24T23:37:04Z"
  tasks_completed: 2
  tests_added: 6
  files_created: 1
  files_modified: 1
---

# Phase 5 Plan 01: lock_list_by_prefix pure op — Summary

**One-liner:** Added `lock_list_by_prefix(mine=False, stale=False) -> list[dict]` to `lock.py` via TDD, scanning `state:lock:*` with optional session-id and staleness filters.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Failing unit tests for lock_list_by_prefix | f79f81d | tests/unit/test_lock_list.py |
| 2 (GREEN) | Implement lock_list_by_prefix in lock.py | 4d09f52 | src/em_proj/state/lock.py |

## What Was Built

`lock_list_by_prefix` is a public pure-ops function appended to `src/em_proj/state/lock.py` in the public operations section (before the hold-runner section), following D-17 thin-ops discipline:

- **Scan**: `client.scan_iter(match=KEY_PREFIX + "*", count=100)` — cursor-based, non-blocking
- **Expire-race safety**: Keys returning `None` from `client.get()` (expired mid-scan) are silently skipped
- **Malformed JSON safety**: `try/_decode_holder()` with `(ValueError, KeyError)` catch — skipped silently (T-5-01-02)
- **mine=True**: filters on `holder.get("session_id") != resolve_session_id()` (local import)
- **stale=True**: filters on `is_holder_stale(holder)` — uses the existing identity probe
- **AND logic**: both predicates must be satisfied when both are True
- **D-18 carry**: does NOT catch `redis.ConnectionError` — verb layer owns that

## Tests Added (tests/unit/test_lock_list.py)

| Test | Coverage |
|------|----------|
| `test_lock_list_empty` | empty namespace returns `[]` |
| `test_lock_list_returns_holder` | single lock returns holder with pid, session_id, expires_at |
| `test_lock_list_mine_filter` | mine=True returns only current-session locks |
| `test_lock_list_stale_filter` | stale=True returns only dead-pid holders |
| `test_lock_list_malformed_skip` | invalid JSON under state:lock: is silently skipped |
| `test_lock_list_mine_and_stale_combined` | mine=True + stale=True requires both (AND logic) |

All 6 tests pass. Full unit suite: 213/213 pass.

## TDD Gate Compliance

- RED gate: commit `f79f81d` — `test(05-01): add failing tests for lock_list_by_prefix pure op`
- GREEN gate: commit `4d09f52` — `feat(05-01): implement lock_list_by_prefix pure op in lock.py`
- REFACTOR: not needed — implementation was clean on first pass

## Deviations from Plan

None — plan executed exactly as written.

The plan's Task 1 and Task 2 are presented as separate tasks but both produce artifacts in the same TDD cycle; they were implemented together as a single RED/GREEN pass per the TDD execution flow. This is consistent with the plan's intent (Task 1 action says "write unit tests before considering this task complete").

## Threat Flags

None. No new network endpoints, auth paths, or trust boundary surfaces introduced. The `lock_list_by_prefix` function is a read-only Redis SCAN operation within the existing `state:lock:*` namespace, covered by the plan's threat model.

## Self-Check

- [x] `src/em_proj/state/lock.py` — function defined at line 546
- [x] `tests/unit/test_lock_list.py` — 6 tests, all pass
- [x] Commit f79f81d exists (RED gate)
- [x] Commit 4d09f52 exists (GREEN gate)
- [x] No `import typer` in lock.py (verified via grep)
- [x] No `emit_*` calls in lock_list_by_prefix (D-17 thin-ops)
- [x] `claim.py` not touched (05-02 lane respected)

## Self-Check: PASSED
