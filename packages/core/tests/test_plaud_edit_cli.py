"""Edit-sync's commands (stage 2b spec §14.4, §14.5; area B1): `recordings plaud baseline` seeding
check state and `--all` rebaselining, `recordings plaud check --volatility` (three recordings
fetched twice a minute apart, nothing written), and `recordings plaud breaker [reset]`. Against
the fake Plaud server, through the worker's config.toml (plaud_live.py). Every ID, title, token and
byte is synthetic, and no output carries a title or a field's value."""

import json
from datetime import timedelta

import pytest
from plaud_live import T0
from recordings.archive import Archive
from recordings.cli import main
from recordings.plaud import breaker, commands, runlock
from recordings.plaud.checks import K_BREAKER
from recordings.plaud.client import FILE_PATH, LIST_PATH
from recordings.plaud.tokens import from_vendor


@pytest.fixture
def cli(live, monkeypatch, capsys):
    """`recordings plaud …` against the live config: (exit code, stdout, stderr)."""
    monkeypatch.setenv("RECORDINGS_CONFIG", live.environ["RECORDINGS_CONFIG"])
    monkeypatch.setattr(runlock, "CLI_WAIT", 0.2)
    monkeypatch.setattr(commands, "_sleep", lambda seconds: None)  # "a minute apart", at once
    live.store().install(from_vendor(live.fake.issue(life=timedelta(days=36500)), now=T0))

    def run(*args: str):
        code = main(["plaud", *args])
        out = capsys.readouterr()
        return code, out.out, out.err

    return run


def imported(live, n: int):
    recs = live.account.add_many(n)
    live.catch_up_done()
    w = live.started()
    w.tick()
    assert len(live.here()) == n
    return w, recs


def no_title(live, *texts: str) -> None:
    for rec in live.account.recordings:
        for text in texts:
            assert rec.name not in text


def envelopes(live, plaud_id: str = "") -> int:
    return sum(
        1
        for r in live.server.requests
        if r.path != LIST_PATH and r.path.startswith(FILE_PATH.format("")) and plaud_id in r.path
    )


def snapshots(live) -> dict[str, int]:
    return {
        rec.id: sum(1 for s in rec.sources if s.kind == "plaud")
        for rec in Archive(live.root).iter_recordings()
    }


def test_the_baseline_seeds_check_state_and_all_rebaselines(live, cli):
    imported(live, 3)
    db = live.db()
    with db._db() as conn:  # 2a's recordings have no check rows before the baseline
        conn.execute("DELETE FROM plaud_checks")
    code, out, err = cli("baseline", "--json")
    assert code == 0, err
    payload = json.loads(out)
    assert (payload["seeded"], payload["already_seeded"]) == (3, 0)
    rows = db.check_rows()
    assert len(rows) == 3 and all(r.baseline_pending for r in rows)
    for row in rows:
        db.set_check(row.recording_id, baseline_pending=False)
    code, out, _ = cli("baseline")
    assert code == 0 and "check state seeded: 0" in out and "already seeded: 3" in out
    assert not any(r.baseline_pending for r in db.check_rows())
    code, out, _ = cli("baseline", "--all")
    assert code == 0 and "rebaselined: 3" in out
    assert all(r.baseline_pending for r in db.check_rows())
    no_title(live, out)


def test_the_volatility_check_fetches_three_twice_and_writes_nothing(live, cli):
    imported(live, 4)
    rows = sorted((r.recording_id, r.next_check_at) for r in live.db().check_rows())
    before, fetched = snapshots(live), envelopes(live)
    code, out, err = cli("check", "--volatility", "--json")
    assert code == 0, err
    payload = json.loads(out)
    assert payload["volatile"] is False and len(payload["recordings"]) == 3
    for row in payload["recordings"]:
        assert row["differ"] == [] and "transcript" in row["parts"]
    assert envelopes(live) == fetched + 6  # three recordings, twice each
    assert snapshots(live) == before  # nothing written
    assert sorted((r.recording_id, r.next_check_at) for r in live.db().check_rows()) == rows
    no_title(live, out)


def test_a_volatile_field_is_found_named_and_stops_the_runbook(live, cli):
    imported(live, 3)
    live.account.make_volatile("other")
    code, out, err = cli("check", "--volatility")
    assert code == 1, err
    assert "other differs (fields: synthetic_nonce)" in out
    assert commands.VOLATILE_STOP in out
    assert "n-" not in out.replace("synthetic_nonce", "")  # never the field's value
    no_title(live, out)


def test_the_volatility_check_leaves_private_and_consent_listed_recordings_alone(live, cli):
    private = live.account.add("Private: synthetic one-to-one")
    listed = live.account.add("Synthetic guest talk")
    plain = live.account.add("Synthetic standup")
    live.catch_up_done()
    w = live.started()
    w.tick()
    live.consent_ids(listed.plaud_id)
    asked = {rec.plaud_id: envelopes(live, rec.plaud_id) for rec in (private, listed)}
    code, out, err = cli("check", "--volatility", "--json")
    assert code == 0, err
    rids = [row["recording_id"] for row in json.loads(out)["recordings"]]
    assert rids == [live.here()[plain.plaud_id]]
    assert {pid: envelopes(live, pid) for pid in asked} == asked
    assert "only 1 of 3" not in out  # JSON: the lines aren't printed
    code, out, _ = cli("check", "--volatility")
    assert "only 1 of 3 could be fetched" in out


def test_breaker_says_whether_it_is_open_and_reset_closes_it(live, cli):
    imported(live, 1)
    code, out, _ = cli("breaker")
    assert code == 0 and "closed" in out
    db = live.db()
    db.put(
        K_BREAKER,
        {
            "open": True,
            "since": T0.isoformat(),
            "recordings": 12,
            "fields": ["folder_id"],
            "parts": [],
            "accept": True,
            "normaliser": 1,
        },
        now=T0,
    )
    code, out, _ = cli("breaker", "--json")
    payload = json.loads(out)
    assert payload["open"] is True and payload["fields"] == ["folder_id"] and payload["accept"]
    code, out, _ = cli("breaker", "reset")
    assert code == 0 and breaker.RESET in out and "closed" in out
    assert not breaker.is_open(db)
    code, out, _ = cli("breaker", "reset")
    assert code == 0 and breaker.NOT_OPEN in out
