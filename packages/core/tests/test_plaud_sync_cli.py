"""The first catch-up and the CLI's syncs (stage 2b spec §11.6, §9.6; area A5): `recordings plaud
sync --dry-run [--list]`, `sync --catch-up` and `sync`, their refusals, their exit codes, `ops`
and `plaud` in one hold, and the turn after the catch-up running a sync. Against the fake Plaud
server, through the same config.toml as the worker's (plaud_live.py). Every ID, title, token and
byte is synthetic, and no output ever carries a title."""

import json
from datetime import datetime, timedelta

import pytest
from fake_plaud import html_page, server_error
from plaud_live import T0
from recordings.archive import RawSource
from recordings.cli import main
from recordings.config import load_config
from recordings.locks import OPS_LOCK, Locks, LockTimeout
from recordings.plaud import runlock
from recordings.plaud import sync as sync_module
from recordings.plaud.checks import K_HOLD, K_LAST_GOOD_LISTING
from recordings.plaud.client import FILE_PATH, LIST_PATH
from recordings.plaud.gate import GATE_INTERRUPTED, gate_now
from recordings.plaud.runlock import plaud_holder, plaud_run
from recordings.plaud.tokens import from_vendor
from recordings.writer import Incoming, Writer


@pytest.fixture
def cli(live, monkeypatch, capsys):
    """`recordings plaud …` against the live config: (exit code, stdout, stderr)."""
    monkeypatch.setenv("RECORDINGS_CONFIG", live.environ["RECORDINGS_CONFIG"])
    monkeypatch.setattr(runlock, "CLI_WAIT", 0.2)  # a held lock answers busy at once
    # The CLI's token store reads the real clock, the fake's sign-in its own: a sign-in that
    # lasts keeps the CLI from refreshing at every call.
    live.store().install(from_vendor(live.fake.issue(life=timedelta(days=36500)), now=T0))

    def run(*args: str):
        code = main(["plaud", *args])
        out = capsys.readouterr()
        return code, out.out, out.err

    return run


def no_title(live, *texts: str) -> None:
    for title in (r.name for r in live.account.recordings if r.name):
        for text in texts:
            assert title not in text


def ops_held(state) -> bool:
    try:
        with Locks(state.locks_dir, timeout=0).hold(OPS_LOCK):
            return False
    except LockTimeout:
        return True


def from_2a(live, plaud_id: str, n: int) -> str:
    """A recording the 2a import brought in, under a Plaud ID (synthetic audio and title)."""
    writer = Writer.open(load_config(live.environ), docs=False)
    media = live.tmp / "in" / f"{n}.mp3"
    media.parent.mkdir(exist_ok=True)
    media.write_bytes(f"ID3 synthetic audio {n}".encode())
    return writer.add(
        Incoming(
            media=media,
            recorded_at=datetime.fromisoformat("2026-09-06T14:00:03-07:00") + timedelta(hours=n),
            timezone_name="America/Vancouver",
            time_source="plaud",
            title=f"Week {n}",
            kind="audio",
            sources=(RawSource(kind="plaud", ref=plaud_id, added_at=T0, payload=b'{"id": 1}'),),
        )
    ).recording.id


def test_the_dry_run_prints_both_equations_and_imports_nothing(live, cli):
    live.account.add_many(3)
    code, out, err = cli("sync", "--dry-run")
    assert code == 0, err
    assert "listed 3 = here 0 + waiting 0 + excluded 0 + missing 3" in out
    assert "in the archive from Plaud 0 = here 0 + gone from Plaud 0" in out
    assert "consent list: 1 IDs, 0 on Plaud now" in out
    assert "missing 3: waits for the first catch-up" in out
    assert "the first catch-up would import 3" in out
    assert live.here() == {} and not gate_now(live.db()).open
    assert live.db().get(K_LAST_GOOD_LISTING) is None  # a dry run is a Compare
    no_title(live, out, err)


def test_the_dry_runs_list_names_each_row_by_id_date_and_duration_never_a_title(live, cli):
    recs = live.account.add_many(2)
    live.consent_ids(recs[0].plaud_id)
    code, out, err = cli("sync", "--dry-run", "--list")
    assert code == 0, err
    for rec in recs:
        assert f"of_{rec.plaud_id}" in out
    assert "excluded" in out and "consent" in out
    no_title(live, out, err)


def test_the_dry_runs_json_carries_counts_and_phrases_and_no_title(live, cli):
    live.account.add_many(2)
    code, out, _ = cli("sync", "--dry-run", "--list", "--json")
    payload = json.loads(out)
    assert code == 0 and payload["equation"]["listed"] == 2 and len(payload["rows"]) == 2
    no_title(live, out)


def test_list_goes_with_dry_run(live, cli):
    code, _, err = cli("sync", "--list")
    assert code == 1 and "--list goes with --dry-run" in err


