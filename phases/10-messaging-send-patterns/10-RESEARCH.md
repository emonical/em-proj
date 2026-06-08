# Phase 10: Messaging Send Patterns — Research

**Researched:** 2026-06-07
**Domain:** Redis Streams fan-out, session registry scope enumeration, topic membership, CLI verb wiring
**Confidence:** HIGH — all findings grounded in verified Phase 8/9 source code

---

## Summary

Phase 10 builds the write side of the inter-session messaging system. The
mailbox (Redis Streams, `mbox_write`, `MBOX_KEY_PREFIX`) and the session
registry (`state:session:*` HASH scan with `project_hash` / `upstream_identity`
fields) are both fully shipped and verified. Phase 10's job is to wire those
two primitives together into three CLI verbs (`send`, `broadcast`,
`subscribe`/`unsubscribe`) and prove end-to-end delivery via a multiprocess
harness.

The session registry does NOT have a list-by-scope index. `session_list()`
returns all live sessions as a flat list, and Phase 10 must filter that list
in Python by `project_hash` or `upstream_identity` field to enumerate
broadcast recipients. No new Redis index is needed — the existing full-scan +
Python filter is exactly the same pattern the enrichment join already uses.

Topic membership is the only truly new storage primitive: a Redis SET per
`(scope_key, topic)` holding subscriber session IDs. The pub/sub PUBLISH side
is a fire-and-forget layer Phase 10 may ship (the channel name is deterministic
from scope + topic) while Phase 11 ships the subscriber daemon. Critically,
durable delivery via `mbox_write` is mandatory now and must not depend on the
daemon existing.

**Primary recommendation:** Three plans — (1) `_ops.py` extensions
(scope enumeration helper, topic SET ops, `send_directed`, `send_broadcast`,
`send_topic`), (2) CLI verbs (`send`, `broadcast`, `subscribe`, `unsubscribe`)
wired into `message_app`, (3) TEST-04 multiprocess harness + structural tests.

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MSG-01 | `em-proj message send --to <session_id> <body>` — directed to one session | `mbox_write` is the write path; `session_show` validates recipient exists |
| MSG-02 | `em-proj message broadcast <body> --scope <project\|upstream\|machine>` | `session_list()` + Python filter by `project_hash`/`upstream_identity` enumerates recipients |
| MSG-03 | `subscribe`/`unsubscribe <topic>`; `send --topic <topic> --scope <...>` | Redis SET keyed by `topic:<scope_key>:<topic>` stores subscriber session IDs |
| MSG-04 | Scope selectable per message; directed routes by `session_id` regardless of scope | Directly implementable via existing `session_list()` API + `identity.py` helpers |
| MSG-05 | Parseable delivery metadata + semantic exit codes | `emit_ok` with `{recipients_written: N, pub_published: N}` dict; exit 0 = success, 1 = Redis unreachable, 2 = recipient not found, 4 = partial fanout |
| TEST-04 | Harness: A→B delivery × 3 patterns × 3 scopes | `tests/multiprocess/test_message_delivery.py`; mailbox path testable now; live daemon path skip-stubbed until Phase 11 |
</phase_requirements>

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Scope enumeration (list sessions by project/upstream/machine) | Backend (`_ops.py`) | — | `session_list()` already returns all live sessions with `project_hash`/`upstream_identity` fields; filter in Python at ops layer |
| Topic membership storage (subscribe/unsubscribe) | Backend (`_ops.py`) | — | Redis SET, same pattern as all other state primitives; no caller needs to know the key shape |
| Fan-out loop (one send → N mailbox writes) | Backend (`_ops.py`) | — | `mbox_write` is the write primitive; the loop is business logic, belongs in `_ops.py`, not in the verb shell |
| Pub/sub PUBLISH (fire-and-forget live path) | Backend (`_ops.py`) | — | Deterministic channel name from scope+session; `client.publish(channel, payload)` — one call per recipient |
| CLI verbs (`send`, `broadcast`, `subscribe`, `unsubscribe`) | CLI mount (`message/__init__.py`) | — | D-14 thin-verb-shell discipline: three-step wrapper (resolve_json_mode → die_if_redis_unreachable → one ops call → emit) |
| Delivery metadata output | CLI mount | — | `emit_ok(data={recipients_written, pub_published, ...})` — same envelope pattern as all prior verbs |

---

## Standard Stack

### Core (all existing — no new dependencies)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis-py | 7.4.0 [VERIFIED: Phase 9 VERIFICATION.md] | `XADD`/`SADD`/`SREM`/`SMEMBERS`/`PUBLISH` | Project-locked; already used for all Redis ops |
| typer | (project-locked) | CLI verb wiring | Project-locked; all existing verbs use it |
| psutil | (project-locked) | Identity/stale probe | Used by `identity.py` → `session_list()` |

