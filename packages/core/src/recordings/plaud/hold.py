"""The listing hold (stage 2b spec §11.4): measuring a complete listing against the last good one,
holding it, Accept this listing, and a small shrink settling by itself.

1. **Measure** (`measure`): against the last good listing (`plaud_listed` 'good', its record
   `K_LAST_GOOD_LISTING`): the recordings here it had that this listing lists under none of their
   Plaud IDs (newly absent, with a small shrink still waiting to settle counted again), and apart
   from them the consent-listed and kept-out IDs it had that are gone now; a census change (a form
   it didn't have, or an ID in both listings in another form); IDs in a form the consent list
   can't name; and the overlap, the share of its IDs listed now. With no last good listing (the
   catch-up's, or the first after a rebuild) the archive's Plaud IDs stand in, and only an
   unreadable form or under 80% overlap can hold it.
2. **Held** when any of: under 80% overlap; an unreadable form; a large shrink (more than 5, or 5%
   of the recordings here, whichever is larger, newly absent); a census change with anything
   newly absent. Otherwise good: a census change with nothing absent is a line in Status's details
   and its census the new baseline; a small shrink (1 up to the large one's limit) holds back only
   the marking of those recordings as gone.
3. **A hold** (`record_verdict`) is `K_HOLD` with its sentence and whether Accept may clear it, the
   held listing in `plaud_listed` 'held', and the `hold` blocker, in one transaction. Nothing is
   imported, marked gone or swept meanwhile (the run's part, reading `K_HOLD`). While it shows a
   census change or under 80% overlap, the compare stores and shows no title of a recording not
   here (`plaud.compare`).
4. **Accept this listing** (`accept`; request `accept` and `recordings plaud accept-listing`)
   makes the held listing the last good one, marks its newly absent recordings gone, and lets
   imports resume. It is refused only if a consent-listed or kept-out recording could be imported:
   when such IDs the last good listing held are missing from the held one and it shows a census
   change or under 80% overlap ("Fix this first", checked again against the consent list and the
   kept-out records as they are now), and for an unreadable form ("needs a code fix"). A hold also
   clears by itself when a later listing no longer meets its condition.
5. **A small shrink settles** when a good listing at least an hour after it first showed is missing
   the same recordings: they are marked gone from Plaud (§12.3). A different set starts the wait
   again; one that comes back ends it.

Only a sync records a verdict (`record_verdict`): a Compare measures and shows, and never moves the
last good listing or a hold (§11.6). The thresholds are constants, not settings (§11.4, §22).
Sentences carry counts and fixed phrases, never a title or an ID.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from recordings.plaud.checks import (
    BLOCKER_PREFIX,
    K_HOLD,
    K_LAST_GOOD_LISTING,
    K_SMALL_SHRINK,
    LISTED_GOOD,
    LISTED_HELD,
    PlaudDB,
)
from recordings.plaud.compare import Here, excluded_ids
from recordings.plaud.keepout import ConsentList
from recordings.plaud.when import parse_stamp

OVERLAP_FLOOR = 0.80  # under 80% overlap holds (the `of_` lesson)
LARGE_AT_LEAST = 5  # a large shrink: more than 5 newly absent…
LARGE_SHARE = 0.05  # …or 5% of the recordings here, whichever is larger
SETTLE_AFTER = timedelta(hours=1)  # a small shrink settles on a good listing this much later
OVERLAP, UNREADABLE, LARGE, CENSUS = "overlap", "unreadable", "large-shrink", "census"
HOLD_BLOCKER = BLOCKER_PREFIX + "hold"
# Replies and refusals (§15.6)
ACCEPTED = "Accepted this listing: imports resume"
NOTHING_HELD = "Nothing to accept: no listing is held"
ACCEPT_REFUSED = "Accept refused: fix this first (runbook)"
CODE_FIX = "needs a code fix: the consent list can't name these IDs"
CONSENT_FIRST = "the consent list can't be read: fix it first (runbook)"
# The run's phrases (§15.6)
HELD_IDS = "held: Plaud's IDs changed"
HELD_MANY = "held: many gone from Plaud"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def fix_first(n: int) -> str:
    """§11.4 step 4's refusal, as Status says it."""
    ids = _plural(n, "consent-listed or kept-out ID", "consent-listed or kept-out IDs")
    return f"Fix this first: {ids} vanished while Plaud's IDs changed (runbook)"


