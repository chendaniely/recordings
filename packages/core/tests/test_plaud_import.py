"""Importing one recording (stage 2b spec §11.5; area A4), against the fake Plaud server: the checks
before any byte, the envelope's title checked before any block or audio, the snapshot and its
`_meta`, auto-private at import, the disk guard, `.tmp/plaud-dl/`, a fresh link, failures counted,
a hash merge into a flagged recording discarded, the snapshot mark, and the records an import
leaves. Every ID, title and byte is synthetic."""

import json
import time
from datetime import timedelta

import pytest
from plaud_scene import NEVER_LISTED, T0, ZONE
from recordings import selfdoc
from recordings.locks import PLAUD_LOCK, Locks
from recordings.plaud.checks import (
    K_CATCH_UP_DONE,
    K_HOLD,
    K_SNAPSHOT_MARK,
)
from recordings.plaud.client import PlaudUnavailable, id_hash
from recordings.plaud.compare import BY_CONSENT, BY_REKEY, BY_TITLE, BY_WAS_TITLE, title_hash
from recordings.plaud.gate import GATE_REFUSAL
from recordings.plaud.importer import (
    EXCLUDED_FLAGGED,
    EXCLUDED_REVIEW,
    STOPPED_CONSENT,
    STOPPED_DISK,
    STOPPED_HELD,
    ConsentGuard,
    ImportStopped,
    ListingStale,
)
from recordings.plaud.normalise import link_errors
from recordings.plaud.scratch import DOWNLOADS, sweep_downloads


def test_a_waiting_recording_arrives_whole_with_its_snapshot_and_outputs(scene):
    rec = scene.account.add("Synthetic standup")
    result = scene.import_(rec)
    assert (result.outcome, result.private, result.attempt, result.success) == (
        "imported",
        False,
        True,
        True,
    )
    loaded = scene.writer.archive.load(result.recording_id)
    assert loaded.title == "Synthetic standup" and loaded.title_by == "plaud"
    assert loaded.time_source == "plaud" and loaded.timezone == ZONE
    assert loaded.recorded_at == rec.start_at  # Plaud's start time, in the default zone
    assert loaded.recorded_at.utcoffset() == timedelta(hours=-7)
    assert loaded.media.duration_ms == 500 and loaded.media.sample_rate == 8000  # measured
    assert loaded.tags == []
    (source,) = [s for s in loaded.sources if s.kind == "plaud"]
    assert source.ref == f"of_{rec.plaud_id}"  # the ID exactly as listed
    snapshot = json.loads((scene.writer.archive.path_for(loaded.id) / source.raw).read_text())
    meta = snapshot["_meta"]
    assert meta["tool"] == "recordings" and meta["timezone"] == ZONE
    assert meta["time_source"] == "start_at" and meta["recorded_at_utc"].startswith("2026-10-01")
    assert "data_link_errors" not in meta and link_errors(snapshot) == []
    assert snapshot["note_list"][0]["data_content"].startswith("# Summary")  # fetched from its link
    # Reconcile wrote Plaud's outputs: the transcript and the summary.
    kinds = sorted(r.kind for _, r in scene.writer.archive.renditions(loaded.id, problems=[]))
    assert kinds == ["notes", "transcript"]
    # The records: the event, the check row, the title hash, the mark, the revision's origin.
    (event,) = scene.db.events_since(T0 - timedelta(days=1), ["imported", "arrived-private"])
    assert (event.kind, event.recording_id, event.canonical_plaud_id) == (
        "imported",
        loaded.id,
        rec.plaud_id,
    )
    check = scene.db.check_row(loaded.id)
    # Recorded a month before it came in: settled at once (§14.2); its own baseline (§14.5).
    assert check.last_checked_at == T0 and check.settled and check.baseline_pending
    row = scene.db.listing(rec.plaud_id)
    assert row.title_sha256 == title_hash("Synthetic standup") and row.import_failures == 0
    assert scene.db.get(K_SNAPSHOT_MARK)["count"] == 1
    with scene.writer.state.db() as conn:
        origins = {r[0] for r in conn.execute("SELECT origin FROM revisions")}
    assert "plaud-import:run-0001" in origins
    # The scratch download is gone, and the archive validates against 2a's model (C8.02).
    assert not (scene.root / DOWNLOADS).exists()
    assert selfdoc.validate(scene.root, deep=True) == []


