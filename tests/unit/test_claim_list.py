"""Unit tests for claim_list_by_prefix in em_proj.state.claim.

Validates 7 behavioral cases from 05-02-PLAN.md:
  - Empty prefix → []
  - After claim_take → returns holder dict with 5 fields
  - mine=True filter → only current session's claims
  - active=True filter → only claims with TTL > 0
  - stale=True filter → only claims with TTL <= 0 (persistent key, expired-but-present)
  - Malformed/empty HASH → silently skipped
  - Different project_hash key → NOT returned (scope enforcement)

All Redis-touching tests use clean_db (function-scoped FLUSHDB on db=15).
"""
from __future__ import annotations

import time

import pytest
import redis as redis_module

import em_proj.redis_client as rc
from em_proj.state.claim import (
    KEY_PREFIX,
    claim_list_by_prefix,
    claim_take,
    claim_release,
)
from em_proj.identity import resolve_session_id, resolve_project_hash


@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    """Reset the redis_client singleton so each test honors EM_PROJ_REDIS_DB=15."""
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()


@pytest.fixture(autouse=True)
def _point_claim_at_test_db(monkeypatch):
    """Force claim.py's get_client() onto db=15 so it shares the clean_db namespace."""
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")


# ---------------------------------------------------------------------------
# Test 1 — empty prefix returns []
# ---------------------------------------------------------------------------


def test_claim_list_empty(clean_db) -> None:
    """No claims in Redis → claim_list_by_prefix() returns empty list.

    Validates: claim_list_by_prefix returns [] when SCAN finds no keys.
    """
    result = claim_list_by_prefix()
    assert result == []


# ---------------------------------------------------------------------------
# Test 2 — after claim_take, returns holder dict with 5 fields
# ---------------------------------------------------------------------------


def test_claim_list_returns_holder(clean_db) -> None:
    """After claim_take('test-area'), claim_list_by_prefix() returns one entry with required fields.

    Validates: holder dict has session_id, project_hash, claimed_at, expires_at fields.
    """
    claim_take("test-area", reason="list-test")
    try:
        result = claim_list_by_prefix()
        assert len(result) == 1
        holder = result[0]
        required_keys = {"session_id", "project_hash", "reason", "claimed_at", "expires_at", "area"}
        assert required_keys == set(holder.keys()), (
            f"Holder keys mismatch. Expected {required_keys}, got {set(holder.keys())}. "
            "'area' field should be injected by claim_list_by_prefix (CR-02 fix)"
        )
        assert holder["area"] == "test-area", (
            f"Expected holder['area'] == 'test-area', got {holder['area']!r}"
        )
        assert isinstance(holder["session_id"], str)
        assert isinstance(holder["project_hash"], str)
        assert isinstance(holder["claimed_at"], float)
        assert isinstance(holder["expires_at"], float)
    finally:
        claim_release("test-area")


# ---------------------------------------------------------------------------
# Test 3 — mine=True filter: only current session's claims
# ---------------------------------------------------------------------------


def test_claim_list_mine_filter(clean_db, monkeypatch) -> None:
    """Two claims (one current session, one fabricated other session); mine=True returns only current.

    Setup: claim_take creates one claim for current session.
    Then write a second claim key directly via hset+expire with a fake session_id.
    mine=True must return only the current-session claim.
    """
    project_hash = resolve_project_hash()
    current_sid = resolve_session_id()

    # Take a claim for current session
    claim_take("my-area", reason="mine-filter-test")

    # Fabricate a second claim under a different session_id directly in Redis
    other_key = KEY_PREFIX + project_hash + ":other-area"
    now = time.time()
    client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    client.hset(other_key, mapping={
        "session_id": "other-session-fabricated-abc123",
        "project_hash": project_hash,
        "reason": "other session",
        "claimed_at": str(now),
        "expires_at": str(now + 1800),
    })
    client.expire(other_key, 1800)

    try:
        result = claim_list_by_prefix(mine=True)
        # Only the current session's claim should appear
        session_ids = [h["session_id"] for h in result]
        assert current_sid in session_ids
        assert "other-session-fabricated-abc123" not in session_ids
        assert len(result) == 1
    finally:
        claim_release("my-area")
        client.delete(other_key)


# ---------------------------------------------------------------------------
# Test 4 — active=True filter: only claims with TTL > 0
# ---------------------------------------------------------------------------


