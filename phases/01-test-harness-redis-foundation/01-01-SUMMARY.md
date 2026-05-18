---
phase: 01-test-harness-redis-foundation
plan: 01
subsystem: infra
tags: [python, uv, hatchling, src-layout, packaging, typer, redis, pytest]

# Dependency graph
requires:
  - phase: bootstrap
    provides: empty repo root with .planning/ worktree and .gitignore
provides:
  - PyPA src/-layout Python project skeleton at repo root
  - pyproject.toml with PEP 621 metadata + hatchling build backend
  - Locked runtime deps (typer 0.25.1, redis 7.4.0) and dev deps (pytest 9.0.3) in uv.lock
  - em-proj script entry point declared (em_proj.cli:app) for Plan 02's uv tool install
  - src/em_proj/ package with __init__.py (exposes __version__), __main__.py (delegates to cli.app), and a placeholder cli.py (raises NotImplementedError pending Plan 02)
  - .python-version pin (3.12) for uv
  - .gitignore coverage for Python build artifacts (.venv/, __pycache__/, *.egg-info/, dist/, .pytest_cache/, *.pyc)
  - README.md bootstrap section documenting uv sync
  - [tool.pytest.ini_options] block (testpaths=["tests"], strict markers/config) for Plans 02-04 to discover tests under tests/unit/ and tests/multiprocess/
affects: [01-02 (typer scaffold + CLI install), 01-03 (Redis client wrapper + verify-redis-config.sh), 01-04 (multi-process harness fixtures)]

# Tech tracking
tech-stack:
  added: [uv 0.9.26, hatchling >=1.18, typer 0.25.1, redis 7.4.0, pytest 9.0.3]
  patterns:
    - "PyPA src/-layout (D-01) — package source under src/em_proj/, prevents accidental imports of pre-install code, plays cleanly with uv tool install --editable"
    - "Entry-point indirection (D-02) — em-proj = em_proj.cli:app in pyproject.toml + __main__.py for python -m em_proj (harness debugging without PATH)"
    - "Placeholder file pattern — cli.py raises NotImplementedError with a clear message pointing at the next plan; keeps __main__.py import-resolvable without locking in the typer body Plan 02 will wholesale-replace"

key-files:
  created:
    - pyproject.toml
    - .python-version
    - src/em_proj/__init__.py
    - src/em_proj/__main__.py
    - src/em_proj/cli.py
    - README.md
    - uv.lock
  modified:
    - .gitignore

key-decisions:
  - "hatchling chosen over uv_build for build backend (RESEARCH Assumption A1 — editable-install maturity)"
  - "Placeholder cli.py with NotImplementedError instead of stub typer.Typer() — Plan 02 wholesale-replaces and embedding a real app body now would create merge friction"
  - "uv.lock committed (not gitignored) — pins exact resolved transitive deps so downstream plans race against a deterministic dependency graph"

patterns-established:
  - "Lower-bound + upper-bound dep pins for runtime libs (typer<1.0, redis<8.0, pytest<10.0) — defeats accidental major-version landing via uv sync; per threat T-01-01-01"
  - "Build-backend gets lower-bound only (hatchling>=1.18) — upper-bound on build backends has historically caused more breakage than it prevents"
  - "Generated uv.lock is committed; .venv/ is gitignored"

requirements-completed: [CLI-01]

# Metrics
duration: ~5min
completed: 2026-05-18
---

# Phase 01 Plan 01: Python project scaffold + locked deps Summary

**PyPA src/-layout Python project skeleton landed: pyproject.toml with hatchling backend + locked typer/redis/pytest deps, src/em_proj/ package with entry-point indirection, and uv sync producing a working .venv/.**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-05-18T14:26Z (approximate; first task commit was 2026-05-18T14:27:14-07:00)
- **Completed:** 2026-05-18T14:28:25-07:00 (Task 2 commit; SUMMARY commit follows)
- **Tasks:** 2
- **Files modified:** 8 (7 created, 1 appended)

