import hashlib
import json
from datetime import datetime, timezone

import pytest

from recordings import media
from recordings.archive import RawSource
from recordings.sources.audio_router import (
    DISPOSITIONS,
    CatalogError,
    map_tags,
    plan,
    read_catalog,
)

A = "a" * 32
B = "b" * 32
C = "c" * 32
D = "d" * 32
O = "8" * 32  # an orphan: audio, but no snapshot and no time
T = "9" * 32  # tagged private in audio-router's catalog
X = "7" * 32  # on the consent list
X2 = "6" * 32  # on the consent list, and only in audio-router's ledger
UUID = "11111111-1111-4111-8111-111111111111"
T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
NONE = ()  # an empty consent list, for the tests about everything else
JA = "会議メモ / Q&A"  # Review Focus 4: a title outside ASCII


def full(ar, plaud_env):
    """Every kind of row audio-router's catalog holds (spec §9.3's mapping table)."""
    ar.plaud(A, envelopes=[plaud_env(A, name="A PRIVATE TITLE"),
                           plaud_env(A, name="Interview: Grace")], tags=("lecture",))
    ar.plaud(B, envelopes=[plaud_env(B)], same_audio_as=f"plaud/audio/2026/08/{A}.wav",
             dup_of=f"plaud/{A}")
    ar.plaud(C, envelopes=[plaud_env(C, name=JA)], recorded_at="2026-08-03T11:00:00-07:00")
    ar.plaud(D, envelopes=[plaud_env(D)], audio=False)  # no audio: arrives through the sync
    ar.plaud(T, envelopes=[plaud_env(T)], recorded_at="2026-08-04T11:00:00-07:00",
             tags=("private",))
    ar.plaud(X, envelopes=[plaud_env(X)], recorded_at="2026-08-05T11:00:00-07:00")
    ar.orphan(O)
    ar.recorder()
    ar.pocket(UUID, title="A PRIVATE TITLE")
    ar.pocket_without_audio()
    ar.private()
    ar.ledger_only = ["f" * 32, X2]
    ar.held = 2
    ar.write_catalog()


def test_the_dry_run_accounts_for_every_catalog_row(ar_archive, plaud_env):
    full(ar_archive, plaud_env)
    the_plan = plan(ar_archive.root, consent={X, X2})
    assert sorted(i.row.uri for i in the_plan.items) == sorted(r["uri"] for r in ar_archive.rows)
    assert the_plan.counts() == {"new": 4, "new-private": 2, "duplicate": 1, "present": 0,
                                 "no-audio": 2, "deferred-to-2b": 1, "consent-excluded": 1,
                                 "unplaced": 0}
    assert sum(the_plan.counts().values()) == len(ar_archive.rows)
    assert set(the_plan.counts()) == set(DISPOSITIONS)
    assert the_plan.ok and the_plan.problems == ()  # deferred rows never block a real run
    report = the_plan.report()
    assert report["catalog_rows"] == 11 and report["private"] == 2
    assert report["deferred_to_2b"] == [O] and report["unprobed"] == []
    assert report["ledger_only"] == ["f" * 32] and report["held_on_plaud"] == 2


def test_a_consent_listed_recording_is_never_read_and_only_counted(ar_archive, plaud_env):
    # why: §9.1, §9.3. Dan's consent list is a hard rule: the recording isn't imported, and no
    # report names its ID, either form of which may be on the list.
    full(ar_archive, plaud_env)
    the_plan = plan(ar_archive.root, consent={X, "of_" + X2})
    (excluded,) = [i for i in the_plan.items if i.disposition == "consent-excluded"]
    assert excluded.row.file_id == X and excluded.audio is None and excluded.sha256 == ""
    text = json.dumps(the_plan.report(), ensure_ascii=False)
    assert X not in text and X2 not in text


def test_the_dry_run_never_names_a_title_and_never_changes_the_source(ar_archive, plaud_env):
    # Review Focus 4, and §9.3: it reads audio-router's archive and never changes it.
    full(ar_archive, plaud_env)
    before = ar_archive.tree_hash()
    text = json.dumps(plan(ar_archive.root, consent={X, X2}).report(), ensure_ascii=False)
    for title in ("A PRIVATE TITLE", "Interview: Grace", "Getting Started", "getting_started",
                  "Pocket chat", "Jul 15", JA, "会議メモ", "Aug 3 at 10-00"):
        assert title not in text
    assert ar_archive.tree_hash() == before


