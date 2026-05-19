"""Harness self-tests — proves TEST-01 and TEST-02 are satisfied.

These tests race `em-proj --version` as the canonical Phase 1 "real binary" verb (D-06).
They exercise THREE of the four D-15 assertion surfaces (exit codes, stdout markers,
duration timing). The fourth surface (post-race Redis state) is exercised by
test_redis_state_isolation_per_test_{setup,verify}.

The harness fixture itself is in tests/conftest.py; pytest auto-discovers it.
"""
from __future__ import annotations

import time

from conftest import EM_PROJ_BIN, RaceResult, TEST_DB


def test_harness_runs_em_proj_at_cli_boundary(multiproc_race) -> None:
    """TEST-01: harness spawns N fork+exec children invoking em-proj --version.

    Asserts on D-15 surfaces: exit codes + stdout markers + RaceResult shape.
    Per D-06, --version is the canonical "real binary" verb for Phase 1 races.
    """
    results = multiproc_race([
        [EM_PROJ_BIN, "--version"],
        [EM_PROJ_BIN, "--version"],
        [EM_PROJ_BIN, "--version"],
    ])

    # Exit code surface (D-15 #1)
    assert len(results) == 3, f"expected 3 results, got {len(results)}"
    assert all(r.returncode == 0 for r in results), (
        f"expected all exit 0; got returncodes {[r.returncode for r in results]}, "
        f"stderrs {[r.stderr for r in results]}"
    )

    # Stdout marker surface (D-15 #2)
    for r in results:
        assert "em-proj 0.1.0" in r.stdout, (
            f"expected 'em-proj 0.1.0' in stdout, got {r.stdout!r}"
        )

    # Shape surface — RaceResult dataclass exposes returncode + stdout + stderr + duration_ms
    for r in results:
        assert isinstance(r, RaceResult), f"expected RaceResult, got {type(r).__name__}"
        assert r.duration_ms > 0, f"expected positive duration, got {r.duration_ms}"
        # No errors on stderr from --version (typer.echo writes to stdout)
        assert r.stderr == "" or "warning" in r.stderr.lower(), (
            f"unexpected stderr from --version: {r.stderr!r}"
        )


def test_race_launches_in_parallel_not_sequence(multiproc_race) -> None:
    """TEST-02: harness launch loop is parallel, not sequential.

    If three em-proj --version invocations took ~150ms each sequentially
    they'd total ~450ms. In parallel they should complete in roughly
    max(child_durations), well under 600ms even with cold-start overhead.

    Threshold tuning: initial 600ms ceiling per RESEARCH Open Question #2.
    If this flakes on first run, measure single-call cold-start wall time
    (e.g. `time em-proj --version`) and set the ceiling to 2x that.
    """
    t0 = time.perf_counter()
    results = multiproc_race([
        [EM_PROJ_BIN, "--version"],
        [EM_PROJ_BIN, "--version"],
        [EM_PROJ_BIN, "--version"],
    ])
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert all(r.returncode == 0 for r in results), (
        f"expected all exit 0; got {[r.returncode for r in results]}"
    )
    assert elapsed_ms < 600, (
        f"harness wall-time {elapsed_ms:.0f}ms exceeds 600ms threshold — "
        f"looks sequential, not parallel. Individual durations: "
        f"{[r.duration_ms for r in results]}. "
        f"If this is genuine cold-start cost on slow hardware (RESEARCH Open Question #2), "
        f"measure single-call time and bump the threshold to 2x that value."
    )


def test_redis_state_isolation_per_test_setup(clean_db) -> None:
    """First half of isolation check: write a sentinel to db=15."""
    clean_db.set("isolation_sentinel", "from_setup_test")
    assert clean_db.get("isolation_sentinel") == "from_setup_test"


def test_redis_state_isolation_per_test_verify(clean_db) -> None:
    """Second half: the sentinel from _setup must be gone (FLUSHDB ran between tests)."""
    assert clean_db.get("isolation_sentinel") is None, (
        "clean_db fixture did NOT FLUSHDB between tests — D-11/D-16 contract broken; "
        "tests can leak state into each other, which would silently break Phase 3+ lock tests"
    )


def test_db_15_not_db_0_safety_net(clean_db) -> None:
    """Paranoia: clean_db must connect to db=15, NOT db=0 (production default).

    If this asserts wrong, FLUSHDB will wipe the developer's real Redis state.
    """
    assert clean_db.connection_pool.connection_kwargs["db"] == TEST_DB == 15, (
        f"clean_db is on db={clean_db.connection_pool.connection_kwargs['db']}, "
        f"expected TEST_DB={TEST_DB}=15"
    )
