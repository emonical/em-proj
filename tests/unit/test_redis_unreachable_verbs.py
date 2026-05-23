"""REDIS-02 + D-19 coverage at the verb level.

Monkey-patches the redis client singleton's ``ping()`` to raise ``ConnectionError`` /
``TimeoutError``, invokes each state verb via ``CliRunner``, asserts exit 1 + the
D-17 actionable stderr line + no Python traceback. Confirms the D-18 single-chokepoint
holds at the user-facing surface — no verb catches the exception itself; the
``em_proj.redis_client`` wrapper owns connection-error translation.

These tests run WITHOUT a real Redis (the whole point is to simulate unreachability).
They deliberately do NOT depend on the ``clean_db`` fixture from ``tests/conftest.py``.
"""
from __future__ import annotations

import pytest
import redis
from typer.testing import CliRunner

import em_proj.redis_client as rc
from em_proj.cli import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    """Reset the redis_client module singleton before and after each test.

    Without this hygiene, the first test to build the singleton fixes its state
    for the rest of the session; subsequent monkeypatches would apply to a
    different `ping` than the verbs actually call.
    """
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()


@pytest.fixture
def runner() -> CliRunner:
    """CliRunner with stdout/stderr separated.

    ``mix_stderr=False`` was the explicit knob in click <8.2; in click >=8.2 the
    separation is the default and the kwarg was removed. Try the explicit form
    first for forward-compat clarity; fall back to the bare ``CliRunner()``.
    """
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def _raise_connerr(*args, **kwargs):
    """Replacement for client.ping that simulates Redis unreachable.

    ``redis.ConnectionError`` is what redis-py raises when the TCP socket can't
    be established (refused/timeout/RST). The wrapper's ``die_if_redis_unreachable``
    catches this exact type plus ``redis.TimeoutError``.
    """
    raise redis.ConnectionError("Connection refused")


def _raise_timeouterr(*args, **kwargs):
    """Replacement for client.ping that simulates a stuck/dead Redis.

    Distinct from ``ConnectionError`` in redis-py's class hierarchy; the wrapper
    must handle BOTH for REDIS-02 to be honestly covered.
    """
    raise redis.TimeoutError("timed out")


# ---------------------------------------------------------------------------
# REDIS-02 verb-level coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["state", "get", "anykey"],
        ["state", "set", "anykey", "anyvalue"],
        ["state", "del", "anykey"],
        ["state", "list"],
    ],
    ids=["get", "set", "del", "list"],
)
def test_each_verb_surfaces_redis_unreachable_message(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """Every state verb surfaces the locked D-17 stderr line + exit 1 when Redis is down.

    Asserts the four D-17 invariants end-to-end through the verb path:
      1. exit code 1 (semantic "error" per CLI-04)
      2. stderr contains "Redis unreachable at 127.0.0.1:6379"
      3. stderr contains "brew services start redis" (the actionable hint)
      4. stderr contains no Python traceback (no-traceback contract)
    """
    client = rc.get_client()
    monkeypatch.setattr(client, "ping", _raise_connerr)

    result = runner.invoke(app, argv)

    assert result.exit_code == 1, (
        f"expected exit 1 for unreachable Redis; got {result.exit_code}. "
        f"stdout={result.stdout!r} stderr={getattr(result, 'stderr', '<none>')!r}"
    )
    assert "Redis unreachable at 127.0.0.1:6379" in result.stderr, (
        f"D-17 stderr line missing; got stderr={result.stderr!r}"
    )
    assert "brew services start redis" in result.stderr, (
        f"D-17 actionable hint missing; got stderr={result.stderr!r}"
    )
    assert "Traceback" not in result.stderr, (
        f"D-17 no-traceback contract violated; got stderr={result.stderr!r}"
    )
    assert result.stdout == "", (
        f"PROJECT.md: errors go to stderr only; got stdout={result.stdout!r}"
    )


def test_verb_does_not_swallow_connection_error(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Named regression gate: D-18 forbids verbs catching redis.ConnectionError.

    If a future edit added ``except redis.ConnectionError`` inside a verb, this
    test would either pass with exit 0 (silent swallow) or surface a different
    error message — neither matches the D-17 contract below. The test name itself
    is the breadcrumb a code reviewer scans for.
    """
    client = rc.get_client()
    monkeypatch.setattr(client, "ping", _raise_connerr)

    result = runner.invoke(app, ["state", "get", "anykey"])

    assert result.exit_code == 1, (
        f"verb appears to have swallowed ConnectionError — wrapper's exit-1 path "
        f"not reached. exit={result.exit_code} stderr={result.stderr!r}"
    )
    assert "Redis unreachable" in result.stderr, (
        "wrapper's D-17 message missing — a verb likely caught the exception."
    )


def test_redis_unreachable_also_handles_timeout_via_verbs(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirm the wrapper handles ``TimeoutError`` (not just ``ConnectionError``).

    ``die_if_redis_unreachable`` catches a tuple of both types; this test exercises
    the second branch end-to-end through a verb.
    """
    client = rc.get_client()
    monkeypatch.setattr(client, "ping", _raise_timeouterr)

    result = runner.invoke(app, ["state", "list"])

    assert result.exit_code == 1
    assert "Redis unreachable at 127.0.0.1:6379" in result.stderr
    assert "brew services start redis" in result.stderr
    assert "Traceback" not in result.stderr
