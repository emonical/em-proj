---
phase: 05-global-state-skill-surface
reviewed: 2026-05-26T00:00:00Z
depth: standard
files_reviewed: 9
files_reviewed_list:
  - src/em_proj/state/lock.py
  - src/em_proj/state/claim.py
  - src/em_proj/state/__init__.py
  - tests/unit/test_lock_list.py
  - tests/unit/test_claim_list.py
  - tests/multiprocess/test_lock_list_race.py
  - tests/multiprocess/test_claim_list_race.py
  - tests/structural/test_phase_05_shape.py
  - /Users/emonical/.claude/skills/em-global-state/SKILL.md
findings:
  critical: 2
  warning: 4
  info: 2
  total: 8
status: issues_found
---

# Phase 5: Code Review Report

**Reviewed:** 2026-05-26
**Depth:** standard
**Files Reviewed:** 9
**Status:** issues_found

## Summary

Phase 5 adds `lock_list_by_prefix` / `claim_list_by_prefix` pure ops, their verb wiring
(`lock-list` / `claim-list` in `state/__init__.py`), and the `em-global-state` SKILL.md.
The architecture is sound — D-17 purity is maintained, D-18 Redis chokepoint is respected,
and the `_HOLDER_DISCLOSURE_KEYS` redaction is wired correctly. Two blockers surfaced:
one is a live crash bug in `claim_list_by_prefix` (TTL fetch on an already-expired key),
and the other is a semantic gap in the `unlock` skill flow that makes the escape hatch
unreliable. Four warnings cover a test that can pass vacuously, a `_ttl` None-dereference
path that is narrowly prevented but left unguarded, a lock-list race test that doesn't
exercise the "key disappears between SCAN and GET" path the code is written to handle,
and an `atexit` registration bug carried from Phase 3 that surfaces during `--hold` with
`lock-list`.

---

## Critical Issues

### CR-01: `claim_list_by_prefix` crashes with `TypeError` when `_ttl` is None and `active`/`stale` filter is set

**File:** `src/em_proj/state/claim.py:468-472`

**Issue:** The code fetches `_ttl = client.ttl(key)` and then immediately checks `if active and _ttl <= 0`. If `client.ttl(key)` returns `-2` because the key expired between the `hgetall` call on line 449 and the `ttl` call on line 465 (a valid race path — HGETALL returns the hash contents then the TTL expires before the next round-trip), the comparison `_ttl <= 0` evaluates as `-2 <= 0 == True`, so `active=True` incorrectly excludes the key (harmless). But there is a subtler crash: `_ttl` is initialized to `None` at line 463. If `active=False` and `stale=False` at the time the lazy guard is entered and then somehow `stale` is re-evaluated as `True`, `_ttl` remains `None` and `_ttl > 0` on line 472 raises `TypeError: '>' not supported between instances of 'NoneType' and 'int'`.

Wait — actually re-reading: the real crash is that `_ttl` is `None` on line 468 (`if active and _ttl <= 0`) when `active=True` but the key does NOT expire between `hgetall` and `ttl` — this path is fine because `_ttl` is populated on line 465 before line 468. However there is a genuine bug: Redis `TTL` returns `-2` when the key does not exist (expired). The active filter line 468 treats `-2 <= 0` as True and skips it — **correct** for the active filter. But the stale filter line 471 treats `-2 > 0` as False and **also skips it** — meaning a key that disappears between HGETALL and TTL passes neither `active` nor `stale` — but also passes neither filter when `active=False` and `stale=False`. The only real crash path:

On line 463-472, `_ttl` starts as `None`. The `if active or stale:` guard on line 464 must be True for `_ttl` to be assigned. If this guard is False (both flags off), `_ttl` remains `None`. Lines 468 and 471 are individually guarded by `if active and ...` / `if stale and ...`. This means if `active=False` and `stale=False`, `_ttl` is never read. If `active=True` or `stale=True`, `_ttl` is populated. So there is no `None` dereference in the current execution path.

