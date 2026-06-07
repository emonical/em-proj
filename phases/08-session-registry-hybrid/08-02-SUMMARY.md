---
phase: 08-session-registry-hybrid
plan: "02"
subsystem: session
tags: [session-registry, cli, typer, d14-thin-verb-shell, package-restructure]
dependency_graph:
  requires:
    - em_proj.session._ops (session_register, session_heartbeat, session_list, session_show, SessionNotFound)
    - em_proj.output (emit_ok, emit_not_found, resolve_json_mode)
    - em_proj.redis_client (get_client, die_if_redis_unreachable)
  provides:
    - em_proj.session (session_app Typer + 4 verb commands)
    - em-proj session register/heartbeat/list/show CLI verbs
  affects:
    - Plan 08-03 (harness invokes these verbs fork+exec style for TEST-03)
    - src/em_proj/cli.py (session subcommand mounted)
tech_stack:
  added:
    - src/em_proj/session/__init__.py (new package init: session_app + 4 verb commands)
    - src/em_proj/session/_ops.py (moved from session.py via git mv)
  patterns:
    - D-14 thin-verb-shell discipline (3-line verb bodies: resolve → precheck → call → emit)
    - Package layout for future verb expansion (session listen in Phase 11)
    - Re-export pattern: __init__.py re-exports all of _ops.py's public + test-accessible symbols
key_files:
  created:
    - src/em_proj/session/__init__.py
  modified:
    - src/em_proj/cli.py
    - src/em_proj/session/_ops.py (git mv from session.py — no content changes)
    - tests/unit/test_session.py (forbidden-import test updated for package layout)
decisions:
  - Package layout chosen (session/ dir) over flat __init__.py for Phase 11 expansion (session listen)
  - All _ops.py symbols re-exported from __init__.py to maintain backward-compatible import paths
  - Private helpers (_build_session_key, _hgetall_to_session, _scan_all_holders_by_session_id) re-exported to preserve existing test import paths without modifying test structure
metrics:
  duration_seconds: 420
  completed_date: "2026-06-07T22:10:00Z"
  tasks_completed: 2
  files_created: 1
  files_modified: 3
---

# Phase 08 Plan 02: Session CLI Verbs Summary

**One-liner:** session.py converted to package (session/_ops.py + session/__init__.py); four D-14 thin-verb-shell commands wired into em-proj CLI via app.add_typer.

## Completed Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create session package with session_app and four verb commands | 8375667 | src/em_proj/session/__init__.py, src/em_proj/session/_ops.py, tests/unit/test_session.py |
| 2 | Mount session_app on cli.py and smoke-test all four verbs | 6dbbde2 | src/em_proj/cli.py |

## What Was Built

**Package restructuring:** `src/em_proj/session.py` (flat module from 08-01) was renamed via `git mv` to `src/em_proj/session/_ops.py`, and `src/em_proj/session/__init__.py` was created as the D-14 mount point.

**`src/em_proj/session/__init__.py`** — the typer CLI layer:
- `session_app = typer.Typer(name="session", no_args_is_help=True, ...)`
- Four verb commands following D-14 thin-verb-shell discipline:
  - `register` — resolve_json_mode → get_client → die_if_redis_unreachable → session_register() → emit_ok
  - `heartbeat` — same pattern; catches SessionNotFound → emit_not_found (exit 2)
  - `list` — same pattern; emits list result directly
  - `show <session_id>` — same pattern + SessionNotFound → emit_not_found (exit 2)
- Full re-export of all `_ops.py` public and test-accessible symbols

**`src/em_proj/cli.py`** — two-line edit:
- `from em_proj.session import session_app`
- `app.add_typer(session_app, name="session", help="Session registry — register, heartbeat, list, show.")`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Forbidden imports test checked wrong file after package restructuring**
- **Found during:** Task 1 unit test run
- **Issue:** `test_session_py_has_no_forbidden_imports` used `inspect.getfile(em_proj.session)` which returned `__init__.py` (the new CLI module) after the package conversion. The `__init__.py` legitimately imports typer, so the test failed.
- **Fix:** Updated test to import `em_proj.session._ops` directly (the business logic module) for the source-level forbidden-import check. The prohibition on typer/multiprocessing applies to the ops module, not the CLI mount module.
- **Files modified:** tests/unit/test_session.py
- **Commit:** 8375667

## Threat Surface Scan

No new network endpoints or trust boundaries. The `session show <session_id>` argv is passed to Redis as a HASH key suffix (`state:session:<session_id>`) with no shell interpolation — per threat register T-08-02-01, this is mitigated. No secrets emitted in output (T-08-02-02 accepted).

## Known Stubs

None. All four verb commands are fully wired to live ops functions.

## Self-Check

- [x] src/em_proj/session/__init__.py exists with session_app Typer and four @session_app.command verbs
- [x] src/em_proj/session/_ops.py exists (moved from session.py)
- [x] src/em_proj/cli.py contains session_app 2x (import + add_typer)
- [x] `from em_proj.session import session_register` succeeds
- [x] `em-proj session --help` exits 0 and shows 4 subcommands
- [x] `em-proj session register --json` exits 0 and emits schema_version=1, status=ok, 9-field data
- [x] `em-proj session list --json` exits 0 and emits schema_version=1, status=ok
- [x] `em-proj session show nonexistent-xyz --json` exits 2 (not_found)
- [x] All 301 unit tests pass (no regressions)
- [x] Commit 8375667 (Task 1) exists in git log
- [x] Commit 6dbbde2 (Task 2) exists in git log

## Self-Check: PASSED
