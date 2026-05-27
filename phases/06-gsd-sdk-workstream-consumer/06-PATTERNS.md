# Phase 6: gsd-sdk Workstream Consumer — Pattern Map

**Mapped:** 2026-05-26
**Files analyzed:** 7 (5 in em-proj + 2 cross-repo in gsd-sdk install)
**Analogs found (em-proj-side):** 5 / 5 (all have strong matches)
**Analogs found (cross-repo):** 0 / 2 — N/A (TypeScript/JS, no Python analogs; see RESEARCH Example 1)

## Note on Phase 6 Asymmetry

Phase 6 is structurally lopsided: the **production code change lives in gsd-sdk's
npm-installed copy** (TypeScript/Node, not in this repo). Em-proj's deliverables
are entirely **tests + a structural shape test** that audits the cross-repo
artifact. There is therefore:

- One set of analogs for em-proj-side files (rows 1–5 below) — closest existing
  Python files in this codebase.
- No code analog in this repo for the two cross-repo files (rows 6–7); they are
  patched per RESEARCH §"Pattern 1" / §"Code Examples Example 1". The structural
  test (file 4) is what enforces the gsd-sdk patch is present at runtime.

## File Classification

| New File | Repo | Role | Data Flow | Closest Analog | Match Quality |
|----------|------|------|-----------|----------------|---------------|
| 1. `tests/multiprocess/test_workstream_consumer_race.py` | em-proj | multi-process race test | request-response (CLI race) | `tests/multiprocess/test_claim_race.py` lines 100–179 (`test_two_sessions_race_claim_one_wins`) | **exact** — same fork+exec race shape; argv[0] changes from `em-proj` to `gsd-sdk` |
| 2. `tests/multiprocess/test_workstream_clobber_demo.py` | em-proj | side-by-side demo test (SC#3) | file-I/O + CLI race | (old path) `tests/multiprocess/test_kv_atomicity.py` (subprocess file writes) + (new path) `test_claim_race.py:100–179` | **role-match** — two distinct test cases combining a synthesized old-path baseline and the new-path race |
| 3. `tests/multiprocess/test_workstream_consumer_fallback.py` *(or fold into #1)* | em-proj | fallback / env-mismatch test | request-response | `tests/multiprocess/test_lock_stale.py:54–80` (env injection + subprocess.Popen with side-effect setup) | **role-match** — same env-perturbation harness shape |
| 4. `tests/structural/test_phase_06_shape.py` | em-proj | structural shape audit | file-I/O grep + AST | `tests/structural/test_phase_05_shape.py` (esp. `SKILL_PATH` resolution lines 56–59 + write-boundary audit lines 258–289) | **exact** — Phase 5 already audits a non-em-proj artifact (`SKILL.md`); Phase 6 audits a non-em-proj artifact (`workstream.js`) with the same xfail-on-missing pattern |
| 5. `tests/conftest.py` extension OR per-module skip | em-proj | fixture / precheck | request-response | `tests/conftest.py:53–83` (`redis_precheck` fixture) | **exact** — direct template; `shutil.which("gsd-sdk")` mirrors the existing `shutil.which(EM_PROJ_BIN)` check |
| 6. `sdk/dist/query/workstream.js` (npm install) | **gsd-sdk** | runtime consumer handler | request-response (JS→subprocess) | *no Python analog* | **N/A — cross-repo TypeScript; see RESEARCH Example 1 for full patch shape** |
| 7. `sdk/src/query/workstream.ts` (npm install) | **gsd-sdk** | TS source-of-truth (not runtime-loaded) | request-response | *no Python analog* | **N/A — cross-repo; mirror of (6) per Q-C symmetry decision** |

## Pattern Assignments

### File 1: `tests/multiprocess/test_workstream_consumer_race.py`

**Role:** multi-process race test
**Data flow:** request-response (two parallel CLI subprocess invocations)
**Closest analog:** `tests/multiprocess/test_claim_race.py` (last touched `ec0247a feat(04-03): add multi-process race tests for claim/release/check verbs`)

**COPIES VERBATIM** (from `test_claim_race.py`):

1. **Module docstring boilerplate** (lines 12–17 — design invariants):
   ```python
   """
   Design invariants (Phase 1 RESEARCH Pitfalls, carried forward):
     - subprocess.Popen NOT multiprocessing.Process (#6 -- fork+exec macOS safety)
     - .communicate(timeout=) NOT .wait() (#2 -- pipe-buffer deadlock)
     - EM_PROJ_REDIS_DB=15 in child env (#4 -- never writes to prod db=0)
   """
   ```

2. **Imports + helpers** (lines 40–94):
   ```python
   from __future__ import annotations

   import os
   import subprocess
   import json

   import pytest
   import redis

   from tests.conftest import EM_PROJ_BIN, TEST_DB, RaceResult  # noqa: F401

   def _project_hash() -> str:
       return os.path.abspath(os.getcwd()).replace("/", "-")

   def _claim_key(area: str) -> str:
       return f"state:claim:{_project_hash()}:{area}"

   def _redis_client() -> redis.Redis:
       return redis.Redis(host="127.0.0.1", port=6379, db=TEST_DB, decode_responses=True)
   ```
   (`_run` helper is also relevant if any non-race calls are needed.)

3. **Per-child env injection + tight launch loop + .communicate(timeout=) pattern** (lines 119–170):
   ```python
   child1_env = {
       **os.environ,
       "EM_PROJ_REDIS_DB": str(TEST_DB),
       "CLAUDE_CODE_SESSION_ID": "session-race-A",
   }
   child2_env = {
       **os.environ,
       "EM_PROJ_REDIS_DB": str(TEST_DB),
       "CLAUDE_CODE_SESSION_ID": "session-race-B",
   }

   # Tight launch loop -- no sleep between spawns; this is the race.
   proc1 = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=child1_env)
   proc2 = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=child2_env)

   try:
       stdout1, stderr1 = proc1.communicate(timeout=10.0)
   except subprocess.TimeoutExpired:
       proc1.kill()
       proc1.communicate()
       pytest.fail("Child 1 did not exit within 10s")
   # ...same for proc2...
   ```

4. **Exit-code distribution + Redis post-condition assertion** (lines 164–179):
   ```python
   exit_codes = sorted([proc1.returncode, proc2.returncode])
   assert exit_codes == [0, 3], (
       f"Expected exactly one winner (exit 0) and one loser (exit 3); got {exit_codes}\n"
       f"child1: rc={proc1.returncode} stderr={stderr1[:200]!r}\n"
       f"child2: rc={proc2.returncode} stderr={stderr2[:200]!r}"
   )
   # Post-race: the winner's claim key must still be alive in Redis (TTL > 0).
   client = _redis_client()
   key = _claim_key("workstream.active")
   ttl = client.ttl(key)
   assert ttl > 0, f"Expected claim key '{key}' TTL > 0; got TTL={ttl}"
   ```

**INVENTS NEW** (Phase-6-specific):

- `cmd = ["gsd-sdk", "query", "workstream.set", "<name>", "--raw", "--cwd", str(tmp_path)]`
  — argv[0] swap from `EM_PROJ_BIN` to literal `"gsd-sdk"`; mandatory
  `--raw --cwd "$CWD"` pair per workstreams.md slash-command convention (RESEARCH
  "Anti-Patterns to Avoid" §5).
- `tmp_path` fixture setup writing `.planning/workstreams/<name>/STATE.md` so
  gsd-sdk's `existsSync(wsDir)` check passes (RESEARCH Example 2 lines 279–281).
- JSON-body parsing: winner has `{"set": true}`, loser has `{"set": false,
  "error": "held_by_another", "holder": {...}}` (per gsd-sdk JS handler shape
  in RESEARCH Pattern 1 lines 233–250).
- Claim area key is `workstream.active` (Phase-6 area-key decision per RESEARCH
  Open Q A4).
- gsd-sdk PATH precheck via `shutil.which("gsd-sdk")` (module-level skip OR
  via extended `redis_precheck`; see file 5).

### File 2: `tests/multiprocess/test_workstream_clobber_demo.py`

**Role:** side-by-side demo test (SC#3 — clobber-vs-resolution)
**Data flow:** file-I/O (old path baseline) + CLI race (new path)
**Closest analog (old-path):** `tests/multiprocess/test_kv_atomicity.py` (subprocess-driven file/state writes)
**Closest analog (new-path):** same as file 1 — `tests/multiprocess/test_claim_race.py:100–179`

**COPIES VERBATIM** (from `test_claim_race.py` for the new-path test case):
Same patterns 1–4 as file 1 above (imports, helpers, race-loop, exit-code assertion).

**INVENTS NEW**:

1. **Old-path baseline test** (no analog — synthesized from RESEARCH Example 3 lines 411–447):
   ```python
   def test_old_path_direct_file_write_clobbers(tmp_path):
       """Baseline: two parallel writes to .planning/active-workstream clobber."""
       planning = tmp_path / ".planning"
       planning.mkdir()
       pointer = planning / "active-workstream"

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
       final = pointer.read_text().strip()
       assert final in ("workstream-A", "workstream-B")
       # No structured "you were displaced" signal exists — clobber.
   ```

2. **Two-test-case file layout** (test_old + test_new, both against same `tmp_path`)
   — distinct from file 1, which is single-purpose. The contrast IS the SC#3 demo.

**KEY DELTA from file 1:** File 2's purpose is the NARRATIVE pair (clobber-vs-resolution),
not a fresh race assertion. File 1 alone would suffice for CONSUMER-02; file 2 exists
exclusively for SC#3's "side-by-side" requirement.

### File 3: `tests/multiprocess/test_workstream_consumer_fallback.py` (OR fold into file 1)

**Role:** fallback / `em-proj`-missing-from-PATH test (Q-B decision)
**Data flow:** request-response with perturbed env
**Closest analog:** `tests/multiprocess/test_lock_stale.py:54–80` (env injection + Popen with environment perturbation)

**COPIES VERBATIM** (env-injection + Popen pattern):
```python
env = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB)}
proc = subprocess.Popen(
    [EM_PROJ_BIN, "state", "lock", "--ttl", "60", "--hold", "stale-foo", "--", "sleep", "30"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    env=env,  # Load-bearing: routes child to db=15, not prod db=0
)
```

**INVENTS NEW**:

- **PATH perturbation**: strip `em-proj`'s install dir from the test's `PATH` env so
  the gsd-sdk subprocess can't find it on PATH:
  ```python
  em_proj_dir = str(Path(shutil.which("em-proj")).parent)
  scrubbed_path = ":".join(p for p in os.environ["PATH"].split(":") if p != em_proj_dir)
  env = {**os.environ, "PATH": scrubbed_path, "EM_PROJ_REDIS_DB": str(TEST_DB)}
  ```
- **Assertion**: gsd-sdk subprocess exits 0 (degraded path proceeds) AND stderr contains
  the documented warning `"gsd-sdk: em-proj not on PATH; falling back to unguarded"`
  (per RESEARCH Pattern 1 lines 224–228).
- Single test case, no `tmp_path` race driver — sequential call is sufficient since
  this test only validates degraded-mode behavior, not concurrency.

**Recommendation (deferred to planner):** If the planner finds file 3 ends up
< 50 LOC of meaningful logic, **merge into file 1** as a third test function
`test_em_proj_missing_falls_through_with_warning`. Keep as a separate file if
file 1 grows past ~250 LOC and readability suffers.

### File 4: `tests/structural/test_phase_06_shape.py`

**Role:** structural shape test (file presence + content grep + SUMMARY coverage)
**Data flow:** file-I/O (read npm-installed JS/TS, read SUMMARY.md inventory)
**Closest analog:** `tests/structural/test_phase_05_shape.py` (last touched `e9a2592 test(05-05): Phase 5 structural shape assertions (12 tests)`)

**COPIES VERBATIM** (from `test_phase_05_shape.py`):

1. **Module docstring + imports + REPO_ROOT setup** (lines 39–52):
   ```python
   from __future__ import annotations

   import ast  # may be omitted if no AST checks needed for Phase 6
   from pathlib import Path

   import pytest

   REPO_ROOT = Path(__file__).resolve().parent.parent.parent
   PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "06-gsd-sdk-workstream-consumer"
   ```

2. **xfail-on-missing-cross-repo-artifact pattern** (lines 55–59 + lines 250–254
   — the SKILL_PATH primary+fallback resolution + `pytest.xfail` for absent file):
   ```python
   # Phase-6 adaptation: resolve gsd-sdk install path via shutil.which → walk to lib/
   import shutil

   def _resolve_workstream_artifact(filename: str) -> Path | None:
       """Resolve {filename} inside the gsd-sdk npm install.

       Strategy: shutil.which('gsd-sdk') → the bin shim points into
       lib/node_modules/get-shit-done-cc/bin/gsd-sdk.js. Walk up to the
       package root and descend into sdk/...
       """
       sdk_bin = shutil.which("gsd-sdk")
       if not sdk_bin:
           return None
       bin_path = Path(sdk_bin).resolve()
       # bin/gsd-sdk → lib/node_modules/get-shit-done-cc/bin/gsd-sdk.js (symlink)
       # Walk up to the package root.
       pkg_root = bin_path.parent.parent  # …/get-shit-done-cc/
       candidate = pkg_root / filename
       return candidate if candidate.exists() else None

   WORKSTREAM_JS = _resolve_workstream_artifact("sdk/dist/query/workstream.js")
   WORKSTREAM_TS = _resolve_workstream_artifact("sdk/src/query/workstream.ts")
   ```

3. **xfail body for missing artifact** (mirror lines 250–254):
   ```python
   def test_gsd_sdk_workstream_js_contains_em_proj_shellout() -> None:
       if WORKSTREAM_JS is None or not WORKSTREAM_JS.exists():
           pytest.xfail(
               "gsd-sdk not installed (or workstream.js not found via "
               "shutil.which('gsd-sdk')) — cannot audit consumer patch"
           )
       source = WORKSTREAM_JS.read_text()
       assert "'em-proj'" in source or '"em-proj"' in source, (
           "workstream.js does not reference 'em-proj' — Phase 6 consumer patch "
           "either never landed or was reverted by an `npm install -g` upgrade."
       )
   ```

4. **SUMMARY.md coverage check** (lines 340–361 — verbatim except for phase number):
   ```python
   def test_phase_06_summaries_present() -> None:
       if not PHASE_DIR.exists():
           pytest.skip(
               f"{PHASE_DIR.relative_to(REPO_ROOT)} not present — planning worktree "
               "may not be attached on this checkout"
           )
       plans = sorted(PHASE_DIR.glob("06-*-PLAN.md"))
       if not plans:
           pytest.skip(f"no 06-*-PLAN.md files yet under {PHASE_DIR.relative_to(REPO_ROOT)}")
       for plan in plans:
           summary = plan.parent / plan.name.replace("-PLAN.md", "-SUMMARY.md")
           assert summary.exists(), (
               f"Missing SUMMARY for {plan.name}: expected {summary.name} "
               f"in {PHASE_DIR.relative_to(REPO_ROOT)}"
           )
   ```

5. **Self-contained helpers convention** (line 36 — comment near top of file):
   ```python
   # Each structural file is self-contained (helper functions copied, NOT shared
   # via a common module) per Phase 1+2+3+4 precedent.
   ```

**INVENTS NEW** (Phase-6-specific):

- **Cross-repo file resolution** (`_resolve_workstream_artifact` helper above) —
  Phase 5 had a primary+fallback pair of well-known paths; Phase 6 must walk
  `shutil.which("gsd-sdk")` to handle the NodeJS install-path variability
  (`v22.13.1` is current but `nvm use <other>` would move it). This is a new
  pattern, not present in any prior structural test.
- **Stronger "ordering" assertion** (per RESEARCH Example 4 lines 504–513):
  ```python
  m = re.search(r"workstreamSet\s*=\s*async[\s\S]+?};", source)
  body = m.group(0)
  em_proj_idx = body.find("em-proj")
  set_active_idx = body.find("setActiveWorkstream")
  assert set_active_idx > em_proj_idx, (
      "em-proj shell-out must appear BEFORE setActiveWorkstream call"
  )
  ```
- **TS-side symmetry check** (per Q-C decision — both files patched):
  ```python
  def test_gsd_sdk_workstream_ts_contains_em_proj_shellout() -> None:
      if WORKSTREAM_TS is None or not WORKSTREAM_TS.exists():
          pytest.xfail("gsd-sdk TS source not available")
      assert "em-proj" in WORKSTREAM_TS.read_text(), (
          "workstream.ts (TS source-of-truth) lacks the em-proj shell-out — "
          "Q-C symmetry contract broken; .ts and .js should be in lockstep"
      )
  ```

### File 5: `tests/conftest.py` extension OR module-level skip

**Role:** session-scoped fixture / precheck
**Data flow:** request-response (probe `shutil.which`)
**Closest analog:** `tests/conftest.py:53–83` (`redis_precheck` fixture, last touched
`f7b814a feat(01-04): add multiproc_race pytest harness fixtures (TEST-01 substrate)`)

**COPIES VERBATIM** (the exact shape — the `redis_precheck` `shutil.which` block
lines 76–82 is the direct template):

```python
# Source: tests/conftest.py:76–82
if shutil.which(EM_PROJ_BIN) is None:
    pytest.skip(
        f"`{EM_PROJ_BIN}` not on PATH — "
        "run `uv tool install --editable .` from repo root",
        allow_module_level=True,
    )
```

**INVENTS NEW**:

Two viable shapes (planner picks one):

**Shape A — extend `redis_precheck` with a per-phase opt-in** (least invasive):
Add a NEW session-scoped fixture `gsd_sdk_precheck` (do NOT modify the existing
`redis_precheck` — Phase 6 is the only phase that needs `gsd-sdk`):
```python
GSD_SDK_BIN: str = "gsd-sdk"  # new module-level constant

@pytest.fixture(scope="session")
def gsd_sdk_precheck() -> None:
    """Skip session if `gsd-sdk` is not on PATH.

    Mirrors redis_precheck's shutil.which probe (lines 76–82).
    Phase 6 is the only phase that depends on gsd-sdk; this fixture is
    opt-in by per-test fixture request, not auto-applied like clean_db.
    """
    if shutil.which(GSD_SDK_BIN) is None:
        pytest.skip(
            f"`{GSD_SDK_BIN}` not on PATH — install via "
            "`npm install -g get-shit-done-cc` to enable Phase 6 consumer tests",
            allow_module_level=True,
        )
```
Phase 6 test files then add `gsd_sdk_precheck` to their function signatures
alongside `clean_db`.

**Shape B — module-level skip at top of each Phase 6 test file** (cleanest blast radius):
```python
# Top of test_workstream_consumer_race.py + test_workstream_clobber_demo.py +
# (if separate) test_workstream_consumer_fallback.py:
import shutil
import pytest

if shutil.which("gsd-sdk") is None:
    pytest.skip(
        "`gsd-sdk` not on PATH — install via `npm install -g get-shit-done-cc`",
        allow_module_level=True,
    )
```
No conftest.py change required. The skip fires at module import time, before
any test in the file runs.

**Recommendation (deferred to planner):** Shape B is structurally cleaner
(conftest.py stays at 160 LOC; Phase 6 dependency stays local to Phase 6 files).
Shape A is better if a future phase also needs `gsd-sdk`. Per RESEARCH Open
Question E, the recommendation matches Shape B ("Phase 6 is the only phase
that depends on `gsd-sdk`").

### File 6: `sdk/dist/query/workstream.js` (npm install)

**Role:** runtime consumer handler (JS, executed by `bin/gsd-sdk.js`)
**Data flow:** request-response (handler → spawnSync → JSON.parse → return)
**Closest analog:** **NONE in this repo** — this is the consumer patch in gsd-sdk's
TypeScript/JavaScript surface, not Python.

**Full patch shape:** RESEARCH §"Pattern 1" (lines 181–260) and §"Code Examples
Example 1" (line 404–405) cover this in detail. The diff inserts a
`spawnSync('em-proj', ['state', 'claim', '--ttl', '1800', '--json',
'workstream.active'])` block immediately BEFORE the existing
`setActiveWorkstream(projectDir, name)` call, with branches on:

- `claimResult.error?.code === 'ENOENT'` → silent fallback + stderr warning (Q-B).
- `claimResult.status === 3` → return `{set: false, error: 'held_by_another',
  holder}` (skip the file write).
- `claimResult.status === 1` → return `{set: false, error: 'claim_refused',
  detail}`.
- `claimResult.status === 0` → fall through to `setActiveWorkstream`.

**Cross-repo install path:** `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/sdk/dist/query/workstream.js`
(verified present: 16 KB).

**Hazard:** Any `npm install -g get-shit-done-cc@latest` reverts this file.
Phase 6's structural test (file 4) catches the regression.

**Planner note:** Do NOT search for a Python analog. The structural test (file 4)
asserts the patch is present at runtime. The plan's action section should reference
RESEARCH Example 1 directly for the patch body.

### File 7: `sdk/src/query/workstream.ts` (npm install)

**Role:** TypeScript source-of-truth (NOT runtime-loaded — only the `.js` is)
**Data flow:** same as file 6
**Closest analog:** **NONE in this repo** — same as file 6.

**Why edited at all:** Q-C decision per RESEARCH — keep `.ts` and `.js` in lockstep
so future upstream-PR or `npm install` audit is trivial. **Only file 6 (`.js`)
matters at runtime**; file 7 is documentation/symmetry.

**Cross-repo install path:** `/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/sdk/src/query/workstream.ts`
(verified present: 15.1 KB).

**Planner note:** Same as file 6 — reference RESEARCH Example 1 (the patch shape
is identical between `.ts` and `.js`; TS source is just slightly less minified).

## Shared Patterns

### Pattern A: subprocess.Popen env-injection contract for race tests

**Source:** `tests/conftest.py:114–142` (`multiproc_race` fixture inner `_run`)
**Apply to:** Files 1, 2, 3 (any test that spawns gsd-sdk OR em-proj children)

**The contract** (verbatim from conftest.py:126–141):
```python
# Inject EM_PROJ_REDIS_DB=15 so children target test DB (Pitfall #4 mitigation).
child_env = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB)}

# Phase 1: tight launch loop. NO awaiting between spawns — this is the race.
starts: list[float] = []
procs: list[subprocess.Popen] = []
for cmd in commands:
    starts.append(time.perf_counter())
    procs.append(
        subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=child_env,
        )
    )
```

**Phase 6 application:** When `gsd-sdk` is argv[0], the `EM_PROJ_REDIS_DB=15` env
var STILL belongs in the env dict — gsd-sdk's `spawnSync({env: process.env})`
will propagate it transparently to the grandchild `em-proj` process (RESEARCH
Pitfall #5).

### Pattern B: Per-child session-id injection for ownership races

**Source:** `tests/multiprocess/test_claim_race.py:119–129`
**Apply to:** Files 1, 2 (whenever two children must trigger an OWNERSHIP race,
not a refresh)

**The contract:**
```python
child1_env = {
    **os.environ,
    "EM_PROJ_REDIS_DB": str(TEST_DB),
    "CLAUDE_CODE_SESSION_ID": "session-race-A",
}
child2_env = {
    **os.environ,
    "EM_PROJ_REDIS_DB": str(TEST_DB),
    "CLAUDE_CODE_SESSION_ID": "session-race-B",
}
```

**Why it's load-bearing:** If both children share the same `CLAUDE_CODE_SESSION_ID`,
the second child triggers the Lua refresh path (exit 0, "refreshed"), masking the
race. Phase 6's race tests MUST inject distinct session IDs (RESEARCH Pitfall #4).

### Pattern C: `tmp_path` + `--cwd` for gsd-sdk subprocess isolation

**Source:** RESEARCH Example 2 lines 278–281 + Pitfall #3 lines 369–373 (no direct
em-proj-side code analog — this is gsd-sdk-specific)
**Apply to:** Files 1, 2 (and 3 if applicable)

**The contract:**
```python
# Set up .planning/workstreams/<name> so gsd-sdk's existsSync(wsDir) passes.
(tmp_path / ".planning" / "workstreams" / "ws-A").mkdir(parents=True)
(tmp_path / ".planning" / "workstreams" / "ws-A" / "STATE.md").write_text(
    "---\nworkstream: ws-A\n---\n"
)

cmd = ["gsd-sdk", "query", "workstream.set", "ws-A",
       "--raw", "--cwd", str(tmp_path)]
```

**Why both flags matter:**
- `--raw` → suppress TTY-pretty output, emit JSON envelope (anti-pattern §4).
- `--cwd "$CWD"` → gsd-sdk reads `.planning/workstreams/` under this dir
  (anti-pattern §5: `--raw --cwd "$CWD"` pair is mandatory per
  `workstreams.md`).

**Pitfall:** Pass `cwd=tmp_path` to `subprocess.Popen` as well, OR `os.chdir(tmp_path)`
before the race. Otherwise em-proj's `project_hash` (derived from Python's `os.getcwd()`
inheriting from Node's cwd, NOT gsd-sdk's `--cwd` flag) won't match what the test
asserts (RESEARCH Pitfall #3).

### Pattern D: xfail-on-missing-cross-repo-artifact (vs. skip)

**Source:** `tests/structural/test_phase_05_shape.py:250–254`
**Apply to:** File 4 (every test that audits the cross-repo `workstream.js`/`workstream.ts`)

**The contract:**
```python
if not SKILL_PATH.exists():
    pytest.xfail(
        "em-global-state SKILL.md not found at ~/.claude/skills/ or .claude/skills/ "
        "— skill may not be installed on this machine"
    )
```

**Why `xfail` not `skip`:** Phase 5 explicitly chose `xfail` so absence is
**visible** in CI output (T-5-05-02 mitigation in Phase 5 plan). For Phase 6,
the same rationale applies even more strongly — an `npm install -g` upgrade
silently reverting the patch would otherwise present as a green test suite
while the actual consumer is broken (RESEARCH Pitfall #6).

### Pattern E: Self-contained structural-test helpers (no shared utility module)

**Source:** `tests/structural/test_phase_05_shape.py:36–37` (comment) + lines 63–110
(helpers `_parse_or_skip`, `_find_assign`, `_find_funcdef`, `_find_imports`,
`_find_all_names`)
**Apply to:** File 4

**The contract:** Each Phase's structural test file copies whatever helpers it
needs verbatim from a sibling Phase test, rather than importing from a shared
`tests/structural/_helpers.py`. The rule was set in Plan 02-05 SUMMARY and held
across Phases 3, 4, 5.

For Phase 6, the relevant helpers reduce to:
- `_resolve_workstream_artifact` (NEW — invented in file 4)
- Optionally `_parse_or_skip` (if any AST checks are added — likely unnecessary
  for Phase 6 since `workstream.js` is JS, not Python)

### Pattern F: Test execution dispatcher convention

**Source:** Project `CLAUDE.md` lines 3–43 (`scripts/test.sh` dispatcher)
**Apply to:** Every plan-action that mentions running the new tests

**The contract:** Tests run via `bash scripts/test.sh multiprocess` or
`bash scripts/test.sh structural` — **NEVER** `uv run pytest` directly. With
`-k` for filter, `--tail N` for output truncation.

**Phase 6 invocations:**
```bash
# Per-task during plan execution:
bash scripts/test.sh multiprocess -k workstream  # files 1 + 2 + 3
bash scripts/test.sh structural -k test_phase_06_shape  # file 4
# Per-wave merge:
bash scripts/test.sh all
# Phase verification gate:
bash scripts/verify-phase.sh 06
```

## No Analog Found (For Cross-Repo Files Only)

| File | Repo | Why no analog |
|------|------|---------------|
| 6. `sdk/dist/query/workstream.js` | gsd-sdk (npm install) | TypeScript/JavaScript, not Python — em-proj has no `.js` files. The planner should reference RESEARCH §"Pattern 1" (lines 181–260) and §"Example 1" (line 404–405) directly for the patch body. The structural test (file 4) is the in-repo gate that catches reversion. |
| 7. `sdk/src/query/workstream.ts` | gsd-sdk (npm install) | Same as file 6. Mirror edit per Q-C symmetry decision. |

**All em-proj-side files (1–5) have strong analogs.**

## Analog Commit References (for planner traceability)

| Analog file | Last-touched commit | What that commit landed |
|-------------|--------------------|--------------------------|
| `tests/multiprocess/test_claim_race.py` | `ec0247a` | feat(04-03): add multi-process race tests for claim/release/check verbs |
| `tests/multiprocess/test_claim_list_race.py` | `adada00` | test(05-03): multi-process race tests for lock-list and claim-list verbs |
| `tests/multiprocess/test_lock_stale.py` | `67ef4e0` | test(03-06): SIGKILL stale-takeover proof (D-10 / ROADMAP SC#2) |
| `tests/conftest.py` | `f7b814a` | feat(01-04): add multiproc_race pytest harness fixtures (TEST-01 substrate) |
| `tests/structural/test_phase_05_shape.py` | `e9a2592` | test(05-05): Phase 5 structural shape assertions (12 tests) |

Plans can reference these hashes in their action sections (e.g., "Action: copy
the per-child env injection block from `ec0247a` lines 119–129 to the new race
test").

## Metadata

**Analog search scope:** `tests/multiprocess/`, `tests/structural/`, `tests/conftest.py`
**Files scanned:** 14 Python test files (7 multiprocess + 5 structural + 1 conftest + 1 phase researcher artifact)
**Cross-repo verifications:** gsd-sdk install at
`/Users/emonical/.nvm/versions/node/v22.13.1/lib/node_modules/get-shit-done-cc/`
(both `sdk/dist/query/workstream.js` and `sdk/src/query/workstream.ts` confirmed
present via `ls -la`).
**Pattern extraction date:** 2026-05-26
**Confidence:** HIGH — all em-proj-side analogs are exact or close role-match;
cross-repo files have no in-repo analog by definition but RESEARCH already
provides full patch shape.
