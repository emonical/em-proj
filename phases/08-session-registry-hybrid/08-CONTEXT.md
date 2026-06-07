# Phase 8: Session Registry (Hybrid) - Context

**Gathered:** 2026-06-07
**Status:** Ready for planning
**Source:** Interactive discuss-phase (curtailed) — implementation decisions resolved by recommendation + v1.0 convention, confirmed by user pivoting straight to plan-phase.

<domain>
## Phase Boundary

Deliver `em-proj session` — a live registry of who is running, where, holding
what. Verbs: `register`, `heartbeat`, `list`, `show`. `list`/`show` are a
**hybrid** view: each session record is enriched with the claims/locks/reservations
that session currently holds (joined over v1.0 holder metadata). Dead sessions are
excluded and reaped via the v1.0 stale-detection composite. The multi-process
harness proves registry liveness + stale reaping across fork+exec'd sessions.

**In scope (SESS-01..05, TEST-03):** the `session` subcommand family + registry
storage + enrichment join + stale reaping + harness coverage.

**Out of scope (later phases):** the listener daemon / auto-heartbeat (P11),
messaging send/scope filtering (P9/P10), SessionStart/UserPromptSubmit hooks +
`/em-sessions` skill (P12). Phase 8 ships only the explicit CLI verbs.
</domain>

<spec_lock>
## Locked by Requirements (do not re-litigate)

From `.planning/REQUIREMENTS.md` (SESS-01..05, TEST-03) and PROJECT.md:

- **Registry metadata schema (SESS-01)** — each session record carries:
  `{session_id, project_hash, upstream_identity, pid, proc_start_epoch, boot_id,
  cwd, registered_at, last_heartbeat}`.
- **Heartbeat refreshes liveness + TTL backstop (SESS-02).**
- **`list` is enriched (SESS-03)** with held claims/locks/reservations; **`show
  <session_id>`** returns one record + its held resources (SESS-04).
- **Stale-detection reuses the v1.0 composite (SESS-05)** — dead pid /
  proc_start mismatch / boot-id change / TTL lapse.
- **Backend = persistent Redis**; identity + output conventions from v1.0.
- **Multi-process harness is the test vehicle (TEST-03).**
</spec_lock>

<decisions>
## Implementation Decisions

### D1 — Enrichment output shape
`session list` shows **per-session counts** of held resources
(`{claims: N, locks: N, reservations: N}`) alongside each session's core record.
`session show <session_id>` returns the **full held-resource dicts** (the actual
claim/lock/reservation holder records). Rationale: keeps `list` cheap and
scannable for sub-agent triage ("who holds anything here?"); one extra `show`
call gets full detail.

### D2 — Liveness window
The **v1.0 read-time composite probe** (`is_holder_stale`: pid + proc_start_epoch
+ boot_id) is the **primary** liveness gate — a crashed session is excluded from
`list` immediately regardless of TTL. The **heartbeat TTL (~5 minutes) is a
backstop** for cases the probe can't catch (boot-id reuse, recycled pid). No
aggressive heartbeating required in Phase 8 (the auto-heartbeat daemon arrives in
P11; until then `session heartbeat` is explicit). Matches the v1.0
"conservative-probe + TTL backstop" philosophy.

### D3 — Reaping policy
**Lazy read-time eviction + TTL backstop.** `list`/`show` probe each entry with
`is_holder_stale`, exclude stale entries from results, and **opportunistically
DEL** them. Redis TTL is the final cleanup. No background sweeper, no separate
`session reap` verb in Phase 8. Directly testable by the harness
(spawn → kill → assert excluded + key DELed).

### D4 — Registry key scope & enrichment join
- **Key:** `state:session:<session_id>` — **machine-global** (NOT project- or
  upstream-scoped), so cross-project sessions are visible (sets up P10 messaging
  scope filtering, which selects on the `project_hash`/`upstream_identity` fields
  already in each record).
- **Storage:** Redis **HASH** per session record (mirrors `claim.py`/`reserve.py`),
  not a JSON string.
- **Enrichment join:** a **single broad SCAN** over `state:claim:*`,
  `state:lock:*`, `state:reserve:*`, grouping holders by their `session_id`
  field, then attaching to each registry entry. This needs a **new cross-namespace
  lister** — the existing `reserve_list_by_prefix(upstream_identity)` is
  upstream-scoped, so its scoping is dropped for this all-sessions pass. Enrich
  with **active (non-stale) holders only**.

### D5 — Registration idempotency
`session register` is an **upsert**: re-registering the same `session_id`
preserves `registered_at`, refreshes `last_heartbeat`, and re-arms the TTL. Safe
for the P12 SessionStart hook to call repeatedly.

