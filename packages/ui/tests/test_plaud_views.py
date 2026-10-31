"""The Plaud view model (2b spec §15, §22; phase 1 plan, Task 19): what the Sync screen, Status's
sync panel, the Library's arrivals cue and Details read, from synthetic state.db rows written
through PlaudDB and the flags module, as the worker writes them. Phase 1 reads its own data only:
what a later phase writes stays empty until that phase brings its reader."""

import json
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from recordings.models import TagRef
from recordings.plaud.checks import (
    K_CATCH_UP_DONE,
    K_CONSENT,
    K_KEEP_OUT_WORDS,
    K_LAST_PING,
    NO_SYNC_YET,
)
from recordings.plaud.flags import SENTENCES, record_late_keep_out
from recordings.plaud.gate import GATE_FIRST
from recordings.plaud.health import RunOutcome, open_blocker, record_run, record_turn
from recordings.plaud.keepout import typo_phrase
from recordings.plaud.requests import NOT_YET
from recordings.plaud.runlock import plaud_run
from recordings.plaud.settings import BAD_WORD
from recordings.tagops import RemoveTags
from recordings.worker import HEARTBEAT_KEY, Progress, heartbeat_once
from recordings.writer import Incoming
from recordings_ui.plaud import (
    DEMO_NOTE,
    DEMO_STATUS,
    FULL_UNKNOWN,
    NO_GOOD_LISTING,
    NO_SYNC,
    PING_FAILING,
    WHY_DETAILS,
    arrivals,
    demo_view,
    demo_view_settings,
    recording_plaud,
    sync_panel,
    sync_view,
)
from recordings_ui.settings import from_env
from recordings_ui.status import status_view
from recordings_ui.views import recording_view

REPO = Path(__file__).resolve().parents[3]
DEMO_FIXTURE = REPO / "demo" / "plaud-sync.json"
T0 = datetime(2026, 10, 23, 14, 20, tzinfo=UTC)
ZERO = {"new": 0, "private": 0, "updated": 0, "kept_out_late": 0, "consent_late": 0}
# What recordings_ui must never load (test_plaud_imports.py's FORBIDDEN).
FORBIDDEN = (
    "recordings.plaud.client",
    "recordings.plaud.tokens",
    "recordings.plaud.sync",
    "recordings.sources.plaud",
)


def test_before_the_catch_up_the_summary_is_the_gate_said_once(plaud_scene):
    # why: P12. The gate stays closed in Phase 1: the summary says so, and Status's conditions
    # don't say it again under it.
    s = plaud_scene
    view = sync_view(s.settings, now=T0, can_request=None)
    assert view["summary"] == {
        "kind": "problem",
        "sentence": GATE_FIRST,
        "explain": None,
        "see_status": True,
    }
    assert (view["gate"], view["next_sync"]) == (GATE_FIRST, None)
    panel = sync_panel(s.settings, now=T0)
    assert panel["summary"] == view["summary"]  # one summary, shared (§15)
    assert f"{GATE_FIRST}." not in panel["conditions"]
    # A sync nothing stops has no good listing yet: "All here" needs Phase 2's listing.
    s.open_gate()
    view = sync_view(s.settings, now=T0, can_request=None)
    assert view["summary"]["sentence"] == NO_GOOD_LISTING and view["gate"] is None


def test_the_views_carry_what_the_worker_handles_and_each_button_its_own_reason(plaud_scene):
    # why: Phase 1's worker handles Keep it here alone. Every view says so, and the UI disables
    # every other button with not_yet, so no press comes back "error: NotImplementedError".
    s = plaud_scene
    rid = s.add(1, "Week 1")
    view = sync_view(s.settings, now=T0, can_request=None)
    for shown in (view, sync_panel(s.settings, now=T0), recording_plaud(s.settings, rid, now=T0)):
        assert (shown["handled"], shown["not_yet"]) == (["keep-here"], NOT_YET)
    assert view["available"] is True and view["reason"] is None
    # The gate holds back every kind but Compare (§11.6).
    assert view["buttons"] == {
        "sync": GATE_FIRST,
        "compare": None,
        "full": GATE_FIRST,
        "import": GATE_FIRST,
    }
    why = "there's no config.toml, so this app only reads the archive"
    view = sync_view(s.settings, now=T0, can_request=why)
    assert view["available"] is False and view["buttons"] == dict.fromkeys(view["buttons"], why)
    # A keep_out_word that won't read idles the sync, whatever the gate says.
    s.open_gate()
    s.keep_out_word_ok = False
    view = sync_view(s.settings, now=T0, can_request=None)
    assert view["buttons"] == {
        "sync": BAD_WORD,
        "compare": None,
        "full": BAD_WORD,
        "import": BAD_WORD,
    }


