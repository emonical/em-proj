from __future__ import annotations
"""Phase 8 structural invariants — source-grep and file-presence assertions.

Encodes Phase 8 plan acceptance criteria as runtime pytest assertions:
  - Session module file presence and required function names (SESS-01..04)
  - KEY_PREFIX machine-global value ("state:session:") locked in source
  - TTL_DEFAULT five-minute default (300 seconds) locked in source
  - Prohibited imports in session ops module (typer, multiprocessing, threading)
  - session_app wired in cli.py (import + add_typer = at least 2 occurrences)
  - session/__init__.py has at least 4 @session_app.command decorators
  - SessionNotFound exception has code attribute with "not_found" value
  - Lua scripts LUA_SESSION_UPSERT and LUA_SESSION_HEARTBEAT are present
  - Cross-namespace scan covers all three namespaces (claim/lock/reserve)
  - SUMMARY coverage: every 08-*-PLAN.md has a matching 08-*-SUMMARY.md

Each structural file is self-contained (no imports from sibling
test_phase_*_shape.py files) per Phase 1+2+3+4+5+6+7 precedent.
"""

from pathlib import Path

import pytest

# Self-contained — no imports from sibling test_phase_*_shape.py files.

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "08-session-registry-hybrid"

# Session ops file: prefer package layout (session/_ops.py), fall back to flat module.
# Plan 08-02 performed `git mv session.py session/_ops.py`, so the package path is canonical.
_SESSION_OPS_CANDIDATES = [
    REPO_ROOT / "src" / "em_proj" / "session" / "_ops.py",
    REPO_ROOT / "src" / "em_proj" / "session.py",
]
SESSION_OPS = next((p for p in _SESSION_OPS_CANDIDATES if p.exists()), None)

SESSION_INIT = REPO_ROOT / "src" / "em_proj" / "session" / "__init__.py"
CLI_PY = REPO_ROOT / "src" / "em_proj" / "cli.py"


# ---------------------------------------------------------------------------
# Test A — session ops module exists and has required function names
# ---------------------------------------------------------------------------


def test_session_module_exists_and_has_required_functions() -> None:
    """Plan 08-01 must create the session ops module with all required public ops.

    Asserts file existence and function name presence via source-text grep.
    Covers SESS-01 (register), SESS-02 (heartbeat), SESS-03 (list), SESS-04 (show),
    D4 enrichment join helper, and _hgetall_to_session decoder.
    """
    assert SESSION_OPS is not None, (
        "No session ops file found at session/_ops.py or session.py — "
        "Plan 08-01 must create the session module"
    )
    src = SESSION_OPS.read_text()

    # SESS-01: registration op
    assert "def session_register" in src, (
        "session_register missing from session ops — SESS-01 not satisfied"
    )
    # SESS-02: heartbeat op
    assert "def session_heartbeat" in src, (
        "session_heartbeat missing from session ops — SESS-02 not satisfied"
    )
    # SESS-03: list op
    assert "def session_list" in src, (
        "session_list missing from session ops — SESS-03 not satisfied"
    )
    # SESS-04: show op
    assert "def session_show" in src, (
        "session_show missing from session ops — SESS-04 not satisfied"
    )
    # D4: enrichment join helper (cross-namespace scan)
    assert "_scan_all_holders_by_session_id" in src, (
        "_scan_all_holders_by_session_id missing — D4 cross-namespace enrichment join "
        "must be implemented in the session ops module"
    )
    # Decoder helper
    assert "def _hgetall_to_session" in src, (
        "_hgetall_to_session missing — the Redis HGETALL type-coercion helper "
        "must be present in the session ops module"
    )


# ---------------------------------------------------------------------------
# Test B — KEY_PREFIX is machine-global ("state:session:")
# ---------------------------------------------------------------------------


def test_session_key_prefix_is_machine_global() -> None:
    """session ops module must define KEY_PREFIX = 'state:session:'.

    Machine-global scope (not project-scoped) is the D4 invariant that enables
    cross-project session listing for Phase 10 message scope filtering.
    A future refactor that accidentally changes the prefix would break all
    existing session keys — this test machine-enforces the value.
    """
    assert SESSION_OPS is not None, "session ops file missing — cannot check KEY_PREFIX"
    src = SESSION_OPS.read_text()

    assert "KEY_PREFIX" in src, "KEY_PREFIX constant missing from session ops"
    assert '"state:session:"' in src or "'state:session:'" in src, (
        "KEY_PREFIX must be set to 'state:session:' — "
        "machine-global namespace (D4 invariant)"
    )


