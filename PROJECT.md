# em-proj

## What This Is

A personal tooling CLI under the `em-proj` top-level namespace. The first deliverable (this milestone) lands the state primitive — `em-proj state` — a coordination layer for multiple Claude Code (and eventually other) terminal sessions running in parallel on the same machine. The state primitive exposes a kv store, advisory locks, and a long-lived "area claim" model that lets any session or sub-agent ask "is anyone else working on X?" before acting.

Future capabilities under `em-proj` will follow as additional subcommands (`session`, `message`, future orchestration tooling) — the namespace is the user's seed of personal project tooling, distinct from framework CLIs like `gsd-sdk`.

## Core Value

A sub-agent, skill, or session can ask the substrate "is it safe to edit X, or is someone else working there?" and get a structured, parseable answer grounded in current cross-session reality.

## Requirements

### Validated

(None yet — ship to validate)

### Active

**M1 (this milestone) — bootstrap em-proj + land the state primitive end-to-end:**

- [ ] **`em-proj` CLI shell:** Python 3.12+ via `uv`, installable via `uv tool install em-proj` from local source; subcommand dispatch via `typer`; `--help`, exit codes, `--json` flag plumbing
- [ ] **`em-proj state` subcommand family:**
    - `get | set | del | list` — kv operations with atomic semantics
    - `lock | unlock` — short-lived advisory locks (process-scoped, 1s default timeout)
    - `claim | release | check` — long-lived area claims (TTL-scoped, refreshable, cross-process)
    - `set --ttl N` — first-class TTL on writes, not just locks
