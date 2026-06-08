"""Daemon process/signal/subprocess ops for the Phase 11 listener daemon.

This module owns ALL daemon process and signal management code for the
per-session listener daemon introduced in Phase 11. It is intentionally
separate from session/_ops.py, which prohibits subprocess/signal imports.

Import policy — deliberately different from _ops.py:
  subprocess, signal, shutil, os are legitimately imported here.
  These are PROHIBITED in session/_ops.py but intentionally present in
  _daemon.py. Any future agent reading session/_ops.py's "Prohibited imports"
  docstring should note that prohibition applies ONLY to _ops.py, not here.

Public entry points:
  - _daemon_start(session_id)       — spawn detached daemon or detect live one
  - _daemon_foreground_run(session_id) — long-lived daemon body (blocks until SIGTERM)
  - _daemon_record_read(session_id) — read the daemon HASH record from Redis
  - _daemon_record_del(session_id)  — delete the daemon HASH record from Redis

Key namespace: DAEMON_KEY_PREFIX = "daemon:" — machine-global scope,
distinct from state:*, mbox:*, topic:* namespaces.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time

from em_proj.identity import current_process_composite, is_holder_stale
from em_proj.redis_client import get_client
from em_proj.session._ops import SessionNotFound, session_heartbeat


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Key prefix for daemon HASH records in Redis.
#: Full key: "daemon:<session_id>" — machine-global scope.
DAEMON_KEY_PREFIX: str = "daemon:"

#: Heartbeat cadence in seconds. Must remain well under TTL_DEFAULT=300.
#: Override with EM_PROJ_DAEMON_HEARTBEAT_INTERVAL env var (used by tests).
DAEMON_HEARTBEAT_INTERVAL: int = int(
    os.environ.get("EM_PROJ_DAEMON_HEARTBEAT_INTERVAL", "60")
)


# ---------------------------------------------------------------------------
# Lua script — atomic write-or-detect for daemon HASH
# ---------------------------------------------------------------------------

#: Atomic write-or-detect script for the daemon HASH record.
#:
#: Mirrors LUA_SESSION_UPSERT from _ops.py — run server-side in one Redis
#: command slot, eliminating TOCTOU races between read and write.
#:
#: KEYS[1] = full Redis key ("daemon:<session_id>")
#: ARGV[1] = pid (int as string)
#: ARGV[2] = proc_start_epoch (float as string)
#: ARGV[3] = boot_id (16-hex-char string)
#: ARGV[4] = started_at (float as string)
#:
#: Returns:
#:   "written"  — key was absent; all 4 fields written
#:   list       — key was present; HMGET result [pid, proc_start_epoch, boot_id, started_at]
LUA_DAEMON_WRITE_OR_DETECT: str = """
local exists = redis.call('EXISTS', KEYS[1])
if exists == 0 then
  redis.call('HSET', KEYS[1],
    'pid', ARGV[1],
    'proc_start_epoch', ARGV[2],
    'boot_id', ARGV[3],
    'started_at', ARGV[4]
  )
  return 'written'
