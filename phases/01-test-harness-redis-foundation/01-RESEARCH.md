# Phase 1: Test Harness + Redis Foundation - Research

**Researched:** 2026-05-18
**Domain:** Python packaging (uv), CLI scaffolding (typer), multi-process pytest harness, redis-py client lifecycle, brew-managed Redis with AOF persistence
**Confidence:** HIGH (primary recommendations verified against installed tooling + official docs)

## Summary

Phase 1 is a pure substrate phase: install `redis-py` and `typer` with the standard PyPA `src/`-layout, scaffold a typer `app` with a `--version` callback and an `app.add_typer()` mount-point ready for Phase 2's `state` sub-app, build a `multiproc_race` pytest fixture that launches all `subprocess.Popen` children in a tight loop (fork+exec — fully safe on macOS, unlike raw `multiprocessing` fork), and lock in the brew-managed Redis config (`appendonly yes` is **NOT** the brew default — it must be edited in `/opt/homebrew/etc/redis.conf`).

The only non-obvious landmines: (1) brew's `redis.conf` ships with `appendonly no` and the `save` directive entirely commented out — both must be edited for REDIS-01 to pass; (2) pytest's default `capsys` fixture only captures Python-level prints — for the harness to capture subprocess stdout reliably it must use `capfd` (file-descriptor capture) OR capture via `Popen(stdout=PIPE)` directly (the latter is what the harness does, sidestepping the issue); (3) `subprocess.Popen` does `fork+exec` natively, which is the macOS-safe pattern — `multiprocessing.Process` with the default `spawn` start method on macOS is also safe but adds Python-import overhead the harness doesn't need.

**Primary recommendation:** Use `src/em_proj/` layout with `hatchling` as the build backend (mature, widely supported by `uv tool install --editable`), typer 0.16+ with `Annotated`-style options, redis-py 6.x with a lazy `_get_client()` module function (NOT a module-level eager `Redis()` call — eager defeats `--help` performance and breaks if Redis is down at import time), and `multiproc_race(commands)` as a function-scoped pytest fixture that wraps a session-scoped Redis precheck.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| CLI dispatch (`--version`, `--help`, future subcommands) | CLI shell (`em_proj/cli.py`) | — | typer owns argument parsing, exit codes, help rendering |
| Redis client lifecycle (lazy init, error translation) | Client wrapper (`em_proj/redis_client.py`) | — | Single chokepoint per Phase 1 D-17/D-19; every future verb calls through it |
| Test orchestration (parallel child launch, joining, result capture) | Test fixture (`tests/conftest.py`) | — | pytest fixture pattern; no production code involved |
| Cross-process atomicity (later phases) | Redis server (Lua / `SET NX EX`) | — | D-09: Redis's single-threaded command processor IS the concurrency control |
| Test data isolation | Redis server (`SELECT 15` + `FLUSHDB`) | Test fixture (driving the FLUSHDB) | D-10/D-11; logical-DB partitioning vs. ephemeral server is the per-Phase-1 choice |
| Binary installation on PATH | uv tool (`uv tool install --editable .`) | pyproject.toml `[project.scripts]` | D-04; standard PyPA entry-point mechanism |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `typer` | `>=0.16,<1.0` | CLI framework | [VERIFIED: pip index] current 0.25.1; 0.16+ supports `Annotated` options (modern style); Phase 1 stays on a permissive lower-bound so Phase 2 can pin later |
| `redis` (redis-py) | `>=6.0,<8.0` | Redis client | [VERIFIED: pip index] current 7.4.0; 6.0 was the major rewrite that added type hints; pinning `<8` lets us hold against breaking changes |
| `pytest` | `>=8.0,<10.0` | Test runner | [VERIFIED: pip index] current 9.0.3; 8.x is the modern baseline (drops py3.7); 9.x already shipped and works |

### Build / Packaging
| Tool | Version | Purpose | Why |
|------|---------|---------|-----|
| `uv` | `>=0.5` | Project manager + tool installer | [VERIFIED: `uv --version` → 0.9.26 installed]; locked by PROJECT.md decision |
| `hatchling` | `>=1.18` | PEP 517 build backend | [CITED: docs.astral.sh/uv/concepts/projects/init] — uv's `uv_build` is newer/less proven; hatchling is the mature default and what most editable installs are tested against |

### Alternatives Considered
| Instead of | Could Use | Why Not For Phase 1 |
|------------|-----------|--------------------|
| `hatchling` | `uv_build` | Newer, less battle-tested with `uv tool install --editable`. Switch later if/when uv_build is the obvious default. |
| sync `redis` | `redis.asyncio` | D-08 locks this — no concurrency need at the client layer |
| pytest `capsys` | `capfd` | The harness uses `Popen(stdout=PIPE)` directly, sidestepping the question entirely |

**Installation:**
```bash
# One-time, per developer machine
brew install redis      # if not already installed
brew install uv         # if not already installed

# Project deps (added to pyproject.toml [project] dependencies + [dependency-groups] dev)
# Then:
uv sync                 # installs runtime + dev deps into .venv
uv tool install --editable .   # exposes `em-proj` binary on PATH
```

**Version verification (run 2026-05-18):**
```bash
$ uv --version            # uv 0.9.26
$ redis-server --version  # v=8.4.0
$ pip index versions typer   # current 0.25.1
$ pip index versions redis   # current 7.4.0
$ pip index versions pytest  # current 9.0.3
```

## Architecture Patterns

### System Architecture Diagram

