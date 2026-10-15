"""Importing audio-router's archive (spec §9.3): read-only, re-runnable and driven by its catalog.

The source is a read-only copy of audio-router's media root (§19), laid out as its
archive/store.py and scripts/build_catalog.py describe (at commit 64612df):

    <source>/raw/<file_id>/<UTC stamp>[-NN].json   snapshots (plaud, recorder)
    <source>/audio/<YYYY>/<MM>/<file_id>.<ext>
    <source>/derived/<file_id>/<kind>-<engine>-<version>-<UTC stamp>.json
    pocket/raw/<id>.json, pocket/audio/…/<id>.<ext>   flat; a closed corpus
    private/…                                         the private tier: its files keep their
                                                      export names, and catalog/private.jsonl
                                                      registers each by its audio's SHA-256
    catalog/catalog.csv, catalog/provenance.json      catalog@5

`catalog/catalog.csv` is the checklist: every row gets exactly one disposition, so the dry run
accounts for every row. Nothing here writes to the source, and no report names a title: the
catalog holds none, and this module never puts one in a reason. Dan's consent list (§9.1)
excludes the recording, not only its ID: a row on it, and every row that duplicates it or carries
its audio, is never read past its ID and is reported only as a count.
"""

from __future__ import annotations

import csv
import errno
import hashlib
import json
import logging
import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Collection, Iterable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from recordings.archive import RawSource, sha256_file
from recordings.disk import STOP_FREE_PERCENT, DiskStatus
from recordings.index import Index, IndexRefused
from recordings.locks import OPS_LOCK, Locks, LockTimeout, announce
from recordings.media import MEDIA_SUFFIXES, MediaProbe, media_kind, probe
from recordings.models import Rendition, TagRef, is_private_tag
from recordings.plaud.normalise import link_errors, meta_fetched_at
from recordings.plaud.reconcile import matches_auto_private
from recordings.refs import canonical_plaud_id
from recordings.sources.ar_outputs import ar_stamp, map_rendition, pocket_outputs
from recordings.writer import Incoming, IndexUnavailable, NotTheWriter, Writer, WriterError

CATALOG = Path("catalog") / "catalog.csv"
PROVENANCE = Path("catalog") / "provenance.json"
REQUIRED_COLUMNS = (
    "uri",
    "source",
    "file_id",
    "recorded_at_local",
    "timezone",
    "time_source",
    "audio_rel",
    "audio_sha256",
    "dup_of",
    "tags",
    "access",
)
SOURCES = ("plaud", "recorder", "pocket")
# audio-router's time_source, per source, as recordings' (§6.2); anything else is unplaced.
TIME_SOURCES = {
    ("plaud", "start_at"): "plaud",
    ("plaud", "created_at"): "plaud",
    ("recorder", "filename"): "metadata",
    ("recorder", "file mtime (inferred)"): "mtime",
    ("pocket", "recording_at"): "metadata",
    ("pocket", "created_at"): "ingest",
    ("private", "filename"): "metadata",
}
DISPOSITIONS = (
    "new",
    "new-private",
    "duplicate",
    "present",
    "no-audio",
    "deferred-to-2b",
    "consent-excluded",
    "unplaced",
)
IMPORTED = ("new", "new-private", "duplicate", "present")  # the dispositions a run imports
# audio-router's privacy labels: the protected markers in its sources/local_.py at 64612df
# (PROTECTED_SEGMENTS, and the markers it checks in file names only), and the one tag each
# becomes (Dan, 2026-10-09; spec §7.4, §9.3). Every marker is recognised, so a recording carrying
# any of them is private. The tags are trimmed to one per meaning: therapy and both spellings of
# counselling are one tag, and the generic markers are plain `private`.
PRIVATE_TAGS: dict[str, str] = {
    "private": "private",
    "personal": "private",
    "voiceprints": "private",  # audio-router's marker for voice data, not a topic
    "therapy": "private/therapy",
    "counselling": "private/therapy",  # Dan: therapy and counselling are the same
    "counseling": "private/therapy",
    "journal": "private/journal",
    "medical": "private/medical",
    "students": "private/students",
}
PRIVACY_LABELS = frozenset(PRIVATE_TAGS)
_TAG = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")  # audio-router's rule for catalog/tags.jsonl
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
# A row the index can't answer for now is unplaced with this reason, and plan() makes it a
# problem too, so not even --allow-unplaced imports past a busy index (§6.4).
INDEX_BUSY = (
    "the index can't answer now (busy, or an I/O error), so whether this archive holds "
    "it is unknown: run the import again"
)


class CatalogError(RuntimeError):
    """The source has no usable catalog, so nothing can be accounted for."""


@dataclass(frozen=True)
class Row:
    uri: str
    source: str
    file_id: str
    recorded_at_local: str
    timezone: str
    time_source: str
    audio_rel: str
    audio_sha256: str
    dup_of: str
    tags: tuple[str, ...]  # audio-router's authored labels (catalog/tags.jsonl)
    access: str


@dataclass(frozen=True)
class Item:
    row: Row
    disposition: str
    reason: str = ""
    kind: str = ""  # the recording's origin: plaud, recorder or pocket
    audio: Path | None = None
    sha256: str = ""
    recorded_at: datetime | None = None
    timezone: str = ""
    time_source: str = ""
    snapshots: tuple[Path, ...] = ()
    renditions: tuple[Path, ...] = ()
    texts: tuple[Path, ...] = ()  # prose exports kept as they came: the private tier's .txt
    present_id: str | None = None
    private: bool = False  # its own labels make it private (decision 23)
    tags: tuple[str, ...] = ()  # recordings tags, privacy labels already under private/; a row
    # deferred to 2b, or with no audio, keeps its own too, for its same-audio group
    group_privacy: tuple[str, ...] = ()  # for a row to import: the privacy tags of every row in
    # its same-audio group, imported or not (§7.4); its recording is created with them
    probed: MediaProbe | None = None  # None: not measured (it can't be, or it is present)
    snapshot_problems: tuple[tuple[str, ...], ...] = ()  # per Plaud snapshot, why reconcile
    # will skip it; () when it is usable (§9.1.1: nothing is built from an incomplete one)


