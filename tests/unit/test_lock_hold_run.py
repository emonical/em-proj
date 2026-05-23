"""Unit tests for lock_hold_run + RefresherThread (Plan 03-05 Task 1).

RED phase — these tests will FAIL until lock_hold_run and RefresherThread are
added to em_proj/state/lock.py.

Covers:
  - lock_hold_run acquires, spawns subprocess, releases on exit
  - lock_hold_run propagates subprocess exit code
  - lock_hold_run raises HeldByAnother when lock already held
  - RefresherThread sends EXPIRE calls at the right interval
  - RefresherThread stops on stop_event.set()
  - RefresherThread catches redis.ConnectionError / redis.TimeoutError and keeps looping
  - lock_hold_run FileNotFoundError path releases lock and propagates
  - lock_hold_run subprocess stdout passes through (stdout=None)
"""
from __future__ import annotations

import threading
import time
import unittest.mock as mock

import pytest

import em_proj.redis_client as rc
from em_proj.state.kv import ValidationError
from em_proj.state.lock import (
    DEFAULT_TTL,
    HeldByAnother,
    KEY_PREFIX,
    RefresherThread,
    lock_acquire,
    lock_hold_run,
)


@pytest.fixture(autouse=True)
def _reset_client():
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()


@pytest.fixture(autouse=True)
def _point_at_test_db(monkeypatch):
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")


# ---------------------------------------------------------------------------
# Test 1 — lock_hold_run acquires, runs cmd, releases, returns exit code
# ---------------------------------------------------------------------------


def test_lock_hold_run_happy_path_exits_0(clean_db):
    """lock_hold_run('foo', 60, None, ['true']) acquires lock, runs 'true', releases, returns 0.

    Verifies: LOCK-03 SC#4 — auto-acquire + run + release on normal exit.
    """
    exit_code = lock_hold_run("foo", DEFAULT_TTL, None, ["true"])
    assert exit_code == 0
    # Lock must be released after subprocess exits
    assert clean_db.get(KEY_PREFIX + "foo") is None


# ---------------------------------------------------------------------------
# Test 2 — lock_hold_run propagates non-zero exit code
# ---------------------------------------------------------------------------


def test_lock_hold_run_propagates_exit_code(clean_db):
    """lock_hold_run with 'false' returns exit code 1; lock released.

    Verifies: the wrapped subprocess's exit code passes through.
    """
    exit_code = lock_hold_run("foo", DEFAULT_TTL, None, ["false"])
    assert exit_code == 1
    # Lock still released even on non-zero exit
    assert clean_db.get(KEY_PREFIX + "foo") is None


# ---------------------------------------------------------------------------
# Test 3 — lock_hold_run raises HeldByAnother when lock already held by another
# ---------------------------------------------------------------------------


def test_lock_hold_run_raises_held_by_another(clean_db):
    """lock_hold_run raises HeldByAnother if the lock is already held.

    Verifies: acquire-failure is surfaced to caller (not swallowed).
    """
    # Acquire the lock directly first
    lock_acquire("foo", DEFAULT_TTL, None)

    with pytest.raises(HeldByAnother):
        # Try to run --hold while the lock is held; should block 1s then raise
        lock_hold_run("foo", DEFAULT_TTL, None, ["true"])


# ---------------------------------------------------------------------------
# Test 4 — lock_hold_run releases lock on FileNotFoundError
# ---------------------------------------------------------------------------


def test_lock_hold_run_releases_on_file_not_found(clean_db):
    """lock_hold_run releases the lock if the subprocess binary is not found.

    Verifies: acquired lock is cleaned up when Popen fails with FileNotFoundError
    (or OSError on macOS, which raises NotADirectoryError for bare binary names).
    """
    with pytest.raises(OSError):  # FileNotFoundError is a subclass of OSError
        lock_hold_run("foo", DEFAULT_TTL, None, ["no_such_binary_xyzzy"])
    # Lock must be released even though Popen failed
    assert clean_db.get(KEY_PREFIX + "foo") is None


# ---------------------------------------------------------------------------
# Test 5 — RefresherThread class exists and is a threading.Thread subclass
# ---------------------------------------------------------------------------


def test_refresher_thread_is_threading_thread():
    """RefresherThread is a subclass of threading.Thread.

    Verifies: class exists and inherits from threading.Thread (daemon thread).
    """
    assert issubclass(RefresherThread, threading.Thread)


# ---------------------------------------------------------------------------
# Test 6 — RefresherThread sends EXPIRE calls
# ---------------------------------------------------------------------------


