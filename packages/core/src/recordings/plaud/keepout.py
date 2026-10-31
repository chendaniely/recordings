"""Keeping recordings out (stage 2b spec §12.1, §12.2): the consent list's grammar and the
keep-out word. Read by the worker, doctor, the CLI, the spike script and the 2a import. Pure
functions over text, except `read_consent` and `signature`, which read the file (and never raise).

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
  decision 12, refined 2026-10-30, "Solid chunks only") a line whose text before the dash, or the
  whole line when it has no dash, is only hex digits once spaces, backticks and `of_` are set
  aside, and holds one unbroken run of 8 or more of them (no space inside) that isn't one whole
  ID: an ID split by a space, or wrapped over two lines (each half is a typo). A note such as
  `- Added 2026-10-23` has no such run and is plain text; a line of two whole IDs, or an ID then
  `added`, is a kept line; one whole ID alone in a form the exclusion rule doesn't read
  (`` - `ID ` — asked ``, doubled backticks, `of_ ID`) is a typo.
  Exclusions and comments are classified first, so it never touches them. A line that still
  holds a line or paragraph separator other than `\r` and `\n` (`\v`, `\f`, `\x1c`-`\x1e`,
  `\x85`, U+2028, U+2029) is a typo too: lines are numbered as an editor does, and such a line
  would otherwise be read as two. A typo fails closed (P8): the list stops;
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

_ID = r"(?:of_)?[0-9a-f]{32}"
_BULLET_PATTERN = r"^\s*(?:[-*]\s*)?"
_LEAD = _BULLET_PATTERN + "`?"
_EXCLUSION = re.compile(_LEAD + f"({_ID})" + r"`?\s*(?:[—–-]|$)", re.IGNORECASE)
_ANY_ID = re.compile(f"(?<![0-9a-z])({_ID})(?![0-9a-f])", re.IGNORECASE)
_FIRST_WORD = re.compile(_LEAD + r"([^\s`]*)")
_HEX = frozenset("0123456789abcdefABCDEF")
_TYPO_HEX = 24
# Dan's decision 12, refined: the text before the dash, set aside spaces, backticks and `of_`.
_BULLET = re.compile(_BULLET_PATTERN)
_DASH = re.compile(r"[—–-]")
_SET_ASIDE = re.compile(r"\s|`|of_", re.IGNORECASE)
_CHUNKS = re.compile(r"[\s`]+")
_OF = re.compile(r"^of_", re.IGNORECASE)
_ID_LENGTH = 32
_SPLIT_HEX = 8
# A line or paragraph separator `str.splitlines` would split on, apart from \r and \n.
_SEPARATORS_OTHER = frozenset("\v\f\x1c\x1d\x1e\x85\u2028\u2029")
_LINE_END = re.compile(r"\r\n|\r|\n")
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


def _join_and(items: Sequence[str]) -> str:
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def kept_lines_note(numbers: Sequence[int]) -> str:
    """The lines read as kept, not as exclusions, by line number: `plaud consent` and doctor say
    it in these words."""
    listed = ", ".join(str(n) for n in numbers)
    return f"lines read as kept, not excluded (check each meant keep): {listed}"


def typo_phrase(numbers: Sequence[int]) -> str:
    """The consent stop for typo lines, by line number only (§12.1, §15.6)."""
    lines = _join_and([str(n) for n in numbers])
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
    if not _ANY_ID.match(word):  # the ID must start the word: `(<id>)` and `of-<id>` are typos
        bare = word[3:] if word[:3].lower() == "of_" else word
        if sum(c in _HEX for c in bare) >= _TYPO_HEX:
            return True
    # Dan's decision 12, refined 2026-10-30 ("Solid chunks only"): an ID split by a space, or
    # wrapped over two lines, would otherwise read as text and exclude nothing. The text before
    # the dash (the whole line when there is none), with spaces, backticks and `of_` set aside, is
    # hex digits and nothing else, and holds one unbroken run (no space inside) of 8 or more that
    # isn't one whole ID. Exclusions and comments never reach here. A note like `Added 2026-10-23`
    # has no such run, and a line of two whole IDs, or an ID then `added`, is a kept line. One whole
    # ID alone, unread as an exclusion, is a typo.
    before = _DASH.split(line[_BULLET.match(line).end() :], maxsplit=1)[0]
    if not set(_SET_ASIDE.sub("", before)) <= _HEX:
        return False
    runs = [run for chunk in _CHUNKS.split(before) if (run := _OF.sub("", chunk))]
    if any(len(run) >= _SPLIT_HEX and len(run) != _ID_LENGTH for run in runs):
        return True
    # One whole ID and nothing else, in a form the exclusion rule didn't read (`ID `, doubled
    # backticks, `of_ ID`): it looks like an ID but can't be read, so the list stops (Part I).
    return len(runs) == 1 and len(runs[0]) == _ID_LENGTH


def _classify(number: int, raw: str) -> ConsentLine:
    line = visible(raw)  # a zero-width space, a BOM or a fullwidth ID reads as a plain file does
    if _SEPARATORS_OTHER.intersection(line):  # an editor shows one line here: fail closed
        return ConsentLine(number, "typo", None)
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
    raw = _LINE_END.split(text)
    if raw[-1] == "":  # the file's final line ending
        raw.pop()
    lines = tuple(_classify(n, line) for n, line in enumerate(raw, start=1))
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


def _stopped(stop: str) -> ConsentList:
    return ConsentList(ids=frozenset(), lines=(), stop=stop)


def read_consent(path: Path | None) -> ConsentList:
    """Never raises: unset, missing, unreadable, empty, no exclusions or a typo is a stop."""
    return read_consent_text(path)[0]


def read_consent_text(path: Path | None) -> tuple[ConsentList, str]:
    """The same, with the text it read ("" when it read none): one read, so what is parsed and
    what is looked at again (2a's parser, for `plaud consent`) are the same bytes."""
    if path is None:
        return _stopped(STOP_MISSING), ""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return _stopped(STOP_MISSING), ""
    except (OSError, ValueError):  # ValueError: a path holding NUL; UnicodeDecodeError is one too
        return _stopped(STOP_UNREADABLE), ""
    return parse_consent(text), text


def signature(path: Path | None) -> FileSignature | None:
    """The file's inode, size and time, to tell a changed or replaced file (§9.2); None when it
    can't be stat'ed."""
    if path is None:
        return None
    try:
        st = os.stat(path)
    except (OSError, ValueError):
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
    if date := _DATE_PREFIX.match(text, i):
        i = _skip_symbols(text, date.end())
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
