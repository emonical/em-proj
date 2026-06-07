# Milestones: em-proj

A historical record of shipped versions. Newest first.

---

## v1.0 — em-proj state primitive ✅

**Shipped:** 2026-06-07
**Phases:** 7 | **Plans:** 30 | **Timeline:** 2026-05-16 → 2026-06-01 (~16 days)
**Code:** ~3,900 LOC Python (11 modules) | **Tests:** ~10,900 LOC (unit + multiprocess + structural)
**Git:** root `1c3b040` → `ae27bb6` (`main`, tagged `v1.0`)

**Delivered:** `em-proj` shipped as an installable Python CLI with the `state`
primitive proven end-to-end — a Redis-backed coordination layer that lets any
Claude Code session or sub-agent ask "is it safe to edit X, or is someone else
working there?" and get a structured, parseable answer.

**Key accomplishments:**
1. **Multi-process test harness first** — fork+exec children racing at the CLI boundary, landed before any locking code (TDD foundation), on persistent AOF Redis
2. **`em-proj` CLI + KV primitive** — typer dispatch, TTY/`--json` output with `schema_version`, semantic exit codes (0/1/2/3), first-class `--ttl`
3. **Identity + advisory locks** — `CLAUDE_CODE_SESSION_ID` resolution, `{pid, proc_start_epoch, boot_id}` stale-detection composite, `lock --hold -- <cmd>` auto-release wrapper
4. **Long-lived claim model** — refreshable TTL claims with holder metadata, anonymous-claim refusal
5. **`/em-global-state` skill** — sub-agent-callable read + escape-hatch surface with confirmation-gated writes
6. **gsd-sdk workstream consumer** — `workstream.set` writes through `em-proj state claim` via shell-out; two-session clobber eliminated end-to-end
7. **Project-scoped reservation registry** — namespaced by `upstream_identity` so sibling clones coordinate shared resources (migrations, db ports) without colliding

**Requirements:** 29/29 v1 requirements validated. No gaps at close.

**Archives:** `milestones/v1.0-ROADMAP.md` · `milestones/v1.0-REQUIREMENTS.md` · `milestones/v1.0-phases/`

**Deferred to future milestones:** session registry (M2), inter-session messaging (M3), workstream handoff (M4+), memory/settings write coordination.