def test_audio_routers_privacy_labels_make_a_recording_private(ar_archive, plaud_env):
    # why: §9.3 (Dan, 2026-10-09). The catalog's tags and access columns are audio-router's own
    # privacy labels: every tag comes over, and a privacy label moves under private/.
    ar_archive.plaud(A, envelopes=[plaud_env(A)], tags=("lecture",))
    ar_archive.plaud(B, envelopes=[plaud_env(B)], tags=("private",))  # the main tier, tagged
    ar_archive.plaud(C, envelopes=[plaud_env(C)])["access"] = "restricted"
    ar_archive.plaud(D, envelopes=[plaud_env(D)], tags=("lecture", "therapy"))
    ar_archive.write_catalog()
    by_id = {i.row.file_id: i for i in plan(ar_archive.root, consent=NONE).items}
    assert (by_id[A].disposition, by_id[A].tags, by_id[A].private) == ("new", ("lecture",), False)
    assert (by_id[B].disposition, by_id[B].tags) == ("new-private", ("private",))
    assert (by_id[C].disposition, by_id[C].tags) == ("new-private", ("private",))
    assert (by_id[D].disposition, by_id[D].tags) == ("new-private", ("lecture", "private/therapy"))
    assert map_tags(["medical", "journal", "students", "private", "lecture"]) == (
        "private/medical", "private/journal", "private/students", "private", "lecture")
    # One tag per meaning: therapy and counselling fold together, and the generic markers are
    # `private`.
    folded = ["counseling", "therapy", "counselling", "personal", "voiceprints", "private"]
    assert map_tags(folded) == ("private/therapy", "private")
    # Labels match in any capitalisation, as is_private_tag does.
    assert map_tags(["Therapy", "PRIVATE", "Lecture"]) == ("private/therapy", "private", "Lecture")


def test_the_private_tier_is_found_by_hash_whatever_its_files_are_called(ar_archive):
    # why: audio-router's catalog/private.jsonl registers a Recorder export by its audio's
    # SHA-256, and the file keeps its export name. There are no raw/ or derived/ folders.
    row = ar_archive.private()
    ar_archive.write_catalog()
    (item,) = plan(ar_archive.root, consent=NONE).items
    assert (item.disposition, item.kind, item.private) == ("new-private", "recorder", True)
    assert item.sha256 == row["file_id"] and item.audio.name == "Aug 3 at 10-00.wav"
    assert [p.name for p in item.texts] == ["Aug 3 at 10-00.txt"]
    assert item.snapshots == () and item.renditions == ()
    assert item.tags == ("private", "private/therapy")


def test_each_row_it_cannot_place_says_why(ar_archive, plaud_env):
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    (ar_archive.root / f"plaud/audio/2026/08/{A}.wav").unlink()
    ar_archive.plaud(B, envelopes=[plaud_env(B)])
    (ar_archive.root / f"plaud/audio/2026/08/{B}.wav").write_bytes(b"damaged in the copy")
    ar_archive.plaud(C, envelopes=[plaud_env(C)])["time_source"] = "a sundial"
    ar_archive.plaud(D, envelopes=[plaud_env(D)], dup_of="plaud/" + "0" * 32)
    ar_archive.plaud(T, envelopes=[plaud_env(T)])["tags"] = "Not A Slug"
    hidden = ar_archive.private()["file_id"]
    (ar_archive.root / "private/recorder-exports/Aug 3 at 10-00.wav").unlink()
    ar_archive.rows.append({**ar_archive.rows[0], "uri": "zoom/z1", "source": "zoom"})
    ar_archive.write_catalog()
    the_plan = plan(ar_archive.root, consent=NONE)
    reasons = {u["uri"]: u["reason"] for u in the_plan.report()["unplaced"]}
    assert "missing from the copy" in reasons[f"plaud/{A}"]
    assert "damaged" in reasons[f"plaud/{B}"]
    assert "a sundial" in reasons[f"plaud/{C}"]
    assert "dup_of" in reasons[f"plaud/{D}"]
    assert "lowercase slug" in reasons[f"plaud/{T}"] and "Not A Slug" not in reasons[f"plaud/{T}"]
    assert "0 media files" in reasons[f"private/{hidden}"]
    assert "unknown source" in reasons["zoom/z1"]
    assert not the_plan.ok


