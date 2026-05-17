# Phase 1: Test Harness + Redis Foundation - Context

**Gathered:** 2026-05-17
**Status:** Ready for planning

<domain>
## Phase Boundary

Land the substrate every subsequent phase races against: a multi-process pytest harness that spawns `fork+exec`'d child processes invoking a real installed `em-proj` binary against a persistent Redis backend, with the underlying shared Redis-client wrapper that gives clean errors when Redis is unreachable.

**Phase 1 ships an installable `em-proj` with the typer dispatch scaffold but no `state` subcommands** — just enough surface for the harness to race a real binary at the CLI boundary. The KV/lock/claim verbs land in later phases.

**Phase 1 boundary expanded during discussion (vs. ROADMAP.md):**

The roadmap initially mapped CLI-01 and CLI-02 to Phase 2 with a "stub binary acceptable" success criterion. During discussion, we pulled them into Phase 1 so the harness races a real installed binary from the start (cleaner harness, no later refactor when the stub gets replaced). REQ-ID remap:

- **Now in Phase 1:** TEST-01, TEST-02, REDIS-01, **CLI-01, CLI-02** (5 REQ-IDs)
- **Phase 2 unchanged otherwise:** CLI-03, CLI-04, CLI-05, KV-01, KV-02, REDIS-02 (the REDIS-02 *machinery* — the Redis-client wrapper with clean error messages — also lands in Phase 1 to support the harness precheck; the user-facing REQ-ID validates fully in Phase 2 once more verbs exist to exercise it)

Downstream: ROADMAP.md and REQUIREMENTS.md traceability table need updating to reflect this remap. The planner should be told to spawn a roadmapper re-run after Phase 1 plans, or to update inline.

</domain>

<decisions>
## Implementation Decisions

### Project Skeleton
- **D-01:** Package layout = `src/em_proj/` (PyPA src/-layout standard — prevents accidental imports of pre-install code; plays nicely with `uv tool install --editable`)
- **D-02:** CLI entrypoint = `em_proj/__main__.py` + `em_proj/cli.py`. `__main__.py` enables `python -m em_proj` for harness debugging without needing `em-proj` on PATH; `cli.py` holds the typer `app`. `pyproject.toml [project.scripts]` exposes `em-proj = "em_proj.cli:app"` so `uv tool install` provides the binary on PATH.
- **D-03:** Test layout = `tests/unit/` (in-process unit tests) + `tests/multiprocess/` (harness-driven integration tests). pytest config (`pyproject.toml [tool.pytest.ini_options]`) discovers both.

### Phase 1 Deliverable Boundary
- **D-04:** Phase 1 ships an installable `em-proj` binary via `uv tool install em-proj` from local source (CLI-01 satisfied).
- **D-05:** Typer dispatch scaffold lands in Phase 1 — typer `app` is initialized, `em-proj --version` returns 0 with a version string, `em-proj --help` renders the typer auto-help (CLI-02 satisfied; CLI-03 "--help for every subcommand and verb" defers to Phase 2 since no subcommands exist yet).
- **D-06:** No `state` subcommands in Phase 1. The harness races against `em-proj --version` (or a similar trivial verb) as the canonical "real binary" — enough to exercise install, dispatch, exit codes, and fork+exec mechanics; KV/lock/claim verbs land in later phases.

### Redis Client Setup
- **D-07:** Connection model = lazy module-level `Redis()` per process. Each `em-proj` CLI invocation is its own short-lived Python process; on first call, the module lazy-inits one `redis.Redis()` instance and reuses it for that invocation's 1-3 round-trips, then exits naturally. No long-lived shared client across invocations.
- **D-08:** Client = synchronous `redis-py` with default connection pool (size 10). Not `redis.asyncio` — every command would `asyncio.run(...)` for no benefit; all M1 operations are stateless from a connection standpoint (single command or single Lua script per operation), so pool size and async machinery are nearly irrelevant.
- **D-09:** **Cross-session safety is a Redis-server concern, not a client concern.** Two sessions racing `lock` or `claim` are serialized by Redis's single-threaded command processor. Atomic primitives (`SET NX EX`) and Lua scripts (`EVAL` for claim check-then-take/refresh) provide the guarantees — not anything at the Python client layer. Avoid `WATCH`/`MULTI`/`EXEC` in M1; use Lua instead for atomicity (no connection-state coupling).

