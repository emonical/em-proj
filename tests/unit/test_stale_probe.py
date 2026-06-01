"""Unit tests for em_proj.identity stale-detection probe functions (IDENT-02).

Covers all branches of the three-probe stale-detection sequence from D-10 step 1:
  - probe_pid_alive: live PID, dead PID, AccessDenied PID
  - probe_proc_start_matches: matching time, mismatched time (PID reuse), NoSuchProcess, AccessDenied
  - current_boot_id: stability and derivation independence
  - is_holder_stale: live holder (all three pass), dead PID, PID reuse, reboot, malformed input
  - is_holder_stale: short-circuit (dead PID skips proc_start probe)

No Redis fixtures here — stale-probe tests are stdlib + psutil only;
tests run with Redis completely absent (redis_precheck / clean_db NOT used).

Test inventory (13 tests):
  test_probe_pid_alive_live_pid           — real os.getpid() returns True
  test_probe_pid_alive_dead_pid           — NoSuchProcess -> False
  test_probe_pid_alive_access_denied      — AccessDenied -> True (conservative, T-3-XX-06)
  test_probe_proc_start_matches_exact     — exact match returns True
  test_probe_proc_start_matches_within_epsilon — delta < PROC_START_EPSILON returns True
  test_probe_proc_start_matches_mismatch  — delta > epsilon (PID reuse) returns False
  test_probe_proc_start_matches_no_such   — NoSuchProcess returns False
  test_probe_proc_start_matches_access_denied — AccessDenied returns True (conservative)
  test_current_boot_id_stable             — two calls return same string
  test_current_boot_id_varies_with_boot_time — _boot_id(different_float) differs
  test_is_holder_stale_live               — all three probes pass -> False
  test_is_holder_stale_dead_pid           — pid gone -> True
  test_is_holder_stale_pid_reuse          — create_time mismatch -> True
  test_is_holder_stale_reboot             — boot_id mismatch -> True
  test_is_holder_stale_malformed_missing_key — ValueError on missing key
  test_is_holder_stale_malformed_pid_type   — ValueError when pid is not int
  test_is_holder_stale_short_circuit      — dead pid does not trigger proc_start probe
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import psutil
import pytest

import em_proj.identity as identity_mod
from em_proj.identity import (
    PROC_START_EPSILON,
    _boot_id,
    current_boot_id,
    is_holder_stale,
    probe_pid_alive,
    probe_proc_start_matches,
)


# ---------------------------------------------------------------------------
# Helpers / small stub classes
# ---------------------------------------------------------------------------

_FAKE_CREATE_TIME = 1716480000.0   # arbitrary fixed float for deterministic tests
_FAKE_BOOT_TIME   = 1716000000.0   # arbitrary fixed float for deterministic boot_id


class _FakeProcess:
    """Minimal psutil.Process stub that returns a fixed create_time."""

    def __init__(self, pid: int, create_time: float = _FAKE_CREATE_TIME) -> None:
        self._pid = pid
        self._create_time = create_time

    def create_time(self) -> float:
        return self._create_time


class _FakeProcessFactory:
    """Callable that returns a _FakeProcess or raises on demand."""

    def __init__(self, create_time: float = _FAKE_CREATE_TIME, exc: Exception | None = None) -> None:
        self._create_time = create_time
        self._exc = exc
        self.call_count = 0

    def __call__(self, pid: int | None = None) -> _FakeProcess:
        self.call_count += 1
        if self._exc is not None:
            raise self._exc
        return _FakeProcess(pid or 0, self._create_time)


# ---------------------------------------------------------------------------
# probe_pid_alive tests
# ---------------------------------------------------------------------------


def test_probe_pid_alive_live_pid() -> None:
    """A live PID (os.getpid()) returns True — exercises the real OS path."""
    assert probe_pid_alive(os.getpid()) is True


def test_probe_pid_alive_dead_pid(monkeypatch) -> None:
    """When psutil.Process raises NoSuchProcess, probe_pid_alive returns False."""
    exc = psutil.NoSuchProcess(pid=99999)
    monkeypatch.setattr(identity_mod.psutil, "Process", _FakeProcessFactory(exc=exc))
    assert probe_pid_alive(99999) is False


def test_probe_pid_alive_access_denied(monkeypatch) -> None:
    """When psutil.Process raises AccessDenied, probe_pid_alive returns True (conservative, T-3-XX-06)."""
    exc = psutil.AccessDenied(pid=1)
    monkeypatch.setattr(identity_mod.psutil, "Process", _FakeProcessFactory(exc=exc))
    assert probe_pid_alive(1) is True


# ---------------------------------------------------------------------------
# probe_proc_start_matches tests
# ---------------------------------------------------------------------------


def test_probe_proc_start_matches_exact(monkeypatch) -> None:
    """Exact create_time match returns True."""
    factory = _FakeProcessFactory(create_time=_FAKE_CREATE_TIME)
    monkeypatch.setattr(identity_mod.psutil, "Process", factory)
    assert probe_proc_start_matches(12345, _FAKE_CREATE_TIME) is True


def test_probe_proc_start_matches_within_epsilon(monkeypatch) -> None:
    """A delta less than PROC_START_EPSILON is considered a match (returns True)."""
    # 0.3 < 0.5 (PROC_START_EPSILON)
    factory = _FakeProcessFactory(create_time=_FAKE_CREATE_TIME)
    monkeypatch.setattr(identity_mod.psutil, "Process", factory)
    assert probe_proc_start_matches(12345, _FAKE_CREATE_TIME + 0.3) is True


def test_probe_proc_start_matches_mismatch(monkeypatch) -> None:
    """A delta well beyond PROC_START_EPSILON (PID reuse) returns False."""
    # actual = _FAKE_CREATE_TIME, expected = _FAKE_CREATE_TIME - 480000.0 (way outside epsilon)
    factory = _FakeProcessFactory(create_time=_FAKE_CREATE_TIME)
    monkeypatch.setattr(identity_mod.psutil, "Process", factory)
    assert probe_proc_start_matches(12345, _FAKE_CREATE_TIME - 480000.0) is False


def test_probe_proc_start_matches_no_such(monkeypatch) -> None:
    """NoSuchProcess (PID gone) returns False — PID gone is stale-equivalent."""
    exc = psutil.NoSuchProcess(pid=99999)
    monkeypatch.setattr(identity_mod.psutil, "Process", _FakeProcessFactory(exc=exc))
    assert probe_proc_start_matches(99999, _FAKE_CREATE_TIME) is False


def test_probe_proc_start_matches_access_denied(monkeypatch) -> None:
    """AccessDenied returns True — cannot determine start time, default to match (T-3-XX-06)."""
    exc = psutil.AccessDenied(pid=1)
    monkeypatch.setattr(identity_mod.psutil, "Process", _FakeProcessFactory(exc=exc))
    assert probe_proc_start_matches(1, _FAKE_CREATE_TIME) is True


# ---------------------------------------------------------------------------
# current_boot_id tests
# ---------------------------------------------------------------------------


def test_current_boot_id_stable() -> None:
    """Two consecutive calls to current_boot_id() return the same string."""
    bid1 = current_boot_id()
    bid2 = current_boot_id()
    assert isinstance(bid1, str), f"expected str, got {type(bid1)}"
    assert len(bid1) == 16, f"expected 16 hex chars, got {len(bid1)}: {bid1!r}"
    assert all(c in "0123456789abcdef" for c in bid1), f"non-hex chars in: {bid1!r}"
    assert bid1 == bid2, f"boot_id not stable: {bid1!r} != {bid2!r}"


def test_current_boot_id_varies_with_boot_time() -> None:
    """_boot_id() with a different float produces a different string (not a constant)."""
    real_boot_id = current_boot_id()
    fake_boot_id = _boot_id(_FAKE_BOOT_TIME)
    # The fake boot time is almost certainly different from the real boot time,
    # so the derived IDs must differ (probability of collision: 1/2^64 via sha256 prefix).
    assert fake_boot_id != real_boot_id, (
        "Expected _boot_id(fake_time) to differ from current_boot_id() — "
        "if these match, the fake boot time accidentally equals the real boot time."
    )


# ---------------------------------------------------------------------------
# is_holder_stale tests
# ---------------------------------------------------------------------------


def test_is_holder_stale_live() -> None:
    """A holder built from the current process and boot is NOT stale (returns False)."""
    pid = os.getpid()
    proc_start = psutil.Process(pid).create_time()
    boot_id = current_boot_id()
    holder = {"pid": pid, "proc_start_epoch": proc_start, "boot_id": boot_id}
    assert is_holder_stale(holder) is False


def test_is_holder_stale_dead_pid(monkeypatch) -> None:
    """A holder whose PID no longer exists is stale (returns True)."""
    exc = psutil.NoSuchProcess(pid=99999)
    monkeypatch.setattr(identity_mod.psutil, "Process", _FakeProcessFactory(exc=exc))
    holder = {
        "pid": 99999,
        "proc_start_epoch": _FAKE_CREATE_TIME,
        "boot_id": current_boot_id(),
    }
    assert is_holder_stale(holder) is True


def test_is_holder_stale_pid_reuse(monkeypatch) -> None:
    """A holder where create_time mismatches (PID reused) is stale (returns True)."""
    # Process is alive but its create_time is way off from the holder's proc_start_epoch.
    factory = _FakeProcessFactory(create_time=_FAKE_CREATE_TIME)
    monkeypatch.setattr(identity_mod.psutil, "Process", factory)
    holder = {
        "pid": 12345,
        "proc_start_epoch": _FAKE_CREATE_TIME - 480000.0,  # mismatch by 480000s
        "boot_id": current_boot_id(),
    }
    assert is_holder_stale(holder) is True


def test_is_holder_stale_reboot(monkeypatch) -> None:
    """A holder with a mismatched boot_id (machine rebooted) is stale (returns True)."""
    pid = os.getpid()
    proc_start = psutil.Process(pid).create_time()
    # Provide a stale boot_id that does not match the current machine boot.
    stale_boot_id = "oldboot00000000a"  # 16 chars, definitely not the live boot_id
    holder = {"pid": pid, "proc_start_epoch": proc_start, "boot_id": stale_boot_id}
    # Guard: if we accidentally generated the real boot_id, skip this test.
    real_bid = current_boot_id()
    if real_bid == stale_boot_id:
        pytest.skip("stale_boot_id accidentally matches real boot_id — extremely unlikely, re-run")
    assert is_holder_stale(holder) is True


def test_is_holder_stale_malformed_missing_key() -> None:
    """A holder dict missing a required key raises ValueError."""
    holder = {"proc_start_epoch": _FAKE_CREATE_TIME, "boot_id": "aaaaaaaaaaaaaaaa"}
    # 'pid' key is missing
    with pytest.raises(ValueError, match="pid"):
        is_holder_stale(holder)


def test_is_holder_stale_malformed_pid_type() -> None:
    """A holder dict with a non-int pid raises ValueError."""
    holder = {"pid": "12345", "proc_start_epoch": _FAKE_CREATE_TIME, "boot_id": "aaaaaaaaaaaaaaaa"}
    with pytest.raises(ValueError, match="pid"):
        is_holder_stale(holder)


def test_is_holder_stale_short_circuit(monkeypatch) -> None:
    """When probe_pid_alive returns False (dead PID), the proc_start probe is NOT called.

    This tests the short-circuit ordering from D-10: on a dead PID we save one
    extra psutil.Process().create_time() call under high-contention scenarios.
    """
    exc = psutil.NoSuchProcess(pid=99999)
    factory = _FakeProcessFactory(exc=exc)
    monkeypatch.setattr(identity_mod.psutil, "Process", factory)

    holder = {
        "pid": 99999,
        "proc_start_epoch": _FAKE_CREATE_TIME,
        "boot_id": current_boot_id(),
    }
    result = is_holder_stale(holder)
    assert result is True, "dead PID must be flagged stale"
    # probe_pid_alive calls Process(pid) once; if short-circuit works, probe_proc_start_matches
    # is never reached (which would also call Process(pid)), so total call count == 1.
    assert factory.call_count == 1, (
        f"Expected exactly 1 psutil.Process() call (short-circuit on dead PID), "
        f"got {factory.call_count} — proc_start probe was called when it should have been skipped"
    )
