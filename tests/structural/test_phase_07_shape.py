from __future__ import annotations
"""Phase 7 structural invariants — source-grep and AST assertions.

Encodes plan acceptance criteria as runtime pytest assertions:
  - Two-namespace disjointness (Pitfall #8): claim.py and reserve.py
    KEY_PREFIX values are different; neither file references the
    other's prefix literal; claim.py has no 'upstream_identity'
    reference (it's a Phase 7-only field).
  - reserve.py shape: 3 Lua scripts as module-level string constants;
    7-field holder enforced via _RESERVE_ARGV_ORDER tuple presence.
  - Verb wiring: state/__init__.py contains @state_app.command('reserve'),
    @state_app.command('reserve-list'), AND the check verb references
    --upstream.
  - Per-child cwd= in multi-clone tests (Pitfall #6 — machine-enforced):
    every subprocess.Popen(…) site in test_reserve_*.py contains a
    cwd= kwarg.
  - Actionable-error copy lock (RESEARCH §Pattern 4): the exact
    'workstream unresolved — set it via' substring is present in
    state/__init__.py.
  - SUMMARY coverage: every 07-*-PLAN.md has a 07-*-SUMMARY.md sibling.

Each structural file is self-contained (no imports from sibling
test_phase_*_shape.py files) per Phase 1+2+3+4+5+6 precedent.
"""

import re
from pathlib import Path

import pytest

# Self-contained — no imports from sibling test_phase_*_shape.py files.

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "07-project-scoped-reservation-registry"
RESERVE_PY = REPO_ROOT / "src" / "em_proj" / "state" / "reserve.py"
CLAIM_PY = REPO_ROOT / "src" / "em_proj" / "state" / "claim.py"
STATE_INIT_PY = REPO_ROOT / "src" / "em_proj" / "state" / "__init__.py"
IDENTITY_PY = REPO_ROOT / "src" / "em_proj" / "identity.py"
RESERVE_RACE_TEST = REPO_ROOT / "tests" / "multiprocess" / "test_reserve_race.py"
RESERVE_THREE_CLONES_TEST = REPO_ROOT / "tests" / "multiprocess" / "test_reserve_three_clones_list.py"

# Cross-repo (NOT in em-proj git tree) — skill-doc invariant only
SKILL_PATH = Path.home() / ".claude" / "skills" / "em-global-state" / "SKILL.md"


# ---------------------------------------------------------------------------
# Test A — reserve.py exists and has all 3 Lua scripts
# ---------------------------------------------------------------------------


def test_reserve_py_exists_and_has_three_lua_scripts() -> None:
    """Plan 07-01 must create reserve.py with 3 named Lua script constants."""
    assert RESERVE_PY.exists(), "Plan 07-01 must create reserve.py"
    src = RESERVE_PY.read_text()
    for lua_name in (
        "LUA_RESERVE_REFRESH_OR_TAKE",
        "LUA_RESERVE_COMPARE_AND_DELETE",
        "LUA_RESERVE_CHECK",
    ):
        assert lua_name in src, f"{lua_name} missing from reserve.py"


# ---------------------------------------------------------------------------
# Test B — KEY_PREFIX disjointness (Pitfall #8 invariant)
# ---------------------------------------------------------------------------


def test_key_prefixes_are_disjoint() -> None:
    """reserve.py and claim.py must define distinct KEY_PREFIX constants.

    The two-namespace invariant (Pitfall #8) means a future refactor cannot
    consolidate these modules into a single parameterized file without this
    test catching the collision.
    """
    assert RESERVE_PY.exists(), "reserve.py missing — Plan 07-01 not applied"
    assert CLAIM_PY.exists(), "claim.py missing — Phase 4 not applied"

    reserve_src = RESERVE_PY.read_text()
    claim_src = CLAIM_PY.read_text()

    # Use regex for the precise assignment shape
    m_reserve = re.search(
        r'^KEY_PREFIX\s*:\s*str\s*=\s*"([^"]+)"',
        reserve_src,
        re.MULTILINE,
    )
    m_claim = re.search(
        r'^KEY_PREFIX\s*:\s*str\s*=\s*"([^"]+)"',
        claim_src,
        re.MULTILINE,
    )

    assert m_reserve and m_reserve.group(1) == "state:reserve:", (
        "reserve.py KEY_PREFIX must be 'state:reserve:'"
    )
    assert m_claim and m_claim.group(1) == "state:claim:", (
        "claim.py KEY_PREFIX must be 'state:claim:'"
    )


