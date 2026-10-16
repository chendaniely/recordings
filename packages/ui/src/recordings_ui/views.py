"""Pure functions from the archive to the JSON the React client draws (spec §12.1).

Kept out of shiny_app.py so they are testable without a session (shinyreact-build-app
skill, "Verify it": factor pure logic out of the app file).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from markdown_it import MarkdownIt
from recordings.archive import Archive, Problem
from recordings.ids import ID_RE
from recordings.models import Recording, Rendition, is_private, is_private_tag, is_untagged

from recordings_ui.status import StatusSettings
from recordings_ui.status import status_view as _status_view

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


def row(rec: Recording) -> dict:
    """One Library row. The tag service patches rows with it after a tag change (spec §5)."""
    return {
        "id": rec.id,
        "title": rec.title,
        "recorded_at": rec.recorded_at.isoformat(),
        "when": when_label(rec.recorded_at),
        "duration": duration_label(rec.media.duration_ms),
        "duration_ms": rec.media.duration_ms,
        "kind": rec.media.kind,
        "sources": sorted({s.kind for s in rec.sources}),
        "tags": _tags(rec),
        "private": is_private(rec),
        "untagged": is_untagged(rec),
    }


def summarise(rows: Iterable[dict], known_types: Iterable[str] = ()) -> dict:
    """The library view's counts, tags (each with its `private` flag) and note types, from its
    rows alone. The tag service recounts them after every change (spec §5); the browser never
    re-implements the privacy rule. A note type in `known_types` that no row carries is listed
    with a count of 0: the archive still has it, through an exclusion, so the picker and the
    sidebar keep offering it (tagging plan, decision 18)."""
    rows = list(rows)
    counts = Counter(t["tag"] for r in rows for t in r["tags"])
    types = dict.fromkeys(known_types, 0)
    types |= {t.removeprefix("notes/"): n for t, n in counts.items() if t.startswith("notes/")}
    return {
        "counts": {"all": len(rows), "untagged": sum(not r["tags"] for r in rows)},
        "tags": [
            {"tag": tag, "count": n, "private": is_private_tag(tag)}
            for tag, n in sorted(counts.items())
            if not tag.startswith("notes/")
        ],
        "note_types": [{"note_type": t, "count": n} for t, n in sorted(types.items())],
    }


def library_view_from(recordings: Iterable[Recording], problems: Iterable[Problem]) -> dict:
    ordered = sorted(recordings, key=lambda r: r.recorded_at, reverse=True)
    rows = [row(r) for r in ordered]
    excluded = {n for r in ordered for n in r.excluded_note_types}  # decision 18
    return {
        "recordings": rows,
        **summarise(rows, excluded),
        "problems": [{"path": str(p.path), "message": p.message} for p in problems],
    }


def library_view(archive: Archive) -> dict:
    recordings = list(archive.iter_recordings())  # fills archive.problems as it goes
    return library_view_from(recordings, archive.problems)


def _turns(r: Rendition) -> list[dict]:
    out = []
    for seg in r.payload.get("segments", []):
        # Until people arrive (stage 3b), a turn shows the name Plaud gave its speaker, if any,
        # and otherwise the diarization label.
        speaker = seg.get("speaker_name") or seg.get("speaker")
        out.append(
            {
                "start": seg["start"],
                "end": seg["end"],
                "speaker": speaker or None,
                "text": seg["text"],
                "words": [
                    {"word": w["word"], "start": w["start"], "end": w["end"]}
                    for w in seg.get("words", [])
                ],
            }
        )
    return out


def _label(r: Rendition) -> str:
    return (
        "Plaud" if r.engine == "plaud" else f"{(r.model or r.engine).split('/')[-1]} · {r.engine}"
    )


def _media_url(
    archive: Archive, rec: Recording, media_base: str | None, problems: list[Problem]
) -> str | None:
    """The server's own route, which checks the file itself; or, with a base (the static Pages
    demo, spec §17.1), the file under it, whose extension sets the Content-Type. recording.json
    is hand-editable, so then media.file must be a plain name: no folder, no way out of the base.
    """
    if media_base is None:
        return f"/media/{rec.id}"
    name = rec.media.file
    if name in ("", ".", "..") or "/" in name or "\\" in name:
        problems.append(
            Problem(
                path=archive.path_for(rec.id) / "recording.json",
                message=f"media.file {name!r} is not a plain file name",
            )
        )
        return None
    return f"{media_base}{quote(name, safe='')}"


def recording_view(archive: Archive, rid: str, *, media_base: str | None = None) -> dict | None:
    if not ID_RE.fullmatch(rid or ""):
        return None
    try:
        rec = archive.load(rid)
    except (KeyError, ValueError):  # unknown id, impossible date in the id, or a broken file
        return None
    problems: list[Problem] = []
    outputs = archive.renditions(rid, problems)
    transcripts = [
        {
            "rendition": path,
            "label": _label(r),
            "engine": r.engine,
            "model": r.model,
            "created_at": r.created_at.isoformat(),
            "turns": _turns(r),
        }
        for path, r in outputs
        if r.kind == "transcript"
    ]
    names = [t["rendition"] for t in transcripts]
    chosen = (
        rec.chosen.transcript if rec.chosen.transcript in names else (names[-1] if names else None)
    )

    groups: dict[str, list[dict]] = {}
    for path, r in reversed(outputs):  # newest first inside each group
        if r.kind == "notes" and r.engine != "plaud":
            groups.setdefault(r.note_type, []).append(
                {
                    "rendition": path,
                    "model": r.model,
                    "engine": r.engine,
                    "created_at": r.created_at.isoformat(),
                    "html": render_markdown(r.payload["markdown"]),
                }
            )
    my_notes = archive.read_my_notes(rid)
    media_url = _media_url(archive, rec, media_base, problems)  # before problems is listed
    return {
        "id": rec.id,
        "title": rec.title,
        "recorded_at": rec.recorded_at.isoformat(),
        "when": when_label(rec.recorded_at),
        "timezone": rec.timezone,
        "duration": duration_label(rec.media.duration_ms),
        "kind": rec.media.kind,
        "private": is_private(rec),
        "media_url": media_url,
        "tags": _tags(rec),
        "sources": [
            {"kind": s.kind, "ref": s.ref, "added_at": (s.fetched_at or s.added_at).isoformat()}
            for s in rec.sources
        ],
        "transcripts": transcripts,
        "chosen_transcript": chosen,
        "notes": [{"note_type": k, "outputs": v} for k, v in sorted(groups.items())],
        "plaud_notes": [
            {
                "rendition": path,
                "created_at": r.created_at.isoformat(),
                "html": render_markdown(r.payload["markdown"]),
            }
            for path, r in outputs
            if r.kind == "notes" and r.engine == "plaud"
        ],
        "my_notes_html": render_markdown(my_notes) if my_notes is not None else None,
        "renditions": [
            {
                "rendition": path,
                "kind": r.kind,
                "note_type": r.note_type,
                "engine": r.engine,
                "model": r.model,
                "created_at": r.created_at.isoformat(),
            }
            for path, r in outputs
        ],
        "problems": [{"path": str(p.path), "message": p.message} for p in problems],
    }


def status_view(archive: Archive, settings: StatusSettings | None) -> dict:
    return _status_view(archive.root, settings)


SNIPPET_CHARS = 160  # spec §5
_STAMP = re.compile(r"-(\d{8}T\d{6}Z)\.json$")  # rendition_filename's UTC stamp


def _stamp(name: str) -> tuple[str, str]:
    found = _STAMP.search(name)
    return (found.group(1) if found else "", name)


def plain_text(markdown: str) -> str:
    """Markdown as plain words, for a snippet: the text of each inline run, and nothing else.
    The browser draws snippets as text nodes, never as HTML (spec §5)."""
    words: list[str] = []
    for token in _md.parse(markdown):
        for child in token.children or []:
            if child.type in ("text", "code_inline"):
                words.append(child.content)
            elif child.type in ("softbreak", "hardbreak"):
                words.append(" ")
        words.append(" ")
    return " ".join("".join(words).split())


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _transcript_text(path: Path, limit: int) -> str:
    try:
        rendition = Rendition.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return ""
    out: list[str] = []
    size = 0
    for segment in rendition.payload.get("segments", []):
        out.append(str(segment.get("text", "")))
        size += len(out[-1]) + 1
        if size > limit:
            break
    return " ".join(out)


def _notes_text(path: Path) -> str:
    try:
        rendition = Rendition.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return ""
    if rendition.kind != "notes" or rendition.engine != "plaud":
        return ""
    return plain_text(str(rendition.payload.get("markdown", "")))


def snippet(archive: Archive, rid: str, *, limit: int = SNIPPET_CHARS) -> str | None:
    """A recording's first words, as plain text (spec §5): from its chosen transcript (or the
    newest), or else its first Plaud note. It reads that one file (tagging plan, decision 11)
    and stops at `limit` characters. None: nothing to show yet."""
    try:
        rec = archive.load(rid)
        folder = archive.path_for(rid) / "renditions"
        names = sorted(p.name for p in folder.glob("*.json"))
    except (KeyError, ValueError, OSError):
        return None
    transcripts = [n for n in names if n.startswith("transcript-")]
    chosen = (rec.chosen.transcript or "").removeprefix("renditions/")
    pick = chosen if chosen in transcripts else max(transcripts, key=_stamp, default=None)
    text = _transcript_text(folder / pick, limit) if pick else ""
    if not text:
        notes = sorted((n for n in names if n.startswith("notes-plaud-")), key=_stamp)
        text = _notes_text(folder / notes[0]) if notes else ""
    return _clip(text, limit) if text else None
