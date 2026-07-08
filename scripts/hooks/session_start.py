#!/usr/bin/env python3
"""Phase 12 SessionStart hook (HOOK-01).

Opt-in, zero-footprint-by-default integration between Claude Code's
SessionStart lifecycle event and em-proj's session registry + listener
daemon. Participation is gated on the EM_SESSIONS_AUTOSTART environment
variable — when unset (or any value other than the literal string "1"),
this script performs no Redis writes at all.

When gated on, this script shells out to the `em-proj` binary exactly like
`~/.claude/scripts/session-registry.py` does (subprocess only — it never
imports em_proj internals): it registers the current session and starts
its listener daemon (detached, idempotent — reusing the Phase 11 daemon
lifecycle verbatim; no new session/daemon code lives here).

HOOK-04 contract: this script always terminates via the single unconditional
call at the bottom of this file, regardless of the gate value or any
internal failure (em-proj absent from PATH, Redis unreachable, malformed
hook JSON, or any other exception) — it must never break session startup.
"""
import json
import os
import subprocess
import sys

#: The opt-in gate env var. Gate is open only when this is exactly "1".
GATE_ENV = "EM_SESSIONS_AUTOSTART"


def _read_hook() -> dict:
    """Read the SessionStart hook JSON payload from stdin.

    Mirrors session-registry.py's `_read_hook`: any parse failure yields an
    empty dict rather than propagating.
    """
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def _child_env(session_id: str) -> dict:
    """Build the subprocess env, propagating Claude Code's session identity.

    Setting CLAUDE_CODE_SESSION_ID here is how every em-proj invocation this
    script makes resolves the SAME identity Claude Code assigned to this
    session (resolve_session_id() reads this var first, before falling back
    to a pid-derived id).
    """
    env = dict(os.environ)
    if session_id:
        env["CLAUDE_CODE_SESSION_ID"] = session_id
    return env


if __name__ == "__main__":
    try:
        if os.environ.get(GATE_ENV) == "1":
            hook = _read_hook()
            session_id = hook.get("session_id") or ""
            env = _child_env(session_id)
            subprocess.run(
                ["em-proj", "session", "register"],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
            subprocess.run(
                ["em-proj", "session", "listen"],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
    except Exception:
        pass
    sys.exit(0)
