# Phase 10: Messaging Send Patterns — Pattern Map

**Mapped:** 2026-06-07
**Files analyzed:** 6 (2 modified, 4 created/activated)
**Analogs found:** 6 / 6

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `src/em_proj/message/_ops.py` (MODIFY) | service/ops | CRUD + pub-sub | `src/em_proj/message/_ops.py` itself (Phase 9 base) | exact — extend in place |
| `src/em_proj/message/__init__.py` (MODIFY) | CLI mount | request-response | `src/em_proj/message/__init__.py` itself (`inbox_cmd`) | exact — extend in place |
| `tests/unit/test_message_send.py` (CREATE) | test | CRUD | `tests/unit/test_mailbox.py` | exact — same module, same test style |
| `tests/multiprocess/test_message_delivery.py` (CREATE) | test | request-response | `tests/multiprocess/test_session_registry.py` | exact — same harness, same Popen pattern |
| `tests/structural/test_phase_10_shape.py` (CREATE) | test | transform | `tests/structural/test_phase_09_shape.py` | exact — same structural pattern |
| `tests/multiprocess/test_mailbox_durability.py` (ACTIVATE) | test | request-response | itself (skip-stub → real body) | self-activation — docstring specifies the body |

---

## Pattern Assignments

### `src/em_proj/message/_ops.py` (MODIFY — extend with send/subscribe ops)

**Analog:** `src/em_proj/message/_ops.py` (existing Phase 9 source, lines 1–316)

**Module docstring additions** — append to the existing docstring block (lines 1–36):
```python
# New in Phase 10:
#   from_session  — injected by send_directed/send_broadcast/send_topic as
#                   resolve_session_id() at call time (sender's identity)
#   pattern       — 'direct' | 'broadcast' | 'topic'
#   scope         — 'project' | 'upstream' | 'machine'
#   topic         — topic string or None

# Prohibited imports (Phase 10 additions, enforced by test_phase_10_shape.py):
#   pipeline (redis.client.Pipeline) — use plain loop per 10-RESEARCH Pitfall 4
```

**New constant — follow MBOX_KEY_PREFIX pattern** (lines 57–69 show the shape):
```python
# After MBOX_TTL_SECONDS on line 65:
#: Key prefix for topic membership sets. Full key: "topic:<scope_key>:<topic>".
#: Distinct from state:*, mbox: namespaces (no collision).
TOPIC_KEY_PREFIX: str = "topic:"
```

**New imports to add** (after line 42 `from em_proj.redis_client import get_client`):
```python
import json  # already present (line 39)
import time  # already present (line 41)

from em_proj.identity import resolve_project_hash, resolve_session_id, resolve_upstream_identity
from em_proj.session._ops import SessionNotFound, session_list, session_show
from em_proj.state.kv import ValidationError  # already present (line 44)
```

**_validate_body pattern** (lines 124–134) — `_validate_topic` mirrors this exactly:
```python
def _validate_body(body: str) -> None:
    """Raise ValidationError if body exceeds MAX_BODY_CHARS."""
    if len(body) > MAX_BODY_CHARS:
        raise ValidationError(
            code="validation_error",
            message=f"body exceeds {MAX_BODY_CHARS} characters",
        )
```
Copy this pattern for `_validate_topic`:
```python
_TOPIC_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,128}$")  # re already imported (line 40)

def _validate_topic(topic: str) -> None:
    """Raise ValidationError if topic is not a valid topic name."""
    if not _TOPIC_RE.fullmatch(topic):
        raise ValidationError(
            code="validation_error",
            message="invalid topic: must match [a-zA-Z0-9_.-]{1..128}",
        )
```

**_build_mbox_key pattern** (lines 92–98) — `_build_topic_key` mirrors this:
```python
def _build_mbox_key(session_id: str) -> str:
    return MBOX_KEY_PREFIX + session_id
```
Copy this pattern for `_build_topic_key` and `_resolve_scope_key`:
```python
def _build_topic_key(scope_key: str, topic: str) -> str:
    return f"{TOPIC_KEY_PREFIX}{scope_key}:{topic}"

def _resolve_scope_key(scope: str) -> str:
    if scope == "machine":
        return "machine"
    if scope == "project":
        return resolve_project_hash()
    if scope == "upstream":
        return resolve_upstream_identity()
    raise ValidationError(
        code="validation_error",
        message=f"unknown scope: {scope!r}; must be 'project', 'upstream', or 'machine'",
    )
```

