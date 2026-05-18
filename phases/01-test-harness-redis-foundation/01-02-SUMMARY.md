---
phase: 01-test-harness-redis-foundation
plan: 02
subsystem: cli
tags: [typer, cli, uv-tool-install, python, annotated-options]

# Dependency graph
requires:
  - phase: 01
    plan: 01
    provides: src/em_proj/ package skeleton + pyproject.toml with em-proj=em_proj.cli:app entry point + uv.lock pinning typer 0.25.1
provides:
  - Working typer.Typer `app` at src/em_proj/cli.py with Annotated --version callback (is_eager=True), no_args_is_help=True, add_completion=False, and Phase 2 mount-point comment for app.add_typer(state_app, name="state")
  - tests/__init__.py + tests/unit/__init__.py as empty package markers (D-03 layout)
  - tests/unit/test_cli.py with test_version + test_help using typer.testing.CliRunner (in-process; the canonical VALIDATION.md per-task verify command)
  - em-proj binary installed via `uv tool install --editable .` (CLI-01 satisfied on this machine)
  - README.md "Tool install" section documenting the --editable requirement per RESEARCH Pitfall #5
affects: [01-03 (Redis client wrapper can land in parallel; no dependency in this direction), 01-04 (multiproc harness will spawn `em-proj --version` as the canonical "real binary" verb per D-06)]

# Tech tracking
tech-stack:
  added: []  # all libs already pinned by Plan 01's uv.lock; this plan only USES typer 0.25.1
  patterns:
    - "Annotated-style typer.Option (RESEARCH Pattern 1) — `Annotated[bool | None, typer.Option('--version', callback=..., is_eager=True)]` on `@app.callback()`; typer 0.16+ idiom that plays with mypy"
    - "Eager-callback short-circuit (`raise typer.Exit()` from inside the `_version_callback`) — bypasses subcommand validation so `--version` works identically whether the typer app has 0 subcommands (Phase 1) or many (Phase 2+)"
    - "uv tool install --editable . (D-04 + Pitfall #5) — installs em-proj binary into ~/.local/bin/em-proj with a live link back to the source tree; source edits propagate without re-install"
    - "Mount-point comment as a reservation — no actual `app.add_typer(state_app, name='state')` code in Phase 1 (D-06), only a comment block marking where Phase 2 will append. Keeps Phase 2 from rewriting the file."

key-files:
  created:
    - tests/__init__.py
    - tests/unit/__init__.py
    - tests/unit/test_cli.py
  modified:
    - src/em_proj/cli.py  # placeholder NotImplementedError replaced wholesale with typer scaffold
    - README.md           # appended "Tool install" section

key-decisions:
  - "Replaced placeholder cli.py wholesale (rm + recreate via Write tool, not Edit) — placeholder was 7 lines, new file is 49 lines; wholesale replacement matches plan instructions and avoids merge-friction with any leftover placeholder content"
  - "Used `uv sync` inside the worktree to materialize a .venv/ (gitignored). The worktree did not inherit Plan 01's .venv/ because .venv/ is gitignored — `uv sync` is the canonical command to reproduce it from uv.lock"
  - "uv tool install --editable . from the worktree path — the editable link points at /Users/emonical/projects/personal/ai-tools/em-proj/.claude/worktrees/agent-a638b4d0bc6312bac/, NOT the main repo root. After the worktree is cleaned up, the user will need to re-run `uv tool install --editable .` from the main repo root for a permanent install. Documented in 'Deviations from Plan' below."
  - "Did NOT add a test_no_args test for `no_args_is_help=True` behavior — plan explicitly told me to skip it because the exit code (0 vs 2) varies with Click version. Manual smoke check confirms exit 0 on this Click 8.4.0."

requirements-completed: [CLI-01 (em-proj on PATH via uv tool install --editable .), CLI-02 (typer dispatch scaffold — --version + --help work)]

# Metrics
duration: ~4 min (executor wall time)
completed: 2026-05-18
---

# Phase 01 Plan 02: typer CLI scaffold + uv tool install Summary

**Replaced Plan 01's placeholder `cli.py` with a real typer app (Annotated `--version` callback + Phase 2 mount-point reservation), landed in-process `CliRunner` tests for --version/--help, and installed `em-proj` on PATH via `uv tool install --editable .` — CLI-01 + CLI-02 both green on this machine; fresh-shell verification pending Task 4 human-verify.**

## Performance

- **Duration:** ~4 min executor wall time (3 auto tasks; Task 4 is a blocking human-verify checkpoint that is NOT counted in executor wall time)
- **Started:** 2026-05-18T21:32:54Z
- **Completed:** 2026-05-18T21:36:30Z (Task 3 commit; SUMMARY commit follows; Task 4 awaits human)
- **Tasks:** 3 of 3 auto tasks complete; 1 of 1 checkpoint awaiting (Task 4)
- **Files modified:** 5 (3 created, 2 modified)

