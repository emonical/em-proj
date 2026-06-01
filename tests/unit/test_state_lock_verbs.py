"""CliRunner tests for the lock + unlock state verbs.

Covers D-07 (--warn TTY-gated), D-08 (--warn+--hold mutually exclusive),
D-09 (non-holder learns via held_by_another), LOCK-01 (acquire+release),
LOCK-02 (1s-block + --warn flow), and the --hold stub structured error.

Test design notes
-----------------
- Uses the same CliRunner pattern as test_state_verbs.py: try/except TypeError
  for click >= 8.2's removal of mix_stderr kwarg.
- Every Redis-touching test uses ``clean_db`` for per-test isolation.
- Redis singleton is reset per test so EM_PROJ_REDIS_DB=15 drives get_client()
  onto the test DB.
- "Pre-set holder" tests build the holder dict inline (no private imports from
  lock.py) via current_process_composite() + acquired_at/expires_at, then
  encode with json.dumps(holder, separators=(",",":"), sort_keys=True).
- Lock key is "state:lock:" + name — the literal prefix is fine in test code.
- For --warn TTY tests: monkeypatch both sys.stdout.isatty and sys.stdin.isatty
  (T-3-XX-05 dual-isatty requirement).
- Test 12 (--hold stub) asserts structured envelope + no traceback.
"""
from __future__ import annotations

import json
import os
import time

import pytest
from typer.testing import CliRunner

import em_proj.redis_client as rc
from em_proj.cli import app
from em_proj.identity import current_process_composite
from em_proj.state.lock import DEFAULT_TTL

# Click 8.2+ / typer 0.13+ separate stdout & stderr by default and removed the
# `mix_stderr` kwarg; older versions still accept it. Try the explicit form first
# (keeps the intent documented + satisfies the plan's grep gate); fall back to
# plain CliRunner on newer click where the kwarg was dropped.
try:
    runner = CliRunner(mix_stderr=False)  # click < 8.2
except TypeError:
    runner = CliRunner()  # click >= 8.2 — default-separated


@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    """Reset the redis_client singleton so each test honors EM_PROJ_REDIS_DB=15."""
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()


@pytest.fixture(autouse=True)
def _point_at_test_db(monkeypatch):
    """Force get_client() onto db=15 so tests use the clean_db namespace."""
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")


def _make_live_holder(name: str, *, pid: int | None = None) -> str:
    """Build an encoded live holder string suitable for setting in Redis.

    Uses current_process_composite() for the identity fields, then overrides
    pid if requested (e.g. pid=99999 for a non-holder / dead-process simulation).
    Encodes with json.dumps(…, separators=(",",":"), sort_keys=True) — the same
    compact, byte-stable format lock.py uses internally.
    """
    composite = current_process_composite()
    if pid is not None:
        composite["pid"] = pid
    now = time.time()
    holder = {
        **composite,
        "reason": None,
        "acquired_at": now,
        "expires_at": now + DEFAULT_TTL,
    }
    return json.dumps(holder, separators=(",", ":"), sort_keys=True)


LOCK_KEY_PREFIX = "state:lock:"


# ---------------------------------------------------------------------------
# Test 1 — lock <name> happy path
# ---------------------------------------------------------------------------


def test_lock_happy_path_acquires_and_emits_ok(clean_db):
    """`state lock foo --json` exits 0 with the OK envelope; data carries expected fields."""
    result = runner.invoke(app, ["state", "lock", "foo", "--json"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stdout)
    assert parsed["schema_version"] == "1"
    assert parsed["status"] == "ok"
    assert parsed["data"]["name"] == "foo"
    assert parsed["data"]["ttl"] == DEFAULT_TTL


# ---------------------------------------------------------------------------
# Test 2 — lock <name> on a live-held lock exits 3
# ---------------------------------------------------------------------------


def test_lock_held_by_live_exits_3(clean_db):
    """`state lock foo` when a live holder owns it exits 3 with held_by_another envelope."""
    encoded = _make_live_holder("foo")
    clean_db.set(LOCK_KEY_PREFIX + "foo", encoded, ex=DEFAULT_TTL)

    result = runner.invoke(app, ["state", "lock", "foo", "--json"])
    assert result.exit_code == 3, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stderr)
    assert parsed["status"] == "held_by_another"
    assert parsed["error"]["code"] == "held_by_another"


# ---------------------------------------------------------------------------
# Test 3 — lock --ttl 30 foo respects TTL override
# ---------------------------------------------------------------------------


