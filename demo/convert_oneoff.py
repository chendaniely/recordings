"""Turn audio-router one-off transcriber JSON into canned TranscriptPayload files.

Segments are Whisper's own (sentence-sized, good for highlighting); each gets the
diarization speaker it overlaps most and the words whose midpoint falls inside it.

    uv run python demo/convert_oneoff.py
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from recordings.models import Segment, TranscriptPayload, Word

HERE = Path(__file__).resolve().parent


def speaker_for(start: float, end: float, turns: list[dict]) -> str | None:
    best, best_overlap = None, 0.0
    for t in turns:
        overlap = min(end, t["end"]) - max(start, t["start"])
        if overlap > best_overlap:
            best, best_overlap = t["speaker"], overlap
    return best


def convert(data: dict, duration_s: float) -> TranscriptPayload:
    words = [w for w in data["words"] if w.get("start") is not None and w.get("end") is not None]
    segments = []
    for seg in data["segments"]:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        # Whisper can hallucinate text past the end of a trimmed clip.
        if seg["start"] >= duration_s:
            continue
        inside = [w for w in words if seg["start"] <= (w["start"] + w["end"]) / 2 < seg["end"]]
        segments.append(Segment(
            start=round(seg["start"], 3),
            end=round(seg["end"], 3),
            speaker=speaker_for(seg["start"], seg["end"], data.get("turns") or []),
            text=text,
            words=[Word(word=w["word"].strip(), start=round(w["start"], 3), end=round(w["end"], 3))
                   for w in inside],
        ))
    return TranscriptPayload(language=data.get("language"), segments=segments)


def main() -> int:
    out = HERE / "canned" / "transcripts"
    out.mkdir(parents=True, exist_ok=True)
    entries = tomllib.loads((HERE / "sources.toml").read_text())["recording"]
    durations = {e["slug"]: e["duration_ms"] / 1000 for e in entries}
    for path in sorted((HERE / ".cache" / "oneoff").glob("*.json")):
        if path.stem not in durations:
            raise SystemExit(f"{path.name}: slug {path.stem!r} is not in demo/sources.toml")
        payload = convert(json.loads(path.read_text(encoding="utf-8")), durations[path.stem])
        (out / path.name).write_text(
            payload.model_dump_json(exclude_none=True, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out / path.name}: {len(payload.segments)} segments")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