def test_flag_and_updated_rows_never_name_a_flagged_recording(plaud_scene):
    # why: P13. A recording kept out by title after it came in is shown by ID alone: in its flag,
    # and in Updated on Plaud, whose rows otherwise name a recording by its title here.
    s = plaud_scene
    s.add(5, "Flagged synthetic 5")
    rid = s.kept_out_late(5)
    other = s.add(6, "Week 6")
    s.db.add_event(
        "retagged-private",
        now=T0,
        recording_id=other,
        plaud_id=s.plaud_id(6),
        detail={"why": "title changed"},
    )
    view = sync_view(s.settings, now=T0, can_request=None)
    (flag,) = view["flags"]
    assert flag["recording_id"] == rid and flag["keep_here"] is True
    assert flag["sentence"] == SENTENCES["kept-out-late"]
    assert flag["copy_id"] == f"- `{s.plaud_id(5)}` —"
    updated = {row["recording_id"]: row for row in view["updated"]}
    assert (updated[rid]["title"], updated[rid]["parts"]) == (None, ["re-tagged private"])
    assert updated[other]["title"] == "Week 6"
    assert "synthetic" not in json.dumps(view).lower()


def test_a_title_stays_hidden_after_keep_it_here_resolves_the_flag(plaud_scene):
    # why: P13. Keep it here closes the flag, but the recording is still kept out by title: its
    # title must not reach Updated on Plaud once nothing is open (found building Task 19).
    from recordings.plaud.flags import keep_here

    s = plaud_scene
    s.add(7, "Kept synthetic 7")
    rid = s.kept_out_late(7)
    keep_here(s.db, rid, now=T0)
    view = sync_view(s.settings, now=T0, can_request=None)
    assert view["flags"] == []
    assert [(row["recording_id"], row["title"]) for row in view["updated"]] == [(rid, None)]
    assert "synthetic" not in json.dumps(view).lower()


def test_the_not_pinged_line_names_the_cause_from_sync_health(plaud_scene):
    # C6.06: an error shows from the first failed run, though Healthchecks emails only about 2
    # hours later, and the not-pinged line names what stopped the ping, from sync_health().
    s = plaud_scene
    s.open_gate()
    pinged = T0 - timedelta(minutes=40)
    s.db.put(K_LAST_PING, {"at": pinged.isoformat(), "ok": True, "status": 200}, now=pinged)
    failed = T0 - timedelta(minutes=20)
    outcome = RunOutcome(
        listing="none", attempts=0, successes=0, disk_refused=False, detail="Plaud unavailable"
    )
    record_run(s.db, outcome, now=failed)
    # The turn's own result, as Task 13 records a turn that ran.
    record_turn(s.db, "ran", now=failed, detail={"result": "Plaud unavailable"})
    panel = sync_panel(s.settings, now=T0)
    assert panel["summary"]["sentence"] == "The sync is failing (since 14:00)"
    assert panel["failing"] == "Plaud sync failing since 14:00 (Plaud unavailable)."
    assert panel["not_pinged"] == (
        "The sync's Healthchecks check isn't being pinged since 13:40: the sync is failing "
        "(since 14:00). It emails you about 2 hours after that."
    )
    assert panel["numbers"]["healthchecks"] == "last pinged 13:40"
    # One schedule interval plus 10 minutes after the last ping, and not before.
    assert sync_panel(s.settings, now=T0 - timedelta(minutes=11))["not_pinged"] is None


