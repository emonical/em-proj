"""em-proj CLI entrypoint. typer dispatch + --version + --help. Subcommands mount below."""

from typing import Annotated

import typer

from em_proj import __version__
from em_proj.session import session_app
from em_proj.state import state_app

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


# Phase 2 subcommand mount (D-14) — nested typer app for KV / lock / claim verbs.
app.add_typer(state_app, name="state", help="KV / lock / claim primitives")

# Phase 8 subcommand mount (D-14) — session registry verbs.
app.add_typer(session_app, name="session", help="Session registry — register, heartbeat, list, show.")


if __name__ == "__main__":
    app()