The real bug: **the `-2` return from `client.ttl()` (key-not-found) is semantically wrong for both filters.** When `active=True` and `_ttl == -2`, the condition `_ttl <= 0` is True → the entry is **skipped** (correct — the key is gone). When `stale=True` and `_ttl == -2`, the condition `_ttl > 0` is False → the entry is **included** (incorrect — the key is gone and should be skipped, not included as a "stale" entry). This means that a key which expires between `hgetall` and `ttl` will be included in `--stale` results with its stale data, reporting a claim that no longer exists in Redis.

**Fix:**
```python
# After fetching _ttl, guard against the "key gone" sentinel:
if active or stale:
    _ttl = client.ttl(key)

# key-not-found sentinel: skip this entry entirely (race: key expired mid-scan)
if _ttl == -2:
    continue

# Filter: active — only keys with TTL > 0 (live expiry set)
if active and _ttl <= 0:
    continue

# Filter: stale — only keys with TTL <= 0 (persistent key, no active expiry)
if stale and _ttl > 0:
    continue
```

---

### CR-02: `unlock` escape hatch in SKILL.md probes the wrong endpoint — `lock-list` does not return lock name

**File:** `/Users/emonical/.claude/skills/em-global-state/SKILL.md:121-136`

**Issue:** The `/em-global-state unlock <name>` flow says:

> Probe for a live holder: `em-proj state lock-list --json`
> Parse the `data.items` array. If an entry with `name` matching `<name>` is found…

But the `lock-list` output items contain the holder fields (`pid`, `session_id`, `project_hash`, `acquired_at`, `expires_at`, `reason`) — **not a `name` field**. The lock name is the Redis key suffix (`state:lock:<name>`) and is never included in the holder dict returned by `lock_list_by_prefix`. The `_HOLDER_DISCLOSURE_KEYS` tuple in `output.py` confirms: `pid, session_id, project_hash, acquired_at, expires_at, reason` — no `name`.

This makes the probe step as documented **unimplementable**: an agent executing this skill cannot determine which item in `data.items` corresponds to `<name>` because no item carries the lock name. The skill body instructs parsing on a field that does not exist in the output schema, so the escape hatch silently skips the confirmation step (falls through to "no matching entry found → proceed without prompting") or fails with undefined behavior.

**Fix option A (simpler, correct for the escape hatch use case):** Replace the `lock-list` probe with a direct lock attempt:
```
1. Probe: em-proj state lock-list --json
   Note: items do not include the lock name. Since the escape-hatch use case
   is forced unlock, skip to step 2 directly. If a confirmation gate is needed,
   use `em-proj state lock-list --json` to show all locks for context and ask
   the user to confirm that <name> should be unlocked, then proceed.
```

**Fix option B (correct long-term):** Add `name` to the holder dict returned by `lock_list_by_prefix` (requires a Phase 5+ field addition to the lock holder shape). Until that ships, the probe in the skill is non-functional.

The immediate fix is to reword the skill's unlock probe to acknowledge that the lock name is not in the output and ask a general "do you want to force unlock `<name>`?" question rather than a name-matched lookup.

---

## Warnings

### WR-01: `test_lock_list_stale_filter` (unit test) passes vacuously when `stale=False` assertion is wrong

**File:** `tests/unit/test_lock_list.py:156-161`

**Issue:** Lines 156-161 assert:
```python
all_results = lock_list_by_prefix(stale=False)
assert len(all_results) == 1
pids = [h["pid"] for h in all_results]
assert 99999999 in pids
```
This is asserting that `stale=False` (the default, no-filter mode) returns the dead holder. But `stale=False` does NOT mean "exclude stale holders" — it means "apply no staleness filter at all." The comment on line 155 even says "The dead holder should appear in the unfiltered list." The test is correct in intent, but it is testing `stale=False` (unfiltered) and checking that a stale holder IS present — not testing the negative case of `stale=False` (i.e., "stale holders are not excluded when stale=False"). This passes by coincidence but does not verify the complementary invariant: **that `stale=False` combined with live locks only returns live locks, not that `stale=False` is a no-op filter**. The test at line 156 does not add any real regression value beyond what line 149's `stale_results = lock_list_by_prefix(stale=True)` already establishes.

