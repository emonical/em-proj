---
phase: 04-long-lived-claims
verified: 2026-05-23T23:15:00Z
status: gaps_found
score: 3/4 must-haves verified
overrides_applied: 0
gaps:
  - truth: "em-proj state check <area> exits 3 if held by another session"
    status: failed
    reason: "check verb always exits 0 when the area is held by anyone, regardless of whether the holder's session_id matches the caller. The ROADMAP SC#2 verbatim requires exit code 3 when held by another session. Plan 04-02 explicitly deferred the 'exit 3 if held by another' path (--mine flag) to Phase 5, but Phase 5 success criteria do not include this behavior."
    artifacts:
      - path: "src/em_proj/state/__init__.py"
        issue: "check verb exits 0 unconditionally when the area is held; no session_id comparison against the caller; no --mine flag."
    missing:
      - "Add --mine flag to check verb OR always compare caller session_id against holder session_id to produce exit code 3 when held by another session (per ROADMAP SC#2)"
      - "Alternatively: open a follow-on issue or add to Phase 5 roadmap success criteria so this is explicitly tracked"
---

# Phase 4: Long-Lived Claims Verification Report

**Phase Goal:** A user (or sub-agent) can declare a long-lived claim over an area of interest with TTL and refresh semantics, and query the holder metadata that answers "is it safe to edit X?".
**Verified:** 2026-05-23T23:15:00Z
**Status:** gaps_found
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | `em-proj state claim <area>` takes 30-min claim by default; `--ttl <secs>` overrides; same holder repeating refreshes TTL rather than erroring | ✓ VERIFIED | CLI spot-check: `--ttl 60` overrides; same session_id repeat with `--ttl 90` exits 0 and Redis TTL advances from ~60 to ~90; `TTL_DEFAULT = 1800` confirmed in claim.py; multiprocess test `test_same_holder_refresh_extends_ttl` passes |
| 2 | `em-proj state check <area>` returns holder record with all 5 fields in JSON; exit 0 if held by anyone; exit 2 if not held; exit 3 if held by another session | ✗ FAILED | Exit 0 (held by anyone) and exit 2 (not held) verified by CLI spot-checks. Exit 3 (held by another) NOT implemented — check verb exits 0 regardless of who holds the claim. Plan 04-02 explicitly deferred this to Phase 5 via `--mine` flag, but Phase 5 success criteria do not include it. |
| 3 | `em-proj state release <area>` releases a claim held by the current session; releasing another session's claim errors with exit code 3 | ✓ VERIFIED | CLI spot-checks: holder release exits 0; non-holder release exits 3 with held_by_another envelope. Multiprocess test `test_non_holder_release_exits_3_claim_survives` passes. Lua compare-and-delete dual-field guard verified in claim.py:139-149. |
| 4 | With `CLAUDE_CODE_SESSION_ID` unset and no fallback resolvable, `em-proj state claim <area>` refuses with exit code 1 and "anonymous claims refused" | ✓ VERIFIED | CLI spot-check: `unset CLAUDE_CODE_SESSION_ID` produces exit 1 with `{"code":"anonymous_claim","message":"anonymous claims refused"}`. Empty-string variant also exits 1. Multiprocess test `test_anonymous_claim_refused_exit_1` covers both variants. Gate fires BEFORE any Redis call (pre-check order confirmed in `__init__.py:469-470`). |

**Score:** 3/4 truths verified

---

### Gap Detail: SC#2 "exit 3 if held by another session"

The ROADMAP SC#2 verbatim: *"exit code 0 if held by anyone, 2 if not held, **3 if held by another session**"*.

Actual behavior: `check` returns exit 0 whenever the area is held, regardless of who holds it. The full 5-field holder dict is returned in JSON (session_id is present and inspectable), but the exit-code signal for "held by another" is absent.

Plan 04-02 `<behavior>` section explicitly states:
> "NOTE: ROADMAP SC#2 also says 'exit code 3 if held by another session'. This check is the caller's responsibility once they have the holder dict — the check verb itself always exits 0 when held (regardless of who holds it) and 2 when not held. The exit-3-if-held-by-another semantics are for SDK consumers comparing against their own session_id; that comparison can be added as a --mine flag in Phase 5."

This was a deliberate plan deviation from the ROADMAP SC, not an implementation oversight. Phase 5 success criteria do not include this feature. It is a genuine untracked gap.

