# Stage 2b: Plaud flows in and keeps up (design)

- **Date:** 2026-10-23
- **Status:** draft for the council's review. Dan's seven decisions, below, are binding and are kept
  as he gave them. The rest is the controller's design, drawn from a five-member research council:
  spec archaeology, the codebase, Plaud's API and reliability, the project's spirit and Dan's
  experience, and architecture, operations and security. Where the controller decided something
  without asking Dan, the text says **(controller's call)**, and §3 lists every such call so Dan can
  check each one.
- **Parent:** `docs/superpowers/specs/2026-10-08-recordings-design.md` (the main spec), §20 stage 2b.
  §14 lists the changes this spec makes to the main spec and to other docs.
- **Read first:** §1 (what this is for) and §4 (how it should feel). Every other section is written
  against them.

## Dan's decisions (2026-10-23)

He answered each question as a scenario, choosing from three options. His choice is quoted as he picked it.
He gave no further reasons, so the reasons below come from the options as they were put to him.

1. **A new recording with a placeholder title: "Download now, tag later"** (not the recommended "wait up
   to 30 min").
   - A new recording is downloaded at the next sync even while its Plaud title is still a placeholder.
   - If Dan later renames it to a title that matches his private patterns, `private` lands at the next
     sync. Each sync re-checks the titles of recent recordings, so the rename lands within one sync.
   - This is a deliberate exception to "the private tag lands before any source or output": until the
     rename is synced, a placeholder-titled recording sits untagged in the archive, the backups and the
     mirror. An untagged recording is already hidden from external agents (main spec §7.4).
2. **Keeping a recording out from the phone: "Title marker"** (recommended).
   - A Plaud title that starts with a word Dan chooses (configurable, for example `noarchive`) is never
     downloaded. The Sync screen lists it as kept out by title.
   - If the sync downloaded it before the rename, the next sync tags it `private` at once and flags it
     for Dan to delete by hand.
   - Deleting the recording in Plaud's app, or adding its ID to the consent list, still work too.
3. **A `private` tag Dan removed: "Stays removed"** (recommended).
   - The sync remembers which Plaud title it last checked for each recording, and re-checks only when
     that recording's title changes in Plaud. Then it may re-tag the recording, with a note saying why
     ("re-tagged private: title changed").
   - This fixes the auto-private residual carried from stage 2a, which tagging part A made visible.
4. **A dead sync worker: "Second check"** (recommended).
   - The sync gets its own Healthchecks dead-man's check, pinged after every good run, with its own
     secret file. It isn't shared with the backup's check, so neither job can hide the other's death.
   - It is a one-time setup step.
5. **Plaud's routine errors: "Push if it lasts 2h"** (recommended).
   - There is no push for a short blip. A push comes when there has been no good sync for about 2 hours.
   - A refused sign-in (a refresh Plaud refuses), a tripped circuit breaker and a consent stop push at
     once.
   - Status shows every error the whole time.
6. **Speaker fixes in bulk: "Re-sync a selection"** (recommended).
   - Select rows in the Library (the selection from tagging part A) and press **Re-sync from Plaud**
     once: one request, with progress.
   - The background sweep, per-recording Re-sync and **Sync everything** stay.
7. **When tagging the real archive may start: "After import + backup"** (recommended).
   - This refines Dan's 2026-10-22 decision. The first real tag waits for step 7's first real Plaud import
     and the backup after it, not just for the dry run, because the dry run only reads.
   - A rollback of step 7 then loses no tags. The README and the runbook change to say so.

## 1. What this is for

`recordings` is where Dan's recordings live for good: on his own hardware, as plain files he can
read, with Dan, not a vendor, deciding what each one is and who may read it (main spec §1). Plaud is
how he records today. Plaud's cloud is rented, and the subscription ends on 2027-08-29 (main spec §22,
decision 7). Stage 2b is where Plaud's recordings start to flow into the archive on their own, and
keep flowing.

### Why now

- **The old loop broke, and nobody was told.** Since about 2026-09-25, Plaud's list endpoint has
  returned IDs as `of_<32 hex>`. `audio-router` checks them against `[0-9a-f]{32}`, so its sync has
  rejected every row since then (main spec §19). By 2026-10-08 about 30 recordings were missing from
  the archive, and the newest vault note was from Sep 14. Dan chose no stopgap. The recordings are
  safe on Plaud, and this sync fills the gap.
- **The relabelling period starts.** Dan is fixing speaker names in Plaud's app before the
  subscription ends (DAN-15, due 2027-08-15), and he expects those fixes to come down (main spec
  §9.1.1).
- **Tagging is waiting.** Tagging part A has shipped. The real archive gets its first tag only after
  2b's first real import and the backup that follows it (decision 7).

### What success feels like

- Dan records a meeting. By the time he sits down, it is in the Library's Untagged list, with
  Plaud's title, and Plaud's transcript if Plaud has made one. He didn't click anything.
- He never opens Plaud's app to check that something arrived. Status says "All here" in one line,
  with the time it was last true.
- A private title arrives private. A recording he keeps out from his phone never arrives. A `private`
  tag he removed stays removed.
- A speaker he renames in Plaud shows up by the next day, or at once if he presses **Re-sync from
  Plaud**.
- When something breaks, his phone tells him once, and says what to do. Otherwise it is quiet.
- Keeping it running is almost no work. If Plaud ever signs the server out, signing in again is one
  command on the Mac.

### What would make 2b a failure, even if every test passes

1. **A recording that must never be kept is kept.** Someone who didn't consent ends up in write-once
   media, in append-only backups and in the Mac's mirror. No fix undoes that.
2. **It stops again, and nobody notices.** This would be the `audio-router` story told twice: an
   expired sign-in, a dead worker, a tripped breaker or a schedule left at 0, silent for weeks.
3. **Dan still checks Plaud by hand.** Then the app adds a chore instead of removing one.
4. **Alert fatigue.** Pushes for Plaud's routine blips teach Dan to mute the topic, and then the push
   that matters goes unseen.
5. **His decisions don't hold.** A tag he removed comes back. A rename never shows, or shows with
   nothing to say when it will.
6. **Upkeep becomes a chore.** The sign-in needs attention every few days, or the server's Plaud
   session fights with the Plaud tools Dan uses on the Mac.
7. **Plaud's copy changes.** Plaud is the source of truth until 2027-08-29. A bug that renamed or
   deleted anything there would be unforgivable, so the client can only read.
8. **There is no way back.** A 2b deploy that can't be rolled back by its tag would break a promise
   that stage 2a made (main spec §20).

## 2. Scope

**In 2b** (main spec §20, with Dan's decisions):
- the Plaud client, read-only, with the `of_` fix (§6)
- the token store and its refresh, and a one-step sign-in from the Mac (§6.4, §6.5)
- a fake Plaud server for tests (§6.6)
- the `worker` container: the scheduler, the UI's requests, and the lock that allows one sync at a
  time (§5)
- the compare, and importing what is missing (§7)
- the consent list, the title marker that keeps a recording out, and auto-private (§7.3, §8)
- edit-sync: the rolling sweep, the circuit breaker, the baseline, Re-sync for one recording or a
  selection, and **Sync everything** (`--full`) (§9)
- the Add page and its Plaud panel, the Status sync panel, Re-sync in the Library, and the arrivals
  cue (§10)
- alerts, including the sync's own dead-man's check (§11)
- the compose service, doctor, deploy, rollback and runbook changes (§12)
- the supervised spikes with Dan (§13)

**Left for later:**
- **Stage 3a:** the review queue (main spec §12.1), where held removals can be accepted (until then
  they are counted, §9.7); the Library on `index.db` (unless the runbook's 2-second check forces it
  earlier, §18); live updates across browser windows beyond the arrivals cue.
- **Stage 3b:** People, linking Plaud's speaker names to people, and "Plaud now says X".
- **Stage 4:** the processing queue and the tag gate. The 2b worker does only Plaud work. Stage 4's
  queue builds on its process, its request table and its loop (main spec §20, stage 4).
- **Stage 5:** upload, URL and the watched folder. "Import your own audio" stays a placeholder.
- **Stage 6:** *Fix in Plaud* and the Plaud-against-home comparison.
- **Not in 2b:** signing in to Plaud from the phone (§6.5); anything that writes to Plaud;
  network-level egress control (§12.1); importing `audio-router`'s deferred rows that Plaud no
  longer lists (§7.2); deleting a recording from the app (the runbook's manual step stands).

**If 2b runs long.** Main spec §20 lets edit-sync ship after the compare and the import. Cut it in
two:
1. **First:** the client, the token store and sign-in, the fake server, the worker and its requests,
   the compare and the import, the consent list, the title marker, auto-private **including the
   title path of §8.1** (decision 1 relies on a renamed title reaching `private` within one sync),
   the alerts, the Add panel, and the Status lines for the listing, the sign-in and failures.
2. **Then:** the sweep, the breaker, the baseline command, Re-sync, `--full`, and the rest of the
   Status panel.

Both halves add `state.db` tables only (§5.5), so either one deploys and rolls back by its tag.

## 3. Settled before design

### Settled by the evidence

The council agreed on these, so they were not put to Dan:

1. **The worker gets its own `Writer`.** "One writer" means one machine, not one process. The
   sentinel and `writer_id` enforce it per machine (main spec §3). Processes coordinate through the
   `flock` locks, `state.db` in WAL mode and `Writer.mutate`, which main spec §6.4 designs for: "the
   web process, the worker, the CLI and Claude's edits write safely side by side". Tagging part A's
   "one `Writer` per process" still holds. Main spec §20's "All Plaud calls and writes happen there"
   narrows to "every Plaud call, and every write that comes from Plaud" (§5.1).
2. **The UI asks the worker to act through request rows in `state.db`,** sent over the existing
   guarded websocket. There is no new HTTP route (§5.3).
3. **`state.db` gains new tables only.** A higher `SCHEMA_VERSION` would make an older release
   refuse the file, which breaks `make rollback` (§5.5).
4. **The circuit breaker counts over a rolling 24 hours,** and a separate guard catches a flood of
   Missing recordings, the `of_` lesson. As written, the breaker could never trip: a run checks about
   5 recordings, and the breaker needs 10 (§9.4, §7.5).
5. **An alert is raised only when Plaud refuses a refresh,** not on every 401. An access token that
   expires and refreshes is routine. The done criterion becomes "a refused refresh raises an alert"
   (§6.4, §15).
6. **The stale config goes.** The static `RECORDINGS_PLAUD_TOKEN` secret (`config.py:33`,
   `config.example.toml:134`) is removed. `recheck_days` becomes `full_sweep_days`. The `[plaud]`
   keys that no code reads get readers (§12.4).
7. **The consent-list parser is fixed.** It must accept the `of_<hex>` form and upper case, instead
   of skipping them silently (`consent.py:17`, §7.3).
8. **The worker needs** its own alert secrets (ntfy), a `worker` role in doctor, the `/index` mount
   and a read-only mount of the consent list (§12).

### The controller's calls, for Dan to check

These are builder's calls with effects Dan will feel. Each is marked **(controller's call)** where it
is specified.

1. **The token's home.** The Plaud sign-in lives in its own host folder, `/srv/recordings/plaud`
   (mode 0700), mounted only in `worker`. It is not under `state/`, where `web` and `backup` could
   read it (§6.4).
2. **Signing in again** happens on the Mac, with one command, `make plaud-sign-in`. There is no phone
   sign-in in 2b (§6.5).
3. **Speaker renames** show within `full_sweep_days`: 1 day while Dan relabels, otherwise the default
   of 7. Revisit if spike S5 finds a cheap change marker (§9.2, §9.3).
4. **A recording deleted in Plaud** is kept forever, and shown as "only in the archive" (§7.7).
5. **The first catch-up** is a supervised CLI run that holds `ops`, between a `pre-import` and a
   `post-import` backup. The schedule is turned on after it. Scheduled runs take per-recording locks
   only (§7.6, §5.4).
6. **Removals waiting for review** are counted on Status's sync panel and in the Sync screen's
   Updated list, with no Accept button until 3a (§9.7).
7. **Importing asks no confirmation.** A toast reports the counts: "31 imported · 4 arrived private
   · View" (main spec §12.2). The dry run is the first import's confirmation (§7.4, §7.6).
8. **The second-sign-in question** is put to Dan only if spike S3 shows that signing in on one side
   signs the other out (§13).

Further calls made while writing this draft:

9. **The auto-private marker lives in `state.db`,** as a hash of the title, not in `recording.json`.
   The archive's models refuse unknown keys (`extra="forbid"`, `models.py:56`), so a new key would
   leave a pre-2b release unable to read the archive after `make rollback` (§8.2).
10. **A listed title is enough to add `private`.** The sync doesn't wait for the recording's fetch,
    so a fetch that keeps failing can't leave a private title untagged (§8.1).
11. **The equation's words.** "Waiting" means the sync will bring the recording in by itself.
    "Missing" means it won't without Dan (§7.2).
12. **Titles in the Add panel.** Recordings not yet here show their titles, with a lock on those that
    will arrive private. Recordings kept out show only their ID, date and duration (§10.1).
13. **A consent stop stops the whole run,** not only its imports, while a consent-listed recording
    is in the archive (§7.3).
14. **The breaker is reset from the CLI only,** because resetting it without a fix would trip it
    again (§9.4).
15. **The flood guard pushes at once,** as a breaker trip (§7.5, §11).
16. **The sync's Healthchecks check** has a period of 20 minutes and a grace of 3 hours, and gets no
    `/fail` (§11).
17. **`audio-router`'s deferred rows that Plaud no longer lists** stay in its frozen archive. 2b
    counts them and doesn't import them (§7.2).
18. **Re-sync of a selection** is capped at 1,000 recordings (§9.6).

### Contradictions settled

Each was found by a council member. The settlement is here, and the section named applies it.

- **"All writes in the worker" against tagging part A's web writer** (main spec §9.1, §20 against
  tagging part A §5): narrowed to Plaud's calls and Plaud's writes (above, 1; §5.1).
- **Where the token lives** (main spec §5 and §6.8 say `state/plaud/tokens.json`; `config.py:33`
  says a static secret; `/state` is mounted in every container as the same user): its own folder,
  mounted in `worker` only (§6.4).
- **One dead-man's check for the backup and the sync** (main spec §14): two checks (decision 4, §11).
- **`recheck_days` against `full_sweep_days`** (template against main spec §5, §9.1.1):
  `full_sweep_days` (§12.4).
- **The breaker's unit** ("of a sweep", main spec §9.1.1): a rolling 24 hours (§9.4).
- **The grace period against "the private tag lands before any source or output"** (main spec §9.1,
  §22 decisions 19 and 35): decision 1 removes the grace and makes one stated exception (§8.3).
- **"A 401 raises an alert"** (main spec §9.1, §14, §20): a refused refresh raises it (§6.4).
- **"Matches … by content hash"** in the compare (main spec §9.1): the listing has no hash, so the
  compare matches by ID, and the hash match happens at import (§7.2).
- **Two containers** (main spec §3) against three (`web`, `backup`, `worker`): three (§5.1).
- **Sync state on Add** (main spec §12.4) **and on Status** (main spec §9.1.1, but absent from its
  §12.5): Add acts, Status reports, and both show one shared summary line (§10).
- **Link lifetime, 300 seconds** (main spec §9.1.1) **or 24 hours** (the `plaud-shared` skill): every
  link is treated as short-lived, and spike S8 measures both (§6.3).
- **The field-name diff "would have caught the `of_` change"** (main spec §9.1.1): it would not,
  because the field was still called `id`. Status also shows a census of ID forms (§6.2).
- **`deferred-to-2b` rows** (main spec §9.3 mapping) **against the runbook deleting `import/`**
  (step 4): §7.2.
- **The consent list in a long-lived service** (doctor treats it as a note; compose doesn't mount
  it): required and mounted read-only in `worker` (§12.1, §12.2).
- **Held removals with no queue** (main spec §9.1.1 against §20's 3a): counted (§9.7).
- **"The first real tag after the dry run"** (README, runbook) **against step 7's real import**:
  decision 7 (§12.5).
- **An auto-private marker in `recording.json`** (2a rulings: `plaud.auto_private_checked_up_to`)
  against `extra="forbid"` and rollback: the marker is in `state.db` (§8.2).
- **"A short page is the last page"** (the `plaud-browse` skill) **against a clamped page size**
  (main spec §9.1.1's fake-server list): a listing ends on an empty page (§6.2).

## 4. How it should feel

These principles turn §1's list into rules. Each names where this spec applies it.

| # | Principle | Where |
|---|---|---|
| P1 | **No silent failure.** Every way the flow can stop either pushes once or shows on Status: a failed run, a refused sign-in, a breaker trip, a consent stop, a shrunken listing, a dead worker, a schedule switched off. | §10.2, §11 |
| P2 | **Honest status.** Each sentence is true now, or isn't shown. Every count adds up. Every figure carries its time ("as of 14:20"). Waiting, overdue and failing are never blurred. | §7.2, §10.2 |
| P3 | **One place to look.** Pushes say "See Status", and Status is complete on its own. Add acts, and links to Status. | §10.1, §10.2 |
| P4 | **Calm alerts.** One push when a failure starts, a reminder a day, one recovery. Routine errors push only when they last 2 hours. A single recording's failure never pushes. | §11 |
| P5 | **Every alert has a next step,** in the runbook. | §11, §12.5 |
| P6 | **Every press gets an answer** within a second. Never a silent no-op. | §5.3, §10.1 |
| P7 | **Dan's choice wins, and stays.** A `private` tag he removed stays removed unless a new title matches. | §8 |
| P8 | **Errs private and fails closed,** typos in the consent list included. | §7.3, §8 |
| P9 | **A window before the irreversible,** usable from the phone. | §7.3 |
| P10 | **Arrival is visible.** An open Library says "3 new from Plaud · Refresh", and rows don't move until Dan refreshes. | §10.3 |
| P11 | **Fresh by default, immediate on demand.** The sweep keeps up, and Re-sync is the immediate path. Each recording says when it was last checked, and when it will be next. | §9 |
| P12 | **Plain sentences first, diagnostics folded away.** | §10.2 |
| P13 | **No titles outside the app's own views.** None at all for a recording kept out. | §6.1, §10, §11 |
| P14 | **The app removes chores.** Dan never checks Plaud by hand, and signing in again is rare and one command. | §6.5, §7 |

## 5. Architecture

### 5.1 Three containers, one writing machine

```
 browser ─websocket─▶ web ──request row──▶ state.db ◀──claims── worker ──GET only──▶ Plaud
                      │ tags, own Writer    (WAL, locks)         │ own Writer
                      └────▶ archive, per-recording flock locks ◀┘
                                     ▲ reads only
                                  backup: restic, the mirror, checks
```

- **One image, three containers:** `web` (`recordings-ui`), `backup` (`recordings ops --loop`,
  stage 2a) and `worker` (`recordings worker`, new). Long jobs never slow down the UI (main spec §3).
- **One writer means one machine.** On the homelab server, several processes may each hold a
  `Writer`. They share the per-recording locks, `state.db` and `writer_id` (main spec §6.4). Each
  process has one `Writer`, on one thread (tagging part A §5).

| Process | Writes | Never |
|---|---|---|
| `web` | tags (tagging part A), the archive's docs at start, request rows | calls Plaud, or holds the token |
| `worker` | new recordings from Plaud, snapshots, Plaud's outputs, Plaud's title, the automatic `private` tag, its own `state.db` tables, its alerts and pings | writes any tag but `private`, writes the docs, or opens a port |
| `backup` | backups, the mirror and checks, with the archive read-only | calls Plaud |
| the CLI in the `worker` container | as the worker | |

### 5.2 The worker process

- **`recordings worker`** is one process with one main thread and one `Writer`, opened with
  `Writer.open(cfg, docs=False)`. The web app writes the archive's docs, so two releases can't take
  turns rewriting them during a deploy.
- **At start**, in this order:
  1. It writes its heartbeat (`meta.worker_heartbeat`).
  2. Under the `plaud` lock, it sweeps its own leftovers in the archive's `.tmp/plaud-dl/`.
     (`Writer._sweep_tmp` leaves names that aren't recording IDs alone.)
  3. It marks any request left `running` by a dead worker as failed ("interrupted"), and any request
     queued more than an hour ago as expired.
  4. It opens the `Writer`. An index rebuild here can take a while, and the heartbeat keeps the
     health check green meanwhile.
- **Every 2 seconds:**
  - It writes the heartbeat, at most every 30 seconds.
  - It claims the oldest queued request and runs it.
  - When a scheduled run is due (`schedule_minutes` after the last run ended), it runs one.
  - When **Sync everything** is due (30 days after the last one), it runs it.
- **Inside a long run,** between two recordings, it serves the heartbeat, single-recording Re-syncs,
  and the light part of a scheduled turn that has come due (§9.6). New recordings keep arriving
  during a long run.
- **On SIGTERM** it stops between recordings and exits within its 30-second grace. A download in
  flight is abandoned, and its temporary file is swept at the next start.
- **With no token or no consent list,** it stays up, healthy and idle. Status and doctor say "sign-in
  needed" or "consent list missing". `make deploy-smoke` and runbook step 5 rely on this.
- **With `schedule_minutes = 0`,** it serves requests only.
- It listens on no port.

### 5.3 Requests from the UI

- **The browser sends** an event input, `plaud_request`, mounted once in `App` with a `null` default,
  as `tag_request` is (tagging part A §5). It is sent only by a person's action: never on load, and
  never from a URL, its hash or storage (main spec §15).
  ```json
  {"request_id": "<random>", "kind": "compare" | "sync" | "import" | "resync" | "full",
   "plaud_ids": ["…"], "recording_ids": ["…"], "all": false}
  ```
- **The web app checks it, then inserts a row** in `plaud_requests`, off the event loop (an
  executor). It checks:
  - `kind` is one of the five;
  - every recording ID parses (`ids.parse_id`);
  - the caps hold: 20,000 Plaud IDs, and 1,000 recording IDs for `resync`.

  A request never carries a title.
- **Requests coalesce:**
  - At most one `sync` or `compare` is queued at a time. A repeat answers "Already queued".
  - A `resync` merges into one that is already queued.
  - A `full` is refused while one is queued or running, or within an hour of the last one.
  - A repeated `request_id` is ignored.
  - An `import` is safe to repeat: a recording already here is skipped.
- **The worker claims a row** (`UPDATE … WHERE state = 'queued'`) and checks it again against fresh
  facts (§7.4): the newest listing, the consent list read now, and the keep-out word on the fetched
  title. It writes its progress and result into the row: counts, IDs and fixed phrases only.
- **Replies:** while a session has a request pending, the server reads that row every 2 seconds and
  sends progress and the final reply to the session (`send_message`). Toasts and the panel update
  from them. Otherwise Status's 60-second redraw is enough.
- **When the worker is down,** rows stay queued. The panel reads the heartbeat and says so (§10.1).
  A row unclaimed for an hour expires at the worker's next start: Dan meant "now", and the schedule
  covers the rest.
- **The CLI** writes no rows. `rc exec worker recordings plaud …` runs in its own process, under the
  same lock.
- **Trust:** only a process with `/state` mounted can write a row, and those already have full trust.
  The browser's path is the guarded websocket. The worst a forged row could do is cause Plaud reads,
  and coalescing caps those.

### 5.4 The sync lock, and the `ops` lock

- **`plaud` allows one sync at a time.**
  - It is taken through its own `Locks` instance, as `ops` is. Taken in the `Writer`'s instance, it
    would make every recording lock taken inside it raise `LockOrderError` (`locks.py:78`).
  - It is held for one run, and never between runs.
  - It is announced with the run's kind (`plaud-sync`, `plaud-compare`, `plaud-import`,
    `plaud-resync`, `plaud-full` or `plaud-catch-up`), so Status and the deploy guard can name the
    holder.
  - A scheduled turn that finds it held is skipped: busy, not a failure. A request waits its turn.
    The CLI waits up to 60 seconds, then exits 75.
- **Scheduled runs and UI requests never take `ops`.** The hourly backup, the mirror and the long
  checks run beside the sync. They already tolerate a writer beside them: a new recording is
  assembled in `.tmp/`, which both leave out (main spec §6.4, §15.1). Taking `ops` would starve the
  sync for hours behind a restore test.
- **The CLI's `recordings plaud sync`** (not `--full`) takes `ops` and `plaud` together, in one
  `hold()` call, announced as `plaud-catch-up`. Both sort as "anything else", `ops` first. This is
  the first catch-up's mode, like the `audio-router` import: no backup or mirror runs while many new
  folders land, and the `pre-import` and `post-import` snapshots bracket the run exactly (§7.6).
- **The `audio-router` import also takes `plaud`,** beside `ops`, so no sync imports between its plan
  and its run.
- **Recording locks are held only around local work:** publishing a snapshot, a `mutate`, writing
  outputs, or moving an assembled recording in. Never across a Plaud call, a download or a wait. The
  web's tag writes wait 2 seconds for a lock (tagging part A §5), and a test bounds the worker's
  longest hold well under that (§16).
- **`tokens.lock`,** in `/plaud`, guards a refresh (§6.4).
- **Later:** Forget (stage 3b) pauses the worker by taking `plaud`.

### 5.5 `state.db`: new tables only

| Table | Holds | Written by | Read by |
|---|---|---|---|
| `plaud_requests` | the UI's requests (§5.3), kept 7 days after they finish | web inserts; worker claims and updates | both |
| `plaud_listing` | one row per Plaud ID ever listed: the ID as listed, its canonical form, a SHA-256 of its title (never the title), its duration, its start time, any change marker S5 finds, first and last seen | worker | both |
| `plaud_compare` | the newest compare's rows by bucket (§7.2). A title is kept only for a row not yet here and not kept out (§10.1) | worker | web |
| `plaud_checks` | check state per recording (main spec §6.8): last checked, last changed, next check, interval, consecutive failures, settled, baseline pending, held removals | worker | both |
| `plaud_check_log` | one row per check: time, recording, result, parts changed. Kept 7 days, for the breaker's window and Status's counts | worker | both |
| `plaud_private_checked` | the auto-private marker (§8.2): for each recording and Plaud ID, the SHA-256 of the title last checked | worker | worker |
| `plaud_events` | notes Dan sees: imported, arrived private, re-tagged private, kept out late (open until resolved). Kept 90 days, open flags until resolved | worker | web |
| `plaud_state` | one row per key: the last good listing, the breaker, the sign-in state, the flood guard, the field names and ID forms of the last good run, `--full`'s progress | worker | both |

- **No `SCHEMA_VERSION` bump.** Each table is `CREATE TABLE IF NOT EXISTS` in `SCHEMA`, and no 2a
  table changes. An older release's `State.open` runs its own schema and ignores tables it doesn't
  know, so `make rollback` keeps working. A test asserts that `SCHEMA_VERSION == 1` and
  `MIGRATIONS == {}`, with a comment saying why.
- **Reused as they are:** `ops_runs` (jobs `plaud-sync` and `plaud-full`), `alerts`, and `meta`
  (`worker_heartbeat`).
- **The only titles in `state.db`** besides the merge-base revisions are in `plaud_compare`, for
  recordings not yet imported. Forget (stage 3b) scrubs them too.
- **`index.db` moves to WAL mode,** the 2a carry (2a rulings, item 72d), because two processes now
  look things up while the other writes.

### 5.6 Where the code goes, and the commands

- **Core (`packages/core/src/recordings/`):**
  - `plaud/client.py`: the HTTP client, copied from `audio-router` with its source commit recorded,
    relicensed MIT (main spec §19)
  - `plaud/tokens.py`: the token store
  - `plaud/sync.py`: the listing, the compare, the import and the checks
  - `plaud/checks.py`: the `state.db` tables
  - `sources/base.py` and `sources/plaud.py`: main spec §9.0's `Source`. An edit-sync check must not
    download media, so the source has a `check()` beside `fetch()`.
  - `worker.py`: the loop
  - `Writer.add_snapshot(recording_id, raw)`: new. It publishes a snapshot write-once and records it
    through `mutate`. 2a has no method that adds a snapshot without media.
- **UI (`packages/ui/src/recordings_ui/plaud.py`):** request checks and inserts, and the views read
  from `state.db`. It imports no client, token or sync module, and a test asserts it, as the Pages
  import rules do.
- **Commands:**

  | Command | Does | Holds |
  |---|---|---|
  | `recordings worker` | the daemon, and compose's command | `plaud`, per run |
  | `recordings worker --alive` | exits 0 when the heartbeat is under 90 seconds old | nothing |
  | `recordings plaud sync --dry-run` | lists and classifies, and prints the equation and counts. `--list` adds the rows not here and the rows kept out, by ID, date and duration | `plaud` |
  | `recordings plaud sync` | lists, then imports every waiting recording, with no cap and no flood guard | `ops`, `plaud` |
  | `recordings plaud sync --full` | **Sync everything** (§9.6) | `plaud` |
  | `recordings plaud resync <id>…` | checks these recordings now | `plaud` |
  | `recordings plaud baseline [--all]` | §9.5 | `plaud` |
  | `recordings plaud breaker reset` | §9.4 | `plaud` |
  | `recordings plaud held` | held removals, by recording ID and part | nothing |
  | `recordings plaud check` | one signed-in read; prints whether the sign-in works, and expiry times | nothing |
  | `recordings plaud token-import <file or ->` | installs a sign-in (§6.5) | `tokens.lock` |
  | `make plaud-sign-in SERVER=<host>` | on the Mac: signs in and installs it (§6.5) | |

- **Exit codes:** 0 ok; 1 failure; 2 partial (the listing was good, but some recordings failed or
  aren't here); 69 Plaud unreachable or rate limited; 75 busy; 77 sign-in needed; 78 configuration
  (the consent list, the config). `--json` prints counts, IDs and fixed phrases, never a title.

## 6. The Plaud client and the token store

### 6.1 The client: read-only, and leaks nothing

- **Copied from `audio-router`** (main spec §19). Each file records the commit it came from, and is
  relicensed MIT. Its tests' data is replaced with synthetic data.
- **The `of_` fix.** The client never checks an ID against a pattern: Plaud IDs are opaque strings
  (main spec §6.3). Every request uses the ID exactly as the newest listing gave it. The canonical
  form (`refs.canonical_plaud_id`) is only for comparing. A check of a recording uses the ID from the
  newest listing, never `sources[].ref`, which holds `audio-router`'s bare form for older recordings.
  A test reads the fake server's request log to prove it.
- **One request function, and only reads.** It sends `GET`, plus the refresh `POST` to the one token
  endpoint. A test drives every client path against the fake server and asserts that nothing else
  reaches it.
- **The token goes to Plaud's API host only.** `Authorization` is added as an unredirected header.
  Presigned URLs (the audio, and blocks behind a `data_link`) are fetched without it. A redirect to
  another host is never followed with it, as alerts already do (`alerts.py:49-69`). Tests: a
  presigned URL on a second port, and a 302 to another host, both arrive with no `Authorization`.
- **stdlib `urllib`,** like `alerts.py`. No new dependency, and nothing that logs request URLs.
- **Timeouts on every call:** 10 seconds to connect and 60 to read. A media download has a stall
  timeout instead (no bytes for 60 seconds), because a long file legitimately takes a while.
- **Nothing personal or secret in logs:** no token, no `Authorization` header, no refresh reply, no
  presigned query (`X-Amz-…`), no response body, and no title. A log names a Plaud ID by the first 8
  hex characters of the SHA-256 of its canonical form. A guard test runs a whole sync at DEBUG and
  scans every log line for these (§16).
- **`[plaud] api_base`** must be `https://`, or `http://` to the loopback for tests, as ntfy's server
  must (main spec §14).

### 6.2 The listing

- **It pages until an empty page.** A short page is not the end, because Plaud may clamp the page
  size. A hard cap (500 pages) stops a server that never ends.
- **Repeats are counted once.** IDs are de-duplicated by canonical form, and the repeats are counted
  and reported.
- **A listing is good** when it ended on an empty page with no error, every row had an ID and a
  title field, and it hasn't shrunk (§7.5). Anything else is **partial**. A partial listing may still
  add new recordings and catch title changes, but it never marks a recording gone from Plaud, and
  never replaces the last good listing.
- **An HTML page, or JSON of the wrong shape,** is a bad listing, never an empty account.
- **Each row is recorded** in `plaud_listing`: the ID as listed, its canonical form, its title's hash,
  its duration and start time, and any change marker S5 finds.
- **A census of ID forms** is kept per listing (`of_` form, bare hex, other), and Status shows it.
  A change in form is the 2026-09-25 failure, and the field-name diff can't see it.

### 6.3 Errors, pacing and budgets

- **Pacing:** one call at a time, and at most one call a second on average. S10 may change this.
- **A scheduled run's budget** is 10 minutes of calls. A download already under way finishes, under
  its stall timeout.
- **429:**
  - A `Retry-After` within the budget is waited out.
  - A longer one ends the run, "rate limited until 14:40", and the next run waits until then.
  - The next runs' sweep cap halves, then grows back by one recording per good run.
- **5xx and timeouts** are retried with backoff (30 seconds, doubling, with jitter). After 3 in a row
  of one kind, the run ends: "Plaud unavailable". That is never a sign-in problem.
- **Connection failures** end the run: "offline".
- **One recording failing** is retried once, then counted. Its next check backs off, and it never
  stops the others. After 3 failures in a row it shows on Status (§10.2).
- **A link that answers 403 or has expired:** the whole envelope is fetched again, once, and the
  check then fails. Links are never stored. Whether content links last 300 seconds and audio links 24
  hours (the two sources disagree) doesn't change this; S8 measures both.
- **A short body or broken JSON** fails that fetch, and nothing is written.
- **A 403 from the API itself** (not from a link) is a run failure, `PlaudForbidden`. It may mean
  the subscription has lapsed.
- **A 401** is §6.4.

### 6.4 The token store

- **Where (controller's call):**
  - The sign-in lives in its own host folder, `/srv/recordings/plaud`, mode 0700, owned by the app's
    user. It is named in `deploy.env` as `RECORDINGS_PLAUD_HOST`.
  - It is mounted read-write at `/plaud` in `worker` only. The file is `tokens.json`, mode 0600.
    `[plaud] token_store` names it (`/plaud/tokens.json`).
  - **Why:** `web` and `backup` mount `/state` read-write, as the same user, so no file mode could
    keep them out of `state/plaud/`. Outside `state/`, the token is never in a backup, and the
    runbook's "Restoring for real" can keep moving the whole state folder aside (the 2a carry,
    settled).
- **The file is our own format:** `version`, `access_token`, `refresh_token`, `expires_at` (UTC),
  `obtained_at` and `refreshed_at`. `token-import` writes it from the vendor CLI's file, whose format
  S1 learns. A refresh rewrites it.
- **Every write is atomic:**
  1. A temporary file in the same folder, created exclusively, mode 0600.
  2. Fsync it, rename it into place, then fsync the folder.

  The file is never wider than 0600, even for a moment.
- **Refresh:**
  - It happens before expiry: when less than 5 minutes, or 10% of the token's life, is left.
  - It also happens once after a 401: refresh, then retry the call once.
  - Only one refresh runs at a time, under `tokens.lock`. The file is read again after the lock is
    taken, because the CLI and the daemon can both refresh.
  - The new file is written before the new access token is used. A reply with no new refresh token
    keeps the old one.
- **A refused refresh means the sign-in is dead** (S4 learns the exact codes, typically 400 or 401
  with `invalid_grant`). It raises `PlaudAuthError`:
  - The run stops, and the alert pushes at once (decision 5).
  - Scheduled runs make no Plaud call until the token file changes (by inode, size and time, checked
    each tick). A dead sign-in is not presented to Plaud 72 times a day.
  - A **Sync now** from Dan tries once anyway.
- **A refresh endpoint that can't be reached, or answers 5xx,** is an outage (§6.3), not a sign-in
  problem.
- **The worker refuses a token file open to group or others,** like `ssh` with a key. The message
  names the file and `chmod 600`. Nothing ever prints the contents.
- **Status** says "Plaud sign-in good", or "Plaud sign-in needed since 14:20". If S1 finds that the
  refresh token has its own expiry, Status adds it, with a warning 7 days ahead.
- **`recordings plaud check`** makes one cheap signed-in read (the current user). It prints whether
  the sign-in works, the access token's expiry, and whether a refresh token is present. It exits 0,
  or 77 when the sign-in is missing or refused.

### 6.5 Signing in again, on the Mac (controller's call)

`make plaud-sign-in SERVER=<host>`, run on the Mac, does it in one step:
1. Makes a temporary `HOME` outside the repo, mode 0700.
2. Runs the vendor CLI's login with that `HOME`. The browser opens, and Dan signs in. The separate
   `HOME` keeps the server's sign-in apart from the Mac's own Plaud CLI and MCP sign-ins
   (`~/.plaud/tokens.json`, `~/.plaud/tokens-mcp.json`).
3. Pipes the CLI's token file over ssh into `recordings plaud token-import -`, run in the `worker`
   container. The token never touches a file on the server outside `/plaud`, or the terminal.
4. `token-import` checks the file's keys, makes one cheap call with it, and installs it atomically at
   0600. It prints only "signed in: yes · access token expires 2026-10-23 16:20 · refresh token:
   present · account 1a2b3c4d" (the first 8 hex characters of the SHA-256 of the account's ID, so
   Dan can see it is his account). If the call fails, it installs nothing and exits 77.
5. Deletes the temporary `HOME`, whether or not the earlier steps worked.

It exits non-zero on any failure, naming the step that failed. The new file is a change, so the
worker's sign-in pause clears at its next tick, and the next run sends one "works again" push.

- **No phone sign-in in 2b.** The vendor's login needs its client secret and a callback on
  `localhost` (main spec §9.1). If signing in again turns out to be frequent, ask Dan again.
- **If a second sign-in signs the first out** (spike S3), Dan is asked the question in §13 before the
  production sign-in.

### 6.6 The fake Plaud server

- **A test helper,** `packages/core/tests/fake_plaud.py`, with a `plaud_server` fixture beside
  `ntfy`. It follows `FakeNtfy` (`conftest.py:483-539`): a stdlib `ThreadingHTTPServer` on
  `127.0.0.1:0`, in a daemon thread.
- **It holds a synthetic account:** recordings built like `plaud_env`'s envelopes, with tiny WAVs
  from `wav_bytes`. Every title, name and text is invented. Its field names come from spikes S5 and
  S8, never its values.
- **It serves:**
  - the list endpoint, with paging
  - `files/{id}`
  - block bodies and audio on a second port, standing in for the storage host
  - the token endpoint
  - the current user
- **It records every request:** method, path, host, and which headers were present (by name). It
  never records a body.
- **It can inject** each fault in §16's list, and each change Dan makes in Plaud's app (a title, a
  speaker rename, regenerated notes, a deletion, a restore).
- It is not a dev server. Demo mode makes no network call (main spec §17); the live e2e tests run it
  in-process (§16).

## 7. The compare and the import

### 7.1 One run, step by step

The same steps serve a scheduled run, **Sync now**, and the CLI:

```
take the plaud lock                      → else: busy, skip
read the consent list, fail closed       → else: stop, push (§7.3)
check the sign-in, refresh if due        → else: stop, push (§6.4)
list every page                          → good or partial (§6.2)
census: ID forms, field names
classify every listed ID                 (§7.2)
title changes on recordings here         → private check (§8.1), then a check (§9.1)
flood guard                              → pause auto-import (§7.5)
import waiting recordings, up to the cap (§7.4)
a chunk of the sweep                     (§9.2)
the breaker                              (§9.4)
record the listing, the compare and the run's result; ping the sync's check if the run was good
release the lock
```

A **Compare** (the dry run) stops after "classify", and writes only `plaud_listing`, `plaud_compare`
and the run's result.

### 7.2 The buckets, and counts that add up

Every listed ID lands in exactly one bucket:
- **here:** in the archive, matched by its Plaud ID in either form (canonical, through the index's
  `find_by_source`).
- **waiting:** not here, and the sync will bring it in by itself, at the next scheduled run or now.
- **excluded:** on the consent list, or kept out by its title (§7.3). Shown by ID, date and duration,
  never by title.
- **missing:** not here, and it won't come without Dan, because:
  - the schedule is off, or auto-import is;
  - the flood guard has paused auto-import;
  - the disk guard has stopped imports; or
  - its import has failed 3 times.

**Held titles** (main spec §7.4: `audio-router` never fetched recordings whose titles matched its
hold patterns) are waiting or missing like any other recording, and arrive tagged `private` (§8.1).

**listed = here + waiting + excluded + missing.** This is main spec §20's "the dry run accounts for
every listed ID", turned into something Dan can see: on Status, in the Add panel and in the CLI's dry
run. Repeats across pages are counted once, and reported. A property test checks the sum over random
accounts (§16).

Two more lists sit beside the equation:
- **Updated on Plaud:** recordings here where a check in the last 7 days found a change (§9). It is
  part of "here".
- **Only in the archive:** recordings with a Plaud source whose every Plaud ID is absent from the last
  good listing (§7.7).

**Matching is by ID only.** The listing carries no audio hash (main spec §9.1), so the compare can't
match by hash. The hash match happens at import: the downloaded audio is hashed, and identical bytes
join the existing recording as a new source reference (`Writer.add`, main spec §6.4). This settles
main spec §9.1's "and by content hash".

**`audio-router`'s deferred rows** (Plaud rows the 2a import reported as `deferred-to-2b`: audio but
no snapshot and no time) are ordinary Plaud IDs here. If Plaud lists one, it is waiting, and is
imported from Plaud like any other. If Plaud no longer lists one, its audio exists only in
`audio-router`'s frozen archive, because runbook step 4 deleted the import copy. 2b doesn't import it
(controller's call). Runbook step 6 counts any such rows from the 2a import's report. Bringing one in
by its file's time waits for a re-run of `import-audio-router` over a fresh copy, if Dan ever wants
it. This settles main spec §9.3's mapping row against runbook step 4.

### 7.3 Keeping recordings out

There are two ways. Both are checked before any byte of the recording is fetched: no `files/{id}`
call, and no audio.

**The consent list** is a hard rule (main spec §9.1):
- It is read fresh at the start of every run, and fails closed. Unset, missing, unreadable, empty or
  holding no IDs stops the run, with exit 78 and a push at once (decision 5).
- **The parser fix:** an entry counts in either form, `of_<hex>` or bare hex, in any case. It still
  counts only when it opens a bullet or stands alone on a line, `audio-router`'s rule.
- **A typo is shown, not skipped.** Lines that look like an ID but can't be read (31 or 33 hex, a
  stray character) are counted. Doctor and Status say "2 lines look like IDs but weren't read".
  This settles `consent.py:17`, which silently skipped the `of_` form and upper case.
- **An ID listed after its recording came in** stops the whole run (controller's call), as the 2a
  import stops (main spec §9.1; 2a rulings, H3). It pushes at once. The run stays stopped until Dan
  takes the recording out by hand (runbook) and runs `recordings reindex --allow-missing`.
- The list is mounted read-only, in `worker` only.

**The title marker** (decision 2):
- A Plaud title that starts with the keep-out word (`[plaud] keep_out_word`, default `noarchive`) is
  never downloaded.
- **How a title matches:**
  1. Apply NFKC and casefold.
  2. Strip leading spaces and Plaud's "MM-DD " date prefix, as auto-private does.
  3. The word must come next, followed by the end of the title, a space or punctuation.

  So `NoArchive: coffee` and `noarchive chat` match, and `noarchived` doesn't.
- It is checked on the listing's title, and again on the envelope's title before the audio is
  downloaded.
- **It is a window, not a vault.** Renaming the recording to a title without the word releases it,
  and it arrives at the next sync. To keep it out for good, Dan adds its ID to the consent list (the
  Add panel shows the ID, with a copy button) or deletes it in Plaud.
- **Too late:** if the recording had already come in when its title gained the word, the next sync
  tags it `private` at once (§8.1) and flags it: "Kept out by title, but already in the archive:
  delete it by hand". The flag shows in Status's Needs attention and in the Add panel, by recording
  ID, never by title. There is no push: Dan renamed it himself. The flag clears when the recording
  leaves the archive, or when its title loses the word.
- **What Dan should expect:** rename before the next sync, within `schedule_minutes` of Plaud
  listing the recording. Status and the Add panel show when the next sync is.

### 7.4 Importing one recording

1. **Before any byte:** the consent list and the keep-out word, on the listing's title (§7.3). The ID
   must be in the newest listing; if that listing is older than `schedule_minutes`, the run lists
   again.
2. **The envelope:** fetch `files/{id}` with the ID as listed, then each block behind a link, without
   `Authorization`. Assemble the snapshot as `audio-router` did. Check the keep-out word on its title;
   if it matches, discard everything and count the recording as kept out.
3. **Tags:** `private` if any title (the listing's or the envelope's) matches
   `auto_private_patterns` (§8.1). It goes in `Incoming.tags`, so it lands in the same rename as
   `recording.json`, before any source or output (main spec §7.4).
4. **The disk:** below `stop_free_percent`, or when the audio's `Content-Length` would leave less than
   that margin, imports stop for this run, and the rest become missing ("the disk is nearly full").
5. **The audio:** downloaded from its presigned URL, without `Authorization`, into the archive's
   `.tmp/plaud-dl/`. It streams to disk, never into memory, and never into `/tmp`, which is a 1 GiB
   RAM tmpfs (`compose.yml:25`). The byte count must equal `Content-Length`.
6. **Measure** it with ffprobe, as the 2a import does. A duration that disagrees with Plaud's is a
   timing verdict (main spec §9.1.1), not a failure.
7. **Add it:** `Writer.add(Incoming(…))`, with a `RawSource` of kind `plaud`, its ref the ID as
   listed, and its payload the snapshot. `recorded_at` comes from Plaud's start time in the default
   zone (main spec §6.2; `time_source` `plaud`; spike S12 confirms the reading). The same bytes as a
   recording already here merge into it (main spec §6.4). The revision's origin is
   `plaud-import:<run ID>`.
8. **Reconcile:** `Writer.reconcile_plaud` writes Plaud's outputs. A snapshot with link errors is
   kept, but builds nothing (main spec §9.1.1), and the recording is checked again at the base
   interval.
9. **Record:** a `plaud_checks` row, with its first check due at the base interval (the first
   snapshot is a baseline, §9.5), the marker (§8.2), and an event: `imported` or `arrived private`.

- No recording lock is held across a network call. The download comes first; under the lock there is
  only local work (§5.4).
- One recording's failure is counted, and the run goes on. After 3 failed tries the recording
  becomes missing: "failed 3 times: see Status". An archive-level error (the sentinel, `state.db`,
  the index, a full disk) stops the run.
- A scheduled run imports at most 20 recordings. The rest stay waiting for the next run. Imports from
  the CLI and from the Add panel have no cap.
- **No confirmation dialog (controller's call).** **Import selected** and **Import all** answer with a
  toast: "31 imported · 4 arrived private · View". **View** opens Untagged, and "4 arrived private"
  opens the Private section, because those recordings skip Untagged. This follows main spec §12.2's
  "no confirmation dialogs". The first import's confirmation is the dry run (§7.6).

### 7.5 Guards

- **A flood of Missing,** the `of_` lesson:
  - A scheduled run that finds more waiting recordings than 25, or 10% of those here (whichever is
    larger), imports nothing and pauses auto-import.
  - It pushes at once, as a breaker trip (decision 5): "Plaud sync paused (everything looks new)".
  - The likely cause is an ID-form change that defeated matching. Status says so, beside the ID-form
    census and the count gone from Plaud, which would jump too.
  - Dan resumes after looking: **Compare**, then **Import selected** or **Import all** in the Add
    panel, or `recordings plaud sync` from the CLI. Either clears the pause.
  - The thresholds are constants, not settings.
- **A shrunken listing** (main spec §9.1.1): fewer than 90% of the last good listing's IDs, and at
  least 5 fewer.
  - It isn't a good listing: nothing is marked gone, and the last good listing is kept.
  - Status says "Plaud's listing shrank by 12: nothing marked gone".
  - It counts as a run without a good listing, for the 2-hour rule (§11).
- **The disk:** §7.4, step 4.

### 7.6 The first catch-up (controller's call)

- **A supervised CLI run,** `rc exec worker recordings plaud sync`, with the schedule still off. It
  holds `ops` and `plaud` (§5.4), so no backup or mirror runs while it imports. It has no cap and no
  flood guard, because Dan has just read the dry run.
- **The order** (runbook step 7; main spec §19):
  1. The dry run is checked (step 6).
  2. A backup tagged `pre-import`.
  3. `recordings plaud baseline` (§9.5).
  4. `recordings plaud sync`.
  5. `recordings validate`.
  6. A backup tagged `post-import`.
  7. The schedule is turned on, and the sync's Healthchecks check unpaused.
  8. One scheduled run shows "All here".
  9. **"You may start tagging now"** (decision 7).
- **The backup tags** `pre-import` and `post-import` are reused. The NAS's retention keeps them for
  good (main spec §15.1), so retention needs no change.
- **After it,** scheduled runs take per-recording locks only, never `ops`.

### 7.7 Gone from Plaud (controller's call)

- A recording whose every Plaud ID is missing from a good listing is **gone from Plaud**. It is kept
  forever, never deleted, and shown as "only in the archive": as a count in the Add panel and on
  Status, and in Details as "Gone from Plaud since 2026-10-23 (kept)".
- The sweep stops checking it. If it comes back (restored from Plaud's trash), it is here again, with
  nothing imported twice.
- `--full` reports these by count, and doesn't fail on them.
- Deleting it from the archive is a manual step (runbook), as in 2a.

## 8. Auto-private (decisions 1, 2 and 3)

Decisions 1 to 3 change 2a's rules. This section is the whole rule as 2b builds it.

### 8.1 When `private` is added, exactly

The patterns are `[plaud] auto_private_patterns` (main spec §7.4). The sync adds `private` (`by:
auto`) in these four cases, and no others:

1. **At import.** If any title the listing or the fetched envelope gives matches, `private` lands with
   `recording.json`, before any source or output (§7.4, step 3).
2. **A title changes in Plaud.**
   - Every run compares every listed title (by its hash) with the one stored for its ID. Decision 1
     asks for recent titles to be re-checked. Checking them all includes the recent ones, and costs
     nothing more, because the listing carries every title.
   - For a recording here whose new title the marker hasn't checked (§8.2), the patterns run on the
     new title.
   - A match adds `private` through `mutate` at once, **from the listing alone (controller's call)**.
     The sync doesn't wait for the recording's fetch, so a fetch that keeps failing can't leave a
     private title untagged.
   - Then the check fetches the recording (§9.1), writes the snapshot, and reconcile moves the title.
   - If the recording wasn't private before, it gets the note "re-tagged private: title changed"
     (decision 3), shown in Details and in the Add panel's Updated list.
   - The same rule runs inside reconcile, on the titles of new snapshots, so a title first seen in a
     fetch is checked too.
3. **A late title marker** (decision 2). A new title that starts with the keep-out word adds
   `private` at once, and raises the flag (§7.3).
4. **The first check of a recording the marker has never seen.** These are recordings imported by
   2a, and, after `state.db` is lost, any recording whose marker the rebuild couldn't seed (§8.2).
   The patterns run on every title any of its snapshots carries, under every one of its Plaud IDs,
   erring private, as in 2a. The baseline (§9.5) does this once, for the whole archive, at step 7,
   before any tagging.

The sync never removes `private`. It never runs the patterns again on a title already checked. So a
`private` tag Dan removed stays removed (decision 3) until that Plaud ID's title changes to one that
matches.

**The grace period is gone** (decision 1). A placeholder title doesn't match, so the recording comes
in untagged. When Dan renames it, case 2 runs at the next sync.

### 8.2 The marker

- **What:** for each recording and each of its canonical Plaud IDs, the SHA-256 of the newest title
  the patterns have checked. It lives in `state.db`, in `plaud_private_checked`. A hash is enough to
  see a change, and keeps titles out of a table that is backed up.
- **When a check is due:** the newest title for that ID hashes differently. After the check, the
  marker moves to that title, whether it matched or not.
- **Why `state.db`, not `recording.json` (controller's call):**
  - The archive's models refuse unknown keys (`extra="forbid"`, `models.py:56`). A new key in
    `recording.json` would leave a pre-2b release unable to read the archive after `make rollback`.
  - It is the sync's bookkeeping, not Dan's decision. Dan's decision is the missing tag itself (main
    spec §6.8).
  - So 2b adds no field to `recording.json`, and doesn't change `FORMAT.md` or the schemas. This
    replaces the 2a rulings' suggested `plaud.auto_private_checked_up_to`.
- **Reconcile stays pure.** The `Writer` passes reconcile the markers for the recording, and records
  the new ones after its `mutate` commits. A crash in between leaves the old marker, so the same
  title is checked again, which errs private.
- **After `state.db` is lost,** the rebuild seeds each recording's marker for its primary Plaud ID
  from `plaud.title_seen`, when it equals that ID's newest title. Only 2a's residual cases can then
  bring back a removed tag, and they err private.
- **This closes the 2a residual** (2a rulings, Task 7, carried to 2b and 3a), in which a tag Dan
  removed came back on every reconcile when `title_seen` couldn't advance. A re-run of
  `import-audio-router` still puts privacy tags back by design (main spec §9.3). That path is
  unchanged.

### 8.3 The deliberate exception, and CLAUDE.md

- **The rule** (CLAUDE.md; main spec §7.4; main spec §22, decision 35): the private tag lands before
  any source or output does.
- **The exception** (decision 1, Dan, 2026-10-23): a Plaud recording whose title becomes private
  after it came in.
  - Its audio, source and outputs landed untagged. `private` follows at the next sync after the
    rename, within one `schedule_minutes` while the listing works, because case 2 of §8.1 doesn't
    wait for the fetch.
  - Until then it sits untagged in the archive, in the hourly backups and in the mirror. An untagged
    recording already counts as private for external agents (main spec §7.4), so the exposure is to
    the backups and the mirror, which hold private recordings by design.
  - The same holds for a late keep-out word (decision 2).
- **What still holds:** whatever titles the app has seen when it imports decide the tag. It never
  imports first and checks after.
- **CLAUDE.md's Privacy bullet** changes from "The private tag lands before any source or output
  does." to:

  > The private tag lands before any source or output does, judged on every title the app has seen
  > at that moment. One deliberate exception (stage 2b, Dan, 2026-10-23): a Plaud recording renamed
  > to a private title, or to the keep-out word, after it came in is tagged `private` at the next
  > sync, so its source and outputs landed first. Until then it is untagged, which external agents
  > already treat as private.

- Main spec §7.4 and §22, decision 35, get the same sentence. Decision 19 and §9.1's grace paragraph
  are replaced (§14).

## 9. Edit-sync

### 9.1 What a check is

- **A check of one recording:**
  1. Fetch its envelope, with the ID as last listed.
  2. Normalise it, and compare it with the newest complete snapshot. Both sides are recomputed (main
     spec §9.1.1).
  3. If it changed, write a new snapshot (`Writer.add_snapshot`), then reconcile.

  It downloads no audio: audio is fetched once, at import.
- **Its result:** "up to date"; "updated", with the parts that changed (title, transcript, speakers,
  notes, Dan's own notes, other); or "failed", with its class. Each check is a row in
  `plaud_check_log`.
- **An unchanged check never writes `recording.json`** (main spec §9.1.1).
- **Locks:** the fetch happens outside every lock. Publishing the snapshot and reconciling happen
  under the recording's lock.

### 9.2 The rolling sweep

- **As main spec §9.1.1 says:**
  - Each recording has a `next_check_at`.
  - Its interval starts at `schedule_minutes`, doubles after each unchanged check, is capped at
    `full_sweep_days`, and resets when a change is found.
  - Each run takes the most overdue recordings first, up to N / (`full_sweep_days` × runs per day),
    rounded up, with a minimum of 1, within the run's budget. N is the number of recordings here.
- **New: the interval doubles only once a recording is settled.** Settled means it has a complete
  transcript and at least one Plaud note, or is more than 48 hours old. A new recording is checked at
  every run while Plaud is still working on it, so its transcript and notes arrive within a run of
  Plaud making them.
- **New: failures back off too,** doubling and capped the same way, so one recording that always
  fails can't eat the budget.
- **`full_sweep_days` (controller's call):** 1 while Dan relabels speakers (the template's value),
  and 7 after (the default when the key is unset). With 1, a speaker rename in an old recording shows
  by the next day. There is no faster cadence without a cheap change signal (§9.3). Re-sync is there
  for the impatient.
- **A changed title or duration in the listing** triggers a check now (main spec §9.1.1). A changed
  duration means a trim in Plaud.

### 9.3 Cheap change detection (pending spike S5)

- **If S5 and S9 find a field in the listing that moves when Dan edits** (an edit time, a version, a
  transcript or summary flag), `plaud_listing` stores it, and a change triggers a check now, like a
  title change. Then renames show within one sync, and `full_sweep_days` can go back to 7 even while
  Dan relabels. The sweep stays as the safety net.
- **If there is no such field,** §9.2 stands as written.
- Either way, the listing's duration is compared at every run.

### 9.4 The circuit breaker

- **What counts:** a check whose only changed part is "other": everything outside the named parts,
  or a field the normaliser doesn't know. What never counts:
  - a change to the title, the transcript, the speakers, the notes or Dan's own notes;
  - an import;
  - a recording's first check after its baseline (§9.5).
- **The window is a rolling 24 hours.** This settles main spec §9.1.1's "of a sweep", which could
  never trip at about 5 recordings a run.
- **It trips** when, within the window, at least 10 recordings counted, and they are more than 5% of
  the recordings checked.
- **While it is open,** a snapshot that would change only "other" isn't written. Everything else
  goes on: listings, imports, title checks, `private` tags, and snapshots with a named change. Noise
  never stops new recordings from arriving.
- **It is stored** in `plaud_state`, so a restart doesn't clear it.
- **What Dan sees:**
  - A push at once (decision 5): "Plaud edits paused (unexpected changes)".
  - Status: "Edit-sync paused since 14:20: 12 recordings changed only in fields Plaud doesn't
    usually change. New field names: `folder_id`, `share_state`. New recordings still arrive."
  - The Add panel repeats the first sentence.
  - Field names only, never their values.
- **The reset is never automatic, and only from the CLI (controller's call).** A reset with no fix
  would trip again. Dan and a Claude session look at the field names, then either:
  1. ship a release that drops the field (a normaliser version bump, which writes zero snapshots,
     main spec §9.1.1), then run `recordings plaud breaker reset`; or
  2. decide the field is real content: run `recordings plaud baseline --all` (each recording's next
     check writes one snapshot that doesn't count), then `breaker reset`.

  A reset sends the "works again" push and is recorded in `ops_runs`. A second trip alerts again.

### 9.5 The baseline, and rebaselining

Main spec §9.1.1 and §19 name the baseline, but never define it. This section defines it.

- **A baseline seeds a recording's check state, and excuses its next check from the breaker:**
  - Last checked is set to its newest snapshot's fetch time (main spec §6.8's rebuild rule).
  - Its next check is spread evenly over `full_sweep_days`, so the whole archive doesn't come due at
    once.
  - Its titles get their first private check, where the marker has never seen them (§8.1, case 4).
- **Its first check** compares today's envelope with an older snapshot; `audio-router`'s may be weeks
  old. A change found then is written once, as a snapshot, and doesn't count.
- **When:**
  - `recordings plaud baseline` at runbook step 7, before the catch-up, so the existing archive is the
    base. Each new import is its own baseline.
  - **Automatically,** for any recording here with no `plaud_checks` row: after `state.db` is
    restored or rebuilt. A lost `state.db` therefore can't trip the breaker. This is the 2a carry
    "seed last_checked on a freshly adopted state.db" (2a rulings, item 39).
  - `baseline --all` seeds every row again (a rebaseline): after a breaker trip accepted as content,
    or a normaliser change Dan chooses to accept.
- **It prints counts:** seeded, already seeded, titles checked, `private` added.

### 9.6 Re-sync: one recording, a selection, everything

- **One recording:**
  - Details' **Re-sync from Plaud** sends a `resync` request.
  - The recording is checked now, ahead of the sweep, and between the recordings of a long run.
  - The reply shows in place: "Up to date", "Updated: speakers", "Gone from Plaud", or "Failed:
    Plaud unavailable".
- **A selection (decision 6):**
  - The Library's selection bar reads "12 selected · Tag… · Re-sync from Plaud · Clear".
  - Pressing it sends one request. Progress shows as "Re-syncing 12 of 25…", and the end as "25
    checked: 7 updated (speakers 6, title 1) · 18 up to date".
  - Recordings without a Plaud source are skipped, and counted.
  - The cap is 1,000 per request (controller's call): "For more, use Sync everything".
  - When it is done, the reply carries the IDs that changed, and the open list re-reads those rows in
    place, as a tag reply does.
- **Sync everything** (`--full`, main spec §9.1.1):
  - It is a button in the Add panel's More menu, and `recordings plaud sync --full`.
  - It lists, imports what is waiting, checks every recording here (oldest check first), then
    retries its failures once.
  - It exits 0 only when the listing was good, and every listed recording is here or excluded and
    was checked with a complete envelope. Otherwise it exits 2, with counts.
  - It resumes after a restart: `plaud_state` keeps its start time, and it continues with the
    recordings not checked since.
  - It runs by itself every 30 days while the schedule is on. A failure alerts (§11).
  - It writes the DAN-15 manifest: each Plaud ID with its archive ID, last snapshot and part hashes,
    and no titles.
  - It runs at most once an hour, and only one is queued or running at a time.
- **Between the recordings of a long run** (Sync everything, or a large Re-sync), the worker serves a
  due scheduled turn's light part: the listing, title checks and imports. It also serves
  single-recording Re-syncs. New recordings keep arriving, and the 2-hour rule (§11) holds.

### 9.7 Removals wait, counted (controller's call)

- Reconcile already holds removals back: a name going back to "Speaker N", or notes vanishing (main
  spec §9.1.1). 2b doesn't apply them. The review queue that accepts them is stage 3a.
- **They are counted:**
  - Status: "3 changes from Plaud are held for your review (the review list comes with stage 3a)".
  - The Add panel's Updated list shows each recording with "removal held". There is no Accept button.
  - `recordings plaud held` lists them by recording ID and part.

## 10. The interface

**Add acts, and Status reports** (P3). Both pages show the same summary line, from one component with
one set of words, and each links to the other. Every push says "See Status", so Status must be
complete on its own.

### 10.1 Add: the Plaud panel

- **A new page,** `#/add`, in the top bar between Tags and Status: "Library · Tags · Add · Status".
  Its Plaud panel is what the main spec calls the Sync screen (main spec §9.1, §12.4).
- **What it is for:** acting. Dan rarely needs it while auto-import is on. He opens it:
  - for the first real sync (runbook steps 6 and 7), while the schedule is off;
  - when he wants something now (**Sync now**);
  - to see what is kept out, and to copy an ID into the consent list;
  - to confirm a fix after a push;
  - for **Sync everything** during the relabelling period.
- **Top to bottom:**
  1. **The summary line,** shared with Status: "All here · listed 14:20 · next sync 14:40", or the
     problem in one sentence and "See Status".
  2. **Sync now** (the main button) and **Compare**, a dry run that lists and classifies but imports
     nothing. A **More** menu holds **Sync everything**, with an estimate: "Checks all 1,234
     recordings with Plaud, about 40 minutes. New recordings keep arriving."
  3. **The equation:** "On Plaud 1,234 = here 1,227 + waiting 2 + excluded 4 + missing 1", each
     number a link to its list, and "from the listing at 14:20".
  4. **Not here yet** (waiting and missing):
     - Each row has a checkbox, the date, time and duration, and the title. A title that will arrive
       private has a lock and "arrives private".
     - Each row's state reads "imports at the next sync", "schedule off", "auto-import off", "paused:
       everything looks new" or "failed 3 times".
     - **Import selected** and **Import all**.
  5. **Kept out:** by the consent list, and by title. Each row has the ID (shortened, with a copy
     button), the date and the duration. Never a title (controller's call).
  6. **Updated on Plaud (7 days):** the recording, what changed (title, transcript, speakers, notes,
     removal held, re-tagged private), and when. Each row opens its recording.
  7. **Only in the archive:** a count, which expands to the list.
  8. **Flags:** recordings kept out by title but already in the archive.
  9. **Import your own audio:** coming later (main spec §9.2).
- **Every press gets an answer within a second** (P6):
  - "Queued", "Queued after the current sync (started 14:20)" or "Already queued".
  - "The Plaud worker isn't running (last reported 14:21): your request waits until it is."

  While a request is pending, the panel updates every 2 seconds. Then it drops back to Status's pace.
- **Titles here** come from `plaud_compare` for recordings not yet here, and from `recording.json`
  for recordings here. They never appear in a log, alert or report.

### 10.2 Status: the sync panel

- **First, plain sentences** (P2, P12), each true now or not shown:
  - "All here: every recording on Plaud is in the archive or deliberately left out. Checked 14:20."
    It shows only when the last good listing is under two schedule intervals old, nothing is waiting
    or missing, and nothing is failing.
  - Or the problem, for example:
    - "Plaud sync failing since 14:20 (Plaud unavailable). Last good listing 12:00. New recordings
      wait on Plaud."
    - "Plaud sign-in needed since 14:20: run `make plaud-sign-in` on the Mac."
    - "Edit-sync paused since 14:20 …" (§9.4)
    - "Plaud sync stopped: the consent list …" (§7.3)
    - "Plaud sync paused: everything looks new …" (§7.5)
  - An error shows here from the first failed run, although its push waits 2 hours (decision 5:
    "Status shows every error the whole time").
  - "Scheduled sync is off", when `schedule_minutes = 0`. Plain, and not in Needs attention.
  - "Auto-import is off: new recordings wait for you on the Add page."
- **Then the numbers:**
  - the equation (§7.2)
  - the last good listing (time and count), and the next sync
  - the sweep: "every recording checked within the last 21 hours (oldest 2026-10-22 17:10)"
  - the sign-in, and the refresh token's own expiry if it has one
  - the last **Sync everything**: "2026-10-01: all 1,234 checked", or its failure
  - held for review; only in the archive
  - "2 recordings failed their last check · Re-sync"
  - "The Plaud worker last reported 14:21"
- **Details, folded away:**
  - the normaliser's version
  - the field names added or gone since the last good run (names only)
  - the census of ID forms
  - changes per part in the last 24 hours
  - the share of named segments, as a canary
  - the last 20 runs, with their fixed results
  - the consent-list lines that look like IDs but weren't read
- **Needs attention gains:**
  - the sync failing for more than 2 hours (decision 5)
  - a sign-in needed
  - the breaker or the flood guard
  - a consent stop
  - the worker silent for more than 2 minutes
  - a `plaud` holder older than 2 hours (12 for Sync everything): "may be stuck"
  - kept-out-late flags
  - recordings failing 3 checks in a row
- **It redraws** every 60 seconds, as it does now (main spec §12.5), and when a request finishes.
- **No titles anywhere on Status.** Recordings are named by ID (P13).

### 10.3 The Library and Details

- **Arrivals are visible** (P10). The Refresh button in the list's header reads "3 new · 2 updated
  from Plaud · Refresh", beside tagging's "5 changed" (`HeadNotes.tsx`). The count comes from a cheap
  read of `plaud_events` every 60 seconds since the page loaded. Rows don't move until Refresh
  (tagging part A §6). The Tags grid's header has the same cue.
- **The selection bar** (`SelectionBar.tsx`) gains **Re-sync from Plaud** (§9.6).
- **The Details tab** gains:
  - "Plaud: checked 2026-10-23 14:20 · next by 2026-10-24 14:20", with **Re-sync from Plaud** and its
    result inline (P11);
  - "Gone from Plaud since 2026-10-23 (kept)";
  - "Tagged private by the sync on 2026-10-23: its Plaud title changed", or "…: its title matched
    when it arrived";
  - "Kept out by title, but already here: delete it by hand (runbook)".

  A recording with no Plaud source shows none of these.
- **A speaker rename shows** in the Transcript tab's Plaud transcript, the newest Plaud transcript
  output (main spec §6.5), and in the Add panel's Updated list as "speakers".
- **Wiring rules** (CLAUDE.md):
  - Every new output (the panel's view, the arrivals count, request replies) is subscribed in `App`
    with its `useShinyOutputStatus` line.
  - `plaud_request` is mounted once in `App`.

### 10.4 On a phone

- Add and Status work at 320 pixels wide. The summary comes first, buttons are full width, and
  targets are at least 44 pixels. List rows stack: the date and duration on one line, the title
  below. Sync everything stays in the More menu.
- A push says "See Status", with no click link: a link would send the app's tailnet address to
  ntfy.sh.
- **From the phone, Dan can:** rename in Plaud (the keep-out word, a private title), **Sync now**,
  import, Re-sync, and read Status.
- **He needs the Mac or the server to:** sign in again, and edit the consent list.

### 10.5 Demo and Pages

- **No worker, no Plaud code, no network** (main spec §17). The Add panel draws a canned view from a
  synthetic fixture in `demo/`, in the same shape as the real one, with the buttons disabled and
  "Demo: nothing is synced". Status's sync panel shows one line: "Demo: Plaud sync isn't part of the
  demo."
- The demo's temporary `state.db` never gets a request row. Pyodide never imports the client, the
  token store or the sync, and a test checks it.

### 10.6 Fixed phrases

Logs, Status, alerts, `ops_runs` and request results use these, with counts, IDs and exception class
names. Never a title, a token, a path from Plaud, or exception text.
- **A run's result:**
  - "ok"
  - "skipped: busy (`plaud-full`)"
  - "offline"
  - "Plaud unavailable"
  - "rate limited until 14:40"
  - "sign-in needed"
  - "paused: unexpected changes"
  - "paused: everything looks new"
  - "stopped: consent list"
  - "listing shrank"
  - "partial: 3 recordings failed"
- **A recording's result:**
  - "up to date"
  - "updated: speakers, title"
  - "failed: Plaud unavailable"
  - "gone from Plaud"
  - "re-tagged private: title changed"
  - "kept out by title: delete it by hand"
- **A reply:**
  - "Queued"
  - "Queued after the current sync (started 14:20)"
  - "Already queued"
  - "Sync everything ran less than an hour ago"
  - "The Plaud worker isn't running (last reported 14:21): your request waits until it is."

## 11. Alerts

- **The machinery is reused** (`ops._alert` and `_conclude`), with the lock key and the job's name
  made parameters; today `run_job` hard-codes `ops`. The jobs are `plaud-sync` and `plaud-full`.
  Each failure class keeps its own alert state, so a breaker trip during a sign-in failure pushes
  once for each.

| Class | Pushes | Title | Next step (runbook) |
|---|---|---|---|
| `PlaudAuthError` (a refused refresh) | at once | "recordings: Plaud sign-in needed" | `make plaud-sign-in` on the Mac |
| `CircuitOpen` (the breaker) | at once | "recordings: Plaud edits paused (unexpected changes)" | look at the field names; release or rebaseline, then reset |
| `FloodGuard` | at once (controller's call) | "recordings: Plaud sync paused (everything looks new)" | Compare, then import by hand |
| `ConsentListError`, `ConsentStop` | at once | "recordings: Plaud sync stopped (consent list)" | fix the list, or take the recording out |
| any other run failure: offline, Plaud unavailable, rate limited, a shrunken listing, a 403, a bug | after 2 hours with no good sync (decision 5) | "recordings: Plaud sync failing" | usually nothing: it heals itself |
| `--full` failed | when the monthly run fails, after retrying its failures | "recordings: Plaud full check failed" | Status's count; Re-sync those |

- **"A good sync"** is a run that got a good listing (§6.2) and ended without a run-level failure. A
  single recording's failure doesn't spoil it. A turn skipped as busy is neither good nor a failure.
- **Bodies are fixed templates** (main spec §14): the job, the error class, the exit code, counts,
  then "See Status". Never a title, an ID, a path or error text.
- **Cadence** (main spec §14): one push at onset, a reminder every 24 hours, and one "works again" push
  only after an onset push. A single recording's failure never pushes (P4).
- **The sync's own dead-man's check** (decision 4):
  - It is a second Healthchecks check. Its URL is the secret `RECORDINGS_DEADMAN_SYNC_URL`, mounted
    in `worker` only. The backup's check is unchanged, and they share nothing.
  - The worker pings it after every good run, Sync everything's listings included. It never pings
    `/start` or `/fail`, so Healthchecks speaks only when good runs stop.
  - **Period 20 minutes, grace 3 hours (controller's call).** The grace is longer than the 2-hour
    rule. A living worker's push, with its reason, always comes first. Healthchecks speaks when the
    worker can't: dead, wedged, or with ntfy broken.
  - It is paused while the schedule is off (runbook).
  - `alert-test` pings both checks with `/log`.
- **Delivery rules are unchanged:** https only, no redirects, a 10-second timeout (main spec §14).
- **The runbook gains "When the Plaud alert comes":** one row per title, with its next step (P5), as
  the mirror's table does.

## 12. Operations

### 12.1 The `worker` service

- **The same image and tag** as `web` and `backup`, with `pull_policy: never`. Its
  `hostname` is `recordings-worker`, its `command` is `["recordings", "worker"]`, and
  `RECORDINGS_ROLE` is `worker`.
- **Hardened like the others:** a read-only root file system, `cap_drop: ALL`, `no-new-privileges`,
  and `/tmp` as a 1 GiB tmpfs (which the worker doesn't use for media). Log rotation, and the
  deployment's user and group.
- **Process settings:** `init: true` (ffprobe runs as a child), `restart: unless-stopped`,
  `stop_grace_period: 30s`, `cpu_shares: 512` (between `web`'s 1024 and `backup`'s 256), and
  `oom_score_adj: 300` (`backup` goes first, never `web`).
- **Mounts,** long syntax with `create_host_path: false`:
  - the config, read-only
  - the archive, read-write
  - `/state`, read-write
  - the `index` named volume, read-write
  - the consent list, read-only, from the new `RECORDINGS_CONSENT_HOST`
  - `/plaud`, read-write, from the new `RECORDINGS_PLAUD_HOST`
- **Secrets:** the ntfy topic, the ntfy token if one is set, and `deadman_sync_url`. No restic, NAS,
  `known_hosts` or backup dead-man secret.
- **Its own health check:** `recordings worker --alive` (interval 30 seconds, start period 60). It must
  never inherit the image's `:8000/healthz` probe (`Dockerfile:80`), which would mark it unhealthy
  forever and fail `make deploy`'s `--wait`.
- **No ports.**
- **Media downloads** go to the archive's `.tmp/plaud-dl/`, never to `/tmp`.
- **Not enforced:** traffic from `web` and `backup` to Plaud isn't blocked at the network layer.
  Docker can't do it without host firewall rules or a proxy. What keeps Plaud out of them is that
  they hold no token. This is a known limit.

### 12.2 Doctor

- **A new role, `--role worker`:**
  - the archive's writer checks, as for `web`: the sentinel, `writer_id`, hard links, the index, and
    `state.db`
  - **the consent list is required:** missing, unreadable or holding no IDs is a problem, because the
    sync stops with exit 78. Lines that look like IDs but can't be read are a warning.
  - **the token store:** missing is a problem ("sign-in needed: run `make plaud-sign-in`"). It reports
    the file's mode and owner (wider than 0600 is a problem: the worker refuses it), the access
    token's expiry, and whether a refresh token is present. Never a value, never a hash.
  - the ntfy secrets and `deadman_sync_url` are allowed. Restic and NAS secrets are a problem.
- **`web` and `backup`:** a mounted `/plaud`, or a set `RECORDINGS_PLAUD_TOKEN`, is a problem.
- **Every role:** a `[backup] extra_paths` entry that holds the token's folder is a problem.
- **Doctor never calls Plaud.** `recordings plaud check` does.

### 12.3 Deploy, rollback and boot

- **`make deploy`** waits for the worker's health check, as it does for `web`'s.
- **The deploy guard** refuses while `ops` is held (as now), and while `plaud` is held by `plaud-full`
  or `plaud-catch-up`. `docker/ops-busy.py` takes the lock's name. A routine sync is safe to
  interrupt: fetch and reconcile are two steps, and `.tmp/` is swept. `FORCE=1` still overrides.
- **`make deploy-smoke`** (CI) runs the worker with no token and no consent list, and checks that it
  stays healthy and that doctor reports both.
- **Rollback stays a tag:**
  - `state.db` gains tables only (§5.5).
  - `recording.json` gains no field (§8.2).
  - `index.db` in WAL mode opens the same in an older release.
  - The token's folder is untouched.
  - The builder confirms that an older release ignores the new `[plaud]` keys (§17).
- **Rolling back across 2b:** the checkout's `compose.yml` has a `worker` whose command an older
  image lacks, and it would restart forever. The runbook's steps are `rc stop worker && rc rm -f
  worker`, then `git checkout` the tag's commit, then `make rollback TAG=<tag>`.
- **The boot unit** needs no logic change, because `up -d` starts every service. Its description
  becomes "the web, backup and worker containers".

### 12.4 Config, secrets and the settings test

- **`config.example.toml`'s `[plaud]` block** becomes:
  ```toml
  [plaud]                            # stage 2b (spec §9.1)
  schedule_minutes = 20              # minutes between scheduled syncs; 0 is off (deploy with 0)
  auto_import = true                 # import new recordings at each sync (nothing runs until tagged)
  full_sweep_days = 1                # check every recording against Plaud within this many days:
                                     # 1 while relabelling speakers in Plaud, then 7 (the default)
  keep_out_word = "noarchive"        # a Plaud title starting with this word is never downloaded
  auto_private_patterns = ["^\\s*private", "interview:"]
  consent_list = "/consent/consent-list.md"
  token_store = "/plaud/tokens.json" # the Plaud sign-in (worker only; `make plaud-sign-in`)
  api_base = "https://…"             # Plaud's API, from the copied client; tests use a fake
  ```
  - `recheck_days` goes: the rolling sweep replaced its hot window (main spec §9.1.1).
  - `keep_out_word` must be 1 to 32 letters, digits, `-` or `_`. Anything else stops the worker with
    a config error (exit 78), so it can never be empty and silently off.
- **Secrets:**
  - `RECORDINGS_PLAUD_TOKEN` leaves `config.py`'s `SECRETS`, the template's secrets comment and the
    README's table.
  - `RECORDINGS_DEADMAN_SYNC_URL` (stage 2b) joins them, with the same URL rules as the backup's.
- **`docker/deploy.example.conf`** gains `RECORDINGS_PLAUD_HOST` and `RECORDINGS_CONSENT_HOST`, and
  the test that every compose variable is documented covers them.
- **The settings test** (CLAUDE.md, "Settings"):
  - Each new key gets its row in `packages/ui/tests/test_template_settings.py`, with the reader that
    uses it.
  - `schedule_minutes` and `auto_import` leave `LATER` and get their readers. `recheck_days` leaves
    with its key, and `plaud_token` leaves `LATER_SECRETS`.
  - `deadman_sync_url` gets a `SECRET_VALUES` row.
- **`.gitignore` and `.dockerignore`** gain `tokens*.json` and `**/.plaud/`.

### 12.5 The runbook

`docs/runbooks/first-run.md` changes like this:

- **1c, before stage 2b:** the spike sessions (§13), with a results table that holds dates, numbers
  and yes or no only.
- **A new page, "Plaud sign-in":** when to sign in, `make plaud-sign-in`, what it prints, and `rc exec
  worker recordings plaud check`.
- **Step 5, deploy 2b with the schedule off:**
  1. `install -d -m 700 $RECORDINGS_HOME/plaud`.
  2. Set `RECORDINGS_PLAUD_HOST` and `RECORDINGS_CONSENT_HOST` in `deploy.env`.
  3. Create the second Healthchecks check (period 20 minutes, grace 3 hours). Put its URL in the
     secrets folder as `deadman_sync_url`, and pause the check.
  4. Set `schedule_minutes = 0`, then `make deploy`.
  5. `make plaud-sign-in`, then `rc exec worker recordings plaud check`.
  6. `rc exec worker recordings doctor --role worker`, then `recordings alert-test`.
- **Step 6, the dry run:**
  1. `rc exec worker recordings plaud sync --dry-run --list`, or **Compare** in the Add panel.
  2. Check that the equation adds up, and that "here" equals the archive's Plaud recordings, with
     anything only in the archive explained.
  3. Check that waiting and missing are about the ~30 newer recordings plus the held titles, and that
     excluded matches the consent list.
  4. Check that each of the 2a import's `deferred-to-2b` IDs is waiting, and count any that Plaud no
     longer lists (§7.2).
  5. **Stop if anything doesn't fit.**
- **Step 7, the catch-up** (§7.6): `pre-import` backup, `baseline`, `sync`, `validate`, `post-import`
  backup, schedule on (`schedule_minutes = 20`, then `rc restart worker`), unpause the check, and
  watch for "All here". Then one line on its own: **"You may start tagging now."** (decision 7)
- **"When the Plaud alert comes":** one row per alert title (§11), with what it means and what to do.
- **Recovery steps:** the breaker (§9.4); a consent stop, or a recording kept out late, taken out by
  hand: move its folder to `trash/` with the app stopped, then `recordings reindex --allow-missing`.
- **"Rolling back across 2b"** (§12.3).
- **Decision 7's lines:**
  - Step 4's "Dan decided on 2026-10-22 that the first real tag waits until stage 2b's import dry run
    is done" becomes "Dan decided on 2026-10-23, refining 2026-10-22, that the first real tag waits
    until stage 2b's first real Plaud import (step 7) and the backup after it."
  - "Rolling back, until the first real tag" says the same. A rollback of step 7 is a restore from
    `pre-import`, which loses no tags because none exist yet.
- **"Restoring for real," step 6** (the 2a carry): the token is outside `state/`, so moving the whole
  state folder aside stays right. The step adds that `state.db`'s `-wal` and `-shm` files move with
  it.
- **Re-imports:** `rc stop worker` around a re-import, as `backup` is stopped today.
- **DAN-15,** before 2027-08-15: a final `--full` and its manifest, then `schedule_minutes = 0`, so
  a lapsed account doesn't produce a failing run every 20 minutes.

## 13. The spikes

The design depends on facts that only Plaud can give. Each spike is a supervised test plan, run with
Dan before 2b's deploy (runbook 1c), with the schedule off. Main spec §9.1 and §19 named two (the
token, and the ID form). The council's research added the rest. S13 and S14 became fake-server tests
(§16).

**Rules for every spike:**
- **Its script** (`scripts/plaud_spike.py`, reviewed before the session, run with `uv run`) prints
  only counts, yes or no, expiry times, HTTP status codes, field names, and hash prefixes (the first
  8 hex characters of a SHA-256). Never a token, a title, transcript text or a whole Plaud ID.
- **It has its own sign-in,** made for the session in a temporary `HOME`, kept in a scratch folder
  on the server (mode 0700) and deleted at the end. The production sign-in (`make plaud-sign-in`)
  comes after the spikes.
- **It only reads.** S9 alone changes anything in Plaud, and only by Dan's hands, on a throwaway clip
  whose title starts with the keep-out word.
- **Results** go into the runbook's 1c table and back into this spec. The fake server takes field
  names from S5 and S8, never values.
- **Three sessions:**
  - A: sign-in (S1, S2, S4, S3), about 45 minutes
  - B: data (S5, S7, S8, S10, S12, S11), about an hour
  - C: S9, once Dan has a throwaway clip, about 30 minutes

**S1. The token's shape and lifetime.** Dan signs in with the vendor CLI under a temporary `HOME` on
the Mac, and the file is piped to the server's scratch folder.
- **Prints:** the key names, the token type, the access token's expiry, whether there is a refresh
  token, and its expiry if it has one.
- **Outcomes:**
  - The access token's life sets the refresh threshold (§6.4).
  - The file's format sets `token-import`'s parser and the fake's token endpoint.
  - A refresh token with a fixed expiry: Status warns 7 days ahead.
  - **No refresh token:** the server would need a new sign-in at every expiry, which fails §1's sixth
    point. Stop and ask Dan.

**S2. Refresh and rotation.** One refresh from the server with the session's sign-in. Then the old
refresh token is tried again. Then one page (`page_size` 1) is listed with the new access token.
- **Prints:** status codes, the new expiry, whether the access and refresh tokens' hashes changed,
  and the page's count.
- **Outcomes:**
  - Rotation on: write-before-use and the single-flight lock (§6.4) are essential, and are designed.
  - The old refresh token rejected: there is no fallback copy. Keep the atomic write, and add no
    `.prev` file.
  - The old token still accepted for a while: keep `tokens.prev.json` for one cycle, as a fallback.
  - **A refresh refused from the server's address** (a sign-in bound to the Mac): the design fails.
    Stop and ask Dan. A relay on the Mac is the only path, and it is outside 2b.

**S4. A refused refresh.** A copy of the session's sign-in has one character of its refresh token
changed, and is refreshed.
- **Prints:** the HTTP status, and the OAuth `error` code only (such as `invalid_grant`), never its
  description.
- **Outcomes:** `PlaudAuthError` matches these codes exactly. Everything else (5xx, the network) is an
  outage. If a refusal and an outage can't be told apart, any 4xx from the token endpoint counts as
  refused: it errs loud.

**S3. Does a second sign-in sign the first out?** With the server's session sign-in working, Dan
signs in on Plaud's web app, then with the Plaud MCP's `login` on the Mac (its own store), then with
the vendor CLI again in another temporary `HOME`. After each, the script makes one cheap call with the
server's sign-in.
- **Prints:** a table of each action against "still signed in: yes or no".
- **Outcomes:**
  - All yes: nothing changes. Dan's Mac tools and the server coexist.
  - Any no: record which action signs the server out, and ask Dan this question before the
    production sign-in.

> **The question (only if S3 finds a sign-out).**
>
> Scenario: signing in to Plaud anywhere else signs the server out. You use the class-notes and
> Plaud skills on the Mac most weeks. Each time one of them signs in, the server's sync stops, and
> your phone says "Plaud sign-in needed". What do you expect?
> - **A. The server owns the sign-in (Recommended).** You stop signing in to Plaud's tools on the
>   Mac. Class notes and Plaud lookups read from the archive and its mirror instead, which hold
>   every recording with Plaud's transcript and notes. Until those skills are changed to read the
>   archive, they don't work.
> - **B. The Mac owns it, and the server borrows it.** The server uses a copy of the Mac CLI's
>   sign-in. It works only if refresh tokens don't rotate (S2). If they do, whichever side refreshes
>   first signs the other out.
> - **C. Take turns.** Keep both. Each time you use a Mac Plaud tool, the server stops until you run
>   `make plaud-sign-in`, and you get one push each time.
>
> Checked before recommending A: §1's sixth point (upkeep must not become a chore, which C fails);
> S2's rotation result (B fails if tokens rotate); and main spec §7.4's "paths around the
> allow-list", which already says the Plaud MCP and skills hand external agents every recording,
> private ones included. A moves Dan off that path. What could go wrong: class-notes depends on
> Plaud's share links, and may need work before it reads the archive.

**S5. The listing's shape, and a cheap change signal.** Page 1 is listed at page sizes 5, 20, 50, 100
and 500. Then the whole listing is taken twice, a minute apart. (The council's S6, paging stability,
is folded in here.)
- **Prints:**
  - the row's field names and types
  - the rows returned at each size (the clamp)
  - any total or next-page field
  - the order, from start dates printed as dates
  - fields that could mark a change (an edit time, a version, flags)
  - for each full pass: the count, unique IDs, repeats across pages, and a hash of the sorted ID list
  - the census of ID forms
- **Outcomes:**
  - The page size, and the clamp, set the client's page size.
  - An empty page ends a listing, as designed. If there is a total, it is compared too.
  - Two passes that differ with nothing recorded meanwhile mean the paging drifts. Then the client
    lists twice when the count is within 5 of the last good listing, and takes the union.
  - **A change marker:** §9.3, renames within one sync. S9 confirms which edits move it.

**S7. The ID form** (main spec §19's own spike). The canonical listing IDs are compared with the
archive's Plaud references, read through the index; no ID is printed. For 3 matched pairs, the audio
is downloaded to scratch and hashed against the archive's. Then one recording is fetched by its `of_`
form and by its bare hex.
- **Prints:** counts (listed, here, listed but not here, here but not listed), 3 hash matches as yes
  or no, and two status codes.
- **Outcomes:**
  - The hex is equal and the audio identical: the compare by canonical ID stands, and the hash merge
    is a backstop.
  - **The hex differs:** every old recording looks new. The flood guard holds the schedule. The
    catch-up then downloads each recording and merges it by hash (main spec §6.4): one download per
    old recording, once, slowly. Dan decides when before step 7.
  - Whether the bare hex works doesn't matter, because the client uses the listed form. It is
    recorded.

**S8. The envelope on the real account, and volatility.** Five recordings (a recent one, an old one,
one with renamed speakers, one without notes if there is one, the longest) are each fetched twice, a
minute apart.
- **Prints:**
  - the key sets of the envelope and its blocks
  - the data types
  - which blocks are inline and which are behind a link
  - the links' `X-Amz-Expires`, and the audio URL's expiry
  - segment counts
  - whether any `embeddingKey` repeats across the five (a count). Main spec §12.6a asks this of 2b's
    spike, for stage 3b.
  - whether the normalised hash is equal across the two fetches
- **Outcomes:**
  - Equal hashes: the drop list holds.
  - **Different hashes:** there is a volatile field. It joins the drop list, with a normaliser
    version bump, before step 7, so the breaker never meets it.
  - Either link lifetime works with §6.3.
  - An unknown block type is handled before the baseline.

**S9. What an edit looks like.** This needs Dan's hands. He records a short clip that isn't private,
and titles it with the keep-out word, so no sync ever imports it. Its part hashes are printed. Then,
one at a time, Dan:
- renames a speaker;
- renames the title, keeping the word;
- regenerates a note, and adds an in-app note;
- edits a transcript line;
- moves it to a folder;
- deletes it to the trash, then restores it.

After each, it is fetched and listed again.
- **Prints:** which part hashes changed, which listing fields changed, and the seconds until the
  change showed. After the deletion: whether it is listed, and the status of `files/{id}`.
- **Outcomes:**
  - A speaker rename changes the speakers part: §15's second criterion holds as designed.
  - A listing field that moves on a rename: §9.3.
  - A delay of seconds: Re-sync's reply may say "Plaud hasn't caught up yet".
  - The trash removes it from the listing: "gone from Plaud" works. If it is still fetchable, "gone"
    is decided from the listing alone, as designed.
  - A folder move that adds a field: into the drop list, or accepted before the baseline.

At the end, Dan deletes the clip in Plaud.

**S10. Pacing.** Twenty calls at one a second.
- **Prints:** the names of any rate-limit headers, and whether `Retry-After` appears.
- **Outcomes:** limit headers set the pacing and the budget below them. With none, one call a second
  and a 10-minute budget stand. Never provoke a 429.

**S12. Time zones.** For 3 recordings Dan remembers (their local time and zone), Plaud's start and
creation times are printed as returned, with their offset from his memory.
- **Outcomes:** if the start time is UTC, `recorded_at` is it in the default zone, as designed. If it
  is local time marked `Z`, it is read as local in the default zone. Record which. This must be
  settled before step 7, because the time in a recording's ID never changes (main spec §6.2).

**S11. A large download.** The longest recording is downloaded to scratch, then deleted.
- **Prints:** its size, the seconds taken, the throughput, whether `Content-Length` is present,
  whether `Range` is honoured (206), and whether the link's expiry passed during the download.
- **Outcomes:** they set the stall timeout and the disk guard's estimate. If a link can expire during
  a download, the client resumes with `Range` when it is honoured, and otherwise restarts once with a
  fresh envelope.

## 14. Changes to the main spec and other docs

The plan's first task applies these to `2026-10-08-recordings-design.md`, each marked "(Dan,
2026-10-23)" or "(stage 2b design, 2026-10-23)", and to the other files named. Spike results are
added as they arrive. In the main spec's list, a bold § is the main spec's own section, and a § in
brackets is this spec's.

**The main spec:**
- **§2, "Decisions that replace earlier notes":** the grace period for placeholder titles no longer
  carries over (decision 1). The consent list still does.
- **§3:**
  - "One image, two containers" becomes three: `web`, `backup` and `worker`. The diagram gains
    `backup`.
  - After "Why one writer": one writer means one machine. Several of its processes may each hold one
    `Writer`, under the shared locks, `state.db` and `writer_id` (§5.1).
- **§5:**
  - The `[plaud]` keys: `schedule_minutes`, `auto_import`, `full_sweep_days`, `keep_out_word`,
    `auto_private_patterns`, `consent_list`, `token_store` and `api_base`.
  - **The token's location:** the token-store bullet becomes "`tokens.json` in its own host folder
    (`RECORDINGS_PLAUD_HOST`, `/srv/recordings/plaud`, mode 0700), mounted read-write in `worker`
    only, at `/plaud`; never backed up or mirrored".
  - The secrets list gains the sync's dead-man URL.
  - `deploy.env` gains `RECORDINGS_PLAUD_HOST` and `RECORDINGS_CONSENT_HOST`.
  - Doctor's roles gain `worker` (§12.2).
- **§6.4:** the `plaud` lock sits beside `ops`, outside the lock order. The worker takes a recording's
  lock only around local work (§5.4).
- **§6.8:**
  - `state.db` is mounted in every container.
  - It gains the tables of §5.5, with no version bump.
  - "The same folder holds the Plaud token store" is removed (the token has its own folder).
  - The rebuild after losing `state.db` also seeds the auto-private marker from `plaud.title_seen`
    (§8.2) and the baseline (§9.5).
- **§7.4:**
  - "Everything comes in, with one exception" becomes two: the consent list, and the keep-out word
    (§7.3).
  - The automatic private tags are rewritten as §8.1: no grace period, the marker, never run twice on
    a checked title, the listing alone suffices, and the late keep-out word.
  - It gains the exception sentence of §8.3.
- **§9.1:**
  - "Recent recordings are checked again for a while, the way `audio-router`'s hot window does"
    points to the rolling sweep instead.
  - The compare matches by ID, and the hash match happens at import (§7.2).
  - The worker bullet's wording becomes: "Every Plaud call, and every write that comes from Plaud,
    happens in the `worker` container, through its own `Writer`. UI buttons ask it to act through
    request rows in `state.db`" (§5.3).
  - The consent list accepts both forms in any case, counts unreadable lines, and an ID listed after
    import stops the whole sync (§7.3).
  - It gains the keep-out word.
  - The grace paragraph is replaced by decision 1.
  - **The token paragraph:** the location; refresh before expiry; "a refused refresh raises an
    alert" instead of "a 401 raises an alert"; and `make plaud-sign-in`.
  - The spike list points to this spec's §13.
- **§9.1.1:**
  - the breaker's 24-hour window, what continues while it is open, and its reset (§9.4)
  - the flood guard (§7.5)
  - the listing's rules (§6.2)
  - doubling only once settled (§9.2)
  - the baseline's definition and command (§9.5)
  - "A 401 stops the run and raises an alert" becomes a refused refresh (§6.4)
  - Re-sync for a selection (§9.6)
  - removals counted until 3a (§9.7)
  - Status's contents, including the census of ID forms (§10.2)
  - **`full_sweep_days`:** 1 in the template while relabelling, 7 when unset (§9.2)
  - the fake server's injections extended (§16)
- **§9.3, the mapping:** deferred rows that Plaud no longer lists stay in `audio-router`'s frozen
  archive (§7.2).
- **§12, navigation:** the Add page exists from 2b. The top bar is "Library, Tags, Add, Status" until
  3b adds People and Insights.
- **§12.1 and §12.3:** the arrivals cue, the selection bar's Re-sync, and the Details lines (§10.3).
- **§12.4:** replaced by §10.1.
- **§12.5:** gains the sync panel and its Needs attention entries (§10.2).
- **§14:**
  - "They fire on: a Plaud 401" becomes a refused Plaud sign-in, a breaker trip or the flood guard,
    a consent stop, a Plaud sync failing for 2 hours, and a failed `--full` (decision 5).
  - **The dead-man's switch** becomes two checks: the backup's, unchanged, and the sync's (decision
    4, §11). Neither shares the other's URL.
- **§15:**
  - The worker publishes no port and adds no HTTP route. Only `worker` holds the token. Egress isn't
    blocked at the network layer (§12.1).
  - "Containers: both run as non-root" becomes all three.
- **§15.1:** "not … the Plaud token store" stays, now about its own folder. Doctor reports a problem
  when `extra_paths` holds it.
- **§16:** gains the tests in this spec's §16.
- **§17 and §17.1:** there is no worker in either demo, and the panels are canned (§10.5).
- **§19:**
  - Step 5: the spikes of §13, the token's folder and the second check.
  - Step 6: the equation.
  - Step 7: §7.6's order.
  - The disposable window ends at the first tag after step 7 and its backup (decision 7).
- **§20, stage 2b:**
  - The worker bullet narrows to Plaud's calls and Plaud's writes (§3).
  - A compose bullet (§12.1).
  - "2b is done when" becomes §15's list.
  - "If 2b runs long" becomes §2's cut, and notes that `state.db` only gains tables.
- **§22:**
  - Decision 19 is replaced by Dan's decision 1 of 2026-10-23.
  - Decision 35's "A private tag is always applied before any content is written" gains §8.3's
    exception.
  - Decision 31 gains the token's location.

**Other files:**
- **`CLAUDE.md`:**
  - The Privacy bullet changes as §8.3 says.
  - The archive's docs are written "whenever a Writer opens" becomes "whenever the web app's or the
    CLI's Writer opens (the worker's doesn't, §5.2)".
  - A new bullet: "The Plaud client is read-only and runs only in the worker. `recordings_ui` never
    imports the client, the token store or the sync."
- **`README.md`:**
  - A paragraph on stage 2b.
  - **Decision 7's line:** "Decided 2026-10-22: the first real tag waits until stage 2b's import dry
    run is done" becomes "Decided 2026-10-23, refining 2026-10-22: the first real tag waits until
    stage 2b's first real Plaud import and the backup after it (runbook step 7)".
  - The config table loses the `RECORDINGS_PLAUD_TOKEN` row, and gains the Plaud sign-in (its own
    folder, `make plaud-sign-in`) and `RECORDINGS_DEADMAN_SYNC_URL`.
  - The Docker section: `make deploy` starts web, backup and worker; the guard also waits for Sync
    everything and the catch-up; and the `plaud-sign-in` target.
- **`docs/runbooks/first-run.md`:** §12.5, including decision 7's lines.
- **`config.example.toml`, `config.py`, `docker/compose.yml`, `docker/deploy.example.conf`,
  `docker/recordings.service`, `docker/ops-busy.py`, `docker/deploy-guard.sh` and
  `test_template_settings.py`:** §12.
- **`Makefile`:** `plaud-sign-in`, listed by `make help`, in GNU make 3.81 like every target.
- **The tagging part A spec, §1:** a note that decision 7 refined when tagging starts.

## 15. Done when

Main spec §20's four criteria, made testable, and what Dan's decisions add. "Fake" means the fake Plaud
server (§6.6). "Real" means a runbook step.

1. **The dry run accounts for every listed ID.**
   - **Fake:** a property test over random accounts, with repeats across pages, both ID forms, consent
     and keep-out rows, and partial listings. Every listed ID is in exactly one of here, waiting,
     excluded or missing; repeats count once; the four add up to the listed total.
   - **Real:** step 6's dry run adds up. "Here" equals the archive's Plaud recordings, and anything
     only in the archive is explained. Waiting and missing are about the ~30 newer recordings plus
     the held titles. Excluded matches the consent list. Dan signs it off before step 7.
2. **A speaker rename in Plaud shows up within the sweep window.**
   - **Fake, with an injected clock:** a speaker renamed in a recording whose interval is at its cap.
     Within `full_sweep_days` (1) there is a new snapshot, a new Plaud transcript output with the new
     name, and "speakers" in Updated on Plaud. With Re-sync, at once.
   - **Real:** S9 shows a rename changes the speakers part. After step 7, one real rename shows at
     once with Re-sync, and a second shows by the next day without it.
3. **`--full` exits 0.**
   - **Fake:** a sound account exits 0 and writes the manifest. A recording that always fails makes
     it exit 2, with the count. Killed part-way, it resumes.
   - **Real:** after step 7, `rc exec worker recordings plaud sync --full` exits 0.
4. **A refused sign-in raises an alert** (it replaces "a 401 raises an alert").
   - **Fake:**
     - An expired access token that refreshes sends no push.
     - A 401, then a refused refresh, sends exactly one push, "Plaud sign-in needed", and none
       again within 24 hours.
     - Scheduled runs then make no Plaud call until the token file changes.
     - A new sign-in brings one "works again".
   - **Real:** not provoked, because it would break the real sign-in. `alert-test` proves delivery.
5. **Decisions 1 to 3 hold** (fake):
   - A placeholder-titled recording comes in at the next run, untagged.
   - Renamed to a private title, it is tagged `private` at the next run, even when its fetch fails.
   - A keep-out title is never requested (the request log).
   - A late keep-out title gets `private` and a flag.
   - A `private` tag removed by Dan stays removed through 100 runs and a `reindex`. It comes back only
     when the title changes to one that matches, with its note.
6. **Nothing stops silently** (decisions 4 and 5):
   - Pings go only after good runs.
   - A Plaud outage of 1 hour sends no push; at 2 hours it sends one; recovery sends one.
   - The breaker, the flood guard and a consent stop push at once.
   - A worker that stops writing its heartbeat fails its health check, and Status says so.
7. **Nothing leaks:** a whole sync at DEBUG leaves no token, `X-Amz-` query or title in any log,
   alert, Status view, `ops_runs` row or request result.
8. **Rollback stays a tag:**
   - `SCHEMA_VERSION` is still 1 and `MIGRATIONS` is empty.
   - A recording written by the 2b import validates against 2a's model and schema.
   - `make deploy-smoke` passes.
9. **The interface:**
   - Add and Status work at 1,280 and 320 pixels.
   - Re-sync works on a selection.
   - The arrivals cue appears.
   - Demo and Pages draw the panels with no network.
10. **Operations:**
    - `make deploy` waits for the worker's health.
    - The worker stays healthy with no sign-in and no consent list.
    - `doctor --role worker` passes on the server.

## 16. Testing

**Every fixture is synthetic** (main spec §16; CLAUDE.md). No test reads a real archive, a real
envelope or a real title. The fake server's shapes come from spikes S5 and S8: field names only, with
invented values.

- **The fake Plaud server** (§6.6) injects:
  - a 401; a refused refresh, with S4's codes; a refresh that rotates, and one that doesn't
  - a 429 with `Retry-After`, within the budget and beyond it
  - 5xx and timeouts
  - short bodies; truncated audio
  - a link that has expired once, then works; a link that expires during a download
  - a clamped page size; an empty listing; a listing that shrinks; an HTML page with a 200; repeats
    across pages; drift while paging
  - the ID form flipping between bare and `of_`
  - reordered blocks; content moving between inline and a link
  - a volatile field that appears after run K
  - a recording that always answers 500
  - Dan's edits: a title, a speaker rename, regenerated notes, a deletion, a restore
- **Core, unit and property tests:**
  - the consent parser: `of_`, upper case, and typos counted
  - keep-out matching
  - the marker's rule (§8)
  - the compare's buckets, as a property
  - the sweep's arithmetic and the settled rule, with an injectable clock (`ops.Context.clock`'s
    pattern)
  - the breaker's window
  - the token store:
    - atomic writes, and the mode
    - a single refresh across two processes
    - a crash between the refresh's reply and the write: the next start still has a working file
  - the client:
    - `GET` only, plus the refresh `POST`
    - no `Authorization` on a presigned or redirected request
    - the listed ID in every URL
    - the timeouts
- **Two real processes** (flock needs them):
  - the worker importing beside the web's tag batch: no lost update, and the worker's longest lock
    hold under a bound well below the web's 2 seconds
  - the worker's automatic `private` landing between a tag dialog and its write
  - Undo after a worker write keeps both writes
  - 2a's `test_two_writer_processes_never_lose_an_update` still passes
- **The flow test,** `test_flow_2b.py`, in the style of `test_flow_2a.py`:
  - It initialises an archive and imports an `audio-router` fixture.
  - Then, against the fake: `baseline`, `sync --dry-run`, `sync`, the worker's loop for N ticks on a
    fake clock, and `--full`.
  - It checks the files through `Archive`, the Status view, alerts through `FakeNtfy`, and pings
    through a fake Healthchecks. It scans every log for tokens, `X-Amz-` and titles.
- **Guard tests that must fail without their fix:**
  - consent-listed and kept-out IDs are never requested (the request log)
  - `recordings_ui` never imports the client, the token store or the sync, and Pyodide never does
  - demo mode never writes a request row and starts no worker (`test_demo_guard`)
  - no title in logs, alerts, Status, `ops_runs` or request results
  - `SCHEMA_VERSION == 1`
  - doctor's `web` and `backup` roles report `/plaud` mounted as a problem
- **Settings:** every new key has its row in `test_template_settings.py`, with its reader (§12.4).
- **Compose:** `test_deploy_template.py`'s rules cover the new service: hardened, a hostname, a role,
  long-syntax binds, the commit tag, and a health check that isn't the image's. `compose_smoke.py`
  runs the worker with no sign-in and no consent list, and checks it is healthy and doctor reports
  both.
- **The UI, with Vitest:**
  - the request state (queued, running, done), and coalesced replies
  - the equation
  - the summary sentence's rules (true, or not shown)
  - the fixed phrases
- **e2e (Playwright), against demo mode:**
  - the Add page and the Status sync panel draw the canned view at 1,280 and 320 pixels
  - the buttons are disabled, with the demo's note
  - axe in light and dark
  - a clean console
- **e2e, live:** a new module with its own servers: `web` and `worker` against the fake Plaud server
  and a synthetic archive, not demo mode.
  - **Compare** shows the equation.
  - **Import selected** imports, and its toast reports the counts.
  - Re-sync on a selection updates the rows.
  - The arrivals cue appears after a scheduled tick.
  - Status shows a refused sign-in.
  - With the worker killed, the panel says it isn't running.
- **Pages** (`make pages-test`): the Add page draws its canned view on Pyodide, sends no request, and
  leaves a clean console.
- **Real-run checks** live in the runbook (steps 5 to 7), not in CI.

## 17. Left to the builder

**Builder's calls, with the recommended answer:**
- Module names and table columns, within §5.5 and §5.6.
- The exact words, within §10.6's phrases.
- Backoff and budget numbers, within §6.3, adjusted by S10.
- The worker's tick (2 seconds), its heartbeat (30 seconds) and `--alive`'s threshold (90 seconds).
- The Add page's look, within shadcn/ui, Tailwind and the brand (main spec §12.6).
- Where the DAN-15 manifest is written: outside the archive, with no titles, in a path the backup
  includes.
- `sources/plaud.py`'s exact shape against main spec §9.0's `Source`, with the `check()` that
  downloads no media.
- The vendor CLI's name and token format, from S1, in `make plaud-sign-in` and `token-import`.
- Whether the token endpoint needs its own `auth_base` setting, if S1 shows it on another host.
- How long finished requests and events are kept (7 and 90 days are the suggestion).

**To check during implementation** (checked when first used, not assumed, as in tagging part A §11):
- `urllib`'s `add_unredirected_header` keeps `Authorization` off redirects, and the opener refuses
  redirects as `alerts.py`'s does.
- An older release ignores the new `[plaud]` keys (`load_config` reads keys by name) and the new
  `state.db` tables.
- WAL for `index.db` on its named volume, with both `web` and `worker` opening it.
- `docker compose up --wait` honours the worker's health check.
- Healthchecks takes a 20-minute period with a 3-hour grace, and `/log` leaves its state alone.
- Shiny: reading a pending request every 2 seconds for one session, and `send_message` for its
  replies, as the tag service does.
- `Writer.add` holds a recording's lock while it copies the media in; a test bounds how long.
- ffprobe in the `worker` container.
- **The Library's first load is under 2 seconds** (runbook, after step 4) before 2b deploys.
  Otherwise the Library moves onto `index.db` first, as the runbook says.

**Carried from stage 2a** (2a rulings), and where 2b handles each:
- `index.db` in WAL mode (item 72d): §5.5.
- The auto-private residual (Task 7, and Task 9's minor 1): §8.2.
- Seeding last checked on a freshly adopted `state.db` (item 39): §9.5.
- The "Restoring for real" wording (item 35): §12.5.
- The Plaud items carried to 2b (item 94):
  - **Handled in 2b, because a rename or re-fetch makes them live:**
    - `speakers.source` following a rename to the newest Plaud transcript
    - degraded-only recordings, which get no Plaud outputs, counted on Status
  - **Carried to 3a:** the timing verdict going stale after a trim, which the duration compare
    surfaces; ties on the newest transcript; note-type numbering moving with `data_id` order; two
    Plaud IDs giving interleaved lineages; keeping Plaud's title for 3a to show.

## 18. Risks

Most serious first.

1. **A recording that must not be kept is downloaded before Dan acts.**
   - The window is until the next sync, at most 20 minutes after Plaud lists it. Decision 1 makes
     downloads earlier than the old 30-minute grace did.
   - **Mitigations:** the keep-out word works from the phone; the consent parser is fixed; the list is
     read at every run.
   - **What remains:** a recording not marked in time is in write-once media, in the backups and in
     the mirror until it is removed by hand. The `pre-import` and `post-import` snapshots keep
     whatever step 7 imported for good.
2. **The flow stops, and nobody notices.**
   - **Mitigations:** decision 4's check; the 2-hour rule; "Scheduled sync is off"; the breaker,
     consent and flood pushes; the heartbeat on Status.
   - **What remains:** ntfy and Healthchecks failing together, or Healthchecks' own notification
     not reaching Dan.
3. **A late private title sits untagged in the backups and the mirror** (decision 1's exception).
   - It lasts one sync after the rename, because the listing path doesn't wait for a fetch. The
     hourly backups keep it untagged until they age out.
4. **The sign-in model fails on contact** (S1, S2, S3): no refresh token, a sign-in tied to the
   Mac's address, or Mac tools that sign the server out. Then "rare and one command" doesn't hold.
   §13 says where to stop and ask Dan.
5. **The token chain is lost** when a rotating refresh crashes between the reply and the write, or
   two refreshes overlap.
   - **Mitigations:** write-before-use under one lock, and reading again after locking.
   - **The cost if it happens:** one push, and one `make plaud-sign-in`.
6. **Duplicates after an ID-form change.**
   - **Mitigations:** the flood guard, and the hash merge at import.
   - **The cost:** bandwidth, and a paused schedule until Dan looks.
7. **A volatile field mints snapshots.**
   - The windowed breaker can still let through up to about 5% of a day's checks before it trips.
   - S8's volatility test, before step 7, catches the common case.
8. **Titles in `state.db`.** `plaud_compare` holds the titles of recordings not yet imported, and
   `state.db` is backed up every hour. The title of a recording Dan later keeps out may stay in old
   `state.db` backups (no audio, encrypted). This is accepted, and stated here. Logs and alerts carry
   none (a guard test).
9. **Rate limits, or a flag on the account,** for an unofficial client polling every 20 minutes.
   - **Mitigations:** pacing, no parallel calls, a budget, and `schedule_minutes = 0` as the off
     switch.
10. **Tag clicks get "busy"** if the worker holds a lock longer than the web's 2 seconds.
    - **Mitigations:** locks only around local work, and a test that bounds the hold.
11. **A later change brings back a one-way door:** a schema version bump, or a new `recording.json`
    key. The guard tests catch both.
12. **The fixtures drift from Plaud's real shapes.** S5's and S8's field names feed the fake server.
13. **A wrong time zone is permanent in an ID.** S12 must settle it before step 7.
14. **The subscription lapses on 2027-08-29.** If the schedule isn't switched off, it fails every
    run, and a push follows after 2 hours. DAN-15's runbook step turns it off after the final
    `--full`.
15. **The Library's first load** may pass 2 seconds as recordings arrive. The runbook then moves the
    Library onto `index.db`, a 3a item pulled forward.
16. **The relabelling evening at scale.** With `full_sweep_days = 1` and thousands of recordings,
    the run budget may cap the sweep, so a full pass takes more than a day. Status shows the real
    oldest check, and Re-sync of a selection is the way round it.
