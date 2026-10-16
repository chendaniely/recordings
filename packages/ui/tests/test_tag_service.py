"""The tag service (tagging spec §5), driven directly with an injected send, against both
stores (the `store` fixture, in conftest.py). The races are spec §9's: each puts another window's
change between this request's check and its write."""

import asyncio
import errno
import logging
import secrets
import threading

import pytest
from pydantic import ValidationError
from recordings.archive import Archive
from recordings.locks import Locks
from recordings.models import TagRef
from recordings.tagops import (
    CASE_VARIANT,
    LOOKS_PRIVATE,
    NOTE_TYPE,
    AddTags,
    Recorded,
    RemoveTags,
    WouldExpose,
)
from recordings_ui.tagging import (
    ALREADY_UNDONE,
    BAD_ID,
    BUSY,
    GONE,
    INVALID,
    NO_RECORDINGS,
    NO_TAGS,
    NOT_CONFIRMED,
    STALE,
    STOP_BUSY,
    STOP_FULL,
    STOP_IO,
    STOP_READ_ONLY,
    STOP_SHUTDOWN,
    STOP_UNEXPECTED,
    TOO_MANY_IDS,
    TOO_MANY_TAGS,
    UNDO_GONE,
    UNREADABLE,
    ArchiveStore,
    Classified,
    TagService,
    TagSession,
    _ids_ok,
    _request_id,
    _texts,
    classify_common,
)
from recordings_ui.writing import STOPPED

GOOD_ID = "20200101T000000+0000_0000beef"  # the right shape; no such recording


@pytest.fixture
def service(store) -> TagService:
    return TagService(store, progress_every=0)  # progress between every two recordings


class Replies(list):
    """Records each reply, as shinyreact's send_message would deliver it."""

    async def __call__(self, reply: dict) -> None:
        self.append(reply)

    @property
    def final(self) -> dict:
        return [r for r in self if r["status"] != "progress"][-1]


def session_of(service: TagService) -> TagSession:
    session = service.session()
    asyncio.run(session.library())  # a helper can't take the `run` fixture
    return session


def ask(session: TagSession, raw: object) -> Replies:
    replies = Replies()
    asyncio.run(session.run(session.check(raw), replies))
    return replies


def req(action, ids=(), tags=(), *, undo=None, confirm=(), request_id=None) -> dict:
    return {
        "request_id": request_id or secrets.token_hex(8),
        "action": action,
        "ids": list(ids),
        "tags": list(tags),
        "undo": undo,
        "confirm_ids": list(confirm),
    }


def tags_of(store, rid: str) -> list[str]:
    return [t.tag for t in store.read(rid).tags]


def elsewhere(op) -> Recorded:
    return Recorded(op, "another-window")


def before_the_first_write(store, monkeypatch, other) -> None:
    """Run `other(real_apply)` once, just before this request's first write: another window's
    change landing between this request's check and its write."""
    real = store.apply
    pending = [other]

    def apply(rid, op):
        while pending:
            pending.pop()(real)
        return real(rid, op)

    monkeypatch.setattr(store, "apply", apply)


# ---- applying, and the view ------------------------------------------------------------------
def test_the_view_says_whether_tagging_works_and_saves(service, run):
    view = run(service.session().library())
    assert view["can_tag"] is True and view["cannot_tag_reason"] is None
    assert view["saves"] is isinstance(service.store, ArchiveStore)


def test_the_view_never_waits_behind_a_write(tag_archive, run):
    # why: spec §5, "reads never queue behind writes". Load and Refresh read the view on the
    # event loop; another window's write, still running on the writer thread, holds up neither.
    store = ArchiveStore(Archive(tag_archive.root), tag_archive.open_writer)
    service = TagService(store)
    gate = threading.Event()
    try:
        assert store.wait_open(30) == (True, None)
        store._thread.executor.submit(gate.wait, 30)  # a write that hasn't finished
        for refresh in (False, True):
            view = run(asyncio.wait_for(service.session().library(refresh=refresh), 5))
            assert view["can_tag"] is True and view["counts"]["all"] == 6
    finally:
        gate.set()
        store.close()


