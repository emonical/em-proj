---
phase: 03-identity-advisory-locks
plan: "04"
subsystem: lock-verbs
tags: [lock, unlock, verbs, typer, cli, tty-gated, warn-flow, hold-stub, d07, d08, d09, tdd]
dependency_graph:
  requires:
    - 03-03  # lock.py public surface: lock_acquire, lock_release, lock_force_displace, HeldByAnother
    - 02-04  # state/__init__.py thin-verb pattern (resolve_json_mode, get_client, die_if_redis_unreachable)
  provides:
    - state/__init__.py: @state_app.command("lock") + @state_app.command("unlock")
    - 6 total verbs in state_app (4 kv + 2 lock)
    - --hold dispatch stub: single emit_error("not_implemented") call site for Plan 03-05 to replace
  affects:
    - 03-05  # --hold implementation replaces the stub in state/__init__.py
    - 03-06  # structural tests assert no private-symbol imports from lock.py in state/__init__.py
tech_stack:
  added: []
  patterns:
    - D-07 dual-isatty: both sys.stdout.isatty() AND sys.stdin.isatty() required for --warn
    - D-08 mutex: warn+hold mutually exclusive before any Redis call
    - D-14/D-17 thin-verb: only public lock.py symbols imported (no _encode_holder, no KEY_PREFIX)
    - em_proj.state.sys monkeypatch pattern for CliRunner TTY tests
key_files:
  created:
    - tests/unit/test_state_lock_verbs.py
  modified:
    - src/em_proj/state/__init__.py
decisions:
  - "D-07 dual-isatty check: sys.stdout.isatty() AND sys.stdin.isatty() both required"
  - "--warn prompt template: \"Lock '{name}' held by session {holder_sid} (pid {holder_pid}, age {age_s}s). Override? [y/N]: \""
  - "Displacement warning: \"Warning: displaced session {holder_sid}'s lock on '{name}'\""
  - "em_proj.state.sys monkeypatch pattern required for CliRunner TTY simulation"
  - "Live-PID holder required for --warn tests (dead PID triggers stale-takeover)"
metrics:
  duration: "~35 minutes"
  completed: "2026-05-23T21:27:04Z"
  tasks_completed: 2
  files_modified: 2
  files_created: 1
  tests_added: 19
  total_tests: 194
---

# Phase 03 Plan 04 Summary — Wire lock + unlock verbs + CliRunner test suite

## What landed

`src/em_proj/state/__init__.py` — extended from 4 verbs (get/set/del/list) to 6 verbs (+ lock + unlock). The two new verbs follow the D-14/D-17 thin-verb discipline: parse argv → resolve_json_mode → get_client + die_if_redis_unreachable → call lock op → emit_*. All lock logic stays in lock.py; all displacement Lua is server-side via the public `lock_force_displace` op.

`tests/unit/test_state_lock_verbs.py` — 19 CliRunner tests covering every flag combination and exit code for the lock + unlock verbs.

## The two verbs

| Typer verb | Python function | Key behaviors |
|------------|-----------------|---------------|
| `lock`     | `lock()`        | D-08 mutex, --hold stub, Redis pre-check, lock_acquire, --warn TTY flow |
| `unlock`   | `unlock()`      | Redis pre-check, lock_release, D-09 non-holder learns |

Both follow the D-18 chokepoint: `client = get_client(); die_if_redis_unreachable(client)` before any business call (6 total occurrences in the module, one per verb).

## --warn prompt template (D-12 discretion)

```
Lock '{name}' held by session {holder_sid} (pid {holder_pid}, age {age_s}s). Override? [y/N]: 
```

Emitted to `sys.stderr.write()`. Includes only non-sensitive fields: lock name (known to caller), session_id (human-readable identifier), pid (diagnostic), age (seconds since acquired_at). Does not include boot_id, proc_start_epoch, project_hash, Redis host, or env values (T-3-XX-02).

## Displacement warning format (D-09)

```
Warning: displaced session {holder_sid}'s lock on '{name}'
```

Emitted to `sys.stderr.write()` immediately after `lock_force_displace` succeeds. Informs the user they overrode someone. The displaced holder learns via unlock-time HeldByAnother (D-09 principle).

