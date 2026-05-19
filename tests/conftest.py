"""Pytest fixtures for the em-proj multi-process test harness.

Lands the substrate (TEST-01 + TEST-02) every subsequent phase races against:
  - redis_precheck (session)  : skip session if Redis or em-proj not available
  - clean_db (function)        : FLUSHDB on db=15 before/after each test (D-11, D-16)
  - multiproc_race (function) : parallel-launch N em-proj children, join, return RaceResult[]

Design constraints baked in:
  - subprocess.Popen + fork+exec (NOT multiprocessing.Process — RESEARCH Pitfall #6 macOS)
  - .communicate(timeout=) NOT .wait() (RESEARCH Pitfall #2 pipe-buffer deadlock)
  - tight launch loop (no awaiting between spawns — D-14, would silently defeat lock tests)
  - EM_PROJ_REDIS_DB=15 in subprocess env so children connect to test DB (Pitfall #4 mitigation)

DO NOT refactor subprocess.Popen to multiprocessing.Process — see RESEARCH Pitfall #6 and
threat T-01-04-05. multiprocessing.Process with fork start-method crashes intermittently
on macOS with OBJC_DISABLE_INITIALIZE_FORK_SAFETY errors; subprocess.Popen does fork+exec
which is always safe because exec replaces the entire process image.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass

import pytest
import redis

TEST_DB: int = 15
EM_PROJ_BIN: str = "em-proj"  # resolved via shutil.which in redis_precheck
DEFAULT_RACE_TIMEOUT: float = 10.0


@dataclass(frozen=True)
class RaceResult:
    """One child-process outcome. Three assertion surfaces per D-15:
      - returncode  : exit code (e.g. [0, 3] for one-wins one-held)
      - stdout      : captured stdout (e.g. JSON output or marker tokens)
      - stderr      : captured stderr (e.g. error messages)
      - duration_ms : wall-time from Popen() to communicate() return

    Post-race Redis state (the fourth surface per D-15) is asserted by reading
    the `clean_db` fixture directly, NOT via this dataclass.
    """

    returncode: int
    stdout: str
    stderr: str
    duration_ms: float


@pytest.fixture(scope="session")
def redis_precheck() -> redis.Redis:
    """Skip the test session if Redis is down or `em-proj` is not on PATH.

    Cheap session-scoped probe — runs once per pytest invocation. Returns a
    redis.Redis(db=TEST_DB) client that the clean_db fixture flushes.
    """
    client = redis.Redis(
        host="127.0.0.1",
        port=6379,
        db=TEST_DB,
        socket_connect_timeout=1.0,
        decode_responses=True,
    )
    try:
        client.ping()
    except (redis.ConnectionError, redis.TimeoutError):
        pytest.skip(
            "Redis not reachable at 127.0.0.1:6379 — "
            "run `brew services start redis` to enable multi-process tests",
            allow_module_level=True,
        )

    if shutil.which(EM_PROJ_BIN) is None:
        pytest.skip(
            f"`{EM_PROJ_BIN}` not on PATH — "
            "run `uv tool install --editable .` from repo root",
            allow_module_level=True,
        )

    return client


@pytest.fixture
def clean_db(redis_precheck: redis.Redis) -> redis.Redis:
    """FLUSHDB on db=15 before AND after each test. Function-scoped: full isolation."""
    redis_precheck.flushdb()
    yield redis_precheck
    redis_precheck.flushdb()  # paranoia cleanup; helps when a test mid-fails


@pytest.fixture
def multiproc_race(clean_db: redis.Redis):
    """Spawn N subprocess.Popen children in parallel, join all, return RaceResult per launch order.

    Usage:
        def test_two_em_projs_race(multiproc_race):
            results = multiproc_race([
                [EM_PROJ_BIN, "--version"],
                [EM_PROJ_BIN, "--version"],
            ])
            assert all(r.returncode == 0 for r in results)

    Design invariants (D-14, RESEARCH Pattern 3, RESEARCH Pitfalls 2/3/6):
      1. Tight launch loop: every Popen() returns immediately (fork+exec);
         all N children are running before any .communicate() call.
      2. .communicate(timeout=) NOT .wait() — avoids 64KB pipe-buffer deadlock.
      3. EM_PROJ_REDIS_DB=15 in child env so they target test DB (NOT prod db=0).
      4. subprocess.Popen NOT multiprocessing.Process — fork+exec safe on macOS.
    """

    def _run(
        commands: list[list[str]],
        timeout: float = DEFAULT_RACE_TIMEOUT,
    ) -> list[RaceResult]:
        assert isinstance(commands, list) and all(isinstance(c, list) for c in commands), (
            "multiproc_race: pass a list of argv-lists, e.g. [[EM_PROJ_BIN, '--version']]"
        )
        assert all(all(isinstance(arg, str) for arg in c) for c in commands), (
            "multiproc_race: argv elements must be str (shell-injection safety — "
            "subprocess.Popen with list[str] does NOT invoke a shell)"
        )

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
                    text=True,  # decode using locale; pairs with decode_responses
                    env=child_env,
                )
            )

        # Phase 2: join all via .communicate(timeout=) — drains pipes + waits.
        results: list[RaceResult] = []
        for start, proc in zip(starts, procs):
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()  # reap zombie
                raise AssertionError(
                    f"multiproc_race: child {proc.args!r} did not exit within {timeout}s; killed"
                )
            duration_ms = (time.perf_counter() - start) * 1000.0
            results.append(RaceResult(proc.returncode, stdout, stderr, duration_ms))

        return results

    return _run
