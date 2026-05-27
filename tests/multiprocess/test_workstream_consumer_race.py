"""Multi-process race tests for ``gsd-sdk query workstream.set`` (Plan 06-02).

Drives ``gsd-sdk`` as the subject-under-test to prove CONSUMER-02 and Q-B at
the CLI boundary.

Covers Phase 6 success criteria:
  CONSUMER-02 (race)   -- test_two_sessions_race_workstream_set_one_wins
  Pitfall #4 sanity    -- test_same_session_refresh_does_not_conflict
  Q-B fallback         -- test_em_proj_missing_falls_through_with_warning

Design invariants (Phase 1 RESEARCH Pitfalls, carried forward):
  - subprocess.Popen NOT multiprocessing.Process (#6 -- fork+exec macOS safety)
  - .communicate(timeout=) NOT .wait() (#2 -- pipe-buffer deadlock)
  - EM_PROJ_REDIS_DB=15 in child env (#4 -- never writes to prod db=0)

Phase-6-specific invariant:
  - Distinct CLAUDE_CODE_SESSION_ID per child (Pitfall #4): two children with
    the SAME session-id trigger the Lua refresh path (exit 0, "refreshed"),
    masking the race. Per-child distinct session-ids force an actual ownership
    race so one wins and one gets held_by_another.

argv[0] is ``gsd-sdk`` (not EM_PROJ_BIN). The mandatory ``--raw`` flag
suppresses TTY-pretty output and emits the JSON envelope. ``subprocess.Popen``
is called with ``cwd=str(tmp_path)`` so the gsd-sdk process inherits the
project directory via ``process.cwd()``; this ensures the TS handler
(``workstreamSet`` in sdk/dist/query/workstream.js) resolves the
``.planning/workstreams/`` path correctly. DO NOT pass ``--cwd tmp_path`` as
a flag — that routes the request through the CJS fallback handler which does
not have the Phase 6 claim gate.

Timing assertion invariant (Warning #6 pin):
  No absolute upper bounds on durations. The race test only asserts exit codes
  and JSON body; the refresh test asserts same-session repeat does not error.
  Timeouts on .communicate() and subprocess.run() are SAFETY limits (15s),
  not performance bounds.
"""
from __future__ import annotations

import shutil

import pytest

if shutil.which("gsd-sdk") is None:
    pytest.skip(
        "`gsd-sdk` not on PATH — install via `npm install -g get-shit-done-cc` "
        "to enable Phase 6 consumer tests",
        allow_module_level=True,
    )

import json
import os
import subprocess
from pathlib import Path

import redis

from tests.conftest import EM_PROJ_BIN, TEST_DB


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _global_path() -> str:
    """Return PATH with venv-resident em-proj excluded.

    When pytest runs under ``uv run pytest``, the venv bin directory is
    prepended to PATH.  If the venv carries a Phase 2 em-proj (which lacks the
    ``state claim`` verb), gsd-sdk's ``spawnSync('em-proj', ...)`` finds that
    stale binary first, exits 2 (unknown verb), falls through ALL of the
    exit-code checks (3, 1, ENOENT) and proceeds to the legacy file-write
    path — causing both race contestants to return ``{set: True}``.

    The fix: strip any PATH entry whose directory sits inside a ``.venv``
    subtree *and* provides ``em-proj``.  Non-em-proj directories inside
    ``.venv`` (the python interpreter, pip, etc.) are kept so other
    subprocess invocations are unaffected.
    """
    keep: list[str] = []
    for entry in os.environ.get("PATH", "").split(":"):
        if not entry:
            continue
        p = Path(entry)
        if ".venv" in p.parts and (p / EM_PROJ_BIN).exists():
            # This is the venv-resident em-proj — exclude it so gsd-sdk
            # falls through to the globally-installed binary.
            continue
        keep.append(entry)
    return ":".join(keep)


def _project_hash_for(path: str) -> str:
    """Return the project_hash string that em-proj subprocesses will use.

    Mirrors ``resolve_project_hash()`` in em_proj/identity.py:
    ``os.path.abspath(path).replace('/', '-')``
    """
    return os.path.abspath(path).replace("/", "-")


