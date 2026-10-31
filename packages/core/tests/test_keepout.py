"""The consent list's grammar and the keep-out word (stage 2b spec §12.1, §12.2). Every ID here
is synthetic."""

import pytest
from recordings import models
from recordings.plaud.keepout import (
    DEFAULT_WORD,
    STOP_EMPTY,
    STOP_MISSING,
    STOP_NO_IDS,
    STOP_UNREADABLE,
    fold,
    keep_out_match,
    parse_consent,
    read_consent,
    signature,
    typo_phrase,
    valid_word,
)

A = "3fa9" + "0" * 24 + "c0de"  # 32 hex, with letters, so case matters
B = "b" * 32
C = "c" * 32


def _kinds(text: str) -> list[str]:
    return [line.kind for line in parse_consent(text).lines]


def _wide(text: str) -> str:
    """The fullwidth form a phone keyboard can type: U+FF01 to U+FF5E for ASCII, U+3000 a space."""
    return "".join("\u3000" if c == " " else chr(ord(c) + 0xFEE0) for c in text)


@pytest.mark.parametrize(
    "line",
    [
        A,
        A.upper(),
        f"of_{A}",
        f"OF_{A.upper()}",
        f"`{A}`",
        f"- {A}",
        f"* `of_{A}`",
        f"- `{A}` — asked not to keep",
        f"- `of_{A}` – her request",
        f"{A} - a note",
        f"  -   `{A}`   —",
    ],
)
def test_an_id_alone_or_then_a_dash_is_an_exclusion_in_any_form(line):
    consent = parse_consent(f"# Never mirror\n{line}\n")
    assert consent.ids == {A}
    assert consent.lines[1].kind == "exclusion" and consent.lines[1].canonical_id == A
    assert consent.readable and consent.review_lines == ()


@pytest.mark.parametrize(
    "line", [f"{A} kept: she agreed later", f"- {A} (fine to keep)", f"{A}:", f"{A},"]
)
def test_an_id_followed_by_words_is_a_kept_line_and_listed(line):
    # why: C5.10. audio-router's rule names a recording Dan KEPT this way; reading it as an
    # exclusion would throw away a recording he chose to keep.
    consent = parse_consent(f"- `{B}` — excluded\n{line}\n")
    assert consent.ids == {B}
    assert consent.lines[1].kind == "kept" and consent.lines[1].canonical_id == A
    assert consent.review_lines == (2,)
    assert consent.readable


@pytest.mark.parametrize(
    "line",
    [f"We kept {A} on purpose.", f"+ {A}", f"1. {A}", f"Her call ({A.upper()}) stands."],
)
def test_an_id_in_a_sentence_or_after_plus_or_number_bullets_is_listed_not_excluded(line):
    consent = parse_consent(f"{B}\n{line}\n")
    assert consent.ids == {B}
    assert consent.review_lines == (2,)


@pytest.mark.parametrize(
    "bad",
    [
        A[:31],  # 31 hex
        A + "a",  # 33 hex
        A[:10] + "g" + A[11:],  # a non-hex character among them
        f"of_{A[:31]}",
        "- `" + A[:24] + "`",  # 24 hex is enough to look like an ID
        f"- `{A[:31]}` — see also {C}",  # a valid ID later on the line doesn't make it kept
    ],
)
def test_31_or_33_hex_or_a_non_hex_among_them_is_a_typo_that_stops_the_list(bad):
    # why: P8. A typo'd exclusion read as "not an exclusion" would download the recording it was
    # meant to keep out, so the whole list stops. The phrase names line numbers, never the text.
    text = f"# Never mirror\n{bad}\n- `{B}` —\n\n{bad} — again\n"
    consent = parse_consent(text)
    assert consent.typo_lines == (2, 5)
    assert consent.stop == typo_phrase([2, 5])
    assert consent.stop == (
        "the consent list has 2 lines that look like IDs but can't be read (lines 2 and 5)"
    )
    assert bad not in consent.stop
    assert not consent.readable
    assert consent.ids == {B}  # late consent listings still run for the lines that read (§12.1)


