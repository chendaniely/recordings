import errno
import json
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from recordings import media
from recordings.archive import Archive
from recordings.cli import main
from recordings.disk import DiskStatus
from recordings.index import Index, IndexRefused
from recordings.locks import Locks, LockTimeout
from recordings.plaud.consent import ConsentListError, consent_list_path, load_consent_list
from recordings.selfdoc import validate
from recordings.sources import audio_router
from recordings.sources.ar_outputs import map_rendition, pocket_outputs
from recordings.sources.audio_router import ImportStopped, plan, run
from recordings.state import State
from recordings.writer import Writer

A = "a" * 32
B = "b" * 32
C = "c" * 32
J = "9" * 32  # a recording whose title is outside ASCII
X = "7" * 32  # on the consent list
UUID = "11111111-1111-4111-8111-111111111111"
NOW = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
JA = "会議メモ / Q&A"  # Review Focus 4: a title outside ASCII
CONSENT = f"# Never mirror\n- `{X}` — excluded\n"

WORDS = {"schema": "audio-router/rendition@1", "kind": "words", "engine": "mlx-whisper",
         "version": "large-v3-turbo@a4aaeec", "file_id": A, "inputs": {"audio_sha256": "x",
                                                                        "sample_rate": 16000},
         "meta": {"model": "mlx-community/whisper-large-v3-turbo", "language": "en"},
         "payload": {"words": [{"word": "Hello", "start": 0.0, "end": 0.5, "prob": 0.9},
                               {"word": "there.", "start": 0.5, "end": 1.4, "prob": 0.9},
                               {"word": "Hi.", "start": 1.6, "end": 2.4, "prob": 0.9}],
                     "segments": [{"id": 0, "start": 0.0, "end": 1.5, "text": " Hello there."},
                                  {"id": 1, "start": 1.5, "end": 2.6, "text": " Hi."}]}}
TURNS = {"kind": "turns", "engine": "pyannote", "version": "community-1@abc", "inputs": {},
         "payload": {"turns": [{"start": 0.0, "end": 1.5, "speaker": "SPEAKER_00"}],
                     "overlap_turns": []}}
MERGED = {"kind": "merged", "engine": "audio-router", "version": "m2+x+y", "inputs": {},
          "payload": {"turns": [{"speaker": "SPEAKER_00", "start": 0.0, "end": 1.5,
                                 "text": "Hello there.", "inferred": False,
                                 "words": [{"word": "Hello", "start": 0.0, "end": 0.5}]}],
                      "text": "[00:00] SPEAKER_00: Hello there."}}
SUMMARY = {"kind": "summary", "engine": "local/qwen3.6-35b-a3b", "version": "qwen+p3",
           "inputs": {}, "payload": {"title": "Greetings", "summary_md": "Two people meet.",
                                     "actions": [{"owner_raw": "Ada", "text": "Say goodbye"}]}}
HUMAN = {"kind": "speakers", "engine": "human", "version": "m2+x+y", "file_id": A,
         "inputs": {"audio_sha256": "x"}, "meta": {"labels": 1},
         "payload": {"names": {"SPEAKER_00": "Ada"}}}  # audio-router's relabel: names Dan typed
PLAUD_TURNS = {"kind": "turns", "engine": "plaud", "version": "v1", "inputs": {}, "payload": {}}
EMBEDDINGS = {"kind": "embeddings", "engine": "pyannote", "version": "x", "inputs": {},
              "payload": {}}
HUMAN_FILE = "speakers-human-m2+x+y-20260801T170600Z.json"


def build(ar, plaud_env):
    ar.plaud(A, envelopes=[plaud_env(A, name="Week 4"), plaud_env(A, name="Week 4, edited")],
             tags=("lecture",),
             derived={"words-mlx_whisper-large_v3_turbo@a4aaeec-20260801T170000Z.json": WORDS,
                      "turns-pyannote-community_1@abc-20260801T170100Z.json": TURNS,
                      "merged-audio_router-m2+x+y-20260801T170200Z.json": MERGED,
                      "summary-local_qwen3.6_35b_a3b-qwen+p3-20260801T170300Z.json": SUMMARY,
                      "turns-plaud-v1-20260801T170400Z.json": PLAUD_TURNS,
                      "embeddings-pyannote-x-20260801T170500Z.json": EMBEDDINGS,
                      HUMAN_FILE: HUMAN})
    ar.plaud(B, envelopes=[plaud_env(B)], same_audio_as=f"plaud/audio/2026/08/{A}.wav",
             dup_of=f"plaud/{A}")
    ar.plaud(C, envelopes=[plaud_env(C)], audio=False)
    ar.plaud(J, envelopes=[plaud_env(J, name=JA)], recorded_at="2026-08-04T09:00:00-07:00")
    ar.recorder()
    ar.pocket(UUID)
    ar.private()
    ar.write_catalog()


