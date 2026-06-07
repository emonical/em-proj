---
phase: 04-long-lived-claims
plan: "04"
subsystem: tests/structural
tags: [structural-tests, phase-gate, verify-phase, claim, shape-assertions]
dependency_graph:
  requires:
    - src/em_proj/state/claim.py
    - src/em_proj/state/__init__.py
    - scripts/verify-phase.sh
    - .planning/phases/04-long-lived-claims/04-01-SUMMARY.md
    - .planning/phases/04-long-lived-claims/04-02-SUMMARY.md
    - .planning/phases/04-long-lived-claims/04-03-SUMMARY.md
  provides:
    - tests/structural/test_phase_04_shape.py
  affects: []
tech_stack:
  added: []
  patterns:
    - Self-contained AST-based structural test file (no imports from other structural files)
    - ast.unparse(dec) decorator inspection for @state_app.command() counting
    - Source-text grep for non-AST checks (redis.Redis constructor, anonymous-refusal literal)
key_files:
  created:
    - tests/structural/test_phase_04_shape.py
  modified: []
decisions:
  - 11 test functions covering all CLAIM-01/02/03 acceptance criteria and D-14/D-17/D-18 carries
  - test_phase_04_summaries_present uses PHASE_DIR.glob() to future-proof against plan additions
  - Decorator inspection via ast.unparse(dec) + "state_app.command" substring — simpler than full AST attribute traversal, robust against call shape variations
metrics:
  duration: "12 minutes"
  completed: "2026-05-23"
  tasks_completed: 2
  files_changed: 1
---

# Phase 04 Plan 04: Structural Shape Assertions + Phase Gate Summary

## One-Liner

11-test AST structural file encoding all Phase 4 acceptance criteria, with verify-phase.sh 04 as the deterministic end-to-end gate.

## What Was Built

`tests/structural/test_phase_04_shape.py` — the Phase 4 structural invariants file, mirroring the pattern from Phase 2 (`test_phase_02_shape.py`) and Phase 3 (`test_phase_03_shape.py`).

### Test Coverage

| Test | Decision/Criterion | What it pins |
|------|--------------------|-------------|
| `test_claim_py_no_typer_import` | D-17 carry | claim.py is CLI-framework-free |
| `test_claim_py_no_multiprocessing_import` | Phase 1 pitfall #6 carry | no concurrency imports in pure-ops module |
| `test_claim_py_no_redis_constructor` | D-18 carry | get_client() only, no redis.Redis() |
| `test_claim_py_ttl_default_is_1800` | CLAIM-01 | TTL_DEFAULT constant value pinned |
| `test_claim_py_key_prefix` | CLAIM-01 | KEY_PREFIX = "state:claim:" pinned |
| `test_claim_py_lua_scripts_present` | CLAIM-01 | all 3 Lua scripts are string constants |
| `test_claim_py_public_ops_exported` | CLAIM-01 | claim_take, claim_release, claim_check defined |
| `test_claim_py_exceptions_exported` | CLAIM-02 | HeldByAnother, ClaimNotHeld class definitions |
| `test_state_init_registers_claim_verbs` | D-14 extended | >= 9 @state_app.command() verbs |
| `test_state_init_anonymous_gate_present` | CLAIM-03 | "anonymous claims refused" literal present |
| `test_phase_04_summaries_present` | Phase gate | all 04-NN-PLAN.md have SUMMARY.md counterparts |

### verify-phase.sh 04 Results

All deterministic checks pass:

| Check | Status |
|-------|--------|
| scripts/test.sh all | PASS (286 passed, 0 failed) |
| scripts/test.sh structural | PASS (64 passed, 0 failed) |
| verify-redis-config | PASS |
| em-proj on PATH | PASS |
| em-proj --version | PASS (0.1.0) |
| TBD/FIXME/XXX/HACK/TODO/PLACEHOLDER | PASS (none in src/ tests/ scripts/) |
| 04-01-PLAN.md → SUMMARY | PASS |
| 04-02-PLAN.md → SUMMARY | PASS |
| 04-03-PLAN.md → SUMMARY | PASS |
| 04-04-PLAN.md → SUMMARY | PASS (this file) |

## Commits

| Task | Hash | Message |
|------|------|---------|
| Task 1 (structural tests) | `caeee2c` | `test(04-04): write test_phase_04_shape.py structural invariants` |
| Task 2 (SUMMARY + phase gate) | `cf50115` (planning branch) | `test(04-04): structural shape assertions + verify-phase.sh 04 clean` |

## Deviations from Plan

None — plan executed exactly as written.

- All 11 test functions written as specified in the plan's `<action>` block
- Self-contained helpers (no imports from other structural files) per Phase 1+2+3 precedent
- verify-phase.sh 04 passes cleanly after SUMMARY creation

## Known Stubs

None. All assertions check live source files via ast.parse() and Path.read_text().

## Threat Flags

No new security-relevant surface. Structural tests use ast.parse() (not exec/eval) for all code-property assertions; source text grep only for non-AST checks (redis.Redis constructor, anonymous-refusal literal).

T-4-04-01: Structural tests use AST nodes, not comments — comments cannot fool ast-based checks (mitigated as designed).

## Self-Check: PASSED
