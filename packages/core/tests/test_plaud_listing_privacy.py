"""Privacy from the listing alone (stage 2b spec §13.1 cases 2 and 3, §12.2's "Too late"; area
A4): a title changed to a private one is tagged at the next run, even when its fetch fails; a
keep-out word added after the recording came in tags it and flags it once; a `private` Dan removed
stays removed through runs and reconciles; a `private` that can't be written is flagged at once.
Against the fake Plaud server's account, through the import. Every ID and title is synthetic."""

from datetime import timedelta

import pytest
from fake_plaud import server_error
from plaud_scene import T0
from recordings.archive import Archive
from recordings.locks import LockTimeout
from recordings.plaud.baseline import DROPPED, KeepOutWordsError, drop_word, enforce_word
from recordings.plaud.checks import (
    K_KEEP_OUT_WORDS,
    K_WORDS_APPLIED,
    KEPT_OUT_LATE,
    PRIVATE_UNWRITTEN,
)
from recordings.plaud.flags import keep_here
from recordings.plaud.listing_privacy import (
    OWED_TITLE,
    mark_words_applied,
    privacy_from_listing,
)
from recordings.plaud.privacy import title_sha256
from recordings.tagops import RemoveTags


def run(scene):
    """One run's privacy from the listing: list, then check every listed title here."""
    scene.now += timedelta(minutes=20)
    listing = scene.listing()
    return privacy_from_listing(scene.writer, scene.db, listing.rows, scene.here(), now=scene.now)


def tags(scene, rid: str) -> list[str]:
    return [t.tag for t in scene.writer.archive.load(rid).tags]


def remove_private(scene, rid: str) -> None:
    """Dan removes the automatic `private` in the app, confirming the recording (tagging part A)."""
    scene.writer.mutate(rid, RemoveTags(("private",), may_expose=frozenset({rid})))


def notes(scene) -> list[str]:
    return [
        e.detail.get("why")
        for e in scene.db.events_since(T0 - timedelta(days=1), ["retagged-private"])
    ]


def test_a_title_renamed_private_is_tagged_at_the_next_run_even_when_its_fetch_fails(scene):
    # Decision 1 (C5.01): the placeholder arrives untagged; renamed, `private` lands from the
    # listing alone, with no fetch at all.
    rec = scene.account.add("2026-11-02 14:05:31")
    rid = scene.import_(rec).recording_id
    assert tags(scene, rid) == []
    scene.account.rename(rec.plaud_id, "Private: synthetic one-to-one")
    scene.server.fault(lambda p, q: p.endswith(rec.plaud_id), server_error())
    before = len(scene.envelope_requests(rec))
    report = run(scene)
    assert report.tagged == (rid,) and report.checked == 1
    assert tags(scene, rid) == ["private"]
    assert notes(scene) == ["title changed"]
    assert len(scene.envelope_requests(rec)) == before  # no fetch was needed
    marker = scene.db.markers(rid)[rec.plaud_id]
    assert marker.title_sha256 == title_sha256("Private: synthetic one-to-one")


def test_a_removed_private_stays_removed_through_runs_and_reconciles(scene):
    # Decision 3 (C5.17): once judged, a title is never judged again; a rename to a title that
    # doesn't match moves the marker, and a reconcile of the old snapshot doesn't put it back.
    rec = scene.account.add("Private: synthetic catch-up")
    rid = scene.import_(rec).recording_id
    assert tags(scene, rid) == ["private"]
    remove_private(scene, rid)
    scene.account.rename(rec.plaud_id, "Synthetic lunch")
    for n in range(100):
        report = run(scene)
        assert report.tagged == () and report.checked == (1 if n == 0 else 0)
        if n % 25 == 0:
            scene.writer.reconcile_plaud(rid)  # a reindex in any process: the old snapshot again
    assert tags(scene, rid) == []
    # It comes back only when the title changes to one that matches, with its note.
    scene.account.rename(rec.plaud_id, "Private: synthetic catch-up, again")
    assert run(scene).tagged == (rid,)
    assert notes(scene) == ["title changed"]


