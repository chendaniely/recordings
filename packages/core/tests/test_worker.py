"""The Plaud worker (spec §9.2, §9.7): its start, the tick, scheduled turns, the heartbeat and
`--alive`. An injected clock everywhere; real threads only where a test says so. Every archive and
consent list is synthetic, and nothing here reaches Plaud: Phase 1's worker has no client."""

import dataclasses
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from recordings import worker as worker_module
from recordings.archive import Archive, RawSource
from recordings.cli import main
from recordings.config import load_config
from recordings.init import init_archive
from recordings.locks import PLAUD_LOCK, Locks
from recordings.models import is_private
from recordings.plaud import keepout
from recordings.plaud.checks import (
    BLOCKER_PREFIX,
    K_CATCH_UP_DONE,
    K_CONSENT,
    K_KEEP_OUT_WORDS,
    K_LAST_ENDED,
    K_LAST_STARTED,
    K_PATTERNS_PASS,
)
from recordings.plaud.flags import CONSENT_LATE, PRIVATE_UNWRITTEN
from recordings.plaud.gate import GATE_FIRST
from recordings.plaud.health import RunOutcome, sync_health
from recordings.plaud.runlock import plaud_run
from recordings.refs import canonical_plaud_id
from recordings.state import State
from recordings.worker import FAILED, SILENT, STUCK, Worker, alive, heartbeat_once
from recordings.writer import Incoming, Writer

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PLAUD_A = "of_" + f"{1:032x}"  # synthetic Plaud IDs
PLAUD_B = "of_" + f"{2:032x}"
PLAUD_C = "of_" + f"{3:032x}"
GOOD = RunOutcome(listing="good", attempts=0, successes=0, disk_refused=False, detail="ok")


class Clock:
    """A fake clock, safe to read from the heartbeat thread too."""

    def __init__(self, now: datetime = T0) -> None:
        self.now = now
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self.now

    def advance(self, **delta: float) -> None:
        with self._lock:
            self.now += timedelta(**delta)


class TurnSpy:
    """A sync turn that records each call and answers `outcome`: Phase 2's turn, stood in."""

    def __init__(self, outcome: RunOutcome = GOOD) -> None:
        self.calls: list[bool] = []
        self.outcome = outcome

    def __call__(self, worker: Worker, *, light: bool) -> RunOutcome:
        self.calls.append(light)
        return self.outcome


def consent_list(folder: Path, *ids: str, extra: str = "") -> Path:
    """A synthetic consent list, written whole and renamed into place, as an editor or
    `make consent-add` does."""
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["# Consent list (synthetic)", *(f"- `{i}` — asked not to keep" for i in ids)]
    tmp = folder / ".consent-list.md.tmp"
    tmp.write_text("\n".join(lines) + "\n" + extra, encoding="utf-8")
    os.replace(tmp, folder / "consent-list.md")
    return folder / "consent-list.md"


def catch_up_done(w: Worker) -> None:
    """What Phase 2's supervised catch-up records: the gate opens (Task 12's gate_now)."""
    w.db.put(K_CATCH_UP_DONE, T0.isoformat(), now=T0)


@pytest.fixture
def setup(tmp_path, ntfy):
    """An archive, its state, a consent folder whose list reads, a config.toml, and the worker's
    environment: the sync's ping URL on the fake Healthchecks (decision 11). No sign-in exists."""
    root, state = tmp_path / "archive", tmp_path / "state"
    init_archive(root, state, writer_id="test")
    consent = tmp_path / "consent"
    consent_list(consent, PLAUD_C)
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[archive]\npath = "{root}"\nwriter_id = "test"\n'
        f'[state]\npath = "{state}"\n[index]\npath = "{tmp_path / "index" / "index.db"}"\n'
        f'[plaud]\nschedule_minutes = 20\nconsent_list = "{consent / "consent-list.md"}"\n'
        f'token_store = "{tmp_path / "plaud" / "tokens.json"}"\n',
        encoding="utf-8",
    )
    environ = {
        "RECORDINGS_CONFIG": str(cfg_file),
        "RECORDINGS_DEADMAN_SYNC_URL": f"{ntfy.url}/sync",
    }
    return SimpleNamespace(
        root=root, state=state, consent=consent, cfg_file=cfg_file, environ=environ, tmp=tmp_path
    )


@pytest.fixture
def make_worker(setup):
    """Workers on the setup's config, each stopped at the end. The heartbeat thread waits an hour
    between writes unless a test asks for less: tests write heartbeats themselves."""
    made: list[Worker] = []

    def make(clock, *, turn=None, heartbeat: float = 3600.0) -> Worker:
        kwargs = {"turn": turn} if turn is not None else {}
        w = Worker(
            load_config(setup.environ), setup.environ, clock=clock, heartbeat=heartbeat, **kwargs
        )
        made.append(w)
        return w

    yield make
    for w in made:
        w.stopping.set()
    for w in made:  # no heartbeat thread outlives its test
        if w._beating is not None:
            w._beating.join(5)
            assert not w._beating.is_alive()


