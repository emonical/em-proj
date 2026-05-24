"""Unit tests for em_proj.state.claim — pure claim ops against real Redis on db=15.

Uses the clean_db fixture from tests/conftest.py for per-test isolation.
Validates CLAIM-01, CLAIM-02, and all 8 behavior cases from 04-01-PLAN.md.

Each test:
  - Declares the behavior case or decision being verified in the docstring
  - Uses clean_db fixture for full Redis isolation
  - Makes 1-3 focused assertions (one concern per test)

Redis-touching tests depend on `clean_db` explicitly (function-scoped FLUSHDB on
db=15). Validation-only tests omit it — they must never reach Redis.
"""
from __future__ import annotations

import os
import time

import pytest
import redis as redis_module

import em_proj.redis_client as rc
from em_proj.state.claim import (
    TTL_DEFAULT,
    MIN_TTL,
    MAX_TTL,
    KEY_PREFIX,
    HeldByAnother,
    ClaimNotHeld,
    claim_take,
    claim_release,
    claim_check,
)
from em_proj.state.kv import ValidationError


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
# Module-level constant checks (CLAIM-01, CLAIM-02)
# ---------------------------------------------------------------------------


def test_ttl_default_is_1800() -> None:
    """CLAIM-01: TTL_DEFAULT must be 1800 seconds (30 minutes)."""
    assert TTL_DEFAULT == 1800


def test_key_prefix_is_state_claim() -> None:
    """KEY_PREFIX must be 'state:claim:'."""
    assert KEY_PREFIX == "state:claim:"


def test_ttl_bounds() -> None:
    """MIN_TTL == 60, MAX_TTL == 86400 (claim bounds per PLAN)."""
    assert MIN_TTL == 60
    assert MAX_TTL == 86400


# ---------------------------------------------------------------------------
# Behavior Case 1 — claim_take on fresh area returns holder dict (CLAIM-02)
# ---------------------------------------------------------------------------


def test_claim_take_fresh_area_returns_holder(clean_db) -> None:
    """Behavior 1: claim_take("foo") with no prior claim → returns holder dict with 5 required fields.

    CLAIM-02: holder dict must contain session_id, project_hash, reason, claimed_at, expires_at.
    """
    holder = claim_take("foo", reason="testing fresh claim")

    assert isinstance(holder, dict)
    required_keys = {"session_id", "project_hash", "reason", "claimed_at", "expires_at"}
    assert required_keys == set(holder.keys())
    assert holder["reason"] == "testing fresh claim"
    assert isinstance(holder["claimed_at"], float)
    assert isinstance(holder["expires_at"], float)
    assert holder["expires_at"] > holder["claimed_at"]


def test_claim_take_area_key_exists_in_redis(clean_db) -> None:
    """claim_take stores the claim in Redis under 'state:claim:<project_hash>:<area>'."""
    from em_proj.identity import resolve_project_hash
    project_hash = resolve_project_hash()

    claim_take("myarea")

    test_client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    key = f"{KEY_PREFIX}{project_hash}:myarea"
    assert test_client.exists(key) == 1


# ---------------------------------------------------------------------------
# Behavior Case 2 — same holder repeat call refreshes TTL (not raise)
# ---------------------------------------------------------------------------


def test_claim_take_same_holder_refreshes_ttl(clean_db) -> None:
    """Behavior 2: claim_take by same session_id + project_hash → refreshes TTL, returns updated holder.

    Same-holder repeat call is idempotent: extends TTL rather than raising HeldByAnother.
    The returned holder has updated expires_at.
    """
    first = claim_take("refresh_area", ttl=120, reason="initial")
    time.sleep(0.05)  # small delay so expires_at can differ
    second = claim_take("refresh_area", ttl=120, reason="should not matter for key")

    assert isinstance(second, dict)
    # The second call must NOT raise HeldByAnother (same holder == refresh path)
    assert second["expires_at"] >= first["expires_at"]


def test_claim_take_refresh_returns_all_5_fields(clean_db) -> None:
    """Behavior 2 (continued): refreshed holder still has all 5 required fields."""
    claim_take("refresh2", ttl=200)
    refreshed = claim_take("refresh2", ttl=200)

    required_keys = {"session_id", "project_hash", "reason", "claimed_at", "expires_at"}
    assert required_keys == set(refreshed.keys())


# ---------------------------------------------------------------------------
# Behavior Case 3 — different session_id raises HeldByAnother
# ---------------------------------------------------------------------------


def test_claim_take_different_session_raises_held_by_another(clean_db, monkeypatch) -> None:
    """Behavior 3: claim_take by different session_id → raises HeldByAnother with holder info.

    Force a different session_id to simulate a second session attempting the same claim.
    """
    # Take the claim with the current session
    claim_take("contested", reason="first session")

    # Now simulate a different session
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "fake-session-id-for-test-00000000")

    # Reset client so new session_id is used
    rc._reset_for_tests()

    with pytest.raises(HeldByAnother) as exc_info:
        claim_take("contested", reason="second session attempt")

    exc = exc_info.value
    assert exc.holder is not None
    assert "session_id" in exc.holder
    assert exc.code == "held_by_another"


