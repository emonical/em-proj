---
phase: 05-global-state-skill-surface
plan: "02"
subsystem: state/claim
tags: [claim, list, pure-ops, tdd, redis-scan]
dependency_graph:
  requires:
    - "04-01: claim_take, claim_release, _hgetall_to_holder, KEY_PREFIX"
    - "03-01: resolve_session_id, resolve_project_hash (identity.py)"
    - "02-xx: get_client, redis_client chokepoint (D-18)"
  provides:
    - "claim_list_by_prefix in src/em_proj/state/claim.py"
    - "7 unit tests in tests/unit/test_claim_list.py"
  affects:
    - "05-03: claim_list verb will wire claim_list_by_prefix as the pure-op backend"
tech_stack:
  added: []
  patterns:
    - "scan_iter(match=prefix + '*', count=100) for key enumeration"
    - "lazy TTL fetch pattern (_ttl = None; fetch once when active or stale filter active)"
    - "TDD RED/GREEN cycle: failing tests committed first, implementation second"
key_files:
  created:
    - tests/unit/test_claim_list.py
  modified:
    - src/em_proj/state/claim.py
decisions:
  - "Lazy TTL fetch: single client.ttl() call per key when active or stale filter is set (IMPORTANT instruction from plan: 'if both active and stale need ttl, fetch ttl once per key, not twice')"
  - "Stale filter covers TTL <= 0: both -1 (persistent/no-expire key) and 0 (about to expire)"
  - "Malformed HASH entries caught via KeyError/ValueError and silently skipped (T-5-02-03 accept disposition)"
  - "cross-project listing explicitly out of scope: scan prefix locked to KEY_PREFIX + current_project_hash + ':'"
metrics:
  duration: "~15 minutes"
  completed: "2026-05-24T23:36:38Z"
  tasks_completed: 2
  files_changed: 2
---

# Phase 05 Plan 02: claim_list_by_prefix Pure Op Summary

**One-liner:** Redis SCAN-based claim enumeration with mine/active/stale filter predicates, scoped to current project_hash.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Failing tests for claim_list_by_prefix | d78d8ba | tests/unit/test_claim_list.py (created) |
| 1+2 (GREEN) | claim_list_by_prefix implementation | b4e7498 | src/em_proj/state/claim.py (modified) |

## What Was Built

### `claim_list_by_prefix` in `src/em_proj/state/claim.py`

New public function appended after `claim_check`. Signature:

```python
def claim_list_by_prefix(
    mine: bool = False,
    active: bool = False,
    stale: bool = False,
) -> list[dict]:
```

Implementation flow:
1. `resolve_project_hash()` → scoped scan prefix
2. `scan_iter(match=prefix + "*", count=100)` iterates all claim keys for current project
3. Per key: `hgetall` → skip empty → `_hgetall_to_holder` (KeyError/ValueError → skip)
4. Filter `mine`: skip if `holder["session_id"] != resolve_session_id()`
5. Lazy TTL fetch: one `client.ttl(key)` call if active or stale filter active
6. Filter `active`: skip if `_ttl <= 0`
7. Filter `stale`: skip if `_ttl > 0`
8. Returns collected list (empty list when no matches)

### `tests/unit/test_claim_list.py` — 7 test functions

All 7 tests pass:
- `test_claim_list_empty`: no claims → returns `[]`
- `test_claim_list_returns_holder`: after `claim_take`, returns holder with 5 required fields
- `test_claim_list_mine_filter`: fabricated other-session claim excluded when `mine=True`
- `test_claim_list_active_filter`: persistent key (ttl=-1) excluded when `active=True`
- `test_claim_list_stale_filter`: persistent key (ttl=-1) returned when `stale=True`
- `test_claim_list_malformed_skip`: incomplete HASH fields → no exception, key skipped
- `test_claim_list_scoped_to_project`: `KEY_PREFIX + "other-project-hash:area"` excluded

## Deviations from Plan

None — plan executed exactly as written.

The pre-existing test failures seen during `bash scripts/test.sh unit` runs (in
`test_lock_kv.py`, `test_lock_hold_run.py`, `test_state_lock_verbs.py`) are within
the 05-01 agent's scope (lock.py changes) and are not regressions caused by this plan.
All claim-scoped tests (test_claim.py, test_claim_verbs.py, test_claim_list.py) pass.

## Threat Surface Scan

No new network endpoints, auth paths, or trust boundary changes introduced.
`claim_list_by_prefix` reads from Redis (existing trust boundary). The SCAN prefix
scope constraint (T-5-02-02) is implemented: only `KEY_PREFIX + current_project_hash + ":"` prefix scanned.

## Self-Check: PASSED

- [x] `grep -n "def claim_list_by_prefix" src/em_proj/state/claim.py` → line 413
- [x] `grep "typer" src/em_proj/state/claim.py` → no import statements (docstring mentions only)
- [x] `tests/unit/test_claim_list.py` exists and was created
- [x] Commits d78d8ba (RED) and b4e7498 (GREEN) exist in git log
- [x] 159 tests pass (all non-lock-scope unit tests)
- [x] TDD gate compliance: `test(05-02)` commit precedes `feat(05-02)` commit
