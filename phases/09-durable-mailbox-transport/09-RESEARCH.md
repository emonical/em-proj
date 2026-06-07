# Phase 9: Durable Mailbox Transport - Research

**Researched:** 2026-06-07
**Domain:** Redis Streams vs Redis List as durable per-session mailbox; redis-py 7.4.0 API
**Confidence:** HIGH

---

## Summary

Phase 9 builds a per-session durable mailbox in Redis. The central decision is transport
structure: Redis Streams or Redis List. The four MBOX requirements demand ordered reads
(MBOX-02), a `--peek` mode that does not consume messages (MBOX-02), a `--since <id>`
resume cursor (MBOX-02), per-message ack/consume that marks a message read (MBOX-02),
bounded growth and per-message TTL (MBOX-03), and a structured record payload (MBOX-04).
Phase 11's listener daemon additionally requires efficient blocking reads to tail live traffic.

**Recommendation: Redis Streams.** Streams provide native ordered IDs, non-destructive
range reads (XRANGE for peek), cursor-based resume (the stream ID IS the cursor), explicit
consume-via-delete (XDEL), MAXLEN/MINID trimming for bounded growth, and XREAD BLOCK for
the Phase 11 daemon. Lists can do ordered append and BRPOP blocking, but they cannot peek
without consuming, cannot resume from a mid-list cursor, and provide no per-entry ID for
`--since`. Every MBOX requirement is a natural fit for Streams and a workaround for Lists.

**Primary recommendation:** Use a Redis Stream per session (`mbox:<session_id>`) with
plain XRANGE reads (no consumer groups), XDEL for consume-ack, MAXLEN trimming on XADD,
and a session-scoped TTL on the stream key. Consumer groups are out of scope for Phase 9
(single consumer per mailbox; the daemon in Phase 11 is still one reader).

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Durable message storage | Database/Storage (Redis) | — | Mailbox persists across session attach/detach; Redis owns durability |
| Inbox read (ordered, peek, since) | API/Backend (`_ops.py`) | CLI verb (`message/__init__.py`) | Business logic in ops; Typer verb is a thin translation layer per D-14 |
| Consume-ack (XDEL) | API/Backend (`_ops.py`) | — | Atomic delete lives in ops; CLI calls it |
| TTL + bounded growth | API/Backend (`_ops.py` on XADD + key EXPIRE) | — | Enforced at write time; no separate reaper needed for normal flow |
| Expired-entry cleanup (MINID reap) | API/Backend (`_ops.py` on inbox read) | — | Lazy trim on each read keeps the stream bounded without a daemon |
| Blocking tail (Phase 11) | API/Backend (`_ops.py` XREAD BLOCK) | — | Daemon calls ops; never talks to Redis directly |
| CLI surface (`message inbox`) | CLI verb (`message/__init__.py`) | — | Thin verb: parse flags, call ops, emit via output.py |

---

## Transport Decision: Redis Streams vs Redis List

### Decision matrix

| Capability | MBOX requirement | Redis Stream | Redis List |
|------------|------------------|--------------|------------|
| Ordered reads | MBOX-02 | Native: XRANGE returns entries in append order by ID [VERIFIED: context7/redis-py] | Native: LRANGE index 0 to -1 is insertion order [VERIFIED: context7/redis-py] |
| Peek (read without consuming) | MBOX-02 `--peek` | Native: XRANGE does not move a cursor or delete entries [VERIFIED: context7/redis-py] | Requires LRANGE — non-destructive but no stable ID per entry; to resume after peek you must track an index |
| Resume from ID (`--since <id>`) | MBOX-02 `--since` | Native: XRANGE min=`<id>` max=`+` returns all entries after that ID [VERIFIED: context7/redis-py] | Not supported: List has no per-entry ID; position changes when earlier entries are deleted; `--since` requires a separate index structure |
| Per-message ack/consume | MBOX-02 consume | XDEL by entry ID: O(1) delete of a specific entry [VERIFIED: context7/redis-py] | LREM or LPOP: LREM scans and removes by value (O(N)); LPOP/RPOP only removes head/tail, not an arbitrary entry |
| Bounded growth / trim | MBOX-03 | MAXLEN on XADD or XTRIM, both approximate and exact [VERIFIED: context7/redis-py] | LTRIM: exact, trims to index range, but trim-by-age requires a separate index |
| Per-message TTL | MBOX-03 | No native per-entry TTL. Stream key gets a key-level EXPIRE; age-based trim via MINID on stream ID timestamp [VERIFIED: context7/redis-py] | No native per-entry TTL; same as Streams — must approximate with LTRIM or scan |
| Key-level TTL (whole mailbox) | MBOX-03 | Standard EXPIRE on the stream key [VERIFIED: context7/redis-py] | Standard EXPIRE on the list key [VERIFIED: context7/redis-py] |
| Blocking read for daemon | Phase 11 DAEMON-01 | XREAD BLOCK: blocks until new entries arrive, then returns them with IDs for cursor tracking [VERIFIED: context7/redis-py] | BRPOP/BLPOP: blocks until an element is available, then pops it (destructive) [VERIFIED: context7/redis-py] |
| Forward compatibility (multiple readers) | Future | Consumer groups (XREADGROUP + XACK) add at-least-once delivery; fully additive [VERIFIED: context7/redis-py] | No equivalent; multi-consumer on a list requires coordination outside Redis |

### Verdict