```
                  developer shell
                        |
                        | $ em-proj --version
                        v
              +------------------+
              |   em-proj bin    |  (installed by `uv tool install --editable .`)
              |  -> em_proj.cli  |
              +--------+---------+
                       |
                       v
              +------------------+        +-----------------------+
              |  em_proj.cli     |        | em_proj.redis_client  |
              |  (typer app)     |------->|  get_client() lazy    |
              |  --version       |        |  error translation    |
              |  --help          |        +-----------+-----------+
              +------------------+                    |
                                                      v
                                          +------------------------+
                                          |  redis-py Redis(db=0)  |
                                          +-----------+------------+
                                                      |
                                                      v
                                          +--------------------------+
                                          |  brew-managed redis      |
                                          |  /opt/homebrew/etc/      |
                                          |    redis.conf            |
                                          |  AOF: appendonly.aof     |
                                          +--------------------------+
                                                      ^
                                                      | (db=15)
                                                      |
                                          +-----------+--------------+
              pytest harness              |                          |
              tests/conftest.py           |                          |
              +-------------------+       |                          |
              | precheck fixture  |  -----+ (ping db=15, skip if down)
              | (session-scoped)  |       |
              +---------+---------+       |
                        |                 |
                        v                 |
              +-------------------+       |
              | multiproc_race    |       |
              | (function-scoped) |       |
              |   FLUSHDB db=15   |  -----+
              |   Popen × N       |
              |   wait + collect  |
              +---------+---------+
                        |
                        | forks N children (each is em-proj bin)
                        v
              +-------------------+   +-------------------+
              | em-proj child 1   |...| em-proj child N   |
              | (own redis.Redis) |   | (own redis.Redis) |
              +-------------------+   +-------------------+
                        |                       |
                        +-----------+-----------+
                                    v
                              (Redis db=15)
```

### Recommended Project Structure

```
em-proj/
├── pyproject.toml                  # PEP 621 + uv config
├── README.md
├── src/
│   └── em_proj/
│       ├── __init__.py             # exposes __version__
│       ├── __main__.py             # enables `python -m em_proj` (3 lines)
│       ├── cli.py                  # typer app + --version callback
│       └── redis_client.py         # lazy Redis(), error translation
└── tests/
    ├── __init__.py                 # (empty; or omit per pytest discovery)
    ├── conftest.py                 # multiproc_race + redis_precheck fixtures
    ├── unit/
    │   ├── __init__.py
    │   ├── test_cli.py             # --version, --help return codes
    │   └── test_redis_client.py    # error translation, lazy init
    └── multiprocess/
        ├── __init__.py
        ├── test_harness_self.py    # TEST-02: harness races em-proj --version
        └── test_redis_state.py     # asserts post-race FLUSHDB isolation
```

### Pattern 1: typer app with `--version` callback and add_typer mount-point

```python
# src/em_proj/cli.py
# Source: https://typer.tiangolo.com/tutorial/options/version/
#         https://typer.tiangolo.com/tutorial/subcommands/add-typer/
from typing import Annotated
import typer

from em_proj import __version__

app = typer.Typer(
    name="em-proj",
    help="Personal tooling CLI under the em-proj namespace.",
    no_args_is_help=True,        # `em-proj` alone prints help instead of erroring
    add_completion=False,        # opt out of typer's auto-completion noise for now
)

def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"em-proj {__version__}")
        raise typer.Exit()

@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,           # process before any subcommand validation
            help="Show version and exit.",
        ),
    ] = None,
) -> None:
    """em-proj entrypoint. Subcommands live as sub-apps mounted below."""

# Phase 2+ will append:
#   from em_proj.commands.state import state_app
#   app.add_typer(state_app, name="state")

if __name__ == "__main__":  # `python -m em_proj` falls back to here too
    app()
```

```python
# src/em_proj/__main__.py
from em_proj.cli import app

if __name__ == "__main__":
    app()
```

```python
# src/em_proj/__init__.py
__version__ = "0.1.0"
```

### Pattern 2: Lazy Redis client wrapper with error translation

```python
# src/em_proj/redis_client.py
# Source: https://redis.readthedocs.io/en/stable/connections.html
import os
import sys
from typing import Optional

import redis

_client: Optional[redis.Redis] = None

def get_client(db: int = 0) -> redis.Redis:
    """Return process-singleton Redis client. Lazy — no connection until first command.

    redis.Redis(...) does NOT eagerly connect; the connection pool opens
    sockets on first command. Safe to call from --help / --version paths.
    """
    global _client
    if _client is None:
        _client = redis.Redis(
            host="127.0.0.1",
            port=6379,
            db=db,
            socket_connect_timeout=2.0,   # cap "is Redis up?" wait
            socket_timeout=5.0,           # cap stuck-command wait
            decode_responses=True,        # str in/out, not bytes
        )
    return _client

class _RedisUnreachable(SystemExit):
    """Sentinel for the run-loop; carries exit code 1."""
    def __init__(self) -> None:
        super().__init__(1)

def die_if_redis_unreachable(client: redis.Redis) -> None:
    """Call before any state command. One-line message to stderr, exit 1, no traceback."""
    try:
        client.ping()
    except (redis.ConnectionError, redis.TimeoutError):
        print(
            "em-proj: error: Redis unreachable at 127.0.0.1:6379 — "
            "run `brew services start redis`",
            file=sys.stderr,
        )
        raise _RedisUnreachable()
```

**Why lazy:** `em-proj --help` and `em-proj --version` MUST NOT touch Redis. An eager module-level `redis.Redis()` is technically free (no connection until first command per [VERIFIED: redis.readthedocs.io]) but a `client.ping()` at import time would defeat help/version. The lazy `get_client()` enforces the discipline structurally.

