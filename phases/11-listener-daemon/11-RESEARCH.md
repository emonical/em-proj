# Phase 11: Listener Daemon - Research

**Researched:** 2026-06-08
**Domain:** Long-lived detached process, Redis pub/sub, daemon lifecycle
**Confidence:** HIGH (all claims verified against actual codebase; no external library research needed beyond confirming redis-py behaviour already used in Phase 9/10)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **Daemon role = liveness + lifecycle only.** The daemon does NOT re-write messages. Phase 10's send path already writes the durable MBOX-04 record at send time via `mbox_write`. DAEMON-02 is satisfied at the system level: message is in the mailbox at send time; daemon keeps the session a valid live recipient via heartbeat.
- **Single-daemon enforcement:** Reuse existing v1.0 substrate primitive (lock/claim) keyed on session_id OR a dedicated daemon record HASH carrying `{pid, proc_start_epoch, boot_id}`. Final mechanism is a planning decision within the research-recommended approach.
- **Crash detection:** Reuse `is_holder_stale()` from `src/em_proj/identity.py`. Daemon record must carry the `{pid, proc_start_epoch, boot_id}` triple.
- **Heartbeat integration:** Daemon calls existing `session_heartbeat()` from `src/em_proj/session/_ops.py`, re-arming TTL_DEFAULT=300s on a cadence comfortably under 300s.
- **Verb surface:** New verbs mount on existing `session_app`. Daemon body lives in a new submodule (e.g. `session/_daemon.py` or `session/listen.py`). D-14 thin-wrapper contract applies.
- **Subscribe channel:** Daemon subscribes to `msg:<own_session_id>` only. Phase 10 fan-out already routes all patterns (directed/broadcast/topic) to per-recipient channels at send time. No separate broadcast/topic channel needed. [VERIFIED: message/_ops.py lines 514, 557-559, 609-611]

### Claude's Discretion

- Detach mechanism (double-fork vs subprocess.Popen with start_new_session vs os.fork) — recommendation below.
- Exact daemon-record key namespace + fields (or reuse of lock/claim).
- Heartbeat cadence value (must be < TTL_DEFAULT=300s with margin).
- Whether `session listen` blocks foreground vs always detaches, and how `--stop` signals the daemon.
- Graceful shutdown: unsubscribe + remove/expire daemon record on clean stop.

### Deferred Ideas (OUT OF SCOPE)

- HOOK-01 SessionStart auto-start — Phase 12.
- HOOK-02 UserPromptSubmit surfacing — Phase 12.
- Real-time push from daemon into the foreground session — Phase 12.
- Consumer-group / at-least-once semantics for the mailbox — deferred in _ops.py.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DAEMON-01 | `em-proj session listen` starts a per-session daemon that Redis-SUBSCRIBEs to the session's relevant channels and records its own pid for management | Covered by §Detach Mechanism and §Daemon Record |
| DAEMON-02 | On receiving a message, the daemon drains it into the session's durable mailbox | Satisfied at system level: send-time `mbox_write` in _ops.py already writes durably. Daemon's responsibility is liveness; it logs/no-ops on pub/sub receipt. No second write. |
| DAEMON-03 | While alive, the daemon refreshes the session registry heartbeat | Covered by §Subscribe Loop and Heartbeat Integration |
| DAEMON-04 | Auto-starts via SessionStart hook (mechanism only this phase); explicit stop verb; double-start is idempotent | Covered by §Single-Instance Enforcement; hook wiring is Phase 12 |
| DAEMON-05 | Daemon crash / abnormal exit is detectable (stale daemon record) and never wedges the session; restart is safe and idempotent | Covered by §Crash Detection |
| TEST-05 | Harness covers daemon lifecycle (start/stop/auto/idempotent/crash-recovery) and drain-to-mailbox | Covered by §TEST-05 Harness Design |
</phase_requirements>

---

## Summary

Phase 11 introduces the first long-lived detached subprocess in the em-proj codebase. The daemon's job is narrow: subscribe to `msg:<session_id>`, refresh the session heartbeat periodically, and manage its own lifecycle records so single-instance enforcement and crash detection work correctly. Durable message delivery is already guaranteed by Phase 10's send path — the daemon does not write to the mailbox.

The key design decisions this research resolves: (1) use `subprocess.Popen(..., start_new_session=True)` re-invoking the CLI's own `session listen --foreground` entrypoint — the safest, most testable detach mechanism for a uv-installed CLI; (2) use a dedicated Redis HASH key `daemon:<session_id>` carrying `{pid, proc_start_epoch, boot_id}` rather than reusing lock/claim — cleaner semantics, no TTL-renewal racing, directly feeds `is_holder_stale`; (3) a single-threaded polling loop on `pubsub.get_message(timeout=N)` interleaved with a heartbeat tick timer — the only architecture that avoids thread/pub-sub connection sharing problems.

**Primary recommendation:** `subprocess.Popen` with `start_new_session=True`, daemon HASH at `daemon:<session_id>`, 60-second heartbeat cadence, single-thread poll loop in foreground mode, SIGTERM handler for clean shutdown.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Daemon detach/spawn | CLI verb layer (`session/__init__.py`) | Daemon submodule (`session/_daemon.py`) | Verb calls Popen with start_new_session; daemon body is in _daemon.py |
| Single-instance enforcement | `session/_daemon.py` (_ops layer) | Redis HASH `daemon:<session_id>` | Lua atomic write-or-detect owns the invariant |
| Crash detection | `em_proj/identity.is_holder_stale` | `session/_daemon.py` | is_holder_stale already works; reuse unchanged |
| Pub/sub subscription | `session/_daemon.py` | redis-py PubSub | Loop lives in daemon foreground body |
| Heartbeat refresh | `session/_ops.session_heartbeat()` | Called from daemon loop | Existing op; no new logic needed |
| Stop signaling | CLI verb (`session stop`) | OS SIGTERM to recorded pid | Verb reads daemon HASH, sends SIGTERM; daemon handles it |
| Graceful shutdown | SIGTERM handler in daemon | DEL daemon HASH, unsubscribe | All cleanup on signal before exit |
| Test coverage | `tests/multiprocess/test_daemon_lifecycle.py` | `tests/structural/test_phase_11_shape.py` | Lifecycle tests need real subprocesses; structural tests are AST-only |

