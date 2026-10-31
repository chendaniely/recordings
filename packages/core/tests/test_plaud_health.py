"""Sync health and the ping rule (stage 2b spec §11.7, §16, Dan's decision 10). FakeNtfy stands in
for Healthchecks (2b phase 1 plan, decision 11): it records each ping and answers `status`."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from recordings.alerts import AlertConfig
from recordings.plaud.checks import (
    BLOCKERS,
    K_FAILING,
    K_LAST_PING,
    K_LAST_TURN,
    NoSyncYet,
    PlaudDB,
)
from recordings.plaud.health import (
    BLOCKER_PHRASES,
    RunOutcome,
    clear_blocker,
    judge_run,
    open_blocker,
    record_run,
    record_turn,
    sync_health,
)
from recordings.state import State
from recordings.worker import TurnContext, conclude_turn

NOW = datetime(2026, 10, 23, 21, 20, tzinfo=UTC)
PACIFIC = timezone(timedelta(hours=-7))
GOOD = RunOutcome("good", attempts=2, successes=1, disk_refused=False, detail="ok")
QUIET = RunOutcome("good", attempts=0, successes=0, disk_refused=False, detail="ok")
NO_LISTING = RunOutcome("none", 0, 0, False, "Plaud unavailable")
NO_SUCCESS = RunOutcome(
    "good", attempts=3, successes=0, disk_refused=False, detail="every fetch failed"
)
DISK = RunOutcome("good", attempts=1, successes=0, disk_refused=True, detail="imports stopped")


@pytest.fixture
def state(new_archive):
    return State.open(new_archive[1])


def context(state, ntfy, *, schedule_minutes=20, clock=lambda: NOW) -> TurnContext:
    alerts = AlertConfig(
        ntfy_server=None,
        topic=None,
        deadman_url=None,
        deadman_sync_url=f"{ntfy.url}/ping/sync-check",
    )
    return TurnContext(state, PlaudDB(state), alerts, schedule_minutes, clock)


def pings(ntfy) -> list[tuple[str, str, str]]:
    return [(r["method"], r["path"], r["body"]) for r in ntfy.requests]


def blocked(name):
    def setup(db):
        open_blocker(db, name, now=NOW)

    return setup


def streak(db):
    db.put(K_FAILING, {"since": NOW.isoformat(), "cause": "work"}, now=NOW)


@pytest.mark.parametrize(
    ("setup", "ran", "pinged"),
    [
        pytest.param(None, GOOD, True, id="a good run, nothing open"),
        pytest.param(None, QUIET, True, id="a good run with no attempts"),
        pytest.param(None, NO_LISTING, False, id="no listing"),
        pytest.param(None, NO_SUCCESS, False, id="attempts, no success"),
        pytest.param(
            None,
            RunOutcome("good", 3, 2, True, "imports stopped"),
            False,
            id="imports succeeded, then the disk guard refused one",
        ),
        pytest.param(streak, QUIET, False, id="a work streak open"),
        *(pytest.param(blocked(n), GOOD, False, id=f"blocker {n}") for n in BLOCKERS),
        # Busy, the gate, a sign-in pause and a rate-limit wait all reach conclude_turn as a turn
        # that ran no sync: one row. Task 13's tests drive each of them through the worker.
        pytest.param(None, None, False, id="no sync ran: busy, the gate, a pause or a wait"),
    ],
)
def test_the_ping_rule_table(state, ntfy, setup, ran, pinged):
    # why: C6.01, Dan's decision 10. One rule: a scheduled sync that is good, and nothing holds
    # it up.
    ctx = context(state, ntfy)
    if setup is not None:
        setup(ctx.db)
    assert conclude_turn(ctx, ran) is pinged
    assert pings(ntfy) == ([("POST", "/ping/sync-check", "")] if pinged else [])


def test_every_blocker_stops_the_ping_even_with_a_good_run(state, ntfy):
    # why: C6.02, C5.08. A consent stop with no attempts, or the breaker with working imports, can
    # be a good run: only the blocker stops the ping.
    ctx = context(state, ntfy)
    open_blocker(ctx.db, "consent", now=NOW, detail={"lines": [14, 22]})
    assert conclude_turn(ctx, QUIET) is False
    clear_blocker(ctx.db, "consent")
    open_blocker(ctx.db, "breaker", now=NOW)
    assert conclude_turn(ctx, RunOutcome("good", 5, 5, False, "ok")) is False
    assert pings(ntfy) == []
    clear_blocker(ctx.db, "breaker")
    assert conclude_turn(ctx, GOOD) is True


def test_flags_a_failing_recording_and_a_failed_full_never_stop_it(state, ntfy):
    # why: C6.02. Late keep-outs and consent listings (flagged once, already private), a failing
    # recording and a failed Sync everything aren't blockers. (A rebuild's review list is Phase
    # 2's, with the rebuild record: its line of C6.02 is tested there.)
    from recordings.plaud.checks import K_FULL

    ctx = context(state, ntfy)
    db = ctx.db
    rid = "20261006T140003-0700_aaaaaaaa"
    db.add_event("kept-out-late", now=NOW, recording_id=rid, open_flag=True)
    db.add_event(
        "consent-late", now=NOW, recording_id="20261006T150003-0700_bbbbbbbb", open_flag=True
    )
    db.set_check(rid, failures=3, failing_since=NOW)
    db.put(K_FULL, {"ok": False, "detail": "partial: 3 recordings failed"}, now=NOW)
    assert conclude_turn(ctx, GOOD) is True


def test_attempts_without_success_are_never_good_and_failing_recordings_count(state, ntfy):
    # why: C6.03. A run that can only retry failing recordings, all still failing, is not good, so
    # a systemic failure can't pass for a recovery; the pings resume after a run with a success.
    ctx = context(state, ntfy)
    retries_only = RunOutcome("good", attempts=2, successes=0, disk_refused=False, detail="ok")
    assert judge_run(retries_only) is False
    assert conclude_turn(ctx, retries_only) is False
    assert conclude_turn(ctx, QUIET) is False  # good, but the work streak is open
    assert conclude_turn(ctx, GOOD) is True
    assert len(pings(ntfy)) == 1


def test_recovery_follows_the_cause(state):
    # why: C6.03, C6.10, §11.7.
    db = PlaudDB(state)
    later = NOW + timedelta(minutes=20)
    record_run(db, NO_LISTING, now=NOW)
    assert db.get(K_FAILING) == {"since": NOW.isoformat(), "cause": "listing"}
    assert record_run(db, QUIET, now=later) is True and db.get(K_FAILING) is None
    record_run(db, DISK, now=NOW)
    assert db.get(K_FAILING)["cause"] == "disk"
    record_run(db, NO_LISTING, now=later)  # the guard didn't refuse: the disk part ends
    assert db.get(K_FAILING) == {"since": NOW.isoformat(), "cause": "listing"}  # since kept
    record_run(db, QUIET, now=later)
    assert db.get(K_FAILING) is None
    record_run(db, NO_SUCCESS, now=NOW)
    record_run(db, QUIET, now=later)  # a good run with no attempts leaves a work streak
    assert db.get(K_FAILING)["cause"] == "work"
    record_run(db, GOOD, now=later)
    assert db.get(K_FAILING) is None


def test_an_outage_then_failing_fetches_then_a_quiet_run_never_pings(state, ntfy):
    # why: §11.7, "a systemic failure can't flip the check up and down". A listing streak ends at
    # a good listing; in a run whose every fetch failed it carries on as a work streak, `since`
    # kept, so the quiet run after it (no attempts) doesn't end it and ping "up".
    later = NOW + timedelta(minutes=20)
    assert conclude_turn(context(state, ntfy), NO_LISTING) is False
    assert conclude_turn(context(state, ntfy, clock=lambda: later), NO_SUCCESS) is False
    assert PlaudDB(state).get(K_FAILING) == {"since": NOW.isoformat(), "cause": "work"}
    assert conclude_turn(context(state, ntfy, clock=lambda: later), QUIET) is False
    assert pings(ntfy) == []
    assert conclude_turn(context(state, ntfy, clock=lambda: later), GOOD) is True


def test_the_cause_names_the_schedule_off_a_bad_word_a_rate_limit_busy_the_gate_and_each_blocker(
    state,
):
    # why: C6.12, §15.2. "Isn't being pinged" names the first cause that applies.
    from recordings.plaud.settings import BAD_WORD

    db = PlaudDB(state)

    def cause(minutes=20):
        return sync_health(state, schedule_minutes=minutes).cause(tz=PACIFIC)

    assert cause() is None
    record_turn(
        db,
        "gate",
        now=NOW,
        detail={"reason": "Scheduled sync waits for the first catch-up (runbook)"},
    )
    assert cause() == "Scheduled sync waits for the first catch-up (runbook)"
    record_turn(db, "busy", now=NOW, detail={"job": "plaud-full", "since": NOW.isoformat()})
    assert cause() == "skipped: busy (`plaud-full`) since 14:20"
    record_turn(
        db, "rate-limited", now=NOW, detail={"until": (NOW + timedelta(minutes=20)).isoformat()}
    )
    assert cause() == "rate limited until 14:40"
    record_turn(db, "idle-config", now=NOW)
    assert cause() == BAD_WORD
    assert cause(minutes=0) == "scheduled sync is off"
    record_run(db, DISK, now=NOW)
    assert cause() == "imports stopped: the disk is too full to import"
    db.put(K_FAILING, {"since": NOW.isoformat(), "cause": "listing"}, now=NOW)
    assert cause() == "the sync is failing (since 14:20)"
    for name in reversed(BLOCKERS):  # the first blocker in §16's order wins
        open_blocker(db, name, now=NOW)
        assert cause() == BLOCKER_PHRASES[name]


def test_a_blocker_keeps_its_first_since(state):
    db = PlaudDB(state)
    open_blocker(db, "consent", now=NOW, detail={"lines": [14]})
    open_blocker(db, "consent", now=NOW + timedelta(hours=1), detail={"lines": [14, 22]})
    health = sync_health(state, schedule_minutes=20)
    assert health.blockers == {"consent": NOW}
    clear_blocker(db, "consent")
    assert sync_health(state, schedule_minutes=20).blockers == {}
    with pytest.raises(ValueError):
        open_blocker(db, "ntfy", now=NOW)


def test_a_repeated_turn_outcome_is_written_once(state):
    db = PlaudDB(state)
    record_turn(db, "gate", now=NOW)
    record_turn(db, "gate", now=NOW + timedelta(seconds=2))
    assert db.get(K_LAST_TURN)["at"] == NOW.isoformat()  # it reads as "since"
    record_turn(db, "ran", now=NOW + timedelta(minutes=20))
    record_turn(db, "ran", now=NOW + timedelta(minutes=40))
    assert db.get(K_LAST_TURN)["at"] == (NOW + timedelta(minutes=40)).isoformat()


def test_a_failed_ping_is_recorded_with_its_status(state, ntfy):
    # why: C6.09. Status shows "last ping failed 14:40 (HTTP 404)".
    ctx = context(state, ntfy)
    ntfy.status = 404
    assert conclude_turn(ctx, GOOD) is False
    assert ctx.db.get(K_LAST_PING) == {
        "at": NOW.isoformat(),
        "ok": False,
        "status": 404,
        "failed_since": NOW.isoformat(),
    }
    assert sync_health(state, schedule_minutes=20).last_ping == (NOW, False, 404)
    ntfy.status = 200
    assert conclude_turn(ctx, GOOD) is True
    assert sync_health(state, schedule_minutes=20).last_ping == (NOW, True, 200)


def test_failed_since_is_the_first_failed_ping_until_a_good_one(state, ntfy):
    # why: §15.2's "the ping has failed for 2 hours" (Task 19 reads it; decision 32). The first
    # failure's time is kept across later failures, and a good ping drops it.
    later = NOW + timedelta(minutes=20)
    ntfy.status = 404
    conclude_turn(context(state, ntfy), GOOD)
    ntfy.status = 500
    conclude_turn(context(state, ntfy, clock=lambda: later), GOOD)
    assert PlaudDB(state).get(K_LAST_PING) == {
        "at": later.isoformat(),
        "ok": False,
        "status": 500,
        "failed_since": NOW.isoformat(),
    }
    ntfy.status = 200
    assert conclude_turn(context(state, ntfy, clock=lambda: later), GOOD) is True
    assert PlaudDB(state).get(K_LAST_PING) == {"at": later.isoformat(), "ok": True, "status": 200}
    again = later + timedelta(minutes=20)
    ntfy.status = 404
    conclude_turn(context(state, ntfy, clock=lambda: again), GOOD)
    assert PlaudDB(state).get(K_LAST_PING)["failed_since"] == again.isoformat()


def test_an_unreachable_check_is_recorded_with_no_status(state, ntfy):
    ctx = context(state, ntfy)
    ntfy.close()
    assert conclude_turn(ctx, GOOD) is False
    assert ctx.db.get(K_LAST_PING)["status"] is None


def test_an_unreadable_state_db_sends_no_ping(state, ntfy):
    # why: §16. Any error while deciding sends nothing; the check's grace notices.
    ctx = context(state, ntfy)
    for name in ("state.db-wal", "state.db-shm"):
        (state.path / name).unlink(missing_ok=True)
    state.db_path.write_bytes(b"this is not a database " * 256)
    assert conclude_turn(ctx, GOOD) is False
    assert pings(ntfy) == []


def test_sync_health_reads_plaud_state_only_and_raises_no_sync_yet_without_it(state):
    db = PlaudDB(state)
    db.add_event(
        "consent-late", now=NOW, recording_id="20261006T140003-0700_aaaaaaaa", open_flag=True
    )
    with state.db() as conn:
        for table in ("plaud_events", "plaud_checks", "plaud_listing", "plaud_requests"):
            conn.execute(f"DROP TABLE {table}")
    health = sync_health(state, schedule_minutes=20)
    assert health.may_ping() and health.cause() is None
    with state.db() as conn:
        conn.execute("DROP TABLE plaud_state")
    with pytest.raises(NoSyncYet):
        sync_health(state, schedule_minutes=20)


def test_a_restart_neither_drops_nor_repeats_a_ping(state, ntfy):
    # why: §16. The answer comes from state.db each turn, never from memory: a new context on the
    # same state.db (a restart) answers as the old one would.
    first = context(state, ntfy)
    open_blocker(first.db, "hold", now=NOW)
    assert conclude_turn(first, GOOD) is False
    restarted = context(State.open(state.path), ntfy)
    assert conclude_turn(restarted, GOOD) is False
    clear_blocker(restarted.db, "hold")
    assert conclude_turn(restarted, GOOD) is True
    assert conclude_turn(context(State.open(state.path), ntfy), GOOD) is True
    assert len(pings(ntfy)) == 2  # one per good turn, none twice


def test_imports_that_succeed_before_the_disk_guard_refuses_are_not_a_good_run(state, ntfy):
    # why: C6.10, §11.7 "A full disk". Some imports worked, then the guard refused one: not good,
    # so no ping, whatever the successes say. The next run the guard doesn't refuse ends the streak.
    ctx = context(state, ntfy)
    partial = RunOutcome("good", 3, 2, True, "imports stopped")
    assert judge_run(partial) is False
    assert conclude_turn(ctx, partial) is False
    assert ctx.db.get(K_FAILING)["cause"] == "disk"
    assert pings(ntfy) == []
    assert conclude_turn(ctx, DISK) is False
    assert conclude_turn(ctx, QUIET) is True
    assert ctx.db.get(K_FAILING) is None
    assert pings(ntfy) == [("POST", "/ping/sync-check", "")]


def test_a_small_shrink_listing_counts_as_good(state, ntfy):
    # why: §11.7, §16. A small shrink is a good listing: it pings, and it ends a listing streak.
    shrink = RunOutcome("small-shrink", 2, 1, False, "ok")
    assert judge_run(shrink) is True
    ctx = context(state, ntfy)
    record_run(ctx.db, NO_LISTING, now=NOW)
    assert conclude_turn(ctx, shrink) is True
    assert ctx.db.get(K_FAILING) is None


@pytest.mark.parametrize("junk", ["yes", 7, ["work"], True])
def test_a_failing_value_that_isnt_a_record_stops_the_ping(state, ntfy, junk):
    # why: §16, fail closed. A K_FAILING nobody can read is a streak, like an unknown cause.
    ctx = context(state, ntfy)
    ctx.db.put(K_FAILING, junk, now=NOW)
    assert sync_health(state, schedule_minutes=20).may_ping() is False
    # It reads as a work streak: a quiet run (no attempts) leaves it, a run with a success ends it.
    assert conclude_turn(ctx, QUIET) is False
    assert pings(ntfy) == []
    assert conclude_turn(ctx, GOOD) is True
