"""The Plaud spikes (stage 2b spec §18): supervised test plans, each run with Dan in a session.

    uv run python scripts/plaud_spike.py <spike> [--home DIR] [--production-config FILE]
        [--token-file FILE] [--id ID]... [--token-url URL] [--own-clip]

It imports `recordings`, so it runs in the workspace, with no PEP 723 block (2b phase 1 plan,
decision 24). Never in CI and never by an agent: only in Dan's sessions A (s1, s2, s4, s3), B (s5,
s7, s8, s10, s12, s11) and C (s9), from a clone of its own, as runbook section 1c says. Its tests
run it against fakes.

**The guard** is built before any Plaud call, and refuses the whole spike (exit 78) when it can't
be sure: a consent stop (the list is read fail-closed); an `[archive] path` that holds no
`archive.json`, or an archive with no recordings or with recordings it can't read (it couldn't
tell which are private); and the deployment's `config.toml` (`--production-config`, by default
`$REPO_DIR/config.toml`) unreadable. Its keep-out words are the session config's, the
deployment's, every word the worker has enforced (`state.db`, when `[state] path` is set) and the
default; its patterns are the session config's and the deployment's, so a hand copy that drifted
only adds. A spike that fetches `files/{id}` or downloads audio refuses any consent-listed ID, any
recording tagged private in the archive, any title a keep-out word matches (S9's own clip aside:
`--own-clip`, for a clip that isn't in the archive), and any title an auto-private pattern
matches, the own clip included (decision 24). A row with no title can't be checked, so it is
refused as well. The envelope's own title is checked again before any block or download (§11.5).
Every spike ends by printing how many it refused, by reason.

**What it prints** is each spike's §18 print list: counts, yes or no, times, status codes, field
names and `short()` prefixes (8 hex of a SHA-256). Never a token, a title, transcript text or a
whole Plaud ID. An error line names an exception's class, never its text, except a `ConfigError`'s:
that is written for display and names keys, never values.

**Exit codes:** 78 means "refused before any call": nothing was sent to Plaud. A stop after a
call was made (`SpikeStopped`), and any other error, exits 1.

**The spike folder** is `$RECORDINGS_HOME/spike`, mode 0700, outside the archive, the state folder,
the index, `/plaud` and every backup path. The session's sign-in (a temporary HOME, `--token-file`)
lives in it, and so does anything a spike downloads, deleted at once, and S9's record of its clip.
Dan deletes the folder at the end of the session (runbook 1c). A spike never refreshes the sign-in
behind Dan's back: S2 and S4 refresh on purpose, at the token URL Dan finds in the vendor CLI's own
refresh before session A (`--token-url`, https only).
"""

from __future__ import annotations

import argparse
import base64
import collections
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from recordings.archive import Archive, sha256_file
from recordings.config import Config, ConfigError, load_config
from recordings.ids import ID_RE
from recordings.models import Recording, is_private
from recordings.plaud.client import (
    API_BASE,
    FILE_PATH,
    LIST_PATH,
    PAGE_SIZE,
    USER_AGENT,
    Listed,
    Page,
    PlaudAuthError,
    PlaudClient,
    PlaudError,
    check_api_base,
    check_link,
    id_hash,
)
from recordings.plaud.consent import ConsentListError, consent_list_path
from recordings.plaud.keepout import DEFAULT_WORD, fold, keep_out_match, read_consent, valid_word
from recordings.plaud.normalise import part_hashes, transcript_rows
from recordings.plaud.reconcile import matches_auto_private, patterns_from_config
from recordings.refs import canonical_plaud_id
from recordings.sentinel import SentinelError, read_sentinel
from recordings.state import State

SPIKES = ("s1", "s2", "s3", "s4", "s5", "s7", "s8", "s9", "s10", "s11", "s12")
Reason = Literal["consent", "private", "no title", "keep-out", "pattern"]
REASONS: tuple[Reason, ...] = ("consent", "private", "no title", "keep-out", "pattern")
NEEDS_ID = {"s7": 3, "s8": 5, "s11": 1, "s12": 3}  # the spikes that name recordings with --id
RECENT = timedelta(hours=24)  # S9: how new a clip must be for --own-clip
EXIT_REFUSED = 78
PLAUD_MOUNT = Path("/plaud")
PAUSE = 60.0  # S5 and S8: "a minute apart"
MOMENT = 5.0  # S9: "a moment apart"
PAGE_CAP = 500  # §11.2: a listing that reaches it is bad
TIMEOUT = 60.0
SIZES = (5, 20, 50, 100, 500)  # S5's page sizes
_CODE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz_")
_SHORT = re.compile(r"[0-9a-f]{8}")  # an ID as this script prints it (S9's --id may be one)
ENFORCED_WORDS = "keep_out_words"  # plaud_state's key for the words the worker enforced (Task 6)
_LIST_KEYS = ("data", "data_list", "files", "items", "list")
sleep = time.sleep  # the tests replace it: a spike's pauses are real only in Dan's sessions


class SpikeRefused(Exception):
    """A fixed phrase, raised only before any call to Plaud: main() prints it and exits 78."""


class SpikeStopped(Exception):
    """A fixed phrase, raised after a call was made: main() prints it and exits 1."""


