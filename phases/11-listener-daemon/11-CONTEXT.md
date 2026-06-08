# Phase 11: Listener Daemon - Context

**Gathered:** 2026-06-08
**Status:** Ready for planning
**Source:** Inline capture (lightweight — stack locked, two load-bearing decisions resolved)

<domain>
## Phase Boundary

Each session runs exactly one long-lived listener daemon that:
1. Redis-`SUBSCRIBE`s to the session's live-delivery channel(s) and records its own pid (DAEMON-01).
2. Refreshes the session registry heartbeat while alive — listener and liveness are one process (DAEMON-03).
3. Has a crash-safe, idempotent lifecycle: explicit stop verb, idempotent double-start (exactly one daemon per session), detectable crash via a stale daemon record, safe restart (DAEMON-04, DAEMON-05).
4. Is proven end-to-end by the multi-process harness (TEST-05).

**Auto-start via SessionStart hook is OUT OF SCOPE for this phase** — the explicit `session listen` / stop verbs and idempotency land here; the SessionStart hook wiring (HOOK-01) is Phase 12. DAEMON-04's hook clause is satisfied at the *mechanism* level (the daemon is start/stop/idempotent so a hook CAN drive it), not by shipping the hook itself.
</domain>

<decisions>
## Implementation Decisions

### Daemon role — liveness + lifecycle only (LOCKED, 2026-06-08)
The daemon does **NOT** re-write messages durably. Phase 10's send path
(`send_directed` / `send_broadcast` / `send_topic` in `src/em_proj/message/_ops.py`)
**already writes the full durable MBOX-04 record into each recipient's mailbox at send
time** (`mbox_write`), then fires a partial fire-and-forget `PUBLISH msg:<recipient>`
as a live nudge. Offline durability (MBOX-01) is therefore already guaranteed by the
send path — every recipient in all three patterns must be a *registered* session to
receive the durable write.

Consequently the daemon's job is:
- **SUBSCRIBE** to `msg:<own_session_id>` for real-time pickup of the live nudge.
- **Refresh the registry heartbeat** on a cadence while alive (one process = listener + liveness).
- **Lifecycle**: start (detached, idempotent), stop, crash-detect, safe restart.

**No double-write.** DAEMON-02 ("daemon drains received messages into the mailbox") is
satisfied at the *system* level: the message is durably in the mailbox by send-time
write, and the daemon keeps the session a valid, live recipient via the heartbeat. The
daemon must NOT write a second (partial) copy into the mailbox.

> Rejected alternative: "drain-and-write with dedup" (daemon writes received pub/sub
> messages to the mailbox, deduped by msg_id). More robust against a send/registration
> race but adds a second writer + payload enrichment + dedup. Not warranted now.

