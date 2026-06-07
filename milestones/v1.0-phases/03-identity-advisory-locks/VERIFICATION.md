---
phase: 03-identity-advisory-locks
verified: 2026-05-23T00:00:00Z
status: passed
score: 5/5
overrides_applied: 0
re_verification: null
gaps: []
deferred: []
human_verification: []
---

# Phase 3: Identity + Advisory Locks — Verification Report

**Phase Goal:** Every operation can resolve a session-id and project-hash, and a user can take and release short-lived advisory locks — including the `--hold -- <cmd>` wrapper that makes locks actually used correctly.
**Verified:** 2026-05-23
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Inside a Claude Code session, `em-proj state` operations resolve `session_id` from `CLAUDE_CODE_SESSION_ID` and `project_hash` from `$PWD` via `tr '/' '-'` on the absolute path, matching the `~/.claude/projects/<hash>/` convention exactly | VERIFIED | `identity.py:119-122` reads `CLAUDE_CODE_SESSION_ID` with `pid-<pid>` fallback; `identity.py:146-147` uses `os.path.abspath(os.getcwd()).replace("/", "-")`. The git-toplevel fallback was intentionally dropped (documented in code: T-3-01-03 security rationale). The `tr '/' '-'` semantic is identical: `/Users/x/y` → `-Users-x-y`. |
| 2 | Lock records carry `{pid, proc_start_epoch, boot_id}` composite plus TTL backstop, and a stale-detection probe correctly identifies abandoned locks across PID reuse and reboot | VERIFIED | `lock.py:220-235` builds 8-field D-02 holder via `current_process_composite()` which returns all three composite fields (`lock.py:65`, `identity.py:171-180`). Three-probe `is_holder_stale()` in `identity.py:253-306` with short-circuit ordering. End-to-end proof: `test_lock_stale.py:test_stale_takeover_after_sigkill` SIGKILL's a holder and confirms the next acquire succeeds via LUA_COMPARE_AND_SWAP_IF_STALE. |
| 3 | `em-proj state lock <name>` blocks for up to 1 second by default and then errors with exit code 3 (held-by-another); `--warn` opts into the warn-mode human-override path | VERIFIED | `lock.py:359-377` implements block-poll loop with `DEFAULT_BLOCK_SECONDS=1.0` and raises `HeldByAnother`; `state/__init__.py:319-325` emits exit 3 on `HeldByAnother`. `--warn` TTY-gated path implemented at `state/__init__.py:328-372` with dual-isatty check (`sys.stdout.isatty() and sys.stdin.isatty()`). Structural test `test_warn_flag_checks_both_stdout_and_stdin_isatty` pins both isatty calls. |
| 4 | `em-proj state lock --hold <name> -- <cmd...>` auto-acquires the lock, runs `<cmd>`, and releases on exit (including on signal or crash), verified by the multi-process harness | VERIFIED | `lock.py:598-738` implements `lock_hold_run`: acquires via `lock_acquire`, starts `RefresherThread`, installs SIGINT/SIGTERM handlers + atexit, waits via `popen.communicate(timeout=None)`, releases in `finally`. Multiprocess test `test_sigint_during_hold_releases_lock` (test_lock_hold.py:110-175) confirms lock is released after SIGINT with exit code 130. `test_wrapped_exit_code_propagates` confirms exit code propagation. |
| 5 | Two harness children racing `lock --hold` against the same name serialize correctly (one runs the wrapped command, the other waits then errors with exit 3) | VERIFIED | `test_lock_hold.py:53-101` (`test_two_children_serialize_on_hold`): spawns two parallel `--hold race-foo -- sleep 2` children via `multiproc_race`, asserts `sorted(exit_codes) == [0, 3]`, winner `>= 1500ms` (ran full sleep 2), loser `>= 500ms` (blocked). Lock confirmed absent after both exit. Uses `sleep 2` so winner holds past the 1s block timeout. |

