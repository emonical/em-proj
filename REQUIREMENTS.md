# Requirements: em-proj

**Defined:** 2026-05-17
**Core Value:** A sub-agent, skill, or session can ask the substrate "is it safe to edit X, or is someone else working there?" and get a structured, parseable answer grounded in current cross-session reality.

## v1 Requirements

Requirements for milestone v1.0 — bootstrap `em-proj` + land the `state` primitive end-to-end. Each maps to one roadmap phase (see Traceability).

### CLI — em-proj CLI shell

- [ ] **CLI-01**: Installable via `uv tool install em-proj` from local source
- [ ] **CLI-02**: Subcommand dispatch via `em-proj <subcommand> <verb> [args...]` (typer)
- [ ] **CLI-03**: `--help` available for every subcommand and verb
- [ ] **CLI-04**: Semantic exit codes — 0 success / 1 error / 2 not-found-or-not-held / 3 held-by-another
- [ ] **CLI-05**: Machine-readable JSON when stdout is not a TTY OR when `--json` is passed; output includes a `schema_version` field; errors go to stderr

### KV — `em-proj state` key/value operations

- [ ] **KV-01**: User can `em-proj state get | set | del | list` with atomic write semantics
- [ ] **KV-02**: User can set a TTL on any write via `em-proj state set --ttl <seconds>` (first-class, not just locks)

### LOCK — short-lived advisory locks

- [ ] **LOCK-01**: User can `em-proj state lock <name>` and `unlock <name>` (process-scoped)
- [ ] **LOCK-02**: `lock` blocks with a 1-second timeout by default; `--warn` flag opts into warn-mode for the human-override path
- [ ] **LOCK-03**: User can `em-proj state lock --hold <name> -- <cmd...>` to auto-acquire, run the command, and auto-release on exit

### CLAIM — long-lived area claims

- [ ] **CLAIM-01**: User can `em-proj state claim <area> [--ttl <secs>]` (default 30min, refreshable) and `release <area>`
- [ ] **CLAIM-02**: User can `em-proj state check <area>` to see holder metadata `{session_id, project_hash, reason, claimed_at, expires_at}`
- [ ] **CLAIM-03**: Claims refuse anonymous holders by default — session-id must be resolvable or the call errors with exit 1

### REDIS — persistent Redis backend

- [ ] **REDIS-01**: System runs against a loopback Redis configured with `appendonly yes`, `appendfsync everysec`, `save 900 1`; managed via `brew services`
- [ ] **REDIS-02**: `em-proj state` commands emit a clear, actionable error when Redis is unreachable (no cryptic connection traces)

### IDENT — identity, namespacing, stale-detection

- [ ] **IDENT-01**: Session-id is resolved from `CLAUDE_CODE_SESSION_ID` with a documented fallback chain; project-hash is derived from `$PWD` (git-toplevel fallback) via `tr '/' '-'` on the absolute path, matching the `~/.claude/projects/<hash>/` convention exactly
- [ ] **IDENT-02**: Stale detection uses a composite `{pid, proc_start_epoch, boot_id}` with TTL as the final backstop

### SKILL — `/global-state` Claude skill

- [ ] **SKILL-01**: User (or sub-agent) can run `/global-state list`, `get <key>`, `locks [--mine|--stale]`, `claims [--mine|--active|--stale]` for parseable read access
- [ ] **SKILL-02**: User can run `/global-state unlock|release [--force]` as an escape hatch, with confirmation prompts when a live holder exists
- [ ] **SKILL-03**: Skill output is parseable by sub-agents (stable schema, no ad-hoc formatting)

### CONSUMER — first validating consumer

- [ ] **CONSUMER-01**: `gsd-sdk workstream.set` writes through `em-proj state claim` via shell-out (no source extension of gsd-sdk)
- [ ] **CONSUMER-02**: Two concurrent Claude Code sessions in the same project no longer silently clobber each other's active-workstream pointer (demonstrated end-to-end)

### TEST — multi-process test harness

- [ ] **TEST-01**: A multi-process test harness exists that spawns `fork+exec`'d child processes and races them at the `em-proj` CLI boundary
- [ ] **TEST-02**: The test harness lands as the first M1 deliverable, before any locking or claim code (TDD-first per pitfalls research)

## v2 Requirements

Deferred to future milestones (M2+). Tracked here so the design space stays visible.

### Session Registry (M2)

- **REG-01**: Cross-session discovery — read view over M1's holder metadata; `who's running, on what project, since when`

### Messaging (M3)

- **MSG-01**: Inter-session pub/sub or RPC via Redis pub/sub + keyspace notifications

### Workstream Handoff (M4+)

- **HAND-01**: Formal protocol for one session passing work to another (built on registry + messaging + claims)

### Memory Coordination (deferred)

- **MEMC-01**: `~/.claude/projects/<hash>/memory/` write coordination via the same `claim` model
- **MEMC-02**: `.claude/settings.local.json` write coordination via the same `claim` model
- **MEMC-03**: Workstream hard-mutex consumer (claim model covers the active-pointer case; hard mutex deferred)

## Out of Scope

Explicitly excluded from M1. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Memory file write coordination | Deferred; will use the same `claim` model when it lands |
| Workstream hard-mutex consumer | Deferred; claim model covers the active-pointer case for now |
| Session registry as a feature | M2; M1 lays the metadata schema so the registry is a read view, not a schema migration |
| Inter-session messaging / pub-sub | M3; Redis pub/sub + keyspace notifications make this cheap when we get there |
| Coordinated workstream handoff | M4+; built on session registry + messaging |
| `em-proj state watch` (key-change subscriptions) | Reserve the verb, do not implement; trivially addable later via Redis keyspace notifications |
| Cross-machine sync | Single-machine, single-user target |
| Multi-key atomic transactions | Redis MULTI/EXEC + Lua available as escape hatch, but no SDK verb in M1 |
| Other AI CLIs as first-class consumers | Design must not preclude them; Claude Code is the only initial consumer |
| Web UI / TUI dashboard | The `/global-state` skill is the human surface |

## Traceability

Which phases cover which requirements. Updated by the roadmapper.

| Requirement | Phase | Status |
|-------------|-------|--------|
| CLI-01 | TBD | Pending |
| CLI-02 | TBD | Pending |
| CLI-03 | TBD | Pending |
| CLI-04 | TBD | Pending |
| CLI-05 | TBD | Pending |
| KV-01 | TBD | Pending |
| KV-02 | TBD | Pending |
| LOCK-01 | TBD | Pending |
| LOCK-02 | TBD | Pending |
| LOCK-03 | TBD | Pending |
| CLAIM-01 | TBD | Pending |
| CLAIM-02 | TBD | Pending |
| CLAIM-03 | TBD | Pending |
| REDIS-01 | TBD | Pending |
| REDIS-02 | TBD | Pending |
| IDENT-01 | TBD | Pending |
| IDENT-02 | TBD | Pending |
| SKILL-01 | TBD | Pending |
| SKILL-02 | TBD | Pending |
| SKILL-03 | TBD | Pending |
| CONSUMER-01 | TBD | Pending |
| CONSUMER-02 | TBD | Pending |
| TEST-01 | TBD | Pending |
| TEST-02 | TBD | Pending |

**Coverage:**
- v1 requirements: 20 total
- Mapped to phases: 0 (pending roadmap)
- Unmapped: 20 ⚠️ (resolved on roadmap creation)

---
*Requirements defined: 2026-05-17*
*Last updated: 2026-05-17 after initial definition for milestone v1.0*