### Pattern 3: Parallel-launch pytest fixture (`multiproc_race`)

```python
# tests/conftest.py
# Source: https://docs.python.org/3/library/subprocess.html#popen-objects
#         https://docs.pytest.org/en/stable/how-to/fixtures.html
from __future__ import annotations
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Iterable

import pytest
import redis

TEST_DB = 15
EM_PROJ_BIN = "em-proj"  # found on PATH via uv tool install

@dataclass(frozen=True)
class RaceResult:
    returncode: int
    stdout: str
    stderr: str
    duration_ms: float

@pytest.fixture(scope="session")
def redis_precheck() -> redis.Redis:
    """Skip the whole test session if Redis isn't up. Cheap session-scoped probe."""
    client = redis.Redis(host="127.0.0.1", port=6379, db=TEST_DB,
                        socket_connect_timeout=1.0, decode_responses=True)
    try:
        client.ping()
    except (redis.ConnectionError, redis.TimeoutError):
        pytest.skip(
            "Redis not reachable at 127.0.0.1:6379 — "
            "run `brew services start redis` to enable multi-process tests",
            allow_module_level=True,
        )
    if shutil.which(EM_PROJ_BIN) is None:
        pytest.skip(
            f"`{EM_PROJ_BIN}` not on PATH — "
            "run `uv tool install --editable .` from repo root",
            allow_module_level=True,
        )
    return client

@pytest.fixture
def clean_db(redis_precheck: redis.Redis) -> redis.Redis:
    """Per-test FLUSHDB on db=15. Function-scoped: full isolation, slower but safe."""
    redis_precheck.flushdb()
    yield redis_precheck
    redis_precheck.flushdb()  # paranoia cleanup; helps when a test mid-fails

@pytest.fixture
def multiproc_race(clean_db: redis.Redis):
    """Spawn N subprocess.Popen children in parallel, join all, return results in launch order.

    Usage:
        def test_two_em_projs_race(multiproc_race):
            results = multiproc_race([
                [EM_PROJ_BIN, "--version"],
                [EM_PROJ_BIN, "--version"],
            ])
            assert all(r.returncode == 0 for r in results)
    """
    def _run(commands: list[list[str]], timeout: float = 10.0) -> list[RaceResult]:
        assert isinstance(commands, list) and all(isinstance(c, list) for c in commands), \
            "multiproc_race: pass a list of argv-lists"

        # Phase 1: tight launch loop. NO awaiting between spawns — this is the race.
        starts: list[float] = []
        procs: list[subprocess.Popen] = []
        for cmd in commands:
            starts.append(time.perf_counter())
            procs.append(subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,            # decode using locale; pairs with decode_responses
            ))

        # Phase 2: join all. communicate() drains pipes (prevents pipe-buffer deadlock
        # if child stdout > 64KB) AND waits — this is the only correct pattern.
        results: list[RaceResult] = []
        for start, proc in zip(starts, procs):
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                raise AssertionError(
                    f"child {proc.args!r} did not exit within {timeout}s; killed"
                )
            duration_ms = (time.perf_counter() - start) * 1000.0
            results.append(RaceResult(proc.returncode, stdout, stderr, duration_ms))

        return results
    return _run
```

**Key invariants in this pattern:**

1. **Launch loop is tight** — every `Popen(...)` returns immediately (kernel-level `fork+exec`). All N children are running before any `.communicate()` call.
2. **`.communicate()` not `.wait()`** — `wait()` deadlocks when the child writes more than ~64KB to a `PIPE` because the pipe buffer fills and the child blocks on `write()` waiting for the parent to read. `communicate()` reads the pipes concurrently.
3. **`timeout=` on every join** — a stuck child cannot hang the entire test suite.
4. **Function-scoped FLUSHDB** — chosen per D's discretion note: full isolation is worth the ~1ms FLUSHDB per test. Session scope would let test ordering leak.

### Anti-Patterns to Avoid

- **`subprocess.run(cmd1); subprocess.run(cmd2)`** — sequential, not concurrent. Silently defeats every locking test in Phases 3+. (Called out explicitly in CONTEXT D-14.)
- **`multiprocessing.Process` with `start_method='fork'` on macOS** — known to crash with the `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` error because the child inherits a half-initialized Objective-C runtime. `subprocess.Popen` does `fork+exec`, which is always safe because `exec` replaces the entire process image. **Stay on subprocess.Popen for the harness.**
- **Eager module-level `redis.Redis()` followed by an eager `.ping()`** — breaks `em-proj --help` when Redis is down.
- **Sharing a `redis.Redis()` instance from parent into child processes** — fortunately N/A here because `subprocess.Popen` doesn't share Python state; the child is a fresh interpreter. But worth flagging for the planner: if anyone ever rewrites the harness to use `multiprocessing.Process`, the inherited Redis socket file descriptor will corrupt cross-process. Close parent connections before forking, or use `fork+exec`.
- **Mixing `capsys` and `subprocess`** — `capsys` only catches Python-level `print()`. The harness uses `Popen(stdout=PIPE)`, sidestepping the issue. Tests that want to inspect the child's stdout read it from `RaceResult.stdout`, not from `capsys`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| CLI arg parsing, help generation, exit codes | Hand-rolled argparse wrapper | `typer` (locked by PROJECT.md) | Subcommand dispatch, `--help` for every verb, type-coerced args all free |
| Redis connection pooling, reconnect, pipelining | Custom socket wrapper | `redis-py` `Redis()` | The default pool already handles per-process reuse; auto-reconnect-on-next-command is built in |
| Parallel subprocess launching with timeout | Threaded `subprocess.run` orchestrator | `subprocess.Popen` + `communicate(timeout=)` | The Popen-then-communicate pattern is the canonical Python recipe; threads add no value here |
| Atomic check-then-set against Redis | `WATCH`/`MULTI`/`EXEC` from Python | `register_script()` + Lua (Phase 4) | Lua runs atomically inside Redis's single-threaded command loop, no client-state coupling |
| Test data isolation | A second redis-server process per test session | `SELECT 15` + `FLUSHDB` (D-10/D-11) | Same instance = same config drift surface; logical DBs are free |
| Binary installation | A custom `install.sh` | `uv tool install --editable .` | Reads `[project.scripts]` from pyproject.toml, drops a shim onto `~/.local/bin`, supports re-install on source change via `--editable` |

