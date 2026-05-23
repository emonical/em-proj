---
phase: 03-identity-advisory-locks
plan: 02
subsystem: identity
tags: [identity, psutil, stale-detection, probe, pid-reuse, reboot, IDENT-02]

# Dependency graph
requires:
  - phase: 03-identity-advisory-locks/01
    provides: identity.py with _boot_id() helper, psutil installed (D-11)
provides:
  - src/em_proj/identity.py — four new probe functions + PROC_START_EPSILON constant
  - tests/unit/test_stale_probe.py — 17 tests covering all D-10 probe branches
affects:
  - Phase 3 Plan 03-03 (lock.py stale-takeover calls is_holder_stale before Lua CAS)
  - Phase 4 claim.py (same probe primitive for claim staleness detection)
  - Phase 5 /global-state skill (stale probe reused for locks --stale filtering)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Conservative-probe principle: AccessDenied -> live signal (T-3-XX-06); false-negatives recoverable, false-positives are corruption"
    - "Three-probe short-circuit sequence (D-10): pid alive -> proc_start match -> boot_id match; first stale signal short-circuits"
    - "PROC_START_EPSILON = 0.5s (T-3-02-06): above clock-jitter noise, below realistic PID-reuse gap"
    - "Monkeypatch psutil.Process via callable factory for deterministic exception injection"

key-files:
  created:
    - tests/unit/test_stale_probe.py (268 lines, 17 tests)
  modified:
    - src/em_proj/identity.py (extended from 151 -> 306 lines, 158 insertions)

key-decisions:
  - "PROC_START_EPSILON = 0.5s — floor above clock-jitter (nanoseconds), ceiling below realistic PID-reuse gap (seconds); documents T-3-02-06 in the constant definition"
  - "AccessDenied -> True in both probe_pid_alive and probe_proc_start_matches — conservative-probe principle: cannot-read = assume-live; false-negatives recoverable via TTL backstop and Phase 5 unlock --force"
  - "is_holder_stale raises ValueError on malformed input — not a runtime-flow error; malformed holder is a caller bug that should surface during development (T-3-02-04: no pid value in error message)"
  - "Short-circuit ordering: probe_pid_alive first (cheap OS lookup); skip proc_start probe on dead PID to save one extra psutil.Process().create_time() call under contention"

# Metrics
duration: ~12min
completed: 2026-05-23
---

# Phase 03 Plan 02: Stale-Detection Probe Primitives Summary

**Four probe helpers added to `em_proj/identity.py`; 17-test suite pins every D-10 branch including conservative-probe AccessDenied invariant (IDENT-02 primitive layer complete).**

## Performance

- **Duration:** ~12 min
- **Completed:** 2026-05-23
- **Tasks completed:** 2 of 2

## Accomplishments

### Task 1: Stale-detection probe helpers in identity.py

Extended `src/em_proj/identity.py` (Plan 03-01 base: 151 lines) with four new functions and one module constant. No existing Plan 03-01 functions were modified.

**New module constant:**
```python
PROC_START_EPSILON: float = 0.5  # seconds; tolerance for psutil create_time() vs proc_start_epoch
```

**New public functions:**

```python
def current_boot_id() -> str:
    # Thin wrapper: _boot_id(psutil.boot_time())
    # Stable within a boot; used by is_holder_stale for probe 3.

def probe_pid_alive(pid: int) -> bool:
    # NoSuchProcess -> False; AccessDenied -> True (conservative); no exception -> True.

def probe_proc_start_matches(pid: int, expected_start_epoch: float) -> bool:
    # abs(actual - expected) < PROC_START_EPSILON -> True
    # NoSuchProcess -> False; AccessDenied -> True (conservative)

def is_holder_stale(holder: dict) -> bool:
    # Validates required keys (pid, proc_start_epoch, boot_id); raises ValueError on malformed input.
    # Short-circuit: probe_pid_alive -> probe_proc_start_matches -> boot_id compare.
    # Returns True on first stale signal; False only when all three pass.
```

**Final PROC_START_EPSILON value and rationale:**
0.5 seconds. Clock jitter between `time.time()` and `psutil.Process().create_time()` is nanoseconds in practice. 0.5s is well above this noise floor and well below any realistic process-start interval that would produce a false "same process" match. Documents T-3-02-06 (clock-skew threat mitigation) directly in the constant definition comment.

**AccessDenied disposition:**
Both `probe_pid_alive` and `probe_proc_start_matches` return `True` on `psutil.AccessDenied`. This implements the conservative-probe principle (T-3-XX-06 / T-3-02-01): when we cannot read a process, we assume it is live rather than stale. False-negatives (missing a stale holder for one acquire cycle) are recoverable — the 60s TTL backstop (D-04) or Phase 5's `unlock --force` handles them. False-positives (displacing a live holder we can't read) are data corruption and must never happen.

**Invariants upheld (carry-forwards from Plan 03-01):**
- No `import typer` — D-17
- No redis imports — identity.py remains Redis-free (D-18 / D-12)

### Task 2: Unit tests for stale-detection probe

Created `tests/unit/test_stale_probe.py` (268 lines, 17 tests). No Redis fixtures.

**Branch coverage matrix:**

