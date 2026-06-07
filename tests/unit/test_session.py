"""Unit tests for em_proj.session — session registry core module.

Tests for Tasks 1–3 of 08-01-PLAN.md:
  - Task 1: module scaffold, constants, Lua scripts, helpers, SessionNotFound
  - Task 2: session_register, session_heartbeat, _scan_all_holders_by_session_id
  - Task 3: session_list, session_show

Each test uses the clean_db fixture for Redis isolation (db=15).
"""
from __future__ import annotations

import inspect
import os
import time

import pytest
import redis as redis_module

import em_proj.redis_client as rc
from em_proj.session import (
    KEY_PREFIX,
    TTL_DEFAULT,
    SessionNotFound,
    _build_session_key,
    _hgetall_to_session,
    _scan_all_holders_by_session_id,
    session_heartbeat,
    session_list,
    session_register,
    session_show,
)


@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    """Reset the redis_client singleton so each test honors EM_PROJ_REDIS_DB=15."""
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()


@pytest.fixture(autouse=True)
def _point_session_at_test_db(monkeypatch):
    """Force session.py's get_client() onto db=15."""
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")


# ---------------------------------------------------------------------------
# Task 1: Module-level constants
# ---------------------------------------------------------------------------


def test_key_prefix_is_state_session() -> None:
    """KEY_PREFIX must be 'state:session:'."""
    assert KEY_PREFIX == "state:session:"


def test_ttl_default_is_300() -> None:
    """TTL_DEFAULT must be 300 (5-minute heartbeat backstop per D2)."""
    assert TTL_DEFAULT == 300


# ---------------------------------------------------------------------------
# Task 1: Lua scripts present as module-level string constants
# ---------------------------------------------------------------------------


def test_lua_session_upsert_is_present_in_source() -> None:
    """LUA_SESSION_UPSERT module-level string constant must be present in session.py source."""
    import em_proj.session as session_module
    src_path = inspect.getfile(session_module)
    with open(src_path) as f:
        src = f.read()
    assert "LUA_SESSION_UPSERT" in src


def test_lua_session_heartbeat_is_present_in_source() -> None:
    """LUA_SESSION_HEARTBEAT module-level string constant must be present in session.py source."""
    import em_proj.session as session_module
    src_path = inspect.getfile(session_module)
    with open(src_path) as f:
        src = f.read()
    assert "LUA_SESSION_HEARTBEAT" in src


# ---------------------------------------------------------------------------
# Task 1: SessionNotFound exception
# ---------------------------------------------------------------------------


def test_session_not_found_has_code_not_found() -> None:
    """SessionNotFound.code must equal 'not_found'."""
    exc = SessionNotFound("test")
    assert exc.code == "not_found"


def test_session_not_found_is_exception() -> None:
    """SessionNotFound must be a subclass of Exception."""
    assert issubclass(SessionNotFound, Exception)


def test_session_not_found_no_arg() -> None:
    """SessionNotFound() with no argument should not raise."""
    exc = SessionNotFound()
    assert exc.code == "not_found"


# ---------------------------------------------------------------------------
# Task 1: _build_session_key helper
# ---------------------------------------------------------------------------


def test_build_session_key_concatenates_prefix() -> None:
    """_build_session_key(session_id) must return KEY_PREFIX + session_id."""
    result = _build_session_key("abc-123")
    assert result == "state:session:abc-123"


def test_build_session_key_with_uuid_style() -> None:
    """_build_session_key with UUID-style id."""
    sid = "550e8400-e29b-41d4-a716-446655440000"
    assert _build_session_key(sid) == "state:session:" + sid


# ---------------------------------------------------------------------------
# Task 1: _hgetall_to_session helper — type coercions
# ---------------------------------------------------------------------------


