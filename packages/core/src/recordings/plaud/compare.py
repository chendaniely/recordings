"""The compare (stage 2b spec §11.3, §11.4 step 6, §9.5): every listed ID in exactly one bucket,
both equations, the re-key match, and what a complete listing writes.

**The buckets,** in this order of precedence (§11.3):
1. **here:** in the archive under this Plaud ID, in either form (`Here`, from the index asked
   strictly, and the files of recordings whose index update is pending). A recording here that is
   consent-listed or kept out late stays here, with its flag.
2. **excluded:** on the consent list (`consent`); kept out by a title that starts with a keep-out
   word now (`title`); kept out by an earlier title, which stays kept out until Release
   (`was-title`, §12.2); or a possible re-keyed consent-listed or kept-out recording (`rekeyed`,
   §11.4 step 6). Never by title: a consent-listed ID's title is dropped before anything else
   reads its row (§12.1).
3. **waiting:** not here, and the next scheduled run brings it in ("imports at the next sync").
4. **missing:** not here, and it won't come without Dan; its state is the first of §15.1's that
   applies: the gate, a consent stop, a hold, review after a rebuild, parked, failed 3 times, the
   disk, the schedule off, auto-import off.

listed = here + waiting + excluded + missing, with repeats counted once (the listing already keeps
one row per canonical ID). Beside it: In the archive from Plaud = here + gone from Plaud + absent
from this listing, counted by recording; of the waiting and missing, how many arrive private (their
listed title matches `auto_private_patterns`); and the consent line (its IDs, and how many are on
Plaud now).

**The re-key match** (§11.4 step 6) runs in every complete listing, held or not, before any title
is stored: each listed ID not here, not already excluded and not released by Dan, listed while a
consent-listed or kept-out ID (or an earlier match) is absent, is compared with each absent one's
start time and duration from the cache. Every such ID in every listing, not only one
`plaud_listing` has never seen, so a first listing that couldn't match it (a consent stop, or the
old ID still listed beside it) never lets it in later; during a consent stop the cache's
consent-listed IDs, from the last readable list, count as excluded. Within `REKEY_SECONDS` on both
is a match. One match, one way: the ID is that recording, excluded and flagged ("rekeyed"). More
than one candidate, either way: it is excluded the same way (`rekey_of` "*") until Dan decides.

**What a complete listing writes** (`record_compare`): only `plaud_listing` (each listed ID's
row, absent since for the rest, a newly kept-out ID with its word, a re-keyed match), the cache
(the compare's rows, and the excluded IDs' dates and durations for the next re-key match), the
re-key flags, recordings gone from Plaud listed again, and `K_COMPARE`. Never the archive, and
never the last good listing: that is `plaud.hold`'s, for a sync only (a Compare never moves it).

**Titles.** A title goes to the cache only for a recording waiting or missing, never during a
consent stop, never while a held listing shows Plaud's IDs changed, never for a recording under
review after a rebuild, and never one `cache.title_allowed` refuses. A title hash goes to
`plaud_listing` only for an ID here that is neither consent-listed nor kept out (§9.5). Nothing here
logs anything.

Late keep-outs and title changes of recordings here (§12.2, §13.1 cases 2 and 3) are the import
area's: `Classified.word` and the listed title are on each row here for it, in memory only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING

from recordings.plaud.cache import Cache, CompareRow, KeyedOut, title_allowed
from recordings.plaud.checks import K_COMPARE, ListingEntry, ListingRow, PlaudDB
from recordings.plaud.keepout import ConsentList, keep_out_match
from recordings.plaud.reconcile import matches_auto_private
from recordings.refs import canonical_plaud_id

if TYPE_CHECKING:
    from recordings.archive import Archive
    from recordings.index import Index
    from recordings.plaud.client import Listed
    from recordings.sources.plaud import Listing
    from recordings.state import State

HERE, EXCLUDED, WAITING, MISSING = "here", "excluded", "waiting", "missing"
BUCKETS = (HERE, WAITING, EXCLUDED, MISSING)
# Not here yet's states (§15.1)
NEXT_SYNC = "imports at the next sync"
GATE_WAIT = "waits for the first catch-up"
CONSENT_STOPPED = "imports stopped: consent list"
HELD = "held: see Status"
REVIEW = "needs your review: the sync's records were rebuilt"
PARKED = "parked"
FAILED = "failed 3 times · Park"
DISK_FULL = "the disk is too full to import"
SCHEDULE_OFF = "schedule off"
AUTO_IMPORT_OFF = "auto-import off"
IMPORT_FAILURES = 3  # §11.5: after 3 failures a recording is missing ("failed 3 times")
# Kept out's kinds (types.ts KeptOutRow.by)
BY_CONSENT, BY_TITLE, BY_WAS_TITLE, BY_REKEY = "consent", "title", "was-title", "rekeyed"
TITLES_HIDDEN_CONSENT = "titles hidden until the consent list reads"
TITLES_HIDDEN_IDS = "titles hidden while Plaud's IDs look changed"
REKEY_SECONDS = 1.0  # §11.4 step 6: within 1 second on both start time and duration
SEVERAL = "*"  # rekey_of when more than one candidate matched, either way
REKEYED_EVENT = "rekeyed"  # its open flag (plaud_events): "may be … back under a new ID: confirm"


class IndexUnavailable(RuntimeError):
    """The index can't say which Plaud IDs are here now: the compare decides nothing from it."""


