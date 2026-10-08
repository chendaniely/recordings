# recordings stage 2a (the archive moves in, protected): implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `audio-router`'s archive into a protected `recordings` archive on the homelab server. That needs:
- a locked write path
- the new `speakers` format and Plaud's segment fields
- `state.db`, a derived `index.db` and the archive sentinel
- the Plaud normaliser and reconcile, as pure functions
- a re-runnable import
- restic backups with a scheduled restore test, and the NAS mirror
- ntfy alerts and a dead-man's switch
- the Status page's backup and disk panels

Nothing in it calls Plaud.

**Architecture:**
- **Every write goes through one `Writer` per process.** It refuses to start without the archive's sentinel and this machine's `state.db`. It takes `flock` locks in a fixed order, and it changes `recording.json` only through `mutate(recording_id, op)`. `mutate` bumps `rev`, checks the file's content hash against the merge base kept in `state.db`, and fsyncs.
- **`index.db` is derived.** `recordings reindex` rebuilds it.
- **The Plaud normaliser and reconcile are pure functions** over the raw snapshots in `source/`. The import feeds them `audio-router`'s snapshots now, and stage 2b's sync will feed them Plaud's.
- **Operations run in a `backup` container,** through `recordings ops --loop`: the backup, mirror, check, restore test and disk check. Each run is recorded in `state.db`. Failures are pushed to ntfy, and each good backup pings a dead-man's switch. The UI's Status page reads those records.

**Tech Stack:**
- **Python:** Python 3.14's standard library (`sqlite3`, `fcntl`, `wave`, `csv`, `urllib.request`), pydantic 2.13.5. **No new Python dependencies.**
- **Tools:** restic 0.19.1 (pinned by SHA-256), rsync, OpenSSH, and ffprobe (from ffmpeg), in the image.
- **Delivery:** Docker Compose.
- **UI:** shinyreact 0.1.0 and React 19, for the Status page.
- **Testing:** pytest 9.1.1, Vitest 3.2.7, pytest-playwright 0.9.0.

**Spec:** `docs/superpowers/specs/2026-10-08-recordings-design.md`. This plan builds §20 stage 2a only, and argues from §3, §5, §6 (especially §6.4, §6.7 and §6.8), §7.4, §7.6, §9.1, §9.1.1, §9.3, §12.5, §14, §15, §15.1, §16, §19 and §22. Read the spec alongside this plan.

## Decisions made while planning (review these)

1. **One `Writer` owns every write, and `Archive` becomes read-only** (stage-1 "As built": "Writers get their own `Archive` instance, never the UI's").
   - `Writer` (`recordings/writer.py`) checks the sentinel, `state.db` and the writer ID when it is made, and holds its own `Archive`.
   - Stage 1's `Archive.add_recording`, `write_rendition` and `_merge_source` move onto it, as `Writer.add`, `Writer.write_rendition` and the duplicate merge inside `Writer.add`.
   - `Archive.find_by_sha256`, which scanned every folder, is replaced by `Index.find_by_sha256`.
2. **Every raw snapshot is one entry in `sources`.** `SourceRef` gains two optional keys: `fetched_at` (when the source returned that payload) and `sha256` (of the raw file's bytes).
   - Reconcile orders snapshots by `fetched_at`, then by their position in `sources`, never by file name (§9.1.1).
   - A re-run skips a snapshot whose kind, reference and `sha256` are already there.
   - Each `audio-router` recording also gets one `audio_router` reference: its catalog `uri`, such as `plaud/<file_id>`. That "keeps `audio-router`'s source and ID" (§9.3), and is how a re-run recognises Recorder and Pocket recordings.
3. **The writer's identity** (§3, §6.8):
   - `[archive] writer_id` in `config.toml` names it. It replaces `writer_host`. A config that still says `writer_host` fails loudly, with the fix in the message, because it names a host and the spec says identity never comes from a host name.
   - `recordings init` records the writer ID in `state.db`, beside the archive's UUID.
   - A process may write only when the config's writer ID, `state.db`'s writer ID and archive UUID, and the sentinel's UUID all agree.
   - The Mac's mirror has no `state/`, so it can never write.
4. **A stale edit is refused, not merged,** until stage 3 (§6.4: "Until stage 3, a stale or invalid edit goes to Needs attention instead").
   - **Merge bases are kept now:** the last eight revisions of each file, in `state.db`.
   - **The revision record has two phases** (pending, then committed). A crash between replacing the file and committing its revision is then recognised for what it is on the next write. It is never mistaken for an outside edit (Review Focus 1).
5. **`recordings reindex` is how an outside edit is accepted,** which `AGENTS.md` already tells agents to run.
   - It records each valid hand-edited `recording.json` as the new merge base, and flags an invalid one in `state.db`.
   - It then runs reconcile on every recording with Plaud snapshots, and rebuilds `index.db`.
   - It refuses to empty a non-empty index (§6.7).
6. **The Library keeps reading the files in 2a. `index.db` serves the writers and the import.**
   - The spec moves `[index]` here because stage 1 globs every folder on each load. The real archive is a few hundred recordings, which the glob handles.
   - The index's readers that matter, search and tag counts, arrive with stage 3a.
   - The Pages demo runs on Pyodide, where `sqlite3` is a separately loaded module, and nothing has checked that it loads there. So the UI's reading path must not import `sqlite3` or `fcntl` at module level.
   - Carried to 3a: the Library reads from `index.db`.
7. **The format change stays inside `recordings-archive@1`** (§22, decision 12).
   - **Every new key is optional:** `rev`, `title_by`, `plaud`, `speakers.source`, `speakers.labels`, `speakers.spans`, `SourceRef.fetched_at` and `SourceRef.sha256`, `MediaInfo.sample_rate` and `MediaInfo.channels`, and the segment fields `speaker_name` and `embedding_key`.
   - **Old files read unchanged.** An empty `speakers` still serialises as `{}`, through pydantic's `exclude_if`, which was checked in 2.13.5 on 2026-10-08.
   - **`rev`:** a file without it counts as revision 0, and the writer starts every new recording at 1.
8. **Plaud's outputs** (§6.5, §9.1.1):
   - **The transcript** comes from the `source_list` block whose `data_type` is `transaction`.
   - **A notes output** comes from every `note_list` tab, Dan's own highlights and memos included, and from the `outline` block. Each gets `note_type` `plaud-<tab>`, ASCII-folded.
   - **`transaction_polish` stays in `source/` only.**
   - **`version` is `plaud@1`,** the normaliser's version.
   - **`created_at` is the snapshot's `fetched_at`,** so a re-run writes byte-identical files.
   - **`inputs.part_sha256`** of a transcript is the hash of its normalised segment rows: text, timing and names.
9. **Removals wait for review** (§9.1.1). Three changes count as removals and are held while their snapshot is newer than `plaud.acknowledged_up_to`: a note tab that vanishes, a transcript that vanishes, and names that revert to "Speaker N" while the diarization fingerprint stays the same.
   - Held items are reported, and nothing is written for them.
   - Accepting them, by moving the marker, is stage 3's review queue.
10. **A recording with two Plaud IDs** (`audio-router`'s byte-identical pair) is reconciled per Plaud ID. Each ID's outputs cite its own snapshots, and the title follows the Plaud ID that was added first.
11. **The timing check** (§9.1.1, stage 2a) is recorded in each Plaud transcript's `meta.timing`. It compares Plaud's envelope duration and the last segment's end with the decoded media duration. The rule is `timing@1`, with a tolerance of 1 s or 1% of the media, whichever is larger.
   - It is stored, not recalculated, because both of its inputs are write-once.
   - Stage 6 relies on it, and may apply a stricter rule to the stored numbers.
12. **The import is driven by `audio-router`'s catalog** (§9.3). `catalog/catalog.csv` is the checklist, and every row gets exactly one disposition:
   - `new`
   - `new-private`
   - `duplicate` (merged into its `dup_of` row's recording)
   - `present` (already in this archive)
   - `no-audio`
   - `unplaced`, with a reason

   Two lists sit outside the rows:
   - **Recordings on disk with no catalog row** are reported as `unlisted` and skipped.
   - **`provenance.json`'s `ledger_only_no_media`** becomes the report's `ledger_only` list.

   **A real run refuses any unplaced row** unless `--allow-unplaced` is given.
13. **How `audio-router`'s outputs map** (§9.3: "keeping their engine names"):

    | `audio-router` output | In `recordings` |
    |---|---|
    | `words` | a `transcript` |
    | `merged` | a `transcript` |
    | `turns` from pyannote | `speakers` |
    | `summary` | `notes`, with `note_type` `audio-router-summary` |
    | any output whose engine is `plaud` | **skipped:** reconcile rebuilds these |
    | `fingerprint` and `embeddings` | **skipped:** voice data never enters the archive (§7.6) |

    - **Pocket's own transcript and summaries** become outputs with engine `pocket`.
    - **Google Recorder's untimed prose transcript** stays in `source/` (open question 4).
14. **The `media/private/` tier** is searched by file ID: one media file named `<file_id>.<ext>`, its snapshots under `raw/<file_id>`, and its outputs under `derived/<file_id>`. These recordings are tagged `private` with `by: you`, because putting them in that tier was Dan's own decision (open question 3).
15. **Mapping `time_source`** (§6.2's values). `audio-router`'s catalog gives its own values, keyed by source:

    | Source | `audio-router` value | `recordings` value |
    |---|---|---|
    | Plaud | `start_at`, `created_at` | `plaud` |
    | Recorder | `filename` | `metadata` |
    | Recorder | `file mtime (inferred)` | `mtime` |
    | Pocket | `recording_at` | `metadata` |
    | Pocket | `created_at` | `ingest` |
    | private | `filename` | `metadata` |

    Any other value leaves the row unplaced.
16. **The dead-man's switch URL is a secret,** as the ntfy topic is. §5 lists it under `config.toml`, but a ping URL from a service like Healthchecks carries its own token: anyone holding it can keep an outage quiet.
17. **What a backup holds** (§15.1) is an explicit list: the archive, `config.toml`, `docker/deploy.env` and a SQLite backup copy of `state.db`.
    - **`voice.db` and the Plaud token store are left out by construction,** because nothing names them.
    - **Snapshots are tagged `recordings`.** `--keep-tag pre-import` and `--keep-tag post-import` keep the milestone snapshots that §19 asks for.
    - **restic's `--host` is the writer ID,** so retention never depends on a container's host name.
18. **Cross-site protection applies to every request whose method isn't safe, across the whole app.**
    - 2a adds no write route to the UI: the import is a CLI step that Dan runs.
    - The guard is in place now, so every later route has it without opting in.
    - A request with no `Origin` and no `Sec-Fetch-Site` comes from no browser, so the guard lets it through.
19. **restic 0.19.1 is pinned by SHA-256.** The checksums come from the release's `SHA256SUMS`, checked 2026-10-08. `scripts/fetch_restic.py` installs it into `.cache/bin/` for CI and the Mac, and the Dockerfile installs it into the image. Nothing is installed system-wide.
20. **Media is measured by `wave` for WAV files, and by ffprobe for everything else.**
    - The synthetic fixtures are WAV, so the tests need no ffmpeg.
    - The image installs ffmpeg for ffprobe.
    - `recordings doctor` reports a missing ffprobe.
21. **Writing the archive's docs at startup** (§6.7; stage-1 "As built") happens when a `Writer` opens, and when the web app starts against an archive it may write. Both happen only once the sentinel is checked.

## Global Constraints

- **Python runs only through uv:** `uv sync`, `uv run …`, `uv add`. Never pip, never the system or Homebrew Python. Python is `3.14`. This plan adds no Python dependency. If one turns out to be needed, add it with `uv add --exact` in the package that uses it.
- **Node 22,** from `packages/ui/frontend/.nvmrc`. Install with `npm ci`. No global npm installs.
- **shinyreact is pinned exactly:** `shinyreact==0.1.0` and `@posit-dev/shinyreact` `0.1.1`. React stays external, mapped to `window.shinyreact`.
- **Check the docs before writing code** against any of these, and say in the commit what you checked. Never write from memory. If the docs don't cover it, say so instead of guessing.

  | What | Docs |
  |---|---|
  | shinyreact | the `/shinyreact-build-app` skill (after `make skills`) |
  | shadcn/ui | the shadcn skill |
  | Tailwind v4 | https://tailwindcss.com/docs |
  | restic | https://restic.readthedocs.io/en/stable/ (`075_scripting` for `--json`; `030_preparing_a_new_repo` for SFTP and REST) |
  | rest-server | https://github.com/restic/rest-server |
  | ntfy | https://docs.ntfy.sh/publish/ |
  | Compose | https://docs.docker.com/reference/compose-file/services/ (long-syntax `volumes`, `create_host_path`, `logging`, `hostname`, `secrets`) |
  | SQLite in Python | https://docs.python.org/3/library/sqlite3.html (`Connection.backup`) |
  | `flock` | https://docs.python.org/3/library/fcntl.html |
  | ffprobe | https://ffmpeg.org/ffprobe.html |

  Facts checked 2026-10-08 that this plan relies on:
  - A long-syntax bind's `create_host_path` **defaults to `true`**, so it must be set to `false` explicitly.
  - `fcntl.flock` with `LOCK_NB` raises `OSError` with `errno` set to `EAGAIN` or `EACCES`.
  - rest-server's `--append-only` refuses deletes with 403, so `forget --prune` cannot run through it.
  - restic's exit codes: 10 means no repository, 11 a lock failure, 12 a wrong password.
- **Recording ID:** `YYYYMMDDTHHMMSS±HHMM_<8 hex>`, in the folder `recordings/YYYY/MM/<id>/` (§6.2).
- **Write rules** (§6.4):
  - **Write once:** media, `source/` and `renditions/` are never overwritten. A write-once file is published with an exclusive hard link.
  - **Editable files** (`recording.json`, `my-notes.md`) are replaced through a temporary file that is fsynced, renamed into place, and followed by an fsync of the folder.
  - **Only `mutate` changes `rev`.** `mutate` refuses a file whose content hash differs from the one it last wrote.
- **Lock order** (§6.4):
  1. the people files
  2. `tags.yaml`
  3. `sha-<hash>` keys
  4. recordings, by sorted ID
  5. everything else (`index`, `catalog`)

  Taking an earlier lock while holding a later one is an error, not a wait.
- **Private:** a recording is private if any tag is `private` or under `private/`, in any capitalisation (`recordings.models.is_private_tag`).
- **No calls to Plaud in 2a.** No code, test or fixture opens a connection to Plaud.
- **Fixtures are synthetic.**
  - Never read the real `audio-router` archive's media or raw transcripts.
  - Its sanitized `media/catalog/` CSVs may be read for column names and counts only.
  - Model the synthetic archive on `audio-router`'s code: `archive/store.py`, `archive/sources/`, `docs/ARCHIVE.md` and `scripts/build_catalog.py`.
  - Invented names only (Ada, Grace).
- **Shell commands:**
  - **Never put an archive's media path in a shell command,** or the text `private/` followed by more path. Dan's tool hook blocks them.
  - **pytest's temporary folders on macOS sit under `/var/folders`,** which resolves into `/private/var`. Never paste one into a shell command. Inspect such files with the Read and Grep tools.
  - **In a `grep` pattern,** write `privat` rather than the full word followed by a slash.
- **Secrets:**
  - Never read token or credential files (`~/.plaud/tokens.json`, `.env`, `~/.secrets`, SSH keys).
  - A secret's value never appears in output, logs, `--json`, an alert, a commit or a test failure message. Code checks presence only.
  - Secrets come from `NAME`, or `NAME_FILE` (Docker secrets under `/run/secrets`).
- **No titles or text in operational output.** Reports, alerts, logs and `--json` carry recording IDs, catalog URIs and counts. They never carry a title, transcript text or a person's name (§10; `audio-router`'s "titles leak through prose" lesson).
- **No private details in anything committed.** Say "the homelab server" and "the NAS". No machine names, IP addresses, employer or course names, or real people. The repo is public.
- **Times:**
  - File stamps are ISO 8601 basic UTC, such as `20261008T143512Z`.
  - UI labels use 24-hour time and full dates, such as `2026-10-08 14:05` (§12).
- **Demo mode** runs on a fresh copy of `demo/archive/` with a temporary state folder. It reads no real config or secrets and makes no network calls (§17).
- **Commits** use Conventional Commits and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A crash between replacing `recording.json` and committing its revision** (a power cut mid-`mutate`) must neither lose the write nor make the next write report a false "edited outside the app". The test is in Task 5.
2. **An import killed halfway must finish cleanly when it runs again,** even with a `.tmp/` assembly left behind and some recordings already done: no duplicate recordings, sources or outputs, and no `.tmp/` left over. The test is in Task 9.
3. **An `audio-router` snapshot that is degraded or not JSON at all** (a data-link error, or an empty transcript) is kept in `source/`. It never feeds reconcile, and it is reported. The tests are in Tasks 7 and 9.
4. **A Plaud tab name or title in another script, or with punctuation** (`会議メモ / Q&A`), gives a safe file name and note type. No title appears in the dry run, the import report or an alert. The tests are in Tasks 7, 8 and 13.
5. **A restored copy with a truncated media file fails the restore test,** and raises an alert. It must not pass just because the files exist. The test is in Task 11.

## Execution checkpoints (Dan, 2026-10-08)

The execution method is **subagent-driven development**: a fresh implementer for each task, then a task review.

Between every two tasks, the forward and reverse review is a **council**:

- **When:** after a task passes its task review, and before the next task is dispatched.
- **Who:** three reviewers, run in parallel, each read-only:
  1. **Reverse.** It compares what was built, including any fix rounds and rulings, with the spec and every earlier task. Does it still match, and has anything the spec needs become harder?
  2. **Forward.** It reads each remaining task's text against the code as it actually stands: names, signatures, file paths, test expectations and the files each commit lists. It proposes exact OLD/NEW edits to the plan.
  3. **A risk lens chosen for the task just finished.** Each task names its lens under **Checkpoint lens**:
     - **data integrity and write safety:** the locked write path, the sentinel, the import and reconcile
     - **security and privacy:** anything touching secrets, the external view, cross-site protection or alerts
     - **operations:** Docker, backups, the mirror and the disk guard
- **Then:** the controller:
  1. merges the three reports into one list
  2. rules on any conflict between them, recording each ruling in the execution ledger
  3. applies the plan edits
  4. commits them as a `docs(plan): …` commit before dispatching the next task

So the plan in git always matches what is being built.

## Open questions for Dan

Each has a default the plan builds, so nothing blocks on it. Change the default before the task that owns it runs.

1. **Which dead-man's switch?** (Tasks 13 and 17)
   - **The constraint:** a dead-man's switch only works if something *outside* the homelab server notices the pings stop. A checker on the server goes down with it, and one on the NAS shares its power and network.
   - **The plan:** it sends an HTTP GET to a configured URL after each good backup. That works with Healthchecks.io (hosted, with a free tier), a self-hosted Healthchecks, or an Uptime Kuma push monitor.
   - **Please choose:** the service, and its period and grace. The plan assumes 1 hour, with a 1-hour grace.
2. **The NAS transport, and where retention runs.** The spike decides (runbook step 1b). Three consequences follow:
   - **rest-server `--append-only` refuses `forget --prune`.** Retention (24 hourly, 14 daily, 8 weekly, 12 monthly) would then have to run on the NAS, with restic and the repository password there, or the repository grows without limit. The plan's `[backup] prune = false` turns it off on the server.
   - **The NAS free-space check (§15.1) needs a shell account on the NAS** (`ssh … df`). An SFTP-only account can't give it, and neither can rest-server. The plan runs the check only when `[nas] ssh` is set, and reports "unknown" otherwise.
   - **The mirror needs rsync over SSH** whichever transport wins.
3. **The `media/private/` tier's layout.**
   - **What the plan assumes:** recordings there are named by file ID, as in the main tiers, so a search under `private/` finds `<file_id>.<ext>` plus `raw/<file_id>` and `derived/<file_id>`.
   - **Why it's an assumption:** the hook blocks reading that tier, and privacy forbids it, so the plan can't check.
   - **Please confirm:** look at the tier yourself, and check the dry run, which lists every private row it can't place.
4. **Google Recorder's untimed transcript.** The plan keeps it in `source/` only, where the Details tab lists it. The other option is to show it now, as a `recorder-transcript` notes output. Stage 4's Whisper replaces it either way.
5. **Pocket's segment times.** The plan reads them as seconds, as `audio-router`'s catalog fixtures model them. If the last segment ends past twice the recording's length, it reads them as milliseconds instead.
6. **ntfy.** The plan supports ntfy.sh with an unguessable private topic, or a self-hosted server without access tokens.
   - A server on the NAS can't alert when the NAS is down.
   - Access tokens are not built in 2a.
7. **The token and ID-form spikes.** §19 step 1 lists them before deploying 2a, but both call Plaud, and 2a makes none. The runbook moves them to just before 2b; neither blocks 2a.

## File map (stage 2a)

```
recordings/
  config.example.toml                       [archive] writer_id, [state], [index], [plaud], [nas], [backup], [mirror], [disk], [alerts]
  Makefile                                  restic, deploy (commit-tagged), demo-archive --media-from-archive
  docs/runbooks/first-run.md                §19 steps 1–4, with exact commands (Task 1)
  scripts/fetch_restic.py                   pinned restic into .cache/bin/ (CI and the Mac)
  scripts/compose_smoke.py                  the real compose file, once, against temporary folders
  demo/build.py                             builds through the Writer; a synthetic Plaud envelope; --media-from-archive
  demo/archive/                             rebuilt: archive.json, rev, Plaud outputs from reconcile
  docker/Dockerfile                         + ffmpeg, rsync, openssh-client, restic 0.19.1
  docker/compose.yml                        web + backup, long-syntax binds, state + index volumes, secrets, logging, hardening
  docker/deploy.example.conf                renamed from deploy.example.env
  .github/workflows/ci.yml                  restic for tests, compose smoke job
  packages/core/src/recordings/
    files.py          durable writes: temp + fsync + rename + fsync dir; exclusive publish
    sentinel.py       archive.json: read_sentinel, write_sentinel, SentinelError
    state.py          state.db: meta, revisions (merge bases), flags, ops runs, alert state, backup copy
    init.py           init_archive: the explicit `recordings init`
    refs.py           canonical source references (Plaud ID forms)
    index.py          index.db: rebuild (refuses to empty), upsert, lookups by SHA and source
    locks.py          flock locks, fixed order, re-entrant per writer
    writer.py         Writer, Incoming, Added, ops (AddTags, MergeIncoming, SetPlaudFields), reindex
    media.py          probe: duration, sample rate, channels (wave or ffprobe)
    disk.py           the disk guard: the sentinel marker plus free space
    ssh.py            ssh options shared by restic and rsync
    backup.py         restic: init, backup, forget/prune, check, restore test
    mirror.py         rsync --delete --delay-updates, refusing without the sentinel
    alerts.py         ntfy and the dead-man's switch
    ops.py            run_job, the schedule, ops --loop, status_summary
    doctor.py         `recordings doctor`, moved out of config.py and extended
    plaud/__init__.py
    plaud/normalise.py   the versioned normaliser, part hashes, the diarization fingerprint
    plaud/blocks.py      Plaud's JSON note blocks as Markdown (from audio-router's vendor_blocks.py)
    plaud/reconcile.py   reconcile: snapshots in fetch order → Plaud outputs + Plaud-owned fields
    sources/__init__.py
    sources/audio_router.py   the catalog, the plan (dry run), the run
    sources/ar_outputs.py     audio-router renditions and Pocket's payload → outputs
  packages/core/tests/
    conftest.py  test_files.py  test_init.py  test_index.py  test_locks.py  test_writer.py
    test_normalise.py  test_reconcile.py  test_media.py  test_import_plan.py  test_import_run.py
    test_disk.py  test_backup.py  test_mirror.py  test_alerts.py  test_ops.py  test_doctor.py
    test_flow_2a.py  test_runbook.py
  packages/ui/src/recordings_ui/
    hosts.py          + WriteGuard (cross-site writes)
    status.py         the Status view (imports the state code lazily, so Pages stays safe)
  packages/ui/frontend/src/
    lib/nav.ts  lib/nav.test.ts  components/StatusPage.tsx  (TopBar, App, DetailsTab, types.ts change)
  packages/ui/tests/  test_writeguard.py  test_demo_guard.py  test_status.py  (+ e2e status test)
```

## Carried from stage 1 (the "As built" triage)

| Stage-1 item | Where it lands |
|---|---|
| `fsync` the file and its parent folder on every write | Task 2 |
| A duplicate merge must not drop Plaud's outputs | Task 5 (the merge keeps the second copy's outputs, decisions and notes), Task 7 (reconcile) |
| Writers get their own `Archive` instance | Task 5 |
| Compose binds use `create_host_path: false` | Task 14 |
| `doctor` reports secrets even when the config fails, and probes hard-link support | Task 16 |
| `validate` checks the media file exists, and handles a broken `recording.json`'s renditions | Task 3 |
| The demo guard test (§17: no network, no secrets) | Task 10 |
| Write the archive docs at startup (§6.7) | Task 5 |
| A Docker `/healthz` smoke test in CI; running the real Compose once as UID 1000 against a temporary archive | Task 14 |
| Harden Compose with `read_only`, `cap_drop` and `no-new-privileges` | Task 14 |
| Clean up the demo temp folders | Task 10 |
| `RawSource.added_at` must be an aware datetime | Task 3 |
| `allowed_hosts` entries with a port or scheme are rejected | Task 10 |
| `base_url` is type-checked in config | Task 2 |
| `find_by_sha256` moves into the index; a lock or changed-since-read check (both listed under stage 3) | Tasks 4 and 5 |
| `ruff check` in CI | **2b.** The repo has no lint-rule set yet, and a global ruff config on Dan's Mac reports 37 findings that CI's defaults wouldn't. Choosing rules is its own small change. |
| UX: page title, favicon, phone layout, the pane's error state, Details dates, the header's time with its offset | **3a,** with the Library work |
| Measure re-renders with a real hour-long transcript | **3a.** The real archive is in the Library after Task 9's first real run. |

---
### Task 1: The first-run runbook

**Checkpoint lens:** operations.

The runbook comes first, because Dan runs two of its steps himself, in parallel with the build: the 15-minute server checklist and the NAS transport spike. The commands it names (`recordings init`, `backup …`, `import-audio-router`, `ops`, `alert-test`, `doctor`) are the ones the later tasks build. Task 17 adds a test that every command it names exists.

**Files:**
- Create: `docs/runbooks/first-run.md`
- Test: `packages/core/tests/test_runbook.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `docs/runbooks/first-run.md`, with the headings `## 1. Spikes`, `### 1a. Server facts`, `### 1b. NAS transport`, `### 1c. Before stage 2b (not needed for 2a)`, `## 2. Deploy 2a against an empty, initialised archive`, `## 3. Bring the source over` and `## 4. Import`, plus a `## Checklist` table. Later tasks amend it, as the forward review finds.

- [ ] **Step 1: Write the failing test**

`packages/core/tests/test_runbook.py`:
```python
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUNBOOK = REPO / "docs" / "runbooks" / "first-run.md"


def _text() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def _code_lines() -> list[str]:
    return [line for block in re.findall(r"```(?:bash|sh)?\n(.*?)```", _text(), re.S)
            for line in block.splitlines()]


def test_the_runbook_covers_steps_one_to_four_of_the_first_real_run():
    text = _text()
    for heading in ("## 1. Spikes", "### 1a. Server facts", "### 1b. NAS transport",
                    "### 1c. Before stage 2b (not needed for 2a)",
                    "## 2. Deploy 2a against an empty, initialised archive",
                    "## 3. Bring the source over", "## 4. Import", "## Checklist"):
        assert heading in text, heading


def test_the_runbook_holds_no_addresses_keys_or_tailnet_names():
    # why: the repo is public. The runbook uses variables and "the homelab server", never a
    # machine's real address or name.
    text = _text()
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text), "an IPv4 address"
    assert ".ts.net" not in text
    assert "BEGIN OPENSSH" not in text and "ssh-ed25519 AAAA" not in text


def test_no_secret_is_ever_typed_on_a_command_line():
    # why: shell history keeps command lines. Secrets go into files through an editor.
    for line in _code_lines():
        assert not re.search(r"\b(printf|echo)\b[^|]*>\s*\S*secrets/", line), line
        assert "RESTIC_PASSWORD=" not in line, line
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest packages/core/tests/test_runbook.py -v`
Expected: FAIL with `FileNotFoundError` for `docs/runbooks/first-run.md`.

- [ ] **Step 3: Write the runbook**

`docs/runbooks/first-run.md`:
````markdown
# The first real run (stage 2a)

Steps 1–4 of the spec's "order of the first real run" (§19). Steps 5–7 belong to stage 2b.
Each block says which machine it runs on. Nothing here prints a secret: a command that needs one
reads it from a file, and secret files are written with an editor, never typed on a command line,
where shell history would keep them.

Set these in the shell you use on the homelab server. They are examples; use your own values.

```bash
export RECORDINGS_HOME=/srv/recordings          # on the server's own data disk (spec §3)
export NAS_HOST=nas                             # how this server reaches the NAS over ssh
export NAS_USER=backup                          # the NAS account for restic and the mirror
export NAS_REPO=/volume1/backups/recordings     # the restic repository's folder on the NAS
export NAS_MIRROR=/volume1/recordings-mirror    # the read-only mirror's folder on the NAS
```

## 1. Spikes

### 1a. Server facts

About 15 minutes, on the homelab server. Write each answer in the checklist at the end.

1. **The archive has its own mount.**
   ```bash
   findmnt -no TARGET,SOURCE -T "$RECORDINGS_HOME"
   ```
   Expected: a `TARGET` that is `$RECORDINGS_HOME` or a parent made for the data disk, never
   `/`. If it shows `/`, stop and mount the data disk first: the archive must not sit on the
   system disk.

2. **The filesystem is local.**
   ```bash
   findmnt -no FSTYPE -T "$RECORDINGS_HOME"
   ```
   Expected: `ext4`, `xfs` or `btrfs`. Not `nfs` or `cifs`: `flock` and hard links must work
   locally (spec §3, §6.4).

3. **Free space.**
   ```bash
   df -h "$RECORDINGS_HOME"
   ```
   It needs room for the `audio-router` archive's size twice over, plus room to grow: once for
   the import copy in `import/`, once for the archive. The monthly restore test needs as much
   again, in `restore-test/`.

4. **The folders, and their owner.** `archive/` stays empty until `recordings init` (step 2), and
   it must live *inside* the mount, so an unmounted disk means a missing folder, which Docker then
   refuses to start without.
   ```bash
   sudo mkdir -p "$RECORDINGS_HOME"/archive "$RECORDINGS_HOME"/state \
     "$RECORDINGS_HOME"/restore-test "$RECORDINGS_HOME"/secrets "$RECORDINGS_HOME"/import
   sudo chown -R "$(id -u):$(id -g)" "$RECORDINGS_HOME"
   chmod 700 "$RECORDINGS_HOME/secrets"
   id -u
   id -g
   ```
   The two numbers go in `docker/deploy.env` as `RECORDINGS_UID` and `RECORDINGS_GID`.

5. **Hard links and `flock` work on this disk.**
   ```bash
   cd "$RECORDINGS_HOME/restore-test" && touch probe && ln probe probe.link \
     && flock -n probe true && echo "hard links and flock: ok"; rm -f probe probe.link
   ```
   Expected: `hard links and flock: ok`.

6. **Docker starts after Tailscale.** The app publishes on the server's Tailscale address. If
   Docker starts first at boot, that address doesn't exist yet, the port can't bind, and the app
   stays down until someone restarts it.
   ```bash
   systemctl show -p After docker.service | tr ' ' '\n' | grep -c tailscaled
   ```
   Expected: `1`. If it prints `0`, add the ordering, then reboot once and check that `web` is up:
   ```bash
   sudo mkdir -p /etc/systemd/system/docker.service.d
   sudo tee /etc/systemd/system/docker.service.d/after-tailscale.conf >/dev/null <<'EOF'
   [Unit]
   After=tailscaled.service
   Wants=tailscaled.service
   EOF
   sudo systemctl daemon-reload
   ```

### 1b. NAS transport

On the homelab server, with the NAS. The choice is restic over SFTP, or rest-server with
`--append-only` (spec §15.1). Try SFTP first: it needs nothing installed on the NAS.

1. **A key for the NAS account.** Add the printed public key to `$NAS_USER` on the NAS. Then
   compare the `known_hosts` line's fingerprint with the one the NAS shows for its own SSH
   service, before trusting it.
   ```bash
   ssh-keygen -t ed25519 -N "" -C recordings-backup -f "$RECORDINGS_HOME/secrets/nas_ssh_key"
   cat "$RECORDINGS_HOME/secrets/nas_ssh_key.pub"
   ssh-keyscan -t ed25519 "$NAS_HOST" > "$RECORDINGS_HOME/secrets/nas_known_hosts"
   ssh-keygen -lf "$RECORDINGS_HOME/secrets/nas_known_hosts"
   ```

2. **Does the account have a shell?** With a shell, the free-space check and the rsync mirror
   both work. Without one, only SFTP does.
   ```bash
   SSH_OPTS="-i $RECORDINGS_HOME/secrets/nas_ssh_key -o UserKnownHostsFile=$RECORDINGS_HOME/secrets/nas_known_hosts -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes"
   ssh $SSH_OPTS "$NAS_USER@$NAS_HOST" df -Pk "$NAS_REPO"
   ```
   Expected: one `df` line. "This account is currently not available" means no shell.

3. **The restic password.** Generate it into a file, then copy it into your password manager now.
   Without it, the backups can't be read.
   ```bash
   install -m 600 /dev/null "$RECORDINGS_HOME/secrets/restic_password"
   head -c 32 /dev/urandom | base64 > "$RECORDINGS_HOME/secrets/restic_password"
   ```

4. **restic over SFTP, against a spike repository.** In the repo checkout, `make restic` puts the
   pinned restic in `.cache/bin/`.
   ```bash
   make restic
   export RESTIC_PASSWORD_FILE="$RECORDINGS_HOME/secrets/restic_password"
   SPIKE="sftp:$NAS_USER@$NAS_HOST:$NAS_REPO-spike"
   .cache/bin/restic -r "$SPIKE" -o sftp.args="$SSH_OPTS" init
   .cache/bin/restic -r "$SPIKE" -o sftp.args="$SSH_OPTS" backup /etc/os-release
   .cache/bin/restic -r "$SPIKE" -o sftp.args="$SSH_OPTS" check
   .cache/bin/restic -r "$SPIKE" -o sftp.args="$SSH_OPTS" restore latest --target "$RECORDINGS_HOME/restore-test/spike"
   .cache/bin/restic -r "$SPIKE" -o sftp.args="$SSH_OPTS" forget --keep-last 1 --prune
   rm -rf "$RECORDINGS_HOME/restore-test/spike"
   ```
   Expected: each command exits 0, and `restore-test/spike/etc/os-release` appeared before the
   last line removed it.

5. **Only if SFTP isn't possible: rest-server with `--append-only`.** It runs in Docker on the NAS
   (see https://github.com/restic/rest-server). With `--append-only`, `forget --prune` fails from
   the server with 403, so retention then has to run on the NAS: stage-2a plan, open question 2.
   ```bash
   # on the NAS
   docker run -d --name rest-server --restart unless-stopped -p 8000:8000 \
     -v /volume1/backups/rest:/data -e OPTIONS="--append-only --private-repos" restic/rest-server
   docker exec -it rest-server create_user recordings
   ```
   The repository is then `rest:http://$NAS_HOST:8000/recordings/`, with
   `[backup] rest_username = "recordings"` in `config.toml`. Its password goes in
   `secrets/rest_password`, written with an editor like every secret file.

6. **Record the decision** in `config.toml` (step 2): `[backup] repository`, and
   `[backup] prune = false` for rest-server. Delete the `-spike` repository on the NAS.

### 1c. Before stage 2b (not needed for 2a)

The token spike and the ID-form spike (§9.1, §19) both call Plaud. Stage 2a makes no Plaud
calls, so they run before stage 2b's deploy, not now.

## 2. Deploy 2a against an empty, initialised archive

On the homelab server, in the repo checkout.

1. **Settings.** Edit both copies: in `config.toml`, `[archive] writer_id`, `[backup] repository`
   and `prune`, `[nas]`, `[mirror] target` and `[alerts] ntfy_server`; in `docker/deploy.env`, the
   UID and GID from step 1a, the Tailscale address as `RECORDINGS_BIND`, and the host folders.
   ```bash
   cp config.example.toml config.toml
   cp docker/deploy.example.conf docker/deploy.env
   ```

2. **Secret files.** One file each, readable only by `RECORDINGS_UID`. The ntfy topic and the
   dead-man's switch URL are typed into an editor:
   ```bash
   cd "$RECORDINGS_HOME/secrets"
   install -m 600 /dev/null ntfy_topic && ${EDITOR:-nano} ntfy_topic
   install -m 600 /dev/null deadman_url && ${EDITOR:-nano} deadman_url
   install -m 600 /dev/null rest_password
   ls -l
   cd -
   ```
   Expected: `deadman_url`, `nas_known_hosts`, `nas_ssh_key`, `nas_ssh_key.pub`, `ntfy_topic`,
   `rest_password` (empty unless step 1b chose rest-server) and `restic_password`, each `-rw-------`.

3. **Build, initialise once, start.** `rc` is shorthand for the project's compose command, with
   the image tagged by the commit (as `make deploy` tags it).
   ```bash
   export RECORDINGS_IMAGE_TAG="$(git rev-parse --short=12 HEAD)"
   alias rc='docker compose --env-file docker/deploy.env -f docker/compose.yml'
   rc build
   rc run --rm web recordings init
   rc run --rm backup recordings backup init
   rc up -d
   rc exec web recordings doctor
   ```
   Expected: `init` prints the archive's UUID; `doctor` reports no problems.

4. **Prove backup and restore.**
   ```bash
   rc run --rm backup recordings backup run
   rc run --rm backup recordings backup restore-test
   rc run --rm backup recordings alert-test
   ```
   Expected: the backup and the restore test both report `ok: true`, the phone gets the test
   push, and the Status page shows the backup and the restore test.

5. **Prove that a stopped backup raises an alert** (§20: "a killed backup raises an alert").
   ```bash
   rc stop backup
   ```
   Wait one period plus the grace of the dead-man's switch (stage-2a plan, open question 1). The checker's alert
   arrives on the phone. Then start it again:
   ```bash
   rc start backup
   ```

## 3. Bring the source over

**On the Mac**, from `audio-router`'s own copy of its archive: never the NAS's Drive copy, which
may be mid-sync. `AR_MEDIA` is the folder that holds `plaud/`, `recorder/`, `pocket/` and
`catalog/`. `HOMELAB` is your ssh name for the homelab server.
```bash
export AR_MEDIA="$HOME/path/to/audio-router/media"
export HOMELAB=homelab
cd "$AR_MEDIA" && find . -type f ! -name '.*' -print0 | sort -z | xargs -0 shasum -a 256 > "$TMPDIR/audio-router.sha256"
rsync -a "$AR_MEDIA"/ "$HOMELAB":/srv/recordings/import/
rsync -a "$TMPDIR/audio-router.sha256" "$HOMELAB":/srv/recordings/import.sha256
```

**On the homelab server:** check the copy, make it read-only, and run the dry run.
```bash
cd "$RECORDINGS_HOME/import" && sha256sum --check --quiet ../import.sha256 && echo "copy verified"
chmod -R a-w "$RECORDINGS_HOME/import"
cd -
rc run --rm -v "$RECORDINGS_HOME/import:/import:ro" web recordings import-audio-router /import --dry-run
```
Expected: `copy verified`, then a report in which every catalog row has one disposition and
`unplaced` is 0. If anything is unplaced, stop: the report gives each row's `uri` and reason.

## 4. Import

```bash
rc run --rm backup recordings backup run --tag pre-import
rc run --rm -v "$RECORDINGS_HOME/import:/import:ro" web recordings import-audio-router /import
rc run --rm web recordings validate /archive --deep
rc run --rm backup recordings backup run --tag post-import
```
Expected: the import reports no failures, `validate` reports no problems, and the Library lists
the imported recordings, most of them untagged.

**Rolling back, until stage 3's first tag** (§19): the deployment is disposable.
```bash
rc down
mv "$RECORDINGS_HOME/archive" "$RECORDINGS_HOME/archive.failed-$(date +%Y%m%d)"
mv "$RECORDINGS_HOME/state" "$RECORDINGS_HOME/state.failed-$(date +%Y%m%d)"
mkdir "$RECORDINGS_HOME/archive" "$RECORDINGS_HOME/state"
```
Then run step 2.3 from `rc run --rm web recordings init` on, and step 4 again. The restic
repository keeps the old snapshots; `recordings init` gives the new archive a new UUID.

## Checklist

| Fact | Answer |
|---|---|
| The archive's mount (1a.1) | |
| Filesystem (1a.2) | |
| Free space (1a.3) | |
| UID and GID (1a.4) | |
| Hard links and `flock` (1a.5) | |
| Docker after Tailscale (1a.6) | |
| NAS transport (1b) | SFTP / rest-server |
| NAS account has a shell (1b.2) | yes / no |
| Retention runs on (1b.5) | the server / the NAS |
| Dead-man's switch: service, period, grace | |
````

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run pytest packages/core/tests/test_runbook.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/first-run.md packages/core/tests/test_runbook.py
git commit -m "docs: the first-run runbook for stage 2a (spec §19 steps 1-4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Durable writes, the archive sentinel, `state.db` and `recordings init`

**Checkpoint lens:** data integrity and write safety.

**Files:**
- Create: `packages/core/src/recordings/files.py`, `sentinel.py`, `state.py`, `init.py`
- Create: `packages/core/tests/conftest.py`, `test_files.py`, `test_init.py`
- Modify: `packages/core/src/recordings/archive.py` (writes use `files`), `selfdoc.py` (imports, `validate` checks the sentinel), `config.py` (`writer_id`, `[state]`, `[index]`, `base_url`), `cli.py` (`init`; `docs` refuses without a sentinel)
- Modify: `packages/core/src/recordings/format/FORMAT.md`, `format/AGENTS.md`
- Modify: `config.example.toml`, `demo/build.py`, `demo/archive/` (rebuilt)
- Modify: `packages/core/tests/test_archive.py`, `test_cli.py`, `test_selfdoc.py`, `test_config.py`

**Interfaces:**
- Consumes: `recordings.models.FORMAT`, `recordings.selfdoc.write_docs(root) -> list[str]`, `recordings.archive.Archive`.
- Produces:
  - **`recordings.files`:**
    - `fsync_dir(path: Path) -> None`
    - `temp_name(path: Path) -> Path`
    - `write_bytes_atomic(path: Path, data: bytes) -> None`
    - `write_text_atomic(path: Path, text: str) -> None`
    - `unique_path(path: Path) -> Path`
    - `create_exclusive(path: Path, data: bytes) -> None`, which raises `FileExistsError`
    - `publish_exclusive(data: bytes, target: Path) -> Path`
    - `copy_file_synced(src: Path, dst: Path) -> None`
  - **`recordings.sentinel`:**
    - `SENTINEL = "archive.json"`
    - `class SentinelError(RuntimeError)`
    - `@dataclass(frozen=True) ArchiveIdentity(uuid: str, created_at: datetime)`
    - `read_sentinel(root: Path) -> ArchiveIdentity`
    - `write_sentinel(root: Path, identity: ArchiveIdentity) -> Path`
  - **`recordings.state`:**
    - `class StateError(RuntimeError)`
    - `class State(path: Path)`, with the properties `db_path` and `locks_dir`
    - `State.create(path, *, archive_uuid: str, writer_id: str) -> State`
    - `State.open(path) -> State`
    - `State.db()`, a context manager yielding a `sqlite3.Connection`
    - `State.meta(key) -> str | None` and `State.set_meta(key, value) -> None`
    - Module constants `SCHEMA`, `SCHEMA_VERSION`.
  - **`recordings.init`:**
    - `class InitError(RuntimeError)`
    - `WRITER_ID_PATTERN = r"[a-z0-9][a-z0-9_.-]{0,62}"`
    - `init_archive(archive_root: Path, state_path: Path, *, writer_id: str, archive_uuid: str | None = None, created_at: datetime | None = None) -> ArchiveIdentity`
  - **`recordings.config.Config`** gains `writer_id: str | None`, `state_path: Path | None` and `index_path: Path | None`, and loses `writer_host`. `RECORDINGS_STATE` and `RECORDINGS_INDEX` override the file.
  - **CLI:** `recordings init [--json]`.
  - **Test fixture** in `packages/core/tests/conftest.py`: `new_archive` → `(root: Path, state: Path)`.

- [ ] **Step 1: Write the failing tests for durable writes**

`packages/core/tests/test_files.py`:
```python
import os

import pytest

from recordings import files


def test_write_bytes_atomic_replaces_and_syncs_the_file_and_its_folder(tmp_path, monkeypatch):
    synced = []
    real_fsync = os.fsync
    monkeypatch.setattr(files.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])
    target = tmp_path / "recording.json"
    target.write_text("old\n", encoding="utf-8")
    files.write_bytes_atomic(target, b"new\n")
    assert target.read_bytes() == b"new\n"
    assert len(synced) == 2  # the temporary file, then the folder after the rename
    assert sorted(p.name for p in tmp_path.iterdir()) == ["recording.json"]


def test_write_text_atomic_leaves_no_temp_file_when_the_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "recording.json"
    target.write_text("old\n", encoding="utf-8")

    def failing_replace(src, dst):
        raise OSError("Simulated replace failure")

    monkeypatch.setattr(files.os, "replace", failing_replace)
    with pytest.raises(OSError, match="Simulated replace failure"):
        files.write_text_atomic(target, "new\n")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["recording.json"]
    assert target.read_text(encoding="utf-8") == "old\n"


def test_create_exclusive_never_replaces_a_file(tmp_path):
    path = tmp_path / "archive.json"
    files.create_exclusive(path, b"one")
    with pytest.raises(FileExistsError):
        files.create_exclusive(path, b"two")
    assert path.read_bytes() == b"one"
    assert [p.name for p in tmp_path.iterdir()] == ["archive.json"]


def test_publish_exclusive_takes_the_next_free_name(tmp_path):
    first = files.publish_exclusive(b"a", tmp_path / "x.json")
    second = files.publish_exclusive(b"b", tmp_path / "x.json")
    assert (first.name, second.name) == ("x.json", "x-2.json")
    assert first.read_bytes() == b"a" and second.read_bytes() == b"b"


def test_publish_exclusive_survives_a_race_for_the_name(tmp_path, monkeypatch):
    # why: another writer can take the name between unique_path's check and the link.
    taken = tmp_path / "x.json"
    taken.write_bytes(b"theirs")
    real_unique = files.unique_path
    calls = []

    def racing_unique(path):
        calls.append(path)
        return taken if len(calls) == 1 else real_unique(path)

    monkeypatch.setattr(files, "unique_path", racing_unique)
    published = files.publish_exclusive(b"ours", tmp_path / "x.json")
    assert taken.read_bytes() == b"theirs"
    assert published.name == "x-2.json" and published.read_bytes() == b"ours"
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_copy_file_synced_copies_bytes_and_mtime(tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"ID3 audio")
    os.utime(src, (1_700_000_000, 1_700_000_000))
    dst = tmp_path / "b.mp3"
    files.copy_file_synced(src, dst)
    assert dst.read_bytes() == b"ID3 audio"
    assert dst.stat().st_mtime == 1_700_000_000
    with pytest.raises(FileExistsError):
        files.copy_file_synced(src, dst)  # never over an existing file
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_files.py -v`
Expected: FAIL with `ImportError: cannot import name 'files' from 'recordings'`.

- [ ] **Step 3: Write `files.py` and move the archive's writes onto it**

`packages/core/src/recordings/files.py`:
```python
"""Durable file writes (spec §6.4).

Every save writes a temporary file, fsyncs it, renames it into place, then fsyncs the folder.
After a crash the folder holds the old file or the new one, never a torn one, and a write that
returned survives a power cut. Write-once files are published with an exclusive hard link, so
nothing can overwrite them, not even a second writer racing for the same name.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import uuid
from pathlib import Path

_CHUNK = 1 << 20


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def temp_name(path: Path) -> Path:
    """A hidden sibling, `.name.<hex>.tmp`, which readers, backups and the mirror skip."""
    return path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")


def _write_new_synced(path: Path, data: bytes) -> None:
    with open(path, "xb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def write_bytes_atomic(path: Path, data: bytes) -> None:
    tmp = temp_name(path)
    try:
        _write_new_synced(tmp, data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    fsync_dir(path.parent)


def write_text_atomic(path: Path, text: str) -> None:
    write_bytes_atomic(path, text.encode("utf-8"))


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while (candidate := path.with_name(f"{path.stem}-{n}{path.suffix}")).exists():
        n += 1
    return candidate


def create_exclusive(path: Path, data: bytes) -> None:
    """Write `data` at `path`, or raise FileExistsError: never replace what is there."""
    tmp = temp_name(path)
    try:
        _write_new_synced(tmp, data)
        os.link(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
    fsync_dir(path.parent)


def publish_exclusive(data: bytes, target: Path) -> Path:
    """Write-once: publish at `target`, or at `target-2`, `-3` … when the name is taken."""
    while True:
        path = unique_path(target)
        try:
            create_exclusive(path, data)
        except FileExistsError:
            continue  # another writer took the name between the check and the link
        return path


def copy_file_synced(src: Path, dst: Path) -> None:
    """Copy bytes and modification time, then fsync, so the copy survives a power cut."""
    with open(src, "rb") as fin, open(dst, "xb") as fout:
        shutil.copyfileobj(fin, fout, _CHUNK)
        fout.flush()
        os.fsync(fout.fileno())
    shutil.copystat(src, dst)
```

In `packages/core/src/recordings/archive.py`:
1. **Delete** the functions `write_text_atomic`, `_publish_bytes_exclusive` and `_unique`, and the
   now-unused `import contextlib`.
2. **Add** after the `recordings.ids` import:
   ```python
   from recordings.files import copy_file_synced, fsync_dir, publish_exclusive, write_text_atomic
   ```
3. In `add_recording`, **replace** `shutil.copy2(media, tmp / media_name)` with
   `copy_file_synced(media, tmp / media_name)`, and **replace** the line
   `os.replace(tmp, final)` with:
   ```python
               os.replace(tmp, final)
               fsync_dir(final.parent)
   ```
4. In `_write_raw`, **replace** `path = _publish_bytes_exclusive(source.payload, target)` with
   `path = publish_exclusive(source.payload, target)`.
5. In `_write_rendition_into`, **replace**
   `path = _publish_bytes_exclusive(dump_json(rendition).encode("utf-8"), target)` with
   `path = publish_exclusive(dump_json(rendition).encode("utf-8"), target)`.

In `packages/core/src/recordings/selfdoc.py`, **replace**
`from recordings.archive import Archive, write_text_atomic` with:
```python
from recordings.archive import Archive
from recordings.files import write_text_atomic
```

In `packages/core/tests/test_archive.py`:
1. **Delete** `test_write_rendition_race_preserves_existing_file`,
   `test_write_raw_race_preserves_existing_payload` and
   `test_write_text_atomic_leaves_no_temp_file_when_the_replace_fails`. `test_files.py` now covers
   all three behaviours, against `files` itself.
2. In `test_add_recording_cleans_up_on_failure`, **replace** the copy-failure setup (from
   `original_copy2 = shutil.copy2` through the `monkeypatch.setattr(...)` line) with:
   ```python
       def failing_copy(*args, **kwargs):
           raise OSError("Simulated copy failure")

       monkeypatch.setattr(archive_module, "copy_file_synced", failing_copy)
   ```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_files.py packages/core/tests/test_archive.py -v`
Expected: all pass (6 in `test_files.py`).

- [ ] **Step 5: Write the failing tests for the sentinel, state and `init`**

`packages/core/tests/conftest.py`:
```python
"""Shared fixtures for the core tests. Every archive here is synthetic and lives in tmp_path."""

import pytest

from recordings.init import init_archive


@pytest.fixture
def new_archive(tmp_path):
    """An initialised, empty archive and its state folder: (root, state_path)."""
    root, state = tmp_path / "archive", tmp_path / "state"
    init_archive(root, state, writer_id="test")
    return root, state
```

`packages/core/tests/test_init.py`:
```python
import json
from datetime import datetime, timezone

import pytest

from recordings.cli import main
from recordings.init import InitError, init_archive
from recordings.selfdoc import validate
from recordings.sentinel import SENTINEL, ArchiveIdentity, SentinelError, read_sentinel, write_sentinel
from recordings.state import State, StateError

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
UUID = "5f0c8e2a-3b1d-4c6e-9a7f-1d2e3f405162"


def test_init_sets_up_an_empty_archive_and_its_state(tmp_path):
    root, state = tmp_path / "archive", tmp_path / "state"
    identity = init_archive(root, state, writer_id="homelab", archive_uuid=UUID, created_at=T0)
    assert identity == ArchiveIdentity(uuid=UUID, created_at=T0)
    assert read_sentinel(root) == identity
    assert (root / "recordings").is_dir() and (root / "FORMAT.md").is_file()
    st = State.open(state)
    assert st.meta("archive_uuid") == UUID and st.meta("writer_id") == "homelab"
    assert st.locks_dir.is_dir()
    assert validate(root) == []


def test_init_refuses_a_folder_that_is_not_empty(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    (root / "something.txt").write_text("x", encoding="utf-8")
    with pytest.raises(InitError, match="not empty"):
        init_archive(root, tmp_path / "state", writer_id="homelab")
    assert not (root / SENTINEL).exists()


def test_init_runs_once_per_state_folder(new_archive, tmp_path):
    _, state = new_archive
    with pytest.raises(InitError, match="already"):
        init_archive(tmp_path / "other", state, writer_id="test")


@pytest.mark.parametrize("bad", ["", "Homelab", "my homelab", "-x", "a" * 64])
def test_init_refuses_a_bad_writer_id(tmp_path, bad):
    with pytest.raises(InitError, match="writer_id"):
        init_archive(tmp_path / "archive", tmp_path / "state", writer_id=bad)


def test_the_sentinel_is_never_replaced(new_archive):
    root, _ = new_archive
    before = (root / SENTINEL).read_bytes()
    with pytest.raises(FileExistsError):
        write_sentinel(root, ArchiveIdentity(uuid=UUID, created_at=T0))
    assert (root / SENTINEL).read_bytes() == before


@pytest.mark.parametrize("text", [
    "{",
    "[]",
    json.dumps({"format": "something-else@1", "uuid": UUID, "created_at": T0.isoformat()}),
    json.dumps({"format": "recordings-archive@1", "uuid": "nope", "created_at": T0.isoformat()}),
    json.dumps({"format": "recordings-archive@1", "uuid": 7, "created_at": T0.isoformat()}),
    json.dumps({"format": "recordings-archive@1", "uuid": UUID, "created_at": "2026-10-08T12:00:00"}),
], ids=["truncated", "not-an-object", "wrong-format", "bad-uuid", "uuid-not-text", "naive-time"])
def test_a_damaged_sentinel_is_refused(tmp_path, text):
    (tmp_path / SENTINEL).write_text(text, encoding="utf-8")
    with pytest.raises(SentinelError):
        read_sentinel(tmp_path)


def test_a_missing_sentinel_asks_whether_the_disk_is_mounted(tmp_path):
    with pytest.raises(SentinelError, match="mounted"):
        read_sentinel(tmp_path)


def test_a_machine_without_state_is_not_the_writer(tmp_path):
    with pytest.raises(StateError, match="not the archive's writer"):
        State.open(tmp_path / "state")


def _config(tmp_path, monkeypatch, *, writer_id="homelab"):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{tmp_path / "archive"}"\nwriter_id = "{writer_id}"\n'
        f'[state]\npath = "{tmp_path / "state"}"\n', encoding="utf-8")
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX"):
        monkeypatch.delenv(name, raising=False)


def test_cli_init_uses_the_config_and_runs_once(tmp_path, monkeypatch, capsys):
    _config(tmp_path, monkeypatch)
    assert main(["init", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["uuid"] == read_sentinel(tmp_path / "archive").uuid
    assert out["writer_id"] == "homelab"
    assert main(["init", "--json"]) == 1
    assert "not empty" in json.loads(capsys.readouterr().out)["error"]


def test_cli_init_says_what_is_missing(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[archive]\npath = "{tmp_path / "archive"}"\n', encoding="utf-8")
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    monkeypatch.delenv("RECORDINGS_STATE", raising=False)
    assert main(["init", "--json"]) == 78
    error = json.loads(capsys.readouterr().out)["error"]
    assert "[state] path" in error and "[archive] writer_id" in error


def test_docs_refuses_a_folder_without_a_sentinel(tmp_path, capsys):
    # why: §6.7. Writing the docs into an unmounted, empty folder would make it look like an archive.
    assert main(["docs", str(tmp_path), "--json"]) == 1
    assert "mounted" in json.loads(capsys.readouterr().out)["error"]
    assert list(tmp_path.iterdir()) == []


def test_validate_reports_a_missing_sentinel(tmp_path):
    (tmp_path / "recordings").mkdir()
    (problem,) = validate(tmp_path)
    assert problem["path"] == str(tmp_path / SENTINEL)
```

Add to `packages/core/tests/test_config.py`:
```python
def test_writer_id_and_the_state_and_index_paths(tmp_path):
    cfg_file = write(tmp_path, '[archive]\nwriter_id = "homelab"\n[state]\npath = "/s"\n'
                               '[index]\npath = "/i/index.db"\n')
    cfg = load_config({"RECORDINGS_CONFIG": str(cfg_file)})
    assert (cfg.writer_id, cfg.state_path, cfg.index_path) == ("homelab", Path("/s"), Path("/i/index.db"))


def test_the_environment_overrides_the_state_and_index_paths(tmp_path):
    cfg_file = write(tmp_path, '[state]\npath = "/s"\n[index]\npath = "/i/index.db"\n')
    cfg = load_config({"RECORDINGS_CONFIG": str(cfg_file), "RECORDINGS_STATE": "/state",
                       "RECORDINGS_INDEX": "/index/index.db"})
    assert (cfg.state_path, cfg.index_path) == (Path("/state"), Path("/index/index.db"))


def test_the_old_writer_host_key_fails_loudly(tmp_path):
    # why: it named a host, and §3 says a writer's identity never comes from a host name.
    cfg_file = write(tmp_path, '[archive]\nwriter_host = "my-homelab"\n')
    with pytest.raises(ConfigError, match="renamed writer_id"):
        load_config({"RECORDINGS_CONFIG": str(cfg_file)})


@pytest.mark.parametrize("value", ['"My Homelab"', '"-x"', "7"])
def test_a_bad_writer_id_is_refused(tmp_path, value):
    cfg_file = write(tmp_path, f"[archive]\nwriter_id = {value}\n")
    with pytest.raises(ConfigError, match="writer_id"):
        load_config({"RECORDINGS_CONFIG": str(cfg_file)})


@pytest.mark.parametrize("value", ["7", '"my-homelab:8000"', '"ftp://x"'])
def test_base_url_must_be_an_http_url(tmp_path, value):
    cfg_file = write(tmp_path, f"[server]\nbase_url = {value}\n")
    with pytest.raises(ConfigError, match="base_url"):
        load_config({"RECORDINGS_CONFIG": str(cfg_file)})
```

- [ ] **Step 6: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_init.py packages/core/tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings.init'`. The new
`test_config.py` tests fail on `Config` having no `writer_id`.

- [ ] **Step 7: Write the sentinel, state, `init`, the config changes and the CLI**

`packages/core/src/recordings/sentinel.py`:
```python
"""The archive sentinel (spec §6.7): `archive.json` at the archive root.

An archive is never set up implicitly. Without this file, a writer started against an unmounted,
empty folder would make a new archive there, and the mirror's `rsync --delete` would then empty the
NAS copy. So `recordings init` writes it once, with the archive's UUID, and every writer, the
backups and the mirror refuse to run when it is missing or its UUID has changed.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from recordings.files import create_exclusive
from recordings.models import FORMAT

SENTINEL = "archive.json"


class SentinelError(RuntimeError):
    """The folder is not an initialised archive, or not the one expected."""


@dataclass(frozen=True)
class ArchiveIdentity:
    uuid: str
    created_at: datetime


def read_sentinel(root: Path) -> ArchiveIdentity:
    path = Path(root) / SENTINEL
    if not path.is_file():
        raise SentinelError(
            f"{root} has no {SENTINEL}, so it is not an initialised archive. Is the archive's "
            "disk mounted? A new archive is set up once, with `recordings init`.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        if data.get("format") != FORMAT:
            raise ValueError(f"format is {data.get('format')!r}, not {FORMAT!r}")
        identity = ArchiveIdentity(uuid=str(uuid.UUID(data["uuid"])),
                                   created_at=datetime.fromisoformat(data["created_at"]))
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SentinelError(f"{path} is not a valid sentinel: {exc}") from None
    if identity.created_at.utcoffset() is None:
        raise SentinelError(f"{path}: created_at has no UTC offset")
    return identity


def write_sentinel(root: Path, identity: ArchiveIdentity) -> Path:
    path = Path(root) / SENTINEL
    text = json.dumps({"format": FORMAT, "uuid": identity.uuid,
                       "created_at": identity.created_at.isoformat()}, indent=2) + "\n"
    create_exclusive(path, text.encode("utf-8"))
    return path
```

`packages/core/src/recordings/state.py`:
```python
"""Operational state that can't be derived (spec §6.8): `state.db`, and the lock files beside it.

It lives in `[state] path` (`/srv/recordings/state`), outside the archive, so the archive's backups
and the mirror never copy it. The backup copies `state.db` on its own, with SQLite's backup
command (§15.1). Reads never need it: the Mac's read-only mirror has none.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

SCHEMA_VERSION = 1
# Later tasks append their tables here. Every statement is CREATE ... IF NOT EXISTS, so opening an
# older state.db adds what is missing and touches nothing else.
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class StateError(RuntimeError):
    pass


class State:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @property
    def db_path(self) -> Path:
        return self.path / "state.db"

    @property
    def locks_dir(self) -> Path:
        return self.path / "locks"

    @classmethod
    def create(cls, path: Path, *, archive_uuid: str, writer_id: str) -> State:
        state = cls(path)
        if state.db_path.exists():
            raise StateError(
                f"{state.db_path} already exists: `recordings init` runs once per archive")
        state.path.mkdir(parents=True, exist_ok=True)
        state.locks_dir.mkdir(exist_ok=True)
        with state.db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", [
                ("schema_version", str(SCHEMA_VERSION)),
                ("archive_uuid", archive_uuid),
                ("writer_id", writer_id),
            ])
        return state

    @classmethod
    def open(cls, path: Path) -> State:
        state = cls(path)
        if not state.db_path.is_file():
            raise StateError(
                f"no state.db in {state.path}, so this machine is not the archive's writer. "
                "`recordings init` creates it, once, on the writer.")
        with state.db() as db:
            db.executescript(SCHEMA)
        state.locks_dir.mkdir(exist_ok=True)
        return state

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        """One connection per use: committed on success, rolled back on error, always closed."""
        with closing(sqlite3.connect(self.db_path, timeout=30)) as conn:
            conn.execute("PRAGMA synchronous=FULL")
            with conn:
                yield conn

    def meta(self, key: str) -> str | None:
        with self.db() as db:
            row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.db() as db:
            db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))
```

`packages/core/src/recordings/init.py`:
```python
"""`recordings init`: set up a new archive, explicitly and once (spec §6.7).

It refuses any folder that isn't empty, and any state folder that already has a state.db, so it can
never adopt or overwrite something by accident. The sentinel is written last: an interrupted init
leaves no archive.json, and every writer refuses to run against it.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from recordings.selfdoc import write_docs
from recordings.sentinel import ArchiveIdentity, write_sentinel
from recordings.state import State

WRITER_ID_PATTERN = r"[a-z0-9][a-z0-9_.-]{0,62}"


class InitError(RuntimeError):
    pass


def init_archive(archive_root: Path, state_path: Path, *, writer_id: str,
                 archive_uuid: str | None = None, created_at: datetime | None = None,
                 ) -> ArchiveIdentity:
    root, state_dir = Path(archive_root), Path(state_path)
    if not re.fullmatch(WRITER_ID_PATTERN, writer_id or ""):
        raise InitError(f"writer_id {writer_id!r} must be a short lowercase name, such as homelab")
    if root.exists() and not root.is_dir():
        raise InitError(f"{root} is not a folder")
    if root.is_dir() and any(root.iterdir()):
        raise InitError(f"{root} is not empty: init only sets up a new, empty archive")
    if (state_dir / "state.db").exists():
        raise InitError(f"{state_dir / 'state.db'} already exists: init runs once per archive")
    identity = ArchiveIdentity(
        uuid=str(uuid.UUID(archive_uuid)) if archive_uuid else str(uuid.uuid4()),
        created_at=created_at or datetime.now(timezone.utc))
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / "recordings").mkdir()
        write_docs(root)
        State.create(state_dir, archive_uuid=identity.uuid, writer_id=writer_id)
        write_sentinel(root, identity)
    except OSError as exc:
        raise InitError(
            f"init failed ({exc}). Remove {root} and {state_dir / 'state.db'}, then run it "
            "again.") from None
    return identity
```

In `packages/core/src/recordings/config.py`:
1. **Add** `import re` to the imports, and after `DEFAULT_PATH`:
   ```python
   WRITER_ID_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,62}")
   ```
2. **Replace** the `Config` dataclass with:
   ```python
   @dataclass(frozen=True)
   class Config:
       path: Path | None  # the file actually read, or None
       archive_path: Path | None
       writer_id: str | None  # the archive's one writer (§3); `recordings init` records it
       default_timezone: str
       base_url: str | None
       data: dict[str, Any] = field(default_factory=dict)  # every section, for later stages
       # Names the app answers to besides localhost and base_url's host (recordings_ui.hosts).
       allowed_hosts: tuple[str, ...] = ()
       state_path: Path | None = None  # [state] path: state.db and the locks (§6.8)
       index_path: Path | None = None  # [index] path: the derived index.db
   ```
3. **Add** before `load_config`:
   ```python
   def _writer_id(archive: dict[str, Any]) -> str | None:
       if "writer_host" in archive:
           raise ConfigError(
               "[archive] writer_host was renamed writer_id in stage 2a. It names the archive's "
               'one writer and is not a host name: set writer_id = "homelab" (any short name).')
       value = archive.get("writer_id")
       if value is None:
           return None
       if not isinstance(value, str) or not WRITER_ID_RE.fullmatch(value):
           raise ConfigError(
               '[archive] writer_id must be a short lowercase name such as "homelab": letters, '
               "digits, '.', '_' or '-'")
       return value


   def _base_url(server: dict[str, Any]) -> str | None:
       value = server.get("base_url")
       if value is None:
           return None
       if not isinstance(value, str) or not value.startswith(("http://", "https://")):
           raise ConfigError('[server] base_url must be a URL, such as "http://my-homelab:8000"')
       return value


   def _path(environ: Mapping[str, str], env: str, section: dict[str, Any]) -> Path | None:
       value = environ.get(env) or section.get("path")
       return Path(value).expanduser() if value else None
   ```
4. **Replace** the `return Config(...)` at the end of `load_config` with:
   ```python
       return Config(
           path=path if path.is_file() else None,
           archive_path=Path(archive_path).expanduser() if archive_path else None,
           writer_id=_writer_id(archive),
           default_timezone=archive.get("default_timezone", "America/Vancouver"),
           base_url=_base_url(server),
           data=data,
           allowed_hosts=_allowed_hosts(server),
           state_path=_path(environ, "RECORDINGS_STATE", data.get("state", {})),
           index_path=_path(environ, "RECORDINGS_INDEX", data.get("index", {})),
       )
   ```

In `packages/core/src/recordings/cli.py`:
1. **Replace** the import line with:
   ```python
   from recordings import __version__, config, schemas, selfdoc
   from recordings.init import InitError, init_archive
   from recordings.sentinel import SentinelError, read_sentinel
   from recordings.state import StateError
   ```
2. **Add** to `build_parser`, before the `schemas` parser:
   ```python
       p = sub.add_parser("init", help="set up a new, empty archive, once: its sentinel and state.db")
       p.add_argument("--json", action="store_true")
   ```
3. **Add** after `_emit`:
   ```python
   def _fail(message: str, as_json: bool, code: int) -> int:
       if as_json:
           print(json.dumps({"error": message}, ensure_ascii=False))
       else:
           print(f"recordings: {message}", file=sys.stderr)
       return code


   def _config(as_json: bool) -> config.Config | int:
       try:
           return config.load_config(os.environ)
       except config.ConfigError as exc:
           return _fail(str(exc), as_json, 78)


   def cmd_init(args: argparse.Namespace) -> int:
       cfg = _config(args.json)
       if isinstance(cfg, int):
           return cfg
       missing = [name for name, value in (("[archive] path", cfg.archive_path),
                                           ("[state] path", cfg.state_path),
                                           ("[archive] writer_id", cfg.writer_id)) if value is None]
       if missing:
           return _fail("set " + ", ".join(missing) + " in config.toml first", args.json, 78)
       try:
           identity = init_archive(cfg.archive_path, cfg.state_path, writer_id=cfg.writer_id)
       except (InitError, StateError) as exc:
           return _fail(str(exc), args.json, 1)
       _emit({"archive": str(cfg.archive_path), "uuid": identity.uuid,
              "writer_id": cfg.writer_id}, args.json)
       return 0
   ```
4. **Replace** `cmd_docs` with:
   ```python
   def cmd_docs(args: argparse.Namespace) -> int:
       try:
           read_sentinel(args.archive)  # §6.7: docs go only into an archive with its sentinel
       except SentinelError as exc:
           return _fail(str(exc), args.json, 1)
       _emit({"written": selfdoc.write_docs(args.archive)}, args.json)
       return 0
   ```
5. **Add** `"init": cmd_init,` to the `commands` dict in `main`.

In `packages/core/src/recordings/selfdoc.py`, **add** the import
`from recordings.sentinel import SENTINEL, SentinelError, read_sentinel`. In `validate`, **replace**
the two lines `archive = Archive(root)` and `problems = []` with:
```python
    problems = []
    try:
        read_sentinel(root)
    except SentinelError as exc:
        problems.append({"path": str(root / SENTINEL), "message": str(exc)})
    archive = Archive(root)
```

In `config.example.toml`, **replace** the `[archive]` section's `writer_host` lines and the
`[index]` section with:
```toml
# The archive's one writer (spec §3): a short name for the homelab server. It is never read from a
# host name, because a container's host name isn't the server's. `recordings init` records it in
# state.db, and only a machine whose config and state.db both name it may write.
writer_id = "homelab"
```
and, after `[server]`:
```toml
[state]                                 # stage 2a: state.db and the lock files (spec §6.8)
# Outside the archive, on the same local disk. In Docker it is /state, and docker/compose.yml sets
# RECORDINGS_STATE=/state, which overrides this value.
path = "/srv/recordings/state"

[index]                                 # stage 2a: the derived index, rebuilt by `recordings reindex`
# In Docker it lives on a named volume at /index, and RECORDINGS_INDEX overrides this value.
path = "/srv/recordings/index/index.db"
```

- [ ] **Step 8: Bring the existing tests, the docs and the demo build into line**

In `packages/core/tests/test_cli.py`, **replace both tests** with:
```python
import json

from recordings.cli import main
from recordings.init import init_archive


def test_docs_then_validate_on_a_new_archive(tmp_path, capsys):
    init_archive(tmp_path / "a", tmp_path / "state", writer_id="test")
    (tmp_path / "a" / "FORMAT.md").unlink()
    assert main(["docs", str(tmp_path / "a"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["written"] == ["FORMAT.md"]
    assert main(["validate", str(tmp_path / "a"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"problems": []}


def test_validate_exits_1_on_problems(tmp_path, capsys):
    init_archive(tmp_path / "a", tmp_path / "state", writer_id="test")
    folder = tmp_path / "a" / "recordings" / "2026" / "10" / "20261006T140003-0700_3fa91c2e"
    folder.mkdir(parents=True)
    (folder / "recording.json").write_text("{", encoding="utf-8")
    assert main(["validate", str(tmp_path / "a"), "--json"]) == 1
    assert len(json.loads(capsys.readouterr().out)["problems"]) == 1
```

In `packages/core/tests/test_selfdoc.py`, in `build_one`, **replace**
`archive = Archive(tmp_path / "archive")` with:
```python
    init_archive(tmp_path / "archive", tmp_path / "state", writer_id="test")
    archive = Archive(tmp_path / "archive")
```
and **add** `from recordings.init import init_archive` to its imports.

In `packages/core/src/recordings/format/FORMAT.md`, **replace** the line
`  README.md  AGENTS.md  FORMAT.md` in the top code block with:
```
  archive.json                   the sentinel: the archive's UUID, written once by `recordings init`
  README.md  AGENTS.md  FORMAT.md
```
and **add** to `## Rules`, after the "Work in progress" bullet:
```markdown
- **The sentinel.** `archive.json` holds the archive's `format` and UUID, and is written once,
  by `recordings init`. Every writer, the backups and the mirror refuse to run without it, or when
  its UUID has changed, so an unmounted, empty folder can never pass for the archive. Never edit
  or copy it into another archive.
```

In `packages/core/src/recordings/format/AGENTS.md`, **add** after rule 3:
```markdown
4. **Never edit, create or delete `archive.json`.** It is the archive's sentinel (see `FORMAT.md`).
```
and renumber the following rules 5–7.

In `demo/build.py`:
1. **Add** the imports `import tempfile` and `from recordings.init import init_archive`, and
   **delete** `from recordings.selfdoc import write_docs`.
2. **Add** below `BUILD_AT`:
   ```python
   # A fixed UUID, so a rebuild is byte-identical. Every copy of the demo shares it, harmlessly: each
   # demo run gets a fresh temporary state folder.
   DEMO_UUID = "5f0c8e2a-3b1d-4c6e-9a7f-1d2e3f405162"
   ```
3. **Replace** the first two lines of `build`'s body
   (`entries = …` and `archive = Archive(out)`) with:
   ```python
       entries = tomllib.loads((HERE / "sources.toml").read_text())["recording"]
       with tempfile.TemporaryDirectory(prefix="recordings-demo-state-") as state:
           init_archive(out, Path(state) / "state", writer_id="demo", archive_uuid=DEMO_UUID,
                        created_at=BUILD_AT)
       archive = Archive(out)
   ```
4. **Delete** the line `write_docs(archive.root)` near the end of `build`: `init_archive` writes
   the docs.

- [ ] **Step 9: Rebuild the demo archive and run every test**

Run: `uv run python demo/build.py --media-dir demo/.cache/media --out demo/archive --force && uv run pytest`
Expected: `built 4 recordings into demo/archive`, then every test passes. `git status demo/archive`
shows `archive.json` added, and the docs changed.

If `demo/.cache/media` is missing, refill it from the committed archive first. It is the same
media, so the rebuild stays byte-identical:
```bash
mkdir -p demo/.cache/media && uv run python -c "import json,pathlib,shutil,tomllib; from recordings.archive import Archive; a=Archive(pathlib.Path('demo/archive')); ext={e['slug']:e['ext'] for e in tomllib.loads(pathlib.Path('demo/sources.toml').read_text())['recording']}; [shutil.copy(a.media_path(r), f'demo/.cache/media/{s}.{ext[s]}') for r,s in json.load(open('demo/canned/aliases.json')).items()]"
```

- [ ] **Step 10: Commit**

```bash
git add packages/core/src/recordings packages/core/tests config.example.toml demo/build.py demo/archive
git commit -m "feat(core): durable writes, the archive sentinel, state.db and recordings init

fsync the file and its folder on every write (stage-1 carry-over). Checked: docs.python.org
sqlite3 (connection handling, PRAGMA journal_mode) and os.fsync/os.link.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 3: The format change: `rev`, the `speakers` shape and Plaud's segment fields

**Checkpoint lens:** data integrity and write safety.

The real archive is still empty, so the format changes now, inside `recordings-archive@1`, with
every new key optional (§22, decision 12). No dual reader is needed.

**Files:**
- Modify: `packages/core/src/recordings/models.py` (whole file below), `archive.py` (`RawSource`, `_write_raw`), `selfdoc.py` (`validate`)
- Modify: `packages/core/src/recordings/format/FORMAT.md`, `format/AGENTS.md`, `format/schemas/*.json` (regenerated)
- Modify: `packages/ui/src/recordings_ui/views.py` (`_turns`)
- Modify: `demo/archive/` (rebuilt)
- Test: `packages/core/tests/test_models.py`, `test_selfdoc.py`, `test_archive.py`; `packages/ui/tests/test_views.py`

**Interfaces:**
- Consumes: `recordings.files.publish_exclusive`, and `recordings.sentinel` (Task 2).
- Produces:
  - **New constants** in `recordings.models`: `SHA256_PATTERN`, `PERSON_SLUG`, `UNKNOWN_PERSON = "unknown"`, and the type `PersonSlug`.
  - **New models:** `PlaudState(title_seen: str | None, acknowledged_up_to: str | None)`, `SpeakerLabel(person, by, plaud_name_seen, not_)` (alias `not`), `SpeakerSpan(start, end, person, by)` and `Speakers(source, labels, spans)`.
  - **`Recording`** gains `rev: int | None`, `title_by: Literal["plaud", "you"] | None` and `plaud: PlaudState | None`. Its `speakers` becomes a `Speakers`.
  - **`SourceRef`** gains `fetched_at: AwareDatetime | None` and `sha256: str | None`.
  - **`MediaInfo`** gains `sample_rate: int | None` and `channels: int | None`.
  - **`Segment`** gains `speaker_name: str | None` and `embedding_key: str | None`.
  - **`recordings.archive.RawSource`** gains `fetched_at: datetime | None = None`, and rejects naive datetimes.

- [ ] **Step 1: Write the failing tests**

Add to `packages/core/tests/test_models.py` (after the existing imports, add
`from recordings.models import UNKNOWN_PERSON, Segment, SourceRef, Speakers, SpeakerSpan`):
```python
def test_empty_speakers_still_serialise_as_an_empty_object():
    # why: §22 decision 12. Every existing file has "speakers": {}, and must keep it on a rewrite.
    data = json.loads(dump_json(recording()))
    assert data["speakers"] == {}
    assert "rev" not in data and "title_by" not in data and "plaud" not in data


def test_the_speakers_shape_round_trips_with_not_spelled_not():
    rec = recording(speakers={
        "source": "renditions/transcript-plaud-plaud@1-20261008T143000Z.json",
        "labels": {"Speaker 1": {"person": "ada-lovelace", "by": "you"},
                   "Speaker 2": {"person": "grace-hopper", "by": "you", "plaud_name_seen": "Ada"},
                   "Speaker 3": {"not": ["ada-lovelace"]}},
        "spans": [{"start": 41.2, "end": 58.0, "person": "grace-hopper", "by": "you"},
                  {"start": 300.0, "end": 312.5, "person": UNKNOWN_PERSON, "by": "you"}]})
    data = json.loads(dump_json(rec))
    assert data["speakers"]["labels"]["Speaker 3"] == {"not": ["ada-lovelace"]}
    assert data["speakers"]["spans"][1]["person"] == "unknown"
    assert Recording.model_validate_json(dump_json(rec)) == rec


@pytest.mark.parametrize("speakers, message", [
    ({"labels": {"Speaker 1": {"person": "Ada Lovelace", "by": "you"}}}, "pattern"),
    ({"labels": {"Speaker 1": {"person": "ada"}}}, "person and by go together"),
    ({"labels": {"Speaker 1": {"by": "you"}}}, "person and by go together"),
    ({"labels": {"Speaker 1": {"person": "ada", "by": "auto"}}}, "you"),
    ({"spans": [{"start": 5.0, "end": 5.0, "person": "ada"}]}, "end after it starts"),
    ({"spans": [{"start": 1.0, "end": 2.0, "person": "ada", "from": "voice"}]}, "Extra inputs"),
])
def test_bad_speaker_decisions_are_rejected(speakers, message):
    with pytest.raises(ValidationError, match=message):
        recording(speakers=speakers)


def test_rev_title_by_and_plaud_are_optional_and_validated():
    rec = recording(rev=7, title_by="plaud",
                    plaud={"title_seen": "Week 4", "acknowledged_up_to": "source/plaud-x.json"})
    assert Recording.model_validate_json(dump_json(rec)) == rec
    with pytest.raises(ValidationError):
        recording(rev=-1)
    with pytest.raises(ValidationError):
        recording(title_by="auto")


def test_plaud_segment_fields_and_snapshot_fields():
    seg = Segment(start=0, end=1.5, speaker="Speaker 1", speaker_name="Ada",
                  embedding_key="ek-1", text="Hello.")
    assert seg.model_dump(exclude_none=True)["speaker_name"] == "Ada"
    ref = SourceRef(kind="plaud", ref="a" * 32, added_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
                    fetched_at=datetime(2026, 8, 1, tzinfo=timezone.utc), sha256="b" * 64)
    assert SourceRef.model_validate_json(ref.model_dump_json()) == ref
    with pytest.raises(ValidationError):
        SourceRef(kind="plaud", ref="x", added_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
                  sha256="not-a-hash")


def test_media_info_keeps_sample_rate_and_channels():
    rec = recording(media={"file": "x.mp3", "sha256": SHA, "kind": "audio", "duration_ms": 1000,
                           "sample_rate": 44100, "channels": 2})
    assert (rec.media.sample_rate, rec.media.channels) == (44100, 2)
    with pytest.raises(ValidationError):
        recording(media={"file": "x.mp3", "sha256": SHA, "kind": "audio", "channels": 0})


def test_speakers_default_is_empty():
    assert Speakers().labels == {} and Speakers().spans == [] and Speakers().source is None


def test_a_span_is_yours_by_default():
    span = SpeakerSpan(start=1, end=2, person="ada")
    assert span.by == "you"
```

Add to `packages/core/tests/test_archive.py`:
```python
def test_a_raw_source_needs_aware_times():
    # why: stage-1 carry-over. A naive time would be read as UTC somewhere and as local elsewhere.
    with pytest.raises(ValueError, match="timezone-aware"):
        RawSource(kind="plaud", ref="x", added_at=datetime(2026, 10, 8, 12, 0))
    with pytest.raises(ValueError, match="timezone-aware"):
        RawSource(kind="plaud", ref="x", added_at=T0, fetched_at=datetime(2026, 8, 1))


def test_a_raw_source_records_its_hash_and_fetch_time(tmp_path):
    archive = Archive(tmp_path / "archive")
    fetched = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    rec = add(archive, media(tmp_path), source=RawSource(
        kind="plaud", ref="of_" + "a" * 32, added_at=T0, fetched_at=fetched, payload=b'{"id": 1}'))
    (ref,) = rec.sources
    assert ref.raw == "source/plaud-20260801T090000Z.json"  # named by when it was fetched
    assert ref.fetched_at == fetched
    assert ref.sha256 == hashlib.sha256(b'{"id": 1}').hexdigest()
```
and add `import hashlib` to its imports.

Add to `packages/core/tests/test_selfdoc.py`:
```python
def test_validate_reports_a_missing_media_file_and_a_missing_source_file(tmp_path):
    # why: stage-1 carry-over. A recording whose media is gone must not validate clean.
    archive, rec = build_one(tmp_path)
    folder = archive.path_for(rec.id)
    (folder / rec.media.file).unlink()
    (folder / rec.sources[0].raw).unlink()
    messages = sorted(p["message"] for p in validate(archive.root))
    assert messages == [f"media.file {rec.media.file!r} is missing",
                        f"source file {rec.sources[0].raw} is missing"]


def test_validate_reports_a_span_past_the_end_of_the_media(tmp_path):
    import json

    archive, rec = build_one(tmp_path)
    path = archive.path_for(rec.id) / "recording.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["media"]["duration_ms"] = 10_000
    data["speakers"] = {"spans": [{"start": 8.0, "end": 12.0, "person": "ada", "by": "you"}]}
    path.write_text(json.dumps(data), encoding="utf-8")
    (problem,) = validate(archive.root)
    assert "runs past the media's end" in problem["message"]


def test_validate_checks_renditions_even_when_recording_json_is_broken(tmp_path):
    # why: stage-1 carry-over. A broken recording.json used to hide its folder's bad outputs.
    archive, rec = build_one(tmp_path)
    folder = archive.path_for(rec.id)
    (folder / "recording.json").write_text("{", encoding="utf-8")
    (folder / "renditions" / "broken.json").write_text("{", encoding="utf-8")
    paths = sorted(p["path"] for p in validate(archive.root))
    assert paths == sorted([str(folder / "recording.json"), str(folder / "renditions" / "broken.json")])


def test_the_docs_describe_rev_speakers_and_the_plaud_fields():
    from importlib import resources

    def read(doc):
        return " ".join((resources.files("recordings") / "format" / doc)
                        .read_text(encoding="utf-8").split())
    fmt = read("FORMAT.md")
    for needle in ("`rev`", "only `mutate`", "`unknown`", "`speaker_name`", "`embedding_key`",
                   "`fetched_at`", "`title_by`", "acknowledged_up_to"):
        assert needle in fmt, needle
    assert "Never change `rev`" in read("AGENTS.md")
```

Add to `packages/ui/tests/test_views.py`:
```python
def test_a_turn_shows_plauds_name_for_its_speaker(tmp_path):
    media = tmp_path / "x.mp3"
    media.write_bytes(b"ID3")
    archive = Archive(tmp_path / "archive")
    t = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rec = archive.add_recording(
        media=media, recorded_at=t, timezone_name="UTC", time_source="plaud", title="x",
        kind="audio", source=RawSource(kind="plaud", ref="x", added_at=t),
        renditions=[Rendition(kind="transcript", engine="plaud", model="plaud", version="plaud@1",
                              created_at=t, payload={"segments": [
                                  {"start": 0, "end": 1, "speaker": "Speaker 1",
                                   "speaker_name": "Ada", "text": "Hello."},
                                  {"start": 1, "end": 2, "speaker": "Speaker 2", "text": "Hi."}]})])
    turns = recording_view(archive, rec.id)["transcripts"][0]["turns"]
    assert [t["speaker"] for t in turns] == ["Ada", "Speaker 2"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_models.py packages/core/tests/test_archive.py packages/core/tests/test_selfdoc.py packages/ui/tests/test_views.py -v`
Expected: FAIL with `ImportError: cannot import name 'UNKNOWN_PERSON'` (`test_models.py`); the
other new tests fail on the missing fields and checks.

- [ ] **Step 3: Write the new models**

Replace `packages/core/src/recordings/models.py` with:
```python
"""The archive's data model (spec §6.3, §6.5, §7.6). JSON Schemas are generated from these.

Every key added in stage 2a is optional, so files written before it read unchanged, inside
`recordings-archive@1` (§22, decision 12).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from recordings.ids import ID_PATTERN, ID_RE

FORMAT = "recordings-archive@1"
RENDITION_SCHEMA = "recordings/rendition@1"
# recording.json sits at recordings/YYYY/MM/<id>/, four levels below the archive root.
SCHEMA_REF = "../../../../schemas/recording.schema.json"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
# A person's slug (§7.6): lowercase ASCII words joined by single hyphens.
PERSON_SLUG = r"^[a-z0-9]+(-[a-z0-9]+)*$"
# The reserved person (§7.6): "not anyone known". A span set to it overrides every inferred name
# for that time, which is how "Not Alex" works for one line or a range. No real person has it.
UNKNOWN_PERSON = "unknown"

TimeSource = Literal["plaud", "metadata", "published", "mtime", "ingest"]
PersonSlug = Annotated[str, Field(pattern=PERSON_SLUG)]


def _empty(value: object) -> bool:
    return not value


class _Model(BaseModel):
    # forbid: a typo in a hand-edited file is an error, not a silently ignored key (§11).
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class MediaInfo(_Model):
    file: str
    sha256: str = Field(pattern=SHA256_PATTERN)
    kind: Literal["audio", "video"]
    duration_ms: int | None = Field(default=None, ge=0)  # of the decoded media, not the source's
    sample_rate: int | None = Field(default=None, gt=0)  # captured at ingest (§9.1.1)
    channels: int | None = Field(default=None, gt=0)
    audio_file: str | None = None  # extracted audio track, video only


class SourceRef(_Model):
    kind: str  # "plaud", "recorder", "pocket", "audio_router", "url", "upload", "watched_folder"
    ref: str  # opaque, exactly as the source returned it (Plaud now sends "of_…")
    added_at: AwareDatetime
    # When the source returned this payload. Snapshots are ordered by it, then by their place in
    # `sources`, never by file name (§9.1.1). Absent means added_at.
    fetched_at: AwareDatetime | None = None
    raw: str | None = None  # e.g. "source/plaud-20261008T143000Z.json"
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)  # of the raw file's bytes


class TagRef(_Model):
    tag: str
    by: Literal["you", "auto"] = "you"

    @field_validator("tag")
    @classmethod
    def _folders_are_clean(cls, value: str) -> str:
        parts = value.split("/")
        if any(not p or p != p.strip() for p in parts):
            raise ValueError(f"tag {value!r} has an empty or space-padded part")
        return value


class Chosen(_Model):
    transcript: str | None = None


class PlaudState(_Model):
    """What Plaud last said, as far as Dan has seen it (§6.3, §6.8)."""

    title_seen: str | None = None  # the Plaud title last seen or dismissed
    acknowledged_up_to: str | None = None  # the newest snapshot whose removals Dan has reviewed


class SpeakerLabel(_Model):
    """Your decision about one diarization label (§7.6). Inferences never live in the file."""

    person: PersonSlug | None = None
    by: Literal["you"] | None = None
    plaud_name_seen: str | None = None  # the Plaud name you overrode, so a change can show
    not_: list[PersonSlug] = Field(default_factory=list, alias="not", exclude_if=_empty)

    @model_validator(mode="after")
    def _person_needs_by(self) -> SpeakerLabel:
        if (self.person is None) != (self.by is None):
            raise ValueError('a label\'s person and by go together: {"person": "…", "by": "you"}')
        return self


class SpeakerSpan(_Model):
    """A stretch of media time, in seconds, that is someone else, or `unknown` (§7.6)."""

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    person: PersonSlug
    by: Literal["you"] = "you"

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> SpeakerSpan:
        if self.end <= self.start:
            raise ValueError(f"a span must end after it starts ({self.start} to {self.end})")
        return self


class Speakers(_Model):
    """Speaker decisions (§7.6). `source` names the diarization the labels belong to."""

    source: str | None = None
    labels: dict[str, SpeakerLabel] = Field(default_factory=dict, exclude_if=_empty)
    spans: list[SpeakerSpan] = Field(default_factory=list, exclude_if=_empty)


class Recording(_Model):
    schema_ref: str | None = Field(default=None, alias="$schema")
    format: Literal["recordings-archive@1"] = FORMAT
    id: str = Field(pattern=ID_PATTERN)  # in the JSON Schema too, for editors and agents
    rev: int | None = Field(default=None, ge=0)  # changed only by mutate (§6.4); absent means 0
    title: str
    title_by: Literal["plaud", "you"] | None = None  # absent: the title came with the source
    plaud: PlaudState | None = None
    recorded_at: AwareDatetime
    timezone: str
    time_source: TimeSource
    media: MediaInfo
    sources: list[SourceRef] = Field(default_factory=list)
    tags: list[TagRef] = Field(default_factory=list)
    excluded_note_types: list[str] = Field(default_factory=list)
    speakers: Speakers = Field(default_factory=Speakers)
    chosen: Chosen = Field(default_factory=Chosen)

    @field_validator("id", mode="before")
    @classmethod
    def _id_shape(cls, value: object) -> object:
        # Before the pattern check, so a bad id is reported as what it is meant to be.
        if not isinstance(value, str) or not ID_RE.fullmatch(value):
            raise ValueError(f"not a recording id: {value!r}")
        return value


def is_private_tag(tag: str) -> bool:
    """Spec §7.4: the tag's first folder is `private`, in any capitalisation.

    The one place the rule lives. `Private/health` is private; `privateer` and
    `notes/private` are not.
    """
    return tag.split("/", 1)[0].casefold() == "private"


def is_private(rec: Recording) -> bool:
    """Spec §7.4: a recording is private iff any of its tags is private."""
    return any(is_private_tag(t.tag) for t in rec.tags)


def is_untagged(rec: Recording) -> bool:
    """Spec §7.1: untagged means no tags at all (notes/* tags count as tags)."""
    return not rec.tags


class Word(_Model):
    word: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class Segment(_Model):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    speaker: str | None = None  # the generic diarization label: Plaud's original_speaker (§9.1.1)
    speaker_name: str | None = None  # the name given in Plaud, when it differs from the label
    embedding_key: str | None = None  # Plaud's embeddingKey, snake_case in this schema
    text: str
    words: list[Word] = Field(default_factory=list)


class TranscriptPayload(_Model):
    language: str | None = None
    segments: list[Segment]


class NotesPayload(_Model):
    markdown: str


class Rendition(_Model):
    """One output file in renditions/ (extends audio-router/rendition@1, spec §6.5)."""

    schema_: Literal["recordings/rendition@1"] = Field(default=RENDITION_SCHEMA, alias="schema")
    kind: Literal["transcript", "speakers", "pick", "notes"]
    note_type: str | None = None
    engine: str
    model: str | None = None  # the model that actually answered
    version: str  # changes whenever the output would change
    created_at: AwareDatetime
    inputs: dict[str, str] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> Rendition:
        if self.kind == "notes":
            if not self.note_type:
                raise ValueError("a notes rendition needs a note_type")
            NotesPayload.model_validate(self.payload)
        elif self.note_type is not None:
            raise ValueError("only notes renditions carry a note_type")
        if self.kind == "transcript":
            TranscriptPayload.model_validate(self.payload)
        return self


def dump_json(model: BaseModel) -> str:
    """The one serialisation used for every file the archive writes: aliases, no nulls, UTF-8."""
    return model.model_dump_json(by_alias=True, exclude_none=True, indent=2) + "\n"
```

- [ ] **Step 4: Capture snapshot times and hashes, check more in `validate`, and show Plaud's names**

In `packages/core/src/recordings/archive.py`, **add** `import hashlib` if it is missing (it is
already imported for `sha256_file`), and **replace** the `RawSource` dataclass with:
```python
@dataclass(frozen=True)
class RawSource:
    kind: str
    ref: str
    added_at: datetime
    payload: bytes | None = None  # written verbatim as source/<kind>-<stamp>.json
    fetched_at: datetime | None = None  # when the source returned it; names the file

    def __post_init__(self) -> None:
        for name in ("added_at", "fetched_at"):
            value = getattr(self, name)
            if value is not None and value.utcoffset() is None:
                raise ValueError(f"RawSource.{name} must be timezone-aware")
```
and **replace** `_write_raw` with:
```python
    def _write_raw(self, folder: Path, source: RawSource) -> SourceRef:
        raw = sha = None
        if source.payload is not None:
            when = source.fetched_at or source.added_at
            target = folder / "source" / f"{_slug(source.kind)}-{utc_stamp(when)}.json"
            path = publish_exclusive(source.payload, target)
            raw = path.relative_to(folder).as_posix()
            sha = hashlib.sha256(source.payload).hexdigest()
        return SourceRef(kind=source.kind, ref=source.ref, added_at=source.added_at,
                         fetched_at=source.fetched_at, raw=raw, sha256=sha)
```

In `packages/core/src/recordings/selfdoc.py`, **replace** everything in `validate` from
`recordings = list(archive.iter_recordings())` to the end of the function with:
```python
    recordings = list(archive.iter_recordings())
    problems += [{"path": str(p.path), "message": p.message} for p in archive.problems]
    for rec in recordings:
        problems += _recording_problems(archive, rec)
    # Every folder's outputs, a broken recording.json's included.
    for folder in archive.recording_dirs():
        for path in sorted((folder / "renditions").glob("*.json")):
            try:
                Rendition.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, UnicodeDecodeError, OSError) as exc:
                problems.append({"path": str(path), "message": str(exc).splitlines()[0]})
    return problems


def _recording_problems(archive: Archive, rec) -> list[dict]:
    folder = archive.path_for(rec.id)
    here = str(folder / "recording.json")
    problems = []
    media = folder / rec.media.file
    try:
        present = media.resolve().is_relative_to(folder.resolve()) and media.is_file()
    except (OSError, ValueError):
        present = False
    if not present:
        problems.append({"path": here, "message": f"media.file {rec.media.file!r} is missing"})
    for source in rec.sources:
        if source.raw and not (folder / source.raw).is_file():
            problems.append({"path": here, "message": f"source file {source.raw} is missing"})
    if rec.media.duration_ms is not None:
        end = rec.media.duration_ms / 1000
        for span in rec.speakers.spans:
            if span.end > end:
                problems.append({"path": here, "message":
                                 f"speakers span {span.start}-{span.end} s runs past the "
                                 f"media's end ({end} s)"})
    return problems
```

In `packages/ui/src/recordings_ui/views.py`, **replace** `_turns` with:
```python
def _turns(r: Rendition) -> list[dict]:
    out = []
    for seg in r.payload.get("segments", []):
        # Until people arrive (stage 3b), a turn shows the name Plaud gave its speaker, if any,
        # and otherwise the diarization label.
        speaker = seg.get("speaker_name") or seg.get("speaker")
        out.append({
            "start": seg["start"],
            "end": seg["end"],
            "speaker": speaker or None,
            "text": seg["text"],
            "words": [{"word": w["word"], "start": w["start"], "end": w["end"]}
                      for w in seg.get("words", [])],
        })
    return out
```
and in `recording_view`, **replace** `"turns": _turns(r, rec.speakers)}` with `"turns": _turns(r)}`.

- [ ] **Step 5: Document the new keys**

In `packages/core/src/recordings/format/FORMAT.md`, **add** a section before `## Rules`:
```markdown
## recording.json

The schema is `schemas/recording.schema.json`. Keys added in stage 2a are optional, so older
files read unchanged.

- `rev`: the file's revision. It goes up by one with every change the app makes, and **only
  `mutate`** changes it. Edit the file by hand and leave `rev` alone: the app notices an outside
  edit by its content, not by `rev`. A file without `rev` is at revision 0.
- `title_by`: who set `title`, `plaud` or `you`. Absent means the title came with the source.
- `plaud.title_seen`: the Plaud title last seen or dismissed. `plaud.acknowledged_up_to`: the
  newest `source/` snapshot whose removals have been reviewed. Until then, a Plaud note that
  vanished, or names that reverted to "Speaker N", are held, not applied.
- `sources`: one entry per raw payload in `source/`, with the source's own `ref`, `added_at`,
  `fetched_at` (when the source returned it) and the file's `sha256`. Snapshots are ordered by
  `fetched_at`, then by their place in this list, never by file name.
- `speakers`: decisions only, never inferences. `source` is the output the labels belong to.
  `labels` maps a label to `{"person": "<slug>", "by": "you"}`, optionally with
  `plaud_name_seen`, or to `{"not": ["<slug>", …]}`. `spans` are `{start, end, person, by}` in
  media seconds. The reserved person `unknown` means "not anyone known" and overrides every
  inferred name for that time. Slugs match `^[a-z0-9]+(-[a-z0-9]+)*$`.

## Transcript segments

Each segment has `start` and `end` (seconds), `text`, optional `words`, and `speaker`: the
generic diarization label (for Plaud, its `original_speaker`). A Plaud transcript also carries
`speaker_name`, the name given in Plaud when it differs from the label, and `embedding_key`,
Plaud's `embeddingKey`.

Plaud's own transcript and notes are outputs with engine `plaud`, rebuilt from `source/`. Each
records its snapshot in `inputs.source` and its part's hash in `inputs.part_sha256`, and its
`version` (`plaud@<n>`) names the normaliser that produced it.
```
and **replace** the rule bullet that starts `- **Only three files are edited:**` with:
```markdown
- **The editable files:** `recording.json`, `my-notes.md` and `tags.yaml`, and from stage 3b
  `people.yaml` and `people.private.yaml`. Each save writes a temporary file, fsyncs it, and
  renames it into place. The app changes them only through `mutate`, under a lock.
```

In `packages/core/src/recordings/format/AGENTS.md`, **replace** rule 3 with:
```markdown
3. **Only edit `recording.json`, `my-notes.md` and `tags.yaml`** (and, from stage 3b, the people
   files). Never edit media, `source/` or `renditions/`: they are write-once. **Never change
   `rev`:** only the app's `mutate` changes it.
```

- [ ] **Step 6: Regenerate the schemas and the demo, and run every test**

Run:
```bash
uv run recordings schemas --write
uv run python demo/build.py --media-dir demo/.cache/media --out demo/archive --force
uv run pytest
```
Expected: `written: [...]`, `built 4 recordings into demo/archive`, then every test passes. The
demo's Plaud source entry now has `sha256`. Its `speakers` stay `{}`, and the schemas and docs in
`demo/archive/` change.

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/recordings packages/core/tests packages/ui/src/recordings_ui/views.py packages/ui/tests/test_views.py demo/archive
git commit -m "feat(core): rev, the speakers shape and Plaud's segment fields, inside recordings-archive@1

Every new key is optional, and an empty speakers block still serialises as {} (pydantic 2.13.5
Field(exclude_if=...), checked). validate now checks media, source files, spans and the outputs of
a broken recording.json.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The derived index (`index.db`)

**Checkpoint lens:** data integrity and write safety.

**Files:**
- Create: `packages/core/src/recordings/refs.py`, `packages/core/src/recordings/index.py`
- Test: `packages/core/tests/test_index.py`

**Interfaces:**
- Consumes: `Archive.iter_recordings()`, `Archive.problems`, `recordings.files.temp_name` and `fsync_dir`, and `recordings.ids.relative_dir`.
- Produces:
  - **`recordings.refs`:** `canonical_plaud_id(ref: str) -> str` and `canonical_ref(kind: str, ref: str) -> str`.
  - **`recordings.index`:**
    - `INDEX_VERSION = 1`
    - `class IndexRefused(RuntimeError)`
    - `class Index(path: Path)`, with these methods:

      | Method | Returns |
      |---|---|
      | `exists()` | `bool` |
      | `count()` | `int` |
      | `archive_uuid()` | `str \| None` |
      | `rebuild(archive: Archive, *, archive_uuid: str, allow_empty: bool = False)` | `int` |
      | `upsert(rec: Recording)` | `None` |
      | `find_by_sha256(sha: str)` | `str \| None` |
      | `sha256_of(recording_id: str)` | `str \| None` |
      | `find_by_source(kind: str, ref: str)` | `list[str]` |

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_index.py`:
```python
from datetime import datetime, timezone

import pytest

from recordings import index as index_module
from recordings.archive import Archive
from recordings.ids import make_id, relative_dir
from recordings.index import Index, IndexRefused
from recordings.models import MediaInfo, Recording, SourceRef, dump_json
from recordings.refs import canonical_plaud_id, canonical_ref

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
HEX = "0123456789abcdef0123456789abcdef"


def put(root, *, when="2026-10-06T14:00:03-07:00", sha="a" * 64, sources=()) -> Recording:
    """A minimal valid recording.json, written by hand: the index only reads files."""
    recorded_at = datetime.fromisoformat(when)
    rid = make_id(recorded_at, sha)
    folder = root / relative_dir(rid)
    folder.mkdir(parents=True)
    rec = Recording(id=rid, title="x", recorded_at=recorded_at, timezone="UTC",
                    time_source="ingest",
                    media=MediaInfo(file=f"{rid}.mp3", sha256=sha, kind="audio"),
                    sources=[SourceRef(kind=k, ref=r, added_at=T0) for k, r in sources])
    (folder / "recording.json").write_text(dump_json(rec), encoding="utf-8")
    return rec


@pytest.mark.parametrize("ref, canonical", [
    (HEX, HEX), ("of_" + HEX, HEX), ("OF_" + HEX.upper(), HEX), (" of_" + HEX + " ", HEX),
    ("demo-apollo13", "demo-apollo13"), ("of_xyz", "of_xyz"),
])
def test_both_plaud_id_forms_are_one_id(ref, canonical):
    # why: §6.3. Plaud's listing switched from bare hex to of_<hex> in September 2026.
    assert canonical_plaud_id(ref) == canonical


def test_other_sources_keep_their_references_as_they_are():
    assert canonical_ref("recorder", "Of_" + HEX) == "Of_" + HEX
    assert canonical_ref("plaud", "of_" + HEX) == HEX


def test_rebuild_then_look_up_by_hash_and_by_either_plaud_id_form(new_archive, tmp_path):
    root, _ = new_archive
    a = put(root, sha="a" * 64, sources=[("plaud", HEX), ("audio_router", f"plaud/{HEX}")])
    b = put(root, when="2026-10-07T09:00:00-07:00", sha="b" * 64,
            sources=[("pocket", "11111111-1111-4111-8111-111111111111")])
    index = Index(tmp_path / "index" / "index.db")
    assert index.rebuild(Archive(root), archive_uuid="u-1") == 2
    assert (index.count(), index.archive_uuid()) == (2, "u-1")
    assert index.find_by_sha256("b" * 64) == b.id
    assert index.sha256_of(a.id) == "a" * 64
    assert index.find_by_sha256("c" * 64) is None
    assert index.find_by_source("plaud", "of_" + HEX) == [a.id]
    assert index.find_by_source("audio_router", f"plaud/{HEX}") == [a.id]
    assert index.find_by_source("plaud", "f" * 32) == []


def test_a_missing_index_answers_nothing(tmp_path):
    index = Index(tmp_path / "nope" / "index.db")
    assert (index.exists(), index.count(), index.archive_uuid()) == (False, 0, None)
    assert index.find_by_sha256("a" * 64) is None and index.find_by_source("plaud", HEX) == []


def test_rebuild_refuses_to_empty_a_non_empty_index(new_archive, tmp_path):
    # why: §6.7. An archive that suddenly lists nothing is more likely unmounted than empty.
    root, _ = new_archive
    rec = put(root)
    index = Index(tmp_path / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")
    empty = tmp_path / "empty"
    (empty / "recordings").mkdir(parents=True)
    with pytest.raises(IndexRefused, match="refusing to empty"):
        index.rebuild(Archive(empty), archive_uuid="u-1")
    assert index.find_by_sha256(rec.media.sha256) == rec.id
    assert index.rebuild(Archive(empty), archive_uuid="u-1", allow_empty=True) == 0
    assert index.count() == 0


def test_a_failed_rebuild_leaves_the_old_index_and_no_temp_file(new_archive, tmp_path, monkeypatch):
    root, _ = new_archive
    rec = put(root)
    index = Index(tmp_path / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")
    put(root, when="2026-10-07T09:00:00-07:00", sha="b" * 64)

    def failing_insert(db, recording):
        raise OSError("disk full")

    monkeypatch.setattr(index_module, "_insert", failing_insert)
    with pytest.raises(OSError, match="disk full"):
        index.rebuild(Archive(root), archive_uuid="u-1")
    assert index.count() == 1 and index.find_by_sha256(rec.media.sha256) == rec.id
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_upsert_replaces_a_recordings_references(new_archive, tmp_path):
    root, _ = new_archive
    rec = put(root, sources=[("plaud", HEX)])
    index = Index(tmp_path / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")
    moved = rec.model_copy(update={"sources": [SourceRef(kind="recorder", ref="r-1", added_at=T0)],
                                   "rev": 3})
    index.upsert(moved)
    assert index.find_by_source("plaud", HEX) == []
    assert index.find_by_source("recorder", "r-1") == [rec.id]
    assert index.count() == 1
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_index.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings.index'`.

- [ ] **Step 3: Write the index**

`packages/core/src/recordings/refs.py`:
```python
"""Source references compared in one canonical form (spec §6.3, §9.1.1).

References are stored exactly as the source returned them. Only comparisons canonicalise: Plaud's
`of_<32 hex>` and the bare 32 hex are one ID, whatever their case. Every other source's references
are opaque.
"""

from __future__ import annotations

import re

_PLAUD_HEX = re.compile(r"[0-9a-f]{32}")


def canonical_plaud_id(ref: str) -> str:
    value = ref.strip().lower()
    if value.startswith("of_"):
        value = value[3:]
    return value if _PLAUD_HEX.fullmatch(value) else ref


def canonical_ref(kind: str, ref: str) -> str:
    return canonical_plaud_id(ref) if kind == "plaud" else ref
```

`packages/core/src/recordings/index.py`:
```python
"""The derived index (spec §6.8): `index.db`, rebuilt from the archive by `recordings reindex`.

Dropping it loses nothing. Writers keep it current after every write, and it answers the lookups a
writer needs at scale: which recording holds these bytes, and which holds this source reference.
`rebuild` refuses to empty an index that holds recordings (§6.7): an archive that suddenly lists
none is far more likely unmounted than empty. It builds a new file beside the old one and renames
it into place, so a failed rebuild leaves the old index as it was.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from recordings.archive import Archive
from recordings.files import fsync_dir, temp_name
from recordings.ids import relative_dir
from recordings.models import Recording
from recordings.refs import canonical_ref

INDEX_VERSION = 1
_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE recordings (
  id TEXT PRIMARY KEY, folder TEXT NOT NULL, sha256 TEXT NOT NULL, rev INTEGER NOT NULL);
CREATE INDEX recordings_by_sha256 ON recordings (sha256);
CREATE TABLE source_refs (
  recording_id TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, canonical TEXT NOT NULL,
  PRIMARY KEY (recording_id, kind, ref));
CREATE INDEX source_refs_by_canonical ON source_refs (kind, canonical);
"""


class IndexRefused(RuntimeError):
    """A rebuild that would empty a non-empty index."""


def _insert(db: sqlite3.Connection, rec: Recording) -> None:
    db.execute("INSERT INTO recordings (id, folder, sha256, rev) VALUES (?, ?, ?, ?)",
               (rec.id, relative_dir(rec.id).as_posix(), rec.media.sha256, rec.rev or 0))
    db.executemany(
        "INSERT OR IGNORE INTO source_refs (recording_id, kind, ref, canonical) VALUES (?, ?, ?, ?)",
        [(rec.id, s.kind, s.ref, canonical_ref(s.kind, s.ref)) for s in rec.sources])


class Index:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _db(self, path: Path | None = None) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(path or self.path, timeout=30)) as conn:
            with conn:
                yield conn

    def exists(self) -> bool:
        return self.path.is_file()

    def _one(self, sql: str, args: tuple = ()) -> object | None:
        if not self.exists():
            return None
        try:
            with self._db() as db:
                row = db.execute(sql, args).fetchone()
        except sqlite3.DatabaseError:  # a damaged or foreign file answers nothing; rebuild it
            return None
        return row[0] if row else None

    def count(self) -> int:
        return int(self._one("SELECT count(*) FROM recordings") or 0)

    def archive_uuid(self) -> str | None:
        return self._one("SELECT value FROM meta WHERE key = 'archive_uuid'")

    def rebuild(self, archive: Archive, *, archive_uuid: str, allow_empty: bool = False) -> int:
        recordings = list(archive.iter_recordings())
        held = self.count()
        if not recordings and held and not allow_empty:
            raise IndexRefused(
                f"{archive.root} lists no recordings but the index holds {held}: refusing to "
                "empty it. Is the archive mounted? Pass --allow-empty if it really is empty.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = temp_name(self.path)
        try:
            with self._db(tmp) as db:
                db.executescript(_SCHEMA)
                db.executemany("INSERT INTO meta (key, value) VALUES (?, ?)",
                               [("index_version", str(INDEX_VERSION)),
                                ("archive_uuid", archive_uuid)])
                for rec in recordings:
                    _insert(db, rec)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
        fsync_dir(self.path.parent)
        return len(recordings)

    def upsert(self, rec: Recording) -> None:
        with self._db() as db:
            db.execute("DELETE FROM source_refs WHERE recording_id = ?", (rec.id,))
            db.execute("DELETE FROM recordings WHERE id = ?", (rec.id,))
            _insert(db, rec)

    def find_by_sha256(self, sha: str) -> str | None:
        return self._one("SELECT id FROM recordings WHERE sha256 = ? ORDER BY id LIMIT 1", (sha,))

    def sha256_of(self, recording_id: str) -> str | None:
        return self._one("SELECT sha256 FROM recordings WHERE id = ?", (recording_id,))

    def find_by_source(self, kind: str, ref: str) -> list[str]:
        if not self.exists():
            return []
        with self._db() as db:
            rows = db.execute(
                "SELECT DISTINCT recording_id FROM source_refs WHERE kind = ? AND canonical = ? "
                "ORDER BY recording_id", (kind, canonical_ref(kind, ref))).fetchall()
        return [row[0] for row in rows]
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_index.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/recordings/refs.py packages/core/src/recordings/index.py packages/core/tests/test_index.py
git commit -m "feat(core): the derived index.db, with lookups by hash and either Plaud ID form

rebuild refuses to empty a non-empty index (spec §6.7) and replaces the file atomically. Checked:
docs.python.org sqlite3.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 5: The locked write path: `Writer`, `mutate` and `recordings reindex`

**Checkpoint lens:** data integrity and write safety.

This is §6.4's write contract. Stage 1's writers move onto it: `add_recording`, `write_rendition`
and `_merge_source`, the duplicate merge included.

**Files:**
- Create: `packages/core/src/recordings/locks.py`, `packages/core/src/recordings/writer.py`
- Modify: `packages/core/src/recordings/state.py` (revisions, flags, pending index updates)
- Modify: `packages/core/src/recordings/archive.py` (read-only now: whole file below)
- Modify: `packages/core/src/recordings/cli.py` (`reindex`), `format/AGENTS.md`, `format/FORMAT.md`
- Modify: `packages/ui/src/recordings_ui/settings.py`, `app.py` (docs at startup)
- Modify: `demo/build.py` (builds through the `Writer`), `demo/archive/` (rebuilt)
- Modify: `packages/core/tests/conftest.py`, `test_archive.py` (whole file below), `test_selfdoc.py`; `packages/ui/tests/conftest.py`, `test_views.py`, `test_app.py`
- Test: `packages/core/tests/test_locks.py`, `packages/core/tests/test_writer.py`

**Interfaces:**
- Consumes:
  - `State`, `StateError` (Task 2)
  - `Index`, `IndexRefused` (Task 4)
  - `read_sentinel`, `SentinelError` (Task 2)
  - from `recordings.files`: `copy_file_synced`, `publish_exclusive`, `write_bytes_atomic`, `write_text_atomic` and `fsync_dir` (Task 2)
  - `canonical_ref` (Task 4)
  - the models (Task 3)
- Produces:
  - **`recordings.locks`:**
    - `class LockTimeout(RuntimeError)`, `class LockOrderError(RuntimeError)`
    - `lock_order(key: str) -> tuple[int, int, str]`
    - `class Locks(folder: Path, *, timeout: float = 30.0)`, with `hold(*keys)`, a context manager
  - **`recordings.state.State`** gains:
    - `last_committed(file) -> Revision | None` and `pending(file) -> Revision | None`
    - `begin(file, rev, sha256, body, origin) -> int`, `commit(revision_id)`, `discard(revision_id)`, `adopt(file, rev, sha256, body) -> int`
    - `flag(file, kind, message)`, `clear_flags(file)`, `flags() -> list[dict]`
    - `mark_index_pending(recording_id)`, `clear_index_pending(recording_id)`, `index_pending() -> list[str]`, `clear_all_index_pending()`
    - `@dataclass(frozen=True) Revision(id, file, rev, sha256, body, origin, committed)`, and `KEEP_REVISIONS = 8`
  - **`recordings.writer`:**
    - errors: `class WriterError(RuntimeError)`, and its subclasses `NotTheWriter`, `StaleFile`, `InvalidFile`
    - `class Op(Protocol)`, with `name: str` and `apply(rec: Recording) -> Recording`
    - ops: `@dataclass(frozen=True) AddTags(tags: tuple[TagRef, ...])` and `MergeIncoming(sources: tuple[SourceRef, ...], tags: tuple[TagRef, ...] = (), excluded_note_types: tuple[str, ...] = ())`
    - `@dataclass(frozen=True) Incoming(media, recorded_at, timezone_name, time_source, title, kind, sources: tuple[RawSource, ...], title_by=None, duration_ms=None, sample_rate=None, channels=None, tags=(), renditions=(), my_notes=None, excluded_note_types=())`
    - `@dataclass(frozen=True) Added(recording: Recording, created: bool, sources_added: int, renditions_added: int)`
    - `@dataclass(frozen=True) ReindexReport(recordings: int, adopted: tuple[str, ...], invalid: tuple[str, ...], problems: tuple[dict, ...])`, with `.to_dict()`
    - `rendition_filename(rendition: Rendition) -> str`
    - `class Writer(root: Path, state: State, index: Index, *, writer_id: str)`:

      | Member | Returns |
      |---|---|
      | `root`, `state`, `index`, `archive`, `locks`, `identity` | (attributes) |
      | `Writer.open(cfg: Config)` | `Writer` |
      | `write_docs()` | `list[str]` |
      | `add(incoming: Incoming)` | `Added` |
      | `mutate(recording_id: str, op: Op)` | `Recording` |
      | `write_rendition(recording_id: str, rendition: Rendition)` | `str \| None` |
      | `reindex(*, allow_empty: bool = False)` | `ReindexReport` |
      | `rebuild_index(*, allow_empty: bool = False)` | `int` |
  - **CLI:** `recordings reindex [--allow-empty] [--json]`.
  - **`recordings_ui.settings.Settings`** gains `config: Config | None = None`.
  - **Test fixtures:**
    - core `conftest.py`: `make_writer(name="archive", writer_id="test") -> Writer`, `writer`, `make_incoming(content=None, *, name="clip.MP3", **over) -> Incoming`
    - ui `conftest.py`: `writer`
- **Removed:** `Archive.add_recording`, `Archive.write_rendition`, `Archive.find_by_sha256`, and the private writing helpers in `archive.py`.

- [ ] **Step 1: Write the failing lock tests**

`packages/core/tests/test_locks.py`:
```python
import subprocess
import sys
import threading
import time

import pytest

from recordings.locks import Locks, LockOrderError, LockTimeout, lock_order

A = "20261006T140003-0700_aaaaaaaa"
B = "20261007T140003-0700_bbbbbbbb"


def test_the_lock_order_is_people_then_tags_then_hashes_then_recordings():
    keys = ["index", B, "sha-" + "f" * 64, "tags.yaml", A, "people.private.yaml", "people.yaml"]
    assert sorted(keys, key=lock_order) == [
        "people.yaml", "people.private.yaml", "tags.yaml", "sha-" + "f" * 64, A, B, "index"]


def test_locks_taken_together_never_deadlock(tmp_path):
    # why: §6.4. Two operations naming the same files in opposite orders still take them in one.
    locks = Locks(tmp_path, timeout=10)
    done = []

    def worker(keys):
        for _ in range(50):
            with locks.hold(*keys):
                pass
        done.append(keys)

    threads = [threading.Thread(target=worker, args=(k,)) for k in ((A, B), (B, A))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(done) == 2


def test_taking_an_earlier_lock_while_holding_a_later_one_is_an_error(tmp_path):
    locks = Locks(tmp_path)
    with locks.hold(A), pytest.raises(LockOrderError), locks.hold("people.yaml"):
        pass


def test_holding_a_key_again_only_counts(tmp_path):
    locks, probe = Locks(tmp_path), Locks(tmp_path, timeout=0)
    with locks.hold(A):
        with locks.hold(A):
            pass
        with pytest.raises(LockTimeout), probe.hold(A):  # still held after the inner exit
            pass
    with probe.hold(A):  # released after the outer exit
        pass


def test_a_lock_is_released_when_its_process_dies(tmp_path):
    # why: §6.8. flock is released by the kernel, so a crash never leaves a stale lock.
    script = ("import sys,time; from pathlib import Path; from recordings.locks import Locks\n"
              "with Locks(Path(sys.argv[1])).hold(sys.argv[2]):\n"
              "    print('held', flush=True); time.sleep(60)\n")
    proc = subprocess.Popen([sys.executable, "-c", script, str(tmp_path), A],
                            stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    with pytest.raises(LockTimeout), Locks(tmp_path, timeout=0.2).hold(A):
        pass
    proc.kill()
    proc.wait(timeout=10)
    started = time.monotonic()
    with Locks(tmp_path, timeout=5).hold(A):
        assert time.monotonic() - started < 5
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_locks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings.locks'`.

- [ ] **Step 3: Write the locks**

`packages/core/src/recordings/locks.py`:
```python
"""Locks for the archive's editable files (spec §6.4, §6.8): `flock` on files in `<state>/locks/`.

The kernel releases an flock when its process dies, so a crash never leaves a stale lock. The lock
files live in the state folder, never in the archive, so backups and the mirror never copy them.

Locks are taken in one fixed order, so two multi-file operations can never deadlock: the people
files, then tags.yaml, then `sha-<hash>` keys, then recordings by sorted ID, then anything else
(`index`, `docs`). Taking an earlier lock while holding a later one raises LockOrderError instead
of risking a deadlock. Within one thread a Locks object is re-entrant: holding a key again only
counts. Separate threads, and separate processes, exclude each other.
"""

from __future__ import annotations

import errno
import fcntl
import os
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from recordings.ids import ID_RE

PEOPLE_FILES = ("people.yaml", "people.private.yaml")
_SAFE = re.compile(r"[^A-Za-z0-9._+-]")


class LockTimeout(RuntimeError):
    pass


class LockOrderError(RuntimeError):
    pass


def lock_order(key: str) -> tuple[int, int, str]:
    if key in PEOPLE_FILES:
        return (0, PEOPLE_FILES.index(key), "")
    if key == "tags.yaml":
        return (1, 0, "")
    if key.startswith("sha-"):
        return (2, 0, key)
    if ID_RE.fullmatch(key):
        return (3, 0, key)
    return (4, 0, key)


class Locks:
    def __init__(self, folder: Path, *, timeout: float = 30.0) -> None:
        self.folder = Path(folder)
        self.timeout = timeout
        self._local = threading.local()

    def _held(self) -> dict[str, list[int]]:
        """This thread's held keys: key -> [file descriptor, depth]."""
        held = getattr(self._local, "held", None)
        if held is None:
            held = self._local.held = {}
        return held

    @contextmanager
    def hold(self, *keys: str) -> Iterator[None]:
        held = self._held()
        wanted = set(keys)
        new = sorted((k for k in wanted if k not in held), key=lock_order)
        if new and held and lock_order(new[0]) < max(lock_order(k) for k in held):
            raise LockOrderError(
                f"lock {new[0]!r} comes before one already held; take all of an operation's "
                "locks together, in one hold()")
        counted: list[str] = []
        try:
            for key in new:
                held[key] = [self._acquire(key), 0]
            for key in wanted:
                held[key][1] += 1
                counted.append(key)
            yield
        finally:
            for key in counted:
                held[key][1] -= 1
            for key in [k for k in wanted if k in held and held[k][1] <= 0]:
                fd = held.pop(key)[0]
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _acquire(self, key: str) -> int:
        self.folder.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.folder / f"{_SAFE.sub('_', key)}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except OSError as exc:
                # fcntl docs: LOCK_NB failing sets errno to EAGAIN or EACCES, by platform.
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    os.close(fd)
                    raise
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise LockTimeout(
                        f"{key} is held by another writer; gave up after {self.timeout:g} s"
                        ) from None
                time.sleep(0.02)
```

- [ ] **Step 4: Run the lock tests to see them pass**

Run: `uv run pytest packages/core/tests/test_locks.py -v`
Expected: 5 passed.

- [ ] **Step 5: Write the failing writer tests**

Replace `packages/core/tests/conftest.py` with:
```python
"""Shared fixtures for the core tests. Every archive here is synthetic and lives in tmp_path."""

import itertools
from datetime import datetime, timezone

import pytest

from recordings.archive import RawSource
from recordings.index import Index
from recordings.init import init_archive
from recordings.state import State
from recordings.writer import Incoming, Writer

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
LOCAL = datetime.fromisoformat("2026-10-06T14:00:03-07:00")


@pytest.fixture
def new_archive(tmp_path):
    """An initialised, empty archive and its state folder: (root, state_path)."""
    root, state = tmp_path / "archive", tmp_path / "state"
    init_archive(root, state, writer_id="test")
    return root, state


@pytest.fixture
def make_writer(tmp_path):
    def make(name: str = "archive", writer_id: str = "test") -> Writer:
        root, state = tmp_path / name, tmp_path / f"{name}-state"
        init_archive(root, state, writer_id=writer_id)
        return Writer(root, State.open(state), Index(tmp_path / f"{name}-index" / "index.db"),
                      writer_id=writer_id)
    return make


@pytest.fixture
def writer(make_writer):
    return make_writer()


@pytest.fixture
def make_incoming(tmp_path):
    """An Incoming with a media file of its own. Pass `content` to make duplicates on purpose."""
    counter = itertools.count()

    def make(content: bytes | None = None, *, name: str = "clip.MP3", **over) -> Incoming:
        n = next(counter)
        path = tmp_path / "in" / f"{n}-{name}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if content is not None else f"ID3 fake audio {n}".encode())
        fields = dict(
            media=path, recorded_at=LOCAL, timezone_name="America/Vancouver",
            time_source="plaud", title="Week 4", kind="audio",
            sources=(RawSource(kind="plaud", ref="of_" + "a" * 32, added_at=T0,
                               payload=b'{"id": 1}'),),
        )
        fields.update(over)
        return Incoming(**fields)
    return make
```

`packages/core/tests/test_writer.py`:
```python
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from recordings import writer as writer_module
from recordings.archive import Archive, RawSource, sha256_file
from recordings.config import load_config
from recordings.index import Index, IndexRefused
from recordings.models import Rendition, TagRef
from recordings.sentinel import SENTINEL
from recordings.writer import (
    AddTags,
    InvalidFile,
    NotTheWriter,
    StaleFile,
    Writer,
    WriterError,
)

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def notes(created_at=T0, model="qwen3.6-35b-a3b", markdown="# Notes") -> Rendition:
    return Rendition(kind="notes", note_type="lecture", engine="canned", model=model,
                     version=f"{model}@demo", created_at=created_at, payload={"markdown": markdown})


def test_add_lays_out_the_folder_and_starts_at_rev_1(writer, make_incoming):
    incoming = make_incoming()
    added = writer.add(incoming)
    rec, sha = added.recording, sha256_file(incoming.media)
    assert added.created and rec.id == f"20261006T140003-0700_{sha[:8]}" and rec.rev == 1
    folder = writer.root / "recordings" / "2026" / "10" / rec.id
    assert (folder / f"{rec.id}.mp3").read_bytes() == incoming.media.read_bytes()
    assert (folder / "source" / "plaud-20261008T120000Z.json").read_bytes() == b'{"id": 1}'
    data = json.loads((folder / "recording.json").read_text(encoding="utf-8"))
    assert data["rev"] == 1 and data["$schema"] == "../../../../schemas/recording.schema.json"
    assert data["sources"][0]["sha256"] == hashlib.sha256(b'{"id": 1}').hexdigest()
    assert not (writer.root / ".tmp").exists()
    assert writer.index.find_by_sha256(sha) == rec.id
    revision = writer.state.last_committed(f"recordings/2026/10/{rec.id}/recording.json")
    assert revision.rev == 1 and revision.body == (folder / "recording.json").read_bytes()


def test_a_writer_needs_the_sentinel_the_same_archive_and_the_same_writer(make_writer, tmp_path):
    writer = make_writer()
    with pytest.raises(NotTheWriter, match="writer_id"):
        Writer(writer.root, writer.state, writer.index, writer_id="laptop")
    other = make_writer("other")
    with pytest.raises(NotTheWriter, match="UUID changed"):
        Writer(writer.root, other.state, writer.index, writer_id="test")
    (writer.root / SENTINEL).unlink()
    with pytest.raises(NotTheWriter, match="mounted"):
        Writer(writer.root, writer.state, writer.index, writer_id="test")


def test_writer_open_says_which_settings_are_missing_and_writes_the_docs(writer, tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'[archive]\npath = "{writer.root}"\n', encoding="utf-8")
    with pytest.raises(NotTheWriter, match=r"\[state\] path"):
        Writer.open(load_config({"RECORDINGS_CONFIG": str(cfg_file)}))
    cfg_file.write_text(
        f'[archive]\npath = "{writer.root}"\nwriter_id = "test"\n[state]\npath = "{writer.state.path}"\n'
        f'[index]\npath = "{writer.index.path}"\n', encoding="utf-8")
    (writer.root / "FORMAT.md").unlink()
    Writer.open(load_config({"RECORDINGS_CONFIG": str(cfg_file)}))
    assert (writer.root / "FORMAT.md").is_file()  # §6.7: the docs are written at startup


def test_the_same_bytes_again_merge_and_keep_the_second_copys_outputs_tags_and_notes(
        writer, make_incoming):
    # why: §6.4. The merge keeps the second copy's sources, outputs, decisions and notes.
    first = writer.add(make_incoming(b"same bytes", my_notes="first\n")).recording
    second = writer.add(make_incoming(
        b"same bytes", name="other.mp3",
        sources=(RawSource(kind="upload", ref="other.mp3", added_at=T0),),
        tags=(TagRef(tag="talks"),), renditions=(notes(),), my_notes="second\n",
        excluded_note_types=("glossary",)))
    assert not second.created and second.recording.id == first.id
    assert (second.sources_added, second.renditions_added) == (1, 1)
    rec = writer.archive.load(first.id)
    assert [s.kind for s in rec.sources] == ["plaud", "upload"]
    assert [t.tag for t in rec.tags] == ["talks"] and rec.excluded_note_types == ["glossary"]
    assert rec.rev == 2
    assert len(writer.archive.renditions(first.id)) == 1
    assert writer.archive.read_my_notes(first.id) == "first\n\n---\n\nsecond\n"
    assert len(list(writer.archive.recording_dirs())) == 1


def test_adding_the_same_thing_twice_adds_nothing(writer, make_incoming):
    incoming = make_incoming(b"bytes", renditions=(notes(),), my_notes="mine\n")
    writer.add(incoming)
    again = writer.add(incoming)
    assert (again.sources_added, again.renditions_added) == (0, 0)
    assert again.recording.rev == 1  # nothing changed, so nothing was written
    assert writer.archive.read_my_notes(again.recording.id) == "mine\n"


def test_a_new_snapshot_of_the_same_source_is_kept_beside_the_first(writer, make_incoming):
    writer.add(make_incoming(b"bytes"))
    later = RawSource(kind="plaud", ref="a" * 32, added_at=T0,
                      fetched_at=datetime(2026, 10, 9, tzinfo=timezone.utc), payload=b'{"id": 2}')
    added = writer.add(make_incoming(b"bytes", sources=(later,)))
    assert added.sources_added == 1
    assert [s.raw for s in added.recording.sources] == [
        "source/plaud-20261008T120000Z.json", "source/plaud-20261009T000000Z.json"]


def test_renditions_are_write_once_ordered_and_never_duplicated(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    a = writer.write_rendition(rec.id, notes(T0.replace(minute=5), model="b-model"))
    b = writer.write_rendition(rec.id, notes(T0, model="a-model"))
    c = writer.write_rendition(rec.id, notes(T0, model="a-model", markdown="# Other"))
    assert writer.write_rendition(rec.id, notes(T0, model="a-model")) is None  # identical
    assert c.endswith("-2.json")
    paths = [p for p, _ in writer.archive.renditions(rec.id)]
    assert set(paths[:2]) == {b, c} and paths[2] == a
    with pytest.raises(KeyError):
        writer.write_rendition("20200101T000000+0000_00000000", notes())


def test_a_non_ascii_title_round_trips_as_utf8(writer, make_incoming):
    rec = writer.add(make_incoming(title="会議メモ 🎙")).recording
    raw = (writer.archive.path_for(rec.id) / "recording.json").read_bytes()
    assert "会議メモ 🎙".encode() in raw and writer.archive.load(rec.id).title == "会議メモ 🎙"


def test_a_failed_assembly_leaves_nothing_behind(writer, make_incoming, monkeypatch):
    def failing_copy(*args, **kwargs):
        raise OSError("Simulated copy failure")

    monkeypatch.setattr(writer_module, "copy_file_synced", failing_copy)
    with pytest.raises(OSError, match="Simulated copy failure"):
        writer.add(make_incoming())
    assert not (writer.root / ".tmp").exists()
    assert list(writer.archive.recording_dirs()) == []


def test_media_that_changes_while_it_is_copied_is_refused(writer, make_incoming, monkeypatch):
    real_copy = writer_module.copy_file_synced

    def copy_then_corrupt(src, dst):
        real_copy(src, dst)
        dst.write_bytes(b"different")

    monkeypatch.setattr(writer_module, "copy_file_synced", copy_then_corrupt)
    with pytest.raises(WriterError, match="changed while it was being copied"):
        writer.add(make_incoming())
    assert list(writer.archive.recording_dirs()) == []


def test_mutate_bumps_rev_and_a_no_op_writes_nothing(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    tagged = writer.mutate(rec.id, AddTags((TagRef(tag="talks"),)))
    assert tagged.rev == 2 and writer.archive.load(rec.id).rev == 2
    path = writer.archive.path_for(rec.id) / "recording.json"
    before = path.read_bytes()
    assert writer.mutate(rec.id, AddTags((TagRef(tag="talks"),))).rev == 2
    assert path.read_bytes() == before


def test_mutate_refuses_an_outside_edit_until_reindex_accepts_it(writer, make_incoming):
    # why: §6.4. Changes are found by content hash, not mtime or rev: an editor can keep both.
    rec = writer.add(make_incoming()).recording
    path = writer.archive.path_for(rec.id) / "recording.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["title"] = "Edited by hand"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(StaleFile, match="recordings reindex"):
        writer.mutate(rec.id, AddTags((TagRef(tag="talks"),)))
    assert [f["kind"] for f in writer.state.flags()] == ["stale"]
    report = writer.reindex()
    assert report.adopted == (f"recordings/2026/10/{rec.id}/recording.json",)
    assert writer.state.flags() == []
    done = writer.mutate(rec.id, AddTags((TagRef(tag="talks"),)))
    assert done.title == "Edited by hand" and done.rev == 2


def test_mutate_refuses_an_invalid_file_and_flags_it(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    writer.add(make_incoming(recorded_at=datetime.fromisoformat("2026-10-07T09:00:00-07:00")))
    path = writer.archive.path_for(rec.id) / "recording.json"
    path.write_text("{", encoding="utf-8")
    writer.reindex()  # accepts nothing for this file: it can't be parsed
    assert [f["kind"] for f in writer.state.flags()] == ["invalid"]
    with pytest.raises((InvalidFile, StaleFile)):
        writer.mutate(rec.id, AddTags((TagRef(tag="talks"),)))
    assert path.read_text(encoding="utf-8") == "{"  # §11: never overwritten or "fixed"


def test_a_crash_before_the_file_is_replaced_loses_only_that_write(writer, make_incoming,
                                                                   monkeypatch):
    # Review Focus 1: a pending revision whose file never landed is discarded, not "stale".
    rec = writer.add(make_incoming()).recording

    def power_cut(path, data):
        raise OSError("power cut")

    monkeypatch.setattr(writer_module, "write_bytes_atomic", power_cut)
    with pytest.raises(OSError, match="power cut"):
        writer.mutate(rec.id, AddTags((TagRef(tag="lost"),)))
    monkeypatch.undo()
    done = writer.mutate(rec.id, AddTags((TagRef(tag="kept"),)))
    assert [t.tag for t in done.tags] == ["kept"] and done.rev == 2


def test_a_crash_after_the_file_is_replaced_keeps_the_write(writer, make_incoming, monkeypatch):
    # Review Focus 1: the file landed but its revision wasn't committed. The next write commits
    # it, instead of calling the app's own write an outside edit.
    rec = writer.add(make_incoming()).recording
    real_commit = writer.state.commit
    calls = []

    def crash_once(revision_id):
        calls.append(revision_id)
        if len(calls) == 1:
            raise OSError("power cut")
        real_commit(revision_id)

    monkeypatch.setattr(writer.state, "commit", crash_once)
    with pytest.raises(OSError, match="power cut"):
        writer.mutate(rec.id, AddTags((TagRef(tag="landed"),)))
    done = writer.mutate(rec.id, AddTags((TagRef(tag="next"),)))
    assert [t.tag for t in done.tags] == ["landed", "next"] and done.rev == 3


def test_an_index_left_behind_by_a_crash_never_causes_a_duplicate(writer, make_incoming):
    first = writer.add(make_incoming(b"bytes")).recording
    writer.index.path.unlink()  # as if the process died after the rename, before the index
    Index(writer.index.path).rebuild(Archive(writer.root / "nowhere"),
                                     archive_uuid=writer.identity.uuid, allow_empty=True)
    again = writer.add(make_incoming(b"bytes", sources=(RawSource(kind="upload", ref="u",
                                                                  added_at=T0),)))
    assert again.recording.id == first.id and not again.created


def test_a_killed_writers_assembly_is_swept_when_its_lock_is_free(writer, make_incoming):
    # Review Focus 2: a writer killed mid-assembly leaves .tmp/<id>.<hex>; the next writer
    # removes it, unless that recording's lock is still held.
    rid = "20261006T140003-0700_deadbeef"
    stray = writer.root / ".tmp" / f"{rid}.0123"
    (stray / "source").mkdir(parents=True)
    held = writer.root / ".tmp" / "20261007T140003-0700_cafebabe.4567"
    held.mkdir()
    with writer.locks.hold("20261007T140003-0700_cafebabe"):
        Writer(writer.root, writer.state, writer.index, writer_id="test")
    assert not stray.exists() and held.exists()


def test_reindex_refuses_to_empty_the_index(writer, make_incoming, tmp_path):
    writer.add(make_incoming())
    (writer.root / "recordings").rename(tmp_path / "moved-away")
    (writer.root / "recordings").mkdir()
    with pytest.raises(IndexRefused):
        writer.reindex()
    assert writer.index.count() == 1


SCRIPT = r"""
import sys
from pathlib import Path
from recordings.index import Index
from recordings.models import TagRef
from recordings.state import State
from recordings.writer import AddTags, Writer
root, state, index, rid, prefix, n = sys.argv[1:7]
w = Writer(Path(root), State.open(Path(state)), Index(Path(index)), writer_id="test")
for i in range(int(n)):
    w.mutate(rid, AddTags((TagRef(tag=f"{prefix}{i}"),)))
"""


def test_two_writer_processes_never_lose_an_update(writer, make_incoming):
    # why: §9.1.1 asks for a lost-update test with two writer processes.
    rec = writer.add(make_incoming()).recording
    args = [str(writer.root), str(writer.state.path), str(writer.index.path), rec.id]
    procs = [subprocess.Popen([sys.executable, "-c", SCRIPT, *args, prefix, "25"])
             for prefix in ("a", "b")]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    final = writer.archive.load(rec.id)
    assert len(final.tags) == 50 and final.rev == 51
```

- [ ] **Step 6: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_writer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings.writer'` (collection error in
`conftest.py`).

- [ ] **Step 7: Add the revisions, flags and pending index updates to `state.db`**

In `packages/core/src/recordings/state.py`:
1. **Add** the imports `from dataclasses import dataclass` and
   `from datetime import datetime, timezone`.
2. **Append** to `SCHEMA` (before its closing `"""`):
   ```sql
   CREATE TABLE IF NOT EXISTS revisions (
     id INTEGER PRIMARY KEY AUTOINCREMENT, file TEXT NOT NULL, rev INTEGER NOT NULL,
     sha256 TEXT NOT NULL, body BLOB NOT NULL, origin TEXT NOT NULL,
     committed INTEGER NOT NULL, written_at TEXT NOT NULL);
   CREATE INDEX IF NOT EXISTS revisions_by_file ON revisions (file, id);
   CREATE TABLE IF NOT EXISTS flags (
     file TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, at TEXT NOT NULL,
     PRIMARY KEY (file, kind));
   CREATE TABLE IF NOT EXISTS index_pending (recording_id TEXT PRIMARY KEY);
   ```
3. **Add** after `class StateError`:
   ```python
   KEEP_REVISIONS = 8  # merge bases kept per file (§6.4); stage 3's three-way merge reads them


   @dataclass(frozen=True)
   class Revision:
       id: int
       file: str
       rev: int
       sha256: str
       body: bytes
       origin: str  # the op's name, "create" or "adopted"
       committed: bool


   def _now() -> str:
       return datetime.now(timezone.utc).isoformat()
   ```
4. **Add** these methods to `State`:
   ```python
       # ---- revisions: the merge bases (§6.4), in two phases so a crash is recognisable --------
       def _revision(self, sql: str, args: tuple) -> Revision | None:
           with self.db() as db:
               row = db.execute(
                   "SELECT id, file, rev, sha256, body, origin, committed FROM revisions " + sql,
                   args).fetchone()
           return Revision(*row[:6], committed=bool(row[6])) if row else None

       def last_committed(self, file: str) -> Revision | None:
           return self._revision("WHERE file = ? AND committed = 1 ORDER BY id DESC LIMIT 1",
                                 (file,))

       def pending(self, file: str) -> Revision | None:
           return self._revision("WHERE file = ? AND committed = 0 ORDER BY id DESC LIMIT 1",
                                 (file,))

       def begin(self, file: str, rev: int, sha256: str, body: bytes, origin: str) -> int:
           with self.db() as db:
               cur = db.execute(
                   "INSERT INTO revisions (file, rev, sha256, body, origin, committed, written_at) "
                   "VALUES (?, ?, ?, ?, ?, 0, ?)", (file, rev, sha256, body, origin, _now()))
               return int(cur.lastrowid)

       def commit(self, revision_id: int) -> None:
           with self.db() as db:
               (file,) = db.execute("SELECT file FROM revisions WHERE id = ?",
                                    (revision_id,)).fetchone()
               db.execute("UPDATE revisions SET committed = 1 WHERE id = ?", (revision_id,))
               db.execute(
                   "DELETE FROM revisions WHERE file = ? AND committed = 1 AND id NOT IN "
                   "(SELECT id FROM revisions WHERE file = ? AND committed = 1 "
                   " ORDER BY id DESC LIMIT ?)", (file, file, KEEP_REVISIONS))

       def discard(self, revision_id: int) -> None:
           with self.db() as db:
               db.execute("DELETE FROM revisions WHERE id = ? AND committed = 0", (revision_id,))

       def adopt(self, file: str, rev: int, sha256: str, body: bytes) -> int:
           revision_id = self.begin(file, rev, sha256, body, "adopted")
           self.commit(revision_id)
           return revision_id

       # ---- flags: files that need attention (until stage 3's UI shows them) -----------------
       def flag(self, file: str, kind: str, message: str) -> None:
           with self.db() as db:
               db.execute("INSERT OR REPLACE INTO flags (file, kind, message, at) VALUES (?, ?, ?, ?)",
                          (file, kind, message, _now()))

       def clear_flags(self, file: str) -> None:
           with self.db() as db:
               db.execute("DELETE FROM flags WHERE file = ?", (file,))

       def flags(self) -> list[dict]:
           with self.db() as db:
               rows = db.execute(
                   "SELECT file, kind, message, at FROM flags ORDER BY file, kind").fetchall()
           return [{"file": f, "kind": k, "message": m, "at": a} for f, k, m, a in rows]

       # ---- index updates not yet made: a crash between a write and its index update ---------
       def mark_index_pending(self, recording_id: str) -> None:
           with self.db() as db:
               db.execute("INSERT OR IGNORE INTO index_pending (recording_id) VALUES (?)",
                          (recording_id,))

       def clear_index_pending(self, recording_id: str) -> None:
           with self.db() as db:
               db.execute("DELETE FROM index_pending WHERE recording_id = ?", (recording_id,))

       def index_pending(self) -> list[str]:
           with self.db() as db:
               return [r[0] for r in db.execute(
                   "SELECT recording_id FROM index_pending ORDER BY recording_id")]

       def clear_all_index_pending(self) -> None:
           with self.db() as db:
               db.execute("DELETE FROM index_pending")
   ```

- [ ] **Step 8: Write the `Writer`, and make `Archive` read-only**

`packages/core/src/recordings/writer.py`:
```python
"""The locked write path (spec §6.4): the only code that changes an archive.

A Writer refuses to exist unless the archive's sentinel, this machine's state.db and the configured
writer ID all agree (§3, §6.7). Every change to a recording.json is an operation applied by
`mutate` under that recording's lock. It checks the file's content hash against the revision the
app last wrote (a merge base in state.db), applies the operation, bumps `rev`, and writes the file
durably. A file edited outside the app is refused until `recordings reindex` accepts the edit
(three-way merges arrive in stage 3). New recordings are assembled under `.tmp/` and renamed into
place whole. Identical bytes arriving again merge into the existing recording, keeping the second
copy's sources, outputs, tags and notes.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import ValidationError

from recordings.archive import Archive, RawSource, sha256_file, utc_stamp
from recordings.config import Config
from recordings.files import (
    copy_file_synced,
    fsync_dir,
    publish_exclusive,
    write_bytes_atomic,
    write_text_atomic,
)
from recordings.ids import ID_RE, make_id, relative_dir
from recordings.index import Index
from recordings.locks import Locks, LockTimeout
from recordings.models import (
    SCHEMA_REF,
    MediaInfo,
    Recording,
    Rendition,
    SourceRef,
    TagRef,
    TimeSource,
    dump_json,
)
from recordings.refs import canonical_ref
from recordings.selfdoc import write_docs
from recordings.sentinel import SentinelError, read_sentinel
from recordings.state import State, StateError

INDEX_LOCK = "index"
DOCS_LOCK = "docs"
_UNSAFE = re.compile(r"[^A-Za-z0-9.@+_-]+")


class WriterError(RuntimeError):
    """Every refusal to write."""


class NotTheWriter(WriterError):
    """This process may not write this archive (§3, §6.7)."""


class StaleFile(WriterError):
    """The file changed outside the app since the app last wrote it."""


class InvalidFile(WriterError):
    """The file on disk doesn't validate, so nothing is applied to it (§11)."""


class Op(Protocol):
    name: str

    def apply(self, rec: Recording) -> Recording: ...


@dataclass(frozen=True)
class AddTags:
    tags: tuple[TagRef, ...]
    name: str = "add-tags"

    def apply(self, rec: Recording) -> Recording:
        tags, have = list(rec.tags), {t.tag for t in rec.tags}
        for tag in self.tags:
            if tag.tag not in have:
                tags.append(tag)
                have.add(tag.tag)
        return rec.model_copy(update={"tags": tags})


@dataclass(frozen=True)
class MergeIncoming:
    """The duplicate merge (§6.4): the second copy's sources, tags and excluded note types join
    the first. Its outputs and notes are written beside the first before this op runs."""

    sources: tuple[SourceRef, ...]
    tags: tuple[TagRef, ...] = ()
    excluded_note_types: tuple[str, ...] = ()
    name: str = "merge-duplicate"

    def apply(self, rec: Recording) -> Recording:
        rec = AddTags(self.tags).apply(rec)
        excluded = list(rec.excluded_note_types)
        excluded += [n for n in dict.fromkeys(self.excluded_note_types) if n not in excluded]
        return rec.model_copy(update={"sources": [*rec.sources, *self.sources],
                                      "excluded_note_types": excluded})


@dataclass(frozen=True)
class Incoming:
    """One recording, as a source hands it to the writer (spec §9.0)."""

    media: Path
    recorded_at: datetime
    timezone_name: str
    time_source: TimeSource
    title: str
    kind: Literal["audio", "video"]
    sources: tuple[RawSource, ...]
    title_by: Literal["plaud", "you"] | None = None
    duration_ms: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    tags: tuple[TagRef, ...] = ()
    renditions: tuple[Rendition, ...] = ()
    my_notes: str | None = None
    excluded_note_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class Added:
    recording: Recording
    created: bool  # False: identical bytes were already there, and this merged into them
    sources_added: int
    renditions_added: int


@dataclass(frozen=True)
class ReindexReport:
    recordings: int
    adopted: tuple[str, ...]  # outside edits accepted as the new merge base
    invalid: tuple[str, ...]  # files that don't validate, flagged in state.db
    problems: tuple[dict, ...]

    def to_dict(self) -> dict:
        return {"recordings": self.recordings, "adopted": list(self.adopted),
                "invalid": list(self.invalid), "problems": list(self.problems)}


def _slug(value: str) -> str:
    return _UNSAFE.sub("_", value).strip("_") or "x"


def rendition_filename(rendition: Rendition) -> str:
    """`<kind>-<engine>-<version>-<UTC stamp>.json`; notes' kind part is `notes-<note type>`."""
    kind = f"notes-{rendition.note_type}" if rendition.kind == "notes" else rendition.kind
    parts = [_slug(kind), _slug(rendition.engine), _slug(rendition.version),
             utc_stamp(rendition.created_at)]
    return "-".join(parts) + ".json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rev_of(raw: bytes) -> int:
    with contextlib.suppress(ValueError, AttributeError, TypeError):
        value = json.loads(raw).get("rev")
        if isinstance(value, int) and value >= 0:
            return value
    return 0


class Writer:
    def __init__(self, root: Path, state: State, index: Index, *, writer_id: str) -> None:
        self.root = Path(root)
        try:
            self.identity = read_sentinel(self.root)
        except SentinelError as exc:
            raise NotTheWriter(str(exc)) from None
        if state.meta("archive_uuid") != self.identity.uuid:
            raise NotTheWriter(
                f"{state.db_path} belongs to another archive: the archive's UUID changed. "
                "Refusing to write.")
        recorded = state.meta("writer_id")
        if recorded != writer_id:
            raise NotTheWriter(
                f"this process's writer_id {writer_id!r} is not the archive's writer "
                f"({recorded!r}); only the writer writes (spec §3)")
        self.state, self.index, self.writer_id = state, index, writer_id
        self.archive = Archive(self.root)  # its own reader, never the UI's
        self.locks = Locks(state.locks_dir)
        self._sweep_tmp()
        if index.archive_uuid() != self.identity.uuid or state.index_pending():
            self.rebuild_index(allow_empty=True)

    @classmethod
    def open(cls, cfg: Config) -> Writer:
        missing = [name for name, value in (("[archive] path", cfg.archive_path),
                                            ("[state] path", cfg.state_path),
                                            ("[index] path", cfg.index_path),
                                            ("[archive] writer_id", cfg.writer_id))
                   if value is None]
        if missing:
            raise NotTheWriter("set " + ", ".join(missing) + " in config.toml to write the archive")
        try:
            state = State.open(cfg.state_path)
        except StateError as exc:
            raise NotTheWriter(str(exc)) from None
        writer = cls(cfg.archive_path, state, Index(cfg.index_path), writer_id=cfg.writer_id)
        writer.write_docs()  # §6.7: at startup, and only into an archive with its sentinel
        return writer

    # ---- docs and the index ----------------------------------------------------------------
    def write_docs(self) -> list[str]:
        with self.locks.hold(DOCS_LOCK):
            return write_docs(self.root)

    def rebuild_index(self, *, allow_empty: bool = False) -> int:
        with self.locks.hold(INDEX_LOCK):
            count = self.index.rebuild(self.archive, archive_uuid=self.identity.uuid,
                                       allow_empty=allow_empty)
            self.state.clear_all_index_pending()
        return count

    def _index(self, rec: Recording) -> None:
        with self.locks.hold(INDEX_LOCK):
            self.index.upsert(rec)
            self.state.clear_index_pending(rec.id)

    # ---- mutate: every change to a recording.json -------------------------------------------
    @staticmethod
    def _rel(recording_id: str) -> str:
        return (relative_dir(recording_id) / "recording.json").as_posix()

    def mutate(self, recording_id: str, op: Op) -> Recording:
        if not ID_RE.fullmatch(recording_id):
            raise KeyError(recording_id)
        rel = self._rel(recording_id)
        with self.locks.hold(recording_id):
            rec = self._load_fresh(rel)
            new = op.apply(rec)
            if new == rec:
                return rec
            try:
                new = Recording.model_validate(
                    {**new.model_dump(by_alias=True), "rev": (rec.rev or 0) + 1})
            except ValidationError as exc:
                raise InvalidFile(
                    f"{op.name} would make {rel} invalid: {str(exc).splitlines()[0]}") from None
            self._write_recording(rel, new, origin=op.name)
            return new

    def _load_fresh(self, rel: str) -> Recording:
        try:
            raw = (self.root / rel).read_bytes()
        except FileNotFoundError:
            raise KeyError(rel) from None
        self._check_fresh(rel, raw)
        try:
            return Recording.model_validate_json(raw)
        except ValidationError as exc:
            message = str(exc).splitlines()[0]
            self.state.flag(rel, "invalid", message)
            raise InvalidFile(
                f"{rel} does not validate ({message}); fix it, then run `recordings reindex`"
                ) from None

    def _check_fresh(self, rel: str, raw: bytes) -> None:
        sha = _sha(raw)
        pending = self.state.pending(rel)
        if pending is not None:
            if pending.sha256 == sha:  # the file landed; its revision's commit didn't
                self.state.commit(pending.id)
                return
            self.state.discard(pending.id)  # the replace never happened
        last = self.state.last_committed(rel)
        if last is None:  # no history yet (a new state.db, or a file from before the writer)
            self.state.adopt(rel, _rev_of(raw), sha, raw)
            return
        if last.sha256 != sha:
            self.state.flag(rel, "stale", "edited outside the app since the app last wrote it; "
                                          "run `recordings reindex` to accept the edit")
            raise StaleFile(
                f"{rel} was edited outside the app since the app last wrote it. Run "
                "`recordings reindex` to accept the edit, then try again.")

    def _write_recording(self, rel: str, rec: Recording, *, origin: str) -> None:
        data = dump_json(rec).encode("utf-8")
        self.state.mark_index_pending(rec.id)
        revision = self.state.begin(rel, rec.rev or 0, _sha(data), data, origin)
        write_bytes_atomic(self.root / rel, data)
        self.state.commit(revision)
        self._index(rec)

    # ---- adding recordings -------------------------------------------------------------------
    def add(self, incoming: Incoming) -> Added:
        sha = sha256_file(incoming.media)
        rid = make_id(incoming.recorded_at, sha)
        with self.locks.hold(f"sha-{sha}"):
            existing = self.index.find_by_sha256(sha)
            if existing is None and (self.root / self._rel(rid)).is_file():
                existing = rid  # the index is behind (a crash after the rename): the folder wins
            if existing is not None:
                return self._merge(existing, sha, incoming)
            return self._create(rid, sha, incoming)

    def _create(self, rid: str, sha: str, incoming: Incoming) -> Added:
        final = self.root / relative_dir(rid)
        with self.locks.hold(rid):
            if final.exists():
                raise WriterError(f"{final} exists but holds different media")
            tmp = self.root / ".tmp" / f"{rid}.{uuid.uuid4().hex}"
            revision = None
            try:
                (tmp / "source").mkdir(parents=True)
                (tmp / "renditions").mkdir()
                media_name = f"{rid}{incoming.media.suffix.lower()}"
                copy_file_synced(incoming.media, tmp / media_name)
                if sha256_file(tmp / media_name) != sha:  # hash the copy, not only the source
                    raise WriterError(f"{incoming.media} changed while it was being copied")
                sources = [self._publish_raw(tmp, s) for s in incoming.sources]
                for rendition in incoming.renditions:
                    self._publish_rendition(tmp, rendition)
                if incoming.my_notes is not None:
                    write_text_atomic(tmp / "my-notes.md", incoming.my_notes)
                rec = Recording(
                    schema_ref=SCHEMA_REF, id=rid, rev=1, title=incoming.title,
                    title_by=incoming.title_by, recorded_at=incoming.recorded_at,
                    timezone=incoming.timezone_name, time_source=incoming.time_source,
                    media=MediaInfo(file=media_name, sha256=sha, kind=incoming.kind,
                                    duration_ms=incoming.duration_ms,
                                    sample_rate=incoming.sample_rate, channels=incoming.channels),
                    sources=sources, tags=list(incoming.tags),
                    excluded_note_types=list(dict.fromkeys(incoming.excluded_note_types)))
                data = dump_json(rec).encode("utf-8")
                write_bytes_atomic(tmp / "recording.json", data)
                rel = self._rel(rid)
                self.state.mark_index_pending(rid)
                revision = self.state.begin(rel, 1, _sha(data), data, "create")
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, final)
            except BaseException:
                shutil.rmtree(tmp, ignore_errors=True)
                if revision is not None:
                    self.state.discard(revision)
                self._drop_empty_tmp()
                raise
            for folder in (final.parent.parent, final.parent):
                fsync_dir(folder)
            self.state.commit(revision)
            self._drop_empty_tmp()
            self._index(rec)
            return Added(rec, created=True, sources_added=len(sources),
                         renditions_added=len(incoming.renditions))

    def _merge(self, rid: str, sha: str, incoming: Incoming) -> Added:
        with self.locks.hold(rid):
            rec = self._load_fresh(self._rel(rid))
            if rec.media.sha256 != sha:
                raise WriterError(f"{rid} exists but holds different media")
            folder = self.archive.path_for(rid)
            have = {(s.kind, canonical_ref(s.kind, s.ref), s.sha256) for s in rec.sources}
            new_sources = []
            for source in incoming.sources:
                digest = _sha(source.payload) if source.payload is not None else None
                key = (source.kind, canonical_ref(source.kind, source.ref), digest)
                if key not in have:
                    have.add(key)
                    new_sources.append(self._publish_raw(folder, source))
            written = sum(self._write_rendition_locked(folder, r) is not None
                          for r in incoming.renditions)
            self._merge_notes(folder, incoming.my_notes)
            merged = self.mutate(rid, MergeIncoming(tuple(new_sources), tuple(incoming.tags),
                                                    tuple(incoming.excluded_note_types)))
            return Added(merged, created=False, sources_added=len(new_sources),
                         renditions_added=written)

    @staticmethod
    def _merge_notes(folder: Path, notes: str | None) -> None:
        if not notes or not notes.strip():
            return
        path = folder / "my-notes.md"
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current is None:
            write_text_atomic(path, notes)
        elif notes.strip() not in current:
            write_text_atomic(path, current.rstrip("\n") + "\n\n---\n\n" + notes)

    # ---- outputs and raw payloads (write-once) -------------------------------------------------
    def write_rendition(self, recording_id: str, rendition: Rendition) -> str | None:
        """Publish an output beside a recording. None when an identical file is already there."""
        folder = self.archive.path_for(recording_id)
        with self.locks.hold(recording_id):
            if not (folder / "recording.json").is_file():
                raise KeyError(recording_id)
            return self._write_rendition_locked(folder, rendition)

    def _write_rendition_locked(self, folder: Path, rendition: Rendition) -> str | None:
        data = dump_json(rendition).encode("utf-8")
        digest = _sha(data)
        for path in (folder / "renditions").glob("*.json"):
            with contextlib.suppress(OSError):
                if _sha(path.read_bytes()) == digest:
                    return None
        return self._publish_rendition(folder, rendition, data)

    @staticmethod
    def _publish_rendition(folder: Path, rendition: Rendition, data: bytes | None = None) -> str:
        (folder / "renditions").mkdir(exist_ok=True)
        data = data if data is not None else dump_json(rendition).encode("utf-8")
        path = publish_exclusive(data, folder / "renditions" / rendition_filename(rendition))
        return path.relative_to(folder).as_posix()

    @staticmethod
    def _publish_raw(folder: Path, source: RawSource) -> SourceRef:
        raw = digest = None
        if source.payload is not None:
            when = source.fetched_at or source.added_at
            (folder / "source").mkdir(exist_ok=True)
            target = folder / "source" / f"{_slug(source.kind)}-{utc_stamp(when)}.json"
            raw = publish_exclusive(source.payload, target).relative_to(folder).as_posix()
            digest = _sha(source.payload)
        return SourceRef(kind=source.kind, ref=source.ref, added_at=source.added_at,
                         fetched_at=source.fetched_at, raw=raw, sha256=digest)

    # ---- housekeeping ------------------------------------------------------------------------
    def _sweep_tmp(self) -> None:
        """Remove assemblies a killed writer left in .tmp/ (Review Focus 2). Each is named
        `<recording id>.<hex>`, and is removed only while that recording's lock is free."""
        tmp_root = self.root / ".tmp"
        if not tmp_root.is_dir():
            return
        probe = Locks(self.state.locks_dir, timeout=0)
        for entry in sorted(tmp_root.iterdir()):
            rid = entry.name.split(".", 1)[0]
            if not ID_RE.fullmatch(rid):
                continue  # not one of ours; leave it alone
            try:
                with probe.hold(rid):
                    shutil.rmtree(entry, ignore_errors=True)
            except LockTimeout:
                continue  # another writer is assembling it right now
        self._drop_empty_tmp()

    def _drop_empty_tmp(self) -> None:
        with contextlib.suppress(OSError):
            (self.root / ".tmp").rmdir()

    # ---- reindex: accept outside edits, rebuild the index (§6.4, §6.7, §11) -----------------
    def reindex(self, *, allow_empty: bool = False) -> ReindexReport:
        adopted, invalid = [], []
        for folder in self.archive.recording_dirs():
            if not ID_RE.fullmatch(folder.name):
                continue  # reported by iter_recordings as a mismatched folder
            rel = (folder / "recording.json").relative_to(self.root).as_posix()
            with self.locks.hold(folder.name):
                outcome = self._accept(rel, (folder / "recording.json").read_bytes())
            if outcome == "adopted":
                adopted.append(rel)
            elif outcome == "invalid":
                invalid.append(rel)
        count = self.rebuild_index(allow_empty=allow_empty)
        problems = tuple({"path": str(p.path), "message": p.message} for p in self.archive.problems)
        return ReindexReport(recordings=count, adopted=tuple(adopted), invalid=tuple(invalid),
                             problems=problems)

    def _accept(self, rel: str, raw: bytes) -> str:
        sha = _sha(raw)
        pending = self.state.pending(rel)
        if pending is not None:
            if pending.sha256 == sha:
                self.state.commit(pending.id)
                self.state.clear_flags(rel)
                return "unchanged"
            self.state.discard(pending.id)
        last = self.state.last_committed(rel)
        if last is not None and last.sha256 == sha:
            return "unchanged"
        try:
            rec = Recording.model_validate_json(raw)
        except ValidationError as exc:
            self.state.flag(rel, "invalid", str(exc).splitlines()[0])
            return "invalid"
        self.state.adopt(rel, rec.rev or 0, sha, raw)
        self.state.clear_flags(rel)
        return "adopted"
```

Replace `packages/core/src/recordings/archive.py` with:
```python
"""Reading the archive (spec §6). The archive is the source of truth.

Reading only. Every write goes through `recordings.writer.Writer`, which holds the locks and the
merge bases (§6.4).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from recordings.ids import relative_dir
from recordings.models import Recording, Rendition

_CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def utc_stamp(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class RawSource:
    kind: str
    ref: str
    added_at: datetime
    payload: bytes | None = None  # written verbatim as source/<kind>-<stamp>.json
    fetched_at: datetime | None = None  # when the source returned it; names the file

    def __post_init__(self) -> None:
        for name in ("added_at", "fetched_at"):
            value = getattr(self, name)
            if value is not None and value.utcoffset() is None:
                raise ValueError(f"RawSource.{name} must be timezone-aware")


@dataclass(frozen=True)
class Problem:
    path: Path
    message: str


@dataclass
class Archive:
    root: Path
    problems: list[Problem] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    def recording_dirs(self) -> Iterator[Path]:
        base = self.root / "recordings"
        if not base.is_dir():
            return
        for folder in sorted(base.glob("*/*/*")):
            if (folder / "recording.json").is_file():
                yield folder

    def iter_recordings(self) -> Iterator[Recording]:
        """Every readable recording. A broken file is recorded in .problems, not raised (§11).

        So is a recording whose id doesn't match its folder (a typo, or a copied folder):
        listing it would show a recording that load() can't find, or the same one twice.
        """
        self.problems = []
        for folder in self.recording_dirs():
            path = folder / "recording.json"
            try:
                rec = Recording.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, UnicodeDecodeError, OSError) as exc:
                self.problems.append(Problem(path=path, message=str(exc).splitlines()[0]))
                continue
            try:
                expected = relative_dir(rec.id).as_posix()
            except ValueError:  # an id of the right shape with an impossible date
                expected = None
            if expected != folder.relative_to(self.root).as_posix():
                message = f"id {rec.id!r} does not match its folder"
                self.problems.append(Problem(path=path, message=message))
                continue
            yield rec

    def path_for(self, rid: str) -> Path:
        return self.root / relative_dir(rid)

    def load(self, rid: str) -> Recording:
        path = self.path_for(rid) / "recording.json"
        if not path.is_file():
            raise KeyError(rid)
        return Recording.model_validate_json(path.read_text(encoding="utf-8"))

    def media_path(self, rid: str) -> Path:
        return self.path_for(rid) / self.load(rid).media.file

    def renditions(self, rid: str, problems: list[Problem] | None = None
                   ) -> list[tuple[str, Rendition]]:
        folder = self.path_for(rid)
        out = []
        for path in sorted((folder / "renditions").glob("*.json")):
            try:
                rendition = Rendition.model_validate_json(path.read_text(encoding="utf-8"))
                out.append((path.relative_to(folder).as_posix(), rendition))
            except (ValidationError, UnicodeDecodeError, OSError) as exc:
                if problems is not None:
                    problems.append(Problem(path=path, message=str(exc).splitlines()[0]))
                else:
                    raise
        return sorted(out, key=lambda item: (item[1].created_at, item[0]))

    def read_my_notes(self, rid: str) -> str | None:
        path = self.path_for(rid) / "my-notes.md"
        return path.read_text(encoding="utf-8") if path.is_file() else None
```

Add the `reindex` command to `packages/core/src/recordings/cli.py`:
1. **Add** the imports:
   ```python
   from recordings.index import IndexRefused
   from recordings.locks import LockTimeout
   from recordings.writer import Writer, WriterError
   ```
2. **Add** to `build_parser`:
   ```python
       p = sub.add_parser("reindex", help="accept outside edits and rebuild the derived index.db")
       p.add_argument("--allow-empty", action="store_true",
                      help="let the index become empty (refused by default: is the archive mounted?)")
       p.add_argument("--json", action="store_true")
   ```
3. **Add** after `cmd_init`:
   ```python
   def _writer(as_json: bool) -> Writer | int:
       cfg = _config(as_json)
       if isinstance(cfg, int):
           return cfg
       try:
           return Writer.open(cfg)
       except (WriterError, LockTimeout) as exc:
           return _fail(str(exc), as_json, 78)


   def cmd_reindex(args: argparse.Namespace) -> int:
       writer = _writer(args.json)
       if isinstance(writer, int):
           return writer
       try:
           report = writer.reindex(allow_empty=args.allow_empty)
       except (IndexRefused, WriterError, LockTimeout) as exc:
           return _fail(str(exc), args.json, 1)
       _emit(report.to_dict(), args.json)
       return 1 if report.invalid else 0
   ```
4. **Add** `"reindex": cmd_reindex,` to `commands`.

In `packages/core/src/recordings/format/AGENTS.md`, **replace** the rule that begins
`**After a bulk edit, run \`recordings reindex\`**` with:
```markdown
**After editing any file, run `recordings reindex`** on the homelab server. It accepts your edits
as the base for the app's next write. Until then, the app refuses to change a file you edited,
rather than overwrite your edit. Jobs that the edit would start wait for approval in the app.
```
In `format/FORMAT.md`, **replace** the "Work in progress" bullet with:
```markdown
- **Work in progress:** writers assemble a new recording in `.tmp/<id>.<hex>/` at the archive root,
  and write temporary files named `.*.tmp`. Readers and mirrors skip both.
```

- [ ] **Step 9: Move the callers onto the `Writer`**

Replace `packages/core/tests/test_archive.py` with:
```python
import json
import shutil
from datetime import datetime, timezone

import pytest

from recordings.archive import Archive, RawSource
from recordings.models import Rendition

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def test_a_broken_recording_json_is_reported_not_fatal(writer, make_incoming):
    good = writer.add(make_incoming()).recording
    bad = writer.add(make_incoming(recorded_at=datetime.fromisoformat("2026-10-07T09:00:00-07:00"))).recording
    (writer.archive.path_for(bad.id) / "recording.json").write_text("{ truncated", encoding="utf-8")
    archive = Archive(writer.root)
    assert [r.id for r in archive.iter_recordings()] == [good.id]
    (problem,) = archive.problems
    assert problem.path.name == "recording.json" and bad.id in str(problem.path)


def test_load_unknown_id_raises_keyerror(tmp_path):
    with pytest.raises(KeyError):
        Archive(tmp_path).load("20261006T140003-0700_00000000")


def test_media_path_points_at_the_media_file(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    assert Archive(writer.root).media_path(rec.id).name == f"{rec.id}.mp3"


def test_renditions_skips_corrupt_files_when_problems_given(writer, make_incoming):
    rec = writer.add(make_incoming(renditions=(Rendition(
        kind="notes", note_type="lecture", engine="canned", model="m", version="m@1",
        created_at=T0, payload={"markdown": "# Notes"}),))).recording
    corrupt = writer.archive.path_for(rec.id) / "renditions" / "corrupt.json"
    corrupt.write_text("{", encoding="utf-8")
    problems = []
    assert len(Archive(writer.root).renditions(rec.id, problems)) == 1
    (problem,) = problems
    assert problem.path == corrupt and problem.message


def _set_id(folder, rid: str) -> None:
    path = folder / "recording.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["id"] = rid
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("wrong", [
    lambda rid: rid[:-1] + ("1" if rid[-1] == "0" else "0"),  # a typo in the hash
    lambda rid: rid.replace("T140003", "T140004"),  # a typo in the time
    lambda rid: "20261399T256199+0000_deadbeef",  # the right shape, but no such date
], ids=["hash", "time", "impossible-date"])
def test_a_recording_whose_id_does_not_match_its_folder_is_reported(writer, make_incoming, wrong):
    good = writer.add(make_incoming()).recording
    bad = writer.add(make_incoming(recorded_at=datetime.fromisoformat("2026-10-07T14:00:03-07:00"))).recording
    typo = wrong(bad.id)
    _set_id(writer.archive.path_for(bad.id), typo)
    archive = Archive(writer.root)
    assert [r.id for r in archive.iter_recordings()] == [good.id]
    (problem,) = archive.problems
    assert problem.path == archive.path_for(bad.id) / "recording.json"
    assert problem.message == f"id {typo!r} does not match its folder"


def test_a_copied_recording_folder_is_reported_and_the_original_kept(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    original = writer.archive.path_for(rec.id)
    copy = original.with_name(f"{rec.id} copy")  # what Finder calls a duplicate
    shutil.copytree(original, copy)
    archive = Archive(writer.root)
    assert [r.id for r in archive.iter_recordings()] == [rec.id]
    (problem,) = archive.problems
    assert problem.path == copy / "recording.json"
    assert problem.message == f"id {rec.id!r} does not match its folder"


def test_a_raw_source_needs_aware_times():
    # why: stage-1 carry-over. A naive time would be read as UTC somewhere and as local elsewhere.
    with pytest.raises(ValueError, match="timezone-aware"):
        RawSource(kind="plaud", ref="x", added_at=datetime(2026, 10, 8, 12, 0))
    with pytest.raises(ValueError, match="timezone-aware"):
        RawSource(kind="plaud", ref="x", added_at=T0, fetched_at=datetime(2026, 8, 1))
```
(Task 3's `test_a_raw_source_records_its_hash_and_fetch_time` is now covered by
`test_writer.py`'s `test_add_lays_out_the_folder_and_starts_at_rev_1` and
`test_a_new_snapshot_of_the_same_source_is_kept_beside_the_first`.)

In `packages/core/tests/test_selfdoc.py`, **replace** `build_one` and its imports of
`Archive`/`RawSource` with:
```python
from datetime import datetime, timezone

from recordings.archive import RawSource
from recordings.index import Index
from recordings.init import init_archive
from recordings.models import Rendition
from recordings.selfdoc import layout_patterns, validate, write_docs
from recordings.state import State
from recordings.writer import Incoming, Writer

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def build_one(tmp_path):
    media = tmp_path / "clip.mp3"
    media.write_bytes(b"ID3 fake")
    root, state = tmp_path / "archive", tmp_path / "state"
    init_archive(root, state, writer_id="test")
    writer = Writer(root, State.open(state), Index(tmp_path / "index.db"), writer_id="test")
    rec = writer.add(Incoming(
        media=media,
        recorded_at=datetime.fromisoformat("2026-10-06T14:00:03-07:00"),
        timezone_name="America/Vancouver",
        time_source="plaud",
        title="Week 4",
        kind="audio",
        sources=(RawSource(kind="plaud", ref="of_x", added_at=T0, payload=b"{}"),),
        my_notes="mine\n",
        renditions=(
            Rendition(kind="transcript", engine="canned", model="whisper-large-v3-turbo",
                      version="large-v3-turbo@a4aaeec", created_at=T0,
                      payload={"segments": [{"start": 0, "end": 1, "text": "hi"}]}),
            Rendition(kind="notes", note_type="lecture", engine="canned", model="m",
                      version="m@demo", created_at=T0, payload={"markdown": "x"}),
        ),
    )).recording
    write_docs(writer.root)
    return writer.archive, rec
```

In `packages/ui/tests/conftest.py`, **add**:
```python
from recordings.index import Index
from recordings.init import init_archive
from recordings.state import State
from recordings.writer import Writer


@pytest.fixture
def writer(tmp_path) -> Writer:
    root, state = tmp_path / "archive", tmp_path / "state"
    init_archive(root, state, writer_id="test")
    return Writer(root, State.open(state), Index(tmp_path / "index.db"), writer_id="test")
```

In `packages/ui/tests/test_views.py`, **add** `from recordings.writer import Incoming` to the
imports, and **replace** the four tests that call `archive.add_recording` with:
```python
def test_a_recording_with_no_outputs_has_empty_tabs(tmp_path, writer):
    media = tmp_path / "x.mp3"
    media.write_bytes(b"ID3")
    rec = writer.add(Incoming(
        media=media, recorded_at=datetime(2026, 10, 1, 9, tzinfo=timezone.utc),
        timezone_name="UTC", time_source="ingest", title="Fresh upload", kind="audio",
        sources=(RawSource(kind="upload", ref="x.mp3",
                           added_at=datetime(2026, 10, 1, tzinfo=timezone.utc)),))).recording
    view = recording_view(writer.archive, rec.id)
    assert view["transcripts"] == [] and view["chosen_transcript"] is None
    assert view["notes"] == [] and view["plaud_notes"] == [] and view["my_notes_html"] is None


def test_model_written_html_is_escaped(tmp_path, writer):
    assert "<script>" not in render_markdown("hi <script>alert(1)</script>")
    assert "&lt;script&gt;" in render_markdown("hi <script>alert(1)</script>")
    media = tmp_path / "x.mp3"
    media.write_bytes(b"ID3")
    t = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rec = writer.add(Incoming(
        media=media, recorded_at=t, timezone_name="UTC", time_source="ingest", title="x",
        kind="audio", sources=(RawSource(kind="upload", ref="x", added_at=t),),
        renditions=(Rendition(kind="notes", note_type="lecture", engine="canned", model="m",
                              version="m@1", created_at=t,
                              payload={"markdown": "<img src=x onerror=alert(1)>"}),))).recording
    html = recording_view(writer.archive, rec.id)["notes"][0]["outputs"][0]["html"]
    assert "<img" not in html


def test_a_capitalised_private_tag_is_private_everywhere(tmp_path, writer):
    from recordings.models import TagRef

    media = tmp_path / "x.mp3"
    media.write_bytes(b"ID3")
    t = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rec = writer.add(Incoming(
        media=media, recorded_at=t, timezone_name="UTC", time_source="ingest", title="x",
        kind="audio", sources=(RawSource(kind="upload", ref="x", added_at=t),),
        tags=(TagRef(tag="Private/Health"), TagRef(tag="notes/private")))).recording
    view = library_view(writer.archive)
    assert {t["tag"]: t["private"] for t in view["tags"]} == {"Private/Health": True}
    assert view["recordings"][0]["private"] is True
    assert recording_view(writer.archive, rec.id)["private"] is True


def test_a_turn_shows_plauds_name_for_its_speaker(tmp_path, writer):
    media = tmp_path / "x.mp3"
    media.write_bytes(b"ID3")
    t = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rec = writer.add(Incoming(
        media=media, recorded_at=t, timezone_name="UTC", time_source="plaud", title="x",
        kind="audio", sources=(RawSource(kind="plaud", ref="x", added_at=t),),
        renditions=(Rendition(kind="transcript", engine="plaud", model="plaud", version="plaud@1",
                              created_at=t, payload={"segments": [
                                  {"start": 0, "end": 1, "speaker": "Speaker 1",
                                   "speaker_name": "Ada", "text": "Hello."},
                                  {"start": 1, "end": 2, "speaker": "Speaker 2",
                                   "text": "Hi."}]}),))).recording
    turns = recording_view(writer.archive, rec.id)["transcripts"][0]["turns"]
    assert [t["speaker"] for t in turns] == ["Ada", "Speaker 2"]
```

In `packages/ui/src/recordings_ui/settings.py`, **add** `from recordings.config import Config` to
the imports (beside `ConfigError, load_config`), **add** the field
`config: Config | None = None  # None in demo mode, which never reads config.toml` to `Settings`,
and **replace** the last line of `from_env` with:
```python
    return Settings(archive=cfg.archive_path, demo=False, allowed_hosts=hosts, config=cfg)
```

In `packages/ui/src/recordings_ui/app.py`:
1. **Add** the imports `import logging`, `import sqlite3`, `from recordings.config import Config`,
   `from recordings.locks import LockTimeout` and `from recordings.writer import Writer, WriterError`,
   and `log = logging.getLogger(__name__)`.
2. **Add** before `create_app`:
   ```python
   def write_docs_at_startup(cfg: Config) -> None:
       """§6.7: write the archive's docs at startup, but only into an archive this process may
       write. Anywhere else (no sentinel, not the writer, an index it can't open) the app still
       serves, read-only. sqlite3's errors are not OSErrors, so they are named here."""
       try:
           Writer.open(cfg)
       except (WriterError, LockTimeout, OSError, sqlite3.Error) as exc:
           log.warning("not writing the archive's docs: %s", exc)
   ```
3. **Add** as the first line of `create_app`'s body:
   ```python
       if settings.config is not None:
           write_docs_at_startup(settings.config)
   ```

Add to `packages/ui/tests/test_app.py`:
```python
def test_the_docs_are_written_at_startup_only_into_an_archive_it_may_write(tmp_path, caplog):
    from recordings.config import load_config
    from recordings.init import init_archive

    init_archive(tmp_path / "archive", tmp_path / "state", writer_id="test")
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[archive]\npath = "{tmp_path / "archive"}"\nwriter_id = "test"\n'
        f'[state]\npath = "{tmp_path / "state"}"\n[index]\npath = "{tmp_path / "index.db"}"\n',
        encoding="utf-8")
    cfg = load_config({"RECORDINGS_CONFIG": str(cfg_file)})
    (tmp_path / "archive" / "FORMAT.md").unlink()
    create_app(Settings(archive=cfg.archive_path, demo=False, config=cfg))
    assert (tmp_path / "archive" / "FORMAT.md").is_file()
    runtime.configure(None)

    bare = tmp_path / "bare"
    (bare / "recordings").mkdir(parents=True)
    other = load_config({"RECORDINGS_CONFIG": str(cfg_file), "RECORDINGS_ARCHIVE": str(bare)})
    with caplog.at_level("WARNING"):
        create_app(Settings(archive=bare, demo=False, config=other))
    assert not (bare / "FORMAT.md").exists() and "not writing the archive's docs" in caplog.text
    runtime.configure(None)
```

Replace `build` in `demo/build.py` with (and **add** the imports
`from recordings.index import Index`, `from recordings.state import State` and
`from recordings.writer import Incoming, Writer`; **remove** the `Archive` import):
```python
def build(media_dir: Path, out: Path, canned_dir: Path) -> dict[str, str]:
    entries = tomllib.loads((HERE / "sources.toml").read_text())["recording"]
    ids: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="recordings-demo-state-") as tmp:
        state = Path(tmp) / "state"
        init_archive(out, state, writer_id="demo", archive_uuid=DEMO_UUID, created_at=BUILD_AT)
        writer = Writer(out, State.open(state), Index(Path(tmp) / "index.db"), writer_id="demo")
        for n, entry in enumerate(entries):
            media = Path(media_dir) / f"{entry['slug']}.{entry['ext']}"
            recorded_at = datetime.fromisoformat(entry["recorded_at"]).replace(
                tzinfo=ZoneInfo(entry["timezone"]))
            sha = sha256_file(media)
            rid = make_id(recorded_at, sha)
            ids[rid] = entry["slug"]
            backend = CannedBackend(canned_dir, {rid: entry["slug"]})
            stamp = BUILD_AT + timedelta(minutes=n)
            renditions = [Rendition(
                kind="transcript", engine="canned", model=WHISPER["model"],
                version=WHISPER["version"], created_at=stamp,
                inputs={"audio_sha256": sha}, meta={"demo": True},
                payload=backend.transcribe(media, recording_id=rid).model_dump(exclude_none=True),
            )]
            for i, note in enumerate(entry.get("notes", []), start=1):
                done = backend.complete(CompletionRequest(
                    prompt="(canned)", model=note["model"], recording_id=rid,
                    note_type=note["note_type"]))
                renditions.append(Rendition(
                    kind="notes", note_type=note["note_type"], engine="canned", model=done.model,
                    version=f"{done.model}@demo", created_at=stamp + timedelta(seconds=i),
                    inputs={"audio_sha256": sha}, meta={"demo": True},
                    payload={"markdown": done.text}))
            payload = None
            if entry.get("plaud"):
                transcript = renditions[0].payload
                renditions.append(Rendition(
                    kind="transcript", engine="plaud", model="plaud", version="plaud@demo",
                    created_at=stamp + timedelta(seconds=30), meta={"demo": True},
                    payload=transcript))
                renditions.append(Rendition(
                    kind="notes", note_type="plaud-summary", engine="plaud", model="plaud",
                    version="plaud@demo", created_at=stamp + timedelta(seconds=31),
                    meta={"demo": True},
                    payload={"markdown": (canned_dir / "notes" / entry["slug"] / "plaud.md")
                             .read_text(encoding="utf-8")}))
                payload = json.dumps({"id": entry["source_ref"], "demo": True,
                                      "name": entry["title"]}, indent=2).encode() + b"\n"
            my_notes_path = canned_dir / "my-notes" / f"{entry['slug']}.md"
            writer.add(Incoming(
                media=media,
                recorded_at=recorded_at,
                timezone_name=entry["timezone"],
                time_source=entry["time_source"],
                title=entry["title"],
                kind=entry["kind"],
                duration_ms=entry["duration_ms"],
                sources=(RawSource(kind=entry["source_kind"], ref=entry["source_ref"],
                                   added_at=BUILD_AT, payload=payload),),
                tags=tuple(TagRef(**t) for t in entry.get("tags", [])),
                renditions=tuple(renditions),
                my_notes=(my_notes_path.read_text(encoding="utf-8")
                          if my_notes_path.is_file() else None),
            ))
    return dict(sorted(ids.items()))
```

- [ ] **Step 10: Rebuild the demo and run every test**

Run:
```bash
uv run python demo/build.py --media-dir demo/.cache/media --out demo/archive --force
uv run pytest
```
Expected: every test passes, `test_writer.py` and `test_locks.py` included. The demo's
`recording.json` files now carry `"rev": 1`.

- [ ] **Step 11: Commit**

```bash
git add packages/core/src/recordings packages/core/tests packages/ui/src/recordings_ui packages/ui/tests demo/build.py demo/archive
git commit -m "feat(core): the locked write path: Writer, mutate, merge bases and recordings reindex

Every write takes flock locks in a fixed order; mutate bumps rev, refuses a file edited outside
the app (content hash against the merge base in state.db) and fsyncs. Stage 1's add_recording,
write_rendition and duplicate merge move onto it; the merge keeps the second copy's outputs, tags
and notes. Checked: docs.python.org fcntl (flock, LOCK_NB errno) and sqlite3.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 6: The Plaud normaliser

**Checkpoint lens:** data integrity and write safety.

A pure, versioned function (§9.1.1). The import uses it now, and stage 2b's sync will use it
unchanged, so both agree on what changed. The payload's shape is modelled on `audio-router`'s
`archive/sources/plaud.py` (the envelope from `files/{id}`), `plaud_transcript.py` (segment rows)
and `artifacts.py` (the canonical hash). Read them, never the archive itself.

**Files:**
- Create: `packages/core/src/recordings/plaud/__init__.py`, `packages/core/src/recordings/plaud/normalise.py`
- Modify: `packages/core/tests/conftest.py` (the `plaud_env` fixture)
- Test: `packages/core/tests/test_normalise.py`

**Interfaces:**
- Consumes: `recordings.refs.canonical_plaud_id` (Task 4).
- Produces, in `recordings.plaud.normalise`:
  - **Constants:**
    - `NORMALISER_VERSION = 1`
    - `DROPPED_KEYS`
    - `SEGMENT_TYPES`, `OWN_NOTE_TYPES`
    - `GENERIC_SPEAKER: re.Pattern`
    - `PRESIGNED = "<presigned>"`
  - **Hashing:**
    - `canonical_json(obj) -> str`
    - `sha256_of(obj) -> str`
  - **The normaliser:**
    - `normalise(envelope: dict) -> dict`
    - `envelope_sha256(envelope: dict) -> str`
  - **Completeness:**
    - `link_errors(envelope: dict) -> list[str]`
    - `is_complete(envelope: dict) -> bool`
  - **Parts of the payload:**
    - `transcript_rows(envelope: dict) -> list[dict] | None`
    - `note_type_base(block: dict) -> str`
    - `note_blocks(envelope: dict) -> list[tuple[str, dict]]`
    - `diarization_fingerprint(envelope: dict) -> str | None`
    - `part_hashes(envelope: dict) -> dict[str, str]`
  - **Test fixture** `plaud_env`: a factory `(fid=HEX, *, name="Week 4", segments=None, notes=None, outline=True, duration=2600, link_error=False, presigned="abc") -> dict`. Also the module constant `HEX` in `conftest.py`.

- [ ] **Step 1: Write the failing tests**

Add to `packages/core/tests/conftest.py` (with `import json` at the top):
```python
HEX = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def plaud_env():
    """A synthetic Plaud `files/{id}` envelope, shaped like audio-router's raw snapshots: invented
    names, invented text, a rotating presigned URL and audio-router's own `_meta`."""
    def make(fid: str = HEX, *, name: str = "Week 4", segments=None, notes=None,
             outline: bool = True, duration: int = 2600, link_error: bool = False,
             presigned: str = "abc") -> dict:
        rows = segments if segments is not None else [
            {"content": "Hello there.", "start_time": 0, "end_time": 1500, "speaker": "Ada",
             "original_speaker": "Speaker 1", "embeddingKey": "ek-1"},
            {"content": "Hi.", "start_time": 1500, "end_time": 2600, "speaker": "Speaker 2",
             "original_speaker": "Speaker 2"},
        ]
        source_list = [{"data_type": "transaction", "data_id": "t-1", "data_tab_name": None,
                        "data_content": json.dumps(rows, ensure_ascii=False)}]
        if outline:
            source_list.append({"data_type": "outline", "data_id": "o-1", "data_tab_name": None,
                                "data_content": json.dumps(
                                    [{"topic": "Greetings", "start_time": 0, "end_time": 2600}])})
        note_list = notes if notes is not None else [
            {"data_type": "auto_sum_note", "data_id": "n-1", "data_tab_name": "Summary",
             "data_content": "# Summary\n\nTwo people say hello."},
            {"data_type": "high_light", "data_id": "h-1", "data_tab_name": None,
             "data_content": json.dumps([{"timestamp": 1000, "title": "Key moment",
                                          "content": "A highlight"}])},
        ]
        if link_error:
            note_list = [*note_list, {
                "data_type": "auto_sum_note", "data_id": "n-2", "data_tab_name": "Action items",
                "data_content": "", "data_link": "https://notes.example/n2?X-Amz-Expires=300",
                "_data_link_error": "HTTP Error 403: Forbidden"}]
        return {
            "id": fid, "name": name, "duration": duration,
            "start_at": "2026-08-01T16:00:00Z", "created_at": "2026-08-01T16:05:00Z",
            "serial_number": "TEST0001",
            "presigned_url": f"https://audio.example/x.mp3?X-Amz-Expires=300&X-Amz-Signature={presigned}",
            "source_list": source_list, "note_list": note_list,
            "_meta": {"tool": "audio-router", "enriched_from_links": []},
        }
    return make
```

`packages/core/tests/test_normalise.py`:
```python
import copy
import json

import pytest

from recordings.plaud.normalise import (
    DROPPED_KEYS,
    diarization_fingerprint,
    envelope_sha256,
    is_complete,
    link_errors,
    normalise,
    note_blocks,
    part_hashes,
    transcript_rows,
)

HEX = "0123456789abcdef0123456789abcdef"


def test_a_rotating_presigned_url_is_not_a_change(plaud_env):
    assert envelope_sha256(plaud_env(presigned="one")) == envelope_sha256(plaud_env(presigned="two"))


def test_only_the_query_part_of_a_url_inside_text_is_replaced(plaud_env):
    # why: audio-router once replaced the whole string, which erased a note from the hash.
    text = "See https://notes.example/a.png?X-Amz-Signature=1 and then decide."
    env = plaud_env(notes=[{"data_type": "auto_sum_note", "data_id": "n", "data_tab_name": "S",
                            "data_content": text}])
    (block,) = normalise(env)["note_list"]
    assert block["data_content"] == "See https://notes.example/a.png?<presigned> and then decide."
    edited = copy.deepcopy(env)
    edited["note_list"][0]["data_content"] = text.replace("decide", "agree")
    assert envelope_sha256(edited) != envelope_sha256(env)


def test_the_fetchers_own_fields_are_dropped(plaud_env):
    env = plaud_env()
    noisy = copy.deepcopy(env)
    noisy["_meta"] = {"tool": "something else", "enriched_from_links": ["transaction"]}
    noisy["source_list"][0]["_fetched_from_data_link"] = True
    noisy["source_list"][0]["data_link"] = "https://x.example/y?X-Amz-Expires=300"
    assert envelope_sha256(noisy) == envelope_sha256(env)


def test_both_id_forms_are_one_id(plaud_env):
    assert envelope_sha256(plaud_env("of_" + HEX)) == envelope_sha256(plaud_env(HEX))


def test_reordered_blocks_are_not_a_change_but_reordered_segments_are(plaud_env):
    env = plaud_env()
    blocks = copy.deepcopy(env)
    blocks["note_list"].reverse()
    blocks["source_list"].reverse()
    assert envelope_sha256(blocks) == envelope_sha256(env)
    rows = json.loads(env["source_list"][0]["data_content"])
    swapped = plaud_env(segments=[rows[1], rows[0]])
    assert envelope_sha256(swapped) != envelope_sha256(env)


def test_the_transcripts_inner_json_is_compared_canonically(plaud_env):
    env = plaud_env()
    rows = json.loads(env["source_list"][0]["data_content"])
    spaced = copy.deepcopy(env)
    spaced["source_list"][0]["data_content"] = json.dumps(
        [dict(reversed(list(r.items()))) for r in rows], indent=4)
    assert envelope_sha256(spaced) == envelope_sha256(env)


def test_an_unknown_new_field_is_a_change(plaud_env):
    env = plaud_env()
    assert envelope_sha256({**env, "brand_new_field": 1}) != envelope_sha256(env)


def _leaves(obj, path=()):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key not in DROPPED_KEYS:
                yield from _leaves(value, (*path, key))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from _leaves(value, (*path, i))
    else:
        yield path, obj


def _changed(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if isinstance(value, str):
        return value + "x"
    return "x"


def test_changing_any_field_off_the_drop_list_changes_the_hash(plaud_env):
    # why: §9.1.1's property test. A normaliser that drops too much silently loses edits.
    env = plaud_env()
    base = envelope_sha256(env)
    leaves = list(_leaves(env))
    assert len(leaves) > 20
    for path, value in leaves:
        edited = copy.deepcopy(env)
        target = edited
        for step in path[:-1]:
            target = target[step]
        target[path[-1]] = _changed(value)
        assert envelope_sha256(edited) != base, path


def test_normalising_twice_changes_nothing(plaud_env):
    once = normalise(plaud_env(link_error=True))
    assert normalise(once) == once


def test_a_snapshot_with_link_errors_is_incomplete(plaud_env):
    assert is_complete(plaud_env())
    env = plaud_env(link_error=True)
    assert not is_complete(env) and link_errors(env) == ["auto_sum_note"]
    unfetched = plaud_env()
    unfetched["note_list"][0] = {**unfetched["note_list"][0], "data_content": "",
                                 "data_link": "https://x.example/y?X-Amz-Expires=300"}
    assert not is_complete(unfetched)


def test_transcript_rows_and_their_absence(plaud_env):
    assert [r["content"] for r in transcript_rows(plaud_env())] == ["Hello there.", "Hi."]
    env = plaud_env()
    env["source_list"][0]["data_content"] = "not json"
    assert transcript_rows(env) is None
    env["source_list"] = []
    assert transcript_rows(env) is None


def test_note_types_are_plaud_and_the_tab_ascii_folded_and_numbered(plaud_env):
    # Review Focus 4: a tab name in another script, or with punctuation, gives a safe note type.
    env = plaud_env(notes=[
        {"data_type": "auto_sum_note", "data_id": "a", "data_tab_name": "Summary", "data_content": "x"},
        {"data_type": "auto_sum_note", "data_id": "b", "data_tab_name": "Summary", "data_content": "y"},
        {"data_type": "auto_sum_note", "data_id": "c", "data_tab_name": "会議メモ / Q&A", "data_content": "z"},
        {"data_type": "consumer_note", "data_id": "d", "data_tab_name": "会議", "data_content": "w"},
        {"data_type": "high_light", "data_id": "e", "data_tab_name": None, "data_content": "[]"},
    ])
    types = [t for t, _ in note_blocks(env)]
    # The outline first, then note_list sorted by (type, tab, id): "Summary" sorts before "会議…".
    assert types == ["plaud-outline", "plaud-summary", "plaud-summary-2", "plaud-q-a",
                     "plaud-consumer-note", "plaud-high-light"]
    assert all(t.isascii() and " " not in t and "/" not in t for t in types)


def test_the_diarization_fingerprint_ignores_names_but_not_timing(plaud_env):
    env = plaud_env()
    rows = json.loads(env["source_list"][0]["data_content"])
    renamed = plaud_env(segments=[{**rows[0], "speaker": "Grace"}, rows[1]])
    resegmented = plaud_env(segments=[{**rows[0], "end_time": 1400}, rows[1]])
    assert diarization_fingerprint(renamed) == diarization_fingerprint(env)
    assert diarization_fingerprint(resegmented) != diarization_fingerprint(env)


def test_part_hashes_say_what_changed(plaud_env):
    env = plaud_env()
    base = part_hashes(env)
    assert set(base) == {"title", "transcript", "speakers", "notes:plaud-outline",
                         "notes:plaud-summary", "notes:plaud-high-light", "other"}
    rows = json.loads(env["source_list"][0]["data_content"])

    def changed(other):
        new = part_hashes(other)
        return {k for k in base if base[k] != new.get(k)}

    assert changed(plaud_env(name="Week 5")) == {"title"}
    assert changed(plaud_env(segments=[{**rows[0], "speaker": "Grace"}, rows[1]])) == {"speakers"}
    assert changed(plaud_env(segments=[{**rows[0], "content": "Hello!"}, rows[1]])) == {"transcript"}
    edited = copy.deepcopy(env)
    edited["note_list"][0]["data_content"] += " Then they leave."
    assert changed(edited) == {"notes:plaud-summary"}
    assert changed({**env, "serial_number": "TEST0002"}) == {"other"}
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_normalise.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings.plaud'`.

- [ ] **Step 3: Write the normaliser**

`packages/core/src/recordings/plaud/__init__.py`:
```python
"""Plaud's payloads (spec §9.1, §9.1.1): the normaliser and reconcile, as pure functions.

Stage 2a uses them to import audio-router's snapshots; stage 2b's sync feeds them Plaud's own. No
module here makes a network call.
"""
```

`packages/core/src/recordings/plaud/normalise.py`:
```python
"""Plaud's payloads, normalised before hashing (spec §9.1.1). A pure, versioned function.

The import (stage 2a) and the sync (stage 2b) both go through it, so they agree on what changed.
A rule change bumps NORMALISER_VERSION. Both sides are then recomputed at compare time, so no
stored hash goes stale; the version is part of every Plaud output's `version`.

The rules:
- Dropped: the presigned audio URL, `data_link`, `_meta`, and the fetcher's own fields
  (`_fetched_from_data_link`, `_data_link_error`).
- Inside strings, only the X-Amz query part of a URL is replaced, never the whole string.
  audio-router once blanked a whole note this way, and lost every later edit to it.
- `source_list` and `note_list` are sorted by (type, tab, id). The segment arrays inside them are
  never reordered. A transcript's inner JSON is re-serialised canonically.
- `of_<hex>` and the bare hex are one ID.
- Everything else, unknown new fields included, is kept, so it counts as a change.

The envelope's shape is audio-router's `files/{id}` snapshot (archive/sources/plaud.py and
plaud_transcript.py at commit 64612df).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from recordings.refs import canonical_plaud_id

NORMALISER_VERSION = 1
DROPPED_KEYS = frozenset(
    {"presigned_url", "data_link", "_meta", "_fetched_from_data_link", "_data_link_error"})
SEGMENT_TYPES = frozenset({"transaction", "transaction_polish"})
OWN_NOTE_TYPES = frozenset({"high_light", "consumer_note", "mark_memo"})  # Dan's own notes
# What Plaud emits before it knows a person: "Speaker 1", "spk_2", bare numbers, "unknown".
GENERIC_SPEAKER = re.compile(r"(?i)^\s*(speaker[\s_-]*\d+|spk[\s_-]*\d+|unknown|\d+)\s*$")
PRESIGNED = "<presigned>"
_PRESIGNED_QUERY = re.compile(r"(https?://[^\s?#\"']+)\?[^\s#\"']*X-Amz-[^\s#\"']*", re.I)


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _scrub(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items() if k not in DROPPED_KEYS}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    if isinstance(obj, str) and "x-amz-" in obj.lower():
        return _PRESIGNED_QUERY.sub(lambda m: f"{m.group(1)}?{PRESIGNED}", obj)
    return obj


def _block_key(block: Any) -> tuple[str, str, str, str]:
    if not isinstance(block, dict):
        return ("", "", "", canonical_json(block))
    return (str(block.get("data_type") or ""), str(block.get("data_tab_name") or ""),
            str(block.get("data_id") or ""), canonical_json(block))


def _canonical_block(block: Any) -> Any:
    if (isinstance(block, dict) and block.get("data_type") in SEGMENT_TYPES
            and isinstance(block.get("data_content"), str)):
        try:
            inner = json.loads(block["data_content"])
        except json.JSONDecodeError:
            return block
        return {**block, "data_content": canonical_json(inner)}
    return block


def normalise(envelope: dict) -> dict:
    env = _scrub(envelope)
    if isinstance(env.get("id"), str):
        env["id"] = canonical_plaud_id(env["id"])
    for section in ("source_list", "note_list"):
        blocks = env.get(section)
        if isinstance(blocks, list):
            env[section] = sorted((_canonical_block(b) for b in blocks), key=_block_key)
    return env


def envelope_sha256(envelope: dict) -> str:
    return sha256_of(normalise(envelope))


def link_errors(envelope: dict) -> list[str]:
    """The data types of blocks whose content didn't arrive: a link error, or a link never
    followed. A snapshot with any is never a baseline, and nothing is built from it (§9.1.1)."""
    out = []
    for section in ("source_list", "note_list"):
        for block in envelope.get(section) or []:
            if not isinstance(block, dict):
                continue
            missing = block.get("data_link") and not str(block.get("data_content") or "").strip()
            if block.get("_data_link_error") or missing:
                out.append(str(block.get("data_type") or "?"))
    return out


def is_complete(envelope: dict) -> bool:
    return not link_errors(envelope)


def transcript_rows(envelope: dict) -> list[dict] | None:
    """The `transaction` block's segment rows, or None when there is none or it isn't JSON."""
    for block in envelope.get("source_list") or []:
        if isinstance(block, dict) and block.get("data_type") == "transaction":
            content = block.get("data_content")
            if not isinstance(content, str) or not content.strip():
                return None
            try:
                rows = json.loads(content)
            except json.JSONDecodeError:
                return None
            ok = isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows)
            return rows if ok else None
    return None


def _ascii_slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")


def note_type_base(block: dict) -> str:
    return (_ascii_slug(str(block.get("data_tab_name") or ""))
            or _ascii_slug(str(block.get("data_type") or "")) or "note")


def note_blocks(envelope: dict) -> list[tuple[str, dict]]:
    """Each note block with its note type, `plaud-<tab>` (§6.5), in normalised order: the
    `outline`, then every `note_list` tab, Dan's own highlights and memos included. A second tab
    with the same name gets `-2`, then `-3`, and so on."""
    env = normalise(envelope)
    blocks = [b for b in env.get("source_list") or []
              if isinstance(b, dict) and b.get("data_type") == "outline"]
    blocks += [b for b in env.get("note_list") or [] if isinstance(b, dict)]
    out, seen = [], {}
    for block in blocks:
        base = note_type_base(block)
        seen[base] = seen.get(base, 0) + 1
        out.append((f"plaud-{base}" if seen[base] == 1 else f"plaud-{base}-{seen[base]}", block))
    return out


def diarization_fingerprint(envelope: dict) -> str | None:
    """Each segment's start and end, in Plaud's integer milliseconds, and its generic label: the
    same fingerprint means only names changed; a different one is a new diarization (§9.1.1)."""
    rows = transcript_rows(normalise(envelope))
    if rows is None:
        return None
    return sha256_of([[r.get("start_time"), r.get("end_time"),
                       r.get("original_speaker") or r.get("speaker") or ""] for r in rows])


def part_hashes(envelope: dict) -> dict[str, str]:
    """A hash per part (§9.1.1): title, transcript text, speaker names, each note tab, and
    everything else. The Sync screen (stage 2b) says what changed from these."""
    env = normalise(envelope)
    parts = {"title": sha256_of(env.get("name"))}
    rows = transcript_rows(env)
    if rows is not None:
        parts["transcript"] = sha256_of([[r.get("start_time"), r.get("end_time"),
                                          r.get("content"), r.get("original_speaker")]
                                         for r in rows])
        parts["speakers"] = sha256_of([[r.get("speaker"), r.get("embeddingKey")] for r in rows])
    for note_type, block in note_blocks(env):
        parts[f"notes:{note_type}"] = sha256_of(block)
    rest = {k: v for k, v in env.items() if k not in ("name", "note_list")}
    rest["source_list"] = [b for b in env.get("source_list") or []
                           if not (isinstance(b, dict)
                                   and b.get("data_type") in ("transaction", "outline"))]
    parts["other"] = sha256_of(rest)
    return parts
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_normalise.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/recordings/plaud packages/core/tests/conftest.py packages/core/tests/test_normalise.py
git commit -m "feat(core): the versioned Plaud normaliser, part hashes and diarization fingerprint

A pure function shared by the import and the sync (spec §9.1.1). Modelled on audio-router's
plaud.py, plaud_transcript.py and artifacts.py at 64612df; synthetic fixtures only.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Reconcile

**Checkpoint lens:** data integrity and write safety.

**Files:**
- Create: `packages/core/src/recordings/plaud/blocks.py`, `packages/core/src/recordings/plaud/reconcile.py`
- Modify: `packages/core/src/recordings/writer.py` (`SetPlaudFields`, `ReconcileReport`, `Writer.reconcile_plaud`, `auto_private`, `reindex` reconciles)
- Modify: `config.example.toml` (`[plaud] auto_private_patterns`), `demo/build.py` (a synthetic Plaud envelope, Plaud outputs from reconcile), `demo/archive/` (rebuilt)
- Test: `packages/core/tests/test_reconcile.py`

**Interfaces:**
- Consumes:
  - from Task 6: `normalise`, `is_complete`, `transcript_rows`, `note_blocks`, `diarization_fingerprint`, `sha256_of`, `GENERIC_SPEAKER`, `NORMALISER_VERSION`
  - from Task 5: `Writer.mutate`, `Writer._write_rendition_locked`, `Writer._load_fresh`, `AddTags`
  - from Task 3: `PlaudState`
- Produces:
  - **`recordings.plaud.blocks`:** `render_outline(raw: str) -> str | None`, `render_marks(raw: str) -> str | None`, `render_note(block: dict) -> str`.
  - **`recordings.plaud.reconcile`:**
    - constants: `PLAUD_VERSION = "plaud@1"`, `TIMING_RULE = "timing@1"`, `TIMING_TOLERANCE_MS = 1000`, `DEFAULT_AUTO_PRIVATE = (r"^\s*private", r"interview:")`
    - `@dataclass(frozen=True) Snapshot(path: str, ref: str, fetched_at: datetime, envelope: dict | None)`
    - `@dataclass(frozen=True) Held(snapshot: str, slot: str, reason: str)`
    - `@dataclass(frozen=True) Reconciled(outputs, title, title_seen, title_difference, add_private, held, skipped)`
    - `timing_check(envelope: dict, rows: list[dict], media_ms: int | None) -> dict`
    - `patterns_from_config(data: dict) -> tuple[str, ...]`
    - `reconcile(rec: Recording, snapshots: Sequence[Snapshot], existing: Sequence[Rendition], *, auto_private: Sequence[str] = DEFAULT_AUTO_PRIVATE) -> Reconciled`
  - **`recordings.writer`:**
    - `@dataclass(frozen=True) SetPlaudFields(title: str | None = None, title_seen: str | None = None, add_private: bool = False)`
    - `@dataclass(frozen=True) ReconcileReport(recording_id: str, written: int, held: tuple[Held, ...], skipped: tuple[str, ...], title_difference: bool, problem: str | None = None)`, with `.to_dict()`
    - `Writer.reconcile_plaud(recording_id: str) -> ReconcileReport`
    - `Writer(..., auto_private: Sequence[str] = DEFAULT_AUTO_PRIVATE)`
    - `ReindexReport.reconciled: tuple[ReconcileReport, ...]`

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_reconcile.py`:
```python
import json
from datetime import datetime, timedelta, timezone

import pytest

from recordings.archive import RawSource
from recordings.config import ConfigError
from recordings.models import MediaInfo, PlaudState, Recording, TagRef
from recordings.plaud.reconcile import (
    PLAUD_VERSION,
    Snapshot,
    patterns_from_config,
    reconcile,
    timing_check,
)
from recordings.writer import rendition_filename

HEX = "0123456789abcdef0123456789abcdef"
T1 = datetime(2026, 8, 1, 16, 10, tzinfo=timezone.utc)


def rec(**over) -> Recording:
    fields = dict(id="20260801T090000-0700_" + "a" * 8, title="Week 4", title_by="plaud",
                  recorded_at=datetime.fromisoformat("2026-08-01T09:00:00-07:00"),
                  timezone="America/Vancouver", time_source="plaud",
                  media=MediaInfo(file="x.wav", sha256="a" * 64, kind="audio", duration_ms=2600))
    fields.update(over)
    return Recording(**fields)


def snap(env, n=0, *, ref=HEX) -> Snapshot:
    when = T1 + timedelta(days=n)
    return Snapshot(path=f"source/plaud-{when:%Y%m%dT%H%M%SZ}.json", ref=ref, fetched_at=when,
                    envelope=env)


def kinds(outputs):
    return sorted((o.kind, o.note_type) for o in outputs)


def test_the_first_snapshot_gives_a_transcript_and_a_notes_output_per_tab(plaud_env):
    result = reconcile(rec(), [snap(plaud_env())], [])
    assert kinds(result.outputs) == [("notes", "plaud-high-light"), ("notes", "plaud-outline"),
                                     ("notes", "plaud-summary"), ("transcript", None)]
    transcript = next(o for o in result.outputs if o.kind == "transcript")
    assert transcript.version == PLAUD_VERSION == "plaud@1"
    assert transcript.created_at == T1
    assert transcript.inputs["source"] == "source/plaud-20260801T161000Z.json"
    assert len(transcript.inputs["part_sha256"]) == 64
    first, second = transcript.payload["segments"]
    assert first == {"start": 0.0, "end": 1.5, "text": "Hello there.", "speaker": "Speaker 1",
                     "speaker_name": "Ada", "embedding_key": "ek-1"}
    assert second == {"start": 1.5, "end": 2.6, "text": "Hi.", "speaker": "Speaker 2"}
    assert transcript.meta["timing"]["ok"] is True
    summary = next(o for o in result.outputs if o.note_type == "plaud-summary")
    assert summary.payload["markdown"] == "# Summary\n\nTwo people say hello."


def test_reconcile_is_idempotent(plaud_env):
    first = reconcile(rec(), [snap(plaud_env())], [])
    again = reconcile(rec(), [snap(plaud_env())], first.outputs)
    assert again.outputs == ()


def test_a_title_edit_writes_no_new_output(plaud_env):
    s1 = snap(plaud_env())
    first = reconcile(rec(), [s1], [])
    result = reconcile(rec(), [s1, snap(plaud_env(name="Week 4: revised"), 1)], first.outputs)
    assert result.outputs == () and result.title == "Week 4: revised"


def test_a_speaker_rename_writes_a_new_transcript_and_a_note_edit_only_that_note(plaud_env):
    env = plaud_env()
    rows = json.loads(env["source_list"][0]["data_content"])
    renamed = plaud_env(segments=[rows[0], {**rows[1], "speaker": "Grace"}])
    renamed["note_list"][0]["data_content"] += " Then they leave."
    s1, s2 = snap(env), snap(renamed, 1)
    first = reconcile(rec(), [s1], [])
    result = reconcile(rec(), [s1, s2], first.outputs)
    assert kinds(result.outputs) == [("notes", "plaud-summary"), ("transcript", None)]
    assert all(o.inputs["source"] == s2.path for o in result.outputs)
    names = [s.get("speaker_name") for s in next(o for o in result.outputs
                                                 if o.kind == "transcript").payload["segments"]]
    assert names == ["Ada", "Grace"]


def test_a_vanished_note_tab_waits_for_review(plaud_env):
    env = plaud_env()
    without = plaud_env(notes=[env["note_list"][1]])
    s1, s2 = snap(env), snap(without, 1)
    first = reconcile(rec(), [s1], [])
    result = reconcile(rec(), [s1, s2], first.outputs)
    assert result.outputs == ()
    assert [(h.snapshot, h.slot, h.reason) for h in result.held] == [
        (s2.path, "notes:plaud-summary", "notes removed")]
    reviewed = rec(plaud=PlaudState(acknowledged_up_to=s2.path))
    assert reconcile(reviewed, [s1, s2], first.outputs).held == ()


def test_names_reverting_to_speaker_n_wait_for_review(plaud_env):
    env = plaud_env()
    rows = json.loads(env["source_list"][0]["data_content"])
    reverted = plaud_env(segments=[{**rows[0], "speaker": "Speaker 1"}, rows[1]])
    s1, s2 = snap(env), snap(reverted, 1)
    first = reconcile(rec(), [s1], [])
    result = reconcile(rec(), [s1, s2], first.outputs)
    assert result.outputs == ()
    assert [h.reason for h in result.held] == ["names removed"]
    reviewed = rec(plaud=PlaudState(acknowledged_up_to=s2.path))
    assert kinds(reconcile(reviewed, [s1, s2], first.outputs).outputs) == [("transcript", None)]


def test_a_vanished_transcript_waits_for_review(plaud_env):
    env = plaud_env()
    gone = plaud_env()
    gone["source_list"] = [b for b in gone["source_list"] if b["data_type"] != "transaction"]
    first = reconcile(rec(), [snap(env)], [])
    result = reconcile(rec(), [snap(env), snap(gone, 1)], first.outputs)
    assert [h.reason for h in result.held] == ["transcript removed"]


def test_a_reverted_transcript_gets_its_own_output(plaud_env):
    env = plaud_env()
    rows = json.loads(env["source_list"][0]["data_content"])
    edited = plaud_env(segments=[{**rows[0], "content": "Hello!"}, rows[1]])
    snaps = [snap(env), snap(edited, 1), snap(plaud_env(), 2)]
    result = reconcile(rec(), snaps, [])
    transcripts = [o for o in result.outputs if o.kind == "transcript"]
    assert [o.inputs["source"] for o in transcripts] == [s.path for s in snaps]


def test_snapshots_are_taken_in_fetch_order_not_list_order(plaud_env):
    env = plaud_env()
    rows = json.loads(env["source_list"][0]["data_content"])
    later = plaud_env(segments=[{**rows[0], "content": "Hello!"}, rows[1]])
    s1, s2 = snap(env), snap(later, 1)
    result = reconcile(rec(), [s2, s1], [])
    transcripts = [o for o in result.outputs if o.kind == "transcript"]
    assert [o.inputs["source"] for o in transcripts] == [s1.path, s2.path]


def test_a_degraded_or_unreadable_snapshot_is_skipped_and_reported(plaud_env):
    # Review Focus 3: nothing is ever built from a snapshot with link errors, or from one that
    # isn't a JSON object.
    s1 = snap(plaud_env(link_error=True))
    s2 = snap(None, 1)
    s3 = Snapshot(path="source/plaud-x.json", ref=HEX, fetched_at=T1 + timedelta(days=2),
                  envelope=["not", "an", "object"])
    result = reconcile(rec(), [s1, s2, s3], [])
    assert result.outputs == () and result.skipped == (s1.path, s2.path, s3.path)


def test_plaud_owns_the_title_only_while_title_by_is_plaud(plaud_env):
    renamed = [snap(plaud_env(name="Week 5"))]
    owned = reconcile(rec(), renamed, [])
    assert (owned.title, owned.title_seen, owned.title_difference) == ("Week 5", "Week 5", None)
    yours = reconcile(rec(title="My title", title_by="you"), renamed, [])
    assert (yours.title, yours.title_difference) == (None, "Week 5")
    dismissed = rec(title="My title", title_by="you", plaud=PlaudState(title_seen="Week 5"))
    assert reconcile(dismissed, renamed, []).title_difference is None


@pytest.mark.parametrize("title, private", [
    ("Private: therapy", True), ("  private notes", True), ("Interview: Grace", True),
    ("Consultation: Private Chef Charity", False), ("Week 4", False)])
def test_the_auto_private_patterns_add_private(plaud_env, title, private):
    assert reconcile(rec(), [snap(plaud_env(name=title))], []).add_private is private


def test_auto_private_never_removes_and_skips_an_already_private_recording(plaud_env):
    already = rec(tags=[TagRef(tag="private", by="you")])
    assert reconcile(already, [snap(plaud_env(name="Private: x"))], []).add_private is False


def test_two_plaud_ids_on_one_recording_are_reconciled_separately(plaud_env):
    other = "fedcba9876543210fedcba9876543210"
    a, b = snap(plaud_env(HEX, name="First"), 0), snap(plaud_env(other, name="Second"), 1, ref=other)
    result = reconcile(rec(title="First"), [a, b], [])
    assert sum(o.kind == "transcript" for o in result.outputs) == 2
    assert result.title is None  # the title follows the Plaud ID added first


def test_the_timing_check_catches_a_trim():
    rows = [{"start_time": 0, "end_time": 3500}]
    ok = timing_check({"duration": 3500}, rows, 3400)
    trimmed = timing_check({"duration": 5000}, rows, 3400)
    unknown = timing_check({"duration": 3500}, rows, None)
    assert ok == {"rule": "timing@1", "plaud_duration_ms": 3500, "last_segment_end_ms": 3500,
                  "media_duration_ms": 3400, "ok": True}
    assert trimmed["ok"] is False
    assert "ok" not in unknown and "media_duration_ms" not in unknown


def test_untimed_segments_are_kept_and_counted(plaud_env):
    env = plaud_env(segments=[{"content": "No timing.", "speaker": "Speaker 1"},
                              {"content": "Timed.", "start_time": 1000, "end_time": 2000,
                               "speaker": "Speaker 1"}])
    (transcript,) = [o for o in reconcile(rec(), [snap(env)], []).outputs if o.kind == "transcript"]
    assert [(s["start"], s["end"]) for s in transcript.payload["segments"]] == [(0.0, 0.0), (1.0, 2.0)]
    assert transcript.meta["untimed_segments"] == 1


def test_a_tab_named_in_another_script_gives_a_safe_file_name(plaud_env):
    # Review Focus 4.
    env = plaud_env(notes=[{"data_type": "auto_sum_note", "data_id": "n",
                            "data_tab_name": "会議メモ / Q&A", "data_content": "x"}])
    (note,) = [o for o in reconcile(rec(), [snap(env)], []).outputs if o.note_type != "plaud-outline"
               and o.kind == "notes"]
    assert note.note_type == "plaud-q-a"
    name = rendition_filename(note)
    assert name.isascii() and "/" not in name and name.startswith("notes-plaud-q-a-plaud-plaud@1-")


def test_the_patterns_come_from_config_and_are_checked():
    assert patterns_from_config({}) == (r"^\s*private", r"interview:")
    assert patterns_from_config({"plaud": {"auto_private_patterns": ["^x"]}}) == ("^x",)
    with pytest.raises(ConfigError, match="auto_private_patterns"):
        patterns_from_config({"plaud": {"auto_private_patterns": ["("]}})
    with pytest.raises(ConfigError, match="auto_private_patterns"):
        patterns_from_config({"plaud": {"auto_private_patterns": "^x"}})


# ---- through the Writer ----------------------------------------------------------------------

def test_the_writer_reconciles_once_and_owns_the_plaud_fields(writer, make_incoming, plaud_env):
    payload = json.dumps(plaud_env(name="Interview: Ada")).encode()
    added = writer.add(make_incoming(
        title="Interview: Ada", title_by="plaud",
        sources=(RawSource(kind="plaud", ref=HEX, added_at=T1, fetched_at=T1, payload=payload),)))
    report = writer.reconcile_plaud(added.recording.id)
    assert report.written == 4 and report.held == () and report.skipped == ()
    recording = writer.archive.load(added.recording.id)
    assert recording.plaud.title_seen == "Interview: Ada"
    assert ("private", "auto") in {(t.tag, t.by) for t in recording.tags}
    assert writer.reconcile_plaud(added.recording.id).written == 0


def test_reindex_finishes_an_update_interrupted_after_its_snapshot(writer, make_incoming,
                                                                    plaud_env):
    # why: §9.1.1. Reconcile runs on reindex, so a snapshot written before a crash is still built.
    env = plaud_env()
    rows = json.loads(env["source_list"][0]["data_content"])
    first = writer.add(make_incoming(b"same", title_by="plaud", sources=(
        RawSource(kind="plaud", ref=HEX, added_at=T1, fetched_at=T1,
                  payload=json.dumps(env).encode()),))).recording
    writer.reconcile_plaud(first.id)
    later = plaud_env(segments=[{**rows[0], "content": "Hello!"}, rows[1]])
    writer.add(make_incoming(b"same", sources=(
        RawSource(kind="plaud", ref="of_" + HEX, added_at=T1, fetched_at=T1 + timedelta(days=1),
                  payload=json.dumps(later).encode()),)))
    report = writer.reindex()
    (rec_report,) = report.reconciled
    assert rec_report.written == 1
    assert report.to_dict()["plaud"] == {"written": 1, "held": 0, "skipped": 0, "problems": 0}
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_reconcile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings.plaud.reconcile'`.

- [ ] **Step 3: Write Plaud's block renderers and reconcile**

`packages/core/src/recordings/plaud/blocks.py`:
```python
"""Plaud's JSON note blocks as Markdown (spec §9.1.1).

Adapted from audio-router's `archive/vendor_blocks.py` at commit 64612df: Dan's own code,
relicensed MIT for this repo (§19). An outline is a list of {topic, start_time, end_time}.
Highlights and memos are lists whose field names vary between blocks. Anything unrecognised
degrades to the raw text: a renderer that raised would turn an unfamiliar payload into a missing
note, which is worse than the JSON it replaces.
"""

from __future__ import annotations

import json
from typing import Any


def _rows(raw: str) -> list[dict] | None:
    if not raw or raw.lstrip()[:1] != "[":
        return None
    try:
        rows = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(rows, list) or not rows:
        return None
    return rows if all(isinstance(r, dict) for r in rows) else None


def _stamp(ms: Any) -> str:
    """`[MM:SS]`, or `[--:--]` when there is no timing: never a made-up 00:00."""
    if not isinstance(ms, (int, float)) or isinstance(ms, bool):
        return "[--:--]"
    s = int(ms // 1000)
    return f"[{s // 60:02d}:{s % 60:02d}]"


def render_outline(raw: str) -> str | None:
    rows = _rows(raw)
    if rows is None or not any("topic" in r for r in rows):
        return None
    lines = [f"- {_stamp(r.get('start_time'))} {str(r.get('topic') or '').strip()}"
             for r in rows if str(r.get("topic") or "").strip()]
    return "\n".join(lines) or None


def render_marks(raw: str) -> str | None:
    rows = _rows(raw)
    if rows is None or not any(("mark_content" in r or "content" in r or "title" in r)
                               for r in rows):
        return None
    out = []
    for r in rows:
        if r.get("is_user_deleted"):
            continue
        title = str(r.get("title") or "").strip()
        body = next((str(r.get(k) or "").strip() for k in ("expanded_content", "content",
                                                           "mark_content")
                     if str(r.get(k) or "").strip()), "")
        if not title and not body:
            continue
        head = f"**{_stamp(r.get('timestamp'))} {title}**" if title else f"**{_stamp(r.get('timestamp'))}**"
        out.append(f"{head}\n\n{body}" if body else head)
    return "\n\n".join(out) or None


def render_note(block: dict) -> str:
    content = block.get("data_content")
    if not isinstance(content, str):
        return ""
    kind = block.get("data_type")
    if kind == "outline":
        text = render_outline(content)
    elif kind in ("high_light", "mark_memo"):
        text = render_marks(content)
    else:
        text = None
    return (text if text is not None else content).strip()
```

`packages/core/src/recordings/plaud/reconcile.py`:
```python
"""Reconcile (spec §9.1.1): every Plaud snapshot in fetch order goes in; Plaud's outputs and the
fields Plaud owns come out.

A pure function. The import (stage 2a) and the sync (stage 2b) both call it, so they write
identical Plaud outputs, and running it again writes nothing new.

- **Outputs:** one transcript output per change in the transcript's segment rows, and one notes
  output per change in each note tab. Each names its snapshot (`inputs.source`) and its part's
  hash (`inputs.part_sha256`). `version` is `plaud@<normaliser version>`. `created_at` is the
  snapshot's fetch time, so a re-run writes byte-identical files.
- **Removals wait for review:** a tab or transcript that vanishes, or names that revert to
  "Speaker N" on an unchanged diarization. They are held while their snapshot is newer than
  `plaud.acknowledged_up_to`.
- **The title:** Plaud owns it only while `title_by` is `plaud`. Plaud's title is checked against
  the auto-private patterns, which may add `private` and never remove it.
- **Skipped snapshots:** one with link errors, or one that isn't a JSON object, is skipped and
  reported. Nothing is ever built from it.
- **Two Plaud IDs on one recording** are reconciled separately. The title follows the ID whose
  snapshot comes first in the sequence given (the recording's `sources` order).
- **Not yet:** people files (stage 3b). Plaud's names stay as `speaker_name`, and linking them to
  people comes later.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from recordings.config import ConfigError
from recordings.models import Recording, Rendition, is_private
from recordings.plaud.blocks import render_note
from recordings.plaud.normalise import (
    GENERIC_SPEAKER,
    NORMALISER_VERSION,
    diarization_fingerprint,
    is_complete,
    normalise,
    note_blocks,
    sha256_of,
    transcript_rows,
)
from recordings.refs import canonical_plaud_id

PLAUD_VERSION = f"plaud@{NORMALISER_VERSION}"
TIMING_RULE = "timing@1"
TIMING_TOLERANCE_MS = 1000
# audio-router's hold patterns (§7.4): a title starting "private", or containing "interview:".
DEFAULT_AUTO_PRIVATE = (r"^\s*private", r"interview:")


@dataclass(frozen=True)
class Snapshot:
    path: str  # "source/plaud-<stamp>.json", relative to the recording's folder
    ref: str  # the Plaud ID, as recorded
    fetched_at: datetime
    envelope: Any  # the parsed payload; anything but a dict is skipped


@dataclass(frozen=True)
class Held:
    snapshot: str
    slot: str  # "transcript" or "notes:<note type>"
    reason: str  # "notes removed", "transcript removed" or "names removed"


@dataclass(frozen=True)
class Reconciled:
    outputs: tuple[Rendition, ...] = ()
    title: str | None = None  # set recording.title to this
    title_seen: str | None = None  # set plaud.title_seen to this
    title_difference: str | None = None  # Plaud's title, when Dan set his own
    add_private: bool = False
    held: tuple[Held, ...] = ()
    skipped: tuple[str, ...] = ()


def patterns_from_config(data: dict) -> tuple[str, ...]:
    value = data.get("plaud", {}).get("auto_private_patterns", list(DEFAULT_AUTO_PRIVATE))
    if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
        raise ConfigError("[plaud] auto_private_patterns must be a list of regular expressions")
    for pattern in value:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"[plaud] auto_private_patterns: {pattern!r} is not a regular "
                              f"expression ({exc})") from None
    return tuple(value)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def timing_check(envelope: dict, rows: list[dict], media_ms: int | None) -> dict:
    """§9.1.1: Plaud's duration and its last segment's end against the decoded media. A mismatch
    means a trim in Plaud (audio-router found timings at up to 135% of the file)."""
    out: dict[str, Any] = {"rule": TIMING_RULE}
    if _number(envelope.get("duration")):
        out["plaud_duration_ms"] = int(envelope["duration"])
    ends = [r["end_time"] for r in rows if _number(r.get("end_time"))]
    if ends:
        out["last_segment_end_ms"] = int(max(ends))
    if media_ms is not None:
        out["media_duration_ms"] = media_ms
        if "plaud_duration_ms" in out:
            tolerance = max(TIMING_TOLERANCE_MS, media_ms // 100)
            out["ok"] = (abs(out["plaud_duration_ms"] - media_ms) <= tolerance
                         and out.get("last_segment_end_ms", 0) <= media_ms + tolerance)
    return out


def _named(row: dict) -> bool:
    name = str(row.get("speaker") or "").strip()
    return bool(name) and not GENERIC_SPEAKER.match(name)


def _diarization_key(row: dict) -> tuple:
    return (row.get("start_time"), row.get("end_time"),
            row.get("original_speaker") or row.get("speaker") or "")


def _names_removed(old: list[dict], new: list[dict]) -> bool:
    """Same diarization, and a name that was there has gone back to a generic label."""
    if [_diarization_key(r) for r in old] != [_diarization_key(r) for r in new]:
        return False
    return any(_named(a) and not _named(b) for a, b in zip(old, new, strict=True))


def _slots(env: dict) -> dict[str, tuple[str, Any]]:
    out: dict[str, tuple[str, Any]] = {}
    rows = transcript_rows(env)
    if rows:
        out["transcript"] = (sha256_of(rows), rows)
    for note_type, block in note_blocks(env):
        if render_note(block):
            out[f"notes:{note_type}"] = (sha256_of(block), block)
    return out


def _transcript(rows: list[dict], digest: str, snap: Snapshot, env: dict,
                rec: Recording) -> Rendition:
    segments, untimed, previous_end = [], 0, 0.0
    for row in rows:
        start_ms, end_ms = row.get("start_time"), row.get("end_time")
        if _number(start_ms) and _number(end_ms) and 0 <= start_ms <= end_ms:
            start, end = start_ms / 1000, end_ms / 1000
        else:
            untimed += 1
            start = end = previous_end
        previous_end = end
        name = str(row.get("speaker") or "").strip()
        label = str(row.get("original_speaker") or "").strip() or name
        segment: dict[str, Any] = {"start": start, "end": end,
                                   "text": str(row.get("content") or "").strip()}
        if label:
            segment["speaker"] = label
        if name and name != label:
            segment["speaker_name"] = name
        key = str(row.get("embeddingKey") or "").strip()
        if key:
            segment["embedding_key"] = key
        segments.append(segment)
    meta: dict[str, Any] = {"diarization": diarization_fingerprint(env),
                            "timing": timing_check(env, rows, rec.media.duration_ms)}
    if untimed:
        meta["untimed_segments"] = untimed
    return Rendition(kind="transcript", engine="plaud", model="plaud", version=PLAUD_VERSION,
                     created_at=snap.fetched_at,
                     inputs={"source": snap.path, "part_sha256": digest},
                     meta=meta, payload={"segments": segments})


def _notes(note_type: str, block: dict, digest: str, snap: Snapshot) -> Rendition:
    return Rendition(kind="notes", note_type=note_type, engine="plaud", model="plaud",
                     version=PLAUD_VERSION, created_at=snap.fetched_at,
                     inputs={"source": snap.path, "part_sha256": digest},
                     meta={"data_type": str(block.get("data_type") or "")},
                     payload={"markdown": render_note(block)})


def _identity(r: Rendition) -> tuple:
    return (r.kind, r.note_type, r.inputs.get("source"), r.inputs.get("part_sha256"))


def reconcile(rec: Recording, snapshots: Sequence[Snapshot], existing: Sequence[Rendition], *,
              auto_private: Sequence[str] = DEFAULT_AUTO_PRIVATE) -> Reconciled:
    ordered = [s for _, s in sorted(enumerate(snapshots), key=lambda p: (p[1].fetched_at, p[0]))]
    ack = rec.plaud.acknowledged_up_to if rec.plaud else None
    ack_position = max((i for i, s in enumerate(ordered) if s.path == ack), default=-1)
    groups: dict[str, list[tuple[int, Snapshot]]] = {}
    for position, snapshot in enumerate(ordered):
        groups.setdefault(canonical_plaud_id(snapshot.ref), []).append((position, snapshot))
    have = {_identity(r) for r in existing if r.engine == "plaud"}
    outputs: list[Rendition] = []
    held: dict[tuple[str, str], Held] = {}
    skipped: list[str] = []
    names: dict[str, str] = {}
    for ref, members in groups.items():
        applied: dict[str, tuple[str, Any]] = {}
        for position, snapshot in members:
            if not isinstance(snapshot.envelope, dict) or not is_complete(snapshot.envelope):
                skipped.append(snapshot.path)
                continue
            env = normalise(snapshot.envelope)
            reviewed = position <= ack_position
            if isinstance(env.get("name"), str) and env["name"].strip():
                names[ref] = env["name"].strip()
            current = _slots(env)
            for slot in sorted(set(applied) | set(current)):
                if slot not in current:
                    if reviewed:
                        del applied[slot]
                    else:
                        reason = "transcript removed" if slot == "transcript" else "notes removed"
                        held[(ref, slot)] = Held(snapshot.path, slot, reason)
                    continue
                digest, value = current[slot]
                if slot in applied and applied[slot][0] == digest:
                    held.pop((ref, slot), None)
                    continue
                if (slot == "transcript" and slot in applied and not reviewed
                        and _names_removed(applied[slot][1], value)):
                    held[(ref, slot)] = Held(snapshot.path, slot, "names removed")
                    continue
                held.pop((ref, slot), None)
                applied[slot] = (digest, value)
                output = (_transcript(value, digest, snapshot, env, rec) if slot == "transcript"
                          else _notes(slot.removeprefix("notes:"), value, digest, snapshot))
                if _identity(output) not in have:
                    have.add(_identity(output))
                    outputs.append(output)

    title = title_seen = difference = None
    add_private = False
    primary = canonical_plaud_id(snapshots[0].ref) if snapshots else None
    plaud_title = names.get(primary) if primary is not None else None
    if plaud_title is not None:
        seen = rec.plaud.title_seen if rec.plaud else None
        if rec.title_by == "you":
            if plaud_title != seen:
                difference = plaud_title
        else:
            if plaud_title != rec.title:
                title = plaud_title
            if plaud_title != seen:
                title_seen = plaud_title
        add_private = not is_private(rec) and any(
            re.search(p, plaud_title, re.IGNORECASE) for p in auto_private)
    return Reconciled(outputs=tuple(outputs), title=title, title_seen=title_seen,
                      title_difference=difference, add_private=add_private,
                      held=tuple(held.values()), skipped=tuple(skipped))
```

- [ ] **Step 4: Reconcile through the `Writer`, and in `reindex`**

In `packages/core/src/recordings/writer.py`:
1. **Add** the imports:
   ```python
   from recordings.models import PlaudState, is_private
   from recordings.plaud.reconcile import (
       DEFAULT_AUTO_PRIVATE,
       Held,
       Snapshot,
       patterns_from_config,
       reconcile,
   )
   ```
2. **Add** after `MergeIncoming`:
   ```python
   @dataclass(frozen=True)
   class SetPlaudFields:
       """The fields Plaud owns (§9.1.1): the title while title_by is plaud, the title last
       seen, and an auto-private tag, which is only ever added."""

       title: str | None = None
       title_seen: str | None = None
       add_private: bool = False
       name: str = "plaud-fields"

       def apply(self, rec: Recording) -> Recording:
           update: dict = {}
           if self.title is not None:
               update |= {"title": self.title, "title_by": "plaud"}
           if self.title_seen is not None:
               plaud = rec.plaud or PlaudState()
               update["plaud"] = plaud.model_copy(update={"title_seen": self.title_seen})
           rec = rec.model_copy(update=update)
           if self.add_private and not is_private(rec):
               rec = AddTags((TagRef(tag="private", by="auto"),)).apply(rec)
           return rec


   @dataclass(frozen=True)
   class ReconcileReport:
       recording_id: str
       written: int
       held: tuple[Held, ...]
       skipped: tuple[str, ...]
       title_difference: bool  # whether Plaud's title differs from yours; never the title itself
       problem: str | None = None

       def to_dict(self) -> dict:
           return {"recording_id": self.recording_id, "written": self.written,
                   "held": [{"snapshot": h.snapshot, "slot": h.slot, "reason": h.reason}
                            for h in self.held],
                   "skipped": list(self.skipped), "title_difference": self.title_difference,
                   "problem": self.problem}
   ```
3. **Replace** `ReindexReport` with:
   ```python
   @dataclass(frozen=True)
   class ReindexReport:
       recordings: int
       adopted: tuple[str, ...]  # outside edits accepted as the new merge base
       invalid: tuple[str, ...]  # files that don't validate, flagged in state.db
       problems: tuple[dict, ...]
       reconciled: tuple[ReconcileReport, ...] = ()

       def to_dict(self) -> dict:
           return {"recordings": self.recordings, "adopted": list(self.adopted),
                   "invalid": list(self.invalid), "problems": list(self.problems),
                   "plaud": {"written": sum(r.written for r in self.reconciled),
                             "held": sum(len(r.held) for r in self.reconciled),
                             "skipped": sum(len(r.skipped) for r in self.reconciled),
                             "problems": sum(r.problem is not None for r in self.reconciled)}}
   ```
4. **Change** the `Writer.__init__` signature to
   `def __init__(self, root: Path, state: State, index: Index, *, writer_id: str, auto_private: Sequence[str] = DEFAULT_AUTO_PRIVATE) -> None:`,
   and **add** `self.auto_private = tuple(auto_private)` beside `self.state, self.index, …`.
5. In `Writer.open`, **replace** the `writer = cls(...)` line with:
   ```python
           writer = cls(cfg.archive_path, state, Index(cfg.index_path), writer_id=cfg.writer_id,
                        auto_private=patterns_from_config(cfg.data))
   ```
6. **Add** this method after `_publish_raw`:
   ```python
       def reconcile_plaud(self, recording_id: str) -> ReconcileReport:
           """Build any Plaud outputs this recording's snapshots imply, and apply the fields Plaud
           owns (§9.1.1). Writes nothing when there is nothing new."""
           with self.locks.hold(recording_id):
               rec = self._load_fresh(self._rel(recording_id))
               folder = self.archive.path_for(recording_id)
               snapshots = []
               for source in rec.sources:
                   if source.kind != "plaud" or not source.raw:
                       continue
                   try:
                       envelope = json.loads((folder / source.raw).read_text(encoding="utf-8"))
                   except (OSError, UnicodeDecodeError, ValueError):
                       envelope = None
                   snapshots.append(Snapshot(path=source.raw, ref=source.ref,
                                             fetched_at=source.fetched_at or source.added_at,
                                             envelope=envelope))
               if not snapshots:
                   return ReconcileReport(recording_id, 0, (), (), False)
               existing = [r for _, r in self.archive.renditions(recording_id, problems=[])]
               result = reconcile(rec, snapshots, existing, auto_private=self.auto_private)
               written = sum(self._write_rendition_locked(folder, r) is not None
                             for r in result.outputs)
               if result.title is not None or result.title_seen is not None or result.add_private:
                   self.mutate(recording_id, SetPlaudFields(result.title, result.title_seen,
                                                            result.add_private))
               return ReconcileReport(recording_id, written, result.held, result.skipped,
                                      result.title_difference is not None)
   ```
7. In `reindex`, **replace** the two lines from `count = self.rebuild_index(...)` to the `return`
   with:
   ```python
           reconciled = []
           for rec in list(self.archive.iter_recordings()):
               if not any(s.kind == "plaud" for s in rec.sources):
                   continue
               try:
                   reconciled.append(self.reconcile_plaud(rec.id))
               except (WriterError, KeyError) as exc:
                   reconciled.append(ReconcileReport(rec.id, 0, (), (), False, problem=str(exc)))
           count = self.rebuild_index(allow_empty=allow_empty)
           problems = tuple({"path": str(p.path), "message": p.message}
                            for p in self.archive.problems)
           return ReindexReport(recordings=count, adopted=tuple(adopted), invalid=tuple(invalid),
                                problems=problems, reconciled=tuple(reconciled))
   ```

In `config.example.toml`, **replace** the line `auto_private_patterns = ["^private"] …` with:
```toml
# Plaud titles matching these are tagged `private` (spec §7.4): Python regular expressions,
# searched ignoring case. Read from stage 2a, by reconcile. They only ever add `private`.
auto_private_patterns = ["^\\s*private", "interview:"]
```

- [ ] **Step 5: Give the demo a real Plaud snapshot, rebuilt by reconcile**

In `demo/build.py`:
1. **Add**, above `build`:
   ```python
   def plaud_envelope(entry: dict, transcript: dict, summary_md: str) -> dict:
       """A synthetic Plaud `files/{id}` envelope, built from the canned transcript, so the demo's
       Plaud outputs come from reconcile, the way the real ones do. Generic speaker labels only:
       no real person's name."""
       labels: dict[str, str] = {}
       rows = []
       for seg in transcript["segments"]:
           raw_label = seg.get("speaker") or "SPEAKER_00"
           label = labels.setdefault(raw_label, f"Speaker {len(labels) + 1}")
           rows.append({"content": seg["text"], "start_time": round(seg["start"] * 1000),
                        "end_time": round(seg["end"] * 1000), "speaker": label,
                        "original_speaker": label})
       return {"id": entry["source_ref"], "name": entry["title"], "duration": entry["duration_ms"],
               "demo": True,
               "source_list": [{"data_type": "transaction", "data_id": "demo-transaction",
                                "data_tab_name": None,
                                "data_content": json.dumps(rows, ensure_ascii=False)}],
               "note_list": [{"data_type": "auto_sum_note", "data_id": "demo-summary",
                              "data_tab_name": "Summary", "data_content": summary_md}]}
   ```
2. In `build`, **replace** the whole `if entry.get("plaud"):` block (the two `renditions.append`
   calls and the `payload = json.dumps(...)` line) with:
   ```python
               fetched_at = None
               if entry.get("plaud"):
                   summary = (canned_dir / "notes" / entry["slug"] / "plaud.md").read_text(
                       encoding="utf-8")
                   envelope = plaud_envelope(entry, renditions[0].payload, summary)
                   payload = json.dumps(envelope, indent=2, ensure_ascii=False).encode() + b"\n"
                   fetched_at = stamp + timedelta(seconds=30)
   ```
3. In the `writer.add(Incoming(...))` call, **replace** the `sources=` argument with
   `sources=(RawSource(kind=entry["source_kind"], ref=entry["source_ref"], added_at=BUILD_AT, fetched_at=fetched_at, payload=payload),),`,
   and **add** `title_by="plaud" if entry.get("plaud") else None,`.
4. **Replace** `writer.add(Incoming(` with `added = writer.add(Incoming(`, and after that call's
   closing `))`, **add**:
   ```python
               if entry.get("plaud"):
                   writer.reconcile_plaud(added.recording.id)  # Plaud's outputs, as the sync makes them
   ```

- [ ] **Step 6: Rebuild the demo and run every test**

Run:
```bash
uv run python demo/build.py --media-dir demo/.cache/media --out demo/archive --force
uv run pytest
```
Expected: every test passes. The Apollo 13 recording's `source/plaud-20261008T120130Z.json` is
now a Plaud-shaped envelope, and its Plaud outputs are
`transcript-plaud-plaud@1-20261008T120130Z.json` and
`notes-plaud-summary-plaud-plaud@1-20261008T120130Z.json`, each with `inputs.source` and
`inputs.part_sha256`. Its `recording.json` has `title_by: plaud` and `plaud.title_seen`.

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/recordings packages/core/tests/test_reconcile.py config.example.toml demo/build.py demo/archive
git commit -m "feat(core): reconcile Plaud snapshots into outputs and the fields Plaud owns

A pure function over every complete snapshot in fetch order (spec §9.1.1): idempotent, removals
held for review, per-Plaud-ID, the timing check recorded. reindex runs it. Block rendering adapted
from audio-router's vendor_blocks.py at 64612df (relicensed MIT). The demo's Plaud outputs now come
from a synthetic Plaud envelope through reconcile.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 8: Reading `audio-router`'s archive: the media probe and the import plan

**Checkpoint lens:** data integrity and write safety.

This task reads and plans. It writes nothing. The plan is the dry run (§9.3): it gives every
catalog row one disposition, says why for each row it can't place, and never names a title. Task 9
carries it out.

**Files:**
- Create: `packages/core/src/recordings/media.py`, `packages/core/src/recordings/sources/__init__.py`, `packages/core/src/recordings/sources/audio_router.py`
- Modify: `packages/core/tests/conftest.py` (the synthetic `audio-router` archive)
- Test: `packages/core/tests/test_media.py`, `packages/core/tests/test_import_plan.py`

**Interfaces:**
- Consumes: `Index.find_by_source`, `Index.find_by_sha256` and `Index.sha256_of` (Task 4), and `sha256_file` (`archive.py`).
- Produces:
  - **`recordings.media`:**
    - suffix sets: `AUDIO_SUFFIXES`, `VIDEO_SUFFIXES`, `CONTAINER_SUFFIXES`, `MEDIA_SUFFIXES`
    - `@dataclass(frozen=True) MediaProbe(duration_ms: int | None, sample_rate: int | None, channels: int | None, has_video: bool = False)`
    - `probe(path: Path, *, ffprobe: str = "ffprobe", timeout: float = 120) -> MediaProbe | None`
    - `media_kind(path: Path, probed: MediaProbe | None) -> Literal["audio", "video"] | None`
  - **`recordings.sources.audio_router`:**
    - constants: `CATALOG`, `PROVENANCE`, `REQUIRED_COLUMNS`, `SOURCES = ("plaud", "recorder", "pocket")`, `TIME_SOURCES`, `DISPOSITIONS`
    - `class CatalogError(RuntimeError)`
    - `@dataclass(frozen=True) Row(uri, source, file_id, recorded_at_local, timezone, time_source, audio_rel, audio_sha256, dup_of, access)`
    - `@dataclass(frozen=True) Item(row: Row, disposition: str, reason: str = "", kind: str = "", audio: Path | None = None, sha256: str = "", recorded_at: datetime | None = None, timezone: str = "", time_source: str = "", snapshots: tuple[Path, ...] = (), renditions: tuple[Path, ...] = (), present_id: str | None = None, private: bool = False)`
    - `@dataclass(frozen=True) Plan(root, items, ledger_only=(), unlisted=(), held_on_plaud=None, problems=())`, with `.counts() -> dict[str, int]`, `.ok -> bool` and `.report() -> dict`
    - `read_catalog(root: Path) -> list[Row]`
    - `snapshot_order(path: Path) -> tuple[str, str]`
    - `plan(root: Path, index: Index | None = None, *, default_timezone: str = "America/Vancouver") -> Plan`
  - **Test fixtures** in `conftest.py`:
    - `wav_bytes(seed: int, seconds: float = 2.0, rate: int = 16000, channels: int = 1) -> bytes`, a module-level helper
    - `AR_COLUMNS`
    - `class ARArchive`, with `plaud()`, `recorder()`, `pocket()`, `pocket_without_audio()`, `private()`, `write_catalog()` and `tree_hash()`
    - the fixture `ar_archive -> ARArchive`

- [ ] **Step 1: Write the failing tests for the media probe**

`packages/core/tests/test_media.py`:
```python
import io
import json
import os
import wave

from recordings import media
from recordings.media import MediaProbe, media_kind, probe


def _wav(path, seconds=1.5, rate=22050, channels=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * channels * int(seconds * rate))
    path.write_bytes(buf.getvalue())
    return path


def test_a_wav_file_is_measured_without_ffprobe(tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    assert probe(_wav(tmp_path / "a.wav")) == MediaProbe(duration_ms=1500, sample_rate=22050,
                                                         channels=2)


def test_anything_else_needs_ffprobe_and_is_never_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    (tmp_path / "a.mp3").write_bytes(b"ID3 not really")
    assert probe(tmp_path / "a.mp3") is None
    (tmp_path / "broken.wav").write_bytes(b"RIFF")
    assert probe(tmp_path / "broken.wav") is None


def test_ffprobes_answer_is_read(tmp_path, monkeypatch):
    # A stand-in ffprobe on PATH: it prints what ffprobe's -of json prints for an m4a.
    answer = {"streams": [{"codec_type": "audio", "sample_rate": "44100", "channels": 1}],
              "format": {"duration": "61.234"}}
    exe = tmp_path / "bin" / "ffprobe"
    exe.parent.mkdir()
    exe.write_text(f"#!/bin/sh\necho '{json.dumps(answer)}'\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{exe.parent}{os.pathsep}{os.environ['PATH']}")
    (tmp_path / "a.m4a").write_bytes(b"not really")
    assert probe(tmp_path / "a.m4a") == MediaProbe(duration_ms=61234, sample_rate=44100,
                                                   channels=1, has_video=False)


def test_the_kind_comes_from_the_suffix_and_for_mp4_from_the_streams(tmp_path):
    assert media_kind(tmp_path / "a.m4a", None) == "audio"
    assert media_kind(tmp_path / "a.MOV", None) == "video"
    assert media_kind(tmp_path / "a.mp4", MediaProbe(1, 1, 1, has_video=True)) == "video"
    assert media_kind(tmp_path / "a.mp4", MediaProbe(1, 1, 1)) == "audio"
    assert media_kind(tmp_path / "a.txt", None) is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_media.py -v`
Expected: FAIL with `ImportError: cannot import name 'media' from 'recordings'`.

- [ ] **Step 3: Write the probe**

`packages/core/src/recordings/media.py`:
```python
"""Measuring media at ingest (spec §9.1.1): its decoded duration, sample rate and channel count.

WAV is read with the standard library's `wave`. Everything else goes through ffprobe (from ffmpeg,
which the image installs; https://ffmpeg.org/ffprobe.html). Without ffprobe the answer is None,
never a guess, and `recordings doctor` reports the missing ffprobe. The decoded duration is what
the timing check compares Plaud's against, so the source's own claim is never used here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AUDIO_SUFFIXES = frozenset({".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac"})
VIDEO_SUFFIXES = frozenset({".mov", ".webm", ".mkv", ".ogv"})
CONTAINER_SUFFIXES = frozenset({".mp4"})  # audio or video: its streams decide
MEDIA_SUFFIXES = AUDIO_SUFFIXES | VIDEO_SUFFIXES | CONTAINER_SUFFIXES


@dataclass(frozen=True)
class MediaProbe:
    duration_ms: int | None
    sample_rate: int | None
    channels: int | None
    has_video: bool = False


def _int(value: object) -> int | None:
    try:
        number = int(float(value))  # ffprobe prints numbers as strings
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def probe(path: Path, *, ffprobe: str = "ffprobe", timeout: float = 120) -> MediaProbe | None:
    path = Path(path)
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                rate = w.getframerate()
                return MediaProbe(duration_ms=round(w.getnframes() * 1000 / rate) if rate else None,
                                  sample_rate=rate or None, channels=w.getnchannels() or None)
        except (wave.Error, EOFError, OSError):
            return None
    exe = shutil.which(ffprobe)
    if exe is None:
        return None
    try:
        proc = subprocess.run(
            [exe, "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,sample_rate,channels", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=timeout)
        data = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    streams = [s for s in data.get("streams") or [] if isinstance(s, dict)]
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    seconds = (data.get("format") or {}).get("duration")
    try:
        duration_ms = round(float(seconds) * 1000) if seconds is not None else None
    except (TypeError, ValueError):
        duration_ms = None
    return MediaProbe(duration_ms=duration_ms, sample_rate=_int(audio.get("sample_rate")),
                      channels=_int(audio.get("channels")),
                      has_video=any(s.get("codec_type") == "video" for s in streams))


def media_kind(path: Path, probed: MediaProbe | None) -> Literal["audio", "video"] | None:
    suffix = Path(path).suffix.lower()
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in CONTAINER_SUFFIXES:
        return "video" if probed is not None and probed.has_video else "audio"
    return None
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest packages/core/tests/test_media.py -v`
Expected: 4 passed.

- [ ] **Step 5: Write the synthetic `audio-router` archive and the failing plan tests**

Add to `packages/core/tests/conftest.py` (with `import csv`, `import hashlib`, `import io`,
`import wave` and `from pathlib import Path` at the top):
```python
# audio-router's catalog@5 columns, in order (scripts/build_catalog.py, COLUMNS).
AR_COLUMNS = [
    "uri", "source", "file_id", "recorded_at_local", "timezone", "time_source", "duration_ms",
    "audio_rel", "audio_sha256", "dup_of", "ledger_status", "transcript_source", "transcript_len",
    "transcript_payload_chars", "segments", "speakers_named", "has_vendor_transcript",
    "has_vendor_notes", "vendor_note_kinds", "has_local_transcript", "has_local_summary",
    "rendition_kinds", "tags", "access",
]


def wav_bytes(seed: int, seconds: float = 2.0, rate: int = 16000, channels: int = 1) -> bytes:
    """A real, tiny WAV file whose bytes differ by seed, so each recording has its own SHA-256."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes([seed % 256, seed // 256 % 256]) * channels * int(seconds * rate))
    return buf.getvalue()


class ARArchive:
    """A synthetic audio-router media root, laid out as its archive/store.py, sources/ and
    scripts/build_catalog.py describe: never a copy of the real one, and every name invented."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.rows: list[dict] = []
        self.ledger_only: list[str] = []
        self.held = 0
        self._seed = 0

    def _write(self, rel: str, data: bytes | str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def _audio(self, rel: str, seconds: float) -> str:
        self._seed += 1
        return hashlib.sha256(self._write(rel, wav_bytes(self._seed, seconds)).read_bytes()).hexdigest()

    def _row(self, **cells) -> dict:
        row = {c: "" for c in AR_COLUMNS}
        row.update(cells)
        self.rows.append(row)
        return row

    def plaud(self, fid: str, *, envelopes=(), stamps=None, derived=None, audio=True,
              recorded_at="2026-08-01T09:00:00-07:00", dup_of="", seconds=2.6,
              same_audio_as: str | None = None, tier: str = "") -> dict:
        """Snapshots in raw/<fid>/<stamp>.json (oldest first; a str is written as is), audio in
        audio/2026/08/<fid>.wav, outputs in derived/<fid>/. tier="private/" puts it all in the
        private tier, registered by file ID, as audio-router's catalog/private.jsonl does."""
        base = f"{tier}plaud"
        for i, env in enumerate(envelopes):
            stamp = stamps[i] if stamps else f"2026080{i + 1}T161000Z"
            self._write(f"{base}/raw/{fid}/{stamp}.json",
                        env if isinstance(env, str) else json.dumps(env, indent=1, ensure_ascii=False))
        for name, doc in (derived or {}).items():
            self._write(f"{base}/derived/{fid}/{name}", json.dumps(doc, indent=1))
        rel, sha = "", ""
        if audio:
            rel = f"{base}/audio/2026/08/{fid}.wav"
            if same_audio_as:
                sha = hashlib.sha256(self._write(rel, (self.root / same_audio_as).read_bytes())
                                     .read_bytes()).hexdigest()
            else:
                sha = self._audio(rel, seconds)
        if tier:
            return self._row(uri=f"private/{fid}", source="private", file_id=fid,
                             recorded_at_local=recorded_at, timezone="America/Vancouver",
                             time_source="filename", duration_ms=str(int(seconds * 1000)),
                             access="restricted")
        return self._row(uri=f"plaud/{fid}", source="plaud", file_id=fid,
                         recorded_at_local=recorded_at, timezone="America/Vancouver",
                         time_source="start_at", duration_ms=str(int(seconds * 1000)),
                         audio_rel=rel, audio_sha256=sha, dup_of=dup_of)

    def private(self, fid: str, **kwargs) -> dict:
        return self.plaud(fid, tier="private/", **kwargs)

    def recorder(self, *, transcript="Speaker 1: Hello.\nSpeaker 2: Hi.\n",
                 recorded_at="2026-07-15T16:17:00-07:00") -> dict:
        self._seed += 1
        data = wav_bytes(self._seed, 2.0)
        fid = hashlib.sha256(data).hexdigest()
        self._write(f"recorder/audio/2026/07/{fid}.wav", data)
        envelope = {  # recorder.envelope_for: a Plaud-shaped envelope around a prose transcript
            "id": fid, "name": "Jul 15 at 16-17", "duration": 2000,
            "start_at": "2026-07-15T16:17:00", "created_at": "2026-07-15T16:17:00",
            "source_list": [{"data_type": "transaction", "data_content": transcript,
                             "data_tab_name": None}],
            "note_list": [], "_meta": {"source": "recorder", "tool": "audio-router"}}
        self._write(f"recorder/raw/{fid}/20260716T000000Z.json", json.dumps(envelope, indent=1))
        return self._row(uri=f"recorder/{fid}", source="recorder", file_id=fid,
                         recorded_at_local=recorded_at, timezone="America/Vancouver",
                         time_source="filename", duration_ms="2000",
                         audio_rel=f"recorder/audio/2026/07/{fid}.wav", audio_sha256=fid)

    def pocket(self, uuid: str, *, title="Pocket chat", recorded_at="2026-08-02T10:00:00-07:00",
               segments=None, summaries=None) -> dict:
        doc = {"id": uuid, "title": title, "duration": 2.0, "recording_at": "2026-08-02T10:00:00",
               "created_at": "2026-08-02T10:05:00", "language": "en", "state": "done", "tags": [],
               "transcript": {"metadata": {}, "text": "Hi there.", "segments": segments if segments
                              is not None else [{"start": 0, "end": 1, "text": "Hi", "speaker": "A"},
                                                {"start": 1, "end": 2, "text": "there."}]},
               "summarizations": summaries if summaries is not None else {"sid-1": {"text": "A chat."}}}
        self._write(f"pocket/raw/{uuid}.json", json.dumps(doc, indent=1))
        sha = self._audio(f"pocket/audio/2026/08/{uuid}.wav", 2.0)
        return self._row(uri=f"pocket/{uuid}", source="pocket", file_id=uuid,
                         recorded_at_local=recorded_at, timezone="America/Vancouver",
                         time_source="recording_at", duration_ms="2000",
                         audio_rel=f"pocket/audio/2026/08/{uuid}.wav", audio_sha256=sha)

    def pocket_without_audio(self, stem: str = "getting_started") -> dict:
        """audio-router's one audio-less Pocket item: its native id is a title slug, so the
        catalog keys it on sha256(stem)[:32], and that slug must never reach any output."""
        self._write(f"pocket/raw/{stem}.json", json.dumps({"id": stem, "title": "Getting Started"}))
        fid = hashlib.sha256(stem.encode("utf-8")).hexdigest()[:32]
        return self._row(uri=f"pocket/{fid}", source="pocket", file_id=fid,
                         recorded_at_local="2026-08-01T08:00:00-07:00",
                         timezone="America/Vancouver", time_source="created_at")

    def write_catalog(self, *, provenance_rows: int | None = None) -> None:
        cat = self.root / "catalog"
        cat.mkdir(parents=True, exist_ok=True)
        with (cat / "catalog.csv").open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=AR_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(sorted(self.rows, key=lambda r: r["uri"]))
        provenance = {"schema": "audio-router/catalog@5",
                      "rows": len(self.rows) if provenance_rows is None else provenance_rows,
                      "ledger_only_no_media": self.ledger_only,
                      "per_source": {"plaud": {"held_count": self.held}}}
        (cat / "provenance.json").write_text(json.dumps(provenance, indent=1), encoding="utf-8")

    def tree_hash(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(p for p in self.root.rglob("*") if p.is_file()):
            digest.update(path.relative_to(self.root).as_posix().encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()


@pytest.fixture
def ar_archive(tmp_path) -> ARArchive:
    return ARArchive(tmp_path / "audio-router-media")
```

`packages/core/tests/test_import_plan.py`:
```python
import json
from datetime import datetime, timezone

import pytest

from recordings.archive import RawSource
from recordings.sources.audio_router import DISPOSITIONS, CatalogError, plan, read_catalog

A = "a" * 32
B = "b" * 32
C = "c" * 32
D = "d" * 32
P = "e" * 32
UUID = "11111111-1111-4111-8111-111111111111"
T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def full(ar, plaud_env):
    """Every kind of row audio-router's catalog holds (spec §9.3's mapping table)."""
    ar.plaud(A, envelopes=[plaud_env(A, name="A PRIVATE TITLE"),
                           plaud_env(A, name="Interview: Grace")])
    ar.plaud(B, envelopes=[plaud_env(B)], same_audio_as=f"plaud/audio/2026/08/{A}.wav",
             dup_of=f"plaud/{A}")
    ar.plaud(C, envelopes=[plaud_env(C)], recorded_at="2026-08-03T11:00:00-07:00")
    ar.plaud(D, envelopes=[plaud_env(D)], audio=False)  # no audio: arrives through the sync
    ar.recorder()
    ar.pocket(UUID, title="A PRIVATE TITLE")
    ar.pocket_without_audio()
    ar.private(P, envelopes=[plaud_env(P, name="A PRIVATE TITLE")])
    ar.ledger_only = ["f" * 32]
    ar.held = 2
    ar.write_catalog()


def test_the_dry_run_accounts_for_every_catalog_row(ar_archive, plaud_env):
    full(ar_archive, plaud_env)
    the_plan = plan(ar_archive.root)
    assert sorted(i.row.uri for i in the_plan.items) == sorted(r["uri"] for r in ar_archive.rows)
    assert the_plan.counts() == {"new": 4, "new-private": 1, "duplicate": 1, "present": 0,
                                 "no-audio": 2, "unplaced": 0}
    assert sum(the_plan.counts().values()) == len(ar_archive.rows)
    assert set(the_plan.counts()) == set(DISPOSITIONS)
    assert the_plan.ok and the_plan.problems == ()
    report = the_plan.report()
    assert report["catalog_rows"] == 8 and report["private"] == 1
    assert report["ledger_only"] == ["f" * 32] and report["held_on_plaud"] == 2


def test_the_dry_run_never_names_a_title_and_never_changes_the_source(ar_archive, plaud_env):
    # Review Focus 4, and §9.3: it reads audio-router's archive and never changes it.
    full(ar_archive, plaud_env)
    before = ar_archive.tree_hash()
    text = json.dumps(plan(ar_archive.root).report(), ensure_ascii=False)
    for title in ("A PRIVATE TITLE", "Interview: Grace", "Getting Started", "getting_started",
                  "Pocket chat", "Jul 15"):
        assert title not in text
    assert ar_archive.tree_hash() == before


def test_each_row_it_cannot_place_says_why(ar_archive, plaud_env):
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    (ar_archive.root / f"plaud/audio/2026/08/{A}.wav").unlink()
    ar_archive.plaud(B, envelopes=[plaud_env(B)])
    (ar_archive.root / f"plaud/audio/2026/08/{B}.wav").write_bytes(b"damaged in the copy")
    ar_archive.plaud(C, envelopes=[plaud_env(C)])["time_source"] = "a sundial"
    ar_archive.plaud(D, envelopes=[plaud_env(D)], dup_of="plaud/" + "0" * 32)
    ar_archive.private(P)
    (ar_archive.root / f"private/plaud/audio/2026/08/{P}.wav").unlink()
    ar_archive.rows.append({**ar_archive.rows[0], "uri": "zoom/z1", "source": "zoom"})
    ar_archive.write_catalog()
    the_plan = plan(ar_archive.root)
    reasons = {u["uri"]: u["reason"] for u in the_plan.report()["unplaced"]}
    assert "missing from the copy" in reasons[f"plaud/{A}"]
    assert "damaged" in reasons[f"plaud/{B}"]
    assert "a sundial" in reasons[f"plaud/{C}"]
    assert "dup_of" in reasons[f"plaud/{D}"]
    assert "0 media files" in reasons[f"private/{P}"]
    assert "unknown source" in reasons["zoom/z1"]
    assert not the_plan.ok


def test_a_short_copy_is_a_problem(ar_archive, plaud_env):
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.write_catalog(provenance_rows=5)
    the_plan = plan(ar_archive.root)
    assert the_plan.problems and "incomplete" in the_plan.problems[0] and not the_plan.ok


def test_the_catalog_must_be_audio_routers(ar_archive):
    with pytest.raises(CatalogError, match="catalog.csv is missing"):
        plan(ar_archive.root)
    (ar_archive.root / "catalog").mkdir(parents=True)
    (ar_archive.root / "catalog" / "catalog.csv").write_text("uri,source\nx,y\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="missing columns"):
        read_catalog(ar_archive.root)


def test_recordings_on_disk_that_the_catalog_misses_are_reported_by_id(ar_archive, plaud_env):
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.write_catalog()
    ar_archive.plaud(B, envelopes=[plaud_env(B)])  # on disk, after the catalog was built
    ar_archive.pocket_without_audio("my_secret_slug")
    the_plan = plan(ar_archive.root)
    assert f"plaud/{B}" in the_plan.unlisted
    assert all("my_secret_slug" not in u for u in the_plan.unlisted)
    assert len(the_plan.unlisted) == 2


def test_snapshots_keep_audio_routers_order_within_one_second(ar_archive, plaud_env):
    # why: audio-router suffixes same-second snapshots -01, -02, and "-" sorts before "." .
    ar_archive.plaud(A, envelopes=[plaud_env(A), plaud_env(A, name="second"), plaud_env(A, name="third")],
                     stamps=["20260801T161000Z", "20260801T161000Z-01", "20260801T161000Z-02"])
    ar_archive.write_catalog()
    (item,) = plan(ar_archive.root).items
    assert [p.name for p in item.snapshots] == [
        "20260801T161000Z.json", "20260801T161000Z-01.json", "20260801T161000Z-02.json"]


def test_a_row_already_in_the_archive_is_present_by_either_plaud_id_form(ar_archive, plaud_env,
                                                                         writer, make_incoming):
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.plaud(B, envelopes=[plaud_env(B)])
    ar_archive.write_catalog()
    audio_a = ar_archive.root / f"plaud/audio/2026/08/{A}.wav"
    writer.add(make_incoming(audio_a.read_bytes(), name="a.wav", sources=(
        RawSource(kind="plaud", ref="of_" + A, added_at=T0),)))
    writer.add(make_incoming(b"other audio", name="b.wav", sources=(
        RawSource(kind="plaud", ref=B, added_at=T0),)))
    by_uri = {i.row.uri: i for i in plan(ar_archive.root, writer.index).items}
    assert by_uri[f"plaud/{A}"].disposition == "present"
    assert by_uri[f"plaud/{B}"].disposition == "unplaced"
    assert "different audio" in by_uri[f"plaud/{B}"].reason
```

- [ ] **Step 6: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_import_plan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings.sources'`.

- [ ] **Step 7: Write the plan**

`packages/core/src/recordings/sources/__init__.py`:
```python
"""One module per input type (spec §9.0). Stage 2a builds `audio_router`; stage 2b adds `plaud`."""
```

`packages/core/src/recordings/sources/audio_router.py`:
```python
"""Importing audio-router's archive (spec §9.3): read-only, re-runnable and driven by its catalog.

The source is a read-only copy of audio-router's media root (§19), laid out as its
archive/store.py and scripts/build_catalog.py describe (at commit 64612df):

    <source>/raw/<file_id>/<UTC stamp>[-NN].json   snapshots (plaud, recorder)
    <source>/audio/<YYYY>/<MM>/<file_id>.<ext>
    <source>/derived/<file_id>/<kind>-<engine>-<version>-<UTC stamp>.json
    pocket/raw/<id>.json, pocket/audio/…/<id>.<ext>   flat; a closed corpus
    private/…                                         the private tier, registered by file ID
    catalog/catalog.csv, catalog/provenance.json      catalog@5

`catalog/catalog.csv` is the checklist: every row gets exactly one disposition, so the dry run
accounts for every row. Nothing here writes to the source, and no report names a title: the
catalog holds none, and this module never puts one in a reason.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from recordings.archive import sha256_file
from recordings.index import Index
from recordings.media import MEDIA_SUFFIXES

CATALOG = Path("catalog") / "catalog.csv"
PROVENANCE = Path("catalog") / "provenance.json"
REQUIRED_COLUMNS = ("uri", "source", "file_id", "recorded_at_local", "timezone", "time_source",
                    "audio_rel", "audio_sha256", "dup_of", "access")
SOURCES = ("plaud", "recorder", "pocket")
# audio-router's time_source, per source, as recordings' (§6.2); anything else is unplaced.
TIME_SOURCES = {
    ("plaud", "start_at"): "plaud", ("plaud", "created_at"): "plaud",
    ("recorder", "filename"): "metadata", ("recorder", "file mtime (inferred)"): "mtime",
    ("pocket", "recording_at"): "metadata", ("pocket", "created_at"): "ingest",
    ("private", "filename"): "metadata",
}
DISPOSITIONS = ("new", "new-private", "duplicate", "present", "no-audio", "unplaced")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


class CatalogError(RuntimeError):
    """The source has no usable catalog, so nothing can be accounted for."""


@dataclass(frozen=True)
class Row:
    uri: str
    source: str
    file_id: str
    recorded_at_local: str
    timezone: str
    time_source: str
    audio_rel: str
    audio_sha256: str
    dup_of: str
    access: str


@dataclass(frozen=True)
class Item:
    row: Row
    disposition: str
    reason: str = ""
    kind: str = ""  # the recording's origin: plaud, recorder or pocket
    audio: Path | None = None
    sha256: str = ""
    recorded_at: datetime | None = None
    timezone: str = ""
    time_source: str = ""
    snapshots: tuple[Path, ...] = ()
    renditions: tuple[Path, ...] = ()
    present_id: str | None = None
    private: bool = False


@dataclass(frozen=True)
class Plan:
    root: Path
    items: tuple[Item, ...]
    ledger_only: tuple[str, ...] = ()
    unlisted: tuple[str, ...] = ()
    held_on_plaud: int | None = None
    problems: tuple[str, ...] = ()

    def counts(self) -> dict[str, int]:
        found = Counter(i.disposition for i in self.items)
        return {d: found.get(d, 0) for d in DISPOSITIONS}

    @property
    def ok(self) -> bool:
        return not self.problems and not any(i.disposition == "unplaced" for i in self.items)

    def report(self) -> dict:
        """Counts, URIs and reasons: never a title or a transcript."""
        return {
            "catalog_rows": len(self.items),
            "by_disposition": self.counts(),
            "private": sum(i.private for i in self.items if i.disposition != "unplaced"),
            "unplaced": [{"uri": i.row.uri, "reason": i.reason}
                         for i in self.items if i.disposition == "unplaced"],
            "no_audio": [i.row.uri for i in self.items if i.disposition == "no-audio"],
            "ledger_only": list(self.ledger_only),
            "unlisted": list(self.unlisted),
            "held_on_plaud": self.held_on_plaud,
            "problems": list(self.problems),
        }


def read_catalog(root: Path) -> list[Row]:
    path = Path(root) / CATALOG
    if not path.is_file():
        raise CatalogError(
            f"{path}: catalog.csv is missing. The import is driven by audio-router's catalog: "
            "build it there with scripts/build_catalog.py, then copy the archive again.")
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise CatalogError(f"{path} is not audio-router's catalog@5: missing columns "
                               + ", ".join(missing))
        rows = [Row(**{c: (r.get(c) or "").strip() for c in REQUIRED_COLUMNS}) for r in reader]
    repeated = sorted(u for u, n in Counter(r.uri for r in rows).items() if n > 1)
    if repeated:
        raise CatalogError(f"{path} lists {repeated[0]} more than once")
    return rows


def snapshot_order(path: Path) -> tuple[str, str]:
    """audio-router's store._order_key: (stamp, suffix), so `x.json` comes before `x-01.json`."""
    stamp, _, suffix = path.stem.partition("-")
    return (stamp, suffix)


def _provenance(root: Path) -> tuple[dict, list[str]]:
    path = root / PROVENANCE
    if not path.is_file():
        return {}, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}, [f"{path} could not be read"]
    return (data if isinstance(data, dict) else {}), []


def _inside(root: Path, rel: str) -> Path | None:
    path = root / rel
    try:
        return path if path.resolve().is_relative_to(root.resolve()) else None
    except (OSError, ValueError):
        return None


def _when(row: Row, default_tz: str) -> tuple[datetime | None, str, str, str]:
    """(recorded_at, its zone's name, recordings' time_source, a reason when unplaced)."""
    time_source = TIME_SOURCES.get((row.source, row.time_source))
    if time_source is None:
        return None, "", "", f"unknown time source {row.time_source!r} for {row.source}"
    if not row.recorded_at_local:
        return None, "", "", "no recording time in the catalog"
    try:
        when = datetime.fromisoformat(row.recorded_at_local)
    except ValueError:
        return None, "", "", f"recording time {row.recorded_at_local!r} isn't ISO 8601"
    zone = row.timezone or default_tz
    if when.utcoffset() is None:
        try:
            when = when.replace(tzinfo=ZoneInfo(zone))
        except (ZoneInfoNotFoundError, ValueError):
            return None, "", "", f"unknown time zone {zone!r}"
    return when, zone, time_source, ""


def _store_files(root: Path, source: str, fid: str) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    if source == "pocket":
        flat = root / "pocket" / "raw" / f"{fid}.json"
        return ((flat,) if flat.is_file() else ()), ()
    raw = sorted((root / source / "raw" / fid).glob("*.json"), key=snapshot_order)
    derived = sorted((root / source / "derived" / fid).glob("*.json"))
    return tuple(raw), tuple(derived)


def _kind_from_id(fid: str) -> str:
    if _HEX32.fullmatch(fid):
        return "plaud"
    if _HEX64.fullmatch(fid):
        return "recorder"
    if _UUID.fullmatch(fid):
        return "pocket"
    return ""


def _find_private(root: Path, fid: str):
    """The private tier's files for one file ID (stage-2a plan, open question 3): one media file named
    `<file_id>.<ext>`, snapshots in `raw/<file_id>/` or `raw/<file_id>.json`, outputs in
    `derived/<file_id>/`. Returns a reason string when it can't."""
    tier = root / "private"
    if not tier.is_dir():
        return "the catalog lists a private recording but the copy has no private tier"
    media = sorted(p for p in tier.rglob(f"{fid}.*")
                   if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES)
    if len(media) != 1:
        return f"{len(media)} media files named {fid} in the private tier (expected 1)"
    snapshots = [p for p in tier.rglob("*.json") if p.parent.name == fid
                 and p.parent.parent.name == "raw"]
    snapshots += [p for p in tier.rglob(f"{fid}.json") if p.parent.name == "raw"]
    renditions = sorted(p for p in tier.rglob("*.json") if p.parent.name == fid
                        and p.parent.parent.name == "derived")
    first = media[0].relative_to(tier).parts[0]
    kind = first if first in SOURCES else _kind_from_id(fid)
    if not kind:
        return f"can't tell which source private recording {fid} came from"
    return kind, media[0], tuple(sorted(snapshots, key=snapshot_order)), tuple(renditions)


def _present(index: Index | None, kind: str, row: Row, sha: str) -> tuple[str | None, str]:
    """Already in this archive? By Plaud ID (either form) first, then audio-router's own
    reference, then the content hash (§9.3)."""
    if index is None or not index.exists():
        return None, ""
    if kind == "plaud":
        for rid in index.find_by_source("plaud", row.file_id):
            if index.sha256_of(rid) != sha:
                return None, (f"Plaud ID {row.file_id} is already in this archive ({rid}) "
                              "with different audio")
            return rid, ""
    for rid in index.find_by_source("audio_router", row.uri):
        return rid, ""
    return index.find_by_sha256(sha), ""


def _place(root: Path, row: Row, by_uri: dict[str, Row], index: Index | None,
           default_tz: str) -> Item:
    if row.source not in (*SOURCES, "private"):
        return Item(row, "unplaced", f"unknown source {row.source!r}")
    when, zone, time_source, why = _when(row, default_tz)
    if when is None:
        return Item(row, "unplaced", why)
    private = row.source == "private"
    if private:
        found = _find_private(root, row.file_id)
        if isinstance(found, str):
            return Item(row, "unplaced", found)
        kind, audio, snapshots, renditions = found
    else:
        kind = row.source
        if not row.audio_rel:
            later = "; Plaud's copy arrives through the sync" if kind == "plaud" else ""
            return Item(row, "no-audio", f"no audio in audio-router{later}", kind=kind)
        audio = _inside(root, row.audio_rel)
        if audio is None or not audio.is_file():
            return Item(row, "unplaced", f"audio {row.audio_rel} is missing from the copy")
        snapshots, renditions = _store_files(root, kind, row.file_id)
    sha = sha256_file(audio)
    if row.audio_sha256 and sha != row.audio_sha256:
        return Item(row, "unplaced",
                    "the audio's SHA-256 differs from the catalog's: the copy is damaged")
    if row.dup_of and row.dup_of not in by_uri:
        return Item(row, "unplaced", f"dup_of names {row.dup_of}, which the catalog doesn't list")
    present, conflict = _present(index, kind, row, sha)
    if conflict:
        return Item(row, "unplaced", conflict)
    disposition = ("present" if present else "duplicate" if row.dup_of
                   else "new-private" if private else "new")
    return Item(row, disposition, kind=kind, audio=audio, sha256=sha, recorded_at=when,
                timezone=zone, time_source=time_source, snapshots=snapshots,
                renditions=renditions, present_id=present, private=private)


def _unlisted(root: Path, rows: list[Row]) -> list[str]:
    """Recordings on disk with no catalog row, by ID only: a Pocket slug is a title, so it is
    reported by its catalog surrogate, sha256(stem)[:32], as build_catalog.py keys it."""
    listed = {(r.source, r.file_id) for r in rows}
    out = []
    for source in ("plaud", "recorder"):
        audio_root = root / source / "audio"
        if audio_root.is_dir():
            for path in sorted(audio_root.rglob("*")):
                if (path.is_file() and not path.name.startswith(".") and path.suffix != ".part"
                        and (source, path.stem) not in listed):
                    out.append(f"{source}/{path.stem}")
    pocket_raw = root / "pocket" / "raw"
    if pocket_raw.is_dir():
        for path in sorted(pocket_raw.glob("*.json")):
            if path.stem.startswith("_"):
                continue
            fid = (path.stem if _UUID.fullmatch(path.stem)
                   else hashlib.sha256(path.stem.encode("utf-8")).hexdigest()[:32])
            if ("pocket", fid) not in listed:
                out.append(f"pocket/{fid}")
    return out


def plan(root: Path, index: Index | None = None, *,
         default_timezone: str = "America/Vancouver") -> Plan:
    root = Path(root)
    rows = read_catalog(root)
    provenance, problems = _provenance(root)
    if isinstance(provenance.get("rows"), int) and provenance["rows"] != len(rows):
        problems.append(f"catalog.csv has {len(rows)} rows but provenance.json says "
                        f"{provenance['rows']}: the copy may be incomplete")
    by_uri = {r.uri: r for r in rows}
    # Rows that are nobody's duplicate first, so a duplicate always merges into its original.
    items = [_place(root, row, by_uri, index, default_timezone)
             for row in sorted(rows, key=lambda r: (bool(r.dup_of), r.uri))]
    ledger = provenance.get("ledger_only_no_media") or []
    held = ((provenance.get("per_source") or {}).get("plaud") or {}).get("held_count")
    return Plan(root=root, items=tuple(items), ledger_only=tuple(sorted(str(x) for x in ledger)),
                unlisted=tuple(_unlisted(root, rows)),
                held_on_plaud=held if isinstance(held, int) else None, problems=tuple(problems))
```

- [ ] **Step 8: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_import_plan.py packages/core/tests/test_media.py -v`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add packages/core/src/recordings/media.py packages/core/src/recordings/sources packages/core/tests/conftest.py packages/core/tests/test_media.py packages/core/tests/test_import_plan.py
git commit -m "feat(core): the audio-router import plan: every catalog row gets one disposition

A read-only dry run over audio-router's catalog@5 (spec §9.3), matching by Plaud ID before content
hash. The media probe reads WAV with wave and everything else with ffprobe (ffprobe docs checked).
The synthetic audio-router archive is modelled on its store.py and build_catalog.py.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `recordings import-audio-router`

**Checkpoint lens:** data integrity and write safety.

**Files:**
- Create: `packages/core/src/recordings/sources/ar_outputs.py`, `packages/core/src/recordings/disk.py`
- Modify: `packages/core/src/recordings/sources/audio_router.py` (the run), `cli.py` (`import-audio-router`), `config.example.toml` (`[disk]`)
- Test: `packages/core/tests/test_import_run.py`, `packages/core/tests/test_disk.py`

**Interfaces:**
- Consumes:
  - from Task 8: `Plan`, `Item`, `plan()`, `probe()`, `media_kind()`
  - from Task 5: `Writer.add`, `Writer.write_rendition`, `Incoming`
  - from Task 7: `Writer.reconcile_plaud`
  - from Task 6: `sha256_of`
  - from Task 2: `read_sentinel`
- Produces:
  - **`recordings.sources.ar_outputs`:**
    - `IMPORT_VERSION = 1`, `SKIPPED: dict[str, str]`
    - `@dataclass(frozen=True) Mapped(rendition: Rendition | None = None, skipped: str | None = None)`
    - `ar_stamp(path: Path) -> datetime | None`
    - `map_rendition(path: Path, doc: object) -> Mapped`
    - `pocket_outputs(doc: dict, raw: str, fetched_at: datetime) -> list[Rendition]`
  - **`recordings.disk`:**
    - `WARN_FREE_PERCENT = 20.0`, `STOP_FREE_PERCENT = 5.0`
    - `@dataclass(frozen=True) DiskStatus(path, marker, free_bytes, total_bytes, level, message)`, with `.free_percent`
    - `disk_thresholds(cfg: Config) -> dict[str, float]`
    - `disk_status(archive_root: Path, *, expected_uuid: str | None, warn: float = 20.0, stop: float = 5.0) -> DiskStatus`
  - **`recordings.sources.audio_router`:**
    - `class ImportStopped(RuntimeError)`, with `.report`
    - `@dataclass RunReport`, with `.to_dict()`
    - `run(plan: Plan, writer: Writer, *, now: datetime, disk: Callable[[], DiskStatus] | None = None) -> RunReport`
  - **CLI:** `recordings import-audio-router PATH [--dry-run] [--allow-unplaced] [--json]`.

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_disk.py`:
```python
from types import SimpleNamespace

import pytest

from recordings import disk
from recordings.config import ConfigError, load_config
from recordings.disk import disk_status, disk_thresholds
from recordings.sentinel import SENTINEL


def fake_statvfs(free_percent):
    return lambda path: SimpleNamespace(f_bavail=int(free_percent * 10), f_frsize=1024,
                                        f_blocks=1000)


@pytest.mark.parametrize("free, level", [(50.0, "ok"), (19.9, "warn"), (4.9, "stop")])
def test_free_space_levels(new_archive, monkeypatch, free, level):
    root, _ = new_archive
    monkeypatch.setattr(disk.os, "statvfs", fake_statvfs(free))
    status = disk_status(root, expected_uuid=None)
    assert status.level == level and status.marker
    assert status.free_percent == pytest.approx(free, abs=0.1)


def test_a_missing_or_changed_marker_stops_everything(new_archive, monkeypatch):
    # why: §15.1. The disk guard checks the sentinel too: a full-looking empty mount point is
    # not the archive.
    root, _ = new_archive
    monkeypatch.setattr(disk.os, "statvfs", fake_statvfs(90.0))
    assert disk_status(root, expected_uuid="not-the-uuid").level == "stop"
    (root / SENTINEL).unlink()
    status = disk_status(root, expected_uuid=None)
    assert (status.level, status.marker) == ("stop", False) and "mounted" in status.message


def test_the_thresholds_come_from_config(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[disk]\nwarn_free_percent = 30\nstop_free_percent = 10\n", encoding="utf-8")
    assert disk_thresholds(load_config({"RECORDINGS_CONFIG": str(cfg_file)})) == {
        "warn": 30.0, "stop": 10.0}
    cfg_file.write_text("[disk]\nwarn_free_percent = 5\nstop_free_percent = 10\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="stop_free_percent"):
        disk_thresholds(load_config({"RECORDINGS_CONFIG": str(cfg_file)}))
```

`packages/core/tests/test_import_run.py`:
```python
import json
from datetime import datetime, timezone

import pytest

from recordings.cli import main
from recordings.disk import DiskStatus
from recordings.selfdoc import validate
from recordings.sources import audio_router
from recordings.sources.ar_outputs import map_rendition, pocket_outputs
from recordings.sources.audio_router import ImportStopped, plan, run
from recordings.writer import Writer

A = "a" * 32
B = "b" * 32
C = "c" * 32
P = "e" * 32
UUID = "11111111-1111-4111-8111-111111111111"
NOW = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)

WORDS = {"schema": "audio-router/rendition@1", "kind": "words", "engine": "mlx-whisper",
         "version": "large-v3-turbo@a4aaeec", "file_id": A, "inputs": {"audio_sha256": "x",
                                                                        "sample_rate": 16000},
         "meta": {"model": "mlx-community/whisper-large-v3-turbo", "language": "en"},
         "payload": {"words": [{"word": "Hello", "start": 0.0, "end": 0.5, "prob": 0.9},
                               {"word": "there.", "start": 0.5, "end": 1.4, "prob": 0.9},
                               {"word": "Hi.", "start": 1.6, "end": 2.4, "prob": 0.9}],
                     "segments": [{"id": 0, "start": 0.0, "end": 1.5, "text": " Hello there."},
                                  {"id": 1, "start": 1.5, "end": 2.6, "text": " Hi."}]}}
TURNS = {"kind": "turns", "engine": "pyannote", "version": "community-1@abc", "inputs": {},
         "payload": {"turns": [{"start": 0.0, "end": 1.5, "speaker": "SPEAKER_00"}],
                     "overlap_turns": []}}
MERGED = {"kind": "merged", "engine": "audio-router", "version": "m2+x+y", "inputs": {},
          "payload": {"turns": [{"speaker": "SPEAKER_00", "start": 0.0, "end": 1.5,
                                 "text": "Hello there.", "inferred": False,
                                 "words": [{"word": "Hello", "start": 0.0, "end": 0.5}]}],
                      "text": "[00:00] SPEAKER_00: Hello there."}}
SUMMARY = {"kind": "summary", "engine": "local/qwen3.6-35b-a3b", "version": "qwen+p3",
           "inputs": {}, "payload": {"title": "Greetings", "summary_md": "Two people meet.",
                                     "actions": [{"owner_raw": "Ada", "text": "Say goodbye"}]}}
PLAUD_TURNS = {"kind": "turns", "engine": "plaud", "version": "v1", "inputs": {}, "payload": {}}
EMBEDDINGS = {"kind": "embeddings", "engine": "pyannote", "version": "x", "inputs": {},
              "payload": {}}


def build(ar, plaud_env):
    ar.plaud(A, envelopes=[plaud_env(A, name="Week 4"), plaud_env(A, name="Week 4, edited")],
             derived={"words-mlx_whisper-large_v3_turbo@a4aaeec-20260801T170000Z.json": WORDS,
                      "turns-pyannote-community_1@abc-20260801T170100Z.json": TURNS,
                      "merged-audio_router-m2+x+y-20260801T170200Z.json": MERGED,
                      "summary-local_qwen3.6_35b_a3b-qwen+p3-20260801T170300Z.json": SUMMARY,
                      "turns-plaud-v1-20260801T170400Z.json": PLAUD_TURNS,
                      "embeddings-pyannote-x-20260801T170500Z.json": EMBEDDINGS})
    ar.plaud(B, envelopes=[plaud_env(B)], same_audio_as=f"plaud/audio/2026/08/{A}.wav",
             dup_of=f"plaud/{A}")
    ar.plaud(C, envelopes=[plaud_env(C)], audio=False)
    ar.recorder()
    ar.pocket(UUID)
    ar.private(P, envelopes=[plaud_env(P, name="A PRIVATE TITLE")])
    ar.write_catalog()


def test_the_import_maps_every_kind_of_recording(ar_archive, plaud_env, writer):
    build(ar_archive, plaud_env)
    before = ar_archive.tree_hash()
    report = run(plan(ar_archive.root, writer.index), writer, now=NOW)
    assert report.failed == []
    assert (report.created, report.merged) == (4, 1)  # B's audio is A's: one recording, two IDs
    assert ar_archive.tree_hash() == before  # §9.3: it never changes the source
    assert validate(writer.root) == []
    recs = {s.ref: r for r in writer.archive.iter_recordings() for s in r.sources}

    a = recs[A]
    assert {(s.kind, s.ref) for s in a.sources} == {
        ("plaud", A), ("plaud", B), ("audio_router", f"plaud/{A}"), ("audio_router", f"plaud/{B}")}
    assert a.title == "Week 4, edited" and a.title_by == "plaud" and a.plaud.title_seen
    assert (a.media.duration_ms, a.media.sample_rate, a.media.channels) == (2600, 16000, 1)
    outputs = {(r.kind, r.engine, r.note_type) for _, r in writer.archive.renditions(a.id)}
    assert ("transcript", "mlx-whisper", None) in outputs
    assert ("transcript", "audio-router", None) in outputs
    assert ("speakers", "pyannote", None) in outputs
    assert ("notes", "local/qwen3.6-35b-a3b", "audio-router-summary") in outputs
    assert ("transcript", "plaud", None) in outputs and ("notes", "plaud", "plaud-summary") in outputs
    assert not any(r.engine == "plaud" and r.kind == "speakers"
                   for _, r in writer.archive.renditions(a.id))  # audio-router's Plaud turns: not copied
    assert report.skipped_outputs == {
        "audio-router's Plaud outputs are rebuilt by reconcile": 1,
        "voice data never enters the archive (spec §7.6)": 1}

    assert [t.tag for t in recs[P].tags] == ["private"] and recs[P].tags[0].by == "you"
    pocket = recs[UUID]
    assert pocket.title == "Pocket chat" and pocket.time_source == "metadata"
    assert {r.engine for _, r in writer.archive.renditions(pocket.id)} == {"pocket"}
    recorder = next(r for r in writer.archive.iter_recordings()
                    if any(s.kind == "recorder" for s in r.sources))
    assert writer.archive.renditions(recorder.id) == []  # its prose transcript stays in source/
    assert C not in recs  # no audio: not imported


def test_running_the_import_again_adds_nothing(ar_archive, plaud_env, writer):
    build(ar_archive, plaud_env)
    run(plan(ar_archive.root, writer.index), writer, now=NOW)
    files = sorted(p.relative_to(writer.root) for p in writer.root.rglob("*") if p.is_file())
    again_plan = plan(ar_archive.root, writer.index)
    assert again_plan.counts()["present"] == 5
    again = run(again_plan, writer, now=datetime(2026, 10, 10, tzinfo=timezone.utc))
    assert (again.created, again.sources_added, again.renditions_added, again.plaud_outputs) == (0, 0, 0, 0)
    assert sorted(p.relative_to(writer.root) for p in writer.root.rglob("*") if p.is_file()) == files


def test_an_import_killed_halfway_finishes_cleanly_when_run_again(ar_archive, plaud_env, writer,
                                                                   monkeypatch):
    # Review Focus 2: some recordings done, a .tmp/ assembly left behind, then a re-run.
    build(ar_archive, plaud_env)
    real_add = Writer.add
    calls = []

    def killed_on_the_third(self, incoming):
        calls.append(1)
        if len(calls) == 3:
            stray = self.root / ".tmp" / "20260802T100000-0700_deadbeef.0123"
            (stray / "source").mkdir(parents=True)
            raise KeyboardInterrupt("killed")
        return real_add(self, incoming)

    monkeypatch.setattr(Writer, "add", killed_on_the_third)
    with pytest.raises(KeyboardInterrupt):
        run(plan(ar_archive.root, writer.index), writer, now=NOW)
    monkeypatch.undo()
    fresh = Writer(writer.root, writer.state, writer.index, writer_id="test")
    report = run(plan(ar_archive.root, fresh.index), fresh, now=NOW)
    assert report.failed == []
    assert len(list(fresh.archive.iter_recordings())) == 4
    assert not (fresh.root / ".tmp").exists()
    assert validate(fresh.root) == []


def test_a_degraded_or_unreadable_snapshot_is_kept_but_never_used(ar_archive, plaud_env, writer):
    # Review Focus 3.
    ar_archive.plaud(A, envelopes=[plaud_env(A), plaud_env(A, link_error=True), "{not json"])
    ar_archive.write_catalog()
    report = run(plan(ar_archive.root, writer.index), writer, now=NOW)
    (rec,) = writer.archive.iter_recordings()
    assert sum(s.kind == "plaud" for s in rec.sources) == 3  # raw is raw: all three are kept
    assert report.skipped_snapshots == 2
    transcripts = [r for _, r in writer.archive.renditions(rec.id)
                   if r.kind == "transcript" and r.engine == "plaud"]
    assert len(transcripts) == 1 and transcripts[0].inputs["source"].endswith("20260801T161000Z.json")


def test_the_import_stops_when_the_disk_guard_says_stop(ar_archive, plaud_env, writer):
    build(ar_archive, plaud_env)
    stop = DiskStatus(path="x", marker=True, free_bytes=1, total_bytes=100, level="stop",
                      message="1.0% free: below 5%, new imports stop")
    with pytest.raises(ImportStopped, match="new imports stop") as caught:
        run(plan(ar_archive.root, writer.index), writer, now=NOW, disk=lambda: stop)
    assert caught.value.report.created == 0
    assert list(writer.archive.iter_recordings()) == []


def test_mapping_audio_routers_outputs(tmp_path):
    words = map_rendition(tmp_path / "words-mlx_whisper-v-20260801T170000Z.json", WORDS).rendition
    assert words.kind == "transcript" and words.engine == "mlx-whisper"
    assert words.model == "mlx-community/whisper-large-v3-turbo"
    assert words.inputs == {"audio_sha256": "x", "sample_rate": "16000"}
    assert [[w["word"] for w in s["words"]] for s in words.payload["segments"]] == [
        ["Hello", "there."], ["Hi."]]
    summary = map_rendition(tmp_path / "summary-x-v-20260801T170300Z.json", SUMMARY).rendition
    assert summary.payload["markdown"] == ("# Greetings\n\nTwo people meet.\n\n## Actions\n\n"
                                           "- Say goodbye (Ada)")
    assert map_rendition(tmp_path / "words-x-v-nostamp.json", WORDS).skipped
    assert map_rendition(tmp_path / "x-20260801T170000Z.json", ["not", "a", "dict"]).skipped
    assert map_rendition(tmp_path / "x-20260801T170000Z.json", {**WORDS, "kind": "novel"}).skipped


def test_pocket_segments_in_milliseconds_are_recognised():
    doc = {"duration": 2.0, "transcript": {"segments": [{"start": 0, "end": 1500, "text": "Hi"}]},
           "summarizations": {}}
    (transcript,) = pocket_outputs(doc, "source/pocket-x.json", NOW)
    assert transcript.payload["segments"][0]["end"] == 1.5 and transcript.meta["time_unit"] == "ms"


def _cli_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{tmp_path / "archive"}"\nwriter_id = "test"\n'
        f'[state]\npath = "{tmp_path / "state"}"\n[index]\npath = "{tmp_path / "index.db"}"\n',
        encoding="utf-8")
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX"):
        monkeypatch.delenv(name, raising=False)


def test_the_cli_dry_run_then_import(ar_archive, plaud_env, tmp_path, monkeypatch, capsys):
    build(ar_archive, plaud_env)
    _cli_config(tmp_path, monkeypatch)
    assert main(["init", "--json"]) == 0
    capsys.readouterr()
    assert main(["import-audio-router", str(ar_archive.root), "--dry-run", "--json"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True and dry["by_disposition"]["new"] == 3
    assert not list((tmp_path / "archive" / "recordings").iterdir())  # a dry run writes nothing
    assert main(["import-audio-router", str(ar_archive.root), "--json"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["imported"]["created"] == 4
    assert "A PRIVATE TITLE" not in out and "Week 4" not in out  # Review Focus 4


def test_the_cli_refuses_unplaced_rows_unless_told(ar_archive, plaud_env, tmp_path, monkeypatch,
                                                    capsys):
    build(ar_archive, plaud_env)
    ar_archive.rows.append({**ar_archive.rows[0], "uri": "zoom/z1", "source": "zoom"})
    ar_archive.write_catalog()
    _cli_config(tmp_path, monkeypatch)
    main(["init", "--json"])
    capsys.readouterr()
    assert main(["import-audio-router", str(ar_archive.root), "--json"]) == 1
    assert "--allow-unplaced" in json.loads(capsys.readouterr().out)["error"]
    assert main(["import-audio-router", str(ar_archive.root), "--allow-unplaced", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["imported"]["created"] == 4


def test_the_run_module_reports_counts_only():
    assert set(audio_router.RunReport().to_dict()) == {
        "created", "merged", "sources_added", "renditions_added", "plaud_outputs",
        "held_removals", "skipped_snapshots", "skipped_outputs", "unprobed", "failed"}
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_import_run.py packages/core/tests/test_disk.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings.sources.ar_outputs'` (and
`recordings.disk`).

- [ ] **Step 3: Write the disk guard**

`packages/core/src/recordings/disk.py`:
```python
"""The disk guard (spec §12.5, §15.1), checked before anything writes in bulk, and by Status.

It checks two things. First the marker: the archive's sentinel, so the disk is mounted and it is
the right archive. Then the free space on the archive's disk: a warning below 20% free, and a stop
below 5%, where new imports stop.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from recordings.config import Config, ConfigError
from recordings.sentinel import SentinelError, read_sentinel

WARN_FREE_PERCENT = 20.0
STOP_FREE_PERCENT = 5.0


@dataclass(frozen=True)
class DiskStatus:
    path: str
    marker: bool
    free_bytes: int | None
    total_bytes: int | None
    level: Literal["ok", "warn", "stop"]
    message: str

    @property
    def free_percent(self) -> float | None:
        if not self.total_bytes or self.free_bytes is None:
            return None
        return self.free_bytes * 100 / self.total_bytes


def disk_thresholds(cfg: Config) -> dict[str, float]:
    section = cfg.data.get("disk", {})
    warn = section.get("warn_free_percent", WARN_FREE_PERCENT)
    stop = section.get("stop_free_percent", STOP_FREE_PERCENT)
    numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (warn, stop))
    if not numbers or not 0 < stop < warn < 100:
        raise ConfigError("[disk] stop_free_percent and warn_free_percent must be numbers with "
                          "0 < stop_free_percent < warn_free_percent < 100")
    return {"warn": float(warn), "stop": float(stop)}


def disk_status(archive_root: Path, *, expected_uuid: str | None,
                warn: float = WARN_FREE_PERCENT, stop: float = STOP_FREE_PERCENT) -> DiskStatus:
    root = Path(archive_root)
    marker, message = True, ""
    try:
        identity = read_sentinel(root)
        if expected_uuid is not None and identity.uuid != expected_uuid:
            marker, message = False, "the archive's UUID changed: this is not the archive expected"
    except SentinelError as exc:
        marker, message = False, str(exc)
    free = total = None
    try:
        st = os.statvfs(root)
        free, total = st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize
    except (OSError, AttributeError):  # missing folder, or no statvfs (the Pages demo)
        pass
    status = DiskStatus(path=str(root), marker=marker, free_bytes=free, total_bytes=total,
                        level="ok", message="")
    pct = status.free_percent
    if not marker:
        level = "stop"
    elif pct is not None and pct < stop:
        level, message = "stop", f"{pct:.1f}% free: below {stop:g}%, new imports stop"
    elif pct is not None and pct < warn:
        level, message = "warn", f"{pct:.1f}% free: below {warn:g}%"
    else:
        level, message = "ok", (f"{pct:.1f}% free" if pct is not None else "free space unknown")
    return DiskStatus(path=str(root), marker=marker, free_bytes=free, total_bytes=total,
                      level=level, message=message)
```

Add to `config.example.toml`, after `[index]`:
```toml
[disk]                                  # stage 2a: the disk guard (spec §12.5)
warn_free_percent = 20                  # Status warns, and an alert goes out, below this
stop_free_percent = 5                   # new imports stop below this
```

- [ ] **Step 4: Write the output mapping and the run**

`packages/core/src/recordings/sources/ar_outputs.py`:
```python
"""audio-router's outputs, and Pocket's payload, as recordings outputs (spec §9.3).

| audio-router                           | recordings                                          |
|----------------------------------------|-----------------------------------------------------|
| words, merged                          | transcript, keeping engine and version              |
| turns (pyannote)                       | speakers                                            |
| summary                                | notes, note type audio-router-summary               |
| anything whose engine is plaud         | skipped: reconcile rebuilds Plaud's outputs (§9.1.1) |
| fingerprint, embeddings                | skipped: voice data never enters the archive (§7.6)  |

Payload shapes are audio-router's worker.py and stages/ at commit 64612df. Pocket keeps its
transcript and summaries inside its raw payload. They become outputs with engine `pocket`, built
from source/ as reconcile builds Plaud's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from recordings.models import Rendition
from recordings.plaud.normalise import canonical_json, sha256_of

IMPORT_VERSION = 1
SKIPPED = {
    "plaud": "audio-router's Plaud outputs are rebuilt by reconcile",
    "fingerprint": "content fingerprints are not an output kind",
    "embeddings": "voice data never enters the archive (spec §7.6)",
}


@dataclass(frozen=True)
class Mapped:
    rendition: Rendition | None = None
    skipped: str | None = None  # a fixed reason, never content


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _timed(item: Any) -> bool:
    return (isinstance(item, dict) and _number(item.get("start")) and _number(item.get("end"))
            and 0 <= item["start"] <= item["end"])


def ar_stamp(path: Path) -> datetime | None:
    """The UTC stamp a rendition's file name ends with (audio-router's store._rendition_stamp)."""
    try:
        return datetime.strptime(path.stem.rsplit("-", 1)[-1], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def _words(items: Any) -> list[dict]:
    return [{"word": str(w.get("word") or ""), "start": float(w["start"]), "end": float(w["end"])}
            for w in items or [] if _timed(w)]


def _words_transcript(payload: dict) -> dict | None:
    segments = [s for s in payload.get("segments") or [] if _timed(s)]
    if not segments:
        return None
    words = sorted(_words(payload.get("words")), key=lambda w: (w["start"] + w["end"]) / 2)
    out, i = [], 0
    for n, seg in enumerate(segments):
        last = n == len(segments) - 1
        inside = []
        while i < len(words):
            middle = (words[i]["start"] + words[i]["end"]) / 2
            if middle < seg["end"] or last:
                if middle >= seg["start"] or n == 0:
                    inside.append(words[i])
                i += 1
            else:
                break
        out.append({"start": float(seg["start"]), "end": float(seg["end"]),
                    "text": str(seg.get("text") or "").strip(), "words": inside})
    return {"segments": out}


def _merged_transcript(payload: dict) -> dict | None:
    turns = [t for t in payload.get("turns") or [] if _timed(t)]
    if not turns:
        return None
    return {"segments": [{"start": float(t["start"]), "end": float(t["end"]),
                          "text": str(t.get("text") or "").strip(),
                          **({"speaker": str(t["speaker"])} if t.get("speaker") else {}),
                          "words": _words(t.get("words"))} for t in turns]}


def _summary_markdown(payload: dict) -> str:
    parts = []
    if isinstance(payload.get("title"), str) and payload["title"].strip():
        parts.append(f"# {payload['title'].strip()}")
    if isinstance(payload.get("summary_md"), str) and payload["summary_md"].strip():
        parts.append(payload["summary_md"].strip())
    actions = [a for a in payload.get("actions") or []
               if isinstance(a, dict) and str(a.get("text") or "").strip()]
    if actions:
        lines = []
        for action in actions:
            owner = str(action.get("owner_raw") or "").strip()
            lines.append(f"- {str(action['text']).strip()}" + (f" ({owner})" if owner else ""))
        parts.append("## Actions\n\n" + "\n".join(lines))
    return "\n\n".join(parts)


def map_rendition(path: Path, doc: object) -> Mapped:
    if not isinstance(doc, dict):
        return Mapped(skipped="not a JSON object")
    kind, engine, version = doc.get("kind"), doc.get("engine"), doc.get("version")
    if not all(isinstance(x, str) and x for x in (kind, engine, version)):
        return Mapped(skipped="no kind, engine or version")
    if engine.split("/")[0] == "plaud":
        return Mapped(skipped=SKIPPED["plaud"])
    if kind in ("fingerprint", "embeddings"):
        return Mapped(skipped=SKIPPED[kind])
    created = ar_stamp(path)
    if created is None:
        return Mapped(skipped="no UTC stamp in its file name")
    payload = doc["payload"] if isinstance(doc.get("payload"), dict) else {}
    meta_in = doc["meta"] if isinstance(doc.get("meta"), dict) else {}
    inputs = {k: str(v) for k, v in (doc.get("inputs") or {}).items()
              if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
    common = dict(engine=engine, version=version, created_at=created, inputs=inputs,
                  meta={"imported_from": "audio-router", "file": path.name,
                        "audio_router_meta": meta_in})
    try:
        if kind == "words":
            body = _words_transcript(payload)
            model = meta_in.get("model") if isinstance(meta_in.get("model"), str) else None
            return (Mapped(Rendition(kind="transcript", model=model, payload=body, **common))
                    if body else Mapped(skipped="a transcript with no timed segments"))
        if kind == "merged":
            body = _merged_transcript(payload)
            return (Mapped(Rendition(kind="transcript", payload=body, **common))
                    if body else Mapped(skipped="a transcript with no timed segments"))
        if kind == "turns":
            return Mapped(Rendition(kind="speakers", payload={
                "turns": list(payload.get("turns") or []),
                "overlap_turns": list(payload.get("overlap_turns") or [])}, **common))
        if kind == "summary":
            markdown = _summary_markdown(payload)
            return (Mapped(Rendition(kind="notes", note_type="audio-router-summary",
                                     model=engine.split("/")[-1], payload={"markdown": markdown},
                                     **common))
                    if markdown else Mapped(skipped="an empty summary"))
    except ValidationError:
        return Mapped(skipped="does not fit the output schema")
    return Mapped(skipped=f"an output kind recordings has no place for ({kind})")


def pocket_outputs(doc: dict, raw: str, fetched_at: datetime) -> list[Rendition]:
    """Pocket's transcript and summaries, from its raw payload (stage-2a plan, open question 5: times are read
    as seconds, or as milliseconds when the last segment ends past twice the duration)."""
    out = []
    transcript = doc.get("transcript") if isinstance(doc.get("transcript"), dict) else {}
    rows = [s for s in transcript.get("segments") or [] if _timed(s)]
    version = f"pocket@{IMPORT_VERSION}"
    if rows:
        duration = doc.get("duration")
        in_ms = _number(duration) and duration > 0 and max(r["end"] for r in rows) > 2 * duration
        scale = 1000.0 if in_ms else 1.0
        segments = [{"start": r["start"] / scale, "end": r["end"] / scale,
                     "text": str(r.get("text") or "").strip(),
                     **({"speaker": str(r["speaker"])} if r.get("speaker") else {})} for r in rows]
        out.append(Rendition(kind="transcript", engine="pocket", model="pocket", version=version,
                             created_at=fetched_at,
                             inputs={"source": raw, "part_sha256": sha256_of(rows)},
                             meta={"time_unit": "ms" if in_ms else "s"},
                             payload={"segments": segments}))
    summaries = doc.get("summarizations") if isinstance(doc.get("summarizations"), dict) else {}
    n = 0
    for key in sorted(summaries):
        value = summaries[key]
        text = (value["text"] if isinstance(value, dict) and isinstance(value.get("text"), str)
                else canonical_json(value))
        if not text.strip():
            continue
        n += 1
        out.append(Rendition(kind="notes",
                             note_type="pocket-summary" if n == 1 else f"pocket-summary-{n}",
                             engine="pocket", model="pocket", version=version,
                             created_at=fetched_at,
                             inputs={"source": raw, "part_sha256": sha256_of(value)},
                             payload={"markdown": text.strip()}))
    return out
```

Append to `packages/core/src/recordings/sources/audio_router.py` (and **add** the imports
`from collections.abc import Callable`, `from dataclasses import asdict, field`,
`from datetime import timezone`, `from recordings.archive import RawSource`,
`from recordings.disk import DiskStatus`, `from recordings.locks import LockTimeout`,
`from recordings.media import media_kind, probe`, `from recordings.models import TagRef`,
`from recordings.sources.ar_outputs import map_rendition, pocket_outputs` and
`from recordings.writer import Incoming, Writer, WriterError`):
```python
@dataclass
class RunReport:
    created: int = 0
    merged: int = 0
    sources_added: int = 0
    renditions_added: int = 0
    plaud_outputs: int = 0
    held_removals: int = 0
    skipped_snapshots: int = 0
    skipped_outputs: dict[str, int] = field(default_factory=dict)
    unprobed: int = 0
    failed: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class ImportStopped(RuntimeError):
    """The disk guard said stop. `.report` is what was done before it did."""

    def __init__(self, message: str, report: RunReport) -> None:
        super().__init__(message)
        self.report = report


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _fetched_at(path: Path) -> datetime:
    """A snapshot's stamp is when audio-router fetched it; Pocket's flat file has only mtime."""
    try:
        return datetime.strptime(path.stem.partition("-")[0], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


def _title(item: Item) -> str:
    for path in reversed(item.snapshots):
        doc = _read_json(path)
        key = "title" if item.kind == "pocket" else "name"
        if isinstance(doc, dict) and isinstance(doc.get(key), str) and doc[key].strip():
            return doc[key].strip()
    return "Untitled recording"


def _import_one(item: Item, writer: Writer, *, now: datetime, report: RunReport) -> None:
    sources = [RawSource(kind=item.kind, ref=item.row.file_id, added_at=now,
                         fetched_at=_fetched_at(path), payload=path.read_bytes())
               for path in item.snapshots]
    sources.append(RawSource(kind="audio_router", ref=item.row.uri, added_at=now))
    renditions = []
    for path in item.renditions:
        mapped = map_rendition(path, _read_json(path))
        if mapped.rendition is not None:
            renditions.append(mapped.rendition)
        else:
            report.skipped_outputs[mapped.skipped] = report.skipped_outputs.get(mapped.skipped, 0) + 1
    probed = probe(item.audio)
    if probed is None:
        report.unprobed += 1
    added = writer.add(Incoming(
        media=item.audio, recorded_at=item.recorded_at, timezone_name=item.timezone,
        time_source=item.time_source, title=_title(item),
        kind=media_kind(item.audio, probed) or "audio", sources=tuple(sources),
        title_by="plaud" if item.kind == "plaud" else None,
        duration_ms=probed.duration_ms if probed else None,
        sample_rate=probed.sample_rate if probed else None,
        channels=probed.channels if probed else None,
        tags=(TagRef(tag="private", by="you"),) if item.private else (),
        renditions=tuple(renditions)))
    report.created += added.created
    report.merged += not added.created
    report.sources_added += added.sources_added
    report.renditions_added += added.renditions_added
    rid = added.recording.id
    if item.kind == "plaud":
        reconciled = writer.reconcile_plaud(rid)
        report.plaud_outputs += reconciled.written
        report.held_removals += len(reconciled.held)
        report.skipped_snapshots += len(reconciled.skipped)
    elif item.kind == "pocket":
        rec = writer.archive.load(rid)
        folder = writer.archive.path_for(rid)
        for source in rec.sources:
            doc = _read_json(folder / source.raw) if source.kind == "pocket" and source.raw else None
            if isinstance(doc, dict):
                for rendition in pocket_outputs(doc, source.raw, source.fetched_at or source.added_at):
                    if writer.write_rendition(rid, rendition) is not None:
                        report.renditions_added += 1


def run(plan: Plan, writer: Writer, *, now: datetime,
        disk: Callable[[], DiskStatus] | None = None) -> RunReport:
    """Import every placeable row. Re-runnable: what is already there is matched and skipped."""
    report = RunReport()
    for item in plan.items:
        if item.disposition in ("no-audio", "unplaced"):
            continue
        if disk is not None:
            status = disk()
            if status.level == "stop":
                raise ImportStopped(status.message, report)
        try:
            _import_one(item, writer, now=now, report=report)
        except (WriterError, LockTimeout, OSError, ValueError) as exc:
            report.failed.append({"uri": item.row.uri, "reason": str(exc)[:300]})
    return report
```

- [ ] **Step 5: Add the command**

In `packages/core/src/recordings/cli.py`:
1. **Add** the imports:
   ```python
   from datetime import datetime, timezone

   from recordings.disk import disk_status, disk_thresholds
   from recordings.index import Index
   from recordings.sources.audio_router import CatalogError, ImportStopped, plan, run
   ```
2. **Add** to `build_parser`:
   ```python
       p = sub.add_parser("import-audio-router",
                          help="import a read-only copy of audio-router's archive (dry run first)")
       p.add_argument("path", type=Path, help="the copy of audio-router's media root")
       p.add_argument("--dry-run", action="store_true", help="report what would happen; write nothing")
       p.add_argument("--allow-unplaced", action="store_true",
                      help="import what can be placed even when some rows can't")
       p.add_argument("--json", action="store_true")
   ```
3. **Add**:
   ```python
   def _matching_index(cfg: config.Config) -> Index | None:
       """The archive's index, for a dry run's matches, when it is this archive's."""
       if cfg.index_path is None or cfg.archive_path is None:
           return None
       try:
           identity = read_sentinel(cfg.archive_path)
       except SentinelError:
           return None
       index = Index(cfg.index_path)
       return index if index.archive_uuid() == identity.uuid else None


   def cmd_import_audio_router(args: argparse.Namespace) -> int:
       cfg = _config(args.json)
       if isinstance(cfg, int):
           return cfg
       if args.dry_run:
           index = _matching_index(cfg)
           try:
               the_plan = plan(args.path, index, default_timezone=cfg.default_timezone)
           except CatalogError as exc:
               return _fail(str(exc), args.json, 1)
           _emit({**the_plan.report(), "dry_run": True,
                  "matched_against_archive": index is not None}, args.json)
           return 0 if the_plan.ok else 1
       writer = _writer(args.json)
       if isinstance(writer, int):
           return writer
       try:
           the_plan = plan(args.path, writer.index, default_timezone=cfg.default_timezone)
           thresholds = disk_thresholds(cfg)
       except (CatalogError, config.ConfigError) as exc:
           return _fail(str(exc), args.json, 1)
       if the_plan.problems:
           return _fail("; ".join(the_plan.problems), args.json, 1)
       unplaced = the_plan.counts()["unplaced"]
       if unplaced and not args.allow_unplaced:
           return _fail(f"{unplaced} catalog rows can't be placed (see --dry-run): fix them, or "
                        "pass --allow-unplaced to import the rest", args.json, 1)

       def guard():
           return disk_status(writer.root, expected_uuid=writer.identity.uuid, **thresholds)

       try:
           result = run(the_plan, writer, now=datetime.now(timezone.utc), disk=guard)
       except ImportStopped as exc:
           _emit({**the_plan.report(), "imported": exc.report.to_dict(), "stopped": str(exc)},
                 args.json)
           return 1
       _emit({**the_plan.report(), "imported": result.to_dict()}, args.json)
       return 1 if result.failed else 0
   ```
4. **Add** `"import-audio-router": cmd_import_audio_router,` to `commands`.

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_import_run.py packages/core/tests/test_disk.py -v && uv run pytest`
Expected: all pass, and the full suite stays green.

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/recordings packages/core/tests/test_import_run.py packages/core/tests/test_disk.py config.example.toml
git commit -m "feat(core): recordings import-audio-router, re-runnable, with the disk guard

Maps audio-router's recordings and outputs per spec §9.3: Plaud outputs rebuilt by reconcile,
voice data skipped, the private tier tagged private, duplicates merged, nothing written to the
source and no title in any report.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 10: Cross-site writes, host names and demo isolation

**Checkpoint lens:** security and privacy.

The Host allow-list (stage 1) stops DNS rebinding, but not a foreign page posting to the app's
real name (§15). From 2a, every request that could change data must:
- not be cross-site, judged by `Origin` or `Sec-Fetch-Site`
- carry exactly `Content-Type: application/json`

2a adds no write route to the UI, so the guard wraps the whole app, and every later route gets it
without opting in. Shiny's own file-upload POST would be refused by it; the app uses none, and
stage 5's uploads go through FastAPI. This task also carries three stage-1 items:
- `allowed_hosts` entries with a port or scheme are refused
- the demo guard test (§17)
- demo mode's temporary folders are cleaned up

**Files:**
- Modify: `packages/ui/src/recordings_ui/hosts.py` (`WriteGuard`), `app.py`, `settings.py`
- Modify: `packages/core/src/recordings/config.py` (`normalise_host`)
- Test: `packages/ui/tests/test_writeguard.py`, `packages/ui/tests/test_demo_guard.py`, `packages/core/tests/test_config.py`, `packages/ui/tests/test_settings.py`

**Interfaces:**
- Consumes: `recordings_ui.hosts.HostGuard`, `origin_name`, `_header` (stage 1), and `Settings` (Task 5).
- Produces:
  - **`recordings_ui.hosts`:** `SAFE_METHODS`, and `class WriteGuard(app: ASGIApp, allowed: frozenset[str])`, which answers 403 for a cross-site request and 415 for any content type that isn't JSON.
  - **`recordings.config.normalise_host(value: str) -> str`**, raising `ValueError`.
  - **`create_app` returns** `HostGuard(WriteGuard(api, hosts), hosts)`.

- [ ] **Step 1: Write the failing tests**

`packages/ui/tests/test_writeguard.py`:
```python
import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from recordings_ui import runtime
from recordings_ui.app import create_app
from recordings_ui.hosts import LOCAL_HOSTS, WriteGuard
from recordings_ui.settings import Settings


async def echo(request):
    return JSONResponse({"ok": True})


@pytest.fixture
def client():
    app = Starlette(routes=[Route("/api/thing", echo, methods=["GET", "POST", "DELETE"])])
    with TestClient(WriteGuard(app, LOCAL_HOSTS), base_url="http://localhost") as c:
        yield c


JSON = {"Content-Type": "application/json"}


def test_a_same_origin_json_write_passes(client):
    assert client.post("/api/thing", json={}, headers={"Origin": "http://localhost:8000",
                                                       "Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_a_write_from_no_browser_passes(client):
    # No Origin and no Sec-Fetch-Site: curl, the CLI's --remote, a script. No website behind it.
    assert client.post("/api/thing", content=b"{}", headers=JSON).status_code == 200


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},  # a sibling subdomain is another origin
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Origin": "http://localhost:8000", "Sec-Fetch-Site": "cross-site"},
], ids=["cross-site", "same-site", "foreign-origin", "null-origin", "mixed"])
def test_a_cross_site_write_is_refused(client, headers):
    # why: §15. A page on another site can post to the app's real host name.
    response = client.post("/api/thing", content=b"{}", headers={**JSON, **headers})
    assert response.status_code == 403


@pytest.mark.parametrize("ctype", ["text/plain", "application/x-www-form-urlencoded",
                                   "multipart/form-data; boundary=x",
                                   "application/json; charset=utf-8", ""])
def test_anything_but_exactly_json_is_refused(client, ctype):
    # why: §15. A browser sends text/plain or a form cross-site without a CORS preflight.
    headers = {"Content-Type": ctype} if ctype else {}
    assert client.post("/api/thing", content=b"{}", headers=headers).status_code == 415
    assert client.delete("/api/thing", headers=headers).status_code == 415


def test_reads_are_not_affected(client):
    assert client.get("/api/thing", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_the_real_app_is_wrapped(demo_archive):
    app = create_app(Settings(archive=demo_archive, demo=True))
    with TestClient(app, base_url="http://localhost") as c:
        assert c.post("/", content=b"{}", headers={**JSON, "Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert c.post("/healthz", content=b"x", headers={"Content-Type": "text/plain"}).status_code == 415
    runtime.configure(None)
```

`packages/ui/tests/test_demo_guard.py`:
```python
"""Demo mode never touches real data (spec §17): no real config, archive, state or secret is
opened, and nothing connects to another machine. Python's audit hooks see every file opened and
every socket connected, whoever opens them."""

import os
import sys

from fastapi.testclient import TestClient

from recordings.archive import Archive
from recordings_ui import runtime, settings as settings_module, views
from recordings_ui.app import create_app
from recordings_ui.settings import from_env

_events: list[tuple[str, str]] | None = None


def _hook(event: str, args: tuple) -> None:
    if _events is None:
        return
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        _events.append(("open", os.fsdecode(args[0])))
    elif event == "socket.connect":
        _events.append(("connect", repr(args[1:])))


sys.addaudithook(_hook)  # stays for the session; idle unless a test switches it on


def test_demo_mode_reads_no_real_paths_secrets_or_network(tmp_path, demo_ids):
    global _events
    real = tmp_path / "real"
    real.mkdir()
    for name in ("config.toml", "secret"):
        (real / name).write_text("x\n", encoding="utf-8")
    environ = {
        "RECORDINGS_CONFIG": str(real / "config.toml"), "RECORDINGS_ARCHIVE": str(real / "archive"),
        "RECORDINGS_STATE": str(real / "state"), "RECORDINGS_INDEX": str(real / "index.db"),
        "RESTIC_PASSWORD_FILE": str(real / "secret"), "RECORDINGS_NTFY_TOPIC_FILE": str(real / "secret"),
        "RECORDINGS_DEADMAN_URL_FILE": str(real / "secret"),
    }
    _events = []
    try:
        settings = from_env(environ, demo=True)
        with TestClient(create_app(settings), base_url="http://localhost") as client:
            assert client.get("/healthz").json() == {"ok": True, "demo": True}
            for rid in demo_ids.values():
                assert client.get(f"/media/{rid}", headers={"Range": "bytes=0-9"}).status_code == 206
        archive = Archive(settings.archive)
        views.library_view(archive)
        for rid in demo_ids.values():
            views.recording_view(archive, rid)
    finally:
        events, _events = _events, None
        runtime.configure(None)
    assert [e for e in events if e[0] == "open" and str(real) in e[1]] == []
    assert [e for e in events if e[0] == "connect"] == []


def test_demo_mode_removes_its_temporary_copy_at_exit(monkeypatch):
    registered = []
    monkeypatch.setattr(settings_module.atexit, "register",
                        lambda fn, *args, **kwargs: registered.append((fn, args, kwargs)))
    settings = from_env({}, demo=True)
    assert settings.archive.is_dir()
    for fn, args, kwargs in registered:
        fn(*args, **kwargs)
    assert not settings.archive.parent.exists()
```

Add to `packages/core/tests/test_config.py`:
```python
@pytest.mark.parametrize("value, expected", [
    ("My-Homelab", "my-homelab"), (" 100.64.0.1 ", "100.64.0.1"), ("[fd7a::1]", "fd7a::1"),
    ("fd7a::1", "fd7a::1")])
def test_host_names_are_normalised(value, expected):
    from recordings.config import normalise_host
    assert normalise_host(value) == expected


@pytest.mark.parametrize("value", ["my-homelab:8000", "http://my-homelab", "my-homelab/x", "", "a b"])
def test_allowed_hosts_with_a_port_scheme_or_path_are_refused(tmp_path, value):
    # why: stage-1 carry-over. They passed validation, never matched, and gave a bare 400.
    cfg_file = write(tmp_path, f'[server]\nallowed_hosts = ["{value}"]\n')
    with pytest.raises(ConfigError, match="allowed_hosts"):
        load_config({"RECORDINGS_CONFIG": str(cfg_file)})
```

Add to `packages/ui/tests/test_settings.py`:
```python
def test_recordings_allowed_hosts_entries_are_checked_too():
    import pytest

    from recordings_ui.settings import env_hosts
    assert env_hosts({"RECORDINGS_ALLOWED_HOSTS": "A.example, b.example"}) == {"a.example", "b.example"}
    with pytest.raises(SystemExit, match="RECORDINGS_ALLOWED_HOSTS"):
        env_hosts({"RECORDINGS_ALLOWED_HOSTS": "host:8000"})
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/ui/tests/test_writeguard.py packages/ui/tests/test_demo_guard.py packages/ui/tests/test_settings.py packages/core/tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'WriteGuard'`; the host and demo cleanup
tests fail on their assertions.

- [ ] **Step 3: Write the guard and the host checks**

Add to `packages/ui/src/recordings_ui/hosts.py`:
```python
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class WriteGuard:
    """Every request that could change data (spec §15): never cross-site, and exactly JSON.

    A browser marks a request `Sec-Fetch-Site: cross-site` or `same-site` when another page sends
    it, and names that page in `Origin`. Either refuses it. Without both headers, no browser sent
    it, so no website is behind it. `Content-Type: application/json` can't be sent cross-site
    without a CORS preflight, which this app never answers.
    """

    def __init__(self, app: ASGIApp, allowed: frozenset[str]) -> None:
        self.app = app
        self.allowed = frozenset(h.lower() for h in allowed)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        site = (_header(scope, b"sec-fetch-site") or "").strip().lower()
        origin = _header(scope, b"origin")
        if (site and site not in ("same-origin", "none")) or (
                origin is not None and origin_name(origin) not in self.allowed):
            await PlainTextResponse("cross-site request refused", status_code=403)(scope, receive, send)
            return
        if (_header(scope, b"content-type") or "").strip().lower() != "application/json":
            await PlainTextResponse("send Content-Type: application/json",
                                    status_code=415)(scope, receive, send)
            return
        await self.app(scope, receive, send)
```

In `packages/ui/src/recordings_ui/app.py`, **replace** `from recordings_ui.hosts import HostGuard`
with `from recordings_ui.hosts import HostGuard, WriteGuard`, and **replace** the last line of
`create_app` with:
```python
    # Around everything, the Shiny app and its websocket included (recordings_ui.hosts): first the
    # host names, then the cross-site write check.
    return HostGuard(WriteGuard(api, settings.allowed_hosts), settings.allowed_hosts)
```

In `packages/core/src/recordings/config.py`, **add** `import ipaddress`, and before
`_allowed_hosts`:
```python
def normalise_host(value: str) -> str:
    """A bare host name or address, lowercased: no scheme, path or port. Entries like
    `my-homelab:8000` used to pass validation and then never match (stage-1 carry-over)."""
    host = value.strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host or "://" in host or "/" in host or " " in host:
        raise ValueError(f"{value!r} is not a host name: give the name or address alone")
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise ValueError(f"{value!r} has a port: give the host name alone") from None
    return host
```
and **replace** `_allowed_hosts` with:
```python
def _allowed_hosts(server: dict[str, Any]) -> tuple[str, ...]:
    value = server.get("allowed_hosts", [])
    if not isinstance(value, list) or not all(isinstance(h, str) for h in value):
        raise ConfigError(
            "[server] allowed_hosts must be a list of host names, "
            'for example ["my-homelab", "100.64.0.1"]')
    try:
        return tuple(normalise_host(h) for h in value)
    except ValueError as exc:
        raise ConfigError(f"[server] allowed_hosts: {exc}") from None
```

In `packages/ui/src/recordings_ui/settings.py`, **add** `import atexit` and
`from recordings.config import normalise_host`, **replace** `env_hosts` with:
```python
def env_hosts(environ: Mapping[str, str]) -> frozenset[str]:
    """RECORDINGS_ALLOWED_HOSTS: extra names, comma-separated, checked like [server] allowed_hosts."""
    names = [n for n in environ.get("RECORDINGS_ALLOWED_HOSTS", "").split(",") if n.strip()]
    try:
        return frozenset(normalise_host(n) for n in names)
    except ValueError as exc:
        raise SystemExit(f"RECORDINGS_ALLOWED_HOSTS: {exc}") from None
```
and in `from_env`'s demo branch, **replace** the `copy = …` line with:
```python
        copy = Path(tempfile.mkdtemp(prefix="recordings-demo-")) / "archive"
        atexit.register(shutil.rmtree, copy.parent, ignore_errors=True)  # stage-1 carry-over
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/ui packages/core/tests/test_config.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/ui/src/recordings_ui packages/ui/tests packages/core/src/recordings/config.py packages/core/tests/test_config.py
git commit -m "fix(ui): refuse cross-site and non-JSON writes; check host names; guard demo mode

WriteGuard wraps the whole app (spec §15), so every route that writes gets it. allowed_hosts with a
port or scheme are refused. A test proves demo mode opens no real path and no socket (spec §17;
Python audit hooks), and the demo's temporary copy is removed at exit.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Backups with restic, and the restore test

**Checkpoint lens:** operations.

**Files:**
- Create: `packages/core/src/recordings/ssh.py`, `packages/core/src/recordings/backup.py`, `packages/core/src/recordings/ops.py`, `scripts/fetch_restic.py`
- Modify: `packages/core/src/recordings/state.py` (`ops_runs`, `Run`, `record_run`, `last_run`, `backup_to`), `config.py` (secrets: `file_only`, `secret_path`), `selfdoc.py` (`validate(deep=)`), `cli.py` (`backup …`, `validate --deep`)
- Modify: `config.example.toml` (`[nas]`, `[backup]`, the secrets list), `Makefile` (`restic`)
- Test: `packages/core/tests/test_backup.py`

**Interfaces:**
- Consumes:
  - from Task 2: `read_sentinel`, `State`, and `write_bytes_atomic`-style durability
  - from Task 3: `validate`
- Produces:
  - **`recordings.config`:**
    - `SecretSpec` gains `file_only: bool = False` and `optional: bool = False`
    - `secret_path(name, environ) -> Path | None`
    - new `SECRETS` entries: `restic_password` (`RESTIC_PASSWORD`), `rest_password` (`RESTIC_REST_PASSWORD`), `nas_ssh_key` (`RECORDINGS_NAS_SSH_KEY`, file only), `nas_known_hosts` (`RECORDINGS_NAS_KNOWN_HOSTS`, file only)
  - **`recordings.ssh`:**
    - `ssh_options(key: Path | None, known_hosts: Path | None, port: int | None) -> list[str]`
    - `@dataclass(frozen=True) Nas(ssh: str | None, port: int | None, key: Path | None, known_hosts: Path | None)`, with `.options() -> list[str]`
    - `nas_from_config(cfg, environ) -> Nas`
    - `nas_free_bytes(nas: Nas, path: str, *, runner=subprocess.run) -> int | None`
  - **`recordings.backup`:**
    - constants `TAG = "recordings"`, `MILESTONE_TAGS = ("pre-import", "post-import")`
    - `class BackupError(RuntimeError)`
    - `@dataclass(frozen=True) Keep(hourly=24, daily=14, weekly=8, monthly=12)`
    - `@dataclass(frozen=True) BackupConfig(repository, host, archive, state, paths, keep, prune, restic, cache_dir, restore_scratch, nas, nas_repo_path, min_nas_free_bytes, rest_username)`
    - `from_config(cfg, environ) -> BackupConfig`
    - environment and commands: `restic_env(bc, environ) -> dict[str, str]`, `restic_base(bc) -> list[str]`, `backup_command(bc, state_copy: Path, *, tags=()) -> list[str]`, `forget_command(bc) -> list[str]`, `check_command(bc) -> list[str]`, `restore_command(bc, target: Path) -> list[str]`, `init_command(bc) -> list[str]`
    - results: `@dataclass(frozen=True) BackupResult(snapshot_id: str, files: int, data_added: int)` and `@dataclass(frozen=True) RestoreResult(recordings: int, problems: tuple[dict, ...])`, with `.ok`
    - runners, each taking `runner=subprocess.run`:

      | Function | Returns |
      |---|---|
      | `run_init(bc, environ, *, runner)` | `str` |
      | `run_backup(bc, state, environ, *, tags=(), runner)` | `BackupResult` |
      | `run_forget(bc, environ, *, runner)` | `str` |
      | `run_check(bc, environ, *, runner)` | `str` |
      | `run_restore_test(bc, environ, *, expected_uuid: str, now: datetime, runner)` | `RestoreResult` |
  - **`recordings.state`:**
    - `@dataclass(frozen=True) Run(job: str, started_at: datetime, finished_at: datetime, ok: bool, detail: str)`
    - `State.record_run(job, started_at, finished_at, ok, detail) -> None`
    - `State.last_run(job, *, ok: bool | None = None) -> Run | None`
    - `State.backup_to(dest: Path) -> Path`
  - **`recordings.ops`:**
    - `@dataclass Context(cfg, environ, state, clock=…, runner=subprocess.run)`
    - `@dataclass(frozen=True) JobResult(job: str, ok: bool, detail: str)`
    - `JOBS: dict[str, Callable[[Context, tuple[str, ...]], str]]`, with `backup`, `prune`, `check` and `restore-test`
    - `FAILURES: tuple[type[BaseException], ...]`
    - `run_job(ctx, job: str, *, tags: tuple[str, ...] = ()) -> JobResult`
  - **`recordings.selfdoc.validate(root, *, deep: bool = False) -> list[dict]`**
  - **CLI:**
    - `recordings backup {init,run,check,prune,restore-test} [--tag TAG …] [--json]`
    - `recordings validate ARCHIVE [--deep]`
    - `make restic`

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_backup.py`:
```python
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from recordings import backup
from recordings.backup import BackupError, Keep
from recordings.config import load_config
from recordings.ops import Context, run_job
from recordings.selfdoc import validate
from recordings.sentinel import SENTINEL

REPO = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)


def restic_or_skip() -> str:
    for candidate in (os.environ.get("RECORDINGS_TEST_RESTIC"), str(REPO / ".cache" / "bin" / "restic"),
                      shutil.which("restic")):
        if candidate and Path(candidate).is_file():
            return candidate
    pytest.skip("restic is not installed: `make restic` puts the pinned one in .cache/bin")


def config(tmp_path, writer, **backup_keys) -> tuple:
    deploy = tmp_path / "deploy.env"
    deploy.write_text("RECORDINGS_PORT=8000\n", encoding="utf-8")
    keys = {"repository": str(tmp_path / "repo"), "extra_paths": [str(deploy)],
            "restore_scratch": str(tmp_path / "scratch"), "cache_dir": str(tmp_path / "cache"),
            **backup_keys}
    lines = "\n".join(f"{k} = {json.dumps(v)}" for k, v in keys.items())
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[archive]\npath = "{writer.root}"\nwriter_id = "test"\n'
        f'[state]\npath = "{writer.state.path}"\n[index]\npath = "{writer.index.path}"\n'
        f"[backup]\n{lines}\n", encoding="utf-8")
    (tmp_path / "pw").write_text("a test password\n", encoding="utf-8")
    environ = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "RECORDINGS_CONFIG": str(cfg_file),
               "RESTIC_PASSWORD_FILE": str(tmp_path / "pw")}
    return load_config(environ), environ


class FakeRestic:
    """Records each restic call and answers like restic's --json output does."""

    def __init__(self, returncode=0, stdout="", stderr="", on_call=None):
        self.calls, self.returncode, self.stdout, self.stderr = [], returncode, stdout, stderr
        self.on_call = on_call

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs["env"]))
        if self.on_call:
            self.on_call(cmd)
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


SUMMARY = json.dumps({"message_type": "summary", "snapshot_id": "f" * 64,
                      "total_files_processed": 12, "data_added": 4096})


def test_the_backup_names_its_paths_and_never_the_password(tmp_path, writer, make_incoming):
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer)
    bc = backup.from_config(cfg, environ)
    fake = FakeRestic(stdout="{\"message_type\":\"status\"}\n" + SUMMARY + "\n")
    result = backup.run_backup(bc, writer.state, environ, tags=("pre-import",), runner=fake)
    assert result == backup.BackupResult(snapshot_id="f" * 64, files=12, data_added=4096)
    ((cmd, env),) = fake.calls
    state_copy = writer.state.path / "backup" / "state.db"
    assert cmd[:3] == [bc.restic, "backup", "--json"]
    assert cmd[cmd.index("--host") + 1] == "test"
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--tag"] == ["recordings", "pre-import"]
    assert cmd[-4:] == [str(writer.root), str(cfg.path), str(tmp_path / "deploy.env"), str(state_copy)]
    assert str(writer.root / ".tmp") in cmd and ".*.tmp" in cmd
    assert "a test password" not in " ".join(cmd)  # it reaches restic only through its environment
    assert env["RESTIC_PASSWORD_FILE"] == str(tmp_path / "pw")
    assert env["RESTIC_REPOSITORY"] == str(tmp_path / "repo")
    assert set(env) <= {"PATH", "HOME", "RESTIC_REPOSITORY", "RESTIC_PASSWORD_FILE",
                        "RESTIC_PASSWORD", "RESTIC_CACHE_DIR", "RESTIC_REST_USERNAME",
                        "RESTIC_REST_PASSWORD"}
    assert state_copy.is_file()
    assert "voice.db" not in " ".join(cmd) and "plaud" not in " ".join(cmd)  # §15.1: never backed up


def test_the_state_db_copy_is_a_consistent_sqlite_backup(writer):
    writer.state.set_meta("marker", "copied")
    copy = writer.state.backup_to(writer.state.path / "backup" / "state.db")
    import sqlite3
    with sqlite3.connect(copy) as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'marker'").fetchone() == ("copied",)


def test_no_backup_runs_without_the_sentinel_or_with_another_archive(tmp_path, writer):
    # why: §6.7, §15.1. An unmounted archive backed up hourly would age the real snapshots out.
    cfg, environ = config(tmp_path, writer)
    bc = backup.from_config(cfg, environ)
    fake = FakeRestic(stdout=SUMMARY)
    writer.state.set_meta("archive_uuid", "00000000-0000-4000-8000-000000000000")
    with pytest.raises(BackupError, match="UUID"):
        backup.run_backup(bc, writer.state, environ, runner=fake)
    (writer.root / SENTINEL).unlink()
    with pytest.raises(BackupError, match="mounted"):
        backup.run_backup(bc, writer.state, environ, runner=fake)
    assert fake.calls == []


def test_a_missing_path_or_password_is_refused_before_restic_runs(tmp_path, writer):
    cfg, environ = config(tmp_path, writer, extra_paths=[str(tmp_path / "nope.env")])
    bc = backup.from_config(cfg, environ)
    with pytest.raises(BackupError, match="nope.env"):
        backup.run_backup(bc, writer.state, environ, runner=FakeRestic(stdout=SUMMARY))
    cfg, environ = config(tmp_path, writer)
    del environ["RESTIC_PASSWORD_FILE"]
    with pytest.raises(BackupError, match="password"):
        backup.run_backup(backup.from_config(cfg, environ), writer.state, environ,
                          runner=FakeRestic(stdout=SUMMARY))


@pytest.mark.parametrize("returncode, stderr", [
    (1, "Fatal: unable to open repository at rest:https://recordings:hunter2@nas:8000/r/\n"),
    (-9, ""),  # killed: §20 "a killed backup raises an alert" starts here
    (12, "Fatal: wrong password or no key found\n")])
def test_a_failed_restic_is_an_error_with_no_secret_in_it(tmp_path, writer, returncode, stderr):
    cfg, environ = config(tmp_path, writer)
    fake = FakeRestic(returncode=returncode, stderr=stderr)
    with pytest.raises(BackupError) as caught:
        backup.run_backup(backup.from_config(cfg, environ), writer.state, environ, runner=fake)
    assert "hunter2" not in str(caught.value) and f"exit {returncode}" in str(caught.value)


def test_retention_keeps_the_spec_counts_and_the_milestones(tmp_path, writer):
    cfg, environ = config(tmp_path, writer)
    cmd = backup.forget_command(backup.from_config(cfg, environ))
    for flag, value in (("--keep-hourly", "24"), ("--keep-daily", "14"), ("--keep-weekly", "8"),
                        ("--keep-monthly", "12"), ("--host", "test"), ("--tag", "recordings")):
        assert cmd[cmd.index(flag) + 1] == value, flag
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--keep-tag"] == ["pre-import", "post-import"]
    assert "--prune" in cmd
    assert backup.from_config(cfg, environ).keep == Keep()


def test_sftp_takes_the_nas_key_and_host_key_from_secret_files(tmp_path, writer):
    (tmp_path / "key").write_text("not a real key\n", encoding="utf-8")
    (tmp_path / "known_hosts").write_text("nas ssh-ed25519 AAAAexample\n", encoding="utf-8")
    cfg, environ = config(tmp_path, writer, repository="sftp:backup@nas:/volume1/backups/r")
    environ |= {"RECORDINGS_NAS_SSH_KEY_FILE": str(tmp_path / "key"),
                "RECORDINGS_NAS_KNOWN_HOSTS_FILE": str(tmp_path / "known_hosts")}
    base = backup.restic_base(backup.from_config(cfg, environ))
    (option,) = [a for a in base if a.startswith("sftp.args=")]
    assert f"-i {tmp_path / 'key'}" in option and "StrictHostKeyChecking=yes" in option
    assert "not a real key" not in " ".join(base)


def test_too_little_space_on_the_nas_stops_the_backup(tmp_path, writer, monkeypatch):
    # why: §15.1. The NAS's free space is checked before backups start.
    cfg_text_extra = {"repository": "sftp:backup@nas:/volume1/backups/r",
                      "nas_repo_path": "/volume1/backups/r", "min_nas_free_gb": 10}
    cfg, environ = config(tmp_path, writer, **cfg_text_extra)
    cfg.data["nas"] = {"ssh": "backup@nas"}
    bc = backup.from_config(cfg, environ)

    def df(cmd, **kwargs):
        if cmd[0] == "ssh":
            return subprocess.CompletedProcess(cmd, 0, "Filesystem 1K-blocks Used Available Capacity Mounted\n"
                                               "/dev/md2 100 90 1048576 90% /volume1\n", "")
        return subprocess.CompletedProcess(cmd, 0, SUMMARY, "")

    with pytest.raises(BackupError, match="free on the NAS"):
        backup.run_backup(bc, writer.state, environ, runner=df)


def test_a_restored_copy_with_a_truncated_file_fails_the_restore_test(tmp_path, writer, make_incoming):
    # Review Focus 5: a restore must be proven byte for byte, not by the files being there.
    rec = writer.add(make_incoming(b"ID3 " + b"x" * 4096)).recording
    cfg, environ = config(tmp_path, writer)

    def restore(cmd):
        if "restore" in cmd:
            target = Path(cmd[cmd.index("--target") + 1])
            copy = target / writer.root.relative_to(writer.root.anchor)
            shutil.copytree(writer.root, copy)
            media = copy / "recordings" / "2026" / "10" / rec.id / rec.media.file
            media.write_bytes(media.read_bytes()[:100])

    fake = FakeRestic(on_call=restore)
    result = backup.run_restore_test(backup.from_config(cfg, environ), environ,
                                     expected_uuid=writer.identity.uuid, now=NOW, runner=fake)
    assert not result.ok and "SHA-256" in result.problems[0]["message"]
    assert not any((tmp_path / "scratch").iterdir())  # the scratch copy is always removed
    ctx = Context(cfg=cfg, environ=environ, state=writer.state, clock=lambda: NOW, runner=fake)
    job = run_job(ctx, "restore-test")
    assert not job.ok and writer.state.last_run("restore-test").ok is False


def test_validate_deep_checks_every_hash(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    assert validate(writer.root, deep=True) == []
    folder = writer.archive.path_for(rec.id)
    (folder / rec.sources[0].raw).chmod(0o644)
    (folder / rec.sources[0].raw).write_bytes(b"tampered")
    assert validate(writer.root) == []  # the quick check sees the file there
    (problem,) = validate(writer.root, deep=True)
    assert "SHA-256" in problem["message"]


def test_run_job_records_each_run(tmp_path, writer, make_incoming):
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer)
    ctx = Context(cfg=cfg, environ=environ, state=writer.state, clock=lambda: NOW,
                  runner=FakeRestic(stdout=SUMMARY))
    assert run_job(ctx, "backup").ok and writer.state.last_run("backup", ok=True).detail.startswith("snapshot")
    off, _ = config(tmp_path, writer, prune=False)
    assert run_job(Context(cfg=off, environ=environ, state=writer.state, clock=lambda: NOW,
                           runner=FakeRestic()), "prune").detail == "prune is off: retention runs on the NAS"


def test_with_a_real_restic_backup_check_forget_and_restore(tmp_path, writer, make_incoming):
    exe = restic_or_skip()
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer, restic=exe)
    bc = backup.from_config(cfg, environ)
    backup.run_init(bc, environ)
    first = backup.run_backup(bc, writer.state, environ, tags=("pre-import",))
    assert len(first.snapshot_id) == 64
    backup.run_check(bc, environ)
    backup.run_forget(bc, environ)
    restored = backup.run_restore_test(bc, environ, expected_uuid=writer.identity.uuid, now=NOW)
    assert restored.ok and restored.recordings == 1
    assert not any((tmp_path / "scratch").iterdir())
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_backup.py -v`
Expected: FAIL with `ImportError: cannot import name 'backup' from 'recordings'`.

- [ ] **Step 3: Write the state additions, secrets and SSH options**

In `packages/core/src/recordings/state.py`:
1. **Append** to `SCHEMA`:
   ```sql
   CREATE TABLE IF NOT EXISTS ops_runs (
     id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL, started_at TEXT NOT NULL,
     finished_at TEXT NOT NULL, ok INTEGER NOT NULL, detail TEXT NOT NULL);
   CREATE INDEX IF NOT EXISTS ops_runs_by_job ON ops_runs (job, id);
   ```
2. **Add** `import os` and `from recordings.files import fsync_dir, temp_name`, then after
   `Revision`:
   ```python
   @dataclass(frozen=True)
   class Run:
       job: str
       started_at: datetime
       finished_at: datetime
       ok: bool
       detail: str
   ```
3. **Add** to `State`:
   ```python
       # ---- operations: each run of a scheduled job (§14, §15.1); Status reads these ----------
       def record_run(self, job: str, started_at: datetime, finished_at: datetime, ok: bool,
                      detail: str) -> None:
           with self.db() as db:
               db.execute("INSERT INTO ops_runs (job, started_at, finished_at, ok, detail) "
                          "VALUES (?, ?, ?, ?, ?)",
                          (job, started_at.isoformat(), finished_at.isoformat(), int(ok), detail))

       def last_run(self, job: str, *, ok: bool | None = None) -> Run | None:
           sql = "SELECT job, started_at, finished_at, ok, detail FROM ops_runs WHERE job = ?"
           args: tuple = (job,)
           if ok is not None:
               sql, args = sql + " AND ok = ?", (job, int(ok))
           with self.db() as db:
               row = db.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
           if row is None:
               return None
           return Run(row[0], datetime.fromisoformat(row[1]), datetime.fromisoformat(row[2]),
                      bool(row[3]), row[4])

       def backup_to(self, dest: Path) -> Path:
           """A consistent copy of state.db, with SQLite's own backup command (§15.1), even while
           another process writes it. Replaced atomically, so a half copy is never backed up."""
           dest = Path(dest)
           dest.parent.mkdir(parents=True, exist_ok=True)
           tmp = temp_name(dest)
           try:
               with closing(sqlite3.connect(self.db_path, timeout=30)) as src, \
                       closing(sqlite3.connect(tmp)) as dst:
                   src.backup(dst)
               os.replace(tmp, dest)
           except BaseException:
               tmp.unlink(missing_ok=True)
               raise
           fsync_dir(dest.parent)
           return dest
   ```

In `packages/core/src/recordings/config.py`:
1. **Replace** `SecretSpec` with:
   ```python
   @dataclass(frozen=True)
   class SecretSpec:
       env: str
       purpose: str
       stage: int  # the build stage that first needs it
       file_only: bool = False  # only NAME_FILE makes sense: the program needs a path (an SSH key)
       optional: bool = False  # needed only for some setups (rest-server's password)
   ```
2. **Add** to `SECRETS`:
   ```python
       "restic_password": SecretSpec(
           "RESTIC_PASSWORD", "the restic repository's password; keep a copy in your password "
           "manager", 2),
       "rest_password": SecretSpec(
           "RESTIC_REST_PASSWORD", "rest-server's password, when backups go there", 2,
           optional=True),
       "nas_ssh_key": SecretSpec(
           "RECORDINGS_NAS_SSH_KEY", "the NAS account's SSH private key, as a file", 2,
           file_only=True),
       "nas_known_hosts": SecretSpec(
           "RECORDINGS_NAS_KNOWN_HOSTS", "the NAS's SSH host key, as a known_hosts file", 2,
           file_only=True),
   ```
3. **Add** after `secret`:
   ```python
   def secret_path(name: str, environ: Mapping[str, str]) -> Path | None:
       """The file a NAME_FILE secret names, checked to exist and be readable, never read here."""
       spec = SECRETS[name]
       if spec.file_only and environ.get(spec.env):
           raise ConfigError(f"give {spec.env}_FILE, the path to the file, not the value itself")
       value = environ.get(f"{spec.env}_FILE")
       if not value:
           return None
       path = Path(value)
       if not path.is_file():
           raise ConfigError(f"{spec.env}_FILE points at {path}, which is not a file")
       try:
           with path.open("rb") as fh:
               fh.read(1)
       except OSError:
           raise ConfigError(f"{spec.env}_FILE at {path} could not be read") from None
       return path
   ```
4. In `secret`, **add** as its first lines:
   ```python
       if SECRETS[name].file_only:
           return str(path) if (path := secret_path(name, environ)) else None
   ```
5. In `doctor`'s secrets loop, it already reports `set` for every entry; leave it (Task 16
   rewrites `doctor`).

`packages/core/src/recordings/ssh.py`:
```python
"""SSH to the NAS (spec §15.1), shared by restic's SFTP backend, the rsync mirror and the NAS
free-space check.

The key and the NAS's host key come from secret files (§5). Host keys are checked strictly against
that file, and nothing is written to ~/.ssh, because the containers' file systems are read-only.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from recordings.config import Config, ConfigError, secret_path


def ssh_options(key: Path | None, known_hosts: Path | None, port: int | None) -> list[str]:
    out = ["-o", "BatchMode=yes", "-o", "ServerAliveInterval=60", "-o", "ServerAliveCountMax=240"]
    if key is not None:
        out += ["-i", str(key), "-o", "IdentitiesOnly=yes"]
    if known_hosts is not None:
        out += ["-o", f"UserKnownHostsFile={known_hosts}", "-o", "StrictHostKeyChecking=yes"]
    if port is not None:
        out += ["-p", str(port)]
    return out


@dataclass(frozen=True)
class Nas:
    ssh: str | None  # an ssh destination with a shell, user@host: the free-space check needs one
    port: int | None
    key: Path | None
    known_hosts: Path | None

    def options(self) -> list[str]:
        return ssh_options(self.key, self.known_hosts, self.port)


def nas_from_config(cfg: Config, environ: Mapping[str, str]) -> Nas:
    section = cfg.data.get("nas", {})
    ssh, port = section.get("ssh"), section.get("port")
    if ssh is not None and (not isinstance(ssh, str) or not ssh.strip()):
        raise ConfigError('[nas] ssh must be an ssh destination, such as "backup@nas"')
    if port is not None and (not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536):
        raise ConfigError("[nas] port must be a port number")
    return Nas(ssh=ssh, port=port, key=secret_path("nas_ssh_key", environ),
               known_hosts=secret_path("nas_known_hosts", environ))


def nas_free_bytes(nas: Nas, path: str, *, runner=subprocess.run) -> int | None:
    """Free bytes on the NAS at `path`, with `df` over ssh; None when [nas] ssh isn't set or the
    account has no shell (an SFTP-only account; stage-2a plan, open question 2)."""
    if not nas.ssh:
        return None
    try:
        proc = runner(["ssh", *nas.options(), nas.ssh, "df", "-Pk", "--", shlex.quote(path)],
                      capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = proc.stdout.strip().splitlines() if proc.returncode == 0 else []
    try:
        return int(lines[-1].split()[3]) * 1024
    except (IndexError, ValueError):
        return None
```

- [ ] **Step 4: Write `backup.py`, `ops.py`, deep validation and the commands**

`packages/core/src/recordings/backup.py`:
```python
"""Backups with restic (spec §15.1): encrypted, deduplicated and versioned, to the NAS.

What is backed up is an explicit list: the archive, config.toml, docker/deploy.env (as
[backup] extra_paths), and a consistent copy of state.db taken with SQLite's own backup command.
Nothing else in the state folder is named, so voice.db and the Plaud token store are left out by
construction. The archive must carry its sentinel, with the UUID state.db expects, before anything
is backed up: an empty mount point backed up hourly would age the real snapshots out (§6.7).

restic is run through its CLI, with --json where it has it
(https://restic.readthedocs.io/en/stable/075_scripting.html). The repository's password reaches
restic only through its environment (RESTIC_PASSWORD_FILE, or RESTIC_PASSWORD), never as an
argument, and nothing here prints it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from recordings.archive import Archive, utc_stamp
from recordings.config import Config, ConfigError, secret
from recordings.selfdoc import validate
from recordings.sentinel import SentinelError, read_sentinel
from recordings.ssh import Nas, nas_free_bytes, nas_from_config
from recordings.state import State

TAG = "recordings"
MILESTONE_TAGS = ("pre-import", "post-import")
_USERINFO = re.compile(r"(\w+://)[^/@\s]+@")


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class Keep:
    hourly: int = 24
    daily: int = 14
    weekly: int = 8
    monthly: int = 12


@dataclass(frozen=True)
class BackupConfig:
    repository: str
    host: str  # restic's --host: the writer ID, never a container's host name
    archive: Path
    state: Path
    paths: tuple[Path, ...]  # config.toml and the extra paths, besides the archive and state copy
    keep: Keep
    prune: bool
    restic: str
    cache_dir: Path | None
    restore_scratch: Path | None
    nas: Nas
    nas_repo_path: str | None
    min_nas_free_bytes: int
    rest_username: str | None = None  # rest-server's user, with a rest: repository


def _positive_int(section: dict, key: str, default: int) -> int:
    value = section.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"[backup] {key} must be a whole number of 1 or more")
    return value


def from_config(cfg: Config, environ: Mapping[str, str]) -> BackupConfig:
    section = cfg.data.get("backup", {})
    repository = section.get("repository")
    if not isinstance(repository, str) or not repository.strip():
        raise ConfigError("[backup] repository is not set (see config.example.toml)")
    missing = [n for n, v in (("[archive] path", cfg.archive_path), ("[state] path", cfg.state_path),
                              ("[archive] writer_id", cfg.writer_id)) if v is None]
    if missing:
        raise ConfigError("set " + ", ".join(missing) + " before backing up")
    extra = section.get("extra_paths", [])
    if not isinstance(extra, list) or not all(isinstance(p, str) for p in extra):
        raise ConfigError("[backup] extra_paths must be a list of paths")
    prune = section.get("prune", True)
    if not isinstance(prune, bool):
        raise ConfigError("[backup] prune must be true or false")
    min_free_gb = section.get("min_nas_free_gb", 10)
    if not isinstance(min_free_gb, (int, float)) or isinstance(min_free_gb, bool) or min_free_gb < 0:
        raise ConfigError("[backup] min_nas_free_gb must be a number")
    paths = ([cfg.path] if cfg.path else []) + [Path(p) for p in extra]
    keep = Keep(**{k: _positive_int(section, f"keep_{k}", d)
                   for k, d in (("hourly", 24), ("daily", 14), ("weekly", 8), ("monthly", 12))})
    return BackupConfig(
        repository=repository.strip(), host=cfg.writer_id, archive=cfg.archive_path,
        state=cfg.state_path, paths=tuple(paths), keep=keep, prune=prune,
        restic=str(section.get("restic", "restic")),
        cache_dir=Path(section["cache_dir"]) if section.get("cache_dir") else None,
        restore_scratch=Path(section["restore_scratch"]) if section.get("restore_scratch") else None,
        nas=nas_from_config(cfg, environ),
        nas_repo_path=section.get("nas_repo_path") or None,
        min_nas_free_bytes=int(min_free_gb * 1024 ** 3),
        rest_username=section.get("rest_username") or None)


def restic_env(bc: BackupConfig, environ: Mapping[str, str]) -> dict[str, str]:
    """restic's whole environment: nothing from ours but these, so no stray variable leaks in."""
    env = {"PATH": environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
           "HOME": environ.get("HOME", "/tmp"), "RESTIC_REPOSITORY": bc.repository}
    if environ.get("RESTIC_PASSWORD_FILE"):
        env["RESTIC_PASSWORD_FILE"] = environ["RESTIC_PASSWORD_FILE"]
    elif environ.get("RESTIC_PASSWORD"):
        env["RESTIC_PASSWORD"] = environ["RESTIC_PASSWORD"]
    else:
        raise BackupError("no restic password: set RESTIC_PASSWORD_FILE (a Docker secret)")
    if bc.cache_dir is not None:
        env["RESTIC_CACHE_DIR"] = str(bc.cache_dir)
    if bc.repository.startswith("rest:"):
        rest_password = secret("rest_password", environ)
        if rest_password:
            env["RESTIC_REST_PASSWORD"] = rest_password
        if bc.rest_username:
            env["RESTIC_REST_USERNAME"] = bc.rest_username
    return env


def restic_base(bc: BackupConfig) -> list[str]:
    base = [bc.restic]
    if bc.repository.startswith("sftp:"):
        base += ["-o", "sftp.args=" + " ".join(bc.nas.options())]
    return base


def backup_command(bc: BackupConfig, state_copy: Path, *, tags: Sequence[str] = ()) -> list[str]:
    tag_args = [x for t in (TAG, *tags) for x in ("--tag", t)]
    return [*restic_base(bc), "backup", "--json", "--host", bc.host, *tag_args,
            "--exclude", str(bc.archive / ".tmp"), "--exclude", ".*.tmp",
            str(bc.archive), *(str(p) for p in bc.paths), str(state_copy)]


def forget_command(bc: BackupConfig) -> list[str]:
    keep_tags = [x for t in MILESTONE_TAGS for x in ("--keep-tag", t)]
    return [*restic_base(bc), "forget", "--host", bc.host, "--tag", TAG,
            "--keep-hourly", str(bc.keep.hourly), "--keep-daily", str(bc.keep.daily),
            "--keep-weekly", str(bc.keep.weekly), "--keep-monthly", str(bc.keep.monthly),
            *keep_tags, "--prune"]


def check_command(bc: BackupConfig) -> list[str]:
    return [*restic_base(bc), "check"]


def restore_command(bc: BackupConfig, target: Path) -> list[str]:
    return [*restic_base(bc), "restore", "latest", "--host", bc.host, "--tag", TAG,
            "--target", str(target)]


def init_command(bc: BackupConfig) -> list[str]:
    return [*restic_base(bc), "init"]


def _run(bc: BackupConfig, cmd: list[str], environ: Mapping[str, str], what: str, runner,
         timeout: float | None = None) -> subprocess.CompletedProcess:
    proc = runner(cmd, env=restic_env(bc, environ), capture_output=True, text=True,
                  timeout=timeout)
    if proc.returncode != 0:
        last = (proc.stderr or "").strip().splitlines()[-1:] or ["no message"]
        raise BackupError(f"restic {what} failed (exit {proc.returncode}): "
                          + _USERINFO.sub(r"\1***@", last[0])[:300])
    return proc


def _check_archive(bc: BackupConfig, state: State) -> str:
    try:
        identity = read_sentinel(bc.archive)
    except SentinelError as exc:
        raise BackupError(f"refusing to back up: {exc}") from None
    if state.meta("archive_uuid") != identity.uuid:
        raise BackupError("refusing to back up: the archive's UUID changed since `recordings init`")
    return identity.uuid


@dataclass(frozen=True)
class BackupResult:
    snapshot_id: str
    files: int
    data_added: int


def run_init(bc: BackupConfig, environ: Mapping[str, str], *, runner=subprocess.run) -> str:
    _run(bc, init_command(bc), environ, "init", runner, timeout=600)
    return "repository initialised"


def run_backup(bc: BackupConfig, state: State, environ: Mapping[str, str], *,
               tags: Sequence[str] = (), runner=subprocess.run) -> BackupResult:
    _check_archive(bc, state)
    for path in bc.paths:
        if not path.is_file():
            raise BackupError(f"refusing to back up: {path} is missing")
    if bc.nas_repo_path and bc.nas.ssh:
        free = nas_free_bytes(bc.nas, bc.nas_repo_path, runner=runner)
        if free is not None and free < bc.min_nas_free_bytes:
            raise BackupError(f"refusing to back up: only {free / 1024 ** 3:.1f} GB free on the NAS")
    state_copy = state.backup_to(bc.state / "backup" / "state.db")
    proc = _run(bc, backup_command(bc, state_copy, tags=tags), environ, "backup", runner)
    summary = None
    for line in proc.stdout.splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("message_type") == "summary":
            summary = message
    if not summary or not summary.get("snapshot_id"):
        raise BackupError("restic backup finished without a snapshot")
    return BackupResult(snapshot_id=summary["snapshot_id"],
                        files=int(summary.get("total_files_processed") or 0),
                        data_added=int(summary.get("data_added") or 0))


def run_forget(bc: BackupConfig, environ: Mapping[str, str], *, runner=subprocess.run) -> str:
    _run(bc, forget_command(bc), environ, "forget", runner)
    return "retention applied"


def run_check(bc: BackupConfig, environ: Mapping[str, str], *, runner=subprocess.run) -> str:
    _run(bc, check_command(bc), environ, "check", runner)
    return "repository checked"


@dataclass(frozen=True)
class RestoreResult:
    recordings: int
    problems: tuple[dict, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def run_restore_test(bc: BackupConfig, environ: Mapping[str, str], *, expected_uuid: str,
                     now: datetime, runner=subprocess.run) -> RestoreResult:
    """Restore the newest snapshot into scratch and prove it (§15.1): `recordings validate`, with
    every hash checked, and the same archive UUID. The scratch copy is always removed."""
    if bc.restore_scratch is None:
        raise BackupError("[backup] restore_scratch is not set")
    target = bc.restore_scratch / f"restore-{utc_stamp(now)}"
    try:
        _run(bc, restore_command(bc, target), environ, "restore", runner)
        restored = target / bc.archive.relative_to(bc.archive.anchor)
        problems = list(validate(restored, deep=True))
        try:
            if read_sentinel(restored).uuid != expected_uuid:
                problems.append({"path": str(restored), "message": "a different archive's UUID"})
        except SentinelError as exc:
            problems.append({"path": str(restored), "message": str(exc)})
        count = sum(1 for _ in Archive(restored).recording_dirs())
    finally:
        shutil.rmtree(target, ignore_errors=True)
    return RestoreResult(recordings=count, problems=tuple(problems))
```

`packages/core/src/recordings/ops.py`:
```python
"""Operations (spec §14, §15.1). Each scheduled job runs through run_job, which records the run
in state.db, so Status can show it and the schedule knows what is due. A failure is a result, not
a crash: it is recorded with its reason, which names IDs and exit codes, never content."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

from recordings import backup
from recordings.backup import BackupError
from recordings.config import Config, ConfigError
from recordings.sentinel import SentinelError
from recordings.state import State, StateError


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Context:
    cfg: Config
    environ: Mapping[str, str]
    state: State
    clock: Callable[[], datetime] = field(default=_now)
    runner: Callable[..., subprocess.CompletedProcess] = field(default=subprocess.run)


@dataclass(frozen=True)
class JobResult:
    job: str
    ok: bool
    detail: str


def _backup(ctx: Context, tags: tuple[str, ...]) -> str:
    result = backup.run_backup(backup.from_config(ctx.cfg, ctx.environ), ctx.state, ctx.environ,
                               tags=tags, runner=ctx.runner)
    return (f"snapshot {result.snapshot_id[:12]}: {result.files} files, "
            f"{result.data_added} bytes added")


def _prune(ctx: Context, tags: tuple[str, ...]) -> str:
    bc = backup.from_config(ctx.cfg, ctx.environ)
    if not bc.prune:
        return "prune is off: retention runs on the NAS"
    return backup.run_forget(bc, ctx.environ, runner=ctx.runner)


def _check(ctx: Context, tags: tuple[str, ...]) -> str:
    return backup.run_check(backup.from_config(ctx.cfg, ctx.environ), ctx.environ,
                            runner=ctx.runner)


def _restore_test(ctx: Context, tags: tuple[str, ...]) -> str:
    result = backup.run_restore_test(
        backup.from_config(ctx.cfg, ctx.environ), ctx.environ,
        expected_uuid=ctx.state.meta("archive_uuid") or "", now=ctx.clock(), runner=ctx.runner)
    if not result.ok:
        first = result.problems[0]
        raise BackupError(f"restore test failed: {len(result.problems)} problems; first: "
                          f"{first['message']} ({first['path']})")
    return f"restored and validated {result.recordings} recordings"


JOBS: dict[str, Callable[[Context, tuple[str, ...]], str]] = {
    "backup": _backup, "prune": _prune, "check": _check, "restore-test": _restore_test,
}
FAILURES: tuple[type[BaseException], ...] = (
    BackupError, ConfigError, SentinelError, StateError, OSError, subprocess.TimeoutExpired)


def run_job(ctx: Context, job: str, *, tags: tuple[str, ...] = ()) -> JobResult:
    started = ctx.clock()
    try:
        detail, ok = JOBS[job](ctx, tags), True
    except FAILURES as exc:
        detail, ok = str(exc)[:500], False
    ctx.state.record_run(job, started, ctx.clock(), ok, detail)
    return JobResult(job, ok, detail)
```

In `packages/core/src/recordings/selfdoc.py`:
1. **Add** the import `from recordings.archive import sha256_file`.
2. **Change** `def validate(root: Path) -> list[dict]:` to
   `def validate(root: Path, *, deep: bool = False) -> list[dict]:`, and
   `problems += _recording_problems(archive, rec)` to
   `problems += _recording_problems(archive, rec, deep=deep)`.
3. **Change** `def _recording_problems(archive: Archive, rec) -> list[dict]:` to
   `def _recording_problems(archive: Archive, rec, *, deep: bool = False) -> list[dict]:`, and
   **add** before its `return problems`:
   ```python
       if deep:  # every byte, as the restore test needs: a file that's there isn't proof enough
           if present and sha256_file(media) != rec.media.sha256:
               problems.append({"path": here, "message": f"media.file {rec.media.file!r} does not "
                                                         "match its SHA-256"})
           for source in rec.sources:
               path = folder / source.raw if source.raw else None
               if source.sha256 and path is not None and path.is_file() \
                       and sha256_file(path) != source.sha256:
                   problems.append({"path": here, "message": f"source file {source.raw} does not "
                                                             "match its SHA-256"})
   ```

In `packages/core/src/recordings/cli.py`:
1. **Add** the imports `from recordings import ops` and `from recordings.state import State`.
2. **Add** `p.add_argument("--deep", action="store_true", help="also check every file's SHA-256")`
   to the `validate` parser, and **change** `cmd_validate`'s first line to
   `problems = selfdoc.validate(args.archive, deep=args.deep)`.
3. **Add** to `build_parser`:
   ```python
       p = sub.add_parser("backup", help="restic backups to the NAS (spec §15.1)")
       p.add_argument("action", choices=["init", "run", "check", "prune", "restore-test"])
       p.add_argument("--tag", action="append", default=[],
                      help="an extra snapshot tag, such as pre-import (with run)")
       p.add_argument("--json", action="store_true")
   ```
4. **Add**:
   ```python
   def _ops_context(as_json: bool) -> ops.Context | int:
       cfg = _config(as_json)
       if isinstance(cfg, int):
           return cfg
       if cfg.state_path is None:
           return _fail("set [state] path in config.toml first", as_json, 78)
       try:
           state = State.open(cfg.state_path)
       except StateError as exc:
           return _fail(str(exc), as_json, 78)
       return ops.Context(cfg=cfg, environ=os.environ, state=state)


   def cmd_backup(args: argparse.Namespace) -> int:
       ctx = _ops_context(args.json)
       if isinstance(ctx, int):
           return ctx
       if args.action == "init":
           try:
               detail = backup.run_init(backup.from_config(ctx.cfg, ctx.environ), ctx.environ)
           except (backup.BackupError, config.ConfigError) as exc:
               return _fail(str(exc), args.json, 1)
           _emit({"job": "backup init", "ok": True, "detail": detail}, args.json)
           return 0
       job = "backup" if args.action == "run" else args.action
       result = ops.run_job(ctx, job, tags=tuple(args.tag))
       _emit({"job": result.job, "ok": result.ok, "detail": result.detail}, args.json)
       return 0 if result.ok else 1
   ```
   with `from recordings import backup` among the imports, and `"backup": cmd_backup,` in
   `commands`.

In `config.example.toml`, **add** after `[disk]`:
```toml
[nas]                                   # stage 2a: the NAS over SSH (spec §15.1)
ssh = "backup@nas"                      # an ssh destination WITH a shell, for the free-space check;
                                        # leave it out for an SFTP-only account (stage-2a plan, open question 2)
port = 22

[backup]                                # stage 2a: restic to the NAS (spec §15.1)
# The transport the runbook's spike chose: restic over SFTP, or rest-server with --append-only.
repository = "sftp:backup@nas:/volume1/backups/recordings"
# repository = "rest:http://nas:8000/recordings/"     # with rest_username, and the rest_password secret
# rest_username = "recordings"
restic = "restic"
# Besides the archive, config.toml and a copy of state.db. In Docker, deploy.env is mounted here.
extra_paths = ["/backup-extra/deploy.env"]
keep_hourly = 24
keep_daily = 14
keep_weekly = 8
keep_monthly = 12
# false with rest-server --append-only: it refuses forget --prune, so retention runs on the NAS.
prune = true
nas_repo_path = "/volume1/backups/recordings"   # where the free-space check looks, over [nas] ssh
min_nas_free_gb = 10
restore_scratch = "/restore"            # the monthly restore test's scratch folder
cache_dir = "/state/cache/restic"
```
and, in the secrets comment block at the end, **add**:
```toml
#   RESTIC_PASSWORD             the restic repository's password (keep a copy elsewhere)  (stage 2)
#   RESTIC_REST_PASSWORD        rest-server's password, only if backups go there      (stage 2)
#   RECORDINGS_NAS_SSH_KEY      the NAS account's SSH key: give RECORDINGS_NAS_SSH_KEY_FILE (stage 2)
#   RECORDINGS_NAS_KNOWN_HOSTS  the NAS's host key: give RECORDINGS_NAS_KNOWN_HOSTS_FILE    (stage 2)
```

`scripts/fetch_restic.py`:
```python
"""Install the pinned restic into .cache/bin/restic, checked against its release checksum.

    uv run python scripts/fetch_restic.py        (or: make restic)

For CI and the Mac. The Docker image installs the same version itself (docker/Dockerfile).
The checksums are from restic's v0.19.1 release SHA256SUMS, checked 2026-10-08. Nothing is
installed system-wide.
"""

from __future__ import annotations

import bz2
import hashlib
import platform
import subprocess
import sys
import urllib.request
from pathlib import Path

VERSION = "0.19.1"
PINNED = {
    ("linux", "x86_64"): ("linux_amd64", "f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c"),
    ("linux", "aarch64"): ("linux_arm64", "a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465"),
    ("darwin", "arm64"): ("darwin_arm64", "c38d579622cf602f665234c5a8c315030b6cf70656028fe6dc29a786b60e5f35"),
}
REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / ".cache" / "bin" / "restic"


def installed() -> bool:
    if not TARGET.is_file():
        return False
    out = subprocess.run([str(TARGET), "version"], capture_output=True, text=True).stdout
    return f"restic {VERSION} " in out


def main() -> int:
    key = (platform.system().lower(), platform.machine().lower())
    if key not in PINNED:
        print(f"fetch_restic: no pinned restic for {key}", file=sys.stderr)
        return 1
    if installed():
        print(f"restic {VERSION} already in {TARGET.relative_to(REPO)}")
        return 0
    name, expected = PINNED[key]
    url = f"https://github.com/restic/restic/releases/download/v{VERSION}/restic_{VERSION}_{name}.bz2"
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != expected:
        print("fetch_restic: the download does not match its pinned SHA-256", file=sys.stderr)
        return 1
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    tmp = TARGET.with_suffix(".part")
    tmp.write_bytes(bz2.decompress(data))
    tmp.chmod(0o755)
    tmp.replace(TARGET)
    print(f"restic {VERSION} -> {TARGET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

In the `Makefile`, **add** `restic` to `.PHONY`, and the target:
```make
restic:
	uv run python scripts/fetch_restic.py
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `make restic && uv run pytest packages/core/tests/test_backup.py -v && uv run pytest`
Expected: restic 0.19.1 lands in `.cache/bin/`, then every test passes. The real-restic test runs
rather than skipping.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/recordings packages/core/tests/test_backup.py scripts/fetch_restic.py config.example.toml Makefile
git commit -m "feat(core): restic backups, retention, check and a byte-for-byte restore test

An explicit backup list (archive, config.toml, deploy.env, an SQLite backup copy of state.db) that
refuses an archive without its sentinel. restic 0.19.1 pinned by SHA-256. Checked: restic docs
(075_scripting --json, 030 SFTP and REST, 060 forget), rest-server --append-only, and
docs.python.org sqlite3 Connection.backup.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: The NAS mirror

**Checkpoint lens:** operations.

**Files:**
- Create: `packages/core/src/recordings/mirror.py`
- Modify: `packages/core/src/recordings/ops.py` (the `mirror` job), `cli.py` (`mirror`), `config.example.toml` (`[mirror]`)
- Test: `packages/core/tests/test_mirror.py`

**Interfaces:**
- Consumes: `read_sentinel` (Task 2), `Nas` and `nas_from_config` (Task 11), and `ops.Context`, `JOBS` and `FAILURES` (Task 11).
- Produces:
  - **`recordings.mirror`:**
    - `class MirrorError(RuntimeError)`
    - `@dataclass(frozen=True) MirrorConfig(target: str, archive: Path, rsync: str, nas: Nas)`
    - `from_config(cfg, environ) -> MirrorConfig`
    - `is_remote(target: str) -> bool`
    - `rsync_command(mc: MirrorConfig) -> list[str]`
    - `run_mirror(mc, *, expected_uuid: str, runner=subprocess.run) -> str`
  - **`ops.JOBS["mirror"]`.**
  - **CLI:** `recordings mirror [--json]`.

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_mirror.py`:
```python
import os
import shutil
import subprocess

import pytest

from recordings import mirror
from recordings.config import load_config
from recordings.mirror import MirrorError, is_remote, rsync_command, run_mirror
from recordings.sentinel import SENTINEL


def mirror_config(tmp_path, writer, target):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'[archive]\npath = "{writer.root}"\nwriter_id = "test"\n'
                        f'[mirror]\ntarget = "{target}"\n', encoding="utf-8")
    environ = {"RECORDINGS_CONFIG": str(cfg_file), "PATH": os.environ["PATH"]}
    return mirror.from_config(load_config(environ), environ)


def test_the_command_deletes_late_puts_updates_in_place_together_and_skips_work_in_progress(
        tmp_path, writer):
    mc = mirror_config(tmp_path, writer, "backup@nas:/volume1/recordings-mirror/")
    cmd = rsync_command(mc)
    for flag in ("-a", "--delete", "--delete-delay", "--delay-updates", "--exclude=/.tmp/",
                 "--exclude=.*.tmp"):
        assert flag in cmd, flag
    assert cmd[-2:] == [f"{writer.root}/", "backup@nas:/volume1/recordings-mirror/"]
    assert cmd[cmd.index("-e") + 1].startswith("ssh -o BatchMode=yes")
    local = rsync_command(mirror_config(tmp_path, writer, str(tmp_path / "mirror")))
    assert "-e" not in local


@pytest.mark.parametrize("target, remote", [("nas:/x", True), ("backup@nas:/x/", True),
                                            ("/mnt/mirror", False), ("./x:y", False)])
def test_is_remote(target, remote):
    assert is_remote(target) is remote


def test_the_mirror_refuses_without_the_sentinel_or_with_another_archive(tmp_path, writer):
    # why: §6.7, §15.1. rsync --delete from an unmounted, empty folder would empty the mirror.
    mc = mirror_config(tmp_path, writer, str(tmp_path / "mirror"))
    calls = []
    with pytest.raises(MirrorError, match="UUID"):
        run_mirror(mc, expected_uuid="00000000-0000-4000-8000-000000000000",
                   runner=lambda *a, **k: calls.append(a))
    (writer.root / SENTINEL).unlink()
    with pytest.raises(MirrorError, match="mounted"):
        run_mirror(mc, expected_uuid=writer.identity.uuid, runner=lambda *a, **k: calls.append(a))
    assert calls == []


def test_a_failed_rsync_is_an_error(tmp_path, writer):
    mc = mirror_config(tmp_path, writer, str(tmp_path / "mirror"))
    failing = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 23, "", "rsync: some error\n")
    with pytest.raises(MirrorError, match="exit 23"):
        run_mirror(mc, expected_uuid=writer.identity.uuid, runner=failing)


def _rsync_with_delay_updates() -> str | None:
    exe = shutil.which("rsync")
    if exe is None:
        return None
    help_text = subprocess.run([exe, "--help"], capture_output=True, text=True).stdout
    return exe if "--delay-updates" in help_text and "--delete-delay" in help_text else None


def test_a_real_local_mirror(tmp_path, writer, make_incoming):
    if _rsync_with_delay_updates() is None:
        pytest.skip("this rsync has no --delay-updates (macOS ships openrsync); CI's has")
    rec = writer.add(make_incoming()).recording
    target = tmp_path / "mirror"
    (target / "stale-file").parent.mkdir(parents=True)
    (target / "stale-file").write_text("from before", encoding="utf-8")
    (writer.root / ".tmp" / "x").mkdir(parents=True)
    mc = mirror_config(tmp_path, writer, f"{target}/")
    run_mirror(mc, expected_uuid=writer.identity.uuid)
    assert (target / SENTINEL).is_file()
    assert (target / "recordings" / "2026" / "10" / rec.id / "recording.json").is_file()
    assert not (target / "stale-file").exists() and not (target / ".tmp").exists()
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_mirror.py -v`
Expected: FAIL with `ImportError: cannot import name 'mirror' from 'recordings'`.

- [ ] **Step 3: Write the mirror**

`packages/core/src/recordings/mirror.py`:
```python
"""The read-only mirror for the Mac (spec §15.1): an hourly one-way rsync of the archive to a
share on the NAS.

- `--delay-updates` puts every changed file in place at the end, so a reader never sees half a
  copy.
- `--delete --delete-delay` removes what the archive no longer holds, after the transfer.
- `.tmp/` and `.*.tmp` (work in progress) are never copied.
- It refuses to run when the archive's sentinel is missing or its UUID has changed (§6.7), so an
  unmounted archive can never empty the mirror through --delete.

The mirror holds private recordings too: on the Mac, keeping external agents out of them is
policy (§7.4).
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from recordings.config import Config, ConfigError
from recordings.sentinel import SentinelError, read_sentinel
from recordings.ssh import Nas, nas_from_config


class MirrorError(RuntimeError):
    pass


@dataclass(frozen=True)
class MirrorConfig:
    target: str
    archive: Path
    rsync: str
    nas: Nas


def from_config(cfg: Config, environ: Mapping[str, str]) -> MirrorConfig:
    section = cfg.data.get("mirror", {})
    target = section.get("target")
    if not isinstance(target, str) or not target.strip():
        raise ConfigError("[mirror] target is not set (see config.example.toml)")
    if cfg.archive_path is None:
        raise ConfigError("set [archive] path before mirroring")
    return MirrorConfig(target=target.strip(), archive=cfg.archive_path,
                        rsync=str(section.get("rsync", "rsync")), nas=nas_from_config(cfg, environ))


def is_remote(target: str) -> bool:
    """rsync's rule: `[user@]host:path` is remote; a path whose first part has no colon is local."""
    first = target.split("/", 1)[0]
    return ":" in first and not target.startswith((".", "/"))


def rsync_command(mc: MirrorConfig) -> list[str]:
    cmd = [mc.rsync, "-a", "--delete", "--delete-delay", "--delay-updates",
           "--exclude=/.tmp/", "--exclude=.*.tmp"]
    if is_remote(mc.target):
        cmd += ["-e", shlex.join(["ssh", *mc.nas.options()])]
    return [*cmd, f"{mc.archive}/", mc.target]


def run_mirror(mc: MirrorConfig, *, expected_uuid: str, runner=subprocess.run) -> str:
    try:
        identity = read_sentinel(mc.archive)
    except SentinelError as exc:
        raise MirrorError(f"refusing to mirror: {exc}") from None
    if identity.uuid != expected_uuid:
        raise MirrorError("refusing to mirror: the archive's UUID changed since `recordings init`")
    proc = runner(rsync_command(mc), capture_output=True, text=True, timeout=6 * 3600)
    if proc.returncode != 0:
        last = (proc.stderr or "").strip().splitlines()[-1:] or ["no message"]
        raise MirrorError(f"rsync failed (exit {proc.returncode}): {last[0][:300]}")
    return "mirrored"
```

In `packages/core/src/recordings/ops.py`, **add** `from recordings import mirror` and
`from recordings.mirror import MirrorError`, then:
```python
def _mirror(ctx: Context, tags: tuple[str, ...]) -> str:
    return mirror.run_mirror(mirror.from_config(ctx.cfg, ctx.environ),
                             expected_uuid=ctx.state.meta("archive_uuid") or "", runner=ctx.runner)
```
and **add** `"mirror": _mirror,` to `JOBS` and `MirrorError` to `FAILURES`.

In `packages/core/src/recordings/cli.py`, **add**:
```python
    p = sub.add_parser("mirror", help="rsync the archive to the NAS's read-only mirror, now")
    p.add_argument("--json", action="store_true")
```
and:
```python
def cmd_mirror(args: argparse.Namespace) -> int:
    ctx = _ops_context(args.json)
    if isinstance(ctx, int):
        return ctx
    result = ops.run_job(ctx, "mirror")
    _emit({"job": result.job, "ok": result.ok, "detail": result.detail}, args.json)
    return 0 if result.ok else 1
```
with `"mirror": cmd_mirror,` in `commands`.

In `config.example.toml`, **add** after `[backup]`:
```toml
[mirror]                                # stage 2a: the read-only mirror the Mac reads (spec §15.1)
target = "backup@nas:/volume1/recordings-mirror/"   # an rsync destination, over [nas]'s ssh key
rsync = "rsync"
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_mirror.py -v`
Expected: all pass. On the Mac, `test_a_real_local_mirror` is skipped (openrsync); it runs in CI.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/recordings packages/core/tests/test_mirror.py config.example.toml
git commit -m "feat(core): the NAS mirror: rsync --delete --delay-updates, refusing without the sentinel

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 13: Alerts, the dead-man's switch and the ops loop

**Checkpoint lens:** security and privacy.

**Files:**
- Create: `packages/core/src/recordings/alerts.py`
- Modify: `packages/core/src/recordings/ops.py` (the schedule, `disk` job, alerting, `run_due`, `loop`), `state.py` (alert state), `config.py` (`ntfy_topic`, `deadman_url` secrets), `cli.py` (`ops`, `alert-test`), `config.example.toml` (`[alerts]`)
- Test: `packages/core/tests/test_alerts.py`, `packages/core/tests/test_ops.py`

**Interfaces:**
- Consumes:
  - from Task 11: `ops.Context`, `run_job`, `JOBS`, `FAILURES`
  - from Task 9: `disk_status`, `disk_thresholds`
  - from Task 2: `config.secret`
- Produces:
  - **`recordings.alerts`:**
    - `TIMEOUT = 10.0`
    - `@dataclass(frozen=True) AlertConfig(ntfy_server: str | None, topic: str | None, deadman_url: str | None)`, with `.enabled` and a `__repr__` that hides the secrets
    - `from_config(cfg, environ) -> AlertConfig`
    - `send(ac, *, title: str, message: str, priority: int = 4, tags: Sequence[str] = ("warning",)) -> bool`
    - `ping_deadman(ac) -> bool`
  - **`recordings.state.State`:** `alert_state(key) -> tuple[bool, datetime | None]` and `set_alert_state(key, *, failing: bool, last_sent_at: datetime | None) -> None`.
  - **`recordings.ops`:**
    - schedule: `INTERVALS`, `ORDER`, `RETRY_AFTER = 15 min`, `REMIND_EVERY = 24 h`, `TITLES`
    - `class DiskWarning(RuntimeError)`
    - `enabled_jobs(cfg) -> tuple[str, ...]`
    - `due(state, now, jobs) -> list[str]`
    - `run_due(ctx) -> list[JobResult]`
    - `loop(ctx, *, tick: float = 60.0, sleep=time.sleep, stop=lambda: False) -> None`
    - `JOBS["disk"]`; `run_job` now alerts and pings
  - **New `SECRETS` entries:** `ntfy_topic` (`RECORDINGS_NTFY_TOPIC`) and `deadman_url` (`RECORDINGS_DEADMAN_URL`).
  - **CLI:** `recordings ops [--loop] [--json]` and `recordings alert-test [--json]`.

- [ ] **Step 1: Write the failing tests**

Add to `packages/core/tests/conftest.py` (with `import threading` and
`from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer` at the top). Both test files
use it, and with `--import-mode=importlib` a fixture in `conftest.py` is the way to share it:
```python
class FakeNtfy:
    """An ntfy server on a real local socket: it records each request and answers `status`."""

    def __init__(self):
        self.requests, self.status = [], 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _record(self):
                length = int(self.headers.get("Content-Length") or 0)
                outer.requests.append({"method": self.command, "path": self.path,
                                       "headers": dict(self.headers),
                                       "body": self.rfile.read(length).decode("utf-8")})
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"id": "x", "event": "message"}).encode())

            do_POST = do_GET = _record

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ntfy():
    server = FakeNtfy()
    yield server
    server.close()
```

`packages/core/tests/test_alerts.py`:
```python
import socket

import pytest

from recordings import alerts
from recordings.alerts import AlertConfig, ping_deadman, send
from recordings.config import ConfigError, load_config


def test_an_alert_posts_to_the_topic_with_title_priority_and_tags(ntfy):
    ac = AlertConfig(ntfy_server=ntfy.url, topic="topic-do-not-print/1", deadman_url=None)
    assert send(ac, title="recordings: backup failed", message="restic backup failed (exit 1)")
    (req,) = ntfy.requests
    assert (req["method"], req["path"]) == ("POST", "/topic-do-not-print%2F1")
    assert req["headers"]["Title"] == "recordings: backup failed"
    assert (req["headers"]["Priority"], req["headers"]["Tags"]) == ("4", "warning")
    assert req["body"] == "restic backup failed (exit 1)"


def test_without_a_server_or_topic_nothing_is_sent(ntfy):
    assert not send(AlertConfig(ntfy.url, None, None), title="t", message="m")
    assert not send(AlertConfig(None, "topic", None), title="t", message="m")
    assert ntfy.requests == []


def test_a_failed_delivery_never_raises_and_never_logs_the_topic(ntfy, caplog):
    ntfy.status = 500
    ac = AlertConfig(ntfy_server=ntfy.url, topic="topic-do-not-print", deadman_url=None)
    with caplog.at_level("WARNING"):
        assert send(ac, title="recordings: check failed", message="m") is False
    with socket.socket() as s:  # a port with nothing listening
        s.bind(("127.0.0.1", 0))
        closed = f"http://127.0.0.1:{s.getsockname()[1]}"
    with caplog.at_level("WARNING"):
        assert send(AlertConfig(closed, "topic-do-not-print", None), title="t", message="m") is False
    assert "topic-do-not-print" not in caplog.text and "HTTP 500" in caplog.text


def test_the_dead_mans_switch_is_a_get(ntfy):
    assert ping_deadman(AlertConfig(None, None, f"{ntfy.url}/ping/abc-do-not-print"))
    assert [(r["method"], r["path"]) for r in ntfy.requests] == [("GET", "/ping/abc-do-not-print")]
    assert not ping_deadman(AlertConfig(None, None, None))


def test_the_config_reads_the_secrets_from_files_and_hides_them(tmp_path):
    (tmp_path / "topic").write_text("topic-do-not-print\n", encoding="utf-8")
    (tmp_path / "ping").write_text("https://hc.example/ping/do-not-print\n", encoding="utf-8")
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[alerts]\nntfy_server = "https://ntfy.sh/"\n', encoding="utf-8")
    environ = {"RECORDINGS_CONFIG": str(cfg_file),
               "RECORDINGS_NTFY_TOPIC_FILE": str(tmp_path / "topic"),
               "RECORDINGS_DEADMAN_URL_FILE": str(tmp_path / "ping")}
    ac = alerts.from_config(load_config(environ), environ)
    assert (ac.ntfy_server, ac.topic, ac.enabled) == ("https://ntfy.sh", "topic-do-not-print", True)
    assert "do-not-print" not in repr(ac) and "do-not-print" not in str(ac)
    cfg_file.write_text('[alerts]\nntfy_server = "ntfy.sh"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="ntfy_server"):
        alerts.from_config(load_config(environ), environ)
```

`packages/core/tests/test_ops.py`:
```python
import json
import subprocess
from datetime import datetime, timedelta, timezone

from recordings import disk
from recordings.cli import main
from recordings.config import load_config
from recordings.ops import Context, due, enabled_jobs, loop, run_due, run_job

T0 = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
SUMMARY = json.dumps({"message_type": "summary", "snapshot_id": "f" * 64,
                      "total_files_processed": 1, "data_added": 1})


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now


def restic(returncode=0, stderr=""):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, SUMMARY if returncode == 0 else "", stderr)
    return run


def context(tmp_path, writer, ntfy, *, clock=None, runner=None, mirror=False):
    (tmp_path / "pw").write_text("pw\n", encoding="utf-8")
    deploy = tmp_path / "deploy.env"
    deploy.write_text("X=1\n", encoding="utf-8")
    mirror_section = f'[mirror]\ntarget = "{tmp_path / "mirror"}"\n' if mirror else ""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[archive]\npath = "{writer.root}"\nwriter_id = "test"\n'
        f'[state]\npath = "{writer.state.path}"\n'
        f'[backup]\nrepository = "{tmp_path / "repo"}"\nextra_paths = ["{deploy}"]\n'
        f'restore_scratch = "{tmp_path / "scratch"}"\n'
        f'[alerts]\nntfy_server = "{ntfy.url}"\n{mirror_section}', encoding="utf-8")
    environ = {"RECORDINGS_CONFIG": str(cfg_file), "RESTIC_PASSWORD_FILE": str(tmp_path / "pw"),
               "RECORDINGS_NTFY_TOPIC": "alerts-topic", "RECORDINGS_DEADMAN_URL": f"{ntfy.url}/ping",
               "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    return Context(cfg=load_config(environ), environ=environ, state=writer.state,
                   clock=clock or Clock(), runner=runner or restic())


def test_which_jobs_run_comes_from_config(tmp_path, writer, ntfy):
    assert enabled_jobs(context(tmp_path, writer, ntfy).cfg) == (
        "disk", "backup", "prune", "check", "restore-test")
    assert "mirror" in enabled_jobs(context(tmp_path, writer, ntfy, mirror=True).cfg)


def test_a_job_is_due_after_its_interval_and_a_failure_is_retried_after_15_minutes(writer):
    state = writer.state
    assert due(state, T0, ("backup", "check")) == ["backup", "check"]
    state.record_run("backup", T0, T0, True, "ok")
    assert due(state, T0 + timedelta(minutes=59), ("backup",)) == []
    assert due(state, T0 + timedelta(hours=1), ("backup",)) == ["backup"]
    later = T0 + timedelta(hours=2)
    state.record_run("backup", later, later, False, "failed")
    assert due(state, later + timedelta(minutes=14), ("backup",)) == []
    assert due(state, later + timedelta(minutes=15), ("backup",)) == ["backup"]


def test_a_good_backup_pings_the_dead_mans_switch(tmp_path, writer, make_incoming, ntfy):
    writer.add(make_incoming())
    assert run_job(context(tmp_path, writer, ntfy), "backup").ok
    assert [r["path"] for r in ntfy.requests] == ["/ping"]


def test_a_killed_backup_alerts_once_reminds_daily_and_says_when_it_recovers(
        tmp_path, writer, make_incoming, ntfy):
    # why: §20, "a killed backup raises an alert". A killed restic exits -9.
    writer.add(make_incoming(title="A PRIVATE TITLE"))
    clock = Clock()
    ctx = context(tmp_path, writer, ntfy, clock=clock, runner=restic(returncode=-9))
    assert not run_job(ctx, "backup").ok
    clock.now += timedelta(hours=1)
    run_job(ctx, "backup")  # still failing: no second alert yet
    clock.now += timedelta(hours=24)
    run_job(ctx, "backup")  # a reminder
    ctx.runner = restic()
    clock.now += timedelta(hours=1)
    assert run_job(ctx, "backup").ok
    titles = [r["headers"].get("Title") for r in ntfy.requests if r["method"] == "POST"]
    assert titles == ["recordings: backup failed", "recordings: backup failed",
                      "recordings: backup recovered"]
    assert all("alerts-topic" in r["path"] for r in ntfy.requests if r["method"] == "POST")
    assert not any("A PRIVATE TITLE" in r["body"] for r in ntfy.requests)  # Review Focus 4
    assert [r["path"] for r in ntfy.requests if r["method"] == "GET"] == ["/ping"]


def test_low_disk_space_raises_an_alert(tmp_path, writer, ntfy, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(disk.os, "statvfs", lambda p: SimpleNamespace(
        f_bavail=150, f_frsize=1024, f_blocks=1000))
    result = run_job(context(tmp_path, writer, ntfy), "disk")
    assert not result.ok and "15.0% free" in result.detail
    (req,) = [r for r in ntfy.requests if r["method"] == "POST"]
    assert req["headers"]["Title"] == "recordings: disk space low"


def test_run_due_runs_what_is_due_and_beats_the_heart(tmp_path, writer, make_incoming, ntfy):
    writer.add(make_incoming())
    ctx = context(tmp_path, writer, ntfy)
    results = {r.job for r in run_due(ctx)}
    assert {"disk", "backup", "prune", "check"} <= results
    assert writer.state.meta("ops_heartbeat") == T0.isoformat()
    assert {r.job for r in run_due(ctx)} <= {"restore-test"}  # the rest ran a moment ago


def test_the_loop_runs_until_told_to_stop(tmp_path, writer, ntfy):
    ctx = context(tmp_path, writer, ntfy)
    ticks = []
    loop(ctx, tick=0, sleep=lambda s: ticks.append(s), stop=lambda: len(ticks) >= 3)
    assert len(ticks) == 3


def test_alert_test_sends_one_and_never_prints_the_topic(tmp_path, writer, ntfy, monkeypatch, capsys):
    ctx = context(tmp_path, writer, ntfy)
    for key, value in ctx.environ.items():
        monkeypatch.setenv(key, value)
    assert main(["alert-test", "--json"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {"ntfy": True, "deadman": True}
    assert "alerts-topic" not in out
    assert [r["headers"].get("Title") for r in ntfy.requests if r["method"] == "POST"] == [
        "recordings: test alert"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_alerts.py packages/core/tests/test_ops.py -v`
Expected: FAIL with `ImportError: cannot import name 'alerts' from 'recordings'`.

- [ ] **Step 3: Write the alerts**

In `packages/core/src/recordings/config.py`, **add** to `SECRETS`:
```python
    "ntfy_topic": SecretSpec(
        "RECORDINGS_NTFY_TOPIC", "the private ntfy topic that alerts go to", 2),
    "deadman_url": SecretSpec(
        "RECORDINGS_DEADMAN_URL", "the dead-man's switch ping URL (it carries its own token)", 2),
```

`packages/core/src/recordings/alerts.py`:
```python
"""Alerts (spec §14): push notifications through ntfy, and a dead-man's switch.

- **ntfy** (https://docs.ntfy.sh/publish/): a POST to <server>/<topic>, with the message as the
  body and Title, Priority and Tags headers. The topic is a secret, because anyone who knows it
  can read the alerts. It is never logged, printed, or put in an error.
- **The dead-man's switch** is a URL pinged after each good backup. An outside service (plan
  question 1) alerts when the pings stop, and that is what notices a stopped container or a dead
  server.

Alert text names jobs, exit codes and IDs, never a recording's title or content. Sending never
raises: a failed delivery is logged without its URL.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import quote

from recordings import __version__
from recordings.config import Config, ConfigError, secret

log = logging.getLogger(__name__)
TIMEOUT = 10.0


@dataclass(frozen=True)
class AlertConfig:
    ntfy_server: str | None
    topic: str | None
    deadman_url: str | None

    @property
    def enabled(self) -> bool:
        return bool(self.ntfy_server and self.topic)

    def __repr__(self) -> str:  # never the topic or the ping URL
        return (f"AlertConfig(ntfy_server={self.ntfy_server!r}, "
                f"topic={'set' if self.topic else None}, "
                f"deadman_url={'set' if self.deadman_url else None})")

    __str__ = __repr__


def from_config(cfg: Config, environ: Mapping[str, str]) -> AlertConfig:
    server = cfg.data.get("alerts", {}).get("ntfy_server")
    if server is not None and (not isinstance(server, str)
                               or not server.startswith(("http://", "https://"))):
        raise ConfigError('[alerts] ntfy_server must be a URL, such as "https://ntfy.sh"')
    return AlertConfig(ntfy_server=server.rstrip("/") if server else None,
                       topic=secret("ntfy_topic", environ),
                       deadman_url=secret("deadman_url", environ))


def _deliver(request: urllib.request.Request) -> bool:
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        log.warning("delivery failed: HTTP %s", exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("delivery failed: %s", type(exc).__name__)
    return False


def send(ac: AlertConfig, *, title: str, message: str, priority: int = 4,
         tags: Sequence[str] = ("warning",)) -> bool:
    if not ac.enabled:
        return False
    request = urllib.request.Request(
        f"{ac.ntfy_server}/{quote(ac.topic, safe='')}", data=message.encode("utf-8"),
        method="POST", headers={"Title": title, "Priority": str(priority), "Tags": ",".join(tags),
                                "User-Agent": f"recordings/{__version__}"})
    delivered = _deliver(request)
    if not delivered:
        log.warning("alert %r was not delivered", title)
    return delivered


def ping_deadman(ac: AlertConfig) -> bool:
    if not ac.deadman_url:
        return False
    return _deliver(urllib.request.Request(
        ac.deadman_url, method="GET", headers={"User-Agent": f"recordings/{__version__}"}))
```

In `packages/core/src/recordings/state.py`, **append** to `SCHEMA`:
```sql
CREATE TABLE IF NOT EXISTS alerts (
  key TEXT PRIMARY KEY, failing INTEGER NOT NULL, last_sent_at TEXT);
```
and **add** to `State`:
```python
    # ---- alert state: one alert per failure, a daily reminder, and "recovered" ---------------
    def alert_state(self, key: str) -> tuple[bool, datetime | None]:
        with self.db() as db:
            row = db.execute("SELECT failing, last_sent_at FROM alerts WHERE key = ?",
                             (key,)).fetchone()
        if row is None:
            return False, None
        return bool(row[0]), datetime.fromisoformat(row[1]) if row[1] else None

    def set_alert_state(self, key: str, *, failing: bool, last_sent_at: datetime | None) -> None:
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO alerts (key, failing, last_sent_at) VALUES (?, ?, ?)",
                       (key, int(failing), last_sent_at.isoformat() if last_sent_at else None))
```

- [ ] **Step 4: Add the schedule, the disk job and alerting to `ops.py`**

In `packages/core/src/recordings/ops.py`:
1. **Add** the imports `import logging`, `import time`, `from datetime import timedelta`,
   `from recordings import alerts`, `from recordings.alerts import AlertConfig` and
   `from recordings.disk import disk_status, disk_thresholds`, plus
   `log = logging.getLogger(__name__)`.
2. **Add** before `JOBS`:
   ```python
   class DiskWarning(RuntimeError):
       """The archive's disk is below a threshold, or its marker is missing."""


   def _disk(ctx: Context, tags: tuple[str, ...]) -> str:
       if ctx.cfg.archive_path is None:
           raise ConfigError("set [archive] path")
       status = disk_status(ctx.cfg.archive_path, expected_uuid=ctx.state.meta("archive_uuid"),
                            **disk_thresholds(ctx.cfg))
       if status.level != "ok":
           raise DiskWarning(status.message)
       return status.message
   ```
3. **Add** `"disk": _disk,` to `JOBS`, and `DiskWarning` to `FAILURES`.
4. **Add** after `FAILURES`:
   ```python
   INTERVALS = {"disk": timedelta(minutes=10), "backup": timedelta(hours=1),
                "mirror": timedelta(hours=1), "prune": timedelta(days=1),
                "check": timedelta(days=7), "restore-test": timedelta(days=30)}
   ORDER = ("disk", "backup", "mirror", "prune", "check", "restore-test")
   RETRY_AFTER = timedelta(minutes=15)
   REMIND_EVERY = timedelta(hours=24)
   TITLES = {"disk": "disk space low", "backup": "backup failed", "mirror": "mirror failed",
             "prune": "backup retention failed", "check": "backup check failed",
             "restore-test": "restore test failed"}


   def enabled_jobs(cfg: Config) -> tuple[str, ...]:
       jobs = {"disk"}
       if cfg.data.get("backup", {}).get("repository"):
           jobs |= {"backup", "prune", "check", "restore-test"}
       if cfg.data.get("mirror", {}).get("target"):
           jobs.add("mirror")
       return tuple(j for j in ORDER if j in jobs)


   def due(state: State, now: datetime, jobs: tuple[str, ...]) -> list[str]:
       out = []
       for job in jobs:
           last = state.last_run(job)
           if last is not None and not last.ok and now - last.finished_at < RETRY_AFTER:
               continue
           good = state.last_run(job, ok=True)
           if good is None or now - good.finished_at >= INTERVALS[job]:
               out.append(job)
       return out


   def _alert_config(ctx: Context) -> AlertConfig | None:
       try:
           return alerts.from_config(ctx.cfg, ctx.environ)
       except ConfigError as exc:
           log.warning("alerts are not configured: %s", exc)
           return None


   def _after(ctx: Context, job: str, ok: bool, detail: str) -> None:
       """One alert when a job starts failing, a reminder every day while it fails, and a
       "recovered" when it works again. A good backup also pings the dead-man's switch."""
       ac = _alert_config(ctx)
       if ac is None:
           return
       if ok and job == "backup":
           alerts.ping_deadman(ac)
       key, now = f"job:{job}", ctx.clock()
       failing, last_sent = ctx.state.alert_state(key)
       if ok:
           if failing:
               alerts.send(ac, title=f"recordings: {job} recovered", message=detail, priority=3,
                           tags=("white_check_mark",))
               ctx.state.set_alert_state(key, failing=False, last_sent_at=now)
           return
       if not failing or last_sent is None or now - last_sent >= REMIND_EVERY:
           delivered = alerts.send(ac, title=f"recordings: {TITLES[job]}", message=detail)
           last_sent = now if delivered else last_sent
       ctx.state.set_alert_state(key, failing=True, last_sent_at=last_sent)
   ```
5. **Replace** `run_job` with:
   ```python
   def run_job(ctx: Context, job: str, *, tags: tuple[str, ...] = ()) -> JobResult:
       started = ctx.clock()
       try:
           detail, ok = JOBS[job](ctx, tags), True
       except FAILURES as exc:
           detail, ok = str(exc)[:500], False
       ctx.state.record_run(job, started, ctx.clock(), ok, detail)
       _after(ctx, job, ok, detail)
       return JobResult(job, ok, detail)


   def run_due(ctx: Context) -> list[JobResult]:
       ctx.state.set_meta("ops_heartbeat", ctx.clock().isoformat())
       return [run_job(ctx, job) for job in due(ctx.state, ctx.clock(), enabled_jobs(ctx.cfg))]


   def loop(ctx: Context, *, tick: float = 60.0, sleep=time.sleep, stop=lambda: False) -> None:
       """The backup service's main loop: what is due, then a minute's sleep, forever."""
       while not stop():
           for result in run_due(ctx):
               log.info("%s: %s (%s)", result.job, "ok" if result.ok else "FAILED", result.detail)
           sleep(tick)
   ```

In `packages/core/src/recordings/cli.py`, **add** `import logging` and `from recordings import alerts`,
then the parsers:
```python
    p = sub.add_parser("ops", help="run the scheduled jobs that are due (--loop: forever)")
    p.add_argument("--loop", action="store_true", help="the backup service's main loop")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("alert-test", help="send a test alert and ping the dead-man's switch")
    p.add_argument("--json", action="store_true")
```
and the commands:
```python
def cmd_ops(args: argparse.Namespace) -> int:
    ctx = _ops_context(args.json)
    if isinstance(ctx, int):
        return ctx
    if args.loop:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        ops.loop(ctx)
        return 0
    results = ops.run_due(ctx)
    _emit({"ran": [{"job": r.job, "ok": r.ok, "detail": r.detail} for r in results]}, args.json)
    return 0 if all(r.ok for r in results) else 1


def cmd_alert_test(args: argparse.Namespace) -> int:
    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    try:
        ac = alerts.from_config(cfg, os.environ)
    except config.ConfigError as exc:
        return _fail(str(exc), args.json, 78)
    if not ac.enabled:
        return _fail("set [alerts] ntfy_server and RECORDINGS_NTFY_TOPIC_FILE first", args.json, 78)
    sent = alerts.send(ac, title="recordings: test alert", message="If you see this, alerts work.",
                       priority=3, tags=("white_check_mark",))
    _emit({"ntfy": sent, "deadman": alerts.ping_deadman(ac)}, args.json)
    return 0 if sent else 1
```
with `"ops": cmd_ops, "alert-test": cmd_alert_test,` in `commands`.

In `config.example.toml`, **add** after `[mirror]`:
```toml
[alerts]                                # stage 2a: push alerts (spec §14)
ntfy_server = "https://ntfy.sh"         # or your own ntfy server (stage-2a plan, open question 6)
# The topic and the dead-man's switch URL are secrets: RECORDINGS_NTFY_TOPIC(_FILE) and
# RECORDINGS_DEADMAN_URL(_FILE), below. The dead-man's switch is an outside service that alerts
# when the hourly pings after each good backup stop (stage-2a plan, open question 1).
```
and in the secrets comment block:
```toml
#   RECORDINGS_NTFY_TOPIC       the private ntfy topic alerts go to                   (stage 2)
#   RECORDINGS_DEADMAN_URL      the dead-man's switch ping URL                        (stage 2)
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_alerts.py packages/core/tests/test_ops.py -v && uv run pytest`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/recordings packages/core/tests config.example.toml
git commit -m "feat(core): ntfy alerts, the dead-man's switch ping and the ops loop

One alert per failure, a daily reminder and a recovery message; a good backup pings the dead-man's
switch, which an outside service watches. The topic and the ping URL are secrets and never logged.
Checked: docs.ntfy.sh/publish (POST /<topic>, Title/Priority/Tags headers).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Docker and Compose for the first deploy

**Checkpoint lens:** operations.

**Files:**
- Modify: `docker/Dockerfile`, `docker/compose.yml` (whole files below), `Makefile` (`deploy`, `deploy-smoke`), `README.md` (the Docker and configuration sections), `.github/workflows/ci.yml` (the compose smoke job)
- Rename: `docker/deploy.example.env` → `docker/deploy.example.conf` (whole file below)
- Create: `scripts/compose_smoke.py`
- Modify: `packages/core/tests/test_deploy_template.py` (whole file below)

**Interfaces:**
- Consumes: the CLI commands from Tasks 2, 5, 9, 11, 12 and 13 (`recordings init`, `ops --loop` and the rest), and the secrets' `_FILE` names (Tasks 11 and 13).
- Produces:
  - **The deployment's shape:**
    - `docker/compose.yml` with the services `web` and `backup`
    - the named volume `index`
    - the secrets `restic_password`, `rest_password`, `nas_ssh_key`, `nas_known_hosts`, `ntfy_topic` and `deadman_url`
    - the deploy variables `RECORDINGS_{ARCHIVE,STATE,RESTORE,SECRETS,CONFIG,DEPLOY_ENV}_HOST`, `RECORDINGS_BIND`, `RECORDINGS_PORT`, `RECORDINGS_UID` and `RECORDINGS_GID`
  - **Image tags:** `RECORDINGS_IMAGE_TAG`, set by `make deploy` to `git rev-parse --short=12 HEAD`, with `-dirty` for uncommitted changes.
  - **Make targets:** `make deploy`, `make deploy-smoke`.

- [ ] **Step 1: Write the failing test**

Replace `packages/core/tests/test_deploy_template.py` with:
```python
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "docker" / "compose.yml"
TEMPLATE = REPO / "docker" / "deploy.example.conf"
SET_BY_MAKE = {"RECORDINGS_IMAGE_TAG"}


def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_every_compose_variable_is_documented_in_the_template_or_set_by_make():
    used = set(re.findall(r"\$\{(\w+)", COMPOSE.read_text(encoding="utf-8")))
    documented = set(re.findall(r"^(\w+)=", TEMPLATE.read_text(encoding="utf-8"), re.M))
    assert used, "compose.yml uses no variables?"
    assert used <= documented | SET_BY_MAKE, f"undocumented: {sorted(used - documented - SET_BY_MAKE)}"


def test_the_template_holds_no_secret_names():
    template = TEMPLATE.read_text(encoding="utf-8")
    for name in ("TOKEN", "API_KEY", "PASSWORD", "SECRET="):
        assert name not in template


def test_the_template_is_not_named_like_an_env_file():
    # why: §5. Dan's tool hook blocks reading .env names, and the template must stay readable.
    assert not (REPO / "docker" / "deploy.example.env").exists()
    for doc in ("README.md", "docs/runbooks/first-run.md", "Makefile"):
        assert "deploy.example.env" not in (REPO / doc).read_text(encoding="utf-8"), doc


def test_every_bind_is_long_syntax_and_never_creates_its_host_path():
    # why: Compose's long syntax defaults create_host_path to true; a folder made in place of an
    # unmounted disk would look like an empty archive.
    for name, service in compose()["services"].items():
        for volume in service.get("volumes", []):
            assert isinstance(volume, dict), f"{name}: short syntax {volume!r}"
            if volume["type"] == "bind":
                assert volume["bind"]["create_host_path"] is False, (name, volume["target"])


def test_the_state_and_the_index_have_mounts_of_their_own():
    doc = compose()
    web = {v["target"]: v for v in doc["services"]["web"]["volumes"]}
    assert web["/state"]["type"] == "bind" and web["/index"]["type"] == "volume"
    assert "index" in doc["volumes"]
    assert doc["services"]["web"]["environment"]["RECORDINGS_STATE"] == "/state"
    assert doc["services"]["web"]["environment"]["RECORDINGS_INDEX"] == "/index/index.db"


def test_every_service_rotates_its_logs_is_hardened_and_has_a_hostname():
    for name, service in compose()["services"].items():
        assert service["logging"]["driver"] == "json-file", name
        assert {"max-size", "max-file"} <= set(service["logging"]["options"]), name
        assert service["read_only"] is True and service["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in service["security_opt"], name
        assert service["hostname"].startswith("recordings-"), name
        assert service["user"] == "${RECORDINGS_UID:?}:${RECORDINGS_GID:?}", name


def test_images_are_tagged_by_commit():
    for name, service in compose()["services"].items():
        assert service["image"].startswith("recordings:${RECORDINGS_IMAGE_TAG:?"), name
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "git rev-parse --short=12 HEAD" in makefile
    assert "RECORDINGS_IMAGE_TAG=$(IMAGE_TAG)" in makefile


def test_the_backup_service_reads_the_archive_and_takes_secrets_only_from_files():
    service = compose()["services"]["backup"]
    archive = next(v for v in service["volumes"] if v["target"] == "/archive")
    assert archive["read_only"] is True
    env = service["environment"]
    for key, value in env.items():
        if key.endswith("_FILE"):
            assert value.startswith("/run/secrets/"), key
    for leaked in ("RESTIC_PASSWORD", "RECORDINGS_NTFY_TOPIC", "RECORDINGS_DEADMAN_URL"):
        assert leaked not in env
    assert service["command"] == ["recordings", "ops", "--loop"]
    assert set(service["secrets"]) <= set(compose()["secrets"])


def test_the_writer_identity_comes_from_config_never_the_host_name():
    config = (REPO / "config.example.toml").read_text(encoding="utf-8")
    assert 'writer_id = "homelab"' in config and "writer_host" not in config
    assert "WRITER" not in COMPOSE.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest packages/core/tests/test_deploy_template.py -v`
Expected: FAIL. `deploy.example.conf` doesn't exist yet, and the old `compose.yml` uses the short
syntax.

- [ ] **Step 3: Write the Docker files**

`git mv docker/deploy.example.env docker/deploy.example.conf`, then replace its contents with:
```sh
# Host-side settings for the Docker deployment. No secrets here: they are files in
# RECORDINGS_SECRETS_HOST (docs/runbooks/first-run.md, step 2).
#   cp docker/deploy.example.conf docker/deploy.env      (deploy.env is git-ignored)
#   make deploy
# Every folder and file named below must already exist. Compose won't create a missing one
# (create_host_path: false), so an unmounted disk stops the start instead of hiding.
# Relative paths are relative to docker/, where compose.yml lives.

# The archive, on the homelab server's own disk (spec §3). Mounted at /archive.
RECORDINGS_ARCHIVE_HOST=/srv/recordings/archive
# state.db and the lock files (spec §6.8). Mounted at /state.
RECORDINGS_STATE_HOST=/srv/recordings/state
# The monthly restore test's scratch folder (spec §15.1). Mounted at /restore in the backup service.
RECORDINGS_RESTORE_HOST=/srv/recordings/restore-test
# One file per secret, readable only by RECORDINGS_UID: restic_password, rest_password,
# nas_ssh_key, nas_known_hosts, ntfy_topic and deadman_url.
RECORDINGS_SECRETS_HOST=/srv/recordings/secrets
# Your config.toml (copied from config.example.toml). Mounted read-only.
RECORDINGS_CONFIG_HOST=../config.toml
# This file, backed up with the archive (spec §15.1). Mounted read-only in the backup service.
RECORDINGS_DEPLOY_ENV_HOST=./deploy.env
# Where to publish the app: this machine's Tailscale address, so only the tailnet reaches it.
# 127.0.0.1 keeps it local while testing.
RECORDINGS_BIND=127.0.0.1
RECORDINGS_PORT=8000
# The owner of /srv/recordings (`id -u` and `id -g` on the homelab server). The containers run as it.
RECORDINGS_UID=1000
RECORDINGS_GID=1000
```

Replace `docker/compose.yml` with:
```yaml
# The real deployment (the homelab server). Every ${VAR} comes from docker/deploy.env (git-ignored;
# documented in docker/deploy.example.conf), except RECORDINGS_IMAGE_TAG, which `make deploy` sets
# to the commit, so every image is tagged by the code it runs and a rollback is a tag.
#
# Binds use the long syntax with create_host_path: false. The long syntax's default is true
# (Compose docs, checked 2026-10-08), and a folder Docker made in place of an unmounted disk would
# look like an empty archive. With false, a missing folder or file stops the start instead.
name: recordings

x-logging: &logging
  driver: json-file
  options: { max-size: "10m", max-file: "5" }

x-hardening: &hardening
  read_only: true
  cap_drop: [ALL]
  security_opt: ["no-new-privileges:true"]
  tmpfs: ["/tmp"]

x-environment: &environment
  RECORDINGS_CONFIG: /config/config.toml
  RECORDINGS_ARCHIVE: /archive
  RECORDINGS_STATE: /state
  RECORDINGS_INDEX: /index/index.db
  HOME: /tmp

services:
  web:
    build: { context: .., dockerfile: docker/Dockerfile }
    image: "recordings:${RECORDINGS_IMAGE_TAG:?run make deploy, which tags the image by commit}"
    # A stable name for logs. The writer's identity is config.toml's writer_id, never this.
    hostname: recordings-web
    user: "${RECORDINGS_UID:?}:${RECORDINGS_GID:?}"
    <<: *hardening
    environment: *environment
    volumes:
      - type: bind
        source: "${RECORDINGS_CONFIG_HOST:?set RECORDINGS_CONFIG_HOST in docker/deploy.env}"
        target: /config/config.toml
        read_only: true
        bind: { create_host_path: false }
      - type: bind
        source: "${RECORDINGS_ARCHIVE_HOST:?set RECORDINGS_ARCHIVE_HOST in docker/deploy.env}"
        target: /archive
        bind: { create_host_path: false }
      - type: bind
        source: "${RECORDINGS_STATE_HOST:?set RECORDINGS_STATE_HOST in docker/deploy.env}"
        target: /state
        bind: { create_host_path: false }
      - type: volume
        source: index
        target: /index
    ports: ["${RECORDINGS_BIND:?}:${RECORDINGS_PORT:?}:8000"]
    logging: *logging
    restart: unless-stopped

  backup:
    image: "recordings:${RECORDINGS_IMAGE_TAG:?run make deploy, which tags the image by commit}"
    hostname: recordings-backup
    user: "${RECORDINGS_UID:?}:${RECORDINGS_GID:?}"
    <<: *hardening
    command: ["recordings", "ops", "--loop"]
    environment:
      <<: *environment
      RESTIC_PASSWORD_FILE: /run/secrets/restic_password
      RESTIC_REST_PASSWORD_FILE: /run/secrets/rest_password
      RECORDINGS_NAS_SSH_KEY_FILE: /run/secrets/nas_ssh_key
      RECORDINGS_NAS_KNOWN_HOSTS_FILE: /run/secrets/nas_known_hosts
      RECORDINGS_NTFY_TOPIC_FILE: /run/secrets/ntfy_topic
      RECORDINGS_DEADMAN_URL_FILE: /run/secrets/deadman_url
    secrets: [restic_password, rest_password, nas_ssh_key, nas_known_hosts, ntfy_topic, deadman_url]
    volumes:
      - type: bind
        source: "${RECORDINGS_CONFIG_HOST:?set RECORDINGS_CONFIG_HOST in docker/deploy.env}"
        target: /config/config.toml
        read_only: true
        bind: { create_host_path: false }
      - type: bind
        source: "${RECORDINGS_ARCHIVE_HOST:?set RECORDINGS_ARCHIVE_HOST in docker/deploy.env}"
        target: /archive
        read_only: true
        bind: { create_host_path: false }
      - type: bind
        source: "${RECORDINGS_STATE_HOST:?set RECORDINGS_STATE_HOST in docker/deploy.env}"
        target: /state
        bind: { create_host_path: false }
      - type: bind
        source: "${RECORDINGS_DEPLOY_ENV_HOST:?set RECORDINGS_DEPLOY_ENV_HOST in docker/deploy.env}"
        target: /backup-extra/deploy.env
        read_only: true
        bind: { create_host_path: false }
      - type: bind
        source: "${RECORDINGS_RESTORE_HOST:?set RECORDINGS_RESTORE_HOST in docker/deploy.env}"
        target: /restore
        bind: { create_host_path: false }
    # No web server here; the dead-man's switch watches this service instead (spec §14).
    healthcheck: { disable: true }
    logging: *logging
    restart: unless-stopped

volumes:
  index: {}  # derived: dropping it loses nothing (spec §6.8)

secrets:
  restic_password: { file: "${RECORDINGS_SECRETS_HOST:?set RECORDINGS_SECRETS_HOST in docker/deploy.env}/restic_password" }
  rest_password: { file: "${RECORDINGS_SECRETS_HOST:?set RECORDINGS_SECRETS_HOST in docker/deploy.env}/rest_password" }
  nas_ssh_key: { file: "${RECORDINGS_SECRETS_HOST:?set RECORDINGS_SECRETS_HOST in docker/deploy.env}/nas_ssh_key" }
  nas_known_hosts: { file: "${RECORDINGS_SECRETS_HOST:?set RECORDINGS_SECRETS_HOST in docker/deploy.env}/nas_known_hosts" }
  ntfy_topic: { file: "${RECORDINGS_SECRETS_HOST:?set RECORDINGS_SECRETS_HOST in docker/deploy.env}/ntfy_topic" }
  deadman_url: { file: "${RECORDINGS_SECRETS_HOST:?set RECORDINGS_SECRETS_HOST in docker/deploy.env}/deadman_url" }
```

In `docker/Dockerfile`, **replace** the runtime stage's first two lines
(`FROM python:3.14-slim AS runtime` and `WORKDIR /app`) with:
```dockerfile
FROM python:3.14-slim AS runtime
ARG TARGETARCH
ARG RESTIC_VERSION=0.19.1
# Stage 2a (spec §15.1, §9.1.1): ffprobe (ffmpeg) measures imported media; rsync and ssh run the
# NAS mirror and restic's SFTP backend; restic makes the backups. restic comes from its GitHub
# release, checked against that release's SHA256SUMS (2026-10-08); curl and bzip2 leave with the
# layer.
RUN set -eu; \
    apt-get update; \
    apt-get install -y --no-install-recommends ffmpeg rsync openssh-client ca-certificates curl bzip2; \
    case "$TARGETARCH" in \
      amd64) sum=f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c ;; \
      arm64) sum=a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465 ;; \
      *) echo "no pinned restic for $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 3 -o /tmp/restic.bz2 \
      "https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_${TARGETARCH}.bz2"; \
    echo "$sum  /tmp/restic.bz2" | sha256sum -c -; \
    bunzip2 -c /tmp/restic.bz2 > /usr/local/bin/restic; \
    chmod 0755 /usr/local/bin/restic; \
    rm /tmp/restic.bz2; \
    apt-get purge -y curl bzip2; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*; \
    restic version
# The index's named volume copies this folder's mode when Docker first creates it, so the
# containers' non-root user (RECORDINGS_UID, not known at build time) can write index.db there.
RUN install -d -m 1777 /index
WORKDIR /app
```
and **change** the first comment line to
`# Spec §4: Node 22 builds the React client; a Python 3.14 runtime serves it, with ffmpeg, rsync, ssh and restic.`

`scripts/compose_smoke.py`:
```python
"""Run the real docker/compose.yml once, against temporary folders, and check /healthz.

    uv run python scripts/compose_smoke.py        (or: make deploy-smoke)

It proves four things:
- every bind compose.yml needs exists, because create_host_path is false
- the image runs as this machine's user, not as root
- `recordings init` works inside it
- the web app answers

It never touches docker/deploy.env or a real archive. The secret files are placeholders: the smoke
test starts only `web` and contacts no NAS.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PORT = 18000
SECRETS = ("restic_password", "rest_password", "nas_ssh_key", "nas_known_hosts", "ntfy_topic",
           "deadman_url")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="recordings-smoke-") as tmp:
        base = Path(tmp)
        for name in ("archive", "state", "restore", "secrets"):
            (base / name).mkdir()
        for name in SECRETS:
            (base / "secrets" / name).write_text("smoke-test-placeholder\n", encoding="utf-8")
        (base / "config.toml").write_text('[archive]\nwriter_id = "smoke"\n', encoding="utf-8")
        deploy = base / "deploy.env"
        deploy.write_text("\n".join([
            f"RECORDINGS_ARCHIVE_HOST={base / 'archive'}", f"RECORDINGS_STATE_HOST={base / 'state'}",
            f"RECORDINGS_RESTORE_HOST={base / 'restore'}", f"RECORDINGS_SECRETS_HOST={base / 'secrets'}",
            f"RECORDINGS_CONFIG_HOST={base / 'config.toml'}", f"RECORDINGS_DEPLOY_ENV_HOST={deploy}",
            "RECORDINGS_BIND=127.0.0.1", f"RECORDINGS_PORT={PORT}",
            f"RECORDINGS_UID={os.getuid()}", f"RECORDINGS_GID={os.getgid()}"]) + "\n",
            encoding="utf-8")
        env = {**os.environ, "RECORDINGS_IMAGE_TAG": "smoke"}
        compose = ["docker", "compose", "-p", "recordings-smoke", "--env-file", str(deploy),
                   "-f", str(REPO / "docker" / "compose.yml")]
        try:
            subprocess.run([*compose, "build", "web"], env=env, check=True)
            subprocess.run([*compose, "run", "--rm", "web", "recordings", "init", "--json"],
                           env=env, check=True)
            subprocess.run([*compose, "up", "-d", "web"], env=env, check=True)
            deadline = time.monotonic() + 90
            while True:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=3) as r:
                        if json.loads(r.read()) == {"ok": True, "demo": False}:
                            break
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    subprocess.run([*compose, "logs", "web"], env=env)
                    print("compose_smoke: /healthz never answered", file=sys.stderr)
                    return 1
                time.sleep(2)
            if not (base / "archive" / "archive.json").is_file():
                print("compose_smoke: recordings init wrote no sentinel", file=sys.stderr)
                return 1
            print("compose smoke: init ran and web answered /healthz, as this machine's user")
            return 0
        finally:
            subprocess.run([*compose, "down", "-v"], env=env)


if __name__ == "__main__":
    raise SystemExit(main())
```

In the `Makefile`:
1. **Add**, below the `NVM :=` line:
   ```make
   # Images are tagged by the commit they run (spec §20, 2a); uncommitted changes add -dirty.
   IMAGE_TAG = $(shell git rev-parse --short=12 HEAD)$(shell git diff --quiet HEAD -- 2>/dev/null || echo -dirty)
   COMPOSE = RECORDINGS_IMAGE_TAG=$(IMAGE_TAG) docker compose --env-file docker/deploy.env -f docker/compose.yml
   ```
2. **Replace** the `deploy` target with:
   ```make
   deploy:
   	$(COMPOSE) up -d --build

   deploy-smoke:
   	uv run python scripts/compose_smoke.py
   ```
3. **Add** `deploy-smoke` to `.PHONY`.

In `README.md`, **replace** the `## Docker` and `## Configuration and secrets` sections with:
````markdown
## Docker

```bash
make docker                                         # demo mode, http://127.0.0.1:8000
cp config.example.toml config.toml                  # app settings (git-ignored)
cp docker/deploy.example.conf docker/deploy.env     # host folders, bind address, UID/GID (git-ignored)
make deploy                                         # builds, tags the image by commit, starts web + backup
make deploy-smoke                                   # the real compose file, once, against temporary folders
```

The first real run, step by step, is `docs/runbooks/first-run.md`.

## Configuration and secrets

| Setting | Where | Needed from |
|---|---|---|
| Archive, state and index paths, the writer ID, backups, mirror, alerts | `config.toml` (template: `config.example.toml`) | stage 1 / 2a |
| Host folders, config path, bind address and port, UID/GID | `docker/deploy.env` (template: `docker/deploy.example.conf`) | stage 1 |
| `RESTIC_PASSWORD`, the NAS SSH key and `known_hosts`, the ntfy topic, the dead-man's switch URL | secret files, as `…_FILE` (Docker secrets) | stage 2a |
| `RECORDINGS_SPARK_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` | environment, or `…_FILE` | stage 4 |

Secrets never go in `config.toml`, `deploy.env`, the image or the repo. `recordings doctor`
reports which are set, never their values. The containers run as a non-root user, with
read-only file systems.
````

In `.github/workflows/ci.yml`, **add** this job after `docker`:
```yaml
  compose-smoke:
    # The real compose file, once, as the runner's own user, against temporary folders (stage-1
    # carry-over): every bind must exist, init must work in the image, and /healthz must answer.
    runs-on: ubuntu-24.04
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with: { persist-credentials: false }
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with: { version: "0.12.23" }
      - run: uv run --frozen python scripts/compose_smoke.py
```

- [ ] **Step 4: Run the tests and the smoke test**

Run: `uv run pytest packages/core/tests/test_deploy_template.py -v && make deploy-smoke`
Expected: the template tests pass; the smoke test builds the image, runs `recordings init` as your
own UID, and prints `compose smoke: init ran and web answered /healthz, as this machine's user`.

- [ ] **Step 5: Commit**

```bash
git add docker scripts/compose_smoke.py Makefile README.md .github/workflows/ci.yml packages/core/tests/test_deploy_template.py
git commit -m "build(docker): the first deploy: web + backup, state and index mounts, secrets, hardening

Long-syntax binds with create_host_path: false (its long-syntax default is true: Compose docs
checked), log rotation, images tagged by commit, hostnames, read-only file systems with cap_drop
ALL and no-new-privileges. restic 0.19.1, ffmpeg, rsync and ssh in the image. deploy.example.env
is renamed deploy.example.conf, so the template is readable (spec §5). A compose smoke test runs
the real file once in CI.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 15: The Status page: backups and disk

**Checkpoint lens:** operations.

**Before writing any UI code, load the `/shinyreact-build-app` skill** (after `make skills`), and
check shadcn's docs for anything the page reuses. Note what you checked in the commit.
`reactive.invalidate_later` is core Shiny for Python; confirm it in the skill's references.

**Files:**
- Create: `packages/ui/src/recordings_ui/status.py`, `packages/ui/frontend/src/lib/nav.ts`, `packages/ui/frontend/src/lib/nav.test.ts`, `packages/ui/frontend/src/components/StatusPage.tsx`
- Modify: `packages/core/src/recordings/disk.py` (`disk_view`), `ops.py` (`when_label`, `status_summary`), `state.py` (`State.read_only`)
- Modify: `packages/ui/src/recordings_ui/runtime.py`, `views.py`, `shiny_app.py`, `app.py`
- Modify: `packages/ui/frontend/src/App.tsx`, `components/TopBar.tsx`, `components/DetailsTab.tsx`, `types.ts`, `app.css`
- Test: `packages/core/tests/test_ops.py` (the summary), `packages/ui/tests/test_status.py`, `test_shiny_server.py`, `test_demo_guard.py`, `e2e/test_library.py`

**Interfaces:**
- Consumes:
  - from Task 11: `State.last_run`
  - from Task 5: `State.flags`
  - from Task 9: `disk_status`, `disk_thresholds`
  - from Task 13: `ops.enabled_jobs`
- Produces:
  - **core:**
    - `recordings.disk.disk_view(status: DiskStatus) -> dict`
    - `recordings.ops.when_label(t: datetime, now: datetime, timezone_name: str) -> str`
    - `recordings.ops.status_summary(state: State, *, archive_root: Path, now: datetime, timezone_name: str, warn: float, stop: float, jobs: tuple[str, ...]) -> dict`
    - `State.read_only(path) -> State | None`
  - **UI, Python:**
    - `recordings_ui.status`: `@dataclass(frozen=True) StatusSettings(state_path, timezone_name, warn, stop, jobs)`, `status_settings(cfg) -> StatusSettings` and `status_view(archive_root: Path, settings: StatusSettings | None, *, now: datetime | None = None) -> dict`
    - `runtime.configure(archive, *, media_base=None, status=None)` and `runtime.status_settings()`
    - `views.status_view(archive, settings)`
    - the Shiny output `status`
  - **UI, TypeScript:**
    - `type Page = "library" | "status"`
    - `pageFromHash(hash: string): Page` and `hashFor(page: Page): string`
    - `interface StatusView`
    - `<StatusPage />`, and `<TopBar page onPage />`

- [ ] **Step 1: Write the failing tests**

Add to `packages/core/tests/test_ops.py`:
```python
def test_the_status_summary_flags_an_old_backup_a_failing_job_and_low_disk(writer, monkeypatch):
    from types import SimpleNamespace

    from recordings.ops import status_summary, when_label
    monkeypatch.setattr(disk.os, "statvfs", lambda p: SimpleNamespace(
        f_bavail=150, f_frsize=1024, f_blocks=1000))
    state = writer.state
    state.record_run("backup", T0 - timedelta(hours=3), T0 - timedelta(hours=3), True, "snapshot x")
    state.record_run("restore-test", T0, T0, False, "restore test failed: 1 problems")
    summary = status_summary(state, archive_root=writer.root, now=T0,
                             timezone_name="America/Vancouver", warn=20.0, stop=5.0,
                             jobs=("disk", "backup", "restore-test"))
    jobs = {j["job"]: j for j in summary["jobs"]}
    assert set(jobs) == {"backup", "restore-test"}
    assert jobs["backup"]["last_ok"] == "2026-10-08 22:00 (3 h ago)"  # 05:00 UTC is 22:00 PDT
    assert jobs["backup"]["attention"] and not jobs["backup"]["failing"]
    assert jobs["restore-test"]["failing"] and jobs["restore-test"]["last_ok"] is None
    assert summary["attention"][0] == "The last backup is more than 2 hours old."
    assert any("Restore test failed" in a for a in summary["attention"])
    assert summary["disk"]["level"] == "warn" and summary["disk"]["free_percent"] == 15.0
    assert when_label(T0 - timedelta(seconds=20), T0, "UTC") == "2026-10-09 07:59 (just now)"
    assert when_label(T0 - timedelta(days=3), T0, "UTC") == "2026-10-06 08:00 (3 days ago)"
```

`packages/ui/tests/test_status.py`:
```python
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from recordings_ui.status import StatusSettings, status_view

T0 = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)


def test_without_a_state_folder_status_shows_disk_only(demo_archive):
    view = status_view(demo_archive, None, now=T0)
    assert view["configured"] is False and view["jobs"] == []
    assert view["disk"]["marker"] is True and view["disk"]["level"] in ("ok", "warn", "stop")


def test_with_state_status_reads_the_runs(writer):
    writer.state.record_run("backup", T0 - timedelta(minutes=30), T0 - timedelta(minutes=30), True,
                            "snapshot x")
    settings = StatusSettings(state_path=writer.state.path, timezone_name="UTC", warn=20.0,
                              stop=5.0, jobs=("disk", "backup"))
    view = status_view(writer.root, settings, now=T0)
    (job,) = view["jobs"]
    assert view["configured"] and job["last_ok"] == "2026-10-09 07:30 (30 min ago)"
    assert not job["attention"]


def test_status_without_state_never_loads_sqlite(demo_archive):
    # why: the Pages demo runs on Pyodide, where sqlite3 is a separate module nobody has checked.
    code = ("import sys; from pathlib import Path; from recordings.archive import Archive; "
            "import recordings_ui.views as v; "
            f"v.status_view(Archive(Path({str(demo_archive)!r})), None); "
            "print('sqlite3' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
```

Add to `packages/ui/tests/test_shiny_server.py`:
```python
def test_status_is_published_and_unconfigured_in_the_demo(local_server: TestServerSession):
    status = local_server.get_output("status").value
    assert status["configured"] is False and status["disk"] is not None
```

In `packages/ui/tests/test_demo_guard.py`, **add** after the `views.recording_view` loop:
```python
        views.status_view(archive, None)  # the demo has no state: Status reads none
```

`packages/ui/frontend/src/lib/nav.test.ts`:
```ts
import { describe, expect, it } from "vitest";

import { hashFor, pageFromHash } from "./nav";

describe("pageFromHash", () => {
  it("reads the page from the hash, and anything else is the Library", () => {
    expect(pageFromHash("#/status")).toBe("status");
    expect(pageFromHash("#status")).toBe("status");
    expect(pageFromHash("#/status?x=1")).toBe("status");
    expect(pageFromHash("")).toBe("library");
    expect(pageFromHash("#/")).toBe("library");
    expect(pageFromHash("#/nonsense")).toBe("library");
  });
  it("round-trips", () => {
    expect(pageFromHash(hashFor("status"))).toBe("status");
    expect(pageFromHash(hashFor("library"))).toBe("library");
  });
});
```

Add to `packages/ui/tests/e2e/test_library.py`:
```python
def test_the_status_page_shows_backups_and_disk(page: Page, server_url):
    page.goto(server_url)
    page.get_by_role("button", name="Status").click()
    expect(page.get_by_test_id("status-page")).to_be_visible()
    expect(page.get_by_test_id("backups-unconfigured")).to_be_visible()
    expect(page.get_by_role("heading", name="Disk")).to_be_visible()
    expect(page).to_have_url(re.compile(r"#/status$"))
    page.get_by_role("button", name="Library").click()
    expect(page.get_by_test_id("status-page")).to_have_count(0)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_ops.py packages/ui/tests/test_status.py packages/ui/tests/test_shiny_server.py -v`, then `cd packages/ui/frontend && npm test`
Expected: FAIL with `ImportError: cannot import name 'status_summary'` and
`ModuleNotFoundError: No module named 'recordings_ui.status'`; Vitest fails to resolve `./nav`.

- [ ] **Step 3: Write the summary, the view and the Shiny output**

In `packages/core/src/recordings/disk.py`, **add**:
```python
def _size(n: int) -> str:
    for unit, scale in (("TB", 10 ** 12), ("GB", 10 ** 9), ("MB", 10 ** 6)):
        if n >= scale:
            return f"{n / scale:.1f} {unit}"
    return f"{n} bytes"


def disk_view(status: DiskStatus) -> dict:
    """What the Status page shows (spec §12.5): the level, the reason, and free space."""
    pct = status.free_percent
    label = (f"{_size(status.free_bytes)} free of {_size(status.total_bytes)}"
             if status.free_bytes is not None and status.total_bytes else None)
    return {"level": status.level, "message": status.message, "marker": status.marker,
            "free_percent": round(pct, 1) if pct is not None else None, "free_label": label}
```

In `packages/core/src/recordings/state.py`, **add** `from urllib.parse import quote`, set
`self._read_only = False` in `__init__`, and **add**:
```python
    @classmethod
    def read_only(cls, path: Path) -> State | None:
        """For readers (Status, doctor): never creates, migrates or writes anything."""
        state = cls(path)
        if not state.db_path.is_file():
            return None
        state._read_only = True
        return state
```
and **replace** the first line of `db()`'s body with:
```python
        target = (f"file:{quote(str(self.db_path))}?mode=ro" if self._read_only
                  else str(self.db_path))
        with closing(sqlite3.connect(target, timeout=30, uri=self._read_only)) as conn:
```

In `packages/core/src/recordings/ops.py`, **add** `from pathlib import Path`,
`from zoneinfo import ZoneInfo` and `from recordings.disk import disk_view`, then:
```python
SHOWN = (("backup", "Backup", timedelta(hours=2)),
         ("restore-test", "Restore test", timedelta(days=31)),
         ("check", "Repository check", timedelta(days=8)),
         ("mirror", "Mirror for the Mac", timedelta(hours=2)))


def when_label(t: datetime, now: datetime, timezone_name: str) -> str:
    """`2026-10-08 14:05 (35 min ago)`: ISO 8601 dates and 24-hour times (spec §12)."""
    minutes = int((now - t).total_seconds() // 60)
    if minutes < 1:
        ago = "just now"
    elif minutes < 60:
        ago = f"{minutes} min ago"
    elif minutes < 48 * 60:
        ago = f"{minutes // 60} h ago"
    else:
        ago = f"{minutes // 1440} days ago"
    return f"{t.astimezone(ZoneInfo(timezone_name)):%Y-%m-%d %H:%M} ({ago})"


def status_summary(state: State, *, archive_root: Path, now: datetime, timezone_name: str,
                   warn: float, stop: float, jobs: tuple[str, ...]) -> dict:
    """Status's backup and disk panels (spec §12.5). The backup needs attention after 2 hours."""
    disk = disk_view(disk_status(archive_root, expected_uuid=state.meta("archive_uuid"),
                                 warn=warn, stop=stop))
    flags = len(state.flags())
    out: dict = {"configured": True, "jobs": [], "attention": [], "flags": flags, "disk": disk}
    for job, label, stale_after in SHOWN:
        if job not in jobs:
            continue
        good, last = state.last_run(job, ok=True), state.last_run(job)
        failing = last is not None and not last.ok
        stale = good is None or now - good.finished_at > stale_after
        out["jobs"].append({
            "job": job, "label": label,
            "last_ok": when_label(good.finished_at, now, timezone_name) if good else None,
            "failing": failing, "detail": last.detail if failing else None,
            "attention": failing or stale})
        if job == "backup" and stale:
            out["attention"].append("The last backup is more than 2 hours old." if good
                                    else "No backup has finished yet.")
        if failing:
            out["attention"].append(f"{label} failed: {last.detail}")
    if disk["level"] != "ok":
        out["attention"].append(disk["message"])
    if flags:
        out["attention"].append(f"{flags} files need attention: run `recordings reindex`.")
    return out
```

`packages/ui/src/recordings_ui/status.py`:
```python
"""The Status page's view (spec §12.5): backups and disk, read-only.

The backup records come from state.db, opened read-only. sqlite3 is imported only when a state.db
exists, so the Pages demo (Pyodide), which has none, never needs it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class StatusSettings:
    state_path: Path | None
    timezone_name: str
    warn: float
    stop: float
    jobs: tuple[str, ...]


def status_settings(cfg) -> StatusSettings:
    """For the server only: it reads the schedule's jobs from config."""
    from recordings.config import ConfigError
    from recordings.disk import STOP_FREE_PERCENT, WARN_FREE_PERCENT, disk_thresholds
    from recordings.ops import enabled_jobs

    try:
        thresholds = disk_thresholds(cfg)
    except ConfigError:
        thresholds = {"warn": WARN_FREE_PERCENT, "stop": STOP_FREE_PERCENT}
    return StatusSettings(state_path=cfg.state_path, timezone_name=cfg.default_timezone,
                          warn=thresholds["warn"], stop=thresholds["stop"],
                          jobs=enabled_jobs(cfg))


def status_view(archive_root: Path, settings: StatusSettings | None, *,
                now: datetime | None = None) -> dict:
    from recordings.disk import STOP_FREE_PERCENT, WARN_FREE_PERCENT, disk_status, disk_view

    now = now or datetime.now(timezone.utc)
    state_db = settings.state_path / "state.db" if settings and settings.state_path else None
    if settings is None or state_db is None or not state_db.is_file():
        warn = settings.warn if settings else WARN_FREE_PERCENT
        stop = settings.stop if settings else STOP_FREE_PERCENT
        return {"configured": False, "jobs": [], "attention": [], "flags": 0,
                "disk": disk_view(disk_status(archive_root, expected_uuid=None, warn=warn,
                                              stop=stop))}
    from recordings.ops import status_summary
    from recordings.state import State

    return status_summary(State.read_only(settings.state_path), archive_root=archive_root,
                          now=now, timezone_name=settings.timezone_name, warn=settings.warn,
                          stop=settings.stop, jobs=settings.jobs)
```

Replace `packages/ui/src/recordings_ui/runtime.py` with:
```python
"""The archive the running app serves, and where Status reads. Set once by create_app (or tests)."""

from __future__ import annotations

from recordings.archive import Archive
from recordings_ui.status import StatusSettings

_archive: Archive | None = None
_media_base: str | None = None
_status: StatusSettings | None = None


def configure(archive: Archive | None, *, media_base: str | None = None,
              status: StatusSettings | None = None) -> None:
    """media_base: where the browser finds media files, as `<media_base><media.file>`.

    None (the server) links the FastAPI route /media/{id}. The static Pages demo, which has no
    FastAPI, sets "../media/" (spec §17.1). status: where Status reads its records; None in demo
    mode, which has no state folder.
    """
    global _archive, _media_base, _status
    _archive = archive
    _media_base = media_base
    _status = status


def archive() -> Archive:
    if _archive is None:
        raise RuntimeError("recordings_ui.runtime.configure() was not called")
    return _archive


def media_base() -> str | None:
    return _media_base


def status_settings() -> StatusSettings | None:
    return _status
```

In `packages/ui/src/recordings_ui/views.py`, **add** the import
`from recordings_ui.status import StatusSettings, status_view as _status_view`, then:
```python
def status_view(archive: Archive, settings: StatusSettings | None) -> dict:
    return _status_view(archive.root, settings)
```
and in `recording_view`, **replace** the `"sources": …` entry with:
```python
        "sources": [{"kind": s.kind, "ref": s.ref,
                     "added_at": (s.fetched_at or s.added_at).isoformat()} for s in rec.sources],
```

In `packages/ui/src/recordings_ui/shiny_app.py`, **replace** `from shiny import Inputs, Outputs, Session`
with `from shiny import Inputs, Outputs, Session, reactive`, and **add** inside `server`:
```python
    @reactive_output
    def status():
        reactive.invalidate_later(60)  # backups finish and disks fill without anyone clicking
        return views.status_view(archive, runtime.status_settings())
```

In `packages/ui/src/recordings_ui/app.py`, **add** `from recordings_ui.status import status_settings`,
and **replace** `runtime.configure(archive)` with:
```python
    runtime.configure(archive, status=status_settings(settings.config) if settings.config else None)
```

- [ ] **Step 4: Write the page**

`packages/ui/frontend/src/lib/nav.ts`:
```ts
import type { Page } from "../types";

/** The page a URL's hash names: `#/status`, or the Library for anything else. */
export function pageFromHash(hash: string): Page {
  const first = hash.replace(/^#\/?/, "").split(/[/?]/)[0];
  return first === "status" ? "status" : "library";
}

export function hashFor(page: Page): string {
  return page === "status" ? "#/status" : "#/";
}
```

Add to `packages/ui/frontend/src/types.ts`:
```ts
export type Page = "library" | "status";

export interface StatusJob {
  job: string; label: string; last_ok: string | null; failing: boolean; detail: string | null; attention: boolean;
}

export interface StatusView {
  configured: boolean; jobs: StatusJob[]; attention: string[]; flags: number;
  disk: { level: "ok" | "warn" | "stop"; message: string; marker: boolean; free_percent: number | null; free_label: string | null } | null;
}
```

`packages/ui/frontend/src/components/StatusPage.tsx`:
```tsx
import { useShinyOutputValue } from "../sr";
import type { StatusView } from "../types";

/** Status's backup and disk panels (spec §12.5), read-only. Alerts are the push view (§14). */
export function StatusPage() {
  const status = useShinyOutputValue<StatusView>("status");
  if (!status) return <main className="status"><p className="empty">Loading…</p></main>;
  const disk = status.disk;
  return (
    <main className="status" data-testid="status-page">
      {status.attention.length > 0 && (
        <div className="problems" role="status" data-testid="status-attention">
          Needs attention:
          <ul>{status.attention.map((a) => <li key={a}>{a}</li>)}</ul>
        </div>
      )}
      <section className="card" aria-labelledby="status-backups">
        <h2 id="status-backups">Backups</h2>
        {status.configured ? (
          <table className="details">
            <thead><tr><th>Job</th><th>Last good run</th><th>State</th></tr></thead>
            <tbody>
              {status.jobs.map((j) => (
                <tr key={j.job} className={j.attention ? "attn" : undefined} data-testid={`status-job-${j.job}`}>
                  <td>{j.label}</td>
                  <td>{j.last_ok ?? "never"}</td>
                  <td>{j.failing ? `Failing: ${j.detail}` : j.attention ? "Overdue" : "OK"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted" data-testid="backups-unconfigured">
            Backups aren't set up here. On the homelab server, the backup service runs them every hour.
          </p>
        )}
      </section>
      <section className="card" aria-labelledby="status-disk">
        <h2 id="status-disk">Disk</h2>
        {disk ? (
          <>
            {disk.free_percent !== null && (
              <div className={`meter ${disk.level}`} role="meter" aria-label="Disk in use" aria-valuemin={0}
                aria-valuemax={100} aria-valuenow={Math.round(100 - disk.free_percent)}>
                <span style={{ width: `${100 - disk.free_percent}%` }} />
              </div>
            )}
            <p>{disk.free_label ?? disk.message}</p>
            {disk.level !== "ok" && <p className="warn" data-testid="disk-warning">{disk.message}</p>}
          </>
        ) : <p className="muted">Free space unknown.</p>}
      </section>
    </main>
  );
}
```

Replace `packages/ui/frontend/src/components/TopBar.tsx` with:
```tsx
import type { Page } from "../types";
import { ThemeSwitch } from "./ThemeSwitch";

const PAGES: { page: Page; label: string }[] = [{ page: "library", label: "Library" }, { page: "status", label: "Status" }];

export function TopBar({ page, onPage }: { page: Page; onPage: (p: Page) => void }) {
  return (
    <header className="topbar">
      <span className="logo"><i aria-hidden />Recordings</span>
      <nav aria-label="Pages">
        {PAGES.map((p) => (
          <button key={p.page} type="button" className={`nav${page === p.page ? " on" : ""}`}
            aria-current={page === p.page ? "page" : undefined} onClick={() => onPage(p.page)}>
            {p.label}
          </button>
        ))}
      </nav>
      <ThemeSwitch />
    </header>
  );
}
```

Replace `packages/ui/frontend/src/App.tsx` with:
```tsx
import { useEffect, useMemo, useState } from "react";

import { RecordingList } from "./components/RecordingList";
import { RecordingPane } from "./components/RecordingPane";
import { Sidebar } from "./components/Sidebar";
import { StatusPage } from "./components/StatusPage";
import { TopBar } from "./components/TopBar";
import { hashFor, pageFromHash } from "./lib/nav";
import { filterRows } from "./lib/transcript";
import { useShinyInitialized, useShinyInput, useShinyOutputValue } from "./sr";
import type { Filter, LibraryView, Page } from "./types";

export default function App() {
  const initialized = useShinyInitialized();
  const library = useShinyOutputValue<LibraryView>("library");
  const [selectedId, setSelectedId] = useShinyInput<string | null>("selected_id", null);
  const [filter, setFilter] = useState<Filter>({ kind: "all" });
  const [page, setPage] = useState<Page>(() => pageFromHash(window.location.hash));
  const rows = useMemo(() => (library ? filterRows(library.recordings, filter) : []), [library, filter]);

  // Hash routes: Back returns to the page you came from, and the server never logs them.
  useEffect(() => {
    const onHash = () => setPage(pageFromHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const go = (next: Page) => {
    window.location.hash = hashFor(next);
    setPage(next);
  };

  if (!initialized) return null; // no flash of empty defaults during connection
  return (
    <div className="app">
      <TopBar page={page} onPage={go} />
      {page === "status" ? <StatusPage /> : library ? (
        <main className="panes">
          <Sidebar library={library} filter={filter} onFilter={setFilter} />
          <RecordingList rows={rows} selectedId={selectedId} onSelect={setSelectedId} />
          <RecordingPane selectedId={selectedId} />
        </main>
      ) : <p className="empty">Loading…</p>}
    </div>
  );
}
```

In `packages/ui/frontend/src/components/DetailsTab.tsx`, **replace**
`{rec.sources.map((s) => <tr key={s.kind + s.ref}>` with
`{rec.sources.map((s, i) => <tr key={i}>`: a recording now has one source entry per snapshot, so
kind and reference repeat.

Append to `packages/ui/frontend/src/app.css`:
```css
button.nav { background: none; border: 0; border-bottom: 2px solid transparent; font: inherit; cursor: pointer; padding: 0 0 2px; margin-right: 12px; }
.status { flex: 1; min-height: 0; overflow: auto; padding: 16px; display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); align-content: start; }
.status .problems { grid-column: 1 / -1; margin: 0; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
.card h2 { margin: 0 0 8px; font-size: 15px; }
tr.attn td { color: var(--destructive); }
.meter { height: 10px; border-radius: 999px; background: var(--muted); overflow: hidden; margin: 6px 0; }
.meter span { display: block; height: 100%; background: var(--primary); }
.meter.warn span { background: var(--brand-orange); }
.meter.stop span { background: var(--destructive); }
.warn { color: var(--destructive); }
```

- [ ] **Step 5: Run every test, the frontend, the browser tests and the Pages demo**

Run: `make test && make e2e && make pages && make pages-test`
Expected: pytest, Vitest (with `nav.test.ts`) and the Playwright tests all pass,
`test_the_status_page_shows_backups_and_disk` included. The Pages smoke test still passes: the
static demo (Pyodide) now runs the Status output too, with no state folder and no `sqlite3`.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/recordings packages/core/tests/test_ops.py packages/ui
git commit -m "feat(ui): the Status page's backup and disk panels, read-only

Backups need attention after 2 hours (spec §12.5), and the disk warns below 20% and stops imports
below 5%. Status reads state.db read-only, and never loads sqlite3 without one, so the Pages demo
is unaffected. Checked: the shinyreact-build-app skill (reactive_output, useShinyOutputValue) and
Shiny's reactive.invalidate_later.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: `recordings doctor` for 2a

**Checkpoint lens:** security and privacy.

**Files:**
- Create: `packages/core/src/recordings/doctor.py`
- Modify: `packages/core/src/recordings/config.py` (remove `doctor`), `cli.py` (`doctor` uses `recordings.doctor`)
- Modify: `packages/core/tests/test_config.py` (imports `doctor` from `recordings.doctor`; the presence test initialises its archive)
- Test: `packages/core/tests/test_doctor.py`

**Interfaces:**
- Consumes:
  - from Tasks 2, 11 and 13: `SECRETS`, `secret`, `secret_path`
  - from Task 2: `read_sentinel`
  - from Task 15: `State.read_only`
  - from Task 4: `Index`
  - from Task 11: `backup.from_config`
  - from Task 12: `mirror.from_config`, `is_remote`
  - from Task 13: `alerts.from_config`
- Produces:
  - `recordings.doctor.doctor(environ) -> dict`, with the keys `config`, `archive`, `state`, `index`, `backup`, `mirror`, `alerts`, `tools`, `secrets`, `problems` and `warnings`
  - `recordings.doctor.hard_links_work(root: Path) -> bool`
  - `recordings.doctor.TOOLS`

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_doctor.py`:
```python
import json
import os

import pytest

from recordings import doctor as doctor_module
from recordings.cli import main
from recordings.doctor import doctor
from recordings.init import init_archive
from recordings.sentinel import SENTINEL

SENTINELS = ("pw-do-not-print", "key-do-not-print", "topic-do-not-print", "ping-do-not-print",
             "hunter2")


def server(tmp_path, *, repository="sftp:backup@nas:/volume1/backups/recordings"):
    init_archive(tmp_path / "archive", tmp_path / "state", writer_id="homelab")
    (tmp_path / "scratch").mkdir()
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    for name, value in (("pw", "pw-do-not-print"), ("key", "key-do-not-print"),
                        ("known_hosts", "nas ssh-ed25519 AAAAexample"),
                        ("topic", "topic-do-not-print"), ("ping", "https://hc.example/ping-do-not-print")):
        (secrets / name).write_text(value + "\n", encoding="utf-8")
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{tmp_path / "archive"}"\nwriter_id = "homelab"\n'
        f'[state]\npath = "{tmp_path / "state"}"\n[index]\npath = "{tmp_path / "index.db"}"\n'
        f'[nas]\nssh = "backup@nas"\n'
        f'[backup]\nrepository = "{repository}"\nrestore_scratch = "{tmp_path / "scratch"}"\n'
        f'nas_repo_path = "/volume1/backups/recordings"\n'
        f'[mirror]\ntarget = "backup@nas:/volume1/recordings-mirror/"\n'
        f'[alerts]\nntfy_server = "https://ntfy.sh"\n', encoding="utf-8")
    return {"RECORDINGS_CONFIG": str(cfg), "RESTIC_PASSWORD_FILE": str(secrets / "pw"),
            "RECORDINGS_NAS_SSH_KEY_FILE": str(secrets / "key"),
            "RECORDINGS_NAS_KNOWN_HOSTS_FILE": str(secrets / "known_hosts"),
            "RECORDINGS_NTFY_TOPIC_FILE": str(secrets / "topic"),
            "RECORDINGS_DEADMAN_URL_FILE": str(secrets / "ping")}


@pytest.fixture(autouse=True)
def tools_installed(monkeypatch):
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: f"/usr/bin/{name}")


def test_a_fully_configured_writer_has_no_problems(tmp_path):
    report = doctor(server(tmp_path))
    assert report["problems"] == []
    assert report["archive"]["sentinel"] and report["archive"]["hard_links"]
    assert report["state"]["matches"] is True
    assert report["backup"] == {"transport": "sftp", "password_set": True, "prune": True,
                                "restore_scratch_exists": True, "nas_free_check": True,
                                "restic_installed": True}
    assert report["mirror"] == {"remote": True, "rsync_installed": True}
    assert report["alerts"] == {"ntfy_server_set": True, "topic_set": True, "deadman_set": True}
    for name in ("restic_password", "nas_ssh_key", "nas_known_hosts", "ntfy_topic", "deadman_url"):
        assert report["secrets"][name]["set"], name
    assert report["secrets"]["nas_ssh_key"]["env"] == "RECORDINGS_NAS_SSH_KEY_FILE"


def test_doctor_never_prints_a_secret_or_the_repository(tmp_path):
    environ = server(tmp_path, repository="rest:https://recordings:hunter2@nas:8000/recordings/")
    text = json.dumps(doctor(environ))
    for value in SENTINELS:
        assert value not in text
    assert "nas:8000" not in text


def test_a_missing_sentinel_or_another_archives_state_is_a_problem(tmp_path):
    environ = server(tmp_path)
    (tmp_path / "archive" / SENTINEL).unlink()
    assert any("mounted" in p for p in doctor(environ)["problems"])


def test_state_from_another_writer_is_a_problem(tmp_path):
    environ = server(tmp_path)
    cfg = tmp_path / "config.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace('writer_id = "homelab"',
                                                           'writer_id = "laptop"'), encoding="utf-8")
    report = doctor(environ)
    assert report["state"]["matches"] is False
    assert any("another archive or another writer_id" in p for p in report["problems"])


def test_a_broken_config_still_reports_the_secrets(tmp_path):
    # why: stage-1 carry-over.
    environ = server(tmp_path)
    (tmp_path / "config.toml").write_text("path = [unclosed", encoding="utf-8")
    report = doctor(environ)
    assert report["secrets"]["restic_password"]["set"] is True
    assert any("config.toml" in p for p in report["problems"])


def test_a_key_given_as_a_value_is_a_problem(tmp_path):
    environ = server(tmp_path) | {"RECORDINGS_NAS_SSH_KEY": "key-do-not-print"}
    report = doctor(environ)
    assert any("RECORDINGS_NAS_SSH_KEY_FILE" in p for p in report["problems"])
    assert "key-do-not-print" not in json.dumps(report)


def test_an_empty_optional_secret_is_only_a_warning(tmp_path):
    environ = server(tmp_path)
    (tmp_path / "secrets" / "rest").write_text("", encoding="utf-8")
    report = doctor(environ | {"RESTIC_REST_PASSWORD_FILE": str(tmp_path / "secrets" / "rest")})
    assert report["problems"] == [] and any("is empty" in w for w in report["warnings"])


def test_hard_links_that_do_not_work_are_a_problem(tmp_path, monkeypatch):
    def no_links(src, dst):
        raise OSError("Operation not permitted")

    environ = server(tmp_path)  # before the patch: init itself publishes with a hard link
    monkeypatch.setattr(doctor_module.os, "link", no_links)
    report = doctor(environ)
    assert report["archive"]["hard_links"] is False
    assert any("hard links" in p for p in report["problems"])
    assert not (tmp_path / "archive" / ".tmp").exists()  # the probe leaves nothing behind


def test_missing_tools_and_alerts_are_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: None)
    environ = {k: v for k, v in server(tmp_path).items() if "DEADMAN" not in k}
    report = doctor(environ)
    assert report["tools"] == {"restic": False, "rsync": False, "ssh": False, "ffprobe": False}
    assert any("restic is not installed" in p for p in report["problems"])
    assert any("dead-man" in w for w in report["warnings"])
    assert any("ffprobe" in w for w in report["warnings"])


def test_the_cli_exits_0_when_all_is_well(tmp_path, monkeypatch, capsys):
    for key, value in server(tmp_path).items():
        monkeypatch.setenv(key, value)
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX"):
        monkeypatch.delenv(name, raising=False)
    assert main(["doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["problems"] == []
```

In `packages/core/tests/test_config.py`, **replace**
`from recordings.config import SECRETS, ConfigError, doctor, load_config, secret` with:
```python
from recordings.config import SECRETS, ConfigError, load_config, secret
from recordings.doctor import doctor
from recordings.init import init_archive
```
and in `test_doctor_reports_presence_never_values`, **replace** the two lines
`archive = tmp_path / "archive"` and `archive.mkdir()` with:
```python
    archive = tmp_path / "archive"
    init_archive(archive, tmp_path / "state", writer_id="test")  # a real, initialised archive
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_doctor.py packages/core/tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings.doctor'`.

- [ ] **Step 3: Write `doctor`**

`packages/core/src/recordings/doctor.py`:
```python
"""`recordings doctor` (spec §5, §6.7, §15.1): what is configured, as presence only.

It never prints a secret's value, nor a repository URL, which can carry a password: only whether
each is set and which transport it is. It reports the secrets first, so a broken config.toml still
shows them (stage-1 carry-over). It probes hard links on the archive's disk, because write-once
publishing needs them, and it reports whether this machine's state.db matches the archive and
writer ID.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from recordings import alerts, backup, mirror
from recordings.config import SECRETS, Config, ConfigError, load_config, secret, secret_path
from recordings.index import Index
from recordings.sentinel import SentinelError, read_sentinel
from recordings.state import State

TOOLS = ("restic", "rsync", "ssh", "ffprobe")


def _secrets(environ: Mapping[str, str], problems: list[str], warnings: list[str]) -> dict:
    out = {}
    for name, spec in SECRETS.items():
        try:
            found = secret_path(name, environ) if spec.file_only else secret(name, environ)
            present = found is not None
        except ConfigError as exc:
            (warnings if spec.optional else problems).append(str(exc))
            present = False
        out[name] = {"env": f"{spec.env}_FILE" if spec.file_only else spec.env, "set": present,
                     "needed_from_stage": spec.stage, "purpose": spec.purpose}
    return out


def hard_links_work(root: Path) -> bool:
    """Try one hard link inside the archive's .tmp/ (which readers and mirrors skip), and leave
    nothing behind."""
    tmp = Path(root) / ".tmp"
    made = not tmp.exists()
    first = tmp / f".doctor-{uuid.uuid4().hex}"
    second = first.with_name(first.name + "-link")
    try:
        tmp.mkdir(exist_ok=True)
        first.write_bytes(b"")
        os.link(first, second)
        return True
    except OSError:
        return False
    finally:
        for path in (second, first):
            with contextlib.suppress(OSError):
                path.unlink()
        if made:
            with contextlib.suppress(OSError):
                tmp.rmdir()


def _archive(cfg: Config, report: dict, problems: list[str]) -> str | None:
    if cfg.archive_path is None:
        problems.append("no archive path: set [archive] path in config.toml, or RECORDINGS_ARCHIVE")
        return None
    info = {"path": str(cfg.archive_path), "exists": cfg.archive_path.is_dir(),
            "sentinel": False, "hard_links": None}
    report["archive"] = info
    if not info["exists"]:
        problems.append(f"archive folder {cfg.archive_path} does not exist")
        return None
    try:
        identity = read_sentinel(cfg.archive_path)
    except SentinelError as exc:
        problems.append(str(exc))
        return None
    info["sentinel"] = True
    info["hard_links"] = hard_links_work(cfg.archive_path)
    if not info["hard_links"]:
        problems.append("hard links don't work on the archive's disk, and write-once publishing "
                        "needs them: is it a network share?")
    return identity.uuid


def _state(cfg: Config, archive_uuid: str | None, report: dict, problems: list[str],
           warnings: list[str]) -> None:
    if cfg.writer_id is None:
        warnings.append("no [archive] writer_id: this machine can't write the archive")
    if cfg.state_path is None:
        warnings.append("no [state] path: this machine can't write the archive")
        return
    info = {"path": str(cfg.state_path), "state_db": (cfg.state_path / "state.db").is_file(),
            "writer_id": cfg.writer_id, "matches": None}
    report["state"] = info
    if not info["state_db"]:
        warnings.append("no state.db yet: run `recordings init` once, on the writer")
        return
    state = State.read_only(cfg.state_path)
    info["matches"] = (state.meta("archive_uuid") == archive_uuid
                       and state.meta("writer_id") == cfg.writer_id)
    if not info["matches"]:
        problems.append("state.db belongs to another archive or another writer_id: this machine "
                        "must not write here")


def _transport(repository: str) -> str:
    for prefix, name in (("sftp:", "sftp"), ("rest:", "rest"), ("/", "local")):
        if repository.startswith(prefix):
            return name
    return "other"


def _backup(cfg: Config, environ: Mapping[str, str], report: dict, problems: list[str],
            warnings: list[str]) -> None:
    if not cfg.data.get("backup", {}).get("repository"):
        warnings.append("backups aren't configured: set [backup] repository")
        return
    try:
        bc = backup.from_config(cfg, environ)
    except ConfigError as exc:
        problems.append(str(exc))
        return
    info = {"transport": _transport(bc.repository),
            "password_set": report["secrets"]["restic_password"]["set"],
            "prune": bc.prune,
            "restore_scratch_exists": bool(bc.restore_scratch and bc.restore_scratch.is_dir()),
            "nas_free_check": bool(bc.nas.ssh and bc.nas_repo_path),
            "restic_installed": shutil.which(bc.restic) is not None}
    report["backup"] = info
    if not info["password_set"]:
        problems.append("RESTIC_PASSWORD_FILE is not set, so backups can't run")
    if info["transport"] == "sftp" and (bc.nas.key is None or bc.nas.known_hosts is None):
        problems.append("an SFTP repository needs RECORDINGS_NAS_SSH_KEY_FILE and "
                        "RECORDINGS_NAS_KNOWN_HOSTS_FILE")
    if not info["restic_installed"]:
        problems.append(f"{bc.restic} is not installed")
    if not info["restore_scratch_exists"]:
        warnings.append("[backup] restore_scratch doesn't exist, so the restore test can't run")
    if not info["nas_free_check"]:
        warnings.append("the NAS's free space isn't checked: set [nas] ssh and [backup] "
                        "nas_repo_path (it needs a shell account)")


def _mirror(cfg: Config, environ: Mapping[str, str], report: dict, problems: list[str],
            warnings: list[str]) -> None:
    if not cfg.data.get("mirror", {}).get("target"):
        warnings.append("the mirror isn't configured: set [mirror] target")
        return
    try:
        mc = mirror.from_config(cfg, environ)
    except ConfigError as exc:
        problems.append(str(exc))
        return
    info = {"remote": mirror.is_remote(mc.target),
            "rsync_installed": shutil.which(mc.rsync) is not None}
    report["mirror"] = info
    if info["remote"] and (mc.nas.key is None or mc.nas.known_hosts is None):
        problems.append("a remote mirror needs RECORDINGS_NAS_SSH_KEY_FILE and "
                        "RECORDINGS_NAS_KNOWN_HOSTS_FILE")
    if not info["rsync_installed"]:
        problems.append(f"{mc.rsync} is not installed")


def _alerts(cfg: Config, environ: Mapping[str, str], report: dict, problems: list[str],
            warnings: list[str]) -> None:
    try:
        ac = alerts.from_config(cfg, environ)
    except ConfigError as exc:
        if "ntfy_server" in str(exc):
            problems.append(str(exc))
        return  # a bad secret file is already reported under secrets
    report["alerts"] = {"ntfy_server_set": bool(ac.ntfy_server), "topic_set": bool(ac.topic),
                        "deadman_set": bool(ac.deadman_url)}
    if not ac.enabled:
        warnings.append("alerts are off: set [alerts] ntfy_server and RECORDINGS_NTFY_TOPIC_FILE")
    if not ac.deadman_url:
        warnings.append("no dead-man's switch, so a stopped server or backup goes unnoticed: "
                        "set RECORDINGS_DEADMAN_URL_FILE")


def doctor(environ: Mapping[str, str]) -> dict[str, Any]:
    problems: list[str] = []
    warnings: list[str] = []
    report: dict[str, Any] = {
        "config": None, "archive": None, "state": None, "index": None, "backup": None,
        "mirror": None, "alerts": None,
        "tools": {tool: shutil.which(tool) is not None for tool in TOOLS},
        "secrets": _secrets(environ, problems, warnings),
        "problems": problems, "warnings": warnings,
    }
    try:
        cfg = load_config(environ)
    except ConfigError as exc:
        problems.append(str(exc))
        return report
    report["config"] = str(cfg.path) if cfg.path else None
    archive_uuid = _archive(cfg, report, problems)
    _state(cfg, archive_uuid, report, problems, warnings)
    if cfg.index_path is not None:
        index = Index(cfg.index_path)
        report["index"] = {"path": str(cfg.index_path), "exists": index.exists(),
                           "recordings": index.count()}
    _backup(cfg, environ, report, problems, warnings)
    _mirror(cfg, environ, report, problems, warnings)
    _alerts(cfg, environ, report, problems, warnings)
    if not report["tools"]["ffprobe"]:
        warnings.append("ffprobe is not installed, so imported media's duration, sample rate "
                        "and channels won't be measured")
    return report
```

In `packages/core/src/recordings/config.py`, **delete** the `doctor` function. In `cli.py`,
**add** `from recordings.doctor import doctor`, and **replace** `report = config.doctor(os.environ)`
with `report = doctor(os.environ)`.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_doctor.py packages/core/tests/test_config.py -v && uv run pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/recordings packages/core/tests/test_doctor.py packages/core/tests/test_config.py
git commit -m "feat(core): doctor checks the state, sentinel, writer ID, backups, mirror and alerts

Presence only: no secret value and no repository URL ever reaches its output. It reports the
secrets even when config.toml is broken, and probes hard links on the archive's disk (stage-1
carry-overs).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: The 2a flow end to end, CI and the docs

**Checkpoint lens:** operations.

**Files:**
- Create: `packages/core/tests/test_flow_2a.py`
- Modify: `packages/core/tests/test_runbook.py` (every command it names exists), `.github/workflows/ci.yml` (restic for the tests), `CLAUDE.md`, `README.md`
- Modify: `docs/runbooks/first-run.md`, only where the forward reviews found drift

**Interfaces:**
- Consumes: every command, through `recordings.cli.main`.
- Produces: a test of the 2a flow; CI that runs it with the pinned restic; and project docs that describe the write path.

- [ ] **Step 1: Write the end-to-end test**

`packages/core/tests/test_flow_2a.py`:
```python
"""Stage 2a end to end, in a temporary folder (spec §20). Through the CLI, in the runbook's order:
init, import a synthetic audio-router archive (a dry run first), validate, back up to a local
restic repository, and run the restore test."""

import json
import os
import shutil
from pathlib import Path

import pytest

from recordings.cli import main
from recordings.state import State

REPO = Path(__file__).resolve().parents[3]
A, B, P = "a" * 32, "b" * 32, "e" * 32
UUID = "11111111-1111-4111-8111-111111111111"


def restic_or_skip() -> str:
    for candidate in (os.environ.get("RECORDINGS_TEST_RESTIC"), str(REPO / ".cache" / "bin" / "restic"),
                      shutil.which("restic")):
        if candidate and Path(candidate).is_file():
            return candidate
    pytest.skip("restic is not installed: `make restic` puts the pinned one in .cache/bin")


def setup(tmp_path, monkeypatch, ar, plaud_env, restic="restic"):
    ar.plaud(A, envelopes=[plaud_env(A), plaud_env(A, name="Week 4, edited")])
    ar.plaud(B, envelopes=[plaud_env(B)], same_audio_as=f"plaud/audio/2026/08/{A}.wav",
             dup_of=f"plaud/{A}")
    ar.plaud("c" * 32, envelopes=[plaud_env("c" * 32)], audio=False)
    ar.recorder()
    ar.pocket(UUID)
    ar.private(P, envelopes=[plaud_env(P, name="A PRIVATE TITLE")])
    ar.ledger_only = ["f" * 32]
    ar.write_catalog()
    (tmp_path / "pw").write_text("an end-to-end password\n", encoding="utf-8")
    (tmp_path / "deploy.env").write_text("RECORDINGS_PORT=8000\n", encoding="utf-8")
    (tmp_path / "scratch").mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{tmp_path / "archive"}"\nwriter_id = "homelab"\n'
        f'[state]\npath = "{tmp_path / "state"}"\n[index]\npath = "{tmp_path / "index" / "index.db"}"\n'
        f'[backup]\nrepository = "{tmp_path / "repo"}"\nrestic = "{restic}"\n'
        f'extra_paths = ["{tmp_path / "deploy.env"}"]\nrestore_scratch = "{tmp_path / "scratch"}"\n'
        f'cache_dir = "{tmp_path / "cache"}"\n', encoding="utf-8")
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    monkeypatch.setenv("RESTIC_PASSWORD_FILE", str(tmp_path / "pw"))
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX", "RESTIC_PASSWORD",
                 "RESTIC_REPOSITORY"):
        monkeypatch.delenv(name, raising=False)


def run(capsys, *argv) -> tuple[int, dict]:
    code = main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_init_import_and_validate(tmp_path, monkeypatch, capsys, ar_archive, plaud_env):
    setup(tmp_path, monkeypatch, ar_archive, plaud_env)
    assert run(capsys, "init")[0] == 0
    code, dry = run(capsys, "import-audio-router", str(ar_archive.root), "--dry-run")
    assert code == 0 and dry["unplaced"] == []
    assert sum(dry["by_disposition"].values()) == dry["catalog_rows"] == len(ar_archive.rows)
    code, done = run(capsys, "import-audio-router", str(ar_archive.root))
    assert code == 0 and done["imported"]["created"] == 4 and done["imported"]["failed"] == []
    assert "A PRIVATE TITLE" not in json.dumps(done)
    assert run(capsys, "validate", str(tmp_path / "archive"), "--deep") == (0, {"problems": []})
    code, again = run(capsys, "import-audio-router", str(ar_archive.root))
    assert code == 0 and again["imported"]["created"] == 0 and again["imported"]["sources_added"] == 0
    code, reindexed = run(capsys, "reindex")
    assert code == 0 and reindexed["recordings"] == 4 and reindexed["plaud"]["written"] == 0


def test_back_up_and_prove_the_restore(tmp_path, monkeypatch, capsys, ar_archive, plaud_env):
    exe = restic_or_skip()
    setup(tmp_path, monkeypatch, ar_archive, plaud_env, restic=exe)
    assert run(capsys, "init")[0] == 0
    assert run(capsys, "backup", "init")[0] == 0
    assert run(capsys, "backup", "run", "--tag", "pre-import")[1]["ok"] is True
    assert run(capsys, "import-audio-router", str(ar_archive.root))[0] == 0
    assert run(capsys, "backup", "run", "--tag", "post-import")[1]["ok"] is True
    code, restored = run(capsys, "backup", "restore-test")
    assert code == 0 and restored["detail"] == "restored and validated 4 recordings"
    state = State.open(tmp_path / "state")
    assert state.last_run("restore-test", ok=True) is not None
    assert not any((tmp_path / "scratch").iterdir())
```

Add to `packages/core/tests/test_runbook.py`:
```python
def test_every_recordings_command_the_runbook_names_exists():
    import argparse

    from recordings.cli import build_parser
    sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    used = set(re.findall(r"\brecordings ([a-z][a-z-]+)", "\n".join(_code_lines())))
    assert {"init", "doctor", "backup", "import-audio-router", "validate", "alert-test"} <= used
    assert used <= set(sub.choices), sorted(used - set(sub.choices))
```

- [ ] **Step 2: Run them**

Run: `make restic && uv run pytest packages/core/tests/test_flow_2a.py packages/core/tests/test_runbook.py -v`
Expected: all pass. Neither test is skipped, because `make restic` installed the pinned restic.
If the runbook test fails, a command was renamed in a fix round: amend the runbook, the one place
it is documented.

- [ ] **Step 3: Run restic in CI**

In `.github/workflows/ci.yml`'s `python` job, **add** these steps before `uv run --frozen pytest`:
```yaml
      # The pinned restic (scripts/fetch_restic.py checks its SHA-256), so the backup and
      # restore-test tests run in CI instead of skipping.
      - run: uv run --frozen python scripts/fetch_restic.py
      - run: echo "$PWD/.cache/bin" >> "$GITHUB_PATH"
```
and **change** `uv run --frozen recordings validate demo/archive` to
`uv run --frozen recordings validate demo/archive --deep`.

- [ ] **Step 4: Bring the project docs up to date**

In `CLAUDE.md`, **replace** the `## Archive rules (spec §6)` section with:
```markdown
## Archive rules (spec §6)

- **Every write goes through `recordings.writer.Writer`.** Its `mutate` is the only thing that
  changes a `recording.json`: it bumps `rev` and refuses a file edited outside the app until
  `recordings reindex` accepts the edit. Media, `source/` and `renditions/` are published
  write-once. Never write archive files any other way, in code or in tests.
- **The sentinel:** writers, backups and the mirror refuse an archive without `archive.json`. Tests
  make archives with `recordings.init.init_archive` (or the `writer` fixture).
- **Privacy:** a recording is private if a tag is `private` or under `private/`, in any
  capitalisation (`recordings.models.is_private_tag`). Don't read a private recording's
  transcript or notes. No report, alert or log carries a title.
- **Fixtures are synthetic:** `packages/core/tests/conftest.py` builds Plaud envelopes and an
  `audio-router` archive by hand. Never copy real data in.
- **The archive's own docs** come from `packages/core/src/recordings/format/`. Edit them there.
- **The first real run** is `docs/runbooks/first-run.md`.
```

In `README.md`, **replace** the line `Stage 1 (this release) is the read-only Library over a demo archive.`
with:
```markdown
Stage 1 built the read-only Library over a demo archive. Stage 2a moves the real archive in: a
locked write path, `recordings import-audio-router`, restic backups with a monthly restore test,
the NAS mirror, alerts, and Status. `docs/runbooks/first-run.md` is the first real run.
```

- [ ] **Step 5: Run everything and scan for secrets**

Run: `make test && make e2e && make deploy-smoke && gitleaks dir . --redact --no-banner && gitleaks git . --redact --no-banner`
Expected: every test passes, the compose smoke test passes, and both gitleaks scans report
`no leaks found`.

- [ ] **Step 6: Commit**

```bash
git add packages/core/tests/test_flow_2a.py packages/core/tests/test_runbook.py .github/workflows/ci.yml CLAUDE.md README.md docs/runbooks/first-run.md
git commit -m "test: stage 2a end to end (init, import, validate, backup, restore test); restic in CI

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Stage 2a is done when

- **The automated checks pass.**
  - `make test`, `make e2e` and `make deploy-smoke` pass, and gitleaks is clean.
  - CI runs the restic tests and the compose smoke test, not skipping either.
- **The first real run goes through,** steps 1–4 of `docs/runbooks/first-run.md`. Then, as §20
  requires:
  - the real archive is on the homelab server and readable in the Library
  - a restore passes `recordings validate --deep`
  - a stopped backup service raises an alert, through the dead-man's switch
- **Not in 2a:**
  - any call to Plaud: the client, the token, the sync, the compare (2b)
  - the review queue for held Plaud removals, and moving `acknowledged_up_to` (3a)
  - the Library reading from `index.db`, and search (3a)
  - writing from the UI (3a)
  - people files and speaker naming (3b)
  - processing (4)

## As built

When the last task's council is done, the controller adds the "As built" record here, in stage
1's shape:
- a table of where the committed code differs from each task's text, with commits
- the list of what is carried to 2b and 3a

## Self-review (2026-10-08)

**Spec coverage.** Each §20 2a item, and the sections behind it, maps to a task:

| Spec | Task |
|---|---|
| §6.4 write contract: `mutate`, `flock`, `rev`, content-hash stale check, merge bases, fsync, fixed lock order | 2 (fsync), 5 |
| §6.4 the duplicate merge keeps the second copy's outputs, decisions and notes; `_merge_source` onto `mutate` | 5 |
| §7.6 the `speakers` shape and `unknown`; §9.1.1 Plaud segment fields; optional keys, schemas and demo docs regenerated | 3 |
| §6.8 `state.db`; derived `index.db`; `recordings reindex`; refusing to empty | 2, 4, 5 |
| §6.7 the sentinel and `recordings init`; every writer and the mirror refuse without it | 2, 5, 11, 12 |
| §9.1.1 the normaliser (versioned, the drop list, X-Amz query only, ordering, ID form, per-part hashes, the property test) | 6 |
| §9.1.1 reconcile (pure, idempotent, every snapshot in fetch order, removals held, the title, auto-private, the timing check) | 7 |
| §9.3 the import and its mapping table, Plaud ID before content hash, every catalog row accounted for | 8, 9 |
| §20 Compose: the state volume, long-syntax binds with `create_host_path: false`, log rotation, SHA tags, `hostname`, the writer ID from config | 2, 14 |
| §5 rename `deploy.example.env`; `test_deploy_template.py` | 14 |
| §15.1 restic, transport as config, the restore test, the mirror, the NAS free-space check, what is backed up | 11, 12 |
| §12.5 the disk guard (marker and free space); Status's backup and disk panels | 9, 15 |
| §14 ntfy and the dead-man's switch | 13 |
| §15 cross-site writes | 10 |
| §5 `doctor` (presence only) | 16 |
| §19 steps 1–4 | 1, 17 |
| §16 lost-update test with two processes; synthetic fixtures; the demo guard test | 5, 6–9, 10 |

**Placeholder scan.** No "TBD", "TODO" or "similar to Task N". Every code step shows its code.
The runbook's shell variables (`NAS_HOST` and the rest) are values Dan sets on his own machines,
not gaps in the plan.

**Type consistency.** The names that cross tasks were checked against their definitions:
- `Writer`, `Incoming`, `Added`, `ReconcileReport` and `ReindexReport`
- `Snapshot`, `Reconciled` and `Held`
- `Plan`, `Item` and `RunReport`
- `BackupConfig`, `MirrorConfig`, `AlertConfig` and `Context`
- `StatusSettings`
- `State.last_run`, `State.read_only` and `Index.find_by_source`

**Review Focus.** Each line has its test in the owning task: 1 in Task 5, 2 in Tasks 5 and 9, 3 in
Tasks 7 and 9, 4 in Tasks 6, 7, 8 and 13, and 5 in Task 11.