@dataclass(frozen=True)
class Plan:
    root: Path
    items: tuple[Item, ...]
    ledger_only: tuple[str, ...] = ()
    unlisted: tuple[str, ...] = ()
    held_on_plaud: int | None = None
    problems: tuple[str, ...] = ()
    unlisted_private: int = 0  # private-tier media no catalog row names: a count, never a name

    def counts(self) -> dict[str, int]:
        found = Counter(i.disposition for i in self.items)
        return {d: found.get(d, 0) for d in DISPOSITIONS}

    def unprobed(self) -> list[str]:
        """Rows to import whose media couldn't be measured: a real run refuses them all. A
        `present` row isn't measured again: its recording holds the decoded duration of the same
        bytes (§9.1.1), so a re-run decodes only what it would create."""
        return [
            i.row.uri
            for i in self.items
            if i.disposition in IMPORTED and i.disposition != "present" and i.probed is None
        ]

    def incomplete_snapshots(self) -> dict:
        """Plaud snapshots reconcile will skip, counted by reason (a snapshot with two reasons
        counts under both), for rows a run imports."""
        bad = [
            reasons
            for i in self.items
            if i.disposition in IMPORTED
            for reasons in i.snapshot_problems
            if reasons
        ]
        by_reason = Counter(r for reasons in bad for r in set(reasons))
        return {"total": len(bad), "by_reason": dict(sorted(by_reason.items()))}

    def all_snapshots_incomplete(self) -> list[str]:
        """Rows whose every snapshot is incomplete: they would get no Plaud outputs at all."""
        return [
            i.row.uri
            for i in self.items
            if i.disposition in IMPORTED and i.snapshot_problems and all(i.snapshot_problems)
        ]

    def offset_only(self) -> list[str]:
        """Rows to import whose time keeps its offset but no zone name, because the zone the
        catalog gives contradicts it: reported, not refused, and fixable in recording.json."""
        return [
            i.row.uri
            for i in self.items
            if i.disposition in IMPORTED and i.recorded_at is not None and not i.timezone
        ]

    def zones(self) -> dict[str, dict[str, int]]:
        """Rows to import by source and zone name ("" for offset only). audio-router stamps a
        Recorder, Pocket or private row with the zone its config named when its index or catalog
        was built, not where it was recorded: a zone wrong for a whole source shows here."""
        found = Counter(
            (i.row.source, i.timezone)
            for i in self.items
            if i.disposition in IMPORTED and i.recorded_at is not None
        )
        out: dict[str, dict[str, int]] = {}
        for (source, zone), n in sorted(found.items()):
            out.setdefault(source, {})[zone] = n
        return out

    @property
    def ok(self) -> bool:
        return (
            not self.problems
            and not self.unprobed()
            and not any(i.disposition == "unplaced" for i in self.items)
        )

    def report(self) -> dict:
        """Counts, URIs and IDs: never a title or a transcript, and never a consent-listed ID."""
        return {
            "catalog_rows": len(self.items),
            "by_disposition": self.counts(),
            # Rows to import whose recording will be private: by their own labels or their group's.
            "private": sum(
                bool(i.private or i.group_privacy) for i in self.items if i.disposition in IMPORTED
            ),
            "unplaced": [
                {"uri": i.row.uri, "reason": i.reason}
                for i in self.items
                if i.disposition == "unplaced"
            ],
            "no_audio": [i.row.uri for i in self.items if i.disposition == "no-audio"],
            # Plaud IDs, so stage 2b's sync can match them (Dan, 2026-10-09).
            "deferred_to_2b": [
                i.row.file_id for i in self.items if i.disposition == "deferred-to-2b"
            ],
            "unprobed": self.unprobed(),
            "incomplete_snapshots": self.incomplete_snapshots(),
            "all_snapshots_incomplete": self.all_snapshots_incomplete(),
            "offset_only": self.offset_only(),
            "zones": self.zones(),
            "ledger_only": list(self.ledger_only),
            "unlisted": list(self.unlisted),
            "unlisted_private": self.unlisted_private,
            "held_on_plaud": self.held_on_plaud,
            "problems": list(self.problems),
        }


def map_tags(labels: Iterable[str]) -> tuple[str, ...]:
    """audio-router's tags as recordings tags (Dan, 2026-10-09). Every label comes over, and a
    privacy label becomes its one tag in PRIVATE_TAGS: `therapy` and both spellings of
    counselling become `private/therapy`, and `personal` becomes `private`."""
    out: list[str] = []
    for label in labels:
        tag = PRIVATE_TAGS.get(label.lower(), label)  # any capitalisation, as is_private_tag
        if tag not in out:
            out.append(tag)
    return tuple(out)


def _row(cells: dict) -> Row:
    values = {c: (cells.get(c) or "").strip() for c in REQUIRED_COLUMNS}
    labels = tuple(t.strip() for t in values.pop("tags").split(";") if t.strip())
    return Row(**values, tags=labels)


def read_catalog(root: Path) -> list[Row]:
    path = Path(root) / CATALOG
    if not path.is_file():
        raise CatalogError(
            f"{path}: catalog.csv is missing. The import is driven by audio-router's catalog: "
            "build it there with scripts/build_catalog.py, then copy the archive again."
        )
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise CatalogError(
                f"{path} is not audio-router's catalog@5: missing columns " + ", ".join(missing)
            )
        rows = [_row(r) for r in reader]
    repeated = sorted(u for u, n in Counter(r.uri for r in rows).items() if n > 1)
    if repeated:
        raise CatalogError(f"{path} lists {repeated[0]} more than once")
    return rows


def snapshot_order(path: Path) -> tuple[str, str]:
    """audio-router's store._order_key: (stamp, suffix), so `x.json` comes before `x-01.json`."""
    stamp, _, suffix = path.stem.partition("-")
    return (stamp, suffix)


def _provenance(root: Path) -> tuple[dict, list[str]]:
    path = root / PROVENANCE
    if not path.is_file():
        return {}, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}, [f"{path} could not be read"]
    return (data if isinstance(data, dict) else {}), []


def _inside(root: Path, rel: str) -> Path | None:
    path = root / rel
    try:
        return path if path.resolve().is_relative_to(root.resolve()) else None
    except (OSError, ValueError):
        return None


def _all_inside(root: Path, paths: Iterable[Path]) -> bool:
    """Every path resolves inside the source: the plan never returns a file from outside it,
    because the import publishes those bytes into write-once `source/`."""
    try:
        base = root.resolve()
        return all(p.resolve().is_relative_to(base) for p in paths)
    except (OSError, ValueError, RuntimeError):
        return False


