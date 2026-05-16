# orchestrator

## What This Is

A coordination layer for multiple Claude Code (and eventually other) terminal sessions running in parallel on the same machine. It's the missing shared-state and orchestration primitive that the GSD framework and Claude Code harness assume but don't provide. Built for a single developer running 4+ concurrent sessions across multiple projects.

## Core Value

Two concurrent Claude Code sessions can read and modify shared state — workstream pointers, project memory, settings — without silently clobbering each other.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] **State primitive (M1):** Shared key-value store under `~/.claude/global-state/` with atomic writes (temp+rename) and `flock(2)` for read-modify-write
- [ ] **`gsd-sdk state` subcommand:** `get | set | del | list | lock | unlock <namespace> <key>` exposed through the existing SDK
- [ ] **`/global-state` skill:** Human-facing inspection/debug surface (`list`, `get`, `unlock`)
- [ ] **Active-workstream use case end-to-end:** `gsd-sdk workstream.set` writes through the state primitive; two sessions in the same project no longer clobber each other's active pointer
- [ ] **Lock semantics:** Advisory-warn is the default; `--block` flag available from day one for hard-mutex callsites
- [ ] **Namespacing:** Per-machine flat directory layout with project-hash prefix on project-scoped keys (`active-workstream/<project-hash>`)
- [ ] **Stale lock handling:** Locks hold `{session_id, pid, host, acquired_at}`; auto-release on stale-PID detection; optional TTL

### Out of Scope

- **Memory file write coordination** — deferred to a follow-up milestone once the state primitive is proven; can land sooner if the spike reveals coupling
- **Per-workstream hard mutex (workstream-lock)** — same reasoning as above
- **Daemon or long-running service** — hard preference for shell+filesystem primitives; revisit only if flock/flat-files prove inadequate
- **Multi-key atomic transactions** — flat files + flock can't give this; SQLite is the upgrade path if needed
- **Cross-machine sync** — single-machine, single-user is the target environment
- **SQLite backend** — defer until flat-files demonstrably fail (multi-key atomicity or cross-machine sync)
- **Other AI CLIs as first-class consumers** — design shouldn't preclude them, but Claude Code is the only initial consumer

## Context

**Concrete pain that triggered this:**
1. **Active-workstream pointer clobber.** `gsd-sdk workstream.set` writes to a global location; two sessions in the same project overwrite each other silently. The `/gsd-workstreams switch` doc mentions session-local storage "if the runtime exposes a session identifier" — unclear if that branch is wired in Claude Code today.
2. **Project memory file races.** `~/.claude/projects/<project-hash>/memory/` (including `MEMORY.md`) is shared by every session on the same project root. No concurrency guard; last-write-wins.
3. **`.claude/settings.local.json` races.** Same shape as (2).

All three collapse onto one missing primitive: a shared key-value store every session can read/write through, with locks.

**Broader orchestrator vision (future milestones):**
- **Session registry / discovery** — cross-session awareness: who's running, on what project, since when
- **Inter-session messaging** — pub/sub or RPC between sessions (e.g., "finish your wave so I can start mine")
- **Coordinated workstream handoff** — formal protocol for one session passing work to another

The state primitive (M1) is the foundation all three of these will be built on.

**Code home — undecided:** Hybrid approach. Build it in this repo as a spike; decide between (a) standalone repo with scripts symlinked into `~/.claude/`, or (b) upstream contribution to gsd-sdk, after the v1 is working and we can see its shape.

**Investigation needed before planning:**
- Locate `gsd-sdk` source tree
- Confirm whether `workstream.set` is session-local today (and if so, how)
- Identify what session-id env var Claude Code exposes (`env | grep -i claude` inside a session)

## Constraints

- **Environment:** macOS Darwin 24.x, zsh, single-user, single-machine
- **Tech preference:** Shell + filesystem primitives over daemons or new long-running services for this kind of plumbing
- **Concurrency primitive:** `flock(2)` (BSD on macOS) — POSIX `fcntl` not needed at this scope
- **Atomicity:** Temp-file + `rename(2)` for writes; no partial-write windows
- **Shell idioms:** Avoid `ls | while read` patterns — the user's environment wraps `ls` with token-saving output that mangles downstream parsing. Use glob loops (`for f in dir/*`) only.
- **Communication style:** Concise, opinionated recommendations; no vendor-tradeoff matrices unless asked
- **Consumer surface:** The SDK subcommand is what other skills consume; the `/global-state` skill is for human inspection/debug

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Flat files + `flock` over SQLite for v1 | Cheap, inspectable, survives crashes; SQLite is the natural upgrade if multi-key atomicity or cross-machine sync is needed | — Pending |
| Per-machine namespace with project-hash prefix (not per-project siloed) | Lets future cross-project skills (session registry, machine-wide coordinator) see everything without redesign | — Pending |
| Advisory-warn locks as default, `--block` flag from day one | Easier to evolve into hard; almost impossible to evolve down from hard once consumers expect blocking. But memory-coord and workstream-lock will want blocking, so build both into the SDK upfront | — Pending |
| Ship state primitive solving active-workstream first; defer memory-coord and workstream-lock | Validates the primitive with a single concrete consumer before fanning out | — Pending |
| "Hybrid" code home — spike here, decide standalone-vs-upstream later | Premature to choose before we see how invasive the gsd-sdk integration actually is | — Pending |

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
*Last updated: 2026-05-16 after initialization*
