# Task 14 review: Standards (ef08b02..fadbfb9, Tier 1)

The code matches the brief verbatim. 54 tests pass, ruff and `commitmsg.py` are clean. Nothing is Critical or Important.

## Hard violations (Minor)
1. `requests.py:228` `datetime.fromisoformat(row[6])`: decision 47 says stamps are parsed with `when.parse_stamp`. Fix: use `parse_stamp`, and treat None as an unreadable row.
2. `requests.py:117` `_iso` is a second formatter. `astimezone` reads a naive `now` as local time, against decision 47's "naive is UTC" (`checks._utc` gets it right). Fix: move `_utc` into `when.py` and use it in both places. Latent: every clock passed today is aware.
3. `worker.py:4-9`: the module docstring lists the tick's steps but not the request step. preferences.md (Docs): wrong docs are fixed in the change that made them wrong.
4. Plan decision 20 says `requests.py`'s top imports are the standard library "and `recordings.ids`". They are actually `recordings.plaud.when`. Fix the decision's text.
5. `plaud.py:68` `_opaque` accepts 128 characters, but spec §9.3 says "under 128". This is a Spec-axis point.

## Smells (judgement, Minor)
6. **Duplicated Code / Feature Envy:** `_notice` parses the heartbeat again, which `worker._heartbeat` already does. That costs a second connection and handles errors differently. Fix: make a public `worker.last_heartbeat`, which Task 19 needs too.
7. **Duplicated Code:** `NO_CONFIG_REASON` repeats the literal in `app.NO_CONFIG`, and `UNREADABLE` repeats `UNREADABLE_ROW`. Nothing pins them equal. Fix: alias the second; move `NO_CONFIG` into `settings.py`, or pin it with a test.
8. **Duplicated Code:** `submit` builds `Reply(…, "refused", X).to_dict()` four times. Use a `_refused` helper.
9. **Speculative Generality:** nothing reads `Claimed.id` or `Claimed.queued_at`, and no test covers `claim_next`'s unreadable-row branch.
10. **Primitive Obsession:** `claim_next` indexes `row[0]`…`row[6]` by position, while `_reply` unpacks its row into names.

Let pass because the plan endorses them: editing both `HANDLED` and `HANDLERS` each phase (decision 45, pinned by a test), and the module name `requests`.

## Commits
Subjects, the "why" in each body, the failing lines and the `Plan:` trailers are all correct, and Sonnet's `Co-Authored-By` follows the rule for an implementer on another model. Minor: fadbfb9's "Checked: Python's asyncio docs" line is copied from the brief, and the report records no check. The Global Constraint "`asyncio.to_thread` in the pinned Shiny" is still open for Task 20. One body line is 156 characters.

## Outside this axis
`SAVE_WAIT_MS` limits only the wait for the lock. An insert stalled for longer can still add a row after the press was told "couldn't be saved".

Verdict: approve
