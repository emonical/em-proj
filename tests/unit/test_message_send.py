"""Unit tests for Phase 10 send/subscribe ops in em_proj.message._ops — RED scaffold.

Covers MSG-01..05 at the ops layer (live Redis db=15, no subprocess). These tests
import not-yet-existing Phase 10 ops (send_directed, send_broadcast, send_topic,
subscribe_topic, unsubscribe_topic, enumerate_scope_recipients); until Wave 1
(10-02) creates them the module-level import below raises ImportError — the
correct RED state for Wave 0.

Fixture pattern (autouse pair) copied verbatim from tests/unit/test_mailbox.py.
Session helpers (_unique_session_id, _register_session_for_test) copied verbatim
from tests/multiprocess/test_session_registry.py.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
import redis as redis_module

import em_proj.redis_client as rc
from em_proj.message import _ops as message_ops
from em_proj.message._ops import (
    TOPIC_KEY_PREFIX,
    enumerate_scope_recipients,
    mailbox_inbox,
    send_broadcast,
    send_directed,
    send_topic,
    subscribe_topic,
    unsubscribe_topic,
)
from em_proj.session._ops import SessionNotFound
from em_proj.state.kv import ValidationError

# ---------------------------------------------------------------------------
# Autouse fixtures — verbatim from test_mailbox.py (lines 31–44)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    """Reset the redis_client singleton so each test honors EM_PROJ_REDIS_DB=15."""
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()


@pytest.fixture(autouse=True)
def _point_session_at_test_db(monkeypatch):
    """Force message._ops's get_client() onto db=15."""
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")


# ---------------------------------------------------------------------------
# Helpers — verbatim from test_session_registry.py (lines 133–189)
# ---------------------------------------------------------------------------


def _unique_session_id() -> str:
    """Generate a unique session_id for test isolation.

    Includes a uuid4 suffix in addition to pid + time_ns: rapid successive calls
    within one process can share a time_ns() value on coarse-granularity clocks
    (macOS), which would collapse two 'distinct' sessions onto one Redis key.
    """
    return f"test-sess-{os.getpid()}-{time.time_ns()}-{uuid.uuid4().hex[:8]}"


def _register_session_for_test(session_id, client):
    """Write a test session record to Redis db=15 with the test runner's live pid.

    The test runner's own pid/proc_start/boot_id make is_holder_stale return False,
    so the session counts as live for session_list()/session_show() for the
    duration of the test.
    """
    import em_proj.session._ops as ops
    from em_proj.identity import current_process_composite, resolve_upstream_identity

    composite = current_process_composite()
    upstream_identity = resolve_upstream_identity()
    cwd = os.getcwd()
    now = time.time()
    redis_key = ops.KEY_PREFIX + session_id

    client.hset(
        redis_key,
        mapping={
            "session_id": session_id,
            "project_hash": composite["project_hash"],
            "upstream_identity": upstream_identity,
            "pid": str(composite["pid"]),
            "proc_start_epoch": str(composite["proc_start_epoch"]),
            "boot_id": composite["boot_id"],
            "cwd": cwd,
            "registered_at": str(now),
            "last_heartbeat": str(now),
        },
    )
    client.expire(redis_key, ops.TTL_DEFAULT)
    return {
        "session_id": session_id,
        "project_hash": composite["project_hash"],
        "upstream_identity": upstream_identity,
        "pid": composite["pid"],
        "proc_start_epoch": composite["proc_start_epoch"],
        "boot_id": composite["boot_id"],
        "cwd": cwd,
        "registered_at": now,
        "last_heartbeat": now,
    }


@pytest.fixture
def redis_client(clean_db):
    """Return the db=15 redis client (clean_db FLUSHDBs before/after each test)."""
    return clean_db


# ---------------------------------------------------------------------------
# MSG-01 — directed send
# ---------------------------------------------------------------------------


