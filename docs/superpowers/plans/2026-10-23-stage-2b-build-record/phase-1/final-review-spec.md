# Final review, Spec axis: stage 2b Phase 1 (8deb039...HEAD)

Ran: pytest 1954 passed; Vitest 455 passed; `make build` then `e2e/test_sync.py` 8 passed.

**Coverage.** All 35 Phase 1 Scenario rows (C5.04 to C11.01) map to their named test files, and each test exists and passes. C8.03 and C10.02 are proven only in CI (`compose_smoke.py`).

**The UI story.** It holds together before the catch-up, with the worker down, for a late keep-out, in a consent stop and in the demo. Unreadable state is the one exception (finding 5).

**Privacy trace.** It is clean from the worker to every view. A consent-listed recording and one kept out by title reach `state.db` as IDs, hashes and fixed phrases only. Logs carry counts and class names, and replies carry IDs. Views name these recordings by ID. Updated hides their titles through `_untitled` and `kept_out_at`. `reindex --allow-missing` drops their flags once a recording is taken out by hand.

## (a) Missing or partial
1. **Minor.** §13.1 case 5: "Status: 'patterns changed: 1,234 titles checked, 6 tagged private'".
   - `PatternsPass.sentence()` is only logged (`worker.py:449`).
   - Status shows only the pass's unwritten IDs.
2. **Minor, needs a ruling.** §15.2 and Part I J4: "'1 recording fails every check · Re-sync · Park'".
   - Plan Task 22 drops Status's Re-sync and Park.
   - `SyncPanel` in `types.ts`, the contract for Phases 2 and 3, has no field for this line.
   - The canned demo shows "Failing 1" with no sentence beside it.
   - No Dan decision or Phase 3 outline line covers it.
3. **Minor.** Every Phase 1 row in the Scenarios table still says "planned", though each is built and its test passes.

## (b) Scope creep
- **None of substance.**
  - `0de8bf2` changes tagging part A's picker: an Esc while the confirmation is open now acts as Cancel. That is outside 2b's spec.
  - It came from the phase-end list's flaky-test item, and `docs/upstream-issues.md` records it.

## (c) Implemented, but looks wrong
4. **Minor.** §15.2: "Scheduled sync is off since …" should be "Plain before the catch-up".
   - `summary_view` gives the schedule-off summary kind "problem": `held_by_gate` needs `schedule_minutes > 0`.
   - Both screens therefore draw it in `--destructive` before the catch-up.
5. **Minor.** P15 puts late flags on "the Library's header".
   - With `state.db` unreadable, `arrivals` returns zeros and `recording_plaud` returns the bare lines.
   - The header's "1 kept out too late" and Details' flags then vanish without a word.
   - Meanwhile the Sync screen and Status both say the records can't be read now.
6. **Minor.** `config.example.toml`'s `token_store` comment names `make plaud-sign-in`.
   - Phase 1 has no such target.
   - Doctor's wording was changed for exactly this reason in Task 15.

**Docs.** CLAUDE.md, the README, the runbook's section 6 and alert tables, FORMAT.md (the source and the demo copy are identical) and `upstream-issues.md` are true, apart from finding 6. The §19 items that are still missing are all in Phase 2's outline:
- the `tokens.json` rule;
- the scratch-download sentence;
- the README's 2b paragraph;
- `e2e-live`.

Verdict: approve