**mbox_write call pattern** (lines 159–210) — send_directed/broadcast/topic call it:
```python
# Canonical msg dict shape (from mbox_write docstring, lines 172–183):
msg_id = mbox_write(
    session_id=recipient_id,
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
# mbox_write raises ValidationError if body exceeds MAX_BODY_CHARS.
# mbox_write does NOT raise on missing recipient — pure write primitive.
```

**session_show recipient validation pattern** (lines 530–573 in session/_ops.py):
```python
# For directed send — validate recipient exists before writing:
# session_show raises SessionNotFound if absent or stale (D3 reaping fires).
try:
    session_show(recipient_id)
except SessionNotFound:
    raise  # re-raise; verb layer catches and calls emit_not_found
```

**session_list filter pattern** (lines 475–527 in session/_ops.py):
```python
# session_list() returns: [{"session": {9-field dict}, "held": {counts}}]
# Fields for scope filter: e["session"]["project_hash"], e["session"]["upstream_identity"]
# is_holder_stale filtering is applied internally — only live sessions returned.
sessions = session_list()
my_project = resolve_project_hash()
recipients = [
    e["session"]["session_id"]
    for e in sessions
    if e["session"]["project_hash"] == my_project
    and e["session"]["session_id"] != resolve_session_id()  # exclude sender
]
```

**Error handling pattern** (ValidationError from lines 131–134, SessionNotFound from session/_ops.py lines 151–167):
```python
# Both exceptions bubble to the verb shell — ops layer re-raises, never emits.
# Verb shell catches and calls emit_error (ValidationError) or emit_not_found (SessionNotFound).
```

**Fan-out loop with partial failure tracking** (no direct analog — follows RESEARCH Design Decision 3):
```python
succeeded = 0
failed = 0
for session_id in recipients:
    try:
        mbox_write(session_id, msg)
        client.publish(f"msg:{session_id}", json.dumps(msg))
        succeeded += 1
    except (redis.ConnectionError, redis.TimeoutError):
        failed += 1
```

---

### `src/em_proj/message/__init__.py` (MODIFY — add send/broadcast/subscribe/unsubscribe verbs)

**Analog:** `src/em_proj/message/__init__.py` — `inbox_cmd` (lines 63–95)

**Full inbox_cmd pattern** (lines 63–95) — every new verb follows this exactly:
```python
@message_app.command("inbox")
def inbox_cmd(
    peek: Annotated[
        bool,
        typer.Option("--peek", help="Read without consuming messages."),
    ] = False,
    since: Annotated[
        str | None,
        typer.Option("--since", help="Resume from this message ID (exclusive)."),
    ] = None,
    json_flag: Annotated[
        bool | None,
        typer.Option("--json/--no-json", help=_JSON_HELP),
    ] = None,
) -> None:
    """..."""
    json_mode = resolve_json_mode(json_flag)   # Step 1: resolve mode
    client = get_client()
    die_if_redis_unreachable(client)           # Step 2: guard
    session_id = resolve_session_id()
    try:
        messages = mailbox_inbox(session_id, since=since, peek=peek)
    except ValidationError as e:
        emit_error(e.code, e.message, json_mode=json_mode)  # Step 3: one ops call + emit
    emit_ok(data=messages, json_mode=json_mode)
```