def test_send_directed_writes_to_recipient_mailbox(redis_client, monkeypatch) -> None:
    """send_directed writes the body to the recipient's mailbox with pattern='direct'."""
    sender = _unique_session_id()
    recipient = _unique_session_id()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sender)
    _register_session_for_test(recipient, redis_client)

    result = send_directed(recipient, "hello there", "machine")
    assert result["recipients_written"] == 1, (
        f"directed send must write to exactly 1 recipient; got {result['recipients_written']}"
    )

    msgs = mailbox_inbox(recipient, since=None, peek=True)
    assert len(msgs) == 1, f"recipient mailbox must hold 1 message; got {len(msgs)}"
    assert msgs[0]["body"] == "hello there"
    assert msgs[0]["from_session"] == sender
    assert msgs[0]["pattern"] == "direct"


def test_send_directed_raises_session_not_found(redis_client, monkeypatch) -> None:
    """send_directed raises SessionNotFound when the recipient is not in the registry."""
    sender = _unique_session_id()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sender)
    absent = _unique_session_id()  # never registered

    with pytest.raises(SessionNotFound):
        send_directed(absent, "hi", "machine")


def test_send_directed_metadata_is_flat_scalars(redis_client, monkeypatch) -> None:
    """Delivery metadata has the five MSG-05 keys, all scalar (MSG-05 + Pitfall 5)."""
    sender = _unique_session_id()
    recipient = _unique_session_id()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sender)
    _register_session_for_test(recipient, redis_client)

    result = send_directed(recipient, "hi", "machine")
    for key in ("recipients_written", "recipients_failed", "pub_published", "pattern", "scope"):
        assert key in result, f"delivery metadata missing key {key!r}: {result!r}"
    for k, v in result.items():
        assert isinstance(v, (int, str)), (
            f"metadata value for {k!r} must be a scalar (int|str) for flat TTY render; got {type(v)}"
        )
    assert result["pattern"] == "direct"
    assert result["scope"] == "machine"


# ---------------------------------------------------------------------------
# MSG-02 — broadcast
# ---------------------------------------------------------------------------


def test_send_broadcast_machine_writes_to_all_non_sender(redis_client, monkeypatch) -> None:
    """broadcast --scope machine writes to every live non-sender session."""
    sender = _unique_session_id()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sender)
    _register_session_for_test(sender, redis_client)
    r1 = _unique_session_id()
    r2 = _unique_session_id()
    _register_session_for_test(r1, redis_client)
    _register_session_for_test(r2, redis_client)

    result = send_broadcast("ping all", "machine")
    assert result["recipients_written"] == 2, (
        f"machine broadcast must reach both non-sender sessions; got {result['recipients_written']}"
    )
    assert len(mailbox_inbox(r1, peek=True)) == 1
    assert len(mailbox_inbox(r2, peek=True)) == 1


def test_send_broadcast_excludes_sender(redis_client, monkeypatch) -> None:
    """A sender does not receive its own broadcast (locked decision D-A5)."""
    sender = _unique_session_id()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sender)
    _register_session_for_test(sender, redis_client)
    other = _unique_session_id()
    _register_session_for_test(other, redis_client)

    send_broadcast("hi", "machine")
    assert mailbox_inbox(sender, peek=True) == [], "sender must not receive its own broadcast"
    assert len(mailbox_inbox(other, peek=True)) == 1


@pytest.mark.parametrize("bad_scope", ["global", "", "ALL", "Project"])
def test_send_broadcast_rejects_invalid_scope(bad_scope) -> None:
    """An invalid scope value raises ValidationError (before any Redis call)."""
    with pytest.raises(ValidationError):
        send_broadcast("hi", bad_scope)


def test_partial_delivery_counts_failures(redis_client, monkeypatch) -> None:
    """A mid-loop ConnectionError increments recipients_failed without aborting the fan-out."""
    sender = _unique_session_id()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sender)
    r1 = _unique_session_id()
    r2 = _unique_session_id()
    _register_session_for_test(r1, redis_client)
    _register_session_for_test(r2, redis_client)

    calls = {"n": 0}
    real_mbox_write = message_ops.mbox_write

    def flaky_mbox_write(session_id, msg):
        calls["n"] += 1
        if calls["n"] == 2:
            raise redis_module.ConnectionError("simulated mid-loop drop")
        return real_mbox_write(session_id, msg)

    monkeypatch.setattr(message_ops, "mbox_write", flaky_mbox_write)
    result = send_broadcast("hi", "machine")
    assert result["recipients_failed"] >= 1, "a mid-loop ConnectionError must be counted as a failure"
    assert result["recipients_written"] >= 1, "the surviving recipient must still be written"