def test_lock_with_ttl_override(clean_db):
    """`state lock --ttl 30 foo` exits 0; envelope data.ttl == 30; Redis TTL ~= 30."""
    result = runner.invoke(app, ["state", "lock", "--ttl", "30", "foo", "--json"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stdout)
    assert parsed["data"]["ttl"] == 30
    redis_ttl = clean_db.ttl(LOCK_KEY_PREFIX + "foo")
    assert 1 <= redis_ttl <= 30, f"expected Redis TTL ≤ 30, got {redis_ttl}"


# ---------------------------------------------------------------------------
# Test 4 — lock --reason persists to holder
# ---------------------------------------------------------------------------


def test_lock_with_reason_persists(clean_db):
    """`state lock --reason 'background sync' foo` persists the reason in the holder JSON."""
    result = runner.invoke(
        app, ["state", "lock", "--reason", "background sync", "foo", "--json"]
    )
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    raw = clean_db.get(LOCK_KEY_PREFIX + "foo")
    assert raw is not None
    holder = json.loads(raw)
    assert holder["reason"] == "background sync"


# ---------------------------------------------------------------------------
# Test 5 — lock --reason too long exits 1 with validation_error
# ---------------------------------------------------------------------------


def test_lock_reason_too_long_exits_1(clean_db):
    """`state lock --reason <257-char string> foo` exits 1 with validation_error."""
    long_reason = "x" * 257
    result = runner.invoke(
        app, ["state", "lock", "--reason", long_reason, "foo", "--json"]
    )
    assert result.exit_code == 1, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stderr)
    assert parsed["status"] == "error"
    assert parsed["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# Test 6 — lock with colon in name exits 1 with validation_error
# ---------------------------------------------------------------------------


def test_lock_colon_in_name_exits_1(clean_db):
    """`state lock 'foo:bar'` exits 1 with validation_error (Phase 2 D-09 carry)."""
    result = runner.invoke(app, ["state", "lock", "foo:bar", "--json"])
    assert result.exit_code == 1, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stderr)
    assert parsed["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# Test 7 — lock --ttl 0 exits 1 or 2 with validation_error (TTL bounds)
# ---------------------------------------------------------------------------


def test_lock_ttl_zero_rejected(clean_db):
    """`state lock --ttl 0 foo` exits 1 or 2 with validation_error (TTL bounds check).

    typer's min= constraint may reject it at parse time (exit 2) or the verb
    body's _validate_ttl call raises ValidationError (exit 1). Both are correct;
    the test accepts either.
    """
    result = runner.invoke(app, ["state", "lock", "--ttl", "0", "foo", "--json"])
    assert result.exit_code in (1, 2), f"exit_code={result.exit_code!r}"
    # Both paths produce a message containing "validation" somewhere
    stderr_text = result.stderr
    assert "validation" in stderr_text.lower() or result.exit_code == 2, (
        f"expected validation error but got: {stderr_text!r}"
    )


# ---------------------------------------------------------------------------
# Test 8 — lock --warn --hold mutex exits 1; no Redis write
# ---------------------------------------------------------------------------


def test_lock_warn_hold_mutex_exits_1_no_redis(clean_db):
    """`state lock --warn --hold foo` exits 1 with validation_error; no Redis key written."""
    result = runner.invoke(
        app, ["state", "lock", "--warn", "--hold", "foo", "--json"]
    )
    assert result.exit_code == 1, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stderr)
    assert parsed["error"]["code"] == "validation_error"
    assert "mutually exclusive" in parsed["error"]["message"]
    # No Redis write: mutex check fires before get_client()
    assert clean_db.get(LOCK_KEY_PREFIX + "foo") is None


# ---------------------------------------------------------------------------
# Test 9 — lock --warn on non-TTY exits 1 with warn_requires_tty
# ---------------------------------------------------------------------------


def test_lock_warn_non_tty_exits_1_warn_requires_tty(clean_db):
    """`state lock --warn foo` on non-TTY (CliRunner default) exits 1 with warn_requires_tty.

    The --warn flow on a non-TTY caller must refuse with exit 1 (D-07 refusal),
    NOT prompt, NOT exit 3 with held_by_another.
    """
    encoded = _make_live_holder("foo")
    clean_db.set(LOCK_KEY_PREFIX + "foo", encoded, ex=DEFAULT_TTL)

    result = runner.invoke(app, ["state", "lock", "--warn", "foo", "--json"])
    assert result.exit_code == 1, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stderr)
    assert parsed["error"]["code"] == "warn_requires_tty"


# ---------------------------------------------------------------------------
# TTY-simulation helpers for --warn tests (Tests 10 and 11)
# ---------------------------------------------------------------------------


