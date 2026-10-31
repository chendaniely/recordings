"""The first catch-up's gate, as Phase 1 needs it (stage 2b spec §11.6). Phase 2's catch-up adds
the snapshot mark, the interrupted catch-up and a rebuilt or restored `state.db`."""

from datetime import UTC, datetime

import pytest
from recordings.plaud.checks import K_CATCH_UP_DONE, NoSyncYet, PlaudDB
from recordings.plaud.gate import GATE_ALLOWED, GATE_FIRST, GATE_REFUSAL, Gate, gate_now
from recordings.state import State

NOW = datetime(2026, 10, 23, 21, 20, tzinfo=UTC)


@pytest.fixture
def db(new_archive):
    return PlaudDB(State.open(new_archive[1]))


def test_the_gate_is_closed_until_the_catch_up_is_done(db):
    # why: §11.6. Until catch_up_done_at is set no scheduled sync runs, and Status says why, which
    # isn't a failure. Nothing in Phase 1 sets it, so a deployed Phase 1 worker stays closed.
    assert gate_now(db) == Gate(False, GATE_FIRST)
    db.put(K_CATCH_UP_DONE, NOW.isoformat(), now=NOW)
    assert gate_now(db) == Gate(True, None)


def test_only_requests_that_write_no_snapshot_pass_the_closed_gate():
    # why: §11.6. "Only Compare runs"; Keep it here only resolves a flag. A sync, an import, a
    # Re-sync and Sync everything would write a snapshot, so the worker refuses them, in §15.6's
    # words.
    assert sorted(GATE_ALLOWED) == ["compare", "keep-here"]
    assert not GATE_ALLOWED & {"sync", "import", "resync", "full"}
    assert GATE_FIRST == "Scheduled sync waits for the first catch-up (runbook)"
    assert GATE_REFUSAL == "The first import is a supervised run on the server (runbook)"


def test_a_state_db_without_the_2b_tables_says_no_sync_yet(db):
    # why: Status and the views say "No sync has run yet" for a state.db from before 2b (Task 19
    # catches NoSyncYet), never a traceback.
    with db.state.db() as conn:
        conn.execute("DROP TABLE plaud_state")
    with pytest.raises(NoSyncYet):
        gate_now(db)