def fewer(n: int) -> str:
    """A small shrink waiting to settle: Status's line, and the run's phrase."""
    return f"waiting: Plaud lists {n:,} fewer"


def shrink_line(n: int) -> str:
    return f"Plaud now lists {n:,} fewer: waiting for the next listing to agree."


@dataclass(frozen=True)
class Measure:
    against: str  # "listing" (the last good one) or "archive" (none yet)
    newly_absent: frozenset[str]  # recording IDs here, absent now (a waiting small shrink too)
    excluded_absent: frozenset[str]  # consent-listed or kept-out IDs it had, absent now
    census_change: bool
    unreadable: int  # IDs listed in a form the consent list can't name
    overlap: float  # the share of the baseline's IDs listed now (1.0 for an empty baseline)
    looks_new: int  # IDs listed now that the baseline didn't have
    here_count: int  # recordings in the archive from Plaud

    @property
    def large(self) -> bool:
        return len(self.newly_absent) > max(LARGE_AT_LEAST, LARGE_SHARE * self.here_count)

    @property
    def ids_changed(self) -> bool:
        """A census change or under 80% overlap: titles are hidden while such a listing is held."""
        return self.census_change or self.overlap < OVERLAP_FLOOR

    @property
    def reasons(self) -> tuple[str, ...]:
        found = []
        if self.overlap < OVERLAP_FLOOR:
            found.append(OVERLAP)
        if self.unreadable:
            found.append(UNREADABLE)
        if self.large:
            found.append(LARGE)
        if self.census_change and (self.newly_absent or self.excluded_absent):
            found.append(CENSUS)
        return tuple(found)

    @property
    def held(self) -> bool:
        return bool(self.reasons)

    @property
    def refused(self) -> str | None:
        """Why Accept can't clear this hold, or None (§11.4 step 4)."""
        if self.unreadable:
            return CODE_FIX
        if self.excluded_absent and self.ids_changed:
            return fix_first(len(self.excluded_absent))
        return None

    def sentence(self) -> str:
        """Status's sentence for a hold (§11.4 step 4), counts only."""
        gone = len(self.newly_absent)
        vanished = _plural(gone, "recording here", "recordings here")
        if self.unreadable:
            return (
                f"Plaud sync paused: Plaud lists {_plural(self.unreadable, 'ID', 'IDs')} in a form "
                "the consent list can't name. It needs a code fix (runbook)."
            )
        if self.census_change:
            return (
                f"Plaud sync paused: Plaud's IDs changed form, and {vanished} vanished with them. "
                "Check the runbook's steps, then Accept this listing."
            )
        if self.overlap < OVERLAP_FLOOR:
            share = int(self.overlap * 100)
            what = (
                "the IDs Plaud listed last time"
                if self.against == "listing"
                else "the archive's Plaud IDs"
            )
            tail = f", and {vanished} vanished" if gone else ""
            return (
                f"Plaud sync paused: only {share}% of {what} are listed now{tail}. Check the "
                "runbook's steps, then Accept this listing."
            )
        return (
            f"Plaud now lists {gone:,} fewer of the recordings here (and {self.looks_new:,} look "
            "new). If you deleted them in Plaud, Accept this listing: they show as gone from Plaud."
        )

    def run_phrase(self) -> str | None:
        """The run's §15.6 phrase for this listing, or None when it is plainly good."""
        if self.held:
            return HELD_MANY if self.reasons == (LARGE,) else HELD_IDS
        if self.newly_absent:
            return fewer(len(self.newly_absent))
        return None


