---
phase: 06-gsd-sdk-workstream-consumer
plan: "02"
subsystem: multiprocess-tests
tags: [gsd-sdk, workstream, race, consumer, multiprocess, CONSUMER-02, SC3]
dependency_graph:
  requires:
    - 06-01-SUMMARY.md  # Phase 6 gsd-sdk claim gate patch must be landed
  provides:
    - CONSUMER-02 multiprocess evidence (race → one winner, one held_by_another)
    - SC#3 side-by-side demo (clobber vs. resolution in ~5 seconds)
  affects:
    - tests/multiprocess/test_workstream_consumer_race.py (new)
    - tests/multiprocess/test_workstream_clobber_demo.py (new)
tech_stack:
  added: []
  patterns:
    - subprocess.Popen with cwd= kwarg (NOT --cwd flag) to invoke TS handler
    - _global_path() venv-scrub to ensure gsd-sdk finds globally-installed em-proj
    - Module-level gsd-sdk skip (Q-E Shape B: allow_module_level=True)
    - Self-contained helpers (no shared module between test files)
    - EM_PROJ_REDIS_DB=15 + distinct CLAUDE_CODE_SESSION_ID per child
key_files:
  created:
    - tests/multiprocess/test_workstream_consumer_race.py
    - tests/multiprocess/test_workstream_clobber_demo.py
  modified: []
decisions:
  - "_global_path() strips venv-resident em-proj so the globally-installed Phase 5/6 binary (with state claim verb) is found by gsd-sdk subprocesses rather than the stale Phase 2 venv copy that lacks the claim verb and exits 2"
  - "cwd= kwarg (not --cwd flag) on Popen: --cwd routes gsd-sdk to CJS fallback handler (no claim gate); cwd= kwarg sets process.cwd() so TS handler resolves .planning/workstreams/ correctly"
  - "Self-contained _global_path() copy in clobber_demo rather than shared module import — project convention for multiprocess test files"
metrics:
  duration: "~25 minutes (including 3 fix iterations)"
  completed: "2026-05-27"
  tasks_completed: 3
  files_changed: 2
---

# Phase 06 Plan 02: gsd-sdk Workstream Consumer Tests Summary

Two multiprocess test files that drive `gsd-sdk` as the subject-under-test to prove
CONSUMER-02 and SC#3 end-to-end at the CLI boundary. Both tests pass against the
Phase 6 gsd-sdk claim gate patch.

## Tasks Completed

| Task | Description | Commit | Status |
|------|-------------|--------|--------|
| 1 | test_workstream_consumer_race.py: race + refresh + Q-B fallback | `9513c60` | done |
| 2 | test_workstream_clobber_demo.py: SC#3 side-by-side demo | `65e51db` | done |
| 3 | Full suite regression (130 passed, 1 skipped) | — | done |

## Artifacts

### `tests/multiprocess/test_workstream_consumer_race.py`

Three tests proving CONSUMER-02, Pitfall #4, and Q-B:

- **test_two_sessions_race_workstream_set_one_wins** (CONSUMER-02): Two parallel
  `gsd-sdk query workstream.set` calls via `subprocess.Popen(cwd=str(tmp_path))`
  with distinct `CLAUDE_CODE_SESSION_ID` values. Exactly one returns `{set: true}`;
  the other returns `{error: "held_by_another", holder: {...}}`. Post-race Redis
  assertion confirms winner's claim key is still alive with positive TTL.

- **test_same_session_refresh_does_not_conflict** (Pitfall #4 sanity): Same session
  calls `workstream.set` twice in sequence. Both calls return `{set: true}`. No
  `held_by_another` on repeat — refresh-or-take Lua semantics carry through the
  gsd-sdk shell-out boundary.

- **test_em_proj_missing_falls_through_with_warning** (Q-B): PATH-scrubbed env
  removes em-proj. gsd-sdk exits 0 and emits documented `em-proj not on PATH`
  warning to stderr. The legacy unguarded file-write path proceeds:
  `.planning/active-workstream` is created.

### `tests/multiprocess/test_workstream_clobber_demo.py`

Two tests for SC#3 side-by-side demo (run with `bash scripts/test.sh multiprocess -k clobber_demo`):

- **test_old_path_direct_file_write_clobbers**: Reproduces pre-Phase-6 behavior.
  Two python3 subprocesses write `.planning/active-workstream` directly in parallel.
  Whoever finishes last wins. No structured displacement signal.

- **test_new_path_through_gsd_sdk_refuses_loser**: Phase 6 resolution. Same race,
  same `tmp_path`, but through `gsd-sdk`. Exactly one winner, one loser with
  `{error: "held_by_another", holder: {...}}`. The structured signal IS the fix.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] _global_path() helper to exclude venv-resident em-proj**

- **Found during:** Task 1 test execution from worktree
- **Issue:** This worktree is at Phase 2 commit (0b4af36). Its venv contains a Phase 2
  em-proj (no `state claim` verb). When `uv run pytest` runs from the worktree, it
  prepends the venv bin to PATH. gsd-sdk's `spawnSync('em-proj', ['state', 'claim', ...])` 
  found the Phase 2 binary first, which exited 2 (unknown verb). The workstream.js
  handler only checks exit codes 3, 1, and ENOENT — exit 2 fell through ALL checks
  and proceeded to `setActiveWorkstream`. Both race contestants returned `{set: True}`.
- **Fix:** Added `_global_path()` helper that strips PATH entries sitting inside a
  `.venv` subtree that contain an `em-proj` binary. Injected as `"PATH": safe_path`
  into all child subprocess envs in both test files. This ensures gsd-sdk finds the
  globally-installed Phase 5/6 em-proj (`~/.local/bin/em-proj`) with the `state claim`
  verb.
- **Files modified:** `test_workstream_consumer_race.py`, `test_workstream_clobber_demo.py`
- **Commits:** included in `9513c60`, `65e51db`

**2. [Rule 1 - Bug] cwd= kwarg, not --cwd flag**

- **Found during:** Plan authoring research (pre-execution knowledge from 06-RESEARCH.md)
- **Issue:** The plan's `key_links` section specified `['gsd-sdk', ..., '--cwd', str(tmp_path)]`
  but passing `--cwd` as a CLI flag routes gsd-sdk to the CJS fallback handler
  (`workstream.cjs` → `cmdWorkstreamSet`) which has NO Phase 6 claim gate.
- **Fix:** Used `cwd=str(tmp_path)` as a `subprocess.Popen` keyword argument instead.
  This sets Node's `process.cwd()` to `tmp_path` so the TS handler resolves
  `.planning/workstreams/` correctly and the Phase 6 claim gate fires.
- **Files modified:** `test_workstream_consumer_race.py`, `test_workstream_clobber_demo.py`

## Test Results

```
# Task 3 — full multiprocess suite
12/12 passed (1.56s)

# Full suite regression
130 passed, 1 skipped (3.20s)
# skipped: tests/structural/test_phase_02_shape.py:363
#   .planning/phases/02-cli-shell-kv-primitive not present — planning worktree
#   may not be attached on this checkout (expected, pre-existing)
```

## Known Stubs

None — all test assertions use real gsd-sdk subprocess output; no mocked or
hardcoded response data.

## Self-Check: PASSED

- `tests/multiprocess/test_workstream_consumer_race.py` — exists
- `tests/multiprocess/test_workstream_clobber_demo.py` — exists
- Commit `9513c60` — verified via `git log`
- Commit `65e51db` — verified via `git log`
- All 5 consumer/clobber tests pass
- Full suite 130 passed, 1 skipped (expected)