@dataclass(frozen=True)
class Here:
    """The archive's Plaud recordings: each canonical Plaud ID and the recordings holding it, and
    those gone from Plaud (§12.3)."""

    by_id: Mapping[str, frozenset[str]]
    gone: frozenset[str] = frozenset()

    def ids_of(self) -> dict[str, frozenset[str]]:
        """Each recording's canonical Plaud IDs."""
        found: dict[str, set[str]] = {}
        for pid, rids in self.by_id.items():
            for rid in rids:
                found.setdefault(rid, set()).add(pid)
        return {rid: frozenset(pids) for rid, pids in found.items()}

    @property
    def recordings(self) -> frozenset[str]:
        return frozenset(rid for rids in self.by_id.values() for rid in rids)


def here_from_archive(
    index: Index, uuid: str, state: State | None, archive: Archive, db: PlaudDB
) -> Here:
    """The index asked strictly, plus the files of recordings whose index update is pending, as
    `flags.listed_here` reads them. An index that can't answer (busy, missing, another archive's),
    or a pending recording whose file can't be read while its folder is there, raises
    IndexUnavailable: an undercount would read recordings here as waiting."""
    import sqlite3

    from recordings.plaud.flags import gone

    if index.archive_uuid() != uuid:
        raise IndexUnavailable("the index isn't this archive's: run `recordings reindex`")
    by_id: dict[str, set[str]] = {}
    try:
        for rid, pid in index.refs_of("plaud", strict=True):
            by_id.setdefault(pid, set()).add(rid)
        pending = state.index_pending() if state is not None else []
    except sqlite3.DatabaseError:
        raise IndexUnavailable("the index can't answer now") from None
    for rid in pending:
        try:
            rec = archive.load(rid)
        except (KeyError, OSError, ValueError):
            if not gone(archive.path_for(rid)):
                raise IndexUnavailable("a recording's file can't be read now") from None
            continue
        for source in rec.sources:
            if source.kind == "plaud":
                by_id.setdefault(canonical_plaud_id(source.ref), set()).add(rid)
    return Here(
        by_id={pid: frozenset(rids) for pid, rids in by_id.items()},
        gone=db.gone_recordings(),
    )


@dataclass(frozen=True)
class Conditions:
    """The facts that decide waiting from missing, and whether titles may be kept (§11.3, §15.1).
    `held` and `ids_changed` come from `plaud.hold.measure` of the same listing."""

    gate_open: bool = True
    held: bool = False
    ids_changed: bool = False  # a held listing that shows a census change or under 80% overlap
    schedule_on: bool = True
    auto_import: bool = True
    disk_full: bool = False


