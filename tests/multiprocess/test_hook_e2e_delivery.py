"""HOOK-03 A-to-B end-to-end delivery proof (directed/broadcast/topic).

This is the REAL two-session pipeline: session A sends via the actual
`em-proj message` CLI, and session B's actual `user_prompt_submit.py` hook
script (from Plan 12-01, unmodified) surfaces it. This is distinct from
Plan 12-01's tests/multiprocess/test_em_sessions_hooks.py, which proves each
hook script's own contract in isolation against a synthetic single-session
seed (a message written directly to Redis, not sent via the CLI).

The genuinely-live two-Claude-Code-session demonstration — two real CC
sessions, both opted in via EM_SESSIONS_AUTOSTART=1, session A sending
`em-proj message send --to <B_session_id> "hello"` and observing the
greeting surface as additional context in session B's next turn — cannot be
scripted (a live CC session is not a test fixture) and is documented as a
separate, manual, non-gating validation step in 12-02-PLAN.md's
<verification> section. This file automates everything that CAN be
automated: the send-to-mailbox-to-hook-surface mechanism.
"""
from __future__ import annotations

import os
import subprocess
import time

import pytest

import em_proj.session._ops as ops
from em_proj.identity import current_process_composite, resolve_upstream_identity

from tests.conftest import EM_PROJ_BIN, TEST_DB
from tests.multiprocess.test_em_sessions_hooks import (
    _run_hook,
    _unique_session_id,
    USER_PROMPT_SUBMIT_HOOK,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register_session_for_test(session_id: str, client) -> dict:  # type: ignore[no-untyped-def]
    """Write a test session record to Redis db=15 with the test runner's live pid.

    Copied verbatim from tests/multiprocess/test_message_delivery.py — the
    runner's own pid/proc_start/boot_id make is_holder_stale return False, so
    the registered session counts as live for session_list()/session_show()
    during the test.
    """
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


def _send_via_cli(
    args: list[str], sender_session_id: str, timeout: float = 15.0
) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    """Run `em-proj message <args>` as sender_session_id via subprocess.run."""
    child_env = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": sender_session_id,
    }
    return subprocess.run(
        [EM_PROJ_BIN, "message"] + args,
        capture_output=True,
        text=True,
        env=child_env,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Task 1 — HOOK-03 A-to-B proof
# ---------------------------------------------------------------------------


def test_hook_e2e_directed_delivery(clean_db, redis_precheck) -> None:  # type: ignore[no-untyped-def]
    a_id, b_id = _unique_session_id(), _unique_session_id()
    _register_session_for_test(a_id, clean_db)
    _register_session_for_test(b_id, clean_db)

    body = "e2e-directed-hello"
    send_result = _send_via_cli(["send", "--to", b_id, body], a_id)
    assert send_result.returncode == 0, (
        f"message send exited {send_result.returncode}; "
        f"stderr={send_result.stderr!r}; stdout={send_result.stdout!r}"
    )

    hook_result = _run_hook(USER_PROMPT_SUBMIT_HOOK, {"session_id": b_id}, gate_on=True)
    assert hook_result.returncode == 0
    assert body in hook_result.stdout
    assert a_id in hook_result.stdout


def test_hook_e2e_broadcast_delivery(clean_db, redis_precheck) -> None:  # type: ignore[no-untyped-def]
    a_id, b_id = _unique_session_id(), _unique_session_id()
    _register_session_for_test(a_id, clean_db)
    _register_session_for_test(b_id, clean_db)

    body = "e2e-broadcast-hello"
    send_result = _send_via_cli(["broadcast", "--scope", "machine", body], a_id)
    assert send_result.returncode == 0, (
        f"message broadcast exited {send_result.returncode}; "
        f"stderr={send_result.stderr!r}; stdout={send_result.stdout!r}"
    )

    hook_result = _run_hook(USER_PROMPT_SUBMIT_HOOK, {"session_id": b_id}, gate_on=True)
    assert hook_result.returncode == 0
    assert body in hook_result.stdout


def test_hook_e2e_topic_delivery(clean_db, redis_precheck) -> None:  # type: ignore[no-untyped-def]
    a_id, b_id = _unique_session_id(), _unique_session_id()
    _register_session_for_test(a_id, clean_db)
    _register_session_for_test(b_id, clean_db)

    topic = "e2e-topic-" + b_id[-8:]
    subscribe_result = _send_via_cli(["subscribe", topic, "--scope", "machine"], b_id)
    assert subscribe_result.returncode == 0, (
        f"message subscribe exited {subscribe_result.returncode}; "
        f"stderr={subscribe_result.stderr!r}; stdout={subscribe_result.stdout!r}"
    )

    body = "e2e-topic-hello"
    send_result = _send_via_cli(
        ["send", "--topic", topic, "--scope", "machine", body], a_id
    )
    assert send_result.returncode == 0, (
        f"message send --topic exited {send_result.returncode}; "
        f"stderr={send_result.stderr!r}; stdout={send_result.stdout!r}"
    )

    hook_result = _run_hook(USER_PROMPT_SUBMIT_HOOK, {"session_id": b_id}, gate_on=True)
    assert hook_result.returncode == 0
    assert body in hook_result.stdout
    assert f"[topic:{topic}]" in hook_result.stdout
