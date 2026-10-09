import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from recordings.archive import RawSource
from recordings.config import Config, ConfigError
from recordings.models import MediaInfo, PlaudState, Recording, TagRef, is_private
from recordings.plaud.reconcile import (
    PLAUD_VERSION,
    Snapshot,
    matches_auto_private,
    patterns_from_config,
    reconcile,
    strip_date_prefix,
    timing_check,
)
from recordings.writer import NotTheWriter, Writer, rendition_filename

HEX = "0123456789abcdef0123456789abcdef"
OTHER = "fedcba9876543210fedcba9876543210"
T1 = datetime(2026, 8, 1, 16, 10, tzinfo=timezone.utc)


def rec(**over) -> Recording:
    fields = dict(id="20260801T090000-0700_" + "a" * 8, title="Week 4", title_by="plaud",
                  recorded_at=datetime.fromisoformat("2026-08-01T09:00:00-07:00"),
                  timezone="America/Vancouver", time_source="plaud",
                  media=MediaInfo(file="x.wav", sha256="a" * 64, kind="audio", duration_ms=2600))
    fields.update(over)
    return Recording(**fields)


def snap(env, n=0, *, ref=HEX) -> Snapshot:
    when = T1 + timedelta(days=n)
    return Snapshot(path=f"source/plaud-{when:%Y%m%dT%H%M%SZ}.json", ref=ref, fetched_at=when,
                    envelope=env)


def kinds(outputs):
    return sorted((o.kind, o.note_type) for o in outputs)


def test_the_first_snapshot_gives_a_transcript_and_a_notes_output_per_tab(plaud_env):
    result = reconcile(rec(), [snap(plaud_env())], [])
    assert kinds(result.outputs) == [("notes", "plaud-high-light"), ("notes", "plaud-outline"),
                                     ("notes", "plaud-summary"), ("transcript", None)]
    transcript = next(o for o in result.outputs if o.kind == "transcript")
    assert transcript.version == PLAUD_VERSION == "plaud@1"
    assert transcript.created_at == T1
    assert transcript.inputs["source"] == "source/plaud-20260801T161000Z.json"
    assert len(transcript.inputs["part_sha256"]) == 64
    first, second = transcript.payload["segments"]
    assert first == {"start": 0.0, "end": 1.5, "text": "Hello there.", "speaker": "Speaker 1",
                     "speaker_name": "Ada", "embedding_key": "ek-1"}
    assert second == {"start": 1.5, "end": 2.6, "text": "Hi.", "speaker": "Speaker 2"}
    assert transcript.meta["timing"]["ok"] is True
    summary = next(o for o in result.outputs if o.note_type == "plaud-summary")
    assert summary.payload["markdown"] == "# Summary\n\nTwo people say hello."


def test_reconcile_is_idempotent(plaud_env):
    first = reconcile(rec(), [snap(plaud_env())], [])
    again = reconcile(rec(), [snap(plaud_env())], first.outputs)
    assert again.outputs == ()


def test_an_output_of_another_version_is_not_this_ones(plaud_env):
    # why: §6.5. `version` names the normaliser, and is part of an output's identity, so a
    # normaliser bump writes every part again.
    first = reconcile(rec(), [snap(plaud_env())], [])
    older = [o.model_copy(update={"version": "plaud@0"}) for o in first.outputs]
    assert kinds(reconcile(rec(), [snap(plaud_env())], older).outputs) == kinds(first.outputs)


def test_notes_are_rendered_from_the_raw_block_and_hashed_from_the_normalised_one(plaud_env):
    # why: notes render from the raw block, so a link and a highlight's picture link survive as
    # Plaud wrote them. A rotating signature is still never a change.
    link = "https://notes.example/a.png?X-Amz-Expires=300&X-Amz-Signature={}"

    def envelope(signature):
        return plaud_env(notes=[
            {"data_type": "auto_sum_note", "data_id": "n", "data_tab_name": "Summary",
             "data_content": f"See [the chart]({link.format(signature)}) here."},
            {"data_type": "high_light", "data_id": "h", "data_tab_name": None,
             "data_content": json.dumps([{"timestamp": 1000, "title": "Board",
                                          "content": "A sketch",
                                          "picture_link": link.format(signature)}])}])

    s1, s2 = snap(envelope("one")), snap(envelope("two"), 1)
    first = reconcile(rec(), [s1], [])
    summary = next(o for o in first.outputs if o.note_type == "plaud-summary")
    assert summary.payload["markdown"] == f"See [the chart]({link.format('one')}) here."
    highlight = next(o for o in first.outputs if o.note_type == "plaud-high-light")
    assert highlight.payload["markdown"] == (
        f"**[00:01] Board**\n\nA sketch\n\n(image: {link.format('one')})")
    assert reconcile(rec(), [s1, s2], first.outputs).outputs == ()