def _claim_key(project_hash: str, area: str = "workstream.active") -> str:
    """Return the full Redis key for a claim on ``area``.

    Key shape from claim.py: ``state:claim:<project_hash>:<area>``
    """
    return f"state:claim:{project_hash}:{area}"


def _redis_client() -> redis.Redis:
    """Return a redis.Redis client connected to db=TEST_DB (db=15)."""
    return redis.Redis(
        host="127.0.0.1",
        port=6379,
        db=TEST_DB,
        decode_responses=True,
    )


def _setup_workstream_dir(tmp_path: Path, name: str) -> None:
    """Create .planning/workstreams/<name>/STATE.md so gsd-sdk's existsSync passes."""
    ws = tmp_path / ".planning" / "workstreams" / name
    ws.mkdir(parents=True)
    (ws / "STATE.md").write_text(f"---\nworkstream: {name}\n---\n")


# ---------------------------------------------------------------------------
# Test 1 -- CONSUMER-02: two simultaneous gsd-sdk calls → exactly one wins
# ---------------------------------------------------------------------------


def test_two_sessions_race_workstream_set_one_wins(clean_db, tmp_path) -> None:
    """Two parallel gsd-sdk workstream.set calls on the same project: one wins, one is refused.

    CONSUMER-02: the em-proj claim gate (via the Phase 6 consumer patch in
    sdk/dist/query/workstream.js) serializes concurrent workstream.set calls at
    the Redis Lua layer. Exactly one caller's JSON body has ``{set: true}``;
    the other has ``{error: "held_by_another", holder: {...}}``.

    Per-child session-id injection is required. If both children share the same
    CLAUDE_CODE_SESSION_ID, the second child triggers the Lua "refreshed" path
    (same-holder refresh -- exit 0, set: true), masking the race. We inject
    distinct session IDs so the second child always sees "held_by_another"
    (Pitfall #4).

    Popen is called with cwd=str(tmp_path) so the gsd-sdk process resolves
    .planning/workstreams/ against tmp_path (via Node's process.cwd()). This
    ensures the TS handler fires rather than the CJS fallback -- the TS handler
    is the one that has the Phase 6 claim gate.

    Timing assertion: no absolute upper bound (Warning #6). We assert the
    JSON body distribution and that the Redis claim key has a positive TTL.
    """
    _setup_workstream_dir(tmp_path, "race-ws")

    safe_path = _global_path()
    child_a_env = {
        **os.environ,
        "PATH": safe_path,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": "ws-race-A",
    }
    child_b_env = {
        **os.environ,
        "PATH": safe_path,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": "ws-race-B",
    }

    # NOTE: --raw only, no --cwd. cwd= kwarg routes gsd-sdk to the TS handler.
    cmd = ["gsd-sdk", "query", "workstream.set", "race-ws", "--raw"]

    # Tight launch loop -- no sleep between spawns; this is the race.
    proc_a = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_a_env,
        cwd=str(tmp_path),
    )
    proc_b = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_b_env,
        cwd=str(tmp_path),
    )

    # Join via .communicate(timeout=) -- drains pipes and avoids deadlock.
    try:
        out_a, err_a = proc_a.communicate(timeout=15.0)
    except subprocess.TimeoutExpired:
        proc_a.kill()
        proc_a.communicate()
        pytest.fail("Child A (ws-race-A) did not exit within 15s")

    try:
        out_b, err_b = proc_b.communicate(timeout=15.0)
    except subprocess.TimeoutExpired:
        proc_b.kill()
        proc_b.communicate()
        pytest.fail("Child B (ws-race-B) did not exit within 15s")

    # Parse JSON bodies from each child's stdout.
    try:
        data_a = json.loads(out_a)
    except json.JSONDecodeError:
        pytest.fail(
            f"Child A stdout is not valid JSON.\n"
            f"stdout={out_a!r}\nstderr={err_a[:200]!r}"
        )
    try:
        data_b = json.loads(out_b)
    except json.JSONDecodeError:
        pytest.fail(
            f"Child B stdout is not valid JSON.\n"
            f"stdout={out_b!r}\nstderr={err_b[:200]!r}"
        )

    winners = [d for d in (data_a, data_b) if d.get("set") is True]
    losers = [d for d in (data_a, data_b) if d.get("error") == "held_by_another"]

    assert len(winners) == 1, (
        f"Expected exactly one winner (set: true); got winners={winners}\n"
        f"child_a: rc={proc_a.returncode} body={data_a}\n"
        f"child_b: rc={proc_b.returncode} body={data_b}"
    )
    assert len(losers) == 1, (
        f"Expected exactly one held_by_another loser; got losers={losers}\n"
        f"child_a: rc={proc_a.returncode} body={data_a}\n"
        f"child_b: rc={proc_b.returncode} body={data_b}"
    )

    # The loser must carry the winner's session_id in the holder dict.
    loser = losers[0]
    assert "holder" in loser, (
        f"Loser response must contain 'holder' dict; got: {loser}"
    )
    if loser["holder"] is not None:
        assert loser["holder"]["session_id"] in ("ws-race-A", "ws-race-B"), (
            f"Loser holder.session_id must be one of the two race sessions; "
            f"got: {loser['holder']['session_id']!r}"
        )

    # Post-race Redis assertion: the winner's claim must still be alive.
    ph = _project_hash_for(str(tmp_path))
    ttl = _redis_client().ttl(_claim_key(ph))
    assert ttl > 0, (
        f"Winner's claim key should still be alive after race; got TTL={ttl}"
    )

    # Defensive cleanup: release the winner's claim so clean_db can FLUSHDB.
    # Failure is non-fatal (clean_db will FLUSHDB anyway).
    winner_session = "ws-race-A" if data_a.get("set") is True else "ws-race-B"
    release_env = {
        **os.environ,
        "PATH": safe_path,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": winner_session,
    }
    subprocess.run(
        [EM_PROJ_BIN, "state", "release", "--json", "workstream.active"],
        capture_output=True,
        env=release_env,
        timeout=10.0,
        check=False,
    )