def _fid(ar, source):
    return next(r["file_id"] for r in ar.rows if r["source"] == source)


def _files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_the_import_maps_every_kind_of_recording(ar_archive, plaud_env, writer):
    build(ar_archive, plaud_env)
    before = ar_archive.tree_hash()
    report = run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    assert report.failed == []
    assert (report.created, report.merged) == (5, 1)  # B's audio is A's: one recording, two IDs
    assert report.measured == {"wave": 6}  # every duration measured, and how
    assert ar_archive.tree_hash() == before  # §9.3: it never changes the source
    assert validate(writer.root) == []
    recs = {s.ref: r for r in writer.archive.iter_recordings() for s in r.sources}

    a = recs[A]
    assert {(s.kind, s.ref) for s in a.sources} == {
        ("plaud", A), ("plaud", B), ("audio_router", f"plaud/{A}"), ("audio_router", f"plaud/{B}")}
    assert a.title == "Week 4, edited" and a.title_by == "plaud" and a.plaud.title_seen
    assert [(t.tag, t.by) for t in a.tags] == [("lecture", "you")]
    assert (a.media.duration_ms, a.media.sample_rate, a.media.channels) == (2600, 16000, 1)
    outputs = {(r.kind, r.engine, r.note_type) for _, r in writer.archive.renditions(a.id)}
    assert ("transcript", "mlx-whisper", None) in outputs
    assert ("transcript", "audio-router", None) in outputs
    assert ("speakers", "pyannote", None) in outputs
    assert ("notes", "local/qwen3.6-35b-a3b", "audio-router-summary") in outputs
    assert ("transcript", "plaud", None) in outputs and ("notes", "plaud", "plaud-summary") in outputs
    assert not any(r.kind == "speakers" and r.engine in ("plaud", "human")
                   for _, r in writer.archive.renditions(a.id))  # never outputs
    (typed,) = [s for s in a.sources if s.kind == "audio_router" and s.raw]
    assert typed.raw == "source/audio_router-20260801T170600Z.json"  # Dan's names, verbatim
    assert ((writer.archive.path_for(a.id) / typed.raw).read_bytes()
            == (ar_archive.root / "plaud" / "derived" / A / HUMAN_FILE).read_bytes())
    assert report.skipped_outputs == {
        "audio-router's Plaud outputs are rebuilt by reconcile": 1,
        "voice data never enters the archive (spec §7.6)": 1}

    assert (recs[J].title, recs[J].title_by) == (JA, "plaud")
    private = recs[_fid(ar_archive, "private")]
    assert [(t.tag, t.by) for t in private.tags] == [("private", "you"), ("private/therapy", "you")]
    assert (private.title, private.title_by) == ("Aug 3 at 10-00", "recorder")
    (txt,) = [s for s in private.sources if s.raw and s.raw.endswith(".txt")]
    assert txt.kind == "recorder" and txt.ref == private.media.sha256
    assert ((writer.archive.path_for(private.id) / txt.raw).read_bytes()
            == (ar_archive.root / "private/recorder-exports/Aug 3 at 10-00.txt").read_bytes())
    pocket = recs[UUID]
    assert (pocket.title, pocket.title_by) == ("Pocket chat", "pocket")
    assert pocket.time_source == "metadata"
    assert {r.engine for _, r in writer.archive.renditions(pocket.id)} == {"pocket"}
    recorder = recs[_fid(ar_archive, "recorder")]
    assert recorder.title_by == "recorder"
    assert writer.archive.renditions(recorder.id) == []  # its prose transcript stays in source/
    assert C not in recs  # no audio: not imported


