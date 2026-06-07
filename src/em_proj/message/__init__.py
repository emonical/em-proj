"""message package init — re-exports ops public API and defines message_app.

Provides the message_app Typer stub for Plan 09-03 to add inbox/send verb commands.
Re-exports the full public API from _ops so callers can import from em_proj.message
directly without knowing the internal module layout.
"""
from __future__ import annotations

import typer

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

__all__ = [
    "message_app",
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
#: Verb commands (inbox, send) are added in Plan 09-03.
message_app = typer.Typer(
    name="message",
    help="Inter-session messaging — inbox.",
    no_args_is_help=True,
    add_completion=False,
)
