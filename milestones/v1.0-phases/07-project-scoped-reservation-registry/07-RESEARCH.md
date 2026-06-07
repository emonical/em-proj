# Phase 7: Project-Scoped Reservation Registry — Research

**Researched:** 2026-05-31
**Domain:** Cross-clone resource reservation namespacing; upstream-repo identity resolution; reservation-list skill surface
**Confidence:** HIGH

## Summary

Phase 7 adds a SECOND identity-namespace on top of the Phase 4 claim primitive. Where Phase 4 claims and Phase 6 workstreams namespace by the per-clone `project_hash` (derived from `os.getcwd()`), Phase 7 reservations namespace by a stable `upstream_identity` derived from `git remote get-url origin`. The same upstream repo, cloned three times into sibling directories, shares ONE reservation namespace — so a migration version reserved in clone A is visible to (and refused by) clones B and C.

The work decomposes into four small, well-bounded surfaces:

1. **`upstream_identity` resolver** in `src/em_proj/identity.py` — `git remote get-url origin` invoked via `subprocess.run` (NOT shell), canonicalized to `host:owner/repo` (lowercased, `.git` stripped, no trailing slash), with explicit fall-back to `resolve_project_hash()` when no `origin` remote exists. Standard library only — no `giturlparse` dependency. The canonicalization rules needed are small enough that pulling a third-party dep increases risk more than it reduces it (and `giturlparse` is a single-author repo with a non-trivial regex stack).

2. **`reserve` ops module** in `src/em_proj/state/reserve.py` — a SIBLING of `claim.py` with the same Lua refresh-or-take / compare-and-delete / check scripts but a different `KEY_PREFIX` (`state:reserve:`) and a different namespace component (`upstream_identity` instead of `project_hash`). The holder dict gains TWO new fields: `upstream_identity` and `workstream`. Everything else is structurally identical to `claim.py` — same exceptions, same Lua atomicity model, same TTL ranges.

3. **Verb wiring** in `src/em_proj/state/__init__.py` — a NEW `reserve` verb (NOT a flag on `claim`), plus a NEW `reserve-list` verb that mirrors `claim-list` but scopes to the upstream identity. The TTY-prompt-on-missing-workstream logic lives at the verb layer (not in `reserve.py`), so the pure-ops module remains free of typer/stdin coupling. Recommended sugar: `em-proj state check --upstream <area>` ADDS a flag to the existing `check` verb to query the reserve namespace from the same surface (preferred over a separate `check-reserve` verb).

4. **Skill extension** — ADD a `reservations` verb to the existing `~/.claude/skills/em-global-state/SKILL.md` rather than creating a new `/em-check-state` skill. The existing skill is the natural home; adding one verb is far cheaper than maintaining a parallel skill file with overlapping concerns. The user's verbatim phrasing was `/em-check-state` but the rationale was "any session can ask from any clone" — a `reservations` verb on `em-global-state` delivers that with one fewer surface to remember.

**Primary recommendation:** Land Phase 7 as five plans in three waves, following the Phase 4/Phase 6 shape exactly:
- **Wave 1 (parallel)**: Plan 07-01 = `upstream_identity` resolver in `identity.py` + unit tests. Plan 07-02 = `reserve.py` pure-ops module + unit tests (depends on Plan 07-01 for the resolver import, but tests can mock the resolver independently; treat 07-02 as depending on 07-01).
- **Wave 2 (after Wave 1)**: Plan 07-03 = `reserve` + `reserve-list` verb wiring in `state/__init__.py` + `check --upstream` flag + multi-clone race tests.
- **Wave 3 (after Wave 2)**: Plan 07-04 = SKILL.md `reservations` verb extension. Plan 07-05 = structural shape test + `bash scripts/verify-phase.sh 07` gate.

