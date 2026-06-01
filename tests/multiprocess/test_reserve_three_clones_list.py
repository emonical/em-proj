"""SC#3 demo: three sibling clones of the same upstream share a
reservation namespace (Plan 07-02).

This file IS the Phase 7 SC#3 human-runnable demo. Run via:

    bash scripts/test.sh multiprocess -k three_clones

Three fake clones are set up under tmp_path, each with an IDENTICAL
fake .git/config containing the same origin URL. One clone takes a
reservation; the other two then invoke `em-proj state reserve-list`
and BOTH see the reservation. The shared visibility IS the proof of
ROADMAP Phase 7 Success Criterion #3.

Design invariants:
  - subprocess.run with per-child cwd= (Pitfall #6 from 07-RESEARCH)
  - EM_PROJ_REDIS_DB=15 in every child env (Pitfall #5)
  - Distinct CLAUDE_CODE_SESSION_ID per child to avoid Lua-refresh
    masking ownership effects (Pitfall #4 carry)
  - --workstream <name> passed explicitly to every reserve invocation
    (Pitfall #5 — non-TTY subprocess would otherwise exit 1)

Self-contained per project convention: _make_fake_clone is duplicated
from test_reserve_race.py rather than imported from a shared module.

Reference: ROADMAP Phase 7 Success Criteria #3.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import redis as redis_module

from tests.conftest import EM_PROJ_BIN, TEST_DB


# ---------------------------------------------------------------------------
# Helper — fake git clone dir (duplicated from test_reserve_race.py)
# ---------------------------------------------------------------------------

def _make_fake_clone(parent: Path, name: str, origin_url: str) -> Path:
    """Create a minimal fake git clone directory under ``parent/name``.

    Uses ``git init`` so the objects/ directory is present and
    ``git remote get-url origin`` works correctly.  Appends the
    ``[remote "origin"]`` block to the generated config so the upstream
    identity can be resolved by ``resolve_upstream_identity``.

    Note: plain ``.git/config + HEAD`` (without git init) is insufficient —
    git requires an ``objects/`` directory (Phase 7 lesson, 07-01-SUMMARY).
    """
    clone_dir = parent / name
    clone_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", str(clone_dir)],
        check=True,
        capture_output=True,
    )
    config_append = (
        '\n[remote "origin"]\n'
        f'\turl = {origin_url}\n'
        '\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
    )
    config_path = clone_dir / ".git" / "config"
    with config_path.open("a") as fh:
        fh.write(config_append)
    return clone_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_ORIGIN = "git@github.com:emonical/roleplay-engine.git"
_CANONICAL_UPSTREAM = "github.com:emonical/roleplay-engine"


def test_three_clones_see_shared_reservation(tmp_path, clean_db) -> None:
    """SC#3 demo: three clones of the same upstream share a reservation
    namespace.

    clone-a takes a reservation; clone-b and clone-c both invoke
    reserve-list and see IDENTICAL results. That identity of results
    IS the proof that a shared upstream-anchored namespace exists.

    ROADMAP Phase 7 Success Criterion #3.
    """
    clone_a = _make_fake_clone(tmp_path, "clone-a", _ORIGIN)
    clone_b = _make_fake_clone(tmp_path, "clone-b", _ORIGIN)
    clone_c = _make_fake_clone(tmp_path, "clone-c", _ORIGIN)

    def _env(session_id: str) -> dict:
        return {
            **os.environ,
            "EM_PROJ_REDIS_DB": str(TEST_DB),
            "CLAUDE_CODE_SESSION_ID": session_id,
        }

    # Step 1: clone-a takes a reservation.
    reserve_result = subprocess.run(
        [
            EM_PROJ_BIN, "state", "reserve",
            "--workstream", "ws-a",
            "--reason", "SC#3 demo",
            "--json",
            "migrations.v200",
        ],
        capture_output=True,
        text=True,
        env=_env("three-clones-A"),
        cwd=str(clone_a),
        timeout=10.0,
    )
    assert reserve_result.returncode == 0, (
        f"clone-a reserve should succeed; got rc={reserve_result.returncode}, "
        f"stderr={reserve_result.stderr[:200]!r}"
    )
    reserve_data = json.loads(reserve_result.stdout)
    assert reserve_data["data"]["area"] == "migrations.v200"
    assert reserve_data["data"]["workstream"] == "ws-a"

    # Step 2: clone-b reads the reservation via reserve-list.
    def _list(clone_dir: Path, session_id: str) -> list[dict]:
        r = subprocess.run(
            [EM_PROJ_BIN, "state", "reserve-list", "--json"],
            capture_output=True,
            text=True,
            env=_env(session_id),
            cwd=str(clone_dir),
            timeout=10.0,
        )
        assert r.returncode == 0, (
            f"reserve-list from {clone_dir.name} failed: rc={r.returncode}, "
            f"stderr={r.stderr[:200]!r}"
        )
        return json.loads(r.stdout)["data"]["items"]

    items_b = _list(clone_b, "three-clones-B")
    items_c = _list(clone_c, "three-clones-C")

    # Each sibling sees exactly one reservation.
    assert len(items_b) == 1, f"clone-b expected 1 item, got: {items_b}"
    assert len(items_c) == 1, f"clone-c expected 1 item, got: {items_c}"

    # The reservation content is correct.
    assert items_b[0]["area"] == "migrations.v200"
    assert items_b[0]["upstream_identity"] == _CANONICAL_UPSTREAM
    assert items_b[0]["workstream"] == "ws-a"

    # Sibling clones see IDENTICAL content — THE proof of SC#3.
    assert items_b == items_c, (
        "Sibling clones must see identical reserve-list contents "
        "(shared upstream_identity namespace). "
        f"Got items_b={items_b!r}, items_c={items_c!r}"
    )


def test_three_clones_distinct_areas_grouped_correctly(tmp_path, clean_db) -> None:
    """Three clones each reserve distinct areas; reserve-list --category
    filters them correctly.

    Tests RESERVE-04 (category filter) under realistic multi-clone setup:
      - clone-a reserves "migrations.v200"
      - clone-b reserves "db.5432"
      - clone-c reserves "migrations.v201"

    Assertions:
      - reserve-list --json → 3 items
      - reserve-list --category migrations --json → 2 items
      - reserve-list --category db --json → 1 item
    """
    clone_a = _make_fake_clone(tmp_path, "clone-a", _ORIGIN)
    clone_b = _make_fake_clone(tmp_path, "clone-b", _ORIGIN)
    clone_c = _make_fake_clone(tmp_path, "clone-c", _ORIGIN)

    def _env(session_id: str) -> dict:
        return {
            **os.environ,
            "EM_PROJ_REDIS_DB": str(TEST_DB),
            "CLAUDE_CODE_SESSION_ID": session_id,
        }

    def _reserve(clone_dir: Path, session_id: str, area: str, workstream: str) -> None:
        r = subprocess.run(
            [
                EM_PROJ_BIN, "state", "reserve",
                "--workstream", workstream,
                "--json",
                area,
            ],
            capture_output=True,
            text=True,
            env=_env(session_id),
            cwd=str(clone_dir),
            timeout=10.0,
        )
        assert r.returncode == 0, (
            f"reserve {area!r} from {clone_dir.name} failed: "
            f"rc={r.returncode}, stderr={r.stderr[:200]!r}"
        )

    # Each clone takes a distinct reservation.
    _reserve(clone_a, "three-clones-cat-A", "migrations.v200", "ws-a")
    _reserve(clone_b, "three-clones-cat-B", "db.5432", "ws-b")
    _reserve(clone_c, "three-clones-cat-C", "migrations.v201", "ws-c")

    def _list(category: str | None, clone_dir: Path, session_id: str) -> list[dict]:
        cmd = [EM_PROJ_BIN, "state", "reserve-list", "--json"]
        if category is not None:
            cmd += ["--category", category]
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=_env(session_id),
            cwd=str(clone_dir),
            timeout=10.0,
        )
        assert r.returncode == 0, (
            f"reserve-list category={category!r} failed: "
            f"rc={r.returncode}, stderr={r.stderr[:200]!r}"
        )
        return json.loads(r.stdout)["data"]["items"]

    # From clone-a's perspective (cwd), list all reservations.
    all_items = _list(None, clone_a, "three-clones-cat-A")
    assert len(all_items) == 3, (
        f"Expected 3 total reservations, got {len(all_items)}: {all_items!r}"
    )
    assert {item["area"] for item in all_items} == {
        "migrations.v200", "db.5432", "migrations.v201"
    }

    # Category filter: migrations → 2 items.
    migrations_items = _list("migrations", clone_a, "three-clones-cat-A")
    assert len(migrations_items) == 2, (
        f"Expected 2 migrations items, got {len(migrations_items)}: {migrations_items!r}"
    )
    assert all(item["area"].startswith("migrations.") for item in migrations_items)

    # Category filter: db → 1 item.
    db_items = _list("db", clone_a, "three-clones-cat-A")
    assert len(db_items) == 1, (
        f"Expected 1 db item, got {len(db_items)}: {db_items!r}"
    )
    assert db_items[0]["area"] == "db.5432"

    # Cross-clone consistency: clone-c sees the same category results.
    migrations_from_c = _list("migrations", clone_c, "three-clones-cat-C")
    assert len(migrations_from_c) == 2
    assert {item["area"] for item in migrations_from_c} == {
        item["area"] for item in migrations_items
    }