**Key insight:** Phase 1 is almost entirely "wire the standard pieces together correctly." The real engineering judgment is which guards to put around the test harness (timeout, communicate-not-wait, function-scoped FLUSHDB) so it can't lie to us later.

## Common Pitfalls

### Pitfall 1: brew's `redis.conf` does NOT match REDIS-01 defaults

**What goes wrong:** Phase 1 "passes" because `brew services start redis` succeeds, but the AOF file never appears at `/opt/homebrew/var/db/redis/appendonly.aof` and Phase 1's success criterion #1 is silently violated.

**Why it happens:** [VERIFIED: grepped `/opt/homebrew/etc/redis.conf` 2026-05-18 on this machine] brew ships with:
- `appendonly no` (line ~437 in current brew redis 8.4.0 conf)
- `save` directive **entirely commented out** (all three `save` lines are `#` comments)
- `appendfsync everysec` (already correct)
- `appendfilename "appendonly.aof"` (already correct)
- `dir /opt/homebrew/var/db/redis/` (already correct)

So two of the four REDIS-01 settings need explicit edits.

**How to avoid:** Plan must include an explicit task to:
1. Edit `/opt/homebrew/etc/redis.conf`: set `appendonly yes`; add `save 900 1` (uncomment the example block to that exact value, or append the line).
2. `brew services restart redis` to pick up the edits.
3. Verify with `redis-cli CONFIG GET appendonly` (expect `yes`), `redis-cli CONFIG GET save` (expect `900 1`), `redis-cli CONFIG GET appendfsync` (expect `everysec`), and `ls /opt/homebrew/var/db/redis/appendonly.aof` (must exist after first write).

**Warning signs:** Phase 1 success criterion #1 says "the AOF is visible at /opt/homebrew/var/db/redis/appendonly.aof" — that file does not exist by default and won't until both `appendonly yes` is set AND at least one write has occurred. Plan a `redis-cli SET _aof_bootstrap 1; redis-cli DEL _aof_bootstrap` after restart to force AOF creation, then assert the file exists.

### Pitfall 2: subprocess pipe-buffer deadlock with `.wait()`

**What goes wrong:** Children that write >64KB to stdout hang forever; the harness reports "timed out" but actually the child is blocked on a full pipe.

**Why it happens:** `Popen.wait()` does not drain the pipes. The child's `write(stdout)` blocks once the OS pipe buffer (default 64KB on Darwin) fills. Classic deadlock.

**How to avoid:** Always use `proc.communicate(timeout=...)` instead of `proc.wait()` when stdout/stderr are `PIPE`. `communicate` reads both pipes concurrently with the join. The fixture above already does this.

**Warning signs:** A test that adds a `print()` of large data inside the child suddenly hangs; the test that used to pass now times out at the harness boundary.

### Pitfall 3: pytest `capsys` does not capture subprocess output

**What goes wrong:** A test uses `capsys.readouterr()` expecting to see the child's `--version` output and gets an empty string.

**Why it happens:** `capsys` only catches Python-level `sys.stdout.write()` in the *test process*. Subprocess children write through OS file descriptors that `capsys` doesn't intercept.

**How to avoid:** The harness reads subprocess stdout via `Popen(stdout=PIPE)` and exposes it on `RaceResult.stdout`. Tests should never reach for `capsys` to inspect child output — read from the `RaceResult` instead.

**Warning signs:** A test does `capsys.readouterr()` after a `multiproc_race(...)` call instead of inspecting `results[i].stdout`.

### Pitfall 4: FLUSHDB on the wrong DB

**What goes wrong:** A test accidentally connects to `db=0` (production default) and `FLUSHDB` wipes the user's real state.

**Why it happens:** `redis.Redis()` defaults to `db=0`. If a test imports `em_proj.redis_client.get_client()` and forgets to pass `db=15`, the precheck fixture's safety net is bypassed.

**How to avoid:**
- `multiproc_race` fixture takes a `clean_db` fixture that *explicitly* connects to `db=15`. Tests should not reach for `get_client()` directly.
- The child em-proj processes the harness spawns can be pointed at `db=15` via an env var (e.g., `EM_PROJ_REDIS_DB`) that `get_client()` reads. Phase 1's binary doesn't need this yet (only `--version`), but the planner should reserve the env-var contract now so Phase 2 inherits it.

**Warning signs:** A test that "passes" but leaves real Redis state mutated. Add a session-scoped check: `assert redis.Redis(db=0).dbsize() unchanged` before and after the test session if you want to be paranoid.

