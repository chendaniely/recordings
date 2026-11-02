"""`make demo-live` (stage 2b, area A6; Dan's answer in Phase 1's Q&A §8): `scripts/demo_live.py`
refuses to start unless every setting points into its own scratch folder under $TMPDIR and at its
own fakes, hands its children none of this shell's settings, secrets or proxies, and stops
everything on Ctrl-C. The browser half is `make e2e-live` (packages/ui/tests/e2e_live/)."""

import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "demo_live.py"


@pytest.fixture(scope="module")
def demo_live():
    path = list(sys.path)  # the script puts packages/core/tests on sys.path, for fake_plaud
    spec = importlib.util.spec_from_file_location("demo_live", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = path
    return module


def config(scratch: Path, api: str, **paths: Path) -> Path:
    where = {
        "archive": scratch / "archive",
        "state": scratch / "state",
        "index": scratch / "index" / "index.db",
        "consent": scratch / "consent" / "consent-list.md",
        "tokens": scratch / "plaud" / "tokens.json",
        **paths,
    }
    path = scratch / "config.toml"
    path.write_text(
        f'[archive]\npath = "{where["archive"]}"\nwriter_id = "demo-live"\n'
        f'[state]\npath = "{where["state"]}"\n[index]\npath = "{where["index"]}"\n'
        f'[plaud]\nconsent_list = "{where["consent"]}"\ntoken_store = "{where["tokens"]}"\n'
        f'api_base = "{api}"\n',
        encoding="utf-8",
    )
    return path


def test_it_starts_only_in_its_own_scratch_folder_against_its_own_fakes(
    demo_live, tmp_path, monkeypatch
):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    scratch = tmp_path / "recordings-demo-live-test"
    scratch.mkdir()
    api, ping = "http://127.0.0.1:41001", "http://127.0.0.1:41002/check"

    def refused(cfg: Path, env_extra: dict | None = None, ping_url: str = ping) -> str:
        env = {"RECORDINGS_CONFIG": str(cfg), "RECORDINGS_DEADMAN_SYNC_URL": ping_url}
        env.update(env_extra or {})
        with pytest.raises(demo_live.Refused) as caught:
            demo_live.refuse_unless_scratch(env, scratch, api_port=41001, ping_port=41002)
        return str(caught.value)

    good = config(scratch, api)
    env = {"RECORDINGS_CONFIG": str(good), "RECORDINGS_DEADMAN_SYNC_URL": ping}
    demo_live.refuse_unless_scratch(env, scratch, api_port=41001, ping_port=41002)  # no raise
    elsewhere = tmp_path / "a-real-archive"
    assert "[archive] path" in refused(config(scratch, api, archive=elsewhere))
    assert "[state] path" in refused(config(scratch, api, state=tmp_path / "state"))
    assert "[plaud] token_store" in refused(config(scratch, api, tokens=tmp_path / "t.json"))
    assert "[plaud] consent_list" in refused(config(scratch, api, consent=tmp_path / "c.md"))
    assert "api_base" in refused(config(scratch, "http://127.0.0.1:41999"))
    assert "ping URL" in refused(good, ping_url="https://hc-ping.example.invalid/x")
    # A setting this shell exports wins over config.toml: never handed on, and refused if it is.
    leaked = refused(good, {"RECORDINGS_ARCHIVE": str(elsewhere)})
    assert "RECORDINGS_ARCHIVE" in leaked and "[archive] path" in leaked
    # The scratch folder itself must be under $TMPDIR.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "other"))
    assert "isn't under $TMPDIR" in refused(good)


def test_the_children_get_none_of_this_shells_settings_secrets_or_proxies(
    demo_live, monkeypatch, tmp_path
):
    monkeypatch.setenv("RECORDINGS_ARCHIVE", "/somewhere/real")
    monkeypatch.setenv("RESTIC_PASSWORD", "synthetic")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.invalid")
    env = demo_live.child_environ(tmp_path / "config.toml", "http://127.0.0.1:1/check")
    assert "RECORDINGS_ARCHIVE" not in env and "RESTIC_PASSWORD" not in env
    assert "HTTPS_PROXY" not in env
    assert env["RECORDINGS_CONFIG"] == str(tmp_path / "config.toml")


def test_it_starts_prints_its_url_and_stops_everything_on_a_double_ctrl_c(tmp_path):
    # The start and the clean stop, before the catch-up (the quick start; `make e2e-live` starts
    # it after one too): the URL, then Ctrl-C: the web app and the worker gone, the folder too.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("RECORDINGS_", "RESTIC_"))}
    env.update(TMPDIR=str(tmp_path), PYTHONUNBUFFERED="1")
    with socket_port() as port:
        pass
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT), "--port", str(port), "--before-catch-up"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        cwd=REPO,
    )
    lines: list[str] = []
    pids: list[int] = []
    reader = threading.Thread(target=lambda: lines.extend(proc.stdout), daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + 120
        while not any(line.startswith("Open http://127.0.0.1:") for line in lines):
            assert proc.poll() is None, "".join(lines)
            assert time.monotonic() < deadline, "".join(lines)
            time.sleep(0.2)
        started = next(line for line in lines if line.startswith("Started:"))
        pids += [int(part.split(")")[0]) for part in started.split("(pid ")[1:]]
        assert len(pids) == 2 and all(alive(pid) for pid in pids)
        (scratch,) = tmp_path.glob("recordings-demo-live-*")
        # A terminal's Ctrl-C reaches make, `uv run` and the script together, and `uv run`
        # forwards it again: the second must not cut the stop short (found by hand, through make).
        proc.send_signal(signal.SIGINT)
        time.sleep(0.2)
        proc.send_signal(signal.SIGINT)
        assert proc.wait(60) == 0
        stray = [pid for pid in pids if alive(pid)]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        for pid in pids:  # a failed stop leaves nothing running after the test either
            if alive(pid):
                os.kill(pid, signal.SIGKILL)
    reader.join(5)
    assert stray == []
    assert not scratch.exists()
    assert lines[-1].startswith("Stopped:")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class socket_port:
    """A free port on the loopback, as the e2e servers pick one."""

    def __enter__(self) -> int:
        import socket

        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        return self.sock.getsockname()[1]

    def __exit__(self, *exc) -> None:
        self.sock.close()
