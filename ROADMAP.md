# Roadmap: em-proj

## Overview

Milestone v1.0 ships `em-proj` as an installable Python CLI and proves the `state` primitive end-to-end with the active-workstream pointer as the first validating consumer. The path: land the multi-process test harness and Redis infrastructure first (TDD-first per pitfalls research), then the CLI shell with KV operations as the first exercisable surface, then identity + locks, then claims, then the `/em-global-state` skill surface, and finally the `gsd-sdk workstream.set` integration that proves two concurrent sessions no longer clobber each other.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Test Harness + Redis Foundation** - Multi-process race harness and persistent Redis backend, the substrate every subsequent phase races against
- [ ] **Phase 2: CLI Shell + KV Primitive** - Installable `em-proj` CLI with `state get|set|del|list` (incl. `--ttl`), semantic exit codes, and Redis-error UX
- [ ] **Phase 3: Identity + Advisory Locks** - Session/project identity resolution, stale-detection composite, and short-lived `lock|unlock|lock --hold` primitives
- [x] **Phase 4: Long-Lived Claims** - `claim|release|check` with TTL, refreshable holder metadata, and anonymous-claim refusal
- [x] **Phase 5: `/em-global-state` Skill Surface** - Sub-agent-parseable read view and escape-hatch over the complete state primitive
- [ ] **Phase 6: gsd-sdk Workstream Consumer** - `gsd-sdk workstream.set` shells out through `em-proj state claim`; concurrent-session clobber demonstrated as resolved end-to-end

## Phase Details

### Phase 1: Test Harness + Redis Foundation
**Goal**: A multi-process test harness exists that races fork+exec'd child processes against a real, persistent Redis instance — the substrate every subsequent phase will build on and be validated against.
**Depends on**: Nothing (first phase)
**Requirements**: TEST-01, TEST-02, REDIS-01, CLI-01, CLI-02 _(CLI-01/02 remapped from Phase 2 per Phase 1 CONTEXT.md D-04..D-06 — traceability table update pending)_
**Success Criteria** (what must be TRUE):
  1. `brew services start redis` brings up a loopback Redis configured with `appendonly yes`, `appendfsync everysec`, `save 900 1`, and the AOF is visible at `/opt/homebrew/var/db/redis/appendonly.aof`
  2. A `pytest`-driven harness can spawn N `fork+exec`'d child processes that invoke a CLI binary (stub acceptable at this point) and assert on their combined exit codes, stdout, and effect on the shared Redis state
  3. The harness lands and passes its self-tests *before* any locking, claim, or consumer code is written (TDD-first ordering enforced)
**Plans:** 4 plans
Plans:
- [ ] 01-01-PLAN.md — Project skeleton: pyproject.toml + src/em_proj/ package + uv sync (CLI-01 partial)
- [ ] 01-02-PLAN.md — typer CLI scaffold (--version + --help) + uv tool install --editable . (CLI-01, CLI-02)
- [ ] 01-03-PLAN.md — Redis client wrapper + brew redis.conf REDIS-01 edits + verify-redis-config.sh (REDIS-01)
- [ ] 01-04-PLAN.md — Multi-process pytest harness fixtures + TEST-01/TEST-02 self-tests (TEST-01, TEST-02)
**UI hint**: no

### Phase 2: CLI Shell + KV Primitive
**Goal**: A user can `uv tool install em-proj` and immediately exercise `em-proj state get|set|del|list` against the Redis backend, with semantic exit codes, machine-readable output, and a clear error when Redis is unreachable.
**Depends on**: Phase 1
**Requirements**: CLI-01, CLI-02, CLI-03, CLI-04, CLI-05, KV-01, KV-02, REDIS-02
**Success Criteria** (what must be TRUE):
  1. `uv tool install em-proj` from local source installs the CLI; `em-proj --help` and `em-proj state --help` both render typer-formatted help
  2. `em-proj state set foo bar`, `em-proj state get foo`, `em-proj state del foo`, and `em-proj state list` operate atomically against Redis and return semantic exit codes (0/1/2/3)
  3. `em-proj state set foo bar --ttl 60` writes a value that Redis evicts after 60s (first-class TTL on writes, not just locks)
  4. When stdout is not a TTY or `--json` is passed, every command emits JSON with a `schema_version` field; errors go to stderr
  5. Stopping Redis and re-running any `em-proj state` command produces a one-line actionable error (e.g. "Redis unreachable at 127.0.0.1:6379 — run `brew services start redis`") with exit code 1, not a Python traceback
