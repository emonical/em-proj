"""Unit tests for em_proj.state.reserve — pure reservation ops against real Redis on db=15.

Uses the clean_db fixture from tests/conftest.py for per-test isolation.
Validates RESERVE-01, RESERVE-02, and the 14 behavior cases from 07-01-PLAN.md
(11 behavior cases + 3 module-constant tests).

Each test:
  - Declares the behavior case or decision being verified in the docstring
  - Uses clean_db fixture for full Redis isolation
  - Makes 1-3 focused assertions (one concern per test)

Redis-touching tests depend on `clean_db` explicitly (function-scoped FLUSHDB on
db=15). Validation-only tests omit it — they must never reach Redis.
"""
from __future__ import annotations

import time

import pytest
import redis as redis_module

import em_proj.redis_client as rc
from em_proj.state.reserve import (
    TTL_DEFAULT,
    MIN_TTL,
    MAX_TTL,
    KEY_PREFIX,
    HeldByAnother,
    ReserveNotHeld,
    reserve_take,
    reserve_release,
    reserve_check,
    reserve_list_by_prefix,
)
from em_proj.state.kv import ValidationError


@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    """Reset the redis_client singleton so each test honors EM_PROJ_REDIS_DB=15."""
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()


@pytest.fixture(autouse=True)
def _point_at_test_db(monkeypatch):
    """Force reserve.py's get_client() onto db=15 so it shares the clean_db namespace."""
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")


# ---------------------------------------------------------------------------
# Module-level constant checks (RESERVE-01, RESERVE-02)
# ---------------------------------------------------------------------------


def test_ttl_default_is_1800() -> None:
    """RESERVE-01: TTL_DEFAULT must be 1800 seconds (30 minutes)."""
    assert TTL_DEFAULT == 1800


def test_key_prefix_is_state_reserve() -> None:
    """KEY_PREFIX must be 'state:reserve:' (two-namespace invariant)."""
    assert KEY_PREFIX == "state:reserve:"


def test_ttl_bounds() -> None:
    """MIN_TTL == 60, MAX_TTL == 604800 (reservations may last up to 7 days)."""
    assert MIN_TTL == 60
    assert MAX_TTL == 604800


# ---------------------------------------------------------------------------
# Behavior Case 1 — reserve_take on fresh area returns 7-field holder dict
# ---------------------------------------------------------------------------


def test_reserve_take_fresh_area_returns_holder_with_7_fields(clean_db) -> None:
    """Behavior 1: reserve_take("foo") with no prior reservation → holder dict with 7 required fields.

    RESERVE-02: holder dict must contain session_id, project_hash, upstream_identity,
    workstream, reason, claimed_at, expires_at.
    """
    holder = reserve_take(
        "foo",
        upstream_identity="github.com:o/r",
        workstream="ws-x",
        reason="testing fresh reservation",
    )

    assert isinstance(holder, dict)
    required_keys = {
        "session_id", "project_hash", "upstream_identity",
        "workstream", "reason", "claimed_at", "expires_at",
    }
    assert required_keys == set(holder.keys())
    assert holder["upstream_identity"] == "github.com:o/r"
    assert holder["workstream"] == "ws-x"
    assert holder["reason"] == "testing fresh reservation"
    assert isinstance(holder["claimed_at"], float)
    assert isinstance(holder["expires_at"], float)
    assert holder["expires_at"] > holder["claimed_at"]


# ---------------------------------------------------------------------------
# Behavior Case 2 — key shape + 7-field HGETALL assertion (Pitfall #3 mitigation)
# ---------------------------------------------------------------------------


def test_reserve_take_area_key_uses_upstream_identity_prefix(clean_db) -> None:
    """Behavior 2: Redis key uses 'state:reserve:<upstream_identity>:<area>'; 7-field HGETALL.

    THIS test catches ARGV-index drift (Pitfall #3 from 07-RESEARCH).
    After reserve_take, assert the exact 7 HASH fields are present with correct values.
    """
    upstream_identity = "x:y/z"
    area = "myarea"
    workstream = "my-workstream"

    reserve_take(
        area,
        upstream_identity=upstream_identity,
        workstream=workstream,
        reason="pitfall3 test",
    )

    test_client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    key = f"{KEY_PREFIX}{upstream_identity}:{area}"
    raw = test_client.hgetall(key)

    assert set(raw.keys()) == {
        "session_id", "project_hash", "upstream_identity",
        "workstream", "reason", "claimed_at", "expires_at",
    }, f"Unexpected HASH fields (ARGV-index drift?): {set(raw.keys())}"
    assert raw["upstream_identity"] == upstream_identity
    assert raw["workstream"] == workstream
    assert raw["reason"] == "pitfall3 test"