| Branch | Test | Signal |
|--------|------|--------|
| probe_pid_alive: live PID | `test_probe_pid_alive_live_pid` | Real `os.getpid()` |
| probe_pid_alive: NoSuchProcess | `test_probe_pid_alive_dead_pid` | False |
| probe_pid_alive: AccessDenied | `test_probe_pid_alive_access_denied` | True (conservative) |
| probe_proc_start_matches: exact match | `test_probe_proc_start_matches_exact` | True |
| probe_proc_start_matches: within epsilon | `test_probe_proc_start_matches_within_epsilon` | True |
| probe_proc_start_matches: mismatch (PID reuse) | `test_probe_proc_start_matches_mismatch` | False |
| probe_proc_start_matches: NoSuchProcess | `test_probe_proc_start_matches_no_such` | False |
| probe_proc_start_matches: AccessDenied | `test_probe_proc_start_matches_access_denied` | True (conservative) |
| current_boot_id: stability | `test_current_boot_id_stable` | Same 16-hex string |
| current_boot_id: derivation independence | `test_current_boot_id_varies_with_boot_time` | Different string for different boot_time |
| is_holder_stale: all probes pass | `test_is_holder_stale_live` | False (real process) |
| is_holder_stale: dead PID | `test_is_holder_stale_dead_pid` | True |
| is_holder_stale: PID reuse | `test_is_holder_stale_pid_reuse` | True |
| is_holder_stale: reboot | `test_is_holder_stale_reboot` | True |
| is_holder_stale: missing key | `test_is_holder_stale_malformed_missing_key` | ValueError |
| is_holder_stale: non-int pid | `test_is_holder_stale_malformed_pid_type` | ValueError |
| is_holder_stale: short-circuit | `test_is_holder_stale_short_circuit` | call_count == 1 |

**Monkeypatch strategy:** `_FakeProcessFactory` is a callable class that tracks `call_count` and can raise a specified exception. Passed via `monkeypatch.setattr(identity_mod.psutil, "Process", factory)` — patches the psutil module reference inside `em_proj.identity` rather than the global psutil module, ensuring the patch is scoped correctly.

**Real-OS path exercised:** `test_probe_pid_alive_live_pid`, `test_is_holder_stale_live`, and `test_is_holder_stale_reboot` use the actual `os.getpid()` + `psutil.Process()` path without mocking.

**psutil version quirks:** No version-specific quirks observed during testing. psutil 7.2.2 on macOS Darwin 24.1.0. `psutil.Process(pid)` construction is sufficient for liveness probing — consistent with documentation and the plan's spec.

## Task Commits

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Stale-detection probe helpers | 81c094d | src/em_proj/identity.py |
| 2 | Stale-probe unit tests | a0f8845 | tests/unit/test_stale_probe.py |

## Verification Results

```
bash scripts/test.sh unit -k test_stale_probe
  17 passed, 97 deselected in 0.02s

bash scripts/test.sh unit -k test_identity
  10 passed, 104 deselected in 0.02s  (no regressions to Plan 03-01 tests)

bash scripts/test.sh unit
  114 passed in 1.66s  (17 new + 97 prior — no regressions)

python -c "from em_proj.identity import is_holder_stale, current_process_composite; h = current_process_composite(); assert is_holder_stale(h) is False"
  ok - current process is not stale

grep -cE 'psutil.(NoSuchProcess|AccessDenied)' src/em_proj/identity.py
  10  (>= 2 required; each exception handled in both probe functions)
```

## Deviations from Plan

None — plan executed exactly as written. All four probe functions implemented per spec; all 17 test branches covered; conservative-probe principle applied consistently; no typer or Redis imports introduced.

## Known Stubs

None — all probe functions call live OS / psutil APIs. No placeholders or hardcoded return values in production code.

## Threat Surface Audit

No new threat surface beyond what was modeled in the plan's `<threat_model>`. The probe functions are read-only OS queries:
- T-3-02-01 (false-positive stale) — mitigated by AccessDenied -> live signal in both probes
- T-3-02-04 (error message information disclosure) — mitigated: `ValueError` names only the missing key, not any pid value

## Requirements Completed

- **IDENT-02** (primitive layer) — stale-detection composite probe implemented with correct three-probe sequence (D-10 step 1). Plan 03-03's Lua compare-and-swap (D-10 step 2) has a working `is_holder_stale` to call before issuing the swap.

## Next Plan Readiness

**Plan 03-03 (lock.py + CLI wiring) is unblocked:**
- `em_proj.identity.is_holder_stale(holder)` is available and tested
- `current_boot_id()` is available for lock.py to include in holder JSON (D-02) and compare on acquire
- All three probe helpers are independently testable — lock.py tests can mock `is_holder_stale` at the boundary

## Self-Check: PASSED

- `src/em_proj/identity.py` — FOUND (306 lines)
- `tests/unit/test_stale_probe.py` — FOUND (268 lines, 17 tests)
- Task 1 commit `81c094d` — FOUND
- Task 2 commit `a0f8845` — FOUND
- `bash scripts/test.sh unit -k test_stale_probe` — 17 passed
- `bash scripts/test.sh unit` — 114 passed (no regressions)