def test_a_title_edit_writes_no_new_output(plaud_env):
    s1 = snap(plaud_env())
    first = reconcile(rec(), [s1], [])
    result = reconcile(rec(), [s1, snap(plaud_env(name="Week 4: revised"), 1)], first.outputs)
    assert result.outputs == () and result.title == "Week 4: revised"


def test_a_speaker_rename_writes_a_new_transcript_and_a_note_edit_only_that_note(plaud_env):
    env = plaud_env()
    rows = json.loads(env["source_list"][0]["data_content"])
    renamed = plaud_env(segments=[rows[0], {**rows[1], "speaker": "Grace"}])
    renamed["note_list"][0]["data_content"] += " Then they leave."
    s1, s2 = snap(env), snap(renamed, 1)
    first = reconcile(rec(), [s1], [])
    result = reconcile(rec(), [s1, s2], first.outputs)
    assert kinds(result.outputs) == [("notes", "plaud-summary"), ("transcript", None)]
    assert all(o.inputs["source"] == s2.path for o in result.outputs)
    names = [s.get("speaker_name") for s in next(o for o in result.outputs
                                                 if o.kind == "transcript").payload["segments"]]
    assert names == ["Ada", "Grace"]


def test_a_vanished_note_tab_waits_for_review(plaud_env):
    env = plaud_env()
    without = plaud_env(notes=[env["note_list"][1]])
    s1, s2 = snap(env), snap(without, 1)
    first = reconcile(rec(), [s1], [])
    result = reconcile(rec(), [s1, s2], first.outputs)
    assert result.outputs == ()
    assert [(h.snapshot, h.slot, h.reason) for h in result.held] == [
        (s2.path, "notes:plaud-summary", "notes removed")]
    reviewed = rec(plaud=PlaudState(acknowledged_up_to=s2.path))
    assert reconcile(reviewed, [s1, s2], first.outputs).held == ()


def test_names_reverting_to_speaker_n_wait_for_review(plaud_env):
    env = plaud_env()
    rows = json.loads(env["source_list"][0]["data_content"])
    reverted = plaud_env(segments=[{**rows[0], "speaker": "Speaker 1"}, rows[1]])
    s1, s2 = snap(env), snap(reverted, 1)
    first = reconcile(rec(), [s1], [])
    result = reconcile(rec(), [s1, s2], first.outputs)
    assert result.outputs == ()
    assert [h.reason for h in result.held] == ["names removed"]
    reviewed = rec(plaud=PlaudState(acknowledged_up_to=s2.path))
    assert kinds(reconcile(reviewed, [s1, s2], first.outputs).outputs) == [("transcript", None)]


def test_names_reverting_wait_for_review_when_rows_have_no_original_speaker(plaud_env):
    # why: the diarization key must not depend on the current name, or "Ada" going back to
    # "Speaker 1" reads as a new diarization and the removal is applied without review.
    rows = [{"content": "Hello there.", "start_time": 0, "end_time": 1500, "speaker": "Ada"},
            {"content": "Hi.", "start_time": 1500, "end_time": 2600, "speaker": "Speaker 2"}]
    env = plaud_env(segments=rows)
    reverted = plaud_env(segments=[{**rows[0], "speaker": "Speaker 1"}, rows[1]])
    s1, s2 = snap(env), snap(reverted, 1)
    first = reconcile(rec(), [s1], [])
    result = reconcile(rec(), [s1, s2], first.outputs)
    assert result.outputs == ()
    assert [h.reason for h in result.held] == ["names removed"]


def test_an_unknown_note_field_never_writes_a_notes_output(plaud_env):
    # why: §9.1.1. A note field outside its own keys is "other", never a notes edit, and outputs
    # are write-once: a volatile field must not leave a duplicate notes output per fetch.
    snaps = []
    for i in range(3):
        env = plaud_env()
        for block in env["note_list"]:
            block["data_error_code"] = i
        snaps.append(snap(env, i))
    once = reconcile(rec(), snaps[:1], [])
    assert kinds(reconcile(rec(), snaps, []).outputs) == kinds(once.outputs)