@dataclass
class Guard:
    consent: frozenset[str]  # canonical IDs; a consent stop raises SpikeRefused before any call
    private_ids: frozenset[str]  # canonical Plaud IDs of archive recordings tagged private
    here: frozenset[str]  # canonical Plaud IDs of every recording in the archive
    words: tuple[str, ...]
    patterns: tuple[str, ...]
    refused: collections.Counter[str] = field(default_factory=collections.Counter)

    def allows(self, plaud_id: str, title: str | None, *, own_clip: bool = False) -> bool:
        """Whether a spike may fetch or download this recording. `own_clip` (S9's clip) skips the
        keep-out check, and nothing else."""
        canonical = canonical_plaud_id(plaud_id)
        reason: Reason
        if canonical in self.consent:
            reason = "consent"
        elif canonical in self.private_ids:
            reason = "private"
        elif title is None:
            reason = "no title"  # nothing to check: refused, the safe way
        elif not own_clip and keep_out_match(title, self.words):
            reason = "keep-out"
        elif matches_auto_private([title, fold(title)], self.patterns):
            reason = "pattern"
        else:
            return True
        self.refused[reason] += 1
        return False

    def report(self) -> str:
        total = sum(self.refused.values())
        if not total:
            return "refused: 0"
        parts = ", ".join(f"{r} {self.refused[r]}" for r in REASONS if self.refused[r])
        return f"refused: {total} ({parts})"


def short(value: str) -> str:
    """8 hex of the SHA-256: the only form an ID, or any other value, is printed in. A Plaud ID is
    hashed in its canonical form (`client.id_hash`), so both of its forms print alike."""
    return id_hash(value)


def _hex32(value: str) -> bool:
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)


def _plaud_ids(rec: Recording) -> list[str]:
    return [canonical_plaud_id(s.ref) for s in rec.sources if s.kind == "plaud"]


def _section(cfg: Config, name: str) -> dict:
    value = cfg.data.get(name, {})
    return value if isinstance(value, dict) else {}


def _production(path: Path | None) -> dict:
    """The deployment's own config.toml, read for its `[plaud]` patterns and word only."""
    if path is None:
        raise SpikeRefused(
            "refused before any call: give --production-config (the deployment's config.toml, "
            "$REPO_DIR/config.toml)"
        )
    try:
        return tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise SpikeRefused(
            "refused before any call: the deployment's config.toml (--production-config) can't "
            "be read"
        ) from None


def _word(data: dict, which: str) -> str:
    plaud = data.get("plaud", {})
    word = plaud.get("keep_out_word", DEFAULT_WORD) if isinstance(plaud, dict) else None
    if not valid_word(word):
        raise SpikeRefused(f"refused: configuration: {which} `keep_out_word` is invalid")
    return word


def _enforced_words(cfg: Config) -> list[str]:
    """Every keep-out word the worker has enforced (plaud_state, Task 6's `K_KEEP_OUT_WORDS`), read
    from state.db without writing it: none when `[state] path` is unset or no worker ran yet."""
    state = State.read_only(cfg.state_path) if cfg.state_path is not None else None
    if state is None:
        return []
    try:
        with state.db() as db:
            row = db.execute(
                "SELECT value FROM plaud_state WHERE key = ?", (ENFORCED_WORDS,)
            ).fetchone()
    except sqlite3.Error as exc:
        if isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc):
            return []
        raise SpikeRefused(
            "refused before any call: state.db can't be read, so the keep-out words the sync "
            f"enforces are unknown ({type(exc).__name__})"
        ) from None
    try:
        words = json.loads(row[0]) if row else []
    except ValueError:
        words = None
    if not isinstance(words, list):
        raise SpikeRefused(
            "refused before any call: the keep-out words the sync enforces can't be read "
            "(state.db's plaud_state row is not a list)"
        )
    return [w for w in words if valid_word(w)]


def build_guard(cfg: Config, archive: Archive, production: Path | None) -> Guard:
    """The consent list (fail-closed), the archive's private recordings (refused when the path isn't
    the archive or it holds none), and every keep-out word and auto-private pattern the session's
    config, the deployment's config and the worker know, erring towards refusing."""
    try:
        path = consent_list_path(cfg.data)
    except ConsentListError:
        path = None
    consent = read_consent(path)
    if consent.stop is not None:
        raise SpikeRefused(f"refused before any call: {consent.stop}")
    try:
        read_sentinel(archive.root)
    except SentinelError:
        raise SpikeRefused(
            "refused before any call: [archive] path holds no archive.json, so it isn't the "
            "archive (is its disk mounted?), and private ones can't be known"
        ) from None
    recordings = list(archive.iter_recordings())
    if archive.problems:
        raise SpikeRefused(
            f"refused before any call: {len(archive.problems)} recordings in the archive can't "
            "be read, so it can't tell which are private (run `recordings validate`)"
        )
    if not recordings:
        raise SpikeRefused(
            "refused before any call: the archive holds no recordings, so it can't tell which "
            "are private: check [archive] path"
        )
    deployed = _production(production)
    words = [_word(cfg.data, "the session's"), _word(deployed, "the deployment's")]
    words += [*_enforced_words(cfg), DEFAULT_WORD]
    try:
        patterns = (*patterns_from_config(cfg.data), *patterns_from_config(deployed))
    except ConfigError:
        raise SpikeRefused("refused: configuration: `auto_private_patterns` is invalid") from None
    return Guard(
        consent=consent.ids,
        private_ids=frozenset(
            pid for rec in recordings if is_private(rec) for pid in _plaud_ids(rec)
        ),
        here=frozenset(pid for rec in recordings for pid in _plaud_ids(rec)),
        words=tuple(dict.fromkeys(words)),
        patterns=tuple(dict.fromkeys(patterns)),
    )


