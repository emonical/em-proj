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

After Plan 02 lands, the `uv tool install --editable .` step is documented here.
