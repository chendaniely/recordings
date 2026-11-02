"""The Plaud client: direct, read-only HTTP against Plaud's public third-party API (stage 2b spec
§10.1).

Copied from audio-router's `src/audio_router/archive/sources/plaud.py` at commit 64612df: Dan's
own code, relicensed MIT for this repo (main spec §19). The edits, each from §10.1:
- **IDs are opaque.** audio-router's `FILE_ID_RE` filter rejected Plaud's `of_<32 hex>` IDs, which
  is why its sync stopped. Nothing here checks an ID against a pattern. Every request uses the ID
  exactly as the newest listing gave it; the canonical form is only for comparing.
- **Only reads.** One request function sends `GET`; `post_refresh` sends the one `POST`, the
  refresh, to the token endpoint (§10.3). A `TokenSource` (the token store, `plaud/tokens.py`)
  supplies the access token and its refresh, replacing audio-router's call to the vendor CLI.
- **A 401 after a good refresh** makes the cheap current-user read (`USER_PATH`, §10.3): a 401
  there too is `PlaudAuthError`; if it works, only that one call was refused (`PlaudCallRefused`,
  that recording's failure). `USER_PATH` and the token endpoint are guesses, since the spikes were
  skipped and audio-router shows neither (docs/superpowers/plans/2026-11-01-stage-2b-guesses.md):
  a 404 at `USER_PATH` falls back to the first listing page, which proves the sign-in as well.
- **Redirects and the token.** API calls refuse every redirect, as `alerts.py` does. Authorization
  goes only to the API host, as an unredirected header. Presigned links (the audio, and blocks
  behind a `data_link`) are fetched without it, must be `https`, and may follow a redirect only to
  another `https` URL. Plain `http` is allowed only to the loopback, and only while the API base is
  the loopback too: that is the tests' fake (2b phase 1 plan, decision 7).
- **Where it may point** (`API_BASE`, `check_api_base`, and the URL rules `check_link` shares) is
  in `endpoint`, which loads no network module: the settings reader imports it, never this module,
  so the web app never loads the client (2b phase 1 plan, decision 31). Both names are imported
  here, so `client.API_BASE` and `client.check_api_base` still work.
- **Nothing secret or personal leaves it.** Every error from the network is one of this
  module's classes, with a fixed message, raised `from None` outside the handler that caught the
  cause, so no chained exception carries a URL, a token or a body (some `urllib` errors hold the
  whole URL). Not its own: `ConfigError` from the constructor (a bad `api_base`), `ValueError` from
  `get_json` for a path that isn't one, and the `OSError` of a local file (the destination's folder,
  its `.part`, the rename), which is the archive's trouble and never reads as "offline". A block
  that fails records its error's class name, never `str(exc)`. A Plaud ID is shown only as
  `id_hash`.
- **Timeouts:** 10 seconds to connect, 60 to read. A download also has a stall rule: no bytes for
  60 seconds (the read timeout, which raises `IncompleteDownload`), or under 50 KB/s on average
  after its first minute, counted from its first answer across its resumes. Past that minute,
  staying above the average also bounds the whole download by Content-Length / MIN_RATE, which
  is §10.1's ceiling scaled from Content-Length. A download needs a Content-Length (ASCII digits,
  not 0) and never writes more than it declares. Other answers are read up to 64 MiB.

**Throughput (§10.2; area A2 of the build straight through).** Every request goes through a
`Throttle`, one per process, which A5's run shares between its clients:
- **Pacing:** one call at a time, each starting at least `PACE` (1 second) after the one before:
  API calls and presigned links alike, since the budget counts both (§14.2's "600-call budget").
- **The budget:** inside `Throttle.budget(BUDGET)` (a scheduled run's 10 minutes), an API call
  that would start after the deadline raises `BudgetSpent` and sends nothing. Links aren't stopped:
  the blocks of an envelope already fetched, and a download under way, finish (§10.2).
- **5xx, 408 and timeouts** (`PlaudUnavailable`) are retried after 30 seconds, then 60, each
  lengthened by up to a quarter at random; the third in a row raises, as does a wait that would
  pass the budget's end. **Connection failures** (`PlaudOffline`) are never retried.
- **429:** a `Retry-After` (in seconds) that fits in the budget, or with no budget is at most
  `MAX_RETRY_AFTER`, is waited out; a longer one, none, or a third 429 in a row raises
  `PlaudRateLimited` with `until` (when Plaud said it would answer again; None when it didn't say).
- **Waits run in 5-second slices,** each calling the throttle's `progress`, which the worker points
  at its heartbeat's progress (§9.2), and which may raise to stop a wait (SIGTERM). A download
  calls it at every MiB too.
- **What ends a run** (`ends_the_run`): a sign-in refused, the API's 403, Plaud unavailable after
  its retries, offline, rate limited past the wait, and the budget spent. Anything else is that
  one recording's failure (§10.2's "retried once, then counted", the caller's).
- **Links:** a presigned link that answers 400, 401, 403 or 410 has expired: `PlaudLinkExpired`,
  never the API's `PlaudForbidden`, so the caller fetches the envelope again, once (§10.2).
- **`Range` resume** (§18's S11 rule): a download whose connection drops resumes from the bytes it
  has, with `Range` (and `If-Range` against a strong ETag), up to `MAX_RESUMES` times. A server
  that answers the whole body instead (Range not honoured, or the object changed) restarts it from
  zero, once. A link that has expired is swapped, once, for the caller's `fresh_link()`.
  `PAGE_SIZE` stays audio-router's 100. S5, S10 and S11 were skipped: these numbers are the spec's,
  and the guesses file says so (docs/superpowers/plans/2026-11-01-stage-2b-guesses.md).
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import os
import random
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from recordings import __version__
from recordings.plaud.endpoint import API_BASE, check_api_base, is_loopback, plain_url, split_url
from recordings.refs import canonical_plaud_id

LIST_PATH = "/open/third-party/files/"
FILE_PATH = "/open/third-party/files/{}"
# The cheap signed-in read (§10.3): a guess, after the vendor CLI's `plaud me`, which audio-router
# runs to refresh. A 404 there falls back to one listing page of PROBE_PAGE_SIZE rows.
USER_PATH = "/open/third-party/users/me"
PROBE_PAGE_SIZE = 10  # the smallest page the API takes
PAGE_SIZE = 100  # audio-router's (the API rejects anything under 10); S5 skipped: a guess
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 60.0
STALL_SECONDS = 60.0  # no bytes for this long
MIN_RATE = 50_000  # bytes per second on average after the first minute
# §10.2's numbers (S10 and S11 skipped, so the spec's own stand; §22: change one only with a reason)
PACE = 1.0  # seconds between the starts of two calls: one at a time, at most one a second
BUDGET = 600.0  # a scheduled run's 10 minutes of calls; the CLI and the Sync screen have none
MAX_RETRY_AFTER = BUDGET  # with no budget, the longest Retry-After waited out (a guess)
BACKOFF = 30.0  # seconds before the first retry after a 5xx or a timeout; it doubles
JITTER = 0.25  # each backoff is lengthened by up to this share, at random, never shortened
TRIES = 3  # a third 5xx or timeout in a row on one call raises; a third 429 too
WAIT_SLICE = 5.0  # a wait runs in slices this long, each one bumping the worker's progress
MAX_RESUMES = 3  # a download's dropped connections resumed with Range (a guess)
PROGRESS_BYTES = 1024 * 1024  # a download bumps the worker's progress at every MiB (§9.2)
USER_AGENT = f"recordings/{__version__}"  # bare urllib's default gets a flat 403 from this API
_CHUNK = 64 * 1024
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_LINK_EXPIRED = frozenset({400, 401, 403, 410})  # how storage hosts refuse a stale signature
_LIST_KEYS = ("data", "data_list", "files", "items", "list")
_MAX_BODY = 64 * 1024 * 1024  # an API answer or a block is never near this; more is refused


class PlaudError(Exception):
    """Every error the client raises: a fixed message per class (spec §15.6), raised `from None`,
    so no URL, token or body travels with it."""

    message = "Plaud error"

    def __init__(self) -> None:
        super().__init__(self.message)


class PlaudAuthError(PlaudError):
    message = "sign-in needed"


class PlaudForbidden(PlaudError):
    message = "Plaud refused access (403)"


class PlaudUnavailable(PlaudError):
    message = "Plaud unavailable"


class PlaudOffline(PlaudError):
    message = "offline"


class PlaudRateLimited(PlaudError):
    """A 429. `retry_after` is the answer's Retry-After in seconds (None: absent, or not a whole
    number of seconds); `until` is set when the throttle gives up waiting: when Plaud said it would
    answer again, for "rate limited until 14:40" and the next run's wait (§10.2), or None."""

    message = "rate limited"

    def __init__(self, retry_after: float | None = None, until: datetime | None = None) -> None:
        super().__init__()
        self.retry_after = retry_after
        self.until = until


