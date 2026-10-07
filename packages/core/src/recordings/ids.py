"""Recording IDs: `20261006T140003-0700_3fa91c2e` (spec §6.2).

ISO 8601 *basic* local time with its UTC offset, then the first 8 hex of the media's
SHA-256. Basic format because the extended form's colons are rejected by SMB/Windows and
shown as "/" by Finder; mixing the two forms would not be valid ISO 8601. The offset stays
because Dan records in more than one time zone. An ID never changes once minted.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath

ID_RE = re.compile(r"(?P<stamp>\d{8}T\d{6}[+-]\d{4})_(?P<short>[0-9a-f]{8})")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def make_id(recorded_at: datetime, sha256_hex: str) -> str:
    offset = recorded_at.utcoffset()
    if offset is None:
        raise ValueError("recorded_at must be timezone-aware")
    if not _SHA256.fullmatch(sha256_hex):
        raise ValueError("sha256_hex must be 64 lowercase hex characters")
    if offset.total_seconds() % 60:
        raise ValueError(f"UTC offset {offset} has seconds; an ID carries ±HHMM only")
    return f"{recorded_at.strftime('%Y%m%dT%H%M%S%z')}_{sha256_hex[:8]}"


def parse_id(recording_id: str) -> tuple[datetime, str]:
    match = ID_RE.fullmatch(recording_id)
    if match is None:
        raise ValueError(f"not a recording id: {recording_id!r}")
    # Python 3.11+ fromisoformat() reads the basic format.
    return datetime.fromisoformat(match["stamp"]), match["short"]


def relative_dir(recording_id: str) -> PurePosixPath:
    """`recordings/YYYY/MM/<id>`, by the local date the ID itself carries."""
    stamp, _ = parse_id(recording_id)
    return PurePosixPath("recordings", f"{stamp:%Y}", f"{stamp:%m}", recording_id)
