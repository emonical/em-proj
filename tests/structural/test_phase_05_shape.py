"""Phase 5 structural invariants — AST-based shape assertions.

Encodes plan acceptance criteria as runtime assertions for Phase 5
(Global State — Skill Surface):

  Group A — lock.py invariants
  - lock_list_by_prefix is a module-level function in lock.py
  - lock.py still has no "typer" import (D-17 carry)
  - lock_list_by_prefix's arguments include "mine" and "stale"

  Group B — claim.py invariants
  - claim_list_by_prefix is a module-level function in claim.py
  - claim.py still has no "typer" import (D-17 carry)
  - claim_list_by_prefix's arguments include "mine", "active", "stale"

  Group C — state/__init__.py verb count
  - state/__init__.py has >= 11 @state_app.command() verbs
    (Phase 4 had 9; Phase 5 adds lock-list and claim-list = 11)

  Group D — SKILL.md write-boundary audit (SC#3)
  - SKILL.md file exists at the expected path
  - Skill body does NOT contain forbidden write verb strings
    ("em-proj state set", "em-proj state del",
     "em-proj state claim " (trailing space), "em-proj state lock " (trailing space))
  - Skill body DOES contain "em-proj state unlock" and "em-proj state release"
  - Skill body contains "AskUserQuestion" (confirmation mechanism per D-04)

  Group E — Phase 5 SUMMARY coverage
  - Every 05-NN-PLAN.md in phases/05-global-state-skill-surface/ has a SUMMARY

Mirrors the Phase 4 precedent in ``tests/structural/test_phase_04_shape.py``
and the Phase 3 precedent in ``tests/structural/test_phase_03_shape.py``.
Skips cleanly if a target file is absent so this module is safe to land
before its dependencies.

Each structural file is self-contained (helper functions copied, NOT shared
via a common module) per Phase 1+2+3+4 precedent.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_EM_PROJ = REPO_ROOT / "src" / "em_proj"
STATE_PKG = SRC_EM_PROJ / "state"
LOCK_PY = STATE_PKG / "lock.py"
CLAIM_PY = STATE_PKG / "claim.py"
STATE_INIT = STATE_PKG / "__init__.py"
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "05-global-state-skill-surface"

# SKILL.md path resolution (CI portability requirement):
#   Primary:   Path.home() / ".claude" / "skills" / "em-global-state" / "SKILL.md"
#   Fallback:  REPO_ROOT / ".claude" / "skills" / "em-global-state" / "SKILL.md"
_skill_primary = Path.home() / ".claude" / "skills" / "em-global-state" / "SKILL.md"
_skill_fallback = REPO_ROOT / ".claude" / "skills" / "em-global-state" / "SKILL.md"
SKILL_PATH: Path = _skill_primary if _skill_primary.exists() else _skill_fallback


# ---------------------------------------------------------------------------
# AST helpers — kept self-contained per Phase 1+2+3+4 precedent.
# ---------------------------------------------------------------------------


def _parse_or_skip(path: Path, label: str) -> ast.Module:
    if not path.exists():
        pytest.skip(f"{label} not present yet ({path.relative_to(REPO_ROOT)})")
    return ast.parse(path.read_text(), filename=str(path))


def _find_assign(tree: ast.Module, name: str) -> ast.AST | None:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == name
                and node.value is not None
            ):
                return node.value
    return None


def _find_funcdef(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _find_imports(tree: ast.Module) -> set[str]:
    """Return the set of top-level module names imported in the tree."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _find_all_names(tree: ast.Module) -> set[str]:
    """Return all Name node ids in the tree (for 'no X import' checks)."""
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


# ---------------------------------------------------------------------------
# Group A — lock.py invariants
# ---------------------------------------------------------------------------


def test_lock_list_by_prefix_defined() -> None:
    """lock_list_by_prefix must be a module-level function in lock.py.

    Plan 05-01 / 05-03 — pure op for the lock-list verb.
    """
    tree = _parse_or_skip(LOCK_PY, "lock.py")
    assert _find_funcdef(tree, "lock_list_by_prefix") is not None, (
        "lock_list_by_prefix not defined in lock.py (Plan 05-01 pure op)"
    )


def test_lock_list_by_prefix_no_typer() -> None:
    """D-17 carry: lock.py must not import typer after Phase 5 additions.

    lock.py is the pure-ops module. Adding lock_list_by_prefix in Phase 5
    must not introduce a typer import. Verb wiring belongs in state/__init__.py.
    """
    tree = _parse_or_skip(LOCK_PY, "lock.py")
    imports = _find_imports(tree)
    assert "typer" not in imports, (
        "lock.py imports typer — D-17 purity contract broken; "
        "verb wiring belongs in state/__init__.py"
    )


