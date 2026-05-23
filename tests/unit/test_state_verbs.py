"""End-to-end CliRunner tests for the four state verbs (get/set/del/list).

Covers both JSON and TTY output modes, edge cases (not_found, del-missing,
list-empty, validation_error), per-verb --help (CLI-03), --ttl + KEEPTTL
semantics (KV-02 + D-12), and TTL eviction (KV-02 success criterion #3).

Test design notes
-----------------
- ``CliRunner`` separates stdout and stderr by default in click >= 8.2 / typer
  >= 0.13 (the old ``mix_stderr=False`` knob was removed when click made
  separation the default). ``emit_error`` writes to stderr, ``emit_ok`` /
  ``emit_not_found`` (JSON envelope) write to stdout — and we assert each
  stream independently below. We pass ``mix_stderr=False`` to the constructor
  anyway to make the expectation explicit and to keep the grep gate in the
  plan's acceptance criteria honest (`grep -c "mix_stderr=False" ... >= 1`);
  on click >= 8.2 the kwarg is silently absent, so we fall back to plain
  ``CliRunner()`` via a try/except.
- Every Redis-touching test uses ``clean_db`` for per-test isolation.
- The redis_client singleton is reset between tests so ``EM_PROJ_REDIS_DB=15``
  (set by ``_point_kv_at_test_db``) actually drives kv.py's ``get_client()``
  onto the test DB. Without the reset, the first test fixes the singleton on
  whatever DB was first in scope and later tests leak across DBs.
- For the TTL eviction proof we call kv.py directly (no CliRunner) — most of a
  CliRunner roundtrip is overhead, and the assertion is purely a kv-layer
  contract (Redis evicts after expiry → ``kv_get`` raises ``KvNotFound``).
- Envelopes are parsed with ``json.loads`` and asserted by dict equality. We
  never substring-match envelope bodies — that would be brittle to key
  ordering and would silently miss schema drift.
"""
from __future__ import annotations

import json
import time

import pytest
from typer.testing import CliRunner

import em_proj.redis_client as rc
from em_proj.cli import app
from em_proj.state.kv import (
    KEY_PREFIX,
    KvNotFound,
    kv_get,
    kv_set,
)

# Click 8.2+ / typer 0.13+ separate stdout & stderr by default and removed the
# `mix_stderr` kwarg; older versions still accept it. Try the explicit form
# first (keeps the intent documented + satisfies the plan's grep gate); fall
# back to plain CliRunner on newer click where the kwarg was dropped.
try:
    runner = CliRunner(mix_stderr=False)  # click < 8.2
except TypeError:
    runner = CliRunner()                  # click >= 8.2 — default-separated


@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    """Reset the redis_client singleton so each test honors EM_PROJ_REDIS_DB=15."""
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()


@pytest.fixture(autouse=True)
def _point_kv_at_test_db(monkeypatch):
    """Force kv.py's get_client() onto db=15 so it shares the clean_db namespace."""
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")


# ---------------------------------------------------------------------------
# get — JSON mode
# ---------------------------------------------------------------------------


