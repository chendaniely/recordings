# Stage 2b, Phase 1: questions and answers

Dan's answers while Phase 1 is planned and built, each with his reason in his words. The spec's own
decisions 1–11 are in its "Dan's decisions" section.

## 1. The spec, approved (2026-10-23)

Dan: "yes approve follow my paln driven development skill on how to commit and review and just start
building".

- The spec is approved as of `1cc4a56`.
- The plan is written and built without waiting for Dan to review it. The council reviews it, then
  re-reviews it, as he asked when the spec work began.
- plan-driven-development's commit formats, review tiers and task closes apply to every task.

## 2. Pushing `stage-2b` (2026-10-23)

Scenario: Phase 1's tasks each end in a close commit on `stage-2b`; the repo is public and CI runs on
push.

- **A. Standing OK for `stage-2b` (Recommended):** every commit is pushed after `make leaks` passes;
  never `main`, never a force push; until Phase 1's PR merges.
- B. Push only at the phase end.
- C. Ask at each close.

Dan chose **A**, the recommended option, with no further reason given.

## 3. A consent-list ID split by a space or a line wrap (asked 2026-10-24)

Found by the plan council (S2). Scenario: a line in the consent list ("never mirror") holds an ID
that a space crept into (`- `0123456789abcdef 0123456789abcdef` — asked`), or an editor wrapped it
over two lines. The spec's typo rule (§12.1, rule 4) looks only at the first word and needs 24 hex
digits there, so these lines read as plain text: nothing is excluded, the list counts as readable,
and from Phase 2 that recording would be downloaded.

- **A. Treat it as a typo (Recommended):** a line whose text before the dash is only hex digits
  (spaces, backticks and `of_` aside), 8 or more of them, and isn't one whole ID stops the list like
  today's typos; Status names the line number. An odd line such as `- 20261023 — asked` also stops
  until it's reworded.
- B. Keep the spec's rule and record the gap: `make consent-add` checks every ID it adds, so a split ID
  only comes from a hand edit.
- C. Join the pieces and read it as the ID.

Dan first asked "i'm confused what is happening here? do we have a format or someting?"; after the
consent list's format and the broken line were shown, he chose **"Stop and tell me"**, option A,
the recommended one, with no further reason given. It is the spec's decision 12 (§12.1 rule 4
widened); Task 3 builds it.

## 4. Decision 12 and an ordinary note line (asked 2026-10-24)

Found by Task 3's review. Scenario: the consent list has a note line such as `- Added 2026-10-23`
or `A bad decade`. Decision 12 as worded counts every character a–f or 0–9 before the dash, spaces
aside, so "Added 2026" (9 such characters) reads as a split ID: every import stops until the line
is reworded.

- **A. Count only an unbroken run (Recommended):** a typo needs one run of 8 or more hex characters
  with no space inside that isn't a whole ID. A split ID always leaves such a run (unless chopped
  into five or more pieces), and `- 20261023 — asked` still stops; `- Added 2026-10-23` doesn't.
- B. Keep decision 12 as worded: such a note line stops the sync until it's reworded; Status names
  the line.
- C. Only a run of 16 or more counts: fewer false stops, but a wrap that leaves a short piece is
  missed.

Dan first asked "what is happening here? this is all about file names?"; after the consent list's
codes and a note line spelled only with a–f letters were shown, he chose **"Solid chunks only"**,
option A, the recommended one, on 2026-10-30, with no further reason given. Decision 12's rule now
counts only an unbroken run of 8 or more hex characters (spec §12.1 rule 4); Task 3 builds it.

## 5. The 320 px top bar under a larger text size (asked 2026-11-01, phase end)

The phase end asked three questions, one at a time: the 320 px top bar; §15.2's "fails every check ·
Re-sync · Park" line on Status; doctor's "sign-in needed" during Phase 1.