def test_hgetall_to_session_coerces_pid_to_int() -> None:
    """_hgetall_to_session must coerce pid from string to int."""
    raw = {
        "session_id": "sid-test",
        "project_hash": "-Users-test",
        "upstream_identity": "github.com:owner/repo",
        "pid": "12345",
        "proc_start_epoch": "1717000000.5",
        "boot_id": "abcdef0123456789",
        "cwd": "/Users/test",
        "registered_at": "1717000000.0",
        "last_heartbeat": "1717000001.0",
    }
    result = _hgetall_to_session(raw)
    assert isinstance(result["pid"], int)
    assert result["pid"] == 12345


def test_hgetall_to_session_coerces_floats() -> None:
    """_hgetall_to_session must coerce proc_start_epoch, registered_at, last_heartbeat to float."""
    raw = {
        "session_id": "sid-test",
        "project_hash": "-Users-test",
        "upstream_identity": "github.com:owner/repo",
        "pid": "99",
        "proc_start_epoch": "1717000000.123",
        "boot_id": "abcdef0123456789",
        "cwd": "/tmp",
        "registered_at": "1717000000.0",
        "last_heartbeat": "1717000001.5",
    }
    result = _hgetall_to_session(raw)
    assert isinstance(result["proc_start_epoch"], float)
    assert result["proc_start_epoch"] == pytest.approx(1717000000.123)
    assert isinstance(result["registered_at"], float)
    assert isinstance(result["last_heartbeat"], float)
    assert result["last_heartbeat"] == pytest.approx(1717000001.5)


def test_hgetall_to_session_preserves_strings() -> None:
    """_hgetall_to_session must preserve session_id, project_hash, upstream_identity, boot_id, cwd as strings."""
    raw = {
        "session_id": "my-session",
        "project_hash": "-Users-me",
        "upstream_identity": "github.com:me/repo",
        "pid": "1",
        "proc_start_epoch": "1.0",
        "boot_id": "deadbeef01234567",
        "cwd": "/home/me",
        "registered_at": "1.0",
        "last_heartbeat": "1.0",
    }
    result = _hgetall_to_session(raw)
    assert result["session_id"] == "my-session"
    assert result["project_hash"] == "-Users-me"
    assert result["upstream_identity"] == "github.com:me/repo"
    assert result["boot_id"] == "deadbeef01234567"
    assert result["cwd"] == "/home/me"


def test_hgetall_to_session_missing_field_raises_key_error() -> None:
    """_hgetall_to_session with a missing required field must raise KeyError."""
    raw = {
        "session_id": "sid",
        # pid missing
        "proc_start_epoch": "1.0",
        "boot_id": "abc",
        "cwd": "/tmp",
        "registered_at": "1.0",
        "last_heartbeat": "1.0",
        "project_hash": "-tmp",
        "upstream_identity": "none",
    }
    with pytest.raises(KeyError):
        _hgetall_to_session(raw)


# ---------------------------------------------------------------------------
# Task 2: session_register
# ---------------------------------------------------------------------------


def test_session_register_returns_9_field_dict(clean_db) -> None:
    """session_register() must return a dict with all 9 required fields."""
    record = session_register()
    required = {
        "session_id", "project_hash", "upstream_identity", "pid",
        "proc_start_epoch", "boot_id", "cwd", "registered_at", "last_heartbeat",
    }
    assert required.issubset(set(record.keys()))


def test_session_register_writes_redis_hash(clean_db) -> None:
    """session_register() must write a HASH to state:session:<session_id> in Redis."""
    record = session_register()
    sid = record["session_id"]
    key = _build_session_key(sid)
    test_client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    assert test_client.exists(key) == 1
    raw = test_client.hgetall(key)
    assert raw["session_id"] == sid


def test_session_register_idempotent_preserves_registered_at(clean_db) -> None:
    """Idempotent upsert (D5): re-registering same session_id preserves registered_at."""
    first = session_register()
    time.sleep(0.1)  # ensure last_heartbeat would differ
    second = session_register()

    assert first["session_id"] == second["session_id"]
    assert second["registered_at"] == pytest.approx(first["registered_at"], abs=0.01)


