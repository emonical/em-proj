#!/usr/bin/env bash
# em-proj test dispatcher.
#
# Wraps pytest invocations so Bash allowlists can pin exact-match per
# subcommand rather than granting wildcard access to pytest/uv/python.
#
# Subcommands:
#   unit            Run unit tests only (tests/unit, -x stop-on-first-fail).
#   multiprocess    Run multiprocess tests (tests/multiprocess, -v).
#   harness         Run harness self-tests only (tests/multiprocess/test_harness_self.py -v).
#   structural      Run AST/source-shape tests (tests/structural, -v). Encodes plan
#                   acceptance criteria as pytest checks so one allowlisted call
#                   covers what would otherwise be many grep/wc invocations.
#   all             Run full suite (pytest -ra).
#   conftest-check  Quick conftest import probe (constants + fixtures + dataclass).
#   collect         pytest --collect-only (lists tests; runs nothing).
#   help            This message.
#
# Options (placed BEFORE the subcommand or anywhere in argv):
#   --tail N        Capture combined stdout+stderr, print only the last N lines.
#                   Preserves the underlying pytest exit code via PIPESTATUS.
#                   Folded into the script so allowlists do not need to permit
#                   ad-hoc `... | tail -N` pipes (each such pipe would otherwise
#                   require its own allowlist entry or a wildcard).
#
# Other extra args are forwarded to pytest (e.g. `test.sh harness -k race`).
# Exit codes: pass-through from pytest (0 green, 1 failure, 2 collection error, 5 no tests).

set -uo pipefail

cd "$(dirname "$0")/.."

# Pre-parse --tail N out of argv; collect the rest into POSITIONAL.
TAIL_N=""
POSITIONAL=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tail)
            if [[ $# -lt 2 ]]; then
                echo "scripts/test.sh: --tail requires a numeric argument" >&2
                exit 2
            fi
            TAIL_N="$2"
            if ! [[ "$TAIL_N" =~ ^[0-9]+$ ]]; then
                echo "scripts/test.sh: --tail expects a non-negative integer, got '$TAIL_N'" >&2
                exit 2
            fi
            shift 2
            ;;
        *)
            POSITIONAL+=("$1")
            shift
            ;;
    esac
done
set -- "${POSITIONAL[@]}"

cmd="${1:-help}"
shift || true

# run_or_tail "$@" — invokes its args, optionally piping through `tail -n TAIL_N`.
# Preserves the pytest exit code (PIPESTATUS[0]) instead of tail's exit code.
run_or_tail() {
    if [[ -n "$TAIL_N" ]]; then
        "$@" 2>&1 | tail -n "$TAIL_N"
        exit "${PIPESTATUS[0]}"
    else
        exec "$@"
    fi
}

case "$cmd" in
    unit)
        run_or_tail uv run pytest tests/unit -x "$@"
        ;;
    multiprocess)
        run_or_tail uv run pytest tests/multiprocess -v "$@"
        ;;
    harness)
        run_or_tail uv run pytest tests/multiprocess/test_harness_self.py -v "$@"
        ;;
    structural)
        run_or_tail uv run pytest tests/structural -v "$@"
        ;;
    all)
        run_or_tail uv run pytest -ra "$@"
        ;;
    conftest-check)
        run_or_tail .venv/bin/python -c "
import sys
sys.path.insert(0, 'tests')
import conftest
from dataclasses import is_dataclass
assert conftest.TEST_DB == 15, f'TEST_DB={conftest.TEST_DB}, expected 15'
assert conftest.EM_PROJ_BIN == 'em-proj', f'EM_PROJ_BIN={conftest.EM_PROJ_BIN!r}, expected em-proj'
assert is_dataclass(conftest.RaceResult), 'RaceResult is not a dataclass'
for name in ('redis_precheck', 'clean_db', 'multiproc_race'):
    assert hasattr(conftest, name), f'conftest missing fixture: {name}'
print('conftest structure OK')
"
        ;;
    collect)
        run_or_tail uv run pytest --collect-only "$@"
        ;;
    help|--help|-h)
        sed -n '3,/^set -uo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
        exit 0
        ;;
    *)
        echo "scripts/test.sh: unknown subcommand '$cmd'" >&2
        echo "run 'scripts/test.sh help' for usage" >&2
        exit 2
        ;;
esac
