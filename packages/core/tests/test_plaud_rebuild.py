"""The gate's later states and the review after a rebuild (stage 2b spec §11.6; area A4): the
snapshot mark before every 2b snapshot, an interrupted first catch-up, a rebuilt `state.db` found
at the worker's start and recorded once, the review marks at listing time, `recordings plaud
review` after a restore, and decision 15's "the sync's record was rebuilt". Against the fake
Plaud server, through the import. Every ID and title is synthetic."""

import io
import json
from datetime import timedelta

import pytest
from plaud_scene import T0
from recordings.plaud.checks import (
    K_CATCH_UP_DONE,
    K_REBUILT,
    K_SNAPSHOT_MARK,
    PlaudDB,
)
from recordings.plaud.compare import MISSING, REVIEW, WAITING, take_listing
from recordings.plaud.gate import (
    GATE_FIRST,
    GATE_INTERRUPTED,
    detect_rebuild,
    gate_now,
    has_2b_snapshot,
)
from recordings.plaud.importer import EXCLUDED_REVIEW
from recordings.plaud.privacy import why_phrase
from recordings.tagops import RemoveTags


def lose_the_sync_records(scene) -> None:
    """state.db's 2b records are gone (a rebuilt state.db): the marks, the gate, the listing."""
    with scene.writer.state.db() as conn:
        conn.execute("DELETE FROM plaud_state")
        conn.execute("DELETE FROM plaud_listing")
        conn.execute("DELETE FROM plaud_private_checked")


def sync(scene, *, later: timedelta = timedelta(minutes=20)):
    scene.now += later
    listing = scene.listing()
    result, _, _ = take_listing(
        scene.db,
        scene.cache,
        listing,
        here=scene.here(),
        consent=scene.consent(),
        words=("noarchive",),
        patterns=(),
        gate_open=gate_now(scene.db).open,
        sync=True,
        now=scene.now,
    )
    return {row.canonical: row for row in result.rows}, listing


def test_the_mark_comes_before_every_2b_snapshot(scene):
    # §11.6: committed in its own transaction before the snapshot, by every path.
    assert scene.db.get(K_SNAPSHOT_MARK) is None
    for n in range(2):
        scene.import_(scene.account.add())
        assert scene.db.get(K_SNAPSHOT_MARK)["count"] == n + 1


def test_an_interrupted_first_catch_up_keeps_the_gate_closed_and_says_so(scene):
    # C5.19: marks, and no catch_up_done_at.
    scene.db.delete(K_CATCH_UP_DONE)
    assert gate_now(scene.db).reason == GATE_FIRST
    scene.importer.catch_up = True
    scene.import_(scene.account.add())  # the catch-up, stopped after one recording
    gate = gate_now(scene.db)
    assert (gate.open, gate.reason) == (False, GATE_INTERRUPTED)
    # C5.20's first half: run again, it imports what isn't here (A5 sets catch_up_done_at).
    second = scene.account.add()
    assert scene.import_(second).outcome == "imported"


def test_a_catch_up_then_a_restart_never_reads_as_rebuilt(scene):
    # C5.22: the mark is committed before the snapshot, so 2b snapshots always have a mark.
    scene.import_(scene.account.add())
    assert has_2b_snapshot(scene.writer.archive)
    assert not detect_rebuild(scene.db, scene.writer.archive, now=scene.now)
    assert scene.db.get(K_REBUILT) is None


def test_a_first_start_with_only_2a_snapshots_isnt_a_rebuild(scene):
    assert not has_2b_snapshot(scene.writer.archive)
    assert not detect_rebuild(scene.db, scene.writer.archive, now=scene.now)
    assert scene.db.get(K_SNAPSHOT_MARK) is None


def test_a_rebuilt_state_db_is_recorded_once_and_passed(scene):
    # §11.6 (C5.24): 2b snapshots, no mark row: rebuilt_at, the gate open and a mark, at once.
    scene.import_(scene.account.add())
    lose_the_sync_records(scene)
    assert not gate_now(scene.db).open
    assert detect_rebuild(scene.db, scene.writer.archive, now=scene.now)
    assert scene.db.get(K_REBUILT) == {"at": scene.now.isoformat(), "by": "detected"}
    assert gate_now(scene.db).open and scene.db.get(K_SNAPSHOT_MARK)["by"] == "rebuild"
    assert not detect_rebuild(scene.db, scene.writer.archive, now=scene.now)  # not again


