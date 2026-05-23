---
phase: 02-cli-shell-kv-primitive
plan: 04
wave: 3
status: complete
requirements: [KV-01, KV-02, CLI-03, CLI-04, CLI-05]
decisions: [D-09, D-10, D-11, D-12, D-13, D-14, D-15, D-16, D-18]
commits:
  - f3a0846 feat(02-04): wire get/set/del/list verbs into state_app
  - 26dfac9 test(02-04): CliRunner tests for state verbs (envelope + exit codes + TTL)
  - cb28141 test(02-04): multiproc race tests for KV-01 atomicity (set/set + set/del)
written_by: executor (worktree agent-a931e52b705cc23ee)
---

# Plan 02-04 SUMMARY — Wire KV verbs + verb-level tests + race atomicity proof

## What landed

`src/em_proj/state/__init__.py` (~173 lines, four `@state_app.command(…)` verbs):
the D-14 mount module + four thin translation layers — `get` / `set` / `del` /
`list`. Every verb resolves `json_mode`, pre-checks Redis via
`die_if_redis_unreachable` (D-18), calls exactly one `em_proj.state.kv` op, and
emits via exactly one `em_proj.output.emit_*` helper. No business logic in this
module; no `redis.ConnectionError` catches.

`tests/unit/test_state_verbs.py` (257 lines, 18 tests): end-to-end
`CliRunner`-driven coverage of every verb in both `--json` and TTY modes,
including:

- Happy-path envelope shape per verb (schema_version, status, data).
- D-10 `not_found` envelope + exit 2 for `state get <missing>`.
- D-11 idempotent `state del <missing>` exits 0 with `deleted=false`.
- D-13 `state list` empty returns `data == {"keys": []}`, exit 0.
- D-07 `state list` returns sorted, prefix-stripped keys.
- KV-02 `--ttl` echoed in the envelope; D-12 KEEPTTL preserved end-to-end via
  bare update.
- KV-02 success-criterion #3: TTL eviction proven end-to-end with `time.sleep(1.5)`.
- CLI-03 per-verb `--help` exits 0 and renders `--json` / `--no-json`; `set --help`
  also renders `--ttl`.
- D-09 validation: `state get 'foo bar' --json` exits 1 with the
  `validation_error` envelope on stderr.

`tests/multiprocess/test_kv_atomicity.py` (128 lines, 2 race tests): the first
Phase 2 consumer of the Phase 1 `multiproc_race` substrate.

- `test_concurrent_set_on_same_key_produces_single_winner` — two parallel
  `em-proj state set racekey <unique-600-char-payload> --json` invocations
  both exit 0; the post-state is exactly one of the two payloads (never a
  mix, never partial). Pins KV-01 atomicity at the Redis-server level.
- `test_concurrent_set_and_del_race_produces_consistent_state` — parallel
  `set`/`del` on the same key both exit 0; post-state is exactly `"alive"`
  OR absent. D-11 `rm -f` semantics never raise on an absent key.

## The four verbs

| Typer verb | Python function    | Backing `em_proj.state.kv` op | Edge-case decisions  |
| ---------- | ------------------ | ----------------------------- | -------------------- |
| `get`      | `get(key, json)`   | `kv_get(key)`                 | D-10 not_found→exit 2|
| `set`      | `set(key, value, ttl, json)` | `kv_set(key, value, ttl)` | KV-02 --ttl, D-12 KEEPTTL |
| `del`      | `delete_kv(key, json)` | `kv_del(key)`             | D-11 rm -f idempotent|
| `list`     | `list_keys(json)`  | `kv_list()`                   | D-07 strip+sort, D-13 empty-OK |

`del` is registered to typer as the string `"del"` (Python reserves the
keyword), with the underlying function named `delete_kv`. typer routes by the
decorator name string, not the function name, so `em-proj state del` resolves
correctly.

## `--help` surface (live stdout)

### `em-proj state --help`