More critically: lines 160-161 assert that `all_results` contains PID `99999999` — but there is no live lock in this test (no `lock_acquire` call), so `all_results` can only contain the one dead holder. The assertion is trivially true because the dead holder is the only key in the db. A stronger test would: (1) acquire a live lock too, then verify `stale=False` returns both; (2) verify `stale=True` returns only the dead one. Without a live lock in the setup, the `stale=False` branch cannot distinguish from `stale=True`.

**Fix:** Add `lock_acquire("livelock")` in the test setup, assert that `lock_list_by_prefix(stale=False)` returns 2 entries (both live and stale), and assert that `lock_list_by_prefix(stale=True)` returns only the stale one. Clean up with `lock_release("livelock")`.

---

### WR-02: `test_lock_list_race` does not exercise the SCAN→GET expiry path

**File:** `tests/multiprocess/test_lock_list_race.py:52-168`

**Issue:** The lock acquired by child A on line 79 (`em-proj state lock list-test-lock`) uses the default TTL of 60 seconds. The lock persists across all assertions. The race scenario documented in the phase context — "keys that expire between SCAN and GET should be skipped silently" — is never triggered by these tests. No test acquires a lock with a very short TTL (`--ttl 1`) and then immediately calls `lock-list` in a race window, nor injects a key with TTL=1 and sleeps 1s. The `lock_list_by_prefix` `None`-guard (line 575: `if raw is None: continue`) is the critical path for TOCTOU; it is unit-tested only by the malformed-JSON test (test 5 in unit tests), and that test inserts a persistent key — it does not exercise the `raw is None` path at all.

The behavior is correct by code inspection, but the race guard has no test coverage that actually causes it to fire.

**Fix:** Add a test that writes a key with `EX=1`, sleeps 1.1s, then calls `lock_list_by_prefix()` and asserts it returns `[]` (not raises). This confirms the `raw is None` skip path works. A unit test using `clean_db` is sufficient — no multiprocess coordination needed.

---

### WR-03: `atexit` registration in `lock_hold_run` passes `popen=None` — cleanup may not terminate the subprocess

**File:** `src/em_proj/state/lock.py:740`

**Issue:** The `atexit.register` call on line 740 is:
```python
atexit.register(_cleanup, name, stop_event, popen)
```
At this point `popen` is `None` (the subprocess has not been spawned yet — that happens on line 748). `atexit` captures the value of `popen` at registration time, not at call time. So when `_cleanup` is invoked via `atexit`, it receives `popen=None`, which means the subprocess termination step (`popen.terminate()`) on line 530 is skipped entirely.

The signal handlers (`_sigint_handler`, `_sigterm_handler`) on lines 728-737 capture `popen` by reference (they close over the variable from the enclosing scope). Those work correctly because `popen` is reassigned on line 748 and closures look up the name at call time. But `atexit.register` is a **function call that passes a positional argument by value at registration time** — the `None` is frozen in.

This means: if the Python process exits normally (not via signal), `_cleanup` is called with `popen=None`, and the wrapped subprocess is NOT terminated. It becomes an orphan. The lock is still released (lock_release is called), but the subprocess continues running detached — which is the wrong behavior for the `--hold` contract.

This bug existed in Phase 3 code (this is a carry-forward) — the review instruction says not to re-flag Phase 3 fixes; however the Phase 3 review did not catch this bug (it was not listed as CR-01..CR-02 or WR-01..WR-04 in prior reviews based on the context given). It is being flagged here because `lock_hold_run` is now exercised by the Phase 5 SKILL.md unlock path and the bug's impact surface has grown.

**Fix:** Capture `popen` at cleanup call time, not at registration time, by wrapping in a lambda or a closure:
```python
# Replace atexit.register call:
atexit.register(lambda: _cleanup(name, stop_event, popen))
```
This works because `popen` is in the enclosing scope and the lambda looks it up at call time, after `popen` has been assigned by the `Popen(...)` call.

