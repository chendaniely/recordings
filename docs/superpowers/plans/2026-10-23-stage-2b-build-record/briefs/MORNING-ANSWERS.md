# Dan's answers to the morning questions: build 1 + 6, 3, 4 and 5

Read `BUILD-FAST.md` (same folder) first; its rules apply. Dan answered on 2026-11-09: read Phase 1's
Q&A §9 (`docs/superpowers/plans/2026-10-23-stage-2b-phase-1-qa.md`), which is binding, including
where it changes decision 16. Question 2 (the allow-list) is **not** to be built.

**Test first:** each test fails before its fix, and the commit quotes the failing line. One commit
per item below. `make check` before each; `make e2e` when `recordings_ui` or the frontend changes;
`make e2e-live` when the sync run or the screens it drives change. Every fix fails closed. Never
amend, reset or push.

## Item A (questions 1 and 6): one re-key rule

The rule, in the spec's terms: a new ID with the start time and duration (within 1 second on both,
`REKEY_SECONDS`) of anything consent-listed or kept out is treated as that recording until Dan
confirms, **whether the old ID has vanished, is still listed, or is already here**.

Where it lives: `packages/core/src/recordings/plaud/compare.py`, `rekey_matches` (~255) and the
`remember_keyed_out` call in the compare's store (~523).

1. **Still listed.** `rekey_matches` drops the `pid not in listed` requirement. The old ID's own row
   can't match itself: it is in `excluded`, so it's not in `new`.
2. **The first listing that shows both.** The cache only learns an ID's date after the compare
   (`remember_keyed_out` runs last), so a listing that first shows the old ID and the new one
   together has nothing to match against, and the new ID would import in that very sync. So match
   against the cache's dates **and this listing's own rows** that are consent-listed or whose title
   starts with an enforced keep-out word (the rows carry their own start time and duration). Pin it:
   the first-ever listing, with both IDs in it, imports neither.
3. **Already here (question 6).** `remember_keyed_out` today takes `bucket == EXCLUDED` rows only, and
   `HERE` wins in `classify`, so a recording here that went on the consent list, or was kept out
   late, never gets its date remembered. Remember those too (kinds as now), and make them count in
   `rekey_matches`' candidates: `excluded_ids` holds consent IDs but not a here row kept out late.
   A recording Dan kept with **Keep it here** (or released) does not count: check how `_released`
   and Keep it here record that, and pin it.
4. A match behaves exactly as a match does today: `EXCLUDED`, `BY_REKEY`, `rekey_of` set, never
   fetched or imported, no title stored or shown (`classify`'s branch), the flag on Status and the
   Sync screen, and Release asks with no title. Don't build a new state. The `SEVERAL` handling
   stays.
5. Existing tests that pin "the old ID still listed doesn't match" are pinning the behaviour Dan just
   changed: rewrite them to the new rule and say so in the commit.
6. **Docs, same commit:** the spec's decision 16 (~line 503, the sentence "A new ID with the same
   start time and duration as a vanished consent-listed or kept-out one") and §11.4 step 6 (~1213,
   "While a consent-listed or kept-out ID is absent", and the "with the old ID still listed beside
   it" clause), §9.5 if needed, and a Revisions line dated 2026-11-09 citing the Q&A §9. Check whether
   the flag's wording ("back under a new ID") still reads true, and the user guide's and CHANGELOG's
   lines on it, the module note and `rekey_matches`' docstring.

## Item B (question 3): `secure_delete` on the cache

`packages/core/src/recordings/plaud/cache.py`: every writable connection sets `PRAGMA
secure_delete=ON`. There are two places that open the file (`Cache.db` ~204, and the one ~149 for
`take_preview`); a read-only connection needs nothing. Test by behaviour if you can: a title written,
then nulled or deleted by the real method, is no longer in the file's bytes. Check SQLite's docs for
how the pragma behaves per connection and with this file's journal mode (it isn't WAL), and say what
you checked. Doc: spec §9.5 and a Revisions line; the CLAUDE.md paragraph on the cache if it now
says something untrue.

## Item C (question 4): the expiry says a long run held the sync

`packages/core/src/recordings/plaud/requests.py`: `EXPIRED` (~112) says "the Plaud worker didn't run
it within the hour. Press again once it's back.", and `expire` (~582) is called from `worker.py`
(~966 and ~1293). Keep the one-hour rule for every kind. When a row expires while another run holds
`plaud`, its message is a new fixed phrase saying a long Plaud run held the sync, and to press again
when it ends (use the run's `runlock.JOB_LABELS` name if that's simple; never a title). A lock you
can't read keeps today's phrase. Orphaned followers keep today's. Pin: lock held gives the new phrase,
no lock gives the old. Doc: the user guide where it explains that message, the spec's §9.3 or wherever
the phrase is quoted, a Revisions line.

## Item D (question 5): a new keep-out word re-checks titles already here

`packages/core/src/recordings/plaud/listing_privacy.py`, `privacy_from_listing` (~74): a title whose
hash equals the marker's is skipped (~103), so a word added later never sees it. New private patterns
already re-check recordings here (`privacy.py`, case 5, `words_too=False`), so this closes that
inconsistency.

Suggested shape (use a better one if the code argues for it, and say why):
- Keep the set of words already applied to recordings here in `state.db`'s `meta`. If it's absent,
  seed it with the words in force and re-check nothing: every title with no marker is judged against
  all the words anyway.
- A skipped title is no longer skipped when it starts with a word **not yet applied** (`keep_out_match`
  on just those). It then takes the existing path: `private` through `_add_private`, why "kept out
  late", and the flag "Recording … was kept out by title after it came in. Take it out by hand, or
  Keep it here."
- Mark the new words applied only after a run that left nothing busy or unwritten, so a failed tag is
  tried again. The applied set only grows, so removing and re-adding a word doesn't re-tag what Dan
  has since dealt with.

Pin: the new word tags and flags a recording here at the next listing; a second listing does nothing
more; an already-applied word never re-tags a recording Dan took `private` off (his "Stays removed"
decision) or kept with Keep it here; a busy write is retried at the next run. `Writer.keep_out_words`
failing still raises as it does. A recording Plaud no longer lists isn't checked: say so in the guide.
The check costs no extra Plaud call.

Docs: spec §12.2 and §13.1 (case 3), a Revisions line; the user guide's keep-out section (a word added
later also catches recordings already here, and tags them `private`); CHANGELOG; the CLAUDE.md
Privacy paragraph's exception if it needs the word-added case; `config.example.toml`'s
`keep_out_word` comment if it says otherwise.

## Report

`.superpowers/sdd/2026-10-23-stage-2b-phase-1/reports/morning-answers-report.md`: commits, each test's
failing line before its fix, what changed, anything you couldn't build and why, and anything that
would change one of Dan's decisions beyond Q&A §9 (don't build that; ask in the report). Commit
trailer: BUILD-FAST's.
