"""Multi-process race tests for `em-proj state claim-list` (Plan 05-03).

Uses real ``em-proj`` subprocess invocations against db=15 (via ``EM_PROJ_REDIS_DB``
injection) to prove claim-list correctness under concurrent access.

Covers two scenarios:
  SC-CL-1 (concurrent list): one child claims an area then calls claim-list --json,
    the other just calls claim-list --json — both exit 0, valid JSON.
    The holder's output includes the claimed area (session_id in items).
  SC-CL-2 (empty concurrent list): two children call claim-list --json with no
    claims held — both exit 0, both return empty items array.

Design invariants (Phase 1 RESEARCH Pitfalls, carried forward):
  - subprocess.Popen NOT multiprocessing.Process (#6 -- fork+exec macOS safety)
  - .communicate(timeout=) NOT .wait() (#2 -- pipe-buffer deadlock)
  - EM_PROJ_REDIS_DB=15 in child env (#4 -- never writes to prod db=0)

Verb name: ``claim-list`` (hyphenated form registered in Plan 05-03 Task 1).

TTL constraint:
  The claim verb enforces MIN_TTL=60 (range 60-86400 seconds). Tests use
  the default TTL (1800s) to avoid specifying --ttl explicitly.

Import pattern:
  from tests.conftest import EM_PROJ_BIN, TEST_DB, RaceResult
  NOT the bare ``from conftest import ...`` form.
"""
from __future__ import annotations

import json
import os
import subprocess

