"""The listing hold (stage 2b spec §11.4; area A3): measuring against the last good listing or the
archive, holding, Accept this listing and its refusal, a small shrink settling, a change of form
with nothing absent, and re-keyed IDs, through the fake Plaud server's account and
`compare.take_listing`. Every ID and title is synthetic."""

from datetime import UTC, datetime, timedelta

import pytest
from recordings.plaud.cache import Cache
from recordings.plaud.checks import (
    K_HOLD,
    K_LAST_GOOD_LISTING,
    K_SMALL_SHRINK,
    LISTED_GOOD,
    LISTED_HELD,
    PlaudDB,
)
from recordings.plaud.client import PlaudAuthError, PlaudClient
from recordings.plaud.compare import (
    BY_CONSENT,
    BY_REKEY,
    BY_WAS_TITLE,
    EXCLUDED,
    HELD,
    MISSING,
    REVIEW,
    TITLES_HIDDEN_IDS,
    WAITING,
    Here,
    take_listing,
)
from recordings.plaud.health import BLOCKER_PHRASES, sync_health
from recordings.plaud.hold import (
    ACCEPT_REFUSED,
    ACCEPTED,
    CODE_FIX,
    CONSENT_FIRST,
    HELD_IDS,
    HELD_MANY,
    NOTHING_HELD,
    Measure,
    accept,
    fix_first,
    measure_against,
)
from recordings.plaud.keepout import parse_consent
from recordings.sources.plaud import PlaudSource
from recordings.state import State

T0 = datetime(2026, 11, 2, 14, 20, tzinfo=UTC)
WORDS = ("noarchive",)


class Tokens:
    def access_token(self) -> str:
        return "synthetic-access-1"

    def refresh(self, rejected: str) -> str:
        raise PlaudAuthError()


class Scene:
    """The fake account, state.db, the cache, the archive's Plaud IDs (by hand) and a clock."""

    def __init__(self, plaud_server, tmp_path, *, here: int = 0, form: str = "of_") -> None:
        from recordings.init import init_archive

        init_archive(tmp_path / "archive", tmp_path / "state", writer_id="test")
        self.db = PlaudDB(State.open(tmp_path / "state"))
        self.cache = Cache(tmp_path / "state")
        self.account = plaud_server.account(form=form)
        self.now = T0
        self.source = PlaudSource(
            PlaudClient(Tokens(), api_base=plaud_server.api_url), clock=lambda: self.now
        )
        self.by_id: dict[str, frozenset[str]] = {}
        self.consent = parse_consent("- `" + "f" * 32 + "` — synthetic, never listed\n")
        for n, rec in enumerate(self.account.add_many(here)):
            self.by_id[rec.plaud_id] = frozenset({f"rec-{n:04d}"})

    @property
    def here(self) -> Here:
        return Here(by_id=dict(self.by_id), gone=self.db.gone_recordings())

    def consent_list(self, *ids: str) -> None:
        self.consent = parse_consent("".join(f"- `of_{i}` — synthetic\n" for i in ids))

    def run(self, *, sync: bool = True, later: timedelta = timedelta(minutes=20)):
        self.now += later
        listing = self.source.list_all()
        assert listing.complete
        return take_listing(
            self.db,
            self.cache,
            listing,
            here=self.here,
            consent=self.consent,
            words=WORDS,
            patterns=(),
            gate_open=True,
            sync=sync,
            now=self.now,
        )

    def outcome(self, **kw) -> str:
        return self.run(**kw)[2]


def blocked(db: PlaudDB) -> bool:
    return "hold" in sync_health(db.state, schedule_minutes=20).blockers


def test_a_good_listing_becomes_the_last_good_one(plaud_server, tmp_path):
    s = Scene(plaud_server, tmp_path, here=10)
    s.account.add_many(3)  # waiting
    result, measured, outcome = s.run()
    assert outcome == "good" and measured.against == "archive" and measured.overlap == 1.0
    last = s.db.get(K_LAST_GOOD_LISTING)
    assert (last["count"], last["census"], last["at"]) == (13, {"of_": 13}, s.now.isoformat())
    assert len(s.db.listed(LISTED_GOOD)) == 13
    assert result.equation["waiting"] == 3 and result.equation["here"] == 10


