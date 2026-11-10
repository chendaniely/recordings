# Area B3: the guide, the setup page, and the Pages demo: report

Status: **DONE_WITH_CONCERNS** (everything in B3's scope is built, tested and committed; the
concerns are a few readings for Dan to glance at, below, none reachable half-built).

Branch `stage-2b`, from `694b6a9`. Not pushed (the controller pushes).

## Commits

| SHA | Subject |
|---|---|
| `0c7dafb` | fix(demo): 🤖 make demo-archive refuses without the media cache, leaving demo/archive alone |
| `aa6e45d` | feat(demo): 🤖 the Pages demo shows stage 2b's screens, with invented data |
| `33faecb` | docs: 🤖 the user guide finished, the setup page, the changelog, and the spec's status |

Every message passed `scripts/commitmsg.py` and carries BUILD-FAST's trailer.

## What was written

### `docs/user-guide.md`, finished (one coherent guide)

Rewritten from the pieces A1 to B2 added, with stage 1, 2a and tagging part A added:
- **Start here:** what the app is, where to open it, the first time with Plaud (→ setup page), a
  normal day, what never happens; a contents list.
- **Trying it first:** the public Pages demo (one paragraph), `make demo`, and `make demo-live`
  with its command table (checked against `scripts/demo_live.py`'s `command` and `--help`: `new`,
  `hold`, `back`, `rename`, `late`, `signout`/`signin`, `tick`, `breaker`, `speakers`,
  `fail`/`mend`, `catch-up`, `dump`, `help`, `quit`; `--before-catch-up`, `--keep`, `PORT`).
