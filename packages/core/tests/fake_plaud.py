"""A fake Plaud server for tests (stage 2b spec §10.6): its transport, its request log, its
sign-in and its faults.

Built like `FakeNtfy` (conftest.py): stdlib `ThreadingHTTPServer`s on 127.0.0.1, each in a daemon
thread. `api_url` stands in for Plaud's API host and `storage_url` for the storage host that
presigned links point at. This is §10.6's
- step 2's transport (links on a second port, with presigned-style queries: `link`, a `FakeLink`
  that serves its body whole or by `Range`);
- step 3 (the sign-in: `FakeSignIn`, the token endpoint and the current user, with token expiry
  read from the same injectable clock as the store);
- step 4 (the request log: method, path, host and header names, never a value or a body);
- step 5 at the transport (§21's faults on the API and on fetches): `fault` answers matching
  requests with any `Response` (a 5xx, a 429 with its Retry-After, a 401, a 403, an HTML 200, a
  short body, a timeout, a dropped connection), and a `FakeLink` drops its connection part-way,
  expires after its first answers, ignores `Range`, or trickles;
- and step 6's hook (`after`): a change to the account after request number K.
Step 1 (area A3): the account and the paged listing (`account`, a `FakeAccount`), in either ID
form, with §21's listing-shaped faults: a clamped page size, an empty listing, a shrink, a row
served twice across pages, drift while paging (a recording added through `after`), malformed rows,
the ID form flipping between bare and `of_`, an ID in a form the consent list can't name, and the
hex changing with the audio unchanged (a re-key). Step 2's envelopes (area A4): `files/{id}` for
each recording on the account, in either form, with an inline transcript, a note behind a
`data_link` and the audio behind `presigned_url`, both on the storage host (`FakeAccount.envelope`);
Dan's edits: a rename (`FakeRecording.name`), an envelope whose title differs from the listing's
(`envelope_name`: a rename between the listing and the fetch), a speaker rename
(`rename_speaker`), regenerated notes (`regenerate_notes`), and Plaud's own changes for edit-sync
(area B1): a new field the same on every fetch (`send_field`), and a field or a transcript that
differs on every fetch (`make_volatile`). The rest of step 6 grows with later areas.

Importable from outside tests (the live demo reuses it): no pytest at module level. Every title,
name, token and text it serves is invented.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import threading
import time
import wave
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from recordings.plaud.client import FILE_PATH, LIST_PATH, USER_PATH
from recordings.plaud.endpoint import TOKEN_PATH


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    hold: float = 0.0  # seconds to keep the connection open, sending nothing, after the body
    delay: float = 0.0  # seconds to wait, sending nothing, before the answer (a timeout)
    cut: int | None = None  # send only this many bytes of the body, then close (a dropped link)
    trickle: tuple[int, float] | None = None  # (bytes, seconds): the body in pieces, this slowly
    reset: bool = False  # close the connection without answering at all


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


# ---- §21's faults, as answers ---------------------------------------------------------------
def server_error(status: int = 500) -> Response:
    return json_response({"detail": "synthetic outage"}, status)


def rate_limited(retry_after: int | str | None = None) -> Response:
    """A 429, with `Retry-After` when given (seconds, or any text a test wants to try)."""
    headers = {"Content-Type": "application/json"}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return Response(429, b'{"detail": "too many requests"}', headers)


def html_page(status: int = 200) -> Response:
    """An HTML page where JSON was expected (a sign-in wall, a proxy's error page)."""
    body = b"<html><body>Synthetic page</body></html>"
    return Response(status, body, {"Content-Type": "text/html"})


def short_body(payload: object, *, missing: int = 100) -> Response:
    """JSON whose Content-Length promises `missing` more bytes than are sent."""
    answer = json_response(payload)
    answer.headers["Content-Length"] = str(len(answer.body) + missing)
    return answer


def timeout(seconds: float) -> Response:
    """No answer for `seconds`: a timeout once the client's read timeout is shorter."""
    return Response(delay=seconds)


def dropped() -> Response:
    """The connection closes with no answer at all."""
    return Response(reset=True)


# What a storage host answers for a presigned link whose signature has expired.
EXPIRED_BODY = b"<Error><Code>AccessDenied</Code><Message>Request has expired</Message></Error>"


def expired_link() -> Response:
    return Response(403, EXPIRED_BODY, {"Content-Type": "application/xml"})


Match = Callable[[str, dict[str, list[str]]], bool]


@dataclass
class Fault:
    """One injected fault (§10.6 step 5): each request on `host` that `match` (path, query)
    accepts takes the next of `responses`, the last repeating, `times` answers in all (None: every
    matching request); then requests are answered as before. `answered` counts them."""

    host: str
    match: Match
    responses: tuple[Response, ...]
    times: int | None
    answered: int = 0

    def take(self, host: str, path: str, query: dict[str, list[str]]) -> Response | None:
        spent = self.times is not None and self.answered >= self.times
        if spent or host != self.host or not self.match(path, query):
            return None
        answer = self.responses[min(self.answered, len(self.responses) - 1)]
        self.answered += 1
        return answer


_RANGE = re.compile(r"bytes=([0-9]{1,15})-", re.ASCII)


class FakeLink:
    """A presigned object on the storage host: its body, served whole, or from byte N with a 206
    for `Range: bytes=N-` (and `If-Range` against its ETag), as S3 does. Its faults (§21's "on
    fetches"), each switched on per test:
    - `ranges` off: every answer is the whole body, whatever was asked (S11 would have said which);
    - `cuts`: answer number i sends only `cuts[i]` bytes of what it would send, then the
      connection closes (dropped mid-download, truncated audio);
    - `expire_after`: from answer number `expire_after` on, every request gets the storage host's
      403 for an expired signature (a link that expires during a download, or that 403s at once);
    - `trickle`, `hold`, `status` and `headers`, as a Response's.
    `starts` records the byte each answer began at (0 for the whole body), never a header."""

    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        hold: float = 0.0,
        ranges: bool = True,
        cuts: tuple[int, ...] | list[int] = (),
        expire_after: int | None = None,
        trickle: tuple[int, float] | None = None,
    ) -> None:
        self.body, self.status, self.headers, self.hold = body, status, dict(headers or {}), hold
        self.ranges, self.cuts, self.expire_after, self.trickle = (
            ranges,
            tuple(cuts),
            expire_after,
            trickle,
        )
        self.etag = '"synthetic-' + hashlib.sha256(body).hexdigest()[:16] + '"'
        self.served = 0
        self.starts: list[int] = []
        self._lock = threading.Lock()

    def answer(self, headers: Mapping[str, str]) -> Response:
        with self._lock:
            n = self.served
            self.served += 1
        if self.expire_after is not None and n >= self.expire_after:
            return expired_link()
        if self.status != 200:
            return Response(self.status, self.body, dict(self.headers), self.hold)
        start, total = 0, len(self.body)
        asked = _RANGE.fullmatch((headers.get("Range") or "").strip())
        if self.ranges and asked and headers.get("If-Range") in (None, self.etag):
            start = int(asked[1])
            if start >= total:
                return Response(416, b"", {"Content-Range": f"bytes */{total}"})
        with self._lock:
            self.starts.append(start)
        extra = {"ETag": self.etag, "Accept-Ranges": "bytes" if self.ranges else "none"}
        if start:
            extra["Content-Range"] = f"bytes {start}-{total - 1}/{total}"
        return Response(
            206 if start else 200,
            self.body[start:],
            {**extra, **self.headers},
            self.hold,
            cut=self.cuts[n] if n < len(self.cuts) else None,
            trickle=self.trickle,
        )


