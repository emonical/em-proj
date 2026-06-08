"""session subcommand family — D-14 mount module + register/heartbeat/list/show/listen/stop verbs.

This module is the D-14 MOUNT POINT for the session subcommand family. It defines
``session_app`` (the nested typer app mounted from ``cli.py``) and attaches six
verb commands — ``register``, ``heartbeat``, ``list``, ``show``, ``listen``, ``stop``
— as thin per-verb command translation layers.

Design contract — this module holds NO business logic
------------------------------------------------------
Per D-14 every verb is a three-step wrapper and nothing more:

    1. Resolve ``json_mode`` via ``resolve_json_mode(json_flag)`` (D-15/D-16).
    2. Obtain the Redis singleton and pre-check it with the redis_client
       die-on-unreachable helper (D-18) — REDIS-02 UX surfaces BEFORE any
       business work.
    3. Call exactly one ``em_proj.session._ops`` op, then emit via exactly one
       ``em_proj.output.emit_*`` helper.

All session business logic lives in ``em_proj.session._ops``; this module only
translates argv into those calls.

Package layout
--------------
This is a package (``session/``) rather than a flat module so that future verbs
(e.g. ``session listen`` in Phase 11) can be added in additional submodules
without moving files. External callers should import from ``em_proj.session``
(this module); imports from ``em_proj.session._ops`` are package-private.

Re-exported public API (for backward-compatible imports):
  KEY_PREFIX, TTL_DEFAULT, SessionNotFound,
  session_register, session_heartbeat, session_list, session_show
"""

from typing import Annotated

import typer

from em_proj.identity import resolve_session_id
from em_proj.output import (
    emit_error,
    emit_not_found,
    emit_ok,
    resolve_json_mode,
)
from em_proj.redis_client import die_if_redis_unreachable, get_client
from em_proj.session._daemon import _daemon_foreground_run, _daemon_start, _daemon_stop
from em_proj.session._ops import (
    KEY_PREFIX,
    LUA_SESSION_HEARTBEAT,
    LUA_SESSION_UPSERT,
    MAX_TTL,
    TTL_DEFAULT,
    SessionNotFound,
    _build_session_key,
    _hgetall_to_session,
    _scan_all_holders_by_session_id,
    session_heartbeat,
    session_list,
    session_register,
    session_show,
)

# Re-export the full API (including private helpers used by tests) so that
# external callers can do:
#   from em_proj.session import session_register
#   from em_proj.session import _build_session_key  (test access)
__all__ = [
    "session_app",
    "KEY_PREFIX",
    "LUA_SESSION_HEARTBEAT",
    "LUA_SESSION_UPSERT",
    "MAX_TTL",
    "TTL_DEFAULT",
    "SessionNotFound",
    "_build_session_key",
    "_hgetall_to_session",
    "_scan_all_holders_by_session_id",
    "session_register",
    "session_heartbeat",
    "session_list",
    "session_show",
]

session_app = typer.Typer(
    name="session",
    help="Session registry — register, heartbeat, list, show.",
    no_args_is_help=True,
    add_completion=False,
)

# Shared --json/--no-json option help text (D-16 — every verb exposes the pair).
_JSON_HELP = (
    "Force JSON or plain text output. "
    "Default: auto-detect from stdout TTY."
)


@session_app.command("register")
def session_register_cmd(
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Register the current process as a live session (upsert, idempotent).

    Exit code mapping:
      0 = registered or refreshed successfully
      1 = Redis unreachable
    """
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    record = session_register()
    emit_ok(data=record, json_mode=json_mode)


@session_app.command("heartbeat")
def session_heartbeat_cmd(
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Refresh the heartbeat TTL for the current session.

    Exit code mapping:
      0 = heartbeat refreshed successfully
      1 = Redis unreachable
      2 = session not found (not registered or already expired)
    """
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    try:
        record = session_heartbeat()
        emit_ok(data=record, json_mode=json_mode)
    except SessionNotFound as exc:
        emit_not_found(str(exc), json_mode=json_mode)


@session_app.command("list")
def session_list_cmd(
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """List all live sessions with held-resource counts.

    Returns a list of session records, each enriched with counts of held
    claims, locks, and reservations. Stale sessions are excluded and reaped.

    Exit code mapping:
      0 = success (empty list is still exit 0)
      1 = Redis unreachable
    """
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    results = session_list()
    emit_ok(data=results, json_mode=json_mode)


@session_app.command("show")
def session_show_cmd(
    session_id: Annotated[str, typer.Argument(help="Session ID to inspect.")],
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Show the full record for a specific session, including held-resource details.

    Returns the 9-field session record plus the full held claim/lock/reserve dicts
    for that session. Stale sessions are excluded and reaped.

    Exit code mapping:
      0 = session found and returned
      1 = Redis unreachable
      2 = session not found (absent, expired, or stale)
    """
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    try:
        record = session_show(session_id)
        emit_ok(data=record, json_mode=json_mode)
    except SessionNotFound as exc:
        emit_not_found(str(exc), json_mode=json_mode)


@session_app.command("listen")
def session_listen_cmd(
    foreground: Annotated[
        bool,
        typer.Option(
            "--foreground/--no-foreground",
            help=(
                "Run daemon in foreground (for testing/debugging). "
                "Default: detach as background process."
            ),
        ),
    ] = False,
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Start the per-session listener daemon (detached background process).

    Double-start is idempotent — if a live daemon is already running for this
    session, the command exits 0 with {"status": "already_running"}.

    Use --foreground to run the daemon body in the calling process (for testing
    and debugging). In foreground mode the process blocks until SIGTERM.

    Exit code mapping:
      0 = started, already running, or foreground run completed
      1 = Redis unreachable
    """
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    session_id = resolve_session_id()
    if foreground:
        # Blocks until SIGTERM — no emit (daemon runs until shutdown signal).
        _daemon_foreground_run(session_id)
    else:
        result = _daemon_start(session_id)
        emit_ok(data=result, json_mode=json_mode)


@session_app.command("stop")
def session_stop_cmd(
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Stop the listener daemon for the current session.

    Idempotent — returns success even if no daemon is running.
    Self-stop only: uses resolve_session_id() as the HASH key (D-07).

    Exit code mapping:
      0 = daemon stopped, not running, or stale record cleared
      1 = Redis unreachable
    """
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    session_id = resolve_session_id()
    result = _daemon_stop(session_id)
    emit_ok(data=result, json_mode=json_mode)
