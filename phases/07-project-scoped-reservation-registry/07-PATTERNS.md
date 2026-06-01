# Phase 7: Project-Scoped Reservation Registry — Pattern Map

**Mapped:** 2026-05-31
**Files analyzed:** 10 (9 in-repo + 1 cross-repo skill)
**Analogs found:** 9 / 9 in-repo (skill file is documented separately — no in-repo analog)

## Overview

Phase 7 is structurally Phase 4 plus a new identity namespace. Every new file has a direct, recent in-repo analog. The only NEW pattern this phase introduces is per-child `cwd=` in multiprocess `subprocess.Popen` calls plus a fake `.git/config` (RESEARCH §Pattern 5). Everything else copies an existing battle-tested shape.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `src/em_proj/identity.py` (EDIT) | identity-resolver / utility | pure-function | `src/em_proj/identity.py` lines 106–148 (self) | exact (extension alongside `resolve_session_id`, `resolve_project_hash`) |
| `src/em_proj/state/reserve.py` (NEW) | pure-ops / data-access | CRUD + atomic Lua | `src/em_proj/state/claim.py` (full file) | structural mirror — 1:1 except for `KEY_PREFIX`, two-extra ARGV/holder fields, and (session_id, upstream_identity) compare |
| `src/em_proj/state/__init__.py` (EDIT) | verb-wiring / controller | request-response (typer command) | `src/em_proj/state/__init__.py` lines 439–505 (`claim` verb) + 552–584 (`check` verb) + 629–669 (`claim-list` verb) | exact — sibling verbs in same module |
| `tests/unit/test_upstream_identity.py` (NEW) | test (unit, pure-function) | table-driven assertion | `tests/unit/test_identity.py` | exact — same Redis-free posture |
| `tests/unit/test_reserve.py` (NEW) | test (unit, pure-ops + real Redis db=15) | clean_db + assertion | `tests/unit/test_claim.py` | exact — structural mirror of analog |
| `tests/unit/test_reserve_verbs.py` (NEW) | test (unit, verb-level via CliRunner) | CliRunner invoke + JSON assert + monkeypatch stdin | `tests/unit/test_claim_verbs.py` | exact — adds TTY/non-TTY monkeypatch path |
| `tests/multiprocess/test_reserve_race.py` (NEW) | test (multiprocess race) | subprocess.Popen + Redis assertions + per-child env + **per-child cwd=** | `tests/multiprocess/test_claim_race.py` lines 102–179 (race shape) + `tests/multiprocess/test_workstream_consumer_race.py` lines 122–200 (cwd= pattern + path massage) | role-match — analog has per-child env but NOT per-child cwd; cwd= helper is NEW |
| `tests/multiprocess/test_reserve_three_clones_list.py` (NEW) | test (multiprocess SC#3 demo) | three subprocess.Popen with per-child cwd= and `reserve-list` JSON readback | `tests/multiprocess/test_workstream_clobber_demo.py` lines 1–60 (SC#3 demo posture + structure) | role-match — analog is two-process; this is three |
| `tests/structural/test_phase_07_shape.py` (NEW) | test (structural, AST + source-grep + filesystem) | source inspection | `tests/structural/test_phase_06_shape.py` | exact pattern (PHASE_DIR, SUMMARY coverage, source-grep), no cross-repo `xfail` resolver needed |
| `~/.claude/skills/em-global-state/SKILL.md` (EDIT, cross-repo) | skill / docs | markdown | n/a — out-of-repo (same posture as Phase 6's gsd-sdk patch) | NO in-repo analog — see "No Analog Found" |

## Pattern Assignments

### `src/em_proj/identity.py` (EDIT — add `resolve_upstream_identity` + `_canonicalize_upstream_url`)

**Analog:** `src/em_proj/identity.py` itself (extend self) — closest by far. Last touched commit `81c094db`.

**Module-docstring extension pattern** (copy posture from lines 1–55 — section-by-section bullet list of public API + invariants). Add a new "Phase 7 — upstream identity" subsection that mirrors the format of lines 19–24 ("Stale-detection probe API (Plan 03-02 additions — IDENT-02):"). The new section should enumerate `resolve_upstream_identity` and `_canonicalize_upstream_url` exactly the way the existing module enumerates its previous additions.

**Resolver shape — copy from lines 106–122 (`resolve_session_id`):**

```python
def resolve_session_id() -> str:
    """Return the calling session's identity string.

    Fallback chain (D-12):
      1. ``CLAUDE_CODE_SESSION_ID`` — UUID set by Claude Code...
      2. ``pid-<os.getpid()>`` — deterministic fallback...
    """
    val = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if val:
        return val
    return f"pid-{os.getpid()}"
```

**Resolver shape — copy from lines 125–147 (`resolve_project_hash`):**

```python
def resolve_project_hash() -> str:
    """Return the project-hash string matching the ~/.claude/projects/<hash>/ convention.
    ...
    Design choice — cwd-only, no git-toplevel fallback:
      Shelling out to ``git rev-parse --show-toplevel`` introduces a PATH-controlled
      attack surface (T-3-01-03). ...
    """
    cwd = os.path.abspath(os.getcwd())
    return cwd.replace("/", "-")
```

**What `resolve_upstream_identity` COPIES verbatim from analog:**
- Function signature shape (`def resolve_upstream_identity(cwd: str | None = None) -> str`).
- Docstring template — "Fallback chain (D-...)" enumerated bullets, "Design choice" block citing T-3-01-03.
- Stateless posture — no Redis import, no module-level cache.
- `from __future__ import annotations` already at line 56 — reuse, do not duplicate.

**What `resolve_upstream_identity` INVENTS (per RESEARCH §Pattern 2):**
- `subprocess.run(["git", "-C", target_cwd, "remote", "get-url", "origin"], shell=False, capture_output=True, text=True, timeout=5.0, check=False)`.
- `try/except (FileNotFoundError, subprocess.TimeoutExpired)` → `return resolve_project_hash()`.
- `if result.returncode != 0` → fall back to `resolve_project_hash()`.
- Module-private `_canonicalize_upstream_url(raw: str) -> str | None` with the two regexes from RESEARCH §Pattern 1.
- Module addition: `import subprocess` at top.

**Critical: invariant the analog establishes (line 5–13) that THIS file's edit must NOT violate:**
> "NO ``import typer`` ... NO redis exceptions ... NO redis_client import — identity is Redis-free."

`subprocess` is new but does NOT break the Redis-free invariant.

---

### `src/em_proj/state/reserve.py` (NEW — pure-ops mirror of claim.py)

**Analog:** `src/em_proj/state/claim.py` (entire file). Last touched commit `0e6e319`.

**This is the most-copied file of Phase 7.** RESEARCH §Pattern 3 explicitly calls it a "structural mirror" with three named deltas.

**Module docstring pattern — copy from claim.py lines 1–51, change key namespacing section:**

```python
"""Pure area-claim operations for `em-proj state claim/release/check` — no typer imports.

Per D-17 this module owns ALL claim business logic; verb wiring in
`em_proj/state/__init__.py` (Plan 04-02) is a thin translation layer:
parse argv → call a function here → call `emit_*`. Nothing in this file
imports `typer`.
...
Claim key namespacing:
  - Every claim key is stored in Redis as ``state:claim:<project_hash>:<area>`` (KEY_PREFIX).
  - Claims are project-scoped (project_hash in the key) unlike locks (user-global).
  ...
"""
```

For reserve.py: keep the same structure, swap "claim" → "reservation", and document the upstream_identity namespacing + 7-field holder + (session_id, upstream_identity) refresh compare.

**Imports — copy verbatim from claim.py lines 52–58:**

```python
from __future__ import annotations

import time

from em_proj.identity import resolve_session_id, resolve_project_hash
from em_proj.redis_client import get_client
from em_proj.state.kv import validate_key, ValidationError  # noqa: F401
```

For reserve.py: ADD `from em_proj.identity import resolve_upstream_identity` to this import block.

**Module constants — copy from claim.py lines 60–80, change KEY_PREFIX only:**

```python
KEY_PREFIX: str = "state:claim:"      # ← change to "state:reserve:" in reserve.py
TTL_DEFAULT: int = 1800               # ← keep
MIN_TTL: int = 60                     # ← keep
MAX_TTL: int = 86400                  # ← keep
MAX_REASON_CHARS: int = 256           # ← keep
```

**Lua script pattern — copy from claim.py lines 103–124 (`LUA_CLAIM_REFRESH_OR_TAKE`), apply RESEARCH §Pattern 3 deltas:**

```python
LUA_CLAIM_REFRESH_OR_TAKE: str = """
local exists = redis.call('EXISTS', KEYS[1])
if exists == 0 then
  redis.call('HSET', KEYS[1],
    'session_id', ARGV[1],
    'project_hash', ARGV[2],
    'reason', ARGV[3],
    'claimed_at', ARGV[4],
    'expires_at', ARGV[5]
  )
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[6]))
  return 'taken'
end
local sid = redis.call('HGET', KEYS[1], 'session_id')
local phash = redis.call('HGET', KEYS[1], 'project_hash')
if sid == ARGV[1] and phash == ARGV[2] then
  redis.call('HSET', KEYS[1], 'expires_at', ARGV[5])
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[6]))
  return 'refreshed'
end
return 'conflict'
"""
```

**Deltas for `LUA_RESERVE_REFRESH_OR_TAKE`:**
1. HSET now writes 7 fields (insert `upstream_identity`, `workstream` between `project_hash` and `reason`).
2. ARGV grows from [1..6] to [1..8].
3. Refresh-guard compares `(session_id, upstream_identity)` NOT `(session_id, project_hash)`.

**Critical Pitfall #3 from RESEARCH** — define a `_RESERVE_ARGV_ORDER` constant tuple to prevent ARGV-index drift. The unit test must assert that after a take, `client.hgetall(key)` returns EXACTLY the 7 expected fields.

**Compare-and-delete Lua pattern — copy from claim.py lines 139–149:**

```python
LUA_CLAIM_COMPARE_AND_DELETE: str = """
local exists = redis.call('EXISTS', KEYS[1])
if exists == 0 then return -1 end
local sid = redis.call('HGET', KEYS[1], 'session_id')
local phash = redis.call('HGET', KEYS[1], 'project_hash')
if sid == ARGV[1] and phash == ARGV[2] then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""
```

For reserve.py: replace `phash`/`project_hash` field check with `upstream`/`upstream_identity`. ARGV stays at 2 (session_id, upstream_identity).

**Check Lua pattern — copy from claim.py lines 161–165 verbatim** (no changes — it's just `EXISTS` + `HGETALL`).

**Exceptions pattern — copy from claim.py lines 173–216:**

```python
class HeldByAnother(Exception):
    code: str = "held_by_another"
    def __init__(self, holder: dict | None = None, message: str | None = None) -> None:
        self.holder = holder
        ...

class ClaimNotHeld(Exception):
    code: str = "not_held"
    ...
```

For reserve.py: rename to `HeldByAnother` (keep) and `ReserveNotHeld`. Same body, only the default message changes ("claim" → "reservation").

**`_build_redis_key` — copy from claim.py lines 224–230, change argument:**

```python
def _build_redis_key(project_hash: str, area: str) -> str:
    return KEY_PREFIX + project_hash + ":" + area
```

For reserve.py: signature becomes `_build_redis_key(upstream_identity: str, area: str)`; body unchanged structurally.

**`_make_holder` — copy from claim.py lines 233–254, expand to 7 fields:**

```python
def _make_holder(area: str, reason: str | None, ttl: int) -> dict:
    now = time.time()
    return {
        "session_id": resolve_session_id(),
        "project_hash": resolve_project_hash(),
        "reason": reason,
        "claimed_at": now,
        "expires_at": now + ttl,
    }
```

For reserve.py: signature gains `upstream_identity: str` and `workstream: str` params (verb resolves these and passes in); returned dict adds those two fields.

**`_hgetall_to_holder` — copy from claim.py lines 257–275, expand to 7 fields** (add `upstream_identity` and `workstream` string-pass-through entries).

**`_validate_reason` and `_validate_ttl` — copy verbatim from claim.py lines 278–301.** No changes; the bounds are identical (MIN_TTL=60, MAX_TTL=86400, MAX_REASON_CHARS=256).

**`claim_take` / `reserve_take` — copy from claim.py lines 309–372:**

Critical block to copy — the "conflict → HGETALL → empty-guard" pattern (lines 354–372):

```python
if result == "taken":
    return holder

if result == "refreshed":
    return holder

# result == "conflict" — different holder present.
raw = client.hgetall(redis_key)
existing = _hgetall_to_holder(raw) if raw else None
raise HeldByAnother(holder=existing)
```

For reserve.py: signature gains `upstream_identity` and `workstream` parameters (verb passes them in); EVAL ARGV list expands by 2 elements (insert at positions 3 and 4 per the Lua deltas above).

**`claim_release` / `reserve_release` — copy from claim.py lines 375–410.** Delta: the ARGV[2] is now `upstream_identity` instead of `project_hash` (matching the compare-and-delete Lua delta).

**`claim_list_by_prefix` / `reserve_list_by_prefix` — copy from claim.py lines 413–487.** Critical deltas:
- Scope by `upstream_identity` (NEW parameter or call `resolve_upstream_identity()` internally — RESEARCH §Pattern 3 implies parameter).
- Inject `area` field on holder (already done at line 461 — preserve verbatim).
- Same `mine`/`active`/`stale` filter logic.
- Same CR-01 ttl=-2 guard (already in claim.py lines 472–475 from commit `0e6e319`) — copy verbatim.

**`claim_check` / `reserve_check` — copy from claim.py lines 490–525.** Delta: scope-key built from `upstream_identity` (passed parameter) not `project_hash`.

---

### `src/em_proj/state/__init__.py` (EDIT — add `reserve`, `reserve-list` verbs; add `--upstream` flag to `check`)

**Analog:** `src/em_proj/state/__init__.py` itself — same module, sibling verbs. Last touched commit `f8353c3`.

**Import-block pattern — extend the block at lines 105–115:**

```python
from em_proj.state.claim import (
    TTL_DEFAULT as CLAIM_TTL_DEFAULT,
    MIN_TTL as CLAIM_MIN_TTL,
    MAX_TTL as CLAIM_MAX_TTL,
    HeldByAnother as ClaimHeldByAnother,
    ClaimNotHeld,
    claim_check,
    claim_list_by_prefix,
    claim_release,
    claim_take,
)
```

For Phase 7: add an analogous `from em_proj.state.reserve import (...)` block immediately after — alias `HeldByAnother as ReserveHeldByAnother` to disambiguate from the claim's exception (which is already aliased the same way). Also `from em_proj.identity import resolve_upstream_identity`. **Pitfall #4 mitigation:** alias `claim_check as workstream_check` to make the workstream-active lookup unmistakable.

**`reserve` verb — mirror `claim` verb at lines 439–505:**

```python
@state_app.command("claim")
def claim(
    area: Annotated[str, typer.Argument(help="The area to claim.")],
    ttl: Annotated[int | None, typer.Option("--ttl", min=CLAIM_MIN_TTL, max=CLAIM_MAX_TTL, ...)] = None,
    reason: Annotated[str | None, typer.Option("--reason", ...)] = None,
    json_flag: Annotated[bool | None, typer.Option("--json/--no-json", help=_JSON_HELP)] = None,
) -> None:
    json_mode = resolve_json_mode(json_flag)
    if not os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip():
        emit_error("anonymous_claim", "anonymous claims refused", json_mode=json_mode)
    client = get_client()
    die_if_redis_unreachable(client)
    effective_ttl = ttl if ttl is not None else CLAIM_TTL_DEFAULT
    try:
        holder = claim_take(area, ttl=effective_ttl, reason=reason)
    except ClaimHeldByAnother as e:
        emit_held_by_another("held_by_another", f"Area '{area}' claimed by session ...", holder=e.holder, json_mode=json_mode)
    except ValidationError as e:
        emit_error(e.code, e.message, json_mode=json_mode)
    else:
        emit_ok({"area": area, "ttl": effective_ttl, "claimed_at": holder["claimed_at"], "expires_at": holder["expires_at"]}, json_mode=json_mode)
```

**Deltas for `reserve` verb:**
1. Add `--workstream <name>` typer Option (default `None`).
2. After `json_mode` resolution and BEFORE Redis pre-check: call `_resolve_workstream(workstream, json_mode)` per RESEARCH §Pattern 4 (TTY-prompt logic — keep in `__init__.py`, NOT in `reserve.py`).
3. After workstream resolved: call `upstream = resolve_upstream_identity()`.
4. Call `reserve_take(area=..., upstream_identity=upstream, workstream=resolved_workstream, ttl=effective_ttl, reason=reason)`.
5. Anonymous-refusal gate stays identical (RESEARCH SECURITY §"Anonymous reservation" line 1145).
6. JSON envelope on success includes `upstream_identity`, `workstream` fields (RESEARCH §Example 3 lines 826–838).

**`_resolve_workstream` helper (NEW private module function in `__init__.py`)** — copy the dual-isatty pattern from `state/__init__.py:344–352`:

```python
# 5b. --warn on non-TTY (D-07 T-3-XX-05 dual-isatty check):
#     BOTH stdout AND stdin must be TTYs — refuse with exit 1 if not.
if not (sys.stdout.isatty() and sys.stdin.isatty()):
    emit_error(
        "warn_requires_tty",
        "--warn requires a TTY for confirmation; ...",
        json_mode=json_mode,
    )
```

For Phase 7: same dual-isatty test, but use `sys.stdin.isatty() and sys.stdout.isatty()` (order-insensitive). On TTY: prompt via stderr (Pattern 4); on non-TTY: emit_error with the locked actionable message.

**`reserve-list` verb — mirror `claim-list` at lines 629–669:**

```python
@state_app.command("claim-list")
def claim_list(
    mine: Annotated[bool, typer.Option("--mine/--no-mine", ...)] = False,
    active: Annotated[bool, typer.Option("--active/--no-active", ...)] = False,
    stale: Annotated[bool, typer.Option("--stale/--no-stale", ...)] = False,
    json_flag: Annotated[bool | None, typer.Option("--json/--no-json", help=_JSON_HELP)] = None,
) -> None:
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    holders = claim_list_by_prefix(mine=mine, active=active, stale=stale)
    emit_ok({"items": holders}, json_mode=json_mode)
```

**Deltas for `reserve-list` verb (RESEARCH §Example 4):**
1. Replace `mine`/`active`/`stale` filters with `--category` (string filter) + `--upstream` (override).
2. If `--upstream` is passed, canonicalize it via `_canonicalize_upstream_url`; else `resolve_upstream_identity()`.
3. Filter: `if category: holders = [h for h in holders if h["area"].split(".", 1)[0] == category]`.
4. JSON output: `{"upstream_identity": canonical, "items": holders}`.
5. Same envelope shape as `claim-list` (uses `emit_ok` with `items` key).

**`check --upstream` flag — extend `check` verb at lines 552–584:**

```python
@state_app.command("check")
def check(
    area: Annotated[str, typer.Argument(help="The area to check.")],
    json_flag: Annotated[bool | None, typer.Option("--json/--no-json", help=_JSON_HELP)] = None,
) -> None:
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    try:
        holder = claim_check(area)
    except ClaimNotHeld:
        emit_not_found(f"Area '{area}' is not claimed", json_mode=json_mode)
    ...
    emit_ok({"area": area, "holder": holder}, json_mode=json_mode)
```

**Delta:** Add `--upstream` typer Option. When set, call `reserve_check(area, upstream_identity=canonical_upstream)` instead of `claim_check(area)`. The exception class to catch changes from `ClaimNotHeld` to `ReserveNotHeld`. Holder dict has 7 fields instead of 5 (auto-flowed through `emit_ok`).

---

### `tests/unit/test_upstream_identity.py` (NEW)

**Analog:** `tests/unit/test_identity.py` (entire file). Last touched commit `cec8949`.

**Module docstring pattern — copy from lines 1–20:**

```python
"""Unit tests for em_proj.identity — stateless, no Redis required.

Covers IDENT-01 contract: env-var resolution, fallback chain, tr-/-to-dash hash,
and composite dict shape + type invariants.

No Redis fixtures here — identity.py is stdlib + psutil only; tests run with Redis
completely absent (no live Redis dependency).

Test inventory (10 tests):
  test_resolve_session_id_with_env_var_set        — env var returned as-is
  ...
"""
```

For Phase 7: same posture (no Redis fixtures); tests are stdlib + `monkeypatch` + `tmp_path` only.

**Test pattern — copy `monkeypatch.chdir(tmp_path)` from line 72:**

```python
def test_resolve_project_hash_slash_to_dash(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    result = resolve_project_hash()
    expected = str(tmp_path.resolve()).replace("/", "-")
    assert result == expected
```

For Phase 7: each `resolve_upstream_identity` test sets up a `tmp_path` with (or without) a fake `.git/config`, monkeypatches cwd, calls the resolver, asserts the canonical form.

**REQUIRED test vector** — RESEARCH §Pattern 1 lines 320–333 specifies a 13-row input/output table that MUST be baked in as a parameterized pytest fixture. Use `@pytest.mark.parametrize` with the verbatim 13 rows.

**INVENT (Phase-7 specific):**
- Helper `_make_fake_git_config(tmp_path: Path, origin_url: str) -> None` (mirrors RESEARCH §Pattern 5's `_make_fake_clone` but for ONE directory). Writes `.git/config` with `[remote "origin"]\n\turl = <url>\n` and a `.git/HEAD` for safety.
- Fallback tests: `tmp_path` with no `.git/` → `resolve_upstream_identity()` returns `resolve_project_hash()` result (Pitfall #7).
- Empty-URL test (Pitfall #2): fake config with `url = ""` → fall back to project_hash.

---

### `tests/unit/test_reserve.py` (NEW)

**Analog:** `tests/unit/test_claim.py` (entire file). Last touched commit `598ca8d`.

**Module-docstring pattern — copy from lines 1–13:**

```python
"""Unit tests for em_proj.state.claim — pure claim ops against real Redis on db=15.

Uses the clean_db fixture from tests/conftest.py for per-test isolation.
Validates CLAIM-01, CLAIM-02, and all 8 behavior cases from 04-01-PLAN.md.
...
"""
```

For Phase 7: same posture — validates RESERVE-01..02 and the (TBD count from plan) behavior cases from 07-NN-PLAN.md.

**Autouse fixtures — copy verbatim from lines 37–48:**

```python
@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()

@pytest.fixture(autouse=True)
def _point_claim_at_test_db(monkeypatch):
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")
```

**Module-level constant checks — copy from lines 56–69, change values:**

```python
def test_ttl_default_is_1800() -> None:
    assert TTL_DEFAULT == 1800

def test_key_prefix_is_state_claim() -> None:
    assert KEY_PREFIX == "state:claim:"   # ← change to "state:reserve:" for Phase 7

def test_ttl_bounds() -> None:
    assert MIN_TTL == 60
    assert MAX_TTL == 86400
```

**Behavior-case shape — copy from lines 77–102 (Behavior Case 1 + Redis-key existence test):**

```python
def test_claim_take_fresh_area_returns_holder(clean_db) -> None:
    holder = claim_take("foo", reason="testing fresh claim")
    assert isinstance(holder, dict)
    required_keys = {"session_id", "project_hash", "reason", "claimed_at", "expires_at"}
    assert required_keys == set(holder.keys())
    ...

def test_claim_take_area_key_exists_in_redis(clean_db) -> None:
    from em_proj.identity import resolve_project_hash
    project_hash = resolve_project_hash()
    claim_take("myarea")
    test_client = redis_module.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    key = f"{KEY_PREFIX}{project_hash}:myarea"
    assert test_client.exists(key) == 1
```

**Deltas for Phase 7:**
- `required_keys` now has 7 elements: add `"upstream_identity"`, `"workstream"`.
- Redis key shape uses upstream_identity instead of project_hash: `f"{KEY_PREFIX}{upstream_identity}:{area}"`.
- Tests must pass `upstream_identity=...` and `workstream=...` arguments to `reserve_take(...)` since the pure-op signature includes them (RESEARCH §Pattern 3 — verb resolves them, pure-op receives them as params).

**Pitfall #3 mitigation test (NEW for Phase 7):** after `reserve_take`, assert `client.hgetall(key)` returns EXACTLY the 7 expected keys with the expected values — catches ARGV-index drift.

---

### `tests/unit/test_reserve_verbs.py` (NEW)

**Analog:** `tests/unit/test_claim_verbs.py` (entire file). Last touched commit `3f8536c2`.

**CliRunner setup — copy verbatim from lines 18–55:**

```python
from typer.testing import CliRunner
import em_proj.redis_client as rc
from em_proj.cli import app

try:
    runner = CliRunner(mix_stderr=False)
except TypeError:
    runner = CliRunner()

@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()

@pytest.fixture(autouse=True)
def _point_at_test_db(monkeypatch):
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")

@pytest.fixture(autouse=True)
def _set_session_id(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "test-session-abc123")
```

**Verb invocation + JSON assertion pattern — copy from lines 61–69:**

```python
def test_claim_exits_0_with_json_output(clean_db):
    result = runner.invoke(app, ["state", "claim", "docs/api", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["status"] == "ok"
    assert payload["data"]["area"] == "docs/api"
```

For Phase 7: same shape, invoke `["state", "reserve", "migrations.v200", "--workstream", "test-ws", "--json"]` (pass `--workstream` explicitly to bypass TTY logic in non-TTY-targeted tests).

**Anonymous-refusal pattern — copy verbatim from lines 76–99 (test 2 + test 2b):**

```python
def test_claim_anonymous_refusal_exit_1(clean_db, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    result = runner.invoke(app, ["state", "claim", "docs/api", "--json"])
    assert result.exit_code == 1
    output_text = result.output
    stderr_text = getattr(result, "stderr", "") or ""
    combined = output_text + stderr_text
    assert "anonymous claims refused" in combined
```

For Phase 7: same shape; assert `"anonymous reservations refused"` (or whatever exact wording the plan locks).

**INVENT (Phase 7-specific) — TTY prompt path:**

Mirrors Phase 4's anonymous-refusal monkeypatch shape but for `sys.stdin.isatty()` / `sys.stdout.isatty()`:

```python
def test_reserve_tty_prompt_resolves_workstream(clean_db, monkeypatch):
    """RESERVE-05: reserve verb prompts on TTY when workstream unset."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.stdin.readline", lambda: "my-prompted-ws\n")
    result = runner.invoke(app, ["state", "reserve", "migrations.v200", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["data"]["workstream"] == "my-prompted-ws"

def test_reserve_nontty_exits_1(clean_db, monkeypatch):
    """RESERVE-05: reserve verb exits 1 on non-TTY when workstream unset."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    result = runner.invoke(app, ["state", "reserve", "migrations.v200", "--json"])
    assert result.exit_code == 1
    assert "workstream unresolved" in (result.output + getattr(result, "stderr", ""))
```

---

### `tests/multiprocess/test_reserve_race.py` (NEW)

**Analog (race shape):** `tests/multiprocess/test_claim_race.py` lines 102–179. Last touched commit `ec0247a`.
**Analog (per-child cwd= + path massage):** `tests/multiprocess/test_workstream_consumer_race.py` lines 122–200. Last touched commit `9513c60`.

**Module-docstring pattern — copy from test_claim_race.py lines 1–39:**

```python
"""Multi-process race tests for `em-proj state claim/release/check` (Plan 04-03).

Uses real ``em-proj`` subprocess invocations against db=15 (via ``EM_PROJ_REDIS_DB``
injection) to prove claim/release/check correctness under concurrent access.
...

Design invariants (Phase 1 RESEARCH Pitfalls, carried forward):
  - subprocess.Popen NOT multiprocessing.Process (#6 -- fork+exec macOS safety)
  - .communicate(timeout=) NOT .wait() (#2 -- pipe-buffer deadlock)
  - EM_PROJ_REDIS_DB=15 in child env (#4 -- never writes to prod db=0)
...
Import pattern (per Phase 1 Plan 04 SUMMARY):
  from tests.conftest import EM_PROJ_BIN, TEST_DB, RaceResult
"""
```

For Phase 7: same posture + add the Phase-7-specific invariant block:

> **Phase-7-specific invariant:** Per-child `cwd=` is REQUIRED (Pitfall #6 from 07-RESEARCH). Tests that vary only `env=` produce false-positive passes because both children resolve the SAME upstream_identity from the SAME (test-runner) cwd.

**Race-test shape — copy verbatim from test_claim_race.py lines 102–179** including the per-child env injection, tight launch loop, `.communicate(timeout=)`, sorted-exit-codes assertion, and post-race TTL > 0 check.

**Per-child cwd= pattern — copy from test_workstream_consumer_race.py lines 174–185:**

```python
proc_a = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    env=child_a_env,
    cwd=str(tmp_path),   # ← key line — cwd kwarg routes resolve_upstream_identity()
)
```

**Fake-clone helper — INVENT (NEW for Phase 7, per RESEARCH §Pattern 5 lines 516–539):**

```python
def _make_fake_clone(parent: Path, name: str, origin_url: str) -> Path:
    """Create a fake clone at parent/name with .git/config containing origin."""
    clone_dir = parent / name
    git_dir = clone_dir / ".git"
    git_dir.mkdir(parents=True)
    config = (
        '[remote "origin"]\n'
        f'\turl = {origin_url}\n'
        '\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
    )
    (git_dir / "config").write_text(config)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    return clone_dir
```

**Cmd shape — RESEARCH §Pattern 5 lines 568–576:** must pass `--workstream test-ws` explicitly to bypass the TTY prompt (Pitfall #5 from RESEARCH).

**Post-race assertion — extend test_claim_race.py lines 172–179 pattern:** assert holder has `upstream_identity == "github.com:emonical/roleplay-engine"` (the canonical form, same across both clones). RESEARCH §Pattern 5 lines 600–618 shows the exact `reserve-list` readback assertion shape.

---

### `tests/multiprocess/test_reserve_three_clones_list.py` (NEW, optional but recommended for SC#3 demo)

**Analog (SC#3 demo posture):** `tests/multiprocess/test_workstream_clobber_demo.py` lines 1–60.
**Analog (race + post-state read):** same as `test_reserve_race.py` analog.

**Module-docstring pattern — copy from test_workstream_clobber_demo.py lines 1–27:**

```python
"""SC#3 side-by-side demo: old-path clobber vs. new-path resolution (Plan 06-02).

This file IS the Phase 6 SC#3 human-runnable demo. Run both tests together:

    bash scripts/test.sh multiprocess -k clobber_demo
...
Reference: ROADMAP Phase 6 Success Criteria #3 (SC#3).
"""
```

For Phase 7: same posture — this file IS the Phase 7 SC#3 demo. Three sibling clones; one reserves; the other two see the reservation via `reserve-list`.

**Test shape — three-process variant of the race pattern:**
1. `_make_fake_clone(tmp_path, "clone-a", origin)`, same for `clone-b`, `clone-c` — all three with the SAME `origin_url`.
2. Clone A: subprocess.Popen with `cwd=clone_a`, takes a reservation.
3. After A returns 0: clone B and C BOTH call `em-proj state reserve-list --json` via `subprocess.run(..., cwd=clone_b/c)`.
4. Assert B and C see IDENTICAL `items` lists, each containing the holder from A.
5. Assert `items[0]["upstream_identity"] == "github.com:emonical/roleplay-engine"` from BOTH B and C.

---

### `tests/structural/test_phase_07_shape.py` (NEW)

**Analog:** `tests/structural/test_phase_06_shape.py` (entire file). Last touched commit `05b01e3`.

**Module docstring pattern — copy from lines 1–23:**

```python
from __future__ import annotations
"""Phase 6 structural invariants — source-grep and filesystem assertions.

Encodes plan acceptance criteria as runtime assertions for Phase 6
(gsd-sdk Workstream Consumer):
...
Each structural file is self-contained (no imports from sibling
test_phase_*_shape.py files) per Phase 1+2+3+4+5 precedent.
"""
```

For Phase 7: same posture, replace "Phase 6" → "Phase 7", enumerate Phase 7 invariants (RESEARCH §Validation Architecture lines 1099–1102).

**Path setup — copy verbatim from lines 33–34:**

```python
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "06-gsd-sdk-workstream-consumer"
```

For Phase 7: change to `"07-project-scoped-reservation-registry"`.

**Skip-on-missing PHASE_DIR pattern — copy from lines 223–247 (the `test_phase_06_summaries_present` test):**

```python
def test_phase_06_summaries_present() -> None:
    if not PHASE_DIR.exists():
        pytest.skip(...)
    plans = sorted(PHASE_DIR.glob("06-*-PLAN.md"))
    if not plans:
        pytest.skip(...)
    for plan in plans:
        summary = plan.parent / plan.name.replace("-PLAN.md", "-SUMMARY.md")
        assert summary.exists()
```

For Phase 7: change `"06-"` glob → `"07-"`.

**Phase-7-specific invariants to assert (RESEARCH §Pitfalls + §Validation Architecture):**

1. **Two-namespace disjoint (Pitfall #8):**
   - `KEY_PREFIX` in `src/em_proj/state/claim.py` == `"state:claim:"`.
   - `KEY_PREFIX` in `src/em_proj/state/reserve.py` == `"state:reserve:"`.
   - Source of `claim.py` does NOT contain `"state:reserve:"`.
   - Source of `reserve.py` does NOT contain `"state:claim:"`.
   - Source of `claim.py` does NOT contain `"upstream_identity"`.

2. **reserve.py shape:**
   - Module exists at `src/em_proj/state/reserve.py`.
   - Defines all 3 Lua scripts (string-grep for `LUA_RESERVE_REFRESH_OR_TAKE`, `LUA_RESERVE_COMPARE_AND_DELETE`, `LUA_RESERVE_CHECK`).
   - Holder shape via test: `import em_proj.state.reserve` + check via inspect that `_make_holder` returns a 7-key dict.

3. **Verb wiring:**
   - `src/em_proj/state/__init__.py` source contains `@state_app.command("reserve")` and `@state_app.command("reserve-list")`.
   - `check` verb references `--upstream` flag (source-grep for `"--upstream"` near the `check` decorator).

4. **Per-child cwd= in multi-clone tests (Pitfall #6):**
   - Source of `tests/multiprocess/test_reserve_race.py` contains `cwd=` as a kwarg in every `subprocess.Popen(` call. AST check or source-grep with regex `subprocess\.Popen\([^)]*cwd=`.

5. **SUMMARY coverage** — same pattern as Phase 6 above.

**NOTE — no cross-repo `xfail` resolver needed:** Phase 6's `test_phase_06_shape.py` uses `_resolve_workstream_artifact` + `pytest.xfail` because it audits the npm-installed gsd-sdk source tree. Phase 7's structural assertions are all on em-proj's OWN source tree — drop the `_resolve_workstream_artifact` pattern entirely; use plain `(REPO_ROOT / "src/em_proj/state/reserve.py").read_text()`.

---

## Shared Patterns (apply to multiple Phase 7 files)

### Anonymous-claim refusal gate (verb layer)

**Source:** `src/em_proj/state/__init__.py` lines 475–476 (`claim` verb).
**Apply to:** `reserve` verb (NEW).

```python
if not os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip():
    emit_error("anonymous_claim", "anonymous claims refused", json_mode=json_mode)
```

Phase 7 should use code `"anonymous_claim"` (same code — single user-facing error class) but message `"anonymous reservations refused"` or keep verbatim "anonymous claims refused" (planner decides).

### Redis-pre-check chokepoint (D-18)

**Source:** `src/em_proj/state/__init__.py` lines 478–480 (every verb).
**Apply to:** `reserve` verb, `reserve-list` verb, `check --upstream` extension.

```python
client = get_client()
die_if_redis_unreachable(client)
```

### Dual-isatty TTY gate (D-07)

**Source:** `src/em_proj/state/__init__.py` lines 344–352 (`lock --warn` flow).
**Apply to:** `reserve` verb's `_resolve_workstream` fallback chain.

```python
if not (sys.stdout.isatty() and sys.stdin.isatty()):
    emit_error("warn_requires_tty", "...", json_mode=json_mode)
```

For Phase 7: error code becomes `"workstream_unresolved"`, message becomes `"workstream unresolved — set it via gsd-sdk query workstream.set <name> or pass --workstream <name>"`.

### Lua compare-on-(session, scope) atomicity

**Source:** `src/em_proj/state/claim.py` lines 103–149 (refresh-or-take + compare-and-delete).
**Apply to:** `reserve.py` (NEW) — both Lua scripts.

**Critical delta:** the second compare term changes from `project_hash` to `upstream_identity`. This is THE core semantic change of Phase 7 (enables cross-clone refresh).

### Holder hgetall round-trip + empty-guard

**Source:** `src/em_proj/state/claim.py` lines 365–372 (conflict path) and lines 257–275 (`_hgetall_to_holder`).
**Apply to:** `reserve.py` (NEW). Same empty-dict guard:

```python
raw = client.hgetall(redis_key)
existing = _hgetall_to_holder(raw) if raw else None
raise HeldByAnother(holder=existing)
```

### Race-test scaffolding

**Source:** `tests/conftest.py` lines 30–32 (constants), 86–92 (`clean_db`), and 94–160 (`multiproc_race` — NOT directly used by Phase 7 race tests per RESEARCH Open Q-I).
**Apply to:** `tests/multiprocess/test_reserve_race.py` and `tests/multiprocess/test_reserve_three_clones_list.py`.

Use `clean_db` directly + direct `subprocess.Popen` (NOT `multiproc_race`) — same shape as `test_claim_race.py`'s test 1.

### Subprocess invariants (Phase 1 pitfalls)

**Source:** `tests/conftest.py` lines 9–17 (docstring).
**Apply to:** all `tests/multiprocess/test_reserve_*.py` files.

- `subprocess.Popen` NOT `multiprocessing.Process` (macOS fork+exec safety).
- `.communicate(timeout=)` NOT `.wait()` (pipe-buffer deadlock).
- `EM_PROJ_REDIS_DB=15` in child env (never write to prod db=0).

**NEW for Phase 7:** per-child `cwd=` in addition to per-child `env=` (Pitfall #6 from RESEARCH).

---

## No Analog Found

| File | Role | Data Flow | Reason / Treatment |
|------|------|-----------|--------------------|
| `~/.claude/skills/em-global-state/SKILL.md` | skill / markdown | docs | Cross-repo deliverable (outside em-proj's git tree). Same posture as Phase 6's gsd-sdk JS patch — the planner treats it as a documented "extend the existing 6-verb skill with a 7th verb (`reservations`) per RESEARCH §Example 5 lines 894–920". The existing 6 verbs in the skill (`list/get/locks/claims/unlock/release`) ARE the local analog for shape; the planner reads `~/.claude/skills/em-global-state/SKILL.md` (244 lines) at plan-write time to copy the verb-subsection shape verbatim. No structural test gates this file (it lives outside em-proj's repo); document the deliverable in PLAN.md and verify manually. |

---

## Metadata

**Analog files & last-modified commits (for planner traceability):**

| Analog | Commit |
|--------|--------|
| `src/em_proj/identity.py` | `81c094db590b81c9b86420befe9e113cf1437ce6` |
| `src/em_proj/state/claim.py` | `0e6e3199025f2aabb048034215b3f392a3585bb8` |
| `src/em_proj/state/__init__.py` | `f8353c3a1b3975d8a5cc985e6b78b96be6979bf6` |
| `tests/unit/test_claim.py` | `598ca8dbb765dc79ad67d2f3b7b052dd061e7671` |
| `tests/unit/test_identity.py` | `cec8949e818fb061137f41ba15bbb0715ef5e5cc` |
| `tests/unit/test_claim_verbs.py` | `3f8536c2468e356ffbdef446173c845ced382704` |
| `tests/multiprocess/test_claim_race.py` | `ec0247aed707f7cce0d2d67c76d5d8fcec79633d` |
| `tests/multiprocess/test_workstream_consumer_race.py` | `9513c60da41a2969a4d87026105a1a1a5122e4c2` |
| `tests/structural/test_phase_06_shape.py` | `05b01e33674b64d8e1e8939736261943a56c6b19` |

**Analog search scope:** `src/em_proj/`, `tests/unit/`, `tests/multiprocess/`, `tests/structural/`, `.planning/phases/06-*/`. Discovery via `ls` (file inventory) + `git log -1 --format=%H -- <path>` per analog.

**Files scanned (full reads):** 9 (analogs above) + RESEARCH.md (1502 lines, read in 4 chunks) + PROJECT.md + ROADMAP.md + REQUIREMENTS.md.

**Pattern extraction date:** 2026-05-31.

**Key insight from RESEARCH (lines 645–647) the planner must respect:**
> "Phase 7 is Phase 4 plus a new identity namespace. The temptation to 'improve' the claim semantics (e.g., consolidate claim.py and reserve.py into one parameterized module) is the dangerous failure mode — resist it. Two modules, structurally parallel, is the correct decomposition."

This pattern map enforces that posture — every "copy from claim.py" instruction above is a deliberate duplication, not a maintenance liability.

## PATTERN MAPPING COMPLETE
