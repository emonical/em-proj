"""Multi-process harness for `em-proj session` — TEST-03 validation.

Proves the four TEST-03 validation points from Phase 8 CONTEXT.md by
combining direct Python API calls (for session registration from a live
process) with real em-proj subprocess invocations (CLI boundary) for
the read side (list) and for state manipulation (claim/release).

Design rationale for register vs. list CLI boundary:
  `em-proj session register --json` is a short-lived process — it registers
  its own pid (os.getpid() inside the em-proj subprocess) and exits.  By the
  time `session list` runs, that pid is dead → is_holder_stale fires → the
  session is excluded and reaped.  This would make TEST-03 points 1 and 2
  unreachable via pure CLI register.

  The correct design: register sessions via Python API (so the test runner's
  stable pid is the registered pid) and verify via CLI `session list --json`
  (the CLI boundary that matters for TEST-03 visibility proof).  TEST-03
  points 3 and 4 explicitly exercise the dead-pid / TTL paths via the list
  CLI boundary.

TEST-03 validation points:
  Point 1 — A registered session appears in `em-proj session list --json`
             with correct metadata (session_id, pid, cwd, held structure).
  Point 2 — A session that has taken a claim shows held["claims"] >= 1 in
             the list enrichment (D4 cross-namespace join via CLI).
  Point 3 — A session whose pid is dead is excluded from the next list call
             and its Redis key is DELed (D3 lazy reaping via CLI list).
  Point 4 — A session whose Redis TTL has lapsed is absent from list (D2
             TTL backstop via CLI list).

Phase 1 design invariants (RESEARCH Pitfalls, carried forward):
  - subprocess.Popen NOT multiprocessing.Process (macOS fork+exec safety)
  - .communicate(timeout=15) NOT .wait() (pipe-buffer deadlock prevention)
  - EM_PROJ_REDIS_DB=15 in every child env (never writes to prod db=0)
  - Distinct session_id per test (prevents same-session upsert masking
    assertions — Phase 4 pitfall #4 carry)

Import pattern (per Phase 1 Plan 04 SUMMARY):
  from tests.conftest import EM_PROJ_BIN, TEST_DB
"""
from __future__ import annotations

import json
import os
import subprocess
import time

import pytest
import redis as redis_module

from tests.conftest import EM_PROJ_BIN, TEST_DB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session_list_via_cli(session_id: str) -> list:
    """Run `em-proj session list --json` via subprocess and return the data list.

    Injects CLAUDE_CODE_SESSION_ID=session_id and EM_PROJ_REDIS_DB=TEST_DB.
    The list verb is machine-global — any session_id returns all live sessions.

    Uses subprocess.Popen + .communicate(timeout=15) per Phase 1 design constraints.

    Returns:
      list of enriched session entries (the data[] from the envelope).
    """
    child_env = {
        **os.environ,
        "CLAUDE_CODE_SESSION_ID": session_id,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
    }
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "session", "list", "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    stdout, _stderr = proc.communicate(timeout=15)
    assert proc.returncode == 0, (
        f"em-proj session list --json exited {proc.returncode}; "
        f"stderr={_stderr!r}; stdout={stdout!r}"
    )
    try:
        envelope = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return []
    data = envelope.get("data", [])
    return data if isinstance(data, list) else []


def _state_claim_via_cli(area: str, session_id: str) -> int:
    """Run `em-proj state claim <area> --json` via subprocess.

    Returns the returncode (0 = claimed, non-zero = error).
    """
    child_env = {
        **os.environ,
        "CLAUDE_CODE_SESSION_ID": session_id,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
    }
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "state", "claim", area, "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    _stdout, _stderr = proc.communicate(timeout=15)
    return proc.returncode


def _state_release_via_cli(area: str, session_id: str) -> None:
    """Run `em-proj state release <area> --json` via subprocess. Fire-and-forget."""
    child_env = {
        **os.environ,
        "CLAUDE_CODE_SESSION_ID": session_id,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
    }
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "state", "release", area, "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    proc.communicate(timeout=15)


def _unique_session_id() -> str:
    """Generate a unique session_id for test isolation.

    Uses os.getpid() + time.time_ns() suffix to prevent collision with
    real Claude Code sessions or other test runs.
    """
    return f"test-sess-{os.getpid()}-{time.time_ns()}"


def _register_session_for_test(session_id: str, client: redis_module.Redis) -> dict:
    """Write a test session record to Redis db=15 with the test runner's live pid.

    Writes a 9-field session HASH (KEY_PREFIX + session_id) using the test
    runner's own process identity (pid, proc_start_epoch, boot_id, cwd) so
    that is_holder_stale returns False for the duration of the test.

    TTL is set to TTL_DEFAULT (300s) so the key doesn't expire mid-test.

    Returns the session dict written (mirrors the session_register() return format).
    """
    import em_proj.session._ops as ops
    from em_proj.identity import current_process_composite, resolve_upstream_identity

    composite = current_process_composite()
    # Override session_id so we use the test-generated one, not the real session
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


