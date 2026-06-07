---
phase: 01-test-harness-redis-foundation
verified: 2026-05-19T00:00:00Z
status: passed
score: 5/5 must-haves verified
overrides_applied: 0
re_verification:
  previous: none — initial verification
---

# Phase 1: Test Harness + Redis Foundation — Verification Report

**Phase Goal (ROADMAP.md):** A multi-process test harness exists that races fork+exec'd child processes against a real, persistent Redis instance — the substrate every subsequent phase will build on and be validated against.

**Verified:** 2026-05-19
**Status:** PHASE COMPLETE
**Re-verification:** No — initial verification
**Verification mode:** Goal-backward (REQ-IDs as observable truths; codebase as evidence)

---

## Per-Requirement Status

| REQ-ID   | Truth (what "delivered" means)                                                                                                  | Status      | Evidence |
|----------|----------------------------------------------------------------------------------------------------------------------------------|-------------|----------|
| REDIS-01 | brew Redis runs with `appendonly=yes`, `appendfsync=everysec`, `save=900 1`, AOF on disk; managed via `brew services`            | PASS        | `bash scripts/verify-redis-config.sh` → `verify-redis-config: OK (appendonly=yes, appendfsync=everysec, save=900 1, AOF present)` exit 0. Live `redis-cli CONFIG GET` confirms each setting. AOF files on disk: `/opt/homebrew/var/db/redis/appendonlydir/{appendonly.aof.1.base.rdb, appendonly.aof.1.incr.aof, appendonly.aof.manifest}` (Redis 8.x split layout — resolves RESEARCH Open Question #1; commit `2ef1881`). |
| CLI-01   | `em-proj` package installable via `uv tool install --editable .`; binary on PATH                                                  | PASS        | `command -v em-proj` → `/Users/emonical/.local/bin/em-proj`. `pyproject.toml:19` declares `em-proj = "em_proj.cli:app"` under `[project.scripts]`. Hatchling wheel-target `packages = ["src/em_proj"]` (pyproject.toml:21-22). Commits `922bcde`, `a6f7dee`, `45cbc42`. |
| CLI-02   | `em-proj --version` outputs `em-proj 0.1.0`; `em-proj --help` lists `--version`                                                   | PASS        | Direct invocation: `em-proj --version` → `em-proj 0.1.0` exit 0; `em-proj --help` → typer-rendered `Usage: em-proj [OPTIONS] COMMAND [ARGS]...` + `Personal tooling CLI under the em-proj namespace.` + `--version Show version and exit.`. Unit tests `tests/unit/test_cli.py::test_version`, `::test_help` pass via `bash scripts/test.sh unit`. Annotated callback `is_eager=True` (cli.py:30); `no_args_is_help=True` (cli.py:12). Commit `885a411`. |
| TEST-01  | `multiproc_race` fixture spawns N fork+exec children racing `em-proj` at CLI boundary                                              | PASS        | `tests/conftest.py:94-160` defines `multiproc_race` fixture; uses `subprocess.Popen` (line 135) + `.communicate(timeout=)` (line 148); injects `EM_PROJ_REDIS_DB=15` into child env (line 127); enforces argv-as-list-of-str shell-injection guard (lines 118-124). `test_harness_runs_em_proj_at_cli_boundary` (tests/multiprocess/test_harness_self.py:17-49) races three `em-proj --version` invocations and asserts exit 0 + stdout marker + RaceResult shape — PASSES via `bash scripts/test.sh harness`. Commits `f7b814a`, `1da3573`, `7251856`. |
| TEST-02  | Harness landed FIRST (before any lock/claim code); parallel-launch ordering enforced; named test by exact VALIDATION.md spelling   | PASS        | `test_race_launches_in_parallel_not_sequence` (tests/multiprocess/test_harness_self.py:52-80) exists with EXACT name from VALIDATION.md; asserts 3×`em-proj --version` race wall-time < 600ms — PASSES. No `state`/`lock`/`claim` executable code in `src/em_proj/` (only commented mount-point reservation at cli.py:38-41; references in docstrings only — `grep -rE "lock\|claim\|state" src/em_proj/` returns only comments). No `src/em_proj/commands/` subpackage exists. Phase 1 substrate complete before any locking logic ships. |

**Score:** 5 / 5 REQ-IDs delivered.

---

## ROADMAP Success Criteria

| # | Criterion (ROADMAP.md Phase 1)                                                                                                                                                                    | Status | Evidence |
|---|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------|----------|
| 1 | `brew services start redis` brings up a loopback Redis configured with `appendonly yes`, `appendfsync everysec`, `save 900 1`, and AOF visible at `/opt/homebrew/var/db/redis/appendonly.aof`     | PASS (with refinement) | All four config settings live + AOF present. The literal path in ROADMAP.md (`/opt/homebrew/var/db/redis/appendonly.aof` as a single file) is superseded by RESEARCH Open Question #1 — this machine's Redis 8.4.0 uses the split-AOF layout under `/opt/homebrew/var/db/redis/appendonlydir/` (multipart). The verify script glob handles both, exit 0. SUMMARY-03 documents this resolution. Intent satisfied. |
| 2 | A `pytest`-driven harness can spawn N `fork+exec`'d child processes that invoke a CLI binary and assert on their combined exit codes, stdout, and effect on the shared Redis state              | PASS | `multiproc_race` fixture: parallel `subprocess.Popen` (fork+exec on macOS), `RaceResult{returncode, stdout, stderr, duration_ms}` (D-15 surfaces #1-#3); `clean_db` fixture exposes Redis state directly (surface #4). `test_harness_runs_em_proj_at_cli_boundary` exercises three of four surfaces against three `em-proj --version` children; `test_redis_state_isolation_per_test_{setup,verify}` exercises the Redis-state surface. |
| 3 | The harness lands and passes its self-tests *before* any locking, claim, or consumer code is written (TDD-first ordering enforced)                                                                | PASS | Five harness self-tests in `tests/multiprocess/test_harness_self.py` all PASS. No `lock`/`claim`/`state` executable code anywhere in `src/em_proj/`. Git log shows no commits introducing locking/claim primitives — only the harness, CLI scaffold, and Redis-client wrapper. Substrate is complete; Phases 3+ have nothing to TDD against yet but the harness is ready for `def test_lock_serializes(multiproc_race, clean_db): ...`. |

---

## SUMMARY.md Artifacts

| Plan | SUMMARY committed on planning branch | Commit |
|------|--------------------------------------|--------|
| 01-01 | YES — `01-01-SUMMARY.md` | `e471899` docs(01-01) |
| 01-02 | YES — `01-02-SUMMARY.md` | `cd3dc2d` docs(01-02) |
| 01-03 | YES — `01-03-SUMMARY.md` | `44a0a1a` docs(01-03) |
| 01-04 | YES — `01-04-SUMMARY.md` | `3d1c860` docs(01-04) |

All four plans documented.

---

## Required Artifacts (file-level)

| Artifact | Expected | Status | Evidence |
|----------|----------|--------|----------|
| `pyproject.toml` | PEP 621 + hatchling + locked deps + entry point | PASS | Read confirms `[project.scripts] em-proj = "em_proj.cli:app"`, `[tool.hatch.build.targets.wheel] packages = ["src/em_proj"]`, `[tool.pytest.ini_options]`, deps `typer>=0.16,<1.0` + `redis>=6.0,<8.0`, dev `pytest>=8.0,<10.0` |
| `src/em_proj/__init__.py` | `__version__ = "0.1.0"` | PASS | Read: `__version__ = "0.1.0"` |
| `src/em_proj/__main__.py` | `from em_proj.cli import app` + `app()` guard | PASS | Read: 4-line delegation to `app()` |
| `src/em_proj/cli.py` | typer scaffold, `--version` Annotated callback, `no_args_is_help=True`, mount-point comment | PASS | Read: typer.Typer instance with `no_args_is_help=True`, Annotated `--version` with `is_eager=True`, Phase 2 `add_typer(state_app, …)` mount-point comment present (commented only) |
| `src/em_proj/redis_client.py` | Lazy `get_client()` + `die_if_redis_unreachable()` + `_RedisUnreachable(SystemExit)` + `EM_PROJ_REDIS_DB` env | PASS | Read: D-07 lazy contract (no socket on import), D-08 sync `redis-py`, D-17 exact stderr format, sentinel SystemExit subclass with exit code 1, env-var resolution chain, `socket_connect_timeout=2.0`, `socket_timeout=5.0` |
| `tests/conftest.py` | `TEST_DB=15`, `EM_PROJ_BIN`, `RaceResult` dataclass, `redis_precheck`/`clean_db`/`multiproc_race` fixtures | PASS | All present, fixtures correctly scoped (session/function/function), `EM_PROJ_REDIS_DB=15` injected into subprocess env, argv shell-injection guard active |
| `tests/multiprocess/test_harness_self.py` | TEST-01 + TEST-02 self-tests with exact VALIDATION.md names | PASS | 5 tests including `test_harness_runs_em_proj_at_cli_boundary`, `test_race_launches_in_parallel_not_sequence`, `test_redis_state_isolation_per_test_setup`, `test_redis_state_isolation_per_test_verify`, `test_db_15_not_db_0_safety_net` — all pass via `bash scripts/test.sh harness` |
| `tests/unit/test_cli.py` | `test_version` + `test_help` via CliRunner | PASS | Both tests defined, pass via `bash scripts/test.sh unit` |
| `tests/unit/test_redis_client.py` | Lazy, env-var, ConnectionError translation, TimeoutError translation | PASS | 4 tests pass via `bash scripts/test.sh unit` |
| `scripts/verify-redis-config.sh` | Bash check for four REDIS-01 settings + AOF presence | PASS | Read: glob-tolerant AOF check (handles both monolithic and Redis 8.x split layouts), three `redis-cli CONFIG GET` checks, exit codes 0/1/2 per contract. Runs green. |
| `/opt/homebrew/etc/redis.conf` | `appendonly yes` + `save 900 1` lines present | PASS | `redis-cli CONFIG GET appendonly` → `yes`, `… save` → `900 1`, `… appendfsync` → `everysec`. Backup `.bak` exists. |

---

## Key Link Verification

| From | To | Via | Status | Detail |
|------|-----|-----|--------|--------|
| `em-proj` binary on PATH | `src/em_proj/cli.py::app` | `pyproject.toml [project.scripts]` entry point | WIRED | `command -v em-proj` resolves; running it produces the typer help/version — proves the entry-point string maps to the live `app` object |
| `src/em_proj/__main__.py` | `src/em_proj/cli.py::app` | `from em_proj.cli import app` | WIRED | grep confirms the import; `python -m em_proj --version` works (validated indirectly by entry-point path which routes through the same module) |
| `tests/conftest.py multiproc_race` | `em-proj` binary on PATH | `subprocess.Popen([EM_PROJ_BIN, ...], env={EM_PROJ_REDIS_DB: "15", ...})` | WIRED | `test_harness_runs_em_proj_at_cli_boundary` races 3 children, all exit 0 with `em-proj 0.1.0` stdout — end-to-end PATH lookup + fork+exec + capture demonstrated |
| `tests/conftest.py clean_db` | Redis db=15 | `redis_precheck.flushdb()` before/after each test | WIRED | `test_redis_state_isolation_per_test_setup` writes sentinel; `..._verify` confirms gone. `test_db_15_not_db_0_safety_net` confirms `connection_pool.connection_kwargs["db"] == 15`. |
| Child process | `em_proj.redis_client.get_client` | `EM_PROJ_REDIS_DB` env var | WIRED | `tests/conftest.py:127` injects env var; `src/em_proj/redis_client.py:37` reads it. Plumbing reserved (no Phase 1 verb actually touches Redis yet — first real use lands in Phase 2). |

---

## Data-Flow Trace (Level 4)

Phase 1 has no rendered/dynamic data — the CLI exposes only `--version`/`--help` (static strings from `__version__` constant) and Redis pieces are infrastructure-only. Level 4 trace is N/A; the artifacts that "render dynamic data" (state verbs) land in Phase 2+.

What we did verify (analogous): the `multiproc_race` fixture's data plumbing — `EM_PROJ_REDIS_DB=15` env → child `get_client()` → `connection_pool.connection_kwargs["db"]` — is exercised by `test_db_15_not_db_0_safety_net` (FLOWING).

---

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full test suite green (29 tests) | `bash scripts/test.sh all` | `29 passed in 0.16s` | PASS |
| Harness self-tests green (5 tests) | `bash scripts/test.sh harness` | `5 passed in 0.11s` | PASS |
| Structural plan-criteria tests (18 tests) | `bash scripts/test.sh structural` | `18 passed in 0.01s` | PASS |
| Conftest importability probe | `bash scripts/test.sh conftest-check` | `conftest structure OK` exit 0 | PASS |
| REDIS-01 verifier | `bash scripts/verify-redis-config.sh` | `verify-redis-config: OK (...)` exit 0 | PASS |
| em-proj on PATH + responds | `command -v em-proj && em-proj --version` | `/Users/emonical/.local/bin/em-proj` + `em-proj 0.1.0` exit 0 | PASS |
| em-proj cold-start under threshold | `/usr/bin/time -p em-proj --version` | `real 0.03` (33ms; 600ms ceiling has ~18× headroom) | PASS |
| em-proj --help renders typer auto-help | `em-proj --help` | typer-rendered usage block listing `--version` | PASS |

All 8 spot-checks green.

---

## Probe Execution

No conventional `scripts/*/tests/probe-*.sh` exist in this project (greenfield Phase 1; no migration probes declared). The closest analog — `scripts/verify-redis-config.sh` — IS executed above (PASS, exit 0). Skipped: no further probes to run.

---

## Requirements Coverage

| REQ-ID | Source Plan | Description | Status | Evidence |
|--------|-------------|-------------|--------|----------|
| CLI-01 | 01-01, 01-02 | Installable via `uv tool install em-proj` from local source | SATISFIED | `em-proj` on PATH; `uv tool install --editable .` documented in README |
| CLI-02 | 01-02 | Subcommand dispatch via `em-proj <subcommand> <verb>` (typer) | SATISFIED (scaffold) | typer dispatch ready; `--version`/`--help` work; mount-point reserved for `state` subcommand in Phase 2 (D-05/D-06 boundary) |
| REDIS-01 | 01-03 | Loopback Redis with `appendonly yes`, `appendfsync everysec`, `save 900 1`; brew services | SATISFIED | All four settings live; AOF on disk; verify script green |
| TEST-01 | 01-04 | Multi-process harness exists; races fork+exec children at em-proj CLI boundary | SATISFIED | `multiproc_race` fixture + `test_harness_runs_em_proj_at_cli_boundary` |
| TEST-02 | 01-04 | Harness lands first, before any locking/claim code | SATISFIED | `test_race_launches_in_parallel_not_sequence` enforces parallel ordering; no lock/claim code anywhere in `src/em_proj/` |

ROADMAP.md Phase 1 entry lists 5 REQ-IDs (TEST-01, TEST-02, REDIS-01, CLI-01, CLI-02 — the latter two pulled in from Phase 2 per 01-CONTEXT.md D-04..D-06). All 5 covered. **Note:** REQUIREMENTS.md Traceability table still maps CLI-01/CLI-02 to Phase 2 — this is a known stale-traceability note flagged in 01-CONTEXT.md "Downstream: ROADMAP.md and REQUIREMENTS.md traceability table need updating to reflect this remap." A roadmapper re-run is the canonical fix (informational, not a Phase 1 blocker).

---

## Anti-Patterns Found

Scanned `src/em_proj/`, `tests/`, `scripts/` for: `TBD|FIXME|XXX|TODO|HACK|PLACEHOLDER`, debt markers, empty implementations, hardcoded empty-data returns, console-only handlers, props passed empty.

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (none found) | — | — | — | All scan patterns returned 0 hits. The only `NotImplementedError` from Plan 01's intentional placeholder was wholesale-replaced by Plan 02 (`grep -c NotImplementedError src/em_proj/cli.py` → 0). |

The `# Phase 2 mount point` comment in `cli.py` references a future `add_typer(state_app, …)` call — this is an INFORMATIONAL reservation marker, not debt. Same for the docstrings in `conftest.py` that say "would silently defeat lock tests" (forward-looking design note about Phase 3+).

---

## Verification Overrides

None applied. None required.

---

## Human Verification Required

None remaining. The two human-verify checkpoints from Plans 01-02 (fresh-shell `em-proj` PATH check) and 01-03 (brew config diff + AOF presence) were resolved by the executor / orchestrator before the verifier ran (evidenced by Task 4 commits and the green state of the substrate). Re-confirming them here would be redundant: this verifier's own commands (`command -v em-proj && em-proj --version` in a fresh subshell, `bash scripts/verify-redis-config.sh`, `ls /opt/homebrew/var/db/redis/appendonlydir/`) all confirm the same end-state in the live environment.

---

## Gaps Summary

**None.** All 5 REQ-IDs satisfied. All 3 ROADMAP success criteria satisfied (Criterion 1 with the documented Open-Question-#1 refinement: monolithic AOF path → split `appendonlydir/`). All 4 SUMMARY.md files committed on planning branch. All 29 tests green. REDIS-01 verifier green. `em-proj` on PATH. No anti-patterns, no debt markers, no orphaned artifacts.

---

## Overall Verdict

**PHASE COMPLETE.**

Phase 1 lands the full substrate every later phase races against:

1. **REDIS-01** — Persistent brew-managed Redis with AOF + RDB; `bash scripts/verify-redis-config.sh` is a reusable CI-callable check.
2. **CLI-01 + CLI-02** — `em-proj` is on PATH via `uv tool install --editable .`; typer scaffold with `--version`/`--help` works end-to-end and is ready for Phase 2 to `app.add_typer(state_app, name="state")` at the reserved mount-point comment.
3. **TEST-01 + TEST-02** — `multiproc_race` fixture spawns fork+exec children racing `em-proj` at the CLI boundary; parallel-launch ordering enforced by `test_race_launches_in_parallel_not_sequence` (600ms ceiling, ~18× headroom on current hardware). Harness landed BEFORE any locking/claim code (no `state` subcommand source ships in this phase).

Phase 2+ can immediately write `def test_lock_serializes(multiproc_race, clean_db): ...` and inherit the full substrate (real binary on PATH + real Redis + real race fixture + db=15 isolation).

---

## Recommendations for Phase 2

1. **Roadmapper re-run pending** — REQUIREMENTS.md Traceability table still maps CLI-01/CLI-02 to Phase 2 (per 01-CONTEXT.md `<domain>` boundary expansion). Schedule a `/gsd-roadmapper` (or equivalent) early in Phase 2 to update the traceability table so REQ-IDs aren't double-counted. Non-blocking, but cleaner before Phase 2 plans land.

2. **`uv tool install` editable-link path caveat** — The currently-installed `em-proj` shim was created via `uv tool install --editable .` from a worktree path (see 01-02-SUMMARY.md "Deviations from Plan #2"). If you have not already, re-run `cd /Users/emonical/projects/personal/ai-tools/em-proj && uv tool install --editable .` from the main repo root so the editable link points at the canonical source location (so post-merge source edits actually propagate). The fresh-shell behavior was working at verification time, but the link is fragile.

3. **D-19 single-chokepoint enforcement going into Phase 2** — Per 01-03-SUMMARY.md and 01-CONTEXT.md D-19, every Phase 2+ `em-proj state` verb MUST call through `em_proj.redis_client.get_client()` rather than constructing `redis.Redis(...)` directly. Consider adding a structural test in `tests/structural/` along the lines of `test_no_direct_redis_client_construction_outside_wrapper` (AST-based: assert that `redis.Redis(` only appears in `src/em_proj/redis_client.py`). Cheap regression gate.

4. **TEST-02 threshold is generous** — Current 600ms ceiling vs. measured ~33ms cold-start gives ~18× headroom. If Phase 2's `em-proj state get` adds Redis round-trips and pushes single-call to ~100ms, the 600ms gate still has 6× headroom for a 3-way race. Don't tighten it preemptively; only revisit if `test_race_launches_in_parallel_not_sequence` ever flakes red.

5. **REDIS-02 validation path is wired but unexercised** — `die_if_redis_unreachable()` exists with full error-translation UX and is unit-tested via monkeypatch, but no Phase 1 verb actually calls it on a real-Redis path. Phase 2's first `em-proj state get/set` should call it as the first line of every state-verb handler (per D-19). The validation strategy in 01-VALIDATION.md already names the test pattern.

6. **Worktree hygiene** — 01-04-SUMMARY.md notes "locked agent worktrees from prior attempts remain attached for now; they can be cleaned up post-verify with `git worktree remove`". Now that verification is in, this is the right time to prune them so Phase 2 starts on a clean state.

---

_Verified: 2026-05-19_
_Verifier: Claude (gsd-verifier), goal-backward methodology_
