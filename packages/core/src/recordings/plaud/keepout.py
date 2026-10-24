"""Keeping recordings out (stage 2b spec §12.1, §12.2): the consent list's grammar and the
keep-out word. Pure functions over text, read by the worker, doctor, the CLI, the spike script and
the 2a import.

**The consent list.** Each line is read as a person sees it (`models.visible`: Unicode NFKC, so a
fullwidth ID or dash reads as the plain one, without format characters such as a zero-width space,
a word joiner or a byte order mark, or the other invisible code points), then is one of these,
tried in this order:
- **a comment:** `#` after optional spaces;
- **an exclusion:** after optional spaces, an optional bullet (`-` or `*`, then any spaces) and an
  optional backtick, a Plaud ID in any hex form `canonical_plaud_id` reads (32 hex digits, bare or
  with `of_`, in any case), then an optional backtick and any spaces, then a dash (`—`, `–` or `-`,
  with a note after it if Dan likes) or the end of the line. These are 2a's meanings
  (`consent.LEGACY_ENTRY_RE`), which read only bare lower-case IDs; 2b adds only the ID forms;
- **a typo:** a line whose first word (after the bullet and backtick) holds 24 or more hex digits,
  with or without `of_`, but no valid ID (31 or 33 hex digits, or a non-hex character among them),
  whatever follows it: a valid ID later on the line doesn't make it a kept line. Or (Dan's
  decision 12) a line whose text before the dash, or the whole line when it has no dash, is only
  hex digits once spaces, backticks and `of_` are set aside, 8 or more of them, and isn't one
  whole ID: an ID split by a space, or wrapped over two lines (each half is a typo). Exclusions
  and comments are classified first, so it never touches them; it does catch a line of two IDs,
  and an ID followed only by hex letters or digits before any dash (`` - `3fa9…` added ``,
  `` - `of_3fa9…` 2026-10-23 asked ``), which read as kept lines before it. Such a line reads as
  an exclusion once a dash follows the ID (`` - `of_3fa9…` — added ``). It fails closed (P8): the
  list stops;
- **a kept line:** any other line that holds an ID: one followed by other words, by a character
  that is neither hex nor a dash in the same word (`3fa9…:`), an ID in a sentence, or after a `+`
  or `1.` bullet. It names a recording Dan kept, so it is never an exclusion, and it is listed by
  line number for him to check;
- otherwise a blank line or text.

A missing, unreadable or empty list, one with no exclusions, or one with a typo is a **consent
stop**, named by a fixed phrase. Phrases carry line numbers, never a line's text. CRLF line
endings, a byte order mark anywhere, a non-breaking space and a fullwidth character read as a plain
file does, so a list saved by a Windows editor or a phone, or two lists joined, reads the same
(Review Focus 1). Only the classifying reads the visible text: what 2a's parser missed is found
on the raw text, with `consent.LEGACY_ENTRY_RE`, as 2a read it.

**The keep-out word.** A title matches when, folded (`models.fold`: NFKC, without format
characters or `HIDDEN_CODEPOINTS`, then casefolded) and with its leading symbols and Plaud's
"MM-DD " prefix skipped, its letters and digits spell the word, skipping spaces, `-`, `_` and `.`
between them, and the character after the word's last letter isn't a letter or digit (§12.2).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from recordings.models import fold, visible
from recordings.refs import canonical_plaud_id

WORD_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
DEFAULT_WORD = "noarchive"

STOP_MISSING = "the consent list is missing"
STOP_UNREADABLE = "the consent list can't be read"
STOP_EMPTY = "the consent list is empty"
STOP_NO_IDS = "the consent list holds no IDs"

LineKind = Literal["exclusion", "kept", "typo", "comment", "blank", "text"]

_LEAD = r"^\s*(?:[-*]\s*)?`?"
_EXCLUSION = re.compile(_LEAD + r"((?:of_)?[0-9a-f]{32})`?\s*(?:[—–-]|$)", re.IGNORECASE)
_ANY_ID = re.compile(r"(?<![0-9a-z])((?:of_)?[0-9a-f]{32})(?![0-9a-f])", re.IGNORECASE)
_FIRST_WORD = re.compile(_LEAD + r"([^\s`]*)")
_HEX = frozenset("0123456789abcdefABCDEF")
_TYPO_HEX = 24
# Dan's decision 12: the text before the dash, set aside spaces, backticks and `of_`.
_BULLET = re.compile(r"^\s*(?:[-*]\s*)?")
_DASH = re.compile(r"[—–-]")
_SET_ASIDE = re.compile(r"\s|`|of_", re.IGNORECASE)
_WHOLE_ID = re.compile(r"\s*`?(?:of_)?[0-9a-f]{32}`?\s*", re.IGNORECASE)
_SPLIT_HEX = 8
_SEPARATORS = frozenset(" -_.")
_DATE_PREFIX = re.compile(r"[0-9]{2}-[0-9]{2}\s")


@dataclass(frozen=True)
class ConsentLine:
    number: int  # 1-based
    kind: LineKind
    canonical_id: str | None  # exclusion and kept lines: the ID, canonical; otherwise None


@dataclass(frozen=True)
class ConsentList:
    ids: frozenset[str]  # the exclusions' canonical IDs (kept even on a stop: late listings run)
    lines: tuple[ConsentLine, ...]
    stop: str | None  # None, or the fixed phrase of a consent stop

    @property
    def readable(self) -> bool:
        return self.stop is None

    @property
    def typo_lines(self) -> tuple[int, ...]:
        return tuple(line.number for line in self.lines if line.kind == "typo")

    @property
    def review_lines(self) -> tuple[int, ...]:
        """The kept lines: every line holding an ID that isn't an exclusion."""
        return tuple(line.number for line in self.lines if line.kind == "kept")