# ---------------------------------------------------------------------------
# MSG-03 — topic subscribe / unsubscribe / send
# ---------------------------------------------------------------------------


def test_send_topic_delivers_to_subscriber(redis_client, monkeypatch) -> None:
    """send_topic delivers to a session that subscribed to the topic."""
    sender = _unique_session_id()
    subscriber = _unique_session_id()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sender)
    _register_session_for_test(subscriber, redis_client)
    subscribe_topic(subscriber, "alerts", "machine")

    result = send_topic("alerts", "machine", "topic body")
    assert result["recipients_written"] == 1
    msgs = mailbox_inbox(subscriber, peek=True)
    assert len(msgs) == 1
    assert msgs[0]["topic"] == "alerts"
    assert msgs[0]["pattern"] == "topic"


def test_unsubscribe_stops_topic_delivery(redis_client, monkeypatch) -> None:
    """After unsubscribe_topic, send_topic no longer delivers to that session."""
    sender = _unique_session_id()
    subscriber = _unique_session_id()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sender)
    _register_session_for_test(subscriber, redis_client)
    subscribe_topic(subscriber, "alerts", "machine")
    unsubscribe_topic(subscriber, "alerts", "machine")

    result = send_topic("alerts", "machine", "should not arrive")
    assert result["recipients_written"] == 0
    assert mailbox_inbox(subscriber, peek=True) == []


def test_send_topic_intersects_live_sessions_only(redis_client, monkeypatch) -> None:
    """A subscribed-but-dead session_id in the topic SET does not receive the message."""
    sender = _unique_session_id()
    live_sub = _unique_session_id()
    ghost_sub = _unique_session_id()  # subscribed but never registered (not live)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sender)
    _register_session_for_test(live_sub, redis_client)
    subscribe_topic(live_sub, "news", "machine")
    subscribe_topic(ghost_sub, "news", "machine")

    result = send_topic("news", "machine", "hi")
    assert result["recipients_written"] == 1, "only the live subscriber should receive"
    assert len(mailbox_inbox(live_sub, peek=True)) == 1
    assert mailbox_inbox(ghost_sub, peek=True) == []


def test_subscribe_rejects_topic_with_space_and_bang() -> None:
    """_validate_topic (via subscribe_topic) rejects a topic with a space and a bang."""
    with pytest.raises(ValidationError):
        subscribe_topic("test-sess-x", "invalid topic!", "machine")


def test_subscribe_rejects_empty_topic() -> None:
    """_validate_topic (via subscribe_topic) rejects an empty topic string."""
    with pytest.raises(ValidationError):
        subscribe_topic("test-sess-x", "", "machine")


def test_subscribe_rejects_topic_over_128_chars() -> None:
    """_validate_topic (via subscribe_topic) rejects a topic longer than 128 chars."""
    with pytest.raises(ValidationError):
        subscribe_topic("test-sess-x", "a" * 129, "machine")


# ---------------------------------------------------------------------------
# MSG-04 — scope enumeration helper
# ---------------------------------------------------------------------------


def test_enumerate_scope_recipients_excludes_sender(redis_client, monkeypatch) -> None:
    """enumerate_scope_recipients('machine') returns all live sessions except the sender."""
    sender = _unique_session_id()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sender)
    _register_session_for_test(sender, redis_client)
    other = _unique_session_id()
    _register_session_for_test(other, redis_client)

    recipients = enumerate_scope_recipients("machine", exclude_session_id=sender)
    assert sender not in recipients, "explicit exclude_session_id must drop the sender (Pitfall 1)"
    assert other in recipients


def test_topic_key_prefix_value() -> None:
    """TOPIC_KEY_PREFIX is the locked 'topic:' namespace (A1)."""
    assert TOPIC_KEY_PREFIX == "topic:"
