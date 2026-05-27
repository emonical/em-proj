# Phase 6: gsd-sdk Workstream Consumer — Research

**Researched:** 2026-05-26
**Domain:** Cross-language consumer integration (Node→Python shell-out); concurrency-race demonstration
**Confidence:** HIGH

## Summary

Phase 6 wires the validating end-to-end consumer: `gsd-sdk query workstream.set <name>` shells out through `em-proj state claim` to acquire the active-workstream pointer as a TTL-scoped, refreshable claim. The consumer code change lives entirely in **gsd-sdk's compiled output** (`sdk/dist/query/workstream.js`), not in em-proj — em-proj's role for Phase 6 is to (a) ship a multi-process race test that drives `gsd-sdk` as the subject-under-test from two distinct sessions, and (b) ship a side-by-side clobber-vs-resolution demo that reproduces the original pain.

A critical finding from direct inspection of the npm-installed gsd-sdk (`/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/`): the current `workstreamSet` handler (in `sdk/src/query/workstream.ts` and compiled to `sdk/dist/query/workstream.js`) **writes directly to `.planning/active-workstream` with `writeFileSync`, no session-scoping, no concurrency guard**. This IS the clobber surface — the older CJS layer (`get-shit-done/bin/lib/planning-workspace.cjs`) had a `createSessionScopedPointerAdapter` and a `withPlanningLock` mechanism, but the modern `gsd-sdk query workstream.set` path does NOT use any of that. The pain is real and unmitigated today.

There is **no local source checkout of gsd-sdk** on this machine (only the npm-installed `node_modules` copy at `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/`). Edits to the consumer must therefore land directly in the npm-installed `sdk/dist/query/workstream.js` (and conceptually also `sdk/src/query/workstream.ts` for source/symmetry, though only the `.js` is loaded at runtime by `bin/gsd-sdk.js`). This shapes Open Question G's answer below.

**Primary recommendation:** Land the shell-out as a guarded mutation of `workstreamSet` in `sdk/dist/query/workstream.js`: before the existing `setActiveWorkstream(projectDir, name)` write, run `child_process.spawnSync('em-proj', ['state', 'claim', '--ttl', '<N>', '--json', 'workstream.active'])` with `env: { ...process.env, CLAUDE_CODE_SESSION_ID: ... }`. On exit code 3, return a structured `held_by_another` envelope and SKIP the file write. On exit code 0, proceed to the existing file write. On exit code 1 (anonymous or em-proj unreachable) or `ENOENT`, behave per a policy decision documented below.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CONSUMER-01 | `gsd-sdk workstream.set` writes through `em-proj state claim` via shell-out (no source extension of gsd-sdk) | Inspected `sdk/dist/query/workstream.js` `workstreamSet`. Identified the precise insertion point: before `setActiveWorkstream(projectDir, name)` (line ~232 of source / equivalent in dist). Subprocess invocation shape derived from claim verb signature in `state/__init__.py:439-505`. |
| CONSUMER-02 | Two concurrent Claude Code sessions in the same project no longer silently clobber each other's active-workstream pointer (demonstrated end-to-end) | Race test pattern proven in `tests/multiprocess/test_claim_race.py::test_two_sessions_race_claim_one_wins`. The Phase 6 test re-uses this shape but with `gsd-sdk` as argv[0] instead of `em-proj`. Lua `LUA_CLAIM_REFRESH_OR_TAKE` (claim.py:103-124) is the server-side serialization point — already validated in Phase 4. |
</phase_requirements>

<user_constraints>
## User Constraints (from PROJECT.md / ROADMAP.md — no CONTEXT.md exists for Phase 6)

### Locked Decisions
1. **Integration is shell-out only.** gsd-sdk's `workstream.set` invokes `em-proj state claim` as a child process. NO Node binding to Python, NO shared library, NO in-process integration. (PROJECT.md "Key Decisions" + roadmap goal.)
2. **`em-proj` source tree is NOT extended for this phase.** Consumer code lives in gsd-sdk's source tree. em-proj-side artifacts are limited to: (a) the multi-process race test, (b) the clobber-vs-resolution demo, (c) the structural shape test for Phase 6, (d) `verify-phase.sh 06` acceptance gate.
3. **The claim is long-lived, refreshable, TTL-scoped — NOT a lock.** The active-workstream pointer is a claim semantic. Phase 4 delivered claim ops; Phase 6 consumes them.
4. **Session-id identity is already resolved** by Phase 3's `IDENT-01` work (`CLAUDE_CODE_SESSION_ID` env var → pid- fallback chain in `em_proj/identity.py`). Shell-out call inherits env — no new identity logic.
5. **Anonymous claims are already refused** by Phase 4 (`CLAIM-03`). Consumer behavior on missing session-id: subprocess exits 1 with `anonymous_claim` error code; gsd-sdk surfaces it.
6. **Test execution flows through `bash scripts/test.sh`** — never `uv run pytest` directly (project CLAUDE.md).
7. **Structural tests go under `tests/structural/test_phase_06_shape.py`** — same pattern as Phase 3/4/5.
8. **Phase verification flows through `bash scripts/verify-phase.sh 06`.**
9. **Never append `Co-Authored-By: Claude` trailers** to commit messages (global rule + project CLAUDE.md).

### Claude's Discretion
- Exact area-key string for the workstream claim (proposal: `workstream.active` or `gsd.workstream`)
- Default TTL for the workstream claim (proposal: `1800` = 30min, same as `TTL_DEFAULT` in claim.py)
- Behavior when `em-proj` is not on PATH (proposal: fall back to current direct-write behavior with a stderr warning; document as accepted degradation)
- Exact shape of the side-by-side demo (pytest fixture vs. shell script vs. both — proposal in §F below)
- Whether to ALSO write `sdk/src/query/workstream.ts` (the source-of-truth `.ts`) in addition to the runtime-loaded `sdk/dist/query/workstream.js` (proposal: yes, for documentation/symmetry, even though only the `.js` is executed)
- Whether to surface the held_by_another error as a non-zero exit from `gsd-sdk` (proposal: yes — exit 3, matching em-proj convention) or as a JSON `{"set": false, "error": "held_by_another", ...}` body with exit 0