Redis Streams wins on three MBOX-02 requirements that Lists cannot satisfy without
external scaffolding: peek-without-consume, stable per-entry IDs for `--since`, and
O(1) consume of an arbitrary entry by ID. Lists would require: (a) storing a JSON payload
as the list value and scanning by content for ack, or (b) maintaining a separate index
key mapping msg_ids to list positions that becomes stale on every LTRIM. Both paths are
fragile hand-rolls against a solved problem. [VERIFIED: context7/redis-py docs]

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis-py | 7.4.0 (installed) | All Redis operations: XADD, XRANGE, XDEL, XTRIM, EXPIRE | Already the project's only Redis client; `get_client()` returns a decode_responses=True client |
| typer | >=0.16 (project dep) | CLI verb wiring for `message inbox` | Matches every other verb family in the project |

[VERIFIED: uv.lock shows redis==7.4.0; pyproject.toml shows redis>=6.0,<8.0]

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| json (stdlib) | Python 3.12+ | Serialize `body` field and full message payload | Body is an arbitrary string; stored as a single JSON-encoded stream field |

### Installation
No new dependencies — all required capabilities are in the installed redis-py 7.4.0.

---

## Key Schema

### Stream key per mailbox

```
mbox:<session_id>
```

Example: `mbox:550e8400-e29b-41d4-a716-446655440000`

**Rationale:**
- `mbox:` prefix is distinct from `state:session:`, `state:claim:`, `state:lock:`,
  `state:reserve:` — no namespace collision.
- Machine-global (not project-scoped), matching the `state:session:` pattern established
  in Phase 8. Scope filtering is done at send time (Phase 10) by resolving recipients from
  the session registry, not by key structure.
- Session ID is the full CLAUDE_CODE_SESSION_ID (or pid-<pid> fallback), exactly as used
  in `state:session:<session_id>`. Building the mailbox key is a simple prefix substitution.

**Key builder (mirrors `_build_session_key`):**
```python
MBOX_KEY_PREFIX: str = "mbox:"

def _build_mbox_key(session_id: str) -> str:
    return MBOX_KEY_PREFIX + session_id
```

[ASSUMED: `mbox:` prefix has no collision with future namespaces — should be confirmed
by reviewing all existing prefixes: `state:session:`, `state:claim:`, `state:lock:`,
`state:reserve:`. All four are under the `state:` top-level; `mbox:` is safe.]

### Stream entry field layout

Each stream entry is a Redis field-value flat map (how Streams store data). Use a
single field `payload` whose value is a JSON-encoded string. This mirrors the lock.py
pattern of a single JSON blob and avoids the field-count multiplicity of storing each
MBOX-04 field as a separate stream field.

**Rationale for single-field JSON vs multi-field stream entry:**
- Stream entries as multi-field maps would store `from_session`, `pattern`, `scope`,
  `topic`, `body`, `sent_at`, `ttl` as separate Redis fields. This works but means:
  (a) every read must reconstruct the dict manually from alternating key/value pairs,
  (b) `topic` may be absent (optional field) requiring careful presence checks,
  (c) the schema is baked into field names with no versioning boundary.
- Single `payload` field with JSON: encodes the entire MBOX-04 record as one string,
  consistent with lock.py's approach, easy to evolve (add fields without changing the
  stream entry structure), decoded once in `_decode_entry`.

**MBOX-04 payload shape (JSON-encoded in the `payload` stream field):**
```python
{
    "msg_id": "<stream entry ID as string — e.g. '1717500000000-0'>",
    "from_session": "<session_id of sender>",
    "pattern": "<direct|broadcast|topic>",   # MSG-01/02/03 patterns
    "scope": "<project|upstream|machine>",   # MSG-04 scope
    "topic": "<topic string or null>",        # present for topic pattern only
    "body": "<message body string>",
    "sent_at": <float epoch>,
    "ttl": <int seconds>,
}
```

**Note on `msg_id`:** The msg_id IS the Redis stream entry ID (e.g., `1717500000000-0`).
It is embedded in the payload at write time for two reasons: (1) XRANGE returns the ID
separately from the field-value dict, so embedding it makes the payload self-contained for
consumers who deserialize just the JSON; (2) `--since <id>` passes the msg_id directly to
XRANGE as the `min` argument — no ID translation needed.

[VERIFIED: XRANGE returns `list[tuple[id, dict]]` per redis-py context7 docs; id is
the stream entry ID string]

---

## msg_id Generation

**Use Redis-native XADD auto-IDs (`id='*'`).** XADD returns the generated ID
(e.g., `1717500000000-0`). The write operation captures this return value and embeds
it back into the payload.

**Write flow:**
```python
# Source: Context7 redis-py XADD docs
payload_without_id = {
    "from_session": from_session,
    "pattern": pattern,
    "scope": scope,
    "topic": topic,
    "body": body,
    "sent_at": sent_at,
    "ttl": ttl,
}
mbox_key = _build_mbox_key(session_id)
# XADD returns the auto-generated ID
entry_id = client.xadd(
    mbox_key,
    fields={"payload": json.dumps(payload_without_id)},
    maxlen=MBOX_MAXLEN,
    approximate=True,
)
# entry_id is the msg_id; embed it into the stored payload
full_payload = {**payload_without_id, "msg_id": entry_id}
# Patch the stored entry with the complete payload (see Pitfall: Two-Write Problem)
```