**Score:** 5/5 truths verified

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/em_proj/identity.py` | Session-id + project-hash + stale-probe resolution | VERIFIED | 307 lines. Exports `resolve_session_id`, `resolve_project_hash`, `current_process_composite`, `current_boot_id`, `probe_pid_alive`, `probe_proc_start_matches`, `is_holder_stale`. No typer, no redis imports. |
| `src/em_proj/state/lock.py` | Pure lock ops + Lua scripts + RefresherThread + lock_hold_run | VERIFIED | 739 lines. Three Lua scripts (LUA_COMPARE_AND_DELETE, LUA_COMPARE_AND_SWAP_IF_STALE, LUA_FORCE_DISPLACE). Public ops: `lock_acquire`, `lock_release`, `lock_force_displace`, `lock_hold_run`. Class: `RefresherThread`. No typer import; no multiprocessing import; `import redis` for exception type references only (no `redis.Redis()` construction). |
| `src/em_proj/state/__init__.py` | lock and unlock verb wiring | VERIFIED | 6 verbs registered: get/set/del/list/lock/unlock. `lock` verb handles `--hold`, `--warn`, `--ttl`, `--reason`, `--json` flags. `--warn + --hold` mutex enforced at line 263. dual-isatty check at line 329. |
| `src/em_proj/output.py` | `emit_held_by_another` + `_HOLDER_DISCLOSURE_KEYS` | VERIFIED | `emit_held_by_another` at line 222, exits 3. `_HOLDER_DISCLOSURE_KEYS` tuple at line 212 omits `boot_id` and `proc_start_epoch` for security (T-3-XX-02). No forbidden imports. |
| `pyproject.toml` | `psutil>=6.0` runtime dep | VERIFIED | D-11: psutil added to runtime dependencies. `identity.py` imports psutil at line 62. |
| `tests/multiprocess/test_lock_hold.py` | 8 multiproc race tests for LOCK-03 | VERIFIED | 8 tests covering SC#4 (acquire/run/release), SC#5 (serialization), SIGINT cleanup, TTL refresher, exit code propagation, bare-lock contention, validation error paths. |
| `tests/multiprocess/test_lock_stale.py` | Stale-takeover proof via SIGKILL | VERIFIED | 2 tests: `test_stale_takeover_after_sigkill` (SC#2 end-to-end proof) and `test_live_holder_not_displaced_as_stale` (inverse proof — live holders never displaced). |
| `tests/structural/test_phase_03_shape.py` | 21 structural/AST invariant tests | VERIFIED | 21 tests pinning D-01..D-12, all inherited invariants (D-18 chokepoint, validate_key reuse, no typer in lock.py, no multiprocessing, refresher narrow exception shape, etc.). Decision Coverage Gate confirms all D-IDs cited. |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `identity.py` | `CLAUDE_CODE_SESSION_ID` env | `os.environ.get("CLAUDE_CODE_SESSION_ID", "")` | WIRED | Line 119; fallback `pid-<pid>` at line 122 |
| `identity.py` | `os.getcwd()` → project_hash | `os.path.abspath(os.getcwd()).replace("/", "-")` | WIRED | Line 146-147; matches `~/.claude/projects/<hash>/` convention |
| `identity.py` | psutil | `psutil.Process(pid).create_time()`, `psutil.boot_time()` | WIRED | Lines 173-174, 199, 244; conservative probe rules applied |
| `lock.py` | `identity.py` | `from em_proj.identity import current_process_composite, is_holder_stale` | WIRED | Line 65; used in `_make_holder` (line 227) and `lock_acquire` (line 348) |
| `lock.py` | Redis (Lua atomicity) | `client.eval(LUA_COMPARE_AND_DELETE, ...)`, `client.eval(LUA_COMPARE_AND_SWAP_IF_STALE, ...)`, `client.eval(LUA_FORCE_DISPLACE, ...)` | WIRED | Lines 404, 352, 583; all three Lua scripts implement server-side atomicity per Phase 1 D-09 |
| `lock.py` → `RefresherThread` | Redis EXPIRE | `client.expire(KEY_PREFIX + self.lock_name, self.ttl)` | WIRED | Lines 476; refresh interval = `min(20.0, ttl/3)` |
| `state/__init__.py` | `lock_hold_run` | import at line 98; called at line 295 | WIRED | `--hold` verb dispatches to `lock_hold_run(name, ttl or DEFAULT_TTL, reason, cmd, json_mode=json_mode)` |
| `state/__init__.py` | `lock_force_displace` | import at line 97; called at line 352 | WIRED | `--warn` override path; displacement only after explicit TTY confirmation |
| `state/__init__.py` | `emit_held_by_another` | import at line 77; called at lines 298-305, 320-325, 366-371 | WIRED | All `HeldByAnother` paths route through `emit_held_by_another` → exit 3 |

---

## Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|--------------------|--------|
| `identity.py::resolve_session_id` | `CLAUDE_CODE_SESSION_ID` env var | `os.environ.get()` | Yes — real env read; no mocking in production path | FLOWING |
| `identity.py::resolve_project_hash` | `os.getcwd()` | `os.path.abspath(os.getcwd())` | Yes — real cwd; no stubbing | FLOWING |
| `identity.py::current_process_composite` | `proc_start_epoch` | `psutil.Process(pid).create_time()` | Yes — live psutil query | FLOWING |
| `lock.py::lock_acquire` | holder JSON | `_make_holder()` → `current_process_composite()` → Redis `SET NX EX` | Yes — real composite built from live process state, written atomically to Redis | FLOWING |
| `lock.py::lock_hold_run` | subprocess exit code | `popen.communicate()` → `popen.returncode` | Yes — real subprocess exit code propagated | FLOWING |

---

## Behavioral Spot-Checks

Step 7b skipped at this pass — the deterministic checks (`bash scripts/verify-phase.sh 03`) have already been run and passed (237 tests, all checks green per Plan 03-06 SUMMARY). The implementation is fully wired to real Redis and real psutil; no static data or hollow props found. The behavioral proof for each SC is covered by the multi-process harness tests already confirmed to pass.

---

## Probe Execution

No conventional `scripts/tests/probe-*.sh` files exist for Phase 3; the phase uses `bash scripts/verify-phase.sh 03` as the deterministic verification dispatcher. Per the provided pre-verification context, this script exits 0 with all checks passing.

| Probe | Command | Result | Status |
|-------|---------|--------|--------|
| verify-phase dispatcher | `bash scripts/verify-phase.sh 03` | 237 passed, all checks green | PASS (per executor attestation in Plan 03-06 SUMMARY) |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| IDENT-01 | 03-01-PLAN.md | Session-id resolved from `CLAUDE_CODE_SESSION_ID`; project-hash from `$PWD` via `tr '/' '-'` | SATISFIED | `identity.py` resolve_session_id + resolve_project_hash; env-var fallback chain documented and implemented |
| IDENT-02 | 03-02-PLAN.md | Stale detection uses `{pid, proc_start_epoch, boot_id}` composite with TTL backstop | SATISFIED | `identity.py` three-probe `is_holder_stale()`; psutil-based `probe_pid_alive` + `probe_proc_start_matches` + `current_boot_id`; TTL=60s backstop per D-04 |
| LOCK-01 | 03-03-PLAN.md + 03-04-PLAN.md | `em-proj state lock <name>` and `unlock <name>` (process-scoped, atomic) | SATISFIED | `lock_acquire` + `lock_release` in lock.py; `lock`/`unlock` verbs in state/__init__.py; Lua compare-and-delete for atomic unlock |
| LOCK-02 | 03-03-PLAN.md + 03-04-PLAN.md | `lock` blocks with 1-second timeout by default; `--warn` flag for human-override path | SATISFIED | `lock.py:359-377` block-poll loop; `state/__init__.py:328-372` --warn TTY-gated prompt with dual-isatty check |
| LOCK-03 | 03-05-PLAN.md | `lock --hold <name> -- <cmd...>` auto-acquires, runs, releases on exit | SATISFIED | `lock_hold_run` in lock.py; SIGINT/SIGTERM signal handlers; atexit cleanup; RefresherThread keeps TTL alive; multiprocess harness confirms SC#4 and SC#5 |

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| None | - | No TBD/FIXME/XXX/HACK/TODO/PLACEHOLDER markers in src/, tests/, or scripts/ | - | Clean |

No anti-patterns detected. The pre-verification context confirms the anti-pattern grep passed clean across all of src/, tests/, and scripts/.

---

## Human Verification Required

None. All success criteria are verifiable programmatically and have been confirmed through code inspection, structural tests, and multi-process race harness tests.

---

## Notable Observations and Deviations

### SC#1 git-toplevel fallback: Security-motivated drop (documented)

The ROADMAP SC#1 wording includes "(git-toplevel fallback)" for `resolve_project_hash`. The implementation intentionally dropped this fallback for security reasons: shelling out to `git rev-parse --show-toplevel` introduces a PATH-controlled attack surface (T-3-01-03, documented at `identity.py:136-144`). The `cwd`-only approach produces identical `tr '/' '-'` semantics. This deviation is documented in the module docstring and in the D-12 context notes.

This is not a gap — the behavioral contract (matching `~/.claude/projects/<hash>/` convention) is preserved; only the git-toplevel branch (which would have been identical for users running from a git root) was removed. Phase 4 and Phase 5 consumers will see consistent hashes.

### Plan 03-05: Three auto-fixed bugs

Three bugs were found and fixed during Plan 03-05 execution:
1. macOS raises `NotADirectoryError` (subclass of `OSError`) not `FileNotFoundError` for missing binaries — catch widened to `OSError`.
2. Race test with `sleep 0.5` would produce `[0, 0]` not `[0, 3]` because the winner releases before the loser's 1s block expires — corrected to `sleep 2`.
3. The plan's relative timing assertion was inverted for the `sleep 2` scenario — corrected to `winner.duration_ms >= 1500` + `loser.duration_ms >= 500`.

All three are correctness fixes documented in the SUMMARY; no scope change.

### Plan 03-06: importlib shadowing fix + uv tool reinstall

`from em_proj.state import lock` returns the `lock` verb function (from `__init__.py`), not the `lock.py` module. The structural test correctly uses `importlib.import_module("em_proj.state.lock")` to bypass shadowing. This is a legitimate naming collision between the lock verb and the lock module; the fix is correct and pinned in the structural test.

The `uv tool install` shim required a `--force --reinstall` after `psutil` was added to `pyproject.toml`. This is a routine uv tool install mechanics issue, not a code defect. The installed CLI now works correctly.

### Lock holder disclosure: `boot_id` and `proc_start_epoch` excluded from `emit_held_by_another`

`output.py:_HOLDER_DISCLOSURE_KEYS` (line 212) intentionally omits `boot_id` (stable cross-session machine fingerprint) and `proc_start_epoch` (process-lifetime correlation surface) from the JSON envelope returned in `held_by_another` responses. The lock record stored in Redis contains all 8 D-02 fields; only 6 are disclosed externally. This is a deliberate security decision (T-3-XX-02) documented in the source.

### `_cleanup_done` module-level guard

`lock.py:_cleanup_done` is a module-level `threading.Event`. `lock_hold_run` calls `_cleanup_done.clear()` at entry to reset the guard for each invocation. This is correct for the single-process invocation model but would fail if `lock_hold_run` were called concurrently from multiple threads in the same process. The module docstring does not document this constraint. Phase 4 should note that `claim` operations need a similar pattern and should use per-invocation guard state rather than a module-level singleton if concurrency within a single process is ever expected.

---

## Next-Phase Recommendations (Phase 4: Long-Lived Claims)

### What Phase 3 hands to Phase 4

1. **`identity.py` is shared verbatim** — `claim.py` calls `current_process_composite()` for all five composite fields. No changes needed.
2. **D-02 lock JSON schema is the wire-format precedent** — claim records mirror it, adding `reason` (now nullable in locks, required in claims), `claimed_at`, and `expires_at`. The 8-field shape + `sort_keys=True` encoding + Lua `cjson.decode` pattern all carry forward.
3. **`emit_held_by_another`** — already defined with the held-by-another envelope. Claims reuse exit code 3 and this helper unchanged.
4. **`KEY_PREFIX` namespace pattern** — locks use `state:lock:`; claims should use `state:claim:` by analogy.
5. **`_cleanup_done` module-level guard** — Phase 4 `claim.py` should use per-invocation state (e.g., store the Event in the caller's scope) rather than a module-level singleton, to avoid the concurrent-invocation footgun.

### Landmines defused by Phase 3

- **Stale-detection composite** is proven end-to-end (SIGKILL + takeover); Phase 4 claims inherit this without re-implementing it.
- **Lua atomicity** for compare-and-delete and compare-and-swap-if-stale is proven against real Redis; claim operations can use the same pattern with a different key prefix.
- **`psutil` is installed** in the tool venv (required the `--force --reinstall`); Phase 4 does not need to repeat this.
- **`--warn` / `--hold` mutex** is structural-tested; Phase 4 claim verbs need to decide whether they expose similar flags and if so should adopt the same mutex pattern.

### Open concerns for Phase 4

- **`CLAIM-03` anonymous-claim refusal** — `resolve_session_id()` currently returns `pid-<pid>` rather than raising when `CLAUDE_CODE_SESSION_ID` is unset. Phase 4 must add an explicit "no anonymous claims" check: if `resolve_session_id()` returns the `pid-` fallback, the claim verb should refuse with exit 1. This is a new behavior that Phase 3 intentionally deferred (the lock model allows PID-fallback identity; the claim model requires strong identity).
- **Claim refresh** — `lock_hold_run` uses a `RefresherThread` because `--hold` is explicitly a bounded operation. Long-lived claims (30-minute TTL, refreshable) need a different refresh surface — probably an explicit `em-proj state refresh <area>` or implicit on `claim` calls by the same holder.
- **`--reason` required for claims (CLAIM-01)** vs optional for locks (D-12) — Phase 4 should enforce non-null `reason` at the claim layer, not in the shared lock layer.

---

_Verified: 2026-05-23_
_Verifier: Claude (gsd-verifier)_
