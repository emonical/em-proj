# Phase 11: Listener Daemon — Code Review

**Verdict:** CHANGES REQUESTED → **REMEDIATED** (Criticals + Highs fixed)

**Findings:** 2 Critical · 3 High · 2 Medium · 1 Low

## Remediation status (commit `e1fc356`, 2026-06-08)

| ID | Severity | Status |
|----|----------|--------|
| C-01 | Critical | ✅ FIXED — `_daemon_start` is now a read-only probe; the child is the sole HASH writer. New `test_daemon_start_detaches` covers the path. |
| C-02 | Critical | ✅ FIXED — heartbeat loop now breaks if `session_heartbeat()`'s returned `session_id` diverges from the daemon's owned id. |
| H-01 | High | ✅ FIXED — stale re-registration checks the retry write result and exits cleanly if another daemon won the race. |
| H-02 | High | ✅ FIXED — poll loop uses the `DAEMON_HEARTBEAT_INTERVAL` constant (single source) instead of re-reading the env var. |
| H-03 | High | ✅ FIXED — crash-recovery test captures `new_pid` and SIGTERMs it as a cleanup fallback; removed the dead `new_proc` block. |
| M-01 | Medium | ⏸ DEFERRED — `_daemon_stop` does not catch `PermissionError` (single-user context; "never raises" docstring is aspirational). Tracked as debt. |
| M-02 | Medium | ⏸ DEFERRED — `sys.exit(0)` in an internal function is stylistic; downgraded by reviewer. Tracked as debt. |
| L-01 | Low | ⏸ DEFERRED — `mbox_write` AST guard checks only attribute calls, not direct-name calls. Hardening tracked as debt. |

Full suite after remediation: **502 passed**, 9 pre-existing Phase 6 orphan
failures unchanged. All Phase 11 daemon tests green (8 lifecycle + 8 structural).

---

## Original findings (for reference)

---

## Critical

### C-01: `_daemon_start` writes its own composite to the HASH then immediately spawns a child — the HASH holds the *parent's* pid, not the daemon's

**File:** `src/em_proj/session/_daemon.py:185–206`

**What's wrong:**
`_daemon_start` calls `_daemon_record_write(session_id)` as step 1, which runs the Lua script using `current_process_composite()` — the *parent* CLI process's PID and identity. Only then does it `subprocess.Popen` the actual daemon. So the daemon HASH ends up containing the CLI invocation's PID (which exits 0 immediately) rather than the background child's PID.

Downstream consequences:
- `_daemon_hash` in tests asserts `int(raw["pid"]) == proc.pid`. This will **fail** unless the test happens to observe the hash only after the child has overwritten it.
- `is_holder_stale` in a subsequent `session listen` call reads the CLI PID, which is already dead → incorrectly classifies a live daemon as stale → spawns a second daemon.
- `_daemon_stop` sends SIGTERM to the dead CLI PID (ProcessLookupError path) rather than the actual daemon.

**Why the test might still pass by accident:** The child process (`--foreground`) calls `_daemon_foreground_run`, which immediately calls `_daemon_record_write` again. Because the parent's HASH record is already present, the Lua script returns the *parent's* existing record to the child. The child then checks `is_holder_stale(result)` — if the parent CLI process has already exited (it has), `is_holder_stale` returns True, so the child DELs the record and re-registers itself (line 262–263). This means the HASH eventually holds the correct child PID, but only after a brief window where it holds the dead parent PID, and only because the foreground path runs the HASH write a second time. The detach path (`_daemon_start` without `--foreground`) is never tested end-to-end in a real background-process scenario where the parent exits before the child writes — the existing `test_daemon_start_detaches` test referenced in the plan was not implemented in the submitted code, so this correctness window is untested.

**The fundamental design conflict:** The plan specifies that the HASH records the daemon's PID. In `_daemon_start`, the HASH should be written by the child process (in `_daemon_foreground_run`), not by the parent. The parent should do no HASH write at all; it should spawn the child and return `{"status": "started", "pid": proc.pid}` (using the Popen pid) without pre-writing anything. The single-instance guard is already handled by `_daemon_foreground_run`'s first call to `_daemon_record_write`.