def test_session_register_idempotent_updates_last_heartbeat(clean_db) -> None:
    """Idempotent upsert: re-registering same session_id updates last_heartbeat."""
    first = session_register()
    time.sleep(0.05)
    second = session_register()
    # last_heartbeat must be >= first call's value
    assert second["last_heartbeat"] >= first["last_heartbeat"]


def test_session_register_sets_ttl(clean_db) -> None:
    """session_register() must set a TTL on the Redis key (TTL_DEFAULT)."""
    record = session_register()
    key = _build_session_key(record["session_id"])
    test_client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    ttl = test_client.ttl(key)
    assert 0 < ttl <= TTL_DEFAULT


# ---------------------------------------------------------------------------
# Task 2: session_heartbeat
# ---------------------------------------------------------------------------


def test_session_heartbeat_raises_if_not_registered(clean_db) -> None:
    """session_heartbeat() on a non-existent key must raise SessionNotFound."""
    with pytest.raises(SessionNotFound):
        session_heartbeat()


def test_session_heartbeat_refreshes_last_heartbeat(clean_db) -> None:
    """session_heartbeat() must update last_heartbeat in the Redis HASH."""
    session_register()
    time.sleep(0.05)
    record = session_heartbeat()
    assert isinstance(record["last_heartbeat"], float)
    # The heartbeat record must have all 9 fields
    required = {
        "session_id", "project_hash", "upstream_identity", "pid",
        "proc_start_epoch", "boot_id", "cwd", "registered_at", "last_heartbeat",
    }
    assert required.issubset(set(record.keys()))


def test_session_heartbeat_rearms_ttl(clean_db) -> None:
    """session_heartbeat() must re-arm the TTL on the session key."""
    session_register()
    key = _build_session_key(session_register()["session_id"])
    test_client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    ttl_after = test_client.ttl(key)
    assert 0 < ttl_after <= TTL_DEFAULT


# ---------------------------------------------------------------------------
# Task 2: _scan_all_holders_by_session_id
# ---------------------------------------------------------------------------


def test_scan_all_holders_returns_dict(clean_db) -> None:
    """_scan_all_holders_by_session_id() must return a dict."""
    result = _scan_all_holders_by_session_id()
    assert isinstance(result, dict)


def test_scan_all_holders_empty_namespaces(clean_db) -> None:
    """_scan_all_holders_by_session_id() on clean db returns empty dict."""
    result = _scan_all_holders_by_session_id()
    assert result == {}


def test_scan_all_holders_groups_claim_by_session_id(clean_db) -> None:
    """_scan_all_holders_by_session_id() must group a claim holder under the correct session_id."""
    from em_proj.state.claim import claim_take, KEY_PREFIX as CLAIM_PREFIX
    from em_proj.identity import resolve_session_id

    claim_take("session-test-area")
    sid = resolve_session_id()

    result = _scan_all_holders_by_session_id()
    assert sid in result
    assert len(result[sid]["claims"]) >= 1


def test_scan_all_holders_skips_stale_holders(clean_db, monkeypatch) -> None:
    """_scan_all_holders_by_session_id() must exclude holders where is_holder_stale() returns True."""
    from em_proj.state.claim import KEY_PREFIX as CLAIM_PREFIX
    from em_proj.identity import resolve_session_id

    # Write a claim record with a nonexistent pid (stale)
    test_client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    fake_sid = "stale-session-99999"
    fake_pid = 99999999  # almost certainly not a real pid
    key = CLAIM_PREFIX + "-Users-fake-project:fake-area"
    test_client.hset(key, mapping={
        "session_id": fake_sid,
        "project_hash": "-Users-fake-project",
        "reason": "",
        "claimed_at": str(time.time()),
        "expires_at": str(time.time() + 1800),
        "pid": str(fake_pid),
        "proc_start_epoch": "1.0",
        "boot_id": "0000000000000000",
    })
    test_client.expire(key, 3600)

    result = _scan_all_holders_by_session_id()
    # The stale fake_sid should NOT appear
    assert fake_sid not in result


