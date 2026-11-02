"""Re-sync and parking a recording (stage 2b spec §14.6, decision 6, §9.3; area B2), run by the
worker against the fake Plaud server: one recording's replies, a selection's end line and progress,
the merge of a second press, a parked recording tried and unparked by a success, Park and Unpark
from Status's failing line, the CLI's `resync`, `park` and `held`, C2.01's Re-sync half and C5.23.
Every account, title, token and byte is synthetic (plaud_live.py); nothing reaches Plaud."""

from datetime import timedelta

import pytest
from fake_plaud import server_error
from plaud_live import T0
from recordings.cli import main
from recordings.plaud import breaker, requests, resync, runlock, sweep
from recordings.plaud.checks import FAILING_AFTER, K_BREAKER, K_HOLD, K_REBUILT
from recordings.plaud.client import FILE_PATH
from recordings.plaud.compare import here_from_archive
from recordings.plaud.gate import GATE_REFUSAL
from recordings.plaud.requests import ALREADY_QUEUED, PlaudRequest
from recordings.plaud.tokens import from_vendor

TZ = "America/Vancouver"


def put(live, kind: str, n: int, **fields) -> requests.Reply:
    return requests.insert(
        live.db().state,
        PlaudRequest(f"request-{n:04d}", kind, **fields),
        now=live.clock(),
        timezone_name=TZ,
    )


def ask(live, w, kind: str, n: int, **fields) -> requests.Reply:
    """Insert a request as web does, then one tick of the worker; its row afterwards."""
    put(live, kind, n, **fields)
    w.tick()
    return requests.read(w.state, f"request-{n:04d}")


def ready(live, count: int):
    """`count` recordings imported by the worker's first scheduled turn: (worker, {Plaud ID:
    recording ID}, the fake's recordings)."""
    recs = live.account.add_many(count)
    live.catch_up_done()
    w = live.started()
    w.tick()
    here = live.here()
    assert len(here) == count
    return w, here, recs


def no_title(live, *texts) -> None:
    for title in (r.name for r in live.account.recordings if r.name):
        for text in texts:
            assert title not in str(text)


def test_one_recording_up_to_date_then_updated_speakers(live):
    w, here, recs = ready(live, 2)
    rid = here[recs[0].plaud_id]
    reply = ask(live, w, "resync", 1, recording_ids=(rid,))
    assert (reply.state, reply.message) == ("done", "Up to date")
    live.account.rename_speaker(recs[0].plaud_id, "Synthetic Ada")
    reply = ask(live, w, "resync", 2, recording_ids=(rid,))
    assert (reply.state, reply.message) == ("done", "Updated: speakers")
    assert reply.result["updated_ids"] == [rid] and reply.result["each"] == {rid: reply.message}
    no_title(live, reply.message, reply.result)


def test_a_selection_is_one_request_with_progress_and_its_end_line(live, monkeypatch):
    # Decision 6 and C9.05's worker half: "25 checked: 7 updated (speakers 6, title 1) · 18 up
    # to date", with a recording that didn't come from Plaud skipped and counted.
    w, here, recs = ready(live, 4)
    live.account.rename_speaker(recs[0].plaud_id, "Synthetic Ada")
    live.account.rename_speaker(recs[1].plaud_id, "Synthetic Lin")
    live.account.rename(recs[1].plaud_id, "Synthetic meeting renamed")
    seen = []
    real = requests.progress

    def spy(state, request_id, **kw):
        seen.append((kw["done"], kw["total"], kw.get("message")))
        real(state, request_id, **kw)

    monkeypatch.setattr(requests, "progress", spy)
    named = [here[r.plaud_id] for r in recs]
    reply = ask(live, w, "resync", 1, recording_ids=tuple(named))
    assert reply.state == "done"
    assert reply.message == "4 checked: 2 updated (speakers 2, title 1) · 2 up to date"
    assert seen[0] == (0, 4, "Re-syncing") and seen[-1][:2] == (4, 4)
    assert sorted(reply.result["updated_ids"]) == sorted(named[:2])
    no_title(live, reply.message, reply.result)


