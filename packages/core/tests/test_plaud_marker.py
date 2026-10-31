"""The auto-private marker (stage 2b spec §13): a removed `private` stays removed until a Plaud
title changes, in every Writer that reconciles. Every archive, title and Plaud ID is synthetic."""

import json
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from recordings.archive import RawSource
from recordings.index import Index
from recordings.models import is_private
from recordings.plaud.checks import Marker, PlaudDB
from recordings.plaud.privacy import (
    PatternSet,
    current_patterns,
    judge,
    title_sha256,
    why_phrase,
)
from recordings.state import State
from recordings.tagops import RemoveTags
from recordings.writer import StaleFile, Writer

HEX = "0123456789abcdef0123456789abcdef"
OTHER = "fedcba9876543210fedcba9876543210"
T1 = datetime(2026, 8, 1, 16, 10, tzinfo=UTC)
LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)
V1 = PatternSet(1, (r"^\s*private", r"interview:"), {1: (r"^\s*private", r"interview:")})


def snapshot(plaud_env, name: str, day: int, *, fid: str = HEX) -> RawSource:
    when = T1 + timedelta(days=day)
    # Each fetch's presigned link differs, as Plaud's do, so no two snapshots are the same bytes.
    payload = json.dumps(plaud_env(fid, name=name, presigned=f"day-{day}")).encode()
    return RawSource(kind="plaud", ref=fid, added_at=when, fetched_at=when, payload=payload)


def add(writer, make_incoming, plaud_env, name: str, *, content=b"rec-1", day=0, fid=HEX, **over):
    """The recording whose media is `content`, with one more Plaud snapshot titled `name`: the
    first call creates it, a later one merges the snapshot into it. Then a reconcile."""
    source = snapshot(plaud_env, name, day, fid=fid)
    rid = writer.add(
        make_incoming(content, **{"title": name, "sources": (source,), **over})
    ).recording.id
    return rid, writer.reconcile_plaud(rid)


def remove_private(writer, rid) -> None:
    writer.mutate(rid, RemoveTags(("private",), frozenset({rid})))  # Dan confirmed it


def web_writer(writer, **over) -> Writer:
    """A second Writer on the same archive and state.db: the `web` role's, as tagging opens it."""
    return Writer(
        writer.root,
        State.open(writer.state.path),
        Index(writer.index.path),
        writer_id="test",
        **over,
    )


def retagged(writer) -> list[str]:
    return [
        e.detail["why"] for e in PlaudDB(writer.state).events_since(LONG_AGO, ["retagged-private"])
    ]


def test_a_removed_private_stays_removed_through_a_reindex_in_a_web_writer(
    writer, make_incoming, plaud_env
):
    # why: C5.16, §13.2. 2a's title_seen rule re-added a removed tag whenever title_seen couldn't
    # advance, as for a title Dan owns (title_by "you"); the marker is in state.db, which every
    # Writer reads, whatever its role.
    rid, report = add(
        writer, make_incoming, plaud_env, "Interview: Ada", title="My own title", title_by="you"
    )
    assert report.private_added == "first check" and is_private(writer.archive.load(rid))
    remove_private(writer, rid)
    web = web_writer(writer)
    assert web.reindex().reconciled[0].private_added is None
    assert not is_private(writer.archive.load(rid))
    add(web, make_incoming, plaud_env, "Private: synthetic", day=1)
    assert is_private(writer.archive.load(rid))
    assert retagged(writer) == ["first check", "title changed"]


def test_a_title_change_that_doesnt_match_moves_the_marker_without_a_tag(
    writer, make_incoming, plaud_env
):
    rid, _ = add(writer, make_incoming, plaud_env, "Week 4")
    db = PlaudDB(writer.state)
    assert db.markers(rid)[HEX].title_sha256 == title_sha256("Week 4")
    _, report = add(writer, make_incoming, plaud_env, "Week 5", day=1)
    assert report.private_added is None and not is_private(writer.archive.load(rid))
    assert db.markers(rid)[HEX].title_sha256 == title_sha256("Week 5")
    assert retagged(writer) == []


