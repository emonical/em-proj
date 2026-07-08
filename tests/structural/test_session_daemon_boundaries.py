"""Architectural-boundary invariants for the session listener daemon.

These are durable regression guards for design decisions that are NOT
obvious from reading the code — each one, if it goes red, means someone
has crossed a boundary the daemon design depends on:

  1. The D-14 layering split: the daemon *ops* module (`session/_daemon.py`)
     owns all process-management code but stays free of the CLI framework;
     the verb layer (`session/__init__.py`) owns typer. Conversely
     `session/_ops.py` must never pull in `subprocess`.
  2. DAEMON-02's "liveness, not delivery" contract: the daemon must never
     write the mailbox itself — the durable record is written at send time
     in `message/_ops.py`. A daemon that also wrote would double-deliver.

Named for the boundary each test protects rather than for the phase that
introduced them — phase numbers tell a future maintainer nothing about
what breaks. (Superseded `test_phase_11_shape.py`, which mixed these three
invariants with point-in-time "did we build it" and planning-artifact
checks that behavioral tests already cover.)

Self-contained — no imports from sibling structural test modules.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SESSION_DAEMON = REPO_ROOT / "src" / "em_proj" / "session" / "_daemon.py"
SESSION_OPS = REPO_ROOT / "src" / "em_proj" / "session" / "_ops.py"


def _import_lines(path: Path) -> list[str]:
    """Return the stripped `import`/`from` lines of a module's source."""
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]


# ---------------------------------------------------------------------------
# D-14 layering — the daemon ops module stays framework-free
# ---------------------------------------------------------------------------


def test_daemon_ops_module_does_not_import_typer() -> None:
    """`session/_daemon.py` must not import typer.

    It is a pure ops module; typer belongs only in the `session/__init__.py`
    verb layer. If typer leaks in here, the D-14 ops/verb split has been
    crossed.
    """
    assert not any("typer" in line for line in _import_lines(SESSION_DAEMON)), (
        "typer found in _daemon.py imports — the CLI framework belongs in "
        "session/__init__.py (the D-14 verb layer), never in the ops module."
    )


def test_session_ops_module_prohibits_subprocess() -> None:
    """`session/_ops.py` must not import subprocess.

    All process/signal/subprocess code lives in `session/_daemon.py`. This is
    the mirror of the typer boundary: `_ops.py` is pure business logic and must
    stay free of process-management imports.
    """
    assert not any("subprocess" in line for line in _import_lines(SESSION_OPS)), (
        "subprocess found in session/_ops.py imports — all process/signal/"
        "subprocess imports belong in session/_daemon.py. See the 'Prohibited "
        "imports' section of the _ops.py docstring."
    )


# ---------------------------------------------------------------------------
# DAEMON-02 — the daemon does liveness, not delivery
# ---------------------------------------------------------------------------


def test_daemon_never_writes_the_mailbox() -> None:
    """`session/_daemon.py` must never call `mbox_write`.

    DAEMON-02 is satisfied at the system level: Phase 10's send path writes the
    durable mailbox record at send time (`message/_ops.py`). The daemon's job is
    session liveness — it receives pub/sub notifications but does NOT re-write
    messages. A daemon that also wrote the mailbox would double-deliver.

    AST-checked: only `Attribute` call targets are inspected, so a docstring or
    comment mentioning ``mbox_write`` never produces a false positive.
    """
    tree = ast.parse(SESSION_DAEMON.read_text())
    called_attrs = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "mbox_write" not in called_attrs, (
        "_daemon.py calls mbox_write — DAEMON-02 requires delivery to happen at "
        "send time in message/_ops.py, not in the daemon. The daemon does "
        "liveness, not delivery."
    )
