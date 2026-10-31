"""The Plaud client (stage 2b spec §10.1), against the fake Plaud server. It only reads, uses each
ID exactly as listed, keeps the token off every link and every redirect, and raises its own fixed
errors, which carry no URL. Every ID, title, token and body here is synthetic."""

import http.client
import json
import os
import re
import socket
import subprocess
import sys
import traceback
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fake_plaud import Response
from recordings.config import ConfigError
from recordings.plaud import client as client_module
from recordings.plaud.client import (
    FILE_PATH,
    LIST_PATH,
    IncompleteDownload,
    Listed,
    PlaudAuthError,
    PlaudBadResponse,
    PlaudClient,
    PlaudError,
    PlaudForbidden,
    PlaudLinkRefused,
    PlaudOffline,
    PlaudRateLimited,
    PlaudRedirectRefused,
    PlaudUnavailable,
    check_link,
    id_hash,
    stamp_meta,
)
from recordings.plaud.endpoint import API_BASE, check_api_base

OF_ID = "of_" + f"{1:032x}"  # Plaud's form since about 2026-09-25
BARE_ID = f"{0xABC:032X}"  # the bare form, upper case
AUDIO = bytes(range(256)) * 400  # 102,400 synthetic bytes


class Tokens:
    """A TokenSource that counts its refreshes. The tokens are synthetic."""

    def __init__(self, *tokens: str) -> None:
        self.tokens = list(tokens or ("synthetic-access-1", "synthetic-access-2"))
        self.refreshed: list[str] = []

    def access_token(self) -> str:
        return self.tokens[0]

    def refresh(self, rejected: str) -> str:
        self.refreshed.append(rejected)
        if len(self.tokens) < 2:
            raise PlaudAuthError()
        self.tokens.pop(0)
        return self.tokens[0]


def _json(payload, status: int = 200) -> Response:
    return Response(status, json.dumps(payload).encode(), {"Content-Type": "application/json"})


def _row(listed_id: str, title: str = "Week 4") -> dict:
    return {
        "id": listed_id,
        "name": title,
        "start_at": "2026-08-01T16:00:00Z",
        "created_at": "2026-08-01T16:05:00Z",
        "duration": 2600,
        "serial_number": "TEST0001",
    }


def _envelope(listed_id: str, link: str | None) -> dict:
    return {
        "id": listed_id,
        "name": "Week 4",
        "presigned_url": "https://audio.example/x.mp3?X-Amz-Signature=synthetic",
        "source_list": [
            {"data_type": "transaction", "data_id": "t-1", "data_content": "", "data_link": link}
        ],
        "note_list": [
            {"data_type": "auto_sum_note", "data_id": "n-1", "data_content": "# Summary"},
        ],
    }


def _client(fake, tokens=None, **kw) -> PlaudClient:
    return PlaudClient(tokens or Tokens(), api_base=fake.api_url, **kw)


def _no_url_in(exc: BaseException, *urls: str) -> None:
    """What a log line or a traceback prints of `exc` holds none of `urls`, no presigned query and
    no token; and no urllib error, which may hold the whole URL, rides along hidden."""
    text = str(exc) + repr(exc) + "".join(traceback.format_exception_only(exc)) + repr(exc.args)
    for url in urls:
        assert url not in text
    assert "X-Amz" not in text and "synthetic-access" not in text
    assert exc.__cause__ is None and exc.__suppress_context__
    assert exc.__context__ is None or isinstance(exc.__context__, PlaudError)