# ---------------------------------------------------------------------------
# Behavior Case 3 — same session refreshes TTL (not raise)
# ---------------------------------------------------------------------------


def test_reserve_take_same_session_refreshes(clean_db) -> None:
    """Behavior 3: reserve_take by same session_id + same upstream_identity → refreshes TTL.

    Same-holder repeat call is idempotent: extends TTL rather than raising HeldByAnother.
    """
    first = reserve_take(
        "refresh_area",
        upstream_identity="github.com:o/r",
        workstream="ws",
        ttl=120,
    )
    time.sleep(0.05)
    second = reserve_take(
        "refresh_area",
        upstream_identity="github.com:o/r",
        workstream="ws",
        ttl=120,
    )

    assert isinstance(second, dict)
    assert second["expires_at"] >= first["expires_at"]


# ---------------------------------------------------------------------------
# Behavior Case 4 — different session raises HeldByAnother
# ---------------------------------------------------------------------------


def test_reserve_take_different_session_raises_held_by_another(clean_db, monkeypatch) -> None:
    """Behavior 4: reserve_take by different session_id → raises HeldByAnother.

    Force a different session_id to simulate a second session attempting the same reservation.
    """
    upstream_identity = "github.com:o/r"
    reserve_take("contested", upstream_identity=upstream_identity, workstream="ws", reason="first")

    # Simulate a different session
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "fake-session-id-for-test-00000000")
    rc._reset_for_tests()

    with pytest.raises(HeldByAnother) as exc_info:
        reserve_take("contested", upstream_identity=upstream_identity, workstream="ws-b")

    exc = exc_info.value
    assert exc.holder is not None
    assert "session_id" in exc.holder
    assert exc.code == "held_by_another"


# ---------------------------------------------------------------------------
# Behavior Case 5 — different upstream_identity does NOT conflict
# ---------------------------------------------------------------------------


def test_reserve_take_different_upstream_does_not_conflict(clean_db) -> None:
    """Behavior 5: two different upstream_identities on same area name → both succeed.

    Confirms the upstream_identity component of the key isolates reservations.
    """
    holder_a = reserve_take("foo", upstream_identity="x:a/b", workstream="ws")
    holder_b = reserve_take("foo", upstream_identity="x:c/d", workstream="ws")

    assert isinstance(holder_a, dict)
    assert isinstance(holder_b, dict)
    # Both should succeed — no conflict because keys differ
    assert holder_a["upstream_identity"] == "x:a/b"
    assert holder_b["upstream_identity"] == "x:c/d"


# ---------------------------------------------------------------------------
# Behavior Case 6 — reserve_release by holder deletes key
# ---------------------------------------------------------------------------


def test_reserve_release_by_holder_deletes_key(clean_db) -> None:
    """Behavior 6: reserve_release by the holder → Lua compare-and-delete → key gone from Redis."""
    upstream_identity = "github.com:o/r"

    reserve_take("releaseme", upstream_identity=upstream_identity, workstream="ws", reason="will release")
    reserve_release("releaseme", upstream_identity=upstream_identity)

    test_client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    key = f"{KEY_PREFIX}{upstream_identity}:releaseme"
    assert test_client.exists(key) == 0


# ---------------------------------------------------------------------------
# Behavior Case 7 — reserve_release by non-holder raises HeldByAnother
# ---------------------------------------------------------------------------


def test_reserve_release_by_non_holder_raises_held_by_another(clean_db, monkeypatch) -> None:
    """Behavior 7: reserve_release by different session_id → raises HeldByAnother."""
    upstream_identity = "github.com:o/r"
    reserve_take("nonholder_release", upstream_identity=upstream_identity, workstream="ws")

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "different-session-9999999999")
    rc._reset_for_tests()

    with pytest.raises(HeldByAnother) as exc_info:
        reserve_release("nonholder_release", upstream_identity=upstream_identity)

    exc = exc_info.value
    assert exc.holder is not None
    assert exc.code == "held_by_another"


# ---------------------------------------------------------------------------
# Behavior Case 8 — reserve_release on absent key raises HeldByAnother(holder=None)
# ---------------------------------------------------------------------------


