"""The circuit breaker (stage 2b spec §14.4, decision 11, §20 criterion 2; area B1), through the
worker's real scheduled turns against the fake Plaud server: C2.03 (speaker renames never trip
it), C2.04 (a field that changes on every fetch trips it, with no Accept), C2.05 (a new stable
field trips it with Accept these changes; pressed, each recording gets one new snapshot; the same
field tripping it again offers no button), C2.06 (a release with a new normaliser version closes
it at start), and the window, the blocker and `breaker reset`. Every ID and title is synthetic."""

from datetime import UTC, datetime, timedelta

import pytest
from recordings.plaud import breaker, requests
from recordings.plaud import normalise as normalise_module
from recordings.plaud.checks import BLOCKER_PREFIX, K_BREAKER, PlaudDB
from recordings.plaud.health import sync_health
from recordings.plaud.requests import PlaudRequest

COUNT = 12  # above breaker.TRIP_AT, so every recording counting trips it
TZ = "America/Vancouver"


def turn(w, live, minutes: float = 20) -> None:
    live.clock.advance(minutes=minutes)
    w.tick()


def log(live):
    return live.db().check_log_since(datetime(2026, 1, 1, tzinfo=UTC))


def snapshots(live, rid: str) -> int:
    from recordings.archive import Archive

    rec = Archive(live.root).load(rid)
    return sum(1 for s in rec.sources if s.kind == "plaud")


@pytest.fixture
def settled(live):
    """Twelve recordings imported and each checked once since (their imports' baselines spent):
    every later check of them may count. `full_sweep_days` 1, as the template has it."""
    live.write_config(extra="full_sweep_days = 1\n")
    recs = live.account.add_many(COUNT)
    live.catch_up_done()
    w = live.started()
    w.tick()
    assert len(live.here()) == COUNT
    for _ in range(4 * COUNT):
        turn(w, live)
        if len({r.recording_id for r in log(live)}) == COUNT:
            break
    assert len({r.recording_id for r in log(live)}) == COUNT
    assert not any(r.counted for r in log(live))
    return w, recs


def run_until(w, live, done, *, turns: int = 6 * 24) -> bool:
    for _ in range(turns):
        turn(w, live)
        if done():
            return True
    return False


def is_open(live) -> bool:
    return breaker.is_open(live.db())


def test_c2_03_three_speaker_renames_20_minutes_apart_on_12_recordings_never_trip_it(live, settled):
    w, recs = settled
    for name in ("Ada", "Grace", "Lin"):
        for rec in recs:
            live.account.rename_speaker(rec.plaud_id, name)
        for _ in range(3 * 24):  # the next day's runs: every rename found at last
            turn(w, live)
            assert not is_open(live)
    updated = [r for r in log(live) if r.result == "updated"]
    assert len({r.recording_id for r in updated}) == COUNT
    assert all(r.named_parts == ("speakers",) and not r.counted for r in updated)
    assert "breaker" not in sync_health(live.db().state, schedule_minutes=20).blockers


def test_c2_04_a_field_that_changes_on_every_fetch_trips_it_with_no_accept(live, settled):
    w, _recs = settled
    live.account.make_volatile("other")
    assert run_until(w, live, lambda: is_open(live))
    value = breaker.record(live.db())
    assert value["parts"] == ["other"] and value["accept"] is False
    view = breaker.view(value, tz=datetime.now(UTC).tzinfo)
    assert view["accept"] is False and view["code_fix"] == breaker.CODE_FIX
    assert "change between two fetches a moment apart" in view["sentence"]
    assert "synthetic_nonce" not in str(view)  # the volatile field's own value, never shown
    # It stops the ping (§16), and suppresses: no more snapshots of the volatile field.
    health = sync_health(live.db().state, schedule_minutes=20)
    assert "breaker" in health.blockers and health.cause() == breaker.PHRASE
    counts = {rid: snapshots(live, rid) for rid in live.here().values()}
    pings = len(live.pings())
    for _ in range(24):
        turn(w, live)
    assert {rid: snapshots(live, rid) for rid in live.here().values()} == counts
    assert len(live.pings()) == pings
    assert any(r.result == "suppressed" for r in log(live))
    # Accept is refused for it, at the worker too.
    reply = press(w, live, "accept-changes")
    assert reply.state == "failed" and reply.message == breaker.CODE_FIX
    # New recordings still arrive.
    live.account.add("Synthetic arrival")
    turn(w, live)
    assert len(live.here()) == COUNT + 1


def press(w, live, kind: str, n: int = 1) -> requests.Reply:
    requests.insert(
        w.state,
        PlaudRequest(f"request-{kind}-{n:04d}", kind),
        now=live.clock(),
        timezone_name=TZ,
    )
    w.tick()
    reply = requests.read(w.state, f"request-{kind}-{n:04d}")
    assert reply is not None
    return reply