def test_a_failed_mutate_leaves_the_marker_and_retries_after_reindex(
    writer, make_incoming, plaud_env
):
    # why: §13.2. The marker moves only once the result is written; a file edited outside the app
    # is retried after `recordings reindex` accepts the edit.
    rid, _ = add(writer, make_incoming, plaud_env, "Week 4")
    writer.add(
        make_incoming(b"rec-1", sources=(snapshot(plaud_env, "Private: synthetic", 1),))
    )  # merged, not yet reconciled
    path = writer.archive.path_for(rid) / "recording.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"Week 4"', '"Edited"'), "utf-8")
    with pytest.raises(StaleFile):
        writer.reconcile_plaud(rid)
    db = PlaudDB(writer.state)
    assert db.markers(rid)[HEX].title_sha256 == title_sha256("Week 4")
    assert not is_private(writer.archive.load(rid))
    writer.reindex()  # accepts the edit, then reconciles
    assert is_private(writer.archive.load(rid))
    assert db.markers(rid)[HEX].title_sha256 == title_sha256("Private: synthetic")


def test_every_title_since_the_marked_one_is_checked():
    # why: §13.1, "on new snapshots' titles". A reconcile that stopped (StaleFile) after "Private:
    # x" landed leaves the marker at "Week 4"; once "Week 5" arrives too, both new titles are due,
    # never only the newest.
    titles = {HEX: ["Week 4", "Private: x", "Week 5"]}
    seen = {HEX: Marker(HEX, title_sha256("Week 4"), 1)}
    verdict = judge(titles, seen, V1, (), private_now=False)
    assert (verdict.add_private, verdict.why, verdict.titles_checked) == (True, "title changed", 2)
    lost = {HEX: Marker(HEX, title_sha256("Week 3"), 1)}  # its snapshot isn't here: all are due
    assert judge(titles, lost, V1, (), private_now=False).titles_checked == 3
    back = {HEX: ["Interview: Ada", "Week 4", "Interview: Ada"]}  # A, B, then A again
    at_b = {HEX: Marker(HEX, title_sha256("Week 4"), 1)}
    assert judge(back, at_b, V1, (), private_now=False).titles_checked == 1


def test_the_marker_moves_last_so_a_keep_out_state_db_refuses_is_due_again(
    writer, make_incoming, plaud_env, monkeypatch
):
    # why: §13.2, §12.2's "Too late". The note and the late keep-out are recorded before the marker
    # moves: if state.db refuses the keep-out, the title stays due, and the next reconcile records
    # it, so the recording is never fetched again and Dan is told.
    source = snapshot(plaud_env, "NoArchive: coffee", 0)
    rid = writer.add(make_incoming(b"rec-1", title="Coffee", sources=(source,))).recording.id

    def full(self, plaud_id, **fields):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(PlaudDB, "set_listing", full)
    with pytest.raises(sqlite3.OperationalError):
        writer.reconcile_plaud(rid)
    db = PlaudDB(writer.state)
    assert is_private(writer.archive.load(rid))  # the tag landed first (§13.2)
    assert db.markers(rid) == {}  # the title is still due
    monkeypatch.undo()
    assert writer.reconcile_plaud(rid).kept_out_word == "noarchive"
    assert db.listing(HEX).kept_out_word == "noarchive"
    assert [(e.kind, e.recording_id) for e in db.open_flags()] == [("kept-out-late", rid)]
    assert db.markers(rid)[HEX].title_sha256 == title_sha256("NoArchive: coffee")
    assert retagged(writer) == ["kept out by title"]  # one note: the second found it private


