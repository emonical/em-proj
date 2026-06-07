---
phase: 07-project-scoped-reservation-registry
plan: "01"
subsystem: state
tags: [redis, lua, identity, upstream, reservation, pure-ops, unit-tests]

# Dependency graph
requires:
  - phase: 04-project-scoped-claim-registry
    provides: claim.py structural mirror (KEY_PREFIX, Lua scripts, exceptions, holders, public ops)
  - phase: 03-session-scoped-lock-registry
    provides: identity.py base (resolve_session_id, resolve_project_hash, stale probes)
provides:
  - resolve_upstream_identity() + _canonicalize_upstream_url() in identity.py (RESERVE-01)
  - src/em_proj/state/reserve.py — pure-ops reservation module with 7-field holder (RESERVE-02)
  - tests/unit/test_upstream_identity.py — 20 Redis-free tests
  - tests/unit/test_reserve.py — 15 Redis-backed tests against db=15
affects:
  - 07-02-plan (verb wiring — consumes reserve_take/release/check/list_by_prefix)
  - 07-03-plan (structural tests — asserts namespace invariants)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Structural mirror pattern: reserve.py is claim.py + 3 named deltas (KEY_PREFIX, 7-field holder, upstream_identity compare)"
    - "_RESERVE_ARGV_ORDER tuple constant: documents Lua ARGV positions to prevent index drift (Pitfall #3)"
    - "Fallback-chain resolver pattern: subprocess.run → canonicalize → project_hash fallback"
    - "Two-namespace invariant: state:reserve: and state:claim: prefixes never cross-contaminate"
    - "git init for fake clone in tests (plain .git/config insufficient — real git requires objects/)"

key-files:
  created:
    - src/em_proj/state/reserve.py
    - tests/unit/test_upstream_identity.py
    - tests/unit/test_reserve.py
  modified:
    - src/em_proj/identity.py

key-decisions:
  - "resolve_upstream_identity uses subprocess.run with shell=False + argv list + timeout=5.0 (T-3-01-03 extension for functional need)"
  - "reserve.py imports resolve_upstream_identity for grep traceability but does NOT call it — verb layer (Plan 07-02) resolves and passes as parameter"
  - "reserve_take/release/check use keyword-only upstream_identity to prevent positional arg swaps (Pitfall #3 defense)"
  - "_URL_FORM regex extended to allow user:token@ form (user-info with colon) — RESEARCH regex omitted colon from charset"
  - "git init required for fake clone helper in tests — plain .git/config + .git/HEAD is not a valid git repo"
  - "Q-H RESOLUTION: Plan 07-02 verb wiring cannot extract workstream name from claim_check('workstream.active').reason — that field is EMPTY when Phase 6 sets a workstream (Phase 6 does not pass --reason when claiming workstream.active). TTY prompt MUST fire when --workstream is not explicitly passed."

patterns-established:
  - "Plan 07-01: Keyword-only arguments on pure-ops public functions prevent positional swap bugs across analogs"
  - "Plan 07-01: _RESERVE_ARGV_ORDER constant + Pitfall#3 mitigation test as paired documentation mechanism"

requirements-completed:
  - RESERVE-01
  - RESERVE-02

# Metrics
duration: 45min
completed: 2026-05-31
---

# Phase 7 Plan 01: Upstream Identity Resolver + Reserve Pure-Ops Module

**Canonical URL-to-host:owner/repo resolver + Redis-atomic reservation module (KEY_PREFIX="state:reserve:") with 7-field holder and per-upstream key namespace**

## Performance

- **Duration:** ~45 min
- **Started:** 2026-05-31
- **Completed:** 2026-05-31
- **Tasks:** 2
- **Files modified:** 4 (1 extended, 3 new)