def test_after_a_rebuild_what_was_made_before_waits_for_review(scene):
    # C5.25: recordings created after it keep importing; one created before it that isn't here
    # needs review, in the first listing or later; Import all leaves it out; Import selected
    # brings it in; its ID on the consent list keeps it out.
    here = scene.account.add("Synthetic, here")
    scene.import_(here)
    before = scene.account.add("Synthetic, made before")
    later_seen = scene.account.add("Synthetic, listed later")
    consented = scene.account.add("Synthetic, consent-listed")
    scene.account.remove(later_seen.plaud_id)  # missed by the first listing (page drift)
    lose_the_sync_records(scene)
    assert detect_rebuild(scene.db, scene.writer.archive, now=scene.now)
    scene.consent_ids(consented.plaud_id)
    after = scene.account.add("Synthetic, made after", start_at=scene.now + timedelta(minutes=5))
    rows, listing = sync(scene)
    assert (rows[before.plaud_id].bucket, rows[before.plaud_id].state) == (MISSING, REVIEW)
    assert rows[before.plaud_id].review and rows[before.plaud_id].listed.title is not None
    assert rows[after.plaud_id].bucket == WAITING
    assert rows[consented.plaud_id].bucket == "excluded"
    assert scene.db.listing(before.plaud_id).needs_review
    # The import, "Import all" or a scheduled run, leaves it out; named, it comes in.
    listed = {r.canonical_id: r for r in listing.rows}
    result = scene.importer.import_one(listed[before.plaud_id], listing_at=scene.now)
    assert (result.outcome, result.why) == ("excluded", EXCLUDED_REVIEW)
    assert scene.import_(after).outcome == "imported"
    assert (
        scene.importer.import_one(
            listed[before.plaud_id], listing_at=scene.now, review_ok=True
        ).outcome
        == "imported"
    )
    assert not scene.db.listing(before.plaud_id).needs_review
    # One first listed later, made before the rebuild, needs review too.
    scene.account.recordings.append(later_seen)
    rows, _ = sync(scene)
    assert rows[later_seen.plaud_id].state == REVIEW


def test_review_rows_show_no_title_in_the_cache(scene):
    scene.import_(scene.account.add())
    before = scene.account.add("Synthetic, made before")
    lose_the_sync_records(scene)
    detect_rebuild(scene.db, scene.writer.archive, now=scene.now)
    sync(scene)
    (row,) = [r for r in scene.cache.compare_rows() if r.canonical_plaud_id == before.plaud_id]
    assert (row.review, row.title) == (True, None)


def test_a_private_put_back_after_a_rebuild_says_why(scene):
    # Decision 15: case 4 after a rebuild is "the sync's record was rebuilt".
    rec = scene.account.add("Private: synthetic one-to-one")
    rid = scene.import_(rec).recording_id
    scene.writer.mutate(rid, RemoveTags(("private",), may_expose=frozenset({rid})))
    lose_the_sync_records(scene)
    detect_rebuild(scene.db, scene.writer.archive, now=scene.now)
    scene.writer.reconcile_plaud(rid)
    notes = [
        e.detail.get("why")
        for e in scene.db.events_since(T0 - timedelta(days=1), ["retagged-private"])
    ]
    assert notes == [why_phrase("rebuilt")] == ["the sync's record was rebuilt"]


def test_the_worker_finds_a_rebuild_at_its_start(scene, tmp_path):
    from recordings.config import load_config
    from recordings.worker import Worker

    scene.import_(scene.account.add())
    lose_the_sync_records(scene)
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[archive]\npath = "{scene.root}"\nwriter_id = "test"\n'
        f'[state]\npath = "{scene.state_path}"\n'
        f'[index]\npath = "{tmp_path / "index" / "index.db"}"\n'
        f'[plaud]\nconsent_list = "{scene.consent_path}"\n',
        encoding="utf-8",
    )
    environ = {"RECORDINGS_CONFIG": str(cfg_file)}
    w = Worker(load_config(environ), environ, clock=lambda: T0, heartbeat=3600)
    try:
        w.start()
        assert w.gate.open and PlaudDB(w.state).get(K_REBUILT)["by"] == "detected"
    finally:
        w.stopping.set()