def test_only_get_reaches_the_fake(plaud_server, tmp_path):
    # why: §10.1. The client only reads: no request that could change Plaud's copy is ever sent.
    rows = [_row(OF_ID)]
    plaud_server.route("GET", LIST_PATH, lambda q: _json({"data_list": rows}))
    block = plaud_server.link("block", b'[{"content": "Hello."}]')
    plaud_server.route("GET", FILE_PATH.format(OF_ID), lambda q: _json(_envelope(OF_ID, block)))
    calls = []
    plaud_server.route(
        "GET",
        "/me",
        lambda q: calls.append(1) or (_json({}, 401) if len(calls) == 1 else _json({})),
    )
    audio = plaud_server.link("audio.mp3", AUDIO)
    client = _client(plaud_server)
    page = client.list_page(1)
    client.envelope(page.rows[0].listed_id)
    client.fetch_link(block)
    client.download(audio, tmp_path / "a.mp3")
    client.get_json("/me")  # a 401, a refresh, then the retry
    assert {r.method for r in plaud_server.requests} == {"GET"}
    assert len(plaud_server.requests) == 7  # the listing, the envelope and its block, the link,
    # the download, and /me twice


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_api_calls_refuse_every_redirect(plaud_server, code):
    # why: §10.1. A redirected API call would carry the token to another host.
    elsewhere = f"{plaud_server.storage_url}/elsewhere"
    plaud_server.route("GET", LIST_PATH, lambda q: Response(code, b"", {"Location": elsewhere}))
    with pytest.raises(PlaudRedirectRefused) as info:
        _client(plaud_server).list_page(1)
    assert [r.host for r in plaud_server.requests] == ["api"]
    _no_url_in(info.value, plaud_server.api_url, elsewhere)


def test_authorization_goes_only_to_the_api_host(plaud_server, tmp_path):
    block = plaud_server.link("block", b"# Notes")
    audio = plaud_server.link("audio.mp3", AUDIO)
    plaud_server.route("GET", FILE_PATH.format(OF_ID), lambda q: _json(_envelope(OF_ID, block)))
    client = _client(plaud_server)
    client.envelope(OF_ID)
    client.download(audio, tmp_path / "a.mp3")
    api = [r for r in plaud_server.requests if r.host == "api"]
    storage = [r for r in plaud_server.requests if r.host == "storage"]
    assert api and all("authorization" in r.header_names for r in api)
    assert len(storage) == 2 and not any("authorization" in r.header_names for r in storage)


def test_the_listed_id_is_used_in_every_url(plaud_server):
    # why: §10.1, the of_ fix. A request uses the ID exactly as the newest listing gave it, never a
    # canonical or stored form: audio-router's sync broke on exactly this change.
    plaud_server.route("GET", LIST_PATH, lambda q: _json([_row(OF_ID), _row(BARE_ID)]))
    for listed_id in (OF_ID, BARE_ID):
        envelope = _envelope(listed_id, None)
        plaud_server.route("GET", FILE_PATH.format(listed_id), lambda q, e=envelope: _json(e))
    client = _client(plaud_server)
    page = client.list_page(1)
    assert [row.listed_id for row in page.rows] == [OF_ID, BARE_ID]
    assert [row.canonical_id for row in page.rows] == [f"{1:032x}", f"{0xABC:032x}"]
    for row in page.rows:
        client.envelope(row.listed_id)
    paths = [r.path for r in plaud_server.requests]
    assert paths == [LIST_PATH, FILE_PATH.format(OF_ID), FILE_PATH.format(BARE_ID)]


@pytest.mark.parametrize(
    "link",
    [
        "https://storage.example/a\nb?X-Amz-Signature=synthetic",
        "https://storage.example/a\tb?X-Amz-Signature=synthetic",
        "https://storage.example/a\x00b?X-Amz-Signature=synthetic",
        "ftp://storage.example/a?X-Amz-Signature=synthetic",
        "file:///storage.example/a?X-Amz-Signature=synthetic",
        "javascript:storage.example/X-Amz-Signature",
        "//storage.example/a?X-Amz-Signature=synthetic",
        "https://user:pw@storage.example/a?X-Amz-Signature=synthetic",
        "https://storage.example:port/a?X-Amz-Signature=synthetic",
        "https://stóráge.example/a?X-Amz-Signature=synthetic",
        7,
    ],
)
def test_a_link_with_a_control_character_or_an_unknown_scheme_raises_without_its_url(
    plaud_server, tmp_path, link
):
    # why: C7.02. A presigned link grants Dan's audio; the error that refuses it must not carry
    # it into a log, Status or a request result.
    client = _client(plaud_server)
    for call in (lambda: client.fetch_link(link), lambda: client.download(link, tmp_path / "a")):
        with pytest.raises(PlaudLinkRefused) as info:
            call()
        assert str(info.value) == "a link from Plaud was refused"
        _no_url_in(info.value, "storage.example", "stóráge")
    plaud_server.route("GET", FILE_PATH.format(OF_ID), lambda q: _json(_envelope(OF_ID, link)))
    env = client.envelope(OF_ID)
    assert env["source_list"][0]["_data_link_error"] == "PlaudLinkRefused"
    assert [r.host for r in plaud_server.requests] == ["api"]  # the link was never requested
    assert not list(tmp_path.iterdir())


