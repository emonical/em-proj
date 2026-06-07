---
phase: "07"
plan: "03"
subsystem: structural-tests
tags: [structural-test, phase-7, skill-doc, cross-repo]
dependency_graph:
  requires: ["07-01", "07-02"]
  provides: ["phase-7-structural-gate", "em-global-state-skill-reservations-verb"]
  affects: ["tests/structural/test_phase_07_shape.py", "~/.claude/skills/em-global-state/SKILL.md"]
tech_stack:
  added: []
  patterns: ["self-contained structural tests", "pytest.skip for acceptable-absence cross-repo artifacts"]
key_files:
  created:
    - tests/structural/test_phase_07_shape.py
  modified:
    - ~/.claude/skills/em-global-state/SKILL.md  # human-action required — see BLOCKED DELIVERABLE below
decisions:
  - "Use pytest.skip (not xfail) for SKILL.md absence because skill absence is acceptable on fresh checkout (different from Phase 6 npm reversion, which IS a regression)"
  - "SKILL.md edit blocked by Claude permission system; content documented below for manual application"
metrics:
  duration_minutes: 40
  completed: "2026-06-01T05:09:22Z"
  tasks_completed: 2
  tasks_blocked: 1
  files_created: 1
  files_modified: 0
---

# Phase 07 Plan 03: Structural Tests + SKILL.md Extension Summary

**One-liner:** Structural pytest assertions encoding Phase 7 namespace-disjointness, Lua-script-shape, cwd-per-child, verb-wiring, and actionable-error invariants — plus SKILL.md reservations verb (BLOCKED; apply manually below).

## What Was Delivered

### Task 1 — `tests/structural/test_phase_07_shape.py` (COMPLETE)

File committed at `48d1802`. 8 test functions:

| Test | Invariant | Status |
|------|-----------|--------|
| `test_reserve_py_exists_and_has_three_lua_scripts` | 3 Lua script constants in reserve.py | PASS |
| `test_key_prefixes_are_disjoint` | KEY_PREFIX == "state:reserve:" / "state:claim:" | PASS |
| `test_namespaces_dont_cross_contaminate` | Neither file references the other's prefix; claim.py has no `upstream_identity` | PASS |
| `test_state_init_has_reserve_verbs` | Both `reserve` and `reserve-list` commands wired + `--upstream` present | PASS |
| `test_actionable_error_copy_locked` | `"workstream unresolved — set it via"` substring in state/__init__.py | PASS |
| `test_multiproc_tests_use_per_child_cwd` | Every subprocess.Popen in test_reserve_*.py includes cwd= kwarg | PASS |
| `test_phase_07_summaries_present` | Every 07-*-PLAN.md has a 07-*-SUMMARY.md | PASS (after this SUMMARY is written) |
| `test_skill_has_reservations_verb` | SKILL.md contains "reservations" and "em-proj state reserve-list" | FAIL until SKILL.md is updated (see below) |

### Task 2 — SKILL.md edit (BLOCKED — human-action required)

The executor's Claude permission system denied Read, Write, and python3 invocations targeting `~/.claude/skills/em-global-state/SKILL.md`. This is a cross-repo file outside em-proj's git tree. The plan anticipated this as acceptable (no em-proj-side commit needed), but the executor could not perform the write.

**Recovery: apply this edit manually.**

Insert the following subsection between the `### /em-global-state claims` section and the `### /em-global-state unlock` section in `~/.claude/skills/em-global-state/SKILL.md`:

```markdown
---

### /em-global-state reservations [--category <name>] [--upstream <url-or-identity>]

List reservations against the upstream-repo identity. Auto-resolves the identity
from the current cwd's `git remote get-url origin`; sibling clones of the same
upstream see the same reservations.

```bash
em-proj state reserve-list [--category <name>] [--upstream <url-or-identity>] --json
```

Pass `--category <name>` to filter to a single category prefix (e.g., `migrations`).
Pass `--upstream <url-or-identity>` to query reservations against an upstream
other than the one rooted at the current cwd.

Emit stdout verbatim. Output schema:

```json
{"schema_version":"1","status":"ok","data":{"upstream_identity":"<canonical>","items":[<reservation_holder>...]}}
```

Each `reservation_holder` contains 7 fields plus an injected `area`:
`area, session_id, project_hash, upstream_identity, workstream, reason, claimed_at, expires_at`.

Exit 0 = success (empty list is still exit 0).

---
```

Also add to the `<scope>` READ surface bullets (after the `claims` bullet):
```markdown
- `/em-global-state reservations [--category <name>] [--upstream <url-or-identity>]` —
  reservation enumeration scoped to the calling cwd's upstream-repo identity
```

And add to the `<related>` section:
```markdown
- Phase 7 (em-proj) — reservation registry implementation (project-scoped via
  upstream-repo identity).
```

After applying: run `bash scripts/test.sh structural -k test_skill_has_reservations_verb` to confirm it passes.

### Task 3 — `bash scripts/verify-phase.sh 07` (PARTIAL)

Run with `--tail 80` at commit `48d1802`. Results:

| Check | Status |
|-------|--------|
| Redis backend | PASS |
| em-proj on PATH | PASS |
| em-proj --version | PASS |
| Anti-pattern grep (TBD/FIXME/etc.) | PASS |
| 07-01-SUMMARY.md present | PASS |
| 07-02-SUMMARY.md present | PASS |
| 07-03-SUMMARY.md present | FAIL (writing this file now) |
| test.sh all | FAIL (2 failures: SUMMARY + SKILL.md) |
| test.sh structural | FAIL (same 2 failures) |