def test_a_placeholder_title_arrives_untagged(scene):
    # Decision 1 (C5.01, the import's half): a placeholder title is downloaded at once, untagged.
    rec = scene.account.add("2026-11-02 14:05:31")
    result = scene.import_(rec)
    assert result.outcome == "imported" and not result.private
    assert scene.writer.archive.load(result.recording_id).tags == []


@pytest.mark.parametrize(
    ("listed_name", "envelope_name"),
    [("Private: synthetic one-to-one", None), ("Synthetic catch-up", "interview: synthetic")],
)
def test_a_private_title_arrives_private_before_any_content(scene, listed_name, envelope_name):
    # §11.5 step 4: the listing's title or the envelope's, as given and folded.
    rec = scene.account.add(listed_name)
    rec.envelope_name = envelope_name
    result = scene.import_(rec)
    assert result.outcome == "imported" and result.private
    loaded = scene.writer.archive.load(result.recording_id)
    assert [(t.tag, t.by) for t in loaded.tags] == [("private", "auto")]
    # It was private from its first revision: the tag came in the same rename as recording.json.
    with scene.writer.state.db() as conn:
        first = conn.execute("SELECT body FROM revisions ORDER BY id LIMIT 1").fetchone()[0]
    assert [t["tag"] for t in json.loads(first)["tags"]] == ["private"]
    (event,) = scene.db.events_since(T0 - timedelta(days=1), ["imported", "arrived-private"])
    assert event.kind == "arrived-private"


def test_a_keep_out_title_is_never_requested(scene):
    # C5.02: the keep-out word on the listing's title. Nothing is fetched; the ID is kept out.
    rec = scene.account.add("NoArchive: synthetic coffee chat")
    result = scene.import_(rec)
    assert (result.outcome, result.why, result.attempt) == ("excluded", BY_TITLE, False)
    assert scene.requested(rec) == []
    row = scene.db.listing(rec.plaud_id)
    assert row.kept_out_at == T0 and row.kept_out_word == "noarchive"
    assert row.title_sha256 is None and list(scene.writer.archive.iter_recordings()) == []


def test_a_keep_out_word_only_in_the_envelope_stops_before_any_block_or_audio(scene):
    # §11.5 step 2: renamed between the listing and the fetch. The envelope is read, and nothing
    # behind it: not its note's link, not its audio.
    rec = scene.account.add("Synthetic grant timelines")
    rec.envelope_name = "noarchive synthetic grant timelines"
    result = scene.import_(rec)
    assert (result.outcome, result.why) == ("excluded", BY_TITLE)
    assert len(scene.envelope_requests(rec)) == 1 and scene.link_requests(rec) == []
    assert scene.db.listing(rec.plaud_id).kept_out_word == "noarchive"
    assert list(scene.writer.archive.iter_recordings()) == []
    assert not (scene.root / DOWNLOADS).exists()


def test_a_consent_listed_id_is_never_requested_even_when_listed_since_the_run_began(scene):
    # §11.5 step 1 and C5.12: the list is read again before each recording's first byte.
    first, second = scene.account.add(), scene.account.add()
    listing = scene.listing()
    rows = {r.canonical_id: r for r in listing.rows}
    assert scene.importer.import_one(rows[first.plaud_id], listing_at=T0).outcome == "imported"
    scene.consent_ids(NEVER_LISTED, second.plaud_id)  # Dan's make consent-add, mid-run
    result = scene.importer.import_one(rows[second.plaud_id], listing_at=T0)
    assert (result.outcome, result.why) == ("excluded", BY_CONSENT)
    assert scene.requested(second) == []


