"""CliRunner tests for the reserve, reserve-list, and check --upstream state verbs.

Covers RESERVE-02 (verb-level take), RESERVE-03 (reserve-list), RESERVE-04
(--category + --upstream filter), RESERVE-05 (TTY prompt + non-TTY exit 1),
the anonymous-claim gate (CLAIM-03 carry), and Q-H finding validation.

Test design notes
-----------------
- Uses the same CliRunner pattern as test_claim_verbs.py: try/except TypeError
  for click >= 8.2's removal of mix_stderr kwarg.
- Every Redis-touching test uses ``clean_db`` for per-test isolation.
- Redis singleton is reset per test so EM_PROJ_REDIS_DB=15 drives get_client()
  onto the test DB.
- Q-H validation lives in test_reserve_phase_6_claim_set_but_name_unknown_still_prompts.
  If a future Phase 7.x stores the workstream name in the claim holder, that test
  SHOULD start failing (the workstream value would no longer come from the prompt) —
  that failure is the signal to re-litigate the design, not to skip the test.

Pitfall #4 mitigation (alias):
  The verb uses ``claim_check as workstream_check`` in the import block to prevent
  accidental shadowing between the workstream-presence check and reserve_check.
  These tests exercise the workstream_check path via the TTY-prompt tests.

Pitfall #5 mitigation (subprocess non-TTY):
  Tests that exercise the non-TTY path monkeypatch sys.stdin.isatty → False so
  CliRunner's stdin-as-pipe behavior is simulated correctly. The workstream flag
  (--workstream <name>) bypasses the TTY-prompt path in multi-process tests.

References: RESERVE-02..05, Pitfall #4, Pitfall #5, Q-H finding (07-01-SUMMARY).
"""
from __future__ import annotations

import json
import os

import pytest
from typer.testing import CliRunner

import em_proj.redis_client as rc
from em_proj.cli import app

# Click 8.2+ / typer 0.13+ separate stdout & stderr by default and removed the
# `mix_stderr` kwarg; older versions still accept it. Try the explicit form first;
# fall back to plain CliRunner on newer click.
try:
    runner = CliRunner(mix_stderr=False)  # click < 8.2
except TypeError:
    runner = CliRunner()  # click >= 8.2 -- default-separated


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


