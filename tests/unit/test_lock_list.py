"""Unit tests for lock_list_by_prefix — pure list op in em_proj.state.lock.

Tests cover:
  - Empty namespace returns []
  - Single lock returned as holder dict with expected fields
  - --mine filter (mine=True) returns only current-session locks
  - --stale filter (stale=True) returns only stale-holder locks
  - Malformed JSON key is silently skipped
  - Combined mine=True + stale=True requires BOTH predicates (AND logic)

Uses clean_db fixture from conftest.py for per-test Redis isolation (db=15).
Imports only: em_proj.state.lock, em_proj.identity, em_proj.redis_client.
No subprocess invocations — those belong in the multiprocess suite.
"""
from __future__ import annotations

import json
import os
import time

import pytest

import em_proj.redis_client as rc
from em_proj.identity import resolve_session_id, current_process_composite
from em_proj.state.lock import (
    KEY_PREFIX,
    _encode_holder,
    lock_acquire,
    lock_release,
    lock_list_by_prefix,
)


@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    """Reset the redis_client singleton so each test honors EM_PROJ_REDIS_DB=15."""
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()


@pytest.fixture(autouse=True)
def _point_lock_at_test_db(monkeypatch):
    """Force lock.py's get_client() onto db=15 so it shares the clean_db namespace."""
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")


# ---------------------------------------------------------------------------
# Test 1 — Empty namespace returns []
# ---------------------------------------------------------------------------


def test_lock_list_empty(clean_db) -> None:
    """lock_list_by_prefix with no locks in Redis returns an empty list.

    Empty is a valid result — not an error.
    """
    result = lock_list_by_prefix()
    assert result == []


# ---------------------------------------------------------------------------
# Test 2 — Single lock returns holder dict with expected fields
# ---------------------------------------------------------------------------


def test_lock_list_returns_holder(clean_db) -> None:
    """After lock_acquire, lock_list_by_prefix returns a list with one entry.

    The holder dict has at minimum: session_id, pid, expires_at fields.
    """
    lock_acquire("testkey")
    try:
        result = lock_list_by_prefix()
        assert len(result) == 1
        holder = result[0]
        assert "session_id" in holder
        assert "pid" in holder
        assert "expires_at" in holder
        assert "name" in holder, (
            "lock_list_by_prefix must inject 'name' into each holder dict (CR-02 fix)"
        )
        assert holder["pid"] == os.getpid()
        assert holder["name"] == "testkey", (
            f"Expected holder['name'] == 'testkey', got {holder['name']!r}"
        )
    finally:
        lock_release("testkey")


# ---------------------------------------------------------------------------
# Test 3 — mine=True filter returns only current-session locks
# ---------------------------------------------------------------------------


def test_lock_list_mine_filter(clean_db) -> None:
    """mine=True returns only locks whose session_id matches the current session.

    Two locks in Redis:
      - one held by current session (via lock_acquire)
      - one with a fabricated different session_id (written directly via Redis HSET)

    mine=True must return only the current-session lock.
    """
    # Acquire a lock for the current session
    lock_acquire("mylock")

    # Write a second lock with a different session_id directly into Redis
    other_session_id = "other-session-abc123"
    composite = current_process_composite()
    other_holder = {
        **composite,
        "session_id": other_session_id,
        "pid": 99990,  # different pid to keep it distinct
        "reason": None,
        "acquired_at": time.time() - 60,
        "expires_at": time.time() + 60,
    }
    clean_db.set(KEY_PREFIX + "otherlock", _encode_holder(other_holder), ex=60)

    try:
        result = lock_list_by_prefix(mine=True)
        # Only the current-session lock should be returned
        assert len(result) == 1
        assert result[0]["session_id"] == resolve_session_id()
    finally:
        lock_release("mylock")


# ---------------------------------------------------------------------------
# Test 4 — stale=True filter returns only stale-holder locks
# ---------------------------------------------------------------------------


