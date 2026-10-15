import json
from datetime import datetime, timezone
from importlib import resources

import pytest
from pydantic import ValidationError

from recordings import schemas
from recordings.cli import main
from recordings.models import UNKNOWN_PERSON, Segment, SourceRef, Speakers, SpeakerSpan
from recordings.models import (
    Recording,
    Rendition,
    TagRef,
    Word,
    dump_json,
    is_private,
    is_private_tag,
    is_untagged,
)

SHA = "3fa91c2e" + "0" * 56


def recording(**over) -> Recording:
    base = dict(
        id="20261006T140003-0700_3fa91c2e",
        title="COURSE 101: Week 4",
        recorded_at=datetime.fromisoformat("2026-10-06T14:00:03-07:00"),
        timezone="America/Vancouver",
        time_source="plaud",
        media={"file": "20261006T140003-0700_3fa91c2e.mp3", "sha256": SHA, "kind": "audio"},
    )
    base.update(over)
    return Recording.model_validate(base)


def test_round_trip_keeps_dollar_schema_and_offset():
    rec = recording(schema_ref="../../../../schemas/recording.schema.json")
    text = dump_json(rec)
    data = json.loads(text)
    assert data["$schema"] == "../../../../schemas/recording.schema.json"
    assert data["recorded_at"] == "2026-10-06T14:00:03-07:00"
    assert data["format"] == "recordings-archive@1"
    assert Recording.model_validate_json(text) == rec


def test_naive_recorded_at_is_rejected():
    with pytest.raises(ValidationError):
        recording(recorded_at=datetime(2026, 10, 6, 14, 0, 3))


def test_bad_id_is_rejected():
    with pytest.raises(ValidationError, match="not a recording id"):
        recording(id="2026-10-06_3fa91c2e")


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Recording.model_validate({**json.loads(dump_json(recording())), "surprise": 1})


@pytest.mark.parametrize("bad", ["", "/x", "x/", "a//b", " lead", "trail ", "a/ b"])
def test_tag_names_have_no_empty_or_padded_parts(bad):
    with pytest.raises(ValidationError):
        TagRef(tag=bad)


@pytest.mark.parametrize(
    "tags, private",
    [
        ([], False),
        ([{"tag": "private"}], True),
        ([{"tag": "private/journal"}], True),
        ([{"tag": "privateer"}], False),  # a prefix of the word is not the folder
        ([{"tag": "talks"}, {"tag": "private/personal"}], True),
        ([{"tag": "talks"}, {"tag": "Private/Health"}], True),
        ([{"tag": "notes/private"}], False),
    ],
)
def test_is_private(tags, private):
    assert is_private(recording(tags=tags)) is private


@pytest.mark.parametrize("tag", ["private", "Private", "PRIVATE/journal", "private/health"])
def test_a_tag_whose_first_folder_is_private_in_any_case_is_private(tag):
    assert is_private_tag(tag) is True


@pytest.mark.parametrize("tag", ["privateer", "notes/private", "public", "Privateer/x"])
def test_other_tags_are_not_private(tag):
    assert is_private_tag(tag) is False


def test_is_untagged_means_no_tags_at_all():
    assert is_untagged(recording())
    assert not is_untagged(recording(tags=[{"tag": "notes/lecture", "by": "auto"}]))


def rendition(**over) -> Rendition:
    base = dict(
        kind="notes",
        note_type="lecture",
        engine="canned",
        model="claude-opus-5-5",
        version="claude-opus-5-5@demo",
        created_at=datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
        payload={"markdown": "# Notes"},
    )
    base.update(over)
    return Rendition.model_validate(base)


def test_rendition_dumps_schema_key():
    assert json.loads(dump_json(rendition()))["schema"] == "recordings/rendition@1"


def test_notes_need_a_note_type_and_markdown():
    with pytest.raises(ValidationError, match="note_type"):
        rendition(note_type=None)
    with pytest.raises(ValidationError):
        rendition(payload={"text": "wrong key"})


