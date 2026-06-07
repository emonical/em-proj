---
phase: "09-durable-mailbox-transport"
plan: "03"
subsystem: "message"
tags: ["cli", "typer", "inbox", "mailbox", "structural-tests"]
dependency_graph:
  requires: ["09-02"]
  provides: ["message CLI surface (MBOX-02)", "em-proj message inbox verb"]
  affects: ["cli.py", "message/__init__.py", "tests/structural/test_phase_09_shape.py"]
tech_stack:
  added: []
  patterns: ["D-14 thin-verb-shell", "resolve_session_id + die_if_redis_unreachable + emit_ok"]
key_files:
  created: []
  modified:
    - src/em_proj/message/__init__.py
    - src/em_proj/cli.py
    - tests/structural/test_phase_09_shape.py
decisions:
  - "inbox verb wires resolve_session_id() for own-mailbox semantics (no --session-id flag)"
  - "AST-based xreadgroup/xack check in structural test (correct fix for docstring false positive)"
  - "inline import ast inside test function (avoids top-level import for rarely-needed module)"
metrics:
  duration: "~10 minutes"
  completed: "2026-06-07"
  tasks_completed: 2
  tasks_total: 2
---

# Phase 9 Plan 03: CLI Wiring and Structural Test Fix Summary

Wire the `inbox` verb into `message/__init__.py`, mount `message_app` on `cli.py`, and fix the false-positive structural test — completing Phase 9's CLI surface for MBOX-02.

## What Was Delivered

- `message/__init__.py` extended with `@message_app.command("inbox")` — accepts `--peek`, `--since`, `--json/--no-json`; wires `die_if_redis_unreachable` + `resolve_session_id()` + `mailbox_inbox(_ops)` + `emit_ok`. No business logic in the module (D-14 compliance).
- `cli.py` updated with `from em_proj.message import message_app` and `app.add_typer(message_app, name="message", ...)` — `message_app` now appears ≥ 2 times in the file.
- `test_uses_streams_not_list` fixed with AST-based method-call inspection: collects all `Attribute.attr` names that appear as call targets via `ast.walk`, so the prohibition on `xreadgroup`/`xack` applies only to executable code, not docstrings explaining why consumer groups are deliberately not used.

## Task Commits

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | inbox verb command | c9f07d1 | src/em_proj/message/__init__.py |
| 2 | cli.py mount + structural test fix | 55b8d96 | src/em_proj/cli.py, tests/structural/test_phase_09_shape.py |

## Test Results

- `scripts/test.sh structural`: 100 passed, 8 skipped (planning worktree not attached — expected)
- `scripts/test.sh all`: 451 passed, 9 skipped — no regressions
- All Phase 9 structural assertions (`test_message_app_wired_in_cli`, `test_message_init_has_inbox_command`, `test_uses_streams_not_list`) GREEN

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking Issue] Worktree reset to Phase 9 base commit**
- **Found during:** Task 1 setup
- **Issue:** Worktree HEAD was at `86521d2` (Phase 8 tip); Phase 9 code (`message/_ops.py`, `message/__init__.py` stub) was absent because the worktree branch had not been reset to the `9c63d05` merge commit specified in the `<worktree_branch_check>`.
- **Fix:** Ran `git reset --hard 9c63d05` per the `<worktree_branch_check>` protocol, which brought the Phase 9 message package into the working tree.
- **Files modified:** None (reset only)
- **Commit:** N/A (pre-existing commits absorbed)

### Structural Test Fix (Planned Deviation)

**2. [Planned - Known Issue] Fix test_uses_streams_not_list false positive**
- **Found during:** Task 2
- **Issue:** `_ops.py` docstring mentions `XREADGROUP`/`XACK` to explain why consumer groups are deliberately not used. Naive `read_text().lower()` check matched the docstring, producing a false positive.
- **Fix:** Replaced with AST-based inspection per CLAUDE.md guideline ("Use AST checks for code properties"). Collects `Attribute.attr` values from `ast.Call` nodes only — excludes string literals, docstrings, comments.
- **Files modified:** `tests/structural/test_phase_09_shape.py`
- **Commit:** 55b8d96

## Known Stubs

None — the `inbox` verb is fully wired. `mailbox_inbox` returns real data from Redis (or `[]` for empty/absent mailbox). No placeholder values flow to CLI output.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes. The `inbox` verb follows the same trust model as all other D-14 verbs: session_id from env var, Redis connection gated by `die_if_redis_unreachable`.

## Self-Check

Files created/modified:
- `src/em_proj/message/__init__.py` — exists: YES (modified)
- `src/em_proj/cli.py` — exists: YES (modified)
- `tests/structural/test_phase_09_shape.py` — exists: YES (modified)

Commits:
- c9f07d1 — inbox verb command
- 55b8d96 — cli.py mount + structural test fix

## Self-Check: PASSED
