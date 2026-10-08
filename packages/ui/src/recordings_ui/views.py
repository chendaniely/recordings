"""Pure functions from the archive to the JSON the React client draws (spec §12.1).

Kept out of shiny_app.py so they are testable without a session (shinyreact-build-app
skill, "Verify it": factor pure logic out of the app file).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from markdown_it import MarkdownIt

from recordings.archive import Archive, Problem
from recordings.ids import ID_RE
from recordings.models import Recording, Rendition, is_private, is_private_tag, is_untagged

# html=False: any HTML in model-written notes is escaped, never rendered (review focus #4).
# No images: an image would make the browser fetch it, and the app makes no external requests.
_md = MarkdownIt("commonmark", {"html": False}).enable("table").disable("image")


def render_markdown(text: str) -> str:
    return _md.render(text)


def when_label(t: datetime) -> str:
    return f"{t:%a %b} {t.day} {t.year} · {t:%H:%M}"


def duration_label(ms: int | None) -> str | None:
    if ms is None:
        return None
    seconds = round(ms / 1000)
    if seconds < 60:
        return f"{seconds} s"
    minutes = seconds // 60
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def _tags(rec: Recording) -> list[dict]:
    return [{"tag": t.tag, "by": t.by} for t in rec.tags]


def _row(rec: Recording) -> dict:
    return {
        "id": rec.id,
        "title": rec.title,
        "recorded_at": rec.recorded_at.isoformat(),
        "when": when_label(rec.recorded_at),
        "duration": duration_label(rec.media.duration_ms),
        "kind": rec.media.kind,
        "sources": sorted({s.kind for s in rec.sources}),
        "tags": _tags(rec),
        "private": is_private(rec),
        "untagged": is_untagged(rec),
    }


def library_view(archive: Archive) -> dict:
    recordings = sorted(archive.iter_recordings(), key=lambda r: r.recorded_at, reverse=True)
    counts = Counter(t.tag for rec in recordings for t in rec.tags)
    return {
        "recordings": [_row(r) for r in recordings],
        "counts": {"all": len(recordings), "untagged": sum(is_untagged(r) for r in recordings)},
        "tags": [
            {"tag": tag, "count": n, "private": is_private_tag(tag)}
            for tag, n in sorted(counts.items()) if not tag.startswith("notes/")
        ],
        "note_types": [
            {"note_type": tag.removeprefix("notes/"), "count": n}
            for tag, n in sorted(counts.items()) if tag.startswith("notes/")
        ],
        "problems": [{"path": str(p.path), "message": p.message} for p in archive.problems],
    }


def _turns(r: Rendition, speakers: dict[str, str]) -> list[dict]:
    out = []
    for seg in r.payload.get("segments", []):
        speaker = seg.get("speaker")
        out.append({
            "start": seg["start"],
            "end": seg["end"],
            "speaker": speakers.get(speaker, speaker) if speaker else None,
            "text": seg["text"],
            "words": [{"word": w["word"], "start": w["start"], "end": w["end"]}
                      for w in seg.get("words", [])],
        })
    return out


def _label(r: Rendition) -> str:
    return "Plaud" if r.engine == "plaud" else f"{(r.model or r.engine).split('/')[-1]} · {r.engine}"


def recording_view(archive: Archive, rid: str) -> dict | None:
    if not ID_RE.fullmatch(rid or ""):
        return None
    try:
        rec = archive.load(rid)
    except (KeyError, ValueError):  # unknown id, impossible date in the id, or a broken file
        return None
    problems: list[Problem] = []
    outputs = archive.renditions(rid, problems)
    transcripts = [
        {"rendition": path, "label": _label(r), "engine": r.engine, "model": r.model,
         "created_at": r.created_at.isoformat(), "turns": _turns(r, rec.speakers)}
        for path, r in outputs if r.kind == "transcript"
    ]
    names = [t["rendition"] for t in transcripts]
    chosen = rec.chosen.transcript if rec.chosen.transcript in names else (names[-1] if names else None)

    groups: dict[str, list[dict]] = {}
    for path, r in reversed(outputs):  # newest first inside each group
        if r.kind == "notes" and r.engine != "plaud":
            groups.setdefault(r.note_type, []).append({
                "rendition": path, "model": r.model, "engine": r.engine,
                "created_at": r.created_at.isoformat(),
                "html": render_markdown(r.payload["markdown"]),
            })
    my_notes = archive.read_my_notes(rid)
    return {
        "id": rec.id,
        "title": rec.title,
        "recorded_at": rec.recorded_at.isoformat(),
        "when": when_label(rec.recorded_at),
        "timezone": rec.timezone,
        "duration": duration_label(rec.media.duration_ms),
        "kind": rec.media.kind,
        "private": is_private(rec),
        "media_url": f"/media/{rec.id}",
        "tags": _tags(rec),
        "sources": [{"kind": s.kind, "ref": s.ref, "added_at": s.added_at.isoformat()}
                    for s in rec.sources],
        "transcripts": transcripts,
        "chosen_transcript": chosen,
        "notes": [{"note_type": k, "outputs": v} for k, v in sorted(groups.items())],
        "plaud_notes": [
            {"rendition": path, "created_at": r.created_at.isoformat(),
             "html": render_markdown(r.payload["markdown"])}
            for path, r in outputs if r.kind == "notes" and r.engine == "plaud"
        ],
        "my_notes_html": render_markdown(my_notes) if my_notes is not None else None,
        "renditions": [
            {"rendition": path, "kind": r.kind, "note_type": r.note_type, "engine": r.engine,
             "model": r.model, "created_at": r.created_at.isoformat()}
            for path, r in outputs
        ],
        "problems": [{"path": str(p.path), "message": p.message} for p in problems],
    }