def test_transcript_payload_is_validated():
    ok = rendition(
        kind="transcript",
        note_type=None,
        payload={"segments": [{"start": 0, "end": 1.5, "text": "Hi", "words": []}]},
    )
    assert ok.payload["segments"][0]["text"] == "Hi"
    with pytest.raises(ValidationError):
        rendition(kind="transcript", note_type=None, payload={"segments": [{"start": 0}]})


def test_committed_schemas_match_the_models():
    committed = resources.files("recordings") / "format" / "schemas"
    for name, schema in schemas.generate().items():
        on_disk = json.loads((committed / name).read_text(encoding="utf-8"))
        assert on_disk == schema, f"{name} is stale: run `make schemas`"


def test_cli_schemas_check_passes_when_current():
    assert main(["schemas", "--check"]) == 0


def test_ids_with_non_ascii_digits_are_rejected():
    with pytest.raises(ValidationError):
        recording(id="٢٠٢٦١٠٠٦T١٤٠٠٠٣-0700_3fa91c2e")


def _committed_schema(name: str) -> dict:
    return json.loads(
        (resources.files("recordings") / "format" / "schemas" / name).read_text(encoding="utf-8")
    )


def test_the_committed_schema_checks_the_id_shape():
    import jsonschema

    schema = _committed_schema("recording.schema.json")
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    good = json.loads(dump_json(recording()))
    assert list(validator.iter_errors(good)) == []
    for bad in [
        "2026-10-06_3fa91c2e",
        "x20261006T140003-0700_3fa91c2e",
        "20261006T140003-0700_3fa91c2ef",
        "٢٠٢٦١٠٠٦T١٤٠٠٠٣-0700_3fa91c2e",
    ]:
        errors = list(validator.iter_errors({**good, "id": bad}))
        assert [e.json_path for e in errors] == ["$.id"], bad


def test_empty_speakers_still_serialise_as_an_empty_object():
    # why: §22 decision 12. Every existing file has "speakers": {}, and must keep it on a rewrite.
    data = json.loads(dump_json(recording()))
    assert data["speakers"] == {}
    assert "rev" not in data and "title_by" not in data and "plaud" not in data


def test_the_speakers_shape_round_trips_with_not_spelled_not():
    rec = recording(
        speakers={
            "source": "renditions/transcript-plaud-plaud@1-20261008T143000Z.json",
            "labels": {
                "Speaker 1": {"person": "ada-lovelace", "by": "you"},
                "Speaker 2": {"person": "grace-hopper", "by": "you", "plaud_name_seen": "Ada"},
                "Speaker 3": {"not": ["ada-lovelace"]},
            },
            "spans": [
                {"start": 41.2, "end": 58.0, "person": "grace-hopper", "by": "you"},
                {"start": 300.0, "end": 312.5, "person": UNKNOWN_PERSON, "by": "you"},
            ],
        }
    )
    data = json.loads(dump_json(rec))
    assert data["speakers"]["labels"]["Speaker 3"] == {"not": ["ada-lovelace"]}
    assert data["speakers"]["spans"][1]["person"] == "unknown"
    assert Recording.model_validate_json(dump_json(rec)) == rec


@pytest.mark.parametrize(
    "speakers, message",
    [
        ({"labels": {"Speaker 1": {"person": "Ada Lovelace", "by": "you"}}}, "pattern"),
        ({"labels": {"Speaker 1": {"person": "ada"}}}, "person and by go together"),
        ({"labels": {"Speaker 1": {"by": "you"}}}, "person and by go together"),
        ({"labels": {"Speaker 1": {"person": "ada", "by": "auto"}}}, "you"),
        ({"spans": [{"start": 5.0, "end": 5.0, "person": "ada"}]}, "end after it starts"),
        ({"spans": [{"start": 1.0, "end": 2.0, "person": "ada", "from": "voice"}]}, "Extra inputs"),
    ],
)
def test_bad_speaker_decisions_are_rejected(speakers, message):
    with pytest.raises(ValidationError, match=message):
        recording(speakers=speakers)


