"""A check of one recording here (stage 2b spec §14.1, §14.4's double fetch; area B1), against the
fake Plaud server's account, through the import: what a check fetches, compares, writes, reconciles
and records, and what it never fetches (C5.03's checks half). Every ID and title is synthetic."""

import json
from datetime import timedelta

import pytest
from fake_plaud import expired_link, json_response
from plaud_scene import T0, ZONE
from recordings import selfdoc
from recordings.archive import RawSource
from recordings.plaud import editsync
from recordings.plaud.checks import (
    FAILING_AFTER,
    K_BREAKER,
    K_SNAPSHOT_MARK,
    KEPT_OUT_LATE,
)
from recordings.plaud.editsync import Checker, other_fields
from recordings.plaud.normalise import link_errors


def checker(scene, *, breaker_open: bool = False) -> Checker:
    return Checker(
        writer=scene.writer,
        db=scene.db,
        client=scene.client,
        consent=scene.importer.consent,
        timezone_name=ZONE,
        run_id="run-0002",
        schedule_minutes=20,
        full_sweep_days=1,
        breaker_open=breaker_open,
        clock=lambda: scene.now,
    )


def listed(scene) -> dict:
    return {row.canonical_id: row for row in scene.listing().rows}


def arrive(scene, name: str | None = None):
    rec = scene.account.add(name)
    result = scene.import_(rec)
    assert result.outcome == "imported"
    return rec, result.recording_id


def check(scene, rid: str, **kw):
    scene.now += timedelta(minutes=21)
    return checker(scene, **kw).check(rid, listed(scene))


def snapshots(scene, rid: str) -> list[str]:
    return [s.raw for s in scene.writer.archive.load(rid).sources if s.kind == "plaud"]


def envelope_fetches(scene, rec) -> int:
    return len(scene.envelope_requests(rec))


def settled(scene, rid: str):
    """Its first check after the import spends the import's baseline (§14.5): settled since."""
    first = check(scene, rid)
    assert first.result == editsync.UP_TO_DATE and not first.counted
    row = scene.db.check_row(rid)
    assert row.settled and not row.baseline_pending
    return row


def test_an_unchanged_check_writes_nothing_and_doubles_the_interval(scene):
    rec, rid = arrive(scene)
    rev = scene.writer.archive.load(rid).rev
    before = envelope_fetches(scene, rec)
    result = check(scene, rid)
    assert result.result == editsync.UP_TO_DATE and result.attempt and result.success
    assert envelope_fetches(scene, rec) == before + 1  # no double fetch without a change
    assert scene.writer.archive.load(rid).rev == rev  # an unchanged check never writes it
    assert len(snapshots(scene, rid)) == 1
    row = scene.db.check_row(rid)
    assert row.last_checked_at == scene.now and row.failures == 0
    assert row.interval_seconds == 40 * 60  # doubled from the import's 20 minutes
    assert row.next_check_at == scene.now + timedelta(minutes=40)
    (logged,) = scene.db.check_log_since(T0)
    assert (logged.result, logged.counted, logged.parts) == ("up to date", False, ())


def test_a_speaker_rename_is_fetched_twice_written_once_and_reconciled(scene):
    rec, rid = arrive(scene)
    settled(scene, rid)
    marks = scene.db.get(K_SNAPSHOT_MARK)["count"]
    scene.account.rename_speaker(rec.plaud_id, "Ada")
    before = envelope_fetches(scene, rec)
    result = check(scene, rid)
    assert result.result == editsync.UPDATED and result.parts == ("speakers",)
    assert result.volatile == () and not result.counted  # a stable edit to a named part
    assert envelope_fetches(scene, rec) == before + 2  # §14.4: a settled change is fetched twice
    assert result.phrase() == "updated: speakers"
    raws = snapshots(scene, rid)
    assert len(raws) == 2 and result.snapshot == raws[-1]
    folder = scene.writer.archive.path_for(rid)
    snap = json.loads((folder / raws[-1]).read_text())
    assert snap["_meta"]["tool"] == "recordings" and link_errors(snap) == []
    assert scene.db.get(K_SNAPSHOT_MARK)["count"] == marks + 1  # the mark before it (§11.6)
    # Reconciled: a second transcript output, carrying the new name.
    transcripts = [
        r for _, r in scene.writer.archive.renditions(rid, problems=[]) if r.kind == "transcript"
    ]
    assert len(transcripts) == 2
    assert any(
        seg.get("speaker_name") == "Ada" for t in transcripts for seg in t.payload["segments"]
    )
    (event,) = scene.db.events_since(T0, ["updated"])
    assert event.recording_id == rid and event.detail["parts"] == ["speakers"]
    row = scene.db.check_row(rid)
    assert row.last_changed_at == scene.now and row.fast_checks_left == 2
    assert row.next_check_at == scene.now + timedelta(minutes=20)
    assert selfdoc.validate(scene.root, deep=True) == []
    # And checked again, nothing changed since: up to date, nothing written.
    assert check(scene, rid).result == editsync.UP_TO_DATE
    assert len(snapshots(scene, rid)) == 2


