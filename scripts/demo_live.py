"""`make demo-live`: the web app and the Plaud worker on this machine, against the fake Plaud
server, with a synthetic account and a scratch archive (stage 2b, area A6; Dan's answer in Phase 1's
Q&A §8: "should have live and github actions github pages demo").

    uv run python scripts/demo_live.py [--port 8000] [--schedule 20] [--before-catch-up] [--keep]

What it starts, every one on 127.0.0.1:
- the fake Plaud server (`packages/core/tests/fake_plaud.py`), in this process: its API and storage
  hosts, a sign-in, and a synthetic account (every title, ID, token and byte invented);
- a fake Healthchecks for the sync's ping, in this process;
- the first catch-up, `recordings plaud sync --catch-up`, unless `--before-catch-up`;
- `recordings worker` and `recordings-ui`, each a process of its own, in its own session, so
  Ctrl-C reaches this script alone and it stops them in order.

**Never a real archive, state folder, sign-in or Plaud.** Everything lives in one folder made under
`$TMPDIR` (`recordings-demo-live-…`), removed at the end unless `--keep`. The children get a
`config.toml` written there and an environment with every `RECORDINGS_*`, `RESTIC_*` and proxy
variable taken out, so nothing in this shell can point them elsewhere; before anything starts,
`refuse_unless_scratch` loads that config the way the children will and refuses to go on if any
path is outside the folder, or the Plaud API, its token URL or the ping is anything but this
process's own loopback servers.

While it runs, type a command and Enter (`help` lists them): `new` (two recordings arrive on
Plaud), `hold` (Plaud stops listing six recordings that are here: the next sync holds), `back`
(they come back), `rename` (the kept-out recording's title loses the keep-out word, so Release
is offered), `late` (a recording here gets the keep-out word in its title: a late keep-out),
`signout` and `signin` (Plaud refuses the sign-in; a new one), `tick` (the scheduled sync runs at
the worker's next tick, as if its interval had passed). Ctrl-C, or `quit`, stops everything.

The e2e-live tests (`make e2e-live`, packages/ui/tests/e2e_live/) start this script the same way
and type the same commands.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / "packages" / "core" / "tests"
sys.path.insert(0, str(TESTS))  # fake_plaud.py: importable from outside tests, no pytest in it

from fake_plaud import FakePlaud, FakeRecording  # noqa: E402
from recordings.config import load_config  # noqa: E402
from recordings.init import init_archive  # noqa: E402
from recordings.plaud.checks import K_LAST_ENDED, K_LAST_STARTED, PlaudDB  # noqa: E402
from recordings.plaud.endpoint import is_loopback  # noqa: E402
from recordings.plaud.settings import plaud_settings  # noqa: E402
from recordings.state import State  # noqa: E402

WORD = "noarchive"  # the template's keep-out word
CHECK_PATH = "/demo-live-sync-check"  # the fake Healthchecks' one check
HIDDEN = ("RECORDINGS_", "RESTIC_")  # this shell's settings and secrets never reach the children
STOP_WAIT = 15.0  # seconds a child has to stop after SIGTERM, before SIGKILL
READY_WAIT = 120.0  # seconds the web app has to answer /healthz
# The synthetic account (all invented): what the first catch-up brings in, one consent-listed, one
# whose title starts with the keep-out word, and one that arrives private.
BEFORE = (
    "Synthetic weekly standup",
    "Synthetic lecture, week 3",
    "Synthetic call with the lab",
    "Synthetic reading group",
    "Synthetic office hours",
    "Synthetic planning session",
)
PRIVATE = "Private: synthetic one-to-one"
CONSENTED = "Synthetic guest talk (on the consent list)"
KEPT = f"{WORD} synthetic doctor's appointment"
ARRIVING = ("Synthetic seminar, today", "Synthetic standup, today", "Private: synthetic catch-up")
HOLD_COUNT = 6  # more than 5 newly absent: a large shrink, which holds (§11.4)

TRY = """\
What to try (spec §15.1, J6):
  - Sync now: the three recordings waiting come in (one arrives private); watch Not here yet.
  - Kept out: one by the consent list, one by its title. Neither shows a title anywhere.
  - Type `hold`, then Sync now or `tick`: Plaud lists 6 fewer, the sync holds, and Accept this
    listing on the Sync screen or Status marks them gone from Plaud. `back` undoes it.
  - Type `rename`, then Compare: the kept-out recording was kept out by title, and Release asks
    you first, with its title; Let it in, then Sync now.
  - Type `late`, then Sync now: a recording here is kept out late (a flag, Keep it here).
  - Type `signout`, then `tick`: Status says the sign-in is needed. `signin` mends it.
