"""Plaud as a source (main spec §9.0, stage 2b spec §11.2): the whole listing, and how far to trust
it.

`PlaudSource.list_all` reads the list endpoint page after page through the client (and so through
its Throttle: pacing, the budget, backoff), and returns a `Listing`:
- **It pages until an empty page.** A short page isn't the end: Plaud may clamp page sizes
  (audio-router's lesson, `list_recordings` at 64612df). Reaching `MAX_PAGES` (500) without an
  empty page makes the listing bad.
- **Repeats** (one canonical ID twice, in any form: drift while paging, or a page served again)
  are kept once, the first time seen, and counted.
- **Malformed rows** (no ID, or no title field) are counted. Up to 1% of the rows, and at least
  2, are tolerated; more make the listing bad. A row with an ID but no title is still listed (its
  title is None): only a row with no ID can't be.
- **An HTML page, or JSON of the wrong shape** (`PlaudBadResponse`), is a bad listing, never an
  empty account; so is any other error that is the one call's (a 404, a refused call).
- **An error that ends the run** (`client.ends_the_run`: the sign-in, Plaud unavailable, offline,
  rate limited, the budget spent) after at least one page with rows makes the listing partial: its
  rows feed §13.1's privacy checks only. On the first page it is bad. Either way the exception is
  kept on the listing (`error`), so the run can record the sign-in's refusal or the rate limit's
  wait (§10.2, §10.3).
- **A census of ID forms** (`of_` hex, bare hex, other), counted over the canonical IDs kept.
  "Other" is an ID the consent list can't name (§12.1), which holds the listing (§11.4).

Nothing here decides a bucket or writes anything: that is `plaud.compare` and `plaud.hold`. Titles
stay in memory, on the rows. Imported only by the worker and the CLI: `recordings_ui` never imports
it (`packages/ui/tests/test_plaud_imports.py`).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from recordings.plaud.client import PAGE_SIZE, Listed, PlaudClient, PlaudError, ends_the_run
from recordings.sources.base import Candidate

MAX_PAGES = 500  # §11.2's cap: reaching it without an empty page makes the listing bad
MALFORMED_SHARE = 0.01  # up to 1% of the rows may be malformed…
MALFORMED_AT_LEAST = 2  # …and at least 2
FORMS = ("of_", "hex", "other")
CAP = "the page cap"
TOO_MANY_MALFORMED = "too many malformed rows"
_OF = re.compile(r"of_[0-9a-f]{32}", re.IGNORECASE)
_HEX = re.compile(r"[0-9a-f]{32}", re.IGNORECASE)

Status = Literal["complete", "partial", "bad"]


def id_form(listed_id: str) -> str:
    """The census's form of an ID as listed: `of_` hex, bare hex (either in any case, as the
    consent parser reads them), or other: one the consent list can't name (§12.1)."""
    value = listed_id.strip()
    if _OF.fullmatch(value):
        return "of_"
    if _HEX.fullmatch(value):
        return "hex"
    return "other"


def malformed_limit(rows: int) -> int:
    """How many malformed rows a listing of `rows` rows (malformed ones included) tolerates."""
    return max(MALFORMED_AT_LEAST, int(rows * MALFORMED_SHARE))


class ListingIncomplete(RuntimeError):
    """`candidates()` on a listing that wasn't complete: never "everything the source holds"."""


@dataclass(frozen=True)
class Listing:
    status: Status
    rows: tuple[Listed, ...]  # one per canonical ID, as first listed, in listing order
    at: datetime  # when the listing began (UTC)
    pages: int = 0  # pages read, the empty one included
    repeats: int = 0  # rows dropped as repeats of a canonical ID already listed
    malformed: int = 0  # rows with no ID or no title field
    census: dict[str, int] = field(default_factory=dict)  # form -> IDs, over `rows`
    why: str | None = None  # a bad or partial listing's cause: CAP, TOO_MANY_MALFORMED, a class
    error: BaseException | None = field(default=None, compare=False, repr=False)

    @property
    def complete(self) -> bool:
        return self.status == "complete"

    def forms(self) -> dict[str, str]:
        """Each canonical ID's form, as listed."""
        return {row.canonical_id: id_form(row.listed_id) for row in self.rows}

    def ids(self) -> frozenset[str]:
        return frozenset(row.canonical_id for row in self.rows)


def _census(rows: tuple[Listed, ...]) -> dict[str, int]:
    counts = Counter(id_form(row.listed_id) for row in rows)
    return {form: counts[form] for form in FORMS if counts[form]}


class PlaudSource:
    """Plaud through the read-only client (stage 2b spec §10.1)."""

    name = "plaud"

    def __init__(
        self,
        client: PlaudClient,
        *,
        page_size: int = PAGE_SIZE,
        max_pages: int = MAX_PAGES,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self.page_size = page_size
        self.max_pages = max_pages
        self.clock = clock

    def list_all(self) -> Listing:
        """Every page until an empty one (the module's notes). Raises only what isn't a Plaud
        error (a bug): every PlaudError becomes a bad or partial listing, kept in `error`."""
        at = self.clock()
        kept: dict[str, Listed] = {}
        repeats = malformed = seen = pages = 0
        listed_some = False
        for number in range(1, self.max_pages + 1):
            try:
                page = self.client.list_page(number, self.page_size)
            except PlaudError as exc:
                partial = ends_the_run(exc) and listed_some
                return self._ended(
                    "partial" if partial else "bad", kept, at, pages, repeats, malformed, exc
                )
            pages += 1
            if not page.rows and not page.malformed:
                return self._judged(kept, at, pages, repeats, malformed, seen)
            listed_some = listed_some or bool(page.rows)
            malformed += page.malformed
            # A row with no title field is counted malformed by the client and still listed.
            seen += page.malformed + sum(1 for row in page.rows if row.title is not None)
            for row in page.rows:
                if row.canonical_id in kept:
                    repeats += 1
                else:
                    kept[row.canonical_id] = row
        rows = tuple(kept.values())
        return Listing("bad", rows, at, pages, repeats, malformed, _census(rows), CAP, None)

    @staticmethod
    def _judged(
        kept: dict[str, Listed], at: datetime, pages: int, repeats: int, malformed: int, seen: int
    ) -> Listing:
        rows = tuple(kept.values())
        if malformed > malformed_limit(seen):
            return Listing(
                "bad", rows, at, pages, repeats, malformed, _census(rows), TOO_MANY_MALFORMED
            )
        return Listing("complete", rows, at, pages, repeats, malformed, _census(rows))

    @staticmethod
    def _ended(
        status: Status,
        kept: dict[str, Listed],
        at: datetime,
        pages: int,
        repeats: int,
        malformed: int,
        exc: PlaudError,
    ) -> Listing:
        rows = tuple(kept.values()) if status == "partial" else ()
        return Listing(
            status, rows, at, pages, repeats, malformed, _census(rows), type(exc).__name__, exc
        )

    def candidates(self) -> list[Candidate]:
        """Main spec §9.0's listing, for a source-agnostic reader: the complete listing's rows.
        A bad or partial listing raises ListingIncomplete (from its error, if it had one)."""
        listing = self.list_all()
        if not listing.complete:
            raise ListingIncomplete(listing.why or listing.status) from listing.error
        return [
            Candidate(
                ref=row.listed_id,
                title=row.title,
                recorded_at=row.recorded_at,
                duration_ms=row.duration_ms,
            )
            for row in listing.rows
        ]
