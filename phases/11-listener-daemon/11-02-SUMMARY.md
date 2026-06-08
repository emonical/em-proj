---
phase: 11-listener-daemon
plan: "02"
subsystem: session-daemon
tags: [daemon, sigterm, crash-recovery, idempotency, stop-verb, multiprocess-test, lifecycle]
dependency_graph:
  requires:
    - phase-11-plan-01: _daemon.py with _daemon_start, _daemon_foreground_run, HASH ops
    - phase-03: identity.py is_holder_stale (SIGTERM-to-wrong-pid guard)
  provides:
    - session/_daemon.py: _daemon_stop function (all four exit paths)
    - session/__init__.py: session stop verb fully implemented (D-14 thin wrapper)
    - tests/multiprocess/test_daemon_lifecycle.py: TEST-05 complete (7/7 green)
  affects:
    - session/__init__.py: stop verb replaced stub with real implementation
tech_stack:
  added: []
  patterns:
    - "is_holder_stale probe before os.kill — SIGTERM-to-wrong-pid mitigation (T-11-02-01)"
    - "ProcessLookupError catch for race window between stale-check and kill"
    - "Poll-with-deadline in test for get_message(timeout=5.0) exit latency"
key_files:
  created: []
  modified:
    - src/em_proj/session/_daemon.py
    - src/em_proj/session/__init__.py
    - tests/multiprocess/test_daemon_lifecycle.py
decisions:
  - "_daemon_stop returns dict with four statuses: not_running, stale_record_cleared, stop_signaled, stopped — never raises"
  - "os.kill is preceded by is_holder_stale probe; stale path returns without kill (T-11-02-01 mitigated)"
  - "test_daemon_stop_live_daemon polls up to 6s for HASH cleanup (daemon poll loop uses get_message timeout=5.0)"
  - "session stop is self-stop only via resolve_session_id() — D-07 locked decision preserved"
metrics:
  duration_seconds: 1080
  completed_date: "2026-06-08"
  tasks_completed: 2
  files_created: 0
  files_modified: 3
---

# Phase 11 Plan 02: Daemon Stop Verb, Idempotency, and Crash-Recovery Tests Summary

Complete daemon lifecycle: _daemon_stop with SIGTERM-guard, session stop verb (replacing stub), and TEST-05 multiprocess tests proving idempotency, crash-recovery, and clean stop.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add _daemon_stop to _daemon.py and replace session stop stub in __init__.py | 67991b0 | src/em_proj/session/_daemon.py, src/em_proj/session/__init__.py |
| 2 | Add idempotency + crash-recovery + stop multiprocess tests | a5219c0 | tests/multiprocess/test_daemon_lifecycle.py |

## What Was Built

### src/em_proj/session/_daemon.py (extended)

Added `_daemon_stop(session_id: str) -> dict` after `_daemon_start`. Four exit paths:
- `not_running` — no daemon HASH record found (None from `_daemon_record_read`)
- `stale_record_cleared` — stale record detected via `is_holder_stale`; `_daemon_record_del` called; os.kill NOT called
- `stop_signaled` — live daemon found; `os.kill(pid, SIGTERM)` sent; returns `{"status": "stop_signaled", "pid": pid}`
- `stopped` — `ProcessLookupError` caught (daemon exited between stale-check and kill); `_daemon_record_del` called

All threat mitigations applied per plan threat model (T-11-02-01 through T-11-02-05).

### src/em_proj/session/__init__.py (extended)

Replaced `session_stop_cmd` stub body with D-14 thin wrapper:

    json_mode = resolve_json_mode(json_flag)
    client = get_client(); die_if_redis_unreachable(client)
    session_id = resolve_session_id()
    result = _daemon_stop(session_id)
    emit_ok(data=result, json_mode=json_mode)

Added `_daemon_stop` to import from `em_proj.session._daemon`. Exit 0 in all cases.

### tests/multiprocess/test_daemon_lifecycle.py (extended)

TEST-05 complete — 7 daemon lifecycle tests, all green:

| Test | Scenario | Status |
|------|----------|--------|
| test_daemon_foreground_starts_and_records_pid | DAEMON-01: HASH pid matches child pid; SIGTERM cleans up | PASS |
| test_daemon_heartbeat_refreshes_session | DAEMON-03: session TTL stays near 300 at 1s cadence | PASS |
| test_daemon_message_liveness | DAEMON-02: message in inbox without daemon writing it | PASS |
| test_daemon_stop_when_not_running | not_running path; exit 0; no orphan HASH | PASS |
| test_daemon_stop_live_daemon | stop_signaled path; polls up to 6s for HASH cleanup | PASS |
| test_daemon_idempotent_double_start | second listen returns already_running with same pid | PASS |
| test_daemon_crash_recovery | SIGKILL leaves stale HASH; session listen clears and respawns fresh | PASS |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] test_daemon_stop_live_daemon timing: 1s wait not enough for SIGTERM handler**
- **Found during:** Task 2 (first test run)
- **Issue:** The daemon poll loop uses `ps.get_message(timeout=5.0)` — after SIGTERM sets `_shutdown = True`, the daemon can be blocked in `get_message` for up to 5 seconds before the flag is checked and `_daemon_record_del` runs. A fixed 1-second sleep was insufficient.
- **Fix:** Replaced fixed `time.sleep(1.0)` with a poll-with-deadline loop (6s deadline, 0.3s poll interval).
- **Files modified:** tests/multiprocess/test_daemon_lifecycle.py
- **Commit:** a5219c0

## Known Stubs

None — all stubs from Plan 11-01 resolved. The `session_stop_cmd` stub is fully implemented.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes beyond the plan's threat model.

## Test Results

- `scripts/test.sh multiprocess -k daemon` — 7 passed, 0 skipped
- `scripts/test.sh structural -k phase_11` — 7 passed, 1 skipped (planning worktree absent — expected)
- `scripts/test.sh all` — 497 passed, 13 skipped, 9 failed (pre-existing orphan failures, unchanged from base commit e98d3dc)

Pre-existing failures (out of scope — unchanged from origin/main):
- `tests/multiprocess/test_workstream_clobber_demo.py::test_new_path_through_gsd_sdk_refuses_loser`
- `tests/multiprocess/test_workstream_consumer_race.py` (3 tests)
- `tests/structural/test_phase_06_shape.py` (5 tests about gsd-sdk TS/JS containing em-proj shellout)

## Self-Check: PASSED

Files modified exist in the worktree:
- src/em_proj/session/_daemon.py — FOUND
- src/em_proj/session/__init__.py — FOUND
- tests/multiprocess/test_daemon_lifecycle.py — FOUND

Commits exist in git log:
- 67991b0 feat(11-02): add _daemon_stop to _daemon.py and implement session stop verb
- a5219c0 test(11-02): add idempotency, crash-recovery, stop lifecycle tests (TEST-05 complete)