def test_the_first_listing_held_under_80_percent_of_the_archives_ids(plaud_server, tmp_path):
    # §11.4 step 1: with no last good listing, the archive's Plaud IDs stand in. The `of_`
    # lesson: Plaud's hex changes, and nothing matches.
    s = Scene(plaud_server, tmp_path, here=10)
    for rec in list(s.account.recordings)[:3]:
        s.account.rekey(rec.plaud_id)
    result, measured, outcome = s.run()
    assert outcome == "held" and measured.against == "archive" and measured.overlap == 0.7
    hold = s.db.get(K_HOLD)
    assert hold["reasons"] == ["overlap"] and hold["accept"] is True and hold["ids_changed"]
    assert hold["sentence"] == (
        "Plaud sync paused: only 70% of the archive's Plaud IDs are listed now. Check the "
        "runbook's steps, then Accept this listing."
    )
    assert blocked(s.db) and BLOCKER_PHRASES["hold"] == "held: see Status"
    assert s.db.get(K_LAST_GOOD_LISTING) is None
    assert result.titles_hidden == TITLES_HIDDEN_IDS
    assert {(r.bucket, r.state) for r in result.bucket(MISSING)} == {(MISSING, HELD)}
    assert all(r.title is None for r in s.cache.compare_rows())
    accepted = accept(s.db, consent=s.consent, now=s.now)
    assert (accepted.state, accepted.message, accepted.gone) == ("accepted", ACCEPTED, ())
    assert s.db.get(K_HOLD) is None and not blocked(s.db)
    assert len(s.db.listed(LISTED_GOOD)) == 10 and s.db.listed(LISTED_HELD) == {}
    assert s.outcome() == "good"


def test_an_unreadable_form_holds_and_accept_needs_a_code_fix(plaud_server, tmp_path):
    s = Scene(plaud_server, tmp_path, here=10)
    assert s.outcome() == "good"
    odd = s.account.add(other_id="synthetic-other-form-7")
    _, measured, outcome = s.run()
    assert outcome == "held" and measured.refused == CODE_FIX
    assert measured.run_phrase() == HELD_IDS
    assert s.db.get(K_HOLD)["accept"] is False
    refused = accept(s.db, consent=s.consent, now=s.now)
    assert (refused.state, refused.why) == ("refused", CODE_FIX)
    assert blocked(s.db)
    # A hold clears by itself when a later listing no longer meets its condition (a fix release,
    # or Plaud putting things back).
    s.account.recordings.remove(odd)
    assert s.outcome() == "good" and s.db.get(K_HOLD) is None and not blocked(s.db)


def test_a_small_shrink_settles_an_hour_later_on_the_same_absent_set(plaud_server, tmp_path):
    # §11.4 step 5 and §12.3 (C6.07): imports go on, and only the marking as gone waits.
    s = Scene(plaud_server, tmp_path, here=40)
    assert s.outcome() == "good"
    recs = list(s.account.recordings)
    gone = [s.by_id[r.plaud_id] for r in recs[:2]]
    for rec in recs[:2]:
        s.account.remove(rec.plaud_id)
    s.account.add()  # a new one still comes in
    result, measured, outcome = s.run()
    assert outcome == "small-shrink" and measured.run_phrase() == "waiting: Plaud lists 2 fewer"
    assert result.equation["waiting"] == 1 and not blocked(s.db)
    assert s.db.get(K_SMALL_SHRINK)["since"] == s.now.isoformat()
    assert s.outcome(later=timedelta(minutes=30)) == "small-shrink"  # not an hour yet
    assert s.db.gone_recordings() == frozenset()
    assert s.outcome(later=timedelta(minutes=31)) == "good"  # 61 minutes: settled
    assert s.db.gone_recordings() == frozenset().union(*gone)
    assert s.db.get(K_SMALL_SHRINK) is None
    events = s.db.events_since(T0, ["gone"])
    assert sorted(e.recording_id for e in events) == sorted(frozenset().union(*gone))
    result, _, outcome = s.run()
    assert outcome == "good" and result.archive["gone"] == 2


def test_a_small_shrinks_wait_starts_again_when_the_absent_set_changes(plaud_server, tmp_path):
    s = Scene(plaud_server, tmp_path, here=40)
    s.outcome()
    recs = list(s.account.recordings)
    s.account.remove(recs[0].plaud_id)
    s.outcome()
    first = s.db.get(K_SMALL_SHRINK)
    s.account.remove(recs[1].plaud_id)
    assert s.outcome(later=timedelta(minutes=50)) == "small-shrink"
    again = s.db.get(K_SMALL_SHRINK)
    assert len(again["absent"]) == 2 and again["since"] > first["since"]
    assert s.outcome(later=timedelta(minutes=50)) == "small-shrink"  # 50 minutes since the change
    s.account.recordings.extend(recs[:2])  # Plaud puts them back
    assert s.outcome() == "good" and s.db.get(K_SMALL_SHRINK) is None
    assert s.db.gone_recordings() == frozenset()


