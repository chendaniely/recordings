"""The sync's requests from the UI (stage 2b spec §9.3, §15.1, §11.5; area A5): Compare, Sync now,
Import selected and Import all, Park and Unpark, run by the worker against the fake Plaud server;
their re-checks, replies and progress; coalescing (one "Already queued" per kind; a sync taking a
queued compare over; imports merged); and a request waiting its turn while another run holds the
sync. Every account, title, token and byte is synthetic (plaud_live.py); nothing reaches Plaud."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from fake_plaud import html_page
from plaud_live import T0
from recordings.plaud import requests
from recordings.plaud.checks import K_HOLD, K_SNAPSHOT_MARK
from recordings.plaud.client import FILE_PATH
from recordings.plaud.health import record_rate_limit
from recordings.plaud.requests import ALREADY_QUEUED, QUEUED, PlaudRequest
from recordings.plaud.runlock import plaud_run
from recordings.plaud.when import local_hhmm

TZ = "America/Vancouver"


def ask(live, w, kind: str, n: int, **fields) -> requests.Reply:
    """Insert a request as web does, then one tick of the worker; its row afterwards."""
    put(live, kind, n, **fields)
    w.tick()
    return requests.read(w.state, f"request-{n:04d}")


def put(live, kind: str, n: int, **fields) -> requests.Reply:
    return requests.insert(
        live.db().state,
        PlaudRequest(f"request-{n:04d}", kind, **fields),
        now=live.clock(),
        timezone_name=TZ,
    )


def up(w) -> None:
    w._beat()  # the worker reports in, as its heartbeat thread does


def test_compare_answers_with_the_equation_and_imports_nothing(live):
    live.account.add_many(3)
    w = live.started()  # the gate is closed: Compare runs anyway (§11.6)
    reply = ask(live, w, "compare", 1)
    assert reply.state == "done"
    assert reply.message == "Compared: listed 3 = here 0 + waiting 0 + excluded 0 + missing 3"
    assert reply.result["equation"]["missing"] == 3
    assert live.here() == {}
    assert live.db().get("last_good_listing") is None  # a Compare never moves it


def test_sync_now_imports_and_says_so(live):
    live.account.add_many(2)
    live.catch_up_done()
    w = live.started()
    reply = ask(live, w, "sync", 1)
    assert (reply.state, reply.message) == ("done", "Synced: 2 imported (0 private) · All here")
    assert len(live.here()) == 2
    again = ask(live, w, "sync", 2)
    assert again.message == "Synced: nothing new · All here"
    # The worker's own scheduled turn, at the first tick, pinged once; the requests never do.
    assert len(live.pings()) == 1


def test_sync_now_during_a_hold_or_a_rate_limit_answers_without_calling_plaud(live):
    live.account.add()
    live.catch_up_done()
    w = live.started()
    w.tick()  # the first scheduled turn; the next isn't due for 20 minutes
    live.db().put(K_HOLD, {"since": T0.isoformat()}, now=T0)
    before = len(live.server.requests)
    assert ask(live, w, "sync", 1).message == "Held: see Status"
    live.db().delete(K_HOLD)
    until = live.clock() + timedelta(minutes=20)
    record_rate_limit(live.db(), until, now=live.clock())
    reply = ask(live, w, "import", 2, select_all=True)
    hhmm = local_hhmm(until, ZoneInfo(TZ))
    assert (reply.state, reply.message) == ("failed", f"Rate limited until {hhmm} · See Status")
    assert len(live.server.requests) == before


def test_import_all_leaves_review_rows_out_and_import_selected_brings_one_in(live):
    # C5.25's request half: after a rebuild, Import all skips what needs review; Import selected
    # (a named ID) brings it in.
    before = live.account.add()
    after = live.account.add(start_at=T0 + timedelta(hours=1))
    live.catch_up_done()
    db = live.db()
    db.put(K_SNAPSHOT_MARK, {"count": 1}, now=T0)
    db.record_rebuilt(at=T0, by="detected", now=T0)
    w = live.started()
    reply = ask(live, w, "import", 1, select_all=True)
    assert (reply.state, reply.message) == ("done", "1 imported")
    assert set(live.here()) == {after.plaud_id}
    reply = ask(live, w, "import", 2, plaud_ids=(f"of_{before.plaud_id}",))
    assert reply.message == "1 imported"
    assert set(live.here()) == {after.plaud_id, before.plaud_id}


def test_an_import_reports_its_progress(live, monkeypatch):
    live.account.add_many(3)
    live.catch_up_done()
    w = live.started()
    seen = []
    real = requests.progress

    def spy(state, request_id, **kw):
        seen.append((kw["done"], kw["total"], kw.get("message")))
        real(state, request_id, **kw)

    monkeypatch.setattr(requests, "progress", spy)
    reply = ask(live, w, "import", 1, select_all=True)
    assert reply.message == "3 imported"
    assert seen[0] == (0, 3, "Importing") and seen[-1][:2] == (3, 3)


def test_park_and_unpark_a_plaud_id_that_failed_to_import(live):
    rec = live.account.add()
    live.catch_up_done()
    live.server.fault(FILE_PATH.format(f"of_{rec.plaud_id}"), html_page())
    w = live.started()
    for n in range(3):
        ask(live, w, "import", n + 1, plaud_ids=(rec.plaud_id,))
    assert live.db().listing(rec.plaud_id).import_failures >= 3
    reply = ask(live, w, "park", 10, plaud_ids=(rec.plaud_id,))
    assert reply.state == "done" and reply.result == {"parked": [rec.plaud_id], "nothing": []}
    assert live.db().listing(rec.plaud_id).parked_at is not None
    reply = ask(live, w, "unpark", 11, plaud_ids=(rec.plaud_id,))
    assert reply.state == "done" and live.db().listing(rec.plaud_id).parked_at is None


def test_a_sync_takes_a_queued_compare_over_and_a_second_press_is_already_queued(live):
    # §9.3's coalescing: one run answers all three presses.
    live.account.add()
    live.catch_up_done()
    w = live.started()
    up(w)
    assert put(live, "compare", 1).message == QUEUED
    assert put(live, "sync", 2).message == QUEUED  # the compare's row becomes a sync
    assert put(live, "compare", 3).message == ALREADY_QUEUED
    assert put(live, "sync", 4).message == ALREADY_QUEUED
    w.tick()
    replies = [requests.read(w.state, f"request-{n:04d}") for n in (1, 2, 3, 4)]
    assert {(r.state, r.message) for r in replies} == {
        ("done", "Synced: 1 imported (0 private) · All here")
    }
    assert len(live.here()) == 1
    with live.db().state.db() as conn:
        kinds = conn.execute("SELECT kind FROM plaud_requests ORDER BY id").fetchall()
    assert [k for (k,) in kinds] == ["sync", "sync", "compare", "sync"]  # the first, upgraded


def test_imports_queued_together_merge_into_one_run(live):
    a, b = live.account.add_many(2)
    live.catch_up_done()
    w = live.started()
    up(w)
    assert put(live, "import", 1, plaud_ids=(a.plaud_id,)).message == QUEUED
    assert put(live, "import", 2, plaud_ids=(b.plaud_id,)).message == QUEUED
    assert put(live, "import", 3, plaud_ids=(b.plaud_id,)).message == ALREADY_QUEUED
    w.tick()
    for n in (1, 2, 3):
        assert requests.read(w.state, f"request-{n:04d}").message == "2 imported"


def test_a_request_waits_its_turn_while_another_run_holds_the_sync(live):
    # §9.4: "A request waits its turn": it stays queued, and the worker serves other kinds.
    live.account.add()
    live.catch_up_done()
    w = live.started()
    with plaud_run(w.state, "plaud-catch-up", timeout=0, now=T0):
        put(live, "sync", 1)
        w.tick()
        assert requests.read(w.state, "request-0001").state == "queued"
    w.tick()
    assert requests.read(w.state, "request-0001").state == "done"


def test_compare_writes_nothing_to_the_archive(live):
    # §11.1: "A Compare stops after classify … It writes nothing to the archive": privacy from
    # the listing is a sync's step, so a rename to a private title waits for the next sync.
    from recordings.archive import Archive
    from recordings.models import is_private

    rec = live.account.add()
    live.catch_up_done()
    w = live.started()
    w.tick()  # the scheduled turn imports it
    rid = live.here()[rec.plaud_id]
    live.account.rename(rec.plaud_id, "Private: synthetic rename")
    assert ask(live, w, "compare", 1).state == "done"
    assert not is_private(Archive(live.root).load(rid))
    live.clock.advance(minutes=20)
    w.tick()  # the next scheduled sync checks the listed title
    assert is_private(Archive(live.root).load(rid))