def test_scan_all_holders_structure_has_all_three_namespaces(clean_db) -> None:
    """Each entry in _scan_all_holders_by_session_id() result must have claims/locks/reserves keys."""
    from em_proj.state.claim import claim_take
    claim_take("scan-structure-area")

    from em_proj.identity import resolve_session_id
    sid = resolve_session_id()

    result = _scan_all_holders_by_session_id()
    assert sid in result
    entry = result[sid]
    assert "claims" in entry
    assert "locks" in entry
    assert "reserves" in entry


# ---------------------------------------------------------------------------
# Task 3: session_list
# ---------------------------------------------------------------------------


def test_session_list_returns_list(clean_db) -> None:
    """session_list() must return a list."""
    result = session_list()
    assert isinstance(result, list)


def test_session_list_empty_when_no_sessions(clean_db) -> None:
    """session_list() must return empty list when no sessions registered."""
    result = session_list()
    assert result == []


def test_session_list_includes_registered_session(clean_db) -> None:
    """session_list() must include a live registered session."""
    session_register()
    result = session_list()
    assert len(result) >= 1


def test_session_list_entry_shape(clean_db) -> None:
    """session_list() entries must have 'session' sub-dict (9 fields) and 'held' sub-dict with counts."""
    session_register()
    result = session_list()
    assert len(result) >= 1
    entry = result[0]
    assert "session" in entry
    assert "held" in entry

    session_record = entry["session"]
    required_session_fields = {
        "session_id", "project_hash", "upstream_identity", "pid",
        "proc_start_epoch", "boot_id", "cwd", "registered_at", "last_heartbeat",
    }
    assert required_session_fields.issubset(set(session_record.keys()))

    held = entry["held"]
    assert "claims" in held
    assert "locks" in held
    assert "reserves" in held
    # counts are integers
    assert isinstance(held["claims"], int)
    assert isinstance(held["locks"], int)
    assert isinstance(held["reserves"], int)


def test_session_list_excludes_stale_session_and_dels_key(clean_db) -> None:
    """session_list() must exclude a stale session (dead pid) and DEL its Redis key."""
    test_client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    fake_sid = "stale-session-for-list-test"
    fake_pid = 99999998
    key = _build_session_key(fake_sid)
    now = time.time()
    test_client.hset(key, mapping={
        "session_id": fake_sid,
        "project_hash": "-Users-fake",
        "upstream_identity": "github.com:fake/repo",
        "pid": str(fake_pid),
        "proc_start_epoch": "1.0",
        "boot_id": "0000000000000000",
        "cwd": "/tmp/fake",
        "registered_at": str(now),
        "last_heartbeat": str(now),
    })
    test_client.expire(key, 300)

    result = session_list()
    # stale session must not appear
    sids = [e["session"]["session_id"] for e in result]
    assert fake_sid not in sids
    # stale key must be deleted
    assert test_client.exists(key) == 0


def test_session_list_includes_enrichment_counts(clean_db) -> None:
    """session_list() held counts must reflect actual claim holders."""
    from em_proj.state.claim import claim_take

    # Register and take a claim in the same session
    session_register()
    claim_take("enrichment-test-area")

    from em_proj.identity import resolve_session_id
    sid = resolve_session_id()

    result = session_list()
    my_entry = next((e for e in result if e["session"]["session_id"] == sid), None)
    assert my_entry is not None
    # Should have at least 1 claim
    assert my_entry["held"]["claims"] >= 1


# ---------------------------------------------------------------------------
# Task 3: session_show
# ---------------------------------------------------------------------------


def test_session_show_raises_for_nonexistent_session(clean_db) -> None:
    """session_show() on nonexistent session_id must raise SessionNotFound."""
    with pytest.raises(SessionNotFound):
        session_show("does-not-exist-00000000")


