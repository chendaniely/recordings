# Task 3 review: Spec axis (Tier 1), d047bbf..4971dd4

The code and tests match the brief exactly (diffed; only the known U+3000 differs). No scope creep. 415 tests pass. Old and new `is_private_tag` agree on 300k random strings. Probes: `<scratch>/t3rev/probe.py`.

## Findings

1. **Important.** `keepout.py:137` uses `_ANY_ID.search(word)`, so a first word whose ID comes after characters other than `of_` reads as **kept**: `of-3fa9…`, `0f_3fa9…`, `'3fa9…'`, `"3fa9…"`, `(3fa9…)`, `[3fa9…]`, `<3fa9…>`. The list still reads, and the recording is downloaded (probed). §12.1 rule 4 makes a first word that "holds 24 or more hex digits, with or without `of_`, but isn't a valid ID" a typo. Rule 2 covers only an ID "in that position followed by" something. `of-` is one missed Shift away from `of_`, and `x3fa9…` is already a typo while `(3fa9…)` is not. Fix: `_ANY_ID.match(word)`, so the ID must start the word. `3fa9…:` stays kept, and no brief test breaks. Add typo cases for these forms, or ask Dan and record his answer in §12.1.
2. **Minor.** `keepout.py:168`: `splitlines()` also breaks on `\v`, `\f`, `\x1c`–`\x1e`, `\x85`, U+2028 and U+2029. After one of these, the stop phrase and the kept-line list name a line one higher than an editor, `grep -n` or `sed -n` shows (probed). Fix: `re.split(r"\r\n|\r|\n", text)`, plus a test.
3. **Minor.** `keepout.py:189,203`: a path containing NUL (TOML allows `\u0000`) makes `read_consent` and `signature` raise `ValueError`, breaking "never raises". Fix: `except (OSError, ValueError)`.
4. **Minor (question for Dan).** Lines that use U+2010, U+2011, U+2012, U+2015 or U+2212 as the dash (NFKC leaves these alone), or `•`, `–` or `—` as the bullet, read as kept. That follows §12.1's literal sets, and these lines are listed. Should any Unicode dash (category Pd) count?
5. **Minor.** Spec §12.1 (line 1397) and §19 (line 2520) cite `consent.py:17`, which is now `from __future__ import annotations`. Point them at `LEGACY_ENTRY_RE` or `keepout`.

## Scenarios
- **C5.10:** proven for Task 3 by `test_an_id_followed_by_words_is_a_kept_line_and_listed` (kind `kept`, `review_lines == (2,)`, ID not in `ids`). Task 8 does the CLI half.
- **Review Focus 1:** pinned. The CRLF, BOM (leading and mid-file), NBSP, zero-width space, word joiner and fullwidth tests each fail without `visible()`.

## Docs grep
`ENTRY_RE` appears only in the 2a plan, which is historical. Main spec line 1409 already says a typo stops the import, and runbook line 728 still holds. No doc quotes the old error text, and nothing says "not yet", "planned" or "unverified" about what this task touched.

## Security checklist
1. **Secrets:** fine. Stop phrases are fixed text with line numbers only; the path that the old error showed is gone.
2. **Permissions:** not applicable. The change creates no files.
3. **Network:** fine. Pure functions; only the standard library, `models` and `refs`; tests use `tmp_path`.
4. **Failing closed:** finding 1 (Important); findings 2 and 3 (Minor). Each of these stops the list (all probed): a missing file, a folder, Latin-1, UTF-16, an empty file, only invisible characters, no IDs, a typo, a split ID, a 23-hex ID, a UUID-form ID. `ids` survive a stop, for late listings (§12.1). The privacy gate is unchanged.
5. **Injection:** not applicable. No SQL, shell or paths built from IDs.
6. **Logging:** fine. No logging added, and no `str(exc)` or `exc_info`.

Verdict: changes needed (0 Critical, 1 Important)
