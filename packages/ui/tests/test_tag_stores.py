"""The tag service's stores and the writer thread (tagging spec §5). The `store` fixture (both
stores) and `run` are in conftest.py, shared with test_tag_service.py."""

import fcntl
import logging
import subprocess
import sys
import threading
import time

import pytest
from pydantic import BaseModel
from recordings.archive import Archive
from recordings.index import Index
from recordings.init import init_archive
from recordings.locks import LockTimeout
from recordings.models import TagRef
from recordings.sentinel import MIRROR, SENTINEL
from recordings.state import State
from recordings.tagops import AddTags, Recorded
from recordings.writer import InvalidFile, NotTheWriter, StaleFile, Writer
from recordings_ui import tagging
from recordings_ui.tagging import (
    BUSY,
    INVALID,
    STALE,
    STOP_NOT_WRITER,
    STOP_STATE,
    STOP_THREAD,
    ArchiveStore,
    Classified,
    MemoryStore,
    pages_store,
)
from recordings_ui.writing import STOPPED, OneThread, ThreadStopped


def add(tag: str) -> Recorded:
    return Recorded(AddTags((TagRef(tag=tag, by="you"),)), "req0001abcd")


def test_both_stores_apply_an_operation_bump_rev_and_show_it(store, tag_archive, run):
    rid = tag_archive.ids["untagged"]
    after = run(store.write(lambda: store.apply(rid, add("talks"))))
    assert [t.tag for t in after.tags] == ["talks"] and after.rev == 2
    assert [t.tag for t in store.read(rid).tags] == ["talks"]
    shown = {r["id"]: r for r in store.library()["recordings"]}[rid]
    assert shown["tags"] == [{"tag": "talks", "by": "you"}] and shown["untagged"] is False


def test_an_unchanged_result_writes_nothing(store, tag_archive, run):
    rid = tag_archive.ids["plain"]
    after = run(store.write(lambda: store.apply(rid, add("talks"))))
    assert after.rev == 1


def test_the_archive_store_writes_the_file(tag_archive, run):
    store = ArchiveStore(Archive(tag_archive.root), tag_archive.open_writer)
    try:
        run(store.write(lambda: store.apply(tag_archive.ids["untagged"], add("talks"))))
        assert tag_archive.tags("untagged") == ["talks"]
    finally:
        store.close()


def test_the_memory_store_writes_no_file_takes_no_lock_and_starts_no_thread(
    tag_archive, monkeypatch, run
):
    # why: spec §5. Pages can't run threads or locks (stage-2a decisions 6 and 7), and its
    # pydantic can't serialise these models faithfully (stage-2a decision 7).
    def refuse(*_args, **_kwargs):
        raise AssertionError("the memory store must not do this")

    before = {p: p.read_bytes() for p in tag_archive.root.rglob("*") if p.is_file()}
    monkeypatch.setattr(BaseModel, "model_dump_json", refuse)
    monkeypatch.setattr(fcntl, "flock", refuse)
    monkeypatch.setattr(threading.Thread, "start", refuse)  # asyncio.run itself starts none
    store = MemoryStore(Archive(tag_archive.root))
    rid = tag_archive.ids["untagged"]
    run(store.write(lambda: store.apply(rid, add("talks"))))
    assert [t.tag for t in store.read(rid).tags] == ["talks"]
    assert list(store.changed()) == [rid]
    assert {p: p.read_bytes() for p in tag_archive.root.rglob("*") if p.is_file()} == before
    assert store.status(retry=True) == (True, None) and store.saves is False
    assert run(store.open_for_write()) == (True, None)


def test_the_pages_store_is_memory_and_only_under_pyodide(tag_archive, monkeypatch):
    # why: spec §5. Outside Pyodide the service never falls back to memory, so `make demo` and
    # the real app can't silently stop saving.
    with pytest.raises(RuntimeError, match="Pyodide"):
        pages_store(Archive(tag_archive.root))
    monkeypatch.setattr(tagging, "under_pyodide", lambda: True)
    assert isinstance(pages_store(Archive(tag_archive.root)), MemoryStore)


