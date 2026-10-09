"""Plaud's payloads, normalised before hashing (spec §9.1.1). A pure, versioned function.

The import (stage 2a) and the sync (stage 2b) both go through it, so they agree on what changed.
A rule change bumps NORMALISER_VERSION. Both sides are then recomputed at compare time, so no
stored hash goes stale; the version is part of every Plaud output's `version`.

The rules:
- Dropped: the presigned audio URL, `data_link`, `_meta`, and the fetcher's own fields
  (`_fetched_from_data_link`, `_data_link_error`).
- Inside strings, only the X-Amz query part of a URL is replaced, never the whole string.
  audio-router once blanked a whole note this way, and lost every later edit to it.
- `source_list` and `note_list` are sorted by (type, tab, id). The segment arrays inside them are
  never reordered. A transcript's inner JSON is re-serialised canonically.
- `of_<hex>` and the bare hex are one ID.
- Everything else, unknown new fields included, is kept, so it counts as a change.

The normalised form is for hashing only. Notes are rendered from the raw block (`note_blocks`
hands back both): the URL scrub runs to the next space or quote, so it also eats a Markdown
link's closing parenthesis.

The envelope's shape is audio-router's `files/{id}` snapshot (archive/sources/plaud.py,
plaud_transcript.py and schemas.py at commit 64612df).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from recordings.refs import canonical_plaud_id

NORMALISER_VERSION = 1
DROPPED_KEYS = frozenset(
    {"presigned_url", "data_link", "_meta", "_fetched_from_data_link", "_data_link_error"})
SEGMENT_TYPES = frozenset({"transaction", "transaction_polish"})
OWN_NOTE_TYPES = frozenset({"high_light", "consumer_note", "mark_memo"})  # Dan's own notes
# What Plaud emits before it knows a person: "Speaker 1", "spk_2", bare numbers, "unknown".
GENERIC_SPEAKER = re.compile(r"(?i)^\s*(speaker[\s_-]*\d+|spk[\s_-]*\d+|unknown|\d+)\s*$")
PRESIGNED = "<presigned>"
_PRESIGNED_QUERY = re.compile(
    r"(https?://[^\s?#\"'\\]+)\?[^\s#\"'\\]*X-Amz-[^\s#\"'\\]*", re.I)
# A segment row's fields, by part: the text with its timing, and the names. A field outside both
# counts as "other", so a field Plaud starts adding to every row still reaches the circuit breaker.
_TEXT_KEYS = ("start_time", "end_time", "content", "original_speaker")
_NAME_KEYS = ("speaker", "embeddingKey")
_ROW_KEYS = frozenset(_TEXT_KEYS + _NAME_KEYS)


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _scrub(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {k: _scrub(v) for k, v in obj.items() if k not in DROPPED_KEYS}
        if out.get("data_type") in SEGMENT_TYPES and isinstance(obj.get("data_content"), str):
            # A segment block's content is JSON inside a string. Scrub the parsed leaves, never
            # the encoded text: there the URL is followed by an escape (`\\n`, `\\"`) that the
            # scrub would eat, breaking the JSON or merging two different texts.
            try:
                inner = json.loads(obj["data_content"])
            except json.JSONDecodeError:
                return out
            out["data_content"] = canonical_json(_scrub(inner))
        return out
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    if isinstance(obj, str) and "x-amz-" in obj.lower():
        return _PRESIGNED_QUERY.sub(lambda m: f"{m.group(1)}?{PRESIGNED}", obj)
    return obj


def _block_key(block: Any) -> tuple[str, str, str, str]:
    if not isinstance(block, dict):
        return ("", "", "", canonical_json(block))
    return (str(block.get("data_type") or ""), str(block.get("data_tab_name") or ""),
            str(block.get("data_id") or ""), canonical_json(block))


def _canonical_block(block: Any) -> Any:
    if (isinstance(block, dict) and block.get("data_type") in SEGMENT_TYPES
            and isinstance(block.get("data_content"), str)):
        try:
            inner = json.loads(block["data_content"])
        except json.JSONDecodeError:
            return block
        return {**block, "data_content": canonical_json(inner)}
    return block


def _normal_block(block: Any) -> Any:
    """One block as `normalise` leaves it."""
    return _canonical_block(_scrub(block))


def normalise(envelope: dict) -> dict:
    env = _scrub(envelope)
    if isinstance(env.get("id"), str):
        env["id"] = canonical_plaud_id(env["id"])
    for section in ("source_list", "note_list"):
        blocks = env.get(section)
        if isinstance(blocks, list):
            env[section] = sorted((_canonical_block(b) for b in blocks), key=_block_key)
    return env


def envelope_sha256(envelope: dict) -> str:
    return sha256_of(normalise(envelope))


def meta_fetched_at(envelope: Any) -> datetime | None:
    """When a legacy snapshot was fetched, from its own `_meta.fetched_at`.

    audio-router's retired rescue script stamped it (schemas.py, `PlaudMeta`); the snapshot's
    file name says only when audio-router wrote the file. None when it is absent, unparseable,
    or has no UTC offset: a naive time could be any zone, so the caller falls back to the file.
    """
    meta = envelope.get("_meta") if isinstance(envelope, dict) else None
    value = meta.get("fetched_at") if isinstance(meta, dict) else None
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return when.astimezone(timezone.utc) if when.tzinfo is not None else None


def link_errors(envelope: dict) -> list[str]:
    """Why a snapshot is incomplete: the data type of each block whose content didn't arrive,
    and `legacy` for each failure a legacy snapshot lists in `_meta.data_link_errors`. A snapshot
    with any is never a baseline, and nothing is built from it (§9.1.1).

    A block's content didn't arrive when it has a link error, or when it is empty and has a
    link: one never followed (`data_link`), or one followed that answered with an empty body
    (`_fetched_from_data_link`). An empty body is no more trustworthy than a failed link. Legacy
    snapshots, from audio-router's retired rescue script, record their failures in `_meta`."""
    out = []
    for section in ("source_list", "note_list"):
        for block in envelope.get(section) or []:
            if not isinstance(block, dict):
                continue
            empty = not str(block.get("data_content") or "").strip()
            linked = block.get("data_link") or block.get("_fetched_from_data_link")
            if block.get("_data_link_error") or (empty and linked):
                out.append(str(block.get("data_type") or "?"))
    meta = envelope.get("_meta")
    legacy = meta.get("data_link_errors") if isinstance(meta, dict) else None
    if legacy:
        out += ["legacy"] * (len(legacy) if isinstance(legacy, list) else 1)
    return out