def _kept_apart(cfg: Config) -> list[Path]:
    """The folders the spike folder stays out of: the archive, the state folder, the index,
    /plaud, and the backup's paths (the archive and the state folder are backed up too)."""
    backup = _section(cfg, "backup")
    named = [backup.get("restore_scratch"), backup.get("cache_dir")]
    extra = backup.get("extra_paths", [])
    named += extra if isinstance(extra, list) else []
    paths = [cfg.archive_path, cfg.state_path, PLAUD_MOUNT]
    paths += [Path(p).expanduser() for p in named if isinstance(p, str) and p]
    if cfg.index_path is not None:
        paths.append(cfg.index_path.parent)
    return [Path(p).expanduser().absolute().resolve() for p in paths if p is not None]


def spike_folder(home: Path) -> Path:
    """`$RECORDINGS_HOME/spike` at 0700, made if missing. Refused when it is wider than 0700, or
    inside (or holding) the archive, the state folder, the index, `/plaud` or a backup path."""
    folder = Path(home).expanduser().absolute() / "spike"
    resolved = folder.resolve()
    for kept in _kept_apart(load_config(os.environ)):
        if resolved.is_relative_to(kept) or kept.is_relative_to(resolved):
            raise SpikeRefused(
                "refused: the spike folder must be outside the archive, the state folder, the "
                f"index, {PLAUD_MOUNT} and every backup path"
            )
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stat.S_IMODE(folder.stat().st_mode) & 0o077:
        raise SpikeRefused(f"refused: the spike folder must be 0700: chmod 700 {folder}")
    return folder


class FileTokens:
    """The session's sign-in, read from --token-file: the vendor CLI's token file, whose access
    token audio-router read as "access_token". A spike never refreshes behind Dan's back, so a
    401 is reported as it is."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def data(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise PlaudAuthError() from None
        if not isinstance(data, dict):
            raise PlaudAuthError()
        return data

    def access_token(self) -> str:
        token = self.data().get("access_token")
        if not isinstance(token, str) or not token:
            raise PlaudAuthError()
        return token

    def refresh(self, rejected: str) -> str:
        raise PlaudAuthError()


@dataclass
class Session:
    args: argparse.Namespace
    cfg: Config
    archive: Archive
    guard: Guard
    folder: Path
    tokens: FileTokens
    client: PlaudClient
    monotonic: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


# -- printing --------------------------------------------------------------------------------------
def say(*parts: object) -> None:
    print(*parts, flush=True)


def _error(exc: BaseException) -> str:
    return type(exc).__name__


def _yes(value: object) -> str:
    return "yes" if value else "no"


def _names(keys: Iterable[object]) -> str:
    return ", ".join(sorted(str(k) for k in keys)) or "none"


def _code(reply: dict) -> str:
    """The OAuth `error` code, only when it looks like one (never its description)."""
    code = reply.get("error")
    if isinstance(code, str) and 0 < len(code) <= 40 and set(code) <= _CODE_CHARS:
        return f", error {code}"
    return ""


def _time(value: object) -> str:
    """An expiry as a UTC time, from epoch seconds (or milliseconds) or ISO 8601; a smaller number
    is a duration in seconds. Anything else is only "present"."""
    if isinstance(value, bool):
        return "present"
    if isinstance(value, int | float):
        if value > 10**9:
            seconds = value / 1000 if value > 10**12 else value
            return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M UTC")
        return f"{int(value)} s"
    if isinstance(value, str):
        try:
            when = datetime.fromisoformat(value)
        except ValueError:
            return "present"
        when = when if when.tzinfo else when.replace(tzinfo=UTC)
        return when.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return "present"


def _expires(url: object) -> str:
    """A presigned link's X-Amz-Expires, as seconds, or "unknown"."""
    if not isinstance(url, str):
        return "unknown"
    values = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("X-Amz-Expires", [])
    return f"{values[0]} s" if values and values[0].isdigit() else "unknown"


# -- reading Plaud ---------------------------------------------------------------------------------
def _batch(payload: Any) -> tuple[str, list]:
    if isinstance(payload, list):
        return "a list", payload
    if isinstance(payload, dict):
        key = next((k for k in _LIST_KEYS if isinstance(payload.get(k), list)), None)
        return f"an object with keys {_names(payload)}", payload[key] if key else []
    return "neither a list nor an object", []


@dataclass
class Listing:
    rows: list[Listed]
    malformed: int
    pages: int

    def census(self) -> str:
        forms = collections.Counter(
            "of_"
            if row.listed_id.lower().startswith("of_")
            else "bare"
            if _hex32(row.listed_id.lower())
            else "other"
            for row in self.rows
        )
        return f"of_ {forms['of_']}, bare {forms['bare']}, other {forms['other']}"

    def by_canonical(self) -> dict[str, Listed]:
        return {row.canonical_id: row for row in self.rows}


def _paged(read: Callable[[int], Any]) -> tuple[list[Any], int]:
    """Every page until `read` answers None for an empty one, never stopping at a short one
    (§11.2): the pages' results, and the number of the empty page that ended it."""
    results = []
    for page in range(1, PAGE_CAP + 1):
        result = read(page)
        if result is None:
            return results, page
        results.append(result)
    raise SpikeStopped(f"stopped: the listing reached {PAGE_CAP} pages, which makes it bad")


def _listing(client: PlaudClient) -> Listing:
    """Every listed row, as `client.list_page` parses it."""

    def read(page: int) -> Page | None:
        result = client.list_page(page)
        return result if result.rows or result.malformed else None

    results, pages = _paged(read)
    return Listing(
        [row for result in results for row in result.rows],
        sum(result.malformed for result in results),
        pages,
    )


