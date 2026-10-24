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

## Calls made for Dan

- **Mode: attended.** Phase PRs wait for Dan's merge, and a change to the spec, one of his decisions
  or a scenario's outcome stops and asks him.
- **2b ships in three phases,** split by the spikes. The plan's "Decisions made while planning" item 1
  gives the reasons.