### Pitfall 5: `uv tool install` doesn't refresh on source edits without `--editable`

**What goes wrong:** Developer edits `em_proj/cli.py`, re-runs the test, sees old behavior, gets confused.

**Why it happens:** `uv tool install .` copies the source into the tool's isolated venv. Source edits don't propagate without re-installing.

**How to avoid:** Always install with `uv tool install --editable .` for development. Document this prominently in README and in a `make` target or shell script.

**Warning signs:** Stale `em-proj --version` output after a version bump.

### Pitfall 6: macOS `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` errors

**What goes wrong:** A future contributor swaps `subprocess.Popen` for `multiprocessing.Process` "for cleanliness" and the harness starts crashing intermittently with Obj-C runtime errors.

**Why it happens:** macOS High Sierra+ kills processes that `fork()` without `exec()` if Obj-C classes are mid-initialization in another thread. `subprocess.Popen` does `fork+exec` natively (safe — `exec` replaces the process image). `multiprocessing.Process` with `fork` start-method does raw `fork` (unsafe on macOS; Python 3.8+ defaults `multiprocessing` to `spawn` on macOS to dodge this, but `fork` is still an opt-in trap).

**How to avoid:** Stay on `subprocess.Popen`. Document this in a code comment on the `multiproc_race` fixture so future maintainers don't "refactor" it.

**Warning signs:** Intermittent test failures with messages mentioning `+[__NSCFConstantString initialize]` or `OBJC_DISABLE_INITIALIZE_FORK_SAFETY`.

## Code Examples

### pyproject.toml (complete, Phase-1-ready)

```toml
# Source: https://docs.astral.sh/uv/concepts/projects/init/
#         https://hatch.pypa.io/latest/config/build/
[build-system]
requires = ["hatchling>=1.18"]
build-backend = "hatchling.build"

[project]
name = "em-proj"
version = "0.1.0"
description = "Personal tooling CLI: state primitive for multi-session coordination."
readme = "README.md"
requires-python = ">=3.12"
license = { text = "MIT" }
authors = [{ name = "Edward Monical-Vuylsteke" }]
dependencies = [
    "typer>=0.16,<1.0",
    "redis>=6.0,<8.0",
]

[project.scripts]
em-proj = "em_proj.cli:app"

[tool.hatch.build.targets.wheel]
packages = ["src/em_proj"]

[dependency-groups]
dev = [
    "pytest>=8.0,<10.0",
]

[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
addopts = ["-ra", "--strict-markers", "--strict-config"]
# pytest discovers tests/unit/ and tests/multiprocess/ via testpaths recursion
```

**Note on `em-proj = "em_proj.cli:app"`:** typer's `app` is callable (typer adds `__call__` to `typer.Typer`), so pointing the entry point at the `app` object directly works. Alternative: define a `def main(): app()` wrapper and point at `em_proj.cli:main`. Either is idiomatic; the direct-to-`app` form is shorter and is what typer's own docs use.

### Minimal harness self-test (TEST-02 sanity)

```python
# tests/multiprocess/test_harness_self.py
import time

def test_race_launches_in_parallel_not_sequence(multiproc_race):
    """If two `em-proj --version` invocations took >1s each but combined wall-time < 1.5s,
    they ran in parallel. If sequential they'd take 2x.

    --version is the canonical "real binary" verb per CONTEXT D-06.
    """
    t0 = time.perf_counter()
    results = multiproc_race([
        ["em-proj", "--version"],
        ["em-proj", "--version"],
        ["em-proj", "--version"],
    ])
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert all(r.returncode == 0 for r in results)
    assert all("em-proj" in r.stdout for r in results)
    # Three sequential `em-proj --version` calls take ~150ms × 3 = ~450ms on Python+typer cold start.
    # In parallel they should complete in roughly max(child_durations), so well under 300ms.
    # This is a soft check; tune empirically after first run on real hardware.
    assert elapsed_ms < 600, (
        f"harness took {elapsed_ms}ms — looks sequential, not parallel"
    )

def test_redis_state_isolation_per_test(clean_db):
    clean_db.set("sentinel", "phase1")
    assert clean_db.get("sentinel") == "phase1"
    # Next test will see an empty db=15 because clean_db FLUSHDBs in setup.
```

### Lua-script atomic check-then-set (for Phase 4 inheritance)

```python
# NOT phase 1 scope — included for downstream phases per CONTEXT canonical_refs.
# Source: https://redis.readthedocs.io/en/stable/lua_scripting.html
from em_proj.redis_client import get_client

# Atomic: take claim only if not already held, OR refresh TTL if same holder
_CLAIM_TAKE_OR_REFRESH = """
local key = KEYS[1]
local me  = ARGV[1]
local ttl = tonumber(ARGV[2])
local current = redis.call('HGET', key, 'session_id')
if current == false or current == me then
    redis.call('HSET', key, 'session_id', me)
    redis.call('EXPIRE', key, ttl)
    return 1
end
return 0
"""

_claim_take_or_refresh = None

def claim_take_or_refresh(area: str, session_id: str, ttl_secs: int) -> bool:
    global _claim_take_or_refresh
    if _claim_take_or_refresh is None:
        _claim_take_or_refresh = get_client().register_script(_CLAIM_TAKE_OR_REFRESH)
    return bool(_claim_take_or_refresh(keys=[f"claim:{area}"], args=[session_id, ttl_secs]))
```

