---
phase: 05-global-state-skill-surface
fixed_at: 2026-05-26T00:00:00Z
review_path: .planning/phases/05-global-state-skill-surface/05-REVIEW.md
iteration: 1
findings_in_scope: 6
fixed: 6
skipped: 0
status: all_fixed
---

# Phase 5: Code Review Fix Report

**Fixed at:** 2026-05-26
**Source review:** `.planning/phases/05-global-state-skill-surface/05-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 6 (CR-01, CR-02, WR-01, WR-02, WR-03, WR-04)
- Fixed: 6
- Skipped: 0

---

## Fixed Issues

### CR-02: `unlock` escape hatch probes wrong endpoint — `lock-list` does not return lock name

**Files modified:** `src/em_proj/state/lock.py`, `src/em_proj/state/claim.py`, `src/em_proj/output.py`, `tests/unit/test_lock_list.py`, `tests/unit/test_claim_list.py`, `tests/unit/test_output.py`, `~/.claude/skills/em-global-state/SKILL.md`
**Commit:** `af6c469`
**Applied fix:** Injected `holder["name"] = key[len(KEY_PREFIX):]` into `lock_list_by_prefix` after decoding each holder, so callers can match on lock name. Added `"name"` to `_HOLDER_DISCLOSURE_KEYS` in `output.py` so the `lock-list` verb includes it after redaction (it was previously stripped out by the key-allowlist redaction). Analogous `holder["area"] = key[len(scan_prefix):]` inject added to `claim_list_by_prefix`. Unit tests updated to assert `name` and `area` fields are present and correct. `_HOLDER_DISCLOSURE_KEYS` pin test updated. SKILL.md `locks` section updated to document the `name` field, and the `unlock` probe description updated to describe how to match by `name`.

---

### CR-01: `claim_list_by_prefix` includes ghost entries when key expires between HGETALL and TTL

**Files modified:** `src/em_proj/state/claim.py`, `tests/unit/test_claim_list.py`
**Commit:** `0e6e319`
**Applied fix:** Added `if _ttl == -2: continue` inside the lazy-TTL block (after `_ttl = client.ttl(key)`, before the `active` / `stale` filter checks). Redis returns `-2` when the key does not exist; the previous code would include such keys as stale entries. Added `test_claim_list_ttl_expiry_race` which monkeypatches `client.ttl` to return `-2` for a specific key and asserts both `active=True` and `stale=True` calls return empty lists (no ghost entries, no crash).

---

### WR-01: `test_lock_list_stale_filter` passes vacuously — no live lock to discriminate against

**Files modified:** `tests/unit/test_lock_list.py`
**Commit:** `af685f3`
**Applied fix:** Added `lock_acquire("livelock")` in the test body with a `try/finally` that calls `lock_release("livelock")`. The `stale=False` assertion now checks `len(all_results) == 2` (both live and dead holder) and asserts both PIDs are present. `stale=True` continues to assert only the dead holder is returned. The test can now meaningfully discriminate: `stale=True` returns 1 entry, `stale=False` returns 2.

---

### WR-02: `test_lock_list_race` does not exercise the SCAN→GET expiry path

**Files modified:** `tests/unit/test_lock_list.py`
**Commit:** `76848a6`
**Applied fix:** Added `test_lock_list_scan_get_expiry_race` to the unit test file. The test writes a lock-namespace key with `EX=1`, confirms it is visible, sleeps 1.1s for expiry, then calls `lock_list_by_prefix()` and asserts no ghost entry is returned. This exercises the `if raw is None: continue` guard in `lock_list_by_prefix` that had no prior test coverage causing it to fire.

---

### WR-03: `atexit.register` passes `popen=None` — subprocess not terminated on normal exit

**Files modified:** `src/em_proj/state/lock.py`, `tests/unit/test_lock_hold_run.py`
**Commit:** `dacbc3e`
**Applied fix:** Replaced `atexit.register(_cleanup, name, stop_event, popen)` with `atexit.register(lambda: _cleanup(name, stop_event, popen))` so `popen` is captured by reference from the enclosing scope rather than frozen to `None` at registration time. Added `test_lock_hold_run_atexit_captures_popen_by_reference` which intercepts `atexit.register` via monkeypatch, runs `lock_hold_run`, then asserts `mock_proc.terminate` was called — confirming `_cleanup` received the real Popen object and not `None`.

---

### WR-04: `test_lock_list_concurrent` acquires lock with default 60s TTL and never releases it

**Files modified:** `tests/multiprocess/test_lock_list_race.py`
**Commit:** `bff2de7`
**Applied fix:** Added `--ttl 30` to the acquire command (SC-LL-1). Wrapped the assertion body in `try/finally` that calls `em-proj state unlock list-test-lock` to explicitly release the lock after the test, regardless of assertion outcome. The 30s TTL provides a defined safe margin (both lock-list children have 10s timeouts each; the total test window is well within 30s). The lock no longer lingers for 60s in db=15 after test exit.

---

## Skipped Issues

None — all 6 in-scope findings were fixed.

---

_Fixed: 2026-05-26_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