def test_a_short_copy_is_a_problem(ar_archive, plaud_env):
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.write_catalog(provenance_rows=5)
    the_plan = plan(ar_archive.root, consent=NONE)
    assert the_plan.problems and "incomplete" in the_plan.problems[0] and not the_plan.ok


def test_the_catalog_must_be_audio_routers(ar_archive):
    with pytest.raises(CatalogError, match="catalog.csv is missing"):
        plan(ar_archive.root, consent=NONE)
    (ar_archive.root / "catalog").mkdir(parents=True)
    (ar_archive.root / "catalog" / "catalog.csv").write_text("uri,source\nx,y\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="missing columns"):
        read_catalog(ar_archive.root)


def test_recordings_on_disk_that_the_catalog_misses_are_reported_by_id(ar_archive, plaud_env):
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.write_catalog()
    ar_archive.plaud(B, envelopes=[plaud_env(B)])  # on disk, after the catalog was built
    ar_archive.plaud(C, envelopes=[plaud_env(C)], audio=False)  # only in raw/
    (ar_archive.root / "recorder" / "derived" / ("9" * 64)).mkdir(parents=True)  # only in derived/
    ar_archive.plaud(X, envelopes=[plaud_env(X)])  # on the consent list: never named
    ar_archive.pocket_without_audio("my_secret_slug")
    (ar_archive.root / "plaud/audio/2026/08/My secret title.mp3").write_bytes(b"ID3")
    the_plan = plan(ar_archive.root, consent={X})

    def surrogate(name):
        return hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]

    assert set(the_plan.unlisted) == {
        f"plaud/{B}", f"plaud/{C}", f"recorder/{'9' * 64}",
        f"pocket/{surrogate('my_secret_slug')}", f"plaud/{surrogate('My secret title')}"}
    text = json.dumps(the_plan.report(), ensure_ascii=False)
    assert "my_secret_slug" not in text and "My secret title" not in text and X not in text


def test_snapshots_keep_audio_routers_order_within_one_second(ar_archive, plaud_env):
    # why: audio-router suffixes same-second snapshots -01, -02, and "-" sorts before "." .
    ar_archive.plaud(A, envelopes=[plaud_env(A), plaud_env(A, name="second"), plaud_env(A, name="third")],
                     stamps=["20260801T161000Z", "20260801T161000Z-01", "20260801T161000Z-02"])
    ar_archive.write_catalog()
    (item,) = plan(ar_archive.root, consent=NONE).items
    assert [p.name for p in item.snapshots] == [
        "20260801T161000Z.json", "20260801T161000Z-01.json", "20260801T161000Z-02.json"]


def test_media_that_cant_be_measured_is_reported(ar_archive, plaud_env, monkeypatch):
    # why: §9.1.1. Without ffprobe and ffmpeg, anything but WAV has no decoded duration.
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.plaud(B, envelopes=[plaud_env(B)], ext=".m4a",
                     recorded_at="2026-08-02T09:00:00-07:00")
    ar_archive.write_catalog()
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    the_plan = plan(ar_archive.root, consent=NONE)
    assert the_plan.report()["unprobed"] == [f"plaud/{B}"] and not the_plan.ok
    assert {i.row.file_id: i.probed.method for i in the_plan.items if i.probed} == {A: "wave"}


def test_a_row_already_in_the_archive_is_present_by_either_plaud_id_form(ar_archive, plaud_env,
                                                                         writer, make_incoming):
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.plaud(B, envelopes=[plaud_env(B)])
    ar_archive.write_catalog()
    audio_a = ar_archive.root / f"plaud/audio/2026/08/{A}.wav"
    writer.add(make_incoming(audio_a.read_bytes(), name="a.wav", sources=(
        RawSource(kind="plaud", ref="of_" + A, added_at=T0),)))
    writer.add(make_incoming(b"other audio", name="b.wav", sources=(
        RawSource(kind="plaud", ref=B, added_at=T0),)))
    by_uri = {i.row.uri: i for i in plan(ar_archive.root, writer.index, consent=NONE).items}
    assert by_uri[f"plaud/{A}"].disposition == "present"
    assert by_uri[f"plaud/{B}"].disposition == "unplaced"
    assert "different audio" in by_uri[f"plaud/{B}"].reason