No new pip dependencies required. [VERIFIED: all messaging ops can be
implemented with existing redis-py + Python stdlib]

### New Redis data structures introduced in Phase 10

| Structure | Key shape | Purpose |
|-----------|-----------|---------|
| Redis SET | `topic:<scope_key>:<topic>` | Subscriber session IDs for a topic within a scope |
| Redis PUBLISH | channel per recipient (see below) | Fire-and-forget live path (Phase 11 daemon consumes) |

**Installation:** None required.

---

## Architecture Patterns

### System Architecture Diagram

```
em-proj message send --to <sid> <body>
em-proj message broadcast <body> --scope <scope>
em-proj message send --topic <t> <body> --scope <scope>
         |
         v
message/__init__.py  (thin verb shell — D-14)
  resolve_json_mode → die_if_redis_unreachable → call one _ops function → emit_ok
         |
         v
message/_ops.py  (business logic)
  ┌────────────────────────────────────────────────────────┐
  │  send_directed(to_session_id, msg)                     │
  │    ├── validate recipient exists (session HGETALL)     │
  │    ├── mbox_write(to_session_id, msg)          [MBOX]  │
  │    └── publish_live(session channel, payload)   [P/S]  │
  │                                                        │
  │  send_broadcast(scope, msg)                            │
  │    ├── enumerate_scope_recipients(scope)        [SESS] │
  │    │     └── session_list() filter by scope field      │
  │    ├── for each recipient: mbox_write(...)      [MBOX] │
  │    └── for each recipient: publish_live(...)    [P/S]  │
  │                                                        │
  │  send_topic(topic, scope, msg)                         │
  │    ├── get_topic_subscribers(scope, topic)      [SET]  │
  │    ├── intersect with live sessions             [SESS] │
  │    ├── for each subscriber: mbox_write(...)     [MBOX] │
  │    └── for each subscriber: publish_live(...)   [P/S]  │
  │                                                        │
  │  subscribe_topic(session_id, topic, scope)             │
  │    └── SADD topic:<scope_key>:<topic> session_id [SET] │
  │                                                        │
  │  unsubscribe_topic(session_id, topic, scope)           │
  │    └── SREM topic:<scope_key>:<topic> session_id [SET] │
  └────────────────────────────────────────────────────────┘
         |
         v
Redis db=0 (prod) / db=15 (tests)
  mbox:<session_id>       — Redis Stream (Phase 9 — no change)
  state:session:<sid>     — Redis HASH  (Phase 8 — read-only from Phase 10)
  topic:<scope_key>:<t>   — Redis SET   (NEW — Phase 10 topic membership)
  channel: msg:<sid>      — pub/sub     (Phase 10 PUBLISH; Phase 11 SUBSCRIBE)
```

### Recommended Project Structure

```
src/em_proj/message/
  _ops.py           # extended with send_directed, send_broadcast, send_topic,
                    # subscribe_topic, unsubscribe_topic, enumerate_scope_recipients,
                    # get_topic_subscribers (new public functions),
                    # TOPIC_KEY_PREFIX constant
  __init__.py       # extended with send_cmd, broadcast_cmd,
                    # subscribe_cmd, unsubscribe_cmd verbs

tests/unit/
  test_mailbox.py   # UNCHANGED (Phase 9 coverage)
  test_message_send.py  # NEW — unit tests for the four new _ops functions

tests/multiprocess/
  test_mailbox_durability.py  # ACTIVATE the existing skip-stub (MBOX-01 E2E)
  test_message_delivery.py    # NEW — TEST-04 multiprocess harness

tests/structural/
  test_phase_10_shape.py      # NEW — structural invariants for Phase 10
```

---

## Design Decisions (with verified grounding)

### 1. Scope Enumeration: No New Index Required

[VERIFIED: `session/_ops.py::session_list`]

`session_list()` returns a list of `{"session": {9-field dict}, "held": {counts}}` items. Each session record has `project_hash` and `upstream_identity` as plain string fields (coerced by `_hgetall_to_session`). Phase 10's `enumerate_scope_recipients(scope)` is a Python filter over `session_list()`:

