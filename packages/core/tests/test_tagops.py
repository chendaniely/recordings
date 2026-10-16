"""The tag operations (tagging spec §4): pure, with the privacy gate inside them."""

import subprocess
import sys
from datetime import datetime

import pytest
from pydantic import ValidationError
from recordings.models import Recording, TagRef, is_private_tag
from recordings.tagops import (
    CASE_VARIANT,
    EMPTY_PART,
    INVISIBLE,
    LOOKS_PRIVATE,
    NOT_NFC,
    NOTE_TYPE,
    PATH_LIKE,
    SPACE,
    TOO_LONG,
    TOO_MANY_MARKS,
    AddTags,
    Effect,
    LostTag,
    Recorded,
    RemoveTags,
    RestoreTags,
    WouldExpose,
    effect_of,
    is_private_lookalike,
    removes_last_private,
    validate_new_tag,
)

RID = "20261006T140003-0700_3fa91c2e"
SHA = "3fa91c2e" + "0" * 56


def rec(*tags: str | tuple[str, str], excluded: tuple[str, ...] = ()) -> Recording:
    """A recording with these tags: "name" is `by: you`, ("name", "auto") is `by: auto`."""
    refs = [
        TagRef(tag=t, by="you") if isinstance(t, str) else TagRef(tag=t[0], by=t[1]) for t in tags
    ]
    return Recording(
        id=RID,
        title="Week 4",
        recorded_at=datetime.fromisoformat("2026-10-06T14:00:03-07:00"),
        timezone="America/Vancouver",
        time_source="plaud",
        media={"file": f"{RID}.mp3", "sha256": SHA, "kind": "audio"},
        tags=refs,
        excluded_note_types=list(excluded),
    )


def names(r: Recording) -> list[str]:
    return [t.tag for t in r.tags]


def you(*tags: str) -> tuple[TagRef, ...]:
    return tuple(TagRef(tag=t, by="you") for t in tags)


# ---- AddTags ---------------------------------------------------------------------------------
def test_add_appends_only_the_missing_tags_and_keeps_an_existing_ones_by():
    out = AddTags(you("talks", "work", "work")).apply(rec(("talks", "auto"), "school"))
    assert names(out) == ["talks", "school", "work"]
    assert out.tags[0].by == "auto"  # already there: left as it is (spec §4)


def test_adding_nothing_new_returns_an_equal_recording_so_mutate_writes_nothing():
    before = rec("talks")
    assert AddTags(you("talks")).apply(before) == before


def test_adding_a_note_type_yourself_clears_its_exclusion_but_an_auto_one_does_not():
    # why: §7.3 "your choice wins" both ways; tagging plan, decision 1.
    excluded = rec("talks", excluded=("lecture", "summary"))
    assert AddTags(you("notes/lecture")).apply(excluded).excluded_note_types == ["summary"]
    auto = AddTags((TagRef(tag="notes/lecture", by="auto"),)).apply(excluded)
    assert auto.excluded_note_types == ["lecture", "summary"]


# ---- RemoveTags ------------------------------------------------------------------------------
def test_remove_drops_the_named_tags_and_skips_ones_the_recording_lacks():
    out = RemoveTags(("talks", "absent")).apply(rec("talks", "school"))
    assert names(out) == ["school"]


def test_removing_a_note_type_records_the_exclusion_whoever_added_it():
    out = RemoveTags(("notes/lecture", "notes/summary")).apply(
        rec(("notes/lecture", "auto"), "notes/summary", "talks", excluded=("summary",))
    )
    assert names(out) == ["talks"]
    assert out.excluded_note_types == ["summary", "lecture"]


def test_a_removal_accepts_an_existing_odd_spelling():
    # why: spec §4, "Removal accepts any existing spelling". New tags with it are refused.
    odd = "talks\u200b"
    assert names(RemoveTags((odd,)).apply(rec(odd, "school"))) == ["school"]


@pytest.mark.parametrize(
    "spelling",
    [
        "private",
        "Private/health",
        "PRIVATE",
        "\uff50\uff52\uff49\uff56\uff41\uff54\uff45",
        "pri\u200bvate",
    ],
)
def test_removing_the_last_private_tag_is_refused_without_may_expose(spelling):
    before = rec(spelling, "talks")
    with pytest.raises(WouldExpose) as caught:
        RemoveTags((spelling,)).apply(before)
    assert caught.value.recording_id == RID
    assert names(RemoveTags((spelling,), frozenset({RID})).apply(before)) == ["talks"]


