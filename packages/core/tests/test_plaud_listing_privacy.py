"""Privacy from the listing alone (stage 2b spec §13.1 cases 2 and 3, §12.2's "Too late"; area
A4): a title changed to a private one is tagged at the next run, even when its fetch fails; a
keep-out word added after the recording came in tags it and flags it once; a `private` Dan removed
stays removed through runs and reconciles; a `private` that can't be written is flagged at once.
Against the fake Plaud server's account, through the import. Every ID and title is synthetic."""

from datetime import timedelta

from fake_plaud import server_error
from plaud_scene import T0
from recordings.plaud.checks import KEPT_OUT_LATE, PRIVATE_UNWRITTEN
from recordings.plaud.flags import keep_here
from recordings.plaud.listing_privacy import OWED_TITLE, privacy_from_listing
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


def test_a_title_that_doesnt_match_moves_the_marker_and_is_never_checked_again(scene):
    rec = scene.account.add("Synthetic planning")
    rid = scene.import_(rec).recording_id
    scene.account.rename(rec.plaud_id, "Synthetic planning, renamed")
    assert run(scene).checked == 1
    assert run(scene).checked == 0
    assert tags(scene, rid) == []
