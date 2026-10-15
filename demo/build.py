"""Build demo/archive from encoded media + demo/canned. Deterministic: fixed stamps, no clock.

uv run python demo/build.py --media-dir demo/.cache/media --out demo/archive --force
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from recordings.archive import RawSource, sha256_file
from recordings.backends import CannedBackend, CompletionRequest
from recordings.ids import make_id
from recordings.index import Index
from recordings.init import init_archive
from recordings.models import Rendition, TagRef
from recordings.state import State
from recordings.writer import Incoming, Writer

HERE = Path(__file__).resolve().parent
# Every stamp in the demo derives from this instant, so a rebuild is byte-identical.
BUILD_AT = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
# A fixed UUID, so a rebuild is byte-identical. Every copy of the demo shares it, harmlessly: each
# demo run gets a fresh temporary state folder.
DEMO_UUID = "5f0c8e2a-3b1d-4c6e-9a7f-1d2e3f405162"
WHISPER = {"model": "mlx-community/whisper-large-v3-turbo", "version": "large-v3-turbo@a4aaeec"}


def plaud_envelope(entry: dict, transcript: dict, summary_md: str) -> dict:
    """A synthetic Plaud `files/{id}` envelope, built from the canned transcript, so the demo's
    Plaud outputs come from reconcile, the way the real ones do. Generic speaker labels only:
    no real person's name."""
    labels: dict[str, str] = {}
    rows = []
    for seg in transcript["segments"]:
        raw_label = seg.get("speaker") or "SPEAKER_00"
        label = labels.setdefault(raw_label, f"Speaker {len(labels) + 1}")
        rows.append(
            {
                "content": seg["text"],
                "start_time": round(seg["start"] * 1000),
                "end_time": round(seg["end"] * 1000),
                "speaker": label,
                "original_speaker": label,
            }
        )
    return {
        "id": entry["source_ref"],
        "name": entry["title"],
        "duration": entry["duration_ms"],
        "demo": True,
        "source_list": [
            {
                "data_type": "transaction",
                "data_id": "demo-transaction",
                "data_tab_name": None,
                "data_content": json.dumps(rows, ensure_ascii=False),
            }
        ],
        "note_list": [
            {
                "data_type": "auto_sum_note",
                "data_id": "demo-summary",
                "data_tab_name": "Summary",
                "data_content": summary_md,
            }
        ],
    }


def build(media_dir: Path, out: Path, canned_dir: Path) -> dict[str, str]:
    entries = tomllib.loads((HERE / "sources.toml").read_text())["recording"]
    ids: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="recordings-demo-state-") as tmp:
        state = Path(tmp) / "state"
        init_archive(out, state, writer_id="demo", archive_uuid=DEMO_UUID, created_at=BUILD_AT)
        writer = Writer(out, State.open(state), Index(Path(tmp) / "index.db"), writer_id="demo")
        for n, entry in enumerate(entries):
            media = Path(media_dir) / f"{entry['slug']}.{entry['ext']}"
            recorded_at = datetime.fromisoformat(entry["recorded_at"]).replace(
                tzinfo=ZoneInfo(entry["timezone"])
            )
            sha = sha256_file(media)
            rid = make_id(recorded_at, sha)
            ids[rid] = entry["slug"]
            backend = CannedBackend(canned_dir, {rid: entry["slug"]})
            stamp = BUILD_AT + timedelta(minutes=n)
            renditions = [
                Rendition(
                    kind="transcript",
                    engine="canned",
                    model=WHISPER["model"],
                    version=WHISPER["version"],
                    created_at=stamp,
                    inputs={"audio_sha256": sha},
                    meta={"demo": True},
                    payload=backend.transcribe(media, recording_id=rid).model_dump(
                        exclude_none=True
                    ),
                )
            ]
            for i, note in enumerate(entry.get("notes", []), start=1):
                done = backend.complete(
                    CompletionRequest(
                        prompt="(canned)",
                        model=note["model"],
                        recording_id=rid,
                        note_type=note["note_type"],
                    )
                )
                renditions.append(
                    Rendition(
                        kind="notes",
                        note_type=note["note_type"],
                        engine="canned",
                        model=done.model,
                        version=f"{done.model}@demo",
                        created_at=stamp + timedelta(seconds=i),
                        inputs={"audio_sha256": sha},
                        meta={"demo": True},
                        payload={"markdown": done.text},
                    )
                )
            payload = None
            fetched_at = None
            if entry.get("plaud"):
                summary = (canned_dir / "notes" / entry["slug"] / "plaud.md").read_text(
                    encoding="utf-8"
                )
                envelope = plaud_envelope(entry, renditions[0].payload, summary)
                payload = json.dumps(envelope, indent=2, ensure_ascii=False).encode() + b"\n"
                fetched_at = stamp + timedelta(seconds=30)
            my_notes_path = canned_dir / "my-notes" / f"{entry['slug']}.md"
            added = writer.add(
                Incoming(
                    media=media,
                    recorded_at=recorded_at,
                    timezone_name=entry["timezone"],
                    time_source=entry["time_source"],
                    title=entry["title"],
                    kind=entry["kind"],
                    duration_ms=entry["duration_ms"],
                    sources=(
                        RawSource(
                            kind=entry["source_kind"],
                            ref=entry["source_ref"],
                            added_at=BUILD_AT,
                            fetched_at=fetched_at,
                            payload=payload,
                        ),
                    ),
                    title_by="plaud" if entry.get("plaud") else None,
                    tags=tuple(TagRef(**t) for t in entry.get("tags", [])),
                    renditions=tuple(renditions),
                    my_notes=(
                        my_notes_path.read_text(encoding="utf-8")
                        if my_notes_path.is_file()
                        else None
                    ),
                )
            )
            if entry.get("plaud"):
                writer.reconcile_plaud(
                    added.recording.id
                )  # Plaud's outputs, as the sync makes them
    return dict(sorted(ids.items()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--media-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--canned", type=Path, default=HERE / "canned")
    ap.add_argument("--force", action="store_true", help="replace an existing --out")
    args = ap.parse_args(argv)
    if args.out.exists():
        if not args.force:
            print(f"{args.out} exists; pass --force to rebuild it", file=sys.stderr)
            return 1
        shutil.rmtree(args.out)
    ids = build(args.media_dir, args.out, args.canned)
    (args.canned / "aliases.json").write_text(json.dumps(ids, indent=2) + "\n")
    print(f"built {len(ids)} recordings into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
