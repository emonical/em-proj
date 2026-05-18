"""Unit tests for em_proj.redis_client — lazy-init contract + error-translation UX.

These tests do NOT require a running Redis instance — they stub `client.ping()` via
monkeypatch. Real-Redis integration tests live in tests/multiprocess/ (Plan 04).
"""
from __future__ import annotations

import pytest
import redis

import em_proj.redis_client as rc


@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    """Reset em_proj.redis_client._client between tests for full isolation."""
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()


def test_get_client_lazy_no_socket_on_import(monkeypatch) -> None:
    """get_client() returns a redis.Redis without opening a socket (no .ping())."""
    # Ensure env var doesn't bleed in from the developer's shell
    monkeypatch.delenv("EM_PROJ_REDIS_DB", raising=False)

    # Behavioral lazy check: if any code path opens a connection during
    # get_client(), .connect() will fire and raise — failing this test.
    # This is API-stable across redis-py patch releases (unlike inspecting
    # pool._created_connections, which is a private underscore attribute).
    import redis.connection

    def _fail_on_connect(self, *args, **kwargs):
        raise AssertionError(
            "redis.connection.Connection.connect() was called during get_client() — "
            "lazy contract violated (D-07)"
        )

    monkeypatch.setattr(redis.connection.Connection, "connect", _fail_on_connect)

    client = rc.get_client()
    assert isinstance(client, redis.Redis)
    # Pool exists but no socket has been opened (proved by monkeypatch above).
    assert client.connection_pool is not None


def test_get_client_reads_env_var(monkeypatch) -> None:
    """get_client() with no `db` arg falls back to EM_PROJ_REDIS_DB env var."""
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")
    client = rc.get_client()
    assert client.connection_pool.connection_kwargs["db"] == 15


def test_die_if_redis_unreachable_prints_actionable_message(monkeypatch, capsys) -> None:
    """die_if_redis_unreachable catches ConnectionError, prints actionable stderr, exits 1."""
    client = rc.get_client()

    # Stub client.ping() to raise ConnectionError as if Redis is down
    def _raise_connerr(*args, **kwargs):
        raise redis.ConnectionError("Connection refused")

    monkeypatch.setattr(client, "ping", _raise_connerr)

    with pytest.raises(SystemExit) as exc_info:
        rc.die_if_redis_unreachable(client)

    assert exc_info.value.code == 1, f"expected exit code 1, got {exc_info.value.code}"

    captured = capsys.readouterr()
    # D-17 message format — must contain the host:port AND the actionable suggestion
    assert "Redis unreachable at 127.0.0.1:6379" in captured.err, (
        f"expected actionable message in stderr, got {captured.err!r}"
    )
    assert "brew services start redis" in captured.err, (
        f"expected `brew services start redis` suggestion in stderr, got {captured.err!r}"
    )
    # NO traceback should appear (SystemExit subclass = clean exit)
    assert "Traceback" not in captured.err, (
        f"stderr leaked a traceback (D-17 violation): {captured.err!r}"
    )
    # And the actionable message must NOT be on stdout (PROJECT.md: errors to stderr)
    assert captured.out == "", f"unexpected stdout leak: {captured.out!r}"


def test_die_if_redis_unreachable_catches_timeout(monkeypatch, capsys) -> None:
    """die_if_redis_unreachable also handles TimeoutError (same UX as ConnectionError)."""
    client = rc.get_client()

    def _raise_timeout(*args, **kwargs):
        raise redis.TimeoutError("Timed out waiting for response")

    monkeypatch.setattr(client, "ping", _raise_timeout)

    with pytest.raises(SystemExit) as exc_info:
        rc.die_if_redis_unreachable(client)

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Redis unreachable" in captured.err