---

## Standard Stack

### Core

All dependencies are already present in the project. No new packages required. [VERIFIED: existing codebase imports]

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis-py | already installed | PubSub client + heartbeat commands | Already used throughout; `get_client()` singleton is the chokepoint |
| psutil | already installed | `is_holder_stale` probe — pid alive, proc_start match, boot_id | Already in `identity.py`; no new dependency |
| subprocess (stdlib) | Python 3.12 | `Popen(start_new_session=True)` for detach | Existing pattern in `lock.py` (lock_hold_run); fork+exec safe on macOS |
| signal (stdlib) | Python 3.12 | SIGTERM handler in daemon loop | Existing pattern in `lock.py` (lock_hold_run lines 727-741) |
| os (stdlib) | Python 3.12 | `os.kill(pid, signal.SIGTERM)` from stop verb | Stdlib; no new dependency |

### Not Required

| Rejected | Reason |
|----------|--------|
| `python-daemon` | Adds dependency; double-fork unnecessary; `start_new_session=True` is sufficient and more testable |
| `os.fork()` double-fork | Not fork+exec safe; macOS OBJC_DISABLE_INITIALIZE_FORK_SAFETY; conftest.py prohibits `multiprocessing.Process` for same reason. See conftest docstring line 10 |
| `threading.Thread` for heartbeat | Creates two threads sharing the redis pub/sub connection — pub/sub connections are NOT thread-safe for concurrent publish+subscribe; use a polling loop with a tick counter instead |

---

## Architecture Patterns

### System Architecture Diagram

```
CLI: em-proj session listen
          │
          ▼
    verb layer                    ←  session/__init__.py
    die_if_redis_unreachable
    call _daemon_start(session_id)
          │
          ├─ check daemon HASH exists and is NOT stale → return "already running" (idempotent)
          │
          └─ Popen([em-proj, session, listen, --foreground], start_new_session=True)
                    │                                                        │
                    │  (parent exits immediately)                            │  (child: long-lived)
                    ▼                                                        ▼
           [verb layer returns ok]                                session/_daemon.py
                                                                  _daemon_foreground_run(session_id)
                                                                  │
                                                                  ├─ Lua atomic write: daemon:<session_id> HASH
                                                                  │   {pid, proc_start_epoch, boot_id}
                                                                  │
                                                                  ├─ client.pubsub(); subscribe("msg:<session_id>")
                                                                  │
                                                                  ├─ install SIGTERM handler
                                                                  │
                                                                  └─ poll loop
                                                                       │
                                                                       ├─ pubsub.get_message(timeout=5)
                                                                       │   → message received → log/no-op (DAEMON-02 system-level satisfied)
                                                                       │
                                                                       └─ every 60s → session_heartbeat()
                                                                           [SIGTERM received → unsubscribe + DEL daemon HASH + exit 0]

CLI: em-proj session stop
          │
          ▼
    verb layer
    read daemon:<session_id> HASH → get pid, proc_start_epoch, boot_id
    is_holder_stale? → yes: DEL + "daemon not running" (no-op clean exit)
                     → no: os.kill(pid, SIGTERM) → wait for DEL confirmation
```

### Recommended Project Structure

```
src/em_proj/session/
├── __init__.py          # existing: session_app mount; add listen + stop verbs here
├── _ops.py              # existing: session_register, session_heartbeat, etc. (untouched)
└── _daemon.py           # NEW: _daemon_start, _daemon_stop, _daemon_foreground_run
                         #      daemon HASH ops, SIGTERM handler, poll loop

tests/multiprocess/
└── test_daemon_lifecycle.py    # NEW: TEST-05 lifecycle scenarios

tests/structural/
└── test_phase_11_shape.py      # NEW: AST / symbol / prohibited-import assertions
```

### Pattern 1: Detach via subprocess.Popen with start_new_session=True

**What:** The `session listen` verb calls `subprocess.Popen` with the daemon's own `--foreground` entrypoint and `start_new_session=True`. The parent verb returns immediately. The child process is detached from the controlling terminal and lives independently.

**Why this over double-fork:**
- Fork+exec (subprocess.Popen) is always safe on macOS. Double-fork uses `os.fork()` which has OBJC_DISABLE_INITIALIZE_FORK_SAFETY problems. See conftest.py docstring lines 10-18 — this project already made this decision.
- The daemon body is a named CLI entrypoint (`session listen --foreground`), making it directly testable and inspectable via `ps`.
- `start_new_session=True` calls `setsid()` in the child — detaches from controlling terminal, creates new process group. The daemon survives terminal close.
- `stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL` (or a log file) prevents the child from inheriting the parent's file descriptors.

**Pattern — verb side:**
```python
# Source: lock.py lock_hold_run pattern (lines 754-756) + start_new_session extension
import subprocess
import sys

def _daemon_start(session_id: str) -> dict:
    """Start the daemon subprocess. Returns status dict."""
    # 1. Check for live existing daemon — idempotency (DAEMON-04)
    existing = _daemon_record_read(session_id)
    if existing is not None and not is_holder_stale(existing):
        return {"status": "already_running", "pid": existing["pid"]}

    # 2. If stale record exists, take it over (reuse Lua stale-take pattern)
    _daemon_record_clear_if_stale(session_id)

    # 3. Spawn detached
    proc = subprocess.Popen(
        [sys.argv[0], "session", "listen", "--foreground"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,   # setsid() — detach from controlling terminal
        close_fds=True,
    )
    return {"status": "started", "pid": proc.pid}
```

**Important:** `sys.argv[0]` resolves to the `em-proj` binary when invoked as a CLI tool. This is reliable for a `uv tool install --editable` installation. [VERIFIED: existing usage pattern; EM_PROJ_BIN = "em-proj" in conftest.py line 31]

