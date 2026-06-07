# Phase 3: Identity + Advisory Locks - Context

**Gathered:** 2026-05-23
**Status:** Ready for planning

<domain>
## Phase Boundary

Land session/project identity resolution, the cross-platform stale-detection composite, and short-lived `state lock | unlock | lock --hold -- <cmd>` advisory locks — process-scoped, atomic, multi-process race-tested. First phase that writes to the `state:lock:*` namespace alongside Phase 2's `state:kv:*`.

**Phase 3 requirements (per ROADMAP.md + REQUIREMENTS.md):**
- **IDENT-01** — resolve `session_id` from `CLAUDE_CODE_SESSION_ID` (documented fallback chain); derive `project_hash` from `$PWD` via `tr '/' '-'` on the absolute path (git-toplevel fallback), exactly matching the `~/.claude/projects/<hash>/` convention
- **IDENT-02** — stale-detection composite `{pid, proc_start_epoch, boot_id}` with TTL as final backstop
- **LOCK-01** — `em-proj state lock <name>` and `unlock <name>` (process-scoped, atomic via `SET NX EX` + Lua compare-and-delete)
- **LOCK-02** — `lock` blocks with 1-second timeout by default; `--warn` opts into the human-override path
- **LOCK-03** — `em-proj state lock --hold <name> -- <cmd...>` auto-acquires, runs `<cmd>`, releases on exit (including signal or crash); verified by multi-process harness

**Out of Phase 3 boundary (deferred to later phases):**
- CLAIM-01/02/03 (long-lived claims with `reason` / refreshable TTL / anonymous-refusal) — Phase 4. Phase 3's lock-value schema (the JSON record below) is the wire-format precedent claims will mirror.
- SKILL-01/02/03 (`/global-state` skill: `locks [--mine|--stale]`, `unlock --force`) — Phase 5. The opportunistic stale-takeover on `lock` acquire is Phase 3's surface; the skill is the visibility + force-cleanup layer on top.

</domain>

<decisions>
## Implementation Decisions

### Lock Value Schema (the wire format)
- **D-01:** Redis stores a single key per lock under `state:lock:<name>`; the value is a compact JSON blob (no two-key split, no pipe-delimited token). Atomic acquire via `SET NX EX <ttl>` with the JSON as the value. Lua scripts use `cjson.decode` for stale-check and ownership-compare. Symmetric with Phase 4 claims — same encoding pattern, one parser for the Phase 5 skill and Phase 6 consumer forever.
- **D-02:** Lock JSON record (FULL holder shape — claims will mirror it in Phase 4):
  ```json
  {
    "pid": 12345,
    "proc_start_epoch": 1716480000.0,
    "boot_id": "<hash of sysctl kern.boottime>",
    "session_id": "<CLAUDE_CODE_SESSION_ID or fallback>",
    "project_hash": "<tr / - on $PWD>",
    "reason": null,
    "acquired_at": 1716480010.5,
    "expires_at": 1716480070.5
  }
  ```
  `reason` is nullable; populated by an optional `--reason '<text>'` flag at `lock` time. Including `session_id`/`project_hash` supports Phase 5 skill filtering (`locks --mine`, `locks --project`) without a separate session-registry lookup (which doesn't exist in M1).
- **D-03:** Time fields (`acquired_at`, `expires_at`, `proc_start_epoch`) are **Unix epoch floats**, not ISO 8601 strings. Direct arithmetic for age/remaining; trivial Lua interop (`redis.call('TIME')` returns epoch + microseconds as numbers); language-neutral. The Phase 5 skill formats for humans at display time. Adding a sibling `*_iso` field later is non-breaking but not done now.

### Lock TTL + `--hold` Refresh
- **D-04:** Default lock TTL = **60 seconds**, with `--ttl <N>` override on `lock` (range to be finalized by planner; sketch is 1–3600s). `state lock <name>` issues `SET NX EX 60` by default.
- **D-05:** `lock --hold -- <cmd>` spawns a tiny daemon refresher thread that runs `EXPIRE state:lock:<name> <ttl>` every `ttl/3` seconds while the wrapped subprocess is alive. Refresher stops on subprocess exit (normal exit, signal, or crash). If the python parent itself crashes mid-`--hold`, the TTL takes over as the final backstop (per PROJECT.md "TTL is the final backstop" wording).
- **D-06:** Lock release on `--hold` exit goes through a Lua compare-and-delete script (read JSON value, verify `pid` + `proc_start_epoch` match, then `DEL`). If the value has changed (e.g., `--warn` override displaced us), the script no-ops — we don't delete someone else's lock just because we entered with one. atexit + signal handlers (SIGINT, SIGTERM) trigger the release path.

### `--warn` Semantics (the human-override path)
- **D-07:** `--warn` is **TTY-gated**. Flow: try `SET NX EX` first. On collision, block 1 second (same as default). If still held after 1s:
  - **On a TTY:** print `Lock <name> held by session <id> (pid N, age Xs). Override? [y/N]` to stderr, read stdin. `y`/`Y` → atomically displace (Lua: DEL + SET NX with new holder JSON) and emit a stderr warning that an override happened. Anything else → exit 3 (`held_by_another`).
  - **On a non-TTY** (stdout or stdin not a TTY): refuse with exit 3 + stderr message `warn-mode requires a TTY for confirmation`. Non-TTY `--warn` is itself a misuse signal — scripts that want to override programmatically should use the Phase 5 skill's `unlock --force`, not `--warn`.
- **D-08:** `--warn` + `--hold` are **mutually exclusive** at typer parse time. The semantics conflict (`--hold` says "I auto-manage everything", `--warn` says "I want a manual confirmation"). Combining them errors with exit 1 before any Redis call.
- **D-09:** When a `--warn` override displaces an existing holder, the displaced holder's next `unlock <name>` returns **exit 3 `held_by_another`** with a stderr message naming that the lock was overridden. (Exact wording — whether to record the displacer's session_id in the lock value or just say "held by another" — is planner discretion; the principle is the holder learns they were displaced.) NOT exit 0 silent (would hide the racy displacement).

