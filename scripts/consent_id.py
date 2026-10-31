"""The one Plaud ID in `make consent-add ID='<text>'` (stage 2b spec §12.1), run on the Mac.

    uv run python scripts/consent_id.py '<text>'

<text> is a bare ID, an `of_` ID in any case, or the Sync screen's Copy ID form (`` - `of_…` — ``,
the consent list's own line), read with the consent list's own exclusion rule
(recordings.plaud.keepout.parse_consent), so whatever the list would read as an exclusion is
accepted here and nothing else. It prints that line's canonical ID, the 32 hex digits in lower
case, which the list matches whatever form Plaud lists it in and docker/consent-add.sh accepts;
or it exits 1 with "not a Plaud ID". Text that holds a second run of 24 or more hex digits
(two Copy IDs pasted together, an ID in the note) is refused too, so no ID is silently dropped.
"""

from __future__ import annotations

import re
import sys

from recordings.models import visible
from recordings.plaud.keepout import parse_consent

# Plaud IDs are 32 hex digits; 24 or more in a row is one, or the start of one that is damaged.
_ID_LIKE = re.compile(r"[0-9a-fA-F]{24,}")


def consent_id(text: str) -> str | None:
    if "\n" in text or "\r" in text:
        return None  # one line, one ID
    # Two IDs pasted on one line: the line holds one, and the other would stay importable. Read
    # as the parser reads it, so a fullwidth digit or a zero-width space doesn't hide one.
    if len(_ID_LIKE.findall(visible(text))) > 1:
        return None
    parsed = parse_consent(text)
    lines = [line for line in parsed.lines if line.kind == "exclusion"]
    if len(lines) != 1 or parsed.typo_lines:
        return None
    return lines[0].canonical_id


def main(argv: list[str]) -> int:
    found = consent_id(argv[0]) if len(argv) == 1 else None
    if found is None:
        print("consent_id: not a Plaud ID", file=sys.stderr)
        return 1
    print(found)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
