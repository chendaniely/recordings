"""The rolling sweep (stage 2b spec §14.2, §14.5, §11.7's companion check, §10.2's cap; area B1):
its intervals and cap with an injected clock at N = 1,000 (§21; C2.01's sweep half), seeding and
rebaselining, listing changes, and the sweep inside the worker's real scheduled turn against the
fake Plaud server. Every ID and title is synthetic."""

import dataclasses
import random
from datetime import UTC, datetime, timedelta

from fake_plaud import json_response
from plaud_live import T0
from recordings.plaud import schedule, sweep
from recordings.plaud.checks import FAILING_AFTER, CheckRow, ListingEntry, PlaudDB
from recordings.plaud.client import FILE_PATH, LIST_PATH
from recordings.plaud.compare import Here
from recordings.state import State

MIN = timedelta(minutes=1)


def row(rid: str, **kw) -> CheckRow:
    base = {
        "recording_id": rid,
        "last_checked_at": None,
        "last_changed_at": None,
        "next_check_at": None,
        "interval_seconds": None,
        "fast_checks_left": 0,
        "failures": 0,
        "failing_since": None,
        "settled": True,
        "baseline_pending": False,
        "held_removals": (),
        "parked_at": None,
        "degraded": False,
        "gone_since": None,
        "kept_here_at": None,
    }
    return CheckRow(**{**base, **kw})


def apply(r: CheckRow, fields: dict) -> CheckRow:
    return dataclasses.replace(r, **{k: v for k, v in fields.items() if hasattr(r, k)})


def after(r: CheckRow | None, outcome: str, now: datetime, **kw) -> dict:
    return schedule.after_check(
        r,
        outcome,
        now=now,
        settled=kw.pop("settled", True),
        imported_at=kw.pop("imported_at", None),
        schedule_minutes=kw.pop("schedule_minutes", 20),
        full_sweep_days=kw.pop("full_sweep_days", 1),
    )


# ---- the intervals (§14.2) -----------------------------------------------------------------
def test_after_a_change_two_more_checks_at_the_interval_then_six_hours_then_doubling():
    now, r = T0, row("r", interval_seconds=24 * 3600)
    seen = []
    for outcome in ("changed", "unchanged", "unchanged", "unchanged", "unchanged", "unchanged"):
        fields = after(r, outcome, now)
        r = apply(r, fields)
        seen.append(fields["next_check_at"] - now)
        now = fields["next_check_at"]
    assert seen == [20 * MIN, 20 * MIN, 6 * 60 * MIN, 12 * 60 * MIN, 24 * 60 * MIN, 24 * 60 * MIN]


def test_an_unsettled_recording_every_interval_for_two_hours_then_hourly():
    imported = T0
    r = row("r", settled=False, interval_seconds=20 * 60)
    early = after(r, "unchanged", T0 + 30 * MIN, settled=False, imported_at=imported)
    assert early["next_check_at"] == T0 + 50 * MIN
    later = after(r, "unchanged", T0 + 3 * 60 * MIN, settled=False, imported_at=imported)
    assert later["next_check_at"] == T0 + 4 * 60 * MIN


def test_failures_back_off_separately_doubling_to_the_cap():
    r = row("r", interval_seconds=6 * 3600)
    waits = []
    for _ in range(9):
        fields = after(r, "failed", T0)
        r = apply(r, fields)
        waits.append(fields["next_check_at"] - T0)
    assert waits[:3] == [20 * MIN, 40 * MIN, 80 * MIN] and waits[-1] == timedelta(days=1)
    assert r.interval_seconds == 6 * 3600 and r.failures == 9  # the success interval is kept
    assert r.failing_since == T0
    ok = after(r, "unchanged", T0 + MIN)
    assert ok["failures"] == 0 and ok["failing_since"] is None


def test_settled_by_its_age_or_its_transcript():
    assert schedule.is_settled(
        recorded_at=T0 - timedelta(hours=49), complete_with_notes=False, now=T0
    )
    assert not schedule.is_settled(recorded_at=T0 - MIN, complete_with_notes=False, now=T0)
    assert schedule.is_settled(recorded_at=T0 - MIN, complete_with_notes=True, now=T0)


def test_the_cap_is_the_specs_worked_example_and_at_least_one():
    assert schedule.sweep_cap(1000, full_sweep_days=1, schedule_minutes=20) == 21
    assert schedule.sweep_cap(3, full_sweep_days=7, schedule_minutes=20) == 1
    assert schedule.sweep_cap(1000, full_sweep_days=1, schedule_minutes=0) == 21  # off: 20 min


