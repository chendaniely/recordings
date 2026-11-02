"""Release's web side (stage 2b spec §12.2, §9.3; area A4): its requests name one Plaud ID and
nothing else, and its question's title is read once from the cache, keyed by the request's own
ID, then gone (BD1, P6, P8). Every ID and title is synthetic."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from recordings.init import init_archive
from recordings.plaud.cache import Cache, Preview
from recordings.plaud.checks import K_CATCH_UP_DONE, K_COMPARE, K_HOLD, K_LAST_ENDED, PlaudDB
from recordings.plaud.compare import TITLES_HIDDEN_IDS
from recordings.plaud.requests import PlaudRequest
from recordings.state import State
from recordings_ui.plaud import (
    UNREADABLE,
    PlaudViewSettings,
    Refusal,
    check_request,
    release_preview,
)

T0 = datetime(2026, 11, 2, 22, 20, tzinfo=UTC)
TZ = ZoneInfo("America/Vancouver")
PID = "0123456789abcdef0123456789abcdef"
RID = "20261006T140003-0700_3fa91c2e"


def raw(kind: str, **over) -> dict:
    return {"request_id": "abcdef12", "kind": kind, "plaud_ids": [f"of_{PID}"], **over}


@pytest.mark.parametrize("kind", ["release-preview", "release", "unrelease"])
def test_a_release_request_names_one_plaud_id_and_nothing_else(kind):
    assert check_request(raw(kind)) == PlaudRequest("abcdef12", kind, plaud_ids=(f"of_{PID}",))
    for bad in (
        {"plaud_ids": []},
        {"plaud_ids": [f"of_{PID}", "a" * 32]},
        {"recording_ids": [RID]},
        {"all": True},
    ):
        assert check_request(raw(kind, **bad)) == Refusal("abcdef12", UNREADABLE), bad


@pytest.fixture
def settings(tmp_path):
    root, state = tmp_path / "archive", tmp_path / "state"
    init_archive(root, state, writer_id="test")
    db = PlaudDB(State.open(state))
    db.put(K_CATCH_UP_DONE, T0.isoformat(), now=T0)
    db.put(K_LAST_ENDED, (T0 - timedelta(minutes=5)).isoformat(), now=T0)
    return PlaudViewSettings(
        state_path=state,
        timezone_name="America/Vancouver",
        schedule_minutes=20,
        full_sweep_days=7,
        auto_import=True,
        keep_out_word_ok=True,
        demo_view=None,
    )


def test_the_question_reads_its_title_once_by_the_requests_own_id(settings):
    Cache(settings.state_path).put_preview(
        "request-0001",
        Preview(PID, "Synthetic grant timelines", T0 - timedelta(hours=2), 12 * 60_000),
        now=T0,
    )
    assert release_preview(settings, "request-0002", now=T0) is None  # another request's
    shown = release_preview(settings, "request-0001", now=T0)
    assert shown == {
        "plaud_id": PID,
        "title": "Synthetic grant timelines",
        "date": f"{(T0 - timedelta(hours=2)).astimezone(TZ):%Y-%m-%d}",
        "time": f"{(T0 - timedelta(hours=2)).astimezone(TZ):%H:%M}",
        "duration": "12 min",
        "next_sync": f"{(T0 + timedelta(minutes=15)).astimezone(TZ):%H:%M}",
    }
    assert release_preview(settings, "request-0001", now=T0) is None  # shown once


@pytest.mark.parametrize("why", ["rekeyed", "hold", "compare"])
def test_a_preview_taken_since_its_title_was_barred_shows_no_title(settings, why):
    # The worker refuses these previews (the end-of-branch privacy review, 2026-11-02); a preview
    # written before the bar (a re-key matched or a hold recorded since) still leaves no title.
    db = PlaudDB(State.open(settings.state_path))
    if why == "rekeyed":
        db.set_listing(f"of_{PID}", rekey_of="a" * 32, first_seen=T0)
    elif why == "hold":
        db.put(K_HOLD, {"ids_changed": True}, now=T0)
    else:
        db.put(K_COMPARE, {"titles_hidden": TITLES_HIDDEN_IDS}, now=T0)
    Cache(settings.state_path).put_preview(
        "request-0001",
        Preview(PID, "Synthetic grant timelines", T0 - timedelta(hours=2), 12 * 60_000),
        now=T0,
    )
    shown = release_preview(settings, "request-0001", now=T0)
    assert shown is not None and shown["title"] == ""
    assert "Synthetic" not in repr(shown)
    assert release_preview(settings, "request-0001", now=T0) is None  # and it is gone


def test_no_question_in_the_demos_or_before_any_cache(settings, tmp_path):
    assert release_preview(None, "request-0001", now=T0) is None
    assert release_preview(settings, "request-0001", now=T0) is None  # no cache file yet
    assert not (settings.state_path / "plaud-cache.db").exists()  # and none was made