def test_regenerated_notes_are_notes_and_a_title_change_is_the_title(scene):
    rec, rid = arrive(scene)
    settled(scene, rid)
    scene.account.regenerate_notes(rec.plaud_id, "# Summary\n\nA regenerated synthetic summary.")
    scene.account.rename(rec.plaud_id, "Synthetic meeting, renamed")
    result = check(scene, rid)
    assert result.parts == ("title", "notes") and not result.counted


def test_an_unsettled_recording_is_fetched_once_and_checked_often(scene):
    rec = scene.account.add(start_at=T0 - timedelta(hours=3))  # recorded 3 hours ago
    rid = scene.import_(rec).recording_id
    assert not scene.db.check_row(rid).settled
    scene.account.rename_speaker(rec.plaud_id, "Grace")
    before = envelope_fetches(scene, rec)
    result = check(scene, rid)
    assert result.result == editsync.UPDATED and envelope_fetches(scene, rec) == before + 1
    # Its transcript is complete with a Plaud note: settled now (§14.2).
    assert scene.db.check_row(rid).settled


@pytest.mark.parametrize(
    "why", ["consent", "kept-out", "kept-out-id", "kept-here", "gone", "parked", "absent"]
)
def test_some_recordings_are_never_fetched_by_a_check(scene, why):
    """C5.03's checks half: a recording kept out or consent-listed after it came in, gone from
    Plaud, parked, or absent from the newest listing, is never requested, and no check is an
    attempt."""
    rec, rid = arrive(scene)
    rows = listed(scene)
    if why == "consent":
        scene.consent_ids(rec.plaud_id)
    elif why == "kept-out":
        scene.db.set_listing(rec.plaud_id, kept_out_at=scene.now, kept_out_word="noarchive")
        scene.db.add_event(KEPT_OUT_LATE, now=scene.now, recording_id=rid, open_flag=True)
    elif why == "kept-out-id":  # its Plaud ID kept out, whatever became of the flag
        scene.db.set_listing(rec.plaud_id, kept_out_at=scene.now, kept_out_word="noarchive")
    elif why == "kept-here":
        scene.db.set_check(rid, kept_here_at=scene.now)
    elif why == "gone":
        scene.db.mark_gone([rid], now=scene.now)
    elif why == "parked":
        scene.db.set_check(rid, parked_at=scene.now)
    else:
        scene.account.remove(rec.plaud_id)
        rows = listed(scene)
    before = len(scene.requested(rec))
    scene.now += timedelta(minutes=21)
    result = checker(scene).check(rid, rows)
    assert not result.attempt and len(scene.requested(rec)) == before
    expected = {
        "consent": editsync.NOT_CONSENT,
        "kept-out": editsync.NOT_KEPT_OUT,
        "kept-out-id": editsync.NOT_KEPT_OUT,
        "kept-here": editsync.NOT_KEPT_OUT,
        "gone": editsync.GONE,
        "parked": editsync.PARKED,
        "absent": editsync.NOT_LISTED,
    }
    assert result.result == expected[why]
    assert scene.db.check_log_since(T0) == []


def test_a_parked_recording_is_checked_through_resync(scene):
    _rec, rid = arrive(scene)
    scene.db.set_check(rid, parked_at=scene.now)
    scene.now += timedelta(minutes=21)
    result = checker(scene).check(rid, listed(scene), parked_ok=True)
    assert result.result == editsync.UP_TO_DATE