def test_a_vanished_transcript_waits_for_review(plaud_env):
    env = plaud_env()
    gone = plaud_env()
    gone["source_list"] = [b for b in gone["source_list"] if b["data_type"] != "transaction"]
    first = reconcile(rec(), [snap(env)], [])
    result = reconcile(rec(), [snap(env), snap(gone, 1)], first.outputs)
    assert [h.reason for h in result.held] == ["transcript removed"]


def test_a_reverted_transcript_gets_its_own_output(plaud_env):
    env = plaud_env()
    rows = json.loads(env["source_list"][0]["data_content"])
    edited = plaud_env(segments=[{**rows[0], "content": "Hello!"}, rows[1]])
    snaps = [snap(env), snap(edited, 1), snap(plaud_env(), 2)]
    result = reconcile(rec(), snaps, [])
    transcripts = [o for o in result.outputs if o.kind == "transcript"]
    assert [o.inputs["source"] for o in transcripts] == [s.path for s in snaps]


def test_snapshots_are_taken_in_fetch_order_not_list_order(plaud_env):
    env = plaud_env()
    rows = json.loads(env["source_list"][0]["data_content"])
    later = plaud_env(segments=[{**rows[0], "content": "Hello!"}, rows[1]])
    s1, s2 = snap(env), snap(later, 1)
    result = reconcile(rec(), [s2, s1], [])
    transcripts = [o for o in result.outputs if o.kind == "transcript"]
    assert [o.inputs["source"] for o in transcripts] == [s1.path, s2.path]


def test_a_degraded_or_unreadable_snapshot_is_skipped_and_reported(plaud_env):
    # Review Focus 3: nothing is ever built from a snapshot with link errors, or from one that
    # isn't a JSON object.
    s1 = snap(plaud_env(link_error=True))
    s2 = snap(None, 1)
    s3 = Snapshot(path="source/plaud-x.json", ref=HEX, fetched_at=T1 + timedelta(days=2),
                  envelope=["not", "an", "object"])
    result = reconcile(rec(), [s1, s2, s3], [])
    assert result.outputs == () and result.skipped == (s1.path, s2.path, s3.path)


def test_plaud_owns_the_title_only_while_title_by_is_plaud_or_absent(plaud_env):
    renamed = [snap(plaud_env(name="Week 5"))]
    owned = reconcile(rec(), renamed, [])
    assert (owned.title, owned.title_seen, owned.title_difference) == ("Week 5", "Week 5", None)
    assert reconcile(rec(title_by=None), renamed, []).title == "Week 5"
    for other in ("you", "recorder", "pocket"):
        kept = reconcile(rec(title="My title", title_by=other), renamed, [])
        assert (kept.title, kept.title_seen, kept.title_difference) == (None, None, "Week 5")
    dismissed = rec(title="My title", title_by="you", plaud=PlaudState(title_seen="Week 5"))
    assert reconcile(dismissed, renamed, []).title_difference is None


@pytest.mark.parametrize("title, private", [
    ("Private: therapy", True), ("  private notes", True), ("Interview: Grace", True),
    ("10-06 Private session", True), ("10-06-private notes", True),
    ("1-2 Interview: Grace", True),
    ("Consultation: Private Chef Charity", False), ("10-06 Consultation: Private Chef", False),
    ("Week 4", False)])
def test_the_auto_private_patterns_add_private(plaud_env, title, private):
    assert reconcile(rec(), [snap(plaud_env(name=title))], []).add_private is private


def test_plauds_date_prefix_is_stripped_before_matching():
    # why: Plaud starts its titles "MM-DD ", so an anchored pattern would match no real title.
    # audio-router's normalize_title strips the same prefix.
    assert strip_date_prefix("10-06 Private session") == "Private session"
    assert strip_date_prefix(" 1-2-Interview: x") == "Interview: x"
    assert strip_date_prefix("2026-10-06 14:00") == "2026-10-06 14:00"
    assert strip_date_prefix("Week 4") == "Week 4"
    assert matches_auto_private(["Week 4", "10-06 Private session"])
    assert not matches_auto_private(["10-06 Week 4"])
    assert matches_auto_private(["10-06 Week 4"], ["^10-06"])  # the title as given counts too