def test_adding_applies_and_replies_with_the_changed_rows_and_counts(service, tag_archive):
    ids = tag_archive.ids
    session = session_of(service)
    reply = ask(session, req("add", [ids["untagged"], ids["plain"]], ["work"])).final
    assert reply["status"] == "done" and reply["changed"] == 2 and reply["unchanged"] == 0
    assert {r["id"] for r in reply["rows"]} == {ids["untagged"], ids["plain"]}
    assert reply["counts"] == {"all": 6, "untagged": 0}
    assert {"tag": "work", "count": 2, "private": False} in reply["tags"]
    assert reply["undo"] and reply["failures"] == [] and reply["message"] is None
    assert tags_of(service.store, ids["plain"]) == ["talks", "work"]


def test_a_tag_already_there_counts_as_unchanged(service, tag_archive):
    session = session_of(service)
    reply = ask(session, req("add", [tag_archive.ids["plain"]], ["talks"])).final
    assert reply["status"] == "done" and reply["changed"] == 0 and reply["unchanged"] == 1
    assert reply["undo"] is None and reply["rows"] == []


# ---- checks, before anything is applied ------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (None, UNREADABLE),
        ("add", UNREADABLE),
        ({"request_id": "short", "action": "add", "ids": [GOOD_ID], "tags": ["x"]}, UNREADABLE),
        (
            {"request_id": "req0001abcd", "action": "clear", "ids": [GOOD_ID], "tags": ["x"]},
            UNREADABLE,
        ),
        ({"request_id": "req0001abcd", "action": "add", "ids": GOOD_ID, "tags": ["x"]}, UNREADABLE),
        ({"request_id": "req0001abcd", "action": "add", "ids": [GOOD_ID], "tags": [1]}, UNREADABLE),
        ({"request_id": "req0001abcd", "action": "add", "ids": [], "tags": ["x"]}, NO_RECORDINGS),
        ({"request_id": "req0001abcd", "action": "add", "ids": ["../etc"], "tags": ["x"]}, BAD_ID),
        # the pattern, but month 13 and hour 25: no folder can hold it, so the request is refused
        # whole; a well-formed ID that isn't found is a per-recording "gone" (spec §5)
        (
            {
                "request_id": "req0001abcd",
                "action": "add",
                "ids": ["20261399T256199+0000_deadbeef"],
                "tags": ["x"],
            },
            BAD_ID,
        ),
        ({"request_id": "req0001abcd", "action": "remove", "ids": [GOOD_ID], "tags": []}, NO_TAGS),
        ({"request_id": "req0001abcd", "action": "undo", "undo": ""}, UNREADABLE),
    ],
)
def test_a_bad_request_is_refused_whole(service, tag_archive, raw, message):
    session = session_of(service)
    replies = ask(session, raw)
    assert [r["status"] for r in replies] == ["failed"]
    assert replies[0]["message"] == message
    assert replies[0]["rows"] == [] and replies[0]["undo"] is None
    assert tags_of(service.store, tag_archive.ids["plain"]) == ["talks"]


def test_a_request_as_shiny_delivers_it_with_tuples_is_read(service, tag_archive):
    # why: Shiny hands a JSON array inside an input value over as a tuple (seen on the wire:
    # 'ids': ('…',)). A check that took only lists would refuse every real request.
    ids = tag_archive.ids
    session = session_of(service)
    raw = {**req("add", [ids["untagged"]], ["work"]), "ids": (ids["untagged"],), "tags": ("work",)}
    assert ask(session, {**raw, "confirm_ids": ()}).final["status"] == "done"
    assert tags_of(service.store, ids["untagged"]) == ["work"]
    gated = {**req("remove", (), ()), "ids": (ids["private"],), "tags": ("private",)}
    confirm = (ids["private"],)
    assert ask(session, {**gated, "confirm_ids": confirm}).final["status"] == "done"
    assert tags_of(service.store, ids["private"]) == []


@pytest.mark.parametrize(
    "tag", ["zebra\u200b", "Talks", "notes/zebra", "zebra/../x", "\u0440rivate"]
)
def test_a_refusal_reply_never_repeats_the_tag(service, tag_archive, tag):
    # why: the reply's message is shown, and is the kind of text that ends up in a report.
    session = session_of(service)
    final = ask(session, req("add", [tag_archive.ids["untagged"]], [tag])).final
    assert final["status"] == "failed" and final["message"]
    assert tag not in final["message"] and "zebra" not in final["message"].casefold()


