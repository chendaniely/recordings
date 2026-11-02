"""The sync run (stage 2b spec §11.1, §11.5's run rules, §11.7, §9.7; area A5), through the
worker's real scheduled turn against the fake Plaud server: imports, the cap, a consent stop, a
late consent listing mid-run, every fetch failing, an outage, the disk guard, a turn that raised,
SIGTERM between recordings, and what each leaves for the ping. Every account, title, token and
byte is synthetic (plaud_live.py), and nothing reaches Plaud."""

import itertools
from datetime import timedelta

import pytest
from fake_plaud import html_page, server_error
from recordings.plaud import sync as sync_module
from recordings.plaud.checks import K_FAILING, K_LAST_TURN
from recordings.plaud.client import FILE_PATH, LIST_PATH
from recordings.plaud.health import sync_health
from recordings.plaud.importer import STOPPED_CONSENT, STOPPED_DISK
from recordings.plaud.sync import SCHEDULED_CAP
from recordings.worker import Stopping


def health(live):
    return sync_health(live.db().state, schedule_minutes=20)


def last_turn(live) -> dict:
    return live.db().get(K_LAST_TURN)


def turn(w, live, minutes: float = 20) -> None:
    """One scheduled turn: the clock moves an interval on, then a tick."""
    live.clock.advance(minutes=minutes)
    w.tick()


def is_envelope(path: str) -> bool:
    """`files/{id}`: the listing's own path is the same prefix, with no ID after it."""
    return path != LIST_PATH and path.startswith(FILE_PATH.format(""))


def envelope_requests(live) -> list[str]:
    return [r.path for r in live.server.requests if is_envelope(r.path)]


def test_a_scheduled_turn_imports_whats_waiting_and_pings(live):
    # §11.1 end to end: the sign-in, the listing, privacy, the compare, the imports; a good run
    # with successes pings the sync's check (§16).
    recs = live.account.add_many(3)
    live.catch_up_done()
    w = live.started()
    w.tick()
    here = live.here()
    assert set(here) == {r.plaud_id for r in recs}
    assert len(live.pings()) == 1
    assert last_turn(live)["outcome"] == "ran"
    assert last_turn(live)["detail"] == {"result": "ok"}
    assert health(live).failing is None
    run = live.db().state.last_run("plaud-sync")
    assert run is not None and run.ok and run.detail == "ok"
    assert w.newest_listing is not None and w.newest_listing.complete


def test_a_backlog_of_40_imports_over_two_scheduled_runs(live):
    # §11.5: a scheduled run imports at most 20; §21's "a 40-recording backlog importing over 2
    # runs with no hold". Newest first, as Plaud lists them.
    recs = live.account.add_many(40)
    live.catch_up_done()
    w = live.started()
    w.tick()
    assert len(live.here()) == SCHEDULED_CAP
    newest = sorted(recs, key=lambda r: r.start_at, reverse=True)[:SCHEDULED_CAP]
    assert set(live.here()) == {r.plaud_id for r in newest}
    turn(w, live)
    assert len(live.here()) == 40
    assert len(live.pings()) == 2
    assert live.db().get("hold") is None


def test_a_consent_typo_during_syncs_stops_every_import_and_the_ping(live):
    # C5.09, the run's half: the listing and privacy still run, nothing is fetched, the consent
    # blocker holds the ping.
    live.account.add_many(2)
    live.catch_up_done()
    live.consent_ids(extra="- `of_" + "a" * 31 + "` — a typo\n")
    w = live.started()
    w.tick()
    assert live.here() == {}
    assert envelope_requests(live) == []
    assert any(r.path == LIST_PATH for r in live.server.requests)
    assert live.pings() == []
    assert last_turn(live)["detail"] == {"result": STOPPED_CONSENT}
    assert "consent" in health(live).blockers


def test_an_id_added_to_the_consent_list_mid_run_is_never_fetched_and_the_run_goes_on(live):
    # C5.12, the run's half: the list is read again before each recording's first byte.
    first, second, third = live.account.add_many(3)  # listed newest first: third, second, first
    live.catch_up_done()
    w = live.started()

    def add_it() -> None:
        live.consent_ids(first.plaud_id)

    # Requests so far: the listing's two pages; then the newest recording's envelope, note and
    # audio. The consent list changes before the second recording's envelope.
    live.server.after(5, add_it)
    w.tick()
    assert first.plaud_id not in live.here()
    assert {second.plaud_id, third.plaud_id} <= set(live.here())
    assert live.fetched(first.plaud_id) == []
    assert len(live.pings()) == 1


