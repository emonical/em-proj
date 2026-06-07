---
phase: 6
slug: gsd-sdk-workstream-consumer
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-26
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x (existing) + `tests/conftest.py` fixtures |
| **Config file** | `pyproject.toml` (existing) |
| **Quick run command** | `bash scripts/test.sh multiprocess -k workstream` |
| **Full suite command** | `bash scripts/test.sh all` |
| **Estimated runtime** | ~10–15 seconds (quick); ~60s (full) |

---

## Sampling Rate

- **After every task commit:** Run `bash scripts/test.sh multiprocess -k workstream`
- **After every plan wave:** Run `bash scripts/test.sh all`
- **Before `/gsd-verify-work`:** Full suite + `bash scripts/verify-phase.sh 06` both green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 06-NN-01 | TBD | 1 | CONSUMER-01 | T-06-01 | `workstreamSet` invokes `em-proj state claim` before `writeFileSync` | structural | `bash scripts/test.sh structural -k test_gsd_sdk_workstream_js_contains_em_proj_shellout` | ❌ W0 | ⬜ pending |
| 06-NN-02 | TBD | 1 | CONSUMER-02 | T-06-02 | Two-session race serializes deterministically; loser gets exit 3 + holder dict | multi-process | `bash scripts/test.sh multiprocess -k test_two_sessions_race_workstream_set_one_wins` | ❌ W0 | ⬜ pending |
| 06-NN-03 | TBD | 1 | SC#3 | T-06-02 | Side-by-side: old direct-write clobbers; new gsd-sdk path refuses loser | multi-process | `bash scripts/test.sh multiprocess -k clobber_demo` | ❌ W0 | ⬜ pending |
| 06-NN-04 | TBD | 1 | CONSUMER-01 | T-06-03 | gsd-sdk fallback path triggers cleanly on ENOENT (`em-proj` missing) | multi-process | `bash scripts/test.sh multiprocess -k missing_em_proj_fallback` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

*Task IDs (06-NN-XX) will be finalized by the planner; wave/plan column will be populated when PLAN.md files exist.*

---

## Wave 0 Requirements

- [ ] `tests/multiprocess/test_workstream_consumer_race.py` — covers CONSUMER-02
- [ ] `tests/multiprocess/test_workstream_clobber_demo.py` — covers SC#3 (clobber-vs-resolution side-by-side)
- [ ] `tests/multiprocess/test_workstream_consumer_fallback.py` (or merged into race test) — covers Q-B silent-fallback behavior
- [ ] `tests/structural/test_phase_06_shape.py` — covers CONSUMER-01 structurally (shellout presence in `sdk/dist/query/workstream.js`, both `.js` and `.ts` per Q-C)
- [ ] `conftest.py` extension OR module-level skip for `gsd-sdk` PATH probe (small; mirrors existing `redis_precheck` fixture pattern)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `gsd-sdk query workstream.set` end-to-end against live Redis from a real Claude Code session | CONSUMER-01, CONSUMER-02 | Final smoke against the real install path (`/Users/emonical/.nvm/.../get-shit-done-cc/`); validates that the npm-installed copy was patched correctly | 1. From this session, run `gsd-sdk query workstream.set test-ws-A`<br>2. Verify `/em-global-state claims --mine` shows the claim<br>3. From a second Claude Code session in the same project, run `gsd-sdk query workstream.set test-ws-B` and verify it surfaces "held by session" (does NOT clobber)<br>4. Run `gsd-sdk query workstream.set test-ws-A` again from session 1 — should refresh, not error |
| `/em-global-state claims --mine` displays the workstream claim with correct holder metadata | CONSUMER-01 | Skill-layer integration — sub-agent consumability validated by inspection | After `workstream.set test-ws`, run `/em-global-state claims --mine`; assert output includes `area: workstream:<project_hash>` (or equivalent), `reason: workstream`, fresh `claimed_at`/`expires_at` |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