**D-14 three-step template for new verbs** — concrete expansion for `send_cmd`:
```python
@message_app.command("send")
def send_cmd(
    to: Annotated[str | None, typer.Option("--to", help="Recipient session ID.")] = None,
    topic: Annotated[str | None, typer.Option("--topic", help="Topic name.")] = None,
    scope: Annotated[str, typer.Option("--scope", help="Scope: project|upstream|machine")] = "machine",
    body: Annotated[str, typer.Argument(help="Message body.")],
    json_flag: Annotated[bool | None, typer.Option("--json/--no-json", help=_JSON_HELP)] = None,
) -> None:
    json_mode = resolve_json_mode(json_flag)       # Step 1
    client = get_client()
    die_if_redis_unreachable(client)               # Step 2
    try:
        if to and topic:
            emit_error("validation_error", "--to and --topic are mutually exclusive", json_mode=json_mode)
        if to:
            result = send_directed(to, body, scope)
        elif topic:
            result = send_topic(topic, scope, body)
        else:
            emit_error("validation_error", "must provide --to <session_id> or --topic <name>", json_mode=json_mode)
    except ValidationError as e:
        emit_error(e.code, e.message, json_mode=json_mode)
    except SessionNotFound as e:
        emit_not_found(str(e), json_mode=json_mode)
    emit_ok(data=result, json_mode=json_mode)      # Step 3
```

**Import block additions** — extend the existing import block (lines 21–33):
```python
# Add alongside existing ops imports:
from em_proj.message._ops import (
    # ... existing imports ...
    TOPIC_KEY_PREFIX,           # new Phase 10 constant
    send_directed,              # new Phase 10 ops
    send_broadcast,
    send_topic,
    subscribe_topic,
    unsubscribe_topic,
)
from em_proj.output import emit_error, emit_not_found, emit_ok, resolve_json_mode  # emit_not_found is new
from em_proj.session._ops import SessionNotFound  # new Phase 10 import
```

**__all__ additions** — extend lines 35–46:
```python
# Add the new names that callers may import from em_proj.message:
"send_directed", "send_broadcast", "send_topic",
"subscribe_topic", "unsubscribe_topic", "TOPIC_KEY_PREFIX",
```

---

### `tests/unit/test_message_send.py` (CREATE)

**Analog:** `tests/unit/test_mailbox.py` (lines 1–282)

**Module header + autouse fixtures** (lines 1–45) — copy verbatim, update doc:
```python
"""Unit tests for Phase 10 send/subscribe ops in em_proj.message._ops."""
from __future__ import annotations

import time
import pytest
import em_proj.redis_client as rc
from em_proj.message._ops import (
    TOPIC_KEY_PREFIX,
    send_directed, send_broadcast, send_topic,
    subscribe_topic, unsubscribe_topic,
    enumerate_scope_recipients,
)
from em_proj.session._ops import SessionNotFound
from em_proj.state.kv import ValidationError

SESSION_ID = "test-sender-10"
RECIPIENT_ID = "test-recipient-10"

@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    """Reset the redis_client singleton so each test honors EM_PROJ_REDIS_DB=15."""
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()

@pytest.fixture(autouse=True)
def _point_session_at_test_db(monkeypatch):
    """Force _ops's get_client() onto db=15."""
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")
```

**Helper pattern** (lines 50–67 from test_mailbox.py) — `_make_msg` is already a valid helper; tests can import or re-define it. The unit tests should mock `session_list` and `mbox_write` using `monkeypatch` to stay fast:
```python
# Mock pattern for ops-layer unit tests (no live Redis required for logic tests):
def test_send_directed_calls_mbox_write(monkeypatch, clean_db):
    """send_directed must call mbox_write with correct pattern='direct' field."""
    # Use live db=15 (clean_db flushes it), not a mock — mirrors test_mailbox.py style.
    # Register recipient directly in Redis first, then call send_directed.
    ...
```

**ValidationError pattern** (lines 254–258 from test_mailbox.py):
```python
def test_invalid_topic_raises_validation_error(clean_db) -> None:
    with pytest.raises(ValidationError):
        subscribe_topic(SESSION_ID, "invalid topic!", "machine")
```

**Parametrize pattern** (lines 266–272 from test_mailbox.py):
```python
@pytest.mark.parametrize("bad_scope", ["global", "", "ALL", "Project"])
def test_invalid_scope_raises_validation_error(clean_db, bad_scope) -> None:
    with pytest.raises(ValidationError):
        send_broadcast("hello", bad_scope)
```

---

