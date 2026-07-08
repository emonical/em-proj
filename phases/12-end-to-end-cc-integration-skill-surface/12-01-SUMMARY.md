---
phase: 12-end-to-end-cc-integration-skill-surface
plan: "01"
subsystem: cc-integration-hooks
tags: [hooks, session-start, user-prompt-submit, mailbox, graceful-degradation]
dependency-graph:
  requires: []
  provides:
    - scripts/hooks/session_start.py
    - scripts/hooks/user_prompt_submit.py
    - .claude/settings.json (hooks.SessionStart, hooks.UserPromptSubmit)
  affects:
    - Plan 12-02 (HOOK-03 A-to-B proof + /em-sessions skill build on these two scripts)
tech-stack:
  added: []
  patterns:
    - "Single top-level try/except Exception: pass + unconditional trailing sys.exit(0) — mirrors ~/.claude/scripts/session-registry.py's swallow-all-errors contract"
    - "Opt-in gate on the literal EM_SESSIONS_AUTOSTART env var — unset is a true zero-Redis-write no-op"
    - "Hook scripts shell out to the em-proj binary via subprocess only, never import em_proj package internals"
key-files:
  created:
    - scripts/hooks/session_start.py
    - scripts/hooks/user_prompt_submit.py
    - tests/multiprocess/test_em_sessions_hooks.py
    - tests/structural/test_hook_script_boundaries.py
  modified:
    - .claude/settings.json
decisions:
  - "Daemon-existence assertion in test_session_start_hook_registers_and_starts_daemon_when_gated_on polls (3s deadline, 0.1s interval) rather than asserting immediately — the detached daemon child writes its own Redis HASH record asynchronously (same pattern as tests/multiprocess/test_daemon_lifecycle.py::test_daemon_start_detaches); a bare assertion was flaky."
metrics:
  duration: "~35 min"
  completed: "2026-07-08"
status: complete
---

# Phase 12 Plan 01: SessionStart + UserPromptSubmit hooks Summary

Shipped the two Claude Code hook scripts that make `em-proj session`/`em-proj message` a live, opt-in, zero-footprint-by-default integration: `session_start.py` auto-registers the session and starts its listener daemon, `user_prompt_submit.py` surfaces and consumes the session's unread mailbox as turn context — both gated on `EM_SESSIONS_AUTOSTART=1` and both unconditionally exiting 0 regardless of failure mode.

## What Was Built

- **`scripts/hooks/session_start.py`** (HOOK-01) — SessionStart hook. Reads the hook JSON payload from stdin, and when `EM_SESSIONS_AUTOSTART == "1"`, propagates the hook's `session_id` into `CLAUDE_CODE_SESSION_ID` and shells out to `em-proj session register` then `em-proj session listen` (detached, no `--foreground`). Prints nothing; always exits 0.
- **`scripts/hooks/user_prompt_submit.py`** (HOOK-02) — UserPromptSubmit hook. On gate-on, shells out to `em-proj message inbox --json` (default consume, not `--peek`), and when the mailbox is non-empty prints a `[em-proj inbox] {N} new message(s):` block with one `- from {from_session} ({pattern}/{scope}) [topic:{topic}]: {body}` line per message. A second invocation immediately after prints nothing (consume-on-surface proven). Always exits 0.
- **`.claude/settings.json`** — added a new top-level `"hooks"` key (sibling of the existing `"permissions"` block, which is untouched) wiring both hooks via `$CLAUDE_PROJECT_DIR`-relative, `python3`-prefixed command strings — repo-scoped per the locked decision.
- **`tests/multiprocess/test_em_sessions_hooks.py`** — 7 deterministic per-hook harness tests invoking the hook scripts directly with synthetic hook JSON on stdin: gate-on registers session + starts daemon, gate-off is a true no-op, mailbox surfacing + consume-on-surface, empty-mailbox no-op, gate-off leaves mailbox untouched, and two HOOK-04 degradation tests (em-proj absent from PATH; em-proj present but always exits 1) proven via PATH manipulation against both hook scripts.
- **`tests/structural/test_hook_script_boundaries.py`** — 3 durable boundary invariants: neither hook imports `em_proj`, every literal `sys.exit(N)` in either file is `sys.exit(0)`, both hooks reference the literal `EM_SESSIONS_AUTOSTART` string.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Daemon-existence assertion was flaky without a poll**
- **Found during:** Task 1 verification (`test_session_start_hook_registers_and_starts_daemon_when_gated_on` failed once with `daemon:<id>` absent immediately after the hook returned)
- **Issue:** The plan's literal test body asserts `clean_db.exists(f"daemon:{session_id}") == 1"` immediately after the hook subprocess returns. `session_start.py`'s call to `em-proj session listen` (no `--foreground`) spawns a *detached* daemon child that writes its own Redis HASH record asynchronously — the parent CLI call returns as soon as the child is forked, before the child has necessarily written its record. `tests/multiprocess/test_daemon_lifecycle.py::test_daemon_start_detaches` already established this exact pattern (a 3-second poll loop with 0.1s sleep) for the identical race.
- **Fix:** Replaced the bare assertion with a poll loop (3s deadline, 0.1s interval) mirroring the existing exemplar, so the test asserts eventual consistency rather than an immediate write.
- **Files modified:** `tests/multiprocess/test_em_sessions_hooks.py`
- **Commit:** `aa65ba3`

