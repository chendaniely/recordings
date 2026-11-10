# Task 9 review: Standards (Tier 1), fc0a45b..315c964

`privacy.py` and `test_plaud_marker.py` match the brief byte for byte (diffed). Ruff check and format pass.

## Hard violations
None. Archive changes go only through `Writer.mutate`/`SetPlaudFields`, and `private` lands before outputs. Only title hashes reach `state.db`. No new log. Reasons are decision 15's phrases, and `kept_out_word` is the configured word, never title text. FORMAT.md's source and the demo copy are identical (`cmp`). The test's hand edit of `recording.json` simulates an outside edit, as `test_plaud_flags.py:213` does.

## Judgement calls (all Minor)
1. **FORMAT.md:66** (also reconcile.py:33): "stays removed until a Plaud title changes" is false for case 5 (a new matching pattern) and case 4 (lost `state.db`). Spec §13.1 says "for the patterns it already passed". Fix: "…until a Plaud title changes, or a pattern added to the list matches it."
2. **reconcile.py:138**: `matches_auto_private`'s docstring still says "reconcile on every snapshot's title". Now only due titles reach it, through `privacy.judge`. A stale doc: fix it in this change.
3. **reconcile.py:32/35**: "the keep-out words run too", but with no `privacy`, `words=()`.
4. **Duplicated Code, writer.py:396/405 vs 305 and `settings.plaud_settings`:67-73**: `valid_word` runs twice, and the key is read a second way. Pass the raw word and let `__init__` check it.
5. **Dead branch, writer.py:396**: `else None` can't run, because `patterns_from_config` (line 390) already refused a non-table `[plaud]`.
6. **Two sources of truth, writer.py:870 / reconcile.py:107**: the Writer passes `auto_private=`, which `privacy` overrides. `Reconciled` holds `add_private` and `verdict.add_private`, and `verdict` is Optional though always set.
7. **Repeated Switches, privacy.py:172**: `why_phrase`'s if-cascade falls through silently to "title changed". A `dict[Why, str]` would fail loudly.
8. **Mysterious Name, privacy.py:64**: `current_patterns` reads like a getter but records a version.
9. **Flag argument, privacy.py:136/147/152**: the positional `True`/`False` for `words_too` would read better as a keyword.

## Commits
`feat(plaud): 🤖 …`: `scripts/commitmsg.py` passes, the body says why and quotes the failing line, and `Plan: … Task 9` is present. `Co-Authored-By: Claude Sonnet 5.5` differs from the brief's text. That's allowed if Sonnet made the commit.

## For the Spec axis
`Writer.reindex` doesn't catch `sqlite3.Error`, so a `state.db` refusal in `_record_privacy`/`current_patterns` stops the whole reindex. `current_patterns` reads and writes in separate transactions.

Verdict: approve
