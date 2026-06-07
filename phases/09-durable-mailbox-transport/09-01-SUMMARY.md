---
phase: "09"
plan: "01"
subsystem: message
tags: [tdd, red-phase, mailbox, tests, redis-streams]
dependency_graph:
  requires: []
  provides:
    - tests/unit/test_mailbox.py
    - tests/structural/test_phase_09_shape.py
    - tests/multiprocess/test_mailbox_durability.py
  affects:
    - Phase 09 Wave 0 test contract (MBOX-01..04 Nyquist map)
    - Plan 09-02 implementation decisions (exclusive-range probe gates Lua fallback)
tech_stack:
  added: []
  patterns:
    - TDD RED phase: test files created before implementation module exists
    - Autouse fixture pattern from test_session.py (db=15 isolation)
    - Structural shape test pattern from test_phase_08_shape.py
    - Multiprocess stub with pytest.skip for Phase 10 dependency
key_files:
  created:
    - tests/unit/test_mailbox.py
    - tests/structural/test_phase_09_shape.py
    - tests/multiprocess/test_mailbox_durability.py
  modified: []
decisions:
  - "test_since_excludes_already_seen: if it fails in GREEN, redis-py 7.4.0 does not pass paren prefix through XRANGE; Plan 09-02 must use Lua fallback"
  - "test_mailbox_durability.py stubs with pytest.skip to keep MBOX-01 in Nyquist map without blocking Phase 9 on Phase 10 dependency"
  - "ValidationError import from em_proj.state.kv (same class as claim.py)"
  - "test_consume_removes_from_stream asserts via XRANGE not xlen per 09-RESEARCH.md Pitfall 4"
metrics:
  duration: "~15 minutes"
  completed: "2026-06-07"
  tasks_completed: 2
  tasks_total: 2
  files_created: 3
  files_modified: 0
---

# Phase 9 Plan 01: Wave 0 Test Scaffolds Summary

Three test files establish the Nyquist test contract for MBOX-01..04 before any
implementation is written. All three confirm RED phase via ModuleNotFoundError
on em_proj.message._ops.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | tests/unit/test_mailbox.py unit scaffold | 5714e52 | tests/unit/test_mailbox.py |
| 2 | Structural shape and durability test scaffolds | 1c7a6b9 | tests/structural/test_phase_09_shape.py, tests/multiprocess/test_mailbox_durability.py |

## Test Functions Created

### tests/unit/test_mailbox.py (10 functions)

| Test | Requirement |
|------|-------------|
| test_import_smoke | RED phase probe |
| test_mbox_write_returns_msg_id | MBOX-02 write |
| test_mailbox_inbox_ordered_reads | MBOX-02 ordered reads |
| test_peek_does_not_consume | MBOX-02 peek |
| test_since_excludes_already_seen | MBOX-02 since + exclusive-range probe |
| test_consume_removes_from_stream | MBOX-02 consume |
| test_mbox_maxlen_bound | MBOX-03 MAXLEN trim |
| test_mbox_payload_has_all_eight_fields | MBOX-04 payload completeness |
| test_topic_field_is_none_for_non_topic_pattern | MBOX-04 topic field |
| test_max_body_chars_enforced | MBOX-04 + body validation |

### tests/structural/test_phase_09_shape.py (8 functions)

| Test | Checks |
|------|--------|
| test_message_ops_module_exists_and_has_required_functions | File + required function names |
| test_message_init_exists | message/__init__.py exists |
| test_mbox_key_prefix_is_mbox_colon | MBOX_KEY_PREFIX = mbox: in source |
| test_message_ops_prohibits_forbidden_imports | No typer/multiprocessing/threading |
| test_message_app_wired_in_cli | cli.py has message_app >= 2 times |
| test_message_init_has_inbox_command | @message_app.command >= 1 in __init__.py |
| test_uses_streams_not_list | xadd+xrange present; xreadgroup/xack absent |
| test_phase_09_summaries_exist | Every 09-*-PLAN.md has a SUMMARY sibling |

### tests/multiprocess/test_mailbox_durability.py (1 function)

| Test | Status |
|------|--------|
| test_mailbox_persists_for_offline_session | Skips: Phase 10 dependency |

## Deviations from Plan

None - plan executed exactly as written.

## RED Phase Verification

- scripts/test.sh unit -k mailbox: exits non-zero with ModuleNotFoundError on em_proj.message
- scripts/test.sh structural: 7 failures for missing Phase 9 files; SUMMARY check skips (planning worktree not attached)
- scripts/test.sh multiprocess: durability test skips cleanly; all 40 prior tests pass

## Known Stubs

tests/multiprocess/test_mailbox_durability.py: test_mailbox_persists_for_offline_session is
intentionally stubbed with pytest.skip pending Phase 10 message send verb.

## Threat Flags

None - test-only files with no new network endpoints, auth paths, or schema changes.

## Self-Check: PASSED

- tests/unit/test_mailbox.py exists
- tests/structural/test_phase_09_shape.py exists
- tests/multiprocess/test_mailbox_durability.py exists
- Commit 5714e52 exists (Task 1)
- Commit 1c7a6b9 exists (Task 2)