def test_clearing_every_tag_of_a_private_recording_is_refused():
    with pytest.raises(WouldExpose):
        RemoveTags(("private", "talks")).apply(rec("private", "talks"))


def test_removing_one_of_two_private_tags_needs_no_confirmation():
    out = RemoveTags(("private",)).apply(rec("private/journal", "private"))
    assert names(out) == ["private/journal"]


def test_the_gate_decides_on_the_recording_it_is_given():
    # why: the gate runs under the lock, on the state written (spec §4). A recording that wasn't
    # private when the browser looked, and is now, is refused.
    with pytest.raises(WouldExpose):
        RemoveTags(("private",), frozenset({"20261007T140003-0700_bbbbbbbb"})).apply(rec("private"))


# ---- effects, Recorded and RestoreTags -------------------------------------------------------
def test_an_effect_lists_gains_losses_with_index_and_by_and_exclusions_both_ways():
    before = rec("a", ("notes/lecture", "auto"), "b", "c", excluded=("summary",))
    after = RemoveTags(("notes/lecture", "c")).apply(
        AddTags(you("notes/summary", "d")).apply(before)
    )
    effect = effect_of(before, after)
    assert effect == Effect(
        recording_id=RID,
        gained=("notes/summary", "d"),
        lost=(LostTag("notes/lecture", "auto", 1), LostTag("c", "you", 3)),
        excluded_added=("lecture",),
        excluded_removed=("summary",),
    )
    assert not effect.empty and effect_of(before, before).empty


def test_recorded_keeps_the_recording_apply_was_given_and_names_the_request():
    op = Recorded(AddTags(you("work")), "req0001abcd")
    before = rec("talks")
    after = op.apply(before)
    assert op.name == "add-tags:req0001abcd"  # revisions.origin (spec §5, "The record")
    assert op.before is before and op.after is after


def test_restore_puts_lost_tags_back_at_their_index_with_their_by_and_drops_gained_ones():
    before = rec("a", ("b", "auto"), "c", "d", excluded=("summary",))
    after = AddTags(you("e", "notes/summary")).apply(RemoveTags(("b", "d")).apply(before))
    restored = RestoreTags(effect_of(before, after)).apply(after)
    assert restored.tags == before.tags
    assert restored.excluded_note_types == before.excluded_note_types


def test_undo_leaves_a_tag_another_writer_added_alone():
    # why: spec §4. The effect is taken from what apply was given, so a tag that landed between
    # the browser's view and the write is never in it.
    op = Recorded(AddTags(you("work")), "req0001abcd")
    op.apply(rec("talks", "school"))  # "school" came from another window, before this write
    effect = effect_of(op.before, op.after)
    assert effect.gained == ("work",)
    later = AddTags(you("later")).apply(op.after)
    assert names(RestoreTags(effect).apply(later)) == ["talks", "school", "later"]


def test_an_undo_that_takes_private_away_needs_confirmation():
    before = rec()
    after = AddTags(you("private")).apply(before)
    effect = effect_of(before, after)
    with pytest.raises(WouldExpose):
        RestoreTags(effect).apply(after)
    assert names(RestoreTags(effect, frozenset({RID})).apply(after)) == []


def test_removes_last_private():
    assert removes_last_private(rec("private", "talks"), rec("talks"))
    assert removes_last_private(rec("Private/health"), rec())
    assert not removes_last_private(rec("private/a", "private/b"), rec("private/b"))
    assert not removes_last_private(rec("talks"), rec())


