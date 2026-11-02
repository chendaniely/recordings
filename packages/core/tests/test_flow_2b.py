"""Stage 2b end to end, on the fake (spec §21's flow test; §20 criteria 4 and 7; area A6): an
archive with an `audio-router` fixture imported as 2a imports it, then against the fake Plaud
server, through the CLI and the worker's real turn: `plaud baseline`, `sync --dry-run`, `sync
--catch-up`, then the worker's loop on a fake clock (scheduled syncs, requests from the Sync
screen, a late keep-out, Release, a refused sign-in and a new one, then edit-sync's checks of a
speaker rename and a new field), with §21's faults injected and every log captured at DEBUG.

- **C4 with the real turn** (A1 proved it through a stand-in): an expired access token that
  refreshes keeps the pings going; a 401 and a refused refresh stop them from that turn on, with
  no Plaud call while paused; a new sign-in, then a good turn, pings again; a 429 from the token
  endpoint is an outage, not `sign-in`.
- **C7.01, the leak scan:** no token, no `X-Amz-` query and no title in any log record, the CLI's
  output, a ping, Status's view (the panel, the arrivals cue, Details' Plaud lines), an
  `ops_runs` row, a request's result, or the 2b tables of `state.db`. The Sync screen's view is
  scanned for tokens and links only: its Not here yet and Updated rows carry titles by design.
- The worker sends no push: the fake Healthchecks sees pings only.

Then (area B2) Sync everything, pressed and from the CLI with its manifest, a Re-sync, and
`plaud held`, all inside the scan.
Every ID, title, token and byte is synthetic, and nothing reaches Plaud."""

import json
import logging
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

from fake_plaud import dropped, expired_link, html_page, rate_limited, server_error
from plaud_live import T0
from recordings.archive import Archive
from recordings.cli import main
from recordings.config import load_config
from recordings.plaud import requests
from recordings.plaud.checks import BLOCKER_PREFIX, K_SIGN_IN
from recordings.plaud.client import FILE_PATH, LIST_PATH
from recordings.plaud.endpoint import TOKEN_PATH
from recordings.plaud.requests import PlaudRequest
from recordings.plaud.tokens import from_vendor
from recordings_ui.plaud import (
    arrivals,
    plaud_view_settings,
    recording_plaud,
    sync_panel,
    sync_view,
)

TZ = "America/Vancouver"
A, B = "a1" * 16, "b2" * 16  # synthetic Plaud IDs the 2a import brings in
TITLES_2A = {A: "Synthetic 2a seminar", B: "Synthetic 2a office hours"}
WORD = "noarchive"
SPEAKER = "Synthetic Speaker Ada"  # Dan's name for a speaker (edit-sync, area B1)
FIELD_VALUE = "synthetic-feature-value-never-shown"  # a new field's value, never shown
TOKEN_MARKS = ("fake-access-", "fake-refresh-", "Bearer ")
LINK_MARKS = ("X-Amz-", "X-Amz-Signature", "synthetic&X-Amz")
TABLES = (
    "plaud_state",
    "plaud_events",
    "plaud_requests",
    "plaud_listing",
    "plaud_checks",
    "plaud_check_log",
    "plaud_listed",
    "ops_runs",
    "meta",
)


class Flow:
    """What the flow ran, to scan at the end: the CLI's output, the views and the replies."""

    def __init__(self, live, capsys, caplog):
        self.live, self.capsys, self.caplog = live, capsys, caplog
        self.texts: dict[str, list[str]] = {"cli": [], "views": [], "screen": [], "replies": []}
        self.n = 0

    def cli(self, *argv: str) -> int:
        code = main(list(argv))
        out = self.capsys.readouterr()
        self.texts["cli"] += [out.out, out.err]
        return code

    def turn(self, w, minutes: float = 21) -> None:
        self.live.clock.advance(minutes=minutes)
        w.tick()

    def ask(self, w, kind: str, **fields) -> requests.Reply:
        self.n += 1
        rid = f"flow-request-{self.n:04d}"
        requests.insert(
            self.live.db().state,
            PlaudRequest(rid, kind, **fields),
            now=self.live.clock(),
            timezone_name=TZ,
        )
        w.tick()
        reply = requests.read(w.state, rid)
        self.texts["replies"].append(json.dumps(reply.to_dict()))
        return reply

    def look(self) -> None:
        """Read every view as the web app would, now: Status's (title-free) and the Sync
        screen's (titles by design, scanned for tokens and links only)."""
        settings = plaud_view_settings(load_config(self.live.environ))
        now = self.live.clock()
        self.texts["views"].append(json.dumps(sync_panel(settings, now=now)))
        self.texts["views"].append(json.dumps(arrivals(settings, since=T0, now=now)))
        for rid in self.live.here().values():
            shown = recording_plaud(settings, rid, now=now)
            self.texts["views"].append(json.dumps(shown))
        self.texts["screen"].append(json.dumps(sync_view(settings, now=now, can_request=None)))