def test_auto_private_checks_every_snapshot_title_and_both_plaud_ids(plaud_env):
    # why: privacy errs towards private. A title Plaud has since changed, the other Plaud ID's
    # title, and a degraded snapshot's title all still count.
    renamed_since = [snap(plaud_env(name="10-06 Private session")),
                     snap(plaud_env(name="Week 4"), 1)]
    assert reconcile(rec(), renamed_since, []).add_private is True
    other_id = [snap(plaud_env(name="Week 4")),
                snap(plaud_env(OTHER, name="Interview: Ada"), 1, ref=OTHER)]
    assert reconcile(rec(), other_id, []).add_private is True
    degraded = [snap(plaud_env(name="Week 4")),
                snap(plaud_env(name="Private", link_error=True), 1)]
    assert reconcile(rec(), degraded, []).add_private is True


def test_auto_private_never_removes_and_skips_an_already_private_recording(plaud_env):
    already = rec(tags=[TagRef(tag="private", by="you")])
    assert reconcile(already, [snap(plaud_env(name="Private: x"))], []).add_private is False


def test_two_plaud_ids_on_one_recording_are_reconciled_separately(plaud_env):
    a = snap(plaud_env(HEX, name="First"))
    b = snap(plaud_env(OTHER, name="Second"), 1, ref=OTHER)
    result = reconcile(rec(title="First"), [a, b], [])
    assert sum(o.kind == "transcript" for o in result.outputs) == 2
    assert result.title is None  # the title follows the Plaud ID added first


def test_the_timing_check_catches_a_trim():
    rows = [{"start_time": 0, "end_time": 3500}]
    ok = timing_check({"duration": 3500}, rows, 3400)
    trimmed = timing_check({"duration": 5000}, rows, 3400)
    unknown = timing_check({"duration": 3500}, rows, None)
    assert ok == {"rule": "timing@1", "plaud_duration_ms": 3500, "last_segment_end_ms": 3500,
                  "media_duration_ms": 3400, "ok": True}
    assert trimmed["ok"] is False
    assert "ok" not in unknown and "media_duration_ms" not in unknown


def test_untimed_segments_are_kept_and_counted(plaud_env):
    env = plaud_env(segments=[{"content": "No timing.", "speaker": "Speaker 1"},
                              {"content": "Timed.", "start_time": 1000, "end_time": 2000,
                               "speaker": "Speaker 1"}])
    (transcript,) = [o for o in reconcile(rec(), [snap(env)], []).outputs if o.kind == "transcript"]
    assert [(s["start"], s["end"]) for s in transcript.payload["segments"]] == [(0.0, 0.0), (1.0, 2.0)]
    assert transcript.meta["untimed_segments"] == 1


def test_a_tab_named_in_another_script_gives_a_safe_file_name(plaud_env):
    # Review Focus 4.
    env = plaud_env(notes=[{"data_type": "auto_sum_note", "data_id": "n",
                            "data_tab_name": "会議メモ / Q&A", "data_content": "x"}])
    (note,) = [o for o in reconcile(rec(), [snap(env)], []).outputs if o.note_type != "plaud-outline"
               and o.kind == "notes"]
    assert note.note_type == "plaud-q-a"
    name = rendition_filename(note)
    assert name.isascii() and "/" not in name and name.startswith("notes-plaud-q-a-plaud-plaud@1-")


def test_the_patterns_come_from_config_and_are_checked():
    assert patterns_from_config({}) == (r"^\s*private", r"interview:")
    assert patterns_from_config({"plaud": {"auto_private_patterns": ["^x"]}}) == ("^x",)
    with pytest.raises(ConfigError, match="auto_private_patterns"):
        patterns_from_config({"plaud": {"auto_private_patterns": ["("]}})
    with pytest.raises(ConfigError, match="auto_private_patterns"):
        patterns_from_config({"plaud": {"auto_private_patterns": "^x"}})


# ---- through the Writer ----------------------------------------------------------------------

def test_the_writer_reconciles_once_and_owns_the_plaud_fields(writer, make_incoming, plaud_env):
    payload = json.dumps(plaud_env(name="Interview: Ada")).encode()
    added = writer.add(make_incoming(
        title="Interview: Ada", title_by="plaud",
        sources=(RawSource(kind="plaud", ref=HEX, added_at=T1, fetched_at=T1, payload=payload),)))
    report = writer.reconcile_plaud(added.recording.id)
    assert report.written == 4 and report.held == () and report.skipped == ()
    recording = writer.archive.load(added.recording.id)
    assert recording.plaud.title_seen == "Interview: Ada"
    assert ("private", "auto") in {(t.tag, t.by) for t in recording.tags}
    assert writer.reconcile_plaud(added.recording.id).written == 0