# ---- names (spec §4) -------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("tag", "message"),
    [
        ("pri\u200bvate", INVISIBLE),  # zero-width space
        ("\u202eprivate", INVISIBLE),  # right-to-left override
        (
            "\uff50\uff52\uff49\uff56\uff41\uff54\uff45",
            LOOKS_PRIVATE,
        ),  # fullwidth (tagging plan, decision 2)
        ("\u0440rivate", LOOKS_PRIVATE),  # Cyrillic er
        ("priv\u03b1te/x", LOOKS_PRIVATE),  # Greek alpha
        ("pr\u0130vate", LOOKS_PRIVATE),  # capital I with a dot
        ("pr\u00edvate", LOOKS_PRIVATE),  # an accent
        ("..", PATH_LIKE),
        ("school/../x", PATH_LIKE),
        ("a\\b", PATH_LIKE),
        ("a\x00b", INVISIBLE),
        ("a\nb", INVISIBLE),
        ("a\u2028b", INVISIBLE),  # line separator
        ("x" * 65, TOO_LONG),
        ("/".join(["x" * 60] * 4), TOO_LONG),  # 243 characters
        ("cafe\u0301", NOT_NFC),
        ("a//b", EMPTY_PART),
        (" a", EMPTY_PART),
        ("notes/unknown", NOTE_TYPE),  # not a type the archive has
        ("notes/Lecture", NOTE_TYPE),
        ("notes/lecture/x", NOTE_TYPE),
        ("Notes/lecture", NOTE_TYPE),
        ("notes", NOTE_TYPE),
    ],
)
def test_a_new_tag_that_breaks_a_rule_is_refused(tag, message):
    assert validate_new_tag(tag, note_types={"lecture"}) == message


@pytest.mark.parametrize(
    "tag",
    [
        "school/course-101",
        "private",
        "Private/health",
        "caf\u00e9",
        "notes/lecture",
        "日本語/メモ",
        "my talks",
    ],
)
def test_a_new_tag_that_meets_the_rules_is_allowed(tag):
    assert validate_new_tag(tag, note_types={"lecture"}) is None


@pytest.mark.parametrize("tag", ["private ", " private", "private\u00a0", "\u00a0private"])
def test_a_padded_first_part_is_refused_before_it_can_become_a_tag(tag):
    # why: the controller's ruling on Task 1. Such a tag would not fold to `private`, so it must
    # never exist: validate_new_tag refuses it, and TagRef itself refuses a space-padded part
    # (U+00A0 counts as padding for str.strip).
    assert validate_new_tag(tag) == EMPTY_PART
    with pytest.raises(ValidationError):
        TagRef(tag=tag)


def test_a_case_variant_of_an_existing_tag_is_refused():
    assert validate_new_tag("Talks", existing={"talks"}) == CASE_VARIANT
    assert validate_new_tag("talks", existing={"talks"}) is None  # the same tag, not a variant


@pytest.mark.parametrize(
    ("tag", "message"),
    [
        ("zebra\u200bsecret", INVISIBLE),
        ("zebrace\u0301", NOT_NFC),
        ("zebra//secret", EMPTY_PART),
        ("zebra" * 20, TOO_LONG),
        ("zebrax" + "\u0301" * 4, TOO_MANY_MARKS),
        ("zebra\u00a0secret", SPACE),
        ("zebra\u0338", INVISIBLE),
        ("zebra/../secret", PATH_LIKE),
        ("notes/zebra", NOTE_TYPE),
        ("\u0440rivate/zebra", LOOKS_PRIVATE),
        ("Zebra", CASE_VARIANT),
    ],
)
def test_a_refusal_never_repeats_the_tag(tag, message):
    # why: a refusal is shown to Dan, but it is also the kind of text that ends up in a report or
    # a log. Each tag holds "zebra", which no message does, so any interpolation of it shows.
    refusal = validate_new_tag(tag, note_types={"lecture"}, existing={"zebra"})
    assert refusal == message
    assert "zebra" not in refusal.casefold()


def test_the_lookalike_message_names_only_private():
    assert LOOKS_PRIVATE == "Did you mean `private`?"


def test_tagops_imports_with_sqlite3_and_fcntl_blocked():
    # why: the Pages demo runs these operations on Pyodide, which has neither (stage-2a
    # decision 6). A module-level import of either would break tagging there.
    code = (
        "import sys; sys.modules['sqlite3'] = None; sys.modules['fcntl'] = None\n"
        "import recordings.tagops\n"
        "assert 'recordings.writer' not in sys.modules\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


# ---- fix round 1: the two privacy rules agree, invisible and lookalike characters ------------
def _plain_rule(tag: str) -> bool:
    """The archive's own rule for agents (format/AGENTS.md): first ASCII-slash folder."""
    return tag.split("/", 1)[0].casefold() == "private"


def _fullwidth(text: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) for c in text)


