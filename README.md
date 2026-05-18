# em-proj

Personal tooling CLI under the `em-proj` namespace. First deliverable: the `em-proj state` primitive for multi-session coordination.

See `.planning/PROJECT.md` for full project context.

## Bootstrap (developer install)

```bash
# One-time per machine
brew install redis uv          # if not already installed

# Project setup (from repo root)
uv sync                        # creates .venv/, installs runtime + dev deps
```

## Tool install (expose `em-proj` on PATH)

After `uv sync`, install the CLI as a uv-managed tool:

```bash
uv tool install --editable .
```

The `--editable` flag is required — without it, source edits don't propagate to the installed binary. Verify with:

```bash
command -v em-proj            # should resolve to uv tool shim
em-proj --version             # should print `em-proj 0.1.0`
em-proj --help                # should render typer auto-help
```

If `command -v em-proj` returns nothing, run `uv tool update-shell` and open a fresh terminal.