### Single-daemon enforcement
Exactly one daemon per session (DAEMON-04 idempotency). Reuse an existing v1.0 substrate
primitive rather than inventing a new one — candidate: the `lock`/`claim` primitive
(`src/em_proj/state/lock.py`, `claim.py`) keyed on the session_id, OR a dedicated daemon
record HASH carrying `{pid, proc_start_epoch, boot_id}`. **Final mechanism is a research
+ planning decision** (see Claude's Discretion).

### Crash detection — reuse the v1.0 stale composite
A crashed/abnormally-exited daemon must be detectable and never wedge the session
(DAEMON-05). Reuse `is_holder_stale()` (`src/em_proj/identity.py`) — the same
`{pid, proc_start_epoch, boot_id}` composite probe used by the registry, claims, locks,
and reserves. A daemon record carrying that triple makes crash detection a probe, not a
new mechanism. Restart after a detected stale daemon record must be safe + idempotent.

### Heartbeat integration
The daemon refreshes the registry heartbeat by calling the existing `session_heartbeat()`
op (`src/em_proj/session/_ops.py`), re-arming `TTL_DEFAULT` (300s). The heartbeat cadence
must be comfortably under the TTL so a live daemon never lets its own session expire. The
module comment in `session/_ops.py` already anticipates this: "the auto-heartbeat daemon
(Phase 11) will keep this alive."

### Verb surface — extend the existing session sub-app
New verbs mount on the existing `session_app` (`src/em_proj/session/__init__.py`) following
the D-14 thin-wrapper contract (resolve json_mode → die_if_redis_unreachable → one `_ops`
call → one `emit_*`). Per the package-layout note in `session/__init__.py`, `session listen`
lives in an additional submodule (e.g. `session/_daemon.py` or `session/listen.py`) so the
mount module stays logic-free. Verbs: `session listen` (start), `session stop` (or
`session listen --stop`), and the daemon body itself (a foreground loop the start verb
detaches).

### Claude's Discretion
- Detach mechanism for the long-lived process (double-fork vs `subprocess.Popen` with
  detach/`start_new_session` vs `os.fork`) — research to recommend; must be crash-safe and
  not tied to the parent CLI process lifetime.
- Exact daemon-record key namespace + fields (or reuse of lock/claim) for single-instance +
  crash detection.
- Heartbeat cadence value (must be < TTL_DEFAULT=300s with margin).
- Whether `session listen` blocks in foreground when invoked directly (debug) vs always
  detaches — and how `--stop` signals the running daemon (SIGTERM to recorded pid).
- Graceful shutdown: unsubscribe + remove/expire daemon record on clean stop.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before researching, planning, or implementing.**

### Live-delivery + mailbox substrate (Phase 9/10)
- `src/em_proj/message/_ops.py` — send patterns publish to `msg:<recipient>` (the channel
  the daemon SUBSCRIBEs); `mbox_write` (send-time durable write); `mbox_blocking_read`
  (documented "for Phase 11 listener daemon"); MBOX-04 record shape; `MBOX_TTL_SECONDS`.
- `src/em_proj/message/__init__.py` — `message_app` mount + verb wrapper pattern.

### Registry + identity substrate (Phase 8 / v1.0)
- `src/em_proj/session/_ops.py` — `session_heartbeat()`, `session_register()`,
  `TTL_DEFAULT=300`, `is_holder_stale` usage for lazy reaping, Lua atomicity pattern.
- `src/em_proj/session/__init__.py` — `session_app` mount, D-14 thin-wrapper contract,
  package-layout note ("`session listen` in Phase 11 can be added in additional submodules").
- `src/em_proj/identity.py` — `is_holder_stale`, `current_process_composite`,
  `resolve_session_id`, boot_id / proc_start_epoch composite.

### Plumbing
- `src/em_proj/redis_client.py` — `get_client`, `die_if_redis_unreachable` (D-18).
- `src/em_proj/output.py` — `emit_ok` / `emit_error` / `emit_not_found` / `resolve_json_mode`.
- `src/em_proj/state/lock.py`, `src/em_proj/state/claim.py` — candidate single-instance
  primitive; reference Lua refresh-or-take + stale-take patterns.

### Project conventions + test surface
- `CLAUDE.md` — test dispatcher (`scripts/test.sh`, never bare pytest/uv), structural tests
  under `tests/structural/`, prohibited-import enforcement pattern.
- `tests/multiprocess/` — the fork+exec harness; TEST-05 (daemon lifecycle +
  drain-to-mailbox) extends it. See `tests/multiprocess/test_harness_self.py`.
- Prior phase plans `.planning/phases/10-messaging-send-patterns/10-0*-PLAN.md` — task/format
  exemplar (frontmatter, read_first, acceptance_criteria, must_haves).
</canonical_refs>

<specifics>
## Specific Ideas

- The daemon SUBSCRIBEs to a single channel `msg:<own_session_id>` — Phase 10 already
  fans out broadcast/topic to per-recipient `msg:<sid>` channels at send time, so the
  daemon does NOT need to subscribe to separate broadcast/topic channels. (This reconciles
  DAEMON-01's "directed + subscribed broadcast/topic scopes" wording with the shipped
  per-recipient-channel fan-out: all scopes arrive on the one per-session channel.)
  Researcher: confirm there is no separate broadcast/topic channel the daemon must also
  subscribe to, given the Phase 10 fan-out model.
- Prohibited-import discipline: the daemon WILL legitimately need a process/detach
  mechanism (the first in this codebase). `session/_ops.py` currently bans
  `subprocess/threading/multiprocessing` via tests. The daemon submodule is where any such
  import lives — keep `_ops.py` clean; place process-spawning code in the daemon submodule
  and update the structural/prohibited-import tests accordingly.
- Green vertical-slice plans: each PLAN.md must end green per plan (tests + impl
  interleaved); NO standalone "lay all RED tests" wave-0 plan.
</specifics>

<deferred>
## Deferred Ideas

- **HOOK-01 SessionStart auto-start** — Phase 12. This phase delivers the start/stop/
  idempotent mechanism a hook can drive, not the hook wiring.
- **HOOK-02 UserPromptSubmit surfacing** unread mailbox into live Claude context — Phase 12.
- **Real-time push** from daemon into the foreground session beyond the SUBSCRIBE pickup —
  the Phase 12 hook reads the mailbox; daemon's pub/sub consumption is the liveness signal.
- Consumer-group / at-least-once semantics for the mailbox — explicitly deferred in
  `message/_ops.py` ("can be layered in Phase 11+"); not pulled in here.
</deferred>

---

*Phase: 11-listener-daemon*
*Context gathered: 2026-06-08 via inline capture (2 decisions locked: daemon role = liveness+lifecycle only; research-first)*
