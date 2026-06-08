from __future__ import annotations
"""Phase 10 structural invariants — file-presence, symbol-presence, and
prohibited-pattern assertions. RED against pre-Wave-1 code; GREEN after all waves
complete.

Mirrors tests/structural/test_phase_09_shape.py. Self-contained — no imports from
sibling test_phase_*_shape.py files (per Phase 1–9 precedent).
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "10-messaging-send-patterns"

MESSAGE_OPS = REPO_ROOT / "src" / "em_proj" / "message" / "_ops.py"
MESSAGE_INIT = REPO_ROOT / "src" / "em_proj" / "message" / "__init__.py"


# ---------------------------------------------------------------------------
# Test A — message ops module has the Phase 10 send/subscribe functions
# ---------------------------------------------------------------------------


def test_message_ops_has_phase10_functions() -> None:
    """message/_ops.py must define every Phase 10 op. FAILS pre-Wave-1 = correct RED."""
    src = MESSAGE_OPS.read_text()
    for fn in (
        "send_directed",
        "send_broadcast",
        "send_topic",
        "subscribe_topic",
        "unsubscribe_topic",
        "enumerate_scope_recipients",
        "_validate_topic",
    ):
        assert f"def {fn}" in src, (
            f"def {fn} missing from message/_ops.py — Phase 10 op not implemented (Wave 1 gap)"
        )


# ---------------------------------------------------------------------------
# Test B — TOPIC_KEY_PREFIX constant is 'topic:'
# ---------------------------------------------------------------------------


def test_topic_key_prefix_constant() -> None:
    """message/_ops.py must define TOPIC_KEY_PREFIX = 'topic:' (A1 namespace lock)."""
    src = MESSAGE_OPS.read_text()
    assert "TOPIC_KEY_PREFIX" in src, "TOPIC_KEY_PREFIX constant missing from message/_ops.py"
    assert '"topic:"' in src or "'topic:'" in src, (
        "TOPIC_KEY_PREFIX must be set to 'topic:' — namespace isolation invariant (A1)"
    )


# ---------------------------------------------------------------------------
# Test C — message ops module prohibits forbidden imports
# ---------------------------------------------------------------------------


def test_message_ops_prohibits_forbidden_imports() -> None:
    """message/_ops.py must not import typer, multiprocessing, or threading."""
    src = MESSAGE_OPS.read_text()
    import_lines = [
        line.strip()
        for line in src.splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]
    for forbidden in ("typer", "multiprocessing", "threading"):
        for line in import_lines:
            assert forbidden not in line, (
                f"Forbidden import {forbidden!r} found in message/_ops.py: {line!r}. "
                "typer belongs in __init__.py; multiprocessing/threading are prohibited."
            )


# ---------------------------------------------------------------------------
# Test D — no Redis pipeline in ops (plain fan-out loop per 10-RESEARCH Pitfall 4)
# ---------------------------------------------------------------------------


def test_no_pipeline_in_ops() -> None:
    """message/_ops.py must not call client.pipeline() — use a plain loop (Pitfall 4).

    AST-checked: only Attribute call targets are inspected, so docstring/comment
    references to 'pipeline' do not produce a false positive.
    """
    src = MESSAGE_OPS.read_text()
    tree = ast.parse(src)
    called_methods = {
        node.func.attr.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "pipeline" not in called_methods, (
        "client.pipeline() call found in message/_ops.py — use a plain fan-out loop "
        "per 10-RESEARCH Pitfall 4 (per-recipient error detection)"
    )


# ---------------------------------------------------------------------------
# Test E — message_app exposes send/broadcast/subscribe/unsubscribe verbs
# ---------------------------------------------------------------------------


def test_message_init_has_send_broadcast_subscribe_commands() -> None:
    """message/__init__.py must register all four Phase 10 verbs. FAILS pre-Wave-2 = RED."""
    src = MESSAGE_INIT.read_text()
    for verb in ("send", "broadcast", "subscribe", "unsubscribe"):
        assert f'@message_app.command("{verb}")' in src, (
            f'@message_app.command("{verb}") missing from message/__init__.py — Wave 2 gap'
        )


# ---------------------------------------------------------------------------
# Test F — SUMMARY coverage: every 10-*-PLAN.md has a 10-*-SUMMARY.md
# ---------------------------------------------------------------------------


def test_phase_10_summaries_exist() -> None:
    """Every 10-*-PLAN.md must have a 10-*-SUMMARY.md sibling once the phase completes.

    Skips when the planning worktree is not attached (legitimate developer setup).
    """
    if not PHASE_DIR.exists():
        pytest.skip(
            f"{PHASE_DIR.relative_to(REPO_ROOT)} not present — "
            "planning worktree may not be attached on this checkout"
        )
    plans = sorted(PHASE_DIR.glob("10-*-PLAN.md"))
    if not plans:
        pytest.skip(f"no 10-*-PLAN.md files yet under {PHASE_DIR.relative_to(REPO_ROOT)}")
    for plan in plans:
        summary = plan.parent / plan.name.replace("-PLAN.md", "-SUMMARY.md")
        assert summary.exists(), (
            f"Missing SUMMARY for {plan.name}: expected {summary.name} "
            f"in {PHASE_DIR.relative_to(REPO_ROOT)}"
        )


# ---------------------------------------------------------------------------
# Test G — send ops have value-returning return statements (flat-dict guard)
# ---------------------------------------------------------------------------


def test_send_return_is_flat_dict() -> None:
    """send_directed / send_broadcast / send_topic must each have a value return.

    Structural presence check (not a value check): FAILS pre-Wave-1, PASSES after
    the ops are implemented to return their delivery-metadata dict.
    """
    src = MESSAGE_OPS.read_text()
    tree = ast.parse(src)
    funcs_needing_return = {"send_directed", "send_broadcast", "send_topic"}
    found_return = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in funcs_needing_return:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and sub.value is not None:
                    found_return.add(node.name)
    missing = funcs_needing_return - found_return
    assert not missing, (
        f"these send ops are missing a value-returning return statement: {sorted(missing)}"
    )