def test_the_caps_refuse_a_request_whole(service, tag_archive):
    session = session_of(service)
    many = [f"20200101T000000+0000_{i:08x}" for i in range(20_001)]
    assert ask(session, req("add", many, ["work"])).final["message"] == TOO_MANY_IDS
    rid = tag_archive.ids["untagged"]
    tags = [f"t{i}" for i in range(21)]
    assert ask(session, req("add", [rid], tags)).final["message"] == TOO_MANY_TAGS
    assert tags_of(service.store, rid) == []
    # confirm_ids are IDs in the request too: the same sanity cap (spec §5)
    for action, undo in (("remove", None), ("undo", "a-token")):
        crafted = req(action, [rid], ["work"], undo=undo, confirm=many)
        assert ask(session, crafted).final["message"] == TOO_MANY_IDS


def test_new_tags_follow_the_rules_and_existing_ones_are_taken_as_they_are(service, tag_archive):
    rid = tag_archive.ids["untagged"]
    session = session_of(service)
    assert ask(session, req("add", [rid], ["\u0440rivate"])).final["message"] == LOOKS_PRIVATE
    assert ask(session, req("add", [rid], ["Talks"])).final["message"] == CASE_VARIANT
    assert ask(session, req("add", [rid], ["notes/unknown"])).final["message"] == NOTE_TYPE
    assert tags_of(service.store, rid) == []
    # "summary" is only an exclusion (on "excluded"): still a type the archive has (decision 18)
    done = ask(session, req("add", [rid], ["notes/lecture", "notes/summary", "talks"])).final
    assert done["status"] == "done"
    assert tags_of(service.store, rid) == ["notes/lecture", "notes/summary", "talks"]


@pytest.mark.parametrize(
    "lookalike",
    [
        "\u0440rivate",  # Cyrillic er: reads as private, and isn't
        "\uff50\uff52\uff49\uff56\uff41\uff54\uff45",  # fullwidth: private to the app only
        "\u0440ri\u200bvate",  # validate_new_tag names INVISIBLE first, which hides the lookalike
    ],
)
def test_a_lookalike_already_in_the_archive_is_never_spread(service, tag_archive, run, lookalike):
    # why: Task 2's review (M2). A lookalike that got in by hand, or from an old import, is in
    # the view; the existing-tag exemption must not add it to more recordings. Removing it still
    # accepts its spelling (spec §4).
    ids = tag_archive.ids
    odd = AddTags((TagRef(tag=lookalike, by="you"),))
    run(service.store.write(lambda: service.store.apply(ids["plain"], elsewhere(odd))))
    session = session_of(service)
    assert {"tag": lookalike, "by": "you"} in session.rows[ids["plain"]]["tags"]
    refused = ask(session, req("add", [ids["untagged"]], ["work", lookalike])).final
    assert refused["status"] == "failed" and refused["message"] == LOOKS_PRIVATE
    assert tags_of(service.store, ids["untagged"]) == []
    removed = ask(session, req("remove", [ids["plain"]], [lookalike], confirm=[ids["plain"]]))
    assert removed.final["status"] == "done" and tags_of(service.store, ids["plain"]) == ["talks"]


def test_an_existing_tag_that_breaks_only_other_rules_is_taken_as_it_is(service, tag_archive, run):
    # why: spec §4. Only the privacy checks apply to a tag the archive already has; a no-break
    # space would be refused in a new tag (SPACE), not in one Dan already uses.
    ids = tag_archive.ids
    odd = AddTags((TagRef(tag="my\u00a0talks", by="you"),))
    run(service.store.write(lambda: service.store.apply(ids["plain"], elsewhere(odd))))
    session = session_of(service)
    done = ask(session, req("add", [ids["untagged"]], ["my\u00a0talks"])).final
    assert done["status"] == "done" and tags_of(service.store, ids["untagged"]) == ["my\u00a0talks"]


