"""Unit tests for em_proj.output — CLI-05 envelope shape, exit codes, TTY routing.

Each test asserts BOTH the side-effect (stdout/stderr capture via capsys) and
the exit code (the SystemExit value via pytest.raises). Envelopes are parsed
with json.loads and asserted by dict-key equality — never substring matching,
which would be brittle to key ordering.

Tests use capsys for stdout/stderr capture and pytest.raises(SystemExit) for
exit-code assertion. Tests do NOT require Redis or typer.
"""
from __future__ import annotations

import json

import pytest

from em_proj.output import (
    SCHEMA_VERSION,
    _HOLDER_DISCLOSURE_KEYS,
    emit_error,
    emit_held_by_another,
    emit_not_found,
    emit_ok,
    resolve_json_mode,
)


# --------------------------------------------------------------------------
# emit_ok
# --------------------------------------------------------------------------


def test_emit_ok_json_mode_envelope_shape(capsys) -> None:
    """emit_ok in JSON mode exits 0 and emits the D-01 success envelope."""
    with pytest.raises(SystemExit) as exc_info:
        emit_ok({"keys": ["foo", "bar"]}, json_mode=True)
    assert exc_info.value.code == 0

    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert set(parsed.keys()) == {"schema_version", "status", "data"}
    assert parsed["schema_version"] == "1"
    assert parsed["status"] == "ok"
    assert parsed["data"] == {"keys": ["foo", "bar"]}


def test_emit_ok_json_mode_is_compact_with_newline(capsys) -> None:
    """JSON output is compact (no spaces after : or ,) and ends in one newline (D-04)."""
    with pytest.raises(SystemExit):
        emit_ok({"keys": ["foo"]}, json_mode=True)

    out = capsys.readouterr().out
    assert out.endswith("\n")
    assert not out[:-1].endswith("\n")  # exactly one trailing newline
    body = out[:-1]
    assert ", " not in body
    assert ": " not in body


def test_emit_ok_tty_mode_no_json(capsys) -> None:
    """emit_ok with json_mode=False renders plain text — no envelope keys leak."""
    with pytest.raises(SystemExit) as exc_info:
        emit_ok({"keys": ["foo"]}, json_mode=False)
    assert exc_info.value.code == 0

    out = capsys.readouterr().out
    assert "schema_version" not in out


def test_emit_ok_auto_detect_uses_isatty(monkeypatch, capsys) -> None:
    """json_mode=None with isatty()->True auto-detects TTY → plain text output."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    with pytest.raises(SystemExit):
        emit_ok({"keys": []}, json_mode=None)

    out = capsys.readouterr().out
    assert "schema_version" not in out


def test_emit_ok_auto_detect_non_tty_emits_json(monkeypatch, capsys) -> None:
    """json_mode=None with isatty()->False auto-detects non-TTY → JSON output."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    with pytest.raises(SystemExit):
        emit_ok({"keys": []}, json_mode=None)

    parsed = json.loads(capsys.readouterr().out)
    assert parsed["schema_version"] == "1"


# --------------------------------------------------------------------------
# emit_not_found
# --------------------------------------------------------------------------


def test_emit_not_found_exit_code_2(capsys) -> None:
    """emit_not_found raises SystemExit with code 2."""
    with pytest.raises(SystemExit) as exc_info:
        emit_not_found("key 'foo' not set", json_mode=True)
    assert exc_info.value.code == 2
    capsys.readouterr()  # drain capture


def test_emit_not_found_json_envelope_has_error_block(capsys) -> None:
    """not_found JSON envelope carries status=not_found and a {code,message} error block."""
    with pytest.raises(SystemExit):
        emit_not_found("key 'foo' not set", json_mode=True)

    parsed = json.loads(capsys.readouterr().out)
    assert parsed["status"] == "not_found"
    assert parsed["error"]["code"] == "not_found"
    assert parsed["error"]["message"] == "key 'foo' not set"


def test_emit_not_found_tty_mode_writes_to_stderr(capsys) -> None:
    """emit_not_found in plain mode writes the message to stderr, not stdout."""
    with pytest.raises(SystemExit):
        emit_not_found("key 'foo' not set", json_mode=False)

    captured = capsys.readouterr()
    assert "key 'foo' not set" in captured.err
    assert captured.out == ""


# --------------------------------------------------------------------------
# emit_error
# --------------------------------------------------------------------------


def test_emit_error_exit_code_1(capsys) -> None:
    """emit_error raises SystemExit with code 1."""
    with pytest.raises(SystemExit) as exc_info:
        emit_error("validation_error", "bad key", json_mode=True)
    assert exc_info.value.code == 1
    capsys.readouterr()  # drain capture


