"""Plaud source: direct HTTP against the public third-party API.

The vendor CLI is deliberately not used for data. It does

    noteList.find((n) => n.data_type === "auto_sum_note")

which discards every other note tab -- including the highlights and typed
notes Dan makes during calls, the one artifact class that cannot be
regenerated from the audio. Its single sanctioned use here is refreshing the
token, which it owns.

The whole public surface is four read-only endpoints. `files/{id}` returns
everything in one call: source_list renditions, note_list tabs, and a
presigned audio URL. There is no endpoint that triggers generation, and no
change signal of any kind -- no updated_at, no etag -- which is why change
detection has to hash content.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

API_BASE = "https://platform.plaud.ai/developer/api"
TOKEN_FILE = Path.home() / ".plaud" / "tokens.json"
PAGE_SIZE = 100          # the API rejects anything under 10
TIMEOUT = 120
USER_AGENT = "audio-router/0.1"

# Vendor ids are 32 lowercase hex. Validated rather than trusted: see the
# fid="" failure mode documented in list_recordings().
FILE_ID_RE = re.compile(r"[0-9a-f]{32}")


class IncompleteDownload(RuntimeError):
    """A response body was shorter than its declared Content-Length."""


class AuthError(RuntimeError):
    """Token missing or refresh failed. Callers map this to EX_AUTH (69)."""


@dataclass(frozen=True)
class Recording:
    """One row of the list endpoint -- metadata only, no content."""
    file_id: str
    name: str
    created_at: datetime | None
    start_at: datetime | None
    duration_ms: int
    serial: str = ""

    @property
    def minutes(self) -> float:
        return round(self.duration_ms / 60000, 1)

    @property
    def recorded_at(self) -> datetime | None:
        """Best available capture time.

        start_at is capture time for device recordings. For uploads it is the
        upload moment -- the API has no field for when an uploaded file was
        actually recorded, which is why two recordings of one event can carry
        start_at values 26 minutes apart while being simultaneous.
        """
        return self.start_at or self.created_at


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


class PlaudClient:
    """Thin HTTP client. Owns token reading, refresh and retry; nothing else."""

    def __init__(self, *, token_file: Path = TOKEN_FILE, plaud_cli: str = "plaud",
                 api_base: str = API_BASE):
        self.token_file = token_file
        self.plaud_cli = plaud_cli
        self.api_base = api_base
        self._token: str | None = None
        # Ids the listing returned that were not well-formed. Surfaced by
        # sync as errors rather than skipped silently.
        self.malformed: list[str] = []

    # -- auth ------------------------------------------------------------
    def _read_token(self) -> str:
        try:
            return json.loads(self.token_file.read_text())["access_token"]
        except FileNotFoundError as exc:
            raise AuthError(f"no token at {self.token_file}; run `plaud login`") from exc
        except (json.JSONDecodeError, KeyError) as exc:
            # A torn read mid-refresh looks exactly like corruption. Retryable.
            raise AuthError(f"token file unreadable ({exc}); retry, or `plaud login`") from exc

    @property
    def token(self) -> str:
        if self._token is None:
            self._token = self._read_token()
        return self._token

    def _refresh(self) -> None:
        """Let the CLI refresh -- it owns the token file.

        Absolute path from config, because launchd runs with a minimal PATH and
        a bare `plaud` would not resolve. Timeout because a hung CLI would sit
        inside the acquisition lock.
        """
        try:
            subprocess.run([self.plaud_cli, "me"], capture_output=True,
                           timeout=60, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise AuthError(f"could not run {self.plaud_cli!r} to refresh: {exc}") from exc
        self._token = None

    # -- transport -------------------------------------------------------
    def get(self, path_or_url: str, *, auth: bool = True, _retried: bool = False) -> bytes:
        url = path_or_url if path_or_url.startswith("http") else self.api_base + path_or_url
        req = urllib.request.Request(url)
        if auth:
            req.add_header("Authorization", f"Bearer {self.token}")
            req.add_header("Accept", "application/json, text/plain, */*")
        # Bare urllib's default User-Agent gets a flat 403 from this API.
        req.add_header("User-Agent", USER_AGENT)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and auth and not _retried:
                self._refresh()
                return self.get(path_or_url, auth=auth, _retried=True)
            raise

    # -- endpoints -------------------------------------------------------
    def list_recordings(self) -> list[Recording]:
        out: list[Recording] = []
        page = 1
        while True:
            payload = json.loads(
                self.get(f"/open/third-party/files/?page={page}&page_size={PAGE_SIZE}"))
            batch = payload if isinstance(payload, list) else next(
                (payload[k] for k in ("data", "data_list", "files", "items", "list")
                 if isinstance(payload.get(k), list)), None)
            if batch is None:
                raise RuntimeError(f"unrecognised list shape: keys={list(payload)[:8]}")
            for r in batch:
                # A missing or renamed `id` field must not become fid="".
                # raw_dir("") is the raw/ root itself, so every recording
                # would append into it, each envelope compared against the
                # PREVIOUS recording's content, `archived` reading True for
                # all of them -- with no error anywhere.
                fid = str(r.get("id") or "").strip()
                if not FILE_ID_RE.fullmatch(fid):
                    self.malformed.append(repr(r.get("id"))[:40])
                    continue
                out.append(Recording(
                    file_id=fid,
                    name=r.get("name") or "",
                    created_at=_parse_dt(r.get("created_at")),
                    start_at=_parse_dt(r.get("start_at")),
                    duration_ms=int(r.get("duration") or 0),
                    serial=str(r.get("serial_number") or ""),
                ))
            # Stop on an EMPTY page, never on a merely short one. A server
            # that clamps page_size (ask 100, get 50) would otherwise look
            # like the end of the list: recordings past the cut are never
            # listed, never gated, never archived -- and `sync` prints
            # "errors: 0". Worse, held.json is rewritten from what a run saw,
            # and held items leave no trace in raw/, so a truncated listing
            # permanently erases the only evidence they exist.
            if not batch:
                return out
            if len(batch) < PAGE_SIZE:
                # Probe one more page to distinguish a clamped page size from
                # a genuine final page. Costs one request per sync.
                page += 1
                continue
            page += 1

    def envelope(self, file_id: str, *, enrich: bool = True) -> dict:
        """The full files/{id} response, with data_link bodies inlined.

        Links carry X-Amz-Expires=300, so they are followed IMMEDIATELY rather
        than collected for a later pass. A link that has already expired by the
        time we reach it records the error in place instead of failing the
        whole recording -- one unreachable artifact should not cost the other
        five.
        """
        env = json.loads(self.get(f"/open/third-party/files/{file_id}"))
        if not enrich:
            return env
        enriched: list[str] = []
        for section in ("source_list", "note_list"):
            for blk in env.get(section) or []:
                if (blk.get("data_content") or "").strip():
                    continue
                link = blk.get("data_link")
                if not link:
                    continue
                try:
                    blk["data_content"] = self.get(link, auth=False).decode("utf-8", "replace")
                    blk["_fetched_from_data_link"] = True
                    enriched.append(blk.get("data_type") or "?")
                except Exception as exc:                       # noqa: BLE001
                    blk["_data_link_error"] = str(exc)[:200]
        env.setdefault("_meta", {})["enriched_from_links"] = enriched
        return env

    def audio_url(self, envelope: dict) -> str | None:
        return envelope.get("presigned_url")

    def download(self, url: str, dest: Path) -> int:
        """Stream audio to disk. Atomic AND complete -- the two are different.

        The atomic half (tmp + rename) was always here. The complete half was
        not, and its absence was silent: CPython's HTTPResponse.read(amt)
        deliberately does NOT raise on premature EOF -- its own source says
        raising IncompleteRead "might break compatibility" -- so a connection
        dropping mid-body just returns b"" and the `while chunk :=` loop exits
        normally. A 100-byte fragment of a 5 MB file was renamed into place,
        counted as a success, never re-fetched (sync skips when dest exists),
        and protected from deletion by the append-only rule. Phase 4 would then
        transcribe it into a transcript that reads as complete.

        So: compare against Content-Length and refuse to finalise a short file.
        The .part is removed, the error surfaces as a fetch-error alarm, and
        the next run retries.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        req = urllib.request.Request(url)
        req.add_header("User-Agent", USER_AGENT)
        written = 0
        with urllib.request.urlopen(req, timeout=900) as resp, open(tmp, "wb") as fh:
            declared = resp.headers.get("Content-Length")
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
                written += len(chunk)
        expected = int(declared) if declared and declared.isdigit() else None
        if expected is not None and written != expected:
            tmp.unlink(missing_ok=True)
            raise IncompleteDownload(
                f"short read: got {written} of {expected} bytes")
        if written == 0:
            tmp.unlink(missing_ok=True)
            raise IncompleteDownload("empty response body")
        tmp.rename(dest)
        return written


def stamp_meta(rec: Recording, tz_name: str) -> dict:
    """Resolve the recording time once, and record how it was resolved.

    Persisted into the envelope so a later reindex reads it back rather than
    re-deriving from a system timezone that may since have changed. The
    time_source field exists because a missing time is otherwise
    indistinguishable from midnight.
    """
    tz = ZoneInfo(tz_name)
    src = "start_at" if rec.start_at else ("created_at" if rec.created_at else None)
    utc = rec.recorded_at
    local = utc.astimezone(tz) if utc else None
    return {
        "recorded_at_utc": utc.isoformat() if utc else None,
        "recorded_at_local": local.isoformat() if local else None,
        "stamp": local.strftime("%Y-%m-%d-%H%M") if local else None,
        "timezone": tz_name,
        "time_source": src,
    }
