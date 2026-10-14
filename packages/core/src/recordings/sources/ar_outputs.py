"""audio-router's outputs, and Pocket's payload, as recordings outputs (spec §9.3).

| audio-router                           | recordings                                          |
|----------------------------------------|-----------------------------------------------------|
| words, merged                          | transcript, keeping engine and version              |
| turns (pyannote)                       | speakers                                            |
| summary                                | notes, note type audio-router-summary               |
| speakers by engine human               | kept verbatim in source/: the names Dan typed       |
| anything whose engine is plaud         | skipped: reconcile rebuilds Plaud's outputs (§9.1.1) |
| fingerprint, embeddings                | skipped: voice data never enters the archive (§7.6)  |

A segment, turn or word with no time is kept, never dropped with its text: it sits at the
previous one's end, as reconcile places Plaud's untimed segments, and `meta` counts them
(`untimed_segments`, `untimed_words`). audio-router's merge keeps untimed words where they arrived,
and one can open a turn (its stages/merge.py and archive/schemas.py MergedTurn).

Payload shapes are audio-router's worker.py and stages/ at commit 64612df. Pocket keeps its
transcript and summaries inside its raw payload. They become outputs with engine `pocket`, built
from source/ as reconcile builds Plaud's.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from recordings.models import Rendition
from recordings.plaud.normalise import canonical_json, sha256_of

IMPORT_VERSION = 1
SKIPPED = {
    "plaud": "audio-router's Plaud outputs are rebuilt by reconcile",
    "fingerprint": "content fingerprints are not an output kind",
    "embeddings": "voice data never enters the archive (spec §7.6)",
}


@dataclass(frozen=True)
class Mapped:
    rendition: Rendition | None = None
    skipped: str | None = None  # a fixed reason, never content
    keep_raw: bool = False  # keep the file verbatim in source/ instead (typed speaker names)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _timed(item: Any) -> bool:
    return (isinstance(item, dict) and _number(item.get("start")) and _number(item.get("end"))
            and 0 <= item["start"] <= item["end"])


def _times(rows: list[dict], start: float = 0.0) -> tuple[list[tuple[float, float]], int]:
    """Each row's (start, end), and how many had none: such a row sits at the previous row's end,
    as reconcile places an untimed Plaud segment."""
    out, untimed, previous = [], 0, start
    for row in rows:
        if _timed(row):
            begin, end = float(row["start"]), float(row["end"])
        else:
            untimed += 1
            begin = end = previous
        previous = end
        out.append((begin, end))
    return out, untimed


def ar_stamp(path: Path) -> datetime | None:
    """The UTC stamp a rendition's file name ends with (audio-router's store._rendition_stamp)."""
    try:
        return datetime.strptime(path.stem.rsplit("-", 1)[-1], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def _words(items: Any, start: float) -> tuple[list[dict], int]:
    """Every word of a merged turn, in order, the untimed ones included and counted."""
    rows = [w for w in items or [] if isinstance(w, dict)]
    times, untimed = _times(rows, start)
    return [{"word": str(w.get("word") or ""), "start": begin, "end": end}
            for w, (begin, end) in zip(rows, times)], untimed


def _words_transcript(payload: dict) -> tuple[dict, int] | None:
    """whisper's segments, each with the timed words nearest to it: a word whose middle falls
    inside a segment goes there, and one in a gap between two segments goes to the nearer one (the
    earlier on a tie), so no timed word is dropped. An untimed word can't be placed by time; its
    text is still in its segment's text."""
    rows = [s for s in payload.get("segments") or [] if isinstance(s, dict)]
    if not rows:
        return None
    times, untimed = _times(rows)
    words = sorted(({"word": str(w.get("word") or ""), "start": float(w["start"]),
                     "end": float(w["end"])} for w in payload.get("words") or [] if _timed(w)),
                   key=lambda w: (w["start"] + w["end"]) / 2)
    starts, ends = [start for start, _ in times], [end for _, end in times]
    inside: list[list[dict]] = [[] for _ in rows]
    for word in words:
        middle = (word["start"] + word["end"]) / 2
        n = bisect_left(ends, middle)  # the first segment that ends at or after the word's middle
        if n == len(rows):
            n -= 1
        elif n and middle - ends[n - 1] <= max(0.0, starts[n] - middle):
            n -= 1  # in the gap, and no nearer to the next segment than to the one before
        inside[n].append(word)
    out = [{"start": start, "end": end, "text": str(seg.get("text") or "").strip(),
            "words": found} for seg, (start, end), found in zip(rows, times, inside)]
    return {"segments": out}, untimed


def _merged_transcript(payload: dict) -> tuple[dict, int, int] | None:
    turns = [t for t in payload.get("turns") or [] if isinstance(t, dict)]
    if not turns:
        return None
    times, untimed = _times(turns)
    segments, untimed_words = [], 0
    for turn, (start, end) in zip(turns, times):
        words, missing = _words(turn.get("words"), start)
        untimed_words += missing
        segments.append({"start": start, "end": end, "text": str(turn.get("text") or "").strip(),
                         **({"speaker": str(turn["speaker"])} if turn.get("speaker") else {}),
                         "words": words})
    return {"segments": segments}, untimed, untimed_words


