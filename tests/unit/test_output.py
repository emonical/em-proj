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
    emit_error,
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