def test_a_rebuild_check_that_fails_at_the_workers_start_is_tried_again_at_its_ticks(
    scene, tmp_path, monkeypatch
):
    # The end-of-branch privacy review (2026-11-02): the start's check raised once and was never
    # tried again, so the gate stayed closed. The tick tries again until it answers.
    from recordings import worker as worker_module
    from recordings.config import load_config
    from recordings.worker import Worker

    scene.import_(scene.account.add())
    lose_the_sync_records(scene)
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[archive]\npath = "{scene.root}"\nwriter_id = "test"\n'
        f'[state]\npath = "{scene.state_path}"\n'
        f'[index]\npath = "{tmp_path / "index" / "index.db"}"\n'
        f'[plaud]\nconsent_list = "{scene.consent_path}"\n',
        encoding="utf-8",
    )
    calls: list[int] = []

    def once_broken(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("synthetic")
        return detect_rebuild(*args, **kwargs)

    monkeypatch.setattr(worker_module, "detect_rebuild", once_broken)
    environ = {"RECORDINGS_CONFIG": str(cfg_file)}
    w = Worker(load_config(environ), environ, clock=lambda: T0, heartbeat=3600)
    try:
        w.start()
        assert not w.gate.open and PlaudDB(w.state).get(K_REBUILT) is None
        w.tick()
        assert w.gate.open and PlaudDB(w.state).get(K_REBUILT)["by"] == "detected"
        w.tick()
        assert len(calls) == 2  # answered: not tried again
    finally:
        w.stopping.set()


def test_a_rebuild_is_recorded_once_even_when_two_runs_check_at_once(scene):
    # "Recording the rebuild stays one transaction, so it is found once": the check for a mark
    # is inside the transaction that records it, so a second check that had read "no mark"
    # before the first committed records nothing.
    scene.import_(scene.account.add())
    lose_the_sync_records(scene)
    assert scene.db.record_rebuilt(
        at=scene.now, by="detected", mark={"count": 1}, unless_marked=True, now=scene.now
    )
    later = scene.now + timedelta(minutes=1)
    assert not scene.db.record_rebuilt(
        at=later, by="detected", mark={"count": 1}, unless_marked=True, now=later
    )
    assert scene.db.get(K_REBUILT)["at"] == scene.now.isoformat()


def test_a_rebuild_marks_listing_rows_written_before_it_for_review(scene):
    # Defence in depth: a row a listing wrote before the rebuild was recorded (no run lists
    # first now, but an older release's might have) is marked as `plaud review` marks it, so the
    # review marks never skip it.
    scene.import_(scene.account.add())
    lose_the_sync_records(scene)
    waiting = scene.account.add("Synthetic, listed before the rebuild was found")
    scene.db.set_listing(f"of_{waiting.plaud_id}", first_seen=scene.now)
    assert detect_rebuild(scene.db, scene.writer.archive, now=scene.now)
    assert scene.db.listing(waiting.plaud_id).needs_review
    rows, _ = sync(scene)
    assert rows[waiting.plaud_id].state == REVIEW


def run_review(scene, tmp_path, monkeypatch) -> tuple[int, str]:
    import contextlib

    from recordings.cli import main

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[archive]\npath = "{scene.root}"\nwriter_id = "test"\n'
        f'[state]\npath = "{scene.state_path}"\n'
        f'[index]\npath = "{tmp_path / "index" / "index.db"}"\n'
        f'[plaud]\nconsent_list = "{scene.consent_path}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg_file))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(["plaud", "review", "--json"])
    return code, out.getvalue()


def test_plaud_review_after_a_restore_marks_what_isnt_here(scene, tmp_path, monkeypatch):
    # C5.27 and C5.28: a state.db restored from an older backup still has marks, so it passes;
    # `plaud review` records rebuilt_at as now, holding `plaud`, and what isn't here waits.
    here = scene.account.add("Synthetic, here")
    scene.import_(here)
    waiting = scene.account.add("Synthetic, listed and waiting")
    sync(scene)
    assert not scene.db.listing(waiting.plaud_id).needs_review
    code, out = run_review(scene, tmp_path, monkeypatch)
    assert code == 0, out
    report = json.loads(out)
    assert report["review"] == 1 and "Synthetic" not in out
    assert scene.db.get(K_REBUILT)["by"] == "plaud review"
    assert scene.db.listing(waiting.plaud_id).needs_review
    assert not scene.db.listing(here.plaud_id).needs_review
    newly = scene.account.add("Synthetic, first listed after the review")
    rows, _ = sync(scene)
    assert rows[newly.plaud_id].state == REVIEW and rows[waiting.plaud_id].state == REVIEW


def test_plaud_review_waits_for_a_run_holding_plaud(scene, tmp_path, monkeypatch):
    from recordings.plaud import runlock

    monkeypatch.setattr(runlock, "CLI_WAIT", 0.1)
    with runlock.plaud_run(scene.writer.state, "plaud-sync", timeout=1, now=T0):
        code, _out = run_review(scene, tmp_path, monkeypatch)
    assert code == 75 and scene.db.get(K_REBUILT) is None


@pytest.mark.parametrize("value", [{"at": "not a time"}, "junk"])
def test_a_rebuild_record_that_cant_be_read_marks_everything_for_review(scene, value):
    # Fail closed: only a recording made before the rebuild could have been kept out before it.
    rec = scene.account.add("Synthetic", start_at=scene.now + timedelta(days=30))
    scene.db.put(K_REBUILT, value, now=T0)
    rows, _ = sync(scene)
    assert rows[rec.plaud_id].state == REVIEW