# ---- §10.6 step 1: the account and the listing ----------------------------------------------
ACCOUNT_START = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)  # the first synthetic recording's start
MIN_PAGE_SIZE = 10  # audio-router: "the API rejects anything under 10" (the 400 is a guess)


def tiny_wav(seed: int, seconds: float = 0.5, rate: int = 8000) -> bytes:
    """A real, tiny WAV whose bytes differ by seed (as conftest's wav_bytes), so each synthetic
    recording has its own audio and SHA-256."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes([seed % 256, seed // 256 % 256]) * int(seconds * rate))
    return buf.getvalue()


def fake_id(n: int) -> str:
    """A synthetic Plaud ID: 32 lower-case hex, the canonical form, from a counter."""
    return hashlib.sha256(f"synthetic-plaud-recording-{n}".encode()).hexdigest()[:32]


def _stamp(when: datetime | None) -> str | None:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if when is not None else None


@dataclass
class FakeRecording:
    """One recording on the synthetic account. `plaud_id` is canonical (32 lower-case hex) unless
    `other_id` gives an ID in a form the consent list can't name. `name` None: its row has no
    title field (a malformed row the listing still lists)."""

    plaud_id: str
    name: str | None
    start_at: datetime
    duration_ms: int
    seed: int
    created_at: datetime | None = None
    serial: str = "TEST0001"
    other_id: str | None = None
    # The envelope's title when it differs from the listing's (renamed between the two); None: the
    # listing's. `envelope_untitled`: the envelope has no `name` at all.
    envelope_name: str | None = None
    envelope_untitled: bool = False
    note_link: bool = True  # the summary note comes behind a data_link (False: inline)
    # Dan's edits and Plaud's own changes, for edit-sync (area B1, §21's "content"):
    speaker: str = "Speaker 1"  # what Dan named Speaker 1 (a rename; "Speaker 1" until then)
    note_text: str | None = None  # regenerated notes (None: the first summary)
    extra: dict = field(default_factory=dict)  # fields Plaud started sending, the same each fetch
    volatile: str | None = None  # "other": a field differs on every fetch; "transcript": its text
    fetches: int = 0  # `files/{id}` answers so far (the volatile values count them)

    def listed_id(self, form: str) -> str:
        if self.other_id is not None:
            return self.other_id
        return f"of_{self.plaud_id}" if form == "of_" else self.plaud_id

    @property
    def audio(self) -> bytes:
        return tiny_wav(self.seed)

    def envelope(self, form: str, owner: FakePlaud) -> dict:
        """Its `files/{id}` answer, shaped like audio-router's raw snapshots (conftest's
        `plaud_env`): an inline transcript, a summary note behind a `data_link` and the audio behind
        `presigned_url`, both served by `owner`'s storage host (`audio/<id>.wav`,
        `notes/<id>.md`). Every word in it is invented."""
        self.fetches += 1
        words = f"Synthetic words {self.seed}."
        if self.volatile == "transcript":
            words = f"Synthetic words {self.seed}, take {self.fetches}."
        rows = [
            {
                "content": words,
                "start_time": 0,
                "end_time": 500,
                "speaker": self.speaker,
                "original_speaker": "Speaker 1",
            }
        ]
        note = self.note_text or f"# Summary\n\nSynthetic summary {self.seed}."
        note_block = {
            "data_type": "auto_sum_note",
            "data_id": f"n-{self.seed}",
            "data_tab_name": "Summary",
            "data_content": note,
        }
        if self.note_link:
            link = owner.link(f"notes/{self.plaud_id}.md", note.encode("utf-8"))
            note_block = {**note_block, "data_content": "", "data_link": link}
        env = {
            "id": self.listed_id(form),
            "duration": self.duration_ms,
            "start_at": _stamp(self.start_at),
            "created_at": _stamp(self.created_at or self.start_at),
            "serial_number": self.serial,
            "presigned_url": owner.link(f"audio/{self.plaud_id}.wav", self.audio),
            "source_list": [
                {
                    "data_type": "transaction",
                    "data_id": f"t-{self.seed}",
                    "data_tab_name": None,
                    "data_content": json.dumps(rows),
                }
            ],
            "note_list": [note_block],
            **self.extra,
        }
        if self.volatile == "other":
            env["synthetic_nonce"] = f"n-{self.seed}-{self.fetches}"
        name = self.envelope_name if self.envelope_name is not None else self.name
        if name is not None and not self.envelope_untitled:
            env["name"] = name
        return env

    def row(self, form: str) -> dict:
        row = {
            "id": self.listed_id(form),
            "duration": self.duration_ms,
            "start_at": _stamp(self.start_at),
            "created_at": _stamp(self.created_at or self.start_at),
            "serial_number": self.serial,
        }
        if self.name is not None:
            row["name"] = self.name
        return row


class FakeAccount:
    """The synthetic account behind the paged list endpoint (`GET LIST_PATH?page=&page_size=`,
    answered `{"data": [...]}` as audio-router's tests have it), newest first. Each listing-shaped
    fault is a field a test sets, or a method:
    - `form`: "of_" or "hex", how every ID is spelt; `flip()` switches it;
    - `clamp`: the most rows a page holds, whatever `page_size` asks (pages come back short);
    - `malformed`: that many rows with no `id`, listed after the recordings;
    - `twice`: canonical IDs whose row is listed again at the end (a repeat across pages);
    - `remove(id)`: a shrink; `rekey(id)`: the same recording (its audio unchanged) under new hex
      in the same form; `add(...)`, through `FakePlaud.after`, is drift while paging.
    Every title is invented."""

    def __init__(self, *, form: str = "of_", owner: FakePlaud | None = None) -> None:
        self.form = form
        self.owner = owner  # serves the envelopes' links (`FakePlaud.account` sets it)
        self.recordings: list[FakeRecording] = []
        self.clamp: int | None = None
        self.malformed = 0
        self.twice: set[str] = set()
        self._count = 0
        self._lock = threading.Lock()

    def add(
        self,
        name: str | None = None,
        *,
        start_at: datetime | None = None,
        duration_ms: int | None = None,
        plaud_id: str | None = None,
        other_id: str | None = None,
        untitled: bool = False,
    ) -> FakeRecording:
        with self._lock:
            self._count += 1
            n = self._count
        title = None if untitled else (f"Synthetic meeting {n}" if name is None else name)
        rec = FakeRecording(
            plaud_id=plaud_id or fake_id(n),
            name=title,
            start_at=start_at or ACCOUNT_START + timedelta(hours=n),
            duration_ms=duration_ms if duration_ms is not None else 60_000 + 1_000 * n,
            seed=n,
            other_id=other_id,
        )
        with self._lock:
            self.recordings.append(rec)
        return rec

    def add_many(self, count: int) -> list[FakeRecording]:
        return [self.add() for _ in range(count)]

    def find(self, plaud_id: str) -> FakeRecording:
        return next(r for r in self.recordings if r.plaud_id == plaud_id)

    def rename(self, plaud_id: str, name: str | None) -> None:
        """Dan renames it in Plaud's app (§10.6 step 6): the listing and the envelope follow."""
        rec = self.find(plaud_id)
        with self._lock:
            rec.name, rec.envelope_name = name, None

    def rename_speaker(self, plaud_id: str, name: str) -> None:
        """Dan names Speaker 1 in Plaud's app (§10.6 step 6): the envelope's rows follow."""
        rec = self.find(plaud_id)
        with self._lock:
            rec.speaker = name

    def regenerate_notes(self, plaud_id: str, text: str) -> None:
        """Dan regenerates the summary in Plaud's app: a new note body behind the same tab."""
        rec = self.find(plaud_id)
        with self._lock:
            rec.note_text = text

    def send_field(self, name: str, value: object, *, plaud_ids: list[str] | None = None) -> None:
        """Plaud starts sending a new top-level field (the same on every fetch) on these
        recordings, or every one: "other", which the breaker counts (§14.4)."""
        with self._lock:
            for rec in self.recordings:
                if plaud_ids is None or rec.plaud_id in plaud_ids:
                    rec.extra[name] = value

    def make_volatile(self, part: str | None, *, plaud_ids: list[str] | None = None) -> None:
        """A field ("other") or the transcript's text ("transcript") that differs on every fetch
        (§21: "a volatile field in other", "a transcript field that differs on every fetch");
        None stops it."""
        with self._lock:
            for rec in self.recordings:
                if plaud_ids is None or rec.plaud_id in plaud_ids:
                    rec.volatile = part

    def remove(self, plaud_id: str) -> None:
        with self._lock:
            self.recordings = [r for r in self.recordings if r.plaud_id != plaud_id]

    def restore(self, recordings: list[FakeRecording]) -> None:
        """Dan restores recordings he deleted (§10.6 step 6, §21's "a restore"): Plaud lists them
        again, as they were. One already listed is left as it is."""
        with self._lock:
            listed = {r.plaud_id for r in self.recordings}
            self.recordings += [r for r in recordings if r.plaud_id not in listed]

    def rekey(self, plaud_id: str, new_id: str | None = None) -> str:
        """The same recording, audio and times unchanged, listed under new hex (§21)."""
        rec = self.find(plaud_id)
        with self._lock:
            self._count += 1
            rec.plaud_id = new_id or fake_id(10_000_000 + self._count)
        return rec.plaud_id

    def flip(self) -> None:
        self.form = "hex" if self.form == "of_" else "of_"

    def rows(self) -> list[dict]:
        with self._lock:
            recordings = sorted(self.recordings, key=lambda r: r.start_at, reverse=True)
            form, twice, malformed = self.form, set(self.twice), self.malformed
        rows = [r.row(form) for r in recordings]
        rows += [r.row(form) for r in recordings if r.plaud_id in twice]
        rows += [{"name": f"Synthetic row with no id {i}"} for i in range(malformed)]
        return rows

    def envelope(self, listed_id: str) -> Response:
        """`GET files/{id}`, for an ID in either form; 404 for one the account doesn't hold."""
        value = listed_id.strip().lower().removeprefix("of_")
        with self._lock:
            found = next(
                (r for r in self.recordings if r.plaud_id == value or r.other_id == listed_id),
                None,
            )
            form = self.form
        if found is None or self.owner is None:
            return NOT_FOUND
        with self._lock:
            env = found.envelope(form, self.owner)
        return json_response(env)

    def listing(self, query: dict[str, list[str]]) -> Response:
        try:
            page = int(query.get("page", ["1"])[0])
            size = int(query.get("page_size", ["20"])[0])
        except ValueError:
            return json_response({"detail": "bad page"}, 400)
        if page < 1 or size < MIN_PAGE_SIZE:
            return json_response({"detail": "page_size must be at least 10"}, 400)
        if self.clamp is not None:
            size = min(size, self.clamp)
        rows = self.rows()
        return json_response({"data": rows[(page - 1) * size : page * size]})


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
        self.links: dict[str, FakeLink] = {}  # by name, as `link` made them
        self.plaud_account: FakeAccount | None = None  # set by `account`
        self.faults: list[Fault] = []
        self._routes: dict[tuple[str, str, str], Route] = {}
        self._hooks: list[tuple[int, Callable[[], None]]] = []
        self._lock = threading.Lock()
        self._servers = {host: self._serve(host) for host in ("api", "storage")}
        self.api_url = f"http://127.0.0.1:{self._servers['api'].server_port}"
        self.storage_url = f"http://127.0.0.1:{self._servers['storage'].server_port}"

    def fault(
        self,
        match: str | Match,
        *responses: Response,
        host: str = "api",
        times: int | None = None,
    ) -> Fault:
        """Inject a fault (§10.6 step 5, §21): requests on `host` to the path `match` (or that
        `match(path, query)` accepts) take `responses` in turn, the last repeating, `times`
        answers in all (None: for ever). Faults are tried in the order added, before the sign-in
        and the routes: "a failure on page K only" is `fault(lambda p, q: q.get("page") ==
        ["K"], server_error())`."""
        if not responses:
            raise ValueError("a fault needs at least one response")
        test = match if callable(match) else (lambda path, _query, want=match: path == want)
        injected = Fault(host, test, tuple(responses), times)
        with self._lock:
            self.faults.append(injected)
        return injected

    def after(self, k: int, hook: Callable[[], None]) -> None:
        """Run `hook` once, after request number `k` (counted from 1, as the request log counts)
        and before request k + 1 is answered: §10.6 step 6's change to the account mid-run."""
        with self._lock:
            self._hooks.append((k, hook))

    def _injected(self, host: str, path: str, query: dict[str, list[str]]) -> Response | None:
        with self._lock:
            for injected in self.faults:
                if (answer := injected.take(host, path, query)) is not None:
                    return answer
        return None

    def _due_hooks(self, count: int) -> list[Callable[[], None]]:
        with self._lock:
            due = [hook for k, hook in self._hooks if k + 1 == count]
            self._hooks = [(k, hook) for k, hook in self._hooks if k + 1 != count]
        return due

    def account(self, *, form: str = "of_") -> FakeAccount:
        """The synthetic account and its listing route (§10.6 step 1): `GET LIST_PATH`."""
        self.plaud_account = FakeAccount(form=form, owner=self)
        self.route("GET", LIST_PATH, self.plaud_account.listing)
        return self.plaud_account

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
                query = parse_qs(parts.query)
                if (injected := outer._injected(host, parts.path, query)) is not None:
                    return injected
                if host == "storage" and self.command == "GET":
                    with outer._lock:
                        stored = outer.links.get(parts.path.removeprefix("/"))
                    if stored is not None:
                        return stored.answer(self.headers)
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
                    count = len(outer.requests)
                for hook in outer._due_hooks(count):
                    hook()
                response = self._respond(parts)
                if response.reset:
                    self.close_connection = True
                    return
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
                    self._body(response)
                    if response.hold:
                        time.sleep(response.hold)

            def _body(self, response: Response) -> None:
                body = response.body if response.cut is None else response.body[: response.cut]
                if response.cut is not None:
                    self.close_connection = True
                if response.trickle is None:
                    self.wfile.write(body)
                    return
                size, pause = response.trickle
                for at in range(0, len(body), max(1, size)):
                    self.wfile.write(body[at : at + max(1, size)])
                    self.wfile.flush()
                    time.sleep(pause)

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
            account = self.plaud_account
        if route is not None:
            return route(parse_qs(parts.query))
        prefix = FILE_PATH.format("")
        if (
            host == "api"
            and method == "GET"
            and account is not None
            and parts.path.startswith(prefix)
            and len(parts.path) > len(prefix)
        ):
            return account.envelope(unquote(parts.path[len(prefix) :]))
        return NOT_FOUND

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
        ranges: bool = True,
        cuts: tuple[int, ...] | list[int] = (),
        expire_after: int | None = None,
        trickle: tuple[int, float] | None = None,
    ) -> str:
        """Serve `body` at /<name> on the storage host, as a `FakeLink` (`self.links[name]`), and
        return a presigned-style link to it. The signature is synthetic: no test may hold a real
        one. `hold` keeps the connection open and silent that many seconds after the body, to
        make a stall; the other faults are FakeLink's."""
        stored = FakeLink(
            body,
            status=status,
            headers=headers,
            hold=hold,
            ranges=ranges,
            cuts=cuts,
            expire_after=expire_after,
            trickle=trickle,
        )
        with self._lock:
            self.links[name] = stored
        return f"{self.storage_url}/{name}?X-Amz-Signature=synthetic&X-Amz-Expires=300"

    def close(self) -> None:
        for server in self._servers.values():
            server.shutdown()
            server.server_close()
