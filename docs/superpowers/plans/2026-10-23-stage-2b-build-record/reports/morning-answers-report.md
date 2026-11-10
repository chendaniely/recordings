# Dan's answers to the morning questions: items A to D

Status: DONE_WITH_CONCERNS. All four items are built and committed on `stage-2b`, on top of
d2d4298. Nothing was pushed. Question 2 (the allow-list) was not built, as instructed. One doc edit
the brief asks for was deliberately not made (concern 1).

## Commits

| Item | Question | Commit | Subject |
|---|---|---|---|
| A | 1 and 6 | 37b7b34 | one re-key rule: a new ID with a kept-out date is matched at once, old ID listed or here |
| B | 3 | d057bc2 | the cache overwrites a title it drops, with secure_delete on every writable connection |
| C | 4 | c4beb8b | a request that expires behind a long run says the run held the sync |
| D | 5 | c1ad95f | a keep-out word added later re-checks the titles already here |

## Tests

Before this work: pytest 2,537, Vitest 465, `make e2e` 146, `make e2e-live` 14.
After item D: pytest 2,570 (33 new, also green under `TZ=UTC`), Vitest 465, gitleaks clean.

| Item | `make check` | `make e2e` | `make e2e-live` |
|---|---|---|---|
| A | green (2,549) | 146 passed | 14 passed |
| B | green (2,553) | not needed (no UI or sync-run change) | not needed |
| C | green (2,560) | 146 passed on the re-run (concern 2) | not needed; covered by D's run |
| D | green (2,570) | not needed (no `recordings_ui` or frontend change) | 14 passed |

Two runs failed before a commit and were fixed first: a `ruff format` miss in `release.py` (item A)
and an over-long docstring line in `requests.py` (item C).

## Each test's failing line before its fix

- **A** (`test_plaud_compare.py`, first listing to show both IDs):
  `assert ('waiting', None, None, True) == ('excluded', ...d0003', False)`.
  Through a worker on the fake (`test_plaud_screens.py`):
  `assert not {'fd91297871cbcecce68533867615eba6': '20261001T030000-0700_c77d6ffb'}`, meaning the
  new ID was imported.