def test_a_429_halves_the_cap_and_good_runs_grow_it_back_by_one():
    assert schedule.next_cap(21, 21, rate_limited=True, good=False) == 10
    assert schedule.next_cap(21, 10, rate_limited=False, good=True) == 11
    assert schedule.next_cap(21, 11, rate_limited=False, good=False) == 11
    assert schedule.next_cap(21, 20, rate_limited=False, good=True) is None  # back to full
    assert schedule.effective_cap(21, 5) == 5 and schedule.effective_cap(21, None) == 21


# ---- a run's plan --------------------------------------------------------------------------
def test_the_plan_takes_extras_then_the_most_overdue_up_to_the_cap():
    rows = [
        row("old", next_check_at=T0 - 60 * MIN),
        row("older", next_check_at=T0 - 90 * MIN),
        row("oldest", next_check_at=T0 - 120 * MIN),
        row("unsettled", settled=False, next_check_at=T0 - MIN),
        row("renamed", next_check_at=T0),  # this run's listing marked it (§14.2)
        row("later", next_check_at=T0 + MIN),
        row("not-covered", next_check_at=T0 - 500 * MIN),
    ]
    covered = {r.recording_id for r in rows} - {"not-covered"}
    picked = schedule.plan(rows, covered=covered, now=T0, cap=2, marked={"renamed"})
    assert picked == ["unsettled", "renamed", "oldest", "older"]


def test_a_run_that_retries_a_failing_recording_also_checks_one_that_isnt():
    """§11.7's companion check (P4): the most overdue covered recording not on the failing list,
    due or not, and checked first."""
    rows = [
        row("failing", failures=FAILING_AFTER, next_check_at=T0 - MIN),
        row("soonest", next_check_at=T0 + 30 * MIN),
        row("later", next_check_at=T0 + 300 * MIN),
        row("gone-not-covered", next_check_at=T0 - 600 * MIN),
    ]
    covered = {"failing", "soonest", "later"}
    assert schedule.plan(rows, covered=covered, now=T0, cap=5) == [
        "soonest",
        "failing",
    ]
    # With a recording that isn't failing among those due, no companion is added.
    rows.append(row("due", next_check_at=T0 - MIN))
    covered.add("due")
    assert schedule.plan(rows, covered=covered, now=T0, cap=5) == [
        "due",
        "failing",
    ]


def test_c2_01_the_sweep_at_1000_recordings_with_40_renames_on_an_injected_clock():
    """C2.01's sweep half (§21): 1,000 recordings seeded by the baseline, `full_sweep_days` 1, a
    run every 20 minutes taking the cap; 40 speaker renames at once (no listing field moves, so
    the cold sweep finds them). Each is found within `full_sweep_days` plus one schedule interval;
    every recording is checked within that too."""
    n, sched = 1000, 20
    cap = schedule.sweep_cap(n, full_sweep_days=1, schedule_minutes=sched)
    rows = {
        f"r{i:04d}": row(
            f"r{i:04d}",
            **schedule.seed_fields(
                index=i,
                count=n,
                last_fetched=T0 - timedelta(days=30),
                settled=True,
                now=T0,
                full_sweep_days=1,
            ),
        )
        for i in range(n)
    }
    rng = random.Random(7)
    renamed_at = T0 + timedelta(hours=30)
    renamed = set(rng.sample(sorted(rows), 40))
    found: dict[str, datetime] = {}
    last: dict[str, datetime] = {}
    now = T0
    while now < T0 + timedelta(days=3):
        for rid in schedule.plan(list(rows.values()), covered=rows, now=now, cap=cap):
            changed = rid in renamed and now >= renamed_at and rid not in found
            if changed:
                found[rid] = now
            gap = now - last[rid] if rid in last else None
            assert gap is None or gap <= timedelta(days=1, minutes=sched)
            last[rid] = now
            rows[rid] = apply(
                rows[rid], after(rows[rid], "changed" if changed else "unchanged", now)
            )
        now += timedelta(minutes=sched)
    assert set(found) == renamed
    assert max(found.values()) - renamed_at <= timedelta(days=1, minutes=sched)
    assert len(last) == n


