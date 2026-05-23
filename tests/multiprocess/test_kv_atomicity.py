"""Multiprocess race tests for KV-01 atomicity.

Uses ``multiproc_race`` from ``tests/conftest.py`` to fork+exec real ``em-proj``
invocations against db=15 (via ``EM_PROJ_REDIS_DB`` injection in conftest).
Asserts that Redis SET/DEL serialize at the server (Phase 1 D-09: cross-session
safety is a Redis-server concern; KV ops are single Redis commands, so they are
server-atomic by construction — see Phase 2 CONTEXT.md threat T-2-04-06).

Why this test is meaningful even though Redis is single-threaded
----------------------------------------------------------------
Redis serializes commands at the server. The interesting failure modes are
client-side: a verb that read-modify-writes (e.g. GET then SET) could produce
torn state under concurrent invocations because the read and the write are
two separate round trips. Our ``state set`` is a single ``SET`` command (one
round trip → one Redis-server command-processor slot → atomic) so the only
legal outcomes are "valueA wins" or "valueB wins" — never a mix, never empty.
This test pins that invariant: if a future refactor introduces a read-modify-
write pattern, the post-state assertion will start flaking.

Imports
-------
Per Phase 1 SUMMARY (Plan 04, "Continuity tests for next phase"), the
documented import path for the shared race constants is::

    from tests.conftest import EM_PROJ_BIN, TEST_DB, RaceResult

(NOT the bare ``from conftest import …`` form — that only works under
rootdir-as-sys.path mode and is brittle to pytest config drift.)

``multiproc_race`` itself is a fixture, so it is injected per-test via the
function signature — not imported. The ``clean_db`` FLUSHDB-before-and-after
isolation comes in implicitly through ``multiproc_race``'s fixture dependency
chain (also per Phase 1 SUMMARY).
"""
from __future__ import annotations

import redis

from tests.conftest import EM_PROJ_BIN, TEST_DB, RaceResult


def test_concurrent_set_on_same_key_produces_single_winner(multiproc_race) -> None:
    """Two parallel ``state set`` on the same key produce exactly one winning value.

    Asserts KV-01 atomicity end-to-end (threat T-2-04-06):
      - Both children exit 0 (Redis SET on the same key both succeed — no lock
        semantics in KV; whichever arrives last wins).
      - The final value is exactly ``value_a`` OR exactly ``value_b`` — never
        partial, never empty, never a concatenation. The payloads are
        deliberately long (~600 chars each, distinct sentinels) so a half-
        written or interleaved value would be obviously broken.
    """
    value_a = "valueA" * 100
    value_b = "valueB" * 100

    results = multiproc_race(
        [
            [EM_PROJ_BIN, "state", "set", "racekey", value_a, "--json"],
            [EM_PROJ_BIN, "state", "set", "racekey", value_b, "--json"],
        ]
    )

    # Result shape — one RaceResult per launched command.
    assert len(results) == 2, f"expected 2 results, got {len(results)}"
    assert all(isinstance(r, RaceResult) for r in results)

    # Both children must succeed (KV-01 SET has no contention failure mode).
    for r in results:
        assert r.returncode == 0, (
            f"child failed: returncode={r.returncode} "
            f"stdout={r.stdout!r} stderr={r.stderr!r}"
        )

    # Post-race Redis state: exactly one of the two payloads, nothing else.
    # We open a fresh client (NOT em_proj.redis_client.get_client) so the
    # singleton state in the test process can never mask the real value.
    client = redis.Redis(
        host="127.0.0.1",
        port=6379,
        db=TEST_DB,
        decode_responses=True,
    )
    final = client.get("state:kv:racekey")
    assert final in (value_a, value_b), (
        f"torn write detected: final value is neither racer's payload — "
        f"got {final!r} (len={len(final) if final else 0})"
    )


def test_concurrent_set_and_del_race_produces_consistent_state(multiproc_race) -> None:
    """A parallel ``state set`` and ``state del`` on the same key produce one of two legal post-states.

    Both children must exit 0 (set has no contention failure; del is idempotent
    per D-11 and exits 0 whether or not the key existed). The post-state is
    either:
      - exactly ``"alive"`` (set landed AFTER del)
      - absent (del landed AFTER set, OR del ran on a key that was already
        absent because set had not yet flushed)

    The assertion is "one of those two outcomes, with no exception or partial
    state" — Redis ordering across racing clients is not deterministic, so the
    test never tries to pin a specific winner.
    """
    results = multiproc_race(
        [
            [EM_PROJ_BIN, "state", "set", "racekey", "alive", "--json"],
            [EM_PROJ_BIN, "state", "del", "racekey", "--json"],
        ]
    )

    assert len(results) == 2
    assert all(isinstance(r, RaceResult) for r in results)
    for r in results:
        assert r.returncode == 0, (
            f"child failed: returncode={r.returncode} "
            f"stdout={r.stdout!r} stderr={r.stderr!r}"
        )

    client = redis.Redis(
        host="127.0.0.1",
        port=6379,
        db=TEST_DB,
        decode_responses=True,
    )
    final = client.get("state:kv:racekey")
    assert final in ("alive", None), (
        f"inconsistent post-state: expected 'alive' or absent, got {final!r}"
    )
