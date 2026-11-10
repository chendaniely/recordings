# Task 20 review: Spec axis (Tier 2), 7984119..ee6cf9e

pytest ui 441 passed, `make test-js` 391 passed; probes used the tests' fakes, in scratch.

## Important

**I1. Running and expired replies carry the insert's message.** `claim_next` (requests.py:261) and `expire` (:336) change only `state`, and `follow` (shiny_app.py:134) sends the row unchanged. Probe: a running reply says "The Plaud worker isn't running…". An expired final reply says "Queued" or "…runs when it's back", and usePlaud.tsx:86 makes it a sticky toast. Fix: write a phrase on claim and on expiry. §15.6 has neither, so the controller picks them and records the gap. Add a session test for each.

**I2. A worker that stops after the press leaves the reply at "Queued" until the tab closes** (probe). §9.3 says the screen then says "The Plaud worker isn't running…", but `_notice` runs only at the insert. Only the worker expires rows. Fix: on each poll of a queued row, check the heartbeat (read-only) and send that sentence.

**I3. One failed read ends `follow`** (shiny_app.py:134). Probe: after one `OperationalError`, no final reply, toast or `plaud_ended` redraw comes, and the request stays queued. Fix: catch `sqlite3.Error` and `OSError` on each poll, log the class, and keep polling.

## Minor
- usePlaud ignores `shiny:disconnected`, so a press after the connection drops gets no answer (P6).
- A double press inserts two rows, and the second's "Nothing to keep…" becomes `last_result`. Nothing tells Task 21 that a request is pending before its first reply, so it can't disable the button.
- `follow_unsaved` also runs when there's no state.db, and logs "the task failed".
- No test covers decision 22's `since` or the `plaud_ended` redraws. Vitest leaves out `failed`, `expired` and "Queued after…" (§21).
- shiny_app.py:129 and :154 say `requests` "needs sqlite3", which is untrue.

## Security checklist
1. **Secrets: fine.** Replies carry IDs, fixed phrases and counts; logs carry class names only.
4. **Failing closed: fine.** A kind not handled yet, or no writable state, is refused and writes 0 rows (tested). The demo's reason comes before NOT_YET. Reads use `mode=rw` and create nothing. No title travels: extra keys are refused, and Vitest pins the five keys sent. Nothing sends a request on load, from a URL or on reconnect.
6. **Logging: fine.** One new line, with the class only.

Scenarios: none. In the demo, a request is refused before any insert.

Verdict: changes needed (0 Critical, 3 Important)