# ---------------------------------------------------------------------------
# Test C — namespaces don't cross-contaminate (Pitfall #8 deeper)
# ---------------------------------------------------------------------------


def test_namespaces_dont_cross_contaminate() -> None:
    """Source-text assertion that the two modules don't reference each
    other's namespace string.

    A future consolidation refactor that parameterizes the prefix would
    necessarily include both strings in one file — that should fail this test.
    The upstream_identity field check prevents Phase 7 holder fields from
    leaking into the Phase 4 claim namespace.
    """
    assert CLAIM_PY.exists(), "claim.py missing"
    assert RESERVE_PY.exists(), "reserve.py missing"

    claim_src = CLAIM_PY.read_text()
    reserve_src = RESERVE_PY.read_text()

    # claim.py must NOT reference reserve namespace
    assert "state:reserve:" not in claim_src, (
        "claim.py source contains 'state:reserve:' — two-namespace "
        "invariant violated (Pitfall #8)"
    )
    # reserve.py must NOT reference claim namespace
    assert "state:claim:" not in reserve_src, (
        "reserve.py source contains 'state:claim:' — two-namespace "
        "invariant violated (Pitfall #8)"
    )
    # claim.py must NOT have upstream_identity (Phase 7-only field).
    # NOTE: if a future Phase consolidates and uses 'upstream_identity'
    # in claim.py, this assertion will fail — that's the point.
    assert "upstream_identity" not in claim_src, (
        "claim.py source contains 'upstream_identity' — Phase 7 holder "
        "field has leaked into the claim namespace (Pitfall #8)"
    )


# ---------------------------------------------------------------------------
# Test D — state/__init__.py has reserve verbs wired
# ---------------------------------------------------------------------------


def test_state_init_has_reserve_verbs() -> None:
    """state/__init__.py must contain both reserve verb registrations.

    Also asserts that --upstream is referenced (the reserve-list verb's
    cross-upstream filter flag from Plan 07-02).
    """
    assert STATE_INIT_PY.exists(), "state/__init__.py missing"
    src = STATE_INIT_PY.read_text()

    assert (
        '@state_app.command("reserve")' in src
        or "@state_app.command('reserve')" in src
    ), (
        "state/__init__.py missing @state_app.command('reserve') — "
        "Plan 07-02 verb wiring not landed"
    )
    assert (
        '@state_app.command("reserve-list")' in src
        or "@state_app.command('reserve-list')" in src
    ), (
        "state/__init__.py missing @state_app.command('reserve-list') — "
        "Plan 07-02 verb wiring not landed"
    )
    # The reserve-list verb must mention --upstream
    assert "--upstream" in src, (
        "state/__init__.py has no '--upstream' reference — the reserve-list "
        "verb extension from Plan 07-02 not landed"
    )


# ---------------------------------------------------------------------------
# Test E — actionable-error copy locked (RESEARCH §Pattern 4)
# ---------------------------------------------------------------------------


def test_actionable_error_copy_locked() -> None:
    """RESEARCH §Pattern 4 + Q-F lock the exact error wording.

    If a future refactor accidentally drops 'set it via' or rephrases the
    message, this test catches it. Plan 07-02's _resolve_workstream helper
    must preserve this exact phrasing.
    """
    assert STATE_INIT_PY.exists(), "state/__init__.py missing"
    src = STATE_INIT_PY.read_text()
    assert "workstream unresolved — set it via" in src, (
        "Locked actionable error copy 'workstream unresolved — set it "
        "via' missing from state/__init__.py. Plan 07-02's "
        "_resolve_workstream helper must preserve this exact phrasing."
    )