def test_the_private_tag_lands_before_any_plaud_output(writer, make_incoming, plaud_env,
                                                        monkeypatch):
    # why: privacy first (§7.4). Were the outputs written first, a crash before the tag would
    # leave Plaud's transcript and notes in a recording nothing marks private.
    payload = json.dumps(plaud_env(name="10-06 Private session")).encode()
    added = writer.add(make_incoming(title="Week 4", title_by="plaud", sources=(
        RawSource(kind="plaud", ref=HEX, added_at=T1, fetched_at=T1, payload=payload),)))
    private_at_each_output = []
    real = Writer._write_rendition_locked

    def watched(self, folder, rendition):
        private_at_each_output.append(is_private(self.archive.load(added.recording.id)))
        return real(self, folder, rendition)

    monkeypatch.setattr(Writer, "_write_rendition_locked", watched)
    assert writer.reconcile_plaud(added.recording.id).written == 4
    assert private_at_each_output == [True] * 4


def test_reindex_finishes_an_update_interrupted_after_its_snapshot(writer, make_incoming,
                                                                    plaud_env):
    # why: §9.1.1. Reconcile runs on reindex, so a snapshot written before a crash is still built.
    env = plaud_env()
    rows = json.loads(env["source_list"][0]["data_content"])
    first = writer.add(make_incoming(b"same", title_by="plaud", sources=(
        RawSource(kind="plaud", ref=HEX, added_at=T1, fetched_at=T1,
                  payload=json.dumps(env).encode()),))).recording
    writer.reconcile_plaud(first.id)
    later = plaud_env(segments=[{**rows[0], "content": "Hello!"}, rows[1]])
    writer.add(make_incoming(b"same", sources=(
        RawSource(kind="plaud", ref="of_" + HEX, added_at=T1, fetched_at=T1 + timedelta(days=1),
                  payload=json.dumps(later).encode()),)))
    report = writer.reindex()
    (rec_report,) = report.reconciled
    assert rec_report.written == 1
    assert report.to_dict()["plaud"] == {"written": 1, "held": 0, "skipped": 0, "problems": 0}


def test_a_bad_pattern_in_config_refuses_the_writer_cleanly(new_archive, tmp_path):
    # why: a typo in [plaud] auto_private_patterns must give the CLI exit 78 and let the web
    # app start read-only, not a traceback: NotTheWriter is what both callers already handle.
    root, state = new_archive
    cfg = Config(path=None, archive_path=root, writer_id="test",
                 default_timezone="America/Vancouver", base_url=None,
                 data={"plaud": {"auto_private_patterns": ["("]}},
                 state_path=state, index_path=tmp_path / "index" / "index.db")
    with pytest.raises(NotTheWriter, match="auto_private_patterns"):
        Writer.open(cfg)


def test_reindex_reconciles_after_it_rebuilds_the_index(writer, make_incoming, plaud_env, caplog):
    # why: with index.db gone, a reconcile before the rebuild would log "left pending" for each
    # recording whose fields it changes; after the rebuild, each `mutate` just upserts.
    for n in range(2):
        fid = HEX[:-1] + str(n)
        payload = json.dumps(plaud_env(fid, name="Interview: A")).encode()
        writer.add(make_incoming(title_by="plaud", sources=(
            RawSource(kind="plaud", ref=fid, added_at=T1, fetched_at=T1, payload=payload),)))
    writer.index.path.unlink()
    with caplog.at_level(logging.WARNING, logger="recordings.writer"):
        report = writer.reindex()
    assert not [r for r in caplog.records if "left pending" in r.getMessage()]
    assert report.recordings == 2 and report.to_dict()["plaud"]["written"] == 8


def test_reconcile_holds_no_lock_when_it_returns(writer, make_incoming, plaud_env):
    # why: Task 5b. `add`, `rebuild_index` and `reindex` take locks that sort before a held
    # recording's, so reconcile must never call them, and must leave nothing held.
    payload = json.dumps(plaud_env(name="Week 4")).encode()
    added = writer.add(make_incoming(title_by="plaud", sources=(
        RawSource(kind="plaud", ref=HEX, added_at=T1, fetched_at=T1, payload=payload),)))
    writer.reconcile_plaud(added.recording.id)
    assert not writer.locks._held()  # a released key is removed, so nothing is held