```python
# Source: session/_ops.py::session_list return shape + _hgetall_to_session coercions
def enumerate_scope_recipients(scope: str) -> list[str]:
    """Return session_ids for all live sessions in the given scope."""
    sessions = session_list()
    if scope == "machine":
        return [e["session"]["session_id"] for e in sessions]
    if scope == "project":
        my_hash = resolve_project_hash()
        return [
            e["session"]["session_id"] for e in sessions
            if e["session"]["project_hash"] == my_hash
        ]
    if scope == "upstream":
        my_upstream = resolve_upstream_identity()
        return [
            e["session"]["session_id"] for e in sessions
            if e["session"]["upstream_identity"] == my_upstream
        ]
    raise ValidationError(
        code="validation_error",
        message=f"unknown scope: {scope!r}; must be 'project', 'upstream', or 'machine'",
    )
```

**Implications for the planner:**
- No new Redis scanning required for recipient enumeration.
- `session_list()` applies is_holder_stale filtering — broadcast automatically excludes dead sessions.
- Machine-global scope sends to ALL live sessions; this is bounded by the number of registered sessions, not unbounded.
- `session_list()` is already imported from `em_proj.session._ops`; Phase 10 `_ops.py` imports it directly (same pattern as `_scan_all_holders_by_session_id` which is also cross-module).

### 2. Topic Membership Storage

[ASSUMED — no prior topic storage in codebase; Redis SET is the canonical fit]

Topic membership is the ONLY new Redis key type Phase 10 introduces. The pattern:

```
topic:<scope_key>:<topic>  →  Redis SET of session_ids
```

Where `scope_key` is:
- `machine` → the literal string `"machine"` (machine-global)
- `project` → `resolve_project_hash()` value (e.g. `-Users-emonical-projects-em-proj`)
- `upstream` → `resolve_upstream_identity()` value (e.g. `github.com:owner/repo`)

**Key namespace:**
```
TOPIC_KEY_PREFIX: str = "topic:"
# Full key: "topic:{scope_key}:{topic}"
# Example: "topic:-Users-emonical-em-proj:myalerts"
# Example: "topic:machine:broadcast-all"
```

This namespace is disjoint from all existing prefixes: `state:session:`, `state:claim:`, `state:lock:`, `state:reserve:`, `mbox:`. [VERIFIED: `_ops.py` module docstring + session `_ops.py` module docstring]

**TTL for topic keys:** Topic SET keys do not carry a Redis TTL. Subscribers persist until explicitly unsubscribed. Stale session_ids in topic sets are harmless — fan-out attempts `mbox_write` to non-existent sessions, which creates an empty mailbox with XADD then EXPIRE; this is benign (the key will expire in MBOX_TTL_SECONDS). If recipient validation is desired, intersect SMEMBERS with `session_list()` result before writing. [ASSUMED — validated against Phase 9 `mbox_write` behavior: it does not check if session exists before writing, consistent with a pure-write primitive]

### 3. Fan-out Semantics

[VERIFIED: `message/_ops.py::mbox_write` — the write primitive for Phase 10 to call]

Fan-out is a Python loop over `mbox_write()` calls — one XADD+EXPIRE per recipient. No pipeline needed for correctness; redis-py pipelines are an optional optimization.

**Partial failure handling:** If `mbox_write` raises (e.g. Redis connection drops mid-loop), Phase 10 should catch `redis.ConnectionError`/`redis.TimeoutError` and surface partial delivery in the output metadata. The `die_if_redis_unreachable` pre-check at verb entry ensures Redis is up before the loop starts; mid-loop failures should be counted and reported.

**Recommended approach:** capture `(succeeded, failed)` counts in the loop; return `{recipients_written: N, recipients_failed: M, pub_published: P}` in `emit_ok`. [ASSUMED]

**Idempotency:** There is no deduplication. Calling `send` twice writes two messages. This is the correct behavior for a message queue. [ASSUMED]

**MBOX-04 record shape** for each `mbox_write` call: [VERIFIED: `message/_ops.py::mbox_write` docstring]

```python
msg = {
    "from_session": resolve_session_id(),  # the sender
    "pattern": "direct" | "broadcast" | "topic",
    "scope": "project" | "upstream" | "machine",
    "topic": topic_name | None,
    "body": body_text,
    "sent_at": time.time(),
    "ttl": MBOX_TTL_SECONDS,
}
```

### 4. Pub/Sub PUBLISH (fire-and-forget live path)

[ASSUMED — pub/sub channel naming not yet defined; following the convention from REQUIREMENTS.md DAEMON-01]

Phase 10 ships the PUBLISH side; Phase 11 ships the SUBSCRIBE daemon. The pub/sub call must be fire-and-forget: a non-listener session is not an error.

**Channel naming convention:**

```
msg:<session_id>   — directed channel for one session
```

For broadcast and topic sends, Phase 10 publishes to each matched recipient's `msg:<session_id>` channel individually (fan-out). The Phase 11 daemon subscribes to `msg:<session_id>` for its own session. This avoids needing shared broadcast channels (which would require all sessions to know and subscribe to each other's scope channels). [ASSUMED — the per-session channel approach is simpler and directly extensible; Phase 11 only needs to know its own channel name]