### Claude's Discretion (planner/executor decides)
- Exact Lua vs pipeline for the register/heartbeat atomic write (follow
  claim.py's `LUA_*_REFRESH_OR_TAKE` analog where atomicity matters).
- Field serialization details in the HASH (epoch float formatting, null handling)
  — mirror `_hgetall_to_holder` conventions.
- Whether `list` accepts `--mine`/`--stale`/scope filters (follow the
  `*_list_by_prefix(mine=, stale=)` analog; add if cheap, defer if not needed by
  TEST-03).
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### v1.0 analog modules (closest patterns to replicate)
- `src/em_proj/identity.py` — REUSE: `resolve_session_id()` (:115),
  `resolve_project_hash()` (:134), `resolve_upstream_identity()` (:385),
  `current_process_composite()` (:159), `is_holder_stale(holder)` (:262) and its
  probes `probe_pid_alive` (:211) / `probe_proc_start_matches` (:234) /
  `current_boot_id` (:197). `PROC_START_EPSILON = 0.5` (:88).
- `src/em_proj/state/claim.py` — **primary analog** for the session record:
  HASH storage, `KEY_PREFIX = "state:claim:"` (:66), `_hgetall_to_holder` (:257),
  `claim_list_by_prefix(mine=, active=, stale=)` (:413) via
  `scan_iter(match=prefix+"*", count=100)`, Lua `LUA_CLAIM_REFRESH_OR_TAKE` (:113),
  `TTL_DEFAULT = 1800` (:68).
- `src/em_proj/state/reserve.py` — upstream-scoped HASH analog;
  `reserve_list_by_prefix(upstream_identity, ...)` (:490) — note its upstream
  scoping is what D4's cross-namespace lister must generalize past.
- `src/em_proj/state/lock.py` — `lock_list_by_prefix(mine=, stale=)` (:546) with
  the `stale` filter + injected key-suffix field pattern.
- `src/em_proj/redis_client.py` — `get_client(db=None)` (:23) singleton.
- `src/em_proj/output.py` — `emit_ok` (:94) / `emit_error` (:166) /
  `emit_not_found` (:135) / `emit_held_by_another` (:233); `SCHEMA_VERSION = "1"`
  (:65); exit codes 0/1/2/3.
- `src/em_proj/cli.py` — typer subcommand wiring (add the `session` subcommand
  here, mirroring how `state` is mounted).

### Planning inputs
- `.planning/ROADMAP.md` — Phase 8 section (goal + 5 success criteria).
- `.planning/REQUIREMENTS.md` — SESS-01..05, TEST-03 full text.
- `.planning/PROJECT.md` — constraints, output convention, Redis config, shell idioms.

### Test analog
- `tests/multiprocess/` — fork+exec race harness (TEST-01/02 precedent); TEST-03
  extends it for registry liveness + stale reaping. `tests/multiprocess/test_harness_self.py`.
- `tests/structural/` — add `test_08_*_shape.py` AST shape assertions per project CLAUDE.md.
</canonical_refs>

<code_context>
## Reusable Assets (from v1.0 scout)

- **Redis key namespaces today:** `state:kv:<key>`, `state:lock:<name>`,
  `state:claim:<project_hash>:<area>`, `state:reserve:<upstream_identity>:<area>`.
  Phase 8 adds `state:session:<session_id>` (machine-global).
- **Storage split:** lock = JSON string value; claim/reserve = Redis HASH. Session
  registry follows the **HASH** convention (claim/reserve), being multi-field.
- **List mechanism:** all three use inline `scan_iter(match=prefix+"*", count=100)`
  + per-key HGETALL/GET + decode + filter. There is **no** existing cross-type
  lister — D4 introduces the first one.
- **TTL/refresh:** claim/reserve refresh via `HSET expires_at` + `EXPIRE` inside a
  Lua EVAL (atomic, same-holder gated). Session heartbeat should mirror this.
- **Composite liveness is read-time:** holders store `{pid, proc_start_epoch,
  boot_id}`; `is_holder_stale(holder)` checks them. Registry entries carry the
  same triple, so reaping reuses `is_holder_stale` unchanged.
- **Output:** non-TTY or `--json` emits `{"schema_version":"1","status":...,"data":...}`.
</code_context>

<validation>
## Validation Strategy (TEST-03)

The multi-process harness MUST prove, across fork+exec'd child sessions:
1. A registered child appears in `session list` with correct metadata.
2. `session list` enrichment shows a resource the child holds (claim/lock/reserve)
   under that child's session_id.
3. A child that is killed (dead pid) is **excluded** from `list` on the next read
   and its registry key is reaped (DELed) — proving the read-time composite +
   lazy eviction path (D2/D3).
4. TTL-lapse backstop: an entry whose heartbeat TTL expired is gone.
</validation>

<deferred>
## Deferred Ideas

- **Auto-heartbeat via listener daemon** → Phase 11 (DAEMON-03). Phase 8 heartbeat
  is explicit-CLI only.
- **`session listen` / messaging channels** → Phases 10–11.
- **Scope-filtered listing for broadcast targeting** → Phase 10 (reads the
  `project_hash`/`upstream_identity` fields Phase 8 stores).
- **SessionStart/UserPromptSubmit hooks + `/em-sessions` skill** → Phase 12.
- **Dedicated `session reap` sweeper verb** → only if ops/testing later demands it
  beyond lazy read-time eviction.
</deferred>

---

*Phase: 08-session-registry-hybrid*
*Context gathered: 2026-06-07 — discuss-phase curtailed; decisions resolved by recommendation + v1.0 convention*