**Two-write problem:** XADD must complete before we know the ID. If we need the ID
in the payload and the payload is the only stream field, we must either:
- (A) Accept that the stored payload lacks `msg_id` and return it from the write
  function separately (msg_id is returned by XADD, never read back from payload).
  Consumers get msg_id from the stream tuple `(id, fields)`, not from the payload JSON.
  This is simpler and avoids a second write.
- (B) Write a placeholder, get the ID, then XDEL + re-XADD with the correct ID.
  This is two roundtrips and fragile.

**Recommendation: Option A.** Store the payload without `msg_id`. The stream tuple
already carries the ID. `_decode_entry` extracts msg_id from the tuple's first element
and injects it into the decoded dict before returning it to the caller. The msg_id
field in MBOX-04 is satisfied by this injection at read time, not at write time.

```python
def _decode_entry(entry_id: str, fields: dict) -> dict:
    """Decode one stream entry tuple into the MBOX-04 record dict."""
    payload = json.loads(fields["payload"])
    payload["msg_id"] = entry_id   # inject stream ID as the canonical msg_id
    return payload
```

[VERIFIED: XADD return type is `bytes | str` (the entry ID) per context7/redis-py]

---

## Consumption / Ack Model

### Plain XRANGE + XDEL (no consumer groups)

