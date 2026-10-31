"""Reconcile (spec §9.1.1): every Plaud snapshot in fetch order goes in; Plaud's outputs and the
fields Plaud owns come out.

A pure function. The import (stage 2a) and the sync (stage 2b) both call it, so they write
identical Plaud outputs, and running it again writes nothing new.

- **Outputs:** one transcript output per change in what it carries (its segments, untimed count,
  last segment's end and diarization keys), and one notes output per change in each tab's
  Markdown. Each names its snapshot (`inputs.source`) and the hash of what it carries
  (`inputs.part_sha256`), so a difference the output can't show is never a new write-once file.
  That hash isn't the Sync screen's part hash: stage 2b compares snapshots with `part_hashes`,
  never with this. `version` is `plaud@<normaliser version>`. `created_at` is the snapshot's fetch
  time, so a re-run writes byte-identical files.
- **An output's identity** is its kind, note type, snapshot, part hash and `version`. An output
  of another version doesn't count, so a normaliser bump writes every part again (§6.5). The skip
  check (`_identity`) keys on `version` and `part_sha256` together, never the hash alone.
- **A diarization fingerprint of `None`** (a recording with only `transaction_polish`) is stored
  as `meta.diarization: null`. Stage 2b's comparison must read it as a new diarization, never as
  "unchanged". Nothing in this task compares fingerprints.
- **Hashed normalised, rendered raw:** a note's hash is of the Markdown its normalised block
  renders to, so a rotating signature is never a change. Its Markdown comes from the raw block,
  so a link and a highlight's picture link stay as Plaud wrote them.
- **Removals wait for review:** a tab or transcript that vanishes, or names that revert to
  "Speaker N" on an unchanged diarization. They are held while their snapshot is newer than
  `plaud.acknowledged_up_to`. One that comes back as it was writes nothing, even once accepted.
- **The title:** Plaud owns it only while `title_by` is `plaud` or absent. Any other value
  (`you`, or another source's kind) keeps the title, and Plaud's shows as a difference until it
  is dismissed.
- **Auto-private:** on the first reconcile, and again whenever a Plaud ID's newest title isn't
  the one last seen, every title any snapshot carries, under either Plaud ID and degraded
  snapshots included, is matched against the patterns, with Plaud's "MM-DD " prefix stripped.
  A match adds `private` and never removes it, and a tag Dan removed stays removed until a
  title changes. The Writer applies it before any output.
- **Skipped snapshots:** one with link errors, or one that isn't a JSON object, is skipped and
  reported. Nothing is ever built from it.
- **Two Plaud IDs on one recording** are reconciled separately. The title follows the ID whose
  snapshot comes first in the sequence given (the recording's `sources` order).
- **Not yet:** people files (stage 3b). Plaud's names stay as `speaker_name`, and linking them to
  people comes later.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from recordings.config import ConfigError
from recordings.models import Recording, Rendition, is_private
from recordings.plaud.blocks import render_note
from recordings.plaud.normalise import (
    GENERIC_SPEAKER,
    NORMALISER_VERSION,
    diarization_fingerprint,
    diarization_key,
    is_complete,
    normalise,
    note_blocks,
    sha256_of,
    transcript_rows,
)
from recordings.refs import canonical_plaud_id

PLAUD_VERSION = f"plaud@{NORMALISER_VERSION}"
TIMING_RULE = "timing@1"
TIMING_TOLERANCE_MS = 1000
# audio-router's hold patterns (§7.4): a title starting "private", or containing "interview:".
DEFAULT_AUTO_PRIVATE = (r"^\s*private", r"interview:")
# Plaud starts every AI title with "MM-DD " ("10-06 Private session"). audio-router's
# normalize_title strips it before matching its holds (gate.py at 64612df); without that, an
# anchored pattern matches no real title.
TITLE_DATE_PREFIX = re.compile(r"^\s*\d{1,2}-\d{1,2}[\s-]+")


@dataclass(frozen=True)
class Snapshot:
    path: str  # "source/plaud-<stamp>.json", relative to the recording's folder
    ref: str  # the Plaud ID, as recorded
    fetched_at: datetime
    envelope: Any  # the parsed payload; anything but a dict is skipped


@dataclass(frozen=True)
class Held:
    snapshot: str
    slot: str  # "transcript" or "notes:<note type>"
    reason: str  # "notes removed", "transcript removed" or "names removed"


@dataclass(frozen=True)
class Reconciled:
    outputs: tuple[Rendition, ...] = ()
    title: str | None = None  # set recording.title to this
    title_seen: str | None = None  # set plaud.title_seen to this
    title_difference: str | None = None  # Plaud's title, when the title isn't Plaud's to set
    add_private: bool = False
    held: tuple[Held, ...] = ()
    skipped: tuple[str, ...] = ()


def patterns_from_config(data: dict) -> tuple[str, ...]:
    section = data.get("plaud", {})
    if not isinstance(section, dict):
        raise ConfigError("[plaud] must be a table (auto_private_patterns is read from it)")
    value = section.get("auto_private_patterns", list(DEFAULT_AUTO_PRIVATE))
    if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
        raise ConfigError("[plaud] auto_private_patterns must be a list of regular expressions")
    for position, pattern in enumerate(value, start=1):
        try:
            re.compile(pattern)
        except re.error:
            # A pattern can be a name Dan wants private: never quoted, and no re.error text.
            raise ConfigError(
                f"[plaud] auto_private_patterns: pattern {position} is not a regular expression"
            ) from None
    return tuple(value)


def strip_date_prefix(title: str) -> str:
    return TITLE_DATE_PREFIX.sub("", title, count=1).strip()


def matches_auto_private(
    titles: Iterable[str], patterns: Sequence[str] = DEFAULT_AUTO_PRIVATE
) -> bool:
    """Whether any of these Plaud titles matches an auto-private pattern (§7.4). Each pattern is
    searched, ignoring case, in the title as given and with its "MM-DD " prefix stripped; trying
    both errs towards private. The import calls it to tag a recording when it is created, and
    reconcile on every snapshot's title."""
    return any(
        re.search(pattern, text, re.IGNORECASE)
        for title in titles
        for text in (title, strip_date_prefix(title))
        for pattern in patterns
    )