Actually 07-01 and 07-02 are sequentially dependent (reserve.py imports the resolver), so the cleanest decomposition is two-wide-one-wave-only: Plan 07-01 (identity resolver + reserve.py pure-ops, single file pair) → Plan 07-02 (verbs + multi-clone tests) → Plan 07-03 (skill + structural). The planner should pick the decomposition that matches em-proj's recent phase cadence (Phase 6 used 3 plans across 2 waves; Phase 5 used 5 plans across 4 waves).

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| RESERVE-01 | Reservations namespace by a stable `upstream_identity` derived from `git remote get-url origin` (slug or hash; project-agnostic, shared across sibling clones of the same upstream repo). Distinct from the per-clone `project_hash` used by Phase 4 claims and Phase 6 workstreams. | `[VERIFIED: src/em_proj/identity.py:106-148 — resolve_session_id() and resolve_project_hash() are the existing identity helpers; both stateless, no subprocess. The new upstream_identity resolver will sit alongside them. Phase 3 T-3-01-03 explicitly REJECTED the git-toplevel shell-out for project_hash for security reasons; Phase 7 must address the same threat for git-remote-get-url-origin.]` Recommended canonical form: `host:owner/repo` (lowercased, `.git` stripped). See Open Question A. |
| RESERVE-02 | At reservation-claim time, the holder dict auto-stamps `workstream` (read from the calling clone's `workstream.active` Phase 6 claim). Holders carry `{session_id, project_hash (caller's local), upstream_identity, workstream, reason, claimed_at, expires_at}`. | `[VERIFIED: src/em_proj/state/claim.py:233-275 — current claim holder is 5 fields. Phase 7 adds two: upstream_identity (scope namespace) AND workstream (auto-stamped). The workstream is read from a separate claim key `state:claim:<project_hash>:workstream.active` via `claim_check("workstream.active")` against the per-clone namespace.]` `[VERIFIED: src/em_proj/state/__init__.py:439-505 — Phase 4's claim verb pattern is the template for Phase 7's reserve verb.]` See Open Questions B and H. |
| RESERVE-03 | `/em-check-state` (no args) auto-resolves `upstream_identity` from current `cwd`'s `git remote get-url origin` and returns ALL reservations against that identity, grouped by category prefix (the part of `<category>.<resource>` before the first dot). | `[VERIFIED: src/em_proj/state/claim.py:413-487 — claim_list_by_prefix already does SCAN MATCH `state:claim:<project_hash>:*`. Phase 7 needs reserve_list_by_prefix doing SCAN MATCH `state:reserve:<upstream_identity>:*`. Grouping by category prefix is a verb-layer concern, not a pure-ops one.]` See Open Question E for skill placement. |
| RESERVE-04 | `/em-check-state --category <name>` filters to one category; `--upstream <url-or-identity>` overrides cwd-based resolution to query reservations against a different upstream from anywhere. | The `--category` filter is a post-scan dict-comprehension at the verb layer (cheap, no Redis-side selectivity needed for M1 cardinality). The `--upstream` override re-uses the canonicalization function — if the user passes a URL it gets canonicalized, if they pass an already-canonical identity it round-trips unchanged. |
| RESERVE-05 | `em-proj state reserve <category>.<resource> [--reason <text>] [--ttl <secs>] [--workstream <name>]` — sugar over `claim` that uses `upstream_identity` instead of `project_hash` and auto-stamps `workstream`. When `workstream.active` is unset AND `--workstream` not passed: TTY prompts; non-TTY exits 1 with actionable error. No silent heuristic fallback. | The verb is NEW (not a flag on `claim`) per Open Question C. The TTY prompt is gated by `sys.stdin.isatty() AND sys.stdout.isatty()` — same dual-isatty pattern as Phase 3 `lock --warn` (`[VERIFIED: state/__init__.py:344-352]`). The non-TTY error message: `"workstream unresolved — set it via `gsd-sdk query workstream.set <name>` or pass `--workstream <name>`"`. |
</phase_requirements>

<user_constraints>
## User Constraints (from objective + ROADMAP.md + REQUIREMENTS.md — no CONTEXT.md exists for Phase 7)

### Locked Decisions (from in-conversation alignment, do NOT re-litigate)

1. **Identity anchor = upstream remote URL.** `git remote get-url origin` → canonicalize → hash/slug. NOT configurable per-clone; auto-resolved from cwd. Falls back to per-clone `project_hash` ONLY if there is no `origin` remote.
2. **Workstream auto-resolution.** Read from the existing Phase 6 `workstream.active` claim (per-clone, project_hash-namespaced). If unset on TTY → prompt; if unset on non-TTY → exit 1 with actionable error. NO basename-stripping heuristic, NO env-var fallback, NO config file lookup.
3. **Two-namespace coexistence.** The Phase 6 `workstream.active` claim stays in the `state:claim:<project_hash>:` namespace (per-clone). Phase 7 reservations live in a NEW `state:reserve:<upstream_identity>:` namespace (shared across siblings). Both work side-by-side; NO migration of existing claims.
4. **Skill surface = NEW verb on existing `/em-global-state` skill** — researcher recommendation. NOT a separate `/em-check-state` skill. Rationale: the existing skill is the natural home for cross-session state reads; adding one verb (`reservations`) is cheaper than maintaining a parallel skill file.
5. **No upstream PR to gsd-sdk required.** Self-contained to em-proj — no changes to `sdk/dist/query/workstream.js` or related gsd-sdk artifacts. Phase 6's consumer wiring stays as-is.
6. **`reserve` is a NEW verb, NOT a flag on `claim`.** `em-proj state reserve` and `em-proj state claim` are siblings with different semantics (different namespace, auto-stamped workstream, prompts on missing workstream). See Open Question C.
7. **Test execution flows through `bash scripts/test.sh`** — never `uv run pytest` directly (project CLAUDE.md).
8. **Structural tests go under `tests/structural/test_phase_07_shape.py`** — same pattern as Phase 3/4/5/6.
9. **Phase verification flows through `bash scripts/verify-phase.sh 07`.**
10. **Never append `Co-Authored-By: Claude` trailers** to commit messages (global rule + project CLAUDE.md).

### Claude's Discretion

- Exact canonical form of `upstream_identity` (recommendation: `host:owner/repo` lowercased; see Open Q-A).
- Whether to add `giturlparse` as a dep or roll a small canonical-form function (recommendation: stdlib-only — see Open Q-A).
- Whether to verbatim-or-hash the canonical form in the Redis key (recommendation: VERBATIM — see Open Q-B).
- Whether `check` gets a `--upstream` flag or `check-reserve` becomes a new verb (recommendation: `--upstream` flag on existing `check` — see Open Q-D).
- Whether `reserve` enforces a separate area-name regex or re-uses `validate_key` from kv.py (recommendation: re-use `validate_key` — Phase 4's claim verb does, and the same regex `^[a-zA-Z0-9_.\-/]+$` already accepts `migrations.v200` and `db.ports`).
- Default TTL for reservations (recommendation: `TTL_DEFAULT = 1800` matching Phase 4 — same long-lived semantics).
- Exact prompt copy for TTY-missing-workstream (recommendation: see §Pattern 4 below).
- Plan decomposition (3 plans / 2 waves vs 5 plans / 3 waves) — Phase 6 used 3/2 cleanly; Phase 5 used 5/4. Recommend 3-plan layout matching Phase 6.
- Whether to test for the SPECIFIC race "two clones in distinct directories with distinct fake `.git/config` files race a reserve" (recommendation: YES — see §Pattern 5 + Open Q-I).
- Whether `reserve-list` (the verb form) renders TTY output grouped by category by default (recommendation: YES on TTY; JSON envelope returns a flat list with `category` injected as a synthesized field).

### Deferred Ideas (OUT OF SCOPE)

- **Reservations against ARBITRARY external resources beyond upstream-repo identity.** E.g., reserving a hostname, a shared S3 prefix, a Datadog dashboard — out of scope. Phase 7's identity anchor is git-remote-origin only.
- **Reservation TTL refresh from a different clone of the same upstream.** Reservation refresh follows claim semantics: same `(session_id, upstream_identity)` refreshes; different session_id with same upstream conflicts. Cross-clone refresh "transfer" is a separate workflow (M2+).
- **`git remote get-url <other-remote-name>` configurability.** Phase 7 reads `origin` only. Multi-remote repos with a non-`origin` upstream (rare but valid) are out of scope for M1.
- **Reservation namespacing by both `upstream_identity` AND a branch/tag suffix.** Future work — for now, reservations are per-repo-not-per-branch.
- **Persistence beyond Redis (e.g., a flat-file shadow of reservations).** Same posture as Phase 4 claims — Redis is the source of truth; durability comes from AOF (REDIS-01).
- **Cross-machine sync** (single-machine, single-user target per PROJECT.md constraints).
- **A separate `/em-check-state` skill file.** Per Locked Decision #4, the verb lives on the existing `em-global-state` skill.
</user_constraints>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| `git remote get-url origin` invocation | `em_proj.identity.resolve_upstream_identity()` | — | Identity resolvers all live in `identity.py` — keeps the resolver co-located with `resolve_session_id` and `resolve_project_hash`. See Open Q-G. |
| URL canonicalization (SSH ↔ HTTPS ↔ trailing-slash) | `em_proj.identity._canonicalize_upstream_url()` (module-private helper) | — | Pure function, no Redis, no subprocess — unit-testable in isolation against a curated input/output table. See Open Q-A. |
| Reserve ops (take / release / check / list) | `em_proj.state.reserve.py` | — | Pure-ops module mirroring `claim.py` 1:1 except for the key namespace and the two extra holder fields. Inherits Phase 4's Lua atomicity model. |
| Verb wiring (typer commands) | `em_proj.state.__init__` | — | Same D-14 thin-shell discipline as Phase 4. The `reserve` verb is a sibling of `claim`. |
| `workstream.active` lookup at reserve time | Verb layer (`state/__init__.py` reserve verb) calls `claim_check("workstream.active")` from `em_proj.state.claim` | — | The verb owns user-flow decisions (read workstream, prompt if missing, pass `--workstream` override). The `reserve.py` pure-ops module receives the resolved workstream as a parameter. See Open Q-H. |
| TTY prompt for missing workstream | Verb layer | — | `reserve.py` MUST NOT import `sys` for stdin/stdout — same purity discipline as `claim.py`. The verb tests `sys.stdin.isatty() and sys.stdout.isatty()` and either prompts or exits 1. See Open Q-F. |
| Two-namespace coexistence assertion | `tests/structural/test_phase_07_shape.py` | — | Structural test asserts `KEY_PREFIX` strings in `claim.py` and `reserve.py` are different and that `claim.py` has no reference to `upstream_identity`. |
| Multi-clone race testing | `tests/multiprocess/test_reserve_race.py` | — | Mirrors Phase 4's `test_claim_race.py` pattern but with per-child `cwd=` pointing at distinct temp dirs each containing a fake `.git/config` with the SAME `[remote "origin"]` block. See Open Q-I. |
| `/em-global-state reservations` skill verb | `~/.claude/skills/em-global-state/SKILL.md` (extension) | — | Adds one verb subsection to the existing 6 in SKILL.md. No new skill file. |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `subprocess.run` (Python 3.12 stdlib) | 3.12 | Invoke `git remote get-url origin` with `shell=False` and an explicit argv list | Same pattern Phase 6 already validated for cross-tool shellouts. Avoids PATH-injection threat T-3-01-03 (Phase 3 explicitly rejected shell-out for project_hash; reserved this surface for Phase 7 to address.) `[VERIFIED: src/em_proj/identity.py:39-46 — Phase 3 module docstring explicitly documents the security rejection of shell-out, leaving a clear path for Phase 7 to do it correctly.]` |
| `em-proj state claim` Lua scripts (refactored) | Phase 4 | Server-side atomicity for refresh-or-take + compare-and-delete on the reservation HASH | Already battle-tested in Phase 4. Phase 7 copies the three Lua scripts verbatim into `reserve.py` with one addition: the holder dict now has TWO more fields (`upstream_identity` + `workstream`). The Lua MUST be updated to HSET all 7 fields atomically and to compare on `session_id` AND `upstream_identity` (NOT `project_hash`) for refresh/release authorization. `[VERIFIED: src/em_proj/state/claim.py:103-165 — three Lua scripts, all in-process atomic.]` |
| `pytest` + `tests/conftest.py` fixtures | matches existing Phase 4/5/6 | Provides `clean_db`, `multiproc_race`, EM_PROJ_REDIS_DB=15 injection | Already shipped; no change needed. `[VERIFIED: tests/conftest.py:1-160]` |
| `subprocess.Popen` with per-child `cwd=` | 3.12 stdlib | Multi-clone simulation in race tests — each child runs in a distinct temp directory containing a fake `.git/config` | New pattern for em-proj (Phase 4-6 used per-child env injection but kept `cwd` identical). The fake `.git/config` is a tiny INI file with `[remote "origin"]\n\turl = <url>` — git understands this without any other git state. See §Pattern 5. |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `redis-py` | as-installed (Phase 4) | Direct Redis assertions in race tests (`client.exists(key)`, `client.hgetall(key)`) | Same pattern as Phase 4 tests. `[VERIFIED: tests/multiprocess/test_claim_race.py:77-84]` |
| `pathlib.Path` + `tempfile.TemporaryDirectory` (Python 3.12 stdlib) | 3.12 | Fake-clone directory setup in race tests | pytest `tmp_path` fixture handles this automatically — see §Pattern 5. |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| stdlib-only canonicalization | `giturlparse` library | `giturlparse` 0.14.0 (Apache 2.0, released 2025-10-22) is a real library with parsing support for SSH/HTTPS/git protocols and host/owner/repo extraction. `[VERIFIED: https://github.com/nephila/giturlparse — README confirms attributes.]` BUT: (1) introduces a transitive dep for ~30 lines of canonicalization logic; (2) the library hasn't published a stable 1.x; (3) em-proj already documents `psutil` as its only non-Redis dep and a stdlib-first culture; (4) we need EXACT canonical form control (lowercased, `.git` stripped, no port preserved) which the library does NOT guarantee out of the box. **Recommend: stdlib-only, ~30 LOC function with an explicit input/output table baked into tests.** `[ASSUMED: no breaking changes in giturlparse since 0.14.0 — but the version landscape for "0.x"-stamped libraries is inherently unstable.]` |
| New `reserve` verb | `claim --upstream` flag | `claim` semantics would diverge: (a) different key prefix, (b) auto-stamped workstream, (c) TTY-prompts on missing workstream — three behavior deltas in one verb makes the help text confusing. New verb is clearer. **Recommend: new `reserve` verb.** |
| New `reserve-list` verb | Extend `claim-list --upstream` | Same argument as above — `claim-list` returns claims (per-clone scope); `reserve-list` returns reservations (per-upstream scope). Different cardinality, different namespace, different default filters. **Recommend: new `reserve-list` verb.** |
| `check --upstream` flag | New `check-reserve` verb | `check` is a single-key read with a clean exit-code contract (0/2). Adding `--upstream` as a flag keeps the surface tight; a new verb duplicates the existing logic. **Recommend: `--upstream` flag on `check`.** See Open Q-D. |
| Separate `/em-check-state` skill | Extend `/em-global-state` with a `reservations` verb | The user's verbatim phrasing was `/em-check-state` but the existing skill already has `list/get/locks/claims/unlock/release` and is the natural home. Adding ONE verb is cheaper than maintaining TWO skill files. **Recommend: extend `/em-global-state`.** The user is unlikely to object once the cost is surfaced — but if they do, the fallback is a 50-line `/em-check-state` skill that shells to the same `em-proj state reserve-list` verb. See Open Q-E. |
| Hash `upstream_identity` in Redis key | Verbatim canonical string | Phase 4's claim key uses VERBATIM `project_hash` (path-with-dashes). Following the same ergonomic for `upstream_identity` makes `redis-cli SCAN MATCH state:reserve:*` human-readable AND consistent with claims. Hashing buys nothing — colons in the canonical form are managed by choice (use `github.com:owner/repo` only — see §Pattern 1 — colons are then unambiguous because they separate prefix segments). **Recommend: VERBATIM.** See Open Q-B. |

**Installation:**
No new dependencies — every required component is already on this machine.
- `git` is on PATH (`[ASSUMED]` — should be confirmed via `command -v git` in the structural test).
- `redis-py`, `psutil`, `typer`, `pytest` already declared in pyproject.toml.

**Version verification (run before plan execution):**
```bash
git --version  # expected: any 2.x
command -v git
```
No `pip install` / `uv add` step is required.

## Architecture Patterns

### System Architecture Diagram

```
Clone A (cwd=/path/clone-a)           Clone B (cwd=/path/clone-b)
  $ em-proj state reserve               $ em-proj state reserve
    migrations.v200                        migrations.v200
  │                                      │
  │  cwd=/path/clone-a                   │  cwd=/path/clone-b
  │  CLAUDE_CODE_SESSION_ID=uuid-A       │  CLAUDE_CODE_SESSION_ID=uuid-B
  ▼                                      ▼
  reserve verb (state/__init__.py)
  │
  │ 1. resolve session_id (env)
  │ 2. resolve project_hash (cwd, per-clone)
  │ 3. resolve upstream_identity:
  │    subprocess.run(['git', '-C', cwd, 'remote', 'get-url', 'origin'])
  │    canonicalize → "github.com:emonical/roleplay-engine"  (SAME for A and B)
  │ 4. resolve workstream:
  │    if --workstream <name>: use it
  │    elif claim_check("workstream.active") → use holder["reason"]
  │    elif sys.stdin.isatty() and sys.stdout.isatty(): prompt
  │    else: exit 1 with actionable error
  │ 5. validate_key("migrations.v200")
  │ 6. reserve_take(area="migrations.v200",
  │                 upstream_identity="github.com:emonical/roleplay-engine",
  │                 workstream="active-ws", reason=..., ttl=1800)
  │
  ▼
  reserve_take (state/reserve.py — pure ops)
  │
  │ build redis_key:
  │   state:reserve:github.com:emonical/roleplay-engine:migrations.v200
  │                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  │                upstream_identity (SAME for A and B)
  │
  ▼
  Redis: EVAL LUA_RESERVE_REFRESH_OR_TAKE
         (server-side, single command slot)
            │
            │ Refresh-or-take guards on (session_id AND upstream_identity)
            │ NOT (session_id AND project_hash) — that's the Phase 4 path.
            │
            ▼
  ┌─────────────┴─────────────┐
  │                           │
  ▼ "taken"                   ▼ "conflict"
  exit 0                      exit 3
  Holder HASH stored:         stdout: held_by_another envelope
  {session_id: A,             with winner's holder dict.
   project_hash: <A's cwd>,   stderr: "reserved by session A in workstream active-ws"
   upstream_identity: ...,
   workstream: ...,
   reason: ..., ...}

   Clone A wins.              Clone B sees Clone A's holder — same upstream_identity,
                              different project_hash, different session_id.

  ─────────────────────────────────────────────────────────────────
  Concurrent: /em-global-state reservations from ANY clone (A, B, C)
  ─────────────────────────────────────────────────────────────────
  Skill → em-proj state reserve-list --json
  │
  ▼
  reserve_list_by_prefix:
    resolve upstream_identity from cwd
    SCAN MATCH state:reserve:<upstream_identity>:*
    HGETALL each key
    return [holder, ...]   (same content from any clone)
  │
  ▼
  Verb groups by category prefix (string before first ".")
  Returns {"migrations": [{...v200...}, {...v201...}],
           "db.ports":   [{...5432...}]}
```

### Component Responsibilities

| File | Responsibility | Action in Phase 7 |
|------|---------------|-------------------|
| `src/em_proj/identity.py` | Identity primitives (session_id, project_hash, process composite, stale probes) | **EDIT** — add `resolve_upstream_identity()` + module-private `_canonicalize_upstream_url()` helper. |
| `src/em_proj/state/reserve.py` (NEW) | Pure-ops reservation module: `reserve_take`, `reserve_release`, `reserve_check`, `reserve_list_by_prefix` + 3 Lua scripts + `HeldByAnother` + `ReserveNotHeld` exceptions | **CREATE** — structural mirror of `claim.py` with `KEY_PREFIX = "state:reserve:"`, holder has 7 fields, Lua compares `(session_id, upstream_identity)` not `(session_id, project_hash)`. |
| `src/em_proj/state/__init__.py` | Verb wiring (typer commands) | **EDIT** — add `reserve`, `reserve-list` verbs; add `--upstream` flag to existing `check` verb. |
| `tests/unit/test_upstream_identity.py` (NEW) | Unit tests for `_canonicalize_upstream_url()` and `resolve_upstream_identity()` | **CREATE** — table-driven test with curated input/output pairs covering SSH, HTTPS, trailing-slash, .git-suffix, port, user-info. |
| `tests/unit/test_reserve.py` (NEW) | Unit tests for `reserve.py` pure ops | **CREATE** — mirrors `tests/unit/test_claim.py` structure; 8 behavior cases for take/release/check/list. |
| `tests/unit/test_reserve_verbs.py` (NEW) | Unit tests for the new verbs including TTY prompt behavior | **CREATE** — uses `monkeypatch.setattr("sys.stdin.isatty", ...)` to simulate TTY/non-TTY; tests prompt-vs-exit-1 paths. |
| `tests/multiprocess/test_reserve_race.py` (NEW) | Two-clone race tests using per-child `cwd=` with distinct fake `.git/config` files | **CREATE** — see §Pattern 5. |
| `tests/multiprocess/test_reserve_three_clones_list.py` (NEW, optional) | Three-clone test: clone A reserves, clones B and C both see it via `reserve-list` | **CREATE** — SC#3 demo. |
| `tests/structural/test_phase_07_shape.py` (NEW) | AST + source-grep assertions on Phase 7 surface | **CREATE** — mirrors Phase 6 structure; asserts (a) Phase 7 files exist, (b) `reserve.py` has the 3 Lua scripts and the 7-field holder, (c) `claim.py` and `reserve.py` KEY_PREFIXes are disjoint, (d) `state/__init__.py` has the new verbs, (e) SUMMARY coverage. |
| `~/.claude/skills/em-global-state/SKILL.md` | Skill surface | **EDIT** — add `/em-global-state reservations [--category <name>] [--upstream <url>]` verb subsection. |
| `src/em_proj/state/claim.py` | Phase 4 claim ops | **NO CHANGE** — coexists with reserve.py in a different namespace. |

### Recommended Project Structure
```
src/em_proj/
├── identity.py               # EDITED — adds resolve_upstream_identity + helper
└── state/
    ├── claim.py              # UNCHANGED
    ├── reserve.py            # NEW — pure ops mirror of claim.py
    └── __init__.py           # EDITED — new verbs

tests/unit/
├── test_upstream_identity.py     # NEW
├── test_reserve.py               # NEW
└── test_reserve_verbs.py         # NEW

tests/multiprocess/
├── test_reserve_race.py                  # NEW — 2-clone race
└── test_reserve_three_clones_list.py     # NEW (optional) — SC#3 demo

tests/structural/
└── test_phase_07_shape.py        # NEW — phase shape invariants

~/.claude/skills/em-global-state/
└── SKILL.md                  # EDITED — adds reservations verb
```

### Pattern 1: Canonical-form function for `git remote get-url origin`

**What:** A pure stdlib function that takes any of the common git URL shapes and returns the canonical form `host:owner/repo` (lowercased, `.git` stripped, no protocol, no port, no trailing slash, no user-info).

**When to use:** Inside `resolve_upstream_identity()` before the result is used in a Redis key.

**Example:**
```python
# Source: synthesis — derived from the inputs identified in Open Q-A
# [VERIFIED: git remote get-url origin produces "git@github.com:emonical/em-proj.git" in this checkout]
import re

# Regex matches:
#   ssh:    git@host:owner/repo.git, git@host:owner/repo
#           ssh://git@host/owner/repo.git, ssh://git@host:22/owner/repo
#   https:  https://host/owner/repo.git, https://host/owner/repo
#           http:// also (in case anyone still uses it on internal networks)
#   git:    git://host/owner/repo.git
#
# Two capture groups: host, owner/repo
_SCP_FORM = re.compile(
    r"^(?:[a-zA-Z0-9_.\-]+@)?"          # optional user@
    r"(?P<host>[a-zA-Z0-9.\-]+)"        # host
    r":(?!\d)"                           # ":" but NOT followed by a digit (port)
    r"(?P<path>[a-zA-Z0-9._\-/]+?)"     # owner/repo
    r"(?:\.git)?/?$"                     # optional .git, optional trailing slash
)
_URL_FORM = re.compile(
    r"^(?:https?|ssh|git)://"            # protocol
    r"(?:[a-zA-Z0-9_.\-]+@)?"           # optional user@
    r"(?P<host>[a-zA-Z0-9.\-]+)"        # host
    r"(?::\d+)?"                         # optional :port
    r"/(?P<path>[a-zA-Z0-9._\-/]+?)"    # /owner/repo
    r"(?:\.git)?/?$"                     # optional .git, optional trailing slash
)


def _canonicalize_upstream_url(raw: str) -> str | None:
    """Return the canonical `host:owner/repo` form, or None if unparseable.

    Canonical form properties:
      - host is lowercased
      - owner/repo case is PRESERVED (GitHub is case-insensitive for repo
        lookup but case-PRESERVING for display; preserving keeps the
        Redis key human-readable as the user wrote it)
      - .git suffix is stripped
      - trailing slash is stripped
      - port (if explicit) is dropped — same repo regardless of port
      - user-info (git@) is dropped
      - protocol is dropped — same repo across ssh/https
    """
    raw = raw.strip()
    if not raw:
        return None

    # Try URL form first (has explicit protocol)
    m = _URL_FORM.match(raw)
    if not m:
        m = _SCP_FORM.match(raw)
    if not m:
        return None

    host = m.group("host").lower()
    path = m.group("path").strip("/")  # belt-and-braces in case the regex leaves a slash
    return f"{host}:{path}"
```

**Test vector (REQUIRED — bake into `test_upstream_identity.py` as a table):**

| Input (`git remote get-url origin` output) | Expected canonical form |
|--------------------------------------------|-------------------------|
| `git@github.com:emonical/roleplay-engine.git` | `github.com:emonical/roleplay-engine` |
| `git@github.com:emonical/roleplay-engine` | `github.com:emonical/roleplay-engine` |
| `https://github.com/emonical/roleplay-engine.git` | `github.com:emonical/roleplay-engine` |
| `https://github.com/emonical/roleplay-engine` | `github.com:emonical/roleplay-engine` |
| `https://github.com/emonical/roleplay-engine/` | `github.com:emonical/roleplay-engine` |
| `ssh://git@github.com/emonical/roleplay-engine.git` | `github.com:emonical/roleplay-engine` |
| `ssh://git@github.com:22/emonical/roleplay-engine.git` | `github.com:emonical/roleplay-engine` |
| `https://user:token@github.com/emonical/roleplay-engine.git` | `github.com:emonical/roleplay-engine` |
| `https://GitHub.COM/emonical/roleplay-engine` | `github.com:emonical/roleplay-engine` (host lowercased) |
| `https://github.com/EMonical/RolePlay-Engine` | `github.com:EMonical/RolePlay-Engine` (owner/repo case preserved) |
| `git@gitlab.example.com:org/sub/repo.git` | `gitlab.example.com:org/sub/repo` (subgroup paths handled) |
| `` (empty) | `None` |
| `not-a-url` | `None` |

`[ASSUMED]`: GitHub's case-insensitive-on-lookup-but-case-preserving-on-display behavior. Preserving owner/repo case is the safer default — a user typing `git clone github.com/EMonical/...` and `git clone github.com/emonical/...` into two sibling clones is rare enough that we should NOT pretend they coordinate. If real-world testing surfaces this as a bug, the fix is a single `lower()` on the path — additive and reversible.

### Pattern 2: `resolve_upstream_identity()` with explicit fallback

**What:** Top-level resolver that returns either a canonical upstream string or the `project_hash` fallback.

**When to use:** Called by the `reserve` verb (and `check --upstream`) to determine the Redis key namespace.

**Example:**
```python
# Source: synthesis — Phase 7's identity.py addition
# Mirrors resolve_session_id / resolve_project_hash discipline
import subprocess
import os


def resolve_upstream_identity(cwd: str | None = None) -> str:
    """Return the canonical upstream-repo identity for the calling clone.

    Strategy:
      1. Run `git -C <cwd> remote get-url origin` via subprocess.run with
         shell=False (T-3-01-03 mitigation — no PATH-controlled shell-out).
      2. If git exits 0, canonicalize the output. Return the canonical string.
      3. If git fails (no origin, not a repo, git missing) OR canonicalization
         returns None: fall back to resolve_project_hash() so reservations
         degrade to per-clone-only semantics rather than refusing.

    The fallback is a critical design choice:
      - Refusing on no-origin would block tests that don't set up a fake .git/
      - Falling back to project_hash means a no-origin clone behaves
        identically to Phase 4 claims (per-clone namespace, never coordinates
        with siblings) — same blast radius as today.
      - Document this as a Phase 7 contract: "no origin remote → per-clone
        scope (same as Phase 4 claims)".
    """
    target_cwd = cwd if cwd is not None else os.getcwd()
    try:
        result = subprocess.run(
            ["git", "-C", target_cwd, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return resolve_project_hash()

    if result.returncode != 0:
        return resolve_project_hash()

    raw = result.stdout.strip()
    canonical = _canonicalize_upstream_url(raw)
    if canonical is None:
        return resolve_project_hash()

    return canonical
```

### Pattern 3: `reserve.py` pure-ops module (structural mirror of `claim.py`)

**What:** A new module structurally identical to `claim.py` but with three deltas:
1. `KEY_PREFIX = "state:reserve:"` (NOT `state:claim:`)
2. Key shape: `KEY_PREFIX + upstream_identity + ":" + area` (NOT `KEY_PREFIX + project_hash + ":" + area`)
3. Holder has 7 fields: `{session_id, project_hash, upstream_identity, workstream, reason, claimed_at, expires_at}` (NOT 5)

**Lua delta — KEY change:** The 3 Lua scripts are otherwise identical to `claim.py`'s, with `project_hash` replaced by `upstream_identity` in the Lua HGET-and-compare logic for refresh and release. This is a STRING substitution at the Lua level, not a logic change.

**Example (one of the three scripts, showing the delta):**
```python
# Source: derived from src/em_proj/state/claim.py:103-124 (LUA_CLAIM_REFRESH_OR_TAKE)
# Delta from claim.py: HSET sets two more fields (upstream_identity, workstream);
#                       refresh-guard compares (session_id, upstream_identity)
#                       NOT (session_id, project_hash).
LUA_RESERVE_REFRESH_OR_TAKE: str = """
local exists = redis.call('EXISTS', KEYS[1])
if exists == 0 then
  redis.call('HSET', KEYS[1],
    'session_id',        ARGV[1],
    'project_hash',      ARGV[2],
    'upstream_identity', ARGV[3],
    'workstream',        ARGV[4],
    'reason',            ARGV[5],
    'claimed_at',        ARGV[6],
    'expires_at',        ARGV[7]
  )
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[8]))
  return 'taken'
end
local sid = redis.call('HGET', KEYS[1], 'session_id')
local upstream = redis.call('HGET', KEYS[1], 'upstream_identity')
if sid == ARGV[1] and upstream == ARGV[3] then
  redis.call('HSET', KEYS[1], 'expires_at', ARGV[7])
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[8]))
  return 'refreshed'
end
return 'conflict'
"""
```

**Why compare on (session_id, upstream_identity) NOT (session_id, project_hash):** The whole point of Phase 7 is cross-clone coordination. If refresh required matching `project_hash`, then clone A's session and clone B's session of the same upstream could never refresh each other's reservation. They CAN'T anyway in the M1 contract (different `session_id`), but if a session ran in clone A and then in clone B (same `session_id` somehow, e.g., the user `cd`'d between clones in one Claude session), the refresh path should work — and the upstream-anchored compare is what makes that true.

### Pattern 4: Verb-layer TTY prompt for missing workstream

**What:** Inside the `reserve` verb, after argv parsing and before calling `reserve_take`, resolve the workstream with this fallback chain:

```python
# Source: synthesis — Phase 7 verb addition to state/__init__.py
# Mirrors Phase 3 D-07 dual-isatty pattern (state/__init__.py:344-352)

def _resolve_workstream(workstream_arg: str | None, json_mode: bool) -> str:
    """Resolve the workstream name for a reserve verb call.

    Fallback chain:
      1. --workstream <name> argument: use it verbatim.
      2. claim_check("workstream.active") returns a holder whose `reason`
         field is the workstream name: use that.
      3. Both stdout AND stdin are TTYs: prompt the user.
      4. Otherwise: exit 1 with the locked actionable error message.
    """
    if workstream_arg:
        return workstream_arg

    # Try Phase 6 workstream.active claim
    try:
        holder = claim_check("workstream.active")
        # Phase 6 sets workstream name as `reason`; confirm via Open Q-H
        if holder.get("reason"):
            return holder["reason"]
    except ClaimNotHeld:
        pass  # fall through to prompt
    except Exception:
        pass  # be defensive — if claim_check fails for any reason, treat as unset

    # TTY prompt path
    if sys.stdin.isatty() and sys.stdout.isatty():
        sys.stderr.write(
            "Workstream is unset for this clone. Enter a workstream name: "
        )
        sys.stderr.flush()
        answer = sys.stdin.readline().strip()
        if answer:
            return answer
        emit_error(
            "workstream_unresolved",
            "empty workstream name; aborting reservation",
            json_mode=json_mode,
        )

    # Non-TTY (CI, scripts, agent subprocess): exit 1 with actionable message
    emit_error(
        "workstream_unresolved",
        "workstream unresolved — set it via `gsd-sdk query workstream.set <name>` "
        "or pass `--workstream <name>`",
        json_mode=json_mode,
    )
    # emit_error exits; the next line is unreachable but satisfies type-checkers
    raise SystemExit(1)
```

**Critical:** This logic lives at the verb layer (`state/__init__.py`), NOT in `reserve.py`. The pure-ops module receives `workstream` as a parameter.

### Pattern 5: Multi-clone race test with per-child `cwd=` and fake `.git/config`

**What:** Two pytest fixture processes simulating two distinct sibling clones of the same upstream repo. Each child gets a distinct `cwd=` pointing at a `tmp_path`-derived directory that contains a fake `.git/config` with the SAME `[remote "origin"]` block.

**When to use:** Race tests for `reserve` verb where the two children MUST resolve the same `upstream_identity` despite running in different directories.

**Example:**
```python
# Source: synthesis — new for Phase 7; combines Phase 4's race pattern
# (tests/multiprocess/test_claim_race.py:120-179) with per-child cwd=

import json
import os
import subprocess
from pathlib import Path

from tests.conftest import EM_PROJ_BIN, TEST_DB


def _make_fake_clone(parent: Path, name: str, origin_url: str) -> Path:
    """Create a fake clone directory at parent/name with .git/config containing origin.

    The .git/config is the minimal INI that satisfies
    `git -C <dir> remote get-url origin`. Tested locally with git 2.43.

    Format:
        [remote "origin"]
        \turl = <url>
            fetch = +refs/heads/*:refs/remotes/origin/*
    """
    clone_dir = parent / name
    git_dir = clone_dir / ".git"
    git_dir.mkdir(parents=True)
    config = (
        '[remote "origin"]\n'
        f'\turl = {origin_url}\n'
        '\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
    )
    (git_dir / "config").write_text(config)
    # Some git versions require HEAD to consider this a valid repo for some
    # commands; remote get-url does NOT require it, but write it for safety.
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    return clone_dir


def test_two_clones_race_reserve_one_wins(clean_db, tmp_path):
    """Two sibling clones racing `em-proj state reserve migrations.v200`.

    Both clones have the SAME origin URL but DIFFERENT cwd. The
    upstream_identity resolver MUST canonicalize both to the same string
    so the reservation serializes server-side via the Lua refresh-or-take
    script. Exactly one child exits 0; the other exits 3.

    Mirrors test_claim_race.py:102-179 but with per-child cwd= instead of
    only per-child env=.
    """
    origin = "git@github.com:emonical/roleplay-engine.git"
    clone_a = _make_fake_clone(tmp_path, "clone-a", origin)
    clone_b = _make_fake_clone(tmp_path, "clone-b", origin)

    child_a_env = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": "reserve-race-A",
    }
    child_b_env = {
        **os.environ,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
        "CLAUDE_CODE_SESSION_ID": "reserve-race-B",
    }

    cmd = [
        EM_PROJ_BIN, "state", "reserve",
        "--ttl", "60",
        "--workstream", "test-ws",   # bypass workstream resolution in tests
        "--reason", "race test",
        "--json",
        "migrations.v200",
    ]

    # Tight launch loop — no sleep between spawns. This is the race.
    proc_a = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=child_a_env, cwd=str(clone_a),
    )
    proc_b = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=child_b_env, cwd=str(clone_b),
    )

    out_a, err_a = proc_a.communicate(timeout=15.0)
    out_b, err_b = proc_b.communicate(timeout=15.0)

    exit_codes = sorted([proc_a.returncode, proc_b.returncode])
    assert exit_codes == [0, 3], (
        f"Expected exactly one winner (exit 0) and one loser (exit 3); "
        f"got {exit_codes}\n"
        f"clone_a: rc={proc_a.returncode} stderr={err_a[:200]!r}\n"
        f"clone_b: rc={proc_b.returncode} stderr={err_b[:200]!r}"
    )

    # The reservation must be visible from BOTH clones via reserve-list
    # (or check --upstream) because they share an upstream_identity.
    list_result = subprocess.run(
        [EM_PROJ_BIN, "state", "reserve-list", "--json"],
        capture_output=True, text=True,
        env=child_a_env, cwd=str(clone_a),
    )
    assert list_result.returncode == 0
    items = json.loads(list_result.stdout)["data"]["items"]
    # Find the migrations.v200 entry
    matches = [i for i in items if i.get("area") == "migrations.v200"]
    assert len(matches) == 1
    holder = matches[0]
    # The holder's upstream_identity must be the canonical form
    assert holder["upstream_identity"] == "github.com:emonical/roleplay-engine"
    # The holder's project_hash should be ONE of the two clone paths
    # (whichever one won)
    assert holder["project_hash"] in (
        str(clone_a).replace("/", "-"),
        str(clone_b).replace("/", "-"),
    )
```

### Anti-Patterns to Avoid

- **DO NOT shell-out via `subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=cwd)`** — the older `git -C <cwd>` form is preferred because it makes the working directory explicit on the argv, eliminating any ambiguity between Python's `cwd=` kwarg and git's working-dir resolution. Always pass `["git", "-C", target_cwd, "remote", "get-url", "origin"]`.
- **DO NOT use `shell=True`** — T-3-01-03 already documented the PATH-controlled shell-out threat. Same threat applies here.
- **DO NOT introduce `giturlparse` as a dependency** — stdlib is sufficient; see Alternatives Considered. If a future phase needs heavier URL parsing (e.g., for non-git URLs), revisit then.
- **DO NOT make `upstream_identity` configurable via env var or config file** — config-file overrides are a footgun; the whole point is that all sibling clones auto-agree. Locked Decision #1.
- **DO NOT silently fall through on missing workstream** — RESERVE-05 explicitly says "No silent heuristic fallback." Either prompt (TTY) or exit 1 (non-TTY).
- **DO NOT auto-derive workstream from repo-root basename** — even though it would be a "reasonable" heuristic, it's exactly the kind of magic the requirement forbids.
- **DO NOT mix `project_hash` and `upstream_identity` in the same Redis key** — the prefixes are disjoint by design (`state:claim:` vs `state:reserve:`). The structural test asserts this.
- **DO NOT call `claim_take` from inside the reserve verb instead of via subprocess** — directly call `reserve_take` (the new pure-op). The reserve verb is NOT a wrapper around the claim verb; it's a sibling.
- **DO NOT use `subprocess.Popen(..., env={..., "CLAUDE_CODE_SESSION_ID": ...})` WITHOUT also passing `EM_PROJ_REDIS_DB=15` in tests** — Phase 4 RESEARCH Pitfall #4 — same trap applies; the child connects to prod db=0 and leaks reservations to the user's live state.
- **DO NOT forget per-child `cwd=` in multi-clone race tests** — Phase 4-6 tests only varied `env=`; Phase 7's per-child `cwd=` is a NEW pattern. A test that varies only env will produce false-positive passes because both children resolve the SAME upstream_identity from the SAME cwd (i.e., the test runner's cwd).
- **DO NOT take a NEW claim from inside the reserve verb when reading `workstream.active`** — `claim_check` (read-only Lua, no HSET, no EXPIRE update) is the right call. Using `claim_take` would refresh the workstream claim's TTL as a side effect of taking a reservation, which is wrong semantically. See Open Q-H.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Cross-process atomic refresh-or-take with TTL | A new Lua dialect or a Python-side mutex | Phase 4's `LUA_CLAIM_REFRESH_OR_TAKE` pattern (verbatim copy with 2 ARGV additions) | Battle-tested by Phase 4 multiprocess tests + ~300 LOC of inspection; reimplementing the same atomicity in `reserve.py` invites subtle divergence |
| Session-id resolution | A new env-var probe in `reserve.py` | `em_proj.identity.resolve_session_id` | Single source of truth; Phase 3 already validated the fallback chain |
| Git URL parsing for canonicalization | `giturlparse` library | 30-LOC stdlib `_canonicalize_upstream_url` | See Alternatives Considered; smaller surface, no dep, easier to debug |
| TTY/non-TTY gate for the workstream prompt | A new `_isatty_dual` helper | Phase 3 D-07 pattern: `sys.stdin.isatty() and sys.stdout.isatty()` | One-liner; carbon-copy from state/__init__.py:344-352 |
| Cross-process JSON envelope for reserve-list | A bespoke schema | The existing `emit_ok({"items": [...]}` envelope | Phase 4's claim-list already returns `{"items": [...]}`; Phase 7 returns `{"items": [...]}` with category injection but the envelope shape is identical |
| Multi-process race fixture | A fresh harness | The existing `multiproc_race` + `clean_db` fixtures (with `cwd=` kwarg added — or direct `subprocess.Popen` like Phase 4 race tests do for per-child env) | Already-shipped fixtures; adding `cwd=` support is either trivial fixture upgrade OR a one-test-file override |
| Group-by-category for reserve-list | A SQL-like aggregation | A Python `defaultdict(list)` after the scan | Cardinality is tiny (a few reservations per upstream); no need for server-side aggregation |

**Key insight:** Phase 7 is Phase 4 plus a new identity namespace. The temptation to "improve" the claim semantics (e.g., consolidate claim.py and reserve.py into one parameterized module) is the dangerous failure mode — resist it. Two modules, structurally parallel, is the correct decomposition. Consolidation can come in M2+ if the duplication actually becomes a maintenance problem.

## Runtime State Inventory

> Phase 7 is structurally additive (a new Redis namespace prefix), NOT a rename or migration. The inventory below verifies that no existing runtime state needs to be touched.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| **Stored data** | Existing Phase 4 claims under `state:claim:<project_hash>:*` (including `state:claim:<project_hash>:workstream.active` from Phase 6). These coexist with the new `state:reserve:<upstream_identity>:*` namespace; no migration needed. | NONE — namespaces are disjoint by construction. Document the two-namespace invariant in `reserve.py` module docstring AND in the structural test. |
| **Live service config** | None — em-proj is a personal CLI, no external services. | NONE. |
| **OS-registered state** | None — no Task Scheduler, no launchd, no pm2 entries reference em-proj reserve or claim. | NONE. |
| **Secrets/env vars** | `CLAUDE_CODE_SESSION_ID` (Phase 3); `EM_PROJ_REDIS_DB` (test injection). No new env var introduced by Phase 7. Phase 7's `--workstream` flag is argv, not env. | NONE — code edit only. |
| **Build artifacts** | em-proj itself is installed via `uv tool install --editable .` — incremental file edits in `src/em_proj/` are picked up immediately (editable mode). No rebuild required after Phase 7 lands. The new `tests/structural/test_phase_07_shape.py` will be discovered by pytest automatically. | NONE. |

**Nothing found in category:** Stored data requiring migration, OS-registered state, secrets — all verified as not applicable.

## Common Pitfalls

### Pitfall 1: Same-session resolving DIFFERENT `upstream_identity` from different cwds
**What goes wrong:** A Claude Code session running in clone A computes `upstream_identity = github.com:org/repo`. The user `cd`'s to clone B (same upstream). The session is still alive, same `CLAUDE_CODE_SESSION_ID`. The next `em-proj state reserve ...` call computes the SAME `upstream_identity` (good) but a DIFFERENT `project_hash` (because cwd changed).

**Why it happens:** `project_hash` is cwd-derived; `upstream_identity` is cwd-derived but canonicalized. They have different sensitivities to `cd`.

**How to avoid:** This is actually the CORRECT behavior — the reservation is tied to the upstream_identity, not the cwd. The holder dict records BOTH so debugging can correlate. The pitfall is misreading this as a bug; document the invariant in `reserve.py` module docstring.

**Warning signs:** A test failing because the same session releases a reservation it took, only the test ran the take in cwd-A and the release in cwd-B. Both should still work because the Lua compares on `(session_id, upstream_identity)`, NOT `(session_id, project_hash)`.

### Pitfall 2: `git remote get-url origin` exits 0 with empty stdout when the remote URL is the empty string
**What goes wrong:** If a user runs `git remote set-url origin ""` (or has a corrupted .git/config), `git remote get-url origin` exits 0 with empty stdout. The naive resolver would canonicalize `""` to `None` (via the `if not raw` guard in Pattern 1) and fall back to `project_hash` — but this is silent.

**Why it happens:** Git tolerates a wide range of malformed remote configs.

**How to avoid:** Treat empty stdout the same as canonicalization-returning-None — fall back to `project_hash` per Pattern 2. The fallback is documented as a contract, so the user behavior is: "your reservations went into per-clone scope; fix your origin URL to coordinate."

**Warning signs:** `reserve-list` shows reservations that look like they should be cross-clone but aren't.

### Pitfall 3: Lua script ARGV index drift between `claim.py` and `reserve.py`
**What goes wrong:** `claim.py`'s `LUA_CLAIM_REFRESH_OR_TAKE` uses ARGV[1]-ARGV[6]. `reserve.py`'s `LUA_RESERVE_REFRESH_OR_TAKE` MUST use ARGV[1]-ARGV[8] (two extra fields: upstream_identity, workstream). A copy-paste error that uses ARGV[3]-ARGV[7] for the wrong fields produces silent miswiring (e.g., `reason` field stores the upstream_identity value).

**Why it happens:** Lua scripts are stringly-typed and have no schema validation server-side.

**How to avoid:** (a) Define a `_RESERVE_ARGV_ORDER` constant tuple in `reserve.py` documenting which ARGV index maps to which holder field; (b) the Python-side `client.eval(...)` call must pass arguments in the same order; (c) unit tests for `reserve_take` MUST assert that after a take, `client.hgetall(key)` returns EXACTLY the 7 expected fields with the expected values.

**Warning signs:** A `reserve_check` returns a holder whose `reason` field looks like a URL. Inspect with `redis-cli HGETALL state:reserve:<key>` to confirm field-to-value mapping.

### Pitfall 4: `claim_check("workstream.active")` against the wrong namespace
**What goes wrong:** The reserve verb reads the active workstream via `claim_check("workstream.active")`. If the verb code accidentally calls `reserve_check` (the new function) instead, it would look up `state:reserve:<upstream_identity>:workstream.active` — which is the WRONG namespace.

**Why it happens:** Both functions are imported into the same `__init__.py` module; tab-completion / accidental shadowing in the editor.

**How to avoid:** Rename the import alias in the verb module: `from em_proj.state.claim import claim_check as workstream_check` makes the call site read `workstream_check("workstream.active")` and prevents the wrong-namespace mistake.

**Warning signs:** Workstream resolution silently falls through to the TTY prompt even when Phase 6 has clearly set the workstream. Means the lookup hit the wrong namespace.

### Pitfall 5: TTY prompt in subprocess context — `sys.stdin.isatty()` returns False
**What goes wrong:** A test invokes `em-proj state reserve foo` via `subprocess.Popen`. `sys.stdin.isatty()` returns False (subprocess stdin is a pipe, not a TTY). The verb falls into the non-TTY exit-1 branch, even though the test "looks" interactive.

**Why it happens:** subprocess.Popen with default stdin defaults to inheriting the parent's stdin (which IS the test runner's stdin), but pytest's test runner has stdin redirected when running under most CI configurations.