def test_running_the_import_again_adds_nothing(ar_archive, plaud_env, writer):
    build(ar_archive, plaud_env)
    run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    files = _files(writer.root)
    again_plan = plan(ar_archive.root, writer.index, consent=())
    assert again_plan.counts()["present"] == 6
    again = run(again_plan, writer, now=datetime(2026, 10, 10, tzinfo=timezone.utc))
    assert (again.created, again.sources_added, again.renditions_added, again.plaud_outputs) == (0, 0, 0, 0)
    assert _files(writer.root) == files


KILL = r"""
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from recordings.index import Index
from recordings.sources.audio_router import plan, run
from recordings.state import State
from recordings.writer import MergeIncoming, Writer

root, state, index, source, point, now = sys.argv[1:7]
calls = []


def die():
    os._exit(137)  # as SIGKILL would: no except, no finally, no cleanup


def nth(n):
    calls.append(1)
    return len(calls) == n


if point == "copy":  # inside copy_file_synced, half-way through the second recording's media
    real_copy = shutil.copyfileobj

    def copy(fin, fout, length=0):
        if not nth(2):
            return real_copy(fin, fout, length)
        data = fin.read()
        fout.write(data[: len(data) // 2])
        fout.flush()
        die()

    shutil.copyfileobj = copy
elif point == "commit":  # State.commit, entered once os.replace has put a file in place
    real_commit = State.commit

    def commit(self, revision_id):
        if nth(2):
            die()
        return real_commit(self, revision_id)

    State.commit = commit
elif point == "merge":  # Writer.mutate for MergeIncoming: the merge's raw files are published
    real_mutate = Writer.mutate

    def mutate(self, recording_id, op):
        if isinstance(op, MergeIncoming):
            die()
        return real_mutate(self, recording_id, op)

    Writer.mutate = mutate
elif point == "reconcile":  # Writer.reconcile_plaud, once its first Plaud output is published
    real_write = Writer._write_rendition_locked

    def write(self, folder, rendition):
        written = real_write(self, folder, rendition)
        if rendition.engine == "plaud" and written is not None:
            die()
        return written

    Writer._write_rendition_locked = write

writer = Writer(Path(root), State.open(Path(state)), Index(Path(index)), writer_id="test")
run(plan(Path(source), writer.index, consent=()), writer, now=datetime.fromisoformat(now))
"""


@pytest.mark.parametrize("point", ["copy", "commit", "merge", "reconcile"])
def test_an_import_killed_anywhere_ends_as_a_clean_run_does(ar_archive, plaud_env, make_writer,
                                                             point):
    # Review Focus 2: killed inside the writer's own code, then run again with a fresh Writer and
    # the same `now`, the archive holds exactly the files a clean run gives, no source twice.
    build(ar_archive, plaud_env)
    clean = make_writer("clean")
    run(plan(ar_archive.root, clean.index, consent=()), clean, now=NOW)
    killed = make_writer("killed")
    child = subprocess.run(
        [sys.executable, "-c", KILL, str(killed.root), str(killed.state.path),
         str(killed.index.path), str(ar_archive.root), point, NOW.isoformat()],
        capture_output=True, timeout=300)
    assert child.returncode == 137, child.stderr.decode(errors="replace")  # it died there
    fresh = Writer(killed.root, State.open(killed.state.path), Index(killed.index.path),
                   writer_id="test")
    report = run(plan(ar_archive.root, fresh.index, consent=()), fresh, now=NOW)
    assert report.failed == []
    assert _files(fresh.root) == _files(clean.root)
    assert not (fresh.root / ".tmp").exists()
    for rec in fresh.archive.iter_recordings():
        keys = [(s.kind, s.ref, s.sha256) for s in rec.sources]
        assert len(keys) == len(set(keys)), rec.id
    assert validate(fresh.root) == []