def test_incomplete_snapshots_are_counted_by_reason_and_never_hidden(ar_archive, plaud_env):
    # why: Task 6's review. reconcile builds nothing from a snapshot with link errors, and a
    # legitimately empty block that kept its data_link looks the same: the dry run says how
    # many, why, and which recordings would get no Plaud outputs at all.
    ar_archive.plaud(A, envelopes=[plaud_env(A), plaud_env(A, link_error=True), "{not json"])
    ar_archive.plaud(B, envelopes=[plaud_env(B, link_error=True)],
                     recorded_at="2026-08-02T09:00:00-07:00")
    ar_archive.plaud(C, envelopes=[plaud_env(C)], recorded_at="2026-08-03T09:00:00-07:00")
    ar_archive.write_catalog()
    the_plan = plan(ar_archive.root, consent=NONE)
    report = the_plan.report()
    assert report["incomplete_snapshots"] == {
        "total": 3, "by_reason": {"auto_sum_note": 2, "unreadable": 1}}
    assert report["all_snapshots_incomplete"] == [f"plaud/{B}"]
    assert the_plan.ok  # reported, not refused: an empty block may be real


def _wav_of(ar, fid):
    return (ar.root / f"plaud/audio/2026/08/{fid}.wav").read_bytes()


def _consent_family(ar, plaud_env):
    """X, and every kind of row that carries X's recording: a copy that duplicates it, the
    original it duplicates, a Recorder copy, and a private-tier copy found by hash."""
    ar.plaud(X, envelopes=[plaud_env(X)], recorded_at="2026-08-05T11:00:00-07:00",
             dup_of=f"plaud/{A}")
    ar.plaud(A, envelopes=[plaud_env(A)], same_audio_as=f"plaud/audio/2026/08/{X}.wav")
    ar.plaud(B, envelopes=[plaud_env(B)], same_audio_as=f"plaud/audio/2026/08/{X}.wav",
             dup_of=f"plaud/{X}", recorded_at="2026-08-06T11:00:00-07:00")
    x_sha = next(r["audio_sha256"] for r in ar.rows if r["file_id"] == X)
    rec = ar.recorder()
    (ar.root / rec["audio_rel"]).write_bytes(_wav_of(ar, X))
    rec["audio_sha256"], rec["dup_of"] = x_sha, f"plaud/{X}"
    priv = ar.private()
    (ar.root / "private/recorder-exports/Aug 3 at 10-00.wav").write_bytes(_wav_of(ar, X))
    priv["file_id"], priv["uri"] = x_sha, f"private/{x_sha}"
    ar.plaud(C, envelopes=[plaud_env(C)], recorded_at="2026-08-07T11:00:00-07:00")  # unrelated
    ar.write_catalog()
    return rec["uri"], priv["uri"]


def test_a_consent_listed_recording_excludes_every_row_that_carries_it(ar_archive, plaud_env):
    # why: §9.1, §9.3 (Dan's ruling, 2026-10-14). The list excludes the recording, not only its
    # ID: a row that duplicates it, the row it duplicates, a copy with the same audio and a
    # private-tier copy found by hash would import the listed recording's audio and transcript.
    rec_uri, priv_uri = _consent_family(ar_archive, plaud_env)
    the_plan = plan(ar_archive.root, consent={X})
    by_uri = {i.row.uri: i for i in the_plan.items}
    for uri in (f"plaud/{X}", f"plaud/{A}", f"plaud/{B}", rec_uri, priv_uri):
        item = by_uri[uri]
        assert item.disposition == "consent-excluded", uri
        assert item.audio is None and item.sha256 == "" and item.snapshots == ()
    assert by_uri[f"plaud/{C}"].disposition == "new"
    assert the_plan.counts()["consent-excluded"] == 5 and the_plan.ok
    text = json.dumps(the_plan.report(), ensure_ascii=False)
    assert X not in text and priv_uri not in text


def test_without_the_id_on_the_consent_list_the_same_rows_import(ar_archive, plaud_env):
    # The control for the test above: the exclusion comes from the list, nothing else.
    rec_uri, priv_uri = _consent_family(ar_archive, plaud_env)
    by_uri = {i.row.uri: i for i in plan(ar_archive.root, consent=NONE).items}
    assert by_uri[f"plaud/{B}"].disposition == "duplicate"
    assert by_uri[rec_uri].disposition == "duplicate"
    assert by_uri[priv_uri].disposition == "new-private"
    assert by_uri[f"plaud/{A}"].disposition == "new"
    assert by_uri[f"plaud/{X}"].disposition == "duplicate"