## Accomplishments
- pyproject.toml with PEP 621 metadata, hatchling >=1.18 build backend, and the em-proj = "em_proj.cli:app" script entry point that Plan 02 will consume via uv tool install
- Locked deps via uv sync: typer 0.25.1, redis 7.4.0, pytest 9.0.3 (all within RESEARCH-verified bounds)
- src/em_proj/ package created per D-01: __init__.py exposes __version__ = "0.1.0"; __main__.py is a 3-line delegation to em_proj.cli.app per D-02; cli.py is a placeholder that raises NotImplementedError with a message pointing at Plan 02
- [tool.pytest.ini_options] block in pyproject.toml discovers tests/unit/ and tests/multiprocess/ per D-03 (Plans 02-04 will populate those dirs)
- .python-version pinned to 3.12 (uv-managed); .gitignore appended with all Python build artifacts; uv.lock committed for reproducible dependency resolution
- README.md bootstrap section documents `uv sync` (Plan 02 appends `uv tool install --editable .`)

## Task Commits

Each task was committed atomically (no Co-Authored-By trailer per project policy):

1. **Task 1: pyproject.toml + .python-version + .gitignore** — `922bcde` (feat)
2. **Task 2: src/em_proj/ package + README + uv sync (uv.lock)** — `a6f7dee` (feat)

**Plan metadata:** committed in this SUMMARY's commit by the worktree merge step (STATE.md / ROADMAP.md updates owned by the orchestrator post-merge, per parallel_execution rules).

## Files Created/Modified

- `pyproject.toml` — PEP 621 metadata, hatchling backend, locked runtime + dev deps, em-proj script entry point, [tool.hatch.build.targets.wheel] packages = ["src/em_proj"], [tool.pytest.ini_options]
- `.python-version` — single line `3.12` (uv reads this to pin the project's Python version)
- `.gitignore` — appended .venv/, __pycache__/, *.egg-info/, dist/, .pytest_cache/, *.pyc
- `src/em_proj/__init__.py` — module docstring + `__version__ = "0.1.0"`
- `src/em_proj/__main__.py` — 3-line shim: `from em_proj.cli import app` + `if __name__ == "__main__": app()`
- `src/em_proj/cli.py` — placeholder `def app() -> None: raise NotImplementedError(...)`; Plan 02 wholesale-replaces
- `README.md` — heading + bootstrap section (`uv sync`)
- `uv.lock` — pinned dependency resolution (14 packages installed: em-proj + typer/redis/pytest + transitive)

## Locked Dependency Versions

From `uv.lock` (the deterministic resolution future plans race against):

| Package | Version | Source |
|---------|---------|--------|
| typer | 0.25.1 | runtime (pyproject `dependencies`) |
| redis | 7.4.0 | runtime (pyproject `dependencies`) |
| pytest | 9.0.3 | dev (pyproject `[dependency-groups] dev`) |
| em-proj | 0.1.0 | local editable install |
| (transitive) | annotated-doc 0.0.4, click 8.4.0, iniconfig 2.3.0, markdown-it-py 4.2.0, mdurl 0.1.2, packaging 26.2, pluggy 1.6.0, pygments 2.20.0, rich 15.0.0, shellingham 1.5.4 | resolved by uv |

All three runtime/dev versions land within the RESEARCH-verified bounds (`typer>=0.16,<1.0`, `redis>=6.0,<8.0`, `pytest>=8.0,<10.0`).

## Verification Results

All five plan-level verification commands green:

| Check | Command | Result |
|-------|---------|--------|
| pyproject.toml parses | `python3 -c "import tomllib; tomllib.loads(open('pyproject.toml').read())"` | exit 0 |
| .venv operational | `test -x .venv/bin/python` | exit 0 |
| __version__ = 0.1.0 | `.venv/bin/python -c "import em_proj; print(em_proj.__version__)"` | prints `0.1.0` |
| __main__ importable | `.venv/bin/python -c "from em_proj.__main__ import app"` | exit 0 (placeholder importable, no ImportError) |
| Entry point declared | `grep -c 'em_proj.cli:app' pyproject.toml` | 1 |

Bonus sanity check: `.venv/bin/python -m em_proj` raises `NotImplementedError("em-proj typer scaffold not yet installed — see Phase 1 Plan 02")` exactly as designed. Plan 02 will replace this with the typer app body.

## Decisions Made

- **Committed uv.lock:** uv.lock is the deterministic-resolution artifact; without it, downstream plans could race against different transitive dep versions per developer machine. .gitignore explicitly excludes .venv/ (regeneratable from uv.lock) but NOT uv.lock itself. This follows uv's recommended workflow.
- **Placeholder cli.py was kept minimal (one function, one raise):** Per plan instructions, embedding the typer body now creates merge friction with Plan 02's wholesale replacement. The placeholder is intentionally trivial — its purpose is to make `from em_proj.cli import app` resolve cleanly between Plan 01 and Plan 02.
- **All four "missing dependency" gaps from RESEARCH §Environment Availability are still open** (brew redis not started yet, AOF not present, em-proj not yet on PATH, redis.conf still default). Those are Plan 02 / Plan 03 / Plan 04 concerns, not Plan 01 scope.

## Deviations from Plan

None - plan executed exactly as written.

The only non-obvious-but-still-in-scope item: I committed `uv.lock` alongside the Task 2 artifacts. The plan's task 2 `<files>` list does not name uv.lock explicitly, but the task action says "Run `uv sync` from the repo root" which generates uv.lock as a deterministic artifact of that command. Leaving it untracked would have left an uncommitted file in the working tree (caught by the executor's post-task untracked-file check) AND would have made the locked-version pins meaningless across future plans. This is the documented behavior of uv-managed projects.