Scenario: on a phone 320 px wide, with the text size turned up in the phone's settings, the top bar's
logo, four tabs and theme icon no longer fit on one row. Before asking: Task 21's bar has 12 px to
spare at 320 px, a larger default font or a failed webfont still fits (the council's goal-fit lens
measured both), and only the phone's own text zoom is left; wrapping is what the old two-row bar did,
and it pushed the short-phone Library's selection bar off.

- **A. The tab row scrolls within itself (Recommended):** the rest of the page stays still; a
  one-line guard, nothing changed at normal text size.
- B. The bar wraps onto two rows and gets taller.
- C. Leave it: the whole page scrolls sideways a little if it happens.

Dan chose **"Tabs scroll"**, option A, the recommended one, on 2026-11-01, with no further reason
given. The phase end's step 4 gives the tab row `overflow-x: auto` (and `min-width: 0`) at 320 px,
with an e2e test under a larger root font size.

## 6. Status's "fails every check" line (asked 2026-11-01, phase end)

Scenario (Phase 3): one recording's Plaud copy keeps failing every edit-sync check; the sync's check
stops being pinged, Healthchecks emails Dan, and he opens Status from the email. Before asking: §15.2
and Part I's J4 and J5 promise "1 recording fails every check · Re-sync · Park" on Status; P3 says
"Status reports, the Sync screen acts", which Task 22's plan followed in dropping the buttons
without asking; Status already makes one exception, Accept; Park is the only way back to "All here"
for a recording stuck that way; and `SyncPanel` has no field for the line yet.

- **A. The line with both buttons on Status (Recommended):** as J4 and J5 promise; one more
  exception to P3, beside Accept.
- B. The line on Status as words only, with "Open the Sync screen"; act there or in Details.
- C. No panel line; Needs attention lists the recording by ID, and he acts from Details.

Dan chose **"Line + buttons"**, option A, the recommended one, on 2026-11-01, with no further reason
given. Phase 2's start adds the line's field to `SyncPanel` (the contract), and Phase 3 draws it
with Re-sync and Park when checks run; P3's text gains the exception at that point.

## 7. Doctor's "sign-in needed" while Phase 1 runs (asked 2026-11-01, phase end)

Scenario: Phase 1 deployed, Dan runs `rc exec worker recordings doctor --role worker`; there is no
way to sign in to Plaud until Phase 2, so the worker is idle on purpose. Before asking: doctor is run
by hand, never in the boot or deploy path; it exits 78 with "sign-in needed" for as long as Phase 1
runs, and the runbook tells Dan to expect exactly that one problem; the council's goal-fit lens (G3)
found that an always-expected problem teaches him to skim past doctor's problems. A note means
doctor's code, the runbook's expected lines and CI's compose smoke change; in Phase 2 it becomes a
problem again, once `make plaud-sign-in` exists.

- **A. A note, and exit 0 (Recommended):** "Plaud sign-in: comes with stage 2b's next phase"; a real
  problem stands out. A problem again in Phase 2.
- B. Keep it a problem (exit 78), as the runbook now says to expect.
- C. Say nothing about the sign-in until Phase 2.

Dan chose **"A note, exit 0"**, option A, the recommended one, on 2026-11-01, with no further reason
given. The phase end's step 4 makes it a note (doctor, its test, the runbook's section 6 and the
compose smoke's expected problems), and Phase 2's outline turns it back into a problem with
`make plaud-sign-in`.

## 8. Building the rest of stage 2b straight through (2026-11-01)

Dan, during the phase end: "can we keep going and just have you one-shot the build? we can then just
have me go through the app to test it out. and you make a docs page about how to use it and its
features. and we can go and change from there." and "i rather have something working we can build on
later than try to have something perfect the first time". Then: "if you can have me answer all the
questions you need in the beginning let's just get those out of the way. otherwise i'd like to go to
bed and then wake up with a built product with docs and setup instructions and a demo".