**Caller workaround exists:** A consumer who receives exit 0 from `check` can inspect `data.holder.session_id` and compare it against their own session to determine if they should treat it as exit 3. The holder metadata answering "is it safe to edit X?" is fully functional. However, the ROADMAP contract for the exit code is not met.

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/em_proj/state/claim.py` | Pure-ops module — claim_take, claim_refresh_or_take, claim_release, claim_check | ✓ VERIFIED | 439 lines; all 3 Lua scripts present; HeldByAnother + ClaimNotHeld exported; TTL_DEFAULT=1800; KEY_PREFIX="state:claim:"; no typer/multiprocessing/threading imports |
| `src/em_proj/state/__init__.py` | state_app registration of claim, release, check verbs | ✓ VERIFIED | 9 `@state_app.command()` decorators confirmed (get/set/del/list/lock/unlock/claim/release/check); CLAIM-03 anonymous gate present on lines 469-470 |
| `tests/unit/test_claim.py` | Unit tests for all 8 behavior cases | ✓ VERIFIED | 21 tests passing; covers take/refresh/conflict/release-by-holder/release-by-non-holder/release-absent/check-held/check-absent |
| `tests/unit/test_claim_verbs.py` | Verb-level unit tests | ✓ VERIFIED | 13 tests passing; covers claim/check/release verb behavior including both anonymous refusal variants |
| `tests/multiprocess/test_claim_race.py` | Multi-process race tests | ✓ VERIFIED | 5 tests passing; covers all 4 ROADMAP SC at CLI boundary with real em-proj subprocess invocations |
| `tests/structural/test_phase_04_shape.py` | AST-based structural invariants | ✓ VERIFIED | 11 tests passing; pins D-17/D-18/CLAIM-01/02/03/D-14 carries |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `claim.py` | `em_proj.identity` | `from em_proj.identity import resolve_session_id, resolve_project_hash` | ✓ WIRED | Line 56; both functions called in `_make_holder` and `claim_release` |
| `claim.py` | `em_proj.redis_client` | `from em_proj.redis_client import get_client` | ✓ WIRED | Line 57; `get_client()` called in claim_take, claim_release, claim_check |
| `LUA_CLAIM_REFRESH_OR_TAKE` | Redis HSET+EXPIRE or refresh | `EVAL` in `claim_take` | ✓ WIRED | Lines 341-351; all 6 ARGV values passed correctly |
| `LUA_CLAIM_COMPARE_AND_DELETE` | Redis DEL guarded by session_id match | `EVAL` in `claim_release` | ✓ WIRED | Line 388; dual-field guard (session_id + project_hash) confirmed in Lua |
| `state/__init__.py claim verb` | `em_proj.state.claim.claim_take` | `from em_proj.state.claim import ... claim_take` | ✓ WIRED | Lines 102-111 import block; claim_take called in claim verb body line 479 |
| `state/__init__.py check verb` | `emit_not_found` (exit 2 on ClaimNotHeld) | `ClaimNotHeld → emit_not_found` | ✓ WIRED | Lines 575-576; ClaimNotHeld caught, emit_not_found called |
| `state/__init__.py claim verb` | anonymous-refusal gate | `os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()` | ✓ WIRED | Lines 469-470; fires BEFORE `die_if_redis_unreachable` |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|--------------------|--------|
| `state/__init__.py check verb` | `holder` dict | `claim_check(area)` → `LUA_CLAIM_CHECK` Lua EVAL → `client.hgetall()` → `_hgetall_to_holder()` | Yes — reads live Redis HASH fields | ✓ FLOWING |
| `state/__init__.py claim verb` | `holder` dict | `claim_take(area, ttl, reason)` → `LUA_CLAIM_REFRESH_OR_TAKE` Lua EVAL → real Redis write | Yes — Lua HSET + EXPIRE to live Redis | ✓ FLOWING |
| `state/__init__.py release verb` | result from `claim_release(area)` | `LUA_CLAIM_COMPARE_AND_DELETE` Lua EVAL → Redis DEL | Yes — Lua compare-and-delete against live Redis | ✓ FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| SC#1: claim default TTL | `em-proj state claim <area> --json` | exit 0; `{"area":..,"ttl":1800,"claimed_at":..,"expires_at":..}` | ✓ PASS |
| SC#1: --ttl override | `em-proj state claim <area> --ttl 60 --json` | exit 0; ttl=60 in output | ✓ PASS |
| SC#1: same-holder refresh | repeat claim with `--ttl 90` (same CLAUDE_CODE_SESSION_ID) | exit 0; `expires_at` advances; `claimed_at` unchanged | ✓ PASS |
| SC#2: check when held | `em-proj state check <area> --json` | exit 0; holder with all 5 fields: session_id, project_hash, reason, claimed_at, expires_at | ✓ PASS |
| SC#2: check when not held | `em-proj state check nonexistent --json` | exit 2; not_found envelope | ✓ PASS |
| SC#2: check exit 3 when held by another | check after claiming with different session_id | exit 0 — SHOULD be exit 3 per ROADMAP SC#2 | ✗ FAIL |
| SC#3: release by holder | `em-proj state release <area> --json` | exit 0; `{"area":..,"released":true}` | ✓ PASS |
| SC#3: release by non-holder | release with different CLAUDE_CODE_SESSION_ID | exit 3; held_by_another envelope with holder metadata | ✓ PASS |
| SC#4: anonymous refusal (unset) | `unset CLAUDE_CODE_SESSION_ID; em-proj state claim <area>` | exit 1; `{"code":"anonymous_claim","message":"anonymous claims refused"}` | ✓ PASS |
| SC#4: anonymous refusal (empty) | `CLAUDE_CODE_SESSION_ID="" em-proj state claim <area>` | exit 1; anonymous claims refused | ✓ PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| CLAIM-01 | 04-01, 04-02, 04-03, 04-04 | `em-proj state claim <area> [--ttl <secs>]` (default 30min, refreshable) and `release <area>` | ✓ SATISFIED | TTL_DEFAULT=1800 in claim.py; --ttl override verified; refresh semantics verified; release by holder exits 0 |
| CLAIM-02 | 04-01, 04-02, 04-03, 04-04 | `em-proj state check <area>` returns holder metadata `{session_id, project_hash, reason, claimed_at, expires_at}` | ✓ SATISFIED | All 5 fields returned in check output; spot-checked; multiprocess tests pass |
| CLAIM-03 | 04-02, 04-03, 04-04 | Claims refuse anonymous holders — session-id must be resolvable or errors exit 1 | ✓ SATISFIED | Anonymous refusal gate in `__init__.py:469-470`; fires before Redis call; verified both empty-string and unset variants |

Note: REQUIREMENTS.md maps CLAIM-01, CLAIM-02, CLAIM-03 to Phase 4. All three are satisfied. The SC#2 gap is at the ROADMAP success criteria level (exit code semantics), not the requirements level (the "returns holder metadata" requirement is met).

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `src/em_proj/state/claim.py` | 356-359 | Non-atomic HGETALL after Lua "refreshed" — TOCTOU race | ⚠️ Warning | If claim key expires between EVAL and HGETALL, `_hgetall_to_holder({})` raises unhandled KeyError. Narrow race window; no test triggers it; all 286 tests pass. Flagged in code review as CR-01. |
| `src/em_proj/state/claim.py` | 361-364 | Non-atomic HGETALL after Lua "conflict" — KeyError if holder expires mid-race | ⚠️ Warning | Same class of bug as CR-01. `_hgetall_to_holder({})` raises KeyError if contested key expires between EVAL and HGETALL. Flagged in code review as CR-02. Fix: `existing = _hgetall_to_holder(raw) if raw else None`. |
| `src/em_proj/state/__init__.py` | 488-492, 542-544 | `except Exception` with attribute-sniffing for ValidationError | ⚠️ Warning | Overly broad catch; ValidationError is already imported; fragile ordering dependency. Flagged in code review as WR-02. |
| `src/em_proj/state/__init__.py` | 143-148 | `get` verb falls through to `emit_ok` after KvNotFound (unbound `value`) | ⚠️ Warning | Pre-existing Phase 2/3 pattern. Latent UnboundLocalError if emit_not_found is ever changed to return. Flagged in code review as WR-01. |

None of the anti-patterns are debt-marker comments (no TBD/FIXME/XXX). All are code quality issues that don't block the phase goal under normal conditions.

**CR-01 and CR-02 assessment for goal-backward verdict:** The success criteria require correct claim/release/check behavior. These bugs affect the "refreshed" and "conflict" paths in claim_take under adversarial timing — narrow windows where a key expires between two Redis round-trips. All 5 race tests pass. The goal is substantively achieved under normal and tested operating conditions. The bugs are real but narrow; they are the kind of thing that warrants a follow-up issue, not a phase block. Judgment: WARNING, not BLOCKER.

---

### Human Verification Required

None. All observable truths are verifiable programmatically.

---

### Gaps Summary

**1 gap blocking full goal achievement:**

**SC#2 missing exit code 3:** The `check` verb returns exit 0 regardless of who holds the claim. The ROADMAP SC#2 specifies exit 3 when held by another session. Plan 04-02 explicitly deferred this behavior ("the --mine flag") to Phase 5, but Phase 5 success criteria do not include it. The caller workaround (inspecting `holder.session_id` from the exit-0 response) exists but the exit code contract is not met.

**Resolution options:**
1. Add `--mine` flag or implicit session comparison to the `check` verb (small addition to `src/em_proj/state/__init__.py`)
2. Add this to Phase 5 success criteria explicitly so it has a tracked home
3. Override this gap if the team accepts that "exit 3" semantics belong to the SDK consumer layer, not the check verb

**To accept this deviation, add to VERIFICATION.md frontmatter:**

```yaml
overrides:
  - must_have: "em-proj state check <area> exits 3 if held by another session"
    reason: "Exit-3-if-held-by-another is the SDK consumer's responsibility via holder.session_id comparison; the check verb intentionally exits 0 when held by anyone (read-only query semantics); deferred to Phase 5 --mine flag"
    accepted_by: "<name>"
    accepted_at: "<ISO timestamp>"
```

---

_Verified: 2026-05-23T23:15:00Z_
_Verifier: Claude (gsd-verifier)_
