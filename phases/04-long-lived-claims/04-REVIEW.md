---
phase: 04-long-lived-claims
reviewed: 2026-05-23T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - src/em_proj/state/claim.py
  - src/em_proj/state/__init__.py
  - tests/unit/test_claim.py
  - tests/unit/test_claim_verbs.py
  - tests/multiprocess/test_claim_race.py
  - tests/structural/test_phase_04_shape.py
findings:
  critical: 2
  warning: 4
  info: 3
  total: 9
status: issues_found
---

# Phase 4: Code Review Report

**Reviewed:** 2026-05-23
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Phase 4 ships the `em-proj state claim/release/check` verb family with Lua-backed
atomicity and a five-field holder record. The architecture is sound: pure-ops
separation in `claim.py`, anonymous-refusal gate before Redis calls, and dual-field
`(session_id, project_hash)` ownership checks throughout. The multiprocess race
tests are well-constructed.

Two critical defects were found. First, the `claim_take` "refreshed" path performs
a bare non-atomic `hgetall` after the Lua script completes — the same TOCTOU hazard
the Lua script was intended to prevent. Second, the `claim_take` "conflict" path
has the same non-atomic follow-up read, but additionally has a latent KeyError crash
if the conflicting holder expires between the Lua `EVAL` and the Python `hgetall`
call. Neither of these windows is wide, but they are real races that can produce
incorrect behavior or unhandled exceptions in production.

Four warnings cover: (1) `get` verb falls through to `emit_ok` after a
`KvNotFound` exception in the existing (pre-Phase 4) code path — a latent
`UnboundLocalError` crash bug; (2) the `claim` and `release` verbs use bare
`except Exception` with attribute-sniffing in place of importing `ValidationError`
directly from `claim.py`; (3) the Lua `LUA_CLAIM_CHECK` returns `false` but the
Python caller tests `if not raw_result`, which conflates a `false` return with an
empty HASH; (4) the `test_claim_verbs.py` Test 6 has a dead tautology assertion.

---

## Critical Issues

### CR-01: Non-atomic HGETALL after Lua "refreshed" — TOCTOU on same-holder refresh

**File:** `src/em_proj/state/claim.py:356-359`

**Issue:** When the Lua script returns `"refreshed"`, the Python code immediately
issues a separate `client.hgetall(redis_key)` to read back the authoritative state.
Between the `EVAL` returning and the `HGETALL` executing, a different server command
(e.g. `DEL` from another process releasing the claim) can run. The claim.py docstring
for `claim_take` explicitly states "Redis is authoritative" as the justification for
this read-back, but that statement is only true atomically inside Lua — not in a
separate round-trip. If the key is deleted after `"refreshed"` is returned, `hgetall`
returns `{}` (empty dict), and `_hgetall_to_holder({})` raises `KeyError` on
`raw["session_id"]` (line 269) — an unhandled exception propagated to the caller.

This is a genuine race: any hold on `refresh_area` by a parallel intruder who times
a release between the two round-trips produces an unhandled crash rather than a
clean `HeldByAnother`.

```python
# Current — TOCTOU between EVAL and HGETALL:
if result == "refreshed":
    raw = client.hgetall(redis_key)          # key may vanish here
    return _hgetall_to_holder(raw)           # KeyError if raw == {}

# Fix — return the locally-built holder for "refreshed":
# The locally-built holder already contains the caller's correct session_id,
# project_hash, reason, claimed_at, and the freshly-computed expires_at.
# The "Redis is authoritative for claimed_at" comment applies only to
# the *initial* claimed_at from the first take, which is not mutated by
# a refresh. Using the local holder for the refresh return is both
# correct and race-free.
if result == "refreshed":
    return holder   # local holder has correct expires_at = now + ttl
```

### CR-02: Non-atomic HGETALL after Lua "conflict" — KeyError crash if holder expires mid-race

**File:** `src/em_proj/state/claim.py:361-364`

**Issue:** When the Lua script returns `"conflict"`, the Python code issues
`client.hgetall(redis_key)`. If the conflicting holder's key expires (or is
released by that holder) between the `EVAL` and the `HGETALL`, `hgetall` returns
`{}`. The code at line 363 passes `{}` to `_hgetall_to_holder`, which immediately
raises `KeyError: 'session_id'` (line 269) — an unhandled exception that escapes
`claim_take`. The verb layer's `except ClaimHeldByAnother` at `__init__.py:480`
does not catch this, so the caller sees an internal Python traceback instead of a
clean `HeldByAnother` error with `holder=None`.

