from __future__ import annotations
"""Phase 9 structural invariants — source-grep and file-presence assertions.

Encodes Phase 9 plan acceptance criteria as runtime pytest assertions:
  - message ops module file presence and required function names (MBOX-01..04)
  - MBOX_KEY_PREFIX value 'mbox:' locked in source
  - Prohibited imports in message ops module (typer, multiprocessing, threading)
  - message_app wired in cli.py (import + add_typer = at least 2 occurrences)
  - message/__init__.py has at least 1 @message_app.command decorator
  - Uses streams (xadd + xrange present); no consumer groups (no xreadgroup/xack)
  - SUMMARY coverage: every 09-*-PLAN.md has a matching 09-*-SUMMARY.md

Each structural file is self-contained (no imports from sibling
test_phase_*_shape.py files) per Phase 1+2+3+4+5+6+7+8 precedent.
"""

from pathlib import Path

import pytest

# Self-contained — no imports from sibling test_phase_*_shape.py files.

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "09-durable-mailbox-transport"

MESSAGE_OPS = REPO_ROOT / "src" / "em_proj" / "message" / "_ops.py"
MESSAGE_INIT = REPO_ROOT / "src" / "em_proj" / "message" / "__init__.py"
CLI_PY = REPO_ROOT / "src" / "em_proj" / "cli.py"


# ---------------------------------------------------------------------------
# Test A — message ops module exists and has required function names
# ---------------------------------------------------------------------------


def test_message_ops_module_exists_and_has_required_functions() -> None:
    """Plan 09-02 must create message/_ops.py with all required public ops.

    Asserts file existence and function name presence via source-text grep.
    Covers MBOX-02 (mbox_write, mailbox_inbox), MBOX-03 (bounded growth + TTL),
    and the private helper/decoder functions required by the implementation.
    """
    assert MESSAGE_OPS.exists(), (
        f"message/_ops.py not found at {MESSAGE_OPS} — "
        "Plan 09-02 must create the message ops module"
    )
    src = MESSAGE_OPS.read_text()

    assert "def mbox_write" in src, (
        "mbox_write missing from message/_ops.py — MBOX-02 write op not satisfied"
    )
    assert "def mailbox_inbox" in src, (
        "mailbox_inbox missing from message/_ops.py — MBOX-02 read op not satisfied"
    )
    assert "def _build_mbox_key" in src, (
        "_build_mbox_key missing from message/_ops.py — key builder helper required"
    )
    assert "def _decode_entry" in src, (
        "_decode_entry missing from message/_ops.py — stream entry decoder required"
    )


# ---------------------------------------------------------------------------
# Test B — message/__init__.py exists
# ---------------------------------------------------------------------------


def test_message_init_exists() -> None:
    """Plan 09-02 must create message/__init__.py as the Typer mount module.

    The message package must exist with its __init__.py for `em-proj message`
    subcommand family to be importable and mountable in cli.py.
    """
    assert MESSAGE_INIT.exists(), (
        f"message/__init__.py not found at {MESSAGE_INIT} — "
        "Plan 09-02 must create the message package"
    )


# ---------------------------------------------------------------------------
# Test C — MBOX_KEY_PREFIX is 'mbox:'
# ---------------------------------------------------------------------------


def test_mbox_key_prefix_is_mbox_colon() -> None:
    """message/_ops.py must define MBOX_KEY_PREFIX = 'mbox:'.

    The 'mbox:' prefix is the A1 assumption in 09-RESEARCH.md (no namespace
    collision with state:claim:, state:lock:, state:reserve:, state:session:).
    A future refactor that changes the prefix would orphan all existing mailbox keys.
    """
    assert MESSAGE_OPS.exists(), "message/_ops.py missing — cannot check MBOX_KEY_PREFIX"
    src = MESSAGE_OPS.read_text()

    assert "MBOX_KEY_PREFIX" in src, (
        "MBOX_KEY_PREFIX constant missing from message/_ops.py"
    )
    assert '"mbox:"' in src or "'mbox:'" in src, (
        "MBOX_KEY_PREFIX must be set to 'mbox:' — "
        "namespace isolation invariant (09-RESEARCH.md A1)"
    )


# ---------------------------------------------------------------------------
# Test D — message ops module prohibits forbidden imports
# ---------------------------------------------------------------------------


def test_message_ops_prohibits_forbidden_imports() -> None:
    """message/_ops.py must not import typer, multiprocessing, or threading.

    These imports are forbidden by the D-14 thin-verb-shell discipline:
    - typer   → belongs in message/__init__.py (the mount module), not ops
    - multiprocessing → Phase 1 Pitfall #6 (fork+exec safety on macOS)
    - threading → not required; would mask concurrency issues

    Filter to actual import lines (startswith 'import' or 'from') to avoid
    false positives from commented-out lines or docstring references.
    """
    assert MESSAGE_OPS.exists(), "message/_ops.py missing — cannot check imports"
    src = MESSAGE_OPS.read_text()

    import_lines = [
        line.strip()
        for line in src.splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]

    for forbidden in ("typer", "multiprocessing", "threading"):
        for line in import_lines:
            assert forbidden not in line, (
                f"Forbidden import '{forbidden}' found in message/_ops.py: {line!r}. "
                f"The {forbidden!r} import must NOT appear in the ops module; "
                "it belongs in message/__init__.py (typer) or is prohibited "
                "(multiprocessing/threading)."
            )


