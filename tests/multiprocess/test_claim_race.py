"""Multi-process race tests for `em-proj state claim/release/check` (Plan 04-03).

Uses real ``em-proj`` subprocess invocations against db=15 (via ``EM_PROJ_REDIS_DB``
injection) to prove claim/release/check correctness under concurrent access.

Covers all four Phase 4 ROADMAP success criteria at the CLI boundary:
  SC#1 (CLAIM-01 / race)    -- test_two_sessions_race_claim_one_wins
  SC#1 (CLAIM-01 / refresh) -- test_same_holder_refresh_extends_ttl
  SC#3 (release guard)      -- test_non_holder_release_exits_3_claim_survives
  SC#3 (holder release)     -- test_holder_release_exits_0_claim_gone
  SC#4 (CLAIM-03 / anon)    -- test_anonymous_claim_refused_exit_1

Design invariants (Phase 1 RESEARCH Pitfalls, carried forward):
  - subprocess.Popen NOT multiprocessing.Process (#6 -- fork+exec macOS safety)
  - .communicate(timeout=) NOT .wait() (#2 -- pipe-buffer deadlock)
  - EM_PROJ_REDIS_DB=15 in child env (#4 -- never writes to prod db=0)

Per-child session-id injection for race tests:
  The multiproc_race fixture injects a single shared env dict for all children.
  For test_two_sessions_race_claim_one_wins, both children must use DIFFERENT
  session IDs to force an actual ownership race (same session_id would trigger
  the refresh path, not a conflict). We bypass multiproc_race and use direct
  subprocess.Popen with per-child env injection.

Timing assertion invariant (Warning #6 pin):
  All timing assertions use RELATIVE bounds only. No absolute upper bounds on
  durations -- these are CI-flaky. The race test only asserts exit codes; the
  refresh test asserts that the refreshed TTL exceeds the initial TTL (relative
  comparison), giving a wide margin without any wall-clock sleep dependency.

TTL constraint note:
  The claim verb enforces MIN_TTL=60 (range 60-86400 seconds). Tests use
  ttl=60 for the initial claim and ttl=90 for the refresh to demonstrate the
  TTL extension path without requiring wall-clock waits.

Import pattern (per Phase 1 Plan 04 SUMMARY):
  from tests.conftest import EM_PROJ_BIN, TEST_DB, RaceResult
  NOT the bare ``from conftest import ...`` form.
"""
from __future__ import annotations

import os
import subprocess
import time  # noqa: F401 -- retained for future timing tests and docstring accuracy

import pytest
import redis

from tests.conftest import EM_PROJ_BIN, TEST_DB, RaceResult  # noqa: F401


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _project_hash() -> str:
    """Return the project_hash string that em-proj subprocesses will use.

    Mirrors ``resolve_project_hash()`` in em_proj/identity.py:
    ``os.path.abspath(os.getcwd()).replace('/', '-')``

    The subprocess inherits our cwd, so this value matches the key namespace
    the subprocess writes to.
    """
    return os.path.abspath(os.getcwd()).replace("/", "-")


def _claim_key(area: str) -> str:
    """Return the full Redis key for a claim on ``area``.

    Key shape from claim.py: ``state:claim:<project_hash>:<area>``
    """
    return f"state:claim:{_project_hash()}:{area}"


def _redis_client() -> redis.Redis:
    """Return a redis.Redis client connected to db=TEST_DB (db=15)."""
    return redis.Redis(
        host="127.0.0.1",
        port=6379,
        db=TEST_DB,
        decode_responses=True,
    )