**Why not consumer groups (XREADGROUP + XACK):** Consumer groups solve multi-consumer
fan-out with at-least-once delivery guarantees. This mailbox has ONE consumer per stream
(the owning session). Consumer groups add complexity (group creation, pending entry list
management, XAUTOCLAIM for crash recovery, group deletion on mailbox cleanup) with no
benefit for the single-consumer case. Consumer groups are a forward-compatibility path
that can be layered in Phase 11+ if multi-process at-least-once delivery semantics are
needed. [ASSUMED: Phase 9 maintains one logical consumer per mailbox — confirm if
Phase 11's daemon and the CLI inbox reader need to share state atomically.]

**Normal inbox flow (MBOX-02):**
```python
# Source: Context7 redis-py XRANGE docs
def mailbox_inbox(session_id: str, since: str | None = None, peek: bool = False) -> list[dict]:
    mbox_key = _build_mbox_key(session_id)
    min_id = since if since is not None else "-"   # "-" = earliest
    entries = client.xrange(mbox_key, min=min_id, max="+")
    # entries: list of (id, fields) tuples

    messages = [_decode_entry(eid, fields) for eid, fields in entries]

    if not peek:
        # Consume: delete each entry from the stream (ack = delete)
        ids_to_delete = [eid for eid, _ in entries]
        if ids_to_delete:
            client.xdel(mbox_key, *ids_to_delete)

    return messages
```

**`--peek` mode:** Call XRANGE but skip the XDEL step. The stream is unchanged.

**`--since <id>` mode:** Pass the last-seen msg_id as the XRANGE `min` argument.
XRANGE with `min=<id>` returns entries with ID GREATER THAN OR EQUAL TO that ID.
To get only strictly-newer entries, use `(` prefix notation: `min="(" + since`.
Note: `(` exclusive range is supported in Redis XRANGE since Redis 6.2.
[VERIFIED: Redis docs; ASSUMED: redis-py 7.4.0 passes the `(` prefix through as-is;
needs verification against the redis-py 7.4.0 XRANGE source.]

Alternative `--since` with strict exclusion using Lua: if exclusive range is not
confirmed via redis-py, a Lua script can do `XRANGE key (since +` in a server-side
call. This is the same atomicity pattern used throughout the codebase.

**Atomicity of inbox read-then-delete:** XRANGE + XDEL is NOT atomic by default.
A crash between XRANGE and XDEL would leave entries unconsumed (they appear read but
are still in the stream). For Phase 9 this is acceptable: the MBOX requirements do not
specify exactly-once delivery; at-most-once on crash is the agreed-on semantics (fire-
and-forget transport, not request/ack). If at-least-once is needed for Phase 11,
wrap read+delete in a Lua script (same pattern as LUA_SESSION_UPSERT). Flag in
Open Questions.

**Lua-atomized version (if needed):**
```lua
-- LUA_MBOX_CONSUME: XRANGE + XDEL as one atomic op
-- KEYS[1] = mbox key
-- ARGV[1] = min_id
-- ARGV[2] = max_id ("+")
-- ARGV[3] = count (or "0" for all)
local entries = redis.call('XRANGE', KEYS[1], ARGV[1], ARGV[2])
local ids = {}
for _, e in ipairs(entries) do
    table.insert(ids, e[1])
end
if #ids > 0 then
    redis.call('XDEL', KEYS[1], unpack(ids))
end
return entries
```

---

## TTL + Bounded Growth (MBOX-03)

### Two orthogonal problems

**Problem 1: Bounded count.** Prevent the stream from growing unboundedly if messages
accumulate (slow reader or dead session). Solution: `MAXLEN ~ N` on XADD trims the
stream to approximately N entries on every write. Approximate trim (`approximate=True`)
is more efficient than exact trim because Redis trims to macro-node boundaries.

```python
MBOX_MAXLEN: int = 500   # bounded at ~500 messages per session
```

[VERIFIED: XADD maxlen + approximate parameters per context7/redis-py]

**Problem 2: Per-message TTL (age-based expiry).** Redis Streams do NOT support
per-entry EXPIRE. The stream key can have a key-level EXPIRE (which expires the
entire mailbox), but individual entries cannot carry per-entry TTL at the Redis level.
[VERIFIED: no XEXPIRE command exists in Redis as of 7.x; per-entry expiry is a
Redis 8.x proposal, not yet available]

**How to honor per-message TTL:**
The stream entry ID encodes millisecond wall-clock time as its prefix (e.g.,
`1717500000000-0` = 1717500000 seconds epoch). This makes the ID itself a timestamp.

`XTRIM` with `MINID` removes all entries whose ID timestamp is older than a cutoff:
```python
# Trim entries older than TTL seconds
now_ms = int(time.time() * 1000)
cutoff_ms = now_ms - (ttl_seconds * 1000)
cutoff_id = f"{cutoff_ms}-0"
client.xtrim(mbox_key, minid=cutoff_id, approximate=True)
```

**When to run MINID trim:** Lazy trim at inbox read time. Each `mailbox_inbox` call:
1. Run MINID trim to evict entries older than the message TTL.
2. Run XRANGE to read remaining entries.
3. If not peek, XDEL consumed entries.

This avoids a background reaper process and stays within the short-lived CLI model.

**What TTL value to use for MINID trim:** Use the mailbox-level default TTL (e.g., 3600s
= 1 hour). Individual messages carry a `ttl` field in the payload, but per-entry trim
based on each message's individual TTL requires either a Lua scan or a sorted set index.
For Phase 9, apply a uniform mailbox-wide TTL for MINID trim (same value used for
key-level EXPIRE). Documents this as a simplification in MBOX-03. [ASSUMED: a uniform
mailbox TTL is acceptable; individual per-message TTL in the payload record is metadata
for callers, not enforced at the Redis trim level in Phase 9.]

**Key-level EXPIRE:** Set EXPIRE on the mailbox key itself to handle dead sessions
whose mailboxes are never read again. Set EXPIRE equal to the mailbox TTL. Refresh
EXPIRE on each write (XADD) to keep an active mailbox alive.

```python
MBOX_TTL_SECONDS: int = 3600   # 1-hour key-level TTL; refreshed on each XADD
MBOX_MAXLEN: int = 500         # count-based trim on each XADD
```

[VERIFIED: EXPIRE + XTRIM MINID semantics per context7/redis-py]

---

## Architecture Patterns

### System Architecture Diagram

```
[Phase 10 send path]          [Phase 11 daemon]        [CLI inbox command]
       |                             |                         |
       v                             v                         v
 mbox_write(session_id, msg)    XREAD BLOCK              mailbox_inbox(session_id,
       |                        mbox:<session_id>         peek=False/True,
       |                        last_id="$"               since=<id>)
       v                             |                         |
 XADD mbox:<session_id>             | new entries             v
   maxlen=500, approx               | arrive            XTRIM MINID (age trim)
   EXPIRE mbox:<session_id>         v                         |
   3600                        _decode_entry(id, fields)      v
                                    |                   XRANGE mbox:<session_id>
                                    v                   min=since or "-"
                               mailbox_write(...)       max="+"
                               (drain to durable               |
                                mailbox via same               v
                                XADD path)             if not peek:
                                                         XDEL consumed IDs
                                                               |
                                                               v
                                                        return list[MBOX-04 dict]
```

### Recommended Project Structure

```
src/em_proj/
├── message/              # New package — mirrors session/ package layout
│   ├── __init__.py       # message_app Typer + verb commands (inbox, etc.)
│   └── _ops.py           # Pure business logic: mbox_write, mailbox_inbox
├── session/              # Existing (Phase 8)
│   ├── __init__.py
│   └── _ops.py
└── cli.py                # Mount message_app (same as session_app mount)
```

### Pattern 1: XADD with MAXLEN + EXPIRE (mbox_write)

```python
# Source: Context7 redis-py XADD docs
def mbox_write(session_id: str, msg: dict) -> str:
    """Write a message to the session's mailbox. Returns the msg_id (stream entry ID)."""
    client = get_client()
    key = _build_mbox_key(session_id)

    payload = {
        "from_session": msg["from_session"],
        "pattern": msg["pattern"],
        "scope": msg["scope"],
        "topic": msg.get("topic"),      # None for non-topic patterns
        "body": msg["body"],
        "sent_at": msg["sent_at"],
        "ttl": msg["ttl"],
    }

    entry_id = client.xadd(
        key,
        fields={"payload": json.dumps(payload, default=str)},
        maxlen=MBOX_MAXLEN,
        approximate=True,
    )
    client.expire(key, MBOX_TTL_SECONDS)
    return entry_id  # This is the msg_id
```

### Pattern 2: XRANGE read with MINID trim + optional XDEL (mailbox_inbox)

```python
# Source: Context7 redis-py XRANGE, XTRIM, XDEL docs
def mailbox_inbox(
    session_id: str,
    since: str | None = None,
    peek: bool = False,
) -> list[dict]:
    client = get_client()
    key = _build_mbox_key(session_id)

    # Age-based trim: remove entries older than MBOX_TTL_SECONDS
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - (MBOX_TTL_SECONDS * 1000)
    client.xtrim(key, minid=f"{cutoff_ms}-0", approximate=True)

    # Range read: from 'since' exclusive if provided, else from beginning
    min_id = f"({since}" if since else "-"   # '(' = exclusive lower bound
    entries = client.xrange(key, min=min_id, max="+")

    messages = [_decode_entry(eid, fields) for eid, fields in entries]

    if not peek and entries:
        ids_to_delete = [eid for eid, _ in entries]
        client.xdel(key, *ids_to_delete)

    return messages
```

### Pattern 3: XREAD BLOCK for Phase 11 daemon (blocking tail)

```python
# Source: Context7 redis-py XREAD docs
# Phase 11 will call this — design is not an open question, just document it.
def mbox_blocking_read(session_id: str, last_id: str, block_ms: int = 5000) -> list[dict]:
    """Block until new entries arrive after last_id. Returns immediately if entries exist."""
    client = get_client()
    key = _build_mbox_key(session_id)
    result = client.xread(
        streams={key: last_id},
        count=10,
        block=block_ms,
    )
    if not result:
        return []
    # result shape: [[stream_name, [(id, fields), ...]]]
    _, entries = result[0]
    return [_decode_entry(eid, fields) for eid, fields in entries]
```

### Anti-Patterns to Avoid

- **Storing msg_id in the stream payload at write time:** XADD returns the ID after the
  write; a second XDEL + re-XADD to embed the ID adds two roundtrips and a window where
  the entry exists with an incomplete payload. Inject msg_id at read time in `_decode_entry`.

- **Using consumer groups for single-consumer mailboxes:** XREADGROUP + XACK + PEL
  management is necessary only for multi-consumer fan-out. Adding it for a per-session
  single-consumer mailbox multiplies complexity (group creation, XAUTOCLAIM, group
  expiry) with no benefit. Avoid in Phase 9.

- **Using LTRIM on a List for count-bounded TTL:** LTRIM trims by index, not by age.
  To approximate age-based TTL with a List, you would need to store timestamps outside
  the list or scan the entire list. This is the hand-rolled solution the Don't Hand-Roll
  section captures.

- **Importing typer in `_ops.py`:** Prohibited by the D-14 thin-verb-shell discipline.
  `message/_ops.py` must not import typer, multiprocessing, or threading (same rule as
  `session/_ops.py`).

- **Catching redis.ConnectionError in `_ops.py`:** Per D-18, connection-error handling
  belongs in the verb layer (`message/__init__.py`), which calls
  `die_if_redis_unreachable(client)` before any ops call.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Ordered persistent message queue | Custom HASH + sorted set index | Redis Stream (XADD) | Streams encode ordering in the ID; no external index needed |
| Peek without consuming | Read + re-write | XRANGE (non-destructive) | XRANGE never modifies the stream |
| Resume cursor | Offset integer + external mapping | Stream entry ID as cursor | IDs are stable until XDEL; XRANGE min=(id resumes at next entry |
| Consume specific entry by ID | LREM scan + value match | XDEL by entry ID | O(1) vs O(N); IDs are unique |
| Age-based trim | Cron job + scan + conditional XDEL | XTRIM MINID | ID prefix IS the timestamp; Redis does the comparison server-side |
| Bounded count | External size counter + conditional trim | XADD maxlen + approximate | Atomic at write time; ~N entries guaranteed |
| Blocking read for daemon | Polling loop with sleep() | XREAD BLOCK | Server-side park; 0 CPU overhead when idle |

**Key insight:** The Redis Stream ID (`<milliseconds>-<seq>`) is simultaneously a
unique message identifier, an ordering key, a resume cursor, and a timestamp. Building
any of these capabilities separately on top of a List is reinventing the Stream.

---

## Common Pitfalls

### Pitfall 1: XRANGE with `(` exclusive prefix and redis-py string passthrough
**What goes wrong:** `min="(" + since` relies on redis-py passing the `(` prefix to
Redis unmodified. If redis-py encodes or strips it, XRANGE becomes inclusive and the
caller re-reads the last already-seen message on every `--since` call.
**Why it happens:** redis-py 7.x passes string IDs as-is to Redis; the `(` prefix is
a Redis 6.2+ XRANGE feature. But if redis-py coerces the string to a numeric type
internally, the `(` is dropped.
**How to avoid:** In the ops unit tests, assert that `mailbox_inbox(since=last_id)` does
NOT return the entry with `msg_id == last_id`. If it does, the `(` prefix is being
stripped; fall back to a Lua XRANGE wrapper.
**Warning signs:** Test `test_since_excludes_already_seen` fails; duplicate messages
appear on repeated `--since` calls.

### Pitfall 2: XTRIM MINID with clock skew
**What goes wrong:** Stream entry IDs use `time.time()` (system clock). If the machine's
clock is stepped backward (NTP correction, virtualization), entries written "in the future"
get an ID with a high timestamp and survive MINID trim indefinitely.
**Why it happens:** Redis uses the server's system clock for auto-IDs; if server clock
steps back, the sequence counter increments to guarantee monotonicity, but IDs from before
the step may have out-of-order timestamps vs. wall time.
**How to avoid:** Use approximate=True on XTRIM MINID (allows Redis to be conservative).
The `MBOX_MAXLEN` count-based trim is the harder bound; MINID trim is a best-effort age
cleanup, not a security boundary.
**Warning signs:** `xrange key - +` shows entries older than MBOX_TTL_SECONDS; this is
cosmetic (they get count-trimmed) not functional.

### Pitfall 3: Empty mailbox vs expired mailbox key distinction
**What goes wrong:** Both an empty stream and an expired key cause XRANGE to return [].
A caller who interprets empty as "no messages" (correct) must also handle "key does not
exist" (stream was expired) gracefully.
**Why it happens:** `client.xrange(key, ...)` returns [] if the key is absent (expired)
and [] if the stream exists but has no entries in the range.
**How to avoid:** `mailbox_inbox` returns [] in both cases — this is correct behavior.
If callers need to distinguish "session has an active mailbox with 0 messages" from "session
has no mailbox at all," use `client.exists(key)`. But for MBOX-02 semantics, both are
"no messages to deliver" and the distinction is unnecessary.
**Warning signs:** Assertions like `assert messages is not None` pass trivially; the
meaningful assertion is `assert isinstance(messages, list)`.

### Pitfall 4: XDEL does not shrink XLEN immediately in all Redis versions
**What goes wrong:** `client.xlen(key)` after XDEL may still report the pre-delete count
for a brief window in some Redis internal implementations (tombstoning).
**Why it happens:** Redis uses macro-nodes; XDEL marks an entry as deleted but may not
compact until the node is fully empty.
**How to avoid:** Do not assert XLEN == 0 after consuming all messages; assert XRANGE
returns []. For bounded-growth tests, assert XLEN <= MBOX_MAXLEN + slack.
**Warning signs:** `test_consume_empties_mailbox` fails on `assert client.xlen(key) == 0`.

### Pitfall 5: XADD on a key with EXPIRE resets/extends the TTL only if explicitly called
**What goes wrong:** Calling `client.xadd(key, ...)` does NOT refresh the key's EXPIRE.
If `expire()` is not called explicitly after each XADD, an active mailbox that is being
written to can expire at the original TTL.
**Why it happens:** XADD creates the key if absent (with no TTL by default); subsequent
XADDs do not extend the TTL. Only an explicit `EXPIRE` or `PEXPIRE` call resets it.
**How to avoid:** Always call `client.expire(key, MBOX_TTL_SECONDS)` after every XADD.
This is a two-call pattern, not a single atomic operation. Accept the non-atomicity: if
the process dies between XADD and EXPIRE, the key inherits whatever TTL it had before (or
no TTL if it was just created). A Lua script can make this atomic if needed.
**Warning signs:** Mailbox keys for active sessions expire mid-test; `clean_db.ttl(key)`
returns -1 after an XADD without a follow-up EXPIRE.

### Pitfall 6: `mbox_write` called from Phase 10 for a session that has no mailbox yet
**What goes wrong:** If a session has never run `session register`, its mailbox key
doesn't exist. XADD creates it. This is actually correct behavior — XADD auto-creates
the stream. However, the caller might expect a "recipient not found" error for unregistered
sessions.
**Why it happens:** Redis auto-creates stream keys on XADD.
**How to avoid:** Phase 10's send-path should validate the recipient session exists in
`state:session:<session_id>` before writing to the mailbox. This is a Phase 10 concern;
Phase 9's `mbox_write` should be agnostic and always write (let the session registry
check happen upstream).
**Warning signs:** Messages accumulate in `mbox:<id>` for session IDs that have no
corresponding `state:session:<id>` entry.

---

## Code Examples

### Verified patterns from official sources

#### XADD with maxlen and approximate
```python
# Source: Context7 redis-py XADD docs
entry_id = client.xadd(
    "mbox:my-session",
    fields={"payload": '{"from_session": "abc", "body": "hello"}'},
    maxlen=500,
    approximate=True,
)
# entry_id: "1717500000000-0" (string; this IS the msg_id)
client.expire("mbox:my-session", 3600)
```

#### XRANGE for ordered read
```python
# Source: Context7 redis-py XRANGE docs
entries = client.xrange("mbox:my-session", min="-", max="+")
# entries: [("1717500000000-0", {"payload": '{"from_session": "abc", ...}'}), ...]
for entry_id, fields in entries:
    msg = json.loads(fields["payload"])
    msg["msg_id"] = entry_id   # inject at read time
```

#### XDEL for consume-ack
```python
# Source: Context7 redis-py XDEL docs
ids_to_delete = [eid for eid, _ in entries]
deleted_count = client.xdel("mbox:my-session", *ids_to_delete)
```

#### XTRIM MINID for age-based cleanup
```python
# Source: Context7 redis-py XTRIM docs
now_ms = int(time.time() * 1000)
cutoff_ms = now_ms - (3600 * 1000)   # 1 hour in milliseconds
client.xtrim("mbox:my-session", minid=f"{cutoff_ms}-0", approximate=True)
```

#### XREAD BLOCK for daemon tail (Phase 11 reference pattern)
```python
# Source: Context7 redis-py XREAD docs
result = client.xread(
    streams={"mbox:my-session": "1717500000000-0"},  # last seen ID
    count=10,
    block=5000,   # 5 second timeout; returns [] on timeout
)
if result:
    stream_name, entries = result[0]
    for entry_id, fields in entries:
        msg = json.loads(fields["payload"])
        msg["msg_id"] = entry_id
```

#### BRPOP vs XREAD BLOCK comparison (why XREAD wins)
```python
# BRPOP (List): destructive, no ID, no cursor
popped = client.brpop("list:my-session", timeout=5)
# Returns (key, value) or None — value is consumed, no resume possible

# XREAD BLOCK (Stream): non-destructive until XDEL, has ID, resumable
result = client.xread(streams={"mbox:my-session": "$"}, block=5000)
# Returns entries with IDs; stream unchanged until explicit XDEL
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Redis List + BRPOP for mailbox | Redis Stream + XREAD BLOCK | Redis 5.0 (2018) | Streams add IDs, ordering, non-destructive reads, consumer groups |
| Consumer groups for single reader | Plain XRANGE + XDEL | Always valid | Consumer groups are for multi-consumer; single-consumer doesn't need PEL overhead |
| Per-entry TTL via sorted set | XTRIM MINID on stream | Redis 6.2 (2021) | MINID uses stream ID timestamp; no external sorted set needed |

**Deprecated/outdated:**
- List-based mailboxes: still valid for simple push/pop but cannot satisfy MBOX-02 peek
  and --since requirements without external scaffolding.
- XGROUP-first approach: common pattern in tutorials assumes multi-consumer; for this
  mailbox, it's overkill.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `mbox:` prefix has no collision with existing or planned namespaces | Key Schema | Low: all existing prefixes are under `state:`; `mbox:` is safe by inspection |
| A2 | Phase 9 has one logical consumer per mailbox (no concurrent inbox readers) | Consumption / Ack Model | Medium: if CLI and daemon both call `mailbox_inbox` concurrently, XRANGE + XDEL race; need Lua atomization |
| A3 | A uniform mailbox-wide TTL for MINID trim is acceptable (individual per-message TTL not enforced at Redis level) | TTL + Bounded Growth | Low: per-message TTL is stored in payload for caller inspection; Redis-level enforcement uses uniform TTL |
| A4 | redis-py 7.4.0 passes the `(` exclusive range prefix to Redis XRANGE unmodified | Consumption / Ack Model (Pattern 2) | Medium: if stripped, --since returns duplicate; need unit test to verify or fall back to Lua |
| A5 | XADD with `nomkstream=False` (default) auto-creates the stream key | mbox_write pattern | Low: XADD always auto-creates; this is Redis core behavior, not a redis-py decision |

---

## Open Questions (RESOLVED)

1. **XRANGE exclusive prefix (`(`) support in redis-py 7.4.0**
   - What we know: Redis 6.2+ supports `(` prefix for exclusive range in XRANGE
   - What's unclear: Whether redis-py 7.4.0 passes the `(` through as a string or strips it
   - RESOLVED: Write a unit test in Wave 0 that asserts `--since <id>` excludes
     the exact entry at `<id>`; if it fails, wrap in Lua. (Plan 09-01 `test_since_excludes_already_seen`
     is the probe; Plan 09-02 Task 2 gates the `(`-vs-Lua path on its result.)

2. **Atomicity of XRANGE + XDEL for consume-ack**
   - What we know: Non-atomic; crash between XRANGE and XDEL loses the consumed entry
   - What's unclear: Whether Phase 9 requires at-most-once or at-least-once delivery
   - RESOLVED: Start with non-atomic (simpler, matches fire-and-forget semantics);
     add Lua atomization in Phase 11 if the daemon's drain-to-mailbox loop needs
     crash-safe consume.

3. **Concurrent inbox readers (CLI + daemon both calling mailbox_inbox)**
   - What we know: Phase 11 daemon drains pub/sub to mailbox; CLI inbox reads the mailbox
   - What's unclear: Whether they can call inbox concurrently
   - RESOLVED: Design Phase 9 ops as if single-reader; document the concurrent
     reader race in pitfalls; Phase 11 will resolve the coordination model.

4. **`mbox_write` recipient validation**
   - What we know: XADD auto-creates the stream for any session_id
   - What's unclear: Phase 10's responsibility for checking recipient existence
   - RESOLVED: Phase 9 `mbox_write` is a pure write primitive (no registry check);
     Phase 10 validates recipients against the session registry before calling mbox_write.

---

## Environment Availability

Step 2.6: SKIPPED — Phase 9 has no new external dependencies beyond the already-confirmed
redis-py 7.4.0 and Redis server from Phase 8.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `bash scripts/test.sh unit` |
| Full suite command | `bash scripts/test.sh all` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MBOX-01 | Mailbox persists messages for offline sessions | multiprocess | `bash scripts/test.sh multiprocess` | No — Wave 0 |
| MBOX-02 | inbox reads in order; `--peek` non-consuming; `--since` resumes | unit + multiprocess | `bash scripts/test.sh unit` | No — Wave 0 |
| MBOX-03 | TTL expiry, MAXLEN trim, MINID age trim | unit | `bash scripts/test.sh unit` | No — Wave 0 |
| MBOX-04 | Message record carries all 8 fields | unit | `bash scripts/test.sh unit` | No — Wave 0 |

### Key test scenarios

**Durability (MBOX-01):** Write a message when "offline" (no receiver listening), then
read it back via `mailbox_inbox`. Assert the message is present. Multiprocess: use
`clean_db` fixture (db=15); write via direct `mbox_write` API call; read via CLI
`em-proj message inbox --json`.

**Ordered reads (MBOX-02):** Write N messages; assert XRANGE returns them in ascending
ID order. Assert the `msg_id` fields in the returned dicts are monotonically increasing.

**Peek non-consuming (MBOX-02):** Write 3 messages; call `mailbox_inbox(peek=True)`;
call `mailbox_inbox(peek=False)`; assert the second call returns the same 3 messages.
Then call again; assert empty.

**Since resume (MBOX-02):** Write msg_A and msg_B; call inbox (consumes both, returns
[A, B]); write msg_C; call inbox with `since=msg_B["msg_id"]`; assert returns [C] only.
**Note:** This tests the exclusive `(` prefix. If XRANGE with `(` is not supported via
redis-py string passthrough, this test will also return msg_B (duplicate). Use this test
to verify the exclusive-range behavior and gate the `(` prefix or Lua fallback.

**MAXLEN bound (MBOX-03):** Write `MBOX_MAXLEN + 100` messages; assert
`client.xlen(mbox_key) <= MBOX_MAXLEN + slack` (approximate trim allows slight overage).

**MINID age trim (MBOX-03):** Write a message; manually set the stream key with an old-
timestamp entry by writing a message and then directly patching the ID via Lua (or write
at time T, then pass a future-biased cutoff to the trim call). Assert the old entry is
removed after the trim step.

**Key EXPIRE (MBOX-03):** Write a message; set `client.expire(mbox_key, 1)` (force-expire);
sleep 2s; assert `mailbox_inbox` returns [].

**MBOX-04 payload completeness:** Write one message with all 8 fields; read back; assert
all 8 fields present and correctly typed.

### Structural tests

`tests/structural/test_phase_09_shape.py` should assert:
- `src/em_proj/message/_ops.py` exists
- `src/em_proj/message/__init__.py` exists
- `MBOX_KEY_PREFIX = "mbox:"` present in source
- `def mbox_write` in `_ops.py`
- `def mailbox_inbox` in `_ops.py`
- `typer`, `multiprocessing`, `threading` NOT in `_ops.py` import lines
- `message_app` referenced >= 2 times in `cli.py`
- Every `09-*-PLAN.md` has a matching `09-*-SUMMARY.md`

### Sampling Rate
- Per task commit: `bash scripts/test.sh unit`
- Per wave merge: `bash scripts/test.sh all`
- Phase gate: Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/unit/test_mailbox.py` — covers MBOX-02, MBOX-03, MBOX-04 (unit, no subprocess)
- [ ] `tests/multiprocess/test_mailbox_durability.py` — covers MBOX-01 (CLI boundary)
- [ ] `tests/structural/test_phase_09_shape.py` — structural assertions
- (No new framework install needed — pytest already present)

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | — |
| V3 Session Management | no | — |
| V4 Access Control | partial | Session ownership: mailbox key is `mbox:<session_id>`; any caller who knows the session_id can read/write. Trust boundary is the same as the session registry (machine-local; same user). |
| V5 Input Validation | yes | `body` field: no length cap defined yet (ASSUMED: needs a MAX_BODY_CHARS constant mirroring MAX_REASON_CHARS in claim.py). `from_session`, `topic` similarly need caps. |
| V6 Cryptography | no | — |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Mailbox flooding (unbounded XADD) | Denial of Service | MAXLEN=500 on XADD; EXPIRE on key |
| Replay (re-reading consumed messages) | Tampering | XDEL removes on consume; --peek is explicit |
| Message injection to wrong session | Spoofing | Phase 10's send path validates sender; Phase 9 mbox_write is internal-only |
| Oversized body | Tampering / DoS | MAX_BODY_CHARS validation in mbox_write (same pattern as claim.py MAX_REASON_CHARS) |

---

## Sources

### Primary (HIGH confidence)
- Context7 `/redis/redis-py` v6_4_0 — XADD, XREAD, XRANGE, XTRIM, XDEL, BRPOP, BLPOP,
  consumer group commands (xreadgroup, xack), TTL commands — all verified via context7 CLI
- `/Users/emonical/projects/personal/ai-tools/em-proj/uv.lock` — confirmed redis-py 7.4.0
- `/Users/emonical/projects/personal/ai-tools/em-proj/pyproject.toml` — confirmed redis>=6.0,<8.0
- `src/em_proj/session/_ops.py` — Lua pattern, key schema, HASH layout, TTL conventions
- `src/em_proj/state/claim.py` — Lua pattern, error conventions, D-18 connection handling
- `src/em_proj/redis_client.py` — decode_responses=True, socket timeouts, singleton pattern
- `src/em_proj/cli.py` — `app.add_typer` mount pattern for new subcommand families
- `tests/multiprocess/test_session_registry.py` — multiprocess harness pattern, subprocess.Popen, clean_db usage

### Secondary (MEDIUM confidence)
- REQUIREMENTS.md, ROADMAP.md, STATE.md — Phase 9 scope, success criteria, forward constraints
- Phase 8 structural test (test_phase_08_shape.py) — structural test conventions for Phase 9

### Tertiary (LOW confidence)
- [ASSUMED A1-A5] — documented in Assumptions Log above

---

## Metadata

**Confidence breakdown:**
- Transport decision (Streams vs List): HIGH — verified against redis-py context7 docs
- Key schema and field layout: HIGH — mirrors verified existing patterns
- msg_id generation via XADD return value: HIGH — XADD return type confirmed
- XTRIM MINID for age trim: HIGH — verified in context7 docs
- XRANGE exclusive `(` prefix support in redis-py 7.4.0: MEDIUM — Redis feature is documented; redis-py passthrough unverified; flagged in Open Questions
- Consumer group exclusion rationale: HIGH — single-consumer case is well-understood
- Security / input validation: MEDIUM — threat patterns are assumed standard; ASVS mapping is applied judgment

**Research date:** 2026-06-07
**Valid until:** 2026-09-07 (stable ecosystem; redis-py and Redis Streams API are not fast-moving)