def test_a_degraded_or_unreadable_snapshot_is_kept_but_never_used(ar_archive, plaud_env, writer):
    # Review Focus 3.
    ar_archive.plaud(A, envelopes=[plaud_env(A), plaud_env(A, link_error=True), "{not json"])
    ar_archive.write_catalog()
    report = run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    (rec,) = writer.archive.iter_recordings()
    assert sum(s.kind == "plaud" for s in rec.sources) == 3  # raw is raw: all three are kept
    assert report.skipped_snapshots == 2
    transcripts = [r for _, r in writer.archive.renditions(rec.id)
                   if r.kind == "transcript" and r.engine == "plaud"]
    assert len(transcripts) == 1 and transcripts[0].inputs["source"].endswith("20260801T161000Z.json")


def test_same_second_snapshots_keep_their_order_in_the_outputs(ar_archive, plaud_env, writer):
    # why: audio-router suffixes same-second snapshots -01, -02, so their stamps tie. With tied
    # fetch times the newest output falls to file names, where `x-2.json` sorts before
    # `x.json`, and the older transcript would be the one shown (§6.5).
    rows = json.loads(plaud_env(A)["source_list"][0]["data_content"])
    edited = plaud_env(A, segments=[{**rows[0], "content": "Edited."}, *rows[1:]])
    ar_archive.plaud(A, envelopes=[plaud_env(A), edited],
                     stamps=["20260801T161000Z", "20260801T161000Z-01"])
    ar_archive.write_catalog()
    run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    (rec,) = writer.archive.iter_recordings()
    transcripts = [r for _, r in writer.archive.renditions(rec.id)
                   if r.kind == "transcript" and r.engine == "plaud"]
    assert [t.payload["segments"][0]["text"] for t in transcripts] == ["Hello there.", "Edited."]


def test_the_import_stops_when_the_disk_guard_says_stop(ar_archive, plaud_env, writer):
    build(ar_archive, plaud_env)
    stop = DiskStatus(path="x", marker=True, free_bytes=1, total_bytes=100, level="stop",
                      message="1.0% free: below 5%, new imports stop")
    with pytest.raises(ImportStopped, match="new imports stop") as caught:
        run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW, disk=lambda: stop)
    assert caught.value.report.created == 0
    assert list(writer.archive.iter_recordings()) == []


def test_a_full_disk_stops_the_import_instead_of_filling_it_again_for_every_row(
        ar_archive, plaud_env, writer, monkeypatch):
    # why: ENOSPC is an OSError, which the run reported per row before going on, so every later
    # row copied its media until the disk filled again. state.db shares that disk.
    build(ar_archive, plaud_env)

    def full(src, dst):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("recordings.writer.copy_file_synced", full)
    with pytest.raises(ImportStopped, match="disk is full") as caught:
        run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    assert [f["reason"] for f in caught.value.report.failed] == [
        "OSError: a file could not be read or written"]
    assert list(writer.archive.iter_recordings()) == []
    assert not (writer.root / ".tmp").exists()


def test_an_archive_that_drops_off_stops_the_import_instead_of_failing_every_row(
        ar_archive, plaud_env, writer, tmp_path):
    # why: §6.7. The Writer re-checks archive.json before each write and raises NotTheWriter. That
    # is not a per-row failure: going on would only fail every later row the same way.
    build(ar_archive, plaud_env)
    (writer.root / "archive.json").rename(tmp_path / "archive.json.away")
    with pytest.raises(ImportStopped, match="archive has dropped off") as caught:
        run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    assert [f["reason"] for f in caught.value.report.failed] == [
        "NotTheWriter: this process may not write the archive now"]
    assert caught.value.report.created == 0


def test_a_real_run_refuses_media_it_cannot_measure(ar_archive, plaud_env, writer, monkeypatch):
    # why: §9.1.1. Reconcile stores its timing check for good, so an import without a decoded
    # duration would store it without one. The run stops before it writes anything.
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.plaud(B, envelopes=[plaud_env(B)], ext=".m4a",
                     recorded_at="2026-08-02T09:00:00-07:00")
    ar_archive.write_catalog()
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    with pytest.raises(ImportStopped, match="can't be measured for 1 of 2"):
        run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    assert list(writer.archive.iter_recordings()) == []


