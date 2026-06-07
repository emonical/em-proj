# Phase 1: Test Harness + Redis Foundation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-17
**Phase:** 1-Test Harness + Redis Foundation
**Areas discussed:** Project skeleton + Phase 1 deliverable boundary, Redis client setup + test lifecycle, Test harness API shape, Healthcheck UX

**Mode notes:** ADVISOR_MODE=true (USER-PROFILE.md present). Calibration tier = `minimal_decisive` (vendor_philosophy=opinionated). NON_TECHNICAL_OWNER=false (self-directed learning, no jargon frustration trigger, explanation_depth=concise). Parallel research-agent spawn was skipped because (a) profile signals (opinionated, fast-intuitive, "no comparison tables unless requested") favor direct recommendations, and (b) PROJECT.md already captures verified-facts research that decided most of the M1 stack — spawning advisor agents would have been theater. User was informed and did not redirect.

---

## Project Skeleton + Phase 1 Deliverable Boundary

Skeleton sub-decisions (no question asked — recommended as `src/em_proj/` layout, `__main__.py` + `cli.py`, `tests/unit/` + `tests/multiprocess/`; user accepted by proceeding):

| Option | Description | Selected |
|--------|-------------|----------|
| `src/em_proj/` layout (PyPA standard) | Prevents accidental imports of pre-install code; plays nicely with `uv tool install --editable` | ✓ |
| Flat `em_proj/` at repo root | Simpler but less robust for editable installs | |

**Phase 1 boundary question (the real ask):**

| Option | Description | Selected |
|--------|-------------|----------|
| `python -m em_proj` stub (workflow recommendation) | Phase 1 ships `em_proj/__main__.py` that prints 'ok' and exits 0. Harness races `python -m em_proj`. Phase 2 swaps `uv tool install` + real binary onto PATH. Honors REQ-ID mapping (CLI-01 stays in Phase 2). | |
| **uv tool install moves to Phase 1** | Phase 1 also lands `uv tool install em-proj` + typer skeleton + `--version`/`--help` so harness races the real binary from the start. Cleaner harness, but pulls CLI-01..03 into Phase 1 (re-mapping needed). | ✓ |
| Let me describe a third option | (open-ended) | |

**User's choice:** uv tool install moves to Phase 1
**Notes:** Decision drives REQ-ID remap captured in CONTEXT.md: CLI-01 + CLI-02 move into Phase 1; CLI-03..05 stay in Phase 2 (subcommand-level `--help`, semantic exit codes across verbs, and JSON output only become exercisable once verbs land). ROADMAP.md + REQUIREMENTS.md traceability need updating after CONTEXT.md commits.

---

## Redis Client Setup + Test Lifecycle

User initially asked for clarification on how the client model interacts with multiple concurrent sessions. I reframed: cross-session safety lives at the Redis-server layer (single-threaded command processor + atomic primitives + Lua scripts), not the client layer. Each `em-proj` invocation is its own short-lived process — "global lazy client" only means "within this one process." After the explanation, the question was re-asked with that framing.

### Client connection model

| Option | Description | Selected |
|--------|-------------|----------|
| **Lazy module-level `Redis()` per process, sync, default pool** | Each em-proj invocation lazy-inits one Redis() at first call, reuses for that invocation's 1-3 round-trips, exits. Cross-session safety = Redis-server concern. Simplest, lowest-overhead. | ✓ |
| Per-call client, sync redis-py | New `Redis()` per CLI invocation, explicit `.close()`. More overhead, zero shared mutable state. | |
| redis.asyncio (async client) | Overkill for single-process CLI — every command would `asyncio.run(...)`. | |

**User's choice:** Lazy module-level Redis() per process
**Notes:** Critical reframing in CONTEXT.md D-09: all M1 operations are stateless from a connection standpoint (one command or one Lua script per op). Avoid `WATCH`/`MULTI`/`EXEC`; use Lua scripts for atomicity (no connection-state coupling).