def add_recording(setup, plaud_id: str, n: int = 1) -> str:
    """A recording with a Plaud source, made through a Writer as the 2a import made them."""
    writer = Writer.open(load_config(setup.environ), docs=False)
    media = setup.tmp / "in" / f"{n}.mp3"
    media.parent.mkdir(exist_ok=True)
    media.write_bytes(f"ID3 synthetic audio {n}".encode())
    added = writer.add(
        Incoming(
            media=media,
            recorded_at=datetime.fromisoformat("2026-10-06T14:00:03-07:00") + timedelta(minutes=n),
            timezone_name="America/Vancouver",
            time_source="plaud",
            title=f"Week {n}",
            kind="audio",
            sources=(RawSource(kind="plaud", ref=plaud_id, added_at=T0, payload=b'{"id": 1}'),),
        )
    )
    return added.recording.id


def add_pattern(setup) -> None:
    """A new auto-private pattern in config.toml: the next start checks every Plaud recording's
    titles again (decision 27)."""
    setup.cfg_file.write_text(
        setup.cfg_file.read_text(encoding="utf-8").replace(
            "[plaud]\n", '[plaud]\nauto_private_patterns = ["^synthetic-pattern"]\n'
        ),
        encoding="utf-8",
    )


def edited_by_hand(setup, rid: str) -> None:
    """recording.json changed outside the app: the Writer refuses it until `reindex` accepts it."""
    path = Archive(setup.root).path_for(rid) / "recording.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["title"] = "Week 1, edited by hand"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def last_turn(w: Worker) -> tuple:
    return sync_health(w.state, schedule_minutes=20).last_turn


def blocker(w: Worker, name: str) -> dict | None:
    """The blocker's row as plaud_state holds it (Task 11: {"since", "detail"}), or None."""
    return w.db.get(BLOCKER_PREFIX + name)


def beat_of(setup) -> dict:
    return json.loads(State.read_only(setup.state).meta("worker_heartbeat"))


def test_start_runs_in_the_spec_order(setup, make_worker, monkeypatch):
    w = make_worker(Clock())
    calls = []
    for name in ("_start_heartbeat", "_open_writer", "_open_gate"):
        original = getattr(w, name)

        def spy(_name=name, _original=original):
            calls.append(_name)
            if _name == "_open_writer":  # the heartbeat is already there, saying `starting`
                calls.append(beat_of(setup)["phase"])
            return _original()

        monkeypatch.setattr(w, name, spy)
    w.start()
    assert calls == ["_start_heartbeat", "_open_writer", "starting", "_open_gate"]
    assert w.writer is not None and w.gate is not None and not w.gate.open
    assert w.progress.read()[0] == "idle"  # the start is done


def test_a_scheduled_turn_before_the_catch_up_makes_no_plaud_call(
    setup, make_worker, ntfy, monkeypatch
):
    # C5.18: schedule_minutes is 20 from the deploy, and the gate holds every turn until the
    # supervised catch-up. No Plaud call, nothing written but the turn's record, and no ping: a
    # check never pinged stays silent.
    clock, spy = Clock(), TurnSpy()
    w = make_worker(clock, turn=spy)
    w.start()
    assert not w.gate.open
    decided, real = [], worker_module.record_turn

    def recording(db, outcome, *, now, detail=None):
        decided.append((outcome, now))
        real(db, outcome, now=now, detail=detail)

    monkeypatch.setattr(worker_module, "record_turn", recording)
    w.tick()
    assert spy.calls == []
    # The gate's own reason, which Status's not-pinged line shows (SyncHealth.cause, Task 11).
    outcome, at, detail = last_turn(w)
    assert (outcome, at, detail) == ("gate", T0, {"reason": GATE_FIRST})
    clock.advance(minutes=19)
    w.tick()  # a turn that didn't run waits one interval, in memory
    clock.advance(minutes=1)
    w.tick()
    assert decided == [("gate", T0), ("gate", T0 + timedelta(minutes=20))]
    assert spy.calls == []
    # The repeated outcome keeps its first time, which reads as "since" (Task 11's record_turn).
    assert last_turn(w)[1] == T0
    assert ntfy.requests == []


def test_the_first_sync_after_the_catch_up_starts_at_the_next_tick(setup, make_worker):
    # why: the runbook's "within 20 minutes" after the catch-up. A turn the gate held waits one
    # interval, but the gate is read again at every tick, and its opening ends the wait.
    clock, spy = Clock(), TurnSpy()
    w = make_worker(clock, turn=spy)
    w.start()
    w.tick()
    assert last_turn(w)[0] == "gate" and spy.calls == []
    clock.advance(minutes=3)
    w.tick()
    assert spy.calls == []  # still closed: still waiting
    catch_up_done(w)
    clock.advance(seconds=2)
    w.tick()
    assert w.gate.open and spy.calls == [False]
    assert last_turn(w)[0] == "ran"


def test_a_held_plaud_lock_skips_the_turn_as_busy_without_a_ping(setup, make_worker, ntfy):
    # C6.08: a wedged holder stops the pings, so the check's grace brings the email.
    clock, spy = Clock(), TurnSpy()
    w = make_worker(clock, turn=spy)
    catch_up_done(w)
    w.start()
    with plaud_run(w.state, "plaud-full", timeout=0, now=T0):
        w.tick()
    assert spy.calls == []
    outcome, _, detail = last_turn(w)
    assert outcome == "busy" and detail["job"] == "plaud-full"
    assert ntfy.requests == []
    clock.advance(minutes=20)
    w.tick()  # free now: the turn runs, and a good run that nothing holds up pings
    assert spy.calls == [False]
    assert [r["path"] for r in ntfy.requests] == ["/sync"]


