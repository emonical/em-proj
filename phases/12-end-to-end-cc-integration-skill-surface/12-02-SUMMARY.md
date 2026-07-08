---
phase: 12-end-to-end-cc-integration-skill-surface
plan: "02"
subsystem: cc-integration-hooks
tags: [hooks, hook-e2e, mailbox, em-sessions-skill, skill-surface]
dependency-graph:
  requires:
    - Plan 12-01 (scripts/hooks/session_start.py, scripts/hooks/user_prompt_submit.py, tests/multiprocess/test_em_sessions_hooks.py helpers)
  provides:
    - tests/multiprocess/test_hook_e2e_delivery.py (HOOK-03 A-to-B proof)
    - docs/em-sessions-skill.md (staged /em-sessions skill content)
    - tests/structural/test_em_sessions_skill_boundaries.py (write-boundary invariant)
  affects:
    - "~/.claude/skills/em-sessions/SKILL.md (orchestrator-applied, out-of-repo, NOT touched by this plan)"
tech-stack:
  added: []
  patterns:
    - "Real two-session pipeline test: subprocess.run em-proj message send/broadcast/subscribe as session A, then _run_hook the actual user_prompt_submit.py as session B — distinct from Plan 12-01's synthetic single-session mailbox seed"
    - "Skill doc as in-repo staging artifact + fenced-code-block regex scan as a durable write-boundary invariant (never trust prose alone to hold a NEVER boundary)"
key-files:
  created:
    - tests/multiprocess/test_hook_e2e_delivery.py
    - docs/em-sessions-skill.md
    - tests/structural/test_em_sessions_skill_boundaries.py
  modified: []
decisions:
  - "Task 2's commit (docs/em-sessions-skill.md + tests/structural/test_em_sessions_skill_boundaries.py) landed as one 221-LOC commit, 21 LOC over the ~200 budget — tagged [budget: single plan task pairs the skill doc with its own guarding structural test; splitting would land an unguarded doc or an untested test file as an intermediate commit]. Matches the plan's single Task 2 <files> pairing; no separate PR needed per project convention for small overages."
metrics:
  duration: "~25 min"
  completed: "2026-07-08"
status: complete
---

# Phase 12 Plan 02: HOOK-03 A-to-B proof + /em-sessions skill staging Summary

Closed Phase 12 (and the v1.1 milestone) by proving the real send-CLI-to-mailbox-to-hook-stdout pipeline across all three send patterns (directed/broadcast/topic), and by staging the full `/em-sessions` read+send skill content in-repo for the orchestrator to copy to `~/.claude/skills/em-sessions/SKILL.md`.

## What Was Built

- **`tests/multiprocess/test_hook_e2e_delivery.py`** (HOOK-03) — 3 tests proving the genuine A-to-B pipeline: session A sends via the real `em-proj message` CLI (`send --to`, `broadcast --scope machine`, `subscribe` + `send --topic`), and session B's actual `user_prompt_submit.py` hook script (Plan 12-01's, unmodified) surfaces the message on stdout. Distinct from Plan 12-01's synthetic single-session seed tests — this is the full mechanism, not just the hook script's own contract. Imports `_run_hook`, `_unique_session_id`, `USER_PROMPT_SUBMIT_HOOK` from Plan 12-01's `tests/multiprocess/test_em_sessions_hooks.py` rather than redefining them; the session-registration and CLI-send helpers mirror `tests/multiprocess/test_message_delivery.py`'s TEST-04 exemplar.
- **`docs/em-sessions-skill.md`** (SKILL-04, SKILL-05) — the full `/em-sessions` skill content, staged in-repo. Mirrors `~/.claude/skills/em-global-state/SKILL.md`'s exact structural shape: frontmatter (`name: em-sessions`, `allowed-tools: [Bash]`), `<objective>`, `<when_to_invoke>`, a `<action>` verb reference with one `###` subsection each for `list`, `show <session_id>`, `inbox [--session <id>]` (always `--peek`), `send <session_id> <body>`, `broadcast <body> [--scope ...]` — each with a fenced bash command and fenced json output schema — and a `<scope>` section splitting READ / WRITE / NEVER (state set/del, lock/claim acquire, session register/listen/stop). This is a STAGING ARTIFACT ONLY — this plan does not and cannot write to `~/.claude/skills/`.
- **`tests/structural/test_em_sessions_skill_boundaries.py`** — 3 durable tests: the doc exists with valid frontmatter; none of the doc's FENCED command examples ever invoke `state set`, `state del`, `session register`, `session listen`, or `session stop` (regex-extracts all ```bash/```json fenced blocks and asserts the forbidden strings are absent from the concatenated fenced text); the doc's prose does name `state set` and `NEVER` (confirming the boundary is documented, not just held mechanically).