def test_reserve_release_absent_raises_held_by_another_with_holder_none(clean_db) -> None:
    """Behavior 8: reserve_release on absent key → raises HeldByAnother with holder=None."""
    with pytest.raises(HeldByAnother) as exc_info:
        reserve_release("not_a_real_area", upstream_identity="github.com:o/r")

    exc = exc_info.value
    assert exc.holder is None
    assert exc.code == "held_by_another"


# ---------------------------------------------------------------------------
# Behavior Case 9 — reserve_check returns 7-field holder when held
# ---------------------------------------------------------------------------


def test_reserve_check_held_returns_7_field_holder(clean_db) -> None:
    """Behavior 9: reserve_check on held area → holder dict with all 7 keys."""
    upstream_identity = "github.com:o/r"
    reserve_take("checkme", upstream_identity=upstream_identity, workstream="ws", reason="for check test")
    holder = reserve_check("checkme", upstream_identity=upstream_identity)

    assert isinstance(holder, dict)
    required_keys = {
        "session_id", "project_hash", "upstream_identity",
        "workstream", "reason", "claimed_at", "expires_at",
    }
    assert required_keys == set(holder.keys())
    assert holder["reason"] == "for check test"
    assert holder["upstream_identity"] == upstream_identity


# ---------------------------------------------------------------------------
# Behavior Case 10 — reserve_check raises ReserveNotHeld when absent
# ---------------------------------------------------------------------------


def test_reserve_check_absent_raises_reserve_not_held(clean_db) -> None:
    """Behavior 10: reserve_check on absent area → raises ReserveNotHeld."""
    with pytest.raises(ReserveNotHeld) as exc_info:
        reserve_check("absent_area", upstream_identity="github.com:o/r")

    exc = exc_info.value
    assert exc.code == "not_held"
    assert "absent_area" in str(exc)


# ---------------------------------------------------------------------------
# Behavior Case 11 — reserve_list_by_prefix scopes to upstream_identity
# ---------------------------------------------------------------------------


def test_reserve_list_by_prefix_returns_holders_for_upstream(clean_db) -> None:
    """Behavior 11: reserve_list_by_prefix returns only holders for the given upstream.

    Take two areas under upstream A and one under upstream B.
    List for upstream A → only 2 results, each with "area" injected.
    """
    upstream_a = "x:a/b"
    upstream_b = "x:c/d"

    reserve_take("area1", upstream_identity=upstream_a, workstream="ws")
    reserve_take("area2", upstream_identity=upstream_a, workstream="ws")
    reserve_take("area1", upstream_identity=upstream_b, workstream="ws")  # different upstream, same area name

    results = reserve_list_by_prefix(upstream_identity=upstream_a)

    assert len(results) == 2
    areas = {h["area"] for h in results}
    assert areas == {"area1", "area2"}
    for h in results:
        assert h["upstream_identity"] == upstream_a
        assert "area" in h


# ---------------------------------------------------------------------------
# Two-namespace disjointness sanity test (NEW for Phase 7 — Pitfall #8 mitigation)
# ---------------------------------------------------------------------------


def test_reserve_does_not_collide_with_claim_for_same_area(clean_db) -> None:
    """Pitfall #8: claim + reserve on same area name → both succeed; two disjoint Redis keys.

    Validates the two-namespace invariant at runtime: state:claim:... and
    state:reserve:... are completely disjoint regardless of area name.
    """
    from em_proj.state.claim import claim_take, KEY_PREFIX as CLAIM_PREFIX

    area = "shared_area"
    upstream_identity = "github.com:o/r"

    claim_holder = claim_take(area, reason="claim side")
    reserve_holder = reserve_take(area, upstream_identity=upstream_identity, workstream="ws", reason="reserve side")

    assert isinstance(claim_holder, dict)
    assert isinstance(reserve_holder, dict)

    # Verify two distinct Redis keys exist
    test_client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    from em_proj.identity import resolve_project_hash
    project_hash = resolve_project_hash()

    claim_key = f"{CLAIM_PREFIX}{project_hash}:{area}"
    reserve_key = f"{KEY_PREFIX}{upstream_identity}:{area}"

    assert test_client.exists(claim_key) == 1, f"claim key missing: {claim_key}"
    assert test_client.exists(reserve_key) == 1, f"reserve key missing: {reserve_key}"
    assert claim_key != reserve_key, "claim and reserve keys must be distinct"
