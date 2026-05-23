"""Phase 2 structural invariants — AST-based shape assertions.

Encodes plan acceptance criteria as runtime assertions for:
  - D-14 — `state_app` mounted under root typer app via `add_typer`
  - D-15 — `em_proj/output.py` exports the locked envelope helpers
  - D-17 — `em_proj/state/` package layout (no typer import in kv.py)
  - D-18 — single redis_client chokepoint tree-wide
  - D-19 — verb-level no-catch of redis errors

Plus the Decision Coverage Gate: a pure-Python scan over all
`02-*-PLAN.md` files asserting that every D-01..D-19 is cited in at least
one plan's body. Encoding this as a pytest test (rather than a shell
`grep | sort | wc -l` pipeline) means one allowlisted
`bash scripts/test.sh structural` call covers it — the no-pipe project
convention is preserved at the verifier layer.

Mirrors the Phase 1 precedent in `tests/structural/test_conftest_shape.py`.
Skips cleanly if a target file is absent so this module is safe to land
before its dependencies.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_EM_PROJ = REPO_ROOT / "src" / "em_proj"
OUTPUT_PY = SRC_EM_PROJ / "output.py"
STATE_PKG = SRC_EM_PROJ / "state"
STATE_INIT = STATE_PKG / "__init__.py"
STATE_KV = STATE_PKG / "kv.py"
CLI_PY = SRC_EM_PROJ / "cli.py"
REDIS_CLIENT = SRC_EM_PROJ / "redis_client.py"
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "02-cli-shell-kv-primitive"


# ---------------------------------------------------------------------------
# AST helpers — kept self-contained per Phase 1 precedent.
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


def _iter_imports(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _iter_attribute_chains(tree: ast.Module) -> list[tuple[str, int]]:
    chains: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            parts: list[str] = []
            cur: ast.AST = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
                chains.append((".".join(reversed(parts)), node.lineno))
    return chains


# ---------------------------------------------------------------------------
# Fixtures — read+parse each target file once per module.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def output_tree() -> ast.Module:
    return _parse_or_skip(OUTPUT_PY, "em_proj/output.py (Plan 02-02 deliverable)")


@pytest.fixture(scope="module")
def state_init_tree() -> ast.Module:
    return _parse_or_skip(STATE_INIT, "em_proj/state/__init__.py")


@pytest.fixture(scope="module")
def state_kv_tree() -> ast.Module:
    return _parse_or_skip(STATE_KV, "em_proj/state/kv.py (Plan 02-03 deliverable)")


@pytest.fixture(scope="module")
def cli_tree() -> ast.Module:
    return _parse_or_skip(CLI_PY, "em_proj/cli.py")


# ---------------------------------------------------------------------------
# D-15 — em_proj/output.py exports
# ---------------------------------------------------------------------------


def test_output_py_exists_and_parseable(output_tree: ast.Module) -> None:
    assert isinstance(output_tree, ast.Module)


def test_output_py_exports_schema_version_constant(output_tree: ast.Module) -> None:
    """D-15: SCHEMA_VERSION must equal the literal string '1' at module top level."""
    value = _find_assign(output_tree, "SCHEMA_VERSION")
    assert value is not None, "SCHEMA_VERSION missing from output.py"
    assert isinstance(value, ast.Constant) and value.value == "1", (
        f'SCHEMA_VERSION must be the literal "1"; got {ast.unparse(value)}'
    )


def test_output_py_exports_resolve_json_mode(output_tree: ast.Module) -> None:
    """D-15: resolve_json_mode is the single point of TTY detection."""
    func = _find_funcdef(output_tree, "resolve_json_mode")
    assert func is not None, "resolve_json_mode missing from output.py"


def test_output_py_exports_three_emit_helpers(output_tree: ast.Module) -> None:
    """D-15: emit_ok / emit_not_found / emit_error are the locked emit surface."""
    for name in ("emit_ok", "emit_not_found", "emit_error"):
        assert _find_funcdef(output_tree, name) is not None, (
            f"{name} missing from output.py — D-15 helper export contract broken"
        )


def test_output_py_no_typer_or_redis_imports(output_tree: ast.Module) -> None:
    """D-15: output.py is the dependency-free envelope module.

    No typer (would couple JSON emission to the CLI framework).
    No redis or em_proj.redis_client (would couple envelope to backend availability).
    """
    imports = _iter_imports(output_tree)
    forbidden = {"typer", "redis", "em_proj.redis_client"}
    leaked = [name for name in imports if name in forbidden or any(
        name.startswith(f"{prefix}.") for prefix in forbidden
    )]
    assert not leaked, (
        f"output.py imports forbidden modules (breaks D-15 dependency-free contract): {leaked}"
    )


# ---------------------------------------------------------------------------
# D-17 — em_proj/state/ package layout
# ---------------------------------------------------------------------------


def test_state_package_has_init_and_kv_files() -> None:
    """D-17: state is a package; kv.py holds the pure ops as a sibling of __init__.py."""
    assert STATE_INIT.exists(), f"missing {STATE_INIT.relative_to(REPO_ROOT)}"
    assert STATE_KV.exists(), f"missing {STATE_KV.relative_to(REPO_ROOT)}"


def test_state_init_defines_state_app_typer(state_init_tree: ast.Module) -> None:
    """D-14: state/__init__.py defines `state_app = typer.Typer(...)` at module top level."""
    value = _find_assign(state_init_tree, "state_app")
    assert value is not None, "state_app assignment missing"
    assert isinstance(value, ast.Call), (
        f"state_app must be assigned from a call (typer.Typer(...)); got {ast.unparse(value)}"
    )
    # The call's func should resolve to typer.Typer.
    func_src = ast.unparse(value.func)
    assert func_src == "typer.Typer", (
        f"state_app must be a typer.Typer instance; got {func_src}"
    )


def test_state_init_registers_four_verbs(state_init_tree: ast.Module) -> None:
    """D-14: exactly four `@state_app.command(...)` decorations naming get/set/del/list."""
    expected = {"get", "set", "del", "list"}
    seen: set[str] = set()
    for node in ast.walk(state_init_tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            if not (
                isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "state_app"
                and dec.func.attr == "command"
            ):
                continue
            # Verb name: first positional arg, OR `name=` keyword.
            verb_name: str | None = None
            if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                verb_name = dec.args[0].value
            else:
                for kw in dec.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        verb_name = kw.value.value
                        break
            if verb_name is not None:
                seen.add(verb_name)
    missing = expected - seen
    assert not missing, (
        f"state_app missing verbs: {sorted(missing)}; got {sorted(seen)}. "
        f"D-14 requires get/set/del/list."
    )


def test_kv_py_does_not_import_typer(state_kv_tree: ast.Module) -> None:
    """D-17: kv.py is the pure ops module — typer must not appear in its imports."""
    imports = _iter_imports(state_kv_tree)
    bad = [name for name in imports if name == "typer" or name.startswith("typer.")]
    assert not bad, (
        f"kv.py imports typer (breaks D-17 — verb wiring belongs in __init__.py): {bad}"
    )


def test_kv_py_uses_redis_client_chokepoint(state_kv_tree: ast.Module) -> None:
    """D-18: kv.py reaches Redis ONLY through em_proj.redis_client.get_client."""
    imports = _iter_imports(state_kv_tree)
    via_redis_client = any(
        name == "em_proj.redis_client" or name.startswith("em_proj.redis_client.")
        for name in imports
    )
    assert via_redis_client, (
        "kv.py must import from em_proj.redis_client (D-18 single chokepoint)"
    )


# ---------------------------------------------------------------------------
# D-18 — Single chokepoint tree-wide
# ---------------------------------------------------------------------------


def test_no_direct_redis_redis_construction_outside_chokepoint() -> None:
    """D-18: only `em_proj/redis_client.py` may construct `redis.Redis(...)` directly.

    Walks every `.py` under `src/em_proj/` and asserts no `redis.Redis` Call
    appears anywhere else. Tests live outside `src/em_proj/` and are unaffected;
    they may construct clients freely for assertion purposes.
    """
    offenders: list[str] = []
    for py in SRC_EM_PROJ.rglob("*.py"):
        if py.resolve() == REDIS_CLIENT.resolve():
            continue
        tree = ast.parse(py.read_text(), filename=str(py))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_src = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
            if func_src == "redis.Redis":
                offenders.append(f"{py.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, (
        "D-18 chokepoint violated — direct `redis.Redis(...)` construction outside "
        f"redis_client.py at:\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# D-19 — Verb-level no-catch invariant
# ---------------------------------------------------------------------------


def test_state_init_does_not_catch_redis_errors(state_init_tree: ast.Module) -> None:
    """D-19: verbs MUST NOT catch redis.ConnectionError / redis.TimeoutError / redis.*Error.

    Connection-error translation belongs to em_proj.redis_client's
    die_if_redis_unreachable — the single chokepoint. Verbs catch only the
    domain exceptions raised by kv.py (KvNotFound, ValidationError).
    """
    offenders: list[str] = []
    for node in ast.walk(state_init_tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            continue
        # Single exception type: `except redis.Foo:`
        types_to_check: list[ast.AST] = []
        if isinstance(node.type, ast.Tuple):
            types_to_check.extend(node.type.elts)
        else:
            types_to_check.append(node.type)
        for t in types_to_check:
            src = ast.unparse(t)
            if src.startswith("redis."):
                offenders.append(f"line {node.lineno}: except {src}")
    assert not offenders, (
        "D-19 violated — state/__init__.py catches a redis exception (verbs must "
        f"defer to the wrapper):\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# D-14 — cli.py mount
# ---------------------------------------------------------------------------


def test_cli_py_mounts_state_app(cli_tree: ast.Module) -> None:
    """D-14: root cli.py must mount state_app via app.add_typer(state_app, name="state", ...)."""
    for node in ast.walk(cli_tree):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "app"
            and node.func.attr == "add_typer"
        ):
            continue
        # First positional arg should be the state_app Name.
        if not (node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "state_app"):
            continue
        # name="state" keyword must be present.
        for kw in node.keywords:
            if (
                kw.arg == "name"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value == "state"
            ):
                return  # passes
    pytest.fail(
        "D-14 mount missing — cli.py must contain app.add_typer(state_app, name=\"state\", ...)"
    )


# ---------------------------------------------------------------------------
# Decision Coverage Gate — every D-01..D-19 cited in at least one Phase 2 plan
# ---------------------------------------------------------------------------


def test_every_decision_id_d01_to_d19_cited_in_at_least_one_plan() -> None:
    """Decision Coverage Gate as a pytest test (replaces shell `grep | sort | wc -l`).

    Reads every `02-*-PLAN.md` under the Phase 2 directory in pure Python, extracts
    every `D-NN` reference (normalizing `D-1` and `D-01` to the same id), and asserts
    the union covers D-01..D-19. A future plan that drops a D-ID without another
    plan picking it up will fail this test under `bash scripts/test.sh structural`.
    """
    if not PHASE_DIR.exists():
        pytest.skip(
            f"{PHASE_DIR.relative_to(REPO_ROOT)} not present — planning worktree "
            "may not be attached on this checkout"
        )
    plan_files = sorted(PHASE_DIR.glob("02-*-PLAN.md"))
    if not plan_files:
        pytest.skip(f"no 02-*-PLAN.md files yet under {PHASE_DIR.relative_to(REPO_ROOT)}")

    cited: set[str] = set()
    for plan in plan_files:
        for match in re.findall(r"D-(\d+)", plan.read_text()):
            cited.add(f"D-{int(match):02d}")

    expected = {f"D-{i:02d}" for i in range(1, 20)}
    missing = expected - cited
    assert not missing, (
        f"Decision Coverage Gate failed — D-IDs not cited in any Phase 2 plan: "
        f"{sorted(missing)}.\n"
        f"Scanned {len(plan_files)} plan file(s); cited D-IDs: {sorted(cited)}."
    )
