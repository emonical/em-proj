---
phase: 04-long-lived-claims
plan: "02"
subsystem: state/__init__.py
tags: [claim, cli, typer, tdd, verbs]
dependency_graph:
  requires:
    - src/em_proj/state/claim.py
    - src/em_proj/identity.py
    - src/em_proj/output.py
    - src/em_proj/redis_client.py
  provides:
    - state_app.command("claim")
    - state_app.command("release")
    - state_app.command("check")
  affects:
    - src/em_proj/state/__init__.py
tech_stack:
  added: []
  patterns:
    - D-18 anonymous-refusal gate before Redis pre-check
    - Three-step verb template (resolve json_mode → Redis pre-check → call op → emit)
    - ClaimHeldByAnother holder=None → exit 2 (not_found), holder set → exit 3 (held_by_another)
key_files:
  created:
    - tests/unit/test_claim_verbs.py
  modified:
    - src/em_proj/state/__init__.py
decisions:
  - Anonymous refusal checked via os.environ.get("CLAUDE_CODE_SESSION_ID","").strip() before die_if_redis_unreachable (T-4-02-01 mitigation + D-18 order)
  - HeldByAnother imported as ClaimHeldByAnother to avoid shadowing lock.py's HeldByAnother
  - ValidationError caught via duck-typing (hasattr e.code/e.message) rather than direct import — keeps verb thin
  - release verb: holder=None maps to emit_not_found (exit 2); holder set maps to emit_held_by_another (exit 3) per ROADMAP SC#3
  - check verb always exits 0 when held (regardless of who holds it) and 2 when not; --mine filtering deferred to Phase 5 per plan spec
metrics:
  duration: "8 minutes"
  completed: "2026-05-24"
  tasks_completed: 1
  files_changed: 2
---

# Phase 04 Plan 02: Claim/Release/Check Verb Wiring Summary

## One-Liner

Thin verb-shell for claim/release/check: CLAIM-03 anonymous-refusal gate + three-step D-18 template wired to claim.py pure ops with correct exit codes (0/1/2/3).

## What Was Built

Three new `@state_app.command()` verbs appended to `src/em_proj/state/__init__.py`, mirroring the lock/unlock verb pattern from Phase 3:

### `claim` verb
- Positional: `area`
- Options: `--ttl` (range `CLAIM_MIN_TTL`–`CLAIM_MAX_TTL`, default `CLAIM_TTL_DEFAULT`), `--reason`, `--json/--no-json`
- CLAIM-03: env check `os.environ.get("CLAUDE_CODE_SESSION_ID","").strip()` fires BEFORE `die_if_redis_unreachable`
- On `ClaimHeldByAnother`: exit 3 with `emit_held_by_another`
- On success: emits `{area, ttl, claimed_at, expires_at}`

### `release` verb
- Positional: `area`; Options: `--json/--no-json`
- `holder=None` → `emit_not_found` (exit 2): "not held, may have expired"
- `holder set` → `emit_held_by_another` (exit 3): "held by another session"
- On success: emits `{area, released: True}`

### `check` verb
- Positional: `area`; Options: `--json/--no-json`
- `ClaimNotHeld` → `emit_not_found` (exit 2)
- On success: emits `{area, holder: <5-field dict>}` (CLAIM-02)

## Test Coverage

13 tests in `tests/unit/test_claim_verbs.py`:

1. claim exits 0, JSON output has area + expires_at
2. anonymous refusal (CLAUDE_CODE_SESSION_ID deleted) → exit 1 + "anonymous claims refused"
3. anonymous refusal (CLAUDE_CODE_SESSION_ID="") → exit 1
4. claim --ttl 120 exits 0
5. claim --reason "editing schema" exits 0
6. release after claim → exit 0, released=True
7. check after claim → exit 0, holder has all 5 fields
8. check unclaimed area → exit 2
9. release unclaimed area → exit 2
10. release by non-holder → exit 3
11. same holder repeat claim → exit 0 (refresh semantics)
12. different session claim → exit 3
13. anonymous refusal fires before Redis (no clean_db needed)

Full suite: **200 tests passed**.

## Commits

| Phase | Hash | Message |
|-------|------|---------|
| RED (test) | `3d46567` | `test(04-02): add failing tests for claim/release/check verbs` |
| GREEN (impl) | `6977bfc` | `feat(04-02): wire claim, release, check verbs in state/__init__.py` |

## Deviations from Plan

None — plan executed exactly as written.

- Anonymous-refusal via env check (not IdentityResolutionError) per plan's explicit NOTE in `<behavior>`
- ClaimHeldByAnother alias used as specified in `<action>` import additions
- `import os` added at module top per plan spec
- Three verbs appended after `unlock` as specified

## Known Stubs

None. All three verbs are fully wired to live Redis via claim.py pure ops.

## Threat Flags

No new security-relevant surface beyond the plan's threat model. The anonymous-refusal gate (T-4-02-01) is implemented: env check fires before `die_if_redis_unreachable`. The `--ttl` max enforcement (T-4-02-03) is handled by typer's `max=CLAIM_MAX_TTL` at the CLI layer.

## TDD Gate Compliance

- RED gate: `test(04-02)` commit `3d46567` — 13 tests fail with "No such command 'claim'"
- GREEN gate: `feat(04-02)` commit `6977bfc` — 200 tests pass
- REFACTOR: not needed (implementation clean on first pass)

## Self-Check: PASSED