def test_a_large_shrink_holds_and_accept_marks_them_gone(plaud_server, tmp_path):
    # §11.4 steps 2 and 4: more than 5, or 5% of those here, newly absent.
    s = Scene(plaud_server, tmp_path, here=40)
    s.outcome()
    recs = list(s.account.recordings)
    for rec in recs[:6]:
        s.account.remove(rec.plaud_id)
    s.account.add_many(4)
    result, measured, outcome = s.run()
    assert outcome == "held" and measured.reasons == ("large-shrink",)
    assert measured.run_phrase() == HELD_MANY and not measured.ids_changed
    hold = s.db.get(K_HOLD)
    assert hold["sentence"] == (
        "Plaud now lists 6 fewer of the recordings here (and 4 look new). If you deleted them in "
        "Plaud, Accept this listing: they show as gone from Plaud."
    )
    assert result.titles_hidden is None  # a plain shrink hides no title
    assert {r.state for r in result.bucket(MISSING)} == {HELD} and not result.bucket(WAITING)
    accepted = accept(s.db, consent=s.consent, now=s.now)
    assert accepted.state == "accepted" and len(accepted.gone) == 6
    assert accepted.message == (
        "Accepted this listing: 6 recordings show as gone from Plaud; imports resume"
    )
    assert s.db.get(K_LAST_GOOD_LISTING)["count"] == 38
    result, _, outcome = s.run()
    assert outcome == "good" and result.equation["waiting"] == 4
    assert accept(s.db, consent=s.consent, now=s.now).message == NOTHING_HELD


def test_five_absent_of_forty_is_a_small_shrink_and_six_is_large(plaud_server, tmp_path):
    def m(absent: int, here: int) -> Measure:
        return Measure(
            "listing", frozenset(map(str, range(absent))), frozenset(), False, 0, 1, 0, here
        )

    assert not m(5, 40).large and m(6, 40).large
    assert not m(10, 200).large and m(11, 200).large  # 5% of 200 is 10


def test_a_change_of_form_with_nothing_absent_holds_nothing(plaud_server, tmp_path):
    # §11.4 step 2 (C5.34, C6.07): the 2026-09-25 change replayed with the same hex. Kept-out IDs
    # stay out, review marks and consent exclusions hold: every table is keyed by canonical ID.
    s = Scene(plaud_server, tmp_path, here=20, form="hex")
    consented = s.account.add()
    kept = s.account.add("noarchive: synthetic")
    review = s.account.add()
    s.consent_list(consented.plaud_id)
    assert s.outcome() == "good"
    s.db.set_listing(review.plaud_id, needs_review=True)
    kept.name = "Synthetic title without the word"
    s.account.flip()
    result, measured, outcome = s.run()
    assert outcome == "good" and measured.census_change and not measured.held
    last = s.db.get(K_LAST_GOOD_LISTING)
    assert last["census"] == {"of_": 23} and last["form_changed_at"] == s.now.isoformat()
    got = {r.canonical: r for r in result.rows}
    assert (got[consented.plaud_id].bucket, got[consented.plaud_id].excluded_by) == (
        EXCLUDED,
        BY_CONSENT,
    )
    assert got[kept.plaud_id].excluded_by == BY_WAS_TITLE
    assert (got[review.plaud_id].state, got[review.plaud_id].title_shown) == (REVIEW, False)
    assert s.db.listing(kept.plaud_id).listed_id == "of_" + kept.plaud_id
    s.account.flip()
    assert s.outcome() == "good"  # and back again


def test_a_change_of_form_with_a_recording_absent_holds_and_hides_titles(plaud_server, tmp_path):
    s = Scene(plaud_server, tmp_path, here=20, form="hex")
    s.outcome()
    s.account.remove(s.account.recordings[0].plaud_id)
    s.account.add()
    s.account.flip()
    result, measured, outcome = s.run()
    assert outcome == "held" and measured.reasons == ("census",)
    assert result.titles_hidden == TITLES_HIDDEN_IDS
    assert all(r.title is None for r in s.cache.compare_rows())
    assert s.db.get(K_HOLD)["sentence"] == (
        "Plaud sync paused: Plaud's IDs changed form, and 1 recording here vanished with them. "
        "Check the runbook's steps, then Accept this listing."
    )
    assert accept(s.db, consent=s.consent, now=s.now).state == "accepted"


def test_accept_is_refused_when_excluded_ids_vanish_while_plauds_ids_changed(
    plaud_server, tmp_path
):
    # §11.4 step 4 (C6.07): a consent-listed or kept-out recording could come back under an ID
    # neither the list nor the kept-out records can name.
    s = Scene(plaud_server, tmp_path, here=20, form="hex")
    consented = s.account.add()
    s.consent_list(consented.plaud_id)
    s.outcome()
    s.account.remove(consented.plaud_id)
    s.account.flip()
    _, measured, outcome = s.run()
    assert outcome == "held" and measured.refused == fix_first(1)
    assert s.db.get(K_HOLD)["refused"] == (
        "Fix this first: 1 consent-listed or kept-out ID vanished while Plaud's IDs changed "
        "(runbook)"
    )
    refused = accept(s.db, consent=s.consent, now=s.now)
    assert (refused.state, refused.message, refused.why) == (
        "refused",
        ACCEPT_REFUSED,
        fix_first(1),
    )
    stopped = parse_consent("")
    assert accept(s.db, consent=stopped, now=s.now).why == CONSENT_FIRST
    assert accept(s.db, consent=None, now=s.now).why == CONSENT_FIRST
    assert blocked(s.db) and s.db.get(K_HOLD) is not None


