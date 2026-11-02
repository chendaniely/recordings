"""Sync everything (stage 2b spec §14.6, §20 criterion 3, §9.2's long runs; area B2), run by the
worker and the CLI against the fake Plaud server: C3.01 (a sound account exits 0 and writes the
manifest), C3.02 (a degraded recording still exits 0), C3.03 (a flagged one exits 0 and is named),
C3.04 (an unparked failing one exits 2 with its count), C3.05 (killed part-way, it resumes), C5.26
(after a rebuild it leaves out what needs review), the press's refusals, the monthly run, the
retired sync, and what a long run serves between two recordings. Every account, title, token and
byte is synthetic (plaud_live.py); nothing reaches Plaud."""

import json
from datetime import timedelta

import pytest
from fake_plaud import expired_link
from plaud_live import T0
from recordings.cli import main
from recordings.plaud import editsync, full, requests, runlock
from recordings.plaud.checks import (
    FAILING_AFTER,
    K_FULL,
    K_HOLD,
    K_LAST_STARTED,
    K_SNAPSHOT_MARK,
)
from recordings.plaud.requests import PlaudRequest
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
    put(live, kind, n, **fields)
    w.tick()
    return requests.read(w.state, f"request-{n:04d}")


def ready(live, count: int):
    recs = live.account.add_many(count)
    live.catch_up_done()
    w = live.started()
    w.tick()
    here = live.here()
    assert len(here) == count
    live.clock.advance(minutes=5)  # later than the imports: Sync everything checks them again
    return w, here, recs


def titles(live) -> list[str]:
    return [r.name for r in live.account.recordings if r.name]


def manifest(live, name: str) -> dict:
    return json.loads((live.state_path / "manifests" / name).read_text(encoding="utf-8"))


def test_c3_01_a_sound_account_exits_0_and_writes_the_manifest(live, monkeypatch):
    w, here, recs = ready(live, 3)
    live.account.rename_speaker(recs[0].plaud_id, "Synthetic Ada")
    assert full.estimate(live.db()) == None  # noqa: E711  "unknown until the first run"
    real = editsync.Checker.check

    def slow(self, rid, listed, **kw):
        live.clock.advance(seconds=40)  # each check takes 40 seconds: the pace it measures
        return real(self, rid, listed, **kw)

    monkeypatch.setattr(editsync.Checker, "check", slow)
    reply = ask(live, w, "full", 1)
    assert (reply.state, reply.message) == ("done", "Sync everything: all 3 checked")
    result = reply.result
    assert result["exit_code"] == 0 and result["ok"] and result["covered"] == 3
    body = manifest(live, result["manifest"])
    assert {e["recording_id"] for e in body["recordings"]} == set(here.values())
    assert {e["state"] for e in body["recordings"]} == {"checked"}
    for entry in body["recordings"]:
        assert entry["last_snapshot"].startswith("source/")
        assert entry["parts"] and not {"title", "speakers"} & set(entry["parts"])
    text = json.dumps(body)
    assert all(t not in text for t in titles(live)) and "Synthetic Ada" not in text
    last = full.last(live.db())
    assert last["exit_code"] == 0 and full.running(live.db()) is None
    assert last["pace"] == pytest.approx(40, abs=1)
    assert full.estimate(live.db()) == "about 2 minutes"  # 40 s × the 3 recordings covered
    assert (live.state_path / "manifests").stat().st_mode & 0o777 == 0o700


def test_c3_02_a_degraded_recording_still_exits_0(live, plaud_server):
    w, _here, recs = ready(live, 2)
    note = f"/notes/{recs[0].plaud_id}.md"
    plaud_server.fault(lambda path, _q: path == note, expired_link(), host="storage")
    reply = ask(live, w, "full", 1)
    assert reply.result["exit_code"] == 0
    assert reply.message == "Sync everything: all 2 checked (1 degraded)"


