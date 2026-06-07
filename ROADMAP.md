# Roadmap: em-proj

## Milestones

- ✅ **v1.0 em-proj state primitive** — Phases 1-7 (shipped 2026-06-07) → see [milestones/v1.0-ROADMAP.md](milestones/v1.0-ROADMAP.md)
- 🚧 **v1.1 Session Registry + Inter-Session Messaging** — Phases 8-12 (in progress) → see `## Phase Details` below

## Phases

<details>
<summary>✅ v1.0 em-proj state primitive (Phases 1-7) — SHIPPED 2026-06-07</summary>

- [x] Phase 1: Test Harness + Redis Foundation (4/4 plans) — completed 2026-05-23
- [x] Phase 2: CLI Shell + KV Primitive (5/5 plans) — completed 2026-05-23
- [x] Phase 3: Identity + Advisory Locks (6/6 plans) — completed 2026-05-23
- [x] Phase 4: Long-Lived Claims (4/4 plans) — completed 2026-05-24
- [x] Phase 5: `/em-global-state` Skill Surface (5/5 plans) — completed 2026-05-26
- [x] Phase 6: gsd-sdk Workstream Consumer (3/3 plans) — completed 2026-05-27
- [x] Phase 7: Project-Scoped Reservation Registry (3/3 plans) — completed 2026-06-04

Full phase details, success criteria, and milestone summary archived in
[milestones/v1.0-ROADMAP.md](milestones/v1.0-ROADMAP.md).

</details>

### v1.1 Session Registry + Inter-Session Messaging (Phases 8-12)

- [x] **Phase 8: Session Registry (Hybrid)** - Live registry of who's running, enriched with held claims/locks/reservations, with stale reaping
- [ ] **Phase 9: Durable Mailbox Transport** - Per-recipient Redis mailbox that persists messages for offline sessions (owns the Streams-vs-List transport decision)
- [ ] **Phase 10: Messaging Send Patterns** - `message send`/`broadcast`/`subscribe` across directed/broadcast/topic patterns × selectable scope
- [ ] **Phase 11: Listener Daemon** - Per-session pub/sub daemon that drains live messages to the mailbox and heartbeats the registry
- [ ] **Phase 12: End-to-End CC Integration + Skill Surface** - SessionStart/UserPromptSubmit hooks surface the mailbox into a live session (A→B proven); `/em-sessions` read+send skill surface

## Phase Details

### Phase 8: Session Registry (Hybrid)
**Goal**: Any session or sub-agent can see who else is live, where, and what each holds — and dead sessions disappear automatically.
**Depends on**: Phase 7 (v1.0 stale-detection composite, holder metadata, list-by-prefix, multi-process harness)
**Requirements**: SESS-01, SESS-02, SESS-03, SESS-04, SESS-05, TEST-03
**Success Criteria** (what must be TRUE):
  1. `em-proj session register` records the current session with full metadata, and `em-proj session heartbeat` refreshes its liveness.
  2. `em-proj session list` returns every live session in a parseable form, each enriched with the claims/locks/reservations that session currently holds.
  3. `em-proj session show <session_id>` returns one session's full record plus its held resources.
  4. A session that dies (dead pid / proc_start mismatch / boot-id change / TTL lapse) is excluded from `list` and reaped, using the v1.0 stale-detection composite.
  5. The multi-process harness proves registry liveness and stale reaping across fork+exec'd sessions.
**Plans**: 3 plans
Plans:
**Wave 1**
- [x] 08-01-PLAN.md — session.py core module (register, heartbeat, list, show, enrichment join, stale reaping)

**Wave 2** *(blocked on Wave 1 completion)*
- [x] 08-02-PLAN.md — session CLI subcommand wiring (session_app Typer + cli.py mount)

**Wave 3** *(blocked on Wave 2 completion)*
- [x] 08-03-PLAN.md — TEST-03 multiprocess harness + Phase 8 structural shape tests

