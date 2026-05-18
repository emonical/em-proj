"""CLI-02 coverage — typer dispatch scaffold: --version and --help.

Uses typer.testing.CliRunner for in-process invocation (no shell-out to the
installed em-proj binary — those tests live in tests/multiprocess/).
"""
from typer.testing import CliRunner

from em_proj import __version__
from em_proj.cli import app

runner = CliRunner()


def test_version() -> None:
    """`em-proj --version` exits 0 and prints `em-proj {__version__}`."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; stdout={result.stdout!r}"
    assert f"em-proj {__version__}" in result.stdout, (
        f"expected 'em-proj {__version__}' in stdout, got {result.stdout!r}"
    )


def test_help() -> None:
    """`em-proj --help` exits 0 and renders typer's auto-help (mentions --version + program name)."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; stdout={result.stdout!r}"
    assert "--version" in result.stdout, (
        f"expected '--version' in help output, got {result.stdout!r}"
    )
    assert "em-proj" in result.stdout.lower() or "Usage:" in result.stdout, (
        f"expected program name or 'Usage:' marker in help, got {result.stdout!r}"
    )
