"""CLI-05 output module — single source of truth for the JSON envelope (D-15).

This module owns every decision about how an `em-proj` verb communicates its
result to the caller. It is deliberately dependency-free: no `typer`, no
`redis`, no other `em_proj` module is imported here. That decoupling means the
envelope contract is independently unit-testable (capsys only — no `CliRunner`,
no live Redis) and downstream verbs collapse to three-liners
(`do redis op -> call emit_*`).

Envelope shape (D-01)
---------------------
Every verb — present and future — emits the SAME envelope, never a per-verb
shape::

    {"schema_version": "1", "status": "<ok|not_found|error>", "data": ..., "error": {...}}

`data` is present on success; `error` is present on non-success. A common
envelope means one parser (the Phase 5 `/global-state` skill, the Phase 6
`gsd-sdk` consumer) handles every verb across every subcommand family forever.

schema_version bump policy (D-02)
---------------------------------
`SCHEMA_VERSION` is an integer-valued string (`"1"`, `"2"`, ...). It bumps ONLY
on a breaking schema change: renaming a field, removing a field, or changing a
field's type. Adding a new optional field (e.g. a future `error.details` or
`error.retry_after`) is NON-breaking and does NOT bump the version — consumers
must ignore unknown keys gracefully (D-05).

Error sub-shape (D-03)
----------------------
The error object's minimal shape is `{code, message}`. The field names `code`
(machine-readable) and `message` (human-readable) are LOCKED FOREVER and must
never be renamed. Additional fields may be added later without a version bump.

stdout vs stderr (PROJECT.md)
-----------------------------
Success output (`emit_ok`) goes to stdout. Error output goes to stderr —
`emit_error` always writes to stderr; `emit_not_found` writes its human-mode
message to stderr (its JSON-mode envelope goes to stdout, since `not_found` is a
queryable result rather than a tool failure).

TTY detection (D-04, D-16)
--------------------------
Plain text is emitted when stdout is a TTY; compact JSON is emitted when stdout
is NOT a TTY or when `--json` is passed. The `json_mode` parameter on every
helper encodes this: `None` -> auto-detect via `sys.stdout.isatty()`, `True` ->
force JSON, `False` -> force plain text. The per-verb `--json/--no-json`
`typer.Option` (wired in Plan 04) resolves to this parameter at the call site;
no CLI plumbing lives in this module.

Information-disclosure note (threat T-2-02-03)
----------------------------------------------
This module does NOT inject file paths, environment values, or Redis connection
details into any message. The caller is responsible for constructing a clean,
leak-free `message` string before handing it to `emit_error` / `emit_not_found`.
"""
from __future__ import annotations

import json
import sys
from typing import Any, NoReturn

#: CLI-05 envelope schema version (D-02). Integer-valued string. Bump ONLY on a
#: breaking change (field rename/removal or type change); additive changes do not.
SCHEMA_VERSION = "1"


def resolve_json_mode(json_flag: bool | None) -> bool:
    """Resolve the effective output mode for a verb (D-16).

    - ``json_flag is True``  -> ``True``  (``--json`` forces JSON)
    - ``json_flag is False`` -> ``False`` (``--no-json`` forces plain text)
    - ``json_flag is None``  -> auto-detect: JSON when stdout is NOT a TTY.

    The non-TTY default is machine-safe (threat T-2-02-01): piped/redirected
    output gets structured JSON; an interactive terminal gets plain text.
    """
    if json_flag is True:
        return True
    if json_flag is False:
        return False
    return not sys.stdout.isatty()


def _dump(payload: dict[str, Any]) -> str:
    """Serialize an envelope to a compact single-line JSON string (D-04).

    `separators=(",", ":")` strips the whitespace `json.dumps` adds by default,
    keeping output compact and NDJSON-compatible for any future streaming verb.
    """
    return json.dumps(payload, separators=(",", ":"))


def emit_ok(data: Any = None, *, json_mode: bool | None = None) -> NoReturn:
    """Emit a success result and exit 0.

    JSON mode: writes ``{"schema_version", "status": "ok", "data": data}`` as
    compact JSON + newline to stdout.

    Plain mode: renders ``data`` for humans on stdout — a dict of scalars prints
    one ``key: value`` per line; a ``{"keys": [...]}`` payload prints one key
    per line; ``None`` prints nothing; anything else prints ``repr(data)``.

    Always raises ``SystemExit(0)``.
    """
    if resolve_json_mode(json_mode):
        sys.stdout.write(
            _dump({"schema_version": SCHEMA_VERSION, "status": "ok", "data": data})
            + "\n"
        )
    else:
        _render_plain(data)
    raise SystemExit(0)


def _render_plain(data: Any) -> None:
    """Render ``data`` as human-readable plain text on stdout (no envelope)."""
    if data is None:
        return
    if isinstance(data, dict):
        # A {"keys": [...]} payload (e.g. `state list`) prints one key per line.
        if set(data.keys()) == {"keys"} and isinstance(data["keys"], list):
            for key in data["keys"]:
                print(key)
            return
        # A flat dict of scalars prints as `key: value` lines.
        if all(not isinstance(v, (dict, list)) for v in data.values()):
            for key, value in data.items():
                print(f"{key}: {value}")
            return
    # Fallback for any shape we do not special-case: an unambiguous repr.
    print(repr(data))


