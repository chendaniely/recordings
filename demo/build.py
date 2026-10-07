"""Build demo/archive from encoded media + demo/canned. Deterministic: fixed stamps, no clock.

    uv run python demo/build.py --media-dir demo/.cache/media --out demo/archive --force
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from recordings.archive import Archive, RawSource, sha256_file
from recordings.backends import CannedBackend, CompletionRequest
from recordings.ids import make_id
from recordings.models import Rendition, TagRef
from recordings.selfdoc import write_docs

HERE = Path(__file__).resolve().parent
# Every stamp in the demo derives from this instant, so a rebuild is byte-identical.
BUILD_AT = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
WHISPER = {"model": "mlx-community/whisper-large-v3-turbo", "version": "large-v3-turbo@a4aaeec"}


def build(media_dir: Path, out: Path, canned_dir: Path) -> dict[str, str]:
    entries = tomllib.loads((HERE / "sources.toml").read_text())["recording"]
    archive = Archive(out)
    ids: dict[str, str] = {}
    for n, entry in enumerate(entries):
        media = Path(media_dir) / f"{entry['slug']}.{entry['ext']}"
        recorded_at = datetime.fromisoformat(entry["recorded_at"]).replace(
            tzinfo=ZoneInfo(entry["timezone"]))
        sha = sha256_file(media)
        rid = make_id(recorded_at, sha)
        ids[rid] = entry["slug"]
        backend = CannedBackend(canned_dir, {rid: entry["slug"]})
        stamp = BUILD_AT + timedelta(minutes=n)
        renditions = [Rendition(
            kind="transcript", engine="canned", model=WHISPER["model"],
            version=WHISPER["version"], created_at=stamp,
            inputs={"audio_sha256": sha}, meta={"demo": True},
            payload=backend.transcribe(media, recording_id=rid).model_dump(exclude_none=True),
        )]
        for i, note in enumerate(entry.get("notes", []), start=1):
            done = backend.complete(CompletionRequest(
                prompt="(canned)", model=note["model"], recording_id=rid,
                note_type=note["note_type"]))
            renditions.append(Rendition(
                kind="notes", note_type=note["note_type"], engine="canned", model=done.model,
                version=f"{done.model}@demo", created_at=stamp + timedelta(seconds=i),
                inputs={"audio_sha256": sha}, meta={"demo": True},
                payload={"markdown": done.text}))
        payload = None
        if entry.get("plaud"):
            transcript = renditions[0].payload
            renditions.append(Rendition(
                kind="transcript", engine="plaud", model="plaud", version="plaud@demo",
                created_at=stamp + timedelta(seconds=30), meta={"demo": True},
                payload=transcript))
            renditions.append(Rendition(
                kind="notes", note_type="plaud-summary", engine="plaud", model="plaud",
                version="plaud@demo", created_at=stamp + timedelta(seconds=31),
                meta={"demo": True},
                payload={"markdown": (canned_dir / "notes" / entry["slug"] / "plaud.md")
                         .read_text(encoding="utf-8")}))
            payload = json.dumps({"id": entry["source_ref"], "demo": True,
                                  "name": entry["title"]}, indent=2).encode() + b"\n"
        my_notes_path = canned_dir / "my-notes" / f"{entry['slug']}.md"
        archive.add_recording(
            media=media,
            recorded_at=recorded_at,
            timezone_name=entry["timezone"],
            time_source=entry["time_source"],
            title=entry["title"],
            kind=entry["kind"],
            duration_ms=entry["duration_ms"],
            source=RawSource(kind=entry["source_kind"], ref=entry["source_ref"],
                             added_at=BUILD_AT, payload=payload),
            tags=[TagRef(**t) for t in entry.get("tags", [])],
            renditions=renditions,
            my_notes=my_notes_path.read_text(encoding="utf-8") if my_notes_path.is_file() else None,
        )
    write_docs(archive.root)
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