def test_the_other_one_recording_lines(live):
    # §14.6's replies: kept out, consent-listed, gone, not from Plaud, edit-sync paused, failed.
    w, here, recs = ready(live, 5)
    a, b, c, d, e = (here[r.plaud_id] for r in recs)
    live.db().set_listing(recs[0].plaud_id, kept_out_at=live.clock(), kept_out_word="noarchive")
    assert ask(live, w, "resync", 1, recording_ids=(a,)).message == "Not checked: kept out"
    live.consent_ids(recs[1].plaud_id)
    w.tick()
    assert ask(live, w, "resync", 2, recording_ids=(b,)).message == (
        "Not checked: on the consent list"
    )
    live.db().mark_gone([c], now=live.clock())
    assert ask(live, w, "resync", 3, recording_ids=(c,)).message == "Gone from Plaud"
    # Edit-sync paused: the breaker open, and a change only in "other".
    live.db().put(K_BREAKER, {"open": True, "since": T0.isoformat(), "window_start": None}, now=T0)
    assert breaker.is_open(live.db())
    live.account.send_field("synthetic_flag", True, plaud_ids=[recs[3].plaud_id])
    assert ask(live, w, "resync", 4, recording_ids=(d,)).message == "Edit-sync paused: see Status"
    live.db().delete(K_BREAKER)
    live.server.fault(FILE_PATH.format(f"of_{recs[4].plaud_id}"), server_error(503))
    reply = ask(live, w, "resync", 5, recording_ids=(e,))
    assert (reply.state, reply.message) == ("failed", "Failed: Plaud unavailable")


def test_a_recording_not_from_plaud_is_skipped(live):
    w, here, recs = ready(live, 1)
    stray = "20261001T100000-0700_abcdef01"  # an archive ID, but nothing here holds it
    reply = ask(live, w, "resync", 1, recording_ids=(stray,))
    assert (reply.state, reply.message) == ("done", "Not checked: it didn't come from Plaud")
    rid = here[recs[0].plaud_id]
    reply = ask(live, w, "resync", 2, recording_ids=(rid, stray))
    assert reply.message == "1 checked: 1 up to date · 1 not checked (no Plaud source 1)"


def test_refused_before_the_catch_up_and_while_a_hold_is_on(live):
    live.account.add()
    w = live.started()  # the gate is closed
    stray = "20261001T100000-0700_abcdef01"
    reply = ask(live, w, "resync", 1, recording_ids=(stray,))
    assert (reply.state, reply.message) == ("refused", GATE_REFUSAL)
    live.catch_up_done()
    w.tick()  # the first scheduled turn; the next isn't due for 20 minutes
    live.db().put(K_HOLD, {"since": T0.isoformat()}, now=T0)
    before = len(live.server.requests)
    reply = ask(live, w, "resync", 2, recording_ids=(stray,))
    assert (reply.state, reply.message) == ("failed", "Held: see Status")
    assert len(live.server.requests) == before  # no Plaud call


def test_a_second_press_merges_into_the_queued_one(live):
    # §9.3: a resync merges into a queued one; one already covered answers "Already queued".
    w, here, recs = ready(live, 3)
    a, b, c = (here[r.plaud_id] for r in recs)
    w._beat()
    assert put(live, "resync", 1, recording_ids=(a,)).message == requests.QUEUED
    assert put(live, "resync", 2, recording_ids=(b, c)).message == requests.QUEUED
    assert put(live, "resync", 3, recording_ids=(c,)).message == ALREADY_QUEUED
    w.tick()
    leader = requests.read(w.state, "request-0001")
    assert leader.message == "3 checked: 3 up to date"
    for n in (2, 3):
        assert requests.read(w.state, f"request-{n:04d}").message == leader.message
    assert set(leader.result["each"]) == {a, b, c}


def test_a_parked_recording_is_tried_and_a_success_unparks_it(live):
    w, here, recs = ready(live, 1)
    rid = here[recs[0].plaud_id]
    reply = ask(live, w, "park", 1, recording_ids=(rid,))
    assert (reply.state, reply.message) == ("done", resync.PARKED_LINE)
    assert reply.result == {"parked": [rid], "nothing": []}
    assert live.db().check_row(rid).parked_at is not None
    assert ask(live, w, "park", 2, recording_ids=(rid,)).message == resync.NOTHING_TO_PARK
    reply = ask(live, w, "resync", 3, recording_ids=(rid,))
    assert reply.message == "Up to date" and reply.result["unparked"] == [rid]
    assert live.db().check_row(rid).parked_at is None