# ---------------------------------------------------------------------------
# Test E — message_app wired in cli.py
# ---------------------------------------------------------------------------


def test_message_app_wired_in_cli() -> None:
    """cli.py must import and mount message_app (import + add_typer = >= 2 occurrences).

    Plan 09-02 wires message_app into cli.py. At least two references
    to 'message_app' are required: the import line and the add_typer call.
    """
    assert CLI_PY.exists(), f"cli.py missing at {CLI_PY}"
    cli_src = CLI_PY.read_text()

    assert "message_app" in cli_src, (
        "cli.py does not reference 'message_app' — "
        "Plan 09-02 message_app mount not applied"
    )
    assert cli_src.count("message_app") >= 2, (
        f"'message_app' appears {cli_src.count('message_app')} time(s) in cli.py; "
        "expected >= 2 (import line + add_typer call). "
        "Plan 09-02 mount incomplete."
    )


# ---------------------------------------------------------------------------
# Test F — message/__init__.py has at least one inbox command
# ---------------------------------------------------------------------------


def test_message_init_has_inbox_command() -> None:
    """message/__init__.py must have >= 1 @message_app.command decorator.

    Plan 09-02 creates the inbox verb as the first MBOX-02 CLI entry point.
    At least one @message_app.command must be present.
    """
    assert MESSAGE_INIT.exists(), (
        f"message/__init__.py missing at {MESSAGE_INIT} — "
        "Plan 09-02 must create the message package with verb commands"
    )
    init_src = MESSAGE_INIT.read_text()

    command_count = init_src.count("@message_app.command")
    assert command_count >= 1, (
        f"@message_app.command appears {command_count} time(s) in message/__init__.py; "
        "expected >= 1 (at minimum the inbox command must be present)"
    )


# ---------------------------------------------------------------------------
# Test G — Uses streams (xadd + xrange), no consumer groups (no xreadgroup/xack)
# ---------------------------------------------------------------------------


def test_uses_streams_not_list() -> None:
    """message/_ops.py must use Redis Streams (xadd + xrange), not Lists.

    Per 09-RESEARCH.md locked decision: plain XRANGE + XDEL (no consumer groups).
    - 'xadd' and 'xrange' must appear (case-insensitive) in the source.
    - 'xreadgroup' and 'xack' must NOT appear — consumer groups are explicitly
      out of scope for Phase 9 (single-consumer mailbox; see research Anti-Patterns).
    """
    assert MESSAGE_OPS.exists(), "message/_ops.py missing — cannot check stream usage"
    src = MESSAGE_OPS.read_text().lower()

    assert "xadd" in src, (
        "'xadd' not found in message/_ops.py — XADD is required for mailbox writes (MBOX-02)"
    )
    assert "xrange" in src, (
        "'xrange' not found in message/_ops.py — XRANGE is required for mailbox reads (MBOX-02)"
    )
    assert "xreadgroup" not in src, (
        "'xreadgroup' found in message/_ops.py — consumer groups are explicitly prohibited "
        "(09-RESEARCH.md Anti-Patterns: single-consumer mailbox does not need PEL overhead)"
    )
    assert "xack" not in src, (
        "'xack' found in message/_ops.py — consumer group ack is explicitly prohibited "
        "(09-RESEARCH.md Anti-Patterns: use XDEL for consume-ack, not XACK)"
    )


# ---------------------------------------------------------------------------
# Test H — SUMMARY coverage: every 09-*-PLAN.md has a 09-*-SUMMARY.md
# ---------------------------------------------------------------------------


def test_phase_09_summaries_exist() -> None:
    """SUMMARY coverage check: every 09-*-PLAN.md must have a 09-*-SUMMARY.md sibling.

    Uses pytest.skip (NOT xfail) when PHASE_DIR is absent — planning worktree
    may not be attached on this checkout, which is a legitimate developer setup
    (not a regression).

    The summary files from Plans 09-01, 09-02, 09-03 are the machine-checkable
    proof that each plan's executor completed and wrote a Summary.
    """
    if not PHASE_DIR.exists():
        pytest.skip(
            f"{PHASE_DIR.relative_to(REPO_ROOT)} not present — "
            "planning worktree may not be attached on this checkout"
        )
    plans = sorted(PHASE_DIR.glob("09-*-PLAN.md"))
    if not plans:
        pytest.skip(
            f"no 09-*-PLAN.md files yet under "
            f"{PHASE_DIR.relative_to(REPO_ROOT)}"
        )
    for plan in plans:
        summary = plan.parent / plan.name.replace("-PLAN.md", "-SUMMARY.md")
        assert summary.exists(), (
            f"Missing SUMMARY for {plan.name}: expected {summary.name} "
            f"in {PHASE_DIR.relative_to(REPO_ROOT)}"
        )
