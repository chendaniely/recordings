# Task 23 review: the Spec axis (Tier 2)

What I checked: spec §15.3, §14.6, P2, P6, P13, P15 and J7, plan decisions 22 and 60. `make test-js`: 452 pass. `make build`: `ui.js` unchanged. The demo at 320 px, in Playwright: on load the page sent only the null mounts of `plaud_request` and `refresh`. The selection bar has no Re-sync. Details on a Plaud-sourced demo recording has no Plaud section. With a cue and a flags line put in by hand, the header wraps and doesn't scroll sideways.

## Important

1. **Keep it here in Details shows nothing while its request is pending.** `PlaudDetails.tsx:67-69` drops the request ID, and `usePlaud.tsx:106` toasts only final replies. So "Queued" never shows. With the worker down, its reply "The Plaud worker isn't running … within the hour" (state `queued`) shows nowhere, for up to an hour. This breaks P6 and §15.1's "Every press gets an answer within a second", which §15.3's flags inherit. Fix: keep the keep-here request ID, as Re-sync does, and show `ctx.results[id]` in the row with `role="status"`. Test it pending, and with the worker-down message.
2. **The header's flags line is wrong after Keep it here.** `shiny_app.py:264` (`plaud_arrivals`) doesn't read `plaud_ended()`, but `recording` does (`:244`). After a Keep it here, Details drops the flag and a toast says "Kept here", but the header still reads "1 kept out too late · See Status" for up to 60 s. That breaks P2 ("true now or absent"), and Status already redraws when a request ends (§15.2). Fix: read `plaud_ended()` in `plaud_arrivals`, and add a session test.

## Minor

- C9.05 is only partly proven. A test shows the bar calls `onResync` once and shows the progress and end lines. Nothing tests the request's IDs (`RecordingList.tsx:360`, `TagsPage.tsx:602`). C9.06 is proven: the counts and the phone form here, and the reset on Refresh by `test_plaud_session.py:299`.
- At 320 px "See Status" is a 59×16 px target and Refresh is 26 px tall. Both are under J7's 44 px; the link is inline and Refresh was already that size.
- §15.3's "the Library's Needs attention" is read as a link to Status. The brief and the commit record this, for Dan to confirm.
- `PlaudDetails` could take `rec.id` from `DetailsTab` instead of reading `recording` again. `n` duplicates `SyncSummary`'s.

## Security checklist

1. **Secrets:** fine. Frontend only: no token, URL or secret in the code, tests or commit.
4. **Failing closed:** fine. No title is shown. Keep it here goes only to a flag that has a recording ID, and the worker refuses one with no open flag (`flags.keep_here`). An unreadable `state.db` gives zero counts, so the line doesn't show, and Status says it can't be read.
6. **Logging:** not applicable. No log or console call was added.
13. **P13:** fine. `flagsLine` reads counts only (`Arrivals` has no other fields). Details draws only `RecordingPlaud`'s IDs, dates and fixed sentences, and `FlagRow` (ID, sentence, Plaud ID). No title.

**Scope:** Re-sync's `aria-describedby`, Details' 44 px buttons and the new flag key are improvements within the brief.

Verdict: changes needed (0 Critical, 2 Important)