def test_the_cause_shows_its_times_in_the_default_zone(plaud_scene):
    # why: SyncHealth.cause shows times in the zone it is given (Task 11), and the views give it
    # the default zone they show every other time in: never UTC by accident.
    s = plaud_scene
    s.open_gate()
    outcome = RunOutcome(
        listing="bad", attempts=0, successes=0, disk_refused=False, detail="Plaud unavailable"
    )
    record_run(s.db, outcome, now=T0)  # 14:20 UTC, 07:20 in Vancouver
    pacific = replace(s.settings, timezone_name="America/Vancouver")
    view = sync_view(pacific, now=T0, can_request=None)
    assert view["summary"]["sentence"] == "The sync is failing (since 07:20)"
    assert sync_panel(pacific, now=T0)["summary"] == view["summary"]


def test_the_schedule_off_for_24_hours_after_the_catch_up_needs_attention(plaud_scene):
    # C6.14. Off before the catch-up is plain; after it, once off for 24 hours, Needs attention.
    s = plaud_scene
    s.schedule_minutes = 0
    before = sync_panel(s.settings, now=T0)
    assert before["summary"]["sentence"] == "Scheduled sync is off"
    assert "Scheduled sync is off." not in before["conditions"]  # the summary says it (P12)
    assert not [a for a in before["attention"] if a.startswith("Scheduled sync")]
    done = T0 - timedelta(hours=30)
    s.open_gate(at=done)
    early = sync_panel(s.settings, now=done + timedelta(hours=23))
    assert "Scheduled sync is off since 2026-10-22 08:20." in early["conditions"]
    assert not [a for a in early["attention"] if a.startswith("Scheduled sync")]
    late = sync_panel(s.settings, now=T0)
    assert [a for a in late["attention"] if a.startswith("Scheduled sync")] == [
        (
            "Scheduled sync has been off since 2026-10-22 08:20: set [plaud] schedule_minutes to "
            "turn it back on, or pause the sync's Healthchecks check (runbook)."
        )
    ]


def test_each_needs_attention_entry_phase_1_can_show(plaud_scene):
    s = plaud_scene
    s.schedule_minutes = 0
    s.open_gate(at=T0 - timedelta(hours=30))
    db = s.db
    # What the worker read, as Task 13 writes it: the count, never the IDs.
    consent = {"count": 3, "stop": typo_phrase([14, 22]), "read_at": T0.isoformat()}
    db.put(K_CONSENT, consent, now=T0)
    open_blocker(db, "consent", now=T0 - timedelta(minutes=5))
    a, b, c = s.add(11, "Week 11"), s.add(12, "Week 12"), s.add(13, "Week 13")
    db.add_event("consent-late", now=T0, recording_id=a, plaud_id=s.plaud_id(11), open_flag=True)
    s.kept_out_late(12)
    db.add_event("private-unwritten", now=T0, recording_id=c, open_flag=True)
    beat = T0 - timedelta(minutes=10)
    heartbeat_once(s.writer.state, Progress(phase="idle", counter=1, at=beat), now=beat)
    failed_since = T0 - timedelta(hours=2, minutes=1)
    db.put(
        K_LAST_PING,
        {
            "at": (T0 - timedelta(minutes=5)).isoformat(),
            "ok": False,
            "status": 404,
            "failed_since": failed_since.isoformat(),
        },
        now=T0,
    )
    with plaud_run(s.writer.state, "plaud-sync", timeout=1.0, now=T0 - timedelta(hours=3)):
        attention = sync_panel(s.settings, now=T0)["attention"]
    for line in (
        (
            "Plaud imports stopped: the consent list has 2 lines that look like IDs but can't be "
            "read (lines 14 and 22)."
        ),
        f"Recording {a} is on the consent list, but in the archive. Take it out by hand (runbook).",
        (
            f"Recording {b} was kept out by title after it came in. Take it out by hand (runbook), "
            "or Keep it here."
        ),
        (
            f"Recording {c} should be private, but its file was edited outside the app: run "
            "`recordings reindex`."
        ),
        (
            "The Plaud worker is silent (last reported 14:10): check that the worker container is "
            "running."
        ),
        "A sync has run since 11:20: it may be stuck.",
        PING_FAILING,
    ):
        assert line in attention, line
    assert [x for x in attention if x.startswith("Scheduled sync has been off since")]
    assert sync_panel(s.settings, now=T0)["numbers"]["consent"]["ids"] == 3
    # Sync everything may hold the lock for 12 hours before it may be stuck.
    with plaud_run(s.writer.state, "plaud-full", timeout=1.0, now=T0 - timedelta(hours=3)):
        attention = sync_panel(s.settings, now=T0)["attention"]
    assert not [x for x in attention if x.endswith("it may be stuck.")]


