"""Servers for the browser tests. A test that writes gets a server of its own (tagging spec §9),
started with TMPDIR=<its tmp_path>, so it finds the demo's temporary copy and reads what the app
wrote there, through Archive."""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest
from recordings.archive import Archive


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
