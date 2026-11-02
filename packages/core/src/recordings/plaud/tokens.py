"""The Plaud token store (stage 2b spec §10.3, §10.4): `tokens.json` in its own folder, refreshed
before it expires and after a 401, one refresh at a time under `tokens.lock`, and written before
the new token is used.

**Only the worker holds it.** `/plaud` is mounted in `worker` alone; `recordings_ui` never imports
this module (test_plaud_imports.py), and neither does the worker's module at its top, so `web`
never loads it. `signin.py` keeps the sign-in's state (the pause, the blocker) without a token.

**The file** is our own JSON (§10.3): `version`, `access_token`, `refresh_token` (or null),
`expires_at` and `refresh_expires_at` (UTC, or null when unknown), `obtained_at`, `refreshed_at`,
and the `client_id` and `client_secret` the refresh sends when the vendor's file held them (never in
`config.toml`, which every container mounts and the backup carries). `from_vendor` keeps only these
from the vendor CLI's file, and drops the rest (an e-mail or a user ID may be in it, §10.4).

**The vendor's file** is the vendor CLI's `~/.plaud/tokens.json`, whose access token audio-router
reads as `access_token` (its `sources/plaud.py` at 64612df); the CLI is `plaud`, and audio-router
tells Dan to sign in with `plaud login`. Every other key, and the token endpoint, is a guess,
since spike S1 was skipped (docs/superpowers/plans/2026-11-01-stage-2b-guesses.md): the refresh
token as `refresh_token`, an expiry as `expires_at` (epoch seconds or milliseconds, or ISO 8601),
`expires_in` or a JWT's `exp`, whichever is earliest; and the same for the refresh token.

**Every write is atomic:** a temporary file in the same folder, created exclusively at 0600; fsync;
rename; fsync the folder. **The folder and the file** must be this user's, at most 0700 and 0600:
anything wider is refused, naming the path and the `chmod` (`TokenStoreUnsafe`).

**What a refresh's failure means** (§10.3): refused (any 4xx but 408 and 429) is PlaudAuthError,
which the run records (`signin.record_refused`: the pause and the `sign-in` blocker); an outage
(unreachable, 5xx, 408, 429, a reply that can't be read) is the client's outage class, never a
sign-in problem. Rotation is assumed, with the old refresh token refused at once (S2's strictest
answer): no `tokens.prev.json`; a crash between Plaud's reply and the write costs one sign-in
(spec §7, risk 4).

Nothing here logs, prints or returns a token except `access_token()` and `refresh()`, to the
client. `Tokens`' repr hides every secret field.
"""

from __future__ import annotations

import base64
import contextlib
import errno
import fcntl
import json
import os
import secrets
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from recordings.config import ConfigError
from recordings.locks import LockTimeout
from recordings.plaud import signin
from recordings.plaud.checks import PlaudDB
from recordings.plaud.client import PlaudAuthError, PlaudClient, post_refresh
from recordings.plaud.signin import TokenFacts
from recordings.plaud.when import parse_stamp, utc_iso

VERSION = 1
LOCK_NAME = "tokens.lock"
# A refresh takes at most the client's connect and read timeouts (70 s): another process's refresh
# is waited out, and a holder past this is stuck.
LOCK_WAIT = 90.0
REFRESH_BEFORE = timedelta(minutes=5)  # §10.3: refresh when under 5 minutes,
REFRESH_SHARE = 0.10  # or 10% of the token's life, is left
MAX_IMPORT = 64 * 1024  # a vendor token file is a few KiB; more is refused unread


class TokenStoreUnsafe(ConfigError):
    """The sign-in's folder or file is wider than 0700 or 0600, or not this user's (§10.3). Its
    text names the path and the fix, never the contents."""


