"""Unit tests for em_proj.state.lock — pure lock ops against real Redis on db=15.

Uses the clean_db fixture from tests/conftest.py for per-test isolation.
Validates LOCK-01, LOCK-02 (1s-block half), D-02..D-10, D-12 from Phase 3 CONTEXT.md.

Each test:
  - Declares the D-XX decision being verified in the docstring
  - Uses clean_db fixture for full Redis isolation
  - Makes 1-3 focused assertions (one concern per test)

Redis-touching tests depend on `clean_db` explicitly (function-scoped FLUSHDB on
db=15). Validation-only tests omit it — they must never reach Redis.
"""
from __future__ import annotations

import json
import os
import time

import pytest

import em_proj.redis_client as rc
from em_proj.identity import current_process_composite, current_boot_id
from em_proj.state.kv import ValidationError
from em_proj.state.lock import (
    DEFAULT_TTL,
    HeldByAnother,
    KEY_PREFIX,
    _encode_holder,
    lock_acquire,
    lock_force_displace,
    lock_release,
)


@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    """Reset the redis_client singleton so each test honors EM_PROJ_REDIS_DB=15.

    lock.py calls get_client() which reads EM_PROJ_REDIS_DB. Resetting the
    singleton between tests keeps them isolated and ensures the test-DB env
    (set by _point_lock_at_test_db) is picked up.
    """
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()


@pytest.fixture(autouse=True)
def _point_lock_at_test_db(monkeypatch):
    """Force lock.py's get_client() onto db=15 so it shares the clean_db namespace."""
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")


# ---------------------------------------------------------------------------
# Test 1 — Acquire on empty key (LOCK-01 / D-01 / D-02)
# ---------------------------------------------------------------------------


def test_acquire_on_empty_key_returns_holder_dict(clean_db) -> None:
    """D-02: lock_acquire returns a dict with all 8 holder fields on an unclaimed key.

    LOCK-01: acquire works atomically via SET NX EX.
    D-02: the holder JSON carries all eight fields from the D-02 wire format.
    """
    holder = lock_acquire("foo")

    assert isinstance(holder, dict)
    required_keys = {"pid", "proc_start_epoch", "boot_id", "session_id",
                     "project_hash", "reason", "acquired_at", "expires_at"}
    assert required_keys == set(holder.keys())
    assert holder["pid"] == os.getpid()
    assert holder["reason"] is None


# ---------------------------------------------------------------------------
# Test 2 — Acquire round-trips through Redis (D-02 / D-03)
# ---------------------------------------------------------------------------


def test_acquire_persisted_value_matches_returned_dict(clean_db) -> None:
    """D-02/D-03: the JSON stored in Redis matches the returned holder dict (round-trip).

    sort_keys=True encoding ensures the stored value is deterministic and parseable.
    """
    holder = lock_acquire("foo")
    raw = clean_db.get(KEY_PREFIX + "foo")
    assert raw is not None
    persisted = json.loads(raw)
    assert persisted == holder


# ---------------------------------------------------------------------------
# Test 3 — Acquire honors TTL (D-04)
# ---------------------------------------------------------------------------


def test_acquire_honors_ttl(clean_db) -> None:
    """D-04: lock_acquire with ttl=2 stores the key with a TTL between 1 and 2 seconds."""
    lock_acquire("foo", ttl=2)
    ttl = clean_db.ttl(KEY_PREFIX + "foo")
    # Redis TTL returns an integer number of remaining seconds; must be >=1 and <=2
    assert 1 <= ttl <= 2


# ---------------------------------------------------------------------------
# Test 4 — Acquire on held by dead PID (D-10 opportunistic stale-takeover)
# ---------------------------------------------------------------------------


def test_acquire_on_stale_dead_pid_takes_over(clean_db) -> None:
    """D-10: lock_acquire performs opportunistic stale-takeover when holder's PID is dead.

    Manually sets a holder with pid=99999 (essentially guaranteed dead on macOS/Linux).
    is_holder_stale probes os.kill → ESRCH → returns True → Lua compare-and-swap
    succeeds → we acquire via takeover. The returned holder has our real pid.
    """
    dead_pid = 99999
    composite = current_process_composite()
    stale_holder = {
        "pid": dead_pid,
        "proc_start_epoch": 1.0,
        "boot_id": "deadbeefdeadbeef",
        "session_id": "dead-session",
        "project_hash": "-dead-project",
        "reason": None,
        "acquired_at": time.time() - 120,
        "expires_at": time.time() + 60,
    }
    clean_db.set(KEY_PREFIX + "foo", _encode_holder(stale_holder), ex=60)

    holder = lock_acquire("foo")

    assert holder["pid"] == os.getpid()
    assert holder["boot_id"] == composite["boot_id"]


# ---------------------------------------------------------------------------
# Test 5 — Acquire on held by LIVE holder blocks then raises HeldByAnother (LOCK-02)
# ---------------------------------------------------------------------------