### Test Redis lifecycle

| Option | Description | Selected |
|--------|-------------|----------|
| **Brew-managed loopback, db=15 for tests, FLUSHDB per test** | Same brew-managed Redis as prod, namespaced to logical DB 15. No extra infra; can't corrupt prod data on db=0; identical Redis version/config as prod. | ✓ |
| Ephemeral redis-server per pytest session | Spin up `redis-server --port <free> --save '' --appendonly no` as session fixture. Stronger isolation but adds process management + tests run against different config than prod. | |
| Other / hybrid | (open-ended) | |

**User's choice:** Brew-managed loopback, db=15
**Notes:** Test-prod parity prioritized over isolation. Reconsider if config drift becomes a real problem.

---

## Test Harness API Shape

| Option | Description | Selected |
|--------|-------------|----------|
| **pytest fixture `multiproc_race`** | `results = multiproc_race([cmd1, cmd2, cmd3])` returns `[Result(returncode, stdout, stderr, duration_ms), ...]`. Parallel Popen launch + join. FLUSHDB autoclean baked in. Tests assert on sorted exit codes + stdout markers + post-race `redis.hgetall(...)`. | ✓ |
| Standalone `tests/race.py` helper module | No pytest magic, importable from non-pytest contexts (REPL). Less ergonomic in tests. | |
| Both — helper wrapped by fixture | Premature for M1 (no non-pytest consumer yet). | |

**User's choice:** pytest fixture `multiproc_race`
**Notes:** Critical implementation detail captured in CONTEXT.md D-14: fixture must launch ALL Popens in a tight loop (parallel start), then join. Sequential `subprocess.run` would defeat the race and silently invalidate every locking/claim test.

---

## Healthcheck UX

| Option | Description | Selected |
|--------|-------------|----------|
| **Wrapper-level only** | Shared Redis-client wrapper converts ConnectionError → one-line actionable message (exit 1). Harness uses pytest session fixture with `r.ping()` for precheck. No dedicated `em-proj health` subcommand. REDIS-02 *machinery* lands Phase 1; REQ-ID fully validates Phase 2 once more verbs exist. | ✓ |
| Wrapper + `em-proj health` subcommand | Same wrapper plus dedicated `health` verb (would add HEALTH-01). Harness could use it as precheck instead of in-process ping. | |
| Other / let me describe | (open-ended) | |

**User's choice:** Wrapper-level only
**Notes:** `em-proj health` deferred to post-M1 if ops debugging surfaces a need. Adding it now would be scope creep without a current REQ-ID.

---

## Claude's Discretion

User did not explicitly defer any decisions to Claude, but the following are noted in CONTEXT.md D-19+ as implementation details the planner can resolve:
- Exact module path for the Redis client wrapper (`em_proj/redis_client.py` vs `em_proj/backend/redis.py`)
- `Result` type for `multiproc_race` — `dataclass` vs `NamedTuple`
- pytest fixture scope for Redis cleanup — `session` vs `function`

## Deferred Ideas

- **`em-proj health` subcommand** — proposed (Area 4 Option B), rejected for Phase 1. Would become HEALTH-01 if revived later.
- **Ephemeral redis-server per pytest session** — proposed (Area 2 Option B), rejected. Revisit if test/prod config drift becomes a real problem.
- **`redis.asyncio` (async client)** — proposed (Area 2 Option C), rejected. Revisit in M3 (inter-session messaging) where Redis pub/sub becomes first-class.
- **Standalone `tests/race.py` helper module** — proposed (Area 3 Option B/C), rejected for Phase 1. Pull out of the fixture later if a manual REPL or external tool wants to invoke races directly.

**No scope creep raised during discussion** — all decisions stayed inside the phase boundary defined by ROADMAP.md (with the agreed-upon boundary expansion to include CLI-01 + CLI-02 in Phase 1, which is a phase-mapping refinement, not new capability).
