# Phase 2: CLI Shell + KV Primitive - Context

**Gathered:** 2026-05-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Land the `em-proj state` KV subcommand family — `get | set | del | list` with first-class TTL — plus the JSON envelope, exit-code, and TTY-detection conventions that every future subcommand (lock, claim, future session/message) inherits. Fully activate the REDIS-02 error-translation wrapper that Phase 1 built but only exercised via tests.

**Phase 2 requirements (per ROADMAP.md + REQUIREMENTS.md, after Phase 1's CLI-01/CLI-02 carry-forward):**
- **CLI-03** — `--help` for every subcommand and every verb (typer auto-help is sufficient when verbs have docstrings + typed signatures)
- **CLI-04** — semantic exit codes: `0` success / `1` error / `2` not-found / `3` held-by-another (KV does not use code 3; reserved for Phase 3 lock contention)
- **CLI-05** — machine-readable JSON when stdout is not a TTY OR when `--json` is passed; `schema_version` field; errors to stderr
- **KV-01** — `em-proj state get | set | del | list` with atomic write semantics (Redis `SET`/`GET`/`DEL`/`SCAN MATCH state:kv:*` provide atomicity at the server)
- **KV-02** — `em-proj state set --ttl <seconds>` first-class (Redis `SET ... EX <ttl>`)
- **REDIS-02** — every `em-proj state` verb surfaces the Phase 1 `die_if_redis_unreachable` one-line error when Redis is down; exit 1, no Python traceback

**Out of Phase 2 boundary (deferred to later phases):**
- LOCK-01/02/03 (locks) — Phase 3
- CLAIM-01/02/03 (claims) — Phase 4
- IDENT-01/02 (session-id, project-hash, stale-detection composite) — Phase 3
- SKILL-01/02/03 (`/global-state` skill) — Phase 5

</domain>

<decisions>
## Implementation Decisions

### JSON Envelope (CLI-05 contract)
- **D-01:** Common envelope for EVERY verb (not per-verb shapes): `{"schema_version": "1", "status": "<ok|not_found|error>", "data": <verb-specific>, "error": {"code": "<machine_code>", "message": "<human>"}}`. `data` is present on success; `error` is present on non-success. Both fields may be absent or null when not applicable.
- **D-02:** `schema_version` value format = integer string (`"1"`, `"2"`, ...). Bump only on breaking schema changes (rename or remove a field, or change a field's type). Adding optional fields does NOT bump.
- **D-03:** Error object minimal shape = `{code, message}` only. Add `details` (object) and `retry_after` (int seconds) later when a verb in Phase 3+ surfaces a need — additions are non-breaking and don't bump `schema_version`. The two locked field names (`code`, `message`) must never be renamed.
- **D-04:** JSON output format = compact (single-line `json.dumps(...)`) with trailing newline. NDJSON-compatible if a future verb streams multiple results. Plain text on TTY, JSON on non-TTY or `--json`.
- **D-05:** `status` enum values lock to `ok`, `not_found`, `error` for Phase 2. Phase 3 lock contention will add `held_by_another`. Adding new status values is non-breaking (consumers MUST ignore unknown values gracefully).

### Key Namespacing in Redis
- **D-06:** Redis key for `em-proj state set foo bar` = `state:kv:foo`. Two-segment prefix locks the convention for the verb family. Phases 3-4 write `state:lock:<name>` and `state:claim:<area>` respectively. Inside em-proj's data, scoping by verb family makes `state list` queries trivial (`SCAN MATCH state:kv:*`) without touching lock/claim data.
- **D-07:** `em-proj state list` strips the `state:kv:` prefix in output — user sees what they typed (`foo`), not the raw Redis key (`state:kv:foo`). Symmetric with `set`/`get` which accept the user-typed form. Prefix is implementation detail.
- **D-08:** `em-proj state list` scope = kv ONLY. Returns keys from `SCAN MATCH state:kv:*` only, not from `state:lock:*` or `state:claim:*`. Lock and claim listings get their own verbs in Phases 3/4 (`state locks --mine`, `state claims --active`). Symmetric with `set`/`get`/`del` which all operate only on the kv namespace.
- **D-09:** Key validation regex = `^[a-zA-Z0-9_.-/]+$`. Letters, digits, underscore, dot, dash, slash. Rejects whitespace, colons (would collide with the `state:kv:` prefix delimiter), shell metacharacters. Invalid keys → exit 1 + `{code: "validation_error", message: "key must match [a-zA-Z0-9_.-/]+"}`. Applies to all verbs accepting a key (Phase 2 = set/get/del; later phases inherit for lock/claim names too).

### KV Exit Codes + Edge Cases
- **D-10:** `em-proj state get <missing>` → exit 2 + error envelope `{code: "not_found", message: "key '<missing>' not set"}`. NOT exit 0 with empty stdout — distinguishes "missing key" from "value was empty string". Forces callers to handle the case explicitly.
- **D-11:** `em-proj state del <missing>` → exit 0, no error (idempotent). TTY: silent. `--json`: `{schema_version: "1", status: "ok", data: {deleted: false}}`. Asymmetric with `get missing` because `del` is `rm -f` semantics — caller wanted the key gone, key is gone, done. The boolean `deleted` field in JSON output communicates whether the key was actually present.
- **D-12:** `em-proj state set <key> <value>` on an existing key, with NO `--ttl` passed, MUST preserve the existing TTL via Redis `SET ... KEEPTTL`. User mental model: "I'm updating the value, not resetting the lifetime." Explicit `--ttl <N>` overrides. Explicit `--no-ttl` (if added later) would clear; for Phase 2 we ship only the implicit-keep behavior.
- **D-13:** `em-proj state list` with zero kv keys → exit 0, empty body on TTY; `--json` returns `{schema_version: "1", status: "ok", data: {keys: []}}`. Empty list is a valid result, not an error.

### Subcommand Mounting Structure
- **D-14:** Mount style = nested typer app. `em_proj/state/__init__.py` defines `state_app = typer.Typer(no_args_is_help=True)`; root `em_proj/cli.py` does `app.add_typer(state_app, name="state", help="KV / lock / claim primitives")`. Each verb is decorated `@state_app.command()`. Future `session`, `message` subcommand families slot in via the same pattern.
- **D-15:** Shared output module = `em_proj/output.py`. Owns: TTY detection (`sys.stdout.isatty()`), envelope construction, compact JSON dump + newline, plain-text rendering for TTY mode, `SCHEMA_VERSION = "1"` constant. Public helpers: `emit_ok(data: Any)` → exit 0; `emit_not_found(message: str)` → exit 2; `emit_error(code: str, message: str)` → exit 1. Single source of truth for the schema_version bump and the envelope shape.
- **D-16:** `--json` is a per-verb typer.Option flag (NOT a root-level flag threaded via Context). Default = `None` → fall through to `sys.stdout.isatty()` auto-detect. Passing `--json` forces JSON; passing `--no-json` (typer auto-generated) forces plain. Discoverable in every verb's `--help`.
- **D-17:** File layout = `em_proj/state/` package, NOT single-file. `em_proj/state/__init__.py` holds `state_app` + verb wiring (thin: parse args → call kv.py op → emit_ok/emit_not_found). `em_proj/state/kv.py` holds pure Python kv ops (`kv_get`, `kv_set`, `kv_del`, `kv_list`) — no typer imports, unit-testable in isolation, call `em_proj.redis_client.get_client()` for the Redis handle. Phase 3 adds `em_proj/state/lock.py`; Phase 4 adds `em_proj/state/claim.py` following the same shape.

### REDIS-02 Activation
- **D-18:** Every `state` verb path that touches Redis goes through `em_proj.redis_client.get_client()` + `die_if_redis_unreachable(client)` (Phase 1 wrapper). The wrapper translates `redis.ConnectionError`/`redis.TimeoutError` into a one-line stderr message ("Redis unreachable at 127.0.0.1:6379 — run `brew services start redis`") + exit 1. No verb is allowed to catch `redis.ConnectionError` itself.
- **D-19:** Test for REDIS-02 = a unit test that monkey-patches `redis_client.get_client` to raise `ConnectionError`, invokes a state verb via typer `CliRunner`, asserts exit 1 + the expected stderr line. NOT a multi-process test (single-process is sufficient since the failure mode is per-process). Structural test enforces that `state/__init__.py` and `state/kv.py` do not catch `redis.ConnectionError` directly (preserves D-18 single-chokepoint invariant — picks up Phase 1's D-19 carry-forward to Phase 2).

### Claude's Discretion
- The exact internal naming of helpers in `output.py` (e.g., `emit_ok` vs `print_success` vs `write_response`) — pick the clearest convention.
- Whether `kv_list` uses `SCAN` (cursor-based, safe for large keyspaces) or `KEYS` (single round trip, blocks Redis on huge keyspaces) — pick SCAN for forward-compat with a kv namespace that could grow, but the Phase 2 typical case is <100 keys so KEYS would be functionally indistinguishable. Researcher should make the call.
- Max value size cap — Redis itself caps at 512MB per value; em-proj should probably reject values >1MB with a `validation_error` (sanity guard against accidentally piping a binary into `state set`). Researcher to confirm a reasonable threshold.
- List ordering — Redis SCAN returns unordered. `state list` can sort alphabetically before emitting (predictable for diffing/scripting) or pass through SCAN order (slightly faster, unpredictable). Default to sorted-alphabetical unless researcher finds a compelling reason not to.
- typer auto-help formatting per verb — let typer's auto-generation handle CLI-03 in Phase 2; revisit if the output looks ugly when all the verbs land.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project context (locks scope, decisions, verified facts)
- `.planning/PROJECT.md` — Core value, M1 requirements, Constraints (esp. "machine-readable JSON when stdout not a TTY OR --json passed", "errors to stderr", "semantic exit codes 0/1/2/3"), Key Decisions (esp. backend = Redis, stack = Python/typer/redis-py, subcommand = `em-proj state`)
- `.planning/REQUIREMENTS.md` — All 24 v1 REQ-IDs. Phase 2 owns: CLI-03, CLI-04, CLI-05, KV-01, KV-02, REDIS-02. Note: CLI-01 and CLI-02 already delivered in Phase 1 (per `01-CONTEXT.md` D-04..D-06)
- `.planning/ROADMAP.md` §"### Phase 2" — Goal + 5 numbered Success Criteria. **MUST satisfy all 5.** Note: success criterion #1 mentions `em-proj --help` and `em-proj state --help` rendering typer-formatted help — Phase 2 needs to surface help for every verb under `state` too (CLI-03 expansion)
- `.planning/phases/01-test-harness-redis-foundation/01-CONTEXT.md` — D-07 (lazy Redis client), D-09 (Lua over MULTI/EXEC for atomicity; not needed in Phase 2 since SET is atomic, but the pattern lives for Phase 3+), D-17 (one-line error UX), D-19 (single chokepoint for Redis errors — carries forward as Phase 2 invariant)

### Phase 1 SUMMARY artifacts (concrete code Phase 2 builds on)
- `.planning/phases/01-test-harness-redis-foundation/01-03-SUMMARY.md` — `em_proj/redis_client.py` public surface: `get_client(db=None)`, `die_if_redis_unreachable(client)`, `_reset_for_tests()`. Phase 2 verbs call these directly.
- `.planning/phases/01-test-harness-redis-foundation/01-04-SUMMARY.md` — `tests/conftest.py` fixtures: `redis_precheck` (session), `clean_db` (function), `multiproc_race` (function), `RaceResult`, constants `TEST_DB=15`, `EM_PROJ_BIN="em-proj"`. KV multi-process tests reuse these.
- `.planning/phases/01-test-harness-redis-foundation/VERIFICATION.md` — Carry-forwards: re-run `uv tool install --editable .` from main repo root (was last installed from a worktree path); REQUIREMENTS.md traceability still maps CLI-01/02 to Phase 2 (informational — Phase 2 should NOT re-deliver them).

### External libraries (use Context7 / direct docs)
- **typer** (>=0.25.1, already pinned in `pyproject.toml`) — `add_typer(sub_app, name=, help=)` for nested subcommands; `typer.Option(None, help=)` for `--json/--no-json` auto-pair; `typer.testing.CliRunner` for CliRunner-based unit tests
- **redis-py** (>=7.4.0) — `client.set(key, value, ex=ttl, keepttl=True)` for SET KEEPTTL; `client.scan_iter(match="state:kv:*")` for prefix listing; atomic semantics inherited from Redis server (no client-side locking needed for KV)
- **Redis docs** — `SET` (https://redis.io/commands/set/), `KEEPTTL` flag semantics; `SCAN` vs `KEYS` tradeoffs; binary-safe values (Redis values are bytes, redis-py default decodes to str via `decode_responses=True`)

### Project conventions (already documented)
- `CLAUDE.md` (repo root) — Test dispatcher convention (`bash scripts/test.sh <sub>` only, never `uv run pytest` directly); `scripts/git-ro.sh` for read-only git; `scripts/verify-phase.sh` for phase verification; `tests/structural/` AST-based shape tests pattern; `Co-Authored-By: Claude` trailer rule (do NOT append)

### No external ADRs
No `.planning/adrs/`, no external spec docs referenced during discussion. All Phase 2 implementation decisions captured in `<decisions>` above.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets (Phase 1 outputs Phase 2 builds on)
- **`em_proj/redis_client.py`** (80 lines) — `get_client(db=None)` lazy module-level singleton; `die_if_redis_unreachable(client)` raises `_RedisUnreachable(SystemExit)` with a one-line stderr message + exit 1; `_reset_for_tests()` for unit-test isolation. **Phase 2 verbs MUST call through this — never instantiate `redis.Redis()` directly** (D-18 + structural test).
- **`em_proj/cli.py`** (45 lines) — typer `app` with `--version` Annotated callback (`is_eager=True`) + `--help` via typer. Phase 2 mounts `state_app` here via `app.add_typer(state_app, name="state", ...)`.
- **`em_proj/__main__.py`** — `python -m em_proj` entry for harness debugging.
- **`tests/conftest.py`** (160 lines) — `multiproc_race` / `clean_db` / `redis_precheck` fixtures + `RaceResult` + `TEST_DB=15` / `EM_PROJ_BIN="em-proj"` constants. KV multi-process tests (e.g., racing `state set` on the same key) reuse `multiproc_race`.
- **`tests/structural/test_conftest_shape.py`** — AST-based shape-enforcement pattern. Phase 2 adds a parallel `test_phase_02_shape.py` enforcing: D-18 single-chokepoint (no direct `redis.Redis()` outside `redis_client.py`), state package layout (D-17), `output.py` exports the documented helpers (D-15).

### Established Patterns
- **From PROJECT.md Constraints + Phase 1 CONTEXT.md** (carry forward):
  - CLI shape `em-proj <subcommand> <verb> [args...]` — nested typer apps preserve this (D-14)
  - Plain text on TTY; JSON when stdout not TTY OR `--json` — `em_proj/output.py` owns the decision per-call (D-15)
  - Errors to stderr; semantic exit codes 0/1/2/3 — `emit_*` helpers map cleanly to these (D-15)
  - Avoid `ls | while read` patterns in shell scripts
  - Communication style: concise, opinionated; no vendor-tradeoff matrices unless asked
- **From repo CLAUDE.md** (project-wide):
  - All test invocations via `bash scripts/test.sh <sub>` — Phase 2 may need to add subcommands (e.g., `state-unit` for `tests/unit/state/`) if tests grow; commit the script extension separately as `chore:`
  - Conventional commits with `feat(02-NN):` / `test(02-NN):` / `chore:` prefix; NO `Co-Authored-By: Claude` trailer
  - tests/structural/ pattern continues — add `test_phase_02_shape.py` for the new module shape

### Integration Points
- **Phase 1 → Phase 2:**
  - `em_proj/redis_client.py` → every kv op in `em_proj/state/kv.py` (D-18)
  - `em_proj/cli.py app` → `app.add_typer(state_app, name="state")` in `cli.py` (D-14)
  - `tests/conftest.py` fixtures → reused by `tests/multiprocess/test_kv_*.py` for racing
- **Phase 2 → Phase 3:** `em_proj/output.py` `emit_*` helpers are inherited; `em_proj/state/__init__.py` `state_app` gets new verbs (`lock`/`unlock`/`--hold`); `em_proj/state/lock.py` lands as a sibling to `kv.py`; key namespace `state:lock:*` follows the same convention as `state:kv:*`
- **Phase 2 → Phase 4:** Same pattern — `em_proj/state/claim.py` joins; `state:claim:*` namespace; claim verbs reuse `emit_*` helpers; key validation regex (D-09) applies to claim area names too
- **Phase 2 → Phase 5:** `/global-state` skill consumes the JSON envelope from D-01..D-05 — every status enum + error code we emit becomes part of the skill's parsing contract

</code_context>

<specifics>
## Specific Ideas

- **Common envelope over per-verb shape** — chosen specifically because the user will write a `/global-state` skill (Phase 5) and a `gsd-sdk workstream.set` consumer (Phase 6) that parse output across multiple verbs. A common envelope means one parser handles every verb across every subcommand family forever.
- **`state:kv:` two-segment prefix** — specifically chosen over `em-proj:state:kv:` to keep keys compact while still allowing `SCAN MATCH state:kv:*` to cleanly separate from `state:lock:*` / `state:claim:*` in Phases 3-4. The user already plans future em-proj subcommands (`session`, `message`); those will use `session:*` / `message:*` as top-level prefixes (no `em-proj:` umbrella).
- **KEEPTTL on bare `set`** — Redis-specific flag (added in Redis 6.0); user explicitly preferred preserving lifetime over Redis default reset behavior. This is the "mental model fix" for an otherwise surprising Redis API.
- **Per-verb `--json` flag with TTY auto-detect default** — chosen over root-level flag because em-proj invocations are one-shot (one verb per `em-proj ...` call), so root-level threading is theoretical complexity. Per-verb flag also shows up in every `--help` for discoverability (CLI-03).
- **Adding `details`/`retry_after` to error envelope LATER (not now)** — explicit hypothesis: "field additions are free; renames are expensive." Adding fields when a real consumer (Phase 3 lock verb hitting `held_by_another`) needs them is non-breaking.

</specifics>

<deferred>
## Deferred Ideas

- **Pretty-printed JSON output (`--pretty` flag)** — proposed as Area 1 Option C, rejected. Compact-only is sufficient; if eyeball-debugging becomes a pain, pipe through `jq .`. Add the flag later if it gets brought up.
- **`em-proj:` umbrella prefix on Redis keys** — proposed as Area 2 Option B, rejected. The 9 extra bytes per key buys defensiveness against multi-tenant Redis we don't have. Reconsider if em-proj ever shares Redis with a separate non-em-proj tool.
- **Raw key view in `state list`** — proposed as Area 2 Option C, rejected for Phase 2. If raw-key debugging becomes useful, add `--raw` flag later.
- **`--include locks,claims` flag on `state list`** — proposed as Area 2, rejected in favor of dedicated `state locks` / `state claims` verbs in Phases 3/4.
- **`--strict` flag on `state del missing`** — proposed as Area 3, rejected for Phase 2. If strict-delete becomes useful, add later.
- **Error object `details` and `retry_after` fields** — pre-declared shape proposed (Area 1 Option B), rejected in favor of additive evolution. Add when Phase 3+ needs them.
- **Documented-but-not-enforced extension convention** (Area 1 Option C) — rejected; the convention is implicit in the schema_version contract (consumers MUST ignore unknown keys), no need to inflate the docs.
- **Root-level `--json` flag** — proposed as Area 4 Option B, rejected. Per-verb flag preserves discoverability via per-verb `--help`.
- **Single-file `em_proj/state.py`** — proposed as Area 4 Option B, rejected. Package layout (`em_proj/state/`) scales cleanly to Phases 3+ when lock/claim land.
- **Verb-per-file under `em_proj/state/`** — proposed as Area 4 Option C, rejected as over-granular.

**No scope creep raised during discussion.** All decisions stayed inside the phase boundary (CLI-03/04/05 + KV-01/02 + REDIS-02 only).

</deferred>

---

*Phase: 2-CLI Shell + KV Primitive*
*Context gathered: 2026-05-19*
