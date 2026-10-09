import copy
import json
from datetime import datetime, timezone

import pytest

from recordings.plaud.normalise import (
    DROPPED_KEYS,
    NORMALISER_VERSION,
    diarization_fingerprint,
    envelope_sha256,
    is_complete,
    link_errors,
    meta_fetched_at,
    normalise,
    note_blocks,
    part_hashes,
    transcript_rows,
)

HEX = "0123456789abcdef0123456789abcdef"


def test_a_rotating_presigned_url_is_not_a_change(plaud_env):
    assert envelope_sha256(plaud_env(presigned="one")) == envelope_sha256(plaud_env(presigned="two"))


def test_only_the_query_part_of_a_url_inside_text_is_replaced(plaud_env):
    # why: audio-router once replaced the whole string, which erased a note from the hash.
    text = "See https://notes.example/a.png?X-Amz-Signature=1 and then decide."
    env = plaud_env(notes=[{"data_type": "auto_sum_note", "data_id": "n", "data_tab_name": "S",
                            "data_content": text}])
    (block,) = normalise(env)["note_list"]
    assert block["data_content"] == "See https://notes.example/a.png?<presigned> and then decide."
    edited = copy.deepcopy(env)
    edited["note_list"][0]["data_content"] = text.replace("decide", "agree")
    assert envelope_sha256(edited) != envelope_sha256(env)


def test_the_fetchers_own_fields_are_dropped(plaud_env):
    env = plaud_env()
    noisy = copy.deepcopy(env)
    noisy["_meta"] = {"tool": "something else", "enriched_from_links": ["transaction"]}
    noisy["source_list"][0]["_fetched_from_data_link"] = True
    noisy["source_list"][0]["data_link"] = "https://x.example/y?X-Amz-Expires=300"
    assert envelope_sha256(noisy) == envelope_sha256(env)


def test_both_id_forms_are_one_id(plaud_env):
    assert envelope_sha256(plaud_env("of_" + HEX)) == envelope_sha256(plaud_env(HEX))


def test_reordered_blocks_are_not_a_change_but_reordered_segments_are(plaud_env):
    env = plaud_env()
    blocks = copy.deepcopy(env)
    blocks["note_list"].reverse()
    blocks["source_list"].reverse()
    assert envelope_sha256(blocks) == envelope_sha256(env)
    rows = json.loads(env["source_list"][0]["data_content"])
    swapped = plaud_env(segments=[rows[1], rows[0]])
    assert envelope_sha256(swapped) != envelope_sha256(env)


def test_the_transcripts_inner_json_is_compared_canonically(plaud_env):
    env = plaud_env()
    rows = json.loads(env["source_list"][0]["data_content"])
    spaced = copy.deepcopy(env)
    spaced["source_list"][0]["data_content"] = json.dumps(
        [dict(reversed(list(r.items()))) for r in rows], indent=4)
    assert envelope_sha256(spaced) == envelope_sha256(env)


def test_an_unknown_new_field_is_a_change(plaud_env):
    env = plaud_env()
    assert envelope_sha256({**env, "brand_new_field": 1}) != envelope_sha256(env)


def _leaves(obj, path=()):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key not in DROPPED_KEYS:
                yield from _leaves(value, (*path, key))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from _leaves(value, (*path, i))
    else:
        yield path, obj


