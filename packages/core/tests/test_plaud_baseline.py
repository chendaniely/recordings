"""The baseline's privacy part, the new-pattern pass, the keep-out words enforced and the `plaud`
lock (stage 2b spec §14.5, §13.1, §12.2, §9.4). Every archive, title and Plaud ID is synthetic."""

import json
import sqlite3
from datetime import UTC, datetime

import pytest
from recordings.archive import RawSource
from recordings.index import Index
from recordings.locks import OPS_LOCK, PLAUD_LOCK, Locks, LockTimeout, running
from recordings.models import is_private
from recordings.plaud.baseline import (
    DROPPED,
    KEEP_CONFIGURED,
    NOT_ENFORCED,
    KeepOutWordsError,
    baseline_privacy,
    drop_word,
    enforce_word,
    enforced_words,
    patterns_pass,
)
from recordings.plaud.checks import K_KEEP_OUT_WORDS, K_PATTERNS_PASS, PlaudDB
from recordings.plaud.flags import record_late_keep_out
from recordings.plaud.runlock import plaud_holder, plaud_run
from recordings.state import State
from recordings.writer import Writer

NOW = datetime(2026, 10, 23, 14, 20, tzinfo=UTC)
T1 = datetime(2026, 8, 1, 16, 10, tzinfo=UTC)


def imported_by_2a(writer, make_incoming, plaud_env, n: int, name: str) -> str:
    """A recording as 2a's import left it: a Plaud snapshot, and no marker (no reconcile here)."""
    fid = f"{n:032x}"
    payload = json.dumps(plaud_env(fid, name=name)).encode()
    source = RawSource(kind="plaud", ref=fid, added_at=T1, fetched_at=T1, payload=payload)
    return writer.add(make_incoming(title=name, sources=(source,))).recording.id


def test_the_baseline_checks_every_title_of_2a_recordings_once(writer, make_incoming, plaud_env):
    # why: §14.5, §13.1 case 4. Before the catch-up, every title the marker never saw is checked;
    # a second run checks none.
    a = imported_by_2a(writer, make_incoming, plaud_env, 1, "Week 4")
    b = imported_by_2a(writer, make_incoming, plaud_env, 2, "Interview: Ada")
    writer.add(make_incoming(sources=()))  # no Plaud source: not the baseline's
    db = PlaudDB(writer.state)
    first = baseline_privacy(writer, db, now=NOW)
    assert first.to_dict() == {
        "titles_checked": 2,
        "private_added": 1,
        "kept_out_late": 0,
        "already_seen": 0,
        "unwritten": [],
        "busy": [],
        "causes": {},
    }
    assert is_private(writer.archive.load(b)) and not is_private(writer.archive.load(a))
    second = baseline_privacy(writer, db, now=NOW)
    assert (second.titles_checked, second.private_added, second.already_seen) == (0, 0, 2)


def test_the_baseline_tags_a_2a_keep_out_title_private_and_flags_it(
    writer, make_incoming, plaud_env
):
    rid = imported_by_2a(writer, make_incoming, plaud_env, 3, "10-21 noarchive coffee")
    db = PlaudDB(writer.state)
    report = baseline_privacy(writer, db, now=NOW)
    assert (report.private_added, report.kept_out_late) == (1, 1)
    assert is_private(writer.archive.load(rid))
    assert [(e.kind, e.recording_id) for e in db.open_flags()] == [("kept-out-late", rid)]


def test_the_baseline_lists_a_recording_it_cant_write(writer, make_incoming, plaud_env):
    rid = imported_by_2a(writer, make_incoming, plaud_env, 4, "Interview: Ada")
    path = writer.archive.path_for(rid) / "recording.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"rev": 1', '"rev": 7'), "utf-8")
    report = baseline_privacy(writer, PlaudDB(writer.state), now=NOW)
    assert report.unwritten == (rid,) and report.private_added == 0


def test_ops_and_plaud_in_one_hold_announce_the_job_on_both(writer):
    # why: §9.4. The catch-up holds both, and the deploy guard, which asks ops, sees it.
    state = writer.state
    with plaud_run(state, "plaud-catch-up", timeout=0, now=NOW, with_ops=True):
        assert running(state.locks_dir, OPS_LOCK) == ("plaud-catch-up", NOW)
        assert running(state.locks_dir, PLAUD_LOCK) == ("plaud-catch-up", NOW)
        assert plaud_holder(state) == ("plaud-catch-up", NOW)
        with pytest.raises(LockTimeout), Locks(state.locks_dir, timeout=0).hold(OPS_LOCK):
            pass
    assert plaud_holder(state) is None and running(state.locks_dir, OPS_LOCK) is None
    with plaud_run(state, "plaud-sync", timeout=0, now=NOW):
        assert running(state.locks_dir, OPS_LOCK) is None  # a sync never takes ops (§9.4)
        with pytest.raises(LockTimeout), plaud_run(state, "plaud-full", timeout=0.1, now=NOW):
            pass  # one sync at a time
    with pytest.raises(ValueError):
        plaud_run(state, "backup", timeout=0, now=NOW).__enter__()