### Deferred Ideas
- **Multi-project workstream claim namespacing** — claim.py already scopes by `project_hash`, so two different repos can both have a "workstream.active" claim simultaneously. No change needed.
- **Refresh-on-every-command for the active workstream** — the claim's refreshable semantic means a session can re-claim on each `workstream.set` without conflict. NOT extending to other gsd-sdk verbs that might want to "renew" the claim periodically; that's out of scope.
- **Memory-coord / settings.json coord** — explicitly deferred (M2).
- **gsd-sdk integration tests on the gsd-sdk side** — out of Phase 6 scope per locked decision #2. If we want gsd-sdk's own CI to test this, it's a follow-up issue against gsd-sdk upstream.
</user_constraints>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Active-workstream pointer storage | em-proj state (Redis) | gsd-sdk file fallback | Phase 6 moves the authoritative pointer from a flat file to a TTL-scoped Redis claim. File write remains as a non-authoritative shadow (or is removed; see Open Question A). |
| Concurrency arbitration (which session wins) | em-proj state Lua (server-side) | — | `LUA_CLAIM_REFRESH_OR_TAKE` is the single atomicity point. Already shipped in Phase 4. |
| Session-id resolution | em-proj `identity.py` | — | gsd-sdk just inherits env; the child process resolves identity. |
| Workstream name validation | gsd-sdk (regex `^[a-zA-Z0-9_-]+$`) | em-proj `validate_key` regex | Two-layer; em-proj's `validate_key` will fire as a safety net. |
| Error surface mapping (exit 3 → structured envelope) | gsd-sdk JS handler | — | gsd-sdk reads the `em-proj` subprocess exit code + JSON stdout and translates to its own `QueryHandler` return shape. |
| Two-session demo / regression test | em-proj `tests/multiprocess/` | em-proj `scripts/` (optional demo script) | Phase 6 ships the regression coverage even though the code-under-test is in gsd-sdk. |
| Phase verification gate | em-proj `scripts/verify-phase.sh 06` | em-proj `tests/structural/test_phase_06_shape.py` | Mirrors Phase 3/4/5 pattern. |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `em-proj state claim` CLI | v0.x (Phase 4) | Claim-acquire subprocess called by gsd-sdk | The whole point of M1 — proven primitive. `[VERIFIED: src/em_proj/state/claim.py + src/em_proj/state/__init__.py:439-505 read directly]` |
| `node:child_process` (spawnSync) | Node 22 stdlib | Synchronous subprocess invocation from gsd-sdk JS handler | Already used in gsd-sdk's `bin/gsd-sdk.js` for the cli.js delegation. `[VERIFIED: bin/gsd-sdk.js:28-37 reads `const { spawnSync } = require('child_process');`]` |
| `subprocess.Popen` (Python) | 3.12 stdlib | Race-driver in em-proj's multi-process tests, with `gsd-sdk` as argv[0] | Proven pattern in `tests/multiprocess/test_claim_race.py`. `[VERIFIED: test_claim_race.py:134-147]` |
| `pytest` + `tests/conftest.py` fixtures | matches existing Phase 4/5 | Provides `clean_db`, `multiproc_race`, EM_PROJ_REDIS_DB=15 injection | Already shipped; no change needed. `[VERIFIED: tests/conftest.py]` |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `redis-py` | as-installed | Direct Redis assertions in race tests (`client.ttl(key)`, `client.exists(key)`) | When asserting Redis-side state post-race (already pattern in Phase 4 tests). `[VERIFIED: test_claim_race.py:77-84]` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `child_process.spawnSync` | `child_process.execFileSync` | Both work; `spawnSync` is preferred because it returns a structured `SpawnSyncReturns<Buffer|string>` with `.status`, `.stdout`, `.stderr` directly — easier to map to a `QueryHandler` return without the wrapping `Error` thrown by `execFileSync` on non-zero exit. **Recommend `spawnSync`.** `[CITED: Node 22 docs — child_process.spawnSync]` |
| Synchronous shell-out | Async `child_process.spawn` + Promise wrapper | gsd-sdk's `QueryHandler` is `async` so async is natural. But the per-call subprocess cost (Python startup) is ~100-150ms and dominates regardless; the simpler sync form is fine. The handler returns `Promise<QueryResult>` so wrapping `spawnSync` in `async (_args, projectDir) => { ... return { data } }` is correct. **Recommend sync spawn inside async handler.** `[ASSUMED]` |
| Per-call subprocess | Long-lived sidecar daemon | Way out of scope; M3+ messaging milestone. |

**Installation:**
No new dependencies — every required component is already on this machine.
- `em-proj` is installed (`/Users/emonical/.local/bin/em-proj`, verified via `command -v em-proj` in `verify-phase.sh`).
- `gsd-sdk` is installed (`/Users/emonical/.nvm/versions/node/v22.13.1/bin/gsd-sdk` → npm-installed `get-shit-done-cc@1.41.2`, `[VERIFIED: gsd-sdk --version returns 'gsd-sdk v1.41.2']`).
- `node` is on PATH (`v22.13.1`, `[VERIFIED: node --version]`).
- `redis-py`, `pytest`, etc. are already devDependencies.

**Version verification:**
- `get-shit-done-cc` package version: `1.41.2` `[VERIFIED: package.json:3 + gsd-sdk --version]`
- Install path: `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/` `[VERIFIED: which gsd-sdk + bin/gsd-sdk.js:30]`
- Install mtime: `May 11 12:25:36 2026` `[VERIFIED: stat package.json]`
- No local git checkout exists for gsd-sdk on this machine `[VERIFIED: find scan of /Users/emonical/projects and /Users/emonical/* for get-shit-done / gsd*]`

## Architecture Patterns

### System Architecture Diagram

```
Session A (Claude Code)          Session B (Claude Code)
  $ /gsd-workstreams switch X      $ /gsd-workstreams switch X
  │                                  │
  │  CLAUDE_CODE_SESSION_ID=uuid-A   │  CLAUDE_CODE_SESSION_ID=uuid-B
  ▼                                  ▼
  gsd-sdk query workstream.set X    gsd-sdk query workstream.set X
  │                                  │
  │  (Node, ESM, sdk/dist/cli.js)    │
  ▼                                  ▼
  workstreamSet(['X'], cwd)         workstreamSet(['X'], cwd)
  │                                  │
  │ ───── NEW: shell-out gate ─────  │ ───── NEW: shell-out gate ─────
  ▼                                  ▼
  spawnSync('em-proj', ['state',    spawnSync('em-proj', ['state',
    'claim', '--ttl', '1800',         'claim', '--ttl', '1800',
    '--json', 'workstream.active'])   '--json', 'workstream.active'])
  │                                  │
  ▼                                  ▼
  Python process (claim verb)       Python process (claim verb)
  │                                  │
  └────────────────┬─────────────────┘
                   ▼
        Redis: EVAL LUA_CLAIM_REFRESH_OR_TAKE
        (server-side, single command slot)
                   │
       ┌───────────┴───────────┐
       │                       │
       ▼ "taken"               ▼ "conflict"
   exit 0                    exit 3
   stdout: {area,            stdout: held_by_another
     ttl, claimed_at,           envelope with holder dict
     expires_at}             stderr: "claim held by session uuid-A"

   gsd-sdk handler:           gsd-sdk handler:
   → setActiveWorkstream      → DO NOT setActiveWorkstream
   → syncRootStateMirror      → return { data: {
   → return { data: {           set: false,
     active: 'X',                error: 'held_by_another',
     set: true,                  holder: {...},
     mirror_synced: true } }     message: '...' } }
   → process exit 0            → process exit 3 (or 0 with body)
```

### Component Responsibilities

