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

import json as _json
import sys as _sys
from typing import Annotated

import typer

from em_proj.identity import resolve_session_id
from em_proj.message._ops import (
    MBOX_KEY_PREFIX,
    MBOX_MAXLEN,
    MBOX_TTL_SECONDS,
    MAX_BODY_CHARS,
    TOPIC_KEY_PREFIX,
    MailboxError,
    mailbox_inbox,
    mbox_blocking_read,
    mbox_write,
    send_broadcast,
    send_directed,
    send_topic,
    subscribe_topic,
    unsubscribe_topic,
)
from em_proj.output import (
    SCHEMA_VERSION,
    emit_error,
    emit_not_found,
    emit_ok,
    resolve_json_mode,
)
from em_proj.redis_client import die_if_redis_unreachable, get_client
from em_proj.session._ops import SessionNotFound
from em_proj.state.kv import ValidationError

__all__ = [
    "message_app",
    "inbox_cmd",
    "send_cmd",
    "broadcast_cmd",
    "subscribe_cmd",
    "unsubscribe_cmd",
    "mbox_write",
    "mailbox_inbox",
    "mbox_blocking_read",
    "send_directed",
    "send_broadcast",
    "send_topic",
    "subscribe_topic",
    "unsubscribe_topic",
    "MBOX_KEY_PREFIX",
    "MBOX_MAXLEN",
    "MBOX_TTL_SECONDS",
    "MAX_BODY_CHARS",
    "TOPIC_KEY_PREFIX",
    "MailboxError",
]

#: Typer application for the 'message' subcommand family.
message_app = typer.Typer(
    name="message",
    help="Inter-session messaging — inbox, send, broadcast, topic.",
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
      1 = Redis unreachable, or invalid --since value
    """
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    session_id = resolve_session_id()
    try:
        messages = mailbox_inbox(session_id, since=since, peek=peek)
    except ValidationError as e:
        emit_error(e.code, e.message, json_mode=json_mode)
    emit_ok(data=messages, json_mode=json_mode)


def _emit_or_partial(result: dict, json_mode: bool | None) -> None:  # type: ignore[type-arg]
    """Emit a send/broadcast delivery-metadata result, then exit 0 or 4.

    Mirrors emit_ok's envelope exactly (one parseable shape for callers) but maps
    a partial fan-out — result["recipients_failed"] > 0 — to exit code 4
    (D-ExitCode4) instead of 0. The full delivery counts are always in ``data``,
    so a caller reads data.recipients_failed regardless of exit code. Exit 4 is
    new and does not collide with 0=ok / 1=error / 2=not_found / 3=held_by_another.
    """
    exit_code = 4 if result.get("recipients_failed", 0) > 0 else 0
    if resolve_json_mode(json_mode):
        _sys.stdout.write(
            _json.dumps(
                {"schema_version": SCHEMA_VERSION, "status": "ok", "data": result},
                separators=(",", ":"),
            )
            + "\n"
        )
    else:
        # result is a flat dict of scalars — render one "key: value" per line
        # (same shape emit_ok's _render_plain produces for a flat dict).
        for key, value in result.items():
            print(f"{key}: {value}")
    raise SystemExit(exit_code)


@message_app.command("send")
def send_cmd(
    body: Annotated[str, typer.Argument(help="Message body.")],
    to: Annotated[
        str | None,
        typer.Option("--to", help="Recipient session ID (directed send)."),
    ] = None,
    topic: Annotated[
        str | None,
        typer.Option("--topic", help="Topic name (topic send)."),
    ] = None,
    scope: Annotated[
        str,
        typer.Option("--scope", help="Scope: project|upstream|machine."),
    ] = "machine",
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Send a message to one session (--to) or a topic's subscribers (--topic).

    --to and --topic are mutually exclusive; exactly one is required.

    Exit code mapping:
      0 = delivered to all recipients (or scope/topic had 0 recipients)
      1 = Redis unreachable, or invalid topic/scope
      2 = directed send to a session that does not exist (not_found)
      4 = partial fan-out (some recipients written, some failed)
    """
    json_mode = resolve_json_mode(json_flag)
    if to and topic:
        emit_error(
            "validation_error",
            "--to and --topic are mutually exclusive",
            json_mode=json_mode,
        )
    if not to and not topic:
        emit_error(
            "validation_error",
            "must provide --to <session_id> or --topic <name>",
            json_mode=json_mode,
        )
    client = get_client()
    die_if_redis_unreachable(client)
    try:
        if to:
            result = send_directed(to, body, scope)
        else:
            result = send_topic(topic, scope, body)
    except ValidationError as e:
        emit_error(e.code, e.message, json_mode=json_mode)
    except SessionNotFound as e:
        emit_not_found(str(e), json_mode=json_mode)
    _emit_or_partial(result, json_mode)


@message_app.command("broadcast")
def broadcast_cmd(
    body: Annotated[str, typer.Argument(help="Message body.")],
    scope: Annotated[
        str,
        typer.Option("--scope", help="Scope: project|upstream|machine."),
    ] = "machine",
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Broadcast a message to all live sessions in a scope (excluding yourself).

    Exit code mapping:
      0 = delivered to all scope-matched sessions (or the scope had none)
      1 = Redis unreachable, or invalid scope
      4 = partial fan-out (some recipients written, some failed)
    """
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    try:
        result = send_broadcast(body, scope)
    except ValidationError as e:
        emit_error(e.code, e.message, json_mode=json_mode)
    _emit_or_partial(result, json_mode)


@message_app.command("subscribe")
def subscribe_cmd(
    topic: Annotated[str, typer.Argument(help="Topic name to join.")],
    scope: Annotated[
        str,
        typer.Option("--scope", help="Scope: project|upstream|machine."),
    ] = "machine",
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Subscribe the current session to a topic within a scope.

    Exit code mapping:
      0 = subscribed (idempotent)
      1 = Redis unreachable, or invalid topic/scope
    """
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    session_id = resolve_session_id()
    try:
        subscribe_topic(session_id, topic, scope)
    except ValidationError as e:
        emit_error(e.code, e.message, json_mode=json_mode)
    emit_ok(data=None, json_mode=json_mode)


@message_app.command("unsubscribe")
def unsubscribe_cmd(
    topic: Annotated[str, typer.Argument(help="Topic name to leave.")],
    scope: Annotated[
        str,
        typer.Option("--scope", help="Scope: project|upstream|machine."),
    ] = "machine",
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Unsubscribe the current session from a topic within a scope.

    Exit code mapping:
      0 = unsubscribed (idempotent)
      1 = Redis unreachable, or invalid topic/scope
    """
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    session_id = resolve_session_id()
    try:
        unsubscribe_topic(session_id, topic, scope)
    except ValidationError as e:
        emit_error(e.code, e.message, json_mode=json_mode)
    emit_ok(data=None, json_mode=json_mode)
