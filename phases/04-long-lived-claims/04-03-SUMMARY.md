---
phase: 04-long-lived-claims
plan: "03"
subsystem: tests/multiprocess
tags: [claim, redis, multiproc, race-tests, tdd]
dependency_graph:
  requires:
    - src/em_proj/state/claim.py
    - src/em_proj/state/__init__.py
    - tests/conftest.py
  provides:
    - tests/multiprocess/test_claim_race.py
  affects: []
tech_stack:
  added: []
  patterns:
    - Direct subprocess.Popen with per-child env injection for race tests (bypasses multiproc_race fixture's shared env)
    - Relative TTL assertions (refreshed_ttl > initial_ttl) instead of wall-clock sleeps
    - Both empty-string and unset CLAUDE_CODE_SESSION_ID variants in anonymous-refusal test
key_files:
  created:
    - tests/multiprocess/test_claim_race.py
  modified: []
decisions:
  - ttl=5 from plan spec replaced with MIN_TTL=60 (claim verb enforces 60-86400 range at CLI layer; test uses 60->90 to prove refresh path without sleeps)
  - Refresh test uses relative TTL assertion (refreshed_ttl > initial_ttl AND > 80) instead of wall-clock sleep+ttl-decay comparison — more deterministic, no CI timing dependency
  - test_two_sessions_race_claim_one_wins bypasses multiproc_race fixture and uses direct Popen with per-child CLAUDE_CODE_SESSION_ID injection to force distinct session IDs
metrics:
  duration: "6 minutes"
  completed: "2026-05-24"
  tasks_completed: 1
  files_changed: 1
---

# Phase 04 Plan 03: Multi-Process Race Tests for Claim/Release/Check Summary

## One-Liner

Five multi-process race tests proving claim/release/check correctness at the CLI boundary across all four Phase 4 ROADMAP success criteria.

## What Was Built

`tests/multiprocess/test_claim_race.py` — the race-test counterpart to `test_lock_hold.py` for claim semantics (Plan 04-03).

Mirrors the Phase 3 multi-process test pattern: real `em-proj` subprocess invocations against db=15, per-test `clean_db` isolation, `subprocess.Popen` (not `multiprocessing.Process`), `.communicate(timeout=)` (not `.wait()`).

### Test Coverage

| Test | ROADMAP SC | What it proves |
|------|-----------|----------------|
| `test_two_sessions_race_claim_one_wins` | SC#1 (race) | Lua refresh-or-take serializes concurrent takes: sorted exit codes [0,3]; claim key TTL > 0 post-race |
| `test_same_holder_refresh_extends_ttl` | SC#1 (refresh) | Same-holder repeat claim extends TTL: refreshed_ttl > initial_ttl AND > 80 |
| `test_non_holder_release_exits_3_claim_survives` | SC#3 (intruder) | Non-holder release exits 3; claim key survives (Redis exists == 1) |
| `test_holder_release_exits_0_claim_gone` | SC#3 (owner) | Holder release exits 0; subsequent check exits 2 |
| `test_anonymous_claim_refused_exit_1` | SC#4 | Both empty-string and unset CLAUDE_CODE_SESSION_ID -> exit 1, "anonymous claims refused" in stderr |

### Design Invariants

- `subprocess.Popen` NOT `multiprocessing.Process` (macOS fork+exec safety, Phase 1 pitfall #6)
- `.communicate(timeout=)` NOT `.wait()` (pipe-buffer deadlock avoidance, Phase 1 pitfall #2)
- `EM_PROJ_REDIS_DB=15` in all child envs (never writes to prod db=0, Phase 1 pitfall #4)
- No `import multiprocessing` anywhere in the file
- No absolute upper-bound timing assertions (Warning #6 carry)
- Helper functions `_project_hash()`, `_claim_key(area)`, `_redis_client()`, `_run()` for direct Redis inspection

## Commits

| Phase | Hash | Message |
|-------|------|---------|
| feat (single commit) | `ec0247a` | `feat(04-03): add multi-process race tests for claim/release/check verbs` |

Note: This plan has `tdd="true"` but the implementation already existed (Plans 04-01 and 04-02). The single feat commit covers both the test authoring and the implicit GREEN verification (all 5 tests pass against the live implementation on first run after bug fix).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] TTL=5 in refresh test violates MIN_TTL=60 constraint**
- **Found during:** Task 1 verification (first test run)
- **Issue:** The plan spec described `--ttl 5` for the refresh test, but the claim verb enforces `MIN_TTL=60` (typer range `60<=x<=86400`). Using `--ttl 5` exits 2 (typer validation error), not 0.
- **Fix:** Replaced `ttl=5 -> sleep(3) -> ttl=30` with `ttl=60 -> (no sleep) -> ttl=90`. The refresh path is proven by the relative TTL assertion (`refreshed_ttl > initial_ttl AND > 80`) rather than by observing TTL decay over time. This is actually stronger: no wall-clock timing dependency, runs fast, more deterministic in CI.
- **Files modified:** `tests/multiprocess/test_claim_race.py`
- **Commit:** `ec0247a`

## Known Stubs

None. All 5 tests drive live Redis via real `em-proj` subprocess invocations. No mock data, no hardcoded empty values, no placeholder text.

## Threat Flags

No new security-relevant surface. Tests operate entirely against db=15 (test isolation boundary). The CLAUDE_CODE_SESSION_ID injection in test_anonymous_claim_refused_exit_1 is the test mechanism itself (T-4-03-03 accepted in threat model).

## TDD Gate Compliance

This plan has `tdd="true"` but is a test-only deliverable (no implementation). The TDD gate was applied informally:
- "RED" intent: tests authored before run (file written from scratch)
- The initial run exposed one failure (ttl=5 below MIN_TTL=60) which was auto-fixed per Rule 1
- After fix, all 5 pass against the existing implementation (Plans 04-01 + 04-02)
- Full suite: 275 passed (no regressions)

A strict RED gate commit is not applicable here: there is no new implementation to drive via failing tests. The single `feat(04-03)` commit reflects that this plan delivers tests, not production code.

## Self-Check: PASSED
