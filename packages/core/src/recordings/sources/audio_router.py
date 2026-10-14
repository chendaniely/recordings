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
import hashlib
import json
import re
from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from recordings.archive import sha256_file
from recordings.index import Index
from recordings.media import MEDIA_SUFFIXES, MediaProbe, probe
from recordings.models import is_private_tag
from recordings.plaud.normalise import link_errors
from recordings.refs import canonical_plaud_id

CATALOG = Path("catalog") / "catalog.csv"
PROVENANCE = Path("catalog") / "provenance.json"
REQUIRED_COLUMNS = ("uri", "source", "file_id", "recorded_at_local", "timezone", "time_source",
                    "audio_rel", "audio_sha256", "dup_of", "tags", "access")
SOURCES = ("plaud", "recorder", "pocket")
# audio-router's time_source, per source, as recordings' (§6.2); anything else is unplaced.
TIME_SOURCES = {
    ("plaud", "start_at"): "plaud", ("plaud", "created_at"): "plaud",
    ("recorder", "filename"): "metadata", ("recorder", "file mtime (inferred)"): "mtime",
    ("pocket", "recording_at"): "metadata", ("pocket", "created_at"): "ingest",
    ("private", "filename"): "metadata",
}
DISPOSITIONS = ("new", "new-private", "duplicate", "present", "no-audio", "deferred-to-2b",
                "consent-excluded", "unplaced")
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
    private: bool = False
    tags: tuple[str, ...] = ()  # recordings tags, privacy labels already under private/
    probed: MediaProbe | None = None  # None: its duration couldn't be measured
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
        """Rows to import whose media couldn't be measured: a real run refuses them all."""
        return [i.row.uri for i in self.items if i.disposition in IMPORTED and i.probed is None]

    def incomplete_snapshots(self) -> dict:
        """Plaud snapshots reconcile will skip, counted by reason (a snapshot with two reasons
        counts under both), for rows a run imports."""
        bad = [reasons for i in self.items if i.disposition in IMPORTED
               for reasons in i.snapshot_problems if reasons]
        by_reason = Counter(r for reasons in bad for r in set(reasons))
        return {"total": len(bad), "by_reason": dict(sorted(by_reason.items()))}

    def all_snapshots_incomplete(self) -> list[str]:
        """Rows whose every snapshot is incomplete: they would get no Plaud outputs at all."""
        return [i.row.uri for i in self.items if i.disposition in IMPORTED
                and i.snapshot_problems and all(i.snapshot_problems)]

    @property
    def ok(self) -> bool:
        return (not self.problems and not self.unprobed()
                and not any(i.disposition == "unplaced" for i in self.items))

    def report(self) -> dict:
        """Counts, URIs and IDs: never a title or a transcript, and never a consent-listed ID."""
        return {
            "catalog_rows": len(self.items),
            "by_disposition": self.counts(),
            "private": sum(i.private for i in self.items if i.disposition in IMPORTED),
            "unplaced": [{"uri": i.row.uri, "reason": i.reason}
                         for i in self.items if i.disposition == "unplaced"],
            "no_audio": [i.row.uri for i in self.items if i.disposition == "no-audio"],
            # Plaud IDs, so stage 2b's sync can match them (Dan, 2026-10-09).
            "deferred_to_2b": [i.row.file_id for i in self.items
                               if i.disposition == "deferred-to-2b"],
            "unprobed": self.unprobed(),
            "incomplete_snapshots": self.incomplete_snapshots(),
            "all_snapshots_incomplete": self.all_snapshots_incomplete(),
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
            "build it there with scripts/build_catalog.py, then copy the archive again.")
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise CatalogError(f"{path} is not audio-router's catalog@5: missing columns "
                               + ", ".join(missing))
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
    """(recorded_at, its zone's name, recordings' time_source, a reason when unplaced)."""
    time_source = TIME_SOURCES.get((row.source, row.time_source))
    if time_source is None:
        return None, "", "", f"unknown time source {row.time_source!r} for {row.source}"
    if not row.recorded_at_local:
        return None, "", "", "no recording time in the catalog"
    try:
        when = datetime.fromisoformat(row.recorded_at_local)
    except ValueError:
        return None, "", "", f"recording time {row.recorded_at_local!r} isn't ISO 8601"
    zone = row.timezone or default_tz
    if when.utcoffset() is None:
        try:
            when = when.replace(tzinfo=ZoneInfo(zone))
        except (ZoneInfoNotFoundError, ValueError):
            return None, "", "", f"unknown time zone {zone!r}"
    return when, zone, time_source, ""