def test_the_worker_line_is_in_plain_words_and_a_failed_start_says_so(plaud_scene):
    # why: §15.2's "last reported 14:21 (checking, …)", never the worker's own phase names; and a
    # worker whose start keeps failing never reads as alive (Task 13 writes the phase `failed`).
    s = plaud_scene
    beat = T0 - timedelta(seconds=20)
    for phase, words in (
        ("idle", "idle"),
        ("turn", "syncing"),
        ("request", "answering a request"),
        ("consent", "reading the consent list"),
    ):
        heartbeat_once(s.writer.state, Progress(phase=phase, counter=1, at=beat), now=beat)
        panel = sync_panel(s.settings, now=T0)
        assert panel["numbers"]["worker"] == f"last reported 14:19 ({words})", phase
    failed = {
        "at": beat.isoformat(),
        "phase": "failed",
        "error": "OSError",
        "progress_counter": 0,
        "progress_at": beat.isoformat(),
    }
    s.writer.state.set_meta(HEARTBEAT_KEY, json.dumps(failed))
    panel = sync_panel(s.settings, now=T0)
    assert panel["numbers"]["worker"] == "last reported 14:19 (couldn't start: `OSError`)"
    assert (
        "The Plaud worker couldn't start (`OSError`): see `rc logs worker` (runbook)."
        in panel["attention"]
    )
    # A class name only: anything else there shows as no particular class (§15.6).
    s.writer.state.set_meta(HEARTBEAT_KEY, json.dumps({**failed, "error": "no such file /x"}))
    assert "(couldn't start: `Exception`)" in sync_panel(s.settings, now=T0)["numbers"]["worker"]


def test_the_patterns_pass_at_start_shows_how_far_it_has_checked(plaud_scene):
    # why: §15.2's "The Plaud worker last reported 14:21 (checking, 412 of 1,234)". Task 13 runs
    # the patterns pass as `checking`, its heartbeat carrying `done` and `total`.
    s = plaud_scene
    beat = T0 - timedelta(seconds=20)
    checking = {
        "at": beat.isoformat(),
        "phase": "checking",
        "done": 412,
        "total": 1234,
        "progress_counter": 412,
        "progress_at": beat.isoformat(),
    }
    s.writer.state.set_meta(HEARTBEAT_KEY, json.dumps(checking))
    worker = sync_panel(s.settings, now=T0)["numbers"]["worker"]
    assert worker == "last reported 14:19 (checking, 412 of 1,234)"
    s.writer.state.set_meta(HEARTBEAT_KEY, json.dumps({**checking, "total": None}))
    assert sync_panel(s.settings, now=T0)["numbers"]["worker"] == "last reported 14:19 (checking)"


def test_a_late_consent_pass_that_failed_names_its_class_and_a_remedy_that_helps(plaud_scene):
    # why: a whole late consent pass that failed (a full or read-only disk, a busy state.db) can't
    # be mended by `recordings reindex`: Task 13's blocker carries the class, and the summary and
    # Needs attention say it, with the remedy that helps.
    s = plaud_scene
    stopped = (
        "Late consent listings stopped (OSError): check the archive disk, then the worker's log."
    )
    open_blocker(s.db, "private-unwritten", now=T0, detail={"error": "OSError"})
    panel = sync_panel(s.settings, now=T0)
    assert panel["summary"]["sentence"] == stopped.removesuffix(".")
    assert stopped in panel["attention"]
    assert sync_view(s.settings, now=T0, can_request=None)["summary"] == panel["summary"]
    # With no error (a flag is open), the summary stays the blocker's own line.
    open_blocker(s.db, "private-unwritten", now=T0, detail={})
    panel = sync_panel(s.settings, now=T0)
    assert panel["summary"]["sentence"] == "Should be private: run `recordings reindex`"
    assert not [a for a in panel["attention"] if a.startswith("Late consent listings stopped")]


