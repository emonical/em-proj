---
phase: 11
slug: listener-daemon
status: approved
nyquist_compliant: true
wave_0_complete: true
created: 2026-06-08
---

# Phase 11 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
>
> **Project override (feedback_green_vertical_slice_plans):** Nyquist's "define the
> automated verify before implementation" requirement is satisfied **test-first WITHIN
> each green vertical-slice plan** — NOT by a standalone Wave-0 RED-tests-only plan.
> Each plan writes its tests and the code that makes them green, and ends green. There
> is no RED-only wave for this phase.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (via `scripts/test.sh` dispatcher — never bare pytest/uv) |
| **Config file** | pyproject.toml (existing) |
| **Quick run command** | `scripts/test.sh unit -k daemon` |
| **Full suite command** | `scripts/test.sh all` |
| **Estimated runtime** | ~30–60 seconds full; multiprocess daemon tests must use `EM_PROJ_DAEMON_HEARTBEAT_INTERVAL=1` to stay under ~5s each |

---

## Sampling Rate

- **After every task commit:** `scripts/test.sh unit -k daemon` (+ `scripts/test.sh structural` for shape tasks)
- **After every plan wave:** `scripts/test.sh all`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** ~60 seconds

---

## Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command |
|--------|----------|-----------|-------------------|
| DAEMON-01 | `session listen` starts a detached daemon SUBSCRIBEd to `msg:<session_id>`, records its own pid in `daemon:<session_id>` | multiprocess | `scripts/test.sh multiprocess -k daemon_start` |
| DAEMON-02 | Message is in recipient's inbox after send while daemon up — durability is the send-time `mbox_write`; daemon does NOT re-write (system-level proof) | multiprocess | `scripts/test.sh multiprocess -k daemon_liveness` |
| DAEMON-03 | Daemon refreshes session registry heartbeat on cadence; session TTL stays alive while daemon runs | multiprocess | `scripts/test.sh multiprocess -k daemon_heartbeat` |
| DAEMON-04 | Explicit `session stop` terminates daemon; double `session listen` is idempotent (exactly one daemon) | multiprocess | `scripts/test.sh multiprocess -k daemon_idempotent` |
| DAEMON-05 | `kill -9` → stale `daemon:<sid>` record detectable via `is_holder_stale` → safe idempotent restart; never wedges session | multiprocess | `scripts/test.sh multiprocess -k daemon_crash` |
| TEST-05 | All lifecycle scenarios (start/stop/auto-mechanism/idempotent/crash-recovery) + drain-to-mailbox green | multiprocess + structural | `scripts/test.sh all -k daemon` |

---

## Per-Task Verification Map

> Exact task IDs are assigned by the planner. Each task carries an `<automated>` verify
> command from the table above OR a structural assertion. Continuity rule: no 3
> consecutive tasks without an automated verify.

| Plan | Wave | Requirement(s) | Test Type | Automated Command |
|------|------|----------------|-----------|-------------------|
| 11-01 | 1 | DAEMON-01, DAEMON-03 (daemon body: subscribe loop + heartbeat tick, `_daemon.py` ops, test-first) | multiprocess + unit | `scripts/test.sh multiprocess -k daemon_start` |
| 11-02 | 2 | DAEMON-04, DAEMON-05 (single-instance record, stop verb, crash/stale detect + restart) | multiprocess | `scripts/test.sh multiprocess -k "daemon_idempotent or daemon_crash"` |
| 11-0x | — | TEST-05 rollup + structural shape (`tests/structural/test_phase_11_shape.py`) | structural | `scripts/test.sh structural -k phase_11` |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky — updated during execution.*

---

## Wave 0 Requirements

**None — no RED-only Wave 0 (project override).** Test infrastructure already exists
(`tests/multiprocess/`, `tests/structural/`, `scripts/test.sh`). New test files are
created test-first inside their owning green plan:
- `tests/multiprocess/test_daemon_lifecycle.py` — created + driven green within 11-01/11-02
- `tests/structural/test_phase_11_shape.py` — created + green within its owning plan

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| (none expected) | — | Lifecycle is fully automatable via the fork+exec multiprocess harness | — |

*If the planner finds a behavior that genuinely cannot be automated (e.g. true terminal-detach observation), record it here rather than skipping coverage.*

---

## Validation Sign-Off

- [ ] Every task has an `<automated>` verify command or a structural assertion
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Each plan ends GREEN (no RED-only plan/wave) — green vertical-slice rule
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s (daemon tests use `EM_PROJ_DAEMON_HEARTBEAT_INTERVAL=1`)
- [x] `nyquist_compliant: true` set in frontmatter after plan-check passes

**Approval:** approved 2026-06-08 (plan-check PASSED — all tasks carry automated verify; green-slice confirmed)