def _store_files(root: Path, source: str, fid: str) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    if source == "pocket":
        flat = root / "pocket" / "raw" / f"{fid}.json"
        return ((flat,) if flat.is_file() else ()), ()
    raw = sorted((root / source / "raw" / fid).glob("*.json"), key=snapshot_order)
    derived = sorted((root / source / "derived" / fid).glob("*.json"))
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
    """The private tier's media files by SHA-256, hashed once per plan, and how many couldn't be
    read or sit outside the source. Its files keep their export names, so a hash is the reliable
    way to find one. A file named by a consent-listed ID is skipped before it is opened (§9.1)."""
    found: dict[str, list[Path]] = {}
    skipped = 0
    tier = root / "private"
    if tier.is_dir():
        try:
            paths = sorted(tier.rglob("*"))
        except OSError:
            return found, 1
        for path in paths:
            if (path.is_file() and not path.name.startswith(".")
                    and path.suffix.lower() in MEDIA_SUFFIXES
                    and canonical_plaud_id(path.stem) not in consent):
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
    texts = tuple(sorted(p for p in found.parent.iterdir()
                         if p.is_file() and p.stem == found.stem and p.suffix.lower() == ".txt"))
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
    reference, then the content hash (§9.3)."""
    if index is None or not index.exists():
        return None, ""
    if kind == "plaud":
        for rid in index.find_by_source("plaud", row.file_id):
            if index.sha256_of(rid) != sha:
                return None, (f"Plaud ID {row.file_id} is already in this archive ({rid}) "
                              "with different audio")
            return rid, ""
    for rid in index.find_by_source("audio_router", row.uri):
        return rid, ""
    return index.find_by_sha256(sha), ""


def _consent_closure(rows: list[Row], consent: frozenset[str]) -> tuple[frozenset[str], frozenset[str]]:
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


def _place(root: Path, row: Row, *, excluded: tuple[frozenset[str], frozenset[str]],
           by_uri: dict[str, Row], index: Index | None, default_tz: str,
           private_media: dict[str, list[Path]]) -> Item:
    """One row, one disposition. An excluded row is never read; any other row whose files can't
    be read is `unplaced`, naming the exception's type and never a path (a private-tier file
    name is an export title)."""
    if row.uri in excluded[0]:
        return Item(row, "consent-excluded")
    try:
        item = _place_row(root, row, by_uri=by_uri, index=index, default_tz=default_tz,
                          private_media=private_media)
    except (OSError, ValueError, RuntimeError) as exc:
        return Item(row, "unplaced", f"its files can't be read ({type(exc).__name__})")
    if item.sha256 and item.sha256 in excluded[1]:  # the same audio as an excluded recording
        return Item(row, "consent-excluded")
    return item


def _place_row(root: Path, row: Row, *, by_uri: dict[str, Row], index: Index | None,
               default_tz: str, private_media: dict[str, list[Path]]) -> Item:
    if row.source not in (*SOURCES, "private"):
        return Item(row, "unplaced", f"unknown source {row.source!r}")
    if not all(_TAG.fullmatch(label) for label in row.tags):
        return Item(row, "unplaced",
                    "a tag isn't a lowercase slug, as audio-router's catalog/tags.jsonl requires")
    if row.source == "plaud" and not row.recorded_at_local:
        snapshots, _ = _store_files(root, "plaud", row.file_id)
        if not snapshots:  # build_catalog.py's orphans: audio alone (Dan, 2026-10-09)
            return Item(row, "deferred-to-2b", "no snapshot and no recording time: stage 2b's "
                        "sync matches it by its Plaud ID", kind="plaud")
    when, zone, time_source, why = _when(row, default_tz)
    if when is None:
        return Item(row, "unplaced", why)
    tags = map_tags(row.tags)
    if row.source == "private" or row.access:  # the private tier, or audio-router's access tier
        tags = tuple(dict.fromkeys(("private", *tags)))
    private = any(is_private_tag(t) for t in tags)
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
            return Item(row, "no-audio", f"no audio in audio-router{later}", kind=kind)
        audio = _inside(root, row.audio_rel)
        if audio is None or not audio.is_file():
            return Item(row, "unplaced", f"audio {row.audio_rel} is missing from the copy")
        snapshots, renditions = _store_files(root, kind, row.file_id)
    if not _all_inside(root, [audio, *snapshots, *renditions, *texts]):
        return Item(row, "unplaced", "a file resolves outside the source root")
    sha = sha256_file(audio)
    if row.audio_sha256 and sha != row.audio_sha256:
        return Item(row, "unplaced",
                    "the audio's SHA-256 differs from the catalog's: the copy is damaged")
    if row.dup_of and row.dup_of not in by_uri:
        return Item(row, "unplaced", f"dup_of names {row.dup_of}, which the catalog doesn't list")
    present, conflict = _present(index, kind, row, sha)
    if conflict:
        return Item(row, "unplaced", conflict)
    disposition = ("present" if present else "duplicate" if row.dup_of
                   else "new-private" if private else "new")
    return Item(row, disposition, kind=kind, audio=audio, sha256=sha, recorded_at=when,
                timezone=zone, time_source=time_source, snapshots=snapshots,
                renditions=renditions, texts=texts, present_id=present, private=private,
                tags=tags, probed=probe(audio),
                snapshot_problems=_snapshot_problems(kind, snapshots))


def _ids_on_disk(root: Path, source: str) -> set[str]:
    """Every name with something on disk, as audio-router's store.archived_ids reads them:
    audio/…/<id>.<ext>, and the folders raw/<id>/ and derived/<id>/."""
    names: set[str] = set()
    audio = root / source / "audio"
    if audio.is_dir():
        names |= {p.stem for p in audio.rglob("*") if p.is_file()
                  and not p.name.startswith(".") and p.suffix not in (".part", ".tmp")}
    for folder in ("raw", "derived"):
        base = root / source / folder
        if base.is_dir():
            names |= {p.name for p in base.iterdir()
                      if p.is_dir() and not p.name.startswith((".", "_"))}
    return names


def _report_id(name: str) -> str:
    """An ID as it is. A name that isn't one (a Pocket title slug, a title-named file) becomes
    build_catalog.py's surrogate, sha256(name)[:32], so no title reaches a report."""
    if _HEX32.fullmatch(name) or _HEX64.fullmatch(name) or _UUID.fullmatch(name):
        return name
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]


