# Stage 2b, Phase 1: the phase-end council's reports

Four read-only reviewers, one per lens, on the whole branch (`8deb039...HEAD` at `6529487`), after the
final whole-branch review and its four fixes. Each had the spec, the plan with its phase-end list,
and the final review's reports, and was told not to repeat what those hold. The reports below are as
each reviewer wrote them, committed before anyone acts on them (phase end step 3). Their claims are
checked against the code, and each finding given a severity and a decision, in the next commit.

## Council lens 1: goal fit, scenarios and Dan's experience

**What I ran.** `pytest` (1961 passed), and a live Phase 1 deploy on a synthetic 2a-style archive with one `of_` consent line: a real worker and web app, seen in Playwright at 1280 and 320 px, plus `doctor --role worker` and `plaud consent`.

### Q1: Dan's Phase 1
- **The worker, idle.** It logs two lines (the gate is closed; 1 tagged, 1 flagged), is healthy, and Status says "last reported 15:46 (idle)". True and calm.
- **The Healthchecks check, never pinged.** Runbook steps 4, 7 (`/log`) and 8 are accurate, and Status shows no not-pinged line before the catch-up. Correct.
- **Late consent.** The recording is tagged and flagged within seconds, in all four places P15 names. `plaud consent` says "(2a's parser missed it)". Good, apart from F2.
- **The Sync screen.** The summary is plain. Copy ID is the only live control, with one reason line for the rest. At 320 px the first screen is three disabled buttons: honest, and fine until Phase 2.
- **Status's panel.** It shows the gate, consent and worker lines, and no sign-in line, which is right for Phase 1.
- **The Library cue.** "1 consent-listed in the archive · See Status". Good.

