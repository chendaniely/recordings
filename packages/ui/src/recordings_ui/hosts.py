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
                await PlainTextResponse("unknown host", status_code=400)(scope, receive, send)
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


def _keep_to_this_origin(send: Send) -> Send:
    """Every response says `Cross-Origin-Resource-Policy: same-origin`: another site can't embed
    /media in an <audio> tag, or load the API's answers with a no-cors request (spec §15)."""
    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            message = {**message, "headers": [*message.get("headers", []), CORP]}
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
        send = _keep_to_this_origin(send)
        if scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        origin = _header(scope, b"origin")
        own = origin is None or (same_origin(scope, origin)
                                 and host_name(_header(scope, b"host") or "") in self.allowed)
        if not (own and sent_by_this_site(scope)):
            await PlainTextResponse("cross-site request refused", status_code=403)(scope, receive, send)
            return
        if (_header(scope, b"content-type") or "").strip().lower() != "application/json":
            await PlainTextResponse("send Content-Type: application/json",
                                    status_code=415)(scope, receive, send)
            return
        await self.app(scope, receive, send)