def test_a_recording_the_patterns_pass_couldnt_check_needs_attention_by_id(plaud_scene):
    # why: spec §13.2, "Needs attention shows it at once, by ID": a recording the last patterns
    # pass couldn't check stays unchecked until a reconcile reaches it (Task 10's review).
    from recordings.plaud.checks import K_PATTERNS_PASS
    from recordings_ui.plaud import PATTERNS_UNCHECKED

    s = plaud_scene
    pass_ = {"version": 1, "attempted": 2, "at": T0.isoformat(), "checked": 3, "tagged": 0}
    s.db.put(K_PATTERNS_PASS, {**pass_, "unwritten": ["rec-a"], "busy": []}, now=T0)
    attention = sync_panel(s.settings, now=T0)["attention"]
    assert PATTERNS_UNCHECKED.format(id="rec-a") in attention
    s.db.put(K_PATTERNS_PASS, {**pass_, "unwritten": [], "busy": []}, now=T0)
    assert not [a for a in sync_panel(s.settings, now=T0)["attention"] if "patterns" in a]


def test_an_unwritten_flag_says_what_its_cause_needs(plaud_scene):
    # why: `recordings reindex` mends a file edited outside the app (StaleFile, InvalidFile), not
    # a disk or permissions fault: Task 8 records each flag's cause, and the sentences follow it.
    s = plaud_scene
    io, edited = s.add(31, "Week 31"), s.add(32, "Week 32")
    s.db.add_event(
        "private-unwritten",
        now=T0,
        recording_id=io,
        detail={"owed": "consent", "cause": "OSError"},
        open_flag=True,
    )
    s.db.add_event(
        "private-unwritten",
        now=T0,
        recording_id=edited,
        detail={"owed": "consent", "cause": "StaleFile"},
        open_flag=True,
    )
    rows = {
        f["recording_id"]: f["sentence"]
        for f in sync_view(s.settings, now=T0, can_request=None)["flags"]
    }
    assert rows[io] == (
        f"Recording {io} should be private, but its file couldn't be written (OSError): check the "
        "archive disk and its permissions."
    )
    assert rows[edited] == SENTENCES["private-unwritten"]
    attention = sync_panel(s.settings, now=T0)["attention"]
    assert rows[io] in attention  # Task 8's sentence names the recording itself
    assert (
        f"Recording {edited} should be private, but its file was edited outside the app: run "
        "`recordings reindex`."
    ) in attention
    assert recording_plaud(s.settings, io, now=T0)["flags"][0]["sentence"] == rows[io]


def test_before_any_listing_the_views_say_so_and_count_nothing(plaud_scene):
    # why: until Phase 2 writes a listing, nothing has been measured: the views say listed is
    # false, and the screens draw no counts from it.
    s = plaud_scene
    assert sync_view(s.settings, now=T0, can_request=None)["listed"] is False
    assert sync_panel(s.settings, now=T0)["listed"] is False


def test_what_a_later_phase_writes_stays_empty_in_phase_1(plaud_scene):
    # Phase 1 reads its own data only: each section a later phase fills keeps its empty value
    # until that phase brings its reader, and the screens draw it empty.
    s = plaud_scene
    s.open_gate()
    s.db.put(K_KEEP_OUT_WORDS, ["noarchive"], now=T0)
    view = sync_view(s.settings, now=T0, can_request=None)
    assert [view[k] for k in ("equation", "archive", "hold", "breaker", "titles_hidden")] == [
        None
    ] * 5
    assert [view["not_here"], view["kept_out"], view["parked"], view["gone"]["rows"]] == [[]] * 4
    assert view["full_estimate"] == FULL_UNKNOWN
    assert view["import_all"] == {"count": 0, "review_more": 0}
    panel = sync_panel(s.settings, now=T0)
    numbers, details = panel["numbers"], panel["details"]
    later = ("equation", "archive", "last_good_listing", "sweep", "sign_in", "full")
    assert [numbers[k] for k in later] == [None] * len(later)
    assert [numbers[k] for k in ("held", "gone", "failing", "parked")] == [0] * 4
    assert (details["runs"], details["census"], details["fields"]) == ([], {}, [])
    assert details["keep_out_words"] == ["noarchive"]  # Task 10 records the words enforced