## D-14/D-17 thin-shell confirmation

`grep -c "_encode_holder" src/em_proj/state/__init__.py` = 0. No private symbol imports from lock.py. The verb body uses only: `lock_acquire`, `lock_release`, `lock_force_displace`, `HeldByAnother`, `DEFAULT_TTL`, `MIN_TTL`, `MAX_TTL`. Displacement Lua is entirely server-side in `lock_force_displace`; the verb body never touches the key namespace or encoding internals.

## --hold stub (D-17 UX invariant)

The single dispatch call site in the `lock` verb body:

```python
if hold:
    # Placeholder — Plan 03-05 replaces with lock_hold_run dispatch
    emit_error(
        "not_implemented",
        "--hold is implemented in Plan 03-05",
        json_mode=json_mode,
    )
```

Exits 1 with `{status:error, error:{code:not_implemented, message:"--hold is implemented in Plan 03-05"}}`. No `NotImplementedError`, no Python traceback. Plan 03-05 replaces this one call site with `lock_hold_run(...)` dispatch.

## Plan 03-05 entry condition

The `--hold` dispatch in `state/__init__.py` is exactly one `emit_error("not_implemented", ...)` call site. Plan 03-05 replaces it with the real `lock_hold_run(name, cmd, ttl=effective_ttl, reason=reason)` dispatch. The verb signature, mutex check, and bare-lock path all stay frozen — Plan 03-05 modifies only the stub call site.

## Test count

19 tests in `tests/unit/test_state_lock_verbs.py`. Full suite: 194 passed, 0 failed.

| Test | Name | What it verifies |
|------|------|-----------------|
| 1 | `test_lock_happy_path_acquires_and_emits_ok` | LOCK-01 OK envelope: name, ttl=DEFAULT_TTL, schema_version |
| 2 | `test_lock_held_by_live_exits_3` | LOCK-02 block + exit 3 with held_by_another envelope |
| 3 | `test_lock_with_ttl_override` | --ttl 30 honored in envelope and Redis TTL |
| 4 | `test_lock_with_reason_persists` | --reason persisted to holder JSON in Redis |
| 5 | `test_lock_reason_too_long_exits_1` | D-12 reason cap: 257 chars → exit 1 validation_error |
| 6 | `test_lock_colon_in_name_exits_1` | D-09 key validation carry: colon → exit 1 |
| 7 | `test_lock_ttl_zero_rejected` | D-04 TTL bounds: ttl=0 → exit 1 or 2 |
| 8 | `test_lock_warn_hold_mutex_exits_1_no_redis` | D-08: --warn+--hold exits 1 before Redis call |
| 9 | `test_lock_warn_non_tty_exits_1_warn_requires_tty` | D-07: --warn non-TTY exits 1 warn_requires_tty |
| 10 | `test_lock_warn_tty_yes_displaces_holder` | D-07: --warn TTY y → displacement, pid updated |
| 11 | `test_lock_warn_tty_no_leaves_original_holder` | D-07: --warn TTY n → exit 3, holder unchanged |
| 12 | `test_lock_hold_stub_structured_error` | D-17: --hold stub exits 1, no traceback |
| 13 | `test_unlock_happy_path_releases` | LOCK-01: unlock clears key, exit 0 |
| 14 | `test_unlock_non_holder_exits_3` | D-09: non-holder exits 3 held_by_another, key untouched |
| 15 | `test_unlock_colon_in_name_exits_1` | key validation carry for unlock |
| 16 | `test_lock_help` | --help shows --warn, --hold, --ttl, --reason, --json |
| 17 | `test_unlock_help` | --help shows --json |
| 18 | `test_lock_envelope_schema_version` | D-02: schema_version == "1" on lock OK |
| 19 | `test_unlock_envelope_schema_version` | D-02: schema_version == "1" on unlock OK |

## Typer 0.25.1 quirk (Rule 1 fix)

The plan spec had `cmd: Annotated[list[str] | None, typer.Argument(default=None, ...)]` but typer 0.25.1 raises `AnnotatedParamWithDefaultValueError` when `default=` is set inside `Annotated` for an `Argument`. Fix: remove `default=None` from the `Annotated` annotation; the `= None` default is already set at the parameter level. This is a known typer 0.25.1 restriction where `Argument` defaults must be set via `=` assignment, not inside `Annotated`.