def test_park_and_unpark_from_the_failing_line(live):
    # Status's "fails every check · Re-sync · Park" (Dan, Phase 1's Q&A §6): one press acts on
    # every recording the line counts. Parked, the sweep leaves it alone and the line drops it;
    # unparked, it is checked at the next run.
    w, here, recs = ready(live, 2)
    rids = [here[r.plaud_id] for r in recs]
    db = live.db()
    for rid in rids:
        db.set_check(rid, failures=FAILING_AFTER, failing_since=live.clock())
    assert {r.recording_id for r in db.failing_checks()} == set(rids)
    reply = ask(live, w, "park", 1, recording_ids=tuple(rids))
    assert reply.result["parked"] == rids
    assert db.failing_checks() == []
    wr = w.writer
    found = here_from_archive(wr.index, wr.identity.uuid, wr.state, wr.archive, db)
    rows = {r.recording_id: r for r in db.check_rows()}
    assert not sweep.covered(found, rows, db, consent_ids=()) & set(rids)
    reply = ask(live, w, "unpark", 2, recording_ids=(rids[0],))
    assert (reply.state, reply.message) == ("done", resync.UNPARKED_LINE)
    row = db.check_row(rids[0])
    assert row.parked_at is None and row.next_check_at <= live.clock()


def test_a_resync_then_a_restart_never_reads_as_rebuilt(live):
    # C5.23: its snapshot goes through Writer.add_snapshot, which commits the mark first.
    w, here, recs = ready(live, 1)
    rid = here[recs[0].plaud_id]
    live.account.rename_speaker(recs[0].plaud_id, "Synthetic Grace")
    assert ask(live, w, "resync", 1, recording_ids=(rid,)).message == "Updated: speakers"
    live.started()  # a restart
    assert live.db().get(K_REBUILT) is None


def test_c2_01_renames_show_at_once_with_resync(live):
    # C2.01's Re-sync half: renames on many recordings, all shown by one Re-sync of the selection,
    # with no wait for the sweep (B1's test covers the sweep's half at 1,000 recordings).
    w, here, recs = ready(live, 12)
    renamed = recs[:5]
    for rec in renamed:
        live.account.rename_speaker(rec.plaud_id, "Synthetic Ada")
    named = tuple(here[r.plaud_id] for r in recs)
    reply = ask(live, w, "resync", 1, recording_ids=named)
    assert reply.message == "12 checked: 5 updated (speakers 5) · 7 up to date"
    assert sorted(reply.result["updated_ids"]) == sorted(here[r.plaud_id] for r in renamed)


# ---- the CLI ---------------------------------------------------------------------------------
@pytest.fixture
def cli(live, monkeypatch, capsys):
    monkeypatch.setenv("RECORDINGS_CONFIG", live.environ["RECORDINGS_CONFIG"])
    monkeypatch.setattr(runlock, "CLI_WAIT", 0.2)
    live.store().install(from_vendor(live.fake.issue(life=timedelta(days=36500)), now=T0))

    def run(*args: str):
        code = main(["plaud", *args])
        out = capsys.readouterr()
        return code, out.out, out.err

    return run


def test_the_cli_resync_park_and_held(live, cli):
    _w, here, recs = ready(live, 2)
    a, b = (here[r.plaud_id] for r in recs)
    live.account.rename_speaker(recs[0].plaud_id, "Synthetic Ada")
    code, out, err = cli("resync", a, b)
    assert code == 0, err
    assert "result: 2 checked: 1 updated (speakers 1) · 1 up to date" in out
    assert f"{a}: Updated: speakers" in out
    code, out, _ = cli("park", b)
    assert code == 0 and f"parked (1): {b}" in out
    assert live.db().check_row(b).parked_at is not None
    code, out, _ = cli("park", b, "--undo")
    assert code == 0 and live.db().check_row(b).parked_at is None
    code, out, _ = cli("park", "not-an-id")
    assert code == 1
    # A name back to "Speaker 1" is a removal reconcile holds (§14.7).
    live.account.rename_speaker(recs[0].plaud_id, "Speaker 1")
    code, out, err = cli("resync", a)
    assert code == 0, err
    code, out, _ = cli("held")
    assert code == 0 and f"{a} · " in out and "names removed" in out
    assert "1 change from Plaud held for your review" in out
    code, out, _ = cli("held", "--json")
    assert '"recording_id"' in out
    no_title(live, out)


def test_the_cli_resync_waits_for_the_catch_up(live, cli):
    code, out, err = cli("resync", "20261001T100000-0700_abcdef01")
    assert code == 1 and f"result: {GATE_REFUSAL}" in out, err


def test_failure_phrases_are_fixed():
    assert resync.failure_phrase("PlaudUnavailable") == "Plaud unavailable"
    assert resync.failure_phrase("WriterError") == "error: `WriterError`"
    assert resync.failure_phrase(None) == "error: `Exception`"
