"""Tag operations (tagging spec §4): pure functions of a Recording, with the privacy gate inside.

Each operation takes a Recording and returns the changed one; an unchanged result is equal to its
input, so `Writer.mutate` writes nothing. On the real archive they run through `Writer.mutate`,
under the recording's lock, with `rev` and the refusal of files edited outside the app (spec
§6.4). The Pages demo runs the same operations in memory.

This module imports only `recordings.models` and the standard library: the Pages demo can't
import sqlite3 or fcntl (stage-2a decision 6).

**Threat model.** The name rules guard against the owner's accidental input (a paste, an
autocorrect, a lookalike keyboard), not against an adversary: exotic homoglyphs beyond the
table below are out of scope. Refusing unassigned code points (category Cn) ties validation to
the runtime's Unicode version: Pyodide's older tables may refuse a newly encoded character as
invisible.

**The privacy gate.** An operation that can remove a tag raises `WouldExpose` when its result
would leave a recording that had a private tag with none, unless the recording's ID is in
`may_expose`. It decides on the Recording it is given, which under `mutate` is the file as it is
on disk, read under the lock.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

from recordings.models import (
    HIDDEN_CODEPOINTS,
    Recording,
    TagRef,
    fold,
    is_private,
    is_private_tag,
)

NOTES = "notes/"

# Why a new tag is refused (spec §4). Shown to Dan as they are; none repeats the tag.
NOT_NFC = "Use the composed form of these letters (Unicode NFC)."
INVISIBLE = "A tag can't hold invisible, control or line-separator characters."
EMPTY_PART = "A folder part is empty, or starts or ends with a space."
TOO_LONG = "Each folder part is at most 64 characters, and the whole tag at most 200."
PATH_LIKE = "A folder part can't be . or .., and a tag can't hold a backslash."
NOTE_TYPE = "Note types come from prompt files: notes/<type> must name one the archive has."
LOOKS_PRIVATE = "Did you mean `private`?"
SPACE = "Use a plain space, not another kind of space character."
TOO_MANY_MARKS = "Too many combining marks are stacked on one letter."
CASE_VARIANT = "A tag with the same letters in another case exists already."

MAX_PART = 64
MAX_TAG = 200
_REFUSED = frozenset({"Cc", "Cf", "Cn", "Zl", "Zp", "Co", "Cs"})
_NOTE_TYPE = re.compile(r"[a-z0-9-]+")
# Cyrillic and Greek letters that look like the Latin letters of "private" (spec §4).
_CONFUSABLES = str.maketrans(
    {
        "\u0440": "p",  # Cyrillic er
        "\u03c1": "p",  # Greek rho
        "\u0433": "r",  # Cyrillic ghe
        "\u0456": "i",  # Cyrillic byelorussian-ukrainian i
        "\u03b9": "i",  # Greek iota
        "\u0131": "i",  # dotless i
        "\u04cf": "i",  # Cyrillic palochka
        "\u0475": "v",  # Cyrillic izhitsa
        "\u03bd": "v",  # Greek nu
        "\u0430": "a",  # Cyrillic a
        "\u03b1": "a",  # Greek alpha
        "\u0442": "t",  # Cyrillic te
        "\u03c4": "t",  # Greek tau
        "\u0435": "e",  # Cyrillic ie
        "\u03b5": "e",  # Greek epsilon
        "\u0251": "a",  # Latin alpha
        "\u0269": "i",  # Latin iota
        "\u1d20": "v",  # small capital V
        "\u1d00": "a",  # small capital A
        "\u2228": "v",  # logical or
        "\u13e2": "p",  # Cherokee letter
        "\u13a1": "r",  # Cherokee letter
        "\u13d9": "v",  # Cherokee letter
        "\u13aa": "a",  # Cherokee letter
        "\u13a2": "t",  # Cherokee letter
        "\u13ac": "e",  # Cherokee letter
        "\u1d18": "p",  # small capital P
        "\u0280": "r",  # small capital R
        "\u026a": "i",  # small capital I
        "\u1d1b": "t",  # small capital T
        "\u1d07": "e",  # small capital E
    }
)
# Characters that read as a "/" (U+FF0F is the one NFKC folds), so a lookalike folder is seen.
_SLASHES = str.maketrans(
    dict.fromkeys(
        "\u2044\u2215\u29f8\u2571\uff0f\u27cb\u2e4a\u29f6\u2afd\u1735\u3033\u30ce\u31d3\u4e3f\u2f03"
        "\u2cc6\U0001d23a\u2041",
        "/",
    )
)
# Combining solidus overlays: they draw a slash over the letter before.
_OVERLAYS = frozenset({0x0337, 0x0338})
MAX_MARKS = 3


class WouldExpose(RuntimeError):
    """Applying this would leave a recording that had a private tag with none, and its ID
    wasn't in `may_expose`. Raised from `apply`, so `mutate` writes nothing."""

    def __init__(self, recording_id: str) -> None:
        super().__init__(recording_id)
        self.recording_id = recording_id


def _no_bare_string(value: object, what: str) -> None:
    if isinstance(value, str | TagRef):
        raise TypeError(f"{what} must be a collection, not a single {type(value).__name__}")


