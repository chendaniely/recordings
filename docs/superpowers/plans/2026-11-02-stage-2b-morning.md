# Stage 2b: the overnight build, and what it needs from Dan

Dan asked on 2026-11-01 to "wake up with a built product with docs and setup instructions and a
demo", built straight through with no per-task review rounds (Phase 1's Q&A §8). This page is the
morning hand-off: what was built, how to try it, the questions only Dan can answer, the calls that
were made for him, and what is left. Every Plaud detail the spikes would have settled is a guess,
one row each in [`2026-11-01-stage-2b-guesses.md`](2026-11-01-stage-2b-guesses.md).

## Try it

1. **On the Mac, `make demo-live`.** The web app and the Plaud worker run against a fake Plaud
   server, with an invented account and a scratch archive under `$TMPDIR` (it refuses to start if
   any setting points elsewhere). It prints the address and what to type: `hold`, `rename`, `late`,
   `breaker`, `signout`/`signin`, `tick`, and others. Ctrl-C stops everything.
2. **The public Pages demo** shows the same screens with canned data once this branch merges (it
   can't open Release's question: that needs the worker's reply).
3. **Read [`docs/user-guide.md`](../../user-guide.md)**, then **[`docs/setup.md`](../../setup.md)**
   before the first real run: server, dry run first. Nothing in this build has touched Plaud or the
   server.

## What was built

Each area is a few commits on `stage-2b`; its report is in the build's workspace, and its guesses
are rows in the guesses file.

- **Sign-in (A1).** `make plaud-sign-in` and `token-import` copy the vendor CLI's sign-in into
  the app's token store, and the worker refreshes it. A sign-in that stops working shows on Status
  as "sign-in needed" and pauses the sync; nothing else stops.
- **Pacing (A2).** Every call to Plaud goes through one throttle: one call a second, a 10-minute
  budget for a scheduled run, `Retry-After` honoured, and an interrupted download resumed where it
  stopped.
- **The listing (A3).** Each run lists the account, compares it with the archive, and keeps the
  titles of recordings not yet here in `plaud-cache.db` for the Sync screen. A listing that shrinks
  sharply, or whose IDs look changed, is held until Dan presses **Accept this listing** on Status.
- **The import (A4).** A recording comes in with its privacy judged before any of its bytes land.
  The consent list and the keep-out word keep recordings out; a recording that may be one of those
  back under a new ID is flagged for Dan, and **Release** lets one in after a second check. A
  rebuilt `state.db` marks everything for review (`recordings plaud review`).
- **Sync (A5).** The scheduled sync and **Sync now**, **Compare**, **Import**, **Park** and
  **Unpark** run one engine. On the server, `make plaud-dry-run` and `make plaud-catch-up` do the
  first run: a backup, a baseline, the catch-up, a deep validate, a second backup.
- **The screens and the live demo (A6).** The Sync screen and Status read the worker's state. The
  whole flow is tested end to end against the fake server (`make e2e-live`, a CI job), and
  `make demo-live` runs it for a person.
- **Keeping up (B1).** The worker re-checks recordings already here for Plaud's later edits (a new
  speaker name, a regenerated note): a sweep reaches each one within `full_sweep_days`, and a
  recording still being transcribed is checked more often. A removal is held for Dan's review, and
  a breaker stops the checks when Plaud starts changing every recording at once, until he presses
  **Accept these changes**.
- **Re-sync, Park and Sync everything (B2).** **Re-sync** fetches a chosen set again; **Sync
  everything** checks the whole account in a resumable run, monthly by default. A recording Plaud
  stops listing is held, never deleted (`recordings plaud held`, `retire`).
- **Docs and the demos (B3).** The user guide covers every screen and Status line, `docs/setup.md`
  takes the server from a deploy to the first real sync, `CHANGELOG.md` lists the stage, and the
  Pages demo shows the Sync screen with an invented 40-recording account.
- **The privacy review.** Two read-only reviewers checked the branch for any way a consent-listed
  or kept-out recording could leak. They found one High, one Medium and seven Lows; seven fixes
  were built, each test first:
  1. a re-keyed recording's Release asks with no title, and no title is fetched or stored;
  2. no Release preview while a held listing shows Plaud's IDs changed;
  3. a rebuilt `state.db` is found before every run's first listing, not only at the worker's start;
  4. a keep-out word that can't be read refuses every request that lists or imports;
  5. the run's own consent read uses the guarded read;
  6. Sync everything stops checking when a hold lands mid-run;
  7. a backup refuses when the token store sits inside what it backs up.

**How it was checked.** At the branch's last code commit, `make check` passed: pytest 2,537 (again
under `TZ=UTC`), Vitest 465, and no leaks in the history. `make e2e` passed 146, `make e2e-live` 14
and `make pages-test` 10, and CI was green on every push.

## Questions that change what gets built