def test_the_check_helpers_that_snippets_reuse():
    assert _texts(["a"]) and _texts(("a",)) and _texts([])
    assert not _texts("a") and not _texts(["a", 1]) and not _texts(None)
    assert _request_id({"request_id": "req0001abcd"}) == "req0001abcd"
    for bad in ("short", "x" * 65, "req0001abcd\n", "req 0001abcd", 12345678):
        assert _request_id({"request_id": bad}) is None
    assert _ids_ok((GOOD_ID,)) and _ids_ok(())
    assert not _ids_ok((GOOD_ID, "20261399T256199+0000_deadbeef"))
    assert not _ids_ok(("../etc",))


def test_a_note_type_removed_everywhere_can_still_be_added(service, tag_archive, run):
    # why: tagging plan, decision 18. After its last tag goes, the type is known only through
    # the exclusion; a new window lists it at 0, and may add it back (main spec §7.3).
    rid = tag_archive.ids["noted"]  # the only notes/lecture
    ask(session_of(service), req("remove", [rid], ["notes/lecture"]))
    later = service.session()
    assert {"note_type": "lecture", "count": 0} in run(later.library())["note_types"]
    back = ask(later, req("add", [rid], ["notes/lecture"])).final
    assert back["status"] == "done" and service.store.read(rid).excluded_note_types == []
    assert {"note_type": "lecture", "count": 1} in back["note_types"]


# ---- the confirmation ------------------------------------------------------------------------
def test_removing_a_last_private_tag_asks_first_and_applies_nothing(service, tag_archive):
    ids = tag_archive.ids
    session = session_of(service)
    reply = ask(session, req("remove", [ids["private"], ids["journal"], ids["plain"]], ["private"]))
    final = reply.final
    assert final["status"] == "confirm" and final["total"] == 3
    assert final["loses_private"] == {"visible": 0, "untagged": 1, "ids": [ids["private"]]}
    assert final["rows"] == [] and final["undo"] is None
    assert tags_of(service.store, ids["private"]) == ["private"]
    assert tags_of(service.store, ids["journal"]) == ["private/journal", "private"]


def test_the_counts_split_visible_from_untagged(service, tag_archive):
    ids = tag_archive.ids
    session = session_of(service)
    ask(session, req("add", [ids["plain"]], ["private"]))
    final = ask(session, req("remove", [ids["plain"], ids["private"]], ["private"])).final
    assert final["loses_private"]["visible"] == 1  # plain keeps "talks"
    assert final["loses_private"]["untagged"] == 1  # private has nothing left


def test_nothing_is_applied_without_confirm_ids_even_for_a_hand_crafted_request(
    service, tag_archive
):
    ids = tag_archive.ids
    session = session_of(service)
    crafted = req("remove", [ids["private"]], ["private"], confirm=[ids["plain"]])
    assert ask(session, crafted).final["status"] == "confirm"
    assert tags_of(service.store, ids["private"]) == ["private"]
    done = ask(session, req("remove", [ids["private"]], ["private"], confirm=[ids["private"]]))
    assert done.final["status"] == "done" and tags_of(service.store, ids["private"]) == []


# ---- the races (spec §9) ---------------------------------------------------------------------
def test_complementary_removals_in_two_windows_never_expose_a_recording(
    service, tag_archive, monkeypatch
):
    # why: each window's check sees another private tag left, so neither asks; the gate, under
    # the lock, refuses the second write.
    rid = tag_archive.ids["journal"]  # private/journal and private
    session = session_of(service)
    before_the_first_write(
        service.store, monkeypatch, lambda real: real(rid, elsewhere(RemoveTags(("private",))))
    )
    final = ask(session, req("remove", [rid], ["private/journal"])).final
    assert final["status"] == "done" and final["changed"] == 0
    assert final["failures"] == [{"id": rid, "reason": NOT_CONFIRMED}]
    assert tags_of(service.store, rid) == ["private/journal"]


def test_a_stale_window_still_asks_because_the_check_reads_the_files(service, tag_archive, run):
    rid = tag_archive.ids["plain"]
    session = session_of(service)  # its view: plain has only "talks"
    private = AddTags((TagRef(tag="private", by="you"),))
    run(service.store.write(lambda: service.store.apply(rid, elsewhere(private))))
    assert session.rows[rid]["tags"] == [{"tag": "talks", "by": "you"}]  # stale
    final = ask(session, req("remove", [rid], ["private"])).final
    assert final["status"] == "confirm" and final["loses_private"]["ids"] == [rid]
    assert final["loses_private"]["visible"] == 1
    assert tags_of(service.store, rid) == ["talks", "private"]


