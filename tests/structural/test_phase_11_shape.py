"""Phase 11 structural invariants — file-presence, symbol-presence,
prohibited-import, and no-mbox_write assertions.

Each test is independently GREEN after Plan 11-01 (except
test_phase_11_summaries_exist which skips until SUMMARY files exist).

Self-contained — no imports from sibling test_phase_*_shape.py files
(per Phase 1-10 precedent).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "11-listener-daemon"

SESSION_DAEMON = REPO_ROOT / "src" / "em_proj" / "session" / "_daemon.py"
SESSION_OPS = REPO_ROOT / "src" / "em_proj" / "session" / "_ops.py"
SESSION_INIT = REPO_ROOT / "src" / "em_proj" / "session" / "__init__.py"


# ---------------------------------------------------------------------------
# Test 1 — _daemon.py exists
# ---------------------------------------------------------------------------


def test_daemon_module_exists() -> None:
    """src/em_proj/session/_daemon.py must exist after Plan 11-01."""
    assert SESSION_DAEMON.exists(), (
        f"_daemon.py not found at {SESSION_DAEMON.relative_to(REPO_ROOT)} — "
        "Phase 11 daemon module not yet created"
    )


# ---------------------------------------------------------------------------
# Test 2 — _daemon.py imports subprocess (legitimately)
# ---------------------------------------------------------------------------


def test_daemon_module_imports_subprocess() -> None:
    """_daemon.py must import subprocess — it owns all process-management code."""
    src = SESSION_DAEMON.read_text()
    assert "import subprocess" in src, (
        "_daemon.py must import subprocess (it owns the Popen/start_new_session=True logic). "
        "This import is intentionally present here and prohibited in _ops.py."
    )


# ---------------------------------------------------------------------------
# Test 3 — _daemon.py does NOT import typer
# ---------------------------------------------------------------------------


def test_daemon_module_not_import_typer() -> None:
    """_daemon.py must not import typer — it is a pure ops module, not a verb layer."""
    src = SESSION_DAEMON.read_text()
    import_lines = [
        line.strip()
        for line in src.splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]
    assert not any("typer" in line for line in import_lines), (
        "typer found in _daemon.py import lines — typer belongs in session/__init__.py "
        "(the D-14 verb layer), never in the ops module."
    )


# ---------------------------------------------------------------------------
# Test 4 — _daemon.py never calls mbox_write (AST check)
# ---------------------------------------------------------------------------


def test_daemon_module_not_call_mbox_write() -> None:
    """_daemon.py must not call mbox_write anywhere.

    DAEMON-02 is satisfied at the system level: Phase 10's send path writes the
    durable mailbox record at send time. The daemon's job is liveness, not delivery.

    AST-checked: only Attribute call targets are inspected, so docstring/comment
    references to 'mbox_write' do not produce a false positive.
    """
    src = SESSION_DAEMON.read_text()
    tree = ast.parse(src)
    called_attrs = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "mbox_write" not in called_attrs, (
        "_daemon.py must not call mbox_write — DAEMON-02 is satisfied at system level "
        "by send-time write in message/_ops.py. Daemon receives pub/sub notifications "
        "but does not re-write messages."
    )


# ---------------------------------------------------------------------------
# Test 5 — _ops.py still prohibits subprocess
# ---------------------------------------------------------------------------


def test_session_ops_prohibits_subprocess() -> None:
    """session/_ops.py must not import subprocess — enforces D-14 boundary."""
    src = SESSION_OPS.read_text()
    import_lines = [
        line.strip()
        for line in src.splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]
    assert not any("subprocess" in line for line in import_lines), (
        "subprocess found in session/_ops.py import lines — all process/signal/subprocess "
        "imports belong in session/_daemon.py, not in _ops.py. "
        "See _ops.py docstring 'Prohibited imports' section."
    )


# ---------------------------------------------------------------------------
# Test 6 — session/__init__.py registers listen AND stop commands
# ---------------------------------------------------------------------------


def test_session_init_has_listen_stop_commands() -> None:
    """session/__init__.py must register @session_app.command('listen') and 'stop'."""
    src = SESSION_INIT.read_text()
    assert '@session_app.command("listen")' in src, (
        '@session_app.command("listen") missing from session/__init__.py — '
        "Phase 11 listen verb not yet wired"
    )
    assert '@session_app.command("stop")' in src, (
        '@session_app.command("stop") missing from session/__init__.py — '
        "Phase 11 stop verb (or stub) not yet wired"
    )


# ---------------------------------------------------------------------------
# Test 7 — _daemon.py defines DAEMON_KEY_PREFIX = "daemon:"
# ---------------------------------------------------------------------------


def test_daemon_key_prefix_constant() -> None:
    """_daemon.py must define DAEMON_KEY_PREFIX with value 'daemon:'."""
    src = SESSION_DAEMON.read_text()
    assert "DAEMON_KEY_PREFIX" in src or '"daemon:"' in src or "'daemon:'" in src, (
        "DAEMON_KEY_PREFIX constant (or 'daemon:' literal) missing from _daemon.py — "
        "daemon HASH key namespace must be explicitly defined"
    )


# ---------------------------------------------------------------------------
# Test 8 — every 11-*-PLAN.md has a 11-*-SUMMARY.md sibling
# ---------------------------------------------------------------------------


def test_phase_11_summaries_exist() -> None:
    """Every 11-*-PLAN.md must have a 11-*-SUMMARY.md sibling once the phase completes.

    Skips when the planning worktree is not attached (legitimate developer setup).
    """
    if not PHASE_DIR.exists():
        pytest.skip(
            f"{PHASE_DIR.relative_to(REPO_ROOT)} not present — "
            "planning worktree may not be attached on this checkout"
        )
    plans = sorted(PHASE_DIR.glob("11-*-PLAN.md"))
    if not plans:
        pytest.skip(f"no 11-*-PLAN.md files yet under {PHASE_DIR.relative_to(REPO_ROOT)}")
    for plan in plans:
        summary = plan.parent / plan.name.replace("-PLAN.md", "-SUMMARY.md")
        assert summary.exists(), (
            f"Missing SUMMARY for {plan.name}: expected {summary.name} "
            f"in {PHASE_DIR.relative_to(REPO_ROOT)}"
        )
