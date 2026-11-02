"""The Sync screen's and Status's readers of what Phase 2 writes (stage 2b spec §15.1, §15.2; area
A6), against a worker on the fake Plaud server (plaud_live.py): the equations and "All here", Not
here yet with its titles checked again (P13, C5.15's screen half), Kept out with Release, a hold
and its buttons, Gone and Parked, the re-key flags, a rebuild's review rows, and the sign-in. The
views are the web app's (`recordings_ui.plaud`), read the way it reads state.db and the cache.
Every ID, title and token is synthetic, and nothing reaches Plaud."""

from datetime import timedelta

from recordings.config import load_config
from recordings.plaud import hold, release
from recordings.plaud import sync as sync_module
from recordings.plaud.checks import K_SIGN_IN
from recordings.plaud.health import record_rate_limit
from recordings.plaud.keepout import read_consent
from recordings.plaud.when import utc_iso
from recordings_ui.plaud import (
    ALL_HERE_EXPLAIN,
    HELD_BUTTON,
    TITLES_HIDDEN_CONSENT,
    TITLES_HIDDEN_IDS,
    plaud_view_settings,
    recording_plaud,
    sync_panel,
    sync_view,
)

WORD = "noarchive"  # the template's keep-out word, the worker's default


def views(live):
    settings = plaud_view_settings(load_config(live.environ))
    now = live.clock()
    return sync_view(settings, now=now, can_request=None), sync_panel(settings, now=now)


def compare(w) -> None:
    """A Compare, as the worker's handler runs it: lists, classifies, imports nothing."""
    sync_module.run_sync(sync_module.worker_run(w), mode="compare")


def turn(w, live, minutes: float = 20) -> None:
    live.clock.advance(minutes=minutes)
    w.tick()


def scene(live):
    """Three ordinary recordings, one consent-listed and one whose title starts with the keep-out
    word; the gate open; the worker started and its first scheduled sync run."""
    ordinary = [live.account.add(f"Synthetic standup {i}") for i in range(3)]
    consented = live.account.add("Synthetic consent-listed talk")
    kept = live.account.add(f"{WORD} synthetic secret plan")
    live.consent_ids(consented.plaud_id)
    live.catch_up_done()
    w = live.started()
    w.tick()
    return w, ordinary, consented, kept


def no_title_in(rows, *titles) -> None:
    text = repr(rows)
    for title in titles:
        assert title not in text


def test_after_a_sync_the_screens_say_all_here_with_the_equations_and_kept_out(live):
    _, ordinary, consented, kept = scene(live)
    view, panel = views(live)
    assert view["listed"] is True and panel["listed"] is True
    eq = view["equation"]
    assert {k: eq[k] for k in ("listed", "here", "waiting", "excluded", "missing")} == {
        "listed": 5,
        "here": 3,
        "waiting": 0,
        "excluded": 2,
        "missing": 0,
    }
    assert eq["listing_at"] is not None
    assert view["archive"] == {"from_plaud": 3, "here": 3, "gone": 0, "absent": 0}
    assert view["consent"]["on_plaud"] == 1  # §12.1: the consent line, how many are on Plaud
    summary = view["summary"]
    assert summary["kind"] == "all-here" and summary["sentence"].startswith("All here · checked ")
    assert "next sync" in summary["sentence"] and summary["explain"] == ALL_HERE_EXPLAIN
    assert panel["summary"] == summary  # one summary, shared (§15)
    # Kept out: by ID, date, time and duration, never a title (§15.1 item 7, P13).
    kept_rows = {row["plaud_id"].removeprefix("of_"): row for row in view["kept_out"]}
    assert kept_rows[consented.plaud_id]["by"] == "consent"
    assert kept_rows[kept.plaud_id]["by"] == "title"
    assert all(row["release"] is False for row in kept_rows.values())
    assert all(row["date"] and row["duration"] for row in kept_rows.values())
    no_title_in(view["kept_out"], consented.name, kept.name)
    no_title_in(panel, consented.name, kept.name, *(r.name for r in ordinary))  # no title on Status
    assert panel["numbers"]["last_good_listing"].endswith("(5 listed)")
    assert panel["details"]["census"] == {"of_": 5}
    assert panel["details"]["runs"] and panel["details"]["runs"][0]["result"] == "ok"
    assert panel["numbers"]["sign_in"].startswith("works (last used ")