def test_a_lock_held_for_a_moment_doesnt_skip_the_turn(setup, make_worker):
    # why: Status, a request's insert and the deploy guard probe `plaud` for an instant. A turn
    # that met one at once would wait a whole interval as "busy", so it waits about a second.
    spy = TurnSpy()
    w = make_worker(Clock(), turn=spy)
    catch_up_done(w)
    w.start()
    held, release = threading.Event(), threading.Event()

    def probe():
        with Locks(w.state.locks_dir, timeout=0).hold(PLAUD_LOCK):
            held.set()
            release.wait(0.3)

    holder = threading.Thread(target=probe)
    holder.start()
    assert held.wait(5)
    w.tick()
    holder.join()
    assert spy.calls == [False] and last_turn(w)[0] == "ran"


def test_due_reads_last_started_and_last_ended_across_a_crash_loop(setup, make_worker):
    # why: spec §9.2. Both stamps persist, so a worker that crashes and restarts doesn't start a
    # sync at every start.
    spy = TurnSpy()
    w = make_worker(Clock(), turn=spy)
    catch_up_done(w)
    w.start()
    w.tick()
    assert spy.calls == [False]
    for minutes in (1, 5, 19):
        restarted = make_worker(Clock(T0 + timedelta(minutes=minutes)), turn=spy)
        restarted.start()
        restarted.tick()
    assert spy.calls == [False]
    later = make_worker(Clock(T0 + timedelta(minutes=20)), turn=spy)
    later.start()
    later.tick()
    assert spy.calls == [False, False]
    # a run that died before it ended: last_started alone holds the next turn back
    an_hour = T0 + timedelta(hours=1)
    w.db.put(K_LAST_STARTED, an_hour.isoformat(), now=an_hour)
    w.db.delete(K_LAST_ENDED)
    assert make_worker(Clock(an_hour + timedelta(minutes=10))).due() is False
    assert make_worker(Clock(an_hour + timedelta(minutes=20))).due() is True


def test_a_last_started_in_the_future_counts_as_long_ago(setup, make_worker):
    # Review Focus 3: an NTP correction after the clock ran ahead must not hold back every turn.
    w = make_worker(Clock())
    tomorrow = (T0 + timedelta(days=1)).isoformat()
    w.db.put(K_LAST_STARTED, tomorrow, now=T0)
    w.db.put(K_LAST_ENDED, tomorrow, now=T0)
    assert w.due() is True
    w.db.put(K_LAST_ENDED, (T0 - timedelta(minutes=5)).isoformat(), now=T0)
    assert w.due() is False  # the stamp that isn't in the future still counts


def test_an_idle_worker_with_no_sign_in_stays_alive_for_20_minutes(setup, make_worker):
    # C6.13: no token, the gate closed: its loop still ticks, so it is alive.
    clock = Clock()
    w = make_worker(clock)
    w.start()
    assert not (setup.tmp / "plaud" / "tokens.json").exists()
    for tick in range(600):  # 20 minutes of 2-second ticks
        clock.advance(seconds=2)
        w.tick()
        if tick % 15 == 14:  # the heartbeat thread's 30 seconds
            heartbeat_once(w.state, w.progress, now=clock())
            assert alive(w.state, now=clock()) == (True, None)


def test_a_wedged_main_thread_fails_alive_after_15_minutes(setup, make_worker):
    clock = Clock()
    w = make_worker(clock)
    w.start()
    w.tick()  # the last tick that returns
    for _ in range(30):  # heartbeats go on for 15 minutes, with no tick
        clock.advance(seconds=30)
        heartbeat_once(w.state, w.progress, now=clock())
    assert alive(w.state, now=clock()) == (True, None)
    clock.advance(seconds=30)
    heartbeat_once(w.state, w.progress, now=clock())
    assert alive(w.state, now=clock()) == (False, STUCK)
    assert alive(w.state, now=clock() + timedelta(seconds=91)) == (False, SILENT)
    # A worker that is starting (an index rebuild in Writer.open) may take 30 minutes, from its
    # start: the last heartbeat was `idle`, so this start is a new one.
    starting = make_worker(Clock(clock()))
    assert starting.progress.starting_since == clock()
    clock.advance(minutes=29)
    heartbeat_once(starting.state, starting.progress, now=clock())
    assert alive(starting.state, now=clock()) == (True, None)
    clock.advance(minutes=2)
    heartbeat_once(starting.state, starting.progress, now=clock())
    assert alive(starting.state, now=clock()) == (False, STUCK)


