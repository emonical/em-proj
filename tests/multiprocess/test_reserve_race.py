"""Multi-process race tests for `em-proj state reserve` (Plan 07-02).

Uses real em-proj subprocess invocations against db=15 (via
EM_PROJ_REDIS_DB injection) to prove cross-clone reserve serialization.

Covers Phase 7 ROADMAP success criteria:
  SC#1 (race path)    -- test_two_clones_race_reserve_one_wins
  SC#1 (list visible) -- test_reserve_list_visible_from_other_clone_after_race
  SC#1 (refresh path) -- test_two_clones_same_session_refresh_does_not_conflict

Design invariants (Phase 1 RESEARCH Pitfalls, carried forward):
  - subprocess.Popen NOT multiprocessing.Process (#6 — macOS fork+exec)
  - .communicate(timeout=) NOT .wait() (#2 — pipe-buffer deadlock)
  - EM_PROJ_REDIS_DB=15 in child env (#4 — never writes to prod db=0)

Phase-7-specific invariants (07-RESEARCH §Pitfalls 5+6):
  - Per-child cwd= pointing at distinct tmp clones with IDENTICAL fake
    .git/config (Pitfall #6 — env-only variation produces false-positive
    passes because both children resolve the SAME upstream_identity
    from the SAME (test runner) cwd).
  - cmd includes `--workstream test-ws` explicitly to bypass the verb's
    TTY-prompt path (Pitfall #5 — subprocess stdin is a pipe, not a
    TTY; without --workstream the verb would exit 1).
  - Distinct CLAUDE_CODE_SESSION_ID per child for the race test
    (otherwise Lua refresh path masks the conflict — Phase 4 pitfall
    #4 carry).

Import pattern (per Phase 1 Plan 04 SUMMARY):
  from tests.conftest import EM_PROJ_BIN, TEST_DB
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import redis as redis_module

from tests.conftest import EM_PROJ_BIN, TEST_DB


# ---------------------------------------------------------------------------
# Helper: _make_fake_clone
# ---------------------------------------------------------------------------


def _make_fake_clone(parent: Path, name: str, origin_url: str) -> Path:
    """Create a fake clone directory at parent/name with a real git repo + origin remote.

    Uses ``git init`` to create a valid (empty) git repository, then appends
    the [remote "origin"] section to .git/config. Plain .git/config + .git/HEAD
    without git init is NOT sufficient — git requires a valid objects/ directory
    structure to recognize the directory as a repository (Rule 1 fix from
    Plan 07-01 execution, carried to multiprocess tests).

    Returns the clone_dir path.
    """
    clone_dir = parent / name
    clone_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", str(clone_dir)],
        capture_output=True,
        check=True,
    )
    git_config = clone_dir / ".git" / "config"
    with git_config.open("a") as f:
        f.write(
            '[remote "origin"]\n'
            f'\turl = {origin_url}\n'
            '\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
        )
    return clone_dir


# ---------------------------------------------------------------------------
# Test 1 — ROADMAP SC#1 (race path): two clones race, exactly one wins
# ---------------------------------------------------------------------------


def test_two_clones_race_reserve_one_wins(clean_db, tmp_path) -> None:
    """Two sibling clones race `em-proj state reserve migrations.v200`: exactly one wins.

    ROADMAP SC#1 (race path): the Lua refresh-or-take script serializes concurrent
    reserve attempts at the server side. Exactly one caller receives "taken"; the
    other receives "conflict" (exit 3, held_by_another).

    Phase-7-specific: both clones have IDENTICAL origin URLs so they resolve the
    SAME upstream_identity, but DISTINCT per-child cwd= so they are recognized as
    distinct clones. The reservation is keyed on (upstream_identity, area), so
    the race fires between the two separate cwd-derived callers.

    ROADMAP SC#2 assertion: the loser's held_by_another envelope carries the winner's
    workstream field — a sibling clone learns "who has it AND in what workstream."
    """
    origin = "git@github.com:emonical/roleplay-engine.git"
    clone_a = _make_fake_clone(tmp_path, "clone-a", origin)
    clone_b = _make_fake_clone(tmp_path, "clone-b", origin)

    child_a_env = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": "reserve-race-A",
    }
    child_b_env = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": "reserve-race-B",
    }

    cmd = [
        EM_PROJ_BIN, "state", "reserve",
        "--ttl", "60",
        "--workstream", "test-ws",   # Pitfall #5: bypass TTY prompt in subprocess
        "--reason", "race test",
        "--json",
        "migrations.v200",
    ]

    # Tight Popen launch loop — no sleep between spawns. This IS the race.
    proc_a = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=child_a_env, cwd=str(clone_a),   # Pitfall #6: distinct cwd per child
    )
    proc_b = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=child_b_env, cwd=str(clone_b),   # Pitfall #6: distinct cwd per child
    )

    out_a, err_a = proc_a.communicate(timeout=15.0)
    out_b, err_b = proc_b.communicate(timeout=15.0)

    exit_codes = sorted([proc_a.returncode, proc_b.returncode])
    assert exit_codes == [0, 3], (
        f"Expected exactly one winner (exit 0) and one loser (exit 3); "
        f"got {exit_codes}\n"
        f"clone_a: rc={proc_a.returncode} stderr={err_a[:200]!r}\n"
        f"clone_b: rc={proc_b.returncode} stderr={err_b[:200]!r}"
    )

    # Identify winners and losers
    winners_out = [out for (rc, out) in [(proc_a.returncode, out_a), (proc_b.returncode, out_b)] if rc == 0]
    losers_err = [err for (rc, err) in [(proc_a.returncode, err_a), (proc_b.returncode, err_b)] if rc == 3]
    assert len(winners_out) == 1 and len(losers_err) == 1

    # Winner's output must have correct upstream_identity and workstream
    winner_payload = json.loads(winners_out[0])
    assert winner_payload["data"]["upstream_identity"] == "github.com:emonical/roleplay-engine"
    assert winner_payload["data"]["workstream"] == "test-ws"

    # ROADMAP SC#2: loser's held_by_another envelope must carry winner's workstream
    loser_payload = json.loads(losers_err[0])
    assert "holder" in loser_payload.get("data", {}), (
        f"Expected loser envelope to have data.holder; got: {loser_payload}"
    )
    holder = loser_payload["data"]["holder"]
    assert holder.get("workstream") == "test-ws", (
        f"Expected holder.workstream == 'test-ws' in loser envelope (ROADMAP SC#2); "
        f"got {holder.get('workstream')!r}\nFull holder: {holder}"
    )
    assert holder.get("upstream_identity") == "github.com:emonical/roleplay-engine", (
        f"Expected holder.upstream_identity in loser envelope; got {holder.get('upstream_identity')!r}"
    )
    assert holder.get("session_id") in ("reserve-race-A", "reserve-race-B"), (
        f"Expected holder.session_id to be one of the two race children; got {holder.get('session_id')!r}"
    )

    # Defensive cleanup note: no explicit release is called here. Clean-up falls
    # to clean_db's FLUSHDB. The release verb does not yet accept --upstream, so
    # we cannot target the reserve namespace from a different cwd. (Phase 7 scope
    # decision: release --upstream is out of scope for Plan 07-02.)


# ---------------------------------------------------------------------------
# Test 2 — reserve-list visible from sibling clone after race
# ---------------------------------------------------------------------------


def test_reserve_list_visible_from_other_clone_after_race(clean_db, tmp_path) -> None:
    """Reservation made by clone-a is visible via reserve-list from clone-b's cwd.

    Setup: one clone takes a reservation; the other clone reads reserve-list.
    This proves cross-clone read visibility (not a race — sequential operations).

    Note: clean_db is a fresh DB for this test (independent of Test 1's state).
    """
    origin = "git@github.com:emonical/roleplay-engine.git"
    clone_a = _make_fake_clone(tmp_path, "clone-a", origin)
    clone_b = _make_fake_clone(tmp_path, "clone-b", origin)

    child_a_env = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": "list-visible-A",
    }
    child_b_env = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": "list-visible-B",
    }

    # Step 1: clone-a takes a reservation
    reserve_cmd = [
        EM_PROJ_BIN, "state", "reserve",
        "--ttl", "60",
        "--workstream", "test-ws",
        "--json",
        "migrations.v200",
    ]
    reserve_result = subprocess.run(
        reserve_cmd, capture_output=True, text=True,
        env=child_a_env, cwd=str(clone_a), timeout=10.0,
    )
    assert reserve_result.returncode == 0, (
        f"clone-a's reserve should succeed; "
        f"rc={reserve_result.returncode}, stderr={reserve_result.stderr[:200]!r}"
    )

    # Step 2: clone-b reads reserve-list
    list_cmd = [EM_PROJ_BIN, "state", "reserve-list", "--json"]
    list_result = subprocess.run(
        list_cmd, capture_output=True, text=True,
        env=child_b_env, cwd=str(clone_b), timeout=10.0,
    )
    assert list_result.returncode == 0, (
        f"reserve-list from clone-b should succeed; "
        f"rc={list_result.returncode}, stderr={list_result.stderr[:200]!r}"
    )

    list_payload = json.loads(list_result.stdout)
    items = list_payload["data"]["items"]
    assert len(items) == 1, (
        f"Expected 1 item in reserve-list from clone-b; got {len(items)}: {items}"
    )
    assert items[0]["area"] == "migrations.v200", (
        f"Expected area='migrations.v200'; got {items[0].get('area')!r}"
    )
    assert items[0]["upstream_identity"] == "github.com:emonical/roleplay-engine", (
        f"Expected canonical upstream_identity; got {items[0].get('upstream_identity')!r}"
    )
    assert items[0]["workstream"] == "test-ws", (
        f"Expected workstream='test-ws'; got {items[0].get('workstream')!r}"
    )


# ---------------------------------------------------------------------------
# Test 3 — same session refresh across distinct clones does not conflict
# ---------------------------------------------------------------------------


def test_two_clones_same_session_refresh_does_not_conflict(clean_db, tmp_path) -> None:
    """Two clones with the SAME session_id: both exit 0 (taken + refreshed).

    Phase 7's cross-clone refresh semantics: the Lua refresh-or-take script
    compares (session_id, upstream_identity) instead of (session_id, project_hash).
    A same-session call from a DIFFERENT clone directory refreshes the TTL rather
    than raising conflict — because project_hash differs but upstream_identity
    matches. Both exits must be 0.

    This validates the core Phase 7 semantic extension over Phase 4 claims.
    """
    origin = "git@github.com:emonical/roleplay-engine.git"
    clone_a = _make_fake_clone(tmp_path, "clone-a", origin)
    clone_b = _make_fake_clone(tmp_path, "clone-b", origin)

    # SAME session_id for both children — this is what distinguishes this test from Test 1
    shared_env_a = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": "refresh-same-session",
    }
    shared_env_b = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": "refresh-same-session",  # SAME session_id
    }

    cmd = [
        EM_PROJ_BIN, "state", "reserve",
        "--ttl", "60",
        "--workstream", "test-ws",
        "--json",
        "migrations.v200",
    ]

    # Tight Popen launch — both attempt the same area with the same session
    proc_a = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=shared_env_a, cwd=str(clone_a),
    )
    proc_b = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=shared_env_b, cwd=str(clone_b),
    )

    out_a, err_a = proc_a.communicate(timeout=15.0)
    out_b, err_b = proc_b.communicate(timeout=15.0)

    # Both should exit 0: one "taken", one "refreshed" — no conflict
    assert proc_a.returncode == 0, (
        f"clone-a should exit 0 (taken or refreshed); "
        f"rc={proc_a.returncode}\nstdout={out_a[:200]!r}\nstderr={err_a[:200]!r}"
    )
    assert proc_b.returncode == 0, (
        f"clone-b should exit 0 (taken or refreshed); "
        f"rc={proc_b.returncode}\nstdout={out_b[:200]!r}\nstderr={err_b[:200]!r}"
    )

    # Post-race: exactly ONE reservation should exist in Redis
    # (the same key was refreshed, not duplicated)
    client = redis_module.Redis(
        host="127.0.0.1", port=6379, db=TEST_DB, decode_responses=True,
    )
    reserve_keys = list(client.scan_iter(match="state:reserve:*", count=100))
    assert len(reserve_keys) == 1, (
        f"Expected exactly 1 reservation key after same-session refresh; "
        f"got {len(reserve_keys)}: {reserve_keys}"
    )

    # Defensive cleanup: falls to clean_db's FLUSHDB.
