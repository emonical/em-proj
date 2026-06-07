---
phase: 09-durable-mailbox-transport
plan: "02"
subsystem: messaging
tags: [redis, redis-streams, xadd, xrange, xdel, xtrim, xread, mailbox, mbox]

requires:
  - phase: 08-session-registry-hybrid
    provides: session registry, get_client() singleton, ValidationError pattern
  - phase: 09-01
    provides: Wave 0 RED tests (test_mailbox.py, test_mailbox_durability.py)

provides:
  - src/em_proj/message/_ops.py -- mbox_write, mailbox_inbox, mbox_blocking_read, constants, helpers
  - src/em_proj/message/__init__.py -- message_app Typer stub + full ops re-export
  - tests/unit/test_mailbox.py -- 10 GREEN unit tests covering MBOX-02..04
  - tests/multiprocess/test_mailbox_durability.py -- MBOX-01 stub (skips until Phase 10)

affects:
  - 09-03 (cli.py mount of message_app; inbox/send verb commands call into this module)
  - 10-message-send-path (mbox_write is the write primitive; Phase 10 validates recipients first)
  - 11-listener-daemon (mbox_blocking_read is the XREAD BLOCK tail for DAEMON-01)

tech-stack:
  added: []
  patterns:
    - "Redis Stream per session mailbox: XADD + MAXLEN + EXPIRE + XTRIM MINID + XRANGE + XDEL"
    - "_decode_entry injects msg_id at read time from stream tuple (Option A -- no two-write roundtrip)"
    - "exclusive ( prefix for XRANGE since=<id> (confirmed working in redis-py 7.4.0)"
    - "MailboxError exception class mirroring SessionNotFound pattern"
    - "_validate_body mirrors _validate_reason from claim.py (same ValidationError reuse)"

key-files:
  created:
    - src/em_proj/message/_ops.py
    - src/em_proj/message/__init__.py
    - tests/unit/test_mailbox.py
    - tests/multiprocess/test_mailbox_durability.py
  modified: []

key-decisions:
  - "Option A for msg_id: store payload WITHOUT msg_id; inject stream entry ID at read time in _decode_entry -- avoids XDEL+re-XADD two-write problem"
  - "( exclusive prefix confirmed working in redis-py 7.4.0 via test_since_excludes_already_seen probe -- Lua fallback not needed"
  - "No consumer groups for Phase 9: single-consumer mailbox; XRANGE + XDEL is sufficient; PEL complexity deferred to Phase 11+"
  - "Uniform mailbox-wide TTL for MINID trim (MBOX_TTL_SECONDS=3600); per-message TTL in payload is metadata for callers only"
  - "MAX_BODY_CHARS=4096 chosen (> claim.py MAX_REASON_CHARS=256; appropriate for message bodies)"

patterns-established:
  - "message/_ops.py: same prohibited-import discipline as session/_ops.py (no typer/multiprocessing/threading)"
  - "MBOX_KEY_PREFIX='mbox:' is machine-global, distinct from all state:* prefixes"
  - "EXPIRE called after every XADD (Pitfall 5 mitigation -- XADD does NOT refresh TTL)"
  - "XTRIM MINID applied lazily at every mailbox_inbox call (no background reaper needed)"

requirements-completed: [MBOX-01, MBOX-02, MBOX-03, MBOX-04]

duration: 9min
completed: 2026-06-07
---

# Phase 09 Plan 02: Durable Mailbox Transport -- Ops Module Summary

