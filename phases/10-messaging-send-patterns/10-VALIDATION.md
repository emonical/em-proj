---
phase: 10
slug: messaging-send-patterns
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-07
---

# Phase 10 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `10-RESEARCH.md` › Validation Architecture.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (project-locked) |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `scripts/test.sh unit` |
| **Full suite command** | `scripts/test.sh all` |
| **Estimated runtime** | ~20–40s (full suite, includes multiprocess) |

---

## Sampling Rate

- **After every task commit:** Run `scripts/test.sh unit`
- **After every plan wave:** Run `scripts/test.sh all`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** ~40 seconds

---

## Per-Task Verification Map

> Plan/task IDs are forward references to the gsd-planner output (research recommends 3 plans:
> Wave 0 test scaffolds · Wave 1 `_ops.py` send/topic primitives · Wave 2 CLI verbs + TEST-04 harness).
> The gsd-nyquist-auditor refines exact task IDs post-planning.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 10-01-* | 01 | 0 | MSG-01..05, TEST-04 | — | test scaffolds RED | unit/structural | `scripts/test.sh unit -k message_send` | ❌ W0 | ⬜ pending |
| 10-02-* | 02 | 1 | MSG-01 | T-10-03 | `session_show()` validates recipient before write | unit | `scripts/test.sh unit -k directed` | ❌ W0 | ⬜ pending |
| 10-02-* | 02 | 1 | MSG-02, MSG-04 | T-10-05 | scope filter correct-by-design (no cross-project leak) | unit | `scripts/test.sh unit -k broadcast or scope` | ❌ W0 | ⬜ pending |
| 10-02-* | 02 | 1 | MSG-03 | T-10-02 | `_validate_topic` allowlist guards Redis key | unit | `scripts/test.sh unit -k topic` | ❌ W0 | ⬜ pending |
| 10-03-* | 03 | 2 | MSG-05 | — | parseable delivery metadata + semantic exit codes | unit | `scripts/test.sh unit -k test_message_send` | ❌ W0 | ⬜ pending |
| 10-03-* | 03 | 2 | TEST-04 | — | 3 patterns × 3 scopes delivery matrix (mailbox path) | integration | `scripts/test.sh multiprocess -k delivery` | ❌ W0 | ⬜ pending |
| 10-03-* | 03 | 2 | MBOX-01 | — | offline recipient gets message after send (activate skip-stub) | integration | `scripts/test.sh multiprocess -k durability` | ✅ (skip-stub) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_message_send.py` — stubs for MSG-01..05 (send_directed, send_broadcast, send_topic, subscribe/unsubscribe with mocked `session_list`/`mbox_write`)
- [ ] `tests/multiprocess/test_message_delivery.py` — stubs for TEST-04 (3×3 matrix, mailbox path; live daemon path skip-stubbed pending Phase 11)
- [ ] `tests/structural/test_phase_10_shape.py` — structural invariants (TOPIC_KEY_PREFIX constant, `message_app` exposes send/broadcast/subscribe/unsubscribe, no `pipeline`/consumer-group imports)
- [ ] Activate `tests/multiprocess/test_mailbox_durability.py` — replace `pytest.skip()` with the MBOX-01 E2E body (offline-send → read)

*Existing conftest fixtures (`clean_db`, `redis_precheck`, `multiproc_race`, `EM_PROJ_BIN`) cover infrastructure — no new framework install.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live pub/sub delivery (PUBLISH side) | MSG-01 (live half) | Consumer is the Phase 11 listener daemon, which does not exist yet | Deferred: covered by Phase 11/12 E2E. Phase 10 ships PUBLISH fire-and-forget + asserts the durable mailbox write only; live cells are skip-stubbed. |

*All durable-path behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 40s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