@pytest.mark.parametrize(
    "lines",
    [
        [f"- `{A[:16]} {A[16:]}` — asked"],  # an ID split by a space
        [f"- `{A[:20]}", f"{A[20:]}` — asked"],  # wrapped over two lines: each half is a typo
        [f"{A[:4]} {A[4:]}"],  # split after only 4 hex digits
        ["- 20261023 — asked"],  # 8 digits before the dash: it may be an ID's start
    ],
)
def test_hex_split_by_a_space_or_a_wrap_is_a_typo_that_stops_the_list(lines):
    # why: Dan's decision 12 ("Stop and tell me"). Read as text, a split ID excludes nothing and
    # the list still reads, so the recording it names would be downloaded.
    consent = parse_consent("# Never mirror\n" + "\n".join(lines) + f"\n- `{B}` —\n")
    numbers = tuple(range(2, 2 + len(lines)))
    assert consent.typo_lines == numbers
    assert consent.stop == typo_phrase(numbers) and not consent.readable
    assert consent.ids == {B}  # late consent listings still run for the lines that read
    assert A[:16] not in consent.stop and "20261023" not in consent.stop


@pytest.mark.parametrize(
    "line", [f"- `{A}` `{B}`", f"- `{A}` added", f"- `of_{A}` 2026-10-23 asked"]
)
def test_an_id_followed_only_by_hex_before_any_dash_is_a_kept_line(line):
    # why: Dan's decision 12, refined 2026-10-30 ("Solid chunks only"): only an unbroken run of 8
    # or more hex characters that isn't one whole ID makes a typo. These have none, so they read
    # as kept lines, listed for Dan. A dash after the ID makes it an exclusion.
    consent = parse_consent(f"# Never mirror\n{line}\n- `{C}` —\n")
    assert consent.lines[1].kind == "kept" and consent.review_lines == (2,)
    assert consent.readable and consent.ids == {C}
    fixed = parse_consent(line.replace("` ", "` — ", 1) + "\n")
    assert fixed.readable and fixed.ids == {A}


@pytest.mark.parametrize(
    "note", ["- Added 2026-10-23", "A bad decade", "Added 2026-10-23: two more", "- bed cafe"]
)
def test_a_note_made_of_short_hex_words_is_plain_text(note):
    # why: Dan's decision 12, refined 2026-10-30. No run of 8 hex characters, so no stop.
    consent = parse_consent(f"# Never mirror\n{note}\n- `{B}` —\n")
    assert consent.lines[1].kind == "text" and consent.readable


@pytest.mark.parametrize(
    "word",
    [
        f"of-{A}",
        f"0f_{A}",
        f"({A})",
        f"'{A}'",
        f'"{A}"',
        f"[{A}]",
        f"<{A}>",
        f"<<{A}>>",
    ],
)
def test_an_id_that_does_not_start_the_first_word_is_a_typo(word):
    # why: spec §12.1 rule 4. The ID must start the first word (after the bullet, backtick and
    # of_); one that doesn't is a mistyped exclusion, not a kept line.
    consent = parse_consent(f"- `{B}` —\n- {word} — asked\n")
    assert consent.typo_lines == (2,) and consent.stop == typo_phrase([2])


def test_lines_are_numbered_as_an_editor_does_and_other_separators_are_typos():
    # why: a stop names the editor's line number, which splits only on \r\n, \r and \n. A line
    # holding any other separator fails closed (P8): `<id>` U+2028 `kept: …` would read as an
    # exclusion once split.
    consent = parse_consent(f"- `{B}` —\n{A}\u2028kept: fine\n")
    assert consent.typo_lines == (2,) and consent.stop == typo_phrase([2])
    consent = parse_consent(f"{B}\n# note\x0cmore\n{A[:31]}\n")
    assert consent.typo_lines == (2, 3)
    for sep in "\v\f\x1c\x1d\x1e\x85\u2028\u2029":
        assert parse_consent(f"{B}\nx{sep}y\n").typo_lines == (2,)
    assert parse_consent(f"{B}\r\n{C}\r{A}\n").ids == {A, B, C}


