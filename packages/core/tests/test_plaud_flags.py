"""Late consent listings and the privacy flags (stage 2b spec §12.1, §12.2, P15). Every archive
and Plaud ID here is synthetic."""

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime

import pytest
from recordings.archive import RawSource
from recordings.models import is_private
from recordings.plaud import flags as flags_module
from recordings.plaud.checks import PlaudDB
from recordings.plaud.flags import (
    CONSENT_LATE,
    KEPT_HERE,
    KEPT_OUT_LATE,
    NOTHING_TO_KEEP,
    PRIVATE_UNWRITTEN,
    PRIVATE_UNWRITTEN_IO,
    SENTENCES,
    apply_late_consent,
    flag,
    flag_sentence,
    keep_here,
    missed_by_2a,
    record_late_keep_out,
)
from recordings.plaud.keepout import parse_consent

NOW = datetime(2026, 10, 23, 14, 20, tzinfo=UTC)
T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
EARLIER = datetime.fromisoformat("2026-10-05T09:00:00-07:00")  # sorts before conftest's LOCAL
TITLE = "Synthetic 042: a title no flag may carry"


def pid(n: int) -> str:
    return f"{n:032x}"


def plaud_rec(writer, make_incoming, n: int, *, ref: str | None = None, **over):
    """A recording here, known to Plaud by synthetic ID n (`of_` form unless `ref` says)."""
    source = RawSource(kind="plaud", ref=ref or f"of_{pid(n)}", added_at=T0, payload=b'{"id": 1}')
    return writer.add(make_incoming(title=TITLE, sources=(source,), **over)).recording


def listed(*ids: str) -> str:
    """A consent list in Dan's form: a comment, then one exclusion per ID."""
    return "# consent list (synthetic)\n" + "".join(f"- `{i}` — asked not to keep\n" for i in ids)


def open_flags(db, rid=None):
    return [e for e in db.open_flags() if rid is None or e.recording_id == rid]


def test_a_late_consent_listing_is_tagged_private_and_flagged_without_a_plaud_call(
    writer, make_incoming, plaud_server
):
    # why: §12.1. Applied from the list and the index alone, whatever the sign-in or the gate:
    # nothing here holds a token, and the gate has never opened.
    rec = plaud_rec(writer, make_incoming, 1)
    other = plaud_rec(writer, make_incoming, 2)
    db = PlaudDB(writer.state)
    report = apply_late_consent(writer, db, parse_consent(listed(pid(1))), now=NOW)
    assert report.tagged == (rec.id,) and report.flagged == (rec.id,)
    assert report.unwritten == report.busy == () and report.index_unavailable is False
    after = writer.archive.load(rec.id)
    assert is_private(after) and ("private", "auto") in {(t.tag, t.by) for t in after.tags}
    assert not is_private(writer.archive.load(other.id))  # every other recording keeps flowing
    (event,) = open_flags(db)
    assert (event.kind, event.recording_id, event.canonical_plaud_id) == (
        CONSENT_LATE,
        rec.id,
        pid(1),
    )
    assert plaud_server.requests == []
    with writer.state.db() as conn:
        events = conn.execute("SELECT COUNT(*) FROM plaud_events").fetchone()[0]
    again = apply_late_consent(writer, db, parse_consent(listed(pid(1))), now=NOW)
    assert again.tagged == again.flagged == ()  # every tick, nothing twice
    assert len(open_flags(db)) == 1
    # Task 13 runs it at every tick: a tick where nothing changed writes nothing.
    assert writer.archive.load(rec.id).rev == after.rev
    with writer.state.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM plaud_events").fetchone()[0] == events


def test_an_upper_case_or_bare_form_finds_the_same_recording(writer, make_incoming):
    # why: §12.1, ID forms. The list and the archive may hold different forms of one ID.
    rec = plaud_rec(writer, make_incoming, 0xABC3, ref=pid(0xABC3))  # bare in the archive
    db = PlaudDB(writer.state)
    consent = parse_consent(listed(f"OF_{pid(0xABC3).upper()}"))
    report = apply_late_consent(writer, db, consent, now=NOW)
    assert report.tagged == (rec.id,)


