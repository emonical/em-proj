"""Pure KV operations for `em-proj state` — no typer, unit-testable in isolation.

Per D-17 this module owns ALL the KV business logic; the verb wiring in
`em_proj/state/__init__.py` (Plan 04) is a thin translation layer: parse argv →
call a function here → call `emit_*`. Nothing in this file imports `typer`.

Per D-18 every Redis handle comes from `em_proj.redis_client.get_client()` — the
single chokepoint carried forward from Phase 1 D-19. This module NEVER constructs
`redis.Redis()` directly and NEVER catches `redis.ConnectionError`; the wrapper
owns connection-error translation and the verb layer calls
`die_if_redis_unreachable(client)` before any op here.

Key namespacing (D-06 / D-07):
  - Every kv key is stored in Redis as ``state:kv:<user-key>`` (KEY_PREFIX).
  - The user-typed key is the bare suffix; ``kv_list()`` strips the prefix so the
    caller sees what they typed, never the raw Redis key.
  - ``kv_list()`` scopes to ``SCAN MATCH state:kv:*`` only (D-08) — it never
    touches the ``state:lock:*`` or ``state:claim:*`` namespaces that Phases 3/4
    add as siblings.

KEEPTTL semantics (D-12):
  Redis' default ``SET`` clears any existing TTL. That is surprising for an
  "update the value, keep the lifetime" mental model. ``kv_set`` with no explicit
  ``ttl`` therefore issues ``SET ... KEEPTTL`` so a bare update preserves the
  remaining TTL. An explicit ``ttl=<int>`` overrides via ``SET ... EX <ttl>``.

Value-size cap (Claude's discretion):
  Values are capped at ``MAX_VALUE_BYTES`` (1 MiB UTF-8). Redis itself allows up
  to 512 MiB per value; the 1 MiB guard is a sanity check against accidentally
  piping a binary into ``state set``. Oversized values raise ``ValidationError``
  before any Redis call.

SCAN consistency (threat T-2-03-06, accepted): Redis ``SCAN`` is best-effort
under concurrent writes — it may double-return or miss a key inserted mid-scan.
That is acceptable for ``state list``, a developer/debugging surface rather than
an authoritative ledger.
"""
from __future__ import annotations

import re

from em_proj.redis_client import get_client

# --- Locked constants -------------------------------------------------------

#: D-06 — two-segment prefix for the kv verb family. The user-typed key is the
#: bare suffix appended to this; Phases 3/4 use ``state:lock:`` / ``state:claim:``.
KEY_PREFIX: str = "state:kv:"

#: D-09 — letters, digits, underscore, dot, dash, slash. The ``-`` is escaped so
#: it is a literal inside the character class; the ``/`` is a literal too. This
#: rejects whitespace, colons (would collide with the KEY_PREFIX delimiter), and
#: shell metacharacters. The ``+`` requires at least one character (empty
#: strings are rejected).
KEY_REGEX = re.compile(r"^[a-zA-Z0-9_.\-/]+$")

#: Claude's discretion — 1 MiB UTF-8 cap on a kv value. Sanity guard against
#: accidentally piping a binary into ``state set``; well under Redis' 512 MiB.
MAX_VALUE_BYTES: int = 1_048_576


# --- Exceptions -------------------------------------------------------------


class KvNotFound(KeyError):
    """Raised by ``kv_get`` when a key is absent (D-10).

    A distinct subclass of ``KeyError`` so the verb layer in Plan 04 can dispatch
    on it specifically — translating it to exit 2 plus the locked error envelope
    ``{code: "not_found", message: "key '<key>' not set"}``. Distinguishing
    "missing key" from "value was the empty string" is the whole point of D-10.
    """


class ValidationError(ValueError):
    """Raised when a key fails the regex or a value exceeds the size cap.

    Carries a machine-readable ``code`` attribute so the verb layer can pass it
    straight to ``emit_error(code, message)`` without re-deriving it. Per threat
    T-2-03-05 the message never echoes the rejected value — only the regex spec
    or the byte cap — to avoid information disclosure through error text.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code: str = code
        self.message: str = message


# --- Validation -------------------------------------------------------------


def validate_key(key: str) -> None:
    """Raise ``ValidationError`` unless ``key`` fully matches ``KEY_REGEX`` (D-09).

    Empty strings are rejected (the regex requires ``+``). Whitespace, colons,
    and shell metacharacters are all rejected. This gate runs BEFORE the
    KEY_PREFIX is concatenated, so a key containing ``:`` can never be used to
    write into the ``state:lock:*`` / ``state:claim:*`` namespaces (threat
    T-2-03-01).
    """
    if KEY_REGEX.fullmatch(key) is None:
        raise ValidationError(
            code="validation_error",
            message="key must match [a-zA-Z0-9_.-/]+",
        )


# --- KV operations ----------------------------------------------------------


def kv_get(key: str) -> str:
    """Return the value stored for ``key``, or raise ``KvNotFound`` if absent.

    Per D-10 a key set to the empty string is NOT missing: ``kv_get`` returns
    ``""`` for it and only raises ``KvNotFound`` when Redis ``GET`` returns
    ``None``. Validates the key first (D-09).
    """
    validate_key(key)
    value = get_client().get(KEY_PREFIX + key)
    if value is None:
        raise KvNotFound(key)
    return value


def kv_set(key: str, value: str, ttl: int | None = None) -> None:
    """Write ``value`` under ``key``, applying TTL semantics per D-12.

    - ``ttl`` is ``None`` (the default): issue ``SET ... KEEPTTL`` so a bare
      update preserves any existing TTL. On a brand-new key KEEPTTL is a safe
      no-op (there is no TTL to preserve).
    - ``ttl`` is an int: issue ``SET ... EX <ttl>``, overriding any existing TTL.

    Validates the key first (D-09), then rejects values larger than
    ``MAX_VALUE_BYTES`` with ``ValidationError(code="value_too_large")`` before
    any Redis call (threat T-2-03-03).
    """
    validate_key(key)
    if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
        raise ValidationError(
            code="value_too_large",
            message=f"value exceeds {MAX_VALUE_BYTES} bytes",
        )
    client = get_client()
    redis_key = KEY_PREFIX + key
    if ttl is not None:
        client.set(redis_key, value, ex=ttl)
    else:
        client.set(redis_key, value, keepttl=True)


def kv_del(key: str) -> bool:
    """Delete ``key`` if present; return whether it existed (D-11).

    ``rm -f`` semantics — never raises for a missing key. Returns ``True`` when
    Redis ``DEL`` removed at least one key, ``False`` otherwise. Validates the
    key first (D-09).
    """
    validate_key(key)
    return get_client().delete(KEY_PREFIX + key) >= 1


def kv_list() -> list[str]:
    """Return every kv key (prefix stripped), sorted alphabetically.

    Scopes to ``SCAN MATCH state:kv:*`` only (D-08) — never the lock or claim
    namespaces. Each returned key has ``KEY_PREFIX`` stripped so the caller sees
    the user-typed form (D-07). Returns ``[]`` when there are no kv keys (D-13 —
    an empty list is a valid result, not an error).

    Uses a cursor-based SCAN (not ``KEYS``) for forward-compat with a growing
    keyspace; results are sorted for predictable diffing/scripting.
    """
    client = get_client()
    prefix_len = len(KEY_PREFIX)
    keys = [k[prefix_len:] for k in client.scan_iter(match=KEY_PREFIX + "*")]
    return sorted(keys)
