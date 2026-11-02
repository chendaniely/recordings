"""The source protocol (main spec §9.0): what the shared ingest and the compare may assume about a
source.

Each input type is its own module under `recordings.sources`. An account-style source (Plaud)
lists what it holds through `candidates()`; a one-shot source (upload, URL; stage 5) returns
nothing there. The compare (main spec §9.1, stage 2b spec §11.3) works for any source that can
list.

Built in stage 2b (its Phase 1 plan, decision 4: a protocol with no implementation would be
untested code) with `sources.plaud` as its first implementation. Main spec §9.0's `fetch()` (media,
the raw payload, the outputs a source already has) comes with the Plaud import (stage 2b spec
§11.5), and stage 2b's `check()` (the current payload of a recording already here, with no media)
with edit-sync (§14.1): each joins this protocol with its first implementation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Candidate:
    """One recording a source holds now, as its listing describes it: metadata only."""

    ref: str  # the source's own reference, exactly as it gave it (opaque)
    title: str | None  # None: the source gave none
    recorded_at: datetime | None  # the capture time when the source knows it
    duration_ms: int | None


@runtime_checkable
class Source(Protocol):
    name: str  # "plaud", "upload", "url", …

    def candidates(self) -> Iterable[Candidate]:
        """Everything the source currently holds. Account-style sources list it; one-shot
        sources return nothing. Raises rather than return a list it can't vouch for: a listing
        that ended part-way is never "everything"."""
        ...