def test_changing_the_keep_out_word_releases_nothing(writer):
    # why: §12.2, decision 26. Each word the worker enforced stays enforced, and what an older
    # word kept out stays out.
    db = PlaudDB(writer.state)
    assert enforce_word(db, "noarchive", now=NOW) == ("noarchive",)
    record_late_keep_out(db, "20261006T140003-0700_aaaaaaaa", ["a" * 32], "noarchive", now=NOW)
    assert enforce_word(db, "hideme", now=NOW) == ("noarchive", "hideme")
    assert enforce_word(db, "HideMe", now=NOW) == ("noarchive", "hideme")  # folded: one word
    assert enforce_word(db, "not a word!", now=NOW) == ("noarchive", "hideme")
    assert db.listing("a" * 32).kept_out_at is not None
    renamed = Writer(
        writer.root,
        State.open(writer.state.path),
        Index(writer.index.path),
        writer_id="test",
        keep_out_word="hideme",
    )
    assert renamed.keep_out_words() == ("hideme", "noarchive")


def test_keep_out_words_drop_removes_an_old_word_and_refuses_the_configured_one(writer):
    db = PlaudDB(writer.state)
    enforce_word(db, "skipme", now=NOW)
    enforce_word(db, "noarchive", now=NOW)
    assert drop_word(db, "NoArchive", configured="noarchive", now=NOW) == KEEP_CONFIGURED
    assert drop_word(db, "elsewhere", configured="noarchive", now=NOW) == NOT_ENFORCED
    assert drop_word(db, "SKIPME", configured="noarchive", now=NOW) == DROPPED
    assert enforced_words(db) == ("noarchive",)


def test_a_new_pattern_is_checked_once_over_the_archive(writer, make_incoming, plaud_env):
    # why: §13.1 case 5, decision 27. The worker's start runs one pass per new version.
    first = Writer(
        writer.root,
        State.open(writer.state.path),
        Index(writer.index.path),
        writer_id="test",
        auto_private=(r"^\s*private",),
    )
    therapy = imported_by_2a(first, make_incoming, plaud_env, 5, "Therapy check-in")
    imported_by_2a(first, make_incoming, plaud_env, 6, "Week 4")
    db = PlaudDB(writer.state)
    baseline_privacy(first, db, now=NOW)
    assert patterns_pass(first, db, now=NOW) is None  # the first version needs no pass
    wider = Writer(
        writer.root,
        State.open(writer.state.path),
        Index(writer.index.path),
        writer_id="test",
        auto_private=(r"^\s*private", r"therapy"),
    )
    bumps = []
    done = patterns_pass(wider, db, now=NOW, progress=lambda: bumps.append(1))
    assert (done.version, done.checked, done.tagged) == (2, 2, 1)
    assert done.sentence() == "patterns changed: 2 titles checked, 1 tagged private"
    assert is_private(wider.archive.load(therapy))
    # §9.2: the worker's start counts each recording as progress, so a long pass isn't "stuck".
    assert len(bumps) == 2
    assert patterns_pass(wider, db, now=NOW) is None  # a second start runs no pass


def refuse_markers(monkeypatch, rid: str, error: Exception) -> None:
    """state.db (or the recording's lock) refuses `rid`'s marker write, as a busy disk would."""
    real = PlaudDB.set_markers

    def refusing(self, recording_id, markers, *, now):
        if recording_id == rid:
            raise error
        return real(self, recording_id, markers, now=now)

    monkeypatch.setattr(PlaudDB, "set_markers", refusing)


def test_a_state_db_refusal_lists_the_recording_and_the_pass_goes_on(
    writer, make_incoming, plaud_env, monkeypatch
):
    # why: Task 9's "a state.db refusal never stops a whole pass": one tuple, shared with reindex.
    first = imported_by_2a(writer, make_incoming, plaud_env, 7, "Week 5")
    second = imported_by_2a(writer, make_incoming, plaud_env, 8, "Interview: Ada")
    refuse_markers(monkeypatch, first, sqlite3.OperationalError("database is locked: Week 5"))
    report = baseline_privacy(writer, PlaudDB(writer.state), now=NOW)
    assert report.unwritten == (first,) and report.causes == {first: "OperationalError"}
    assert (report.titles_checked, report.private_added) == (1, 1)
    assert is_private(writer.archive.load(second))