## Accomplishments
- Extended `src/em_proj/identity.py` with `_canonicalize_upstream_url()` (13-row SCP/URL regex canonicalizer) and `resolve_upstream_identity()` (subprocess.run fallback chain)
- Created `src/em_proj/state/reserve.py` — structural mirror of claim.py with KEY_PREFIX="state:reserve:", 7-field holder (adds `upstream_identity` + `workstream`), and (session_id, upstream_identity) Lua refresh/compare semantics
- Created `tests/unit/test_upstream_identity.py` — 20 Redis-free tests (13 parametrized canonicalizer rows + 7 resolver behavior cases)
- Created `tests/unit/test_reserve.py` — 15 Redis-backed tests (3 constant checks + 11 behavior cases including two-namespace coexistence + Pitfall#3 ARGV-drift detection)
- All 251 unit tests pass; claim.py untouched

## Task Commits

1. **Task 1: Extend identity.py + test_upstream_identity.py** — `39e9f4a` (feat)
2. **Task 2: Create reserve.py + test_reserve.py** — `a71986a` (feat)

**Plan metadata:** (committed with SUMMARY)

## Files Created/Modified
- `src/em_proj/identity.py` — Extended with `import subprocess`, `import re`, `_SCP_FORM`, `_URL_FORM`, `_canonicalize_upstream_url`, `resolve_upstream_identity`; module docstring Phase 7 subsection added
- `src/em_proj/state/reserve.py` — NEW: full pure-ops reservation module (KEY_PREFIX="state:reserve:", 3 Lua scripts, HeldByAnother + ReserveNotHeld exceptions, 4 public ops, _RESERVE_ARGV_ORDER constant)
- `tests/unit/test_upstream_identity.py` — NEW: 13-row parametrized canonicalizer + 7 resolver behavior cases (Redis-free)
- `tests/unit/test_reserve.py` — NEW: 15 tests including cross-namespace coexistence + Pitfall#3 HGETALL key assertion

## Decisions Made
- **subprocess in identity.py**: `resolve_upstream_identity` uses `subprocess.run(shell=False, timeout=5.0)` with argv list. Earlier resolvers rejected subprocess (T-3-01-03) but only because there was no functional need. Phase 7 HAS a functional need — upstream identity cannot be derived from cwd alone.
- **Verb layer owns resolver call**: `reserve.py` imports `resolve_upstream_identity` for grep traceability but never calls it. The verb layer resolves and passes `upstream_identity` as a parameter to all pure-ops functions. This matches RESEARCH §Architectural Responsibility Map.
- **Keyword-only `*` on public ops**: `reserve_take(area, *, upstream_identity, workstream, ...)` prevents positional argument swap between `upstream_identity` and `workstream` (Pitfall #3 defense).
- **Q-H RESOLUTION (hand-off to Plan 07-02)**: Direct inspection of Phase 6 confirms `--reason` is NOT passed when claiming `workstream.active`. Therefore `claim_check("workstream.active").reason` is `None` even when a workstream is set. Plan 07-02's `_resolve_workstream` helper CANNOT derive the workstream name from that field — the TTY prompt MUST fire whenever `--workstream` is not explicitly passed on the CLI. `claim_check("workstream.active")` proves a workstream EXISTS but cannot reveal its NAME.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] _URL_FORM regex did not match user:token@ user-info form**
- **Found during:** Task 1 — `test_canonicalize_upstream_url_table[user_token_https]`
- **Issue:** RESEARCH §Pattern 1 regex used `[a-zA-Z0-9_.\-]+@` for user-info, excluding the `:` needed for `user:token@host/...` form. Row 8 of the 13-row table failed.
- **Fix:** Changed charset to `[a-zA-Z0-9_.\-:]+@` in `_URL_FORM` to allow colon in user-info segment.
- **Files modified:** `src/em_proj/identity.py`
- **Verification:** All 13 parametrized rows pass; no other rows regressed.
- **Committed in:** `39e9f4a` (Task 1 commit)

**2. [Rule 1 - Bug] Plain .git/config + .git/HEAD is not a valid git repository**
- **Found during:** Task 1 — `test_resolve_upstream_identity_with_origin_returns_canonical`
- **Issue:** RESEARCH §Pattern 5 described writing `.git/config` + `.git/HEAD` as sufficient for `git -C <dir> remote get-url origin`. In practice, git requires a valid `objects/` tree to recognize a directory as a repository — without it, git exits 128 "not a git repository".
- **Fix:** Changed `_make_fake_git_config` helper to call `git init <dir>` then append the `[remote "origin"]` section to the generated `.git/config`. This produces a real (empty) git repository that `git remote get-url` accepts.
- **Files modified:** `tests/unit/test_upstream_identity.py`
- **Verification:** All 7 resolver behavior cases pass, including the 3 fallback cases that depend on git exiting non-zero (non-git dir remains a non-git dir since we don't run git init there).
- **Committed in:** `39e9f4a` (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (both Rule 1 — Bug)
**Impact on plan:** Both fixes were necessary for correctness. Neither changes the design; both are within-function repairs to match the 13-row test vector. No scope creep.

## Issues Encountered
None beyond the two auto-fixed bugs above.

## Known Stubs
None — all functions fully implemented; no placeholder returns or TODO paths.

## Threat Flags
No new threat surfaces beyond those documented in the plan's `<threat_model>`. The two mitigations confirmed implemented:
- T-07-01: _canonicalize_upstream_url returns None for non-matching inputs → resolver falls back to project_hash.
- T-07-09: subprocess.run timeout=5.0 caps git hang time; TimeoutExpired triggers fallback.
- T-07-12: _RESERVE_ARGV_ORDER constant + test_reserve_take_area_key_uses_upstream_identity_prefix test.

## Q-H Finding (hand-off to Plan 07-02)

**RESOLVED via direct code inspection.** Phase 6 does NOT pass `--reason <name>` when claiming `workstream.active`. The `claim_check("workstream.active").reason` field is therefore `None` whenever Phase 6 set the workstream — even when a workstream IS set.

**Implication for Plan 07-02:** `_resolve_workstream` helper CANNOT extract the workstream name from `reason`. The field proves existence (`claim_check` succeeds → workstream is set) but not identity (the name). The TTY prompt MUST fire whenever `--workstream` is not explicitly passed on the CLI. This is the "UX is worse than expected" graceful failure from RESEARCH Open Q-H "Risk if wrong" — correct behavior, worse UX.

## Next Phase Readiness
- **Plan 07-02 (verb wiring)**: `reserve_take`, `reserve_release`, `reserve_check`, `reserve_list_by_prefix` all ready; `resolve_upstream_identity` exported from identity.py. Verb layer should call resolver and pass `upstream_identity=` kwarg to all ops. See Q-H finding above for `_resolve_workstream` implementation guidance.
- **Plan 07-03 (structural tests)**: `reserve.py` does not contain `state:claim:` literal; `claim.py` is untouched; two-namespace runtime test passes. Structural assertions can grep source files directly.

---
*Phase: 07-project-scoped-reservation-registry*
*Completed: 2026-05-31*
