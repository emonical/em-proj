# 10-01 SUMMARY — Wave 0: RED test scaffolds

**Plan:** 10-01-PLAN.md (Phase 10, Wave 0)
**Status:** Complete
**Requirements:** MSG-01, MSG-02, MSG-03, MSG-04, MSG-05, TEST-04 (test scaffolds only)

## What was built

Four test files laying the RED automated-verify surface for every Phase 10
acceptance criterion, per the Nyquist rule (verify commands defined before
implementation). Waves 1 (10-02) and 2 (10-03) turn these GREEN.

1. **`tests/unit/test_message_send.py`** (CREATE) — 14 unit test functions (one
   parametrized over 4 scope values) against the not-yet-existing ops layer.
   Module-level import of `send_directed`/`send_broadcast`/`send_topic`/
   `subscribe_topic`/`unsubscribe_topic`/`enumerate_scope_recipients`/
   `TOPIC_KEY_PREFIX` → ImportError at collection until Wave 1. Autouse fixture
   pair copied verbatim from `test_mailbox.py`; session helpers copied verbatim
   from `test_session_registry.py`.

2. **`tests/structural/test_phase_10_shape.py`** (CREATE) — 7 structural tests
   (A–G): ops-function presence, `TOPIC_KEY_PREFIX='topic:'`, forbidden imports
   absent, no `client.pipeline()` (AST), `message_app` send/broadcast/subscribe/
   unsubscribe verbs, SUMMARY coverage, send-ops value-return guard.

3. **`tests/multiprocess/test_message_delivery.py`** (CREATE) — TEST-04 3×3
   matrix: 9 mailbox-path cells (skip-stubbed for Wave 2) + 3 live-path cells
   (skip-stubbed for Phase 11), plus the `subprocess.Popen`/`communicate(timeout=15)`
   harness helpers Wave 2 fills bodies against.

4. **`tests/multiprocess/test_mailbox_durability.py`** (ACTIVATE) — replaced the
   skip-stub with the real MBOX-01 offline-durability body.

## Files changed

| File | Change | LOC |
|------|--------|-----|
| `tests/unit/test_message_send.py` | create | +323 |
| `tests/structural/test_phase_10_shape.py` | create | +166 |
| `tests/multiprocess/test_message_delivery.py` | create | +190 |
| `tests/multiprocess/test_mailbox_durability.py` | activate | +108 / −26 |

Commits (phase branch `gsd/phase-10-messaging-send-patterns`):
`aa75106` (unit), `4ed4fd4` (structural), `c5cbe20` (delivery harness),
`4595364` (durability activation).

## Verification results

| Verify command | Expected RED state | Observed |
|----------------|--------------------|----------|
| `test.sh unit -k test_message_send` | ImportError (collection) | ✓ `cannot import name 'TOPIC_KEY_PREFIX'` |
| `test.sh structural -k phase_10` | A/B/E/F/G fail; C/D pass | ✓ 5 failed, 2 passed |
| `test.sh multiprocess -k delivery` | 12 collected, all skip | ✓ 12 skipped |
| `test.sh multiprocess -k durability` | 1 fail (send verb absent) | ✓ fails: `No such command 'send'` (exit 2) |
| `test.sh all` (continue-on-collection-errors) | only intended REDs fail | ✓ **462 passed, 18 skipped**, 6 failed + 1 error — all intended RED items; no unrelated regression (baseline was 453) |

## Deviations

- **MBOX-01 durability test registers the recipient.** The 10-PATTERNS sample
  body sent to an *unregistered* `offline_id` and expected exit 0. That collides
  with the Phase 10 directed-send recipient-existence check (research §6:
  `session_show` → `SessionNotFound` → exit 2) and with the unit test
  `test_send_directed_raises_session_not_found` (absent recipient must raise).
  The only self-consistent reading of "offline" is *a registered session not
  running a Phase 11 listener daemon*, so the test registers the recipient with
  the test runner's live pid. Durability = the message persists in the mailbox
  until the offline session reads it. Recorded here so Wave 1/2 implement the
  existence check, not a pure-write directed send.

- **`test_phase_10_summaries_exist` (Test F)** is currently RED (PLANs exist
  without SUMMARYs) rather than skipped, because the planning worktree is
  attached. It goes GREEN once all three 10-*-SUMMARY.md files land — a
  phase-completion gate, expected to be red during Waves 0–2.

## Self-Check: PASSED

All four files created/activated at the specified paths; all RED/SKIP states
match the plan's `<verification>` block exactly; no unrelated test regressed
(462 passing vs 453 baseline). The module-level ImportError interrupts whole-suite
collection by pytest default — this self-heals in Wave 1 when the ops symbols exist.
