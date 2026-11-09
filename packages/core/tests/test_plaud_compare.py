"""The compare (stage 2b spec §11.3, §11.4 step 6, §9.5's cache; area A3): the buckets and their
precedence, both equations, what a complete listing writes to plaud_listing and the cache, the
titles the cache may hold, and the re-key match. Every ID and title is synthetic."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from recordings.archive import RawSource
from recordings.plaud.cache import Cache, KeyedOut, title_allowed
from recordings.plaud.checks import K_COMPARE, ListingEntry, PlaudDB
from recordings.plaud.client import Listed
from recordings.plaud.compare import (
    AUTO_IMPORT_OFF,
    BY_CONSENT,
    BY_REKEY,
    BY_TITLE,
    BY_WAS_TITLE,
    CONSENT_STOPPED,
    DISK_FULL,
    EXCLUDED,
    FAILED,
    GATE_WAIT,
    HELD,
    HERE,
    MISSING,
    NEXT_SYNC,
    PARKED,
    REVIEW,
    SCHEDULE_OFF,
    SEVERAL,
    TITLES_HIDDEN_CONSENT,
    TITLES_HIDDEN_IDS,
    WAITING,
    Conditions,
    Here,
    IndexUnavailable,
    classify,
    compare,
    here_from_archive,
    lines,
    record_compare,
    title_hash,
)
from recordings.plaud.flags import KEPT_HERE, keep_here, record_late_keep_out
from recordings.plaud.keepout import parse_consent
from recordings.state import State

T0 = datetime(2026, 11, 2, 14, 20, tzinfo=UTC)
START = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
WORDS = ("noarchive",)
PATTERNS = (r"^\s*private",)


def pid(n: int) -> str:
    return f"{0x5EED0000 + n:032x}"


def listed(n: int, title: str | None = None, *, form: str = "of_", **over) -> Listed:
    fields = {
        "listed_id": ("of_" if form == "of_" else "") + pid(n),
        "title": f"Synthetic meeting {n}" if title is None else title,
        "created_at": START + timedelta(hours=n),
        "start_at": START + timedelta(hours=n),
        "duration_ms": 60_000 + 1_000 * n,
    }
    fields.update(over)
    return Listed(**fields)


def consent(*ns: int):
    return parse_consent("".join(f"- `{pid(n)}` — synthetic\n" for n in ns) or "# none\n")


GOOD_CONSENT = consent(99)  # reads cleanly and names an ID nobody lists


@pytest.fixture
def db(tmp_path):
    from recordings.init import init_archive

    init_archive(tmp_path / "archive", tmp_path / "state", writer_id="test")
    return PlaudDB(State.open(tmp_path / "state"))


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "state")


def run(rows, *, here=None, consent_list=GOOD_CONSENT, listing=None, keyed=None, **conditions):
    here = here or Here(by_id={})
    cond = Conditions(**conditions)
    classified = classify(
        rows,
        here=here,
        consent=consent_list,
        words=WORDS,
        listing=listing or {},
        keyed_out=keyed or {},
        patterns=PATTERNS,
        conditions=cond,
    )
    return compare(classified, at=T0, here=here, consent=consent_list, conditions=cond)


def by_id(result):
    return {row.canonical: row for row in result.rows}


def test_every_listed_id_lands_in_one_bucket_by_precedence(db):
    # §11.3: here, then excluded, then waiting or missing. A recording here that is consent-listed
    # or carries the keep-out word stays here (its flag is the import's): precedence 1 wins.
    here = Here(by_id={pid(1): frozenset({"rec-a"}), pid(2): frozenset({"rec-b"})})
    rows = [
        listed(1, form="hex"),  # here, listed bare while the archive holds it either way
        listed(2, "noarchive: but it is here"),  # here, the word aside (a late keep-out)
        listed(3),  # consent-listed
        listed(4, "NoArchive coffee"),  # kept out by its title
        listed(5),  # waiting
        listed(6, "Private: synthetic"),  # waiting, arrives private
    ]
    result = run(rows, here=here, consent_list=consent(3, 2))
    got = by_id(result)
    assert [got[pid(n)].bucket for n in range(1, 7)] == [
        HERE,
        HERE,
        EXCLUDED,
        EXCLUDED,
        WAITING,
        WAITING,
    ]
    assert got[pid(2)].word == "noarchive" and got[pid(2)].recording_ids == ("rec-b",)
    assert (got[pid(3)].excluded_by, got[pid(3)].listed.title) == (BY_CONSENT, None)
    assert (got[pid(4)].excluded_by, got[pid(4)].kept_out_now) == (BY_TITLE, True)
    assert got[pid(6)].arrives_private and not got[pid(5)].arrives_private
    assert result.equation == {
        "listed": 6,
        "here": 2,
        "waiting": 2,
        "excluded": 2,
        "missing": 0,
        "arrive_private": 1,
    }
    assert result.consent == {"ids": 2, "on_plaud": 2}


@pytest.mark.parametrize(
    ("conditions", "row", "state"),
    [
        ({"gate_open": False}, {}, GATE_WAIT),
        ({"held": True}, {}, HELD),
        ({}, {"needs_review": True}, REVIEW),
        ({}, {"parked_at": T0}, PARKED),
        ({}, {"import_failures": 3}, FAILED),
        ({"disk_full": True}, {}, DISK_FULL),
        ({"schedule_on": False}, {}, SCHEDULE_OFF),
        ({"auto_import": False}, {}, AUTO_IMPORT_OFF),
        ({}, {"import_failures": 2}, None),
    ],
)
def test_missing_says_why_it_wont_come_without_dan(db, conditions, row, state):
    # §11.3's missing, with §15.1's state: the first reason that applies.
    if row:
        db.set_listing(pid(1), first_seen=T0 - timedelta(days=1), **row)
    result = run([listed(1)], listing=db.listing_rows(), **conditions)
    (one,) = result.rows
    assert (one.bucket, one.state) == ((MISSING, state) if state else (WAITING, NEXT_SYNC))
    assert one.review == bool(row.get("needs_review"))


def test_a_consent_stop_leaves_everything_not_here_missing_and_shows_no_title(db, cache):
    # §12.1: during a consent stop no title of a recording not here is stored or shown (C5.15).
    stopped = parse_consent("- 3fa9 split typo 1234567890\n")
    assert stopped.stop is not None
    result = run([listed(1), listed(2)], consent_list=stopped)
    assert {(r.bucket, r.state, r.title_shown) for r in result.rows} == {
        (MISSING, CONSENT_STOPPED, False)
    }
    assert result.titles_hidden == TITLES_HIDDEN_CONSENT
    record_compare(db, cache, result, consent=stopped, listing={}, now=T0)
    assert [r.title for r in cache.compare_rows()] == [None, None]


def test_a_hold_with_plauds_ids_changed_shows_no_title_but_a_plain_hold_does(db, cache):
    # §11.4 step 3 (C5.15): while a held listing shows a census change or under 80% overlap, a
    # consent-listed or kept-out recording may be among the new IDs: no title. A large shrink
    # alone hides none.
    changed = run([listed(1)], held=True, ids_changed=True)
    assert changed.titles_hidden == TITLES_HIDDEN_IDS and not changed.rows[0].title_shown
    record_compare(db, cache, changed, consent=GOOD_CONSENT, listing={}, now=T0)
    assert cache.compare_rows()[0].title is None
    plain = run([listed(1)], held=True)
    assert plain.titles_hidden is None and plain.rows[0].title_shown


def test_review_rows_show_no_title_and_import_all_leaves_them_out(db):
    # §11.6: a recording that needs review shows by date, time and duration; Import all counts
    # the rest ("Import all 31 · 6 more need your review").
    db.set_listing(pid(2), first_seen=T0 - timedelta(days=1), needs_review=True)
    db.set_listing(pid(3), first_seen=T0 - timedelta(days=1), parked_at=T0)
    result = run([listed(1), listed(2), listed(3)], listing=db.listing_rows())
    got = by_id(result)
    assert got[pid(1)].title_shown and not got[pid(2)].title_shown
    assert result.import_all == {"count": 1, "review_more": 1}


def test_kept_out_by_title_stays_kept_out_until_release(db, cache):
    # §12.2 (C5.05): the title loses the word, and it stays out ("was kept out by title"),
    # never with a title. Only Release lets it in.
    first = run([listed(1, "noarchive: synthetic")])
    record_compare(db, cache, first, consent=GOOD_CONSENT, listing={}, now=T0)
    row = db.listing(pid(1))
    assert (row.kept_out_at, row.kept_out_word, row.title_sha256) == (T0, "noarchive", None)
    later = run([listed(1, "Synthetic coffee")], listing=db.listing_rows())
    assert (later.rows[0].bucket, later.rows[0].excluded_by) == (EXCLUDED, BY_WAS_TITLE)
    record_compare(db, cache, later, consent=GOOD_CONSENT, listing=db.listing_rows(), now=T0)
    (cached,) = cache.compare_rows()
    assert (cached.excluded_by, cached.title) == (BY_WAS_TITLE, None)
    assert db.listing(pid(1)).kept_out_at == T0  # kept once set
    db.set_listing(pid(1), released_at=T0 + timedelta(minutes=5))
    released = run([listed(1, "Synthetic coffee")], listing=db.listing_rows())
    assert (released.rows[0].bucket, released.rows[0].title_shown) == (WAITING, True)
    again = run([listed(1, "noarchive again")], listing=db.listing_rows())
    assert again.rows[0].excluded_by == BY_TITLE  # a word in the title now keeps it out


def test_a_complete_listing_writes_plaud_listing_and_marks_the_rest_absent(db, cache):
    # §9.5: the ID as listed follows the newest form; a title hash and duration only for an ID
    # here that is neither consent-listed nor kept out; everything else unlisted is absent.
    here = Here(by_id={pid(1): frozenset({"rec-a"}), pid(2): frozenset({"rec-b"})})
    db.set_listing(pid(7), first_seen=T0 - timedelta(days=3))  # listed before, gone now
    rows = [listed(1), listed(2), listed(3), listed(4)]
    result = run(rows, here=here, consent_list=consent(2, 3), listing=db.listing_rows())
    record_compare(db, cache, result, consent=consent(2, 3), listing=db.listing_rows(), now=T0)
    one = db.listing(pid(1))
    assert one.listed_id == "of_" + pid(1)
    assert (one.title_sha256, one.duration_ms) == (title_hash("Synthetic meeting 1"), 61_000)
    for n in (2, 3, 4):
        row = db.listing(pid(n))
        assert (row.title_sha256, row.duration_ms, row.absent_since) == (None, None, None)
    assert db.listing(pid(7)).absent_since == T0
    flipped = run([listed(1, form="hex")], here=here, listing=db.listing_rows())
    record_compare(db, cache, flipped, consent=GOOD_CONSENT, listing=db.listing_rows(), now=T0)
    assert db.listing(pid(1)).listed_id == pid(1)  # the row follows the newest form
    assert db.get(K_COMPARE)["equation"]["listed"] == 1


def test_the_archive_equation_counts_recordings_here_gone_and_absent(db):
    # §11.3: In the archive from Plaud = here + gone from Plaud + absent from this listing.
    here = Here(
        by_id={
            pid(1): frozenset({"rec-a"}),
            pid(2): frozenset({"rec-a"}),  # one recording, two Plaud IDs
            pid(3): frozenset({"rec-b"}),
            pid(4): frozenset({"rec-c"}),
        },
        gone=frozenset({"rec-c"}),
    )
    result = run([listed(1), listed(2), listed(5)], here=here)
    assert result.equation["here"] == 2  # listed IDs
    assert result.archive == {"from_plaud": 3, "here": 1, "gone": 1, "absent": 1}
    assert lines(result)[1] == (
        "in the archive from Plaud 3 = here 1 + gone from Plaud 1 + absent from this listing 1"
    )


def test_a_gone_recording_listed_again_is_here_again(db, cache):
    # §12.3: "If it returns to the listing, it is here again."
    db.mark_gone(["rec-a"], now=T0)
    here = Here(by_id={pid(1): frozenset({"rec-a"})}, gone=db.gone_recordings())
    result = run([listed(1)], here=here)
    record_compare(db, cache, result, consent=GOOD_CONSENT, listing={}, now=T0)
    assert db.check_row("rec-a").gone_since is None


def keyed(n: int, kind: str = "consent", *, start=None, duration=None) -> KeyedOut:
    return KeyedOut(
        pid(n),
        kind,
        start if start is not None else START + timedelta(hours=n),
        duration if duration is not None else 60_000 + 1_000 * n,
    )


def test_a_consent_listed_id_back_under_new_hex_is_matched_and_shows_no_title(db, cache):
    # §11.4 step 6 (C5.29): an ID nobody has seen, listed while a consent-listed one is absent,
    # within a second on start time and duration, is that recording: excluded, flagged, and its
    # title never stored.
    db.set_listing(pid(3), first_seen=T0 - timedelta(days=2))
    old = keyed(3)
    new = listed(
        40,
        "Synthetic title that must not be kept",
        start_at=old.start_at + timedelta(milliseconds=600),
        duration_ms=old.duration_ms + 900,
    )
    result = run([new], consent_list=consent(3), listing=db.listing_rows(), keyed={pid(3): old})
    (row,) = result.rows
    assert (row.bucket, row.excluded_by, row.rekey_of, row.title_shown) == (
        EXCLUDED,
        BY_REKEY,
        pid(3),
        False,
    )
    record_compare(db, cache, result, consent=consent(3), listing=db.listing_rows(), now=T0)
    assert cache.compare_rows()[0].title is None
    assert db.listing(pid(40)).rekey_of == pid(3)
    (flag,) = db.open_flags(("rekeyed",))
    assert (flag.canonical_plaud_id, flag.detail) == (pid(40), {"of": "consent"})
    # Seen once, it stays excluded in later listings, and is flagged only once.
    later = run([new], consent_list=consent(3), listing=db.listing_rows())
    assert (later.rows[0].excluded_by, later.rows[0].rekey_of) == (BY_REKEY, None)
    record_compare(db, cache, later, consent=consent(3), listing=db.listing_rows(), now=T0)
    assert len(db.open_flags(("rekeyed",))) == 1
    # Dan confirms with `make consent-add` and the new ID: the flag resolves.
    record_compare(db, cache, later, consent=consent(3, 40), listing=db.listing_rows(), now=T0)
    assert db.open_flags(("rekeyed",)) == []


def test_a_kept_out_id_re_keyed_is_matched_too(db):
    # C5.31.
    db.set_listing(pid(5), first_seen=T0 - timedelta(days=2), kept_out_at=T0, kept_out_word="x")
    old = keyed(5, "title")
    new = listed(41, start_at=old.start_at, duration_ms=old.duration_ms)
    result = run([new], listing=db.listing_rows(), keyed={pid(5): old})
    assert (result.rows[0].excluded_by, result.rows[0].rekey_of) == (BY_REKEY, pid(5))


def test_no_match_beyond_a_second_and_two_candidates_hold_the_new_id(db):
    # §11.4 step 6 (C5.32): more than one candidate, either way, holds the new ID the same way.
    for n in (3, 4):
        db.set_listing(pid(n), first_seen=T0 - timedelta(days=2))
    a = keyed(3, start=START, duration=90_000)
    b = keyed(4, start=START + timedelta(milliseconds=500), duration=90_500)
    far = listed(50, start_at=START + timedelta(seconds=2), duration_ms=90_000)
    result = run([far], consent_list=consent(3, 4), listing=db.listing_rows(), keyed={})
    assert result.rows[0].bucket == WAITING  # nothing absent in the cache: nothing to match
    both = {pid(3): a, pid(4): b}
    result = run([far], consent_list=consent(3, 4), listing=db.listing_rows(), keyed=both)
    assert result.rows[0].bucket == WAITING  # 2 seconds away: no match
    near = listed(51, start_at=START + timedelta(milliseconds=250), duration_ms=90_250)
    result = run([near], consent_list=consent(3, 4), listing=db.listing_rows(), keyed=both)
    assert (result.rows[0].excluded_by, result.rows[0].rekey_of) == (BY_REKEY, SEVERAL)
    twin = listed(52, start_at=START, duration_ms=90_000)
    one = {pid(3): a}
    result = run([near, twin], consent_list=consent(3), listing=db.listing_rows(), keyed=one)
    assert {r.rekey_of for r in result.rows} == {SEVERAL}  # two new IDs for one absent one


def twin_of(old: Listed, n: int = 40, title: str = "Synthetic title that must not be kept"):
    """A new ID within a second of `old` on both start time and duration."""
    return listed(
        n,
        title,
        start_at=old.recorded_at + timedelta(milliseconds=600),
        duration_ms=old.duration_ms + 900,
    )


def rekeyed(row) -> tuple:
    return (row.bucket, row.excluded_by, row.rekey_of, row.title_shown)


def test_a_new_id_beside_the_old_is_matched_at_once_and_after_the_old_goes(db, cache):
    # Dan's answers to questions 1 and 6 (Phase 1's Q&A §9): one rule, whether the old ID has
    # vanished, is still listed or is here. This test pinned the opposite until 2026-11-09 (no
    # match while the old ID is listed), which would have left the title on the Sync screen.
    old = listed(3)
    first = run([old], consent_list=consent(3))
    record_compare(db, cache, first, consent=consent(3), listing={}, now=T0)
    twin = twin_of(old)
    beside = run(
        [old, twin], consent_list=consent(3), listing=db.listing_rows(), keyed=cache.keyed_out()
    )
    assert rekeyed(by_id(beside)[pid(40)]) == (EXCLUDED, BY_REKEY, pid(3), False)
    record_compare(db, cache, beside, consent=consent(3), listing=db.listing_rows(), now=T0)
    assert {r.canonical_plaud_id: r.title for r in cache.compare_rows()}[pid(40)] is None
    (flag,) = db.open_flags(("rekeyed",))
    assert (flag.canonical_plaud_id, flag.detail) == (pid(40), {"of": "consent"})
    # Seen once it stays matched, with the old ID listed or not, and is flagged once.
    for rows in ([old, twin], [twin]):
        later = run(
            rows, consent_list=consent(3), listing=db.listing_rows(), keyed=cache.keyed_out()
        )
        assert rekeyed(by_id(later)[pid(40)]) == (EXCLUDED, BY_REKEY, None, False)
        record_compare(db, cache, later, consent=consent(3), listing=db.listing_rows(), now=T0)
    assert len(db.open_flags(("rekeyed",))) == 1


@pytest.mark.parametrize(
    ("title", "kept_by", "consent_list", "of"),
    [
        (None, BY_CONSENT, consent(3), "consent"),
        ("noarchive: synthetic", BY_TITLE, GOOD_CONSENT, "kept-out"),
    ],
)
def test_the_first_listing_that_shows_both_ids_imports_neither(
    db, cache, title, kept_by, consent_list, of
):
    # Question 1: the cache learns an ID's date only after the compare, so a listing that first
    # shows the old ID and the new one together matches against its own rows.
    old = listed(3, title)
    result = run([old, twin_of(old)], consent_list=consent_list)
    got = by_id(result)
    assert (got[pid(3)].bucket, got[pid(3)].excluded_by) == (EXCLUDED, kept_by)
    assert rekeyed(got[pid(40)]) == (EXCLUDED, BY_REKEY, pid(3), False)
    assert result.equation["waiting"] == result.equation["missing"] == 0
    record_compare(db, cache, result, consent=consent_list, listing={}, now=T0)
    assert [r.title for r in cache.compare_rows()] == [None, None]
    assert db.listing(pid(40)).rekey_of == pid(3)
    (flag,) = db.open_flags(("rekeyed",))
    assert (flag.canonical_plaud_id, flag.detail) == (pid(40), {"of": of})


def test_several_candidates_still_listed_hold_the_new_id(db):
    # The SEVERAL rule stays, with the candidates listed beside the new ID.
    a = listed(3, start_at=START, duration_ms=90_000)
    b = listed(4, start_at=START + timedelta(milliseconds=500), duration_ms=90_500)
    near = listed(51, start_at=START + timedelta(milliseconds=250), duration_ms=90_250)
    got = by_id(run([a, b, near], consent_list=consent(3, 4)))
    assert rekeyed(got[pid(51)]) == (EXCLUDED, BY_REKEY, SEVERAL, False)


def test_a_title_kept_out_never_matches_itself_or_another_kept_out_title(db):
    a = listed(4, "noarchive: synthetic")
    b = twin_of(a, 41, "noarchive: synthetic twin")
    assert {(r.excluded_by, r.rekey_of) for r in run([a, b]).rows} == {(BY_TITLE, None)}


@pytest.mark.parametrize("old_still_listed", [True, False])
def test_a_recording_here_then_consent_listed_counts_for_the_match(db, cache, old_still_listed):
    # Dan's answer to question 6: it returns under a new ID with different audio, so only its
    # date, time and duration can say what it is. §9.5 keeps them, and `here` winning in the
    # buckets must not lose them.
    here = Here(by_id={pid(3): frozenset({"rec-a"})})
    old = listed(3)
    first = run([old], here=here, consent_list=consent(3))
    assert first.rows[0].bucket == HERE
    record_compare(db, cache, first, consent=consent(3), listing={}, now=T0)
    remembered = cache.keyed_out()[pid(3)]
    assert (remembered.kind, remembered.start_at, remembered.duration_ms) == (
        BY_CONSENT,
        old.recorded_at,
        old.duration_ms,
    )
    twin = twin_of(old)
    later = run(
        [old, twin] if old_still_listed else [twin],
        here=here,
        consent_list=consent(3),
        listing=db.listing_rows(),
        keyed=cache.keyed_out(),
    )
    assert rekeyed(by_id(later)[pid(40)]) == (EXCLUDED, BY_REKEY, pid(3), False)
    record_compare(db, cache, later, consent=consent(3), listing=db.listing_rows(), now=T0)
    (flag,) = db.open_flags(("rekeyed",))
    assert flag.detail == {"of": "consent"}


def test_a_recording_here_with_the_word_counts_in_the_listing_that_first_shows_it(db, cache):
    # A word added to a title here: the late keep-out flag is the import area's, and the row is
    # here, but a new ID beside it is that recording all the same.
    here = Here(by_id={pid(5): frozenset({"rec-k"})})
    old = listed(5, "noarchive: synthetic")
    result = run([old, twin_of(old)], here=here)
    got = by_id(result)
    assert got[pid(5)].bucket == HERE
    assert rekeyed(got[pid(40)]) == (EXCLUDED, BY_REKEY, pid(5), False)
    record_compare(db, cache, result, consent=GOOD_CONSENT, listing={}, now=T0)
    assert cache.keyed_out()[pid(5)].kind == BY_TITLE
    (flag,) = db.open_flags(("rekeyed",))
    assert flag.detail == {"of": "kept-out"}


def test_a_recording_here_kept_out_by_an_earlier_title_counts_for_the_match(db, cache):
    # Kept out late, and the title has lost the word since: it stays kept out (§12.2).
    record_late_keep_out(db, "rec-k", [pid(5)], "noarchive", now=T0)
    here = Here(by_id={pid(5): frozenset({"rec-k"})})
    old = listed(5, "Synthetic, the word gone")
    first = run([old], here=here, listing=db.listing_rows())
    record_compare(db, cache, first, consent=GOOD_CONSENT, listing=db.listing_rows(), now=T0)
    assert cache.keyed_out()[pid(5)].kind == BY_WAS_TITLE
    later = run([twin_of(old)], here=here, listing=db.listing_rows(), keyed=cache.keyed_out())
    assert rekeyed(later.rows[0]) == (EXCLUDED, BY_REKEY, pid(5), False)


def test_a_recording_dan_kept_with_keep_it_here_does_not_count_for_the_match(db, cache):
    # Keep it here is Dan's decision about that recording: a new ID with its date comes in as
    # any other would. `kept_out_at` stays set after it (flags.py), so the keep is what's read.
    record_late_keep_out(db, "rec-k", [pid(5)], "noarchive", now=T0)
    assert keep_here(db, "rec-k", now=T0) == KEPT_HERE
    here = Here(by_id={pid(5): frozenset({"rec-k"})}, kept_here=db.kept_here_recordings())
    old = listed(5, "noarchive: synthetic")
    twin = twin_of(old)
    before = {pid(5): keyed(5, "title", start=old.recorded_at, duration=old.duration_ms)}
    result = run([old, twin], here=here, listing=db.listing_rows(), keyed=before)
    got = by_id(result)
    assert (got[pid(40)].bucket, got[pid(40)].rekey_of) == (WAITING, None)
    record_compare(db, cache, result, consent=GOOD_CONSENT, listing=db.listing_rows(), now=T0)
    assert pid(5) not in cache.keyed_out()
    assert db.open_flags(("rekeyed",)) == []
    # The consent list outranks Keep it here.
    listed_after = run(
        [old, twin], here=here, consent_list=consent(5), listing=db.listing_rows(), keyed=before
    )
    assert rekeyed(by_id(listed_after)[pid(40)]) == (EXCLUDED, BY_REKEY, pid(5), False)


def test_an_id_dan_released_does_not_count_for_the_match(db):
    db.set_listing(pid(6), first_seen=T0, kept_out_at=T0, kept_out_word="noarchive")
    db.set_listing(pid(6), released_at=T0 + timedelta(minutes=5))
    old = listed(6, "Synthetic, released")
    twin = twin_of(old, 46)
    result = run([old, twin], listing=db.listing_rows(), keyed={pid(6): keyed(6, "title")})
    assert {r.bucket for r in result.rows} == {WAITING}


def test_an_id_here_or_released_by_dan_is_never_a_rekey_candidate(db):
    # A recording here is here (§11.3's precedence); Release keeps a released ID released, so
    # the match never catches it again (§12.2).
    db.set_listing(pid(3), first_seen=T0 - timedelta(days=2))
    old = keyed(3)
    here = Here(by_id={pid(61): frozenset({"rec-z"})})
    mine = listed(61, start_at=old.start_at, duration_ms=old.duration_ms)
    result = run([mine], here=here, consent_list=consent(3), keyed={pid(3): old})
    assert result.rows[0].bucket == HERE
    db.set_listing(pid(60), first_seen=T0 - timedelta(days=2), rekey_of=pid(3))
    db.set_listing(pid(60), released_at=T0)
    db.set_listing(pid(62), first_seen=T0, kept_out_at=T0, kept_out_word="noarchive")
    db.set_listing(pid(62), released_at=T0 + timedelta(minutes=5))
    rows = [
        listed(n, start_at=old.start_at, duration_ms=old.duration_ms, title=f"Synthetic {n}")
        for n in (60, 62)
    ]
    result = run(rows, consent_list=consent(3), listing=db.listing_rows(), keyed={pid(3): old})
    assert {r.bucket for r in result.rows} == {WAITING}


def test_the_cache_keeps_excluded_ids_dates_until_30_days_absent(db, cache):
    # §9.5: a consent-listed or kept-out ID's date, time and duration, kept until it has been
    # absent for 30 days, for the re-key match.
    result = run([listed(3), listed(4, "noarchive x")], consent_list=consent(3))
    record_compare(db, cache, result, consent=consent(3), listing={}, now=T0)
    assert set(cache.keyed_out()) == {pid(3), pid(4)}
    assert cache.keyed_out()[pid(3)].start_at == START + timedelta(hours=3)
    cache.remember_keyed_out([], now=T0 + timedelta(days=29))
    assert set(cache.keyed_out()) == {pid(3), pid(4)}
    cache.remember_keyed_out([], now=T0 + timedelta(days=31))
    assert cache.keyed_out() == {}


def test_the_tick_purges_titles_the_consent_list_or_a_word_now_catches(db, cache):
    # The outline's P1: a title stored by a compare, of an ID consent-listed or matching an
    # enforced word since, never reaches the browser: the worker's tick nulls it.
    result = run([listed(1), listed(2, "Archive later"), listed(3)])
    record_compare(db, cache, result, consent=GOOD_CONSENT, listing={}, now=T0)
    assert all(r.title for r in cache.compare_rows())
    assert cache.purge_titles(consent=GOOD_CONSENT, words=WORDS) == 0
    assert cache.purge_titles(consent=consent(1), words=WORDS) == 1
    assert cache.purge_titles(consent=consent(1), words=(*WORDS, "archive")) == 1
    titles = {r.canonical_plaud_id: r.title for r in cache.compare_rows()}
    assert titles == {pid(1): None, pid(2): None, pid(3): "Synthetic meeting 3"}
    assert cache.purge_titles(consent=None, words=WORDS) == 1  # not read yet: a stop
    assert cache.purge_titles(consent=GOOD_CONSENT, words=WORDS) == 0


def test_title_allowed_fails_closed():
    assert title_allowed(pid(1), "A", consent=GOOD_CONSENT, words=WORDS)
    assert not title_allowed(pid(1), None, consent=GOOD_CONSENT, words=WORDS)
    assert not title_allowed(pid(1), "A", consent=None, words=WORDS)
    assert not title_allowed(pid(1), "A", consent=GOOD_CONSENT, words=None)
    assert not title_allowed("OF_" + pid(1).upper(), "A", consent=consent(1), words=WORDS)


def test_the_read_only_cache_never_writes(db, cache, tmp_path):
    assert Cache.read_only(tmp_path / "state") is None  # no listing has run yet
    result = run([listed(1)])
    record_compare(db, cache, result, consent=GOOD_CONSENT, listing={}, now=T0)
    reader = Cache.read_only(tmp_path / "state")
    assert [r.title for r in reader.compare_rows()] == ["Synthetic meeting 1"]
    assert reader.compare_at() == T0
    with pytest.raises(Exception, match="readonly"):
        reader.purge_titles(consent=None, words=WORDS)


def test_forget_clears_a_title_hash_the_listing_wrote_without_a_marker(
    writer, make_incoming, tmp_path
):
    # The phase-end row (Task 6's review): the listing writes a title hash for a recording here
    # that no reconcile has marked yet, so forget reads its Plaud IDs from its last revision.
    rec = writer.add(
        make_incoming(
            sources=(RawSource(kind="plaud", ref="of_" + pid(1), added_at=T0, payload=b"{}"),)
        )
    ).recording
    db = PlaudDB(writer.state)
    db.write_listing([ListingEntry(pid(1), "of_" + pid(1), title_hash("Week 4"), 1000)], now=T0)
    assert db.markers(rec.id) == {} and db.listing(pid(1)).title_sha256 is not None
    writer.state.forget(
        f"{writer.archive.path_for(rec.id).relative_to(writer.root)}/recording.json"
    )
    assert db.listing(pid(1)).title_sha256 is None


def test_here_comes_from_the_index_and_pending_files(writer, make_incoming):
    rec = writer.add(
        make_incoming(
            sources=(
                RawSource(kind="plaud", ref="OF_" + pid(1).upper(), added_at=T0, payload=b"{}"),
            )
        )
    ).recording
    db = PlaudDB(writer.state)
    here = here_from_archive(writer.index, writer.identity.uuid, writer.state, writer.archive, db)
    assert here.by_id == {pid(1): frozenset({rec.id})} and here.kept_here == frozenset()
    db.set_check(rec.id, kept_here_at=T0)  # Keep it here's mark
    here = here_from_archive(writer.index, writer.identity.uuid, writer.state, writer.archive, db)
    assert here.kept_here == frozenset({rec.id}) and here.kept_here_ids() == frozenset({pid(1)})
    # An index update still pending (a crash between a write and its index update): the file says.
    with sqlite3.connect(writer.index.path) as conn:
        conn.execute("DELETE FROM source_refs")
    writer.state.mark_index_pending(rec.id)
    here = here_from_archive(writer.index, writer.identity.uuid, writer.state, writer.archive, db)
    assert here.by_id == {pid(1): frozenset({rec.id})}
    writer.index.path.unlink()  # the index can't answer: never "nothing here"
    with pytest.raises(IndexUnavailable):
        here_from_archive(writer.index, writer.identity.uuid, writer.state, writer.archive, db)