def test_the_demo_view_is_the_hand_written_fixture_with_the_demo_notes(tmp_path):
    raw = json.loads(DEMO_FIXTURE.read_text(encoding="utf-8"))
    view = demo_view(DEMO_FIXTURE)
    sync, panel = view["sync"], view["panel"]
    # Every key is written, and none is misspelt.
    assert set(raw["sync"]) == set(sync) and set(raw["panel"]) == set(panel)
    assert (sync["demo"], sync["available"], sync["reason"]) == (True, False, DEMO_NOTE)
    assert sync["summary"]["kind"] == "demo" and sync["summary"]["sentence"] == DEMO_NOTE
    assert set(sync["buttons"].values()) == {DEMO_NOTE}
    assert (sync["handled"], sync["not_yet"], sync["gate"]) == (["keep-here"], NOT_YET, None)
    assert sync["listed"] is True and panel["listed"] is True  # the demo shows every part
    assert sync["not_here"] == [{**row, "park": False} for row in raw["sync"]["not_here"]]
    assert not [r for r in sync["kept_out"] if r["release"]]
    assert not [f for f in sync["flags"] if f["keep_here"]]
    assert panel["summary"]["sentence"] == DEMO_STATUS and panel["attention"] == []
    assert view["arrivals"] == raw["arrivals"]
    # Hand-written and synthetic: every Plaud ID is a small counter.
    ids = re.findall(r"of_[0-9a-f]+", DEMO_FIXTURE.read_text(encoding="utf-8"))
    assert ids and all(re.fullmatch(r"of_0{24}[0-9a-f]{8}", i) for i in ids)
    # Demo mode copies it beside the archive's copy, where the views read it (decision 23).
    canned = demo_view_settings(from_env({}, demo=True).archive)
    assert canned.demo_view.read_bytes() == DEMO_FIXTURE.read_bytes()
    assert sync_view(canned, now=T0, can_request="any reason") == sync
    assert sync_panel(canned, now=T0) == panel
    assert arrivals(canned, since=T0, now=T0) == view["arrivals"]
    assert recording_plaud(canned, "19620912T100000-0500_229ce3ea", now=T0) is None
    assert status_view(tmp_path, None, now=T0, plaud=canned)["sync"] == panel
    # A missing file is an empty canned view, never an error: the demo still starts.
    empty = demo_view(tmp_path / "missing.json")
    assert empty["sync"]["not_here"] == [] and empty["sync"]["buttons"]["compare"] == DEMO_NOTE


