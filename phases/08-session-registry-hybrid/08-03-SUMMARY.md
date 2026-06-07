---
phase: 08-session-registry-hybrid
plan: "03"
subsystem: session
tags: [session-registry, test-harness, multiprocess, structural, TEST-03]
dependency_graph:
  requires:
    - em_proj.session._ops (session_register, session_list, SESSION_OPS path)
    - em_proj.session.__init__ (session_app, four verb commands)
    - em_proj.cli (session_app mounted)
    - tests.conftest (EM_PROJ_BIN, TEST_DB, clean_db fixture)
    - em_proj.identity (current_process_composite, is_holder_stale)
    - Redis db=15 (test isolation via EM_PROJ_REDIS_DB=15)
  provides:
    - TEST-03 proof (registry liveness + enrichment + stale reaping)
    - Phase 8 AST shape assertions (9 structural invariants)
  affects:
    - gsd-verify-phase: can now use scripts/verify-phase.sh to run both test files
    - Future phases: test_phase_08_shape.py encodes locked design choices
tech_stack:
  added:
    - tests/multiprocess/test_session_registry.py (TEST-03 harness, 4 tests)
    - tests/structural/test_phase_08_shape.py (AST shape assertions, 10 tests)
  patterns:
    - Test runner live-pid registration (for points 1+2) + CLI list boundary proof
    - CLI register short-lived pid (for points 3+4) proving D3 reaping and D2 TTL
    - Direct Redis EXPIRE manipulation for TTL lapse test (T-08-03-01 accepted)
    - Source-text grep for locked design choices (KEY_PREFIX, TTL_DEFAULT, imports)
key_files:
  created:
    - tests/multiprocess/test_session_registry.py
    - tests/structural/test_phase_08_shape.py
  modified: []
decisions:
  - Register strategy: points 1+2 use _register_session_for_test() (writes to Redis with test runner's live pid) so is_holder_stale returns False; points 3+4 use CLI register subprocess (short-lived pid) to exercise the stale-reaping and TTL-expiry paths
  - Structural test uses pytest.skip (not xfail) for absent PHASE_DIR — absent planning worktree is a legitimate dev setup, not a regression
  - Forbidden import check filters to actual import lines (startswith 'import '/'from ') to avoid false positives from docstring references
metrics:
  duration_seconds: 720
  completed_date: "2026-06-07T23:10:00Z"
  tasks_completed: 2
  files_created: 2
  files_modified: 0
---

# Phase 08 Plan 03: TEST-03 Session Registry Harness Summary

**One-liner:** TEST-03 multiprocess harness with four tests proving registry liveness, D4 enrichment join, D3 stale reaping, and D2 TTL backstop via CLI list boundary; plus 10-assertion structural shape file encoding Phase 8 locked design choices.

## Completed Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | TEST-03 multiprocess session registry harness | f98450e | tests/multiprocess/test_session_registry.py |
| 2 | Phase 8 AST structural shape assertions | (see below) | tests/structural/test_phase_08_shape.py |

## What Was Built

**tests/multiprocess/test_session_registry.py** — the TEST-03 harness:

Four test functions, each covering one TEST-03 validation point:

- `test_registered_child_appears_in_list` (point 1): Writes a session record with the test runner's live pid, then calls `em-proj session list --json` via subprocess. Asserts the session appears with correct field types (pid as int, cwd as string, held sub-keys present).

- `test_enrichment_shows_held_claim_under_session_id` (point 2): Same registration approach + `em-proj state claim <area> --json` in the same session_id. Asserts `held["claims"] >= 1` in the list enrichment — proves D4 cross-namespace join end-to-end.

- `test_killed_child_excluded_and_reaped` (point 3): Runs `em-proj session register --json` via subprocess (short-lived process, pid dies immediately). After a brief sleep, calls `session list --json` and asserts the session is absent + Redis key is DELed — proves D3 lazy reaping.

- `test_ttl_lapse_session_absent_from_list` (point 4): Registers via CLI subprocess, then directly sets TTL=1 via `clean_db.expire()`, sleeps 2s, calls `session list --json`. Asserts the session is absent — proves D2 TTL backstop.

**Design deviation from plan**: Tests 1 and 2 cannot use CLI `session register --json` directly for the register step because the CLI process exits immediately after registering its own pid, and `session list` immediately reaps the dead-pid session. Instead, `_register_session_for_test()` writes a Redis session HASH directly with the test runner's live pid. This is the correct approach: the CLI boundary being tested is `session list` (the read side), and the registration data is realistic. Tests 3 and 4 use CLI register specifically to exercise the dead-pid reaping path.

**tests/structural/test_phase_08_shape.py** — 10 structural assertions:

| Test | What It Enforces |
|------|-----------------|
| test_session_module_exists_and_has_required_functions | session ops file exists; 6 required symbols present |
| test_session_key_prefix_is_machine_global | KEY_PREFIX = "state:session:" locked |
| test_session_ttl_default_is_five_minutes | TTL_DEFAULT = 300 locked |
| test_session_module_prohibits_forbidden_imports | No typer/multiprocessing/threading in ops |
| test_session_app_wired_in_cli | session_app appears >= 2× in cli.py |
| test_session_init_has_four_verb_commands | >= 4 @session_app.command decorators |
| test_session_not_found_exception_has_code_attribute | SessionNotFound has code = "not_found" |
| test_lua_scripts_are_present | LUA_SESSION_UPSERT and LUA_SESSION_HEARTBEAT present |
| test_cross_namespace_scan_covers_all_three_namespaces | claim/lock/reserve namespaces all scanned |
| test_phase_08_summaries_exist | Every 08-*-PLAN.md has a SUMMARY sibling |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] CLI register subprocess pid immediately dead — tests 1 and 2 need live pid**
- **Found during:** Task 1 (first test run)
- **Issue:** `em-proj session register --json` is a short-lived process that records its own pid and exits. By the time `session list` is called, is_holder_stale detects the dead pid and excludes (and DELs) the session. Tests 1 and 2 failed with empty list.
- **Fix:** Created `_register_session_for_test()` helper that writes the session HASH directly to Redis using the test runner's live pid (via `current_process_composite()`). The test runner remains alive for the duration of each test. Tests 3 and 4 retain the CLI register approach since they specifically exercise the dead-pid stale-reaping path.
- **Files modified:** tests/multiprocess/test_session_registry.py
- **Commit:** included in f98450e

## Known Stubs

None. All four test functions exercise real em-proj CLI subprocesses against real Redis db=15.

## Threat Surface Scan

No new network endpoints or auth paths introduced. Tests write to Redis db=15 only (T-08-03-01 accepted — test isolation database). No new source files that touch trust boundaries.

## Self-Check

- [x] tests/multiprocess/test_session_registry.py exists with 4 test functions
- [x] tests/structural/test_phase_08_shape.py exists with 10 test functions
- [x] `scripts/test.sh multiprocess -k "session"` passes — all 4 tests green
- [x] `scripts/test.sh structural` passes — all 10 structural tests green (plus skips for earlier phases without planning worktree)
- [x] `scripts/test.sh all` passes — no regressions
- [x] Each task committed individually (test files only, no STATE.md/ROADMAP.md)
- [x] Commit f98450e (Task 1) exists in git log
- [x] No multiprocessing imports in test files
- [x] All Popen calls have EM_PROJ_REDIS_DB in env
- [x] All communicate() calls have timeout argument

## Self-Check: PASSED