def test_a_failed_start_says_so_and_restarts_dont_renew_the_30_minutes(
    setup, make_worker, monkeypatch
):
    # why: spec §9.2. A start that fails (here Writer.open, on a damaged index) makes Docker start
    # the worker again, and each start wrote a fresh `starting` heartbeat: a crash loop read as
    # alive for ever. A failed start now says so, and the 30 minutes run from the first start.
    def damaged(*args, **kwargs):
        raise sqlite3.DatabaseError("synthetic: the index is damaged")

    # Its own scope: monkeypatch.undo() would also undo the autouse fixture that hides this
    # machine's settings (conftest.py), which shares this test's monkeypatch.
    with monkeypatch.context() as patched:
        patched.setattr(worker_module.Writer, "open", damaged)
        for minutes in (0, 10, 20):
            w = make_worker(Clock(T0 + timedelta(minutes=minutes)))
            with pytest.raises(sqlite3.DatabaseError):
                w.run()
            beat = beat_of(setup)
            assert (beat["phase"], beat["error"]) == ("failed", "DatabaseError")
            assert beat["starting_since"] == T0.isoformat()
            assert alive(w.state, now=T0 + timedelta(minutes=minutes)) == (False, FAILED)
        # the next restart says `starting` again, but its 30 minutes ran out since the first start
        again = make_worker(Clock(T0 + timedelta(minutes=31)))
        again._start_heartbeat()
        assert alive(again.state, now=T0 + timedelta(minutes=31)) == (False, STUCK)
    # the start that works reads alive as soon as it ends, not at the next beat; and once a start
    # finishes, the next one is a new run of starts
    fixed = make_worker(Clock(T0 + timedelta(minutes=32)))
    fixed.start()
    assert alive(fixed.state, now=T0 + timedelta(minutes=32)) == (True, None)
    later = T0 + timedelta(minutes=40)
    assert make_worker(Clock(later)).progress.starting_since == later


def test_a_patterns_pass_at_start_reads_as_checking_n_of_m(setup, make_worker, monkeypatch):
    # why: spec §15.2's "last reported 14:21 (checking, 412 of 1,234)". A new auto-private
    # pattern makes the next start check every Plaud recording's titles again (decision 27):
    # each one bumps the progress under the phase `checking`, with its count.
    for n, plaud_id in enumerate((PLAUD_A, PLAUD_B), start=1):
        add_recording(setup, plaud_id, n)
    make_worker(Clock()).start()  # the patterns as configured: their first version needs no pass
    add_pattern(setup)
    w = make_worker(Clock(T0 + timedelta(minutes=1)))
    seen = []
    real = worker_module.patterns_pass

    def watched(writer, db, *, now, progress):
        def each():
            progress()
            beat = w.progress.beat(now)
            seen.append((beat["phase"], beat["done"], beat["total"]))

        return real(writer, db, now=now, progress=each)

    monkeypatch.setattr(worker_module, "patterns_pass", watched)
    w.start()
    assert seen == [("checking", 1, 2), ("checking", 2, 2)]
    assert w.progress.read()[0] == "idle"


class Killed(BaseException):
    """The process killed mid-step (out of memory): no handler sees it, and nothing it would have
    written afterwards is written."""


def test_a_crash_during_the_patterns_pass_at_start_never_gets_a_fresh_30_minutes(
    setup, make_worker, monkeypatch
):
    # why: decision 48. The pass at start is part of the start: one that dies at every restart (an
    # OOM kill, say) is a crash loop, so each restart keeps the first start's time, and it is
    # STUCK 30 minutes after that start, never alive for ever.
    for n, plaud_id in enumerate((PLAUD_A, PLAUD_B), start=1):
        add_recording(setup, plaud_id, n)
    make_worker(Clock()).start()  # a start that finished: the next one begins a new run of starts
    add_pattern(setup)
    current: list[Worker] = []

    def killed(writer, db, *, now, progress):
        progress()  # the pass reaches its first recording
        w = current[-1]
        heartbeat_once(w.state, w.progress, now=w.clock())  # the heartbeat thread's beat
        raise Killed  # and the process dies

    monkeypatch.setattr(worker_module, "patterns_pass", killed)
    first = T0 + timedelta(minutes=1)
    for minutes in (1, 11, 21):
        current.append(make_worker(Clock(T0 + timedelta(minutes=minutes))))
        with pytest.raises(Killed):
            current[-1].start()
        beat = beat_of(setup)
        assert (beat["phase"], beat["starting_since"]) == ("checking", first.isoformat())
        assert alive(current[-1].state, now=T0 + timedelta(minutes=minutes)) == (True, None)
    current.append(make_worker(Clock(T0 + timedelta(minutes=32))))
    assert current[-1].progress.starting_since == first
    with pytest.raises(Killed):
        current[-1].start()
    assert alive(current[-1].state, now=T0 + timedelta(minutes=32)) == (False, STUCK)


def test_the_heartbeat_keeps_writing_during_a_long_synchronous_step(
    setup, make_worker, monkeypatch
):
    # why: spec §9.2. A download or a Retry-After wait blocks the main thread; the heartbeat
    # thread, on its own connections, keeps --alive green. A real thread at a short interval: the
    # main thread blocks on the fourth heartbeat, bounded at 10 seconds.
    clock = Clock()
    beats = []
    reached = threading.Event()
    write = worker_module.heartbeat_once

    def counting(state, progress, *, now):
        clock.advance(seconds=30)  # each beat stands for 30 seconds of the long step
        write(state, progress, now=clock())
        beats.append(now)
        if len(beats) >= 4:
            reached.set()

    monkeypatch.setattr(worker_module, "heartbeat_once", counting)
    w = make_worker(clock, heartbeat=0.01)
    counter = w.progress.read()[1]
    w._start_heartbeat()
    assert reached.wait(10)  # the main thread is blocked here, as in a long download
    w.stopping.set()
    w._beating.join(5)  # a beat already under way lands before the reads below, never between them
    beat = json.loads(w.state.meta("worker_heartbeat"))
    assert w.progress.read()[1] == counter == beat["progress_counter"]
    assert alive(w.state, now=datetime.fromisoformat(beat["at"])) == (True, None)