def test_the_tagging_module_imports_without_sqlite3_fcntl_or_the_writer(tag_archive):
    # why: spec §5. recordings_ui.tagging is vendored into the Pages demo, whose Pyodide has no
    # sqlite3 or fcntl; only ArchiveStore may bring them in.
    code = (
        "import sys; sys.modules['sqlite3'] = None; sys.modules['fcntl'] = None\n"
        "from pathlib import Path\n"
        "from recordings.archive import Archive\n"
        "from recordings_ui.tagging import MemoryStore\n"
        f"view = MemoryStore(Archive(Path({str(tag_archive.root)!r}))).library()\n"
        "assert view['counts']['all'] == 6\n"
        "assert 'recordings.writer' not in sys.modules\n"
        "assert 'recordings_ui.writing' not in sys.modules\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


# ---- can_tag and its reasons (spec §5) -------------------------------------------------------
def _status(tag_archive, open_writer) -> tuple[bool, str | None]:
    store = ArchiveStore(Archive(tag_archive.root), open_writer)
    try:
        return store.wait_open(30)
    finally:
        store.close()


def test_the_writer_can_tag(tag_archive):
    assert _status(tag_archive, tag_archive.open_writer) == (True, None)


def test_the_mirrors_copy_cannot_tag(tag_archive):
    (tag_archive.root / MIRROR).write_text("the mirror's copy\n", encoding="utf-8")
    ok, reason = _status(tag_archive, tag_archive.open_writer)
    assert not ok and "mirror" in reason


def test_an_archive_without_its_sentinel_cannot_tag(tag_archive):
    (tag_archive.root / SENTINEL).rename(tag_archive.root.parent / "sentinel-aside.json")
    ok, reason = _status(tag_archive, tag_archive.open_writer)
    assert not ok and SENTINEL in reason


def test_a_foreign_state_db_cannot_tag(tag_archive, tmp_path):
    init_archive(tmp_path / "other", tmp_path / "other-state", writer_id="test")

    def foreign() -> Writer:
        return Writer(
            tag_archive.root,
            State.open(tmp_path / "other-state"),
            Index(tag_archive.index),
            writer_id="test",
        )

    ok, reason = _status(tag_archive, foreign)
    assert not ok and "another archive" in reason


def test_an_unreadable_state_db_cannot_tag(tag_archive):
    for leftover in tag_archive.state.glob("state.db-*"):
        leftover.unlink()
    (tag_archive.state / "state.db").write_bytes(b"this is not a database\n" * 64)
    ok, reason = _status(tag_archive, tag_archive.open_writer)
    assert not ok and "restore state.db" in reason.lower()


def test_another_machines_writer_id_cannot_tag(tag_archive):
    ok, reason = _status(tag_archive, lambda: tag_archive.open_writer(writer_id="other"))
    assert not ok and "not the archive's writer" in reason


def test_a_failed_open_logs_its_class_never_its_text(tag_archive, caplog):
    # why: Global Constraints. The text is shown as cannot_tag_reason; it can name a path or an
    # ID, so the log carries a fixed phrase and the exception's class only.
    def refused() -> Writer:
        raise NotTheWriter("Week 4 lives at /somewhere/secret")

    with caplog.at_level(logging.WARNING):
        ok, reason = _status(tag_archive, refused)
    assert not ok and reason == "Week 4 lives at /somewhere/secret"
    assert "not writing the archive's docs" in caplog.text and "NotTheWriter" in caplog.text
    assert "Week 4" not in caplog.text and "secret" not in caplog.text


def test_a_transient_failure_is_retried_by_the_next_request(tag_archive, run):
    # why: spec §5. A busy moment at start must not disable tagging until a restart.
    calls = []

    def flaky() -> Writer:
        calls.append(1)
        if len(calls) == 1:
            raise LockTimeout("docs is held by another writer; gave up after 2 s")
        return tag_archive.open_writer()

    store = ArchiveStore(Archive(tag_archive.root), flaky)
    try:
        ok, reason = store.wait_open(30)
        assert not ok and "LockTimeout" in reason and "Refresh" in reason
        assert run(store.open_for_write()) == (True, None) and len(calls) == 2
    finally:
        store.close()


def test_other_failures_are_retried_only_by_refresh_which_never_waits(tag_archive, run):
    # why: spec §5. A request retries only a transient failure; Refresh retries any, and queues
    # the retry without waiting for it, so a read never queues behind the writer thread.
    calls = []
    gate = threading.Event()

    def refused_then_slow() -> Writer:
        calls.append(1)
        if len(calls) == 1:
            raise NotTheWriter("not the archive's writer")
        gate.wait(10)  # the retry is slow: Refresh must not wait for it
        return tag_archive.open_writer()

    store = ArchiveStore(Archive(tag_archive.root), refused_then_slow)
    try:
        assert store.wait_open(30) == (False, "not the archive's writer")
        assert run(store.open_for_write())[0] is False and len(calls) == 1  # not retried
        assert store.status() == (False, "not the archive's writer") and len(calls) == 1
        assert store.status(retry=True)[0] is False  # queued, and answered at once
        gate.set()
        assert store.wait_open(30) == (True, None) and len(calls) == 2
    finally:
        gate.set()
        store.close()


def test_a_stopped_writer_thread_turns_tagging_off_and_refuses_work(tag_archive, run):
    store = ArchiveStore(Archive(tag_archive.root), tag_archive.open_writer)
    store.wait_open(30)
    store.close()
    deadline = time.monotonic() + 10
    while store._thread.executor.alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert store.status(retry=True) == (False, STOPPED)
    assert run(store.open_for_write()) == (False, STOPPED)
    assert f"Stopped: {STOPPED}" == STOP_THREAD  # one phrase, in both places (spec §8)
    with pytest.raises(ThreadStopped):
        run(store.write(lambda: None))


@pytest.mark.parametrize(
    ("exc", "found"),
    [
        (NotTheWriter("x"), Classified(STOP_NOT_WRITER, True)),
        (ThreadStopped("x"), Classified(STOP_THREAD, True)),
        (StaleFile("x"), Classified(STALE, False)),
        (InvalidFile("x"), Classified(INVALID, False)),
        (LockTimeout("x"), Classified(BUSY, False)),
        (ValueError("x"), None),
    ],
)
def test_the_archive_store_names_the_writers_refusals(tag_archive, exc, found):
    store = ArchiveStore(Archive(tag_archive.root), tag_archive.open_writer)
    try:
        assert store.classify(exc) == found
    finally:
        store.close()


def test_a_sqlite_error_is_busy_or_stops_the_batch(tag_archive):
    import sqlite3

    store = ArchiveStore(Archive(tag_archive.root), tag_archive.open_writer)
    try:
        busy = sqlite3.OperationalError("database is locked")
        busy.sqlite_errorname = "SQLITE_BUSY"
        assert store.classify(busy) == Classified(BUSY, False)
        assert store.classify(sqlite3.DatabaseError("x")) == Classified(STOP_STATE, True)
    finally:
        store.close()


# ---- the one-thread executor -----------------------------------------------------------------
def test_one_thread_runs_jobs_in_order_and_survives_a_failing_job():
    pool = OneThread("test-one-thread")
    try:
        seen: list[int] = []
        first = pool.submit(seen.append, 1)
        bad = pool.submit(lambda: 1 / 0)
        last = pool.submit(seen.append, 2)
        assert first.result(5) is None and last.result(5) is None and seen == [1, 2]
        with pytest.raises(ZeroDivisionError):
            bad.result(5)
        assert pool._thread.daemon and pool.alive()
    finally:
        pool.shutdown()
    assert not pool.alive()
    with pytest.raises(ThreadStopped):
        pool.submit(seen.append, 3)


def test_one_thread_finishes_what_was_queued_then_refuses_more():
    pool = OneThread("test-one-thread")
    gate = threading.Event()
    blocked = pool.submit(gate.wait, 10)
    queued = [pool.submit(lambda: "ran") for _ in range(3)]
    pool.shutdown(wait=False)
    gate.set()
    assert blocked.result(5) is True
    assert all(f.result(5) == "ran" for f in queued)  # queued before the stop: they still run
    with pytest.raises(ThreadStopped):
        pool.submit(lambda: "after")


@pytest.mark.parametrize("key", ["docs", "index"])
def test_a_lock_the_cli_holds_during_open_is_a_transient_failure_not_a_permanent_one(
    tag_archive, run, key
):
    # why: lock_timeout governs the locks Writer.__init__/open takes (DOCS_LOCK, INDEX_LOCK), so
    # a LockTimeout can escape the open while the CLI holds one. It must be retried by the next
    # request, never left as a permanent can_tag=false.
    from recordings.locks import Locks

    if key == "index":
        for leftover in tag_archive.index.parent.glob(tag_archive.index.name + "*"):
            leftover.unlink()  # a missing index is rebuilt on open, under INDEX_LOCK

    def open_like_the_cli_does() -> Writer:  # Writer.open: construct, then write the docs
        writer = tag_archive.open_writer(lock_timeout=0.2)
        writer.write_docs()
        return writer

    cli = Locks(State.open(tag_archive.state).locks_dir)
    store = ArchiveStore(Archive(tag_archive.root), open_like_the_cli_does)
    try:
        with cli.hold(key):
            ok, reason = store.wait_open(30)
            assert not ok and "Refresh" in reason and "LockTimeout" in reason
            assert store._thread.transient is True
        assert run(store.open_for_write()) == (True, None)
    finally:
        store.close()
