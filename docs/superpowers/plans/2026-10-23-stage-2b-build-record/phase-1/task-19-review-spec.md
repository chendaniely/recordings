# Task 19 review: Spec axis (Tier 2)

Tests: 425 pass, ruff clean. Probes ran on synthetic scenes.

## Ruling on "Built differently" 1
**Matches the spec; stricter than the brief only.** P13: "None ever for a consent-listed recording; for one kept out by title … only inside Release's own question." A recording kept with Keep it here stays kept out by title (`kept_out_at` stays, §12.2). Release doesn't apply to it, so P13 allows its title nowhere here. The brief's rule leaked it once Keep it here closed the flag. Phase 1's `private-unwritten` flags are all owed by consent (worker.py:641). §15.1 item 8 needs only "the recording", and the Library still shows the title. Accept it.

## Important
1. **A corrupt row raises an exception.** `_guarded` (plaud.py:638), `arrivals` and `recording_plaud` don't catch it. Bad JSON in `plaud_state` or in an event's `detail` raises `JSONDecodeError`; a bad `at` raises `TypeError`. Through `status_view`, all of Status fails, backups included (P1). **Fix:** treat `ValueError` and `TypeError` as unreadable. Add a Needs attention line naming the class, and tests.
2. **The summary gives a remedy that can't work** (plaud.py:559). With an `OSError`-caused `private-unwritten` flag, the blocker has no detail (worker.py:651). The summary says "run `recordings reindex`" beside a flag saying "check the archive disk". §15.6 (decision 58): such a flag "says instead" the disk remedy. **Fix:** handle it as the failed late-consent pass is handled. Add a test.

## Minor
- One missing 2b table blanks the Sync screen's flags while Status shows them. A bad column gives "see Needs attention" with nothing there. Rare.
- The summary's kind is `problem` for the gate and for the schedule off before the catch-up, which §11.6 and §15.2 call not a failure. Tasks 21 and 22 must not draw them as errors.
- For Phase 2: the `kept_out_at` title rule should skip released rows.

## Scenarios and docs
C6.06 is proved by `test_the_not_pinged_line_names_the_cause_from_sync_health` (Python side), and C6.14 by `test_the_schedule_off_for_24_hours_after_the_catch_up_needs_attention`. No doc became untrue.

## Security
- **1 Secrets: fine.** No token, URL or `api_base`.
- **4 Failing closed: Important 1;** otherwise fine.
- **6 Logging: fine.** Nothing new logged.
- **P13: fine.** Status has no title (tested). Titles appear only in Updated; arrivals are counts. The canned view is synthetic.

Verdict: changes needed (0 Critical, 2 Important)
