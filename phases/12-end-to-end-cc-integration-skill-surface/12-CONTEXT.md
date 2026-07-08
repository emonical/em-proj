# Phase 12: End-to-End CC Integration + Skill Surface - Context

**Gathered:** 2026-07-08
**Status:** Ready for planning
**Source:** Inline capture (lightweight — stack + CLI locked; integration surface verified directly; no research agent). Milestone-strategy fork resolved with the operator (see Strategic Framing).

<domain>
## Phase Boundary

Close the v1.1 milestone by proving the shipped `em-proj session` + `em-proj message`
CLI end-to-end through the Claude Code hook path, and exposing a read+send skill
surface. **No new source primitives** — the CLI verbs Phase 12 consumes are all
already shipped (Phases 8–11):

- `em-proj session register | heartbeat | listen | stop | list | show`
- `em-proj message inbox [--peek] [--since] | send --to | broadcast | subscribe | unsubscribe`

Phase 12 delivers only the **integration glue** (hook scripts + wiring), the
**skill surface**, and the **end-to-end proof**:

1. **HOOK-01** — a SessionStart hook that auto-registers the session and starts its
   listener daemon (`session register` then `session listen`, detached + idempotent).
2. **HOOK-02** — a UserPromptSubmit hook that surfaces unread mailbox messages into
   the live session on its next turn (`message inbox` → stdout-as-context).
3. **HOOK-03** — end-to-end demonstrated: a message from session A (directed/
   broadcast/topic) appears in session B's live context via the hook path.
4. **HOOK-04** — graceful degradation: em-proj-absent / Redis-down / daemon-down
   never breaks session startup (hook always exits 0, emits nothing on failure).
5. **SKILL-04 / SKILL-05** — a new `/em-sessions` skill: read (`session list/show`,
   `message inbox --peek`) + send (`message send`/`broadcast`) as first-class
   CLI-backed ops, consistent with the v1.0 skill-write boundary.

</domain>

<strategic_framing>
## Why this phase is deliberately minimal-footprint (operator decision 2026-07-08)

em-proj's v1.1 session-registry + inter-session-messaging model has been **superseded
at the architecture level** by the ai-dev-stack "Orchestration Substrate" design
(`ai-tools/ai-dev-stack/.planning/decisions/ORCHESTRATION-SUBSTRATE-ARCHITECTURE.md`,
2026-06-28). That note:

- Explicitly supersedes "an em-proj-centric coordination layer."
- Settles em-proj's role as "a proven implementation of the **narrow lease/allocation
  primitive**, NOT the coordination model" (its `reserve` migration-version use case).
- Lists em-proj `message/` + `session/` as **prior art to examine** for a future
  Redis-Streams→NATS comms bus — not the chosen substrate.
- Chooses a different model: an **awareness fabric** (graph-routed advisories, push/
  pull/continual, self-heal) over an **agent-neutral** wire (A2A via LiteLLM), because
  Claude/Cursor/Codex must all speak the protocol.

Concurrently, ai-dev-stack v1.2 (active) is spending a whole milestone **eliminating
per-session daemon proliferation** (measured 17 per-session MCP daemons → ~4–6
supervised).

**Consequence for Phase 12 (operator fork "A", 2026-07-08):** finish v1.1 as a
self-contained, minimal-footprint milestone — NOT the machine's always-on session bus.

- **Opt-in, not global.** Do NOT install the hooks into the global
  `~/.claude/settings.json`. Participation is explicit; default is zero footprint.
- Prove the mechanism + ship the skill; do not over-invest in making em-proj the
  machine-wide layer.
- em-proj session/message is recorded as **prior art feeding the ai-dev-stack v1.4
  substrate research**. The "is this the real coordination layer?" reconciliation is
  deferred there — out of scope for Phase 12.

</strategic_framing>

<decisions>
## Implementation Decisions

### Skill surface = NEW `/em-sessions` skill (LOCKED, 2026-07-08)
A new global skill, sibling to `/em-global-state`, NOT an extension of it. Mirrors the
CLI's own namespace split: `/em-global-state` stays focused on `state` (KV/locks/
claims/reservations); `/em-sessions` owns the registry + messaging surface.

- **Read surface:** `session list`, `session show <id>`, `message inbox --peek`.
  The skill's inbox read defaults to `--peek` so it never consumes the mailbox out
  from under the UserPromptSubmit hook (the hook is the consumer; the skill is a
  viewer).
- **Send surface:** `message send --to <id>` / `message broadcast --scope <...>`.
  `send`/`broadcast` are explicit message ops — allowed under the v1.0 skill-write
  boundary (they are first-class CLI operations, not ad-hoc state writes). Topic
  subscribe/unsubscribe MAY be included as send-adjacent ops.
- **NEVER** exposes `state set/del`, lock/claim acquire, or `session register/listen/
  stop` (lifecycle is the hook's/daemon's job, not a skill write).