def test_a_new_pattern_is_checked_against_that_pattern_only(writer, make_incoming, plaud_env):
    # why: §13.1 case 5, Dan's decision 3. A tag Dan removed stays removed for the patterns it
    # passed.
    first = web_writer(writer, auto_private=(r"^\s*private",))
    removed, _ = add(first, make_incoming, plaud_env, "Private chat", content=b"one")
    remove_private(first, removed)
    interview, _ = add(first, make_incoming, plaud_env, "Interview: Ada", content=b"two")
    assert not is_private(first.archive.load(interview))
    second = web_writer(writer, auto_private=(r"^\s*private", r"interview:"))
    reports = {r.recording_id: r for r in second.reindex().reconciled}
    assert reports[interview].private_added == "patterns changed"
    assert reports[removed].private_added is None
    assert is_private(second.archive.load(interview))
    assert not is_private(second.archive.load(removed))
    assert {m.patterns_version for m in PlaudDB(writer.state).markers(interview).values()} == {2}


def test_a_removed_pattern_rechecks_nothing(writer, make_incoming, plaud_env):
    rid, _ = add(writer, make_incoming, plaud_env, "Interview: Ada")
    remove_private(writer, rid)
    narrower = web_writer(writer, auto_private=(r"^\s*private",))
    assert narrower.reindex().reconciled[0].private_added is None
    restored = web_writer(writer)  # the pattern back again: the title passed it at version 1
    assert restored.reindex().reconciled[0].private_added is None
    assert not is_private(writer.archive.load(rid))
    assert PlaudDB(writer.state).markers(rid)[HEX].patterns_version == 1


def test_a_recording_the_marker_never_saw_is_checked_over_every_title_and_id(plaud_env):
    # why: §13.1 case 4: 2a's imports, a hash merge under a new ID, a lost state.db. Every title of
    # every snapshot, under every Plaud ID, erring private.
    titles = {HEX: ["10-06 Interview: Ada", "Week 4"], OTHER: ["Week 5"]}
    verdict = judge(titles, {}, V1, ("noarchive",), private_now=False)
    assert (verdict.add_private, verdict.why, verdict.titles_checked) == (True, "first check", 3)
    assert {m.canonical_plaud_id: m.title_sha256 for m in verdict.markers} == {
        HEX: title_sha256("Week 4"),
        OTHER: title_sha256("Week 5"),
    }
    # One ID unseen is enough: the seen one's old titles are checked again too.
    seen = {HEX: Marker(HEX, title_sha256("Week 4"), 1)}
    assert judge(titles, seen, V1, (), private_now=False).titles_checked == 3


def test_a_to_b_to_a_rechecks_a(writer, make_incoming, plaud_env):
    # why: §13.2. The marker holds only the newest title, so A after B is due again: erring private.
    rid, _ = add(writer, make_incoming, plaud_env, "Interview: Ada")
    remove_private(writer, rid)
    add(writer, make_incoming, plaud_env, "Week 4", day=1)
    assert not is_private(writer.archive.load(rid))
    _, report = add(writer, make_incoming, plaud_env, "Interview: Ada", day=2)
    assert report.private_added == "title changed"


def test_a_keep_out_title_found_by_reconcile_is_tagged_and_flagged_once(
    writer, make_incoming, plaud_env
):
    # why: §13.1 case 3, §12.2's "Too late", decision 14: any Writer that reconciles finds it.
    rid, report = add(writer, make_incoming, plaud_env, "NoArchive: coffee")
    assert report.kept_out_word == "noarchive" and report.private_added == "kept out by title"
    assert is_private(writer.archive.load(rid))
    db = PlaudDB(writer.state)
    assert [(e.kind, e.recording_id) for e in db.open_flags()] == [("kept-out-late", rid)]
    assert db.listing(HEX).kept_out_word == "noarchive"
    assert writer.reconcile_plaud(rid).kept_out_word is None  # not due again
    web_writer(writer).reindex()
    assert len(db.open_flags()) == 1