### Stale-Detection Composite + Probe Trigger
- **D-10:** Stale-detection is **opportunistic on acquire**, with Phase 5 skill visibility layered on top. Flow when `lock <name>` finds the key held:
  1. Read holder JSON. Run liveness probe in Python (NOT in Lua — needs OS calls):
     - `os.kill(pid, 0)` — does the PID exist? (ESRCH → stale via "dead PID")
     - `psutil.Process(pid).create_time()` — does it match `proc_start_epoch`? (mismatch → stale via "PID reuse")
     - Hash of `psutil.boot_time()` — does it match `boot_id`? (mismatch → stale via "reboot wiped the holder")
  2. If ANY probe says stale: run Lua compare-and-swap (`GET` → verify value still matches the stale holder JSON → `DEL` + `SET NX` with new holder). The compare-and-swap protects against a live process taking over between our probe and our write.
  3. If live: continue with the normal block (1s default) or `--warn` flow.
- **D-11:** **Add `psutil >= 6.0` to runtime dependencies.** Updates PROJECT.md's allowed-deps list from `redis-py, typer, pytest` to `redis-py, typer, psutil, pytest`. Rationale: `psutil.Process(pid).create_time()` and `psutil.boot_time()` give portable, battle-tested cross-platform probes. Hand-parsing `ps -o lstart=` + `sysctl kern.boottime` is fragile across macOS minor versions and slow under contention (subprocess spawn per probe). IDENT-02 is load-bearing for every lock and every Phase 4 claim — getting the probe right matters more than the dep count.

