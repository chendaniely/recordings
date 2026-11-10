# Task 15 review: Standards (Tier 1)

Base 236ae13, head 4ef74a6. `test_doctor.py`: 71 passed. A scratch probe (outside the repo) tried two untested cases.

## Hard violations

None. No value, hash, title or token content reaches the report; archive and privacy rules are untouched.

## Findings

**Important**

1. `doctor.py:490-498` (`_consent`, which now runs for `worker`). With `[plaud] consent_list` unset, the worker's report has the problem "the consent list is missing…" and also the note "…the services that stay up don't need it". The note is false for the worker, which stays up and needs the list (the probe confirmed it). Standard: preferences.md, "Wrong docs are fixed in the change that made them wrong". Fix: pass `role` and drop that clause, or the note, for `worker`.

**Minor (judgement calls)**

2. Duplicated Code, `doctor.py:637-655` vs `533-550`: the `from_config` try, the loop that removes the error from problems and warnings, and the `report["alerts"]` dict are all `_alerts`' own, with `deadman_url` swapped out. Extract `_alert_config(...) -> AlertConfig | None`.
3. Duplicated Code, `_enforced_words` (`:611`): it re-implements `plaud.baseline.enforced_words`. Where baseline raises `KeepOutWordsError` on a row that isn't a list, it returns `[]`, so doctor hides a corrupt row. Call baseline's function and report the error.
4. Duplicated Code, `_lines` (`:579`): it is `keepout._join_and` with a prefix. The kept-lines note also says the same thing as `plaud/commands.py:220` in different words. Use one public helper in keepout.
5. Duplicated Code, `PLAUD_MOUNT` (`:70`) and the literal `/plaud/tokens.json` (`:131`): both restate `settings.DEFAULT_TOKEN_STORE`. Derive them from it.
6. Repeated Switches, `doctor()` `:726-734`: three `_forbid` branches on `role` (nine role tests in `doctor()` now), with the `"deadman_sync_url"` literal twice. Use a `{role: names}` table beside the comment that already describes it.
7. `:671`'s "(spec §12.1)" means stage 2b's spec. `:498`'s "(spec §9.1)" means the main spec, whose §12.1 is the Library. Write "stage 2b spec §12.1" (the brief dictated the text).
8. `:645` appends "(RECORDINGS_DEADMAN_SYNC_URL_FILE names its URL's file)" whatever the cause. With a missing ntfy topic file (probed), it points at the wrong variable.
9. Mysterious Name: `test_every_job_secret_is_a_known_secret` (`test_doctor.py:540`) names the removed `JOB_SECRETS`. Also `cli.py:87`'s help lists `ROLES` by hand (Shotgun Surgery when the next role comes).

## Commits

4ef74a6: subject, body (what, why, the failing line) and `Plan:` trailer are fine. `Co-Authored-By: Claude Sonnet 5.5` follows the plan's "names the model that makes the commit".

Verdict: changes needed (0 Critical, 1 Important)