- [ ] **Persistent Redis backend** on loopback with `appendonly yes`, `appendfsync everysec`, `save 900 1`; managed via `brew services`; healthcheck path with clear error if redis isn't running
- [ ] **Lock default = block-with-1s-timeout, `--warn` opt-in** (per pitfalls research; safe-default principle)
- [ ] **Claim model** for area-of-interest declarations: default TTL 30min, refreshable, explicit `release` or auto-expire; holder metadata `{session_id, project_hash, reason, claimed_at, expires_at}` queryable via `check`
- [ ] **`em-proj state lock --hold -- <cmd>`** subcommand-wrapper ergonomic (auto-acquire, run command, auto-release on exit — the pattern that makes locks actually used correctly)
- [ ] **Session-id resolution** via `CLAUDE_CODE_SESSION_ID` env var (verified live by architecture research); fallback chain documented; refuse anonymous claims by default
- [ ] **Project-hash scheme** matches `~/.claude/projects/<hash>/` convention (`tr '/' '-'` on absolute path); auto-derived from `$PWD` via git-toplevel or PWD fallback
- [ ] **Machine-readable output by default** when stdout is not a TTY (`--json` for explicit); stable schema with version field; semantic exit codes (0/1/2/3 = success/error/not-found-or-not-held/held-by-another)
- [ ] **`/global-state` Claude skill** as the read + escape-hatch surface: `list`, `get`, `locks [--mine|--stale]`, `claims [--mine|--active|--stale]`, `unlock|release [--force]` with confirmation prompts for live holders. Designed to be invoked by sub-agents (parseable output), not just humans.
- [ ] **First validating consumer — active-workstream pointer:** `gsd-sdk workstream.set` writes through `em-proj state claim` (it's a claim, not a lock — sessions work on a workstream across many commands). Two sessions in the same project no longer silently clobber each other's active pointer.
- [ ] **Stale-detection composite:** PID + `proc_start_epoch` (defeats PID reuse) + boot-id backstop (defeats reboot-with-leftover-state); TTL is the final backstop
- [ ] **Multi-process test harness as the first M1 deliverable** — spawn `fork+exec`'d child processes, race them at the CLI boundary; in-process tests are insufficient for advisory locking (per pitfalls research)

### Out of Scope (M1)

- **Memory file write coordination** — deferred; will use the same `claim` model when it lands
- **Workstream hard-mutex consumer** — deferred; claim model covers the active-pointer case
- **Session registry as a feature** — M2; M1 lays the metadata schema so the registry is a read view, not a schema migration
- **Inter-session messaging / pub-sub** — M3; Redis pub/sub + keyspace notifications make this cheap when we get there
- **Coordinated workstream handoff** — M4+; built on session registry + messaging
- **`state watch` (key-change subscriptions)** — reserve the verb, do not implement; trivially addable later via Redis keyspace notifications
- **Cross-machine sync** — single-machine, single-user target
- **Multi-key atomic transactions** — Redis MULTI/EXEC + Lua available as escape hatch, but no SDK verb in M1
- **Other AI CLIs as first-class consumers** — design must not preclude them; Claude Code is the only initial consumer
- **Web UI / TUI dashboard** — the `/global-state` skill is the human surface

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

## Constraints

- **Environment:** macOS Darwin 24.x, zsh, single-user, single-machine
- **Backend:** Persistent Redis on loopback. Config: `appendonly yes`, `appendfsync everysec`, `save 900 1`. Managed via `brew services start redis`. AOF lives at `/opt/homebrew/var/db/redis/appendonly.aof` and is plaintext-inspectable.
- **Stack:** Python 3.12+. Distributed and installed via `uv tool install em-proj` from local source. Runtime deps: `typer` (CLI), `redis-py` (client), `pytest` (test only). No Node, no Go, no Rust.
- **Dependencies allowed:** `redis-py`, `typer`, `pytest`. The zero-deps stance from gsd-sdk's culture does not apply — `em-proj` is its own project and picks the right tools for the job.
- **CLI shape:** `em-proj <subcommand> <verb> [args...]`. Subcommands are top-level capabilities (`state`, future: `session`, `message`, etc.). Verbs are operations within a subcommand.
- **Output convention:** Plain text on TTY by default; machine-readable JSON when stdout is not a TTY OR when `--json` is passed. Stable schema with `"schema_version"` field. Errors go to stderr. Semantic exit codes documented in `--help`.
- **Shell idioms:** Avoid `ls | while read` patterns — the user's environment wraps `ls` with token-saving output that mangles downstream parsing. Use glob loops (`for f in dir/*`) only.
- **Communication style:** Concise, opinionated recommendations; no vendor-tradeoff matrices unless asked.
- **Skills are read+escape-hatch only:** The `/global-state` skill never writes through itself except for `unlock`/`release` with explicit `--force` and confirmation. Writes must declare themselves through code (CLI calls or SDK), never through ad-hoc debug surfaces.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| **Backend = persistent Redis** (not flat-files, not SQLite) | Future milestones (session registry, inter-session messaging, workstream handoff) want pub/sub + keyspace notifications + TTL natively. Redis primitives map directly. Persistent config (`appendonly yes`) makes it durable for a dev tool. | — Pending |
| **Stack = Python 3.12+ via uv** | User is primarily a Java/Python developer. Readability dominates for personal tooling that will be maintained across years. Latency cost of Python startup (~150ms) is invisible at this invocation frequency. `uv tool install` solves historic Python distribution pain. | — Pending |
| **Top-level namespace = `em-proj`** (new CLI, not extending gsd-sdk) | Personal tooling deserves a coherent namespace separable from frameworks. Avoids collision with gsd-sdk's existing `state.*` command family. Future personal utilities also land under `em-proj`. | — Pending |
| **Subcommand = `em-proj state`** | Inside a clean namespace, `state` reads naturally. No collision possible. | — Pending |
| **Lock default = block-with-1s-timeout, `--warn` opt-in** | Safe default at the SDK callsite; per pitfalls research, "advisory-warn becomes theater" — users normalize the prompt and the lock provides no real exclusion. 1s cap defeats mystery hangs. `--warn` preserves the human-override path for the rare callsites that want it. | — Pending |
| **Claim model added to M1** alongside lock | The actual M1 use case (active-workstream pointer) is a claim (long-lived, cross-process, session-scoped), not a lock (process-scoped). Building claim primitives now makes the validating consumer work right and gives sub-agents the "is it safe to edit X?" substrate the broader orchestrator vision requires. | — Pending |
| **Active-workstream pointer = first validating consumer** | One concrete, end-to-end use case proves the primitive before fanning out to memory-coord and workstream-lock. Defer those to follow-up milestones. | — Pending |
| **Multi-process test harness as first M1 deliverable** | Per pitfalls research, single-process tests give false confidence with advisory locking. The harness must exist before any locking code so we can TDD the primitive. | — Pending |
| **gsd-sdk integration via shell-out, not source extension** | `em-proj` is its own namespace; workstream.set refactor calls `gsd-sdk` as a subprocess. Keeps em-proj independent of gsd-sdk's release cycle and language stack. | — Pending |
| **Defer memory-coord and workstream-lock to follow-up milestones** | Same primitive, same backend; prove with one consumer first. | — Pending |

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
*Last updated: 2026-05-16 after Redis/Python/em-proj/claim-model pivot*
