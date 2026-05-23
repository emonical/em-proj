"""Unit tests for em_proj.identity — stateless, no Redis required.

Covers IDENT-01 contract: env-var resolution, fallback chain, tr-/-to-dash hash,
and composite dict shape + type invariants.

No Redis fixtures here — identity.py is stdlib + psutil only; tests run with Redis
completely absent (no live Redis dependency).

Test inventory (10 tests):
  test_resolve_session_id_with_env_var_set        — env var returned as-is
  test_resolve_session_id_fallback_when_unset     — non-empty fallback when var missing
  test_resolve_session_id_fallback_when_empty     — empty string treated as unset
  test_resolve_project_hash_slash_to_dash         — cwd translated correctly
  test_resolve_project_hash_starts_with_dash      — absolute path always yields leading dash
  test_composite_has_exact_keys                   — exactly 5 keys in returned dict
  test_composite_pid_equals_os_getpid             — pid value matches current process
  test_composite_proc_start_epoch_is_recent_float — float type, within last 5 minutes
  test_composite_boot_id_stable_and_fixed_length  — deterministic 16-hex-char string
  test_composite_session_id_matches_resolver      — composite delegates to resolve_session_id
"""
from __future__ import annotations

import os
import time

import pytest

from em_proj.identity import (
    current_process_composite,
    resolve_project_hash,
    resolve_session_id,
)


# ---------------------------------------------------------------------------
# resolve_session_id tests
# ---------------------------------------------------------------------------


def test_resolve_session_id_with_env_var_set(monkeypatch) -> None:
    """When CLAUDE_CODE_SESSION_ID is set to a non-empty string, it is returned as-is."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "test-uuid-abc")
    result = resolve_session_id()
    assert result == "test-uuid-abc"


def test_resolve_session_id_fallback_when_unset(monkeypatch) -> None:
    """When CLAUDE_CODE_SESSION_ID is absent, fallback is a non-empty string."""
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    result = resolve_session_id()
    assert isinstance(result, str)
    assert len(result) > 0


def test_resolve_session_id_fallback_when_empty(monkeypatch) -> None:
    """An empty CLAUDE_CODE_SESSION_ID env var is treated as unset — fallback returned."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "")
    result = resolve_session_id()
    # The fallback must be non-empty (empty env var → not the empty string)
    assert isinstance(result, str)
    assert len(result) > 0
    assert result != ""


# ---------------------------------------------------------------------------
# resolve_project_hash tests
# ---------------------------------------------------------------------------


def test_resolve_project_hash_slash_to_dash(monkeypatch, tmp_path) -> None:
    """resolve_project_hash returns the absolute cwd with every '/' replaced by '-'."""
    monkeypatch.chdir(tmp_path)
    result = resolve_project_hash()
    expected = str(tmp_path.resolve()).replace("/", "-")
    assert result == expected, f"expected {expected!r}, got {result!r}"


def test_resolve_project_hash_starts_with_dash(monkeypatch, tmp_path) -> None:
    """Absolute paths always start with '/' which becomes '-', so output starts with '-'."""
    monkeypatch.chdir(tmp_path)
    result = resolve_project_hash()
    assert result.startswith("-"), f"expected leading dash from absolute path, got {result!r}"


# ---------------------------------------------------------------------------
# current_process_composite tests
# ---------------------------------------------------------------------------


def test_composite_has_exact_keys() -> None:
    """current_process_composite() returns a dict with exactly the five expected keys."""
    d = current_process_composite()
    expected_keys = {"pid", "proc_start_epoch", "boot_id", "session_id", "project_hash"}
    assert set(d.keys()) == expected_keys, (
        f"key mismatch — expected {expected_keys}, got {set(d.keys())}"
    )


def test_composite_pid_equals_os_getpid() -> None:
    """The 'pid' field in the composite matches os.getpid() of the calling process."""
    d = current_process_composite()
    assert d["pid"] == os.getpid(), (
        f"pid mismatch — expected {os.getpid()}, got {d['pid']}"
    )


def test_composite_proc_start_epoch_is_recent_float() -> None:
    """'proc_start_epoch' is a float and within 5 minutes of now (test process can't be old)."""
    d = current_process_composite()
    proc_start = d["proc_start_epoch"]
    assert isinstance(proc_start, float), f"expected float, got {type(proc_start)}"
    now = time.time()
    age_seconds = now - proc_start
    assert 0 <= age_seconds < 300, (
        f"proc_start_epoch {proc_start} is {age_seconds:.1f}s from now — "
        "expected < 5 minutes (test process should be recent)"
    )


def test_composite_boot_id_stable_and_fixed_length() -> None:
    """'boot_id' is a stable 16-char hex string; two calls return the same value."""
    d1 = current_process_composite()
    d2 = current_process_composite()
    boot_id = d1["boot_id"]
    assert isinstance(boot_id, str), f"expected str, got {type(boot_id)}"
    assert len(boot_id) == 16, f"expected 16 chars, got {len(boot_id)}: {boot_id!r}"
    # Hex chars only (sha256 hexdigest prefix)
    assert all(c in "0123456789abcdef" for c in boot_id), (
        f"boot_id contains non-hex chars: {boot_id!r}"
    )
    # Same boot → same boot_id
    assert d1["boot_id"] == d2["boot_id"], (
        f"boot_id not stable: {d1['boot_id']!r} != {d2['boot_id']!r}"
    )


def test_composite_session_id_matches_resolver(monkeypatch) -> None:
    """The composite's session_id uses resolve_session_id(), not a parallel implementation."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sentinel-session-xyz")
    d = current_process_composite()
    expected = resolve_session_id()
    assert d["session_id"] == expected, (
        f"composite session_id {d['session_id']!r} != resolve_session_id() {expected!r}"
    )