from tests.conftest import EM_PROJ_BIN, TEST_DB, RaceResult  # noqa: F401


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _run(argv: list[str], extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run an em-proj command via subprocess.run, capturing stdout+stderr.

    Always injects ``EM_PROJ_REDIS_DB=TEST_DB`` so the child targets db=15.
    ``extra_env`` entries are merged on top.
    """
    env = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB), **(extra_env or {})}
    return subprocess.run(argv, capture_output=True, text=True, env=env)


# ---------------------------------------------------------------------------
# Test 1 -- SC-CL-1: concurrent claim-list where one child has a claim
# ---------------------------------------------------------------------------


def test_claim_list_concurrent(clean_db) -> None:
    """One child claims an area then calls claim-list --json; the other just calls
    claim-list --json concurrently. Both exit 0, return valid JSON.

    The holder's output must include an item containing its own session_id.

    SC-CL-1: concurrent claim-list calls are read-only and cannot corrupt Redis.
    """
    session_a = "session-claim-list-A"
    env_a = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": session_a,
    }
    env_b = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": "session-claim-list-B",
    }

    # Claim the area as session_a before the concurrent list calls.
    acquire_result = _run(
        [EM_PROJ_BIN, "state", "claim", "list-test-area"],
        extra_env={"CLAUDE_CODE_SESSION_ID": session_a},
    )
    assert acquire_result.returncode == 0, (
        f"Claim acquire failed: rc={acquire_result.returncode} "
        f"stderr={acquire_result.stderr!r}"
    )

    # Both children call claim-list concurrently.
    proc_a = subprocess.Popen(
        [EM_PROJ_BIN, "state", "claim-list", "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env_a,
    )
    proc_b = subprocess.Popen(
        [EM_PROJ_BIN, "state", "claim-list", "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env_b,
    )

    try:
        stdout_a, stderr_a = proc_a.communicate(timeout=10.0)
    except subprocess.TimeoutExpired:
        proc_a.kill()
        proc_a.communicate()
        raise AssertionError("Child A (claim-list) did not exit within 10s")

    try:
        stdout_b, stderr_b = proc_b.communicate(timeout=10.0)
    except subprocess.TimeoutExpired:
        proc_b.kill()
        proc_b.communicate()
        raise AssertionError("Child B (claim-list) did not exit within 10s")

    # Both must exit 0.
    assert proc_a.returncode == 0, (
        f"Child A claim-list failed: rc={proc_a.returncode} "
        f"stderr={stderr_a!r} stdout={stdout_a!r}"
    )
    assert proc_b.returncode == 0, (
        f"Child B claim-list failed: rc={proc_b.returncode} "
        f"stderr={stderr_b!r} stdout={stdout_b!r}"
    )

    # Both must return valid JSON.
    try:
        data_a = json.loads(stdout_a)
    except json.JSONDecodeError as e:
        raise AssertionError(f"Child A output is not valid JSON: {e!r}\nOutput: {stdout_a!r}") from e

    try:
        data_b = json.loads(stdout_b)
    except json.JSONDecodeError as e:
        raise AssertionError(f"Child B output is not valid JSON: {e!r}\nOutput: {stdout_b!r}") from e

    # Both must have the expected envelope shape.
    assert data_a.get("status") == "ok", (
        f"Child A envelope status not 'ok': {data_a!r}"
    )
    assert data_b.get("status") == "ok", (
        f"Child B envelope status not 'ok': {data_b!r}"
    )
    assert "items" in data_a.get("data", {}), (
        f"Child A missing 'items' in data: {data_a!r}"
    )
    assert "items" in data_b.get("data", {}), (
        f"Child B missing 'items' in data: {data_b!r}"
    )

    # The claim was acquired by session_a. Both children share the same project_hash
    # (they inherit the test process's cwd), so claim_list_by_prefix scopes to the
    # same namespace. Child A's output must include the claim.
    items_a = data_a["data"]["items"]
    assert any(item.get("session_id") == session_a for item in items_a), (
        f"Expected session_id={session_a!r} in child A claim-list items; "
        f"got items: {items_a!r}"
    )

    # Verify all 5 claim fields are present in each item (no redaction for claims).
    for item in items_a:
        assert "session_id" in item, f"Missing session_id in claim item: {item!r}"
        assert "project_hash" in item, f"Missing project_hash in claim item: {item!r}"
        assert "claimed_at" in item, f"Missing claimed_at in claim item: {item!r}"
        assert "expires_at" in item, f"Missing expires_at in claim item: {item!r}"
        # reason may be None (null) — key must still exist
        assert "reason" in item, f"Missing reason key in claim item: {item!r}"


# ---------------------------------------------------------------------------
# Test 2 -- SC-CL-2: concurrent claim-list with no claims held
# ---------------------------------------------------------------------------


def test_claim_list_empty_concurrent(clean_db) -> None:
    """Two children call claim-list --json concurrently with no claims held.

    Both must exit 0 and return {"status":"ok","data":{"items":[]}}.

    SC-CL-2: empty list is a valid result, not an error.
    """
    env = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        # Inject a session id so the verb does not hit the anonymous-refusal gate
        # (claim-list itself does not check CLAUDE_CODE_SESSION_ID, but it calls
        # claim_list_by_prefix which calls resolve_session_id; the pid fallback
        # is sufficient, but an explicit env value keeps the test intent clear).
        "CLAUDE_CODE_SESSION_ID": "session-claim-list-empty",
    }
    cmd = [EM_PROJ_BIN, "state", "claim-list", "--json"]

    proc1 = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    proc2 = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )

    try:
        stdout1, stderr1 = proc1.communicate(timeout=10.0)
    except subprocess.TimeoutExpired:
        proc1.kill()
        proc1.communicate()
        raise AssertionError("Child 1 (claim-list empty) did not exit within 10s")

    try:
        stdout2, stderr2 = proc2.communicate(timeout=10.0)
    except subprocess.TimeoutExpired:
        proc2.kill()
        proc2.communicate()
        raise AssertionError("Child 2 (claim-list empty) did not exit within 10s")

    assert proc1.returncode == 0, (
        f"Child 1 claim-list (empty) failed: rc={proc1.returncode} stderr={stderr1!r}"
    )
    assert proc2.returncode == 0, (
        f"Child 2 claim-list (empty) failed: rc={proc2.returncode} stderr={stderr2!r}"
    )

    try:
        data1 = json.loads(stdout1)
    except json.JSONDecodeError as e:
        raise AssertionError(f"Child 1 output not valid JSON: {e!r}\nOutput: {stdout1!r}") from e

    try:
        data2 = json.loads(stdout2)
    except json.JSONDecodeError as e:
        raise AssertionError(f"Child 2 output not valid JSON: {e!r}\nOutput: {stdout2!r}") from e

    assert data1 == {"schema_version": "1", "status": "ok", "data": {"items": []}}, (
        f"Child 1 claim-list empty: unexpected envelope: {data1!r}"
    )
    assert data2 == {"schema_version": "1", "status": "ok", "data": {"items": []}}, (
        f"Child 2 claim-list empty: unexpected envelope: {data2!r}"
    )