# ---------------------------------------------------------------------------
# Test C — TTL_DEFAULT is 300 seconds (5 minutes)
# ---------------------------------------------------------------------------


def test_session_ttl_default_is_five_minutes() -> None:
    """session ops module must define TTL_DEFAULT = 300.

    The 5-minute heartbeat backstop (D2) is a locked design choice.
    Changing this value would alter the stale-reaping timeline.
    """
    assert SESSION_OPS is not None, "session ops file missing — cannot check TTL_DEFAULT"
    src = SESSION_OPS.read_text()

    assert "TTL_DEFAULT" in src, "TTL_DEFAULT constant missing from session ops"
    assert "TTL_DEFAULT: int = 300" in src or "TTL_DEFAULT = 300" in src, (
        "TTL_DEFAULT must be 300 (5-minute heartbeat backstop per D2); "
        "found TTL_DEFAULT but not the value 300"
    )


# ---------------------------------------------------------------------------
# Test D — session ops module prohibits forbidden imports
# ---------------------------------------------------------------------------


def test_session_module_prohibits_forbidden_imports() -> None:
    """The session ops module must not import typer, multiprocessing, or threading.

    These imports are forbidden by the D-14 thin-verb-shell discipline:
    - typer   → belongs in session/__init__.py (the mount module), not ops
    - multiprocessing → Phase 1 Pitfall #6 (fork+exec safety on macOS)
    - threading → not required; would mask concurrency issues

    Filter to actual import lines (startswith 'import' or 'from') to avoid
    false positives from commented-out lines or docstring references.
    """
    assert SESSION_OPS is not None, "session ops file missing — cannot check imports"
    src = SESSION_OPS.read_text()

    import_lines = [
        line.strip()
        for line in src.splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]

    for forbidden in ("typer", "multiprocessing", "threading"):
        for line in import_lines:
            assert forbidden not in line, (
                f"Forbidden import '{forbidden}' found in session ops: {line!r}. "
                f"The {forbidden!r} import must NOT appear in the ops module; "
                "it belongs in session/__init__.py (typer) or is prohibited (multiprocessing/threading)."
            )


# ---------------------------------------------------------------------------
# Test E — session_app wired in cli.py
# ---------------------------------------------------------------------------


def test_session_app_wired_in_cli() -> None:
    """cli.py must import and mount session_app (import + add_typer = >= 2 occurrences).

    Plan 08-02 Task 2 wires session_app into cli.py. At least two references
    to 'session_app' are required: the import line and the add_typer call.
    """
    assert CLI_PY.exists(), f"cli.py missing at {CLI_PY}"
    cli_src = CLI_PY.read_text()

    assert "session_app" in cli_src, (
        "cli.py does not reference 'session_app' — "
        "Plan 08-02 session_app mount not applied"
    )
    assert cli_src.count("session_app") >= 2, (
        f"'session_app' appears {cli_src.count('session_app')} time(s) in cli.py; "
        "expected >= 2 (import line + add_typer call). "
        "Plan 08-02 Task 2 mount incomplete."
    )


# ---------------------------------------------------------------------------
# Test F — session/__init__.py has four verb commands
# ---------------------------------------------------------------------------


def test_session_init_has_four_verb_commands() -> None:
    """session/__init__.py must have >= 4 @session_app.command decorators.

    Plan 08-02 creates register, heartbeat, list, and show — the four SESS-01..04
    verbs. Each must have a @session_app.command decorator.
    """
    assert SESSION_INIT.exists(), (
        f"session/__init__.py missing at {SESSION_INIT} — "
        "Plan 08-02 must create the session package with verb commands"
    )
    init_src = SESSION_INIT.read_text()

    command_count = init_src.count("@session_app.command")
    assert command_count >= 4, (
        f"@session_app.command appears {command_count} time(s) in session/__init__.py; "
        "expected >= 4 (register, heartbeat, list, show)"
    )


# ---------------------------------------------------------------------------
# Test G — SessionNotFound exception has code attribute with "not_found" value
# ---------------------------------------------------------------------------


def test_session_not_found_exception_has_code_attribute() -> None:
    """session ops module must define SessionNotFound with code = 'not_found'.

    The code attribute is the machine-readable error key (maps to
    output.py emit_not_found's code argument). A future rename of the
    attribute or value would break the emit_not_found call contract.
    """
    assert SESSION_OPS is not None, "session ops file missing — cannot check SessionNotFound"
    src = SESSION_OPS.read_text()

    assert "class SessionNotFound" in src, (
        "SessionNotFound exception class missing from session ops"
    )
    assert '"not_found"' in src or "'not_found'" in src, (
        "SessionNotFound.code value 'not_found' not found in source; "
        "the code attribute must be set to the string 'not_found'"
    )


