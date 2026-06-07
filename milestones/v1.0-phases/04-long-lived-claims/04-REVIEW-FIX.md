---
phase: 04-long-lived-claims
fixed_at: 2026-05-24T00:00:00Z
review_path: .planning/phases/04-long-lived-claims/04-REVIEW.md
iteration: 1
findings_in_scope: 6
fixed: 6
skipped: 0
status: all_fixed
---

# Phase 4: Code Review Fix Report

**Fixed at:** 2026-05-24
**Source review:** .planning/phases/04-long-lived-claims/04-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 6 (CR-01, CR-02, WR-01, WR-02, WR-03, WR-04)
- Fixed: 6
- Skipped: 0

All 283 tests passed (3 skipped — planning worktree not attached) after every fix.

## Fixed Issues

### CR-01: Non-atomic HGETALL after Lua "refreshed" — TOCTOU on same-holder refresh

**Files modified:** `src/em_proj/state/claim.py`
**Commit:** 93c1805
**Applied fix:** Replaced `client.hgetall(redis_key)` + `_hgetall_to_holder(raw)` on the "refreshed" path with `return holder`. The locally-built holder already contains the correct `session_id`, `project_hash`, `reason`, `claimed_at` (not mutated on refresh), and freshly-computed `expires_at`. The separate HGETALL round-trip created a TOCTOU window where a concurrent release could delete the key and cause `_hgetall_to_holder({})` to raise `KeyError`. Updated the docstring step 8 to remove the incorrect "Redis is authoritative — read back" note.

### CR-02: Non-atomic HGETALL after Lua "conflict" — KeyError crash if holder expires mid-race

**Files modified:** `src/em_proj/state/claim.py`
**Commit:** 6c690fd
**Applied fix:** Added `if raw else None` guard on the conflict path: `existing = _hgetall_to_holder(raw) if raw else None`. If the conflicting holder's key expires between the Lua EVAL returning "conflict" and the subsequent HGETALL, `hgetall()` returns `{}` — the guard passes `None` to `HeldByAnother(holder=None)` instead of crashing with `KeyError: 'session_id'`. Updated docstring step 9 accordingly.

### WR-01: `get` verb falls through to `emit_ok(value)` after `KvNotFound` — latent UnboundLocalError

**Files modified:** `src/em_proj/state/__init__.py`
**Commit:** a200975
**Applied fix:** Moved `emit_ok(...)` into an `else:` clause on both the `get` verb and the `delete_kv` verb. Both previously had `emit_ok` after the `try/except` block, reachable only because `emit_not_found`/`emit_error` happen to raise `SystemExit`. The `else:` clause makes the control flow explicit and prevents `UnboundLocalError` if either helper is ever refactored to return rather than raise.

### WR-02: `claim` and `release` verbs use bare `except Exception` with attribute-sniffing for `ValidationError`

**Files modified:** `src/em_proj/state/__init__.py`
**Commit:** 543fdd8
**Applied fix:** Replaced `except Exception as e: if hasattr(e, "code") and hasattr(e, "message"): ...` with `except ValidationError as e: emit_error(e.code, e.message, ...)` in both the `claim` and `release` verb handlers. `ValidationError` is already imported from `em_proj.state.kv`. Also moved `emit_ok(...)` into `else:` clauses for consistency with the WR-01 fix.

### WR-03: `LUA_CLAIM_CHECK` — `if not raw_result` conflates `false` with empty HASH

**Files modified:** `src/em_proj/state/claim.py`
**Commit:** 9a3cfe4
**Applied fix:** Changed `if not raw_result:` to `if raw_result is None:` in `claim_check`. The Lua script returns false (Python `None` via redis-py `decode_responses=True`) when the key is absent. The old falsy guard would also trigger for an empty list `[]`, which could mask corrupted-HASH state. Updated the comment to clarify the intent.

### WR-04: `test_claim_verbs.py` — anonymous-refusal test has no Redis pre-check isolation

**Files modified:** `tests/unit/test_claim_verbs.py`
**Commit:** 3f8536c
**Applied fix:** Added a `monkeypatch.setattr` on `em_proj.state.die_if_redis_unreachable` that records calls into a `redis_calls` list, with a final assertion that the list is empty. The previous test only checked exit code and output text, which passes whether Redis is running or not — if the ordering were accidentally reversed (anonymous check moved after the Redis pre-check), the test would still pass as long as Redis is available. The mock ensures the test fails if `die_if_redis_unreachable` is called before the anonymous check. Note: the REVIEW.md suggested `em_proj.state.__init__.die_if_redis_unreachable` as the monkeypatch target, but the correct importable path is `em_proj.state.die_if_redis_unreachable` (verified by running the test).

---

_Fixed: 2026-05-24_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