def _when(row: Row, default_tz: str) -> tuple[datetime | None, str, str, str]:
    """(recorded_at, its zone's name, recordings' time_source, a reason when unplaced).

    audio-router writes a main-tier time in the zone its config named then, with that zone's
    offset at that instant (plaud.stamp_meta, index._local_from_start_at, build_catalog's
    _local_iso), so its offset and zone agree, a trip's included: both are kept as they are. A
    naive time is wall-clock in the row's zone, or the default when the row names none. An
    offset its zone contradicts keeps its offset, the clock the source wrote, and gets no zone
    name: build_catalog gives every private.jsonl row its config's zone, whatever the
    hand-written time says. `Plan.offset_only()` lists them."""
    time_source = TIME_SOURCES.get((row.source, row.time_source))
    if time_source is None:
        return None, "", "", f"unknown time source {row.time_source!r} for {row.source}"
    if not row.recorded_at_local:
        return None, "", "", "no recording time in the catalog"
    try:
        when = datetime.fromisoformat(row.recorded_at_local)
    except ValueError:
        return None, "", "", f"recording time {row.recorded_at_local!r} isn't ISO 8601"
    name = row.timezone or default_tz
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None, "", "", f"unknown time zone {name!r}"
    if when.utcoffset() is None:
        when = when.replace(tzinfo=zone)
    elif when.utcoffset() != when.astimezone(zone).utcoffset():
        name = ""
    if when.utcoffset().total_seconds() % 60:
        return (
            None,
            "",
            "",
            (
                f"recording time {row.recorded_at_local!r} has an offset in "
                "seconds, which a recording ID can't carry"
            ),
        )
    return when, name, time_source, ""


def _json_files(folder: Path) -> list[Path]:
    """The `.json` files in one store folder, as audio-router's `glob("*.json")` finds them. A
    folder that isn't there has none. One that is there but can't be listed raises, so its row is
    unplaced instead of imported without its snapshots or outputs: on Python 3.13+ `Path.glob`
    skips a folder it can't list without a word, and an empty answer looks like a recording
    with no transcript."""
    try:
        entries = list(folder.iterdir())
    except FileNotFoundError:
        return []
    return [p for p in entries if p.name.endswith(".json")]


def _walk(top: Path) -> tuple[list[Path], int]:
    """Every file under `top`, sorted, and how many folders couldn't be listed. `Path.rglob`
    skips those without a word on Python 3.13+, so a recording in one would look absent instead
    of unreadable. Symlinked folders aren't followed, as `rglob` doesn't follow them."""
    errors: list[OSError] = []
    files = [
        folder / name for folder, _, names in top.walk(on_error=errors.append) for name in names
    ]
    return sorted(files), len(errors)


def _store_files(root: Path, source: str, fid: str) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    if source == "pocket":
        flat = root / "pocket" / "raw" / f"{fid}.json"
        try:
            flat.stat()  # raises when it can't be reached; is_file() would answer False
        except FileNotFoundError:
            return (), ()
        return (flat,), ()
    raw = sorted(_json_files(root / source / "raw" / fid), key=snapshot_order)
    derived = sorted(_json_files(root / source / "derived" / fid))
    return tuple(raw), tuple(derived)


def _kind_from_id(fid: str) -> str:
    if _HEX32.fullmatch(fid):
        return "plaud"
    if _HEX64.fullmatch(fid):
        return "recorder"  # a Recorder export, registered by its audio's SHA-256
    if _UUID.fullmatch(fid):
        return "pocket"
    return ""


def _private_media(root: Path, consent: frozenset[str]) -> tuple[dict[str, list[Path]], int]:
    """The private tier's media files by SHA-256, hashed once per plan, and how many files or
    folders couldn't be read or sit outside the source. Its files keep their export names, so a
    hash is the reliable way to find one. A file named by a consent-listed ID is skipped before
    it is opened (§9.1)."""
    found: dict[str, list[Path]] = {}
    skipped = 0
    tier = root / "private"
    if tier.is_dir():
        paths, skipped = _walk(tier)  # a folder that can't be listed counts as one
        for path in paths:
            if (
                path.is_file()
                and not path.name.startswith(".")
                and path.suffix.lower() in MEDIA_SUFFIXES
                and canonical_plaud_id(path.stem) not in consent
            ):
                try:
                    if not _all_inside(root, [path]):
                        skipped += 1
                        continue
                    found.setdefault(sha256_file(path), []).append(path)
                except OSError:
                    skipped += 1  # counted, never named: a file name here is an export title
    return found, skipped


def _find_private(root: Path, fid: str, media_by_sha: dict[str, list[Path]]):
    """One private row's files: the one media file whose SHA-256 is its file ID (or, for an ID
    that isn't a hash, whose name is), and any `.txt` with the same stem beside it, which is
    Recorder's prose transcript. Returns (kind, media, texts), or a reason string."""
    if not (root / "private").is_dir():
        return "the catalog lists a private recording but the copy has no private tier"
    media = set(media_by_sha.get(fid, ()))
    media |= {p for paths in media_by_sha.values() for p in paths if p.stem == fid}
    if len(media) != 1:
        return f"{len(media)} media files in the private tier match {fid} (expected 1)"
    (found,) = media
    kind = _kind_from_id(fid)
    if not kind:
        return f"can't tell which source private recording {fid} came from"
    texts = tuple(
        sorted(
            p
            for p in found.parent.iterdir()
            if p.is_file() and p.stem == found.stem and p.suffix.lower() == ".txt"
        )
    )
    return kind, found, texts


def _snapshot_problems(kind: str, snapshots: tuple[Path, ...]) -> tuple[tuple[str, ...], ...]:
    """For a Plaud recording, per snapshot, the reasons reconcile will skip it: `unreadable` when
    it isn't a JSON object, else the data type of each block whose content didn't arrive (and
    `legacy`). Reasons are a fixed vocabulary: anything that isn't a short slug is `other`, so no
    title can reach a report."""
    if kind != "plaud":
        return ()
    out = []
    for path in snapshots:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            doc = None
        reasons = ["unreadable"] if not isinstance(doc, dict) else link_errors(doc)
        out.append(tuple(r if re.fullmatch(r"[a-z_]{1,40}", r) else "other" for r in reasons))
    return tuple(out)


def _present(index: Index | None, kind: str, row: Row, sha: str) -> tuple[str | None, str]:
    """Already in this archive? By Plaud ID (either form) first, then audio-router's own
    reference, then the content hash (§9.3). The lookups are strict: an index that can't answer
    now raises sqlite3.DatabaseError rather than say "not here" (§6.4)."""
    if index is None or not index.exists():
        return None, ""
    if kind == "plaud":
        for rid in index.find_by_source("plaud", row.file_id, strict=True):
            if index.sha256_of(rid, strict=True) != sha:
                return None, (
                    f"Plaud ID {row.file_id} is already in this archive ({rid}) "
                    "with different audio"
                )
            return rid, ""
    for rid in index.find_by_source("audio_router", row.uri, strict=True):
        # The same source row with other bytes: never a second copy.
        if index.sha256_of(rid, strict=True) != sha:
            return None, f"{row.uri} is already in this archive ({rid}) with different audio"
        return rid, ""
    return index.find_by_sha256(sha, strict=True), ""