# ---------------------------------------------------------------------------
# Test 1 — TEST-03 point 1: registered session appears in CLI list with metadata
# ---------------------------------------------------------------------------


def test_registered_child_appears_in_list(clean_db) -> None:
    """Registered session appears in `em-proj session list --json` with correct metadata.

    TEST-03 point 1: write a session record (with the test runner's live pid so
    is_holder_stale returns False), then invoke `em-proj session list --json`
    via subprocess and assert the session appears with correct field types.

    The CLI boundary being tested is `session list` — the read side that every
    caller (including external tooling) uses to discover live sessions.

    Assertions:
      - session list entry found for the registered session_id
      - entry["session"]["pid"] is an int (coerced by _hgetall_to_session)
      - entry["session"]["cwd"] is a non-empty string
      - entry["held"] has "claims", "locks", "reserves" sub-keys
    """
    unique_id = _unique_session_id()

    # Register using the test runner's live pid (so is_holder_stale returns False)
    _register_session_for_test(unique_id, clean_db)

    # Verify via CLI list
    data = _session_list_via_cli(session_id=unique_id)
    matching = [e for e in data if e.get("session", {}).get("session_id") == unique_id]
    assert matching, (
        f"session_id {unique_id!r} not found in session list output; "
        "SESS-01/TEST-03 point 1 failed — registered session must appear in list"
    )

    entry = matching[0]
    session_rec = entry["session"]

    # pid must be an int (coerced by _hgetall_to_session in session_list)
    assert isinstance(session_rec.get("pid"), int), (
        f"session['pid'] must be int, got {type(session_rec.get('pid')).__name__!r}; "
        "_hgetall_to_session must coerce pid to int"
    )
    assert session_rec["pid"] > 0, "session['pid'] must be a positive integer"

    # cwd must be a non-empty string
    assert isinstance(session_rec.get("cwd"), str) and session_rec["cwd"], (
        f"session['cwd'] must be a non-empty string, got {session_rec.get('cwd')!r}"
    )

    # held structure must have all three sub-keys (D1 enrichment join)
    held = entry.get("held", {})
    for sub_key in ("claims", "locks", "reserves"):
        assert sub_key in held, (
            f"entry['held'] missing '{sub_key}' key; "
            "D1 enrichment join must populate all three namespaces"
        )


# ---------------------------------------------------------------------------
# Test 2 — TEST-03 point 2: enrichment shows held claim under session_id
# ---------------------------------------------------------------------------


def test_enrichment_shows_held_claim_under_session_id(clean_db) -> None:
    """CLI list enrichment shows claims count >= 1 after claiming in the same session.

    TEST-03 point 2: write a session record with the test runner's live pid,
    take a claim via CLI in that session_id, then invoke `session list --json`
    and assert held["claims"] >= 1 for our session.

    Proves the D4 cross-namespace enrichment join works end-to-end:
    state:claim:* SCAN → grouped by session_id → merged into session list output.
    """
    unique_id = _unique_session_id()
    claim_area = f"test-08-enrich-{os.getpid()}"

    # Register using the test runner's live pid (stable for the duration of the test)
    _register_session_for_test(unique_id, clean_db)

    # Take a claim via CLI in the same session_id
    rc_claim = _state_claim_via_cli(area=claim_area, session_id=unique_id)
    assert rc_claim == 0, (
        f"state claim {claim_area!r} failed with exit {rc_claim}; "
        "cannot test enrichment without a live claim"
    )

    try:
        # Verify via CLI list — enrichment must show the claim
        data = _session_list_via_cli(session_id=unique_id)
        matching = [e for e in data if e.get("session", {}).get("session_id") == unique_id]
        assert matching, (
            f"session_id {unique_id!r} not found in session list — "
            "session was registered but does not appear in list"
        )

        entry = matching[0]
        held = entry.get("held", {})
        claims_count = held.get("claims", 0)
        assert claims_count >= 1, (
            f"held['claims'] = {claims_count}; expected >= 1 after claiming {claim_area!r}. "
            "D4 enrichment join must count claims per session_id (TEST-03 point 2)"
        )

    finally:
        # Cleanup: release the claim regardless of assertion outcome
        _state_release_via_cli(area=claim_area, session_id=unique_id)


# ---------------------------------------------------------------------------
# Test 3 — TEST-03 point 3: session with dead pid excluded and reaped
# ---------------------------------------------------------------------------