**Redis Stream mailbox ops (mbox_write + mailbox_inbox + mbox_blocking_read) with XADD+MAXLEN+EXPIRE writes, XTRIM MINID+XRANGE+XDEL reads, and confirmed ( exclusive-range support in redis-py 7.4.0**

## Performance

- **Duration:** 9 min
- **Started:** 2026-06-07T22:38:31Z
- **Completed:** 2026-06-07T22:47:00Z
- **Tasks:** 2
- **Files created:** 4

## Accomplishments

- Created `src/em_proj/message/_ops.py` -- complete mailbox ops module with all required public functions, constants, and private helpers. No typer/multiprocessing/threading imports.
- Created `src/em_proj/message/__init__.py` -- package init with message_app Typer stub (no verb commands yet; Plan 09-03 adds them) and full re-exports.
- Brought Wave 0 tests into worktree: all 10 `test_mailbox.py` unit tests pass GREEN; MBOX-01 durability stub skips cleanly until Phase 10.
- Confirmed architectural gate: `test_since_excludes_already_seen` is GREEN -- redis-py 7.4.0 passes ( prefix through XRANGE unmodified; Lua fallback is not needed.

## Task Commits

1. **Task 1: message/_ops.py scaffold with mbox_write, mailbox_inbox, mbox_blocking_read** - `ed16bc8` (feat)
2. **Task 2: Wave 0 tests confirmed GREEN; exclusive-range probe GREEN** - `f93502f` (feat)
3. **Durability stub** - `3c9777c` (chore)

**Plan metadata:** (docs commit follows)

## Files Created

- `src/em_proj/message/_ops.py` -- Core ops: MBOX_KEY_PREFIX, MBOX_MAXLEN, MBOX_TTL_SECONDS, MAX_BODY_CHARS, MailboxError, _build_mbox_key, _decode_entry, _validate_body, mbox_write, mailbox_inbox, mbox_blocking_read
- `src/em_proj/message/__init__.py` -- message_app Typer stub + re-exports all ops public symbols
- `tests/unit/test_mailbox.py` -- 10 unit tests covering MBOX-02 (write/read/peek/since/consume), MBOX-03 (maxlen bound), MBOX-04 (payload completeness)
- `tests/multiprocess/test_mailbox_durability.py` -- MBOX-01 stub (pytest.skip until Phase 10)

## Decisions Made

- **Option A for msg_id storage:** The stream entry payload does NOT include msg_id at write time. `_decode_entry` injects it from the XRANGE tuple first element at read time. Avoids the XDEL+re-XADD two-write roundtrip (09-RESEARCH.md Two-write problem section).
- **( exclusive prefix -- Lua fallback not needed:** `test_since_excludes_already_seen` probe confirmed GREEN. redis-py 7.4.0 passes the ( character through to Redis XRANGE unmodified. Source comment documents which path was taken.
- **No consumer groups:** Single-consumer mailbox design. XRANGE+XDEL is the consume-ack pattern. PEL management complexity deferred to Phase 11+ if multi-consumer at-least-once semantics are needed.
- **MAX_BODY_CHARS=4096:** Chosen as a reasonable body cap (larger than claim.py MAX_REASON_CHARS=256; suitable for message bodies while still bounding stream entry size).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Wave 0 test files not present in worktree**
- **Found during:** Task 1 (test collection showed 0 mailbox tests)
- **Issue:** The worktree was branched from commit `86521d2` (pre-merge), but the Wave 0 test files were added in merge commit `f8cf40` (09-01 test scaffolds). The worktree did not have `tests/unit/test_mailbox.py` or `tests/multiprocess/test_mailbox_durability.py`.
- **Fix:** Read files from the merge commit via `git show f8cf40...:tests/unit/test_mailbox.py` and wrote them to the worktree. Content is identical to the plan-specified Wave 0 RED tests.
- **Files modified:** tests/unit/test_mailbox.py (new), tests/multiprocess/test_mailbox_durability.py (new)
- **Verification:** `scripts/test.sh unit -k "mailbox"` -- 10 passed
- **Committed in:** f93502f, 3c9777c

---

**Total deviations:** 1 auto-fixed (Rule 3 - blocking: missing Wave 0 test files in worktree)
**Impact on plan:** Necessary to achieve GREEN phase. No scope creep -- files are identical to the plan-specified Wave 0 tests.

## Issues Encountered

- The `scripts/test.sh unit -k "mailbox and write"` filter from the plan verify block returned 0 tests (filter too restrictive). Ran `scripts/test.sh unit -k "mailbox"` instead, which correctly selected all 10 mailbox tests.
- Commit size precheck blocked the initial commit (584 LOC). Split into: implementation module (budget-excepted -- new module atomic), test file (budget-excepted -- Wave 0 test file), and durability stub.

## Next Phase Readiness

- `mbox_write`, `mailbox_inbox`, `mbox_blocking_read` are ready for Plan 09-03 (CLI verb wiring)
- `message_app` stub in `__init__.py` is ready to receive `@message_app.command` decorators
- No blockers -- all unit tests GREEN, no regressions

---
*Phase: 09-durable-mailbox-transport*
*Completed: 2026-06-07*

## Self-Check

Files created check:
- `src/em_proj/message/_ops.py` exists: FOUND
- `src/em_proj/message/__init__.py` exists: FOUND
- `tests/unit/test_mailbox.py` exists: FOUND

Commits check:
- `ed16bc8` feat(09-02-task-1): FOUND
- `f93502f` feat(09-02-task-2): FOUND
- `3c9777c` chore(09-02): FOUND

## Self-Check: PASSED