def test_c3_03_a_flagged_recording_still_exits_0_and_is_named(live):
    w, here, recs = ready(live, 2)
    flagged = here[recs[0].plaud_id]
    live.consent_ids(recs[0].plaud_id)  # consent-listed after it came in: a late consent listing
    w.tick()
    reply = ask(live, w, "full", 1)
    assert reply.result["exit_code"] == 0 and reply.result["flagged"] == [flagged]
    assert reply.message == "Sync everything: all 1 checked (1 flagged)"
    body = manifest(live, reply.result["manifest"])
    states = {e["recording_id"]: e["state"] for e in body["recordings"]}
    assert states[flagged] == "flagged"


def test_c3_04_an_unparked_failing_recording_exits_2_and_a_parked_one_0(live, plaud_server):
    from fake_plaud import html_page
    from recordings.plaud.client import FILE_PATH

    w, here, recs = ready(live, 3)
    bad = here[recs[0].plaud_id]
    plaud_server.fault(FILE_PATH.format(f"of_{recs[0].plaud_id}"), html_page())
    reply = ask(live, w, "full", 1)
    assert reply.result["exit_code"] == 2 and reply.result["failed_ids"] == [bad]
    assert reply.message == "Sync everything: 2 of 3 fine: 1 failed · See Status"
    assert reply.result["newly_failing"] == [bad] and not full.last(live.db())["ok"]
    live.db().set_check(bad, parked_at=live.clock())
    live.clock.advance(hours=2)  # past the hour a press waits after the last one
    reply = ask(live, w, "full", 2)
    assert reply.result["exit_code"] == 0
    assert reply.message == "Sync everything: all 2 checked (1 parked)"


def test_an_outage_part_way_fails_the_run_and_status_says_so(live, plaud_server):
    # §14.6: a run-level failure shows on Status's Sync everything line; it finishes (a failed one
    # waits for next month or a press), so it is never left to resume.
    from fake_plaud import server_error
    from recordings.plaud.client import FILE_PATH

    w, _here, recs = ready(live, 3)
    plaud_server.fault(FILE_PATH.format(f"of_{recs[0].plaud_id}"), server_error(503))
    reply = ask(live, w, "full", 1)
    assert reply.state == "failed"
    assert reply.message == "Sync everything: failed (Plaud unavailable) · See Status"
    assert reply.result["exit_code"] == 69 and reply.result["ok"] is False
    last = full.last(live.db())
    assert last["ok"] is False and last["line"] == "failed (Plaud unavailable)"
    assert full.running(live.db()) is None
    assert not full.due(live.db(), now=live.clock(), schedule_minutes=20)


def test_c3_05_killed_part_way_it_resumes(live, monkeypatch):
    w, here, _recs = ready(live, 4)
    checked: list[str] = []
    real = editsync.Checker.check

    def counting(self, rid, listed, **kw):
        checked.append(rid)
        if len(checked) == 2:
            w.stopping.set()  # SIGTERM: the run stops between this recording and the next
        return real(self, rid, listed, **kw)

    monkeypatch.setattr(editsync.Checker, "check", counting)
    reply = ask(live, w, "full", 1)
    assert (reply.state, reply.message) == ("failed", requests.INTERRUPTED)
    unfinished = full.running(live.db())
    assert unfinished is not None and unfinished["done"] == 2
    first_two = list(checked)
    live.clock.advance(minutes=1)
    w2 = live.started()  # a restart: the worker resumes it at its first tick
    w2.tick()
    assert full.running(live.db()) is None
    last = full.last(live.db())
    assert last["exit_code"] == 0 and last["line"] == "all 4 checked"
    assert not set(first_two) & set(checked[2:])  # what was checked since it started isn't again
    assert sorted(checked) == sorted(here.values())