| File | Responsibility | Action in Phase 6 |
|------|---------------|-------------------|
| `node_modules/get-shit-done-cc/sdk/dist/query/workstream.js` | Runtime handler reached by `gsd-sdk query workstream.set` | **EDIT** — insert `spawnSync('em-proj', ...)` gate before `setActiveWorkstream(...)` call in `workstreamSet`. |
| `node_modules/get-shit-done-cc/sdk/src/query/workstream.ts` | TS source-of-truth (NOT loaded at runtime; only the `.js` is) | **EDIT for symmetry/documentation** — keep `.ts` and `.js` in lockstep so the next `npm install -g get-shit-done-cc` upgrade doesn't surprise us with a missing-shellout. (Caveat: an upgrade will overwrite both anyway — see Open Question G.) |
| `src/em_proj/state/claim.py` | Pure claim ops (refresh-or-take, release, check) | **NO CHANGE** — already meets the contract. |
| `src/em_proj/state/__init__.py` | Claim verb wiring | **NO CHANGE** — `em-proj state claim` already produces the JSON envelope and exit codes Phase 6 needs. |
| `tests/multiprocess/test_workstream_consumer_race.py` (NEW) | Two-session race against `gsd-sdk` as subject-under-test | **CREATE** — mirrors `test_claim_race.py` shape, swaps argv[0]. |
| `tests/multiprocess/test_workstream_clobber_demo.py` (NEW) | Side-by-side clobber-vs-resolution demo (SC#3) | **CREATE** — two test cases, one driving the file-only path, one driving the through-claim path. |
| `tests/structural/test_phase_06_shape.py` (NEW) | AST + filesystem-grep assertions on Phase 6 surface | **CREATE** — assert (a) Phase 6 files exist, (b) gsd-sdk `workstream.js` contains the `em-proj` `spawnSync` call OR an em-proj-side wrapper, (c) SUMMARY coverage. |
| `scripts/em-proj-workstream-demo.sh` (NEW, optional) | Human-runnable side-by-side demo (SC#3 "human-runnable in ~10s") | **CREATE OR FOLD into pytest** — see §F decision. |

### Recommended Project Structure
```
sdk/dist/query/
└── workstream.js          # EDITED — shell-out gate added to workstreamSet

src/em_proj/state/         # UNCHANGED — claim ops already complete

tests/multiprocess/
├── test_workstream_consumer_race.py     # NEW — two-session race
└── test_workstream_clobber_demo.py      # NEW — SC#3 side-by-side

tests/structural/
└── test_phase_06_shape.py               # NEW — phase shape invariants

scripts/
└── em-proj-workstream-demo.sh           # NEW (optional) — human-runnable demo
```

### Pattern 1: Synchronous Python-subprocess from Node async handler

**What:** Wrap `child_process.spawnSync` inside an `async` `QueryHandler` for blocking-but-clean error mapping.

**When to use:** When the work being done is intrinsically sequential against a single backend (Redis claim acquire), Python startup cost dominates, and the handler shape requires `Promise<QueryResult>`.

**Example (concrete shape for `workstreamSet`):**
```javascript
// Source: synthesis of Node 22 child_process.spawnSync docs +
//         existing gsd-sdk pattern in bin/gsd-sdk.js:32-37
//         + em-proj state claim CLI shape verified in src/em_proj/state/__init__.py:439-505
const { spawnSync } = require('child_process');

export const workstreamSet = async (args, projectDir) => {
    const name = args[0];

    if (!name || name === '--clear') {
        // ...existing branch unchanged (clear/no-name path)
    }

    if (!/^[a-zA-Z0-9_-]+$/.test(name)) {
        return { data: { active: null, error: 'invalid_name',
                         message: '...' } };
    }

    const wsDir = join(workstreamsDir(projectDir), name);
    if (!existsSync(wsDir)) {
        return { data: { active: null, error: 'not_found', workstream: name } };
    }

    // ─── NEW: claim gate before file write ────────────────────────────────
    const claimResult = spawnSync(
        'em-proj',
        ['state', 'claim', '--ttl', '1800', '--json', 'workstream.active'],
        {
            env: process.env,           // CLAUDE_CODE_SESSION_ID inherited
            encoding: 'utf-8',
            stdio: ['ignore', 'pipe', 'pipe'],
        }
    );

    if (claimResult.error && claimResult.error.code === 'ENOENT') {
        // em-proj not on PATH — degrade per policy decision (see Open Q)
        // Recommend: emit a stderr warning and fall through to file-only write
        process.stderr.write(
            'gsd-sdk: em-proj not on PATH; falling back to unguarded ' +
            'workstream.set (concurrent sessions may clobber).\n'
        );
    } else if (claimResult.status === 3) {
        // Held by another session — DO NOT write
        let holder = null;
        try { holder = JSON.parse(claimResult.stdout).data.holder; } catch {}
        return { data: {
            set: false,
            error: 'held_by_another',
            workstream: name,
            holder,
            message: holder
                ? `workstream held by session ${holder.session_id}`
                : 'workstream.active held by another session',
        } };
    } else if (claimResult.status === 1) {
        // Anonymous refusal or validation error — propagate
        let detail = claimResult.stderr || '(no detail)';
        return { data: {
            set: false,
            error: 'claim_refused',
            workstream: name,
            detail,
        } };
    }
    // claimResult.status === 0 → claim taken or refreshed; proceed
    // ─────────────────────────────────────────────────────────────────────

    setActiveWorkstream(projectDir, name);
    syncRootStateMirror(projectDir, name);
    return { data: { active: name, set: true,
                     mirror_synced: existsSync(join(wsDir, 'STATE.md')) } };
};
```

### Pattern 2: Two-session race via subprocess.Popen with per-child env injection

**What:** Same fork+exec pattern used in Phase 4's `test_claim_race.py`, swapping `EM_PROJ_BIN` for the `gsd-sdk` invocation argv.

**When to use:** Any test asserting concurrency outcome between two distinct Claude Code sessions in the same project.

**Example:**
```python
# Source: tests/multiprocess/test_claim_race.py:120-179
# Adapted: argv[0] becomes 'gsd-sdk'; argv tail becomes
#   ['query', 'workstream.set', 'X', '--raw', '--cwd', cwd]
import subprocess
import os

def test_two_sessions_race_workstream_set_one_wins(clean_db, tmp_path):
    # Set up a .planning/workstreams/X directory in tmp_path so
    # gsd-sdk's existsSync(wsDir) check passes.
    planning = tmp_path / ".planning" / "workstreams" / "active-ws"
    planning.mkdir(parents=True)
    (planning / "STATE.md").write_text("---\nworkstream: active-ws\n---\n")

    child_a_env = {
        **os.environ,
        "EM_PROJ_REDIS_DB": "15",
        "CLAUDE_CODE_SESSION_ID": "ws-race-A",
    }
    child_b_env = {
        **os.environ,
        "EM_PROJ_REDIS_DB": "15",
        "CLAUDE_CODE_SESSION_ID": "ws-race-B",
    }

    cmd = ["gsd-sdk", "query", "workstream.set", "active-ws",
           "--raw", "--cwd", str(tmp_path)]

    proc_a = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True,
                              env=child_a_env)
    proc_b = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True,
                              env=child_b_env)

    out_a, err_a = proc_a.communicate(timeout=15.0)
    out_b, err_b = proc_b.communicate(timeout=15.0)

    # Exactly one session should win the claim
    # (its stdout JSON has set: true); the other should report
    # held_by_another (its stdout JSON has set: false, error: 'held_by_another').
    import json
    a_data = json.loads(out_a)
    b_data = json.loads(out_b)
    winners = [d for d in (a_data, b_data) if d.get('set') is True]
    losers = [d for d in (a_data, b_data) if d.get('set') is False
              and d.get('error') == 'held_by_another']
    assert len(winners) == 1, f"Expected 1 winner; got {winners}"
    assert len(losers) == 1, f"Expected 1 held_by_another loser; got {losers}"
```

### Anti-Patterns to Avoid

- **DO NOT extend gsd-sdk via a Node binding to the Python claim API.** Locked decision #1 — shell-out only.
- **DO NOT write a wrapper that pre-acquires the claim in Python then invokes gsd-sdk.** The shell-out direction MUST be Node-calling-Python (gsd-sdk is the consumer; em-proj is the substrate). Reversing it loses the point of the locked architecture.
- **DO NOT use `child_process.execSync` or `execFileSync`.** Both throw on non-zero exit, which forces a try/catch around expected exit-3 control flow. `spawnSync` returns a struct cleanly. `[CITED: Node 22 child_process docs]`
- **DO NOT shell out without `--json`.** The claim verb's TTY-detection emits human-formatted text by default; gsd-sdk MUST pass `--json` so the response parses deterministically. `[VERIFIED: src/em_proj/state/__init__.py:497-505 emit_ok call respects json_mode]`
- **DO NOT skip `--cwd` in the race test invocations of `gsd-sdk`.** Per slash-command convention (`workstreams.md:38, 41, 46, 50, 56, 61`) the `--raw --cwd "$CWD"` pair is mandatory.
- **DO NOT use a wildcard Bash allowlist for `gsd-sdk`** in the test harness. The test wrapper uses `subprocess.Popen` directly (no shell), so this doesn't bite in the test code itself — but any helper script that drives the demo must follow the project's wrapper-script-with-exact-match pattern.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Cross-process Redis claim with refresh semantics | A second Lua script, a Node-side advisory lock, a file-lock | `em-proj state claim` (Phase 4, shipped) | Already has Lua atomicity, TTL, refresh-or-take, anonymous refusal, holder metadata — every contract Phase 6 needs. |
| Session-id resolution | A new env-var probe in gsd-sdk | `CLAUDE_CODE_SESSION_ID` env passed through to the subprocess | em-proj's `identity.py` is the single source. gsd-sdk just forwards env. |
| Cross-process atomic write to `.planning/active-workstream` | `withPlanningLock` (in old CJS layer) | The Redis claim IS the lock | The file write becomes a non-authoritative cache; the claim is the truth. |
| Subprocess-output parsing JSON envelope | A schema validator, a regex parser | `JSON.parse(claimResult.stdout)` against the documented envelope | `{"schema_version":"1","status":"ok","data":{...}}` is the stable contract. `[VERIFIED: em-proj state claim-list --mine --json output observed in this session]` |
| Two-session race test infrastructure | A fresh fixture | The existing `multiproc_race`/`clean_db` fixtures + `EM_PROJ_REDIS_DB=15` injection | Already works against arbitrary argv (verified in conftest.py:114-160). Just pass `["gsd-sdk", ...]` instead of `[EM_PROJ_BIN, ...]`. |

**Key insight:** Every novel concurrency, identity, and atomicity concern was solved in Phases 3-5. Phase 6 is glue + a regression test. The temptation to "improve" the claim semantics in passing is the most dangerous failure mode — resist it.

## Runtime State Inventory

> Phase 6 is structurally a refactor/migration (gsd-sdk's writeFileSync → claim-mediated write). Runtime state inventory applies.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| **Stored data** | `.planning/active-workstream` flat file at each project root that uses gsd-sdk workstreams. Plus, post-Phase-6: a `state:claim:<project_hash>:workstream.active` Redis HASH per project. | Code edit only — gsd-sdk's file-write path is retained as a non-authoritative shadow (or removed; see Open Q A). No DB migration needed because the claim is new (no prior keys to migrate). |
| **Live service config** | None — gsd-sdk is npm-installed, not a service. n8n / Datadog / Cloudflare are not in scope. | None. |
| **OS-registered state** | None — no Task Scheduler tasks, no launchd, no pm2 entries reference "workstream." | None. |
| **Secrets/env vars** | `CLAUDE_CODE_SESSION_ID` is read by Phase 3 identity code. No new env var introduced. | None — code edit only (gsd-sdk's spawnSync inherits the parent env). |
| **Build artifacts** | `sdk/dist/query/workstream.js` is the compiled artifact derived from `sdk/src/query/workstream.ts`. The npm-installed package ships BOTH; only `.js` is loaded. **Critical:** an `npm install -g get-shit-done-cc` upgrade will overwrite both with upstream contents, silently reverting Phase 6's consumer edit. | Document this hazard in the plan (Open Q G). Either (a) submit upstream PR to gsd-sdk, (b) wrap the global install with a post-install patch, or (c) accept that upgrade-erodes-the-shellout and add a verify-phase check that asserts the shellout is still present. |

**Nothing found in category:** Stale data, OS-registered state, secrets — all verified as not applicable.

## Common Pitfalls

### Pitfall 1: Python subprocess startup cost (~100–150ms) dominates `workstream.set` latency
**What goes wrong:** Every `gsd-sdk query workstream.set` call now spawns a Python interpreter to invoke `em-proj state claim`. Cold start cost.
**Why it happens:** Python 3.12 startup is ~100ms; `typer` import adds ~30–50ms; redis-py connect adds ~5ms.
**How to avoid:** **Accept it.** `workstream.set` is a low-frequency operation (a few times per session, on context switch — not in any hot path). Phase 4 already accepted this cost for `claim`. Document as accepted tradeoff in the plan.
**Warning signs:** If `workstream.set` ends up in a hot loop (it shouldn't), revisit with a sidecar daemon (M3+).

### Pitfall 2: `spawnSync` env-passthrough on Windows-vs-Unix
**What goes wrong:** By default `child_process.spawnSync` passes `process.env` through verbatim — but on Windows, env-var inheritance has edge cases around case-insensitive keys.
**Why it happens:** Node docs note that on Windows, `process.env` is case-insensitive but `options.env` (if provided) is case-sensitive.
**How to avoid:** Pass `env: process.env` explicitly (we do), and DO NOT try to construct a fresh env dict that omits things — that's where Windows case-mangling bites. `CLAUDE_CODE_SESSION_ID` is upper-case and will survive on both platforms. `[CITED: Node 22 child_process docs]`
**Warning signs:** macOS+Linux green, Windows red. Single-machine target per PROJECT.md constraints — Windows is out of scope, but document the assumption.

### Pitfall 3: gsd-sdk's `--cwd` flag vs em-proj's `project_hash` resolution
**What goes wrong:** `gsd-sdk query workstream.set X --cwd /some/path` operates on `/some/path/.planning/`, but the subprocess `em-proj state claim` inherits the Node process's actual `process.cwd()`, NOT `/some/path`. The `project_hash` em-proj computes will be based on Node's cwd, NOT gsd-sdk's `--cwd`. In normal use these are the same (the slash command runs `gsd-sdk query workstream.set X --raw --cwd "$CWD"` from `$CWD`), but in tests we may pass a `tmp_path` cwd via `--cwd` while leaving Node's `process.cwd()` as the test runner's cwd.
**Why it happens:** Two independent cwd notions. em-proj derives `project_hash` from `os.getcwd()` (which Python inherits from Node's cwd, not from gsd-sdk's `--cwd` arg).
**How to avoid:** Pass `cwd: tmp_path` to `spawnSync` in gsd-sdk's handler when `--cwd` is provided. The consumer-side patch MUST set `spawnSync(..., { cwd: projectDir, env: process.env, ... })`. Or: in the test, use `os.chdir(tmp_path)` before the race so Python and Node agree.
**Warning signs:** Race test passes locally but the claim key is keyed by the test-runner's cwd, not the per-test `tmp_path`. Means two different test files can leak claims into each other.

### Pitfall 4: Refresh-vs-conflict for the same session re-setting the same workstream
**What goes wrong:** Session A runs `workstream.set X` (acquires claim). Five minutes later, Session A runs `workstream.set X` again. If the consumer treats every `set` as a "new claim," it errors with held_by_another against itself.
**Why it happens:** Forgetting that `claim_take`'s Lua script has refresh-or-take semantics for the same `(session_id, project_hash)` tuple. Phase 4's `LUA_CLAIM_REFRESH_OR_TAKE` returns `"refreshed"` (exit 0) when the SAME session re-claims.
**How to avoid:** The fix is in the contract — `em-proj state claim` ALREADY handles this correctly. The pitfall is misreading the contract and adding a "have I already claimed it?" check on the gsd-sdk side. **Don't.** Just call claim; exit 0 means "you have it" regardless of whether it was newly taken or refreshed.
**Warning signs:** A test that has the SAME session call `workstream.set X` twice in succession should exit 0 both times. If it errors with held_by_another, the contract is misread.

### Pitfall 5: Test harness leaks claims into prod Redis db=0
**What goes wrong:** A test calls `gsd-sdk query workstream.set X` without `EM_PROJ_REDIS_DB=15` in the child env. The em-proj subprocess connects to db=0 and writes a claim there. The user's live sessions see ghost claims.
**Why it happens:** gsd-sdk's `spawnSync({ env: process.env })` passes the test runner's env to em-proj. If the test runner sets `EM_PROJ_REDIS_DB=15` in its own env (which the existing `multiproc_race` fixture does at line 127 of conftest.py), the variable propagates correctly. But if the test invokes `subprocess.Popen(['gsd-sdk', ...])` directly without injecting `EM_PROJ_REDIS_DB`, gsd-sdk has no way to know about it.
**How to avoid:** Every race test that invokes `gsd-sdk` MUST inject `EM_PROJ_REDIS_DB=15` into the gsd-sdk subprocess's env. The variable will then propagate to the spawned em-proj grandchild. Pattern: `subprocess.Popen([...], env={**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB), "CLAUDE_CODE_SESSION_ID": "..."})`.
**Warning signs:** Test exit codes look right but `clean_db` flushdb doesn't clean the leaked keys (because the keys went to db=0).

### Pitfall 6: Upgrading `get-shit-done-cc` silently reverts the consumer patch
**What goes wrong:** `npm install -g get-shit-done-cc@latest` (or any auto-update) overwrites `sdk/dist/query/workstream.js` with upstream content, erasing the Phase 6 shell-out gate without warning. Two sessions silently clobber again.
**Why it happens:** The patch is applied to a node_modules file that npm regards as canonical and replaceable.
**How to avoid:** Document the hazard in 06-SUMMARY.md and add the consumer-edit-presence check to `tests/structural/test_phase_06_shape.py`. The test asserts that the npm-installed `workstream.js` contains the literal string `em-proj` (or some sentinel comment we inject). On upgrade, the test fails and the gap is visible. Long-term: upstream PR to gsd-sdk.
**Warning signs:** Phase verifier reports all green; `workstream.set` from two sessions clobbers in practice. Indicates the structural test isn't reaching into the actual loaded JS.

### Pitfall 7: "Old path" reproduction in the demo
**What goes wrong:** SC#3 requires showing the OLD (clobbering) behavior side-by-side with the NEW (resolution) behavior. If Phase 6's patch replaces the entire `workstreamSet` body, there's nothing left to demonstrate the clobber against.
**Why it happens:** The cleanest refactor IS to inline the gate before the write, leaving the file-write code intact. **But** the OLD-path demo needs to bypass the gate.
**How to avoid:** Three viable mechanisms; recommend #2:
  1. **Restore the pre-patch `workstreamSet` temporarily.** Brittle, can't reproduce after the patch ships.
  2. **Use direct file writes for the old-path demo.** Drive the clobber by writing `.planning/active-workstream` from two pytest fixture processes in parallel via plain Python `open(p, 'w').write(name)`. Then drive the new-path resolution by invoking `gsd-sdk query workstream.set` from the same two fixture processes. Two separate test cases; same `tmp_path`; same harness. **Recommended.**
  3. **Add an `--unsafe` / `EM_PROJ_BYPASS=1` env-var bypass in gsd-sdk.** Adds a permanent escape hatch we don't want. NOT recommended.
**Warning signs:** SC#3 demo is a hand-wavy README rather than a runnable artifact.

## Code Examples

### Example 1: gsd-sdk `workstreamSet` patch (JS — for `sdk/dist/query/workstream.js`)
See Pattern 1 above — full code shape verified against current `workstream.js` (lines 211-235 of `workstream.ts`, equivalent block in compiled `.js`).

### Example 2: Race test (Python — for `tests/multiprocess/test_workstream_consumer_race.py`)
See Pattern 2 above — full code shape, adapted from `test_claim_race.py:120-179`.

### Example 3: Clobber demo old-path baseline (Python — for `tests/multiprocess/test_workstream_clobber_demo.py`)
```python
# Source: synthesis — direct file writes mirror gsd-sdk's pre-patch behavior
import os
import subprocess
from pathlib import Path


def test_old_path_direct_file_write_clobbers(tmp_path):
    """Baseline: two parallel writes to .planning/active-workstream clobber.

    Reproduces the pre-Phase-6 behavior by writing directly to the file the
    way gsd-sdk's setActiveWorkstream did (writeFileSync, no guard).
    """
    planning = tmp_path / ".planning"
    planning.mkdir()
    pointer = planning / "active-workstream"

    # Simulate two sessions writing in parallel via subprocess
    # (avoid Python multiprocessing's macOS fork-safety gotcha)
    script_a = f"""
from pathlib import Path
Path({str(pointer)!r}).write_text("workstream-A\\n")
"""
    script_b = f"""
from pathlib import Path
Path({str(pointer)!r}).write_text("workstream-B\\n")
"""
    p_a = subprocess.Popen(["python3", "-c", script_a])
    p_b = subprocess.Popen(["python3", "-c", script_b])
    p_a.wait(timeout=5)
    p_b.wait(timeout=5)

    # The clobber: whatever wrote last wins; the other session's choice is lost.
    final = pointer.read_text().strip()
    assert final in ("workstream-A", "workstream-B")
    # No structured "you were displaced" signal exists.


def test_new_path_through_gsd_sdk_refuses_loser(clean_db, tmp_path):
    """Resolution: same race through gsd-sdk produces a deterministic outcome."""
    # Set up workstreams dir so existsSync passes
    (tmp_path / ".planning" / "workstreams" / "ws-A").mkdir(parents=True)
    (tmp_path / ".planning" / "workstreams" / "ws-A" / "STATE.md").write_text("---\nworkstream: ws-A\n---\n")

    base_env = {**os.environ, "EM_PROJ_REDIS_DB": "15"}
    env_a = {**base_env, "CLAUDE_CODE_SESSION_ID": "demo-A"}
    env_b = {**base_env, "CLAUDE_CODE_SESSION_ID": "demo-B"}

    cmd = ["gsd-sdk", "query", "workstream.set", "ws-A",
           "--raw", "--cwd", str(tmp_path)]

    p_a = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, env=env_a)
    p_b = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, env=env_b)
    out_a, _ = p_a.communicate(timeout=15)
    out_b, _ = p_b.communicate(timeout=15)

    import json
    a = json.loads(out_a)
    b = json.loads(out_b)
    winners = [d for d in (a, b) if d.get("set") is True]
    losers = [d for d in (a, b) if d.get("error") == "held_by_another"]
    assert len(winners) == 1, f"got {a}, {b}"
    assert len(losers) == 1
    # The structured signal: the loser learns it was refused (NOT silently clobbered).
    assert "holder" in losers[0]
    assert losers[0]["holder"]["session_id"] in ("demo-A", "demo-B")
```

### Example 4: Structural shape test sketch (Python — for `tests/structural/test_phase_06_shape.py`)
```python
# Source: synthesis from test_phase_05_shape.py:46-362 patterns
import re
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GSD_SDK_INSTALL = Path("/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc")
WORKSTREAM_JS = GSD_SDK_INSTALL / "sdk" / "dist" / "query" / "workstream.js"

def test_gsd_sdk_workstream_js_contains_em_proj_shellout():
    """The npm-installed workstream.js must contain the em-proj shell-out (CONSUMER-01)."""
    if not WORKSTREAM_JS.exists():
        pytest.xfail("gsd-sdk not installed at expected path — cannot audit consumer")
    source = WORKSTREAM_JS.read_text()
    # The patch inserts spawnSync('em-proj', ['state', 'claim', ...]) before
    # setActiveWorkstream(...) in workstreamSet. Assert both fragments are present
    # AND that the order is correct.
    assert "'em-proj'" in source or '"em-proj"' in source, (
        "workstream.js does not reference 'em-proj' — Phase 6 consumer patch "
        "either never landed or was reverted by an `npm install -g` upgrade."
    )
    # Stronger: the spawnSync call appears BEFORE setActiveWorkstream within workstreamSet
    m = re.search(r"workstreamSet\s*=\s*async[\s\S]+?};", source)
    assert m, "could not locate workstreamSet handler in dist/query/workstream.js"
    body = m.group(0)
    em_proj_idx = body.find("em-proj")
    set_active_idx = body.find("setActiveWorkstream")
    assert em_proj_idx > 0, "em-proj shell-out not present in workstreamSet body"
    assert set_active_idx > em_proj_idx, (
        "em-proj shell-out must appear BEFORE setActiveWorkstream call"
    )

def test_phase_06_summaries_present():
    """SUMMARY coverage check, identical pattern to Phase 5."""
    phase_dir = REPO_ROOT / ".planning" / "phases" / "06-gsd-sdk-workstream-consumer"
    if not phase_dir.exists():
        pytest.skip("phase 6 dir not present yet")
    plans = sorted(phase_dir.glob("06-*-PLAN.md"))
    if not plans:
        pytest.skip("no 06-*-PLAN.md files yet")
    for plan in plans:
        summary = plan.parent / plan.name.replace("-PLAN.md", "-SUMMARY.md")
        assert summary.exists(), f"Missing SUMMARY for {plan.name}"
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| gsd-sdk's `setActiveWorkstream` → `writeFileSync(planningRoot/active-workstream)` direct write | Same write, but gated by `em-proj state claim` shell-out that may refuse | Phase 6 (this phase) | Two sessions in the same project no longer silently clobber. |
| Older CJS path with `pickActiveWorkstreamAdapter` (session-scoped tmpdir file) | TS path with no session-scoping at all | gsd-sdk's TS port (pre-Phase-6 already shipped) | The TS port REGRESSED the session-scoping the CJS had. Phase 6 doesn't restore the tmpdir-per-session pattern; it replaces it with the Redis claim. |
| Hand-rolled `withPlanningLock` in CJS | Server-side Redis Lua claim | Phase 4 → Phase 6 | Atomicity moves from filesystem advisory locking to Redis Lua. |

**Deprecated/outdated:**
- `get-shit-done/bin/lib/planning-workspace.cjs:createSessionScopedPointerAdapter` — used by the CJS layer; NOT reached by `gsd-sdk query workstream.set`. Leave alone; Phase 6 doesn't touch the CJS surface.
- The `WORKSTREAM_SESSION_ENV_KEYS` array in `planning-workspace.cjs:16-29` — heuristic env-var probing for session-id. Phase 6 uses `CLAUDE_CODE_SESSION_ID` exclusively via em-proj's `identity.py`; the CJS heuristic is dead code for the consumer path.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Synchronous `spawnSync` inside an `async` `QueryHandler` is acceptable performance-wise (~150ms per `workstream.set`) | Pattern 1, Pitfall 1 | If `workstream.set` is called in a hot path elsewhere in gsd-sdk, this becomes a latency complaint. **Mitigation:** scan gsd-sdk callers; if only the explicit `/gsd-workstreams switch` slash-command path hits this, the assumption holds. |
| A2 | gsd-sdk's npm package upgrade pathway is `npm install -g get-shit-done-cc` and an upgrade WILL overwrite `sdk/dist/query/workstream.js` | Pitfall 6, Open Q G | If the user never upgrades, the hazard is theoretical. **Mitigation:** the structural test catches reversion regardless. |
| A3 | The user wants the loser of a `workstream.set` race to receive a non-zero process exit (exit 3) from `gsd-sdk` rather than an exit-0-with-error-body | Locked decisions discretion area | If the user prefers exit 0 + body, the JSON shape is correct but the exit code mapping in `sdk/dist/cli.js` (separate file) may need adjustment. **Mitigation:** ask the user during plan; default to exit 0 + body to match the existing `set: false, error: 'not_found'` convention in `workstreamSet`. |
| A4 | The area-key string `workstream.active` is acceptable (vs. `gsd.workstream` or similar) | Pattern 1 example code | Cosmetic only — the structural test pins whatever we choose. |
| A5 | The default TTL of 1800s (30min) for the workstream claim is appropriate | Pattern 1 example code | If a Claude Code session runs >30min without re-calling `workstream.set`, the claim auto-expires and a parallel session can take it. **Acceptable** — refresh-on-set is the design. |
| A6 | The pytest harness can resolve `gsd-sdk` on PATH the same way it resolves `em-proj` | Pattern 2 + Example 3 | If `gsd-sdk` isn't on PATH inside the pytest environment (unlikely — it's a global npm bin), tests skip cleanly. **Mitigation:** extend conftest.py's `redis_precheck` to also probe `shutil.which("gsd-sdk")` and `skip` cleanly. |
| A7 | No upstream pull-request to gsd-sdk is in scope for Phase 6 | Open Q G | If the user wants to upstream this, it's a separate workstream against a separate repo (gsd-sdk's source-of-truth presumably lives at github.com/gsd-build/get-shit-done per `package.json:42`). **Recommend:** treat as a follow-up issue. |

## Open Questions

1. **Q-A: Does the file write to `.planning/active-workstream` remain, or is it removed?**
   - What we know: The file is read by `getActiveWorkstream(projectDir)` in `workstream.ts:61-78` and other handlers (`workstreamGet`, `workstreamComplete`, `workstreamProgress`). If we remove the file write, those handlers all need to read from the Redis claim instead.
   - What's unclear: Whether Phase 6's scope is "claim-gate the write" or "replace the file write with claim semantics."
   - Recommendation: **Keep the file write as a non-authoritative shadow** (claim is authoritative; file is a UX cache for `getActiveWorkstream` reads to remain instant without a Redis call). The handler order is: (1) claim, (2) write file, (3) sync root mirror. File is only written when the claim was acquired (gates against clobber). The other read handlers continue to read the file. Smallest-surface change. Document the file as advisory in 06-SUMMARY.md.

2. **Q-B: When `em-proj` is not on PATH, what does `workstream.set` do?**
   - What we know: `spawnSync.error.code === 'ENOENT'` is detectable. The pre-Phase-6 behavior was unguarded direct write. Reverting to that on ENOENT preserves backward compatibility.
   - What's unclear: Whether silent fallback is preferable to a hard error.
   - Recommendation: **Silent fallback with a stderr warning** (matches the example code in Pattern 1). The user can always observe the warning; the cost of hard-failing is breaking gsd-sdk for users who don't have em-proj installed. The plan should call this out explicitly so the user can object before execution.

3. **Q-C: Where does the patched `workstream.js` actually live for the plan to point to?**
   - What we know: `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/sdk/dist/query/workstream.js` is the runtime-loaded file. The TS source `sdk/src/query/workstream.ts` is also shipped but NOT loaded (only the `.js` is consumed).
   - What's unclear: Whether the plan edits only the `.js`, only the `.ts`, or both.
   - Recommendation: **Edit BOTH for symmetry.** Only the `.js` matters for runtime, but keeping `.ts` and `.js` in lockstep makes the intent visible and makes future upstreaming (Open Q G) trivial. Add a structural test that checks both contain `em-proj`.

4. **Q-D: How does the structural test handle the gsd-sdk install path varying across machines?**
   - What we know: The user's install is at `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/`. On a different machine or Node version, the path differs.
   - Recommendation: **Resolve `gsd-sdk` via `shutil.which("gsd-sdk")` then walk `..` to find `lib/node_modules/get-shit-done-cc/sdk/dist/query/workstream.js`.** Skip the test cleanly if any step fails. Same xfail pattern as `test_phase_05_shape.py:SKILL_PATH` (primary + fallback resolution).

5. **Q-E: Should `tests/multiprocess/test_workstream_consumer_race.py` skip if `gsd-sdk` is missing?**
   - What we know: The fixture pattern in `conftest.py:53-83` (`redis_precheck`) already skips cleanly if `em-proj` is missing.
   - Recommendation: **Extend `redis_precheck` with a `gsd-sdk` probe** OR add a Phase 6 specific module-level skip. The latter is cleaner — Phase 6 is the only phase that depends on `gsd-sdk`.

6. **Q-F: Pytest fixture vs shell script for the SC#3 human-runnable demo?**
   - What we know: SC#3 requires "human-runnable in ~10s" and "parseable enough to assert against in CI." Pytest gives CI integration; a shell script gives a one-liner the user can run by hand.
   - Recommendation: **Pytest fixture is sufficient.** `bash scripts/test.sh multiprocess -k clobber_demo` runs in seconds and is human-readable. Adding a separate shell script is double-bookkeeping. If the user wants a script, it can be a thin wrapper that just invokes the same pytest selection.

7. **Q-G: Does Phase 6 ship an upstream PR to gsd-sdk?**
   - What we know: `package.json:42` declares `git+https://github.com/gsd-build/get-shit-done.git` as the source repo. No local checkout exists.
   - What's unclear: Whether the user wants em-proj's M1 to include upstream contribution scope.
   - Recommendation: **No.** Out of M1 scope. The patch lands in the npm-installed copy; the structural test catches reversion on upgrade. Track upstreaming as a follow-up issue separate from M1 completion.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `em-proj` CLI | Race tests, demo, structural test, the actual subprocess call | ✓ | as-built (Phase 4/5) | — |
| `gsd-sdk` CLI | Race tests, demo, the production consumer surface | ✓ | v1.41.2 | — |
| `node` | gsd-sdk runtime; structural test optional | ✓ | v22.13.1 | — |
| Redis | All claim ops | ✓ (precheck via `redis_precheck` fixture) | brew-managed | Test skip on unreachable (existing) |
| `pytest` + `uv` | Test execution | ✓ | as-shipped | — |
| Local source checkout of `gsd-sdk` | Open Q G (upstream PR) | ✗ | — | Edit npm-installed copy directly; track upstream as follow-up |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** Local gsd-sdk source — Phase 6 edits the npm-installed copy directly; upstream is out of scope.

## Project Constraints (from CLAUDE.md)

| Directive | Applies To Phase 6 | How |
|-----------|--------------------|-----|
| `scripts/test.sh` dispatcher, NEVER `uv run pytest` directly | New tests added to `tests/multiprocess/` and `tests/structural/` | All test invocations in the plan must reference `bash scripts/test.sh <sub>` |
| Structural tests under `tests/structural/test_<phase>_shape.py` | Phase 6 ships `test_phase_06_shape.py` | Same AST-pattern as Phase 5 |
| `scripts/verify-phase.sh 06` is the acceptance gate | Phase 6 verification | Same shape as Phase 3/4/5 |
| `scripts/git-ro.sh` for read-only git inspection | Plan/SUMMARY drafts may inspect git history | Use this wrapper for any git read; raw `git -C` is one allowlist entry per path |
| No `Co-Authored-By: Claude` trailers | All Phase 6 commits | Plan-author and executor BOTH must follow |
| Conventional Commits `feat(06-NN): ...` style | All Phase 6 commits | Mirrors Phase 4/5 |
| `.planning/` is a worktree on the `planning` branch | Editing PHASE 6 markdown files | Commits go through the `.planning/` worktree, not the main checkout |
| No top-level `\|` pipe in Bash invocations | Any helper scripts | Use `--tail N` on `scripts/test.sh`, never `| tail` |

## Validation Architecture

> `.planning/config.json` (or absence thereof) — Nyquist validation is treated as enabled.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x (existing) + `tests/conftest.py` fixtures |
| Config file | `pyproject.toml` (existing) |
| Quick run command | `bash scripts/test.sh multiprocess -k workstream` |
| Full suite command | `bash scripts/test.sh all` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CONSUMER-01 | `workstreamSet` in gsd-sdk shells out to `em-proj state claim` before writing | structural (file-content grep) | `bash scripts/test.sh structural -k test_gsd_sdk_workstream_js_contains_em_proj_shellout` | ❌ Wave 0 (new file) |
| CONSUMER-02 | Two concurrent sessions race; one wins, one gets structured held_by_another | multi-process | `bash scripts/test.sh multiprocess -k test_two_sessions_race_workstream_set_one_wins` | ❌ Wave 0 (new file) |
| SC#3 (clobber-vs-resolution side-by-side) | Old-path direct file write clobbers; new-path through gsd-sdk refuses loser | multi-process | `bash scripts/test.sh multiprocess -k clobber_demo` | ❌ Wave 0 (new file) |

### Sampling Rate
- **Per task commit:** `bash scripts/test.sh multiprocess -k workstream` (covers new race + demo tests)
- **Per wave merge:** `bash scripts/test.sh all` (full suite green)
- **Phase gate:** `bash scripts/verify-phase.sh 06` green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/multiprocess/test_workstream_consumer_race.py` — covers CONSUMER-02
- [ ] `tests/multiprocess/test_workstream_clobber_demo.py` — covers SC#3
- [ ] `tests/structural/test_phase_06_shape.py` — covers CONSUMER-01 (structural form: shellout presence in `workstream.js`)
- [ ] `conftest.py` extension OR module-level skip for gsd-sdk PATH probe (small)

*(No framework install needed — pytest/uv already in place.)*

## Security Domain

> `.planning/config.json` may or may not enable `security_enforcement`; defaulting to "enabled" per discovery rules.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | No new auth surface; session-id identity is inherited from Phase 3 |
| V3 Session Management | no | Same — session-id is `CLAUDE_CODE_SESSION_ID` |
| V4 Access Control | partial | Claim refusal IS access control. The dual-field `(session_id, project_hash)` check in `LUA_CLAIM_COMPARE_AND_DELETE` prevents one session releasing another's claim. **No new control needed — Phase 4 shipped this.** |
| V5 Input Validation | yes | `validate_key(area)` regex in `state/kv.py` validates the area string. gsd-sdk's `/^[a-zA-Z0-9_-]+$/` validates the workstream name BEFORE shelling out. Two-layer. |
| V6 Cryptography | no | No new crypto. |

### Known Threat Patterns for `{Node→Python subprocess shell-out}`

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Argument injection via workstream name | Tampering | gsd-sdk validates name as `/^[a-zA-Z0-9_-]+$/` BEFORE spawnSync (`workstream.ts:222-224`); em-proj re-validates via `validate_key`. Defense in depth. `[VERIFIED: workstream.ts:223, kv.py validate_key]` |
| Environment-variable exfiltration via child env | Information disclosure | `env: process.env` passes the full env. No new secrets handled. `CLAUDE_CODE_SESSION_ID` is the only sensitive-ish variable; em-proj already reads it. **Accept.** |
| PATH hijacking — `em-proj` resolved to attacker binary | Tampering | `spawnSync('em-proj', ...)` resolves via PATH. If an attacker controls PATH they already own the session. **Accept** as out of scope (single-user, single-machine per PROJECT.md constraints). |
| Holder-metadata disclosure via held_by_another response | Information disclosure | The `holder` dict in the gsd-sdk response contains `session_id` and `project_hash`. Same disclosure Phase 5 audited and accepted (`_HOLDER_DISCLOSURE_KEYS` redaction does NOT apply to claims — claims have no `boot_id`/`proc_start_epoch` to redact). **Accept.** `[VERIFIED: state/__init__.py:626 & 665 + verification report Phase 5]` |
| Claim leak into prod Redis db=0 via test misconfiguration | Integrity | Race tests inject `EM_PROJ_REDIS_DB=15` into the gsd-sdk subprocess env; em-proj's grandchild inherits. Existing `clean_db` flushdb pattern cleans test isolation. **Mitigate via test discipline (Pitfall 5).** |

## Sources

### Primary (HIGH confidence — direct read in this session)
- `/Users/emonical/projects/personal/ai-tools/em-proj/.planning/PROJECT.md` — locked decisions
- `/Users/emonical/projects/personal/ai-tools/em-proj/.planning/REQUIREMENTS.md` — CONSUMER-01, CONSUMER-02
- `/Users/emonical/projects/personal/ai-tools/em-proj/.planning/ROADMAP.md` — Phase 6 SC + dependencies
- `/Users/emonical/projects/personal/ai-tools/em-proj/.planning/STATE.md` — milestone progress
- `/Users/emonical/projects/personal/ai-tools/em-proj/CLAUDE.md` — project conventions
- `/Users/emonical/projects/personal/ai-tools/em-proj/src/em_proj/state/claim.py` — claim ops module
- `/Users/emonical/projects/personal/ai-tools/em-proj/src/em_proj/state/__init__.py` — claim/release/check verb wiring
- `/Users/emonical/projects/personal/ai-tools/em-proj/tests/conftest.py` — `multiproc_race`, `clean_db`, `redis_precheck` fixtures
- `/Users/emonical/projects/personal/ai-tools/em-proj/tests/multiprocess/test_claim_race.py` — pattern source for Phase 6 race test
- `/Users/emonical/projects/personal/ai-tools/em-proj/tests/structural/test_phase_05_shape.py` — pattern source for Phase 6 shape test
- `/Users/emonical/projects/personal/ai-tools/em-proj/scripts/verify-phase.sh` — verification gate dispatcher
- `/Users/emonical/projects/personal/ai-tools/em-proj/scripts/test.sh` — test dispatcher
- `/Users/emonical/projects/personal/ai-tools/em-proj/.planning/phases/04-long-lived-claims/04-VERIFICATION.md` — Phase 4 outcomes (claim contract)
- `/Users/emonical/projects/personal/ai-tools/em-proj/.planning/phases/05-global-state-skill-surface/05-VERIFICATION.md` — Phase 5 outcomes
- `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/sdk/src/query/workstream.ts` — TS source-of-truth (NOT runtime-loaded)
- `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/sdk/dist/query/workstream.js` — compiled runtime-loaded artifact (THE edit target)
- `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/sdk/src/query/workstream.test.ts` — existing vitest pattern in gsd-sdk
- `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/sdk/package.json` — gsd-sdk uses vitest
- `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/get-shit-done/bin/lib/workstream.cjs` — CJS layer (NOT reached by `gsd-sdk query workstream.set`)
- `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/get-shit-done/bin/lib/planning-workspace.cjs` — older session-scoped pointer adapter (NOT reached by TS handler)
- `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/commands/gsd/workstreams.md` — slash command documentation (invocation shape)
- `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/bin/gsd-sdk.js` — entry-point shim (verifies `child_process.spawnSync` is the idiom)
- `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/package.json` — version 1.41.2

### Secondary (MEDIUM confidence — runtime probes)
- `gsd-sdk --version` → `gsd-sdk v1.41.2`
- `gsd-sdk query workstream.get --raw --cwd /tmp` → `{"active":null,"mode":"flat"}`
- `gsd-sdk query workstream.set --raw --cwd /tmp` → `{"set":false,"reason":"name required..."}`
- `em-proj state claim-list --mine --json` → `{"schema_version":"1","status":"ok","data":{"items":[]}}`
- `node --version` → `v22.13.1`
- `stat -f "%Sm" .../package.json` → install mtime `May 11 12:25:36 2026`

### Tertiary (LOW confidence — none)
- No web sources consulted; entire phase is verifiable from local source trees.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every component is on this machine, verified by direct read or runtime probe.
- Architecture: HIGH — gsd-sdk handler shape, em-proj claim contract, and test harness pattern all verified.
- Pitfalls: HIGH — each pitfall has either a verified contract source or a documented mitigation pattern from prior phases.
- Open Questions: MEDIUM — most have a default recommendation; A2/G are forward-looking and depend on the user's intent.

**Research date:** 2026-05-26
**Valid until:** 2026-06-09 (stable — em-proj contracts shipped, gsd-sdk install pinned at 1.41.2; only risk is an `npm install -g get-shit-done-cc` upgrade in the interim)