class TokenImportError(Exception):
    """A vendor token file `token-import` can't take: a fixed phrase naming the key, never a
    value."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _visible(value: object) -> bool:
    """A token as it may go into a header or a form: visible ASCII, no space."""
    return (
        isinstance(value, str) and bool(value) and value.isascii() and value.isprintable()
    ) and " " not in value


def _when(value: object) -> datetime | None:
    """An absolute time: epoch seconds or milliseconds (a number, or digits), or ISO 8601. A
    smaller number is no time (a duration is `expires_in`'s)."""
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        if value > 10**12:
            value = value / 1000
        if value <= 10**9:
            return None
        try:
            return datetime.fromtimestamp(value, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    return parse_stamp(value) if isinstance(value, str) else None


def _seconds(value: object) -> float | None:
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return float(value)


def _jwt_exp(token: object) -> datetime | None:
    """A JWT's `exp`, read for its time only; None for anything else."""
    if not isinstance(token, str) or token.count(".") != 2:
        return None
    middle = token.split(".")[1]
    claims: object = None
    with contextlib.suppress(ValueError, TypeError, RecursionError):
        claims = json.loads(base64.urlsafe_b64decode(middle + "=" * (-len(middle) % 4)))
    return _when(claims.get("exp")) if isinstance(claims, dict) else None


def _earliest(*candidates: datetime | None) -> datetime | None:
    found = [c for c in candidates if c is not None]
    return min(found) if found else None


def _expiry(
    data: Mapping[str, Any],
    at: tuple[str, ...],
    within: tuple[str, ...],
    token: object,
    base: datetime,
) -> datetime | None:
    """The earliest expiry the data gives: an absolute time under `at`, a duration under `within`
    from `base`, or the token's own JWT `exp`."""
    absolute = [_when(data.get(k)) for k in at]
    relative = [base + timedelta(seconds=s) for s in (_seconds(data.get(k)) for k in within) if s]
    return _earliest(*absolute, *relative, _jwt_exp(token))


_ACCESS_AT, _ACCESS_IN = ("expires_at",), ("expires_in",)
_REFRESH_AT = ("refresh_expires_at", "refresh_token_expires_at")
_REFRESH_IN = ("refresh_expires_in", "refresh_token_expires_in")
_ISSUED = ("obtained_at", "created_at", "issued_at", "iat")


@dataclass(frozen=True)
class Tokens:
    """One sign-in. Its repr shows times only."""

    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_at: datetime | None
    obtained_at: datetime
    refreshed_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    client_id: str | None = field(default=None, repr=False)
    client_secret: str | None = field(default=None, repr=False)

    def facts(self) -> TokenFacts:
        return TokenFacts(self.expires_at, self.refresh_token is not None, self.refresh_expires_at)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "version": VERSION,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": utc_iso(self.expires_at) if self.expires_at else None,
            "refresh_expires_at": (
                utc_iso(self.refresh_expires_at) if self.refresh_expires_at else None
            ),
            "obtained_at": utc_iso(self.obtained_at),
            "refreshed_at": utc_iso(self.refreshed_at) if self.refreshed_at else None,
        }
        if self.client_id is not None:
            out["client_id"] = self.client_id
        if self.client_secret is not None:
            out["client_secret"] = self.client_secret
        return out

    @classmethod
    def from_json(cls, data: object) -> Tokens:
        """Our own file, as `to_json` writes it. Anything else is PlaudAuthError: a sign-in this
        store can't use is one Dan makes again."""
        if not isinstance(data, dict) or data.get("version") != VERSION:
            raise PlaudAuthError()
        access, refresh = data.get("access_token"), data.get("refresh_token")
        obtained = parse_stamp(data.get("obtained_at"))
        if not _visible(access) or not (refresh is None or _visible(refresh)) or obtained is None:
            raise PlaudAuthError()
        extra = {k: data.get(k) for k in ("client_id", "client_secret")}
        if any(v is not None and not _visible(v) for v in extra.values()):
            raise PlaudAuthError()
        return cls(
            access_token=access,
            refresh_token=refresh,
            expires_at=parse_stamp(data.get("expires_at")),
            obtained_at=obtained,
            refreshed_at=parse_stamp(data.get("refreshed_at")),
            refresh_expires_at=parse_stamp(data.get("refresh_expires_at")),
            **extra,
        )

    def refreshed(self, reply: Mapping[str, Any], now: datetime) -> Tokens:
        """After a refresh (§10.3): the reply's access token and expiry; its refresh token, or
        the old one when it sends none."""
        new_refresh = reply.get("refresh_token")
        rotated = _visible(new_refresh) and new_refresh != self.refresh_token
        return replace(
            self,
            access_token=reply["access_token"],
            refresh_token=new_refresh if _visible(new_refresh) else self.refresh_token,
            expires_at=_expiry(reply, _ACCESS_AT, _ACCESS_IN, reply["access_token"], now),
            refresh_expires_at=(
                _expiry(reply, _REFRESH_AT, _REFRESH_IN, new_refresh, now)
                if rotated
                else _earliest(
                    self.refresh_expires_at, _expiry(reply, _REFRESH_AT, _REFRESH_IN, None, now)
                )
            ),
            refreshed_at=now,
        )


def from_vendor(data: object, *, now: datetime) -> Tokens:
    """The vendor CLI's token file, as `token-import` takes it (§10.4, §10.5): only our own keys
    are kept. Raises TokenImportError naming what is wrong, never a value."""
    if not isinstance(data, dict):
        raise TokenImportError("the token file isn't a JSON object")
    access = data.get("access_token")
    if not _visible(access):
        raise TokenImportError("the token file has no usable access_token")
    refresh = data.get("refresh_token")
    if refresh is not None and not _visible(refresh):
        raise TokenImportError("the token file's refresh_token can't be used")
    extra = {}
    for key in ("client_id", "client_secret"):
        value = data.get(key)
        if value is not None and not _visible(value):
            raise TokenImportError(f"the token file's {key} can't be used")
        extra[key] = value
    issued = _earliest(*(_when(data.get(k)) for k in _ISSUED))
    base = issued if issued is not None and issued <= now else now
    return Tokens(
        access_token=access,
        refresh_token=refresh,
        expires_at=_expiry(data, _ACCESS_AT, _ACCESS_IN, access, base),
        obtained_at=now,
        refresh_expires_at=_expiry(data, _REFRESH_AT, _REFRESH_IN, refresh, base),
        **extra,
    )


def due(tokens: Tokens, now: datetime) -> bool:
    """§10.3: refresh when under 5 minutes, or 10% of the token's life, is left. An expiry the
    sign-in doesn't give is never due: the refresh then waits for a 401."""
    if tokens.expires_at is None:
        return False
    life = tokens.expires_at - (tokens.refreshed_at or tokens.obtained_at)
    margin = max(REFRESH_BEFORE, life * REFRESH_SHARE)
    return tokens.expires_at - now <= margin


def write_tokens(path: Path, tokens: Tokens) -> None:
    """Atomic (§10.3): a temporary file in the same folder, created exclusively at 0600, fsynced,
    renamed over the file, then the folder fsynced. Nothing is left behind on failure."""
    folder = path.parent
    tmp = folder / f".tokens-{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:  # closes fd, even when the write fails
            fh.write(json.dumps(tokens.to_json()).encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    folder_fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(folder_fd)
    finally:
        os.close(folder_fd)


def read_tokens(path: Path) -> Tokens:
    """The token file as it is, its modes unchecked (doctor reports those itself). No file, or
    one this store can't use, is PlaudAuthError."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise PlaudAuthError() from None
    data: object = None
    with contextlib.suppress(ValueError, RecursionError):
        data = json.loads(raw)
    return Tokens.from_json(data)


def check_modes(path: Path) -> None:
    """TokenStoreUnsafe naming each fault, before the file is opened (§10.3)."""
    lines = signin.unsafe_modes(path)
    if lines:
        raise TokenStoreUnsafe("; ".join(lines))


class TokenStore:
    """The token file at `path` as a TokenSource for the client (§10.3)."""

    def __init__(
        self,
        path: Path,
        *,
        token_url: str,
        clock: Callable[[], datetime] = _utc_now,
        lock_wait: float = LOCK_WAIT,
        post: Callable[[str, Mapping[str, str]], dict] = post_refresh,
    ) -> None:
        self.path, self.token_url, self.clock = Path(path), token_url, clock
        self.lock_wait, self.post = lock_wait, post

    def exists(self) -> bool:
        return self.path.is_file()

    def read(self) -> Tokens:
        """The sign-in as the file holds it now. No file, or one this store can't use, is
        PlaudAuthError; a folder or file too open is TokenStoreUnsafe."""
        check_modes(self.path)
        return read_tokens(self.path)

    def facts(self) -> TokenFacts:
        return self.read().facts()

    # ---- TokenSource --------------------------------------------------------------------------
    def access_token(self) -> str:
        """The access token, refreshed first when due."""
        tokens = self.read()
        if due(tokens, self.clock()):
            tokens = self._refresh(rejected=None)
        return tokens.access_token

    def refresh(self, rejected: str) -> str:
        """After a 401 on `rejected`: a new access token (§10.3)."""
        return self._refresh(rejected=rejected).access_token

    def _refresh(self, *, rejected: str | None) -> Tokens:
        """One refresh at a time, under `tokens.lock`. The file is read again once the lock is
        held, because the CLI and the daemon can both refresh: if another process already did
        (the token is no longer due, or no longer the one refused), its result is used and nothing
        is sent. The new file is written before the new token is returned."""
        with self._lock():
            current = self.read()
            now = self.clock()
            if rejected is None and not due(current, now):
                return current
            if rejected is not None and current.access_token != rejected:
                return current
            if current.refresh_token is None:
                raise PlaudAuthError()  # nothing to refresh with: Dan signs in again
            fields = {"grant_type": "refresh_token", "refresh_token": current.refresh_token}
            if current.client_id is not None:
                fields["client_id"] = current.client_id
            if current.client_secret is not None:
                fields["client_secret"] = current.client_secret
            reply = self.post(self.token_url, fields)
            new = current.refreshed(reply, self.clock())
            write_tokens(self.path, new)
            return new

    def install(self, tokens: Tokens) -> None:
        """`token-import`'s write, under the lock: the folder must exist and be safe."""
        if not self.path.parent.is_dir():
            raise TokenStoreUnsafe(
                f"{self.path.parent} is missing: create it with `install -d -m 700` (runbook "
                "section 6 step 2)"
            )
        check_modes(self.path)
        with self._lock():
            write_tokens(self.path, tokens)

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        """`tokens.lock` beside the file: an exclusive flock, released when the process dies."""
        fd = os.open(self.path.parent / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            deadline = time.monotonic() + self.lock_wait
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EACCES):
                        raise
                    if time.monotonic() >= deadline:
                        raise LockTimeout(
                            f"{LOCK_NAME} is held by another refresh; gave up after "
                            f"{self.lock_wait:g} s"
                        ) from None
                    time.sleep(0.05)
            yield
        finally:
            os.close(fd)  # closing drops the flock


class StaticTokens:
    """A sign-in not installed yet (`token-import`'s one cheap call): its access token, and no
    refresh, so a 401 is PlaudAuthError at once."""

    def __init__(self, tokens: Tokens) -> None:
        self._tokens = tokens

    def access_token(self) -> str:
        return self._tokens.access_token

    def refresh(self, rejected: str) -> str:
        raise PlaudAuthError()


def check_sign_in(
    store: TokenStore,
    client: PlaudClient,
    db: PlaudDB | None,
    *,
    now: datetime,
    probe: bool = False,
) -> str | None:
    """§11.1's "check the sign-in, refresh if due" at a run's start, and `recordings plaud
    check`'s read (`probe`). With `probe`, or while a refusal stands (a Sync now, or a run after a
    new token file), the cheap current-user read proves the sign-in, refreshing first if due;
    otherwise the token is only refreshed if due, and the run's first call proves it. A refusal is
    recorded (the pause and the `sign-in` blocker) and raised as PlaudAuthError; a sign-in that
    works ends both and keeps its facts for Status. Returns the account's 8 hex when the read
    named one. `db` None records nothing. A PlaudAuthError later in a run is recorded by the run:
    `signin.record_refused(db, store.path, now=…)`."""
    try:
        if probe or (db is not None and signin.refused_since(db) is not None):
            account = client.current_user()
        else:
            store.access_token()
            account = None
    except PlaudAuthError:
        if db is not None:
            signin.record_refused(db, store.path, now=now)
        raise
    if db is not None:
        signin.record_good(db, store.facts(), now=now)
    return account