def test_a_consent_stop_stops_every_import_before_any_request(scene):
    # C5.09: a typo stops imports, for the run, with nothing fetched.
    rec = scene.account.add()
    listed = scene.row(rec)
    scene.consent_path.write_text("- `of_" + "a" * 31 + "` — a typo\n", encoding="utf-8")
    with pytest.raises(ImportStopped) as stopped:
        scene.importer.import_one(listed, listing_at=T0)
    assert stopped.value.phrase == STOPPED_CONSENT
    assert scene.requested(rec) == []


def test_the_consent_guard_trusts_only_a_file_a_tick_old(tmp_path):
    # Phase 1's phase-end list, Task 13 (S11): a list written in place in two flushes is never
    # read in the pause. A file younger than the settle time is waited for, then read.
    path = tmp_path / "consent-list.md"
    path.write_text("- `of_" + "a" * 32 + "` — synthetic\n", encoding="utf-8")
    written = max(path.stat().st_mtime_ns, path.stat().st_ctime_ns)
    clock = [written + 500_000_000]  # half a second after the write
    waits = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        clock[0] += int(seconds * 1e9)

    guard = ConsentGuard(path, settle=2.0, sleep=sleep, clock_ns=lambda: clock[0])
    assert guard.current().ids == frozenset({"a" * 32})
    assert waits == [pytest.approx(1.5)]


def test_a_consent_file_that_keeps_changing_stops_imports(tmp_path):
    path = tmp_path / "consent-list.md"
    path.write_text("- `of_" + "a" * 32 + "` — synthetic\n", encoding="utf-8")
    guard = ConsentGuard(path, settle=2.0, tries=3, sleep=lambda s: None, clock_ns=time.time_ns)
    with pytest.raises(ImportStopped) as stopped:
        guard.current()  # always younger than 2 seconds by this clock: never trusted
    assert stopped.value.phrase == STOPPED_CONSENT


def test_the_disk_guard_stops_imports_before_the_first_byte(scene):
    # §11.5 step 5: twice the audio's Content-Length must leave the stop margin.
    rec = scene.account.add()
    scene.free = 0.05 + 2 * len(rec.audio) / 10**12 / 2  # room for one copy, not two
    with pytest.raises(ImportStopped) as stopped:
        scene.import_(rec)
    assert stopped.value.phrase == STOPPED_DISK
    assert list(scene.writer.archive.iter_recordings()) == []
    assert not (scene.root / DOWNLOADS).exists()
    assert scene.db.listing(rec.plaud_id) is None  # the disk's failure, not the recording's


def test_a_disk_below_the_margin_stops_before_the_download_starts(scene):
    rec = scene.account.add()
    scene.free = 0.01
    with pytest.raises(ImportStopped) as stopped:
        scene.import_(rec)
    assert stopped.value.phrase == STOPPED_DISK
    assert not any(p.startswith("/audio/") for p in scene.link_requests(rec))


def test_an_audio_link_that_expires_once_is_swapped_for_a_fresh_envelope(scene):
    # §10.2: the envelope is fetched again (with its checks) for a fresh link, once.
    rec = scene.account.add()
    from fake_plaud import expired_link

    scene.server.fault(f"/audio/{rec.plaud_id}.wav", expired_link(), host="storage", times=1)
    result = scene.import_(rec)
    assert result.outcome == "imported"
    assert len(scene.envelope_requests(rec)) == 2


def test_a_fresh_envelope_that_takes_the_keep_out_word_stops_the_download(scene):
    rec = scene.account.add("Synthetic retro")
    from fake_plaud import expired_link

    def rename(path, query):
        rec.envelope_name = "noarchive synthetic retro"  # renamed while the link expired
        return True

    scene.server.fault(
        lambda p, q: p == f"/audio/{rec.plaud_id}.wav" and rename(p, q),
        expired_link(),
        host="storage",
        times=1,
    )
    result = scene.import_(rec)
    assert (result.outcome, result.why) == ("excluded", BY_TITLE)
    assert list(scene.writer.archive.iter_recordings()) == []


