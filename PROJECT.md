# em-proj

## What This Is

A personal tooling CLI under the `em-proj` top-level namespace — a coordination layer for multiple Claude Code (and eventually other) terminal sessions running in parallel on the same machine. The shipped `em-proj state` primitive (v1.0) exposes a kv store, advisory locks, a long-lived "area claim" model, and a project-scoped reservation registry that let any session or sub-agent ask "is anyone else working on X?" before acting.

The next layer (v1.1) adds `em-proj session` (a live registry of who's running, where, holding what) and `em-proj message` (broadcast/directed/topic messaging between sessions) — moving from passive "is it safe?" coordination toward active cross-session communication. Further capabilities (workstream handoff, memory-write coordination) follow as additional subcommands — the namespace is the user's seed of personal project tooling, distinct from framework CLIs like `gsd-sdk`.

## Core Value

A sub-agent, skill, or session can ask the substrate "is it safe to edit X, or is someone else working there?" and get a structured, parseable answer grounded in current cross-session reality.

## Current State

**Shipped: v1.0 em-proj state primitive** (2026-06-07) — 7 phases, 30 plans, 29/29 requirements validated.

`em-proj` is an installed Python CLI with the `state` primitive proven end-to-end:
KV store, advisory locks, long-lived claims, the `/em-global-state` skill surface,
the `gsd-sdk workstream.set` consumer (clobber eliminated), and a project-scoped
reservation registry for cross-clone coordination — all on persistent Redis.

See `## Requirements › Validated` below and `.planning/MILESTONES.md` for the
shipped record. Full phase detail: `.planning/milestones/v1.0-ROADMAP.md`.

## Current Milestone: v1.1 Session Registry + Inter-Session Messaging

**Goal:** Extend `em-proj` from *passive* coordination (claims/locks/reservations)
to *active* cross-session awareness and communication — a hybrid session registry
plus a messaging layer delivered through durable mailboxes and a live pub/sub
listener daemon, proven end-to-end by a message surfacing in a live Claude Code
session.

**Target features:**
- **`em-proj session`** family — explicit `register` + heartbeat; `list`/`show` as a hybrid view (live sessions enriched with the claims/locks/reservations each holds); stale-eviction via heartbeat TTL
- **Listener daemon** — per-session Redis pub/sub `SUBSCRIBE` process; auto-starts via SessionStart hook + explicit `session listen`/stop (both); drains received messages to the local mailbox on receipt; doubles as the registry heartbeat
- **`em-proj message`** family — `send` in three patterns (broadcast, directed by `session_id`, topic subscribe/unsubscribe), with selectable scope per message (`project_hash` | `upstream_identity` | machine-global); `inbox` to read the mailbox
- **Durable per-recipient mailbox** (Redis), pull-based, with message TTL/cleanup
- **Full end-to-end validating consumer** — a SessionStart/UserPromptSubmit hook that surfaces the mailbox into a live Claude Code session; a message from session A demonstrably appears in session B
- **Multi-process harness** — registry liveness, A→B delivery across all 3 patterns × 3 scopes, daemon drain, stale eviction
- **Skill read surface** — `/em-sessions` (or extend `/em-global-state`) for registry + inbox introspection

**Explicitly deferred this milestone:** `request/ack` reply semantics; blocking-wait (`message wait`/BLPOP) delivery.

See `### Active` requirements below for full detail.

## Requirements

### Validated

Shipped and verified in **v1.0** (2026-06-07). Full detail:
`.planning/milestones/v1.0-REQUIREMENTS.md`.

- ✓ **`em-proj` CLI shell** (typer dispatch, `--help`, semantic exit codes 0/1/2/3, `--json`/non-TTY JSON with `schema_version`) — v1.0
- ✓ **`em-proj state` KV** (`get|set|del|list`, atomic, first-class `--ttl`) — v1.0
- ✓ **Advisory locks** (`lock|unlock`, block-with-1s-timeout default + `--warn`, `lock --hold -- <cmd>` wrapper) — v1.0
- ✓ **Claim model** (30min refreshable TTL, holder metadata `{session_id, project_hash, reason, claimed_at, expires_at}`, anonymous-claim refusal) — v1.0
- ✓ **Persistent Redis backend** (loopback, `appendonly yes`/`everysec`/`save 900 1`, brew-managed, actionable unreachable error) — v1.0
- ✓ **Identity + stale-detection** (`CLAUDE_CODE_SESSION_ID` resolution, project-hash `tr '/' '-'` scheme, `{pid, proc_start_epoch, boot_id}` composite + TTL backstop) — v1.0
- ✓ **`/em-global-state` skill** (sub-agent-parseable read + confirmation-gated escape hatch) — v1.0
- ✓ **gsd-sdk workstream consumer** (`workstream.set` → `em-proj state claim` shell-out; clobber eliminated end-to-end) — v1.0
- ✓ **Project-scoped reservation registry** (`reserve` + `/em-check-state`, namespaced by `upstream_identity` for cross-clone coordination) — v1.0
- ✓ **Multi-process test harness** (fork+exec children racing at the CLI boundary; landed first, TDD) — v1.0

### Active

**v1.1 (this milestone) — Session Registry + Inter-Session Messaging.** Full
requirements with REQ-IDs in `.planning/REQUIREMENTS.md`:

- [ ] **Session registry (hybrid)** — explicit `register` + heartbeat, `list`/`show` enriched with held claims/locks/reservations, stale-eviction via heartbeat TTL
- [ ] **Listener daemon** — per-session pub/sub process, auto + explicit lifecycle, drains to mailbox, heartbeats the registry
- [ ] **Messaging** — broadcast / directed / topic patterns; selectable scope (project | upstream | machine-global); durable per-recipient mailbox with TTL
- [ ] **End-to-end CC integration** — SessionStart/UserPromptSubmit hook surfaces the mailbox into a live session (validating consumer; A→B proven)
- [ ] **Registry/inbox skill surface** + multi-process harness coverage

**Future (deferred beyond v1.1):**

- [ ] **Workstream handoff (M4+)** — formal protocol for one session passing work to another (built on registry + messaging + claims)
- [ ] **Memory / settings write coordination** — `~/.claude/projects/<hash>/memory/` and `.claude/settings.local.json` races, via the same claim model
- [ ] **Workstream hard-mutex consumer** — beyond the active-pointer claim case
- [ ] **Request/ack + blocking-wait messaging** — reply semantics and BLPOP-style delivery, deferred from v1.1

### Out of Scope

- **`state watch` (key-change subscriptions)** — verb reserved, not implemented; trivially addable later via Redis keyspace notifications
- **Cross-machine sync** — single-machine, single-user target
- **Multi-key atomic transactions** — Redis MULTI/EXEC + Lua available as escape hatch, but no SDK verb in M1
- **Other AI CLIs as first-class consumers** — design must not preclude them; Claude Code is the only initial consumer
- **Web UI / TUI dashboard** — the `/em-global-state` skill is the human surface

## Context

**Why this exists — concrete pain that triggered it:**

1. **Active-workstream pointer clobber.** `gsd-sdk workstream.set` writes to a global location; two sessions in the same project overwrite each other silently. The `/gsd-workstreams switch` doc mentions session-local storage "if the runtime exposes a session identifier" — but that branch may not be wired in Claude Code today.
2. **Project memory file races.** `~/.claude/projects/<project-hash>/memory/` (including `MEMORY.md`) is shared by every session on the same project root. No concurrency guard; last-write-wins.
3. **`.claude/settings.local.json` races.** Same shape as (2).

All three collapse onto the same missing primitive: a shared key-value store + claim/lock semantics every session can read/write through.

**Broader orchestrator vision (future milestones):**

- **Session registry / discovery** — cross-session awareness; who's running, on what project, since when. Reads off M1's holder metadata.
- **Inter-session messaging** — pub/sub or RPC between sessions ("finish your wave so I can start mine"). Redis pub/sub.
- **Coordinated workstream handoff** — formal protocol for one session passing work to another. Built on registry + messaging + claims.

M1's state primitive is the foundation all three of these are built on. Backend choice (persistent Redis) was driven by this — Redis primitives (`SET NX EX`, hashes, pub/sub, keyspace notifications, streams) map directly onto every future milestone without rearchitecture.

**Verified facts (from research):**

- **Session-id env var:** `CLAUDE_CODE_SESSION_ID` (UUID). Also available: `CLAUDECODE=1`, `CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_EXECPATH`. Verified live in this session.
- **Project-hash scheme:** `tr '/' '-'` on absolute path (no hashing, no truncation). `~/.claude/projects/-Users-emonical-projects-personal-ai-tools-orchestrator/` is the verified example.
- **Existing precedent:** `~/.claude/sessions/<pid>.json` files use `{pid, sessionId, procStart, cwd, host}` — mirror this for stale-detection metadata.
- **`gsd-sdk` lives at:** `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/` (TypeScript/Node 22, ESM, with a CJS sibling layer at `get-shit-done/bin/lib/*.cjs`).
- **gsd-sdk has its own `state.*` command family** (`state.load`, `state.json`, `state.get` for STATE.md). This is why `em-proj state` lives in a separate top-level namespace, not as a `gsd-sdk` subcommand.
- **`flock(1)` shell utility is not on macOS by default** — irrelevant now that we're on Redis, but documented as a reason the original "shell+flock" sketch was structurally weak.

**Code home decided:** `em-proj` is its own top-level personal tooling CLI, distinct from `gsd-sdk`. This repo bootstraps it. Integration with `gsd-sdk` is via shelling out to the `gsd-sdk` binary (the workstream.set refactor), not by extending the gsd-sdk source tree.

**Codebase state after v1.0:** ~3,900 LOC source across 11 Python modules
(`em_proj/` + `em_proj/state/{kv,lock,claim,reserve}.py`), ~10,900 LOC tests
(unit + multiprocess race harness + structural AST shape tests). Stack: Python
3.12+, `typer`, `redis-py`, `psutil`, `pytest`; installed via `uv tool install`.
Backed by brew-managed persistent Redis. `main` tagged `v1.0` at `ae27bb6`.

## Constraints

- **Environment:** macOS Darwin 24.x, zsh, single-user, single-machine
- **Backend:** Persistent Redis on loopback. Config: `appendonly yes`, `appendfsync everysec`, `save 900 1`. Managed via `brew services start redis`. AOF lives at `/opt/homebrew/var/db/redis/appendonly.aof` and is plaintext-inspectable.
- **Stack:** Python 3.12+. Distributed and installed via `uv tool install em-proj` from local source. Runtime deps: `typer` (CLI), `redis-py` (client), `pytest` (test only). No Node, no Go, no Rust.
- **Dependencies allowed:** `redis-py`, `typer`, `psutil`, `pytest`. The zero-deps stance from gsd-sdk's culture does not apply — `em-proj` is its own project and picks the right tools for the job. `psutil` added in Phase 3 (D-11) for cross-platform `proc_start_epoch` + `boot_id` probing in the stale-detection composite.
- **CLI shape:** `em-proj <subcommand> <verb> [args...]`. Subcommands are top-level capabilities (`state`, future: `session`, `message`, etc.). Verbs are operations within a subcommand.
- **Output convention:** Plain text on TTY by default; machine-readable JSON when stdout is not a TTY OR when `--json` is passed. Stable schema with `"schema_version"` field. Errors go to stderr. Semantic exit codes documented in `--help`.
- **Shell idioms:** Avoid `ls | while read` patterns — the user's environment wraps `ls` with token-saving output that mangles downstream parsing. Use glob loops (`for f in dir/*`) only.
- **Communication style:** Concise, opinionated recommendations; no vendor-tradeoff matrices unless asked.
- **Skills are read+escape-hatch only:** The `/em-global-state` skill never writes through itself except for `unlock`/`release` with explicit `--force` and confirmation. Writes must declare themselves through code (CLI calls or SDK), never through ad-hoc debug surfaces.

## Planning Conventions

These bind `/gsd-plan-phase` (and `/gsd-discuss-phase`) when structuring any phase for this project:

- **Every plan is a green vertical slice.** Each PLAN.md takes a coherent unit of behavior and ends with the FULL test suite green. Within a plan, do RED→GREEN per task (write the failing test, then make it pass) — but the plan as a whole is committed green and is independently reviewable and mergeable.
- **No standalone RED test-scaffold plan, and no "Wave 0 = lay all failing tests" wave.** The Nyquist rule (every requirement gets an automated verify *defined before the code*) is satisfied test-first *inside each slice*, NOT by batching all failing tests into a separate plan/commit that leaves the suite red across plan boundaries. A plan whose deliverable is "tests that stay red until a later plan" is a planning defect — split the phase by behavior instead.
- **Green-per-commit / main-always-green.** Because plans are green slices, branch history stays bisectable and any plan's commits can be reviewed and merged on their own — no squash-to-hide-red-history.
- **Incident (2026-06-08):** Phases 09 and 10 were planned RED-wave-then-GREEN-wave (10-01 was an all-failing-tests plan, committed + SUMMARY'd while red). Phase 09 reached `main` with red intermediate commits via PR #5 before this was caught; Phase 10 had to be squash-merged (PR #6) to keep `main` green. Fixed here so Phase 11+ are planned as green vertical slices.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| **Backend = persistent Redis** (not flat-files, not SQLite) | Future milestones (session registry, inter-session messaging, workstream handoff) want pub/sub + keyspace notifications + TTL natively. Redis primitives map directly. Persistent config (`appendonly yes`) makes it durable for a dev tool. | ✓ Good (v1.0) |
| **Stack = Python 3.12+ via uv** | User is primarily a Java/Python developer. Readability dominates for personal tooling that will be maintained across years. Latency cost of Python startup (~150ms) is invisible at this invocation frequency. `uv tool install` solves historic Python distribution pain. | ✓ Good (v1.0) |
| **Top-level namespace = `em-proj`** (new CLI, not extending gsd-sdk) | Personal tooling deserves a coherent namespace separable from frameworks. Avoids collision with gsd-sdk's existing `state.*` command family. Future personal utilities also land under `em-proj`. | ✓ Good (v1.0) |
| **Subcommand = `em-proj state`** | Inside a clean namespace, `state` reads naturally. No collision possible. | ✓ Good (v1.0) |
| **Lock default = block-with-1s-timeout, `--warn` opt-in** | Safe default at the SDK callsite; per pitfalls research, "advisory-warn becomes theater" — users normalize the prompt and the lock provides no real exclusion. 1s cap defeats mystery hangs. `--warn` preserves the human-override path for the rare callsites that want it. | ✓ Good (v1.0) |
| **Claim model added to M1** alongside lock | The actual M1 use case (active-workstream pointer) is a claim (long-lived, cross-process, session-scoped), not a lock (process-scoped). Building claim primitives now makes the validating consumer work right and gives sub-agents the "is it safe to edit X?" substrate the broader orchestrator vision requires. | ✓ Good (v1.0) |
| **Active-workstream pointer = first validating consumer** | One concrete, end-to-end use case proves the primitive before fanning out to memory-coord and workstream-lock. Defer those to follow-up milestones. | ✓ Good (v1.0) |
| **Multi-process test harness as first M1 deliverable** | Per pitfalls research, single-process tests give false confidence with advisory locking. The harness must exist before any locking code so we can TDD the primitive. | ✓ Good (v1.0) |
| **gsd-sdk integration via shell-out, not source extension** | `em-proj` is its own namespace; workstream.set refactor calls `gsd-sdk` as a subprocess. Keeps em-proj independent of gsd-sdk's release cycle and language stack. | ✓ Good (v1.0) |
| **Defer memory-coord and workstream-lock to follow-up milestones** | Same primitive, same backend; prove with one consumer first. | ✓ Good (v1.0) |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-07 — v1.0 shipped; v1.1 (session registry + messaging) scoping started*