def test_every_fetch_failing_stops_the_pings_until_one_succeeds(live):
    # C6.04: every files/{id} answering a shape the client can't read (each recording's own
    # failure) while the listing works. No pings; once each recording has failed 3 times it is
    # missing and no longer tried, and those quiet runs still don't ping (a work streak ends only
    # with a success); a new recording that imports pings again.
    live.account.add_many(2)
    live.catch_up_done()
    fault = live.server.fault(lambda p, _q: is_envelope(p), html_page())
    w = live.started()
    for _ in range(4):  # 2 hours apart: each failure's backoff (20, 40 minutes) has run out
        turn(w, live, minutes=120)
    assert live.here() == {}
    assert live.pings() == []
    assert health(live).failing is not None and health(live).failing.cause == "work"
    rows = live.db().listing_rows()
    assert len(rows) == 2 and all(r.import_failures >= 3 for r in rows.values())
    live.server.faults.remove(fault)
    turn(w, live)  # every recording on the failing list: nothing tried, still no ping
    assert live.pings() == [] and last_turn(live)["detail"] == {"result": "ok"}
    new = live.account.add()
    turn(w, live)
    assert set(live.here()) == {new.plaud_id}
    assert len(live.pings()) == 1 and health(live).failing is None


def test_an_envelope_endpoint_thats_down_ends_each_run_and_never_pings(live):
    # C6.04's other half: files/{id} answering 500 is an outage (A2's run-ending error): each run
    # ends at its first fetch, an attempt without success, "Plaud unavailable", and no ping.
    live.account.add_many(2)
    live.catch_up_done()
    live.server.fault(lambda p, _q: is_envelope(p), server_error(500))
    w = live.started()
    for _ in range(3):
        turn(w, live)
    assert live.here() == {} and live.pings() == []
    assert last_turn(live)["detail"] == {"result": "Plaud unavailable"}
    assert health(live).failing.cause == "work"


def test_an_outage_of_an_hour_stops_the_pings_then_they_resume(live):
    # C6.05: Plaud answers 503 for an hour (each call retried, then given up).
    live.account.add()
    live.catch_up_done()
    w = live.started()
    w.tick()
    assert len(live.pings()) == 1
    fault = live.server.fault(LIST_PATH, server_error(503))
    for _ in range(3):
        turn(w, live)
    assert len(live.pings()) == 1
    assert last_turn(live)["detail"] == {"result": "Plaud unavailable"}
    assert health(live).failing.cause == "listing"
    live.server.faults.remove(fault)
    turn(w, live)
    assert len(live.pings()) == 2 and health(live).failing is None


def test_the_disk_guard_refusing_stops_the_pings_until_it_doesnt(live, monkeypatch):
    # C6.11: a run whose import the disk guard refused isn't good; Status says why; the pings
    # resume at the first run it doesn't refuse.
    from recordings.disk import DiskStatus

    level = {"now": "stop"}

    def disk(root, **_kw):
        free = 10**10 if level["now"] == "stop" else 9 * 10**11
        return DiskStatus(str(root), True, free, 10**12, level["now"], "")

    monkeypatch.setattr(sync_module, "disk_status", disk)
    monkeypatch.setattr("recordings.plaud.importer.disk_status", disk)
    rec = live.account.add()
    live.catch_up_done()
    w = live.started()
    w.tick()
    assert live.here() == {}
    assert live.fetched(rec.plaud_id) == []
    assert live.pings() == []
    assert health(live).cause() == STOPPED_DISK
    level["now"] = "ok"
    turn(w, live)
    assert rec.plaud_id in live.here()
    assert len(live.pings()) == 1 and health(live).failing is None


def test_a_turn_that_raised_starts_a_streak_that_only_a_success_ends(live, monkeypatch):
    # The phase-end list's Task 11 row: a turn that raised reaches conclude_turn as a run that
    # isn't good, so a quiet run after it can't ping "up"; a run with a success does.
    live.catch_up_done()
    w = live.started()

    def broken(*_a, **_kw):
        raise RuntimeError("a synthetic bug")

    with monkeypatch.context() as m:
        m.setattr(sync_module, "take_listing", broken)
        w.tick()
    assert last_turn(live)["outcome"] == "error"
    assert live.db().get(K_FAILING)["cause"] == "work"
    turn(w, live)  # a quiet, good run: nothing to import
    assert live.pings() == [] and health(live).failing is not None
    live.account.add()
    turn(w, live)
    assert len(live.pings()) == 1 and health(live).failing is None