## CliRunner TTY monkeypatch pattern

CliRunner replaces `sys.stdin` and `sys.stdout` with BytesIO buffers during `invoke()`. To simulate a TTY for the `--warn` path, the tests patch the `sys` module attribute inside `em_proj.state` with a mock:

```python
import em_proj.state as state_mod
monkeypatch.setattr(state_mod, "sys", _tty_sys_mock())
```

The mock's `stdin.readline()` delegates dynamically to `real_sys.stdin.readline()` at call time (after CliRunner has patched `sys.stdin` with its input BytesIO), so `input="y\n"` works correctly. The mock's `stdout.isatty()` and `stdin.isatty()` return True unconditionally.

Important: tests 10/11 use the CURRENT process's composite (no pid override) as the pre-set holder. A dead-PID holder would trigger stale-takeover in `lock_acquire`, bypassing the HeldByAnother path entirely.

## Deviations from plan

### [Rule 1 - Bug] Annotated default= not accepted by typer 0.25.1

- **Found during:** Task 2 test run
- **Issue:** `Annotated[list[str] | None, typer.Argument(default=None, ...)]` raises `AnnotatedParamWithDefaultValueError` in typer 0.25.1
- **Fix:** Removed `default=None` from the `Annotated` annotation on the `cmd` parameter; the `= None` assignment provides the default
- **Files modified:** `src/em_proj/state/__init__.py`
- **Commit:** f3dbadf (bundled with Task 2 commit)

### [Rule 2 - Missing] Live-PID holder required for --warn TTY tests

- **Found during:** Task 2 test run (tests 10/11 failing with exit_code=0)
- **Issue:** Plan spec used `pid=99998` for the pre-set holder; on macOS, pid 99998 is almost certainly dead, triggering stale-takeover in `lock_acquire` which bypasses HeldByAnother entirely
- **Fix:** Changed tests 10/11 to use the current process's composite (`_make_live_holder("foo")` without pid override) so the pre-set holder is a live holder and lock_acquire correctly block-polls then raises HeldByAnother
- **Files modified:** `tests/unit/test_state_lock_verbs.py`
- **Commit:** f3dbadf

### [Rule 2 - Missing] em_proj.state.sys monkeypatch needed for CliRunner TTY simulation

- **Found during:** Task 2 test run (test 10 "displaced" not in stderr)
- **Issue:** Patching `sys.stdout.isatty` globally doesn't affect the CliRunner-replaced buffer; the verb's `sys.stdout.isatty()` call was on the buffer (returns False)
- **Fix:** Replace `state_mod.sys` with a mock module where `stdout.isatty()` and `stdin.isatty()` return True, and `stdin.readline()` delegates dynamically to the current `real_sys.stdin` (CliRunner's BytesIO)
- **Files modified:** `tests/unit/test_state_lock_verbs.py`
- **Commit:** f3dbadf

## Requirements satisfied

- **LOCK-01** — `em-proj state lock <name>` and `em-proj state unlock <name>` work end-to-end
- **LOCK-02** — 1s block + exit 3 on still-held + --warn opts into TTY-gated override via lock_force_displace

## Self-Check: PASSED

- File `src/em_proj/state/__init__.py` — FOUND with lock + unlock verbs
- File `tests/unit/test_state_lock_verbs.py` — FOUND (19 tests)
- Commit `3462bfa` (Task 1 replay) — FOUND
- Commit `f3dbadf` (Task 2 tests) — FOUND
- `bash scripts/test.sh all` — 194 passed, 0 failed
- `grep -c "@state_app.command(" src/em_proj/state/__init__.py` = 6
- `grep -c "NotImplementedError" src/em_proj/state/__init__.py` = 0
- `grep -c '"not_implemented"' src/em_proj/state/__init__.py` = 1
- `grep -c "_encode_holder" src/em_proj/state/__init__.py` = 0
- `grep -c "_encode_holder" tests/unit/test_state_lock_verbs.py` = 0