## Deviations from Plan

None — plan executed exactly as written. One commit-size note:

**Commit-size budget exception (not a Rule 1-4 deviation, a size-policy note)**
- Task 2's single commit (docs/em-sessions-skill.md + tests/structural/test_em_sessions_skill_boundaries.py) totaled 221 LOC, 21 over the project's ~200 LOC/commit soft budget.
- The plan's Task 2 `<files>` block pairs these two files as one unit of work (a doc plus the structural test that guards it); splitting into two commits would land either an unguarded doc or a test file with no target to assert against as an intermediate, non-reviewable state.
- Tagged `[budget: single plan task pairs the skill doc with its own guarding structural test; splitting would land an unguarded doc or an untested test file as an intermediate commit]` in the commit body per the project's budget-exception convention (no `SIZE_OVERRIDE=1` needed — the precheck accepted the reasoned exception).

## CLI Sanity Probes (non-gating, per plan)

- `.venv/bin/em-proj session list --json` → `{"schema_version":"1","status":"ok","data":[]}` (exit 0)
- `.venv/bin/em-proj message inbox --peek --json` → `{"schema_version":"1","status":"ok","data":[]}` (exit 0)

Both confirm the first two verbs `docs/em-sessions-skill.md` references behave exactly as documented.

## Known Pre-existing Test Failures (Out of Scope)

`scripts/test.sh all` shows the same **9 pre-existing failures** documented in 12-01-SUMMARY.md, unrelated to this plan's changes: `tests/multiprocess/test_workstream_clobber_demo.py::test_new_path_through_gsd_sdk_refuses_loser`, all 3 tests in `tests/multiprocess/test_workstream_consumer_race.py`, and all 5 tests in `tests/structural/test_phase_06_shape.py`. These are the documented Phase 6 gsd-sdk orphan test failures (project memory: `project_phase06_gsd_sdk_orphan_failures.md`) — caused by installed `get-shit-done-cc` module drift against the checked-in `.ts`/`.js` workstream shellout expectations, present on `main` itself. Not touched by this plan; not fixed in-phase per standing project convention.

Full-suite result after both tasks: **9 failed (known orphans), 513 passed, 9 skipped** (65s).

## ORCHESTRATOR FOLLOW-UP REQUIRED

**`docs/em-sessions-skill.md` is NOT yet applied.** The executor's permission scope denies writes under `~/.claude/skills/`, so this plan deliberately staged the full skill content in-repo instead of writing the deployed location directly. The orchestrator (or a manual post-execution step) must copy `docs/em-sessions-skill.md`'s content verbatim to `~/.claude/skills/em-sessions/SKILL.md` (creating the `em-sessions` directory if absent) to complete SKILL-04/SKILL-05.

## Self-Check: PASSED

- `tests/multiprocess/test_hook_e2e_delivery.py` — FOUND, 3 tests, all pass (confirmed live, not skipped: `scripts/test.sh multiprocess -k hook_e2e --tail 60` → `3 passed, 68 deselected`)
- `docs/em-sessions-skill.md` — FOUND, 157 insertions, valid YAML frontmatter (`name: em-sessions`)
- `tests/structural/test_em_sessions_skill_boundaries.py` — FOUND, 3 tests, all pass (`scripts/test.sh structural -k em_sessions_skill --tail 30` → `3 passed, 121 deselected`)
- Commit `662e285` — FOUND in `git log` (Task 1: HOOK-03 A-to-B proof)
- Commit `91d905e` — FOUND in `git log` (Task 2: skill doc + structural test)
- `~/.claude/skills/` — NOT touched by this plan (confirmed: no writes attempted outside the em-proj repo)
- No orphaned Redis keys after the full test run (`clean_db` fixture FLUSHDBs on db=15 before and after every test)