### Carried Forward from Prior Phases (locked, not re-discussed)
- **Atomicity via Lua scripts** (`EVAL`/`EVALSHA`), not `WATCH`/`MULTI`/`EXEC` — Phase 1 D-09. Applies to opportunistic stale-takeover (D-10), compare-and-delete on unlock (D-06), `--warn` override (D-07).
- **All Redis access via `em_proj.redis_client.get_client()` + `die_if_redis_unreachable()`** — Phase 2 D-18. No direct `redis.Redis()` in `state/lock.py` or `state/identity.py`. Structural test enforces.
- **Key prefix `state:lock:`** — Phase 2 D-06. Phase 5 skill `state list` already scopes to `state:kv:*` only (Phase 2 D-08); `state locks` verb in Phase 5 scopes to `state:lock:*`.
- **Lock name validation regex `^[a-zA-Z0-9_.\-/]+$`** — Phase 2 D-09 (explicit carry: "applies to all verbs accepting a key … later phases inherit for lock/claim names too"). Same `ValidationError(code="validation_error")` path.
- **JSON envelope** — Phase 2 D-01..D-05. Phase 3 adds the `held_by_another` status value (pre-announced in Phase 2 D-05) and the new error codes `held_by_another`, `not_held`, `warn_requires_tty`. Schema version stays `"1"` (additive only).
- **Per-verb `--json/--no-json` flag with TTY auto-detect default** — Phase 2 D-16. Every new verb (`lock`, `unlock`) exposes the pair. `lock --hold` is a special case: when wrapping a subprocess, the wrapped command's stdout passes through; only the wrapper's own emit (acquire/release log lines on stderr) honors the json mode.
- **File layout** — Phase 2 D-17. New files: `em_proj/state/lock.py` (sibling to `kv.py`, holds pure lock ops + Lua script strings, no typer imports). New verb wiring in `em_proj/state/__init__.py` (thin: parse argv → call lock.py op → `emit_*`). `em_proj/identity.py` (top-level sibling to `redis_client.py`) for session_id / project_hash / current-process composite — top-level because future `em-proj session` and `em-proj message` subcommands will need the same primitives (planner-flagged in D-12).
- **emit_* helpers** — Phase 2 D-15. New helper: `emit_held_by_another(code, message)` → exit 3 with the `held_by_another` envelope status. Add to `em_proj/output.py`.
- **Multi-process harness fixtures** (`multiproc_race`, `clean_db`, `EM_PROJ_REDIS_DB=15`) — Phase 1 D-13..D-16. New tests under `tests/multiprocess/test_lock_*.py` reuse them directly.

