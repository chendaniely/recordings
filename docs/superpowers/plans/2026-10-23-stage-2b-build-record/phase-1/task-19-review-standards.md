# Task 19 review: Standards axis (Tier 2), 5f1afbd..fdcd848

**What I checked:** `uv run pytest packages/ui/tests -q` gave 425 passed. ruff check and format are clean, and the tree is clean. `demo/plaud-sync.json` and `types.ts` are identical to the brief's. `www/ui.js` is untouched. No logging was added, and no title reaches Status. No client, token or sync module is imported. Nothing at the top of `plaud.py` loads sqlite3. The commit has the subject format, a body that says why with the lines that failed first, and the `Plan:` trailer. Departure 1 (`_untitled`) is stricter than the brief and fits P13.

**Critical:** none. **Important:** none.

**Minor** (each is a judgement call unless marked):
1. **Privacy filter tested only in combination** (`plaud.py:701-740`). `kept_out_late` plus `keep_here` sets all four hiding paths at once: the resolved flag event, `kept-here`, the `why` in `KEPT_OUT_WHY`, and the listing's `kept_out_at`. Dropping any single path fails no test. Add one test for each path.
2. **Some state.db reads happen twice in one view.** `_worker` reads the heartbeat at line 862, then `alive()` reads it again at 880; use `parse_heartbeat` and `beat_alive`. `_Read.__init__` reads three values again that `sync_health` has already read: `K_CATCH_UP_DONE` (through `gate_now`), `K_LAST_PING`, and the `private-unwritten` blocker. Every `get` opens its own connection, so the view isn't one snapshot. `listing()` and `_body()` run once per row. `demo_view` re-parses the file on each call.
3. **Repeated Switches.** `sync_view`, `sync_panel`, `arrivals` and `recording_plaud` each repeat the same opening: the demo check, `_open`, the None check and the guard. `recording_plaud` also re-implements `_guarded` at lines 1111-1116. One `_from_state(settings, part, build, empty, unreadable)` helper would replace them.
4. **Duplicated Code:**
   - `try: _dict(json.loads(...))` appears five times.
   - `_untitled` and `_recording_lines` hand-write SQL on `plaud_events` and decode `detail` themselves, although `events_since` and `open_flags(recording_id=)` already return decoded `Event`s.
   - The `hold`/`breaker` overrides appear twice in `demo_view`.
   - "the disk is too full to import" is written three times.
5. **Long Function.** `_panel_from` is about 128 lines (907-1034); extract `_conditions`, `_attention` and `_healthchecks`. Its `flag_line` chooses by comparing a string with `SENTENCES`.
6. **Copied constants with no test pinning them.** `"kept-here"` (line 251) copies `checks.KEPT_HERE_EVENT`. `CONSENT_UNREADABLE` (line 222) copies `keepout.STOP_UNREADABLE`, and keepout imports cleanly on Pyodide, so it could be imported inside the function. `NO_SYNC` is pinned by a test; these two are not.
7. **Wrong comment** (hard violation, minor impact). `settings.py:84` and `views.py:257` say "recordings_ui.plaud imports this". It doesn't: `plaud.py` imports `Settings` only under `TYPE_CHECKING`, and never imports `views`. Correct the reason, or move the imports to the top.
8. `UNREADABLE_SYNC` says "see Needs attention", but `_summary` sets `see_status: False` (lines 665 and 849).
9. `_guarded` catches only `DatabaseError`. A malformed row (a `ValueError` from `_decode`, or an `Event.at` of None giving a `TypeError` in the sort) escapes `status_view` and breaks the backups section too. That goes against `status.py`'s "rather than breaking the page".
10. **Divergent Change.** `plaud.py` is 1,269 lines holding both the request protocol and the view model, and Phases 2 and 3 add their readers here. Consider a `plaud_views.py`.
11. **Mysterious Name.** `runtime.plaud_settings()` returns `PlaudViewSettings`, which clashes with `recordings.plaud.settings.plaud_settings`. With `app.plaud_views` and `plaud_view_settings`, one concept has three names.

Verdict: approve
