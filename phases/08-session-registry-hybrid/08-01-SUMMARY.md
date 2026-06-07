---
phase: 08-session-registry-hybrid
plan: "01"
subsystem: session
tags: [session-registry, redis, lua, tdd]
dependency_graph:
  requires:
    - em_proj.identity (current_process_composite, is_holder_stale, resolve_session_id, resolve_upstream_identity)
    - em_proj.redis_client (get_client)
    - em_proj.state.claim (KEY_PREFIX — cross-namespace scan)
    - em_proj.state.lock (KEY_PREFIX — cross-namespace scan)
    - em_proj.state.reserve (KEY_PREFIX — cross-namespace scan)
  provides:
    - em_proj.session (session_register, session_heartbeat, session_list, session_show)
    - state:session:<session_id> Redis key namespace
  affects:
    - Plan 08-02 (verb layer imports session_register/heartbeat/list/show from this module)
    - Plan 08-03 (harness exercises CLI verbs backed by these ops)
tech_stack:
  added:
    - src/em_proj/session.py (new module)
    - tests/unit/test_session.py (46 unit tests)
  patterns:
    - Lua atomic upsert (LUA_SESSION_UPSERT mirrors LUA_CLAIM_REFRESH_OR_TAKE)
    - Lazy read-time stale reaping (D3)
    - Cross-namespace scan for enrichment join (D4)
    - TDD RED/GREEN discipline (test commit before implementation commit)
key_files:
  created:
    - src/em_proj/session.py
    - tests/unit/test_session.py
  modified: []
decisions:
  - D5 idempotency: refreshed branch reads registered_at from Redis via HGETALL (not from caller's `now`) to return the preserved original value
  - Claim/reserve stale-probe: v1.0 records lack pid fields; code checks for optional pid presence before calling is_holder_stale (claim holders without pid are included without stale probe; test-written records with pid are stale-probed)
  - Local imports for state submodules inside _scan_all_holders_by_session_id to avoid circular import risk
metrics:
  duration_seconds: 289
  completed_date: "2026-06-07T21:25:23Z"
  tasks_completed: 3
  files_created: 2
  files_modified: 0
---

# Phase 08 Plan 01: Session Registry Core Module Summary

**One-liner:** Machine-global session HASH registry with Lua atomic upsert/heartbeat, cross-namespace enrichment join, and lazy stale reaping via is_holder_stale composite probe.

## Completed Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| RED | Failing tests for session module | f602d81 | tests/unit/test_session.py |
| GREEN (Tasks 1+2+3) | Session registry core implementation | 541bcb4 | src/em_proj/session.py |

## What Was Built

`src/em_proj/session.py` — the pure-ops core module for the Phase 8 session registry.

**Public API (6 symbols + exception):**
- `KEY_PREFIX = "state:session:"` — machine-global Redis namespace
- `TTL_DEFAULT = 300` — 5-minute heartbeat backstop (D2)
- `SessionNotFound` — exception with `code = "not_found"`
- `session_register()` — upsert to Redis HASH, D5 idempotency preserves `registered_at`
- `session_heartbeat()` — atomic last_heartbeat refresh + TTL re-arm
- `session_list()` — enriched list with held counts (D1), lazy stale reaping (D3)
- `session_show(session_id)` — full session record + full held dicts (D1)

**Private helpers:**
- `_build_session_key(session_id)` — KEY_PREFIX + session_id
- `_hgetall_to_session(raw)` — type coercions: pid→int, proc_start_epoch/registered_at/last_heartbeat→float
- `_scan_all_holders_by_session_id()` — cross-namespace scan of claim/lock/reserve, grouped by session_id

**Lua scripts:**
- `LUA_SESSION_UPSERT` — registered/refreshed/conflict returns; preserves registered_at on refresh
- `LUA_SESSION_HEARTBEAT` — refreshed/not_found/conflict returns

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Claim holder stale-probe for test-written records with pid fields**
- **Found during:** Task 2 GREEN (test_scan_all_holders_skips_stale_holders failing)
- **Issue:** Test writes a claim HASH that includes pid/proc_start_epoch/boot_id to simulate a stale holder, but the implementation skipped stale-probe for all claim records (because standard CLAIM-02 records don't carry those fields). This caused the fake stale holder to appear in results.
- **Fix:** Check if the claim/reserve HASH carries optional pid/proc_start_epoch/boot_id fields; apply is_holder_stale only when those fields are present. Standard v1.0 records without pid are included without stale probe (session TTL is the backstop). Records with pid (test-written or future extended records) are stale-probed.
- **Files modified:** src/em_proj/session.py (_scan_all_holders_by_session_id)
- **Commit:** 541bcb4 (included in GREEN phase commit)

## Threat Surface Scan

No new network endpoints introduced. `state:session:*` key namespace is added to the Redis surface — this is the planned addition per D4. No auth paths, no file access patterns, no schema changes at external trust boundaries beyond what is documented in the plan's threat register.

## Known Stubs

None. The module delivers full business logic with no placeholder returns or hardcoded empty values.

## Self-Check

- [x] src/em_proj/session.py exists
- [x] tests/unit/test_session.py exists
- [x] Commit f602d81 (RED) exists in git log
- [x] Commit 541bcb4 (GREEN) exists in git log
- [x] `scripts/test.sh unit -k "session"` passes (46/46)
- [x] All 301 unit tests pass (no regressions)
- [x] No typer/multiprocessing/subprocess/threading imports in session.py
- [x] 6 public symbols + SessionNotFound present

## Self-Check: PASSED
