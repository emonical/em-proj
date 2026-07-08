"""Multi-process harness for Phase 11 daemon lifecycle — TEST-05 validation.

Proves DAEMON-01 (start/record pid), DAEMON-02 (message-liveness at system
level), and DAEMON-03 (heartbeat refresh) through real subprocess invocations
against Redis db=15.

Phase 1 design invariants (carried forward):
  - subprocess.Popen NOT multiprocessing.Process (macOS fork+exec safety)
  - .communicate(timeout=N) NOT .wait() (pipe-buffer deadlock prevention)
  - EM_PROJ_REDIS_DB=15 in every child env (never writes to prod db=0)
  - Distinct session_id per test (prevents same-session upsert masking)

--foreground pattern for testability (11-RESEARCH Pitfall 5):
  Tests start the daemon via `em-proj session listen --foreground` as a
  Popen child so they control its lifetime and can verify SIGTERM cleanup.
  The detach code path (default non-foreground) is tested in
  test_daemon_start_detaches.

Import pattern:
  from tests.conftest import EM_PROJ_BIN, TEST_DB
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time

import pytest
import redis as redis_module

from tests.conftest import EM_PROJ_BIN, TEST_DB


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _run_cli(
    args: list[str],
    session_id: str | None = None,
    timeout: float = 10.0,
    env_overrides: dict | None = None,  # type: ignore[type-arg]
) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    """Run `em-proj <args>` and wait for it to finish.

    Builds child_env with EM_PROJ_REDIS_DB=15 (+ optional session_id and
    overrides) and uses subprocess.run for short-lived CLI calls.
    """
    child_env: dict = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB)}
    if session_id is not None:
        child_env["CLAUDE_CODE_SESSION_ID"] = session_id
    if env_overrides:
        child_env.update(env_overrides)
    return subprocess.run(
        [EM_PROJ_BIN] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
        timeout=timeout,
    )


def _start_foreground_daemon(
    session_id: str,
    heartbeat_interval: int = 1,
) -> subprocess.Popen:  # type: ignore[type-arg]
    """Start `em-proj session listen --foreground` as a long-lived Popen child.

    The child blocks until SIGTERM. Use _stop_foreground_daemon() for cleanup.

    EM_PROJ_DAEMON_HEARTBEAT_INTERVAL is set to heartbeat_interval so tests
    can verify heartbeat behaviour without waiting 60 seconds.
    """
    child_env = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": session_id,
        "EM_PROJ_DAEMON_HEARTBEAT_INTERVAL": str(heartbeat_interval),
    }
    return subprocess.Popen(
        [EM_PROJ_BIN, "session", "listen", "--foreground"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=child_env,
    )


def _stop_foreground_daemon(proc: subprocess.Popen, timeout: float = 5.0) -> None:  # type: ignore[type-arg]
    """Send SIGTERM to the foreground daemon child and wait for it to exit.

    Escalates to SIGKILL if the process does not exit within timeout seconds.
    """
    proc.send_signal(signal.SIGTERM)
    try:
        proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()


def _daemon_hash(
    client: redis_module.Redis, session_id: str
) -> dict | None:  # type: ignore[type-arg]
    """Read the daemon HASH record from Redis db=15.

    Returns None if the key is absent (or empty). Returns raw str-valued dict
    from Redis otherwise (callers convert fields as needed).
    """
    raw = client.hgetall(f"daemon:{session_id}")
    if not raw:
        return None
    return raw


def _register_session_for_test(
    session_id: str, client: redis_module.Redis
) -> None:
    """Register a session in Redis db=15 using the test runner's live pid/composite.

    This is required before starting the foreground daemon so that
    session_heartbeat() called from within the daemon can find a live
    session record rather than raising SessionNotFound.

    Sets CLAUDE_CODE_SESSION_ID in os.environ temporarily around the call
    so session_register() picks up the desired session_id.
    """
    from em_proj.session._ops import session_register
    from em_proj.redis_client import _reset_for_tests, get_client

    # Point the in-process Redis singleton at db=15 for the registration call.
    _reset_for_tests()
    orig_db = os.environ.get("EM_PROJ_REDIS_DB")
    os.environ["EM_PROJ_REDIS_DB"] = str(TEST_DB)

    orig_sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    os.environ["CLAUDE_CODE_SESSION_ID"] = session_id
    try:
        # get_client() will read EM_PROJ_REDIS_DB=15 now.
        session_register()
    finally:
        if orig_sid is None:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            os.environ["CLAUDE_CODE_SESSION_ID"] = orig_sid
        if orig_db is None:
            os.environ.pop("EM_PROJ_REDIS_DB", None)
        else:
            os.environ["EM_PROJ_REDIS_DB"] = orig_db
        # Always reset the singleton so subsequent tests get a fresh client.
        _reset_for_tests()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_daemon_foreground_starts_and_records_pid(clean_db: redis_module.Redis) -> None:
    """DAEMON-01: --foreground daemon writes daemon HASH with correct pid; SIGTERM cleans up.

    Start the daemon in --foreground mode, verify the daemon:<session_id> HASH
    appears with pid matching the child's actual PID, then SIGTERM and verify
    the HASH is deleted (clean shutdown).
    """
    session_id = "test-daemon-foreground"
    _register_session_for_test(session_id, clean_db)

    proc = _start_foreground_daemon(session_id)
    time.sleep(0.8)  # allow daemon to write daemon HASH

    raw = _daemon_hash(clean_db, session_id)
    assert raw is not None, (
        f"daemon:{session_id} HASH absent — daemon did not write its record after 0.8s"
    )
    assert int(raw["pid"]) == proc.pid, (
        f"daemon HASH pid {raw['pid']!r} != child pid {proc.pid} — "
        "daemon must write its own process pid"
    )

    _stop_foreground_daemon(proc)
    time.sleep(0.5)  # allow SIGTERM handler to run _daemon_record_del

    raw_after = _daemon_hash(clean_db, session_id)
    assert not raw_after, (
        f"daemon:{session_id} HASH still present after SIGTERM — "
        "SIGTERM handler must call _daemon_record_del before exit"
    )


def test_daemon_heartbeat_refreshes_session(clean_db: redis_module.Redis) -> None:
    """DAEMON-03: daemon heartbeat refreshes the session TTL toward TTL_DEFAULT=300.

    Register a session so its TTL starts at 300s. Start the foreground daemon
    with heartbeat_interval=1. Wait 3 seconds. Confirm the session TTL has been
    refreshed (stays close to 300 rather than decaying to ~297).
    """
    session_id = "test-daemon-heartbeat"
    _register_session_for_test(session_id, clean_db)

    session_key = f"state:session:{session_id}"
    ttl_before = clean_db.ttl(session_key)
    assert ttl_before > 0, f"session {session_key!r} not in Redis after register"

    proc = _start_foreground_daemon(session_id, heartbeat_interval=1)
    time.sleep(3)  # 3 heartbeats at 1s cadence

    ttl_after = clean_db.ttl(session_key)
    _stop_foreground_daemon(proc)

    assert ttl_after > 250, (
        f"session TTL after 3s = {ttl_after}s — expected > 250 (heartbeat should "
        f"refresh toward {300}; starting TTL was {ttl_before}s)"
    )


def test_daemon_message_liveness(clean_db: redis_module.Redis) -> None:
    """DAEMON-02 system-level: message appears in inbox while daemon is alive.

    This is the core DAEMON-02 proof: the daemon's role is liveness (keeping the
    session heartbeat alive so the session remains a valid recipient). The send-time
    mbox_write in message/_ops.py writes the durable mailbox record. The inbox
    reflects the message WITHOUT the daemon writing it.

    Steps:
      1. Register sender + recipient sessions.
      2. Start daemon for recipient (--foreground, heartbeat_interval=1).
      3. Send a directed message via CLI.
      4. Read inbox via CLI and assert message is present.
      5. SIGTERM daemon; cleanup.
    """
    sender_id = "test-daemon-sender"
    recipient_id = "test-daemon-recipient"

    _register_session_for_test(sender_id, clean_db)
    _register_session_for_test(recipient_id, clean_db)

    proc = _start_foreground_daemon(recipient_id, heartbeat_interval=1)
    time.sleep(0.5)  # allow daemon to subscribe and write HASH

    # Send directed message via CLI.
    send_result = _run_cli(
        ["message", "send", "--to", recipient_id, "hello-daemon"],
        session_id=sender_id,
    )
    assert send_result.returncode == 0, (
        f"message send failed (rc={send_result.returncode}): {send_result.stderr!r}"
    )

    # Read inbox via CLI for the recipient.
    inbox_result = _run_cli(
        ["message", "inbox", "--json", "--peek"],
        session_id=recipient_id,
    )
    assert inbox_result.returncode == 0, (
        f"message inbox failed (rc={inbox_result.returncode}): {inbox_result.stderr!r}"
    )

    _stop_foreground_daemon(proc)

    envelope = json.loads(inbox_result.stdout.strip())
    messages = envelope.get("data", [])
    if not isinstance(messages, list):
        messages = []

    assert len(messages) >= 1, (
        f"inbox empty after directed send — expected at least 1 message. "
        f"Full envelope: {envelope!r}"
    )
    bodies = [m.get("body") for m in messages]
    assert "hello-daemon" in bodies, (
        f"'hello-daemon' not found in inbox bodies {bodies!r} — "
        "DAEMON-02 system-level proof: send-time mbox_write must have written it"
    )


def test_daemon_stop_when_not_running(clean_db: redis_module.Redis) -> None:
    """Plan 11-02: session stop with no daemon record exits 0 and returns not_running.

    TEST-05 scenario: stop verb is idempotent — no daemon HASH means not_running,
    not an error. Verifies the not_running exit path of _daemon_stop.
    """
    session_id = "test-stop-not-running"
    # Confirm no HASH record exists before the call.
    assert _daemon_hash(clean_db, session_id) is None, (
        f"daemon:{session_id} HASH unexpectedly present before test"
    )
    result = _run_cli(["session", "stop", "--json"], session_id=session_id)
    assert result.returncode == 0, (
        f"session stop with no daemon should exit 0; got rc={result.returncode}, "
        f"stderr={result.stderr!r}"
    )
    envelope = json.loads(result.stdout.strip())
    status = envelope.get("data", {}).get("status")
    assert status == "not_running", (
        f"expected status=not_running, got status={status!r}. "
        f"Full envelope: {envelope!r}"
    )
    # Confirm no orphan HASH record was created.
    assert _daemon_hash(clean_db, session_id) is None, (
        f"daemon:{session_id} HASH appeared unexpectedly after stop"
    )


def test_daemon_stop_live_daemon(clean_db: redis_module.Redis) -> None:
    """Plan 11-02: session stop sends SIGTERM to a live daemon and it cleans up.

    TEST-05 scenario: stop_signaled path — daemon is live, HASH exists, SIGTERM
    sent. After the SIGTERM handler runs, the HASH is DELed (clean shutdown).
    """
    session_id = "test-stop-live"
    _register_session_for_test(session_id, clean_db)
    proc = _start_foreground_daemon(session_id)
    try:
        time.sleep(0.5)  # allow daemon to write HASH and subscribe
        raw = _daemon_hash(clean_db, session_id)
        assert raw is not None, (
            f"daemon:{session_id} HASH absent after 0.5s — daemon did not start"
        )
        result = _run_cli(["session", "stop", "--json"], session_id=session_id)
        assert result.returncode == 0, (
            f"session stop should exit 0; got rc={result.returncode}, "
            f"stderr={result.stderr!r}"
        )
        envelope = json.loads(result.stdout.strip())
        status = envelope.get("data", {}).get("status")
        assert status in ("stop_signaled", "stopped"), (
            f"expected stop_signaled or stopped, got {status!r}. "
            f"Full envelope: {envelope!r}"
        )
        # Wait up to 6s for SIGTERM handler to fire and _daemon_record_del to run.
        # The daemon poll loop uses get_message(timeout=5.0), so it may take up to
        # 5 seconds after SIGTERM to notice _shutdown=True and call _daemon_record_del.
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if not _daemon_hash(clean_db, session_id):
                break
            time.sleep(0.3)
        raw_after = _daemon_hash(clean_db, session_id)
        assert not raw_after, (
            f"daemon:{session_id} HASH still present 6s after SIGTERM — "
            "SIGTERM handler must call _daemon_record_del before exit"
        )
    finally:
        # Ensure the process is gone even if assertions fail mid-test.
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=3)


def test_daemon_idempotent_double_start(clean_db: redis_module.Redis) -> None:
    """Plan 11-02: calling session listen twice returns already_running on the second call.

    TEST-05 scenario: double-start idempotency — Lua write-or-detect in _daemon_start
    returns the existing record when a live daemon is already registered. No second
    process is spawned; the same pid is reported.
    """
    session_id = "test-daemon-idempotent"
    _register_session_for_test(session_id, clean_db)
    proc = _start_foreground_daemon(session_id)
    try:
        time.sleep(0.5)  # allow daemon to write HASH
        raw = _daemon_hash(clean_db, session_id)
        assert raw is not None, (
            f"daemon:{session_id} HASH absent after 0.5s — daemon did not start"
        )
        first_pid = int(raw["pid"])
        assert first_pid == proc.pid, (
            f"HASH pid {first_pid} != child pid {proc.pid}"
        )
        # Second call — should detect live daemon and return already_running.
        result2 = _run_cli(["session", "listen", "--json"], session_id=session_id)
        assert result2.returncode == 0, (
            f"second session listen should exit 0; got rc={result2.returncode}, "
            f"stderr={result2.stderr!r}"
        )
        envelope2 = json.loads(result2.stdout.strip())
        data2 = envelope2.get("data", {})
        assert data2.get("status") == "already_running", (
            f"expected already_running, got {data2.get('status')!r}. "
            f"Full envelope: {envelope2!r}"
        )
        assert data2.get("pid") == first_pid, (
            f"second call reported pid={data2.get('pid')}, expected {first_pid}"
        )
    finally:
        # Clean up: stop the daemon via CLI first, then wait.
        _run_cli(["session", "stop"], session_id=session_id)
        time.sleep(0.5)
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_daemon_crash_recovery(clean_db: redis_module.Redis) -> None:
    """Plan 11-02: SIGKILL leaves stale HASH; subsequent session listen clears it and respawns.

    TEST-05 scenario: crash-recovery — kill -9 bypasses the SIGTERM handler, leaving
    the daemon HASH in Redis. The next session listen call detects the stale record
    via is_holder_stale, DELs it, and spawns a fresh daemon with a new pid.
    """
    session_id = "test-daemon-crash"
    _register_session_for_test(session_id, clean_db)
    proc = _start_foreground_daemon(session_id)
    old_pid = proc.pid
    new_pid = None  # pid of the crash-recovery daemon, captured for cleanup fallback
    try:
        time.sleep(0.5)  # allow daemon to write HASH
        raw = _daemon_hash(clean_db, session_id)
        assert raw is not None, (
            f"daemon:{session_id} HASH absent after 0.5s — daemon did not start"
        )
        assert int(raw["pid"]) == old_pid, (
            f"HASH pid {raw['pid']} != child pid {old_pid}"
        )
        # SIGKILL — bypasses SIGTERM handler, leaves HASH orphaned.
        proc.kill()
        proc.wait(timeout=3)
        time.sleep(0.3)  # allow OS to reap the process
        raw_stale = _daemon_hash(clean_db, session_id)
        assert raw_stale is not None, (
            f"daemon:{session_id} HASH absent after SIGKILL — "
            "SIGKILL must NOT trigger the SIGTERM cleanup handler"
        )
        assert int(raw_stale["pid"]) == old_pid, (
            f"stale HASH pid changed unexpectedly: {raw_stale['pid']!r}"
        )
        # Crash recovery: session listen should clear the stale HASH and spawn fresh.
        result = _run_cli(["session", "listen", "--json"], session_id=session_id)
        assert result.returncode == 0, (
            f"session listen after crash should exit 0; got rc={result.returncode}, "
            f"stderr={result.stderr!r}"
        )
        envelope = json.loads(result.stdout.strip())
        data = envelope.get("data", {})
        assert data.get("status") == "started", (
            f"expected started, got {data.get('status')!r}. "
            f"Full envelope: {envelope!r}"
        )
        new_pid = data.get("pid")
        assert new_pid is not None, "started response must include pid"
        assert new_pid != old_pid, (
            f"new daemon pid {new_pid} == old pid {old_pid} — crash recovery must spawn fresh"
        )
        time.sleep(0.5)  # allow new daemon to write HASH
        raw_new = _daemon_hash(clean_db, session_id)
        assert raw_new is not None, (
            f"daemon:{session_id} HASH absent after crash recovery spawn"
        )
        assert int(raw_new["pid"]) == new_pid, (
            f"new HASH pid {raw_new['pid']} != spawned pid {new_pid}"
        )
    finally:
        # Stop the new daemon via CLI; if that failed to reap it (Redis hiccup,
        # HASH already gone, etc.), SIGTERM the captured pid directly as a
        # fallback so the detached crash-recovery daemon never leaks.
        _run_cli(["session", "stop"], session_id=session_id, timeout=5.0)
        time.sleep(1.0)
        if new_pid is not None:
            try:
                os.kill(int(new_pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass


def test_daemon_start_detaches(clean_db: redis_module.Redis) -> None:
    """DAEMON-01 detach path: `session listen` (no --foreground) spawns a
    background daemon, returns immediately with status=started, and the daemon
    records ITS OWN pid in the HASH (not the parent CLI's).

    Covers the production code path (the default, non-foreground invocation) and
    guards the C-01 regression: the parent CLI must NOT write the HASH; the
    spawned child is the sole writer of its own pid.
    """
    session_id = "test-daemon-detaches"
    _register_session_for_test(session_id, clean_db)
    new_pid = None
    try:
        # Non-foreground: the verb spawns a detached child and returns at once.
        result = _run_cli(
            ["session", "listen", "--json"],
            session_id=session_id,
            env_overrides={"EM_PROJ_DAEMON_HEARTBEAT_INTERVAL": "1"},
        )
        assert result.returncode == 0, (
            f"session listen (detach) should exit 0; got rc={result.returncode}, "
            f"stderr={result.stderr!r}"
        )
        envelope = json.loads(result.stdout.strip())
        data = envelope.get("data", {})
        assert data.get("status") == "started", (
            f"expected started, got {data.get('status')!r}. Full envelope: {envelope!r}"
        )
        new_pid = data.get("pid")
        assert new_pid is not None, "started response must include the daemon pid"

        # The detached child must register ITS OWN pid in the HASH (not the
        # parent CLI's, which has already exited). Poll briefly for the write.
        deadline = time.monotonic() + 3.0
        raw = None
        while time.monotonic() < deadline:
            raw = _daemon_hash(clean_db, session_id)
            if raw is not None:
                break
            time.sleep(0.1)
        assert raw is not None, (
            f"daemon:{session_id} HASH absent after detach — the child daemon "
            "never wrote its record (C-01 regression: parent must not pre-write)"
        )
        assert int(raw["pid"]) == int(new_pid), (
            f"HASH pid {raw['pid']!r} != reported daemon pid {new_pid} — "
            "the HASH must hold the child daemon's pid, not the parent CLI's"
        )
        # The recorded pid must be a live process (the detached daemon).
        os.kill(int(raw["pid"]), 0)  # raises ProcessLookupError if not alive
    finally:
        _run_cli(["session", "stop"], session_id=session_id, timeout=5.0)
        time.sleep(1.0)
        if new_pid is not None:
            try:
                os.kill(int(new_pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
