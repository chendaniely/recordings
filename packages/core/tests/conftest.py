"""Shared fixtures for the core tests. Every archive here is synthetic and lives in tmp_path."""

import csv
import hashlib
import io
import itertools
import json
import threading
import wave
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from recordings.archive import RawSource
from recordings.index import Index
from recordings.init import init_archive
from recordings.state import State
from recordings.writer import Incoming, Writer

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
LOCAL = datetime.fromisoformat("2026-10-06T14:00:03-07:00")


@pytest.fixture
def new_archive(tmp_path):
    """An initialised, empty archive and its state folder: (root, state_path)."""
    root, state = tmp_path / "archive", tmp_path / "state"
    init_archive(root, state, writer_id="test")
    return root, state


@pytest.fixture
def make_writer(tmp_path):
    def make(name: str = "archive", writer_id: str = "test") -> Writer:
        root, state = tmp_path / name, tmp_path / f"{name}-state"
        init_archive(root, state, writer_id=writer_id)
        return Writer(
            root,
            State.open(state),
            Index(tmp_path / f"{name}-index" / "index.db"),
            writer_id=writer_id,
        )

    return make


@pytest.fixture
def writer(make_writer):
    return make_writer()


@pytest.fixture
def make_incoming(tmp_path):
    """An Incoming with a media file of its own. Pass `content` to make duplicates on purpose."""
    counter = itertools.count()

    def make(content: bytes | None = None, *, name: str = "clip.MP3", **over) -> Incoming:
        n = next(counter)
        path = tmp_path / "in" / f"{n}-{name}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if content is not None else f"ID3 fake audio {n}".encode())
        fields = dict(
            media=path,
            recorded_at=LOCAL,
            timezone_name="America/Vancouver",
            time_source="plaud",
            title="Week 4",
            kind="audio",
            sources=(
                RawSource(kind="plaud", ref="of_" + "a" * 32, added_at=T0, payload=b'{"id": 1}'),
            ),
        )
        fields.update(over)
        return Incoming(**fields)

    return make


HEX = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def plaud_env():
    """A synthetic Plaud `files/{id}` envelope, shaped like audio-router's raw snapshots: invented
    names, invented text, a rotating presigned URL and audio-router's own `_meta`. `polish` adds a
    `transaction_polish` block with the same rows, as Plaud's polished transcript has."""

    def make(
        fid: str = HEX,
        *,
        name: str = "Week 4",
        segments=None,
        notes=None,
        outline: bool = True,
        polish: bool = False,
        duration: int = 2600,
        link_error: bool = False,
        presigned: str = "abc",
    ) -> dict:
        rows = (
            segments
            if segments is not None
            else [
                {
                    "content": "Hello there.",
                    "start_time": 0,
                    "end_time": 1500,
                    "speaker": "Ada",
                    "original_speaker": "Speaker 1",
                    "embeddingKey": "ek-1",
                },
                {
                    "content": "Hi.",
                    "start_time": 1500,
                    "end_time": 2600,
                    "speaker": "Speaker 2",
                    "original_speaker": "Speaker 2",
                },
            ]
        )
        source_list = [
            {
                "data_type": "transaction",
                "data_id": "t-1",
                "data_tab_name": None,
                "data_content": json.dumps(rows, ensure_ascii=False),
            }
        ]
        if outline:
            source_list.append(
                {
                    "data_type": "outline",
                    "data_id": "o-1",
                    "data_tab_name": None,
                    "data_content": json.dumps(
                        [{"topic": "Greetings", "start_time": 0, "end_time": 2600}]
                    ),
                }
            )
        if polish:
            source_list.append(
                {
                    "data_type": "transaction_polish",
                    "data_id": "p-1",
                    "data_tab_name": None,
                    "data_content": json.dumps(rows, ensure_ascii=False),
                }
            )
        note_list = (
            notes
            if notes is not None
            else [
                {
                    "data_type": "auto_sum_note",
                    "data_id": "n-1",
                    "data_tab_name": "Summary",
                    "data_content": "# Summary\n\nTwo people say hello.",
                },
                {
                    "data_type": "high_light",
                    "data_id": "h-1",
                    "data_tab_name": None,
                    "data_content": json.dumps(
                        [{"timestamp": 1000, "title": "Key moment", "content": "A highlight"}]
                    ),
                },
            ]
        )
        if link_error:
            note_list = [
                *note_list,
                {
                    "data_type": "auto_sum_note",
                    "data_id": "n-2",
                    "data_tab_name": "Action items",
                    "data_content": "",
                    "data_link": "https://notes.example/n2?X-Amz-Expires=300",
                    "_data_link_error": "HTTP Error 403: Forbidden",
                },
            ]
        return {
            "id": fid,
            "name": name,
            "duration": duration,
            "start_at": "2026-08-01T16:00:00Z",
            "created_at": "2026-08-01T16:05:00Z",
            "serial_number": "TEST0001",
            "presigned_url": f"https://audio.example/x.mp3?X-Amz-Expires=300&X-Amz-Signature={presigned}",
            "source_list": source_list,
            "note_list": note_list,
            "_meta": {"tool": "audio-router", "enriched_from_links": []},
        }

    return make


