from __future__ import annotations
"""Phase 6 structural invariants — source-grep and filesystem assertions.

Encodes plan acceptance criteria as runtime assertions for Phase 6
(gsd-sdk Workstream Consumer):

  Phase 6 invariants:
    - CONSUMER-01: gsd-sdk's workstreamSet shells out to em-proj state
      claim before setActiveWorkstream() — asserted via source-text grep
      + ordering check on the npm-installed sdk/dist/query/workstream.js
    - Q-C lockstep: BOTH .js (runtime-loaded) and .ts (source-of-truth)
      contain the shellout
    - Q-D portable resolver: shutil.which('gsd-sdk') + walk to pkg root
      (no hardcoded nvm path)
    - Pattern D xfail-on-missing: cross-repo absence is visible in CI
    - SUMMARY coverage: every 06-NN-PLAN.md has a 06-NN-SUMMARY.md

No AST helpers are needed — Phase 6 audits JS/TS files via source grep
+ regex, not Python AST.

Each structural file is self-contained (no imports from sibling
test_phase_*_shape.py files) per Phase 1+2+3+4+5 precedent.
"""

import re
import shutil
from pathlib import Path

import pytest

# Self-contained — no imports from sibling test_phase_*_shape.py files.

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "06-gsd-sdk-workstream-consumer"


# ---------------------------------------------------------------------------
# Q-D portable resolver: shutil.which("gsd-sdk") + walk to pkg root
# ---------------------------------------------------------------------------


def _resolve_workstream_artifact(rel_path: str) -> Path | None:
    """Resolve <gsd-sdk-package-root>/<rel_path> portably.

    Strategy:
      1. shutil.which("gsd-sdk") → bin shim symlink
         (e.g. /Users/<u>/.nvm/versions/node/<v>/bin/gsd-sdk)
      2. Resolve symlinks: Path(...).resolve()
         (resolves to .../lib/node_modules/get-shit-done-cc/bin/gsd-sdk.js)
      3. Walk up two dirs: parent.parent → .../get-shit-done-cc/
      4. Descend into rel_path: pkg_root / rel_path

    Returns None if gsd-sdk is not on PATH OR the candidate does not exist.
    The CALLER is responsible for pytest.xfail when None is returned.
    """
    sdk_bin = shutil.which("gsd-sdk")
    if not sdk_bin:
        return None
    bin_path = Path(sdk_bin).resolve()
    pkg_root = bin_path.parent.parent
    candidate = pkg_root / rel_path
    return candidate if candidate.exists() else None


WORKSTREAM_JS = _resolve_workstream_artifact("sdk/dist/query/workstream.js")
WORKSTREAM_TS = _resolve_workstream_artifact("sdk/src/query/workstream.ts")


# ---------------------------------------------------------------------------
# Test A — CONSUMER-01: workstream.js contains em-proj shellout
# ---------------------------------------------------------------------------


def test_gsd_sdk_workstream_js_contains_em_proj_shellout() -> None:
    """The npm-installed workstream.js must contain the em-proj shell-out (CONSUMER-01).

    Phase 6's claim gate inserts spawnSync('em-proj', ['state', 'claim', ...])
    before setActiveWorkstream(...) in workstreamSet. This test asserts the
    literal 'em-proj' is present in the runtime-loaded .js.

    Uses xfail (NOT skip) when the cross-repo artifact is missing so that an
    npm-upgrade reversion is VISIBLE in CI output (Pattern D / Pitfall #6).
    """
    if WORKSTREAM_JS is None:
        pytest.xfail(
            "gsd-sdk not installed (or workstream.js not found via "
            "shutil.which('gsd-sdk') + walk to "
            "lib/node_modules/get-shit-done-cc/sdk/dist/query/) — "
            "cannot audit consumer patch. If gsd-sdk IS installed, the "
            "patch may have been reverted by `npm install -g "
            "get-shit-done-cc` upgrade — re-apply Plan 06-01."
        )
    source = WORKSTREAM_JS.read_text()
    assert "'em-proj'" in source or '"em-proj"' in source, (
        f"{WORKSTREAM_JS} does not reference 'em-proj' — Phase 6 consumer "
        "patch either never landed or was reverted by an `npm install -g` "
        "upgrade. Re-apply Plan 06-01 to restore."
    )


# ---------------------------------------------------------------------------
# Test B — ordering invariant: em-proj shellout precedes setActiveWorkstream
# ---------------------------------------------------------------------------