def test_a_late_consent_listing_lands_at_the_next_tick_with_no_sign_in(setup, make_worker):
    # C5.11: from the list and the index alone, whatever the sign-in or the gate.
    rid = add_recording(setup, PLAUD_A)
    clock = Clock()
    w = make_worker(clock)
    w.start()
    w.tick()
    assert not is_private(Archive(setup.root).load(rid))
    consent_list(setup.consent, PLAUD_C, PLAUD_A)
    clock.advance(seconds=2)
    w.tick()
    rec = Archive(setup.root).load(rid)
    assert any(t.tag == "private" and t.by == "auto" for t in rec.tags)
    assert [(e.kind, e.recording_id) for e in w.db.open_flags()] == [(CONSENT_LATE, rid)]
    assert not (setup.tmp / "plaud").exists()  # no sign-in, ever


def test_a_list_replaced_by_rename_is_read_at_the_next_tick(setup, make_worker):
    # C5.14: the folder mount sees a replaced file, and its new inode is read at once. K_CONSENT
    # keeps the count, never the IDs.
    clock = Clock()
    w = make_worker(clock)
    w.start()
    w.tick()
    assert w.db.get(K_CONSENT)["count"] == 1
    consent_list(setup.consent, PLAUD_C, PLAUD_B)
    clock.advance(seconds=2)
    w.tick()
    assert w.consent.ids == {canonical_plaud_id(i) for i in (PLAUD_B, PLAUD_C)}
    read = w.db.get(K_CONSENT)
    assert (read["count"], read["read_at"], read["stop"]) == (2, clock().isoformat(), None)
    assert canonical_plaud_id(PLAUD_B) not in json.dumps(read)


def test_a_list_that_changes_while_read_is_read_again_next_tick(setup, make_worker, monkeypatch):
    # Review Focus 2: an editor or `sed -i` writing in place. A half-written list must never be
    # read as "exclude fewer": the previous list stays in force until a read the file holds still
    # for.
    clock = Clock()
    w = make_worker(clock)
    w.start()
    w.tick()
    assert w.consent.ids == {canonical_plaud_id(PLAUD_C)}
    real_read = keepout.read_consent

    def read_while_an_editor_writes(path):
        reading = real_read(path)  # the half-written list: PLAUD_C is missing from it
        consent_list(setup.consent, PLAUD_C, PLAUD_A, PLAUD_B)  # and the write goes on
        return reading

    consent_list(setup.consent, PLAUD_A)
    monkeypatch.setattr(keepout, "read_consent", read_while_an_editor_writes)
    clock.advance(seconds=2)
    w.tick()
    assert w.consent.ids == {canonical_plaud_id(PLAUD_C)}
    monkeypatch.setattr(keepout, "read_consent", real_read)
    clock.advance(seconds=2)
    w.tick()
    assert w.consent.ids == {canonical_plaud_id(i) for i in (PLAUD_A, PLAUD_B, PLAUD_C)}


def test_an_edit_that_keeps_the_size_and_the_mtime_is_still_read(setup, make_worker):
    # why: `cp -p` or `touch -r` after an edit in place leaves the inode, the size and the mtime
    # as they were; the change time still moves, and the worker reads the list again.
    clock = Clock()
    w = make_worker(clock)
    w.start()
    w.tick()
    path = setup.consent / "consent-list.md"
    old = path.stat()
    time.sleep(0.05)  # the file system's change time moves on (Linux keeps it coarse)
    with path.open("r+", encoding="utf-8") as fh:  # PLAUD_C's line becomes PLAUD_A's, in place
        text = fh.read()
        fh.seek(0)
        fh.write(text.replace(PLAUD_C, PLAUD_A))
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    new = path.stat()
    assert (new.st_ino, new.st_size, new.st_mtime_ns) == (old.st_ino, old.st_size, old.st_mtime_ns)
    clock.advance(seconds=2)
    w.tick()
    assert w.consent.ids == {canonical_plaud_id(PLAUD_A)}


def test_what_was_read_is_written_again_until_it_lands(setup, make_worker, monkeypatch):
    # why: spec §16, "worked out afresh each turn". A busy state.db that refuses K_CONSENT's write,
    # or a blocker row another process removed, is put right at the next tick, not when the file
    # next changes.
    clock = Clock()
    w = make_worker(clock)
    w.start()
    w.tick()
    real_put = w.db.put

    def busy(key, value, *, now):
        if key == K_CONSENT:
            raise sqlite3.OperationalError("database is locked")
        real_put(key, value, now=now)

    consent_list(setup.consent, PLAUD_C, extra="- `" + "a" * 31 + "` — one digit short\n")
    monkeypatch.setattr(w.db, "put", busy)
    clock.advance(seconds=2)
    w.tick()
    assert w.db.get(K_CONSENT)["stop"] is None  # not written
    monkeypatch.setattr(w.db, "put", real_put)
    clock.advance(seconds=2)
    w.tick()
    assert w.db.get(K_CONSENT)["typo_lines"] == [3]
    assert "consent" in sync_health(w.state, schedule_minutes=20).blockers
    w.db.delete(BLOCKER_PREFIX + "consent")  # a restore, or another process, removed the row
    clock.advance(seconds=2)
    w.tick()
    assert "consent" in sync_health(w.state, schedule_minutes=20).blockers


