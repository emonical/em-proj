"""Phase 4 structural invariants — AST-based shape assertions.

Encodes plan acceptance criteria as runtime assertions for Phase 4
(Long-lived Claims):

  - D-17 carry — claim.py is pure: no typer import, no multiprocessing import
  - D-18 carry — claim.py has no redis.Redis() constructor call (uses get_client() only)
  - CLAIM-01 constants — TTL_DEFAULT = 1800, KEY_PREFIX = "state:claim:" present
  - CLAIM-01 Lua — all 3 Lua scripts present as module-level string constants
      (LUA_CLAIM_REFRESH_OR_TAKE, LUA_CLAIM_COMPARE_AND_DELETE, LUA_CLAIM_CHECK)
  - CLAIM-01 public ops — claim_take, claim_release, claim_check defined in claim.py
  - CLAIM-02 exceptions — HeldByAnother and ClaimNotHeld defined in claim.py
  - CLAIM-03 anonymous gate — state/__init__.py claim verb body contains
      the string "anonymous claims refused" (literal grep on source)
  - D-14 extended — state/__init__.py registers >= 9 verbs total
      (get + set + del + list + lock + unlock + claim + release + check = 9)
  - Phase 4 PLAN presence — every 04-NN-PLAN.md in phases/04-long-lived-claims/ has a
      corresponding 04-NN-SUMMARY.md (SUMMARY inventory check)

Mirrors the Phase 3 precedent in ``tests/structural/test_phase_03_shape.py``
and the Phase 2 precedent in ``tests/structural/test_phase_02_shape.py``.
Skips cleanly if a target file is absent so this module is safe to land
before its dependencies.

Each structural file is self-contained (helper functions copied, NOT shared
via a common module) per Phase 1+2+3 precedent — the rule "each structural
file is self-contained" was documented in Plan 02-05 SUMMARY.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_EM_PROJ = REPO_ROOT / "src" / "em_proj"
STATE_PKG = SRC_EM_PROJ / "state"
CLAIM_PY = STATE_PKG / "claim.py"
STATE_INIT = STATE_PKG / "__init__.py"
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "04-long-lived-claims"


# ---------------------------------------------------------------------------
# AST helpers — kept self-contained per Phase 1+2+3 precedent.
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
# D-17 carry: claim.py is pure — no typer import
# ---------------------------------------------------------------------------


def test_claim_py_no_typer_import() -> None:
    """D-17 carry: claim.py must not import typer.

    claim.py is the pure-ops module for long-lived area claims. Importing typer
    would couple it to the CLI framework. Verb wiring belongs in state/__init__.py.
    """
    tree = _parse_or_skip(CLAIM_PY, "claim.py")
    imports = _find_imports(tree)
    assert "typer" not in imports, (
        "claim.py imports typer — D-17 purity contract broken; "
        "verb wiring belongs in state/__init__.py"
    )


# ---------------------------------------------------------------------------
# D-17 carry: claim.py is pure — no multiprocessing import
# ---------------------------------------------------------------------------


def test_claim_py_no_multiprocessing_import() -> None:
    """Phase 1 pitfall #6 carry: claim.py must not import multiprocessing.

    claim.py has no blocking poll, no RefresherThread, no --hold mode.
    multiprocessing.Process with the fork start-method crashes intermittently
    on macOS. No concurrency primitives needed here.
    """
    tree = _parse_or_skip(CLAIM_PY, "claim.py")
    imports = _find_imports(tree)
    assert "multiprocessing" not in imports, (
        "claim.py imports multiprocessing — Phase 1 pitfall #6 carry; "
        "claim.py must have no concurrency imports"
    )


# ---------------------------------------------------------------------------
# D-18 carry: claim.py has no redis.Redis() constructor call
# ---------------------------------------------------------------------------


def test_claim_py_no_redis_constructor() -> None:
    """D-18 carry: claim.py must not construct redis.Redis() directly.

    All Redis handles come from em_proj.redis_client.get_client() — the single
    chokepoint. Direct redis.Redis() construction outside redis_client.py violates D-18.
    """
    source = CLAIM_PY.read_text()
    assert "redis.Redis(" not in source, (
        "claim.py must use get_client() not redis.Redis() — D-18 single-chokepoint contract"
    )


# ---------------------------------------------------------------------------
# CLAIM-01 constants: TTL_DEFAULT = 1800
# ---------------------------------------------------------------------------


def test_claim_py_ttl_default_is_1800() -> None:
    """CLAIM-01: TTL_DEFAULT must equal 1800 (30 minutes) in claim.py."""
    tree = _parse_or_skip(CLAIM_PY, "claim.py")
    node = _find_assign(tree, "TTL_DEFAULT")
    assert node is not None, "TTL_DEFAULT missing from claim.py (CLAIM-01)"
    assert isinstance(node, ast.Constant) and node.value == 1800, (
        f"TTL_DEFAULT must equal 1800; got {ast.unparse(node)}"
    )


# ---------------------------------------------------------------------------
# CLAIM-01 constants: KEY_PREFIX = "state:claim:"
# ---------------------------------------------------------------------------


def test_claim_py_key_prefix() -> None:
    """CLAIM-01: KEY_PREFIX must equal \"state:claim:\" in claim.py."""
    tree = _parse_or_skip(CLAIM_PY, "claim.py")
    node = _find_assign(tree, "KEY_PREFIX")
    assert node is not None, "KEY_PREFIX missing from claim.py (CLAIM-01)"
    assert isinstance(node, ast.Constant) and node.value == "state:claim:", (
        f"KEY_PREFIX must equal 'state:claim:'; got {ast.unparse(node)}"
    )


