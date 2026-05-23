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

Stale-detection probe API (Plan 03-02 additions — IDENT-02):
  - ``current_boot_id() -> str``              — current machine boot identifier (for holder comparison)
  - ``probe_pid_alive(pid) -> bool``           — True if PID is alive or unreadable (conservative)
  - ``probe_proc_start_matches(pid, expected_start_epoch) -> bool`` — True on match or AccessDenied
  - ``is_holder_stale(holder: dict) -> bool``  — composite three-probe stale gate (D-10 step 1)

Conservative-probe principle (T-3-02-01 / T-3-XX-06):
  When a probe cannot determine liveness (``psutil.AccessDenied``), we return the
  "live" signal rather than the "stale" signal.  False-negatives (missing a stale
  holder for one acquire cycle) are recoverable — the TTL backstop (D-04: 60s) or
  Phase 5's ``unlock --force`` handles them.  False-positives (displacing a live
  holder) are data corruption and are never acceptable.

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
# Module-level constants (Plan 03-02 — stale-probe tolerance)
# ---------------------------------------------------------------------------

#: Tolerance (in seconds) for comparing ``psutil.Process(pid).create_time()``
#: against the holder's ``proc_start_epoch`` field.
#:
#: Rationale (T-3-02-06):
#:   Clock jitter between ``time.time()`` and psutil's ``create_time()`` is on the
#:   order of nanoseconds to microseconds in practice.  0.5 seconds is well above
#:   clock-jitter noise and well below the shortest realistic process-start gap
#:   that could produce a false "same process" match.  Choosing < 1s avoids
#:   wrapping around high-frequency process recycling; choosing > 0 avoids
#:   spurious mismatches from floating-point rounding.
PROC_START_EPSILON: float = 0.5  # seconds; tolerance for psutil create_time() vs holder's proc_start_epoch


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
# Public API — identity primitives (Plan 03-01)
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
    Plan 03-02's probe functions below.
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


# ---------------------------------------------------------------------------
# Public API — stale-detection probes (Plan 03-02 — IDENT-02)
# ---------------------------------------------------------------------------


def current_boot_id() -> str:
    """Return the boot identifier for the current OS boot (D-10 step 1.3).

    Thin wrapper over ``_boot_id(psutil.boot_time())``.  Stable across all calls
    within a single OS boot; changes on reboot.

    Lock records store the holder's ``boot_id`` at acquire time.  ``lock.py``
    calls ``current_boot_id()`` during the stale probe and compares it against
    ``holder["boot_id"]`` — a mismatch means the machine rebooted since the
    holder acquired the lock, making the holder unconditionally stale.
    """
    return _boot_id(psutil.boot_time())


def probe_pid_alive(pid: int) -> bool:
    """Return True if the given PID appears to be alive, False if it is definitively gone.

    Conservative-probe rule (T-3-XX-06):
      - ``psutil.NoSuchProcess`` → PID is gone; return ``False`` (stale signal).
      - ``psutil.AccessDenied``  → PID exists but we cannot read it; return ``True``
        (live signal — "I cannot tell, default to live").  We must never displace a
        holder we cannot fully read; the TTL backstop recovers false-negatives.
      - No exception → PID is alive; return ``True``.

    Note: constructing ``psutil.Process(pid)`` is sufficient to probe existence on
    most platforms; no further method call is needed.
    """
    try:
        psutil.Process(pid)
        return True
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        # Cannot read process metadata — default to "live" (conservative, T-3-XX-06).
        return True


def probe_proc_start_matches(pid: int, expected_start_epoch: float) -> bool:
    """Return True if the process at ``pid`` appears to be the same process that set ``expected_start_epoch``.

    Compares ``psutil.Process(pid).create_time()`` against ``expected_start_epoch``
    within ``PROC_START_EPSILON`` seconds.  A match means the PID has not been
    recycled; a mismatch (or a missing PID) means the original process is gone and
    a different process is now occupying that PID.

    Return values:
      - ``True``  — ``abs(actual_create_time - expected_start_epoch) < PROC_START_EPSILON``
                    OR ``psutil.AccessDenied`` (conservative — cannot determine, assume match).
      - ``False`` — ``psutil.NoSuchProcess`` (PID gone → stale) OR time mismatch (PID reused).

    Conservative-probe rule (T-3-XX-06) applies here too: ``AccessDenied`` means we
    cannot read the start time, so we defer to other probes (``probe_pid_alive`` and
    the ``boot_id`` check in ``is_holder_stale``) rather than declaring stale.
    """
    try:
        actual = psutil.Process(pid).create_time()
        return abs(actual - expected_start_epoch) < PROC_START_EPSILON
    except psutil.NoSuchProcess:
        # PID is gone — original process no longer exists; stale signal.
        return False
    except psutil.AccessDenied:
        # Cannot read create_time — default to "match" (conservative, T-3-XX-06).
        return True


def is_holder_stale(holder: dict) -> bool:  # type: ignore[type-arg]
    """Return True if the lock holder described by ``holder`` is no longer alive.

    Runs the three-probe sequence from D-10 step 1 in short-circuit order:
      1. ``probe_pid_alive(holder["pid"])``                                → False → stale
      2. ``probe_proc_start_matches(holder["pid"], holder["proc_start_epoch"])`` → False → stale
      3. ``current_boot_id() == holder["boot_id"]``                        → False → stale

    Returns ``False`` (holder is live) ONLY when all three probes pass.
    Returns ``True`` (holder is stale) on the first failing probe.

    Short-circuit ordering is intentional: probe 1 (pid alive) makes a cheap OS
    lookup; on a dead PID we skip the ``create_time()`` call (probe 2 would also
    return False, but we avoid the extra psutil call under contention).

    ``holder`` must be a dict with these keys and types:
      - ``"pid"``               : int
      - ``"proc_start_epoch"``  : float (or int — coerced automatically by psutil comparison)
      - ``"boot_id"``           : str

    Raises ``ValueError`` if any required key is missing or if ``holder["pid"]`` is
    not an ``int``.  A malformed holder is a caller bug, not a routine flow — lock.py
    lets this propagate so it surfaces as an assertion failure during development.

    Security note (T-3-02-04): this function returns a ``bool``; no process metadata
    or pid values appear in the ``ValueError`` message beyond the key name, so error
    messages do not leak information about the process table.
    """
    # --- Input validation ---
    required_keys = ("pid", "proc_start_epoch", "boot_id")
    for key in required_keys:
        if key not in holder:
            raise ValueError(f"holder dict is missing required key: {key!r}")
    if not isinstance(holder["pid"], int):
        raise ValueError("holder['pid'] must be an int")

    pid: int = holder["pid"]
    expected_start: float = holder["proc_start_epoch"]
    expected_boot_id: str = holder["boot_id"]

    # Probe 1 — is the PID alive?
    if not probe_pid_alive(pid):
        return True  # PID gone → stale

    # Probe 2 — does the PID's create_time match the holder's record?
    if not probe_proc_start_matches(pid, expected_start):
        return True  # PID reused → stale

    # Probe 3 — does the current boot_id match the holder's boot_id?
    if current_boot_id() != expected_boot_id:
        return True  # Machine rebooted since the holder acquired the lock → stale

    # All three probes say live.
    return False
