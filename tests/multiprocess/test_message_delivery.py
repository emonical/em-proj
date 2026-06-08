"""Multi-process harness for `em-proj message` — TEST-04 3×3 delivery matrix.

Phase 1 design invariants (carried forward):
  - subprocess.Popen NOT multiprocessing.Process (macOS fork+exec safety)
  - .communicate(timeout=15) NOT .wait() (pipe-buffer deadlock prevention)
  - EM_PROJ_REDIS_DB=15 in every child env (never writes to prod db=0)
  - Distinct session_id per test (prevents same-session upsert masking)

Wave 0 state (this file as created by 10-01):
  - 9 mailbox-path cells (3 patterns × 3 scopes) are skip-stubs flagged for Wave 2
    (10-03), where the send/broadcast/subscribe CLI verbs ship and the bodies are
    activated against the harness helpers below.
  - 3 live-path cells (one per pattern) are skip-stubs flagged for Phase 11, where
    the listener daemon (`em-proj session listen`) ships.

Helpers (_unique_session_id, _register_session_for_test, _send_via_cli,
_inbox_via_cli) are defined now so Wave 2 fills test bodies without re-deriving
the harness.
"""
from __future__ import annotations

import json
import os
import subprocess
import time

import pytest
import redis as redis_module

from tests.conftest import EM_PROJ_BIN, TEST_DB

# ---------------------------------------------------------------------------
# Helpers — copied verbatim from test_session_registry.py
# ---------------------------------------------------------------------------


def _unique_session_id() -> str:
    """Generate a unique session_id for test isolation."""
    return f"test-sess-{os.getpid()}-{time.time_ns()}"


def _register_session_for_test(session_id: str, client: redis_module.Redis) -> dict:
    """Write a test session record to Redis db=15 with the test runner's live pid.

    The runner's own pid/proc_start/boot_id make is_holder_stale return False, so the
    registered session counts as live for session_list()/session_show() during the test.
    Override project_hash / upstream_identity on the returned key afterward to build
    cross-scope scenarios (per 10-RESEARCH "For scope testing").
    """
    import em_proj.session._ops as ops
    from em_proj.identity import current_process_composite, resolve_upstream_identity

    composite = current_process_composite()
    upstream_identity = resolve_upstream_identity()
    cwd = os.getcwd()
    now = time.time()
    redis_key = ops.KEY_PREFIX + session_id

    client.hset(
        redis_key,
        mapping={
            "session_id": session_id,
            "project_hash": composite["project_hash"],
            "upstream_identity": upstream_identity,
            "pid": str(composite["pid"]),
            "proc_start_epoch": str(composite["proc_start_epoch"]),
            "boot_id": composite["boot_id"],
            "cwd": cwd,
            "registered_at": str(now),
            "last_heartbeat": str(now),
        },
    )
    client.expire(redis_key, ops.TTL_DEFAULT)
    return {
        "session_id": session_id,
        "project_hash": composite["project_hash"],
        "upstream_identity": upstream_identity,
        "pid": composite["pid"],
        "proc_start_epoch": composite["proc_start_epoch"],
        "boot_id": composite["boot_id"],
        "cwd": cwd,
        "registered_at": now,
        "last_heartbeat": now,
    }


def _send_via_cli(args: list[str], sender_session_id: str) -> tuple[subprocess.Popen, str, str]:
    """Run `em-proj message <args>` via subprocess.Popen + .communicate(timeout=15).

    Returns (proc, stdout, stderr). The caller asserts on proc.returncode.
    """
    child_env = {
        **os.environ,
        "CLAUDE_CODE_SESSION_ID": sender_session_id,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
    }
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "message"] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    stdout, stderr = proc.communicate(timeout=15)
    return proc, stdout, stderr


def _inbox_via_cli(session_id: str) -> list:
    """Run `em-proj message inbox --json --peek` for session_id; return the messages list."""
    child_env = {
        **os.environ,
        "CLAUDE_CODE_SESSION_ID": session_id,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
    }
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "message", "inbox", "--json", "--peek"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    stdout, _stderr = proc.communicate(timeout=15)
    envelope = json.loads(stdout)
    data = envelope.get("data", [])
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# Mailbox-path cells — 3 patterns × 3 scopes (skip-stubs; activated in Wave 2)
# ---------------------------------------------------------------------------

_WAVE2_SKIP = "message send verb not yet available — enable after Wave 2 (10-03-PLAN)"


def test_directed_machine_scope(clean_db, redis_precheck) -> None:
    pytest.skip(_WAVE2_SKIP)


def test_directed_project_scope(clean_db, redis_precheck) -> None:
    pytest.skip(_WAVE2_SKIP)


def test_directed_upstream_scope(clean_db, redis_precheck) -> None:
    pytest.skip(_WAVE2_SKIP)


def test_broadcast_machine_scope(clean_db, redis_precheck) -> None:
    pytest.skip(_WAVE2_SKIP)


def test_broadcast_project_scope(clean_db, redis_precheck) -> None:
    pytest.skip(_WAVE2_SKIP)


def test_broadcast_upstream_scope(clean_db, redis_precheck) -> None:
    pytest.skip(_WAVE2_SKIP)


def test_topic_machine_scope(clean_db, redis_precheck) -> None:
    pytest.skip(_WAVE2_SKIP)


def test_topic_project_scope(clean_db, redis_precheck) -> None:
    pytest.skip(_WAVE2_SKIP)


def test_topic_upstream_scope(clean_db, redis_precheck) -> None:
    pytest.skip(_WAVE2_SKIP)


# ---------------------------------------------------------------------------
# Live-path cells — pub/sub delivery (skip-stubs; activated in Phase 11)
# ---------------------------------------------------------------------------

_PHASE11_SKIP = (
    "Phase 11 listener daemon not yet available — "
    "enable once 'em-proj session listen' ships"
)


def test_live_delivery_directed(clean_db, redis_precheck) -> None:
    pytest.skip(_PHASE11_SKIP)


def test_live_delivery_broadcast(clean_db, redis_precheck) -> None:
    pytest.skip(_PHASE11_SKIP)


def test_live_delivery_topic(clean_db, redis_precheck) -> None:
    pytest.skip(_PHASE11_SKIP)