def test_a_locked_recording_is_busy_not_unwritten(writer, make_incoming, plaud_env, monkeypatch):
    # why: Global Constraints: a LockTimeout is "busy", neither a failure nor an attempt.
    busy = imported_by_2a(writer, make_incoming, plaud_env, 9, "Week 6")
    refuse_markers(monkeypatch, busy, LockTimeout("busy"))
    report = baseline_privacy(writer, PlaudDB(writer.state), now=NOW)
    assert report.busy == (busy,) and report.unwritten == ()


def test_an_invalid_recording_json_is_listed_not_skipped(writer, make_incoming, plaud_env):
    rid = imported_by_2a(writer, make_incoming, plaud_env, 10, "Interview: Ada")
    (writer.archive.path_for(rid) / "recording.json").write_text("{not json", "utf-8")
    db = PlaudDB(writer.state)
    report = baseline_privacy(writer, db, now=NOW)
    assert report.unwritten == (rid,) and report.causes == {rid: "InvalidFile"}


def wider_writer(writer, patterns=(r"^\s*private", r"therapy")):
    return Writer(
        writer.root,
        State.open(writer.state.path),
        Index(writer.index.path),
        writer_id="test",
        auto_private=patterns,
    )


def first_pass_done(writer, make_incoming, plaud_env):
    first = wider_writer(writer, (r"^\s*private",))
    therapy = imported_by_2a(first, make_incoming, plaud_env, 11, "Therapy check-in")
    other = imported_by_2a(first, make_incoming, plaud_env, 12, "Week 7")
    baseline_privacy(first, PlaudDB(writer.state), now=NOW)
    assert patterns_pass(first, PlaudDB(writer.state), now=NOW) is None
    return therapy, other


def test_a_pattern_pass_that_could_not_write_one_is_reported_and_runs_again(
    writer, make_incoming, plaud_env, monkeypatch
):
    # why: §13.2. The pass says what it couldn't check, and isn't recorded as complete.
    _, other = first_pass_done(writer, make_incoming, plaud_env)
    db, wider = PlaudDB(writer.state), wider_writer(writer)
    with monkeypatch.context() as patch:
        refuse_markers(patch, other, sqlite3.OperationalError("disk full"))
        done = patterns_pass(wider, db, now=NOW)
    assert done.unwritten == {other: "OperationalError"} and done.tagged == 1
    assert done.sentence() == "patterns changed: 1 title checked, 1 tagged private, 1 not written"
    assert db.get(K_PATTERNS_PASS) == {
        "version": 1,
        "attempted": 2,
        "at": NOW.isoformat(),
        "checked": 1,
        "tagged": 1,
        "unwritten": [other],
        "busy": [],
    }
    again = patterns_pass(wider, db, now=NOW)  # the next start runs it again
    assert again is not None and again.unwritten == {}
    assert db.get(K_PATTERNS_PASS)["version"] == 2 and db.get(K_PATTERNS_PASS)["unwritten"] == []
    assert patterns_pass(wider, db, now=NOW) is None


def test_a_pattern_pass_lists_a_busy_recording_and_an_invalid_one(
    writer, make_incoming, plaud_env, monkeypatch
):
    therapy, other = first_pass_done(writer, make_incoming, plaud_env)
    (writer.archive.path_for(therapy) / "recording.json").write_text("{not json", "utf-8")
    db, wider = PlaudDB(writer.state), wider_writer(writer)
    refuse_markers(monkeypatch, other, LockTimeout("busy"))
    done = patterns_pass(wider, db, now=NOW)
    assert done.unwritten == {therapy: "InvalidFile"} and done.busy == (other,)
    assert done.sentence().endswith("1 not written, 1 busy")
    assert db.get(K_PATTERNS_PASS)["version"] == 1 and not done.complete


def test_a_keep_out_words_row_that_is_not_a_list_is_refused_and_left_alone(writer):
    db = PlaudDB(writer.state)
    db.put(K_KEEP_OUT_WORDS, {"words": ["skipme"]}, now=NOW)
    with pytest.raises(KeepOutWordsError, match="keep_out_words"):
        enforce_word(db, "noarchive", now=NOW)
    with pytest.raises(KeepOutWordsError):
        drop_word(db, "skipme", configured="noarchive", now=NOW)
    with pytest.raises(KeepOutWordsError):
        enforced_words(db)
    assert db.get(K_KEEP_OUT_WORDS) == {"words": ["skipme"]}


def test_the_cli_waits_a_minute_for_plaud():
    from recordings.plaud import runlock

    assert runlock.CLI_WAIT == 60.0