**2. [Pre-existing file discovered, not a deviation] `scripts/hooks/session_start.py` already existed on disk (untracked)**
- **Found during:** Task 1, before writing the file
- **Detail:** An untracked `scripts/hooks/session_start.py` was already present in the working tree at plan start, byte-for-byte matching Task 1's action spec (shebang, docstring caution about literal `sys.exit(N)` text, `GATE_ENV`, `_read_hook`, `_child_env`, single try/except + unconditional trailing `sys.exit(0)`, register-then-listen with no `--foreground`). This is almost certainly leftover from a prior aborted execution attempt of this exact plan. It was verified against the plan spec line-by-line and used as-is (no rewrite needed) rather than overwritten, since it was correct and Write-tool rules require reading-before-overwriting an existing file with no benefit to redoing identical work.
- **Files:** `scripts/hooks/session_start.py`
- **Commit:** `aa65ba3` (first commit of the plan; the file was untracked until this commit)

## Known Pre-existing Test Failures (Out of Scope)

`scripts/test.sh all` shows **9 pre-existing failures** unrelated to this plan's changes, in `tests/multiprocess/test_workstream_clobber_demo.py`, `tests/multiprocess/test_workstream_consumer_race.py`, and `tests/structural/test_phase_06_shape.py`. These are the documented Phase 6 gsd-sdk orphan test failures (project memory: `project_phase06_gsd_sdk_orphan_failures.md`) — caused by installed `get-shit-done-cc` module drift against the checked-in `.ts`/`.js` workstream shellout expectations, present on `main` itself and untouched by this plan (confirmed via `git log` — none of those three files were modified in this session; last touched by the phase-06 wave-2 merge commit `3d2b1aa`). Not fixed in-phase per standing project convention.

## Self-Check: PASSED

- `scripts/hooks/session_start.py` — FOUND
- `scripts/hooks/user_prompt_submit.py` — FOUND
- `.claude/settings.json` — FOUND, valid JSON, contains both `SessionStart` and `UserPromptSubmit` hook entries
- `tests/multiprocess/test_em_sessions_hooks.py` — FOUND, 7 tests, all pass
- `tests/structural/test_hook_script_boundaries.py` — FOUND, 3 tests, all pass
- Commit `aa65ba3` — FOUND in `git log`
- Commit `cf5d165` — FOUND in `git log`
- Commit `4b7a226` — FOUND in `git log`
- No orphaned daemon processes or Redis keys after the full test run (`redis-cli -n 15 dbsize` → 0)
