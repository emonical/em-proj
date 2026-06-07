---
phase: 9
slug: durable-mailbox-transport
status: planned
nyquist_compliant: true
wave_0_complete: false
created: 2026-06-07
updated: 2026-06-07
---

# Phase 9 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `bash scripts/test.sh unit` |
| **Full suite command** | `bash scripts/test.sh all` |
| **Structural command** | `bash scripts/test.sh structural` |
| **Estimated runtime** | ~5 seconds (unit only), ~15 seconds (all) |

---

## Sampling Rate

- **After every task commit:** Run `bash scripts/test.sh unit`
- **After every plan wave:** Run `bash scripts/test.sh all`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** < 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 09-01-T1 | 01 | 0 | MBOX-02, MBOX-03, MBOX-04 | T-09-01-01 | EM_PROJ_REDIS_DB=15 forced by autouse fixture; no prod Redis writes | unit | `bash scripts/test.sh collect` | ❌ W0 | ⬜ pending |
| 09-01-T2 | 01 | 0 | MBOX-01 | T-09-01-03 | structural read-only; multiprocess test skips (Phase 10 stub) | structural + multiprocess | `bash scripts/test.sh collect` | ❌ W0 | ⬜ pending |
| 09-02-T1 | 02 | 1 | MBOX-03, MBOX-04 | T-09-02-01, T-09-02-02 | body cap enforced; MAXLEN prevents flood; EXPIRE set on write | unit | `bash scripts/test.sh unit -k "mailbox and write"` | ❌ W0 | ⬜ pending |
| 09-02-T2 | 02 | 1 | MBOX-02 | T-09-02-05 | exclusive '(' range or Lua fallback confirmed by test_since_excludes_already_seen | unit | `bash scripts/test.sh unit -k "mailbox"` | ❌ W0 | ⬜ pending |
| 09-03-T1 | 03 | 2 | MBOX-02 | T-09-03-01, T-09-03-02 | session_id from env var; --since passed safely to ops layer | unit + structural | `bash scripts/test.sh unit -k "mailbox"` | ❌ W0 | ⬜ pending |
| 09-03-T2 | 03 | 2 | MBOX-02 | T-09-03-04 | no --session-id flag; inbox reads only caller's own mailbox | structural | `bash scripts/test.sh structural` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_mailbox.py` — unit tests for MBOX-02, MBOX-03, MBOX-04 + exclusive-range probe (Plan 09-01 Task 1)
- [ ] `tests/structural/test_phase_09_shape.py` — structural shape assertions for Phase 9 (Plan 09-01 Task 2)
- [ ] `tests/multiprocess/test_mailbox_durability.py` — MBOX-01 durability stub (skips until Phase 10, Plan 09-01 Task 2)

*Wave 0 creates all three test files. RED phase: test_mailbox.py fails with ImportError on em_proj.message._ops (does not exist yet). Structural tests fail for missing message/ package. Multiprocess test skips cleanly.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification or are deferred to Phase 10.*

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Redis XDEL does not immediately update XLEN | MBOX-03 | Pitfall 4: test asserts XRANGE=[] not xlen==0; the XLEN behavior is Redis-internal | Read test_consume_removes_from_stream assertion; confirm it uses xrange not xlen |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: every task has an automated verify command routed through `bash scripts/test.sh`
- [x] Wave 0 covers all MISSING references (test_mailbox.py, test_phase_09_shape.py, test_mailbox_durability.py)
- [x] No watch-mode flags in any test or verify block
- [x] Feedback latency < 15s (unit suite) / < 20s (all suite)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** planned — ready for execution