So Phases 2 and 3 are built on `stage-2b` without per-phase plans or per-task review rounds; the spike
sessions are skipped (audio-router's code gives Plaud's shapes, and Dan's testing replaces the
spikes); the safety rules stay; one privacy review at the end; one PR. Where the spec leaves
something open, the build takes the most private, fail-closed reading and lists each guess for Dan.
Two questions were asked first:

**The demo.** Scenario: Dan wakes up and wants to try stage 2b before touching his real Plaud
account. Options: A (recommended), a live local demo (`make demo-live`: the app and the worker on the
Mac against the fake Plaud with synthetic recordings), and the public Pages demo with stage 2b's
screens, canned; B, the canned demo only; C, the live demo only. Dan answered, in his words: "should
have live and github actions github pages demo": both, the live local demo and the Pages demo that
GitHub Actions builds and deploys.

**The first real run.** Scenario: after the demo, the real Plaud account and the real archive.
Options: A (recommended), on the homelab server by the runbook, deploy, `make plaud-sign-in` from the
Mac, then the first catch-up's dry run, which imports nothing until run for real; B, on the Mac
against a scratch copy of the archive; C, straight to the server with no dry-run step. Dan chose
**"Server, dry run first"**, option A. The setup instructions take him there.

## 9. The six questions after the overnight build (asked and answered 2026-11-09)

The morning hand-off (`2026-11-02-stage-2b-morning.md`) listed six questions. Dan asked what the
answers should be, and to "think about the goal, the tradeoffs and which solution can address the
goal the best". The goal they were judged against: recordings arrive without babysitting, and one
kept out never gets in. Each was read against the code, then Dan answered, in his words: "ok let's
go with your decisions".

1. **A consent-listed or kept-out recording appears under a new ID while the old ID is still
   listed.** Flag it straight away, as a re-keyed match is: kept out, no title stored, Dan confirms
   or presses **Release**. This replaces the morning doc's recommendation (skip the import until the
   old ID goes), which the code showed would leave its title on the Sync screen, saying "imports at
   the next sync", for as long as Plaud kept both. It widens decision 16, which covered only an old
   ID that has vanished.
2. **An allow-list for where the sign-in may go.** Yes, `plaud.ai` and its subdomains plus the
   loopback, with no override setting, but built only after the first real token refresh has
   worked. The API address comes from audio-router; the refresh address is a guess, and an
   allow-list built now could break the sign-in. It is the least valuable of the six.
3. **A title the cache deleted may linger in the file's free pages.** Turn on `secure_delete` for
   `plaud-cache.db`.
4. **A request expires behind a long command-line run.** Keep the one-hour expiry; say that a long
   run held the sync, not that the worker is away.
5. **A new keep-out word matches a recording already here.** At the next sync it is tagged `private`
   and flagged, as a recording renamed to the word already is, and as a new private pattern already
   re-checks recordings here.
6. **A recording here goes on the consent list, then returns under a new ID with different audio.**
   Remember its date, time and duration, as §9.5 says. With question 1 this is one rule: a new ID
   with the start time and duration of anything consent-listed or kept out is treated as that
   recording until Dan confirms, whether the old ID has vanished, is still listed or is already here.

**`CLAUDE.md`'s Privacy paragraph (asked 2026-11-09, after question 5 was built).** Scenario: the
paragraph lists the cases where a recording gets tagged `private` after it came in, and doesn't name
a keep-out word added later. The sentence is incomplete rather than wrong, and only Dan can approve
a change to that file. Options: A (recommended), add the case; B, leave it. Dan chose **A** ("yes
A"), with no further reason given.

## Calls made for Dan

- **Mode: attended.** Phase PRs wait for Dan's merge, and a change to the spec, one of his decisions
  or a scenario's outcome stops and asks him.
- **2b ships in three phases,** split by the spikes. The plan's "Decisions made while planning" item 1
  gives the reasons.
