"""message package init — re-exports ops public API and defines message_app.

Provides the message_app Typer app and the inbox verb command for the em-proj
message subcommand family. Re-exports the full public API from _ops so callers
can import from em_proj.message directly without knowing the internal module layout.

Design contract — this module holds NO business logic (D-14):
  Each verb is a three-step wrapper:
    1. Resolve json_mode via resolve_json_mode(json_flag).
    2. Obtain the Redis singleton and pre-check with die_if_redis_unreachable.
    3. Call exactly one _ops function, then emit via one emit_* helper.
  All message business logic lives in em_proj.message._ops.
"""
from __future__ import annotations

from typing import Annotated

import typer

from em_proj.identity import resolve_session_id
from em_proj.message._ops import (
    MBOX_KEY_PREFIX,
    MBOX_MAXLEN,
    MBOX_TTL_SECONDS,
    MAX_BODY_CHARS,
    MailboxError,
    mailbox_inbox,
    mbox_blocking_read,
    mbox_write,
)
from em_proj.output import emit_ok, resolve_json_mode
from em_proj.redis_client import die_if_redis_unreachable, get_client

__all__ = [
    "message_app",
    "inbox_cmd",
    "mbox_write",
    "mailbox_inbox",
    "mbox_blocking_read",
    "MBOX_KEY_PREFIX",
    "MBOX_MAXLEN",
    "MBOX_TTL_SECONDS",
    "MAX_BODY_CHARS",
    "MailboxError",
]

#: Typer application for the 'message' subcommand family.
message_app = typer.Typer(
    name="message",
    help="Inter-session messaging — inbox.",
    no_args_is_help=True,
    add_completion=False,
)

# Shared --json/--no-json option help text (D-16 — every verb exposes the pair).
_JSON_HELP = (
    "Force JSON or plain text output. "
    "Default: auto-detect from stdout TTY."
)


@message_app.command("inbox")
def inbox_cmd(
    peek: Annotated[
        bool,
        typer.Option("--peek", help="Read without consuming messages."),
    ] = False,
    since: Annotated[
        str | None,
        typer.Option("--since", help="Resume from this message ID (exclusive)."),
    ] = None,
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Read the current session's mailbox.

    By default, reads all messages and marks them consumed. Use --peek to
    read without consuming, or --since <msg_id> to resume from a cursor.

    Exit code mapping:
      0 = success (empty mailbox is still exit 0)
      1 = Redis unreachable
    """
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    session_id = resolve_session_id()
    messages = mailbox_inbox(session_id, since=since, peek=peek)
    emit_ok(data=messages, json_mode=json_mode)
