"""Unit tests for resolve_upstream_identity and _canonicalize_upstream_url — stateless, no Redis.

Covers RESERVE-01: upstream-identity resolver + canonicalizer shipped in Plan 07-01.

No Redis fixtures here — identity.py is stdlib + psutil only; these tests run
with Redis completely absent (no live Redis dependency).

Test inventory (20 tests):
  --- Canonicalizer (13 parametrized rows, one test function) ---
  test_canonicalize_upstream_url_table            — all 13 rows from RESEARCH §Pattern 1

  --- Resolver (7 behavior cases) ---
  test_resolve_upstream_identity_with_origin_returns_canonical    — ssh URL → canonical
  test_resolve_upstream_identity_with_https_origin_canonicalizes  — https URL → canonical
  test_resolve_upstream_identity_no_git_dir_falls_back_to_project_hash — no .git/ → fallback
  test_resolve_upstream_identity_empty_origin_falls_back          — empty URL → fallback (Pitfall #2)
  test_resolve_upstream_identity_unparseable_origin_falls_back    — bad URL → fallback
  test_resolve_upstream_identity_explicit_cwd_argument            — cwd= kwarg honored
  test_resolve_upstream_identity_git_missing_falls_back           — FileNotFoundError → fallback
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from em_proj.identity import (
    _canonicalize_upstream_url,
    resolve_project_hash,
    resolve_upstream_identity,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_fake_git_config(tmp_path: Path, origin_url: str) -> None:
    """Create a minimal fake git repo satisfying git -C <dir> remote get-url origin.

    Uses ``git init`` to create a real (empty) git repository, then appends the
    [remote "origin"] section to .git/config so that ``git remote get-url origin``
    returns the given URL.

    A plain .git/config + .git/HEAD without ``git init`` is NOT sufficient — git
    requires a valid objects/ directory structure to recognise the directory as a
    repository.  Rule 1 fix applied during Plan 07-01 execution.
    """
    import subprocess as _subprocess
    _subprocess.run(
        ["git", "init", str(tmp_path)],
        capture_output=True,
        check=True,
    )
    git_config = tmp_path / ".git" / "config"
    with git_config.open("a") as f:
        f.write(
            '[remote "origin"]\n'
            f'\turl = {origin_url}\n'
            '\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
        )


# ---------------------------------------------------------------------------
# Canonicalizer — 13-row parametrized table (RESEARCH §Pattern 1 lines 320-333)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("git@github.com:emonical/roleplay-engine.git", "github.com:emonical/roleplay-engine"),
        ("git@github.com:emonical/roleplay-engine", "github.com:emonical/roleplay-engine"),
        ("https://github.com/emonical/roleplay-engine.git", "github.com:emonical/roleplay-engine"),
        ("https://github.com/emonical/roleplay-engine", "github.com:emonical/roleplay-engine"),
        ("https://github.com/emonical/roleplay-engine/", "github.com:emonical/roleplay-engine"),
        ("ssh://git@github.com/emonical/roleplay-engine.git", "github.com:emonical/roleplay-engine"),
        ("ssh://git@github.com:22/emonical/roleplay-engine.git", "github.com:emonical/roleplay-engine"),
        ("https://user:token@github.com/emonical/roleplay-engine.git", "github.com:emonical/roleplay-engine"),
        ("https://GitHub.COM/emonical/roleplay-engine", "github.com:emonical/roleplay-engine"),
        ("https://github.com/EMonical/RolePlay-Engine", "github.com:EMonical/RolePlay-Engine"),
        ("git@gitlab.example.com:org/sub/repo.git", "gitlab.example.com:org/sub/repo"),
        ("", None),
        ("not-a-url", None),
    ],
    ids=[
        "scp_with_git_suffix",
        "scp_no_suffix",
        "https_with_git",
        "https_no_suffix",
        "https_trailing_slash",
        "ssh_protocol",
        "ssh_with_port",
        "user_token_https",
        "host_lowercased",
        "owner_repo_case_preserved",
        "subgroup_path",
        "empty",
        "malformed",
    ],
)
def test_canonicalize_upstream_url_table(raw: str, expected: str | None) -> None:
    """All 13 RESEARCH §Pattern 1 rows pass through the canonicalizer correctly."""
    assert _canonicalize_upstream_url(raw) == expected


# ---------------------------------------------------------------------------
# Resolver — 7 behavior cases (PLAN §Task 1 <behavior>)
# ---------------------------------------------------------------------------


def test_resolve_upstream_identity_with_origin_returns_canonical(
    monkeypatch, tmp_path
) -> None:
    """SSH origin URL → canonical host:owner/repo returned (not project_hash fallback)."""
    _make_fake_git_config(tmp_path, "git@github.com:emonical/repo.git")
    monkeypatch.chdir(tmp_path)
    result = resolve_upstream_identity()
    assert result == "github.com:emonical/repo"


def test_resolve_upstream_identity_with_https_origin_canonicalizes(
    monkeypatch, tmp_path
) -> None:
    """HTTPS origin URL → canonical host:owner/repo returned."""
    _make_fake_git_config(tmp_path, "https://github.com/emonical/my-project.git")
    monkeypatch.chdir(tmp_path)
    result = resolve_upstream_identity()
    assert result == "github.com:emonical/my-project"


def test_resolve_upstream_identity_no_git_dir_falls_back_to_project_hash(
    monkeypatch, tmp_path
) -> None:
    """No .git/ directory → git exits non-zero → resolve_project_hash() returned.

    Pitfall #7 from RESEARCH: a directory without .git/ causes git to exit non-zero.
    The resolver must fall back gracefully, not raise.
    """
    # tmp_path has no .git/ — git will return non-zero
    monkeypatch.chdir(tmp_path)
    result = resolve_upstream_identity()
    expected_fallback = resolve_project_hash()
    assert result == expected_fallback


def test_resolve_upstream_identity_empty_origin_falls_back(
    monkeypatch, tmp_path
) -> None:
    """Fake config with empty URL → git returns 0 with empty stdout → fallback (Pitfall #2)."""
    _make_fake_git_config(tmp_path, "")
    monkeypatch.chdir(tmp_path)
    result = resolve_upstream_identity()
    expected_fallback = resolve_project_hash()
    assert result == expected_fallback


def test_resolve_upstream_identity_unparseable_origin_falls_back(
    monkeypatch, tmp_path
) -> None:
    """Fake config with unparseable URL → canonicalization returns None → fallback."""
    _make_fake_git_config(tmp_path, "not-a-url")
    monkeypatch.chdir(tmp_path)
    result = resolve_upstream_identity()
    expected_fallback = resolve_project_hash()
    assert result == expected_fallback


def test_resolve_upstream_identity_explicit_cwd_argument(tmp_path) -> None:
    """cwd= kwarg is honored without monkeypatch.chdir — the cwd argument routes git -C."""
    _make_fake_git_config(tmp_path, "git@github.com:emonical/explicit-cwd-test.git")
    # No monkeypatch.chdir — pass cwd explicitly
    result = resolve_upstream_identity(cwd=str(tmp_path))
    assert result == "github.com:emonical/explicit-cwd-test"


def test_resolve_upstream_identity_git_missing_falls_back(monkeypatch, tmp_path) -> None:
    """FileNotFoundError from subprocess.run (git not on PATH) → fallback to project_hash.

    Implementation note: We monkeypatch subprocess.run directly to raise
    FileNotFoundError rather than manipulating PATH, because PATH manipulation
    can be unreliable on macOS where /usr/bin/git may be found via /etc/paths
    or shell init files outside the env we control.  The monkeypatch approach
    is deterministic and tests the exact except-branch in the resolver.
    """
    _make_fake_git_config(tmp_path, "git@github.com:emonical/repo.git")
    monkeypatch.chdir(tmp_path)

    def _raise_file_not_found(*args, **kwargs):
        raise FileNotFoundError("git: No such file or directory")

    monkeypatch.setattr("em_proj.identity.subprocess.run", _raise_file_not_found)
    result = resolve_upstream_identity()
    expected_fallback = resolve_project_hash()
    assert result == expected_fallback