class PlaudBadResponse(PlaudError):
    message = "Plaud's answer couldn't be read"


class PlaudNotFound(PlaudBadResponse):
    """A 404: to the sync, an answer it can't use, as before; `current_user` tells it apart."""


class PlaudCallRefused(PlaudError):
    """A 401 again after a good refresh, while the current-user read works (§10.3): the sign-in
    is good, and this one call failed, as that recording's failure."""

    message = "Plaud refused this call (401), though the sign-in works"


class PlaudRedirectRefused(PlaudError):
    message = "Plaud answered with a redirect, which is never followed"


class PlaudLinkRefused(PlaudError):
    message = "a link from Plaud was refused"


class PlaudLinkExpired(PlaudError):
    """A presigned link answered 400, 401, 403 or 410: its signature has expired (§10.2). Never
    the API's own 403: the caller fetches the envelope again, once, for fresh links."""

    message = "a link from Plaud has expired"


class IncompleteDownload(PlaudError):
    message = "the download was short, empty or stalled"


class BudgetSpent(PlaudError):
    """A scheduled run's 10 minutes of calls are spent (§10.2): no API call starts after them."""

    message = "the run's time for Plaud calls is spent"


class _Dropped(PlaudError):
    """A download's connection ended before its last byte: resumed, never raised past `download`."""


# What ends a run (§10.2, §10.3), once the throttle's own retries are done: the sign-in, the API's
# 403, Plaud unavailable or offline, rate limited past the wait, and the budget spent. Every other
# PlaudError is the one recording's failure, which the caller retries once, then counts.
RUN_ENDING: tuple[type[PlaudError], ...] = (
    PlaudAuthError,
    PlaudForbidden,
    PlaudUnavailable,
    PlaudOffline,
    PlaudRateLimited,
    BudgetSpent,
)


