"""state subcommand family — per D-14, nested typer app mounted from cli.py.

Plans 03/04 attach the get/set/del/list verbs by importing
kv_get/kv_set/kv_del/kv_list from em_proj.state.kv and decorating thin
command wrappers with @state_app.command(). Phases 3/4 add lock.py and
claim.py as siblings using the same pattern.
"""

import typer

state_app = typer.Typer(
    name="state",
    help="KV / lock / claim primitives",
    no_args_is_help=True,        # `em-proj state` alone prints help (D-14)
    add_completion=False,        # opt out of typer auto-completion until needed
)