SLASHES = [
    "\u2044", "\u2215", "\u29f8", "\u2571", "\uff0f", "\u27cb", "\u2e4a", "\u29f6", "\u2afd",
    "\u1735", "\u3033", "\u30ce", "\u31d3", "\u4e3f", "\u2f03", "\u2cc6", "\U0001d23a", "\u2041",
]  # fmt: skip
HIDDEN = [
    "\u3164", "\u115f", "\u1160", "\uffa0", "\u2800", "\u2065", "\u034f", "\u17b4", "\u17b5",
    "\u180b", "\u180c", "\u180d", "\u180e", "\u180f", "\ufe00", "\ufe0f", "\U000e0100",
    "\U000e01ef",
]  # fmt: skip
CONFUSABLE_CASES = [
    ("\u0251", "priv{}te"), ("\u0269", "pr{}vate"), ("\u1d20", "pri{}ate"),
    ("\u1d00", "priv{}te"), ("\u2228", "pri{}ate"), ("\u13e2", "{}rivate"),
    ("\u13a1", "p{}ivate"), ("\u13d9", "pri{}ate"), ("\u13aa", "priv{}te"),
    ("\u13a2", "priva{}e"), ("\u13ac", "privat{}"), ("\u1d18", "{}rivate"),
    ("\u0280", "p{}ivate"), ("\u026a", "pr{}vate"), ("\u1d1b", "priva{}e"),
    ("\u1d07", "privat{}"),
]  # fmt: skip


@pytest.mark.parametrize("slash", SLASHES)
def test_a_slash_lookalike_after_private_is_refused_as_looking_private(slash):
    assert validate_new_tag(f"private{slash}journal") == LOOKS_PRIVATE


def test_a_fullwidth_private_before_a_fullwidth_slash_is_refused():
    assert validate_new_tag(_fullwidth("private") + "\uff0fx") == LOOKS_PRIVATE


@pytest.mark.parametrize("char", HIDDEN, ids=[f"U+{ord(c):04X}" for c in HIDDEN])
def test_an_invisible_character_is_refused(char):
    assert validate_new_tag(f"private{char}") == INVISIBLE


@pytest.mark.parametrize(
    ("char", "pattern"), CONFUSABLE_CASES, ids=[f"U+{ord(c):04X}" for c, _ in CONFUSABLE_CASES]
)
def test_more_lookalike_letters_of_private_are_refused(char, pattern):
    assert validate_new_tag(pattern.format(char)) == LOOKS_PRIVATE


def test_a_tag_with_many_stacked_combining_marks_is_refused():
    assert validate_new_tag("x" + "\u0301" * 4) == TOO_MANY_MARKS
    assert validate_new_tag("x" + "\u0301" * 3) is None
    assert validate_new_tag("zebrax" + "\u0301" * 4) == TOO_MANY_MARKS
    assert "zebra" not in TOO_MANY_MARKS.casefold()


def test_every_accepted_tag_is_private_exactly_when_the_plain_rule_says():
    probes = [
        "private", "Private/x", "privateer", "notes/private", "my talks", "pri vate", "private/",
        "PRIVATE\uff0fx", "\u0440rivate", "caf\u00e9", "private\u00ad", "\u200dprivate",
        "\ufb00/x", "\u2160/x", "private\u00a0", "a\u2044b", "private\u2044x", "x/private",
        *(f"private{s}journal" for s in SLASHES),
        *(f"private{h}" for h in HIDDEN),
        *(p.format(c) for c, p in CONFUSABLE_CASES),
        _fullwidth("private") + "\uff0fx",
        _fullwidth("private"),
    ]  # fmt: skip
    accepted = [t for t in probes if validate_new_tag(t, note_types={"lecture"}) is None]
    assert "private" in accepted and "Private/x" in accepted  # the sweep isn't vacuous
    for tag in accepted:
        assert is_private_tag(tag) == _plain_rule(tag), ascii(tag)


# ---- fix round 1: guards on the operations -------------------------------------------------
def test_operations_refuse_a_bare_string_and_coerce_sequences():
    with pytest.raises(TypeError):
        AddTags("work")
    with pytest.raises(TypeError):
        RemoveTags("talks")
    with pytest.raises(TypeError):
        RemoveTags(("a",), may_expose=RID)
    with pytest.raises(TypeError):
        RestoreTags(effect_of(rec(), rec()), may_expose=RID)
    op = RemoveTags(["a"], {RID})
    assert op.tags == ("a",) and op.may_expose == frozenset({RID})
    assert AddTags([TagRef(tag="a")]).tags == (TagRef(tag="a"),)


