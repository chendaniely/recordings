"""The tag operations (tagging spec §4): pure, with the privacy gate inside them."""

import subprocess
import sys
from datetime import datetime

import pytest
from pydantic import ValidationError
from recordings.models import Recording, TagRef
from recordings.tagops import (
    CASE_VARIANT,
    EMPTY_PART,
    INVISIBLE,
    LOOKS_PRIVATE,
    NOT_NFC,
    NOTE_TYPE,
    PATH_LIKE,
    TOO_LONG,
    AddTags,
    Effect,
    LostTag,
    Recorded,
    RemoveTags,
    RestoreTags,
    WouldExpose,
    effect_of,
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
    odd = "talks​"
    assert names(RemoveTags((odd,)).apply(rec(odd, "school"))) == ["school"]


@pytest.mark.parametrize(
    "spelling", ["private", "Private/health", "PRIVATE", "ｐｒｉｖａｔｅ", "pri​vate"]
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
        ("pri​vate", INVISIBLE),  # zero-width space
        ("‮private", INVISIBLE),  # right-to-left override
        ("ｐｒｉｖａｔｅ", LOOKS_PRIVATE),  # fullwidth (tagging plan, decision 2)
        ("рrivate", LOOKS_PRIVATE),  # Cyrillic er
        ("privαte/x", LOOKS_PRIVATE),  # Greek alpha
        ("prİvate", LOOKS_PRIVATE),  # capital I with a dot
        ("prívate", LOOKS_PRIVATE),  # an accent
        ("..", PATH_LIKE),
        ("school/../x", PATH_LIKE),
        ("a\\b", PATH_LIKE),
        ("a\x00b", INVISIBLE),
        ("a\nb", INVISIBLE),
        ("a b", INVISIBLE),  # line separator
        ("x" * 65, TOO_LONG),
        ("/".join(["x" * 60] * 4), TOO_LONG),  # 243 characters
        ("café", NOT_NFC),
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
        "café",
        "notes/lecture",
        "日本語/メモ",
        "my talks",
    ],
)
def test_a_new_tag_that_meets_the_rules_is_allowed(tag):
    assert validate_new_tag(tag, note_types={"lecture"}) is None


@pytest.mark.parametrize("tag", ["private ", " private", "private ", " private"])
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
        ("zebra​secret", INVISIBLE),
        ("zebracé", NOT_NFC),
        ("zebra//secret", EMPTY_PART),
        ("zebra" * 20, TOO_LONG),
        ("zebra/../secret", PATH_LIKE),
        ("notes/zebra", NOTE_TYPE),
        ("рrivate/zebra", LOOKS_PRIVATE),
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
