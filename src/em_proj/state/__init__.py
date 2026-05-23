"""state subcommand family — D-14 mount module + the four KV verbs.

This module is the D-14 MOUNT POINT: it defines ``state_app`` (the nested typer
app mounted from ``cli.py``) and attaches the four KV verbs — ``get``, ``set``,
``del``, ``list`` — as thin per-verb command translation layers.

Design contract — this module holds NO business logic
-----------------------------------------------------
Per D-14 every verb is a three-step wrapper and nothing more:

    1. Resolve ``json_mode`` via ``resolve_json_mode(json_flag)`` (D-15/D-16).
    2. Obtain the Redis singleton and pre-check it with the redis_client
       die-on-unreachable helper (D-18) — REDIS-02 UX surfaces BEFORE any
       business work.
    3. Call exactly one ``em_proj.state.kv`` op, then emit via exactly one
       ``em_proj.output.emit_*`` helper.

All KV business logic lives in ``em_proj.state.kv`` (D-17); all envelope /
TTY-detection logic lives in ``em_proj.output`` (D-15). This module only
translates argv into those calls.

D-15 / D-16 — JSON mode
-----------------------
Every verb exposes the auto-paired json/no-json flag as a single
``typer.Option`` (typer infers the negative form from the slash syntax). The
default ``None`` means auto-detect from the stdout TTY status. Each verb
resolves the flag through ``resolve_json_mode`` — the single point where
TTY-detection is exercised at the verb layer — and passes the resolved boolean
straight to ``emit_*``.

D-18 — Redis-error single chokepoint
------------------------------------
Every verb body begins with a ``get_client()`` call followed immediately by the
redis_client die-on-unreachable pre-check. The verbs NEVER catch
``redis.ConnectionError`` / ``redis.TimeoutError`` — connection-error
translation is owned solely by ``em_proj.redis_client``. Verbs catch ONLY the
domain exceptions ``KvNotFound`` and ``ValidationError`` raised by ``kv.py``.

``del`` is a Python keyword
---------------------------
The ``del`` verb's underlying function is named ``delete_kv`` because ``del``
cannot be a Python identifier. typer routes by the decorator's name string,
not by the function name, so the verb is still reachable as
``em-proj state del``.
"""

from typing import Annotated

import typer

from em_proj.output import emit_error, emit_not_found, emit_ok, resolve_json_mode
from em_proj.redis_client import die_if_redis_unreachable, get_client
from em_proj.state.kv import (
    KvNotFound,
    ValidationError,
    kv_del,
    kv_get,
    kv_list,
    kv_set,
)

state_app = typer.Typer(
    name="state",
    help="KV / lock / claim primitives",
    no_args_is_help=True,        # `em-proj state` alone prints help (D-14)
    add_completion=False,        # opt out of typer auto-completion until needed
)

# Shared --json/--no-json option help text (D-16 — every verb exposes the pair).
_JSON_HELP = (
    "Force JSON or plain text output. "
    "Default: auto-detect from stdout TTY."
)


@state_app.command("get")
def get(
    key: Annotated[str, typer.Argument(help="The kv key to read.")],
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Read a value from the kv namespace.

    Exits 2 if the key is not set (distinct from an empty-string value).
    """
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    try:
        value = kv_get(key)
    except KvNotFound:
        emit_not_found(f"key '{key}' not set", json_mode=json_mode)
    except ValidationError as e:
        emit_error(e.code, e.message, json_mode=json_mode)
    emit_ok({"key": key, "value": value}, json_mode=json_mode)


@state_app.command("set")
def set(  # noqa: A001 — `set` is the user-facing verb name; shadowing the builtin is intentional and scoped.
    key: Annotated[str, typer.Argument(help="The kv key to write.")],
    value: Annotated[str, typer.Argument(help="The value to store.")],
    ttl: Annotated[
        int | None,
        typer.Option(
            "--ttl",
            min=1,
            help=(
                "Time to live in seconds. Without --ttl, an existing key's "
                "TTL is preserved (KEEPTTL)."
            ),
        ),
    ] = None,
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Write a value to the kv namespace.

    With no --ttl on an existing key, preserves the existing TTL (KEEPTTL).
    """
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    try:
        kv_set(key, value, ttl=ttl)
    except ValidationError as e:
        emit_error(e.code, e.message, json_mode=json_mode)
    emit_ok({"key": key, "ttl": ttl}, json_mode=json_mode)


@state_app.command("del")
def delete_kv(
    key: Annotated[str, typer.Argument(help="The kv key to delete.")],
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Delete a value from the kv namespace.

    Idempotent — exits 0 whether or not the key existed; the `deleted` boolean
    indicates which.
    """
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    try:
        deleted = kv_del(key)
    except ValidationError as e:
        emit_error(e.code, e.message, json_mode=json_mode)
    emit_ok({"key": key, "deleted": deleted}, json_mode=json_mode)


@state_app.command("list")
def list_keys(
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """List all keys in the kv namespace, alphabetically.

    Excludes lock and claim namespaces.
    """
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    keys = kv_list()
    emit_ok({"keys": keys}, json_mode=json_mode)
