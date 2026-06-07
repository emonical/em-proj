# Requirements: em-proj v1.1

**Defined:** 2026-06-07
**Milestone:** v1.1 — Session Registry + Inter-Session Messaging
**Core Value:** A sub-agent, skill, or session can ask the substrate "is it safe to edit X, or is someone else working there?" — and now also *see who else is live* and *talk to them*.

Builds on the v1.0 `state` primitive (identity, holder metadata, stale-detection,
list-by-prefix, multi-process harness). Adds two new top-level subcommands:
`em-proj session` and `em-proj message`.

## v1.1 Requirements

### SESS — Session registry (hybrid)

- [ ] **SESS-01**: `em-proj session register` records the current session in a live registry with metadata `{session_id, project_hash, upstream_identity, pid, proc_start_epoch, boot_id, cwd, registered_at, last_heartbeat}`
- [ ] **SESS-02**: `em-proj session heartbeat` refreshes the session's liveness; a registry entry auto-expires after a missed-heartbeat TTL backstop
- [ ] **SESS-03**: `em-proj session list` returns all live sessions (parseable), each **enriched** with the claims/locks/reservations that session currently holds (hybrid view over v1.0 holder metadata)
- [ ] **SESS-04**: `em-proj session show <session_id>` returns one session's full record plus its held resources
- [ ] **SESS-05**: Stale sessions (dead pid / `proc_start` mismatch / boot-id change / TTL lapse) are excluded from `list` and reaped, reusing the v1.0 stale-detection composite

### DAEMON — per-session listener daemon

- [ ] **DAEMON-01**: `em-proj session listen` starts a per-session daemon that Redis-`SUBSCRIBE`s to the session's relevant channels (directed + subscribed broadcast/topic scopes) and records its own pid for management
- [ ] **DAEMON-02**: On receiving a message, the daemon **drains it into the session's durable mailbox** — delivery does not depend on the session being attached/foregrounded
- [ ] **DAEMON-03**: While alive, the daemon refreshes the session registry heartbeat (the listener and liveness are one process)
- [ ] **DAEMON-04**: The daemon **auto-starts via a SessionStart hook** and stops on session end; an explicit stop verb (`session stop` / `session listen --stop`) terminates it; double-start is idempotent (exactly one daemon per session)
- [ ] **DAEMON-05**: Daemon crash / abnormal exit is detectable (stale daemon record) and never wedges the session; restart is safe and idempotent

### MBOX — durable mailbox

- [ ] **MBOX-01**: Each session has a durable per-recipient mailbox in Redis that **persists messages for offline/unattached sessions** (durable transport — Streams vs List — decided at plan-phase)
- [ ] **MBOX-02**: `em-proj message inbox [--peek] [--since <id>]` reads the mailbox in order; consumption marks messages read/acked, `--peek` reads without consuming
- [ ] **MBOX-03**: Mailbox messages carry a TTL and are cleaned up after expiry or after read+ack; the mailbox is bounded to prevent unbounded growth
- [ ] **MBOX-04**: Message records carry `{msg_id, from_session, pattern, scope, topic?, body, sent_at, ttl}`

### MSG — messaging send patterns

- [ ] **MSG-01**: `em-proj message send --to <session_id> <body>` — **directed** message to one session (live via pub/sub if listening, durable via mailbox regardless)
- [ ] **MSG-02**: `em-proj message broadcast <body> --scope <project|upstream|machine>` — **one-to-many** to all sessions in the selected scope
- [ ] **MSG-03**: **Topic** pub/sub — `em-proj message subscribe <topic>` / `unsubscribe <topic>`; `em-proj message send --topic <topic> <body> --scope <...>` routes to subscribers
- [ ] **MSG-04**: Scope is **selectable per message** (`project_hash` | `upstream_identity` | machine-global); directed messages route by `session_id` regardless of scope
- [ ] **MSG-05**: Send is parseable / machine-first — returns delivery metadata (recipients matched, live vs mailbox-only) and semantic exit codes

### HOOK — end-to-end Claude Code integration (validating consumer)

- [ ] **HOOK-01**: A SessionStart hook auto-registers the session and starts its listener daemon
- [ ] **HOOK-02**: A UserPromptSubmit (or equivalent) hook surfaces unread mailbox messages into the live Claude Code session on its next turn
- [ ] **HOOK-03**: **End-to-end demonstrated** — a message sent from session A (directed / broadcast / topic) appears in session B's live context via the hook path (the v1.1 validating consumer, analogous to v1.0's workstream consumer)
- [ ] **HOOK-04**: Hook integration degrades gracefully when Redis or the daemon is unavailable — never breaks session startup

### SKILL — skill surface (continues v1.0 SKILL-01..03)

- [ ] **SKILL-04**: `/em-sessions` (or extended `/em-global-state`) lists live sessions + held resources, parseable for sub-agents
- [ ] **SKILL-05**: The skill can read a session's inbox and send a message as first-class operations (read + send via the CLI; consistent with the v1.0 skill-write boundary — `send` is an explicit message op, not an ad-hoc state write)

### TEST — multi-process harness (continues v1.0 TEST-01/02)

- [ ] **TEST-03**: Harness covers registry liveness + stale reaping across fork+exec'd sessions
- [ ] **TEST-04**: Harness covers A→B delivery across all 3 patterns (directed / broadcast / topic) × 3 scopes (project / upstream / machine), via both mailbox and live daemon
- [ ] **TEST-05**: Harness covers daemon lifecycle (start / stop / auto / idempotent / crash-recovery) and drain-to-mailbox

## Out of Scope (v1.1)

Explicitly excluded; documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Request/ack reply semantics | Deferred; adds reply channel + correlation IDs. Revisit once one-way messaging proves out |
| Blocking-wait delivery (`message wait` / BLPOP) | Deferred; the daemon+mailbox pull model covers automated coordination first |
| Workstream handoff protocol | M4+; built on registry + messaging once both are validated |
| Memory / settings write coordination | Separate consumer of the v1.0 claim model; not part of registry+messaging |
| Cross-machine sync | Single-machine, single-user target (unchanged from v1.0) |
| Other AI CLIs as first-class consumers | Design must not preclude them; Claude Code is the only initial consumer |
| Persistent message history / audit log | Mailbox is transient (TTL-bounded); durable message archive is out of scope |
| Web UI / TUI dashboard | The skill surface is the human read view |

## Future Requirements (M2+ / deferred)

- **ACK-01**: Request/ack with correlation IDs and a reply channel
- **WAIT-01**: Blocking-wait delivery for scripted "wait until X" coordination
- **HAND-01**: Workstream handoff protocol (registry + messaging + claims)
- **MEMC-01/02/03**: Memory + settings write coordination, workstream hard-mutex

## Traceability

Which phases cover which requirements. **Populated by the roadmapper.**

| Requirement | Phase | Status |
|-------------|-------|--------|
| SESS-01..05 | TBD | Pending |
| DAEMON-01..05 | TBD | Pending |
| MBOX-01..04 | TBD | Pending |
| MSG-01..05 | TBD | Pending |
| HOOK-01..04 | TBD | Pending |
| SKILL-04..05 | TBD | Pending |
| TEST-03..05 | TBD | Pending |

**Coverage:**
- v1.1 requirements: 26 total
- Mapped to phases: 0 (roadmapper to assign)

---
*Requirements defined: 2026-06-07*
