"""Multi-process harness for `em-proj message` — TEST-04 3×3 delivery matrix.

Phase 1 design invariants (carried forward):
  - subprocess.Popen NOT multiprocessing.Process (macOS fork+exec safety)
  - .communicate(timeout=15) NOT .wait() (pipe-buffer deadlock prevention)
  - EM_PROJ_REDIS_DB=15 in every child env (never writes to prod db=0)
  - Distinct session_id per test (prevents same-session upsert masking)

Wave 0 state (this file as created by 10-01):
  - 9 mailbox-path cells (3 patterns × 3 scopes) are skip-stubs flagged for Wave 2
    (10-03), where the send/broadcast/subscribe CLI verbs ship and the bodies are
    activated against the harness helpers below.
  - 3 live-path cells (one per pattern) are skip-stubs flagged for Phase 11, where
    the listener daemon (`em-proj session listen`) ships.

Helpers (_unique_session_id, _register_session_for_test, _send_via_cli,
_inbox_via_cli) are defined now so Wave 2 fills test bodies without re-deriving
the harness.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import uuid

import pytest
import redis as redis_module

from tests.conftest import EM_PROJ_BIN, TEST_DB

# ---------------------------------------------------------------------------
# Helpers — copied verbatim from test_session_registry.py
# ---------------------------------------------------------------------------


def _unique_session_id() -> str:
    """Generate a unique session_id for test isolation.

    Includes a uuid4 suffix in addition to pid + time_ns: rapid successive calls
    can share a time_ns() value on coarse-granularity clocks (macOS), which would
    collapse two 'distinct' sessions onto one Redis key.
    """
    return f"test-sess-{os.getpid()}-{time.time_ns()}-{uuid.uuid4().hex[:8]}"


def _register_session_for_test(session_id: str, client: redis_module.Redis) -> dict:
    """Write a test session record to Redis db=15 with the test runner's live pid.

    The runner's own pid/proc_start/boot_id make is_holder_stale return False, so the
    registered session counts as live for session_list()/session_show() during the test.
    Override project_hash / upstream_identity on the returned key afterward to build
    cross-scope scenarios (per 10-RESEARCH "For scope testing").
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


def _send_via_cli(args: list[str], sender_session_id: str) -> tuple[subprocess.Popen, str, str]:
    """Run `em-proj message <args>` via subprocess.Popen + .communicate(timeout=15).

    Returns (proc, stdout, stderr). The caller asserts on proc.returncode.
    """
    child_env = {
        **os.environ,
        "CLAUDE_CODE_SESSION_ID": sender_session_id,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
    }
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "message"] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    stdout, stderr = proc.communicate(timeout=15)
    return proc, stdout, stderr


def _inbox_via_cli(session_id: str) -> list:
    """Run `em-proj message inbox --json --peek` for session_id; return the messages list."""
    child_env = {
        **os.environ,
        "CLAUDE_CODE_SESSION_ID": session_id,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
    }
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "message", "inbox", "--json", "--peek"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    stdout, _stderr = proc.communicate(timeout=15)
    envelope = json.loads(stdout)
    data = envelope.get("data", [])
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# Assertion / mutation helpers for the mailbox-path matrix
# ---------------------------------------------------------------------------

_MBOX04_FIELDS = ("msg_id", "from_session", "pattern", "scope", "topic", "body", "sent_at", "ttl")


def _override_field(client: redis_module.Redis, session_id: str, field: str, value: str) -> None:
    """Overwrite one field of a registered session record (for cross-scope tests)."""
    import em_proj.session._ops as ops

    client.hset(ops.KEY_PREFIX + session_id, field, value)


def _send_ok(args: list[str], sender_session_id: str) -> dict:
    """Run a `message send`/`broadcast` CLI call, assert exit 0, return the data dict."""
    proc, stdout, stderr = _send_via_cli(args, sender_session_id)
    assert proc.returncode == 0, (
        f"`message {' '.join(args)}` exited {proc.returncode}; stderr={stderr!r}; stdout={stdout!r}"
    )
    envelope = json.loads(stdout)
    assert envelope["status"] == "ok", f"expected ok envelope; got {envelope!r}"
    return envelope["data"]


