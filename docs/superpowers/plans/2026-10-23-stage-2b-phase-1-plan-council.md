# Stage 2b, Phase 1: the plan council's reports

Nine read-only reviewers read the plan at `0b3cefe`
(`docs/superpowers/plans/2026-10-23-stage-2b-phase-1.md`) against the approved spec and the real
code, each through one lens and over the tasks named. The plan's code had already been applied to
scratch copies of the repo and tested there against stand-ins for unbuilt tasks, so they looked
hardest at what that could not catch. Severities: Critical (breaks a safety property, loses or leaks
data, or blocks the phase), Important (a bug or missing case a user or test would hit, a wrong
listing, a factually wrong doc), Minor.

The reports follow verbatim, apart from scratch-folder names. The controller's disposition of every
finding is in the plan's "Rulings after the plan council" and in the commit that applies them.

| ID | Lens | Scope |
|---|---|---|
| G | 1, goal fit, scenarios and Dan's experience | Tasks 2, 5, 17, 18, 19–25; spec Part I |
| R | 2, reliability | Tasks 6, 9, 11–17 |
| S | 3a, security and privacy | Tasks 3, 4, 5, 7, 8; the checklist over the whole plan; tiers |
| P | 3b, security and privacy | Tasks 9, 10, 13, 14, 18, 19, 20 |
| M | 4, simplicity | the plan's heads and decisions; spot reads |
| BA | 5, the builder | Tasks 1–6 |
| BB | 5, the builder | Tasks 7–12 |
| BC | 5, the builder | Tasks 13–18 |
| BD | 5, the builder | Tasks 19–24 |

## Council lens 1: goal fit, scenarios and Dan's experience (G)

Every Phase 1 scenario row has a task and a failing-first test, and Task 5 comes early enough. The problems are in what Dan runs by hand, and in buttons that don't work yet.

### Important

**G1. The runbook says ntfy is optional, but the compose file still requires its topic file.** *(Task 2, step 5)*
- **What the plan does:** it comments out `install … ntfy_topic` and drops `ntfy_topic` from the expected files.
- **Evidence:** `docker/compose.yml:98` always sets `RECORDINGS_NTFY_TOPIC_FILE`, and `compose.yml:146` mounts `${RECORDINGS_SECRETS_HOST}/ntfy_topic` with no fallback. Task 17 keeps it in `deploy.example.conf`. A missing file means `make deploy` fails. An empty one makes `config.secret` raise "is empty" (`config.py:270`), so the backup's dead-man pings stop (`doctor.py:510`).
- **Fix:** in Task 17, make `ntfy_topic` optional the way `ntfy_token` already is (`compose.yml:103,149`). Document it in the template, test it in `test_deploy_template`, and run the smoke test without the topic. The runbook then says doctor's "alerts are off" warning is expected.

**G2. Runbook 1c runs the spikes in `$REPO_DIR`, the live deployment's checkout.** *(Task 5, step 5)*
- **Why that breaks:** until the merge, the script exists only on `stage-2b`. The boot unit runs `up-deployed.sh up -d` from that same folder (`recordings.service:23,26`).
- **The failure:** once Task 17 lands, stage-2b's `worker` service needs `${RECORDINGS_CONSENT_HOST:?}` and `${RECORDINGS_PLAUD_HOST:?}`. Every `rc` line would fail, and so would the boot's `up -d`, leaving web and backup down after a reboot.
- **Fix:** 1c clones `stage-2b` into its own folder, names the commit, and runs `uv sync --frozen` there. It never moves `$REPO_DIR`.

**G3. Session A stops after S1.** *(Task 5, step 3: `s1` and `s2`)*
- **No token URL:** S2 and S4 need `--token-url`, "which session A's S1 finds" (decision 24). But `s1` prints only key names, the token type, expiries and claim names, so nothing points to the endpoint. The runbook doesn't say where else to look.
- **A guessed request shape:** `s2` posts only `grant_type` and `refresh_token`. If Plaud's endpoint also needs a `client_id` (S1 would show one among the key names), the 4xx prints "the refresh was refused from this machine: stop and ask Dan". That is spec §18's "the design fails" branch, reached by a malformed request.
- **Fix:**
  - Say where the token URL comes from (the vendor CLI or `audio-router`).
  - Have `s2` send a `client_id` when the token file has one.
  - Word a refusal that carries an OAuth error code as "compare with the vendor CLI's own refresh". Keep "stop and ask Dan" for a refusal the Mac doesn't get.

**G4. 1c gives no steps for the spikes Dan drives by hand, and their results depend on the order.** *(Task 5, step 5)*
- **S3:** the three sign-ins (web app, Plaud MCP login, vendor CLI in a second temporary `HOME`) aren't listed, nor "run `s3` once as a baseline first". The baseline matters: S2 replays the old refresh token, which under reuse detection can revoke the session's sign-in, so S3 would report a sign-out the second sign-in didn't cause and prompt a question Dan didn't need.
- **S9:**
  - Missing order: run `s9` without `--id` repeatedly from the transfer until the clip shows up; then `--id … --own-clip` once as the baseline, and again after each edit in (c).
  - Missing source: where the clip's Plaud ID comes from (it isn't in the archive, and nothing prints whole IDs).
- **The token file:** what to do if the CLI didn't write `$tmphome/.plaud/tokens.json` (spec §22 leaves the CLI's name and format to S1).
- **Fix:** a short procedure per session in 1c.

**G5. The plan has no way to import a "needs your review" row.** *(Task 21, steps 2 and 5)*
- **Evidence:** `SyncPage` draws a review row with no checkbox, and the test asserts `review.querySelector("input")` is null. Spec §11.6 says it waits "until **Import selected** brings it in". §5 call 11 and J4 say "for him to import or keep out", and C5.25 says "Import selected brings it in". Phase 2 would have to rework this UI.
- **Fix:** keep the checkbox on review rows (Import all already leaves them out), and test that picking one sends `import` with its ID.

**G6. Runbook section 6 says Status shows "sign-in needed" in Phase 1. It won't.** *(Task 17, step 6; decision 10)*
- **Evidence:** nothing in Phase 1 opens the `sign-in` blocker (`blocked_before_call` returns None). Status will show "Scheduled sync waits for the first catch-up (runbook)", and `numbers.sign_in` will be null.
- **A second problem:** doctor's problem names `make plaud-sign-in`, which doesn't exist until Phase 2.
- **Fix:** the runbook says doctor reports "sign-in needed", Status shows the gate's line, and the make target comes with the next phase.

**G7. Buttons whose handler comes in a later phase answer "error: `NotImplementedError`" in a deployed app.** *(decision 2; Tasks 14, 19, 21, 23)*
- **When Dan meets it:**
  - In Phase 1, Compare is the only button enabled (J8).
  - After Phase 2's catch-up opens the gate, Re-sync in every Details, the Library's selection bar, Sync everything and Accept these changes all fail until Phase 3. That is exactly when tagging starts (decision 7) and relabelling begins.
- **Why it matters:** spec §15.6 keeps "error: `<Class>`" for faults (J4: "a Claude session").
- **A related gap:** the selection bar's Re-sync ignores the gate (`resyncReason` checks only `available`), so in Phase 1 it is enabled and gets back "The first import is a supervised run on the server (runbook)".
- **Fix:**
  - At start, the worker records the request kinds it can handle in `plaud_state`.
  - The view model then disables any kind with no handler, with a plain reason ("Comes with a later phase of stage 2b").
  - The gate's reason reaches Details and both selection bars.

### Minor

**G8. A wording error in the new ntfy section.** *(Task 2, step 5, "Setting up ntfy, later")* It reads "the topic in `[alerts] ntfy_server`". It should be "the server in `[alerts] ntfy_server`, the topic in the `ntfy_topic` secret".

**G9. Status's worker line shows internal phase names.** *(Task 19, `_worker`)* It prints "(turn)", "(request)" or "(consent)". The spec's line is "(checking, 412 of 1,234)". Map the phases to plain words.

**G10. Status repeats one cause up to four times.** *(Task 19)* The summary, the failing line, the not-pinged line and the condition can all say "sign-in needed". In Phase 1 the gate's line appears twice. For P12, drop a condition that equals the summary.

**G11. The Library has no Needs attention list.** *(Task 23)* §15.3 says "the Library's Needs attention lists them by archive ID". No stage has built that list, so the plan uses Status's without saying so. Record this as a reading Dan can see.

**G12. The phase end never says whether Dan deploys Phase 1.** *(Task 25)* Deploying is what makes J8 step 3 ("tagged … within seconds of deploy") happen before the dry run. Add an optional step for Dan after the merge: "[Dan] deploy Phase 1 (section 6)", with what it gains.

**G13. Two Sync screen phrases.** *(Task 21)* Gone from Plaud's hint says "To keep one out for good", but it points to the steps for taking one out; the toast lacks J6's "· View".

## Council lens 2: reliability (Tasks 6, 9, 11–17)

1 Critical, 7 Important, 6 Minor.

### Critical