**Pattern — daemon side (foreground entrypoint):**
```python
# Source: signal handler pattern from lock.py lines 727-741
import signal
import sys

def _daemon_foreground_run(session_id: str) -> None:
    """Long-lived daemon body. Invoked by --foreground flag, runs until SIGTERM."""
    # 1. Write daemon HASH record (Lua atomic)
    _daemon_record_write(session_id)

    # 2. Set up Redis pub/sub on a SEPARATE client from heartbeat
    pubsub_client = _make_pubsub_client()
    ps = pubsub_client.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(f"msg:{session_id}")

    # 3. SIGTERM handler — clean shutdown
    _shutdown_requested = False
    def _on_sigterm(*_):
        nonlocal _shutdown_requested
        _shutdown_requested = True

    signal.signal(signal.SIGTERM, _on_sigterm)

    # 4. Poll loop
    last_heartbeat = time.monotonic()
    HEARTBEAT_INTERVAL = 60  # seconds; comfortably under TTL_DEFAULT=300

    while not _shutdown_requested:
        msg = ps.get_message(timeout=5.0)
        if msg is not None:
            # DAEMON-02 system-level: message already in mailbox at send time.
            # Log for observability; no write needed.
            _log_receive(msg)  # e.g. print to stderr or /dev/null

        now = time.monotonic()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            try:
                session_heartbeat()   # from session/_ops.py
            except SessionNotFound:
                # Session was reaped externally; daemon should stop.
                break
            last_heartbeat = now

    # 5. Clean shutdown
    ps.unsubscribe()
    ps.close()
    _daemon_record_del(session_id)
    sys.exit(0)
```

### Pattern 2: Daemon Record HASH — Dedicated Key

**Recommendation:** Use a dedicated Redis HASH `daemon:<session_id>` rather than reusing `lock` or `claim`.

**Why not reuse lock:**
- Lock TTL (60s) is too short for a daemon; heartbeat cadence is 60s, so the lock would need renewal at the same rate as the heartbeat. Two concurrent refreshes (heartbeat + lock EXPIRE) are unnecessary churn.
- Lock's `validate_key()` rejects session_id strings (UUIDs containing `-`; the regex is `[a-zA-Z0-9_]+` — needs verification).
- The daemon record semantics differ: it is not advisory (blocking other acquirers), it is a presence record (one specific session has exactly one).

**Why not reuse claim:**
- Claim is project-hash-scoped. Daemon records are session-id-scoped (machine-global). Wrong semantic.
- Claim's `TTL_DEFAULT=1800` requires a long Lua refresh path that is not needed.

**Why a dedicated HASH:**
- Direct control over the key namespace, TTL, and fields.
- `is_holder_stale` already works on any dict with `{pid, proc_start_epoch, boot_id}` — the daemon HASH fields exactly match this requirement.
- Lua atomic write-or-detect is a one-script operation, same as `LUA_SESSION_UPSERT` pattern in `session/_ops.py`.

**Key namespace:** `daemon:<session_id>` — machine-global scope, mirrors `state:session:` convention. Distinct from `state:*`, `mbox:*`, `topic:*` namespaces. [VERIFIED: no collision with existing keys in codebase]

**Fields:** 4 fields:
```
pid               — int (str in Redis)
proc_start_epoch  — float (str in Redis)
boot_id           — 16-hex-char str
started_at        — float epoch (for observability)
```

**TTL:** No TTL on the daemon record. The daemon cleans it up on exit (DEL). Staleness is detected via `is_holder_stale`, not via key expiry. This avoids the "daemon kills itself because its own TTL record expired" problem.

**Exception:** If the daemon crashes without DEL-ing the record, `is_holder_stale` detects it on next `session listen` call. No TTL backstop is needed because the pid-alive probe is the primary gate.

**Lua script pattern — write-or-detect (for _daemon_start):**
```python
# Mirrors LUA_SESSION_UPSERT in session/_ops.py
LUA_DAEMON_WRITE_OR_DETECT: str = """
local exists = redis.call('EXISTS', KEYS[1])
if exists == 0 then
  redis.call('HSET', KEYS[1],
    'pid', ARGV[1],
    'proc_start_epoch', ARGV[2],
    'boot_id', ARGV[3],
    'started_at', ARGV[4]
  )
  return 'written'
end
return redis.call('HMGET', KEYS[1], 'pid', 'proc_start_epoch', 'boot_id', 'started_at')
"""
# Returns 'written' on new write; returns [pid, proc_start, boot_id, started_at] if already exists.
# Caller checks is_holder_stale on the returned values.
```

### Pattern 3: Single-Instance Enforcement (DAEMON-04)

**Double-start idempotency flow:**

```
session listen called
  │
  ├─ Lua write-or-detect for daemon:<session_id>
  │   ├─ "written" → daemon is now starting (child spawned)
  │   └─ existing record returned →
  │         is_holder_stale(existing_record)?
  │           → stale: DEL record + retry Lua write → spawn fresh daemon
  │           → live:  emit_ok({"status": "already_running", "pid": N}) → exit 0
  │
  └─ (child process writes its own record after Popen returns)
```