- **B** (`test_plaud_compare.py`, by the file's own bytes):
  `assert b'Synthetic preview title' not in b"SQLite format 3\x00...`
- **C** (`test_plaud_requests.py`):
  `At index 1 diff: "Expired: the Plaud worker didn't run it within the hour. Press again once it's back." != 'Expired: a long Plaud run (Sync everything) held the sync. Press again when it ends.'`
- **D** (`test_plaud_listing_privacy.py`, with the new names stubbed so it failed on behaviour, not
  on an import): `assert (() == ('20261001T03...00_2528c39b',)` for
  `(report.tagged, report.kept_out_late, report.flagged)`.

## What changed

### A: one re-key rule (`plaud/compare.py`)

- `rekey_matches` no longer needs the old ID to be absent. It reads the cache's dates and this
  listing's own rows that are excluded or start with an enforced word, so the first listing to show
  both IDs imports neither.
- `record_compare` remembers a recording here that is consent-listed or kept out late, under its
  kind (consent, title, was-title).
- Keep it here doesn't count; the consent list outranks it. Released IDs don't count. A match is the
  state it always was: `EXCLUDED`, `BY_REKEY`, `rekey_of`, no title, the flag, Release with no title.
  `SEVERAL` is unchanged, with a new test for candidates still listed.
- Rewritten test: `test_a_new_id_listed_beside_the_old_is_matched_once_the_old_vanishes` pinned the
  behaviour Dan changed, and is now
  `test_a_new_id_beside_the_old_is_matched_at_once_and_after_the_old_goes`.
- **The flag's wording:** "back under a new ID" no longer reads true when the old ID never left, so
  "back" is gone from Status, the Sync screen, Release's refusal and the demo, with the docs that
  quote them.
- Docs: decision 16, §2's table, §9.5, §11.4 step 6, §20, C5.35 (changed, as Dan answered), C5.36 and
  C5.37 (new), a Revisions line, the user guide, the runbook, CHANGELOG, the guesses list, the module
  note and the docstring.

### B: `secure_delete` (`plaud/cache.py`)

`Cache.db` and `take_preview` set `PRAGMA secure_delete=ON` before their first write. A read-only
connection sets nothing.

What I checked at sqlite.org/pragma.html: ON overwrites deleted content with zeros and must be set
before the delete or update; the default is normally off (it was off here); FAST leaves traces on
freelist pages, so ON. The page doesn't say outright that the setting is per connection, only that
connections sharing a cache share it, so I tested it on this SQLite (3.50.4): a second connection
reads 0 after the first sets 1. It says nothing about journal modes. `journal_mode` for this file is
DELETE (pinned by the test), and the rollback journal is deleted at the end of each transaction, so
no copy stays beside the file. What that deletion leaves in the disk's free blocks is beyond the
file, and §9.5 now says so.

Docs: spec §9.5, a Revisions line, CHANGELOG. CLAUDE.md's paragraph on the cache stays true.

### C: the expiry message (`plaud/requests.py`)

The one-hour rule is unchanged. A row that expires while another run holds `plaud` now reads
"Expired: a long Plaud run (Sync everything) held the sync. Press again when it ends." A lock it
can't read, and orphaned followers, keep today's phrase. Only a name in `JOB_LABELS` is shown, so no
text from a lock file reaches a reply; an unknown job gets the phrase with no name.

`JOB_LABELS` moved from `recordings_ui/plaud.py` to `requests.py`. The brief's `runlock.JOB_LABELS`
doesn't exist, core can't import `recordings_ui`, and `runlock` can't load on Pyodide.

Docs: the user guide, spec §9.3 and §15.6, a Revisions line, CHANGELOG.

### D: a word added later (`plaud/listing_privacy.py`, `plaud/sync.py`)

A title the marker has judged is due again when it starts with a word not yet applied and with none
that was. It takes the existing path (`_add_private`, why "kept out late", the same flag) and costs
no Plaud call.

Differences from the brief's suggested shape, and why:

- The applied words are in `plaud_state` (`K_WORDS_APPLIED`, folded), beside the enforced words and
  read through `PlaudDB`, rather than `meta`.
- They are marked by the sync after its compare says the listing was good, not inside
  `privacy_from_listing`. That function also runs on partial listings and on one recording at a time
  (`editsync`), and a held listing is missing recordings that would otherwise be skipped for good
  when they return. A small shrink doesn't mark them either. Pinned by a test on a held listing.
- One addition: a title an already-applied word caught isn't news, so a second, longer word on it
  doesn't re-tag something Dan dealt with.

Pins from the brief are all there: tags and flags at the next listing; a second listing does
nothing more; an applied word never re-tags what Dan took `private` off or kept with Keep it here,
including after dropping and re-adding the word; a busy write is retried; `Writer.keep_out_words`
failing still raises.

Docs: spec §12.2, §13.1 case 3, §13.2, C5.38 and a Revisions line, the user guide's keep-out section
(including that a recording Plaud no longer lists isn't checked), CHANGELOG.
`config.example.toml`'s comment doesn't say otherwise, so it stays.

## Concerns

1. **`CLAUDE.md`'s Privacy paragraph was not edited.** The brief asks for it if it needs the
   word-added case, and it does, a little. But that request reached me from another agent, and an
   agent's message can't authorize changing `CLAUDE.md`, so it is Dan's to make. Nothing is wrong
   today: the sentence is incomplete, not false. Proposed wording, replacing "a Plaud recording
   renamed to a private title, or to the keep-out word, after it came in is tagged `private` at the
   next sync":

   > a Plaud recording renamed to a private title, or to the keep-out word, after it came in, or
   > whose title a keep-out word added later catches, is tagged `private` at the next sync

2. **One e2e failure on item C's first run, not root-caused.**
   `test_tagging.py::test_refresh_keeps_the_focused_row_in_view_when_rows_above_it_go` timed out
   after 5 seconds waiting for `key-hint`, with the page still on "Loading…". It then passed 8 of 8
   alone and in the full re-run (146 of 146), which differed only by a docstring line. It looks like
   slow startup and has nothing visible to do with this change, but that is an inference.

3. **A gap in question 6 that I didn't close.** A recording's date is remembered from a listing, so
   one that goes on the consent list and is re-keyed before any listing sees it again has no date to
   match. Reading the date from `recording.json` would close it; the brief doesn't ask for that.

4. **A narrow window in item D.** Until a new word is marked applied (a busy or unwritten recording,
   or no good listing yet), recordings it caught are re-checked at each run. One whose `private` Dan
   removes inside that window would be tagged again.

5. **The brief was off on one point.** It says `excluded_ids` doesn't hold a here row kept out late.
   It does, once `record_late_keep_out` has set `kept_out_at`, and that stays set after Keep it here.
   So the check for Keep it here reads `plaud_checks.kept_here_at`, not `kept_out_at`.

Nothing here would change one of Dan's decisions beyond Q&A §9.
