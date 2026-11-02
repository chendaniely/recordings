"""A fake Plaud server for tests (stage 2b spec §10.6): its transport, its request log and its
sign-in.

Built like `FakeNtfy` (conftest.py): stdlib `ThreadingHTTPServer`s on 127.0.0.1, each in a daemon
thread. `api_url` stands in for Plaud's API host and `storage_url` for the storage host that
presigned links point at. This is §10.6's step 2 (links on a second port, with presigned-style
queries), step 3 (the sign-in: `FakeSignIn`, the token endpoint and the current user, with token
expiry read from the same injectable clock as the store) and step 4 (the request log: method,
path, host and header names, never a value or a body), with routes each test sets. Steps 1, 5 and
6 (the account, the listing, the faults and Dan's edits) grow with later areas.

Importable from outside tests (the live demo reuses it): no pytest at module level. Every title,
name, token and text it serves is invented.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from recordings.plaud.client import USER_PATH
from recordings.plaud.endpoint import TOKEN_PATH


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    hold: float = 0.0  # seconds to keep the connection open, sending nothing, after the body
    delay: float = 0.0  # seconds to wait, sending nothing, before the answer (a timeout)


@dataclass(frozen=True)
class LoggedRequest:
    method: str
    path: str  # without the query
    host: str  # "api" or "storage"
    header_names: tuple[str, ...]  # lower-case, sorted; never a value, never a body


Route = Callable[[dict[str, list[str]]], Response]
NOT_FOUND = Response(404, b'{"detail": "not found"}', {"Content-Type": "application/json"})
UNAUTHORIZED = Response(401, b'{"detail": "unauthorized"}', {"Content-Type": "application/json"})


def json_response(payload: object, status: int = 200) -> Response:
    return Response(status, json.dumps(payload).encode(), {"Content-Type": "application/json"})


class FakeSignIn:
    """The sign-in (§10.6 step 3). Tokens are invented (`fake-access-…`, `fake-refresh-…`) and
    expire by `clock`, the same injectable clock a test gives the token store. Each fault is
    switched on per test (§21's token-endpoint list):
    - `refuse` (a Response): every refresh is refused with it (S4's 400 `invalid_grant` by
      default when set through `refuse_refresh()`);
    - `fault` (a Response): every refresh answers it instead (a 5xx, a 429, an HTML 200, a delay);
    - `rotate`: a good refresh hands back a new refresh token and retires the old one; off, the
      reply carries none and the old one stays good;
    - `reject_new`: a refresh answers well, but its new access token is refused everywhere (a 401
      after a good refresh that the current-user read confirms);
    - `refused_paths`: API paths that answer 401 to any token (a 401 after a good refresh that
      the current-user read doesn't confirm: that one call's failure);
    - `user_path_missing`: the current-user path answers 404 (the client's fallback);
    - `required`: API routes answer 401 without a good access token."""

    def __init__(
        self,
        clock: Callable[[], datetime],
        *,
        life: timedelta = timedelta(hours=1),
        account_id: str = "synthetic-account-0001",
    ) -> None:
        self.clock, self.life, self.account_id = clock, life, account_id
        self.rotate = True
        self.required = True
        self.reject_new = False
        self.user_path_missing = False
        self.refuse: Response | None = None
        self.fault: Response | None = None
        self.refused_paths: set[str] = set()
        self.delay = 0.0  # seconds a good refresh takes (two processes refreshing at once)
        self.refreshes = 0  # good refreshes answered
        self.forms: list[tuple[str, ...]] = []  # each refresh's field names, never a value
        self._access: dict[str, datetime] = {}  # token -> expiry
        self._refresh: set[str] = set()
        self._lock = threading.Lock()

    # ---- what a test sets up ----------------------------------------------------------------
    def issue(self, *, life: timedelta | None = None) -> dict:
        """A new sign-in, as the vendor CLI's token file might hold it (`plaud login`): the keys
        audio-router reads and the ones this build guesses, plus two the importer must drop."""
        access, refresh = self._new_access(life), self._new_refresh()
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "Bearer",
            "expires_at": int(self._access[access].timestamp()),
            "email": "someone@example.invalid",  # invented: token-import drops it
            "user_id": "synthetic-user-0001",  # invented: token-import drops it
        }

    def refuse_refresh(self, status: int = 400, error: str = "invalid_grant") -> None:
        self.refuse = json_response({"error": error}, status)

    def revoke(self) -> None:
        """Every access token stops working at once (Plaud signs the server out)."""
        with self._lock:
            self._access.clear()

    def revoke_refresh(self) -> None:
        with self._lock:
            self._refresh.clear()

    def valid(self, token: str | None) -> bool:
        with self._lock:
            expiry = self._access.get(token or "")
        return expiry is not None and self.clock() < expiry

    # ---- the endpoints --------------------------------------------------------------------------
    def token_endpoint(self, form: Mapping[str, list[str]]) -> Response:
        with self._lock:
            self.forms.append(tuple(sorted(form)))
        if self.fault is not None:
            return self.fault
        if self.refuse is not None:
            return self.refuse
        grant = form.get("grant_type", [""])[0]
        old = form.get("refresh_token", [""])[0]
        with self._lock:
            known = old in self._refresh
            if known and self.rotate:
                self._refresh.discard(old)
        if grant != "refresh_token" or not known:
            return json_response({"error": "invalid_grant"}, 400)
        access = self._new_access(None, usable=not self.reject_new)
        reply: dict = {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": int(self.life.total_seconds()),
        }
        if self.rotate:
            reply["refresh_token"] = self._new_refresh()
        with self._lock:
            self.refreshes += 1
        answer = json_response(reply)
        answer.delay = self.delay
        return answer

    def user(self, token: str | None) -> Response:
        if self.user_path_missing:
            return NOT_FOUND
        if not self.valid(token):
            return UNAUTHORIZED
        return json_response({"data": {"id": self.account_id, "email": "someone@example.invalid"}})

    def allows(self, path: str, token: str | None) -> bool:
        if path in self.refused_paths:
            return False
        return not self.required or self.valid(token)

    # ---- helpers ------------------------------------------------------------------------------
    def _new_access(self, life: timedelta | None, *, usable: bool = True) -> str:
        token = "fake-access-" + secrets.token_urlsafe(18)
        if usable:
            with self._lock:
                self._access[token] = self.clock() + (life or self.life)
        return token

    def _new_refresh(self) -> str:
        token = "fake-refresh-" + secrets.token_urlsafe(18)
        with self._lock:
            self._refresh.add(token)
        return token


def _bearer(value: str | None) -> str | None:
    if value and value.startswith("Bearer "):
        return value.removeprefix("Bearer ")
    return None


class FakePlaud:
    def __init__(self) -> None:
        self.requests: list[LoggedRequest] = []
        self.sign_in: FakeSignIn | None = None  # set by `signed_in`: off, routes answer anyone
        self._routes: dict[tuple[str, str, str], Route] = {}
        self._lock = threading.Lock()
        self._servers = {host: self._serve(host) for host in ("api", "storage")}
        self.api_url = f"http://127.0.0.1:{self._servers['api'].server_port}"
        self.storage_url = f"http://127.0.0.1:{self._servers['storage'].server_port}"

    def signed_in(
        self, clock: Callable[[], datetime] | None = None, **kwargs: object
    ) -> FakeSignIn:
        """Switch the sign-in on: the token endpoint, the current user, and API routes that need
        a good access token. `clock` is the store's clock (UTC now by default)."""
        self.sign_in = FakeSignIn(clock or (lambda: datetime.now(UTC)), **kwargs)
        return self.sign_in

    def _serve(self, host: str) -> ThreadingHTTPServer:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _respond(self, parts) -> Response:
                sign_in = outer.sign_in
                if host != "api" or sign_in is None:
                    return outer._route(host, self.command, parts)
                token = _bearer(self.headers.get("Authorization"))
                if self.command == "POST" and parts.path == TOKEN_PATH:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = self.rfile.read(length).decode("ascii", "replace")
                    return sign_in.token_endpoint(parse_qs(body))
                if self.command == "GET" and parts.path == USER_PATH:
                    return sign_in.user(token)
                if not sign_in.allows(parts.path, token):
                    return UNAUTHORIZED
                return outer._route(host, self.command, parts)

            def _answer(self) -> None:
                parts = urlsplit(self.path)
                names = tuple(sorted({name.lower() for name in self.headers}))
                with outer._lock:
                    outer.requests.append(LoggedRequest(self.command, parts.path, host, names))
                response = self._respond(parts)
                if response.delay:
                    time.sleep(response.delay)
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

    def _route(self, host: str, method: str, parts) -> Response:
        with self._lock:
            route = self._routes.get((host, method, parts.path))
        return route(parse_qs(parts.query)) if route else NOT_FOUND

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