def _tty_sys_mock():
    """Build a mock sys module for use with monkeypatch.setattr(state_mod, 'sys', ...).

    The challenge: CliRunner replaces sys.stdin and sys.stdout with BytesIO
    buffers during isolation, but the replacement happens on the real `sys`
    module — not on our custom module. To bridge this:
    - stdout.isatty() returns True (simulating a TTY so --warn proceeds)
    - stdin.isatty() returns True (second half of D-07 dual-isatty check)
    - stdin.readline() delegates dynamically to ``import sys; sys.stdin.readline()``
      so it reads from CliRunner's input BytesIO (which CliRunner patches on the
      real sys.stdin during invoke, AFTER our mock is in place).
    - All other attributes delegate to the real sys module.
    """
    import sys as real_sys
    import types

    class _StdoutTtyMock:
        def isatty(self):
            return True

        def __getattr__(self, name):
            return getattr(real_sys.stdout, name)

        def write(self, s):
            # Delegate to real sys.stdout so CliRunner capture works
            return real_sys.stdout.write(s)

        def flush(self):
            return real_sys.stdout.flush()

    class _StdinTtyMock:
        def isatty(self):
            return True

        def readline(self):
            # Always read from the current real sys.stdin — this is the
            # CliRunner-patched BytesIO when called inside invoke().
            return real_sys.stdin.readline()

        def __getattr__(self, name):
            return getattr(real_sys.stdin, name)

    mock_sys = types.ModuleType("sys")
    mock_sys.__dict__.update(real_sys.__dict__)
    mock_sys.stdout = _StdoutTtyMock()
    mock_sys.stdin = _StdinTtyMock()
    # Keep stderr as-is so CliRunner can capture it
    mock_sys.stderr = real_sys.stderr
    return mock_sys


# ---------------------------------------------------------------------------
# Test 10 — lock --warn on simulated TTY with "y" → displacement succeeds
# ---------------------------------------------------------------------------


def test_lock_warn_tty_yes_displaces_holder(clean_db, monkeypatch):
    """`state lock --warn foo` on simulated TTY with 'y' → exits 0, our holder in Redis.

    Uses the current process composite as the pre-set holder (guarantees the PID
    is alive so stale-detection won't auto-takeover the lock before --warn fires).
    """
    import em_proj.state as state_mod

    monkeypatch.setattr(state_mod, "sys", _tty_sys_mock())

    # Use current process composite — PID is alive → not stale → block-poll → HeldByAnother
    encoded = _make_live_holder("foo")
    clean_db.set(LOCK_KEY_PREFIX + "foo", encoded, ex=DEFAULT_TTL)

    result = runner.invoke(
        app, ["state", "lock", "--warn", "foo", "--json"], input="y\n"
    )
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    # After displacement, Redis holds OUR holder (current pid)
    raw = clean_db.get(LOCK_KEY_PREFIX + "foo")
    assert raw is not None
    new_holder = json.loads(raw)
    assert new_holder["pid"] == os.getpid()


# ---------------------------------------------------------------------------
# Test 11 — lock --warn on simulated TTY with "n" → exit 3, original holder unchanged
# ---------------------------------------------------------------------------


def test_lock_warn_tty_no_leaves_original_holder(clean_db, monkeypatch):
    """`state lock --warn foo` on simulated TTY with 'n' → exits 3, original value unchanged.

    Uses the current process composite (live PID) so stale detection won't
    auto-acquire before --warn fires.
    """
    import em_proj.state as state_mod

    monkeypatch.setattr(state_mod, "sys", _tty_sys_mock())

    # Use current process composite — PID is alive → not stale → block-poll → HeldByAnother
    encoded = _make_live_holder("foo")
    clean_db.set(LOCK_KEY_PREFIX + "foo", encoded, ex=DEFAULT_TTL)

    result = runner.invoke(
        app, ["state", "lock", "--warn", "foo", "--json"], input="n\n"
    )
    assert result.exit_code == 3, f"stderr={result.stderr!r}"
    # Original holder still in Redis (we did not displace it)
    assert clean_db.get(LOCK_KEY_PREFIX + "foo") == encoded


# ---------------------------------------------------------------------------
# Test 12 — lock --hold with real dispatch exits 0; wrapped exit code propagates
# ---------------------------------------------------------------------------