**R1. One recording that can't be written stops every late consent listing, and nothing says so.** Task 8, step 3 (`flags.apply_late_consent`), as Task 13's `_consent_tick` runs it.
- Evidence: around `writer.mutate` the code catches only `KeyError`, `LockTimeout` and `StaleFile`/`InvalidFile`/`NotTheWriter` (plan 8036–8045).
  - A folder with the wrong owner, or a full archive disk, raises `OSError`. A busy `state.db` raises `sqlite3.OperationalError`.
  - Either one leaves the loop, and `_step` only logs it.
  - `_owed_private` runs in the same step (13101–13105), so no `private-unwritten` flag or blocker opens.
  - The order is the same at every tick, so every consent-listed recording sorted after the bad one stays untagged for good.
  - Spec §16 has this blocker for exactly "a `private` the sync owes fails to land".
- Fix: per recording, catch `OSError` and `sqlite3.Error` as `unwritten`, and run `_owed_private` as its own `_step`. Test with a read-only folder sorted before a second listed recording: the second gets `private`, and the blocker opens.

### Important

**R2. A crash-looping worker reads as alive.** Task 13 (`start`, `alive`, `main`).
- Evidence:
  - The heartbeat thread starts before `Writer.open` and `gate_at_start`. When either fails, `main` exits (78, or 1 for an uncaught `sqlite3.Error`) and `restart: unless-stopped` starts it again.
  - Every start writes a fresh `starting` heartbeat. Docker's restart delay tops out at about a minute, under the 90 s limit, and the 30-minute `starting` allowance starts again each time.
- Result:
  - `--alive` and Status (Task 19 calls `alive`) never say "silent" or "stuck", which is what the runbook's "When the Plaud alert comes" relies on.
  - A start that fails after the first health check has passed (30 s) lets `make deploy --wait` report success.
- Fix: carry `starting_since` across restarts (kept while the previous heartbeat was `starting`) and measure the 30 minutes from it. On a failed start, `main` writes `phase: "failed"` with the error's class, and `alive` reports it.

**R3. A `private-unwritten` flag never closes once the `private` is no longer owed.** Task 13, `_owed_private` (13173–13175).
- Evidence: a flag resolves only when the recording becomes private. Remove the consent line (it was added by mistake) and the flag stays open for good, and so does the blocker. From Phase 2 on that is a permanent "isn't being pinged": crying wolf, with a remedy (`recordings reindex`) that can't clear it.
- Fix: resolve the flag when the consent list reads cleanly and none of the recording's IDs is listed, as `apply_late_consent` already does for `consent-late`. Add a test.

**R4. The tick's blockers depend on what the worker remembers.** Task 13, `_watch_consent` (13120–13137) and `_owed_private`.
- Evidence: `self.consent` is set before `K_CONSENT` and the blocker are written. The blockers are written only when `changed`, or when `_unwritten_open` flips. So a failed write (a busy or full `state.db`) is never retried, and neither is a blocker row that another process or a restore removed. A typo can then go unblocked until the file changes again. Spec §16 says the answer is "worked out afresh each turn".
- Fix: update memory only after the writes succeed, and call the idempotent `open_blocker`/`clear_blocker` at every tick.

**R5. A failing streak that began with no good listing never takes the work cause.** Task 11, `health._ends`.
- Evidence: `_ends("listing")` returns `good`, so a run with a good listing whose attempts all fail leaves the streak as "listing". The next run with no attempts clears it and pings "up" in the middle of a systemic failure. The docstring's "carries … under the new cause" can happen only for "disk". Spec §11.7: "a systemic failure can't flip the check up and down".
- Fix: `_ends("listing")` becomes `listing in ("good", "small-shrink")`, so such a run carries the streak on as "work", `since` kept. Test: an outage, every fetch failing, then a quiet run, and no ping.

**R6. `index.db` never switches to WAL on the server.** Task 6, step 3.
- Evidence: only `Index.rebuild` calls `_wal()`, and `Writer.__init__` rebuilds only a foreign index or one with an update left pending (`writer.py:323`). So the deployed 2a index stays in rollback-journal mode. The test rebuilds first, so it passes anyway, and spec §9.5 (2a item 72d) goes undelivered.
- Fix: call `_wal()` in `Writer.__init__` once the index is known to be this archive's. Test it on an index left in rollback-journal mode.

