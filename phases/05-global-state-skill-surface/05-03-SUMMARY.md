---
phase: 05-global-state-skill-surface
plan: "03"
subsystem: state/__init__
tags: [lock-list, claim-list, cli-surface, multiprocess-tests, phase-05]
dependency_graph:
  requires:
    - 05-01  # lock_list_by_prefix pure op in lock.py
    - 05-02  # claim_list_by_prefix pure op in claim.py
  provides:
    - lock-list verb (em-proj state lock-list [--mine] [--stale] [--json])
    - claim-list verb (em-proj state claim-list [--mine] [--active] [--stale] [--json])
  affects:
    - src/em_proj/state/__init__.py
    - tests/multiprocess/test_lock_list_race.py
    - tests/multiprocess/test_claim_list_race.py
tech_stack:
  added: []
  patterns:
    - D-14 three-step thin-verb discipline (resolve_json_mode, die_if_redis_unreachable, pure-op, emit_ok)
    - _HOLDER_DISCLOSURE_KEYS dict comprehension for lock holder redaction (T-5-03-01)
    - subprocess.Popen + communicate(timeout=) for multiprocess test isolation
key_files:
  created:
    - tests/multiprocess/test_lock_list_race.py
    - tests/multiprocess/test_claim_list_race.py
  modified:
    - src/em_proj/state/__init__.py
decisions:
  - "Used hyphenated command names (lock-list, claim-list) via @state_app.command() — simpler than nested Typer apps and consistent with the plan's stated acceptable forms"
  - "Applied _HOLDER_DISCLOSURE_KEYS dict comprehension inline in the lock-list verb body — keeps redaction logic in the verb layer, visible to auditors reading __init__.py"
  - "Claim-list emits all 5 claim fields without redaction (T-5-03-02 accept) — claims have no boot_id or proc_start_epoch"
  - "test_lock_list_concurrent acquires the lock sequentially before the concurrent list race — avoids a timing window where the list runs before the lock is written"
metrics:
  duration: "~20 minutes"
  completed: "2026-05-24T00:20:00Z"
  tasks_completed: 2
  tests_added: 4
  files_created: 2
  files_modified: 1
---

# Phase 5 Plan 03: lock-list and claim-list verb wiring — Summary

**One-liner:** Wired `em-proj state lock-list` and `em-proj state claim-list` CLI verbs in `state/__init__.py`, backed by Wave 1 pure ops, with multi-process race tests confirming concurrent reads are safe.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Wire lock-list and claim-list verbs in state/__init__.py | f8353c3 | src/em_proj/state/__init__.py |
| 2 | Multi-process race tests for lock list and claim list | adada00 | tests/multiprocess/test_lock_list_race.py, tests/multiprocess/test_claim_list_race.py |

## What Was Built

### Task 1 — Two new verbs in `state/__init__.py`

**`lock-list` verb** (`@state_app.command("lock-list")`):
- Parameters: `--mine/--no-mine` (default False), `--stale/--no-stale` (default False), `--json/--no-json` (default None)
- Calls `lock_list_by_prefix(mine=mine, stale=stale)` from `em_proj.state.lock`
- Applies `_HOLDER_DISCLOSURE_KEYS` dict comprehension to each holder before `emit_ok` — excludes `boot_id` and `proc_start_epoch` (T-5-03-01 mitigation)
- Output shape: `{"schema_version":"1","status":"ok","data":{"items":[...]}}`

**`claim-list` verb** (`@state_app.command("claim-list")`):
- Parameters: `--mine/--no-mine`, `--active/--no-active`, `--stale/--no-stale`, `--json/--no-json`
- Calls `claim_list_by_prefix(mine=mine, active=active, stale=stale)` from `em_proj.state.claim`
- No redaction — all 5 claim fields safe to emit (T-5-03-02 accept)
- Output shape: `{"schema_version":"1","status":"ok","data":{"items":[...]}}`

**Imports added:**
- `_HOLDER_DISCLOSURE_KEYS` from `em_proj.output`
- `lock_list_by_prefix` from `em_proj.state.lock`
- `claim_list_by_prefix` from `em_proj.state.claim`

### Task 2 — Multi-process race tests

**`test_lock_list_race.py`** (2 tests):
- `test_lock_list_concurrent`: acquires a lock then runs two concurrent `lock-list --json` calls; both exit 0, both return valid JSON; holder's output contains the lock's `session_id`; no `boot_id`/`proc_start_epoch` in output
- `test_lock_list_empty_concurrent`: two concurrent `lock-list --json` with no locks; both return `{items:[]}`

**`test_claim_list_race.py`** (2 tests):
- `test_claim_list_concurrent`: claims an area then runs two concurrent `claim-list --json` calls; both exit 0; holder's output contains the claim's `session_id` and all 5 required fields
- `test_claim_list_empty_concurrent`: two concurrent `claim-list --json` with no claims; both return `{items:[]}`

## Test Results

- Full unit suite: 213/213 pass (no regressions)
- Full multiprocess suite: 26/26 pass
- Full test suite (`all`): 300 passed, 3 skipped (pre-existing planning-worktree skips)

## Deviations from Plan

None — plan executed exactly as written.

The plan gave explicit latitude on command naming ("either form is acceptable"); hyphenated `lock-list` / `claim-list` was chosen as the simpler path (single `@state_app.command()` decorator, no nested Typer apps).

## Threat Flags

None. No new network endpoints, auth paths, or trust boundary surfaces introduced. Both new verbs are read-only Redis SCAN operations within existing `state:lock:*` and `state:claim:*` namespaces, covered by the plan's threat model (T-5-03-01 mitigated by `_HOLDER_DISCLOSURE_KEYS` redaction; T-5-03-02 and T-5-03-03 accepted as planned).

## Self-Check

- [x] `src/em_proj/state/__init__.py` — `def lock_list` at line ~589, `def claim_list` at line ~631
- [x] `tests/multiprocess/test_lock_list_race.py` — 2 tests, all pass
- [x] `tests/multiprocess/test_claim_list_race.py` — 2 tests, all pass
- [x] Commit f8353c3 exists (Task 1)
- [x] Commit adada00 exists (Task 2)
- [x] `em-proj state lock-list --json` exits 0, returns `{"schema_version":"1","status":"ok","data":{"items":[]}}`
- [x] `em-proj state claim-list --json` exits 0, returns same shape
- [x] `_HOLDER_DISCLOSURE_KEYS` imported and applied in `lock_list` verb body
- [x] No redaction in `claim_list` verb body (all 5 fields safe)
- [x] No `import typer` in lock.py or claim.py (D-17 preserved)

## Self-Check: PASSED
