## Council lens 1: goal fit, scenarios and Dan's experience

**What I ran.** `pytest` (1961 passed), and a live Phase 1 deploy on a synthetic 2a-style archive with one `of_` consent line: a real worker and web app, seen in Playwright at 1280 and 320 px, plus `doctor --role worker` and `plaud consent`.

### Q1: Dan's Phase 1
- **The worker, idle.** It logs two lines (the gate is closed; 1 tagged, 1 flagged), is healthy, and Status says "last reported 15:46 (idle)". True and calm.
- **The Healthchecks check, never pinged.** Runbook steps 4, 7 (`/log`) and 8 are accurate, and Status shows no not-pinged line before the catch-up. Correct.
- **Late consent.** The recording is tagged and flagged within seconds, in all four places P15 names. `plaud consent` says "(2a's parser missed it)". Good, apart from F2.
- **The Sync screen.** The summary is plain. Copy ID is the only live control, with one reason line for the rest. At 320 px the first screen is three disabled buttons: honest, and fine until Phase 2.
- **Status's panel.** It shows the gate, consent and worker lines, and no sign-in line, which is right for Phase 1.
- **The Library cue.** "1 consent-listed in the archive · See Status". Good.

### Findings
**F1. Important.** `recordings_ui/plaud.py:230,1033`; `plaud/baseline.py:261`; `worker.py:393`.
- Needs attention's "Recording X wasn't checked against the private patterns: run `recordings reindex`" reads `K_PATTERNS_PASS.unwritten`. Only the patterns pass writes that key, and the pass runs only when the worker starts.
- What Dan sees: the reindex he runs lands `private`, and the line stays until the worker restarts. P2 is broken on the privacy path.
- **Fix now** (Q3c's answer fixes it).

**F2. Minor.** `plaud.py:1231`; `PlaudDetails.tsx:58`.
- A late consent listing writes no `retagged-private` event, so Details shows the line meant for an event aged out after 90 days: "Tagged private by the sync", with no date or reason (§15.3). This is the only automatic `private` Phase 1 writes on the real archive.
- The flag below it repeats the recording's own ID, and the Plaud ID again with a second Copy ID.
- **Phase-end list.**

**F3. Minor.** `doctor.py:719`.
- "sign-in needed" is a *problem* (exit 78) for as long as Phase 1 runs. A problem that is always expected teaches Dan to skim past doctor's problems.
- Make it a note until Phase 2's `make plaud-sign-in`.
- **Dan.**

**F4. Minor.** The plan's outlines.
- No outline line carries C5.23 (Phase 3's check marks before `add_snapshot`, or a Re-sync then a restart reads as "rebuilt"), C5.26, C3.06's parked table or C5.17.
- **Step 5:** scenario IDs on each outline line.

**F5. Minor, from before 2b.** `RecordingPane.tsx`.
- At 320 px the Details tab row is 327 px wide, so `main.panes` scrolls sideways (345 against 305).
- **Phase-end list, then Phase 3's 320 px item.**

### Q2: Scenarios
- All 35 Phase 1 rows have tests. I spot-checked C6.06, C6.13 (`tick`, turn included), C9.06 and C5.18: each asserts the outcome.
- Every Phase 2 or 3 row has an outline line, except F4's.

### Q3: Recommendations
- **(a) 320 px top bar: one guard, no redesign.**
  - A 20 px root font and a fallback font both leave no sideways scroll, because the labels are px-sized in 44 px boxes.
  - Phone text zoom remains. `overflow-x: auto; min-width: 0` on the tab row keeps any scroll inside the tabs.
  - Later.
- **(b) "Fails every check · Re-sync · Park": keep it on Status, with both buttons.**
  - Part I's J4 and J5 promise the line, and Task 22 dropped the buttons without Dan's decision.
  - Status is where the email sends Dan, and Park is the only way back to "All here".
  - The field goes into `SyncPanel` at Phase 2's start; the buttons come in Phase 3.
- **(c) Task 13: yes, stop the ping.**
  - An unchecked recording opens the existing `private-unwritten` flag, so no new blocker is needed (decision 10).
  - This errs private (P8), and it is rare: it needs a new pattern and a broken file at once.
  - `still_owed` clears the flag at the tick after the reindex lands, which also fixes F1.
  - Otherwise it sits in the backups and the mirror, known only to a stale line.

### Q4: The spirit of the project
No wording blames Dan. Hidden states and too many words: F1 and F2 only.