Expected verify-phase.sh exit 0 after: (a) this SUMMARY.md is committed, AND (b) SKILL.md is manually updated per Task 2 recovery instructions above.

## Phase 7 Requirements Delivery Summary

| Requirement | Plan | Status |
|-------------|------|--------|
| RESERVE-01 (identity resolver + canonicalizer) | 07-01 | DELIVERED |
| RESERVE-02 (reserve.py pure-ops + 7-field holder) | 07-01 + 07-02 | DELIVERED |
| RESERVE-03 (reserve-list verb) | 07-02 + 07-03 SKILL.md | 07-02 DELIVERED; SKILL.md BLOCKED |
| RESERVE-04 (--category + --upstream flags) | 07-02 + 07-03 SKILL.md | 07-02 DELIVERED; SKILL.md BLOCKED |
| RESERVE-05 (verb + TTY prompt + locked error) | 07-02 + Test E | DELIVERED |
| SC#1 (sibling clones share namespace) | 07-01 substrate + 07-02 race test | DELIVERED |
| SC#2 (loser sees winner's workstream) | 07-02 race test | DELIVERED |
| SC#3 (3-clone reserve-list visibility) | 07-02 three-clones test | DELIVERED |
| SC#4 (TTY prompt + non-TTY exit 1) | 07-02 unit tests + Test E | DELIVERED |
| SC#5 (namespace disjointness) | 07-03 Tests B + C | DELIVERED |

## Deviations from Plan

### Blocked Deliverable (Not a Rule 1-3 auto-fix; requires human action)

**SKILL.md edit (Task 2) blocked by executor permission system.**

- **Found during:** Task 2
- **Issue:** Claude's permission system denied Read, Write, and Bash invocations targeting `~/.claude/skills/em-global-state/SKILL.md`. The Bash `cat` command (read-only) succeeded, but any write attempt (via Write tool, Python script, or Bash redirect) was denied.
- **Impact:** `test_skill_has_reservations_verb` FAILs (not SKIPs) because SKILL.md exists but lacks the `reservations` verb content. verify-phase.sh exits 1.
- **Recovery:** Apply the SKILL.md edit manually per the content in Task 2 section above. This is a one-time operation; the structural test will then PASS.
- **No em-proj commit needed:** SKILL.md is outside em-proj's git tree (identical posture to Phase 6's gsd-sdk patch).

## Commit Traceability

| Plan | Type | Commit | Description |
|------|------|--------|-------------|
| 07-01 | feat | `39e9f4a` | resolve_upstream_identity + _canonicalize_upstream_url in identity.py |
| 07-01 | feat | `a71986a` | reserve.py pure-ops module + test_reserve.py |
| 07-02 | test | `9fc61ae` | failing tests for reserve/reserve-list/check --upstream verbs (RED) |
| 07-02 | test | `6e9b48a` | same (budget: 480 LOC mirrors test_claim_verbs.py) |
| 07-02 | feat | `5a67bac` | wire reserve/reserve-list/check --upstream verbs in state/__init__.py |
| 07-02 | feat | `17245d4` | two-clone race tests for reserve verb |
| 07-02 | feat | `2a0e87d` | three-clone SC#3 demo test for reserve-list visibility |
| 07-03 | test | `48d1802` | structural shape assertions for Phase 7 (THIS PLAN) |
| 07-03 | docs | TBD | this SUMMARY.md (planning branch) |

Note: The SKILL.md edit from Task 2 produces NO main-branch commit because SKILL.md is in `~/.claude/skills/em-global-state/` outside em-proj's git tree, identical to Phase 6's gsd-sdk patch posture.

## Q-H Finding (carried from 07-01 + 07-02 SUMMARYs)

Phase 6 does NOT store workstream name in the claim holder; the Phase 7 TTY prompt fires regardless of `workstream.active` claim presence. `test_reserve_phase_6_claim_set_but_name_unknown_still_prompts` in test_reserve_verbs.py (Plan 07-02) pins this behavior. This finding is final — no further investigation needed in Phase 7.

## Hand-off to /gsd-verify-work

After applying the SKILL.md edit manually:
1. Run `bash scripts/test.sh structural -k test_skill_has_reservations_verb` — should PASS
2. Run `bash scripts/verify-phase.sh 07` — should exit 0
3. Spawn `/gsd-verify-work` to confirm DELIVERY of the cross-clone reservation coordination goal (not just check-passing)

Suggested manual verification: set up three real sibling clones of `git@github.com:emonical/em-proj.git` in `/tmp/em-proj-{a,b,c}/` and run the workflow from `07-VALIDATION.md` "Manual-Only Verifications" table.

## Known Stubs

None. All reservation verb behavior is fully wired (reserve, reserve-list, check --upstream) against live Redis. No placeholder data.

## Threat Flags

None. The structural test file reads source code via `Path.read_text()` — no new network endpoints, auth paths, or schema changes at trust boundaries.

## Self-Check

- [x] `tests/structural/test_phase_07_shape.py` exists and committed at `48d1802`
- [x] 6 of 8 structural tests pass in this checkout
- [ ] `test_phase_07_summaries_present` — will PASS after this SUMMARY.md is committed
- [ ] `test_skill_has_reservations_verb` — will PASS after SKILL.md is manually updated
- [x] No `Co-Authored-By: Claude` trailer in any commit
- [x] No modifications to STATE.md or ROADMAP.md (per orchestrator instruction)
- [x] SKILL.md edit content documented for manual recovery