def test_the_details_lines(plaud_scene):
    s = plaud_scene
    s.open_gate()
    rid = s.add(10, "Week 10", tags=(TagRef(tag="private", by="auto"),))
    db = s.db
    db.add_event(
        "retagged-private",
        now=T0 - timedelta(days=1),
        recording_id=rid,
        plaud_id=s.plaud_id(10),
        detail={"why": "title changed"},
    )
    record_late_keep_out(db, rid, [s.plaud_id(10)], "noarchive", now=T0)
    lines = recording_plaud(s.settings, rid, now=T0)
    assert lines["plaud_ids"] == [{"id": s.plaud_id(10), "copy_id": f"- `{s.plaud_id(10)}` —"}]
    # When it was checked and when next, and gone from Plaud, come with the checks (Phase 3).
    assert (lines["checked"], lines["next_by"], lines["gone_since"]) == (None, None, None)
    assert lines["tagged_private"] == (
        "Tagged private by the sync on 2026-10-22: its Plaud title changed"
    )
    assert lines["removed_private"] is None
    (flag,) = lines["flags"]
    assert flag["kind"] == "kept-out-late" and flag["keep_here"] is True
    assert lines["resync"] == "Not checked: kept out"
    assert recording_view(s.writer.archive, rid, plaud=s.settings, now=T0)["plaud"] == lines
    # Decision 3, made visible once Dan removes it; the date is the removing revision's.
    s.writer.mutate(rid, RemoveTags(("private",), may_expose=frozenset({rid})))
    with closing(sqlite3.connect(s.writer.state.db_path)) as conn, conn:
        conn.execute(
            "UPDATE revisions SET written_at = ? WHERE id = (SELECT MAX(id) FROM revisions)",
            (T0.isoformat(),),
        )
    lines = recording_plaud(s.settings, rid, now=T0)
    assert lines["tagged_private"] is None
    assert lines["removed_private"] == (
        "Tagged private by the sync on 2026-10-22. You removed it on 2026-10-23. It comes back "
        "only if its Plaud title changes to a private one."
    )
    # With the gate closed, Re-sync says the gate's reason (§11.6).
    db.delete(K_CATCH_UP_DONE)
    assert recording_plaud(s.settings, rid, now=T0)["resync"] == GATE_FIRST
    # No Plaud source: no Plaud lines.
    media = s.tmp / "own.mp3"
    media.write_bytes(b"ID3 synthetic own audio")
    own = s.writer.add(
        Incoming(
            media=media,
            recorded_at=T0,
            timezone_name="UTC",
            time_source="metadata",
            title="Week 0",
            kind="audio",
            sources=(),
        )
    ).recording.id
    assert recording_plaud(s.settings, own, now=T0) is None
    assert recording_view(s.writer.archive, own, plaud=s.settings, now=T0)["plaud"] is None


def test_arrivals_count_events_since_and_flags_by_count_only(plaud_scene):
    s = plaud_scene
    db = s.db
    since = T0 - timedelta(hours=1)
    db.add_event(
        "imported", now=since - timedelta(minutes=1), recording_id="20261023T120000+0000_0000beef"
    )
    for i, kind in enumerate(
        ("imported", "imported", "arrived-private", "updated", "retagged-private")
    ):
        db.add_event(
            kind, now=T0 - timedelta(minutes=10), recording_id=f"20261023T14{i:02d}00+0000_0000beef"
        )
    a, b = s.add(21, "Week 21"), s.add(22, "Week 22")
    db.add_event("kept-out-late", now=T0, recording_id=a, plaud_id=s.plaud_id(21), open_flag=True)
    db.add_event("consent-late", now=T0, recording_id=b, plaud_id=s.plaud_id(22), open_flag=True)
    counts = arrivals(s.settings, since=since, now=T0)
    assert counts == {"new": 2, "private": 1, "updated": 2, "kept_out_late": 1, "consent_late": 1}
    assert {type(v) for v in counts.values()} == {int}  # counts only: no title, no ID
    # A Refresh moves `since`: what came before it no longer counts; the flags still do.
    assert arrivals(s.settings, since=T0, now=T0) == {**ZERO, "kept_out_late": 1, "consent_late": 1}
    assert arrivals(None, since=since, now=T0) == ZERO


def test_details_words_every_reason_the_marker_records():
    # why: decision 15 (decision 33: kept out by title). A reason Details can't word would show no
    # reason at all. Phase 1's marker records these four; Phase 2's rebuild record adds its own.
    from recordings.plaud.privacy import why_phrase

    recorded = {
        why_phrase(why) for why in ("title changed", "first check", "new pattern", "kept out late")
    }
    assert recorded == {"title changed", "first check", "patterns changed", "kept out by title"}
    assert recorded <= set(WHY_DETAILS)
    assert WHY_DETAILS[why_phrase("kept out late")] == "kept out by title"
    assert NO_SYNC == NO_SYNC_YET  # kept apart only because checks needs sqlite3


