# Retrospective: em-proj

A living retrospective across milestones. Newest milestone first.

## Milestone: v1.0 — em-proj state primitive

**Shipped:** 2026-06-07
**Phases:** 7 | **Plans:** 30 | **Timeline:** 2026-05-16 → 2026-06-01

### What Was Built

A Redis-backed cross-session coordination CLI: KV store with first-class TTL,
process-scoped advisory locks (with a `--hold -- <cmd>` auto-release wrapper),
long-lived refreshable claims with holder metadata, a `/em-global-state` skill
read+escape-hatch surface, a `gsd-sdk workstream.set` consumer that eliminates
two-session pointer clobber, and a project-scoped reservation registry namespaced
by upstream-repo identity for cross-clone coordination. All validated by a
fork+exec multi-process race harness landed first.

### What Worked

- **TDD-first harness ordering.** Building the multi-process race harness as the
  first deliverable (before any locking code) paid off — advisory-locking bugs
  surface only under real concurrency, and the harness caught them throughout.
- **Pure-ops modules + thin verb wiring.** Splitting each primitive into a pure
  `state/<x>.py` ops module plus typer verb wiring kept unit tests fast and the
  race tests focused on the CLI boundary.
- **Structural AST shape tests.** `tests/structural/test_phase_NN_shape.py`
  encoded each plan's acceptance criteria as runtime assertions, replacing dozens
  of ad-hoc grep/wc checks with one allowlisted dispatcher call.
- **Per-phase code-review fix passes.** Phases 4 and 5 ran explicit CR/WR fix
  series that hardened error paths and stale-filter races before phase close.

### What Was Inefficient

- **PR/branch hygiene at the milestone boundary.** Phase 7 work was committed
  directly to local `main` AND mirrored onto stacked `pr/*` branches that were
  never reviewed, leaving an inconsistent close state (unpushed main, dead PR
  refs, no MILESTONES.md). Closing required a force-push reconciliation.
- **Agent-worktree pile-up.** Parallel `Agent(isolation="worktree")` execution
  left ~17 `worktree-agent-*` branches and a locked worktree behind (a known
  hazard); `git worktree prune` skips locked entries, so they accumulate.
- **STATE.md drift.** STATE.md described "3 stacked PRs pending merge" that no
  longer matched reality at close time — manual state files go stale silently.

### Patterns Established

- Planning artifacts on an orphan `planning` branch via git worktree; `.planning/`
  gitignored on `main`. Commits to planning artifacts must run from inside the
  worktree (not via SDK commit from the main root, which stages nothing).
- Dispatcher scripts (`scripts/test.sh`, `verify-phase.sh`, `git-ro.sh`) with
  exact-match allowlist entries instead of wildcard tool grants.
- gsd-sdk integration by patching the npm-installed dist + guarding with an
  xfail-on-reversion structural test, rather than forking gsd-sdk.

### Key Lessons

1. Decide the PR-vs-direct-commit flow for a phase BEFORE committing — don't
   commit to `main` and open review PRs for the same commits.
2. Reconcile `origin/main` and close/merge PRs as part of the phase, not deferred
   to milestone close.
3. Sweep agent worktrees/branches at phase end while the context is fresh.

### Cost Observations

- Model mix: primarily opus/sonnet (GSD `balanced` profile).
- Notable: structural shape tests + the race harness front-loaded effort but made
  every later phase's verification cheap and deterministic.

---

## Cross-Milestone Trends

| Milestone | Phases | Plans | Timeline | Requirements |
|-----------|--------|-------|----------|--------------|
| v1.0 | 7 | 30 | ~16 days | 29/29 ✓ |

_Trends will accumulate as future milestones complete._