def test_not_here_yet_shows_titles_only_as_the_cache_allows_them_again(live):
    # §15.1 item 6, P13 and C5.15's screen half: a Compare lists what waits, with titles; a
    # keep-out word added since, or a consent stop the worker read since, hides them on the screen
    # even before the tick has purged the cache.
    w, *_ = scene(live)
    fresh = live.account.add("Synthetic planning")
    private = live.account.add("Private synthetic chat")  # auto_private_patterns' "^\s*private"
    compare(w)
    view, panel = views(live)
    rows = {row["plaud_id"].removeprefix("of_"): row for row in view["not_here"]}
    assert rows[fresh.plaud_id]["title"] == fresh.name
    assert rows[private.plaud_id]["arrives_private"] is True
    assert rows[fresh.plaud_id]["state"] == "imports at the next sync"
    assert view["import_all"] == {"count": 2, "review_more": 0}
    assert view["summary"]["sentence"].startswith("2 waiting · checked ")
    no_title_in(panel, fresh.name, private.name)
    # A word enforced since the compare: the screen hides the title it starts at once.
    live.db().put("keep_out_words", [WORD, "synthetic"], now=live.clock())
    view, _ = views(live)
    rows = {row["plaud_id"].removeprefix("of_"): row for row in view["not_here"]}
    assert rows[fresh.plaud_id]["title"] is None
    assert rows[private.plaud_id]["title"] == private.name
    live.db().put("keep_out_words", [WORD], now=live.clock())
    # A consent stop the worker has read: no title at all, and the screen says why.
    live.consent_ids(extra="- `of_" + "a" * 31 + "` — a typo\n")
    w.tick()
    view, _ = views(live)
    assert view["titles_hidden"] == TITLES_HIDDEN_CONSENT
    assert [row["title"] for row in view["not_here"]] == [None, None]
    assert view["consent"]["on_plaud"] is None and view["consent"]["stop"]


def test_a_hold_disables_sync_now_and_the_imports_then_accept_shows_them_gone(live):
    # §11.4 and §15.1: a large shrink holds; Sync now and the imports are off with the hold's
    # reason (the worker refuses them anyway); titles hide while Plaud's IDs look changed;
    # Status needs attention; Accept marks the vanished recordings gone from Plaud.
    for i in range(5):
        live.account.add(f"Synthetic extra {i}")
    w, *_ = scene(live)
    here = live.here()
    assert len(here) == 8
    vanished = [r for r in live.account.recordings if r.plaud_id in here][:6]
    for rec in vanished:
        live.account.remove(rec.plaud_id)
    waiting = live.account.add("Synthetic after the hold")
    turn(w, live)
    view, panel = views(live)
    assert view["hold"] is not None and view["hold"]["accept"] is True
    assert view["hold"]["sentence"].startswith("Plaud sync paused")
    assert view["buttons"]["sync"] == HELD_BUTTON and view["buttons"]["import"] == HELD_BUTTON
    assert view["buttons"]["compare"] is None
    assert view["titles_hidden"] == TITLES_HIDDEN_IDS
    assert all(row["title"] is None for row in view["not_here"])
    no_title_in(view, waiting.name)
    assert panel["hold"] == view["hold"]
    assert any(line.startswith("Plaud sync is held since ") for line in panel["attention"])
    db = live.db()
    accepted = hold.accept(db, consent=read_consent(live.consent_path), now=live.clock())
    assert accepted.state == "accepted"
    view, panel = views(live)
    assert view["hold"] is None and view["buttons"]["sync"] is None
    gone = {row["recording_id"] for row in view["gone"]["rows"]}
    assert gone == {here[r.plaud_id] for r in vanished}
    assert view["gone"]["last_7_days"] == 6 and panel["numbers"]["gone"] == 6
    rid = here[vanished[0].plaud_id]
    shown = recording_plaud(plaud_view_settings(load_config(live.environ)), rid, now=live.clock())
    assert shown["gone_since"] is not None


