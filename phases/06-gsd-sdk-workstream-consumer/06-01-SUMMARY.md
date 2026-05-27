---
phase: 06-gsd-sdk-workstream-consumer
plan: "01"
subsystem: integration
tags: [gsd-sdk, em-proj, child_process, spawnSync, workstream, claim, redis, node]

# Dependency graph
requires:
  - phase: 04-long-lived-claims
    provides: em-proj state claim CLI (exit 0/1/3, --json, --ttl, workstream.active area)
  - phase: 05-global-state-skill-surface
    provides: em-proj on PATH via global install
provides:
  - "gsd-sdk workstreamSet handler with em-proj claim gate before setActiveWorkstream()"
  - "ENOENT/status-1/status-3/status-0 branch coverage in both .js and .ts"
  - "spawnSync('em-proj', ['state', 'claim', '--ttl', '1800', '--json', 'workstream.active'], {cwd: projectDir}) pattern"
affects: [06-02-PLAN, 06-03-PLAN, test_phase_06_shape]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Node.js spawnSync inside async QueryHandler: sync Python subprocess from async JS handler"
    - "cwd: projectDir in spawnSync to align em-proj project_hash with gsd-sdk --project-dir"
    - "Four-branch claim result handling: ENOENT fallback, status-3 held_by_another, status-1 claim_refused, status-0 proceed"

key-files:
  created: []
  modified:
    - /Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/sdk/dist/query/workstream.js
    - /Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/sdk/src/query/workstream.ts

key-decisions:
  - "Cross-repo edit: patched npm-installed files directly (no local gsd-sdk checkout; no upstream PR in M1 scope)"
  - "File write retained as advisory shadow post-claim (Q-A): claim is authoritative, .planning/active-workstream is UX cache"
  - "ENOENT silent fallback with stderr warning (Q-B): preserves backward compat for users without em-proj"
  - "Both .js and .ts patched in lockstep (Q-C): .ts is documentation/symmetry only, .js is the runtime artifact"
  - "cwd: projectDir passed to spawnSync to fix Pitfall 3: em-proj project_hash must derive from gsd-sdk's --project-dir, not Node process.cwd()"
  - "ESM import syntax used for child_process (node:child_process) to match the file's module type"

patterns-established:
  - "Pattern: Node spawnSync inside async QueryHandler for em-proj subprocess integration"
  - "Pattern: Four-branch claim result dispatch (ENOENT/3/1/0) for any future claim-gated handler"

requirements-completed: [CONSUMER-01]

# Metrics
duration: 20min
completed: 2026-05-27
---

# Phase 06 Plan 01: gsd-sdk Workstream Consumer Summary

**gsd-sdk workstreamSet patched with spawnSync('em-proj', ['state', 'claim', ...]) gate before setActiveWorkstream(), delivering CONSUMER-01 end-to-end via direct npm-installed file edit**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-05-27T16:26:00Z
- **Completed:** 2026-05-27T16:46:38Z
- **Tasks:** 3 (all complete)
- **Files modified:** 2 (both outside em-proj git repo)

## Accomplishments

- Patched `sdk/dist/query/workstream.js` (runtime-loaded) with the em-proj claim gate: `spawnSync('em-proj', [...])` inserted before `setActiveWorkstream()` in `workstreamSet`
- Patched `sdk/src/query/workstream.ts` (TS source-of-truth) identically for Q-C lockstep
- Live smoke test confirmed: `gsd-sdk query workstream.set smoke-ws --project-dir /tmp/em-proj-smoke-06` with `CLAUDE_CODE_SESSION_ID=smoke-session-06` and `EM_PROJ_REDIS_DB=15` exits 0, returns `{"active":"smoke-ws","set":true,"mirror_synced":true}`, and creates `state:claim:-private-tmp-em-proj-smoke-06:workstream.active` in Redis db 15

## Task Commits

This plan produced NO em-proj git commits. All edits are to npm-installed files at the npm global path (`/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/`) which are outside any git repository. The only commit is this SUMMARY.md.

1. **Task 1: Patch sdk/dist/query/workstream.js** - no commit (cross-repo file, not git-tracked)
2. **Task 2: Mirror patch into sdk/src/query/workstream.ts** - no commit (cross-repo file, not git-tracked)
3. **Task 3: Live smoke test** - no commit (behavioral verification only)

**Plan metadata commit:** See SUMMARY.md commit on planning branch.

## Files Created/Modified

- `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/sdk/dist/query/workstream.js` — Added `import { spawnSync } from 'node:child_process';` at top; inserted four-branch claim gate in `workstreamSet` between `not_found` early return and `setActiveWorkstream()` call
- `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/sdk/src/query/workstream.ts` — Same patch in TypeScript form with `(claimResult.error as NodeJS.ErrnoException).code` type annotation; `holder: any` for loose-typed consistency with surrounding code

## Patch Shape (evidence excerpt)

The key insertion in `workstreamSet` (identical structure in both .js and .ts):

