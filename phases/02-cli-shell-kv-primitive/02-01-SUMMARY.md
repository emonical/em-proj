---
phase: 02-cli-shell-kv-primitive
plan: 01
subsystem: cli
tags: [carry-forward, scaffold, typer, mount, cli-help, nested-typer]

# Dependency graph
requires:
  - phase: 01-test-harness-redis-foundation/02
    provides: src/em_proj/cli.py — typer root app with --version Annotated callback + --help; pyproject.toml [project.scripts] em-proj entrypoint
  - phase: 01-test-harness-redis-foundation/03
    provides: src/em_proj/redis_client.py — get_client + die_if_redis_unreachable (not consumed by this plan; Plans 03/04 wire it through state/kv.py)
provides:
  - src/em_proj/state/__init__.py — empty state_app Typer instance (name="state", help="KV / lock / claim primitives", no_args_is_help=True, add_completion=False); the D-14 mount target Plans 03/04 attach verbs to via @state_app.command()
  - src/em_proj/cli.py — app.add_typer(state_app, name="state", ...) wiring; the Phase 2 placeholder mount-point comment is now a live mount
  - tests/unit/test_state_mount.py — 3 CliRunner tests verifying the empty-mount surface (state --help exits 0, contains the D-14 mount string, no-verb invocation prints help without crashing)
affects: [02-03 (kv ops module em_proj/state/kv.py lands as sibling of __init__.py), 02-04 (get/set/del/list verbs decorate @state_app.command() — no further cli.py edits needed), 03-* (lock.py sibling + lock/unlock verbs reuse the same mount), 04-* (claim.py sibling + claim/release/check verbs reuse the same mount)]

# Tech tracking
tech-stack:
  added: []  # no new deps; typer was already pinned in pyproject.toml since Phase 1
  patterns:
    - "Nested typer app (D-14) — sub-app defined in its own package __init__.py, mounted on the root app via app.add_typer(sub_app, name=, help=). Future session/message subcommand families slot in the same way."
    - "Empty-mount-first scaffold — a Typer sub-app with zero registered_commands is a valid intermediate state; no_args_is_help=True handles the empty-invocation case, and later plans attach verbs without touching cli.py again."

key-files:
  created:
    - src/em_proj/state/__init__.py
    - tests/unit/test_state_mount.py
  modified:
    - src/em_proj/cli.py

key-decisions:
  - "D-14 mount realized exactly as specified — state_app lives in em_proj/state/__init__.py (package, not single-file per D-17), mounted under the root app as `em-proj state`. The mount is intentionally empty (registered_commands == []) pending Plan 04's verbs."
  - "Editable reinstall ran from the worktree path, not the literal main repo root. The orchestrator spawns parallel executors inside a git worktree; `uv tool install --editable .` resolves CWD = the worktree. This is correct for in-wave verification (the worktree IS the live source tree). The orchestrator re-runs the install from the main repo root after merge — see Deviations."
  - "Task 1 produced no source changes (verification-only) so it has no standalone commit. The editable-install side-effect is documented here rather than committed as a no-op `chore`, per the plan's <output> guidance ('if no commit makes sense, fold into the doc commit')."
  - "Task 3 (tdd=true) RED and GREEN collapse — Task 2 already landed the mount the test verifies, so test_state_mount.py passes on first run. This is the plan's intended structure: Task 3's <done> describes verification of an already-implemented mount with no implementation step. Committed as a single test() commit."

requirements-completed: [CLI-01, CLI-02, CLI-03]
# CLI-01/CLI-02 are carry-forward re-verifications from Phase 1 (per 01-CONTEXT.md D-04..D-06);
# CLI-03 is PARTIALLY satisfied (state-subcommand-level --help). Plan 04's per-verb --help completes CLI-03.

# Metrics
duration: 2min
completed: 2026-05-20
---

# Phase 02 Plan 01: Carry-forward verification + empty state_app mount Summary

**Re-verified the Phase 1 CLI carry-forward (em-proj installable + --version + --help, all prior unit tests green) and landed the D-14 nested-typer mount: an empty `state_app` Typer instance mounted under the root app as `em-proj state`, ready for Plans 03/04 to attach KV verbs without further cli.py edits.**

## Performance

- **Duration:** ~2 min (90s wall-clock)
- **Started:** 2026-05-20T15:53:33Z
- **Completed:** 2026-05-20T15:55:03Z
- **Tasks completed:** 3 of 3