def test_lock_list_by_prefix_params() -> None:
    """lock_list_by_prefix must accept 'mine' and 'stale' parameters.

    These filter parameters are the lock-list verb's --mine and --stale flags.
    """
    tree = _parse_or_skip(LOCK_PY, "lock.py")
    funcdef = _find_funcdef(tree, "lock_list_by_prefix")
    assert funcdef is not None, "lock_list_by_prefix not found in lock.py"
    arg_names = [arg.arg for arg in funcdef.args.args]
    assert "mine" in arg_names, (
        f"lock_list_by_prefix is missing 'mine' parameter; found: {arg_names}"
    )
    assert "stale" in arg_names, (
        f"lock_list_by_prefix is missing 'stale' parameter; found: {arg_names}"
    )


# ---------------------------------------------------------------------------
# Group B — claim.py invariants
# ---------------------------------------------------------------------------


def test_claim_list_by_prefix_defined() -> None:
    """claim_list_by_prefix must be a module-level function in claim.py.

    Plan 05-02 / 05-03 — pure op for the claim-list verb.
    """
    tree = _parse_or_skip(CLAIM_PY, "claim.py")
    assert _find_funcdef(tree, "claim_list_by_prefix") is not None, (
        "claim_list_by_prefix not defined in claim.py (Plan 05-02 pure op)"
    )


def test_claim_list_by_prefix_no_typer() -> None:
    """D-17 carry: claim.py must not import typer after Phase 5 additions.

    claim.py is the pure-ops module. Adding claim_list_by_prefix in Phase 5
    must not introduce a typer import. Verb wiring belongs in state/__init__.py.
    """
    tree = _parse_or_skip(CLAIM_PY, "claim.py")
    imports = _find_imports(tree)
    assert "typer" not in imports, (
        "claim.py imports typer — D-17 purity contract broken; "
        "verb wiring belongs in state/__init__.py"
    )


def test_claim_list_by_prefix_params() -> None:
    """claim_list_by_prefix must accept 'mine', 'active', and 'stale' parameters.

    These filter parameters are the claim-list verb's --mine, --active, --stale flags.
    """
    tree = _parse_or_skip(CLAIM_PY, "claim.py")
    funcdef = _find_funcdef(tree, "claim_list_by_prefix")
    assert funcdef is not None, "claim_list_by_prefix not found in claim.py"
    arg_names = [arg.arg for arg in funcdef.args.args]
    assert "mine" in arg_names, (
        f"claim_list_by_prefix is missing 'mine' parameter; found: {arg_names}"
    )
    assert "active" in arg_names, (
        f"claim_list_by_prefix is missing 'active' parameter; found: {arg_names}"
    )
    assert "stale" in arg_names, (
        f"claim_list_by_prefix is missing 'stale' parameter; found: {arg_names}"
    )


# ---------------------------------------------------------------------------
# Group C — state/__init__.py verb count
# ---------------------------------------------------------------------------


def test_state_init_registers_list_verbs() -> None:
    """state/__init__.py must have >= 11 @state_app.command() verbs.

    Phase 4 registered 9 verbs (get/set/del/list/lock/unlock/claim/release/check).
    Phase 5 adds lock-list and claim-list = 11 total.
    """
    tree = _parse_or_skip(STATE_INIT, "state/__init__.py")
    command_calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                unparsed = ast.unparse(dec)
                if "state_app.command" in unparsed:
                    command_calls.append(unparsed)
    assert len(command_calls) >= 11, (
        f"Expected >= 11 @state_app.command() verbs "
        f"(get/set/del/list/lock/unlock/claim/release/check/lock-list/claim-list), "
        f"found {len(command_calls)}: {command_calls}"
    )


# ---------------------------------------------------------------------------
# Group D — SKILL.md write-boundary audit (SC#3)
# ---------------------------------------------------------------------------


def test_skill_file_exists() -> None:
    """The em-global-state SKILL.md must exist at the expected path.

    Primary path:  ~/.claude/skills/em-global-state/SKILL.md
    Fallback path: <repo>/.claude/skills/em-global-state/SKILL.md

    Uses xfail (not skip) if neither exists — absence must be VISIBLE in CI output
    (T-5-05-02 mitigation).
    """
    if not SKILL_PATH.exists():
        pytest.xfail(
            "em-global-state SKILL.md not found at ~/.claude/skills/ or .claude/skills/ "
            "— skill may not be installed on this machine"
        )
    assert SKILL_PATH.is_file(), f"SKILL_PATH exists but is not a file: {SKILL_PATH}"