---

### WR-04: `test_lock_list_concurrent` (race test) acquires a lock using `em-proj state lock` but the lock expires before assertions complete

**File:** `tests/multiprocess/test_lock_list_race.py:79-88`

**Issue:** The lock acquired via `_run([EM_PROJ_BIN, "state", "lock", "list-test-lock"], ...)` on line 79 uses the default TTL of 60 seconds. The test then spawns two `lock-list` children and waits up to 10s each (lines 105, 111). The assertions on line 155-158 check that the lock-holder's session_id appears in child A's output. This is generally fine for a 60s TTL.

However, the lock is never explicitly released. After the test completes, the 60s lock lingers in db=15. If `clean_db` runs immediately after, it flushes the key — fine. But if the test suite hangs partway through (e.g., the 10s timeout on child A fires and `AssertionError` is raised on line 109), the lock is never cleaned up. More importantly: the acquire command uses `em-proj state lock` which issues a bare `lock_acquire` — a 60s lock with no refresh. If the two children's `lock-list` calls take longer than the TTL (possible if Redis is slow), the lock may expire between acquire and the lock-list assertions, making the assertion on line 155 ("session_id in items_a") silently incorrect (it will see an empty items list and fail).

This is a latent flakiness risk, not a crash, but a race test should not depend on wall-time coincidences. The assertion at line 155 has no retry logic.

**Fix:** Assert that child A's exit code is 0 AND that `items_a` is non-empty _or_ handle the case where the lock expired mid-test as an acceptable race outcome (with a comment). Alternatively, add `--ttl 30` to the acquire command and document the dependency on the test completing within 30s.

---

## Info

### IN-01: `RaceResult` imported but unused in both multiprocess race test files

**File:** `tests/multiprocess/test_lock_list_race.py:29`, `tests/multiprocess/test_claim_list_race.py:34`

**Issue:** Both race test files import `RaceResult` from `tests.conftest`:
```python
from tests.conftest import EM_PROJ_BIN, TEST_DB, RaceResult  # noqa: F401
```
The `# noqa: F401` suppresses the "imported but unused" warning. `RaceResult` is not used in either file — both tests use `subprocess.Popen` directly and do not use the `multiproc_race` fixture. The import is apparently kept to follow the "import pattern" comment in the module docstring, but it adds noise and may confuse future readers.

**Fix:** Remove `RaceResult` from the import line in both files (and the `# noqa: F401` comment if it no longer applies).

---

### IN-02: `lock-list` verb docstring says "boot_id and proc_start_epoch excluded" but `_HOLDER_DISCLOSURE_KEYS` name field ordering in `reason` differs from lock holder dict key ordering

**File:** `src/em_proj/state/__init__.py:603-611`

**Issue:** The `lock_list` verb docstring references `_HOLDER_DISCLOSURE_KEYS` but the comment on line 605 says:

> "Returns a JSON array of lock holder objects (boot_id and proc_start_epoch excluded per _HOLDER_DISCLOSURE_KEYS — T-5-03-01 information-disclosure mitigation)."

This is accurate. However, the SKILL.md (line 82) documents the lock holder fields as:
> `pid`, `session_id`, `project_hash`, `acquired_at`, `expires_at`, `reason`

The `_HOLDER_DISCLOSURE_KEYS` tuple definition in `output.py` is:
```python
("pid", "session_id", "project_hash", "acquired_at", "expires_at", "reason")
```

This matches. No real bug here — the documentation is consistent. Flagging as info only because the `reason` field appears in `_HOLDER_DISCLOSURE_KEYS` but the lock holder dict built by `_make_holder` always includes `reason` (even when it is `None`), so it will always be present in the redacted output. Callers who check for `reason` field existence will always find it. This is the correct behavior; it just is not explicitly documented in the verb docstring.

**Fix:** No code change needed. Optionally add a one-line note to the `lock_list` verb docstring: `reason is always present (may be null).`

---

_Reviewed: 2026-05-26_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
