---
phase: 02-cli-shell-kv-primitive
plan: 03
wave: 2
status: complete
requirements: [KV-01, KV-02]
decisions: [D-06, D-07, D-08, D-09, D-10, D-11, D-12, D-13, D-17, D-18, D-19]
commits:
  - f71f4e0 feat(02-03): add em_proj/state/kv.py pure KV ops with KEEPTTL + key validation
  - 3b17900 test(02-03): cover kv ops — prefix, KEEPTTL, validation, not-found
written_by: orchestrator (executor rate-limited mid-plan; see Recovery)
---

# Plan 02-03 SUMMARY — Pure KV Operations Module

## What landed

`src/em_proj/state/kv.py` (177 lines) — the pure KV business-logic module. No
typer imports, no direct `redis.Redis()` construction. Unit-testable in
isolation; the Plan 04 verb layer becomes a thin translation shell on top.

`tests/unit/test_state_kv.py` (232 lines, 39 tests) — full coverage of every
kv.py code path.

## Public API (consumed by Plan 04 verb wiring)

```python
KEY_PREFIX: str = "state:kv:"          # D-06 two-segment prefix
KEY_REGEX = re.compile(r"^[a-zA-Z0-9_.\-/]+$")  # D-09
MAX_VALUE_BYTES: int = 1_048_576       # 1 MiB value cap (Claude's discretion)

class KvNotFound(KeyError): ...        # D-10 — distinct from empty-string value
class ValidationError(ValueError):     # carries .code + .message for emit_error
    code: str
    message: str

def validate_key(key: str) -> None             # D-09 — raises ValidationError
def kv_get(key: str) -> str                    # D-10 — raises KvNotFound if absent
def kv_set(key: str, value: str, ttl: int | None = None) -> None  # D-12 KEEPTTL
def kv_del(key: str) -> bool                   # D-11 — rm -f, returns existed?
def kv_list() -> list[str]                     # D-07/D-08/D-13 — sorted, prefix-stripped
```

## Decisions satisfied

| D-ID | How |
|------|-----|
| D-06 | `KEY_PREFIX = "state:kv:"` — every key stored as `state:kv:<user-key>` |
| D-07 | `kv_list()` strips `KEY_PREFIX` so caller sees the user-typed key |
| D-08 | `kv_list()` scopes to `SCAN MATCH state:kv:*` — never lock/claim namespaces |
| D-09 | `validate_key()` enforces `^[a-zA-Z0-9_.-/]+$`; rejects `:`, whitespace, empties |
| D-10 | `kv_get` returns `""` for an empty-string value; raises `KvNotFound` only on Redis `GET → None` |
| D-11 | `kv_del` is `rm -f` — returns `bool` existed, never raises for missing key |
| D-12 | `kv_set` with no `ttl` issues `SET ... KEEPTTL`; explicit `ttl` issues `SET ... EX` |
| D-13 | `kv_list` returns `[]` for an empty keyspace — valid result, not an error |
| D-17 | All KV business logic lives here; Plan 04 verbs are a thin shell |
| D-18 | Every Redis handle via `em_proj.redis_client.get_client()` — no direct `redis.Redis()`, no `ConnectionError` catching |
| D-19 | Inherits the Phase 1 single-chokepoint — connection-error translation owned by `redis_client.py` |

## Requirements

- **KV-01** (partial) — the four pure ops (`kv_get/set/del/list`) are complete; user-facing `em-proj state get|set|del|list` verb wiring is Plan 04.
- **KV-02** (partial) — `kv_set` accepts the `ttl` parameter and applies `SET ... EX`; the `--ttl` CLI flag is wired in Plan 04.

## Threats addressed

- **T-2-03-01** cross-namespace key smuggling — `validate_key` rejects `:` BEFORE `KEY_PREFIX` concatenation, so a user key can never escape into `state:lock:*` / `state:claim:*`.
- **T-2-03-03** value-size DoS — `kv_set` rejects values > 1 MiB UTF-8 with `ValidationError(code="value_too_large")` before any Redis call.
- **T-2-03-05** info disclosure — `ValidationError` messages echo only the regex spec / byte cap, never the rejected value.
- **T-2-03-06** SCAN inconsistency under concurrent writes — accepted; `state list` is a developer/debugging surface, not an authoritative ledger.

## Verification

- `bash scripts/test.sh unit` — 63 passed (39 new `test_state_kv.py` + 24 prior).
- `bash scripts/test.sh all` — 86 passed, no regressions.
- All test invocations via `bash scripts/test.sh`; no raw pytest, no pipes.

## Recovery note

The gsd-executor for this plan was interrupted by a transient server-side
rate-limit (not a usage cap) on its return path. State at interruption:
`kv.py` was complete and committed on the worktree branch
(`worktree-agent-ac822c6dc2c32c74f`, commit `f71f4e0`); `test_state_kv.py`
was fully written but uncommitted; no SUMMARY.md.

Orchestrator recovery (filesystem-fallback): merged the worktree branch into
main (brings `f71f4e0`), copied the written test file into `tests/unit/`,
ran `bash scripts/test.sh unit` to confirm all 39 tests pass (proving the
test file was complete, not truncated), committed it as `3b17900`, wrote this
SUMMARY. No re-execution of `kv.py` was needed — the executor's work was
sound and complete; only the commit-test-file + SUMMARY steps were missing.