def measure_against(
    forms: Mapping[str, str],
    *,
    last_good: Mapping[str, str] | None,
    here: Here,
    excluded: frozenset[str],
    waiting: frozenset[str] = frozenset(),
) -> Measure:
    """§11.4 step 1 (the module's notes). `forms` is this listing's canonical IDs and forms;
    `last_good` the last good listing's, or None when there is none; `excluded` the IDs the sync
    keeps out now; `waiting` a small shrink's recordings still waiting to settle."""
    listed = set(forms)
    unreadable = sum(1 for form in forms.values() if form == "other")
    ids_of = here.ids_of()
    absent_now = {rid for rid, pids in ids_of.items() if not pids & listed}
    if last_good is None:
        baseline = set(here.by_id)
        return Measure(
            against="archive",
            newly_absent=frozenset(),
            excluded_absent=frozenset(),
            census_change=False,
            unreadable=unreadable,
            overlap=len(baseline & listed) / len(baseline) if baseline else 1.0,
            looks_new=len(listed - baseline),
            here_count=len(ids_of),
        )
    before = set(last_good)
    had = {rid for rid, pids in ids_of.items() if pids & before}
    newly = (had & absent_now) | (waiting & absent_now)
    old_forms = set(last_good.values())
    census_change = any(form not in old_forms for form in forms.values()) or any(
        last_good[pid] != form for pid, form in forms.items() if pid in last_good
    )
    return Measure(
        against="listing",
        newly_absent=frozenset(newly),
        excluded_absent=frozenset(pid for pid in before & excluded if pid not in listed),
        census_change=census_change,
        unreadable=unreadable,
        overlap=len(before & listed) / len(before) if before else 1.0,
        looks_new=len(listed - before),
        here_count=len(ids_of),
    )


def _waiting(db: PlaudDB) -> tuple[frozenset[str], datetime | None]:
    value = db.get(K_SMALL_SHRINK)
    if not isinstance(value, dict) or not isinstance(value.get("absent"), list):
        return frozenset(), None
    return frozenset(str(r) for r in value["absent"]), parse_stamp(value.get("since"))


def measure(forms: Mapping[str, str], *, db: PlaudDB, here: Here, excluded: frozenset[str]):
    """`measure_against` the last good listing as state.db records it."""
    last = db.get(K_LAST_GOOD_LISTING)
    waiting, _ = _waiting(db)
    return measure_against(
        forms,
        last_good=db.listed(LISTED_GOOD) if last is not None else None,
        here=here,
        excluded=excluded,
        waiting=waiting,
    )


def verdict(m: Measure) -> str:
    """The listing's outcome (health.Listing): "held", "small-shrink" or "good"."""
    if m.held:
        return "held"
    return "small-shrink" if m.newly_absent else "good"


def _record(forms: Mapping[str, str], at: datetime, census_changed_at: str | None) -> dict:
    """K_LAST_GOOD_LISTING's value: its time, ID count, census, ID-set hash, and when Plaud's IDs
    last changed form with nothing absent (Status's details line)."""
    ids = "\n".join(sorted(forms))
    counts: dict[str, int] = {}
    for form in forms.values():
        counts[form] = counts.get(form, 0) + 1
    return {
        "at": at.isoformat(),
        "count": len(forms),
        "census": counts,
        "hash": hashlib.sha256(ids.encode("utf-8")).hexdigest(),
        "form_changed_at": census_changed_at,
    }