def test_a_set_that_grows_after_the_confirm_asks_again(service, tag_archive, run):
    ids = tag_archive.ids
    targets = [ids["private"], ids["journal"]]
    session = session_of(service)
    first = ask(session, req("remove", targets, ["private"])).final
    assert first["loses_private"]["ids"] == [ids["private"]]
    journal_only = RemoveTags(("private/journal",))
    run(service.store.write(lambda: service.store.apply(ids["journal"], elsewhere(journal_only))))
    again = ask(session, req("remove", targets, ["private"], confirm=[ids["private"]])).final
    assert again["status"] == "confirm"
    assert sorted(again["loses_private"]["ids"]) == sorted(targets)
    assert tags_of(service.store, ids["private"]) == ["private"]
    assert tags_of(service.store, ids["journal"]) == ["private"]


def test_an_add_that_lands_before_the_write_is_left_alone_by_undo(
    service, tag_archive, monkeypatch
):
    rid = tag_archive.ids["plain"]
    session = session_of(service)
    school = AddTags((TagRef(tag="school", by="you"),))
    before_the_first_write(service.store, monkeypatch, lambda real: real(rid, elsewhere(school)))
    added = ask(session, req("add", [rid], ["work"])).final
    assert tags_of(service.store, rid) == ["talks", "school", "work"]
    undone = ask(session, req("undo", undo=added["undo"])).final
    assert undone["status"] == "done" and tags_of(service.store, rid) == ["talks", "school"]


# ---- failures, stops and landings ------------------------------------------------------------
def test_a_broken_file_fails_alone_and_the_rest_are_applied_and_undoable(service, tag_archive):
    ids = tag_archive.ids
    session = session_of(service)
    broken = Archive(tag_archive.root).path_for(ids["noted"]) / "recording.json"
    broken.write_text("{", encoding="utf-8")  # a hand edit gone wrong (main spec §11)
    final = ask(session, req("add", [ids["untagged"], ids["noted"], ids["plain"]], ["work"])).final
    assert final["status"] == "done" and final["changed"] == 2
    assert final["failures"] == [{"id": ids["noted"], "reason": INVALID}]
    undone = ask(session, req("undo", undo=final["undo"])).final
    assert undone["changed"] == 2
    assert tags_of(service.store, ids["untagged"]) == []
    assert tags_of(service.store, ids["plain"]) == ["talks"]


def test_a_file_that_isnt_utf8_fails_alone(service, tag_archive):
    # why: Task 4's review. The Writer raises InvalidFile; the memory store lets the reader's own
    # error out (here a UnicodeDecodeError). Both must read "the file doesn't validate".
    ids = tag_archive.ids
    session = session_of(service)
    broken = Archive(tag_archive.root).path_for(ids["noted"]) / "recording.json"
    broken.write_bytes(b"\xff\xfe not UTF-8")  # a hand edit gone wrong (main spec §11)
    final = ask(session, req("add", [ids["noted"], ids["untagged"]], ["work"])).final
    assert final["failures"] == [{"id": ids["noted"], "reason": INVALID}]
    assert final["status"] == "done" and final["changed"] == 1


def _validation_error() -> ValidationError:
    try:
        TagRef(tag="a//b")
    except ValidationError as exc:
        return exc
    raise AssertionError("TagRef accepted an empty folder part")


@pytest.mark.parametrize(
    ("exc", "found"),
    [
        (WouldExpose(GOOD_ID), Classified(NOT_CONFIRMED, False)),
        (KeyError(GOOD_ID), Classified(GONE, False)),
        (FileNotFoundError(errno.ENOENT, "gone"), Classified(GONE, False)),
        (_validation_error(), Classified(INVALID, False)),  # the memory store's own
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"), Classified(INVALID, False)),
        (OSError(errno.ENOSPC, "full"), Classified(STOP_FULL, True)),
        (OSError(errno.EDQUOT, "quota"), Classified(STOP_FULL, True)),
        (OSError(errno.EROFS, "read-only"), Classified(STOP_READ_ONLY, True)),
        (OSError(errno.EIO, "i/o"), Classified(STOP_IO, True)),
        (ValueError("this effect belongs to another recording"), Classified(STOP_UNEXPECTED, True)),
        (ZeroDivisionError(), Classified(STOP_UNEXPECTED, True)),
    ],
)
def test_the_common_rules_name_each_failure(exc, found):
    # why: spec §8 and decision 14. Fixed phrases by class; anything unknown stops the batch.
    assert classify_common(exc) == found