### Phase 9: Durable Mailbox Transport
**Goal**: Every session has a durable per-recipient mailbox that holds messages until the session reads them — even if the session was offline when the message was sent.
**Depends on**: Phase 8 (sessions are addressable recipients in the registry)
**Requirements**: MBOX-01, MBOX-02, MBOX-03, MBOX-04
**Success Criteria** (what must be TRUE):
  1. A durable per-recipient mailbox in Redis persists messages for offline/unattached sessions (durable transport — Redis Streams vs List — decided in this phase's plan, since fire-and-forget pub/sub alone cannot reach offline sessions).
  2. `em-proj message inbox` reads the mailbox in order; consumption marks messages read/acked, `--peek` reads without consuming, `--since <id>` resumes from a point.
  3. Mailbox messages carry a TTL and are cleaned up after expiry or after read+ack; the mailbox is bounded against unbounded growth.
  4. A stored message record carries `{msg_id, from_session, pattern, scope, topic?, body, sent_at, ttl}`.
**Plans**: 3 plans
Plans:
**Wave 0**
- [x] 09-01-PLAN.md — Wave 0 test scaffolds (unit stubs, structural shape, multiprocess durability stub)

**Wave 1** *(blocked on Wave 0 completion)*
- [x] 09-02-PLAN.md — message/_ops.py core module (mbox_write, mailbox_inbox, mbox_blocking_read, constants)

**Wave 2** *(blocked on Wave 1 completion)*
- [x] 09-03-PLAN.md — message/__init__.py inbox verb + cli.py mount + structural tests GREEN

### Phase 10: Messaging Send Patterns
**Goal**: A session can send a message to another session, to a scope-bounded group, or to a topic — and it lands durably in every matched recipient's mailbox.
**Depends on**: Phase 9 (mailbox is the durable landing surface), Phase 8 (scope/recipient resolution off the registry)
**Requirements**: MSG-01, MSG-02, MSG-03, MSG-04, MSG-05, TEST-04
**Success Criteria** (what must be TRUE):
  1. `em-proj message send --to <session_id> <body>` delivers a directed message to one session's mailbox regardless of scope.
  2. `em-proj message broadcast <body> --scope <project|upstream|machine>` delivers to every session in the selected scope.
  3. `em-proj message subscribe`/`unsubscribe <topic>` manage topic membership, and `message send --topic <topic> --scope <...>` routes to that scope's subscribers.
  4. `em-proj message send` returns parseable delivery metadata (recipients matched, live vs mailbox-only) with semantic exit codes.
  5. The harness proves A→B delivery across all 3 patterns (directed/broadcast/topic) × 3 scopes (project/upstream/machine).
**Plans**: TBD

### Phase 11: Listener Daemon
**Goal**: Each session runs exactly one long-lived listener that picks up live pub/sub traffic, drains it into the mailbox, and keeps the registry heartbeat fresh — with a lifecycle that is crash-safe and idempotent.
**Depends on**: Phase 10 (messages/channels to subscribe to), Phase 9 (mailbox to drain into), Phase 8 (registry to heartbeat)
**Requirements**: DAEMON-01, DAEMON-02, DAEMON-03, DAEMON-04, DAEMON-05, TEST-05
**Success Criteria** (what must be TRUE):
  1. `em-proj session listen` starts a per-session daemon that SUBSCRIBEs to the session's relevant channels (directed + subscribed broadcast/topic scopes) and records its own pid.
  2. On receiving a message the daemon drains it into the session's durable mailbox, so delivery does not depend on the session being foregrounded.
  3. While alive, the daemon refreshes the session registry heartbeat (listener and liveness are one process).
  4. An explicit stop verb terminates the daemon, double-start is idempotent (exactly one daemon per session), and a crashed daemon is detectable (stale daemon record), never wedges the session, and restarts safely.
  5. The harness proves daemon lifecycle (start/stop/auto/idempotent/crash-recovery) and drain-to-mailbox.
**Plans**: TBD

### Phase 12: End-to-End CC Integration + Skill Surface
**Goal**: A message sent from one live Claude Code session demonstrably appears in another live session's context, and sub-agents/skills can read the registry and inbox and send messages — proving the milestone end-to-end.
**Depends on**: Phase 11 (auto-started daemon), Phase 10 (send patterns), Phase 9 (mailbox to surface), Phase 8 (registry to read)
**Requirements**: HOOK-01, HOOK-02, HOOK-03, HOOK-04, SKILL-04, SKILL-05
**Success Criteria** (what must be TRUE):
  1. A SessionStart hook auto-registers the session and starts its listener daemon.
  2. A UserPromptSubmit (or equivalent) hook surfaces unread mailbox messages into the live Claude Code session on its next turn.
  3. End-to-end demonstrated: a message sent from session A (directed/broadcast/topic) appears in session B's live context via the hook path — the v1.1 validating consumer.
  4. Hook integration degrades gracefully when Redis or the daemon is unavailable — session startup never breaks.
  5. `/em-sessions` (or extended `/em-global-state`) lists live sessions + held resources parseably, and can read a session's inbox and send a message as first-class CLI-backed operations consistent with the v1.0 skill-write boundary.
**Plans**: TBD

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Test Harness + Redis Foundation | v1.0 | 4/4 | Complete | 2026-05-23 |
| 2. CLI Shell + KV Primitive | v1.0 | 5/5 | Complete | 2026-05-23 |
| 3. Identity + Advisory Locks | v1.0 | 6/6 | Complete | 2026-05-23 |
| 4. Long-Lived Claims | v1.0 | 4/4 | Complete | 2026-05-24 |
| 5. `/em-global-state` Skill Surface | v1.0 | 5/5 | Complete | 2026-05-26 |
| 6. gsd-sdk Workstream Consumer | v1.0 | 3/3 | Complete | 2026-05-27 |
| 7. Project-Scoped Reservation Registry | v1.0 | 3/3 | Complete | 2026-06-04 |
| 8. Session Registry (Hybrid) | v1.1 | 3/3 | Complete | 2026-06-07 |
| 9. Durable Mailbox Transport | v1.1 | 0/3 | Not started | - |
| 10. Messaging Send Patterns | v1.1 | 0/0 | Not started | - |
| 11. Listener Daemon | v1.1 | 0/0 | Not started | - |
| 12. End-to-End CC Integration + Skill Surface | v1.1 | 0/0 | Not started | - |
