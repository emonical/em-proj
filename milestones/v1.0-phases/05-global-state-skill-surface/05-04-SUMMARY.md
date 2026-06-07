---
phase: 05-global-state-skill-surface
plan: "04"
subsystem: cli-surface
tags: [em-global-state, skill, state-cli, locks, claims, kv, escape-hatch]

requires:
  - phase: 05-03
    provides: lock-list and claim-list CLI verbs (hyphenated form) in em-proj state

provides:
  - "/em-global-state skill at ~/.claude/skills/em-global-state/SKILL.md"
  - "6 verbs: list, get, locks, claims, unlock, release"
  - "AskUserQuestion confirmation gate for write verbs (unlock, release)"
  - "SKILL-01, SKILL-02, SKILL-03 user-facing surface"

affects:
  - 05-05  # structural test plan audits SKILL.md source text for forbidden write patterns

tech-stack:
  added: []
  patterns:
    - "em-* skill shape: YAML frontmatter (name, description, allowed-tools) + objective + when_to_invoke + action + scope + related"
    - "Write-verb confirmation gate via AskUserQuestion with --force bypass (D-04)"
    - "SC#3 audit invariant: skill body excludes set/del/claim/lock verbs; only unlock+release permitted"

key-files:
  created:
    - ~/.claude/skills/em-global-state/SKILL.md  # NOT tracked by em-proj git — lives in user's home dir
  modified: []

key-decisions:
  - "SKILL.md scope section uses paraphrase ('KV write operations (set / del)') instead of literal forbidden verb strings to pass SC#3 structural audit without self-violating"
  - "Verb names confirmed as lock-list and claim-list (hyphenated) from 05-03-SUMMARY.md and state/__init__.py source"
  - "AskUserQuestion confirmation required for unlock and release unless --force passed (D-04 locked decision)"
  - "skill NEVER shells out to lock-acquire, claim-acquire, set, or del verbs — write surface limited to unlock+release only"

patterns-established:
  - "em-global-state skill: canonical surface for state inspection and emergency escape hatch; not a general-purpose mutation tool"

requirements-completed: [SKILL-01, SKILL-02, SKILL-03]

duration: 15min
completed: 2026-05-26
---

# Phase 5 Plan 04: em-global-state Skill — Summary

**Created `~/.claude/skills/em-global-state/SKILL.md` with 6 verbs (list, get, locks, claims, unlock, release) wrapping the em-proj state CLI, with AskUserQuestion confirmation gates for write verbs and explicit SC#3 write-surface restriction.**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-05-26T04:00:00Z
- **Completed:** 2026-05-26T04:00:23Z
- **Tasks:** 1
- **Files modified:** 1 (system file outside em-proj repo)

## Accomplishments

- Created `~/.claude/skills/em-global-state/SKILL.md` at the correct absolute path.
- Documented all 6 `/em-global-state` verbs with exact `em-proj state` CLI invocations and output schemas.
- Implemented AskUserQuestion confirmation flow for `unlock` and `release` write verbs (D-04 locked decision).
- Enforced SC#3 write-surface audit invariant: `em-proj state unlock` and `em-proj state release` present; `em-proj state set`, `em-proj state del`, lock-acquire verb, and claim-acquire verb absent from skill body.
- Confirmed exact CLI verb names (`lock-list`, `claim-list`) from 05-03-SUMMARY.md and `state/__init__.py` source.

## Task Commits

This plan produces no em-proj git commits — the SKILL.md artifact lives at
`/Users/emonical/.claude/skills/em-global-state/SKILL.md`, which is outside the
em-proj repository and not tracked by git.

The SUMMARY.md commit (planning branch) is the sole commit artifact for this plan:

1. **Task 1: Create ~/.claude/skills/em-global-state/SKILL.md** — no em-proj commit (system-state change outside repo)

**Plan metadata:** see planning branch commit for this SUMMARY.md.

## Files Created/Modified