def ends_the_run(exc: BaseException) -> bool:
    return isinstance(exc, RUN_ENDING)


class TokenSource(Protocol):
    def access_token(self) -> str:
        """The current access token; PlaudAuthError when there is none."""
        ...

    def refresh(self, rejected: str) -> str:
        """After a 401 on `rejected`: a new access token, or PlaudAuthError."""
        ...


@dataclass(frozen=True)
class Listed:
    """One row of the list endpoint: metadata only, no content."""

    listed_id: str  # as the listing gave it, stripped of surrounding whitespace
    title: str | None  # None: the row had no title field
    created_at: datetime | None
    start_at: datetime | None
    duration_ms: int | None
    serial: str = ""

    @property
    def canonical_id(self) -> str:
        return canonical_plaud_id(self.listed_id)

    @property
    def recorded_at(self) -> datetime | None:
        """Best available capture time. start_at is the capture time for device recordings; for
        uploads it is the upload moment, which is why S12 checks it (§18)."""
        return self.start_at or self.created_at


@dataclass(frozen=True)
class Page:
    rows: tuple[Listed, ...]
    malformed: int  # rows with no ID or no title field (§11.2 counts them)


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)  # 3.11 and later read a trailing Z
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _duration(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _loopback_http(api_base: str) -> bool:
    parts = split_url(api_base)
    return parts is not None and parts.scheme == "http" and is_loopback(parts.hostname)


def check_link(url: object, *, api_base: str) -> str:
    """A presigned link from Plaud: https, in visible ASCII; plain http only to the loopback while
    `api_base` is the loopback too (decision 7). Otherwise PlaudLinkRefused, without the URL: a
    link may carry a signature that grants Dan's audio."""
    parts = split_url(url)
    secure = parts is not None and parts.scheme == "https"
    test_http = (
        parts is not None
        and parts.scheme == "http"
        and is_loopback(parts.hostname)
        and _loopback_http(api_base)
    )
    if not (secure or test_http):
        raise PlaudLinkRefused() from None
    return str(url)


def id_hash(plaud_id: str) -> str:
    """The only form a Plaud ID is shown in: 8 hex of the SHA-256 of its canonical form (§10.1)."""
    return hashlib.sha256(canonical_plaud_id(plaud_id).encode("utf-8")).hexdigest()[:8]


_ACCOUNT_KEYS = ("id", "user_id", "uid")  # a guess: S1 would have shown the reply's names


def account_hash(payload: object) -> str | None:
    """The only form the account is shown in (§10.5): 8 hex of the SHA-256 of its ID, from the
    current-user answer's `id` (or `user_id`, `uid`), at its top or under `data`; None when it
    names none."""
    for part in (payload.get("data") if isinstance(payload, dict) else None, payload):
        if not isinstance(part, dict):
            continue
        for key in _ACCOUNT_KEYS:
            value = part.get(key)
            if isinstance(value, str | int) and not isinstance(value, bool) and str(value):
                return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8]
    return None


def _visible(value: object) -> bool:
    """A token as it may go into a header or a form: visible ASCII, no space (header injection)."""
    return (
        isinstance(value, str) and bool(value) and value.isascii() and value.isprintable()
    ) and " " not in value


_MAX_TOKEN_REPLY = 1024 * 1024


def post_refresh(token_url: str, fields: Mapping[str, str]) -> dict:
    """The one POST (§10.1, §10.3): a refresh, form-encoded, to the token endpoint, never
    redirected and with no Authorization header. Returns the reply's JSON object, which has a
    usable `access_token`. Refused (any 4xx but 408 and 429, or a 200 whose JSON names an
    `error`): PlaudAuthError. 408, 429, 5xx, no connection, or a reply that can't be read: the
    outage classes, never a sign-in problem. Nothing of the request or the reply is logged, and
    every error is this module's own, raised from None."""
    request = urllib.request.Request(
        token_url, data=urlencode(dict(fields)).encode("ascii"), method="POST"
    )
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    response = _guarded(
        lambda: _API_OPENER.open(request, timeout=CONNECT_TIMEOUT),
        PlaudBadResponse,
        from_http=_from_refresh,
    )
    with response:
        body = _guarded(
            lambda: response.read(_MAX_TOKEN_REPLY + 1), PlaudBadResponse, from_http=_from_refresh
        )
        unfinished = bool(getattr(response, "length", None))
    if len(body) > _MAX_TOKEN_REPLY or unfinished:
        raise PlaudBadResponse() from None
    reply: object = None
    # HTML or broken JSON: raised below, outside the handler, so no JSONDecodeError rides along.
    with contextlib.suppress(ValueError, RecursionError):
        reply = json.loads(body)
    if isinstance(reply, dict) and isinstance(reply.get("error"), str):
        raise PlaudAuthError() from None  # a refusal some servers send with a 200
    if not isinstance(reply, dict) or not _visible(reply.get("access_token")):
        raise PlaudBadResponse() from None
    return reply


class _HTTPConnection(http.client.HTTPConnection):
    """Connects within CONNECT_TIMEOUT (the opener's timeout), then reads with READ_TIMEOUT."""

    def connect(self) -> None:
        super().connect()
        self.sock.settimeout(READ_TIMEOUT)