# ---------------------------------------------------------------------------
# Behavior Case 4 — claim_release by holder deletes the key
# ---------------------------------------------------------------------------


def test_claim_release_by_holder_deletes_key(clean_db) -> None:
    """Behavior 4: claim_release by the holder → Lua compare-and-delete → key gone from Redis."""
    from em_proj.identity import resolve_project_hash
    project_hash = resolve_project_hash()

    claim_take("releaseme", reason="will release")
    claim_release("releaseme")

    test_client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    key = f"{KEY_PREFIX}{project_hash}:releaseme"
    assert test_client.exists(key) == 0


def test_claim_release_by_holder_returns_none(clean_db) -> None:
    """Behavior 4 (continued): claim_release returns None on success."""
    claim_take("releaseme2")
    result = claim_release("releaseme2")
    assert result is None


# ---------------------------------------------------------------------------
# Behavior Case 5 — claim_release by non-holder raises HeldByAnother (exit 3)
# ---------------------------------------------------------------------------


def test_claim_release_by_non_holder_raises_held_by_another(clean_db, monkeypatch) -> None:
    """Behavior 5: claim_release by different session_id → raises HeldByAnother with current holder.

    Take the claim as session A, then try to release as session B.
    """
    claim_take("nonholder_release", reason="original holder")

    # Simulate different session for the release
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "different-session-9999999999")
    rc._reset_for_tests()

    with pytest.raises(HeldByAnother) as exc_info:
        claim_release("nonholder_release")

    exc = exc_info.value
    # Should carry the current holder dict (not None)
    assert exc.holder is not None
    assert exc.code == "held_by_another"


# ---------------------------------------------------------------------------
# Behavior Case 6 — claim_release on absent key raises HeldByAnother(holder=None)
# ---------------------------------------------------------------------------


def test_claim_release_on_absent_key_raises_held_by_another_holder_none(clean_db) -> None:
    """Behavior 6: claim_release on absent key → raises HeldByAnother with holder=None.

    The Lua script returns -1 for absent key → Python raises HeldByAnother(holder=None).
    """
    with pytest.raises(HeldByAnother) as exc_info:
        claim_release("not_a_real_area")

    exc = exc_info.value
    assert exc.holder is None
    assert exc.code == "held_by_another"


# ---------------------------------------------------------------------------
# Behavior Case 7 — claim_check returns holder dict when claimed
# ---------------------------------------------------------------------------


def test_claim_check_when_held_returns_holder_dict(clean_db) -> None:
    """Behavior 7: claim_check("foo") when held → returns holder dict with all 5 fields."""
    claim_take("checkme", reason="for check test")
    holder = claim_check("checkme")

    assert isinstance(holder, dict)
    required_keys = {"session_id", "project_hash", "reason", "claimed_at", "expires_at"}
    assert required_keys == set(holder.keys())
    assert holder["reason"] == "for check test"


# ---------------------------------------------------------------------------
# Behavior Case 8 — claim_check raises ClaimNotHeld when absent
# ---------------------------------------------------------------------------


def test_claim_check_when_absent_raises_claim_not_held(clean_db) -> None:
    """Behavior 8: claim_check("foo") when absent → raises ClaimNotHeld."""
    with pytest.raises(ClaimNotHeld) as exc_info:
        claim_check("absent_area")

    exc = exc_info.value
    assert exc.code == "not_held"
    assert "absent_area" in str(exc)


# ---------------------------------------------------------------------------
# Validation tests — no Redis needed
# ---------------------------------------------------------------------------


def test_claim_take_rejects_invalid_area_name() -> None:
    """validate_key() from kv.py gates area names: spaces, colons, etc. are rejected."""
    with pytest.raises(ValidationError):
        claim_take("invalid area name!")


def test_claim_take_rejects_reason_over_256_chars() -> None:
    """Reason must not exceed 256 characters (_validate_reason)."""
    with pytest.raises(ValidationError):
        claim_take("validname", reason="x" * 257)


def test_claim_take_rejects_ttl_below_min() -> None:
    """TTL below MIN_TTL raises ValidationError."""
    with pytest.raises(ValidationError):
        claim_take("validname", ttl=MIN_TTL - 1)


def test_claim_take_rejects_ttl_above_max() -> None:
    """TTL above MAX_TTL raises ValidationError."""
    with pytest.raises(ValidationError):
        claim_take("validname", ttl=MAX_TTL + 1)


# ---------------------------------------------------------------------------
# No forbidden imports
# ---------------------------------------------------------------------------


def test_no_typer_import() -> None:
    """claim.py must not import typer (D-17 pure-ops constraint)."""
    import ast
    import pathlib

    src = pathlib.Path("src/em_proj/state/claim.py")
    tree = ast.parse(src.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert "typer" not in name, f"claim.py imports typer: {name}"


def test_no_multiprocessing_or_threading_import() -> None:
    """claim.py must not import multiprocessing or threading (no hold runner)."""
    import ast
    import pathlib

    src = pathlib.Path("src/em_proj/state/claim.py")
    tree = ast.parse(src.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert "multiprocessing" not in name, f"claim.py imports multiprocessing"
                assert "threading" not in name, f"claim.py imports threading"