def test_privacy_is_in_place_when_a_recording_is_created(ar_archive, plaud_env, writer,
                                                          monkeypatch):
    # why: §7.4. A private tag is never a second write after the content: a catalog label, and a
    # Plaud title that matches the auto-private patterns (in any snapshot, date prefix and all),
    # are both in Incoming.tags when the recording is created.
    ar_archive.plaud(A, envelopes=[plaud_env(A)], tags=("private",))  # the main tier, tagged
    ar_archive.plaud(B, envelopes=[plaud_env(B, name="10-06 Private catch-up"),
                                   plaud_env(B, name="Catch-up")],
                     recorded_at="2026-08-02T09:00:00-07:00")
    ar_archive.write_catalog()
    seen = {}
    real_add = Writer.add

    def add(self, incoming):
        seen[incoming.sources[0].ref] = [(t.tag, t.by) for t in incoming.tags]
        return real_add(self, incoming)

    monkeypatch.setattr(Writer, "add", add)
    report = run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    assert report.failed == []
    assert seen == {A: [("private", "you")], B: [("private", "auto")]}


def test_privacy_covers_every_copy_of_the_audio_before_the_first_is_created(
        ar_archive, plaud_env, writer, monkeypatch):
    # why: §7.4. B is A's duplicate (the same audio, so one recording), and B's title is
    # auto-private. A is added first. It must be created private, never made private by B's
    # merge after A's content and outputs are already in the archive.
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.plaud(B, envelopes=[plaud_env(B, name="Interview: Grace")],
                     same_audio_as=f"plaud/audio/2026/08/{A}.wav", dup_of=f"plaud/{A}")
    ar_archive.write_catalog()
    seen = []
    real_add = Writer.add

    def add(self, incoming):
        seen.append((incoming.sources[0].ref, [(t.tag, t.by) for t in incoming.tags]))
        return real_add(self, incoming)

    monkeypatch.setattr(Writer, "add", add)
    report = run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    assert report.failed == []
    assert seen == [(A, [("private", "auto")]), (B, [("private", "auto")])]


def test_audio_that_changed_after_the_plan_is_refused(ar_archive, plaud_env, writer):
    # why: the plan hashed the audio. The run passes that hash, and add refuses other bytes.
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.write_catalog()
    the_plan = plan(ar_archive.root, writer.index, consent=())
    (ar_archive.root / f"plaud/audio/2026/08/{A}.wav").write_bytes(b"RIFF changed after the plan")
    report = run(the_plan, writer, now=NOW)
    assert report.failed == [{"uri": f"plaud/{A}", "reason": "WriterError: the writer refused it"}]
    assert list(writer.archive.iter_recordings()) == []


def test_a_failure_is_reported_in_fixed_words(ar_archive, plaud_env, writer, monkeypatch):
    # Review Focus 4: an exception's text can carry a title, so a report gives its type and a
    # fixed phrase, never its message.
    build(ar_archive, plaud_env)

    def refuse(self, incoming):
        raise ValueError(f"input_value={incoming.title!r}")

    monkeypatch.setattr(Writer, "add", refuse)
    report = run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    assert {f["reason"] for f in report.failed} == {"ValueError: a value did not validate"}
    text = json.dumps(report.to_dict(), ensure_ascii=False)
    for title in (JA, "会議メモ", "Week 4", "Pocket chat", "Aug 3 at 10-00", "Jul 15"):
        assert title not in text


def test_an_archive_that_reads_as_empty_stops_the_import(ar_archive, plaud_env, writer,
                                                          monkeypatch):
    # why: §6.7. add refuses when the archive reads as empty (its disk unmounted, most likely),
    # and the run stops there, in fixed words, rather than going on into it.
    build(ar_archive, plaud_env)

    titles = []

    def unmounted(self, incoming):
        # Writer.add's real IndexRefused messages carry paths and counts, never a title. This
        # stub is a canary: if the run ever repeated an exception's text, the title would show.
        titles.append(incoming.title)
        raise IndexRefused(f"no recordings under the archive (canary {incoming.title!r})")

    monkeypatch.setattr(Writer, "add", unmounted)
    with pytest.raises(ImportStopped, match="reads as empty") as caught:
        run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    assert [f["reason"] for f in caught.value.report.failed] == [
        "IndexRefused: the archive reads as empty or unreadable"]
    text = str(caught.value) + json.dumps(caught.value.report.to_dict(), ensure_ascii=False)
    assert titles and all(title not in text and "canary" not in text for title in titles)


