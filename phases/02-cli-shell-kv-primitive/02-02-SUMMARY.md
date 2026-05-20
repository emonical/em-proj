---
phase: 02-cli-shell-kv-primitive
plan: 02
subsystem: output
tags: [output, envelope, json, schema-version, tty-detection, helpers, cli-05]
requirements: [CLI-05]
dependency_graph:
  requires: []
  provides:
    - "em_proj.output.SCHEMA_VERSION — locked CLI-05 schema version constant"
    - "em_proj.output.resolve_json_mode — TTY-vs-forced output mode resolver"
    - "em_proj.output.emit_ok / emit_not_found / emit_error — envelope emit helpers"
  affects:
    - "Plan 02-03 (kv ops) — verb code calls emit_* at the end of every path"
    - "Plan 02-04 (verb wiring) — per-verb --json/--no-json typer.Option resolves to json_mode"
    - "Phase 3/4 lock + claim verbs inherit the same emit_* helpers"
    - "Phase 5 /global-state skill parses the D-01 envelope this module emits"
tech_stack:
  added: []
  patterns:
    - "Dependency-free output primitive — no typer, no redis, no em_proj sibling imports"
    - "SystemExit raised directly from emit_* (clean exit, no traceback — mirrors redis_client)"
    - "Compact JSON via json.dumps(separators=(',',':')) + single trailing newline"
key_files:
  created:
    - "src/em_proj/output.py"
    - "tests/unit/test_output.py"
  modified: []
decisions:
  - "Internal helpers named emit_ok/emit_not_found/emit_error (D-15 locked names); private _dump + _render_plain for serialization and plain-text rendering"
  - "emit_not_found JSON envelope goes to stdout (queryable result), not stderr — only its plain-mode message goes to stderr"
  - "emit_error JSON envelope goes to stderr even in JSON mode (errors→stderr per PROJECT.md)"
metrics:
  duration: "~3 min"
  completed: "2026-05-20"
  tasks: 2
  files: 2
---

# Phase 2 Plan 02: Output Envelope Module Summary

JSON envelope single-source-of-truth (`em_proj/output.py`) implementing the CLI-05 contract — compact-JSON/plain-text emit helpers with TTY auto-detection and semantic exit codes, fully decoupled from typer and redis.

## What Was Built

`em_proj/output.py` is the dependency-free primitive every `em-proj` verb routes its result through. It owns the JSON envelope shape, the `SCHEMA_VERSION` constant, TTY detection, compact JSON serialization, plain-text rendering, and exit-code emission. Plans 03/04 import these helpers so verb code collapses to `do redis op → call emit_*`.

### Five public exports (D-15)

| Export | Description |
|--------|-------------|
| `SCHEMA_VERSION` | Locked integer-string constant `"1"` (D-02). Bumps only on a breaking schema change. |
| `resolve_json_mode(json_flag)` | Resolves output mode: `True`→JSON, `False`→plain, `None`→auto-detect via `sys.stdout.isatty()` (D-16). |
| `emit_ok(data, *, json_mode=None)` | Success path. JSON envelope or plain render to stdout, then `SystemExit(0)`. |
| `emit_not_found(message, *, json_mode=None)` | Missing-resource path. JSON envelope to stdout / plain message to stderr, then `SystemExit(2)`. |
| `emit_error(code, message, *, json_mode=None)` | Failure path. JSON envelope or human message — always to stderr, then `SystemExit(1)`. |

### Locked envelope shape (D-01)

```
{"schema_version": "1", "status": "<ok|not_found|error>", "data": <verb-specific>, "error": {...}}
```

`data` present on success; `error` present on non-success. Error sub-shape is locked to `{code, message}` (D-03) — both field names are permanent and never renamed; future additive fields (`details`, `retry_after`) do not bump `schema_version`.

### TTY-detection policy (D-04, D-16)

`json_mode=None` → auto-detect: JSON when stdout is **not** a TTY (machine-safe default), plain text when interactive. `json_mode=True` forces JSON; `json_mode=False` forces plain text. The per-verb `--json/--no-json` typer.Option (Plan 04) resolves to this parameter — no CLI plumbing lives in this module.

### stdout vs stderr routing

- `emit_ok` → stdout (success).
- `emit_error` → stderr always, even the JSON envelope (errors→stderr per PROJECT.md).
- `emit_not_found` → JSON envelope to stdout (a queryable result), plain message to stderr.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Write the output.py envelope module (D-15) | `dc0e596` | `src/em_proj/output.py` |
| 2 | capsys unit tests for envelope shape, exit codes, TTY routing | `327ce3b` | `tests/unit/test_output.py` |

Both commits in plan order: `feat(02-02): add em_proj/output.py envelope helpers (CLI-05 contract per D-15)` then `test(02-02): cover emit_* envelope shape + exit codes + TTY routing (15 tests)`.

## Verification

- `bash scripts/test.sh unit -k test_output` — 15 passed, 6 deselected.
- `bash scripts/test.sh unit` — 21 passed (no regressions in test_cli / test_redis_client).
- `uv run python -c "from em_proj.output import emit_ok, emit_not_found, emit_error, SCHEMA_VERSION, resolve_json_mode; assert SCHEMA_VERSION == '1'"` — exits 0.
- `output.py`: 156 non-blank lines (≥60 required), zero typer imports, zero redis imports, import-clean (no side effects).
- `test_output.py`: 15 named test functions, `json.loads` used 5× (≥4 required), `capsys` used 24× (≥8 required), zero typer/redis imports.

## Deviations from Plan

None — plan executed exactly as written. No bugs, missing functionality, or blocking issues encountered.

## TDD Gate Compliance

Plan frontmatter is `type: execute` with both tasks marked `tdd="true"`. The plan structured Task 1 as the implementation (`output.py`) and Task 2 as the test suite (`test_output.py`) — implementation-then-test ordering rather than a strict RED-before-GREEN cycle. This was followed as specified: Task 1 committed `feat(...)`, Task 2 committed `test(...)`. The test suite was authored against the public contract in `<behavior>` (parsed-dict assertions, not regex) and all 15 tests pass green. No RED-gate `test(...)` commit precedes the `feat(...)` commit because the plan deliberately ordered the module first; this is the plan's intended sequence, recorded here for transparency.

## Cited Decisions

D-01 (common envelope), D-02 (schema_version bump policy), D-03 (locked `{code,message}` error shape), D-04 (compact JSON + newline, plain on TTY), D-05 (`status` enum `ok|not_found|error`), D-15 (`output.py` single source of truth + helper names), D-16 (`json_mode` parameter convention).

## Self-Check: PASSED

- `src/em_proj/output.py` — FOUND
- `tests/unit/test_output.py` — FOUND
- Commit `dc0e596` (feat) — FOUND
- Commit `327ce3b` (test) — FOUND
