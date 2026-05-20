"""CLI-03 partial coverage — `em-proj state --help` renders typer auto-help (D-14 mount verification).

Uses typer.testing.CliRunner for in-process invocation (no shell-out to the
installed em-proj binary). Verifies only the empty-mount behavior — these
tests do NOT depend on Plans 03/04 verb wiring and do NOT require a running
Redis (no clean_db fixture, no redis_precheck).
"""
from typer.testing import CliRunner

from em_proj.cli import app

runner = CliRunner()

# The literal D-14 mount help string, asserted verbatim below.
MOUNT_HELP_STRING = "KV / lock / claim primitives"


def test_state_mount_help_exits_zero() -> None:
    """`em-proj state --help` exits 0 (the state sub-app is mounted under root)."""
    result = runner.invoke(app, ["state", "--help"])
    assert result.exit_code == 0, (
        f"expected exit 0, got {result.exit_code}; stdout={result.stdout!r}"
    )


def test_state_help_mentions_mount_string() -> None:
    """`em-proj state --help` stdout contains the D-14 mount help text."""
    result = runner.invoke(app, ["state", "--help"])
    assert MOUNT_HELP_STRING in result.stdout, (
        f"expected {MOUNT_HELP_STRING!r} in state --help output, got {result.stdout!r}"
    )


def test_state_no_args_shows_help() -> None:
    """`em-proj state` with no verb prints help (no_args_is_help=True), no unhandled exception.

    typer's no_args_is_help can return exit code 0 or 2 depending on version;
    both are acceptable — what matters is no unhandled exception is raised.
    """
    result = runner.invoke(app, ["state"])
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"expected no unhandled exception, got {result.exception!r}"
    )
    assert result.exit_code in (0, 2), (
        f"expected exit 0 or 2 from no_args_is_help, got {result.exit_code}; "
        f"stdout={result.stdout!r}"
    )