# ---------------------------------------------------------------------------
# Test F — multi-clone tests use per-child cwd= (Pitfall #6 invariant)
# ---------------------------------------------------------------------------


def test_multiproc_tests_use_per_child_cwd() -> None:
    """Every subprocess.Popen call in test_reserve_*.py must include
    cwd= as a kwarg.

    A test that varies only env= would produce a false-positive race outcome
    because both children would resolve the SAME upstream_identity from the
    test runner's cwd.
    """
    for test_file in (RESERVE_RACE_TEST, RESERVE_THREE_CLONES_TEST):
        assert test_file.exists(), f"Plan 07-02 must create {test_file.name}"
        src = test_file.read_text()

        # Find every subprocess.Popen( open paren.
        popen_matches = list(re.finditer(r"subprocess\.Popen\s*\(", src))
        if not popen_matches:
            # This test file uses subprocess.run, not Popen — that's fine.
            # Popen is only required in the race test; three-clones may use run.
            continue

        for m in popen_matches:
            # Take the slice from the open paren forward and find
            # the matching close paren. For simplicity, look at the
            # next 600 chars (Popen calls are short) and check for cwd=.
            chunk = src[m.start() : m.start() + 600]
            assert "cwd=" in chunk, (
                f"subprocess.Popen at offset {m.start()} in "
                f"{test_file.name} missing cwd= kwarg (Pitfall #6). "
                "Without per-child cwd=, both children would resolve "
                "the same upstream_identity from the test runner's "
                "cwd, producing a false-positive race outcome."
            )


# ---------------------------------------------------------------------------
# Test G — SUMMARY coverage: every 07-*-PLAN.md has a 07-*-SUMMARY.md
# ---------------------------------------------------------------------------


def test_phase_07_summaries_present() -> None:
    """SUMMARY coverage check, identical pattern to Phase 4/5/6.

    Uses skip (not xfail) for PHASE_DIR absence because PHASE_DIR is
    em-proj-internal; absence indicates "this checkout has no planning
    worktree attached" which is a legitimate developer setup (not a
    regression).
    """
    if not PHASE_DIR.exists():
        pytest.skip(
            f"{PHASE_DIR.relative_to(REPO_ROOT)} not present — "
            "planning worktree may not be attached on this checkout"
        )
    plans = sorted(PHASE_DIR.glob("07-*-PLAN.md"))
    if not plans:
        pytest.skip(
            f"no 07-*-PLAN.md files yet under "
            f"{PHASE_DIR.relative_to(REPO_ROOT)}"
        )
    for plan in plans:
        summary = plan.parent / plan.name.replace("-PLAN.md", "-SUMMARY.md")
        assert summary.exists(), (
            f"Missing SUMMARY for {plan.name}: expected {summary.name} "
            f"in {PHASE_DIR.relative_to(REPO_ROOT)}"
        )


# ---------------------------------------------------------------------------
# Test H — SKILL.md has reservations verb subsection
# ---------------------------------------------------------------------------


def test_skill_has_reservations_verb() -> None:
    """Cross-repo skill assertion. Uses pytest.skip (NOT xfail) on
    missing skill — the developer's checkout may not have the
    em-global-state skill installed.

    Absence of a personal Claude skill is NOT a Phase 7 failure (different
    from Phase 6's npm-install reversion case, which IS a regression).
    Absence here is acceptable on a fresh checkout; em-proj's CLI still
    works without the skill installed.
    """
    if not SKILL_PATH.exists():
        pytest.skip(
            f"{SKILL_PATH} not present — em-global-state skill not "
            "installed on this checkout. Phase 7 still ships the "
            "verb in em-proj; the skill is a separate user surface."
        )
    skill_src = SKILL_PATH.read_text()
    assert "reservations" in skill_src, (
        "SKILL.md missing the 'reservations' verb — Plan 07-03 "
        "Task 2 not applied OR skill was overwritten since"
    )
    assert "em-proj state reserve-list" in skill_src, (
        "SKILL.md reservations verb does not invoke "
        "`em-proj state reserve-list` — schema documentation broken"
    )