def test_a_title_that_loses_the_word_offers_release_and_a_released_row_says_so(live):
    # §12.2, §15.1 item 7: kept out by an earlier title, Release is offered; once released, the
    # row reads "Released · comes in at the next sync (…)" with Undo, until the next sync.
    w, _, _, kept = scene(live)
    live.account.rename(kept.plaud_id, "Synthetic plan, renamed")
    turn(w, live)
    view, _ = views(live)
    row = next(r for r in view["kept_out"] if r["plaud_id"].endswith(kept.plaud_id))
    assert (row["by"], row["release"], row["released"]) == ("was-title", True, None)
    no_title_in(view["kept_out"], "Synthetic plan, renamed", kept.name)
    release.release(live.db(), kept.plaud_id, now=live.clock())
    view, _ = views(live)
    row = next(r for r in view["kept_out"] if r["plaud_id"].endswith(kept.plaud_id))
    assert row["release"] is False
    assert row["released"] == f"Released · comes in at the next sync ({view['next_sync']})"
    turn(w, live)
    assert kept.plaud_id in live.here()  # the next sync brings it in


def test_a_re_keyed_consent_listed_recording_is_a_flag_a_kept_out_row_and_needs_attention(live):
    # §11.4 step 6, §15.1 items 2 and 7, §15.2: by Plaud ID, date and duration; no title.
    w, _, consented, _ = scene(live)
    new_id = live.account.rekey(consented.plaud_id)
    turn(w, live)
    view, panel = views(live)
    (flag,) = [f for f in view["flags"] if f["kind"] == "rekeyed"]
    assert flag["plaud_id"].endswith(new_id) and flag["recording_id"] is None
    assert flag["sentence"].startswith("May be a consent-listed recording back under a new")
    assert flag["date"] and flag["duration"] and flag["copy_id"]
    row = next(r for r in view["kept_out"] if r["plaud_id"].endswith(new_id))
    assert (row["by"], row["release"]) == ("rekeyed", True)
    assert any(new_id in line and "new Plaud ID" in line for line in panel["attention"])
    no_title_in((view["flags"], view["kept_out"], panel), consented.name)


def test_a_re_keyed_match_kept_out_by_a_title_since_still_asks_with_no_title(live):
    # A security review of the fix that made a re-keyed match's Release ask with no title: one
    # whose title took the keep-out word and then lost it reads "was kept out by title" in the
    # compare, but it may still be a consent-listed recording, so its row is the re-keyed one,
    # whose Release asks with no title (SyncPage draws that question from `by`).
    w, _, consented, _ = scene(live)
    new_id = live.account.rekey(consented.plaud_id)
    turn(w, live)
    live.account.rename(new_id, f"{WORD} synthetic, renamed")
    turn(w, live)
    live.account.rename(new_id, "Synthetic, renamed again")
    turn(w, live)
    listed = live.db().listing(new_id)
    assert listed.kept_out_at is not None and listed.rekey_of is not None
    view, _ = views(live)
    row = next(r for r in view["kept_out"] if r["plaud_id"].endswith(new_id))
    assert (row["by"], row["release"]) == ("rekeyed", True)
    no_title_in(view["kept_out"], "Synthetic, renamed again", consented.name)


def test_after_plaud_review_the_rows_need_review_without_titles_and_status_says_how_many(live):
    # §11.6, §15.2: "The sync's records were rebuilt: 2 recordings wait for your review".
    w, *_ = scene(live)
    first = live.account.add("Synthetic before the restore 1")
    live.account.add("Synthetic before the restore 2")
    compare(w)
    db = live.db()
    review = [pid for pid in db.listing_rows() if pid not in live.here()]
    review = [pid for pid in review if not db.listing(pid).kept_out_at]
    db.record_rebuilt(at=live.clock(), by="plaud review", review=review, now=live.clock())
    compare(w)
    view, panel = views(live)
    rows = [r for r in view["not_here"] if r["review"]]
    assert len(rows) == 2 and all(r["title"] is None for r in rows)
    assert view["import_all"]["review_more"] == 2
    no_title_in(view["not_here"], first.name)
    line = "The sync's records were rebuilt: 2 recordings wait for your review on the Sync screen."
    assert line in panel["attention"]