def is_complete(envelope: dict) -> bool:
    return not link_errors(envelope)


def _rows_of(block: Any) -> list[dict] | None:
    """A segment block's rows, or None when it isn't one or its content isn't a JSON list of
    objects."""
    if not isinstance(block, dict) or block.get("data_type") not in SEGMENT_TYPES:
        return None
    content = block.get("data_content")
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        rows = json.loads(content)
    except json.JSONDecodeError:
        return None
    ok = isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows)
    return rows if ok else None


def transcript_rows(envelope: dict) -> list[dict] | None:
    """The `transaction` block's segment rows, or None when there is none or it isn't JSON."""
    for block in envelope.get("source_list") or []:
        if isinstance(block, dict) and block.get("data_type") == "transaction":
            return _rows_of(block)
    return None


def _ascii_slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")


def note_type_base(block: dict) -> str:
    return (_ascii_slug(str(block.get("data_tab_name") or ""))
            or _ascii_slug(str(block.get("data_type") or "")) or "note")


def note_blocks(envelope: dict) -> list[tuple[str, dict, dict]]:
    """Each note block as (note type, normalised block, raw block), in normalised order: the
    `outline`, then every `note_list` tab, Dan's own highlights and memos included. The note
    type is `plaud-<tab>` (§6.5); a second tab with the same name gets `-2`, then `-3`, and so
    on. Hash the normalised block. Render the raw one, whose links are whole."""
    outline = [b for b in envelope.get("source_list") or []
               if isinstance(b, dict) and b.get("data_type") == "outline"]
    tabs = [b for b in envelope.get("note_list") or [] if isinstance(b, dict)]
    out, seen = [], {}
    for blocks in (outline, tabs):
        pairs = sorted(((_normal_block(b), b) for b in blocks), key=lambda p: _block_key(p[0]))
        for block, raw in pairs:
            base = note_type_base(block)
            seen[base] = seen.get(base, 0) + 1
            note_type = f"plaud-{base}" if seen[base] == 1 else f"plaud-{base}-{seen[base]}"
            out.append((note_type, block, raw))
    return out


def diarization_fingerprint(envelope: dict) -> str | None:
    """Each segment's start and end, in Plaud's integer milliseconds, and its generic label: the
    same fingerprint means only names changed; a different one is a new diarization (§9.1.1)."""
    rows = transcript_rows(normalise(envelope))
    if rows is None:
        return None
    return sha256_of([[r.get("start_time"), r.get("end_time"),
                       r.get("original_speaker") or r.get("speaker") or ""] for r in rows])


def part_hashes(envelope: dict) -> dict[str, str]:
    """A hash per part (§9.1.1): title, transcript text, speaker names, each note tab, and
    everything else. The Sync screen (stage 2b) says what changed from these, and the circuit
    breaker counts only "other".

    Both segment blocks feed the transcript and speakers parts: `transaction`, and Plaud's
    `transaction_polish`, which has the same rows, names included. A speaker rename changes
    both blocks, so with polish under "other" one relabelling sweep would trip the breaker.
    Every field lands in a part. A segment row's text and timing go to "transcript", its names
    to "speakers", and any other row field to "other", with the rest of its block. A segment
    block that doesn't parse goes to "other" whole."""
    env = normalise(envelope)
    parts = {"title": sha256_of(env.get("name"))}
    sources = env.get("source_list")
    text, names, residue = [], [], []
    for block in sources if isinstance(sources, list) else []:
        rows = _rows_of(block)
        if rows is not None:
            kind = block["data_type"]
            text.append([kind, [[r.get(k) for k in _TEXT_KEYS] for r in rows]])
            names.append([kind, [[r.get(k) for k in _NAME_KEYS] for r in rows]])
            extra = [[i, {k: v for k, v in r.items() if k not in _ROW_KEYS}]
                     for i, r in enumerate(rows) if not _ROW_KEYS.issuperset(r)]
            residue.append([{k: v for k, v in block.items() if k != "data_content"}, extra])
        elif not (isinstance(block, dict) and block.get("data_type") == "outline"):
            residue.append(block)  # the outline is a note tab; anything else stays here whole
    if text:
        parts["transcript"] = sha256_of(text)
        parts["speakers"] = sha256_of(names)
    for note_type, block, _raw in note_blocks(env):
        parts[f"notes:{note_type}"] = sha256_of(block)
    rest = {k: v for k, v in env.items() if k != "name"}
    if isinstance(sources, list):
        rest["source_list"] = residue
    if isinstance(env.get("note_list"), list):
        rest["note_list"] = [b for b in env["note_list"] if not isinstance(b, dict)]
    parts["other"] = sha256_of(rest)
    return parts