def _raw_rows(client: PlaudClient) -> list[dict]:
    """Every listed row as Plaud gave it (S9's listing fields, S12's times), paging alike."""

    def read(page: int) -> list | None:
        _shape, batch = _batch(client.get_json(f"{LIST_PATH}?page={page}&page_size={PAGE_SIZE}"))
        return batch or None

    results, _pages_read = _paged(read)
    return [row for batch in results for row in batch if isinstance(row, dict)]


def _files(client: PlaudClient, listed_id: str) -> dict:
    """files/{id}, without fetching the blocks behind links (S7, S9, S11)."""
    env = client.get_json(FILE_PATH.format(urllib.parse.quote(listed_id, safe="")))
    if not isinstance(env, dict):
        raise SpikeStopped("stopped: files/{id} didn't answer an object")
    return env


def _gate(session: Session, listed_id: str, title: str | None) -> bool:
    """The guard on one recording, and the "refused by the guard" line when it says no."""
    if session.guard.allows(listed_id, title, own_clip=session.args.own_clip):
        return True
    say(f"{short(listed_id)}: refused by the guard")
    return False


def _checked(session: Session, row: Listed) -> dict | None:
    """files/{id}, and the guard again on the title the envelope itself gives, before any block or
    download (§11.5: the listing's title alone isn't enough, and one fetch's check never covers a
    later fetch). None, said, when it is refused."""
    env = _files(session.client, row.listed_id)
    title = env.get("name")
    return env if _gate(session, row.listed_id, title if isinstance(title, str) else None) else None


def _same_archive(session: Session, listing: Listing) -> None:
    """The consent and private checks match by ID. When the listing and the archive share none,
    they would match nothing (a wrong archive, or an ID form that differs): stop before any
    files/{id}."""
    if not set(listing.by_canonical()) & session.guard.here:
        raise SpikeStopped(
            "stopped: the listing and the archive share no recording, so the consent and private "
            "checks would match nothing: is [archive] path this account's archive? (S7, runbook 1c)"
        )


def _resolve(session: Session, given: str) -> str:
    """--id is a Plaud ID, in any form, or an archive recording ID: then its first Plaud ID."""
    if ID_RE.fullmatch(given):
        try:
            ids = _plaud_ids(session.archive.load(given))
        except (KeyError, ValueError):
            ids = []
        if not ids:
            raise SpikeStopped(f"stopped: {short(given)} is no recording with a Plaud source")
        return ids[0]
    return canonical_plaud_id(given)


def _named(session: Session, listing: Listing, count: int) -> list[Listed]:
    """The recordings Dan named with --id, as listed, each passed by the guard."""
    found = listing.by_canonical()
    allowed = []
    for given in session.args.id[:count]:
        canonical = _resolve(session, given)
        row = found.get(canonical)
        if row is None:
            say(f"{short(canonical)}: not listed")
        elif _gate(session, row.listed_id, row.title):
            allowed.append(row)
    return allowed


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _post_form(url: str, fields: dict[str, str]) -> tuple[int | None, dict]:
    """One form POST to the token URL, never redirected: the status and the reply's JSON object,
    or (None, {}) when unreachable. Nothing of it is printed here."""
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode("ascii"),
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,  # bare urllib's gets a flat 403 from this API (client.py)
        },
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=TIMEOUT) as response:
            status, body = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read()
        exc.close()
    except (urllib.error.URLError, OSError, ValueError):
        return None, {}
    try:
        reply = json.loads(body)
    except ValueError:
        reply = {}
    return status, reply if isinstance(reply, dict) else {}


def _raw_get(url: str, headers: dict[str, str]) -> tuple[int | None, list[str]]:
    """A GET that reads at most one byte, never redirected: the status and the header names."""
    request = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=TIMEOUT) as response:
            response.read(1)
            return response.status, list(response.headers.keys())
    except urllib.error.HTTPError as exc:
        names = list(exc.headers.keys())
        exc.close()
        return exc.code, names
    except (urllib.error.URLError, OSError, ValueError):
        return None, []


def _token_url(url: str | None) -> str:
    parts = urllib.parse.urlsplit(url) if isinstance(url, str) else None
    if parts is None or parts.scheme != "https" or not parts.hostname:
        raise SpikeRefused(
            "refused: --token-url must be the https token URL, from the vendor CLI's own refresh "
            "(runbook 1c)"
        )
    return url


def _refresh_fields(data: dict, refresh_token: str) -> dict[str, str]:
    """A refresh's form: the grant, the refresh token, and the token file's `client_id` when it
    has one (S1 prints its key names), as an OAuth client sends it."""
    fields = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    client_id = data.get("client_id")
    if isinstance(client_id, str) and client_id:
        fields["client_id"] = client_id
    return fields


def _http(status: int | None) -> str:
    return "unreachable" if status is None else f"HTTP {status}"


def _refused() -> str:
    """What a refused refresh means for the session. Any refusal, with an OAuth error code or not,
    is first read as a request that differs from the one Plaud expects: compare it with the vendor
    CLI's own refresh (runbook 1c, session A step 3). Only when the two match, and the Mac refreshes
    while this server is refused, is it spec §18's "refused from the server's address" (the runbook
    says to stop and ask Dan then)."""
    return (
        "the refresh was refused: compare this request with the vendor CLI's own refresh "
        "(runbook 1c, session A step 3) before reading it as the design failing"
    )


def _refresh_start(session: Session) -> tuple[str, dict, str]:
    """S2's and S4's start: the token URL, the token file's data, and its refresh token."""
    url = _token_url(session.args.token_url)
    data = session.tokens.data()
    old = data.get("refresh_token")
    if not isinstance(old, str) or not old:
        raise SpikeRefused("refused: the sign-in has no refresh token: stop and ask Dan (S1)")
    return url, data, old