def test_rev_title_by_and_plaud_are_optional_and_validated():
    rec = recording(
        rev=7,
        title_by="plaud",
        plaud={"title_seen": "Week 4", "acknowledged_up_to": "source/plaud-x.json"},
    )
    assert Recording.model_validate_json(dump_json(rec)) == rec
    with pytest.raises(ValidationError):
        recording(rev=-1)
    with pytest.raises(ValidationError):
        recording(title_by="auto")


@pytest.mark.parametrize("who", ["you", "plaud", "recorder", "pocket"])
def test_title_by_is_you_or_the_source_kind(who):
    # why: §6.3. The import sets title_by to the item's source kind, not only plaud.
    assert recording(title_by=who).title_by == who


def test_a_validation_error_never_echoes_the_input():
    # why: the import reports a failed row's error. A title or transcript text must never reach a
    # report or an alert, so pydantic leaves the input out of its messages.
    with pytest.raises(ValidationError) as caught:
        recording(title_by="会議メモ / Q&A")
    assert "会議メモ" not in str(caught.value)
    with pytest.raises(ValidationError) as caught:
        Rendition(
            kind="transcript",
            engine="plaud",
            version="plaud@1",
            created_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
            payload={"segments": [{"start": 0, "text": "Ada said something private."}]},
        )
    assert "something private" not in str(caught.value)


def test_plaud_segment_fields_and_snapshot_fields():
    seg = Segment(
        start=0,
        end=1.5,
        speaker="Speaker 1",
        speaker_name="Ada",
        embedding_key="ek-1",
        text="Hello.",
    )
    assert seg.model_dump(exclude_none=True)["speaker_name"] == "Ada"
    ref = SourceRef(
        kind="plaud",
        ref="a" * 32,
        added_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        sha256="b" * 64,
    )
    assert SourceRef.model_validate_json(ref.model_dump_json()) == ref
    with pytest.raises(ValidationError):
        SourceRef(
            kind="plaud",
            ref="x",
            added_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
            sha256="not-a-hash",
        )


def test_media_info_keeps_sample_rate_and_channels():
    rec = recording(
        media={
            "file": "x.mp3",
            "sha256": SHA,
            "kind": "audio",
            "duration_ms": 1000,
            "sample_rate": 44100,
            "channels": 2,
        }
    )
    assert (rec.media.sample_rate, rec.media.channels) == (44100, 2)
    with pytest.raises(ValidationError):
        recording(media={"file": "x.mp3", "sha256": SHA, "kind": "audio", "channels": 0})


def test_speakers_default_is_empty():
    assert Speakers().labels == {} and Speakers().spans == [] and Speakers().source is None


def test_a_span_is_yours_by_default():
    span = SpeakerSpan(start=1, end=2, person="ada")
    assert span.by == "you"


def test_non_finite_numbers_are_refused_so_a_written_file_always_reads_back():
    for bad in (float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            Word(word="x", start=0, end=bad)
        with pytest.raises(ValidationError):
            SpeakerSpan(start=0, end=bad, person="alex")


def test_two_spans_with_the_same_times_are_refused_but_overlaps_are_fine():
    def span(a, b):
        return {"start": a, "end": b, "person": "alex"}

    assert len(Speakers(spans=[span(0, 9), span(3, 5)]).spans) == 2
    with pytest.raises(ValidationError, match="same start and end"):
        Speakers(spans=[span(1, 2), span(1, 2)])


def test_validation_errors_never_quote_a_tag_or_an_id():
    with pytest.raises(ValidationError) as tag_err:
        TagRef(tag="a//Marital counselling ")
    assert "counselling" not in str(tag_err.value)
    with pytest.raises(ValidationError) as id_err:
        recording(id="Secret title")
    assert "Secret" not in str(id_err.value)