`register_script` returns a callable that handles `NOSCRIPT` errors transparently (re-uploads the script and retries) — no need to manage `SCRIPT LOAD` / `EVALSHA` directly.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `setup.py` + `setup.cfg` | `pyproject.toml` (PEP 621) | 2020+ (PEP 621); fully standard by 2024 | uv only supports pyproject — no setup.py needed |
| typer with explicit `typer.Option("--flag")` positional | `Annotated[T, typer.Option(...)]` | typer 0.9+ (2023); now idiomatic | Cleaner type hints, plays better with mypy |
| `redis.Redis().pipeline()` with `WATCH`/`MULTI`/`EXEC` | Lua scripts via `register_script()` | Always available; preferred since Redis 2.6 (2012) | Atomicity without client-state coupling; D-09 codifies this for Phase 4 |
| `pip install -e .` for editable | `uv tool install --editable .` | uv 0.4+ (2024) | One command does PATH wiring and venv isolation |
| `multiprocessing.Process` with `fork` on macOS | `multiprocessing.spawn` (default since 3.8); subprocess.Popen for our case | Python 3.8 (2019) on macOS | Phase 1 sidesteps entirely by using subprocess.Popen, which has always been safe |

**Deprecated/outdated:**
- **`pip install -e . --user`** for tool installation: superseded by `uv tool install` which manages isolated venvs per tool.
- **`flock(1)`-based shell locking** (per PROJECT.md): not available on macOS by default; structurally weak anyway. Already explicitly rejected.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `hatchling` is a better Phase 1 choice than `uv_build` for `uv tool install --editable` | Standard Stack | LOW — both work; if `uv_build` is fine, planner can swap. Hatchling is widely tested. |
| A2 | Python 3.12 cold-start + typer import ≈ 150ms (used in harness wall-time assertion) | Code Examples (test_harness_self) | MEDIUM — empirical; the `< 600ms` ceiling should be tuned on first run. Mark as "tune on first run" in the plan. |
| A3 | macOS default pipe buffer is 64KB (drives the `communicate()` recommendation) | Common Pitfalls #2 | LOW — actual value varies (16KB-64KB on Darwin), but the recommendation holds at any reasonable size. |
| A4 | `appendonly.aof` filename matches the literal path in the success criterion | Common Pitfalls #1 | LOW — verified `appendfilename "appendonly.aof"` in current brew conf 2026-05-18. Recent Redis (≥7) introduced AOF base-file split (`appendonly.aof.1.base.rdb` + `.incr.aof`); we need to verify the planner's "exists" check accepts either the symlink/manifest file OR the directory presence. |
| A5 | `socket_connect_timeout=2.0` is a sane precheck cap (vs. 1.0 or 5.0) | Pattern 2 (lazy client) | LOW — arbitrary tuning; 2s is comfortable for a healthy local Redis and bounded for a dead one. |

**Action for planner:** A4 is the only assumption with a concrete check-this-on-real-hardware impact. The plan should include a task that runs `redis-cli CONFIG GET appendfilename` AND `ls /opt/homebrew/var/db/redis/` after `brew services restart redis + small write`, and adjusts the assertion to match what Redis 8.x actually produces (likely a manifest file + base + incr files, not a single monolithic appendonly.aof).

## Open Questions (RESOLVED)

1. **Redis 8.x AOF file layout — single `appendonly.aof` or split manifest?**
   - What we know: redis-server 8.4.0 is installed; default conf has `appendfilename "appendonly.aof"`.
   - What's unclear: Redis 7 introduced multi-part AOF (`.base.rdb` + `.incr.aof` files + a manifest), and Phase 1 success criterion #1 names a single `appendonly.aof` path. The actual on-disk file may be `appendonly.aof.1.base.rdb` + `appendonly.aof.1.incr.aof` + `appendonly.aof.manifest`.
   - **RESOLVED:** Accept any file matching the `appendonly.aof*` glob. Plan 03's `scripts/verify-redis-config.sh` implements glob-tolerant AOF presence assertion; Phase 1 success criterion #1 wording in ROADMAP.md to be updated by orchestrator post-plan.

2. **Tune the parallel-launch wall-time threshold (Pattern 3 test)**
   - What we know: 3 parallel `em-proj --version` calls should complete in roughly the max single-call duration (~150-300ms cold).
   - What's unclear: actual cold-start of `em-proj` from `uv tool install`'s shim on this machine.
   - **RESOLVED:** Initial ceiling = 600ms (2× empirical ~300ms cold-start upper bound). Plan 04 records this as the starting threshold and instructs the executor to tune on first run if the test flakes; observed wall-time recorded in Plan 04's SUMMARY.md.

3. **Should `EM_PROJ_REDIS_DB` env var be reserved in Phase 1 or deferred to Phase 2?**
   - What we know: harness needs children to talk to db=15, not db=0. Phase 1's `em-proj --version` doesn't touch Redis, so the env var isn't *needed* yet.
   - What's unclear: whether the planner wants to bake the env-var contract into the Phase 1 client wrapper for Phase 2 to inherit, or push it to Phase 2.
   - **RESOLVED:** Yes — include the env-var read in `get_client()` from day one (3-line addition: `db = int(os.environ.get("EM_PROJ_REDIS_DB", "0"))`). Plan 03 Task 1 implements; Plan 04's `multiproc_race` injects `EM_PROJ_REDIS_DB=15` into child env so children land on the test DB cleanly when Phase 2 verbs arrive.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `uv` | All Python tooling | ✓ | 0.9.26 | — |