def test_the_import_holds_the_ops_lock(ar_archive, plaud_env, writer):
    # why: the backup loop and the CLI's jobs share the "ops" lock (Task 11), so no snapshot or
    # mirror run lands half-way through an import.
    build(ar_archive, plaud_env)
    the_plan = plan(ar_archive.root, writer.index, consent=())
    with Locks(writer.state.locks_dir, timeout=0).hold("ops"):
        with pytest.raises(LockTimeout):
            run(the_plan, writer, now=NOW, lock_timeout=0.1)
    assert list(writer.archive.iter_recordings()) == []


def test_mapping_audio_routers_outputs(tmp_path):
    words = map_rendition(tmp_path / "words-mlx_whisper-v-20260801T170000Z.json", WORDS).rendition
    assert words.kind == "transcript" and words.engine == "mlx-whisper"
    assert words.model == "mlx-community/whisper-large-v3-turbo"
    assert words.inputs == {"audio_sha256": "x", "sample_rate": "16000"}
    assert [[w["word"] for w in s["words"]] for s in words.payload["segments"]] == [
        ["Hello", "there."], ["Hi."]]
    summary = map_rendition(tmp_path / "summary-x-v-20260801T170300Z.json", SUMMARY).rendition
    assert summary.payload["markdown"] == ("# Greetings\n\nTwo people meet.\n\n## Actions\n\n"
                                           "- Say goodbye (Ada)")
    assert map_rendition(tmp_path / HUMAN_FILE, HUMAN).keep_raw
    assert map_rendition(tmp_path / "words-x-v-nostamp.json", WORDS).skipped
    assert map_rendition(tmp_path / "x-20260801T170000Z.json", ["not", "a", "dict"]).skipped
    assert map_rendition(tmp_path / "x-20260801T170000Z.json", {**WORDS, "kind": "novel"}).skipped


def test_untimed_turns_words_and_segments_are_kept(tmp_path):
    # why: audio-router keeps an untimed word where it arrived, and one can open a merged turn
    # (its MergedTurn has a nullable start and end). Its text must survive, as reconcile keeps
    # Plaud's untimed segments.
    merged = {**MERGED, "payload": {"turns": [
        {"speaker": "SPEAKER_UNKNOWN", "start": None, "end": None, "text": "Um",
         "words": [{"word": "Um", "start": None, "end": None}], "inferred": False},
        *MERGED["payload"]["turns"]]}}
    out = map_rendition(tmp_path / "merged-x-v-20260801T170200Z.json", merged).rendition
    assert [(s["text"], s["start"], s["end"]) for s in out.payload["segments"]] == [
        ("Um", 0.0, 0.0), ("Hello there.", 0.0, 1.5)]
    assert out.payload["segments"][0]["words"] == [{"word": "Um", "start": 0.0, "end": 0.0}]
    assert (out.meta["untimed_segments"], out.meta["untimed_words"]) == (1, 1)
    words = {**WORDS, "payload": {**WORDS["payload"], "segments": [
        *WORDS["payload"]["segments"], {"id": 2, "start": None, "end": None, "text": " Bye."}]}}
    out = map_rendition(tmp_path / "words-x-v-20260801T170000Z.json", words).rendition
    assert [(s["text"], s["start"]) for s in out.payload["segments"]] == [
        ("Hello there.", 0.0), ("Hi.", 1.5), ("Bye.", 2.6)]
    assert out.meta["untimed_segments"] == 1


def test_pocket_segments_in_milliseconds_are_recognised():
    doc = {"duration": 2.0, "transcript": {"segments": [{"start": 0, "end": 1500, "text": "Hi"}]},
           "summarizations": {}}
    (transcript,) = pocket_outputs(doc, "source/pocket-x.json", NOW)
    assert transcript.payload["segments"][0]["end"] == 1.5 and transcript.meta["time_unit"] == "ms"


