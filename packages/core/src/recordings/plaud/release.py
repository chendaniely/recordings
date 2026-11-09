"""Release (stage 2b spec §12.2, §11.4 step 6): letting in a recording the sync keeps out, after
Dan has seen its title in Release's own question.

- **What Release applies to:** an ID kept out by an earlier title ("was kept out by title": its
  `plaud_listing.kept_out_at` is set and not released), and a possible re-keyed match (`rekey_of`,
  §11.4 step 6) that is a different recording. Never a consent-listed ID, never one already here
  (a late keep-out has Keep it here instead), and never during a consent stop.
- **The preview** (`release-preview`): the worker answers with the recording's current title,
  from the run's newest listing in memory or a fresh one, written to the cache only, keyed by the
  request's own ID (`Cache.put_preview`); the web app shows it once (`cache.take_preview`). It is
  refused, with no title fetched, for a consent-listed ID, during a consent stop, while the gate
  is closed (the worker refuses the kind), and while the title still carries a keep-out word
  (Release would be refused anyway, and that title is never shown). Since the end-of-branch
  privacy review (2026-11-02) it is also refused, with no title fetched, for a re-keyed match
  (NO_TITLE_REKEYED: its title is never stored or shown, §11.4 step 6, so Release's question
  asks by ID, date and duration and goes straight to Let it in), and while Plaud's IDs look
  changed (NO_TITLE_HELD, `titles_hidden`: §11.4 step 3), a review row's Show title included.
- **Let it in** (`release`): checked again when the worker claims it: the consent list as it is
  now, and the current title against every enforced word (a re-keyed match's too, its title
  read and never returned or stored). Refused with the reason ("Release refused: its title still
  starts with `noarchive`", "Release refused: it is on the consent list"). Otherwise
  `released_at` is now, so the compare reads it as waiting and the next sync imports it (§11.3,
  `compare._released`). A re-keyed match's open flag resolves: Dan has decided it is a different
  recording.
- **Undo** (`unrelease`): until a sync has fetched it, `released_at` goes and it is kept out
  again (a re-keyed match is flagged again).
- **The CLI** (`recordings plaud release <plaud-id> [--undo]`) asks y/N on the terminal, showing
  the ID, date and duration only: never a title.

Network-free: the worker's handlers and the CLI fetch the listing themselves (they import the
client inside the function, so the worker never loads it at module level). Every reply is a
fixed phrase (§15.6); no title is logged or stored outside the cache's preview.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from recordings.plaud.checks import K_COMPARE, K_HOLD, ListingRow, PlaudDB
from recordings.plaud.compare import REKEYED_EVENT, SEVERAL, TITLES_HIDDEN_IDS, _released
from recordings.plaud.keepout import ConsentList, keep_out_match
from recordings.refs import canonical_plaud_id

REFUSED_CONSENT = "Release refused: it is on the consent list"
REFUSED_STOP = "Release refused: the consent list can't be read: fix it first (runbook)"
REFUSED_HERE = "Release doesn't apply: it is already in the archive (Keep it here, on its flag)"
NOTHING = "Nothing to release: it isn't kept out"
NOT_LISTED = "Release refused: Plaud doesn't list it now"
ASK = "Release: see its question"  # the preview's reply line: the title goes to the cache only
# No title is fetched for a re-keyed match (§11.4 step 6): Release asks by ID, date and duration.
NO_TITLE_REKEYED = "No title shown: it may be a consent-listed or kept-out recording under a new ID"
# Nor while Plaud's IDs look changed (§11.4 step 3): a held listing, or the newest Compare's.
NO_TITLE_HELD = "No title shown while Plaud's IDs look changed: see Status"
UNDONE = "Undone: kept out again"
TOO_LATE = "Too late to undo: it has come in"
NOT_RELEASED = "Nothing to undo: it isn't released"
ONE_ID = "Release takes one Plaud ID"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def refused_word(word: str) -> str:
    return f"Release refused: its title still starts with `{word}`"


def released_line(next_sync: str | None) -> str:
    """§15.6's "Released · comes in at the next sync (14:40) · Undo"."""
    when = f" ({next_sync})" if next_sync else ""
    return f"Released · comes in at the next sync{when} · Undo"


def rekeyed(row: ListingRow | None) -> bool:
    """A possible re-keyed match not released (§11.4 step 6): its title is never fetched, stored
    or shown. Read from `rekey_of` itself, never from `kept_by`, which reads "was-title" first: a
    re-keyed match whose title later took the keep-out word, then lost it, carries both."""
    return row is not None and row.rekey_of is not None and not _released(row)


def kept_by(row: ListingRow | None) -> str | None:
    """What keeps this ID out that Release can lift: "was-title", "rekeyed", or None."""
    if row is None or _released(row):
        return None
    if row.kept_out_at is not None:
        return "was-title"
    if row.rekey_of is not None:
        return "rekeyed"
    return None