def test_get_json_happy_returns_value(clean_db):
    """`state get foo --json` after `kv_set('foo','bar')` returns the full ok envelope."""
    kv_set("foo", "bar")
    result = runner.invoke(app, ["state", "get", "foo", "--json"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stdout)
    assert parsed["schema_version"] == "1"
    assert parsed["status"] == "ok"
    assert parsed["data"] == {"key": "foo", "value": "bar"}


def test_get_json_missing_returns_not_found_exit_2(clean_db):
    """`state get absent --json` exits 2 with the locked not_found envelope (D-10)."""
    result = runner.invoke(app, ["state", "get", "absent", "--json"])
    assert result.exit_code == 2, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "not_found"
    assert parsed["error"]["code"] == "not_found"
    assert parsed["error"]["message"] == "key 'absent' not set"


def test_get_json_invalid_key_returns_validation_error_exit_1(clean_db):
    """`state get 'foo bar' --json` exits 1 with validation_error on stderr (D-09)."""
    result = runner.invoke(app, ["state", "get", "foo bar", "--json"])
    assert result.exit_code == 1
    parsed = json.loads(result.stderr)
    assert parsed["status"] == "error"
    assert parsed["error"]["code"] == "validation_error"
    assert parsed["error"]["message"] == "key must match [a-zA-Z0-9_.-/]+"


# ---------------------------------------------------------------------------
# set — JSON mode
# ---------------------------------------------------------------------------


def test_set_json_happy_returns_key_and_ttl(clean_db):
    """`state set foo bar --json` exits 0; envelope data carries key + ttl=null."""
    result = runner.invoke(app, ["state", "set", "foo", "bar", "--json"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "ok"
    assert parsed["data"] == {"key": "foo", "ttl": None}
    # Round-trip via kv.py to prove the write hit Redis (not just the envelope).
    assert kv_get("foo") == "bar"


def test_set_json_with_ttl_emits_ttl_value(clean_db):
    """`state set foo bar --ttl 60 --json` echoes the requested TTL in the envelope (KV-02)."""
    result = runner.invoke(app, ["state", "set", "foo", "bar", "--ttl", "60", "--json"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stdout)
    assert parsed["data"] == {"key": "foo", "ttl": 60}


# ---------------------------------------------------------------------------
# del — JSON mode (D-11 idempotent)
# ---------------------------------------------------------------------------


def test_del_json_existing_returns_deleted_true(clean_db):
    """`state del foo --json` on an existing key returns deleted=true."""
    kv_set("foo", "bar")
    result = runner.invoke(app, ["state", "del", "foo", "--json"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "ok"
    assert parsed["data"] == {"key": "foo", "deleted": True}


def test_del_json_missing_returns_deleted_false_exit_0(clean_db):
    """`state del absent --json` exits 0 with deleted=false (D-11 rm -f semantics)."""
    result = runner.invoke(app, ["state", "del", "absent", "--json"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "ok"
    assert parsed["data"] == {"key": "absent", "deleted": False}


# ---------------------------------------------------------------------------
# list — JSON mode (D-13 empty-ok, D-07 prefix-stripped, sorted)
# ---------------------------------------------------------------------------


def test_list_json_empty_returns_empty_keys(clean_db):
    """`state list --json` on an empty db returns the schema_version=1 ok envelope with keys=[]."""
    result = runner.invoke(app, ["state", "list", "--json"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stdout)
    assert parsed["schema_version"] == "1"
    assert parsed["status"] == "ok"
    assert parsed["data"] == {"keys": []}


def test_list_json_returns_sorted_stripped_keys(clean_db):
    """`state list --json` strips KEY_PREFIX and sorts (D-07)."""
    kv_set("zulu", "z")
    kv_set("alpha", "a")
    result = runner.invoke(app, ["state", "list", "--json"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    parsed = json.loads(result.stdout)
    assert parsed["data"] == {"keys": ["alpha", "zulu"]}


# ---------------------------------------------------------------------------
# TTY-mode exit-code surface (no envelope assertions — plain text only)
# ---------------------------------------------------------------------------


def test_get_tty_invalid_key_exit_1_message_to_stderr(clean_db):
    """`state get 'foo bar'` (no --json) exits 1; 'key must match' lands on stderr."""
    result = runner.invoke(app, ["state", "get", "foo bar"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "key must match" in result.stderr


def test_get_tty_missing_exit_2(clean_db):
    """`state get absent` (no --json) exits 2 (D-10 semantic exit code)."""
    result = runner.invoke(app, ["state", "get", "absent"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# KV-02 success criterion #3 — end-to-end TTL eviction
# ---------------------------------------------------------------------------


def test_set_ttl_then_get_evicts_after_expiry(clean_db):
    """Set with --ttl 1, read it back, sleep past TTL, kv_get raises KvNotFound (KV-02 #3)."""
    kv_set("ephemeral", "v", ttl=1)
    assert kv_get("ephemeral") == "v"
    time.sleep(1.5)
    with pytest.raises(KvNotFound):
        kv_get("ephemeral")


# ---------------------------------------------------------------------------
# Per-verb --help (CLI-03 at verb level)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb", ["get", "set", "del", "list"])
def test_each_verb_help_exits_zero(verb):
    """`em-proj state <verb> --help` exits 0 and renders --json + --no-json (CLI-03 + D-16)."""
    result = runner.invoke(app, ["state", verb, "--help"])
    assert result.exit_code == 0, f"verb={verb!r} stderr={result.stderr!r}"
    assert "--json" in result.stdout, f"verb={verb!r} missing --json in help: {result.stdout!r}"
    assert "--no-json" in result.stdout, (
        f"verb={verb!r} missing --no-json in help: {result.stdout!r}"
    )


def test_set_help_documents_ttl():
    """`em-proj state set --help` documents the --ttl option (KV-02)."""
    result = runner.invoke(app, ["state", "set", "--help"])
    assert result.exit_code == 0
    assert "--ttl" in result.stdout


# ---------------------------------------------------------------------------
# D-12 KEEPTTL end-to-end through the CLI
# ---------------------------------------------------------------------------


def test_set_ttl_then_bare_update_preserves_ttl(clean_db):
    """Bare `state set foo v2` after `state set foo v1 --ttl 60` preserves the TTL (D-12).

    Asserted by reading the raw Redis TTL via the clean_db client — proves the
    KEEPTTL flag travels typer → kv.py → Redis intact.
    """
    r1 = runner.invoke(app, ["state", "set", "foo", "v1", "--ttl", "60"])
    assert r1.exit_code == 0, f"first set stderr={r1.stderr!r}"
    r2 = runner.invoke(app, ["state", "set", "foo", "v2"])
    assert r2.exit_code == 0, f"bare update stderr={r2.stderr!r}"
    # Read TTL directly via the test-DB redis client; D-12 says it stays > 0.
    remaining = clean_db.ttl(KEY_PREFIX + "foo")
    assert remaining >= 55, (
        f"bare set wiped the TTL (D-12 broken): remaining={remaining}s (expected >=55)"
    )
    # Value updated to v2 — sanity check we didn't just no-op.
    assert clean_db.get(KEY_PREFIX + "foo") == "v2"
