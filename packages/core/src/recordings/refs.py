"""Source references compared in one canonical form (spec §6.3, §9.1.1).

References are stored exactly as the source returned them. Only comparisons canonicalise: Plaud's
`of_<32 hex>` and the bare 32 hex are one ID, whatever their case. Every other source's references
are opaque.
"""

from __future__ import annotations

import re

_PLAUD_HEX = re.compile(r"[0-9a-f]{32}")


def canonical_plaud_id(ref: str) -> str:
    value = ref.strip().lower()
    value = value.removeprefix("of_")
    return value if _PLAUD_HEX.fullmatch(value) else ref


def canonical_ref(kind: str, ref: str) -> str:
    return canonical_plaud_id(ref) if kind == "plaud" else ref