def test_a_plain_http_link_is_refused_unless_the_api_is_the_loopback(plaud_server, tmp_path):
    # why: decision 7. A plain-http link would send its signature in clear. The fake's storage
    # port is plain http on 127.0.0.1, so only a loopback API base allows a loopback http link.
    link = plaud_server.link("audio.mp3", AUDIO)
    assert check_link(link, api_base=plaud_server.api_url) == link
    assert check_link("https://storage.example/a", api_base="https://api.example") is not None
    for api_base, url in [
        ("https://api.example", link),  # an https API: an http link is refused, whatever its host
        ("https://localhost:1", link),
        (plaud_server.api_url, "http://storage.example/a"),  # a loopback API, a remote http link
    ]:
        with pytest.raises(PlaudLinkRefused):
            check_link(url, api_base=api_base)
    secure = PlaudClient(Tokens(), api_base="https://api.example")
    with pytest.raises(PlaudLinkRefused):
        secure.download(link, tmp_path / "a.mp3")
    assert plaud_server.requests == []


def test_presigned_links_may_redirect_only_to_https(plaud_server):
    # why: §10.1. A link may move to another https URL (storage hosts do), never down to http.
    elsewhere = plaud_server.link("elsewhere", b"moved")
    link = plaud_server.link("block", b"", status=302, headers={"Location": elsewhere})
    with pytest.raises(PlaudLinkRefused) as info:
        _client(plaud_server).fetch_link(link)
    assert [r.path for r in plaud_server.requests] == ["/block"]  # never followed
    _no_url_in(info.value, plaud_server.storage_url)
    # The redirect handler itself: https is followed, anything else refused.
    handler = client_module._HttpsRedirect()
    request = urllib.request.Request("https://storage.example/a")
    request.add_unredirected_header("Authorization", "Bearer synthetic-access-1")  # as _get does
    headers = {"Location": "https://other.example/b"}
    moved = handler.redirect_request(
        request, _Body(), 302, "Found", headers, "https://other.example/b"
    )
    assert moved is not None and moved.full_url == "https://other.example/b"
    assert moved.get_header("Authorization") is None
    with pytest.raises(PlaudLinkRefused):
        handler.redirect_request(request, _Body(), 302, "Found", {}, "http://other.example/b")


class _Body:
    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    ("status", "headers", "error"),
    [
        (500, {}, PlaudUnavailable),
        (503, {}, PlaudUnavailable),
        (408, {}, PlaudUnavailable),
        (403, {}, PlaudForbidden),
        (404, {}, PlaudBadResponse),
        (429, {"Retry-After": "30"}, PlaudRateLimited),
    ],
)
def test_errors_are_fixed_classes_raised_from_none(plaud_server, status, headers, error):
    plaud_server.route("GET", LIST_PATH, lambda q: Response(status, b"<html>nope</html>", headers))
    with pytest.raises(error) as info:
        _client(plaud_server).list_page(1)
    assert "http" not in str(info.value)
    assert str(info.value) == error.message
    _no_url_in(info.value, plaud_server.api_url, "nope")
    if error is PlaudRateLimited:
        assert info.value.retry_after == 30.0