def record_verdict(
    db: PlaudDB, forms: Mapping[str, str], m: Measure, *, at: datetime, now: datetime
) -> str:
    """A sync's complete listing, recorded (steps 2, 3 and 5): held, or the new last good listing
    (with a small shrink waiting or settled). Returns `verdict(m)`."""
    if m.held:
        old = db.get(K_HOLD)
        since = old.get("since") if isinstance(old, dict) else None
        blocker = db.get(HOLD_BLOCKER)
        blocked = blocker.get("since") if isinstance(blocker, dict) else None
        last = db.get(K_LAST_GOOD_LISTING)
        changed = last.get("form_changed_at") if isinstance(last, dict) else None
        record = {
            "since": since if isinstance(since, str) else now.isoformat(),
            "at": at.isoformat(),
            "reasons": list(m.reasons),
            "sentence": m.sentence(),
            "accept": m.refused is None,
            "refused": m.refused,
            "ids_changed": m.ids_changed,
            "unreadable": m.unreadable,
            "census_change": m.census_change,
            "newly_absent": sorted(m.newly_absent),
            "excluded_absent": len(m.excluded_absent),
            "looks_new": m.looks_new,
            "listing": _record(forms, at, at.isoformat() if m.census_change else changed),
        }
        db.set_listed(
            LISTED_HELD,
            forms,
            put={
                K_HOLD: record,
                HOLD_BLOCKER: {
                    "since": blocked if isinstance(blocked, str) else now.isoformat(),
                    "detail": {"why": list(m.reasons)},
                },
            },
            now=now,
        )
        return "held"
    waiting, since = _waiting(db)
    settle = bool(m.newly_absent) and waiting == m.newly_absent and since is not None
    settle = settle and at - since >= SETTLE_AFTER
    if settle:
        db.mark_gone(m.newly_absent, now=now)  # first: a crash after it settles again, harmlessly
    put: dict = {}
    delete = [K_HOLD, HOLD_BLOCKER]
    if m.newly_absent and not settle:
        if waiting != m.newly_absent or since is None:
            put[K_SMALL_SHRINK] = {"absent": sorted(m.newly_absent), "since": at.isoformat()}
    else:
        delete.append(K_SMALL_SHRINK)
    last = db.get(K_LAST_GOOD_LISTING)
    changed = last.get("form_changed_at") if isinstance(last, dict) else None
    put[K_LAST_GOOD_LISTING] = _record(forms, at, at.isoformat() if m.census_change else changed)
    db.set_listed(LISTED_GOOD, forms, put=put, delete=delete, now=now)
    return "small-shrink" if m.newly_absent and not settle else "good"


@dataclass(frozen=True)
class Accepted:
    state: str  # "accepted" | "refused" | "nothing"
    message: str  # a §15.6 reply
    gone: tuple[str, ...] = ()  # recordings marked gone from Plaud
    why: str | None = None  # a refusal's reason, as Status says it


def accept(db: PlaudDB, *, consent: ConsentList | None, now: datetime) -> Accepted:
    """Accept this listing (step 4), checked again against fresh facts: the consent list in force
    and the kept-out records as they are now. The caller holds `plaud`."""
    hold = db.get(K_HOLD)
    if not isinstance(hold, dict):
        return Accepted("nothing", NOTHING_HELD)
    ids_changed = hold.get("ids_changed") is not False  # unreadable reads as changed: fail closed
    if hold.get("unreadable") or not isinstance(hold.get("listing"), dict):
        return Accepted("refused", f"Accept refused: {CODE_FIX}", why=CODE_FIX)
    if ids_changed:
        if consent is None or consent.stop is not None:
            return Accepted("refused", f"Accept refused: {CONSENT_FIRST}", why=CONSENT_FIRST)
        last_good = db.listed(LISTED_GOOD)
        held = db.listed(LISTED_HELD)
        excluded = excluded_ids(consent, db.listing_rows())
        vanished = [pid for pid in last_good if pid in excluded and pid not in held]
        if vanished:
            return Accepted("refused", ACCEPT_REFUSED, why=fix_first(len(vanished)))
    absent = hold.get("newly_absent")
    gone = db.mark_gone([str(r) for r in absent] if isinstance(absent, list) else [], now=now)
    db.promote_held(
        put={K_LAST_GOOD_LISTING: hold["listing"]},
        delete=[K_HOLD, HOLD_BLOCKER, K_SMALL_SHRINK],
        now=now,
    )
    message = ACCEPTED
    if gone:
        message = (
            f"Accepted this listing: {_plural(len(gone), 'recording shows', 'recordings show')} "
            "as gone from Plaud; imports resume"
        )
    return Accepted("accepted", message, gone=tuple(gone))
