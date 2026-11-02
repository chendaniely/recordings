"""A worker against the fake Plaud server (stage 2b, area A5): an archive, its state, a config.toml
whose `[plaud] api_base` is the fake's, a consent list that reads, a sign-in installed through the
fake's token endpoint, the sync's ping URL on a fake Healthchecks, a fake clock, and the fake's
synthetic account. conftest.py's `live` fixture makes one; the CLI's tests run `recordings plaud
…` against the same config. Every ID, title, token and byte is synthetic, and nothing here reaches
Plaud."""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from recordings.archive import Archive
from recordings.config import load_config
from recordings.init import init_archive
from recordings.plaud import importer
from recordings.plaud.checks import K_CATCH_UP_DONE, PlaudDB
from recordings.plaud.endpoint import TOKEN_PATH
from recordings.plaud.tokens import TokenStore, from_vendor
from recordings.state import State
from recordings.worker import Worker

T0 = datetime(2026, 11, 2, 14, 20, tzinfo=UTC)
NEVER_LISTED = "e" * 32  # the consent list names it, so the list reads cleanly; nobody lists it


class Clock:
    """A fake clock, safe to read from the heartbeat thread too."""

    def __init__(self, now: datetime = T0) -> None:
        self.now = now
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self.now

    def advance(self, **delta: float) -> None:
        with self._lock:
            self.now += timedelta(**delta)


class Live:
    def __init__(self, tmp_path: Path, plaud_server, ntfy, monkeypatch, *, schedule: int = 20):
        self.tmp, self.server, self.ntfy = tmp_path, plaud_server, ntfy
        self.root, self.state_path = tmp_path / "archive", tmp_path / "state"
        init_archive(self.root, self.state_path, writer_id="test")
        self.consent_path = tmp_path / "consent" / "consent-list.md"
        self.consent_path.parent.mkdir()
        self.consent_ids()
        self.token_path = tmp_path / "plaud" / "tokens.json"
        self.cfg_file = tmp_path / "config.toml"
        self.write_config(schedule=schedule)
        self.environ = {
            "RECORDINGS_CONFIG": str(self.cfg_file),
            "RECORDINGS_DEADMAN_SYNC_URL": f"{ntfy.url}/sync",
        }
        self.clock = Clock()
        self.fake = plaud_server.signed_in(self.clock)
        self.account = plaud_server.account()
        folder = self.token_path.parent
        folder.mkdir(mode=0o700)
        folder.chmod(0o700)
        self.store().install(from_vendor(self.fake.issue(), now=T0))
        # The consent file is trusted at once: the tests write it, then read it within a tick.
        monkeypatch.setattr(importer, "SETTLE_SECONDS", 0.0)
        self._workers: list[Worker] = []

    def write_config(self, *, schedule: int = 20, extra: str = "") -> None:
        self.cfg_file.write_text(
            f'[archive]\npath = "{self.root}"\nwriter_id = "test"\n'
            f'default_timezone = "America/Vancouver"\n'
            f'[state]\npath = "{self.state_path}"\n'
            f'[index]\npath = "{self.tmp / "index" / "index.db"}"\n'
            f"[plaud]\nschedule_minutes = {schedule}\n"
            f'consent_list = "{self.consent_path}"\n'
            f'token_store = "{self.token_path}"\n'
            f'api_base = "{self.server.api_url}"\n{extra}',
            encoding="utf-8",
        )

    def store(self) -> TokenStore:
        """The worker's token store, as `[plaud]` names it (the token URL is the API's own)."""
        url = self.server.api_url + TOKEN_PATH
        return TokenStore(self.token_path, token_url=url, clock=self.clock)

    def consent_ids(self, *ids: str, extra: str = "") -> None:
        """Replace the consent list (by rename, as `make consent-add` does)."""
        text = "".join(f"- `of_{i}` — synthetic\n" for i in (NEVER_LISTED, *ids)) + extra
        tmp = self.consent_path.with_name("consent-list.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.consent_path)

    def worker(self, **kwargs) -> Worker:
        w = Worker(
            load_config(self.environ),
            self.environ,
            clock=self.clock,
            heartbeat=3600.0,
            **kwargs,
        )
        self._workers.append(w)
        return w

    def started(self, **kwargs) -> Worker:
        w = self.worker(**kwargs)
        w.start()
        return w

    def catch_up_done(self) -> None:
        """What `sync --catch-up` records at its clean end: the gate opens."""
        PlaudDB(State.open(self.state_path)).put(K_CATCH_UP_DONE, T0.isoformat(), now=T0)

    def db(self) -> PlaudDB:
        return PlaudDB(State.open(self.state_path))

    def here(self) -> dict[str, str]:
        """Each canonical Plaud ID in the archive, and the recording holding it."""
        from recordings.refs import canonical_plaud_id

        found = {}
        for rec in Archive(self.root).iter_recordings():
            for source in rec.sources:
                if source.kind == "plaud":
                    found[canonical_plaud_id(source.ref)] = rec.id
        return found

    def pings(self) -> list[dict]:
        return [r for r in self.ntfy.requests if r["path"] == "/sync"]

    def fetched(self, plaud_id: str) -> list[str]:
        """Every request that named this recording: its envelope, its blocks, its audio."""
        return [r.path for r in self.server.requests if plaud_id in r.path]

    def close(self) -> None:
        for w in self._workers:
            w.stopping.set()
        for w in self._workers:
            if w._beating is not None:
                w._beating.join(5)
