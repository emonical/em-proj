"""Phase 9 MBOX-01 durability test — proves messages persist for offline sessions
across the CLI boundary.

Activated in Wave 2 of Phase 10 when the `message send` verb ships; the assertion
on `returncode == 0` stays RED until 10-03 completes (in Wave 0/Wave 1 there is no
`message send` verb, so the send subprocess exits non-zero and this test FAILS —
the correct RED state, NOT a skip).

"Offline" here means a registered session that is not running a Phase 11 listener
daemon. The directed send still validates the recipient exists (Phase 10
recipient-existence check via session_show), so the recipient is registered in the
test; the message persists in its durable mailbox until the offline session reads it.
"""
from __future__ import annotations

import json
import os
import subprocess
import time

from tests.conftest import EM_PROJ_BIN, TEST_DB


def _register_session_for_test(session_id, client):
    """Write a test session record to Redis db=15 with the test runner's live pid.

    Copied from tests/multiprocess/test_session_registry.py — the runner's own
    pid/proc_start/boot_id make is_holder_stale return False, so session_show()
    treats the recipient as a live (but offline-from-a-daemon) session.
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


def test_mailbox_persists_for_offline_session(clean_db, redis_precheck) -> None:
    """MBOX-01: Messages written to a session's mailbox persist for offline retrieval.

    Step 1: `em-proj message send --to <offline_id> "hello offline"` from a sender —
            assert returncode == 0 (durable write even though the recipient is not
            running a listener).
    Step 2: `em-proj message inbox --json --peek` as the offline recipient —
            assert returncode == 0, the inbox has >= 1 message, and the message
            carries all 8 MBOX-04 fields.
    """
    offline_id = f"test-offline-{os.getpid()}-{time.time_ns()}"
    sender_id = f"test-sender-{os.getpid()}-{time.time_ns()}"

    # The recipient is a registered session that simply isn't running a listener
    # daemon ("offline"). Directed send validates recipient existence, so it must
    # be in the registry; durability is that the message survives in the mailbox
    # until the offline session reads it.
    _register_session_for_test(offline_id, clean_db)

    # Step 1 — send to the offline recipient
    child_env = {
        **os.environ,
        "CLAUDE_CODE_SESSION_ID": sender_id,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
    }
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "message", "send", "--to", offline_id, "--json", "hello offline"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    stdout, stderr = proc.communicate(timeout=15)
    assert proc.returncode == 0, (
        f"message send exited {proc.returncode}; stderr={stderr!r}; stdout={stdout!r}"
    )

    # Step 2 — read the offline recipient's mailbox
    child_env2 = {
        **os.environ,
        "CLAUDE_CODE_SESSION_ID": offline_id,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
    }
    proc2 = subprocess.Popen(
        [EM_PROJ_BIN, "message", "inbox", "--json", "--peek"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env2,
    )
    stdout2, stderr2 = proc2.communicate(timeout=15)
    assert proc2.returncode == 0, (
        f"message inbox exited {proc2.returncode}; stderr={stderr2!r}; stdout={stdout2!r}"
    )

    envelope = json.loads(stdout2)
    messages = envelope.get("data", [])
    assert len(messages) >= 1, "offline session's inbox must contain the persisted message"

    msg = messages[0]
    assert msg["body"] == "hello offline"
    for field in ("msg_id", "from_session", "pattern", "scope", "topic", "body", "sent_at", "ttl"):
        assert field in msg, f"MBOX-04 field {field!r} missing from persisted message: {msg!r}"