def test_a_recording_that_fails_is_tried_once_more_then_counted(scene):
    # §10.2: one recording's failure is retried once, then counted; after 3 it is missing.
    from fake_plaud import html_page

    rec = scene.account.add()
    scene.server.fault(lambda p, q: p.endswith(rec.plaud_id), html_page())
    for n in (1, 2, 3):
        result = scene.import_(rec)
        assert (result.outcome, result.why, result.attempt) == ("failed", "PlaudBadResponse", True)
        row = scene.db.listing(rec.plaud_id)
        assert row.import_failures == n and row.next_import_at > T0
    assert len(scene.envelope_requests(rec)) == 6  # twice per run
    assert list(scene.writer.archive.iter_recordings()) == []


def test_an_outage_ends_the_run_as_itself(scene):
    from fake_plaud import server_error

    rec = scene.account.add()
    scene.server.fault(lambda p, q: p.endswith(rec.plaud_id), server_error())
    with pytest.raises(PlaudUnavailable):
        scene.import_(rec)
    assert scene.db.listing(rec.plaud_id) is None  # the run's failure, not the recording's


def test_an_envelope_that_names_another_recording_is_refused(scene):
    rec, other = scene.account.add(), scene.account.add()
    from fake_plaud import json_response

    wrong = other.envelope("of_", scene.server)
    scene.server.fault(lambda p, q: p.endswith(rec.plaud_id), json_response(wrong))
    result = scene.import_(rec)
    assert (result.outcome, result.why) == ("failed", "PlaudBadResponse")
    assert list(scene.writer.archive.iter_recordings()) == []


def test_the_same_audio_merges_into_the_recording_here(scene):
    # main spec §6.4: identical bytes join the existing recording as a new source reference.
    first = scene.account.add("Synthetic one")
    second = scene.account.add("Synthetic one again")
    second.seed = first.seed  # the same audio
    rid = scene.import_(first).recording_id
    result = scene.import_(second)
    assert (result.outcome, result.recording_id) == ("merged", rid)
    refs = {s.ref for s in scene.writer.archive.load(rid).sources if s.kind == "plaud"}
    assert refs == {f"of_{first.plaud_id}", f"of_{second.plaud_id}"}


def test_the_same_audio_as_a_consent_listed_recording_is_discarded(scene):
    # §11.5's last rule: the consent list excludes the recording, not only its ID. The download
    # is discarded, nothing in the archive changes, and the new ID is kept out and flagged.
    first = scene.account.add("Synthetic one")
    rid = scene.import_(first).recording_id
    scene.consent_ids(NEVER_LISTED, first.plaud_id)
    before = (scene.writer.archive.path_for(rid) / "recording.json").read_bytes()
    second = scene.account.add("Synthetic one again")
    second.seed = first.seed
    result = scene.import_(second)
    assert (result.outcome, result.why) == ("excluded", EXCLUDED_FLAGGED)
    assert (scene.writer.archive.path_for(rid) / "recording.json").read_bytes() == before
    assert scene.db.listing(second.plaud_id).rekey_of == first.plaud_id
    (flag,) = scene.db.open_flags(("rekeyed",))
    assert (flag.canonical_plaud_id, flag.detail) == (
        second.plaud_id,
        {"of": "consent", "by": "audio"},
    )
    assert not (scene.root / DOWNLOADS).exists()
    # The next try finds it re-keyed, and fetches nothing.
    again = scene.import_(second)
    assert (again.outcome, again.why) == ("excluded", BY_REKEY)


