"""Servers for the browser tests. A test that writes gets a server of its own (tagging spec §9),
started with TMPDIR=<its tmp_path>/server, so it finds the demo's temporary copy and reads what the app
wrote there, through Archive."""

import io
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import wave
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from recordings.archive import Archive
from recordings.index import Index
from recordings.init import init_archive
from recordings.models import TagRef
from recordings.sentinel import MIRROR
from recordings.state import State
from recordings.writer import Incoming, Writer
from recordings_ui.settings import DEMO_UUID


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Server:
    url: str
    tmp: Path
    proc: subprocess.Popen
    log: Path

    @property
    def copy(self) -> Path:
        (found,) = self.tmp.glob("recordings-demo-*/archive")
        return found

    def tags(self, rid: str) -> list[tuple[str, str]]:
        """The tags on disk, read through Archive: tag and `by` only (spec §9)."""
        return [(t.tag, t.by) for t in Archive(self.copy).load(rid).tags]

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


def start_server(tmp: Path, archive: Path | None = None) -> Server:
    tmp.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("RECORDINGS_")}
    env["TMPDIR"] = str(tmp)
    if archive is not None:
        env["RECORDINGS_DEMO_ARCHIVE"] = str(archive)
    log = tmp / "server.log"
    with log.open("wb") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "recordings_ui", "--demo", "--port", str(port)],
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while True:
        if proc.poll() is not None:
            tail = log.read_text(errors="replace")[-2000:]
            pytest.fail(f"demo server exited with {proc.returncode}:\n{tail}")
        try:
            with urllib.request.urlopen(f"{url}/healthz", timeout=1):
                break
        except OSError:
            if time.monotonic() > deadline:
                proc.kill()
                tail = log.read_text(errors="replace")[-2000:]
                pytest.fail(f"demo server did not start:\n{tail}")
            time.sleep(0.2)
    return Server(url, tmp, proc, log)


@pytest.fixture
def server(tmp_path):
    started = start_server(tmp_path / "server")
    yield started
    started.stop()


@pytest.fixture
def console(page):
    """Fails the test on any console error or uncaught exception (spec §9: a clean console)."""
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"uncaught: {e}"))
    yield errors
    assert errors == [], f"console errors: {errors}"


def _wav(seed: int) -> bytes:
    """A tenth of a second of quiet, different for each seed: real audio, so a row that opens
    plays without a decode error."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(seed.to_bytes(2, "little") * 800)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def big_archive(tmp_path_factory) -> Path:
    """300 synthetic recordings with the demo's UUID, so demo mode serves them through
    RECORDINGS_DEMO_ARCHIVE (spec §9). 200 untagged; 100 tagged from 20 topics, every tenth of
    those private too. Built through init_archive and Writer.add; invented titles only."""
    base = tmp_path_factory.mktemp("big")
    root = base / "archive"
    init_archive(root, base / "state", writer_id="e2e", archive_uuid=DEMO_UUID)
    writer = Writer(root, State.open(base / "state"), Index(base / "index.db"), writer_id="e2e")
    media = base / "media"
    media.mkdir()
    t0 = datetime.fromisoformat("2026-01-05T09:00:00-08:00")
    for i in range(300):
        path = media / f"{i:03d}.wav"
        path.write_bytes(_wav(i))
        tags: tuple[TagRef, ...] = ()
        if i >= 200:
            tags = (TagRef(tag=f"topic/t{i % 20:02d}", by="you"),)
            if i % 10 == 0:
                tags += (TagRef(tag="private", by="you"),)
        writer.add(
            Incoming(
                media=path,
                recorded_at=t0 + timedelta(hours=i),
                timezone_name="America/Vancouver",
                time_source="metadata",
                title=f"Synthetic {i:03d}",
                kind="audio",
                sources=(),
                tags=tags,
                duration_ms=60_000 + i * 1000,
            )
        )
    return root


@pytest.fixture
def big_server(tmp_path, big_archive):
    started = start_server(tmp_path / "server", archive=big_archive)
    yield started
    started.stop()


@pytest.fixture
def mirror_server(tmp_path, big_archive):
    """The 300-recording archive as the NAS mirror's copy, which no Writer will write (spec §9)."""
    copy = tmp_path / "mirror-archive"
    shutil.copytree(big_archive, copy)
    (copy / MIRROR).write_text("the NAS mirror's copy\n", encoding="utf-8")
    started = start_server(tmp_path / "server", archive=copy)
    yield started
    started.stop()