def test_the_catch_up_imports_everything_under_ops_and_plaud_and_opens_the_gate(
    live, cli, monkeypatch
):
    # §11.6, §9.4: no cap (25 > a scheduled run's 20), `ops` and `plaud` in one hold announced as
    # plaud-catch-up, its listing the last good one, the gate open at its clean end.
    live.account.add_many(25)
    seen = []
    real = sync_module.run_sync

    def spy(run, **kw):
        state = run.writer.state
        seen.append((plaud_holder(state)[0], ops_held(state)))
        return real(run, **kw)

    monkeypatch.setattr(sync_module, "run_sync", spy)
    code, out, err = cli("sync", "--catch-up")
    assert code == 0, out + err
    assert seen == [("plaud-catch-up", True)]
    assert len(live.here()) == 25
    assert gate_now(live.db()).open
    assert live.db().get(K_LAST_GOOD_LISTING) is not None
    run = live.db().state.last_run("plaud-catch-up")
    assert run is not None and run.ok and run.detail == "ok"
    assert "importing 25 of 25" in err and "the first catch-up is done" in out
    no_title(live, out, err)
    code, out, err = cli("sync", "--catch-up")
    assert code == 1 and "already done" in err


def test_an_interrupted_catch_up_says_so_and_run_again_finishes_then_the_schedule_starts(live, cli):
    # C5.19, C5.20: Plaud goes down after the first recording; the gate stays closed and says
    # so; run again, it picks up where it stopped; the turn after it runs a sync and pings.
    live.account.add_many(4)
    down = []

    def outage() -> None:
        down.append(live.server.fault(lambda p, _q: p != LIST_PATH, server_error(503)))

    live.server.after(5, outage)  # the listing (2 pages), then the first recording (3)
    code, out, err = cli("sync", "--catch-up")
    assert code == 69 and "Plaud unavailable" in out, out + err
    assert len(live.here()) == 1
    gate = gate_now(live.db())
    assert not gate.open and gate.reason == GATE_INTERRUPTED
    live.server.faults.remove(down[0])
    code, out, err = cli("sync", "--catch-up")
    assert code == 0, out + err
    assert len(live.here()) == 4 and gate_now(live.db()).open
    new = live.account.add()
    w = live.started()
    w.tick()  # the schedule starts by itself: no config edit, no restart
    assert new.plaud_id in live.here()
    assert len(live.pings()) == 1


def test_a_held_catch_up_stops_and_accept_listing_then_lets_it_run(live, cli):
    # §11.6: measured against the archive's 2a Plaud IDs, a listing with under 80% of them holds;
    # `accept-listing` (the CLI's, before the catch-up) makes it the baseline.
    for n in range(5):
        from_2a(live, f"{n + 1:032x}", n)  # recordings 2a imported that Plaud no longer lists
    live.account.add_many(2)
    code, out, err = cli("sync", "--catch-up")
    assert code == 1 and "Plaud sync paused" in out, out + err
    assert live.db().get(K_HOLD) is not None and len(live.here()) == 5
    code, out, err = cli("accept-listing")
    assert code == 0, out + err
    code, out, err = cli("sync", "--catch-up")
    assert code == 0, out + err
    assert len(live.here()) == 7 and gate_now(live.db()).open


def test_sync_is_refused_before_the_catch_up_and_while_a_hold_is_on(live, cli):
    live.account.add()
    code, _, err = cli("sync")
    assert code == 1 and "--catch-up" in err
    live.catch_up_done()
    live.db().put(K_HOLD, {"since": T0.isoformat()}, now=T0)
    code, out, err = cli("sync")
    assert code == 1 and "held: see Status" in out
    assert live.here() == {}


def test_the_exit_codes(live, cli):
    rec = live.account.add()
    live.catch_up_done()
    fault = live.server.fault(FILE_PATH.format(f"of_{rec.plaud_id}"), html_page())
    code, out, _ = cli("sync")
    assert code == 2 and "result: every fetch failed" in out  # the listing good, one failed
    live.server.faults.remove(fault)
    assert cli("sync")[0] == 0
    live.consent_ids(extra="- `of_" + "a" * 31 + "` — a typo\n")
    assert cli("sync")[0] == 78  # the consent list stops imports
    live.consent_ids()
    fault = live.server.fault(LIST_PATH, server_error(503))
    assert cli("sync")[0] == 69  # Plaud unreachable
    live.server.faults.remove(fault)
    with plaud_run(live.db().state, "plaud-full", timeout=0, now=T0):
        code, _, err = cli("sync")
    assert code == 75 and "skipped: busy (`plaud-full`)" in err
    live.fake.revoke()
    live.fake.refuse_refresh()
    code, out, _ = cli("sync")
    assert code == 77 and "sign-in needed" in out
