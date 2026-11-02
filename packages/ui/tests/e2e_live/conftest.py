"""The live demo for the browser tests (`make e2e-live`; stage 2b spec §21's "e2e, live"; area A6):
`scripts/demo_live.py`, the same script `make demo-live` runs, with its web app, its worker and the
fake Plaud server, in a scratch folder under each module's own TMPDIR. A test types the demo's
commands (`hold`, `rename`, `tick`, …) on its stdin, as Dan types them, and reads its stdout."""

from __future__ import annotations

import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "demo_live.py"
START_WAIT = 240.0  # seconds: the first catch-up paces one call a second, as the real one does
STOP_WAIT = 45.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class LiveDemo:
    proc: subprocess.Popen
    tmp: Path
    port: int
    lines: list[str] = field(default_factory=list)
    pids: dict[str, int] = field(default_factory=dict)
    _new: queue.Queue = field(default_factory=queue.Queue)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))
            self._new.put(line.rstrip("\n"))

    def wait_for(self, prefix: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                tail = "\n".join(self.lines[-40:])
                pytest.fail(f"demo-live never printed {prefix!r}:\n{tail}")
            try:
                line = self._new.get(timeout=min(left, 1.0))
            except queue.Empty:
                if self.proc.poll() is not None:
                    tail = "\n".join(self.lines[-40:])
                    pytest.fail(f"demo-live exited {self.proc.returncode}:\n{tail}")
                continue
            if line.startswith(prefix):
                return line

    def send(self, command: str, timeout: float = 30.0) -> str:
        """Type a command, and wait for its answer (`ok: …`)."""
        assert self.proc.stdin is not None
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()
        return self.wait_for("ok:", timeout)

    def stray(self) -> list[str]:
        """The children still running, by name."""
        return [name for name, pid in self.pids.items() if alive(pid)]

    def interrupt(self) -> int:
        """Ctrl-C, as a terminal sends it to this process alone (its children have sessions of
        their own); its exit code."""
        self.proc.send_signal(signal.SIGINT)
        try:
            return self.proc.wait(STOP_WAIT)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            pytest.fail("demo-live didn't stop on Ctrl-C")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def start_demo(tmp: Path, *args: str) -> LiveDemo:
    """The demo, started as `make demo-live` starts it, until it prints its URL."""
    tmp.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("RECORDINGS_", "RESTIC_"))}
    env["TMPDIR"] = str(tmp)
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT), "--port", str(port), *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        cwd=REPO,
    )
    demo = LiveDemo(proc, tmp, port)
    threading.Thread(target=demo._read, daemon=True).start()
    started = demo.wait_for("Started:", START_WAIT)
    # "Started: the web app (pid 123) and the worker (pid 456), in …"
    web = int(started.split("(pid ", 1)[1].split(")", 1)[0])
    worker = int(started.split("(pid ", 2)[2].split(")", 1)[0])
    demo.pids = {"web": web, "worker": worker}
    demo.wait_for("Open ", 10)
    return demo


def stop_demo(demo: LiveDemo) -> None:
    if demo.proc.poll() is None:
        demo.interrupt()
    for pid in demo.pids.values():
        if alive(pid):
            os.kill(pid, signal.SIGKILL)


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """The demo after its first catch-up, with a long schedule: scheduled syncs run only when a
    test types `tick`. Its folder is kept (`--keep`), so CI keeps its logs on a failure."""
    started = start_demo(tmp_path_factory.mktemp("demo-live"), "--schedule", "600", "--keep")
    yield started
    stop_demo(started)


@pytest.fixture(scope="module")
def demo_before(tmp_path_factory):
    """The demo before its first catch-up: the gate closed."""
    started = start_demo(tmp_path_factory.mktemp("demo-before"), "--before-catch-up")
    yield started
    stop_demo(started)


@pytest.fixture
def console(page):
    """Fails the test on any console error or uncaught exception (spec §9: a clean console)."""
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"uncaught: {e}"))
    yield errors
    assert errors == [], f"console errors: {errors}"