def _changed(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if isinstance(value, str):
        return value + "x"
    return "x"


def _edited(env, path):
    edited = copy.deepcopy(env)
    target = edited
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = _changed(target[path[-1]])
    return edited


def test_changing_any_field_off_the_drop_list_changes_the_hash(plaud_env):
    # why: §9.1.1's property test. A normaliser that drops too much silently loses edits.
    env = plaud_env()
    base = envelope_sha256(env)
    leaves = list(_leaves(env))
    assert len(leaves) > 20
    for path, _value in leaves:
        assert envelope_sha256(_edited(env, path)) != base, path


def _row(content):
    return [{"content": content, "start_time": 0, "end_time": 900, "speaker": "Ada",
             "original_speaker": "Speaker 1"}]


def _reversed_blocks(env):
    env = copy.deepcopy(env)
    env["note_list"].reverse()
    env["source_list"].reverse()
    return env


_URL = "http://a.example/b?X-Amz-S=1"
_IDEMPOTENCE_CASES = {
    "plain": lambda make: make(),
    "link error": lambda make: make(link_error=True),
    "url in text": lambda make: make(notes=[{
        "data_type": "auto_sum_note", "data_id": "n", "data_tab_name": "S",
        "data_content": "See [x](https://n.example/a.png?X-Amz-Signature=1) now."}]),
    "polish": lambda make: make(polish=True),
    "of_ id": lambda make: make("of_" + HEX),
    "reversed blocks": lambda make: _reversed_blocks(make(polish=True)),
    "url then newline": lambda make: make(segments=_row(f"{_URL}\nfirst")),
    "url then quote": lambda make: make(segments=_row(f'{_URL}" q')),
}


@pytest.mark.parametrize("case", _IDEMPOTENCE_CASES)
def test_normalising_twice_changes_nothing(plaud_env, case):
    once = normalise(_IDEMPOTENCE_CASES[case](plaud_env))
    assert normalise(once) == once


def test_a_presigned_url_inside_a_transcript_row_keeps_the_row_whole(plaud_env):
    # why: the scrub once ran on the encoded JSON, where the URL is followed by `\n` or `\"`.
    # It ate the backslash, so the row stopped parsing and its parts fell into "other", or two
    # different texts hashed alike.
    for tail in ("\nfirst", '" q'):
        env = plaud_env(polish=True, segments=_row(_URL + tail))
        parts = part_hashes(env)
        assert {"transcript", "speakers"} <= set(parts), tail
        (row,) = transcript_rows(normalise(env))
        assert row["content"] == "http://a.example/b?<presigned>" + tail
        edited = plaud_env(polish=True, segments=_row(_URL + tail + "x"))
        assert _parts_changed(env, edited) == {"transcript"}, tail
    first = plaud_env(segments=_row(_URL + "\nfirst"))
    second = plaud_env(segments=_row(_URL + "\nsecond"))
    assert part_hashes(first)["transcript"] != part_hashes(second)["transcript"]


def test_a_snapshot_with_link_errors_is_incomplete(plaud_env):
    assert is_complete(plaud_env())
    env = plaud_env(link_error=True)
    assert not is_complete(env) and link_errors(env) == ["auto_sum_note"]
    unfetched = plaud_env()
    unfetched["note_list"][0] = {**unfetched["note_list"][0], "data_content": "",
                                 "data_link": "https://x.example/y?X-Amz-Expires=300"}
    assert not is_complete(unfetched)
    # A link that answered with an empty body is no better than one that failed.
    empty_body = plaud_env()
    empty_body["note_list"][0] = {**empty_body["note_list"][0], "data_content": " ",
                                  "_fetched_from_data_link": True}
    assert link_errors(empty_body) == ["auto_sum_note"]


def test_a_legacy_snapshot_lists_its_link_errors_in_meta(plaud_env):
    # why: audio-router's retired rescue script wrote these, with no per-block error.
    legacy = plaud_env()
    legacy["_meta"] = {"tool": "audio-router", "data_link_errors": ["a link that failed"]}
    assert link_errors(legacy) == ["legacy"]
    legacy["_meta"]["data_link_errors"] = []
    assert is_complete(legacy)


def test_a_legacy_snapshot_says_when_it_was_fetched(plaud_env):
    env = plaud_env()
    assert meta_fetched_at(env) is None
    env["_meta"] = {"fetched_at": "2025-03-01T04:00:00-08:00"}
    assert meta_fetched_at(env) == datetime(2025, 3, 1, 12, tzinfo=timezone.utc)
    env["_meta"] = {"fetched_at": "2025-03-01T12:00:00Z"}
    assert meta_fetched_at(env) == datetime(2025, 3, 1, 12, tzinfo=timezone.utc)
    for unusable in ("2025-03-01T12:00:00", "yesterday", 1740830400):  # no offset, not a time
        env["_meta"] = {"fetched_at": unusable}
        assert meta_fetched_at(env) is None
    assert meta_fetched_at(["not", "an", "object"]) is None


def test_transcript_rows_and_their_absence(plaud_env):
    assert [r["content"] for r in transcript_rows(plaud_env())] == ["Hello there.", "Hi."]
    env = plaud_env()
    env["source_list"][0]["data_content"] = "not json"
    assert transcript_rows(env) is None
    env["source_list"] = []
    assert transcript_rows(env) is None


def test_note_types_are_plaud_and_the_tab_ascii_folded_and_numbered(plaud_env):
    # Review Focus 4: a tab name in another script, or with punctuation, gives a safe note type.
    env = plaud_env(notes=[
        {"data_type": "auto_sum_note", "data_id": "a", "data_tab_name": "Summary", "data_content": "x"},
        {"data_type": "auto_sum_note", "data_id": "b", "data_tab_name": "Summary", "data_content": "y"},
        {"data_type": "auto_sum_note", "data_id": "c", "data_tab_name": "会議メモ / Q&A", "data_content": "z"},
        {"data_type": "consumer_note", "data_id": "d", "data_tab_name": "会議", "data_content": "w"},
        {"data_type": "high_light", "data_id": "e", "data_tab_name": None, "data_content": "[]"},
    ])
    types = [t for t, _, _ in note_blocks(env)]
    # The outline first, then note_list sorted by (type, tab, id): "Summary" sorts before "会議…".
    assert types == ["plaud-outline", "plaud-summary", "plaud-summary-2", "plaud-q-a",
                     "plaud-consumer-note", "plaud-high-light"]
    assert all(t.isascii() and " " not in t and "/" not in t for t in types)


def test_note_blocks_pair_each_normalised_block_with_its_raw_one(plaud_env):
    # why: notes render from the raw block, never the normalised one.
    text = "See [the chart](https://notes.example/a.png?X-Amz-Signature=1) here."
    env = plaud_env(notes=[{"data_type": "auto_sum_note", "data_id": "n", "data_tab_name": "S",
                            "data_content": text}])
    _outline, (note_type, block, raw) = note_blocks(env)
    assert note_type == "plaud-s" and raw["data_content"] == text
    assert block["data_content"] == "See [the chart](https://notes.example/a.png?<presigned>) here."


def test_the_diarization_fingerprint_ignores_names_but_not_timing(plaud_env):
    env = plaud_env()
    rows = json.loads(env["source_list"][0]["data_content"])
    renamed = plaud_env(segments=[{**rows[0], "speaker": "Grace"}, rows[1]])
    resegmented = plaud_env(segments=[{**rows[0], "end_time": 1400}, rows[1]])
    assert diarization_fingerprint(renamed) == diarization_fingerprint(env)
    assert diarization_fingerprint(resegmented) != diarization_fingerprint(env)


def _parts_changed(before, after):
    old, new = part_hashes(before), part_hashes(after)
    return {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}


def test_part_hashes_say_what_changed(plaud_env):
    env = plaud_env()
    assert set(part_hashes(env)) == {"title", "transcript", "speakers", "notes:plaud-outline",
                                     "notes:plaud-summary", "notes:plaud-high-light", "other"}
    rows = json.loads(env["source_list"][0]["data_content"])
    assert _parts_changed(env, plaud_env(name="Week 5")) == {"title"}
    renamed = plaud_env(segments=[{**rows[0], "speaker": "Grace"}, rows[1]])
    assert _parts_changed(env, renamed) == {"speakers"}
    reworded = plaud_env(segments=[{**rows[0], "content": "Hello!"}, rows[1]])
    assert _parts_changed(env, reworded) == {"transcript"}
    edited = copy.deepcopy(env)
    edited["note_list"][0]["data_content"] += " Then they leave."
    assert _parts_changed(env, edited) == {"notes:plaud-summary"}
    assert _parts_changed(env, {**env, "serial_number": "TEST0002"}) == {"other"}


def test_the_polished_transcript_belongs_to_the_transcript_and_speakers(plaud_env):
    # why: Plaud renames a speaker in both segment blocks. Were `transaction_polish` "other", one
    # relabelling sweep would trip the circuit breaker, which counts only "other" (§9.1.1).
    env = plaud_env(polish=True)
    rows = json.loads(env["source_list"][0]["data_content"])
    renamed = plaud_env(polish=True, segments=[{**rows[0], "speaker": "Grace"}, rows[1]])
    assert _parts_changed(env, renamed) == {"speakers"}
    polished = copy.deepcopy(env)
    (block,) = [b for b in polished["source_list"] if b["data_type"] == "transaction_polish"]
    block["data_content"] = json.dumps([{**rows[0], "content": "Hello, there."}, rows[1]])
    assert _parts_changed(env, polished) == {"transcript"}


def test_every_field_lands_in_some_part_and_new_row_fields_are_other(plaud_env):
    # why: a field in no part changes the snapshot while the Sync screen shows nothing, and the
    # circuit breaker never counts it. A field Plaud starts adding to every segment row is the
    # volatile field the breaker exists for, so it is "other", never "transcript".
    env = plaud_env(polish=True)
    for path, _value in _leaves(env):
        assert _parts_changed(env, _edited(env, path)), path
    rows = json.loads(env["source_list"][0]["data_content"])
    tagged = plaud_env(polish=True, segments=[{**r, "request_id": "r-1"} for r in rows])
    assert _parts_changed(env, tagged) == {"other"}
    # A segment block that doesn't parse stays in "other", whole.
    garbled = copy.deepcopy(env)
    garbled["source_list"][-1]["data_content"] = "not json"  # the polish block
    more = copy.deepcopy(garbled)
    more["source_list"][-1]["data_content"] = "not json either"
    assert _parts_changed(garbled, more) == {"other"}


def _note(text):
    return {"id": HEX, "note_list": [{"data_type": "auto_sum_note", "data_id": "n",
                                      "data_tab_name": "S", "data_content": text}]}


def test_text_glued_to_a_signed_link_is_still_compared():
    # why: the scrub once ran to the next space, so an edit to `)ok` or to the second of two
    # adjacent images never changed the hash. A permanent miss.
    glued = "see (http://h/x?X-Amz-S=1)"
    assert envelope_sha256(_note(glued + "ok")) != envelope_sha256(_note(glued + "NO"))
    images = "![a](http://h/x?X-Amz-S=1)![b](http://h/{}?X-Amz-S=2)"
    assert envelope_sha256(_note(images.format("y"))) != envelope_sha256(_note(images.format("z")))
    # The signature still goes, and a sentence's period stays.
    assert normalise(_note("a http://h/q?a=1&X-Amz-Date=2%3A3. b"))["note_list"][0][
        "data_content"] == "a http://h/q?<presigned>. b"


@pytest.mark.parametrize("hostile", [
    {"id": HEX, "name": "\ud800"},                                          # lone surrogate
    {"id": HEX, "source_list": [{"data_type": "transaction", "data_id": "t",
                                 "data_content": "[" * 5000 + "]" * 5000}]},     # too deep
    {"id": HEX, "source_list": [{"data_type": "transaction", "data_id": "t",
                                 "data_content": '[{"start_time": ' + "9" * 5000 + "}]"}]},
    {"id": HEX, "source_list": [{"data_type": ["x"], "data_id": "t"}]},          # unhashable type
    {"id": HEX, "source_list": [{"data_type": "transaction", "data_id": "t",
                                 "data_content": '[{"content": "x", "start_time": NaN}]'}]},
])
def test_hostile_payloads_hash_instead_of_raising(hostile):
    # why: one odd snapshot from a third party must not abort a whole import or sync pass.
    assert envelope_sha256(hostile) == envelope_sha256(copy.deepcopy(hostile))
    assert "other" in part_hashes(hostile)
    assert normalise(normalise(hostile)) is not None


def _with_rows(rows):
    return {"id": HEX, "source_list": [{"data_type": "transaction", "data_id": "t",
                                        "data_content": json.dumps(rows)}]}


def test_the_fingerprint_survives_a_rename_without_original_speaker_and_float_times():
    def rows(first, mid=1500):
        return [{"content": "a", "start_time": 0, "end_time": mid, "speaker": first},
                {"content": "b", "start_time": mid, "end_time": 2600, "speaker": "Speaker 2"}]
    base = diarization_fingerprint(_with_rows(rows("Ada")))
    assert diarization_fingerprint(_with_rows(rows("Grace"))) == base         # a rename
    assert diarization_fingerprint(_with_rows(rows("Ada", 1500.0))) == base   # 1500.0 is 1500
    assert diarization_fingerprint(_with_rows(rows("Ada", 1400))) != base     # re-segmented
    assert (diarization_fingerprint(_with_rows(rows("Speaker 1")))
            != diarization_fingerprint(_with_rows(rows("Speaker 3"))))        # generic label


def test_a_new_field_on_a_note_block_is_other_not_a_notes_edit(plaud_env):
    # why: §9.1.1. Unknown fields count towards the circuit breaker; notes edits never do. A
    # volatile field Plaud adds to every note block must not look like Dan edited every note.
    env = plaud_env()
    tagged = copy.deepcopy(env)
    for block in tagged["note_list"]:
        block["volatile_new"] = "abc"
    for block in tagged["source_list"]:
        if block["data_type"] == "outline":
            block["volatile_new"] = "abc"
    assert _parts_changed(env, tagged) == {"other"}
    edited = copy.deepcopy(env)
    edited["note_list"][0]["data_title"] = "Renamed"
    assert _parts_changed(env, edited) == {"notes:plaud-summary"}


GOLDEN_ENVELOPE = {
    "id": "of_0123456789abcdef0123456789abcdef", "name": "Week 4 \u4f1a\u8b70", "duration": 2600,
    "presigned_url": "https://a.example/x.mp3?X-Amz-Signature=abc",
    "source_list": [
        {"data_type": "transaction", "data_id": "t-1", "data_tab_name": None,
         "data_content": json.dumps([
             {"content": "Hello \u00e9.", "start_time": 0, "end_time": 1500, "speaker": "Ada",
              "original_speaker": "Speaker 1"},
             {"content": "Hi.", "start_time": 1500, "end_time": 2600, "speaker": "Speaker 2",
              "original_speaker": "Speaker 2"}])},
        {"data_type": "outline", "data_id": "o-1", "data_tab_name": None,
         "data_content": '[{"topic": "Hi", "start_time": 0}]'}],
    "note_list": [{"data_type": "auto_sum_note", "data_id": "n-1", "data_tab_name": "Summary",
                   "data_content": "See [c](https://n.example/a.png?X-Amz-Signature=1) now."}],
    "_meta": {"tool": "x"},
}


def test_the_normaliser_version_is_pinned_to_its_behaviour():
    # why: every stored Plaud hash and output `version` is computed by this function. Changing a
    # rule without bumping NORMALISER_VERSION makes the next sync see every recording as changed
    # (or miss real edits). Change these only together with NORMALISER_VERSION.
    assert NORMALISER_VERSION == 1
    assert envelope_sha256(GOLDEN_ENVELOPE) == (
        "dc6cb5b2bd9bc97705148b6ae3cdc355e5a9ac338b8b2f6c986bfbe594ac2d8c")
    assert part_hashes(GOLDEN_ENVELOPE) == {
        "title": "ffba669af7869bb0f89d4b48a826866cdb406fc6495268b43df7e97bdaff7161",
        "transcript": "67e0b85575db1594ffabddb4212c9211d20d4e7d6604e6fa52200b622b747b31",
        "speakers": "52e370b791f4b3277153c56521f8fcfcd7f0827300a7dbaff76524039f141fbf",
        "notes:plaud-outline": "19c6f09bde57fc5e25d3ae808a508de00674a79d25fb87cd898a41305ae53855",
        "notes:plaud-summary": "0fcd3affdaa0fd4a9e19924fc9a00f123aacfb4fdb9f814c8057850f77314cab",
        "other": "61b023a3b6f767843c8ff2b059b872efbaae82554d17edf3f7b5c9eb9da3d8ba"}
    assert diarization_fingerprint(GOLDEN_ENVELOPE) == (
        "df7b445ce1c5b6fd3c6907eb3d64349e443feece735a1f7ccda674771a29632b")
