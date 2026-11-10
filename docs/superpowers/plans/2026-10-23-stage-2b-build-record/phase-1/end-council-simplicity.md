## Council lens 4: simplicity

I used `wc` and `grep` only. Nothing already listed is repeated here.

**1. Important. There is no recipe for adding a request kind or a blocker.**
- **The cost:** a new kind touches nine places:
  - `KINDS`, `HANDLED`, `HANDLERS` and `GATE_ALLOWED`;
  - `check_request`, `KIND_LABELS` and `PlaudKind`;
  - `kindReason`;
  - the pin at `test_plaud_requests.py:135`.
- A blocker touches five: `BLOCKERS`, `BLOCKER_PHRASES`, the worker, `_panel_from` and the runbook's table.
- Phase 2 adds about nine kinds and two blockers.
- **Fix:** two CLAUDE.md recipes, like the tag operation's.
- **Risk:** none.
- **Decision:** Phase 2's start.

**2. Important. The plan carries the code.**
- **The cost:**
  - about 20,900 of its 25,023 lines are listings that the "Built differently" notes disown;
  - 8 of the 60 decisions are placeholders;
  - the source cites "Task N" 36 times.
- **Fix:** step 5 adds one "Carried forward" page: the decisions the code cites (one line each, with the built name), the open rows and the Consumes lines. Phase 2's plan:
  - cites commits instead of carrying listings;
  - drops the placeholders and the "Built differently" notes;
  - has comments keep § and "decision N", but drop "Task N's fix round".
- **Risk:** a rationale is lost if its decision isn't carried.
- **Decision:** fix now (step 5).

**3. Important. Split these before Phase 2 grows them.**
- **`worker.py`:**
  - the heartbeat and liveness code (`Progress` to `_starting_since`, about 180 lines) gets its own module, which also fixes the layering item;
  - the handlers go to `plaud/handlers.py`.
- **`recordings_ui/plaud.py`:** besides the views split, the follow loop (`row_reply` to `plaud_job`, about 110 lines) moves out of `shiny_app.server`'s closure, so it can be tested without a server.
- **`SyncPage.tsx`:** one component per section.
- **`first-run.md`** (1,492 lines, titled "stage 2a"): `first-run.md`, `spikes.md` (section 1c) and `operations.md`, with 14 references to update.
- **Risk:** churn.
- **Decision:** Phase 2's start.

**4. Minor. The flag wording has four forms, and the code picks one by comparing strings.**
- **The forms:** `flags.SENTENCES`, `_panel_from`'s `late`, `PRIVATE_UNWRITTEN_IO`/`_MISSING`, and `BLOCKER_PHRASES["private-unwritten"]` with `UNWRITTEN_STOPPED`. The header also has "kept out too late".
- **The comparisons:** `plaud.py:573`, `:579` and `:999`, and `FlagRow`'s `sentence.includes(recording_id)`.
- **Fix:** `flag_sentence` always names the recording; delete `late` and the comparisons.
- **Risk:** the §15.6 phrases and the pinned tests change.
- **Decision:** Phase 2's start.

**5. Minor. Dead branches.**
- **What:** at `plaud.py:1005–1009`, the consent stop, the flag lines and the disk line also go into `attention` with the same text. The P12 filter at `:1061` therefore always removes them.
- **Fix:** add them to `attention` only.
- **Risk:** none.
- **Decision:** fix now.

**6. Minor. The canned view writes keys `demo_view` overwrites.**
- **What:** `test_plaud_views.py:494` forces `demo/plaud-sync.json` to write `handled`, `not_yet`, `buttons`, `summary` and `gate`, which go stale when `HANDLED` widens.
- **Fix:** omit them, and let the test skip them.
- **Decision:** Phase 2's start.

**7. Minor. Phase 1 carries Phase 2 and 3 weight.**
- **What:**
  - `PlaudViewSettings.full_sweep_days` is never read.
  - About half of `SyncPage.tsx`, and of its tests, draws only the canned view.
- **Fix:** remove the field. Phase 2's plan calls `types.ts` a draft, and replaces each fixture test when its reader lands.
- **Decision:** Phase 2's start.

**8. Minor. The frontend does two things two ways.**
- **What:** `replyText` and `pendingText` format progress differently, and `n` is defined in both `SyncSummary.tsx` and `HeadNotes.tsx`.
- **Decision:** Phase 2's start.

**9. Minor. The spike script has no end.**
- **What:** `scripts/plaud_spike.py` (1,192 lines) and its test (607 lines) serve three sessions, and already drift.
- **Fix:** Phase 3's outline deletes both, and runbook 1c, once session C's results are in. Until then, fix only the guard.
- **Decision:** Later.

**10. Minor. Duplicated tests and CSS.**
- **What:**
  - Five NOT_SAVED tests in `test_plaud_requests_ui.py` could be one table.
  - "Insert off the event loop" is tested twice.
  - Six phone 44 px rules could be one selector list.
  - `.resync-long` styles a Re-sync that Phase 1 never draws.
- **Decision:** Later.

**Wording.** Beyond finding 4, drop `PATTERNS_UNCHECKED`'s "which says what stops it" and `not_pinged`'s "It emails you about 2 hours after that."