@dataclass(frozen=True)
class Classified:
    """One listed ID in its bucket. The title stays here, in memory: `record_compare` decides
    what of it may be stored."""

    listed: Listed
    bucket: str
    state: str | None = None  # waiting or missing: a §15.1 state phrase
    excluded_by: str | None = None
    recording_ids: tuple[str, ...] = ()  # here
    word: str | None = None  # the keep-out word its listed title starts with now
    arrives_private: bool = False
    review: bool = False
    rekey_of: str | None = None  # newly matched in this listing (§11.4 step 6)
    title_shown: bool = False  # its title may go to the cache
    kept_out_now: bool = False  # newly kept out by its title: plaud_listing records it

    @property
    def canonical(self) -> str:
        return self.listed.canonical_id


@dataclass(frozen=True)
class Compare:
    at: datetime  # when the listing began
    rows: tuple[Classified, ...]
    equation: dict[str, int]
    archive: dict[str, int]
    consent: dict[str, int]
    titles_hidden: str | None
    import_all: dict[str, int]
    census: dict[str, int] = field(default_factory=dict)
    repeats: int = 0
    malformed: int = 0

    def bucket(self, name: str) -> list[Classified]:
        return [row for row in self.rows if row.bucket == name]

    def record(self) -> dict:
        """K_COMPARE's value: counts and fixed phrases, never a title or an ID."""
        return {
            "at": self.at.isoformat(),
            "equation": dict(self.equation),
            "archive": dict(self.archive),
            "consent": dict(self.consent),
            "titles_hidden": self.titles_hidden,
            "import_all": dict(self.import_all),
            "census": dict(self.census),
            "repeats": self.repeats,
            "malformed": self.malformed,
        }


def _released(row: ListingRow | None) -> bool:
    """Release let it in (§12.2): released after it was last kept out."""
    if row is None or row.released_at is None:
        return False
    return row.kept_out_at is None or row.released_at >= row.kept_out_at


def _kept_out(row: ListingRow | None) -> bool:
    return row is not None and row.kept_out_at is not None and not _released(row)


def _rekeyed(row: ListingRow | None) -> bool:
    return row is not None and row.rekey_of is not None and not _released(row)


def excluded_ids(consent: ConsentList | None, listing: Mapping[str, ListingRow]) -> frozenset[str]:
    """Every canonical ID the sync keeps out now: consent-listed, kept out by a title (not
    released), or a re-keyed match."""
    kept = {pid for pid, row in listing.items() if _kept_out(row) or _rekeyed(row)}
    return frozenset(kept | (consent.ids if consent is not None else frozenset()))


def _close(a: datetime | None, b: datetime | None, da: int | None, db_: int | None) -> bool:
    if a is None or b is None or da is None or db_ is None:
        return False
    return abs((a - b).total_seconds()) <= REKEY_SECONDS and abs(da - db_) <= REKEY_SECONDS * 1000


def rekey_matches(
    rows: Sequence[Listed],
    *,
    here: Here,
    listing: Mapping[str, ListingRow],
    keyed_out: Mapping[str, KeyedOut],
    excluded: frozenset[str],
    stop: bool = False,
) -> dict[str, str]:
    """§11.4 step 6: the match of each listed ID that is not here, not excluded and not released
    by Dan, an absent excluded canonical ID, or SEVERAL. Every such ID, in every listing, not
    only one `plaud_listing` has never seen: an ID whose first listing couldn't match it (a
    consent stop, or the old ID still listed beside it) must not be let in by the next. During a
    consent stop (`stop`) the cache's consent-listed IDs, from the last readable list, count as
    excluded. A false match keeps a recording out and flags it for Dan: the safe direction."""
    if stop:
        excluded = excluded | {pid for pid, k in keyed_out.items() if k.kind == BY_CONSENT}
    listed = {row.canonical_id for row in rows}
    absent = [k for pid, k in sorted(keyed_out.items()) if pid not in listed and pid in excluded]
    if not absent:
        return {}
    new = [
        row
        for row in rows
        if row.canonical_id not in here.by_id
        and row.canonical_id not in excluded
        and not _released(listing.get(row.canonical_id))
    ]
    candidates = {
        row.canonical_id: [
            k.canonical_plaud_id
            for k in absent
            if _close(row.recorded_at, k.start_at, row.duration_ms, k.duration_ms)
        ]
        for row in new
    }
    claimed: dict[str, int] = {}
    for found in candidates.values():
        for pid in found:
            claimed[pid] = claimed.get(pid, 0) + 1
    matches = {}
    for new_id, found in candidates.items():
        if not found:
            continue
        one = len(found) == 1 and claimed[found[0]] == 1
        matches[new_id] = found[0] if one else SEVERAL
    return matches