## Accomplishments

- **`src/em_proj/cli.py`** wholesale-replaced (was placeholder `def app() -> None: raise NotImplementedError(...)`; now a 49-line typer scaffold per RESEARCH §Pattern 1)
    - `app = typer.Typer(name="em-proj", help=..., no_args_is_help=True, add_completion=False)`
    - `_version_callback(value)` raises `typer.Exit()` after `typer.echo(f"em-proj {__version__}")`
    - `@app.callback()` decorates `main()` with `Annotated[bool | None, typer.Option("--version", callback=..., is_eager=True, help=...)]`
    - Phase 2 mount-point comment block reserved: `# Phase 2 mount point — append below when state_app lands: ... app.add_typer(state_app, name="state")`
    - Bottom-of-file `if __name__ == "__main__": app()` for `python src/em_proj/cli.py` REPL debugging
- **`tests/__init__.py` + `tests/unit/__init__.py`** created as 0-byte package markers (D-03)
- **`tests/unit/test_cli.py`** created with two tests using `typer.testing.CliRunner`:
    - `test_version` asserts exit 0 and `"em-proj 0.1.0"` in stdout
    - `test_help` asserts exit 0 and `"--version"` in stdout (so a user discovers the flag) + program name or `Usage:` marker
- **`uv tool install --editable .`** ran clean from the worktree root; `em-proj` resolves to `/Users/emonical/.local/bin/em-proj`
    - `em-proj --version` → `em-proj 0.1.0` exit 0
    - `em-proj --help` → typer auto-rendered help, exit 0, contains `--version`
    - `em-proj` (no args) → typer auto-help, exit 0 (Click 8.4.0 honors `no_args_is_help=True` with exit 0)
- **`README.md`** appended "## Tool install" section documenting the `--editable` flag requirement per RESEARCH Pitfall #5

## Task Commits

Each task was committed atomically (no Co-Authored-By trailer per project policy):

1. **Task 1: Replace placeholder cli.py with typer app** — `885a411` (feat)
2. **Task 2: tests/__init__.py + tests/unit/__init__.py + test_cli.py** — `0f37c04` (test)
3. **Task 3: `uv tool install --editable .` + README append** — `45cbc42` (docs)

Task 4 (human-verify) is the blocking gate — no commit; awaits user signal "approved" in a fresh shell per `<how-to-verify>` in PLAN.md.

**Plan metadata** is committed on the planning branch alongside this SUMMARY.md; STATE.md / ROADMAP.md updates owned by the orchestrator post-merge (parallel_execution rules).

## Files Created/Modified

