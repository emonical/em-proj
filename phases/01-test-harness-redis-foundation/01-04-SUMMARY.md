---
phase: 01-test-harness-redis-foundation
plan: 04
wave: 3
status: complete
requirements_satisfied: [TEST-01, TEST-02]
decisions_satisfied: [D-06, D-11, D-13, D-14, D-15, D-16]
commits:
  - f7b814a feat(01-04): add multiproc_race pytest harness fixtures (TEST-01 substrate)
  - 1da3573 test(01-04): add harness self-tests (TEST-01 + TEST-02)
  - 7251856 fix(01-04): import conftest via tests.conftest, not bare module name
---

# Plan 01-04 SUMMARY — Multi-Process Harness + Self-Tests

## What landed

Three commits on main bring the Phase 1 substrate to completion:

1. **`f7b814a`** — `tests/conftest.py` (160 lines): `RaceResult` dataclass, three fixtures (`redis_precheck` session-scoped, `clean_db` and `multiproc_race` function-scoped), `EM_PROJ_REDIS_DB=15` env injection.
2. **`1da3573`** — `tests/multiprocess/__init__.py` (empty marker) + `tests/multiprocess/test_harness_self.py` (5 tests).
3. **`7251856`** — `from tests.conftest import ...` fix (the recovered worktree code used `from conftest import` which fails under pytest's package mode).

## conftest.py public API (reused verbatim by Phase 2+)

```python
TEST_DB: int = 15
EM_PROJ_BIN: str = "em-proj"               # resolved via shutil.which
DEFAULT_RACE_TIMEOUT: float = 10.0

@dataclass(frozen=True)
class RaceResult:
    returncode: int
    stdout: str
    stderr: str
    duration_ms: float

@pytest.fixture(scope="session")
def redis_precheck() -> redis.Redis: ...
    # pytest.skip session if Redis down OR em-proj missing from PATH

@pytest.fixture            # function-scoped (D-11, D-16)
def clean_db(redis_precheck: redis.Redis) -> redis.Redis: ...
    # FLUSHDB on db=15 before AND after each test

@pytest.fixture            # function-scoped (D-13, D-14)
def multiproc_race(clean_db: redis.Redis):
    def _run(commands: list[list[str]], timeout: float = 10.0) -> list[RaceResult]: ...
    return _run
```

Tests in Phase 3+ that need parallel-launch racing import these via
`from tests.conftest import EM_PROJ_BIN, RaceResult, TEST_DB` and depend on
`multiproc_race` + `clean_db` as fixtures (the latter auto-FLUSHDBs db=15).

## Empirical timings (resolves RESEARCH Open Question #2)

Measured on this development hardware (macOS, M-series, 2026-05-19):

| Measurement                                          | Wall time |
|------------------------------------------------------|-----------|
| Single-call cold start: `/usr/bin/time -p em-proj --version` (avg of 5) | ~33ms     |
| 3-way race subset run: `bash scripts/test.sh harness -k test_race`      | 180-350ms (includes pytest startup) |
| Actual `multiproc_race(3 × --version)` `elapsed_ms`                     | well under 100ms per test pass |

**TEST-02 threshold (600ms ceiling) needs NO tuning** — current single-call
cold start is ~33ms and 2× would be ~66ms; the 600ms ceiling has ~9× headroom.
If a future hardware/dependency change causes single-call to creep past ~250ms,
revisit the threshold to (single-call × 2). Until then, the 600ms gate is loose
enough to absorb noise without being so loose it stops catching sequential-loop
regressions.

## Test inventory (all green via `bash scripts/test.sh all`)

29 tests total across 4 test trees:

| Tree                                | Tests | Purpose                                                |
|-------------------------------------|-------|--------------------------------------------------------|
| `tests/multiprocess/test_harness_self.py` | 5 | Harness substrate (TEST-01 + TEST-02 + isolation + db-15 paranoia) |
| `tests/structural/test_conftest_shape.py` | 18 | AST-based encoding of plan acceptance criteria         |
| `tests/unit/test_cli.py`            | 2     | typer dispatch + --version (CLI-02)                    |
| `tests/unit/test_redis_client.py`   | 4     | Lazy Redis client + error UX (Plan 03)                 |

## DO NOT REFACTOR notes (for future contributors)

- **`subprocess.Popen` is locked.** Do NOT swap for `multiprocessing.Process` —
  RESEARCH Pitfall #6 + threat T-01-04-05. macOS will crash intermittently
  with `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` errors under fork+spawn. The
  module docstring + structural test
  (`test_does_not_import_multiprocessing`) enforce this.
- **`.communicate(timeout=)` is locked.** Do NOT swap for `.wait()` —
  RESEARCH Pitfall #2. Bare `.wait()` deadlocks when child stdout/stderr
  fills the 64KB pipe buffer; `.communicate()` drains the pipes while waiting.
  Structural test `test_no_bare_wait_calls` enforces.
- **Tight launch loop in `multiproc_race`.** D-14: every `Popen()` returns
  immediately (fork+exec); ALL N children are running before any
  `.communicate()` call. Sequential launch would silently defeat every
  Phase 3+ lock test. TEST-02 (`test_race_launches_in_parallel_not_sequence`)
  is the regression gate.
- **`EM_PROJ_REDIS_DB=15` env injection is critical.** Pitfall #4. Without
  it, children connect to db=0 (prod default) and `FLUSHDB` from `clean_db`
  wipes the developer's real Redis state. Both `test_db_15_not_db_0_safety_net`
  (runtime) and `test_injects_em_proj_redis_db_env` (structural) gate this.

## Verification discipline

All test verification went through `bash scripts/test.sh <subcommand>` per
the project's dispatcher convention (project `CLAUDE.md`). Source-text
acceptance criteria from the plan that would have required individual
`grep` / `wc -l` / `test -s` Bash invocations are now encoded as AST tests
in `tests/structural/test_conftest_shape.py` — one allowlisted dispatcher
call (`bash scripts/test.sh all`) covers everything.

## Recovery note

Plan 04 was executed by a prior subagent run that was interrupted; the
executor had committed Task 1 (`feat(01-04): add multiproc_race pytest
harness fixtures`) on its worktree branch but left Task 2 untracked in the
worktree before the session paused. Recovery:

1. Deleted the stale untracked `tests/conftest.py` on main (matched the
   worktree's committed version cosmetically).
2. Cherry-picked the worktree's `0c95833` onto main as `f7b814a`.
3. Copied the worktree's `tests/multiprocess/{__init__.py,test_harness_self.py}`
   onto main and committed as `1da3573`.
4. Fixed the `from conftest import` → `from tests.conftest import` issue
   that surfaced once the package was discovered under pytest's package
   mode (`tests/__init__.py` is present, which disables the rootdir-as-syspath
   shortcut the plan's note assumed).

No fresh executor spawn was needed. The locked agent worktrees from prior
attempts remain attached for now; they can be cleaned up post-verify with
`git worktree remove` once Phase 1 verification is in.

## Phase 1 substrate completion checklist

- [x] **REDIS-01** — Persistent brew-managed Redis with AOF + RDB; `scripts/verify-redis-config.sh` green
- [x] **CLI-01** — `em-proj` typer scaffold (`em-proj/cli.py`, `em-proj/__main__.py`)
- [x] **CLI-02** — `em-proj --version` + `--help` via typer Annotated callback (eager); installed via `uv tool install --editable .`
- [x] **TEST-01** — Multi-process harness exists; races fork+exec children at the CLI boundary
- [x] **TEST-02** — Harness landed FIRST (before any lock/claim code); `test_race_launches_in_parallel_not_sequence` enforces parallel-launch ordering

Phase 1 is complete. Phase 2+ can build `em-proj state` subcommands and TDD them against this substrate.
