# Phase 11 — listener-daemon — VERIFICATION

- Generated: 2026-06-08T11:28-0700
- Phase branch: `gsd/phase-11-listener-daemon` @ `a778d16`
- Verifier: em-execute-phase orchestrator (inline judgment over `scripts/verify-phase.sh 11`)
- Requirements: DAEMON-01, DAEMON-02, DAEMON-03, DAEMON-04, DAEMON-05, TEST-05

## Verdict: GOAL DELIVERED ✅ (with documented orphan test failures unrelated to this phase)

Phase 11 delivers the per-session listener daemon: a detached subprocess that
maintains session liveness via periodic heartbeat, subscribes to its message
channel, and exposes a complete `listen`/`stop` lifecycle with crash-safe
restart. All Phase 11 functionality is implemented and its tests pass. The only
failing checks are 9 pre-existing orphan failures owned by Phase 6, unchanged
from `origin/main`.

**Code-review remediation (commit `e1fc356`):** the `code_review` gate
(see `11-REVIEW.md`) found 2 Critical + 3 High latent bugs in the detached-daemon
path (the production path — `--foreground` is test-only). All Criticals and Highs
were fixed inline and validated: C-01 (parent-pid-in-HASH / daemon-suicide
window), C-02 (heartbeat session divergence), H-01 (re-registration race), H-02
(interval constant), H-03 (test cleanup). The previously-missing
`test_daemon_start_detaches` now covers the detach path end-to-end. M-01/M-02/L-01
(Medium/Low) are deferred as documented debt.

## Goal-backward check — what the phase promised vs. delivered

| Requirement | Delivered? | Evidence |
|-------------|-----------|----------|
| DAEMON-01 (detached daemon start, records pid, exits 0) | ✅ | `_daemon_start` Popen(`start_new_session=True`) re-invokes `session listen --foreground`; `test_daemon_start_detaches`, `test_daemon_foreground_starts_and_records_pid` green |
| DAEMON-02 (message liveness — system-level) | ✅ | `_daemon_foreground_run` subscribes via pubsub, no `mbox_write`; `test_daemon_message_liveness` proves inbox delivery while daemon alive (send-time write owns durability) |
| DAEMON-03 (heartbeat keeps session alive) | ✅ | monotonic-tick `session_heartbeat()` at `EM_PROJ_DAEMON_HEARTBEAT_INTERVAL`; `test_daemon_heartbeat_refreshes_session` asserts TTL refreshed (>250) |
| DAEMON-04 (single-instance idempotency) | ✅ | Lua `LUA_DAEMON_WRITE_OR_DETECT` write-or-detect; second `listen` returns `already_running` w/ same pid; `test_daemon_idempotent_double_start` green |
| DAEMON-05 (stop + crash/stale recovery) | ✅ | `_daemon_stop` 4 exit paths; `is_holder_stale` probe before `os.kill` (SIGTERM-to-wrong-pid guard); `test_daemon_stop_live_daemon`, `test_daemon_crash_recovery` green |
| TEST-05 (multiprocess lifecycle harness) | ✅ | `tests/multiprocess/test_daemon_lifecycle.py` — 7 tests green (foreground, heartbeat, liveness, idempotent, crash-recovery, stop-live, stop-not-running) |

## Structural invariants

- `session/_ops.py` remains free of `subprocess`/`signal`/`shutil`/`threading` imports — daemon process code isolated in `_daemon.py` (structural test green).
- `_daemon.py` contains no `mbox_write` call — DAEMON-02 satisfied at system level (structural test green).
- `DAEMON_KEY_PREFIX = "daemon:"` namespace distinct from `state:*`/`mbox:*`/`topic:*`.
- All 8 `test_phase_11_shape.py` invariants pass (summaries-exist now green — both SUMMARYs on disk).

## Deterministic check results (`scripts/verify-phase.sh 11`)

| Check | Status |
|-------|--------|
| Redis backend (appendonly/appendfsync/save + AOF) | PASS |
| em-proj on PATH + `--version` | PASS |
| Anti-pattern markers (TBD/FIXME/XXX/HACK/TODO/PLACEHOLDER) | PASS (none) |
| Plan/SUMMARY coverage (11-01, 11-02) | PASS |
| Commit traceability (all 6 `(11-NN)` commits present) | PASS |
| `scripts/test.sh all` | FAIL — see orphan analysis below |
| `scripts/test.sh structural` | FAIL — same orphan set |

## Orphan failure analysis (NOT this phase's defect)

9 failures, all owned by Phase 6's gsd-sdk workstream symmetry contract:

```
tests/multiprocess/test_workstream_clobber_demo.py::test_new_path_through_gsd_sdk_refuses_loser
tests/multiprocess/test_workstream_consumer_race.py::test_two_sessions_race_workstream_set_one_wins
tests/multiprocess/test_workstream_consumer_race.py::test_same_session_refresh_does_not_conflict
tests/multiprocess/test_workstream_consumer_race.py::test_em_proj_missing_falls_through_with_warning
tests/structural/test_phase_06_shape.py::test_gsd_sdk_workstream_js_contains_em_proj_shellout
tests/structural/test_phase_06_shape.py::test_gsd_sdk_workstream_js_shellout_precedes_set_active
tests/structural/test_phase_06_shape.py::test_gsd_sdk_workstream_js_contains_held_by_another_branch
tests/structural/test_phase_06_shape.py::test_gsd_sdk_workstream_js_contains_enoent_fallback
tests/structural/test_phase_06_shape.py::test_gsd_sdk_workstream_ts_contains_em_proj_shellout
```

Root cause: the globally-installed `get-shit-done-cc` node module at
`~/.nvm/.../lib/node_modules/get-shit-done-cc/sdk/src/query/workstream.{ts,js}`
no longer contains the `em-proj` shellout the Phase 6 structural contract
asserts. This is **environment/installed-SDK state**, not repository code —
Phase 6's own source is untouched.

Orphan confirmation (per em-execute-phase invariant #6): the three failing test
files are **unchanged** between `origin/main` and this phase branch
(`git diff --name-only origin/main HEAD -- <files>` → empty). The failure set is
identical to the pre-execution baseline. Phase 11 introduced **zero** new
failures.

Recommended follow-up (separate from Phase 11): re-apply the `em-proj` shellout
to the installed gsd-sdk workstream module, or relax the Phase 6 structural
contract to xfail when the installed module diverges. Tracked outside this phase.

## Next-phase recommendation

Phase 11 is complete and ready to ship. The daemon lifecycle is the foundation
for any subsequent always-on session behavior. Before opening the phase PR,
consider addressing the Phase 6 orphan set so the suite is green end-to-end (it
is currently red on `main` independent of this work).
