---
phase: 03-identity-advisory-locks
plan: 06
subsystem: verification-substrate
tags: [structural, ast, stale-takeover, verify-phase, decision-coverage-gate, regression-gate]
dependency_graph:
  requires: [03-01, 03-02, 03-03, 03-04, 03-05]
  provides: [phase-3-verification-substrate, structural-regression-gate, stale-takeover-proof]
  affects: [future-phases-that-touch-lock.py-identity.py-state-init]
tech_stack:
  added: []
  patterns: [ast-structural-testing, sigkill-process-simulation, decision-coverage-gate]
key_files:
  created:
    - tests/structural/test_phase_03_shape.py
    - tests/multiprocess/test_lock_stale.py
  modified: []
decisions:
  - "Used importlib.import_module('em_proj.state.lock') instead of 'from em_proj.state import lock' to avoid shadowing by the state/__init__.py lock verb function"
  - "Reinstalled uv tool editable install (--force --reinstall) to add psutil to the tool venv; psutil was in pyproject.toml but the shim was stale from pre-Phase-3"
  - "Test 13 (D-19 narrowed) split into two tests: test_lock_primitives_do_not_catch_redis_errors (AST) + test_refresher_narrow_handler_present_in_lock_source (source grep) for clarity"
metrics:
  duration: "~25 minutes"
  completed: "2026-05-23"
  tasks: 3
  files: 2
---

# Phase 3 Plan 6: Verification Substrate Summary

Phase 3 verification substrate: structural AST test file (21 tests) pinning every Phase 3 D-* and inherited invariant; end-to-end stale-takeover proof via SIGKILL; verify-phase.sh 03 exits 0.

## What Was Built

### Task 1: `tests/structural/test_phase_03_shape.py` (21 tests)

Self-contained structural test file mirroring the Phase 1+2 precedent. Copies all helper functions (`_parse_or_skip`, `_find_assign`, `_find_funcdef`, `_iter_imports`, `_iter_attribute_chains`) verbatim.

**Tests by group:**

| # | Test | Invariant Pinned |
|---|------|-----------------|
| 1 | `test_identity_py_exists_at_top_level` | D-12: identity.py at top level, not under state/ |
| 2 | `test_identity_py_is_pure_no_typer_no_redis_client` | D-17 carry: identity.py is pure |
| 3 | `test_identity_py_imports_psutil` | D-11: psutil runtime dep + importable |
| 4 | `test_no_direct_redis_redis_construction_outside_chokepoint_phase3` | D-18 tree-wide extended (lock.py + identity.py) |
| 5 | `test_lock_holder_round_trip_has_eight_fields` | D-02: 8-field holder round-trip |
| 6 | `test_lua_compare_and_delete_script_exists` | D-06: LUA_COMPARE_AND_DELETE has cjson.decode + DEL |
| 7 | `test_lua_compare_and_swap_if_stale_script_exists` | D-10: LUA_COMPARE_AND_SWAP_IF_STALE has SET |
| 8 | `test_warn_flag_checks_both_stdout_and_stdin_isatty` | D-07: both isatty() checks present |
| 9 | `test_warn_hold_mutex_check_in_verb` | D-08: warn+hold mutex in verb body |
| 10 | `test_emit_held_by_another_exported_from_output` | D-15 carry: emit_held_by_another in output.py |
| 11 | `test_output_py_remains_dependency_free_after_phase3` | D-15 carry: output.py still dep-free |
| 12 | `test_lock_py_uses_validate_key_from_kv` | D-17/D-14 carry: validate_key imported from kv.py |
| 13 | `test_lock_primitives_do_not_catch_redis_errors` | D-19 carry (narrowed): primitives no-catch |
| 14 | `test_refresher_narrow_handler_present_in_lock_source` | D-19 carry: refresher handler required |
| 15 | `test_lock_py_does_not_import_typer` | D-17 carry: lock.py pure ops module |
| 16 | `test_lock_py_does_not_import_multiprocessing` | Phase 1 pitfall #6 carry |
| 17 | `test_state_init_registers_six_verbs_including_lock_unlock` | D-14 extended: 6 verbs |
| 18 | `test_every_phase_3_decision_cited_in_at_least_one_plan` | Decision Coverage Gate D-01..D-12 |
| 19 | `test_lock_force_displace_is_exported` | Blocker #1 pin a: public export |
| 20 | `test_state_init_imports_no_private_symbols_from_lock` | Blocker #1 pin b: no private imports in verb |
| 21 | `test_refresher_catches_redis_transients` | Blocker #3 pin: exact exception tuple shape |