def _missing_state(row: ListingRow | None, conditions: Conditions, stop: bool) -> str | None:
    """The first of §15.1's reasons it won't come without Dan, or None: it is waiting."""
    if not conditions.gate_open:
        return GATE_WAIT
    if stop:
        return CONSENT_STOPPED
    if conditions.held:
        return HELD
    if row is not None and row.needs_review:
        return REVIEW
    if row is not None and row.parked_at is not None:
        return PARKED
    if row is not None and row.import_failures >= IMPORT_FAILURES:
        return FAILED
    if conditions.disk_full:
        return DISK_FULL
    if not conditions.schedule_on:
        return SCHEDULE_OFF
    if not conditions.auto_import:
        return AUTO_IMPORT_OFF
    return None


def classify(
    rows: Sequence[Listed],
    *,
    here: Here,
    consent: ConsentList | None,
    words: Sequence[str],
    listing: Mapping[str, ListingRow],
    keyed_out: Mapping[str, KeyedOut],
    patterns: Sequence[str],
    conditions: Conditions,
) -> tuple[Classified, ...]:
    """Every listed ID in exactly one bucket, by §11.3's precedence (the module's notes). `consent`
    None (not read yet) is a consent stop, as is one that doesn't read; `listing` is
    plaud_listing as it was before this listing; `keyed_out` the cache's dates."""
    stop = consent is None or consent.stop is not None
    consent_ids = consent.ids if consent is not None else frozenset()
    excluded = excluded_ids(consent, listing)
    matched = rekey_matches(
        rows, here=here, listing=listing, keyed_out=keyed_out, excluded=excluded, stop=stop
    )
    out = []
    for listed in rows:
        pid = listed.canonical_id
        row = listing.get(pid)
        if pid in here.by_id:
            out.append(
                Classified(
                    listed,
                    HERE,
                    recording_ids=tuple(sorted(here.by_id[pid])),
                    word=keep_out_match(listed.title, words),
                )
            )
            continue
        if pid in consent_ids:  # its title is dropped before anything else reads the row
            out.append(Classified(replace(listed, title=None), EXCLUDED, excluded_by=BY_CONSENT))
            continue
        word = keep_out_match(listed.title, words)
        if word is not None:
            out.append(
                Classified(
                    listed,
                    EXCLUDED,
                    excluded_by=BY_TITLE,
                    word=word,
                    kept_out_now=row is None or row.kept_out_at is None or _released(row),
                )
            )
            continue
        if _kept_out(row):
            out.append(Classified(listed, EXCLUDED, excluded_by=BY_WAS_TITLE))
            continue
        if pid in matched or _rekeyed(row):
            rekey = matched.get(pid)
            out.append(Classified(listed, EXCLUDED, excluded_by=BY_REKEY, rekey_of=rekey))
            continue
        state = _missing_state(row, conditions, stop)
        review = row is not None and row.needs_review
        shown = (
            not stop
            and not (conditions.held and conditions.ids_changed)
            and not review
            and title_allowed(pid, listed.title, consent=consent, words=words)
        )
        out.append(
            Classified(
                listed,
                WAITING if state is None else MISSING,
                state=state or NEXT_SYNC,
                arrives_private=listed.title is not None
                and matches_auto_private([listed.title], patterns),
                review=review,
                title_shown=shown,
            )
        )
    return tuple(out)