```python
# fire-and-forget — ignore return value (subscriber count)
client.publish(f"msg:{session_id}", json.dumps(payload))
```

**Phase 10 vs Phase 11 boundary (critical):**
- Phase 10 MUST: write to `mbox:<session_id>` via `mbox_write` (durable, always)
- Phase 10 MAY: call `client.publish(f"msg:{session_id}", ...)` after the mailbox write (live path, fire-and-forget, no listener in Phase 10 so publish count = 0 for all sends)
- Phase 11 WILL: `SUBSCRIBE msg:<session_id>` in the daemon, drain to mailbox via `mbox_write`

Including PUBLISH now costs one extra Redis round-trip per recipient but future-proofs the channel name convention before Phase 11 is built. The output metadata `pub_published` count will always be 0 in Phase 10 tests (no subscriber), which is correct and expected.

### 5. Delivery Metadata and Exit Codes (MSG-05)

[VERIFIED: `output.py` — existing emit_ok/emit_error/emit_not_found exit code conventions]

Existing exit codes: 0 = success, 1 = error/Redis unreachable, 2 = not_found, 3 = held_by_another.

Phase 10 adds:
- Exit 0: all recipients written successfully (or scope/topic had 0 recipients — still success)
- Exit 1: Redis unreachable (die_if_redis_unreachable fires)
- Exit 2: directed send — recipient session not found (consistent with `emit_not_found` convention)
- Exit 4: partial failure during fan-out (at least 1 write succeeded, at least 1 failed; new code)

Exit 4 is new and does not conflict with any existing code. [ASSUMED — needs planner confirmation]

**Success data payload:**
```json
{
  "recipients_written": 3,
  "recipients_failed": 0,
  "pub_published": 0,
  "pattern": "broadcast",
  "scope": "project"
}
```

**Plain-text output (TTY mode):** `emit_ok` plain rendering falls back to `repr(data)` for nested dicts currently (see `output.py::_render_plain`). For the send verbs, the data dict is shallow enough that a custom plain rendering ("sent to 3 recipients") may be warranted. [ASSUMED]

### 6. Recipient Validation Before Write (directed send)

[VERIFIED: Phase 9 VERIFICATION.md — "recipient-existence validation before write (Phase 9 mbox_write is a pure write primitive)" was explicitly DEFERRED to Phase 10]

For `send --to <session_id>`, Phase 10 should validate the recipient is a live session before writing. The check:

```python
from em_proj.session._ops import session_show, SessionNotFound
try:
    session_show(recipient_id)  # raises SessionNotFound if stale/absent
except SessionNotFound:
    raise  # verb layer catches and emits emit_not_found
```

This is the correct approach: `session_show` applies `is_holder_stale` and `client.delete` for stale sessions (D3 reaping), so a directed send to a dead session returns exit 2 rather than silently writing to an orphaned mailbox. [VERIFIED: `session/_ops.py::session_show` implementation]

For broadcast and topic sends, skip per-recipient validation — enumerate live sessions via `session_list()` (which already filters stale) and write to all of them.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Session listing | Custom `client.scan_iter("state:session:*")` | `session_list()` from `em_proj.session._ops` | Already filters stale, already applies is_holder_stale, already enriched |
| Scope key derivation for project scope | Inline `os.getcwd().replace("/", "-")` | `resolve_project_hash()` from `em_proj.identity` | Single canonical source; already imported in session ops |
| Scope key derivation for upstream scope | Inline git subprocess | `resolve_upstream_identity()` from `em_proj.identity` | Already handles timeout, fallback, canonicalization |
| Redis connection + ping guard | Inline `client.ping()` | `die_if_redis_unreachable(client)` from `em_proj.redis_client` | Locked error message format (D-17); consistent UX |
| Body length validation | Inline `len(body) > N` check | Already in `mbox_write` via `_validate_body` | `mbox_write` raises `ValidationError` — no pre-check needed |
| Session ID resolution | Inline `os.environ.get("CLAUDE_CODE_SESSION_ID")` | `resolve_session_id()` from `em_proj.identity` | Canonical fallback chain |

---

## Common Pitfalls

