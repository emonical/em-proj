"""Redis client wrapper — lazy module-level singleton, error-translating.

Single chokepoint per Phase 1 D-19: every Phase 2+ `em-proj state` verb calls through
this module. `get_client()` is lazy (no socket until first command). `die_if_redis_unreachable()`
catches ConnectionError + TimeoutError and prints a one-line actionable stderr message + exits 1
(per D-17). The EM_PROJ_REDIS_DB env var (default 0) lets the test harness point children at db=15.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

import redis

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6379
DEFAULT_DB = 0

_client: Optional[redis.Redis] = None


def get_client(db: int | None = None) -> redis.Redis:
    """Return process-singleton Redis client. Lazy — no connection until first command.

    Resolution order for `db`:
      1. explicit `db=` argument (if not None)
      2. EM_PROJ_REDIS_DB env var (Plan 04 harness sets this to 15 for children)
      3. DEFAULT_DB (0)

    redis.Redis(...) does NOT open a socket at construction; the pool opens
    connections on first command. Safe to call from --help / --version paths.
    """
    global _client
    if _client is None:
        if db is None:
            db = int(os.environ.get("EM_PROJ_REDIS_DB", str(DEFAULT_DB)))
        _client = redis.Redis(
            host=DEFAULT_HOST,
            port=DEFAULT_PORT,
            db=db,
            socket_connect_timeout=2.0,   # cap "is Redis up?" wait (RESEARCH A5)
            socket_timeout=5.0,           # cap stuck-command wait
            decode_responses=True,        # str in/out, not bytes
        )
    return _client


class _RedisUnreachable(SystemExit):
    """Sentinel SystemExit subclass carrying exit code 1.

    Subclassing SystemExit (not raising RuntimeError) means the interpreter
    unwinds cleanly without printing a traceback — D-17's no-traceback contract.
    """

    def __init__(self) -> None:
        super().__init__(1)


def die_if_redis_unreachable(client: redis.Redis) -> None:
    """Verify Redis is reachable. On failure: actionable stderr + exit 1, no traceback.

    Call before any state command. The error message format is locked by D-17:
        em-proj: error: Redis unreachable at <host>:<port> — run `brew services start redis`
    """
    try:
        client.ping()
    except (redis.ConnectionError, redis.TimeoutError):
        print(
            f"em-proj: error: Redis unreachable at {DEFAULT_HOST}:{DEFAULT_PORT} — "
            "run `brew services start redis`",
            file=sys.stderr,
        )
        raise _RedisUnreachable()


def _reset_for_tests() -> None:
    """Test-only helper — reset the module singleton. Do not call from production code."""
    global _client
    _client = None