def test_a_keep_out_word_after_it_came_in_tags_and_flags_it_once(scene):
    # Decision 2's "too late" (C5.03): `private` at once, one flag, and its ID kept out from now
    # on, so nothing of it is fetched again.
    rec = scene.account.add("Synthetic coffee chat")
    rid = scene.import_(rec).recording_id
    scene.account.rename(rec.plaud_id, "noarchive synthetic coffee chat")
    report = run(scene)
    assert (report.tagged, report.kept_out_late, report.flagged) == ((rid,), (rid,), (rid,))
    assert tags(scene, rid) == ["private"] and notes(scene) == ["kept out by title"]
    (flag,) = scene.db.open_flags()
    assert (flag.kind, flag.recording_id) == (KEPT_OUT_LATE, rid)
    assert scene.db.listing(rec.plaud_id).kept_out_word == "noarchive"
    fetched = len(scene.requested(rec))
    again = scene.import_(rec)  # never fetched again, even when its transcript appears
    assert again.outcome == "excluded" and len(scene.requested(rec)) == fetched
    assert run(scene).flagged == ()  # one flag per recording (P15)
    assert len(scene.db.open_flags()) == 1


def test_keep_it_here_then_a_later_title_with_the_word_doesnt_flag_it_again(scene):
    rec = scene.account.add("Synthetic retro")
    rid = scene.import_(rec).recording_id
    scene.account.rename(rec.plaud_id, "noarchive synthetic retro")
    run(scene)
    keep_here(scene.db, rid, now=scene.now)
    scene.account.rename(rec.plaud_id, "noarchive: synthetic retro, renamed")
    report = run(scene)
    assert report.kept_out_late == (rid,) and report.flagged == ()
    assert scene.db.open_flags() == [] and tags(scene, rid) == ["private"]


def test_a_private_that_cant_be_written_is_flagged_at_once_and_lands_after_a_reindex(scene):
    # §13.2: the marker stays, the next run tries again, Needs attention names it at once.
    import json

    rec = scene.account.add("Synthetic standup")
    rid = scene.import_(rec).recording_id
    path = scene.writer.archive.path_for(rid) / "recording.json"
    edited = json.loads(path.read_text(encoding="utf-8"))
    edited["title"] = "Synthetic standup, edited by hand"
    path.write_text(json.dumps(edited), encoding="utf-8")
    scene.account.rename(rec.plaud_id, "Private: synthetic standup")
    report = run(scene)
    assert report.unwritten == (rid,) and report.causes == {rid: "StaleFile"}
    (flag,) = scene.db.open_flags((PRIVATE_UNWRITTEN,))
    assert (flag.recording_id, flag.detail) == (rid, {"owed": OWED_TITLE, "cause": "StaleFile"})
    marker = scene.db.markers(rid)[rec.plaud_id]
    assert marker.title_sha256 == title_sha256("Synthetic standup")  # still due
    assert run(scene).unwritten == (rid,)  # retried, and the flag isn't doubled
    assert len(scene.db.open_flags((PRIVATE_UNWRITTEN,))) == 1
    scene.writer.reindex()
    assert run(scene).tagged == (rid,) and tags(scene, rid) == ["private"]


def test_a_title_owed_private_that_changes_back_resolves_its_flag(scene):
    # The flag holds the sync's ping while the `private` is owed (§16); renamed back before the
    # tag could land, nothing is owed, and the flag goes at the next run.
    import json

    rec = scene.account.add("Synthetic standup")
    rid = scene.import_(rec).recording_id
    path = scene.writer.archive.path_for(rid) / "recording.json"
    edited = json.loads(path.read_text(encoding="utf-8"))
    edited["title"] = "Synthetic standup, edited by hand"
    path.write_text(json.dumps(edited), encoding="utf-8")
    scene.account.rename(rec.plaud_id, "Private: synthetic standup")
    assert run(scene).unwritten == (rid,)
    scene.account.rename(rec.plaud_id, "Synthetic standup, renamed back")
    report = run(scene)
    assert report.unwritten == () and report.checked == 1
    assert scene.db.open_flags((PRIVATE_UNWRITTEN,)) == []
    assert tags(scene, rid) == []