### `/em-sessions` SKILL.md is ORCHESTRATOR-APPLIED, not executor-applied (LOCKED)
The skill file lives at `~/.claude/skills/em-sessions/SKILL.md` (global, like
`~/.claude/skills/em-global-state/SKILL.md`). **The gsd-executor permission scope
denies writes under `~/.claude/skills/`** (memory: cross-repo SKILL.md edits need the
orchestrator path). The plan MUST specify the full SKILL.md content as a deliverable
but mark it **orchestrator-applied** — the executor does not write it; the orchestrator
(or a manual post-step) creates the file after execution. Its acceptance criteria are
verified against the CLI behavior, not against an executor-written file.

### Hook scripts live IN THE REPO; wiring is REPO-SCOPED + env-gated (LOCKED)
- Hook scripts are new files in the em-proj repo (executor-writable, version-controlled)
  — e.g. under `scripts/hooks/`. Do NOT place them in `~/.claude/scripts/` (that is the
  home of the *separate* legacy `session-registry.py`; see coexistence below, and it is
  not executor-writable).
- Reference wiring ships in the em-proj **project** `.claude/settings.json`
  (`SessionStart` + `UserPromptSubmit` entries), referencing the scripts via
  `$CLAUDE_PROJECT_DIR/scripts/hooks/...`. This is repo-scoped by construction — it
  never touches global config.
- **Opt-in gate:** participation is OFF by default. A session participates only when
  `EM_SESSIONS_AUTOSTART=1` is set in its environment. When unset, both hooks are an
  immediate no-op (exit 0, no daemon, no output) → zero footprint. Both the
  SessionStart and UserPromptSubmit hooks honor the same gate.
- Document (do NOT ship) how to extend participation to other repos / globally by
  copying the same hook entries + setting the env var.

### HOOK-01 SessionStart hook behavior
On `EM_SESSIONS_AUTOSTART=1`: run `em-proj session register` then `em-proj session
listen` (detached, idempotent — double-start returns `already_running`). Reuses the
already-shipped idempotent daemon lifecycle from Phase 11. Registration + daemon are
one participation step. Coexists with (does not replace) the existing SessionStart
hooks.

### HOOK-02 UserPromptSubmit hook behavior — surface via stdout-as-context
On `EM_SESSIONS_AUTOSTART=1`: run `em-proj message inbox --json`, format unread
messages, and print to stdout. Claude Code surfaces UserPromptSubmit stdout as
additional context on the next turn (verified live: the Honcho memory hook uses this
exact path — "UserPromptSubmit hook additional context: ...").