@dataclass(frozen=True)
class Verdict:
    ok: bool
    message: str  # a fixed phrase: never a title


def titles_hidden(db: PlaudDB) -> bool:
    """§11.4 step 3: no title of a recording not here is stored or shown while a held listing
    shows Plaud's IDs changed (a census change or under 80% overlap; a hold whose record can't
    say reads as changed, failing closed), nor while the newest Compare's own measure says so."""
    hold = db.get(K_HOLD)
    if hold is not None and (not isinstance(hold, dict) or hold.get("ids_changed") is not False):
        return True
    compared = db.get(K_COMPARE)
    return isinstance(compared, dict) and compared.get("titles_hidden") == TITLES_HIDDEN_IDS


def may_ask(pid: str, consent: ConsentList | None, db: PlaudDB, *, here: bool) -> Verdict:
    """Whether Release applies to this ID at all, before any listing is read: never during a
    consent stop, for a consent-listed ID, one already here, or one nothing keeps out."""
    pid = canonical_plaud_id(pid)
    if consent is None or consent.stop is not None:
        return Verdict(False, REFUSED_STOP)
    if pid in consent.ids:
        return Verdict(False, REFUSED_CONSENT)
    if here:
        return Verdict(False, REFUSED_HERE)
    if kept_by(db.listing(pid)) is None:
        return Verdict(False, NOTHING)
    return Verdict(True, ASK)


def may_preview(
    pid: str,
    consent: ConsentList | None,
    db: PlaudDB,
    *,
    here: bool,
    review_ok: bool = True,
) -> Verdict:
    """Whether a preview may fetch this ID's title at all (§12.2 step 1), before any listing is
    read: no title is fetched for a consent-listed ID or during a consent stop; none for a
    re-keyed match, whose title is never stored or shown (§11.4 step 6: Release asks by ID, date
    and duration, and goes straight to Let it in); and none while Plaud's IDs look changed
    (`titles_hidden`). A review row (§11.6, "its title on demand, through Release's preview") may
    be previewed too."""
    pid = canonical_plaud_id(pid)
    row = db.listing(pid)
    review = review_ok and row is not None and row.needs_review
    verdict = may_ask(pid, consent, db, here=here)
    if not verdict.ok and not (review and verdict.message == NOTHING):
        return verdict
    if rekeyed(row):  # its field, whatever else keeps it out
        return Verdict(False, NO_TITLE_REKEYED)
    if titles_hidden(db):
        return Verdict(False, NO_TITLE_HELD)
    return Verdict(True, ASK)


def may_release(
    pid: str,
    title: str | None,
    consent: ConsentList | None,
    words: tuple[str, ...],
    db: PlaudDB,
    *,
    here: bool,
) -> Verdict:
    """§12.2 step 3's re-check when the worker claims `release`, against the consent list as it
    is now and the current title (from a listing) against every enforced word. A re-keyed match
    is checked the same way, its title read and never returned or stored."""
    verdict = may_ask(pid, consent, db, here=here)
    if not verdict.ok:
        return verdict
    word = keep_out_match(title, words)
    if word is not None:
        return Verdict(False, refused_word(word))
    return Verdict(True, released_line(None))


def release(db: PlaudDB, plaud_id: str, *, now: datetime) -> str:
    """Record the release (the caller has checked `may_release`): `released_at` now, a
    `released` event, and a re-keyed match's open flag resolved. Returns what kept it out."""
    pid = canonical_plaud_id(plaud_id)
    kind = kept_by(db.listing(pid)) or "was-title"
    db.set_listing(plaud_id, released_at=now)
    db.add_event("released", now=now, plaud_id=pid, detail={"of": kind})
    for event in db.open_flags((REKEYED_EVENT,)):
        if event.canonical_plaud_id == pid:
            db.resolve(event.id, now=now)
    return kind


def unrelease(db: PlaudDB, plaud_id: str, *, here: bool, now: datetime) -> str:
    """Undo (§12.2 step 4): kept out again, until a sync has fetched it. A re-keyed match gets
    its flag back. Returns UNDONE, TOO_LATE or NOT_RELEASED."""
    pid = canonical_plaud_id(plaud_id)
    if here:
        return TOO_LATE
    row = db.listing(pid)
    if row is None or not _released(row) or (row.kept_out_at is None and row.rekey_of is None):
        return NOT_RELEASED
    db.set_listing(plaud_id, released_at=None)
    if row.kept_out_at is None and row.rekey_of is not None:
        earlier = [
            e
            for e in db.events_since(EPOCH, (REKEYED_EVENT,))
            if e.canonical_plaud_id == pid and isinstance(e.detail.get("of"), str)
        ]
        several = "several" if row.rekey_of == SEVERAL else "kept-out"
        of = earlier[-1].detail["of"] if earlier else several
        db.add_event(REKEYED_EVENT, now=now, plaud_id=pid, detail={"of": of}, open_flag=True)
    return UNDONE