```
 Usage: em-proj state [OPTIONS] COMMAND [ARGS]...

 KV / lock / claim primitives

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ get   Read a value from the kv namespace.                                    │
│ set   Write a value to the kv namespace.                                     │
│ del   Delete a value from the kv namespace.                                  │
│ list  List all keys in the kv namespace, alphabetically.                     │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### `em-proj state get --help`

```
 Usage: em-proj state get [OPTIONS] KEY

 Read a value from the kv namespace.

 Exits 2 if the key is not set (distinct from an empty-string value).

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│ *    key      TEXT  The kv key to read. [required]                           │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --json    --no-json      Force JSON or plain text output. Default:           │
│                          auto-detect from stdout TTY.                        │
│ --help                   Show this message and exit.                         │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### `em-proj state set --help`

```
 Usage: em-proj state set [OPTIONS] KEY VALUE

 Write a value to the kv namespace.

 With no --ttl on an existing key, preserves the existing TTL (KEEPTTL).

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│ *    key        TEXT  The kv key to write. [required]                        │
│ *    value      TEXT  The value to store. [required]                         │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --ttl                  INTEGER RANGE [x>=1]  Time to live in seconds.        │
│                                              Without --ttl, an existing      │
│                                              key's TTL is preserved          │
│                                              (KEEPTTL).                      │
│ --json    --no-json                          Force JSON or plain text        │
│                                              output. Default: auto-detect    │
│                                              from stdout TTY.                │
│ --help                                       Show this message and exit.     │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### `em-proj state del --help`

```
 Usage: em-proj state del [OPTIONS] KEY

 Delete a value from the kv namespace.

 Idempotent — exits 0 whether or not the key existed; the `deleted` boolean
 indicates which.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│ *    key      TEXT  The kv key to delete. [required]                         │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --json    --no-json      Force JSON or plain text output. Default:           │
│                          auto-detect from stdout TTY.                        │
│ --help                   Show this message and exit.                         │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### `em-proj state list --help`

