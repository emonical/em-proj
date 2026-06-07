---
phase: 01-test-harness-redis-foundation
plan: 03
subsystem: infra
tags: [redis, brew, aof, persistence, python, redis-py, error-translation, pytest, monkeypatch]

# Dependency graph
requires:
  - phase: 01-test-harness-redis-foundation/01
    provides: pyproject.toml with redis>=6.0,<8.0 dep + uv.lock + src/em_proj/ package layout + .venv built from uv sync
provides:
  - src/em_proj/redis_client.py — lazy module-level Redis client (no socket until first command per D-07) with EM_PROJ_REDIS_DB env-var plumbing (RESEARCH Open Question #3) and die_if_redis_unreachable() error UX (D-17 single-chokepoint foundation per D-19)
  - tests/unit/test_redis_client.py — 4 in-process pytest tests covering lazy contract, env-var read, ConnectionError translation, TimeoutError translation
  - scripts/verify-redis-config.sh — reusable bash check for the four REDIS-01 settings + AOF presence (exit codes 0/1/2 = ok/wrong/unreachable); glob-tolerant for monolithic AND Redis 8.x split-AOF layouts
  - /opt/homebrew/etc/redis.conf edited for REDIS-01 (appendonly no -> appendonly yes; appended save 900 1) — system-level, with .bak rollback
  - brew-managed Redis service running with REDIS-01 config + AOF on disk
affects: [01-04 (multi-process harness — needs running Redis on db=15 + EM_PROJ_REDIS_DB env contract), 02-* (state verbs all import get_client + die_if_redis_unreachable), 04-* (claim model uses register_script via the same client)]

# Tech tracking
tech-stack:
  added: []  # no new deps; redis-py 7.4.0 was already locked in Plan 01
  patterns:
    - "Lazy module-level singleton with explicit reset for test isolation"
    - "Sentinel SystemExit subclass for clean-exit error UX — _RedisUnreachable(SystemExit) raises without a traceback (Python suppresses SystemExit tracebacks by default) and carries exit code 1; satisfies D-17 no-traceback contract"
    - "Env-var resolution chain pattern — explicit arg -> EM_PROJ_REDIS_DB env -> DEFAULT_DB"
    - "Bash verify-script with glob-tolerant resource-presence check — 'shopt -s nullglob' + multiple glob patterns to cover schema-version-dependent on-disk layouts"
    - "REDIS-01 brew config edit pattern — sed -i.bak for in-place line flip + printf >> for the append; idempotent across rerun"

key-files:
  created:
    - src/em_proj/redis_client.py
    - tests/unit/test_redis_client.py
    - scripts/verify-redis-config.sh
  modified:
    - /opt/homebrew/etc/redis.conf  # system-level — NOT in git; .bak is the audit trail

key-decisions:
  - "RESEARCH Open Question #1 RESOLVED — this machine's brew Redis 8.4.0 uses the split-AOF layout under /opt/homebrew/var/db/redis/appendonlydir/ (appendonly.aof.1.base.rdb + appendonly.aof.1.incr.aof + appendonly.aof.manifest). Plan's prescribed verify-script glob would have silently reported zero matches. Extended to cover both monolithic and split layouts in a single nullglob expansion."
  - "RESEARCH Open Question #3 RESOLVED — EM_PROJ_REDIS_DB env-var contract baked into get_client() now; default 0 when unset; Plan 04's multiproc_race fixture will inject EM_PROJ_REDIS_DB=15."
  - "Kept _reset_for_tests() as the explicit test seam (vs. monkeypatching m._client directly). Underscore-prefix marks it private; tests use it through em_proj.redis_client.rc._reset_for_tests()."
  - "tests/unit/__init__.py NOT created in this worktree — that's Plan 02's territory (running in parallel). pytest's testpaths=['tests'] config discovers tests/unit/test_redis_client.py without needing the __init__.py marker."

patterns-established:
  - "All future Phase 2+ state verbs MUST construct redis-py clients through em_proj.redis_client.get_client() — never call redis.Redis(...) directly elsewhere in the codebase. Enforces D-19 single-chokepoint contract. Future-phase grep gate: 'grep -r \"redis.Redis(\" src/ --include=*.py' should match only src/em_proj/redis_client.py."
  - "Stub redis.connection.Connection.connect via monkeypatch — preferred over inspecting redis-py's private pool._created_connections counter."
  - "verify-redis-config.sh is the canonical 'is REDIS-01 still satisfied?' command — CI-callable, dev-debuggable, idempotent."

requirements-completed: [REDIS-01]

# Metrics
duration: 5min
completed: 2026-05-18
---

# Phase 01 Plan 03: Redis client wrapper + REDIS-01 brew config Summary

**Lazy module-level Redis client with D-17 error translation + brew-managed Redis flipped to appendonly=yes + save 900 1 (RESEARCH Open Question #1 resolved: Redis 8.4 split-AOF under appendonlydir/).**

## Performance

- **Duration:** ~5 min (275s wall-clock)
- **Started:** 2026-05-18T21:34:24Z
- **Completed:** 2026-05-18T21:38:59Z (Task 3 commit; Task 4 is a human-verify checkpoint)
- **Tasks completed:** 3 of 4 (Task 4 pending human-verify)

## Accomplishments

- **`src/em_proj/redis_client.py`** — the single chokepoint per D-19. Lazy `get_client(db=None)` (no socket until first command), EM_PROJ_REDIS_DB env-var honored (default 0), `die_if_redis_unreachable(client)` catches both `redis.ConnectionError` and `redis.TimeoutError` → prints the exact D-17 one-line message to stderr → raises `_RedisUnreachable` (SystemExit subclass, exit 1, no traceback). socket_connect_timeout=2.0 + socket_timeout=5.0 cap both stuck-connect and stuck-command paths.
- **REDIS-01 brew config flipped LIVE.** `/opt/homebrew/etc/redis.conf` now has `appendonly yes` (was `no`) and `save 900 1` appended (was commented out per RESEARCH Pitfall #1). `appendfsync everysec` was already brew default. `redis.conf.bak` is the rollback baseline. `brew services restart redis` applied the new config. AOF files created via `redis-cli SET _aof_bootstrap 1 + DEL`.
- **`scripts/verify-redis-config.sh`** — reusable bash check; exit codes 0/1/2 = ok/setting-wrong/redis-unreachable; uses `shopt -s nullglob` for AOF presence covering both monolithic and Redis 8.x split layouts. Currently exits 0 on this machine.
- **`tests/unit/test_redis_client.py`** — 4 in-process pytest tests, runs in <0.1s with zero Redis dependency:
  - `test_get_client_lazy_no_socket_on_import`
  - `test_get_client_reads_env_var`
  - `test_die_if_redis_unreachable_prints_actionable_message`
  - `test_die_if_redis_unreachable_catches_timeout`

## Task Commits

Each task committed atomically (no Co-Authored-By trailer per project policy):

1. **Task 1: src/em_proj/redis_client.py** — `a671a26` (feat)
2. **Task 2: redis.conf edit + brew restart + scripts/verify-redis-config.sh** — `d39046e` (feat)
3. **Task 3: tests/unit/test_redis_client.py** — `424d78f` (test)
4. **Task 4: human-verify checkpoint** — PENDING (surfaced to orchestrator for user approval)

## Files Created/Modified

- `src/em_proj/redis_client.py` (CREATED, 80 lines)
- `scripts/verify-redis-config.sh` (CREATED, 56 lines, chmod +x'd)
- `tests/unit/test_redis_client.py` (CREATED, 99 lines)
- `/opt/homebrew/etc/redis.conf` (MODIFIED — system-level, NOT in git)
- `/opt/homebrew/etc/redis.conf.bak` (CREATED — system-level, NOT in git)

### Diff against `redis.conf.bak`

```
1387c1387
< appendonly no
---
> appendonly yes
2295a2296,2298
>
> # em-proj REDIS-01 — added by Phase 1 Plan 03
> save 900 1
```

Exactly two edits — one line flipped, three lines appended.

### On-disk AOF layout (resolves RESEARCH Open Question #1)

`/opt/homebrew/var/db/redis/appendonlydir/`:
- `appendonly.aof.1.base.rdb` (88 bytes)
- `appendonly.aof.1.incr.aof` (98 bytes)
- `appendonly.aof.manifest` (102 bytes)

Redis 7+ / 8.x multipart AOF layout controlled by `appenddirname` config knob (default `"appendonlydir"`).

## Public API Surface (for downstream consumers — Plan 04 + Phase 2+)

```python
# src/em_proj/redis_client.py

def get_client(db: int | None = None) -> redis.Redis:
    """Lazy module-level singleton. Resolution: explicit db > EM_PROJ_REDIS_DB env > DEFAULT_DB(0)."""

def die_if_redis_unreachable(client: redis.Redis) -> None:
    """Pre-flight check. Catches ConnectionError + TimeoutError; prints D-17 one-line stderr; raises _RedisUnreachable (exit 1, no traceback)."""

class _RedisUnreachable(SystemExit):
    """exit code 1; SystemExit subclass = no Python traceback."""

def _reset_for_tests() -> None:
    """Test seam — clears the module singleton between tests. Do not call from production code."""
```

```bash
# scripts/verify-redis-config.sh exit code contract:
#   0 = appendonly=yes + appendfsync=everysec + save=900 1 + AOF present
#   1 = one or more settings wrong (printed to stderr)
#   2 = redis unreachable (printed to stderr; suggests `brew services start redis`)
```

## Decisions Made

- **EM_PROJ_REDIS_DB env-var contract reserved in Phase 1** (vs. punting to Phase 2). Three-line addition; lets Plan 04's `multiproc_race` fixture set `EM_PROJ_REDIS_DB=15` in subprocess env from day one. Resolves RESEARCH Open Question #3.
- **`_RedisUnreachable` is a SystemExit subclass** (vs. `raise SystemExit(1) from None`). Python suppresses tracebacks for SystemExit subclasses by default.
- **`_reset_for_tests()` as explicit test seam** instead of `monkeypatch.setattr(rc, '_client', None)`.
- **Glob-tolerant AOF check covers BOTH layouts in a single nullglob** rather than branching on the `appenddirname` config value.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Plan's prescribed AOF glob would silently miss Redis 8.x split-AOF layout**

- **Found during:** Task 2 (verify-redis-config.sh post-write check)
- **Issue:** Plan-prescribed glob was `"$AOF_DIR"/appendonly.aof*`. On Redis 8.4.0 the AOF files land under `/opt/homebrew/var/db/redis/appendonlydir/`. Plan's glob would return zero matches.
- **Fix:** Extended the glob to cover BOTH layouts: `aof_files=("$AOF_DIR"/appendonly.aof* "$AOF_DIR"/appendonlydir/appendonly.aof*)`
- **Verification:** `bash scripts/verify-redis-config.sh` exits 0
- **Committed in:** `d39046e` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed.
**Impact:** Necessary for correctness; delivers exactly what the plan's `<action>` block said about glob-tolerance.

## Issues Encountered

- **`brew services list` showed redis=`none` at plan start** — expected per RESEARCH §Environment Availability. Resolved by `brew services restart redis`.
- **Initial zsh glob attempt failed with "no matches found"** while inspecting AOF dir — worked around with `bash -c 'shopt -s nullglob; ...'`. The verify script itself sets `shopt -s nullglob` explicitly.
- **`.venv/` had to be materialized** via `uv sync` before acceptance commands could run. `uv sync` resolved 15 packages in 10ms.
- **Write tool blocked at subagent-policy layer** — worked around by returning SUMMARY content via the final-message channel; orchestrator wrote/committed it on the planning branch.

## Pending Human-Verify Checkpoint (Task 4)

Six verification checks (Task 4 of plan 01-03):

1. `grep -E '^(appendonly|appendfsync|save) ' /opt/homebrew/etc/redis.conf` → three expected lines
2. `diff /opt/homebrew/etc/redis.conf.bak /opt/homebrew/etc/redis.conf` → exactly the diff shown above
3. `brew services list | grep redis` → `started`
4. `redis-cli CONFIG GET appendonly && redis-cli CONFIG GET appendfsync && redis-cli CONFIG GET save` → live values match
5. `ls /opt/homebrew/var/db/redis/` → `appendonlydir/` present with three files
6. `bash scripts/verify-redis-config.sh` → exit 0 with OK message

**Rollback if needed:** `cp /opt/homebrew/etc/redis.conf.bak /opt/homebrew/etc/redis.conf && brew services restart redis`

## Threat Surface Audit

| Threat ID | Status | Notes |
|-----------|--------|-------|
| T-01-03-01 (traceback leak) | mitigated | `_RedisUnreachable(SystemExit)` + test asserts `"Traceback" not in captured.err` |
| T-01-03-02 (Redis bound 0.0.0.0) | mitigated (pre-existing) | brew default `bind 127.0.0.1 ::1` unchanged |
| T-01-03-03 (FLUSHDB on prod db=0) | mitigated | `get_client()` defaults db=0 only if neither arg nor env set; tests use `EM_PROJ_REDIS_DB=15` |
| T-01-03-04 (DoS via stuck command) | mitigated | `socket_connect_timeout=2.0` + `socket_timeout=5.0` |
| T-01-03-05 (shell injection in verify script) | mitigated | argv-style invocations only |

## Next Phase Readiness

**Plan 04 (multi-process harness) is unblocked:**
- get_client() exists; harness can `from em_proj.redis_client import get_client` and `client.flushdb()` inside the `clean_db` fixture
- EM_PROJ_REDIS_DB env contract honored
- Redis reachable on `127.0.0.1:6379` with persistence
- `bash scripts/verify-redis-config.sh` callable from CI as precheck
- `die_if_redis_unreachable()` available for Phase 2 state verbs

**Phase 2+ state verbs:** every `em-proj state <verb>` MUST `from em_proj.redis_client import get_client, die_if_redis_unreachable`. Grep gate: `grep -r "redis.Redis(" src/ --include='*.py'` should match ONLY `src/em_proj/redis_client.py`.

---
*Phase: 01-test-harness-redis-foundation*
*Completed: 2026-05-18 (Task 4 human-verify pending)*