def compare(
    listing_rows: Sequence[Classified],
    *,
    at: datetime,
    here: Here,
    consent: ConsentList | None,
    conditions: Conditions,
    census: Mapping[str, int] | None = None,
    repeats: int = 0,
    malformed: int = 0,
) -> Compare:
    """Both equations and the consent line from the classified rows (§11.3)."""
    counts = dict.fromkeys(BUCKETS, 0)
    for row in listing_rows:
        counts[row.bucket] += 1
    not_here = [r for r in listing_rows if r.bucket in (WAITING, MISSING)]
    listed_ids = {r.canonical for r in listing_rows}
    here_now = frozenset(rid for r in listing_rows for rid in r.recording_ids)
    gone = here.gone - here_now
    from_plaud = here.recordings
    consent_ids = consent.ids if consent is not None else frozenset()
    stop = consent is None or consent.stop is not None
    if stop:
        hidden = TITLES_HIDDEN_CONSENT
    elif conditions.held and conditions.ids_changed:
        hidden = TITLES_HIDDEN_IDS
    else:
        hidden = None
    importable = [r for r in not_here if not r.review and r.state != PARKED]
    return Compare(
        at=at,
        rows=tuple(listing_rows),
        equation={
            "listed": len(listing_rows),
            "here": counts[HERE],
            "waiting": counts[WAITING],
            "excluded": counts[EXCLUDED],
            "missing": counts[MISSING],
            "arrive_private": sum(1 for r in not_here if r.arrives_private),
        },
        archive={
            "from_plaud": len(from_plaud),
            "here": len(here_now & from_plaud),
            "gone": len(gone & from_plaud),
            "absent": len(from_plaud - here_now - gone),
        },
        consent={"ids": len(consent_ids), "on_plaud": len(consent_ids & listed_ids)},
        titles_hidden=hidden,
        import_all={
            "count": len(importable),
            "review_more": sum(1 for r in not_here if r.review),
        },
        census=dict(census or {}),
        repeats=repeats,
        malformed=malformed,
    )


def title_hash(title: str) -> str:
    """§13.2's hash: a plain SHA-256 of the title's UTF-8 bytes, exactly as Plaud gave it."""
    return hashlib.sha256(title.encode("utf-8")).hexdigest()


def _entry(row: Classified, consent_ids: frozenset[str], before: ListingRow | None) -> ListingEntry:
    """The row's plaud_listing write: a title hash and duration only for an ID here that is
    neither consent-listed, nor kept out, nor caught by a keep-out word now (§9.5)."""
    hashed = (
        row.bucket == HERE
        and row.canonical not in consent_ids
        and row.word is None
        and not _kept_out(before)
        and row.listed.title is not None
    )
    return ListingEntry(
        canonical_plaud_id=row.canonical,
        listed_id=row.listed.listed_id,
        title_sha256=title_hash(row.listed.title) if hashed else None,
        duration_ms=row.listed.duration_ms if hashed else None,
        kept_out_word=row.word if row.kept_out_now else None,
        rekey_of=row.rekey_of,
    )


def record_compare(
    db: PlaudDB,
    cache: Cache,
    result: Compare,
    *,
    consent: ConsentList | None,
    listing: Mapping[str, ListingRow],
    now: datetime,
) -> None:
    """What a complete listing writes (the module's notes). `listing` is plaud_listing as it was
    before this listing, as `classify` read it."""
    consent_ids = consent.ids if consent is not None else frozenset()
    db.write_listing(
        [_entry(row, consent_ids, listing.get(row.canonical)) for row in result.rows],
        now=result.at,
    )
    db.back_from_gone(rid for row in result.rows for rid in row.recording_ids)
    matched = [row for row in result.rows if row.rekey_of is not None]
    # The cache's kind says "consent" during a stop too, when the list in force names nothing.
    kinds = {pid: k.kind for pid, k in cache.keyed_out().items()} if matched else {}
    for row in matched:
        by_consent = row.rekey_of in consent_ids or kinds.get(row.rekey_of) == BY_CONSENT
        of = BY_CONSENT if by_consent else "kept-out"
        db.add_event(
            REKEYED_EVENT,
            now=now,
            plaud_id=row.canonical,
            detail={"of": "several" if row.rekey_of == SEVERAL else of},
            open_flag=True,
        )
    if consent is not None and consent.stop is None:
        _confirmed(db, consent_ids, now=now)
    cache.remember_keyed_out(
        (
            KeyedOut(
                row.canonical,
                row.excluded_by or "",
                row.listed.recorded_at,
                row.listed.duration_ms,
            )
            for row in result.rows
            if row.bucket == EXCLUDED
        ),
        now=result.at,
    )
    cache.replace_compare(
        (
            CompareRow(
                canonical_plaud_id=row.canonical,
                listed_id=row.listed.listed_id,
                bucket=row.bucket,
                state=row.state,
                excluded_by=row.excluded_by,
                start_at=row.listed.recorded_at,
                duration_ms=row.listed.duration_ms,
                title=row.listed.title if row.title_shown else None,
                arrives_private=row.arrives_private,
                review=row.review,
            )
            for row in result.rows
            if row.bucket != HERE
        ),
        at=result.at,
    )
    db.put(K_COMPARE, result.record(), now=now)