def test_a_private_file_named_by_a_listed_id_is_never_read(ar_archive, monkeypatch):
    from recordings.sources import audio_router
    ar_archive.private()
    ar_archive._write(f"private/recorder-exports/of_{X}.wav", b"never to be read")
    ar_archive._write(f"private/recorder-exports/{X2}.wav", b"never to be read either")
    ar_archive.write_catalog()
    hashed = []
    real = audio_router.sha256_file
    monkeypatch.setattr(audio_router, "sha256_file", lambda p: (hashed.append(p.name), real(p))[1])
    the_plan = plan(ar_archive.root, consent={X, X2})
    assert hashed and not any(X in n or X2 in n for n in hashed)
    assert the_plan.report()["unlisted_private"] == 0


def test_one_unreadable_file_does_not_stop_the_dry_run(ar_archive, plaud_env):
    # why: §9.3. The dry run accounts for every row, and no reason carries a path (a private-tier
    # file name is an export title).
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.plaud(B, envelopes=[plaud_env(B)], recorded_at="2026-08-02T09:00:00-07:00")
    ar_archive.private()
    stray = ar_archive._write("private/recorder-exports/A Stray Title.wav", b"unreadable")
    locked = ar_archive.root / f"plaud/audio/2026/08/{A}.wav"
    ar_archive.write_catalog()
    stray.chmod(0)
    locked.chmod(0)
    try:
        the_plan = plan(ar_archive.root, consent=NONE)
    finally:
        stray.chmod(0o644)
        locked.chmod(0o644)
    by_uri = {i.row.uri: i for i in the_plan.items}
    assert by_uri[f"plaud/{A}"].disposition == "unplaced"
    assert "PermissionError" in by_uri[f"plaud/{A}"].reason
    assert by_uri[f"plaud/{B}"].disposition == "new"
    assert len(the_plan.problems) == 1 and "1 private-tier file" in the_plan.problems[0]
    text = json.dumps(the_plan.report(), ensure_ascii=False)
    assert "A Stray Title" not in text and str(ar_archive.root) not in text and not the_plan.ok


def test_a_malformed_wav_is_unprobed_not_fatal(ar_archive, plaud_env, monkeypatch):
    import struct
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    (ar_archive.root / f"plaud/audio/2026/08/{A}.wav").write_bytes(
        b"RIFF" + struct.pack("<I", 100) + b"WAVE" + b"LIST" + struct.pack("<I", 10**6) + b"abc")
    ar_archive.rows[0]["audio_sha256"] = ""
    ar_archive.write_catalog()
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    the_plan = plan(ar_archive.root, consent=NONE)
    assert the_plan.report()["unprobed"] == [f"plaud/{A}"] and not the_plan.ok


def test_every_path_the_plan_returns_is_inside_the_source(ar_archive, plaud_env, tmp_path):
    import shutil
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.write_catalog()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    shutil.move(str(ar_archive.root / "plaud" / "raw" / A), str(outside / A))
    (ar_archive.root / "plaud" / "raw" / A).symlink_to(outside / A, target_is_directory=True)
    (item,) = plan(ar_archive.root, consent=NONE).items
    assert item.disposition == "unplaced" and "outside the source" in item.reason


def test_private_media_with_no_catalog_row_is_counted_never_named(ar_archive):
    ar_archive.private()
    ar_archive._write("private/recorder-exports/Unlisted Title.wav", b"RIFFnot a row")
    ar_archive.write_catalog()
    report = plan(ar_archive.root, consent=NONE).report()
    assert report["unlisted_private"] == 1
    assert "Unlisted Title" not in json.dumps(report, ensure_ascii=False)


def test_the_pocket_listing_honours_the_consent_list(ar_archive):
    ar_archive.pocket_without_audio("my_secret_slug")
    ar_archive.rows.clear()
    ar_archive.write_catalog()
    surrogate = hashlib.sha256(b"my_secret_slug").hexdigest()[:32]
    assert plan(ar_archive.root, consent=NONE).unlisted == (f"pocket/{surrogate}",)
    assert plan(ar_archive.root, consent={surrogate}).unlisted == ()