### Findings
**F1. Important.** `recordings_ui/plaud.py:230,1033`; `plaud/baseline.py:261`; `worker.py:393`.
- Needs attention's "Recording X wasn't checked against the private patterns: run `recordings reindex`" reads `K_PATTERNS_PASS.unwritten`. Only the patterns pass writes that key, and the pass runs only when the worker starts.
- What Dan sees: the reindex he runs lands `private`, and the line stays until the worker restarts. P2 is broken on the privacy path.
- **Fix now** (Q3c's answer fixes it).

**F2. Minor.** `plaud.py:1231`; `PlaudDetails.tsx:58`.
- A late consent listing writes no `retagged-private` event, so Details shows the line meant for an event aged out after 90 days: "Tagged private by the sync", with no date or reason (§15.3). This is the only automatic `private` Phase 1 writes on the real archive.
- The flag below it repeats the recording's own ID, and the Plaud ID again with a second Copy ID.
- **Phase-end list.**

**F3. Minor.** `doctor.py:719`.
- "sign-in needed" is a *problem* (exit 78) for as long as Phase 1 runs. A problem that is always expected teaches Dan to skim past doctor's problems.
- Make it a note until Phase 2's `make plaud-sign-in`.
- **Dan.**

**F4. Minor.** The plan's outlines.
- No outline line carries C5.23 (Phase 3's check marks before `add_snapshot`, or a Re-sync then a restart reads as "rebuilt"), C5.26, C3.06's parked table or C5.17.
- **Step 5:** scenario IDs on each outline line.

**F5. Minor, from before 2b.** `RecordingPane.tsx`.
- At 320 px the Details tab row is 327 px wide, so `main.panes` scrolls sideways (345 against 305).
- **Phase-end list, then Phase 3's 320 px item.**

### Q2: Scenarios
- All 35 Phase 1 rows have tests. I spot-checked C6.06, C6.13 (`tick`, turn included), C9.06 and C5.18: each asserts the outcome.
- Every Phase 2 or 3 row has an outline line, except F4's.

### Q3: Recommendations
- **(a) 320 px top bar: one guard, no redesign.**
  - A 20 px root font and a fallback font both leave no sideways scroll, because the labels are px-sized in 44 px boxes.
  - Phone text zoom remains. `overflow-x: auto; min-width: 0` on the tab row keeps any scroll inside the tabs.
  - Later.
- **(b) "Fails every check · Re-sync · Park": keep it on Status, with both buttons.**
  - Part I's J4 and J5 promise the line, and Task 22 dropped the buttons without Dan's decision.
  - Status is where the email sends Dan, and Park is the only way back to "All here".
  - The field goes into `SyncPanel` at Phase 2's start; the buttons come in Phase 3.
- **(c) Task 13: yes, stop the ping.**
  - An unchecked recording opens the existing `private-unwritten` flag, so no new blocker is needed (decision 10).
  - This errs private (P8), and it is rare: it needs a new pattern and a broken file at once.
  - `still_owed` clears the flag at the tick after the reindex lands, which also fixes F1.
  - Otherwise it sits in the backups and the mirror, known only to a stale line.

### Q4: The spirit of the project
No wording blames Dan. Hidden states and too many words: F1 and F2 only.

## Council lens 2: reliability

**What I ran.** Core and UI pytest: all passed. Five probes on synthetic archives in the session scratchpad. Nothing in the repo changed apart from this file.

### What holds
- **Crash and restart.** Each of these recovers or fails closed:
  - a mutate (2a's pending revision);
  - late consent (the tag, then the flag);
  - `_record_privacy` (the markers go last);
  - the patterns pass (not recorded as complete);
  - a consent list replaced by rename;
  - `at_start`, which runs before `Writer.open`.

  A flock dies with its process.
- **Concurrency.** No `state.db` transaction takes a flock inside it. The locks are never taken in reverse order, and every wait has a timeout. The poller only reads, under WAL.
- **Time.** Every stamp is read through `parse_stamp`, so the `isoformat()` drift (already listed) has no live effect. The containers share one clock.

### Findings

1. **Minor. `worker.py:766-776`, `requests.py:358-373`, `shiny_app.py:134`: a `running` row ends only when the worker starts again.**
   - **Sequence:** the handler resolves the flag, then `requests.finish` raises (`state.db` busy, or a full disk). The row stays `running`, because `expire` and `prune` skip it.
   - **Probe:** still `running` after 8 simulated days.
   - **What the session sees:** it polls for ever, and `row_reply` gives a running row no worker-down notice. So `rc stop worker` in the middle of a request also shows "Running" indefinitely.
   - **Decision:** phase-end list. Retry `finish` at the next tick; add the notice for running rows.

2. **Minor. `docker/consent-add.sh:53-63`: the list is renamed into place without an fsync, unlike `files.write_bytes_atomic`.**
   - **Sequence:** on a filesystem without ext4's flush on rename (XFS, say), a power cut soon after can leave either an empty list (a stop, which fails closed) or a zero-filled tail.
   - **Probe:** 7 exclusions, zeroed from mid-file, parse cleanly as 3, with no stop. The worker then resolves the lost IDs' consent-late flags (`flags.py:277-287`), and Phase 2 would import their recordings.
   - **Also:** two runs at once lose one ID.
   - **Decision:** fix now. `sync "$tmp"` before the `mv`, then sync the folder.

3. **Minor. `Makefile:102-103`: the deploy guard answers before the build, which takes minutes.**
   - **Sequence:** a Sync everything (Phase 2) or a CLI baseline that starts during the build is stopped part-way, with no refusal.
   - **Decision:** phase-end list. Build, then run the guard, then `up -d --no-build --wait`.

4. **Minor. `plaud/flags.py:164`: every tick opens one `index.db` connection per consent-list ID, even when nothing changed.**
   - **Measured cost of an idle tick:**

     | Consent list | Idle tick |
     |---|---|
     | 1 ID | 10 ms |
     | 500 IDs | 404 ms |
     | 2,000 IDs | 1.5 s |

     99% of the time is in `find_by_source`.
   - **Decision:** phase-end list. One `IN (…)` query, or skip while the list's signature and the index's `data_version` hold.

5. **Minor. `worker.py:281-284`: clock jumps.**
   - **Clock put back during a start:** `starting_since` is then in the future, and `beat_alive` reads STUCK until the start ends (probed).
   - **Clock put forward over an hour:** requests queued just before the jump expire at the next tick.
   - **The heartbeat** heals within 30 s.
   - **Decision:** Later.

6. **Minor. Tests: wall-clock upper bounds with only 0.3 to 0.7 s to spare.**
   - `test_plaud_requests_ui.py:259`: `< 1.3` against a 1.0 s backstop.
   - `test_plaud_requests_ui.py:159` and `:174`: `< 1.5`.
   - `test_worker.py:291`: a 0.3 s hold against a 1.0 s wait.

   These are the load-sensitive shapes behind the flakes that `964a412` fixed.
   - **Decision:** phase-end list. Assert on the deadline given, not on time elapsed.

### Data for decision 48's existing item
- **Cost:** the patterns pass takes 20.8 ms and 18 `state.db` connections per recording, measured over 1,000 synthetic recordings.
- **At 10,000 recordings:** about 3.5 min here, and much longer on a slow disk.
- **Meanwhile:** no late consent applies.

## Council lens 3: security and privacy

Probes on synthetic archives are in the scratchpad (`council/test_probe_*.py`). The privacy tests pass (65).

### 1. The checklist
1. **Secrets: fine.**
   - Errors name the setting, never the URL (`alerts.py:100`).
   - Pings log a status or a class.
   - Authorization is unredirected (`client.py:374`).
   - The ignore files cover the sign-in.
2. **Permissions: fine.**
   - `deadman_sync_url` and `/plaud` are in `worker` only; `deadman_url` is in `backup` only (`compose.yml:107,165,191`).
   - Doctor forbids them per role (`doctor.py:62-77`).
3. **Network: fine.**
   - `worker` has no ports.
   - `test_plaud_imports.py` passes.
   - The root conftest refuses `plaud.ai` and drops proxies.
4. **Failing closed:** S1 and S2.
5. **Injection: fine.**
   - SQL is parameterised; only constants go into f-strings.
   - Commands run as argv.
   - IDs are checked against `ID_RE`.
6. **Logging: fine.**
   - Logs carry IDs, counts and classes, and nothing new uses `exc_info`.
   - The `str(exc)` leftovers are already listed.

### 2. Tiers
Every tier was right:
- Tasks 1 and 2 are tooling and wording.
- `check_request` belongs to Task 14, which was Tier 1.
- Task 19 shows titles only of recordings here, inside the app.

**Forward:** Phase 2's views of titles not here (the cache's readers, Not here yet, Release's preview) must be Tier 1, because decision 56's reason ends there.

### 3. The judged set
It holds on every path the app writes: a merge under a new ID, A–P–A, an older snapshot merged in, reindex, a restore with an older or newer `state.db`, a lost `state.db`, and `forget`. The gaps are S1 and S3.

### 4. Traces
- **Consent-listed:** `private`, then a flag by ID. Flags clear only while the list reads.
- **Kept out by title:** state.db keeps the word, never the title.
- **Renamed to private:** in Phase 1, only a reconcile catches it.
- **A typo or an unreadable list:** a stop, by line number, and the blocker opens.
- **The Healthchecks ping:** an empty body.

Apart from S1 and S2, no stop lifts silently and no title leaves the app.

### 5. Exposure
All fine except S4:
- **`make consent-add`:** `ID` arrives unexpanded and is canonicalised to 32 hex before ssh. `consent-add.sh:18-25` checks it again.
- **Pages:** only committed demo files.
- **The demo guard:** `cannot_request_reason`.
- **The request input:** an allowlist of keys, and `parse_id` and `_opaque` on the IDs.

### 6. Task 13's question
**Yes.** Stop the ping through the existing `private-unwritten` blocker; it needs no new one.
- §13.2 says a marker that can't move opens that blocker "until the tag lands".
- Under P8, a `private` that is possibly owed counts as owed. Decision 46 already treats a failed late-consent pass that way.
- Leave out `busy` recordings.
- Retry the pass at each turn, not only at start. Otherwise the email lasts until a restart.

### Findings

**S1. Important. Fix now.** `audio_router.py:1184-1186`, `reconcile.py:135`.
- **What happens:** the import's create-time check calls `matches_auto_private` unfolded. `privacy._hits` and the spike both fold.
- **Probed:**
  - `"Ｐrivate…"`, `"Pri​vate…"` and `"PRI­VATE…"` are each created with their envelope and media and no tag.
  - `private` lands only at the reconcile that follows.
  - If that reconcile fails, the run stops with `ImportStopped`. The recording stays untagged and unflagged.
- **What it breaks:** CLAUDE.md's "every title the app has seen at that moment", and §13.1 case 1.
- **History:** Task 9's spec review noted it (line 12); nobody ruled on it.
- **Fix:** fold inside `matches_auto_private`. Phase 2's import then folds too.

**S2. Important. Dan; S2a to the phase-end list.** `worker.py:447-459`.
- **What happens:** when the start's patterns pass raises as a whole, `_step` swallows it.
  - Only a log line is left: no `K_PATTERNS_PASS`, no Needs attention, no blocker.
  - Nothing retries it until a restart.
- **Probed:** with an `OperationalError`, the worker went idle with `K_PATTERNS_PASS` empty and no blockers. A new private pattern silently never runs.
- **S2a:** record the attempt.
- **The ping:** as in §6.

**S3. Minor. Phase-end list.** `writer.py:874`, `privacy.py:162-170`.
- **What happens:** the judged set trusts `recording.json`'s `sha256`, not the bytes it reads.
- **Probed:** two cases are never judged, when they sit between two titles the marker holds:
  - a `sha256: null` snapshot, which `reindex` accepts;
  - a file changed under a judged sha.
- **How it is reached:** only by an outside edit or by corruption.
- **Fix:** hash the bytes read.

**S4. Minor. Phase-end list.** `backup.py:357-388`.
- **What happens:** Task 16 allows folders in `extra_paths`.
  - `/state` then backs up `voice.db` and Phase 2's `plaud-cache.db`.
  - `/run/secrets` backs up the restic password and the NAS key.
- **Only doctor objects** (`doctor.py:751-776`).
- **Why now:** Phase 3 has Dan type `/state/manifests`.
- **Fix:** `run_backup` refuses what doctor reports, as it refuses a symbolic link.

0 Critical, 2 Important, 2 Minor.

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

## Decisions (the session, after checking each claim)

Each Important claim was checked against the code before this table:
- **S1:** `sources/audio_router.py:1184` calls `plaud/reconcile.py`'s `matches_auto_private`, which doesn't fold, while `privacy._hits` folds before calling it.
- **G1 and S2:** only `plaud/baseline.py:240-254` writes `K_PATTERNS_PASS`, and only for a pass that ends. `worker._step` (`worker.py:526-538`) logs a whole pass that raised, and nothing else.
- **R2:** `docker/consent-add.sh` renames the new list into place with no `sync`.
- **Simplicity 5:** `_panel_from` puts lines into both `conditions` and `attention`, and the P12 filter then drops them from `conditions`.

The Minors were taken as written. "Step 4" means fixed in its own commit during the phase end's step 4. "Phase 2's start" means carried into Phase 2's outline, for when that code is touched again.

| Finding | Severity | Decision |
|---|---|---|
| G1, S2 (and Task 13's question for the council) | Important | **Step 4, at Tier 1.** The controller rules on Task 13's question: yes. Both lenses agree, and §13.2 already says a marker that can't move opens the `private-unwritten` blocker "until the tag lands". Under P8, a possibly owed `private` counts as owed. So: a recording the patterns pass couldn't check opens a `private-unwritten` flag, which stops the ping, as a late consent failure does. The pass is retried at each turn, not only at start, so a reindex clears the line at the next tick (G1). A pass that raises as a whole records its attempt and opens the blocker (S2). `busy` recordings are left out. |
| S1 | Important | **Step 4, at Tier 1.** `matches_auto_private` folds (NFKC, without invisible characters) inside itself, so every caller does: the audio-router import, the judge, and Phase 2's import. |
| Simplicity 1 | Important | **Step 4 (missing docs are written now).** Two CLAUDE.md recipes: adding a Plaud request kind, and adding a blocker. |
| Simplicity 2 | Important | **Step 5.** A "Carried forward" page: the decisions the code cites, each one line with its built name; the open rows; the Consumes lines Phase 2 needs. Phase 2's plan cites commits instead of carrying listings. |
| Simplicity 3 | Important | **Phase 2's start.** Split `worker.py` (liveness, handlers), the follow loop out of `shiny_app.server`, `SyncPage.tsx` by section, and `first-run.md` into three. |
| G2 | Minor | **Phase 2's start.** Details shows a late consent listing's `private` with no date or reason, and the flag repeats the IDs. |
| G3 | Minor | **Dan.** Should doctor's "sign-in needed" stay a problem (exit 78) for as long as Phase 1 runs, or be a note until Phase 2's sign-in? |
| G4 | Minor | **Step 5.** Scenario IDs on each outline line (C5.23, C5.26, C3.06, C5.17). |
| G5 | Minor | **Later, with the 320 px item.** Details' tab row is 327 px wide at 320 px. It predates 2b (tagging part A). |
| R1 | Minor | **Phase 2's start.** A `running` row whose `finish` raised stays `running` until a restart. Retry `finish`, and give a running row the worker-down notice. |
| R2 | Minor | **Step 4, at Tier 1.** `consent-add.sh` syncs before its rename, and the folder after it. |
| R3 | Minor | **Step 4.** `make deploy` builds first, then runs the guard, then `up -d --no-build --wait`. |
| R4 | Minor | **Phase 2's start.** One `index.db` query per tick for all consent-listed IDs, or skip the query while nothing has changed. |
| R5 | Minor | **Later.** Clock jumps: the heartbeat heals within 30 s, and the containers share one clock. |
| R6 | Minor | **Step 4.** The request tests assert on the deadline given, not on elapsed time, so load can't flake them. |
| S3 | Minor | **Phase 2's start, with the snapshot marks.** Hash the bytes the judged set reads, not `recording.json`'s `sha256`. |
| S4 | Minor | **Step 4, at Tier 1.** `run_backup` refuses an extra path doctor reports (`/state`, `/run/secrets` and similar), as it refuses a symbolic link. |
| Tier note | — | **Step 5.** Phase 2's views of titles not in the archive (the cache's readers, Not here yet, Release's preview) are Tier 1. |
| Simplicity 4, 6, 7, 8 and the wording | Minor | **Phase 2's start.** |
| Simplicity 5 | Minor | **Step 4.** Lines go into `attention` only, so the dead branches are gone. |
| Simplicity 9, 10 | Minor | **Later.** The spike script goes with Phase 3's session C; the duplicated tests and CSS are left alone. |
| The judged-set reread (phase-end row from Task 9) | — | **Closed.** The security lens found that it holds on every path the app writes, and S3 is the one gap. |
| Q3(a), the 320 px top bar | — | **Dan.** The goal-fit lens recommends a guard (`overflow-x: auto` on the tab row), not a redesign. |
| Q3(b), §15.2's failing-checks line | — | **Dan.** The goal-fit lens recommends keeping it on Status with both buttons: the field at Phase 2's start, the buttons in Phase 3. |