def _save_tokens(session: Session, data: dict, reply: dict) -> None:
    """Keep the session's sign-in usable after a refresh: the reply's tokens replace the old ones
    in the token file, written atomically at 0600 in the spike folder."""
    new = dict(data)
    for key in ("access_token", "refresh_token", "expires_in", "expires_at", "token_type"):
        if key in reply:
            new[key] = reply[key]
    path = session.tokens.path
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tokens-")  # created at 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(new, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _claims(token: object) -> dict | None:
    """A JWT's claims, for their names and times only; None for anything else."""
    if not isinstance(token, str) or token.count(".") != 2:
        return None
    middle = token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(middle + "=" * (-len(middle) % 4)))
    except ValueError:
        return None
    return claims if isinstance(claims, dict) else None


# -- the spikes ------------------------------------------------------------------------------------
def s1(session: Session) -> None:
    """The token's shape and lifetime: key names, token type, expiries, a refresh token."""
    try:
        data = session.tokens.data()
    except PlaudAuthError:
        raise SpikeRefused("refused: no readable token file at --token-file") from None
    say(f"token file keys: {_names(data)}")
    kind = data.get("token_type")
    say(f"token type: {kind if isinstance(kind, str) and kind.isalpha() else 'unknown'}")
    say(f"refresh token: {_yes(data.get('refresh_token'))}")
    for key in sorted(data):
        if "expire" in key.lower() or key.lower() in ("exp", "iat", "obtained_at", "created_at"):
            say(f"{key}: {_time(data[key])}")
    for name in ("access_token", "refresh_token"):
        claims = _claims(data.get(name))
        if claims is None:
            say(f"{name}: not a JWT")
            continue
        say(f"{name} claims: {_names(claims)}")
        if isinstance(claims.get("exp"), int | float):
            say(f"{name} expires: {_time(claims['exp'])}")
        if all(isinstance(claims.get(k), int | float) for k in ("exp", "iat")):
            say(f"{name} lifetime: {int(claims['exp'] - claims['iat']) // 60} min")


def s2(session: Session) -> None:
    """Refresh and rotation: one refresh, the old refresh token again, then one listing page."""
    url, data, old = _refresh_start(session)
    status, reply = _post_form(url, _refresh_fields(data, old))
    say(f"refresh: {_http(status)}{_code(reply)}")
    if status is None:
        say("the token URL didn't answer: check --token-url and the network")
        return
    if status != 200 or not reply.get("access_token"):
        say(_refused())
        return
    new = reply.get("refresh_token")
    say(f"reply keys: {_names(reply)}")
    say(f"new refresh token: {_yes(new)} · rotated: {_yes(new and new != old)}")
    if "expires_in" in reply:
        say(f"expires_in: {_time(reply['expires_in'])}")
    _save_tokens(session, data, reply)
    again, again_reply = _post_form(url, _refresh_fields(data, old))
    say(f"the old refresh token again: {_http(again)}{_code(again_reply)}")
    if again == 200 and again_reply.get("access_token"):
        _save_tokens(session, session.tokens.data(), again_reply)  # the newest pair is kept
    try:
        page = session.client.list_page(1, 10)
        say(f"one listing page with the new access token: ok, {len(page.rows)} rows")
    except PlaudError as exc:
        say(f"one listing page with the new access token: {_error(exc)}")


def s3(session: Session) -> None:
    """Does a second sign-in sign the first out? One cheap call with the session's sign-in."""
    try:
        session.client.list_page(1, 10)
        say("signed in: yes")
    except PlaudError as exc:
        say(f"signed in: no ({_error(exc)})")


def s4(session: Session) -> None:
    """A refused refresh: a copy of the refresh token with one character changed."""
    url, data, old = _refresh_start(session)
    changed = old[:-1] + ("A" if old[-1] != "A" else "B")
    status, reply = _post_form(url, _refresh_fields(data, changed))
    say(f"refused refresh: {_http(status)}{_code(reply)}")


def _pass(client: PlaudClient, n: int) -> set[str]:
    listing = _listing(client)
    ids = [row.canonical_id for row in listing.rows]
    unique = set(ids)
    say(
        f"pass {n}: {len(unique)} IDs, {len(ids) - len(unique)} repeats, {listing.malformed} "
        f"malformed, {listing.pages} pages · census {listing.census()} · "
        f"ID set {short(','.join(sorted(unique)))}"
    )
    return unique


def s5(session: Session) -> None:
    """The listing's shape, a cheap change signal, and paging."""
    for size in SIZES:
        try:
            payload = session.client.get_json(f"{LIST_PATH}?page=1&page_size={size}")
        except PlaudError as exc:
            say(f"page 1 at size {size}: {_error(exc)}")
            continue
        shape, batch = _batch(payload)
        rows = [row for row in batch if isinstance(row, dict)]
        say(f"page 1 at size {size}: {len(batch)} rows, {shape}")
        say(f"  row fields: {_names({key for row in rows for key in row})}")
        for key, value in sorted(payload.items() if isinstance(payload, dict) else []):
            if isinstance(value, int) and not isinstance(value, bool):
                say(f"  {key}: {value}")
    first = _pass(session.client, 1)
    sleep(PAUSE)
    second = _pass(session.client, 2)
    say(
        f"same ID set: {_yes(first == second)} · only in pass 1: {len(first - second)} · "
        f"only in pass 2: {len(second - first)}"
    )


