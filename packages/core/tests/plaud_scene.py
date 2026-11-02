"""A synthetic Plaud scene for the import's tests and the runs built on it (stage 2b, area A4): an
archive and its Writer, state.db with the gate open, the fake server's account, a consent list
on disk, a clock, a disk to fill, and an `Importer` as a run after the first catch-up holds one.
conftest.py's `plaud_scene` fixture makes one. Every ID, title and byte is synthetic."""

from __future__ import annotations

import os
from datetime import UTC, datetime

from recordings.disk import DiskStatus
from recordings.index import Index
from recordings.init import init_archive
from recordings.plaud.cache import Cache
from recordings.plaud.checks import K_CATCH_UP_DONE, PlaudDB
from recordings.plaud.client import PlaudAuthError, PlaudClient
from recordings.plaud.compare import here_from_archive
from recordings.plaud.importer import ConsentGuard, Importer
from recordings.sources.plaud import PlaudSource
from recordings.state import State
from recordings.writer import Writer

T0 = datetime(2026, 11, 2, 14, 20, tzinfo=UTC)
ZONE = "America/Vancouver"
NEVER_LISTED = "f" * 32  # the consent list names it, so the list reads cleanly; nobody lists it


class Tokens:
    def access_token(self) -> str:
        return "synthetic-access-1"

    def refresh(self, rejected: str) -> str:
        raise PlaudAuthError()


class Scene:
    def __init__(self, plaud_server, tmp_path, *, form: str = "of_") -> None:
        self.tmp = tmp_path
        self.root, self.state_path = tmp_path / "archive", tmp_path / "state"
        init_archive(self.root, self.state_path, writer_id="test")
        self.writer = Writer(
            self.root,
            State.open(self.state_path),
            Index(tmp_path / "index" / "index.db"),
            writer_id="test",
        )
        self.db = PlaudDB(self.writer.state)
        self.cache = Cache(self.state_path)
        self.db.put(K_CATCH_UP_DONE, T0.isoformat(), now=T0)
        self.server = plaud_server
        self.account = plaud_server.account(form=form)
        self.client = PlaudClient(Tokens(), api_base=plaud_server.api_url)
        self.consent_path = tmp_path / "consent" / "consent-list.md"
        self.consent_path.parent.mkdir()
        self.consent_ids(NEVER_LISTED)
        self.now = T0
        self.free = 0.5  # the disk's free share
        self.importer = Importer(
            writer=self.writer,
            db=self.db,
            client=self.client,
            consent=ConsentGuard(self.consent_path, settle=0),
            timezone_name=ZONE,
            run_id="run-0001",
            clock=lambda: self.now,
            disk=self.disk,
        )

    def disk(self) -> DiskStatus:
        total = 10**12
        free = int(total * self.free)
        level = "stop" if self.free < 0.05 else "ok"
        return DiskStatus(str(self.root), True, free, total, level, "")

    def consent_ids(self, *ids: str) -> None:
        """Replace the consent list (by rename, as `make consent-add` does) with these IDs."""
        text = "".join(f"- `of_{i}` — synthetic\n" for i in ids)
        tmp = self.consent_path.with_name("consent-list.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.consent_path)

    def consent(self):
        return self.importer.consent.current()

    def listing(self):
        listing = PlaudSource(self.client, clock=lambda: self.now).list_all()
        assert listing.complete
        return listing

    def here(self):
        w = self.writer
        return here_from_archive(w.index, w.identity.uuid, w.state, w.archive, self.db)

    def row(self, rec):
        return next(r for r in self.listing().rows if r.canonical_id == rec.plaud_id)

    def import_(self, rec, **kw):
        listed = self.row(rec)
        return self.importer.import_one(listed, listing_at=self.now, **kw)

    def requested(self, rec) -> list[str]:
        """Every request that named this recording: its envelope, its blocks, its audio."""
        return [r.path for r in self.server.requests if rec.plaud_id in r.path]

    def envelope_requests(self, rec) -> list[str]:
        return [p for p in self.requested(rec) if p.startswith("/open/third-party/files/")]

    def link_requests(self, rec) -> list[str]:
        return [p for p in self.requested(rec) if not p.startswith("/open/third-party/files/")]
