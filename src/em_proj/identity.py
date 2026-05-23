"""Session-id, project-hash, and process-composite resolution for em-proj.

This module is the identity primitive every lock record (Phase 3) and claim record
(Phase 4) writes through.  It is deliberately stateless and has NO Redis imports —
it exists to be trivially unit-testable without a Redis connection and to be shareable
with future ``em-proj session`` / ``em-proj message`` subcommands without a
circular-import risk (D-12 top-level placement rationale).

Invariants (carry-forwards from Phase 2 D-17 / D-19):
  - NO ``import typer`` — this is a pure-ops module.
  - NO redis exceptions — Redis is not in scope here.
  - NO redis_client import — identity is Redis-free (no circular dependency risk).

Public API (consumed by Phase 3 lock.py and Phase 4 claim.py):
  - ``resolve_session_id() -> str``   — CLAUDE_CODE_SESSION_ID or documented fallback
  - ``resolve_project_hash() -> str`` — tr('/', '-') on absolute cwd path
  - ``current_process_composite() -> dict[str, object]`` — five-key holder record subset

Fallback chain for ``resolve_session_id`` (D-12):
  1. ``CLAUDE_CODE_SESSION_ID`` env var — set in every Claude Code session; UUID string.
  2. ``pid-<os.getpid()>`` — deterministic within the lifetime of the calling process,
     guaranteed non-empty, human-readable for debugging.  The ``pid-`` prefix prevents
     ambiguity with UUID-formatted session IDs.

``resolve_project_hash`` strategy (T-3-01-03 threat mitigation):
  Uses ``os.getcwd()`` only — no subprocess, no ``git rev-parse --show-toplevel`` shell-out.
  This eliminates the PATH-controlled git attack surface called out in T-3-01-03.
  The git-toplevel fallback mentioned in the plan is therefore NOT implemented; the
  module docstring documents this decision explicitly so a future agent does not
  re-introduce the shell-out.  The hash matches the ``~/.claude/projects/<hash>/``
  convention exactly: ``/Users/x/y`` -> ``-Users-x-y`` (leading slash → leading dash,
  every interior slash → dash, no truncation, no cryptographic hash — per PROJECT.md
  Verified Facts).

``boot_id`` derivation (deterministic, stable within one OS boot):
  ``_boot_id(boot_time)`` — ``hashlib.sha256(str(boot_time).encode()).hexdigest()[:16]``
  Using ``str(psutil.boot_time())`` (a float) as input means two calls on the same
  machine within the same boot always produce the same 16-char hex string.  The
  ``[:16]`` slice keeps the field compact for JSON readability.  The helper is
  module-level so Plan 03-02's stale-probe can import and call it directly to
  re-derive the expected ``boot_id`` from a live probe without duplicating the formula.
"""
from __future__ import annotations

import hashlib
import os


import psutil


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _boot_id(boot_time: float) -> str:
    """Return a stable 16-hex-char identifier derived from the system boot epoch.

    Two calls within the same OS boot always return the same string.  The input
    ``boot_time`` is ``psutil.boot_time()`` — a float Unix epoch.  Callers that
    need to re-derive the boot_id for a stale-probe comparison (Plan 03-02) import
    this helper directly rather than duplicating the derivation formula.

    Derivation: ``sha256(str(boot_time).encode()).hexdigest()[:16]``
    """
    raw = str(boot_time).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_session_id() -> str:
    """Return the calling session's identity string.

    Fallback chain (D-12):
      1. ``CLAUDE_CODE_SESSION_ID`` — UUID set by Claude Code.  Returned as-is when
         the var is present AND non-empty (an empty string is treated as unset).
      2. ``pid-<os.getpid()>`` — deterministic fallback; non-empty; unique within
         the machine's PID lifetime.  The ``pid-`` prefix distinguishes it from UUID
         session IDs in log output and ``locks --mine`` filtering.

    This function is called once per ``current_process_composite()`` invocation;
    callers that cache the composite do not pay for repeated env reads.
    """
    val = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if val:
        return val
    return f"pid-{os.getpid()}"


def resolve_project_hash() -> str:
    """Return the project-hash string matching the ~/.claude/projects/<hash>/ convention.

    The hash is ``os.getcwd()`` resolved to an absolute path, then every ``/`` replaced
    by ``-`` (no cryptographic hashing, no truncation — per PROJECT.md Verified Facts).
    Example: ``/Users/emonical/projects/personal/ai-tools/em-proj``
             → ``-Users-emonical-projects-personal-ai-tools-em-proj``

    The leading slash becomes a leading dash; that is intentional and matches what
    Claude Code uses under ``~/.claude/projects/``.

    Design choice — cwd-only, no git-toplevel fallback:
      Shelling out to ``git rev-parse --show-toplevel`` introduces a PATH-controlled
      attack surface (T-3-01-03).  Since the project-hash is informational metadata
      (it enables ``locks --mine`` filtering in Phase 5 but does not gate lock
      ownership — that is ``pid + proc_start_epoch + boot_id``), correctness of the
      exact path is less critical than the security invariant.  The cwd is always
      available and requires no subprocess.  If a future phase needs git-toplevel
      resolution it should use ``subprocess.run(['git', ...], shell=False)`` with
      a verified absolute path to the git binary, not a shell-out.
    """
    cwd = os.path.abspath(os.getcwd())
    return cwd.replace("/", "-")


def current_process_composite() -> dict[str, object]:
    """Return the holder-record subset for the current process (D-02).

    The returned dict has exactly five keys:
      - ``pid``               (int)   — current process PID via ``os.getpid()``
      - ``proc_start_epoch``  (float) — process creation time via ``psutil.Process().create_time()``
      - ``boot_id``           (str)   — 16-hex-char stable boot identifier via ``_boot_id()``
      - ``session_id``        (str)   — CLAUDE_CODE_SESSION_ID or fallback via ``resolve_session_id()``
      - ``project_hash``      (str)   — cwd-as-dash-separated path via ``resolve_project_hash()``

    The composite is the shared foundation for Phase 3 lock records and Phase 4 claim
    records.  ``lock.py`` and ``claim.py`` extend it with ``reason``, ``acquired_at``/
    ``claimed_at``, and ``expires_at`` fields; they never reconstruct these five fields
    inline.

    ``psutil.Process()`` without an explicit pid targets the current process.
    ``psutil.NoSuchProcess`` is NOT caught here — if it raises, that is a genuine bug
    (the current process cannot probe itself) and should propagate as an unhandled
    exception.  Stale-probe error handling (for querying OTHER processes) lives in
    Plan 03-02's ``lock.py``.
    """
    pid = os.getpid()
    proc = psutil.Process(pid)
    boot_time = psutil.boot_time()
    return {
        "pid": pid,
        "proc_start_epoch": proc.create_time(),
        "boot_id": _boot_id(boot_time),
        "session_id": resolve_session_id(),
        "project_hash": resolve_project_hash(),
    }