def test_an_outside_edit_is_refused_with_its_reason(tag_archive):
    store = ArchiveStore(Archive(tag_archive.root), tag_archive.open_writer)
    service = TagService(store, progress_every=0)
    try:
        session = session_of(service)
        rid = tag_archive.ids["plain"]
        path = Archive(tag_archive.root).path_for(rid) / "recording.json"
        text = path.read_text(encoding="utf-8").replace('"Week 0"', '"Edited by hand"')
        path.write_text(text, encoding="utf-8")  # an edit outside the app (main spec §6.4)
        final = ask(session, req("add", [rid, tag_archive.ids["untagged"]], ["work"])).final
        assert final["failures"] == [{"id": rid, "reason": STALE}] and final["changed"] == 1
    finally:
        store.close()


def test_a_recording_that_is_gone_fails_alone(service, tag_archive):
    session = session_of(service)
    final = ask(session, req("add", [GOOD_ID, tag_archive.ids["untagged"]], ["work"])).final
    assert final["failures"] == [{"id": GOOD_ID, "reason": GONE}] and final["changed"] == 1


def test_an_archive_level_error_stops_the_batch_with_rows_and_undo(
    service, tag_archive, monkeypatch
):
    ids = tag_archive.ids
    real = service.store.apply

    def apply(rid, op):
        if rid == ids["plain"]:
            raise OSError(errno.ENOSPC, "No space left on device: Week 0")
        return real(rid, op)

    monkeypatch.setattr(service.store, "apply", apply)
    session = session_of(service)
    final = ask(
        session, req("add", [ids["untagged"], ids["plain"], ids["excluded"]], ["work"])
    ).final
    assert final["status"] == "failed" and final["message"] == STOP_FULL
    assert [r["id"] for r in final["rows"]] == [ids["untagged"]] and final["undo"]
    assert tags_of(service.store, ids["excluded"]) == ["talks"]  # never reached


def test_a_change_that_landed_before_an_error_counts_as_applied(service, tag_archive, monkeypatch):
    # why: spec §5. An fsync of the folder can fail after the rename; the change is on disk.
    rid = tag_archive.ids["untagged"]
    real = service.store.apply

    def apply(r, op):
        real(r, op)
        raise OSError(errno.EIO, "fsync of the folder failed")

    monkeypatch.setattr(service.store, "apply", apply)
    session = session_of(service)
    final = ask(session, req("add", [rid], ["work"])).final
    assert final["status"] == "failed" and final["message"] == STOP_IO
    assert final["changed"] == 1 and final["undo"] and final["rows"][0]["id"] == rid


def test_five_busy_recordings_in_a_row_stop_the_batch(tag_archive):
    store = ArchiveStore(
        Archive(tag_archive.root), lambda: tag_archive.open_writer(lock_timeout=0.05)
    )
    service = TagService(store, progress_every=0)
    ids = list(tag_archive.ids.values())
    try:
        session = session_of(service)
        with Locks(tag_archive.state / "locks").hold(*ids):  # another job holds them all
            final = ask(session, req("add", ids, ["work"])).final
        assert final["status"] == "failed" and final["message"] == STOP_BUSY
        assert [f["reason"] for f in final["failures"]] == [BUSY] * 5
    finally:
        store.close()


def test_an_unexpected_error_stops_the_batch(service, tag_archive, monkeypatch):
    monkeypatch.setattr(service.store, "apply", lambda rid, op: 1 / 0)
    session = session_of(service)
    final = ask(session, req("add", [tag_archive.ids["untagged"]], ["work"])).final
    assert final["status"] == "failed" and final["message"] == STOP_UNEXPECTED