def _consent_closure(
    rows: list[Row], consent: frozenset[str]
) -> tuple[frozenset[str], frozenset[str]]:
    """The recordings Dan's consent list excludes (§9.1, §9.3): the list excludes the recording,
    not only its ID. Returns (URIs, audio SHA-256s), closed over catalog data alone, so no
    excluded row's files are read. A row is excluded when its own ID is listed, when its `dup_of`
    names an excluded row, when an excluded row's `dup_of` names it, or when its audio is an
    excluded row's (a private row's file ID is its audio's SHA-256)."""
    by_uri = {r.uri: r for r in rows}
    uris = {r.uri for r in rows if canonical_plaud_id(r.file_id) in consent}
    shas: set[str] = set()

    def audio_of(r: Row) -> set[str]:
        return {r.audio_sha256} - {""} | ({r.file_id} if r.source == "private" else set())

    changed = True
    while changed:
        changed = False
        shas |= {s for u in uris for s in audio_of(by_uri[u])}
        named = {by_uri[u].dup_of for u in uris}
        for r in rows:
            if r.uri not in uris and (r.dup_of in uris or r.uri in named or audio_of(r) & shas):
                uris.add(r.uri)
                changed = True
    return frozenset(uris), frozenset(shas)


def _consent_kept(
    index: Index | None, rows: list[Row], excluded: tuple[frozenset[str], frozenset[str]]
) -> int:
    """How many recordings this archive already holds that the consent list now excludes: an ID
    listed after its import. Found by Plaud ID, audio-router's reference and audio hash, from
    catalog data alone, so no file of theirs is opened. The lookups are strict: an index that
    can't answer now raises sqlite3.DatabaseError, never counts none (§6.4)."""
    if index is None or not index.exists():
        return 0
    found: set[str] = set()
    for row in rows:
        if row.uri in excluded[0]:
            if row.source == "plaud":
                found.update(index.find_by_source("plaud", row.file_id, strict=True))
            found.update(index.find_by_source("audio_router", row.uri, strict=True))
    found.update(rid for sha in excluded[1] if (rid := index.find_by_sha256(sha, strict=True)))
    return len(found)


def _place(
    root: Path,
    row: Row,
    *,
    excluded: tuple[frozenset[str], frozenset[str]],
    by_uri: dict[str, Row],
    index: Index | None,
    default_tz: str,
    private_media: dict[str, list[Path]],
) -> Item:
    """One row, one disposition. An excluded row is never read; any other row whose files can't
    be read is `unplaced`, naming the exception's type and never a path (a private-tier file
    name is an export title). So is one the index can't answer for now, and plan() makes that a
    problem: a busy index never reads as "not here"."""
    if row.uri in excluded[0]:
        return Item(row, "consent-excluded")
    try:
        item = _place_row(
            root,
            row,
            by_uri=by_uri,
            index=index,
            default_tz=default_tz,
            private_media=private_media,
        )
    except sqlite3.DatabaseError:  # a strict lookup in _present (§6.4)
        return Item(row, "unplaced", INDEX_BUSY)
    except (OSError, ValueError, RuntimeError) as exc:
        return Item(row, "unplaced", f"its files can't be read ({type(exc).__name__})")
    if item.sha256 and item.sha256 in excluded[1]:  # the same audio as an excluded recording
        return Item(row, "consent-excluded")
    return item


def _place_row(
    root: Path,
    row: Row,
    *,
    by_uri: dict[str, Row],
    index: Index | None,
    default_tz: str,
    private_media: dict[str, list[Path]],
) -> Item:
    if row.source not in (*SOURCES, "private"):
        return Item(row, "unplaced", f"unknown source {row.source!r}")
    if not all(_TAG.fullmatch(label) for label in row.tags):
        return Item(
            row,
            "unplaced",
            "a tag isn't a lowercase slug, as audio-router's catalog/tags.jsonl requires",
        )
    # Its labels first, from the catalog alone: a row that doesn't import keeps them too, so its
    # privacy reaches the row of its same-audio group that creates the recording (§7.4).
    tags = map_tags(row.tags)
    if row.source == "private" or row.access:  # the private tier, or audio-router's access tier
        tags = tuple(dict.fromkeys(("private", *tags)))
    private = any(is_private_tag(t) for t in tags)
    if row.source == "plaud" and not row.recorded_at_local:
        snapshots, _ = _store_files(root, "plaud", row.file_id)
        if not snapshots:  # build_catalog.py's orphans: audio alone (Dan, 2026-10-09). Its audio
            # is never read; only its labels count, for its same-audio group.
            return Item(
                row,
                "deferred-to-2b",
                "no snapshot and no recording time: stage 2b's sync matches it by its Plaud ID",
                kind="plaud",
                private=private,
                tags=tags,
            )
    when, zone, time_source, why = _when(row, default_tz)
    if when is None:
        return Item(row, "unplaced", why)
    texts: tuple[Path, ...] = ()
    if row.source == "private":
        found = _find_private(root, row.file_id, private_media)
        if isinstance(found, str):
            return Item(row, "unplaced", found)
        kind, audio, texts = found
        snapshots, renditions = (), ()
    else:
        kind = row.source
        if not row.audio_rel:
            later = "; Plaud's copy arrives through the sync" if kind == "plaud" else ""
            return Item(
                row,
                "no-audio",
                f"no audio in audio-router{later}",
                kind=kind,
                private=private,
                tags=tags,
            )
        audio = _inside(root, row.audio_rel)
        if audio is None or not audio.is_file():
            return Item(row, "unplaced", f"audio {row.audio_rel} is missing from the copy")
        snapshots, renditions = _store_files(root, kind, row.file_id)
        if not snapshots:  # build_catalog built this row from one: its time, or pocket/raw/
            return Item(
                row,
                "unplaced",
                "the copy has no snapshot of it, though the catalog "
                "was built from one: the copy is incomplete",
            )
    if not _all_inside(root, [audio, *snapshots, *renditions, *texts]):
        return Item(row, "unplaced", "a file resolves outside the source root")
    sha = sha256_file(audio)
    # A private row's 64-hex ID is its audio's hash, as the catalog's column is a main-tier row's.
    expected = row.audio_sha256 or (
        row.file_id if row.source == "private" and _HEX64.fullmatch(row.file_id) else ""
    )
    if expected and sha != expected:
        return Item(
            row, "unplaced", "the audio's SHA-256 differs from the catalog's: the copy is damaged"
        )
    if row.dup_of and row.dup_of not in by_uri:
        return Item(row, "unplaced", f"dup_of names {row.dup_of}, which the catalog doesn't list")
    present, conflict = _present(index, kind, row, sha)
    if conflict:
        return Item(row, "unplaced", conflict)
    disposition = (
        "present" if present else "duplicate" if row.dup_of else "new-private" if private else "new"
    )
    return Item(
        row,
        disposition,
        kind=kind,
        audio=audio,
        sha256=sha,
        recorded_at=when,
        timezone=zone,
        time_source=time_source,
        snapshots=snapshots,
        renditions=renditions,
        texts=texts,
        present_id=present,
        private=private,
        tags=tags,
        probed=None if present else probe(audio),
        snapshot_problems=_snapshot_problems(kind, snapshots),
    )