def _summary_markdown(payload: dict) -> str:
    parts = []
    if isinstance(payload.get("title"), str) and payload["title"].strip():
        parts.append(f"# {payload['title'].strip()}")
    if isinstance(payload.get("summary_md"), str) and payload["summary_md"].strip():
        parts.append(payload["summary_md"].strip())
    actions = [a for a in payload.get("actions") or []
               if isinstance(a, dict) and str(a.get("text") or "").strip()]
    if actions:
        lines = []
        for action in actions:
            owner = str(action.get("owner_raw") or "").strip()
            lines.append(f"- {str(action['text']).strip()}" + (f" ({owner})" if owner else ""))
        parts.append("## Actions\n\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _counted(common: dict, **counts: int) -> dict:
    """`common` with any non-zero untimed counts added to its meta."""
    return {**common, "meta": {**common["meta"], **{k: v for k, v in counts.items() if v}}}


def map_rendition(path: Path, doc: object) -> Mapped:
    if not isinstance(doc, dict):
        return Mapped(skipped="not a JSON object")
    kind, engine, version = doc.get("kind"), doc.get("engine"), doc.get("version")
    if not all(isinstance(x, str) and x for x in (kind, engine, version)):
        return Mapped(skipped="no kind, engine or version")
    if engine.split("/")[0] == "plaud":
        return Mapped(skipped=SKIPPED["plaud"])
    if kind in ("fingerprint", "embeddings"):
        return Mapped(skipped=SKIPPED[kind])
    if kind == "speakers" and engine == "human":
        return Mapped(keep_raw=True)  # the names Dan typed (§9.3): verbatim, never an output
    created = ar_stamp(path)
    if created is None:
        return Mapped(skipped="no UTC stamp in its file name")
    payload = doc["payload"] if isinstance(doc.get("payload"), dict) else {}
    meta_in = doc["meta"] if isinstance(doc.get("meta"), dict) else {}
    inputs = {k: str(v) for k, v in (doc.get("inputs") or {}).items()
              if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
    common = dict(engine=engine, version=version, created_at=created, inputs=inputs,
                  meta={"imported_from": "audio-router", "file": path.name,
                        "audio_router_meta": meta_in})
    try:
        if kind == "words":
            built = _words_transcript(payload)
            if built is None:
                return Mapped(skipped="a transcript with no segments")
            body, untimed = built
            model = meta_in.get("model") if isinstance(meta_in.get("model"), str) else None
            return Mapped(Rendition(kind="transcript", model=model, payload=body,
                                    **_counted(common, untimed_segments=untimed)))
        if kind == "merged":
            built = _merged_transcript(payload)
            if built is None:
                return Mapped(skipped="a transcript with no segments")
            body, untimed, untimed_words = built
            return Mapped(Rendition(kind="transcript", payload=body,
                                    **_counted(common, untimed_segments=untimed,
                                               untimed_words=untimed_words)))
        if kind == "turns":
            return Mapped(Rendition(kind="speakers", payload={
                "turns": list(payload.get("turns") or []),
                "overlap_turns": list(payload.get("overlap_turns") or [])}, **common))
        if kind == "summary":
            markdown = _summary_markdown(payload)
            return (Mapped(Rendition(kind="notes", note_type="audio-router-summary",
                                     model=engine.split("/")[-1], payload={"markdown": markdown},
                                     **common))
                    if markdown else Mapped(skipped="an empty summary"))
    except ValidationError:
        return Mapped(skipped="does not fit the output schema")
    shown = kind if re.fullmatch(r"[a-z_]{1,40}", kind) else "other"  # a file's text is never shown
    return Mapped(skipped=f"an output kind recordings has no place for ({shown})")


def pocket_outputs(doc: dict, raw: str, fetched_at: datetime) -> list[Rendition]:
    """Pocket's transcript and summaries, from its raw payload. Times are read as seconds, or as
    milliseconds when the last timed segment ends past twice the duration (stage-2a plan,
    answered question 5). A segment with no time is kept and counted."""
    out = []
    transcript = doc.get("transcript") if isinstance(doc.get("transcript"), dict) else {}
    rows = [s for s in transcript.get("segments") or [] if isinstance(s, dict)]
    version = f"pocket@{IMPORT_VERSION}"
    if rows:
        duration = doc.get("duration")
        ends = [r["end"] for r in rows if _timed(r)]
        in_ms = _number(duration) and duration > 0 and bool(ends) and max(ends) > 2 * duration
        scale = 1000.0 if in_ms else 1.0
        times, untimed = _times(rows)
        segments = [{"start": start / scale, "end": end / scale,
                     "text": str(r.get("text") or "").strip(),
                     **({"speaker": str(r["speaker"])} if r.get("speaker") else {})}
                    for r, (start, end) in zip(rows, times)]
        meta: dict[str, Any] = {"time_unit": "ms" if in_ms else "s"}
        if untimed:
            meta["untimed_segments"] = untimed
        out.append(Rendition(kind="transcript", engine="pocket", model="pocket", version=version,
                             created_at=fetched_at,
                             inputs={"source": raw, "part_sha256": sha256_of(
                                 [segments, untimed, meta["time_unit"]])},
                             meta=meta, payload={"segments": segments}))
    summaries = doc.get("summarizations") if isinstance(doc.get("summarizations"), dict) else {}
    n = 0
    for key in sorted(summaries):
        value = summaries[key]
        text = (value["text"] if isinstance(value, dict) and isinstance(value.get("text"), str)
                else canonical_json(value))
        if not text.strip():
            continue
        n += 1
        out.append(Rendition(kind="notes",
                             note_type="pocket-summary" if n == 1 else f"pocket-summary-{n}",
                             engine="pocket", model="pocket", version=version,
                             created_at=fetched_at,
                             inputs={"source": raw, "part_sha256": sha256_of(text.strip())},
                             payload={"markdown": text.strip()}))
    return out
