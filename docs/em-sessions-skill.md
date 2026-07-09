---
name: em-sessions
description: "Read the live em-proj session registry + inbox, and send messages to other sessions. Shells out to em-proj CLI — requires em-proj installed (uv tool install)."
allowed-tools:
  - Bash
---

<objective>
This skill wraps `em-proj session` (the live session registry) and `em-proj message`
(inbox/send/broadcast) behind the same `schema_version` JSON envelope as
`/em-global-state`, so agents and users never need to hand-roll Redis queries or
subprocess calls to see who else is live or to notify another session.

It NEVER writes KV/locks/claims (that is `/em-global-state`'s domain) and it NEVER
manages session or daemon lifecycle — `session register`, `session listen`, and
`session stop` are the SessionStart hook's and daemon's job (Phase 12 HOOK-01), not a
skill-invoked write. This skill is a viewer plus an explicit two-verb send surface,
nothing more.
</objective>

<when_to_invoke>
- User types `/em-sessions` followed by a verb.
- A sub-agent wants to see who else is live before starting overlapping work.
- A sub-agent wants to peek at an inbox without consuming it.
- A sub-agent wants to notify another session directly, or broadcast to a scope.
</when_to_invoke>

<action>

## Verb reference

### /em-sessions list

List all live sessions with held-resource counts.

```bash
em-proj session list --json
```

Emit stdout verbatim. Output schema:

```json
{"schema_version":"1","status":"ok","data":[{"session":{"session_id":"...","project_hash":"...","upstream_identity":"...","pid":0,"proc_start_epoch":0.0,"boot_id":"...","cwd":"...","registered_at":0.0,"last_heartbeat":0.0},"held":{"claims":0,"locks":0,"reserves":0}}]}
```

Exit 0 = success (empty list is still exit 0).

---

### /em-sessions show `<session_id>`

Show the full record for a specific session, including held claim/lock/reserve details.

```bash
em-proj session show <session_id> --json
```

Emit stdout verbatim.
- Exit 0 = found; output: `{"schema_version":"1","status":"ok","data":{"session":{...9 fields...},"held":{"claims":[...],"locks":[...],"reserves":[...]}}}`
- Exit 2 = not found; output: `{"schema_version":"1","status":"not_found",...}`

---

### /em-sessions inbox [--session `<session_id>`]

Peek the current session's mailbox WITHOUT consuming it. This verb ALWAYS uses
`--peek` — the skill is a viewer, never the mailbox's consumer. The
UserPromptSubmit hook is the sole consumer (it reads and marks messages read on
every turn); a skill invocation must never race that consume-on-surface contract.

```bash
em-proj message inbox --peek --json
```

Pass `--session <session_id>` to peek a session OTHER than the caller's own by
setting `CLAUDE_CODE_SESSION_ID` for that invocation only:

```bash
CLAUDE_CODE_SESSION_ID=<session_id> em-proj message inbox --peek --json
```

Emit stdout verbatim. Output schema — a list of MBOX-04 records:

```json
{"schema_version":"1","status":"ok","data":[{"msg_id":"...","from_session":"...","pattern":"direct","scope":"machine","topic":null,"body":"...","sent_at":0.0,"ttl":3600}]}
```

Exit 0 = success (empty mailbox is still exit 0).

---

### /em-sessions send `<session_id>` `<body>`

Send a directed message to one session's durable mailbox.

```bash
em-proj message send --to <session_id> <body> --json
```

Emit stdout verbatim.
- Exit 0 = delivered.
- Exit 2 = recipient does not exist (not_found).
- Exit 4 = partial fan-out.

---

### /em-sessions broadcast `<body>` [--scope project|upstream|machine]

Broadcast a message to every live session in a scope (excluding yourself). Default
scope is `machine`.

```bash
em-proj message broadcast <body> --scope <scope> --json
```

Emit stdout verbatim.
- Exit 0 = delivered, or the scope had zero recipients.
- Exit 4 = partial fan-out.

</action>

<scope>
**READ surface** (safe to call at any time — no side effects):

- `/em-sessions list` — live session enumeration
- `/em-sessions show <session_id>` — single-session detail lookup
- `/em-sessions inbox [--session <session_id>]` — mailbox peek, ALWAYS `--peek`,
  never consumes

**WRITE surface** (explicit message operations — first-class CLI operations, not
ad-hoc state writes):

- `/em-sessions send <session_id> <body>` — directed send to one session
- `/em-sessions broadcast <body> [--scope project|upstream|machine]` — fan-out send
  to a scope

**NEVER used for:**

`state set` / `state del` (KV writes — `/em-global-state`'s domain); the lock-acquire
or claim-acquire verb; `session register` / `session listen` / `session stop`
(session and daemon lifecycle is the SessionStart hook's and daemon's job — Phase 12
HOOK-01 — never a skill-invoked write). This skill is purely a registry/mailbox
viewer plus an explicit message-send surface — not a general-purpose state or
lifecycle mutation tool.
</scope>

<related>
- `em-proj` CLI — install with `uv tool install --editable .` from the em-proj repo.
- `/em-global-state` — sibling skill; state/locks/claims/reservations only, never
  session registry or messaging.
- Phase 8 (em-proj) — session registry implementation.
- Phase 9 (em-proj) — durable per-session mailbox (Redis Streams) implementation.
- Phase 10 (em-proj) — directed/broadcast/topic send patterns implementation.
- Phase 11 (em-proj) — listener daemon (liveness, not delivery — the daemon never
  writes the mailbox).
- Phase 12 (em-proj) — SessionStart/UserPromptSubmit hook wiring and this skill.
</related>