**Fix:** Remove the `_daemon_record_write` call from `_daemon_start`. Instead, rely on the child's own `_daemon_foreground_run` to write the HASH. For the `already_running` detection path in `_daemon_start`, replace the Lua-write-then-check pattern with a read-only probe:

```python
def _daemon_start(session_id: str) -> dict:
    # Probe for an existing live daemon (read-only — no HASH write from this process).
    record = _daemon_record_read(session_id)
    if record is not None and not is_holder_stale(record):
        return {"status": "already_running", "pid": record["pid"]}
    # Stale record present — clear it before spawning.
    if record is not None:
        _daemon_record_del(session_id)
    # Spawn the background daemon; it writes its own HASH in _daemon_foreground_run.
    binary = shutil.which("em-proj") or "em-proj"
    proc = subprocess.Popen(
        [binary, "session", "listen", "--foreground"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=None,
    )
    return {"status": "started", "pid": proc.pid}
```

Note: This changes the single-instance guarantee from "Lua-atomic in the parent" to "child writes its own record". The TOCTOU window between the read-only probe and the child's write is acceptable because `_daemon_foreground_run` already handles the race via its own Lua write-or-detect guard.

---

### C-02: `session_heartbeat()` in `_daemon_foreground_run` reads `CLAUDE_CODE_SESSION_ID` from the daemon's environment, not from the `session_id` argument — silent heartbeat misfires when env var is absent or wrong

**File:** `src/em_proj/session/_daemon.py:296`

**What's wrong:**
`session_heartbeat()` in `_ops.py` resolves the session ID by calling `resolve_session_id()`, which reads `os.environ["CLAUDE_CODE_SESSION_ID"]` or falls back to `pid-<os.getpid()>`. The daemon's `_daemon_foreground_run` receives `session_id` as a parameter but never passes it to `session_heartbeat`. Instead it calls `session_heartbeat()` with no arguments (line 296).

In the detached subprocess path, the parent's `session listen` command invokes the child via `subprocess.Popen([..., "session", "listen", "--foreground"], env=None)`. The `--foreground` handler calls `resolve_session_id()` to obtain `session_id`, which means `CLAUDE_CODE_SESSION_ID` must be set in the *spawning environment* and must propagate to the child. This is documented in the plan as working correctly via `env=None` inheritance.

However, if the daemon's heartbeat session_id (from `CLAUDE_CODE_SESSION_ID`) is ever different from the `session_id` argument passed to `_daemon_foreground_run` — for example in future refactors, or if the env var is mutated between the two calls — the heartbeat silently refreshes the *wrong* session key. The daemon HASH key uses `session_id` (the argument) but the heartbeat uses `resolve_session_id()` (the env var). These are distinct code paths with no cross-check.

More concretely: if `_daemon_foreground_run` is called with a `session_id` argument (which `__init__.py` does — `_daemon_foreground_run(session_id)` where `session_id = resolve_session_id()`), the two resolutions will agree in normal operation. But the heartbeat path is fragile: it silently heartbeats a different key if they ever diverge, and `SessionNotFound` would be raised from the wrong key — causing the daemon to exit and incorrectly reporting the session as reaped.

**Fix:** Pass `session_id` explicitly to a heartbeat call. Since `session_heartbeat()` does not accept a `session_id` parameter, wrap it:

```python
# In _daemon_foreground_run, replace bare session_heartbeat() with:
try:
    # session_heartbeat() reads resolve_session_id() internally.
    # Guard against env/arg divergence by checking the result matches our session_id.
    hb_result = session_heartbeat()
    # Optional but defensive:
    if hb_result.get("session_id") != session_id:
        # Heartbeat refreshed a different session — our session key is gone.
        break
    last_heartbeat = now
except SessionNotFound:
    break
```

Or, more robustly, add a `session_id` parameter to `session_heartbeat()` in `_ops.py` so the caller can pass it directly rather than relying on env resolution.

---

## High

