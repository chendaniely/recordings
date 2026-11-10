# Task 8 review: Standards (Tier 1)

Range 1c7f1d9..e048326. The code matches the brief's code exactly (checked with diff). 40 tests pass and ruff is clean.

**No hard CLAUDE.md violations.** `private` goes through `Writer.mutate(SetPlaudFields)`, which calls `tagops.AddTags`. Logs carry only IDs and class names. Fixtures are synthetic. The runbook matches §12.4 word for word, and its line references are still correct. The CLAUDE.md edit is accurate.

## Important
1. **`flags.py` 136, 196, 205: warnings repeat at every tick** (against decision 46's "logged once per class"). Task 13's `_log_late` logs only when the report changes (plan line 13763), but these lines run every 2 s regardless. One outside-edited consent-listed recording gives about 43,000 warnings a day. **Fix:** drop them or log at debug level; the `LateReport` already carries the IDs and classes.

## Minor (judgement calls)
2. **Duplicated Code:** `commands._here` (105) copies `flags._listed_here` but leaves out the pending-index fallback. So `plaud consent` can leave out a recording the worker tags. **Fix:** one shared lookup.
3. **Duplicated Code:** `commands._fail`/`_config` (67, 75) copy `cli.py:158-170`. **Fix:** a shared helper module.
4. **Duplicated Code, `flags.py`:** the load-and-`except (KeyError, OSError, ValueError)` pattern appears three times (141, 154, 183). Plaud IDs are worked out from sources twice (144 and `_plaud_ids`). The `kept_here_at` check appears twice (161, 246). **Fix:** reuse `_plaud_ids`; add a `_kept_here` helper.
5. **Primitive Obsession:** lines 50-52 restate `checks.FLAG_KINDS`/`BLOCKERS`, and line 262 uses a bare `"kept-here"`. **Fix:** define the constants once in `checks.py`.
6. **Feature Envy:** `flag()` (108) and `keep_here()` (255) filter `open_flags()` by recording in Python. **Fix:** a `recording_id=` parameter on `PlaudDB.open_flags`.
7. **Repeated Switches:** `on_plaud` (int or str) and `in_archive` (list or str) force `isinstance` checks (140, 184-198). **Fix:** `null` plus a phrase field.
8. **`commands.py:100` uses `State.open`** (which creates and migrates tables) in a command that only reads. `state.py` gives readers `State.read_only`. **Fix:** use it.
9. **The consent file is read twice** (137, 161). The reads can differ, and the second misses `ValueError`. **Fix:** read once; `parse_consent(text)`.

## Commits
The subject format, a body that says why, the failing lines, the `Plan:` trailers and `commitmsg.py` (exits 0) are all correct. `Co-Authored-By` names the implementer's model. Minor: e048326 leaves out the runbook test's failing line.

Verdict: changes needed (0 Critical, 1 Important)