def test_lock_hold_real_dispatch_exits_0(clean_db):
    """`state lock --hold foo -- echo hi` exits 0; lock released; real dispatch (Plan 03-05).

    UPDATED from Plan 03-04 stub test: the stub (emit_error not_implemented) has been
    replaced with lock_hold_run dispatch. The wrapped 'echo hi' exits 0 so the verb
    exits 0.

    Note: CliRunner captures subprocess stdout through the inherited file descriptor.
    We verify exit code and that the lock key is absent after execution.
    """
    result = runner.invoke(
        app, ["state", "lock", "--hold", "--json", "foo", "--", "echo", "hi"]
    )
    assert result.exit_code == 0, (
        f"Expected exit 0 from --hold echo hi, got {result.exit_code}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    # Lock must be released after subprocess exits
    assert clean_db.get("state:lock:foo") is None


def test_lock_hold_non_zero_exit_propagates(clean_db):
    """`state lock --hold foo -- false` exits 1; lock released; exit code propagates.

    Verifies: the wrapped subprocess's non-zero exit code passes through as the
    verb's exit code.
    """
    result = runner.invoke(
        app, ["state", "lock", "--hold", "--json", "foo", "--", "false"]
    )
    assert result.exit_code == 1, (
        f"Expected exit 1 from --hold false, got {result.exit_code}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    # Lock still released even on non-zero wrapped exit
    assert clean_db.get("state:lock:foo") is None


def test_lock_hold_empty_cmd_exits_1_validation_error(clean_db):
    """`state lock --hold foo` (no cmd after --) exits 1 with validation_error.

    Verifies: the empty-cmd check fires before lock acquisition.
    """
    result = runner.invoke(app, ["state", "lock", "--hold", "--json", "foo"])
    assert result.exit_code == 1, f"stderr={result.stderr!r}"
    envelope = json.loads(result.stderr)
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "validation_error"
    assert "not_implemented" not in result.stderr


# ---------------------------------------------------------------------------
# Test 13 — unlock happy path releases the lock
# ---------------------------------------------------------------------------


def test_unlock_happy_path_releases(clean_db):
    """`state unlock foo` after acquiring exits 0; key absent from Redis."""
    # Acquire first
    acquire_result = runner.invoke(app, ["state", "lock", "foo", "--json"])
    assert acquire_result.exit_code == 0, f"acquire stderr={acquire_result.stderr!r}"

    result = runner.invoke(app, ["state", "unlock", "foo", "--json"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "ok"
    assert parsed["data"] == {"name": "foo", "released": True}
    assert clean_db.get(LOCK_KEY_PREFIX + "foo") is None


# ---------------------------------------------------------------------------
# Test 14 — unlock on non-holder exits 3 with held_by_another
# ---------------------------------------------------------------------------


def test_unlock_non_holder_exits_3(clean_db):
    """`state unlock foo` when a different process holds exits 3 with held_by_another."""
    # Pre-set a holder owned by pid=99999 (a different process)
    encoded = _make_live_holder("foo", pid=99999)
    clean_db.set(LOCK_KEY_PREFIX + "foo", encoded, ex=DEFAULT_TTL)

    result = runner.invoke(app, ["state", "unlock", "foo", "--json"])
    assert result.exit_code == 3, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stderr)
    assert parsed["status"] == "held_by_another"
    # Lock value untouched
    assert clean_db.get(LOCK_KEY_PREFIX + "foo") == encoded


# ---------------------------------------------------------------------------
# Test 15 — unlock with colon in name exits 1 with validation_error
# ---------------------------------------------------------------------------


def test_unlock_colon_in_name_exits_1(clean_db):
    """`state unlock 'foo:bar'` exits 1 with validation_error."""
    result = runner.invoke(app, ["state", "unlock", "foo:bar", "--json"])
    assert result.exit_code == 1, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stderr)
    assert parsed["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# Test 16 — lock --help shows expected flags
# ---------------------------------------------------------------------------


def test_lock_help(clean_db):
    """`state lock --help` exits 0; help text documents all expected flags."""
    result = runner.invoke(app, ["state", "lock", "--help"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    for flag in ("--warn", "--hold", "--ttl", "--reason", "--json"):
        assert flag in result.stdout, f"missing {flag!r} in lock --help"


# ---------------------------------------------------------------------------
# Test 17 — unlock --help shows --json
# ---------------------------------------------------------------------------


def test_unlock_help(clean_db):
    """`state unlock --help` exits 0; help text includes --json."""
    result = runner.invoke(app, ["state", "unlock", "--help"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    assert "--json" in result.stdout


# ---------------------------------------------------------------------------
# Test 18 — schema_version = "1" on lock + unlock OK envelopes (D-02 carry)
# ---------------------------------------------------------------------------


def test_lock_envelope_schema_version(clean_db):
    """`state lock foo --json` emits schema_version == '1' in the OK envelope (D-02 carry)."""
    result = runner.invoke(app, ["state", "lock", "foo", "--json"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stdout)
    assert parsed["schema_version"] == "1"


def test_unlock_envelope_schema_version(clean_db):
    """`state unlock foo --json` after acquire emits schema_version == '1' (D-02 carry)."""
    # Acquire first
    acquire_result = runner.invoke(app, ["state", "lock", "foo", "--json"])
    assert acquire_result.exit_code == 0, f"acquire stderr={acquire_result.stderr!r}"

    result = runner.invoke(app, ["state", "unlock", "foo", "--json"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stdout)
    assert parsed["schema_version"] == "1"