def s7(session: Session) -> None:
    """The ID form: listing IDs against the archive's, the consent list, and three audio hashes.
    The archive is read itself, because the index lives in a Docker volume."""
    listing = _listing(session.client)
    listed = set(listing.by_canonical())
    here = {
        pid: rec.media.sha256
        for rec in session.archive.iter_recordings()
        for pid in _plaud_ids(rec)
    }
    say(f"listed: {len(listed)} · census {listing.census()}")
    say(
        f"in the archive: {len(listed & set(here))} · listed, not in the archive: "
        f"{len(listed - set(here))} · in the archive, not listed: {len(set(here) - listed)}"
    )
    say(f"consent-list IDs in the listing: {len(session.guard.consent & listed)}")
    _same_archive(session, listing)
    passed: list[Listed] = []  # the rows whose envelope title the guard passed
    for row in _named(session, listing, 3):
        name = short(row.canonical_id)
        if row.canonical_id not in here:
            say(f"{name}: not in the archive")
            continue
        dest = session.folder / f"s7-{name}.audio"
        try:
            env = _checked(session, row)
            if env is None:
                continue
            passed.append(row)
            link = check_link(env.get("presigned_url"), api_base=session.client.api_base)
            session.client.download(link, dest)
            say(f"{name}: audio identical: {_yes(sha256_file(dest) == here[row.canonical_id])}")
        except PlaudError as exc:
            say(f"{name}: {_error(exc)}")
        finally:
            dest.unlink(missing_ok=True)
    if passed:  # only a recording whose envelope the guard passed is fetched again
        bare = passed[0].canonical_id
        for label, form in (("of_ form", f"of_{bare}"), ("bare hex", bare)):
            try:
                _files(session.client, form)
                say(f"files/{{id}} by its {label}: ok")
            except PlaudError as exc:
                say(f"files/{{id}} by its {label}: {_error(exc)}")


def _blocks(env: dict) -> list[dict]:
    blocks = []
    for section in ("source_list", "note_list"):
        value = env.get(section)
        blocks += [b for b in value if isinstance(b, dict)] if isinstance(value, list) else []
    return blocks


def _part_label(part: str) -> str:
    """A part as it may be printed: a note tab's name is Dan's, so every tab is just "notes"."""
    return "notes" if part.startswith("notes:") else part


def _labels(parts: Iterable[str]) -> str:
    return _names({_part_label(part) for part in parts})


def s8(session: Session) -> None:
    """The envelope and volatility: five recordings, each fetched twice, a minute apart, the
    envelope's title checked before each fetch."""
    listing = _listing(session.client)
    _same_archive(session, listing)
    owners: dict[str, set[str]] = collections.defaultdict(set)  # embeddingKey's hash -> recordings
    for row in _named(session, listing, 5):
        name = short(row.canonical_id)
        try:
            if _checked(session, row) is None:
                continue
            first = session.client.envelope(row.listed_id)
            sleep(PAUSE)
            if _checked(session, row) is None:
                continue
            second = session.client.envelope(row.listed_id)
        except PlaudError as exc:
            say(f"{name}: {_error(exc)}")
            continue
        say(f"{name}: envelope keys: {_names(first)}")
        say(f"{name}: audio link expires in {_expires(first.get('presigned_url'))}")
        for block in _blocks(first):
            kind = block.get("data_type")
            kind = kind if isinstance(kind, str) and kind.isidentifier() else "unknown"
            link, error = block.get("data_link"), block.get("_data_link_error")
            line = f"{name}: block {kind}: keys {_names(block)} · behind a link: {_yes(link)}"
            if link:
                line += f" · link expires in {_expires(link)}"
            if isinstance(error, str) and error.isidentifier():
                line += f" · error {error}"
            say(line)
        rows = transcript_rows(first) or []
        say(f"{name}: segments: {len(rows)}")
        for r in rows:
            if isinstance(r.get("embeddingKey"), str) and r["embeddingKey"]:
                owners[short(r["embeddingKey"])].add(row.canonical_id)
        before, after = part_hashes(first), part_hashes(second)
        for part in sorted(set(before) | set(after)):
            label = _part_label(part)
            say(f"{name}: part {label}: matched {_yes(before.get(part) == after.get(part))}")
    shared = sum(1 for recordings in owners.values() if len(recordings) > 1)
    say(f"embeddingKey values seen in more than one recording: {shared}")


def _changed(new: dict, old: dict) -> list[str]:
    return sorted(k for k in set(new) | set(old) if new.get(k) != old.get(k))


def _clip(session: Session, listing: Listing, last: dict) -> tuple[str, Listed | None, str | None]:
    """S9's clip, from --id: the 8-hex name the run without --id printed for it, or its Plaud ID.
    Its name, its listed row (None once it isn't listed), and the ID to ask files/{id} by: as
    listed, else as the last run kept it in the spike folder, else as given."""
    given = session.args.id[0]
    if _SHORT.fullmatch(given):
        rows = [row for row in listing.rows if short(row.canonical_id) == given]
        if len(rows) > 1:
            raise SpikeRefused("refused: two listed recordings print that name: give the Plaud ID")
        if rows:
            return given, rows[0], rows[0].listed_id
        kept = last.get("listed_id") if last.get("id") == given else None
        return given, None, kept if isinstance(kept, str) else None
    canonical = _resolve(session, given)
    return short(canonical), listing.by_canonical().get(canonical), given