**Note:** Test 13 (D-19 narrowed) from the plan was split into two tests (13 + 14) for clarity, yielding 21 tests (plan specified >= 20).

### Task 2: `tests/multiprocess/test_lock_stale.py` (2 tests)

End-to-end stale-takeover proof against real Redis:

| Test | Invariant Proven |
|------|-----------------|
| `test_stale_takeover_after_sigkill` | D-10: SIGKILL'd holder leaves stale lock; next acquire takes over via LUA_COMPARE_AND_SWAP_IF_STALE. **ROADMAP SC#2 closed.** |
| `test_live_holder_not_displaced_as_stale` | D-10 inverse: live holder is NOT displaced (stale-detection only fires on dead PIDs) |

### Task 3: verify-phase.sh 03

`bash scripts/verify-phase.sh 03` exits 0 after:
- All 237 tests pass (37 multiprocess, 53 structural, 147 unit)
- `em-proj --version` works (uv tool reinstalled with psutil; see Deviations)
- Anti-pattern grep clean (no TBD/FIXME/XXX/HACK/TODO/PLACEHOLDER)
- SUMMARY.md present for all 6 Phase 3 plans

## verify-phase.sh 03 Output

```
# verify-phase: 03-identity-advisory-locks

## Test suite
| scripts/test.sh all       | PASS | 237 passed in 25.74s |
| scripts/test.sh structural | PASS | 53 passed in 0.06s   |

## Redis backend
| verify-redis-config | PASS | verify-redis-config: OK (appendonly=yes, appendfsync=everysec, save=900 1, AOF present) |

## em-proj CLI
| em-proj on PATH     | PASS | /Users/emonical/.local/bin/em-proj |
| em-proj --version   | PASS | em-proj 0.1.0 |

## Anti-pattern markers
| TBD/FIXME/XXX/HACK/TODO/PLACEHOLDER | PASS | none found in src/ tests/ scripts/ |

## Plan / SUMMARY coverage
| 03-01-PLAN.md | PASS | 03-01-SUMMARY.md present (201 lines) |
| 03-02-PLAN.md | PASS | 03-02-SUMMARY.md present (186 lines) |
| 03-03-PLAN.md | PASS | 03-03-SUMMARY.md present (208 lines) |
| 03-04-PLAN.md | PASS | 03-04-SUMMARY.md present (187 lines) |
| 03-05-PLAN.md | PASS | 03-05-SUMMARY.md present (250 lines) |
| 03-06-PLAN.md | PASS | 03-06-SUMMARY.md present |
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] importlib.import_module to avoid verb shadowing**
- **Found during:** Task 1, test_lock_holder_round_trip_has_eight_fields
- **Issue:** `from em_proj.state import lock` imports the `lock` verb *function* from `state/__init__.py`, not the `lock.py` module. `lock_module._make_holder` raised `AttributeError: 'function' object has no attribute '_make_holder'`. Same issue with `lock_force_displace` test.
- **Fix:** Used `importlib.import_module("em_proj.state.lock")` to bypass the shadowing.
- **Files modified:** `tests/structural/test_phase_03_shape.py`
- **Commit:** 266579c (in the same task commit)

**2. [Rule 3 - Blocking Issue] uv tool shim missing psutil**
- **Found during:** Task 3, verify-phase.sh em-proj --version check
- **Issue:** The `uv tool install` shim at `~/.local/bin/em-proj` was from before Phase 3 added `psutil` to pyproject.toml dependencies. `em-proj --version` failed with `ModuleNotFoundError: No module named 'psutil'`.
- **Fix:** `uv tool install --editable . --force --reinstall` (per Phase 2 02-04/02-05 SUMMARY pattern; documented in Task 3 action block).
- **Files modified:** None (tool environment only)
- **Note:** `pytest` tests run via `.venv` (where psutil was already present from `uv sync`), so the test suite passed even before the reinstall. Only the standalone `em-proj` binary was affected.

**3. [Rule 2 - Auto-add] D-19 narrowed split into two tests**
- **Found during:** Task 1 implementation
- **Issue:** Plan specified test 13 (D-19 narrowed) as one test with both AST primitive-check and source grep. Two clearly distinct assertions (one AST, one regex) are easier to read and debug as separate tests.
- **Fix:** Split into `test_lock_primitives_do_not_catch_redis_errors` (AST) + `test_refresher_narrow_handler_present_in_lock_source` (source grep). This yields 21 tests vs plan's >= 20 threshold.
- **Files modified:** `tests/structural/test_phase_03_shape.py`

## Blocker Resolution Confirmations

**Blocker #1 (lock_force_displace exported + no private imports in verb code):**
- `test_lock_force_displace_is_exported` — PASSES: `_find_funcdef(lock_tree, "lock_force_displace")` is not None; `importlib.import_module("em_proj.state.lock").lock_force_displace` is callable.
- `test_state_init_imports_no_private_symbols_from_lock` — PASSES: no `_encode_holder`, `_decode_holder`, `_make_holder`, `_validate_reason` in state/__init__.py source.

**Blocker #2 (no "not_implemented" string or NotImplementedError on --hold path):**
- Anti-pattern grep: no TBD/FIXME/XXX/HACK/TODO/PLACEHOLDER in source.
- `test_state_init_registers_six_verbs_including_lock_unlock` — PASSES: lock + unlock verbs are real implementations, not stubs.
- `bash scripts/test.sh all` — 237 tests pass including lock_hold_run integration tests.

**Blocker #3 (refresher exception handler shape pinned):**
- `test_refresher_catches_redis_transients` — PASSES: `except\s*\(\s*redis\.ConnectionError\s*,\s*redis\.TimeoutError\s*\)` regex matches in lock.py.

## Known Stubs

None. All Phase 3 functionality is implemented:
- identity.py: resolve_session_id, resolve_project_hash, current_process_composite, stale-detection probes
- lock.py: lock_acquire, lock_release, lock_force_displace, lock_hold_run, RefresherThread, all three Lua scripts
- state/__init__.py: lock and unlock verbs fully wired (--hold, --warn, --ttl, --reason)
- output.py: emit_held_by_another, _HOLDER_DISCLOSURE_KEYS

## Phase 3 Readiness Statement

**Phase 3 is verifiable via `bash scripts/verify-phase.sh 03`; ready for `gsd-verify-phase 3`.**

The verifier sub-agent's job: "run `bash scripts/verify-phase.sh 03`; read the output; apply judgment about whether the phase GOAL is delivered (not just that checks pass); write VERIFICATION.md with next-phase recommendations."

## Self-Check: PASSED

- `tests/structural/test_phase_03_shape.py` exists: FOUND
- `tests/multiprocess/test_lock_stale.py` exists: FOUND
- Task 1 commit 266579c exists: FOUND
- Task 2 commit 67ef4e0 exists: FOUND
- `bash scripts/verify-phase.sh 03` exits 0: CONFIRMED (after uv tool reinstall)
- 237 tests pass: CONFIRMED
