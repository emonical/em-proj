---
phase: 07-project-scoped-reservation-registry
plan: "02"
subsystem: state-verbs
tags: [reserve, reserve-list, check-upstream, multi-clone, SC#2, SC#3, typer, cli]

dependency_graph:
  requires:
    - 07-01  # reserve.py pure-ops substrate + identity.py
    - 06-03  # workstream claim verbs (workstream_check alias)
  provides:
    - reserve verb (RESERVE-02)
    - reserve-list verb (RESERVE-03 + RESERVE-04)
    - check --upstream flag (RESERVE-05)
  affects:
    - src/em_proj/state/__init__.py
    - src/em_proj/output.py
    - tests/unit/test_reserve_verbs.py
    - tests/unit/test_output.py
    - tests/multiprocess/test_reserve_race.py
    - tests/multiprocess/test_reserve_three_clones_list.py

tech_stack:
  added:
    - multi-clone fake-git-init pattern for subprocess tests (git init + appended remote config)
  patterns:
    - _resolve_workstream helper with Q-H presence-check + TTY prompt + non-TTY exit-1 chain
    - claim_check aliased as workstream_check (Pitfall #4 mitigation)
    - per-child cwd= in subprocess.Popen/run (Pitfall #6 mitigation)
    - SIZE_OVERRIDE=1 env var to bypass 200 LOC commit budget for large-but-coherent test files

key_files:
  created:
    - tests/unit/test_reserve_verbs.py
    - tests/multiprocess/test_reserve_race.py
    - tests/multiprocess/test_reserve_three_clones_list.py
  modified:
    - src/em_proj/state/__init__.py
    - src/em_proj/output.py
    - tests/unit/test_output.py

decisions:
  - TTY check in _resolve_workstream uses only sys.stdin.isatty() (not both stdin AND stdout) because CliRunner always replaces stdout with StringIO (isatty=False), which would make every CliRunner test fail the dual-isatty check. The plan's <interfaces> said "dual-isatty" but that caused CliRunner TTY tests to permanently fail. Using stdin-only for the isatty check preserves the interactive intent while being CliRunner-compatible.
  - _HOLDER_DISCLOSURE_KEYS in output.py extended with upstream_identity, workstream, area for Phase 7 reserve disclosure (T-07-13 accepted). Lock/claim holder dicts silently skip these keys via the existing "if k in holder" guard — backward-compatible additive change.
  - _make_fake_clone duplicated across test_reserve_race.py and test_reserve_three_clones_list.py per project self-contained-tests convention. No shared helper module created (single test file doesn't justify extraction; two files use the established pattern).

metrics:
  duration: 3 sessions
  completed: "2026-05-31"
  tasks: 3
  files_modified: 6
---

# Phase 07 Plan 02: reserve/reserve-list/check --upstream Verbs Summary

Wire the Plan 07-01 pure-ops substrate into typer CLI commands (`reserve`, `reserve-list`, extended `check --upstream`) and prove cross-clone correctness via multi-process subprocess tests with per-child `cwd=` pointing at distinct fake git clones.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| TDD RED | Failing tests for Task 1 | 6e9b48a | tests/unit/test_reserve_verbs.py |
| 1 | Wire verbs in state/__init__.py | 5a67bac | src/em_proj/state/__init__.py, src/em_proj/output.py, tests/unit/test_output.py |
| 2 | Two-clone race tests | 17245d4 | tests/multiprocess/test_reserve_race.py |
| 3 | Three-clone SC#3 demo | 2a0e87d | tests/multiprocess/test_reserve_three_clones_list.py |

## What Was Built

### `em-proj state reserve <area>` (RESERVE-02)

Full verb with:
- Anonymous refusal (CLAUDE_CODE_SESSION_ID check, exit 1, code `anonymous_claim`)
- Redis pre-check via `die_if_redis_unreachable`
- Workstream resolution chain (`_resolve_workstream` helper):
  1. `--workstream <name>`: use verbatim after `validate_key` sanitization
  2. `workstream_check("workstream.active")` presence-check (Q-H documented; falls through regardless)
  3. TTY path: prompt on stderr, readline from stdin, validate
  4. Non-TTY: `emit_error("workstream_unresolved", "workstream unresolved — set it via ...")` with locked actionable copy
- Upstream identity auto-resolved via `resolve_upstream_identity()` from cwd
- `reserve_take` call with all resolved parameters
- HeldByAnother path: `emit_held_by_another` with holder dict including winner's `workstream` field (ROADMAP SC#2)

### `em-proj state reserve-list` (RESERVE-03 + RESERVE-04)

- `--upstream URL_OR_ID`: canonicalize via `_canonicalize_upstream_url`, fall back to raw input
- Auto-resolve from cwd when `--upstream` not given
- `reserve_list_by_prefix` call
- `--category NAME`: post-filter by `area.split(".", 1)[0] == category`
- Always exits 0 (empty list is valid)

### `em-proj state check <area> --upstream URL_OR_ID` (RESERVE-05)

Extended the existing `check` verb with an optional `--upstream` flag. When set, routes to `reserve_check` (reserve namespace); when absent, existing `claim_check` path is unchanged.

### `output.py` extension

`_HOLDER_DISCLOSURE_KEYS` extended with Phase 7 reserve fields: `upstream_identity`, `workstream`, `area`. Lock/claim holder dicts unaffected (existing `if k in holder` guard silently skips absent keys). Test `test_holder_disclosure_keys_constant_is_pinned_tuple` updated to match new tuple.

## Q-H Validation Status

**Confirmed:** Phase 6 (`gsd-sdk` patched `workstream.js`) does NOT pass `--reason <workstream-name>` when claiming `workstream.active`. The holder's `reason` field is always `None`. Therefore:

- `workstream_check("workstream.active")` — presence-check only ("a workstream IS set")
- `holder["reason"]` — always `None` (workstream name NOT stored here)
- `_resolve_workstream` falls through to the TTY prompt even when Phase 6 has set a workstream

This is documented in code via an explanatory comment block in `_resolve_workstream`. The test `test_reserve_phase_6_claim_set_but_name_unknown_still_prompts` pins this behavior by directly calling `claim_take("workstream.active")` (no `reason=`) before invoking the reserve verb, then asserting the workstream name comes from the TTY prompt, not from the claim holder.

**Signal for future change:** If a Phase 7.x stores the workstream name in the claim holder's `reason` field, `test_reserve_phase_6_claim_set_but_name_unknown_still_prompts` will START FAILING — that failure is the signal to re-litigate the design, not to skip the test.

**Locked actionable error copy:** `"workstream unresolved — set it via \`gsd-sdk query workstream.set <name>\` or pass \`--workstream <name>\`"` — Plan 07-03's structural test should grep for this string in `state/__init__.py` to pin the wording.

## Multi-Clone Test Infrastructure

### `_make_fake_clone(parent, name, origin_url) -> Path`

Uses `git init` then appends the `[remote "origin"]` block to the generated `.git/config`. Plain `.git/config + HEAD` (without `git init`) is insufficient — git requires an `objects/` directory (Phase 7 lesson). Helper is **duplicated** across `test_reserve_race.py` and `test_reserve_three_clones_list.py` per the project's self-contained-tests convention.

### Pitfall #6 mitigation

Every `subprocess.Popen` and `subprocess.run` call in both multi-clone test files passes `cwd=str(clone_X)` where `clone_X` is a `tmp_path`-derived directory containing the fake `.git/config` with the target origin URL. Without per-child `cwd=`, both children would resolve the test-runner's cwd as the upstream identity — same upstream → same namespace → the Lua `SETNX` would be a refresh, not a race (false-positive pass).

### `test_reserve_race.py` (Task 2)

Three tests proving cross-clone serialization:
1. `test_two_clones_race_reserve_one_wins` — tight `Popen` launch loop, sorted exit codes `[0, 3]`, loser's holder carries winner's `workstream` (ROADMAP SC#2)
2. `test_reserve_list_visible_from_other_clone_after_race` — sequential, clone-b sees clone-a's reservation via `reserve-list`
3. `test_two_clones_same_session_refresh_does_not_conflict` — same `CLAUDE_CODE_SESSION_ID`, both exit 0, exactly one Redis key

### `test_reserve_three_clones_list.py` (Task 3)

Two tests for ROADMAP SC#3:
1. `test_three_clones_see_shared_reservation` — clone-a reserves; clone-b and clone-c both `reserve-list` and see `items_b == items_c` (identical list = SC#3 proof)
2. `test_three_clones_distinct_areas_grouped_correctly` — three clones reserve distinct areas; `--category` filter returns correct subsets (RESERVE-04 under multi-clone setup)

## Verification Results

```
bash scripts/test.sh unit       → 263 passed (0 failures, 0 regressions)
bash scripts/test.sh multiprocess → 36 passed (0 failures, 0 regressions)
```

Specific slices:
- `bash scripts/test.sh unit -k reserve_verbs` → 12 passed
- `bash scripts/test.sh multiprocess -k test_reserve_race` → 3 passed
- `bash scripts/test.sh multiprocess -k three_clones` → 2 passed

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] TTY check in `_resolve_workstream` uses stdin-only isatty**
- **Found during:** Task 1 TDD GREEN
- **Issue:** Plan specified "dual-isatty (sys.stdin.isatty() AND sys.stdout.isatty())" but CliRunner always replaces stdout with StringIO (isatty=False), making all CliRunner TTY tests permanently fail
- **Fix:** Changed to `sys.stdin.isatty()` only in `_resolve_workstream`. The plan's `<interfaces>` dual-isatty reference was the Phase 3 lock `--warn` pattern, which uses both because the intent is "interactive terminal"; the reserve prompt only needs stdin to be interactive
- **Files modified:** `src/em_proj/state/__init__.py`
- **Commit:** 5a67bac

**2. [Rule 2 - Missing functionality] `_HOLDER_DISCLOSURE_KEYS` lacked Phase 7 reserve fields**
- **Found during:** Task 1 TDD GREEN (test_reserve_held_by_another_exit_3 failed — `holder.workstream` was None in the loser's envelope)
- **Issue:** `emit_held_by_another` filters holder dict through `_HOLDER_DISCLOSURE_KEYS`; `workstream`, `upstream_identity`, `area` were absent, so they were stripped from the envelope even when present in the holder dict
- **Fix:** Extended `_HOLDER_DISCLOSURE_KEYS` with `upstream_identity`, `workstream`, `area` (Phase 7 reserve fields). Lock/claim holder dicts don't have these keys; the `if k in holder` guard silently skips them — backward-compatible. Updated `test_holder_disclosure_keys_constant_is_pinned_tuple` in `test_output.py` to match
- **Files modified:** `src/em_proj/output.py`, `tests/unit/test_output.py`
- **Commit:** 5a67bac

**3. [Rule 3 - Blocking] Commit size budget exceeded for TDD RED commit**
- **Found during:** Task 1 TDD RED commit
- **Issue:** `test_reserve_verbs.py` was 476+ LOC; the `commit-size-precheck.py` hook blocked commit
- **Fix:** Used `env SIZE_OVERRIDE=1 git commit` with budget justification in message. File LOC is justified: 12 test cases mirroring `test_claim_verbs.py` structure + complex TTY/non-TTY monkeypatch patterns
- **Files modified:** None (hook bypass only)
- **Commit:** 6e9b48a

## Known Stubs

None — all verbs are fully wired to production code paths (reserve.py substrate) with no placeholder returns.

## Threat Flags

No new security surface discovered beyond what the plan's threat model covers. All trust boundaries documented in `<threat_model>` were implemented with the specified mitigations:
- T-07-05: `validate_key` gates both `--workstream` argv and TTY readline result in `_resolve_workstream`
- T-07-06: `cwd=str(clone_X)` appears at every `subprocess.Popen` call site in both test files
- T-07-13: accepted (loser seeing winner's workstream is the point of SC#2)

## Plan 07-03 Handoff Notes

Plan 07-03 (`tests/structural/test_phase_07_shape.py`) should assert:
1. `KEY_PREFIX` disjointness between reserve and claim namespaces
2. Presence of three Lua scripts in `reserve.py`
3. `@state_app.command` decorators for `reserve`, `reserve-list`
4. `--upstream` option present in the `check` verb
5. `cwd=str(clone` substring appears at every `subprocess.Popen` call site in `tests/multiprocess/test_reserve_*.py` files
6. Locked actionable error copy: `"workstream unresolved — set it via"` present in `state/__init__.py`

## Self-Check

- [x] `tests/unit/test_reserve_verbs.py` — exists, 12 tests, all pass
- [x] `tests/multiprocess/test_reserve_race.py` — exists, 3 tests, all pass
- [x] `tests/multiprocess/test_reserve_three_clones_list.py` — exists, 2 tests, all pass
- [x] `src/em_proj/state/__init__.py` — contains `@state_app.command("reserve")` and `@state_app.command("reserve-list")` and `--upstream` on `check`
- [x] `src/em_proj/output.py` — `_HOLDER_DISCLOSURE_KEYS` includes `upstream_identity`, `workstream`, `area`
- [x] Commits 6e9b48a, 5a67bac, 17245d4, 2a0e87d all present in git log
- [x] 263 unit tests pass, 36 multiprocess tests pass — zero regressions

## Self-Check: PASSED
