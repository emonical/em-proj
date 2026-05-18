"""em-proj CLI entrypoint. typer dispatch + --version + --help. Subcommands mount below."""

from typing import Annotated

import typer

from em_proj import __version__

app = typer.Typer(
    name="em-proj",
    help="Personal tooling CLI under the em-proj namespace.",
    no_args_is_help=True,        # `em-proj` alone prints help (D-05 + RESEARCH Pattern 1)
    add_completion=False,        # opt out of typer auto-completion until needed
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"em-proj {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,           # process before any subcommand validation
            help="Show version and exit.",
        ),
    ] = None,
) -> None:
    """em-proj entrypoint. Subcommands live as sub-apps mounted below."""


# Phase 2 mount point — append below when state_app lands:
#
#   from em_proj.commands.state import state_app
#   app.add_typer(state_app, name="state")


if __name__ == "__main__":
    app()
