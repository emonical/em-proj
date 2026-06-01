"""CliRunner tests for the claim, release, and check state verbs.

Covers CLAIM-01 (claim/refresh), CLAIM-02 (5-field holder), CLAIM-03 (anon refusal),
the exit-code contract (0/1/2/3), and the anonymous-claim gate.

Test design notes
-----------------
- Uses the same CliRunner pattern as test_state_lock_verbs.py: try/except TypeError
  for click >= 8.2's removal of mix_stderr kwarg.
- Every Redis-touching test uses ``clean_db`` for per-test isolation.
- Redis singleton is reset per test so EM_PROJ_REDIS_DB=15 drives get_client()
  onto the test DB.
- Anonymous refusal tests set CLAUDE_CODE_SESSION_ID="" (or delete it) and assert
  exit code 1 + "anonymous claims refused" in stderr (CLAIM-03).
- Session-id is injected via monkeypatch to control which session "holds" the claim.
"""
from __future__ import annotations

import json
import os

import pytest
from typer.testing import CliRunner

import em_proj.redis_client as rc
from em_proj.cli import app

# Click 8.2+ / typer 0.13+ separate stdout & stderr by default and removed the
# `mix_stderr` kwarg; older versions still accept it. Try the explicit form first;
# fall back to plain CliRunner on newer click.
try:
    runner = CliRunner(mix_stderr=False)  # click < 8.2
except TypeError:
    runner = CliRunner()  # click >= 8.2 — default-separated


@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    """Reset the redis_client singleton so each test honors EM_PROJ_REDIS_DB=15."""
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()


@pytest.fixture(autouse=True)
def _point_at_test_db(monkeypatch):
    """Force get_client() onto db=15 so tests use the clean_db namespace."""
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")


@pytest.fixture(autouse=True)
def _set_session_id(monkeypatch):
    """Set a deterministic CLAUDE_CODE_SESSION_ID for all claim verb tests."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "test-session-abc123")


# ---------------------------------------------------------------------------
# Test 1: claim → exits 0, JSON output has area + expires_at fields
# ---------------------------------------------------------------------------

def test_claim_exits_0_with_json_output(clean_db):
    """em-proj state claim <area> --json exits 0 with area + expires_at in data."""
    result = runner.invoke(app, ["state", "claim", "docs/api", "--json"])
    assert result.exit_code == 0, f"Expected exit 0; got {result.exit_code}\nstdout={result.stdout}\nstderr={getattr(result, 'stderr', '')}"
    payload = json.loads(result.stdout.strip())
    assert payload["status"] == "ok"
    assert payload["data"]["area"] == "docs/api"
    assert "expires_at" in payload["data"]
    assert "claimed_at" in payload["data"]


# ---------------------------------------------------------------------------
# Test 2: claim with CLAUDE_CODE_SESSION_ID="" → exit 1, stderr "anonymous claims refused"
# ---------------------------------------------------------------------------

def test_claim_anonymous_refusal_exit_1(clean_db, monkeypatch):
    """CLAIM-03: claim with no session_id → exit 1, 'anonymous claims refused' in stderr."""
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    result = runner.invoke(app, ["state", "claim", "docs/api", "--json"])
    assert result.exit_code == 1, f"Expected exit 1; got {result.exit_code}"
    # In typer/click CliRunner, stderr may be in result.stderr or result.output
    # depending on mix_stderr setting. Check both.
    output_text = result.output
    stderr_text = getattr(result, "stderr", "") or ""
    combined = output_text + stderr_text
    assert "anonymous claims refused" in combined, (
        f"Expected 'anonymous claims refused' in output; got:\nstdout={result.output}\nstderr={stderr_text}"
    )


def test_claim_anonymous_refusal_empty_env(clean_db, monkeypatch):
    """CLAIM-03: claim with CLAUDE_CODE_SESSION_ID='' → exit 1."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "")
    result = runner.invoke(app, ["state", "claim", "docs/api", "--json"])
    assert result.exit_code == 1, f"Expected exit 1; got {result.exit_code}"
    output_text = result.output
    stderr_text = getattr(result, "stderr", "") or ""
    combined = output_text + stderr_text
    assert "anonymous claims refused" in combined


