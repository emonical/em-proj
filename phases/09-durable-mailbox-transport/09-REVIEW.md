---
phase: 09-durable-mailbox-transport
reviewed: 2026-06-07T00:00:00Z
depth: standard
files_reviewed: 3
files_reviewed_list:
  - src/em_proj/message/_ops.py
  - src/em_proj/message/__init__.py
  - src/em_proj/cli.py
findings:
  critical: 1
  warning: 3
  info: 1
  total: 5
status: issues
triage:
  triaged_by: orchestrator
  critical_confirmed: 0
  critical_rejected: ["CR-01 — false positive (RESP2 list shape, not RESP3 dict)"]
  real_actionable: ["WR-02 — malformed --since traceback (user-facing)"]
  defensive_optional: ["WR-01 — corrupt-payload decode guard"]
  non_issue: ["WR-03 — client IS used for the reachability check"]
  deferred_phase_10: ["IN-01 — topic/from_session caps (no send verb yet)"]
---

# Phase 9: Code Review Report

**Reviewed:** 2026-06-07
**Depth:** standard
**Files Reviewed:** 3
**Status:** issues_found

## Summary

Reviewed the Phase 9 durable mailbox transport implementation against the
09-02/09-03 PLAN threat models, the claim.py analog, and the three focus areas:
cross-session isolation, consume-ack correctness, and input validation.

The implementation is structurally sound: D-18 (no connection-error catch in ops),
D-14 (no business logic in the verb layer), cross-session isolation via
`resolve_session_id()` from env, and the consume-ack non-atomicity are all handled
correctly and documented. The `mbox_blocking_read` function contains a
correctness defect in how it interprets the redis-py XREAD return value under
`decode_responses=True`. There are also two warning-class gaps in error propagation.

---

## Critical Issues

### CR-01: `mbox_blocking_read` result unpacking is wrong for `decode_responses=True`

**File:** `src/em_proj/message/_ops.py:283-285`

**Issue:** The comment at line 283 documents the expected shape as
`[[stream_name, [(id, fields), ...]]]` — a list of 2-element lists. That is the
**bytes-mode** shape from redis-py. With `decode_responses=True` (which is what
`get_client()` returns per `redis_client.py:44`), redis-py's XREAD returns a
**dict** keyed by stream name: `{stream_name: [(id, fields), ...]}`.

The code then does:

```python
_, entries = result[0]
```

On a `dict`, `result[0]` is **not** a `(stream_name, entries)` tuple. In Python
3.7+, iterating or indexing a dict by integer does not work — `dict[0]` raises
`TypeError` (dicts are not subscriptable by integer). This crashes with
`TypeError: unhashable type` or a `KeyError` the first time `mbox_blocking_read`
is called with any matching entries.

The function is marked "Phase 11 only" and has no unit test in the current test
suite (`test_mailbox.py` does not test `mbox_blocking_read`), so this defect is
latent but will surface immediately when Phase 11 wires it up.

**Fix:**

Unpack from the dict directly. The stream name is the key the function already
knows (`key`):

```python
result = client.xread(
    streams={key: last_id},
    count=10,
    block=block_ms,
)
if not result:
    return []
# decode_responses=True: result is {stream_name: [(id, fields), ...]}
entries = result[key]
return [_decode_entry(eid, fields) for eid, fields in entries]
```

Alternatively, if guarding against the key being absent in the result dict:

```python
entries = result.get(key, [])
return [_decode_entry(eid, fields) for eid, fields in entries]
```

---

## Warnings

### WR-01: `_decode_entry` has no protection against corrupt or missing payload

**File:** `src/em_proj/message/_ops.py:112`

**Issue:** `_decode_entry` calls `json.loads(fields["payload"])` with no
exception handling. Two failure paths:

1. `fields["payload"]` raises `KeyError` if the stream entry was written by
   external tooling or a future code path that omitted the `payload` field.
2. `json.loads(...)` raises `json.JSONDecodeError` if the stored value is
   truncated or corrupt (e.g. Redis memory eviction mid-write, external XADD).

Neither is caught. The error propagates through `mailbox_inbox` → `inbox_cmd`,
which has no try/except. The `die_if_redis_unreachable` guard does not cover
application-level decode errors. The result is an unhandled exception printed
as a full Python traceback, breaking the "no traceback on recoverable errors"
contract the rest of the codebase maintains.