def _number(value: Any) -> bool:
    """A usable number: not a bool, not Infinity or NaN, and an integer small enough to be a
    millisecond count (a huge one would overflow the arithmetic below)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return abs(value) < 2**53
    return isinstance(value, float) and math.isfinite(value) and abs(value) < 2**53


def timing_check(envelope: dict, rows: list[dict], media_ms: int | None) -> dict:
    """§9.1.1: Plaud's duration and its last segment's end against the decoded media. A mismatch
    means a trim in Plaud (audio-router found timings at up to 135% of the file)."""
    out: dict[str, Any] = {"rule": TIMING_RULE}
    if _number(envelope.get("duration")):
        out["plaud_duration_ms"] = int(envelope["duration"])
    ends = [r["end_time"] for r in rows if _number(r.get("end_time"))]
    if ends:
        out["last_segment_end_ms"] = int(max(ends))
    if media_ms is not None:
        out["media_duration_ms"] = media_ms
        tolerance = max(TIMING_TOLERANCE_MS, media_ms // 100)
        checks = []
        if "plaud_duration_ms" in out:
            checks.append(abs(out["plaud_duration_ms"] - media_ms) <= tolerance)
        if "last_segment_end_ms" in out:  # a trim shows here even when Plaud gives no duration
            checks.append(out["last_segment_end_ms"] <= media_ms + tolerance)
        if checks:
            out["ok"] = all(checks)
    return out


def _named(row: dict) -> bool:
    name = str(row.get("speaker") or "").strip()
    return bool(name) and not GENERIC_SPEAKER.match(name)


def _names_removed(old: list[dict], new: list[dict]) -> bool:
    """Same diarization (the fingerprint's own per-segment key), and a name that was there has
    gone back to a generic label."""
    if [diarization_key(r) for r in old] != [diarization_key(r) for r in new]:
        return False
    return any(_named(a) and not _named(b) for a, b in zip(old, new, strict=True))


def _title_of(envelope: Any) -> str | None:
    name = envelope.get("name") if isinstance(envelope, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


def _segments(rows: list[dict]) -> tuple[list[dict], int]:
    """The segments a transcript output carries, and how many rows had no usable timing."""
    segments, untimed, previous_end = [], 0, 0.0
    for row in rows:
        start_ms, end_ms = row.get("start_time"), row.get("end_time")
        if _number(start_ms) and _number(end_ms) and 0 <= start_ms <= end_ms:
            start, end = start_ms / 1000, end_ms / 1000
        else:
            untimed += 1
            start = end = previous_end
        previous_end = end
        name = str(row.get("speaker") or "").strip()
        label = str(row.get("original_speaker") or "").strip() or name
        segment: dict[str, Any] = {
            "start": start,
            "end": end,
            "text": str(row.get("content") or "").strip(),
        }
        if label:
            segment["speaker"] = label
        if name and (name != label or _named(row)):  # a real name is kept even without a label
            segment["speaker_name"] = name
        key = str(row.get("embeddingKey") or "").strip()
        if key:
            segment["embedding_key"] = key
        segments.append(segment)
    return segments, untimed


def _slots(envelope: dict) -> dict[str, tuple[str, Any]]:
    """Each part reconcile writes, as (hash, value). The transcript's value is its normalised
    rows. A note's value is its raw block, which is what gets rendered. Each hash covers only
    what its output carries, never where it came from."""
    out: dict[str, tuple[str, Any]] = {}
    rows = transcript_rows(normalise(envelope))
    if rows:
        # Outputs are write-once, so a difference the output can't show is never a new one:
        # 1500.0 for 1500, a trailing space, an empty embeddingKey for none, an unknown row
        # field. The hash is of the segments, the untimed count, the last segment's end and
        # the diarization keys: everything the output takes from the rows.
        segments, untimed = _segments(rows)
        last_end = timing_check({}, rows, None).get("last_segment_end_ms")
        out["transcript"] = (
            sha256_of([segments, untimed, last_end, [diarization_key(r) for r in rows]]),
            rows,
        )
    for note_type, block, raw in note_blocks(envelope):
        if render_note(raw):
            # Rendered from the normalised block, so a rotating signature is never a change,
            # and neither is a field the Markdown doesn't show (data_title, data_id, an unknown
            # field) nor how a JSON tab's text is laid out.
            digest = sha256_of([render_note(block), str(block.get("data_type") or "")])
            out[f"notes:{note_type}"] = (digest, raw)
    return out


def _transcript(
    rows: list[dict], digest: str, snap: Snapshot, env: dict, rec: Recording
) -> Rendition:
    segments, untimed = _segments(rows)
    meta: dict[str, Any] = {
        "diarization": diarization_fingerprint(env),
        "timing": timing_check(env, rows, rec.media.duration_ms),
    }
    if untimed:
        meta["untimed_segments"] = untimed
    return Rendition(
        kind="transcript",
        engine="plaud",
        model="plaud",
        version=PLAUD_VERSION,
        created_at=snap.fetched_at,
        inputs={"source": snap.path, "part_sha256": digest},
        meta=meta,
        payload={"segments": segments},
    )


def _notes(note_type: str, block: dict, digest: str, snap: Snapshot) -> Rendition:
    return Rendition(
        kind="notes",
        note_type=note_type,
        engine="plaud",
        model="plaud",
        version=PLAUD_VERSION,
        created_at=snap.fetched_at,
        inputs={"source": snap.path, "part_sha256": digest},
        meta={"data_type": str(block.get("data_type") or "")},
        payload={"markdown": render_note(block)},
    )


def _identity(r: Rendition) -> tuple:
    return (r.kind, r.note_type, r.inputs.get("source"), r.inputs.get("part_sha256"), r.version)


def reconcile(
    rec: Recording,
    snapshots: Sequence[Snapshot],
    existing: Sequence[Rendition],
    *,
    auto_private: Sequence[str] = DEFAULT_AUTO_PRIVATE,
) -> Reconciled:
    ordered = [s for _, s in sorted(enumerate(snapshots), key=lambda p: (p[1].fetched_at, p[0]))]
    ack = rec.plaud.acknowledged_up_to if rec.plaud else None
    ack_position = max((i for i, s in enumerate(ordered) if s.path == ack), default=-1)
    groups: dict[str, list[tuple[int, Snapshot]]] = {}
    for position, snapshot in enumerate(ordered):
        groups.setdefault(canonical_plaud_id(snapshot.ref), []).append((position, snapshot))
    have = {_identity(r) for r in existing if r.engine == "plaud"}
    outputs: list[Rendition] = []
    held: dict[tuple[str, str], Held] = {}
    skipped: list[str] = []
    names: dict[str, str] = {}
    for ref, members in groups.items():
        applied: dict[str, tuple[str, Any]] = {}
        gone: dict[str, tuple[str, Any]] = {}  # removals accepted: nothing was written for them
        for position, snapshot in members:
            if not isinstance(snapshot.envelope, dict) or not is_complete(snapshot.envelope):
                skipped.append(snapshot.path)
                continue
            env = normalise(snapshot.envelope)
            reviewed = position <= ack_position
            name = _title_of(snapshot.envelope)
            if name is not None:
                names[ref] = name
            current = _slots(snapshot.envelope)
            for slot in sorted(set(applied) | set(current)):
                if slot not in current:
                    if reviewed:
                        gone[slot] = applied.pop(slot)
                    else:
                        reason = "transcript removed" if slot == "transcript" else "notes removed"
                        held[(ref, slot)] = Held(snapshot.path, slot, reason)
                    continue
                digest, value = current[slot]
                if slot in applied and applied[slot][0] == digest:
                    held.pop((ref, slot), None)
                    continue
                if slot not in applied and slot in gone and gone[slot][0] == digest:
                    applied[slot] = gone.pop(slot)  # back as it was: its output is already there
                    continue
                if (
                    slot == "transcript"
                    and slot in applied
                    and not reviewed
                    and _names_removed(applied[slot][1], value)
                ):
                    held[(ref, slot)] = Held(snapshot.path, slot, "names removed")
                    continue
                held.pop((ref, slot), None)
                applied[slot] = (digest, value)
                output = (
                    _transcript(value, digest, snapshot, env, rec)
                    if slot == "transcript"
                    else _notes(slot.removeprefix("notes:"), value, digest, snapshot)
                )
                if _identity(output) not in have:
                    have.add(_identity(output))
                    outputs.append(output)

    title = title_seen = difference = None
    primary = canonical_plaud_id(snapshots[0].ref) if snapshots else None
    plaud_title = names.get(primary) if primary is not None else None
    seen = rec.plaud.title_seen if rec.plaud else None
    if plaud_title is not None:
        if rec.title_by in (None, "plaud"):
            # Only a change on Plaud's side moves the title: one Dan edited by hand stays.
            if plaud_title != seen and plaud_title != rec.title:
                title = plaud_title
            if plaud_title != seen:
                title_seen = plaud_title
        elif plaud_title != seen and plaud_title != rec.title:
            difference = plaud_title
    # §7.4: the patterns run on the first reconcile, and again whenever a Plaud ID's newest title
    # (a degraded snapshot's included) isn't the one last seen, so a private tag Dan removed
    # isn't put back on every reindex. When they run, privacy errs towards private: every title
    # any snapshot carries, under either Plaud ID, titles Plaud has since changed included.
    titles, newest = [], {}
    for snapshot in ordered:
        if (name := _title_of(snapshot.envelope)) is not None:
            titles.append(name)
            newest[canonical_plaud_id(snapshot.ref)] = name
    due = seen is None or any(name != seen for name in newest.values())
    add_private = due and not is_private(rec) and matches_auto_private(titles, auto_private)
    return Reconciled(
        outputs=tuple(outputs),
        title=title,
        title_seen=title_seen,
        title_difference=difference,
        add_private=add_private,
        held=tuple(held.values()),
        skipped=tuple(skipped),
    )