def test_an_already_flagged_or_gone_recording_gets_no_second_flag(writer, make_incoming):
    # why: P15, C5.13. One flag per recording: an open late keep-out changes kind; a recording gone
    # from Plaud has no privacy flag of its own, so it gets this one, once.
    kept_out = plaud_rec(writer, make_incoming, 4)
    gone = plaud_rec(writer, make_incoming, 5)
    db = PlaudDB(writer.state)
    assert record_late_keep_out(db, kept_out.id, [f"of_{pid(4)}"], "noarchive", now=NOW)
    db.set_check(gone.id, gone_since=NOW)
    consent = parse_consent(listed(pid(4), pid(5)))
    report = apply_late_consent(writer, db, consent, now=NOW)
    assert sorted(report.flagged) == sorted([kept_out.id, gone.id])
    assert {(e.recording_id, e.kind) for e in open_flags(db)} == {
        (kept_out.id, CONSENT_LATE),
        (gone.id, CONSENT_LATE),
    }
    assert apply_late_consent(writer, db, consent, now=NOW).flagged == ()
    assert len(open_flags(db)) == 2
    # A consent-late flag is never turned back by a late keep-out.
    assert not record_late_keep_out(db, gone.id, [pid(5)], "noarchive", now=NOW)
    assert [e.kind for e in open_flags(db, gone.id)] == [CONSENT_LATE]


def test_one_flag_per_recording_holds_against_a_racing_writer(writer, make_incoming, monkeypatch):
    # why: P15 across processes. Another process's flag landing between the read and the insert
    # meets the partial unique index; this one then sees it, and adds none.
    rec = plaud_rec(writer, make_incoming, 6)
    db = PlaudDB(writer.state)
    real = PlaudDB.open_flags
    calls = []

    def first_sees_nothing(self, kinds=("kept-out-late", "consent-late")):
        calls.append(1)
        if len(calls) == 1:
            self.add_event(KEPT_OUT_LATE, now=NOW, recording_id=rec.id, open_flag=True)
            return []
        return real(self, kinds)

    monkeypatch.setattr(PlaudDB, "open_flags", first_sees_nothing)
    assert flag(db, CONSENT_LATE, rec.id, plaud_id=pid(6), now=NOW) is True  # changed its kind
    monkeypatch.undo()
    assert [e.kind for e in open_flags(db, rec.id)] == [CONSENT_LATE]


def test_removing_the_line_clears_the_flag_and_keeps_private(writer, make_incoming):
    # why: §12.1. Dan keeps it: the flag clears at the next tick, `private` stays.
    rec = plaud_rec(writer, make_incoming, 7)
    db = PlaudDB(writer.state)
    apply_late_consent(writer, db, parse_consent(listed(pid(7), pid(99))), now=NOW)
    report = apply_late_consent(writer, db, parse_consent(listed(pid(99))), now=NOW)
    assert report.cleared == (rec.id,)
    assert open_flags(db) == []
    assert is_private(writer.archive.load(rec.id))


def test_a_typo_applies_the_exclusions_that_read_and_clears_nothing(writer, make_incoming):
    # why: §12.1. During a consent stop the exclusions that do read still apply, and no flag is
    # cleared: a line that became a typo must never read as "exclude fewer".
    flagged = plaud_rec(writer, make_incoming, 8)
    newly = plaud_rec(writer, make_incoming, 9)
    db = PlaudDB(writer.state)
    apply_late_consent(writer, db, parse_consent(listed(pid(8))), now=NOW)
    typo = listed(pid(9)) + f"- `{pid(8)[:31]}` — asked not to keep\n"  # line 8's ID lost a digit
    consent = parse_consent(typo)
    assert not consent.readable
    report = apply_late_consent(writer, db, consent, now=NOW)
    assert report.tagged == (newly.id,) and report.cleared == ()
    assert {e.recording_id for e in open_flags(db)} == {flagged.id, newly.id}