This is especially relevant because `mailbox_inbox` applies a list comprehension
over ALL returned entries — a single corrupt entry aborts the entire read and
leaves the caller with no messages, even though the prior entries decoded fine.

**Fix:** Wrap the decode in `_decode_entry` and skip or surface corrupt entries:

```python
def _decode_entry(entry_id: str, fields: dict) -> dict:
    try:
        payload = json.loads(fields["payload"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise MailboxError(
            f"corrupt stream entry {entry_id!r}: {exc}"
        ) from exc
    payload["msg_id"] = entry_id
    return payload
```

Then in `mailbox_inbox`, either skip corrupt entries with a log/warning, or let
`MailboxError` propagate cleanly (at least it gives a typed, non-traceback error
at the verb layer). The current `inbox_cmd` does not catch `MailboxError` — that
is fine if `MailboxError` is allowed to produce a clean CLI error, but the verb
layer would need to emit it as an `emit_error` response.

---

### WR-02: Unvalidated `--since` causes unhandled `ResponseError` traceback

**File:** `src/em_proj/message/_ops.py:237` / `src/em_proj/message/__init__.py:90`

**Issue:** The `since` parameter is accepted directly from the CLI (`--since
<id>`) and passed verbatim to Redis XRANGE as `f"({since}"`. Redis 6.2+ rejects
a malformed stream ID with a `RESP ResponseError` (e.g. `ERR Invalid stream ID
specified as stream command argument`). This error is completely unhandled and
surfaces as a Python traceback.

Examples of triggering inputs:
- `--since ""` → min becomes `"("` (empty ID after prefix)
- `--since "not-a-stream-id"` → Redis rejects `(not-a-stream-id`
- `--since "1234"` (missing sequence) → `(1234` — Redis may accept this as
  `1234-0` implicitly on some versions, but the behavior is version-dependent

The threat model T-09-02-05 marks this "accept" for Phase 9 citing machine-local
single-user context. That is reasonable for the authorization concern. However,
the traceback emission is a separate quality concern: even a local user supplying
a bad cursor should get a clean error message, not a stack trace. The plan's own
exit-code contract says exit 1 = Redis unreachable — a malformed-ID crash exits
with a non-zero code via uncaught exception, violating that contract.

**Fix:** Add a format guard before building `min_id`:

```python
import re

_STREAM_ID_RE = re.compile(r"^\d+-\d+$|^\d+$")

def _validate_since(since: str) -> None:
    """Raise ValidationError if since is not a valid Redis stream entry ID."""
    if not _STREAM_ID_RE.match(since):
        raise ValidationError(
            code="validation_error",
            message=f"invalid --since value {since!r}: must be a stream entry ID (e.g. '1717500000000-0')",
        )
```

Call `_validate_since(since)` in `mailbox_inbox` before building `min_id` when
`since is not None`. The `inbox_cmd` does not need to change — `ValidationError`
already propagates as an unhandled exception to Typer, which prints its message.

Alternatively, wrap the `client.xrange(...)` call in a try/except for
`redis.ResponseError` and re-raise as `ValidationError`.

---

### WR-03: `inbox_cmd` acquires a Redis client that is never used by the command itself

**File:** `src/em_proj/message/__init__.py:87-88`

**Issue:** `inbox_cmd` calls `get_client()` and assigns the result to `client`,
then passes `client` to `die_if_redis_unreachable(client)`. Immediately after,
it calls `mailbox_inbox(session_id, ...)`, which calls `get_client()` again
internally (safe — it is a singleton, so no second connection is made). The
local `client` variable is not used beyond the reachability check.

This is not a bug, but it is the same latent coupling issue present in some
session verbs: if `mailbox_inbox` were ever refactored to accept a `client`
argument, the caller forgets to pass it and silently creates a second singleton.
More immediately, the pattern is inconsistent with the plan's own D-14 contract
("the verb layer is a three-step wrapper") — step 2 says "obtain the Redis
singleton," implying it is passed down, but it is not.

**Fix:** Remove the unused `client` variable or pass it through:

```python
# Option A — remove the local var; die_if_redis_unreachable accepts a fresh client
die_if_redis_unreachable(get_client())
session_id = resolve_session_id()
messages = mailbox_inbox(session_id, since=since, peek=peek)
```

