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

## Calls made for Dan

- **Mode: attended.** Phase PRs wait for Dan's merge, and a change to the spec, one of his decisions
  or a scenario's outcome stops and asks him.
- **2b ships in three phases,** split by the spikes. The plan's "Decisions made while planning" item 1
  gives the reasons.