def test_a_path_holding_nul_is_a_stop_not_an_error():
    from pathlib import Path

    assert read_consent(Path("a\0b")).stop == STOP_UNREADABLE
    assert signature(Path("a\0b")) is None


def test_typo_phrase_of_no_lines_does_not_raise():
    assert "0 lines" in typo_phrase([])


def test_an_exclusion_a_kept_line_a_comment_and_a_note_read_as_before():
    # why: Dan's decision 12 widens the typo rule only: these lines read as they always did.
    text = (
        f"# Never mirror: {A[:8]} {A[8:16]}\n- `{A}` — asked\n{C} kept: she agreed later\n"
        "- asked in person\n"
    )
    assert _kinds(text) == ["comment", "exclusion", "kept", "text"]
    consent = parse_consent(text)
    assert consent.readable and consent.ids == {A} and consent.review_lines == (3,)


def test_typo_phrase_counts_one_line_and_lists_three():
    assert typo_phrase([14]) == (
        "the consent list has 1 line that looks like an ID but can't be read (line 14)"
    )
    assert typo_phrase([3, 14, 22]).endswith("(lines 3, 14 and 22)")


def test_comments_and_blank_lines_are_ignored():
    text = f"# Never mirror\n\n   \n  # - `{A}` is a comment\n#{C}\n- `{B}` —\nPlain words.\n"
    consent = parse_consent(text)
    assert consent.ids == {B}
    assert _kinds(text) == ["comment", "blank", "blank", "comment", "comment", "exclusion", "text"]
    assert consent.review_lines == () and consent.readable


def test_missing_unreadable_empty_or_idless_lists_stop(tmp_path):
    # why: §12.1. A list that can't say what to exclude never means "exclude nothing".
    assert read_consent(None).stop == STOP_MISSING
    assert read_consent(tmp_path / "absent.md").stop == STOP_MISSING
    folder = tmp_path / "a-folder.md"
    folder.mkdir()
    assert read_consent(folder).stop == STOP_UNREADABLE
    latin = tmp_path / "latin.md"
    latin.write_bytes(b"caf\xe9 " + B.encode() + b"\n")
    assert read_consent(latin).stop == STOP_UNREADABLE
    empty = tmp_path / "empty.md"
    empty.write_text("  \n\n", encoding="utf-8")
    assert read_consent(empty).stop == STOP_EMPTY
    idless = tmp_path / "idless.md"
    idless.write_text(f"# Never mirror\nWe kept {A} on purpose.\n", encoding="utf-8")
    assert read_consent(idless).stop == STOP_NO_IDS
    good = tmp_path / "good.md"
    good.write_text(f"- `{A}` —\n", encoding="utf-8")
    assert read_consent(good).ids == {A} and read_consent(good).readable


def test_crlf_bom_and_nbsp_lines_read_as_plain_ones(tmp_path):
    # why: Review Focus 1. A Windows editor, a phone or a paste adds CRLF endings, a byte order
    # mark or non-breaking spaces; a BOM must not turn line 1's ID into a typo or a kept line.
    plain = f"{A}\n- `{B}` — asked\n{C} kept: fine\n"
    odd = "\ufeff" + f"{A}\r\n- `{B}`\u00a0—\u00a0asked\r\n{C} kept: fine\r\n"
    path = tmp_path / "consent-list.md"
    path.write_bytes(odd.encode("utf-8"))
    for consent in (parse_consent(odd), read_consent(path)):
        assert consent.ids == parse_consent(plain).ids == {A, B}
        assert consent.typo_lines == () and consent.review_lines == (3,)
        assert consent.readable