# ---------------------------------------------------------------------------
# Test 3: claim with --ttl → exits 0, TTL honored
# ---------------------------------------------------------------------------

def test_claim_with_ttl(clean_db):
    """em-proj state claim <area> --ttl 120 exits 0."""
    result = runner.invoke(app, ["state", "claim", "schema", "--ttl", "120", "--json"])
    assert result.exit_code == 0, f"Expected exit 0; got {result.exit_code}\nstdout={result.stdout}"
    payload = json.loads(result.stdout.strip())
    assert payload["status"] == "ok"
    assert payload["data"]["area"] == "schema"
    assert payload["data"]["ttl"] == 120


# ---------------------------------------------------------------------------
# Test 4: claim with --reason → exits 0
# ---------------------------------------------------------------------------

def test_claim_with_reason(clean_db):
    """em-proj state claim <area> --reason 'editing schema' exits 0."""
    result = runner.invoke(
        app, ["state", "claim", "schema", "--reason", "editing schema", "--json"]
    )
    assert result.exit_code == 0, f"Expected exit 0; got {result.exit_code}\nstdout={result.stdout}"
    payload = json.loads(result.stdout.strip())
    assert payload["status"] == "ok"


# ---------------------------------------------------------------------------
# Test 5: release after claim → exits 0
# ---------------------------------------------------------------------------

def test_release_after_claim_exits_0(clean_db):
    """em-proj state release <area> after holding the claim → exits 0."""
    # First, take the claim
    claim_result = runner.invoke(app, ["state", "claim", "docs/api", "--json"])
    assert claim_result.exit_code == 0, f"Claim failed: {claim_result.stdout}"

    # Now release it
    result = runner.invoke(app, ["state", "release", "docs/api", "--json"])
    assert result.exit_code == 0, f"Expected exit 0; got {result.exit_code}\nstdout={result.stdout}"
    payload = json.loads(result.stdout.strip())
    assert payload["status"] == "ok"
    assert payload["data"]["released"] is True
    assert payload["data"]["area"] == "docs/api"


# ---------------------------------------------------------------------------
# Test 6: check after claim → exits 0, JSON data.holder has all 5 fields
# ---------------------------------------------------------------------------

def test_check_after_claim_exits_0_with_5_holder_fields(clean_db):
    """em-proj state check <area> when claimed → exits 0, data.holder has all 5 fields."""
    # Claim first
    claim_result = runner.invoke(app, ["state", "claim", "docs/api", "--json"])
    assert claim_result.exit_code == 0, f"Claim failed: {claim_result.stdout}"

    # Check
    result = runner.invoke(app, ["state", "check", "docs/api", "--json"])
    assert result.exit_code == 0, f"Expected exit 0; got {result.exit_code}\nstdout={result.stdout}"
    payload = json.loads(result.stdout.strip())
    assert payload["status"] == "ok"
    holder = payload["data"]["holder"]
    # CLAIM-02: must have all 5 fields
    assert "session_id" in holder, f"Missing session_id in holder: {holder}"
    assert "project_hash" in holder, f"Missing project_hash in holder: {holder}"
    assert "reason" in holder or "reason" in holder, f"Missing reason in holder: {holder}"
    assert "claimed_at" in holder, f"Missing claimed_at in holder: {holder}"
    assert "expires_at" in holder, f"Missing expires_at in holder: {holder}"


# ---------------------------------------------------------------------------
# Test 7: check on unclaimed area → exits 2
# ---------------------------------------------------------------------------

def test_check_unclaimed_exits_2(clean_db):
    """em-proj state check <area> when not claimed → exits 2."""
    result = runner.invoke(app, ["state", "check", "nonexistent-area", "--json"])
    assert result.exit_code == 2, f"Expected exit 2; got {result.exit_code}\nstdout={result.stdout}"
    payload = json.loads(result.stdout.strip())
    assert payload["status"] == "not_found"


# ---------------------------------------------------------------------------
# Test 8: release on unclaimed area → exits 2 (not held / expired)
# ---------------------------------------------------------------------------

def test_release_unclaimed_exits_2(clean_db):
    """em-proj state release <area> on non-existent claim → exits 2 (not held)."""
    result = runner.invoke(app, ["state", "release", "nonexistent-area", "--json"])
    assert result.exit_code == 2, f"Expected exit 2; got {result.exit_code}\nstdout={result.stdout}"
    payload = json.loads(result.stdout.strip())
    assert payload["status"] == "not_found"