- `src/em_proj/cli.py` — wholesale-replaced (was 7-line placeholder; now 49-line typer scaffold). Contents per RESEARCH Pattern 1 verbatim: typer.Typer instance, `_version_callback`, `@app.callback()` with Annotated --version, mount-point comment, `if __name__ == "__main__"` guard.
- `tests/__init__.py` — created, 0 bytes
- `tests/unit/__init__.py` — created, 0 bytes
- `tests/unit/test_cli.py` — created, 32 lines. Module docstring + two test functions: `test_version` and `test_help`. Module-scoped `runner = CliRunner()`. Imports `__version__` from `em_proj` and `app` from `em_proj.cli`.
- `README.md` — appended "## Tool install" section after Bootstrap. Documents `uv tool install --editable .` command, the `--editable` requirement (per Pitfall #5), and the three verify commands (`command -v em-proj`, `em-proj --version`, `em-proj --help`).

## Verification Results

All Task 1, Task 2, Task 3 acceptance + verify commands green:

| Check | Command | Result |
|-------|---------|--------|
| Placeholder replaced | `grep -c NotImplementedError src/em_proj/cli.py` | 0 |
| no_args_is_help present | `grep -c 'no_args_is_help=True' src/em_proj/cli.py` | 1 |
| is_eager present | `grep -c 'is_eager=True' src/em_proj/cli.py` | 1 |
| Mount-point reserved | `grep -c 'add_typer(state_app' src/em_proj/cli.py` | 1 |
| __version__ imported | `grep -c 'from em_proj import __version__' src/em_proj/cli.py` | 1 |
| typer instance instantiable | `python -c "from em_proj.cli import app; assert isinstance(app, typer.Typer)"` | exit 0 |
| CliRunner --version | `python -c "...invoke(app, ['--version'])...assert exit_code==0 and 'em-proj 0.1.0' in stdout"` | exit 0 |
| CliRunner --help | `python -c "...invoke(app, ['--help'])...assert exit_code==0 and '--version' in stdout"` | exit 0 |
| tests/__init__.py is empty | `test ! -s tests/__init__.py` | exit 0 |
| tests/unit/__init__.py is empty | `test ! -s tests/unit/__init__.py` | exit 0 |
| Two test functions defined | `grep -cE '^def test_(version|help)\(' tests/unit/test_cli.py` | 2 |
| pytest collect-only succeeds | `uv run pytest tests/unit/test_cli.py --co -q` | 2 tests collected |
| **VALIDATION.md per-task verify** | `uv run pytest tests/unit/test_cli.py::test_version tests/unit/test_cli.py::test_help -x` | 2 passed in 0.06s |
| uv tool install exit 0 | `uv tool install --editable .` | exit 0; installed em-proj 0.1.0 |
| em-proj on PATH | `command -v em-proj` | `/Users/emonical/.local/bin/em-proj` |
| em-proj --version | `em-proj --version` | `em-proj 0.1.0` exit 0 |
| em-proj --help | `em-proj --help` | typer auto-help exit 0; contains `--version` |
| em-proj (no args) | `em-proj` | typer auto-help exit 0 (Click 8.4.0 + no_args_is_help=True) |
| README Tool install heading | `grep -c '^## Tool install' README.md` | 1 |
| README --editable documented | `grep -c 'uv tool install --editable \.' README.md` | 1 |

## Decisions Made

- **Wholesale replacement of cli.py via Write tool, not Edit:** plan instructs "rm + recreate — do NOT append" and the placeholder was 7 lines; using `Write` is the cleanest way to atomically replace the entire file. Verified after write that `grep -c NotImplementedError` returns 0.
- **`uv sync` inside the worktree before running .venv-dependent acceptance checks:** Plan 01's `.venv/` lives in the main repo root; the worktree starts with no `.venv/` because it's gitignored. The canonical fix is `uv sync` (which reproduces from the committed `uv.lock`), not copying the parent's `.venv/`. This produced a fresh `.venv/` inside the worktree that is also gitignored, so no commit-time effect.
- **The editable install link points at the worktree path:** `uv tool install --editable .` from `/Users/emonical/projects/personal/ai-tools/em-proj/.claude/worktrees/agent-a638b4d0bc6312bac/` installs `em-proj` with the editable link pointing at THAT path. When the worktree is cleaned up post-merge, the installed `em-proj` shim will break (the link will dangle). Documented in Deviations below; the user should re-run `uv tool install --editable .` from the main repo root after merge.
- **Did NOT add a test_no_args test:** Plan explicitly directs to skip it because the exit code (0 vs 2) varies with Click version, and the plan does NOT want to encode the wrong expectation. The smoke test in Task 3 verified exit 0 on Click 8.4.0; that's the only no-args check this plan ships.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Worktree had no .venv/ — ran `uv sync` to materialize one**

- **Found during:** Task 1 acceptance checks
- **Issue:** Plan Task 1 acceptance/verify commands use `.venv/bin/python -c "..."` to do in-process CliRunner verification. The worktree was freshly created and `.venv/` is gitignored (per Plan 01's `.gitignore`), so the worktree had no `.venv/` — the commands failed with `no such file or directory: .venv/bin/python`.
- **Fix:** Ran `uv sync` from the worktree root, which reproduced the `.venv/` deterministically from the committed `uv.lock` (em-proj editable + typer 0.25.1 + redis 7.4.0 + pytest 9.0.3 + transitive deps). Same pinned versions as Plan 01.
- **Files modified:** None tracked — `.venv/` is gitignored
- **Commit:** N/A (no source change)
- **Why this is a Rule 3 (blocking) deviation, not Rule 4 (architectural):** It's a missing dependency on the executor's environment; the fix is the canonical `uv sync` command that this project's `README.md` already documents in the Bootstrap section. No code changes, no new tools introduced.

**2. [Deviation note — NOT a Rule 1/2/3 fix; worktree-specific scope creep] Editable link points at the worktree path**

- **Found during:** Task 3 install
- **Issue:** `uv tool install --editable .` from the worktree root produces an `em-proj` shim whose editable source link points at `/Users/emonical/projects/personal/ai-tools/em-proj/.claude/worktrees/agent-a638b4d0bc6312bac/`, NOT the main repo root. When the orchestrator merges this worktree back to main and cleans up the agent worktree, the installed `em-proj` shim will dangle (no source to import from).
- **Fix decided:** None applied in this plan. Documented here for the user. The fix is a single command after merge: `cd /Users/emonical/projects/personal/ai-tools/em-proj && uv tool install --editable .` (this will overwrite the worktree-rooted install with one rooted at the canonical repo path).
- **Why not auto-fix:** I cannot reliably touch the main repo from inside the worktree without risking concurrent-Plan-03 conflicts; and the human-verify checkpoint (Task 4) is the right place to surface this so the user can do the post-merge re-install from a fresh shell when they re-test PATH.
- **Files modified:** None
- **Commit:** N/A
- **Surfaces to user via:** Task 4 human-verify checkpoint instructions + this Deviations section

### Plan instructions followed exactly

Everything else executed as written. The acceptance criteria for all three auto-tasks passed without modification. The plan's `<read_first>` block was honored: I read RESEARCH §Pattern 1 (Pattern 1 source for the cli.py snippet), CONTEXT D-04 / D-05 / D-06 (boundary decisions), VALIDATION.md (per-task verify command), and the placeholder cli.py (confirmed `NotImplementedError` was present before replacement).

## Known Stubs

None. Plan 01's stub `def app() -> None: raise NotImplementedError(...)` was the only stub in `cli.py` and Task 1 wholesale-replaced it with the real typer app. The Phase 2 mount-point comment is a documented reservation (NOT a stub — it carries no runtime code path).

## Issues Encountered

None blocking. The `.venv/`-missing situation (Rule 3 deviation #1) is the only mid-execution adjustment; it was a one-command fix (`uv sync`) and did not require any planner intervention.

## User Setup Required

**Yes — Task 4 human-verify checkpoint is the gate.** The plan requires the user to open a FRESH terminal (not the one running this executor, not the orchestrator's) and verify:

1. `command -v em-proj` → prints a path (expected: `/Users/emonical/.local/bin/em-proj`)
2. `em-proj --version` → prints `em-proj 0.1.0`, exits 0
3. `em-proj --help` → renders typer help including `--version` and "Personal tooling CLI under the em-proj namespace.", exits 0
4. `em-proj` (no args) → typer prints help, exits 0 (Click 8.4.0 + `no_args_is_help=True`; observed in this executor's smoke test)

If all four pass, the user types "approved" in the orchestrator. If `command -v em-proj` returns nothing in a fresh shell (PATH-shadowing), the user should run `uv tool update-shell` and restart the terminal — per the README "Tool install" section this plan added.

**Worktree caveat (documented in Deviations #2):** the currently-installed `em-proj` shim links to the worktree path. Post-merge, the user should re-run `cd /Users/emonical/projects/personal/ai-tools/em-proj && uv tool install --editable .` from the main repo root to point the shim at the canonical source location.

## Next Phase Readiness

**Plan 03 (Redis client wrapper) can complete in parallel** — it does not depend on Plan 02's deliverables (different files: `src/em_proj/redis_client.py`, `tests/unit/test_redis_client.py`, `scripts/verify-redis-config.sh`). Plan 03 is running alongside this executor in a separate worktree.

**Plan 04 (multi-process harness) depends on Plan 02** — Plan 04's `test_harness_self.py` will spawn `subprocess.Popen(["em-proj", "--version"], ...)` children, which requires:
- ✅ `em-proj` binary on PATH (Task 3 done; Task 4 fresh-shell verify pending)
- ✅ `em-proj --version` exit 0 with `em-proj 0.1.0` stdout (canonical "real binary" verb per D-06)
- ✅ typer dispatch scaffold (Task 1) — so Plan 04's harness can later race `em-proj state lock ...` against `em-proj state claim ...` in Phase 3+ without harness rewrite

No blockers for Plan 04 once Task 4 is approved AND Plan 03 lands.

## Threat Flags

None. This plan's surface — `--version` and `--help` from typer — has no new threat surface beyond what the threat model in PLAN.md already covers (T-01-02-01 argv parsing, T-01-02-02 stderr on import, T-01-02-03 uv tool install user-global mutation). No new network endpoints, no auth paths, no file access at trust boundaries, no schema changes.

## Self-Check: PASSED

Files created/modified verified to exist in worktree (`/Users/emonical/projects/personal/ai-tools/em-proj/.claude/worktrees/agent-a638b4d0bc6312bac/`):

- `src/em_proj/cli.py` — FOUND (43 lines; Annotated --version; no_args_is_help=True; mount-point comment)
- `tests/__init__.py` — FOUND (0 bytes)
- `tests/unit/__init__.py` — FOUND (0 bytes)
- `tests/unit/test_cli.py` — FOUND (32 lines; test_version + test_help; CliRunner)
- `README.md` — FOUND (modified with "## Tool install" section)

Commits exist on branch `worktree-agent-a638b4d0bc6312bac`:

- `885a411` — FOUND (Task 1: feat — cli.py replacement)
- `0f37c04` — FOUND (Task 2: test — tests/unit/test_cli.py + __init__.py files)
- `45cbc42` — FOUND (Task 3: docs — README "Tool install" section)

Task 4 is `checkpoint:human-verify` and has no commit (correct — it's the user-approval gate).

---

*Phase: 01-test-harness-redis-foundation*
*Completed (auto tasks): 2026-05-18*
*Awaiting: Task 4 human-verify in a fresh shell*