Commands: new, hold, back, rename, late, signout, signin, tick, catch-up, dump, help, quit (or
Ctrl-C). `dump` writes the screens' data as JSON (start with --keep to keep it)."""


def say(text: str) -> None:
    print(text, flush=True)


class Refused(SystemExit):
    """A setting points outside the scratch folder, or at anything but the fakes: nothing starts."""


# ---- the guard ------------------------------------------------------------------------------
def _inside(path: Path | None, folder: Path) -> bool:
    if path is None:
        return False
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


def _ours(url: str, port: int) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "http" and is_loopback(parts.hostname) and parts.port == port


def refuse_unless_scratch(
    environ: dict[str, str], scratch: Path, *, api_port: int, ping_port: int
) -> None:
    """Load the config as the children will (`environ` is theirs) and refuse unless every path is
    inside `scratch`, `scratch` is inside $TMPDIR, and Plaud, its token URL and the ping are this
    process's own loopback fakes. Nothing has started when it refuses."""
    problems = []
    if not _inside(scratch, Path(tempfile.gettempdir())):
        problems.append(f"the scratch folder {scratch} isn't under $TMPDIR")
    leaked = sorted(k for k in environ if k.startswith(HIDDEN) and k not in CHILD_KEYS)
    if leaked:
        problems.append(f"the children's environment still holds {', '.join(leaked)}")
    cfg = load_config(environ)
    ps = plaud_settings(cfg)
    for name, path in (
        ("[archive] path", cfg.archive_path),
        ("[state] path", cfg.state_path),
        ("[index] path", cfg.index_path),
        ("[plaud] consent_list", ps.consent_list),
        ("[plaud] token_store", ps.token_store),
    ):
        if not _inside(path, scratch):
            problems.append(f"{name} ({path}) is outside the scratch folder {scratch}")
    if not _ours(ps.api_base, api_port):
        problems.append(f"[plaud] api_base ({ps.api_base}) isn't the fake Plaud server")
    if not _ours(ps.token_url, api_port):
        problems.append(f"[plaud] token_url ({ps.token_url}) isn't the fake Plaud server")
    ping = environ.get("RECORDINGS_DEADMAN_SYNC_URL", "")
    if not _ours(ping, ping_port):
        problems.append("the sync's ping URL isn't the demo's own fake Healthchecks")
    if problems:
        raise Refused("make demo-live refused to start:\n  " + "\n  ".join(problems))


CHILD_KEYS = frozenset({"RECORDINGS_CONFIG", "RECORDINGS_DEADMAN_SYNC_URL"})


def child_environ(config: Path, ping_url: str) -> dict[str, str]:
    """This shell's environment without its settings, secrets and proxies, plus the demo's own
    config and ping URL."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(HIDDEN) and not k.lower().endswith("_proxy")
    }
    env["RECORDINGS_CONFIG"] = str(config)
    env["RECORDINGS_DEADMAN_SYNC_URL"] = ping_url
    return env


def local_zone() -> str:
    """This machine's time zone, as an IANA name, so the app shows local times; UTC otherwise."""
    from zoneinfo import ZoneInfo

    for name in (os.environ.get("TZ", "").lstrip(":"), _etc_localtime()):
        if name:
            try:
                ZoneInfo(name)
            except (ValueError, KeyError, OSError):
                continue
            return name
    return "UTC"


def _etc_localtime() -> str:
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return ""
    return target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else ""