@pytest.mark.parametrize(
    "line",
    [
        f"\u200b- `{A}` — asked",  # a zero-width space before the bullet
        f"- `{A}`\u200b — asked",  # one between the ID and the dash
        f"- \u2060`{A}` —",  # a word joiner before the ID
        f"\ufeff- `{A}` —",  # a byte order mark mid-file, from two lists joined
        f"- `{A[:16]}\u200b{A[16:]}` —",  # a zero-width space inside the ID
        _wide(f"- of_{A}"),  # fullwidth, bullet and all
        f"- `{_wide(A.upper())}` — her request",
    ],
)
def test_invisible_and_fullwidth_characters_read_as_a_plain_file_does(line):
    # why: S1, Review Focus 1. Read as raw text, each of these made an exclusion a kept line or
    # plain text, and the list still read: the recording it names would have been downloaded.
    consent = parse_consent(f"# Never mirror\n{line}\n")
    assert consent.lines[1].kind == "exclusion" and consent.lines[1].canonical_id == A
    assert consent.ids == {A} and consent.readable and consent.review_lines == ()


def test_signature_tells_a_replaced_file_and_none_for_a_missing_one(tmp_path):
    path = tmp_path / "consent-list.md"
    path.write_text(f"{A}\n", encoding="utf-8")
    before = signature(path)
    replacement = tmp_path / "new.md"
    replacement.write_text(f"{A}\n", encoding="utf-8")
    replacement.replace(path)  # what an editor, `sed -i` or `make consent-add` does
    assert signature(path) is not None and signature(path) != before
    assert signature(tmp_path / "absent.md") is None and signature(None) is None


@pytest.mark.parametrize(
    ("title", "matches"),
    [
        ("NoArchive: coffee", True),
        ("No archive", True),
        ("no-archive", True),
        ("[noarchive]", True),
        ("🔒 noarchive", True),
        ("10-21 noarchive", True),
        ("\u200bnoarchive", True),
        ("noarchived", False),
        ("No archives", False),
    ],
)
def test_the_keep_out_word_matches_the_spec_examples(title, matches):
    # why: §12.2's own examples, both ways: forgiving about case, spaces, dashes, symbols and
    # Plaud's date prefix, but the whole word must come first.
    assert keep_out_match(title, [DEFAULT_WORD]) == (DEFAULT_WORD if matches else None)


@pytest.mark.parametrize("title", ["No.Archive_test", "  -- NOARCHIVE"])
def test_the_keep_out_word_skips_dots_underscores_and_leading_symbols(title):
    assert keep_out_match(title, [DEFAULT_WORD]) == DEFAULT_WORD


@pytest.mark.parametrize("title", ["Coffee noarchive", "noarchive2", "no archive7", "", None])
def test_the_keep_out_word_needs_the_whole_word_first(title):
    assert keep_out_match(title, [DEFAULT_WORD]) is None


def test_fold_drops_format_and_hidden_characters_and_casefolds():
    # why: M9. The keep-out word folds exactly as is_private_tag folds a tag: one rule, in models.
    assert fold is models.fold
    assert fold("ｐｒｉｖａｔｅ") == "private"
    assert fold("No\u200bArch\u3164ive") == "noarchive"
    assert keep_out_match("ｎｏａｒｃｈｉｖｅ: synthetic", [DEFAULT_WORD]) == DEFAULT_WORD


def test_an_earlier_word_still_matches():
    # why: §12.2. Changing the word never releases what an older word kept out.
    words = ("noarchive", "skipme")
    assert keep_out_match("skipme: Week 4", words) == "skipme"
    assert keep_out_match("NoArchive Week 4", words) == "noarchive"
    assert keep_out_match("Week 4", words) is None


@pytest.mark.parametrize(
    ("word", "ok"),
    [
        ("noarchive", True),
        ("skip_me-2", True),
        ("x" * 32, True),
        ("x" * 33, False),
        ("", False),
        ("no archive", False),
        ("-", False),  # S5: no letter or digit, so it would fold to nothing and never match
        ("__", False),
        ("-_-", False),
        ("_x", True),
        ("noärchive", False),
        (None, False),
        (7, False),
    ],
)
def test_valid_word_is_1_to_32_letters_digits_dash_or_underscore(word, ok):
    assert valid_word(word) is ok