# ---------------------------------------------------------------------------
# Test 2 -- Pitfall #4 sanity: same session refresh does not conflict
# ---------------------------------------------------------------------------


def test_same_session_refresh_does_not_conflict(clean_db, tmp_path) -> None:
    """Same-session repeat workstream.set exits successfully both times.

    Pitfall #4 sanity check: the em-proj claim contract's refresh-or-take Lua
    semantics mean a same-session repeat call to ``workstream.set`` should succeed
    (exit 0, set: true) not error with held_by_another.

    If the consumer-side code incorrectly modeled repeat calls as "new claim"
    attempts, the second call would fail against itself. This test proves the
    refresh path is preserved through the gsd-sdk shell-out boundary.
    """
    _setup_workstream_dir(tmp_path, "refresh-ws")

    def _run_set(extra_env: dict) -> tuple[int, dict, str]:
        cmd = ["gsd-sdk", "query", "workstream.set", "refresh-ws", "--raw"]
        env = {**os.environ, "PATH": _global_path(), "EM_PROJ_REDIS_DB": str(TEST_DB), **extra_env}
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=15.0,
            cwd=str(tmp_path),
        )
        try:
            body = json.loads(r.stdout)
        except json.JSONDecodeError:
            body = {}
        return r.returncode, body, r.stderr

    # Step 1: first call -- should succeed and take the claim.
    rc1, body1, stderr1 = _run_set({"CLAUDE_CODE_SESSION_ID": "ws-refresh-X"})
    assert rc1 == 0, (
        f"First workstream.set call failed; rc={rc1} stderr={stderr1[:200]!r}"
    )
    assert body1.get("set") is True, (
        f"First call should have set: true; got: {body1}"
    )

    # Step 2: same session, repeat call -- should refresh (not conflict).
    rc2, body2, stderr2 = _run_set({"CLAUDE_CODE_SESSION_ID": "ws-refresh-X"})
    assert rc2 == 0, (
        f"Second (refresh) workstream.set call failed; rc={rc2} stderr={stderr2[:200]!r}\n"
        "Expected same-holder repeat call to succeed (refresh path)"
    )
    assert body2.get("set") is True, (
        f"Second call should have set: true (refresh); got: {body2}"
    )
    assert body2.get("error") != "held_by_another", (
        f"Second call must NOT return held_by_another for same session; got: {body2}"
    )

    # Post-state: the claim key should still exist in Redis.
    ph = _project_hash_for(str(tmp_path))
    assert _redis_client().exists(_claim_key(ph)) == 1, (
        "Expected claim key to exist in Redis after two successful set calls"
    )


