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
