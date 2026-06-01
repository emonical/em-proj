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

### SKILL — `/em-global-state` Claude skill

- [ ] **SKILL-01**: User (or sub-agent) can run `/em-global-state list`, `get <key>`, `locks [--mine|--stale]`, `claims [--mine|--active|--stale]` for parseable read access
- [ ] **SKILL-02**: User can run `/em-global-state unlock|release [--force]` as an escape hatch, with confirmation prompts when a live holder exists
- [ ] **SKILL-03**: Skill output is parseable by sub-agents (stable schema, no ad-hoc formatting)

### CONSUMER — first validating consumer

- [ ] **CONSUMER-01**: `gsd-sdk workstream.set` writes through `em-proj state claim` via shell-out (no source extension of gsd-sdk)
- [ ] **CONSUMER-02**: Two concurrent Claude Code sessions in the same project no longer silently clobber each other's active-workstream pointer (demonstrated end-to-end)

### RESERVE — project-scoped reservation registry (cross-clone coordination)

- [ ] **RESERVE-01**: Reservations namespace by a stable `upstream_identity` derived from `git remote get-url origin` (slug or hash; project-agnostic, shared across sibling clones of the same upstream repo). Distinct from the per-clone `project_hash` used by Phase 4 claims and Phase 6 workstreams.
- [ ] **RESERVE-02**: At reservation-claim time, the holder dict auto-stamps `workstream` (read from the calling clone's `workstream.active` Phase 6 claim). Holders thus carry `{session_id, project_hash (caller's local), upstream_identity, workstream, reason, claimed_at, expires_at}`.
- [ ] **RESERVE-03**: `/em-check-state` (no args) auto-resolves `upstream_identity` from current `cwd`'s `git remote get-url origin` and returns ALL reservations against that identity, grouped by category prefix (the part of `<category>.<resource>` before the first dot).
- [ ] **RESERVE-04**: `/em-check-state --category <name>` filters to one category; `--upstream <url-or-identity>` overrides cwd-based resolution to query reservations against a different upstream from anywhere.
- [ ] **RESERVE-05**: New verb shape `em-proj state reserve <category>.<resource> [--reason <text>] [--ttl <secs>] [--workstream <name>]` — sugar over `claim` that uses `upstream_identity` instead of `project_hash` and auto-stamps `workstream`. When `workstream.active` is unset AND `--workstream` is not passed: TTY prompts; non-TTY exits 1 with actionable error. No silent heuristic fallback.

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
| Web UI / TUI dashboard | The `/em-global-state` skill is the human surface |

## Traceability

Which phases cover which requirements. Updated by the roadmapper.

| Requirement | Phase | Status |
|-------------|-------|--------|
| CLI-01 | Phase 2 | Pending |
| CLI-02 | Phase 2 | Pending |
| CLI-03 | Phase 2 | Pending |
| CLI-04 | Phase 2 | Pending |
| CLI-05 | Phase 2 | Pending |
| KV-01 | Phase 2 | Pending |
| KV-02 | Phase 2 | Pending |
| LOCK-01 | Phase 3 | Pending |
| LOCK-02 | Phase 3 | Pending |
| LOCK-03 | Phase 3 | Pending |
| CLAIM-01 | Phase 4 | Pending |
| CLAIM-02 | Phase 4 | Pending |
| CLAIM-03 | Phase 4 | Pending |
| REDIS-01 | Phase 1 | Pending |
| REDIS-02 | Phase 2 | Pending |
| IDENT-01 | Phase 3 | Pending |
| IDENT-02 | Phase 3 | Pending |
| SKILL-01 | Phase 5 | Pending |
| SKILL-02 | Phase 5 | Pending |
| SKILL-03 | Phase 5 | Pending |
| CONSUMER-01 | Phase 6 | Pending |
| CONSUMER-02 | Phase 6 | Pending |
| RESERVE-01 | Phase 7 | Pending |
| RESERVE-02 | Phase 7 | Pending |
| RESERVE-03 | Phase 7 | Pending |
| RESERVE-04 | Phase 7 | Pending |
| RESERVE-05 | Phase 7 | Pending |
| TEST-01 | Phase 1 | Pending |
| TEST-02 | Phase 1 | Pending |

**Coverage:**
- v1 requirements: 29 total
- Mapped to phases: 29 ✓
- Unmapped: 0

**Per-phase distribution:**

| Phase | REQ-IDs | Count |
|-------|---------|-------|
| Phase 1: Test Harness + Redis Foundation | TEST-01, TEST-02, REDIS-01 | 3 |
| Phase 2: CLI Shell + KV Primitive | CLI-01, CLI-02, CLI-03, CLI-04, CLI-05, KV-01, KV-02, REDIS-02 | 8 |
| Phase 3: Identity + Advisory Locks | IDENT-01, IDENT-02, LOCK-01, LOCK-02, LOCK-03 | 5 |
| Phase 4: Long-Lived Claims | CLAIM-01, CLAIM-02, CLAIM-03 | 3 |
| Phase 5: `/em-global-state` Skill Surface | SKILL-01, SKILL-02, SKILL-03 | 3 |
| Phase 6: gsd-sdk Workstream Consumer | CONSUMER-01, CONSUMER-02 | 2 |
| Phase 7: Project-Scoped Reservation Registry | RESERVE-01, RESERVE-02, RESERVE-03, RESERVE-04, RESERVE-05 | 5 |
| **Total** | | **29** |

> Note: previous frontmatter cited "20 total" v1 requirements; the actual count enumerated above and in the table is 29 (24 from the original M1 scope + 5 from the Phase 7 M1-extension RESERVE group). Coverage is computed against the 29 enumerated requirements.

---
*Requirements defined: 2026-05-17*
*Last updated: 2026-05-17 — Traceability populated with v1.0 phase mappings*
