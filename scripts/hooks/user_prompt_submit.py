#!/usr/bin/env python3
"""Phase 12 UserPromptSubmit hook (HOOK-02).

Opt-in, zero-footprint-by-default integration between Claude Code's
UserPromptSubmit lifecycle event and em-proj's mailbox. Participation is
gated on the EM_SESSIONS_AUTOSTART environment variable — when unset (or any
value other than the literal string "1"), this script performs no Redis
reads and prints nothing.

When gated on, this script shells out to the `em-proj` binary exactly like
`~/.claude/scripts/session-registry.py` does (subprocess only — it never
imports em_proj internals): it reads the current session's mailbox via
`em-proj message inbox --json` (default consume, NOT --peek — matches the
mailbox's read-once semantics) and, when messages exist, prints a formatted
block to stdout that becomes additional context for this turn.

HOOK-04 contract: this script always terminates via the single unconditional
call at the bottom of this file, regardless of the gate value or any
internal failure (em-proj absent from PATH, Redis unreachable, malformed
hook JSON, malformed inbox output, or any other exception) — it must never
break prompt submission.
"""
import json
import os
import subprocess
import sys

#: The opt-in gate env var. Gate is open only when this is exactly "1".
GATE_ENV = "EM_SESSIONS_AUTOSTART"


def _read_hook() -> dict:
    """Read the UserPromptSubmit hook JSON payload from stdin.

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


def _format_messages(messages: list) -> str:
    """Format a list of MBOX-04 records as the `[em-proj inbox]` stdout block.

    One line per message: `- from {from_session} ({pattern}/{scope}): {body}`,
    with an ` [topic:{topic}]` suffix inserted before the colon when the
    message's topic field is non-null.
    """
    lines = [f"[em-proj inbox] {len(messages)} new message(s):"]
    for m in messages:
        topic_suffix = f" [topic:{m['topic']}]" if m.get("topic") else ""
        lines.append(
            f"- from {m.get('from_session', '?')} "
            f"({m.get('pattern', '?')}/{m.get('scope', '?')})"
            f"{topic_suffix}: {m.get('body', '')}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    try:
        if os.environ.get(GATE_ENV) == "1":
            hook = _read_hook()
            session_id = hook.get("session_id") or ""
            env = _child_env(session_id)
            result = subprocess.run(
                ["em-proj", "message", "inbox", "--json"],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
            if result.returncode == 0:
                payload = json.loads(result.stdout)
                messages = payload.get("data") or []
                if messages:
                    print(_format_messages(messages))
    except Exception:
        pass
    sys.exit(0)