def test_a_timeout_is_unavailable_and_no_connection_is_offline(monkeypatch):
    class TimingOut:
        def open(self, request, timeout):
            raise urllib.error.URLError(TimeoutError("timed out"))

    with monkeypatch.context() as patch:  # never undo(): it would undo the autouse guards too
        patch.setattr(client_module, "_API_OPENER", TimingOut())
        with pytest.raises(PlaudUnavailable) as info:
            PlaudClient(Tokens(), api_base="http://127.0.0.1:9").list_page(1)
    _no_url_in(info.value, "127.0.0.1")
    with socket.socket() as probe:  # a port nothing listens on
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(PlaudOffline) as info:
        PlaudClient(Tokens(), api_base=f"http://127.0.0.1:{port}").list_page(1)
    _no_url_in(info.value, f"127.0.0.1:{port}")


def test_a_401_refreshes_once_and_retries_once(plaud_server):
    calls = []

    def first_refused(query):
        calls.append(1)
        return _json({}, 401) if len(calls) == 1 else _json([_row(OF_ID)])

    plaud_server.route("GET", LIST_PATH, first_refused)
    tokens = Tokens()
    assert len(_client(plaud_server, tokens).list_page(1).rows) == 1
    assert tokens.refreshed == ["synthetic-access-1"]
    plaud_server.route("GET", "/me", lambda q: _json({}, 401))
    tokens = Tokens()
    with pytest.raises(PlaudAuthError):
        _client(plaud_server, tokens).get_json("/me")
    assert tokens.refreshed == ["synthetic-access-1"]
    assert [r.path for r in plaud_server.requests].count("/me") == 2  # one retry, never more
    lone = Tokens("synthetic-access-1")  # a refresh the source refuses: no retry at all
    with pytest.raises(PlaudAuthError):
        _client(plaud_server, lone).get_json("/me")
    assert [r.path for r in plaud_server.requests].count("/me") == 3


def test_a_short_download_or_a_stall_raises_incomplete_download(plaud_server, tmp_path):
    # why: §10.1. CPython's read(amt) returns b"" at a premature end instead of raising, which
    # once let audio-router rename a fragment into place as a whole recording.
    cases = [
        (AUDIO[:1000], {"Content-Length": str(len(AUDIO))}),  # cut short
        (b"", {}),  # empty
        (AUDIO, {"Content-Length": ""}),  # no length to check against
    ]
    for n, (body, headers) in enumerate(cases):
        link = plaud_server.link(f"audio-{n}.mp3", body, headers=headers)
        with pytest.raises(IncompleteDownload):
            _client(plaud_server).download(link, tmp_path / "a.mp3")
        assert not list(tmp_path.iterdir())  # neither the file nor its .part
    link = plaud_server.link("long.mp3", AUDIO * 3)
    ticks = iter(range(0, 10_000, 61))  # each read is 61 seconds after the one before
    slow = _client(plaud_server, monotonic=lambda: float(next(ticks)), min_rate=10_000)
    with pytest.raises(IncompleteDownload):
        slow.download(link, tmp_path / "a.mp3")
    assert not list(tmp_path.iterdir())


def test_a_good_download_lands_whole_and_leaves_no_part(plaud_server, tmp_path):
    link = plaud_server.link("long.mp3", AUDIO * 3)
    dest = tmp_path / "audio" / "b.mp3"
    assert _client(plaud_server).download(link, dest) == len(AUDIO) * 3
    assert dest.read_bytes() == AUDIO * 3
    assert [p.name for p in dest.parent.iterdir()] == ["b.mp3"]


def test_a_failed_block_records_its_error_class_never_its_text(plaud_server):
    refused = plaud_server.link("refused", b"denied", status=403)
    good = plaud_server.link("good", b'[{"content": "Hello."}]')
    envelope = _envelope(OF_ID, good)
    envelope["note_list"] += [
        {"data_type": "auto_sum_note", "data_id": "n-2", "data_content": "", "data_link": refused},
        {"data_type": "high_light", "data_id": "h-1", "data_content": "", "data_link": "ftp://x"},
    ]
    plaud_server.route("GET", FILE_PATH.format(OF_ID), lambda q: _json(envelope))
    env = _client(plaud_server).envelope(OF_ID)
    transcript, summary, refused_note, bad_link = env["source_list"] + env["note_list"]
    assert transcript["data_content"] == '[{"content": "Hello."}]'
    assert transcript["_fetched_from_data_link"] is True
    assert summary["data_content"] == "# Summary" and "_data_link_error" not in summary
    assert refused_note["_data_link_error"] == "PlaudForbidden"
    assert bad_link["_data_link_error"] == "PlaudLinkRefused"
    assert env["_meta"]["enriched_from_links"] == ["transaction"]
    errors = json.dumps([refused_note["_data_link_error"], bad_link["_data_link_error"]])
    assert "http" not in errors and "denied" not in errors and "X-Amz" not in errors