## Accomplishments

### Task 1 — Phase 1 carry-forward re-verified (CLI-01, CLI-02)

All five acceptance checks ran as separate Bash invocations (no chains, no pipes per repo CLAUDE.md):

1. `uv tool install --editable .` — exit 0; rebuilt and reinstalled the `em-proj` binary.
2. `command -v em-proj` — exit 0; resolves to `/Users/emonical/.local/bin/em-proj`.
3. `em-proj --version` — exit 0; stdout = `em-proj 0.1.0`.
4. `em-proj --help` — exit 0; renders typer auto-help including `--version`.
5. `bash scripts/test.sh unit` — exit 0; all 6 prior unit tests pass (`test_cli.py` ×2, `test_redis_client.py` ×4).

No source files modified in Task 1 (verification-only). CLI-01 (installable + on PATH) and CLI-02 (typer dispatch + `--version` + `--help`) both still hold.

### Task 2 — Empty state_app mount (D-14)

- **`src/em_proj/state/__init__.py`** (CREATED) — defines `state_app = typer.Typer(name="state", help="KV / lock / claim primitives", no_args_is_help=True, add_completion=False)`. Module docstring documents the D-14 pattern and how Plans 03/04 attach verbs.
- **`src/em_proj/cli.py`** (MODIFIED) — added `from em_proj.state import state_app` next to the existing imports; replaced the 4-line Phase 2 placeholder comment block with a live `app.add_typer(state_app, name="state", help="KV / lock / claim primitives")`. The `--version` callback and `if __name__ == "__main__"` guard are untouched.
- No `@state_app.command()` verbs added — the mount is intentionally empty (`state_app.registered_commands == []`), Plan 04's territory.

### Task 3 — CliRunner tests for the state mount (CLI-03 partial)

