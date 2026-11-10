# Task 3 review: Standards (Tier 1), d047bbf..4971dd4

Checked: the code matches the brief verbatim, apart from the known U+3000. There are no other literal invisible characters. Ruff is clean, and the six test files pass (415). `is_private_tag` still gives the same answers: casefolding and dropping Cf characters never add or remove a `/`. No archive writes, no titles in phrases, synthetic IDs only.

## Important (outside the Standards axis: what the spec's wording does; needs Dan's ruling)
1. **keepout.py:319-321, decision 12's branch.** A note line whose letters are all a-f, plus digits, counts as a typo and stops every import. I checked three: `- Added 2026-10-23`, `Added 2026-10-23: two more` and `A bad decade` each gives "has 1 line that looks like an ID…". This fails closed, so nothing leaks. But a dated note line stops the sync, and Dan's real list may already have one. This is what "only hex digits … 8 or more" says word for word. A possible rule: require one token of 8 or more hex digits.

## Minor
2. **keepout.py:340 `splitlines()`** also splits on `\f`, `\v`, `\x1c`-`\x1e`, U+0085 and U+2028/9. So the line numbers in a stop can differ from the editor's. Also, `` - `ID` ``U+2028`kept: …` reads as an exclusion (I checked it). Fix: split on `\n` after mapping `\r\n` and `\r`.
3. **Duplicated Code: tagops.py:296-299.** `_reads_as` still has its own copy of `models.fold`, so the line "The one place it lives" in fold's docstring (models.py:191) is wrong. Fix: `text = fold(part)`.
4. **Duplicated Code: keepout.py.**
   - `(?:of_)?[0-9a-f]{32}` appears three times (lines 240, 241, 249), and `_LEAD` repeats `_BULLET`. Build both from one fragment each.
   - `read_consent` builds the same `ConsentList(ids=frozenset(), lines=(), stop=…)` three times.
5. **Mysterious Name: keepout.py:401.**
   - `i + 6` relies on the date prefix's length. Use the match's `.end()`.
   - `_and` is unclear. `_join_and` would say what it does. It also raises IndexError on `typo_phrase([])`.
6. **Docs.**
   - The keepout docstring says "Pure functions over text", but `read_consent` and `signature` do I/O.
   - The 2b spec's §12.1 still points to `consent.py:17` as today's parser. That line is now in the docstring, and the rule is `LEGACY_ENTRY_RE` (consent.py:24). Dan's preferences say wrong docs are fixed in the change that made them wrong.
7. **Commit body.** It quotes only test_keepout's failing line, not the one from the new `test_the_2a_import_now_reads_of_and_upper_case_exclusions`. The plan's commit format asks for the failing output line of each new test. These pass: the subject format, the `Plan:` trailer and `commitmsg.py`. The trailer names Sonnet 5.5, which the plan's rule allows for an implementer on another model.

Not reported: Speculative Generality (`signature`, `valid_word` and `LEGACY_ENTRY_RE` are not used yet) and Middle Man (`load_consent_list`). The plan asks for each of them for later tasks.

Verdict: changes needed (0 Critical, 1 Important)