def test_listing_rows_without_an_id_or_title_are_counted_malformed(plaud_server):
    rows = [
        _row(OF_ID),
        _row(BARE_ID),
        _row("an-opaque-id-7"),  # no pattern check: Plaud's IDs are opaque
        {"name": "no id"},
        {"id": "", "name": "an empty id"},
        {"id": 123, "name": "a number"},
        {k: v for k, v in _row(f"{2:032x}").items() if k != "name"},  # no title field
        {**_row(f"{3:032x}"), "name": None},
        "not a row",
    ]
    plaud_server.route("GET", LIST_PATH, lambda q: _json({"data_list": rows}))
    page = _client(plaud_server).list_page(1, page_size=10)
    assert [r.listed_id for r in page.rows] == [
        OF_ID,
        BARE_ID,
        "an-opaque-id-7",
        f"{2:032x}",
        f"{3:032x}",
    ]
    assert [r.title for r in page.rows][-2:] == [None, None]
    assert page.malformed == 6
    assert plaud_server.requests[0].path == LIST_PATH
    first = page.rows[0]
    assert first.duration_ms == 2600 and first.serial == "TEST0001"
    assert first.recorded_at == first.start_at and first.start_at.tzinfo is not None
    for shape in (b"<html>sign in</html>", b'{"unexpected": []}', b'"text"'):
        plaud_server.route("GET", LIST_PATH, lambda q, s=shape: Response(200, s))
        with pytest.raises(PlaudBadResponse):  # never an empty account
            _client(plaud_server).list_page(1)


@pytest.mark.parametrize(
    "url", ["https://api.example/developer/api/", "http://127.0.0.1:8123", "http://[::1]:9"]
)
def test_check_api_base_accepts_https_and_loopback_http_only(url):
    assert check_api_base(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example/developer/api",
        "https://user:pw@api.example",
        "https://api.example/api?x=1",
        "https://api.example/api#x",
        "ftp://api.example",
        "https://",
        "https://api.example:notaport",
        "https://api.example/a b",
        "https://apí.example",
        None,
        7,
    ],
)
def test_check_api_base_refuses_everything_else_naming_the_setting(url):
    with pytest.raises(ConfigError) as info:
        check_api_base(url)
    assert "[plaud] api_base" in str(info.value)
    assert "api.example" not in str(info.value) and "apí" not in str(info.value)
    with pytest.raises(ConfigError):
        PlaudClient(Tokens(), api_base=url)


