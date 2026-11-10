# Final review, Standards axis (stage 2b Phase 1, `8deb039...HEAD`)

`make lint` passes. Items already on the phase-end list are not repeated here.

## Breaches of documented standards

1. **Important: a wrong doc.** `config.example.toml:113` says `token_store … (worker only; `make plaud-sign-in`)`, but this phase has no such target. Task 15's fix (2c9eb46) removed the same pointer from doctor for exactly that reason (`doctor.py:716`, pinned by `test_doctor.py:956`), and the runbook (line 1097) puts it in Phase 2. `.gitignore:37` has the same mistake. Standard broken: "Wrong docs are fixed in the change that made them wrong" (preferences).
2. **Minor.** In `plaud/commands.py:238-245`, `_writer` prints `str(exc)` for `OSError` and `sqlite3.Error`. `cmd_keep_out_words` in the same file (line 339) prints only the class name. Standard: Global Constraints, exit codes: `--json` prints "counts, IDs and fixed phrases".

## Smells (judgement calls, all Minor)

3. **Duplicated Code / Shotgun Surgery.** `recordings_ui/plaud.py:216-268` copies core's vocabulary: the event kinds (`FLAG_EVENTS`, `ARRIVAL_EVENTS`, `TITLE_HIDING_EVENTS`, a literal `"retagged-private"`), `NO_SYNC`, `CONSENT_UNREADABLE`, `CAUSES` (health's `Cause`, `_CAUSES` and `DISK`) and `KEPT_OUT_WHY`. Test pins keep the copies in step. `keepout.py` already imports on Pyodide, so `CONSENT_UNREADABLE` doesn't need to be a copy. Fix: one module with no sqlite3, as `when.py` is for times.
4. **Duplicated Code.** `scripts/plaud_spike.py:106,199-226` re-implements `baseline.enforced_words`: its own SQL, its own `ENFORCED_WORDS` constant equal to `K_KEEP_OUT_WORDS`, and an inline "no such table" check. 2c9eb46 moved doctor onto the shared reader but not the spike.
5. **Duplicated Code.** `_fail` and `_config` are identical in `cli.py:171-183` and `plaud/commands.py:90-102`. The exit codes are named `EXIT_*` in commands.py but are bare numbers in `cli.py`, in `worker.py:795` and in the spike.
6. **Duplicated Code that has drifted.** `flags.UNWRITTEN` (line 89) and `writer.RECONCILE_ERRORS` (line 106) are two lists of the errors one recording's write may raise. The later list adds `TypeError`, `RecursionError` and `ArithmeticError`. In `apply_late_consent`, any of those ends the pass for every later recording, which contradicts its docstring ("stops none after it"). It fails closed: the blocker opens.
7. **Duplicated Code in the frontend.**
   - `type JQuery` and the `shiny:disconnected` effect appear in both `useTagging.tsx:73,309` and `usePlaud.tsx:57,84`.
   - `plaud.available ? null : (plaud.reason ?? NO_PLAUD)` is written in `SyncPage.tsx:90`, `SyncPanel.tsx:22` and `PlaudDetails.tsx:24`, although `usePlaud.why` exists.
   - The flag list with its `kept` map appears in `SyncPage.tsx:142-156` and `PlaudDetails.tsx:58-71`, keyed differently.
8. **Convention drift.** `checks.py` and `requests.py` store times with `utc_iso(now)`. `health.py:115-175`, `worker.py:208-214,563,712,723`, `baseline.py:258` and `privacy.py:89` store `now.isoformat()` instead. Date and time is also formatted in two places (`commands._shown` and the UI's `_stamp`).
9. **Feature Envy.**
   - `worker._check_one` (line 469) walks the archive again to count recordings that `baseline._sweep` has already listed.
   - A recording's Plaud IDs are worked out in six places: `flags._ids_of`, the spike's `_plaud_ids`, `baseline:85`, `worker:469`, `views:255` and `writer:959`.
10. **Feature Envy / layering.** `requests._down` (line 164) and the UI's `_worker` (line 900) import `recordings.worker` just for its heartbeat helpers, and `requests` hand-writes the `meta` table's SQL.

Verdict: changes needed (0 Critical, 1 Important)