def test_an_index_that_cant_answer_is_retried_and_shown(writer, make_incoming, monkeypatch):
    # why: §12.1. A strict lookup that can't answer is never "nothing here": nothing is tagged,
    # nothing is cleared, it says so, and the next tick does it.
    rec = plaud_rec(writer, make_incoming, 10)
    db = PlaudDB(writer.state)
    apply_late_consent(writer, db, parse_consent(listed(pid(10))), now=NOW)

    def busy(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    newer = plaud_rec(writer, make_incoming, 11)
    monkeypatch.setattr(writer.index, "find_by_source", busy)
    report = apply_late_consent(writer, db, parse_consent(listed(pid(11))), now=NOW)
    assert report.index_unavailable is True
    assert report.tagged == report.cleared == ()
    assert [e.recording_id for e in open_flags(db)] == [rec.id]  # pid 10's line went: not cleared
    monkeypatch.undo()
    report = apply_late_consent(writer, db, parse_consent(listed(pid(11))), now=NOW)
    assert report.tagged == (newer.id,) and report.cleared == (rec.id,)


def test_a_missing_index_is_unavailable_not_empty(writer, make_incoming):
    plaud_rec(writer, make_incoming, 12)
    writer.index.path.unlink()
    report = apply_late_consent(
        writer, PlaudDB(writer.state), parse_consent(listed(pid(12))), now=NOW
    )
    assert report.index_unavailable is True and report.tagged == ()


def test_a_recording_whose_index_update_is_pending_is_still_found(writer, make_incoming):
    # why: an update the index couldn't take leaves a pending mark, and an index that says "none".
    rec = plaud_rec(writer, make_incoming, 13)
    with writer.locks.hold("index"), writer.index._db() as conn:
        conn.execute("DELETE FROM source_refs WHERE recording_id = ?", (rec.id,))
    writer.state.mark_index_pending(rec.id)
    report = apply_late_consent(
        writer, PlaudDB(writer.state), parse_consent(listed(pid(13))), now=NOW
    )
    assert report.tagged == (rec.id,)


def test_a_file_edited_outside_the_app_is_reported_unwritten(writer, make_incoming):
    # why: §13.2. The `private` it owes can't land: reported, so the worker opens its blocker.
    rec = plaud_rec(writer, make_incoming, 14)
    path = writer.archive.path_for(rec.id) / "recording.json"
    path.write_text(path.read_text(encoding="utf-8").replace("Synthetic 042", "Edited"), "utf-8")
    db = PlaudDB(writer.state)
    report = apply_late_consent(writer, db, parse_consent(listed(pid(14))), now=NOW)
    assert report.unwritten == (rec.id,) and report.tagged == ()
    assert report.causes == {rec.id: "StaleFile"}  # its flag keeps §13.2's sentence
    assert [e.recording_id for e in open_flags(db)] == [rec.id]  # flagged all the same


def test_a_busy_recording_is_retried_at_the_next_tick(writer, make_incoming):
    from recordings.locks import Locks

    rec = plaud_rec(writer, make_incoming, 15)
    writer.locks.timeout = 0.1
    db = PlaudDB(writer.state)
    with Locks(writer.state.locks_dir, timeout=0).hold(rec.id):
        report = apply_late_consent(writer, db, parse_consent(listed(pid(15))), now=NOW)
    assert report.busy == (rec.id,) and open_flags(db) == []
    assert apply_late_consent(writer, db, parse_consent(listed(pid(15))), now=NOW).tagged == (
        rec.id,
    )


def locked_out(writer, rec, monkeypatch) -> None:
    """Its folder can't be entered (the wrong owner): Python reads that as "no file there"."""
    writer.archive.path_for(rec.id).chmod(0o000)


def undecodable(writer, rec, monkeypatch) -> None:
    """Its recording.json isn't UTF-8 (a half-copied file, another tool's write)."""
    (writer.archive.path_for(rec.id) / "recording.json").write_bytes(b'{"title": "\xff\xfe"}')


def busy_state_db(writer, rec, monkeypatch) -> None:
    """state.db can't take its revision now (another process's long write, a full disk)."""
    real = writer.mutate

    def mutate(recording_id, op):
        if recording_id == rec.id:
            raise sqlite3.OperationalError("database is locked")
        return real(recording_id, op)

    monkeypatch.setattr(writer, "mutate", mutate)


ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.mark.parametrize(
    ("fault", "cause"),
    [
        pytest.param(
            locked_out,
            "PermissionError",
            id="a folder it can't enter",
            marks=pytest.mark.skipif(ROOT, reason="root enters any folder"),
        ),
        pytest.param(undecodable, "InvalidFile", id="a file that doesn't decode"),
        pytest.param(busy_state_db, "OperationalError", id="a busy state.db"),
    ],
)
def test_one_recording_that_cant_be_written_stops_none_after_it(
    writer, make_incoming, monkeypatch, fault, cause
):
    # why: §12.1 "Every other recording keeps flowing", §16. A folder with the wrong owner, a full
    # disk, a busy state.db or a file that doesn't decode is that recording's alone: it is reported
    # unwritten, with its class for its flag's sentence, so the worker opens its blocker, and every
    # recording sorted after it is tagged.
    first = plaud_rec(writer, make_incoming, 40, recorded_at=EARLIER)
    second = plaud_rec(writer, make_incoming, 41)
    assert first.id < second.id  # the bad one is met first
    db = PlaudDB(writer.state)
    folder = writer.archive.path_for(first.id)
    mode = folder.stat().st_mode & 0o777
    fault(writer, first, monkeypatch)
    try:
        report = apply_late_consent(writer, db, parse_consent(listed(pid(40), pid(41))), now=NOW)
    finally:
        folder.chmod(mode)
    assert report.unwritten == (first.id,) and report.causes == {first.id: cause}
    assert report.tagged == (second.id,) and is_private(writer.archive.load(second.id))
    assert {e.recording_id for e in open_flags(db)} == {first.id, second.id}  # flagged all the same


def test_an_unwritten_flags_sentence_follows_its_cause():
    # why: §13.2, §16. `recordings reindex` mends an outside edit; it can't mend a full disk or a
    # folder's owner. So any other cause names its class and points at the disk, never at reindex.
    rid = "20261006T140003-0700_aaaaaaaa"
    edited = SENTENCES[PRIVATE_UNWRITTEN]
    assert flag_sentence(PRIVATE_UNWRITTEN, rid, "StaleFile") == edited
    assert flag_sentence(PRIVATE_UNWRITTEN, rid, "InvalidFile") == edited
    assert flag_sentence(PRIVATE_UNWRITTEN, rid) == edited  # a flag with no cause recorded
    assert flag_sentence(PRIVATE_UNWRITTEN, rid, "PermissionError") == (
        f"Recording {rid} should be private, but its file couldn't be written (PermissionError): "
        "check the archive disk and its permissions."
    )
    io = flag_sentence(PRIVATE_UNWRITTEN, rid, "OSError")
    assert io == PRIVATE_UNWRITTEN_IO.format(id=rid, cause="OSError") and "reindex" not in io
    assert flag_sentence(CONSENT_LATE, rid, "OSError") == SENTENCES[CONSENT_LATE]
    # Only a class name is ever shown: anything else in the detail reads as "unknown".
    odd = flag_sentence(PRIVATE_UNWRITTEN, rid, "Synthetic 042: a title")
    assert "Synthetic" not in odd and "(unknown)" in odd


def test_a_flag_state_db_refuses_is_added_at_the_next_tick(writer, make_incoming, monkeypatch):
    # why: §12.1. The tag has landed, so nothing is owed: state.db refusing the flag stops neither
    # the recordings after it nor the flag, which the next tick adds (`flag` reads the open flags
    # afresh each time).
    first = plaud_rec(writer, make_incoming, 42, recorded_at=EARLIER)
    second = plaud_rec(writer, make_incoming, 43)
    db = PlaudDB(writer.state)
    real = flags_module.flag

    def refused(db, kind, recording_id, **kwargs):
        if recording_id == first.id:
            raise sqlite3.OperationalError("database or disk is full")
        return real(db, kind, recording_id, **kwargs)

    monkeypatch.setattr(flags_module, "flag", refused)
    consent = parse_consent(listed(pid(42), pid(43)))
    report = apply_late_consent(writer, db, consent, now=NOW)
    assert report.tagged == (first.id, second.id) and report.flagged == (second.id,)
    assert report.unwritten == () and report.causes == {}
    monkeypatch.undo()
    assert apply_late_consent(writer, db, consent, now=NOW).flagged == (first.id,)


def tree_hash(root) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_keep_here_clears_the_flag_and_changes_no_file(writer, make_incoming):
    # why: C5.04, §12.2. Keep it here changes no data: `private` stays, nothing more is fetched.
    rec = plaud_rec(writer, make_incoming, 16)
    db = PlaudDB(writer.state)
    record_late_keep_out(db, rec.id, [f"of_{pid(16)}"], "noarchive", now=NOW)
    before = tree_hash(writer.root)
    assert keep_here(db, rec.id, now=NOW) == KEPT_HERE
    assert tree_hash(writer.root) == before
    assert open_flags(db) == []
    assert db.check_row(rec.id).kept_here_at is not None
    assert [e.kind for e in db.events_since(T0, ["kept-here"])] == ["kept-here"]
    assert db.listing(pid(16)).kept_out_at == NOW  # still kept out: nothing more is fetched


def test_keep_here_refuses_without_an_open_late_keep_out(writer, make_incoming):
    rec = plaud_rec(writer, make_incoming, 17)
    db = PlaudDB(writer.state)
    assert keep_here(db, rec.id, now=NOW) == NOTHING_TO_KEEP
    apply_late_consent(writer, db, parse_consent(listed(pid(17))), now=NOW)
    assert keep_here(db, rec.id, now=NOW) == NOTHING_TO_KEEP  # a consent-late flag isn't one
    assert [e.kind for e in open_flags(db)] == [CONSENT_LATE]


def test_a_later_keep_out_title_after_keep_here_never_flags_again(writer, make_incoming):
    rec = plaud_rec(writer, make_incoming, 18)
    db = PlaudDB(writer.state)
    assert record_late_keep_out(db, rec.id, [pid(18)], "noarchive", now=NOW)
    keep_here(db, rec.id, now=NOW)
    assert record_late_keep_out(db, rec.id, [pid(18)], "noarchive", now=NOW) is False
    assert open_flags(db) == []


def test_a_late_keep_out_dates_a_new_listing_row_by_the_keep_out(writer, make_incoming):
    # why: decision 36. Phase 1 lists nothing, so a 2a recording's ID has no plaud_listing row: the
    # keep-out makes it (Task 6's set_listing) with the ID as the archive holds it, dated by the
    # keep-out, never as if a listing had shown it.
    upper = f"OF_{pid(24).upper()}"
    rec = plaud_rec(writer, make_incoming, 24, ref=upper)
    db = PlaudDB(writer.state)
    assert db.listing(pid(24)) is None
    record_late_keep_out(db, rec.id, [upper], "noarchive", now=T0)
    row = db.listing(pid(24))
    assert (row.listed_id, row.first_seen, row.last_seen) == (upper, T0, T0)
    assert (row.kept_out_at, row.kept_out_word, row.absent_since) == (T0, "noarchive", None)


def test_a_kept_out_recording_whose_consent_line_goes_is_flagged_kept_out_again(
    writer, make_incoming
):
    # why: P15 both ways. Its title kept it out too, so the line going leaves that flag.
    rec = plaud_rec(writer, make_incoming, 19)
    db = PlaudDB(writer.state)
    record_late_keep_out(db, rec.id, [pid(19)], "noarchive", now=NOW)
    apply_late_consent(writer, db, parse_consent(listed(pid(19), pid(99))), now=NOW)
    apply_late_consent(writer, db, parse_consent(listed(pid(99))), now=NOW)
    assert [e.kind for e in open_flags(db, rec.id)] == [KEPT_OUT_LATE]


def test_missed_by_2a_names_the_of_and_upper_case_exclusions():
    text = listed(pid(30), f"of_{pid(31)}", pid(0xBC).upper()) + f"{pid(33)}\n"
    assert missed_by_2a(text, parse_consent(text)) == {pid(31), pid(0xBC)}


def test_no_flag_or_event_carries_a_title(writer, make_incoming):
    rec = plaud_rec(writer, make_incoming, 34)
    other = plaud_rec(writer, make_incoming, 35)
    db = PlaudDB(writer.state)
    apply_late_consent(writer, db, parse_consent(listed(pid(34))), now=NOW)
    record_late_keep_out(db, other.id, [pid(35)], "noarchive", now=NOW)
    keep_here(db, other.id, now=NOW)
    with writer.state.db() as conn:
        rows = [
            *conn.execute("SELECT * FROM plaud_events"),
            *conn.execute("SELECT * FROM plaud_listing"),
            *conn.execute("SELECT * FROM plaud_checks"),
        ]
    assert rows and "Synthetic 042" not in json.dumps(rows, default=str)
    assert rec.title == TITLE