def test_an_enforced_older_word_still_keeps_out(writer, make_incoming, plaud_env):
    # why: §12.2. Changing the word releases nothing: every word the worker enforced still counts.
    from recordings.plaud.checks import K_KEEP_OUT_WORDS

    PlaudDB(writer.state).put(K_KEEP_OUT_WORDS, ["noarchive", "skipme"], now=datetime.now(UTC))
    renamed = web_writer(writer, keep_out_word="hideme")
    assert renamed.keep_out_words() == ("hideme", "noarchive", "skipme")
    _, report = add(renamed, make_incoming, plaud_env, "skipme: a chat")
    assert report.kept_out_word == "skipme"
    assert web_writer(writer, keep_out_word="bad word!").keep_out_words() == ("noarchive", "skipme")


def test_the_marker_hashes_the_title_exactly_as_given(writer, make_incoming, plaud_env):
    rid, _ = add(writer, make_incoming, plaud_env, "Week 4")
    add(writer, make_incoming, plaud_env, "week 4", day=1)  # a change of case is due
    assert PlaudDB(writer.state).markers(rid)[HEX].title_sha256 == title_sha256("week 4")
    assert title_sha256(" Week 4") != title_sha256("Week 4")
    titles = {HEX: [" Interview: Ada"]}
    seen = {HEX: Marker(HEX, title_sha256("Interview: Ada"), 1)}
    assert judge(titles, seen, V1, (), private_now=False).why == "title changed"


def test_title_seen_no_longer_decides_privacy(writer, make_incoming, plaud_env):
    # why: §13.2 retires title_seen for privacy. A recording 2a left with title_seen at its title
    # and no marker is checked (case 4); one whose marker matches isn't, whatever title_seen says.
    from recordings.writer import SetPlaudFields

    rid, _ = add(writer, make_incoming, plaud_env, "Interview: Ada")
    remove_private(writer, rid)
    writer.mutate(rid, SetPlaudFields(title_seen="Something else"))
    assert writer.reconcile_plaud(rid).private_added is None  # the marker says: judged
    with writer.state.db() as conn:
        conn.execute("DELETE FROM plaud_private_checked")
    writer.mutate(rid, SetPlaudFields(title_seen="Interview: Ada"))
    assert writer.reconcile_plaud(rid).private_added == "first check"


def test_a_marker_is_kept_only_for_recordings_in_the_archive_and_holds_no_title(
    writer, make_incoming, plaud_env, tmp_path
):
    rid, _ = add(writer, make_incoming, plaud_env, "Synthetic 042 title")
    kept, _ = add(writer, make_incoming, plaud_env, "Week 4", content=b"rec-2", fid=OTHER)
    with writer.state.db() as conn:
        rows = conn.execute("SELECT * FROM plaud_private_checked").fetchall()
    assert len(rows) == 2 and "Synthetic 042" not in json.dumps(rows)
    shutil.move(writer.archive.path_for(rid), tmp_path / "taken-out")  # §12.4, by hand
    writer.reindex(allow_missing=True)
    assert PlaudDB(writer.state).markers(rid) == {}
    assert set(PlaudDB(writer.state).markers(kept)) == {OTHER}


def test_pattern_versions_are_recorded_when_the_set_changes(writer):
    db = PlaudDB(writer.state)
    now = datetime(2026, 10, 23, tzinfo=UTC)
    assert current_patterns(db, ("a", "b"), now=now).version == 1
    assert current_patterns(db, ("b", "a"), now=now).version == 1  # a reordering adds nothing
    later = current_patterns(db, ("a", "b", "c"), now=now)
    assert (later.version, later.added_since(1)) == (2, ("c",))
    assert later.added_since(99) == ("a", "b", "c")  # an unknown version: everything


def test_why_phrases_are_decision_15s():
    # Phase 1's four; "the sync's record was rebuilt" comes with Phase 2's rebuild record.
    assert why_phrase("title changed") == "title changed"
    assert why_phrase("first check") == "first check"
    assert why_phrase("new pattern") == "patterns changed"
    assert why_phrase("kept out late") == "kept out by title"


def test_privacy_imports_no_sqlite3():
    # why: the views read why_phrase, and the Pages demo (Pyodide) has no sqlite3.
    import subprocess
    import sys

    code = "import sys, recordings.plaud.privacy; print('sqlite3' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