- **Consume-on-surface.** Default `inbox` consumes (marks read) so each message
  surfaces exactly once — matches the mailbox's read-once semantics. Accepted tradeoff:
  if surfacing fails after consume, the message is lost; acceptable at this
  minimal/demo grade. (The daemon does NOT re-drain — Phase 11 locked "no double
  write"; the mailbox is the durable copy written at send time.)
- Empty mailbox → no output (do not emit an empty context banner every turn).

### HOOK-04 graceful degradation (applies to BOTH hooks)
Each hook script MUST NOT break session startup under any failure:
- em-proj not on PATH → exit 0, no output.
- Redis unreachable (`em-proj` exits 1 via `die_if_redis_unreachable`) → exit 0, no output.
- Daemon start failure / any exception → exit 0, no output.
Model this on the existing `session-registry.py` pattern (swallow all exceptions,
always return 0). No stderr noise that could surface as a broken-hook warning.

### Existing `session-registry.py` = COEXIST, untouched (LOCKED)
`~/.claude/scripts/session-registry.py` (em-proj **state KV**, keyed per-directory
`session.active.<dir>`, transcript-mtime liveness) predates and is orthogonal to the
v1.1 `em-proj session` HASH registry. It serves memory-sweep dir-guarding
(`/em-analyze-ai-memories`'s `check <dir>`). Phase 12 leaves it completely untouched —
no key collision (KV `session.active.*` vs HASH `session:<id>`), different concern.
Consolidating the two registries is a substrate-era question, explicitly deferred.

### Green vertical slices (project convention — LOCKED)
Each PLAN.md ends with the FULL suite green; RED→GREEN per task inside the slice; NO
standalone "lay all failing tests" wave-0 plan. Nyquist is satisfied test-first inside
each slice (per PROJECT.md Planning Conventions), NOT via a separate VALIDATION.md
batch — so no VALIDATION.md is expected for this run.

### Claude's Discretion (planner/executor)
- Hook script language (bash vs a small Python module reusing em_proj internals) —
  bash shelling to the `em-proj` binary is the simplest and keeps HOOK-04 trivial;
  prefer it unless there's a concrete reason to import em_proj.
- Exact plan split (aim for ~2 green slices; skill is a small orchestrator-applied
  deliverable that may ride its own plan or fold into the E2E plan).
- Precise message-formatting for the surfaced context block (sender, pattern, body).
- Whether the SessionStart hook also refreshes/reaps before registering.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### The CLI surface Phase 12 wires (already shipped — do NOT reimplement)
- `src/em_proj/session/__init__.py` — `session_app`: `register`, `heartbeat`, `list`,
  `show`, `listen` (`--foreground/--no-foreground`), `stop`. D-14 thin-wrapper contract.
- `src/em_proj/session/_daemon.py` — `_daemon_start` (detached, idempotent),
  `_daemon_stop`, `_daemon_foreground_run`, daemon HASH ops, heartbeat cadence.
- `src/em_proj/message/__init__.py` — `message_app`: `inbox` (`--peek`, `--since`),
  `send` (`--to`/`--topic`, `--scope`), `broadcast` (`--scope`), `subscribe`,
  `unsubscribe`. Exit codes incl. 4 = partial fan-out.
- `src/em_proj/message/_ops.py` — send-time durable mailbox write (MBOX-04 record),
  `msg:<recipient>` PUBLISH nudge, `MBOX_TTL_SECONDS`.
- `src/em_proj/identity.py` — `resolve_session_id` (`CLAUDE_CODE_SESSION_ID`),
  stale composite.

### Integration exemplars
- `~/.claude/scripts/session-registry.py` — the graceful-degradation pattern to mirror
  (swallow all errors, exit 0, shell to `em-proj`). COEXIST; do not modify.
- `~/.claude/skills/em-global-state/SKILL.md` — the skill structure/format to mirror
  for `/em-sessions` (frontmatter, `<objective>`, verb reference, `<scope>` READ vs
  WRITE vs NEVER, `--json` verbatim-emit convention).
- `~/.claude/settings.json` (SessionStart array) — how existing SessionStart hooks are
  wired; the new project-scoped hook coexists with these (does NOT edit the global file).

### Project conventions + test surface
- `CLAUDE.md` — `scripts/test.sh` dispatcher (never bare pytest/uv); structural tests
  under `tests/structural/` named for the invariant, not the phase; prohibited-import
  discipline.
- `tests/multiprocess/` — fork+exec harness; `tests/multiprocess/test_daemon_lifecycle.py`
  is the exemplar for daemon/session multiprocess tests. HOOK-03 extends this shape.
- Prior phase plans `.planning/phases/11-listener-daemon/11-0*-PLAN.md` — task/format
  exemplar (frontmatter, read_first, acceptance_criteria, must_haves, threat_model).

### Strategic context (read for framing, not implementation)
- `ai-tools/ai-dev-stack/.planning/decisions/ORCHESTRATION-SUBSTRATE-ARCHITECTURE.md`
  — why this phase is minimal-footprint; em-proj demoted to lease primitive + prior art.

</canonical_refs>

<specifics>
## Specific Ideas

- **HOOK-03 proof is two-layered:** (1) an automated, deterministic harness test that
  invokes the UserPromptSubmit hook SCRIPT with synthetic hook JSON on stdin against a
  session B whose mailbox holds a message, asserting the script's stdout contains the
  message body — this is the durable regression test for the hook path; plus a
  SessionStart-hook test asserting register+listen on gate-on and no-op+exit-0 on
  gate-off / Redis-down (HOOK-04). (2) A full A→B path test in the multiprocess harness:
  session A `message send --to B`, mailbox holds it, B's UserPromptSubmit hook surfaces
  it. The genuinely-live two-CC-session demo is documented as a manual validation step
  (a live CC session can't be fully automated), but the mechanism is automated.
- **`$CLAUDE_PROJECT_DIR`** is the documented way for a project settings.json hook to
  reference a repo-relative script path. Use it in the wiring.
- **Prohibited-import discipline:** if a hook is implemented in Python and needs
  process/subprocess, keep it in the hook script, not in `_ops.py` (which bans those
  imports via structural tests) — same rule Phase 11 followed for the daemon submodule.
- The `/em-sessions` skill must allowlist only `Bash` (+ `AskUserQuestion` if any
  send needs confirmation) like `em-global-state`; sends are explicit and need no
  displacement confirmation, so AskUserQuestion may be unnecessary.

</specifics>

<deferred>
## Deferred Ideas

- **Global / machine-wide auto-participation** — deliberately NOT shipped (operator
  fork A). Documented as an opt-in extension only.
- **Consolidating `session-registry.py` (KV dir-guard) with the v1.1 HASH registry** —
  substrate-era question; out of scope.
- **Reconciling em-proj session/message with the ai-dev-stack orchestration substrate**
  (comms bus, awareness fabric, agent-neutral A2A) — belongs to ai-dev-stack v1.4;
  em-proj session/message stands as prior art.
- **Request/ack reply semantics, blocking-wait delivery** — already deferred beyond v1.1.
- **Agent-neutral (Cursor/Codex) hook surface** — the substrate's concern, not this
  Claude-Code-only validating consumer.

</deferred>

---

*Phase: 12-end-to-end-cc-integration-skill-surface*
*Context gathered: 2026-07-08 via inline capture — 3 decisions locked (new /em-sessions skill; opt-in repo-scoped env-gated hooks; coexist with session-registry.py) + minimal-footprint milestone-close framing (operator fork A)*
