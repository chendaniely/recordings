import os
import select
import subprocess
import sys
import threading
import time

import pytest

from recordings.locks import Locks, LockOrderError, LockTimeout, lock_order

A = "20261006T140003-0700_aaaaaaaa"
B = "20261007T140003-0700_bbbbbbbb"


def test_the_lock_order_is_people_then_tags_then_hashes_then_recordings():
    keys = ["index", B, "sha-" + "f" * 64, "tags.yaml", A, "people.private.yaml", "people.yaml"]
    assert sorted(keys, key=lock_order) == [
        "people.yaml", "people.private.yaml", "tags.yaml", "sha-" + "f" * 64, A, B, "index"]


def test_locks_taken_together_never_deadlock(tmp_path):
    # why: §6.4. Two operations naming the same files in opposite orders still take them in one.
    locks = Locks(tmp_path, timeout=10)
    done = []

    def worker(keys):
        for _ in range(50):
            with locks.hold(*keys):
                pass
        done.append(keys)

    threads = [threading.Thread(target=worker, args=(k,)) for k in ((A, B), (B, A))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(done) == 2


def test_taking_an_earlier_lock_while_holding_a_later_one_is_an_error(tmp_path):
    locks = Locks(tmp_path)
    with locks.hold(A), pytest.raises(LockOrderError), locks.hold("people.yaml"):
        pass


def test_holding_a_key_again_only_counts(tmp_path):
    locks, probe = Locks(tmp_path), Locks(tmp_path, timeout=0)
    with locks.hold(A):
        with locks.hold(A):
            pass
        with pytest.raises(LockTimeout), probe.hold(A):  # still held after the inner exit
            pass
    with probe.hold(A):  # released after the outer exit
        pass


def test_lock_acquisition_follows_lock_order_regardless_of_argument_order(tmp_path, monkeypatch):
    # why: within one process, hold() builds wanted = set(keys), whose iteration order
    # doesn't depend on argument order. So (A, B) and (B, A) acquire in the same order
    # even without the sort. Across processes, hash seeds differ, so deadlock-freedom
    # depends entirely on the sort. This test catches removal of sorted(..., key=lock_order).
    locks = Locks(tmp_path)
    acquired = []

    original_acquire = locks._acquire

    def spy_acquire(key: str, deadline: float) -> int:
        acquired.append(key)
        return original_acquire(key, deadline)

    monkeypatch.setattr(locks, "_acquire", spy_acquire)

    keys = (B, "index", "sha-" + "f" * 64, A, "tags.yaml", "people.private.yaml", "people.yaml")
    with locks.hold(*keys):
        pass

    assert acquired == sorted(keys, key=lock_order)


def test_a_hold_of_several_contended_keys_waits_one_timeout_in_total(tmp_path):
    # why: the timeout covers the whole hold(). Per key, N contended keys could wait N timeouts
    # while the message claims one. A is free after 0.3 s; B stays held, so B's wait is what counts.
    holder_b = Locks(tmp_path)
    a_taken, a_free = threading.Event(), threading.Event()

    def hold_a_briefly():
        with Locks(tmp_path).hold(A):
            a_taken.set()
            a_free.wait(timeout=10)

    thread = threading.Thread(target=hold_a_briefly)
    thread.start()
    assert a_taken.wait(timeout=10)
    threading.Timer(0.3, a_free.set).start()
    started = time.monotonic()
    try:
        with holder_b.hold(B), pytest.raises(LockTimeout):
            with Locks(tmp_path, timeout=0.5).hold(A, B):
                pass
        elapsed = time.monotonic() - started
    finally:
        a_free.set()
        thread.join(timeout=10)
    assert elapsed < 0.7  # one deadline of 0.5 s; a fresh one for B after A would end near 0.8


def test_a_timeout_on_a_later_key_releases_the_earlier_ones(tmp_path):
    with Locks(tmp_path).hold(B), pytest.raises(LockTimeout):
        with Locks(tmp_path, timeout=0.1).hold(A, B):
            pass
    with Locks(tmp_path, timeout=0).hold(A):  # A was taken first, then given back
        pass


def test_no_file_descriptor_leaks_when_the_wait_is_interrupted(tmp_path, monkeypatch):
    # why: a KeyboardInterrupt (or any exception) while waiting must not leave the lock file open.
    def interrupted(_seconds):
        raise KeyboardInterrupt

    before = set(os.listdir("/dev/fd"))
    with Locks(tmp_path).hold(A):  # another holder, so the second one waits and sleeps
        monkeypatch.setattr(time, "sleep", interrupted)
        with pytest.raises(KeyboardInterrupt):
            with Locks(tmp_path, timeout=10).hold(A):
                pass
        monkeypatch.undo()
        assert len(set(os.listdir("/dev/fd")) - before) == 1  # only the holder's own lock file
    assert set(os.listdir("/dev/fd")) == before


@pytest.mark.parametrize("key", ["a/b", "a b", "", "../x"])
def test_a_key_that_is_not_a_plain_file_name_is_refused(tmp_path, key):
    # why: keys become file names. Substituting characters let "a/b" and "a b" share a lock file.
    with pytest.raises(ValueError):
        with Locks(tmp_path).hold(key):
            pass


def test_a_lock_is_released_when_its_process_dies(tmp_path):
    # why: §6.8. flock is released by the kernel, so a crash never leaves a stale lock.
    script = ("import sys,time; from pathlib import Path; from recordings.locks import Locks\n"
              "with Locks(Path(sys.argv[1])).hold(sys.argv[2]):\n"
              "    print('held', flush=True); time.sleep(60)\n")
    with subprocess.Popen([sys.executable, "-c", script, str(tmp_path), A],
                          stdout=subprocess.PIPE, text=True) as proc:
        try:
            assert select.select([proc.stdout], [], [], 10)[0], "the child never took the lock"
            assert proc.stdout.readline().strip() == "held"
            with pytest.raises(LockTimeout), Locks(tmp_path, timeout=0.2).hold(A):
                pass
            proc.kill()
            proc.wait(timeout=10)
        finally:
            proc.kill()
    started = time.monotonic()
    with Locks(tmp_path, timeout=5).hold(A):
        assert time.monotonic() - started < 5


def test_a_held_ops_lock_says_which_job_runs_and_since_when(tmp_path):
    # why: spec §12.5. Status shows the running job and since when, read from another process.
    from datetime import datetime, timezone

    from recordings.locks import OPS_LOCK, announce, running
    folder, since = tmp_path / "locks", datetime(2026, 10, 9, 7, 0, tzinfo=timezone.utc)
    assert running(folder) is None  # no lock file yet
    with Locks(folder, timeout=0).hold(OPS_LOCK):
        assert running(folder) is None  # held, not announced
        with announce(folder, "restore-test", since):
            assert running(folder) == ("restore-test", since)
        assert running(folder) is None  # announce empties the file
    (folder / "ops.lock").write_text('{"job": "check", "since": "2026-10-09T07:00:00+00:00"}')
    assert running(folder) is None  # a crashed holder's leftover: the lock is free
