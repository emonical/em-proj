"""state subcommand family — D-14 mount module + the four KV verbs + lock/unlock.

This module is the D-14 MOUNT POINT: it defines ``state_app`` (the nested typer
app mounted from ``cli.py``) and attaches the four KV verbs — ``get``, ``set``,
``del``, ``list`` — plus the two lock verbs — ``lock``, ``unlock`` — as thin
per-verb command translation layers.

Design contract — this module holds NO business logic
-----------------------------------------------------
Per D-14 every verb is a three-step wrapper and nothing more:

    1. Resolve ``json_mode`` via ``resolve_json_mode(json_flag)`` (D-15/D-16).
    2. Obtain the Redis singleton and pre-check it with the redis_client
       die-on-unreachable helper (D-18) — REDIS-02 UX surfaces BEFORE any
       business work.
    3. Call exactly one ``em_proj.state.kv`` or ``em_proj.state.lock`` op, then
       emit via exactly one ``em_proj.output.emit_*`` helper.

All KV business logic lives in ``em_proj.state.kv`` (D-17); all lock logic
lives in ``em_proj.state.lock`` (D-17); all envelope / TTY-detection logic
lives in ``em_proj.output`` (D-15). This module only translates argv into
those calls.

D-14/D-17 thin-verb-shell discipline (lock verbs)
--------------------------------------------------
Lock verbs import ONLY public symbols from ``em_proj.state.lock``:
``lock_acquire``, ``lock_release``, ``lock_force_displace``, ``HeldByAnother``,
``DEFAULT_TTL``, ``MIN_TTL``, ``MAX_TTL``. Private helpers (underscore-prefixed)
in lock.py stay module-private; the verb body has zero knowledge of the key
namespace. Displacement Lua is server-side via ``lock_force_displace`` — no
private-symbol leakage into this module.

D-07 — --warn TTY gate
----------------------
``--warn`` checks BOTH ``sys.stdout.isatty()`` AND ``sys.stdin.isatty()``
(T-3-XX-05 dual-isatty requirement). Both must be True to allow the prompt;
otherwise the verb refuses with ``warn_requires_tty`` error code + exit 1.

D-08 — --warn + --hold mutual exclusion
----------------------------------------
``--warn`` and ``--hold`` are mutually exclusive at verb-body level, checked
BEFORE any Redis call. Exit 1 with ``validation_error``.

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
domain exceptions (``KvNotFound``, ``ValidationError``, ``HeldByAnother``).

``del`` is a Python keyword
---------------------------
The ``del`` verb's underlying function is named ``delete_kv`` because ``del``
cannot be a Python identifier. typer routes by the decorator's name string,
not by the function name, so the verb is still reachable as
``em-proj state del``.
"""

import sys
import time
from typing import Annotated

import typer