def test_claim_list_active_filter(clean_db) -> None:
    """One live claim (TTL > 0) and one expired-but-present (no expire set, ttl=-1).

    active=True must return only the live claim.

    The 'expired-but-present' claim is created via hset WITHOUT expire, so Redis
    reports ttl=-1 (persistent key, simulating a TTL-expired claim not yet evicted).
    """
    project_hash = resolve_project_hash()

    # Live claim via claim_take (has TTL)
    claim_take("live-area", reason="active-filter")

    # Stale-but-present claim: persistent key (no expire → ttl = -1)
    stale_key = KEY_PREFIX + project_hash + ":stale-area"
    now = time.time()
    client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    client.hset(stale_key, mapping={
        "session_id": "stale-session-xyz",
        "project_hash": project_hash,
        "reason": "stale claim",
        "claimed_at": str(now - 3600),
        "expires_at": str(now - 1800),
    })
    # NO client.expire() call — ttl will be -1 (persistent)

    try:
        result = claim_list_by_prefix(active=True)
        # Only the live-area claim should appear
        assert len(result) == 1
        assert result[0]["reason"] == "active-filter"
    finally:
        claim_release("live-area")
        client.delete(stale_key)


# ---------------------------------------------------------------------------
# Test 5 — stale=True filter: only claims with TTL <= 0
# ---------------------------------------------------------------------------


def test_claim_list_stale_filter(clean_db) -> None:
    """Same setup as active filter; stale=True returns only the expired-but-present one.

    A persistent key (no expire → ttl=-1) is treated as 'stale' for the purposes
    of the stale filter (TTL <= 0 encompasses both -1 and 0).
    """
    project_hash = resolve_project_hash()

    # Live claim via claim_take (has TTL)
    claim_take("live-area2", reason="stale-filter-live")

    # Stale-but-present claim: persistent key (no expire → ttl = -1)
    stale_key = KEY_PREFIX + project_hash + ":stale-area2"
    now = time.time()
    client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    client.hset(stale_key, mapping={
        "session_id": "stale-session-xyz2",
        "project_hash": project_hash,
        "reason": "stale claim2",
        "claimed_at": str(now - 3600),
        "expires_at": str(now - 1800),
    })
    # NO client.expire() call — ttl will be -1 (persistent)

    try:
        result = claim_list_by_prefix(stale=True)
        # Only the stale-area2 claim should appear
        assert len(result) == 1
        assert result[0]["session_id"] == "stale-session-xyz2"
    finally:
        claim_release("live-area2")
        client.delete(stale_key)


# ---------------------------------------------------------------------------
# Test 6 — malformed/empty HASH → silently skipped
# ---------------------------------------------------------------------------


def test_claim_list_malformed_skip(clean_db) -> None:
    """A key under the claim prefix with an empty HASH is silently skipped.

    claim_list_by_prefix must not raise on empty/malformed HGETALL results.
    """
    project_hash = resolve_project_hash()

    # Write a key with an empty HASH (no fields)
    bad_key = KEY_PREFIX + project_hash + ":bad-area"
    client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    # Create a key with missing required fields (only one field, not the 5 expected)
    client.hset(bad_key, mapping={"junk_field": "junk_value"})

    try:
        # Must not raise KeyError or ValueError — malformed entries are silently skipped
        result = claim_list_by_prefix()
        # The malformed key should not appear in results
        bad_keys_in_result = [h for h in result if h.get("session_id") is None]
        assert len(bad_keys_in_result) == 0
    finally:
        client.delete(bad_key)


# ---------------------------------------------------------------------------
# Test 7 — scope enforcement: different project_hash key NOT returned
# ---------------------------------------------------------------------------


def test_claim_list_scoped_to_project(clean_db) -> None:
    """A claim key written under a different project_hash is NOT returned.

    Scope enforcement: SCAN prefix is KEY_PREFIX + current_project_hash + ":"
    — no cross-project claims returned.
    """
    client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)

    # Write a claim under a completely different project_hash
    other_project_key = KEY_PREFIX + "other-project-hash:area"
    now = time.time()
    client.hset(other_project_key, mapping={
        "session_id": "other-project-session",
        "project_hash": "other-project-hash",
        "reason": "from other project",
        "claimed_at": str(now),
        "expires_at": str(now + 1800),
    })
    client.expire(other_project_key, 1800)

    try:
        result = claim_list_by_prefix()
        # The other-project claim must NOT appear
        other_project_entries = [h for h in result if h.get("project_hash") == "other-project-hash"]
        assert len(other_project_entries) == 0
    finally:
        client.delete(other_project_key)