class _HTTPSConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        super().connect()  # the TCP connect and the TLS handshake, within CONNECT_TIMEOUT
        self.sock.settimeout(READ_TIMEOUT)


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_HTTPConnection, req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_HTTPSConnection, req, context=self._context)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """An API call is never redirected: it carries the token (alerts.py's `_NoRedirect`)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # HTTPDefaultErrorHandler raises HTTPError(code): PlaudRedirectRefused


class _HttpsRedirect(urllib.request.HTTPRedirectHandler):
    """A presigned link may be redirected only to another https URL (§10.1)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = split_url(newurl)
        if parts is None or parts.scheme != "https":
            fp.close()
            raise PlaudLinkRefused() from None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(redirects: urllib.request.HTTPRedirectHandler) -> urllib.request.OpenerDirector:
    """Proxies from the environment only, and only for https, as alerts.py's: through one, a
    request travels in a CONNECT tunnel and the proxy sees the host name alone. The default
    handler would also ask macOS's system configuration, which starts threads that never end."""
    proxies = urllib.request.getproxies_environment()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({k: v for k, v in proxies.items() if k == "https"}),
        _HTTPHandler,
        _HTTPSHandler,
        redirects,
    )


_API_OPENER = _opener(_NoRedirect())
_LINK_OPENER = _opener(_HttpsRedirect())


def _ascii_number(value: object) -> int | None:
    """A whole number from a header: ASCII digits only. str.isdigit() is also True for '\u00b2',
    which int() and float() then refuse with a bare ValueError."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    # 15 digits is far more than any header here holds, and keeps int() and float() from the
    # OverflowError (past 308 digits) and ValueError (past 4300) that a longer one would raise.
    return int(text) if text.isascii() and text.isdigit() and len(text) <= 15 else None


def _retry_after(headers: Any) -> float | None:
    seconds = _ascii_number(headers.get("Retry-After") if headers is not None else None)
    return None if seconds is None else float(seconds)


def _from_http(exc: urllib.error.HTTPError) -> PlaudError:
    code = exc.code
    if code in _REDIRECTS:
        return PlaudRedirectRefused()
    if code == 401:
        return PlaudAuthError()
    if code == 403:
        return PlaudForbidden()
    if code == 429:
        return PlaudRateLimited(_retry_after(exc.headers))
    if code == 408 or code >= 500:
        return PlaudUnavailable()
    if code == 404:
        return PlaudNotFound()
    return PlaudBadResponse()


def _from_refresh(exc: urllib.error.HTTPError) -> PlaudError:
    """The token endpoint's answers (§10.3): any 4xx but 408 and 429 refuses the refresh (S4 would
    have named the exact codes, typically 400 or 401 with `invalid_grant`). 408, 429 and 5xx are
    an outage, never a sign-in problem."""
    code = exc.code
    if code in _REDIRECTS:
        return PlaudRedirectRefused()
    if code == 429:
        return PlaudRateLimited(_retry_after(exc.headers))
    if code == 408 or code >= 500:
        return PlaudUnavailable()
    if 400 <= code < 500:
        return PlaudAuthError()
    return PlaudBadResponse()


def _from_link(exc: urllib.error.HTTPError) -> PlaudError:
    """A presigned link's answers: a stale signature is PlaudLinkExpired (§10.2), never the API's
    sign-in or 403; the rest (404, 408, 429, 5xx) read as the API's do."""
    if exc.code in _LINK_EXPIRED:
        return PlaudLinkExpired()
    return _from_http(exc)