@pytest.fixture(autouse=True)
def _set_session_id(monkeypatch):
    """Set a deterministic CLAUDE_CODE_SESSION_ID for all reserve verb tests."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "test-session-abc123")


# ---------------------------------------------------------------------------
# Test 1: reserve → exits 0, JSON output has area + workstream + upstream_identity
# ---------------------------------------------------------------------------

def test_reserve_exits_0_with_json_output(clean_db, monkeypatch):
    """em-proj state reserve <area> --workstream test-ws --json exits 0 with expected fields."""
    # Monkeypatch resolve_upstream_identity so the verb uses a deterministic upstream
    monkeypatch.setattr(
        "em_proj.state.resolve_upstream_identity",
        lambda *a, **kw: "github.com:test-org/test-repo",
    )
    result = runner.invoke(
        app, ["state", "reserve", "--workstream", "test-ws", "--json", "migrations.v200"]
    )
    assert result.exit_code == 0, (
        f"Expected exit 0; got {result.exit_code}\n"
        f"stdout={result.stdout}\nstderr={getattr(result, 'stderr', '')}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["status"] == "ok"
    assert payload["data"]["area"] == "migrations.v200"
    assert payload["data"]["workstream"] == "test-ws"
    assert payload["data"]["upstream_identity"] == "github.com:test-org/test-repo"
    assert "expires_at" in payload["data"]
    assert "claimed_at" in payload["data"]


# ---------------------------------------------------------------------------
# Test 2: reserve with no CLAUDE_CODE_SESSION_ID → exit 1, anonymous refused
# ---------------------------------------------------------------------------

def test_reserve_anonymous_refusal_exit_1(clean_db, monkeypatch):
    """CLAIM-03 carry: reserve with no session_id → exit 1, 'anonymous reservations refused'."""
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    result = runner.invoke(
        app, ["state", "reserve", "--workstream", "test-ws", "--json", "migrations.v200"]
    )
    assert result.exit_code == 1, f"Expected exit 1; got {result.exit_code}"
    output_text = result.output
    stderr_text = getattr(result, "stderr", "") or ""
    combined = output_text + stderr_text
    assert "anonymous reservations refused" in combined, (
        f"Expected 'anonymous reservations refused' in output; "
        f"got:\nstdout={result.output}\nstderr={stderr_text}"
    )


# ---------------------------------------------------------------------------
# Test 3: --workstream flag bypasses workstream resolution
# ---------------------------------------------------------------------------

def test_reserve_workstream_flag_bypasses_resolution(clean_db, monkeypatch):
    """--workstream <name> succeeds even with no workstream.active claim."""
    monkeypatch.setattr(
        "em_proj.state.resolve_upstream_identity",
        lambda *a, **kw: "github.com:test-org/test-repo",
    )
    # No workstream.active claim is set — the verb should still succeed via --workstream
    result = runner.invoke(
        app, ["state", "reserve", "--workstream", "explicit-ws", "--json", "area.v1"]
    )
    assert result.exit_code == 0, (
        f"Expected exit 0 with explicit --workstream; got {result.exit_code}\n"
        f"stdout={result.stdout}\nstderr={getattr(result, 'stderr', '')}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["data"]["workstream"] == "explicit-ws"


# ---------------------------------------------------------------------------
# Test 4: TTY prompt path — stdin.isatty + stdout.isatty both True → prompts
# ---------------------------------------------------------------------------

def test_reserve_tty_prompt_path(clean_db, monkeypatch):
    """When TTY present and no --workstream, the verb prompts and reads from stdin.

    Uses the _tty_sys_mock() pattern from test_state_lock_verbs.py: monkeypatch
    em_proj.state.sys with a mock module whose stdin.isatty() returns True and
    whose stdin.readline() delegates to the real sys.stdin (which CliRunner has
    wired to the `input=` argument). This is the correct way to simulate a TTY
    inside CliRunner -- plain monkeypatch of sys.stdin is overwritten by CliRunner.
    """
    import em_proj.state as state_mod

    monkeypatch.setattr(
        "em_proj.state.resolve_upstream_identity",
        lambda *a, **kw: "github.com:test-org/test-repo",
    )
    monkeypatch.setattr(state_mod, "sys", _tty_sys_mock())

    result = runner.invoke(
        app, ["state", "reserve", "--json", "migrations.prompt"],
        input="prompted-ws\n",
    )
    assert result.exit_code == 0, (
        f"Expected exit 0 via TTY prompt; got {result.exit_code}\n"
        f"stdout={result.stdout}\nstderr={getattr(result, 'stderr', '')}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["data"]["workstream"] == "prompted-ws"


# ---------------------------------------------------------------------------
# Test 5: non-TTY exits 1 when no --workstream and no claim
# ---------------------------------------------------------------------------

def test_reserve_nontty_exits_1_when_workstream_unset(clean_db, monkeypatch):
    """Non-TTY + no --workstream + no workstream.active claim → exit 1, workstream_unresolved.

    Default CliRunner invocation has stdin.isatty() → False (pipe mode), which
    already exercises the non-TTY exit-1 path without any monkeypatching of
    sys.stdin. We do NOT pass input= to CliRunner; the verb should bail before
    any readline() call.
    """
    monkeypatch.setattr(
        "em_proj.state.resolve_upstream_identity",
        lambda *a, **kw: "github.com:test-org/test-repo",
    )
    # No sys.stdin monkeypatching needed: CliRunner's default stdin is not a TTY.

    result = runner.invoke(
        app, ["state", "reserve", "--json", "migrations.nontty"]
    )
    assert result.exit_code == 1, f"Expected exit 1; got {result.exit_code}"
    output_text = result.output
    stderr_text = getattr(result, "stderr", "") or ""
    combined = output_text + stderr_text
    assert "workstream unresolved" in combined, (
        f"Expected 'workstream unresolved' in output; got:\n{combined}"
    )


# ---------------------------------------------------------------------------
# Test 6: Q-H validation — Phase 6 claim set but name unknown → still prompts
# ---------------------------------------------------------------------------

def test_reserve_phase_6_claim_set_but_name_unknown_still_prompts(clean_db, monkeypatch):
    """Q-H validation: even with workstream.active CLAIMED, the verb prompts.

    Phase 6 (gsd-sdk patched workstream.js) claims 'workstream.active' WITHOUT
    passing --reason, so the holder's reason field is ALWAYS None. The verb cannot
    extract the workstream name from the claim holder — it MUST fall through to
    the TTY prompt path. This test pins that behavior.

    If a future Phase 7.x stores the workstream name in the claim holder, this
    test SHOULD start failing (workstream would come from claim, not prompt) —
    that failure is the signal to re-litigate the design, not to skip the test.
    """
    from em_proj.state.claim import claim_take

    monkeypatch.setattr(
        "em_proj.state.resolve_upstream_identity",
        lambda *a, **kw: "github.com:test-org/test-repo",
    )

    # Simulate Phase 6 claiming workstream.active WITHOUT --reason (no name stored)
    claim_take("workstream.active", ttl=1800)  # no reason= arg, mirrors Phase 6's argv

    # Now monkeypatch TTY and invoke reserve WITHOUT --workstream.
    # Use _tty_sys_mock() + input= so CliRunner's stdin buffer delivers the answer.
    import em_proj.state as state_mod
    monkeypatch.setattr(state_mod, "sys", _tty_sys_mock())

    result = runner.invoke(
        app, ["state", "reserve", "--json", "migrations.q_h"],
        input="name-from-prompt\n",
    )
    assert result.exit_code == 0, (
        f"Expected exit 0 (name from prompt); got {result.exit_code}\n"
        f"stdout={result.stdout}\nstderr={getattr(result, 'stderr', '')}"
    )
    payload = json.loads(result.stdout.strip())
    # The workstream MUST come from the prompt, NOT from the claim holder
    assert payload["data"]["workstream"] == "name-from-prompt", (
        f"Expected workstream='name-from-prompt' (from TTY prompt); "
        f"got workstream={payload['data'].get('workstream')!r}. "
        "The Phase 6 claim presence-check must NOT short-circuit the prompt path."
    )


# ---------------------------------------------------------------------------
# Test 7: TTY prompt with empty input → exit 1, empty workstream name
# ---------------------------------------------------------------------------

def test_reserve_empty_tty_input_exits_1(clean_db, monkeypatch):
    """TTY prompt with empty input → exit 1, 'empty workstream name'."""
    import em_proj.state as state_mod

    monkeypatch.setattr(
        "em_proj.state.resolve_upstream_identity",
        lambda *a, **kw: "github.com:test-org/test-repo",
    )
    monkeypatch.setattr(state_mod, "sys", _tty_sys_mock())

    result = runner.invoke(
        app, ["state", "reserve", "--json", "migrations.empty_ws"],
        input="\n",
    )
    assert result.exit_code == 1, f"Expected exit 1; got {result.exit_code}"
    output_text = result.output
    stderr_text = getattr(result, "stderr", "") or ""
    combined = output_text + stderr_text
    assert "empty workstream name" in combined, (
        f"Expected 'empty workstream name' in output; got:\n{combined}"
    )


# ---------------------------------------------------------------------------
# Test 8: held-by-another exits 3, holder has winner's workstream
# ---------------------------------------------------------------------------

def test_reserve_held_by_another_exit_3(clean_db, monkeypatch):
    """Two sessions: session A takes reserve, session B gets exit 3 with holder.workstream from A."""
    from em_proj.state.reserve import reserve_take

    monkeypatch.setattr(
        "em_proj.state.resolve_upstream_identity",
        lambda *a, **kw: "github.com:test-org/test-repo",
    )

    # Session A takes the reservation directly (bypasses verb to isolate conflict path)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-A-reserve")
    rc._reset_for_tests()
    reserve_take(
        "migrations.v200",
        upstream_identity="github.com:test-org/test-repo",
        workstream="ws-from-A",
        ttl=1800,
    )

    # Session B tries to reserve via the verb
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-B-reserve")
    rc._reset_for_tests()

    result = runner.invoke(
        app,
        ["state", "reserve", "--workstream", "ws-from-B", "--json", "migrations.v200"],
    )
    assert result.exit_code == 3, (
        f"Expected exit 3 (held_by_another); got {result.exit_code}\n"
        f"stdout={result.stdout}\nstderr={getattr(result, 'stderr', '')}"
    )

    # The error envelope should carry the holder's workstream
    output_text = result.output
    stderr_text = getattr(result, "stderr", "") or ""
    # Try to parse JSON from whichever stream has it
    for text in [output_text, stderr_text]:
        text = text.strip()
        if text.startswith("{"):
            try:
                payload = json.loads(text)
                holder = payload.get("data", {}).get("holder", {})
                assert holder.get("workstream") == "ws-from-A", (
                    f"Expected holder.workstream == 'ws-from-A'; got {holder.get('workstream')!r}"
                )
                assert holder.get("session_id") == "session-A-reserve", (
                    f"Expected holder.session_id == 'session-A-reserve'; "
                    f"got {holder.get('session_id')!r}"
                )
            except (json.JSONDecodeError, KeyError):
                pass


# ---------------------------------------------------------------------------
# Test 9: reserve-list returns items under current upstream
# ---------------------------------------------------------------------------

def test_reserve_list_returns_items_under_current_upstream(clean_db, monkeypatch):
    """reserve-list --upstream <id> returns items taken under that upstream."""
    from em_proj.state.reserve import reserve_take

    upstream = "github.com:o/r"

    # Take two reservations directly (avoids needing resolve_upstream_identity in verb)
    reserve_take(
        "migrations.v100",
        upstream_identity=upstream,
        workstream="ws-list-test",
        ttl=1800,
    )
    reserve_take(
        "migrations.v200",
        upstream_identity=upstream,
        workstream="ws-list-test",
        ttl=1800,
    )

    result = runner.invoke(
        app, ["state", "reserve-list", "--upstream", "github.com:o/r", "--json"]
    )
    assert result.exit_code == 0, (
        f"Expected exit 0; got {result.exit_code}\n"
        f"stdout={result.stdout}\nstderr={getattr(result, 'stderr', '')}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["status"] == "ok"
    items = payload["data"]["items"]
    assert len(items) == 2, f"Expected 2 items; got {len(items)}: {items}"


# ---------------------------------------------------------------------------
# Test 10: reserve-list --category filter
# ---------------------------------------------------------------------------

def test_reserve_list_category_filter(clean_db, monkeypatch):
    """reserve-list --category <name> filters items by area prefix-before-first-dot."""
    from em_proj.state.reserve import reserve_take

    upstream = "github.com:o/r"
    reserve_take("migrations.v100", upstream_identity=upstream, workstream="ws", ttl=1800)
    reserve_take("migrations.v200", upstream_identity=upstream, workstream="ws", ttl=1800)
    reserve_take("db.5432", upstream_identity=upstream, workstream="ws", ttl=1800)

    # Filter by 'migrations' → 2 items
    result_migrations = runner.invoke(
        app, ["state", "reserve-list", "--upstream", upstream,
               "--category", "migrations", "--json"]
    )
    assert result_migrations.exit_code == 0, f"Expected exit 0; got {result_migrations.exit_code}"
    payload_m = json.loads(result_migrations.stdout.strip())
    assert len(payload_m["data"]["items"]) == 2, (
        f"Expected 2 migrations items; got {payload_m['data']['items']}"
    )

    # Filter by 'db' → 1 item
    result_db = runner.invoke(
        app, ["state", "reserve-list", "--upstream", upstream,
               "--category", "db", "--json"]
    )
    assert result_db.exit_code == 0, f"Expected exit 0; got {result_db.exit_code}"
    payload_db = json.loads(result_db.stdout.strip())
    assert len(payload_db["data"]["items"]) == 1, (
        f"Expected 1 db item; got {payload_db['data']['items']}"
    )


# ---------------------------------------------------------------------------
# Test 11: reserve-list --upstream canonicalizes raw URL
# ---------------------------------------------------------------------------

def test_reserve_list_upstream_override_canonicalizes(clean_db, monkeypatch):
    """reserve-list --upstream raw URL canonicalizes to host:owner/repo."""
    from em_proj.state.reserve import reserve_take

    canonical = "github.com:o/r"
    reserve_take("area.x", upstream_identity=canonical, workstream="ws", ttl=1800)

    # Pass the raw SCP-form URL; the verb should canonicalize to "github.com:o/r"
    result = runner.invoke(
        app, ["state", "reserve-list", "--upstream", "git@github.com:o/r.git", "--json"]
    )
    assert result.exit_code == 0, f"Expected exit 0; got {result.exit_code}"
    payload = json.loads(result.stdout.strip())
    assert payload["data"]["upstream_identity"] == "github.com:o/r", (
        f"Expected upstream_identity == 'github.com:o/r'; "
        f"got {payload['data'].get('upstream_identity')!r}"
    )
    assert len(payload["data"]["items"]) == 1, (
        f"Expected 1 item under canonical upstream; got {payload['data']['items']}"
    )


# ---------------------------------------------------------------------------
# Test 12: check --upstream routes to reserve namespace
# ---------------------------------------------------------------------------

def test_check_with_upstream_routes_to_reserve_namespace(clean_db, monkeypatch):
    """check <area> --upstream routes to reserve namespace; check without routes to claim namespace."""
    from em_proj.state.reserve import reserve_take

    upstream = "github.com:o/r"
    area = "migrations.v200"

    # Take a reservation under the upstream
    reserve_take(area, upstream_identity=upstream, workstream="ws-check", ttl=1800)

    # check --upstream → exit 0 (reserve namespace has the key)
    result_with = runner.invoke(
        app, ["state", "check", area, "--upstream", upstream, "--json"]
    )
    assert result_with.exit_code == 0, (
        f"Expected exit 0 (reserve namespace); got {result_with.exit_code}\n"
        f"stdout={result_with.stdout}\nstderr={getattr(result_with, 'stderr', '')}"
    )
    payload_with = json.loads(result_with.stdout.strip())
    assert payload_with["status"] == "ok"
    assert "holder" in payload_with["data"]

    # check WITHOUT --upstream → exit 2 (claim namespace, distinct — key absent there)
    result_without = runner.invoke(
        app, ["state", "check", area, "--json"]
    )
    assert result_without.exit_code == 2, (
        f"Expected exit 2 (claim namespace, not held); got {result_without.exit_code}\n"
        f"stdout={result_without.stdout}\nstderr={getattr(result_without, 'stderr', '')}"
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

class _FakeStdin:
    """Minimal fake stdin that controls isatty() return value and readline() response.

    Used only for the non-TTY exit test (test_reserve_nontty_exits_1_when_workstream_unset)
    where we monkeypatch sys.stdin to force isatty() → False. The TTY-prompt tests use
    _tty_sys_mock() instead because CliRunner overwrites sys.stdin during invoke.
    """

    def __init__(self, response: str, *, is_tty: bool):
        self._response = response
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty

    def readline(self) -> str:
        return self._response

    def read(self, n: int = -1) -> str:
        return self._response


def _make_fake_stdin(response: str, *, is_tty: bool) -> _FakeStdin:
    """Return a fake stdin object for monkeypatching sys.stdin in non-TTY tests."""
    return _FakeStdin(response, is_tty=is_tty)


def _tty_sys_mock():
    """Build a mock sys module for use with monkeypatch.setattr(state_mod, 'sys', ...).

    Mirrors the _tty_sys_mock() helper in test_state_lock_verbs.py.

    The challenge: CliRunner replaces sys.stdin and sys.stdout with BytesIO
    buffers during isolation, but the replacement happens on the real `sys`
    module — not on our custom module. To bridge this:
    - stdin.isatty() returns True (simulating a TTY so the prompt path fires)
    - stdin.readline() delegates dynamically to ``import sys; sys.stdin.readline()``
      so it reads from CliRunner's input BytesIO (which CliRunner patches on the
      real sys.stdin during invoke, AFTER our mock is in place).
    - All other attributes delegate to the real sys module.
    """
    import sys as real_sys
    import types

    class _StdinTtyMock:
        def isatty(self) -> bool:
            return True

        def readline(self) -> str:
            # Always read from the current real sys.stdin — this is the
            # CliRunner-patched BytesIO when called inside invoke().
            return real_sys.stdin.readline()

        def __getattr__(self, name: str):
            return getattr(real_sys.stdin, name)

    mock_sys = types.ModuleType("sys")
    mock_sys.__dict__.update(real_sys.__dict__)
    mock_sys.stdin = _StdinTtyMock()
    # Keep stderr as-is so CliRunner can capture it
    mock_sys.stderr = real_sys.stderr
    return mock_sys