def test_a_consent_typo_opens_the_consent_blocker_at_the_tick_and_a_clean_read_clears_it(
    setup, make_worker
):
    # C5.08 and decision 13: Status shows a typo within seconds, and the check stops being pinged.
    clock = Clock()
    w = make_worker(clock)
    w.start()
    w.tick()
    assert "consent" not in sync_health(w.state, schedule_minutes=20).blockers
    consent_list(setup.consent, PLAUD_C, extra="- `" + "a" * 31 + "` — one digit short\n")
    clock.advance(seconds=2)
    w.tick()
    health = sync_health(w.state, schedule_minutes=20)
    assert "consent" in health.blockers and not health.may_ping()
    assert w.db.get(K_CONSENT)["typo_lines"] == [3]
    consent_list(setup.consent, PLAUD_C)
    clock.advance(seconds=2)
    w.tick()
    assert "consent" not in sync_health(w.state, schedule_minutes=20).blockers


def test_a_typo_stop_still_applies_the_exclusions_that_read(setup, make_worker):
    # why: spec §12.1. A typo stops every import, but adding `private` is always safe: the
    # exclusions that do read are applied at once, and the stop shows as the consent blocker.
    rid = add_recording(setup, PLAUD_A)
    consent_list(setup.consent, PLAUD_C, PLAUD_A, extra="- `" + "a" * 31 + "` — one digit short\n")
    w = make_worker(Clock())
    w.start()
    w.tick()
    assert w.consent.stop is not None and canonical_plaud_id(PLAUD_A) in w.consent.ids
    assert is_private(Archive(setup.root).load(rid))
    assert [(e.kind, e.recording_id) for e in w.db.open_flags()] == [(CONSENT_LATE, rid)]
    assert "consent" in sync_health(w.state, schedule_minutes=20).blockers


def test_a_private_that_cant_be_written_opens_its_blocker_and_flag_until_it_lands(
    setup, make_worker
):
    # why: spec §16's `private-unwritten`. A consent-listed recording whose file was edited
    # outside the app can't be tagged until `recordings reindex` accepts the edit.
    rid = add_recording(setup, PLAUD_A)
    edited_by_hand(setup, rid)
    consent_list(setup.consent, PLAUD_C, PLAUD_A)
    clock = Clock()
    w = make_worker(clock)
    w.start()
    w.tick()
    assert not is_private(Archive(setup.root).load(rid))
    (flag,) = w.db.open_flags((PRIVATE_UNWRITTEN,))
    assert flag.recording_id == rid
    assert flag.detail == {"owed": "consent", "cause": "StaleFile"}  # Task 19 words it by cause
    assert "private-unwritten" in sync_health(w.state, schedule_minutes=20).blockers
    clock.advance(seconds=2)
    w.tick()  # still owed: still one flag
    assert len(w.db.open_flags((PRIVATE_UNWRITTEN,))) == 1
    w.writer.reindex()  # `recordings reindex` accepts the edit
    clock.advance(seconds=2)
    w.tick()
    assert is_private(Archive(setup.root).load(rid))
    assert w.db.open_flags((PRIVATE_UNWRITTEN,)) == []
    assert "private-unwritten" not in sync_health(w.state, schedule_minutes=20).blockers


def test_a_private_no_longer_owed_closes_its_flag_and_the_blocker(setup, make_worker):
    # why: a line added by mistake, then taken out. Nothing is owed any more, so the flag and the
    # blocker close, though the recording never became private: else the sync's check would stay
    # unpinged for good, with a remedy (`recordings reindex`) that couldn't clear it.
    rid = add_recording(setup, PLAUD_A)
    edited_by_hand(setup, rid)
    consent_list(setup.consent, PLAUD_C, PLAUD_A)
    clock = Clock()
    w = make_worker(clock)
    w.start()
    w.tick()
    assert len(w.db.open_flags((PRIVATE_UNWRITTEN,))) == 1
    consent_list(setup.consent, PLAUD_C, extra="- `" + "a" * 31 + "` — one digit short\n")
    clock.advance(seconds=2)
    w.tick()  # a stop can't say the line went: the flag stays
    assert len(w.db.open_flags((PRIVATE_UNWRITTEN,))) == 1
    consent_list(setup.consent, PLAUD_C)
    clock.advance(seconds=2)
    w.tick()
    assert w.db.open_flags((PRIVATE_UNWRITTEN,)) == []
    assert "private-unwritten" not in sync_health(w.state, schedule_minutes=20).blockers
    assert not is_private(Archive(setup.root).load(rid))