def take_listing(
    db: PlaudDB,
    cache: Cache,
    listing: Listing,
    *,
    here: Here,
    consent: ConsentList | None,
    words: Sequence[str],
    patterns: Sequence[str],
    gate_open: bool,
    schedule_on: bool = True,
    auto_import: bool = True,
    disk_full: bool = False,
    sync: bool,
    now: datetime,
):
    """One complete listing, as a Compare (`sync` False) or a sync takes it: measured against the
    last good listing (`plaud.hold.measure`), classified, recorded (`record_compare`), and, for a
    sync only, judged and recorded as good, a small shrink or held (`plaud.hold.record_verdict`).
    Returns (Compare, Measure, the listing's outcome for health.RunOutcome: "good",
    "small-shrink" or "held"; for a Compare, what it would be)."""
    from recordings.plaud import hold

    if not listing.complete:
        raise ValueError("only a complete listing is compared (§11.2)")
    before = db.listing_rows()
    excluded = excluded_ids(consent, before)
    measured = hold.measure(listing.forms(), db=db, here=here, excluded=excluded)
    conditions = Conditions(
        gate_open=gate_open,
        held=measured.held,
        ids_changed=measured.ids_changed,
        schedule_on=schedule_on,
        auto_import=auto_import,
        disk_full=disk_full,
    )
    rows = classify(
        listing.rows,
        here=here,
        consent=consent,
        words=words,
        listing=before,
        keyed_out=cache.keyed_out(),
        patterns=patterns,
        conditions=conditions,
    )
    result = compare(
        rows,
        at=listing.at,
        here=here,
        consent=consent,
        conditions=conditions,
        census=listing.census,
        repeats=listing.repeats,
        malformed=listing.malformed,
    )
    record_compare(db, cache, result, consent=consent, listing=before, now=now)
    if sync:
        outcome = hold.record_verdict(db, listing.forms(), measured, at=listing.at, now=now)
    else:
        outcome = hold.verdict(measured)
    return result, measured, outcome


def lines(result: Compare) -> list[str]:
    """The equations as the dry run and the CLI print them (§11.3): counts only, never a title."""
    e, a, c = result.equation, result.archive, result.consent
    listed = (
        f"listed {e['listed']:,} = here {e['here']:,} + waiting {e['waiting']:,} + excluded "
        f"{e['excluded']:,} + missing {e['missing']:,}"
    )
    absent = f" + absent from this listing {a['absent']:,}" if a["absent"] else ""
    archive = (
        f"in the archive from Plaud {a['from_plaud']:,} = here {a['here']:,} + gone from Plaud "
        f"{a['gone']:,}{absent}"
    )
    out = [
        listed,
        archive,
        f"of the waiting and missing, {e['arrive_private']:,} arrive private",
        f"consent list: {c['ids']:,} IDs, {c['on_plaud']:,} on Plaud now",
    ]
    if result.titles_hidden:
        out.append(result.titles_hidden)
    return out


def _confirmed(db: PlaudDB, consent_ids: frozenset[str], *, now: datetime) -> None:
    """A re-keyed match Dan confirmed with `make consent-add` (§11.4 step 6): its open flag is
    resolved once the consent list names the new ID."""
    for event in db.open_flags((REKEYED_EVENT,)):
        if event.canonical_plaud_id in consent_ids:
            db.resolve(event.id, now=now)
