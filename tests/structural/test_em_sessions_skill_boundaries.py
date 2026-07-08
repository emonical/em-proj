"""Write-boundary invariant for the /em-sessions skill (SKILL-04, SKILL-05).

`docs/em-sessions-skill.md` is the in-repo staging artifact for
`~/.claude/skills/em-sessions/SKILL.md` (orchestrator-applied — the
gsd-executor permission scope denies writes under `~/.claude/skills/`, so
this repo can never verify the deployed copy directly; it verifies the
staged content instead).

The locked decision this file protects: the skill may DESCRIBE `state set`,
`state del`, `session register`, `session listen`, and `session stop` in
prose (explaining what is excluded from its surface), but it must NEVER show
any of them as an actual command inside a fenced code block. If a future
edit ever adds one of those verbs to a `bash` example, this test goes red —
the write boundary would otherwise only be a documentation convention, not a
durable guarantee.

Self-contained — no imports from sibling structural test modules.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_DOC = REPO_ROOT / "docs" / "em-sessions-skill.md"

_FORBIDDEN_INVOCATIONS = (
    "state set",
    "state del",
    "session register",
    "session listen",
    "session stop",
)

_FENCE_RE = re.compile(r"```(?:bash|json)?\n(.*?)```", re.DOTALL)


def test_em_sessions_skill_doc_exists_and_has_frontmatter() -> None:
    src = SKILL_DOC.read_text()
    assert src.startswith("---"), "docs/em-sessions-skill.md must open with YAML frontmatter"
    assert "name: em-sessions" in src, "frontmatter must declare name: em-sessions"


def test_em_sessions_skill_fenced_commands_never_invoke_forbidden_verbs() -> None:
    src = SKILL_DOC.read_text()
    fenced_blocks = _FENCE_RE.findall(src)
    fenced_text = "\n".join(fenced_blocks)
    for forbidden in _FORBIDDEN_INVOCATIONS:
        assert forbidden not in fenced_text, (
            f"{forbidden!r} found inside a fenced command example in "
            "docs/em-sessions-skill.md — this crosses the SKILL-04/05 locked "
            "write boundary (state set/del, lock/claim acquire, and session "
            "register/listen/stop must never appear as an actual command to "
            "run; the boundary may only be described in prose)."
        )


def test_em_sessions_skill_documents_the_never_boundary() -> None:
    src = SKILL_DOC.read_text()
    assert "NEVER" in src, "docs/em-sessions-skill.md must document its NEVER boundary"
    assert "state set" in src, (
        "the NEVER section must name 'state set' as an excluded verb in prose "
        "(only the fenced-block check restricts where it may NOT appear)"
    )