def test_a_late_consent_pass_that_fails_opens_the_blocker_logs_once_and_clears(
    setup, make_worker, monkeypatch, caplog
):
    # why: spec §13.2, "shows it at once". A pass that fails as a whole (a full or read-only
    # disk) owes every `private` it would have written: the private-unwritten blocker opens with
    # the error's class, its own step runs whatever the pass did, and the log says so once.
    clock = Clock()
    w = make_worker(clock)
    w.start()
    w.tick()
    real = worker_module.apply_late_consent

    def full_disk(*args, **kwargs):
        raise OSError(28, "synthetic: no space left on device")

    monkeypatch.setattr(worker_module, "apply_late_consent", full_disk)
    caplog.set_level(logging.WARNING, logger="recordings.worker")
    for _ in range(3):
        clock.advance(seconds=2)
        w.tick()
    assert blocker(w, "private-unwritten")["detail"] == {"error": "OSError"}
    assert not sync_health(w.state, schedule_minutes=20).may_ping()
    failed = [r.getMessage() for r in caplog.records if "late consent failed" in r.getMessage()]
    assert failed == ["late consent failed (OSError)"]
    assert "no space" not in caplog.text  # the class only, never the text
    monkeypatch.setattr(worker_module, "apply_late_consent", real)
    clock.advance(seconds=2)
    w.tick()
    assert blocker(w, "private-unwritten") is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root enters any folder")
def test_a_folder_it_cant_enter_is_never_read_as_gone(setup, make_worker, monkeypatch):
    # why: a file in a folder this user can't enter reads as missing (Path.is_file() is False), so
    # Archive.load raises KeyError as if the recording were gone. While its folder exists, the
    # flag stays open: here through a failed pass, which leaves no `unwritten` to keep it.
    rid = add_recording(setup, PLAUD_A)
    edited_by_hand(setup, rid)
    consent_list(setup.consent, PLAUD_C, PLAUD_A)
    clock = Clock()
    w = make_worker(clock)
    w.start()
    w.tick()
    assert len(w.db.open_flags((PRIVATE_UNWRITTEN,))) == 1

    def full_disk(*args, **kwargs):
        raise OSError(28, "synthetic: no space left on device")

    monkeypatch.setattr(worker_module, "apply_late_consent", full_disk)
    folder = Archive(setup.root).path_for(rid)
    mode = folder.stat().st_mode
    folder.chmod(0)
    try:
        clock.advance(seconds=2)
        w.tick()
        assert len(w.db.open_flags((PRIVATE_UNWRITTEN,))) == 1
    finally:
        folder.chmod(mode)


@pytest.mark.skipif(os.geteuid() == 0, reason="root enters any folder")
def test_a_folder_under_a_parent_it_cant_enter_is_never_read_as_gone(
    setup, make_worker, monkeypatch
):
    # why: Task 8's rule. Path.exists() reads any OSError as "no", so a month folder the worker
    # can't enter would read as the recording removed, and its flag and the blocker would close
    # while its `private` is still owed. Only a `stat` that says "not found" makes it gone.
    rid = add_recording(setup, PLAUD_A)
    edited_by_hand(setup, rid)
    consent_list(setup.consent, PLAUD_C, PLAUD_A)
    clock = Clock()
    w = make_worker(clock)
    w.start()
    w.tick()
    assert len(w.db.open_flags((PRIVATE_UNWRITTEN,))) == 1

    def full_disk(*args, **kwargs):
        raise OSError(28, "synthetic: no space left on device")

    monkeypatch.setattr(worker_module, "apply_late_consent", full_disk)
    parent = Archive(setup.root).path_for(rid).parent
    mode = parent.stat().st_mode
    parent.chmod(0)
    try:
        clock.advance(seconds=2)
        w.tick()
        assert len(w.db.open_flags((PRIVATE_UNWRITTEN,))) == 1
    finally:
        parent.chmod(mode)


def test_an_index_that_cant_answer_for_a_minute_opens_the_blocker(setup, make_worker, monkeypatch):
    # why: an index that can't answer stops every late consent listing as surely as a pass that
    # fails. A busy one is retried at each tick; past a minute it is shown the same way, and it
    # clears once the index answers.
    clock = Clock()
    w = make_worker(clock)
    w.start()
    w.tick()
    real = worker_module.apply_late_consent

    def unavailable(*args, **kwargs):
        return dataclasses.replace(real(*args, **kwargs), index_unavailable=True)

    monkeypatch.setattr(worker_module, "apply_late_consent", unavailable)
    for _ in range(2):
        clock.advance(seconds=30)
        w.tick()
    assert blocker(w, "private-unwritten") is None  # 30 seconds: still retrying
    clock.advance(seconds=30)
    w.tick()
    assert blocker(w, "private-unwritten")["detail"] == {"error": "IndexUnavailable"}
    monkeypatch.setattr(worker_module, "apply_late_consent", real)
    clock.advance(seconds=2)
    w.tick()
    assert blocker(w, "private-unwritten") is None


def test_a_bad_keep_out_word_leaves_the_worker_idle_and_alive(setup, make_worker):
    # why: spec §9.2. It stays up and healthy; the turn idles, and doctor and Status say why.
    setup.cfg_file.write_text(
        setup.cfg_file.read_text(encoding="utf-8").replace(
            "[plaud]\n", '[plaud]\nkeep_out_word = "no archive!"\n'
        ),
        encoding="utf-8",
    )
    clock, spy = Clock(), TurnSpy()
    w = make_worker(clock, turn=spy)
    catch_up_done(w)
    w.start()
    w.tick()
    assert spy.calls == [] and last_turn(w)[0] == "idle-config"
    assert "no archive!" not in (w.db.get(K_KEEP_OUT_WORDS) or [])
    heartbeat_once(w.state, w.progress, now=clock())
    assert alive(w.state, now=clock()) == (True, None)


