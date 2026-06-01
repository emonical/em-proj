"""Phase 3 structural invariants — AST-based shape assertions.

Encodes plan acceptance criteria as runtime assertions for Phase 3
(Identity + Advisory Locks):

  - D-12 — ``em_proj/identity.py`` exists at top-level (not under state/)
  - D-17 carry — identity.py is pure: no typer, no redis_client import
  - D-11 — psutil is importable; referenced in identity.py
  - D-18 tree-wide (extended) — no ``redis.Redis(...)`` construction in lock.py
    or identity.py (lock.py may ``import redis`` for exception-type references;
    construction is the forbidden pattern)
  - D-02 record shape — holder round-trip carries exactly 8 fields
  - D-06 Lua compare-and-delete script exists in lock.py
  - D-10 Lua compare-and-swap-if-stale script exists in lock.py
  - D-07 --warn double-isatty check in state/__init__.py
  - D-08 --warn + --hold mutual exclusion gate in state/__init__.py
  - D-15 carry — emit_held_by_another exported from output.py
  - D-15 carry — output.py remains dependency-free
  - D-17/D-14 carry — lock.py imports validate_key from em_proj.state.kv
  - D-19 carry (narrowed) — lock primitives do not catch redis errors;
    refresher's narrow (ConnectionError, TimeoutError) handler is allowed + required
  - D-17 carry — lock.py does not import typer
  - Phase 1 pitfall #6 carry — lock.py does not import multiprocessing
  - D-14 extended — state/__init__.py registers >= 6 verbs (kv + lock + unlock)
  - Decision Coverage Gate (Phase 3) — every D-01..D-12 cited in at least one plan
  - Blocker #1 pin a — lock_force_displace is exported from em_proj.state.lock
  - Blocker #1 pin b — state/__init__.py does NOT import private symbols from lock.py
  - Blocker #3 pin — RefresherThread.run() catches (redis.ConnectionError, redis.TimeoutError)

Mirrors the Phase 1 precedent in ``tests/structural/test_conftest_shape.py``
and the Phase 2 precedent in ``tests/structural/test_phase_02_shape.py``.
Skips cleanly if a target file is absent so this module is safe to land
before its dependencies.

Each structural file is self-contained (helper functions copied, NOT shared
via a common module) per Phase 1+2 precedent — the rule "each structural file
is self-contained" was documented in Plan 02-05 SUMMARY.
"""
from __future__ import annotations

import ast
import re
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_EM_PROJ = REPO_ROOT / "src" / "em_proj"
IDENTITY_PY = SRC_EM_PROJ / "identity.py"
OUTPUT_PY = SRC_EM_PROJ / "output.py"
STATE_PKG = SRC_EM_PROJ / "state"
STATE_INIT = STATE_PKG / "__init__.py"
STATE_LOCK = STATE_PKG / "lock.py"
REDIS_CLIENT = SRC_EM_PROJ / "redis_client.py"
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "03-identity-advisory-locks"


# ---------------------------------------------------------------------------
# AST helpers — kept self-contained per Phase 1+2 precedent.
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
def identity_tree() -> ast.Module:
    return _parse_or_skip(IDENTITY_PY, "em_proj/identity.py (Plan 03-01 deliverable)")


@pytest.fixture(scope="module")
def lock_tree() -> ast.Module:
    return _parse_or_skip(STATE_LOCK, "em_proj/state/lock.py (Plan 03-03 deliverable)")


@pytest.fixture(scope="module")
def state_init_tree() -> ast.Module:
    return _parse_or_skip(STATE_INIT, "em_proj/state/__init__.py")


@pytest.fixture(scope="module")
def output_tree() -> ast.Module:
    return _parse_or_skip(OUTPUT_PY, "em_proj/output.py")


# ---------------------------------------------------------------------------
# D-12: identity.py at top level
# ---------------------------------------------------------------------------