**Plans:** 5 plans
Plans:
- [x] 02-01-PLAN.md — Phase 1 carry-forward verification + empty state_app mount (CLI-01, CLI-02, CLI-03 partial)
- [x] 02-02-PLAN.md — em_proj/output.py envelope helpers + unit tests (CLI-05 contract)
- [x] 02-03-PLAN.md — em_proj/state/kv.py pure KV ops + validation + KEEPTTL semantics (KV-01, KV-02)
- [x] 02-04-PLAN.md — Verb wiring (get/set/del/list) with --json + --ttl + multiproc atomicity tests (KV-01, KV-02, CLI-03..05)
- [x] 02-05-PLAN.md — REDIS-02 verb-level test + structural shape test (D-14..D-19) + end-to-end phase verification
**UI hint**: no

### Phase 3: Identity + Advisory Locks
**Goal**: Every operation can resolve a session-id and project-hash, and a user can take and release short-lived advisory locks — including the `--hold -- <cmd>` wrapper that makes locks actually used correctly.
**Depends on**: Phase 2
**Requirements**: IDENT-01, IDENT-02, LOCK-01, LOCK-02, LOCK-03
**Success Criteria** (what must be TRUE):
  1. Inside a Claude Code session, `em-proj state` operations resolve `session_id` from `CLAUDE_CODE_SESSION_ID` and `project_hash` from `$PWD` via `tr '/' '-'` on the absolute path (git-toplevel fallback), matching the `~/.claude/projects/<hash>/` convention exactly
  2. Lock records carry a `{pid, proc_start_epoch, boot_id}` composite plus TTL backstop, and a stale-detection probe correctly identifies abandoned locks across PID reuse and reboot
  3. `em-proj state lock <name>` blocks for up to 1 second by default and then errors with exit code 3 (held-by-another); `--warn` opts into the warn-mode human-override path
  4. `em-proj state lock --hold <name> -- <cmd...>` auto-acquires the lock, runs `<cmd>`, and releases on exit (including on signal or crash), verified by the multi-process harness
  5. Two harness children racing `lock --hold` against the same name serialize correctly (one runs the wrapped command, the other waits then errors with exit 3)
**Plans:** 6 plans
Plans:
- [x] 03-01-PLAN.md — em_proj/identity.py session/project resolution + composite + psutil dep (IDENT-01)
- [x] 03-02-PLAN.md — Stale-detection composite probe (current_boot_id, probe_pid_alive, probe_proc_start_matches, is_holder_stale) (IDENT-02)
- [x] 03-03-PLAN.md — em_proj/state/lock.py pure ops + Lua compare-and-delete/swap scripts + emit_held_by_another (LOCK-01, LOCK-02 1s-block half)
- [x] 03-04-PLAN.md — lock/unlock verb wiring + --warn TTY-gated flow + --warn/--hold mutex (LOCK-01, LOCK-02 full)
- [x] 03-05-PLAN.md — lock --hold wrapper + refresher thread + signal/atexit cleanup + multi-process race tests (LOCK-03)
- [x] 03-06-PLAN.md — Structural shape test + stale-takeover multi-process test + bash scripts/verify-phase.sh 03 verification
**UI hint**: no