**R7. Rolling back across 2b stops every backup.** Tasks 16 and 18 (the runbook's "Rolling back across 2b").
- Evidence: section 6 has Dan add `"/consent"` to `[backup] extra_paths`. 2a's `run_backup` refuses any extra path that isn't a file (`backup.py:376`), and 2a's `compose.yml` doesn't mount `/consent`. So after `make rollback` every backup is refused, and the section doesn't say so.
- Fix: add a step to take `"/consent"` out before rolling back and put it back after deploying forward, with a runbook assertion.

**R8. Section 6's failed-deploy advice points to the wrong rollback.** Task 17, step 6 (plan 16719): "fails … then roll back (2.8)".
- Evidence: 2.8 is `make rollback TAG=` run with the 2b checkout's `compose.yml`. That starts `worker` on the old image, which lacks the `worker` command. Spec §17.3 says to stop and remove `worker` and `git checkout` first.
- Fix: point to "Rolling back across 2b" (Task 18 can update the pointer). Update the Makefile's "when web doesn't pass" comment too.

### Minor

**R9. Long steps at start don't count as progress.** Task 12's `has_2b_snapshot` and `seed_markers_after_rebuild`, and Task 10's `patterns_pass`, never bump it, though spec §9.2 asks for a bump "at every recording". While the gate is closed (all of Phase 1), every start reads every `plaud-*.json` file whole. On a large archive `--alive` says stuck after 30 minutes. Fix: pass a progress callback.

**R10. The scheduled turn tries the `plaud` lock with `timeout=0`.** `locks.running` probes with `LOCK_SH`, and Status and `insert` probe this way. A probe at that same instant makes the turn "busy", and decision 42 then skips 20 minutes. Use a timeout of about 1 s.

**R11. The first sync after the catch-up can come later than the runbook allows.** After a "gate" turn, decision 42's in-memory wait can hold that sync for up to 20 minutes, plus the run itself. That is past section 8's "within 20 minutes … if it doesn't, stop". While waiting on a gate outcome, check `gate_now` at each tick.

**R12. A lasting failure fills the log.** `_step` warns at every tick, 43,200 lines a day. Log once per (step, class), as `_beat` does.

**R13. Two tests can hang or fail for reasons outside the code.** `test_two_sessions_inserting_at_once_make_one_row` reads stdout with no timeout (use `communicate(timeout=30)`), and the `chmod 0o200` test fails as root (skip it there).

**R14. Spec §22's "a check never pinged never goes down" has no owning task.** Phase 1 leaves the sync's check unpinged for weeks: assign it to Task 17.

### Checked and sound

Additive tables with `SCHEMA_VERSION` 1. `immutable=1` reads a WAL index (probed on SQLite 3.54). The orphan snapshot is adopted. `record_rebuild` is one transaction. Interrupted requests are failed at start. `conclude_turn` sends nothing on an error. A consent read counts only if the file held still.

## Council report: lens 3, security and privacy (first half)

Tasks 3, 4, 5, 7 and 8 read in full. Probes ran against the plan's `keepout.py` (a scratch copy, identical to plan lines 2318–2546).

### Important

**S1. Invisible or fullwidth characters turn an exclusion into a kept line, or into nothing** (Task 3, Step 3, `_classify`).
- **What happens:** a zero-width space or word joiner at the start of a line, a BOM in the middle of the file (from concatenating two files), or a ZWSP between the ID and the dash: the line reads as **kept** and the list stays readable. A fullwidth ID (from a phone keyboard) reads as **text**, which is not even listed.
- **Probed:** `"​- `ID` — asked"`, `"- `ID`​ —"` and `"﻿- `ID` —"` on line 2 all give `kept`, `stop=None`.
- **Why it matters:** Review Focus 1 promises "read exactly as from a plain file", but only a leading BOM is handled, and Status doesn't show kept lines.
- **Fix:** classify each line after NFKC and dropping category Cf and `HIDDEN_CODEPOINTS` (`fold` without the casefold). Keep `missed_by_2a` on the raw text, and add these cases to `test_crlf_bom_and_nbsp_lines_read_as_plain_ones`.

**S2. An ID broken by a space or a line wrap is silently ignored** (Task 3; spec §12.1, typo rule 4). The typo rule only looks at the first word.
- **Probed:** an ID with a space in the middle (16 hex digits, a space, 16 more) reads as `text`. An ID wrapped over two lines (20 hex digits, then 12) reads as two `text` lines. Nothing is excluded or listed, and the list counts as readable.
- **Why it needs a ruling:** this follows the spec's own typo rule, so only Dan can change it.
- **Fix (proposed):** a first word made only of hex digits, 8 or more long, that isn't a valid ID becomes a typo. Otherwise, record the gap under the spec's Later section.

**S3. The spike's guard trusts a hand-written session config** (Task 5, `build_guard`; runbook 1c).
- **Wrong archive path:** a wrong `[archive] path`, or an unmounted data disk, gives an empty set of private IDs, with no warning. `recording_dirs()` globs nothing (`archive.py:72`), `load_config` doesn't check the path (`config.py:204–211`), and no sentinel is read. A recording Dan tagged private, with an ordinary title, then passes the guard for S8 (the envelope with its blocks) and S11 (the audio download), against §18's "never fetches or downloads … a private recording".
- **Drift:** the patterns and the word come from a hand copy of `config.toml`.
- **Old words:** the words the worker has enforced (`plaud_state`) are ignored, though §18 says "the keep-out words".
- **Fix:** `read_sentinel` the archive, and refuse when it fails or the archive holds no recordings (exit 78, before any call). Take the union with production's patterns and word, and add the enforced words when `[state] path` is set. Test: a config pointing at an empty folder exits 78, and `fake.requests == []`.

**S4. One undecodable `recording.json` silently stops late consent listings** (Task 8, Step 3, `apply_late_consent`).
- **What happens:** it catches only `(ValidationError, OSError)`, but `Archive.load` raises `UnicodeDecodeError` (`archive.py:107`). Task 13's `_step("consent")` swallows it at every tick: later recordings are never tagged, `_owed_private` never runs, and a warning lands every 2 s, against §12.1's "Every other recording keeps flowing".
- **Fix:** catch `ValueError`, as `_plaud_ids` already does. `mutate` then reports `InvalidFile`, so the recording counts as unwritten and the blocker opens. Add a test.

### Minor

**S5.** `valid_word` accepts `"-"`, `"__"` and `"-_-"`, but `keep_out_match` reduces each to an empty target and never matches (probed). The keep-out is then silently off. **Fix:** require at least one letter or digit, so such a word gets `BAD_WORD`.

**S6.** Decision 30's guard has a proxy hole. With `https_proxy` set, the client's opener (built at import from `getproxies_environment()`) and the spike's `build_opener` connect through the proxy, which resolves `plaud.ai` itself, bypassing `getaddrinfo`. **Fix:** the root `conftest.py` drops the proxy variables at import; test that `_API_OPENER` has no https proxy.

**S7.** The spike never re-checks the envelope's own `name`. S8 and S9 fetch blocks through `client.envelope`, and S7 and S11 download after `_files`, judged on the listing's title alone (§11.5). **Fix:** check `guard.allows(row.listed_id, env.get("name"))` after `_files`, before any block or download.

**S8.** `--own-clip` skips the keep-out check for any `--id`, so a typo can fetch an older kept-out recording's envelope. **Fix:** allow it only for a recording not in the archive and recorded within the last day.

**S9.** The spike's `main()` catches only `SpikeRefused`, `ConfigError` and `PlaudError`. Any other exception prints its text in a traceback, against the script's own docstring. **Fix:** add a last `except Exception` that prints the class name and exits 1.

**S10.** `.dockerignore`'s `tokens*.json` matches only at the build context's root (Docker patterns aren't recursive, and the file's other lines use `**/`). **Fix:** `**/tokens*.json`, and test that line.

**S11.** Review Focus 2's before-and-after signature check (Task 13) accepts a list written in place in two flushes when the read falls in the pause. In Phase 1 that churns flags for one tick; in Phase 2 an import could slip in. **Fix:** on the phase-end list, have Phase 2's read before each download trust only a file whose mtime is at least one tick old.

### Security checklist (whole plan)

1. **Secrets:** fine.
   - The client raises fixed classes `from None`, with no URL in them (`_no_url_in`).
   - `deadman_sync_url` is checked under its own name, and its repr shows only "set".
   - `ping_url` logs only the status or the class.
   - The spike's token file is 0600 (`mkstemp`) in a 0700 folder, and never printed.
   - For the dockerignore, see S10.
2. **Permissions:** fine. `/plaud` and `deadman_sync_url` are mounted in `worker` only, and `/consent` read-only in `worker` and `backup`, not `web` (Task 17's tests). Doctor checks the modes and owners (Task 15).
3. **Network exposure:** fine apart from S6. No new port, and fakes only.
   - **Decision 31 is pinned by tests that would fail,** each in a fresh interpreter: Task 4 walks every `recordings_ui` import, Task 7 calls `plaud_settings`, Task 19 calls `plaud_view_settings`, and Task 13 imports `recordings.worker`, which `requests.insert` and the views load lazily.
   - The one gap: Task 24's live probe runs in demo mode, so no request reaches `insert`.
4. **Failing closed:** see S1–S5 and S11. Otherwise fine:
   - A missing, unreadable (Latin-1 or UTF-16), empty or ID-less list stops, and so does a typo.
   - CRLF, a leading BOM, NBSP and U+202F read as in a plain file (probed).
   - A consent stop still applies the exclusions that read, and clears nothing.
   - An index that can't answer is retried, and a bad `keep_out_word` idles the worker.
   - The ping has an empty body.
5. **Injection:** fine. SQL is parameterised, and `set_listing` accepts only known columns. IDs are URL-quoted with `safe=""`. Links are checked for scheme, printable ASCII, host, port and no user name. The spike's file paths come from 8-hex hashes.
6. **Logging:** fine apart from S9. Logs carry archive IDs, class names and line numbers, and the new code has no `exc_info`.

### Tiers

All 25 labels are right. Add checklist lines 1, 4 and 6 to the Tier 2 reviews of Task 19 (P13's last view gate), Task 20 (decision 21's preview title) and Task 24 (the canned view on public Pages).

## Council lens 3b: security and privacy, Tasks 9, 10, 13, 14, 18, 19, 20

No Critical findings: 7 Important, 5 Minor. Phase 1 never calls Plaud (`stand_in_turn` raises and the gate can't open), so most of these are gaps Phase 2 will rely on.

### Important

**P1. Cached titles of IDs consent-listed since the compare** (Task 19, Step 4, `_sync_from`, line 19099).
- The code: `"title": None if r.titles_hidden or row.review else row.title`. `_Read` already holds `K_CONSENT` (line 18811) but doesn't use it here.
- The worker re-reads the list within 2 s, but the cache only changes at the next compare: 20 minutes later, or never while the sign-in has lapsed or the schedule is off.
- That breaks P13 (spec line 410): "None ever for a consent-listed recording".
- Fix: no title when the row's canonical ID is in `K_CONSENT["ids"]`, or when the title matches an enforced word (`r.words`). The same in `release_preview`.
- Phase-end list: Phase 2's tick purges cache titles of newly listed IDs.

**P2. Case 2 checks only the newest title** (Task 9, Step 3, `judge`, lines 9202-9203).
- Spec §13.1 (line 1580): "The same rule runs inside reconcile, on new snapshots' titles".
- Scenario: a reconcile hits `StaleFile` after snapshot B ("Private: …") lands, and C arrives before `reindex`. With the marker still at A, only C is checked; B never is. Phase 3's edit-sync can do the same.
- Fix: check every title after the last one whose hash equals the marker (all of them if none does). Decision 3 and A→B→A still hold.
- Test: titles `[A, "Private: x", "Week 5"]`, marker at A, ends private.

**P3. Markers move before the late keep-out is recorded** (Task 9, Step 5, `_record_privacy`, lines 9625-9636).
- The order is `set_markers`, then `add_event`, then `record_late_keep_out`.
- If the last raises (a busy or full `state.db`), the title is no longer due. `kept_out_at` and the flag are lost for good: Dan is never told, and "never fetched again" breaks once edit-sync exists.
- Fix: record the event and the keep-out first, `set_markers` last.

**P4. A failing late consent listing is silent** (Task 13, Step 3, `_consent_tick` and `_step`, lines 13086-13106).
- When `apply_late_consent` raises (`OSError` from `mutate` on a full or read-only disk, `sqlite3.Error`, `UnicodeDecodeError`), `_owed_private` never runs.
- Task 8 catches only `StaleFile`/`InvalidFile`/`NotTheWriter` per recording (lines 8040-8045), so one bad file aborts the whole pass at every tick.
- Result: the consent-listed recording stays public, with no flag, blocker or Status line, only a log line every 2 s. Spec §13.2 (lines 1610-1614): "shows it at once".
- Fix, Task 13: on an exception, open a blocker (a fixed phrase plus the error's class), clear it after a clean report, and log once per class.
- Fix, Task 8 (the other security reviewer's task): catch `OSError` and `sqlite3.Error` per recording into `unwritten`.

**P5. A pasted Copy ID never reaches make** (Task 18: the runbook at line 17524, the README, the `##` help line).
- The docs give `make consent-add ID=<id>`, with the Copy ID form `` - `of_…` — ``. Pasted unquoted or in double quotes, zsh runs the backticks and drops the ID. I tried it here:
  - unquoted: `command not found: of_…`, then make gets `ID=-` and a goal `—`;
  - double quotes: make gets `ID=-  —`.
- It fails closed, but Review Focus 4's promise is false in a real terminal.
- `test_consent_add_accepts_the_copy_id_form` passes argv with no shell, so it can't catch this.
- Fix: document `ID='<paste>'`, single-quoted, everywhere (usage error included), and add a test that runs the documented line through `sh -c`.

**P6. `release_preview` never returns a title** (Task 20, Step 4, lines 20822-20831).
- It opens `Cache.read_only`, which uses `mode=ro` (Task 6, lines 6579-6586), then calls `drop_preview`. SQLite refuses the delete (checked here: `attempt to write a readonly database`), and `except sqlite3.Error` returns None.
- So Release's question has no title, and the title is never deleted when read. Spec line 815 says "deleted once the question is answered".
- Not caught: PART-D-REPORT says Task 20's Python got "ruff only (needs Task 14)".
- Fix: read and delete in one writable transaction once `read_only` has found the file, keyed as in P8. Then run `test_plaud_session.py`.

**P7. Tiers: Tasks 19 and 20 should be Tier 1.**
- Task 19 is the only gate on which titles reach the browser and on Status's "no titles".
- Task 20 carries decision 21's preview title and the never-sent-on-load rule.
- If either is wrong, a title leaks: the Tier 1 definition. They need the checklist.
- Tasks 9, 10, 13, 14 and 18 are correctly Tier 1.

### Minor

**P8. The preview isn't tied to its request** (Task 20, `follow`/`preview_of(raw)`; Task 6, `release_title`).
- The preview is looked up by Plaud ID only, with that ID taken from the browser's raw request, not the row.
- A reused `request_id` gets that row back from `insert`, and can name any ID.
- Fix: match the existing `release_preview.request_id` column, and take the ID from the row.

**P9. A title fits in `plaud_ids`** (Task 14, `check_request`, lines 14827-14833).
- Any printable text of up to 128 characters, spaces included, passes. So a title can be stored in `plaud_requests` and echoed back in park's `result.unknown` (line 14426).
- Fix: visible ASCII, no spaces: still opaque.

**P10. The consent watch can miss an edit** (Task 13, `_watch_consent`; Task 3, `signature`, lines 2485-2494).
- It compares inode, size and mtime, so a same-size in-place edit that keeps its mtime (`cp -p`, `touch -r`) goes unseen.
- Fix: add `st_ctime_ns`.

**P11. The runtime import guard is thin** (Tasks 4, 19).
- `test_plaud_imports` checks module loads only. Calls are probed only for `plaud_view_settings` and for importing `recordings.worker`. The views' lazy imports and `submit`→`_notice` aren't probed: clean today, but Phase 2 adds modules.
- Fix: a subprocess probe that calls every view and `submit` against a scene, then asserts no `FORBIDDEN` module loaded.
- Also: Task 9's `Writer.open` comment (line 9516) is false since decision 31.

**P12. Make expands the ID** (Task 18, Makefile lines 17449-17452).
- `$(if $(ID),…)`, and the copy make exports to the environment, both expand the value. Here, `ID='$(shell …)'` ran under make 3.81, so "never pasted into the shell line" overclaims.
- It's self-inflicted only. Fix: `$(value ID)`, or test for emptiness in the shell.

### Security checklist (my scope)

1. **Secrets:** fine. No token here; `consent-add.sh` and `ops-busy.py` read no secret.
2. **Permissions:** fine. `consent-add.sh` writes through `mktemp` (0600) in the 0700 folder, then keeps the old mode (0600 if new).
3. **Network exposure:** fine at import; runtime only partly probed (P11). Nothing listens.
4. **Failing closed:** findings P1 and P4. What holds:
   - a list caught mid-write keeps the previous one in force;
   - both ID checks refuse anything but `(of_)?<32 hex>`;
   - the worker re-checks the gate for every kind;
   - keep-here resolves only kept-out-late flags;
   - the demo and Pages always get a refusal reason, so they never insert.
5. **Injection:** findings P5 and P12. SQL is parameterised, and `'$$id'` can only be hex.
6. **Logging:** fine, apart from P9. Fixed phrases, IDs, counts and class names; no `exc_info`.

## Council report: lens 4, simplicity (M)

Read: PLAN-HEADS.md; spec decisions 10–11, Revisions, commits `e031a0e`/`1cc4a56`; bodies of Tasks 6, 12–14, 18–20.

**Size.** Most of the ~23,900 lines follow from the spec's own Phase 1 (Outline row 1: core, ops and the whole UI) written as full TDD listings. About a tenth (~2,500 lines, in Tasks 6, 12, 14, 19, 20) is Phase 2/3 code moved forward for states Phase 1 can't reach. The late rulings (decisions 18, 19, 21, 29, 38, 41, 43) and M5's cross-task bug cluster there. **The simplification pass holds:** no push code, no `alerts.send`, no extra blocker or alert rule, no new wait for Dan. The ping rule can't run in Phase 1 (the gate never opens), but the spec's Outline asks for it, so it isn't flagged.

### Important

**M1. Task 12 builds the gate's Phase 2/3 states** (Steps 1–7 and 8–12). In Phase 1 nothing writes a snapshot mark (`mark_snapshot` has no caller) and nothing writes a 2b snapshot. `Writer.add_snapshot` first appears in Phase 3's outline (plan line 23888: "A check: … `add_snapshot`"); Phase 2 imports through `Writer.add` (23876). So the interrupted state, `has_2b_snapshot`, `record_rebuild`, `seed_markers_after_rebuild`, `plaud review` (and decision 25's job), `AddSnapshot` and `validate(warnings=)` (decision 29) cover unreachable states: 1 of 11 gate tests (`test_the_gate_is_closed_the_first_time`) covers a reachable one. With no mark, every worker start scans every `source/plaud-*.json` (decision 18). Decision 43 splits "mark before every 2b snapshot" across phases only because this code arrives early. C5.19, C5.24 and C5.27's phases were the planner's call (`5b11135`).
*Fix:* Phase 1's gate is `gate_now(db)`: open when `catch_up_done_at` is set, else closed with `GATE_FIRST`; plus `GATE_REFUSAL`. Merge it into Task 13. Marks, the interrupted state, rebuild, seeding and `review` go to Phase 2's catch-up; `add_snapshot` and the orphan warning to Phase 3's check, with decisions 18, 29, 41 and 43.
*If wrong:* Phase 1 rolled back from Phase 2 onto a restored `state.db` would add `private` with "first check" instead of the rebuild note. Same tags, erring private.

**M2. Park has no effect in Phase 1, and its handler can't be reached** (Task 14, Steps 3–5). `GATE_ALLOWED` is `{compare, keep-here}`, and Task 14's own test asserts that `park` and `unpark` get `GATE_REFUSAL` (plan lines 13718–13721). `_park` and `_unpark` run only under `make_worker(gate_open=True)`. `plaud park` sets `parked_at`, which only the views read. Phase 3's outline owns "parking's effects" (23892), and nothing can fail in Phase 1.
*Fix:* move `set_parked`, the two handlers, `cmd_park`, `PARKED`/`UNPARKED`/`UNKNOWN_ID` and decision 19 to Phase 2 (Plaud IDs that failed to import) and Phase 3 (recordings). Phase 1's `HANDLERS` is `keep-here` alone.
*If wrong:* Dan can't park in Phase 1, where nothing fails.

**M3. Coalescing for request kinds that have no handler** (Task 14, Step 3, `insert`). The compare/sync takeover, the resync merge and full's cooldown are all built now. The cooldown reads `ops_runs` and `plaud_requests` for a `plaud-full` that can't exist before Phase 3, and its docstring already leaves "Resuming" to Phase 3. Decision 38's alias adds an `also` list in `result` and a `json_each` scan in every `_find`, which runs every 2 seconds per pending session. In Phase 1 every such row is refused within a tick, so Dan sees no difference.
*Fix:* Phase 1's insert checks, drops a repeated `request_id`, inserts the row and composes the notice. Each kind's coalescing lands with its handler. If an alias is still needed, return the joined row's `request_id` for the session to follow and relabel: no JSON in `result`.
*If wrong:* two quick presses of Sync get two refusals instead of one "Already queued".

**M4. The view model reads Phase 2/3 state in shapes nobody has written yet** (Task 19, Step 4: about 1,330 lines in one step). It reads `last_good_listing`'s census, `form_change` and `fields_changed`, plus `hold`, `breaker`, `full_sync`, `sign_in.expires_at`, the cache's compare rows, re-keyed rows and review counts. Its head then fixes those shapes for Phase 2/3. Spec §18 lets only "tasks that use no spike result" run before the spikes; these shapes are S1, S5 and S7 results. Only `test_each_needs_attention_entry_phase_1_can_show` is tested; the readers for hold, breaker, sign-in, disk and re-keyed rows are untested guesses behind `isinstance` guards.
*Fix:* keep `types.ts`, the canned `demo/plaud-sync.json` and Tasks 21–24, which satisfies decision 2. The Python views fill Phase 1's own data (summary, cause, blockers, flags, consent, gate, worker, ping, Needs attention) and return null or [] for the rest; each Phase 2/3 writer adds its own reader. Move all of `plaud/cache.py` (Task 6) to Phase 2 with its writer. It is derived, so decision 3's rollback argument doesn't cover it; `keyed_out` has no reader at all. Split what is left of Task 19 into two tasks: the views, then the canned view and types.
*If wrong:* Phase 2 writes readers it would have inherited; the deployed screen is unchanged ("No sync has run yet").

**M5. Release's title path is built before anything produces a title, and its halves already disagree** (Tasks 6, 20, 21). Phase 2 owns Release (23877), and decision 5 defers `plaud release` because it needs the listing's title. Yet the server-side path that carries a kept-out recording's title is built now: `Cache.release_title`/`drop_preview`, `PREVIEW_LIFE`, `release_preview`, `preview_of` and decision 21. Task 20's test hand-writes the worker's half ("Preview ready", an invented reply). `release_preview` opens `Cache.read_only(...)` (line 20822) and then calls `drop_preview` (20829). Task 6's own test says that call raises on a read-only cache (5849–5851), and `except sqlite3.Error: return None` swallows the error. The preview is always None, so 20635 fails once Part D's stand-ins meet the real `Cache`. It fails closed: no leak.
*Fix:* keep `ReleaseDialog`, fed from fixtures (C5.06, C9.03). Move the server-side title transport to Phase 2, alongside the worker's `release-preview` handler.

### Minor

**M6. Time parsing and formatting exist in five copies.** `health._when` treats a naive stamp as UTC, `worker._stamp` treats it as None, and `recordings_ui.plaud._at` treats it as UTC. `requests._hhmm`, `commands._shown`/`_zone` and 2a's `ops.when_label` format times on their own. So `due()` and Status read one `last_started` differently. *Fix:* one parse and one local-time format, in a module without sqlite3 so Pyodide can import it.

**M7. The result line is built twice:** on the server by `_last_result` with `KIND_LABELS`, and on the client by `resultLine` with `LABELS`. The client uses `toLocaleTimeString`, the browser's zone, while `types.ts` says the server formats every time in the default zone. *Fix:* drop `resultLine` and `LABELS`, and use `last_result`, which Task 20 already redraws when a request finishes.

**M8. Small pieces with no Phase 1 caller:**
- `K_RETIRED`: never read or written; `retired_at` is Phase 3's (23894).
- `upsert_listed` and `never_fetch`: only tests call them.
- The `.tmp/plaud-dl/` sweep at start: nothing downloads until Phase 2, which lists it in its outline (23876).
- `blocked_before_call`: returns None.
- The `"off"` turn outcome: unreachable in production (`due()` is False when off).
- `K_CONSENT["ids"]`: copies the whole consent ID list into `state.db`, though every reader only counts it.

**M9. Two copies of rules that already exist.**
- `keepout.fold` repeats the fold inside `models.is_private_tag`, whose docstring says "The one place the rule lives" (`models.py:182`). Export one `fold` from `models`.
- `consent_id.py` has its own `_TYPED` regex, and decision 40 lower-cases `of_`, both to keep an ID "as typed". The list already matches by canonical ID, so printing `lines[0].canonical_id` removes both; `consent-add.sh` accepts it.

**Counts:** Critical 0 · Important 5 · Minor 4.

## Council report BA: the builder, Tasks 1 to 6 (plan lines 157-6722)

Counts: Critical 0, Important 2, Minor 6.

### What I checked and found sound
- **All 92 "replace X with Y" edits in Tasks 1-6 match their real file exactly once** (script run against the worktree: Makefile, CLAUDE.md, README, runbook, both specs, root and core `conftest.py`, `state.py`, `index.py`, `test_index.py`, `test_import_plan.py`, `test_import_run.py`, `plaud/__init__.py`).
- **Task 2 Step 7's numbers hold.** I applied Task 2's edits to a copy of the main spec: `stage 2b design, 2026-10-23` is on 44 lines, `Dan, 2026-10-23` on 14, and none of the stale phrases in the Step 7 grep is left.
- **Task 1:** the Makefile in the plan keeps every old target and recipe (diff with the `## ` text stripped shows only the new targets and `.PHONY`). The `commitmsg.py` listing equals the skill's file plus the stated three changes. `test_repo_tools.py` expectations (`2 failed, 4 passed`, then 6) are right.
- **Every commit message in Tasks 1-6 passes `commitmsg.py`** (run on all seven), with `Plan:` and `Co-Authored-By:` adjacent in the last paragraph. Every `git add` names every file the task creates or changes.
- **Cross-task names agree.** Everything later tasks consume from Tasks 3, 4 and 6 exists with that name and shape: `keepout.*`, `consent.LEGACY_ENTRY_RE`, `endpoint.*`, all 18 `K_*` keys, every `PlaudDB` method called later, `NO_SYNC_YET`, `Cache.*`. The real helpers Task 5 calls (`Archive.load`, `Archive.problems`, `rec.media.sha256`, `ID_RE`, `part_hashes`, `transcript_rows`, `matches_auto_private`, `patterns_from_config`, `DEFAULT_AUTO_PRIVATE`, `load_config` reading `RECORDINGS_CONFIG`) exist with those signatures. `State.path` is the folder, as Task 5 and 6 tests assume.
- Task 3's new 2a-import test mirrors an existing one (`test_import_run.py:772`) and fails before the change with the quoted error. `recordings.plaud.endpoint` loads none of `client`, `urllib.request`, `http.client`, `ssl` (checked).

### Important

**BA1. Task 6 never makes the existing `index.db` WAL, so §20's "`index.db` is in WAL mode" is false after a real deploy.**
- Where: Task 6 Step 3, `Index._wal()`, called only at the end of `Index.rebuild`.
- Evidence: `Index.upsert` and `Writer.open` never call it. `rebuild` runs only for a new, damaged or foreign index (`writer.py:321-326`, `594-609`) or an explicit `recordings reindex`. The 2a index on the production volume is already valid, so it stays in rollback-journal mode. No later task runs `reindex`, calls `_wal`, or has doctor check `journal_mode` (grep of Tasks 7-25 finds nothing; Task 17's runbook section 6 has no reindex step). Spec line 2490 lists it as a done-criterion, and §9.5 says "`index.db` moves to WAL mode". `web` and the worker then share a rollback-mode file, which is the whole-file locking the change was meant to remove.
- Fix: in Task 6, also set WAL when a `Writer` opens an index that exists and says it is an index (header `APPLICATION_ID`), or on the first `upsert`. Add a test that opens a rollback-mode index through `Writer` and finds `wal`. Alternatively, add `recordings reindex` to runbook section 6 and a doctor line for `journal_mode`.

**BA2. Task 5's runbook 1c never gets `scripts/plaud_spike.py` onto the server.**
- Where: Task 5 Step 5 (the 1c text).
- Evidence: it says `cd "$REPO_DIR" && uv sync --frozen`, then `uv run python scripts/plaud_spike.py s1`. The server checkout is on `main`, and the script exists only on branch `stage-2b` until Phase 1 merges. Step 8 says "the close tells Dan the sessions can be scheduled". Dan would hit "No such file". Merging first is not stated either, though Phase 1 is its own PR.
- Fix: add a step before `uv sync`: `git fetch origin && git switch stage-2b` (or "after Phase 1 merges: `git pull`"), and say which one applies.

### Minor

**BA3. Task 3: a typo'd exclusion with a valid ID later on the same line is read as a *kept* line, so no consent stop.** I ran the plan's `keepout.py`: `` - `<31 hex>` — see also <32 hex> `` gives `['kept', ...]`, `stop=None`. `_classify` tries `_ANY_ID.search` over the whole line before `_is_typo`, so the typo fails open, against P8. Same for a zero-width space before an ID (kept, listed, not excluded). Fix: test the first word for the typo shape first, and call a line kept only when the first word holds no typo-shaped run. Add one case to `test_31_or_33_hex...`.

**BA4. Task 2: README, runbook section 4 and "Rolling back" say "(runbook section 8)" / "(section 8)".** Phase 1 writes only section 6 (decision 10, Task 17); sections 7 and 8 come in Phase 2. A reader in Phase 1 finds no section 8. Fix: word it "(the runbook's import section, added in stage 2b's second phase)", or say so beside the first mention.

**BA5. Task 6: `Index._wal()` can fail after the rebuild has committed.** `PRAGMA journal_mode=WAL` needs the file free of other connections. On the first conversion, while `web` is reading, it raises `database is locked` (after 30 s) *after* `rebuild` committed, so `rebuild_index` raises and skips `_catch_up_index()`. Fix: catch `sqlite3.OperationalError` in `_wal`, log a fixed phrase, carry on (WAL is an optimisation); the next open retries.

**BA6. Task 6 "Files" omits `packages/core/tests/test_import_plan.py`,** which Step 4 edits and the `git add` includes. Add it to the Files list.

**BA7. Every commit trailer hard-codes `Co-Authored-By: Claude Opus 5.5`.** The session's own attribution reminder says Sonnet 5.5. If a different model builds a task, the history misattributes. Fix: say "the Co-Authored-By line the session's attribution gives" in the commit-format section, keeping the `Plan:` line fixed.

**BA8. Small wording points.**
- Task 4 Step 2: the UI import test fails with `AssertionError` carrying the probe's `ModuleNotFoundError` in stderr (the helper asserts `returncode == 0`), not a bare `ModuleNotFoundError`.
- Task 1: the copied `commitmsg.py` tells the user to see "AGENTS.md, Commits", which this repo does not have (kept verbatim on purpose). A one-line note in Task 1 would stop a reader hunting for it.

### Spot checks that raised nothing
- Task 4: `FakePlaud` request counts (7), `Content-Length: ""` handling, the stall rule's synthetic clock, and the `.part` clean-up on every error path read correctly. Link and redirect handling matches spec §10.1 and decision 7.
- Task 5: the guard test expectations (15 tests), the print list, the `0700` folder rule, and that nothing prints a title, token or whole ID. `part_hashes` keys (`title`, `transcript`, `speakers`, `notes:<type>`, `other`) cannot leak a tab name because `s8` masks `notes:`.
- Task 6: busy-index tests flipped to `locking_mode=EXCLUSIVE` are consistent with WAL semantics. Schema text equals the Interfaces block. `State.forget`'s recording-ID derivation equals the caller's (`Path(f).parent.name`).

## Council report, lens 5 (the builder), Tasks 7 to 12: findings BB1 to BB9

Scope: plan lines 6723 to 12183. I read all six tasks in full and PLAN-HEADS.md. I checked the
before-text of every edit against the worktree. I read Task 6's `checks.py` listing (plan lines
6070 to 6481) and Task 3's and Task 4's interfaces for what these tasks consume.

I ran the Task 7 to 12 tests in a scratch copy, which has Tasks 1 to 6 and 7 to 12 as real
code, with no stubs. 109 passed across flags, marker, baseline, health, gate, CLI and settings. The
two settings failures were the template and `.gitignore` edits, which that tree lacks. The whole
core suite plus `test_template_settings.py` gave 1,218 passed and 6 failed. All 6 are the docs,
template and README edits missing from that tree (the three new runbook sections, the template's
secrets comment and `[plaud]` block, the README row); each is a Step the plan does write, and the
template, README and `.gitignore` text checked out by reading (see BB7). I could not re-run the
failing tests with the edits applied: the sandbox hook refused the command that built the copy.

**Result: no Critical. One Important, plus one Important on a wrong listing. The rest is Minor.** The
code listings match the real files, the expected-failure lines are right, every commit message is
written out with `Plan:` and `Co-Authored-By:` in one final trailer paragraph, and every `git add`
names every file the step creates or changes.

### Important

**BB1. Task 12, Step 12: the runbook's "After a lost or restored `state.db`" sequence collides with
"Restoring for real" step 4.**
- Evidence: the new section says `rc stop worker` before restoring, then `rc run --rm worker
  recordings plaud review`, then `rc start worker`. Step 1 of "Restoring for real" already begins
  with `rc down`, so the stop is moot. Step 4 ends with `rc up -d` (runbook lines 972 to 1005),
  which starts `worker` together with everything else.
- Effect: a restored `state.db` still has snapshot marks, so the gate is open once Phase 2's
  catch-up is done. The restored worker starts at step 4, before `plaud review` is recorded, and can
  run a scheduled sync that re-imports what Dan had kept out or deleted since the backup. That is
  the thing `review` exists to prevent. Harmless in Phase 1 (the gate can't open), but it is the
  text Phase 2 inherits. The test (`test_the_runbook_says_what_to_do_after_a_lost_or_restored_state_db`)
  only checks `stop < review < start` inside the new section, so it can't see this.
- Fix: say that after a restore, `plaud review` runs between step 4's `reindex` and its `rc up -d`
  (or step 4 becomes `rc up -d web backup`, then `plaud review`, then `rc up -d worker`). Extend the
  test to assert that order across both sections.

**BB2. Task 9, Step 5 (`Writer.open`): the listing, its comment and the Interfaces disagree.**
- Evidence: PLAN-HEADS Task 9 says "`Writer.open` passes `plaud_settings(cfg).keep_out_word` when
  `keep_out_word_ok`" and lists `settings.plaud_settings` under Consumes. The listing never imports
  `settings`. It reads `cfg.data` itself, with the comment "without importing that module: it brings
  the Plaud client". Decision 31 and Task 7 made that false: `settings.py` imports only `endpoint`.
- Effect: the stated reason is wrong, and the real reason is left unwritten. `plaud_settings` raises
  `ConfigError` for any other bad `[plaud]` key (for example `api_base`), and `Writer.open` runs in
  `web`, which must still open. A later reader will "fix" the duplicate logic and break the web app
  on a typo.
- Fix: rewrite the comment to say that, correct the Interfaces line, and drop the Consumes entry.

### Minor

**BB3. Task 7 to 12: "decision N" means two things.** Plan decisions 1 to 43 and Dan's spec
decisions 1 to 11 share the phrase. Task 7's "(decision 4)" is the spec's (the two checks); plan
decision 4 is `sources/base.py`. Task 9's "decision 3" is the spec's (the marker); plan decision 3 is
the tables. Task 11's "decision 10" is the spec's; plan decision 10 is "Phase 1 can be deployed".
These sit in the same files as correct plan citations (decisions 11, 12, 26, 27, 32). Say "Dan's
decision N" for the spec's, as Task 7's code comment already does.

**BB4. Task 12, `test_has_2b_snapshot_reads_past_an_unreadable_snapshot` does not test reading
past.** It has one snapshot, stamped `audio-router`, so it returns False whether or not the read
fails. It also passes trivially if the suite runs as root. Fix: add a second recording stamped
`TOOL`, sorted after the unreadable one, and assert True.

**BB5. Task 11, `test_the_ping_rule_table`: four params are the same call.** The rows "busy", "the
gate", "a sign-in pause" and "a rate-limit wait" all do `conclude_turn(ctx, None)` with no state set,
so they test nothing the first does not. The ids imply coverage that belongs to Task 13. Collapse
them into one row, or have them record the matching `record_turn` first.

**BB6. Task 7, `.dockerignore` gets `tokens*.json`, which matches only at the build-context root.**
Spec §17.4 gives the text and the test pins the literal line, so this follows the spec; but
`**/tokens*.json` would also cover a nested file.

**BB7. Task 7: a leftover `RECORDINGS_PLAUD_TOKEN`.** `packages/core/tests/test_backup.py:1032`
still passes it as an unrelated stray environment variable. It passes, but Task 7's message says the
name is gone. Rename it to `RECORDINGS_SPARK_API_KEY` in the same script, or leave it and say so.
Also, `test_ping_deadman_is_unchanged` passes before the code (it is a pin, not a red test); the
commit's "failed first" line is about the other tests.

**BB8. Task 12: `selfdoc.validate` now warns about an orphan file in `source/`, but FORMAT.md still
says "one entry per raw payload in `source/`".** A short "an unrecorded file is a warning and is
adopted by the next snapshot of the same bytes" in `format/FORMAT.md` (and `demo/archive/FORMAT.md`,
byte-compared) would keep the archive's own doc true. Non-JSON `recordings validate` prints the
warnings as a Python list repr, as `problems` already does.

**BB9. Trailer model name.** Every commit ends `Co-Authored-By: Claude Opus 5.5`. `commitmsg.py`
doesn't check it, but an implementer on another model will write a wrong name if it copies the plan.
Tell the dispatcher to use the session's own attribution.

### Cross-task checks (all agree)

- Task 7 produces `alerts.ping_url`, `AlertConfig.deadman_sync_url` and `settings.BAD_WORD` /
  `plaud_settings`. Tasks 11, 13, 15, 17 and 19 call them with the same names and signatures.
- Task 8 `LateReport`, `SENTENCES`, `keep_here`, `PRIVATE_UNWRITTEN` match Task 13's and Task 14's use.
- Tasks 10 to 12 `enforce_word`, `patterns_pass`, `plaud_run`, `plaud_holder`, `gate_at_start`,
  `gate_now`, `GATE_REFUSAL`, `record_turn`, `open_blocker`, `conclude_turn`, `TurnContext` match
  Tasks 13 and 14. The `K_LAST_PING` `failed_since` and `K_LAST_TURN` shapes match Task 19.
- Task 6's `set_listing` (decision 36) and `set_check` upsert match how Tasks 8 and 11 call them
  (`set_listing` gets a datetime, `set_check` an ISO string; both are encoded).
- The runbook parse test skips `rc … worker recordings …` lines until Task 17 adds `worker` to its
  prefix list, so the worker commands in Tasks 8, 11 and 12's runbook text are unparsed until then.
  That is fine, and the plan says so for Task 8.
- Runbook text in Task 11 names `make plaud-sign-in`, which doesn't exist until Phase 2. It is the
  spec's wording, and the sign-in blocker can't open in Phase 1.

## Council report BC: the builder, Tasks 13 to 18 (plan lines 12184-17622)

Method: read Tasks 13-18 in full, checked every "before" text against the worktree (Makefile, compose.yml,
deploy.example.conf, recordings.service, doctor.py, backup.py, cli.py, state.py, locks.py, alerts.py,
test_doctor.py, test_backup.py, test_deploy_template.py, test_runbook.py, test_ops.py, runbook, README,
CLAUDE.md, config.example.toml, compose_smoke.py), and every call into Tasks 3-12 against those tasks'
own listings in the plan (not the heads). Counts: 0 Critical, 2 Important, 6 Minor.

### Important

**BC1. Task 14 Step 1, `test_park_unpark_and_keep_here_handlers`: two assertions compare a `datetime` to a string and cannot pass.**
Evidence: the test asserts `w.db.check_row(rid).parked_at == T0.isoformat()` and
`w.db.listing(PLAUD_B).parked_at == T0.isoformat()` (plan lines 13769-13770). Task 6 defines
`CheckRow.parked_at: datetime | None` and `ListingRow.parked_at: datetime | None` (plan lines 6161-6176),
and `_TIMES` contains `"parked_at"` so `_decode` returns `datetime.fromisoformat(...)` (lines 6191-6245).
A correct implementation therefore fails the test, and the implementer is told to make it pass.
Fix: compare with `T0` (`... .parked_at == T0`). The CLI test (13812-13816) uses `is not None` and is fine.

**BC2. Task 17 Step 6 puts `## 6. Deploy 2b` where Task 17's own test (and Task 2) say it must not go.**
Evidence: the test asserts `headings.index("## 6. Deploy 2b") == headings.index("## 5. The mirror on the Mac") + 1`
(plan 16222). The step says "After section 5 ..., before `## Tagging`" (16666). Task 2 Step 5 (plan 825-851)
has already inserted `## Setting up ntfy, later` immediately before `## Tagging`, so after Task 2 the order is
5, Setting up ntfy, Tagging. An implementer following the words "before `## Tagging`" lands between ntfy and
Tagging and the heading index is +2: the test fails. Fix: say "immediately after section 5's last line, before
`## Setting up ntfy, later`" (and name the anchor line in the "replace" form the other steps use).

### Minor

**BC3. Step 2 "expected failure" lines are wrong in three places, and two are repeated in commit bodies.**
(a) Task 14 Step 2 and its commit body say `ModuleNotFoundError: No module named 'recordings.plaud.requests'`.
The test's first import is `from recordings.plaud import requests`, which raises
`ImportError: cannot import name 'requests' from 'recordings.plaud'`. (b) Task 18 Step 2 says the make-targets tests
fail on the "not a Plaud ID" assertion with `No rule to make target`. The `server` fixture copies
`docker/consent-add.sh` first, which does not exist yet, so every test ERRORS in setup with `FileNotFoundError`.
(c) Task 16 Step 2 lists `test_the_backup_never_carries_plaud_cache_db` and the `/state/manifests` test in the `-k`
run as if they fail first; both are pins that pass before the code (fine as pins, but the commit body's "New tests failed
first: BackupRefused" is true of the others only). Fix the three lines; say which tests are pins.

**BC4. Task 16's folder support is never run against real restic, and the smoke is the natural place.**
`scripts/compose_smoke.py` (Task 17's whole-file listing) keeps `extra_paths = ["/backup-extra/deploy.env"]`
although Task 17 mounts `/consent` in `backup`. The unit tests use `FakeRestic`. The plan's own risk (decision 9: "a missing
or non-file extra path refuses every backup") would be proved by adding `"/consent"` to the smoke CONFIG's
`extra_paths` (the folder is empty there, and restic restores empty directories). It also silences the new
"consent folder is not in [backup] extra_paths" warning that the smoke's `doctor --role backup` now prints.
Optional; not a blocker.

**BC5. P6 ("answers within a second") is only tested for a missing state.db.**
`submit` has no timeout. `State._connect` uses `timeout=30` (state.py:208), so a locked state.db leaves the press
waiting up to 30 s. Review Focus 5's test (`State(tmp_path / "unmounted")`) passes because the file is missing. Either wrap
the `to_thread` in `asyncio.wait_for(..., 1.0)` with a NOT_SAVED answer on `TimeoutError`, or state in a comment that the
promise covers an unusable state.db and not a busy one.

**BC6. The Makefile comment above `deploy` is left wrong.** Makefile:68-69 says `--wait` fails "when web doesn't pass its
health check". Task 17 adds the worker's and updates README but not this comment (not in Task 17's Files, not in the
`git add`). CLAUDE.md-style rule: wrong docs are fixed in the change that made them wrong.

**BC7. Task 18's `test_consent_add_expands_server_and_repo_dir_on_the_mac` can be disturbed by the caller's environment.**
`server.env` is a copy of `os.environ`. A `SERVER` or `REPO_DIR` exported in the developer's shell is a make variable and
defeats the "refused: no local.mk" half (`$(if $(SERVER),,$(error ...))` would not fire). Pass `env` without those two names
(or `-e`-free: `env.pop("SERVER", None)`, `env.pop("REPO_DIR", None)`).

**BC8. Every commit trailer hard-codes `Co-Authored-By: Claude Opus 5.5`.** The attribution line is the implementing
model's, and this session's reminder says Sonnet 5.5. Say once in "The commit format" that the trailer is the implementer's
own, or `commitmsg.py` and the plan disagree with the harness. All seven commit messages otherwise have `Plan:` first in a
single final trailer paragraph, with no blank line before `Co-Authored-By:`.

### Checked and fine (no finding)

- **Interfaces across tasks.** Task 13 calls `apply_late_consent`/`LateReport` (Task 8), `gate_at_start`/`gate_now`/`Gate.reason`
  (Task 12; `gate_now` returns "first-time" with no mark, so the test's `GATE_FIRST` detail is right), `plaud_run`/`plaud_holder`
  (Task 10), `enforce_word`/`patterns_pass().sentence()` (Task 10), `TurnContext` positional order, `conclude_turn`, `record_turn`
  (dedupes a repeated outcome and keeps the first `at`, which the test relies on), `open_blocker`/`clear_blocker` (Task 11),
  `AlertConfig(ntfy_server=, topic=, deadman_url=)` (real alerts.py:103). K_CONSENT / K_LAST_STARTED / K_LAST_ENDED shapes agree with
  Task 8's `cmd_consent` and Task 19's readers (plan 8466, 18811-18816, 20856). Task 14's `set_parked` uses `db.listing`/`set_listing`/`set_check`
  as Task 6 defines them (`set_check` upserts). Task 15-18 use `typo_phrase`, `plaud_settings`, `BAD_WORD`, `DEFAULT_TOKEN_STORE`,
  `CACHE_NAME`, `ping_url`, `AlertConfig.deadman_sync_url`, `parse_consent().lines/.typo_lines`, `canonical_plaud_id` as the earlier tasks define them.
- **Before-texts match the real files**: doctor.py (docstring, imports, ROLES/JOB_SECRETS block, `_state`'s `missing` line, the
  `role == "web"` and `role != "web"` blocks, `if role == "backup": _extra_paths`), backup.py (docstring, `run_backup` loop,
  `_check_scratch_space`, `_not_in_snapshot`), cli.py (import line 15, doctor help line 86, both `cmd_alert_test` hunks, `alert-test`
  then the abbreviation loop), compose.yml/deploy.example.conf/recordings.service, the README bullets and `make deploy` line,
  config.example.toml lines 60-61, CLAUDE.md lines 119-123, the runbook's intro, section 3's paragraph and server block, section 4's
  opening and block, section 5's last line, the existing test_doctor/test_deploy_template/test_runbook hunks the plan edits
  (including the fake-docker scripts and `ops_lock_held`).
- **Test logic traced**: Task 13's 19 tests (the clock, `_not_before`, signature-race, 15/30-minute `alive` arithmetic, SIGTERM,
  the heartbeat thread); Task 14's 12 core tests (aliases, `also`, coalescing, `BEGIN IMMEDIATE`, expiry keeping the message,
  the UTC 04:55 conversion); the UI tests; Task 15's doctor cases (including that `test_the_worker_has_no_alerts_off_warning` is
  not hurt by missing ffmpeg because the file's autouse `tools_installed` fixture patches `which`); Task 16's byte arithmetic and
  `-k` selection; Task 17's compose tests against the YAML; Task 18's guard/probe tests and the shell scripts (POSIX, `set -eu`,
  mode copy, rename, trap).
- **`git add` lists** name every file each commit touches (Task 14's two commits split core and UI correctly).
- **No placeholder or "similar to Task N"**; the only elisions are Task 18 Step 9's `…` markers inside an existing runbook block,
  which say the other lines stay.

### What the scratch run could not catch, and I did not find wrong
Expected next steps for the reviewer of the other builders: Tasks 7-12 are where these six depend most; the places a mismatch would
bite Tasks 13-18 are `PlaudDB.parked_at` typing (BC1, found), the consent parser's typo-before-no-IDs order (checked: typo wins, as Task 15's test needs),
and the runbook heading order (BC2, found).

## Council lens 5 (the builder), Tasks 19-24 (plan lines 17623-23847)

Method: read all six tasks in full. Checked each name, `plaud_state` key, event kind and column that
Tasks 19-24 use against Tasks 6-14's listings in the plan (`checks.py`, `cache.py`, `health.py`,
`gate.py`, `worker.py`, `runlock.py`, `requests.py`, `plaud.py`, `flags.py`, `privacy.py`,
`keepout.py`, `settings.py`) and against the real files in the worktree. Read-only; nothing run.

Counts: Critical 0, Important 3, Minor 8.

### What checked out (no finding)

- Every Task 19-24 Python name resolves to a Task 6-14 definition with the signature used: `PlaudDB`
  (`get`, `put`, `put_many`, `set_check`, `set_listing`, `upsert_listed`, `add_event(open_flag=)`,
  `open_flags`, `events_since`, `listing`, `check_row`), the `K_*` keys, `NoSyncYet`, `no_such_table`,
  `NO_SYNC_YET`, `Cache.read_only`/`compare_rows`/`summary`/`release_title`/`drop_preview`,
  `CompareRow` fields, `sync_health` (`.blockers` is name to datetime; `.failing`, `.last_turn`
  and `.last_ping` shapes), `SyncHealth.cause(tz=)`, `record_run`/`RunOutcome` (positional order and
  keywords), `record_turn`, `open_blocker`, `BLOCKER_PHRASES`, `gate_now`/`Gate.open`/`.reason`/
  `GATE_FIRST`, `plaud_run(state, job, *, timeout, now)`, `plaud_holder`, `heartbeat_once`/`Progress`/
  `alive`/`HEARTBEAT_KEY`, `typo_phrase`, `why_phrase` (all four whys land in `WHY_DETAILS`),
  `SENTENCES`/`KEPT_OUT_LATE`/`CONSENT_LATE`/`PRIVATE_UNWRITTEN`, `PlaudSettings` fields
  (`schedule_minutes`, `full_sweep_days`, `auto_import`, `keep_out_word_ok`), `requests.read`/
  `claim_next(...).request`/`progress`/`finish`, `check_request`/`Refusal`/`submit`/
  `cannot_request_reason`, and the shapes Task 13 writes (`K_CONSENT.ids` as a list,
  `K_LAST_TURN.detail.result`).
- Table columns queried (`plaud_listing`, `plaud_checks`, `plaud_check_log`, `plaud_events`,
  `plaud_requests`, `revisions.body/written_at/committed`, `ops_runs.job/finished_at/detail`) all
  exist in Task 6's schema and the real `state.py`.
- Before-text of the edits matches the real files: `status.py`, `views.py` (`recording_view`
  signature, docstring, `status_view`), `runtime.py`, `app.py` (`create_app` head), `shiny_app.py`
  (the replacement keeps every existing line), `settings.py` (`copytree`), `StatusPage.test.ts`,
  `RecordingList.test.ts`/`TagsPage.test.ts` mock lines, `nav.ts` (comment and first lines),
  `TopBar.tsx`, `app.css` anchors (`The picker's dialog...` comment; the second 760px block ends at
  `.selection-bar`), `build_pages.py` and `pages/app.py` diffs, CLAUDE.md/README/Makefile/pages.yml
  lines. Every new CSS rule is inside one of the two existing `max-width: 760px` blocks; no third.
- UI rules: both new outputs are subscribed in `App`; `plaud_request` and `plaud_result` live in
  `usePlaud`, mounted once in `App`; nothing is sent on load/hash/storage; no `stopPropagation`;
  equation numbers are buttons, not hash links.
- No placeholder or "similar to" in the range. All six commit messages are written out with
  `Plan:` and `Co-Authored-By` as one final trailer paragraph; each `git add` names every file the
  task's text edits (Task 19 includes `conftest.py`, `test_demo_guard.py`, `Dockerfile`).
- No Python line over 100 characters in the range.

### Important

**BD1 - Release's question can never get its title: `release_preview` deletes through a read-only
cache. (Task 20 Step 4; Task 6 `Cache`.)**
`release_preview` opens `Cache.read_only(settings.state_path)`, then calls `cache.drop_preview(...)`.
Task 6's `Cache._connect` opens a read-only cache with `mode=ro`, so the `DELETE` raises
`sqlite3.OperationalError: attempt to write a readonly database`; the `except sqlite3.Error` returns
`None`. So `preview` is always `None`, and `test_a_release_preview_reply_carries_its_title_to_that_session_only`
fails on `final["preview"] == {...}` and on `release_title(...) is None`. In use, Release answers
"Preview ready" and never asks.
Fix: read the title with the read-only cache, then delete with a writable `Cache(settings.state_path)`
(only when `Cache.read_only` found the file, so web never creates it), and say so in the docstring;
or have `drop_preview` open its own writable connection. Web's `/state` is read-write (Task 14), so
this is allowed.

**BD2 - Copy ID does nothing on the plain-HTTP page the app is served on. (Task 21 `CopyId`.)**
`navigator.clipboard?.writeText(...)` is `undefined` outside a secure context. `lib/queue.ts` says
"the app may be served over plain HTTP on the tailnet", and Review Focus 4 (Copy ID pasted into
`make consent-add`) depends on this button. On HTTP the click is a silent no-op (no "Copied", no
error), so the consent flow's first step fails and Dan sees nothing. jsdom has no clipboard, so no
test catches it.
Fix: fall back to `document.execCommand("copy")` on a temporary, selected `<textarea>`, and when
both fail show the text as selectable (`Copy failed: select it`). Add a Vitest for the
no-`navigator.clipboard` path.

**BD3 - Needs attention omits "a sign-in expiring within 7 days". (Task 19 `_panel_from`.)**
Spec §15.2 lists "a sign-in needed, or one expiring within 7 days" under Needs attention. The plan
reads `sign_in.expires_at` only for the numbers line ("signed in - expires ...") and builds an
attention line only for the `sign-in` blocker. Phase 2 will write `expires_at`, and nothing will warn
before the token lapses (the first signal is then the 2-hour Healthchecks email). The Phase 2/3
"must write" list also doesn't note that an attention rule is owed.
Fix: add `if expires and 0 < expires - now <= 7 days: attention.append("Plaud sign-in expires <date>:
at the Mac, make plaud-sign-in.")` with a test in `test_each_needs_attention_entry_phase_1_can_show`;
the key is already specified, so it is testable now.

### Minor

**BD4 - ReleaseDialog returns focus to `<body>`. (Task 21.)** `ConfirmDialog` uses `useReturnFocus`
because Radix returns focus only to a Trigger and this dialog opens on a reply. `ReleaseDialog` opens
the same way and omits it (Cancel and Let it in both lose focus; WCAG 2.4.3, which the project fixed
once in Task 15 round 1). Spread `useReturnFocus(() => <the Release button>)` on its content.

**BD5 - Status's "fail every check" line has no Re-sync/Park. (Task 19 `conditions`, Task 22.)** Spec
§15.2/§296: "1 recording fails every check - Re-sync - Park". The plan writes plain text. State in the
plan that Status reports only (it does say that for Task 22) or link to the Sync screen's Parked/Not
here lists; today the sentence offers no next step.

**BD6 - "Show title" on a review row gives no feedback. (Task 21 `showTitle`.)** The reply is
`release-preview`, which `usePlaud` excludes from toast and kept line, and `SyncPage` shows an
`answer.message` only for the Release row (`asking`). If the worker refuses it (gate closed, as in
all of Phase 1) or errors, the press looks dead. Show the message beside the row, as the Release row
does.

**BD7 - `picked` is never pruned. (Task 21.)** After Import selected, or when a redraw drops a row,
"Import selected (N)" still counts IDs that are gone. Clear `picked` after sending, and intersect it
with `view.not_here` on each render.

**BD8 - The phone selection bar may wrap with Re-sync added. (Task 23 `.resync-long`.)** The bar
already carries "N selected - Tag... T - Clear Esc - just tagged work" at 320 px; "Re-sync" adds about
60 px, and the existing short-screen e2e tests measure that bar's height. `make e2e` will show it;
note it as the first thing to check, with the fallback of dropping "just tagged" on a phone.

**BD9 - `from_env` rebinds its own parameter. (Task 19 Step 6.)** `demo = find_demo_archive(environ)`
inside `from_env(environ, *, demo: bool | None)` shadows the boolean. It works (the branch uses the
literal `True`), but it reads as a bug. Name it `committed`.

**BD10 - Two edits lack a quoted before-text. (Task 21 Step 4.)** "and `hashFor` with:" and the
`App.tsx` conditional are replaced by description. They are unambiguous against the real files, but
Task 19's `app.py` import ("add ... to the `recordings_ui` imports") also gives no position and `I001`
is on; say "between `hosts` and `settings`" and "sorted: `PlaudViewSettings, cannot_request_reason,
demo_view_settings, plaud_view_settings`" (Task 20).

**BD11 - Three Task 24 guard tests pass before their task.** The plan admits it. They guard
Tasks 14, 19, 20; fine, but the commit's "failed first with" line is true only for
`check_inputs() ... 'view'`. Keep the sentence as is.

**BD12 - CLAUDE.md's UI rule text is now incomplete.** "Every message handler and input lives in
`App` (through `useTagging`)" and "Tag requests are only ever sent by a person's action" don't name
`usePlaud` or `plaud_request`. Not wrong, so it may wait for the phase end, but the phase-end list
(Task 25) should carry it: add `plaud_request`, `usePlaud`, the two new outputs, and "Copy ID needs an
HTTP fallback".

### Test fail-before/pass-after notes

- All Vitest files fail before (missing module/export) and pass after as written; I traced each
  assertion against the component markup (testids, button text such as `Import all 31`,
  `Released - comes in ... - Undo`, `data-request` counts, ids for `aria-describedby`).
- Python view tests trace correctly against the specified `PlaudDB`/`health`/`gate` behaviour
  (summary sentences, `14:35` next sync, `13:40` not-pinged cause, 24-hour schedule-off, stuck holder
  at 3 h vs 12 h, the Details/removed-private dates via the `revisions.written_at` update).
- The canned view JSON has exactly the keys of `_empty_sync` (21) and `_empty_panel` (11), and its IDs
  fit `of_0{24}[0-9a-f]{8}`.
- `test_a_release_preview_reply_carries_its_title...` is the one that cannot pass (BD1).