## Known Stubs

| File | Line | Stub | Resolution |
|------|------|------|------------|
| `src/em_proj/cli.py` | 4-6 | `def app() -> None: raise NotImplementedError(...)` | **Intentional and plan-documented.** Plan 02 wholesale-replaces this file with the typer `app = typer.Typer(...)` per CONTEXT D-02 and PLAN.md `<interfaces>` forward contract. The placeholder ensures `from em_proj.cli import app` resolves between plans without ImportError; invoking it raises a clear message pointing at the resolving plan. |

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required by Plan 01. (Plans 02-04 will require Redis to be brew-running and the em-proj binary to be uv-tool-installed; documented in those plans' SUMMARY files when they land.)

## Next Phase Readiness

**Wave 2 (Plans 02 and 03) can proceed in parallel.** Both depend on:

- ✅ `pyproject.toml` exists with `em-proj = "em_proj.cli:app"` entry point (Plan 02 consumes via `uv tool install --editable .`)
- ✅ `src/em_proj/` package exists in the correct shape (D-01 src/-layout, D-02 cli.py + __main__.py)
- ✅ `[tool.pytest.ini_options]` block discovers `tests/unit/` and `tests/multiprocess/` (Plans 02 and 04 populate those dirs)
- ✅ Locked dep versions in uv.lock (Plans 02-04 race against the same resolved typer/redis/pytest versions)
- ✅ `.venv/bin/python` operational (Plans 02-04 can `uv run pytest` immediately)

Plan 02 will: (a) replace `src/em_proj/cli.py` with the real typer `app` body (--version callback + add_typer mount-point), (b) populate `tests/unit/test_cli.py`, (c) run `uv tool install --editable .` and document it in README, (d) verify `em-proj --version` from a fresh shell.

Plan 03 will: (a) create `src/em_proj/redis_client.py` with lazy `get_client()` + error translation per CONTEXT D-07/D-08/D-09/D-17, (b) populate `tests/unit/test_redis_client.py`, (c) write `scripts/verify-redis-config.sh` and the brew redis.conf edits for REDIS-01.

No blockers for Plan 02 or Plan 03. Plan 04 (the multiprocess harness) waits on Plan 02 (needs `em-proj` on PATH for the harness self-test).

## Self-Check: PASSED

Files created/modified verified to exist in worktree:
- `pyproject.toml` — FOUND
- `.python-version` — FOUND
- `src/em_proj/__init__.py` — FOUND
- `src/em_proj/__main__.py` — FOUND
- `src/em_proj/cli.py` — FOUND
- `README.md` — FOUND
- `uv.lock` — FOUND
- `.gitignore` — FOUND (modified)

Commits exist on branch `worktree-agent-a678dfaccd85fb39c`:
- `922bcde` — FOUND (Task 1)
- `a6f7dee` — FOUND (Task 2)

---
*Phase: 01-test-harness-redis-foundation*
*Completed: 2026-05-18*