- `/Users/emonical/.claude/skills/em-global-state/SKILL.md` — em-global-state skill; wraps `em-proj state` CLI; 6 verbs; NOT tracked by em-proj git.

## Decisions Made

- Confirmed verb names as `lock-list` and `claim-list` (hyphenated) from Wave 2 decisions and live `state/__init__.py` source (`@state_app.command("lock-list")`, `@state_app.command("claim-list")`).
- Used paraphrase in `<scope>` block's NEVER section ("KV write operations (`set` / `del`)") rather than the literal command strings, so the SKILL.md itself passes the SC#3 structural audit that 05-05 will enforce.
- AskUserQuestion confirmation flow: probe live holder first (`lock-list --json` for unlock, `check <area> --json` for release), then confirm; `--force` bypasses prompt.

## Deviations from Plan

None — plan executed exactly as written.

One minor auto-fix applied inline: the initial SKILL.md draft included literal forbidden strings (`em-proj state set`, `em-proj state del`) in the `<scope>` NEVER section as explanatory text. These were immediately rewritten to a paraphrase before the file was finalized, keeping all acceptance criteria passing. This is not a deviation from the plan's intent; the plan's acceptance_criteria require these strings to be absent from the file, and the parallel execution note allows their presence "only as quoted explanatory text if absolutely required" — since a paraphrase works, the stricter path was taken.

## Issues Encountered

None.

## User Setup Required

None — the skill file is written to `~/.claude/skills/em-global-state/SKILL.md`; no external service configuration or environment variables required. The skill itself requires `em-proj` installed via `uv tool install --editable .` from the em-proj repo, but that is an existing prerequisite.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. This plan is a pure markdown documentation artifact. The threat model entries from the plan's `<threat_model>` are addressed:

- **T-5-04-01** (unlock --force without confirmation): accepted — `--force` is an explicit operator escape hatch; SKILL.md documents the displacement risk.
- **T-5-04-02** (skill write surface beyond unlock/release): mitigated — SKILL.md body excludes all forbidden verb strings; SC#3 structural test in 05-05 will audit the source text.
- **T-5-04-03** (skill emits holder output verbatim): accepted — CLI applies `_HOLDER_DISCLOSURE_KEYS` redaction at the verb layer before skill receives output.

## Known Stubs

None — SKILL.md documents real CLI invocations that are live after Plans 05-01 through 05-03.

## Next Phase Readiness

- `~/.claude/skills/em-global-state/SKILL.md` is ready for structural audit in Plan 05-05.
- Plan 05-05 (`test-skill-audit`) will add `tests/structural/test_05_skill_audit.py` to verify: SKILL.md exists at the correct path, all 6 verb patterns present, forbidden write-verb strings absent, `em-proj state unlock` and `em-proj state release` present.
- No blockers.

## Self-Check

- [x] `/Users/emonical/.claude/skills/em-global-state/SKILL.md` exists at correct absolute path
- [x] Frontmatter `name: em-global-state` present
- [x] `allowed-tools` includes `Bash` and `AskUserQuestion`
- [x] All 6 verb subsections present: list, get, locks, claims, unlock, release
- [x] `em-proj state lock-list` (not `lock list`) — correct hyphenated form confirmed
- [x] `em-proj state claim-list` (not `claim list`) — correct hyphenated form confirmed
- [x] `em-proj state unlock` appears (write verb permitted)
- [x] `em-proj state release` appears (write verb permitted)
- [x] `em-proj state set` does NOT appear
- [x] `em-proj state del` does NOT appear
- [x] `em-proj state claim ` (with trailing space) does NOT appear
- [x] `em-proj state lock ` (with trailing space) does NOT appear
- [x] `AskUserQuestion` appears in unlock and release sections
- [x] `--force` appears in unlock and release sections
- [x] No changes to STATE.md, ROADMAP.md, or config.json
- [x] No code files changed on em-proj main branch

## Self-Check: PASSED
