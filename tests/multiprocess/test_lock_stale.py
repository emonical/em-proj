"""Multi-process stale-takeover tests for `em-proj state lock` (Plan 03-06 / D-10).

Proves that a crashed ``--hold`` process (killed via SIGKILL) leaves a stale lock
in Redis, and that the NEXT ``lock`` invocation opportunistically takes over via
the Lua compare-and-swap script (D-10 / LUA_COMPARE_AND_SWAP_IF_STALE).

SIGKILL vs. SIGINT (this file vs. test_lock_hold.py):
  test_lock_hold.py uses SIGINT — signal handlers fire, cleanup runs, lock released.
  THIS FILE uses SIGKILL — the signal CANNOT be caught by the process; atexit and
  signal handlers do NOT run; the lock is guaranteed-stale.

  SIGKILL is the only faithful way to simulate a crashed holder (kernel-killed OOM,
  hardware fault, `kill -9`). SIGINT/SIGTERM would let the process clean up, which
  exercises the normal release path, not the stale-takeover path.

Design invariants (Phase 1 RESEARCH Pitfalls, carried forward):
  - subprocess.Popen NOT multiprocessing.Process (#6 — fork+exec macOS safety)
  - .communicate(timeout=) NOT .wait() (#2 — pipe-buffer deadlock)
  - EM_PROJ_REDIS_DB=15 in child env (#4 — never writes to prod db=0)
  - NO ``import multiprocessing`` — Phase 1 pitfall #6 carry

CLI option order invariant (Warning #5 pin, iteration-2):
  em-proj state lock [--ttl N] [--hold] <name> [-- <cmd...>]
  Options BEFORE positional name BEFORE '--' separator.
  All invocations in this file follow this convention.

Import pattern (per Phase 1 Plan 04 SUMMARY):
  from tests.conftest import EM_PROJ_BIN, TEST_DB
  NOT the bare ``from conftest import …`` form.

ROADMAP Success Criterion #2 closed by test_stale_takeover_after_sigkill:
  "The stale-detection composite correctly identifies abandoned locks across
  PID reuse and reboot." End-to-end proof: SIGKILL → stale lock in Redis →
  next acquire takes over via LUA_COMPARE_AND_SWAP_IF_STALE.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time

import pytest

from tests.conftest import EM_PROJ_BIN, TEST_DB


# ---------------------------------------------------------------------------
# Test 1 — D-10: SIGKILL'd holder leaves stale lock; next acquire takes over
# ---------------------------------------------------------------------------


def test_stale_takeover_after_sigkill(clean_db) -> None:
    """Spawn `em-proj state lock --hold stale-foo --ttl 60 -- sleep 30`, SIGKILL
    the parent at t=0.5s (no cleanup runs), then immediately invoke
    `em-proj state lock --hold stale-foo --ttl 60 -- echo took-over` and assert
    it succeeds via the opportunistic Lua compare-and-swap on a dead-PID holder.

    This proves D-10's opportunistic stale-takeover on acquire ACTUALLY works
    against real Redis with a real dead process (ROADMAP SC#2).

    SIGKILL is load-bearing here: SIGKILL cannot be caught; atexit and signal
    handlers do NOT run; the lock stays in Redis as a stale artifact. This is
    the exact failure mode the stale-detection composite is designed to recover from.
    """
    env = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB)}

    # Step 1: Spawn child holding the lock for a long time
    # CLI form (Warning #5): options BEFORE positional name BEFORE '--'
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "state", "lock", "--ttl", "60", "--hold", "stale-foo", "--", "sleep", "30"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,  # Load-bearing: routes child to db=15, not prod db=0
    )

    # Step 2: Wait for the lock to land (acquire is ~50ms typically; 0.8s is generous)
    time.sleep(0.8)

    # Step 3: Confirm the lock is held by the child
    lock_raw = clean_db.get("state:lock:stale-foo")
    assert lock_raw is not None, (
        "Expected lock 'stale-foo' to be held after 0.8s; "
        "child process may not have started or acquired yet"
    )
    dead_holder = json.loads(lock_raw)
    dead_pid = dead_holder["pid"]

    # Step 4: SIGKILL the child — atexit and signal handlers do NOT run
    # The lock remains in Redis as a stale artifact (SIGKILL cannot be caught).
    os.kill(proc.pid, signal.SIGKILL)

    # Step 5: Reap the zombie (T-3-06-02: 2s reap timeout; practically instant)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        pytest.fail("SIGKILL'd process did not exit within 2s")

    # Confirm SIGKILL exit code (-9 on POSIX)
    assert proc.returncode == -signal.SIGKILL, (
        f"Expected exit code -9 (SIGKILL) but got {proc.returncode}"
    )

    # Step 6: Confirm the lock is STILL present (TTL backstop is 60s; SIGKILL bypasses cleanup)
    stale_raw = clean_db.get("state:lock:stale-foo")
    assert stale_raw is not None, (
        "Expected stale lock 'stale-foo' to still be present after SIGKILL "
        "(TTL=60s; no cleanup ran)"
    )

    # Step 7: Next acquire via opportunistic takeover
    # CLI form (Warning #5): options BEFORE positional name BEFORE '--'
    takeover = subprocess.run(
        [EM_PROJ_BIN, "state", "lock", "--ttl", "60", "--hold", "stale-foo", "--", "echo", "took-over"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert takeover.returncode == 0, (
        f"Expected takeover acquire to exit 0 (D-10 opportunistic stale-takeover); "
        f"got {takeover.returncode}\nstderr={takeover.stderr!r}\nstdout={takeover.stdout!r}"
    )
    # Verify the wrapped command actually ran
    assert "took-over" in takeover.stdout, (
        f"Expected 'took-over' in stdout (wrapped echo ran); got: {takeover.stdout!r}"
    )

    # Step 8: Lock is released after the second --hold echo exits
    final_val = clean_db.get("state:lock:stale-foo")
    assert final_val is None, (
        "Expected lock 'stale-foo' to be released after second --hold exits cleanly; "
        f"got: {final_val!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Stale-takeover does NOT displace a live holder (inverse proof)
# ---------------------------------------------------------------------------


def test_live_holder_not_displaced_as_stale(clean_db) -> None:
    """D-10 inverse: the opportunistic stale-takeover must NOT fire on a LIVE holder.

    Spawn child A holding the lock (live, with cleanup). Immediately attempt a
    second acquire via subprocess.run — it must exit 3 (held_by_another), NOT 0
    (takeover). Confirm child A's lock record is still intact.

    This proves the Lua compare-and-swap correctly refuses to fire when the holder
    is a live process (probe_pid_alive / probe_proc_start_matches / boot_id all pass).
    """
    env = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB)}

    # Spawn child A: hold the lock for 3 seconds (long enough to outlast child B's attempt)
    # CLI form (Warning #5): options BEFORE positional name BEFORE '--'
    proc_a = subprocess.Popen(
        [EM_PROJ_BIN, "state", "lock", "--hold", "live-foo", "--", "sleep", "3"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    # Wait for A to acquire
    time.sleep(0.5)

    # Confirm A holds the lock
    lock_raw_a = clean_db.get("state:lock:live-foo")
    assert lock_raw_a is not None, "Child A must have acquired the lock before child B attempts"
    holder_a_pid = json.loads(lock_raw_a)["pid"]

    # Attempt second acquire — should exit 3 (held_by_another), NOT take over
    proc_b = subprocess.run(
        [EM_PROJ_BIN, "state", "lock", "--hold", "live-foo", "--", "echo", "nope"],
        capture_output=True,
        text=True,
        timeout=5,
        env=env,
    )
    assert proc_b.returncode == 3, (
        f"Expected exit 3 (held_by_another — live holder should NOT be displaced); "
        f"got {proc_b.returncode}\nstdout={proc_b.stdout!r}\nstderr={proc_b.stderr!r}"
    )

    # Confirm child A's lock record is unchanged (still the same pid)
    lock_raw_after = clean_db.get("state:lock:live-foo")
    assert lock_raw_after is not None, "Lock must still be held by child A"
    holder_after_pid = json.loads(lock_raw_after)["pid"]
    assert holder_after_pid == holder_a_pid, (
        f"Lock must still be held by child A (pid={holder_a_pid}); "
        f"got pid={holder_after_pid} after child B's failed attempt"
    )

    # Wait for child A to finish naturally
    try:
        stdout_a, stderr_a = proc_a.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc_a.kill()
        proc_a.communicate()
        pytest.fail("Child A (lock --hold sleep 3) did not complete within 5s")

    assert proc_a.returncode == 0, (
        f"Expected child A to exit 0 after sleep 3; got {proc_a.returncode}"
    )

    # Lock released after A exits naturally
    assert clean_db.get("state:lock:live-foo") is None, (
        "Expected lock 'live-foo' to be released after child A exits cleanly"
    )
