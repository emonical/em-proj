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

import os
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
    lock_hold_run,
    lock_release,
)
from em_proj.state.claim import (
    TTL_DEFAULT as CLAIM_TTL_DEFAULT,
    MIN_TTL as CLAIM_MIN_TTL,
    MAX_TTL as CLAIM_MAX_TTL,
    HeldByAnother as ClaimHeldByAnother,
    ClaimNotHeld,
    claim_take,
    claim_release,
    claim_check,
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
    --hold -- <cmd>: auto-acquire, run <cmd>, auto-release on exit (LOCK-03).

    --hold exit code mapping:
      - acquire failed (HeldByAnother) → exit 3
      - empty cmd → exit 1 (validation_error)
      - wrapped cmd exits N → exit N
      - SIGINT during --hold → exit 130 (cleanup runs first)
      - SIGTERM during --hold → exit 143 (cleanup runs first)

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

    # 3. --hold dispatch (Plan 03-05 — replaces the stub from Plan 03-04).
    #
    # Exit code mapping for --hold:
    #   - acquire failed (HeldByAnother) → exit 3 (held_by_another)
    #   - empty cmd (validation) → exit 1 (validation_error)
    #   - wrapped cmd spawned, exits N → exit N (propagated via SystemExit)
    #   - SIGINT during --hold → exit 130 (lock_hold_run's signal handler fires cleanup)
    #   - SIGTERM during --hold → exit 143 (same shape)
    #
    # The success path returns through SystemExit(exit_code) so the wrapped
    # subprocess's exit code becomes the process exit code. For example:
    #   em-proj state lock --hold foo -- false  → exits 1 (wrapped command's code)
    #   em-proj state lock --hold foo -- true   → exits 0
    if hold:
        if not cmd:
            # Empty cmd: validate before any Redis call.
            emit_error(
                "validation_error",
                "--hold requires a command after `--`",
                json_mode=json_mode,
            )
        # Redis pre-check before the hold runner (D-18 chokepoint).
        client = get_client()
        die_if_redis_unreachable(client)
        try:
            exit_code = lock_hold_run(name, ttl or DEFAULT_TTL, reason, cmd, json_mode=json_mode)
            raise SystemExit(exit_code)
        except HeldByAnother as e:
            # Lock is held by another process — exit 3 with held_by_another envelope.
            emit_held_by_another(
                "held_by_another",
                f"Lock '{name}' held by session "
                f"{e.holder['session_id'] if e.holder else 'unknown'}",
                holder=e.holder,
                json_mode=json_mode,
            )
        except ValidationError as e:
            emit_error(e.code, e.message, json_mode=json_mode)

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


@state_app.command("claim")
def claim(
    area: Annotated[str, typer.Argument(help="The area to claim.")],
    ttl: Annotated[
        int | None,
        typer.Option(
            "--ttl",
            min=CLAIM_MIN_TTL,
            max=CLAIM_MAX_TTL,
            help=f"Claim TTL in seconds (default {CLAIM_TTL_DEFAULT}; range {CLAIM_MIN_TTL}–{CLAIM_MAX_TTL}).",
        ),
    ] = None,
    reason: Annotated[
        str | None,
        typer.Option("--reason", help="Free-form reason metadata (max 256 chars)."),
    ] = None,
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Declare a long-lived claim over an area.

    Refreshes TTL if the current session already holds the claim (idempotent).

    Exit code mapping:
      0 = claimed or TTL refreshed
      1 = error (anonymous claim refused, validation error)
      3 = area already held by another session
    """
    # 1. Resolve json_mode first so anonymous-refusal error has correct format.
    json_mode = resolve_json_mode(json_flag)

    # 2. CLAIM-03 / T-4-02-01: anonymous refusal gate — BEFORE any Redis call.
    #    The pid- fallback in identity.py means IdentityResolutionError is never
    #    raised in practice; an explicit env check is the correct gate here.
    if not os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip():
        emit_error("anonymous_claim", "anonymous claims refused", json_mode=json_mode)

    # 3. Redis pre-check (D-18 chokepoint).
    client = get_client()
    die_if_redis_unreachable(client)

    # 4. Call claim op and emit result.
    effective_ttl = ttl if ttl is not None else CLAIM_TTL_DEFAULT
    try:
        holder = claim_take(area, ttl=effective_ttl, reason=reason)
    except ClaimHeldByAnother as e:
        emit_held_by_another(
            "held_by_another",
            f"Area '{area}' claimed by session "
            f"{e.holder['session_id'] if e.holder else 'unknown'}",
            holder=e.holder,
            json_mode=json_mode,
        )
    except Exception as e:
        # ValidationError from claim.py — has .code and .message attributes
        if hasattr(e, "code") and hasattr(e, "message"):
            emit_error(e.code, e.message, json_mode=json_mode)
        raise

    emit_ok(
        {
            "area": area,
            "ttl": effective_ttl,
            "claimed_at": holder["claimed_at"],
            "expires_at": holder["expires_at"],
        },
        json_mode=json_mode,
    )


@state_app.command("release")
def release(
    area: Annotated[str, typer.Argument(help="The area to release.")],
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Release a long-lived claim held by the current session.

    Exit code mapping:
      0 = released successfully
      2 = not held (absent or expired)
      3 = held by another session
    """
    # 1. Resolve json_mode (D-15/D-16).
    json_mode = resolve_json_mode(json_flag)

    # 2. Redis pre-check (D-18 chokepoint).
    client = get_client()
    die_if_redis_unreachable(client)

    # 3. Attempt release; handle HeldByAnother per ROADMAP SC#3.
    try:
        claim_release(area)
    except ClaimHeldByAnother as e:
        # holder=None → key was absent (expired or never claimed) → exit 2 (not_found).
        # holder set → different session holds the claim → exit 3 (held_by_another).
        if e.holder is None:
            emit_not_found(f"Area '{area}' is not claimed", json_mode=json_mode)
        else:
            emit_held_by_another(
                "held_by_another",
                f"Area '{area}' is held by session {e.holder['session_id']}",
                holder=e.holder,
                json_mode=json_mode,
            )
    except Exception as e:
        if hasattr(e, "code") and hasattr(e, "message"):
            emit_error(e.code, e.message, json_mode=json_mode)
        raise

    # 4. Success.
    emit_ok({"area": area, "released": True}, json_mode=json_mode)


@state_app.command("check")
def check(
    area: Annotated[str, typer.Argument(help="The area to check.")],
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Check whether an area is claimed and return holder metadata.

    Exit code mapping:
      0 = area is held (by anyone); returns full 5-field holder dict
      2 = area is not claimed
    """
    # 1. Resolve json_mode (D-15/D-16).
    json_mode = resolve_json_mode(json_flag)

    # 2. Redis pre-check (D-18 chokepoint).
    client = get_client()
    die_if_redis_unreachable(client)

    # 3. Check claim state; emit result.
    try:
        holder = claim_check(area)
    except ClaimNotHeld:
        emit_not_found(f"Area '{area}' is not claimed", json_mode=json_mode)
    except Exception as e:
        if hasattr(e, "code") and hasattr(e, "message"):
            emit_error(e.code, e.message, json_mode=json_mode)
        raise

    # 4. Success: emit all 5 holder fields (CLAIM-02 / ROADMAP SC#2).
    emit_ok({"area": area, "holder": holder}, json_mode=json_mode)