def test_killed_child_excluded_and_reaped(clean_db) -> None:
    """Session with a dead pid is excluded from next list call and key is DELed.

    TEST-03 point 3: proves D2/D3 stale reaping via the lazy-reap path through
    the CLI `session list --json` boundary.

    Approach (canonical lazy-reap, per plan ALTERNATIVE section):
      1. Register a session via `em-proj session register --json`. The register
         subprocess records its own pid, then exits immediately (pid now dead).
      2. Give the subprocess time to fully exit.
      3. Call `em-proj session list --json` via subprocess — is_holder_stale
         detects the dead pid → session excluded from results + key DELed (D3).
      4. Assert session_id absent from list.
      5. Assert Redis key does not exist (DELed by D3 lazy reaping).

    This is the correct canonical approach for point 3: `session register --json`
    is intentionally short-lived; we're proving that the LIST verb correctly
    detects and reaps stale sessions, not that register keeps a process alive.
    """
    unique_id = _unique_session_id()
    redis_key = f"state:session:{unique_id}"

    child_env = {
        **os.environ,
        "CLAUDE_CODE_SESSION_ID": unique_id,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
    }

    # Step 1: Register via CLI — subprocess records its own pid, then exits
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "session", "register", "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    stdout, _stderr = proc.communicate(timeout=15)
    assert proc.returncode == 0, (
        f"session register exited {proc.returncode}; "
        f"stderr={_stderr!r}; stdout={stdout!r}"
    )

    # Verify the key exists in Redis (register wrote it)
    assert clean_db.exists(redis_key), (
        f"Redis key {redis_key!r} missing immediately after register; "
        "session register must write the session HASH"
    )

    # Step 2: Give the subprocess time to fully exit (pid now dead)
    time.sleep(0.2)

    # Step 3: Call session list via CLI — D3 lazy reaping must fire
    data_after_death = _session_list_via_cli(session_id=unique_id)
    matching_after_death = [
        e for e in data_after_death
        if e.get("session", {}).get("session_id") == unique_id
    ]
    assert not matching_after_death, (
        f"session_id {unique_id!r} still appears in session list after its pid died; "
        "D3 lazy reaping (is_holder_stale → DEL → exclude) did not fire. "
        "TEST-03 point 3 failed."
    )

    # Step 4: Verify the Redis key was DELed (not just hidden — full reaping proof)
    key_exists = clean_db.exists(redis_key)
    assert key_exists == 0, (
        f"Redis key {redis_key!r} still exists after stale reaping; "
        "session_list must call client.delete(key) on stale detection (D3). "
        "TEST-03 point 3 failed."
    )


# ---------------------------------------------------------------------------
# Test 4 — TEST-03 point 4: TTL-lapsed session absent from list
# ---------------------------------------------------------------------------


def test_ttl_lapse_session_absent_from_list(clean_db) -> None:
    """A session whose Redis TTL has lapsed is absent from `em-proj session list --json`.

    TEST-03 point 4: proves the D2 TTL backstop via the CLI list boundary.
    Rather than waiting the full TTL_DEFAULT (300 seconds), we use Redis EXPIRE
    to set TTL=1s, then sleep(2) to let the key expire, then assert absence.

    Implementation:
      1. Register a session via `em-proj session register --json`
      2. Force-expire the Redis key to 1 second via direct Redis client call
      3. Wait 2 seconds for the key to expire
      4. Call `session list --json` and assert the session is absent

    NOTE: do NOT wait 300 seconds — use Redis client to force-expire via EXPIRE 1.
    This is test infrastructure (T-08-03-01 accepts direct Redis manipulation on db=15).
    """
    unique_id = _unique_session_id()
    redis_key = f"state:session:{unique_id}"

    child_env = {
        **os.environ,
        "CLAUDE_CODE_SESSION_ID": unique_id,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
    }

    # Step 1: Register via CLI
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "session", "register", "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    stdout, _stderr = proc.communicate(timeout=15)
    assert proc.returncode == 0, (
        f"session register exited {proc.returncode}; "
        f"stderr={_stderr!r}; stdout={stdout!r}"
    )

    # Step 2: Verify key exists, then force-expire to 1 second
    assert clean_db.exists(redis_key), (
        f"Redis key {redis_key!r} missing after register — "
        "cannot test TTL lapse without a live key"
    )
    clean_db.expire(redis_key, 1)

    # Step 3: Wait for the TTL to lapse
    time.sleep(2)

    # Confirm the key is gone from Redis (TTL fired)
    assert not clean_db.exists(redis_key), (
        f"Redis key {redis_key!r} still exists after forced TTL=1 + 2s sleep; "
        "Redis EXPIRE did not fire as expected"
    )

    # Step 4: Call session list via CLI — expired key must be absent
    # (HGETALL returns empty dict for expired key → session_list skips it)
    data = _session_list_via_cli(session_id=unique_id)
    matching = [
        e for e in data
        if e.get("session", {}).get("session_id") == unique_id
    ]
    assert not matching, (
        f"session_id {unique_id!r} still appears in session list after TTL lapse; "
        "session_list must skip sessions whose Redis keys have expired (D2 backstop). "
        "TEST-03 point 4 failed."
    )