# ---- seeding, rebaselining, the cap's record, listing changes --------------------------------
def test_the_baseline_seeds_spreads_and_excuses_then_rebaselines(scene):
    recs = scene.account.add_many(4)
    rids = [scene.import_(r).recording_id for r in recs]
    db = scene.db
    with db._db() as conn:  # a state.db with no check rows for them (2a's, or rebuilt)
        conn.execute("DELETE FROM plaud_checks")
    seeded = sweep.seed_missing(db, scene.writer, rids, now=scene.now, full_sweep_days=1)
    assert seeded == 4
    seeded_rows = sorted(
        (db.check_row(rid) for rid in rids),
        key=lambda r: r.next_check_at,  # type: ignore[arg-type]
    )
    spread = [r.next_check_at - scene.now for r in seeded_rows]
    assert spread == [timedelta(hours=6) * i for i in range(4)]
    for r in seeded_rows:
        rec = scene.writer.archive.load(r.recording_id)
        assert r.baseline_pending and r.last_checked_at == rec.sources[0].fetched_at
    assert sweep.seed_missing(db, scene.writer, rids, now=scene.now, full_sweep_days=1) == 0
    db.set_check(rids[0], baseline_pending=False, last_checked_at=T0)
    db.set_check(rids[1], baseline_pending=False)
    db.mark_gone([rids[1]], now=scene.now)
    assert sweep.rebaseline(db, now=scene.now, full_sweep_days=1) == 3
    assert db.check_row(rids[0]).baseline_pending and db.check_row(rids[0]).last_checked_at == T0
    assert not db.check_row(rids[1]).baseline_pending  # gone from Plaud: left as it is


def test_the_cap_halved_by_a_429_is_kept_until_good_runs_grow_it_back(scene):
    plan = sweep.Plan((), covered=1000, cap=21, full_cap=21)
    sweep.record_cap(scene.db, plan, rate_limited=True, good=False, now=T0)
    assert scene.db.get(sweep.K_SWEEP)["cap"] == 10
    plan = sweep.Plan((), covered=1000, cap=20, full_cap=21)
    sweep.record_cap(scene.db, plan, rate_limited=False, good=True, now=T0)
    assert scene.db.get(sweep.K_SWEEP) is None


def test_a_listing_change_marks_the_recording_for_a_check_now_never_its_first_sight(scene):
    db = scene.db
    rid = "20261001T030000-0700_2528c39b"
    db.set_check(rid, next_check_at=T0 + timedelta(days=1))
    pid = "a" * 32
    entry = ListingEntry(pid, f"of_{pid}", "1" * 64, 60_000, recording_ids=(rid,))
    assert db.write_listing([entry], now=T0) == []  # first sight: no change
    assert db.check_row(rid).next_check_at == T0 + timedelta(days=1)
    later = T0 + timedelta(minutes=20)
    assert db.write_listing([entry], now=later) == []  # the same: no change
    renamed = dataclasses.replace(entry, title_sha256="2" * 64)
    assert db.write_listing([renamed], now=later + MIN) == [rid]
    assert db.check_row(rid).next_check_at == later + MIN
    trimmed = dataclasses.replace(renamed, duration_ms=50_000)
    assert db.write_listing([trimmed], now=later + 2 * MIN) == [rid]  # a trim in Plaud
    kept_out = dataclasses.replace(trimmed, title_sha256=None, duration_ms=None)
    assert db.write_listing([kept_out], now=later + 3 * MIN) == []


def test_the_sweep_covers_what_is_here_and_neither_gone_parked_kept_out_nor_consent_listed(scene):
    db = scene.db
    here = Here(by_id={f"{i}" * 32: frozenset({f"rec-{i}"}) for i in range(1, 7)})
    db.mark_gone(["rec-1"], now=T0)
    db.set_check("rec-2", parked_at=T0)
    db.set_check("rec-3", kept_here_at=T0)
    db.set_listing("4" * 32, kept_out_at=T0, kept_out_word="noarchive")
    rows = {r.recording_id: r for r in db.check_rows()}
    got = sweep.covered(here, rows, db, consent_ids={"5" * 32})
    assert got == {"rec-6"}


# ---- through the worker's scheduled turn -----------------------------------------------------
def turn(w, live, minutes: float = 20) -> None:
    live.clock.advance(minutes=minutes)
    w.tick()


def envelope_paths(live) -> list[str]:
    return [
        r.path
        for r in live.server.requests
        if r.path != LIST_PATH and r.path.startswith(FILE_PATH.format(""))
    ]


def check_log(live):
    return live.db().check_log_since(datetime(2026, 1, 1, tzinfo=UTC))