def test_a_hold_recorded_mid_run_stops_its_checks_and_it_resumes_once_the_hold_clears(
    live, monkeypatch
):
    # The end-of-branch privacy review (2026-11-02): a hold recorded mid-run (a light turn between
    # two recordings found Plaud's IDs changed) stops further checks and fetches in that run
    # (§14.2: "The sweep pauses under any hold"); it is left unfinished, the tick doesn't resume it
    # while the hold is on, and it resumes once the hold clears.
    w, here, _recs = ready(live, 4)
    checked: list[str] = []
    fetched: list[int] = []
    real = editsync.Checker.check

    def holding(self, rid, listed, **kw):
        result = real(self, rid, listed, **kw)
        checked.append(rid)
        if len(checked) == 2:
            live.db().put(K_HOLD, {"since": live.clock().isoformat(), "ids_changed": True}, now=T0)
            fetched.append(len(live.server.requests))
        return result

    monkeypatch.setattr(editsync.Checker, "check", holding)
    reply = ask(live, w, "full", 1)
    assert (reply.state, reply.message) == (
        "failed",
        "Sync everything: failed (held: see Status) · See Status",
    )
    assert len(checked) == 2 and len(live.server.requests) == fetched[0]
    assert full.running(live.db()) is not None and full.last(live.db()) is None
    live.clock.advance(minutes=1)
    w.tick()  # still held: nothing resumes
    assert len(checked) == 2 and full.running(live.db()) is not None
    live.db().delete(K_HOLD)
    live.clock.advance(minutes=1)
    w.tick()  # the hold cleared: it resumes, and checks only what it hadn't
    assert full.running(live.db()) is None
    last = full.last(live.db())
    assert last["exit_code"] == 0 and last["line"] == "all 4 checked"
    assert sorted(checked) == sorted(here.values())


def test_c5_26_after_a_rebuild_it_leaves_out_what_needs_review(live):
    before = live.account.add()  # made before the rebuild, and not here
    live.catch_up_done()
    db = live.db()
    db.put(K_SNAPSHOT_MARK, {"count": 1}, now=T0)
    db.record_rebuilt(at=T0, by="detected", now=T0)
    after = live.account.add(start_at=T0 + timedelta(hours=1))
    w = live.started()
    w.tick()
    assert set(live.here()) == {after.plaud_id}
    live.clock.advance(minutes=5)
    reply = ask(live, w, "full", 1)
    assert set(live.here()) == {after.plaud_id}  # still not imported
    assert reply.result["exit_code"] == 2
    assert reply.message == "Sync everything: 1 of 2 fine: 1 need your review · See Status"
    assert before.plaud_id in {
        e["plaud_id"]
        for e in manifest(live, reply.result["manifest"])["recordings"]
        if e["state"] == "needs review"
    }


def test_a_press_is_refused_when_queued_running_or_recent_and_resumes_an_unfinished_one(live):
    w, _here, _recs = ready(live, 1)
    w._beat()
    assert put(live, "full", 1).state == "queued"
    second = put(live, "full", 2)
    assert (second.state, second.message) == ("refused", requests.ALREADY_QUEUED)
    w.tick()
    assert requests.read(w.state, "request-0001").state == "done"
    recent = put(live, "full", 3)
    assert (recent.state, recent.message) == ("refused", full.RECENT)
    # An unfinished one: the press resumes it.
    db = live.db()
    db.put(K_FULL, {"running": {"started": live.clock().isoformat(), "done": 0}}, now=T0)
    hhmm = requests._hhmm(live.clock(), TZ)
    resumed = put(live, "full", 4)
    assert (resumed.state, resumed.message) == ("queued", f"Resuming (started {hhmm})")
    w.tick()  # the worker resumes it
    assert requests.read(w.state, "request-0004").state == "done"
    # While one runs (holding `plaud`), a second press says so, and other requests wait.
    db.put(K_FULL, {"running": {"started": T0.isoformat(), "done": 412, "total": 1234}}, now=T0)
    with runlock.plaud_run(db.state, "plaud-full", timeout=1, now=T0):
        busy = put(live, "full", 5)
        assert (busy.state, busy.message) == (
            "refused",
            "Sync everything is running (412 of 1,234)",
        )
        assert put(live, "sync", 6).message == "Waiting for Sync everything (412 of 1,234)"


