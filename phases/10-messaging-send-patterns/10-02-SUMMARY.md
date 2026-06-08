# 10-02 SUMMARY — Wave 1: message/_ops.py send/subscribe layer

**Plan:** 10-02-PLAN.md (Phase 10, Wave 1)
**Status:** Complete
**Requirements:** MSG-01, MSG-02, MSG-03, MSG-04, MSG-05

## What was built

Extended `src/em_proj/message/_ops.py` (Phase 9 base) with the full Phase 10
send/subscribe business logic — turning the Wave 0 RED unit tests GREEN. No CLI
code (Wave 2). All locked design decisions implemented exactly.

**Task 1 — constants / regex / private helpers + imports:**
- New top-level imports: `import redis as _redis`; `from em_proj.identity import
  resolve_project_hash, resolve_session_id, resolve_upstream_identity`;
  `from em_proj.session._ops import SessionNotFound, session_list, session_show`.
- `TOPIC_KEY_PREFIX = "topic:"`; `_TOPIC_RE = ^[a-zA-Z0-9_.\-]{1,128}$`.
- `_validate_topic`, `_build_topic_key`, `_resolve_scope_key` (allowlist
  machine/project/upstream; raises ValidationError otherwise).
- Module docstring updated with Phase 10 fields + `pipeline` added to the
  prohibited-imports line.

**Task 2 — scope enumeration + topic membership:**
- `enumerate_scope_recipients(scope, exclude_session_id=None)` — filters
  `session_list()` (already stale-filtered) by project_hash/upstream_identity;
  excludes `exclude_session_id` only when provided.
- `subscribe_topic` / `unsubscribe_topic` — SADD/SREM on `topic:<scope_key>:<topic>`.
- `get_topic_subscribers` — SMEMBERS → `set[str]`.

**Task 3 — send patterns:**
- `send_directed(to, body, scope)` — `session_show` existence check
  (SessionNotFound propagates), `mbox_write`, fire-and-forget PUBLISH; returns
  flat metadata dict.
- `send_broadcast(body, scope)` — fan-out to live scope recipients excluding
  sender; plain loop with `(_redis.ConnectionError, _redis.TimeoutError)`
  counting → `recipients_failed` (exit-4 signal).
- `send_topic(topic, scope, body)` — `_validate_topic` first; subscribers ∩ live
  sessions (sender excluded); same fan-out/partial-failure handling.
- All three return `{recipients_written, recipients_failed, pub_published,
  pattern, scope}` — all scalars.

## Files changed

| File | Change | LOC |
|------|--------|-----|
| `src/em_proj/message/_ops.py` | extend (3 commits) | +81 / +80 / +147 |
| `tests/unit/test_message_send.py` | fix (collision-proof IDs + enumerate contract) | +10 / −4 |

Commits (phase branch): `f19c597` (Task 1), `51038b6` (Task 2), `cc69570`
(Task 3), `996f6f5` (test fix).

## Verification results

| Verify command | Expected | Observed |
|----------------|----------|----------|
| `test.sh unit -k test_message_send` | all GREEN | ✓ **18 passed** |
| `test.sh unit` | no regression | ✓ **336 passed** |
| `test.sh structural -k phase_10` | A/B/C/D/G pass; E/F fail | ✓ 5 passed, 2 failed (E=CLI verbs Wave 2, F=SUMMARYs) |

## Deviations

- **`_unique_session_id()` hardened with a uuid4 suffix** (test-only). The
  verbatim Phase 8 helper (`pid + time.time_ns()`) collides on rapid successive
  calls under macOS's coarse realtime-clock granularity, collapsing two distinct
  test sessions onto one Redis key — the broadcast test saw 1 recipient instead
  of 2. Root-caused as test-infra flakiness, not an ops bug (`session_list` keys
  per session_id, never dedupes by pid). Recommend lifting the same fix into
  `test_session_registry.py`'s helper and `test_message_delivery.py` (Wave 2).
- **`test_enumerate_scope_recipients_excludes_sender` corrected** to pass
  `exclude_session_id` explicitly — `enumerate_scope_recipients` excludes the
  caller only via that param (the contract `send_broadcast`/`send_topic` use); it
  does not auto-exclude `resolve_session_id()`. The Wave 0 test had assumed
  auto-exclusion.

## Self-Check: PASSED

All 7 new public functions + `TOPIC_KEY_PREFIX` present; `test_message_send`
18/18 GREEN; full unit suite 336/336 GREEN (no regression); structural A
(functions), B (prefix), C (no forbidden imports), D (no pipeline), G (value
returns) all pass. E (CLI verbs) and F (SUMMARYs) remain RED for Wave 2 — correct.