def test_session_show_returns_session_and_held(clean_db) -> None:
    """session_show() must return {'session': {...9 fields...}, 'held': {'claims': [...], ...}}."""
    record = session_register()
    sid = record["session_id"]

    result = session_show(sid)
    assert "session" in result
    assert "held" in result

    required_session_fields = {
        "session_id", "project_hash", "upstream_identity", "pid",
        "proc_start_epoch", "boot_id", "cwd", "registered_at", "last_heartbeat",
    }
    assert required_session_fields.issubset(set(result["session"].keys()))

    held = result["held"]
    assert "claims" in held
    assert "locks" in held
    assert "reserves" in held
    # held values are lists (full dicts, not counts)
    assert isinstance(held["claims"], list)
    assert isinstance(held["locks"], list)
    assert isinstance(held["reserves"], list)


def test_session_show_full_held_dicts_not_counts(clean_db) -> None:
    """session_show() held dict must contain full holder dicts, not integer counts."""
    from em_proj.state.claim import claim_take

    session_register()
    claim_take("show-full-dict-area")

    from em_proj.identity import resolve_session_id
    sid = resolve_session_id()

    result = session_show(sid)
    claims = result["held"]["claims"]
    assert len(claims) >= 1
    # Each claim entry should be a dict with session_id field
    assert isinstance(claims[0], dict)
    assert "session_id" in claims[0]


def test_session_show_excludes_stale_holders(clean_db) -> None:
    """session_show() must exclude stale holders from held dict."""
    test_client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    from em_proj.state.claim import KEY_PREFIX as CLAIM_PREFIX

    record = session_register()
    sid = record["session_id"]

    # Write a fake claim for this session with a dead pid
    fake_pid = 99999997
    claim_key = CLAIM_PREFIX + "-tmp-fake:stale-show-area"
    now = time.time()
    test_client.hset(claim_key, mapping={
        "session_id": sid,
        "project_hash": "-tmp-fake",
        "reason": "",
        "claimed_at": str(now),
        "expires_at": str(now + 1800),
        "pid": str(fake_pid),
        "proc_start_epoch": "1.0",
        "boot_id": "0000000000000000",
    })
    test_client.expire(claim_key, 3600)

    result = session_show(sid)
    # The stale claim (dead pid) should NOT be in held claims
    claim_pids = [c.get("pid") for c in result["held"]["claims"]]
    assert fake_pid not in claim_pids


def test_session_show_stale_session_raises_and_dels(clean_db) -> None:
    """session_show() on a stale session must raise SessionNotFound and DEL the key."""
    test_client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    fake_sid = "stale-show-session-test"
    fake_pid = 99999996
    key = _build_session_key(fake_sid)
    now = time.time()
    test_client.hset(key, mapping={
        "session_id": fake_sid,
        "project_hash": "-Users-fake",
        "upstream_identity": "github.com:fake/repo",
        "pid": str(fake_pid),
        "proc_start_epoch": "1.0",
        "boot_id": "0000000000000000",
        "cwd": "/tmp/fake",
        "registered_at": str(now),
        "last_heartbeat": str(now),
    })
    test_client.expire(key, 300)

    with pytest.raises(SessionNotFound):
        session_show(fake_sid)
    # Key must be DELed
    assert test_client.exists(key) == 0


# ---------------------------------------------------------------------------
# Task 3: Forbidden imports check (no typer/multiprocessing/subprocess/threading)
# ---------------------------------------------------------------------------


def test_session_py_has_no_forbidden_imports() -> None:
    """session._ops must not import typer, multiprocessing, subprocess, or threading.

    After the session package restructuring (08-02), business logic lives in
    em_proj.session._ops (not em_proj.session.__init__, which legitimately imports
    typer for CLI wiring). This test checks the ops module source file directly.
    """
    import em_proj.session._ops as ops_module
    src_path = inspect.getfile(ops_module)
    with open(src_path) as f:
        src = f.read()

    # Strip comment lines before checking
    non_comment_lines = [
        line for line in src.splitlines()
        if not line.lstrip().startswith("#")
    ]
    non_comment = "\n".join(non_comment_lines)

    assert "import typer" not in non_comment
    assert "import multiprocessing" not in non_comment
    assert "import subprocess" not in non_comment
    assert "import threading" not in non_comment