def _unlisted(root: Path, rows: list[Row], consent: frozenset[str]) -> list[str]:
    """Recordings on disk with no catalog row, by ID only, leaving out consent-listed IDs."""
    listed = {(r.source, r.file_id) for r in rows}
    out: set[str] = set()
    for source in ("plaud", "recorder"):
        for name in _ids_on_disk(root, source):
            fid = _report_id(name)
            if (source, fid) not in listed and canonical_plaud_id(fid) not in consent:
                out.add(f"{source}/{fid}")
    pocket_raw = root / "pocket" / "raw"
    if pocket_raw.is_dir():
        for path in pocket_raw.glob("*.json"):
            if path.stem.startswith("_"):
                continue
            fid = _report_id(path.stem)
            if ("pocket", fid) not in listed and canonical_plaud_id(fid) not in consent:
                out.add(f"pocket/{fid}")
    return sorted(out)


def plan(root: Path, index: Index | None = None, *, consent: Collection[str],
         default_timezone: str = "America/Vancouver") -> Plan:
    """The dry run (§9.3). `consent` is Dan's consent list (§9.1), which the caller reads
    fail-closed with `recordings.plaud.consent`. It has no default, so no caller forgets it."""
    root = Path(root)
    listed = frozenset(canonical_plaud_id(c) for c in consent)
    rows = read_catalog(root)
    excluded = _consent_closure(rows, listed)
    provenance, problems = _provenance(root)
    if isinstance(provenance.get("rows"), int) and provenance["rows"] != len(rows):
        problems.append(f"catalog.csv has {len(rows)} rows but provenance.json says "
                        f"{provenance['rows']}: the copy may be incomplete")
    by_uri = {r.uri: r for r in rows}
    private_media, skipped = _private_media(root, listed)
    if skipped:
        problems.append(f"{skipped} private-tier files couldn't be read or sit outside the "
                        "source: they are not matched to any row")
    named = {r.file_id for r in rows if r.source == "private"}
    unlisted_private = sum(1 for sha, paths in private_media.items() for p in paths
                           if sha not in named and p.stem not in named)
    # Rows that are nobody's duplicate first, so a duplicate always merges into its original.
    items = [_place(root, row, excluded=excluded, by_uri=by_uri, index=index,
                    default_tz=default_timezone, private_media=private_media)
             for row in sorted(rows, key=lambda r: (bool(r.dup_of), r.uri))]
    ledger = [str(x) for x in provenance.get("ledger_only_no_media") or []]
    held = ((provenance.get("per_source") or {}).get("plaud") or {}).get("held_count")
    return Plan(root=root, items=tuple(items),
                ledger_only=tuple(sorted(x for x in ledger
                                         if canonical_plaud_id(x) not in listed)),
                unlisted=tuple(_unlisted(root, rows, listed)),
                held_on_plaud=held if isinstance(held, int) else None, problems=tuple(problems),
                unlisted_private=unlisted_private)