def test_a_stop_at_shutdown_ends_the_batch_between_recordings(service, tag_archive, monkeypatch):
    ids = list(tag_archive.ids.values())
    real = service.store.apply

    def apply(rid, op):
        after = real(rid, op)
        service.stop()  # as the app's shutdown does, while a batch runs
        return after

    monkeypatch.setattr(service.store, "apply", apply)
    session = session_of(service)
    final = ask(session, req("add", ids, ["work"])).final
    assert final["status"] == "failed" and final["message"] == STOP_SHUTDOWN
    assert final["changed"] == 1 and final["undo"]
    assert sum("work" in tags_of(service.store, rid) for rid in ids) == 1


def test_a_request_after_the_writer_thread_stopped_fails_with_its_reason(tag_archive):
    store = ArchiveStore(Archive(tag_archive.root), tag_archive.open_writer)
    service = TagService(store, progress_every=0)
    session = session_of(service)
    store.close()
    final = ask(session, req("add", [tag_archive.ids["untagged"]], ["work"])).final
    assert final["status"] == "failed" and final["message"] == f"Stopped: {STOPPED}"


def test_a_send_that_fails_mid_batch_still_finishes_and_never_raises(service, tag_archive, run):
    # why: Review Focus 2. The window closes mid-batch: the batch finishes on the server, and a
    # reload shows what landed.
    ids = list(tag_archive.ids.values())
    session = session_of(service)

    async def closed(_reply):
        raise ConnectionError("the window closed")

    run(session.run(session.check(req("add", ids, ["work"])), closed))
    assert all("work" in tags_of(service.store, rid) for rid in ids)


def test_a_final_reply_that_fails_to_send_is_never_followed_by_a_false_failure(
    service, tag_archive
):
    # why: the batch was applied. If its reply can't be sent, a second reply saying "Stopped: an
    # unexpected error" must not follow it: should that one get through, the window would show
    # a failure for changes that landed.
    rid = tag_archive.ids["untagged"]
    session = session_of(service)
    sent: list[dict] = []

    async def once_broken(reply: dict) -> None:
        if reply["status"] != "progress" and not sent:
            sent.append({"status": "lost"})
            raise ConnectionError("a moment's trouble")
        sent.append(reply)

    asyncio.run(session.run(session.check(req("add", [rid], ["work"])), once_broken))
    assert sent == [{"status": "lost"}] and tags_of(service.store, rid) == ["work"]


def test_progress_is_sent_between_recordings(service, tag_archive):
    ids = list(tag_archive.ids.values())
    session = session_of(service)
    replies = ask(session, req("add", ids, ["work"]))
    progress = [r for r in replies if r["status"] == "progress"]
    assert [p["done_so_far"] for p in progress] == [1, 2, 3, 4, 5]
    assert {p["total"] for p in progress} == {6}
    assert replies[-1]["status"] == "done" and replies[-1]["done_so_far"] == 6


# ---- request IDs and Undo --------------------------------------------------------------------
def test_a_request_id_is_applied_at_most_once_and_a_gated_one_is_not_recorded(service, tag_archive):
    ids = tag_archive.ids
    session = session_of(service)
    once = req("add", [ids["untagged"]], ["work"])
    assert ask(session, once).final["changed"] == 1
    assert ask(session, once) == []  # a repeat is ignored: no reply at all
    other_window = session_of(service)
    assert ask(other_window, once) == []  # kept per process, not per window
    gated = req("remove", [ids["private"]], ["private"])
    assert ask(session, gated).final["status"] == "confirm"
    assert ask(session, gated).final["status"] == "confirm"  # not recorded: asked again


def test_undo_is_single_use_and_an_unknown_token_says_so(service, tag_archive):
    rid = tag_archive.ids["untagged"]
    session = session_of(service)
    added = ask(session, req("add", [rid], ["work"])).final
    undone = ask(session, req("undo", undo=added["undo"])).final
    assert undone["status"] == "done" and tags_of(service.store, rid) == []
    assert undone["undo"]  # Redo (tagging plan, decision 7)
    again = ask(session, req("undo", undo=added["undo"])).final
    assert again["status"] == "failed" and again["message"] == ALREADY_UNDONE
    reloaded = session_of(service)
    assert ask(reloaded, req("undo", undo=undone["undo"])).final["message"] == UNDO_GONE
    redone = ask(session, req("undo", undo=undone["undo"])).final
    assert redone["status"] == "done" and tags_of(service.store, rid) == ["work"]