This is a quality fix, not a correctness fix. The singleton guarantee makes the
current code functionally correct.

---

## Info

### IN-01: `topic` and `from_session` fields are unbounded in the stored payload

**File:** `src/em_proj/message/_ops.py:168-176`

**Issue:** `_validate_body` caps `msg["body"]` at `MAX_BODY_CHARS` (4096). The
`from_session`, `pattern`, `scope`, and `topic` fields are stored without any
length cap. The total stored payload can exceed `MAX_BODY_CHARS` by up to
`len(from_session) + len(topic) + overhead`. The threat model T-09-02-02 states
"MAXLEN is the hard(ish) bound" for DoS, which is true at the entry count level,
but the per-entry size bound is not enforced.

For Phase 9 this is acceptable: `from_session` is env-var derived (bounded by
`CLAUDE_CODE_SESSION_ID` length), and there is no `send` verb yet to supply
`topic` from CLI. When Phase 10 adds the `send` verb with a caller-supplied
`topic`, a cap should be added (mirror `MAX_BODY_CHARS` pattern with a
`MAX_TOPIC_CHARS` constant and `_validate_topic`).

**Fix (Phase 10 prerequisite):** Add before the Phase 10 `send` verb ships:

```python
MAX_TOPIC_CHARS: int = 256  # mirror claim.py MAX_REASON_CHARS

def _validate_topic(topic: str | None) -> None:
    if topic is not None and len(topic) > MAX_TOPIC_CHARS:
        raise ValidationError(
            code="validation_error",
            message=f"topic exceeds {MAX_TOPIC_CHARS} characters",
        )
```

Call `_validate_topic(msg.get("topic"))` in `mbox_write` before the Redis call.

---

_Reviewed: 2026-06-07_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_

---

## Orchestrator Triage (post-review verification, 2026-06-07)

The findings above are the raw reviewer output. After verifying each against the
source, the actionable picture is:

### CR-01 — **REJECTED (false positive).**
The reviewer assumed `decode_responses=True` makes `xread` return a **dict** (RESP3
map semantics). But `get_client()` (`redis_client.py:38-45`) constructs the client
**without `protocol=3`**, so the connection is **RESP2**, where redis-py's `xread`
returns a **list of `[stream_name, entries]` pairs**. The existing
`_, entries = result[0]` correctly unpacks the first pair (confirmed by the
docstring shape `[[stream_name, [(id, fields)…]]]`). **The code is correct; do NOT
apply the proposed `result[key]` fix — it would break working code under RESP2.**
Optional forward-compat hardening (handle both list and dict) could be added if the
project ever switches to `protocol=3`, but that is not a bug today. `mbox_blocking_read`
is currently unexercised (Phase 11), so a regression test would be worthwhile when it is wired.

### WR-02 — **CONFIRMED real (user-facing), recommend fix.**
A malformed `--since` (e.g. `--since foo`) builds `min="(foo"` and Redis rejects the
stream ID with `ResponseError` **even on an empty/absent mailbox** (Redis validates ID
syntax before key lookup) → unhandled traceback, violating the no-traceback contract.
NOTE: the reviewer's claim that "inbox_cmd does not need to change" is **incomplete** —
an uncaught `ValidationError` still tracebacks through Typer. A correct fix needs BOTH a
`_validate_since` guard in `_ops.py` AND verb-layer surfacing via `emit_error(...)`
(the project's `ValidationError → emit_error` envelope pattern in `output.py`).

### WR-01 — defensive/optional. Corrupt-payload guard in `_decode_entry`; in normal
operation only `mbox_write` creates entries (always valid JSON), so this hardens against
data corruption that "shouldn't" happen. Cheap; pair with WR-02 if a fix pass is run.

### WR-03 — **non-issue.** `client` IS used — it is passed to `die_if_redis_unreachable(client)`.
No change needed.

### IN-01 — **deferred to Phase 10** (correct). `topic` is not caller-supplied until the
Phase 10 `send` verb exists; add `MAX_TOPIC_CHARS` + `_validate_topic` then.

**Net:** 0 confirmed Critical. 1 real user-facing Warning (WR-02) + 1 optional defensive
(WR-01) are good candidates for a `/gsd-code-review 09 --fix` pass or to fold into the
phase PR review. None block phase completion — the phase goal is delivered and the full
suite is green.