### Phase 4: Long-Lived Claims
**Goal**: A user (or sub-agent) can declare a long-lived claim over an area of interest with TTL and refresh semantics, and query the holder metadata that answers "is it safe to edit X?".
**Depends on**: Phase 3
**Requirements**: CLAIM-01, CLAIM-02, CLAIM-03
**Success Criteria** (what must be TRUE):
  1. `em-proj state claim <area>` takes a 30-minute claim by default; `--ttl <secs>` overrides; repeating the call by the same holder refreshes the TTL rather than erroring
  2. `em-proj state check <area>` returns the holder record `{session_id, project_hash, reason, claimed_at, expires_at}` in JSON (or formatted text on TTY), with exit code 0 if held by anyone and 2 if not held. (Ownership comparison — "is it me holding it?" — is the SDK consumer's job, and is also surfaced by the Phase 5 `/em-global-state claims --mine` skill.)
  3. `em-proj state release <area>` releases a claim held by the current session; releasing another session's claim errors with exit code 3 (escape hatch is the `/em-global-state` skill, not the SDK)
  4. With `CLAUDE_CODE_SESSION_ID` unset and no fallback resolvable, `em-proj state claim <area>` refuses with exit code 1 and a one-line "anonymous claims refused" error
**Plans:** 4 plans
Plans:
**Wave 1**
- [x] 04-01-PLAN.md — src/em_proj/state/claim.py pure ops + Lua scripts (CLAIM-01, CLAIM-02)

**Wave 2** *(blocked on Wave 1 completion)*
- [x] 04-02-PLAN.md — claim/release/check verb wiring in state/__init__.py + anonymous-refusal gate (CLAIM-01, CLAIM-02, CLAIM-03)

**Wave 3** *(blocked on Wave 2 completion)*
- [x] 04-03-PLAN.md — Multi-process race tests: race-winner/loser, holder refresh, non-holder release, anonymous refusal (CLAIM-01, CLAIM-02, CLAIM-03)

**Wave 4** *(blocked on Wave 3 completion)*
- [x] 04-04-PLAN.md — Structural shape test (test_phase_04_shape.py) + verify-phase.sh 04 acceptance gate (CLAIM-01, CLAIM-02, CLAIM-03)
**UI hint**: no

### Phase 5: `/em-global-state` Skill Surface
**Goal**: A sub-agent or human can introspect cross-session state and exercise an escape hatch for stuck holders without hand-rolling Redis queries — the read+escape-hatch surface over the now-complete state primitive.
**Depends on**: Phase 4
**Requirements**: SKILL-01, SKILL-02, SKILL-03
**Success Criteria** (what must be TRUE):
  1. `/em-global-state list`, `/em-global-state get <key>`, `/em-global-state locks [--mine|--stale]`, and `/em-global-state claims [--mine|--active|--stale]` all return parseable, stable-schema output suitable for a sub-agent to consume
  2. `/em-global-state unlock <name>` and `/em-global-state release <area>` work as escape hatches; with a live holder, the skill prompts for confirmation; `--force` bypasses confirmation
  3. The skill never writes through itself except for `unlock`/`release` (verified by audit of the skill surface) — all other writes flow through code, not ad-hoc debug commands
**Plans:** 5 plans
Plans:
**Wave 1** *(parallel — no shared files)*
- [x] 05-01-PLAN.md — lock_list_by_prefix pure op in lock.py + unit tests (SKILL-01)
- [x] 05-02-PLAN.md — claim_list_by_prefix pure op in claim.py + unit tests (SKILL-01)

**Wave 2** *(blocked on Wave 1)*
- [x] 05-03-PLAN.md — Wire lock-list + claim-list verbs in state/__init__.py + multi-process tests (SKILL-01, SKILL-03)

**Wave 3** *(blocked on Wave 2)*
- [x] 05-04-PLAN.md — ~/.claude/skills/em-global-state/SKILL.md with all 6 verbs + confirmation flow (SKILL-01, SKILL-02, SKILL-03)

**Wave 4** *(blocked on Wave 3)*
- [x] 05-05-PLAN.md — Structural shape test (test_phase_05_shape.py) + SC#3 write-boundary audit + verify-phase.sh 05 gate (SKILL-01, SKILL-02, SKILL-03)
**UI hint**: no

### Phase 6: gsd-sdk Workstream Consumer
**Goal**: `gsd-sdk workstream.set` writes through `em-proj state claim` via shell-out, proving the primitive end-to-end against the original pain — two concurrent Claude Code sessions in the same project no longer silently clobber each other's active-workstream pointer.
**Depends on**: Phase 5
**Requirements**: CONSUMER-01, CONSUMER-02
**Success Criteria** (what must be TRUE):
  1. `gsd-sdk workstream.set <name>` shells out to `em-proj state claim` (no source extension of `gsd-sdk` — integration is via subprocess only)
  2. Two harness children simulating concurrent Claude Code sessions in the same project both attempting `workstream.set` produce a deterministic outcome: one wins the claim, the other receives a structured "held by session <id>" error rather than a silent clobber
  3. A human-runnable demo (or harness fixture) reproduces the original pain (clobber under the old path) and the resolution (claim refusal under the new path) side-by-side
**Plans**: TBD
**UI hint**: no

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Test Harness + Redis Foundation | 0/TBD | Not started | - |
| 2. CLI Shell + KV Primitive | 0/TBD | Not started | - |
| 3. Identity + Advisory Locks | 6/6 | Complete | 2026-05-23 |
| 4. Long-Lived Claims | 4/4 | Complete | 2026-05-24 |
| 5. `/em-global-state` Skill Surface | 5/5 | Complete | 2026-05-26 |
| 6. gsd-sdk Workstream Consumer | 0/TBD | Not started | - |