def test_lock_list_stale_filter(clean_db) -> None:
    """stale=True returns only locks whose holder fails is_holder_stale.

    A holder with pid=99999999 (guaranteed absent), proc_start_epoch=0.0,
    boot_id="deadbeef00000000" is stale. stale=True returns it; stale=False does not.
    """
    composite = current_process_composite()
    dead_holder = {
        **composite,
        "pid": 99999999,
        "proc_start_epoch": 0.0,
        "boot_id": "deadbeef00000000",
        "session_id": "dead-session-xyz",
        "reason": None,
        "acquired_at": time.time() - 120,
        "expires_at": time.time() + 60,
    }
    clean_db.set(KEY_PREFIX + "deadlock", _encode_holder(dead_holder), ex=60)

    # stale=True should return the stale lock
    stale_results = lock_list_by_prefix(stale=True)
    assert len(stale_results) == 1
    assert stale_results[0]["pid"] == 99999999

    # stale=False (default) should NOT return the stale lock
    # (it returns ALL locks, stale or not — stale=False means "no filter on staleness")
    # But with stale=True it only returns stale ones
    all_results = lock_list_by_prefix(stale=False)
    # The dead holder should appear in the unfiltered list
    assert len(all_results) == 1
    # With no filter, both stale and live appear
    pids = [h["pid"] for h in all_results]
    assert 99999999 in pids


# ---------------------------------------------------------------------------
# Test 5 — Malformed JSON key is silently skipped
# ---------------------------------------------------------------------------


def test_lock_list_malformed_skip(clean_db) -> None:
    """A key under state:lock: containing invalid JSON is silently skipped.

    No exception is raised; the malformed key does not appear in results.
    """
    # Write invalid JSON directly into a lock-namespace key
    clean_db.set(KEY_PREFIX + "badkey", b"not-valid-json-{{{", ex=60)

    # Should not raise; malformed key is skipped
    result = lock_list_by_prefix()
    assert result == []


# ---------------------------------------------------------------------------
# Test 6 — mine=True and stale=True combined (AND logic)
# ---------------------------------------------------------------------------


def test_lock_list_mine_and_stale_combined(clean_db) -> None:
    """mine=True and stale=True combined requires both conditions (AND logic).

    Setup:
      - Lock A: current session, live holder (current pid) → should NOT appear
      - Lock B: current session, stale holder (dead pid) → SHOULD appear
      - Lock C: other session, stale holder (dead pid) → should NOT appear

    Only Lock B matches mine=True AND stale=True.
    """
    current_sid = resolve_session_id()
    composite = current_process_composite()

    # Lock A: current session, live holder
    lock_acquire("livelock")

    # Lock B: current session, stale holder
    stale_mine_holder = {
        **composite,
        "session_id": current_sid,
        "pid": 99999998,
        "proc_start_epoch": 0.0,
        "boot_id": "deadbeef00000000",
        "reason": None,
        "acquired_at": time.time() - 120,
        "expires_at": time.time() + 60,
    }
    clean_db.set(KEY_PREFIX + "stalemine", _encode_holder(stale_mine_holder), ex=60)

    # Lock C: other session, stale holder
    stale_other_holder = {
        **composite,
        "session_id": "completely-other-session",
        "pid": 99999997,
        "proc_start_epoch": 0.0,
        "boot_id": "deadbeef00000000",
        "reason": None,
        "acquired_at": time.time() - 120,
        "expires_at": time.time() + 60,
    }
    clean_db.set(KEY_PREFIX + "staleother", _encode_holder(stale_other_holder), ex=60)

    try:
        result = lock_list_by_prefix(mine=True, stale=True)
        # Only Lock B satisfies both: current session AND stale
        assert len(result) == 1
        assert result[0]["session_id"] == current_sid
        assert result[0]["pid"] == 99999998
    finally:
        lock_release("livelock")