def _assert_mbox04(msg: dict) -> None:
    """Assert a received message carries all 8 MBOX-04 fields."""
    for field in _MBOX04_FIELDS:
        assert field in msg, f"MBOX-04 field {field!r} missing from message: {msg!r}"


# ---------------------------------------------------------------------------
# Mailbox-path cells — 3 patterns × 3 scopes (TEST-04)
# ---------------------------------------------------------------------------


def test_directed_machine_scope(clean_db, redis_precheck) -> None:
    """directed × machine: send --to a registered session; it appears in that inbox."""
    sender = _unique_session_id()
    recipient = _unique_session_id()
    _register_session_for_test(recipient, clean_db)

    data = _send_ok(
        ["send", "--to", recipient, "--scope", "machine", "--json", "hello directed"],
        sender,
    )
    assert data["recipients_written"] == 1
    assert data["recipients_failed"] == 0
    assert data["pattern"] == "direct"

    msgs = _inbox_via_cli(recipient)
    assert len(msgs) == 1
    _assert_mbox04(msgs[0])
    assert msgs[0]["pattern"] == "direct"
    assert msgs[0]["scope"] == "machine"
    assert msgs[0]["body"] == "hello directed"


def test_directed_project_scope(clean_db, redis_precheck) -> None:
    """directed × project: scope is informational for directed; recipient still gets it."""
    sender = _unique_session_id()
    recipient = _unique_session_id()
    _register_session_for_test(recipient, clean_db)

    data = _send_ok(
        ["send", "--to", recipient, "--scope", "project", "--json", "hi proj"],
        sender,
    )
    assert data["recipients_written"] == 1
    msgs = _inbox_via_cli(recipient)
    assert len(msgs) == 1
    assert msgs[0]["scope"] == "project"
    assert msgs[0]["body"] == "hi proj"


def test_directed_upstream_scope(clean_db, redis_precheck) -> None:
    """directed × upstream: scope is informational for directed; recipient still gets it."""
    sender = _unique_session_id()
    recipient = _unique_session_id()
    _register_session_for_test(recipient, clean_db)

    data = _send_ok(
        ["send", "--to", recipient, "--scope", "upstream", "--json", "hi up"],
        sender,
    )
    assert data["recipients_written"] == 1
    msgs = _inbox_via_cli(recipient)
    assert len(msgs) == 1
    assert msgs[0]["scope"] == "upstream"
    assert msgs[0]["body"] == "hi up"


def test_broadcast_machine_scope(clean_db, redis_precheck) -> None:
    """broadcast × machine: every live non-sender session receives; sender does not."""
    sender = _unique_session_id()
    r1 = _unique_session_id()
    r2 = _unique_session_id()
    _register_session_for_test(sender, clean_db)
    _register_session_for_test(r1, clean_db)
    _register_session_for_test(r2, clean_db)

    data = _send_ok(["broadcast", "--scope", "machine", "--json", "hello all"], sender)
    assert data["recipients_written"] == 2
    assert data["recipients_failed"] == 0

    assert len(_inbox_via_cli(r1)) == 1
    assert len(_inbox_via_cli(r2)) == 1
    assert _inbox_via_cli(sender) == [], "sender must not receive its own broadcast"


def test_broadcast_project_scope(clean_db, redis_precheck) -> None:
    """broadcast × project: only same-project sessions receive; out-of-project excluded."""
    sender = _unique_session_id()
    r_in = _unique_session_id()
    r_out = _unique_session_id()
    _register_session_for_test(sender, clean_db)
    _register_session_for_test(r_in, clean_db)
    _register_session_for_test(r_out, clean_db)
    _override_field(clean_db, r_out, "project_hash", "different-project-hash")

    data = _send_ok(["broadcast", "--scope", "project", "--json", "hello proj"], sender)
    assert data["recipients_written"] == 1, "only the in-project non-sender session should receive"

    assert len(_inbox_via_cli(r_in)) == 1
    assert _inbox_via_cli(r_out) == [], "out-of-project session must not receive"
    assert _inbox_via_cli(sender) == [], "sender must not receive its own broadcast"