def test_a_gated_undo_keeps_its_token(service, tag_archive):
    rid = tag_archive.ids["untagged"]
    session = session_of(service)
    added = ask(session, req("add", [rid], ["private"])).final
    gated = ask(session, req("undo", undo=added["undo"])).final
    assert gated["status"] == "confirm"
    assert gated["loses_private"] == {"visible": 0, "untagged": 1, "ids": [rid]}
    done = ask(session, req("undo", undo=added["undo"], confirm=[rid])).final
    assert done["status"] == "done" and tags_of(service.store, rid) == []


def test_undo_tokens_survive_a_refresh(service, tag_archive, run):
    # why: Review Focus 3. Refresh rebuilds the view; only a reload ends the session's Undo.
    session = session_of(service)
    added = ask(session, req("add", [tag_archive.ids["untagged"]], ["work"])).final
    run(session.library(refresh=True))
    assert ask(session, req("undo", undo=added["undo"])).final["status"] == "done"


def test_the_session_keeps_only_the_last_twenty_actions(service, tag_archive):
    session = session_of(service)
    rid = tag_archive.ids["untagged"]
    tokens = [ask(session, req("add", [rid], [f"t{i}"])).final["undo"] for i in range(21)]
    assert ask(session, req("undo", undo=tokens[0])).final["message"] == UNDO_GONE
    assert ask(session, req("undo", undo=tokens[1])).final["status"] == "done"


def test_removing_a_note_type_records_the_exclusion_and_undo_reverses_it(service, tag_archive):
    rid = tag_archive.ids["noted"]
    session = session_of(service)
    removed = ask(session, req("remove", [rid], ["notes/lecture"])).final
    assert service.store.read(rid).excluded_note_types == ["lecture"]
    ask(session, req("undo", undo=removed["undo"]))
    rec = service.store.read(rid)
    assert [t.tag for t in rec.tags] == ["notes/lecture", "school/course-101"]
    assert rec.tags[0].by == "auto" and rec.excluded_note_types == []


# ---- deltas, now_visible and logs ------------------------------------------------------------
def test_deltas_patch_rows_without_rebuilding_the_view(service, tag_archive, monkeypatch):
    rid = tag_archive.ids["untagged"]
    session = session_of(service)
    monkeypatch.setattr(service.store, "library", lambda: pytest.fail("the view was rebuilt"))
    final = ask(session, req("add", [rid], ["Private/health"])).final
    (row,) = final["rows"]
    assert row["id"] == rid and row["private"] is True and row["untagged"] is False
    assert final["counts"] == {"all": 6, "untagged": 0}
    assert {"tag": "Private/health", "count": 1, "private": True} in final["tags"]
    assert {"note_type": "lecture", "count": 1} in final["note_types"]
    assert session.rows[rid] == row


@pytest.mark.parametrize(("tag", "visible"), [("work", 1), ("private", 0)])
def test_now_visible_counts_untagged_recordings_tagged_without_private(
    service, tag_archive, tag, visible
):
    ids = tag_archive.ids
    session = session_of(service)
    final = ask(session, req("add", [ids["untagged"], ids["plain"]], [tag])).final
    assert final["now_visible"] == visible


def test_logs_carry_no_tag_names_titles_or_exception_text(
    service, tag_archive, monkeypatch, caplog
):
    ids = tag_archive.ids
    real = service.store.apply

    def apply(rid, op):
        if rid == ids["plain"]:
            raise RuntimeError("Week 0 is tagged private/ada")  # a title and a tag in its text
        return real(rid, op)

    monkeypatch.setattr(service.store, "apply", apply)
    session = session_of(service)
    with caplog.at_level(logging.DEBUG):
        ask(session, req("add", [ids["untagged"]], ["private/ada"]))
        ask(session, req("add", [ids["plain"]], ["private/ada"]))
        ask(session, req("add", [ids["plain"]], ["\u0440rivate"]))
    assert "private/ada" not in caplog.text and "Week" not in caplog.text
    assert "\u0440" not in caplog.text and "RuntimeError" in caplog.text
    assert ids["plain"] in caplog.text  # IDs are fine: they carry no title