def test_restore_refuses_another_recordings_effect():
    before = rec("a")
    effect = effect_of(before, AddTags(you("b")).apply(before))
    other = rec("a").model_copy(update={"id": "20261007T140003-0700_bbbbbbbb"})
    with pytest.raises(ValueError, match="recording"):
        RestoreTags(effect).apply(other)


def test_recorded_clears_after_before_calling_the_op():
    class Boom:
        name = "boom"

        def apply(self, rec):
            raise RuntimeError

    op = Recorded(AddTags(you("work")), "req0001abcd")
    op.apply(rec("talks"))
    assert op.after is not None
    op.op = Boom()
    with pytest.raises(RuntimeError):
        op.apply(rec("talks"))
    assert op.after is None


# ---- fix round 2: the owner's accidental input, not an adversary ----------------------------
def test_japanese_tags_still_pass_though_the_katakana_no_is_a_slash_lookalike():
    # why: the slash mapping only feeds the lookalike test, so a tag whose first folder isn't a
    # private lookalike is untouched.
    assert validate_new_tag("\u30e1\u30e2/\u30ce\u30fc\u30c8") is None
    assert validate_new_tag("\u30ce\u30fc\u30c8") is None


@pytest.mark.parametrize("char", ["\u0337", "\u0338"], ids=["U+0337", "U+0338"])
def test_a_combining_solidus_overlay_is_refused(char):
    assert validate_new_tag(f"private{char}journal") == INVISIBLE
    assert validate_new_tag(f"x{char}") == INVISIBLE


@pytest.mark.parametrize("char", ["\u00a0", "\u2003", "\u3000", "\u202f"])
def test_a_space_that_is_not_a_plain_space_is_refused_inside_a_tag(char):
    assert validate_new_tag(f"my{char}talks") == SPACE
    assert validate_new_tag("my talks") is None


def test_spacing_marks_are_not_counted_as_stacked_marks():
    assert validate_new_tag("\u1000\u103c\u103d\u1031\u1038") is None  # Burmese
    assert validate_new_tag("x" + "\u0903" * 5) is None  # spacing marks (Mc)
    assert validate_new_tag("x" + "\u20dd" * 4) == TOO_MANY_MARKS  # enclosing marks (Me)


def test_operations_refuse_a_single_tagref_or_plain_strings_to_add():
    with pytest.raises(TypeError):
        AddTags(TagRef(tag="a"))
    with pytest.raises(TypeError):
        AddTags(("work",))
    with pytest.raises(TypeError):
        RemoveTags(TagRef(tag="a"))


# ---- the privacy checks on their own (Task 2's review, M2) ---------------------------------------
@pytest.mark.parametrize(
    "tag",
    [
        "\u0440rivate",  # Cyrillic er
        "\uff50\uff52\uff49\uff56\uff41\uff54\uff45",  # fullwidth: private to the app only
        "private\u2044journal",  # a slash lookalike
        "\u0440ri\u200bvate",  # a lookalike that also breaks an earlier rule (INVISIBLE)
        "pri\u0301vate",  # a lookalike that also breaks an earlier rule (NOT_NFC)
    ],
)
def test_a_lookalike_is_found_whatever_other_rule_it_breaks(tag):
    # why: Task 2's review (M2). The tag service checks a tag the archive already has with the
    # privacy rules alone; validate_new_tag names only the first rule a tag breaks, which can be
    # an earlier one, so it can't be asked whether an existing tag is a lookalike.
    assert is_private_lookalike(tag)
    assert validate_new_tag(tag, note_types={"lecture"}) is not None


@pytest.mark.parametrize(
    "tag",
    [
        "private",
        "PRIVATE",
        "Private/health",
        "privateer",
        "notes/private",
        "x/private",
        "talks",
        "my\u00a0talks",  # refused as a new tag (SPACE), but nothing to do with privacy
    ],
)
def test_a_tag_spelled_plainly_is_no_lookalike(tag):
    assert not is_private_lookalike(tag)
