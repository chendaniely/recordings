"""The Plaud client: direct, read-only HTTP against Plaud's public third-party API (stage 2b spec
§10.1).

Copied from audio-router's `src/audio_router/archive/sources/plaud.py` at commit 64612df: Dan's
own code, relicensed MIT for this repo (main spec §19). The edits, each from §10.1:
- **IDs are opaque.** audio-router's `FILE_ID_RE` filter rejected Plaud's `of_<32 hex>` IDs, which
  is why its sync stopped. Nothing here checks an ID against a pattern. Every request uses the ID
  exactly as the newest listing gave it; the canonical form is only for comparing.
- **Only reads.** One request function sends `GET`. The refresh `POST` arrives with the token store
  in Phase 2 (spikes S1, S2, S4); until then a `TokenSource` supplies the access token and its
  refresh, replacing audio-router's call to the vendor CLI.
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
  after its first minute. Past that minute, staying above the average also bounds the whole
  download by Content-Length / MIN_RATE, which is §10.1's ceiling scaled from Content-Length. A
  download needs a Content-Length (ASCII digits, not 0) and never writes more than it declares.
  Other answers are read up to 64 MiB.

Phase 2 sets `PAGE_SIZE` from S5 and adds pacing, the budget and backoff from S5 and S10 (§10.2):
this copy makes one call when asked and never waits.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote
from zoneinfo import ZoneInfo

from recordings import __version__
from recordings.plaud.endpoint import API_BASE, check_api_base, is_loopback, plain_url, split_url
from recordings.refs import canonical_plaud_id

LIST_PATH = "/open/third-party/files/"
FILE_PATH = "/open/third-party/files/{}"
PAGE_SIZE = 100  # as copied (the API rejects anything under 10). Phase 2: S5, S10.
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 60.0
STALL_SECONDS = 60.0  # no bytes for this long
MIN_RATE = 50_000  # bytes per second on average after the first minute
USER_AGENT = f"recordings/{__version__}"  # bare urllib's default gets a flat 403 from this API
_CHUNK = 64 * 1024
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
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
    message = "rate limited"

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__()
        self.retry_after = retry_after


class PlaudBadResponse(PlaudError):
    message = "Plaud's answer couldn't be read"


class PlaudRedirectRefused(PlaudError):
    message = "Plaud answered with a redirect, which is never followed"


class PlaudLinkRefused(PlaudError):
    message = "a link from Plaud was refused"


class IncompleteDownload(PlaudError):
    message = "the download was short, empty or stalled"


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
    return int(text) if text.isascii() and text.isdigit() else None


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
    return PlaudBadResponse()


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
        error = _from_http(exc)
        exc.close()
    except _CAUGHT as exc:
        error = _from_transport(exc, on_short_body, stall=stall)
    raise error from None


class PlaudClient:
    """Thin, read-only HTTP client: one request function, fixed errors, nothing logged."""

    def __init__(
        self,
        tokens: TokenSource,
        *,
        api_base: str = API_BASE,
        monotonic: Callable[[], float] = time.monotonic,
        stall_seconds: float = STALL_SECONDS,
        min_rate: int = MIN_RATE,
    ) -> None:
        self.tokens = tokens
        self.api_base = check_api_base(api_base)
        self.monotonic = monotonic
        self.stall_seconds = stall_seconds
        self.min_rate = min_rate

    # -- the one request function: GET only -------------------------------------------------
    def _get(
        self,
        url: str,
        *,
        token: str | None,
        on_short_body: type[PlaudError],
    ) -> bytes:
        request = urllib.request.Request(url, method="GET")
        request.add_header("User-Agent", USER_AGENT)
        opener = _LINK_OPENER
        if token is not None:
            # Unredirected: never copied onto a redirect, which _NoRedirect refuses anyway.
            request.add_unredirected_header("Authorization", f"Bearer {token}")
            request.add_header("Accept", "application/json, text/plain, */*")
            opener = _API_OPENER
        response = _guarded(lambda: opener.open(request, timeout=CONNECT_TIMEOUT), on_short_body)
        with response:
            # A body shorter than Content-Length raises IncompleteRead; one past the limit is
            # refused as a short-body error, whatever it declared.
            body = _guarded(lambda: response.read(_MAX_BODY + 1), on_short_body)
        if len(body) > _MAX_BODY:
            raise on_short_body() from None
        return body

    def get_json(self, path: str) -> Any:
        """GET `api_base + path` with the access token. A 401 asks the token source for a new
        token once and tries once more; a second 401 is PlaudAuthError."""
        if not path.startswith("/") or not plain_url(path):
            raise ValueError("get_json takes a path on the API host")
        url = self.api_base + path
        token = self.tokens.access_token()
        try:
            body: bytes | None = self._get(url, token=token, on_short_body=PlaudBadResponse)
        except PlaudAuthError:
            body = None
        if body is None:  # a 401: one refresh, then one more try
            body = self._get(url, token=self.tokens.refresh(token), on_short_body=PlaudBadResponse)
        error = PlaudBadResponse()
        try:
            return json.loads(body)
        except ValueError:  # broken JSON, an HTML page, bytes that aren't UTF-8
            pass  # raised below, outside the handler: no JSONDecodeError (it holds the body)
        raise error from None

    # -- endpoints -------------------------------------------------------------------------------
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

    def envelope(self, listed_id: str) -> dict:
        """The whole files/{id} answer, for the ID exactly as listed, with block bodies behind a
        `data_link` fetched at once (their links expire within 300 seconds). A block that fails
        records its error's class name in place, so one unreachable block costs only itself."""
        env = self.get_json(FILE_PATH.format(quote(listed_id, safe="")))
        if not isinstance(env, dict):
            raise PlaudBadResponse()
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

    def download(self, url: str, dest: Path) -> int:
        """Stream a presigned link to `dest`. Complete as well as atomic: a body that isn't
        exactly Content-Length bytes, an empty one, one with no Content-Length, or a stall, is
        IncompleteDownload, and no file is left. At most Content-Length bytes are ever written.
        CPython's read(amt) returns b"" at a premature
        end rather than raising, which once let audio-router rename a 100-byte fragment into
        place, so the count is compared here (S11 checks the storage host sends the length)."""
        link = check_link(url, api_base=self.api_base)
        dest = Path(dest)
        request = urllib.request.Request(link, method="GET")
        request.add_header("User-Agent", USER_AGENT)
        response = _guarded(
            lambda: _LINK_OPENER.open(request, timeout=CONNECT_TIMEOUT), IncompleteDownload
        )
        with response:
            # The length bounds the download (§10.1's ceiling, §11.5's disk guard): without a
            # usable one, nothing is written. http.client ignores it for a chunked body, so the
            # loop also stops the moment a chunk would pass it.
            expected = _ascii_number(response.headers.get("Content-Length"))
            if not expected:
                raise IncompleteDownload() from None
            # Past here only the file side raises OSError, and it goes up unchanged.
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_name(dest.name + ".part")
            written = 0
            try:
                with part.open("wb") as fh:
                    start = self.monotonic()
                    while chunk := _guarded(
                        lambda: response.read1(_CHUNK), IncompleteDownload, stall=IncompleteDownload
                    ):
                        if written + len(chunk) > expected:
                            raise IncompleteDownload() from None
                        fh.write(chunk)
                        written += len(chunk)
                        elapsed = self.monotonic() - start
                        if elapsed > self.stall_seconds and written / elapsed < self.min_rate:
                            raise IncompleteDownload() from None
                if written != expected:
                    raise IncompleteDownload() from None
                os.replace(part, dest)
            finally:
                with contextlib.suppress(OSError):  # cleanup must not hide the real error
                    part.unlink(missing_ok=True)
        return written


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
