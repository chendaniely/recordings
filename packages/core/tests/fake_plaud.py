"""A fake Plaud server for tests (stage 2b spec §10.6): its transport and its request log.

Built like `FakeNtfy` (conftest.py): stdlib `ThreadingHTTPServer`s on 127.0.0.1, each in a daemon
thread. `api_url` stands in for Plaud's API host and `storage_url` for the storage host that
presigned links point at. This is §10.6's step 2 (links on a second port, with presigned-style
queries) and step 4 (the request log: method, path, host and header names, never a value or a
body), with routes each test sets. The account, the listing, the envelopes, the sign-in and the
faults (steps 1, 3, 5 and 6) need spikes S1, S5 and S8, so they come in Phase 2 (2b phase 1 plan,
decision 6). Every title, name and text a test serves is invented.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    hold: float = 0.0  # seconds to keep the connection open, sending nothing, after the body


@dataclass(frozen=True)
class LoggedRequest:
    method: str
    path: str  # without the query
    host: str  # "api" or "storage"
    header_names: tuple[str, ...]  # lower-case, sorted; never a value, never a body


Route = Callable[[dict[str, list[str]]], Response]
NOT_FOUND = Response(404, b'{"detail": "not found"}', {"Content-Type": "application/json"})


class FakePlaud:
    def __init__(self) -> None:
        self.requests: list[LoggedRequest] = []
        self._routes: dict[tuple[str, str, str], Route] = {}
        self._lock = threading.Lock()
        self._servers = {host: self._serve(host) for host in ("api", "storage")}
        self.api_url = f"http://127.0.0.1:{self._servers['api'].server_port}"
        self.storage_url = f"http://127.0.0.1:{self._servers['storage'].server_port}"

    def _serve(self, host: str) -> ThreadingHTTPServer:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                parts = urlsplit(self.path)
                names = tuple(sorted({name.lower() for name in self.headers}))
                with outer._lock:
                    outer.requests.append(LoggedRequest(self.command, parts.path, host, names))
                    route = outer._routes.get((host, self.command, parts.path))
                response = route(parse_qs(parts.query)) if route else NOT_FOUND
                self.send_response(response.status)
                for name, value in {
                    "Content-Length": str(len(response.body)),
                    **response.headers,
                }.items():
                    self.send_header(name, value)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(response.body)
                    if response.hold:
                        time.sleep(response.hold)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _answer

            def log_message(self, *args) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # A short poll, so close() returns at once: shutdown() waits for the next poll.
        threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
        return server

    def route(self, method: str, path: str, handler: Route) -> None:
        """Answer `method path` on the API host; `handler` gets the parsed query."""
        with self._lock:
            self._routes[("api", method, path)] = handler

    def link(
        self,
        name: str,
        body: bytes,
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        hold: float = 0.0,
    ) -> str:
        """Serve `body` at /<name> on the storage host, and return a presigned-style link to it.
        The signature is synthetic: no test may hold a real one. `hold` keeps the connection open
        and silent that many seconds after the body, to make a stall."""
        response = Response(status, body, dict(headers or {}), hold)
        with self._lock:
            self._routes[("storage", "GET", f"/{name}")] = lambda _query: response
        return f"{self.storage_url}/{name}?X-Amz-Signature=synthetic&X-Amz-Expires=300"

    def close(self) -> None:
        for server in self._servers.values():
            server.shutdown()
            server.server_close()