def test_the_api_base_lives_where_no_network_module_loads():
    # why: spec §9.1 ("It imports no client, token or sync module"), the plan's decision 31. The
    # settings reader takes API_BASE and check_api_base from endpoint, and the web app calls it, so
    # endpoint loads neither the client nor a network module. The client takes both from it.
    assert (client_module.API_BASE, client_module.check_api_base) == (API_BASE, check_api_base)
    code = (
        "import sys, recordings.plaud.endpoint; print(sorted(m for m in ("
        "'recordings.plaud.client', 'urllib.request', 'http.client', 'ssl') if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_id_hash_and_stamp_meta():
    assert id_hash(OF_ID) == id_hash(OF_ID.upper()) == id_hash(f"{1:032x}")
    assert re.fullmatch(r"[0-9a-f]{8}", id_hash(OF_ID))
    listed = Listed(OF_ID, "Week 4", None, client_module._parse_dt("2026-08-01T16:00:00Z"), 2600)
    meta = stamp_meta(listed, "America/Vancouver")
    assert meta == {
        "recorded_at_utc": "2026-08-01T16:00:00+00:00",
        "recorded_at_local": "2026-08-01T09:00:00-07:00",
        "stamp": "2026-08-01-0900",
        "timezone": "America/Vancouver",
        "time_source": "start_at",
        "tool": "recordings",
    }
    assert stamp_meta(Listed(OF_ID, None, None, None, None), "UTC")["time_source"] is None


def test_the_client_holds_no_real_looking_id():
    # why: §9.6. The repo is public, and a copied client could carry a real recording's ID in a
    # comment or a test default. Synthetic IDs live in the tests, never in the client.
    text = Path(client_module.__file__).read_text(encoding="utf-8")
    allowed: set[str] = set()
    found = set(re.findall(r"(?i)(?:of_)?[0-9a-f]{32}", text)) | set(
        re.findall(r"(?i)of_[0-9a-f]+", text)
    )
    assert found <= allowed, sorted(found - allowed)


def test_no_test_can_reach_plaud():
    # why: decision 30. Every test uses a fake; a lookup of Plaud's host fails loudly instead.
    for host in ("platform.plaud.ai", "PLATFORM.PLAUD.AI.", b"api.plaud.ai"):
        with pytest.raises(OSError, match="tests never reach Plaud"):
            socket.getaddrinfo(host, 443)
    assert socket.getaddrinfo("127.0.0.1", 80)  # anything else still resolves


@pytest.fixture(scope="module")
def guard_seen_by_a_module_fixture():
    # A module-scoped fixture runs before any function-scoped one, so a guard that only a
    # function-scoped fixture installs would not cover it. It looks at the guard, never up a
    # Plaud host: were the guard missing, a lookup would reach the network.
    return socket.getaddrinfo.__qualname__


def test_the_guard_covers_module_scoped_fixtures_too(guard_seen_by_a_module_fixture):
    assert guard_seen_by_a_module_fixture == "_refuse_plaud.<locals>.guarded"


def test_no_test_goes_through_a_proxy():
    # why: S6. Through an https proxy, urllib hands Plaud's host name to the proxy, which looks it
    # up itself, past the guard above. The root conftest drops every *_proxy variable before the
    # client is imported, and the client builds its openers from the environment at import.
    assert not [name for name in os.environ if name.lower().endswith("_proxy")]
    for opener in (client_module._API_OPENER, client_module._LINK_OPENER):
        proxies = [h.proxies for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
        assert not any(proxies), proxies


def _chunked(body: bytes) -> bytes:
    return f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"


def test_a_download_is_bounded_by_its_content_length(plaud_server, tmp_path, monkeypatch):
    # why: §10.1's ceiling is scaled from Content-Length, and §11.5's disk guard assumes it. A
    # chunked body ignores the length in http.client, and a missing or odd length bounds nothing.
    sizes = []
    real_open = Path.open

    def spying_open(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        if self.name.endswith(".part"):
            real_write = handle.write
            handle.write = lambda data: sizes.append(len(data)) or real_write(data)
        return handle

    monkeypatch.setattr(Path, "open", spying_open)
    chunked = {"Transfer-Encoding": "chunked", "Content-Length": "10"}
    cases = [
        (_chunked(AUDIO), chunked),  # more than it declared
        (AUDIO, {"Content-Length": ""}),  # no length
        (AUDIO, {"Content-Length": "0"}),  # a zero length
        (AUDIO, {"Content-Length": "²"}),  # not an ASCII number
    ]
    for n, (body, headers) in enumerate(cases):
        link = plaud_server.link(f"bounded-{n}.mp3", body, headers=headers)
        with pytest.raises(IncompleteDownload) as info:
            _client(plaud_server).download(link, tmp_path / "a.mp3")
        _no_url_in(info.value, "127.0.0.1")
        assert not list(tmp_path.iterdir())
    assert sum(sizes) <= 10  # only the chunked case could write, and never past its length


@pytest.mark.parametrize("digit", ["²", "³", "¹"])
def test_a_header_number_is_read_in_ascii_digits_only(plaud_server, tmp_path, digit):
    # why: '²'.isdigit() is True and float() then raises a bare ValueError, whose context is an
    # HTTPError that holds the whole presigned URL. Every error here is the module's own.
    plaud_server.route("GET", LIST_PATH, lambda q: Response(429, b"", {"Retry-After": digit}))
    with pytest.raises(PlaudRateLimited) as info:
        _client(plaud_server).list_page(1)
    assert info.value.retry_after is None
    _no_url_in(info.value, plaud_server.api_url)
    limited = plaud_server.link("limited", b"", status=429, headers={"Retry-After": digit})
    for call in (
        lambda: _client(plaud_server).fetch_link(limited),
        lambda: _client(plaud_server).download(limited, tmp_path / "a.mp3"),
    ):
        with pytest.raises(PlaudRateLimited) as info:
            call()
        _no_url_in(info.value, limited, plaud_server.storage_url)
    odd = plaud_server.link("odd", AUDIO, headers={"Content-Length": digit})
    with pytest.raises(IncompleteDownload) as info:
        _client(plaud_server).download(odd, tmp_path / "a.mp3")
    _no_url_in(info.value, odd, plaud_server.storage_url)
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
def test_a_local_file_error_is_not_offline(plaud_server, tmp_path):
    # why: a read-only folder or a full disk is the archive's trouble, not a lost connection.
    folder = tmp_path / "locked"
    folder.mkdir()
    folder.chmod(0o500)
    try:
        link = plaud_server.link("audio.mp3", AUDIO)
        with pytest.raises(OSError) as info:
            _client(plaud_server).download(link, folder / "a.mp3")
        assert not isinstance(info.value, PlaudError)
    finally:
        folder.chmod(0o700)
    assert not list(folder.iterdir())


def test_a_stall_in_the_read_loop_is_an_incomplete_download(plaud_server, tmp_path, monkeypatch):
    # why: the docs say a stalled download is IncompleteDownload: no bytes for READ_TIMEOUT.
    monkeypatch.setattr(client_module, "READ_TIMEOUT", 0.3)
    link = plaud_server.link(
        "stalls.mp3", AUDIO[:1000], headers={"Content-Length": str(len(AUDIO))}, hold=3.0
    )
    with pytest.raises(IncompleteDownload) as info:
        _client(plaud_server).download(link, tmp_path / "a.mp3")
    _no_url_in(info.value, "127.0.0.1")
    assert not list(tmp_path.iterdir())


def test_a_body_past_the_read_limit_is_refused(plaud_server, monkeypatch):
    monkeypatch.setattr(client_module, "_MAX_BODY", 100)
    plaud_server.route("GET", LIST_PATH, lambda q: Response(200, b"[" + b" " * 200 + b"]"))
    with pytest.raises(PlaudBadResponse):
        _client(plaud_server).list_page(1)
    link = plaud_server.link("big", b"x" * 200)
    with pytest.raises(IncompleteDownload):
        _client(plaud_server).fetch_link(link)
    small = plaud_server.link("small", b"x" * 100)
    assert _client(plaud_server).fetch_link(small) == b"x" * 100


def test_broken_json_leaves_no_context_holding_the_body(plaud_server):
    plaud_server.route("GET", LIST_PATH, lambda q: Response(200, b"<html>sign in</html>"))
    with pytest.raises(PlaudBadResponse) as info:
        _client(plaud_server).list_page(1)
    assert info.value.__context__ is None and info.value.__cause__ is None
    _no_url_in(info.value, "sign in")


def test_connections_use_the_connect_and_read_timeouts(plaud_server, monkeypatch):
    # why: §10.1. 10 seconds to connect (the opener's timeout, which http.client keeps as
    # `timeout`), then 60 to read (set on the socket once connected).
    seen = []
    real = client_module._HTTPConnection.connect

    def spy(self):
        real(self)
        seen.append((self.timeout, self.sock.gettimeout()))

    monkeypatch.setattr(client_module._HTTPConnection, "connect", spy)
    plaud_server.route("GET", LIST_PATH, lambda q: _json([]))
    _client(plaud_server).list_page(1)
    assert seen == [(10.0, 60.0)]
    for opener in (client_module._API_OPENER, client_module._LINK_OPENER):
        kinds = {type(h) for h in opener.handlers}
        assert client_module._HTTPHandler in kinds and client_module._HTTPSHandler in kinds


@pytest.mark.parametrize("digits", [400, 5000])
def test_a_very_long_header_number_is_no_number_at_all(plaud_server, tmp_path, digits):
    # why: float(int(text)) overflows past 308 digits and int() refuses past 4300; the error's
    # context was an HTTPError holding the presigned URL.
    number = "9" * digits
    plaud_server.route("GET", LIST_PATH, lambda q: Response(429, b"", {"Retry-After": number}))
    with pytest.raises(PlaudRateLimited) as info:
        _client(plaud_server).list_page(1)
    assert info.value.retry_after is None
    _no_url_in(info.value, plaud_server.api_url)
    limited = plaud_server.link("limited", b"", status=429, headers={"Retry-After": number})
    for call in (
        lambda: _client(plaud_server).fetch_link(limited),
        lambda: _client(plaud_server).download(limited, tmp_path / "a.mp3"),
    ):
        with pytest.raises(PlaudRateLimited) as info:
            call()
        _no_url_in(info.value, limited, plaud_server.storage_url)
    odd = plaud_server.link("odd", AUDIO, headers={"Content-Length": number})
    with pytest.raises(IncompleteDownload) as info:
        _client(plaud_server).download(odd, tmp_path / "a.mp3")
    _no_url_in(info.value, odd, plaud_server.storage_url)
    assert not list(tmp_path.iterdir())


def test_a_body_cut_short_is_never_returned_as_a_whole(plaud_server):
    # why: read(amt) never raises IncompleteRead; a transcript block cut short would be stored as
    # complete, and a listing cut to "[]" would read as an empty account.
    cut = plaud_server.link("cut", b"x" * 20, headers={"Content-Length": "52"})
    with pytest.raises(IncompleteDownload) as info:
        _client(plaud_server).fetch_link(cut)
    _no_url_in(info.value, cut, plaud_server.storage_url)
    plaud_server.route("GET", LIST_PATH, lambda q: Response(200, b"[]", {"Content-Length": "500"}))
    with pytest.raises(PlaudBadResponse) as info:
        _client(plaud_server).list_page(1)
    _no_url_in(info.value, plaud_server.api_url)


def test_a_deeply_nested_body_is_a_bad_response(plaud_server, monkeypatch):
    # Whether real nesting overflows depends on the recursion limit, which another test in the run
    # may have raised; so json.loads is made to raise what a deep body makes it raise.
    def too_deep(_body):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(client_module.json, "loads", too_deep)
    plaud_server.route("GET", LIST_PATH, lambda q: Response(200, b"[[[[]]]]"))
    with pytest.raises(PlaudBadResponse) as info:
        _client(plaud_server).list_page(1)
    assert info.value.__context__ is None and info.value.__cause__ is None


def test_the_https_read_timeout_and_the_download_connect_timeout_are_pinned(
    plaud_server, tmp_path, monkeypatch
):
    # why: §10.1. The https connection sets its read timeout after the handshake, like the http
    # one, and a download connects within the same 10 seconds; deleting either must fail a test.
    def plain_connect(self):
        self.sock = socket.socket()

    monkeypatch.setattr(http.client.HTTPSConnection, "connect", plain_connect)
    connection = client_module._HTTPSConnection("storage.example")
    connection.connect()
    try:
        assert connection.sock.gettimeout() == 60.0
    finally:
        connection.sock.close()
    seen = []
    real = client_module._HTTPConnection.connect

    def spy(self):
        real(self)
        seen.append(self.timeout)

    monkeypatch.setattr(client_module._HTTPConnection, "connect", spy)
    _client(plaud_server).download(plaud_server.link("a.mp3", AUDIO), tmp_path / "a.mp3")
    assert seen == [10.0]