def ar_fixture(ar_archive, plaud_env) -> None:
    """Two Plaud recordings 2a brought in from audio-router (synthetic envelopes and audio)."""
    for i, (pid, title) in enumerate(TITLES_2A.items()):
        ar_archive.plaud(
            pid,
            envelopes=[plaud_env(pid, name=title)],
            recorded_at=f"2026-08-0{i + 2}T10:00:00-07:00",
        )
    ar_archive.write_catalog()


def state_dump(live) -> str:
    rows = []
    with closing(sqlite3.connect(live.state_path / "state.db")) as db:
        for table in TABLES:
            try:
                rows += [repr(r) for r in db.execute(f"SELECT * FROM {table}")]
            except sqlite3.OperationalError:
                continue
    return "\n".join(rows)


def test_stage_2b_end_to_end_on_the_fake_leaks_nothing(
    live, ar_archive, plaud_env, capsys, caplog, monkeypatch
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("RECORDINGS_CONFIG", live.environ["RECORDINGS_CONFIG"])
    flow = Flow(live, capsys, caplog)
    acct, fake = live.account, live.fake
    # The CLI's token store reads the real clock: its sign-in lasts (the worker's uses the fake).
    live.store().install(from_vendor(fake.issue(life=timedelta(days=36500)), now=T0))

    # 2a: the audio-router import, then the same two recordings listed on Plaud.
    ar_fixture(ar_archive, plaud_env)
    assert flow.cli("import-audio-router", str(ar_archive.root), "--json") == 0
    for i, (pid, title) in enumerate(TITLES_2A.items()):
        acct.add(title, plaud_id=pid, start_at=T0 - timedelta(days=90 - i))
    meetings = [acct.add(f"Synthetic catch-up meeting {i}") for i in range(3)]
    private = acct.add("Private: synthetic one-to-one")
    consented = acct.add("Synthetic consent-listed guest talk")
    kept = acct.add(f"{WORD} synthetic appointment")
    live.consent_ids(consented.plaud_id)

    # The first catch-up, in the runbook's order (§11.6, §17.5), with §21's faults on the way.
    assert flow.cli("plaud", "baseline") == 0
    live.server.fault(LIST_PATH, server_error(503), times=1)  # retried after a backoff
    assert flow.cli("plaud", "sync", "--dry-run", "--list") == 0
    live.server.fault(
        lambda path, _q: path == FILE_PATH.format(f"of_{meetings[0].plaud_id}"),
        html_page(),
        times=1,
    )
    live.server.fault(
        lambda p, _q: p.startswith("/audio/"), expired_link(), host="storage", times=1
    )
    live.server.fault(lambda p, _q: p.startswith("/notes/"), dropped(), host="storage", times=1)
    code = flow.cli("plaud", "sync", "--catch-up")
    assert code in (0, 2)
    here = live.here()
    assert {A, B, private.plaud_id} <= set(here)
    assert consented.plaud_id not in here and kept.plaud_id not in here
    assert live.fetched(consented.plaud_id) == [] and live.fetched(kept.plaud_id) == []
    rec = Archive(live.root).load(here[private.plaud_id])
    assert any(t.tag == "private" for t in rec.tags)  # it arrived private

    # The worker, on the fake clock from now on: set past the CLI's real one (a kept-out time the
    # catch-up wrote must come before a Release), with an hour's sign-in that refreshes (C4.01).
    live.clock.now = max(live.clock(), datetime.now(UTC)) + timedelta(seconds=1)
    live.store().install(from_vendor(fake.issue(), now=live.clock()))
    # Its first scheduled sync brings what the catch-up's faults left, and pings.
    w = live.started()
    w.tick()
    flow.look()
    assert {m.plaud_id for m in meetings} <= set(live.here())
    assert len(live.pings()) == 1

    # New on Plaud: Compare, then Import selected, through the Sync screen's requests; a 429
    # with a short Retry-After on the listing is waited out.
    new = acct.add("Synthetic standup today")
    live.server.fault(LIST_PATH, rate_limited(5), times=1)
    assert flow.ask(w, "compare").state == "done"
    flow.look()
    reply = flow.ask(w, "import", plaud_ids=(new.listed_id("of_"),))
    assert reply.state == "done" and new.plaud_id in live.here()

    # A late keep-out: renamed in Plaud to start with the word; the next sync flags it.
    late = meetings[1]
    acct.rename(late.plaud_id, f"{WORD} {late.name}")
    flow.turn(w)
    flow.look()
    rid = live.here()[late.plaud_id]
    assert any(t.tag == "private" for t in Archive(live.root).load(rid).tags)
    assert flow.ask(w, "keep-here", recording_ids=(rid,)).state == "done"

    # Release: the kept-out title loses its word; preview, let in, the next sync brings it.
    acct.rename(kept.plaud_id, "Synthetic appointment, renamed")
    flow.turn(w)
    preview = flow.ask(w, "release-preview", plaud_ids=(kept.listed_id("of_"),))
    assert preview.state == "done"
    assert flow.ask(w, "release", plaud_ids=(kept.listed_id("of_"),)).state == "done"
    pings = len(live.pings())
    flow.turn(w)
    assert kept.plaud_id in live.here() and len(live.pings()) == pings + 1

    # C4.01: the hour's access token expires; the turn refreshes it, and the pings go on.
    refreshes = fake.refreshes
    flow.turn(w, minutes=65)
    assert fake.refreshes > refreshes and len(live.pings()) == pings + 2

    # C4.04: a 429 from the token endpoint is an outage, never "sign-in needed".
    fake.fault = rate_limited(30)
    flow.turn(w, minutes=65)
    assert live.db().get(BLOCKER_PREFIX + "sign-in") is None
    assert len(live.pings()) == pings + 2
    fake.fault = None

    # C4.02: revoked, and the refresh refused: no ping from that turn on, and no Plaud call while
    # the pause stands (a scheduled turn makes none until the token file changes).
    fake.revoke()
    fake.refuse_refresh()
    flow.turn(w, minutes=65)
    assert live.db().get(BLOCKER_PREFIX + "sign-in") is not None
    flow.look()
    calls = len(live.server.requests)
    flow.turn(w)
    flow.turn(w)
    assert len(live.server.requests) == calls  # paused: no Plaud call
    assert len(live.pings()) == pings + 2
    panel = sync_panel(plaud_view_settings(load_config(live.environ)), now=live.clock())
    assert panel["summary"]["sentence"] == "Sign-in needed"
    assert any(a.startswith("Plaud sign-in needed since ") for a in panel["attention"])

    # C4.03: a new sign-in (as `make plaud-sign-in` installs one), then a good turn: it pings.
    fake.refuse = None
    live.store().install(from_vendor(fake.issue(), now=live.clock()))
    flow.turn(w)
    assert live.db().get(BLOCKER_PREFIX + "sign-in") is None
    assert live.db().get(K_SIGN_IN).get("refused_at") is None
    assert len(live.pings()) == pings + 3
    flow.look()

    # Edit-sync (area B1): Dan names a speaker on one recording, and Plaud starts sending a new
    # field on every one; the scheduled turns' sweep checks each, writes the rename (stable: never
    # counted), and neither the name nor the field's value reaches a log, a view or state.db.
    acct.rename_speaker(meetings[0].plaud_id, SPEAKER)
    acct.send_field("synthetic_feature", FIELD_VALUE)
    named = live.here()[meetings[0].plaud_id]
    for _ in range(3 * 72):
        flow.turn(w, minutes=20)
        found = live.db().check_log_since(T0 - timedelta(days=1))
        if any(r.recording_id == named and "speakers" in r.named_parts for r in found):
            break
    flow.look()
    updated = [r for r in found if r.recording_id == named and "speakers" in r.named_parts]
    assert updated and not any(r.counted for r in updated)
    assert any("synthetic_feature" in r.fields for r in found)

    # Sync everything and Re-sync (area B2): a press through the worker, its manifest, a Re-sync
    # of the renamed recording, and the CLI's `sync --full` and `held`; none carries a title, a
    # speaker's name or a field's value.
    flow.turn(w, minutes=61)
    reply = flow.ask(w, "full")
    assert reply.state == "done" and reply.result["exit_code"] in (0, 2), reply
    manifest = live.state_path / "manifests" / reply.result["manifest"]
    flow.texts["cli"].append(manifest.read_text(encoding="utf-8"))
    acct.rename_speaker(meetings[0].plaud_id, f"{SPEAKER} again")
    assert flow.ask(w, "resync", recording_ids=(named,)).message == "Updated: speakers"
    assert flow.cli("plaud", "sync", "--full", "--json") in (0, 2)
    assert flow.cli("plaud", "held") == 0
    flow.look()

    # C7.01: the leak scan, over everything the flow produced.
    titles = {r.name for r in acct.recordings if r.name} | set(TITLES_2A.values())
    titles |= {late.name, f"{WORD} {late.name}", f"{WORD} synthetic appointment"}
    titles |= {SPEAKER, f"{SPEAKER} again", FIELD_VALUE}  # names and a value: never logged
    logs = "\n".join(r.getMessage() for r in caplog.records)
    pinged = "\n".join(json.dumps(p) for p in live.ntfy.requests)
    scanned = {
        "logs": logs,
        "cli": "\n".join(flow.texts["cli"]),
        "status": "\n".join(flow.texts["views"]),
        "replies": "\n".join(flow.texts["replies"]),
        "state.db": state_dump(live),
        "pings": pinged,
    }
    for where, text in scanned.items():
        for mark in (*TOKEN_MARKS, *LINK_MARKS):
            assert mark not in text, f"{where} carries {mark!r}"
        for title in titles:
            assert title not in text, f"{where} carries a title"
    screen = "\n".join(flow.texts["screen"])
    for mark in (*TOKEN_MARKS, *LINK_MARKS):
        assert mark not in screen, f"the Sync screen's view carries {mark!r}"
    # The worker sends no push: the fake Healthchecks saw the sync's pings and nothing else.
    assert {r["path"] for r in live.ntfy.requests} == {"/sync"}
    # The refreshes went to the fake's token endpoint.
    assert any(r.method == "POST" and r.path == TOKEN_PATH for r in live.server.requests)
