#!/usr/bin/env bash
# Verify REDIS-01 settings on the brew-managed Redis instance.
# Exit codes:
#   0 = all four settings correct + AOF on disk
#   1 = one or more settings wrong (printed to stderr)
#   2 = redis unreachable (printed to stderr; suggests `brew services start redis`)

set -uo pipefail

AOF_DIR="/opt/homebrew/var/db/redis"

if ! redis-cli ping > /dev/null 2>&1; then
    echo "verify-redis-config: redis unreachable at 127.0.0.1:6379 — run \`brew services start redis\`" >&2
    exit 2
fi

errs=0

check() {
    local key="$1" expected="$2"
    local actual
    actual=$(redis-cli CONFIG GET "$key" | tail -n 1)
    if [[ "$actual" != "$expected" ]]; then
        echo "verify-redis-config: $key expected '$expected', got '$actual'" >&2
        errs=$((errs + 1))
    fi
}

check appendonly  yes
check appendfsync everysec
check save        "900 1"

# AOF file presence — glob-tolerant for Redis 8.x split AOF (RESEARCH Open Question #1).
# Use glob expansion (NOT `ls | grep` — PROJECT.md Constraints forbid `ls | while read` and the
# user's RTK wraps `ls` with token-saving output that mangles parsing).
#
# Redis 8.x default layout lives under `$AOF_DIR/appendonlydir/` (multi-part: base.rdb +
# incr.aof + manifest), driven by the `appenddirname` config knob which defaults to
# "appendonlydir". Older Redis versions wrote a single `appendonly.aof` file directly in
# `$AOF_DIR`. The glob below covers BOTH layouts — empty match in either pattern is
# absorbed by nullglob.
shopt -s nullglob
aof_files=("$AOF_DIR"/appendonly.aof* "$AOF_DIR"/appendonlydir/appendonly.aof*)
if [[ ${#aof_files[@]} -eq 0 ]]; then
    echo "verify-redis-config: no AOF file under $AOF_DIR (checked both monolithic and appendonlydir/ layouts) — run \`redis-cli SET _bootstrap 1\` to force creation" >&2
    errs=$((errs + 1))
fi

if [[ $errs -gt 0 ]]; then
    echo "verify-redis-config: $errs check(s) failed" >&2
    exit 1
fi

echo "verify-redis-config: OK (appendonly=yes, appendfsync=everysec, save=900 1, AOF present)"
exit 0
