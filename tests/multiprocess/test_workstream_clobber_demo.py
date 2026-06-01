"""SC#3 side-by-side demo: old-path clobber vs. new-path resolution (Plan 06-02).

This file IS the Phase 6 SC#3 human-runnable demo. Run both tests together:

    bash scripts/test.sh multiprocess -k clobber_demo

Output reads:
    test_old_path_direct_file_write_clobbers PASSED
    test_new_path_through_gsd_sdk_refuses_loser PASSED

Narrative:
  test_old_path_... → reproduces the pre-Phase-6 pain: two sessions write
    .planning/active-workstream in parallel; whoever finishes last wins and
    the other session's choice is silently overwritten. No structured signal.

  test_new_path_... → shows the Phase 6 resolution: gsd-sdk workstream.set
    shells out to em-proj state claim before writing the file. The Lua
    refresh-or-take script serializes the two attempts; one wins, the other
    receives a structured {error: "held_by_another", holder: {...}} response
    rather than a silent clobber.

Q-F decision (locked): pytest fixture is sufficient for SC#3; no separate
shell script is needed. bash scripts/test.sh multiprocess -k clobber_demo
runs both tests in ~5 seconds and is human-readable.

Reference: ROADMAP Phase 6 Success Criteria #3 (SC#3).
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

from tests.conftest import TEST_DB


# ---------------------------------------------------------------------------
# Module-level helpers (self-contained per project convention — copied from
# test_workstream_consumer_race.py; NOT extracted to a shared module)
# ---------------------------------------------------------------------------


def _global_path() -> str:
    """Return PATH with venv-resident em-proj excluded.

    When pytest runs under ``uv run pytest``, the venv bin directory is
    prepended to PATH.  If the venv carries a Phase 2 em-proj (which lacks the
    ``state claim`` verb), gsd-sdk's ``spawnSync('em-proj', ...)`` finds that
    stale binary first, exits 2 (unknown verb), falls through the exit-code
    checks and proceeds to the legacy file-write path — causing both race
    contestants to return ``{set: True}``.

    Strips PATH entries that live inside a ``.venv`` subtree AND provide
    ``em-proj``.  Other ``.venv`` directories (python, pip, etc.) are kept.
    """
    # Determine the em-proj bin name from conftest constant (EM_PROJ_BIN = "em-proj").
    _em_proj_bin = "em-proj"
    keep: list[str] = []
    for entry in os.environ.get("PATH", "").split(":"):
        if not entry:
            continue
        p = Path(entry)
        if ".venv" in p.parts and (p / _em_proj_bin).exists():
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
    """Return the full Redis key for a claim on ``area``."""
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
# Test 1 -- SC#3 baseline: old-path direct file write clobbers (no guard)
# ---------------------------------------------------------------------------


def test_old_path_direct_file_write_clobbers(tmp_path) -> None:
    """Baseline: two parallel writes to .planning/active-workstream clobber.

    Reproduces the pre-Phase-6 behavior by writing directly to the pointer file
    the way gsd-sdk's setActiveWorkstream did before the claim gate was added
    (writeFileSync with no concurrency guard).

    Two python3 subprocesses write different workstream names in parallel.
    Whoever finishes last wins; the other session's choice is silently lost.

    No structured "you were displaced" signal exists in this path — that is
    the clobber pattern Phase 6 fixes.

    The assertion is intentionally weak: ``final in (A, B)``. The narrative IS
    the test. Both outcomes are valid since we are demonstrating the race, not
    asserting a specific winner.
    """
    planning = tmp_path / ".planning"
    planning.mkdir()
    pointer = planning / "active-workstream"

    # Simulate two sessions writing in parallel via subprocess (not
    # multiprocessing.Process — fork+exec macOS safety; RESEARCH Pitfall #6).
    script_a = f"""
from pathlib import Path
Path({str(pointer)!r}).write_text("workstream-A\\n")
"""
    script_b = f"""
