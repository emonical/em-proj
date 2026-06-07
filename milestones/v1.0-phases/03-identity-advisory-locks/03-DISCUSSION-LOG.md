# Phase 3: Identity + Advisory Locks - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-23
**Phase:** 3-Identity + Advisory Locks
**Areas discussed:** Lock value/payload shape, Lock TTL + refresh behavior, `--warn` semantics, Stale-detection trigger

---

## Lock value/payload shape

### Q1: What does Redis store under `state:lock:<name>`?

| Option | Description | Selected |
|--------|-------------|----------|
| JSON blob in value | Single key per lock, atomic `SET NX EX`, Lua `cjson.decode` for stale/ownership. Symmetric with Phase 4 claims. | ✓ |
| Compact pipe-delimited token | `<pid>\|<proc_start_epoch>\|<boot_id>\|<session_id>` — smaller, but parsing convention is fragile (adding a field forces a versioning hack). | |
| Two-key split: token + meta hash | `state:lock:<name>` value = `held`; `state:lock:<name>:meta` = Redis HASH. Queryable via HGET but every op becomes 2-key Lua. | |

**User's choice:** JSON blob in value
**Notes:** Right-forever choice: wire-format change later would force rewriting Lua + skill parsers + tests. JSON keeps lock and claim on one schema.

---

### Q2: Should the lock JSON include `session_id` and `project_hash`, or stay pure process-identity?

