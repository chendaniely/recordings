# Task 5 report: the spike script and its guard

**Status:** DONE. One commit, a642251, "feat(spike): the Plaud spike script, whose guard refuses consent-listed, private and kept-out recordings and counts them". It has the `Plan:` trailer and the Sonnet 5.5 Co-Authored-By line. The base is in `task-5-base`.

## What I implemented
- `scripts/plaud_spike.py`, `packages/core/tests/test_plaud_spike.py` and the runbook's section 1c are all taken verbatim from the brief. I extracted them mechanically from the brief's text, so the `\u` escapes and the `§` characters are intact.
- Only the commit message differs: its trailer says Sonnet 5.5, as instructed.

## TDD evidence
- RED: `uv run pytest packages/core/tests/test_plaud_spike.py -q` printed `24 errors in 0.29s`. The first error was `FileNotFoundError: [Errno 2] No such file or directory: '.../scripts/plaud_spike.py'`, as the brief predicts.
- GREEN: after adding the script, the same command printed `24 passed in 7.65s`.
- Runbook: `uv run pytest packages/core/tests/test_runbook.py -q` printed `11 passed`.
- `uv run ruff format .` printed `124 files left unchanged`.
- `make check` passed: 1548 passed, 141 deselected, gitleaks found no leaks (350 commits scanned before this one). Nothing flaky showed up.

## Adaptation to Task 4 as built
None was needed.
- The script never uses `short=` or `on_short_body`.
- S11 relies on `download()` refusing a response with no `Content-Length`, and it prints "Content-Length: present" for that reason.
- No `no_plaud_network` fixture is used. The tests run against the `plaud_server` fixture on 127.0.0.1.
- The script imports `API_BASE` and `check_api_base` from `recordings.plaud.client`, and that import works. The names are defined in `plaud/endpoint.py` and `client.py` imports them.

## Files changed
- `<the stage-2b worktree>/scripts/plaud_spike.py` (new)
- `<the stage-2b worktree>/packages/core/tests/test_plaud_spike.py` (new)
- `<the stage-2b worktree>/docs/runbooks/first-run.md` (section 1c replaced)

## Self-review
- The diff matches the brief. The guard order is consent, private, no title, keep-out (skipped for the own clip), then pattern.
- A title that can't be checked is refused.
- No Plaud host was looked up and no real Plaud call was made. Every request in the tests went to the fake server. S2's refresh POST is replaced in its test.
- No personal paths, tokens or real IDs appear in the files. The runbook uses `$HOME`, `$REPO_DIR` and `$RECORDINGS_HOME` placeholders.

## Concerns
- Tests passed on the first run with the brief's code, so no test needed a fix.
- Process note: my first `make check` wait used a watcher that matched its own command line, so I had to watch the PID instead. This had no effect on the result.

## Fix round (Tier 1 review), commit 2aecd27

All twelve rulings done, test-first: 10 new tests failed first (failing lines are quoted in the commit body), then 34 passed; `make check` passed (1558 pytest, 378 Vitest, gitleaks clean).

- 1: S7's ID-form step uses the first row whose envelope title the guard passed, and fetches nothing when none did.
- 2: `--own-clip` needs a row outside the archive and listed within 24 hours (`_own_clip`; for a clip that left the listing, the `recorded_at` the last run kept). The stale comment is gone.
- 3: `_part_label` / `_labels` are shared by S8 and S9. The S9 test checks `out.lower()`.
- 4: `_post_form` sends `client.USER_AGENT`. No status prints "unreachable". Every refusal prints "compare ... first". "stop and ask Dan" is now only in the runbook's matched case.
- 5: `_same_archive` runs in S7 (after its counts), S8, S9 (with `--id`) and S11. The runbook stops session B when S7's "in the archive" count is 0.
- 6: S11 reports Content-Length from the Range probe's header names.
- 7: new `SpikeStopped` (exit 1). `--id` is checked in `main()` through `NEEDS_ID`. Runbook 1c says only "refused before any call" means nothing was sent.
- 8: S8 and S9 run `_checked` before each envelope fetch.
- 9: an unparsable or non-list `keep_out_words` row is a refusal before any call.
- 10: runbook 1c edits as ruled: `[backup]` in the session config, session C's own setup and sign-in, the `PlaudAuthError` procedure, the temporary homes' paths written down, and the long lines wrapped.
- 11: `_gate`, `_refresh_start`, `_paged`, the `Reason` Literal with a "no title" reason.
- 12: the docstring is narrowed to say a `ConfigError`'s text may print.

Interpretations to check:
- Ruling 5 and ruling 2's "SpikeRefused" became `SpikeStopped` (exit 1), because the listing has already been read when they fire and ruling 7 reserves 78 for before any call. The existing own-clip-in-archive test now expects 1.
- Existing tests adapted:
  - Two assertions: the guard's no-title count, and S2's second refusal wording.
  - S9's clip test: a recent clip, and a listed recording the archive shares.
  - `_serve_account` takes a title function for the S8 test.
- Standards' smells not in the rulings were left alone: `Guard.here` naming and the `_names` / `env` names.

## Fix round 2 (re-review Minors), commit eff76f2

5 tests failed first (failing lines are in the commit body), then 48 passed (spike and runbook tests). `make check` passed (1561 pytest, 378 Vitest, gitleaks clean).
- A: new `_follow` follows the links of the envelope `_checked` passed, on that same dict, using `client.fetch_link`. S8 and S9 no longer fetch files/{id} again. The S8 test now asserts 2 files/{id} requests (one per check), and the existing private-on-first-fetch test still sees no block request. The S9 note-mask test's fetch count changed to match.
- B: `_clip`'s two-names stop is `SpikeStopped` (exit 1).
- C: S11 prints "Content-Length: unknown" unless the Range probe got 200 or 206.
- D: runbook 1c says "Exit 1 means it stopped; calls may have been made".
- 5: S10 sends `client.USER_AGENT`.
- 6: the docstring says `ConfigError`'s text names keys and paths, never values.