### Claude's Discretion
- **D-12:** Identity module home recommended at top-level `em_proj/identity.py` (sibling to `redis_client.py`), not nested under `em_proj/state/`. Rationale: future `em-proj session` / `em-proj message` subcommands also need session_id / project_hash resolution; nesting under `state/` would force a later move. Planner can confirm or move into `state/` if there's a structural reason.
- The exact set of helpers in `em_proj/identity.py` (e.g., `resolve_session_id()`, `resolve_project_hash()`, `current_process_composite() → dict`) — pick names that read cleanly.
- Lua script storage convention — inline triple-quoted strings module-level in `lock.py`, or load-once via `SCRIPT LOAD` + cached `EVALSHA`? Planner picks; for M1's invocation frequency (~ms latency budget per call) the difference is academic. Inline strings + `EVAL` is simpler; `EVALSHA` saves a few bytes on the wire.
- Refresher thread mechanics for `--hold` — `threading.Thread(daemon=True)` + `threading.Event` for stop-signal vs. `asyncio` event loop vs. a simple `subprocess.Popen.poll()` loop in the parent. Pick the shape that's easiest to test deterministically; the harness needs to assert the refresher actually ran.
- `--warn` prompt timeout — should the prompt hang forever waiting for input, or default to "no" after N seconds? Reasonable default: 30s, then exit 3. Planner picks.
- `--reason '<text>'` validation — max length (256 chars?), allowed characters (probably permissive — it's free-form metadata for human display). Planner picks.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project context (locks scope, decisions, verified facts)
- `.planning/PROJECT.md` — Core value, M1 active requirements (LOCK + IDENT sections), Constraints (especially "Dependencies allowed: redis-py, typer, pytest" — Phase 3 D-11 adds `psutil` to this list), Key Decisions (lock-default = block-with-1s + --warn opt-in; claim model added to M1; stale-detection composite includes proc_start_epoch + boot_id; CLAUDE_CODE_SESSION_ID is verified live). **Phase 3 PROJECT.md update:** add `psutil` to allowed-deps; mark IDENT/LOCK key decisions as ✓ Pending → ✓ Decided when phase ships.
- `.planning/REQUIREMENTS.md` — Phase 3 owns: IDENT-01, IDENT-02, LOCK-01, LOCK-02, LOCK-03 (5 REQ-IDs). All other REQ-IDs are out of scope for Phase 3.
- `.planning/ROADMAP.md` §"### Phase 3" — Goal + 5 numbered Success Criteria. **MUST satisfy all 5.** Notably criterion #5: "Two harness children racing `lock --hold` against the same name serialize correctly (one runs the wrapped command, the other waits then errors with exit 3)."
- `.planning/phases/01-test-harness-redis-foundation/01-CONTEXT.md` — D-07 (lazy module-level Redis client per process), D-09 (atomicity via Lua, NOT WATCH/MULTI/EXEC — Phase 3 stale-takeover + unlock compare-and-delete inherit this), D-13..D-16 (multiproc_race fixture shape), D-17 (one-line Redis-unreachable UX), D-19 (single chokepoint for Redis errors — Phase 3 lock.py and identity.py inherit this invariant).
- `.planning/phases/02-cli-shell-kv-primitive/02-CONTEXT.md` — D-01..D-05 (JSON envelope schema — Phase 3 adds `held_by_another` status + new error codes), D-06 (`state:` namespace prefix — Phase 3 writes to `state:lock:`), D-09 (key validation regex — Phase 3 lock names inherit), D-14..D-17 (typer subapp mount, output.py, --json per-verb flag, state/ package layout — Phase 3 sibling files follow this), D-18 (Redis-error single chokepoint — Phase 3 verbs inherit), D-19 (structural test pattern for "no direct redis.Redis()" — Phase 3 extends to lock.py and identity.py).

### Phase 1 / Phase 2 SUMMARY artifacts (concrete code Phase 3 builds on)
- `.planning/phases/01-test-harness-redis-foundation/01-03-SUMMARY.md` — `em_proj/redis_client.py` public surface: `get_client(db=None)`, `die_if_redis_unreachable(client)`, `_reset_for_tests()`. Lock verbs call these directly.
- `.planning/phases/01-test-harness-redis-foundation/01-04-SUMMARY.md` — `tests/conftest.py` fixtures: `redis_precheck` (session), `clean_db` (function), `multiproc_race` (function), `RaceResult`, constants `TEST_DB=15`, `EM_PROJ_BIN="em-proj"`. Lock multi-process tests (e.g., racing `state lock`, `lock --hold` vs `lock`) reuse these.
- `.planning/phases/02-cli-shell-kv-primitive/02-03-SUMMARY.md` and `02-04-SUMMARY.md` — `em_proj/state/kv.py` and `em_proj/state/__init__.py`: the shape `em_proj/state/lock.py` and the lock-verb wiring must mirror exactly (verb body = `resolve_json_mode → get_client → die_if_redis_unreachable → lock_op → emit_*`).
- `.planning/phases/02-cli-shell-kv-primitive/02-05-SUMMARY.md` — `tests/structural/test_phase_02_shape.py` precedent; Phase 3 adds `test_phase_03_shape.py` enforcing: D-18 single-chokepoint (no direct `redis.Redis()` in `lock.py` or `identity.py`); identity.py and lock.py exist at the documented paths; `emit_held_by_another` exists in `output.py`; `psutil` is importable.

### External libraries (use Context7 / direct docs)
- **redis-py** (>=7.4.0, already pinned) — `client.set(key, value, nx=True, ex=ttl)` for atomic acquire; `client.eval(script, numkeys, *keys_and_args)` for Lua compare-and-swap (stale-takeover) and compare-and-delete (unlock); `client.expire(key, ttl)` for the refresher thread; `decode_responses=True` is set in `redis_client.py` so all values come back as `str`.
- **typer** (>=0.25.1, already pinned) — `add_typer` for new lock verbs; `typer.Option("--warn/--no-warn", "--hold/--no-hold", "--ttl", "--reason")` for the flags; `typer.Exit(code=...)` for semantic exit codes; mutually-exclusive option pattern (raise inside the verb body if both `--warn` and `--hold` are set, per D-08).
- **psutil** (>=6.0, NEW dep added in Phase 3 per D-11) — `psutil.Process(pid).create_time()` returns process start as float epoch (matches Phase 3 `proc_start_epoch` field); `psutil.boot_time()` returns system boot as float epoch (Phase 3 hashes this for `boot_id`); `psutil.NoSuchProcess` exception when PID is gone (alternative to `os.kill(pid, 0)` ESRCH check). Cross-platform macOS/Linux — pyproject.toml dep declared as `psutil>=6.0`.
- **Redis docs** — `SET NX EX` semantics (https://redis.io/commands/set/), `EVAL` / `EVALSHA` for Lua atomicity, `EXPIRE` for refresh, `TIME` for server-side now-epoch inside Lua scripts.

### Project conventions (already documented)
- `CLAUDE.md` (repo root) — Test dispatcher (`bash scripts/test.sh <sub>` only, never `uv run pytest`); `tests/structural/` AST-based shape tests pattern (Phase 3 adds `test_phase_03_shape.py`); `scripts/verify-phase.sh` for phase verification; conventional commits with `feat(03-NN):` / `test(03-NN):` prefix; **NO `Co-Authored-By: Claude` trailer** (global rule).

### Verified facts (carried forward, no re-verification needed)
- `CLAUDE_CODE_SESSION_ID` UUID env var present in Claude Code sessions (PROJECT.md "Verified facts" — verified live during M1 bootstrap research)
- Project-hash scheme: `tr '/' '-'` on absolute path, no hashing, no truncation. Example: `~/.claude/projects/-Users-emonical-projects-personal-ai-tools-em-proj/`
- `~/.claude/sessions/<pid>.json` files use `{pid, sessionId, procStart, cwd, host}` — Phase 3 lock JSON schema (D-02) is the same shape minus host (single-machine constraint).

### No external ADRs
No `.planning/adrs/`, no external spec docs referenced during discussion. All Phase 3 implementation decisions captured in `<decisions>` above.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets (Phase 1 + Phase 2 outputs Phase 3 builds on)
- **`em_proj/redis_client.py`** (80 lines) — `get_client(db=None)` + `die_if_redis_unreachable(client)`. Phase 3 lock verbs and the `--hold` refresher thread MUST call through this. No direct `redis.Redis()` in `lock.py` or `identity.py` (structural test enforces).
- **`em_proj/cli.py`** (44 lines) — typer `app` with state subapp mounted via `app.add_typer(state_app, name="state", ...)`. Phase 3 adds `lock` and `unlock` verbs to `state_app`; no changes to `cli.py` itself.
- **`em_proj/state/__init__.py`** (172 lines) — `state_app` typer app + the four KV verb wirings. Phase 3 adds `lock` and `unlock` `@state_app.command()` decorators following the existing thin-wrapper pattern (`resolve_json_mode → get_client → die_if_redis_unreachable → op → emit_*`).
- **`em_proj/state/kv.py`** (177 lines) — Pure KV ops + `KvNotFound` / `ValidationError` exceptions + `KEY_REGEX` / `validate_key`. Phase 3 lock.py reuses `KEY_REGEX` / `validate_key` for lock names (Phase 2 D-09 carry). Phase 3 may need a sibling `LockNotHeld` exception for unlock-missing semantics.
- **`em_proj/output.py`** (193 lines) — `SCHEMA_VERSION`, `emit_ok` / `emit_not_found` / `emit_error` / `resolve_json_mode`. Phase 3 adds `emit_held_by_another(code, message)` → exit 3 with envelope `{schema_version: "1", status: "held_by_another", error: {code, message}}`.
- **`tests/conftest.py`** (160 lines) — `multiproc_race`, `clean_db`, `redis_precheck`, `RaceResult`, `TEST_DB=15`, `EM_PROJ_BIN="em-proj"`. Phase 3 lock contention tests reuse `multiproc_race` directly — same fork+exec semantics that already work for KV atomicity (Phase 2 `tests/multiprocess/test_kv_atomicity.py`).
- **`tests/structural/test_phase_02_shape.py`** — AST + source-inspection pattern. Phase 3 adds `test_phase_03_shape.py` for the new module shape + invariant enforcement.

### Established Patterns
- **CLI shape** `em-proj <subcommand> <verb> [args...]` — Phase 3 verbs slot in under `state_app`: `em-proj state lock <name>`, `em-proj state unlock <name>`, `em-proj state lock --hold <name> -- <cmd...>`.
- **Per-verb `--json/--no-json` flag** with TTY auto-detect default (Phase 2 D-16) — every new Phase 3 verb exposes it. Special case: `lock --hold` passes wrapped subprocess stdout through; the wrapper's own log lines go to stderr (json-mode controls only the wrapper's emit, not the child's stdout).
- **emit_* helpers + semantic exit codes** — Phase 3 introduces exit 3 (`held_by_another`) which Phase 2 reserved but didn't use; new `emit_held_by_another` helper makes it a one-call pattern symmetric with the rest of output.py.
- **Atomicity via Lua** — Phase 1 D-09 / Phase 3 D-06 / D-07 / D-10. Lua scripts live as triple-quoted module-level strings in `lock.py` (or load-once SCRIPT LOAD if planner picks that); never `WATCH`/`MULTI`/`EXEC`.
- **Conventional commits** with `feat(03-NN):` / `test(03-NN):` / `chore(03-NN):` prefix; NO `Co-Authored-By: Claude` trailer.
- **Test dispatcher** — All test invocations via `bash scripts/test.sh <sub>` (`unit`, `multiprocess`, `structural`, `all`). Never `uv run pytest` directly.
- **Structural test pattern** — `tests/structural/test_phase_03_shape.py` codifies Phase 3 D-* as runtime AST assertions: identity.py exists; lock.py exists; no direct `redis.Redis()` outside redis_client.py; `emit_held_by_another` is exported from `output.py`; `psutil` is importable.

### Integration Points
- **Phase 1 → Phase 3:** `em_proj/redis_client.py` → identity.py + lock.py + the --hold refresher thread (D-11/D-18 chokepoint). `tests/conftest.py` fixtures → lock contention tests (`multiproc_race` for racing two children).
- **Phase 2 → Phase 3:** `em_proj/state/__init__.py` `state_app` gets `lock` and `unlock` verb decorators added. `em_proj/output.py` adds `emit_held_by_another`. `em_proj/state/kv.py` `KEY_REGEX` / `validate_key` reused for lock-name validation.
- **Phase 3 → Phase 4:** Lock JSON schema (D-02) is the wire-format precedent for claim JSON. Phase 4 `em_proj/state/claim.py` mirrors lock.py's shape. `em_proj/identity.py` is shared verbatim (claim verbs call the same `resolve_session_id()` / `resolve_project_hash()` helpers). `CLAIM-03` "refuse anonymous claims" reuses identity.py's session-id resolution + an explicit error path.
- **Phase 3 → Phase 5:** `/global-state locks [--mine|--stale]` skill consumes the `state:lock:*` JSON values directly; `session_id` / `project_hash` / `acquired_at` / `expires_at` fields drive filtering and display. `unlock --force` skill verb is the explicit cleanup path for stale locks the opportunistic-on-acquire flow missed.
- **Phase 3 → Phase 6:** `em-proj state lock` itself isn't called by the gsd-sdk consumer (that's the claim model, Phase 4), but Phase 3 establishes the JSON-record + identity + stale-detection patterns the consumer's claim writes through.

</code_context>

<specifics>
## Specific Ideas

- **Single JSON blob per lock key** — chosen specifically because Phase 4 claims will need an even richer holder record and the Phase 5 skill should parse one schema across both. The pipe-delimited token alternative (compact but parsing-fragile) was rejected explicitly for "right forever" reasons — adding a 5th field to a pipe convention is a wire-format break.
- **Unix epoch floats over ISO strings** — chosen because Lua's `redis.call('TIME')` returns epoch + microseconds as numbers; numeric arithmetic for age/remaining is trivial; the skill formats for humans only at display time. Mirrors the rationale for keeping `schema_version` as a string but times as numbers.
- **60s default TTL** — short enough that an abandoned lock auto-releases within a minute (PROJECT.md "TTL is the final backstop"); long enough to span most short-lived advisory operations without `--hold`. `--hold`'s refresher thread keeps long-running wrapped commands alive past this default.
- **TTY-gated `--warn`** — explicit defense against the "advisory-warn becomes theater" failure mode called out in PROJECT.md's pitfalls research. A non-TTY caller that wants to override must use the Phase 5 skill's `unlock --force`, not `--warn` — `--warn` is humans-only.
- **`--warn` + `--hold` mutually exclusive** — the semantics conflict (auto-manage vs manual confirm). Erroring at parse time is cheaper than discovering the conflict mid-run.
- **Opportunistic stale-takeover on acquire** — the whole point of building the {pid, proc_start_epoch, boot_id} composite is to enable automatic recovery. Pure skill-driven cleanup would be a regression — every crashed `--hold` would require manual intervention before the next acquire.
- **psutil added to runtime deps** — IDENT-02 is load-bearing for every lock and every Phase 4 claim. Hand-parsing `ps -o lstart=` is fragile across macOS minor versions and slow under contention. The dep count goes from 3 → 4; PROJECT.md's allowed-deps list updates accordingly.
- **Identity at top-level `em_proj/identity.py`** — future `em-proj session` / `em-proj message` subcommands need the same primitives. Nesting under `state/` would force a later move.

</specifics>

<deferred>
## Deferred Ideas

- **Refresher failure modes** — what does `--hold` do if Redis blips mid-run? Abort the wrapped subprocess (defensive), or keep it running and hope the lock survives (best-effort)? Planner picks; sensible default = log warning to stderr, keep subprocess running, attempt one refresh retry per cycle. Reconsider if a real failure surfaces.
- **Refresher ownership verification** — should the refresher detect a skill `unlock --force` displacement (lock value changed mid-`--hold`) and abort the subprocess? Useful corner case for Phase 5+ integration; defer to when the skill lands.
- **`--ttl <N>` range validation** — Phase 3 sketches 1–3600s; planner finalizes the exact bounds and error message.
- **Prompt exact wording in `--warn` TTY mode** — Phase 3 sketches "Lock <name> held by session <id> (pid N, age Xs). Override? [y/N]"; planner finalizes (whether to show `reason`, whether to record `displaced_session_id` in the new holder JSON for cross-reference in unlock errors).
- **Prompt timeout in `--warn` TTY mode** — default to 30s then exit 3, or wait forever? Planner picks; 30s is a reasonable safety default.
- **`--reason '<text>'` validation** — max length and allowed character set. Permissive metadata; planner picks (sketch: 256 chars, any printable Unicode).
- **`em-proj health` user-facing verb** — already deferred in Phase 1 (D-18); reaffirmed for Phase 3. Skill `list` / `locks` covers the introspection need.
- **`/global-state locks [--mine|--stale|--all]` skill surface** — Phase 5 scope. Phase 3 makes it implementable by writing the full holder JSON (D-02) into every lock value.
- **Cross-machine sync** — out of scope per PROJECT.md (single-machine, single-user). Phase 3 `boot_id` is per-machine and not portable; that's fine.
- **`lock --tags <a,b>` or hierarchical lock names** — not proposed in this discussion; flagged here only to note that the `KEY_REGEX` already supports `/` in names so `lock --hold project/build` works without code changes. If hierarchical semantics (e.g., "release all locks under `project/*`") become useful, that's a future verb, not a Phase 3 feature.

**No scope creep raised during discussion.** All decisions stayed inside Phase 3's boundary (IDENT-01/02 + LOCK-01/02/03 only). Phase 4 (CLAIM-*) and Phase 5 (SKILL-*) were referenced only as integration points downstream, not as work items.

</deferred>

---

*Phase: 3-Identity + Advisory Locks*
*Context gathered: 2026-05-23*
