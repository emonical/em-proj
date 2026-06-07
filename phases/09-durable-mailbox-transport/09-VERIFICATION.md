---
phase: "09-durable-mailbox-transport"
verified: "2026-06-07"
verdict: PASS
goal_delivered: true
requirements: ["MBOX-01", "MBOX-02", "MBOX-03", "MBOX-04"]
deterministic_checks: all_pass
tests: "453 passed, 7 skipped (test.sh all); 102 passed, 6 skipped (structural)"
---

# Phase 9 — Durable Mailbox Transport — Verification

**Goal:** Every session has a durable per-recipient mailbox that holds messages until the
session reads them — even if the session was offline when the message was sent.

**Verdict: PASS — goal delivered at the Phase 9 boundary.**

## Deterministic checks (scripts/verify-phase.sh 09)

| Check | Result |
|-------|--------|
| `scripts/test.sh all` | PASS — 453 passed, 7 skipped |
| `scripts/test.sh structural` | PASS — 102 passed, 6 skipped |
| Redis backend (AOF/appendfsync/save) | PASS |
| `em-proj` on PATH + `--version` | PASS (0.1.0) |
| Anti-pattern markers (TBD/FIXME/XXX/HACK/TODO/PLACEHOLDER) | PASS — none in src/ tests/ scripts/ |
| Plan↔SUMMARY coverage | PASS — 09-01, 09-02, 09-03 all have summaries |
| Commit traceability | PASS — every `(09-NN)` change committed atomically |

## Requirement coverage (goal-backward)

| Req | Delivered by | Evidence |
|-----|--------------|----------|
| **MBOX-01** — durable per-recipient mailbox persists for offline sessions | `mbox_write` → Redis Stream `mbox:<session_id>` | XADD persists independent of whether the recipient session is attached; the stream is the durable store. Cross-process offline-send→read E2E is a documented skip-stub pending Phase 10's `message send` verb (correct phase boundary). |
| **MBOX-02** — `message inbox [--peek] [--since <id>]` reads in order; consume marks read | `inbox` verb + `mailbox_inbox` | XRANGE ascending order; `--peek` skips XDEL (non-consuming); default consumes via XDEL; `--since <id>` uses exclusive `(` lower bound — confirmed working in redis-py 7.4.0 by `test_since_excludes_already_seen` (no Lua fallback needed). |
| **MBOX-03** — TTL + bounded growth | `mbox_write` + `mailbox_inbox` | XADD `maxlen=500` (count bound), `EXPIRE 3600` refreshed per write, lazy `XTRIM MINID` age-trim at read. |
| **MBOX-04** — 8-field record | `_decode_entry` | Injects `{msg_id (stream id), from_session, pattern, scope, topic?, body, sent_at, ttl}` at read time. |

## Locked design honored

- Transport = **Redis Streams** (XADD/XRANGE/XTRIM/XDEL/XREAD); **no consumer groups** — enforced by `test_uses_streams_not_list`, now AST-based (asserts no `xreadgroup`/`xack` *call sites*, allowing the explanatory docstring).
- `msg_id` = stream entry ID injected at read time (not stored in payload).
- `mbox_blocking_read` (XREAD BLOCK) shipped now as the stable interface Phase 11's listener daemon will call.

## Deferred to later phases (by design, not gaps)

- **Phase 10:** `message send` verb (write side) → unblocks the MBOX-01 multiprocess durability E2E test (currently a clean skip-stub).
- **Phase 10:** recipient-existence validation before write (Phase 9 `mbox_write` is a pure write primitive).
- **Phase 11:** concurrent-reader coordination + optional Lua atomization of XRANGE+XDEL for at-least-once delivery (Phase 9 is single-reader, at-most-once by design).

## Next phase

→ **Phase 10: Messaging Send Patterns** (MSG-01..05, TEST-04) — builds the send side on top of this mailbox and enables the deferred durability E2E test.