def _run(argv: list[str], extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run an em-proj command via subprocess.run, capturing stdout+stderr.

    Always injects ``EM_PROJ_REDIS_DB=TEST_DB`` so the child targets db=15,
    not prod db=0.  ``extra_env`` entries are merged on top.
    """
    env = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB), **(extra_env or {})}
    return subprocess.run(argv, capture_output=True, text=True, env=env)


# ---------------------------------------------------------------------------
# Test 1 -- ROADMAP SC#1: two simultaneous claim attempts -> exactly one wins
# ---------------------------------------------------------------------------


def test_two_sessions_race_claim_one_wins(clean_db) -> None:
    """Two parallel claim attempts on the same area: exactly one exits 0, the other exits 3.

    ROADMAP SC#1 (race path): the Lua refresh-or-take script serializes concurrent
    claim attempts at the server side. Exactly one caller receives "taken"; the
    other receives "conflict" (exit 3, held_by_another).

    Per-child session-id injection is required here. If both children share the same
    CLAUDE_CODE_SESSION_ID, the second child would trigger the Lua "refreshed" path
    (same-holder refresh -- exit 0), masking the race. We inject distinct session IDs
    so the second child always sees "conflict".

    Uses direct subprocess.Popen (not multiproc_race fixture) for per-child env
    injection. Follows the tight-launch-then-communicate pattern from conftest.py.

    Timing assertion: no absolute upper bound (Warning #6). We only assert the
    exit-code distribution [0, 3] and that the Redis key has a positive TTL.
    """
    child1_env = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": "session-race-A",
    }
    child2_env = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": "session-race-B",
    }

    cmd = [EM_PROJ_BIN, "state", "claim", "--ttl", "60", "race-area"]

    # Tight launch loop -- no sleep between spawns; this is the race.
    proc1 = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child1_env,
    )
    proc2 = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child2_env,
    )

    # Join via .communicate(timeout=) -- drains pipes and avoids deadlock.
    try:
        stdout1, stderr1 = proc1.communicate(timeout=10.0)
    except subprocess.TimeoutExpired:
        proc1.kill()
        proc1.communicate()
        pytest.fail("Child 1 did not exit within 10s")

    try:
        stdout2, stderr2 = proc2.communicate(timeout=10.0)
    except subprocess.TimeoutExpired:
        proc2.kill()
        proc2.communicate()
        pytest.fail("Child 2 did not exit within 10s")

    exit_codes = sorted([proc1.returncode, proc2.returncode])
    assert exit_codes == [0, 3], (
        f"Expected exactly one winner (exit 0) and one loser (exit 3); "
        f"got {exit_codes}\n"
        f"child1: rc={proc1.returncode} stderr={stderr1[:200]!r}\n"
        f"child2: rc={proc2.returncode} stderr={stderr2[:200]!r}"
    )

    # Post-race: the winner's claim key must still be alive in Redis (TTL > 0).
    client = _redis_client()
    key = _claim_key("race-area")
    ttl = client.ttl(key)
    assert ttl > 0, (
        f"Expected claim key '{key}' to exist with TTL > 0 after race; "
        f"got TTL={ttl} (key {'exists' if client.exists(key) else 'absent'})"
    )


# ---------------------------------------------------------------------------
# Test 2 -- ROADMAP SC#1: same holder refreshes TTL past initial TTL
# ---------------------------------------------------------------------------


def test_same_holder_refresh_extends_ttl(clean_db) -> None:
    """Same-holder repeat claim with higher TTL refreshes the expiry.

    ROADMAP SC#1 (refresh path): the Lua refresh-or-take script detects that
    the caller's (session_id, project_hash) already holds the claim and refreshes
    expires_at + EXPIRE instead of raising a conflict.

    NOTE: The claim verb enforces MIN_TTL=60 (range 60-86400). The plan's
    narrative used ttl=5 for illustration; the actual constraint requires
    MIN_TTL=60. We use ttl=60 (initial) then ttl=90 (refresh) to verify
    the refresh path; this avoids any wall-clock sleep dependency entirely.

    Steps:
      1. Claim with ttl=60 as session-refresh -> exit 0; record initial Redis TTL
      2. Immediately repeat claim with ttl=90 as session-refresh -> exit 0 (refresh)
      3. Assert Redis TTL > initial TTL (was extended from ~60 to ~90)
      4. Assert Redis TTL > 80 (must be close to the 90s we just set; CI margin)
    """
    session_env = {"CLAUDE_CODE_SESSION_ID": "session-refresh"}

    # Step 1: claim with ttl=60
    r1 = _run(
        [EM_PROJ_BIN, "state", "claim", "--ttl", "60", "refresh-area"],
        extra_env=session_env,
    )
    assert r1.returncode == 0, (
        f"Initial claim (ttl=60) failed; rc={r1.returncode} stderr={r1.stderr!r}"
    )

    # Record the Redis TTL immediately after the initial claim
    client = _redis_client()
    key = _claim_key("refresh-area")
    initial_ttl = client.ttl(key)
    assert initial_ttl > 0, (
        f"Expected positive TTL after initial claim; got {initial_ttl}"
    )

    # Step 2: refresh claim with ttl=90 -- same session, should exit 0 (refresh path)
    r2 = _run(
        [EM_PROJ_BIN, "state", "claim", "--ttl", "90", "refresh-area"],
        extra_env=session_env,
    )
    assert r2.returncode == 0, (
        f"Refresh claim (ttl=90) failed; rc={r2.returncode} stderr={r2.stderr!r}\n"
        "Expected same-holder repeat claim to exit 0 (refresh, not conflict)"
    )

    # Step 3: verify Redis TTL increased (refresh extended the expiry)
    refreshed_ttl = client.ttl(key)
    assert refreshed_ttl > initial_ttl, (
        f"Expected refreshed TTL ({refreshed_ttl}) > initial TTL ({initial_ttl}); "
        "the refresh should have extended expiry from ~60s to ~90s"
    )

    # Step 4: TTL must be close to 90s (> 80 for CI margin)
    assert refreshed_ttl > 80, (
        f"Expected Redis TTL > 80 after 90s refresh; got TTL={refreshed_ttl}\n"
        "TTL well below 90 would indicate the refresh did not fire correctly"
    )


# ---------------------------------------------------------------------------
# Test 3 -- ROADMAP SC#3: non-holder release exits 3, claim survives
# ---------------------------------------------------------------------------


def test_non_holder_release_exits_3_claim_survives(clean_db) -> None:
    """Non-holder release attempt exits 3; the claim key remains intact.

    ROADMAP SC#3 (intruder path): the Lua compare-and-delete script verifies that
    BOTH session_id AND project_hash match before deleting. A different session_id
    triggers the mismatch path (result=0 -> HeldByAnother -> exit 3).

    Steps:
      1. Claim as session-holder -> exit 0
      2. Attempt release as session-intruder -> exit 3
      3. Verify claim key still exists in Redis (exists == 1)
    """
    holder_env = {"CLAUDE_CODE_SESSION_ID": "session-holder"}
    intruder_env = {"CLAUDE_CODE_SESSION_ID": "session-intruder"}

    # Step 1: claim as session-holder
    r1 = _run(
        [EM_PROJ_BIN, "state", "claim", "contested-area"],
        extra_env=holder_env,
    )
    assert r1.returncode == 0, (
        f"Initial claim failed; rc={r1.returncode} stderr={r1.stderr!r}"
    )

    # Step 2: release as session-intruder -> should exit 3
    r2 = _run(
        [EM_PROJ_BIN, "state", "release", "contested-area"],
        extra_env=intruder_env,
    )
    assert r2.returncode == 3, (
        f"Expected non-holder release to exit 3 (held_by_another); "
        f"got {r2.returncode}\nstderr={r2.stderr!r}"
    )

    # Step 3: verify claim key still present in Redis
    client = _redis_client()
    key = _claim_key("contested-area")
    assert client.exists(key) == 1, (
        f"Expected claim key '{key}' to still exist after failed intruder release; "
        "the non-holder must not be able to delete the claim"
    )


# ---------------------------------------------------------------------------
# Test 4 -- ROADMAP SC#3: holder release exits 0, subsequent check exits 2
# ---------------------------------------------------------------------------


def test_holder_release_exits_0_claim_gone(clean_db) -> None:
    """Holder releases own claim: exits 0; subsequent check exits 2 (not held).

    ROADMAP SC#3 (owner path): the Lua compare-and-delete script finds a matching
    (session_id, project_hash) pair and deletes the key. The follow-up check verb
    finds no key and exits 2 (not_found / ClaimNotHeld).

    Steps:
      1. Claim as session-owner -> exit 0
      2. Release as session-owner -> exit 0 (deleted)
      3. Check -> exit 2 (not held)
    """
    owner_env = {"CLAUDE_CODE_SESSION_ID": "session-owner"}

    # Step 1: claim as session-owner
    r1 = _run(
        [EM_PROJ_BIN, "state", "claim", "owned-area"],
        extra_env=owner_env,
    )
    assert r1.returncode == 0, (
        f"Initial claim failed; rc={r1.returncode} stderr={r1.stderr!r}"
    )

    # Step 2: release as session-owner -> exit 0
    r2 = _run(
        [EM_PROJ_BIN, "state", "release", "owned-area"],
        extra_env=owner_env,
    )
    assert r2.returncode == 0, (
        f"Holder release failed; rc={r2.returncode} stderr={r2.stderr!r}\n"
        "Expected holder to release own claim with exit 0"
    )

    # Step 3: check -> exit 2 (claim is gone)
    r3 = _run(
        [EM_PROJ_BIN, "state", "check", "owned-area"],
        extra_env=owner_env,
    )
    assert r3.returncode == 2, (
        f"Expected check of released area to exit 2 (not held); "
        f"got {r3.returncode}\nstdout={r3.stdout!r}\nstderr={r3.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Test 5 -- ROADMAP SC#4: anonymous claim attempt exits 1
# ---------------------------------------------------------------------------


def test_anonymous_claim_refused_exit_1(clean_db) -> None:
    """Anonymous claim attempt exits 1 with 'anonymous claims refused' in stderr.

    ROADMAP SC#4 / CLAIM-03: the claim verb checks
    ``os.environ.get('CLAUDE_CODE_SESSION_ID', '').strip()`` BEFORE any Redis
    call. An empty or absent session ID triggers the anonymous-refusal gate
    (exit 1, error code 'anonymous_claim').

    Tests both variants:
      Variant A: CLAUDE_CODE_SESSION_ID="" (set but empty)
      Variant B: CLAUDE_CODE_SESSION_ID not present in env at all (popped)

    Both variants must exit 1 and report "anonymous claims refused" in stderr.
    An optional post-check verifies no claim key was written to Redis.
    """
    area = "anon-area"

    # Variant A: CLAUDE_CODE_SESSION_ID set to empty string
    env_a = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB), "CLAUDE_CODE_SESSION_ID": ""}
    r_a = subprocess.run(
        [EM_PROJ_BIN, "state", "claim", area],
        capture_output=True,
        text=True,
        env=env_a,
    )
    assert r_a.returncode == 1, (
        f"Variant A (empty string): expected exit 1; got {r_a.returncode}\n"
        f"stderr={r_a.stderr!r}"
    )
    assert "anonymous claims refused" in r_a.stderr, (
        f"Variant A: expected 'anonymous claims refused' in stderr; "
        f"got: {r_a.stderr!r}"
    )

    # Variant B: CLAUDE_CODE_SESSION_ID not present in env (popped entirely)
    env_b = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
    env_b["EM_PROJ_REDIS_DB"] = str(TEST_DB)
    r_b = subprocess.run(
        [EM_PROJ_BIN, "state", "claim", area],
        capture_output=True,
        text=True,
        env=env_b,
    )
    assert r_b.returncode == 1, (
        f"Variant B (unset): expected exit 1; got {r_b.returncode}\n"
        f"stderr={r_b.stderr!r}"
    )
    assert "anonymous claims refused" in r_b.stderr, (
        f"Variant B: expected 'anonymous claims refused' in stderr; "
        f"got: {r_b.stderr!r}"
    )

    # Post-check: no claim key should exist (gate fires before Redis call)
    client = _redis_client()
    key = _claim_key(area)
    assert client.exists(key) == 0, (
        f"Expected no claim key '{key}' after anonymous refusal; "
        "the anonymous-refusal gate must fire before any Redis write"
    )