| Option | Description | Selected |
|--------|-------------|----------|
| Include session_id + project_hash | Full record: `{pid, proc_start_epoch, boot_id, session_id, project_hash, reason, acquired_at, expires_at}`. Phase 5 skill `--mine` / `--project` filtering works without a session registry. | ✓ |
| Pure process-identity only | `{pid, proc_start_epoch, boot_id, acquired_at, expires_at}` — faithful to LOCK-01 "process-scoped" wording. Skill `--mine` would need a separate session-id lookup (doesn't exist in M1). | |
| Process-identity + session_id only | Skip project_hash (derivable from cwd) and reason. Middle ground. | |

**User's choice:** Include session_id + project_hash
**Notes:** Full record now so lock + claim + skill all parse one schema.

---

### Q3: Time format for `acquired_at` and `expires_at`?

| Option | Description | Selected |
|--------|-------------|----------|
| Unix epoch float | `acquired_at: 1716480000.123`. Direct arithmetic; trivial Lua interop via `redis.call('TIME')`; language-neutral. Skill formats for humans at display time. | ✓ |
| ISO 8601 strings (UTC) | Human-readable in raw dumps, sortable lexicographically. Cost: every consumer parses to compute age/remaining; Lua TIME returns epoch so we'd convert in Lua. | |
| Both — epoch + `*_iso` sibling | Redundant, two-fields-to-keep-in-sync. | |

**User's choice:** Unix epoch float

---

## Lock TTL + refresh behavior

### Q1: Default TTL + `--hold` long-command behavior?

| Option | Description | Selected |
|--------|-------------|----------|
| 60s default, `--ttl <N>` override, `--hold` auto-refreshes | `SET NX EX 60` by default; `--ttl` overrides; `--hold` spawns daemon refresher thread running `EXPIRE` every `ttl/3` seconds while subprocess alive. TTL is final backstop if python parent crashes. | ✓ |
| 60s default, `--ttl <N>` override, `--hold` does NOT refresh | Simpler (no thread), but `<cmd>` running past 60s loses the lock — silent race. Footgun for `--hold`'s auto-release promise. | |
| No fixed default — require explicit `--ttl` every time | Forces callers to think about lifetime, but adds friction for a knob that's safe to default. | |

**User's choice:** 60s default, `--ttl <N>` override, `--hold` auto-refreshes

---

## `--warn` semantics

### Q1: What does `--warn` actually do when the lock is held?

| Option | Description | Selected |
|--------|-------------|----------|
| TTY-gated prompt: 1s block, then prompt on TTY / refuse on non-TTY | 1s acquire-block; if still held, on TTY prompt `[y/N]`; on non-TTY refuse with exit 3 + `warn-mode requires a TTY`. `--warn` + `--hold` mutually exclusive at parse. | ✓ |
| Warn + acquire anyway (no prompt) | Simple, no TTY logic. Cost: this IS the 'advisory-warn becomes theater' failure mode PROJECT.md's pitfalls research warned against. | |
| Warn + 1s block + override silently on timeout | Grace window then silent override. Same theater failure mode. | |
| Prompt always (no 1s block) | Skips the natural-finish grace window. | |

**User's choice:** TTY-gated prompt: 1s block, then prompt on TTY / refuse on non-TTY
**Notes:** Non-TTY callers that need programmatic override should use Phase 5 skill `unlock --force`, not `--warn`.

---

### Q2: After a successful `--warn` override, what does the displaced holder's `unlock` return?

| Option | Description | Selected |
|--------|-------------|----------|
| Exit 3 `held_by_another` | Stderr names the override; caller learns they were displaced. | ✓ |
| Exit 0 silent (no-op) | Simpler, but the holder never learns they were displaced — downstream code assumes the lock did its job. | |
| Exit 2 `not_found` | Middle signal — knows they didn't hold it, doesn't learn it was overridden. | |

**User's choice:** Exit 3 `held_by_another`
**Notes:** Important signal that something racy happened.

---

## Stale-detection trigger

### Q1: When does the `{pid, proc_start_epoch, boot_id}` composite get probed?

| Option | Description | Selected |
|--------|-------------|----------|
| Opportunistic on acquire + skill visibility | `lock` flow: try `SET NX EX` → on collision read holder JSON → run liveness probe in Python (kill+psutil) → if stale, Lua compare-and-swap; if live, block 1s. Phase 5 skill `locks --stale` also exposed. | ✓ |
| Explicit-only via skill | `lock` only blocks 1s and errors. All cleanup via Phase 5 skill. Pro: zero false-positive risk. Con: every abandoned lock needs manual intervention; `--hold`'s auto-release promise degrades. | |
| Opportunistic only (no skill visibility in M1) | Acquire is the only stale-cleanup path. Pro: less surface. Con: no audit "are there phantoms?". | |

**User's choice:** Opportunistic on acquire + skill visibility
**Notes:** The composite exists precisely to enable opportunistic recovery — skill is the free-layer-on-top.

---

### Q2: macOS dependency choice for `proc_start_epoch` / `boot_id` probing

| Option | Description | Selected |
|--------|-------------|----------|
| Add `psutil` | `psutil.Process(pid).create_time()` + `psutil.boot_time()` — battle-tested cross-platform. Updates PROJECT.md allowed-deps list (redis-py, typer, pytest, **psutil**). | ✓ |
| Shell out to `ps -o lstart=` + `sysctl kern.boottime` | No new dep but fragile across macOS versions; locale-sensitive date parsing; subprocess spawn per probe. | |
| Best-effort — `kill(pid, 0)` alone, accept PID-reuse risk | Zero deps but violates IDENT-02 (proc_start_epoch defeats PID reuse — explicit goal in PROJECT.md). | |

**User's choice:** Add `psutil`
**Notes:** IDENT-02 is load-bearing for every lock and every Phase 4 claim. Right call to add the dep — PROJECT.md update needed.

---

## Claude's Discretion

- Identity module home recommendation: `em_proj/identity.py` top-level (sibling to `redis_client.py`), shared with future `session` / `message` subcommands. Planner can confirm or move into `state/` if there's a structural reason.
- Exact helper names in `identity.py` (e.g., `resolve_session_id`, `resolve_project_hash`, `current_process_composite`) — pick names that read cleanly.
- Lua script storage convention — inline triple-quoted module strings vs. `SCRIPT LOAD` + cached `EVALSHA`. Difference is academic at M1's invocation frequency; planner picks the simpler shape.
- Refresher thread mechanics — `threading.Thread(daemon=True)` + `threading.Event` for stop-signal, or `subprocess.Popen.poll()` loop in the parent. Pick what's easiest to test deterministically.
- `--warn` prompt timeout default — 30s then exit 3 is a sensible safety default; planner picks.
- `--reason '<text>'` validation — max length (sketch 256 chars), allowed charset (permissive Unicode). Planner picks.
- Whether the `--warn` override records `displaced_session_id` in the new holder JSON (for cross-reference in the displaced holder's later `unlock` error message) — planner picks; principle is "the displaced holder learns".
- `--ttl <N>` range bounds (sketch 1–3600s) — planner finalizes.

## Deferred Ideas

- Refresher failure modes when Redis blips mid-`--hold` run (abort subprocess vs keep going)
- Refresher ownership verification — detect skill `unlock --force` displacement mid-`--hold` and abort subprocess
- Default `--warn` prompt timeout (none / 30s / forever)
- Whether the override records `displaced_session_id` in the new holder JSON for richer unlock error messages
- Phase 5 `/global-state locks [--mine|--stale|--all]` skill surface (Phase 5 scope; Phase 3 makes it implementable by writing the full holder JSON)
- `em-proj health` user-facing verb (already deferred in Phase 1; reaffirmed)
- Hierarchical lock names with `--tags` or "release all locks under `project/*`" semantics — `KEY_REGEX` already allows `/` so basic hierarchy works today; aggregate ops are a future verb

**No scope creep raised during discussion.** All decisions stayed inside Phase 3's boundary (IDENT-01/02 + LOCK-01/02/03 only).