def test_an_excluded_id_plaud_simply_stops_listing_never_blocks_accept(plaud_server, tmp_path):
    # §11.4 step 4: "One that Plaud simply no longer lists, with neither signal, never blocks
    # Accept."
    s = Scene(plaud_server, tmp_path, here=40)
    consented = s.account.add()
    s.consent_list(consented.plaud_id)
    s.outcome()
    s.account.remove(consented.plaud_id)
    for rec in list(s.account.recordings)[:6]:
        s.account.remove(rec.plaud_id)
    _, measured, outcome = s.run()
    assert outcome == "held" and len(measured.excluded_absent) == 1 and measured.refused is None
    assert accept(s.db, consent=s.consent, now=s.now).state == "accepted"


def test_ten_percent_re_keyed_is_a_large_shrink_and_accept_imports_no_matched_id(
    plaud_server, tmp_path
):
    # §21's "some IDs re-keyed in the same form" (C5.30, C5.31): the hex changes with the audio
    # and times unchanged. Recordings here vanish (a large shrink); a consent-listed and a kept-out
    # one come back under new hex, and the re-key match keeps both out, before and after Accept.
    s = Scene(plaud_server, tmp_path, here=60)
    consented = s.account.add()
    kept = s.account.add("noarchive: synthetic")
    s.consent_list(consented.plaud_id)
    assert s.outcome() == "good"
    rekeyed = [s.account.rekey(r.plaud_id) for r in list(s.account.recordings)[:6]]
    new_consented = s.account.rekey(consented.plaud_id)
    kept.name = "Synthetic title, the word gone"
    new_kept = s.account.rekey(kept.plaud_id)
    result, measured, outcome = s.run()
    assert outcome == "held" and measured.reasons == ("large-shrink",)
    assert measured.overlap > 0.8 and measured.refused is None
    got = {r.canonical: r for r in result.rows}
    for new in (new_consented, new_kept):
        assert (got[new].bucket, got[new].excluded_by) == (EXCLUDED, BY_REKEY)
    assert {r.canonical_plaud_id: r.title for r in s.cache.compare_rows()}[new_consented] is None
    assert accept(s.db, consent=s.consent, now=s.now).state == "accepted"
    result, _, outcome = s.run()
    got = {r.canonical: r for r in result.rows}
    assert outcome == "good"
    assert {got[new].bucket for new in (new_consented, new_kept)} == {EXCLUDED}
    assert {got[new].bucket for new in rekeyed} == {WAITING}  # here again, by its audio, at import
    assert len(s.db.open_flags(("rekeyed",))) == 2


def test_a_compare_never_moves_the_last_good_listing_or_a_hold(plaud_server, tmp_path):
    # §11.6: "Compare never moves it." It writes plaud_listing, the cache and its result only.
    s = Scene(plaud_server, tmp_path, here=40)
    assert s.outcome(sync=False) == "good"
    assert s.db.get(K_LAST_GOOD_LISTING) is None and s.db.listed(LISTED_GOOD) == {}
    s.outcome()
    before = s.db.get(K_LAST_GOOD_LISTING)
    for rec in list(s.account.recordings)[:6]:
        s.account.remove(rec.plaud_id)
    result, _, outcome = s.run(sync=False)
    assert outcome == "held" and {r.state for r in result.bucket(MISSING)} <= {HELD}
    assert s.db.get(K_HOLD) is None and not blocked(s.db)
    assert s.db.get(K_LAST_GOOD_LISTING) == before


@pytest.mark.parametrize(
    ("forms", "last", "census"),
    [
        ({"a": "of_"}, {"a": "of_"}, False),
        ({"a": "hex"}, {"a": "of_"}, True),  # an ID in both, now in another form
        ({"a": "of_", "b": "hex"}, {"a": "of_"}, True),  # a form the last didn't have
        ({"a": "of_", "b": "of_"}, {"a": "of_", "c": "hex"}, False),  # shares aren't compared
    ],
)
def test_the_census_is_measured_over_shared_ids_and_new_forms(forms, last, census):
    m = measure_against(forms, last_good=last, here=Here(by_id={}), excluded=frozenset())
    assert m.census_change is census