def _ids_on_disk(root: Path, source: str) -> tuple[set[str], int]:
    """Every name with something on disk, as audio-router's store.archived_ids reads them:
    audio/…/<id>.<ext>, and the folders raw/<id>/ and derived/<id>/. Also how many folders
    couldn't be listed: a recording in one would look absent, so plan() reports them."""
    names: set[str] = set()
    unlistable = 0
    audio = root / source / "audio"
    if audio.is_dir():
        files, unlistable = _walk(audio)
        names |= {
            p.stem
            for p in files
            if p.is_file() and not p.name.startswith(".") and p.suffix not in (".part", ".tmp")
        }
    for folder in ("raw", "derived"):
        base = root / source / folder
        try:
            entries = list(base.iterdir())
        except FileNotFoundError:
            continue
        except OSError:
            unlistable += 1
            continue
        names |= {p.name for p in entries if p.is_dir() and not p.name.startswith((".", "_"))}
    return names, unlistable


def _report_id(name: str) -> str:
    """An ID as it is. A name that isn't one (a Pocket title slug, a title-named file) becomes
    build_catalog.py's surrogate, sha256(name)[:32], so no title reaches a report."""
    if _HEX32.fullmatch(name) or _HEX64.fullmatch(name) or _UUID.fullmatch(name):
        return name
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]


def _unlisted(root: Path, rows: list[Row], consent: frozenset[str]) -> tuple[list[str], int]:
    """Recordings on disk with no catalog row, by ID only, leaving out consent-listed IDs, and
    how many store folders couldn't be listed to look for them."""
    listed = {(r.source, r.file_id) for r in rows}
    out: set[str] = set()
    unlistable = 0
    for source in ("plaud", "recorder"):
        names, missed = _ids_on_disk(root, source)
        unlistable += missed
        for name in names:
            fid = _report_id(name)
            if (source, fid) not in listed and canonical_plaud_id(fid) not in consent:
                out.add(f"{source}/{fid}")
    try:
        pocket = _json_files(root / "pocket" / "raw")
    except OSError:
        pocket, unlistable = [], unlistable + 1
    for path in pocket:
        if path.stem.startswith("_"):
            continue
        fid = _report_id(path.stem)
        if ("pocket", fid) not in listed and canonical_plaud_id(fid) not in consent:
            out.add(f"pocket/{fid}")
    return sorted(out), unlistable


def _links(item: Item) -> set[str]:
    """What joins a row to the others with its audio: its URI and its `dup_of` (so `dup_of` links
    both ways), the catalog's audio hash (a private row's 64-hex ID is one), and the hash of the
    file read."""
    row = item.row
    out = {row.uri, *(f"sha:{s}" for s in (row.audio_sha256, item.sha256) if s)}
    if row.dup_of:
        out.add(row.dup_of)
    if row.source == "private" and _HEX64.fullmatch(row.file_id):
        out.add(f"sha:{row.file_id}")
    return out


def _groups(items: list[Item]) -> dict[str, str]:
    """Each row's same-audio group, by URI: the rows linked to it (_links), directly or through
    any other row, whether it imports or not. A consent-excluded row is in none: it is never read
    past its ID, and the consent closure follows the same links, so no row that imports is linked
    to one."""
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        while parent.setdefault(key, key) != key:
            key = parent[key]
        return key

    linked = [i for i in items if i.disposition != "consent-excluded"]
    for item in linked:
        first, *rest = sorted(_links(item))
        for key in rest:
            a, b = find(key), find(first)
            if a != b:
                parent[a] = b
    return {i.row.uri: find(i.row.uri) for i in linked}


def _hold_groups(items: list[Item], groups: dict[str, str]) -> list[Item]:
    """Rows with the same audio are one recording, created with the whole group's privacy (§9.3,
    §7.4). When one of them can't be placed, the rest wait too: imported alone, the recording
    would lack that row's labels, titles and snapshots, and a later run could add its private
    tag only after the content was already in."""
    waits: dict[str, str] = {}
    for item in items:
        if item.disposition == "unplaced":
            waits.setdefault(groups[item.row.uri], item.row.uri)
    return [
        Item(
            item.row,
            "unplaced",
            f"{waits[groups[item.row.uri]]} has the same audio and "
            "can't be placed: the recording waits for it",
        )
        if item.disposition in IMPORTED and groups[item.row.uri] in waits
        else item
        for item in items
    ]


def _share_privacy(items: list[Item], groups: dict[str, str]) -> list[Item]:
    """Each row to import gets its whole group's privacy tags, as group_privacy: those of every
    row linked to it, whether or not that row imports (§7.4, §9.3). A row deferred to 2b, or with
    no audio, gives its privacy labels and nothing else: its audio and files are never read, and
    its other labels stay with it."""
    privacy: dict[str, dict[str, None]] = {}
    for item in items:
        if item.row.uri in groups:
            privacy.setdefault(groups[item.row.uri], {}).update(
                dict.fromkeys(t for t in item.tags if is_private_tag(t))
            )
    return [
        replace(item, group_privacy=tuple(privacy[groups[item.row.uri]]))
        if item.disposition in IMPORTED
        else item
        for item in items
    ]