def test_a_keep_out_word_in_the_envelope_alone_fetches_no_block_and_writes_nothing(scene):
    rec, rid = arrive(scene)
    rec.envelope_name = "noarchive synthetic aside"  # renamed between the listing and the fetch
    links = len(scene.link_requests(rec))
    result = check(scene, rid)
    assert result.result == editsync.NOT_KEPT_OUT and not result.attempt
    assert len(scene.link_requests(rec)) == links  # no block, no audio
    assert len(snapshots(scene, rid)) == 1
    loaded = scene.writer.archive.load(rid)
    assert "private" in [t.tag for t in loaded.tags]  # §13.1 case 3: private, and one flag
    assert scene.db.open_flags((KEPT_OUT_LATE,), recording_id=rid)
    # And never fetched again.
    before = len(scene.requested(rec))
    assert check(scene, rid).result == editsync.NOT_KEPT_OUT
    assert len(scene.requested(rec)) == before


def test_a_private_title_first_seen_in_a_check_is_tagged_before_its_snapshot(scene):
    rec, rid = arrive(scene)
    settled(scene, rid)
    rec.envelope_name = "Private synthetic chat"
    published = []
    real = scene.writer.add_snapshot

    def spy(recording_id, source, **kw):
        published.append([t.tag for t in scene.writer.archive.load(recording_id).tags])
        return real(recording_id, source, **kw)

    scene.writer.add_snapshot = spy
    result = check(scene, rid)
    assert result.result == editsync.UPDATED and "title" in result.parts
    assert published and "private" in published[0]  # tagged before the snapshot landed
    (note,) = scene.db.events_since(T0, ["retagged-private"])
    assert note.detail["why"] == "title changed"


def test_link_errors_twice_make_a_degraded_check_whose_snapshot_is_kept_once(scene, plaud_server):
    rec, rid = arrive(scene)
    renditions = len(list(scene.writer.archive.renditions(rid, problems=[])))
    plaud_server.fault(lambda path, _q: path.startswith("/notes/"), expired_link(), host="storage")
    result = check(scene, rid)
    assert result.result == editsync.DEGRADED and result.attempt and not result.success
    assert len(scene.envelope_requests(rec)) >= 3  # the import's, then the check's and its refetch
    raws = snapshots(scene, rid)
    assert len(raws) == 2
    degraded = json.loads((scene.writer.archive.path_for(rid) / raws[-1]).read_text())
    assert link_errors(degraded)  # kept, and never a baseline
    assert len(list(scene.writer.archive.renditions(rid, problems=[]))) == renditions
    assert scene.db.check_row(rid).degraded
    # The same degraded answer again writes no second copy.
    assert check(scene, rid).result == editsync.DEGRADED
    assert len(snapshots(scene, rid)) == 2


def test_a_failing_check_is_retried_once_counted_and_reaches_the_failing_list(scene, plaud_server):
    rec, rid = arrive(scene)
    plaud_server.fault(
        lambda path, _q: rec.plaud_id in path and path.startswith("/open/third-party/files/"),
        json_response({"detail": "synthetic"}, 404),
    )
    for n in range(1, FAILING_AFTER + 1):
        before = envelope_fetches(scene, rec)
        result = check(scene, rid)
        assert result.result == editsync.FAILED and result.failure == "PlaudNotFound"
        assert result.attempt and not result.success
        assert envelope_fetches(scene, rec) == before + 2  # tried once more, then counted
        row = scene.db.check_row(rid)
        assert row.failures == n
        assert row.next_check_at == scene.now + timedelta(minutes=20 * 2 ** (n - 1))
    assert row.failing_since is not None
    (failing,) = scene.db.failing_checks()  # Status's "fails every check" line (P1 wrap)
    assert failing.recording_id == rid
    plaud_server.faults.clear()
    assert check(scene, rid).result == editsync.UP_TO_DATE
    assert scene.db.failing_checks() == [] and scene.db.check_row(rid).failing_since is None