```
 Usage: em-proj state list [OPTIONS]

 List all keys in the kv namespace, alphabetically.

 Excludes lock and claim namespaces.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --json    --no-json      Force JSON or plain text output. Default:           │
│                          auto-detect from stdout TTY.                        │
│ --help                   Show this message and exit.                         │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## D-18 confirmation — every verb pre-checks Redis

All four verb bodies begin with the locked two-line pattern:

```python
client = get_client()
die_if_redis_unreachable(client)
```

`grep -c "die_if_redis_unreachable(client)" src/em_proj/state/__init__.py`
returns **4** — one per verb. `grep -cE "except redis\.(ConnectionError|TimeoutError)"`
returns **0** — verbs never catch redis errors (D-18 single-chokepoint
preserved; the wrapper in `em_proj.redis_client` owns translation).

REDIS-02 user-facing surface (the one-line error when Redis is down) is exercised
end-to-end by the existing Phase 1 redis-client tests; the per-verb proof via a
monkeypatched ConnectionError lands in Plan 05 per D-19.

## Multiproc atomicity proof

Two race scenarios were tested; both pass under `bash scripts/test.sh multiprocess`:

| Race scenario | Legal post-states | Threat addressed |
| ------------- | ----------------- | ---------------- |
| `state set racekey valueA*100` ‖ `state set racekey valueB*100` | exactly `valueA*100` OR exactly `valueB*100` | T-2-04-06 (concurrent SET cannot produce a torn value) |
| `state set racekey alive` ‖ `state del racekey` | exactly `"alive"` OR absent | T-2-04-06 (set/del serialize at the server; del is rm -f) |

Both races spawn real `em-proj` fork+exec children with `EM_PROJ_REDIS_DB=15`
injected into the child env by `multiproc_race`. The post-state is read via a
fresh `redis.Redis(db=15)` client (NOT `em_proj.redis_client.get_client`) so
the test-process singleton can never mask a torn write.

This plan is the FIRST Phase-2 consumer of the Phase 1 `multiproc_race`
substrate — validates that the harness works for real KV ops, not just
`--version`.

## Decisions satisfied

| D-ID | How |
| ---- | --- |
| D-09 | `state get 'foo bar'` exits 1 with `validation_error` envelope (asserted in TTY-mode and JSON-mode tests). |
| D-10 | `state get <missing>` exits 2; envelope = `{status: "not_found", error: {code: "not_found", message: "key '<missing>' not set"}}`. |
| D-11 | `state del <missing>` exits 0; envelope `data == {"key": <missing>, "deleted": false}`. |
| D-12 | Bare `state set foo v2` after `state set foo v1 --ttl 60` keeps TTL ≥ 55s — asserted by reading raw redis TTL. |
| D-13 | `state list` on an empty db returns `data == {"keys": []}` exit 0 — empty list is a valid result. |
| D-14 | This module is pure dispatch — no business logic; four `@state_app.command(…)` decorations only. |
| D-15 | Every verb routes through `resolve_json_mode(json_flag)` then calls exactly one `emit_*` helper. |
| D-16 | Every verb exposes `--json/--no-json` typer.Option(None, …); default None → auto-detect via `sys.stdout.isatty()`. |
| D-18 | Every verb body begins with `get_client()` + `die_if_redis_unreachable(client)`; zero `redis.ConnectionError` catches. |

## Requirements

- **KV-01** — `em-proj state get|set|del|list` reachable end-to-end with atomic
  semantics inherited from Redis SET/GET/DEL/SCAN (proven via multiproc race).
- **KV-02** — `em-proj state set --ttl <seconds>` first-class; `--ttl` exposed in
  per-verb `--help`; KEEPTTL semantics on bare update; TTL eviction proven end-to-end.
- **CLI-03** — `--help` renders for every verb (`get`, `set`, `del`, `list`),
  including the `--json/--no-json` pair and `--ttl` on `set`.
- **CLI-04** — semantic exit codes: 0 success, 1 validation_error, 2 not_found,
  D-11 idempotent del exits 0 (code 3 reserved for Phase 3 lock contention).
- **CLI-05** — JSON envelope per D-01..D-05 emitted on `--json` or non-TTY
  stdout; verified by `test_list_json_empty_returns_empty_keys` (schema_version
  == "1") and the per-verb envelope-shape tests.

## Threats addressed

- **T-2-04-01** (command injection) — typer's argv parsing does not invoke a
  shell; multiproc tests use list[str] argv (no shell). The verb passes raw
  key through `validate_key` (D-09); value flows through the redis-py wire
  protocol untouched.
- **T-2-04-02** (info disclosure in errors) — verbs construct error messages
  containing only the user-typed key (already known to the user) and the
  locked regex spec / byte cap; no env values, no file paths, no Redis host.
- **T-2-04-03** (TTY hijack) — `resolve_json_mode(None)` returns
  `not sys.stdout.isatty()`; non-TTY → JSON (machine-safe default).
- **T-2-04-04** (verb-level redis.ConnectionError catch) — grep gate confirms
  zero catches. Verbs catch ONLY `KvNotFound` and `ValidationError`.
- **T-2-04-06** (multiprocess set-set race producing torn value) — asserted
  end-to-end by `test_concurrent_set_on_same_key_produces_single_winner`.

## Verification

- `bash scripts/test.sh unit -k test_state_verbs` — 18 passed.
- `bash scripts/test.sh multiprocess -k test_kv_atomicity` — 2 passed.
- `bash scripts/test.sh multiprocess` — 7 passed (no regressions).
- `bash scripts/test.sh all` — **106 passed** (no regressions in any prior
  test).
- `grep -c '@state_app.command(' src/em_proj/state/__init__.py` returns 4.
- `grep -c '"--json/--no-json"' src/em_proj/state/__init__.py` returns 4.
- `grep -c '"--ttl"' src/em_proj/state/__init__.py` returns 1.
- `grep -c "die_if_redis_unreachable(client)" src/em_proj/state/__init__.py`
  returns 4.
- `grep -cE "except redis\.(ConnectionError|TimeoutError)" src/em_proj/state/__init__.py`
  returns 0.

All test invocations went through `bash scripts/test.sh` — no raw `pytest` /
`uv run pytest`, no pipes.

## Deviations from plan

### [Rule 3 — Blocking] CliRunner(`mix_stderr=False`) is no longer accepted in click ≥ 8.2 / typer ≥ 0.25.1

- **Found during:** Task 2.
- **Issue:** The plan specifies `CliRunner(mix_stderr=False)` so stdout and
  stderr can be asserted independently. The pinned typer (0.25.1) brings in
  click 8.4, which removed the `mix_stderr` kwarg — separation is now the
  default — and instantiating `CliRunner(mix_stderr=False)` raises
  `TypeError: CliRunner.__init__() got an unexpected keyword argument 'mix_stderr'`.
- **Fix:** Wrapped the constructor in a `try/except TypeError` so the test
  file works on both click < 8.2 (passes the kwarg explicitly) and click ≥ 8.2
  (falls back to plain `CliRunner()` — separation is already the default).
  Documented inline so a future click downgrade does not silently regress the
  intent. The plan's grep-gate `mix_stderr=False` remains satisfied (the
  literal still appears once in the test source).
- **Files modified:** `tests/unit/test_state_verbs.py`.
- **Commit:** `26dfac9`.

### [Rule 3 — Blocking] Editable `em-proj` install pointed at a stale worktree

- **Found during:** pre-Task-3 smoke test (`em-proj --version` raised
  `ModuleNotFoundError: No module named 'em_proj'`).
- **Issue:** The globally-installed editable `em-proj` (via
  `uv tool install --editable .`) was pointing at a previous worktree path
  (`agent-ada4bed9731d24878`) that no longer exists. The Phase 1 VERIFICATION
  carry-forward already flagged this drift mode. Task 3 (multiproc race tests)
  spawns the real `em-proj` binary; with the binary broken, the tests would
  immediately fail to even collect — not for a logic bug but for an
  environmental one.
- **Fix:** Ran `uv tool install --editable . --force --reinstall` from this
  worktree so the binary on PATH again resolves to a live source tree.
- **Files modified:** none (out-of-tree editable install).
- **Commit:** none.

### [Rule 3 — minor] Removed an unused `import redis` from the verb test file

- Tests do not need a raw redis client (they use `clean_db` directly for the
  KEEPTTL TTL read); the unused import would have been a lint smell. Removed
  before the Task 2 commit. No behavior change.

## Future work

- **REDIS-02 user-facing UX test** — proves the one-line error message + exit
  1 when Redis is down, via a monkeypatched ConnectionError. Lands in Plan 05
  per D-19.
- **Structural test enforcing D-18 tree-wide** — Plan 05 will add a
  `tests/structural/test_phase_02_shape.py` that uses AST to assert no module
  outside `redis_client.py` catches `redis.ConnectionError` /
  `redis.TimeoutError`. The current grep gate is module-scoped (verb file
  only); the structural test will be tree-scoped.

## Self-Check: PASSED

- File `tests/unit/test_state_verbs.py` — FOUND.
- File `tests/multiprocess/test_kv_atomicity.py` — FOUND.
- File `.planning/phases/02-cli-shell-kv-primitive/02-04-SUMMARY.md` — FOUND.
- Commit `f3a0846` (Task 1 — wire verbs, already merged into main as part of
  `5924408`) — FOUND in `git log --all`.
- Commit `26dfac9` (Task 2 — verb tests) — FOUND on worktree branch.
- Commit `cb28141` (Task 3 — atomicity race tests) — FOUND on worktree branch.
- `src/em_proj/state/__init__.py` last touched by `f3a0846` (Task 1) — NOT
  modified by Task 2 / Task 3 (per the executor's scope contract).
- `bash scripts/test.sh all` — 106 passed, 0 failed.