# ---------------------------------------------------------------------------
# Test 3 -- Q-B fallback: em-proj missing from PATH falls through with warning
# ---------------------------------------------------------------------------


def test_em_proj_missing_falls_through_with_warning(clean_db, tmp_path) -> None:
    """gsd-sdk exits 0 when em-proj is not on PATH (Q-B silent fallback).

    Q-B decision: when spawnSync('em-proj', ...) errors with ENOENT (em-proj
    not installed or not on PATH), gsd-sdk falls back to the legacy unguarded
    file-write behavior. This preserves backward compatibility for users who
    do not have em-proj installed while still emitting a visible stderr warning.

    The scrubbed PATH applies ONLY to the gsd-sdk subprocess env -- em-proj
    remains discoverable for the pytest process infrastructure (clean_db, etc.).
    """
    _setup_workstream_dir(tmp_path, "race-ws")

    # Verify em-proj is on PATH in THIS process (so the test is meaningful).
    if shutil.which(EM_PROJ_BIN) is None:
        pytest.skip(f"`{EM_PROJ_BIN}` not on PATH; cannot test PATH-scrub fallback")

    # Build a minimal PATH that contains only system executables but NOT em-proj.
    # We must retain /usr/bin and /bin so that gsd-sdk (node) can still run.
    # We also need the node bin directory so gsd-sdk itself is callable.
    gsd_sdk_bin = shutil.which("gsd-sdk")
    node_bin = shutil.which("node") or ""
    # Collect directories needed for gsd-sdk and node but not em-proj.
    keep_dirs: set[str] = set()
    for path_dir in os.environ.get("PATH", "").split(":"):
        if not path_dir:
            continue
        p = Path(path_dir)
        # Keep if it contains gsd-sdk or node but NOT em-proj.
        has_gsd_sdk = (gsd_sdk_bin is not None) and (p / "gsd-sdk").exists()
        has_node = node_bin and (p / "node").exists()
        has_em_proj = (p / EM_PROJ_BIN).exists()
        if (has_gsd_sdk or has_node) and not has_em_proj:
            keep_dirs.add(path_dir)
        elif not has_em_proj and path_dir in ("/usr/bin", "/bin", "/usr/local/bin"):
            keep_dirs.add(path_dir)

    # Preserve the PATH order; only include the keep_dirs.
    scrubbed_path = ":".join(
        p for p in os.environ.get("PATH", "").split(":") if p in keep_dirs
    )

    env = {
        **os.environ,
        "PATH": scrubbed_path,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": "ws-fallback-test",
    }

    cmd = ["gsd-sdk", "query", "workstream.set", "race-ws", "--raw"]
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=15.0,
        cwd=str(tmp_path),
    )

    assert r.returncode == 0, (
        "gsd-sdk should exit 0 on em-proj-missing (Q-B silent fallback). "
        f"Got rc={r.returncode}, stderr={r.stderr[:200]!r}"
    )
    assert "em-proj not on PATH" in r.stderr, (
        "Expected the documented Q-B fallback warning in stderr. "
        f"Got stderr={r.stderr[:300]!r}"
    )

    # Legacy file-write path took effect: .planning/active-workstream was written.
    pointer = tmp_path / ".planning" / "active-workstream"
    assert pointer.exists(), (
        "Expected .planning/active-workstream to exist after Q-B legacy fallback; "
        "the file-write path should have proceeded"
    )
