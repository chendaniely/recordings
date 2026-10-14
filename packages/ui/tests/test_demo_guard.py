"""Demo mode never touches real data (spec §17): no real config, archive, state or secret is
opened, no other program runs, and nothing looks up or connects to another machine. Python's
audit hooks see these whoever does them. SQLite opens its files from C, which the "open" event
never sees, so "sqlite3.connect" is watched too."""

import os
import socket
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient

from recordings.archive import Archive
from recordings_ui import runtime, settings as settings_module, views
from recordings_ui.app import create_app
from recordings_ui.settings import from_env

PATH_EVENTS = ("open", "sqlite3.connect", "os.scandir", "os.listdir")
ANY_EVENTS = ("socket.connect", "socket.getaddrinfo", "subprocess.Popen")
_events: list[tuple[str, str]] | None = None


def _hook(event: str, args: tuple) -> None:
    if _events is None:
        return
    if event in PATH_EVENTS:
        if args and isinstance(args[0], (str, bytes, os.PathLike)):
            _events.append((event, os.fsdecode(args[0])))
    elif event in ANY_EVENTS:
        _events.append((event, repr(args[:2])))


sys.addaudithook(_hook)  # stays for the session; idle unless a test switches it on


@contextmanager
def _watching() -> Iterator[list[tuple[str, str]]]:
    global _events
    _events = events = []
    try:
        yield events
    finally:
        _events = None


def test_the_hook_sees_sqlite_listings_other_programs_and_name_lookups(tmp_path):
    # The guard below is only as good as what the hook sees.
    with _watching() as events:
        sqlite3.connect(tmp_path / "x.db").close()
        os.listdir(tmp_path)
        list(os.scandir(tmp_path))
        subprocess.run([sys.executable, "-c", "pass"], check=True)
        socket.getaddrinfo("localhost", 80)
    assert ("sqlite3.connect", str(tmp_path / "x.db")) in events
    assert {e[0] for e in events} >= {"os.listdir", "os.scandir", "subprocess.Popen",
                                      "socket.getaddrinfo"}


def test_demo_mode_reads_no_real_paths_secrets_or_network(tmp_path, demo_ids):
    real = tmp_path / "real"
    real.mkdir()
    for name in ("config.toml", "secret"):
        (real / name).write_text("x\n", encoding="utf-8")
    environ = {
        "RECORDINGS_CONFIG": str(real / "config.toml"), "RECORDINGS_ARCHIVE": str(real / "archive"),
        "RECORDINGS_STATE": str(real / "state"), "RECORDINGS_INDEX": str(real / "index.db"),
        "RESTIC_PASSWORD_FILE": str(real / "secret"), "RECORDINGS_NTFY_TOPIC_FILE": str(real / "secret"),
        "RECORDINGS_DEADMAN_URL_FILE": str(real / "secret"),
    }
    with _watching() as events:
        try:
            settings = from_env(environ, demo=True)
            with TestClient(create_app(settings), base_url="http://localhost") as client:
                assert client.get("/healthz").json() == {"ok": True, "demo": True}
                for rid in demo_ids.values():
                    assert client.get(f"/media/{rid}", headers={"Range": "bytes=0-9"}).status_code == 206
                with client.websocket_connect("ws://localhost/websocket/",
                                              headers={"Origin": "http://localhost"}):
                    pass  # a Shiny session starts
            archive = Archive(settings.archive)
            views.library_view(archive)
            for rid in demo_ids.values():
                views.recording_view(archive, rid)
        finally:
            runtime.configure(None)
    assert [e for e in events if e[0] in PATH_EVENTS and str(real) in e[1]] == []
    assert [e for e in events if e[0] in ANY_EVENTS] == []


def test_demo_mode_removes_its_temporary_copy_at_exit(monkeypatch):
    registered = []
    monkeypatch.setattr(settings_module.atexit, "register",
                        lambda fn, *args, **kwargs: registered.append((fn, args, kwargs)))
    settings = from_env({}, demo=True)
    assert settings.archive.is_dir()
    for fn, args, kwargs in registered:
        fn(*args, **kwargs)
    assert not settings.archive.parent.exists()