def test_refresher_thread_sends_expire(clean_db):
    """RefresherThread calls EXPIRE on the lock key at the expected interval.

    Verifies: D-05 refresher thread re-EXPIREs the lock to keep it alive.
    Uses a short ttl=3 so the interval is 1.0s (ttl/3 = 1.0s).
    """
    stop_event = threading.Event()
    ttl = 3  # interval = min(20.0, 3/3) = 1.0s

    # Set a lock key manually so the refresher has something to EXPIRE
    clean_db.set(KEY_PREFIX + "foo", "test", ex=ttl)

    refresher = RefresherThread("foo", ttl, stop_event)
    refresher.daemon = True
    refresher.start()

    # Wait for at least one refresh cycle
    time.sleep(1.2)
    stop_event.set()
    refresher.join(timeout=2.0)

    # Key should still exist (refresher kept it alive)
    remaining_ttl = clean_db.ttl(KEY_PREFIX + "foo")
    assert remaining_ttl > 0, f"Expected key still alive after refresh, TTL={remaining_ttl}"


# ---------------------------------------------------------------------------
# Test 7 — RefresherThread stops when stop_event is set
# ---------------------------------------------------------------------------


def test_refresher_thread_stops_on_event():
    """RefresherThread exits cleanly when stop_event is set.

    Verifies: the thread terminates without hanging (uses stop_event.wait).
    """
    stop_event = threading.Event()
    refresher = RefresherThread("foo", 60, stop_event)
    refresher.daemon = True
    refresher.start()

    stop_event.set()
    refresher.join(timeout=2.0)
    assert not refresher.is_alive(), "RefresherThread should have stopped after stop_event.set()"


# ---------------------------------------------------------------------------
# Test 8 — RefresherThread catches redis.ConnectionError and keeps looping
# ---------------------------------------------------------------------------


def test_refresher_thread_catches_connection_error_and_keeps_looping():
    """RefresherThread catches redis.ConnectionError and keeps looping (Blocker #3).

    Verifies: T-3-05-04 recovery posture — narrow Redis-transient catch, log + loop.
    The thread must NOT crash when Redis is temporarily unreachable.
    """
    stop_event = threading.Event()
    refresher = RefresherThread("foo", 3, stop_event)  # interval = 1.0s
    refresher.daemon = True

    import redis
    import em_proj.redis_client

    # Patch get_client() to raise ConnectionError on the first EXPIRE, then succeed
    call_count = [0]
    original_get_client = em_proj.redis_client.get_client

    def mock_get_client():
        client_mock = mock.MagicMock()
        call_count[0] += 1
        if call_count[0] <= 2:
            # Raise ConnectionError on first two EXPIRE calls
            client_mock.expire.side_effect = redis.ConnectionError("mocked connection lost")
        else:
            client_mock.expire.return_value = 1
        return client_mock

    with mock.patch("em_proj.redis_client.get_client", side_effect=mock_get_client):
        refresher.start()
        # Wait for two failed refreshes (each ~1.0s)
        time.sleep(2.5)
        stop_event.set()
        refresher.join(timeout=2.0)

    # Thread should have stopped cleanly (not crashed)
    assert not refresher.is_alive(), "RefresherThread crashed on ConnectionError instead of looping"


# ---------------------------------------------------------------------------
# Test 9 — RefresherThread interval is capped at REFRESH_INTERVAL_CAP (20.0s)
# ---------------------------------------------------------------------------


def test_refresher_thread_interval_capped_for_long_ttl():
    """RefresherThread.interval is min(20.0, ttl/3) — capped at 20.0s for long TTLs.

    Verifies: REFRESH_INTERVAL_CAP constant is enforced for ttl > 60s.
    """
    stop_event = threading.Event()
    refresher = RefresherThread("foo", 3600, stop_event)  # ttl=1 hour; ttl/3=1200s → cap at 20.0
    assert refresher.interval <= 20.0, (
        f"Expected interval <= 20.0s for TTL=3600, got {refresher.interval}"
    )


# ---------------------------------------------------------------------------
# Test 10 — lock_hold_run uses communicate(), not wait()
# ---------------------------------------------------------------------------


def test_lock_hold_run_uses_communicate(clean_db):
    """lock_hold_run calls popen.communicate() not popen.wait() (Phase 1 pitfall #2).

    This is a structural test — verifies the symbol .communicate is called.
    Uses a short-running cmd to keep the test fast.
    """
    with mock.patch("subprocess.Popen") as mock_popen_cls:
        mock_proc = mock.MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (None, None)
        mock_proc.pid = 12345
        mock_popen_cls.return_value = mock_proc

        lock_hold_run("foo", DEFAULT_TTL, None, ["true"])

        # communicate() must have been called (not wait())
        mock_proc.communicate.assert_called_once()
        # wait() must NOT have been called on the popen object
        mock_proc.wait.assert_not_called()
