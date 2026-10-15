"""The app answers only to its own names (spec §15: reachable only over Tailscale).

Two attacks come from websites open in your own browser, which can reach the app's address:
- **DNS rebinding:** a website points its own name at the app's address, then reads the app
  as "same origin". The request still carries the website's name in `Host`.
- **Cross-site websockets:** browsers let any page open a websocket to any address. The page
  can't read HTTP responses from the app, but Shiny's websocket would hand it a recording.
  The browser always sends the page's own `Origin`.

HostGuard refuses both: an unknown `Host`, and a websocket whose `Origin` isn't exactly the
app's own (scheme, name and port), or that the browser marks as sent by another site. WriteGuard
does the same for every request that could change data, and keeps every response to this origin.

A framed app is this origin, so its own websocket would pass every check above: every response
therefore forbids framing, and keeps the opener, the referrer and `/media`'s cache to this origin
(`_keep_to_this_origin`).

Behind a TLS-terminating reverse proxy the app sees `http` while the browser's `Origin` says
`https`. The proxy must preserve the `Host` header and be trusted for `X-Forwarded-Proto`: its
one address listed in uvicorn's `FORWARDED_ALLOW_IPS` (default: 127.0.0.1 and ::1 only), never
`*` or a range. Otherwise every websocket and every write is refused, which fails closed. A
proxy that rewrites `Host` to `127.0.0.1:8000` (nginx's default) fails open: HostGuard passes
every request, and only the exact-origin websocket check holds. uvicorn takes the scheme and
the client address from these headers, never `Host`, so nothing here trusts the client address.
"""

from __future__ import annotations

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
POLICY_VIOLATION = 1008  # RFC 6455 close code


def host_name(host: str) -> str:
    """The name in a `Host` header, lowercased, without the port or IPv6 brackets."""
    host = host.strip().lower()
    if host.startswith("["):  # [::1]:8000
        end = host.find("]")
        return host[1:end] if end > 0 else ""
    return host.partition(":")[0]


def same_origin(scope: Scope, origin: str) -> bool:
    """Whether `origin` is exactly this request's own: its scheme, then its Host header as sent.

    An origin is a scheme, a host name and a port (RFC 6454). A page at http://localhost:9999 is
    another origin than the app at http://localhost:8000, and may be another program, though its
    host name is allowed. Browsers leave a default port out of both headers, so those still match.
    Behind a TLS proxy, `scheme` is `https` only if uvicorn trusts the proxy's `X-Forwarded-Proto`
    (`FORWARDED_ALLOW_IPS`); see the module docstring.
    """
    host = (_header(scope, b"host") or "").strip().lower()
    scheme = scope.get("scheme", "http")
    scheme = {"ws": "http", "wss": "https"}.get(scheme, scheme)
    return bool(host) and origin.strip().lower() == f"{scheme}://{host}"


def sent_by_this_site(scope: Scope) -> bool:
    """False when the browser says another site sent the request: `Sec-Fetch-Site` is anything
    but `same-origin` or `none` (a typed address). `same-site` is refused too: a sibling name
    under the same domain is another origin. With no header, Origin decides."""
    site = (_header(scope, b"sec-fetch-site") or "").strip().lower()
    return site in ("", "same-origin", "none")


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope["headers"]:
        if key == name:
            return value.decode("latin-1")
    return None


class HostGuard:
    """Pure ASGI middleware: refuse requests addressed to a name not in `allowed`."""

    def __init__(self, app: ASGIApp, allowed: frozenset[str]) -> None:
        self.app = app
        self.allowed = frozenset(h.lower() for h in allowed)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):  # lifespan
            await self.app(scope, receive, send)
            return
        host_ok = host_name(_header(scope, b"host") or "") in self.allowed
        if scope["type"] == "http":
            if not host_ok:
                await PlainTextResponse("unknown host", status_code=400)(
                    scope, receive, _keep_to_this_origin(scope, send)
                )
                return
        else:
            # A browser always sends Origin on a websocket. It must be the app's own, exactly,
            # and the handshake not marked as sent by another site. No Origin means no browser
            # sent it, so no website is behind it.
            origin = _header(scope, b"origin")
            origin_ok = (origin is None or same_origin(scope, origin)) and sent_by_this_site(scope)
            if not (host_ok and origin_ok):
                # Refuse the handshake without accepting (the server answers 403).
                if (await receive())["type"] == "websocket.connect":
                    await send({"type": "websocket.close", "code": POLICY_VIOLATION})
                return
        await self.app(scope, receive, send)


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CORP = (b"cross-origin-resource-policy", b"same-origin")
# A framed app is this origin, so its websocket passes every check above: a page that framed it
# under a decoy could steer Dan's clicks (clickjacking). CSP for current browsers, X-Frame-Options
# for older ones.
NO_FRAMING = (
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"x-frame-options", b"DENY"),
)
# A page that opens the app in a window doesn't share its browsing context group (and so its
# process); a link in a note doesn't send the app's address to the site it opens.
NO_OPENER_OR_REFERRER = (
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"referrer-policy", b"same-origin"),
)
# /media only: a private recording's audio would otherwise sit in the disk cache of every device
# that played it. Range requests still seek.
NO_STORE = (b"cache-control", b"no-store")


def _keep_to_this_origin(scope: Scope, send: Send) -> Send:
    """Every response keeps to this origin (spec §15). `Cross-Origin-Resource-Policy: same-origin`:
    another site can't embed /media in an <audio> tag, or load the API's answers with a no-cors
    request. `frame-ancestors 'none'` and `X-Frame-Options: DENY`: no page can frame the app.
    `Cross-Origin-Opener-Policy` and `Referrer-Policy`: see above. `/media` is `no-store`."""
    extra = [CORP, *NO_FRAMING, *NO_OPENER_OR_REFERRER]
    if scope["path"].startswith("/media/"):
        extra.append(NO_STORE)

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            message = {**message, "headers": [*message.get("headers", []), *extra]}
        await send(message)

    return wrapped


class WriteGuard:
    """Every request that could change data (spec §15): from this app's own page, or from no
    browser at all, and exactly JSON. Every response also keeps to this origin (above).

    - **Who sent it.** A browser names the sending page in `Origin`, and marks a request another
      site sent with `Sec-Fetch-Site: cross-site` or `same-site`. Either refuses it, and so does
      an `Origin` that isn't exactly the app's own (same_origin). Without both headers, no browser
      sent it, so no website is behind it.
    - **What it carries.** `Content-Type: application/json` can't be sent cross-site without a
      CORS preflight, which this app never answers.
    - **Stage 5's uploads** will be the one exception: an allow-list of routes, written down here
      now. The upload route accepts `application/octet-stream`, on that path only, and only with
      a custom header (`X-Recordings-Upload: 1`, which forces a preflight) and
      `Sec-Fetch-Site: same-origin`. Nothing else is exempt.
    """

    def __init__(self, app: ASGIApp, allowed: frozenset[str]) -> None:
        self.app = app
        self.allowed = frozenset(h.lower() for h in allowed)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        send = _keep_to_this_origin(scope, send)
        if scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        origin = _header(scope, b"origin")
        own = origin is None or (
            same_origin(scope, origin) and host_name(_header(scope, b"host") or "") in self.allowed
        )
        if not (own and sent_by_this_site(scope)):
            await PlainTextResponse("cross-site request refused", status_code=403)(
                scope, receive, send
            )
            return
        if (_header(scope, b"content-type") or "").strip().lower() != "application/json":
            await PlainTextResponse("send Content-Type exactly application/json", status_code=415)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)