NEW_WORD = "keepaway"
CAUGHT_LATER = "Keepaway: synthetic grant call"


def good_run(scene):
    """One run with a good listing: what `sync._one_listing` does around the compare."""
    report = run(scene)
    mark_words_applied(scene.db, report, now=scene.now)
    return report


def here_then_word_added(scene, title: str = CAUGHT_LATER):
    """A recording here, judged with the words in force, and then a new word its title starts with
    (as the worker's `enforce_word` records one at its next start)."""
    rec = scene.account.add(title)
    rid = scene.import_(rec).recording_id
    assert tags(scene, rid) == []
    assert good_run(scene).checked == 0
    enforce_word(scene.db, NEW_WORD, now=scene.now)
    return rec, rid


def test_a_new_word_tags_and_flags_a_recording_here_once(scene):
    # Dan's answer to question 5 (Phase 1's Q&A §9): the title never changed, so its hash is the
    # marker's, and a word added later used to skip it. New patterns already re-check what's here.
    rec, rid = here_then_word_added(scene)
    before = len(scene.envelope_requests(rec))
    report = good_run(scene)
    assert (report.tagged, report.kept_out_late, report.flagged) == ((rid,), (rid,), (rid,))
    assert tags(scene, rid) == ["private"] and notes(scene) == ["kept out by title"]
    (flag,) = scene.db.open_flags()
    assert (flag.kind, flag.recording_id) == (KEPT_OUT_LATE, rid)
    assert scene.db.listing(rec.plaud_id).kept_out_word == NEW_WORD
    assert len(scene.envelope_requests(rec)) == before  # no extra Plaud call
    again = good_run(scene)
    assert (again.checked, again.tagged, again.kept_out_late, again.flagged) == (0, (), (), ())
    assert len(scene.db.open_flags()) == 1


def test_the_note_says_kept_out_when_the_title_also_matches_a_private_pattern(scene):
    # The title didn't change, so "title changed" would be untrue.
    rec = scene.account.add("Synthetic standup")
    rid = scene.import_(rec).recording_id
    scene.account.rename(rec.plaud_id, "Private: synthetic standup")
    assert good_run(scene).tagged == (rid,)
    remove_private(scene, rid)
    enforce_word(scene.db, "private", now=scene.now)
    assert good_run(scene).tagged == (rid,)
    assert notes(scene) == ["title changed", "kept out by title"]


def test_with_no_record_the_words_in_force_are_seeded_and_nothing_is_rechecked(scene):
    # A title with no marker is judged against every word anyway, so the first run has nothing to
    # catch up on, and a `private` Dan removed stays removed.
    rec = scene.account.add("Synthetic retro")
    rid = scene.import_(rec).recording_id
    scene.account.rename(rec.plaud_id, "noarchive synthetic retro")
    assert run(scene).tagged == (rid,)
    remove_private(scene, rid)
    assert scene.db.get(K_WORDS_APPLIED) == ["noarchive"]
    scene.db.delete(K_WORDS_APPLIED)
    report = good_run(scene)
    assert (report.checked, report.tagged) == (0, ())
    assert tags(scene, rid) == [] and scene.db.get(K_WORDS_APPLIED) == ["noarchive"]


def test_an_applied_word_never_retags_what_dan_dealt_with(scene):
    # "Stays removed" (decision 3), and the applied set only grows, so dropping a word and adding
    # it back re-tags nothing.
    _, rid = here_then_word_added(scene)
    assert good_run(scene).tagged == (rid,)
    remove_private(scene, rid)
    assert good_run(scene).checked == 0
    assert drop_word(scene.db, NEW_WORD, configured="noarchive", now=scene.now) == DROPPED
    assert good_run(scene).checked == 0
    enforce_word(scene.db, NEW_WORD, now=scene.now)
    assert good_run(scene).checked == 0
    assert tags(scene, rid) == []


def test_an_applied_word_never_flags_what_dan_kept_here_again(scene):
    _, rid = here_then_word_added(scene)
    assert good_run(scene).flagged == (rid,)
    keep_here(scene.db, rid, now=scene.now)
    assert good_run(scene).checked == 0
    assert scene.db.open_flags() == [] and tags(scene, rid) == ["private"]


