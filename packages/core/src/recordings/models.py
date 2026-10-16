"""The archive's data model (spec §6.3, §6.5, §7.6). JSON Schemas are generated from these.

Every key added in stage 2a is optional, so files written before it read unchanged, inside
`recordings-archive@1` (§22, decision 12).
"""

from __future__ import annotations

import unicodedata
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from recordings.ids import ID_PATTERN, ID_RE

# Code points that draw nothing but are not format characters (category Cf): Hangul fillers, the
# Braille blank, variation selectors and the like. `is_private_tag` ignores them, and a new tag
# may not hold them (`tagops.validate_new_tag`).
HIDDEN_CODEPOINTS = frozenset(
    {0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x3164, 0xFFA0, 0x2800}
    | set(range(0x180B, 0x1810))
    | set(range(0xFE00, 0xFE10))
    | set(range(0xE0100, 0xE01F0))
)
FORMAT = "recordings-archive@1"
RENDITION_SCHEMA = "recordings/rendition@1"
# recording.json sits at recordings/YYYY/MM/<id>/, four levels below the archive root.
SCHEMA_REF = "../../../../schemas/recording.schema.json"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
# A person's slug (§7.6): lowercase ASCII words joined by single hyphens.
PERSON_SLUG = r"^[a-z0-9]+(-[a-z0-9]+)*$"
# The reserved person (§7.6): "not anyone known". A span set to it overrides every inferred name
# for that time, which is how "Not Alex" works for one line or a range. No real person has it.
UNKNOWN_PERSON = "unknown"

TimeSource = Literal["plaud", "metadata", "published", "mtime", "ingest"]
# Who set a recording's title (§6.3): you, or the source kind that supplied it. Stage 5 adds
# "upload" and "url".
TitleBy = Literal["you", "plaud", "recorder", "pocket"]
PersonSlug = Annotated[str, Field(pattern=PERSON_SLUG)]


def _empty(value: object) -> bool:
    # For exclude_if. The Pages demo's pydantic 2.10.5 ignores exclude_if, so the browser only
    # reads models and never serialises them (decision 7).
    return not value


class _Model(BaseModel):
    # forbid: a typo in a hand-edited file is an error, not a silently ignored key (§11).
    # hide_input_in_errors: a validation error names the field, never its value, so a title or
    # transcript text can't leak into a report, a log or an alert.
    # allow_inf_nan=False: JSON cannot carry inf, pydantic would write it as null, and the file
    # would then fail to read back.
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, hide_input_in_errors=True, allow_inf_nan=False
    )


class MediaInfo(_Model):
    file: str
    sha256: str = Field(pattern=SHA256_PATTERN)
    kind: Literal["audio", "video"]
    duration_ms: int | None = Field(default=None, ge=0)  # of the decoded media, not the source's
    sample_rate: int | None = Field(default=None, gt=0)  # captured at ingest (§9.1.1)
    channels: int | None = Field(default=None, gt=0)
    audio_file: str | None = None  # extracted audio track, video only


class SourceRef(_Model):
    kind: str  # "plaud", "recorder", "pocket", "audio_router", "url", "upload", "watched_folder"
    ref: str  # opaque, exactly as the source returned it (Plaud now sends "of_…")
    added_at: AwareDatetime
    # When the source returned this payload. Snapshots are ordered by it, then by their place in
    # `sources`, never by file name (§9.1.1). Absent means added_at.
    fetched_at: AwareDatetime | None = None
    raw: str | None = None  # e.g. "source/plaud-20261008T143000Z.json"
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)  # of the raw file's bytes


class TagRef(_Model):
    tag: str
    by: Literal["you", "auto"] = "you"

    @field_validator("tag")
    @classmethod
    def _folders_are_clean(cls, value: str) -> str:
        parts = value.split("/")
        if any(not p or p != p.strip() for p in parts):
            raise ValueError("a tag has an empty or space-padded folder part")
        return value


class Chosen(_Model):
    transcript: str | None = None


class PlaudState(_Model):
    """What Plaud last said, as far as Dan has seen it (§6.3, §6.8)."""

    title_seen: str | None = None  # the Plaud title last seen or dismissed
    acknowledged_up_to: str | None = None  # the newest snapshot whose removals Dan has reviewed