def plan(
    root: Path, index: Index | None = None, *, consent: Collection[str], default_timezone: str
) -> Plan:
    """The dry run (§9.3). `consent` is Dan's consent list (§9.1), which the caller reads
    fail-closed with `recordings.plaud.consent`. `default_timezone` is `[archive] default_timezone`
    (it places a time the source gave without an offset). Neither has a default, so no caller
    forgets it."""
    root = Path(root)
    listed = frozenset(canonical_plaud_id(c) for c in consent)
    rows = read_catalog(root)
    excluded = _consent_closure(rows, listed)
    provenance, problems = _provenance(root)
    if isinstance(provenance.get("rows"), int) and provenance["rows"] != len(rows):
        problems.append(
            f"catalog.csv has {len(rows)} rows but provenance.json says "
            f"{provenance['rows']}: the copy may be incomplete"
        )
    # The list excludes a recording's copies by its audio hash (§9.1). A listed row with none
    # (its private-tier file ID is its hash, so that one counts) can't have its copies
    # recognised: the run stops, saying how many and never which.
    blind = sum(
        1
        for r in rows
        if canonical_plaud_id(r.file_id) in listed
        and not r.audio_sha256
        and not (r.source == "private" and _HEX64.fullmatch(r.file_id))
    )
    if blind:
        problems.append(
            f"{blind} consent-listed rows have no catalog audio hash, so copies of "
            "them can't be recognised: rebuild the catalog"
        )
    # An ID listed after its recording was imported: 2a can't take the recording out, so the
    # run stops until Dan does (§9.1). Counted, never named, and found from catalog data alone.
    try:
        kept = _consent_kept(index, rows, excluded)
    except sqlite3.DatabaseError:  # a busy index never counts none (§6.4)
        kept = 0
        problems.append(
            "the index can't answer now (busy, or an I/O error), so recordings "
            "already here that the consent list now excludes can't be counted: run "
            "the import again"
        )
    if kept:
        problems.append(
            f"{kept} recordings already in this archive are now on the consent "
            "list: 2a can't remove them, so take them out by hand (then `recordings "
            "reindex --allow-missing`) before importing again"
        )
    by_uri = {r.uri: r for r in rows}
    private_media, skipped = _private_media(root, listed)
    if skipped:
        problems.append(
            f"{skipped} private-tier files or folders couldn't be read or sit "
            "outside the source: they are not matched to any row"
        )
    named = {r.file_id for r in rows if r.source == "private"}
    unlisted_private = sum(
        1
        for sha, paths in private_media.items()
        for p in paths
        if sha not in named and p.stem not in named
    )
    # Rows that are nobody's duplicate first, so a duplicate always merges into its original.
    placed = [
        _place(
            root,
            row,
            excluded=excluded,
            by_uri=by_uri,
            index=index,
            default_tz=default_timezone,
            private_media=private_media,
        )
        for row in sorted(rows, key=lambda r: (bool(r.dup_of), r.uri))
    ]
    busy = sum(1 for i in placed if i.reason == INDEX_BUSY)
    if busy:
        problems.append(
            f"the index can't answer now (busy, or an I/O error) for {busy} rows, so "
            "the plan can't tell which are already here: run the import again"
        )
    groups = _groups(placed)
    items = _share_privacy(_hold_groups(placed, groups), groups)
    unlisted, unlistable = _unlisted(root, rows, listed)
    if unlistable:
        problems.append(
            f"{unlistable} store folders couldn't be listed: recordings in them can't "
            "be found, so the copy can't be checked against the catalog"
        )
    ledger = [str(x) for x in provenance.get("ledger_only_no_media") or []]
    held = ((provenance.get("per_source") or {}).get("plaud") or {}).get("held_count")
    return Plan(
        root=root,
        items=tuple(items),
        ledger_only=tuple(sorted(x for x in ledger if canonical_plaud_id(x) not in listed)),
        unlisted=tuple(unlisted),
        held_on_plaud=held if isinstance(held, int) else None,
        problems=tuple(problems),
        unlisted_private=unlisted_private,
    )


_log = logging.getLogger(__name__)
# A fixed phrase per kind of failure. An exception's own text can carry a title (pydantic's
# input_value, a path built from a tab name), so a report never shows it (Review Focus 4).
_FAILURES = (
    (IndexRefused, "the archive reads as empty or unreadable"),
    (LockTimeout, "a lock stayed busy"),
    (IndexUnavailable, "the index could not answer"),
    (NotTheWriter, "this process may not write the archive now"),
    (sqlite3.Error, "state.db or the index could not be used"),
    (WriterError, "the writer refused it"),
    (RecursionError, "a payload is nested too deeply"),
    (ArithmeticError, "a number was out of range"),
    (TypeError, "a value had the wrong type"),
    (KeyError, "a recording folder is missing"),
    (OSError, "a file could not be read or written"),
    (ValueError, "a value did not validate"),
)


@dataclass
class RunReport:
    created: int = 0
    merged: int = 0
    sources_added: int = 0
    renditions_added: int = 0
    plaud_outputs: int = 0
    held_removals: int = 0
    skipped_snapshots: int = 0
    skipped_outputs: dict[str, int] = field(default_factory=dict)
    measured: dict[str, int] = field(default_factory=dict)  # durations, by how they were measured
    failed: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class ImportStopped(RuntimeError):
    """The run stopped, and `.report` is what was done before it did. It stops, in fixed words:
    - before it writes anything, when the plan has problems or any media can't be measured;
    - when the disk guard says stop, or the next new recording would leave less than the stop
      margin free;
    - when `add` finds the archive reads as empty (its disk is unmounted, most likely);
    - when a row fails with a full disk (ENOSPC, EDQUOT), any sqlite3 error, an index that can't
      answer, or an archive that has dropped off: going on would only fail every later row.
    The lock timing out is a LockTimeout, not this."""

    def __init__(self, message: str, report: RunReport) -> None:
        super().__init__(message)
        self.report = report


def _failure(exc: BaseException) -> str:
    phrase = next((p for kind, p in _FAILURES if isinstance(exc, kind)), "it failed")
    return f"{type(exc).__name__}: {phrase}"


def _stops_the_run(exc: BaseException) -> bool:
    """A full disk, or state.db or the index unusable: going on would only fail every later row,
    and fill the disk again for each one."""
    if isinstance(exc, OSError):
        return exc.errno in (errno.ENOSPC, errno.EDQUOT)
    return isinstance(exc, (sqlite3.Error, IndexUnavailable, NotTheWriter))


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None