def test_the_plaud_views_import_on_pyodide_without_sqlite(demo_archive):
    # why: the Pages demo runs recordings_ui on Pyodide, which has no sqlite3 (tagging.py,
    # status.py). Its views draw only the canned view, and must not import what reads state.db.
    code = (
        "import sys; from datetime import UTC, datetime; from pathlib import Path; "
        "import recordings_ui.plaud as p; import recordings_ui.status as st; "
        f"s = p.demo_view_settings(Path({str(demo_archive)!r})); now = datetime.now(UTC); "
        "p.sync_view(s, now=now, can_request=None); p.sync_panel(s, now=now); "
        "p.arrivals(s, since=now, now=now); p.recording_plaud(s, 'x', now=now); "
        "st.status_view(Path('.'), None, plaud=s); "
        "print(sorted(m for m in ('sqlite3', 'recordings.state', 'recordings.writer', "
        "'recordings.plaud.checks') if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_the_views_settings_load_no_plaud_client_even_when_read():
    # why: spec §9.1 ("It imports no client, token or sync module"), the plan's decision 31. The
    # web app reads plaud_view_settings when it starts, which calls plaud_settings: Task 4's guard
    # only sees imports, so this checks the call too.
    code = (
        "import sys\n"
        "from recordings.config import Config\n"
        "from recordings_ui.plaud import plaud_view_settings\n"
        "data = {'plaud': {'api_base': 'https://plaud.example/api', 'schedule_minutes': 5}}\n"
        "s = plaud_view_settings(Config(path=None, archive_path=None, writer_id=None,\n"
        "    default_timezone='UTC', base_url=None, data=data))\n"
        "assert s.schedule_minutes == 5, s\n"
        "print('recordings.plaud.client' in sys.modules)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


# Calls every view against a real state.db, then submits a request that is inserted, and prints
# which of the forbidden modules (argv[4]) and of the readers they import lazily got loaded.
CALL_EVERYTHING = """
import asyncio, json, sys
from datetime import UTC, datetime
from pathlib import Path
from recordings.archive import Archive
from recordings.state import State
from recordings_ui import plaud as p
from recordings_ui.status import StatusSettings, status_view
from recordings_ui.views import recording_view

state_path, root, rid = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
now = datetime.now(UTC)
s = p.PlaudViewSettings(state_path=state_path, timezone_name="UTC", schedule_minutes=20,
    full_sweep_days=7, auto_import=True, keep_out_word_ok=True, demo_view=None)
p.sync_view(s, now=now, can_request=None)
p.sync_panel(s, now=now)
p.arrivals(s, since=now, now=now)
assert p.recording_plaud(s, rid, now=now)["flags"]
assert recording_view(Archive(root), rid, plaud=s, now=now)["plaud"]
status = StatusSettings(state_path=state_path, timezone_name="UTC", warn=20.0, stop=5.0,
    jobs=("disk",))
assert status_view(root, status, now=now, plaud=s)["sync"]["attention"]
request = {"request_id": "probe0001test", "kind": "keep-here", "plaud_ids": [],
    "recording_ids": [rid], "all": False}
reply = asyncio.run(p.submit(State(state_path), request, now=now, timezone_name="UTC",
    reason=None))
assert reply["state"] == "queued", reply
read = ("recordings.plaud.checks", "recordings.plaud.health", "recordings.worker")
print(json.dumps({"forbidden": sorted(m for m in json.loads(sys.argv[4]) if m in sys.modules),
    "read": [m in sys.modules for m in read]}))
"""


def test_calling_the_views_and_submitting_a_request_loads_no_plaud_client(plaud_scene):
    # why: spec §9.1. The views and submit import their readers inside each call, where Task 4's
    # import guard can't see them: this calls each, against a scene, in a fresh interpreter.
    s = plaud_scene
    s.add(1, "Week 1")
    rid = s.kept_out_late(1)
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            CALL_EVERYTHING,
            str(s.writer.state.path),
            str(s.writer.root),
            rid,
            json.dumps(FORBIDDEN),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    found = json.loads(out.stdout.strip().splitlines()[-1])
    assert found == {"forbidden": [], "read": [True, True, True]}


def test_the_docker_image_carries_the_canned_view():
    # The Docker demo (make docker) finds it beside RECORDINGS_DEMO_ARCHIVE=/app/demo/archive.
    dockerfile = (REPO / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY demo/plaud-sync.json demo/plaud-sync.json\n" in dockerfile