from em_proj.output import (
    emit_error,
    emit_held_by_another,
    emit_not_found,
    emit_ok,
    resolve_json_mode,
)
from em_proj.redis_client import die_if_redis_unreachable, get_client
from em_proj.state.kv import (
    KvNotFound,
    ValidationError,
    kv_del,
    kv_get,
    kv_list,
    kv_set,
)
from em_proj.state.lock import (
    DEFAULT_TTL,
    MAX_TTL,
    MIN_TTL,
    HeldByAnother,
    lock_acquire,
    lock_force_displace,
    lock_release,
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


@state_app.command("lock")
def lock(
    name: Annotated[str, typer.Argument(help="The lock name.")],
    cmd: Annotated[
        list[str] | None,
        typer.Argument(help="Command to wrap (preceded by '--'). Required when --hold is set."),
    ] = None,
    ttl: Annotated[
        int | None,
        typer.Option("--ttl", min=MIN_TTL, max=MAX_TTL, help="Lock TTL in seconds (default 60)."),
    ] = None,
    reason: Annotated[
        str | None,
        typer.Option("--reason", help="Free-form reason metadata (max 256 chars)."),
    ] = None,
    warn: Annotated[
        bool,
        typer.Option("--warn/--no-warn", help="On collision, prompt for human override (TTY only)."),
    ] = False,
    hold: Annotated[
        bool,
        typer.Option("--hold/--no-hold", help="Auto-acquire, run <cmd>, auto-release on exit."),
    ] = False,
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Acquire a short-lived advisory lock.

    Without flags: blocks up to 1s, exits 3 if still held.
    --warn (TTY only): prompts for human override on held lock.
    --hold -- <cmd>: auto-acquire, run <cmd>, auto-release on exit (Plan 03-05).

    Order convention: options before positionals before '--':
      em-proj state lock [--ttl N] [--reason "x"] [--warn|--hold] [--json|--no-json] <name> [-- <cmd...>]
    """
    # 1. Resolve json_mode — single point of resolution (D-15/D-16).
    json_mode = resolve_json_mode(json_flag)

    # 2. D-08 mutex: --warn and --hold are mutually exclusive — BEFORE any Redis call.
    if warn and hold:
        emit_error(
            "validation_error",
            "--warn and --hold are mutually exclusive",
            json_mode=json_mode,
        )

    # 3. --hold stub — Plan 03-05 replaces this single emit_error call with
    #    lock_hold_run dispatch. Placeholder: exits 1 with structured envelope
    #    (Phase 2 D-17 UX invariant: no Python tracebacks for known error states).
    if hold:
        # Placeholder — Plan 03-05 replaces with lock_hold_run dispatch
        emit_error(
            "not_implemented",
            "--hold is implemented in Plan 03-05",
            json_mode=json_mode,
        )

    # 4. Redis pre-check (D-18 chokepoint — must be before any business call).
    client = get_client()
    die_if_redis_unreachable(client)

    # 5. Attempt acquire; handle HeldByAnother per --warn flag.
    effective_ttl = ttl if ttl is not None else DEFAULT_TTL
    try:
        holder = lock_acquire(name, ttl=effective_ttl, reason=reason)
    except HeldByAnother as e:
        # 5a. No --warn: emit held_by_another and exit 3.
        if not warn:
            emit_held_by_another(
                "held_by_another",
                f"Lock '{name}' held by session {e.holder['session_id'] if e.holder else 'unknown'}",
                holder=e.holder,
                json_mode=json_mode,
            )

        # 5b. --warn on non-TTY (D-07 T-3-XX-05 dual-isatty check):
        #     BOTH stdout AND stdin must be TTYs — refuse with exit 1 if not.
        if not (sys.stdout.isatty() and sys.stdin.isatty()):
            emit_error(
                "warn_requires_tty",
                "--warn requires a TTY for confirmation; use the /global-state skill's "
                "unlock --force for programmatic override",
                json_mode=json_mode,
            )

        # 5c. --warn on TTY: prompt the user for manual override.
        holder_sid = e.holder["session_id"] if e.holder else "unknown"
        holder_pid = e.holder["pid"] if e.holder else "?"
        age_s = (
            f"{time.time() - e.holder['acquired_at']:.0f}"
            if e.holder and "acquired_at" in e.holder
            else "?"
        )
        sys.stderr.write(
            f"Lock '{name}' held by session {holder_sid} "
            f"(pid {holder_pid}, age {age_s}s). Override? [y/N]: "
        )
        sys.stderr.flush()
        answer = sys.stdin.readline().strip().lower()
        if answer == "y":
            new_holder = lock_force_displace(name, ttl=effective_ttl, reason=reason)
            sys.stderr.write(
                f"Warning: displaced session {holder_sid}'s lock on '{name}'\n"
            )
            sys.stderr.flush()
            emit_ok(
                {
                    "name": name,
                    "ttl": effective_ttl,
                    "acquired_at": new_holder["acquired_at"],
                    "expires_at": new_holder["expires_at"],
                },
                json_mode=json_mode,
            )
        else:
            emit_held_by_another(
                "held_by_another",
                f"Lock '{name}' held by session {holder_sid}",
                holder=e.holder,
                json_mode=json_mode,
            )
    except ValidationError as e:
        emit_error(e.code, e.message, json_mode=json_mode)

    # 6. Success path.
    emit_ok(
        {
            "name": name,
            "ttl": effective_ttl,
            "acquired_at": holder["acquired_at"],
            "expires_at": holder["expires_at"],
        },
        json_mode=json_mode,
    )


@state_app.command("unlock")
def unlock(
    name: Annotated[str, typer.Argument(help="The lock name.")],
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Release an advisory lock held by the current process."""
    # 1. Resolve json_mode (D-15/D-16).
    json_mode = resolve_json_mode(json_flag)

    # 2. Redis pre-check (D-18 chokepoint).
    client = get_client()
    die_if_redis_unreachable(client)

    # 3. Attempt release; handle errors.
    try:
        lock_release(name)
    except HeldByAnother as e:
        # D-09: non-holder learns they were displaced; exit 3.
        emit_held_by_another(
            "held_by_another",
            f"Lock '{name}' is held by another holder (or has been displaced/expired)",
            holder=e.holder,
            json_mode=json_mode,
        )
    except ValidationError as e:
        emit_error(e.code, e.message, json_mode=json_mode)

    # 4. Success.
    emit_ok({"name": name, "released": True}, json_mode=json_mode)