from pathlib import Path
Path({str(pointer)!r}).write_text("workstream-B\\n")
"""
    p_a = subprocess.Popen(["python3", "-c", script_a])
    p_b = subprocess.Popen(["python3", "-c", script_b])
    p_a.wait(timeout=5)
    p_b.wait(timeout=5)

    # The clobber: whatever wrote last wins; the other session's choice is lost.
    # No structured "you were displaced" signal exists — that is the point.
    final = pointer.read_text().strip()
    assert final in ("workstream-A", "workstream-B"), (
        "Old-path direct write should produce one of the two values "
        f"(whichever finished last). Got: {final!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 -- SC#3 resolution: new-path through gsd-sdk refuses the loser
# ---------------------------------------------------------------------------


def test_new_path_through_gsd_sdk_refuses_loser(clean_db, tmp_path) -> None:
    """Resolution: same race through gsd-sdk produces a deterministic structured outcome.

    Two parallel gsd-sdk workstream.set calls with distinct CLAUDE_CODE_SESSION_ID
    values race against the same project. The Phase 6 claim gate (em-proj state
    claim in sdk/dist/query/workstream.js) serializes the attempts at the Redis
    Lua layer:

      - Exactly one process gets {set: true} (winner took the claim)
      - Exactly one process gets {error: "held_by_another", holder: {...}}

    The structured signal IS the resolution: the loser is explicitly told it was
    refused (and by whom), rather than silently having its write overwritten.

    This is the side-by-side contrast to test_old_path_direct_file_write_clobbers.
    Running both tests back-to-back via:
        bash scripts/test.sh multiprocess -k clobber_demo
    demonstrates SC#3's "human-runnable in ~5 seconds" requirement.

    Note: cwd=str(tmp_path) is passed as a subprocess.Popen kwarg (not --cwd
    flag) so gsd-sdk's process.cwd() resolves to tmp_path and the TS handler
    with the Phase 6 claim gate is invoked rather than the CJS fallback.
    """
    _setup_workstream_dir(tmp_path, "demo-ws")

    safe_path = _global_path()
    base_env = {**os.environ, "PATH": safe_path, "EM_PROJ_REDIS_DB": str(TEST_DB)}
    env_a = {**base_env, "CLAUDE_CODE_SESSION_ID": "demo-A"}
    env_b = {**base_env, "CLAUDE_CODE_SESSION_ID": "demo-B"}

    cmd = ["gsd-sdk", "query", "workstream.set", "demo-ws", "--raw"]

    # Tight launch loop -- no sleep between spawns; this is the race.
    p_a = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env_a,
        cwd=str(tmp_path),
    )
    p_b = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env_b,
        cwd=str(tmp_path),
    )

    out_a, err_a = p_a.communicate(timeout=15)
    out_b, err_b = p_b.communicate(timeout=15)

    try:
        a = json.loads(out_a)
    except json.JSONDecodeError:
        pytest.fail(
            f"Process A stdout is not valid JSON.\nstdout={out_a!r}\nstderr={err_a[:200]!r}"
        )
    try:
        b = json.loads(out_b)
    except json.JSONDecodeError:
        pytest.fail(
            f"Process B stdout is not valid JSON.\nstdout={out_b!r}\nstderr={err_b[:200]!r}"
        )

    winners = [d for d in (a, b) if d.get("set") is True]
    losers = [d for d in (a, b) if d.get("error") == "held_by_another"]

    assert len(winners) == 1, (
        f"Expected exactly one winner (set: true); got {a}, {b}"
    )
    assert len(losers) == 1, (
        f"Expected exactly one held_by_another loser; got {a}, {b}"
    )

    # The structured signal — the resolution: the loser learns it was refused,
    # not silently clobbered.
    assert "holder" in losers[0], (
        f"Loser response must contain 'holder' dict; got: {losers[0]}"
    )
    if losers[0]["holder"] is not None:
        assert losers[0]["holder"]["session_id"] in ("demo-A", "demo-B"), (
            f"Loser holder.session_id must be one of the two demo sessions; "
            f"got: {losers[0]['holder']['session_id']!r}"
        )
