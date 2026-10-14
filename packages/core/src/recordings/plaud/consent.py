"""Dan's consent list, "never mirror" (spec §9.1): Plaud IDs that never enter the archive.

The parser is audio-router's (archive/gate.py at 64612df, itself ported from the vault's sync): an
ID counts only when it opens a bullet or stands alone on a line, because the list names a recording
Dan KEPT in a sentence, and a looser parser would exclude that one. Lines starting with `#` are
skipped. It is read fail-closed: a missing, unreadable or empty list, or one with no IDs, is an
error, never "exclude nothing". The import (stage 2a) and the sync (stage 2b) both read it here.
"""

from __future__ import annotations

import re
from pathlib import Path

from recordings.refs import canonical_plaud_id

ENTRY_RE = re.compile(r"^\s*(?:[-*]\s*)?`?([0-9a-f]{32})`?\s*(?:[—–-]|$)")


class ConsentListError(RuntimeError):
    """The consent list can't be trusted, so nothing that depends on it may run."""


def consent_list_path(data: dict) -> Path:
    """`[plaud] consent_list` from config.toml. Unset is an error: there is no default list."""
    value = (data.get("plaud") or {}).get("consent_list")
    if not isinstance(value, str) or not value.strip():
        raise ConsentListError(
            "[plaud] consent_list is not set in config.toml. It names Dan's consent list, and "
            "the import refuses to run without it (spec §9.1).")
    return Path(value).expanduser()


def load_consent_list(path: Path) -> frozenset[str]:
    """The IDs on the list, in canonical form. Raises ConsentListError rather than guess."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConsentListError(
            f"the consent list {path} is missing: refusing, because a missing list would "
            "exclude nothing") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConsentListError(
            f"the consent list {path} can't be read ({type(exc).__name__}): refusing") from None
    if not text.strip():
        raise ConsentListError(
            f"the consent list {path} is empty (a placeholder, or a truncated copy?): refusing")
    ids = frozenset(canonical_plaud_id(m.group(1)) for line in text.splitlines()
                    if not line.lstrip().startswith("#")
                    for m in [ENTRY_RE.match(line)] if m)
    if not ids:
        raise ConsentListError(
            f"the consent list {path} holds no IDs, so it is the wrong file: refusing")
    return ids