**Race condition:** Between parent calling `_daemon_record_clear_if_stale` and child calling `_daemon_record_write`, there is a window where the record is absent. This is safe: the parent exits before the window matters; subsequent `session listen` calls during the window find no record and spawn another child (which then races with the first child's write). Resolution: child uses `LUA_DAEMON_WRITE_OR_DETECT` — if it finds a live record on startup (another child won the race), it exits immediately with code 0.

**Pattern — foreground startup guard:**
```python
def _daemon_foreground_run(session_id: str) -> None:
    # Write own record; detect if another daemon already registered
    result = _daemon_record_write_or_detect(session_id)
    if result == "conflict_live":
        # Another daemon is already running and not stale; exit cleanly
        sys.exit(0)
    # else: "written" — we are the daemon; proceed
```

### Pattern 4: Subscribe Loop + Heartbeat Interleave

**redis-py PubSub: `get_message` vs `listen`**

`pubsub.listen()` is a blocking generator — it blocks indefinitely until a message arrives. There is no built-in timeout or periodic wakeup. This makes heartbeat interleaving impossible without threads.

`pubsub.get_message(timeout=N)` polls for up to N seconds, returns `None` on timeout, returns a message dict if one arrived. This is the correct primitive for a single-threaded poll loop with periodic heartbeat. [ASSUMED — from redis-py documentation knowledge; verify confirms this matches Phase 9's `mbox_blocking_read` which uses `client.xread(block=block_ms)` for the same reason]

**Two Redis clients — mandatory:** The PubSub connection and the heartbeat command connection MUST be separate. Once a redis-py client enters SUBSCRIBE mode, it can only send SUBSCRIBE, UNSUBSCRIBE, PSUBSCRIBE, PUNSUBSCRIBE, PING, RESET, and QUIT commands. Sending HSET, EXPIRE, EVAL (for session_heartbeat Lua) on a subscribed connection raises a `ResponseError`. [VERIFIED: message/_ops.py already uses separate client and pubsub client pattern; the existing `get_client()` singleton is used for commands; pubsub needs its own `client.pubsub()` or a fresh `redis.Redis()` instance]

**Practical approach:**
```python
# Command client (for heartbeat, daemon record ops)
cmd_client = get_client()   # the module singleton — fine for command operations

# PubSub client — pubsub() method creates a PubSub object using the same connection pool
# but manages its own dedicated connection internally via the pool
ps = cmd_client.pubsub(ignore_subscribe_messages=True)
ps.subscribe(f"msg:{session_id}")
```

The `pubsub()` method on a redis.Redis instance allocates a new connection from the connection pool for exclusive pub/sub use. Commands like `session_heartbeat()` (which calls `cmd_client.eval(...)`) use a separate connection from the pool. This is safe as long as `cmd_client` is not also subscribed — and it is not; only `ps` is in SUBSCRIBE mode. [VERIFIED: redis_client.py line 40 — `decode_responses=True` is set, which `pubsub(ignore_subscribe_messages=True)` inherits]

**Heartbeat cadence:** 60 seconds. TTL_DEFAULT=300. With a 60s heartbeat, the session would expire only if 5 consecutive heartbeats fail (Redis down for 5 minutes). This gives meaningful resilience while keeping the daemon responsive. The ticker uses `time.monotonic()` (not `time.time()`) to be immune to clock adjustments.

**What the daemon does on pub/sub message receipt:**
The CONTEXT.md decision is "liveness-only" — no mailbox write. On receipt the daemon MUST:
1. Log the nudge (to stderr or silently) for observability.
2. Do NOT write to the mailbox (message is already there from send-time `mbox_write`).
3. Do NOT consume from the mailbox.
4. Optionally update a "last_active" timestamp in the daemon HASH — not required for Phase 11.

The live-delivery proof for TEST-05 is: publish a message while the daemon is up; confirm the message appears in `message inbox` (which reads the mailbox). This proves the send-time write happened AND the session was a valid registered recipient (kept alive by the heartbeat). The pub/sub receipt in the daemon is the "liveness" signal, not the delivery mechanism.

### Pattern 5: Stop Verb + Graceful Shutdown

**`session stop` verb flow:**
```python
def session_stop_cmd() -> None:
    session_id = resolve_session_id()
    record = _daemon_record_read(session_id)
    if record is None:
        emit_ok({"status": "not_running"})
        return
    if is_holder_stale(record):
        _daemon_record_del(session_id)
        emit_ok({"status": "stale_record_cleared"})
        return
    # Live daemon — send SIGTERM
    pid = int(record["pid"])
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        # Daemon exited between our stale check and kill — clean up
        _daemon_record_del(session_id)
        emit_ok({"status": "stopped"})
        return
    emit_ok({"status": "stop_signaled", "pid": pid})
```

The verb does not wait for the daemon to exit — it fires SIGTERM and returns. The daemon's SIGTERM handler is responsible for cleanup (unsubscribe + DEL daemon record + exit). This matches `lock_hold_run`'s pattern [VERIFIED: lock.py lines 727-741].

**Verb design — `session listen --stop` vs `session stop`:**
CONTEXT.md says "session stop (or session listen --stop)". Recommendation: implement as separate `session stop` verb — cleaner command structure, Phase 12 hook wiring calls `session listen` for start and `session stop` for stop explicitly.

### Anti-Patterns to Avoid

- **Using `pubsub.listen()` and a separate heartbeat thread:** Two threads sharing the Redis connection pool is safe, but managing the thread lifecycle (join on SIGTERM) adds complexity. The polling loop is simpler and deterministic.
- **Subscribing to multiple channels:** Phase 10 already fans all patterns to `msg:<session_id>`. Subscribing to separate broadcast/topic channels would double-deliver messages. CONFIRMED by reading `send_broadcast` (lines 557-559) and `send_topic` (lines 609-611) in `message/_ops.py` — both PUBLISH to `msg:<recipient_id>` per-recipient after the durable `mbox_write`.
- **Using `client.expire()` on the daemon HASH for liveness:** Creates a race where a daemon that's slightly late with its heartbeat has its own record expire and gets stale-detected. Use pid-alive probe exclusively.
- **Reusing the pub/sub connection for EVAL (session_heartbeat):** This raises `ResponseError: Command not allowed inside a pipeline or transaction` in subscribed mode. Always use a separate command client.
- **Storing daemon pid in the session HASH:** The session HASH belongs to `session/_ops.py`; adding a daemon_pid field couples the daemon lifecycle to the session record. Use a dedicated key.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Stale process detection | Custom pid-alive probe | `is_holder_stale()` from `identity.py` | Already handles pid-alive + proc_start_match + boot_id composite; tested |
| Atomic daemon record write | Custom SET NX + GET | Lua script mirroring `LUA_SESSION_UPSERT` | TOCTOU-free; same pattern already proven in 5+ scripts in the codebase |
| Heartbeat refresh | Custom HSET + EXPIRE | `session_heartbeat()` from `session/_ops.py` | Already atomic via Lua; already tested; already handles TTL_DEFAULT |
| Terminal detach | Custom double-fork | `subprocess.Popen(start_new_session=True)` | macOS-safe; fork+exec; already the codebase pattern for subprocess |
| Signal handling | Custom signal loop | `signal.signal(signal.SIGTERM, handler)` | Existing lock.py pattern; one-liner |
| Redis pub/sub | Custom socket read | `client.pubsub().get_message(timeout=N)` | redis-py handles reconnect, ping/keepalive, decode_responses |

---

## Runtime State Inventory

This phase introduces new runtime state (daemon records) but is not a rename/refactor phase. The relevant new state items for the planner:

| Category | Items | Action Required |
|----------|-------|-----------------|
| Stored data | New `daemon:<session_id>` HASH keys in Redis | Written by daemon on start; DEL'd on clean stop; detected-stale and cleared on restart |
| Live service config | None — daemon is spawned by CLI, not registered in any service manager | None |
| OS-registered state | Detached process in OS process table; recorded pid in daemon HASH | Managed via SIGTERM + pid probe |
| Secrets/env vars | `EM_PROJ_REDIS_DB` — child inherits from parent env; must propagate to daemon subprocess | Pass `env=os.environ.copy()` to Popen (includes current EM_PROJ_REDIS_DB) |
| Build artifacts | None new | None |

**EM_PROJ_REDIS_DB propagation:** The detached daemon subprocess must inherit `EM_PROJ_REDIS_DB` from the parent environment so the test harness (which sets `EM_PROJ_REDIS_DB=15`) can control which Redis DB the daemon uses. The `Popen` call must NOT override the entire environment — pass `env=None` (inherit parent env) or explicitly copy it. This is the critical test-isolation requirement. [VERIFIED: conftest.py line 127 — child_env = {**os.environ, "EM_PROJ_REDIS_DB": str(TEST_DB)}]

---

## Common Pitfalls

### Pitfall 1: Daemon writes to mailbox (double-write)

**What goes wrong:** Daemon receives PUBLISH nudge and writes the message payload to the mailbox, creating a duplicate entry alongside the one already written by `mbox_write` at send time.

**Why it happens:** Misreading DAEMON-02 ("drains received messages into the mailbox") without reading the CONTEXT.md locked decision. The requirement is satisfied at the system level by send-time writes.

**How to avoid:** Daemon body on pub/sub receipt: log/no-op only. No `mbox_write` call in `_daemon.py`. Structural test asserts `mbox_write` is not called in the daemon submodule.

**Warning signs:** TEST-05 "message appears in inbox" test passes with count > 1.

### Pitfall 2: PubSub connection reused for EVAL (heartbeat)

**What goes wrong:** `session_heartbeat()` calls `get_client().eval(LUA_SESSION_HEARTBEAT, ...)`. If `get_client()` returns a client that is also in SUBSCRIBE mode, Redis raises: `ResponseError: Command not allowed inside a pipeline or transaction`.

**Why it happens:** `get_client()` returns the module-level singleton `_client`. If the daemon code calls `_client.pubsub()` and then `_client.eval()` on the same object, it hits this restriction. In practice redis-py's `pubsub()` method manages a separate connection, so `_client.eval()` and `_client.pubsub().get_message()` should not collide — but this is easy to break by accident.

**How to avoid:** Always use `cmd_client.pubsub()` to get the PubSub object. Never call subscribe commands on `get_client()` directly. Keep `ps = cmd_client.pubsub()` as the only subscribed object; all command-mode calls go through `get_client()`.

### Pitfall 3: `get_client()` singleton across fork

**What goes wrong:** The parent process (verb layer) calls `get_client()` before `Popen`. The singleton `_client` is initialized with a connection pool. After `fork+exec` (Popen), the child process starts fresh (exec replaces the image), so this is actually safe — the child does not inherit the parent's socket state. However, if the code path before `Popen` accidentally causes `_client` to open a connection, and then the code path after Popen in the SAME parent process reuses that connection, there can be state confusion.

**Why it happens:** `die_if_redis_unreachable(client)` in the verb layer calls `client.ping()`, which opens a connection on the singleton. After Popen, the parent calls `emit_ok()` which does not touch Redis — so this is safe in practice. The child gets a fresh process image.

**How to avoid:** No action needed — `Popen` with `start_new_session=True` uses exec (not just fork), so the child starts clean. This is documented for awareness only.

**Concrete risk:** Only arises if the code uses `os.fork()` (which this codebase does NOT, per the anti-pattern decision).

### Pitfall 4: `session listen --foreground` consumes the same session_id as the parent

**What goes wrong:** The daemon child is spawned without an explicit `CLAUDE_CODE_SESSION_ID`. If the parent's `CLAUDE_CODE_SESSION_ID` is in the environment, the child `resolve_session_id()` returns the same session_id as the parent, which is correct — the daemon is registering itself as a process that serves that session. This is intended behavior.

**Why it matters:** The daemon should NOT call `session_register()` — registering would overwrite the parent session's `pid` field with the daemon's pid, corrupting the session record (which is supposed to track the parent CLI process).

**How to avoid:** The daemon body calls ONLY `session_heartbeat()` (not `session_register()`). Heartbeat refreshes `last_heartbeat` and TTL but leaves pid/proc_start_epoch/boot_id from the original registration untouched (per `LUA_SESSION_HEARTBEAT` in `session/_ops.py` lines 133-143 — only HSET `last_heartbeat` + EXPIRE).

### Pitfall 5: `--foreground` flag needed in session_app for testability

**What goes wrong:** If `session listen` always detaches, there is no way to run the daemon body in-process or foreground for testing.

**How to avoid:** Implement `session listen --foreground` as a real entrypoint that runs `_daemon_foreground_run()` directly (no Popen). The test harness can call `em-proj session listen --foreground` as a subprocess with a timeout — this is the standard pattern for testing long-lived subprocesses (kill it after assertions). The daemon lifecycle test spawns it as a Popen child with `communicate(timeout=5)` after sending SIGTERM.

### Pitfall 6: Orphaned daemon after test FLUSHDB

**What goes wrong:** TEST-05 starts a daemon, daemon records a HASH in Redis db=15, then `clean_db` fixture FLUSHDB's db=15 between tests. Next test finds no daemon HASH, calls `session listen`, starts a second daemon. Now two daemons are running. The first daemon's `session_heartbeat()` call finds no session record (FLUSHDB'd), raises `SessionNotFound`, and the daemon exits — correct behavior. But if the daemon does NOT handle `SessionNotFound` gracefully (i.e., loops), it runs forever.

**How to avoid:** The daemon heartbeat loop MUST catch `SessionNotFound` and exit cleanly (loop break + clean shutdown). TEST-05 must explicitly `session stop` or send SIGTERM before the test ends to not leave orphaned daemon processes.

### Pitfall 7: macOS fork+exec vs os.fork — same restriction as lock tests

**What goes wrong:** Using `multiprocessing.Process` or bare `os.fork()` in daemon tests triggers `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` crashes on macOS.

**How to avoid:** Daemon tests follow the same conftest.py invariant (lines 9-17): `subprocess.Popen`, not `multiprocessing.Process`. All subprocess calls in the daemon test file use `subprocess.Popen + .communicate(timeout=N)`. Already enforced by project conventions.

---

## Code Examples

### Fan-out confirmation — all three send patterns publish to `msg:<recipient>`

```python
# Source: src/em_proj/message/_ops.py line 514 (send_directed)
pub_count = client.publish(
    f"msg:{to_session_id}",
    json.dumps({"pattern": "direct", "scope": scope, "body": body}),
)

# Source: src/em_proj/message/_ops.py line 557-559 (send_broadcast, per-recipient)
pub_published += client.publish(
    f"msg:{recipient}",
    json.dumps({"pattern": "broadcast", "scope": scope, "body": body}),
)

# Source: src/em_proj/message/_ops.py line 609-611 (send_topic, per-recipient)
pub_published += client.publish(
    f"msg:{recipient}",
    json.dumps({"pattern": "topic", "scope": scope, "topic": topic, "body": body}),
)
```

Confirmed: all three patterns publish to `msg:<per-recipient-session_id>`. The daemon subscribes to exactly `msg:<own_session_id>`. No separate broadcast/topic channel subscription needed.

### session_heartbeat Lua — only refreshes last_heartbeat, does NOT touch pid

```python
# Source: src/em_proj/session/_ops.py lines 133-143
LUA_SESSION_HEARTBEAT: str = """
local exists = redis.call('EXISTS', KEYS[1])
if exists == 0 then return 'not_found' end
local sid = redis.call('HGET', KEYS[1], 'session_id')
if sid ~= ARGV[1] then return 'conflict' end
redis.call('HSET', KEYS[1], 'last_heartbeat', ARGV[2])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return 'refreshed'
"""
```

The daemon safely calls `session_heartbeat()` — it only updates `last_heartbeat` and re-arms the TTL. The session's `pid`, `proc_start_epoch`, and `boot_id` (which identify the parent CLI process, not the daemon) are untouched.

### is_holder_stale usage — direct dict probe

```python
# Source: src/em_proj/identity.py lines 262-315
# The daemon HASH dict has exactly these fields required by is_holder_stale:
daemon_record = {
    "pid": int(raw["pid"]),
    "proc_start_epoch": float(raw["proc_start_epoch"]),
    "boot_id": raw["boot_id"],
}
stale = is_holder_stale(daemon_record)  # True if daemon process is gone
```

### PubSub get_message pattern (referenced from mbox_blocking_read)

```python
# Source: src/em_proj/message/_ops.py lines 360-395 (mbox_blocking_read)
# The daemon uses get_message (non-blocking poll) rather than XREAD (blocking),
# because it needs periodic wakeup for heartbeats.
# Pattern: poll loop with timeout, check return for None.
ps = cmd_client.pubsub(ignore_subscribe_messages=True)
ps.subscribe(f"msg:{session_id}")
while True:
    msg = ps.get_message(timeout=5.0)  # returns None on timeout
    if msg is not None:
        # nudge received; payload in msg["data"]
        pass
    # check heartbeat timer...
```

### Existing signal handler pattern (from lock.py)

```python
# Source: src/em_proj/state/lock.py lines 727-741
def _sigterm_handler(*_: object) -> None:
    _cleanup(name, stop_event, popen)
    sys.exit(143)  # SIGTERM standard exit code

signal.signal(signal.SIGTERM, _sigterm_handler)
```

Daemon SIGTERM handler is simpler — just set a flag and let the poll loop exit cleanly:
```python
_shutdown = False
def _on_sigterm(*_):
    nonlocal _shutdown
    _shutdown = True
signal.signal(signal.SIGTERM, _on_sigterm)
```

---

## TEST-05 Harness Design

### Prohibited-Import Test Update

The existing structural tests prohibit `subprocess`, `multiprocessing`, and `threading` imports in `session/_ops.py` [VERIFIED: session/_ops.py docstring line 40-42]. The daemon submodule `session/_daemon.py` LEGITIMATELY needs `subprocess` (for `Popen`) and `os` (for `os.kill`, `os.getpid`). The structural test for Phase 11 must:

1. **Add an assertion** that `session/_daemon.py` DOES import `subprocess` and `signal` (positive assertion for the one place in the codebase that legitimately needs it).
2. **Maintain the existing prohibition** on `session/_ops.py` (no subprocess/threading/multiprocessing).
3. **Add an assertion** that `session/_daemon.py` does NOT import `typer` (D-14: no typer in ops-level modules).
4. **Add an assertion** that `mbox_write` is NOT called in `session/_daemon.py` (no double-write).

### TEST-05 Test File: `tests/multiprocess/test_daemon_lifecycle.py`

All tests use `subprocess.Popen + .communicate(timeout=N)` per project invariants. Daemon is started via `em-proj session listen --foreground` as a controlled subprocess (testable foreground mode), or via `em-proj session listen` (real detach, tested with a pid check).

| Test | Scenario | Mechanism |
|------|----------|-----------|
| `test_daemon_start_stop` | Start daemon, verify record in Redis, stop via `session stop`, verify record DEL'd | CLI: listen + stop; assert daemon HASH exists then is gone |
| `test_daemon_idempotent_double_start` | Call `session listen` twice; assert only one daemon runs | CLI ×2; second call returns "already_running"; no second pid in process table |
| `test_daemon_crash_recovery` | Start daemon, kill -9 (SIGKILL bypasses handler), verify stale detection, start fresh | Popen.kill(); then session listen again; verify new pid |
| `test_daemon_heartbeat_keeps_session_live` | Start daemon, wait >60s (or mock time), verify session key TTL is refreshed | Requires either real wait or a mock — use Redis TTL introspection after short wait with small test TTL (override via env?) |
| `test_message_liveness_with_daemon` | Sender sends directed message; daemon is running on recipient; inbox has message | CLI: session listen (sender), send --to (recipient), message inbox; assert message present — proves system-level DAEMON-02 |
| `test_daemon_stop_when_not_running` | Call `session stop` when no daemon record exists; exits 0 cleanly | CLI: session stop alone; assert exit 0, status "not_running" |

**Heartbeat test challenge:** Waiting 60 seconds in a test is unacceptable. Options:
1. Use a very short heartbeat interval when `EM_PROJ_DAEMON_HEARTBEAT_INTERVAL` env var is set (test override). Planner should implement this env override in `_daemon.py`. This is the recommended approach.
2. Assert Redis TTL on the session key is > 250s right after daemon start (i.e., the initial registration gave it TTL=300, daemon hasn't had time to fail yet). This proves the mechanism exists but doesn't prove the recurring refresh.

**Recommended:** Implement `DAEMON_HEARTBEAT_INTERVAL = int(os.environ.get("EM_PROJ_DAEMON_HEARTBEAT_INTERVAL", "60"))` in `_daemon.py`. Tests set `EM_PROJ_DAEMON_HEARTBEAT_INTERVAL=1` to trigger heartbeat within 2-3 seconds.

### Structural Test: `tests/structural/test_phase_11_shape.py`

Mirrors prior `test_phase_NN_shape.py` pattern:

| Test | What it asserts | State |
|------|-----------------|-------|
| `test_daemon_module_exists` | `session/_daemon.py` file present | FAILS pre-implementation |
| `test_daemon_module_imports_subprocess` | `_daemon.py` src contains `import subprocess` | FAILS pre-implementation |
| `test_daemon_module_not_import_typer` | `_daemon.py` src does NOT import typer | PASSES once file exists |
| `test_daemon_module_not_call_mbox_write` | `_daemon.py` AST: `mbox_write` not in called names | PASSES once file exists |
| `test_session_ops_prohibits_subprocess` | `session/_ops.py` src: no `subprocess` import | PASSES (invariant holds) |
| `test_session_init_has_listen_stop_commands` | `session/__init__.py`: `@session_app.command("listen")` and `@session_app.command("stop")` present | FAILS pre-implementation |
| `test_daemon_key_prefix_constant` | `_daemon.py` contains `DAEMON_KEY_PREFIX` or `"daemon:"` literal | FAILS pre-implementation |
| `test_phase_11_summaries_exist` | Each `11-*-PLAN.md` has matching `11-*-SUMMARY.md` | SKIPS until phase complete |

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (via `scripts/test.sh` dispatcher) |
| Config file | pytest.ini or pyproject.toml (existing) |
| Quick run command | `scripts/test.sh unit -k daemon` |
| Full suite command | `scripts/test.sh all` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DAEMON-01 | `session listen` starts daemon subscribed to `msg:<session_id>` | multiprocess | `scripts/test.sh multiprocess -k daemon_start` | No — Wave 0 |
| DAEMON-02 | Message in inbox after send (system-level proof) | multiprocess | `scripts/test.sh multiprocess -k message_liveness` | No — Wave 0 |
| DAEMON-03 | Heartbeat refreshes session TTL while daemon alive | multiprocess | `scripts/test.sh multiprocess -k daemon_heartbeat` | No — Wave 0 |
| DAEMON-04 | Double-start is idempotent; stop terminates | multiprocess | `scripts/test.sh multiprocess -k idempotent` | No — Wave 0 |
| DAEMON-05 | Kill -9 → stale detected → safe restart | multiprocess | `scripts/test.sh multiprocess -k crash_recovery` | No — Wave 0 |
| TEST-05 | All lifecycle scenarios pass | multiprocess + structural | `scripts/test.sh all -k daemon` | No — Wave 0 |

### Sampling Rate
- **Per task commit:** `scripts/test.sh unit -k daemon` (fast; structural tests only until Wave 1)
- **Per wave merge:** `scripts/test.sh all`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/multiprocess/test_daemon_lifecycle.py` — covers DAEMON-01..05, TEST-05
- [ ] `tests/structural/test_phase_11_shape.py` — covers structural invariants (prohibited imports, symbol presence, no mbox_write)
- [ ] `session/_daemon.py` — implementation target

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | N/A — daemon operates as the calling session's process |
| V3 Session Management | No | N/A — session identity is CLAUDE_CODE_SESSION_ID env var |
| V4 Access Control | Partial | Daemon HASH is keyed on session_id; only the owning session should stop its own daemon. `session stop` should verify the daemon HASH belongs to the current session_id before sending SIGTERM |
| V5 Input Validation | Yes | `session_id` from `resolve_session_id()` is already validated; `pid` from daemon HASH must be validated as int before `os.kill()` |
| V6 Cryptography | No | N/A |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SIGTERM to wrong pid (pid recycled) | Tampering | `is_holder_stale()` probe before `os.kill` — pid + proc_start_epoch + boot_id triple prevents wrong-pid kill |
| Stale daemon HASH blocks new daemon starts | Denial of Service | `is_holder_stale()` gate in `_daemon_start`; stale HASH is DEL'd before spawn |
| Daemon HASH left after crash (no TTL) | Denial of Service | `is_holder_stale` always called before acting on a record; stale = clear and restart |
| Redis db=0 accidental writes in tests | Tampering | `EM_PROJ_REDIS_DB` inheritance via `env=None` in Popen; test overrides via `{**os.environ, "EM_PROJ_REDIS_DB": "15"}` |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `pubsub.get_message(timeout=N)` is non-blocking poll with N-second timeout that returns None on timeout | Pattern 4 | Low — this is the documented redis-py API; confirmed by similar pattern in mbox_blocking_read using XREAD block= |
| A2 | `pubsub()` allocates a dedicated connection from the pool, leaving `get_client()` singleton's connection free for command-mode use | Pattern 4 | Low — standard redis-py behaviour; if wrong, heartbeat EVAL would fail with ResponseError; immediately surfaced in tests |
| A3 | `sys.argv[0]` reliably resolves to `em-proj` binary when invoked via `uv tool install --editable .` | Pattern 1 | Medium — if sys.argv[0] is the .py entry point script rather than the binary wrapper, Popen would fail; mitigation: use `shutil.which("em-proj")` as fallback |
| A4 | `start_new_session=True` on macOS calls `setsid()` and detaches from the controlling terminal | Pattern 1 | Low — documented Python stdlib behavior; subprocess.Popen passes POSIX_SPAWN_SETPGROUP on macOS or calls setsid() directly |

**If A3 is wrong:** The daemon binary path should be resolved via `shutil.which("em-proj")` before `Popen`. The planner should add this as a verification step in the verb layer.

---

## Open Questions

1. **`sys.argv[0]` vs `shutil.which("em-proj")` for daemon entrypoint**
   - What we know: `EM_PROJ_BIN = "em-proj"` in conftest.py (line 31) — `em-proj` is on PATH after `uv tool install --editable .`
   - What's unclear: whether `sys.argv[0]` is the bare binary name or absolute path in all invocation contexts
   - Recommendation: use `shutil.which("em-proj") or "em-proj"` — explicit and robust

2. **Heartbeat test without real wait**
   - What we know: 60s heartbeat cadence would make a real-wait test unacceptable
   - What's unclear: whether the planner will implement `EM_PROJ_DAEMON_HEARTBEAT_INTERVAL` env override
   - Recommendation: implement the env override in Wave 1; test uses `EM_PROJ_DAEMON_HEARTBEAT_INTERVAL=1`

3. **`session stop` ownership check**
   - What we know: `session stop` should stop the current session's daemon
   - What's unclear: should `session stop` reject if a different session owns the daemon HASH? (In practice, each session_id has its own daemon HASH, so this is a non-issue)
   - Recommendation: implement `session stop` as self-stop only (uses `resolve_session_id()` as the HASH key); no cross-session stop verb needed in Phase 11

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Redis | Daemon heartbeat + pub/sub | Yes (existing requirement) | 7.x | N/A — Redis is a hard requirement |
| `em-proj` on PATH | Daemon self-re-invocation | Yes | Current dev install | N/A |
| `psutil` | `is_holder_stale` | Yes (existing dependency) | Installed | N/A |

**No new external dependencies required.**

---

## Sources

### Primary (HIGH confidence)
- `src/em_proj/message/_ops.py` (verified) — send patterns publish to `msg:<recipient>` at lines 514, 557-559, 609-611; `mbox_blocking_read` at lines 360-395; prohibited imports docstring at lines 46-48
- `src/em_proj/session/_ops.py` (verified) — `session_heartbeat()` at lines 303-340; `LUA_SESSION_HEARTBEAT` at lines 133-143; `TTL_DEFAULT=300` at line 68; prohibited imports docstring at lines 40-42
- `src/em_proj/identity.py` (verified) — `is_holder_stale()` at lines 262-315; `current_process_composite()` at lines 159-189
- `src/em_proj/state/lock.py` (verified) — `subprocess.Popen` pattern at lines 754-756; `start_new_session` context (not present — recommendation extends this); SIGTERM handler pattern at lines 727-741; `RefresherThread` pattern at lines 437-491
- `src/em_proj/redis_client.py` (verified) — `get_client()` singleton at lines 23-46; `decode_responses=True` at line 40
- `src/em_proj/session/__init__.py` (verified) — `session_app` mount, D-14 thin-wrapper contract, package-layout note at line 26
- `tests/conftest.py` (verified) — `subprocess.Popen` invariant docstring lines 9-18; `EM_PROJ_BIN = "em-proj"` line 31; `TEST_DB = 15` line 30
- `.planning/phases/11-listener-daemon/11-CONTEXT.md` (verified) — all locked decisions

### Secondary (MEDIUM confidence)
- `tests/multiprocess/test_harness_self.py` — pattern for multi-process tests
- `tests/multiprocess/test_session_registry.py` — session registration pattern for daemon tests
- `tests/multiprocess/test_message_delivery.py` — live-path skip-stub pattern for Phase 11 daemon dependency
- `tests/structural/test_phase_10_shape.py` — structural test pattern for Phase 11 shape tests

### Tertiary (LOW confidence — training knowledge, not verified via external tool)
- A1–A4 in Assumptions Log — redis-py PubSub get_message API behavior

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all existing dependencies, verified in source
- Architecture: HIGH — patterns grounded in verified codebase code paths
- Pitfalls: HIGH — derived from verified codebase constraints (fork+exec, singleton, Lua patterns)
- TEST-05 harness: HIGH — mirrors existing Phase 10 harness pattern exactly

**Research date:** 2026-06-08
**Valid until:** 2026-07-08 (stable — no external library churn; all deps already locked)