def test_c2_05_a_new_stable_field_trips_it_with_accept_and_accepting_writes_one_snapshot_each(
    live, settled
):
    w, _recs = settled
    live.account.send_field("synthetic_feature", "on")
    assert run_until(w, live, lambda: is_open(live))
    value = breaker.record(live.db())
    assert value["accept"] is True and value["fields"] == ["synthetic_feature"]
    view = breaker.view(value, tz=UTC)
    assert view["fields"] == ["synthetic_feature"] and view["accept"] is True
    assert "Plaud started sending something new on" in view["sentence"]
    assert "on" not in view["fields"]  # its value, never shown
    before = {rid: snapshots(live, rid) for rid in live.here().values()}
    reply = press(w, live, "accept-changes")
    assert (reply.state, reply.message) == ("done", breaker.ACCEPTED)
    assert reply.result["fields"] == ["synthetic_feature"]
    assert not is_open(live)
    assert "breaker" not in sync_health(live.db().state, schedule_minutes=20).blockers
    run = live.db().state.last_run("plaud-accept-changes")
    assert run is not None and run.ok and "synthetic_feature" in run.detail
    # Over the next pass (full_sweep_days = 1) each recording gets one new snapshot, excused.
    for _ in range(3 * 24 + 3):
        turn(w, live)
        assert not is_open(live)
    after = {rid: snapshots(live, rid) for rid in live.here().values()}
    grew = {rid: after[rid] - before[rid] for rid in after}
    # Each recording that hadn't written the field before the trip gets exactly one new snapshot.
    assert set(grew.values()) <= {0, 1} and sum(grew.values()) >= COUNT - breaker.TRIP_AT
    assert all(after[rid] - before[rid] <= 1 for rid in after)
    # The same field changing again on every recording trips it again, with no button.
    live.account.send_field("synthetic_feature", "off")
    assert run_until(w, live, lambda: is_open(live))
    again = breaker.record(live.db())
    assert again["accept"] is False and again["again"] is True
    assert breaker.view(again, tz=UTC)["code_fix"] == breaker.CODE_FIX


def test_c2_06_a_release_with_a_new_normaliser_version_closes_it_at_start(
    live, settled, monkeypatch
):
    w, _recs = settled
    live.account.send_field("synthetic_feature", "on")
    assert run_until(w, live, lambda: is_open(live))
    assert breaker.record(live.db())["normaliser"] == normalise_module.NORMALISER_VERSION
    w.stopping.set()
    monkeypatch.setattr(
        normalise_module, "NORMALISER_VERSION", normalise_module.NORMALISER_VERSION + 1
    )
    live.started()  # the release's worker
    assert not is_open(live)
    db = live.db()
    assert db.get(BLOCKER_PREFIX + "breaker") is None
    value = breaker.record(db)
    assert value["closed"]["by"] == "release"
    line = breaker.closed_line(value, now=live.clock(), tz=UTC)
    assert line is not None and "closed by the release" in line
    assert breaker.closed_line(value, now=live.clock() + timedelta(days=2), tz=UTC) is None


# ---- the window, reset, and what is stored ---------------------------------------------------
def counted(db: PlaudDB, n: int, *, at: datetime, checked: int = 0) -> None:
    for i in range(n):
        db.log_check(
            f"rec-{i:03d}", "updated", parts=["other", "other:folder_id"], counted=True, now=at
        )
    for i in range(checked):
        db.log_check(f"ok-{i:03d}", "up to date", now=at)


def test_it_trips_at_10_recordings_more_than_5_percent_of_those_checked(scene):
    db, now = scene.db, scene.now
    counted(db, 9, at=now, checked=50)
    assert not breaker.evaluate(db, now=now)
    counted(db, 10, at=now, checked=0)
    assert breaker.evaluate(db, now=now)
    value = breaker.record(db)
    assert value["recordings"] == 10 and value["fields"] == ["folder_id"]
    assert db.get(BLOCKER_PREFIX + "breaker") is not None
    assert not breaker.evaluate(db, now=now)  # already open


def test_ten_recordings_but_not_more_than_5_percent_of_a_big_sweep_dont_trip_it(scene):
    db, now = scene.db, scene.now
    counted(db, 10, at=now, checked=190)  # 10 of 200 checked: exactly 5%
    assert not breaker.evaluate(db, now=now)


def test_the_window_is_the_last_24_hours_from_window_start(scene):
    db, now = scene.db, scene.now
    counted(db, 12, at=now - timedelta(hours=25))
    assert not breaker.evaluate(db, now=now)
    counted(db, 12, at=now - timedelta(hours=2))
    db.put(
        K_BREAKER, {"open": False, "window_start": (now - timedelta(hours=1)).isoformat()}, now=now
    )
    assert not breaker.evaluate(db, now=now)  # a reset an hour ago: its window starts then
    assert breaker.evaluate(db, now=now + timedelta(hours=25)) is False


def test_reset_closes_it_excusing_nothing(scene):
    db, now = scene.db, scene.now
    assert breaker.reset(db, now=now) == breaker.NOT_OPEN
    counted(db, 12, at=now)
    assert breaker.evaluate(db, now=now)
    assert breaker.reset(db, now=now) == breaker.RESET
    assert not breaker.is_open(db) and db.get(BLOCKER_PREFIX + "breaker") is None
    assert breaker.record(db)["closed"]["by"] == "reset"
    assert not breaker.evaluate(db, now=now + timedelta(minutes=1))  # the window starts now


def test_a_stored_breaker_that_cant_be_read_is_open_with_no_accept(scene):
    scene.db.put(K_BREAKER, ["not", "an", "object"], now=scene.now)
    assert breaker.is_open(scene.db)
    assert breaker.view(breaker.record(scene.db), tz=UTC)["accept"] is False