def _own_clip(session: Session, canonical: str | None, row: Listed | None, last: dict) -> None:
    """--own-clip skips the keep-out check, so it is allowed only for Dan's new clip: one that isn't
    in the archive (a kept-out recording never was imported, so that alone doesn't tell them apart)
    and was made in the last 24 hours: by its listed start time, or the one the last run kept."""
    when = row.recorded_at if row else None
    if row is None and isinstance(last.get("recorded_at"), str):
        try:
            when = datetime.fromisoformat(last["recorded_at"])
        except ValueError:
            when = None
    recent = when is not None and timedelta(0) <= session.now() - when <= RECENT
    if canonical in session.guard.here or not recent:
        raise SpikeStopped(
            "stopped: --own-clip is for a clip that isn't in the archive and was made in the "
            "last 24 hours"
        )


def s9(session: Session) -> None:
    """What an edit looks like, on Dan's own throwaway clip (session C). Without --id: the newest
    listed rows, for (a), each by the name --id then takes. With --id (and --own-clip): the clip's
    parts and listing fields, and what changed since the last run."""
    listing = _listing(session.client)
    if not session.args.id:
        dated = [row for row in listing.rows if row.recorded_at]
        for row in sorted(dated, key=lambda r: r.recorded_at, reverse=True)[:5]:
            minutes = int((session.now() - row.recorded_at).total_seconds() // 60)
            word = keep_out_match(row.title, session.guard.words) is not None
            say(f"{short(row.canonical_id)}: keep-out word: {_yes(word)} · {minutes} min old")
        return
    record_path = session.folder / "s9-last.json"
    try:
        last = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    last = last if isinstance(last, dict) else {}
    name, row, ask_by = _clip(session, listing, last)
    canonical = row.canonical_id if row else canonical_plaud_id(ask_by) if ask_by else None
    if session.args.own_clip:
        _own_clip(session, canonical, row, last if last.get("id") == name else {})
    _same_archive(session, listing)
    say(f"{name}: listed: {_yes(row)}")
    if row is None:
        # Its title was checked by the run that recorded it; consent and private are checked now.
        checked = last.get("id") == name and last.get("pattern_ok") is True
        if ask_by is None or not checked or not session.guard.allows(ask_by, "", own_clip=True):
            say(f"{name}: files/{{id}} not fetched: no earlier run checked its title")
            return
        try:
            _files(session.client, ask_by)
            say(f"{name}: files/{{id}}: ok")
        except PlaudError as exc:
            say(f"{name}: files/{{id}}: {_error(exc)}")
        return
    say(f"{name}: keep-out word: {_yes(keep_out_match(row.title, session.guard.words))}")
    if not _gate(session, row.listed_id, row.title) or _checked(session, row) is None:
        return
    raw = next(
        (r for r in _raw_rows(session.client) if canonical_plaud_id(str(r.get("id"))) == canonical),
        {},
    )
    try:
        first = session.client.envelope(row.listed_id)
        sleep(MOMENT)
        if _checked(session, row) is None:
            return
        second = session.client.envelope(row.listed_id)
    except PlaudError as exc:
        say(f"{name}: {_error(exc)}")
        return
    parts = {p: h[:8] for p, h in part_hashes(first).items()}
    again = {p: h[:8] for p, h in part_hashes(second).items()}
    say(f"{name}: parts that differ a moment apart: {_labels(_changed(parts, again))}")
    fields = {key: short(json.dumps(value, sort_keys=True)) for key, value in raw.items()}
    record = {
        "id": name,
        "listed_id": row.listed_id,  # kept in the 0700 spike folder only, never printed
        "at": session.now().isoformat(),
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
        "parts": parts,
        "fields": fields,
        "pattern_ok": True,
    }
    if last.get("id") == name:
        seconds = int((session.now() - datetime.fromisoformat(last["at"])).total_seconds())
        say(f"{name}: parts changed since the last run: {_labels(_changed(parts, last['parts']))}")
        changed_fields = _names(_changed(fields, last["fields"]))
        say(f"{name}: listing fields changed since the last run: {changed_fields}")
        say(f"{name}: seconds since the last run: {seconds}")
    record_path.write_text(json.dumps(record), encoding="utf-8")
    record_path.chmod(0o600)


def s10(session: Session) -> None:
    """Pacing: twenty calls at one a second, and the rate-limit headers' names. It stops at the
    first 429: a spike never provokes one on purpose."""
    url = f"{session.client.api_base}{LIST_PATH}?page=1&page_size=10"
    limits: set[str] = set()
    statuses: collections.Counter[str] = collections.Counter()
    for n in range(20):
        auth = {"Authorization": f"Bearer {session.tokens.access_token()}"}
        status, names = _raw_get(url, {**auth, "User-Agent": "recordings-spike"})
        statuses[str(status)] += 1
        limits |= {h for h in names if "ratelimit" in h.lower() or "retry" in h.lower()}
        if status == 429:
            say(f"stopped at call {n + 1}: HTTP 429")
            break
        sleep(1.0)
    say(f"statuses: {', '.join(f'{k} ×{v}' for k, v in sorted(statuses.items()))}")
    retry_after = any(h.lower() == "retry-after" for h in limits)
    say(f"rate-limit headers: {_names(limits)} · Retry-After seen: {_yes(retry_after)}")


def s11(session: Session) -> None:
    """A large download: size, time, throughput, Content-Length, Range, and the link's expiry."""
    listing = _listing(session.client)
    _same_archive(session, listing)
    named = _named(session, listing, 1)
    if not named:
        return
    row = named[0]
    try:
        env = _checked(session, row)
        if env is None:
            return
        link = check_link(env.get("presigned_url"), api_base=session.client.api_base)
    except PlaudError as exc:
        say(f"{short(row.canonical_id)}: {_error(exc)}")
        return
    status, seen = _raw_get(link, {"Range": "bytes=0-0"})
    say(f"Range honoured: {_yes(status == 206)} (HTTP {status})")
    # download() refuses a body without one, as IncompleteDownload: the same word as a stall.
    has_length = any(name.lower() == "content-length" for name in seen)
    say(f"Content-Length: {'present' if has_length else 'absent'}")
    dest = session.folder / "s11.audio"
    start = session.monotonic()
    try:
        size = session.client.download(link, dest)
        seconds = max(session.monotonic() - start, 0.001)
        say(f"size: {size / 1e6:.1f} MB · {seconds:.0f} s · {size / 1e6 / seconds:.2f} MB/s")
        expires = _expires(link)
        passed = expires != "unknown" and seconds > float(expires.split()[0])
        say(f"link expires in {expires} · its expiry passed during the download: {_yes(passed)}")
    except PlaudError as exc:
        say(f"download: {_error(exc)}")
    finally:
        dest.unlink(missing_ok=True)


def s12(session: Session) -> None:
    """Time zones: Plaud's start time for three recordings Dan remembers, read both ways."""
    listing = _listing(session.client)
    raw = {canonical_plaud_id(str(r.get("id"))): r for r in _raw_rows(session.client)}
    zone = ZoneInfo(session.cfg.default_timezone)
    for row in _named(session, listing, 3):
        given = raw.get(row.canonical_id, {}).get("start_at")
        if not isinstance(given, str) or row.start_at is None:
            say(f"{short(row.canonical_id)}: no start time")
            continue
        marked = "Z" if given.endswith("Z") else "an offset, or nothing"
        as_utc = row.start_at.astimezone(zone).strftime("%H:%M")
        as_local = row.start_at.replace(tzinfo=None).strftime("%H:%M")
        say(
            f"{short(row.canonical_id)}: marked {marked} · read as UTC, in "
            f"{session.cfg.default_timezone}: {as_utc} · read as local time: {as_local}"
        )


SPIKE_FUNCTIONS: dict[str, Callable[[Session], None]] = {
    "s1": s1,
    "s2": s2,
    "s3": s3,
    "s4": s4,
    "s5": s5,
    "s7": s7,
    "s8": s8,
    "s9": s9,
    "s10": s10,
    "s11": s11,
    "s12": s12,
}


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="plaud_spike.py", description="stage 2b's spikes (§18)")
    ap.add_argument("spike", choices=SPIKES)
    ap.add_argument(
        "--home",
        default=os.environ.get("RECORDINGS_HOME"),
        help="RECORDINGS_HOME, the spike folder's parent",
    )
    repo = os.environ.get("REPO_DIR")
    ap.add_argument(
        "--production-config",
        type=Path,
        default=Path(repo) / "config.toml" if repo else None,
        help="the deployment's config.toml, for its patterns and word ($REPO_DIR/config.toml)",
    )
    ap.add_argument("--token-file", type=Path, help="the session's token file, in the spike folder")
    ap.add_argument(
        "--id",
        action="append",
        default=[],
        help="a Plaud ID or an archive recording ID (S7, S8, S9, S11, S12); for S9 also the "
        "8-hex name its run without --id printed",
    )
    ap.add_argument(
        "--token-url", help="S2 and S4: the https token URL, from the vendor CLI's own refresh"
    )
    ap.add_argument(
        "--own-clip",
        action="store_true",
        help="S9 only, for a clip that isn't in the archive: skip the keep-out check",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    guard: Guard | None = None
    try:
        if args.own_clip and args.spike != "s9":
            raise SpikeRefused("refused: --own-clip is for S9 only")
        if args.spike in ("s2", "s4"):
            _token_url(args.token_url)  # before anything is read
        elif args.token_url is not None:
            raise SpikeRefused("refused: --token-url is for S2 and S4 only")
        if args.spike in NEEDS_ID and not args.id:
            raise SpikeRefused(f"refused: name up to {NEEDS_ID[args.spike]} recordings with --id")
        if not args.home:
            raise SpikeRefused("refused: give --home (RECORDINGS_HOME)")
        cfg = load_config(os.environ)
        if cfg.archive_path is None:
            raise SpikeRefused("refused: no archive is configured, so private ones can't be known")
        archive = Archive(cfg.archive_path)
        guard = build_guard(cfg, archive, args.production_config)
        folder = spike_folder(Path(args.home))
        token_file = (args.token_file or folder / "home" / ".plaud" / "tokens.json").absolute()
        if not token_file.resolve().is_relative_to(folder.resolve()):
            raise SpikeRefused("refused: --token-file must be inside the spike folder")
        tokens = FileTokens(token_file)
        api_base = check_api_base(_section(cfg, "plaud").get("api_base", API_BASE))
        client = PlaudClient(tokens, api_base=api_base)
        SPIKE_FUNCTIONS[args.spike](Session(args, cfg, archive, guard, folder, tokens, client))
        return 0
    except SpikeRefused as exc:
        say(str(exc))
        return EXIT_REFUSED
    except ConfigError as exc:
        say(f"refused: {exc}")  # written for display: names keys, never values
        return EXIT_REFUSED
    except SpikeStopped as exc:
        say(str(exc))
        return 1
    except PlaudError as exc:
        say(f"stopped: {_error(exc)}")
        return 1
    except Exception as exc:  # noqa: BLE001  anything else: its class, never its text or a traceback
        say(f"stopped: {_error(exc)}")
        return 1
    finally:
        if guard is not None:
            say(guard.report())


if __name__ == "__main__":
    sys.exit(main())