**How to avoid:** Every test that invokes `em-proj state reserve` MUST pass `--workstream <name>` explicitly. The TTY prompt path is exercised only by unit tests of the verb logic (using monkeypatched `sys.stdin.isatty`), NOT by multiprocess tests. Document this in the conftest.py docstring or in a new section of CLAUDE.md.

**Warning signs:** A race test fails with "workstream unresolved" exit 1 instead of the expected exit 3 / exit 0 outcomes.

### Pitfall 6: Multi-clone fixture forgets per-child `cwd=`
**What goes wrong:** A race test sets up two clone directories but invokes the children with shared `cwd=` (the test runner's cwd). Both children resolve the SAME `upstream_identity` (the em-proj repo itself) — the test "passes" but doesn't actually exercise the cross-clone path.

**Why it happens:** Forgetting that Phase 7 is the FIRST phase where per-child `cwd=` matters.

**How to avoid:** Structural test asserts that `tests/multiprocess/test_reserve_race.py` source contains `cwd=` as a keyword argument at every `subprocess.Popen` call site. Cheap AST check or source-grep.

**Warning signs:** `reserve_list` from clone B returns the SAME `project_hash` as the winner, not a distinct one. Means both children resolved the same path — they were both running in the test runner's cwd.

### Pitfall 7: git binary missing or git -C interpreting cwd as a non-repo
**What goes wrong:** `git -C /some/path remote get-url origin` exits with `fatal: not a git repository` when `/some/path` exists but has no `.git/` directory. The resolver should fall back to `project_hash`.

**Why it happens:** A clone-root probe with no `.git/` setup (e.g., `mkdir clone-a` without writing a fake `.git/config`).

**How to avoid:** Pattern 2's resolver already handles `result.returncode != 0` — git's non-zero exit triggers the fallback. The exact stderr message doesn't matter (`fatal:` text is git-version-dependent). The unit test for `resolve_upstream_identity` should exercise this path with `tmp_path` having no `.git/` directory at all.

**Warning signs:** A reserve verb call exits with a stale `project_hash`-namespaced reservation instead of an `upstream_identity` one. Inspect with `redis-cli SCAN MATCH 'state:reserve:*'` to confirm the prefix.

### Pitfall 8: The two-namespace invariant breaks if `claim.py` accidentally writes under `state:reserve:` (or vice versa)
**What goes wrong:** A future refactor merges `claim.py` and `reserve.py` into a parameterized module. A bug in the parameter wiring causes a claim to write into the reserve namespace.

**Why it happens:** Speculative consolidation that ignores Locked Decision #3.

**How to avoid:** Structural test asserts: (a) `claim.py` source-text does NOT contain `state:reserve:`; (b) `reserve.py` source-text does NOT contain `state:claim:`; (c) the two `KEY_PREFIX` constants are different.

**Warning signs:** Any test combining `claim_take` and `reserve_take` against the same area name shows interference.

## Code Examples

### Example 1: `resolve_upstream_identity` resolver (Python — for `src/em_proj/identity.py`)
See Pattern 1 (canonicalization) + Pattern 2 (resolver) above.

### Example 2: `reserve.py` Lua + ops (Python — for `src/em_proj/state/reserve.py`)
See Pattern 3 above. Mirror `src/em_proj/state/claim.py` 1:1 with the documented deltas.

### Example 3: `reserve` verb with TTY prompt logic (Python — for `src/em_proj/state/__init__.py`)
```python
# Source: synthesis — Phase 7 verb addition based on Phase 4's claim verb
# (state/__init__.py:439-505) + Phase 3's --warn TTY pattern
# (state/__init__.py:344-352)
from em_proj.identity import resolve_upstream_identity
from em_proj.state.reserve import (
    TTL_DEFAULT as RESERVE_TTL_DEFAULT,
    MIN_TTL as RESERVE_MIN_TTL,
    MAX_TTL as RESERVE_MAX_TTL,
    HeldByAnother as ReserveHeldByAnother,
    ReserveNotHeld,
    reserve_check,
    reserve_list_by_prefix,
    reserve_release,
    reserve_take,
)


@state_app.command("reserve")
def reserve(
    area: Annotated[str, typer.Argument(help="The category.resource to reserve.")],
    ttl: Annotated[
        int | None,
        typer.Option("--ttl", min=RESERVE_MIN_TTL, max=RESERVE_MAX_TTL,
                     help=f"Reservation TTL (default {RESERVE_TTL_DEFAULT}).")
    ] = None,
    reason: Annotated[
        str | None,
        typer.Option("--reason", help="Free-form reason (max 256 chars).")
    ] = None,
    workstream: Annotated[
        str | None,
        typer.Option("--workstream", help="Override workstream auto-resolution.")
    ] = None,
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """Reserve <category.resource> against the upstream-repo identity.

    Sibling clones of the same upstream share reservations.
    Exit codes: 0 reserved | 1 error/workstream-unresolved | 3 held-by-another.
    """
    json_mode = resolve_json_mode(json_flag)

    # Anonymous refusal — same gate as claim verb
    if not os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip():
        emit_error("anonymous_claim", "anonymous reservations refused",
                   json_mode=json_mode)

    # Redis pre-check
    client = get_client()
    die_if_redis_unreachable(client)

    # Resolve workstream (Pattern 4) BEFORE upstream resolution
    # because the prompt may exit; no point spending time on git.
    resolved_workstream = _resolve_workstream(workstream, json_mode)

    # Resolve upstream identity (Pattern 2)
    upstream = resolve_upstream_identity()

    effective_ttl = ttl if ttl is not None else RESERVE_TTL_DEFAULT
    try:
        holder = reserve_take(
            area=area,
            upstream_identity=upstream,
            workstream=resolved_workstream,
            ttl=effective_ttl,
            reason=reason,
        )
    except ReserveHeldByAnother as e:
        emit_held_by_another(
            "held_by_another",
            f"Reservation '{area}' held by session "
            f"{e.holder['session_id'] if e.holder else 'unknown'} "
            f"in workstream {e.holder['workstream'] if e.holder else 'unknown'}",
            holder=e.holder, json_mode=json_mode,
        )
    except ValidationError as e:
        emit_error(e.code, e.message, json_mode=json_mode)
    else:
        emit_ok(
            {
                "area": area,
                "upstream_identity": upstream,
                "workstream": resolved_workstream,
                "ttl": effective_ttl,
                "claimed_at": holder["claimed_at"],
                "expires_at": holder["expires_at"],
            },
            json_mode=json_mode,
        )
```

### Example 4: `reserve-list` verb with category grouping (Python — for `src/em_proj/state/__init__.py`)
```python
@state_app.command("reserve-list")
def reserve_list(
    category: Annotated[
        str | None,
        typer.Option("--category", help="Filter to a single category prefix.")
    ] = None,
    upstream: Annotated[
        str | None,
        typer.Option("--upstream",
                     help="Override cwd-based upstream resolution.")
    ] = None,
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """List reservations against the upstream-repo identity.

    Returns a flat list in JSON; grouped by category on TTY.
    Exit 0 always (empty list is still exit 0).
    """
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)

    # Resolve upstream — either from --upstream override (canonicalized
    # via _canonicalize_upstream_url, same as the resolver path) or from cwd.
    if upstream:
        canonical = _canonicalize_upstream_url(upstream) or upstream
    else:
        canonical = resolve_upstream_identity()

    holders = reserve_list_by_prefix(upstream_identity=canonical)

    # Optional category filter (post-scan; cardinality is tiny)
    if category:
        holders = [h for h in holders if h["area"].split(".", 1)[0] == category]

    # JSON output: flat list. The `area` field is already injected by
    # reserve_list_by_prefix (mirrors claim_list_by_prefix:461).
    emit_ok(
        {
            "upstream_identity": canonical,
            "items": holders,
        },
        json_mode=json_mode,
    )
```

(TTY grouping is a render-layer concern; see Open Question E for placement.)

### Example 5: SKILL.md addition (Markdown — for `~/.claude/skills/em-global-state/SKILL.md`)
```markdown
### /em-global-state reservations [--category <name>] [--upstream <url-or-identity>]

List reservations against the upstream-repo identity. Auto-resolves the identity
from the current cwd's `git remote get-url origin`; sibling clones of the same
upstream see the same reservations.

```bash
em-proj state reserve-list [--category <name>] [--upstream <url-or-identity>] --json
```

Pass `--category <name>` to filter to a single category prefix (e.g., `migrations`).
Pass `--upstream <url-or-identity>` to query reservations against an upstream
other than the one rooted at the current cwd.

Emit stdout verbatim. Output schema:

```json
{"schema_version":"1","status":"ok","data":{"upstream_identity":"<canonical>","items":[<reservation_holder>...]}}
```

Each `reservation_holder` contains 7 fields plus an injected `area`:
`area, session_id, project_hash, upstream_identity, workstream, reason, claimed_at, expires_at`.

Exit 0 = success (empty list is still exit 0).
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Per-clone-only claim namespace (Phase 4) | Cross-clone upstream namespace (Phase 7) — coexists with Phase 4 claims | Phase 7 (this phase) | Sibling clones of the same upstream coordinate reservations without conflicting with per-clone claims |
| `setActiveWorkstream` direct file write (pre-Phase 6) | `em-proj state claim workstream.active` (Phase 6, per-clone) | Phase 6 | Two sessions in the same clone serialize on the workstream pointer; Phase 7 reads this pointer to stamp the reserve holder |

**Deprecated/outdated:** Nothing yet — Phase 7 is purely additive.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | GitHub case-insensitive-on-lookup but case-preserving-on-display behavior. The canonicalizer preserves owner/repo case while lowercasing host. | Pattern 1, Open Q-A | If the user's siblings actually use mixed-case differently, they don't coordinate. Mitigation: a single `lower()` on the path is an additive fix. |
| A2 | Phase 6's `workstream.active` claim stores the workstream name in the holder's `reason` field. | Open Q-H, Pattern 4 | If Phase 6 stores it elsewhere (e.g., in a separate Redis key), the reserve verb's workstream auto-resolution fails. Mitigation: Plan 07-02 verifies via direct inspection of `setActiveWorkstream` shim before writing the reserve verb. |
| A3 | `giturlparse` 0.14.0 published 2025-10-22 has no breaking changes since I last verified. | Alternatives Considered | Low risk; we recommend stdlib-only path anyway. |
| A4 | Subprocess `git -C <cwd> remote get-url origin` with `shell=False` is safe against PATH-injection per Phase 3 T-3-01-03. | Pattern 2, Anti-Patterns | If git itself contains a vulnerability in `remote get-url`, the threat reopens. Standard library trust; same posture as `subprocess.run` calls elsewhere. |
| A5 | `git --version` is available on every host this CLI targets (macOS Darwin, single-user). | Standard Stack | Stated in PROJECT.md constraints; em-proj is a personal tool. Mitigation: structural test asserts `command -v git` exits 0. |
| A6 | The five fields currently in the claim holder (`session_id, project_hash, reason, claimed_at, expires_at`) can be extended to seven (`+ upstream_identity, workstream`) without breaking the Phase 4 `claim_list_by_prefix` decoder. The reserve_list_by_prefix is a NEW function; the existing claim list never touches the reserve namespace. | Pattern 3 | If a future change parameterizes the decoder across both namespaces, the schema must be branched. For Phase 7, two separate functions = two separate schemas. |
| A7 | `subprocess.run` with `timeout=5.0` for `git remote get-url origin` is enough headroom on every dev machine. Five seconds is ~50x typical `git remote` latency. | Pattern 2 | If git hangs (e.g., on a credential prompt), the resolver times out and falls back to `project_hash`. Acceptable — same as no-origin path. |

If this table is empty: All claims in this research were verified or cited — no user confirmation needed.

## Open Questions

### Open Q-A: Canonical form of `upstream_identity`

**What we know:** Same upstream URL can be expressed as `git@github.com:emonical/roleplay-engine.git`, `https://github.com/emonical/roleplay-engine.git`, `https://github.com/emonical/roleplay-engine`, with/without trailing slash, with/without `.git` suffix, SSH vs HTTPS, with/without user-info, with explicit port. All these MUST canonicalize to the same identity.

**What's unclear:** (1) Should the canonical form use a colon as the host/owner separator (`github.com:emonical/repo`) or a slash (`github.com/emonical/repo`)? (2) Should owner/repo case be preserved or lowercased?

**Recommendation:**
- **Colon separator: `github.com:emonical/repo`.** Reason: the Redis key shape is `state:reserve:<upstream_identity>:<area>`. A slash-separated upstream like `github.com/emonical/repo` would make the key `state:reserve:github.com/emonical/repo:migrations.v200` — still parseable by SCAN MATCH but visually confusing because the slashes look like Redis namespace separators (which Redis itself doesn't have, but convention does). Colons keep "host:owner/repo" together as one logical chunk. Phase 4's verbatim `project_hash` already contains colons-as-namespace-separators via the key prefix, and Phase 7's canonical form contains ONE colon — the prefix separator `state:reserve:` has two colons of its own, so the result `state:reserve:github.com:emonical/repo:migrations.v200` is unambiguous when split on `:` from the right (last colon separates the area).
- **Host lowercased, path case-preserved.** Reason: GitHub is case-insensitive for lookups but case-preserving for display; the canonical form should respect both. See A1 in the Assumptions Log.

**Confidence:** HIGH on stdlib-only path; MEDIUM on exact separator. The user may push back on the colon choice during `/gsd-discuss-phase` — be ready to default to slash if so.

### Open Q-B: Slug vs hash for the Redis key prefix

**What we know:** Phase 4's claim key prefix uses VERBATIM `project_hash` (a path with slashes converted to dashes). Phase 7 has two options: use the canonical identity verbatim (`state:reserve:github.com:emonical/roleplay-engine:migrations.v200`), or hash it (`state:reserve:<sha256-hex>:migrations.v200`).

**What's unclear:** The verbatim form is human-debuggable; the hashed form is shorter and never contains odd characters.

**Recommendation:** **VERBATIM.** Three reasons:
1. Phase 4 already chose verbatim for `project_hash`; consistency reduces cognitive load.
2. `redis-cli SCAN MATCH 'state:reserve:github.com:emonical/*'` lets a debugger find every reservation against one upstream without a lookup table.
3. The canonical form already strips the characters that would cause trouble (no whitespace, no `*`, no `?`, no glob metacharacters). The colon character IS in the canonical form but is also in the prefix structure; SCAN MATCH treats colons as literals, so this is fine.

**Confidence:** HIGH.

### Open Q-C: `reserve` verb design — new verb or flag on `claim`?

**What we know:** Locked Decision #6 says new verb.

**What's unclear:** Nothing — locked.

**Recommendation:** New `reserve` verb (locked).

**Confidence:** HIGH. The semantic deltas (different namespace, workstream stamping, TTY prompt) make a flag on `claim` too overloaded.

### Open Q-D: `check` extension for the new namespace

**What we know:** RESERVE-04 calls out `/em-check-state --upstream <url-or-identity>` as the override flag. But `em-proj state check <area>` already exists for claims. Two options: (a) add `--upstream` to existing `check` verb, (b) new `check-reserve` verb.

**What's unclear:** Which surface is cleaner — flag or new verb.

**Recommendation:** **Add `--upstream` flag to existing `check` verb.** When `--upstream` is present (or empty string to mean "auto-resolve from cwd"), the verb queries the `state:reserve:<upstream>:` namespace; otherwise it queries the `state:claim:<project_hash>:` namespace as today. Single flag, behavior change is explicit, and the JSON envelope already includes a namespace-source field via the holder dict.

**Confidence:** MEDIUM. Could go either way; the new-verb path keeps the existing `check` verb perfectly stable. If the planner wants stability, recommend new `check-reserve` verb — cheap, no behavior change to `check`.

### Open Q-E: `/em-check-state` skill placement

**What we know:** The user's verbatim ask was `/em-check-state` from any clone. The existing skill has `list|get|locks|claims|unlock|release`. Adding a 7th verb `reservations` is small.

**What's unclear:** Does the user actually care about the skill name, or is it about the verb being callable?

**Recommendation:** **Extend `/em-global-state` with a `reservations` verb.** Rationale: same skill is the natural home for cross-session state reads; one verb extension is cheaper than two-skill maintenance; user verbatim ask is preserved in spirit (any-clone-readable list) without requiring a new skill file. Add an alias note in SKILL.md documenting that `/em-global-state reservations` is the canonical surface, in case the user invokes a hypothetical `/em-check-state` (it would fail to find a skill — surface the alternative).

**Fallback if user objects:** Create a thin `~/.claude/skills/em-check-state/SKILL.md` that shells to the same `em-proj state reserve-list`. ~30 lines. Add to the plan as a "Plan 07-04 alternative."

**Confidence:** MEDIUM. User may have stronger feelings about the verb name than I'm assuming. Worth a /gsd-discuss-phase round if the planner wants certainty.

### Open Q-F: TTY-prompt mechanics

**What we know:** Phase 3 `lock --warn` uses `sys.stdin.isatty() AND sys.stdout.isatty()` (dual check). Phase 7 should do the same.

**What's unclear:** Should the prompt also accept defaults (e.g., empty input → exit 1)? What's the exact prompt copy?

**Recommendation:**
- **Prompt copy:** `"Workstream is unset for this clone. Enter a workstream name (or press Enter to abort): "`
- **Empty input → exit 1 with the same actionable-error message used in the non-TTY path.** No silent default.
- **No timeout on the prompt.** Phase 3's `--warn` prompt doesn't have one either; the user can ctrl-C.

**Confidence:** HIGH on mechanics; MEDIUM on exact copy.

### Open Q-G: Where does `upstream_identity` resolver live?

**What we know:** Options are (a) `identity.py`, (b) new `upstream.py`, (c) private helper in `reserve.py`.

**What's unclear:** Nothing significant.

**Recommendation:** **`identity.py` alongside `resolve_session_id` and `resolve_project_hash`.** Reason: identity.py is the natural home; keeps all identity resolvers co-located; no circular-import risk (identity.py has no Redis dep, reserve.py imports identity.py — same pattern as claim.py already does).

**Confidence:** HIGH.

### Open Q-H: `workstream.active` schema from Phase 6

**What we know:** Phase 6 wires `gsd-sdk query workstream.set <name>` to `em-proj state claim workstream.active --reason <name>` (per Phase 6 RESEARCH §Pattern 1 line 214 example: `'workstream.active'` is the area, and the reason — set via `--reason` — appears to be the workstream name).

**What's unclear:** Looking at `tests/multiprocess/test_workstream_consumer_race.py` should confirm the EXACT field where the workstream name is stored: is it the `reason` field, or is it stored elsewhere? `[VERIFIED via 06-RESEARCH.md Pattern 1: the gsd-sdk shellout uses '--ttl', '1800', '--json', 'workstream.active' — but reads of the Phase 6 source for `--reason` are NEEDED to confirm.]`

**Recommendation:** **Plan 07-01 MUST verify the Phase 6 schema before Plan 07-02 (verbs) is written.** Specific verification step:
1. Read `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/sdk/dist/query/workstream.js` to find the exact `spawnSync` argv for the claim.
2. If `--reason <name>` is passed, the workstream name lives in the holder's `reason` field — Pattern 4's logic works as-is.
3. If `--reason` is NOT passed, the workstream name is NOT in the holder dict — Pattern 4 needs a different lookup (perhaps reading a separate KV key `workstream.name`?).

**Risk if wrong:** The auto-resolved workstream is empty/None even when Phase 6 has clearly set one — the user gets a TTY prompt for a value that should be already known. Mitigation: TTY prompt is graceful failure, but UX is worse than expected.

**Confidence:** MEDIUM — depends on a direct read of the Phase 6 patched JS at Plan execution time.

### Open Q-I: Multi-clone race test fixture

**What we know:** Phase 4 race tests use per-child env injection only; cwd is shared.

**What's unclear:** Should the `multiproc_race` fixture itself be extended to support per-child `cwd=`, or should Phase 7 tests use direct `subprocess.Popen` (like `test_claim_race.py` does for per-child env)?

**Recommendation:** **Direct `subprocess.Popen` in Phase 7 tests** — follow `test_claim_race.py:120-179` (which already uses direct Popen for per-child env). Extending the `multiproc_race` fixture introduces a new kwarg that Phase 1-6 tests don't use, which is fine but mixes concerns. Phase 7's tests are bespoke enough that direct Popen is cleaner. The fixture can be extended in a later milestone if a second consumer appears.

**Confidence:** HIGH.

### Open Q-J: Anti-pattern landmines for the planner

(Already captured in §Common Pitfalls 1-8 and §Anti-Patterns to Avoid above. No additional open question.)

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `git` (system command) | `resolve_upstream_identity` | ✓ | `[VERIFIED: git@github.com:emonical/em-proj.git returned by 'git remote get-url origin' in this checkout]` | If absent, resolver falls back to `project_hash` (per-clone scope) |
| `redis-server` (loopback) | All Redis ops | `[ASSUMED ✓ — Phase 1 REDIS-01 ships and Phase 4-6 tests pass]` | `[VERIFIED via verify-phase.sh — checks REDIS-01]` | None — Phase 4 already depends on this; Phase 7 inherits |
| `em-proj` CLI on PATH | Multi-process tests | `[ASSUMED ✓ — Phase 4-6 tests already depend on this]` | as-installed | None |
| Python 3.12+ | All code | `[VERIFIED: pyproject.toml:10 requires-python = ">=3.12"]` | as-installed | None |
| `psutil` | Inherited from Phase 3 (not used by Phase 7 itself) | ✓ | as-declared in pyproject.toml | None |
| `typer` | Verb wiring | ✓ | `>=0.16,<1.0` per pyproject.toml | None |
| `redis-py` | All Redis ops | ✓ | `>=6.0,<8.0` per pyproject.toml | None |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** `git` is the only one with a documented fallback (per-clone scope). All others are hard requirements already shipping in Phase 4+.

## Validation Architecture

> nyquist_validation is not explicitly disabled in `.planning/config.json` (not checked here — assume enabled).

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.0+ (declared in pyproject.toml dev group) |
| Config file | pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `bash scripts/test.sh unit -k phase_07` |
| Full suite command | `bash scripts/test.sh all` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| RESERVE-01 | `upstream_identity` resolver returns canonical form for SSH/HTTPS/etc. inputs | unit | `bash scripts/test.sh unit -k upstream_identity` | ❌ Wave 0 (tests/unit/test_upstream_identity.py NEW) |
| RESERVE-01 | `upstream_identity` falls back to `project_hash` when no origin | unit | `bash scripts/test.sh unit -k upstream_identity_no_origin` | ❌ Wave 0 |
| RESERVE-01 | Two clones with same origin URL resolve to SAME upstream_identity | multiprocess | `bash scripts/test.sh multiprocess -k reserve_race` | ❌ Wave 0 (tests/multiprocess/test_reserve_race.py NEW) |
| RESERVE-02 | Holder dict has all 7 fields after `reserve_take` | unit | `bash scripts/test.sh unit -k reserve_holder_shape` | ❌ Wave 0 (tests/unit/test_reserve.py NEW) |
| RESERVE-02 | `workstream` field is auto-stamped from `workstream.active` claim | unit | `bash scripts/test.sh unit -k reserve_workstream_autostamp` | ❌ Wave 0 (tests/unit/test_reserve_verbs.py NEW) |
| RESERVE-03 | `reserve-list` returns all reservations under current upstream | unit + multiprocess | `bash scripts/test.sh unit -k reserve_list` + `bash scripts/test.sh multiprocess -k reserve_three_clones` | ❌ Wave 0 |
| RESERVE-03 | `reserve-list` results are identical from any of 3 sibling clones | multiprocess | `bash scripts/test.sh multiprocess -k reserve_three_clones_list` | ❌ Wave 0 (tests/multiprocess/test_reserve_three_clones_list.py NEW) |
| RESERVE-04 | `--category <name>` filters to a single category prefix | unit | `bash scripts/test.sh unit -k reserve_list_category_filter` | ❌ Wave 0 |
| RESERVE-04 | `--upstream <url>` overrides cwd-based resolution | unit | `bash scripts/test.sh unit -k reserve_list_upstream_override` | ❌ Wave 0 |
| RESERVE-05 | `reserve` verb prompts on TTY when workstream unset | unit (monkeypatched stdin) | `bash scripts/test.sh unit -k reserve_tty_prompt` | ❌ Wave 0 |
| RESERVE-05 | `reserve` verb exits 1 on non-TTY when workstream unset | unit | `bash scripts/test.sh unit -k reserve_nontty_exit_1` | ❌ Wave 0 |
| RESERVE-05 | `--workstream <name>` overrides auto-resolution | unit | `bash scripts/test.sh unit -k reserve_workstream_flag` | ❌ Wave 0 |
| (struct invariant) | Phase 4 claim namespace and Phase 7 reserve namespace are disjoint | structural | `bash scripts/test.sh structural -k phase_07_namespace_disjoint` | ❌ Wave 0 (tests/structural/test_phase_07_shape.py NEW) |
| (struct invariant) | `reserve.py` has 3 Lua scripts + 7-field holder | structural | `bash scripts/test.sh structural -k phase_07_reserve_shape` | ❌ Wave 0 |
| (struct invariant) | Multi-clone tests use per-child `cwd=` | structural | `bash scripts/test.sh structural -k phase_07_multiproc_cwd` | ❌ Wave 0 |
| (struct invariant) | SUMMARY coverage | structural | `bash scripts/test.sh structural -k phase_07_summaries` | ❌ Wave 0 |
| (phase gate) | Full phase acceptance | dispatcher | `bash scripts/verify-phase.sh 07` | ✅ (script exists; phase 07 dir does not yet) |

### Sampling Rate
- **Per task commit:** `bash scripts/test.sh unit -k <task-pattern>` (e.g., `-k reserve` after touching reserve.py).
- **Per wave merge:** `bash scripts/test.sh all` (covers unit + multiprocess + structural).
- **Phase gate:** `bash scripts/verify-phase.sh 07` (covers all above + anti-pattern grep + SUMMARY inventory + Redis check).

### Wave 0 Gaps

The following test files do NOT exist and MUST be created by Phase 7 plans:

- [ ] `tests/unit/test_upstream_identity.py` — canonicalizer test vector + resolver behavior (RESERVE-01)
- [ ] `tests/unit/test_reserve.py` — pure-ops behavior, mirrors `test_claim.py` (RESERVE-02)
- [ ] `tests/unit/test_reserve_verbs.py` — verb-level tests including TTY prompt with monkeypatched stdin (RESERVE-05)
- [ ] `tests/multiprocess/test_reserve_race.py` — two-clone race with per-child `cwd=` (RESERVE-01, RESERVE-02)
- [ ] `tests/multiprocess/test_reserve_three_clones_list.py` — SC#3 demo: three clones see the same reserve-list (RESERVE-03)
- [ ] `tests/structural/test_phase_07_shape.py` — phase-shape invariants (all RESERVE-*)

No framework install needed — pytest is already declared.

## Security Domain

> security_enforcement defaults to enabled.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | em-proj is single-user; session-id is env-var only (T-4-01-01 accept). |
| V3 Session Management | no | Same as Phase 4. |
| V4 Access Control | yes | The Lua compare-on-(session_id, upstream_identity) is the access-control point for reserve refresh/release. Same shape as Phase 4 claim.py. |
| V5 Input Validation | yes | `validate_key` (Phase 2 KV-09) gates the area name. The new `_canonicalize_upstream_url` gates the upstream URL — input rejected → fall back to project_hash, never propagated as-is into a Redis key. |
| V6 Cryptography | no | No new cryptographic primitives. boot_id remains a sha256-truncated string, but that's Phase 3 territory. |

### Known Threat Patterns for em-proj stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| PATH-controlled `git` injection via shell-out | Tampering / Elevation | `subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"], shell=False)` — argv list, no shell expansion. T-3-01-03 carry-forward. |
| Reservation theft (clone B releases clone A's reservation) | Tampering | LUA_RESERVE_COMPARE_AND_DELETE compares BOTH `session_id` AND `upstream_identity`. Same shape as Phase 4 T-4-01-03. |
| Reservation TTL exhaustion (caller refreshes indefinitely) | DoS | MAX_TTL = 86400 caps the Lua EXPIRE argument. Same as Phase 4 T-4-01-05. |
| Injection via crafted git remote URL (malicious `.git/config` causes Redis key collision) | Tampering / Information Disclosure | Canonicalization regex (Pattern 1) rejects URLs that don't match `host:owner/repo` shape; unparseable → None → fallback to project_hash. No raw user input ever becomes a Redis key segment. |
| Anonymous reservation | Elevation | Verb-layer anonymous-refusal gate (`os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()`). Mirrors Phase 4 CLAIM-03. |
| Cross-namespace key collision (claim accidentally lands in reserve namespace) | Tampering | Structural test asserts (a) `state:reserve:` and `state:claim:` prefixes are disjoint, (b) `claim.py` source has no reference to `state:reserve:` and vice versa. |
| Workstream prompt response injection (user enters shell metacharacters) | Tampering / Injection | The workstream string is passed as a Redis HASH field value — never to a shell, never to a Redis Lua script as code. `validate_key` (if applied to workstream) would reject newlines and other metacharacters. **Recommend: apply `validate_key` to the resolved workstream string before passing to `reserve_take`.** |

## Project Constraints (from CLAUDE.md)

CLAUDE.md (project-level) imposes these constraints on Phase 7 work:

- **Test execution:** `bash scripts/test.sh <sub>` only. NO direct `uv run pytest`, NO `python -m pytest`. Multi-process tests via `bash scripts/test.sh multiprocess`. Unit tests via `bash scripts/test.sh unit`. Structural tests via `bash scripts/test.sh structural` (or `all`).
- **Output truncation:** `--tail N` argument on dispatcher subcommands; NEVER hand-pipe to `tail`. Required by global no-pipe-precheck hook.
- **Structural tests:** New phase MUST add `tests/structural/test_phase_07_shape.py`.
- **Phase verification:** `bash scripts/verify-phase.sh 07` is the acceptance gate.
- **Read-only git inspection:** Use `bash scripts/git-ro.sh` for any read-only git op against other paths (NOT `git -C <path>`).
- **Planning artifacts:** `.planning/` is a worktree on the `planning` branch. Do NOT attempt to commit `.planning/` files from main checkout — operate from inside `.planning/`.
- **Commit conventions:** Conventional Commits (`feat(07-NN): ...`). NEVER append `Co-Authored-By: Claude` trailers.

These are LOCKED — the planner must not propose alternatives.

## Sources

### Primary (HIGH confidence)
- `/Users/emonical/projects/personal/ai-tools/em-proj/src/em_proj/identity.py` (lines 56-307) — current identity resolvers
- `/Users/emonical/projects/personal/ai-tools/em-proj/src/em_proj/state/claim.py` (lines 1-526) — claim ops + Lua scripts (Phase 7's structural mirror)
- `/Users/emonical/projects/personal/ai-tools/em-proj/src/em_proj/state/__init__.py` (lines 439-670) — verb wiring patterns (claim, release, check, claim-list)
- `/Users/emonical/projects/personal/ai-tools/em-proj/src/em_proj/state/kv.py` (lines 92-107) — `validate_key` regex (re-used for area name validation)
- `/Users/emonical/projects/personal/ai-tools/em-proj/tests/conftest.py` (lines 53-160) — fixtures (`redis_precheck`, `clean_db`, `multiproc_race`)
- `/Users/emonical/projects/personal/ai-tools/em-proj/tests/multiprocess/test_claim_race.py` (lines 102-408) — race-test pattern template
- `/Users/emonical/projects/personal/ai-tools/em-proj/tests/structural/test_phase_06_shape.py` (lines 1-247) — structural test pattern template
- `/Users/emonical/projects/personal/ai-tools/em-proj/.planning/phases/06-gsd-sdk-workstream-consumer/06-RESEARCH.md` — Phase 6 research (cross-namespace coexistence assumptions)
- `/Users/emonical/projects/personal/ai-tools/em-proj/.planning/REQUIREMENTS.md` (lines 56-62) — RESERVE-01..05 definitions
- `/Users/emonical/projects/personal/ai-tools/em-proj/.planning/ROADMAP.md` (lines 146-157) — Phase 7 goal + success criteria
- `/Users/emonical/projects/personal/ai-tools/em-proj/CLAUDE.md` — project conventions (test.sh, structural tests, verify-phase.sh, git-ro.sh, commit conventions)
- `/Users/emonical/.claude/skills/em-global-state/SKILL.md` — current skill (6 verbs to extend)

### Secondary (MEDIUM confidence)
- `https://github.com/nephila/giturlparse` (README, accessed 2026-05-31) — confirms `giturlparse` library exists with `parse(url).host/owner/repo` attributes; version 0.14.0; Apache 2.0 license. Used to inform Alternatives Considered.
- `https://pypi.org/project/giturlparse/` (referenced but unable to fetch directly due to client challenge; cross-referenced via search results). Confirms package exists on PyPI.

### Tertiary (LOW confidence)
- `[ASSUMED]`: Phase 6 stores workstream name in claim holder's `reason` field. Confirmation required at Plan 07-02 time by reading the patched `sdk/dist/query/workstream.js`. See Open Q-H.
- `[ASSUMED]`: GitHub case-preserve-on-display behavior. See Pattern 1 and Assumption A1.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every dependency is already shipped or stdlib.
- Architecture: HIGH — mirror of Phase 4's already-validated structure with two named deltas.
- Canonical form: MEDIUM — recommended form is sound but user may prefer slash-separator.
- TTY prompt copy: MEDIUM — exact wording is discretionary.
- Workstream schema (Phase 6): MEDIUM — requires verification at Plan execution time. See Open Q-H and Assumption A2.
- Pitfalls: HIGH — derived from Phase 4/5/6 verified patterns plus 3 new Phase-7-specific traps (per-child `cwd=`, two-namespace invariant, git fallback).

**Research date:** 2026-05-31
**Valid until:** 2026-06-30 (30 days — stable substrate, Phase 4/5/6 unchanged, no fast-moving deps)