def _from_transport(
    exc: BaseException, on_short_body: type[PlaudError], *, stall: type[PlaudError] | None = None
) -> PlaudError:
    """A failure below HTTP (§10.2): a body cut short is `on_short_body`; a timeout while reading
    a body is `stall` when one is given; no connection at all (refused, unreachable, no DNS) is
    "offline"; a timeout, a reset, TLS or a broken reply is "Plaud unavailable"."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, http.client.IncompleteRead):
        return on_short_body()
    if stall is not None and isinstance(reason, TimeoutError):
        return stall()
    if isinstance(
        reason, TimeoutError | ConnectionResetError | ssl.SSLError | http.client.HTTPException
    ):
        return PlaudUnavailable()
    if isinstance(reason, OSError):
        return PlaudOffline()
    return PlaudUnavailable()


_CAUGHT = (PlaudError, urllib.error.URLError, http.client.HTTPException, OSError, ValueError)


def _guarded(
    call: Callable[[], Any],
    on_short_body: type[PlaudError],
    *,
    stall: type[PlaudError] | None = None,
    from_http: Callable[[urllib.error.HTTPError], PlaudError] = _from_http,
) -> Any:
    """Run one network call (opening a request, or reading its body) and turn whatever it raises
    into this module's own error, raised after the handler that caught it, so nothing chained to
    it holds a URL. Only network calls go through here: a local file error is not "offline"."""
    error: PlaudError
    try:
        return call()
    except PlaudError as exc:  # a refused link redirect, raised in _HttpsRedirect
        error = exc
    except urllib.error.HTTPError as exc:
        error = from_http(exc)
        exc.close()
    except _CAUGHT as exc:
        error = _from_transport(exc, on_short_body, stall=stall)
    raise error from None


_sleep = time.sleep  # a Throttle's waits when none is injected (the core tests skip them)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Throttle:
    """Pacing, the budget, and riding out Plaud's bad moments (§10.2): every request the client
    sends goes through `call`. One per process, shared by its clients, so "one call at a time, at
    most one a second" holds across them. Its clocks, its sleep, its randomness and its progress
    hook are injected: tests never really wait.

    - `monotonic` paces and measures the budget; `clock` (UTC) says when a rate limit ends.
    - `progress` is called at every slice of a wait and every MiB of a download, and may raise to
      stop the wait (the worker's SIGTERM). Counters: `calls` sent, `retries` after a 5xx or a
      timeout, `rate_limits` (429s answered), for the run's record and the sweep's cap (§14.2).
    """

    def __init__(
        self,
        *,
        pace: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], datetime] = _utc_now,
        rand: Callable[[], float] = random.random,
        progress: Callable[[], None] | None = None,
    ) -> None:
        self.pace = PACE if pace is None else pace
        self.monotonic, self.sleep, self.clock, self.rand = monotonic, sleep, clock, rand
        self.progress = progress
        self.calls = self.retries = self.rate_limits = 0
        self._last: float | None = None  # when the last call started
        self._deadline: float | None = None
        self._lock = threading.RLock()

    # ---- the budget ----------------------------------------------------------------------------
    @contextlib.contextmanager
    def budget(self, seconds: float = BUDGET) -> Iterator[Throttle]:
        """A scheduled run's budget (§10.2), from now: no API call starts after it, and no wait
        runs past it. Nested, the earlier deadline holds; leaving restores the one before."""
        previous = self._deadline
        deadline = self.monotonic() + seconds
        self._deadline = deadline if previous is None else min(previous, deadline)
        try:
            yield self
        finally:
            self._deadline = previous

    def left(self) -> float | None:
        """Seconds of budget left (0 when spent), or None with no budget."""
        return None if self._deadline is None else max(0.0, self._deadline - self.monotonic())

    def spent(self) -> bool:
        return self.left() == 0.0

    # ---- the one way out -----------------------------------------------------------------------
    def call(self, send: Callable[[], Any], *, budgeted: bool) -> Any:
        """Send one request (`send` raises the client's own errors), paced, retrying a 5xx or a
        timeout after a backoff and waiting out a 429 that fits. `budgeted`: an API call, which
        doesn't start once the budget is spent; a link is never stopped by it."""
        with self._lock:
            if budgeted and self.spent():
                raise BudgetSpent() from None
            unavailable = limited = 0
            while True:
                self._pace()
                self.calls += 1
                try:
                    return send()
                except PlaudUnavailable:
                    unavailable += 1
                    delay = BACKOFF * 2 ** (unavailable - 1) * (1 + JITTER * self.rand())
                    if unavailable >= TRIES or not self._fits(delay):
                        raise
                    self.retries += 1
                except PlaudRateLimited as exc:
                    self.rate_limits += 1
                    limited += 1
                    delay = exc.retry_after
                    if delay is None:
                        raise
                    if limited >= TRIES or not self._fits(delay, MAX_RETRY_AFTER):
                        exc.until = self.clock() + timedelta(seconds=delay)
                        raise
                self._wait(delay)

    def bump(self) -> None:
        if self.progress is not None:
            self.progress()

    # ---- helpers -------------------------------------------------------------------------------
    def _pace(self) -> None:
        now = self.monotonic()
        start = now if self._last is None else max(now, self._last + self.pace)
        if start > now:
            (self.sleep or _sleep)(start - now)
        self._last = start

    def _fits(self, seconds: float, ceiling: float | None = None) -> bool:
        left = self.left()
        return (left is None or seconds <= left) and (ceiling is None or seconds <= ceiling)

    def _wait(self, seconds: float) -> None:
        """Wait in WAIT_SLICE slices, bumping the progress after each (§9.2, §10.2)."""
        remaining = max(0.0, seconds)
        while remaining > 0:
            step = min(WAIT_SLICE, remaining)
            (self.sleep or _sleep)(step)
            remaining -= step
            self.bump()


_RANGE = re.compile(r"bytes ([0-9]{1,15})-([0-9]{1,15})/([0-9]{1,15})", re.ASCII)


def _content_range(value: object) -> tuple[int, int, int] | None:
    """`bytes first-last/total` as three numbers, or None."""
    found = _RANGE.fullmatch(value.strip()) if isinstance(value, str) else None
    return None if found is None else (int(found[1]), int(found[2]), int(found[3]))


def _strong_etag(value: object) -> str | None:
    """An ETag `If-Range` may carry: strong (quoted, no `W/`), visible ASCII, of a sane length."""
    if not isinstance(value, str):
        return None
    tag = value.strip()
    ok = tag.startswith('"') and tag.endswith('"') and 2 < len(tag) <= 256
    return tag if ok and tag.isascii() and tag.isprintable() else None


class PlaudClient:
    """Thin, read-only HTTP client: one request function, fixed errors, nothing logged. Every
    request goes through `throttle` (a paced one of its own when none is given)."""

    def __init__(
        self,
        tokens: TokenSource,
        *,
        api_base: str = API_BASE,
        monotonic: Callable[[], float] = time.monotonic,
        stall_seconds: float = STALL_SECONDS,
        min_rate: int = MIN_RATE,
        throttle: Throttle | None = None,
    ) -> None:
        self.tokens = tokens
        self.api_base = check_api_base(api_base)
        self.monotonic = monotonic  # the download's stall rule
        self.stall_seconds = stall_seconds
        self.min_rate = min_rate
        self.throttle = throttle if throttle is not None else Throttle()

    # -- the one request function: GET only -------------------------------------------------
    def _get(
        self,
        url: str,
        *,
        token: str | None,
        on_short_body: type[PlaudError],
    ) -> bytes:
        """One GET through the throttle: with `token`, an API call (budgeted); without, a link."""

        def send() -> bytes:
            request = urllib.request.Request(url, method="GET")
            request.add_header("User-Agent", USER_AGENT)
            opener, from_http = _LINK_OPENER, _from_link
            if token is not None:
                # Unredirected: never copied onto a redirect, which _NoRedirect refuses anyway.
                request.add_unredirected_header("Authorization", f"Bearer {token}")
                request.add_header("Accept", "application/json, text/plain, */*")
                opener, from_http = _API_OPENER, _from_http
            response = _guarded(
                lambda: opener.open(request, timeout=CONNECT_TIMEOUT),
                on_short_body,
                from_http=from_http,
            )
            with response:
                # read(amt) never raises IncompleteRead: http.client returns what it got and
                # leaves `length` above 0. So a body past the limit, or one that ended before its
                # Content-Length, is refused as a short-body error.
                body = _guarded(lambda: response.read(_MAX_BODY + 1), on_short_body)
                unfinished = bool(getattr(response, "length", None))
            if len(body) > _MAX_BODY or unfinished:
                raise on_short_body() from None
            return body

        return self.throttle.call(send, budgeted=token is not None)

    def _open_link(self, link: str, start: int, etag: str | None) -> http.client.HTTPResponse:
        """Open a presigned link for a download, through the throttle (never stopped by the
        budget): from byte `start` with `Range`, and `If-Range` when the first answer gave a
        strong ETag, so a changed object comes whole instead of spliced."""
        request = urllib.request.Request(link, method="GET")
        request.add_header("User-Agent", USER_AGENT)
        if start:
            request.add_header("Range", f"bytes={start}-")
            if etag is not None:
                request.add_header("If-Range", etag)
        return self.throttle.call(
            lambda: _guarded(
                lambda: _LINK_OPENER.open(request, timeout=CONNECT_TIMEOUT),
                IncompleteDownload,
                from_http=_from_link,
            ),
            budgeted=False,
        )

    def _signed(self, url: str, token: str) -> bytes | None:
        """One signed-in GET; None for a 401."""
        try:
            return self._get(url, token=token, on_short_body=PlaudBadResponse)
        except PlaudAuthError:
            return None

    def _probe(self, token: str) -> None:
        """After a 401, a good refresh, and a 401 again (§10.3): the cheap current-user read with
        the new token decides. A 401 there too is PlaudAuthError, the sign-in's end; if it works,
        only the one call was refused, PlaudCallRefused. Any other failure of the read (an outage)
        is raised as itself: it proves nothing about the sign-in. Always raises."""
        try:
            body = self._signed(self.api_base + USER_PATH, token)
        except PlaudNotFound:  # USER_PATH is a guess: one listing page proves the sign-in too
            page = f"{LIST_PATH}?page=1&page_size={PROBE_PAGE_SIZE}"
            body = self._signed(self.api_base + page, token)
        if body is None:
            raise PlaudAuthError() from None
        raise PlaudCallRefused() from None

    def get_json(self, path: str) -> Any:
        """GET `api_base + path` with the access token. A 401 asks the token source for a new
        token once and tries once more; a second 401 makes the current-user read (`_probe`),
        which raises PlaudAuthError or PlaudCallRefused."""
        if not path.startswith("/") or not plain_url(path):
            raise ValueError("get_json takes a path on the API host")
        url = self.api_base + path
        token = self.tokens.access_token()
        body = self._signed(url, token)
        if body is None:  # a 401: one refresh, then one more try
            token = self.tokens.refresh(token)
            body = self._signed(url, token)
            if body is None:
                self._probe(token)
        error = PlaudBadResponse()
        try:
            return json.loads(body)
        except (ValueError, RecursionError):  # broken JSON, HTML, bytes that aren't UTF-8, nesting
            pass  # raised below, outside the handler: no JSONDecodeError (it holds the body)
        raise error from None

    # -- endpoints -------------------------------------------------------------------------------
    def current_user(self) -> str | None:
        """The cheap signed-in read (§10.3, §10.5): the account's ID as `account_hash`'s 8 hex,
        or None when the answer names none. When Plaud answers 404 at USER_PATH (a guess), the
        first listing page proves the sign-in instead, its rows unread, and the account is None.
        Raises the client's errors: PlaudAuthError when the sign-in doesn't work."""
        try:
            payload = self.get_json(USER_PATH)
        except PlaudNotFound:
            self.list_page(1, PROBE_PAGE_SIZE)
            return None
        return account_hash(payload)

    def list_page(self, page: int, page_size: int = PAGE_SIZE) -> Page:
        """One page of the listing. No ID is checked against a pattern: IDs are opaque. A caller
        pages until an empty page, never stopping at a short one: a server that clamps page sizes
        would otherwise end the listing early, and erase what it never listed (§11.2)."""
        payload = self.get_json(f"{LIST_PATH}?page={int(page)}&page_size={int(page_size)}")
        if isinstance(payload, list):
            batch = payload
        elif isinstance(payload, dict):
            batch = next((payload[k] for k in _LIST_KEYS if isinstance(payload.get(k), list)), None)
        else:
            batch = None
        if batch is None:
            raise PlaudBadResponse()  # an HTML page or the wrong shape is never an empty account
        rows, malformed = [], 0
        for row in batch:
            listed_id = row.get("id") if isinstance(row, dict) else None
            if not isinstance(listed_id, str) or not listed_id.strip():
                # A missing or renamed `id` must never become "": audio-router's lesson.
                malformed += 1
                continue
            title = row.get("name")
            if not isinstance(title, str):
                malformed += 1
                title = None
            rows.append(
                Listed(
                    listed_id=listed_id.strip(),
                    title=title,
                    created_at=_parse_dt(row.get("created_at")),
                    start_at=_parse_dt(row.get("start_at")),
                    duration_ms=_duration(row.get("duration")),
                    serial=str(row.get("serial_number") or ""),
                )
            )
        return Page(rows=tuple(rows), malformed=malformed)

    def envelope(self, listed_id: str, *, enrich: bool = True) -> dict:
        """The whole files/{id} answer, for the ID exactly as listed, with block bodies behind a
        `data_link` fetched at once (their links expire within 300 seconds) unless `enrich` is
        False. The import fetches it bare, checks its title and ID, and only then calls `enrich`
        (§11.5 step 2: no block or audio of a recording whose envelope title is kept out; Phase 1
        plan, decision 37)."""
        env = self.get_json(FILE_PATH.format(quote(listed_id, safe="")))
        if not isinstance(env, dict):
            raise PlaudBadResponse()
        return self.enrich(env) if enrich else env

    def enrich(self, env: dict) -> dict:
        """Fetch the block bodies behind each `data_link` into `data_content`, in place. A block
        that fails records its error's class name in place (`_data_link_error`), so one
        unreachable block costs only itself; an error that ends the run (Plaud unavailable after
        its retries, offline, rate limited) is raised instead. The budget never stops the blocks
        of an envelope already fetched."""
        enriched: list[str] = []
        for section in ("source_list", "note_list"):
            blocks = env.get(section)
            for block in blocks if isinstance(blocks, list) else []:
                if not isinstance(block, dict):
                    continue
                content = block.get("data_content")
                link = block.get("data_link")
                if (isinstance(content, str) and content.strip()) or not link:
                    continue
                try:
                    block["data_content"] = self.fetch_link(link).decode("utf-8", "replace")
                except PlaudError as exc:
                    if ends_the_run(exc):
                        raise
                    block["_data_link_error"] = type(exc).__name__
                    continue
                block["_fetched_from_data_link"] = True
                enriched.append(str(block.get("data_type") or "?"))
        meta = env.setdefault("_meta", {})
        if isinstance(meta, dict):
            meta["enriched_from_links"] = enriched
        return env

    def fetch_link(self, url: str) -> bytes:
        """A presigned link's body: checked by check_link, sent without Authorization."""
        return self._get(
            check_link(url, api_base=self.api_base), token=None, on_short_body=IncompleteDownload
        )

    def download(
        self,
        url: str,
        dest: Path,
        *,
        fresh_link: Callable[[], str] | None = None,
        admit: Callable[[int], None] | None = None,
    ) -> int:
        """Stream a presigned link to `dest`, and return its size. Complete as well as atomic: a
        body that isn't exactly Content-Length bytes, an empty one, one with no Content-Length,
        or a stall, is IncompleteDownload, and no file is left. At most Content-Length bytes are
        ever written. CPython's read(amt) returns b"" at a premature end rather than raising,
        which once let audio-router rename a 100-byte fragment into place, so the count is
        compared here.

        A connection that drops is resumed with `Range` (S11's rule, §18), and a server that sends
        the whole body instead restarts it once (see the module's notes). `admit(length)` is
        called with the Content-Length before any byte is written: §11.5's disk guard raises
        there to refuse it. `fresh_link()`, called at most once when the link has expired, returns
        a new link to the same audio (the caller fetches the envelope again, with its checks);
        without it, an expired link raises PlaudLinkExpired."""
        link = check_link(url, api_base=self.api_base)
        return _Download(self, link, Path(dest), fresh_link=fresh_link, admit=admit).run()


class _Download:
    """One download's state across its answers: the length the first one declared (which bounds
    every later one), its strong ETag, the bytes in `.part`, and how many resumes, restarts and
    fresh links it has used. Only the file side raises OSError, and it goes up unchanged."""

    def __init__(
        self,
        client: PlaudClient,
        link: str,
        dest: Path,
        *,
        fresh_link: Callable[[], str] | None,
        admit: Callable[[int], None] | None,
    ) -> None:
        self.client, self.link, self.dest = client, link, dest
        self.part = dest.with_name(dest.name + ".part")
        self.fresh_link, self.admit = fresh_link, admit
        self.expected: int | None = None
        self.etag: str | None = None
        self.written = 0
        self.start = 0.0
        self.next_mark = PROGRESS_BYTES
        self.resumes = 0
        self.restarted = self.refreshed = False

    def run(self) -> int:
        try:
            while True:
                response = self._open()
                with response:
                    mode = self._accept(response)
                    self._stream(response, mode)
                if self.written == self.expected:
                    os.replace(self.part, self.dest)
                    return self.written
                if self.resumes >= MAX_RESUMES:  # dropped once too often
                    raise IncompleteDownload() from None
                self.resumes += 1
        finally:
            with contextlib.suppress(OSError):  # cleanup must not hide the real error
                self.part.unlink(missing_ok=True)

    def _open(self) -> http.client.HTTPResponse:
        """The next answer: the whole body first, then from `written` on. An expired link is
        swapped once for `fresh_link()`. A resume refused any other way (416, say) is
        IncompleteDownload, unless the error ends the run."""
        while True:
            if self.expected is not None and self._too_slow():  # the stall rule, across resumes
                raise IncompleteDownload() from None
            error: PlaudError
            try:
                return self.client._open_link(self.link, self.written, self.etag)
            except PlaudLinkExpired as exc:
                error = exc
            except PlaudError as exc:
                if not self.written or ends_the_run(exc):
                    raise
                error = IncompleteDownload()
            if not isinstance(error, PlaudLinkExpired) or self.fresh_link is None or self.refreshed:
                raise error from None
            self.refreshed = True
            self.link = check_link(self.fresh_link(), api_base=self.client.api_base)

    def _accept(self, response: http.client.HTTPResponse) -> str:
        """Check an answer against what is written, and return the file mode for its body: "ab"
        for a 206 that continues exactly where `.part` ends, "wb" for a whole body."""
        length = _ascii_number(response.headers.get("Content-Length"))
        if self.written and response.status == 206:
            wanted = (self.written, self.expected - 1, self.expected)
            if _content_range(response.headers.get("Content-Range")) != wanted or (
                length is not None and length != self.expected - self.written
            ):
                raise IncompleteDownload() from None
            return "ab"
        # The length bounds the download (§10.1's ceiling, §11.5's disk guard): without a usable
        # one, nothing is written.
        if response.status != 200 or not length:
            raise IncompleteDownload() from None
        if self.expected is None:
            self.expected = length
            if self.admit is not None:
                self.admit(length)
            self.dest.parent.mkdir(parents=True, exist_ok=True)
            self.start = self.client.monotonic()
        elif length != self.expected:  # another object: never part of this one
            raise IncompleteDownload() from None
        if self.written:  # the whole body again: Range not honoured, or the object changed
            if self.restarted:
                raise IncompleteDownload() from None
            self.restarted, self.written = True, 0
        self.etag = _strong_etag(response.headers.get("ETag"))
        self.next_mark = PROGRESS_BYTES
        return "wb"

    def _stream(self, response: http.client.HTTPResponse, mode: str) -> None:
        """Write the body to `.part` until it ends. A connection that drops just returns, short;
        a stall (no bytes for READ_TIMEOUT), a body past its length, or a download too slow is
        IncompleteDownload. http.client ignores the length for a chunked body, so the loop also
        stops the moment a chunk would pass it."""
        with self.part.open(mode) as fh:
            while chunk := self._read(response):
                if self.written + len(chunk) > self.expected:
                    raise IncompleteDownload() from None
                fh.write(chunk)
                self.written += len(chunk)
                while self.written >= self.next_mark:
                    self.client.throttle.bump()
                    self.next_mark += PROGRESS_BYTES
                if self._too_slow():
                    raise IncompleteDownload() from None

    @staticmethod
    def _read(response: http.client.HTTPResponse) -> bytes:
        try:
            return _guarded(lambda: response.read1(_CHUNK), _Dropped, stall=IncompleteDownload)
        except (_Dropped, PlaudUnavailable, PlaudOffline):  # cut short or reset: resumed
            return b""

    def _too_slow(self) -> bool:
        """§10.1's stall rule past its first minute: under MIN_RATE on average, counted from the
        first answer, so a resume never starts the clock again. Staying above that average also
        bounds the whole download by Content-Length / MIN_RATE: §10.1's ceiling scaled from
        Content-Length, which needs no check of its own."""
        client = self.client
        elapsed = client.monotonic() - self.start
        return elapsed > client.stall_seconds and self.written / elapsed < client.min_rate


def stamp_meta(listed: Listed, tz_name: str) -> dict:
    """Resolve the recording time once, and record how (audio-router's keys), with `tool`, which
    marks a snapshot written by this app (`_meta.tool == "recordings"`, the gate's test, §11.6).
    Persisted into the snapshot so a later reindex reads it back rather than re-deriving it from a
    time zone that may since have changed; `time_source` exists because a missing time is
    otherwise indistinguishable from midnight."""
    tz = ZoneInfo(tz_name)
    source = "start_at" if listed.start_at else ("created_at" if listed.created_at else None)
    utc = listed.recorded_at
    local = utc.astimezone(tz) if utc else None
    return {
        "recorded_at_utc": utc.isoformat() if utc else None,
        "recorded_at_local": local.isoformat() if local else None,
        "stamp": local.strftime("%Y-%m-%d-%H%M") if local else None,
        "timezone": tz_name,
        "time_source": source,
        "tool": "recordings",
    }