end
return redis.call('HMGET', KEYS[1], 'pid', 'proc_start_epoch', 'boot_id', 'started_at')
"""


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _daemon_record_key(session_id: str) -> str:
    """Return the full Redis key for the daemon HASH record."""
    return f"{DAEMON_KEY_PREFIX}{session_id}"


def _daemon_record_write(session_id: str) -> str | dict:  # type: ignore[type-arg]
    """Atomically write daemon HASH record or detect an existing one.

    Uses LUA_DAEMON_WRITE_OR_DETECT for TOCTOU-safe operation.

    Returns:
      "written"    — record was absent; this process's composite written.
      dict         — record already existed; {pid: int, proc_start_epoch: float,
                     boot_id: str, started_at: float} of the existing holder.
    """
    client = get_client()
    composite = current_process_composite()
    key = _daemon_record_key(session_id)
    result = client.eval(
        LUA_DAEMON_WRITE_OR_DETECT,
        1,  # number of KEYS
        key,
        str(composite["pid"]),
        str(composite["proc_start_epoch"]),
        composite["boot_id"],
        str(time.time()),
    )
    if result == "written":
        return "written"
    # result is [pid_str, proc_start_str, boot_id, started_at_str]
    pid_str, start_str, boot_id, started_at_str = result
    return {
        "pid": int(pid_str),
        "proc_start_epoch": float(start_str),
        "boot_id": boot_id,
        "started_at": float(started_at_str),
    }


def _daemon_record_read(session_id: str) -> dict | None:  # type: ignore[type-arg]
    """Read the daemon HASH record from Redis, converting strings to native types.

    Returns None if the key is absent. Otherwise returns:
      {pid: int, proc_start_epoch: float, boot_id: str, started_at: float}

    Note: is_holder_stale() requires pid as a plain int — this function
    converts the raw Redis string to int before returning.
    """
    client = get_client()
    raw = client.hgetall(_daemon_record_key(session_id))
    if not raw:
        return None
    return {
        "pid": int(raw["pid"]),
        "proc_start_epoch": float(raw["proc_start_epoch"]),
        "boot_id": raw["boot_id"],
        "started_at": float(raw["started_at"]),
    }


def _daemon_record_del(session_id: str) -> None:
    """Delete the daemon HASH record from Redis."""
    client = get_client()
    client.delete(_daemon_record_key(session_id))


def _daemon_clear_if_stale(session_id: str) -> bool:
    """Delete the daemon HASH record if the recorded process is stale.

    Returns True if a stale record was cleared. Returns False if the record
    was absent (nothing to clear) or the recorded process is still live.
    """
    record = _daemon_record_read(session_id)
    if record is None:
        return False
    if is_holder_stale(record):
        _daemon_record_del(session_id)
        return True
    return False


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def _daemon_start(session_id: str) -> dict:  # type: ignore[type-arg]
    """Start a detached listener daemon subprocess, or detect a live existing one.

    Implements single-instance enforcement via Lua atomic write-or-detect.

    Returns:
      {"status": "started",        "pid": <child_pid>}  — new daemon spawned
      {"status": "already_running","pid": <existing_pid>} — live daemon present
    """
    # Step 1: Lua atomic write-or-detect — try to register this process.
    result = _daemon_record_write(session_id)

    if isinstance(result, dict):
        # Key already existed — check whether the recorded daemon is still alive.
        if not is_holder_stale(result):
            return {"status": "already_running", "pid": result["pid"]}
        # Stale record — clear it and proceed to spawn.
        _daemon_record_del(session_id)

    # Step 2: Spawn the daemon as a detached subprocess.
    # Use shutil.which to locate the em-proj binary; fall back to "em-proj" by name.
    binary = shutil.which("em-proj") or "em-proj"
    proc = subprocess.Popen(
        [binary, "session", "listen", "--foreground"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # setsid() — detach from controlling terminal
        close_fds=True,
        env=None,  # inherit parent env; EM_PROJ_REDIS_DB propagates automatically
    )
    return {"status": "started", "pid": proc.pid}


def _daemon_stop(session_id: str) -> dict:  # type: ignore[type-arg]
    """Stop the running daemon for session_id.

    Self-stop only — uses session_id as the HASH key (D-07 locked decision).
    Returns a status dict; never raises.

    Exit conditions:
      not_running         — no daemon HASH record found
      stale_record_cleared — stale record found and cleared (no os.kill)
      stop_signaled       — SIGTERM sent to live daemon pid
      stopped             — daemon exited between stale-check and kill (ProcessLookupError)

    Security: os.kill is NEVER called when is_holder_stale returns True.
    SIGTERM-to-wrong-pid threat (T-11-02-01) mitigated by is_holder_stale probe
    (pid + proc_start_epoch + boot_id triple) before any signal delivery.
    """
    record = _daemon_record_read(session_id)
    if record is None:
        return {"status": "not_running"}
    if is_holder_stale(record):
        _daemon_record_del(session_id)
        return {"status": "stale_record_cleared"}
    # Record exists and daemon is live — pid already int from _daemon_record_read.
    pid = record["pid"]
    try:
        os.kill(pid, signal.SIGTERM)
        return {"status": "stop_signaled", "pid": pid}
    except ProcessLookupError:
        # Daemon exited between the stale-check and the kill attempt.
        _daemon_record_del(session_id)
        return {"status": "stopped"}


def _daemon_foreground_run(session_id: str) -> None:
    """Long-lived daemon body. Invoked by `session listen --foreground`.

    Blocks until SIGTERM is received. On clean shutdown:
      1. Unsubscribes from Redis pub/sub
      2. Deletes the daemon HASH record
      3. Exits 0

    Never calls mbox_write — DAEMON-02 is satisfied at the system level:
    Phase 10's send path writes the durable mailbox record at send time.
    """
    # Step 1: Atomically register this process as the daemon holder.
    result = _daemon_record_write(session_id)
    if isinstance(result, dict):
        # Another record already exists.
        if not is_holder_stale(result):
            # A live daemon is already registered — this process is a duplicate.
            # Race-condition guard (RESEARCH Pattern 3): exit cleanly.
            sys.exit(0)
        # Stale record — clear it and re-register as this process.
        _daemon_record_del(session_id)
        _daemon_record_write(session_id)

    # Step 2: Set up Redis pub/sub. Two clients:
    #   cmd_client — used for heartbeat EVAL commands
    #   ps         — cmd_client.pubsub() allocates a separate pool connection;
    #                never call EVAL on ps (RESEARCH Pitfall 2 / T-11-01-02)
    cmd_client = get_client()
    ps = cmd_client.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(f"msg:{session_id}")

    # Step 3: Install SIGTERM handler for clean shutdown.
    _shutdown = False

    def _on_sigterm(*_: object) -> None:
        nonlocal _shutdown
        _shutdown = True

    signal.signal(signal.SIGTERM, _on_sigterm)

    # Step 4: Poll loop — receive pub/sub messages and refresh heartbeat.
    last_heartbeat = time.monotonic()
    interval = int(os.environ.get("EM_PROJ_DAEMON_HEARTBEAT_INTERVAL", "60"))

    while not _shutdown:
        msg = ps.get_message(timeout=5.0)
        if msg is not None:
            # DAEMON-02 system-level: message already written to mailbox at send time.
            # Log/no-op: daemon does not write to mbox.
            pass

        now = time.monotonic()
        if now - last_heartbeat >= interval:
            try:
                session_heartbeat()
                last_heartbeat = now
            except SessionNotFound:
                # Session was reaped externally — clean exit.
                break

    # Step 5: Clean shutdown.
    ps.unsubscribe()
    ps.close()
    _daemon_record_del(session_id)
    sys.exit(0)
