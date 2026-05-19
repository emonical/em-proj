#!/usr/bin/env bash
# em-proj phase verification dispatcher.
#
# Encodes the deterministic checks a GSD phase verifier would otherwise
# have to run as separate allowlisted Bash invocations. Emits a structured
# markdown report to stdout; the subagent reads the report and applies
# judgment to write VERIFICATION.md prose.
#
# Why: one allowlisted call (Bash(bash scripts/verify-phase.sh *)) covers
# what would otherwise be ~10-15 separate prompts for test runs,
# anti-pattern greps, file inventories, and structural sanity checks.
#
# Usage:
#   scripts/verify-phase.sh <phase-id>
#   scripts/verify-phase.sh 01
#
# Exit codes:
#   0 = all checks PASS
#   1 = one or more checks FAIL (report shows which)
#   2 = bad input or environment issue

set -uo pipefail
cd "$(dirname "$0")/.."

if [[ $# -lt 1 ]] || [[ "${1:-}" == "help" ]] || [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then
    cat <<'EOF'
Usage: scripts/verify-phase.sh <phase-id>

Examples:
  scripts/verify-phase.sh 01
  scripts/verify-phase.sh 02

Emits a markdown report of deterministic phase checks:
  - test suite (bash scripts/test.sh all)
  - structural tests (bash scripts/test.sh structural)
  - REDIS-01 checks (bash scripts/verify-redis-config.sh) if present
  - em-proj binary on PATH + --version output
  - anti-pattern grep (TBD/FIXME/XXX/TODO/HACK/PLACEHOLDER) on src/ tests/ scripts/
  - SUMMARY.md presence for every PLAN.md in the phase directory
  - recent git commits for traceability

Exit codes:
  0 = all checks PASS
  1 = one or more checks FAIL
  2 = bad input or environment issue
EOF
    exit 0
fi

PHASE_ID="$1"

# Resolve the phase directory (.planning/phases/<id>-<name>).
shopt -s nullglob
PHASE_CANDIDATES=(.planning/phases/"${PHASE_ID}"-*)
shopt -u nullglob
if [[ ${#PHASE_CANDIDATES[@]} -eq 0 ]]; then
    echo "verify-phase: no phase directory matching .planning/phases/${PHASE_ID}-*" >&2
    echo "Existing phases:" >&2
    ls -d .planning/phases/*/ 2>/dev/null >&2 || echo "  (none)" >&2
    exit 2
fi
PHASE_DIR="${PHASE_CANDIDATES[0]}"
PHASE_NAME=$(basename "$PHASE_DIR")

FAIL_COUNT=0

mark_pass() { printf "| %s | PASS | %s |\n" "$1" "$2"; }
mark_skip() { printf "| %s | SKIP | %s |\n" "$1" "$2"; }
mark_fail() {
    printf "| %s | **FAIL** | %s |\n" "$1" "$2"
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

# ----------------------------------------------------------------------------
# Header
# ----------------------------------------------------------------------------
echo "# verify-phase: ${PHASE_NAME}"
echo
echo "- Generated: $(date '+%Y-%m-%dT%H:%M:%S%z')"
echo "- Repo HEAD: $(git rev-parse --short HEAD) ($(git symbolic-ref --short HEAD 2>/dev/null || echo detached))"
echo "- Phase dir: \`${PHASE_DIR}\`"
echo

# ----------------------------------------------------------------------------
# Section 1: Test suite
# ----------------------------------------------------------------------------
echo "## Test suite"
echo
echo "| Check | Status | Detail |"
echo "|-------|--------|--------|"

test_output=$(bash scripts/test.sh all 2>&1)
test_exit=$?
test_summary=$(echo "$test_output" | tail -1)
if [[ $test_exit -eq 0 ]]; then
    mark_pass "scripts/test.sh all" "$test_summary"
else
    mark_fail "scripts/test.sh all" "exit $test_exit — $test_summary"
fi

structural_output=$(bash scripts/test.sh structural 2>&1)
structural_exit=$?
structural_summary=$(echo "$structural_output" | tail -1)
if [[ $structural_exit -eq 0 ]]; then
    mark_pass "scripts/test.sh structural" "$structural_summary"
else
    mark_fail "scripts/test.sh structural" "exit $structural_exit — $structural_summary"
fi
echo

# ----------------------------------------------------------------------------
# Section 2: REDIS-01 (only if script exists; not all phases need it)
# ----------------------------------------------------------------------------
echo "## Redis backend"
echo
echo "| Check | Status | Detail |"
echo "|-------|--------|--------|"
if [[ -f scripts/verify-redis-config.sh ]]; then
    redis_output=$(bash scripts/verify-redis-config.sh 2>&1)
    redis_exit=$?
    redis_summary=$(echo "$redis_output" | tail -1)
    case $redis_exit in
        0) mark_pass "verify-redis-config" "$redis_summary" ;;
        2) mark_skip "verify-redis-config" "redis unreachable (\`brew services start redis\`)" ;;
        *) mark_fail "verify-redis-config" "$redis_summary" ;;
    esac
else
    mark_skip "verify-redis-config" "script not present"
fi
echo

# ----------------------------------------------------------------------------
# Section 3: em-proj CLI on PATH
# ----------------------------------------------------------------------------
echo "## em-proj CLI"
echo
echo "| Check | Status | Detail |"
echo "|-------|--------|--------|"
if command -v em-proj >/dev/null 2>&1; then
    em_proj_path=$(command -v em-proj)
    mark_pass "em-proj on PATH" "$em_proj_path"
    version_output=$(em-proj --version 2>&1)
    version_exit=$?
    if [[ $version_exit -eq 0 ]] && [[ "$version_output" =~ em-proj[[:space:]]+[0-9]+\.[0-9]+\.[0-9]+ ]]; then
        mark_pass "em-proj --version" "$version_output"
    else
        mark_fail "em-proj --version" "exit $version_exit — $version_output"
    fi
else
    mark_fail "em-proj on PATH" "not found (run \`uv tool install --editable .\` from repo root)"
fi
echo

# ----------------------------------------------------------------------------
# Section 4: Anti-pattern markers in shipped code
# ----------------------------------------------------------------------------
echo "## Anti-pattern markers"
echo
echo "| Check | Status | Detail |"
echo "|-------|--------|--------|"
# -I  : skip binary files
# -r  : recurse
# -n  : line numbers
# Restrict to source/test/script trees only.
hits=$(grep -InrE '\b(TBD|FIXME|XXX|HACK|TODO|PLACEHOLDER)\b' \
    src/ tests/ scripts/ 2>/dev/null \
    | grep -v '/__pycache__/' \
    | grep -v '\.pyc:' \
    | grep -v 'verify-phase\.sh' \
    || true)
if [[ -z "$hits" ]]; then
    mark_pass "TBD/FIXME/XXX/HACK/TODO/PLACEHOLDER" "none found in src/ tests/ scripts/"
else
    hit_count=$(printf '%s\n' "$hits" | wc -l | tr -d ' ')
    mark_fail "TBD/FIXME/XXX/HACK/TODO/PLACEHOLDER" "$hit_count occurrence(s); see Anti-pattern details below"
fi
echo

if [[ -n "$hits" ]]; then
    echo "### Anti-pattern details"
    echo
    echo '```'
    printf '%s\n' "$hits"
    echo '```'
    echo
fi

# ----------------------------------------------------------------------------
# Section 5: Plan / SUMMARY coverage
# ----------------------------------------------------------------------------
echo "## Plan / SUMMARY coverage"
echo
echo "| Plan file | SUMMARY status | Detail |"
echo "|-----------|---------------|--------|"
shopt -s nullglob
plan_files=("$PHASE_DIR"/[0-9][0-9]-[0-9][0-9]-PLAN.md)
shopt -u nullglob
if [[ ${#plan_files[@]} -eq 0 ]]; then
    mark_fail "plan files" "no PLAN.md files in $PHASE_DIR"
else
    for plan in "${plan_files[@]}"; do
        base=$(basename "$plan")
        summary="${plan/-PLAN.md/-SUMMARY.md}"
        sum_base=$(basename "$summary")
        if [[ -f "$summary" ]]; then
            lines=$(wc -l < "$summary" | tr -d ' ')
            mark_pass "$base" "$sum_base present (${lines} lines)"
        else
            mark_fail "$base" "$sum_base missing"
        fi
    done
fi
echo

# ----------------------------------------------------------------------------
# Section 6: Recent commits on main (traceability)
# ----------------------------------------------------------------------------
echo "## Recent commits on main"
echo
# Strip leading zeros for the prefix pattern (so "01" matches "(01-NN)").
phase_num="${PHASE_ID#0}"
phase_num="${phase_num#0}"

echo "Last 15 commits:"
echo '```'
git log --oneline -15
echo '```'
echo

echo "Commits tagged for this phase (matching \`(${PHASE_ID}-NN)\`):"
echo '```'
git log --oneline -50 | grep -E "\(${PHASE_ID}-[0-9]+\)" || echo "(none found — check commit tagging convention)"
echo '```'
echo

# ----------------------------------------------------------------------------
# Footer
# ----------------------------------------------------------------------------
echo "---"
echo
if [[ $FAIL_COUNT -eq 0 ]]; then
    echo "**Result: all deterministic checks PASS** — judgment and next-phase recommendations belong in VERIFICATION.md."
    exit 0
else
    echo "**Result: $FAIL_COUNT check(s) FAIL** — VERIFICATION.md must reference the failures above."
    exit 1
fi