### Test Redis Lifecycle
- **D-10:** Tests connect to the same brew-managed loopback Redis as production (identical version + config — what passes in tests will pass in prod), but namespaced to **logical DB 15** (Redis ships with 16 numbered DBs; 0 is prod default, 15 is tests).
- **D-11:** Each test starts with `FLUSHDB` on db=15 to ensure isolation. The pytest fixture handles this automatically.
- **D-12:** No ephemeral redis-server process for tests in M1. Reconsider if test-vs-prod config drift becomes a concrete problem (it shouldn't — same instance, same AOF settings).

### Test Harness API
- **D-13:** Harness shape = pytest fixture `multiproc_race(commands: list[list[str]]) -> list[Result]`. Returns one `Result(returncode, stdout, stderr, duration_ms)` per command, in launch order.
- **D-14:** Spawn semantics — fixture launches all N `subprocess.Popen` instances in a tight loop (parallel start, not sequential), then joins all. This is critical: sequential `subprocess.run` would defeat the race. Race-correctness is itself a harness self-test (TEST-02 ordering).
- **D-15:** Three assertion surfaces, all first-class:
  1. **Exit codes** — `assert sorted(r.returncode for r in results) == [0, 3]` (one wins, one rejected)
  2. **Stdout markers** — each child can be instructed to print a token; assert tokens are present/absent
  3. **Post-race Redis state** — after the race, the test inspects Redis directly (e.g., `r.hgetall('claim:foo')`) to confirm ground truth matches what exit codes implied
- **D-16:** Session-scoped FLUSHDB autoclean is part of the fixture, so individual tests do not need to remember.

### Healthcheck UX
- **D-17:** Redis client wrapper (a thin module that owns the lazy `Redis()` instance) is responsible for catching `redis.ConnectionError` and re-raising / printing a one-line actionable message:
  ```
  em-proj: error: Redis unreachable at 127.0.0.1:6379 — run `brew services start redis`
  ```
  Exit code 1, message to stderr, no Python traceback. This is the foundational machinery for REDIS-02 even though REDIS-02 itself stays mapped to Phase 2.
- **D-18:** **No dedicated `em-proj health` subcommand in Phase 1.** The harness uses its own pytest session-scoped precheck fixture that does `redis.Redis(db=15).ping()` and skips the test session with a clear message if Redis is down. Adding a user-facing `em-proj health` verb would be a new capability (no current REQ-ID) and can land later if useful.
- **D-19:** The shared client wrapper is the single chokepoint for Redis errors. As `state` subcommands land in Phase 2+, they call through the wrapper and inherit clean error UX for free.

### Claude's Discretion
- The exact module structure inside `em_proj/` (e.g., whether the Redis client wrapper lives at `em_proj/redis_client.py` or `em_proj/backend/redis.py`) is implementation detail — pick what reads cleanly for the planner.
- Whether `Result` from `multiproc_race` is a `dataclass` or a `NamedTuple` — pick the more pytest-friendly option.
- pytest fixture scope (`session` vs `function`) for the Redis cleanup — pick based on harness ergonomics; `function` is safer (full isolation) but `session` is faster if tests are well-behaved.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project context (locks scope, decisions, verified facts)
- `.planning/PROJECT.md` — Core value, M1 active requirements, decisions table (especially Redis backend choice, Python/uv stack, lock-default rationale, claim model rationale, multi-process test harness as first deliverable per pitfalls research)
- `.planning/REQUIREMENTS.md` — All 24 v1 REQ-IDs; in particular TEST-01, TEST-02, REDIS-01, CLI-01, CLI-02 are Phase 1 scope after the remap captured in this CONTEXT.md
- `.planning/ROADMAP.md` — Phase 1 entry. **Note:** ROADMAP.md's Phase 1 success criteria mention "stub CLI binary acceptable" — superseded by D-04..D-06 in this CONTEXT.md (real binary via uv tool install)

### Redis (backend)
- Redis docs on persistence: `appendonly yes`, `appendfsync everysec`, `save 900 1` are referenced in PROJECT.md Constraints. AOF location: `/opt/homebrew/var/db/redis/appendonly.aof`. `brew services start redis` to bring up.
- Redis Lua scripting (`EVAL` / `SCRIPT LOAD`) — load patterns for atomic check-then-set used by claim model (Phase 4). Not required reading in Phase 1, but the client wrapper should be Lua-friendly from day one.

### Python / packaging
- `uv` docs for `uv tool install` from local source (`uv tool install --from .` or `uv tool install path/to/dir`)
- typer docs for app structure, exit codes, `--help` auto-generation
- `pyproject.toml` `[project.scripts]` for binary registration

### Verified facts (carry forward from PROJECT.md)
- `CLAUDE_CODE_SESSION_ID` env var (used in Phase 3+, not Phase 1)
- Project-hash scheme `tr '/' '-'` on abs path (used in Phase 3+, not Phase 1)
- `~/.claude/projects/<hash>/` convention (used in Phase 3+, not Phase 1)

### No external ADRs
No `.planning/adrs/`, no external spec docs referenced during discussion. All Phase 1 implementation decisions captured in `<decisions>` above.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **None.** Phase 1 is greenfield — there is no `src/em_proj/` yet, no `tests/`, no `pyproject.toml`. Everything is created in Phase 1.

### Established Patterns
- **From PROJECT.md Constraints (project-wide, apply throughout):**
  - Avoid `ls | while read` patterns — the user's environment wraps `ls` with token-saving output that mangles parsing. Use glob loops (`for f in dir/*`) only.
  - Plain text on TTY; JSON when stdout is not a TTY OR `--json` flag (Phase 1 doesn't yet exercise this but the typer scaffold should not preclude it)
  - Errors to stderr; semantic exit codes (0/1/2/3) — Phase 1 only uses 0/1 (success / Redis unreachable)
  - Communication style for downstream agents: concise, opinionated recommendations; no vendor-tradeoff matrices unless asked

### Integration Points
- **Phase 1 → Phase 2:** Shared Redis-client wrapper is the integration seam. Every Phase 2+ `state` verb calls through it. Wrapper API surface should be minimal: `get_redis() -> redis.Redis` (lazy-init) plus an error-translation decorator/contextmanager.
- **Phase 1 → all subsequent phases:** `multiproc_race` fixture is the integration seam for every test of locks, claims, and the gsd-sdk consumer. Future phases will import and reuse the fixture.

</code_context>

<specifics>
## Specific Ideas

- **PyPA src/-layout** is the specific Python project structure choice — referenced because it has known benefits (prevents accidental imports of pre-install code, plays nicely with editable installs) and is the modern default for new Python packages distributed via `uv tool install`.
- **`db=15` for tests** is a specific Redis convention — uses one of the 16 numbered logical DBs that ship by default with Redis (`databases 16` in `redis.conf`); db 0 is prod default, db 15 is the highest-numbered and a common "tests live here" pick.
- **Lua scripts for claim atomicity** — referenced as the M1 escape hatch for atomic check-then-set instead of `WATCH`/`MULTI`/`EXEC`. Lua is fully atomic on the Redis server (single-threaded execution), simpler than transactions, and has zero connection-state coupling.
- **Parallel Popen launch loop** is the specific race semantic — `[Popen(cmd, ...) for cmd in commands]` starts all processes immediately; subsequent `.wait()` calls join them. Sequential `subprocess.run(cmd1); subprocess.run(cmd2)` would NOT race and would silently defeat every locking test.

</specifics>

<deferred>
## Deferred Ideas

- **`em-proj health` subcommand** — proposed as Area 4 Option B, rejected for Phase 1 (no current REQ-ID, would be scope creep). Can land later if a need surfaces (e.g., during ops debugging). Would become HEALTH-01 in REQUIREMENTS.md.
- **Ephemeral redis-server per pytest session** — proposed as Area 2 Option B, rejected for Phase 1 in favor of brew-managed Redis + db=15. Reconsider if test/prod config drift becomes a real problem.
- **redis.asyncio (async client)** — proposed as Area 2 Option C, rejected. Would only matter if multi-connection concurrency (pub/sub, BLPOP, long-lived subscribers) lands. Revisit in M3 (inter-session messaging) where Redis pub/sub becomes first-class.
- **Standalone `tests/race.py` helper module** — proposed as Area 3 Option B/C, rejected for Phase 1 (no non-pytest consumer yet). Pull out of the fixture later if a manual REPL or external tool wants to invoke races directly.

**No scope creep raised during discussion.** All decisions stayed inside the phase boundary.

</deferred>

---

*Phase: 1-Test Harness + Redis Foundation*
*Context gathered: 2026-05-17*