def test_the_worker_runs_it_30_days_after_the_last_and_never_while_retired(live):
    w, _here, _recs = ready(live, 1)
    db = live.db()
    assert full.last(db) is None and not full.due(db, now=live.clock(), schedule_minutes=20)
    live.clock.advance(days=30)
    assert full.due(db, now=live.clock(), schedule_minutes=20)  # 30 days after the catch-up
    assert not full.due(db, now=live.clock(), schedule_minutes=0)  # the schedule off
    full.retire(db, now=live.clock())
    assert not full.due(db, now=live.clock(), schedule_minutes=20)
    assert not w.due()  # no scheduled turn either
    full.retire(db, now=live.clock(), undo=True)
    w.tick()  # the scheduled turn, then Sync everything by itself
    last = full.last(db)
    assert last is not None and last["exit_code"] == 0
    live.clock.advance(days=29)
    assert not full.due(db, now=live.clock(), schedule_minutes=20)
    live.clock.advance(days=1, minutes=1)
    assert full.due(db, now=live.clock(), schedule_minutes=20)


def test_between_two_recordings_it_serves_a_one_recording_resync_and_the_light_turn(live):
    # §9.2: inside a long run, a due scheduled sync's light turn (which pings), and a Re-sync of
    # one recording, run between two recordings; a Re-sync of several waits its turn.
    w, here, recs = ready(live, 3)
    a, b, c = (here[r.plaud_id] for r in recs)
    w._beat()
    put(live, "full", 1)
    put(live, "resync", 3, recording_ids=(b, c))  # several: never served inside
    assert requests.claim_next(w.state, now=live.clock(), inside=True) is None
    state = live.db().state
    with state.db() as conn:  # a later press of one recording, kept apart from the queued one
        conn.execute("UPDATE plaud_requests SET state = 'done' WHERE request_id = 'request-0003'")
    put(live, "resync", 2, recording_ids=(a,))
    with state.db() as conn:
        conn.execute("UPDATE plaud_requests SET state = 'queued' WHERE request_id = 'request-0003'")
    pings = len(live.pings())
    started = live.db().get(K_LAST_STARTED)
    # A scheduled turn is due: the tick serves the request first, so the turn comes due inside
    # Sync everything, whose light turn takes it (the tick's own turn after it isn't due again).
    live.clock.advance(minutes=20)
    w.tick()
    assert requests.read(w.state, "request-0001").state == "done"
    assert requests.read(w.state, "request-0002").message == "Up to date"  # served inside
    assert requests.read(w.state, "request-0003").state == "queued"  # several: waits
    assert live.db().get(K_LAST_STARTED) != started  # the light turn ran
    assert len(live.pings()) == pings + 1


def test_the_constants_requests_keeps_match_their_sources():
    # why: requests.py keeps copies (it imports on Pyodide, and checks needs more than the
    # standard library there); a renamed key or phrase would let a second press through.
    assert requests.FULL_KEY == K_FULL
    assert (requests.FULL_RECENT, requests.FULL_COOLDOWN) == (full.RECENT, full.COOLDOWN)


def test_the_worker_makes_the_manifests_folder_at_its_start(live):
    assert not (live.state_path / "manifests").exists()
    live.started()
    assert (live.state_path / "manifests").is_dir()


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


def test_the_cli_sync_full_and_retire(live, cli):
    code, out, err = cli("sync", "--full")
    assert code == 1  # before the first catch-up
    _w, here, _recs = ready(live, 2)
    db = live.db()
    for rid in here.values():  # the CLI reads the real clock: these checks are old to it
        db.set_check(rid, last_checked_at=T0 - timedelta(days=30))
    code, out, err = cli("sync", "--full")
    assert code == 0, err
    assert "result: all 2 checked" in out and "manifest: manifests/plaud-" in out
    assert all(t not in out + err for t in titles(live))
    rid = next(iter(here.values()))
    db.set_check(rid, failures=FAILING_AFTER, failing_since=T0)
    code, out, _ = cli("retire")
    assert code == 0 and "retired since" in out
    code, out, _ = cli("retire", "--undo")
    assert code == 0 and "isn't retired" in out