def test_a_crash_between_publish_and_mutate_leaves_an_orphan_validate_warns_about(scene):
    rec, rid = arrive(scene)
    real = scene.writer.mutate

    def crash(recording_id, op):
        raise OSError("synthetic crash after the publish")

    scene.writer.mutate = crash
    payload = json.dumps({"id": rec.plaud_id, "name": "synthetic"}).encode()
    when = scene.now + timedelta(minutes=5)
    with pytest.raises(OSError):
        scene.writer.add_snapshot(
            rid, RawSource("plaud", rec.plaud_id, when, payload, fetched_at=when)
        )
    scene.writer.mutate = real
    assert len(snapshots(scene, rid)) == 1  # recording.json doesn't name it
    assert selfdoc.validate(scene.root, deep=True) == []  # a warning, never a problem
    (warning,) = selfdoc.orphan_snapshots(scene.root)
    assert warning["message"] == selfdoc.ORPHAN_SNAPSHOT
    assert "synthetic" not in warning["message"]


def test_a_reconcile_that_failed_is_repaired_by_the_next_check_with_no_change(scene):
    rec, rid = arrive(scene)
    settled(scene, rid)
    scene.account.rename_speaker(rec.plaud_id, "Lin")
    real = scene.writer.reconcile_plaud
    scene.writer.reconcile_plaud = lambda recording_id: (_ for _ in ()).throw(OSError("crash"))
    assert check(scene, rid).result == editsync.UPDATED
    scene.writer.reconcile_plaud = real
    count = len(list(scene.writer.archive.renditions(rid, problems=[])))
    assert check(scene, rid).result == editsync.UP_TO_DATE  # unchanged since: nothing fetched
    assert len(list(scene.writer.archive.renditions(rid, problems=[]))) == count + 1


def test_a_new_field_counts_and_names_the_field_never_its_value(scene):
    _rec, rid = arrive(scene)
    settled(scene, rid)
    scene.account.send_field("synthetic_flag", "a-synthetic-value-never-shown")
    result = check(scene, rid)
    assert result.result == editsync.UPDATED and result.parts == ("other",)
    assert result.counted and result.volatile == () and result.fields == ("synthetic_flag",)
    (logged,) = [r for r in scene.db.check_log_since(T0) if r.result == "updated"]
    assert logged.counted and logged.fields == ("synthetic_flag",)
    assert "a-synthetic-value-never-shown" not in json.dumps(logged.parts)


def test_a_part_that_differs_between_two_fetches_is_volatile_and_counts(scene):
    _rec, rid = arrive(scene)
    settled(scene, rid)
    scene.account.make_volatile("transcript")
    result = check(scene, rid)
    assert result.result == editsync.UPDATED and result.volatile == ("transcript",)
    assert result.counted


def test_the_first_check_after_an_import_never_counts(scene):
    _rec, rid = arrive(scene)
    scene.account.send_field("synthetic_flag", 1)
    result = check(scene, rid)
    assert result.result == editsync.UPDATED and not result.counted
    assert not scene.db.check_row(rid).baseline_pending


def test_while_the_breaker_is_open_a_change_only_in_what_counted_is_suppressed(scene):
    rec, rid = arrive(scene)
    row = settled(scene, rid)
    scene.db.put(K_BREAKER, {"open": True}, now=scene.now)
    scene.account.send_field("synthetic_flag", 1)
    result = check(scene, rid, breaker_open=True)
    assert result.result == editsync.SUPPRESSED and result.success and not result.counted
    assert len(snapshots(scene, rid)) == 1  # no snapshot
    after = scene.db.check_row(rid)
    assert after.last_changed_at is None  # treated as unchanged: the interval doesn't reset
    assert after.interval_seconds == min(row.interval_seconds * 2, 24 * 3600)
    # A change with another part in it is written as usual.
    scene.account.rename_speaker(rec.plaud_id, "Ada")
    result = check(scene, rid, breaker_open=True)
    assert result.result == editsync.UPDATED and set(result.parts) == {"speakers", "other"}


def test_other_fields_are_names_only():
    old = {"id": "a" * 32, "name": "synthetic", "source_list": [], "note_list": []}
    new = {
        **old,
        "folder_id": "a-synthetic-value",
        "https://example.invalid/?X-Amz-Signature=synthetic": 1,
    }
    assert other_fields(old, new) == ("?", "folder_id")
    assert other_fields(new, new) == ()
