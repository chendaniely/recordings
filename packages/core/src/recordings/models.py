"""The archive's data model (spec §6.3, §6.5). JSON Schemas are generated from these."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from recordings.ids import ID_RE

FORMAT = "recordings-archive@1"
RENDITION_SCHEMA = "recordings/rendition@1"
# recording.json sits at recordings/YYYY/MM/<id>/, four levels below the archive root.
SCHEMA_REF = "../../../../schemas/recording.schema.json"

TimeSource = Literal["plaud", "metadata", "published", "mtime", "ingest"]


class _Model(BaseModel):
    # forbid: a typo in a hand-edited file is an error, not a silently ignored key (§11).
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class MediaInfo(_Model):
    file: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal["audio", "video"]
    duration_ms: int | None = Field(default=None, ge=0)
    audio_file: str | None = None  # extracted audio track, video only


class SourceRef(_Model):
    kind: str  # "plaud", "url", "upload", "watched_folder", "audio_router"
    ref: str  # opaque, exactly as the source returned it (Plaud now sends "of_…")
    added_at: AwareDatetime
    raw: str | None = None  # e.g. "source/plaud-20261008T143000Z.json"


class TagRef(_Model):
    tag: str
    by: Literal["you", "auto"] = "you"

    @field_validator("tag")
    @classmethod
    def _folders_are_clean(cls, value: str) -> str:
        parts = value.split("/")
        if any(not p or p != p.strip() for p in parts):
            raise ValueError(f"tag {value!r} has an empty or space-padded part")
        return value


class Chosen(_Model):
    transcript: str | None = None


class Recording(_Model):
    schema_ref: str | None = Field(default=None, alias="$schema")
    format: Literal["recordings-archive@1"] = FORMAT
    id: str
    title: str
    recorded_at: AwareDatetime
    timezone: str
    time_source: TimeSource
    media: MediaInfo
    sources: list[SourceRef] = Field(default_factory=list)
    tags: list[TagRef] = Field(default_factory=list)
    excluded_note_types: list[str] = Field(default_factory=list)
    speakers: dict[str, str] = Field(default_factory=dict)
    chosen: Chosen = Field(default_factory=Chosen)

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if not ID_RE.fullmatch(value):
            raise ValueError(f"not a recording id: {value!r}")
        return value


def is_private(rec: Recording) -> bool:
    """Spec §7.4: private iff a tag is `private` or sits under `private/`."""
    return any(t.tag == "private" or t.tag.startswith("private/") for t in rec.tags)


def is_untagged(rec: Recording) -> bool:
    """Spec §7.1: untagged means no tags at all (notes/* tags count as tags)."""
    return not rec.tags


class Word(_Model):
    word: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class Segment(_Model):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    speaker: str | None = None
    text: str
    words: list[Word] = Field(default_factory=list)


class TranscriptPayload(_Model):
    language: str | None = None
    segments: list[Segment]


class NotesPayload(_Model):
    markdown: str


class Rendition(_Model):
    """One output file in renditions/ (extends audio-router/rendition@1, spec §6.5)."""

    schema_: Literal["recordings/rendition@1"] = Field(default=RENDITION_SCHEMA, alias="schema")
    kind: Literal["transcript", "speakers", "pick", "notes"]
    note_type: str | None = None
    engine: str
    model: str | None = None  # the model that actually answered
    version: str  # changes whenever the output would change
    created_at: AwareDatetime
    inputs: dict[str, str] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> Rendition:
        if self.kind == "notes":
            if not self.note_type:
                raise ValueError("a notes rendition needs a note_type")
            NotesPayload.model_validate(self.payload)
        elif self.note_type is not None:
            raise ValueError("only notes renditions carry a note_type")
        if self.kind == "transcript":
            TranscriptPayload.model_validate(self.payload)
        return self


def dump_json(model: BaseModel) -> str:
    """The one serialisation used for every file the archive writes: aliases, no nulls, UTF-8."""
    return model.model_dump_json(by_alias=True, exclude_none=True, indent=2) + "\n"