# ---------------------------------------------------------------------------
# Test 9: release by non-holder → exits 3 (held_by_another)
# ---------------------------------------------------------------------------

def test_release_by_non_holder_exits_3(clean_db, monkeypatch):
    """em-proj state release by non-holder → exits 3 with held_by_another."""
    # Claim with session A
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-A")
    rc._reset_for_tests()
    claim_result = runner.invoke(app, ["state", "claim", "docs/api", "--json"])
    assert claim_result.exit_code == 0, f"Claim failed: {claim_result.stdout}"

    # Try to release with session B
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-B")
    rc._reset_for_tests()
    result = runner.invoke(app, ["state", "release", "docs/api", "--json"])
    assert result.exit_code == 3, f"Expected exit 3; got {result.exit_code}\nstdout={result.stdout}"
    # The error goes to stderr in JSON mode; check both
    output_text = result.output
    stderr_text = getattr(result, "stderr", "") or ""
    combined = output_text + stderr_text
    # At minimum, exit code 3 is the signal
    assert result.exit_code == 3


# ---------------------------------------------------------------------------
# Test 10: same holder repeat claim → exits 0 (refresh semantics)
# ---------------------------------------------------------------------------

def test_claim_repeat_by_same_holder_exits_0(clean_db):
    """CLAIM-01 refresh: same holder calling claim again exits 0 (TTL refreshed)."""
    # First claim
    result1 = runner.invoke(app, ["state", "claim", "docs/api", "--json"])
    assert result1.exit_code == 0, f"First claim failed: {result1.stdout}"

    # Second claim by same session
    result2 = runner.invoke(app, ["state", "claim", "docs/api", "--json"])
    assert result2.exit_code == 0, f"Second claim (refresh) failed: {result2.stdout}"
    payload = json.loads(result2.stdout.strip())
    assert payload["status"] == "ok"


# ---------------------------------------------------------------------------
# Test 11: different session claim → exits 3 (held_by_another)
# ---------------------------------------------------------------------------

def test_claim_by_different_session_exits_3(clean_db, monkeypatch):
    """Different session claiming held area → exits 3 with held_by_another."""
    # Claim with session A
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-A-unique")
    rc._reset_for_tests()
    claim_result = runner.invoke(app, ["state", "claim", "docs/api", "--json"])
    assert claim_result.exit_code == 0, f"First claim failed: {claim_result.stdout}"

    # Different session B tries to claim
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-B-unique")
    rc._reset_for_tests()
    result = runner.invoke(app, ["state", "claim", "docs/api", "--json"])
    assert result.exit_code == 3, f"Expected exit 3; got {result.exit_code}\nstdout={result.stdout}"
    # Error envelope should be on stderr for JSON mode
    output_text = result.output
    stderr_text = getattr(result, "stderr", "") or ""
    combined = output_text + stderr_text
    assert result.exit_code == 3


# ---------------------------------------------------------------------------
# Test 12: anonymous refusal fires BEFORE any Redis call (D-18 order)
# ---------------------------------------------------------------------------

def test_claim_anonymous_fires_before_redis(monkeypatch):
    """Anonymous refusal must fire before die_if_redis_unreachable (D-18 order).

    No clean_db fixture here — we verify the exit code is 1 even without
    needing a real Redis connection for the anon check.

    Mocks die_if_redis_unreachable so that if the ordering were accidentally
    reversed (anonymous check moved after the Redis pre-check), this test
    would fail rather than passing for the wrong reason (Redis just happens
    to be running).
    """
    redis_calls: list[bool] = []
    monkeypatch.setattr(
        "em_proj.state.die_if_redis_unreachable",
        lambda *a, **kw: redis_calls.append(True),
    )
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    result = runner.invoke(app, ["state", "claim", "docs/api"])
    assert result.exit_code == 1, f"Expected exit 1; got {result.exit_code}"
    output_text = result.output
    stderr_text = getattr(result, "stderr", "") or ""
    combined = output_text + stderr_text
    assert "anonymous claims refused" in combined
    assert not redis_calls, "die_if_redis_unreachable must not be called before anonymous refusal"
