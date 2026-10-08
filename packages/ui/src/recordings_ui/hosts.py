"""The app answers only to its own names (spec §15: reachable only over Tailscale).

Two attacks come from websites open in your own browser, which can reach the app's address:
- **DNS rebinding:** a website points its own name at the app's address, then reads the app
  as "same origin". The request still carries the website's name in `Host`.
- **Cross-site websockets:** browsers let any page open a websocket to any address. The page
  can't read HTTP responses from the app, but Shiny's websocket would hand it a recording.
  The browser always sends the page's own `Origin`.

HostGuard refuses both: an unknown `Host`, and a websocket whose `Origin` names another site.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
POLICY_VIOLATION = 1008  # RFC 6455 close code


def host_name(host: str) -> str:
    """The name in a `Host` header, lowercased, without the port or IPv6 brackets."""
    host = host.strip().lower()
    if host.startswith("["):  # [::1]:8000
        end = host.find("]")
        return host[1:end] if end > 0 else ""
    return host.partition(":")[0]


def origin_name(origin: str) -> str:
    try:
        return urlsplit(origin).hostname or ""  # lowercased; "" for "null"
    except ValueError:  # e.g. an unclosed IPv6 bracket
        return ""


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
            # No Origin means no browser sent it, so no website is behind it.
            origin = _header(scope, b"origin")
            origin_ok = origin is None or origin_name(origin) in self.allowed
            if not (host_ok and origin_ok):
                # Refuse the handshake without accepting (the server answers 403).
                if (await receive())["type"] == "websocket.connect":
                    await send({"type": "websocket.close", "code": POLICY_VIOLATION})
                return
        await self.app(scope, receive, send)
