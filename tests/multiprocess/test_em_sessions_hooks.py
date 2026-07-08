"""Deterministic per-hook harness for Phase 12 (HOOK-01/02/04).

Invokes the hook SCRIPTS directly via `python3 <path>` with synthetic hook
JSON piped on stdin — this is distinct from
tests/multiprocess/test_hook_e2e_delivery.py (Plan 12-02), which proves the
full A-to-B session-to-session delivery pipeline through Claude Code's actual
hook wiring. This file only proves each hook script's own contract in
isolation: gate on/off, message surfacing + consume-on-surface, empty
mailbox, and graceful degradation when `em-proj` is absent or failing
(HOOK-04).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from tests.conftest import EM_PROJ_BIN, TEST_DB

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SESSION_START_HOOK = REPO_ROOT / "scripts" / "hooks" / "session_start.py"
USER_PROMPT_SUBMIT_HOOK = REPO_ROOT / "scripts" / "hooks" / "user_prompt_submit.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_hook(
    hook_path: Path,
    payload: dict,  # type: ignore[type-arg]
    gate_on: bool = True,
    env_overrides: dict | None = None,  # type: ignore[type-arg]
    timeout: float = 10.0,
) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    """Invoke a hook script directly, piping `payload` as its stdin JSON.

    Strips any ambient EM_SESSIONS_AUTOSTART value first for hermetic tests,
    then sets it to "1" only when gate_on is True.
    """
    child_env = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB)}
    child_env.pop("EM_SESSIONS_AUTOSTART", None)
    if gate_on:
        child_env["EM_SESSIONS_AUTOSTART"] = "1"
    if env_overrides:
        child_env.update(env_overrides)
    return subprocess.run(
        ["python3", str(hook_path)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=child_env,
        timeout=timeout,
    )


def _unique_session_id() -> str:
    """Generate a unique session_id for test isolation.

    Mirrors tests/multiprocess/test_message_delivery.py's helper exactly —
    a uuid4 suffix on top of pid + time_ns prevents rapid successive calls
    from colliding on a coarse-granularity clock (macOS).
    """
    return f"test-sess-{os.getpid()}-{time.time_ns()}-{uuid.uuid4().hex[:8]}"


def _stop_session_daemon(session_id: str) -> None:
    """Best-effort daemon teardown so no spawned process survives a test."""
    env = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": session_id,
    }
    subprocess.run(
        [EM_PROJ_BIN, "session", "stop"],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Task 1 — HOOK-01 session_start.py gate on/off
# ---------------------------------------------------------------------------


def test_session_start_hook_registers_and_starts_daemon_when_gated_on(clean_db) -> None:  # type: ignore[no-untyped-def]
    session_id = _unique_session_id()
    payload = {"session_id": session_id}
    result = _run_hook(SESSION_START_HOOK, payload, gate_on=True)
    assert result.returncode == 0
    assert result.stdout == ""
    try:
        assert clean_db.exists(f"state:session:{session_id}") == 1
        # The detached daemon child writes its own HASH record asynchronously
        # (see test_daemon_lifecycle.py::test_daemon_start_detaches) — poll
        # briefly rather than asserting immediately.
        deadline = time.monotonic() + 3.0
        daemon_exists = False
        while time.monotonic() < deadline:
            if clean_db.exists(f"daemon:{session_id}") == 1:
                daemon_exists = True
                break
            time.sleep(0.1)
        assert daemon_exists, f"daemon:{session_id} HASH absent after 3s poll"
    finally:
        _stop_session_daemon(session_id)


def test_session_start_hook_noop_when_gate_off(clean_db) -> None:  # type: ignore[no-untyped-def]
    session_id = _unique_session_id()
    payload = {"session_id": session_id}
    result = _run_hook(SESSION_START_HOOK, payload, gate_on=False)
    assert result.returncode == 0
    assert result.stdout == ""
    assert clean_db.exists(f"state:session:{session_id}") == 0
    assert clean_db.exists(f"daemon:{session_id}") == 0
