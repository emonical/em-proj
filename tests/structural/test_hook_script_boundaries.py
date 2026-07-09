"""Architectural-boundary invariants for the Phase 12 Claude Code hook scripts.

These are durable regression guards for design decisions that are NOT
obvious from reading the code — each one, if it goes red, means someone has
crossed a boundary the hook design depends on:

  1. Neither hook script imports the `em_proj` Python package. Both are
     designed to shell out to the installed `em-proj` binary only (mirroring
     `~/.claude/scripts/session-registry.py`) — this keeps them testable and
     installable independent of the package's own import graph, and never
     couples Claude Code's hook runtime to em-proj's internal module layout.
  2. Every literal `sys.exit(N)` call in either file has N == 0. HOOK-04
     requires both hooks to always exit 0 — a hook script that ever exits
     non-zero would break SessionStart/UserPromptSubmit for every Claude Code
     session in this repo, not just fail loudly for the user running em-proj.
  3. Both hooks gate on the literal `EM_SESSIONS_AUTOSTART` env var name —
     the opt-in, zero-footprint-by-default contract locked in 12-CONTEXT.md.

Named for the boundary each test protects rather than for the phase that
introduced them — phase numbers tell a future maintainer nothing about what
breaks. Self-contained — no imports from sibling structural test modules.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SESSION_START_HOOK = REPO_ROOT / "scripts" / "hooks" / "session_start.py"
USER_PROMPT_SUBMIT_HOOK = REPO_ROOT / "scripts" / "hooks" / "user_prompt_submit.py"
HOOK_SCRIPTS = (SESSION_START_HOOK, USER_PROMPT_SUBMIT_HOOK)


def _import_lines(path: Path) -> list[str]:
    """Return the stripped `import`/`from` lines of a module's source."""
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]


# ---------------------------------------------------------------------------
# Both hooks shell out only — never import em_proj internals
# ---------------------------------------------------------------------------


def test_hook_scripts_never_import_em_proj_package() -> None:
    """Neither hook script imports the `em_proj` package.

    Both hooks are designed to shell out to the installed `em-proj` binary
    only, never import the Python package directly (keeps them testable and
    installable independent of the package's own import graph).
    """
    for path in HOOK_SCRIPTS:
        assert not any("em_proj" in line for line in _import_lines(path)), (
            f"{path.name} imports em_proj — hook scripts must shell out to the "
            "em-proj binary via subprocess only, never import package internals."
        )


# ---------------------------------------------------------------------------
# HOOK-04 — hooks always exit 0
# ---------------------------------------------------------------------------


def test_hook_scripts_always_exit_zero() -> None:
    """Every literal `sys.exit(N)` call in either hook script has N == 0.

    HOOK-04 requires both hooks to never break session startup / prompt
    submission — a non-zero exit anywhere would violate that contract for
    every Claude Code session in this repo.
    """
    for path in HOOK_SCRIPTS:
        src = path.read_text()
        calls = re.findall(r"sys\.exit\(\s*(\d+)\s*\)", src)
        assert calls, f"no literal sys.exit(N) call found in {path.name}"
        assert set(calls) == {"0"}, (
            f"found a non-zero sys.exit call in {path.name} — hook scripts "
            "must never break session startup (HOOK-04)."
        )


# ---------------------------------------------------------------------------
# Opt-in gate — both hooks check the same literal env var
# ---------------------------------------------------------------------------


def test_hook_scripts_gate_on_em_sessions_autostart_env_var() -> None:
    """Both hook scripts reference the literal `EM_SESSIONS_AUTOSTART` env var.

    This is the opt-in, zero-footprint-by-default gate locked in
    12-CONTEXT.md: unset means both hooks are an immediate no-op.
    """
    for path in HOOK_SCRIPTS:
        assert "EM_SESSIONS_AUTOSTART" in path.read_text(), (
            f"{path.name} does not reference EM_SESSIONS_AUTOSTART — the "
            "opt-in/zero-footprint-by-default gate must be present verbatim."
        )