- **The screens:** the top bar; the Library (sidebar filters, rows, the header with Refresh's
  counts and the arrivals cue, the privacy-flags count, "Tagging is off", the keys, the selection
  bar); a recording (header, player, the five tabs, Details' Plaud lines); the Tags page; the Sync
  screen top to bottom with every button and its replies; Status (the running job, Needs
  attention, the Plaud sync panel, Backups, Disk).
- **The tasks:** tagging (selection, the picker's keys, drag, ×, Undo, the private confirmation,
  the first-real-tag rule), signing in, the first catch-up (→ setup page), how a recording comes
  in (merged with the scheduled sync), keeping one out and Release, a late rename, the checks,
  Re-sync, failing every check, Sync everything, held removals, a held sync, the breaker, Plaud
  down, after a rebuild, taking a recording out (→ runbook).
- **Reference:** **every line Status can show**, as two tables (the Plaud sync's 32 rows; the
  backups' and disk's 8), each with what it means and what to do; the equations; the exit codes.

Every quoted phrase was checked against the code that writes it (`recordings_ui/plaud.py`'s views,
`ops.status_summary`, `disk`, `plaud/hold.py`, `breaker.py`, `flags.py`, `sync.py`'s reply lines,
`commands.py`'s dry-run lines, `gate.py`, `doctor.py`, the Makefile's messages, and the React
components' fixed text). Linked from the README's new "Using it" list, the runbook's intro and
CLAUDE.md.

### `docs/setup.md`, new (Dan's answer: "Server, dry run first")

Before you start (2a's sections 0 to 5 done; the spikes skipped and the guesses file; try
`make demo-live` first; no tagging yet), a five-row table of where each step runs and what it
does to the archive, then one section per step: 1 deploy (runbook §6 steps 1 to 5), 2 `make
plaud-sign-in` (then §6 steps 7 and 8's checks), 3 the consent list, the keep-out word and the
private patterns, 4 `make plaud-dry-run` with a table for reading every line it prints, 5 the
volatility check and `make plaud-catch-up`; then "After the first sync". Each step says
**what it does to the archive** (the dry run: nothing at all) and **where to stop and ask**. It
links the runbook's sections rather than copying their commands.

### The Pages demo (`demo/plaud-sync.json`, hand-written)

One invented account of 40 recordings, so a hold can be shown (the many-gone kind, which hides no
titles): the Sync screen's flags (a late keep-out with Keep it here, a re-keyed match), the hold
with Accept this listing, the breaker (`share_state`) with Accept these changes, Not here yet
(held rows, one arriving private, "failed 3 times" with Park, a review row with Show title, a
parked Plaud ID), Kept out (consent, title, re-keyed and "was kept out by title" with Release, and
a released row with Undo), Updated (speakers and "removal held", a title, re-tagged private), Gone,
Parked (a Plaud ID and a recording); Status's failing-checks line, not pinged, three conditions,
the hold and the breaker, every number, three runs; and the arrivals cue. Plaud IDs are small
counters; titles are invented except the demo archive's own, named by their IDs.

**`demo_view` changed:** it no longer forces a row's Release, Park and Keep it here, or a hold's
and the breaker's Accept, to false. The screens now draw them, each disabled with "Demo: nothing is
synced" (the view is unavailable with that reason, so the UI disables every request button), which
is what §15.5's "buttons disabled" says; before, they were left out, so the public demo couldn't
show Release at all. The demo's server still refuses any request (`test_demo_guard`, unchanged).

**Release's preview:** the question itself ("Let '…' in?") is a request's reply, which the demo
refuses by design (§15.5), so the public demo shows Release's button (off) and a released row, not
the question. `make demo-live`'s `rename` shows the question.

**Found by it:** on a phone the Library's header grew from 45 to 69 px once Refresh carried the
arrivals cue ("5 from Plaud · Refresh"), failing `make e2e`'s 320 px test; in the real app it would
squeeze the list below its 92 px floor on a short phone. The phone block of `app.css` now hides
"· newest first" (`.list-order`), so the count and Refresh stay one line. The canned privacy-flags
count stays 0: its "See Status" link lands between Refresh and the Tags grid in Tab order, which
two grid tests assume isn't there (the late keep-out shows on the Sync screen and Status instead).

### The footgun (`make demo-archive`)

`demo/build.py` checks, before removing anything, that every media file `demo/sources.toml` names
is in the media folder; otherwise it names the missing files, says to run `uv run python
demo/fetch.py` first, says `demo/archive` is left as it was, and exits 1. Tested first (both tests
failed with `FileNotFoundError` after the `rmtree`); `make demo-archive` here, with no cache, now
refuses and `git status` shows `demo/` untouched.

### `CHANGELOG.md`, new

Stage 2b's entry (2026-11-02): what Dan can now do (nine plain bullets), then the notable changes
(the worker, make targets, `recordings plaud` commands, settings, doctor, validate, the phone
header, the docs), and a line for the stages before.

### The spec's status line

"all three phases built on branch `stage-2b` … not yet merged or deployed · the spike sessions were
skipped, so Plaud's API details … are the build's guesses, each recorded in
`docs/superpowers/plans/2026-11-01-stage-2b-guesses.md` … · setting up: `docs/setup.md`; using it:
`docs/user-guide.md`", and a Revisions line for B3.

## Tests

- New: `test_demo_archive.py` (2: no cache or an empty one refuses and leaves a copy of the demo
  archive byte for byte; a partial cache names exactly the missing files).
- Changed: `test_plaud_views.py`'s demo test (the rows' buttons pinned as written, and each one
  offered); `test_build_pages.py`'s Pyodide probe (5 rows); `make e2e`'s canned-view test (the new
  rows, the hold, the breaker, the released row, every new button disabled, the arrivals cue at
  1,280 and 320 px); one tagging e2e test reads Refresh's marks gone rather than its whole text
  (the canned cue stays after Refresh); `make pages-test` (the hold, the breaker, the released row,
  Parked and the cue on Pyodide); `test_runbook.py`'s checkout pin.
- Last runs, on the tree of the last two commits: `make check` green (pytest 2,496 passed twice,
  local and `TZ=UTC`; Vitest 462; ruff clean; gitleaks clean); `make e2e` 146 passed; `make pages
  pages-test` 10 passed. Before `0c7dafb`: its two new tests failed first, then pytest 2,496,
  Vitest 462, ruff and gitleaks green (that run's `TZ=UTC` pass failed one Pages-build test only
  because the canned view was being edited in the working tree meanwhile; fixed in `aa6e45d`).
  The one warning is the existing `test_demo_guard.py` fork warning.
- The first `make e2e` with the new canned view failed three tests: two Tags-grid Tab-order tests
  (the privacy-flags "See Status" link, now left out of the canned view) and the 320 px Library
  header (69 px: the CSS fix). All three pass now, in the full run above.
- Not run: `make e2e-live` (nothing it drives changed: the real views are untouched; only
  `demo_view` and the phone header's CSS changed, and `make e2e` covers both).

## Docs found wrong, and fixed

- The runbook's section 6 checked out "Phase 1's merge commit"; 2b ships as one pull request, so
  "stage 2b's merge commit" (and `test_runbook.py`'s pin).
- Runbook 1c read as if the spike sessions still came before 2b; it now says they were skipped
  (Q&A §8), where the guesses are, and why the sessions stay.
- The guide quoted Details' private line without its date ("Tagged private by the sync: its Plaud
  title changed"; it is "… on 2026-11-02: …"), and listed only some of its reasons.
- The guide's flow repeated the scheduled sync and "how a recording comes in" as two sections,
  and the Sync screen's buttons and its top-to-bottom as two; each pair is now one.
- README and the spec's status still said Phase 1 was in progress / Phases 2 and 3 planned.

## Questions for Dan

1. **The demo's row buttons:** Release, Park, Keep it here and both Accepts are now drawn in the
   demos, off with "Demo: nothing is synced", where Phase 1 left them out. Keep, or hide them again?
2. **The canned world** is a 40-recording invented account, bigger than the 4-recording demo
   archive, so a hold can show; under the hold every new row reads "held: see Status", so "imports
   at the next sync" doesn't appear. Keep the hold, or a calmer canned state with "imports at the
   next sync" and no hold?
3. **The phone header** drops "· newest first" at 760 px and under. Fine, or shorten something
   else?
4. **The setup page** assumes the server already ran 2a's first run (runbook sections 0 to 5). If
   it hasn't, it sends you to section 0 first. Is that where you are?
5. Carried from the earlier areas, unchanged: decision 16's gap; the questions in A6's, B1's and
   B2's reports.

## What's left

- **Details' Plaud lines in the demos:** `recording_plaud` returns None there, so neither demo
  shows Details' "Plaud: checked …" or its Re-sync (B2 noted it); the canned view has no slot for
  a recording's Details.
- **The Library's privacy-flags count in the canned view** stays 0 (the grid's Tab-order tests);
  showing it means teaching those tests about the link.
- **Release's question** can't be shown on Pages (above).
- A6's phase-end refactors of `recordings_ui/plaud.py` and A5's council items: not B3's, still open.
