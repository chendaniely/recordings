"""Dan's consent list, "never mirror" (spec §9.1): Plaud IDs that never enter the archive.

The grammar moved to `keepout.parse_consent` in stage 2b (its spec §12.1). The meanings are still
audio-router's (archive/gate.py at 64612df, itself ported from the vault's sync): an ID counts only
when it opens a bullet or stands alone on a line, then a dash or the end, because the list names a
recording Dan KEPT in a sentence, and a looser parser would exclude that one. Lines starting with
`#` are skipped. 2b adds the `of_` and upper-case forms, lists the lines read as kept, and fails
closed on a line that looks like an ID but isn't one. It is read fail-closed: a missing,
unreadable or empty list, one with no IDs, or one with a typo line is an error, never "exclude
nothing". The import (stage 2a) and the sync (stage 2b) both read it through here or `keepout`.

`LEGACY_ENTRY_RE` is 2a's entry rule, unchanged. It decides nothing any more: it only names the
exclusions 2a's parser missed (an `of_` or upper-case ID), whose recordings 2a may have imported
(`recordings plaud consent`, 2b spec §12.1).
"""

from __future__ import annotations

import re
from pathlib import Path

from recordings.plaud.keepout import read_consent

LEGACY_ENTRY_RE = re.compile(r"^\s*(?:[-*]\s*)?`?([0-9a-f]{32})`?\s*(?:[—–-]|$)")


class ConsentListError(RuntimeError):
    """The consent list can't be trusted, so nothing that depends on it may run."""


def consent_list_path(data: dict) -> Path:
    """`[plaud] consent_list` from config.toml. Unset is an error: there is no default list."""
    value = (data.get("plaud") or {}).get("consent_list")
    if not isinstance(value, str) or not value.strip():
        raise ConsentListError(
            "[plaud] consent_list is not set in config.toml. It names Dan's consent list, and "
            "the import refuses to run without it (spec §9.1)."
        )
    return Path(value).expanduser()


def load_consent_list(path: Path) -> frozenset[str]:
    """The exclusions' IDs, in canonical form. Raises ConsentListError, with the consent stop's
    fixed phrase (line numbers, never a line's text or the path), rather than guess."""
    consent = read_consent(Path(path))
    if consent.stop is not None:
        raise ConsentListError(consent.stop)
    return consent.ids
