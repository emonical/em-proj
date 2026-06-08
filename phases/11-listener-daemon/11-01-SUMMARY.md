---
phase: 11-listener-daemon
plan: "01"
subsystem: session-daemon
tags: [daemon, pubsub, redis, subprocess, signal, heartbeat, structural-test, multiprocess-test]
dependency_graph:
  requires:
    - phase-10: message/_ops.py send path (mbox_write at send time — DAEMON-02 system-level basis)
    - phase-08: session/_ops.py session_register/session_heartbeat (DAEMON-03 basis)
    - phase-03: identity.py is_holder_stale (crash detection)
  provides:
    - session/_daemon.py: daemon ops module (start, foreground run, HASH read/write/del)
    - session/__init__.py: listen verb (start + --foreground) and stop stub
    - tests/structural/test_phase_11_shape.py: 8 structural invariants (7 green, 1 skips)
    - tests/multiprocess/test_daemon_lifecycle.py: 4 lifecycle tests (3 green, 1 skips)
  affects:
    - session/__init__.py: extended with 2 new commands (listen, stop)
tech_stack:
  added: []
  patterns:
    - "subprocess.Popen(start_new_session=True) for detached daemon spawn"
    - "Lua atomic write-or-detect for single-instance HASH enforcement"
    - "SIGTERM handler via signal.signal + nonlocal flag for clean shutdown"
    - "pubsub.get_message(timeout=5.0) single-threaded poll loop"
    - "EM_PROJ_DAEMON_HEARTBEAT_INTERVAL env override for test cadence control"
key_files:
  created:
    - src/em_proj/session/_daemon.py
    - tests/structural/test_phase_11_shape.py
    - tests/multiprocess/test_daemon_lifecycle.py
  modified:
    - src/em_proj/session/__init__.py
decisions:
  - "DAEMON_KEY_PREFIX='daemon:' — machine-global namespace distinct from state:*/mbox:*/topic:*"
  - "Lua LUA_DAEMON_WRITE_OR_DETECT atomic write-or-detect prevents double-start races"
  - "Two Redis clients in _daemon_foreground_run: cmd_client for EVAL/heartbeat, ps=cmd_client.pubsub() for subscribe (Pitfall 2 avoidance)"
  - "DAEMON-02 satisfied at system level: no mbox_write in _daemon.py; send-time write in message/_ops.py is the durable record"
  - "env=None on Popen (inherit parent env) ensures EM_PROJ_REDIS_DB propagates to daemon child"
  - "stop stub in Plan 11-01 satisfies structural test; full implementation in Plan 11-02"
metrics:
  duration_seconds: 462
  completed_date: "2026-06-08"
  tasks_completed: 3
  files_created: 3
  files_modified: 1
---

# Phase 11 Plan 01: Daemon Body Module, Verb Wiring, and Lifecycle Tests Summary

Daemon body module with Redis pub/sub foreground loop, Lua atomic single-instance enforcement, SIGTERM-clean shutdown, heartbeat integration, and multiprocess lifecycle tests proving DAEMON-01/02/03 at the system level.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create _daemon.py with daemon HASH ops and foreground loop | 96f1338 | src/em_proj/session/_daemon.py |
| 2 | Wire session listen verb + structural shape test | e82a3b2, 7f22070 | src/em_proj/session/__init__.py, tests/structural/test_phase_11_shape.py |
| 3 | Write multiprocess daemon lifecycle tests (all green) | 2bb6283 | tests/multiprocess/test_daemon_lifecycle.py |

## What Was Built

### src/em_proj/session/_daemon.py (new)

The daemon ops module. All subprocess/signal/shutil code for the listener daemon lives here, intentionally separate from _ops.py (which prohibits these imports).

Key exports:
- `DAEMON_KEY_PREFIX = "daemon:"` — key namespace for daemon HASH records
- `DAEMON_HEARTBEAT_INTERVAL` — default 60s, overridable via `EM_PROJ_DAEMON_HEARTBEAT_INTERVAL`
- `LUA_DAEMON_WRITE_OR_DETECT` — Lua atomic write-or-detect (mirrors LUA_SESSION_UPSERT pattern)
- `_daemon_start(session_id)` — single-instance enforcement + `Popen(start_new_session=True)`
- `_daemon_foreground_run(session_id)` — poll loop + SIGTERM handler + heartbeat + clean shutdown
- `_daemon_record_read/write/del/_daemon_clear_if_stale` — HASH primitive ops