def test_a_second_word_on_a_title_an_applied_word_already_caught_is_not_news(scene):
    # The title was judged against a word that catches it, and Dan dealt with that.
    rec = scene.account.add("Synthetic retro")
    rid = scene.import_(rec).recording_id
    scene.account.rename(rec.plaud_id, "noarchive keepaway retro")
    assert good_run(scene).tagged == (rid,)
    remove_private(scene, rid)
    enforce_word(scene.db, "no-archive-keepaway", now=scene.now)
    assert good_run(scene).checked == 0
    assert tags(scene, rid) == []


def test_a_busy_write_is_tried_again_at_the_next_run(scene, monkeypatch):
    _, rid = here_then_word_added(scene)

    def busy(*args, **kwargs):
        raise LockTimeout(rid)

    with monkeypatch.context() as held:
        held.setattr(scene.writer, "mutate", busy)
        report = good_run(scene)
    assert report.busy == (rid,) and tags(scene, rid) == []
    assert NEW_WORD not in scene.db.get(K_WORDS_APPLIED)  # not marked: it would never be retried
    assert good_run(scene).tagged == (rid,)
    assert tags(scene, rid) == ["private"] and NEW_WORD in scene.db.get(K_WORDS_APPLIED)


def test_an_unwritten_tag_is_flagged_and_tried_again_after_a_reindex(scene):
    import json

    _, rid = here_then_word_added(scene)
    path = scene.writer.archive.path_for(rid) / "recording.json"
    edited = json.loads(path.read_text(encoding="utf-8"))
    edited["title"] = "Edited by hand, synthetic"
    path.write_text(json.dumps(edited), encoding="utf-8")
    report = good_run(scene)
    assert report.unwritten == (rid,) and NEW_WORD not in scene.db.get(K_WORDS_APPLIED)
    assert len(scene.db.open_flags((PRIVATE_UNWRITTEN,))) == 1
    scene.writer.reindex()
    assert good_run(scene).tagged == (rid,)
    assert tags(scene, rid) == ["private"] and NEW_WORD in scene.db.get(K_WORDS_APPLIED)


def test_words_that_cant_be_read_still_raise_and_seed_nothing(scene):
    rec = scene.account.add("Synthetic standup")
    scene.import_(rec)
    scene.db.put(K_KEEP_OUT_WORDS, "not a list", now=scene.now)
    with pytest.raises(KeepOutWordsError):
        run(scene)
    assert scene.db.get(K_WORDS_APPLIED) is None


def test_a_held_listing_leaves_a_new_word_unapplied_for_what_it_didnt_list(live):
    # A hold means recordings are missing from the listing: marking the word applied then would
    # skip them for good when they come back. Only a good listing marks it.
    recs = [live.account.add(f"{CAUGHT_LATER} {i}") for i in range(20)]
    live.catch_up_done()
    w = live.started()
    w.tick()
    assert len(live.here()) == 20
    enforce_word(live.db(), NEW_WORD, now=live.clock())
    missing = recs[:10]
    for rec in missing:
        live.account.remove(rec.plaud_id)
    live.clock.advance(minutes=20)
    w.tick()
    assert NEW_WORD not in live.db().get(K_WORDS_APPLIED)
    live.account.restore(missing)
    live.clock.advance(minutes=20)
    w.tick()
    here, archive = live.here(), Archive(live.root)
    assert {tuple(t.tag for t in archive.load(here[r.plaud_id]).tags) for r in recs} == {
        ("private",)
    }
    assert NEW_WORD in live.db().get(K_WORDS_APPLIED)


def test_a_title_that_doesnt_match_moves_the_marker_and_is_never_checked_again(scene):
    rec = scene.account.add("Synthetic planning")
    rid = scene.import_(rec).recording_id
    scene.account.rename(rec.plaud_id, "Synthetic planning, renamed")
    assert run(scene).checked == 1
    assert run(scene).checked == 0
    assert tags(scene, rid) == []
