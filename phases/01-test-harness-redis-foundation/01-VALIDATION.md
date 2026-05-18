---
phase: 1
slug: test-harness-redis-foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-18
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest >=8.0,<10.0 (current system: 9.0.3) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` (Wave 0 creates) |
| **Quick run command** | `uv run pytest tests/unit -x` |
| **Full suite command** | `uv run pytest -ra` |
| **Estimated runtime** | ~5–15 seconds (unit only: ~1s; multiprocess included: 5–15s) |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/unit -x` (~1s, no Redis dependency)
- **After every plan wave:** Run `uv run pytest -ra` (full suite incl. multiprocess; ~5–15s)
- **Before `/gsd-verify-work`:** Full suite must be green + manual `redis-cli CONFIG GET *` verification dump + `em-proj --version` runs on a fresh shell
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

> Populated by the planner during plan generation. Each plan task maps to one or more REQ-IDs and gets an automated command (or Wave 0 file gap reference). The matrix below is the REQ-ID → behavior → test map extracted from RESEARCH.md §Validation Architecture; the planner expands it into task-level rows.

| Req ID | Behavior | Test Type | Automated Command | File Exists |
|--------|----------|-----------|-------------------|-------------|
| REDIS-01 | brew-managed Redis with `appendonly yes`, `appendfsync everysec`, `save 900 1`; AOF visible | smoke (shell + redis-cli) | `bash scripts/verify-redis-config.sh` | ❌ W0 |
| CLI-01 | `em-proj` installable via `uv tool install --editable .` and on PATH | smoke | `command -v em-proj && em-proj --version` | ❌ W0 |
| CLI-02 | typer dispatch scaffold — `--version` and `--help` work, ready for `add_typer` | unit | `uv run pytest tests/unit/test_cli.py::test_version tests/unit/test_cli.py::test_help -x` | ❌ W0 |
| TEST-01 | Multi-process harness can spawn N fork+exec children racing `em-proj` at the CLI boundary | integration | `uv run pytest tests/multiprocess/test_harness_self.py -x` | ❌ W0 |
| TEST-02 | Harness lands and self-tests pass BEFORE any locking/claim code | ordering (TDD enforcement) | `uv run pytest tests/multiprocess/test_harness_self.py::test_race_launches_in_parallel_not_sequence -x` | ❌ W0 |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `pyproject.toml` — entire file; defines `[tool.pytest.ini_options]` block + `[project]` + `[project.scripts]` + Python pin (`>=3.12`) + runtime deps (`typer`, `redis`) + test deps (`pytest`)
- [ ] `src/em_proj/__init__.py` — empty package marker
- [ ] `src/em_proj/__main__.py` — `python -m em_proj` entrypoint (delegates to `cli.app`)
- [ ] `src/em_proj/cli.py` — typer `app = Typer()`; `--version` callback (`is_eager=True`); ready for `app.add_typer(state_app, name="state")` in Phase 2
- [ ] `src/em_proj/redis_client.py` (or `backend/redis.py`) — lazy `get_client()` + `ConnectionError` → actionable-message wrapper
- [ ] `tests/conftest.py` — `redis_precheck`, `clean_db` (db=15 + FLUSHDB), `multiproc_race` fixtures
- [ ] `tests/unit/test_cli.py` — `--version` exits 0 + version string; `--help` exits 0 + non-empty help text
- [ ] `tests/unit/test_redis_client.py` — lazy-init test (no connect on import); error-translation test (stubs `ping()` raising `ConnectionError`; asserts one-line stderr)
- [ ] `tests/multiprocess/test_harness_self.py` — TEST-01 (children race em-proj at CLI boundary) + TEST-02 (race launches in parallel, not sequence — wall-time threshold ~< 600ms tuned on first run per RESEARCH Open Question #2)
- [ ] `scripts/verify-redis-config.sh` — bash script asserts `appendonly yes`, `appendfsync everysec`, `save 900 1`, and AOF presence at `/opt/homebrew/var/db/redis/appendonly.aof` (or split AOF glob — RESEARCH Open Question #1; resolve on first run)
- [ ] Framework install: `uv sync` + `uv tool install --editable .` — bootstrap commands; document in `README.md`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| brew config edit + restart actually persists across `brew services restart redis` | REDIS-01 | Touching the user's `~/.homebrew/.../redis.conf` is a destructive op; should be confirmed by hand on first install rather than silently mutated by a test | (1) `cat /opt/homebrew/etc/redis.conf \| grep -E "^(appendonly\|appendfsync\|save)"` (2) confirm matches REDIS-01 spec (3) `brew services restart redis` (4) re-run `bash scripts/verify-redis-config.sh` |
| `em-proj` on PATH in a fresh shell after `uv tool install --editable .` | CLI-01 | `uv tool install` mutates user-global tool state (`~/.local/share/uv/tools/` or similar); verifying in a fresh shell catches PATH-shadowing issues that the install-time test cannot | Open a new terminal, `command -v em-proj && em-proj --version`; both must succeed without sourcing the project venv |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags (`pytest --no-header` and `-ra`, no `pytest-watch`)
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending (filled in after planner expands Per-Task Verification Map)