def _fetched_at(path: Path) -> datetime:
    """When audio-router fetched a snapshot: a legacy snapshot's own `_meta.fetched_at`, else the
    file's stamp. Pocket's flat file and a private-tier `.txt` have only their mtime.

    audio-router's same-second siblings (`x.json`, `x-01.json`, …) were written in counter
    order. The counter goes in as microseconds, so that order is in the data: with tied times,
    the newest output would fall to file names, where `x-2.json` sorts before `x.json`."""
    legacy = meta_fetched_at(_read_json(path)) if path.suffix == ".json" else None
    if legacy is not None:
        return legacy
    stamp, _, counter = path.stem.partition("-")
    try:
        when = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return when + timedelta(microseconds=int(counter)) if counter.isdigit() else when


def _titles(item: Item, *, complete_only: bool = False) -> list[str]:
    """Every title the item's snapshots carry, oldest first; with `complete_only`, only those of
    snapshots reconcile will use. They go only into recording.json and the auto-private check
    (which uses them all, erring private), never into a report."""
    key = "title" if item.kind == "pocket" else "name"
    problems = item.snapshot_problems or ((),) * len(item.snapshots)
    docs = [
        _read_json(path)
        for path, bad in zip(item.snapshots, problems, strict=True)
        if not (complete_only and bad)
    ]
    return [
        d[key].strip()
        for d in docs
        if isinstance(d, dict) and isinstance(d.get(key), str) and d[key].strip()
    ]


def _identity(rendition: Rendition) -> tuple:
    """An imported output's identity, as reconcile keys Plaud's: an audio-router file by its name
    (each is imported once, whatever this mapping later makes of it), and a Pocket output by its
    snapshot, the hash of what it carries and its `version`, which carries IMPORT_VERSION."""
    if rendition.meta.get("imported_from") == "audio-router":
        return ("audio-router", rendition.meta.get("file"))
    return (
        rendition.kind,
        rendition.note_type,
        rendition.inputs.get("source"),
        rendition.inputs.get("part_sha256"),
        rendition.version,
    )


def _imported(writer: Writer, rid: str) -> set[tuple]:
    """The identities of the outputs the import already wrote to a recording. Bytes aren't
    identity: compared by bytes, a change to the mapping, or to how an output is written, would
    leave a second, permanent copy of each on the next re-run, and the first would stay the one
    shown (`x-2.json` sorts before `x.json`)."""
    return {
        _identity(r)
        for _, r in writer.archive.renditions(rid, problems=[])
        if r.engine == "pocket" or r.meta.get("imported_from") == "audio-router"
    }


def _title(item: Item) -> tuple[str, bool]:
    """(the title, whether the source supplied it): the newest complete snapshot's, never a
    degraded one's (§9.1.1), else a private-tier export's own name, which is how Recorder titles
    a recording."""
    titles = _titles(item, complete_only=True)
    if titles:
        return titles[-1], True
    audio = item.audio
    if item.row.source == "private" and audio is not None and audio.stem != item.row.file_id:
        return audio.stem, True
    return "Untitled recording", False


def _recorded(writer: Writer, item: Item) -> MediaProbe:
    """A present row's measurement, from its recording: the decoded duration of the same bytes,
    taken when it was created (§9.1.1), so a re-run never decodes it again. The media is decoded
    only when the recording holds none. A recording that has gone raises KeyError, so the row
    fails and is never created unmeasured."""
    media = writer.archive.load(item.present_id or "").media
    if media.sha256 == item.sha256 and media.duration_ms is not None:
        return MediaProbe(
            duration_ms=media.duration_ms,
            sample_rate=media.sample_rate,
            channels=media.channels,
            has_video=media.kind == "video",
            method="recorded",
        )
    measured = probe(item.audio) if item.audio is not None else None
    if measured is None:
        raise WriterError("its media can't be measured (spec §9.1.1)")
    return measured


def _import_one(
    item: Item,
    writer: Writer,
    *,
    now: datetime,
    report: RunReport,
    group: list[Item],
    counted: set[tuple],
) -> None:
    sources = [
        RawSource(
            kind=item.kind,
            ref=item.row.file_id,
            added_at=now,
            fetched_at=_fetched_at(path),
            payload=path.read_bytes(),
        )
        for path in item.snapshots
    ]
    sources += [
        RawSource(
            kind=item.kind,
            ref=item.row.file_id,
            added_at=now,
            fetched_at=_fetched_at(path),
            payload=path.read_bytes(),
            suffix=path.suffix.lower(),
        )
        for path in item.texts
    ]  # Recorder's prose transcript, kept as it came (§9.3)
    if not sources:  # the source's own ID is always kept, so a later sync can match it
        sources.append(RawSource(kind=item.kind, ref=item.row.file_id, added_at=now))
    sources.append(RawSource(kind="audio_router", ref=item.row.uri, added_at=now))
    have = _imported(writer, item.present_id) if item.present_id else set()
    renditions = []
    for path in item.renditions:
        mapped = map_rendition(path, _read_json(path))
        if mapped.rendition is not None:
            if _identity(mapped.rendition) not in have:  # each audio-router file goes in once
                renditions.append(mapped.rendition)
        elif mapped.keep_raw:  # the speaker names Dan typed: verbatim in source/ (§9.3)
            sources.append(
                RawSource(
                    kind="audio_router",
                    ref=item.row.uri,
                    added_at=now,
                    fetched_at=ar_stamp(path),
                    payload=path.read_bytes(),
                )
            )
        else:
            report.skipped_outputs[mapped.skipped] = (
                report.skipped_outputs.get(mapped.skipped, 0) + 1
            )
    # Privacy first (§7.4): every private tag is in place when the recording is created, never
    # a second write after its content. The group's privacy (group_privacy) includes the rows
    # that don't import: one deferred to 2b, or with no audio.
    tags = [
        TagRef(tag=tag, by="you")
        for tag in dict.fromkeys(
            [
                *(t for member in group for t in member.tags),
                *(t for member in group for t in member.group_privacy),
            ]
        )
    ]
    if item.disposition == "present":  # its recording exists: a label Dan removed stays removed,
        tags = [t for t in tags if is_private_tag(t.tag)]  # and only privacy is merged again
    titles = [t for member in group if member.kind == "plaud" for t in _titles(member)]
    if not any(is_private_tag(t.tag) for t in tags) and matches_auto_private(
        titles, writer.auto_private
    ):
        tags.append(TagRef(tag="private", by="auto"))
    title, from_source = _title(item)
    probed = item.probed if item.probed is not None else _recorded(writer, item)
    added = writer.add(
        Incoming(
            media=item.audio,
            recorded_at=item.recorded_at,
            timezone_name=item.timezone,
            time_source=item.time_source,
            title=title,
            title_by=item.kind if from_source else None,
            kind=media_kind(item.audio, probed) or "audio",
            sources=tuple(sources),
            duration_ms=probed.duration_ms,
            sample_rate=probed.sample_rate,
            channels=probed.channels,
            tags=tuple(tags),
            renditions=tuple(renditions),
            sha256=item.sha256,
        )
    )
    if item.probed is not None:  # a present row was measured when its recording was created
        report.measured[probed.method] = report.measured.get(probed.method, 0) + 1
    report.created += added.created
    report.merged += not added.created
    report.sources_added += added.sources_added
    report.renditions_added += added.renditions_added
    rid = added.recording.id
    if item.kind == "plaud":
        reconciled = writer.reconcile_plaud(rid)
        report.plaud_outputs += reconciled.written
        # Reconcile rebuilds these from all of the recording's snapshots on every call, so a
        # same-audio group would count each once per row: count each once per recording.
        for path in reconciled.skipped:
            if ("skipped", rid, path) not in counted:
                counted.add(("skipped", rid, path))
                report.skipped_snapshots += 1
        for held in reconciled.held:
            if ("held", rid, held.snapshot, held.slot) not in counted:
                counted.add(("held", rid, held.snapshot, held.slot))
                report.held_removals += 1
    elif item.kind == "pocket":
        rec = writer.archive.load(rid)
        folder = writer.archive.path_for(rid)
        have = _imported(writer, rid)
        last: dict[
            tuple, str
        ] = {}  # each slot's part hash at the previous snapshot, as reconcile's `applied`
        for source in rec.sources:
            doc = (
                _read_json(folder / source.raw) if source.kind == "pocket" and source.raw else None
            )
            if isinstance(doc, dict):
                for rendition in pocket_outputs(
                    doc, source.raw, source.fetched_at or source.added_at
                ):
                    slot, part = (
                        (rendition.kind, rendition.note_type),
                        rendition.inputs["part_sha256"],
                    )
                    if (
                        last.get(slot) == part
                    ):  # the same output as the last snapshot's: no new file
                        continue
                    last[slot] = part
                    if _identity(rendition) in have:  # written before, whatever its bytes now
                        continue
                    if writer.write_rendition(rid, rendition) is not None:
                        report.renditions_added += 1