### Pitfall 1: Writing to sender's own mailbox during broadcast/topic
**What goes wrong:** `session_list()` returns all live sessions including the sender; fan-out loop calls `mbox_write(sender_id, msg)` for the sender.
**Why it happens:** No exclusion logic in the enumeration helper.
**How to avoid:** In `enumerate_scope_recipients()`, exclude `resolve_session_id()` from the returned list. Document this as a deliberate UX choice (you don't receive your own broadcast).
**Warning signs:** Test shows sender's inbox contains a copy of their own broadcast.

### Pitfall 2: Topic fan-out to unregistered/dead sessions in the SET
**What goes wrong:** SMEMBERS returns session_ids that are no longer in the registry (session expired/dead); `mbox_write` writes to orphaned `mbox:` keys.
**Why it happens:** Topic SET is not TTL-bounded; entries survive the session.
**How to avoid:** In `send_topic`, intersect SMEMBERS result with live session_ids from `session_list()`. This is O(N) in number of subscribers but N is bounded by live session count.
**Warning signs:** `mbox:<stale_session_id>` stream keys accumulate with no corresponding `state:session:` entry.

### Pitfall 3: subscribe uses sender's scope key, not the subscriber's
**What goes wrong:** `subscribe <topic> --scope project` records the subscriber under the sender's `project_hash`. If subscribed from a different project directory, the scope key is wrong.
**Why it happens:** `resolve_project_hash()` returns the CALLING process's project hash.
**How to avoid:** For `subscribe`/`unsubscribe`, the scope key is always derived from the calling process's identity (it's the subscriber's own scope). This is correct — a subscriber joins topic `<their_scope>:<topic>`. The `--scope` option on `subscribe` should control which scope they're joining as a member of. Document clearly.
**Warning signs:** Subscribers not receiving messages from correct scope.

### Pitfall 4: Redis pipeline vs loop for fan-out
**What goes wrong:** Using `client.pipeline()` reduces round-trips but makes per-recipient error detection harder (pipeline errors are returned as a list, not raised per command).
**Why it happens:** Pipeline is an attractive optimization.
**How to avoid:** Use a plain loop for Phase 10. Optimize with pipeline only if benchmarks show it matters. The session count on a single machine is small (<100 realistically).
**Warning signs:** Partial failures silently swallowed.

### Pitfall 5: `emit_ok` plain-text rendering for nested dicts
**What goes wrong:** `_render_plain` in `output.py` falls back to `repr(data)` for dicts with nested values (e.g. if data contains a list).
**Why it happens:** `_render_plain` special-cases only flat dicts and `{"keys": [...]}`.
**How to avoid:** Keep the `emit_ok` data payload flat (all scalar values). The delivery metadata `{recipients_written: N, recipients_failed: M, pub_published: P, pattern: str, scope: str}` is all scalars and will render correctly as `key: value` lines in TTY mode.
**Warning signs:** `send` TTY output shows `repr(...)` instead of clean key: value lines.

### Pitfall 6: `die_if_redis_unreachable` vs mid-loop ConnectionError
**What goes wrong:** Redis becomes unreachable AFTER the pre-check during a large fan-out loop. Unhandled `redis.ConnectionError` propagates as traceback.
**Why it happens:** Pre-check proves Redis reachable at verb entry but not mid-loop.
**How to avoid:** Wrap the fan-out loop in `try/except (redis.ConnectionError, redis.TimeoutError)` and track partial failures.
**Warning signs:** Traceback visible in CLI output during broadcast to many recipients.

---

## Code Examples

### Pattern 1: D-14 thin verb shell (mirror of inbox_cmd)
```python
# Source: message/__init__.py::inbox_cmd (verified pattern)
@message_app.command("send")
def send_cmd(
    to: Annotated[str | None, typer.Option("--to", help="Recipient session ID.")] = None,
    topic: Annotated[str | None, typer.Option("--topic", help="Topic name.")] = None,
    scope: Annotated[str, typer.Option("--scope", help="Scope: project|upstream|machine")] = "machine",
    body: Annotated[str, typer.Argument(help="Message body.")] = ...,
    json_flag: Annotated[bool | None, typer.Option("--json/--no-json", help=_JSON_HELP)] = None,
) -> None:
    json_mode = resolve_json_mode(json_flag)
    client = get_client()
    die_if_redis_unreachable(client)
    try:
        result = send_directed(to, body, scope)  # or send_topic(...)
    except ValidationError as e:
        emit_error(e.code, e.message, json_mode=json_mode)
    except SessionNotFound as e:
        emit_not_found(str(e), json_mode=json_mode)
    emit_ok(data=result, json_mode=json_mode)
```

### Pattern 2: mbox_write call (verified signature)
```python
# Source: message/_ops.py::mbox_write — session_id, msg dict
msg_id = mbox_write(
    session_id=recipient_session_id,
    msg={
        "from_session": resolve_session_id(),
        "pattern": "direct",   # or "broadcast" or "topic"
        "scope": scope,
        "topic": None,          # or topic_name
        "body": body,
        "sent_at": time.time(),
        "ttl": MBOX_TTL_SECONDS,
    }
)
```

### Pattern 3: topic SET operations
```python
# Source: [ASSUMED — follows redis-py SET API]
TOPIC_KEY_PREFIX = "topic:"

def _build_topic_key(scope_key: str, topic: str) -> str:
    return f"{TOPIC_KEY_PREFIX}{scope_key}:{topic}"

def subscribe_topic(session_id: str, topic: str, scope: str) -> None:
    scope_key = _resolve_scope_key(scope)
    key = _build_topic_key(scope_key, topic)
    client = get_client()
    client.sadd(key, session_id)

def get_topic_subscribers(scope: str, topic: str) -> set:
    scope_key = _resolve_scope_key(scope)
    key = _build_topic_key(scope_key, topic)
    client = get_client()
    return client.smembers(key)  # returns set of str (decode_responses=True)
```

### Pattern 4: multiprocess test (verified harness pattern)
```python
# Source: tests/multiprocess/test_session_registry.py::_session_list_via_cli pattern
def _send_via_cli(to: str, body: str, sender_session_id: str) -> subprocess.CompletedProcess:
    child_env = {
        **os.environ,
        "CLAUDE_CODE_SESSION_ID": sender_session_id,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
    }
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "message", "send", "--to", to, "--json", body],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    stdout, stderr = proc.communicate(timeout=15)
    return proc, stdout, stderr
```

---

## Runtime State Inventory

Not applicable — Phase 10 is a greenfield feature addition (new verbs, new topic SET keys). No rename or migration required.

---

## TEST-04 Harness Design

### What is testable in Phase 10 (mailbox path only)

All 3 patterns × 3 scopes can be verified via the mailbox path without the Phase 11 daemon:

| Pattern | Scope | Test approach |
|---------|-------|---------------|
| directed | project | Register 2 sessions with same project_hash; send --to; read inbox |
| directed | upstream | Register 2 sessions with same upstream_identity; send --to; read inbox |
| directed | machine | Register 2 sessions; send --to; scope is irrelevant for directed |
| broadcast | project | Register 3 sessions (2 in project, 1 out); broadcast --scope project; both in-project get message |
| broadcast | upstream | Register 3 sessions (2 with same upstream, 1 without); broadcast --scope upstream |
| broadcast | machine | Register 2 sessions; broadcast --scope machine; both get message |
| topic | project | Session A subscribes; Session B sends --topic X --scope project; A's inbox has message |
| topic | upstream | Same with upstream scope |
| topic | machine | Same with machine scope |

**9 cells, all testable via mailbox reads.** The `pub_published` count will be 0 (no daemon listening) but `recipients_written` will be the correct N.

### Live path (Phase 11 daemon) — skip-stub in Phase 10

The live-delivery proof (MSG-01's "live via pub/sub if listening") requires Phase 11's daemon. Structure the test as:

```python
def test_live_delivery_directed(clean_db, redis_precheck) -> None:
    pytest.skip(
        "Phase 11 listener daemon not yet available — "
        "enable once 'em-proj session listen' ships"
    )
```

This mirrors the exact pattern from `test_mailbox_durability.py`. [VERIFIED: `tests/multiprocess/test_mailbox_durability.py`]

### Activate the existing MBOX-01 skip-stub

`tests/multiprocess/test_mailbox_durability.py::test_mailbox_persists_for_offline_session` is a skip-stub waiting for Phase 10. When `message send` ships, this test can be activated by replacing the `pytest.skip()` with the actual test body described in its docstring. [VERIFIED: docstring in `test_mailbox_durability.py` lines 22-35]

### Harness infrastructure to reuse

[VERIFIED: `tests/conftest.py` and `tests/multiprocess/test_session_registry.py`]

- `clean_db` fixture: `FLUSHDB` on db=15 before/after each test — reuse as-is
- `redis_precheck` fixture: skip if Redis down or `em-proj` not on PATH — reuse as-is
- `_register_session_for_test(session_id, client)` helper from `test_session_registry.py` — copy or import for TEST-04 (registers a session with the test runner's live pid so `is_holder_stale` returns False)
- `EM_PROJ_BIN`, `TEST_DB` constants from `tests.conftest`
- `subprocess.Popen` + `.communicate(timeout=15)` pattern — required (macOS fork+exec safety)

**For scope testing:** `_register_session_for_test` writes the calling process's `project_hash` and `upstream_identity`. To test cross-scope scenarios, inject different `project_hash` values directly via Redis (same test-infrastructure pattern as TTL override in Point 4 of TEST-03). The `state:session:` HASH fields are directly writable in the test harness.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Topic SET key shape: `topic:<scope_key>:<topic>` | Design Decision 2 | Minor — only affects key naming; rename before first use |
| A2 | Pub/sub channel per recipient: `msg:<session_id>` | Design Decision 4 | Medium — Phase 11 daemon must use same channel name; coordinate before Phase 11 |
| A3 | Exit code 4 for partial fan-out failure | Design Decision 5 | Low — exit code is internal convention; adjust if conflicts arise |
| A4 | Topic SET keys have no TTL (persist until unsubscribed) | Design Decision 2 | Low — stale entries are handled by intersecting with live sessions |
| A5 | `send` does not deliver to the sender (broadcast/topic self-exclusion) | Common Pitfall 1 | Low — UX choice; document explicitly |
| A6 | PUBLISH is included in Phase 10 as fire-and-forget | Design Decision 4 | Low — could defer entirely to Phase 11; either works |

---

## Open Questions

1. **pub_published metadata accuracy**
   - What we know: `client.publish(channel, payload)` returns the number of subscribers who received the message; in Phase 10 this is always 0.
   - What's unclear: Should `pub_published` in output reflect actual live subscribers (always 0 in Phase 10), or should it be omitted until Phase 11?
   - Recommendation: Include `pub_published: 0` now so the field is present and parseable when Phase 11 listeners add subscribers — no schema change needed.

2. **`send` verb argument shape: `--to` + `--topic` mutual exclusion**
   - What we know: MSG-01 is `send --to <sid>` (directed); MSG-03 is `send --topic <t> --scope <s>` (topic).
   - What's unclear: Should these be one `send` verb with mutually exclusive options, or two verbs (`send --to` and `send --topic`)?
   - Recommendation: One `send` verb, enforce mutual exclusion in the verb shell (`if to and topic: emit_error(...)`). Typer does not natively support mutual exclusion but a simple `if` check suffices.

3. **`broadcast` as a separate verb or `send --broadcast`**
   - What we know: REQUIREMENTS.md and ROADMAP.md phrase it as `em-proj message broadcast <body> --scope <...>` (a distinct verb), not `send --broadcast`.
   - Recommendation: Implement as a separate `broadcast` verb for clarity and discoverability — `message broadcast <body> --scope project`.

4. **subscribe/unsubscribe scope: should it default to project or machine?**
   - What we know: MSG-03 says scope is selectable per message; subscribe/unsubscribe manage membership.
   - Recommendation: `subscribe <topic> --scope machine` (machine-global default, consistent with other scope defaults). User can narrow to project/upstream.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Redis | All send ops, topic SET | Checked by `redis_precheck` fixture | 7+ | Tests skip via `redis_precheck` |
| `em-proj` on PATH | CLI multiprocess tests | Checked by `redis_precheck` fixture | 0.1.0 | Tests skip |
| redis-py | `client.sadd`/`smembers`/`publish` | Already installed (Phase 9) | 7.4.0 | — |

`client.sadd`, `client.smembers`, `client.srem`, `client.publish` are all standard redis-py commands present in 7.4.0. [VERIFIED: redis-py 7.x covers all Redis 6.2+ commands; XADD already confirmed working in Phase 9]

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (project-locked) |
| Config file | `pyproject.toml` (or pytest.ini — check project root) |
| Quick run command | `scripts/test.sh unit` |
| Full suite command | `scripts/test.sh all` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MSG-01 | directed send writes to recipient mailbox | integration (multiprocess) | `scripts/test.sh multiprocess -k directed` | No — Wave 0 |
| MSG-02 | broadcast delivers to all scope-matched sessions | integration (multiprocess) | `scripts/test.sh multiprocess -k broadcast` | No — Wave 0 |
| MSG-03 | subscribe/unsubscribe and topic send routing | integration (multiprocess) | `scripts/test.sh multiprocess -k topic` | No — Wave 0 |
| MSG-04 | scope selection per message (project/upstream/machine) | integration (multiprocess) | `scripts/test.sh multiprocess -k scope` | No — Wave 0 |
| MSG-05 | parseable delivery metadata + exit codes | unit | `scripts/test.sh unit -k test_message_send` | No — Wave 0 |
| MBOX-01 | durability: offline recipient gets message after send | integration (multiprocess) | `scripts/test.sh multiprocess -k durability` | Yes (skip-stub) — activate |
| TEST-04 | 3 patterns × 3 scopes delivery matrix | integration (multiprocess) | `scripts/test.sh multiprocess -k delivery` | No — Wave 0 |

### Sampling Rate
- **Per task commit:** `scripts/test.sh unit`
- **Per wave merge:** `scripts/test.sh all`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/unit/test_message_send.py` — covers MSG-05 (send_directed, send_broadcast, send_topic unit tests with mocked session_list and mbox_write)
- [ ] `tests/multiprocess/test_message_delivery.py` — covers TEST-04 (3×3 matrix, mailbox path only; live path skip-stubbed)
- [ ] `tests/structural/test_phase_10_shape.py` — structural invariants (TOPIC_KEY_PREFIX constant, prohibited imports, message_app has send/broadcast/subscribe commands, no pipeline import)
- [ ] Activate `tests/multiprocess/test_mailbox_durability.py` — replace `pytest.skip()` with actual test body (MBOX-01 E2E)

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | — |
| V3 Session Management | no | — |
| V4 Access Control | limited | session_show validates recipient exists; no cross-session read access |
| V5 Input Validation | yes | `_validate_body` in `mbox_write` enforces MAX_BODY_CHARS; scope/topic validated via explicit allowlist |
| V6 Cryptography | no | — |

### Threat Patterns for This Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Broadcast to all sessions (machine scope) | Information disclosure | Only live sessions receive; no access to their content; sender controls their own message body |
| Topic name as injection vector | Tampering | Redis key constructed from topic string; validate topic to `[a-zA-Z0-9_.-]+` before use as key component |
| Sending to stale/dead session_id | Spoofing | `session_show()` validation for directed sends; broadcast uses `session_list()` which filters stale |
| Body length abuse | DoS | `_validate_body` enforces `MAX_BODY_CHARS = 4096`; already in `mbox_write` [VERIFIED] |
| Cross-project info leak via broadcast | Information disclosure | Scope filtering is correct-by-design: project scope only reaches sessions in the same `project_hash`; no cross-project leakage |

**Additional recommendation:** Add a `_validate_topic(topic: str) -> None` function in `_ops.py` that enforces topic name to `[a-zA-Z0-9_.-]+` (max 128 chars). Mirrors `_validate_body` and `_validate_since` patterns. Prevents Redis key injection via topic name. [ASSUMED]

---

## Sources

### Primary (HIGH confidence)
- `src/em_proj/message/_ops.py` — `mbox_write`, `mailbox_inbox`, `mbox_blocking_read`, `MBOX_KEY_PREFIX`, `MBOX_TTL_SECONDS`, `MAX_BODY_CHARS`, `MailboxError`
- `src/em_proj/message/__init__.py` — `message_app`, `inbox_cmd` (D-14 verb shell pattern to mirror)
- `src/em_proj/session/_ops.py` — `session_list`, `session_show`, `KEY_PREFIX`, `_hgetall_to_session`, `SessionNotFound`
- `src/em_proj/identity.py` — `resolve_session_id`, `resolve_project_hash`, `resolve_upstream_identity`
- `src/em_proj/output.py` — `emit_ok`, `emit_error`, `emit_not_found`, exit code conventions
- `src/em_proj/redis_client.py` — `die_if_redis_unreachable`, `get_client`, `EM_PROJ_REDIS_DB`
- `tests/conftest.py` — `clean_db`, `redis_precheck`, `multiproc_race`, `EM_PROJ_BIN`, `TEST_DB`
- `tests/multiprocess/test_session_registry.py` — `_register_session_for_test`, harness patterns
- `tests/multiprocess/test_mailbox_durability.py` — existing skip-stub to activate
- `.planning/phases/09-durable-mailbox-transport/09-VERIFICATION.md` — Phase 9 delivered primitives, explicit Phase 10 deferred items

### Secondary (MEDIUM confidence)
- `.planning/REQUIREMENTS.md` — MSG-01..05, TEST-04 requirement text
- `.planning/ROADMAP.md` — Phase 10 success criteria
- `.planning/STATE.md` — locked decisions (v1.1 message scope, patterns)

### Tertiary (LOW confidence — ASSUMED claims)
- Topic SET key naming convention (`topic:<scope_key>:<topic>`) — no prior topic implementation to verify against; follows existing prefix patterns
- Pub/sub channel naming (`msg:<session_id>`) — no prior pub/sub implementation; must be confirmed before Phase 11 is designed

---

## Metadata

**Confidence breakdown:**
- Standard Stack: HIGH — no new dependencies; all libraries verified in Phase 8/9
- Architecture (mailbox fan-out, scope enumeration): HIGH — grounded in actual Phase 8/9 source
- Topic storage + pub/sub channel naming: MEDIUM — follows clear patterns but no prior implementation to verify
- Exit code 4: LOW — new exit code, needs confirmation to avoid conflicts

**Research date:** 2026-06-07
**Valid until:** 2026-07-07 (stable stack; no external service dependencies)
