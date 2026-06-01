---
phase: 7
slug: project-scoped-reservation-registry
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-31
---

# Phase 7 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x (existing) + `tests/conftest.py` fixtures |
| **Config file** | `pyproject.toml` (existing) |
| **Quick run command** | `bash scripts/test.sh unit -k reserve` |
| **Full suite command** | `bash scripts/test.sh all` |
| **Estimated runtime** | ~15–20s (quick); ~60s (full) |

---

## Sampling Rate

- **After every task commit:** `bash scripts/test.sh unit -k reserve` or `-k upstream_identity` depending on touched file
- **After every plan wave:** `bash scripts/test.sh all`
- **Before `/gsd-verify-work`:** Full suite + `bash scripts/verify-phase.sh 07` both green
- **Max feedback latency:** 20s

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 07-NN-01 | TBD | 1 | RESERVE-01 | T-07-01 | `upstream_identity` resolver returns canonical `host:owner/repo` form for SSH/HTTPS/etc. inputs | unit | `bash scripts/test.sh unit -k upstream_identity` | ❌ W0 | ⬜ pending |
| 07-NN-02 | TBD | 1 | RESERVE-01 | T-07-01 | Falls back to `project_hash` when origin remote is missing | unit | `bash scripts/test.sh unit -k upstream_identity_no_origin` | ❌ W0 | ⬜ pending |
| 07-NN-03 | TBD | 1 | RESERVE-02 | T-07-02 | `reserve.py` pure-ops: 3 Lua scripts (TAKE/RELEASE/CHECK), 7-field holder, refresh-on-same-holder, exit-3 on different-holder | unit | `bash scripts/test.sh unit -k test_reserve` | ❌ W0 | ⬜ pending |
| 07-NN-04 | TBD | 2 | RESERVE-02, RESERVE-05 | T-07-03 | `reserve` verb auto-stamps `workstream` from `workstream.active` claim; `--workstream <name>` overrides | unit | `bash scripts/test.sh unit -k reserve_workstream` | ❌ W0 | ⬜ pending |
| 07-NN-05 | TBD | 2 | RESERVE-05 | T-07-04 | TTY-gated prompt on missing workstream; non-TTY exits 1 with actionable error | unit | `bash scripts/test.sh unit -k reserve_tty` | ❌ W0 | ⬜ pending |
| 07-NN-06 | TBD | 2 | RESERVE-03, RESERVE-04 | T-07-05 | `reserve-list` verb groups by category; `--category` filter; `--upstream` override | unit | `bash scripts/test.sh unit -k reserve_list` | ❌ W0 | ⬜ pending |
| 07-NN-07 | TBD | 2 | RESERVE-01, RESERVE-02 | T-07-06 | Two-clone race with per-child `cwd=` and fake `.git/config`; one wins, other gets exit 3 + winner's `workstream` in error | multi-process | `bash scripts/test.sh multiprocess -k reserve_race` | ❌ W0 | ⬜ pending |
| 07-NN-08 | TBD | 2 | RESERVE-03 (SC#3) | T-07-06 | Three-clone simulation: all three subprocesses see the same `reserve-list` output for the shared upstream | multi-process | `bash scripts/test.sh multiprocess -k reserve_three_clones_list` | ❌ W0 | ⬜ pending |
| 07-NN-09 | TBD | 3 | RESERVE-03, RESERVE-04 | T-07-05 | `/em-global-state reservations [--category <name>] [--upstream <id>]` verb in SKILL.md, parseable output, schema_version field | skill-doc | `bash scripts/test.sh structural -k phase_07_skill` | ❌ W0 | ⬜ pending |
| 07-NN-10 | TBD | 3 | (struct) | T-07-07 | Structural invariants: namespace disjointness (state:claim:* vs state:reserve:*), 3-Lua-scripts shape, multi-clone tests use cwd= | structural | `bash scripts/test.sh structural -k test_phase_07_shape` | ❌ W0 | ⬜ pending |
| 07-NN-11 | TBD | 3 | (gate) | — | `bash scripts/verify-phase.sh 07` exits 0 | dispatcher | `bash scripts/verify-phase.sh 07` | ✅ (script exists) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

*Task IDs (07-NN-XX) will be finalized by the planner; wave/plan columns will be populated when PLAN.md files exist.*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_upstream_identity.py` — canonicalizer test vectors (SSH, HTTPS, .git suffix, trailing slash, user-info, port) + no-origin fallback
- [ ] `tests/unit/test_reserve.py` — pure ops: take/release/check/refresh/exit-3 (mirror of `test_claim.py`)
- [ ] `tests/unit/test_reserve_verbs.py` — verb-level: workstream auto-stamp, `--workstream` override, TTY prompt (monkeypatched stdin), non-TTY exit 1, exit-code envelope
- [ ] `tests/multiprocess/test_reserve_race.py` — two clones with distinct `cwd=` + fake `.git/config`, race, deterministic serialization, winner's workstream visible in loser's error
- [ ] `tests/multiprocess/test_reserve_three_clones_list.py` — SC#3 demo: three clones see identical `reserve-list` output
- [ ] `tests/structural/test_phase_07_shape.py` — namespace disjointness, 3-Lua-script shape, multi-clone cwd assertion, SUMMARY coverage

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| End-to-end: three real sibling clones of an actual GitHub repo see shared reservations | RESERVE-01, RESERVE-03 | Validates `git remote get-url origin` resolution against the *real* git binary in *real* clone directories, beyond what the fake-`.git/config` fixture covers | 1. Set up three clones of `git@github.com:emonical/em-proj.git` in `/tmp/em-proj-{a,b,c}/`<br>2. From each, run `gsd-sdk query workstream.set <name>` with distinct names<br>3. From clone-a: `em-proj state reserve migrations.v200 --reason "test"`<br>4. From clone-b and clone-c: `/em-global-state reservations` — verify both see the v200 entry with `workstream=<a-name>`<br>5. From clone-b: `em-proj state reserve migrations.v200` — should fail exit 3 with workstream-a in error<br>6. Cleanup |
| TTY prompt UX matches user expectation | RESERVE-05 | Subjective — does the prompt copy + flow feel natural? | 1. In a fresh terminal with no `workstream.active` claim set, run `em-proj state reserve test.thing`<br>2. Observe prompt copy + behavior on empty input vs valid name<br>3. Confirm exit-1 message on non-TTY is actionable (run from a non-interactive shell) |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 20s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