def test_gsd_sdk_workstream_js_shellout_precedes_set_active() -> None:
    """Ordering invariant: the em-proj claim gate MUST appear before the LAST
    setActiveWorkstream call within workstreamSet (the success-path write).

    Context: workstreamSet has multiple branches. The clear/reset branch may
    call setActiveWorkstream(projectDir, '') to clear the active workstream —
    this early path does NOT need a claim gate. The plan's invariant is that
    the SUCCESS path (setting a new workstream) is gated by the em-proj claim
    BEFORE setActiveWorkstream is called with the new name.

    Therefore we assert: em-proj appears BEFORE the LAST occurrence of
    setActiveWorkstream in the region starting from the workstreamSet declaration.
    The last call is the success-path write.
    """
    if WORKSTREAM_JS is None:
        pytest.xfail("see test_gsd_sdk_workstream_js_contains_em_proj_shellout")
    source = WORKSTREAM_JS.read_text()

    # Find the start of the workstreamSet handler declaration.
    m = re.search(r"workstreamSet\s*=\s*async", source)
    assert m, (
        "Could not locate 'workstreamSet = async' declaration in workstream.js. "
        "File format may have changed in an upstream release."
    )
    handler_start = m.start()

    # Work with the source text from handler_start onward.
    handler_tail = source[handler_start:]

    # Find the em-proj claim gate (first occurrence after handler start).
    em_proj_idx = handler_tail.find("'em-proj'")
    if em_proj_idx == -1:
        em_proj_idx = handler_tail.find('"em-proj"')

    assert em_proj_idx > 0, (
        f"em-proj shellout not found after workstreamSet declaration in {WORKSTREAM_JS}"
    )

    # Find the LAST setActiveWorkstream( call — this is the success-path write.
    last_set_active_idx = handler_tail.rfind("setActiveWorkstream(")
    assert last_set_active_idx > 0, (
        f"setActiveWorkstream( not found after workstreamSet declaration in {WORKSTREAM_JS}"
    )

    assert last_set_active_idx > em_proj_idx, (
        "em-proj claim gate must appear BEFORE the last setActiveWorkstream call "
        "(gate-precedes-write invariant for the success path). "
        f"Found em-proj at offset {em_proj_idx} from handler start, "
        f"last setActiveWorkstream( at offset {last_set_active_idx}."
    )


# ---------------------------------------------------------------------------
# Test C — held_by_another branch present
# ---------------------------------------------------------------------------


def test_gsd_sdk_workstream_js_contains_held_by_another_branch() -> None:
    """Assert the status-3 branch is wired (loser receives structured
    envelope, not silent fall-through).
    """
    if WORKSTREAM_JS is None:
        pytest.xfail("see test_gsd_sdk_workstream_js_contains_em_proj_shellout")
    source = WORKSTREAM_JS.read_text()
    assert "held_by_another" in source, (
        "workstream.js missing 'held_by_another' branch — the loser of a "
        "race would not receive the structured envelope CONSUMER-02 demands."
    )


# ---------------------------------------------------------------------------
# Test D — ENOENT fallback branch present
# ---------------------------------------------------------------------------


def test_gsd_sdk_workstream_js_contains_enoent_fallback() -> None:
    """Assert the Q-B silent fallback branch is wired (em-proj missing
    from PATH → stderr warning + legacy write, not hard error).
    """
    if WORKSTREAM_JS is None:
        pytest.xfail("see test_gsd_sdk_workstream_js_contains_em_proj_shellout")
    source = WORKSTREAM_JS.read_text()
    assert "ENOENT" in source, (
        "workstream.js missing 'ENOENT' branch — em-proj-missing-from-PATH "
        "case will not fall through to the legacy unguarded write (Q-B)."
    )


# ---------------------------------------------------------------------------
# Test E — Q-C lockstep: TS source-of-truth also contains the shellout
# ---------------------------------------------------------------------------


def test_gsd_sdk_workstream_ts_contains_em_proj_shellout() -> None:
    """Q-C lockstep: the TS source-of-truth must also contain the shellout,
    even though only the .js is runtime-loaded. Symmetry keeps future
    upstream-PR work trivial and makes the patch visible to readers of
    the .ts source.
    """
    if WORKSTREAM_TS is None:
        pytest.xfail(
            "gsd-sdk TS source not found via "
            "lib/node_modules/get-shit-done-cc/sdk/src/query/workstream.ts — "
            "Q-C lockstep cannot be verified"
        )
    ts_source = WORKSTREAM_TS.read_text()
    assert "em-proj" in ts_source, (
        f"{WORKSTREAM_TS} (TS source-of-truth) lacks 'em-proj' shellout — "
        "Q-C symmetry contract broken; .ts and .js must be in lockstep."
    )


# ---------------------------------------------------------------------------
# Test F — SUMMARY coverage: every 06-NN-PLAN.md has a 06-NN-SUMMARY.md
# ---------------------------------------------------------------------------


def test_phase_06_summaries_present() -> None:
    """SUMMARY coverage check, identical pattern to Phase 4/5.

    Uses skip (not xfail) for PHASE_DIR absence because PHASE_DIR is
    em-proj-internal, not cross-repo; absence indicates "this checkout
    has no planning worktree attached" which is a legitimate developer
    setup (not a regression).
    """
    if not PHASE_DIR.exists():
        pytest.skip(
            f"{PHASE_DIR.relative_to(REPO_ROOT)} not present — planning "
            "worktree may not be attached on this checkout"
        )
    plans = sorted(PHASE_DIR.glob("06-*-PLAN.md"))
    if not plans:
        pytest.skip(
            f"no 06-*-PLAN.md files yet under "
            f"{PHASE_DIR.relative_to(REPO_ROOT)}"
        )
    for plan in plans:
        summary = plan.parent / plan.name.replace("-PLAN.md", "-SUMMARY.md")
        assert summary.exists(), (
            f"Missing SUMMARY for {plan.name}: expected {summary.name} "
            f"in {PHASE_DIR.relative_to(REPO_ROOT)}"
        )