def test_a_scheduled_run_checks_whats_due_and_a_rename_shows_within_the_sweep(live):
    live.write_config(extra="full_sweep_days = 1\n")
    recs = live.account.add_many(3)
    live.catch_up_done()
    w = live.started()
    w.tick()  # imports all three, each its own baseline, checked again in 20 minutes
    assert len(live.here()) == 3
    assert check_log(live) == []
    # Recorded a month ago, so settled: the cold sweep, whose cap at N = 3 is 1 a run (§14.2).
    for n in (1, 2, 3):
        turn(w, live, 21)
        assert len(check_log(live)) == n
    log = check_log(live)
    assert sorted(r.recording_id for r in log) == sorted(live.here().values())
    assert {r.result for r in log} == {"up to date"}
    assert len(live.pings()) == 4
    live.account.rename_speaker(recs[0].plaud_id, "Ada")
    for _ in range(3 * 24):  # a day of runs at most finds it
        turn(w, live)
        if any(r.result == "updated" for r in check_log(live)):
            break
    updated = [r for r in check_log(live) if r.result == "updated"]
    assert [r.named_parts for r in updated] == [("speakers",)]
    assert updated[0].at - T0 <= timedelta(days=1, minutes=60)


def test_a_lone_recording_that_always_fails_never_stops_the_ping(live):
    """§11.7 and P4: once it is on the failing list, a run that retries it also checks one that
    isn't, whose success keeps the run good."""
    live.write_config(extra="full_sweep_days = 1\n")
    bad, _ok = live.account.add_many(2)
    live.catch_up_done()
    w = live.started()
    w.tick()
    live.server.fault(
        lambda path, _q: bad.plaud_id in path and path.startswith(FILE_PATH.format("")),
        json_response({"detail": "synthetic"}, 404),
    )
    rid = live.here()[bad.plaud_id]
    for _ in range(40):
        turn(w, live)
        row = live.db().check_row(rid)
        if row.failures >= FAILING_AFTER + 1:
            break
    assert row.failures > FAILING_AFTER
    (failing,) = live.db().failing_checks()
    assert failing.recording_id == rid
    # Each run that retried it since it joined the failing list also checked the other one.
    retries = [r for r in check_log(live) if r.recording_id == rid and r.result == "failed"]
    others = [r for r in check_log(live) if r.recording_id != rid]
    assert all(any(abs((o.at - r.at).total_seconds()) < 60 for o in others) for r in retries[3:])
    assert live.db().get("failing") is None  # no failing streak: the pings go on
    pings = len(live.pings())
    turn(w, live, 60 * 24)
    assert len(live.pings()) == pings + 1


def test_a_light_turn_checks_nothing(live):
    from recordings.plaud import sync

    live.account.add_many(1)
    live.catch_up_done()
    w = live.started()
    w.tick()
    live.clock.advance(minutes=30)
    sync.worker_turn(w, light=True)
    assert check_log(live) == []
    sync.worker_turn(w, light=False)
    assert len(check_log(live)) == 1


def test_the_sweep_pauses_under_a_hold(live):
    recs = live.account.add_many(8)
    live.catch_up_done()
    w = live.started()
    w.tick()
    for rec in recs[:6]:  # a large shrink: the next sync holds
        live.account.remove(rec.plaud_id)
    before = len(envelope_paths(live))
    turn(w, live, 30)
    assert live.db().get("hold") is not None
    assert len(envelope_paths(live)) == before and check_log(live) == []


def test_a_rebuilt_state_db_seeds_the_sweep_and_trips_nothing(live, tmp_path):
    live.write_config(extra="full_sweep_days = 1\n")
    live.account.add_many(3)
    live.catch_up_done()
    w = live.started()
    w.tick()
    db = PlaudDB(State.open(live.state_path))
    with db._db() as conn:  # every check row lost
        conn.execute("DELETE FROM plaud_checks")
    turn(w, live)
    rows = db.check_rows()
    assert len(rows) == 3 and all(r.next_check_at is not None for r in rows)
    assert db.get("breaker") is None


def test_a_title_changed_in_the_listing_is_checked_in_the_same_run(live):
    """§14.2's listing changes: a changed title hash marks the recording for a check now, outside
    the cold sweep's cap, so a rename in Plaud shows within one sync."""
    live.write_config(extra="full_sweep_days = 7\n")
    recs = live.account.add_many(3)
    live.catch_up_done()
    w = live.started()
    w.tick()
    for _ in range(6):
        turn(w, live)
    assert not any(r.result == "updated" for r in check_log(live))
    live.account.rename(recs[0].plaud_id, "Synthetic meeting, renamed")
    turn(w, live)
    updated = [r for r in check_log(live) if r.result == "updated"]
    assert [r.named_parts for r in updated] == [("title",)]
    assert updated[0].recording_id == live.here()[recs[0].plaud_id]