Threat mitigations applied (from plan threat model):
- T-11-01-01: `_daemon_record_read` converts raw Redis string to `int(raw["pid"])` — is_holder_stale requires int
- T-11-01-02: Two separate Redis clients — cmd_client for EVAL/heartbeat, ps=cmd_client.pubsub() for subscribe — prevents PubSub/EVAL connection conflict
- T-11-01-03: `env=None` on Popen inherits parent env including EM_PROJ_REDIS_DB
- T-11-01-04: Lua LUA_DAEMON_WRITE_OR_DETECT atomic — second caller receives existing record; if not stale → sys.exit(0)

### src/em_proj/session/__init__.py (extended)

Added two new commands:
- `@session_app.command("listen")` — D-14 thin wrapper: die_if_redis_unreachable → resolve_session_id → `_daemon_foreground_run(session_id)` (--foreground) or `_daemon_start(session_id)` + emit_ok (default)
- `@session_app.command("stop")` — stub emitting `{"status": "not_implemented"}` (Plan 11-02 TODO)

### tests/structural/test_phase_11_shape.py (new)

8 structural invariants. 7 green after this plan, 1 skips (test_phase_11_summaries_exist when planning worktree absent):
- File presence, subprocess import present in _daemon.py
- No typer in _daemon.py imports
- AST check: no mbox_write calls in _daemon.py
- _ops.py still clean (no subprocess)
- Both listen and stop commands registered in __init__.py
- DAEMON_KEY_PREFIX defined

### tests/multiprocess/test_daemon_lifecycle.py (new)

4 lifecycle tests against Redis db=15 (3 green, 1 skips):
- `test_daemon_foreground_starts_and_records_pid` — DAEMON-01: HASH appears with matching pid; deleted on SIGTERM
- `test_daemon_heartbeat_refreshes_session` — DAEMON-03: session TTL stays near 300s at 1s heartbeat cadence
- `test_daemon_message_liveness` — DAEMON-02 system-level: send a directed message while daemon alive; inbox contains it without daemon writing it
- `test_daemon_stop_when_not_running` — skip-stub for Plan 11-02

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

| Stub | File | Reason |
|------|------|--------|
| `session_stop_cmd` emits `{"status": "not_implemented"}` | src/em_proj/session/__init__.py | Full stop verb (read HASH, validate pid, send SIGTERM, wait for DEL) deferred to Plan 11-02 per plan specification |

This stub is intentional and documented. Plan 11-02 implements the full stop verb. The stub satisfies the structural test requirement that `@session_app.command("stop")` exists, enabling `test_session_init_has_listen_stop_commands` to pass without coupling plans.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes beyond what is documented in the plan's threat model.

## Test Results

- `scripts/test.sh structural -k phase_11` — 7 passed, 1 skipped (planning worktree absent)
- `scripts/test.sh multiprocess -k daemon` — 3 passed, 1 skipped (Plan 11-02 stub)
- `scripts/test.sh all` — 493 passed, 14 skipped, 9 failed (pre-existing Phase 6 gsd-sdk workstream tests unrelated to Phase 11)

Pre-existing failures (out of scope — existed on base commit 763a593):
- `tests/multiprocess/test_workstream_clobber_demo.py::test_new_path_through_gsd_sdk_refuses_loser`
- `tests/multiprocess/test_workstream_consumer_race.py` (3 tests)
- `tests/structural/test_phase_06_shape.py` (5 tests about gsd-sdk TS/JS containing em-proj shellout)

## Self-Check: PASSED

Files created/modified exist in the worktree:
- src/em_proj/session/_daemon.py — FOUND
- tests/structural/test_phase_11_shape.py — FOUND
- tests/multiprocess/test_daemon_lifecycle.py — FOUND
- src/em_proj/session/__init__.py — FOUND (modified)

Commits exist in git log:
- 96f1338 feat(11-01): add _daemon.py with foreground loop, HASH ops, Lua write-or-detect
- e82a3b2 feat(11-01): add session listen --foreground verb wiring and stop stub
- 7f22070 test(11-01): add structural shape invariants for Phase 11
- 2bb6283 test(11-01): add multiprocess daemon lifecycle tests (foreground, heartbeat, message-liveness)
