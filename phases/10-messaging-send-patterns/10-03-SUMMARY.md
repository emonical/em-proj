# 10-03 SUMMARY — Wave 2: CLI verbs + TEST-04 harness GREEN

**Plan:** 10-03-PLAN.md (Phase 10, Wave 2)
**Status:** Complete
**Requirements:** MSG-01, MSG-02, MSG-03, MSG-04, MSG-05, TEST-04

## What was built

The integration wave — wired the Wave 1 ops layer to CLI verbs and proved
end-to-end delivery via subprocess tests. After this wave all Phase 10
requirements are complete.

**Tasks 1–2 — `src/em_proj/message/__init__.py` (D-14 thin verb shells):**
- `send_cmd` — `--to`/`--topic` mutually exclusive (exactly one required, guarded
  before the Redis check); routes to `send_directed` or `send_topic`; maps
  `SessionNotFound` → `emit_not_found` (exit 2).
- `broadcast_cmd` — routes to `send_broadcast`.
- `subscribe_cmd` / `unsubscribe_cmd` — manage topic membership for the current
  session (`--scope` default `machine`).
- `_emit_or_partial(result, json_mode)` — mirrors `emit_ok`'s envelope but maps
  `recipients_failed > 0` → exit 4 (D-ExitCode4); full counts always in `data`.
- Imports/`__all__`/help text extended; new `SCHEMA_VERSION`, `emit_not_found`,
  `SessionNotFound`, `send_*`/`subscribe_*` imports.

**Task 3 — `tests/multiprocess/test_message_delivery.py`:**
- Replaced the 9 mailbox-path skip-stubs with real subprocess bodies covering the
  full directed/broadcast/topic × machine/project/upstream matrix; cross-scope
  exclusion via `_override_field` on `project_hash`/`upstream_identity`; sender
  self-exclusion for broadcast/topic; MBOX-04 field assertions. Live-path cells
  remain skip-stubbed for Phase 11.

## Files changed

| File | Change | LOC |
|------|--------|-----|
| `src/em_proj/message/__init__.py` | extend (4 verbs + helper) | +197 / −2 |
| `tests/multiprocess/test_message_delivery.py` | activate 9 cells + helpers | +181 / −13 |

Commits (phase branch): `a296ccb` (verb layer), `81730d1` (TEST-04 harness).

## Verification results

| Verify command | Expected | Observed |
|----------------|----------|----------|
| `test.sh multiprocess -k delivery` | 9 pass, 3 skip | ✓ **9 passed, 3 skipped** |
| `test.sh multiprocess -k durability` | MBOX-01 pass | ✓ **1 passed** |
| `test.sh structural -k phase_10` | A–E,G pass; F pass once SUMMARYs land | ✓ 6 passed pre-SUMMARY; F passes after this file |
| `test.sh unit` | no regression | ✓ **336 passed** |
| `test.sh all` | full suite green | (run at phase verification) |

## Requirement coverage

- **MSG-01** directed send → `test_directed_*` + durability MBOX-01.
- **MSG-02** broadcast scope fan-out → `test_broadcast_*` (machine/project/upstream).
- **MSG-03** subscribe/unsubscribe + topic send → `test_topic_*`.
- **MSG-04** per-message scope → broadcast/topic each cover 3 scopes; directed
  records scope as informational.
- **MSG-05** parseable metadata + exit codes → JSON envelope asserted per test;
  exit-4 path implemented in `_emit_or_partial` (ops dict covered by
  `test_partial_delivery_counts_failures`); exit-2 via `emit_not_found`.
- **TEST-04** 3×3 harness → 9 cells GREEN.

## Deviations

- **Verb-layer commits bundled into two cohesive commits** rather than the plan's
  per-task split (Task 1 send/broadcast, Task 2 subscribe/unsubscribe): all four
  verbs live in one file and form one reviewable unit (`__init__.py` verb wiring).
  Per-plan traceability (10-03) is preserved. `[budget: …]` annotations recorded.
- **`_unique_session_id()` hardened with uuid4 suffix** in the delivery harness
  too (same coarse-clock collision fix as 10-02; carry-forward recommendation).

## Self-Check: PASSED

All four `@message_app.command` verbs present; 9/9 TEST-04 cells GREEN; MBOX-01
durability GREEN; unit 336/336; structural A–G GREEN once this SUMMARY lands. exit-4
partial-delivery and exit-2 not-found paths implemented per the locked decisions.