def test_identity_py_exists_at_top_level(identity_tree: ast.Module) -> None:
    """D-12: em_proj/identity.py must exist at top-level (sibling to redis_client.py).

    Future em-proj session/message subcommands need the same identity primitives;
    nesting under state/ would force a later move (D-12 Claude's discretion).
    """
    assert IDENTITY_PY.exists(), (
        f"identity.py missing at {IDENTITY_PY.relative_to(REPO_ROOT)} — "
        "D-12 requires top-level placement (not under state/)"
    )
    # Assert it is NOT under state/ — a misplaced identity.py would be a false pass
    assert STATE_PKG not in IDENTITY_PY.parents, (
        "identity.py must NOT be under em_proj/state/; it must be a top-level sibling"
    )
    assert isinstance(identity_tree, ast.Module)


# ---------------------------------------------------------------------------
# D-17 carry: identity.py is pure
# ---------------------------------------------------------------------------


def test_identity_py_is_pure_no_typer_no_redis_client(identity_tree: ast.Module) -> None:
    """D-17 carry: identity.py must not import typer or em_proj.redis_client.

    identity.py is the stateless identity primitive shared by lock (Phase 3)
    and claim (Phase 4). Importing typer would couple it to the CLI framework;
    importing redis_client would introduce a Redis dependency that breaks
    unit-testability (no live Redis needed for identity functions).
    """
    imports = _iter_imports(identity_tree)
    forbidden = {"typer", "redis", "em_proj.redis_client"}
    leaked = [name for name in imports if name in forbidden or any(
        name.startswith(f"{prefix}.") for prefix in forbidden
    )]
    assert not leaked, (
        f"identity.py imports forbidden modules (D-17 purity contract broken): {leaked}"
    )


# ---------------------------------------------------------------------------
# D-11: psutil is importable; used in identity.py
# ---------------------------------------------------------------------------


def test_identity_py_imports_psutil(identity_tree: ast.Module) -> None:
    """D-11: psutil (>= 6.0) is a runtime dep; identity.py must use it.

    D-11 adds psutil to the allowed-deps list (redis-py, typer, psutil, pytest).
    psutil.Process(pid).create_time() and psutil.boot_time() are the portable
    cross-platform stale-detection probes (IDENT-02).
    """
    imports = _iter_imports(identity_tree)
    assert "psutil" in imports, (
        "identity.py must import psutil (D-11 — stale-detection probes IDENT-02)"
    )
    # Also confirm psutil is importable at test time (sanity check that dep is installed)
    import psutil  # noqa: F401 — intentional importability check


# ---------------------------------------------------------------------------
# D-18 tree-wide: no redis.Redis(...) outside redis_client.py
# (Phase 2 test extended to cover lock.py and identity.py)
# ---------------------------------------------------------------------------


