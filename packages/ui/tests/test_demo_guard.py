"""Demo mode never touches real data (spec §17): no real config, archive, state or secret is
opened, no other program runs, and nothing looks up or connects to another machine. Python's
audit hooks see these whoever does them. SQLite opens its files from C, which the "open" event
never sees, so "sqlite3.connect" is watched too."""

import contextlib
import json
import mimetypes
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

import recordings
from fastapi.testclient import TestClient
from recordings.archive import Archive
from recordings_ui import runtime, views
from recordings_ui import settings as settings_module
from recordings_ui.app import create_app
from recordings_ui.settings import find_demo_archive, from_env

PATH_EVENTS = ("open", "sqlite3.connect", "os.scandir", "os.listdir")
ANY_EVENTS = (
    "socket.connect",
    "socket.getaddrinfo",
    "socket.gethostbyname",
    "socket.gethostbyname_ex",
    "socket.gethostbyaddr",
    "socket.getnameinfo",
    "subprocess.Popen",
    "_posixsubprocess.fork_exec",
    "os.exec",
    "os.posix_spawn",
    "os.spawn",
    "os.system",
    "os.fork",
)
# What changes the file system. "open" is a write only when its mode or flags say so; the rest
# always are. The audit event names are Python's own; os.replace emits "os.rename".
WRITE_EVENTS = {
    "os.rename": (0, 1),
    "os.mkdir": (0,),
    "os.remove": (0,),
    "os.rmdir": (0,),
    "shutil.rmtree": (0,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.utime": (0,),
    "os.symlink": (1,),
    "os.link": (0, 1),
    "os.truncate": (0,),
    "shutil.copyfile": (1,),
    "shutil.copymode": (1,),
    "shutil.copystat": (1,),
    "shutil.move": (1,),
}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_events: list[tuple[str, str]] | None = None


def _path(value: str | bytes | os.PathLike) -> str:
    path = os.fsdecode(value)
    if path.startswith("file:"):  # State opens state.db by URI, so mode=rw never creates it
        path = unquote(urlsplit(path).path)
    return path


def _opens_for_writing(args: tuple) -> bool:
    mode = args[1] if len(args) > 1 else None
    flags = args[2] if len(args) > 2 else None
    if isinstance(mode, str) and set(mode) & set("wax+"):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _hook(event: str, args: tuple) -> None:
    if _events is None:
        return
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        if _opens_for_writing(args):
            _events.append(("write:open", _path(args[0])))
    elif event in WRITE_EVENTS:
        for i in WRITE_EVENTS[event]:
            if len(args) > i and isinstance(args[i], (str, bytes, os.PathLike)):
                _events.append(("write:" + event, _path(args[i])))
    if event in PATH_EVENTS:
        if args and isinstance(args[0], (str, bytes, os.PathLike)):
            _events.append((event, _path(args[0])))
    elif event in ANY_EVENTS:
        _events.append((event, repr(args[:2])))


sys.addaudithook(_hook)  # stays for the session; idle unless a test switches it on


@contextmanager
def _watching() -> Iterator[list[tuple[str, str]]]:
    global _events
    _events = events = []
    bytecode = sys.dont_write_bytecode  # compiling a module the first time isn't the demo's write
    sys.dont_write_bytecode = True
    try:
        yield events
    finally:
        sys.dont_write_bytecode = bytecode
        _events = None


def _inside(path: str, root: str) -> bool:
    path, root = os.path.realpath(path), os.path.realpath(root)
    return os.path.commonpath([path, root]) == root


def test_the_hook_sees_every_kind_of_write_but_not_reads(tmp_path):
    # The write guard below is only as good as what the hook sees.
    src, other = tmp_path / "a.txt", tmp_path / "b.txt"
    src.write_text("x", encoding="utf-8")
    with _watching() as events:
        src.read_text(encoding="utf-8")
        os.open(src, os.O_RDONLY | os.O_CLOEXEC) and None
    assert [e for e in events if e[0].startswith("write:")] == []
    with _watching() as events:
        src.open("a", encoding="utf-8").close()
        os.close(os.open(other, os.O_WRONLY | os.O_CREAT))
        os.replace(other, tmp_path / "c.txt")
        os.mkdir(tmp_path / "d")
        os.chmod(src, 0o644)
        os.utime(src)
        os.symlink(src, tmp_path / "s")
        os.link(src, tmp_path / "l")
        os.truncate(src, 0)
        os.remove(tmp_path / "s")
        os.rmdir(tmp_path / "d")
        shutil.rmtree(tmp_path / "gone", ignore_errors=True)
    wrote = {(e[0], os.path.basename(e[1])) for e in events if e[0].startswith("write:")}
    assert wrote >= {
        ("write:open", "a.txt"),
        ("write:open", "b.txt"),
        ("write:os.rename", "b.txt"),
        ("write:os.rename", "c.txt"),
        ("write:os.mkdir", "d"),
        ("write:os.chmod", "a.txt"),
        ("write:os.utime", "a.txt"),
        ("write:os.symlink", "s"),
        ("write:os.link", "l"),
        ("write:os.truncate", "a.txt"),
        ("write:os.remove", "s"),
        ("write:os.rmdir", "d"),
        ("write:shutil.rmtree", "gone"),
    }


def test_the_hook_sees_sqlite_listings_other_programs_and_name_lookups(tmp_path):
    # The guard below is only as good as what the hook sees.
    with _watching() as events:
        sqlite3.connect(tmp_path / "x.db").close()
        os.listdir(tmp_path)
        list(os.scandir(tmp_path))
        subprocess.run([sys.executable, "-c", "pass"], check=True)
        socket.getaddrinfo("localhost", 80)
        socket.gethostbyname("localhost")
        socket.gethostbyname_ex("localhost")
        socket.gethostbyaddr("127.0.0.1")
        socket.getnameinfo(("127.0.0.1", 80), 0)
        os.system("true")
        os.waitpid(os.posix_spawn(sys.executable, [sys.executable, "-c", "pass"], os.environ), 0)
        with contextlib.suppress(OSError):
            os.execv(str(tmp_path / "none"), ["none"])  # the event comes first; the call fails
        if os.fork() == 0:
            os._exit(0)
        os.wait()
    assert ("sqlite3.connect", str(tmp_path / "x.db")) in events
    # os.spawn is Windows-only (POSIX spawnv forks and execs); gethostbyname_ex raises
    # socket.gethostbyname. Both stay watched.
    assert {e[0] for e in events} >= {
        "os.listdir",
        "os.scandir",
        "subprocess.Popen",
        "_posixsubprocess.fork_exec",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.gethostbyaddr",
        "socket.getnameinfo",
        "os.system",
        "os.posix_spawn",
        "os.exec",
        "os.fork",
    }


def test_demo_mode_reads_no_real_paths_secrets_or_network(tmp_path, demo_ids, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    for name in ("config.toml", "secret"):
        (real / name).write_text("x\n", encoding="utf-8")
    environ = {
        "RECORDINGS_CONFIG": str(real / "config.toml"),
        "RECORDINGS_ARCHIVE": str(real / "archive"),
        "RECORDINGS_STATE": str(real / "state"),
        "RECORDINGS_INDEX": str(real / "index.db"),
        "RESTIC_PASSWORD_FILE": str(real / "secret"),
        "RECORDINGS_NTFY_TOPIC_FILE": str(real / "secret"),
        "RECORDINGS_DEADMAN_URL_FILE": str(real / "secret"),
    }
    for name, value in environ.items():  # code reading os.environ itself must find them too
        monkeypatch.setenv(name, value)
    with _watching() as events:
        try:
            settings = from_env(environ, demo=True)
            with TestClient(create_app(settings), base_url="http://localhost") as client:
                assert client.get("/healthz").json() == {"ok": True, "demo": True}
                for rid in demo_ids.values():
                    assert (
                        client.get(f"/media/{rid}", headers={"Range": "bytes=0-9"}).status_code
                        == 206
                    )
                with client.websocket_connect(
                    "ws://localhost/websocket/", headers={"Origin": "http://localhost"}
                ) as ws:
                    # Shiny runs the server function after the client's `init` message, and sends
                    # an output only once the client says it is not hidden. So the guard sees what
                    # the outputs read, init says so for each, and the test waits for their values.
                    wanted = {"library", "status"}  # "recording" needs a selected_id
                    ws.send_text(
                        json.dumps(
                            {
                                "method": "init",
                                "data": {
                                    f".clientdata_output_{name}_hidden": False for name in wanted
                                },
                            }
                        )
                    )
                    seen: set[str] = set()

                    def read() -> None:
                        while not wanted <= seen:
                            seen.update(json.loads(ws.receive_text()).get("values", {}))

                    reader = threading.Thread(target=read, daemon=True)
                    reader.start()
                    reader.join(20)  # receive_text has no timeout: a missing output must not hang
                    assert wanted <= seen, f"the demo's websocket sent only {sorted(seen)}"
                assert runtime.tagging().store.wait_open(30) == (True, None)
            archive = Archive(settings.archive)
            views.library_view(archive)
            for rid in demo_ids.values():
                views.recording_view(archive, rid)
            # What create_app configured for Status in demo mode: if it ever read the real config's
            # [state] path, opening the real state.db would show up in the audit hook's events.
            views.status_view(archive, runtime.status_settings())
        finally:
            runtime.configure(None)
    assert [e for e in events if e[0] in PATH_EVENTS and str(real) in e[1]] == []
    # Demo mode's own Writer (tagging spec §5): its state.db and index were opened, both inside
    # the demo's temporary folder.
    opened = [e[1] for e in events if e[0] == "sqlite3.connect"]
    folder = os.path.realpath(settings.archive.parent)
    assert any(p.endswith("state.db") for p in opened) and any(
        p.endswith("index.db") for p in opened
    )
    assert all(_inside(p, folder) for p in opened)
    # Nothing else either: every path opened is in the demo's copy, the committed demo, the
    # Python install or the UI's own folder (code, and its built front end in www/). This also
    # catches the relative default config.toml and any reading of os.environ that the lines above
    # don't know about. Three kinds of path are allowed by name, found when this was written:
    # the system's MIME tables (mimetypes, for /media), and Shiny's per-session upload folder in
    # the system temp folder (created when a session starts, never written to).
    roots = [
        os.path.realpath(p)
        for p in (
            settings.archive.parent,
            find_demo_archive({}),
            sys.prefix,
            sys.base_prefix,
            Path(views.__file__).parent,
            # The archive's docs, which demo mode's Writer copies into its archive (§6.7): read-only
            # files of the core package (selfdoc reads them with importlib.resources).
            Path(recordings.__file__).parent / "format",
        )
    ]
    mime_tables = {os.path.realpath(p) for p in mimetypes.knownfiles}
    temp = os.path.realpath(tempfile.gettempdir())

    def allowed(path: str) -> bool:
        path = os.path.realpath(path)
        return (
            any(os.path.commonpath([path, r]) == r for r in roots)
            or path in mime_tables
            or (os.path.dirname(path) == temp and os.path.basename(path).startswith("fileupload-"))
        )

    assert [e for e in events if e[0] in PATH_EVENTS and not allowed(e[1])] == []

    # Writes: only inside the demo's temporary folder (the copy, its state, index and locks), or
    # Shiny's per-session upload folder. Nothing in the committed demo, the Python install, the
    # UI's folder or the core package's format/ folder (read-only) changes.
    def may_write(path: str) -> bool:
        real = os.path.realpath(path)
        return _inside(real, str(settings.archive.parent)) or (
            os.path.dirname(real) == temp and os.path.basename(real).startswith("fileupload-")
        )

    writes = [e for e in events if e[0].startswith("write:")]
    assert writes, "the demo's Writer wrote nothing: the hook isn't seeing writes"
    assert [e for e in writes if not may_write(e[1])] == []


def test_demo_mode_removes_its_temporary_copy_at_exit(monkeypatch):
    registered = []
    monkeypatch.setattr(
        settings_module.atexit,
        "register",
        lambda fn, *args, **kwargs: registered.append((fn, args, kwargs)),
    )
    settings = from_env({}, demo=True)
    assert settings.archive.is_dir()
    for fn, args, kwargs in registered:
        fn(*args, **kwargs)
    assert not settings.archive.parent.exists()


def test_demo_mode_removes_its_temporary_copy_on_sigterm(tmp_path):
    # why: docker stop, kill and systemd send SIGTERM. uvicorn puts back the default handler and
    # re-raises, which skips atexit, unless __main__ turns SIGTERM into a normal exit.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    log = tmp_path / "server.log"
    with log.open("wb") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "recordings_ui", "--demo", "--port", str(port)],
            env={**os.environ, "TMPDIR": str(scratch)},
            stdout=out,
            stderr=out,
        )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1):
                    break
            except OSError:
                assert proc.poll() is None and time.monotonic() < deadline, log.read_text()
                time.sleep(0.2)
        assert list(scratch.glob("recordings-demo-*"))  # the copy exists while it runs
    finally:
        proc.terminate()
        proc.wait(timeout=30)
    assert proc.returncode == 128 + signal.SIGTERM
    assert list(scratch.glob("recordings-demo-*")) == []
