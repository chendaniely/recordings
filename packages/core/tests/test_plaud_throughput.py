"""The Plaud client's throughput (stage 2b spec §10.1, §10.2; area A2): pacing, the budget, 5xx
and 429 backoff, the stall rule and `Range` resume, against the fake Plaud server's faults (§10.6
steps 4 and 5, §21). The clocks, the sleep and the randomness are injected: no test here really
waits for a backoff. Every ID, body and token is synthetic."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from fake_plaud import (
    Response,
    dropped,
    expired_link,
    html_page,
    json_response,
    rate_limited,
    server_error,
    short_body,
    timeout,
)
from recordings.plaud import client as client_module
from recordings.plaud.client import (
    BACKOFF,
    BUDGET,
    FILE_PATH,
    JITTER,
    LIST_PATH,
    MAX_RESUMES,
    MAX_RETRY_AFTER,
    MIN_RATE,
    PACE,
    PAGE_SIZE,
    PROGRESS_BYTES,
    STALL_SECONDS,
    TRIES,
    WAIT_SLICE,
    BudgetSpent,
    IncompleteDownload,
    PlaudAuthError,
    PlaudBadResponse,
    PlaudCallRefused,
    PlaudClient,
    PlaudError,
    PlaudForbidden,
    PlaudLinkExpired,
    PlaudLinkRefused,
    PlaudNotFound,
    PlaudOffline,
    PlaudRateLimited,
    PlaudUnavailable,
    Throttle,
    ends_the_run,
)

T0 = datetime(2026, 11, 1, 14, 20, tzinfo=UTC)
OF_ID = "of_" + f"{7:032x}"
AUDIO = bytes(range(256)) * 400  # 102,400 synthetic bytes


class FakeTime:
    """One fake clock for the throttle's monotonic, its UTC clock and the download's stall rule.
    Sleeping moves it on; nothing really waits."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self) -> datetime:
        return T0 + timedelta(seconds=self.now)


class Progress:
    def __init__(self) -> None:
        self.bumps = 0

    def __call__(self) -> None:
        self.bumps += 1


def _throttle(t: FakeTime, progress=None, **kw) -> Throttle:
    return Throttle(
        monotonic=t.monotonic,
        sleep=t.sleep,
        clock=t.clock,
        rand=lambda: 0.5,
        progress=progress,
        **kw,
    )


class Tokens:
    def access_token(self) -> str:
        return "synthetic-access-1"

    def refresh(self, rejected: str) -> str:
        raise PlaudAuthError()


def _client(fake, t: FakeTime, progress=None, **kw) -> PlaudClient:
    return PlaudClient(
        Tokens(),
        api_base=fake.api_url,
        monotonic=t.monotonic,
        throttle=_throttle(t, progress),
        **kw,
    )


def _failing(*errors: PlaudError | None):
    """A request that raises each of `errors` in turn (None: answers "ok"), counting its calls."""
    calls = []

    def send():
        calls.append(1)
        error = errors[min(len(calls), len(errors)) - 1]
        if error is not None:
            raise error
        return "ok"

    send.calls = calls
    return send


# ---- the numbers ------------------------------------------------------------------------------


def test_the_numbers_are_the_specs():
    # why: §10.1, §10.2 and §22: these change only with a stated reason (S5, S10 and S11 were
    # skipped, so they are the spec's own, recorded in the guesses file).
    assert PAGE_SIZE == 100  # audio-router's: the API rejects anything under 10
    assert (PACE, BUDGET, BACKOFF, TRIES, WAIT_SLICE) == (1.0, 600.0, 30.0, 3, 5.0)
    assert (STALL_SECONDS, MIN_RATE, client_module.READ_TIMEOUT) == (60.0, 50_000, 60.0)
    assert (MAX_RETRY_AFTER, MAX_RESUMES, JITTER, PROGRESS_BYTES) == (600.0, 3, 0.25, 1 << 20)
    throttle = Throttle()
    assert throttle.pace == 1.0 and throttle.sleep is None  # the real sleep, outside tests
    assert PlaudClient(Tokens(), api_base="https://api.example").throttle.pace == 1.0