def test_acquire_on_live_holder_raises_held_by_another(clean_db) -> None:
    """LOCK-02: lock_acquire raises HeldByAnother after the 1s block when a live holder holds.

    We simulate a live holder by storing our own process's real pid + proc_start_epoch +
    boot_id so is_holder_stale correctly identifies it as LIVE. The block-poll runs
    for ~1s then raises HeldByAnother with the holder dict attached.
    """
    composite = current_process_composite()
    live_holder = {
        **composite,
        "reason": None,
        "acquired_at": time.time() - 10,
        "expires_at": time.time() + 60,
    }
    clean_db.set(KEY_PREFIX + "foo", _encode_holder(live_holder), ex=60)

    with pytest.raises(HeldByAnother) as exc_info:
        lock_acquire("foo")

    exc = exc_info.value
    assert exc.holder is not None
    assert exc.holder["pid"] == composite["pid"]


# ---------------------------------------------------------------------------
# Test 6 — Release by holder deletes the key (LOCK-01 / D-06)
# ---------------------------------------------------------------------------


def test_release_by_holder_deletes_key(clean_db) -> None:
    """D-06: lock_release deletes the key when we are the current holder.

    Lua compare-and-delete verifies pid + proc_start_epoch match before DEL.
    """
    lock_acquire("foo")
    lock_release("foo")

    assert clean_db.get(KEY_PREFIX + "foo") is None


# ---------------------------------------------------------------------------
# Test 7 — Release by non-holder raises HeldByAnother (D-09 / T-3-03-04)
# ---------------------------------------------------------------------------


def test_release_by_non_holder_raises_held_by_another(clean_db) -> None:
    """D-09 / T-3-03-04: lock_release raises HeldByAnother when we are not the holder.

    Lua compare-and-delete finds pid != our pid; returns 0; we raise HeldByAnother.
    The lock value remains untouched (the other holder's value is preserved).
    T-3-03-04: PID + proc_start_epoch both must match before any DEL.
    """
    # Set a holder with a different PID (not us)
    composite = current_process_composite()
    other_holder = {
        **composite,
        "pid": 99998,  # different pid
        "reason": "other process",
        "acquired_at": time.time(),
        "expires_at": time.time() + 60,
    }
    original_blob = _encode_holder(other_holder)
    clean_db.set(KEY_PREFIX + "foo", original_blob, ex=60)

    with pytest.raises(HeldByAnother):
        lock_release("foo")

    # Key is still there with the original value
    assert clean_db.get(KEY_PREFIX + "foo") == original_blob


# ---------------------------------------------------------------------------
# Test 8 — Release on absent key raises HeldByAnother with holder=None (D-09)
# ---------------------------------------------------------------------------


def test_release_on_absent_key_raises_held_by_another_none(clean_db) -> None:
    """D-09: lock_release on a non-existent key raises HeldByAnother(holder=None).

    This is the displaced-then-expired flow: the holder was displaced while we held
    the lock and the new holder's lock expired before we tried to release.
    """
    with pytest.raises(HeldByAnother) as exc_info:
        lock_release("foo")

    assert exc_info.value.holder is None


# ---------------------------------------------------------------------------
# Test 9 — Validation: colon in name raises ValidationError (Phase 2 D-09 carry)
# ---------------------------------------------------------------------------


def test_acquire_colon_in_name_raises_validation_error() -> None:
    """Phase 2 D-09 carry: lock names use the same KEY_REGEX as kv keys.

    A colon in the name is rejected (would collide with the KEY_PREFIX delimiter).
    T-3-03-01: lock-name namespace escape prevention.
    """
    with pytest.raises(ValidationError) as exc_info:
        lock_acquire("foo:bar")

    assert exc_info.value.code == "validation_error"


# ---------------------------------------------------------------------------
# Test 10 — TTL bounds validation (D-04 range)
# ---------------------------------------------------------------------------


def test_acquire_ttl_out_of_range_raises_validation_error() -> None:
    """D-04: lock_acquire rejects ttl < MIN_TTL and ttl > MAX_TTL.

    The range is 1–3600 seconds (1 second floor, 1 hour ceiling).
    """
    with pytest.raises(ValidationError):
        lock_acquire("foo", ttl=0)

    with pytest.raises(ValidationError):
        lock_acquire("foo", ttl=10000)


# ---------------------------------------------------------------------------
# Test 11 — Reason length validation (D-12 / T-3-03-02)
# ---------------------------------------------------------------------------


def test_acquire_reason_length_validation(clean_db) -> None:
    """D-12: reason capped at 256 characters. 257 chars raises; 256 chars succeeds.

    T-3-03-02: reason is capped at MAX_REASON_CHARS=256 to prevent accidental
    large payloads being stored in the lock value.
    """
    with pytest.raises(ValidationError) as exc_info:
        lock_acquire("foo", reason="x" * 257)
    assert exc_info.value.code == "validation_error"

    # 256 chars exactly is allowed
    holder = lock_acquire("foo", reason="x" * 256)
    assert len(holder["reason"]) == 256