# audio-router's catalog@5 columns, in order (scripts/build_catalog.py, COLUMNS).
AR_COLUMNS = [
    "uri",
    "source",
    "file_id",
    "recorded_at_local",
    "timezone",
    "time_source",
    "duration_ms",
    "audio_rel",
    "audio_sha256",
    "dup_of",
    "ledger_status",
    "transcript_source",
    "transcript_len",
    "transcript_payload_chars",
    "segments",
    "speakers_named",
    "has_vendor_transcript",
    "has_vendor_notes",
    "vendor_note_kinds",
    "has_local_transcript",
    "has_local_summary",
    "rendition_kinds",
    "tags",
    "access",
]


def wav_bytes(seed: int, seconds: float = 2.0, rate: int = 16000, channels: int = 1) -> bytes:
    """A real, tiny WAV file whose bytes differ by seed, so each recording has its own SHA-256."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes([seed % 256, seed // 256 % 256]) * channels * int(seconds * rate))
    return buf.getvalue()


class ARArchive:
    """A synthetic audio-router media root, laid out as its archive/store.py, sources/ and
    scripts/build_catalog.py describe: never a copy of the real one, and every name invented."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.rows: list[dict] = []
        self.ledger_only: list[str] = []
        self.held = 0
        self._seed = 0

    def _write(self, rel: str, data: bytes | str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def _audio(self, rel: str, seconds: float) -> str:
        self._seed += 1
        return hashlib.sha256(
            self._write(rel, wav_bytes(self._seed, seconds)).read_bytes()
        ).hexdigest()

    def _row(self, **cells) -> dict:
        row = {c: "" for c in AR_COLUMNS}
        row.update(cells)
        self.rows.append(row)
        return row

    def plaud(
        self,
        fid: str,
        *,
        envelopes=(),
        stamps=None,
        derived=None,
        audio=True,
        recorded_at="2026-08-01T09:00:00-07:00",
        dup_of="",
        seconds=2.6,
        same_audio_as: str | None = None,
        tags=(),
        ext=".wav",
    ) -> dict:
        """Snapshots in plaud/raw/<fid>/<stamp>.json (oldest first; a str is written as is), audio
        in plaud/audio/2026/08/<fid><ext> (WAV bytes, whatever the suffix), outputs in
        plaud/derived/<fid>/. `tags` are the row's authored labels (catalog/tags.jsonl)."""
        for i, env in enumerate(envelopes):
            stamp = stamps[i] if stamps else f"2026080{i + 1}T161000Z"
            self._write(
                f"plaud/raw/{fid}/{stamp}.json",
                env if isinstance(env, str) else json.dumps(env, indent=1, ensure_ascii=False),
            )
        for name, doc in (derived or {}).items():
            self._write(
                f"plaud/derived/{fid}/{name}", json.dumps(doc, indent=1, ensure_ascii=False)
            )
        rel, sha = "", ""
        if audio:
            rel = f"plaud/audio/2026/08/{fid}{ext}"
            if same_audio_as:
                sha = hashlib.sha256(
                    self._write(rel, (self.root / same_audio_as).read_bytes()).read_bytes()
                ).hexdigest()
            else:
                sha = self._audio(rel, seconds)
        return self._row(
            uri=f"plaud/{fid}",
            source="plaud",
            file_id=fid,
            recorded_at_local=recorded_at,
            timezone="America/Vancouver",
            time_source="start_at",
            duration_ms=str(int(seconds * 1000)),
            audio_rel=rel,
            audio_sha256=sha,
            dup_of=dup_of,
            tags=";".join(sorted(tags)),
        )

    def orphan(self, fid: str) -> dict:
        """A Plaud row build_catalog.py builds from the audio alone: no snapshot, and no time."""
        rel = f"plaud/audio/2026/08/{fid}.wav"
        return self._row(
            uri=f"plaud/{fid}",
            source="plaud",
            file_id=fid,
            audio_rel=rel,
            audio_sha256=self._audio(rel, 2.0),
            ledger_status="rendered",
        )

    def private(
        self,
        *,
        name: str = "Aug 3 at 10-00",
        text: str | None = "Speaker 1: Hello.\n",
        tags=("therapy",),
        recorded_at="2026-08-03T10:00:00-07:00",
    ) -> dict:
        """The private tier as audio-router's catalog/private.jsonl registers it: a Recorder export
        that keeps its export name (a title), registered by its audio's SHA-256, with Recorder's
        prose transcript beside it as a .txt of the same stem. No raw/ and no derived/ folders,
        and the row's audio columns are empty, as build_catalog.py leaves them."""
        self._seed += 1
        data = wav_bytes(self._seed, 2.0)
        fid = hashlib.sha256(data).hexdigest()
        self._write(f"private/recorder-exports/{name}.wav", data)
        if text is not None:
            self._write(f"private/recorder-exports/{name}.txt", text)
        return self._row(
            uri=f"private/{fid}",
            source="private",
            file_id=fid,
            recorded_at_local=recorded_at,
            timezone="America/Vancouver",
            time_source="filename",
            duration_ms="2000",
            access="restricted",
            tags=";".join(sorted(tags)),
        )

    def recorder(
        self,
        *,
        transcript="Speaker 1: Hello.\nSpeaker 2: Hi.\n",
        recorded_at="2026-07-15T16:17:00-07:00",
    ) -> dict:
        self._seed += 1
        data = wav_bytes(self._seed, 2.0)
        fid = hashlib.sha256(data).hexdigest()
        self._write(f"recorder/audio/2026/07/{fid}.wav", data)
        envelope = {  # recorder.envelope_for: a Plaud-shaped envelope around a prose transcript
            "id": fid,
            "name": "Jul 15 at 16-17",
            "duration": 2000,
            "start_at": "2026-07-15T16:17:00",
            "created_at": "2026-07-15T16:17:00",
            "source_list": [
                {"data_type": "transaction", "data_content": transcript, "data_tab_name": None}
            ],
            "note_list": [],
            "_meta": {"source": "recorder", "tool": "audio-router"},
        }
        self._write(f"recorder/raw/{fid}/20260716T000000Z.json", json.dumps(envelope, indent=1))
        return self._row(
            uri=f"recorder/{fid}",
            source="recorder",
            file_id=fid,
            recorded_at_local=recorded_at,
            timezone="America/Vancouver",
            time_source="filename",
            duration_ms="2000",
            audio_rel=f"recorder/audio/2026/07/{fid}.wav",
            audio_sha256=fid,
        )

    def pocket(
        self,
        uuid: str,
        *,
        title="Pocket chat",
        recorded_at="2026-08-02T10:00:00-07:00",
        segments=None,
        summaries=None,
    ) -> dict:
        doc = {
            "id": uuid,
            "title": title,
            "duration": 2.0,
            "recording_at": "2026-08-02T10:00:00",
            "created_at": "2026-08-02T10:05:00",
            "language": "en",
            "state": "done",
            "tags": [],
            "transcript": {
                "metadata": {},
                "text": "Hi there.",
                "segments": segments
                if segments is not None
                else [
                    {"start": 0, "end": 1, "text": "Hi", "speaker": "A"},
                    {"start": 1, "end": 2, "text": "there."},
                ],
            },
            "summarizations": summaries
            if summaries is not None
            else {"sid-1": {"text": "A chat."}},
        }
        self._write(f"pocket/raw/{uuid}.json", json.dumps(doc, indent=1))
        sha = self._audio(f"pocket/audio/2026/08/{uuid}.wav", 2.0)
        return self._row(
            uri=f"pocket/{uuid}",
            source="pocket",
            file_id=uuid,
            recorded_at_local=recorded_at,
            timezone="America/Vancouver",
            time_source="recording_at",
            duration_ms="2000",
            audio_rel=f"pocket/audio/2026/08/{uuid}.wav",
            audio_sha256=sha,
        )

    def pocket_without_audio(self, stem: str = "getting_started") -> dict:
        """audio-router's one audio-less Pocket item: its native id is a title slug, so the
        catalog keys it on sha256(stem)[:32], and that slug must never reach any output."""
        self._write(f"pocket/raw/{stem}.json", json.dumps({"id": stem, "title": "Getting Started"}))
        fid = hashlib.sha256(stem.encode("utf-8")).hexdigest()[:32]
        return self._row(
            uri=f"pocket/{fid}",
            source="pocket",
            file_id=fid,
            recorded_at_local="2026-08-01T08:00:00-07:00",
            timezone="America/Vancouver",
            time_source="created_at",
        )

    def write_catalog(self, *, provenance_rows: int | None = None) -> None:
        cat = self.root / "catalog"
        cat.mkdir(parents=True, exist_ok=True)
        with (cat / "catalog.csv").open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=AR_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(sorted(self.rows, key=lambda r: r["uri"]))
        provenance = {
            "schema": "audio-router/catalog@5",
            "rows": len(self.rows) if provenance_rows is None else provenance_rows,
            "ledger_only_no_media": self.ledger_only,
            "per_source": {"plaud": {"held_count": self.held}},
        }
        (cat / "provenance.json").write_text(json.dumps(provenance, indent=1), encoding="utf-8")

    def tree_hash(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(p for p in self.root.rglob("*") if p.is_file()):
            digest.update(path.relative_to(self.root).as_posix().encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()


@pytest.fixture
def ar_archive(tmp_path) -> ARArchive:
    return ARArchive(tmp_path / "audio-router-media")


class FakeNtfy:
    """An ntfy server on a real local socket: it records each request and answers `status`.
    The dead-man's switch pings land here too, under the ping URL's own path."""

    def __init__(self):
        self.requests, self.status = [], 200
        self.redirect, self.redirect_code = None, 302  # set `redirect` to a URL to answer with one
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _record(self):
                length = int(self.headers.get("Content-Length") or 0)
                outer.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": self.rfile.read(length).decode("utf-8"),
                    }
                )
                if outer.redirect:
                    self.send_response(outer.redirect_code)
                    self.send_header("Location", f"{outer.redirect}{self.path}")
                    self.end_headers()
                    return
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"id": "x", "event": "message"}).encode())

            do_POST = do_GET = _record

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ntfy():
    server = FakeNtfy()
    yield server
    server.close()


@pytest.fixture
def other_ntfy():
    """A second FakeNtfy, on another port: where a redirect would lead."""
    server = FakeNtfy()
    yield server
    server.close()