class SpeakerLabel(_Model):
    """Your decision about one diarization label (§7.6). Inferences never live in the file."""

    person: PersonSlug | None = None
    by: Literal["you"] | None = None
    plaud_name_seen: str | None = None  # the Plaud name you overrode, so a change can show
    not_: list[PersonSlug] = Field(default_factory=list, alias="not", exclude_if=_empty)

    @model_validator(mode="after")
    def _person_needs_by(self) -> SpeakerLabel:
        if (self.person is None) != (self.by is None):
            raise ValueError('a label\'s person and by go together: {"person": "…", "by": "you"}')
        return self


class SpeakerSpan(_Model):
    """A stretch of media time, in seconds, that is someone else, or `unknown` (§7.6)."""

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    person: PersonSlug
    by: Literal["you"] = "you"

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> SpeakerSpan:
        if self.end <= self.start:
            raise ValueError(f"a span must end after it starts ({self.start} to {self.end})")
        return self


class Speakers(_Model):
    """Speaker decisions (§7.6). `source` names the diarization the labels belong to."""

    source: str | None = None
    labels: dict[str, SpeakerLabel] = Field(default_factory=dict, exclude_if=_empty)
    spans: list[SpeakerSpan] = Field(default_factory=list, exclude_if=_empty)

    @model_validator(mode="after")
    def _spans_are_identified_by_their_times(self) -> Speakers:
        # §7.6: a span is identified by its (start, end), so two with the same times are
        # ambiguous. Overlaps are allowed: a narrower span overrides a wider one.
        seen = [(s.start, s.end) for s in self.spans]
        if len(seen) != len(set(seen)):
            raise ValueError("two speaker spans have the same start and end")
        return self


class Recording(_Model):
    schema_ref: str | None = Field(default=None, alias="$schema")
    format: Literal["recordings-archive@1"] = FORMAT
    id: str = Field(pattern=ID_PATTERN)  # in the JSON Schema too, for editors and agents
    rev: int | None = Field(default=None, ge=0)  # changed only by mutate (§6.4); absent means 0
    title: str
    title_by: TitleBy | None = None  # absent: not recorded; Plaud's sync treats it as "plaud"
    plaud: PlaudState | None = None
    recorded_at: AwareDatetime
    timezone: str
    time_source: TimeSource
    media: MediaInfo
    sources: list[SourceRef] = Field(default_factory=list)
    tags: list[TagRef] = Field(default_factory=list)
    excluded_note_types: list[str] = Field(default_factory=list)
    speakers: Speakers = Field(default_factory=Speakers)
    chosen: Chosen = Field(default_factory=Chosen)

    @field_validator("id", mode="before")
    @classmethod
    def _id_shape(cls, value: object) -> object:
        # Before the pattern check, so a bad id is reported as what it is meant to be.
        if not isinstance(value, str) or not ID_RE.fullmatch(value):
            raise ValueError("not a recording id")
        return value


def is_private_tag(tag: str) -> bool:
    """Spec §7.4: the tag's first folder is `private`, in any capitalisation.

    The one place the rule lives. `Private/health` is private; `privateer` and
    `notes/private` are not. It errs towards private: the tag is folded with Unicode NFKC
    (so a fullwidth spelling of the word, or a fullwidth solidus, counts) and format characters
    such as a zero-width space, and the other invisible code points in `HIDDEN_CODEPOINTS`, are
    dropped, before `casefold()` (tagging spec §4).
    """
    folded = unicodedata.normalize("NFKC", tag)
    first = folded.split("/", 1)[0]
    first = "".join(
        c for c in first if unicodedata.category(c) != "Cf" and ord(c) not in HIDDEN_CODEPOINTS
    )
    return first.casefold() == "private"


def is_private(rec: Recording) -> bool:
    """Spec §7.4: a recording is private iff any of its tags is private."""
    return any(is_private_tag(t.tag) for t in rec.tags)


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
    speaker: str | None = None  # the generic diarization label: Plaud's original_speaker (§9.1.1)
    speaker_name: str | None = None  # the name given in Plaud: a real one, or one unlike the label
    embedding_key: str | None = None  # Plaud's embeddingKey, snake_case in this schema
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