def test_skill_write_boundary_no_forbidden_verbs() -> None:
    """Skill body must NOT contain forbidden write verb strings (SC#3 audit).

    Forbidden patterns (write verbs that must not appear in the skill surface):
      "em-proj state set"    — KV write (set key)
      "em-proj state del"    — KV write (delete key)
      "em-proj state claim " — claim-acquire (trailing space distinguishes from "claim-list")
      "em-proj state lock "  — lock-acquire (trailing space distinguishes from "lock-list")

    Note: comments in SKILL.md are part of the skill body — forbidden strings
    must be absent even in comments (T-5-05-01 rationale: the skill is a markdown
    document; source-text grep is the correct audit mechanism).
    """
    if not SKILL_PATH.exists():
        pytest.xfail(
            "em-global-state SKILL.md not found — cannot audit write boundary"
        )
    source = SKILL_PATH.read_text()

    forbidden_patterns = [
        "em-proj state set",
        "em-proj state del",
        "em-proj state claim ",
        "em-proj state lock ",
    ]
    for pattern in forbidden_patterns:
        assert pattern not in source, (
            f"Forbidden write verb pattern found in SKILL.md: {pattern!r} — "
            "SC#3 write-boundary violated; skill must not expose KV write or "
            "lock/claim acquisition verbs"
        )


def test_skill_write_boundary_has_permitted_verbs() -> None:
    """Skill body MUST contain the permitted write escape-hatch verbs (SC#3 audit).

    Permitted write verbs (escape hatches only):
      "em-proj state unlock"  — releases an advisory lock
      "em-proj state release" — releases a long-lived claim

    Both must be present so the skill is usable as an emergency escape hatch.
    """
    if not SKILL_PATH.exists():
        pytest.xfail(
            "em-global-state SKILL.md not found — cannot audit permitted verbs"
        )
    source = SKILL_PATH.read_text()

    assert "em-proj state unlock" in source, (
        "'em-proj state unlock' not found in SKILL.md — "
        "SC#3: escape hatch verb must be present"
    )
    assert "em-proj state release" in source, (
        "'em-proj state release' not found in SKILL.md — "
        "SC#3: escape hatch verb must be present"
    )


def test_skill_confirmation_mechanism() -> None:
    """Skill body must contain 'AskUserQuestion' (confirmation mechanism per D-04).

    Write verbs (unlock, release) require human confirmation via AskUserQuestion
    unless --force is passed. Its presence in the skill source is a structural
    invariant — removing it silently removes the confirmation gate.
    """
    if not SKILL_PATH.exists():
        pytest.xfail(
            "em-global-state SKILL.md not found — cannot audit confirmation mechanism"
        )
    source = SKILL_PATH.read_text()

    assert "AskUserQuestion" in source, (
        "'AskUserQuestion' not found in SKILL.md — "
        "D-04 confirmation mechanism absent from skill surface"
    )


# ---------------------------------------------------------------------------
# Group E — Phase 5 SUMMARY coverage
# ---------------------------------------------------------------------------


def test_phase_05_summaries_present() -> None:
    """Every 05-NN-PLAN.md in phases/05-global-state-skill-surface/ must have a SUMMARY.md.

    Same pattern as test_phase_04_summaries_present in test_phase_04_shape.py.
    Structural test analog of verify-phase.sh SUMMARY coverage check.
    Skips if PHASE_DIR doesn't exist (planning worktree not attached).
    """
    if not PHASE_DIR.exists():
        pytest.skip(
            f"{PHASE_DIR.relative_to(REPO_ROOT)} not present — planning worktree "
            "may not be attached on this checkout"
        )
    plans = sorted(PHASE_DIR.glob("05-*-PLAN.md"))
    if not plans:
        pytest.skip(f"no 05-*-PLAN.md files yet under {PHASE_DIR.relative_to(REPO_ROOT)}")
    for plan in plans:
        # e.g. 05-01-PLAN.md → 05-01-SUMMARY.md
        summary = plan.parent / plan.name.replace("-PLAN.md", "-SUMMARY.md")
        assert summary.exists(), (
            f"Missing SUMMARY for {plan.name}: expected {summary.name} "
            f"in {PHASE_DIR.relative_to(REPO_ROOT)}"
        )