```javascript
// ─── em-proj claim gate (Phase 6 CONSUMER-01) ─────────────────────────
const claimResult = spawnSync(
    'em-proj',
    ['state', 'claim', '--ttl', '1800', '--json', 'workstream.active'],
    {
        env: process.env,
        encoding: 'utf-8',
        stdio: ['ignore', 'pipe', 'pipe'],
        cwd: projectDir,
    }
);
if (claimResult.error && claimResult.error.code === 'ENOENT') {
    process.stderr.write('gsd-sdk: em-proj not on PATH; falling back to ...\n');
} else if (claimResult.status === 3) {
    // ... return { data: { set: false, error: 'held_by_another', ... } }
} else if (claimResult.status === 1) {
    // ... return { data: { set: false, error: 'claim_refused', ... } }
}
// claimResult.status === 0 → fall through to setActiveWorkstream
```

All four branches confirmed present in both files:
- ENOENT (em-proj not on PATH) → stderr warning + legacy fallback
- status 3 (held_by_another) → structured refusal envelope, no file write
- status 1 (claim_refused/anonymous) → claim_refused envelope, no file write
- status 0 (taken/refreshed) → proceed to setActiveWorkstream + syncRootStateMirror

## Decisions Made

- **ESM import syntax**: The compiled `.js` uses ESM `import` (not CJS `require`) because `sdk/package.json` has `"type": "module"`. Used `import { spawnSync } from 'node:child_process'` matching the `node:fs` / `node:path` prefix convention.
- **cwd: projectDir fix**: Pitfall 3 mitigation. Without `cwd: projectDir`, em-proj would derive `project_hash` from Node's `process.cwd()` (the gsd-sdk bin's cwd), not from the user's `--project-dir`. With this fix, claim keys are scoped to the correct project. Verified in smoke test: claim appeared under `-private-tmp-em-proj-smoke-06` hash matching `/private/tmp/em-proj-smoke-06`.
- **--project-dir flag**: The plan's smoke test used `--cwd`; gsd-sdk's query CLI actually uses `--project-dir`. Corrected during execution.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] --cwd → --project-dir in smoke test invocation**
- **Found during:** Task 3 (smoke test)
- **Issue:** Plan specified `gsd-sdk query workstream.set smoke-ws --raw --cwd /tmp/em-proj-smoke-06`, but `gsd-sdk query` subcommand parser only recognizes `--project-dir` (not `--cwd`). Using `--cwd` left projectDir as `process.cwd()`, causing `not_found` because the workstream dir was at `/tmp/.../workstreams/smoke-ws` but projectDir resolved to the wrong path.
- **Fix:** Used `--project-dir /tmp/em-proj-smoke-06` instead.
- **Files modified:** None (smoke test is transient; no file change needed)
- **Verification:** `gsd-sdk query workstream.set smoke-ws --project-dir /tmp/em-proj-smoke-06` returned `{"set": true}`.
- **Committed in:** N/A (inline smoke test fix)

---

**Total deviations:** 1 auto-fixed (Rule 1 — smoke test arg correction)
**Impact on plan:** Trivial flag name correction. The underlying patch and claim gate are unaffected.

## Issues Encountered

- macOS symlink: `/tmp` → `/private/tmp` caused the claim key to be stored under `-private-tmp-em-proj-smoke-06` (the resolved canonical path). This is expected behavior — em-proj uses `os.getcwd()` which returns the canonical path after symlink resolution. No action needed; the gate functions correctly.

## Cross-Repo Edit Notice

**These file changes are NOT tracked in any git repository.** The patched files live at the npm global install path:
- `...get-shit-done-cc/sdk/dist/query/workstream.js`
- `...get-shit-done-cc/sdk/src/query/workstream.ts`

Any future `npm install -g get-shit-done-cc` will overwrite both files, silently reverting the Phase 6 gate.

**Recovery:** Re-run Plan 06-01's Tasks 1 and 2 (both are idempotent file edits). The exact patch is in this SUMMARY and in `06-RESEARCH.md §Pattern 1`.

**Detection:** Plan 06-03's `test_phase_06_shape.py::test_gsd_sdk_workstream_js_contains_em_proj_shellout` greps the npm-installed JS for `'em-proj'`. On reversion, this structural test xfail-fails visibly.

## Known Stubs

None - all branches are wired to their correct behavior.

## Threat Flags

No new threat surface introduced beyond what is documented in the plan's `<threat_model>`. The spawnSync uses `cwd: projectDir` (T-06-01 mitigation), `env: process.env` (T-06-03 mitigation), and the `workstream.active` area string is a hardcoded literal (not user-controlled).

## Next Phase Readiness

- Plan 06-02 (multi-process race test) can now proceed: `gsd-sdk query workstream.set` is claim-gated
- Plan 06-03 (structural shape test) can audit both patched files via grep
- The em-proj claim gate is live end-to-end; Plans 06-02 and 06-03 prove correctness under concurrent load

---
*Phase: 06-gsd-sdk-workstream-consumer*
*Completed: 2026-05-27*
