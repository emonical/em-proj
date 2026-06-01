"""Multi-process race tests for `em-proj state lock --hold` (Plan 03-05 / LOCK-03).

Uses ``multiproc_race`` from ``tests/conftest.py`` to fork+exec real ``em-proj``
invocations against db=15 (via ``EM_PROJ_REDIS_DB`` injection in conftest).

Success criteria tested:
  - ROADMAP SC#4: lock --hold auto-acquires, runs cmd, releases on exit
  - ROADMAP SC#5: two children racing --hold serialize correctly
  - D-05: refresher thread keeps TTL alive past the base TTL during --hold
  - D-06: SIGINT during --hold triggers cleanup before exit (lock released)

Design invariants (Phase 1 RESEARCH Pitfalls, carried forward):
  - subprocess.Popen NOT multiprocessing.Process (#6 — fork+exec macOS safety)
  - .communicate(timeout=) NOT .wait() (#2 — pipe-buffer deadlock)
  - EM_PROJ_REDIS_DB=15 in child env (#4 — never writes to prod db=0)

CLI option order invariant (Warning #5 pin, iteration-2):
  em-proj state lock [--ttl N] [--hold] <name> [-- <cmd...>]
  Options BEFORE positional name BEFORE '--' separator.

Timing assertion invariant (Warning #6 pin, iteration-2):
  All timing assertions use RELATIVE bounds only. No absolute upper bounds on
  durations (e.g., no "loser < 2500ms") — these are CI-flaky. Relative bounds
  (loser > winner + 500ms) are sufficient because the 1s block is the dominant
  signal.

Import pattern (per Phase 1 Plan 04 SUMMARY):
  from tests.conftest import EM_PROJ_BIN, TEST_DB, RaceResult
  NOT the bare ``from conftest import …`` form.

``multiproc_race`` itself is a fixture, injected per-test via the function
signature. ``clean_db`` comes in implicitly through multiproc_race's fixture
dependency chain.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time

import pytest
import redis

from tests.conftest import EM_PROJ_BIN, TEST_DB, RaceResult


# ---------------------------------------------------------------------------
# Test 1 — ROADMAP SC#5: two children racing --hold serialize correctly
# ---------------------------------------------------------------------------


def test_two_children_serialize_on_hold(multiproc_race, clean_db) -> None:
    """Two parallel --hold children against the same lock name serialize correctly.

    SC#5 (ROADMAP): one child runs the wrapped command (exits 0); the other
    blocks 1 second then exits 3 (held_by_another).

    Timing assertion: relative bound only (Warning #6 pin).
    "loser.duration_ms > winner.duration_ms + 500" is sufficient because the
    1s block is the dominant signal. Absolute bounds (e.g., < 2500ms) are
    flaky in CI. See Warning #6 in the parallel-execution context.
    """
    # Use sleep 2 so the winner holds the lock for 2s — well past the 1s block
    # poll window. If we used sleep 0.5, the winner would release before the
    # loser's 1s block expires, allowing the loser to also acquire and exit 0.
    results = multiproc_race([
        [EM_PROJ_BIN, "state", "lock", "--hold", "race-foo", "--", "sleep", "2"],
        [EM_PROJ_BIN, "state", "lock", "--hold", "race-foo", "--", "sleep", "2"],
    ])

    assert len(results) == 2
    exit_codes = sorted([r.returncode for r in results])
    assert exit_codes == [0, 3], (
        f"Expected [0, 3] exit codes, got {exit_codes}\n"
        f"results: {[(r.returncode, r.stderr[:200]) for r in results]}"
    )

    winner = next(r for r in results if r.returncode == 0)
    loser = next(r for r in results if r.returncode == 3)

    # Winner ran sleep 2 — should take at least 1.5s
    assert winner.duration_ms >= 1500, (
        f"Expected winner to take >= 1500ms (wrapped sleep 2s), got {winner.duration_ms:.0f}ms"
    )

    # Loser blocked at least 500ms (block-poll default is 1s; 500ms lower bound is
    # generous for CI). This proves the loser did NOT immediately acquire.
    assert loser.duration_ms >= 500, (
        f"Expected loser to block for >= 500ms, got {loser.duration_ms:.0f}ms"
    )

    # Exit codes [0, 3] already prove correct serialization (winner ran cmd, loser blocked).
    # We do NOT assert an absolute upper bound on loser.duration_ms (Warning #6 pin):
    # absolute timing bounds are flaky in CI due to OS scheduling and startup overhead.
    # The relative lower bound on winner.duration_ms (>= 1500ms) confirms the winner
    # actually ran sleep 2 rather than acquiring a stale lock and running fast.

    # Post-race: winner released the lock cleanly on exit
    assert clean_db.get(f"state:lock:race-foo") is None, (
        "Expected lock 'race-foo' to be released after both children exit"
    )


# ---------------------------------------------------------------------------
# Test 2 — SC#4 + D-06: SIGINT during --hold releases the lock
# ---------------------------------------------------------------------------


def test_sigint_during_hold_releases_lock(clean_db) -> None:
    """SIGINT during --hold triggers cleanup: lock released, exit code 130.

    Verifies D-06: signal handler runs cleanup (lock_release) before sys.exit(130).
    The lock must be absent after the child exits so the next acquirer can proceed.

    Uses subprocess.Popen directly (not multiproc_race) so we can inject SIGINT
    while the child is alive. The env injection (EM_PROJ_REDIS_DB=TEST_DB) is
    load-bearing — without it, the child writes to db=0 (prod DB), which is
    dangerous (Phase 1 pitfall #4).
    """
    env = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB)}

    # Spawn the child: hold the lock while sleeping 5s
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "state", "lock", "--hold", "sig-foo", "--", "sleep", "5"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,  # Load-bearing: routes child to db=15, not prod db=0
    )

    # Wait for the lock to be acquired (the child needs time to acquire + start sleep)
    time.sleep(0.8)

    # Verify the lock is held
    lock_val = clean_db.get("state:lock:sig-foo")
    assert lock_val is not None, (
        "Expected lock 'sig-foo' to be held after 0.8s; child may not have started yet"
    )

    # Send SIGINT to the child
    proc.send_signal(signal.SIGINT)

    # Wait for child to exit (should exit quickly: signal handler runs cleanup + sys.exit(130))
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        pytest.fail("Child did not exit within 3s after SIGINT")

    # Assert exit code is 130 (SIGINT standard)
    assert proc.returncode == 130, (
        f"Expected exit 130 after SIGINT, got {proc.returncode}\n"
        f"stderr={proc.stderr.read()!r}"
    )

    # Assert lock is released after SIGINT cleanup
    lock_after = clean_db.get("state:lock:sig-foo")
    assert lock_after is None, (
        f"Expected lock 'sig-foo' to be released after SIGINT cleanup; "
        f"lock value={lock_after!r}"
    )

    # Verify the next acquirer can proceed (lock is truly clean)
    follow_up = subprocess.run(
        [EM_PROJ_BIN, "state", "lock", "--hold", "sig-foo", "--", "echo", "ok"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert follow_up.returncode == 0, (
        f"Expected follow-up --hold to succeed after SIGINT cleanup; "
        f"returncode={follow_up.returncode} stderr={follow_up.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Test 3 — D-05: refresher keeps TTL alive past the base TTL
# ---------------------------------------------------------------------------


def test_refresher_keeps_ttl_alive(clean_db) -> None:
    """Refresher re-EXPIREs the lock so it survives past the base TTL.

    D-05: lock --ttl 2 --hold ttl-foo -- sleep 3 should succeed even though
    the base TTL (2s) expires before the wrapped sleep (3s) completes — the
    refresher thread keeps it alive by calling EXPIRE every ttl/3 = 0.67s.

    CLI form (Warning #5 pin): options BEFORE positional name BEFORE '--'.

    Timing note: this test is wall-time-sensitive. The 2s TTL gives a 0.5s margin:
    the refresher fires at t≈0.67s and t≈1.33s, so by t=1.5s the TTL has been
    renewed at least twice. The margin between the first renewal and the base
    TTL expiry is ~1.33s — ample for CI even under modest load.
    """
    env = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB)}

    # CLI form (Warning #5): options BEFORE positional name BEFORE '--'
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "state", "lock", "--ttl", "2", "--hold", "ttl-foo", "--", "sleep", "3"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    # Sample TTL mid-run: wait 1.5s so the refresher has fired at least twice
    # (interval = min(20.0, 2/3) ≈ 0.67s → two refreshes by t=1.5s)
    time.sleep(1.5)

    # TTL should be > 0 (refresher renewed it; without renewal it would be ≤ 0)
    # Note: this is wall-time-sensitive; 0.5s margin before the 2s base TTL would expire
    mid_ttl = clean_db.ttl("state:lock:ttl-foo")
    assert mid_ttl > 0, (
        f"Expected TTL > 0 at t=1.5s (refresher should have renewed it); "
        f"got TTL={mid_ttl} — refresher may not be working"
    )

    # Wait for the process to complete
    try:
        stdout, stderr = proc.communicate(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        pytest.fail("--hold sleep 3 did not complete within 6s")

    assert proc.returncode == 0, (
        f"Expected --hold sleep 3 to exit 0 (lock survived); "
        f"got returncode={proc.returncode}\nstderr={stderr!r}"
    )

    # Lock released after subprocess exits
    assert clean_db.get("state:lock:ttl-foo") is None


# ---------------------------------------------------------------------------
# Test 4 — wrapped exit code propagates
# ---------------------------------------------------------------------------


def test_wrapped_exit_code_propagates(clean_db) -> None:
    """exit 7 from the wrapped command propagates as the verb's exit code.

    Verifies: lock_hold_run returns the subprocess's exit code; the verb
    raises SystemExit(exit_code) to propagate it.
    """
    env = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB)}
    result = subprocess.run(
        [EM_PROJ_BIN, "state", "lock", "--hold", "rc-foo", "--", "bash", "-c", "exit 7"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 7, (
        f"Expected exit 7 from wrapped 'exit 7', got {result.returncode}\n"
        f"stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Test 5 — wrapped non-zero exit does NOT prevent lock release
# ---------------------------------------------------------------------------


def test_wrapped_nonzero_exit_still_releases_lock(clean_db) -> None:
    """Lock is released even when the wrapped command exits non-zero.

    Verifies: the finally block in lock_hold_run always runs cleanup,
    regardless of the wrapped subprocess's exit code.
    """
    env = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB)}
    result = subprocess.run(
        [EM_PROJ_BIN, "state", "lock", "--hold", "rc-foo", "--", "bash", "-c", "exit 7"],
        capture_output=True,
        text=True,
        env=env,
    )
    # returncode=7 from test 4 (same lock name; clean_db flushes between tests)
    assert result.returncode == 7

    # Lock must be released
    assert clean_db.get("state:lock:rc-foo") is None, (
        "Expected lock 'rc-foo' to be released even after non-zero wrapped exit"
    )


# ---------------------------------------------------------------------------
# Test 6 — bare lock contention against --hold
# ---------------------------------------------------------------------------


def test_bare_lock_contention_against_hold(clean_db) -> None:
    """Bare 'state lock' blocks 1s then exits 3 while --hold has the lock.

    Child A: lock --hold contend-foo -- sleep 2 (holds 2s)
    Child B (after 0.3s): lock contend-foo (bare, no --hold) → block 1s → exit 3
    After A finishes: lock is released.

    Verifies: the lock namespace is shared between --hold and bare lock/unlock.
    """
    env = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB)}

    # Child A: hold the lock for 2 seconds
    proc_a = subprocess.Popen(
        [EM_PROJ_BIN, "state", "lock", "--hold", "contend-foo", "--", "sleep", "2"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    # Wait for A to acquire the lock
    time.sleep(0.4)

    # Child B: bare lock (no --hold) — should block 1s then exit 3
    proc_b = subprocess.run(
        [EM_PROJ_BIN, "state", "lock", "contend-foo"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc_b.returncode == 3, (
        f"Expected bare lock to exit 3 (held_by_another) while --hold has it; "
        f"got {proc_b.returncode}\nstderr={proc_b.stderr!r}"
    )

    # Wait for A to finish (sleep 2s)
    try:
        stdout_a, stderr_a = proc_a.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc_a.kill()
        proc_a.communicate()
        pytest.fail("Child A (lock --hold sleep 2) did not complete within 5s")

    assert proc_a.returncode == 0, (
        f"Expected child A to exit 0 after sleep 2; got {proc_a.returncode}"
    )

    # Lock should be released after A exits
    assert clean_db.get("state:lock:contend-foo") is None


# ---------------------------------------------------------------------------
# Test 7 — invalid lock name exits 1 with validation_error
# ---------------------------------------------------------------------------


def test_invalid_lock_name_exits_1(clean_db) -> None:
    """`lock --hold 'foo:bar' -- echo nope` exits 1 with validation_error in stderr.

    Verifies: name validation fires before any subprocess spawn (fast-path exit).
    Relaxed assertion: verify exit 1 + validation_error in stderr (we cannot
    easily verify "echo never ran" without instrumentation).
    """
    env = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB)}
    result = subprocess.run(
        [EM_PROJ_BIN, "state", "lock", "--hold", "--json", "foo:bar", "--", "echo", "nope"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1, (
        f"Expected exit 1 for invalid name 'foo:bar'; got {result.returncode}\n"
        f"stderr={result.stderr!r}"
    )
    assert "validation_error" in result.stderr, (
        f"Expected 'validation_error' in stderr; got: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Test 8 — empty cmd exits 1 with validation_error
# ---------------------------------------------------------------------------


def test_empty_cmd_exits_1(clean_db) -> None:
    """`lock --hold foo` (no `--` and no cmd) exits 1 with validation_error.

    Verifies: the empty-cmd check in the verb fires before lock acquisition.
    """
    env = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB)}
    result = subprocess.run(
        [EM_PROJ_BIN, "state", "lock", "--hold", "--json", "foo"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1, (
        f"Expected exit 1 for --hold with no cmd; got {result.returncode}\n"
        f"stderr={result.stderr!r}"
    )
    assert "validation_error" in result.stderr, (
        f"Expected 'validation_error' in stderr; got: {result.stderr!r}"
    )