# ---------------------------------------------------------------------------
# CLAIM-01 Lua scripts: all 3 present as module-level string constants
# ---------------------------------------------------------------------------


def test_claim_py_lua_scripts_present() -> None:
    """CLAIM-01: all 3 Lua scripts must be present as module-level string constants.

    LUA_CLAIM_REFRESH_OR_TAKE  — atomic take-or-refresh (T-4-01-02)
    LUA_CLAIM_COMPARE_AND_DELETE — release guarded by session_id+project_hash (T-4-01-03)
    LUA_CLAIM_CHECK             — atomic EXISTS+HGETALL (no TOCTOU for check path)
    """
    tree = _parse_or_skip(CLAIM_PY, "claim.py")
    for name in ("LUA_CLAIM_REFRESH_OR_TAKE", "LUA_CLAIM_COMPARE_AND_DELETE", "LUA_CLAIM_CHECK"):
        node = _find_assign(tree, name)
        assert node is not None, f"{name} missing from claim.py (CLAIM-01 Lua scripts)"
        assert isinstance(node, ast.Constant) and isinstance(node.value, str), (
            f"{name} must be a string constant in claim.py"
        )


# ---------------------------------------------------------------------------
# CLAIM-01 public ops: claim_take, claim_release, claim_check defined
# ---------------------------------------------------------------------------


def test_claim_py_public_ops_exported() -> None:
    """CLAIM-01: claim_take, claim_release, claim_check must be module-level functions."""
    tree = _parse_or_skip(CLAIM_PY, "claim.py")
    for name in ("claim_take", "claim_release", "claim_check"):
        assert _find_funcdef(tree, name) is not None, (
            f"{name} not defined in claim.py (CLAIM-01 public ops)"
        )


# ---------------------------------------------------------------------------
# CLAIM-02 exceptions: HeldByAnother and ClaimNotHeld defined
# ---------------------------------------------------------------------------


def test_claim_py_exceptions_exported() -> None:
    """CLAIM-02: HeldByAnother and ClaimNotHeld must be class definitions in claim.py."""
    tree = _parse_or_skip(CLAIM_PY, "claim.py")
    class_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }
    assert "HeldByAnother" in class_names, (
        "HeldByAnother not defined in claim.py (CLAIM-02 exceptions)"
    )
    assert "ClaimNotHeld" in class_names, (
        "ClaimNotHeld not defined in claim.py (CLAIM-02 exceptions)"
    )


# ---------------------------------------------------------------------------
# D-14 extended: state/__init__.py registers >= 9 verbs total
# ---------------------------------------------------------------------------


def test_state_init_registers_claim_verbs() -> None:
    """D-14 extended: state_app must register at least 9 verbs.

    Phase 2 pinned 4 KV verbs (get/set/del/list).
    Phase 3 added lock + unlock = 6 total.
    Phase 4 adds claim + release + check = 9 total.

    Counts @state_app.command(...) decorated functions via AST decorator inspection.
    """
    tree = _parse_or_skip(STATE_INIT, "state/__init__.py")
    command_calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                unparsed = ast.unparse(dec)
                if "state_app.command" in unparsed:
                    command_calls.append(unparsed)
    assert len(command_calls) >= 9, (
        f"Expected >= 9 @state_app.command() verbs (get/set/del/list/lock/unlock/claim/release/check), "
        f"found {len(command_calls)}: {command_calls}"
    )


# ---------------------------------------------------------------------------
# CLAIM-03 anonymous gate: state/__init__.py contains the refusal message
# ---------------------------------------------------------------------------


def test_state_init_anonymous_gate_present() -> None:
    """CLAIM-03: state/__init__.py claim verb must refuse anonymous claims.

    The anonymous-refusal gate (CLAIM-03 / T-4-02-01) fires before any Redis
    call. The literal message "anonymous claims refused" must appear in the
    source so the multi-process test can assert on stderr content.
    """
    source = STATE_INIT.read_text()
    assert "anonymous claims refused" in source, (
        "CLAIM-03 anonymous-refusal message 'anonymous claims refused' not found "
        "in state/__init__.py — T-4-02-01 mitigation missing"
    )


# ---------------------------------------------------------------------------
# Phase 4 SUMMARY coverage
# ---------------------------------------------------------------------------


def test_phase_04_summaries_present() -> None:
    """Every 04-NN-PLAN.md in phases/04-long-lived-claims/ must have a 04-NN-SUMMARY.md.

    This is the structural test analog of verify-phase.sh's SUMMARY coverage check.
    Encoded here so one `bash scripts/test.sh structural` call covers it.
    """
    if not PHASE_DIR.exists():
        pytest.skip(
            f"{PHASE_DIR.relative_to(REPO_ROOT)} not present — planning worktree "
            "may not be attached on this checkout"
        )
    plans = sorted(PHASE_DIR.glob("04-*-PLAN.md"))
    if not plans:
        pytest.skip(f"no 04-*-PLAN.md files yet under {PHASE_DIR.relative_to(REPO_ROOT)}")
    for plan in plans:
        # e.g. 04-01-PLAN.md → 04-01-SUMMARY.md
        summary = plan.parent / plan.name.replace("-PLAN.md", "-SUMMARY.md")
        assert summary.exists(), (
            f"Missing SUMMARY for {plan.name}: expected {summary.name} "
            f"in {PHASE_DIR.relative_to(REPO_ROOT)}"
        )