def test_a_pocket_outputs_identity_hashes_what_it_carries():
    # why: Task 7's ruling. part_sha256 is the hash of what the output carries, so a difference
    # it can't show (a number spelled 1500.0, a trailing space, an unknown field, a summary's
    # model) is never a new write-once output.
    doc = {"duration": 2.0,
           "transcript": {"segments": [{"start": 0, "end": 1500, "text": "Hi", "id": 1}]},
           "summarizations": {"a": {"text": " A chat. ", "model": "x"}}}
    same = {"duration": 2.0,
            "transcript": {"segments": [{"start": 0.0, "end": 1500.0, "text": "Hi ", "id": 2,
                                         "extra": True}]},
            "summarizations": {"a": {"text": "A chat.", "model": "y"}}}
    other = {**doc, "summarizations": {"a": {"text": "A different chat."}}}
    one = pocket_outputs(doc, "source/pocket-1.json", NOW)
    two = pocket_outputs(same, "source/pocket-2.json", NOW)
    assert [r.kind for r in one] == ["transcript", "notes"]
    assert [r.inputs["part_sha256"] for r in one] == [r.inputs["part_sha256"] for r in two]
    changed = pocket_outputs(other, "source/pocket-3.json", NOW)
    assert changed[0].inputs["part_sha256"] == one[0].inputs["part_sha256"]
    assert changed[1].inputs["part_sha256"] != one[1].inputs["part_sha256"]


def test_two_pocket_snapshots_that_carry_the_same_output_give_one(ar_archive, writer):
    ar_archive.pocket(UUID)
    ar_archive.write_catalog()
    run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    raw = ar_archive.root / "pocket" / "raw" / f"{UUID}.json"
    doc = json.loads(raw.read_text(encoding="utf-8"))
    doc["state"] = "processed"  # nothing an output carries
    doc["transcript"]["segments"][0]["end"] = 1.0  # spelled as a float, as it was an int
    raw.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    again = run(plan(ar_archive.root, writer.index, consent=()), writer,
                now=datetime(2026, 10, 10, tzinfo=timezone.utc))
    assert (again.sources_added, again.renditions_added, again.failed) == (1, 0, [])
    (rec,) = writer.archive.iter_recordings()
    assert sum(s.kind == "pocket" for s in rec.sources) == 2  # raw is raw: both are kept
    assert len(writer.archive.renditions(rec.id)) == 2  # one transcript, one summary


def test_a_consent_listed_recording_is_never_imported_through_its_copy_either(
        ar_archive, plaud_env, writer):
    # why: §9.1. The list excludes the recording, not only its ID: B's catalog row says it is
    # X's duplicate, and it carries X's audio. The run imports neither, and B's title, which
    # would be auto-private, never reaches a group's tags.
    ar_archive.plaud(X, envelopes=[plaud_env(X)], recorded_at="2026-08-05T09:00:00-07:00")
    ar_archive.plaud(B, envelopes=[plaud_env(B, name="Interview: Grace")],
                     same_audio_as=f"plaud/audio/2026/08/{X}.wav", dup_of=f"plaud/{X}")
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.write_catalog()
    the_plan = plan(ar_archive.root, writer.index, consent={X})
    assert the_plan.counts()["consent-excluded"] == 2
    report = run(the_plan, writer, now=NOW)
    assert (report.created, report.failed) == (1, [])
    refs = {s.ref for r in writer.archive.iter_recordings() for s in r.sources}
    assert refs == {A, f"plaud/{A}"}


def test_the_consent_list_is_read_as_audio_router_reads_it(tmp_path):
    path = tmp_path / "consent.md"
    path.write_text("# Never mirror\n"
                    f"- `{X}` — a recording Dan excluded\n"
                    f"{'6' * 32}\n"
                    f"We kept {'5' * 32} on purpose, so it is named in a sentence.\n"
                    f"# - {'4' * 32} is a comment\n", encoding="utf-8")
    assert load_consent_list(path) == {X, "6" * 32}
    assert consent_list_path({"plaud": {"consent_list": str(path)}}) == path


@pytest.mark.parametrize("content", [None, "", "No IDs here.\n"])
def test_the_consent_list_fails_closed(tmp_path, content):
    # why: §9.1. A missing, empty or ID-less list stops the import; it never means "exclude
    # nothing". So does an unset [plaud] consent_list.
    path = tmp_path / "consent.md"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(ConsentListError):
        load_consent_list(path)
    with pytest.raises(ConsentListError, match="consent_list"):
        consent_list_path({})