class Op(Protocol):
    @property
    def name(self) -> str: ...

    def apply(self, rec: Recording) -> Recording: ...


@dataclass(frozen=True)
class LostTag:
    tag: str
    by: Literal["you", "auto"]
    index: int  # where it was in the recording's tags, so Undo puts it back there


@dataclass(frozen=True)
class Effect:
    """What one write changed on one recording: what its Undo reverses (spec §4)."""

    recording_id: str
    gained: tuple[str, ...]
    lost: tuple[LostTag, ...]
    excluded_added: tuple[str, ...]
    excluded_removed: tuple[str, ...]

    @property
    def empty(self) -> bool:
        return not (self.gained or self.lost or self.excluded_added or self.excluded_removed)


def effect_of(before: Recording, after: Recording) -> Effect:
    had, has = {t.tag for t in before.tags}, {t.tag for t in after.tags}
    old, new = before.excluded_note_types, after.excluded_note_types
    return Effect(
        recording_id=before.id,
        gained=tuple(t.tag for t in after.tags if t.tag not in had),
        lost=tuple(LostTag(t.tag, t.by, i) for i, t in enumerate(before.tags) if t.tag not in has),
        excluded_added=tuple(n for n in new if n not in old),
        excluded_removed=tuple(n for n in old if n not in new),
    )


def removes_last_private(before: Recording, after: Recording) -> bool:
    """True when `before` has a private tag and `after` has none (spec §4)."""
    return is_private(before) and not is_private(after)


def _gate(before: Recording, after: Recording, may_expose: frozenset[str]) -> Recording:
    if removes_last_private(before, after) and before.id not in may_expose:
        raise WouldExpose(before.id)
    return after


def _note_type(tag: str) -> str | None:
    return tag[len(NOTES) :] if tag.startswith(NOTES) else None


def _append_tags(tags: list[TagRef], new: Iterable[TagRef]) -> list[TagRef]:
    """`tags` plus the tags of `new` it doesn't have, in order. Never touches exclusions."""
    out, have = list(tags), {t.tag for t in tags}
    for tag in new:
        if tag.tag not in have:
            out.append(tag)
            have.add(tag.tag)
    return out


@dataclass(frozen=True)
class AddTags:
    """Adds the tags the recording doesn't have. A tag already there is left as it is, `by`
    included. Adding `notes/<type>` yourself takes `<type>` out of `excluded_note_types`
    (tagging plan, decision 1)."""

    tags: tuple[TagRef, ...]
    name: str = "add-tags"

    def __post_init__(self) -> None:
        _no_bare_string(self.tags, "tags")
        object.__setattr__(self, "tags", tuple(self.tags))
        if not all(isinstance(t, TagRef) for t in self.tags):
            raise TypeError("tags must be TagRef objects")

    def apply(self, rec: Recording) -> Recording:
        tags = _append_tags(rec.tags, self.tags)
        yours = {_note_type(t.tag) for t in self.tags if t.by == "you"} - {None}
        excluded = [n for n in rec.excluded_note_types if n not in yours]
        update: dict = {"tags": tags}
        if excluded != rec.excluded_note_types:
            update["excluded_note_types"] = excluded
        if tags == rec.tags and "excluded_note_types" not in update:
            return rec
        return rec.model_copy(update=update)


@dataclass(frozen=True)
class RemoveTags:
    """Removes the named tags; a tag the recording doesn't have is skipped. Removing any
    `notes/<type>`, `auto` or `you`, adds `<type>` to `excluded_note_types`, so auto-pick never
    adds it back (main spec §7.3). The privacy gate applies (spec §4)."""

    tags: tuple[str, ...]
    may_expose: frozenset[str] = frozenset()
    name: str = "remove-tags"

    def __post_init__(self) -> None:
        _no_bare_string(self.tags, "tags")
        _no_bare_string(self.may_expose, "may_expose")
        object.__setattr__(self, "tags", tuple(self.tags))
        object.__setattr__(self, "may_expose", frozenset(self.may_expose))

    def apply(self, rec: Recording) -> Recording:
        drop = set(self.tags)
        kept = [t for t in rec.tags if t.tag not in drop]
        if len(kept) == len(rec.tags):
            return rec
        removed = [n for t in rec.tags if t.tag in drop and (n := _note_type(t.tag))]
        excluded = list(rec.excluded_note_types)
        excluded += [n for n in dict.fromkeys(removed) if n not in excluded]
        after = rec.model_copy(update={"tags": kept, "excluded_note_types": excluded})
        return _gate(rec, after, self.may_expose)