This is a real race on contested areas with short TTLs.

```python
# Current — crashes if key expires between EVAL and HGETALL:
raw = client.hgetall(redis_key)
existing = _hgetall_to_holder(raw)           # KeyError if raw == {}
raise HeldByAnother(holder=existing)

# Fix — guard against an empty dict:
raw = client.hgetall(redis_key)
existing = _hgetall_to_holder(raw) if raw else None
raise HeldByAnother(holder=existing)
```

---

## Warnings

### WR-01: `get` verb falls through to `emit_ok(value)` after `KvNotFound` — latent UnboundLocalError

**File:** `src/em_proj/state/__init__.py:143-148`

**Issue:** The `get` verb body reads:

```python
try:
    value = kv_get(key)
except KvNotFound:
    emit_not_found(...)           # raises SystemExit(2) — OK
except ValidationError as e:
    emit_error(...)               # raises SystemExit(1) — OK
emit_ok({"key": key, "value": value}, ...)
```

`emit_not_found` and `emit_error` both raise `SystemExit`, so in normal operation
the fall-through to `emit_ok` is dead after those exceptions. However, if any
future refactor changes one of those helpers to return instead of raise (a real
risk given the `NoReturn` annotation is only advisory), the `emit_ok` call on
line 148 executes with `value` unbound, producing `UnboundLocalError: local
variable 'value' referenced before assignment`. This pattern also appears in
`delete_kv` (line 203-205) where `deleted` is similarly unbound if the
`ValidationError` path is taken and somehow falls through.

This is a pre-existing pattern in Phase 2/3 code, but Phase 4 inherits it and it
should be fixed before the pattern propagates further.

**Fix:** Add `return` or `else:` guards to make the fall-through impossible:
```python
try:
    value = kv_get(key)
except KvNotFound:
    emit_not_found(f"key '{key}' not set", json_mode=json_mode)  # noqa: dead after raise
except ValidationError as e:
    emit_error(e.code, e.message, json_mode=json_mode)           # noqa: dead after raise
else:
    emit_ok({"key": key, "value": value}, json_mode=json_mode)
```

### WR-02: `claim` and `release` verbs use bare `except Exception` with attribute-sniffing for `ValidationError`

**File:** `src/em_proj/state/__init__.py:488-492, 542-544`

**Issue:** Both the `claim` verb (lines 488-492) and the `release` verb (lines
542-544) catch `ValidationError` from `claim.py` via:

```python
except Exception as e:
    if hasattr(e, "code") and hasattr(e, "message"):
        emit_error(e.code, e.message, json_mode=json_mode)
    raise
```

`ValidationError` is already imported from `em_proj.state.kv` at line 86. The
`claim.py` module re-exports it via `from em_proj.state.kv import validate_key, ValidationError`.
The correct pattern used by every other verb is `except ValidationError as e:`.

The `except Exception` catch is broader than intended: any exception with `.code`
and `.message` attributes — including `HeldByAnother` itself — will be silently
swallowed by `emit_error` instead of being re-raised with its correct semantics.
In practice `ClaimHeldByAnother` is caught first by the preceding `except`
clause, but the ordering dependency is fragile and the broadness is a quality
defect.

**Fix:**
```python
# claim verb:
except ClaimHeldByAnother as e:
    emit_held_by_another(...)
except ValidationError as e:
    emit_error(e.code, e.message, json_mode=json_mode)

# release verb — identical pattern
```

### WR-03: `LUA_CLAIM_CHECK` — `if not raw_result` conflates `false` with empty HASH

**File:** `src/em_proj/state/claim.py:427-428`

**Issue:** The Lua `LUA_CLAIM_CHECK` script returns `false` (Lua nil/false) when
the key is absent. redis-py with `decode_responses=True` translates Lua `false`
as Python `None`. The Python caller tests:

```python
if not raw_result:
    raise ClaimNotHeld(...)
```

This is correct for the `None` case, but `not raw_result` is also truthy for an
empty list `[]`. A Redis HASH can never be empty while a key exists (HSET always
writes at least one field), so this is not currently reachable in production. But
the guard creates a subtle logic gap: if a bug elsewhere creates a Redis HASH key
with zero fields, `claim_check` would raise `ClaimNotHeld` instead of returning
a holder, silently masking the corrupted state. More importantly, the condition
disguises the intent.