def test_nothing_imports_while_the_gate_is_closed_but_the_catch_up(scene):
    rec = scene.account.add()
    scene.db.delete(K_CATCH_UP_DONE)
    with pytest.raises(ImportStopped) as stopped:
        scene.import_(rec)
    assert stopped.value.phrase == GATE_REFUSAL and scene.requested(rec) == []
    scene.importer.catch_up = True
    assert scene.import_(rec).outcome == "imported"


def test_nothing_imports_under_a_hold(scene):
    rec = scene.account.add()
    scene.db.put(K_HOLD, {"since": T0.isoformat()}, now=T0)
    with pytest.raises(ImportStopped) as stopped:
        scene.import_(rec)
    assert stopped.value.phrase == STOPPED_HELD and scene.requested(rec) == []


def test_a_listing_older_than_one_interval_is_listed_again(scene):
    rec = scene.account.add()
    listed = scene.row(rec)
    with pytest.raises(ListingStale):
        scene.importer.import_one(listed, listing_at=T0 - timedelta(minutes=21))
    assert scene.requested(rec) == []


def test_an_id_already_here_is_skipped(scene):
    rec = scene.account.add()
    rid = scene.import_(rec).recording_id
    result = scene.import_(rec)
    assert (result.outcome, result.recording_id, result.attempt) == ("here", rid, False)
    assert len(scene.envelope_requests(rec)) == 1


def test_kept_out_stays_out_until_released(scene):
    # C5.05: a title that loses the word stays kept out; Release lets it in.
    rec = scene.account.add("noarchive synthetic")
    assert scene.import_(rec).why == BY_TITLE
    scene.account.rename(rec.plaud_id, "Synthetic, renamed")
    assert (scene.import_(rec).why, scene.requested(rec)) == (BY_WAS_TITLE, [])
    scene.db.set_listing(rec.plaud_id, released_at=T0 + timedelta(seconds=1))
    assert scene.import_(rec).outcome == "imported"


def test_a_released_id_whose_title_takes_the_word_again_is_kept_out_again(scene):
    rec = scene.account.add("noarchive synthetic")
    scene.import_(rec)
    scene.db.set_listing(rec.plaud_id, released_at=T0 + timedelta(seconds=1))
    scene.now = T0 + timedelta(minutes=1)
    assert scene.import_(rec).why == BY_TITLE  # the word is back: kept out again
    scene.account.rename(rec.plaud_id, "Synthetic, renamed again")
    assert scene.import_(rec).why == BY_WAS_TITLE


def test_a_review_row_waits_for_dan_unless_he_named_it(scene):
    rec = scene.account.add()
    scene.db.set_listing(f"of_{rec.plaud_id}", needs_review=True, first_seen=T0)
    result = scene.import_(rec)
    assert (result.outcome, result.why) == ("excluded", EXCLUDED_REVIEW)
    assert scene.requested(rec) == []
    assert scene.import_(rec, review_ok=True).outcome == "imported"


def test_the_worker_sweeps_its_leftover_downloads_unless_a_run_holds_plaud(scene):
    folder = scene.root / DOWNLOADS
    folder.mkdir(parents=True)
    (folder / f"{id_hash('a' * 32)}-left.mp3.part").write_bytes(b"half a download")
    with Locks(scene.writer.state.locks_dir, timeout=1).hold(PLAUD_LOCK):
        assert sweep_downloads(scene.root, scene.writer.state) is None  # its holder owns them
    assert folder.exists()
    assert sweep_downloads(scene.root, scene.writer.state) == 1
    assert not folder.exists() and not (scene.root / ".tmp").exists()


def test_no_title_reaches_a_log_or_a_result(scene, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    kept = scene.account.add("noarchive synthetic secret")
    private = scene.account.add("Private: synthetic secret")
    results = [scene.import_(kept), scene.import_(private)]
    assert "secret" not in caplog.text and "secret" not in repr(results)
    # A Plaud ID is logged only as its hash (§10.1); results carry IDs, for the run's records.
    for rec in (kept, private):
        assert rec.plaud_id not in caplog.text