### `tests/multiprocess/test_message_delivery.py` (CREATE)

**Analog:** `tests/multiprocess/test_session_registry.py` (lines 1–449)

**Module header + imports** (lines 1–52) — copy pattern, update doc:
```python
"""Multi-process harness for `em-proj message` — TEST-04 3×3 delivery matrix.

Phase 1 design invariants carried forward:
  - subprocess.Popen NOT multiprocessing.Process (macOS fork+exec safety)
  - .communicate(timeout=15) NOT .wait() (pipe-buffer deadlock prevention)
  - EM_PROJ_REDIS_DB=15 in every child env (never writes to prod db=0)
  - Distinct session_id per test
"""
from __future__ import annotations

import json
import os
import subprocess
import time

import pytest
import redis as redis_module

from tests.conftest import EM_PROJ_BIN, TEST_DB
```

**`_register_session_for_test` helper** (lines 142–189 from test_session_registry.py) — copy verbatim. This is the key infrastructure for scope-testing:
```python
def _register_session_for_test(session_id: str, client: redis_module.Redis) -> dict:
    """Write a test session record to Redis db=15 with the test runner's live pid."""
    import em_proj.session._ops as ops
    from em_proj.identity import current_process_composite, resolve_upstream_identity
    composite = current_process_composite()
    upstream_identity = resolve_upstream_identity()
    cwd = os.getcwd()
    now = time.time()
    redis_key = ops.KEY_PREFIX + session_id
    client.hset(redis_key, mapping={
        "session_id": session_id,
        "project_hash": composite["project_hash"],
        "upstream_identity": upstream_identity,
        "pid": str(composite["pid"]),
        "proc_start_epoch": str(composite["proc_start_epoch"]),
        "boot_id": composite["boot_id"],
        "cwd": cwd,
        "registered_at": str(now),
        "last_heartbeat": str(now),
    })
    client.expire(redis_key, ops.TTL_DEFAULT)
    return {...}
```

**Scope override for cross-scope tests** (pattern from RESEARCH "For scope testing" note):
```python
# Inject a different project_hash to create a session in a different "project":
import em_proj.session._ops as ops
redis_key = ops.KEY_PREFIX + other_session_id
client.hset(redis_key, "project_hash", "different-project-hash")
```

**CLI subprocess helper pattern** (lines 59–92 from test_session_registry.py) — new `_send_via_cli` follows this:
```python
def _send_via_cli(
    args: list[str],
    sender_session_id: str,
) -> tuple[subprocess.Popen, str, str]:
    """Run `em-proj message <args>` via subprocess.Popen + .communicate(timeout=15)."""
    child_env = {
        **os.environ,
        "CLAUDE_CODE_SESSION_ID": sender_session_id,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
    }
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "message"] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    stdout, stderr = proc.communicate(timeout=15)
    return proc, stdout, stderr
```