# ---------------------------------------------------------------------------
# Test 12 — Re-acquire after release works (D-06 / LOCK-01)
# ---------------------------------------------------------------------------


def test_reacquire_after_release(clean_db) -> None:
    """D-06: after releasing a lock, acquiring it again returns a fresh holder.

    The second acquire must have a later acquired_at than the first.
    """
    holder1 = lock_acquire("foo")
    lock_release("foo")
    holder2 = lock_acquire("foo")

    assert holder2["acquired_at"] >= holder1["acquired_at"]
    assert holder2["pid"] == holder1["pid"]


# ---------------------------------------------------------------------------
# Test 13 — 1s block timing (LOCK-02 / D-07)
# ---------------------------------------------------------------------------


def test_acquire_blocks_approximately_one_second(clean_db) -> None:
    """LOCK-02: lock_acquire blocks between 0.9s and 1.5s before raising HeldByAnother.

    The 0.9-1.5s window absorbs poll-loop overhead and CI scheduling jitter.
    The exact block time depends on DEFAULT_BLOCK_SECONDS (1.0) + poll-loop overhead
    from DEFAULT_BLOCK_POLL_MS (50ms per iteration).
    """
    composite = current_process_composite()
    live_holder = {
        **composite,
        "reason": None,
        "acquired_at": time.time(),
        "expires_at": time.time() + 60,
    }
    clean_db.set(KEY_PREFIX + "foo", _encode_holder(live_holder), ex=60)

    start = time.monotonic()
    with pytest.raises(HeldByAnother):
        lock_acquire("foo")
    elapsed = time.monotonic() - start

    # The block should be at least ~1s and not unreasonably long
    assert 0.9 <= elapsed <= 1.5, f"Expected 0.9-1.5s block, got {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Test 14 — force_displace on absent key acquires successfully (lock_force_displace)
# ---------------------------------------------------------------------------


def test_force_displace_on_absent_key_acquires(clean_db) -> None:
    """lock_force_displace on an absent key writes a fresh holder and returns it.

    DEL on a non-existent key is a no-op; SET NX EX succeeds immediately.
    """
    holder = lock_force_displace("foo")

    assert holder["pid"] == os.getpid()
    raw = clean_db.get(KEY_PREFIX + "foo")
    assert raw is not None
    assert json.loads(raw) == holder


# ---------------------------------------------------------------------------
# Test 15 — force_displace on held by another replaces the holder (D-07 / D-14)
# ---------------------------------------------------------------------------


def test_force_displace_on_held_key_replaces_holder(clean_db) -> None:
    """D-07 / D-14: lock_force_displace replaces any current holder, regardless of liveness.

    force_displace does NOT consult is_holder_stale — it is an unconditional atomic
    replacement. Even a live holder (with pid=99998, a different process) is displaced.
    The returned dict and persisted value both show our pid, not 99998.
    """
    composite = current_process_composite()
    other_holder = {
        **composite,
        "pid": 99998,
        "reason": "some other process",
        "acquired_at": time.time(),
        "expires_at": time.time() + 60,
    }
    clean_db.set(KEY_PREFIX + "foo", _encode_holder(other_holder), ex=60)

    holder = lock_force_displace("foo")

    # Our process now owns the lock
    assert holder["pid"] == os.getpid()
    raw = clean_db.get(KEY_PREFIX + "foo")
    assert raw is not None
    persisted = json.loads(raw)
    assert persisted["pid"] == os.getpid()
    # The old holder's pid is gone
    assert persisted["pid"] != 99998


# ---------------------------------------------------------------------------
# Test 16 — force_displace honors ttl and reason (D-04 / D-12)
# ---------------------------------------------------------------------------


def test_force_displace_ttl_and_reason(clean_db) -> None:
    """D-04 / D-12: lock_force_displace stores the reason and the key has the requested TTL.

    Uses ttl=30 so the assertion is fast; checks Redis TTL is 28-30.
    """
    holder = lock_force_displace("foo", ttl=30, reason="manual override")

    assert holder["reason"] == "manual override"
    ttl = clean_db.ttl(KEY_PREFIX + "foo")
    assert 28 <= ttl <= 30, f"Expected TTL 28-30, got {ttl}"


# ---------------------------------------------------------------------------
# Test 17 — force_displace validation (D-09 carry / D-12)
# ---------------------------------------------------------------------------


def test_force_displace_validation() -> None:
    """D-09 carry / D-12: lock_force_displace validates name, ttl, and reason.

    All three must raise ValidationError before any Redis call.
    """
    with pytest.raises(ValidationError):
        lock_force_displace("foo:bar")  # colon in name

    with pytest.raises(ValidationError):
        lock_force_displace("foo", ttl=0)  # ttl below MIN_TTL

    with pytest.raises(ValidationError):
        lock_force_displace("foo", reason="x" * 257)  # reason too long