def _would_fill(status: DiskStatus, item: Item, stop_free_percent: float, *, creates: bool) -> bool:
    """Whether copying the item's media would leave less than the stop margin free. Only the row
    that creates a recording copies media: the first of its same-audio group not yet here, which
    is a `duplicate` when its original is deferred to 2b. The rest merge into it."""
    if not creates or item.audio is None or status.free_bytes is None or not status.total_bytes:
        return False
    try:
        size = item.audio.stat().st_size
    except OSError:
        return False  # add will fail on it and say so
    return (status.free_bytes - size) * 100 / status.total_bytes < stop_free_percent


def run(
    plan: Plan,
    writer: Writer,
    *,
    now: datetime,
    disk: Callable[[], DiskStatus] | None = None,
    stop_free_percent: float = STOP_FREE_PERCENT,
    lock_timeout: float = 600,
) -> RunReport:
    """Import every placeable row. Re-runnable: what is already there is matched and skipped.

    It holds the ops lock throughout, so a backup or the mirror never runs half-way through it.
    Before it takes the lock or writes anything it refuses a plan with problems (§9.1: that
    includes the rows the consent list can't be applied to) and any recording whose media can't
    be measured (§9.1.1). One recording's failure is reported and the run goes on. It raises
    ImportStopped, keeping the report, when the disk guard says stop or the next new recording
    would leave less than `stop_free_percent` free, when the archive reads as empty, and when a
    row fails with a full disk, a sqlite3 error, an index that can't answer or an archive that
    has dropped off: going on would only fail every later row."""
    report = RunReport()
    if plan.problems:
        raise ImportStopped(
            f"the plan has {len(plan.problems)} problem{'s' if len(plan.problems) != 1 else ''} "
            "(see --dry-run): nothing was imported",
            report,
        )
    # Recordings already here go first: a label added in audio-router since (a privacy label
    # among them) lands even when a later new recording stops the run.
    todo = sorted(
        (item for item in plan.items if item.disposition in IMPORTED),
        key=lambda item: item.disposition != "present",
    )
    # Rows with the same audio become one recording, whichever is added first. Each is added
    # with its whole group's privacy, so a duplicate's label or title is never a late write.
    by_sha: dict[str, list[Item]] = {}
    for item in todo:
        by_sha.setdefault(item.sha256, []).append(item)
    unmeasured = len(plan.unprobed())
    if unmeasured:
        raise ImportStopped(
            f"media can't be measured for {unmeasured} of {len(todo)} recordings: anything but "
            "WAV needs ffprobe and ffmpeg (spec §9.1.1). Nothing was imported.",
            report,
        )
    counted: set[tuple] = set()
    with (
        Locks(writer.state.locks_dir, timeout=lock_timeout).hold(OPS_LOCK),
        announce(writer.state.locks_dir, "import", now),
    ):
        for item in todo:
            if disk is not None:
                status = disk()
                if status.level == "stop":
                    raise ImportStopped(status.message, report)
                creates = item.disposition != "present" and by_sha[item.sha256][0] is item
                if _would_fill(status, item, stop_free_percent, creates=creates):
                    raise ImportStopped(
                        f"the next recording would leave less than "
                        f"{stop_free_percent:g}% free: new imports stop",
                        report,
                    )
            try:
                _import_one(
                    item, writer, now=now, report=report, group=by_sha[item.sha256], counted=counted
                )
            except IndexRefused as exc:  # the archive reads as empty: unmounted, most likely
                _log.debug("importing %s stopped the run", item.row.uri, exc_info=True)
                report.failed.append({"uri": item.row.uri, "reason": _failure(exc)})
                raise ImportStopped(
                    "the archive reads as empty or unreadable (is its disk "
                    "mounted?): the import stopped",
                    report,
                ) from None
            except (
                WriterError,
                LockTimeout,
                KeyError,
                OSError,
                ValueError,
                TypeError,
                RecursionError,
                ArithmeticError,
                sqlite3.Error,
            ) as exc:
                _log.debug("importing %s failed", item.row.uri, exc_info=True)
                report.failed.append({"uri": item.row.uri, "reason": _failure(exc)})
                if _stops_the_run(exc):
                    raise ImportStopped(
                        "the disk is full, the archive has dropped off, or state.db "
                        "or the index can't be used now: the import stopped",
                        report,
                    ) from None
    return report
