"""Plaud's JSON note blocks as Markdown (spec §9.1.1).

Adapted from audio-router's `archive/vendor_blocks.py` at commit 64612df: Dan's own code,
relicensed MIT for this repo (§19). An outline is a list of {topic, start_time, end_time}.
Highlights and memos are lists whose field names vary between blocks; a highlight can carry a
picture link its text doesn't mention, and it is kept. Anything unrecognised degrades to the raw
text: a renderer that raised would turn an unfamiliar payload into a missing note, which is worse
than the JSON it replaces.

Reconcile renders the raw block, never the normalised one, so links stay whole. A change here
changes the outputs, so it bumps NORMALISER_VERSION too: `version` must change whenever the
output would (§6.5).
"""

from __future__ import annotations

import json
from typing import Any


def _rows(raw: str) -> list[dict] | None:
    if not raw or raw.lstrip()[:1] != "[":
        return None
    try:
        rows = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(rows, list) or not rows:
        return None
    return rows if all(isinstance(r, dict) for r in rows) else None


def _stamp(ms: Any) -> str:
    """`[MM:SS]`, or `[--:--]` when there is no timing: never a made-up 00:00."""
    if not isinstance(ms, (int, float)) or isinstance(ms, bool):
        return "[--:--]"
    s = int(ms // 1000)
    return f"[{s // 60:02d}:{s % 60:02d}]"


def render_outline(raw: str) -> str | None:
    rows = _rows(raw)
    if rows is None or not any("topic" in r for r in rows):
        return None
    lines = [f"- {_stamp(r.get('start_time'))} {str(r.get('topic') or '').strip()}"
             for r in rows if str(r.get("topic") or "").strip()]
    return "\n".join(lines) or None


def render_marks(raw: str) -> str | None:
    rows = _rows(raw)
    if rows is None or not any(("mark_content" in r or "content" in r or "title" in r)
                               for r in rows):
        return None
    out = []
    for r in rows:
        if r.get("is_user_deleted"):
            continue
        title = str(r.get("title") or "").strip()
        body = next((str(r.get(k) or "").strip() for k in ("expanded_content", "content",
                                                           "mark_content")
                     if str(r.get(k) or "").strip()), "")
        picture = str(r.get("picture_link") or r.get("picture_full_url") or "").strip()
        if not title and not body and not picture:
            continue
        stamp = _stamp(r.get("timestamp"))
        entry = f"**{stamp} {title}**" if title else f"**{stamp}**"
        if body:
            entry += f"\n\n{body}"
        if picture:
            entry += f"\n\n(image: {picture})"
        out.append(entry)
    return "\n\n".join(out) or None


def render_note(block: dict) -> str:
    content = block.get("data_content")
    if not isinstance(content, str):
        return ""
    kind = block.get("data_type")
    if kind == "outline":
        text = render_outline(content)
    elif kind in ("high_light", "mark_memo"):
        text = render_marks(content)
    else:
        text = None
    return (text if text is not None else content).strip()
