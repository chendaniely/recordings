import json
from datetime import datetime, timezone
from importlib import resources

import pytest
from pydantic import ValidationError

from recordings import schemas
from recordings.cli import main
from recordings.models import (
    Recording,
    Rendition,
    TagRef,
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
        (resources.files("recordings") / "format" / "schemas" / name).read_text(encoding="utf-8"))


def test_the_committed_schema_checks_the_id_shape():
    import jsonschema

    schema = _committed_schema("recording.schema.json")
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    good = json.loads(dump_json(recording()))
    assert list(validator.iter_errors(good)) == []
    for bad in ["2026-10-06_3fa91c2e", "x20261006T140003-0700_3fa91c2e",
                "20261006T140003-0700_3fa91c2ef", "٢٠٢٦١٠٠٦T١٤٠٠٠٣-0700_3fa91c2e"]:
        errors = list(validator.iter_errors({**good, "id": bad}))
        assert [e.json_path for e in errors] == ["$.id"], bad