def test_no_direct_redis_redis_construction_outside_chokepoint_phase3() -> None:
    """D-18 extended: lock.py and identity.py must not construct redis.Redis(...).

    lock.py IS allowed to ``import redis`` for exception-type references in the
    refresher (Blocker #3 resolution — redis.ConnectionError, redis.TimeoutError).
    The forbidden pattern is ``redis.Redis(...)`` (construction call), NOT
    ``import redis`` (the module reference).

    Walks all .py files under src/em_proj/ — same as Phase 2's chokepoint test
    but re-stated here so Phase 3's structural file is standalone and self-evidencing.
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
# D-02 record shape: lock.py defines an 8-field holder
# ---------------------------------------------------------------------------


def test_lock_holder_round_trip_has_eight_fields() -> None:
    """D-02: the holder dict produced by lock.py must have exactly 8 fields.

    Uses the module-private _make_holder and _encode_holder/_decode_holder to
    exercise the round-trip without needing live Redis. NOTE: structural tests
    are allowed to reach into module-private symbols for testing purposes — the
    rule only forbids VERB code (state/__init__.py) from importing them.

    The 8 required fields (D-02):
      pid, proc_start_epoch, boot_id, session_id, project_hash,
      reason, acquired_at, expires_at
    """
    import json
    import importlib
    lock_module = importlib.import_module("em_proj.state.lock")

    holder = lock_module._make_holder(reason=None, ttl=60)
    assert isinstance(holder, dict), f"_make_holder must return a dict; got {type(holder)}"

    expected_keys = {
        "pid",
        "proc_start_epoch",
        "boot_id",
        "session_id",
        "project_hash",
        "reason",
        "acquired_at",
        "expires_at",
    }
    actual_keys = set(holder.keys())
    assert actual_keys == expected_keys, (
        f"D-02 holder must have exactly 8 fields; "
        f"missing={expected_keys - actual_keys}, extra={actual_keys - expected_keys}"
    )

    # Round-trip through encode/decode (sort_keys=True stability check)
    encoded = lock_module._encode_holder(holder)
    assert isinstance(encoded, str)
    decoded = json.loads(encoded)
    assert set(decoded.keys()) == expected_keys, (
        "D-02 round-trip must preserve all 8 fields"
    )

    # Type assertions on selected fields
    assert isinstance(holder["pid"], int)
    assert isinstance(holder["proc_start_epoch"], float)
    assert isinstance(holder["boot_id"], str) and len(holder["boot_id"]) == 16
    assert isinstance(holder["session_id"], str)
    assert isinstance(holder["project_hash"], str)
    assert holder["reason"] is None  # we passed reason=None
    assert isinstance(holder["acquired_at"], float)
    assert isinstance(holder["expires_at"], float)
    assert holder["expires_at"] > holder["acquired_at"]


# ---------------------------------------------------------------------------
# D-06: compare-and-delete Lua script exists
# ---------------------------------------------------------------------------


def test_lua_compare_and_delete_script_exists(lock_tree: ast.Module) -> None:
    """D-06: LUA_COMPARE_AND_DELETE is defined in lock.py with correct bodies.

    The compare-and-delete Lua script is the atomic unlock primitive. It must
    contain cjson.decode (for JSON parsing inside Lua) and redis.call('DEL', ...)
    (the actual deletion). Without these, unlock is not atomic.
    """
    value = _find_assign(lock_tree, "LUA_COMPARE_AND_DELETE")
    assert value is not None, (
        "LUA_COMPARE_AND_DELETE missing from lock.py (D-06 Lua compare-and-delete)"
    )
    assert isinstance(value, ast.Constant) and isinstance(value.value, str), (
        "LUA_COMPARE_AND_DELETE must be a string constant"
    )
    script = value.value
    assert "cjson.decode" in script, (
        "LUA_COMPARE_AND_DELETE must use cjson.decode for JSON parsing inside Lua"
    )
    assert "redis.call('DEL'" in script or 'redis.call("DEL"' in script, (
        "LUA_COMPARE_AND_DELETE must call redis.call('DEL', ...) to delete the key"
    )


# ---------------------------------------------------------------------------
# D-10: compare-and-swap-if-stale Lua script exists
# ---------------------------------------------------------------------------


def test_lua_compare_and_swap_if_stale_script_exists(lock_tree: ast.Module) -> None:
    """D-10: LUA_COMPARE_AND_SWAP_IF_STALE is defined in lock.py.

    The opportunistic stale-takeover script must contain SET (to write the new
    holder) and return the new_json on success. This is the server-side half of
    D-10's TOCTOU protection.
    """
    value = _find_assign(lock_tree, "LUA_COMPARE_AND_SWAP_IF_STALE")
    assert value is not None, (
        "LUA_COMPARE_AND_SWAP_IF_STALE missing from lock.py (D-10 opportunistic stale-takeover)"
    )
    assert isinstance(value, ast.Constant) and isinstance(value.value, str), (
        "LUA_COMPARE_AND_SWAP_IF_STALE must be a string constant"
    )
    script = value.value
    # Must contain SET (to write the new holder JSON on takeover)
    assert "redis.call('SET'" in script or 'redis.call("SET"' in script, (
        "LUA_COMPARE_AND_SWAP_IF_STALE must call redis.call('SET', ...) to write new holder"
    )


# ---------------------------------------------------------------------------
# D-07: --warn checks BOTH isatty calls
# ---------------------------------------------------------------------------


def test_warn_flag_checks_both_stdout_and_stdin_isatty() -> None:
    """D-07: --warn gate must check BOTH sys.stdout.isatty() AND sys.stdin.isatty().

    T-3-XX-05 mitigation: a non-TTY stdin means the user cannot actually type a
    response to the --warn prompt. Checking only stdout allows scripts that redirect
    stdin to silently trigger the prompt path.

    Uses source-text grep (simpler than AST for this check; both literals must be
    present in the same file).
    """
    source = STATE_INIT.read_text()
    assert "sys.stdout.isatty()" in source, (
        "state/__init__.py missing sys.stdout.isatty() check for --warn gate (D-07 / T-3-XX-05)"
    )
    assert "sys.stdin.isatty()" in source, (
        "state/__init__.py missing sys.stdin.isatty() check for --warn gate (D-07 / T-3-XX-05). "
        "Both stdout AND stdin must be TTYs — checking only stdout allows non-interactive override."
    )


# ---------------------------------------------------------------------------
# D-08: --warn + --hold mutex check
# ---------------------------------------------------------------------------


def test_warn_hold_mutex_check_in_verb(state_init_tree: ast.Module) -> None:
    """D-08: --warn and --hold must be mutually exclusive at verb-body level.

    The mutex check must run BEFORE any Redis call. Source-text grep for the
    guard expression covering both orderings (warn and hold / hold and warn).
    """
    source = STATE_INIT.read_text()
    # Accept either ordering of the boolean expression
    has_guard = "warn and hold" in source or "hold and warn" in source
    assert has_guard, (
        "state/__init__.py missing mutual-exclusion check for --warn and --hold "
        "(D-08: these options are semantically incompatible; error before any Redis call)"
    )


# ---------------------------------------------------------------------------
# D-15 carry: emit_held_by_another exported from output.py
# ---------------------------------------------------------------------------


def test_emit_held_by_another_exported_from_output(output_tree: ast.Module) -> None:
    """D-15 carry: emit_held_by_another must be a module-level function in output.py.

    Phase 3 adds this helper symmetric with emit_error (D-09 displaced-holder-learns
    principle; D-05 status pre-announced in Phase 2). Exit code 3 is the 'held_by_another'
    exit code (first user wins).
    """
    func = _find_funcdef(output_tree, "emit_held_by_another")
    assert func is not None, (
        "emit_held_by_another missing from output.py — D-15 / D-09 require this helper "
        "for the 'held_by_another' exit-code-3 path"
    )


# ---------------------------------------------------------------------------
# D-15 carry: output.py still dependency-free (emit_held_by_another must not leak)
# ---------------------------------------------------------------------------


def test_output_py_remains_dependency_free_after_phase3(output_tree: ast.Module) -> None:
    """D-15 carry: emit_held_by_another must not introduce forbidden imports to output.py.

    output.py is the dependency-free envelope module. Adding emit_held_by_another
    must not accidentally import typer, redis, or em_proj.redis_client.
    """
    imports = _iter_imports(output_tree)
    forbidden = {"typer", "redis", "em_proj.redis_client"}
    leaked = [name for name in imports if name in forbidden or any(
        name.startswith(f"{prefix}.") for prefix in forbidden
    )]
    assert not leaked, (
        f"output.py imports forbidden modules after Phase 3 additions "
        f"(breaks D-15 dependency-free contract): {leaked}"
    )


# ---------------------------------------------------------------------------
# D-17/D-14 carry: lock.py imports validate_key from em_proj.state.kv
# ---------------------------------------------------------------------------


def test_lock_py_uses_validate_key_from_kv(lock_tree: ast.Module) -> None:
    """D-17/D-14 carry: lock.py must import validate_key (or em_proj.state.kv).

    Phase 2 D-09 explicit carry: "applies to all verbs accepting a key …
    later phases inherit for lock/claim names too." Lock names go through
    the same validation regex as KV keys.
    """
    imports = _iter_imports(lock_tree)
    uses_kv = any(
        name == "em_proj.state.kv" or name.startswith("em_proj.state.kv.")
        for name in imports
    )
    assert uses_kv, (
        "lock.py must import from em_proj.state.kv (validate_key reuse — Phase 2 D-09 carry)"
    )


# ---------------------------------------------------------------------------
# D-19 carry (narrowed): lock primitives do not catch redis errors;
# refresher handler is allowed and required
# ---------------------------------------------------------------------------


def test_lock_primitives_do_not_catch_redis_errors(lock_tree: ast.Module) -> None:
    """D-19 carry (narrowed): lock_acquire, lock_release, lock_force_displace must
    NOT catch redis errors. The refresher's narrow handler is allowed.

    Walks the function bodies of the three primitive ops and asserts no ExceptHandler
    node catches a redis.* exception. The refresher's (redis.ConnectionError,
    redis.TimeoutError) handler is permitted — it lives in RefresherThread.run(),
    not in the primitives.
    """
    forbidden_funcs = {"lock_acquire", "lock_release", "lock_force_displace"}
    offenders: list[str] = []

    for node in lock_tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name not in forbidden_funcs:
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.ExceptHandler):
                continue
            if sub.type is None:
                continue
            types_to_check: list[ast.AST] = []
            if isinstance(sub.type, ast.Tuple):
                types_to_check.extend(sub.type.elts)
            else:
                types_to_check.append(sub.type)
            for t in types_to_check:
                src = ast.unparse(t)
                if src.startswith("redis."):
                    offenders.append(f"{node.name}() line {sub.lineno}: except {src}")

    assert not offenders, (
        "D-19 violated — lock primitive(s) catch redis exceptions "
        f"(must defer to redis_client.die_if_redis_unreachable):\n  "
        + "\n  ".join(offenders)
    )


def test_refresher_narrow_handler_present_in_lock_source() -> None:
    """D-19 carry (narrowed — Blocker #3 context): the refresher's EXPIRE calls
    must catch (redis.ConnectionError, redis.TimeoutError) — the documented
    recovery posture (T-3-05-04).

    This is a COMPLEMENTARY assertion to the primitive no-catch test above:
    together they pin "primitives don't catch Redis errors; the refresher's narrow
    transient catch is the only exception and is required."
    """
    source = STATE_LOCK.read_text()
    pattern = re.compile(r"except\s*\(\s*redis\.ConnectionError\s*,\s*redis\.TimeoutError\s*\)")
    assert pattern.search(source) is not None, (
        "lock.py must contain `except (redis.ConnectionError, redis.TimeoutError)` "
        "in RefresherThread.run() — D-19 carry / Blocker #3 recovery posture. "
        "A refactor that drops the narrow catch (or changes it to bare `except Exception`) "
        "fails this test."
    )


# ---------------------------------------------------------------------------
# D-17 carry: lock.py does not import typer
# ---------------------------------------------------------------------------


def test_lock_py_does_not_import_typer(lock_tree: ast.Module) -> None:
    """D-17 carry: lock.py is the pure ops module — typer must not appear in its imports.

    Verb wiring belongs in state/__init__.py; the pure ops module must be
    importable without the CLI framework (enables isolation-testable unit tests).
    """
    imports = _iter_imports(lock_tree)
    bad = [name for name in imports if name == "typer" or name.startswith("typer.")]
    assert not bad, (
        f"lock.py imports typer (breaks D-17 — verb wiring belongs in __init__.py): {bad}"
    )


# ---------------------------------------------------------------------------
# Phase 1 pitfall #6 carry: lock.py does not import multiprocessing
# ---------------------------------------------------------------------------


def test_lock_py_does_not_import_multiprocessing(lock_tree: ast.Module) -> None:
    """Phase 1 pitfall #6 carry: lock.py must not import multiprocessing.

    multiprocessing.Process with the fork start-method crashes intermittently
    on macOS with OBJC_DISABLE_INITIALIZE_FORK_SAFETY errors. All concurrency in
    lock.py uses threading (daemon thread for refresher) and subprocess.Popen
    (for --hold wrapped commands). The multiprocessing module must not appear.
    """
    imports = _iter_imports(lock_tree)
    assert "multiprocessing" not in imports, (
        "lock.py must not import multiprocessing (Phase 1 pitfall #6 — "
        "use threading.Thread for refresher, subprocess.Popen for wrapped commands)"
    )


# ---------------------------------------------------------------------------
# D-14 extended: state/__init__.py registers >= 6 verbs
# ---------------------------------------------------------------------------


def test_state_init_registers_six_verbs_including_lock_unlock(state_init_tree: ast.Module) -> None:
    """D-14 extended: state_app must register at least 6 verbs: get/set/del/list/lock/unlock.

    Phase 2 pinned 4 KV verbs. Phase 3 adds lock and unlock. This test extends
    the Phase 2 assertion to cover the new lock-verb pair.
    """
    expected = {"get", "set", "del", "list", "lock", "unlock"}
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
        f"state_app missing Phase 3 verbs: {sorted(missing)}; got {sorted(seen)}. "
        f"D-14 (extended) requires get/set/del/list/lock/unlock."
    )


# ---------------------------------------------------------------------------
# Decision Coverage Gate for Phase 3
# ---------------------------------------------------------------------------


def test_every_phase_3_decision_cited_in_at_least_one_plan() -> None:
    """Decision Coverage Gate: every D-01..D-12 cited in at least one Phase 3 plan.

    Reads every ``03-*-PLAN.md`` under the Phase 3 directory in pure Python,
    extracts every ``D-NN`` reference (normalizing ``D-1`` and ``D-01`` to the
    same id), and asserts the union covers D-01..D-12. Phase 3 has exactly 12
    decisions per 03-CONTEXT.md.

    A future plan that drops a D-ID without another plan picking it up will fail
    this test under ``bash scripts/test.sh structural``.
    """
    if not PHASE_DIR.exists():
        pytest.skip(
            f"{PHASE_DIR.relative_to(REPO_ROOT)} not present — planning worktree "
            "may not be attached on this checkout"
        )
    plan_files = sorted(PHASE_DIR.glob("03-*-PLAN.md"))
    if not plan_files:
        pytest.skip(f"no 03-*-PLAN.md files yet under {PHASE_DIR.relative_to(REPO_ROOT)}")

    cited: set[str] = set()
    for plan in plan_files:
        for match in re.findall(r"D-(\d+)", plan.read_text()):
            cited.add(f"D-{int(match):02d}")

    expected = {f"D-{i:02d}" for i in range(1, 13)}  # D-01..D-12
    missing = expected - cited
    assert not missing, (
        f"Decision Coverage Gate failed — D-IDs not cited in any Phase 3 plan: "
        f"{sorted(missing)}.\n"
        f"Scanned {len(plan_files)} plan file(s); cited D-IDs: {sorted(cited)}."
    )


# ---------------------------------------------------------------------------
# Blocker #1 pin a: lock_force_displace is exported
# ---------------------------------------------------------------------------


def test_lock_force_displace_is_exported(lock_tree: ast.Module) -> None:
    """Blocker #1: --warn override uses the public op lock_force_displace,
    not private _encode_holder. This pins the public surface.

    The D-14/D-17 thin-verb-shell discipline requires displacement Lua to be
    server-side via lock_force_displace, not assembled in the verb body via
    private _encode_holder. This test pins that lock_force_displace exists
    as a module-level function and is importable (catches accidental indentation
    under a class).
    """
    assert _find_funcdef(lock_tree, "lock_force_displace") is not None, (
        "lock_force_displace must be a module-level function in em_proj.state.lock "
        "(Blocker #1: public op for the --warn displacement path)"
    )
    # Also assert importability — catches accidental indentation under a class
    import importlib
    lock_module = importlib.import_module("em_proj.state.lock")
    assert hasattr(lock_module, "lock_force_displace"), (
        "lock_force_displace must be accessible as em_proj.state.lock.lock_force_displace"
    )
    assert callable(lock_module.lock_force_displace), (
        "lock_force_displace must be callable"
    )


# ---------------------------------------------------------------------------
# Blocker #1 pin b: verb code imports no private symbols from lock.py
# ---------------------------------------------------------------------------


def test_state_init_imports_no_private_symbols_from_lock(state_init_tree: ast.Module) -> None:
    """Blocker #1: state/__init__.py (verb code) must not import private
    (underscore-prefixed) symbols from em_proj.state.lock. The thin-verb-shell
    discipline (D-14/D-17) requires displacement Lua to live in
    lock_force_displace (server-side), not in the verb body via _encode_holder.

    Explicitly pins the four specific symbols that would be the likely backslide
    candidates: _encode_holder, _decode_holder, _make_holder, _validate_reason.
    """
    private_names = []
    for node in ast.walk(state_init_tree):
        if isinstance(node, ast.ImportFrom) and node.module == "em_proj.state.lock":
            for alias in node.names:
                if alias.name.startswith("_"):
                    private_names.append(alias.name)
    assert not private_names, (
        f"state/__init__.py must not import private symbols from em_proj.state.lock; "
        f"found: {private_names}. Use the public lock_force_displace op instead "
        f"(D-14/D-17 thin-shell discipline)."
    )
    # Explicit name-checks for the four specific symbols that would be the
    # likely backslide candidates:
    source = STATE_INIT.read_text()
    for forbidden in ("_encode_holder", "_decode_holder", "_make_holder", "_validate_reason"):
        assert forbidden not in source, (
            f"state/__init__.py references {forbidden!r} — private symbol from lock.py. "
            f"Use the lock_force_displace public op (D-14/D-17 thin-shell discipline)."
        )


# ---------------------------------------------------------------------------
# Blocker #3 pin: refresher catches (redis.ConnectionError, redis.TimeoutError)
# ---------------------------------------------------------------------------


def test_refresher_catches_redis_transients(lock_tree: ast.Module) -> None:
    """Blocker #3: RefresherThread.run() must catch (redis.ConnectionError,
    redis.TimeoutError) — the documented recovery posture (T-3-05-04 / D-05).

    A refactor that drops the narrow catch (or changes it to bare `except Exception`
    or a single-arg `except redis.ConnectionError`) fails this test. The recovery
    posture's narrow tuple is the documented contract: log + keep-looping, best-effort
    survival while Redis is temporarily unreachable.

    If this test fails, either the handler was removed or its shape changed.
    To update: change both lock.py AND this regex in one commit (with justification).
    """
    source = STATE_LOCK.read_text()
    # Accept whitespace flexibility but pin the exact exception tuple shape.
    pattern = re.compile(r"except\s*\(\s*redis\.ConnectionError\s*,\s*redis\.TimeoutError\s*\)")
    assert pattern.search(source) is not None, (
        "lock.py must contain `except (redis.ConnectionError, redis.TimeoutError)` "
        "in RefresherThread.run() — Blocker #3 recovery posture (log + keep-looping). "
        "If this test fails, either the handler was removed or its shape changed "
        "(T-3-05-04 / D-05 refresher recovery contract)."
    )