def test_sigterm_between_recordings_stops_the_run_and_records_nothing(live):
    # §9.2: SIGTERM stops a run between recordings; the turn isn't a failure and pings nothing.
    live.account.add_many(3)
    live.catch_up_done()
    w = live.started()
    imported = []
    real = w.between_recordings

    def stop_after_one() -> None:
        imported.append(len(live.here()))
        w.stopping.set()
        real()

    w.between_recordings = stop_after_one
    with pytest.raises(Stopping):
        sync_module.worker_turn(w, light=False)
    assert len(live.here()) == 1
    w.stopping.clear()
    w.tick()  # the scheduled turn, stopped the same way, records nothing
    assert live.db().get(K_FAILING) is None


def test_a_light_turn_is_the_same_run(live):
    # §9.2: inside a long run, the light turn of a due sync lists, checks titles and imports up to
    # 20 (the sweep, area B1's, joins it later).
    live.account.add_many(2)
    live.catch_up_done()
    w = live.started()
    outcome = w.turn(w, light=True)
    assert outcome.listing == "good" and outcome.successes == 2
    assert len(live.here()) == 2


def test_a_scheduled_run_leaves_a_failing_recordings_retry_to_its_backoff(live):
    # §11.5: a failed import's next try waits 20 minutes, then 40, doubling; a scheduled run skips
    # it until then, and counts nothing for it.
    rec = live.account.add()
    live.catch_up_done()
    fault = live.server.fault(FILE_PATH.format(f"of_{rec.plaud_id}"), html_page())
    w = live.started()
    w.tick()  # T0: the first failure; the next try at +20
    turn(w, live)  # +20: tried again, the second failure; the next try at +60
    assert live.db().listing(rec.plaud_id).import_failures == 2
    before = len(envelope_requests(live))
    turn(w, live)  # +40: still backing off
    assert len(envelope_requests(live)) == before
    live.server.faults.remove(fault)
    turn(w, live)  # +60
    assert rec.plaud_id in live.here()
    assert live.db().listing(rec.plaud_id).import_failures == 0


def test_after_a_rebuild_a_scheduled_run_leaves_what_needs_review_out(live):
    # C5.25, the run's half: a recording made before the rebuild waits for Dan's review; one made
    # after it imports as usual.
    from plaud_live import T0
    from recordings.plaud.checks import K_SNAPSHOT_MARK

    before = live.account.add()
    after = live.account.add(start_at=T0 + timedelta(hours=1))
    live.catch_up_done()
    db = live.db()
    db.put(K_SNAPSHOT_MARK, {"count": 1}, now=T0)
    db.record_rebuilt(at=T0, by="detected", now=T0)
    w = live.started()
    turn(w, live, minutes=90)
    assert set(live.here()) == {after.plaud_id}
    assert live.fetched(before.plaud_id) == []
    assert live.db().listing(before.plaud_id).needs_review


def test_a_consent_list_caught_mid_write_at_the_runs_start_is_a_stop_for_the_run(live, monkeypatch):
    # The end-of-branch privacy review (2026-11-02): the run's own read of the consent list at
    # its start uses the guarded read, as the worker's and the import's do (§12.1). A list being
    # written in place (it changes at every look) is a consent stop for the run: nothing fetched,
    # no title of a recording not here stored, and the run says why.
    from recordings.plaud import importer
    from recordings.plaud.cache import Cache
    from recordings.plaud.checks import K_COMPARE
    from recordings.plaud.compare import TITLES_HIDDEN_CONSENT

    live.account.add_many(2)
    live.catch_up_done()
    w = live.started()
    changes = itertools.count(1)
    real = importer._consent_key

    def writing(path):
        key = real(path)
        return None if key is None else (key[0], key[1] + next(changes))

    monkeypatch.setattr(importer, "_consent_key", writing)
    w.tick()
    assert live.here() == {} and envelope_requests(live) == []
    assert live.db().get(K_COMPARE)["titles_hidden"] == TITLES_HIDDEN_CONSENT
    assert [r.title for r in Cache(live.state_path).compare_rows()] == [None, None]
    assert last_turn(live)["detail"] == {"result": STOPPED_CONSENT}
