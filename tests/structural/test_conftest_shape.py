"""Structural tests for the Phase 1 harness (Plan 01-04 deliverables).

AST + source-text inspection that encodes Plan 04's normative source-text
acceptance criteria as pytest assertions. Consolidates ~15 grep/wc/test
invocations into one allowlisted dispatcher call (`bash scripts/test.sh all`)
and survives as a regression gate after Plan 04 lands.

If the Plan 04 files do not yet exist, the relevant tests skip cleanly so this
module is safe to add before Plan 04 executes.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFTEST = REPO_ROOT / "tests" / "conftest.py"
HARNESS_TEST = REPO_ROOT / "tests" / "multiprocess" / "test_harness_self.py"


# ---------------------------------------------------------------------------
# Fixtures: read + parse target files once per module; skip if absent.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def conftest_source() -> str:
    if not CONFTEST.exists():
        pytest.skip(f"{CONFTEST.relative_to(REPO_ROOT)} not present yet (Plan 04 deliverable)")
    return CONFTEST.read_text()


@pytest.fixture(scope="module")
def conftest_tree(conftest_source: str) -> ast.Module:
    return ast.parse(conftest_source, filename=str(CONFTEST))


@pytest.fixture(scope="module")
def harness_test_source() -> str:
    if not HARNESS_TEST.exists():
        pytest.skip(f"{HARNESS_TEST.relative_to(REPO_ROOT)} not present yet (Plan 04 deliverable)")
    return HARNESS_TEST.read_text()


# ---------------------------------------------------------------------------
# AST helpers.
# ---------------------------------------------------------------------------


def _find_assign(tree: ast.Module, name: str) -> ast.AST | None:
    """Locate top-level `name = ...` or `name: T = ...`; return the value node or None."""
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


def _fixture_scope(func: ast.FunctionDef) -> str:
    """Pytest fixture scope. 'function' is the default when @pytest.fixture lacks scope kwarg."""
    for dec in func.decorator_list:
        if isinstance(dec, ast.Call):
            if (
                isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "pytest"
                and dec.func.attr == "fixture"
            ):
                for kw in dec.keywords:
                    if kw.arg == "scope" and isinstance(kw.value, ast.Constant):
                        return str(kw.value.value)
                return "function"
        elif isinstance(dec, ast.Attribute):
            if (
                isinstance(dec.value, ast.Name)
                and dec.value.id == "pytest"
                and dec.attr == "fixture"
            ):
                return "function"
    return ""


def _class_has_decorator(cls: ast.ClassDef, name: str) -> bool:
    for dec in cls.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == name:
            return True
        if isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name) and dec.func.id == name:
                return True
    return False


# ---------------------------------------------------------------------------
# Conftest: top-level constants.
# ---------------------------------------------------------------------------


def test_test_db_constant_is_15(conftest_tree: ast.Module) -> None:
    """TEST_DB must equal literal 15 (RESEARCH Pitfall #4 — defeats FLUSHDB on prod db=0)."""
    value = _find_assign(conftest_tree, "TEST_DB")
    assert value is not None, "TEST_DB constant missing from conftest.py"
    assert isinstance(value, ast.Constant) and value.value == 15, (
        f"TEST_DB must be the literal 15; got {ast.unparse(value)}"
    )


def test_em_proj_bin_constant(conftest_tree: ast.Module) -> None:
    value = _find_assign(conftest_tree, "EM_PROJ_BIN")
    assert value is not None, "EM_PROJ_BIN constant missing from conftest.py"
    assert isinstance(value, ast.Constant) and value.value == "em-proj", (
        f'EM_PROJ_BIN must equal the literal "em-proj"; got {ast.unparse(value)}'
    )


# ---------------------------------------------------------------------------
# Conftest: RaceResult dataclass.
# ---------------------------------------------------------------------------


def test_race_result_is_dataclass(conftest_tree: ast.Module) -> None:
    """RaceResult must be a @dataclass (D-15 — three assertion surfaces in a frozen record)."""
    for node in conftest_tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "RaceResult":
            assert _class_has_decorator(node, "dataclass"), (
                f"RaceResult must carry @dataclass; got decorators "
                f"{[ast.unparse(d) for d in node.decorator_list]}"
            )
            field_names = {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
            required = {"returncode", "stdout", "stderr", "duration_ms"}
            missing = required - field_names
            assert not missing, f"RaceResult missing fields: {sorted(missing)}"
            return
    pytest.fail("RaceResult class not defined in conftest.py")


# ---------------------------------------------------------------------------
# Conftest: fixtures with correct scopes.
# ---------------------------------------------------------------------------


def test_redis_precheck_is_session_scoped(conftest_tree: ast.Module) -> None:
    func = _find_funcdef(conftest_tree, "redis_precheck")
    assert func is not None, "redis_precheck fixture missing"
    scope = _fixture_scope(func)
    assert scope == "session", (
        f"redis_precheck must be @pytest.fixture(scope='session'); got scope={scope!r}"
    )


def test_clean_db_is_function_scoped(conftest_tree: ast.Module) -> None:
    """D-11 + D-16: function-scoped FLUSHDB autoclean (full isolation > session speedup)."""
    func = _find_funcdef(conftest_tree, "clean_db")
    assert func is not None, "clean_db fixture missing"
    scope = _fixture_scope(func)
    assert scope == "function", (
        f"clean_db must be function-scoped (D-11, D-16 — full isolation); got scope={scope!r}"
    )


def test_multiproc_race_is_function_scoped(conftest_tree: ast.Module) -> None:
    func = _find_funcdef(conftest_tree, "multiproc_race")
    assert func is not None, "multiproc_race fixture missing"
    scope = _fixture_scope(func)
    assert scope == "function", (
        f"multiproc_race must be function-scoped; got scope={scope!r}"
    )


# ---------------------------------------------------------------------------
# Conftest: locked design choices (Pitfalls #2, #4, #6).
# AST-based — checks REAL code, not docstring/comment mentions.
# ---------------------------------------------------------------------------


def _iter_imports(tree: ast.Module) -> list[str]:
    """Return all imported module names (both `import x` and `from x import ...`)."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _iter_attribute_chains(tree: ast.Module) -> list[str]:
    """Return dotted attribute chains used in code, e.g. 'subprocess.Popen', 'os.environ'."""
    chains: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            parts = []
            cur: ast.AST = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
                chains.append(".".join(reversed(parts)))
    return chains


def _iter_method_calls(tree: ast.Module, method_name: str) -> list[ast.Call]:
    """Return all Call nodes whose func is `<anything>.<method_name>`."""
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == method_name
        ):
            calls.append(node)
    return calls


def test_uses_subprocess_popen(conftest_tree: ast.Module) -> None:
    chains = _iter_attribute_chains(conftest_tree)
    assert any(c.endswith("subprocess.Popen") for c in chains), (
        "conftest must use subprocess.Popen (fork+exec — macOS fork-safe per Pitfall #6)"
    )


def test_does_not_import_multiprocessing(conftest_tree: ast.Module) -> None:
    """RESEARCH Pitfall #6 + threat T-01-04-05 — macOS fork-safety; locked-in choice."""
    imports = _iter_imports(conftest_tree)
    bad = [name for name in imports if name == "multiprocessing" or name.startswith("multiprocessing.")]
    assert not bad, (
        f"conftest must NOT import multiprocessing (Pitfall #6: macOS fork+spawn unsafety). "
        f"Found imports: {bad}. Use subprocess.Popen + fork+exec instead. See PLAN 01-04 threat T-01-04-05."
    )

    # Also check no `multiprocessing.X` attribute access (in case it sneaks in via globals).
    chains = _iter_attribute_chains(conftest_tree)
    bad_chains = [c for c in chains if c == "multiprocessing" or c.startswith("multiprocessing.")]
    assert not bad_chains, (
        f"conftest must NOT reference multiprocessing in code (only docstring mentions allowed). "
        f"Found: {bad_chains}"
    )


def test_uses_communicate_with_timeout(conftest_tree: ast.Module) -> None:
    """RESEARCH Pitfall #2: .wait() deadlocks on 64KB pipe buffer; use .communicate(timeout=)."""
    communicate_calls = _iter_method_calls(conftest_tree, "communicate")
    with_timeout = [
        c for c in communicate_calls
        if any(kw.arg == "timeout" for kw in c.keywords)
    ]
    assert with_timeout, (
        "conftest must call .communicate(timeout=...) at least once (Pitfall #2 — "
        "bare .wait() deadlocks when child stdout/stderr fills the 64KB pipe buffer)"
    )


def test_no_bare_wait_calls(conftest_tree: ast.Module) -> None:
    """No `<x>.wait()` in code — only `.communicate(timeout=)` (Pitfall #2)."""
    wait_calls = _iter_method_calls(conftest_tree, "wait")
    bare = [c for c in wait_calls if not c.args and not c.keywords]
    assert not bare, (
        f"conftest contains {len(bare)} bare .wait() call(s) in code; "
        f"use .communicate(timeout=...) instead (Pitfall #2). "
        f"Located at lines: {[c.lineno for c in bare]}"
    )


def test_injects_em_proj_redis_db_env(conftest_source: str) -> None:
    """RESEARCH Pitfall #4: children must target db=15 via EM_PROJ_REDIS_DB env injection."""
    assert "EM_PROJ_REDIS_DB" in conftest_source, (
        "conftest must inject EM_PROJ_REDIS_DB into child env (Pitfall #4 — "
        "without it children connect to db=0 and FLUSHDB wipes prod state)"
    )


def test_validates_argv_shape(conftest_source: str) -> None:
    """T-01-04-01 shell-injection mitigation: argv must be list[list[str]]."""
    has_str_check = "isinstance(arg, str)" in conftest_source
    has_list_check = "isinstance(c, list)" in conftest_source or "isinstance(commands, list)" in conftest_source
    assert has_str_check and has_list_check, (
        "conftest must validate argv shape at the fixture entry "
        "(T-01-04-01 shell-injection mitigation — Popen with list[str] does not invoke a shell)"
    )


def test_conftest_at_least_60_lines(conftest_source: str) -> None:
    line_count = conftest_source.count("\n")
    assert line_count >= 60, (
        f"conftest.py must be >= 60 lines (Plan 04 min_lines: 60); got {line_count}"
    )


# ---------------------------------------------------------------------------
# Harness self-tests: required test names (VALIDATION.md-named contract).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "test_harness_runs_em_proj_at_cli_boundary",
        "test_race_launches_in_parallel_not_sequence",
        "test_redis_state_isolation_per_test_setup",
        "test_redis_state_isolation_per_test_verify",
        "test_db_15_not_db_0_safety_net",
    ],
)
def test_harness_self_defines_named_test(harness_test_source: str, name: str) -> None:
    """The plan's success_criteria + VALIDATION.md reference these tests by exact name."""
    assert re.search(rf"^def {re.escape(name)}\b", harness_test_source, re.MULTILINE), (
        f"harness self-tests must define {name!r} "
        f"(VALIDATION.md per-task verification refers to this exact name)"
    )
