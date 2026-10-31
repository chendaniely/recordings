"""The one Plaud ID in `make consent-add ID='<text>'` (stage 2b spec §12.1), run on the Mac.

    uv run python scripts/consent_id.py '<text>'

<text> is a bare ID, an `of_` ID in any case, or the Sync screen's Copy ID form (`` - `of_…` — ``,
the consent list's own line), read with the consent list's own exclusion rule
(recordings.plaud.keepout.parse_consent), so whatever the list would read as an exclusion is
accepted here and nothing else. It prints that line's canonical ID, the 32 hex digits in lower
case, which the list matches whatever form Plaud lists it in and docker/consent-add.sh accepts;
or it exits 1 with "not a Plaud ID".
"""

from __future__ import annotations

import sys

from recordings.plaud.keepout import parse_consent


def consent_id(text: str) -> str | None:
    if "\n" in text or "\r" in text:
        return None  # one line, one ID
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