# ---- pacing -----------------------------------------------------------------------------------


def test_calls_start_at_least_a_second_apart():
    # why: §10.2 "one call at a time, at most one a second on average". The first call never
    # waits; a call that took longer than the pace leaves nothing to wait for.
    t = FakeTime()
    throttle = _throttle(t)
    starts = []

    def send(takes: float = 0.0):
        starts.append(t.now)
        t.now += takes
        return "ok"

    for _ in range(3):
        throttle.call(send, budgeted=True)
    throttle.call(lambda: send(2.5), budgeted=False)  # a link counts as a call too
    throttle.call(send, budgeted=True)
    assert starts == [0.0, 1.0, 2.0, 3.0, 5.5]
    assert t.sleeps == [1.0, 1.0, 1.0]
    assert throttle.calls == 5


def test_one_call_at_a_time_across_threads():
    # why: §10.2. Clients sharing a throttle (A5's run) never have two requests in flight.
    throttle = Throttle(pace=0.0)
    inside, most = [0], [0]
    lock = threading.Lock()

    def send():
        with lock:
            inside[0] += 1
            most[0] = max(most[0], inside[0])
        threading.Event().wait(0.01)
        with lock:
            inside[0] -= 1

    threads = [
        threading.Thread(target=lambda: throttle.call(send, budgeted=True)) for _ in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert most[0] == 1 and throttle.calls == 6


# ---- 5xx, timeouts and connection failures -----------------------------------------------------


def test_a_5xx_or_a_timeout_is_retried_after_30_then_60_seconds_in_slices():
    # why: §10.2 "retried with backoff (30 seconds, doubling, with jitter)", and §9.2: every slice
    # of a backoff bumps the worker's progress, so the heartbeat sees it moving.
    t, progress = FakeTime(), Progress()
    throttle = _throttle(t, progress)
    send = _failing(PlaudUnavailable(), PlaudUnavailable(), None)
    assert throttle.call(send, budgeted=True) == "ok"
    first, second = BACKOFF * (1 + JITTER * 0.5), 2 * BACKOFF * (1 + JITTER * 0.5)
    assert len(send.calls) == 3 and throttle.retries == 2
    assert sum(t.sleeps) == pytest.approx(first + second)
    assert max(t.sleeps) <= WAIT_SLICE
    assert progress.bumps == len(t.sleeps)  # pacing waited nothing: each backoff was longer


def test_the_jitter_only_ever_lengthens_a_backoff():
    for rand, factor in ((0.0, 1.0), (1.0, 1 + JITTER)):
        t = FakeTime()
        throttle = Throttle(monotonic=t.monotonic, sleep=t.sleep, rand=lambda r=rand: r)
        throttle.call(_failing(PlaudUnavailable(), None), budgeted=True)
        assert sum(t.sleeps) == pytest.approx(BACKOFF * factor)


def test_a_third_5xx_in_a_row_ends_the_run_as_plaud_unavailable():
    t = FakeTime()
    throttle = _throttle(t)
    send = _failing(PlaudUnavailable())
    with pytest.raises(PlaudUnavailable) as info:
        throttle.call(send, budgeted=True)
    assert len(send.calls) == TRIES == 3
    assert ends_the_run(info.value)


def test_a_connection_failure_is_never_retried():
    # why: §10.2 "Connection failures end the run: offline".
    t = FakeTime()
    send = _failing(PlaudOffline())
    with pytest.raises(PlaudOffline):
        _throttle(t).call(send, budgeted=True)
    assert len(send.calls) == 1 and t.sleeps == []


@pytest.mark.parametrize(
    "error", [PlaudForbidden(), PlaudAuthError(), PlaudBadResponse(), PlaudLinkExpired()]
)
def test_other_errors_go_straight_to_the_caller(error):
    t = FakeTime()
    send = _failing(error)
    with pytest.raises(type(error)):
        _throttle(t).call(send, budgeted=True)
    assert len(send.calls) == 1 and t.sleeps == []


# ---- 429 ----------------------------------------------------------------------------------------


def test_a_retry_after_within_the_budget_is_waited_out_in_slices():
    # why: §10.2 "a Retry-After within the budget is waited out, in 5-second slices".
    t, progress = FakeTime(), Progress()
    throttle = _throttle(t, progress)
    send = _failing(PlaudRateLimited(12.0), None)
    with throttle.budget():
        assert throttle.call(send, budgeted=True) == "ok"
    assert t.sleeps == [5.0, 5.0, 2.0] and progress.bumps == 3
    assert throttle.rate_limits == 1


def test_a_retry_after_past_the_budget_ends_the_run_saying_until_when():
    # why: §10.2 "A longer one ends the run, 'rate limited until 14:40', and the next run waits".
    t = FakeTime()
    throttle = _throttle(t)
    send = _failing(PlaudRateLimited(1200.0))
    with throttle.budget(), pytest.raises(PlaudRateLimited) as info:
        throttle.call(send, budgeted=True)
    assert len(send.calls) == 1 and t.sleeps == []
    assert info.value.until == T0 + timedelta(seconds=1200)
    assert ends_the_run(info.value)
    t.now = 590.0  # 10 seconds left: a 15-second wait no longer fits
    with throttle.budget(10.0), pytest.raises(PlaudRateLimited):
        throttle.call(_failing(PlaudRateLimited(15.0)), budgeted=True)


def test_with_no_budget_a_retry_after_up_to_ten_minutes_is_waited():
    # The CLI and the Sync screen have no budget (§10.2); a wait longer than a scheduled run's
    # whole budget ends the run even so (a guess: the worker's tick would wait with it).
    t = FakeTime()
    throttle = _throttle(t)
    assert throttle.call(_failing(PlaudRateLimited(MAX_RETRY_AFTER), None), budgeted=True) == "ok"
    assert sum(t.sleeps) == MAX_RETRY_AFTER
    with pytest.raises(PlaudRateLimited) as info:
        throttle.call(_failing(PlaudRateLimited(MAX_RETRY_AFTER + 1)), budgeted=True)
    assert info.value.until == t.clock() + timedelta(seconds=MAX_RETRY_AFTER + 1)


def test_a_429_with_no_retry_after_ends_the_run_with_no_until():
    t = FakeTime()
    with pytest.raises(PlaudRateLimited) as info:
        _throttle(t).call(_failing(PlaudRateLimited(None)), budgeted=True)
    assert info.value.until is None and t.sleeps == []


def test_a_third_429_in_a_row_ends_the_run():
    # A server answering "Retry-After: 0" for ever would otherwise be asked again for ever.
    t = FakeTime()
    send = _failing(PlaudRateLimited(0.0))
    with pytest.raises(PlaudRateLimited) as info:
        _throttle(t).call(send, budgeted=True)
    assert len(send.calls) == 3 and info.value.until == t.clock()


# ---- the budget ---------------------------------------------------------------------------------


def test_no_api_call_starts_once_the_budget_is_spent_but_links_still_go():
    # why: §10.2. A scheduled run's 10 minutes: no new API call after them; the blocks of an
    # envelope already fetched, and a download under way, finish.
    t = FakeTime()
    throttle = _throttle(t)
    with throttle.budget():
        assert throttle.left() == BUDGET and not throttle.spent()
        t.now += BUDGET
        assert throttle.spent()
        send = _failing(None)
        with pytest.raises(BudgetSpent) as info:
            throttle.call(send, budgeted=True)
        assert send.calls == [] and ends_the_run(info.value)
        assert throttle.call(send, budgeted=False) == "ok"
    assert throttle.left() is None and not throttle.spent()  # the budget was the run's
    assert throttle.call(send, budgeted=True) == "ok"


def test_a_backoff_that_would_pass_the_budget_ends_the_run_at_once():
    t = FakeTime()
    throttle = _throttle(t)
    send = _failing(PlaudUnavailable())
    with throttle.budget(60.0), pytest.raises(PlaudUnavailable):
        throttle.call(send, budgeted=True)
    assert len(send.calls) == 2  # 33.75 s fitted; 67.5 s didn't


def test_a_light_turns_budget_inside_a_long_run_ends_with_it():
    t = FakeTime()
    throttle = _throttle(t)
    with throttle.budget(600.0):
        with throttle.budget(100.0):
            assert throttle.left() == 100.0
        with throttle.budget(900.0):  # never past the outer one
            assert throttle.left() == 600.0
        assert throttle.left() == 600.0
    assert throttle.left() is None


def test_a_progress_hook_that_raises_stops_a_wait():
    # The worker's SIGTERM: it stops within its grace, not after a 2-minute Retry-After.
    class Stop(Exception):
        pass

    t = FakeTime()

    def progress():
        raise Stop()

    send = _failing(PlaudRateLimited(120.0), None)
    with pytest.raises(Stop):
        _throttle(t, progress).call(send, budgeted=True)
    assert len(send.calls) == 1 and t.sleeps == [5.0]


@pytest.mark.parametrize(
    ("error", "ends"),
    [
        (PlaudAuthError(), True),
        (PlaudForbidden(), True),
        (PlaudUnavailable(), True),
        (PlaudOffline(), True),
        (PlaudRateLimited(), True),
        (BudgetSpent(), True),
        (PlaudBadResponse(), False),
        (PlaudNotFound(), False),
        (PlaudCallRefused(), False),
        (PlaudLinkExpired(), False),
        (PlaudLinkRefused(), False),
        (IncompleteDownload(), False),
        (ValueError(), False),
    ],
)
def test_what_ends_the_run_and_what_is_one_recordings_failure(error, ends):
    # why: §10.2. The run stops for the sign-in, the API's 403, an outage, a rate limit past its
    # wait and the budget; anything else is retried once, then counted, by the caller.
    assert ends_the_run(error) is ends


# ---- against the fake server: the API -----------------------------------------------------------


def test_the_listing_rides_out_two_5xx(plaud_server):
    t = FakeTime()
    plaud_server.route("GET", LIST_PATH, lambda q: json_response({"data_list": []}))
    plaud_server.fault(LIST_PATH, server_error(503), server_error(502), times=2)
    page = _client(plaud_server, t).list_page(1)
    assert page.rows == () and [r.path for r in plaud_server.requests] == [LIST_PATH] * 3
    assert sum(t.sleeps) == pytest.approx(3 * BACKOFF * (1 + JITTER * 0.5))


def test_a_real_timeout_is_retried_then_ends_the_run(plaud_server, monkeypatch):
    # The socket's read timeout, for real (shortened): a timeout is Plaud unavailable, retried.
    monkeypatch.setattr(client_module, "READ_TIMEOUT", 0.1)
    plaud_server.fault(LIST_PATH, timeout(0.5))
    with pytest.raises(PlaudUnavailable):
        _client(plaud_server, FakeTime()).list_page(1)
    assert len(plaud_server.requests) == 3


def test_a_dropped_connection_is_retried_like_a_5xx(plaud_server):
    plaud_server.route("GET", LIST_PATH, lambda q: json_response([]))
    plaud_server.fault(LIST_PATH, dropped(), times=1)
    assert _client(plaud_server, FakeTime()).list_page(1).rows == ()
    assert len(plaud_server.requests) == 2


def test_a_429_on_files_is_waited_out_then_one_past_the_budget_ends_the_run(plaud_server):
    t = FakeTime()
    path = FILE_PATH.format(OF_ID)
    plaud_server.route("GET", path, lambda q: json_response({"id": OF_ID}))
    plaud_server.fault(path, rate_limited(20), times=1)
    client = _client(plaud_server, t)
    with client.throttle.budget():
        assert client.envelope(OF_ID)["id"] == OF_ID
        assert t.sleeps[:4] == [5.0, 5.0, 5.0, 5.0]
        plaud_server.fault(path, rate_limited(3600), times=1)
        with pytest.raises(PlaudRateLimited) as info:
            client.envelope(OF_ID)
    assert info.value.retry_after == 3600.0
    assert info.value.until == t.clock() + timedelta(seconds=3600)
    assert [r.path for r in plaud_server.requests].count(path) == 3


@pytest.mark.parametrize(
    ("answer", "error", "ends"),
    [
        (Response(403, b'{"detail": "no subscription"}'), PlaudForbidden, True),
        (html_page(), PlaudBadResponse, False),
        (short_body({"id": OF_ID}), PlaudBadResponse, False),
        (Response(404), PlaudNotFound, False),
    ],
)
def test_api_answers_that_arent_retried(plaud_server, answer, error, ends):
    # why: §10.2. The API's 403 ends the run ("Plaud refused access (403)"); an HTML 200 on
    # files/{id}, a short body or a 404 fails that one fetch, with nothing written.
    plaud_server.fault(FILE_PATH.format(OF_ID), answer)
    t = FakeTime()
    with pytest.raises(error) as info:
        _client(plaud_server, t).envelope(OF_ID)
    assert len(plaud_server.requests) == 1 and t.sleeps == []
    assert ends_the_run(info.value) is ends


def test_every_files_fetch_failing_while_the_listing_works(plaud_server):
    # §21 "every files/{id} failing while the listing works": a predicate picks the requests.
    def not_the_listing(path, _query):
        return path.startswith(LIST_PATH) and path != LIST_PATH

    plaud_server.route("GET", LIST_PATH, lambda q: json_response([{"id": OF_ID, "name": "Week"}]))
    plaud_server.fault(not_the_listing, server_error())
    client = _client(plaud_server, FakeTime())
    assert [row.listed_id for row in client.list_page(1).rows] == [OF_ID]
    with pytest.raises(PlaudUnavailable):
        client.envelope(OF_ID)
    paths = [r.path for r in plaud_server.requests]
    assert paths == [LIST_PATH] + [FILE_PATH.format(OF_ID)] * 3


def test_a_failure_on_page_k_only(plaud_server):
    plaud_server.route("GET", LIST_PATH, lambda q: json_response({"data_list": []}))
    plaud_server.fault(lambda p, q: q.get("page") == ["2"], html_page())
    client = _client(plaud_server, FakeTime())
    assert client.list_page(1).rows == ()
    with pytest.raises(PlaudBadResponse):
        client.list_page(2)
    assert client.list_page(3).rows == ()


def test_the_budget_stops_the_next_api_call_but_an_envelope_finishes_its_blocks(plaud_server):
    # why: §10.2. The envelope's own call starts in time; its blocks (links that expire within
    # 300 seconds) are fetched although the budget runs out meanwhile; the next call isn't sent.
    t = FakeTime()
    block = plaud_server.link("block", b'[{"content": "Hello."}]')
    envelope = {
        "id": OF_ID,
        "source_list": [{"data_type": "transaction", "data_content": "", "data_link": block}],
    }

    def slow_envelope(_query):
        t.now += BUDGET  # the answer took the rest of the budget
        return json_response(envelope)

    plaud_server.route("GET", FILE_PATH.format(OF_ID), slow_envelope)
    client = _client(plaud_server, t)
    with client.throttle.budget():
        env = client.envelope(OF_ID)
        assert env["source_list"][0]["data_content"] == '[{"content": "Hello."}]'
        with pytest.raises(BudgetSpent):
            client.list_page(1)
    assert [r.host for r in plaud_server.requests] == ["api", "storage"]


def test_a_blocks_outage_ends_the_run_and_its_expiry_is_recorded_in_place(plaud_server):
    # A link's 403 costs only its block (the caller fetches the envelope again, once); a storage
    # host down three times in a row is Plaud unavailable, which ends the run.
    expired = plaud_server.link("expired", b"x", expire_after=0)
    down = plaud_server.link("down", b"x", status=503)
    blocks = [{"data_type": "transaction", "data_content": "", "data_link": expired}]
    plaud_server.route(
        "GET", FILE_PATH.format(OF_ID), lambda q: json_response({"source_list": blocks})
    )
    client = _client(plaud_server, FakeTime())
    env = client.envelope(OF_ID)
    assert env["source_list"][0]["_data_link_error"] == "PlaudLinkExpired"
    blocks.append({"data_type": "auto_sum_note", "data_content": "", "data_link": down})
    with pytest.raises(PlaudUnavailable):
        client.envelope(OF_ID)
    assert plaud_server.links["down"].served == 3


@pytest.mark.parametrize("status", [400, 401, 403, 410])
def test_a_link_that_refuses_its_signature_has_expired(plaud_server, status, tmp_path):
    link = plaud_server.link("stale", b"x", status=status)
    client = _client(plaud_server, FakeTime())
    for call in (lambda: client.fetch_link(link), lambda: client.download(link, tmp_path / "a")):
        with pytest.raises(PlaudLinkExpired) as info:
            call()
        assert not ends_the_run(info.value)
        assert info.value.__cause__ is None and info.value.__suppress_context__


# ---- the request log ----------------------------------------------------------------------------


def test_the_request_log_holds_names_never_values(plaud_server, tmp_path):
    # §10.6 step 4: method, path, host and header names, never a value or a body.
    plaud_server.route("GET", LIST_PATH, lambda q: json_response([]))
    link = plaud_server.link("audio.mp3", AUDIO, cuts=[50_000])
    client = _client(plaud_server, FakeTime())
    client.list_page(1)
    client.download(link, tmp_path / "a.mp3")
    first, *storage = plaud_server.requests
    assert (first.method, first.path, first.host) == ("GET", LIST_PATH, "api")
    assert "authorization" in first.header_names
    assert [r.host for r in storage] == ["storage", "storage"]
    assert {"range", "if-range"} <= set(storage[1].header_names)
    text = repr(plaud_server.requests)
    for value in ("synthetic-access", "Bearer", "bytes=", "synthetic-", "X-Amz", "page_size"):
        assert value not in text


def test_a_hook_runs_once_after_request_k(plaud_server):
    # §10.6 step 6's "a hook that changes the account after request number K".
    names = ["Week 1"]
    plaud_server.route("GET", LIST_PATH, lambda q: json_response([{"id": OF_ID, "name": names[0]}]))
    plaud_server.after(2, lambda: names.__setitem__(0, "Week 2"))
    client = _client(plaud_server, FakeTime())
    seen = [client.list_page(1).rows[0].title for _ in range(4)]
    assert seen == ["Week 1", "Week 1", "Week 2", "Week 2"]


def test_a_fault_takes_its_answers_in_turn_then_lets_go(plaud_server):
    # A 500, then 429s (the last answer repeating), three answers in all; then the route answers.
    # One 5xx and two 429s reach neither kind's third in a row, so the call gets through.
    plaud_server.route("GET", LIST_PATH, lambda q: json_response([]))
    injected = plaud_server.fault(LIST_PATH, server_error(), rate_limited(1), times=3)
    assert _client(plaud_server, FakeTime()).list_page(1).rows == ()
    assert injected.answered == 3 and len(plaud_server.requests) == 4


# ---- downloads --------------------------------------------------------------------------------


def test_a_dropped_download_resumes_with_range_and_lands_whole(plaud_server, tmp_path):
    # why: §18's S11 rule. A connection that drops part-way resumes from the bytes already
    # written, with If-Range against the first answer's ETag, instead of starting again.
    link = plaud_server.link("audio.mp3", AUDIO, cuts=[40_000])
    dest = tmp_path / "audio" / "a.mp3"
    assert _client(plaud_server, FakeTime()).download(link, dest) == len(AUDIO)
    assert dest.read_bytes() == AUDIO
    assert plaud_server.links["audio.mp3"].starts == [0, 40_000]
    assert [p.name for p in dest.parent.iterdir()] == ["a.mp3"]


def test_a_server_that_ignores_range_restarts_the_download_once(plaud_server, tmp_path):
    whole = plaud_server.link("whole.mp3", AUDIO, ranges=False, cuts=[40_000])
    dest = tmp_path / "a.mp3"
    assert _client(plaud_server, FakeTime()).download(whole, dest) == len(AUDIO)
    assert dest.read_bytes() == AUDIO and plaud_server.links["whole.mp3"].starts == [0, 0]
    twice = plaud_server.link("twice.mp3", AUDIO, ranges=False, cuts=[40_000, 40_000])
    with pytest.raises(IncompleteDownload):
        _client(plaud_server, FakeTime()).download(twice, tmp_path / "b.mp3")
    assert plaud_server.links["twice.mp3"].starts == [0, 0, 0]  # the whole body, every time
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.mp3"]


def test_drops_past_the_resumes_allowed_are_an_incomplete_download(plaud_server, tmp_path):
    link = plaud_server.link("drops.mp3", AUDIO, cuts=[10_000] * 10)
    with pytest.raises(IncompleteDownload) as info:
        _client(plaud_server, FakeTime()).download(link, tmp_path / "a.mp3")
    assert plaud_server.links["drops.mp3"].starts == [0, 10_000, 20_000, 30_000]
    assert MAX_RESUMES == 3 and not list(tmp_path.iterdir())
    assert str(info.value) == IncompleteDownload.message


def test_a_link_that_expires_during_a_download_resumes_on_a_fresh_one(plaud_server, tmp_path):
    # why: §10.2 and S11. The link expires mid-download, so the resume is refused; the caller's
    # fresh link (the envelope fetched again) carries it on from where it stopped, once.
    old = plaud_server.link("old.mp3", AUDIO, cuts=[40_000], expire_after=1)
    with pytest.raises(PlaudLinkExpired):  # no fresh link to swap in: the caller's to fetch
        _client(plaud_server, FakeTime()).download(old, tmp_path / "a.mp3")
    assert not list(tmp_path.iterdir())
    old = plaud_server.link("old2.mp3", AUDIO, cuts=[40_000], expire_after=1)
    fresh_calls = []

    def fresh_link() -> str:
        fresh_calls.append(1)
        return plaud_server.link("new.mp3", AUDIO)

    dest = tmp_path / "b.mp3"
    client = _client(plaud_server, FakeTime())
    assert client.download(old, dest, fresh_link=fresh_link) == len(AUDIO)
    assert dest.read_bytes() == AUDIO and fresh_calls == [1]
    assert plaud_server.links["new.mp3"].starts == [40_000]


def test_a_fresh_link_is_asked_for_once(plaud_server, tmp_path):
    old = plaud_server.link("old.mp3", AUDIO, expire_after=0)
    calls = []

    def fresh_link() -> str:
        calls.append(1)
        return plaud_server.link(f"new-{len(calls)}.mp3", AUDIO, expire_after=0)

    with pytest.raises(PlaudLinkExpired):
        _client(plaud_server, FakeTime()).download(old, tmp_path / "a.mp3", fresh_link=fresh_link)
    assert calls == [1] and not list(tmp_path.iterdir())


def test_a_fresh_link_to_changed_audio_restarts_and_is_never_spliced(plaud_server, tmp_path):
    # If-Range: the new object's ETag differs, so the server sends it whole, and the download
    # restarts rather than gluing two objects together. Another length is refused outright.
    changed = bytes(reversed(AUDIO))
    old = plaud_server.link("old.mp3", AUDIO, cuts=[40_000], expire_after=1)
    dest = tmp_path / "a.mp3"
    client = _client(plaud_server, FakeTime())
    client.download(old, dest, fresh_link=lambda: plaud_server.link("new.mp3", changed))
    assert dest.read_bytes() == changed and plaud_server.links["new.mp3"].starts == [0]
    old = plaud_server.link("old2.mp3", AUDIO, cuts=[40_000], expire_after=1)
    with pytest.raises(IncompleteDownload):
        client.download(
            old, tmp_path / "b.mp3", fresh_link=lambda: plaud_server.link("short.mp3", AUDIO[:9])
        )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.mp3"]


def test_a_refused_resume_is_an_incomplete_download(plaud_server, tmp_path):
    # A 416, or a 206 that doesn't continue where the file ends, never lands a file.
    link = plaud_server.link("odd.mp3", AUDIO[:1000], headers={"Content-Length": "5000"})
    with pytest.raises(IncompleteDownload):
        _client(plaud_server, FakeTime()).download(link, tmp_path / "a.mp3")
    assert not list(tmp_path.iterdir())


def test_a_trickle_fails_the_stall_rule_and_is_never_resumed(plaud_server, tmp_path):
    # why: §10.1. Past its first minute, a download under 50 KB/s on average, or past its ceiling,
    # ends: the clock moves 61 seconds a read, so the first piece already comes too late.
    t = FakeTime()
    link = plaud_server.link("slow.mp3", AUDIO, trickle=(20_000, 0.001))
    real = t.monotonic

    def slow() -> float:
        t.now += 61.0
        return real()

    client = PlaudClient(Tokens(), api_base=plaud_server.api_url, monotonic=slow)
    with pytest.raises(IncompleteDownload):
        client.download(link, tmp_path / "a.mp3")
    assert plaud_server.links["slow.mp3"].starts == [0]
    assert not list(tmp_path.iterdir())


def test_a_downloads_time_counts_across_its_resumes(plaud_server, tmp_path):
    # The stall rule's average (and so its ceiling, Content-Length / MIN_RATE) counts from the
    # first answer: a resume that waited out a long Retry-After can't start the clock again.
    t = FakeTime()
    link = plaud_server.link("audio.mp3", AUDIO, cuts=[40_000])
    plaud_server.fault(
        lambda path, query: plaud_server.links["audio.mp3"].served == 1,
        rate_limited(120),
        host="storage",
        times=1,
    )
    with pytest.raises(IncompleteDownload):
        _client(plaud_server, t).download(link, tmp_path / "a.mp3")
    assert sum(t.sleeps) >= 120 and not list(tmp_path.iterdir())


def test_a_download_bumps_the_progress_at_every_mib(plaud_server, tmp_path):
    # §9.2: "every MiB of a download", so a long download keeps `--alive` green.
    body = bytes(3 * PROGRESS_BYTES + 10)
    link = plaud_server.link("big.mp3", body, cuts=[PROGRESS_BYTES + 5])
    progress = Progress()
    _client(plaud_server, FakeTime(), progress).download(link, tmp_path / "a.mp3")
    assert progress.bumps == 3


def test_the_disk_guard_sees_the_length_before_any_byte_is_written(plaud_server, tmp_path):
    # §11.5 step 5: twice the Content-Length against the free space, before the download.
    class DiskFull(Exception):
        pass

    seen = []

    def admit(length: int) -> None:
        seen.append(length)
        raise DiskFull()

    link = plaud_server.link("audio.mp3", AUDIO)
    with pytest.raises(DiskFull):
        _client(plaud_server, FakeTime()).download(link, tmp_path / "dl" / "a.mp3", admit=admit)
    assert seen == [len(AUDIO)] and not list(tmp_path.iterdir())
    allowed = []
    _client(plaud_server, FakeTime()).download(link, tmp_path / "a.mp3", admit=allowed.append)
    assert allowed == [len(AUDIO)]


def test_a_downloads_errors_still_carry_no_url(plaud_server, tmp_path):
    link = plaud_server.link("drops.mp3", AUDIO, cuts=[10_000] * 10)
    old = plaud_server.link("old.mp3", AUDIO, expire_after=0)
    for url in (link, old):
        with pytest.raises(PlaudError) as info:
            _client(plaud_server, FakeTime()).download(url, tmp_path / "a.mp3")
        text = str(info.value) + repr(info.value) + repr(info.value.args)
        assert "127.0.0.1" not in text and "X-Amz" not in text
        assert info.value.__cause__ is None and info.value.__suppress_context__
        assert info.value.__context__ is None or isinstance(info.value.__context__, PlaudError)


def test_every_link_403ing(plaud_server, tmp_path):
    # §21 "every link 403ing": one fault on the whole storage host.
    plaud_server.fault(lambda path, query: True, expired_link(), host="storage")
    audio = plaud_server.link("audio.mp3", AUDIO)
    client = _client(plaud_server, FakeTime())
    with pytest.raises(PlaudLinkExpired):
        client.fetch_link(audio)
    with pytest.raises(PlaudLinkExpired):
        client.download(audio, tmp_path / "a.mp3", fresh_link=lambda: audio)
    assert plaud_server.links["audio.mp3"].served == 0  # the fault answered every request
    assert not list(tmp_path.iterdir())