@dataclass(frozen=True)
class RestoreTags:
    """Undo's operation (spec §4): puts lost tags back at their original index with their
    original `by`, removes gained tags, and reverses the exclusion changes. The privacy gate
    applies: an Undo that takes `private` away asks first."""

    effect: Effect
    may_expose: frozenset[str] = frozenset()
    name: str = "restore-tags"

    def __post_init__(self) -> None:
        _no_bare_string(self.may_expose, "may_expose")
        object.__setattr__(self, "may_expose", frozenset(self.may_expose))

    def apply(self, rec: Recording) -> Recording:
        if rec.id != self.effect.recording_id:
            raise ValueError("this effect belongs to another recording")
        gained = set(self.effect.gained)
        tags = [t for t in rec.tags if t.tag not in gained]
        have = {t.tag for t in tags}
        for lost in sorted(self.effect.lost, key=lambda x: x.index):
            if lost.tag not in have:
                tags.insert(min(lost.index, len(tags)), TagRef(tag=lost.tag, by=lost.by))
                have.add(lost.tag)
        excluded = [n for n in rec.excluded_note_types if n not in self.effect.excluded_added]
        excluded += [n for n in self.effect.excluded_removed if n not in excluded]
        if tags == rec.tags and excluded == rec.excluded_note_types:
            return rec
        after = rec.model_copy(update={"tags": tags, "excluded_note_types": excluded})
        return _gate(rec, after, self.may_expose)


@dataclass
class Recorded:
    """Wraps an operation so the caller sees the Recording `apply` was given (before) and the one
    it returned (after), both taken under the recording's lock (spec §4). `after` is what the op
    produced, set before the write: not proof that it landed, so a caller re-reads the recording
    to confirm. `mutate` itself is
    unchanged. Its name is `<op>:<request_id>`, which `mutate` records as the revision's origin
    in state.db (spec §5, "The record")."""

    op: Op
    request_id: str
    before: Recording | None = None
    after: Recording | None = None

    @property
    def name(self) -> str:
        return f"{self.op.name}:{self.request_id}"

    def apply(self, rec: Recording) -> Recording:
        self.before = rec
        self.after = None  # an op that raises must not leave the last call's result behind
        self.after = self.op.apply(rec)
        return self.after


def _reads_as(part: str) -> str:
    """How a folder part reads, for the lookalike check: NFKC, format characters and the other
    invisible code points (HIDDEN_CODEPOINTS, as `is_private_tag` drops them) dropped,
    casefolded, accents dropped, and the confusable letters mapped to Latin ones."""
    text = fold(part)
    text = "".join(
        c for c in unicodedata.normalize("NFKD", text) if unicodedata.category(c) != "Mn"
    )
    return text.translate(_CONFUSABLES)


def _has_many_marks(text: str) -> bool:
    run = 0
    for c in text:
        run = run + 1 if unicodedata.category(c) in ("Mn", "Me") else 0
        if run > MAX_MARKS:
            return True
    return False


def is_private_lookalike(tag: str) -> bool:
    """True when the tag's first folder reads as `private` without being spelled `private` in
    plain letters, or when the app's rule (`is_private_tag`) and the archive's plain rule (the
    first folder, casefolded, is `private`) disagree on it (spec §4; tagging plan, decision 2).

    `validate_new_tag` refuses such a tag with LOOKS_PRIVATE. The tag service also asks this of a
    tag the archive already has, so a lookalike that got in by hand is never spread (Task 2's
    review, M2). Any string can be asked, whatever other rule it breaks."""
    plain_private = tag.split("/", 1)[0].casefold() == "private"
    # A slash lookalike hides a private folder: judge the first folder of the tag as it reads.
    first_as_read = tag.translate(_SLASHES).split("/", 1)[0]
    if _reads_as(first_as_read) == "private" and not plain_private:
        return True
    return is_private_tag(tag) != plain_private


def validate_new_tag(
    tag: str, *, note_types: Collection[str] = (), existing: Collection[str] = ()
) -> str | None:
    """Why a *new* tag can't be made, or None (spec §4). A tag that already exists in the archive
    isn't checked here: removing it accepts any spelling, and adding it again is refused only
    when `is_private_lookalike` says so (Task 2's review, M2)."""
    if not unicodedata.is_normalized("NFC", tag):
        return NOT_NFC
    if any(
        unicodedata.category(c) in _REFUSED or ord(c) in HIDDEN_CODEPOINTS or ord(c) in _OVERLAYS
        for c in tag
    ):
        return INVISIBLE
    parts = tag.split("/")
    if any(not p or p != p.strip() for p in parts):
        return EMPTY_PART
    if any(unicodedata.category(c) == "Zs" and c != " " for c in tag):
        return SPACE
    if _has_many_marks(tag):
        return TOO_MANY_MARKS
    if len(tag) > MAX_TAG or any(len(p) > MAX_PART for p in parts):
        return TOO_LONG
    if "\\" in tag or any(p in (".", "..") for p in parts):
        return PATH_LIKE
    if parts[0].casefold() == "notes":
        # Note types come from prompt files (main spec §7.3): the picker can't invent one.
        ok = (
            parts[0] == "notes"
            and len(parts) == 2
            and _NOTE_TYPE.fullmatch(parts[1]) is not None
            and parts[1] in note_types
        )
        if not ok:
            return NOTE_TYPE
    if is_private_lookalike(tag):
        return LOOKS_PRIVATE  # tagging plan, decision 2; the app's and the plain rule agree
    folded = tag.casefold()
    if any(other != tag and other.casefold() == folded for other in existing):
        return CASE_VARIANT
    return None