def test_emit_error_json_envelope_to_stderr(capsys) -> None:
    """emit_error JSON envelope goes to stderr, not stdout (D-01 + PROJECT.md)."""
    with pytest.raises(SystemExit):
        emit_error("validation_error", "bad key", json_mode=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    parsed = json.loads(captured.err)
    assert parsed["status"] == "error"
    assert parsed["error"]["code"] == "validation_error"
    assert parsed["error"]["message"] == "bad key"


def test_emit_error_tty_mode_writes_human_message_to_stderr(capsys) -> None:
    """emit_error in plain mode writes 'em-proj: error: <message>' to stderr."""
    with pytest.raises(SystemExit):
        emit_error("validation_error", "bad key", json_mode=False)

    captured = capsys.readouterr()
    assert "em-proj: error: bad key" in captured.err


# --------------------------------------------------------------------------
# SCHEMA_VERSION + resolve_json_mode
# --------------------------------------------------------------------------


def test_schema_version_is_literal_one() -> None:
    """SCHEMA_VERSION is the string literal '1' (catches accidental int/float/bump)."""
    assert SCHEMA_VERSION == "1"
    assert isinstance(SCHEMA_VERSION, str)


def test_resolve_json_mode_forced_true() -> None:
    """resolve_json_mode(True) forces JSON regardless of TTY state."""
    assert resolve_json_mode(True) is True


def test_resolve_json_mode_forced_false() -> None:
    """resolve_json_mode(False) forces plain text regardless of TTY state."""
    assert resolve_json_mode(False) is False


def test_resolve_json_mode_none_uses_isatty(monkeypatch) -> None:
    """resolve_json_mode(None) with isatty()->False auto-detects non-TTY → JSON."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert resolve_json_mode(None) is True


# --------------------------------------------------------------------------
# emit_held_by_another (D-09 / D-15 / CLI-04 exit code 3)
# --------------------------------------------------------------------------


def test_emit_held_by_another_json_envelope(capsys) -> None:
    """emit_held_by_another in JSON mode exits 3 and emits the held_by_another envelope.

    D-09: displaced holder learns via held_by_another status.
    D-05: held_by_another status pre-announced in Phase 2; schema_version stays "1".
    CLI-04: exit code 3 reserved for held_by_another.
    """
    with pytest.raises(SystemExit) as exc_info:
        emit_held_by_another("held_by_another", "Lock foo held by session abc", json_mode=True)
    assert exc_info.value.code == 3

    captured = capsys.readouterr()
    assert captured.out == ""  # errors go to stderr, never stdout
    parsed = json.loads(captured.err)
    assert parsed["schema_version"] == "1"
    assert parsed["status"] == "held_by_another"
    assert parsed["error"]["code"] == "held_by_another"
    assert parsed["error"]["message"] == "Lock foo held by session abc"
    assert "data" not in parsed  # no holder kwarg → no data block


def test_emit_held_by_another_plain_mode(capsys) -> None:
    """emit_held_by_another in plain mode writes 'em-proj: <message>' to stderr, exits 3.

    Mirrors emit_error plain-mode behavior (D-15 symmetry).
    """
    with pytest.raises(SystemExit) as exc_info:
        emit_held_by_another("held_by_another", "lock held", json_mode=False)
    assert exc_info.value.code == 3

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "em-proj: lock held" in captured.err


def test_emit_held_by_another_with_holder_includes_sanitized_subset(capsys) -> None:
    """JSON mode with holder= emits data.holder containing exactly _HOLDER_DISCLOSURE_KEYS.

    T-3-XX-02: boot_id and proc_start_epoch are EXCLUDED from disclosure (machine-identifier
    leakage and process-start correlation surface respectively).
    """
    all_8_fields = {
        "pid": 12345,
        "proc_start_epoch": 1716480000.0,
        "boot_id": "abcdef1234567890",
        "session_id": "test-session-abc",
        "project_hash": "-Users-test-project",
        "reason": "holding the fort",
        "acquired_at": 1716480010.5,
        "expires_at": 1716480070.5,
    }

    with pytest.raises(SystemExit) as exc_info:
        emit_held_by_another(
            "held_by_another",
            "lock is held",
            holder=all_8_fields,
            json_mode=True,
        )
    assert exc_info.value.code == 3

    parsed = json.loads(capsys.readouterr().err)
    assert "data" in parsed
    holder_subset = parsed["data"]["holder"]

    # Exactly these keys — T-3-XX-02 mitigation
    assert set(holder_subset.keys()) == {
        "pid", "session_id", "project_hash", "acquired_at", "expires_at", "reason"
    }
    # Explicit exclusion assertions — boot_id leaks machine identity
    assert "boot_id" not in holder_subset
    # proc_start_epoch enables correlation/fingerprinting of process start times
    assert "proc_start_epoch" not in holder_subset

    # Values are passed through correctly
    assert holder_subset["pid"] == 12345
    assert holder_subset["session_id"] == "test-session-abc"
    assert holder_subset["reason"] == "holding the fort"


def test_emit_held_by_another_schema_version(capsys) -> None:
    """held_by_another envelope uses schema_version '1' (D-05: additive, no bump).

    Phase 2 D-05 pre-announced this status value; adding it is non-breaking.
    """
    with pytest.raises(SystemExit):
        emit_held_by_another("held_by_another", "held", json_mode=True)

    parsed = json.loads(capsys.readouterr().err)
    assert parsed["schema_version"] == "1"


def test_holder_disclosure_keys_constant_is_pinned_tuple() -> None:
    """_HOLDER_DISCLOSURE_KEYS is the exact pinned tuple — structural pin against drift.

    T-3-XX-02: the tuple is the single source of truth for holder disclosure.
    Changing it here requires understanding the security rationale above.
    boot_id and proc_start_epoch must NOT appear in this tuple.

    Phase 7 addendum (T-07-13 accepted): upstream_identity, workstream, and area
    were added to surface the winner's context in reservation conflicts (ROADMAP SC#2).
    Lock/claim holder dicts silently skip these keys (``if k in holder`` guard in
    emit_held_by_another), so lock/claim disclosure behavior is UNCHANGED.
    """
    assert _HOLDER_DISCLOSURE_KEYS == (
        "name",
        "pid",
        "session_id",
        "project_hash",
        "upstream_identity",  # Phase 7 reserve: upstream repo identity
        "workstream",         # Phase 7 reserve: workstream name (ROADMAP SC#2)
        "area",               # Phase 7 reserve: reservation area name
        "acquired_at",
        "expires_at",
        "reason",
    )
    assert "boot_id" not in _HOLDER_DISCLOSURE_KEYS
    assert "proc_start_epoch" not in _HOLDER_DISCLOSURE_KEYS
