---
phase: 04-long-lived-claims
plan: "01"
subsystem: state/claim
tags: [claim, redis, lua, pure-ops, tdd]
dependency_graph:
  requires:
    - src/em_proj/identity.py
    - src/em_proj/redis_client.py
    - src/em_proj/state/kv.py
  provides:
    - src/em_proj/state/claim.py
  affects: []
tech_stack:
  added: []
  patterns:
    - Redis HASH for per-field updates (vs single JSON blob in lock.py)
    - Lua refresh-or-take idempotency for same-holder repeat calls
    - Dual-field (session_id + project_hash) Lua guard on modify/delete
key_files:
  created:
    - src/em_proj/state/claim.py
    - tests/unit/test_claim.py
  modified: []
decisions:
  - Redis HASH (HSET/HGETALL) instead of single JSON string — refresh only needs HSET expires_at + EXPIRE, no full rewrite
  - LUA_CLAIM_REFRESH_OR_TAKE checks both session_id AND project_hash before refresh (T-4-01-02)
  - claim_check uses LUA_CLAIM_CHECK (EXISTS + HGETALL atomic) to eliminate TOCTOU
  - reason stored as empty string in HASH, normalized back to None in _hgetall_to_holder
metrics:
  duration: "4 minutes"
  completed: "2026-05-24"
  tasks_completed: 1
  files_changed: 2
---

# Phase 04 Plan 01: claim.py Pure-Ops Module Summary

## One-Liner

Redis HASH-backed area claims with Lua refresh-or-take idempotency and dual-field session_id+project_hash ownership guards.

## What Was Built

`src/em_proj/state/claim.py` — the pure-ops module for long-lived area claims (Plan 04-01).

Mirrors `lock.py` structurally but with claim semantics:
- No blocking poll, no stale-detection probe, no RefresherThread, no `--hold` runner
- **Refresh-or-take**: same-holder repeat call extends TTL rather than raising `HeldByAnother`
- **Session-scoped ownership**: `session_id` (not `pid+proc_start_epoch`) identifies the holder
- **Project-scoped key**: `state:claim:<project_hash>:<area>` (vs `state:lock:<name>`)
- **Redis HASH storage**: 5 fields (`session_id`, `project_hash`, `reason`, `claimed_at`, `expires_at`) stored via `HSET`/`HGETALL`

### Constants

| Constant | Value |
|----------|-------|
| `KEY_PREFIX` | `"state:claim:"` |
| `TTL_DEFAULT` | `1800` (30 min) |
| `MIN_TTL` | `60` |
| `MAX_TTL` | `86400` |
| `MAX_REASON_CHARS` | `256` |

### Lua Scripts

| Script | Purpose |
|--------|---------|
| `LUA_CLAIM_REFRESH_OR_TAKE` | Atomic take-or-refresh: absent → take; same-holder → refresh expires_at+EXPIRE; conflict → return "conflict" |
| `LUA_CLAIM_COMPARE_AND_DELETE` | Dual-field guard: session_id+project_hash must match before DEL |
| `LUA_CLAIM_CHECK` | Atomic EXISTS+HGETALL (no TOCTOU for check path) |

### Public API

- `claim_take(area, ttl=TTL_DEFAULT, reason=None) -> dict`
- `claim_release(area) -> None`
- `claim_check(area) -> dict`
- `HeldByAnother(code="held_by_another")`
- `ClaimNotHeld(code="not_held")`

## Test Coverage

21 tests in `tests/unit/test_claim.py` covering all 8 behavior cases from the plan:

1. Fresh area take returns 5-field holder dict
2. Same-holder repeat call refreshes TTL (not raises)
3. Different session_id raises `HeldByAnother`
4. Release by holder deletes key (returns None)
5. Release by non-holder raises `HeldByAnother` with current holder
6. Release on absent key raises `HeldByAnother(holder=None)`
7. `claim_check` when held returns holder dict
8. `claim_check` when absent raises `ClaimNotHeld`

Plus validation, key-shape, and no-forbidden-imports tests.

## Commits

| Phase | Hash | Message |
|-------|------|---------|
| RED (test) | `598ca8d` | `test(04-01): add failing tests for claim.py pure-ops module` |
| GREEN (impl) | `6d9008a` | `feat(04-01): implement claim.py pure-ops module` |

## Deviations from Plan

None — plan executed exactly as written.

The plan spec was followed precisely:
- Redis HASH storage as specified
- Three Lua scripts as specified
- All 5 public exports (claim_take, claim_release, claim_check, HeldByAnother, ClaimNotHeld)
- No typer, multiprocessing, subprocess, or threading imports

## Known Stubs

None. All three public operations are fully wired to live Redis with tested behavior.

## Threat Flags

No new security-relevant surface beyond what the plan's threat model covers.
The `state:claim:<project_hash>:<area>` key namespace was anticipated in `kv.py`'s
docstring comment (`kv_list()` scopes to `state:kv:*` only, explicitly excluding
`state:lock:*` and `state:claim:*`). No new trust boundaries introduced.

## TDD Gate Compliance

- RED gate: `test(04-01)` commit `598ca8d` — tests fail with `ModuleNotFoundError`
- GREEN gate: `feat(04-01)` commit `6d9008a` — 21 tests pass, full suite 255 passed
- REFACTOR: not needed (implementation clean on first pass)

## Self-Check: PASSED