# ---- the fake Healthchecks --------------------------------------------------------------------
class FakeChecks:
    """Answers every ping 200 and counts them: Status then shows "last pinged 14:40"."""

    def __init__(self) -> None:
        self.pings = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _ping(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                outer.pings += 1
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"OK")

            do_GET = do_POST = do_HEAD = _ping

            def log_message(self, *args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.url = f"http://127.0.0.1:{self.port}{CHECK_PATH}"
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# ---- the demo -----------------------------------------------------------------------------------
class Demo:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.scratch = Path(tempfile.mkdtemp(prefix="recordings-demo-live-"))
        self.logs = self.scratch / "logs"
        self.logs.mkdir()
        self.plaud = FakePlaud()
        self.checks = FakeChecks()
        self.sign_in = self.plaud.signed_in()  # real time: the worker's own clock
        self.account = self.plaud.account()
        self.children: dict[str, subprocess.Popen] = {}
        self.held: list[FakeRecording] = []
        self.before: list[str] = []  # the Plaud IDs the first catch-up brings in, oldest first
        self.stopping = threading.Event()
        self._stopped = False
        self.made = 0  # `new`'s count, for its titles
        self.started_at = datetime.now(UTC)  # the arrivals cue counts from here
        root = self.scratch
        self.archive, self.state = root / "archive", root / "state"
        self.consent = root / "consent" / "consent-list.md"
        self.tokens = root / "plaud" / "tokens.json"
        self.config = root / "config.toml"
        self.env = child_environ(self.config, self.checks.url)
        self.port = args.port
        self.url = f"http://127.0.0.1:{self.port}/"

    # ---- setting up ------------------------------------------------------------------------
    def write_config(self) -> None:
        self.config.write_text(
            "# make demo-live's own config: a scratch archive and the fake Plaud server only.\n"
            f'[archive]\npath = "{self.archive}"\nwriter_id = "demo-live"\n'
            f'default_timezone = "{local_zone()}"\n'
            f'[state]\npath = "{self.state}"\n'
            f'[index]\npath = "{self.scratch / "index" / "index.db"}"\n'
            f"[plaud]\nschedule_minutes = {self.args.schedule}\n"
            f'keep_out_word = "{WORD}"\n'
            f'consent_list = "{self.consent}"\n'
            f'token_store = "{self.tokens}"\n'
            f'api_base = "{self.plaud.api_url}"\n',
            encoding="utf-8",
        )

    def fill_account(self) -> None:
        now = datetime.now(UTC)
        start = now - timedelta(days=len(BEFORE) + 3)
        for i, title in enumerate((*BEFORE, PRIVATE, CONSENTED, KEPT)):
            self.account.add(
                title,
                start_at=start + timedelta(days=i, hours=1),
                duration_ms=(12 + 7 * i) * 60_000,
            )
        consented = next(r for r in self.account.recordings if r.name == CONSENTED)
        self.before = [r.plaud_id for r in self.account.recordings if r.name in (*BEFORE, PRIVATE)]
        self.consent.parent.mkdir()
        self.consent.write_text(
            "# The demo's consent list: one synthetic ID, never mirrored.\n"
            f"- `of_{consented.plaud_id}` — the synthetic guest talk\n",
            encoding="utf-8",
        )

    def setup(self) -> None:
        self.write_config()
        api_port = urlsplit(self.plaud.api_url).port or 0
        refuse_unless_scratch(self.env, self.scratch, api_port=api_port, ping_port=self.checks.port)
        init_archive(self.archive, self.state, writer_id="demo-live")
        self.fill_account()
        folder = self.tokens.parent
        folder.mkdir(mode=0o700)
        folder.chmod(0o700)
        self.import_token()

    def recordings_cli(self) -> list[str]:
        found = Path(sys.executable).with_name("recordings")
        if found.is_file():
            return [str(found)]
        code = "import sys; from recordings.cli import main; sys.exit(main(sys.argv[1:]))"
        return [sys.executable, "-c", code]

    def import_token(self) -> None:
        """A sign-in, as `make plaud-sign-in` installs one: the fake's vendor-like file through
        `recordings plaud token-import -`, which checks it with one call."""
        vendor = json.dumps(self.sign_in.issue()).encode()
        done = subprocess.run(
            [*self.recordings_cli(), "plaud", "token-import", "-"],
            input=vendor,
            env=self.env,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if done.returncode != 0:
            raise SystemExit(f"make demo-live: the demo's sign-in failed (exit {done.returncode})")

    def catch_up(self) -> None:
        n = len(BEFORE) + 1
        say(f"Running the first catch-up: {n} recordings, about {3 * n + 5} seconds (the real")
        say("pacing: one call a second)…")
        with (self.logs / "catch-up.log").open("wb") as out:
            done = subprocess.run(
                [*self.recordings_cli(), "plaud", "sync", "--catch-up"],
                env=self.env,
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=600,
                check=False,
                start_new_session=True,
            )
        if done.returncode != 0:
            raise SystemExit(
                f"make demo-live: the first catch-up exited {done.returncode}: see "
                f"{self.logs / 'catch-up.log'}"
            )

    # ---- the processes -----------------------------------------------------------------------
    def spawn(self, name: str, argv: list[str]) -> None:
        out = (self.logs / f"{name}.log").open("wb")
        self.children[name] = subprocess.Popen(
            argv, env=self.env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True
        )
        out.close()

    def start(self) -> None:
        self.spawn("worker", [*self.recordings_cli(), "worker"])
        self.spawn(
            "web",
            [
                sys.executable,
                "-m",
                "recordings_ui",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
        )
        deadline = time.monotonic() + READY_WAIT
        while True:
            for name, proc in self.children.items():
                if proc.poll() is not None:
                    raise SystemExit(
                        f"make demo-live: the {name} stopped (exit {proc.returncode}): see "
                        f"{self.logs / (name + '.log')}"
                    )
            try:
                with urllib.request.urlopen(f"{self.url}healthz", timeout=1):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise SystemExit("make demo-live: the web app didn't start in time") from None
                time.sleep(0.25)

    def wait_first_turn(self) -> None:
        """The worker's first scheduled sync, which runs at its first tick: the arrivals come
        after it, so they wait for Sync now (or the next scheduled sync)."""
        if self.args.before_catch_up or self.args.schedule <= 0:
            return
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not self.stopping.is_set():
            try:
                if PlaudDB(State.read_only(self.state)).get(K_LAST_ENDED) is not None:
                    return
            except Exception:  # noqa: BLE001  state.db busy for a moment: look again
                pass
            time.sleep(0.5)

    def arrive(self, titles: tuple[str, ...] = ARRIVING) -> None:
        now = datetime.now(UTC)
        for i, title in enumerate(titles):
            self.account.add(
                title, start_at=now - timedelta(minutes=30 - 10 * i), duration_ms=(5 + i) * 60_000
            )

    def stop(self) -> None:
        """SIGTERM to each child (the web app first, then the worker, which stops between two
        recordings), SIGKILL after STOP_WAIT, then the fakes; the scratch folder goes unless
        --keep. Once only."""
        if self._stopped:
            return
        self._stopped = True
        self.stopping.set()
        for name in ("web", "worker"):
            proc = self.children.get(name)
            if proc is None or proc.poll() is not None:
                continue
            proc.terminate()
            try:
                proc.wait(STOP_WAIT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        self.plaud.close()
        self.checks.close()
        if self.args.keep:
            say(f"Kept {self.scratch} (logs in {self.logs}).")
        else:
            shutil.rmtree(self.scratch, ignore_errors=True)
        say("Stopped: the web app, the worker and the fake Plaud server.")

    # ---- the commands ------------------------------------------------------------------------
    def listed_before(self) -> list[FakeRecording]:
        """What the first catch-up brought in and Plaud still lists, oldest first."""
        listed = {r.plaud_id: r for r in self.account.recordings}
        return [listed[pid] for pid in self.before if pid in listed]

    def command(self, line: str) -> bool:
        """One typed command; False to stop."""
        word = line.strip().lower()
        if not word:
            return True
        if word in ("quit", "exit", "stop"):
            return False
        if word == "help":
            say(TRY)
        elif word == "new":
            self.made += 1
            self.arrive((f"Synthetic note {self.made}", f"Private: synthetic call {self.made}"))
            say("ok: two recordings arrive on Plaud (one private): Compare or Sync now")
        elif word == "hold":
            if self.held:
                say("ok: already held back; `back` returns them")
                return True
            self.held = self.listed_before()[:HOLD_COUNT]
            for rec in self.held:
                self.account.remove(rec.plaud_id)
            say(f"ok: Plaud stops listing {len(self.held)} recordings: the next sync holds")
        elif word == "back":
            self.account.restore(self.held)
            say(f"ok: Plaud lists {len(self.held)} recordings again")
            self.held = []
        elif word == "rename":
            kept = next((r for r in self.account.recordings if r.name == KEPT), None)
            if kept is None:
                say("ok: nothing to rename (already renamed)")
                return True
            self.account.rename(kept.plaud_id, "Synthetic doctor's appointment")
            say("ok: the kept-out recording's title lost the keep-out word: Compare, then Release")
        elif word == "late":
            target = next(iter(self.listed_before()[-1:]), None)
            if target is None:
                say("ok: nothing here to rename")
                return True
            self.account.rename(target.plaud_id, f"{WORD} {target.name}")
            say("ok: a recording here now starts with the keep-out word: Sync now")
        elif word == "signout":
            self.sign_in.revoke()
            self.sign_in.refuse_refresh()
            say("ok: Plaud refuses the sign-in: `tick` or Sync now, then see Status")
        elif word == "signin":
            self.sign_in.refuse = None
            self.import_token()
            say("ok: signed in again: the next sync uses it")
        elif word == "tick":
            self.make_due()
            say("ok: the scheduled sync runs at the worker's next tick")
        elif word == "catch-up":
            self.catch_up()
            say("ok: the first catch-up is done: the scheduled sync starts by itself")
        elif word == "dump":
            say(f"ok: the screens' data, in the canned view's shape: {self.dump()}")
        else:
            say(f"unknown command {word!r}: type help")
        return True

    def dump(self) -> Path:
        """The Sync screen's, Status's and the arrivals cue's data now, as `{"sync", "panel",
        "arrivals"}`: the shape of `demo/plaud-sync.json`, the Pages demo's canned view (area B3
        starts from it, then edits it by hand). Every title in it is this demo's own invention."""
        from recordings_ui.plaud import arrivals, plaud_view_settings, sync_panel, sync_view

        settings = plaud_view_settings(load_config(self.env))
        now = datetime.now(UTC)
        view = {
            "sync": sync_view(settings, now=now, can_request=None),
            "panel": sync_panel(settings, now=now),
            "arrivals": arrivals(settings, since=self.started_at, now=now),
        }
        path = self.scratch / "plaud-sync.json"
        path.write_text(json.dumps(view, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def make_due(self) -> None:
        """As if the schedule's interval had passed: the last turn's times moved back by it."""
        db = PlaudDB(State.open(self.state))
        now = datetime.now(UTC)
        back = now - timedelta(minutes=max(self.args.schedule, 1) + 1)
        for key in (K_LAST_STARTED, K_LAST_ENDED):
            db.put(key, back.isoformat(), now=now)

    def console(self) -> None:
        """Commands from stdin until EOF or `quit`; on EOF it waits for Ctrl-C."""
        for line in sys.stdin:
            if self.stopping.is_set() or not self.command(line):
                break
        else:
            self.stopping.wait()
            return
        self.stopping.set()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="demo_live", description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8000, help="the web app's port (default 8000)")
    ap.add_argument(
        "--schedule", type=int, default=20, help="[plaud] schedule_minutes (default 20)"
    )
    ap.add_argument(
        "--before-catch-up",
        action="store_true",
        help="start before the first catch-up: only Compare works until you run it",
    )
    ap.add_argument("--keep", action="store_true", help="keep the scratch folder at the end")
    args = ap.parse_args(argv)
    demo = Demo(args)

    def interrupted(signum: int, frame: object) -> None:
        demo.stopping.set()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    code = 0
    try:
        demo.setup()
        if not args.before_catch_up:
            demo.catch_up()
        demo.start()
        demo.wait_first_turn()
        demo.arrive()
        web, worker = demo.children["web"].pid, demo.children["worker"].pid
        say(f"Started: the web app (pid {web}) and the worker (pid {worker}), in {demo.scratch}.")
        say(f"Open {demo.url}#/add")
        say(TRY)
        demo.console()
    except KeyboardInterrupt:
        say("")
    except SystemExit as exc:  # a refusal, or a step that failed: said, then everything stops
        say(str(exc.code))
        code = 1
    finally:
        demo.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