@dataclass(frozen=True)
class FileSignature:
    inode: int
    size: int
    mtime_ns: int


def _and(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def typo_phrase(numbers: Sequence[int]) -> str:
    """The consent stop for typo lines, by line number only (§12.1, §15.6)."""
    lines = _and([str(n) for n in numbers])
    if len(numbers) == 1:
        return f"the consent list has 1 line that looks like an ID but can't be read (line {lines})"
    return (
        f"the consent list has {len(numbers)} lines that look like IDs but can't be read "
        f"(lines {lines})"
    )


def _is_typo(line: str) -> bool:
    """The line looks like an ID but isn't one (P8): its first word holds 24 or more hex digits,
    with or without `of_`, and no valid ID; or its text before the dash is a broken ID."""
    match = _FIRST_WORD.match(line)
    word = match.group(1) if match else ""
    if not _ANY_ID.search(word):
        bare = word[3:] if word[:3].lower() == "of_" else word
        if sum(c in _HEX for c in bare) >= _TYPO_HEX:
            return True
    # Dan's decision 12 ("Stop and tell me"): an ID split by a space, or wrapped over two lines,
    # would otherwise read as text and exclude nothing. The text before the dash (the whole line
    # when there is none), with spaces, backticks and `of_` set aside, is 8 or more hex digits and
    # nothing else, and isn't one whole ID. Exclusions and comments never reach here. By the same
    # words a line of two IDs, or an ID followed only by hex before any dash (`added`, a date like
    # 2026-10-23), stops the list too: it read as kept before, and a dash after the ID fixes it.
    before = _DASH.split(line[_BULLET.match(line).end() :], maxsplit=1)[0]
    digits = _SET_ASIDE.sub("", before)
    return len(digits) >= _SPLIT_HEX and set(digits) <= _HEX and not _WHOLE_ID.fullmatch(before)


def _classify(number: int, raw: str) -> ConsentLine:
    line = visible(raw)  # a zero-width space, a BOM or a fullwidth ID reads as a plain file does
    if not line.strip():
        return ConsentLine(number, "blank", None)
    if line.lstrip().startswith("#"):
        return ConsentLine(number, "comment", None)
    if match := _EXCLUSION.match(line):
        return ConsentLine(number, "exclusion", canonical_plaud_id(match.group(1)))
    if _is_typo(line):  # before a kept line: a typo stays a typo whatever follows it
        return ConsentLine(number, "typo", None)
    if match := _ANY_ID.search(line):
        return ConsentLine(number, "kept", canonical_plaud_id(match.group(1)))
    return ConsentLine(number, "text", None)


def parse_consent(text: str) -> ConsentList:
    lines = tuple(_classify(n, line) for n, line in enumerate(text.splitlines(), start=1))
    ids = frozenset(
        line.canonical_id for line in lines if line.kind == "exclusion" and line.canonical_id
    )
    typos = [line.number for line in lines if line.kind == "typo"]
    if not visible(text).strip():
        stop = STOP_EMPTY
    elif typos:
        stop = typo_phrase(typos)
    elif not ids:
        stop = STOP_NO_IDS
    else:
        stop = None
    return ConsentList(ids=ids, lines=lines, stop=stop)


def read_consent(path: Path | None) -> ConsentList:
    """Never raises: unset, missing, unreadable, empty, no exclusions or a typo is a stop."""
    if path is None:
        return ConsentList(ids=frozenset(), lines=(), stop=STOP_MISSING)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ConsentList(ids=frozenset(), lines=(), stop=STOP_MISSING)
    except (OSError, UnicodeDecodeError):
        return ConsentList(ids=frozenset(), lines=(), stop=STOP_UNREADABLE)
    return parse_consent(text)


def signature(path: Path | None) -> FileSignature | None:
    """The file's inode, size and time, to tell a changed or replaced file (§9.2); None when it
    can't be stat'ed."""
    if path is None:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return FileSignature(inode=st.st_ino, size=st.st_size, mtime_ns=st.st_mtime_ns)


def valid_word(word: object) -> bool:
    """1 to 32 letters, digits, `-` or `_`, at least one of them a letter or digit: a word of
    dashes and underscores alone would fold to nothing and never match (§12.2)."""
    return (
        isinstance(word, str)
        and WORD_RE.fullmatch(word) is not None
        and any(c.isalnum() for c in word)
    )


def _skip_symbols(text: str, i: int) -> int:
    while i < len(text) and not text[i].isalnum():
        i += 1
    return i


def _spells(text: str, target: str) -> bool:
    """§12.2's steps 2 and 3, on a folded title and a folded word of letters and digits."""
    i = _skip_symbols(text, 0)
    if _DATE_PREFIX.match(text, i):
        i = _skip_symbols(text, i + 6)
    matched = 0
    while i < len(text):
        c = text[i]
        if c.isalnum():
            if c != target[matched]:
                return False
            matched += 1
            if matched == len(target):
                return not (i + 1 < len(text) and text[i + 1].isalnum())
        elif c not in _SEPARATORS:
            return False
        i += 1
    return False


def keep_out_match(title: str | None, words: Iterable[str]) -> str | None:
    """The first word, as given, that the title starts with by §12.2's rule; None for no title."""
    if not title:
        return None
    folded = fold(title)
    for word in words:
        target = "".join(c for c in fold(word) if c.isalnum())
        if target and _spells(folded, target):
            return word
    return None