| `python3` (≥3.12) | Runtime | ✓ | 3.13.12 (system; uv will manage 3.12+ envs) | uv can install 3.12 if missing |
| `redis-server` | Backend | ✓ | 8.4.0 | — |
| `redis-cli` | Verification + config inspection | ✓ | 8.4.0 | — |
| `brew` | Service management | ✓ | 5.1.11 | — |
| `pytest` (system) | Harness | ✓ | 9.0.3 (system) | uv installs project-local version via dev deps |
| brew-managed redis service | REDIS-01 acceptance | ✗ (installed but not started; `brew services list` shows `none`) | — | `brew services start redis` (Phase 1 task) |
| `appendonly.aof` file | REDIS-01 acceptance | ✗ (`/opt/homebrew/var/db/redis/` is empty) | — | Edit `redis.conf` → `appendonly yes`, restart, write, verify |
| `em-proj` binary on PATH | Harness | ✗ (Phase 1 creates it) | — | `uv tool install --editable .` (Phase 1 task) |

**Missing dependencies with no fallback:** None — every gap is filled by a Phase 1 task.

**Missing dependencies with fallback:** All four "missing" items above are expected gaps that Phase 1 plan tasks must close. The planner should include explicit tasks for: (1) edit brew redis.conf, (2) start redis service, (3) verify AOF, (4) `uv sync` + `uv tool install --editable .`.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest >=8.0,<10.0 (current system: 9.0.3) |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` (Wave 0 creates) |
| Quick run command | `uv run pytest tests/unit -x` |
| Full suite command | `uv run pytest -ra` |
| Multiprocess-only | `uv run pytest tests/multiprocess` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| REDIS-01 | brew-managed Redis with `appendonly yes`, `appendfsync everysec`, `save 900 1`; AOF visible | smoke (shell + redis-cli) | `redis-cli CONFIG GET appendonly` then `ls /opt/homebrew/var/db/redis/appendonly.aof*` | ❌ Wave 0 (shell script `scripts/verify-redis-config.sh`) |
| CLI-01 | `em-proj` installable via `uv tool install --editable .` and on PATH | smoke | `command -v em-proj && em-proj --version` | ❌ Wave 0 (test in `tests/unit/test_cli.py`) |
| CLI-02 | typer dispatch scaffold — `--version` and `--help` work, ready for `add_typer` | unit | `uv run pytest tests/unit/test_cli.py::test_version tests/unit/test_cli.py::test_help -x` | ❌ Wave 0 |
| TEST-01 | Multi-process harness can spawn N fork+exec children racing em-proj at CLI boundary | integration | `uv run pytest tests/multiprocess/test_harness_self.py -x` | ❌ Wave 0 |
| TEST-02 | Harness lands and self-tests pass BEFORE any locking/claim code | ordering (TDD enforcement) | `uv run pytest tests/multiprocess/test_harness_self.py::test_race_launches_in_parallel_not_sequence -x` | ❌ Wave 0 |

**Note on REDIS-02 (not officially in Phase 1 but landing here per CONTEXT D-17/D-19):** Redis-unreachable error UX is testable via `test_redis_client.py::test_die_if_redis_unreachable_prints_actionable_message` — can be a unit test that stubs `client.ping()` to raise `ConnectionError` and asserts on stderr capture (via `capsys` since the print happens in-process).

### Sampling Rate

- **Per task commit:** `uv run pytest tests/unit -x` (~1s, no Redis dependency)
- **Per wave merge:** `uv run pytest -ra` (full suite incl. multiprocess; ~5-15s)
- **Phase gate:** `uv run pytest -ra` green + manual `redis-cli CONFIG GET *` verification dump + `em-proj --version` on a fresh shell

### Wave 0 Gaps

- [ ] `pyproject.toml` — entire file; defines `[tool.pytest.ini_options]` block
- [ ] `tests/conftest.py` — `redis_precheck`, `clean_db`, `multiproc_race` fixtures (Pattern 3)
- [ ] `tests/unit/__init__.py`, `tests/multiprocess/__init__.py` — empty (or omit if pytest discovery is configured for src-layout)
- [ ] `tests/unit/test_cli.py` — CLI-02 coverage (`--version`, `--help` exit codes)
- [ ] `tests/unit/test_redis_client.py` — lazy init test + error-translation test (foundational for REDIS-02 in Phase 2)
- [ ] `tests/multiprocess/test_harness_self.py` — TEST-01/TEST-02 self-tests
- [ ] `scripts/verify-redis-config.sh` — bash one-liner that asserts the four REDIS-01 settings and AOF presence; runnable in CI or by hand
- [ ] Framework install: `uv sync` + `uv tool install --editable .` — bootstrap commands; document in README

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Redis on loopback; no auth surface; single-user single-machine |
| V3 Session Management | no | No HTTP/web surface in Phase 1 |
| V4 Access Control | no | Loopback Redis + OS file permissions on AOF; no multi-user model |
| V5 Input Validation | partial | typer auto-validates CLI args (type coercion); Phase 1 only has `--version`/`--help`, no user-provided strings to inject |
| V6 Cryptography | no | No secrets stored; no encryption needs in Phase 1 |
| V7 Error Handling | yes | Redis-unreachable path MUST NOT leak Python tracebacks to user (D-17); structured one-line stderr message + exit 1 |
| V8 Data Protection | partial | AOF file at `/opt/homebrew/var/db/redis/appendonly.aof` inherits OS file permissions (likely `600` or `644` from brew); document that this is loopback-only, single-user — no encryption-at-rest in M1 |
| V14 Configuration | yes | Redis binds to loopback only (`bind 127.0.0.1 ::1` — verify in brew conf, the default); no `requirepass` needed because loopback + single-user; document the threat model assumption |

### Known Threat Patterns for {Python CLI + local Redis} stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Redis bound to all interfaces (0.0.0.0) by accident | Information Disclosure | Verify `redis-cli CONFIG GET bind` returns `127.0.0.1 ::1`; document the assumption in conf-edit task |
| `pickle.loads` on Redis-stored data | Tampering / RCE | N/A Phase 1 (no serialization yet); flag for Phase 2: store strings/JSON, never pickle |
| Lua script injection via user-controlled `KEYS`/`ARGV` | Tampering | Use `register_script` with literal Lua strings; never concatenate user input into Lua source. Phase 4 concern. |
| Stack-trace leak to stderr exposing internal paths | Information Disclosure | The `die_if_redis_unreachable` pattern (Pattern 2) catches specifically and prints one line — no traceback |
| Race conditions in test fixture allowing cross-test data pollution | Tampering (of tests) | Function-scoped `FLUSHDB` in `clean_db` fixture; explicit `db=15` constant |

**Phase 1 security posture:** Effectively non-applicable beyond V7 (no traceback leaks) and V14 (loopback-bind verification). The CLI surface is `--version` and `--help`; no user-controlled inputs flow to Redis or to system commands. Document and move on; the heavy security thinking lands in Phase 4 (claim model with cross-session metadata).

## Project Constraints (from PROJECT.md / no project CLAUDE.md)

There is no `./CLAUDE.md` in the repo root. The user-global `~/.claude/CLAUDE.md` is informational (RTK, planning artifact storage, skill namespace) — none of those impose Phase 1 implementation constraints. PROJECT.md constraints that DO apply:

- **Stack:** Python 3.12+ via uv. Runtime deps limited to `typer`, `redis-py`, `pytest`. No Node/Go/Rust.
- **Communication style:** Concise, opinionated recommendations; no vendor-tradeoff matrices (honored throughout this RESEARCH.md by single recommendations + one-line rationale).
- **Shell idioms:** Avoid `ls | while read` — use glob loops (`for f in dir/*`). N/A for Phase 1's Python code, but if any bash helper scripts (e.g., `scripts/verify-redis-config.sh`) land, they must follow this.
- **Output convention:** Plain text on TTY by default; JSON when stdout is not a TTY OR `--json`. Phase 1 only emits `em-proj --version` text; the typer scaffold MUST NOT preclude adding `--json` later (it doesn't — `--json` would be a top-level option on the same `@app.callback()` Phase 2 can append).
- **Semantic exit codes:** 0/1/2/3. Phase 1 only uses 0 (success) and 1 (Redis unreachable, even though Phase 1's `--version` never triggers it — the wrapper code is the foundation).
- **Errors to stderr.** Honored by `print(..., file=sys.stderr)` in `die_if_redis_unreachable`.

## Sources

### Primary (HIGH confidence)
- `pip index versions typer` / `redis` / `pytest` — verified 2026-05-18 on this machine
- `uv --version` → 0.9.26, `redis-server --version` → 8.4.0, `brew --version` → 5.1.11 — verified 2026-05-18
- Direct grep of `/opt/homebrew/etc/redis.conf` — confirmed `appendonly no` and `save` commented out as brew defaults
- [redis-py exception hierarchy](https://github.com/redis/redis-py/blob/master/redis/exceptions.py) — full hierarchy retrieved
- [typer add_typer pattern](https://typer.tiangolo.com/tutorial/subcommands/add-typer/) — official tutorial
- [typer --version flag pattern](https://typer.tiangolo.com/tutorial/options/version/) — official tutorial
- [redis-py Lua scripting docs](https://redis.readthedocs.io/en/stable/lua_scripting.html) — `register_script` API and NOSCRIPT auto-retry

### Secondary (MEDIUM confidence)
- [pytest capture stdout/stderr](https://docs.pytest.org/en/stable/how-to/capture-stdout-stderr.html) — `capsys` vs `capfd` distinction
- [uv tool install reference](https://docs.astral.sh/uv/reference/cli/) — `--editable` flag and `[project.scripts]` handling (docs incomplete; cross-verified with practitioner behavior)
- [uv project init concepts](https://docs.astral.sh/uv/concepts/projects/init/) — pyproject.toml shape for src-layout

### Tertiary (LOW confidence; cross-referenced)
- [Subprocess parallel launch pattern](https://shuzhanfan.github.io/2017/12/parallel-processing-python-subprocess/) — community example of the Popen-list + wait-list idiom
- [macOS Obj-C fork safety](https://www.wefearchange.org/2018/11/forkmacos.rst) — the canonical write-up explaining why `multiprocessing.fork` is dangerous on macOS but `subprocess.Popen` is fine
- [Reduce redis-py default max_connections issue #2220](https://github.com/redis/redis-py/issues/2220) — confirms `2**31` default and lazy-creation semantics

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all versions verified against the installed `pip index` registry; library APIs verified against official docs
- Architecture / fixture pattern: HIGH — subprocess.Popen patterns and pytest fixtures are well-documented Python idioms; the parallel-launch detail is cross-verified
- Redis brew config pitfall: HIGH — grepped the actual file on this machine 2026-05-18
- AOF file layout post-Redis-7 (Assumption A4): MEDIUM — flagged as Open Question #1
- Cold-start wall-time threshold (Assumption A2): MEDIUM — flagged as Open Question #2

**Research date:** 2026-05-18
**Valid until:** 2026-06-15 (30 days; stack is stable, only AOF file-layout detail might surprise)