**Answered 2026-11-09** (Phase 1's Q&A §9). Question 1 went with C, not the A recommended here: the
code showed A would leave the title on the Sync screen. Question 2's allow-list waits for the first
real token refresh. The others went as recommended. The questions as they were asked:

1. **A recording on the consent list comes back under a new ID while the old one is still listed.**
   Today the new ID doesn't match (the match waits for the old ID to vanish), so it would be
   imported.
   - A (recommended): the import skips any ID whose start time and duration are within a second of
     a still-listed consent-listed or kept-out one, until the old ID goes and the match catches it.
   - B: leave it, since Plaud may never list both at once.
   - C: flag it for review, as a match is, so Dan decides with **Release**.
2. **A typo in `config.toml` points `api_base` at another site.** The app would send the sign-in
   there; only the scheme is checked today.
   - A (recommended): allow only `plaud.ai` and its subdomains (and the loopback, for the fake),
     built after the dry run confirms the host, so a wrong guess can't block the first run.
   - B: leave it, since only Dan writes `config.toml` and every container mounts it read-only.
   - C: the allow-list, with a setting to override it.
3. **A title the cache deleted may linger in the file's free pages** until SQLite reuses them.
   - A (recommended): turn on `PRAGMA secure_delete` for `plaud-cache.db`; it's small, so the cost
     is nothing.
   - B: leave it: the file is never backed up and its folder is private.
   - C: vacuum the file after each deletion.
4. **Dan presses Sync now while the catch-up runs from the command line.** The request waits, then
   expires after an hour saying the worker didn't run it, though the worker was up.
   - A: never expire a request that waits on a long run; run it when the run ends.
   - B (recommended): keep the expiry, and say a long run held the sync, so press again (a Sync now
     right after a catch-up has nothing left to do).
   - C: leave it.
5. **Dan adds a keep-out word, and a recording already here has it in its title.** Today nothing
   happens to that recording, and edit-sync keeps fetching it.
   - A (recommended): at the next sync it is tagged `private` and flagged, as a recording renamed
     to the word after it came in already is (Dan's decision, 2026-10-23).
   - B: the word only keeps out recordings not here yet; the guide says so.
   - C: flag it for review without tagging it.
6. **A recording here goes on the consent list, then Plaud re-keys it with different bytes.** The
   re-key match only remembers recordings that were kept out from the start, so the new ID comes
   in as a second, untagged copy (identical bytes are still caught).
   - A (recommended): remember the date, time and duration of consent-listed and kept-out
     recordings that are here too, as §9.5 says.
   - B: leave it, since Plaud may never re-key with different bytes.

## Calls made for Dan (they stand unless he objects)

**Sign-in and failing checks**
- A sign-in with no refresh token is a warning, not refused.
- `token-import` exits 69 when Plaud can't be reached (and records nothing), and 77 only for a
  file it can't use or a sign-in Plaud refuses.
- Status's failing-checks line: one press of Re-sync or Park acts on every recording it counts.
- Small shrinks of the listing add up toward a hold.

**Sync and import**
- Sync now, like a scheduled sync, leaves a recording that failed 3 times alone; **Import all**
  and **Import selected** try it.
- The dry run says "missing: waits for the first catch-up" for recordings the catch-up would
  import.
- Sync now is disabled during a hold, and Re-sync is refused under one.
- Sync everything exits 2 while there are review rows.
- A request pressed during a long command-line run waits in the queue (rather than "busy").
- Sync everything pressed during a hold leaves its run unfinished, to resume when the hold clears,
  rather than failing and waiting a month.
- Every run that lists checks first for a rebuilt `state.db`. Until the first snapshot exists,
  that means a scan of the archive's Plaud sources each run. Before the first catch-up, the
  command line's `sync` answers 75 (busy) when another run holds the sync.

**Release and the keep-out word**
- Release's preview is refused while the title carries the keep-out word.
- A download whose audio matches a consent-listed or kept-out recording here is kept out and
  flagged "rekeyed by audio".
- No Release on a recording still kept out by its title.

**The breaker and checks**
- Accept is offered when the breaker trips; if the same field keeps changing on unsettled
  recordings, it trips again at once and says it needs a code fix.
- A degraded check never reaches the failing list.
- The title marker flips when the listing's and the envelope's titles disagree (it errs private).
- "Share of named segments" stays empty.

**Wording and the demos**
- The arrivals cue reads "1 new · 1 private".
- `make demo-live` runs the catch-up when it starts.
- The demos draw their buttons disabled ("Demo: nothing is synced"), and the Pages demo can't
  open Release's question.

## Left for later

- **Refactors of `recordings_ui/plaud.py`** from A6's phase end: split the views into
  `plaud_views.py`, return fields instead of display text, split the Sync screen around one
  request button, and one module of e2e helpers.
- **A5's council items:** a running row whose end can't be written; a notice when the worker is
  down; the deploy guard building first; wall-clock bounds in the request tests.
- **The demos:** Details' Plaud lines and the Library's flags count don't show yet.
- **Tests:** the 320 px layout fix's test was written after the fix; one scenario (S9, the delay
  note) was skipped.
- **`CLAUDE.md`** doesn't name the `live` test fixture yet.
- **Wording:** the web app's log line when Release's question can't read `state.db` says "Release's
  next sync couldn't be read". It's a log line, not a screen.