def _cli_config(tmp_path, monkeypatch, *, consent: str | None = CONSENT):
    text = (f'[archive]\npath = "{tmp_path / "archive"}"\nwriter_id = "test"\n'
            f'[state]\npath = "{tmp_path / "state"}"\n[index]\npath = "{tmp_path / "index.db"}"\n')
    if consent is not None:
        (tmp_path / "consent.md").write_text(consent, encoding="utf-8")
        text += f'[plaud]\nconsent_list = "{tmp_path / "consent.md"}"\n'
    cfg = tmp_path / "config.toml"
    cfg.write_text(text, encoding="utf-8")
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX"):
        monkeypatch.delenv(name, raising=False)


def test_the_cli_dry_run_then_import(ar_archive, plaud_env, tmp_path, monkeypatch, capsys):
    build(ar_archive, plaud_env)
    _cli_config(tmp_path, monkeypatch)
    assert main(["init", "--json"]) == 0
    capsys.readouterr()
    archive = tmp_path / "archive"
    before = sorted(p.relative_to(archive) for p in archive.rglob("*"))  # init's own files
    assert main(["import-audio-router", str(ar_archive.root), "--dry-run", "--json"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True and dry["by_disposition"]["new"] == 4
    assert sorted(p.relative_to(archive) for p in archive.rglob("*")) == before  # wrote nothing
    assert main(["import-audio-router", str(ar_archive.root), "--json"]) == 0
    done = json.loads(capsys.readouterr().out)
    assert done["imported"]["created"] == 5
    text = json.dumps(done, ensure_ascii=False)  # Review Focus 4
    for title in ("A PRIVATE TITLE", "Week 4", JA, "会議メモ", "Aug 3 at 10-00"):
        assert title not in text


def test_the_cli_reads_the_consent_list_and_fails_closed(ar_archive, plaud_env, tmp_path,
                                                         monkeypatch, capsys):
    build(ar_archive, plaud_env)
    ar_archive.plaud(X, envelopes=[plaud_env(X)], recorded_at="2026-08-05T09:00:00-07:00")
    ar_archive.write_catalog()
    _cli_config(tmp_path, monkeypatch, consent=None)
    assert main(["init", "--json"]) == 0
    capsys.readouterr()
    assert main(["import-audio-router", str(ar_archive.root), "--dry-run", "--json"]) == 78
    assert "consent_list" in json.loads(capsys.readouterr().out)["error"]
    _cli_config(tmp_path, monkeypatch, consent="")
    assert main(["import-audio-router", str(ar_archive.root), "--json"]) == 78
    assert "empty" in json.loads(capsys.readouterr().out)["error"]
    _cli_config(tmp_path, monkeypatch)
    assert main(["import-audio-router", str(ar_archive.root), "--dry-run", "--json"]) == 0
    dry = capsys.readouterr().out
    assert json.loads(dry)["by_disposition"]["consent-excluded"] == 1 and X not in dry
    assert main(["import-audio-router", str(ar_archive.root), "--json"]) == 0
    assert X not in capsys.readouterr().out
    refs = {s.ref for r in Archive(tmp_path / "archive").iter_recordings() for s in r.sources}
    assert X not in refs


def test_the_cli_refuses_unplaced_rows_unless_told(ar_archive, plaud_env, tmp_path, monkeypatch,
                                                    capsys):
    build(ar_archive, plaud_env)
    # A row of no known source, with no audio: it shares nothing with A, so A still imports.
    ar_archive.rows.append({**ar_archive.rows[0], "uri": "zoom/z1", "source": "zoom",
                            "audio_sha256": ""})
    ar_archive.write_catalog()
    _cli_config(tmp_path, monkeypatch)
    main(["init", "--json"])
    capsys.readouterr()
    assert main(["import-audio-router", str(ar_archive.root), "--json"]) == 1
    assert "--allow-unplaced" in json.loads(capsys.readouterr().out)["error"]
    assert main(["import-audio-router", str(ar_archive.root), "--allow-unplaced", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["imported"]["created"] == 5


def test_the_run_module_reports_counts_only():
    assert set(audio_router.RunReport().to_dict()) == {
        "created", "merged", "sources_added", "renditions_added", "plaud_outputs",
        "held_removals", "skipped_snapshots", "skipped_outputs", "measured", "failed"}