def emit_not_found(message: str, *, json_mode: bool | None = None) -> NoReturn:
    """Emit a 'queried key/resource does not exist' result and exit 2.

    `not_found` is a distinct status from `error` (D-05): the tool worked
    correctly, the thing asked for simply is not there. Exit code 2 lets a
    caller distinguish it from a genuine failure (exit 1).

    JSON mode: writes the ``not_found`` envelope with an
    ``error: {code: "not_found", message}`` block to stdout (a queryable result,
    not a tool failure).

    Plain mode: writes ``message`` to stderr.

    Always raises ``SystemExit(2)``.
    """
    if resolve_json_mode(json_mode):
        sys.stdout.write(
            _dump(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "not_found",
                    "error": {"code": "not_found", "message": message},
                }
            )
            + "\n"
        )
    else:
        print(message, file=sys.stderr)
    raise SystemExit(2)


def emit_error(code: str, message: str, *, json_mode: bool | None = None) -> NoReturn:
    """Emit an error result and exit 1.

    `code` is a stable machine-readable identifier (e.g. ``validation_error``);
    `message` is the human-readable explanation. Both field names are locked by
    D-03.

    JSON mode: writes the ``error`` envelope to STDERR — errors go to stderr per
    the PROJECT.md constraint, even in machine-readable mode.

    Plain mode: writes ``em-proj: error: <message>`` to stderr.

    Always raises ``SystemExit(1)``.
    """
    if resolve_json_mode(json_mode):
        sys.stderr.write(
            _dump(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "error",
                    "error": {"code": code, "message": message},
                }
            )
            + "\n"
        )
    else:
        print(f"em-proj: error: {message}", file=sys.stderr)
    raise SystemExit(1)


#: Pinned tuple of holder-dict keys exposed in the ``held_by_another`` envelope's
#: ``data.holder`` subset (T-3-XX-02 / T-3-03-02 mitigations).
#:
#: INCLUDED: pid, session_id, project_hash, acquired_at, expires_at, reason.
#: These give the caller enough context to identify who holds the lock and when.
#:
#: EXCLUDED — reason and security:
#:   boot_id:          16-hex machine-identifier derived from boot time; including
#:                     it leaks a stable cross-session machine fingerprint (T-3-XX-02).
#:   proc_start_epoch: process creation epoch; disclosing it enables correlation of
#:                     process start times across different lock names, revealing
#:                     process-lifetime patterns (T-3-XX-02 correlation surface).
#:
#: This constant is the SINGLE source of truth. Tests import and assert it by
#: value to prevent accidental drift. Any change here requires deliberate security
#: review of the above rationale.
_HOLDER_DISCLOSURE_KEYS: tuple[str, ...] = (
    "name",
    "pid",
    "session_id",
    "project_hash",
    "acquired_at",
    "expires_at",
    "reason",
)


def emit_held_by_another(
    code: str,
    message: str,
    *,
    holder: dict | None = None,
    json_mode: bool | None = None,
) -> NoReturn:
    """Emit a held_by_another result and exit 3.

    Symmetric with ``emit_error`` but uses the ``held_by_another`` status value
    (D-09 displaced-holder-learns principle; D-05 status pre-announced in Phase 2)
    and exits with CLI-04 exit code 3 ("held by another" — first user).

    JSON mode: writes ``{"schema_version":"1","status":"held_by_another",
    "error":{"code":code,"message":message}}`` to stderr. If ``holder`` is not
    None, adds ``"data":{"holder":<sanitized subset>}`` — the subset is built
    by picking exactly the keys in ``_HOLDER_DISCLOSURE_KEYS`` from ``holder``
    (missing keys silently skipped). See ``_HOLDER_DISCLOSURE_KEYS`` for the
    security rationale on which keys are included and which are excluded.

    Plain mode: writes ``em-proj: <message>`` to stderr (mirrors ``emit_error``
    plain-mode shape but without the ``error: `` prefix, since the caller
    typically composes a full human message already).

    Always raises ``SystemExit(3)`` — CLI-04 exit code 3.

    Note: This function does NOT inject env values, file paths, or Redis host
    info into the message (T-3-XX-02 mitigation). The caller is responsible for
    constructing a clean, leak-free ``message`` string.
    """
    if resolve_json_mode(json_mode):
        envelope: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "status": "held_by_another",
            "error": {"code": code, "message": message},
        }
        if holder is not None:
            envelope["data"] = {
                "holder": {k: holder[k] for k in _HOLDER_DISCLOSURE_KEYS if k in holder}
            }
        sys.stderr.write(_dump(envelope) + "\n")
    else:
        print(f"em-proj: {message}", file=sys.stderr)
    raise SystemExit(3)
