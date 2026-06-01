"""Multi-process race tests for `em-proj state lock-list` (Plan 05-03).

Uses real ``em-proj`` subprocess invocations against db=15 (via ``EM_PROJ_REDIS_DB``
injection) to prove lock-list correctness under concurrent access.

Covers two scenarios:
  SC-LL-1 (concurrent list): one child holds a lock then lists, the other just
    lists — both exit 0, the holder's output contains its own session_id.
  SC-LL-2 (empty concurrent list): two children both call lock-list --json with
    no locks held — both exit 0, both return empty items array.

Design invariants (Phase 1 RESEARCH Pitfalls, carried forward):
  - subprocess.Popen NOT multiprocessing.Process (#6 -- fork+exec macOS safety)
  - .communicate(timeout=) NOT .wait() (#2 -- pipe-buffer deadlock)
  - EM_PROJ_REDIS_DB=15 in child env (#4 -- never writes to prod db=0)

Verb name: ``lock-list`` (hyphenated form registered in Plan 05-03 Task 1).

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
# Test 1 -- SC-LL-1: concurrent list where one child holds a lock
# ---------------------------------------------------------------------------


def test_lock_list_concurrent(clean_db) -> None:
    """One child acquires a lock then calls lock-list --json; the other just calls
    lock-list --json concurrently. Both exit 0, return valid JSON.

    The lock-holder's output must include an item containing its own session_id.
    The non-holder's output may or may not include the lock (SCAN is eventually
    consistent after expiry; while the lock is live it should appear).

    SC-LL-1: concurrent list calls are read-only and cannot corrupt Redis or
    each other's results.
    """
    session_a = "session-lock-list-A"
    env_a = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": session_a,
    }
    env_b = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": "session-lock-list-B",
    }

    # Child A: acquire a lock with an explicit --ttl 30 so the test is not
    # sensitive to slow Redis.  The two lock-list children must finish within
    # 30s combined; the communicate() calls time out at 10s each, so 30s is
    # a safe margin.  WR-04: explicit TTL avoids relying on the 60s default.
    acquire_result = _run(
        [EM_PROJ_BIN, "state", "lock", "list-test-lock", "--ttl", "30"],
        extra_env={"CLAUDE_CODE_SESSION_ID": session_a},
    )
    assert acquire_result.returncode == 0, (
        f"Lock acquire failed: rc={acquire_result.returncode} "
        f"stderr={acquire_result.stderr!r}"
    )

    # Release the lock at the end of the test regardless of outcome.
    # WR-04: explicit release ensures the key doesn't linger and prevents
    # the 30s TTL lock from polluting subsequent test runs in the same session.
    try:
        # Now both children call lock-list concurrently.
        proc_a = subprocess.Popen(
            [EM_PROJ_BIN, "state", "lock-list", "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env_a,
        )
        proc_b = subprocess.Popen(
            [EM_PROJ_BIN, "state", "lock-list", "--json"],
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
            raise AssertionError("Child A (lock-list) did not exit within 10s")

        try:
            stdout_b, stderr_b = proc_b.communicate(timeout=10.0)
        except subprocess.TimeoutExpired:
            proc_b.kill()
            proc_b.communicate()
            raise AssertionError("Child B (lock-list) did not exit within 10s")

        # Both must exit 0.
        assert proc_a.returncode == 0, (
            f"Child A lock-list failed: rc={proc_a.returncode} "
            f"stderr={stderr_a!r} stdout={stdout_a!r}"
        )
        assert proc_b.returncode == 0, (
            f"Child B lock-list failed: rc={proc_b.returncode} "
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

        # The lock was acquired by session_a; it must appear in child A's output.
        items_a = data_a["data"]["items"]
        assert any(item.get("session_id") == session_a for item in items_a), (
            f"Expected session_id={session_a!r} in child A lock-list items; "
            f"got items: {items_a!r}"
        )

        # Verify no boot_id or proc_start_epoch leaked into any item (T-5-03-01).
        for item in items_a:
            assert "boot_id" not in item, (
                f"boot_id leaked in lock-list output item: {item!r}"
            )
            assert "proc_start_epoch" not in item, (
                f"proc_start_epoch leaked in lock-list output item: {item!r}"
            )

    finally:
        # Explicit release so the lock doesn't linger for 30s after test exit.
        # clean_db FLUSHDB would also clean it, but explicit release is faster
        # and guards against the case where clean_db teardown is delayed.
        _run(
            [EM_PROJ_BIN, "state", "unlock", "list-test-lock"],
            extra_env={"CLAUDE_CODE_SESSION_ID": session_a},
        )


# ---------------------------------------------------------------------------
# Test 2 -- SC-LL-2: concurrent lock-list with no locks held
# ---------------------------------------------------------------------------


def test_lock_list_empty_concurrent(clean_db) -> None:
    """Two children call lock-list --json concurrently with no locks held.

    Both must exit 0 and return {"status":"ok","data":{"items":[]}}.

    SC-LL-2: empty list is a valid result, not an error.
    """
    env = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB)}
    cmd = [EM_PROJ_BIN, "state", "lock-list", "--json"]

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
        raise AssertionError("Child 1 (lock-list empty) did not exit within 10s")

    try:
        stdout2, stderr2 = proc2.communicate(timeout=10.0)
    except subprocess.TimeoutExpired:
        proc2.kill()
        proc2.communicate()
        raise AssertionError("Child 2 (lock-list empty) did not exit within 10s")

    assert proc1.returncode == 0, (
        f"Child 1 lock-list (empty) failed: rc={proc1.returncode} stderr={stderr1!r}"
    )
    assert proc2.returncode == 0, (
        f"Child 2 lock-list (empty) failed: rc={proc2.returncode} stderr={stderr2!r}"
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
        f"Child 1 lock-list empty: unexpected envelope: {data1!r}"
    )
    assert data2 == {"schema_version": "1", "status": "ok", "data": {"items": []}}, (
        f"Child 2 lock-list empty: unexpected envelope: {data2!r}"
    )
