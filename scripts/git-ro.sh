#!/usr/bin/env bash
# em-proj read-only git wrapper for `rtk git -C <path> <subcommand>`.
#
# Why: allows allowlisting `Bash(bash scripts/git-ro.sh *)` as a single entry
# covering all non-destructive git inspection across the main repo and any
# attached worktrees. Without this wrapper, inspecting agent worktrees would
# need either separate per-path allowlist entries or wildcard git access.
#
# Usage:
#   scripts/git-ro.sh <path> <subcommand> [args...]
#
# Examples:
#   scripts/git-ro.sh . status
#   scripts/git-ro.sh .claude/worktrees/agent-xxx log --oneline -10
#   scripts/git-ro.sh .claude/worktrees/agent-xxx show --stat 0c95833
#   scripts/git-ro.sh . diff --stat HEAD~1
#
# Routing: prefers `rtk git` for token savings; falls back to plain `git` if
# rtk is not on PATH.
#
# Subcommand whitelist: non-destructive subcommands only (see ALLOWED below).
# Destructive flags on otherwise-safe subcommands (e.g. `git branch -d`,
# `git config <key> <value>`, `git worktree add`) are explicitly rejected
# even when the parent subcommand is on the whitelist.

set -uo pipefail

# Non-destructive subcommands only. If you need a destructive op, use raw
# git via an exact-match allowlist entry, NOT this wrapper.
ALLOWED_SUBCOMMANDS=(
    status log diff show
    ls-files ls-tree rev-parse rev-list describe
    branch worktree remote tag stash
    blame shortlog reflog cherry
    config cat-file name-rev merge-base check-ignore
    count-objects fsck show-ref show-branch
    grep
)

usage() {
    cat <<EOF
scripts/git-ro.sh — read-only git wrapper (routes through rtk if present)

Usage: scripts/git-ro.sh <path> <subcommand> [args...]

Allowed subcommands (non-destructive):
  ${ALLOWED_SUBCOMMANDS[*]}

Subcommand-specific guards (destructive flags rejected even on allowed subs):
  branch    rejects -d -D -m -M -c -C --delete --move --copy
  worktree  only 'list' allowed (add/remove/move/prune/lock/unlock/repair blocked)
  remote    only readonly (add/remove/rename/set-url/update/prune blocked)
  tag       only listing (creation/deletion via -d -a -s -f blocked)
  stash     only 'list' and 'show' (push/pop/drop/clear blocked)
  config    only --get/--list/--get-all (--unset/--add/edit/set blocked)

For destructive operations, use raw git via exact-match allowlist entries.
EOF
}

if [[ $# -lt 2 ]] || [[ "${1:-}" == "help" ]] || [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi

path="$1"
subcommand="$2"
shift 2

# Subcommand whitelist check.
allowed=0
for sub in "${ALLOWED_SUBCOMMANDS[@]}"; do
    if [[ "$sub" == "$subcommand" ]]; then
        allowed=1
        break
    fi
done
if [[ $allowed -eq 0 ]]; then
    echo "scripts/git-ro.sh: subcommand '$subcommand' is not in the read-only whitelist" >&2
    echo "Allowed: ${ALLOWED_SUBCOMMANDS[*]}" >&2
    exit 2
fi

# Per-subcommand destructive-flag guards.
case "$subcommand" in
    branch)
        for arg in "$@"; do
            case "$arg" in
                -d|-D|-m|-M|-c|-C|--delete|--move|--copy)
                    echo "scripts/git-ro.sh: 'git branch $arg' mutates refs; not permitted" >&2
                    exit 2
                    ;;
            esac
        done
        ;;
    worktree)
        if [[ $# -gt 0 ]]; then
            case "$1" in
                list)
                    ;;
                add|remove|move|prune|lock|unlock|repair)
                    echo "scripts/git-ro.sh: 'git worktree $1' is destructive; only 'list' is allowed" >&2
                    exit 2
                    ;;
            esac
        fi
        ;;
    remote)
        if [[ $# -gt 0 ]]; then
            case "$1" in
                add|remove|rm|rename|set-url|set-head|set-branches|update|prune)
                    echo "scripts/git-ro.sh: 'git remote $1' is destructive; not permitted" >&2
                    exit 2
                    ;;
            esac
        fi
        ;;
    tag)
        for arg in "$@"; do
            case "$arg" in
                -d|--delete|-a|--annotate|-s|--sign|-f|--force)
                    echo "scripts/git-ro.sh: 'git tag $arg' creates/deletes tags; not permitted" >&2
                    exit 2
                    ;;
            esac
        done
        # A tag-name positional (no leading dash) means create; reject.
        for arg in "$@"; do
            if [[ "$arg" != -* ]] && [[ "$arg" != "list" ]]; then
                echo "scripts/git-ro.sh: 'git tag $arg' creates a tag; not permitted (use -l/--list for listing)" >&2
                exit 2
            fi
        done
        ;;
    stash)
        if [[ $# -gt 0 ]]; then
            case "$1" in
                list|show)
                    ;;
                *)
                    echo "scripts/git-ro.sh: 'git stash $1' is destructive; only 'list' and 'show' allowed" >&2
                    exit 2
                    ;;
            esac
        fi
        ;;
    config)
        for arg in "$@"; do
            case "$arg" in
                --unset|--unset-all|--add|--rename-section|--remove-section|--replace-all|--edit|-e)
                    echo "scripts/git-ro.sh: 'git config $arg' mutates config; not permitted" >&2
                    exit 2
                    ;;
            esac
        done
        # Two-positional form `git config <key> <value>` is a set; reject.
        nonflag_count=0
        for arg in "$@"; do
            [[ "$arg" != -* ]] && nonflag_count=$((nonflag_count + 1))
        done
        if [[ $nonflag_count -ge 2 ]]; then
            echo "scripts/git-ro.sh: 'git config <key> <value>' sets a value; not permitted" >&2
            exit 2
        fi
        ;;
esac

# Dispatcher: prefer rtk (token-savings proxy), fall back to plain git.
if command -v rtk >/dev/null 2>&1; then
    exec rtk git -C "$path" "$subcommand" "$@"
else
    exec git -C "$path" "$subcommand" "$@"
fi