**_inbox_via_cli** (reads the recipient's inbox to verify delivery):
```python
def _inbox_via_cli(session_id: str) -> list:
    """Run `em-proj message inbox --json` for session_id and return messages list."""
    child_env = {
        **os.environ,
        "CLAUDE_CODE_SESSION_ID": session_id,
        "EM_PROJ_REDIS_DB": str(TEST_DB),
    }
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "message", "inbox", "--json", "--peek"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=child_env,
    )
    stdout, _ = proc.communicate(timeout=15)
    envelope = json.loads(stdout)
    return envelope.get("data", [])
```

**Phase 11 live-path skip-stub pattern** (lines 33–36 from test_mailbox_durability.py):
```python
def test_live_delivery_directed(clean_db, redis_precheck) -> None:
    pytest.skip(
        "Phase 11 listener daemon not yet available — "
        "enable once 'em-proj session listen' ships"
    )
```

**Test naming convention** (mirrors test_session_registry.py test function names):
```python
# Use descriptive names that encode the 3×3 matrix cell:
def test_directed_send_project_scope(clean_db, redis_precheck) -> None: ...
def test_broadcast_project_scope(clean_db, redis_precheck) -> None: ...
def test_topic_send_machine_scope(clean_db, redis_precheck) -> None: ...
```

**Unique session ID helper** (lines 133–139 from test_session_registry.py) — copy verbatim:
```python
def _unique_session_id() -> str:
    return f"test-sess-{os.getpid()}-{time.time_ns()}"
```

---

### `tests/structural/test_phase_10_shape.py` (CREATE)

**Analog:** `tests/structural/test_phase_09_shape.py` (lines 1–277)

**Module header + path constants** (lines 1–28) — copy pattern, update paths:
```python
from __future__ import annotations
"""Phase 10 structural invariants."""

from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "10-messaging-send-patterns"

MESSAGE_OPS = REPO_ROOT / "src" / "em_proj" / "message" / "_ops.py"
MESSAGE_INIT = REPO_ROOT / "src" / "em_proj" / "message" / "__init__.py"
```

**Test A — file presence + required function names** (lines 36–64) — mirror for Phase 10:
```python
def test_message_ops_has_phase10_functions() -> None:
    src = MESSAGE_OPS.read_text()
    for fn in ("send_directed", "send_broadcast", "send_topic",
               "subscribe_topic", "unsubscribe_topic", "enumerate_scope_recipients",
               "_validate_topic"):
        assert f"def {fn}" in src, f"{fn} missing from message/_ops.py"
```

**Test B — TOPIC_KEY_PREFIX constant** (mirrors test_mbox_key_prefix_is_mbox_colon, lines 85–101):
```python
def test_topic_key_prefix_is_topic_colon() -> None:
    src = MESSAGE_OPS.read_text()
    assert "TOPIC_KEY_PREFIX" in src
    assert '"topic:"' in src or "'topic:'" in src
```

**Test C — prohibited imports in _ops.py** (lines 109–136) — copy with Phase 10 additions:
```python
def test_message_ops_prohibits_forbidden_imports() -> None:
    src = MESSAGE_OPS.read_text()
    import_lines = [
        line.strip() for line in src.splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]
    for forbidden in ("typer", "multiprocessing", "threading"):
        for line in import_lines:
            assert forbidden not in line, f"Forbidden import {forbidden!r}: {line!r}"
```

**Test D — no pipeline import** (AST check, mirrors test_uses_streams_not_list lines 194–242):
```python
def test_no_pipeline_in_ops() -> None:
    import ast
    src = MESSAGE_OPS.read_text()
    tree = ast.parse(src)
    called_methods = {
        node.func.attr.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "pipeline" not in called_methods, (
        "client.pipeline() call found — use plain loop per 10-RESEARCH Pitfall 4"
    )
```

**Test E — message_app has send/broadcast/subscribe commands** (mirrors lines 169–185):
```python
def test_message_init_has_send_broadcast_subscribe_commands() -> None:
    init_src = MESSAGE_INIT.read_text()
    for verb in ("send", "broadcast", "subscribe", "unsubscribe"):
        assert f'@message_app.command("{verb}")' in init_src, (
            f"@message_app.command({verb!r}) missing from message/__init__.py"
        )
```

**Test F — SUMMARY coverage** (lines 250–277) — copy with Phase 10 pattern:
```python
def test_phase_10_summaries_exist() -> None:
    if not PHASE_DIR.exists():
        pytest.skip(f"{PHASE_DIR.relative_to(REPO_ROOT)} not present")
    plans = sorted(PHASE_DIR.glob("10-*-PLAN.md"))
    if not plans:
        pytest.skip("no 10-*-PLAN.md files yet")
    for plan in plans:
        summary = plan.parent / plan.name.replace("-PLAN.md", "-SUMMARY.md")
        assert summary.exists(), f"Missing SUMMARY for {plan.name}"
```

---

### `tests/multiprocess/test_mailbox_durability.py` (ACTIVATE — replace skip-stub)

**Analog:** `tests/multiprocess/test_session_registry.py` — `test_registered_child_appears_in_list` (lines 197–247)

**Current stub to replace** (lines 20–36 from test_mailbox_durability.py):
```python
def test_mailbox_persists_for_offline_session(clean_db, redis_precheck) -> None:
    pytest.skip(
        "Phase 10 'message send' verb not yet available — ..."
    )
```

**Replacement body** (per the docstring in lines 22–35 of test_mailbox_durability.py):
```python
def test_mailbox_persists_for_offline_session(clean_db, redis_precheck) -> None:
    """MBOX-01: Messages written to a session's mailbox persist for offline retrieval."""
    offline_id = f"test-offline-{os.getpid()}-{time.time_ns()}"
    sender_id = f"test-sender-{os.getpid()}-{time.time_ns()}"

    # Step 1: send --to offline_id from sender_id (offline_id NOT registered — durability test)
    child_env = {**os.environ, "CLAUDE_CODE_SESSION_ID": sender_id, "EM_PROJ_REDIS_DB": str(TEST_DB)}
    proc = subprocess.Popen(
        [EM_PROJ_BIN, "message", "send", "--to", offline_id, "--json", "hello offline"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=child_env,
    )
    stdout, stderr = proc.communicate(timeout=15)
    assert proc.returncode == 0, f"send failed: {stderr!r}"

    # Step 2: read the offline session's inbox
    child_env2 = {**os.environ, "CLAUDE_CODE_SESSION_ID": offline_id, "EM_PROJ_REDIS_DB": str(TEST_DB)}
    proc2 = subprocess.Popen(
        [EM_PROJ_BIN, "message", "inbox", "--json", "--peek"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=child_env2,
    )
    stdout2, _ = proc2.communicate(timeout=15)
    assert proc2.returncode == 0

    envelope = json.loads(stdout2)
    messages = envelope.get("data", [])
    assert len(messages) >= 1, "inbox must contain the sent message"
    msg = messages[0]
    for field in ("msg_id", "from_session", "pattern", "scope", "topic", "body", "sent_at", "ttl"):
        assert field in msg, f"MBOX-04 field {field!r} missing from message"
```

**Required imports to add to test_mailbox_durability.py** (currently only `pytest`):
```python
import json
import os
import subprocess
import time

from tests.conftest import EM_PROJ_BIN, TEST_DB
```

---

## Shared Patterns

### D-14 Three-Step Verb Shell
**Source:** `src/em_proj/message/__init__.py` lines 63–95 (`inbox_cmd`)
**Apply to:** All four new verb commands (send, broadcast, subscribe, unsubscribe)
```python
# Step 1: resolve_json_mode(json_flag)
# Step 2: get_client() + die_if_redis_unreachable(client)
# Step 3: one _ops call + emit_ok / emit_error / emit_not_found
# NO business logic in verb shell — all decision logic in _ops.py
```

### ValidationError raise-and-re-raise
**Source:** `src/em_proj/message/__init__.py` lines 91–94
```python
try:
    messages = mailbox_inbox(session_id, since=since, peek=peek)
except ValidationError as e:
    emit_error(e.code, e.message, json_mode=json_mode)
```
**Apply to:** All verb shells that call ops functions that may raise ValidationError (send, broadcast, subscribe, unsubscribe).

### SessionNotFound → emit_not_found
**Source:** `src/em_proj/session/_ops.py` lines 151–167 (SessionNotFound class); `src/em_proj/output.py` lines 135–163 (emit_not_found, exit 2)
```python
except SessionNotFound as e:
    emit_not_found(str(e), json_mode=json_mode)
```
**Apply to:** `send_cmd` (directed --to path only). Broadcast and topic sends skip per-recipient validation.

### emit_ok flat-dict payload (MSG-05)
**Source:** `src/em_proj/output.py` lines 116–131 (`_render_plain` — flat dict of scalars renders as `key: value` lines)
```python
# Keep emit_ok data payload all-scalar for clean TTY rendering:
emit_ok(data={
    "recipients_written": N,
    "recipients_failed": M,
    "pub_published": P,
    "pattern": "broadcast",
    "scope": "project",
}, json_mode=json_mode)
# All scalars → _render_plain renders each as "key: value" (lines 127–130)
# Nested dict or list falls back to repr() — avoid
```
**Apply to:** Return value of all send ops and their verb shells.

### Prohibited imports in `_ops.py`
**Source:** `src/em_proj/message/_ops.py` lines 34–36 (docstring + test enforcement)
```python
# Prohibited: typer, multiprocessing, threading, pipeline
# Enforced by: tests/structural/test_phase_10_shape.py
```
**Apply to:** All additions to `message/_ops.py`.

### Autouse fixture pair for unit tests
**Source:** `tests/unit/test_mailbox.py` lines 31–44
```python
@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()

@pytest.fixture(autouse=True)
def _point_session_at_test_db(monkeypatch):
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")
```
**Apply to:** `tests/unit/test_message_send.py` — copy both fixtures verbatim.

### subprocess.Popen + .communicate(timeout=15) — NOT multiprocessing
**Source:** `tests/multiprocess/test_session_registry.py` lines 75–82
```python
proc = subprocess.Popen(
    [EM_PROJ_BIN, ...],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=child_env,
)
stdout, stderr = proc.communicate(timeout=15)
```
**Apply to:** `test_message_delivery.py` (all CLI calls) and `test_mailbox_durability.py` (activation body).

### `_unique_session_id()` test isolation helper
**Source:** `tests/multiprocess/test_session_registry.py` lines 133–139
```python
def _unique_session_id() -> str:
    return f"test-sess-{os.getpid()}-{time.time_ns()}"
```
**Apply to:** `test_message_delivery.py` — copy verbatim. Prevents stale-session collision with real Claude Code sessions.

### `_register_session_for_test(session_id, client)` scope-test infrastructure
**Source:** `tests/multiprocess/test_session_registry.py` lines 142–189
**Apply to:** `test_message_delivery.py` — copy verbatim. Required for broadcast/topic scope tests to create sessions with controlled `project_hash` / `upstream_identity` values.

### Structural test path constants + PHASE_DIR skip pattern
**Source:** `tests/structural/test_phase_09_shape.py` lines 23–28 and 261–268
```python
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "10-messaging-send-patterns"
# ...
if not PHASE_DIR.exists():
    pytest.skip(f"{PHASE_DIR.relative_to(REPO_ROOT)} not present — planning worktree may not be attached")
```
**Apply to:** `tests/structural/test_phase_10_shape.py` — copy pattern, substitute Phase 10 paths.

---

## No Analog Found

All files have close analogs. No gaps.

---

## Metadata

**Analog search scope:** `src/em_proj/message/`, `src/em_proj/session/`, `src/em_proj/`, `tests/unit/`, `tests/multiprocess/`, `tests/structural/`
**Files scanned:** 9
**Pattern extraction date:** 2026-06-07

---

## PATTERN MAPPING COMPLETE

**Phase:** 10 — Messaging Send Patterns
**Files classified:** 6
**Analogs found:** 6 / 6

### Coverage
- Files with exact analog: 6
- Files with role-match analog: 0
- Files with no analog: 0

### Key Patterns Identified
- All verb commands use the D-14 three-step shell: `resolve_json_mode` → `die_if_redis_unreachable` → one `_ops` call → `emit_*`; no business logic in `__init__.py`
- New `_ops.py` functions follow the `_validate_*` / `_build_*_key` / `get_client()` inside the function body pattern established by `mbox_write` and `_build_mbox_key`
- Multiprocess tests use `subprocess.Popen` + `.communicate(timeout=15)`, `EM_PROJ_REDIS_DB=15`, `_register_session_for_test` with the test runner's live pid, and `_unique_session_id()` for isolation — all from `test_session_registry.py`
- Structural tests use `ast.walk` for call-site checks (not docstring grep), `Path(__file__).resolve().parent.parent.parent` for REPO_ROOT, and `pytest.skip` (not xfail) for absent planning worktree
- `emit_ok` data payload must be all-scalar to get clean `key: value` TTY rendering from `_render_plain`; nested dicts fall back to `repr()`

### File Created
`/Users/emonical/projects/personal/ai-tools/em-proj/.planning/phases/10-messaging-send-patterns/10-PATTERNS.md`

### Ready for Planning
Pattern mapping complete. Planner can now reference analog patterns in PLAN.md files.
