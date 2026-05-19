# em-proj — Claude Code conventions

## Test execution: use `scripts/test.sh`, not `uv run pytest`

All test invocations go through `scripts/test.sh`. The dispatcher exists so
Bash allowlists can pin exact-match per subcommand rather than granting
wildcard access to `uv`, `pytest`, or `python`. See the global
"Tool surface minimization" rule in `~/.claude/CLAUDE.md`.

Subcommands (run `bash scripts/test.sh help` for the live list):

| Subcommand        | What it runs                                                  |
|-------------------|---------------------------------------------------------------|
| `unit`            | `uv run pytest tests/unit -x`                                 |
| `multiprocess`    | `uv run pytest tests/multiprocess -v`                         |
| `harness`         | `uv run pytest tests/multiprocess/test_harness_self.py -v`    |
| `all`             | `uv run pytest -ra`                                           |
| `conftest-check`  | Structural sanity: import conftest, assert constants/fixtures |
| `collect`         | `uv run pytest --collect-only`                                |
| `help`            | Print usage                                                   |

### Output truncation: `--tail N`

If you need to truncate noisy output, pass `--tail N` to the dispatcher
instead of piping through `tail -N`. Pipes broaden the allowlist surface
and each ad-hoc pipe would otherwise need its own entry.

```bash
bash scripts/test.sh harness --tail 30
bash scripts/test.sh all --tail 50
```

`--tail` preserves the underlying pytest exit code via `PIPESTATUS[0]`, so
green/red status is unchanged.

### Pytest pass-through args

Extra args after the subcommand forward to pytest (filter patterns, marker
selection, etc.):

```bash
bash scripts/test.sh harness -k race
bash scripts/test.sh multiprocess --tail 40 -k isolation
```

## Other dispatcher scripts

| Script                              | Purpose                                            |
|-------------------------------------|----------------------------------------------------|
| `scripts/verify-redis-config.sh`    | Verify REDIS-01 brew settings + AOF presence       |
| `scripts/git-ro.sh`                 | Read-only git wrapper routed through `rtk git -C <path>` |
| `scripts/verify-phase.sh`           | Deterministic phase-verification dispatcher (test suite + anti-pattern grep + SUMMARY inventory + commit traceability) |

### `scripts/git-ro.sh` — read-only git inspection

Wraps `rtk git -C <path> <subcommand> [args...]` with a non-destructive
subcommand whitelist + per-subcommand destructive-flag guards (e.g. `branch
-d`, `worktree add`, `config <k> <v>`, `tag <name>` are all rejected). Run
`bash scripts/git-ro.sh help` for the full surface.

Use this for any git inspection of attached worktrees or the main repo — one
allowlist entry (`Bash(bash scripts/git-ro.sh *)`) covers all of them. Do NOT
fall back to raw `git -C <path>` for read operations; that requires a separate
allowlist entry per path.

For destructive ops (commit, push, rebase, reset, etc.), use raw git with
exact-match allowlist entries — never via this wrapper.

**TODO (future): globalize this script.** The wrapper is project-agnostic.
When a second project would benefit from it, lift to `~/.claude/scripts/git-ro.sh`
and allowlist globally via `Bash(bash ~/.claude/scripts/git-ro.sh *)`. The em-proj
copy can then become a thin pointer or be removed entirely. Memory:
`feedback-git-ro-global`.

### `scripts/verify-phase.sh` — phase verification dispatcher

`scripts/verify-phase.sh <phase-id>` runs the deterministic checks a GSD
phase verifier would otherwise have to run as separate Bash invocations:
test suite (`test.sh all` + `test.sh structural`), Redis backend check,
`em-proj` on PATH + `--version`, anti-pattern grep (TBD/FIXME/XXX/HACK/TODO/
PLACEHOLDER) on `src/ tests/ scripts/`, SUMMARY.md presence for every
PLAN.md in the phase directory, recent commit traceability. Emits a
structured markdown report to stdout.

A `gsd-verifier` subagent spawn should now reduce to: "run
`bash scripts/verify-phase.sh <id>`, read the output, apply judgment about
whether the phase goal is *delivered* (not just that checks pass), write
VERIFICATION.md with next-phase recommendations." One allowlisted call
replaces ~10–15 individual prompts for tests + greps + git inspections.

Exit codes: 0 = all pass, 1 = one or more checks fail (report shows which),
2 = bad input. Run `bash scripts/verify-phase.sh help` for the full surface.

**TODO (future): globalize alongside git-ro.sh.** Phase-verification is a
project-agnostic concept for GSD. After validating the pattern reduces
friction in Phase 2's verifier spawn, lift to `~/.claude/scripts/verify-phase.sh`.
Memory: `feedback-verify-phase-validate`.

## Structural tests (`tests/structural/`)

Pytest tests that use `ast` + source inspection to encode plan acceptance
criteria as runtime assertions. Replaces the dozens of per-criterion `grep`
/ `wc -l` / `test -s` invocations a plan's `<acceptance_criteria>` block
would otherwise require — one allowlisted dispatcher call (`bash scripts/
test.sh all` or `bash scripts/test.sh structural`) covers every structural
check.

New phases that produce significant code should add a
`tests/structural/test_<phase>_shape.py` file capturing the plan's named
structural criteria (file presence, symbol existence, fixture scopes, locked
design choices like "no `multiprocessing` import"). Use AST checks for code
properties; reserve source-text grep only for things outside Python (shell
scripts, markdown).

## Planning artifacts

`.planning/` is a worktree attached to the `planning` branch (orphan).
The `main` branch does NOT track `.planning/`. Do not attempt to commit
anything under `.planning/` from the main checkout — work from inside
`.planning/` if you need to commit planning artifacts. See the global
"Planning artifact storage" rule.

## Commit conventions

- Conventional Commits style (`feat(01-04): ...`, `test(01-04): ...`,
  `docs(01-02): ...`, etc.) where the parenthetical is `phase-plan`.
- **Never append `Co-Authored-By: Claude ...` trailers** — the user has
  opted out of model attribution at the commit level (global rule).