def test_parked_rows_come_from_plaud_listing_and_failing_counts_them_not(live):
    w, *_ = scene(live)
    stuck = live.account.add("Synthetic that fails")
    compare(w)
    live.db().set_listing(stuck.listed_id("of_"), parked_at=live.clock())
    compare(w)
    view, panel = views(live)
    assert view["parked"] == [{"id": stuck.listed_id("of_"), "kind": "plaud"}]
    assert panel["numbers"]["parked"] == 1
    row = next(r for r in view["not_here"] if r["plaud_id"] == stuck.listed_id("of_"))
    assert row["state"] == "parked" and row["park"] is False


def test_a_refused_sign_in_shows_on_status_and_needs_attention(live):
    # §10.3, §15.2, C4: a revoked token and a refused refresh: the summary, the number, and the
    # line in Needs attention, with what to do at the Mac.
    w, *_ = scene(live)
    live.fake.revoke()
    live.fake.refuse_refresh()
    turn(w, live)
    view, panel = views(live)
    assert view["summary"] == {
        "kind": "problem",
        "sentence": "Sign-in needed",
        "explain": None,
        "see_status": True,
    }
    assert panel["numbers"]["sign_in"].startswith("needed since ")
    (line,) = [a for a in panel["attention"] if a.startswith("Plaud sign-in needed since ")]
    assert "nothing is lost" in line and "`make plaud-sign-in`" in line


def test_a_sign_in_expiring_within_7_days_needs_attention(live):
    # BD3: the refresh token's own expiry when there is one, else the access token's.
    scene(live)
    db, now = live.db(), live.clock()
    state = {"ok_at": utc_iso(now), "expires_at": utc_iso(now + timedelta(hours=1))}
    db.put(K_SIGN_IN, {**state, "refresh_token": True, "refresh_expires_at": None}, now=now)
    assert not [a for a in views(live)[1]["attention"] if "sign-in expires" in a]
    soon = utc_iso(now + timedelta(days=3))
    db.put(K_SIGN_IN, {**state, "refresh_token": True, "refresh_expires_at": soon}, now=now)
    _, panel = views(live)
    assert [a for a in panel["attention"] if a.startswith("The Plaud sign-in expires ")]
    assert "refresh token expires " in panel["numbers"]["sign_in"]
    db.put(K_SIGN_IN, {**state, "refresh_token": False}, now=now)
    _, panel = views(live)
    assert [a for a in panel["attention"] if a.startswith("The Plaud sign-in expires ")]
    assert panel["numbers"]["sign_in"].endswith("no refresh token")


def test_a_rate_limits_wait_turns_sync_now_and_the_imports_off_until_it_ends(live):
    # A5: a person's sync or import is refused while the wait runs; the buttons say until when.
    scene(live)
    record_rate_limit(live.db(), live.clock() + timedelta(minutes=30), now=live.clock())
    view, _ = views(live)
    assert view["buttons"]["sync"].startswith("Rate limited until ")
    assert view["buttons"]["compare"] is None
    live.clock.advance(minutes=31)
    assert views(live)[0]["buttons"]["sync"] is None


def test_a_small_shrink_is_a_plain_line_on_status(live):
    # §11.4 step 5: a few recordings vanish: no hold, Status says it is waiting to agree.
    w, ordinary, *_ = scene(live)
    live.account.remove(ordinary[0].plaud_id)
    turn(w, live)
    view, panel = views(live)
    assert view["hold"] is None
    line = "Plaud now lists 1 fewer: waiting for the next listing to agree."
    assert line in panel["conditions"]