# ---------------------------------------------------------------------------
# Test H — Lua scripts LUA_SESSION_UPSERT and LUA_SESSION_HEARTBEAT are present
# ---------------------------------------------------------------------------


def test_lua_scripts_are_present() -> None:
    """session ops module must define LUA_SESSION_UPSERT and LUA_SESSION_HEARTBEAT.

    These server-side atomic scripts are the D5 atomicity mechanism for
    register and heartbeat operations. Absence means the Lua atomicity
    layer is missing — a correctness regression.
    """
    assert SESSION_OPS is not None, "session ops file missing — cannot check Lua scripts"
    src = SESSION_OPS.read_text()

    assert "LUA_SESSION_UPSERT" in src, (
        "LUA_SESSION_UPSERT missing from session ops — "
        "atomic upsert script required for session_register (SESS-01)"
    )
    assert "LUA_SESSION_HEARTBEAT" in src, (
        "LUA_SESSION_HEARTBEAT missing from session ops — "
        "atomic heartbeat script required for session_heartbeat (SESS-02)"
    )


# ---------------------------------------------------------------------------
# Test I — cross-namespace scan covers all three namespaces
# ---------------------------------------------------------------------------


def test_cross_namespace_scan_covers_all_three_namespaces() -> None:
    """_scan_all_holders_by_session_id must scan claim, lock, and reserve namespaces.

    The D4 enrichment join MUST scan all three namespaces to correctly
    populate held["claims"], held["locks"], and held["reserves"] in the
    session list output. A future refactor that drops one namespace would
    silently break the enrichment counts.

    Asserts that the session ops source references all three namespace prefixes
    (either directly as string literals or as imported KEY_PREFIX constants).
    """
    assert SESSION_OPS is not None, "session ops file missing — cannot check namespaces"
    src = SESSION_OPS.read_text()

    # Check that the source references all three namespaces
    # (either as literal strings or as CLAIM_PREFIX / LOCK_PREFIX / RESERVE_PREFIX vars)
    has_claim = "state:claim:" in src or "CLAIM_PREFIX" in src
    has_lock = "state:lock:" in src or "LOCK_PREFIX" in src
    has_reserve = "state:reserve:" in src or "RESERVE_PREFIX" in src

    assert has_claim, (
        "session ops source does not reference the claim namespace "
        "('state:claim:' or CLAIM_PREFIX). D4 enrichment join must scan "
        "state:claim:* — missing means held['claims'] will always be 0."
    )
    assert has_lock, (
        "session ops source does not reference the lock namespace "
        "('state:lock:' or LOCK_PREFIX). D4 enrichment join must scan "
        "state:lock:* — missing means held['locks'] will always be 0."
    )
    assert has_reserve, (
        "session ops source does not reference the reserve namespace "
        "('state:reserve:' or RESERVE_PREFIX). D4 enrichment join must scan "
        "state:reserve:* — missing means held['reserves'] will always be 0."
    )


# ---------------------------------------------------------------------------
# Test J — SUMMARY coverage: every 08-*-PLAN.md has a 08-*-SUMMARY.md
# ---------------------------------------------------------------------------


def test_phase_08_summaries_exist() -> None:
    """SUMMARY coverage check: every 08-*-PLAN.md must have a 08-*-SUMMARY.md sibling.

    Uses pytest.skip (NOT xfail) when PHASE_DIR is absent — planning worktree
    may not be attached on this checkout, which is a legitimate developer setup
    (not a regression).

    The summary files from Plans 08-01, 08-02, 08-03 are the machine-checkable
    proof that each plan's executor completed and wrote a Summary.
    """
    if not PHASE_DIR.exists():
        pytest.skip(
            f"{PHASE_DIR.relative_to(REPO_ROOT)} not present — "
            "planning worktree may not be attached on this checkout"
        )
    plans = sorted(PHASE_DIR.glob("08-*-PLAN.md"))
    if not plans:
        pytest.skip(
            f"no 08-*-PLAN.md files yet under "
            f"{PHASE_DIR.relative_to(REPO_ROOT)}"
        )
    for plan in plans:
        summary = plan.parent / plan.name.replace("-PLAN.md", "-SUMMARY.md")
        assert summary.exists(), (
            f"Missing SUMMARY for {plan.name}: expected {summary.name} "
            f"in {PHASE_DIR.relative_to(REPO_ROOT)}"
        )
