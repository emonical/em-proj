"""Phase 9 MBOX-01 durability test — proves messages persist for offline
sessions across the CLI boundary. Skipped until Phase 10 message send verb ships.

When Phase 10 ships, this test will:
  1. Spawn a subprocess running: em-proj message send --to <session_id> "hello"
  2. Spawn a second subprocess running: em-proj message inbox --json
  3. Assert second child's stdout parses as a JSON list with at least one message
  4. Assert the message contains all MBOX-04 fields (msg_id, from_session, pattern,
     scope, topic, body, sent_at, ttl)
  5. Assert second child's returncode == 0

This stub collects and skips (does not fail), keeping MBOX-01 in the Nyquist map
without blocking the Phase 9 GREEN phase on a Phase 10 dependency.
"""
from __future__ import annotations

import pytest


def test_mailbox_persists_for_offline_session(clean_db, redis_precheck) -> None:
    """MBOX-01: Messages written to a session's mailbox persist for offline retrieval.

    Full test body (activated when Phase 10 'message send' verb ships):
      - Step 1: Spawn subprocess: em-proj message send --to <session_id> "hello"
        Assert returncode == 0; message is written to the mailbox even when
        the recipient session is not actively listening (offline durability).
      - Step 2: Spawn subprocess: em-proj message inbox --json (as the recipient session)
        Assert returncode == 0; stdout parses as a JSON list with >= 1 entry.
        Assert the entry has all MBOX-04 fields present and correctly typed:
          msg_id (str), from_session (str), pattern (str), scope (str),
          topic (str|None), body (str), sent_at (float), ttl (int).
    """
    pytest.skip(
        "Phase 10 'message send' verb not yet available — "
        "enable this test once Phase 10 ships and em-proj message send is on PATH"
    )