**Fix:** Test for `None` explicitly:
```python
if raw_result is None:
    raise ClaimNotHeld(message=f"area '{area}' is not claimed")
```

### WR-04: `test_claim_verbs.py` — anonymous-refusal test has no Redis pre-check isolation

**File:** `tests/unit/test_claim_verbs.py:267-280`

**Issue:** `test_claim_anonymous_fires_before_redis` (line 267) is the critical
test verifying that the anonymous-refusal gate fires *before* any Redis call. The
test omits `clean_db` (intentional) but also omits any mechanism to verify that
Redis was *not* contacted. The test passes because `emit_error` raises
`SystemExit(1)` before `die_if_redis_unreachable` is called. However, if the
gate check in `__init__.py` were accidentally reordered (anonymous check moved
after the Redis pre-check), the test would still pass as long as Redis is running
— it would just fail on `die_if_redis_unreachable` for a different reason.

A more reliable test would either (a) point the client at a non-existent Redis
port and assert exit 1 (not the connection-error exit 1 message), or (b) mock
`die_if_redis_unreachable` and assert it was never called.

**Fix (option b — least invasive):**
```python
def test_claim_anonymous_fires_before_redis(monkeypatch):
    called = []
    monkeypatch.setattr(
        "em_proj.state.__init__.die_if_redis_unreachable",
        lambda *a, **kw: called.append(True)
    )
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    result = runner.invoke(app, ["state", "claim", "docs/api"])
    assert result.exit_code == 1
    assert not called, "die_if_redis_unreachable must not be called before anonymous refusal"
```

---

## Info

### IN-01: `test_claim_verbs.py` — Test 6 has a dead tautology assertion

**File:** `tests/unit/test_claim_verbs.py:168`

**Issue:** The assertion:
```python
assert "reason" in holder or "reason" in holder, ...
```
Both sides of the `or` are identical — this is a tautology. The second clause is
a copy-paste remnant. It provides no additional coverage.

**Fix:** Replace with the same pattern used for all other fields:
```python
assert "reason" in holder, f"Missing reason in holder: {holder}"
```

### IN-02: `claim_take` "refreshed" path does not update `reason` field

**File:** `src/em_proj/state/claim.py:119`

**Issue:** The Lua `LUA_CLAIM_REFRESH_OR_TAKE` script on the refresh path only
updates `expires_at` and the Redis TTL:

```lua
redis.call('HSET', KEYS[1], 'expires_at', ARGV[5])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[6]))
```

If the caller supplies a new `reason` on a refresh call, it is silently ignored.
The existing stored `reason` is preserved unchanged. The claim.py docstring does
not document this behavior explicitly. A caller who does:
```
claim_take("area", reason="initial reason")
claim_take("area", reason="updated reason")   # <-- reason silently not updated
```
...will see the first `reason` on a subsequent `claim_check`. This may be
intentional (per CLAIM-01 design), but it is undocumented and potentially
surprising.

**Fix:** Either document the invariant ("reason is set at take time and never
updated on refresh") in `LUA_CLAIM_REFRESH_OR_TAKE`'s docstring and in
`claim_take`'s docstring, or add `reason` to the refresh HSET call:
```lua
redis.call('HSET', KEYS[1], 'reason', ARGV[3], 'expires_at', ARGV[5])
```

### IN-03: `test_no_typer_import` and `test_no_multiprocessing_or_threading_import` use relative path

**File:** `tests/unit/test_claim.py:302, 317`

**Issue:** Both AST-inspection tests in `test_claim.py` open `claim.py` using:
```python
src = pathlib.Path("src/em_proj/state/claim.py")
```
This is a relative path resolved against the process working directory at test
runtime. If pytest is invoked from a directory other than the repo root, the file
will not be found and the test will raise `FileNotFoundError` (not a helpful
failure). The structural tests in `test_phase_04_shape.py` correctly use
`Path(__file__).resolve().parent.parent.parent` for repo-root anchoring.

**Fix:**
```python
src = Path(__file__).resolve().parent.parent.parent / "src" / "em_proj" / "state" / "claim.py"
```

---

_Reviewed: 2026-05-23_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