def test_sigterm_stops_between_ticks(setup, make_worker, monkeypatch):
    # why: spec §9.2. `docker stop` sends SIGTERM; the tick in progress finishes, and no other
    # starts, within the 30-second grace.
    w = make_worker(Clock())
    seen = []
    real_tick = w.tick

    def tick():
        seen.append("begin")
        if len(seen) == 1:
            signal.raise_signal(signal.SIGTERM)
        real_tick()
        seen.append("end")

    monkeypatch.setattr(w, "tick", tick)
    before = signal.getsignal(signal.SIGTERM)
    assert w.run() == 0
    assert seen == ["begin", "end"]
    assert signal.getsignal(signal.SIGTERM) == before


def test_sigterm_stops_the_patterns_pass_at_start_between_recordings(
    setup, make_worker, monkeypatch
):
    # why: spec §9.2, "it stops between recordings". `docker stop` gives 30 seconds: a long pass at
    # start ends after the recording in hand, isn't recorded as complete, and the next start runs
    # it again.
    for n, plaud_id in enumerate((PLAUD_A, PLAUD_B), start=1):
        add_recording(setup, plaud_id, n)
    make_worker(Clock()).start()
    add_pattern(setup)
    reconciled = []
    real = Writer.reconcile_plaud

    def reconcile(writer, recording_id):
        reconciled.append(recording_id)
        if len(reconciled) == 1:
            signal.raise_signal(signal.SIGTERM)  # `docker stop`, during the first recording
        return real(writer, recording_id)

    monkeypatch.setattr(Writer, "reconcile_plaud", reconcile)
    w = make_worker(Clock(T0 + timedelta(minutes=1)))
    assert w.run() == 0
    assert len(reconciled) == 1
    assert w.db.get(K_PATTERNS_PASS) is None  # not complete: the next start runs it again
    make_worker(Clock(T0 + timedelta(minutes=2))).start()
    assert len(reconciled) == 3 and w.db.get(K_PATTERNS_PASS)["version"] == 2


def test_recordings_worker_alive_exits_0_or_1(setup, make_worker, monkeypatch, capsys):
    for key, value in setup.environ.items():
        monkeypatch.setenv(key, value)
    assert main(["worker", "--alive"]) == 1  # never started: no heartbeat
    assert capsys.readouterr().out.strip() == SILENT
    now = datetime.now(UTC)
    w = make_worker(Clock(now))
    heartbeat_once(w.state, w.progress, now=now)
    assert main(["worker", "--alive"]) == 0
    heartbeat_once(w.state, w.progress, now=now - timedelta(minutes=2))
    assert main(["worker", "--alive"]) == 1
    assert capsys.readouterr().out.strip() == SILENT


def test_alive_with_a_config_it_cant_read_exits_1_with_a_fixed_line(setup, monkeypatch, capsys):
    # why: Docker's health check gives a meaning to 0 and 1 only (2 is reserved), so `--alive`
    # says "unhealthy" as 1, whatever stops it, in a fixed line that never carries the config's
    # text or path.
    broken = setup.tmp / "broken-config" / "config.toml"
    broken.parent.mkdir()
    broken.write_text('[archive]\npath = "/srv/synthetic-path\n', encoding="utf-8")  # unclosed
    for config_file in (broken, setup.tmp / "nowhere" / "config.toml"):
        monkeypatch.setenv("RECORDINGS_CONFIG", str(config_file))
        assert main(["worker", "--alive"]) == 1
        out, err = capsys.readouterr()
        assert (out.strip(), err) == (worker_module.NO_CONFIG, "")
        assert "synthetic-path" not in out and "nowhere" not in out


def test_anything_else_that_stops_the_worker_prints_its_class_only(setup, monkeypatch, capsys):
    # why: spec §15.6, class names only. An exception none of main's three exits names would end
    # in a traceback that carries its text.
    for key, value in setup.environ.items():
        monkeypatch.setenv(key, value)

    class Broken:
        def __init__(self, cfg, environ):
            pass

        def run(self):
            raise RuntimeError("synthetic text that names /srv/a-path")

    monkeypatch.setattr(worker_module, "Worker", Broken)
    assert worker_module.main([]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "a-path" not in err


def test_the_workers_writer_writes_no_docs(setup, make_worker):
    # why: spec §9.1's table. Only the web app's and the CLI's Writers write the archive's docs.
    readme = setup.root / "README.md"
    assert readme.is_file()  # init_archive wrote them
    readme.unlink()
    make_worker(Clock()).start()
    assert not readme.exists()
    Writer.open(load_config(setup.environ))
    assert readme.is_file()


def test_importing_the_worker_brings_no_client_token_store_or_sync():
    # why: requests.insert (Task 14) and the views (Task 19) read `alive` from recordings.worker,
    # in `web`, which must never hold the Plaud client, the token store or the sync. Phase 2
    # passes its turn in.
    code = (
        "import sys, recordings.worker\n"
        "names = ('recordings.plaud.client', 'recordings.plaud.tokens', 'recordings.plaud.sync',"
        " 'recordings.sources.plaud')\n"
        "print([n for n in names if n in sys.modules])\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]"