def test_broadcast_upstream_scope(clean_db, redis_precheck) -> None:
    """broadcast × upstream: only same-upstream sessions receive; out-of-upstream excluded."""
    sender = _unique_session_id()
    r_in = _unique_session_id()
    r_out = _unique_session_id()
    _register_session_for_test(sender, clean_db)
    _register_session_for_test(r_in, clean_db)
    _register_session_for_test(r_out, clean_db)
    _override_field(clean_db, r_out, "upstream_identity", "different-host:other/repo")

    data = _send_ok(["broadcast", "--scope", "upstream", "--json", "hello upstream"], sender)
    assert data["recipients_written"] == 1, "only the in-upstream non-sender session should receive"

    assert len(_inbox_via_cli(r_in)) == 1
    assert _inbox_via_cli(r_out) == [], "out-of-upstream session must not receive"
    assert _inbox_via_cli(sender) == [], "sender must not receive its own broadcast"


def test_topic_machine_scope(clean_db, redis_precheck) -> None:
    """topic × machine: a subscriber receives a topic send; non-subscribers do not."""
    sender = _unique_session_id()
    subscriber = _unique_session_id()
    _register_session_for_test(subscriber, clean_db)
    topic = "test-topic-machine"

    sproc, _sout, serr = _send_via_cli(["subscribe", topic, "--scope", "machine", "--json"], subscriber)
    assert sproc.returncode == 0, f"subscribe exited {sproc.returncode}; stderr={serr!r}"

    data = _send_ok(["send", "--topic", topic, "--scope", "machine", "--json", "hello topic"], sender)
    assert data["recipients_written"] == 1

    msgs = _inbox_via_cli(subscriber)
    assert len(msgs) == 1
    _assert_mbox04(msgs[0])
    assert msgs[0]["pattern"] == "topic"
    assert msgs[0]["topic"] == topic
    assert msgs[0]["scope"] == "machine"


def test_topic_project_scope(clean_db, redis_precheck) -> None:
    """topic × project: subscriber in project scope receives a project-scoped topic send."""
    sender = _unique_session_id()
    subscriber = _unique_session_id()
    _register_session_for_test(subscriber, clean_db)
    topic = "test-topic-project"

    sproc, _sout, serr = _send_via_cli(["subscribe", topic, "--scope", "project", "--json"], subscriber)
    assert sproc.returncode == 0, f"subscribe exited {sproc.returncode}; stderr={serr!r}"

    data = _send_ok(["send", "--topic", topic, "--scope", "project", "--json", "hello topic proj"], sender)
    assert data["recipients_written"] == 1

    msgs = _inbox_via_cli(subscriber)
    assert len(msgs) == 1
    assert msgs[0]["topic"] == topic
    assert msgs[0]["scope"] == "project"


def test_topic_upstream_scope(clean_db, redis_precheck) -> None:
    """topic × upstream: subscriber in upstream scope receives an upstream-scoped topic send."""
    sender = _unique_session_id()
    subscriber = _unique_session_id()
    _register_session_for_test(subscriber, clean_db)
    topic = "test-topic-upstream"

    sproc, _sout, serr = _send_via_cli(["subscribe", topic, "--scope", "upstream", "--json"], subscriber)
    assert sproc.returncode == 0, f"subscribe exited {sproc.returncode}; stderr={serr!r}"

    data = _send_ok(["send", "--topic", topic, "--scope", "upstream", "--json", "hello topic up"], sender)
    assert data["recipients_written"] == 1

    msgs = _inbox_via_cli(subscriber)
    assert len(msgs) == 1
    assert msgs[0]["topic"] == topic
    assert msgs[0]["scope"] == "upstream"


# ---------------------------------------------------------------------------
# Live-path cells — pub/sub delivery (skip-stubs; activated in Phase 11)
# ---------------------------------------------------------------------------

_PHASE11_SKIP = (
    "Phase 11 listener daemon not yet available — "
    "enable once 'em-proj session listen' ships"
)


def test_live_delivery_directed(clean_db, redis_precheck) -> None:
    pytest.skip(_PHASE11_SKIP)


def test_live_delivery_broadcast(clean_db, redis_precheck) -> None:
    pytest.skip(_PHASE11_SKIP)


def test_live_delivery_topic(clean_db, redis_precheck) -> None:
    pytest.skip(_PHASE11_SKIP)