### H-01: `_daemon_foreground_run` proceeds to the poll loop after the stale-record re-registration path without confirming the second `_daemon_record_write` succeeded — silent re-entry risk

**File:** `src/em_proj/session/_daemon.py:261–263`

**What's wrong:**
In the stale-record path (lines 261–263), the code does:
```python
_daemon_record_del(session_id)
_daemon_record_write(session_id)
```

The return value of the second `_daemon_record_write` is discarded. If a second daemon won the race in the narrow window between the `DEL` and this second write (another process also detected the stale record and wrote first), `_daemon_record_write` returns a dict (the winner's record) rather than `"written"`. The code falls through to the poll loop without checking — now two daemons both believe they are the authoritative holder for `session_id`.

**Fix:**
```python
_daemon_record_del(session_id)
retry_result = _daemon_record_write(session_id)
if isinstance(retry_result, dict) and not is_holder_stale(retry_result):
    # Another daemon won the re-registration race — exit cleanly.
    sys.exit(0)
```

---

### H-02: `DAEMON_HEARTBEAT_INTERVAL` module constant is evaluated at import time — daemon spawned from a different process inherits the *parent's* evaluated value, not the env var set for the child

**File:** `src/em_proj/session/_daemon.py:46–48`

**What's wrong:**
```python
DAEMON_HEARTBEAT_INTERVAL: int = int(
    os.environ.get("EM_PROJ_DAEMON_HEARTBEAT_INTERVAL", "60")
)
```

This is evaluated once when the module is imported. In the detached subprocess path, the child process re-imports the module and evaluates the constant fresh from its environment (which inherits `EM_PROJ_DAEMON_HEARTBEAT_INTERVAL`). This is fine.

However, the poll loop in `_daemon_foreground_run` (line 284) also re-reads the env var:
```python
interval = int(os.environ.get("EM_PROJ_DAEMON_HEARTBEAT_INTERVAL", "60"))
```

This means the module-level `DAEMON_HEARTBEAT_INTERVAL` constant is never used inside the poll loop — the constant exists but the actual behavior is driven by the local re-read on line 284. This is inconsistent: the exported constant and the runtime behavior are defined independently. If a caller queries `DAEMON_HEARTBEAT_INTERVAL` to understand the active interval, they get the import-time value (which could differ from the poll loop's value if the env var changed between import and poll-loop entry — a real scenario in tests that mutate `os.environ` before calling `_daemon_foreground_run` directly).

**Fix:** Use the constant in the poll loop:
```python
interval = DAEMON_HEARTBEAT_INTERVAL
```

If env-var-at-runtime overridability is intentional, remove the module-level constant or document clearly that the exported constant is advisory only. Mixing the two is the defect.

---

### H-03: `test_daemon_crash_recovery` leaks the new detached daemon process — cleanup only references `new_proc` which is always `None`

**File:** `tests/multiprocess/test_daemon_lifecycle.py:409,460–462`

**What's wrong:**
```python
new_proc = None
try:
    ...
    result = _run_cli(["session", "listen", "--json"], session_id=session_id)
    ...
    new_pid = data.get("pid")
    ...
finally:
    _run_cli(["session", "stop"], session_id=session_id, timeout=5.0)
    time.sleep(1.0)
    if new_proc is not None and new_proc.poll() is None:  # never True — new_proc is always None
        new_proc.kill()
        new_proc.wait(timeout=3)
```

`_run_cli(["session", "listen", "--json"])` uses the detach path (no `--foreground`), which spawns a background daemon and returns immediately. The Popen object for that daemon is never captured — `new_proc` remains `None` throughout. The `finally` block's `new_proc.kill()` guard is dead code.

The cleanup relies entirely on `_run_cli(["session", "stop"], ...)` in the `finally`. If that call fails (Redis down, daemon exits but HASH wasn't cleaned, test interrupted), the detached daemon process is orphaned. There is no timeout-escalation to SIGKILL.

This is a test reliability defect, not a production code defect, but orphaned daemon processes can cause interference between test runs (the `clean_db` FLUSHDB removes the session record, causing the daemon's next heartbeat to raise `SessionNotFound` and exit — but this is timing-dependent and not guaranteed within a single test run's teardown window).

**Fix:** After `_run_cli(["session", "listen", "--json"])` spawns the new daemon, read the new pid from the response and terminate it explicitly:

```python
new_pid = data.get("pid")
# Attempt to find and terminate the new daemon process explicitly in finally.
# Store pid so cleanup can SIGTERM it if session stop fails.
finally:
    stop_result = _run_cli(["session", "stop"], session_id=session_id, timeout=5.0)
    time.sleep(1.0)
    # If stop failed, send SIGTERM directly to the known pid.
    if new_pid is not None:
        try:
            os.kill(new_pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
```

---

## Medium

### M-01: `_daemon_stop` has a TOCTOU window between `is_holder_stale` and `os.kill` — but `ProcessLookupError` is handled; `PermissionError` is not

**File:** `src/em_proj/session/_daemon.py:233–239`

**What's wrong:**
The plan explicitly documents handling `ProcessLookupError` (T-11-02-05). The code does catch it. However, `os.kill(pid, signal.SIGTERM)` can also raise `PermissionError` (EPERM) if the process exists but is owned by a different user, or in restricted environments. This exception propagates uncaught out of `_daemon_stop` and from there through `session_stop_cmd` to the CLI, producing an unhandled traceback rather than a clean error message.

In a single-user context this is unlikely (the daemon is always spawned by the same user), but it is a latent crash path that violates the docstring: "never raises."

**Fix:**
```python
try:
    os.kill(pid, signal.SIGTERM)
    return {"status": "stop_signaled", "pid": pid}
except ProcessLookupError:
    _daemon_record_del(session_id)
    return {"status": "stopped"}
except PermissionError:
    # pid exists but we cannot signal it — treat as an unowned/orphaned record.
    _daemon_record_del(session_id)
    return {"status": "stale_record_cleared"}
```

---

### M-02: `_daemon_foreground_run` does not clean up the pubsub connection on the early-exit path (stale race → `sys.exit(0)` on line 260)

**File:** `src/em_proj/session/_daemon.py:255–260`

**What's wrong:**
The early exit at line 260 (`sys.exit(0)`) is reached before `ps` (the pubsub object) is ever created. That particular early exit is fine for resources. However, there is a second early exit path: if `_daemon_record_write` on retry (the stale re-registration path, lines 261–263) were to follow with a `sys.exit(0)` (after the fix recommended in H-01), the pubsub would still not yet exist at that point. The concern is actually the inverse: after the fix to H-01 adds a `sys.exit(0)` on line ~265, the `ps.unsubscribe()` / `ps.close()` in the shutdown block (lines 303–304) would be unreachable from that path.

More importantly: the `SessionNotFound` `break` at line 300 exits the `while not _shutdown` loop but does NOT call `ps.unsubscribe()` or `ps.close()` before reaching the cleanup block at lines 302–306. Tracing the control flow:

```
while not _shutdown:          # loop body
    ...
    except SessionNotFound:
        break                 # exits the while loop

# Falls through to here:
ps.unsubscribe()              # line 303 — is reached
ps.close()                    # line 304 — is reached
_daemon_record_del(session_id)
sys.exit(0)
```

Actually the cleanup block IS reached from the `break` path. On re-read, this is not a defect — the `break` exits the `while` and falls through to lines 302–306. This finding is downgraded; see note below.

**Residual concern:** The `sys.exit(0)` on line 260 (early exit before pubsub is created) is safe. **No resource leak exists in the submitted code for this path.** Downgrading to a style note: the early `sys.exit(0)` could be replaced with a `return` since `_daemon_foreground_run` is declared `-> None`, and `sys.exit` is unconventional for a non-shell-boundary function. The caller (`session_listen_cmd`) does not emit anything after `_daemon_foreground_run(session_id)` anyway, so a `return` would be equivalent and cleaner. Keeping as Medium only because `sys.exit(0)` in an internal function is mildly surprising and makes reasoning about cleanup paths harder.

---

## Low

### L-01: The `mbox_write` AST check in `test_phase_11_shape.py` only walks `ast.Attribute` call nodes — a direct `mbox_write(...)` call (no attribute access) would be missed

**File:** `tests/structural/test_phase_11_shape.py:87–96`

**What's wrong:**
```python
called_attrs = {
    node.func.attr
    for node in ast.walk(tree)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
}
assert "mbox_write" not in called_attrs
```

This only catches `something.mbox_write(...)` attribute-style calls. A direct call `mbox_write(...)` where the function is imported directly (e.g. `from em_proj.message._ops import mbox_write`) would use `ast.Name` for `node.func`, not `ast.Attribute`, and would be invisible to this check.

In the current code this is fine because `mbox_write` is not imported at all. But the test provides weaker-than-stated guarantees: it only catches attribute calls, not name calls. If a future edit imports and calls `mbox_write` directly, the structural test silently passes.

**Fix:** Add a parallel check for `ast.Name` calls:

```python
called_names = {
    node.func.id
    for node in ast.walk(tree)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
}
assert "mbox_write" not in called_names, (
    "_daemon.py must not call mbox_write directly"
)
assert "mbox_write" not in called_attrs, (
    "_daemon.py must not call mbox_write via attribute access"
)
```

---

## Summary Table

| ID   | Severity | File                                    | Issue                                                                               |
|------|----------|-----------------------------------------|-------------------------------------------------------------------------------------|
| C-01 | Critical | `_daemon.py:185–206`                    | `_daemon_start` writes *parent* CLI process PID to HASH before spawning child       |
| C-02 | Critical | `_daemon.py:296`                        | `session_heartbeat()` resolves session_id from env, not from `session_id` argument  |
| H-01 | High     | `_daemon.py:261–263`                    | Second `_daemon_record_write` return value discarded — silent two-daemon race       |
| H-02 | High     | `_daemon.py:46–48, 284`                 | Module-level `DAEMON_HEARTBEAT_INTERVAL` constant unused in poll loop                |
| H-03 | High     | `test_daemon_lifecycle.py:409,460–462`  | `new_proc` always `None` in crash-recovery test — detached daemon cleanup is dead   |
| M-01 | Medium   | `_daemon.py:233–239`                    | `os.kill` can raise `PermissionError`; not caught; docstring claims "never raises"  |
| M-02 | Medium   | `_daemon.py:255–260`                    | `sys.exit(0)` in internal function is unconventional; return would be cleaner        |
| L-01 | Low      | `test_phase_11_shape.py:87–96`          | `mbox_write` AST guard misses direct-name calls (only checks `ast.Attribute`)       |

---

## Per-Focus-Area Notes

**Security (T-11-01-01, T-11-02-01, T-11-02-05):** `_daemon_stop` correctly casts `pid` to `int` via `_daemon_record_read` before `os.kill`, and correctly calls `is_holder_stale` before the kill. `ProcessLookupError` is handled. `PermissionError` is not (M-01). The `is_holder_stale` probe (pid + proc_start_epoch + boot_id triple) is present and gates all kill paths.

**Correctness (Lua atomicity, two-client split, SIGTERM handler, stale recovery):** The Lua write-or-detect script is correctly structured. The two-client split (cmd_client / pubsub) correctly avoids issuing EVAL on the subscribed connection. The SIGTERM handler correctly sets `_shutdown = True`. The stale-recovery path in `_daemon_foreground_run` discards the second `_daemon_record_write` return value (H-01). The fundamental correctness defect is C-01 (parent PID in HASH).

**Resource Safety:** `_start_foreground_daemon` in tests is missing `close_fds=True` and `start_new_session=True` — the `--foreground` child inherits the test runner's file descriptors and process group. In production the detach Popen has both flags correctly set. For test isolation this is acceptable but worth noting. No pubsub connection leaks were identified in the cleanup path (lines 303–304 are reachable from both the `_shutdown` path and the `break`/SessionNotFound path).

**EM_PROJ_REDIS_DB propagation:** Correctly handled via `env=None` in `_daemon_start` (inherits parent env, which the test harness sets to db=15). Confirmed.