- **`tests/unit/test_state_mount.py`** (CREATED) — 3 deterministic in-process tests, no Redis dependency, run in 0.06s:
  - `test_state_mount_help_exits_zero` — `em-proj state --help` exits 0.
  - `test_state_help_mentions_mount_string` — stdout contains the literal D-14 mount string `KV / lock / claim primitives`.
  - `test_state_no_args_shows_help` — `em-proj state` with no verb prints help with no unhandled exception; exit code in `(0, 2)` (typer's `no_args_is_help` version-tolerant).

## Task Commits

Each task committed atomically (no Co-Authored-By trailer per project policy):

1. **Task 1: carry-forward verification** — no standalone commit (verification-only, no source changes; editable-install side-effect documented in this SUMMARY per plan `<output>` guidance).
2. **Task 2: state_app mount** — `632ff37` (feat) — `feat(02-01): mount empty state_app under root typer app (D-14)`
3. **Task 3: state mount tests** — `b02cfe0` (test) — `test(02-01): add CliRunner tests for state mount help (CLI-03 partial)`

## Files Created/Modified

- `src/em_proj/state/__init__.py` (CREATED, 22 lines)
- `tests/unit/test_state_mount.py` (CREATED, 47 lines)
- `src/em_proj/cli.py` (MODIFIED — +1 import line, placeholder comment block → live `add_typer` call)

## Verification Results

All `<verification>` checks from the plan pass:

- `command -v em-proj` → `/Users/emonical/.local/bin/em-proj` (exit 0)
- `em-proj --version` → `em-proj 0.1.0` (exit 0)
- `em-proj --help` → typer auto-help (exit 0)
- `bash scripts/test.sh unit -k test_state_mount` → 3 passed, exit 0
- `bash scripts/test.sh unit` → 9 passed (6 prior + 3 new), exit 0
- `grep -c "add_typer(state_app" src/em_proj/cli.py` → exactly 1 match

All four `<success_criteria>` met: (1) carry-forward verified, (2) `state` mount landed per D-14, (3) `em-proj state --help` renders typer auto-help — CLI-03 partial, (4) no Redis dependency introduced.

## Decisions Made

- **`state_app` lives in a package `__init__.py`** (`em_proj/state/__init__.py`), not a single-file module — per D-17, so Plans 03/04 can add `kv.py` (and Phases 3/4 `lock.py`/`claim.py`) as siblings.
- **The mount is intentionally empty.** `state_app.registered_commands == []` is a valid intermediate state; `no_args_is_help=True` handles the no-verb invocation. Plan 04 attaches `get/set/del/list` by decorating `@state_app.command()` — no further `cli.py` edits required.
- **Task 1 produced no commit.** It is verification-only; the `uv tool install` side-effect is documented here rather than committed as a no-op `chore`, per the plan's `<output>` instruction.

## Deviations from Plan

### Auto-fixed / Documented Adaptations

**1. [Rule 3 - Environment] Editable install ran from the worktree path, not the literal "repo root"**

- **Found during:** Task 1, step 1 (`uv tool install --editable .`).
- **Issue:** The plan's `<action>` says to run the install "from the repo root (`/Users/emonical/projects/personal/ai-tools/em-proj`)". This executor runs as a parallel worktree agent; its CWD is `/Users/emonical/projects/personal/ai-tools/em-proj/.claude/worktrees/agent-ada4bed9731d24878`. `uv tool install --editable .` resolves `.` to the worktree, so the installed binary points at the worktree source tree, not `main`.
- **Resolution:** No fix attempted — this is correct in-wave behavior. The worktree IS the live source tree for this wave's changes; the install correctly imports the freshly-mounted `state_app`. The plan's intent (prove the editable install works against a live source tree, addressing the Phase 1 VERIFICATION.md carry-forward where the prior install pointed at a stale worktree) is satisfied. The GSD orchestrator re-runs `uv tool install --editable .` from the main repo root after merging this wave, which finalizes the carry-forward against `main`.
- **Files modified:** none.
- **Commit:** n/a (Task 1 is verification-only).

---

**Total deviations:** 1 documented environment adaptation (no code change, no fix needed).
**Impact:** None on deliverables. The carry-forward is verified at the worktree level; the orchestrator's post-merge reinstall completes it against `main`.

## Issues Encountered

- **`.venv/` materialized on first `bash scripts/test.sh unit` call** — `uv run` created the worktree's virtual environment (CPython 3.12.12, 14 packages in 12ms). Expected; not a blocker.

## TDD Gate Compliance

Task 3 is `tdd="true"`. Because Task 2 lands the mount that Task 3's tests verify, the RED and GREEN phases collapse — `test_state_mount.py` passes on first run against the already-mounted `state_app`. This is the plan's intended structure (Task 3's `<done>` describes verification of an already-implemented mount, with no implementation sub-step). The tests are committed as a single `test(02-01)` commit. No standalone failing-test commit was created because there was nothing left to implement — the fail-fast rule (a test passing during RED signals the feature already exists) is satisfied by design here, not by accident.

## Threat Surface Audit

| Threat ID | Status | Notes |
|-----------|--------|-------|
| T-2-01-01 (stale editable install root) | mitigated (in-wave) | Task 1 re-ran `uv tool install --editable .`; resolves to a live source tree. Orchestrator finalizes against `main` post-merge. |
| T-2-01-02 (`--version` info disclosure) | accept | Output is the package literal `em-proj 0.1.0`; no env/path leakage. |
| T-2-01-03 (empty state_app DoS) | accept | `registered_commands == []` is intentional; `no_args_is_help=True` handles the empty invocation cleanly (verified by `test_state_no_args_shows_help`). |

No new threat surface introduced beyond the plan's `<threat_model>` — no network endpoints, no auth paths, no file access, no schema changes.

## Next Plan Readiness

**Plan 02-03 (KV ops module) is unblocked:**
- `em_proj/state/` package exists; `kv.py` lands as a sibling of `__init__.py` (D-17).
- `state_app` is mounted and importable as `from em_proj.state import state_app`.

**Plan 02-04 (KV verbs) is unblocked:**
- Verbs attach via `@state_app.command()` in `em_proj/state/__init__.py` — `cli.py` needs no further edits.
- Per-verb `--help` from Plan 04 completes CLI-03 (this plan delivered the subcommand-level `--help`).

**Phases 3 / 4:** `lock.py` and `claim.py` join `em_proj/state/` as siblings; their verbs mount on the same `state_app` via the established D-14 pattern.

## Self-Check: PASSED

- `src/em_proj/state/__init__.py` — FOUND
- `tests/unit/test_state_mount.py` — FOUND
- Commit `632ff37` — FOUND
- Commit `b02cfe0` — FOUND

---
*Phase: 02-cli-shell-kv-primitive*
*Completed: 2026-05-20*
