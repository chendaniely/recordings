# recordings stage 2a (the archive moves in, protected): implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `audio-router`'s archive into a protected `recordings` archive on the homelab server. That needs:
- a locked write path
- the new `speakers` format and Plaud's segment fields
- `state.db`, a derived `index.db` and the archive sentinel
- the Plaud normaliser and reconcile, as pure functions
- a re-runnable import
- restic backups with a scheduled restore test, and the NAS mirror
- ntfy.sh alerts and a Healthchecks.io dead-man's switch
- the Status page's backup and disk panels

Nothing in it calls Plaud.

**Architecture:**
- **Every write goes through one `Writer` per process.** It refuses to start without the archive's sentinel and this machine's `state.db`. It takes `flock` locks in a fixed order, and it changes `recording.json` only through `mutate(recording_id, op)`. `mutate` bumps `rev`, checks the file's content hash against the merge base kept in `state.db`, and fsyncs.
- **`index.db` is derived.** `recordings reindex` rebuilds it.
- **The Plaud normaliser and reconcile are pure functions** over the raw snapshots in `source/`. The import feeds them `audio-router`'s snapshots now, and stage 2b's sync will feed them Plaud's.
- **Operations run in a `backup` container,** through `recordings ops --loop`: the backup, mirror, check, restore test and disk check. Each run is recorded in `state.db`. Failures are pushed to ntfy, and each good backup pings a dead-man's switch. The UI's Status page reads those records.
  - **A run holds the `ops` lock** (`Locks(…).hold("ops")`, separate from the Writer's lock order). The CLI waits up to 600 s for it, and the real import holds it too. The loop doesn't wait: a busy lock means "skipped: busy", which is not a failure and sends no alert.

**Tech Stack:**
- **Python:** Python 3.14's standard library (`sqlite3`, `fcntl`, `wave`, `csv`, `urllib.request`), pydantic 2.13.5. **No new Python dependencies.**
- **Tools:** restic 0.19.1 (pinned by SHA-256), rsync, OpenSSH, and ffprobe (from ffmpeg), in the image.
- **On the NAS:** rest-server 0.14.0 with `--append-only`, and a static restic 0.19.1 for retention. The runbook installs both, pinned by SHA-256; the build doesn't touch the NAS.
- **Delivery:** Docker Compose, started at boot by its own systemd unit.
- **UI:** shinyreact 0.1.0 and React 19, for the Status page.
- **Testing:** pytest 9.1.1, Vitest 3.2.7, pytest-playwright 0.9.0.

**Spec:** `docs/superpowers/specs/2026-10-08-recordings-design.md`. This plan builds §20 stage 2a only, and argues from §3, §5, §6 (especially §6.4, §6.7 and §6.8), §7.4, §7.6, §9.1, §9.1.1, §9.3, §12.5, §14, §15, §15.1, §16, §19 and §22. Read the spec alongside this plan.

## Decisions made while planning (review these)

1. **One `Writer` owns every write, and `Archive` becomes read-only** (stage-1 "As built": "Writers get their own `Archive` instance, never the UI's").
   - `Writer` (`recordings/writer.py`) checks the sentinel, `state.db` and the writer ID when it is made, and holds its own `Archive`.
   - Stage 1's `Archive.add_recording`, `write_rendition` and `_merge_source` move onto it, as `Writer.add`, `Writer.write_rendition` and the duplicate merge inside `Writer.add`.
   - `Archive.find_by_sha256`, which scanned every folder, is replaced by `Index.find_by_sha256`.
   - **Task 5 is built in three parts** (2026-10-09), each with its own review and council:
     - **5a:** the locks.
     - **5b:** the state revisions, the `Writer`, `test_writer.py` and the conftest. `Archive`'s write methods stay for now, because `writer.py` imports only names that already exist.
     - **5c:** `Archive` becomes read-only, and its callers move (5b already retired the seven `test_archive.py` tests that `test_writer.py` replaces; 5c moves the rest): `test_archive.py`, `test_selfdoc.py`, the UI's conftest, `test_views.py`, `test_app.py`, `settings.py`, the docs written at startup in `app.py`, `reindex`, the demo build, AGENTS/FORMAT, and `cmd_docs`, which goes through `Writer.open`.
     - Other tasks may still say "Task 5" where they consume its names.
2. **Every raw snapshot is one entry in `sources`.** `SourceRef` gains two optional keys: `fetched_at` (when the source returned that payload) and `sha256` (of the raw file's bytes).
   - Reconcile orders snapshots by `fetched_at`, then by their position in `sources`, never by file name (§9.1.1).
   - A re-run skips a snapshot whose kind, reference and `sha256` are already there.
   - Publishing a raw snapshot reuses a `source/` file whose bytes are identical. A process killed between publishing and recording a snapshot therefore leaves no duplicate when it runs again (Review Focus 2).
   - Each `audio-router` recording also gets one `audio_router` reference: its catalog `uri`, such as `plaud/<file_id>`. That "keeps `audio-router`'s source and ID" (§9.3), and is how a re-run recognises Recorder and Pocket recordings.
3. **The writer's identity** (§3, §6.8):
   - `[archive] writer_id` in `config.toml` names it. It replaces `writer_host`. A config that still says `writer_host` fails loudly, with the fix in the message, because it names a host and the spec says identity never comes from a host name.
   - `recordings init` records the writer ID in `state.db`, beside the archive's UUID.
   - A process may write only when the config's writer ID, `state.db`'s writer ID and archive UUID, and the sentinel's UUID all agree.
   - The Mac's mirror has no `state/`, so it can never write.
   - **A damaged `state.db`** is reported as a `StateError` that says to restore it from the backup (runbook: "Restoring for real"), never as a traceback. A missing one is never created by accident: only `init` (or `init --adopt`) creates it, exclusively.
   - **`recordings init --adopt`** (ruled 2026-10-09) is the last resort when `state.db` is lost and its backup copy is unusable. It recreates `state.db` from the sentinel's UUID and the config's writer ID. Each file's merge base is adopted on its next write (§6.8 expects a rebuild).
4. **A stale edit is refused, not merged,** until stage 3 (§6.4: "Until stage 3, a stale or invalid edit goes to Needs attention instead").
   - **Merge bases are kept now:** the last eight revisions of each file, in `state.db`.
   - **The revision record has two phases** (pending, then committed). A crash between replacing the file and committing its revision is then recognised for what it is on the next write. It is never mistaken for an outside edit (Review Focus 1).
5. **`recordings reindex` is how an outside edit is accepted,** which `AGENTS.md` already tells agents to run.
   - It records each valid hand-edited `recording.json` as the new merge base, and flags an invalid one in `state.db`.
   - It then runs reconcile on every recording with Plaud snapshots, and rebuilds `index.db`.
   - It refuses to empty a non-empty index (§6.7).
6. **The Library keeps reading the files in 2a. `index.db` serves the writers and the import.**
   - The spec moves `[index]` here because stage 1 globs every folder on each load. The real archive is under a hundred catalog rows today, which the glob handles. **Ruled 2026-10-09: the Library stays on files** (§20, 2a).
   - The runbook's step 4 checks it: the Library's first load takes under 2 s. If it doesn't, the Library moves onto the index before 2b.
   - The index's readers that matter, search and tag counts, arrive with stage 3a.
   - The Pages demo runs on Pyodide, where `sqlite3` is a separately loaded module, and nothing has checked that it loads there. So the UI's reading path must not import `sqlite3` or `fcntl` at module level.
   - Carried to 3a: the Library reads from `index.db`.
7. **The format change stays inside `recordings-archive@1`** (§22, decision 12).
   - **Every new key is optional:** `rev`, `title_by`, `plaud`, `speakers.source`, `speakers.labels`, `speakers.spans`, `SourceRef.fetched_at` and `SourceRef.sha256`, `MediaInfo.sample_rate` and `MediaInfo.channels`, and the segment fields `speaker_name` and `embedding_key`.
   - **`title_by`** is `Literal["you", "plaud", "recorder", "pocket"] | None`: `you`, or the source kind that supplied the title (§6.3). Stage 5 adds `upload` and `url`.
   - **Old files read unchanged.** An empty `speakers` still serialises as `{}`, through pydantic's `exclude_if`, which was checked in 2.13.5 on 2026-10-08.
   - **The browser never serialises a model.** The Pages demo runs pydantic 2.10.5 (Pyodide 0.27.7), which has no `exclude_if`: it ignores it with a DeprecationWarning, so an empty `speakers` would dump there as `{"labels": {}, "spans": []}`. That is harmless while Pages only reads, and it must stay that way.
   - **`rev`:** a file without it counts as revision 0, and the writer starts every new recording at 1.
8. **Plaud's outputs** (§6.5, §9.1.1):
   - **The transcript** comes from the `source_list` block whose `data_type` is `transaction`.
   - **A notes output** comes from every `note_list` tab, Dan's own highlights and memos included, and from the `outline` block. Each gets `note_type` `plaud-<tab>`, ASCII-folded.
   - **`transaction_polish` stays in `source/` only.**
   - **`version` is `plaud@1`,** the normaliser's version.
   - **Reconcile's identity for a part includes `version`** (ruled 2026-10-09, per §6.5). A new normaliser version therefore re-renders every Plaud output, not only the parts whose hash changed.
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
   - `deferred-to-2b` (Dan, 2026-10-09): a Plaud row with audio but no snapshot and no time. It is reported with its Plaud ID and counted, never `unplaced`, so a real run doesn't refuse. Stage 2b's sync matches it by that ID; if Plaud no longer has it, 2b imports it from `audio-router`, timed by the file's mtime. It is on the plan tail's "Carried to stage 2b" list.
   - `consent-excluded` (ruled 2026-10-09: "Not unless I allow it"): a Plaud ID on Dan's consent list (`[plaud] consent_list`, §9.1). It is skipped, and the reports carry only its count. The list is read in the dry run and the run, failing closed: an unset, missing, unreadable or empty list stops both.
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
    | `speakers` from engine `human` (names Dan typed) | **kept verbatim in `source/`** |
    | `summary` | `notes`, with `note_type` `audio-router-summary` |
    | any output whose engine is `plaud` | **skipped:** reconcile rebuilds these |
    | `fingerprint` and `embeddings` | **skipped:** voice data never enters the archive (§7.6) |

    - **Turns and segments with no time** (in `merged` and `words`) are kept, as reconcile keeps them, never dropped with their text.
    - **Pocket's own transcript and summaries** become outputs with engine `pocket`.
    - **Google Recorder's untimed prose transcript** stays in `source/` (answered question 4).
14. **The `media/private/` tier is matched by SHA-256** (ruled 2026-10-09). A 64-hex file ID there is the audio's SHA-256, and no `audio-router` writer laid that tier out like the others: no `raw/<file_id>` and no `derived/<file_id>`.
    - The import finds the media file whose SHA-256 equals the ID.
    - A `.txt` with the same stem (a Recorder export's prose transcript) comes too, as a raw source.
    - The row's catalog tags are carried (decision 23).
    - These recordings are tagged `private` with `by: you`, because putting them in that tier was Dan's own decision.
15. **Mapping `time_source`** (§6.2's values). `audio-router`'s catalog gives its own values, keyed by source:

    | Source | `audio-router` value | `recordings` value |
    |---|---|---|
    | Plaud | `start_at`, `created_at` | `plaud` |
    | Recorder | `filename` | `metadata` |
    | Recorder | `file mtime (inferred)` | `mtime` |
    | Pocket | `recording_at` | `metadata` |
    | Pocket | `created_at` | `ingest` |
    | private | `filename` | `metadata` |

    Any other value leaves the row unplaced, except a Plaud row with no snapshot and no time, which is `deferred-to-2b` (decision 12).
16. **The alert services** (Dan, 2026-10-09). Both are off-box, so neither goes down with the homelab server or the NAS.
    - **The dead-man's switch is Healthchecks.io,** with a 1-hour period and a grace of 3 hours. The grace is long because the loop runs the mirror, the check and the restore test between backups, and the first full mirror or a long restore test must not look like an outage.
      - The app pings the base URL after each good backup, `/start` when a backup begins and `/fail` when it fails. `alert-test` uses `/log`, which leaves the check's status alone.
      - **The ping URL is a secret,** as the ntfy topic is (§5, §14). A Healthchecks URL carries its own token: anyone holding it can keep an outage quiet.
    - **Pushes go to ntfy.sh,** on an unguessable topic, checked against `[-_A-Za-z0-9]{1,64}` rather than URL-quoted. An optional `RECORDINGS_NTFY_TOKEN_FILE` covers a self-hosted server with access tokens.
    - **Both URLs must be `https`.**
    - **Alert bodies come from fixed templates** (Global Constraints), never from exception text.
17. **What a backup holds** (§15.1) is an explicit list: the archive, `config.toml`, `docker/deploy.env` and a SQLite backup copy of `state.db`.
    - **`voice.db` and the Plaud token store are left out by construction,** because nothing names them.
    - **Snapshots are tagged `recordings`.** `--keep-tag pre-import` and `--keep-tag post-import` keep the milestone snapshots that §19 asks for. With rest-server, the NAS's retention task applies them (decision 22); with the SFTP fallback, the server's own `forget` does.
    - **restic's `--host` is the writer ID,** so retention never depends on a container's host name.
18. **Cross-site protection applies to every request whose method isn't safe, across the whole app.**
    - 2a adds no write route to the UI: the import is a CLI step that Dan runs.
    - The guard is in place now, so every later route has it without opting in.
    - A request with no `Origin` and no `Sec-Fetch-Site` comes from no browser, so the guard lets it through.
19. **restic 0.19.1 is pinned by SHA-256.** The checksums come from the release's `SHA256SUMS`, checked 2026-10-08. The darwin_arm64 pin was corrected on 2026-10-09 to `7be0a144ccc377880f294204aa271d76e4b79554b42a751151d425ce6ebac143`; the draft had carried the darwin_amd64 sum.
    - `scripts/fetch_restic.py` installs it into `.cache/bin/` for CI and the Mac, and the Dockerfile installs it into the image. Nothing is installed system-wide.
    - The runbook's NAS spike downloads the same pinned restic itself, with `curl` and `sha256sum -c`, so it never waits for Task 11's `make restic`.
20. **Media is measured by `wave` for WAV files, and by decoding for everything else.**
    - For mp3, m4a and the rest, the duration comes from decoding the file (ffmpeg), not from the container's header or the bitrate. The method used is recorded with the measurement (Task 8).
    - The synthetic fixtures are WAV, so the tests need no ffmpeg.
    - The image installs ffmpeg for ffprobe and the decode.
    - `recordings doctor` reports a missing ffprobe or ffmpeg, and a real import refuses when it can't measure a non-WAV item (Task 9).
21. **Writing the archive's docs at startup** (§6.7; stage-1 "As built") happens when a `Writer` opens, and when the web app starts against an archive it may write. Both happen only once the sentinel is checked.
22. **The NAS transport is rest-server with `--append-only`** (Dan, 2026-10-09: "my synology is on ext4"). Ext4 has no Btrfs snapshots, so an SFTP repository would have nothing protecting it from a compromised server. With append-only, the server can add snapshots but never delete or change one.
    - **Where it runs:** on the NAS, in Container Manager, or as rest-server's static binary started at boot by DSM's Task Scheduler (no Container Manager needed). The runbook's spike picks.
    - **The password's path:** TLS with a self-signed certificate (`--tls`), which restic trusts through a new optional `[backup] cacert` setting (`--cacert`). Or plain HTTP when the server reaches the NAS over the tailnet, whose WireGuard tunnel carries it. The spike picks.
    - **Retention runs on the NAS:** a weekly DSM scheduled task runs a static restic directly on the repository's folder, with its own root-only copy of the password:
      `forget --keep-hourly 24 --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --keep-tag pre-import --keep-tag post-import --prune`.
      On the server, `[backup] prune = false`, and `doctor` warns that retention must run on the NAS.
    - **`restic unlock` still works** through append-only, so the server clears stale locks before `check`.
    - **The mirror** uses one SSH key for a separate, non-admin NAS account. Its `authorized_keys` line carries `restrict,from="<server address>"`. DSM's rsync service is on, and the account's share permissions reach only the mirror share, which only Dan reads. There is no forced command, so sftp's `df` still works. A compromised server can then wipe only the mirror, which is derived; `--max-delete=50` bounds an accident.
    - **The NAS's free space** comes from sftp's `df` on the mirror account (`statvfs@openssh.com`, if DSM's SFTP server has it; the spike checks). Otherwise it is "unknown", and Status and `doctor` say so.
    - **SFTP is only a fallback,** for when rest-server can't run at all. It accepts the risk. Hyper Backup of the backup share to an off-site target is then the mitigation.
    - The spec's "Later: Synology's own snapshots" no longer applies on ext4.
23. **`audio-router`'s tags all come over** (Dan, 2026-10-09).
    - Every label in the catalog's `tags` column becomes a tag.
    - The privacy labels become private tags. Task 8 recognises every protected marker in `audio-router`'s `sources/local_.py` at 64612df, and trims the tags to one per meaning (Dan, 2026-10-09: "counselling and counseling are the same", and "therapy and counseling are also all the same"): `private`, `personal` and `voiceprints` → `private`; `therapy`, `counselling` and `counseling` → `private/therapy`; `journal`, `medical` and `students` → `private/<label>`.
    - A row is `new-private` when its source is `private`, its `access` is non-empty, or any of its tags is a privacy label.
    - **This list is a floor, not the definition of privacy** (Dan, 2026-10-09). `private` is a gate, not a topic: it means only models on Dan's own hardware may process the recording, and external agents such as Claude Code may not read it. Other recordings are private too (some lectures, for example) without carrying any of these labels. They arrive untagged, which external agents already treat as private (§7.4), and Dan adds `private` when he tags them in stage 3.
    - The private tag is in `Incoming.tags` when the recording is created, so it is never published untagged (Global Constraints: privacy comes first).

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
  | Healthchecks.io | https://healthchecks.io/docs/ (`/start`, `/fail`, `/log`; period and grace) |
  | ntfy | https://docs.ntfy.sh/publish/ |
  | Compose | https://docs.docker.com/reference/compose-file/services/ (long-syntax `volumes`, `create_host_path`, `logging`, `hostname`, `secrets`) |
  | systemd | https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html (`RequiresMountsFor=`) |
  | SQLite in Python | https://docs.python.org/3/library/sqlite3.html (`Connection.backup`) |
  | `flock` | https://docs.python.org/3/library/fcntl.html |
  | ffprobe | https://ffmpeg.org/ffprobe.html |

  Facts checked 2026-10-08 that this plan relies on:
  - A long-syntax bind's `create_host_path` **defaults to `true`**, so it must be set to `false` explicitly.
  - `fcntl.flock` with `LOCK_NB` raises `OSError` with `errno` set to `EAGAIN` or `EACCES`.
  - rest-server's `--append-only` refuses deletes with 403, so `forget --prune` cannot run through it.
  - restic's exit codes: 10 means no repository, 11 a lock failure, 12 a wrong password.

  Facts checked 2026-10-09:
  - rest-server 0.14.0 (the current release, 2025-05-31) takes `--append-only`, `--private-repos`, and `--tls` with `--tls-cert` and `--tls-key`. A client trusts a self-signed certificate with restic's `--cacert`. Its `.htpasswd` takes bcrypt lines (`htpasswd -B`). Its Docker image ships `htpasswd` and a `create_user` script, which prompts for the password when given only the user.
  - rest-server 0.14.0's `SHA256SUMS`: `linux_amd64.tar.gz` `4c9c95bc079a0334e81fad379b19dc5c3353c71c2c88d652cafce2081c2b1c66`, `linux_arm64.tar.gz` `cef139cbe8b27b16bda731d17f093b0aa466b8c60b136c12d78b6f2bff3daf22`. Its image `restic/rest-server:0.14.0` has the index digest `sha256:d2aff06f47eb38637dff580c3e6bce4af98f386c396a25d32eb6727ec96214a5`.
  - restic 0.19.1's `SHA256SUMS`: `linux_amd64.bz2` `f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c`, `linux_arm64.bz2` `a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465`, `darwin_arm64.bz2` `7be0a144ccc377880f294204aa271d76e4b79554b42a751151d425ce6ebac143`.
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

  Taking an earlier lock while holding a later one is an error, not a wait. The `ops` lock sits outside this order, in its own `Locks` instance.
- **Private:** a recording is private if any tag is `private` or under `private/`, in any capitalisation (`recordings.models.is_private_tag`).
  - **Privacy comes first.** A recording that will be private carries the tag when it is created (`Incoming.tags`). The duplicate merge and reconcile apply it with their first `mutate`, before they publish any source or rendition (Tasks 5b, 7 and 9).
  - The auto-private patterns are matched against every snapshot's title, with Plaud's `MM-DD ` prefix stripped first, and against both Plaud IDs (Task 7).
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
  - **Failure text comes from fixed templates, never `str(exc)`.** A job's stored `detail` is `f"{type(exc).__name__}; exit {code}"` where there is an exit code. An alert body is `f"{job} failed ({detail}). See Status."`. An import row's `failed[].reason` is `type(exc).__name__` plus a fixed phrase.
  - The full exception goes only to the container log, at debug level, and never into `state.db` or an alert.
  - The pydantic models set `hide_input_in_errors=True`, so a validation error never quotes a title or transcript.
- **No private details in anything committed.** Say "the homelab server" and "the NAS". No machine names, IP addresses, employer or course names, or real people. The repo is public.
- **Times:**
  - File stamps are ISO 8601 basic UTC, such as `20261008T143512Z`.
  - UI labels use 24-hour time and full dates, such as `2026-10-08 14:05` (§12).
- **Demo mode** runs on a fresh copy of `demo/archive/` with a temporary state folder. It reads no real config or secrets and makes no network calls (§17).
- **Commits** use Conventional Commits and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A crash between replacing `recording.json` and committing its revision** (a power cut mid-`mutate`) must neither lose the write nor make the next write report a false "edited outside the app". The test is in Task 5b.
2. **An import killed halfway must finish cleanly when it runs again,** even with a `.tmp/` assembly left behind and some recordings already done.
   - **The kill lands inside real code,** never before it. The kill points are parametrised:
     - in `copy_file_synced`, after half the bytes
     - in `State.commit`, after the `os.replace`
     - in `Writer.mutate`, while the op is `MergeIncoming`
     - in `Writer.reconcile_plaud`
   - **The re-run is compared with a clean run.** It uses a fresh `Writer` and the same `now`. The archive's file list must equal a clean run's, with no duplicate recordings, `sources` entries or outputs, and no `.tmp/` left over.
   - The tests are in Tasks 5b (a crash in `_create`, and `_publish_raw` reusing identical bytes) and 9.
3. **An `audio-router` snapshot that is degraded or not JSON at all** (a data-link error, or an empty transcript) is kept in `source/`. It never feeds reconcile, and it is reported. The tests are in Tasks 7 and 9.
4. **A Plaud tab name or title in another script, or with punctuation** (`会議メモ / Q&A`), gives a safe file name and note type, and never reaches operational output.
   - **The fixtures carry it:** Tasks 8 and 9 give a catalog row and its snapshot `name="会議メモ / Q&A"`. Their tests assert that it appears nowhere in the dry run or the import report, both dumped with `ensure_ascii=False` so the check sees the characters themselves.
   - **Failure text comes from fixed templates.** Task 13's test forces an exception whose message carries that title, and asserts that the alert body and the stored `detail` don't.
   - The tests are in Tasks 7, 8, 9 and 13.
5. **A restored copy with a truncated media file fails the restore test,** and raises an alert. It must not pass just because the files exist. The test is in Task 11.

## Execution checkpoints (Dan, 2026-10-08; sharpened 2026-10-09)

The execution method is **subagent-driven development**: a fresh implementer for each task, then a task review.

**The ledger** is `progress.md` in the subagent-driven-development workspace. It records, for each task: its commits, the suite's passed count, the council's merged findings, every ruling, and every fix round.

**The mechanical gate.** Before the council, the controller runs the full suite (`uv run pytest`) and writes the passed count in the ledger.
- The count never drops below the previous task's. A lower count stops the run until it is fixed.
- A test that a task deliberately deletes or moves is named in the ledger, with the test that replaces it.
- For reference, the draft measured 229 passed before Task 2. Its later counts grow with this round's new tests, so the rule is the comparison, not a fixed number.

**Between every two tasks, the forward and reverse review is a council:**

- **When:** after a task passes its task review and the gate, and before the next task is dispatched.
- **Who:** three reviewers, run in parallel, each read-only. **Three lenses after every task, without exception** (Dan's rule), including the small ones such as Tasks 1, 4, 12 and 17. A docs-only task may run its council on a cheaper model.
  1. **Reverse.** It compares what was built, including any fix rounds and rulings, with the spec and every earlier task. Does it still match, and has anything the spec needs become harder?
  2. **Forward.** It reads **the next task in full**, plus **the Files and Interfaces blocks of every later task**, against the code as it actually stands: names, signatures, file paths, test expectations and the files each commit lists. It proposes exact OLD/NEW edits to the plan.
  3. **A risk lens chosen for the task just finished.** Each task names its lens under **Checkpoint lens**:
     - **data integrity and write safety:** the locked write path, the sentinel, the import and reconcile
     - **security and privacy:** anything touching secrets, the external view, cross-site protection or alerts
     - **operations:** Docker, backups, the mirror and the disk guard
- **When the reverse review finds the built code wrong,** not just the plan, a fix round on that task runs first: a fresh implementer, its own task review, and the gate. Only then is the next task dispatched. The council's edits to later tasks are re-checked against the fixed code.
- **Then:** the controller:
  1. merges the three reports into one list
  2. rules on any conflict between them, recording each ruling in the ledger
  3. applies the plan edits
  4. commits them as a `docs(plan): …` commit before dispatching the next task

So the plan in git always matches what is being built.

## Answered (2026-10-09)

The questions the draft asked Dan, with their answers. The numbers stay, because code comments cite them as "stage-2a plan, answered question N".

1. **Dead-man's switch:** Healthchecks.io, with a 1-hour period and a 3-hour grace, pinged at `/start`, `/fail` and the base URL; `alert-test` uses `/log` (decision 16).
2. **NAS transport and retention:** rest-server `--append-only`, with retention as a weekly task on the NAS and `[backup] prune = false`; SFTP only as a fallback that accepts the risk. Free space comes from sftp's `df` on the mirror account, so no shell account is needed (decision 22).
3. **The `media/private/` tier:** its 64-hex ID is the audio's SHA-256, so the import matches by hash, keeps the same-stem `.txt`, and carries the tags (decision 14).
4. **Google Recorder's untimed transcript:** it stays in `source/` only. `audio-router`'s `recorder.py` confirms it is prose with no reliable times, and stage 4's Whisper replaces it.
5. **Pocket's segment times:** still unsettled, because no `audio-router` code reads them. The default stands: seconds, unless the last segment ends past twice the recording's length.
6. **ntfy:** ntfy.sh with an unguessable topic. An optional `RECORDINGS_NTFY_TOKEN_FILE` covers a self-hosted server with access tokens (decision 16).
7. **The token and ID-form spikes:** they call Plaud, so they run just before 2b (spec §19 step 5, and runbook 1c).
8. **The council's own questions:** the transport on ext4 (decision 22), the alert services (decision 16), `audio-router`'s tags (decision 23), the orphan Plaud rows and the consent list (decision 12), a lost `state.db` (decision 3), `version` in reconcile's identity (decision 8), and the Library (decision 6).

## File map (stage 2a)

```
recordings/
  config.example.toml                       [archive] writer_id, [state], [index], [plaud], [nas], [backup] (+ cacert), [mirror], [disk], [alerts]
  Makefile                                  restic, deploy (commit-tagged, refuses -dirty), rollback, deploy-smoke
  docs/runbooks/first-run.md                §19 steps 1–4, with exact commands, and Restoring for real (Task 1)
  scripts/fetch_restic.py                   pinned restic into .cache/bin/ (CI and the Mac)
  scripts/compose_smoke.py                  the real compose file, once, against temporary folders
  demo/build.py                             builds through the Writer; a synthetic Plaud envelope
  demo/archive/                             rebuilt: archive.json, rev, Plaud outputs from reconcile
  docker/Dockerfile                         + ffmpeg, rsync, openssh-client, restic 0.19.1
  docker/compose.yml                        web + backup, long-syntax binds, state + index volumes, secrets, logging, hardening
  docker/recordings.service                 systemd unit: waits for the tailnet address and the data disk, then compose up -d (Task 14)
  docker/up-deployed.sh                     runs compose on the tag the web container already runs (the unit's ExecStart)
  conftest.py                               repo root: strips RECORDINGS_* and RESTIC_* from every test, keeping the test switches (Task 14)
  docker/deploy.example.conf                renamed from deploy.example.env
  .github/workflows/ci.yml                  restic for tests (Task 11), compose smoke job
  .github/dependabot.yml                    + the docker ecosystem, for the pinned base-image digests (Task 14)
  packages/core/src/recordings/
    files.py          durable writes: temp + fsync + rename + fsync dir; exclusive publish
    sentinel.py       archive.json: read_sentinel, write_sentinel, SentinelError
    state.py          state.db: meta, revisions (merge bases, Task 5b), flags, ops runs, alert state, backup copy
    init.py           init_archive: the explicit `recordings init`, and `init --adopt`
    refs.py           canonical source references (Plaud ID forms)
    index.py          index.db: rebuild (refuses to empty), upsert, lookups by SHA and source
    locks.py          flock locks, fixed order, re-entrant per writer (Task 5a)
    writer.py         Writer, Incoming, Added, ops (AddTags, MergeIncoming, SetPlaudFields), reindex (Task 5b)
    archive.py        read-only: its write methods move to the Writer, and its callers move with them (Task 5c)
    media.py          probe: duration, sample rate, channels (wave, or decoding)
    disk.py           the disk guard: the sentinel marker plus free space
    ssh.py            ssh options shared by restic, rsync and sftp
    backup.py         restic: init, backup, forget/prune, check, restore test
    mirror.py         rsync --delete --delay-updates --max-delete, refusing without the sentinel or into a wrong target
    alerts.py         ntfy and the dead-man's switch
    ops.py            run_job, the schedule, ops --loop, status_summary
    doctor.py         `recordings doctor --role`, moved out of config.py and extended
    plaud/__init__.py
    plaud/normalise.py   the versioned normaliser, part hashes, the diarization fingerprint
    plaud/blocks.py      Plaud's JSON note blocks as Markdown (from audio-router's vendor_blocks.py)
    plaud/reconcile.py   reconcile: snapshots in fetch order → Plaud outputs + Plaud-owned fields; the auto-private matcher
    plaud/consent.py     the consent list, read failing closed (Task 9)
    sources/__init__.py
    sources/audio_router.py   the catalog, the plan (dry run), the run
    sources/ar_outputs.py     audio-router renditions and Pocket's payload → outputs
  packages/core/tests/
    conftest.py  test_files.py  test_init.py  test_index.py  test_locks.py (5a)  test_writer.py (5b)
    test_normalise.py  test_reconcile.py  test_media.py  test_import_plan.py  test_import_run.py
    test_disk.py  test_backup.py  test_mirror.py  test_alerts.py  test_ops.py  test_doctor.py
    test_flow_2a.py  test_runbook.py  test_deploy_template.py
  packages/ui/src/recordings_ui/
    hosts.py          + WriteGuard (cross-site writes)
    status.py         the Status view (imports the state code lazily, so Pages stays safe)
  packages/ui/frontend/src/
    lib/nav.ts  lib/nav.test.ts  lib/when.ts  lib/when.test.ts (the header's time with its offset)
    components/StatusPage.tsx  (TopBar, App, DetailsTab, types.ts change)
  packages/ui/tests/  test_writeguard.py  test_demo_guard.py  test_status.py  test_template_settings.py  (+ e2e status test)
```

## Carried from stage 1 (the "As built" triage)

| Stage-1 item | Where it lands |
|---|---|
| `fsync` the file and its parent folder on every write | Task 2 |
| A duplicate merge must not drop Plaud's outputs | Task 5b (the merge keeps the second copy's outputs, decisions and notes), Task 7 (reconcile) |
| Writers get their own `Archive` instance | Tasks 5b and 5c |
| Compose binds use `create_host_path: false` | Task 14 |
| `doctor` reports secrets even when the config fails, and probes hard-link support | Task 16 |
| `validate` checks the media file exists, and handles a broken `recording.json`'s renditions | Task 3 |
| The demo guard test (§17: no network, no secrets) | Task 10 |
| Write the archive docs at startup (§6.7) | Tasks 5b (`Writer.open`) and 5c (the web app) |
| A Docker `/healthz` smoke test in CI; running the real Compose once as UID 1000 against a temporary archive | Task 14 |
| Harden Compose with `read_only`, `cap_drop` and `no-new-privileges` | Task 14 |
| Clean up the demo temp folders | Task 10 |
| `RawSource.added_at` must be an aware datetime | Task 3 |
| `allowed_hosts` entries with a port or scheme are rejected | Task 10 |
| `base_url` is type-checked in config | Task 2 |
| `find_by_sha256` moves into the index; a lock or changed-since-read check (both listed under stage 3) | Tasks 4 and 5b |
| The header's time with its offset | **Task 15** (ruled 2026-10-09). The real archive spans several time zones, so the recording pane's header shows the time with its offset from the first deploy. |
| `ruff check` in CI | **2b.** The repo has no lint-rule set yet, and a global ruff config on Dan's Mac reports 37 findings that CI's defaults wouldn't. Choosing rules is its own small change. |
| UX: page title, favicon, phone layout, the pane's error state, Details dates | **3a,** with the Library work |
| Measure re-renders with a real hour-long transcript | **3a.** The real archive is in the Library after Task 9's first real run. |

---
### Task 1: The first-run runbook

**Checkpoint lens:** operations.

The runbook comes first, because Dan runs two of its steps himself, in parallel with the build: the 15-minute server checklist and the NAS transport spike. The spike fetches its own pinned restic and rest-server with `curl` and `sha256sum -c`, so it needs nothing the build makes. The commands it names (`recordings init`, `backup …`, `mirror`, `import-audio-router`, `reindex`, `ops`, `alert-test`, `doctor`) are the ones the later tasks build. Task 17 adds a test that every command it names exists.

**Files:**
- Create: `docs/runbooks/first-run.md`
- Test: `packages/core/tests/test_runbook.py`

**Interfaces:**
- Consumes: nothing. It names what later tasks build, so the forward reviews check it against them:
  - `recordings init` and `init --adopt` (Task 2), `reindex` (Task 5c), `import-audio-router` with the `deferred-to-2b` and `consent-excluded` dispositions (Tasks 8 and 9)
  - `backup init|run|restore-test`, the optional `[backup] cacert`, and `mirror` (Tasks 11 and 12)
  - `ops --loop` and `alert-test` (Task 13)
  - `docker/recordings.service` and the `nas_cacert` secret file (Task 14)
  - `doctor --role web|backup` (Task 16)
- Produces: `docs/runbooks/first-run.md`, with the headings `## 1. Spikes`, `### 1a. Server facts`, `### 1b. NAS transport`, `### 1c. Before stage 2b (not needed for 2a)`, `## 2. Deploy 2a against an empty, initialised archive`, `## 3. Bring the source over`, `## 4. Import` and `## Restoring for real`, plus a `## Checklist` table. Later tasks amend it, as the forward review finds.

- [ ] **Step 1: Write the failing test**

`packages/core/tests/test_runbook.py`:
```python
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUNBOOK = REPO / "docs" / "runbooks" / "first-run.md"
# A fenced block whose fences start their own lines. Pairing fence lines (rather than any
# three backticks) keeps a json block from shifting every later block by one.
FENCE = re.compile(r"^[ \t]*```(\w*)[ \t]*\n(.*?)^[ \t]*```[ \t]*$", re.S | re.M)


def _text() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def _code_lines(text: str | None = None) -> list[str]:
    """Every line of the shell blocks (bash, sh or unmarked), in order."""
    return [line for lang, body in FENCE.findall(_text() if text is None else text)
            if lang in ("", "bash", "sh") for line in body.splitlines()]


def _section(heading: str) -> str:
    """The runbook from `heading` to the next heading of the same or a higher level. Fenced
    blocks are skipped while looking, because a shell comment also starts with #."""
    level = len(heading) - len(heading.lstrip("#"))
    inside, out = False, None
    for line in _text().splitlines():
        if re.match(r"\s*```", line):
            inside = not inside
        elif not inside and out is None and line == heading:
            out = []
            continue
        elif not inside and out is not None and re.match(rf"#{{1,{level}}} ", line):
            break
        if out is not None:
            out.append(line)
    assert out is not None, f"no heading {heading!r}"
    return "\n".join(out)


def test_the_runbook_covers_steps_one_to_four_and_a_real_restore():
    text = _text()
    for heading in ("## 1. Spikes", "### 1a. Server facts", "### 1b. NAS transport",
                    "### 1c. Before stage 2b (not needed for 2a)",
                    "## 2. Deploy 2a against an empty, initialised archive",
                    "## 3. Bring the source over", "## 4. Import", "## Restoring for real",
                    "## Checklist"):
        assert heading in text, heading


def test_the_runbook_holds_no_addresses_keys_or_tailnet_names():
    # why: the repo is public. The runbook uses variables and "the homelab server", never a
    # machine's real address or name.
    text = _text()
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text), "an IPv4 address"
    assert ".ts.net" not in text
    assert "BEGIN OPENSSH" not in text and "ssh-ed25519 AAAA" not in text


def test_no_secret_is_ever_typed_on_a_command_line():
    # why: shell history keeps command lines. Secrets go into files through an editor, and
    # tools that want one are given the file or asked at a prompt.
    for line in _code_lines():
        assert not re.search(r"\b(printf|echo)\b[^|]*>\s*\S*secrets/", line), line
        assert "RESTIC_PASSWORD=" not in line, line
        assert not re.search(r"\bcreate_user\s+\S+\s+\S", line), line  # user AND password
        assert not re.search(r"\bhtpasswd\b.*\s-[A-Za-z]*b", line), line  # -b: password as an argument
        assert not re.search(r"rest:https?://[^/\s]*:[^/\s]*@", line), line  # user:pass@ in a URL


def test_the_spikes_need_nothing_the_build_makes():
    # why: Dan runs the spikes while the build is still going, so they can't use `make
    # restic` (Task 11) or the image. Whatever they download is checked against its pin.
    lines = _code_lines(_section("## 1. Spikes"))
    for line in lines:
        assert not re.match(r"\s*(make|rc)\b", line), line
        assert ".cache/bin" not in line, line
        if "restic/rest-server:" in line:
            assert "@sha256:" in line, f"an image not pinned by digest: {line}"
    downloads = [i for i, line in enumerate(lines) if "releases/download/" in line]
    assert len(downloads) >= 3, "restic on the server, rest-server and restic on the NAS"
    for i in downloads:
        assert "sha256sum -c" in lines[i + 1], f"not checked: {lines[i]}"


def test_option_lists_are_arrays_so_zsh_and_bash_split_them_alike():
    # why: zsh doesn't split an unquoted $VAR into words, so `ssh $SSH_OPTS host` hands ssh one
    # long argument. An array expanded as "${NAME[@]}" splits the same way in both shells.
    lines = _code_lines()
    arrays = {m.group(1) for line in lines for m in re.finditer(r"\b([A-Z_]+)=\(", line)}
    assert {"SSH_OPTS", "CACERT", "IMPORT_MOUNTS"} <= arrays
    for line in lines:
        for name in arrays:
            assert not re.search(rf"\$\{{?{name}\b(?!\[)", line), line


def test_the_folders_are_private_before_anything_lands():
    # why: state.db keeps whole revisions of every recording.json, titles included, so it is
    # as private as the archive. Nobody but the owner gets into $RECORDINGS_HOME.
    lines = _code_lines(_section("### 1a. Server facts"))
    assert any('chmod 700 "$RECORDINGS_HOME"' in line and '"$RECORDINGS_HOME/state"' in line
               for line in lines)


def test_manual_runs_never_race_the_backup_loop():
    # why: `rc up -d` starts `ops --loop`. A manual backup beside it fights it for restic's
    # lock, and a loop backup could snapshot an import half done.
    running = False
    for line in _code_lines():
        if re.search(r"\brc (up -d|start backup)\b", line) or "systemctl reboot" in line:
            running = True
        elif re.search(r"\brc (stop backup|kill backup|down)\b", line):
            running = False
        elif re.search(r"\brc run --rm backup\b", line) or (
                "import-audio-router" in line and "--dry-run" not in line):
            assert not running, line
    # §20 asks for a killed backup service to raise the alert, and a stop isn't a kill.
    assert any(re.search(r"\brc kill backup\b", line) for line in _code_lines())


def test_the_imported_data_is_restore_tested_before_the_import_copy_goes():
    lines = _code_lines(_section("## 4. Import"))

    def first(pattern: str) -> int:
        found = [i for i, line in enumerate(lines) if re.search(pattern, line)]
        assert found, f"step 4 never runs {pattern!r}"
        return found[0]

    post = first(r"backup run --tag post-import")
    restored = first(r"recordings backup restore-test")
    deleted = first(r'rm -rf "\$RECORDINGS_HOME/import"')
    assert post < restored < deleted


def test_restoring_for_real_puts_every_piece_back():
    code = "\n".join(_code_lines(_section("## Restoring for real")))
    for needed in ("restore latest --host", "--tag recordings", "--include",
                   "recordings reindex", "recordings init --adopt",
                   "/archive", "config.toml", "deploy.env", "state.db"):
        assert needed in code, needed
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest packages/core/tests/test_runbook.py -v`
Expected: 9 failed, each with `FileNotFoundError` for `docs/runbooks/first-run.md`.

- [ ] **Step 3: Write the runbook**

`docs/runbooks/first-run.md`:
````markdown
# The first real run (stage 2a)

Steps 1–4 of the spec's "order of the first real run" (§19), and how to restore for real. Steps
5–7 belong to stage 2b. Each block says which machine it runs on. Nothing here prints a secret: a
command that needs one reads it from a file, and secret files are written with an editor, never
typed on a command line, where shell history would keep them.

Set these in the shell you use on the homelab server. They are examples; use your own values.
The blocks work in bash and in zsh: lists of options are kept in arrays, which both shells split
the same way.

```bash
export RECORDINGS_HOME=/srv/recordings          # on the server's own data disk (spec §3)
export SERVER_TS="$(tailscale ip -4)"           # this server's tailnet address
export NAS_HOST=nas                             # the NAS's DNS or tailnet name: never an ssh alias
export NAS_MIRROR_USER=recordings-mirror        # the NAS account the mirror uses (1b.8)
export NAS_MIRROR=/volume1/recordings-mirror    # the mirror's shared folder, as rsync over ssh sees it
export NAS_MIRROR_SFTP=/recordings-mirror       # the same folder, as SFTP sees it
```

`NAS_HOST` must resolve inside a container, where only DNS works: a name from `~/.ssh/config`
would work for you and fail for the backup service.

Blocks marked "on the NAS" run there, as a DSM administrator over SSH (Control Panel → Terminal &
SNMP → Enable SSH service), in the NAS's own shell. The variables above aren't set there.

## 1. Spikes

### 1a. Server facts

About 15 minutes, on the homelab server. Write each answer in the checklist at the end.

1. **The archive's folder sits on the data disk.**
   ```bash
   sudo mkdir -p "$RECORDINGS_HOME"
   findmnt -no TARGET,SOURCE -T "$RECORDINGS_HOME"
   ```
   Expected: a `TARGET` that is `$RECORDINGS_HOME` or a parent made for the data disk, never
   `/`. If it shows `/`, stop: remove the folder you just made (`sudo rmdir "$RECORDINGS_HOME"`),
   mount the data disk, and start again. The archive must not sit on the system disk.

2. **The filesystem is local.**
   ```bash
   findmnt -no FSTYPE -T "$RECORDINGS_HOME"
   ```
   Expected: `ext4`, `xfs` or `btrfs`. Not `nfs` or `cifs`: `flock`, hard links and SQLite's
   write-ahead log must work locally (spec §3, §6.4, §6.8). `state.db` lives here too, in
   `state/`: `[state] path` must never point at NFS or SMB.

3. **Free space.**
   ```bash
   df -h "$RECORDINGS_HOME"
   ```
   It needs room for the `audio-router` archive three times over, with at least 20% of the disk
   still free afterwards. In step 4 the import copy (`import/`), the archive and the restore
   test's copy (`restore-test/`) all exist at once, and the restore test refuses to take the disk
   below 20% free. A real restore ("Restoring for real") needs the archive's size again.

4. **The folders, their owner, and who can read them.** `archive/` stays empty until
   `recordings init` (step 2), and it must live *inside* the mount, so an unmounted disk means a
   missing folder, which Docker then refuses to start without. `state.db` keeps whole revisions
   of every `recording.json`, titles included, so `state/` is as private as the archive.
   ```bash
   sudo mkdir -p "$RECORDINGS_HOME"/archive "$RECORDINGS_HOME"/state "$RECORDINGS_HOME"/restore-test \
     "$RECORDINGS_HOME"/secrets "$RECORDINGS_HOME"/import "$RECORDINGS_HOME"/bin
   sudo chown -R "$(id -u):$(id -g)" "$RECORDINGS_HOME"
   chmod 700 "$RECORDINGS_HOME" "$RECORDINGS_HOME/state" "$RECORDINGS_HOME/secrets"
   id -u
   id -g
   ```
   The two numbers go in `docker/deploy.env` as `RECORDINGS_UID` and `RECORDINGS_GID`. With
   `$RECORDINGS_HOME` at 700, no other local user reaches anything under it.

5. **Hard links and `flock` work on this disk.**
   ```bash
   cd "$RECORDINGS_HOME/restore-test" && touch probe && ln probe probe.link \
     && flock -n probe true && echo "hard links and flock: ok"; rm -f probe probe.link
   cd -
   ```
   Expected: `hard links and flock: ok`.

6. **Tailscale's address at boot.** The app publishes on the server's tailnet address. At boot,
   `tailscaled` reports ready before that address exists, and Docker never retries a container
   whose port couldn't bind. Step 2.5 installs a unit that waits for the address and the data
   disk; here, record the facts it relies on.
   ```bash
   systemctl is-enabled tailscaled
   systemctl show -p After docker.service | tr ' ' '\n' | grep -c tailscaled
   ip -4 -o addr show tailscale0
   ```
   Expected: `enabled`; `0` or `1` (whether Docker already starts after Tailscale: either is
   fine, the unit covers both); and one `inet` line on `tailscale0`. No `tailscale0` means
   Tailscale runs in userspace mode, where the unit's wait can't work: say so in the checklist
   before going on.

### 1b. NAS transport

On the NAS and the homelab server. The NAS runs DSM 7.3 on ext4, which has no Btrfs snapshots,
so nothing on the NAS could protect an SFTP repository from a compromised server. Backups go to
**rest-server with `--append-only`** instead (spec §15.1): the homelab server can add snapshots
but never delete or change one. Retention runs on the NAS (1b.7). The mirror uses its own
non-admin NAS account over SSH (1b.8). SFTP is only the fallback (1b.9).

1. **What the NAS has, and whether its name resolves.**
   ```bash
   # on the NAS
   uname -m
   sudo docker version --format '{{.Server.Version}}'
   ```
   Expected: `x86_64` or `aarch64`; then a version if Container Manager is installed and
   running, or "command not found" if not (1b.5 then uses the static binary). On the server:
   ```bash
   getent hosts "$NAS_HOST"
   ```
   Expected: one address. Nothing means `NAS_HOST` is a name only ssh knows: pick one that DNS
   or the tailnet resolves.

2. **restic for the spike, on the server.** Its own copy, checked against the release's
   `SHA256SUMS` (the same pin as Task 11's `scripts/fetch_restic.py` and the image).
   ```bash
   cd "$RECORDINGS_HOME/bin"
   case "$(uname -m)" in
     x86_64)  ARCH=linux_amd64 SUM=f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c ;;
     aarch64) ARCH=linux_arm64 SUM=a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465 ;;
     *)       ARCH=unpinned SUM=none ;;
   esac
   curl -fsSLO "https://github.com/restic/restic/releases/download/v0.19.1/restic_0.19.1_$ARCH.bz2"
   echo "$SUM  restic_0.19.1_$ARCH.bz2" | sha256sum -c - && bunzip2 -c "restic_0.19.1_$ARCH.bz2" > restic && chmod 755 restic
   rm -f "restic_0.19.1_$ARCH.bz2"
   ./restic version
   cd -
   ```
   Expected: `restic_0.19.1_linux_amd64.bz2: OK` (or `arm64`), then `restic 0.19.1 compiled
   with …`. A sum that doesn't match: stop. Another machine type: take its line from restic's
   v0.19.1 `SHA256SUMS` and check it the same way.

3. **The two passwords.** Generate both in your password manager, which keeps the copies you
   need to read the backups. Paste each into its file with an editor.
   ```bash
   cd "$RECORDINGS_HOME/secrets"
   install -m 600 /dev/null restic_password && ${EDITOR:-nano} restic_password
   install -m 600 /dev/null rest_password && ${EDITOR:-nano} rest_password
   cd -
   ```
   `restic_password` encrypts the repository: without it the backups can't be read.
   `rest_password` is rest-server's login for its user `recordings`.

4. **The NAS's folders, and TLS or the tailnet.** In DSM (Control Panel → Shared Folder), create
   two shared folders on `volume1`:
   - `backups`: read and write for administrators only.
   - `recordings-mirror`: read and write for the mirror account (1b.8), read-only for your own DSM
     account, no access for anyone else. Turn its recycle bin off and leave it out of indexing:
     the mirror refuses a target that holds anything but its own archive.

   Then the folders the rest of 1b uses:
   ```bash
   # on the NAS
   sudo mkdir -p /volume1/backups/rest /volume1/backups/tls /volume1/backups/bin /volume1/backups/retention
   sudo chmod 700 /volume1/backups/tls /volume1/backups/retention
   ```
   **Choose how rest-server's password travels:**
   - **TLS,** which works over the LAN or the tailnet. A self-signed certificate for `NAS_HOST`:
     ```bash
     # on the NAS. NAS_HOST: the same name the server uses.
     NAS_HOST=nas
     sudo openssl req -newkey rsa:2048 -nodes -x509 -days 3650 -subj "/CN=$NAS_HOST" \
       -addext "subjectAltName = DNS:$NAS_HOST" \
       -keyout /volume1/backups/tls/private_key -out /volume1/backups/tls/public_key
     sudo chmod 600 /volume1/backups/tls/private_key
     sudo cat /volume1/backups/tls/public_key
     ```
     Then, on the server, paste the printed certificate into `nas_cacert`. It isn't secret, but it
     lives with the secret files, so the backup service gets it as one:
     ```bash
     install -m 600 /dev/null "$RECORDINGS_HOME/secrets/nas_cacert" && ${EDITOR:-nano} "$RECORDINGS_HOME/secrets/nas_cacert"
     ```
     The repository is then `rest:https://<NAS_HOST>:8000/recordings/`, with
     `[backup] cacert = "/run/secrets/nas_cacert"`.
   - **The tailnet,** only if the NAS runs Tailscale and `NAS_HOST` is its tailnet name. Its
     WireGuard tunnel then carries the password, and rest-server needs no TLS. The repository is
     `rest:http://<NAS_HOST>:8000/recordings/`, with no `cacert`. Plain HTTP to a LAN name would
     send the password in clear, so never use `http://` with one. Compose still needs the file:
     ```bash
     install -m 600 /dev/null "$RECORDINGS_HOME/secrets/nas_cacert"
     ```

5. **rest-server, append-only.** One of the two ways below, whichever 1b.1 allows. Both keep the
   repositories in `/volume1/backups/rest` and run as root, like the retention task (1b.7), so
   each can read what the other writes. Over the tailnet, leave out the three `--tls` options.

   **In Container Manager:**
   ```bash
   # on the NAS
   sudo docker run -d --name rest-server --restart unless-stopped -p 8000:8000 \
     -v /volume1/backups/rest:/data -v /volume1/backups/tls:/tls:ro \
     -e OPTIONS="--append-only --private-repos --tls --tls-cert /tls/public_key --tls-key /tls/private_key" \
     restic/rest-server:0.14.0@sha256:d2aff06f47eb38637dff580c3e6bce4af98f386c396a25d32eb6727ec96214a5
   sudo docker exec -it rest-server create_user recordings
   ```
   `create_user` asks for the password twice: paste `rest_password` from your password manager.

   **Or as the static binary, started at boot by Task Scheduler:**
   ```bash
   # on the NAS
   cd /tmp
   case "$(uname -m)" in
     x86_64)  ARCH=linux_amd64 SUM=4c9c95bc079a0334e81fad379b19dc5c3353c71c2c88d652cafce2081c2b1c66 ;;
     aarch64) ARCH=linux_arm64 SUM=cef139cbe8b27b16bda731d17f093b0aa466b8c60b136c12d78b6f2bff3daf22 ;;
     *)       ARCH=unpinned SUM=none ;;
   esac
   curl -fsSLO "https://github.com/restic/rest-server/releases/download/v0.14.0/rest-server_0.14.0_$ARCH.tar.gz"
   echo "$SUM  rest-server_0.14.0_$ARCH.tar.gz" | sha256sum -c - && tar xzf "rest-server_0.14.0_$ARCH.tar.gz"
   sudo install -m 755 "rest-server_0.14.0_$ARCH/rest-server" /volume1/backups/bin/rest-server
   rm -rf "rest-server_0.14.0_$ARCH" "rest-server_0.14.0_$ARCH.tar.gz"
   ```
   The sums are from rest-server v0.14.0's `SHA256SUMS`, checked 2026-10-09. For another
   machine type, take its line from there.

   DSM has no `htpasswd`, so make the login line on the homelab server, with rest-server's own
   image. It asks for the password (paste `rest_password`) and prints a bcrypt hash, never the
   password:
   ```bash
   docker run --rm -it --entrypoint htpasswd restic/rest-server:0.14.0@sha256:d2aff06f47eb38637dff580c3e6bce4af98f386c396a25d32eb6727ec96214a5 -nB recordings
   ```
   On the NAS, paste the printed `recordings:$2y$…` line into the password file with an editor:
   ```bash
   # on the NAS
   sudo vi /volume1/backups/rest/.htpasswd
   sudo chmod 600 /volume1/backups/rest/.htpasswd
   ```
   Then, in DSM: Control Panel → Task Scheduler → Create → Triggered Task → User-defined script.
   User `root`, event Boot-up, and this script:
   ```bash
   /volume1/backups/bin/rest-server --path /volume1/backups/rest --listen :8000 --append-only --private-repos --tls --tls-cert /volume1/backups/tls/public_key --tls-key /volume1/backups/tls/private_key >>/volume1/backups/rest-server.log 2>&1
   ```
   Run the task once now (select it, then Run). 1b.6 checks it from the server; reboot the NAS
   once afterwards and check again, which proves the boot start.

6. **Prove it from the server, against a spike repository.**
   ```bash
   RESTIC="$RECORDINGS_HOME/bin/restic"
   export RESTIC_PASSWORD_FILE="$RECORDINGS_HOME/secrets/restic_password"
   export RESTIC_REST_USERNAME=recordings
   export RESTIC_REST_PASSWORD="$(cat "$RECORDINGS_HOME/secrets/rest_password")"
   CACERT=(--cacert "$RECORDINGS_HOME/secrets/nas_cacert")     # over the tailnet: CACERT=()
   SPIKE="rest:https://$NAS_HOST:8000/recordings/spike/"        # over the tailnet: rest:http://
   "$RESTIC" -r "$SPIKE" "${CACERT[@]}" init
   "$RESTIC" -r "$SPIKE" "${CACERT[@]}" backup /etc/os-release
   "$RESTIC" -r "$SPIKE" "${CACERT[@]}" backup /etc/os-release
   "$RESTIC" -r "$SPIKE" "${CACERT[@]}" check
   "$RESTIC" -r "$SPIKE" "${CACERT[@]}" restore latest --target "$RECORDINGS_HOME/restore-test/spike" --verify
   "$RESTIC" -r "$SPIKE" "${CACERT[@]}" unlock
   "$RESTIC" -r "$SPIKE" "${CACERT[@]}" forget --keep-last 1 --prune
   rm -rf "$RECORDINGS_HOME/restore-test/spike"
   ```
   Expected: every `restic` command exits 0 except the last, and `restore-test/spike/etc/os-release`
   appeared before the `rm` removed it. `unlock` works through append-only. `forget` fails with
   HTTP 403 (Forbidden): the server can't delete a snapshot, which is the point.

   The spike can't be deleted through rest-server either, so delete it on the NAS:
   ```bash
   # on the NAS
   sudo rm -rf /volume1/backups/rest/recordings/spike
   ```

7. **Retention on the NAS.** The server can't forget old snapshots through append-only, so a
   weekly task on the NAS does, with restic run directly on the repository's folder and its own
   copy of the password, readable only by root. First, the same pinned restic on the NAS:
   ```bash
   # on the NAS
   cd /tmp
   case "$(uname -m)" in
     x86_64)  ARCH=linux_amd64 SUM=f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c ;;
     aarch64) ARCH=linux_arm64 SUM=a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465 ;;
     *)       ARCH=unpinned SUM=none ;;
   esac
   curl -fsSLO "https://github.com/restic/restic/releases/download/v0.19.1/restic_0.19.1_$ARCH.bz2"
   echo "$SUM  restic_0.19.1_$ARCH.bz2" | sha256sum -c - && bunzip2 -c "restic_0.19.1_$ARCH.bz2" > restic
   sudo install -m 755 restic /volume1/backups/bin/restic && rm -f restic "restic_0.19.1_$ARCH.bz2"
   sudo install -m 600 /dev/null /volume1/backups/retention/restic_password
   sudo vi /volume1/backups/retention/restic_password
   ```
   Paste the same `restic_password` from your password manager. If the NAS lacks `bunzip2`, run
   the download and the check on the server instead, and copy the checked `restic` over.

   Then, in DSM: Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script.
   User `root`, weekly (Sunday 04:30, say), with run details sent by email when the script ends
   abnormally, and this script. The cache and temporary files stay on the volume, because DSM's
   system partition is small.
   ```bash
   export RESTIC_PASSWORD_FILE=/volume1/backups/retention/restic_password
   export RESTIC_CACHE_DIR=/volume1/backups/retention/cache TMPDIR=/volume1/backups/retention/tmp
   mkdir -p "$RESTIC_CACHE_DIR" "$TMPDIR"
   R=/volume1/backups/rest/recordings
   /volume1/backups/bin/restic -r "$R" unlock
   /volume1/backups/bin/restic -r "$R" forget --retry-lock 30m --keep-hourly 24 --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --keep-tag pre-import --keep-tag post-import --prune
   ```
   The keep rules are spec §15.1's. The `pre-import` and `post-import` snapshots are kept for
   good (§19). Step 2.6 runs the task once, when the repository has its first snapshot.

8. **The mirror's account** (spec §19: whether the mirror's account can run rsync into its
   share).
   - In DSM, create the user `recordings-mirror`. It is not an administrator. It can read and
     write `recordings-mirror` and nothing else, and its application permissions allow only
     rsync and SFTP.
   - Turn on Control Panel → File Services → rsync → Enable rsync service, and File Services →
     FTP → SFTP. Turn on the user home service (User & Group → Advanced), because the account's
     key lives in its home.

   The key and the NAS's host key, on the server:
   ```bash
   K="$RECORDINGS_HOME/secrets"
   ssh-keygen -t ed25519 -N "" -C recordings-mirror -f "$K/nas_ssh_key"
   ssh-keyscan -t ed25519 "$NAS_HOST" > "$K/nas_known_hosts"
   ssh-keygen -lf "$K/nas_known_hosts"
   cat "$K/nas_ssh_key.pub"
   ```
   Compare that fingerprint with the NAS's own before trusting it
   (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`, on the NAS).

   On the NAS, the account's `authorized_keys` gets one line: `restrict,from="<the server's
   address, as the NAS sees it>" `, then the whole `.pub` line. The address is the server's
   tailnet address if it reaches the NAS over the tailnet, or its LAN address. `restrict` turns
   off forwarding and terminals. There is no forced command, so rsync and sftp's `df` both work.
   ```bash
   # on the NAS
   sudo mkdir -p /volume1/homes/recordings-mirror/.ssh
   sudo vi /volume1/homes/recordings-mirror/.ssh/authorized_keys
   sudo chown -R recordings-mirror:users /volume1/homes/recordings-mirror/.ssh
   sudo chmod 711 /volume1/homes/recordings-mirror
   sudo chmod 700 /volume1/homes/recordings-mirror/.ssh
   sudo chmod 600 /volume1/homes/recordings-mirror/.ssh/authorized_keys
   ```

   Then prove it from the server: a dry run, a real copy, and sftp's `df`, which also removes the
   copy again.
   ```bash
   K="$RECORDINGS_HOME/secrets"
   SSH_OPTS=(-i "$K/nas_ssh_key" -o "UserKnownHostsFile=$K/nas_known_hosts" -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes)
   rsync -an -e "ssh ${SSH_OPTS[*]}" /etc/os-release "$NAS_MIRROR_USER@$NAS_HOST:$NAS_MIRROR/"
   rsync -a -e "ssh ${SSH_OPTS[*]}" /etc/os-release "$NAS_MIRROR_USER@$NAS_HOST:$NAS_MIRROR/"
   printf -- '-df %s\nrm %s/os-release\nls %s\n' "$NAS_MIRROR_SFTP" "$NAS_MIRROR_SFTP" "$NAS_MIRROR_SFTP" | sftp -b - "${SSH_OPTS[@]}" "$NAS_MIRROR_USER@$NAS_HOST"
   ```
   Expected:
   - Both `rsync` runs exit 0. If rsync says the folder doesn't exist, this DSM shows rsync the
     share's SFTP-style path: set `NAS_MIRROR` to `$NAS_MIRROR_SFTP` and try again.
   - If the real copy fails with "failed to set permissions" (exit 23), the share's ACLs refuse
     rsync's permission changes. Run it again with `--no-perms` added, and write "`--no-perms`:
     yes" in the checklist, and set `[mirror] no_perms = true` in step 2.1.
   - sftp prints a `df` table with an `Avail` column, removes the file, and lists nothing. The
     share is empty again, as the mirror requires. Both shares sit on `volume1`, so this `df`
     measures the backups' space too. If sftp says the server doesn't support
     `statvfs@openssh.com`, the free-space check will say "unknown": write that in the checklist.

9. **Only if rest-server can't run at all: restic over SFTP.** This accepts the risk that
   1b avoids: the server's key can delete the repository, and ext4 has no snapshots to fall back
   on. Hyper Backup of the `backups` share to an off-site target is then the mitigation.
   - Create a second non-admin account, `recordings-backup`, with SFTP only, read and write on
     `backups` only. Give it the same public key, restricted the same way as in 1b.8.
   - DSM's SFTP root lists the shared folders, so the repository path starts at the share:
     `/backups/recordings`, not `/volume1/backups/recordings`.
   ```bash
   SPIKE="sftp:recordings-backup@$NAS_HOST:/backups/recordings-spike"
   "$RESTIC" -r "$SPIKE" -o sftp.args="${SSH_OPTS[*]}" init
   "$RESTIC" -r "$SPIKE" -o sftp.args="${SSH_OPTS[*]}" backup /etc/os-release
   "$RESTIC" -r "$SPIKE" -o sftp.args="${SSH_OPTS[*]}" check
   "$RESTIC" -r "$SPIKE" -o sftp.args="${SSH_OPTS[*]}" forget --keep-last 1 --prune
   ```
   Expected: each exits 0. Then delete `/volume1/backups/recordings-spike` on the NAS. The
   repository is `sftp:recordings-backup@<NAS_HOST>:/backups/recordings`, with
   `[backup] prune = true`, and no retention task on the NAS (skip 1b.7's task).

10. **Record the decision** for step 2's `config.toml`:
    - `[backup] repository`: `rest:https://<NAS_HOST>:8000/recordings/` (or `http://` over the
      tailnet), with `rest_username = "recordings"`, `cacert = "/run/secrets/nas_cacert"` with
      TLS, and `prune = false`.
    - `[nas] ssh = "recordings-mirror@<NAS_HOST>"`, for sftp's `df`, and
      `[backup] nas_repo_path = "/recordings-mirror"`: any folder on the backups' volume, as SFTP
      sees it.
    - `[mirror] target = "recordings-mirror@<NAS_HOST>:/volume1/recordings-mirror/"`, or the form
      1b.8 found.
    - `[mirror] no_perms = true`, only if 1b.8 needed `--no-perms`.

### 1c. Before stage 2b (not needed for 2a)

The token spike and the ID-form spike (§9.1, §19) both call Plaud. Stage 2a makes no Plaud
calls, so they run before stage 2b's deploy, not now.

## 2. Deploy 2a against an empty, initialised archive

On the homelab server, in the repo checkout.

1. **Settings.** Copy both templates, then edit them:
   - `config.toml`: `[archive] writer_id`; `[state] path`; `[backup]`, `[nas]` and `[mirror]` as
     1b.10 recorded; `[alerts] ntfy_server = "https://ntfy.sh"`; and
     `[plaud] consent_list = "/consent/consent-list.md"` (step 3 brings the list over).
   - `docker/deploy.env`: the UID and GID from 1a.4, this server's tailnet address (`$SERVER_TS`)
     as `RECORDINGS_BIND`, and the host folders under `$RECORDINGS_HOME`.
   - **`state.db` must be on a local disk.** In the containers `[state] path` is `/state`, bound
     to `RECORDINGS_STATE_HOST`. Keep that on the disk 1a.2 checked, never on NFS or SMB.
   ```bash
   cp config.example.toml config.toml
   cp docker/deploy.example.conf docker/deploy.env
   ```

2. **Alerts, and the last secret files.**
   - **ntfy.sh:** generate a topic in your password manager: 20 or more random letters and
     digits (only `A–Z`, `a–z`, `0–9`, `-` and `_`, at most 64). Subscribe to it in the ntfy app
     on your phone. The topic is the only lock on your alerts.
   - **Healthchecks.io:** create a check with a period of 1 hour and a grace of 3 hours. The
     grace is long because the loop runs the mirror, the check and the restore test between
     backups, and a long one must not look like an outage. Add an integration that reaches your
     phone: email, or ntfy with the same topic. Copy the check's ping URL
     (`https://hc-ping.com/…`) without `/start` or `/fail`; the app adds those.
   - **Keep both in your password manager,** the topic and the ping URL. Whoever holds them can
     read your alerts or keep an outage quiet.
   ```bash
   cd "$RECORDINGS_HOME/secrets"
   install -m 600 /dev/null ntfy_topic && ${EDITOR:-nano} ntfy_topic
   install -m 600 /dev/null deadman_url && ${EDITOR:-nano} deadman_url
   ls -l
   cd -
   ```
   Expected: `deadman_url`, `nas_cacert` (empty over the tailnet), `nas_known_hosts`,
   `nas_ssh_key`, `nas_ssh_key.pub`, `ntfy_topic`, `rest_password` and `restic_password`. Each is
   `-rw-------`, except the `.pub`.

3. **The tailnet ACL, before anything listens** (spec §15: the app's port is for your devices
   only). Set it in Tailscale's admin console, under Access controls. A sketch to merge into your
   own policy, with the server tagged `tag:recordings`:
   ```jsonc
   {
     "tagOwners": { "tag:recordings": ["autogroup:admin"] },
     "grants": [
       // Only your own devices reach the app's port (RECORDINGS_PORT).
       { "src": ["you@example.com"], "dst": ["tag:recordings"], "ip": ["tcp:8000"] }
     ]
   }
   ```
   No broader rule, such as the default allow-all, may still cover the server. Then check from a
   device that should be refused (a tagged device, a shared node, or the NAS if it is on the
   tailnet), with `SERVER_TS` set there to the server's tailnet address:
   ```bash
   nc -vz -w 5 "$SERVER_TS" 8000
   ```
   Expected: a timeout. "Connection refused" means its packets reached the server: fix the ACL
   before going on. From one of your own devices, the same line says "refused" for now, because
   nothing listens yet.

4. **Build, initialise once, start.** `rc` is shorthand for the project's compose command, with
   the image tagged by the commit (as `make deploy` tags it).
   ```bash
   git status --porcelain
   export RECORDINGS_IMAGE_TAG="$(git rev-parse --short=12 HEAD)"
   alias rc='docker compose --env-file docker/deploy.env -f docker/compose.yml'
   rc build
   rc run --rm web recordings init
   rc run --rm backup recordings backup init
   rc up -d
   rc exec web recordings doctor --role web
   rc exec backup recordings doctor --role backup
   ```
   Expected:
   - `git status --porcelain` prints nothing, so the tag names the code exactly.
   - `init` prints the archive's UUID, and `backup init` reports the repository initialised.
   - Neither `doctor` reports a problem. `--role web` confirms that the web service holds no
     backup or mirror secrets.
   - Both warn that retention is off here (`[backup] prune = false`). That is right for
     rest-server: retention runs on the NAS (1b.7).
   - From your own device, `nc -vz -w 5 "$SERVER_TS" 8000` now connects. From the refused
     device it still times out.

   `rc up -d` also starts the backup service's loop, which runs every due job at once: the first
   backup, mirror, check and restore test run now, on the empty archive.

5. **Start at boot.** `docker/recordings.service` waits, for up to 2 minutes, for an IPv4 address
   on `tailscale0` and for the data disk, then runs `docker compose … up -d`. It is its own unit,
   not a change to `docker.service`, so other containers on this server never wait for it. It
   reads the deployed image tag from the existing web container (`docker/up-deployed.sh`), so the
   `rc build` / `rc up -d` flow above needs no tag file. Install it:
   ```bash
   sudo install -m 644 docker/recordings.service /etc/systemd/system/recordings.service
   ```
   If the paths at its top don't match your checkout and `$RECORDINGS_HOME`, change the installed
   copy with `sudo systemctl edit --full recordings.service`, never the checkout's file, which
   would make the next deploy `-dirty`. Then:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable recordings.service
   sudo systemctl reboot
   ```
   After the reboot, in a new shell with the variables, `RECORDINGS_IMAGE_TAG` and `rc` set
   again:
   ```bash
   systemctl is-active recordings.service
   rc ps
   curl -fsS "http://$SERVER_TS:8000/healthz"
   ```
   Expected: `active`, both `web` and `backup` running, and an answer from `/healthz`.

6. **Prove backup, restore and the mirror.** Stop the loop first, so the manual runs don't
   compete with it for restic's lock, then start it again.
   ```bash
   rc stop backup
   rc run --rm backup recordings backup run
   rc run --rm backup recordings backup restore-test
   rc run --rm backup recordings mirror
   rc run --rm backup recordings alert-test
   rc start backup
   ```
   Expected:
   - `backup run`, `restore-test` and `mirror` each report `ok: true`.
   - The phone gets the test push. Healthchecks shows the check up, with a log entry from
     `alert-test`.
   - The Status page shows the backup, the restore test and the mirror.

   Then, on the NAS, run the retention task once (Task Scheduler: select it, then Run). Its
   history shows that it ended normally.

7. **Prove that a killed backup raises an alert** (§20: "a killed backup raises an alert").
   ```bash
   rc kill backup
   ```
   `kill`, not `stop`, because §20 asks for a killed backup, and Docker doesn't restart a
   container killed by hand. Wait the period plus the grace (1 + 3 hours) from the last ping. The
   checker's alert arrives on the phone. Then start it again:
   ```bash
   rc start backup
   ```

## 3. Bring the source over

**On the Mac**, from `audio-router`'s own copy of its archive: never the NAS's Drive copy, which
may be mid-sync. `AR_MEDIA` is the folder that holds `plaud/`, `recorder/`, `pocket/` and
`catalog/`. `CONSENT_LIST` is `audio-router`'s consent list ("never mirror"). `HOMELAB` is your
ssh name for the homelab server.
```bash
export AR_MEDIA="$HOME/path/to/audio-router/media"
export CONSENT_LIST="$HOME/path/to/consent-list.md"
export HOMELAB=homelab
cd "$AR_MEDIA" && find . -type f ! -name '.*' -print0 | sort -z | xargs -0 shasum -a 256 > "$TMPDIR/audio-router.sha256"
rsync -a "$AR_MEDIA"/ "$HOMELAB":/srv/recordings/import/
rsync -a "$TMPDIR/audio-router.sha256" "$HOMELAB":/srv/recordings/import.sha256
rsync -a "$CONSENT_LIST" "$HOMELAB":/srv/recordings/consent-list.md
```

**On the homelab server:** check the copy, make it read-only, and run the dry run.
```bash
cd "$RECORDINGS_HOME/import" && sha256sum --check --quiet ../import.sha256 && echo "copy verified"
cd -
chmod -R a-w "$RECORDINGS_HOME/import"
chmod 400 "$RECORDINGS_HOME/consent-list.md"
IMPORT_MOUNTS=(-v "$RECORDINGS_HOME/import:/import:ro" -v "$RECORDINGS_HOME/consent-list.md:/consent/consent-list.md:ro")
rc run --rm "${IMPORT_MOUNTS[@]}" web recordings import-audio-router /import --dry-run
```
Expected: `copy verified`, then a report in which every catalog row has one disposition and
`unplaced` is 0.
- IDs on the consent list appear only as a `consent-excluded` count. If the list can't be read,
  the dry run refuses: it fails closed.
- A Plaud row with audio but no snapshot and no time is `deferred-to-2b`, with its Plaud ID, for
  2b's sync.
- Every `audio-router` tag comes over. Privacy labels (`private`, `therapy` and the others in
  Task 8's `PRIVATE_TAGS`) become private tags, one per meaning. Those rows, rows with an `access` tier, and
  the private tier are `new-private`: check the report's private count against
  `audio-router`'s private rows.
- If anything is unplaced, stop: the report gives each row's `uri` and reason.

## 4. Import

In the shell from step 3, where `IMPORT_MOUNTS` is set. Stop the loop first, so no scheduled
backup lands in the middle of the import. Pause the Healthchecks check first (its Pause button):
no backup pings while the loop is stopped, and the import can outlast the grace.
```bash
rc stop backup
rc run --rm backup recordings backup run --tag pre-import
rc run --rm "${IMPORT_MOUNTS[@]}" web recordings import-audio-router /import
rc run --rm web recordings validate /archive --deep
rc run --rm backup recordings backup run --tag post-import
rc run --rm backup recordings backup restore-test
rc start backup
```
Expected: the import reports no failures, `validate` reports no problems, and both backups and the
restore test report `ok: true`. The restore test now proves the imported data, not just an empty
archive. Resume the Healthchecks check. The first full mirror of the imported archive runs next
and can take hours; if Healthchecks alerts during it, the next backup's ping clears it.

Once the restore test has passed, and only then, delete the import copy. The archive and its
backups hold everything, and `import/` is a plaintext copy nobody needs. Keep `consent-list.md`:
2b's sync reads it.
```bash
chmod -R u+w "$RECORDINGS_HOME/import"
rm -rf "$RECORDINGS_HOME/import" "$RECORDINGS_HOME/import.sha256"
```

Then open the Library on one of your devices. It lists the imported recordings, with
`audio-router`'s tags, and its first load takes under 2 seconds (your browser's network panel shows the
time). If it takes longer, stop: the Library moves onto `index.db` before stage 2b (stage-2a
plan, decision 6).

**Rolling back, until stage 3's first tag** (§19): the deployment is disposable.
```bash
rc down
mv "$RECORDINGS_HOME/archive" "$RECORDINGS_HOME/archive.failed-$(date +%Y%m%d)"
mv "$RECORDINGS_HOME/state" "$RECORDINGS_HOME/state.failed-$(date +%Y%m%d)"
mkdir "$RECORDINGS_HOME/archive" && mkdir -m 700 "$RECORDINGS_HOME/state"
```
Then:
- Run step 2.4 from `rc run --rm web recordings init` on, but skip `backup init`: the repository
  is still there, with the old snapshots.
- Run step 3 again if `import/` is already gone, then step 4.
- `recordings init` gives the new archive a new UUID, so the mirror refuses its old copy. Once
  you're sure, empty the `recordings-mirror` share on the NAS (in File Station), and the next run
  fills it.
- When the new import has passed step 4, delete the `.failed-` folders: they are plaintext
  copies too.

## Restoring for real

When the archive, `state.db`, or the whole server is lost. Each snapshot holds the container
paths `/archive`, `/config/config.toml`, `/backup-extra/deploy.env` and `/state/backup/state.db`
(a consistent SQLite copy). `index.db` is derived, so it isn't there; `reindex` rebuilds it.

On the homelab server, in the repo checkout, with the variables from the top of this runbook
set. A new server first goes through 1a, then:
- clone the repo to `$RECORDINGS_HOME/repo`
- get restic as in 1b.2
- get its secret files from your password manager as in 1b.3, 1b.4 and 2.2
- make a new mirror key and replace its `authorized_keys` line on the NAS (1b.8), because the
  `from=` address may change
- set the tailnet ACL for it (2.3)

1. **Find the snapshot.**
   ```bash
   export RECORDINGS_IMAGE_TAG="$(git rev-parse --short=12 HEAD)"
   alias rc='docker compose --env-file docker/deploy.env -f docker/compose.yml'
   rc down
   RESTIC="$RECORDINGS_HOME/bin/restic"
   export RESTIC_PASSWORD_FILE="$RECORDINGS_HOME/secrets/restic_password"
   export RESTIC_REST_USERNAME=recordings
   export RESTIC_REST_PASSWORD="$(cat "$RECORDINGS_HOME/secrets/rest_password")"
   CACERT=(--cacert "$RECORDINGS_HOME/secrets/nas_cacert")     # over the tailnet: CACERT=()
   REPO="rest:https://$NAS_HOST:8000/recordings/"               # [backup] repository
   WRITER_ID=homelab                                            # [archive] writer_id
   "$RESTIC" -r "$REPO" "${CACERT[@]}" snapshots --host "$WRITER_ID" --tag recordings
   ```
   `rc down` only matters if anything still runs. `snapshots` lists the newest last.

2. **Restore it into a scratch folder.** It needs as much free space as the archive.
   ```bash
   "$RESTIC" -r "$REPO" "${CACERT[@]}" restore latest --host "$WRITER_ID" --tag recordings \
     --target "$RECORDINGS_HOME/restore-real" --verify
   ```

3. **Move each lost piece into place.** Move what is there aside first, and delete it only once
   the restored copy works. Moving the whole old `state/` aside also takes its stale
   write-ahead log with it, which must never meet the restored `state.db`.
   ```bash
   R="$RECORDINGS_HOME/restore-real"
   STAMP="$(date +%Y%m%d)"
   mv "$RECORDINGS_HOME/archive" "$RECORDINGS_HOME/archive.lost-$STAMP"
   mv "$R/archive" "$RECORDINGS_HOME/archive"
   mv "$RECORDINGS_HOME/state" "$RECORDINGS_HOME/state.lost-$STAMP"
   mkdir -m 700 "$RECORDINGS_HOME/state"
   mv "$R/state/backup/state.db" "$RECORDINGS_HOME/state/state.db"
   cp "$R/config/config.toml" config.toml
   cp "$R/backup-extra/deploy.env" docker/deploy.env
   ```
   Run only the lines for what you lost. If your own `config.toml` or `deploy.env` survived,
   compare it with the restored one (`diff`) rather than overwrite it.

4. **Rebuild what's derived, check, start.** `reindex` adopts every `recording.json` as it now
   stands, so files written after the snapshot's `state.db` copy aren't refused as edited
   outside the app, and it rebuilds `index.db`.
   ```bash
   rc build   # a new server: once deploy.env is back, because its UID and GID are build args
   rc run --rm web recordings reindex
   rc run --rm web recordings validate /archive --deep
   rc up -d
   rc exec web recordings doctor --role web
   rc exec backup recordings doctor --role backup
   ```

5. **One recording, not the whole archive.** Restore just its folder with `--include`, move the
   damaged copy aside (if there is one), move the restored one in, and run `reindex`.
   ```bash
   ID=20261008T143512+0000_0123abcd                             # the recording's ID
   D="recordings/${ID:0:4}/${ID:4:2}/$ID"
   "$RESTIC" -r "$REPO" "${CACERT[@]}" restore latest --host "$WRITER_ID" --tag recordings \
     --include "/archive/$D" --target "$RECORDINGS_HOME/restore-real" --verify
   mv "$RECORDINGS_HOME/archive/$D" "$RECORDINGS_HOME/$ID.lost-$(date +%Y%m%d)"
   mv "$RECORDINGS_HOME/restore-real/archive/$D" "$RECORDINGS_HOME/archive/$D"
   rc run --rm web recordings reindex
   ```

6. **`state.db` lost, and its backup copy unusable.** Move the damaged `state/` aside, then
   recreate `state.db` from the archive's sentinel. Each file's merge base is adopted on its next
   write.
   ```bash
   rc down
   mv "$RECORDINGS_HOME/state" "$RECORDINGS_HOME/state.lost-$(date +%Y%m%d)"
   mkdir -m 700 "$RECORDINGS_HOME/state"
   rc run --rm web recordings init --adopt
   rc run --rm web recordings reindex
   rc up -d
   ```

7. **Clean up** once `validate` is clean and the next backup has passed: delete
   `$RECORDINGS_HOME/restore-real` and the `.lost-` folders. A new server also installs the boot
   unit (2.5).

## Checklist

| Fact | Answer |
|---|---|
| The archive's mount (1a.1) | |
| Filesystem, for the archive and `state/` (1a.2) | local: ext4 / xfs / btrfs |
| Free space (1a.3) | |
| UID and GID (1a.4) | |
| `$RECORDINGS_HOME`, `state/` and `secrets/` are 700 (1a.4) | yes / no |
| Hard links and `flock` (1a.5) | |
| Docker starts after Tailscale (1a.6); `tailscale0` exists | 0 / 1; yes / no |
| The NAS's machine type; Container Manager (1b.1) | x86_64 / aarch64; yes / no |
| `NAS_HOST` resolves by DNS (1b.1) | yes / no |
| rest-server runs in (1b.5) | Container Manager / Task Scheduler |
| rest-server's password travels by (1b.4) | TLS with `[backup] cacert` / the tailnet |
| Append-only proven: `forget` refused with 403 (1b.6) | yes / no |
| rest-server is back after a NAS reboot (1b.5) | yes / no |
| The NAS's weekly retention task, as root (1b.7) | set up; first run ended normally (2.6) |
| The mirror account can rsync into its share (1b.8) | yes / no |
| The mirror needs `--no-perms` (1b.8) | yes / no |
| The NAS's free space over sftp's `df` (1b.8) | works / unknown |
| NAS transport (1b) | rest-server / SFTP fallback, risk accepted |
| The tailnet ACL refuses another device (2.3) | yes / no |
| Dead-man's switch: Healthchecks.io, period 1 h, grace 3 h (2.2) | |
| ntfy topic and ping URL in the password manager (2.2) | yes / no |
| `recordings.service` brings the app up after a reboot (2.5) | yes / no |
| The killed backup raised the alert (2.7) | yes / no |
| The restore test passed on the imported data; `import/` deleted (4) | yes / no |
| The Library's first load (4) | under 2 s / slower |
````

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run pytest packages/core/tests/test_runbook.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/first-run.md packages/core/tests/test_runbook.py
git commit -m "docs: the first-run runbook for stage 2a (spec §19 steps 1-4, and a real restore)

rest-server --append-only on the NAS, with retention as a NAS task, because the NAS is on
ext4 with no snapshots. The spike fetches its own pinned restic and rest-server. The loop is
stopped around manual runs, the imported data is restore-tested before import/ goes, and
Restoring for real puts each piece back.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Durable writes, the archive sentinel, `state.db` and `recordings init`

**Checkpoint lens:** data integrity and write safety.

**Files:**
- Create: `packages/core/src/recordings/files.py`, `sentinel.py`, `state.py`, `init.py`
- Create: `packages/core/tests/conftest.py`, `test_files.py`, `test_init.py`
- Modify: `packages/core/src/recordings/archive.py` (writes use `files`), `selfdoc.py` (imports, `validate` checks the sentinel), `config.py` (`writer_id`, `[state]`, `[index]`, `base_url`), `cli.py` (`init`, `init --adopt`; `docs` refuses without a sentinel)
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
    - `State.create(path, *, archive_uuid: str, writer_id: str) -> State`, the only code that creates a `state.db` (SQLite `mode=rwc`). Of two racing creates, one wins and the other raises `StateError("… already exists …")`.
    - `State.open(path) -> State`. It raises `StateError` when `state.db` is missing ("not the archive's writer"), damaged (any `sqlite3.DatabaseError`; the message points to the runbook's "Restoring for real" and to `init --adopt`), or newer than the code. It runs the migrations an older one hasn't had.
    - `State.db()`, a context manager yielding a `sqlite3.Connection`. It opens with `mode=rw`, so a missing `state.db` is an error, never a new, empty file.
    - `State._connect(mode: str) -> sqlite3.Connection`: private, the one place a connection is made. Task 15 passes `"ro"`.
    - `State.meta(key) -> str | None` and `State.set_meta(key, value) -> None`
    - Module constants `SCHEMA` (the version-1 tables), `SCHEMA_VERSION = 1`, `MIGRATIONS: dict[int, tuple[str, ...]] = {}` (numbered from 2) and `RESTORE_HINT`.
  - **`recordings.init`:**
    - `class InitError(RuntimeError)`
    - `WRITER_ID_PATTERN = r"[a-z0-9][a-z0-9_.-]{0,62}"`
    - `init_archive(archive_root: Path, state_path: Path, *, writer_id: str, archive_uuid: str | None = None, created_at: datetime | None = None) -> ArchiveIdentity`
    - `adopt_archive(archive_root: Path, state_path: Path, *, writer_id: str) -> ArchiveIdentity`: recreates a lost `state.db` from the archive's sentinel, and refuses when `state.db` exists. Each file's merge base is adopted the next time the app writes it (Task 5b's `_check_fresh`).
  - **`recordings.config.Config`** gains `writer_id: str | None`, `state_path: Path | None` and `index_path: Path | None`, and loses `writer_host`. `RECORDINGS_STATE` and `RECORDINGS_INDEX` override the file.
  - **CLI:** `recordings init [--adopt] [--json]`. Its JSON is `{"archive", "uuid", "writer_id", "adopted"}`.
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
               # The recording's entry, and the month and year folders' entries when they are new.
               for folder in (final.parent, final.parent.parent, final.parent.parent.parent):
                   fsync_dir(folder)
   ```
   Task 5b's `Writer._create` replaces this method and keeps the same three fsyncs.
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
import sqlite3
from datetime import datetime, timezone

import pytest

from recordings import init as init_module
from recordings import state as state_module
from recordings.cli import main
from recordings.init import InitError, adopt_archive, init_archive
from recordings.selfdoc import validate
from recordings.sentinel import SENTINEL, ArchiveIdentity, SentinelError, read_sentinel, write_sentinel
from recordings.state import MIGRATIONS, SCHEMA_VERSION, State, StateError

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


@pytest.mark.parametrize("content", [b"not a database " * 100, b""], ids=["garbage", "no-tables"])
def test_a_damaged_state_db_is_refused_with_the_way_back(tmp_path, content):
    # why: a bare sqlite3.DatabaseError would reach the CLI as a traceback, with no way forward.
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "state.db").write_bytes(content)
    with pytest.raises(StateError, match="Restoring for real"):
        State.open(tmp_path / "state")


def test_state_never_creates_a_state_db_by_accident(tmp_path):
    # why: a plain connect makes an empty file, which later passes for a state.db.
    (tmp_path / "state").mkdir()
    with pytest.raises(sqlite3.OperationalError):
        State(tmp_path / "state").meta("writer_id")
    assert not (tmp_path / "state" / "state.db").exists()


def test_two_inits_racing_for_one_state_folder_fail_cleanly(tmp_path, monkeypatch):
    # why: both can pass the exists() check. The loser gets a StateError, not an IntegrityError.
    real_connect = State._connect

    def rival_wins(self, mode):
        monkeypatch.setattr(State, "_connect", real_connect)
        State.create(self.path, archive_uuid=UUID, writer_id="rival")
        return real_connect(self, mode)

    monkeypatch.setattr(State, "_connect", rival_wins)
    with pytest.raises(StateError, match="already exists"):
        State.create(tmp_path / "state", archive_uuid=UUID, writer_id="test")
    assert State.open(tmp_path / "state").meta("writer_id") == "rival"


def test_a_state_db_newer_than_the_code_is_refused(tmp_path):
    # why: a rollback after a deploy. Older code would misread tables it doesn't know.
    st = State.create(tmp_path / "state", archive_uuid=UUID, writer_id="test")
    st.set_meta("schema_version", str(SCHEMA_VERSION + 1))
    with pytest.raises(StateError, match="newer"):
        State.open(tmp_path / "state")
    assert st.meta("schema_version") == str(SCHEMA_VERSION + 1)


def test_open_runs_the_numbered_migrations_in_order(tmp_path, monkeypatch):
    # why: CREATE TABLE IF NOT EXISTS can't add a column, so a later stage changes a table this way.
    State.create(tmp_path / "state", archive_uuid=UUID, writer_id="test")
    monkeypatch.setattr(state_module, "MIGRATIONS", {
        2: ("CREATE TABLE extra (x TEXT)",), 3: ("ALTER TABLE extra ADD COLUMN y TEXT",)})
    monkeypatch.setattr(state_module, "SCHEMA_VERSION", 3)
    st = State.open(tmp_path / "state")
    State.open(tmp_path / "state")  # a second open finds nothing left to do
    assert st.meta("schema_version") == "3"
    with st.db() as db:
        assert [row[1] for row in db.execute("PRAGMA table_info(extra)")] == ["x", "y"]


def test_the_migrations_are_numbered_from_2_without_gaps():
    assert sorted(MIGRATIONS) == list(range(2, SCHEMA_VERSION + 1))


def test_an_interrupted_init_writes_no_sentinel_and_never_says_remove(tmp_path, monkeypatch):
    # why: "Remove <archive>" is one wrong path away from deleting a real archive.
    def failing_docs(root):
        raise OSError("Simulated disk full")

    monkeypatch.setattr(init_module, "write_docs", failing_docs)
    with pytest.raises(InitError) as caught:
        init_archive(tmp_path / "archive", tmp_path / "state", writer_id="test")
    assert "move both aside" in str(caught.value) and "Remove" not in str(caught.value)
    assert not (tmp_path / "archive" / SENTINEL).exists()


def test_adopt_recreates_a_lost_state_db_from_the_sentinel(new_archive):
    # why: §6.8. Without it, a lost state.db with no usable backup leaves the archive unwritable.
    root, state = new_archive
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    for path in state.glob("state.db*"):
        path.unlink()
    identity = adopt_archive(root, state, writer_id="test")
    assert identity == read_sentinel(root)
    st = State.open(state)
    assert (st.meta("archive_uuid"), st.meta("writer_id")) == (identity.uuid, "test")
    assert sorted(p.relative_to(root) for p in root.rglob("*")) == before  # archive untouched


def test_adopt_never_replaces_a_state_db(new_archive):
    root, state = new_archive
    with pytest.raises(InitError, match="move it aside"):
        adopt_archive(root, state, writer_id="test")


def test_adopt_needs_the_archives_sentinel(tmp_path):
    (tmp_path / "archive").mkdir()
    with pytest.raises(InitError, match="mounted"):
        adopt_archive(tmp_path / "archive", tmp_path / "state", writer_id="test")
    assert not (tmp_path / "state").exists()


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


def test_cli_init_adopt_recreates_a_lost_state_db(tmp_path, monkeypatch, capsys):
    _config(tmp_path, monkeypatch)
    assert main(["init", "--json"]) == 0
    uuid = json.loads(capsys.readouterr().out)["uuid"]
    for path in (tmp_path / "state").glob("state.db*"):
        path.unlink()
    assert main(["init", "--adopt", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["uuid"], out["writer_id"], out["adopted"]) == (uuid, "homelab", True)
    assert main(["init", "--adopt", "--json"]) == 1
    assert "exists" in json.loads(capsys.readouterr().out)["error"]


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
Expected: pytest stops before running anything, with
`ImportError while loading conftest '…/packages/core/tests/conftest.py'` and
`E   ModuleNotFoundError: No module named 'recordings.init'`. That error hides the new
`test_config.py` tests, which would otherwise fail on `Config` having no `writer_id`.

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
and the mirror never copy it. It must be on a local disk: `flock` and SQLite's WAL both break over
NFS and SMB. The backup copies `state.db` on its own, with SQLite's backup command (§15.1). Reads
never need it: the Mac's read-only mirror has none.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

SCHEMA_VERSION = 1
# The version-1 tables. Later tasks in stage 2a append theirs here. Every statement is
# CREATE ... IF NOT EXISTS, so opening a state.db made earlier in 2a adds what is missing.
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
# Every change after version 1, keyed by the version it makes: MIGRATIONS[2] takes a state.db
# from version 1 to 2. CREATE TABLE IF NOT EXISTS can't add a column, so a later stage that changes
# a table adds its statements here and raises SCHEMA_VERSION to match. Never edit a shipped one.
MIGRATIONS: dict[int, tuple[str, ...]] = {}

RESTORE_HINT = (
    "restore state.db from the backup (runbook: Restoring for real). If no backup copy is "
    "usable, move it aside and run `recordings init --adopt`.")


class StateError(RuntimeError):
    pass


def _already(db_path: Path) -> str:
    return f"{db_path} already exists: `recordings init` runs once per archive"


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
        """Make state.db, once. Of two inits racing for one folder, one wins and one is refused:
        the meta rows go in as one transaction, and a second set breaks their primary key."""
        state = cls(path)
        if state.db_path.exists():
            raise StateError(_already(state.db_path))
        state.path.mkdir(parents=True, exist_ok=True)
        state.locks_dir.mkdir(exist_ok=True)
        try:
            with closing(state._connect("rwc")) as conn:  # the one place a state.db is made
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute("PRAGMA journal_mode=WAL")
                with conn:
                    conn.executescript(SCHEMA)
                    conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", [
                        ("schema_version", "1"),
                        ("archive_uuid", archive_uuid),
                        ("writer_id", writer_id),
                    ])
                state._upgrade(conn)
        except sqlite3.IntegrityError:  # another init got there between the check and the insert
            raise StateError(_already(state.db_path)) from None
        except sqlite3.DatabaseError as exc:
            raise StateError(f"{state.db_path} could not be created ({exc})") from None
        return state

    @classmethod
    def open(cls, path: Path) -> State:
        state = cls(path)
        if not state.db_path.is_file():
            raise StateError(
                f"no state.db in {state.path}, so this machine is not the archive's writer. "
                "`recordings init` creates it, once, on the writer.")
        try:
            with state.db() as db:
                state._version(db)  # refuse a newer state.db before changing anything in it
                db.executescript(SCHEMA)
                state._upgrade(db)
        except sqlite3.DatabaseError as exc:
            raise StateError(
                f"{state.db_path} can't be read ({exc}). If it is damaged, {RESTORE_HINT}"
                ) from None
        state.locks_dir.mkdir(exist_ok=True)
        return state

    def _connect(self, mode: str) -> sqlite3.Connection:
        """A URI with an explicit mode: `rw` never creates a missing file, as a plain path would."""
        uri = f"{self.db_path.absolute().as_uri()}?mode={mode}"
        return sqlite3.connect(uri, uri=True, timeout=30)

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        """One connection per use: committed on success, rolled back on error, always closed.

        It opens an existing state.db only: a missing one is an error, never a new, empty file.
        """
        with closing(self._connect("rw")) as conn:
            conn.execute("PRAGMA synchronous=FULL")
            with conn:
                yield conn

    # ---- the schema version: refuse a newer state.db, migrate an older one ------------------
    def _version(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        try:
            version = int(row[0])
        except (TypeError, ValueError):
            raise StateError(f"{self.db_path} has no readable schema_version, so it is damaged: "
                             f"{RESTORE_HINT}") from None
        if version > SCHEMA_VERSION:
            raise StateError(
                f"{self.db_path} is at schema version {version}, newer than this release's "
                f"{SCHEMA_VERSION}: a newer release wrote it, and this one can't read it. Deploy "
                "that release again.")
        return version

    def _upgrade(self, conn: sqlite3.Connection) -> None:
        """Run the numbered migrations this state.db hasn't had, in order, in one transaction."""
        if self._version(conn) == SCHEMA_VERSION:
            return
        with conn:
            conn.execute("BEGIN IMMEDIATE")  # one process migrates; one that waited finds it done
            for n in range(self._version(conn) + 1, SCHEMA_VERSION + 1):
                for statement in MIGRATIONS[n]:
                    conn.execute(statement)
            conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'",
                         (str(SCHEMA_VERSION),))

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

`recordings init --adopt` is the way back when state.db is lost and no backup copy is usable
(§6.8). It recreates state.db for an existing archive from the archive's own sentinel, and writes
nothing into the archive. The merge bases went with the old state.db, so the app adopts each
recording.json as it stands the next time it writes it.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from recordings.selfdoc import write_docs
from recordings.sentinel import (
    SENTINEL,
    ArchiveIdentity,
    SentinelError,
    read_sentinel,
    write_sentinel,
)
from recordings.state import State

WRITER_ID_PATTERN = r"[a-z0-9][a-z0-9_.-]{0,62}"


class InitError(RuntimeError):
    pass


def _check_writer_id(writer_id: str) -> None:
    if not re.fullmatch(WRITER_ID_PATTERN, writer_id or ""):
        raise InitError(f"writer_id {writer_id!r} must be a short lowercase name, such as homelab")


def init_archive(archive_root: Path, state_path: Path, *, writer_id: str,
                 archive_uuid: str | None = None, created_at: datetime | None = None,
                 ) -> ArchiveIdentity:
    root, state_dir = Path(archive_root), Path(state_path)
    _check_writer_id(writer_id)
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
        # Never "remove": a wrong path in that advice could cost a real archive.
        raise InitError(
            f"init stopped part-way ({exc}) and wrote no {SENTINEL}. Check that {root} and "
            f"{state_dir / 'state.db'} hold only what this init just wrote, then move both "
            "aside and run init again.") from None
    return identity


def adopt_archive(archive_root: Path, state_path: Path, *, writer_id: str) -> ArchiveIdentity:
    """Recreate a lost state.db for an existing archive, from its sentinel (§6.8)."""
    root, state_dir = Path(archive_root), Path(state_path)
    _check_writer_id(writer_id)
    try:
        identity = read_sentinel(root)
    except SentinelError as exc:
        raise InitError(str(exc)) from None
    if (state_dir / "state.db").exists():
        raise InitError(
            f"{state_dir / 'state.db'} exists: --adopt only replaces a lost one. If it is damaged "
            "and no backup copy is usable, move it aside first.")
    try:
        State.create(state_dir, archive_uuid=identity.uuid, writer_id=writer_id)
    except OSError as exc:
        raise InitError(f"--adopt stopped ({exc}); it changed nothing in {root}") from None
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
   from recordings.init import InitError, adopt_archive, init_archive
   from recordings.sentinel import SentinelError, read_sentinel
   from recordings.state import StateError
   ```
2. **Add** to `build_parser`, before the `schemas` parser:
   ```python
       p = sub.add_parser("init", help="set up a new, empty archive, once: its sentinel and state.db")
       p.add_argument("--adopt", action="store_true",
                      help="recreate a lost state.db for the existing archive, from its archive.json")
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
       setup = adopt_archive if args.adopt else init_archive
       try:
           identity = setup(cfg.archive_path, cfg.state_path, writer_id=cfg.writer_id)
       except (InitError, StateError) as exc:
           return _fail(str(exc), args.json, 1)
       _emit({"archive": str(cfg.archive_path), "uuid": identity.uuid,
              "writer_id": cfg.writer_id, "adopted": args.adopt}, args.json)
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

In `config.example.toml`, make two replacements:
1. In `[archive]`, **replace** the `writer_host` lines:
   ```toml
   # The only machine allowed to write the archive (stage 2): that machine's hostname.
   writer_host = "my-homelab"
   ```
   with:
   ```toml
   # The archive's one writer (spec §3): a short name for the homelab server. It is never read from a
   # host name, because a container's host name isn't the server's. `recordings init` records it in
   # state.db, and only a machine whose config and state.db both name it may write.
   writer_id = "homelab"
   ```
2. **Delete** the old `[index]` section, which sits right after `[server]`:
   ```toml
   [index]
   # SQLite index and job queue (stage 3), on the same local disk as the archive.
   path = "/data/index.sqlite"
   ```
   and, in its place, after `[server]` and before `[plaud]`, **add**:
   ```toml
   [state]                                 # stage 2a: state.db and the lock files (spec §6.8)
   # Outside the archive, on a local disk: flock and SQLite's WAL don't work over NFS or SMB. In
   # Docker it is /state, and docker/compose.yml sets RECORDINGS_STATE=/state, which overrides it.
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

fsync the file and its folder on every write (stage-1 carry-over). state.db opens with mode=rw,
refuses a damaged or newer file with the way back, and migrates by number. init --adopt
recreates a lost state.db from the sentinel. Checked: docs.python.org sqlite3 (URI mode=rw,
connection handling, PRAGMA journal_mode) and os.fsync/os.link.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 3: The format change: `rev`, the `speakers` shape and Plaud's segment fields

**Checkpoint lens:** data integrity and write safety.

The real archive is still empty, so the format changes now, inside `recordings-archive@1`, with
every new key optional (§22, decision 12). No dual reader is needed.

An empty `speakers` keeps serialising as `{}` through pydantic's `exclude_if`. The Pages demo runs
Pyodide 0.27.7, whose pydantic 2.10.5 has no `exclude_if` and ignores it with a warning, so there
an empty block would dump as `{"labels": {}, "spans": []}`. That is harmless only because the
browser reads models and never serialises them; keep it that way (decision 7).

**Files:**
- Modify: `packages/core/src/recordings/models.py` (whole file below), `archive.py` (`RawSource`, `_write_raw`), `selfdoc.py` (`validate`)
- Modify: `packages/core/src/recordings/format/FORMAT.md`, `format/AGENTS.md`, `format/schemas/*.json` (regenerated)
- Modify: `packages/ui/src/recordings_ui/views.py` (`_turns`)
- Modify: `demo/archive/` (rebuilt)
- Test: `packages/core/tests/test_models.py`, `test_selfdoc.py`, `test_archive.py`; `packages/ui/tests/test_views.py`

**Interfaces:**
- Consumes: `recordings.files.publish_exclusive`, and `recordings.sentinel` (Task 2).
- Produces:
  - **New constants** in `recordings.models`: `SHA256_PATTERN`, `PERSON_SLUG`, `UNKNOWN_PERSON = "unknown"`, and the types `PersonSlug` and `TitleBy = Literal["you", "plaud", "recorder", "pocket"]`.
  - **New models:** `PlaudState(title_seen: str | None, acknowledged_up_to: str | None)`, `SpeakerLabel(person, by, plaud_name_seen, not_)` (alias `not`), `SpeakerSpan(start, end, person, by)` and `Speakers(source, labels, spans)`.
  - **`Recording`** gains `rev: int | None`, `title_by: Literal["you", "plaud", "recorder", "pocket"] | None` (spelled `TitleBy | None`) and `plaud: PlaudState | None`. Its `speakers` becomes a `Speakers`. `title_by` is `you`, or the source kind that supplied the title (§6.3). Task 5b's `Incoming.title_by` uses the same `TitleBy | None`.
  - **Every model** (`_Model`'s config) gains `hide_input_in_errors=True`: a `ValidationError`'s text never carries the input, such as a title or transcript text.
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


@pytest.mark.parametrize("who", ["you", "plaud", "recorder", "pocket"])
def test_title_by_is_you_or_the_source_kind(who):
    # why: §6.3. The import sets title_by to the item's source kind, not only plaud.
    assert recording(title_by=who).title_by == who


def test_a_validation_error_never_echoes_the_input():
    # why: the import reports a failed row's error. A title or transcript text must never reach a
    # report or an alert, so pydantic leaves the input out of its messages.
    with pytest.raises(ValidationError) as caught:
        recording(title_by="会議メモ / Q&A")
    assert "会議メモ" not in str(caught.value)
    with pytest.raises(ValidationError) as caught:
        Rendition(kind="transcript", engine="plaud", version="plaud@1",
                  created_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
                  payload={"segments": [{"start": 0, "text": "Ada said something private."}]})
    assert "something private" not in str(caught.value)


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
                   "`fetched_at`", "`title_by`", "`recorder`", "acknowledged_up_to",
                   "only appends to `my-notes.md`"):
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
other new tests fail on the missing fields and checks. Stage 1's messages carry the input
(`input_value='会議メモ / Q&A'`), which `test_a_validation_error_never_echoes_the_input` refuses.

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
# Who set a recording's title (§6.3): you, or the source kind that supplied it. Stage 5 adds
# "upload" and "url".
TitleBy = Literal["you", "plaud", "recorder", "pocket"]
PersonSlug = Annotated[str, Field(pattern=PERSON_SLUG)]


def _empty(value: object) -> bool:
    # For exclude_if. The Pages demo's pydantic 2.10.5 ignores exclude_if, so the browser only
    # reads models and never serialises them (decision 7).
    return not value


class _Model(BaseModel):
    # forbid: a typo in a hand-edited file is an error, not a silently ignored key (§11).
    # hide_input_in_errors: a validation error names the field, never its value, so a title or
    # transcript text can't leak into a report, a log or an alert.
    model_config = ConfigDict(extra="forbid", populate_by_name=True, hide_input_in_errors=True)


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
    title_by: TitleBy | None = None  # absent: not recorded; Plaud's sync treats it as "plaud"
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
- `title_by`: who set `title`: `you`, or the source kind that supplied it (`plaud`, `recorder` or
  `pocket`). Plaud's sync replaces the title only while `title_by` is `plaud` or absent; for any
  other value it shows Plaud's new title as a difference instead.
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
  renames it into place. The app changes the structured ones (`recording.json`, and later
  `tags.yaml` and the people files) only through `mutate`, under that file's lock. It only
  appends to `my-notes.md`, under the recording's lock: `my-notes.md` is free Markdown with no
  `rev`.
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
Field(exclude_if=...), checked; the Pages demo's 2.10.5 lacks it, and never serialises). title_by
takes you or the source kind. Validation errors never carry their input. validate now checks
media, source files, spans and the outputs of a broken recording.json.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The derived index (`index.db`)

**Checkpoint lens:** data integrity and write safety.

**Files:**
- Create: `packages/core/src/recordings/refs.py`, `packages/core/src/recordings/index.py`
- Test: `packages/core/tests/test_index.py`

**Interfaces:**
- Consumes: `Archive.iter_recordings()`, `Archive.problems`, and `recordings.ids.relative_dir`.
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

    - `rebuild` runs as one `BEGIN IMMEDIATE` transaction on the live file, so a failed rebuild leaves the old index and no temp file. A file SQLite can't read (`SQLITE_NOTADB` or `SQLITE_CORRUPT`) is deleted and rebuilt. A database that isn't an index raises `IndexRefused`, and so does emptying a non-empty index without `allow_empty`.
    - A missing or unreadable index answers every lookup with nothing.

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
from recordings.state import State

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


def test_a_failed_rebuild_leaves_the_old_index_and_nothing_else(new_archive, tmp_path, monkeypatch):
    # why: the failure comes after the old tables are dropped, inside the one transaction.
    root, _ = new_archive
    rec = put(root)
    index = Index(tmp_path / "index.db")
    index.rebuild(Archive(root), archive_uuid="u-1")
    put(root, when="2026-10-07T09:00:00-07:00", sha="b" * 64)

    def failing_insert(db, recording):
        raise OSError("Simulated disk full")

    monkeypatch.setattr(index_module, "_insert", failing_insert)
    with pytest.raises(OSError, match="Simulated disk full"):
        index.rebuild(Archive(root), archive_uuid="u-1")
    assert index.count() == 1 and index.find_by_sha256(rec.media.sha256) == rec.id
    assert sorted(p.name for p in tmp_path.iterdir()) == ["archive", "index.db", "state"]


def test_a_garbage_index_is_rebuilt(new_archive, tmp_path):
    # why: §6.8. index.db is derived, so a file SQLite can't read is thrown away and rebuilt.
    root, _ = new_archive
    rec = put(root, sources=[("plaud", HEX)])
    index = Index(tmp_path / "index.db")
    index.path.write_bytes(b"not a database " * 100)
    assert (index.count(), index.archive_uuid()) == (0, None)
    assert index.find_by_source("plaud", HEX) == []
    assert index.rebuild(Archive(root), archive_uuid="u-1") == 1
    assert index.find_by_sha256(rec.media.sha256) == rec.id
    assert index.find_by_source("plaud", HEX) == [rec.id]


def test_rebuild_never_replaces_a_database_that_is_not_an_index(new_archive, tmp_path):
    # why: the rebuild drops its tables in place. A mistyped [index] path must not take state.db's
    # tables with it.
    root, state = new_archive
    put(root)
    with pytest.raises(IndexRefused, match="not an index"):
        Index(state / "state.db").rebuild(Archive(root), archive_uuid="u-1")
    assert State.open(state).meta("writer_id") == "test"


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
Expected: FAIL with `ImportError: cannot import name 'index' from 'recordings'`.

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
none is far more likely unmounted than empty. It rebuilds inside one transaction on the live file,
so a failed rebuild leaves the old index as it was and nothing beside it, and readers see the old
index until the new one commits. A file SQLite can't read at all is thrown away first.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from recordings.archive import Archive
from recordings.ids import relative_dir
from recordings.models import Recording
from recordings.refs import canonical_ref

INDEX_VERSION = 1
_TABLES = ("meta", "recordings", "source_refs")  # dropping a table drops its indexes too
# One statement each: they run inside rebuild's transaction, which executescript would commit.
_SCHEMA = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE recordings ("
    " id TEXT PRIMARY KEY, folder TEXT NOT NULL, sha256 TEXT NOT NULL, rev INTEGER NOT NULL)",
    "CREATE INDEX recordings_by_sha256 ON recordings (sha256)",
    "CREATE TABLE source_refs ("
    " recording_id TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, canonical TEXT NOT NULL,"
    " PRIMARY KEY (recording_id, kind, ref))",
    "CREATE INDEX source_refs_by_canonical ON source_refs (kind, canonical)",
)
_UNREADABLE = ("SQLITE_NOTADB", "SQLITE_CORRUPT")


class IndexRefused(RuntimeError):
    """A rebuild that would empty a non-empty index, or replace a database that isn't one."""


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
    def _db(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            with conn:
                yield conn

    def exists(self) -> bool:
        return self.path.is_file()

    def _rows(self, sql: str, args: tuple = ()) -> list[tuple]:
        if not self.exists():
            return []
        try:
            with self._db() as db:
                return db.execute(sql, args).fetchall()
        except sqlite3.DatabaseError:  # a damaged or foreign file answers nothing; rebuild it
            return []

    def _one(self, sql: str, args: tuple = ()) -> object | None:
        rows = self._rows(sql, args)
        return rows[0][0] if rows else None

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
        if self.exists() and not self._readable():
            for suffix in ("", "-journal", "-wal", "-shm"):  # derived (§6.8): start again
                Path(f"{self.path}{suffix}").unlink(missing_ok=True)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")  # one writer; readers keep the old index until commit
            self._check_ours(db)
            for table in _TABLES:
                db.execute(f"DROP TABLE IF EXISTS {table}")
            for statement in _SCHEMA:
                db.execute(statement)
            db.executemany("INSERT INTO meta (key, value) VALUES (?, ?)",
                           [("index_version", str(INDEX_VERSION)), ("archive_uuid", archive_uuid)])
            for rec in recordings:
                _insert(db, rec)
        return len(recordings)

    def _readable(self) -> bool:
        """False only when SQLite can't read the file at all; a busy file is still an error."""
        try:
            with self._db() as db:
                return db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        except sqlite3.DatabaseError as exc:
            if exc.sqlite_errorname in _UNREADABLE:
                return False
            raise

    def _check_ours(self, db: sqlite3.Connection) -> None:
        tables = {row[0] for row in
                  db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        ours = "meta" in tables and db.execute(
            "SELECT 1 FROM meta WHERE key = 'index_version'").fetchone() is not None
        if tables and not ours:
            raise IndexRefused(
                f"{self.path} is a database but not an index, so rebuild won't replace it. "
                "Check [index] path.")

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
        rows = self._rows(
            "SELECT DISTINCT recording_id FROM source_refs WHERE kind = ? AND canonical = ? "
            "ORDER BY recording_id", (kind, canonical_ref(kind, ref)))
        return [row[0] for row in rows]
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_index.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/recordings/refs.py packages/core/src/recordings/index.py packages/core/tests/test_index.py
git commit -m "feat(core): the derived index.db, with lookups by hash and either Plaud ID form

rebuild refuses to empty a non-empty index (spec §6.7) and runs as one BEGIN IMMEDIATE
transaction on the live file, so a failed rebuild leaves the old index and no debris. A file
SQLite can't read is rebuilt; a database that isn't an index is refused. Checked:
docs.python.org sqlite3 (transaction control, sqlite_errorname).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 5a: Locks for the archive's editable files

**Checkpoint lens:** data integrity and write safety.

Task 5 is §6.4's write contract, built in three parts so each one ends green:
- **5a** adds the locks.
- **5b** adds the locked write path (`Writer`, `mutate`, the merge bases) beside stage 1's writers.
- **5c** moves every caller onto the `Writer`, makes `Archive` read-only and adds
  `recordings reindex`.

Stage 1's writers (`add_recording`, `write_rendition` and `_merge_source`, the duplicate merge
included) move onto the `Writer` in 5c.

**Files:**
- Create: `packages/core/src/recordings/locks.py`
- Test: `packages/core/tests/test_locks.py`

**Interfaces:**
- Consumes: `recordings.ids.ID_RE` (stage 1).
- Produces, in **`recordings.locks`:**
  - `class LockTimeout(RuntimeError)`, `class LockOrderError(RuntimeError)`
  - `PEOPLE_FILES = ("people.yaml", "people.private.yaml")`
  - `OPS_LOCK = "ops"`: the key a bulk job holds (the import in Task 9, `ops.run_job` in Task 11), on its own `Locks` instance so it never mixes with a writer's lock order
  - `lock_order(key: str) -> tuple[int, int, str]`
  - `class Locks(folder: Path, *, timeout: float = 30.0)`, with `hold(*keys)`, a context manager

**Done when:** the full suite passes, with 5 more passed than after Task 4. Record the count in
the execution ledger.

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
Expected: a collection error, `ModuleNotFoundError: No module named 'recordings.locks'`.

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
OPS_LOCK = "ops"  # one bulk job at a time: the import (Task 9) and ops.run_job (Task 11)
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

- [ ] **Step 5: Run every test**

Run: `uv run pytest`
Expected: every test passes, 5 more than after Task 4. Record the count in the ledger.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/recordings/locks.py packages/core/tests/test_locks.py
git commit -m "feat(core): flock locks for the archive's editable files, in one fixed order

People files, then tags.yaml, then sha-<hash> keys, then recordings by ID: two multi-file
operations can never deadlock, and the kernel releases a lock when its process dies (spec §6.4,
§6.8). Checked: docs.python.org fcntl (flock, LOCK_NB errno).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5b: The locked write path: `Writer`, `mutate` and the merge bases

**Checkpoint lens:** data integrity and write safety.

The `Writer` arrives beside stage 1's writers. `Archive.add_recording` and its helpers stay until
Task 5c, so the UI, the demo build and the older tests keep working. That works because
`writer.py` imports only names that already exist.

Three orderings matter here:
- **Privacy first (§7.4).** A private tag goes in `Incoming.tags`, so a new recording is private
  from the moment it exists. When a duplicate brings one, `_merge` applies it as its first
  `mutate`, before it publishes anything.
- **A write that landed stays landed.** If the index can't be updated after the commit, the
  write still succeeds and leaves its `index_pending` mark. `add` finishes pending marks before it
  looks for a duplicate.
- **A re-run never duplicates a snapshot.** `_publish_raw` reuses a `source/` file whose bytes
  are identical.

**Files:**
- Create: `packages/core/src/recordings/writer.py`
- Modify: `packages/core/src/recordings/state.py` (revisions, flags, pending index updates)
- Modify: `packages/core/tests/conftest.py` (whole file below), `packages/core/tests/test_archive.py` (seven tests retire)
- Test: `packages/core/tests/test_writer.py`

**Interfaces:**
- Consumes:
  - `State`, `StateError`, `RESTORE_HINT` (Task 2)
  - `Index`, `IndexRefused` (Task 4)
  - `read_sentinel`, `SentinelError`, `SENTINEL` (Task 2); `adopt_archive`, `init_archive` (Task 2)
  - from `recordings.files`: `copy_file_synced`, `publish_exclusive`, `write_bytes_atomic`, `write_text_atomic` and `fsync_dir` (Task 2)
  - `canonical_ref` (Task 4)
  - the models, `TitleBy` and `is_private_tag` included (Task 3)
  - `Locks`, `LockTimeout` (Task 5a)
  - `Archive`, `RawSource`, `sha256_file`, `utc_stamp` (stage 1, and Task 3's `RawSource`)
- Produces:
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
    - `@dataclass(frozen=True) Incoming(media, recorded_at, timezone_name, time_source, title, kind, sources: tuple[RawSource, ...], title_by: TitleBy | None = None, duration_ms=None, sample_rate=None, channels=None, tags=(), renditions=(), my_notes=None, excluded_note_types=())`. A private tag goes in `tags`.
    - `@dataclass(frozen=True) Added(recording: Recording, created: bool, sources_added: int, renditions_added: int)`
    - `@dataclass(frozen=True) ReindexReport(recordings: int, adopted: tuple[str, ...], invalid: tuple[str, ...], problems: tuple[dict, ...])`, with `.to_dict()`
    - `rendition_filename(rendition: Rendition) -> str`
    - `class Writer(root: Path, state: State, index: Index, *, writer_id: str)`:

      | Member | Returns |
      |---|---|
      | `root`, `state`, `index`, `archive`, `locks`, `identity`, `writer_id` | (attributes) |
      | `Writer.open(cfg: Config, *, docs: bool = True)` | `Writer` |
      | `write_docs()` | `list[str]` |
      | `add(incoming: Incoming)` | `Added` |
      | `mutate(recording_id: str, op: Op)` | `Recording` |
      | `write_rendition(recording_id: str, rendition: Rendition)` | `str \| None` |
      | `reindex(*, allow_empty: bool = False)` | `ReindexReport` |
      | `rebuild_index(*, allow_empty: bool = False)` | `int` |

      Private helpers that later tasks call or quote: `_rel`, `_load_fresh`, `_check_fresh`,
      `_write_recording`, `_write_rendition_locked`, `_publish_raw`, `_index`, `_catch_up_index`.
  - **Test fixtures** in core `conftest.py`: `make_writer(name="archive", writer_id="test") -> Writer`, `writer`, `make_incoming(content=None, *, name="clip.MP3", **over) -> Incoming`. `new_archive` stays.

**Done when:** the full suite passes, with 23 more passed than after Task 5a: `test_writer.py`'s
30, less the seven `test_archive.py` tests it replaces (Step 6). Record the count in the ledger.

- [ ] **Step 1: Write the failing writer tests**

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
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from recordings import writer as writer_module
from recordings.archive import Archive, RawSource, sha256_file
from recordings.config import load_config
from recordings.index import Index, IndexRefused
from recordings.init import adopt_archive
from recordings.models import Rendition, TagRef, is_private
from recordings.sentinel import SENTINEL
from recordings.state import State
from recordings.writer import (
    AddTags,
    InvalidFile,
    MergeIncoming,
    NotTheWriter,
    StaleFile,
    Writer,
    WriterError,
)

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
NEXT_DAY = datetime.fromisoformat("2026-10-07T09:00:00-07:00")  # a second, different ID


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


def test_a_damaged_state_db_is_not_the_writer_and_says_to_restore_it(writer, tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[archive]\npath = "{writer.root}"\nwriter_id = "test"\n[state]\npath = "{writer.state.path}"\n'
        f'[index]\npath = "{writer.index.path}"\n', encoding="utf-8")
    for leftover in writer.state.path.glob("state.db-*"):  # WAL files: garbage stands alone
        leftover.unlink()
    writer.state.db_path.write_bytes(b"this is not a database\n" * 64)
    with pytest.raises(NotTheWriter, match="(?i)restore state.db from the backup"):
        Writer.open(load_config({"RECORDINGS_CONFIG": str(cfg_file)}))
    with pytest.raises(NotTheWriter, match="(?i)restore state.db from the backup"):
        Writer(writer.root, State(writer.state.path), writer.index, writer_id="test")


def test_after_init_adopt_the_next_write_takes_each_file_as_its_base(writer, make_incoming):
    # why: §6.8. `recordings init --adopt` recreates a lost state.db, which has no merge bases.
    rec = writer.add(make_incoming()).recording
    shutil.rmtree(writer.state.path)
    adopt_archive(writer.root, writer.state.path, writer_id="test")
    fresh = Writer(writer.root, State.open(writer.state.path), writer.index, writer_id="test")
    done = fresh.mutate(rec.id, AddTags((TagRef(tag="talks"),)))
    assert done.rev == 2 and [t.tag for t in done.tags] == ["talks"]


def test_a_writer_never_empties_the_index_when_it_starts(writer, make_incoming, tmp_path):
    # why: §6.7. A pending mark made the next Writer rebuild with allow_empty=True, so an archive
    # that reads as empty (not mounted, or unreadable after a UID change) emptied the index.
    rec = writer.add(make_incoming()).recording
    writer.state.mark_index_pending(rec.id)
    (writer.root / "recordings").rename(tmp_path / "moved-away")
    (writer.root / "recordings").mkdir()
    with pytest.raises(NotTheWriter, match="refusing to empty"):
        Writer(writer.root, writer.state, writer.index, writer_id="test")
    assert writer.index.count() == 1


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


def test_a_private_tag_on_a_duplicate_lands_before_any_of_its_content(writer, make_incoming,
                                                                      monkeypatch):
    # why: §7.4. A merge killed after publishing must never leave private content in a
    # recording that isn't private yet.
    rec = writer.add(make_incoming(b"bytes")).recording

    def killed(folder, source):
        raise KeyboardInterrupt("killed")

    monkeypatch.setattr(Writer, "_publish_raw", staticmethod(killed))
    with pytest.raises(KeyboardInterrupt):
        writer.add(make_incoming(
            b"bytes", tags=(TagRef(tag="private"),), renditions=(notes(),),
            sources=(RawSource(kind="upload", ref="u", added_at=T0, payload=b"{}"),)))
    assert is_private(writer.archive.load(rec.id))


def test_a_merge_killed_before_its_mutate_leaves_no_duplicate_source_on_the_rerun(
        writer, make_incoming, monkeypatch):
    # Review Focus 2: the new snapshot is published, then the process dies before mutate records
    # it. Running the same add again must reuse that file, not publish a `-2` copy.
    writer.add(make_incoming(b"bytes"))
    later = RawSource(kind="plaud", ref="a" * 32, added_at=T0,
                      fetched_at=datetime(2026, 10, 9, tzinfo=timezone.utc), payload=b'{"id": 2}')
    real_mutate = Writer.mutate

    def killed_in_the_merge(self, recording_id, op):
        if isinstance(op, MergeIncoming):
            raise KeyboardInterrupt("killed")
        return real_mutate(self, recording_id, op)

    monkeypatch.setattr(Writer, "mutate", killed_in_the_merge)
    with pytest.raises(KeyboardInterrupt):
        writer.add(make_incoming(b"bytes", sources=(later,)))
    monkeypatch.undo()
    again = writer.add(make_incoming(b"bytes", sources=(later,)))
    folder = writer.archive.path_for(again.recording.id)
    expected = ["plaud-20261008T120000Z.json", "plaud-20261009T000000Z.json"]
    assert sorted(p.name for p in (folder / "source").iterdir()) == expected
    assert [s.raw for s in again.recording.sources] == [f"source/{name}" for name in expected]


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


def test_a_new_year_folder_is_made_durable_in_recordings(writer, make_incoming, monkeypatch):
    # why: §6.4. A new year folder is an entry in recordings/, so recordings/ needs an fsync too.
    synced = []
    real_fsync_dir = writer_module.fsync_dir

    def spy(path):
        synced.append(Path(path))
        real_fsync_dir(path)

    monkeypatch.setattr(writer_module, "fsync_dir", spy)
    writer.add(make_incoming())
    assert writer.root / "recordings" in synced


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


def test_rev_never_goes_backwards_after_a_hand_edit_lowers_it(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    writer.mutate(rec.id, AddTags((TagRef(tag="a"),)))
    writer.mutate(rec.id, AddTags((TagRef(tag="b"),)))  # rev 3
    path = writer.archive.path_for(rec.id) / "recording.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["rev"] = 1
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    writer.reindex()
    assert writer.mutate(rec.id, AddTags((TagRef(tag="c"),))).rev == 4


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


def test_reindex_clears_a_stale_flag_once_the_edit_is_undone(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    path = writer.archive.path_for(rec.id) / "recording.json"
    original = path.read_bytes()
    path.write_bytes(original.replace(b'"Week 4"', b'"Edited"'))
    with pytest.raises(StaleFile):
        writer.mutate(rec.id, AddTags((TagRef(tag="talks"),)))
    path.write_bytes(original)  # the edit is undone by hand
    assert writer.reindex().adopted == ()
    assert writer.state.flags() == []


def test_mutate_refuses_an_invalid_file_and_flags_it(writer, make_incoming):
    rec = writer.add(make_incoming()).recording
    writer.add(make_incoming(recorded_at=NEXT_DAY))
    path = writer.archive.path_for(rec.id) / "recording.json"
    path.write_text("{", encoding="utf-8")
    writer.reindex()  # accepts nothing for this file: it can't be parsed
    assert [f["kind"] for f in writer.state.flags()] == ["invalid"]
    with pytest.raises(InvalidFile) as refused:
        writer.mutate(rec.id, AddTags((TagRef(tag="talks"),)))
    assert "reindex" not in str(refused.value)  # reindex can't help: it flags it invalid again
    assert [f["kind"] for f in writer.state.flags()] == ["invalid"]  # never "stale" as well
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


def test_a_crash_after_a_new_recording_lands_but_before_its_commit_merges_on_the_next_add(
        writer, make_incoming, monkeypatch):
    # Review Focus 1, for add: the folder landed, its revision wasn't committed, and the index
    # never heard of it. The same bytes again, under another ID, merge into it without StaleFile.
    def power_cut(revision_id):
        raise OSError("power cut")

    monkeypatch.setattr(writer.state, "commit", power_cut)
    with pytest.raises(OSError, match="power cut"):
        writer.add(make_incoming(b"same bytes"))
    monkeypatch.undo()
    (folder,) = writer.archive.recording_dirs()
    again = writer.add(make_incoming(b"same bytes", recorded_at=NEXT_DAY, sources=(
        RawSource(kind="upload", ref="u", added_at=T0),)))
    assert not again.created and again.recording.id == folder.name
    assert again.sources_added == 1 and again.recording.rev == 2


def test_an_index_left_behind_by_a_crash_never_causes_a_duplicate(writer, make_incoming):
    first = writer.add(make_incoming(b"bytes")).recording
    writer.index.path.unlink()  # as if the process died after the rename, before the index
    Index(writer.index.path).rebuild(Archive(writer.root / "nowhere"),
                                     archive_uuid=writer.identity.uuid, allow_empty=True)
    again = writer.add(make_incoming(b"bytes", sources=(RawSource(kind="upload", ref="u",
                                                                  added_at=T0),)))
    assert again.recording.id == first.id and not again.created


def test_a_failed_index_update_never_fails_the_write_and_the_next_add_catches_up(
        writer, make_incoming, monkeypatch):
    # The write has landed, so a busy index must not report it as failed. The next add must
    # still find it, or the same bytes under another ID would become a second recording.
    def busy(rec):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(writer.index, "upsert", busy)
    first = writer.add(make_incoming(b"same bytes")).recording
    assert writer.state.index_pending() == [first.id]
    monkeypatch.undo()
    again = writer.add(make_incoming(b"same bytes", recorded_at=NEXT_DAY))
    assert not again.created and again.recording.id == first.id
    assert writer.state.index_pending() == []


def test_an_index_entry_whose_folder_was_deleted_by_hand_is_not_an_error(writer, make_incoming):
    # A KeyError used to escape add() here, and the import doesn't catch KeyError.
    writer.add(make_incoming(b"kept"))
    gone = writer.add(make_incoming(b"deleted")).recording
    shutil.rmtree(writer.archive.path_for(gone.id))
    again = writer.add(make_incoming(b"deleted", recorded_at=NEXT_DAY))
    assert again.created and again.recording.id != gone.id
    assert writer.index.find_by_sha256(gone.media.sha256) == again.recording.id
    assert writer.index.count() == 2


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

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_writer.py -v`
Expected: `ImportError while loading conftest '…/packages/core/tests/conftest.py'`, with
`E   ModuleNotFoundError: No module named 'recordings.writer'`.

- [ ] **Step 3: Add the revisions, flags and pending index updates to `state.db`**

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
3. **Add** after `_already` (below `class StateError`):
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
4. **Add** these methods at the end of `State`, after `set_meta`. Each goes through `db()`, so
   through `_connect("rw")`, and never creates a missing `state.db`:
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

- [ ] **Step 4: Write the `Writer`**

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
copy's sources, outputs, tags and notes. A private tag always lands before the content it
protects (§7.4).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import uuid
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
from recordings.index import Index, IndexRefused
from recordings.locks import Locks, LockTimeout
from recordings.models import (
    SCHEMA_REF,
    MediaInfo,
    Recording,
    Rendition,
    SourceRef,
    TagRef,
    TimeSource,
    TitleBy,
    dump_json,
    is_private_tag,
)
from recordings.refs import canonical_ref
from recordings.selfdoc import write_docs
from recordings.sentinel import SentinelError, read_sentinel
from recordings.state import RESTORE_HINT, State, StateError

INDEX_LOCK = "index"
DOCS_LOCK = "docs"
_UNSAFE = re.compile(r"[^A-Za-z0-9.@+_-]+")
log = logging.getLogger(__name__)


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
    title_by: TitleBy | None = None
    duration_ms: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    # A private tag goes here, never in a later write, so the recording is private from the
    # moment it exists (§7.4).
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
        try:
            archive_uuid, recorded = state.meta("archive_uuid"), state.meta("writer_id")
        except (StateError, sqlite3.DatabaseError) as exc:
            raise NotTheWriter(f"{state.db_path} can't be read ({type(exc).__name__}). If it is "
                               f"damaged, {RESTORE_HINT}") from None
        if archive_uuid != self.identity.uuid:
            raise NotTheWriter(
                f"{state.db_path} belongs to another archive: the archive's UUID changed. "
                "Refusing to write.")
        if recorded != writer_id:
            raise NotTheWriter(
                f"this process's writer_id {writer_id!r} is not the archive's writer "
                f"({recorded!r}); only the writer writes (spec §3)")
        self.state, self.index, self.writer_id = state, index, writer_id
        self.archive = Archive(self.root)  # its own reader, never the UI's
        self.locks = Locks(state.locks_dir)
        self._sweep_tmp()
        foreign = index.archive_uuid() != self.identity.uuid  # new, damaged or another archive's
        if foreign or state.index_pending():
            try:
                # §6.7: only an index that isn't this archive's may start out empty.
                self.rebuild_index(allow_empty=foreign)
            except IndexRefused as exc:
                hint = "" if foreign else (
                    f" A writer can't start with --allow-empty: if the archive really is empty, "
                    f"delete {index.path} (it is derived) and start again.")
                raise NotTheWriter(f"{exc}{hint}") from None

    @classmethod
    def open(cls, cfg: Config, *, docs: bool = True) -> Writer:
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
        if docs:
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
        """Bring the index up to date after a committed write. The write has landed, so a busy or
        damaged index never fails it: the pending mark stays, and the next add, or the next
        Writer, finishes the update."""
        try:
            with self.locks.hold(INDEX_LOCK):
                self.index.upsert(rec)
                self.state.clear_index_pending(rec.id)
        except (LockTimeout, sqlite3.DatabaseError) as exc:
            log.warning("index update for %s left pending (%s)", rec.id, type(exc).__name__)

    def _catch_up_index(self) -> None:
        """Finish the index updates a writer left pending: it crashed, or the index was busy.
        `add` runs this first, so a long-running Writer sees another process's crash before it
        looks for a duplicate. Each recording's lock waits out a write still in progress."""
        for rid in self.state.index_pending():
            with self.locks.hold(rid):
                try:
                    raw = (self.root / self._rel(rid)).read_bytes()
                except FileNotFoundError:  # its assembly never landed: nothing to index
                    self.state.clear_index_pending(rid)
                    continue
                try:
                    rec = Recording.model_validate_json(raw)
                except ValidationError:
                    continue  # edited into an invalid file: `recordings reindex` reports it
                self._index(rec)

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
            # rev never goes backwards, even after an adopted hand edit lowered it (§6.4)
            last = self.state.last_committed(rel)
            rev = max(rec.rev or 0, last.rev if last is not None else 0) + 1
            try:
                new = Recording.model_validate({**new.model_dump(by_alias=True), "rev": rev})
            except ValidationError as exc:
                raise InvalidFile(
                    f"{op.name} would make {rel} invalid: {str(exc).splitlines()[0]}") from None
            self._write_recording(rel, new, origin=op.name)
            return new

    def _load_fresh(self, rel: str) -> Recording:
        """The file as it is now, once it validates and is the one the app last wrote."""
        try:
            raw = (self.root / rel).read_bytes()
        except FileNotFoundError:
            raise KeyError(rel) from None
        try:
            rec = Recording.model_validate_json(raw)
        except ValidationError as exc:
            # Checked before freshness: `reindex` can't help a file that doesn't validate.
            message = str(exc).splitlines()[0]
            self.state.flag(rel, "invalid", message)
            raise InvalidFile(
                f"{rel} does not validate ({message}). Fix it by hand: the app changes nothing "
                "in a file that doesn't validate (§11).") from None
        self._check_fresh(rel, raw)
        return rec

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
        self._catch_up_index()
        sha = sha256_file(incoming.media)
        rid = make_id(incoming.recorded_at, sha)
        with self.locks.hold(f"sha-{sha}"):
            existing = self._holding(sha, rid)
            if existing is not None:
                return self._merge(existing, sha, incoming)
            return self._create(rid, sha, incoming)

    def _holding(self, sha: str, rid: str) -> str | None:
        """The recording that already holds these bytes, if any."""
        existing = self.index.find_by_sha256(sha)
        if existing is not None and not (self.root / self._rel(existing)).is_file():
            # The index names a folder that is gone (removed by hand): rebuild, then ask again.
            self.rebuild_index()
            existing = self.index.find_by_sha256(sha)
        if existing is None and (self.root / self._rel(rid)).is_file():
            existing = rid  # the index is behind (a crash after the rename): the folder wins
        return existing

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
            # The recording's entry, and the month and year folders' entries when they are new:
            # a new year folder is an entry in recordings/ (§6.4).
            for folder in (final.parent, final.parent.parent, final.parent.parent.parent):
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
            private = tuple(t for t in incoming.tags if is_private_tag(t.tag))
            if private:  # §7.4: the private tag lands first, before any content it protects
                self.mutate(rid, AddTags(private))
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
            digest = _sha(source.payload)
            # A file with these bytes already there is reused: a merge killed between publishing
            # and recording its snapshot never leaves a duplicate (Review Focus 2).
            same = next((p for p in sorted(target.parent.glob(f"{target.stem}*.json"))
                         if _sha(p.read_bytes()) == digest), None)
            path = same or publish_exclusive(source.payload, target)
            raw = path.relative_to(folder).as_posix()
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
            self.state.clear_flags(rel)  # an edit undone by hand leaves no stale flag behind
            return "unchanged"
        try:
            rec = Recording.model_validate_json(raw)
        except ValidationError as exc:
            self.state.flag(rel, "invalid", str(exc).splitlines()[0])
            return "invalid"
        # The base keeps the highest rev seen, so a hand-lowered rev never makes rev go back.
        self.state.adopt(rel, max(rec.rev or 0, last.rev if last is not None else 0), sha, raw)
        self.state.clear_flags(rel)
        return "adopted"
```

- [ ] **Step 5: Run the writer tests to see them pass**

Run: `uv run pytest packages/core/tests/test_writer.py packages/core/tests/test_locks.py -v`
Expected: 35 passed.

- [ ] **Step 6: Retire the `test_archive.py` tests that `test_writer.py` now covers**

In `packages/core/tests/test_archive.py`, **delete** these seven tests. Each drove a write
method that Task 5c removes, and `test_writer.py` covers the same behaviour through the `Writer`:
- `test_add_recording_lays_out_the_folder` (now `test_add_lays_out_the_folder_and_starts_at_rev_1`)
- `test_same_bytes_again_merge_into_one_recording` (now `test_the_same_bytes_again_merge_and_keep_the_second_copys_outputs_tags_and_notes`)
- `test_renditions_are_write_once_and_ordered` (now `test_renditions_are_write_once_ordered_and_never_duplicated`)
- `test_add_recording_writes_given_renditions_and_my_notes` (now `test_adding_the_same_thing_twice_adds_nothing`)
- `test_non_ascii_title_round_trips_as_utf8` (now `test_a_non_ascii_title_round_trips_as_utf8`)
- `test_add_recording_cleans_up_on_failure` (now `test_a_failed_assembly_leaves_nothing_behind`)
- `test_a_raw_source_records_its_hash_and_fetch_time`, from Task 3 (now
  `test_add_lays_out_the_folder_and_starts_at_rev_1` and
  `test_a_new_snapshot_of_the_same_source_is_kept_beside_the_first`)

Leave the file's helpers and imports as they are: Task 5c replaces the whole file.

- [ ] **Step 7: Run every test**

Run: `uv run pytest`
Expected: every test passes, 23 more than after Task 5a. Record the count in the ledger.

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/recordings/state.py packages/core/src/recordings/writer.py packages/core/tests/conftest.py packages/core/tests/test_writer.py packages/core/tests/test_archive.py
git commit -m "feat(core): the locked write path: Writer, mutate and the merge bases in state.db

mutate bumps rev, never backwards, and refuses a file edited outside the app (its content hash
against the merge base) or one that doesn't validate. add assembles under .tmp/ and merges
identical bytes, keeping the second copy's outputs, tags and notes. A private tag lands before
any content, a re-run reuses a snapshot it already published, and a busy index never fails a
write that landed. Stage 1's Archive writers stay until Task 5c. Checked: docs.python.org
sqlite3.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5c: Every caller moves onto the `Writer`; `Archive` is read-only; `recordings reindex`

**Checkpoint lens:** data integrity and write safety.

After this task, only the `Writer` writes. `recordings docs`, the app's docs at startup and the
demo build all go through `Writer.open` or a `Writer`. So nothing writes into an archive that this
machine doesn't write, the Mac's mirror included: the mirror carries `archive.json`, but it has
no `state.db`.

**Files:**
- Modify: `packages/core/src/recordings/archive.py` (read-only now: whole file below)
- Modify: `packages/core/src/recordings/cli.py` (`reindex`; `docs` through the `Writer`), `format/AGENTS.md`, `format/FORMAT.md`
- Modify: `packages/ui/src/recordings_ui/settings.py`, `app.py` (docs at startup)
- Modify: `demo/build.py` (builds through the `Writer`), `demo/archive/` (rebuilt)
- Modify: `packages/core/tests/test_archive.py` (whole file below), `test_selfdoc.py`, `test_cli.py`, `test_init.py`
- Modify: `packages/ui/tests/conftest.py` (whole file below), `test_views.py`, `test_app.py`

**Interfaces:**
- Consumes:
  - `Writer`, `Writer.open(cfg, *, docs=True)`, `Incoming`, `WriterError` (Task 5b)
  - `LockTimeout` (Task 5a)
  - `IndexRefused` (Task 4)
  - `Config` (Task 2)
- Produces:
  - **CLI:**
    - `recordings reindex [--allow-empty] [--json]`: exit 0, 1 when a file doesn't validate, 78 when this process isn't the writer.
    - `recordings docs [ARCHIVE] [--json]` now opens the `Writer`. `ARCHIVE` is optional, and must be `[archive] path`. It exits 78 when this process isn't the writer.
    - the helper `_writer(as_json: bool, *, docs: bool = True) -> Writer | int`
  - **`recordings_ui.settings.Settings`** gains `config: Config | None = None`.
  - **`recordings_ui.app.write_docs_at_startup(cfg: Config) -> None`**
  - **Test fixture** in ui `conftest.py`: `writer`, in folders of its own (`written-archive`, `written-state`, `written-index/`), so a test can take `demo_archive` as well.
- **Removed:** `Archive.add_recording`, `Archive.write_rendition`, `Archive.find_by_sha256`, and the private writing helpers in `archive.py` (`_write_raw`, `_write_rendition_into`, `_merge_source`, `_drop_empty_tmp`, `_slug`, `_UNSAFE`).

**Done when:** the full suite passes, with 3 more passed than after Task 5b (two in
`test_cli.py`, one in `test_app.py`), and the demo is rebuilt. Record the count in the ledger.

- [ ] **Step 1: Write the failing tests**

In `packages/core/tests/test_cli.py`:
1. **Add** `import shutil` below `import json`.
2. **Add** after the imports:
   ```python
   def _writer_config(tmp_path, monkeypatch, archive, state, index=None):
       cfg = tmp_path / "config.toml"
       cfg.write_text(
           f'[archive]\npath = "{archive}"\nwriter_id = "test"\n[state]\npath = "{state}"\n'
           f'[index]\npath = "{index or tmp_path / "index.db"}"\n', encoding="utf-8")
       monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
       for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX"):
           monkeypatch.delenv(name, raising=False)
   ```
3. **Replace** `test_docs_then_validate_on_a_new_archive` with:
   ```python
   def test_docs_then_validate_on_a_new_archive(tmp_path, monkeypatch, capsys):
       init_archive(tmp_path / "a", tmp_path / "state", writer_id="test")
       _writer_config(tmp_path, monkeypatch, tmp_path / "a", tmp_path / "state")
       (tmp_path / "a" / "FORMAT.md").unlink()
       assert main(["docs", "--json"]) == 0
       assert json.loads(capsys.readouterr().out)["written"] == ["FORMAT.md"]
       assert main(["validate", str(tmp_path / "a"), "--json"]) == 0
       assert json.loads(capsys.readouterr().out) == {"problems": []}


   def test_docs_are_never_written_into_the_macs_mirror(tmp_path, monkeypatch, capsys):
       # why: §6.7. The mirror carries archive.json too, so checking the sentinel alone let
       # `recordings docs` write into it. Only the archive's writer writes, through Writer.open.
       init_archive(tmp_path / "a", tmp_path / "state", writer_id="test")
       mirror = tmp_path / "mirror"
       shutil.copytree(tmp_path / "a", mirror)
       (mirror / "FORMAT.md").unlink()
       _writer_config(tmp_path, monkeypatch, tmp_path / "a", tmp_path / "state")
       assert main(["docs", str(mirror), "--json"]) == 78  # not the configured archive
       assert "not this machine's archive" in json.loads(capsys.readouterr().out)["error"]
       _writer_config(tmp_path, monkeypatch, mirror, tmp_path / "mac-state")  # the Mac: no state.db
       assert main(["docs", "--json"]) == 78
       assert "state.db" in json.loads(capsys.readouterr().out)["error"]
       assert not (mirror / "FORMAT.md").exists()


   def test_reindex_accepts_an_outside_edit(writer, make_incoming, tmp_path, monkeypatch, capsys):
       rec = writer.add(make_incoming(sources=())).recording  # no Plaud source to reconcile
       path = writer.archive.path_for(rec.id) / "recording.json"
       path.write_text(path.read_text(encoding="utf-8").replace('"Week 4"', '"Edited"'),
                       encoding="utf-8")
       _writer_config(tmp_path, monkeypatch, writer.root, writer.state.path, writer.index.path)
       assert main(["reindex", "--json"]) == 0
       out = json.loads(capsys.readouterr().out)
       assert out["recordings"] == 1 and out["invalid"] == []
       assert out["adopted"] == [f"recordings/2026/10/{rec.id}/recording.json"]
   ```

In `packages/core/tests/test_init.py`, **replace** `test_docs_refuses_a_folder_without_a_sentinel`
with:
```python
def test_docs_refuses_a_folder_without_a_sentinel(tmp_path, monkeypatch, capsys):
    # why: §6.7. Writing the docs into an unmounted, empty folder would make it look like an archive.
    init_archive(tmp_path / "archive", tmp_path / "state", writer_id="test")
    empty = tmp_path / "unmounted"
    empty.mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{empty}"\nwriter_id = "test"\n[state]\npath = "{tmp_path / "state"}"\n'
        f'[index]\npath = "{tmp_path / "index.db"}"\n', encoding="utf-8")
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX"):
        monkeypatch.delenv(name, raising=False)
    assert main(["docs", "--json"]) == 78
    assert "mounted" in json.loads(capsys.readouterr().out)["error"]
    assert list(empty.iterdir()) == []
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

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_cli.py packages/core/tests/test_init.py packages/ui/tests/test_app.py -v`
Expected: 5 failures:
- `test_docs_then_validate_on_a_new_archive` and `test_docs_refuses_a_folder_without_a_sentinel`: `SystemExit: 2` (argparse: "the following arguments are required: archive")
- `test_docs_are_never_written_into_the_macs_mirror`: `assert 0 == 78`, because the sentinel check alone lets `docs` write into the mirror
- `test_reindex_accepts_an_outside_edit`: `SystemExit: 2` (argparse: "invalid choice: 'reindex'")
- `test_the_docs_are_written_at_startup_only_into_an_archive_it_may_write`: `TypeError: Settings.__init__() got an unexpected keyword argument 'config'`

- [ ] **Step 3: Add `recordings reindex`, and write the docs through the `Writer`**

In `packages/core/src/recordings/cli.py`:
1. **Add** the imports, below Task 2's:
   ```python
   from recordings.index import IndexRefused
   from recordings.locks import LockTimeout
   from recordings.writer import Writer, WriterError
   ```
   Keep Task 2's `read_sentinel` and `SentinelError` import: `docs` no longer uses it, but Task 9's
   `_matching_index` does.
2. In `build_parser`, **replace**:
   ```python
       p = sub.add_parser("docs", help="write README/AGENTS/FORMAT and schemas into an archive")
       p.add_argument("archive", type=Path)
   ```
   with:
   ```python
       p = sub.add_parser("docs", help="write README/AGENTS/FORMAT and schemas into the archive")
       p.add_argument("archive", type=Path, nargs="?",
                      help="optional: it must be this machine's archive, [archive] path")
   ```
3. **Add** to `build_parser`, before the `doctor` parser:
   ```python
       p = sub.add_parser("reindex", help="accept outside edits and rebuild the derived index.db")
       p.add_argument("--allow-empty", action="store_true",
                      help="let the index become empty (refused by default: is the archive mounted?)")
       p.add_argument("--json", action="store_true")
   ```
4. **Add** after `cmd_init`:
   ```python
   def _writer(as_json: bool, *, docs: bool = True) -> Writer | int:
       cfg = _config(as_json)
       if isinstance(cfg, int):
           return cfg
       try:
           return Writer.open(cfg, docs=docs)
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
5. **Replace** `cmd_docs` with:
   ```python
   def cmd_docs(args: argparse.Namespace) -> int:
       """§6.7: the docs go only into the archive this machine writes, through its Writer. The Mac's
       mirror carries archive.json too, but it has no state.db, so it is refused."""
       cfg = _config(args.json)
       if isinstance(cfg, int):
           return cfg
       if args.archive is not None and (
               cfg.archive_path is None or args.archive.resolve() != cfg.archive_path.resolve()):
           return _fail(f"{args.archive} is not this machine's archive ([archive] path in "
                        "config.toml): only the archive's writer writes its docs", args.json, 78)
       writer = _writer(args.json, docs=False)
       if isinstance(writer, int):
           return writer
       try:
           written = writer.write_docs()
       except LockTimeout as exc:
           return _fail(str(exc), args.json, 1)
       _emit({"written": written}, args.json)
       return 0
   ```
6. **Add** `"reindex": cmd_reindex,` to the `commands` dict in `main`.

- [ ] **Step 4: Write the docs at startup**

In `packages/ui/src/recordings_ui/settings.py`:
1. **Replace** `from recordings.config import ConfigError, load_config` with
   `from recordings.config import Config, ConfigError, load_config`.
2. **Add** the field
   `config: Config | None = None  # None in demo mode, which never reads config.toml` to
   `Settings`, after `allowed_hosts`.
3. **Replace** the last line of `from_env` with:
   ```python
       return Settings(archive=cfg.archive_path, demo=False, allowed_hosts=hosts, config=cfg)
   ```

In `packages/ui/src/recordings_ui/app.py`:
1. **Add** the imports `import logging`, `import sqlite3`, `from recordings.config import Config`,
   `from recordings.locks import LockTimeout` and `from recordings.writer import Writer, WriterError`,
   and below the imports `log = logging.getLogger(__name__)`.
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
3. **Add** as the first lines of `create_app`'s body:
   ```python
       if settings.config is not None:
           write_docs_at_startup(settings.config)
   ```

- [ ] **Step 5: Run the new tests to see them pass**

Run: `uv run pytest packages/core/tests/test_cli.py packages/core/tests/test_init.py packages/ui/tests/test_app.py -v`
Expected: all pass.

- [ ] **Step 6: Make `Archive` read-only, and move the remaining callers onto the `Writer`**

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

In `packages/core/tests/test_selfdoc.py`, **replace** everything from the top of the file to the
end of `build_one` with:
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

Replace `packages/ui/tests/conftest.py` with:
```python
import json
import shutil
from pathlib import Path

import pytest

from recordings.index import Index
from recordings.init import init_archive
from recordings.state import State
from recordings.writer import Writer

REPO = Path(__file__).resolve().parents[3]
DEMO_ARCHIVE = REPO / "demo" / "archive"


@pytest.fixture
def demo_archive(tmp_path) -> Path:
    """A fresh copy, so no test can change the committed demo."""
    dst = tmp_path / "archive"
    shutil.copytree(DEMO_ARCHIVE, dst)
    return dst


@pytest.fixture(scope="session")
def demo_ids() -> dict[str, str]:
    aliases = json.loads((REPO / "demo" / "canned" / "aliases.json").read_text())
    return {slug: rid for rid, slug in aliases.items()}


@pytest.fixture
def writer(tmp_path) -> Writer:
    """A new, empty archive and its Writer, in folders of its own, so a test can also take
    demo_archive (which is tmp_path/"archive")."""
    root, state = tmp_path / "written-archive", tmp_path / "written-state"
    init_archive(root, state, writer_id="test")
    return Writer(root, State.open(state), Index(tmp_path / "written-index" / "index.db"),
                  writer_id="test")
```

In `packages/ui/tests/test_views.py`, **add** `from recordings.writer import Incoming` to the
imports, and **replace** the four tests that call `archive.add_recording`
(`test_a_recording_with_no_outputs_has_empty_tabs`, `test_model_written_html_is_escaped`,
`test_a_capitalised_private_tag_is_private_everywhere`, and Task 3's
`test_a_turn_shows_plauds_name_for_its_speaker`) with:
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

In `demo/build.py`:
1. **Replace** `from recordings.archive import Archive, RawSource, sha256_file` with
   `from recordings.archive import RawSource, sha256_file`.
2. **Add** the imports `from recordings.index import Index`, `from recordings.state import State`
   and `from recordings.writer import Incoming, Writer`.
3. **Replace** `build` with:
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

In `packages/core/src/recordings/format/AGENTS.md`, **replace**:
```markdown
6. **After a bulk edit, run `recordings reindex`** (available from stage 3). Jobs that the
   edit would start wait for approval in the app.
```
with:
```markdown
6. **After editing any file, run `recordings reindex`** on the homelab server. It accepts your
   edits as the base for the app's next write. Until then, the app refuses to change a file you
   edited, rather than overwrite your edit. Jobs that the edit would start wait for approval in
   the app.
```

In `packages/core/src/recordings/format/FORMAT.md`, **replace**:
```markdown
- **Work in progress:** writers assemble a new recording in `.tmp/` at the archive root and
  write temporary files named `.*.tmp`; readers and mirrors should ignore both.
```
with:
```markdown
- **Work in progress:** writers assemble a new recording in `.tmp/<id>.<hex>/` at the archive root,
  and write temporary files named `.*.tmp`. Readers and mirrors skip both.
```

- [ ] **Step 7: Rebuild the demo and run every test**

If `demo/.cache/media` is missing, refill it from the committed archive first. It is the same
media, so the rebuild stays byte-identical:
```bash
mkdir -p demo/.cache/media && uv run python -c "import json,pathlib,shutil,tomllib; from recordings.archive import Archive; a=Archive(pathlib.Path('demo/archive')); ext={e['slug']:e['ext'] for e in tomllib.loads(pathlib.Path('demo/sources.toml').read_text())['recording']}; [shutil.copy(a.media_path(r), f'demo/.cache/media/{s}.{ext[s]}') for r,s in json.load(open('demo/canned/aliases.json')).items()]"
```

Run:
```bash
uv run python demo/build.py --media-dir demo/.cache/media --out demo/archive --force
uv run pytest
```
Expected: `built 4 recordings into demo/archive`, then every test passes, 3 more than after Task
5b. The demo's `recording.json` files now carry `"rev": 1`, and its `AGENTS.md` and `FORMAT.md`
change. Record the count in the ledger.

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/recordings packages/core/tests packages/ui/src/recordings_ui packages/ui/tests demo/build.py demo/archive
git commit -m "refactor: every writer goes through the Writer; Archive is read-only; recordings reindex

reindex accepts outside edits and rebuilds index.db. recordings docs, the app's docs at startup
and the demo build all open the Writer, so nothing writes into an archive this machine doesn't
write: the Mac's mirror carries archive.json but has no state.db. Stage 1's add_recording,
write_rendition and duplicate merge are gone. Checked: docs.python.org argparse (nargs='?').

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 6: The Plaud normaliser

**Checkpoint lens:** data integrity and write safety.

A pure, versioned function (§9.1.1). The import uses it now, and stage 2b's sync will use it
unchanged, so both agree on what changed. The payload's shape is modelled on `audio-router`'s
`archive/sources/plaud.py` (the envelope from `files/{id}`), `plaud_transcript.py` (segment rows),
`schemas.py` (the legacy `_meta`) and `artifacts.py` (the canonical hash). Read them, never the
archive itself.

Three details matter beyond the hash:
- **Every field lands in a part.** The Sync screen (stage 2b) says what changed from the part
  hashes, and the circuit breaker counts only "other". `transaction_polish` carries the same
  rows as `transaction`, speaker names included, so both feed the transcript and speakers parts.
  Under "other", one relabelling sweep would trip the breaker. A field Plaud starts adding to
  every segment row goes to "other", because that is the volatile field the breaker exists for.
- **Legacy snapshots** from `audio-router`'s retired rescue script record failures in
  `_meta.data_link_errors` and the fetch time in `_meta.fetched_at` (schemas.py, `PlaudMeta`).
  `link_errors` honours the first. `meta_fetched_at` reads the second, for Task 9.
- **The normalised form is for hashing only.** `note_blocks` hands back each raw block beside its
  normalised one, and reconcile (Task 7) renders the raw one.

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
  - **Completeness and legacy snapshots:**
    - `link_errors(envelope: dict) -> list[str]`: a data type per block whose content didn't arrive (a link error, a link never followed, or a link that answered with an empty body), plus `legacy` per entry in `_meta.data_link_errors`
    - `is_complete(envelope: dict) -> bool`
    - `meta_fetched_at(envelope: Any) -> datetime | None`: a legacy snapshot's `_meta.fetched_at`, in UTC; None when absent, unparseable or without an offset
  - **Parts of the payload:**
    - `transcript_rows(envelope: dict) -> list[dict] | None`
    - `note_type_base(block: dict) -> str`
    - `note_blocks(envelope: dict) -> list[tuple[str, dict, dict]]`: (note type, normalised block, raw block)
    - `diarization_fingerprint(envelope: dict) -> str | None`
    - `part_hashes(envelope: dict) -> dict[str, str]`
  - **Test fixture** `plaud_env`: a factory `(fid=HEX, *, name="Week 4", segments=None, notes=None, outline=True, polish=False, duration=2600, link_error=False, presigned="abc") -> dict`. Also the module constant `HEX` in `conftest.py`.

- [ ] **Step 1: Write the failing tests**

Add to `packages/core/tests/conftest.py` (with `import json` at the top):
```python
HEX = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def plaud_env():
    """A synthetic Plaud `files/{id}` envelope, shaped like audio-router's raw snapshots: invented
    names, invented text, a rotating presigned URL and audio-router's own `_meta`. `polish` adds a
    `transaction_polish` block with the same rows, as Plaud's polished transcript has."""
    def make(fid: str = HEX, *, name: str = "Week 4", segments=None, notes=None,
             outline: bool = True, polish: bool = False, duration: int = 2600,
             link_error: bool = False, presigned: str = "abc") -> dict:
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
        if polish:
            source_list.append({"data_type": "transaction_polish", "data_id": "p-1",
                                "data_tab_name": None,
                                "data_content": json.dumps(rows, ensure_ascii=False)})
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
from datetime import datetime, timezone

from recordings.plaud.normalise import (
    DROPPED_KEYS,
    diarization_fingerprint,
    envelope_sha256,
    is_complete,
    link_errors,
    meta_fetched_at,
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


def _edited(env, path):
    edited = copy.deepcopy(env)
    target = edited
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = _changed(target[path[-1]])
    return edited


def test_changing_any_field_off_the_drop_list_changes_the_hash(plaud_env):
    # why: §9.1.1's property test. A normaliser that drops too much silently loses edits.
    env = plaud_env()
    base = envelope_sha256(env)
    leaves = list(_leaves(env))
    assert len(leaves) > 20
    for path, _value in leaves:
        assert envelope_sha256(_edited(env, path)) != base, path


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
    # A link that answered with an empty body is no better than one that failed.
    empty_body = plaud_env()
    empty_body["note_list"][0] = {**empty_body["note_list"][0], "data_content": " ",
                                  "_fetched_from_data_link": True}
    assert link_errors(empty_body) == ["auto_sum_note"]


def test_a_legacy_snapshot_lists_its_link_errors_in_meta(plaud_env):
    # why: audio-router's retired rescue script wrote these, with no per-block error.
    legacy = plaud_env()
    legacy["_meta"] = {"tool": "audio-router", "data_link_errors": ["a link that failed"]}
    assert link_errors(legacy) == ["legacy"]
    legacy["_meta"]["data_link_errors"] = []
    assert is_complete(legacy)


def test_a_legacy_snapshot_says_when_it_was_fetched(plaud_env):
    env = plaud_env()
    assert meta_fetched_at(env) is None
    env["_meta"] = {"fetched_at": "2025-03-01T04:00:00-08:00"}
    assert meta_fetched_at(env) == datetime(2025, 3, 1, 12, tzinfo=timezone.utc)
    env["_meta"] = {"fetched_at": "2025-03-01T12:00:00Z"}
    assert meta_fetched_at(env) == datetime(2025, 3, 1, 12, tzinfo=timezone.utc)
    for unusable in ("2025-03-01T12:00:00", "yesterday", 1740830400):  # no offset, not a time
        env["_meta"] = {"fetched_at": unusable}
        assert meta_fetched_at(env) is None
    assert meta_fetched_at(["not", "an", "object"]) is None


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
    types = [t for t, _, _ in note_blocks(env)]
    # The outline first, then note_list sorted by (type, tab, id): "Summary" sorts before "会議…".
    assert types == ["plaud-outline", "plaud-summary", "plaud-summary-2", "plaud-q-a",
                     "plaud-consumer-note", "plaud-high-light"]
    assert all(t.isascii() and " " not in t and "/" not in t for t in types)


def test_note_blocks_pair_each_normalised_block_with_its_raw_one(plaud_env):
    # why: notes are rendered from the raw block. The scrub runs to the next space, so on the
    # normalised block a Markdown link loses its closing parenthesis.
    text = "See [the chart](https://notes.example/a.png?X-Amz-Signature=1) here."
    env = plaud_env(notes=[{"data_type": "auto_sum_note", "data_id": "n", "data_tab_name": "S",
                            "data_content": text}])
    _outline, (note_type, block, raw) = note_blocks(env)
    assert note_type == "plaud-s" and raw["data_content"] == text
    assert block["data_content"] == "See [the chart](https://notes.example/a.png?<presigned> here."


def test_the_diarization_fingerprint_ignores_names_but_not_timing(plaud_env):
    env = plaud_env()
    rows = json.loads(env["source_list"][0]["data_content"])
    renamed = plaud_env(segments=[{**rows[0], "speaker": "Grace"}, rows[1]])
    resegmented = plaud_env(segments=[{**rows[0], "end_time": 1400}, rows[1]])
    assert diarization_fingerprint(renamed) == diarization_fingerprint(env)
    assert diarization_fingerprint(resegmented) != diarization_fingerprint(env)


def _parts_changed(before, after):
    old, new = part_hashes(before), part_hashes(after)
    return {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}


def test_part_hashes_say_what_changed(plaud_env):
    env = plaud_env()
    assert set(part_hashes(env)) == {"title", "transcript", "speakers", "notes:plaud-outline",
                                     "notes:plaud-summary", "notes:plaud-high-light", "other"}
    rows = json.loads(env["source_list"][0]["data_content"])
    assert _parts_changed(env, plaud_env(name="Week 5")) == {"title"}
    renamed = plaud_env(segments=[{**rows[0], "speaker": "Grace"}, rows[1]])
    assert _parts_changed(env, renamed) == {"speakers"}
    reworded = plaud_env(segments=[{**rows[0], "content": "Hello!"}, rows[1]])
    assert _parts_changed(env, reworded) == {"transcript"}
    edited = copy.deepcopy(env)
    edited["note_list"][0]["data_content"] += " Then they leave."
    assert _parts_changed(env, edited) == {"notes:plaud-summary"}
    assert _parts_changed(env, {**env, "serial_number": "TEST0002"}) == {"other"}


def test_the_polished_transcript_belongs_to_the_transcript_and_speakers(plaud_env):
    # why: Plaud renames a speaker in both segment blocks. Were `transaction_polish` "other", one
    # relabelling sweep would trip the circuit breaker, which counts only "other" (§9.1.1).
    env = plaud_env(polish=True)
    rows = json.loads(env["source_list"][0]["data_content"])
    renamed = plaud_env(polish=True, segments=[{**rows[0], "speaker": "Grace"}, rows[1]])
    assert _parts_changed(env, renamed) == {"speakers"}
    polished = copy.deepcopy(env)
    (block,) = [b for b in polished["source_list"] if b["data_type"] == "transaction_polish"]
    block["data_content"] = json.dumps([{**rows[0], "content": "Hello, there."}, rows[1]])
    assert _parts_changed(env, polished) == {"transcript"}


def test_every_field_lands_in_some_part_and_new_row_fields_are_other(plaud_env):
    # why: a field in no part changes the snapshot while the Sync screen shows nothing, and the
    # circuit breaker never counts it. A field Plaud starts adding to every segment row is the
    # volatile field the breaker exists for, so it is "other", never "transcript".
    env = plaud_env(polish=True)
    for path, _value in _leaves(env):
        assert _parts_changed(env, _edited(env, path)), path
    rows = json.loads(env["source_list"][0]["data_content"])
    tagged = plaud_env(polish=True, segments=[{**r, "request_id": "r-1"} for r in rows])
    assert _parts_changed(env, tagged) == {"other"}
    # A segment block that doesn't parse stays in "other", whole.
    garbled = copy.deepcopy(env)
    garbled["source_list"][-1]["data_content"] = "not json"  # the polish block
    more = copy.deepcopy(garbled)
    more["source_list"][-1]["data_content"] = "not json either"
    assert _parts_changed(garbled, more) == {"other"}
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

The normalised form is for hashing only. Notes are rendered from the raw block (`note_blocks`
hands back both): the URL scrub runs to the next space or quote, so it also eats a Markdown
link's closing parenthesis.

The envelope's shape is audio-router's `files/{id}` snapshot (archive/sources/plaud.py,
plaud_transcript.py and schemas.py at commit 64612df).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
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
# A segment row's fields, by part: the text with its timing, and the names. A field outside both
# counts as "other", so a field Plaud starts adding to every row still reaches the circuit breaker.
_TEXT_KEYS = ("start_time", "end_time", "content", "original_speaker")
_NAME_KEYS = ("speaker", "embeddingKey")
_ROW_KEYS = frozenset(_TEXT_KEYS + _NAME_KEYS)


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


def _normal_block(block: Any) -> Any:
    """One block as `normalise` leaves it."""
    return _canonical_block(_scrub(block))


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


def meta_fetched_at(envelope: Any) -> datetime | None:
    """When a legacy snapshot was fetched, from its own `_meta.fetched_at`.

    audio-router's retired rescue script stamped it (schemas.py, `PlaudMeta`); the snapshot's
    file name says only when audio-router wrote the file. None when it is absent, unparseable,
    or has no UTC offset: a naive time could be any zone, so the caller falls back to the file.
    """
    meta = envelope.get("_meta") if isinstance(envelope, dict) else None
    value = meta.get("fetched_at") if isinstance(meta, dict) else None
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return when.astimezone(timezone.utc) if when.tzinfo is not None else None


def link_errors(envelope: dict) -> list[str]:
    """Why a snapshot is incomplete: the data type of each block whose content didn't arrive,
    and `legacy` for each failure a legacy snapshot lists in `_meta.data_link_errors`. A snapshot
    with any is never a baseline, and nothing is built from it (§9.1.1).

    A block's content didn't arrive when it has a link error, or when it is empty and has a
    link: one never followed (`data_link`), or one followed that answered with an empty body
    (`_fetched_from_data_link`). An empty body is no more trustworthy than a failed link. Legacy
    snapshots, from audio-router's retired rescue script, record their failures in `_meta`."""
    out = []
    for section in ("source_list", "note_list"):
        for block in envelope.get(section) or []:
            if not isinstance(block, dict):
                continue
            empty = not str(block.get("data_content") or "").strip()
            linked = block.get("data_link") or block.get("_fetched_from_data_link")
            if block.get("_data_link_error") or (empty and linked):
                out.append(str(block.get("data_type") or "?"))
    meta = envelope.get("_meta")
    legacy = meta.get("data_link_errors") if isinstance(meta, dict) else None
    if legacy:
        out += ["legacy"] * (len(legacy) if isinstance(legacy, list) else 1)
    return out


def is_complete(envelope: dict) -> bool:
    return not link_errors(envelope)


def _rows_of(block: Any) -> list[dict] | None:
    """A segment block's rows, or None when it isn't one or its content isn't a JSON list of
    objects."""
    if not isinstance(block, dict) or block.get("data_type") not in SEGMENT_TYPES:
        return None
    content = block.get("data_content")
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        rows = json.loads(content)
    except json.JSONDecodeError:
        return None
    ok = isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows)
    return rows if ok else None


def transcript_rows(envelope: dict) -> list[dict] | None:
    """The `transaction` block's segment rows, or None when there is none or it isn't JSON."""
    for block in envelope.get("source_list") or []:
        if isinstance(block, dict) and block.get("data_type") == "transaction":
            return _rows_of(block)
    return None


def _ascii_slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")


def note_type_base(block: dict) -> str:
    return (_ascii_slug(str(block.get("data_tab_name") or ""))
            or _ascii_slug(str(block.get("data_type") or "")) or "note")


def note_blocks(envelope: dict) -> list[tuple[str, dict, dict]]:
    """Each note block as (note type, normalised block, raw block), in normalised order: the
    `outline`, then every `note_list` tab, Dan's own highlights and memos included. The note
    type is `plaud-<tab>` (§6.5); a second tab with the same name gets `-2`, then `-3`, and so
    on. Hash the normalised block. Render the raw one, whose links are whole."""
    outline = [b for b in envelope.get("source_list") or []
               if isinstance(b, dict) and b.get("data_type") == "outline"]
    tabs = [b for b in envelope.get("note_list") or [] if isinstance(b, dict)]
    out, seen = [], {}
    for blocks in (outline, tabs):
        pairs = sorted(((_normal_block(b), b) for b in blocks), key=lambda p: _block_key(p[0]))
        for block, raw in pairs:
            base = note_type_base(block)
            seen[base] = seen.get(base, 0) + 1
            note_type = f"plaud-{base}" if seen[base] == 1 else f"plaud-{base}-{seen[base]}"
            out.append((note_type, block, raw))
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
    everything else. The Sync screen (stage 2b) says what changed from these, and the circuit
    breaker counts only "other".

    Both segment blocks feed the transcript and speakers parts: `transaction`, and Plaud's
    `transaction_polish`, which has the same rows, names included. A speaker rename changes
    both blocks, so with polish under "other" one relabelling sweep would trip the breaker.
    Every field lands in a part. A segment row's text and timing go to "transcript", its names
    to "speakers", and any other row field to "other", with the rest of its block. A segment
    block that doesn't parse goes to "other" whole."""
    env = normalise(envelope)
    parts = {"title": sha256_of(env.get("name"))}
    sources = env.get("source_list")
    text, names, residue = [], [], []
    for block in sources if isinstance(sources, list) else []:
        rows = _rows_of(block)
        if rows is not None:
            kind = block["data_type"]
            text.append([kind, [[r.get(k) for k in _TEXT_KEYS] for r in rows]])
            names.append([kind, [[r.get(k) for k in _NAME_KEYS] for r in rows]])
            extra = [[i, {k: v for k, v in r.items() if k not in _ROW_KEYS}]
                     for i, r in enumerate(rows) if not _ROW_KEYS.issuperset(r)]
            residue.append([{k: v for k, v in block.items() if k != "data_content"}, extra])
        elif not (isinstance(block, dict) and block.get("data_type") == "outline"):
            residue.append(block)  # the outline is a note tab; anything else stays here whole
    if text:
        parts["transcript"] = sha256_of(text)
        parts["speakers"] = sha256_of(names)
    for note_type, block, _raw in note_blocks(env):
        parts[f"notes:{note_type}"] = sha256_of(block)
    rest = {k: v for k, v in env.items() if k != "name"}
    if isinstance(sources, list):
        rest["source_list"] = residue
    if isinstance(env.get("note_list"), list):
        rest["note_list"] = [b for b in env["note_list"] if not isinstance(b, dict)]
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

A pure function shared by the import and the sync (spec §9.1.1). Every field lands in a part,
and transaction_polish feeds the transcript and speakers parts, so a speaker rename never counts
towards the circuit breaker. Legacy snapshots' _meta link errors and fetch times are
honoured. Modelled on audio-router's plaud.py, plaud_transcript.py, schemas.py and artifacts.py
at 64612df; synthetic fixtures only.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Reconcile

**Checkpoint lens:** data integrity and write safety.

Three rules shape this task:
- **Privacy first.** `reconcile_plaud` applies the fields Plaud owns, an auto-private tag among
  them, in one `mutate` before it publishes any output. A crash in between leaves a tagged
  recording whose outputs the next reconcile writes. It never leaves an output in a recording
  that its title marks private and no tag does yet.
- **Auto-private looks everywhere.** The patterns are matched against every title any snapshot
  carries, under either Plaud ID, degraded snapshots included, with Plaud's "MM-DD " prefix
  stripped first, as `audio-router`'s `normalize_title` does (gate.py at 64612df). Without the
  strip, `^\s*private` matches no real title. Task 9 calls the same `matches_auto_private` to tag
  a recording when it is created.
- **Hashed normalised, rendered raw.** A note's `part_sha256` hashes its normalised block, so a
  rotating signature is never a change. Its Markdown comes from the raw block: the scrub would eat
  a Markdown link's closing parenthesis. `version` is part of an output's identity, so a
  normaliser bump writes every part again (§6.5).

**Files:**
- Create: `packages/core/src/recordings/plaud/blocks.py`, `packages/core/src/recordings/plaud/reconcile.py`
- Modify: `packages/core/src/recordings/writer.py` (`SetPlaudFields`, `ReconcileReport`, `Writer.reconcile_plaud`, `auto_private`, `reindex` reconciles)
- Modify: `config.example.toml` (`[plaud] auto_private_patterns`), `demo/build.py` (a synthetic Plaud envelope, Plaud outputs from reconcile), `demo/archive/` (rebuilt)
- Test: `packages/core/tests/test_reconcile.py`

**Interfaces:**
- Consumes:
  - from Task 6: `normalise`, `is_complete`, `transcript_rows`, `note_blocks` (with each raw block), `diarization_fingerprint`, `sha256_of`, `GENERIC_SPEAKER`, `NORMALISER_VERSION`
  - from Task 5b: `Writer.mutate`, `Writer._write_rendition_locked`, `Writer._load_fresh`, `AddTags`
  - from Task 3: `PlaudState`, `is_private`, and `Recording.title_by: Literal["you", "plaud", "recorder", "pocket"] | None`
- Produces:
  - **`recordings.plaud.blocks`:** `render_outline(raw: str) -> str | None`, `render_marks(raw: str) -> str | None` (keeps a highlight's picture link), `render_note(block: dict) -> str`.
  - **`recordings.plaud.reconcile`:**
    - constants: `PLAUD_VERSION = "plaud@1"`, `TIMING_RULE = "timing@1"`, `TIMING_TOLERANCE_MS = 1000`, `DEFAULT_AUTO_PRIVATE = (r"^\s*private", r"interview:")`, `TITLE_DATE_PREFIX: re.Pattern` (`^\s*\d{1,2}-\d{1,2}[\s-]+`)
    - `@dataclass(frozen=True) Snapshot(path: str, ref: str, fetched_at: datetime, envelope: dict | None)`
    - `@dataclass(frozen=True) Held(snapshot: str, slot: str, reason: str)`
    - `@dataclass(frozen=True) Reconciled(outputs, title, title_seen, title_difference, add_private, held, skipped)`
    - `timing_check(envelope: dict, rows: list[dict], media_ms: int | None) -> dict`
    - `patterns_from_config(data: dict) -> tuple[str, ...]`
    - `strip_date_prefix(title: str) -> str`
    - `matches_auto_private(titles: Iterable[str], patterns: Sequence[str] = DEFAULT_AUTO_PRIVATE) -> bool`: the one matcher; Task 9 uses it too
    - `reconcile(rec: Recording, snapshots: Sequence[Snapshot], existing: Sequence[Rendition], *, auto_private: Sequence[str] = DEFAULT_AUTO_PRIVATE) -> Reconciled`
  - **`recordings.writer`:**
    - `@dataclass(frozen=True) SetPlaudFields(title: str | None = None, title_seen: str | None = None, add_private: bool = False)`
    - `@dataclass(frozen=True) ReconcileReport(recording_id: str, written: int, held: tuple[Held, ...], skipped: tuple[str, ...], title_difference: bool, problem: str | None = None)`, with `.to_dict()`
    - `Writer.reconcile_plaud(recording_id: str) -> ReconcileReport`: Plaud's fields and any private tag first, then the outputs
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
from recordings.models import MediaInfo, PlaudState, Recording, TagRef, is_private
from recordings.plaud.reconcile import (
    PLAUD_VERSION,
    Snapshot,
    matches_auto_private,
    patterns_from_config,
    reconcile,
    strip_date_prefix,
    timing_check,
)
from recordings.writer import Writer, rendition_filename

HEX = "0123456789abcdef0123456789abcdef"
OTHER = "fedcba9876543210fedcba9876543210"
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


def test_an_output_of_another_version_is_not_this_ones(plaud_env):
    # why: §6.5. `version` names the normaliser, and is part of an output's identity, so a
    # normaliser bump writes every part again.
    first = reconcile(rec(), [snap(plaud_env())], [])
    older = [o.model_copy(update={"version": "plaud@0"}) for o in first.outputs]
    assert kinds(reconcile(rec(), [snap(plaud_env())], older).outputs) == kinds(first.outputs)


def test_notes_are_rendered_from_the_raw_block_and_hashed_from_the_normalised_one(plaud_env):
    # why: the normaliser's scrub eats a Markdown link's closing parenthesis, and a highlight's
    # picture link must survive. A rotating signature is still never a change.
    link = "https://notes.example/a.png?X-Amz-Expires=300&X-Amz-Signature={}"

    def envelope(signature):
        return plaud_env(notes=[
            {"data_type": "auto_sum_note", "data_id": "n", "data_tab_name": "Summary",
             "data_content": f"See [the chart]({link.format(signature)}) here."},
            {"data_type": "high_light", "data_id": "h", "data_tab_name": None,
             "data_content": json.dumps([{"timestamp": 1000, "title": "Board",
                                          "content": "A sketch",
                                          "picture_link": link.format(signature)}])}])

    s1, s2 = snap(envelope("one")), snap(envelope("two"), 1)
    first = reconcile(rec(), [s1], [])
    summary = next(o for o in first.outputs if o.note_type == "plaud-summary")
    assert summary.payload["markdown"] == f"See [the chart]({link.format('one')}) here."
    highlight = next(o for o in first.outputs if o.note_type == "plaud-high-light")
    assert highlight.payload["markdown"] == (
        f"**[00:01] Board**\n\nA sketch\n\n(image: {link.format('one')})")
    assert reconcile(rec(), [s1, s2], first.outputs).outputs == ()


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


def test_plaud_owns_the_title_only_while_title_by_is_plaud_or_absent(plaud_env):
    renamed = [snap(plaud_env(name="Week 5"))]
    owned = reconcile(rec(), renamed, [])
    assert (owned.title, owned.title_seen, owned.title_difference) == ("Week 5", "Week 5", None)
    assert reconcile(rec(title_by=None), renamed, []).title == "Week 5"
    for other in ("you", "recorder", "pocket"):
        kept = reconcile(rec(title="My title", title_by=other), renamed, [])
        assert (kept.title, kept.title_seen, kept.title_difference) == (None, None, "Week 5")
    dismissed = rec(title="My title", title_by="you", plaud=PlaudState(title_seen="Week 5"))
    assert reconcile(dismissed, renamed, []).title_difference is None


@pytest.mark.parametrize("title, private", [
    ("Private: therapy", True), ("  private notes", True), ("Interview: Grace", True),
    ("10-06 Private session", True), ("10-06-private notes", True),
    ("1-2 Interview: Grace", True),
    ("Consultation: Private Chef Charity", False), ("10-06 Consultation: Private Chef", False),
    ("Week 4", False)])
def test_the_auto_private_patterns_add_private(plaud_env, title, private):
    assert reconcile(rec(), [snap(plaud_env(name=title))], []).add_private is private


def test_plauds_date_prefix_is_stripped_before_matching():
    # why: Plaud starts its titles "MM-DD ", so an anchored pattern would match no real title.
    # audio-router's normalize_title strips the same prefix.
    assert strip_date_prefix("10-06 Private session") == "Private session"
    assert strip_date_prefix(" 1-2-Interview: x") == "Interview: x"
    assert strip_date_prefix("2026-10-06 14:00") == "2026-10-06 14:00"
    assert strip_date_prefix("Week 4") == "Week 4"
    assert matches_auto_private(["Week 4", "10-06 Private session"])
    assert not matches_auto_private(["10-06 Week 4"])
    assert matches_auto_private(["10-06 Week 4"], ["^10-06"])  # the title as given counts too


def test_auto_private_checks_every_snapshot_title_and_both_plaud_ids(plaud_env):
    # why: privacy errs towards private. A title Plaud has since changed, the other Plaud ID's
    # title, and a degraded snapshot's title all still count.
    renamed_since = [snap(plaud_env(name="10-06 Private session")),
                     snap(plaud_env(name="Week 4"), 1)]
    assert reconcile(rec(), renamed_since, []).add_private is True
    other_id = [snap(plaud_env(name="Week 4")),
                snap(plaud_env(OTHER, name="Interview: Ada"), 1, ref=OTHER)]
    assert reconcile(rec(), other_id, []).add_private is True
    degraded = [snap(plaud_env(name="Week 4")),
                snap(plaud_env(name="Private", link_error=True), 1)]
    assert reconcile(rec(), degraded, []).add_private is True


def test_auto_private_never_removes_and_skips_an_already_private_recording(plaud_env):
    already = rec(tags=[TagRef(tag="private", by="you")])
    assert reconcile(already, [snap(plaud_env(name="Private: x"))], []).add_private is False


def test_two_plaud_ids_on_one_recording_are_reconciled_separately(plaud_env):
    a = snap(plaud_env(HEX, name="First"))
    b = snap(plaud_env(OTHER, name="Second"), 1, ref=OTHER)
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


def test_the_private_tag_lands_before_any_plaud_output(writer, make_incoming, plaud_env,
                                                        monkeypatch):
    # why: privacy first (§7.4). Were the outputs written first, a crash before the tag would
    # leave Plaud's transcript and notes in a recording nothing marks private.
    payload = json.dumps(plaud_env(name="10-06 Private session")).encode()
    added = writer.add(make_incoming(title="Week 4", title_by="plaud", sources=(
        RawSource(kind="plaud", ref=HEX, added_at=T1, fetched_at=T1, payload=payload),)))
    private_at_each_output = []
    real = Writer._write_rendition_locked

    def watched(self, folder, rendition):
        private_at_each_output.append(is_private(self.archive.load(added.recording.id)))
        return real(self, folder, rendition)

    monkeypatch.setattr(Writer, "_write_rendition_locked", watched)
    assert writer.reconcile_plaud(added.recording.id).written == 4
    assert private_at_each_output == [True] * 4


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
Highlights and memos are lists whose field names vary between blocks; a highlight can carry a
picture link its text doesn't mention, and it is kept. Anything unrecognised degrades to the raw
text: a renderer that raised would turn an unfamiliar payload into a missing note, which is worse
than the JSON it replaces.

Reconcile renders the raw block, never the normalised one, so links stay whole. A change here
changes the outputs, so it bumps NORMALISER_VERSION too: `version` must change whenever the
output would (§6.5).
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
        picture = str(r.get("picture_link") or r.get("picture_full_url") or "").strip()
        if not title and not body and not picture:
            continue
        stamp = _stamp(r.get("timestamp"))
        entry = f"**{stamp} {title}**" if title else f"**{stamp}**"
        if body:
            entry += f"\n\n{body}"
        if picture:
            entry += f"\n\n(image: {picture})"
        out.append(entry)
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
- **An output's identity** is its kind, note type, snapshot, part hash and `version`. An output
  of another version doesn't count, so a normaliser bump writes every part again (§6.5).
- **Hashed normalised, rendered raw:** a note's hash comes from its normalised block, so a
  rotating signature is never a change. Its Markdown comes from the raw block, so a link keeps
  its closing parenthesis and a highlight keeps its picture link.
- **Removals wait for review:** a tab or transcript that vanishes, or names that revert to
  "Speaker N" on an unchanged diarization. They are held while their snapshot is newer than
  `plaud.acknowledged_up_to`.
- **The title:** Plaud owns it only while `title_by` is `plaud` or absent. Any other value
  (`you`, or another source's kind) keeps the title, and Plaud's shows as a difference until it
  is dismissed.
- **Auto-private:** every title any snapshot carries, under either Plaud ID and degraded
  snapshots included, is matched against the patterns, with Plaud's "MM-DD " prefix stripped.
  A match adds `private` and never removes it. The Writer applies it before any output.
- **Skipped snapshots:** one with link errors, or one that isn't a JSON object, is skipped and
  reported. Nothing is ever built from it.
- **Two Plaud IDs on one recording** are reconciled separately. The title follows the ID whose
  snapshot comes first in the sequence given (the recording's `sources` order).
- **Not yet:** people files (stage 3b). Plaud's names stay as `speaker_name`, and linking them to
  people comes later.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
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
# Plaud starts every AI title with "MM-DD " ("10-06 Private session"). audio-router's
# normalize_title strips it before matching its holds (gate.py at 64612df); without that, an
# anchored pattern matches no real title.
TITLE_DATE_PREFIX = re.compile(r"^\s*\d{1,2}-\d{1,2}[\s-]+")


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
    title_difference: str | None = None  # Plaud's title, when the title isn't Plaud's to set
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


def strip_date_prefix(title: str) -> str:
    return TITLE_DATE_PREFIX.sub("", title, count=1).strip()


def matches_auto_private(titles: Iterable[str],
                         patterns: Sequence[str] = DEFAULT_AUTO_PRIVATE) -> bool:
    """Whether any of these Plaud titles matches an auto-private pattern (§7.4). Each pattern is
    searched, ignoring case, in the title as given and with its "MM-DD " prefix stripped; trying
    both errs towards private. The import calls it to tag a recording when it is created, and
    reconcile on every snapshot's title."""
    return any(re.search(pattern, text, re.IGNORECASE)
               for title in titles for text in (title, strip_date_prefix(title))
               for pattern in patterns)


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


def _title_of(envelope: Any) -> str | None:
    name = envelope.get("name") if isinstance(envelope, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


def _slots(envelope: dict) -> dict[str, tuple[str, Any]]:
    """Each part reconcile writes, as (hash, value). The transcript's value is its normalised
    rows. A note's value is its raw block, which is what gets rendered; its hash is of the
    normalised block."""
    out: dict[str, tuple[str, Any]] = {}
    rows = transcript_rows(normalise(envelope))
    if rows:
        out["transcript"] = (sha256_of(rows), rows)
    for note_type, block, raw in note_blocks(envelope):
        if render_note(raw):
            out[f"notes:{note_type}"] = (sha256_of(block), raw)
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
    return (r.kind, r.note_type, r.inputs.get("source"), r.inputs.get("part_sha256"), r.version)


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
            name = _title_of(snapshot.envelope)
            if name is not None:
                names[ref] = name
            current = _slots(snapshot.envelope)
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
    primary = canonical_plaud_id(snapshots[0].ref) if snapshots else None
    plaud_title = names.get(primary) if primary is not None else None
    if plaud_title is not None:
        seen = rec.plaud.title_seen if rec.plaud else None
        if rec.title_by in (None, "plaud"):
            if plaud_title != rec.title:
                title = plaud_title
            if plaud_title != seen:
                title_seen = plaud_title
        elif plaud_title != seen:
            difference = plaud_title
    # Privacy errs towards private: every title any snapshot carries, under either Plaud ID,
    # degraded snapshots and titles Plaud has since changed included.
    titles = [t for t in (_title_of(s.envelope) for s in ordered) if t is not None]
    add_private = not is_private(rec) and matches_auto_private(titles, auto_private)
    return Reconciled(outputs=tuple(outputs), title=title, title_seen=title_seen,
                      title_difference=difference, add_private=add_private,
                      held=tuple(held.values()), skipped=tuple(skipped))
```

- [ ] **Step 4: Reconcile through the `Writer`, and in `reindex`**

In `packages/core/src/recordings/writer.py`:
1. **Add** the imports (`Sequence` after `import uuid`, the rest with the `recordings` imports):
   ```python
   from collections.abc import Sequence
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
       """The fields Plaud owns (§9.1.1): the title while title_by is plaud or absent, the title
       last seen, and an auto-private tag, which is only ever added."""

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
       problem: str | None = None  # a fixed phrase: never exception text, which can carry a title

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
           owns (§9.1.1). Writes nothing when there is nothing new.

           Privacy first: Plaud's fields, an auto-private tag among them, land in one `mutate`
           before any output is published. A crash in between leaves a tagged recording whose
           outputs the next reconcile writes."""
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
               if result.title is not None or result.title_seen is not None or result.add_private:
                   self.mutate(recording_id, SetPlaudFields(result.title, result.title_seen,
                                                            result.add_private))
               written = sum(self._write_rendition_locked(folder, r) is not None
                             for r in result.outputs)
               return ReconcileReport(recording_id, written, result.held, result.skipped,
                                      result.title_difference is not None)
   ```
7. In `reindex`, **replace** everything from `count = self.rebuild_index(allow_empty=allow_empty)`
   to the end of the method (the `return ReindexReport(...)` statement) with:
   ```python
           reconciled = []
           for rec in list(self.archive.iter_recordings()):
               if not any(s.kind == "plaud" for s in rec.sources):
                   continue
               try:
                   reconciled.append(self.reconcile_plaud(rec.id))
               except (WriterError, KeyError) as exc:
                   # A fixed phrase, never the exception's text, which can carry a title.
                   problem = f"{type(exc).__name__}: not reconciled"
                   reconciled.append(ReconcileReport(rec.id, 0, (), (), False, problem=problem))
           count = self.rebuild_index(allow_empty=allow_empty)
           problems = tuple({"path": str(p.path), "message": p.message}
                            for p in self.archive.problems)
           return ReindexReport(recordings=count, adopted=tuple(adopted), invalid=tuple(invalid),
                                problems=problems, reconciled=tuple(reconciled))
   ```

In `config.example.toml`, **replace** the line `auto_private_patterns = ["^private"] …` with:
```toml
# Plaud titles matching these are tagged `private` (spec §7.4): Python regular expressions,
# searched ignoring case in every title a snapshot carries, as given and with Plaud's "MM-DD "
# date prefix stripped (as audio-router's normalize_title does). Read from stage 2a, by
# reconcile and the import. They only ever add `private`.
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
`inputs.part_sha256`. Its `recording.json` has `title_by: plaud` and `plaud.title_seen`, and no
`private` tag, because no demo title matches the patterns.

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/recordings packages/core/tests/test_reconcile.py config.example.toml demo/build.py demo/archive
git commit -m "feat(core): reconcile Plaud snapshots into outputs and the fields Plaud owns

A pure function over every complete snapshot in fetch order (spec §9.1.1): idempotent, version
part of an output's identity, removals held for review, per-Plaud-ID, the timing check recorded.
Privacy first: the auto-private tag lands before any output, and the patterns see every snapshot
title with Plaud's MM-DD prefix stripped. Notes are hashed normalised and rendered raw. reindex
runs it. Block rendering adapted from audio-router's vendor_blocks.py at 64612df (relicensed MIT).
The demo's Plaud outputs now come from a synthetic Plaud envelope through reconcile.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 8: Reading `audio-router`'s archive: the media probe and the import plan

**Checkpoint lens:** data integrity and write safety.

This task reads and plans. It writes nothing. The plan is the dry run (§9.3): it gives every
catalog row one disposition, says why for each row it can't place, and never names a title. Task 9
carries it out.

Four rulings of 2026-10-09 shape it (header decisions 12, 14 and 23):
- **Tags:** every tag in the catalog comes over, and a privacy label moves under `private/`. A
  privacy label, a non-empty `access` or the private tier makes a row `new-private`.
- **Orphan Plaud rows:** a Plaud row with audio but no snapshot and no time is `deferred-to-2b`. It
  is reported by its Plaud ID, and never `unplaced`.
- **The private tier:** it is matched by SHA-256, and a `.txt` with the same stem comes with it.
- **The consent list:** an ID on Dan's consent list is `consent-excluded`. It is never read past
  its ID, and only counted. `plan()` takes the list as a required argument, and Task 9 reads it
  from config, failing closed.

**Files:**
- Create: `packages/core/src/recordings/media.py`, `packages/core/src/recordings/sources/__init__.py`, `packages/core/src/recordings/sources/audio_router.py`
- Modify: `packages/core/tests/conftest.py` (the synthetic `audio-router` archive)
- Test: `packages/core/tests/test_media.py`, `packages/core/tests/test_import_plan.py`

**Interfaces:**
- Consumes:
  - `Index.find_by_source`, `Index.find_by_sha256` and `Index.sha256_of`, and `recordings.refs.canonical_plaud_id` (Task 4)
  - `recordings.models.is_private_tag` (Task 3), and `sha256_file` (`archive.py`)
  - the `plaud_env` fixture (Task 6)
- Produces:
  - **`recordings.media`:**
    - suffix sets: `AUDIO_SUFFIXES`, `VIDEO_SUFFIXES`, `CONTAINER_SUFFIXES`, `MEDIA_SUFFIXES`; and `DECODE_RATE = 16_000`
    - `@dataclass(frozen=True) MediaProbe(duration_ms: int | None, sample_rate: int | None, channels: int | None, has_video: bool = False, method: str = "")`, where `method` is `"wave"` or `"decoded"`
    - `decoded_ms(path: Path, *, ffmpeg: str = "ffmpeg", timeout: float = 600) -> int | None`
    - `probe(path: Path, *, ffprobe: str = "ffprobe", ffmpeg: str = "ffmpeg", timeout: float = 600) -> MediaProbe | None`
    - `media_kind(path: Path, probed: MediaProbe | None) -> Literal["audio", "video"] | None`
  - **`recordings.sources.audio_router`:**
    - constants:
      - `CATALOG`, `PROVENANCE`, `SOURCES = ("plaud", "recorder", "pocket")`, `TIME_SOURCES`
      - `REQUIRED_COLUMNS`, now with `tags`
      - `DISPOSITIONS = ("new", "new-private", "duplicate", "present", "no-audio", "deferred-to-2b", "consent-excluded", "unplaced")`
      - `IMPORTED = ("new", "new-private", "duplicate", "present")`: what a run imports
      - `PRIVATE_TAGS`, each recognised label to its one tag: `private`, `personal` and `voiceprints` → `private`; `therapy`, `counselling` and `counseling` → `private/therapy`; `journal`, `medical` and `students` → `private/<label>`. `PRIVACY_LABELS = frozenset(PRIVATE_TAGS)`
    - `class CatalogError(RuntimeError)`
    - `@dataclass(frozen=True) Row(uri, source, file_id, recorded_at_local, timezone, time_source, audio_rel, audio_sha256, dup_of, tags: tuple[str, ...], access)`
    - `@dataclass(frozen=True) Item(row: Row, disposition: str, reason: str = "", kind: str = "", audio: Path | None = None, sha256: str = "", recorded_at: datetime | None = None, timezone: str = "", time_source: str = "", snapshots: tuple[Path, ...] = (), renditions: tuple[Path, ...] = (), texts: tuple[Path, ...] = (), present_id: str | None = None, private: bool = False, tags: tuple[str, ...] = (), probed: MediaProbe | None = None)`
    - `@dataclass(frozen=True) Plan(root, items, ledger_only=(), unlisted=(), held_on_plaud=None, problems=())`, with:
      - `.counts() -> dict[str, int]` and `.unprobed() -> list[str]`
      - `.ok -> bool`: no problems, no unplaced row and nothing unprobed
      - `.report() -> dict`, with the keys `catalog_rows`, `by_disposition`, `private`, `unplaced`, `no_audio`, `deferred_to_2b`, `unprobed`, `ledger_only`, `unlisted`, `held_on_plaud` and `problems`
    - `map_tags(labels: Iterable[str]) -> tuple[str, ...]`
    - `read_catalog(root: Path) -> list[Row]`
    - `snapshot_order(path: Path) -> tuple[str, str]`
    - `plan(root: Path, index: Index | None = None, *, consent: Collection[str], default_timezone: str = "America/Vancouver") -> Plan`
  - **Test fixtures** in `conftest.py`:
    - `wav_bytes(seed: int, seconds: float = 2.0, rate: int = 16000, channels: int = 1) -> bytes`, a module-level helper
    - `AR_COLUMNS`
    - `class ARArchive`, with these methods:
      - `plaud(fid, *, envelopes=(), stamps=None, derived=None, audio=True, recorded_at=…, dup_of="", seconds=2.6, same_audio_as=None, tags=(), ext=".wav")`
      - `orphan(fid)`, `recorder()`, `pocket(uuid)` and `pocket_without_audio()`
      - `private(*, name="Aug 3 at 10-00", text=…, tags=("therapy",), recorded_at=…)`, which returns the row with its 64-hex `file_id`
      - `write_catalog()` and `tree_hash()`
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


def _stand_ins(tmp_path, monkeypatch, *, pcm_bytes, streams):
    """Stand-ins for ffprobe and ffmpeg on PATH. ffprobe prints what its `-of json` prints, with a
    container duration of 61.234 s that must never be used; ffmpeg "decodes" to `pcm_bytes` bytes
    of 16 kHz mono 16-bit PCM."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    answer = {"streams": streams, "format": {"duration": "61.234"}}
    (bin_dir / "ffprobe").write_text(f"#!/bin/sh\necho '{json.dumps(answer)}'\n", encoding="utf-8")
    (bin_dir / "ffmpeg").write_text(f"#!/bin/sh\nhead -c {pcm_bytes} /dev/zero\n", encoding="utf-8")
    for exe in bin_dir.iterdir():
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_a_wav_file_is_measured_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    assert probe(_wav(tmp_path / "a.wav")) == MediaProbe(duration_ms=1500, sample_rate=22050,
                                                         channels=2, method="wave")


def test_anything_else_needs_ffmpeg_and_is_never_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    (tmp_path / "a.mp3").write_bytes(b"ID3 not really")
    assert probe(tmp_path / "a.mp3") is None
    (tmp_path / "broken.wav").write_bytes(b"RIFF")
    assert probe(tmp_path / "broken.wav") is None


def test_the_duration_is_decoded_never_the_containers_claim(tmp_path, monkeypatch):
    # why: §9.1.1. reconcile's timing check compares Plaud's duration against this one, and an
    # mp3's or m4a's container duration is only an estimate. 64000 bytes of PCM is 2000 ms.
    _stand_ins(tmp_path, monkeypatch, pcm_bytes=64000,
               streams=[{"codec_type": "audio", "sample_rate": "44100", "channels": 1}])
    (tmp_path / "a.m4a").write_bytes(b"not really")
    assert probe(tmp_path / "a.m4a") == MediaProbe(duration_ms=2000, sample_rate=44100,
                                                   channels=1, has_video=False, method="decoded")


def test_a_failed_decode_or_a_file_with_no_audio_is_not_measured(tmp_path, monkeypatch):
    _stand_ins(tmp_path, monkeypatch, pcm_bytes=0,
               streams=[{"codec_type": "audio", "sample_rate": "44100", "channels": 1}])
    (tmp_path / "a.m4a").write_bytes(b"not really")
    assert probe(tmp_path / "a.m4a") is None
    (tmp_path / "bin" / "ffprobe").write_text(
        "#!/bin/sh\necho '{\"streams\": [{\"codec_type\": \"video\"}]}'\n", encoding="utf-8")
    assert probe(tmp_path / "a.m4a") is None


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

The duration is always measured on decoded audio, never taken from a container's claim, which for
mp3 and m4a is an estimate from the bitrate:
- **WAV:** the standard library's `wave` counts its frames (method `wave`).
- **Anything else,** or a WAV that `wave` can't read: ffmpeg decodes the first audio stream to
  16 kHz mono PCM and the samples are counted (method `decoded`). That is how audio-router measured
  it: its stages/audio.py decodes to 16 kHz mono WAV first (at 64612df). ffprobe gives the source's
  own sample rate and channel count, and whether it carries video. Both come from ffmpeg, which the
  image installs (https://ffmpeg.org/ffmpeg.html, https://ffmpeg.org/ffprobe.html).

Without ffmpeg and ffprobe the answer is None, never a guess, and `recordings doctor` reports them
missing. `MediaProbe.method` says how a duration was measured, and the import counts each method.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AUDIO_SUFFIXES = frozenset({".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac"})
VIDEO_SUFFIXES = frozenset({".mov", ".webm", ".mkv", ".ogv"})
CONTAINER_SUFFIXES = frozenset({".mp4"})  # audio or video: its streams decide
MEDIA_SUFFIXES = AUDIO_SUFFIXES | VIDEO_SUFFIXES | CONTAINER_SUFFIXES
DECODE_RATE = 16_000  # Hz, mono, 16-bit PCM: what audio-router decoded to before measuring


@dataclass(frozen=True)
class MediaProbe:
    duration_ms: int | None
    sample_rate: int | None
    channels: int | None
    has_video: bool = False
    method: str = ""  # how duration_ms was measured: "wave" or "decoded"


def _int(value: object) -> int | None:
    try:
        number = int(float(value))  # ffprobe prints numbers as strings
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _wave(path: Path) -> MediaProbe | None:
    try:
        with wave.open(str(path), "rb") as w:
            rate, frames, channels = w.getframerate(), w.getnframes(), w.getnchannels()
    except (wave.Error, EOFError, OSError):
        return None
    if not rate:
        return None
    return MediaProbe(duration_ms=round(frames * 1000 / rate), sample_rate=rate,
                      channels=channels or None, method="wave")


def _streams(exe: str, path: Path, timeout: float) -> list[dict] | None:
    try:
        proc = subprocess.run(
            [exe, "-v", "error", "-show_entries", "stream=codec_type,sample_rate,channels",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=timeout)
        data = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return [s for s in data.get("streams") or [] if isinstance(s, dict)]


def decoded_ms(path: Path, *, ffmpeg: str = "ffmpeg", timeout: float = 600) -> int | None:
    """Decode the first audio stream to 16 kHz mono PCM and count its samples. The PCM streams
    through a pipe and is only counted, so a long recording never sits in memory or on disk."""
    exe = shutil.which(ffmpeg)
    if exe is None:
        return None
    try:
        proc = subprocess.Popen(
            [exe, "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1",
             "-ar", str(DECODE_RATE), "-c:a", "pcm_s16le", "-f", "s16le", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    timer = threading.Timer(timeout, proc.kill)  # a decode that hangs is killed: None
    timer.daemon = True
    timer.start()
    total = 0
    try:
        while chunk := proc.stdout.read(1 << 16):
            total += len(chunk)
        code = proc.wait()
    finally:
        timer.cancel()
        proc.stdout.close()
    if code != 0 or total < 2:
        return None
    return round((total // 2) * 1000 / DECODE_RATE)


def probe(path: Path, *, ffprobe: str = "ffprobe", ffmpeg: str = "ffmpeg",
          timeout: float = 600) -> MediaProbe | None:
    path = Path(path)
    if path.suffix.lower() == ".wav":
        measured = _wave(path)
        if measured is not None:
            return measured
    exe = shutil.which(ffprobe)
    if exe is None:
        return None
    streams = _streams(exe, path, timeout)
    audio = next((s for s in streams or [] if s.get("codec_type") == "audio"), None)
    if audio is None:
        return None  # unreadable, or no audio stream to decode
    duration_ms = decoded_ms(path, ffmpeg=ffmpeg, timeout=timeout)
    if duration_ms is None:
        return None
    return MediaProbe(duration_ms=duration_ms, sample_rate=_int(audio.get("sample_rate")),
                      channels=_int(audio.get("channels")),
                      has_video=any(s.get("codec_type") == "video" for s in streams),
                      method="decoded")


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
Expected: 5 passed. The stand-in `ffprobe` and `ffmpeg` are shell scripts on `PATH`, so the tests
need no real ffmpeg.

- [ ] **Step 5: Write the synthetic `audio-router` archive and the failing plan tests**

Add to `packages/core/tests/conftest.py` (with `import csv`, `import hashlib`, `import io`,
`import wave` and `from pathlib import Path` at the top; Task 6 already imports `json`):
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
              same_audio_as: str | None = None, tags=(), ext=".wav") -> dict:
        """Snapshots in plaud/raw/<fid>/<stamp>.json (oldest first; a str is written as is), audio
        in plaud/audio/2026/08/<fid><ext> (WAV bytes, whatever the suffix), outputs in
        plaud/derived/<fid>/. `tags` are the row's authored labels (catalog/tags.jsonl)."""
        for i, env in enumerate(envelopes):
            stamp = stamps[i] if stamps else f"2026080{i + 1}T161000Z"
            self._write(f"plaud/raw/{fid}/{stamp}.json",
                        env if isinstance(env, str) else json.dumps(env, indent=1, ensure_ascii=False))
        for name, doc in (derived or {}).items():
            self._write(f"plaud/derived/{fid}/{name}",
                        json.dumps(doc, indent=1, ensure_ascii=False))
        rel, sha = "", ""
        if audio:
            rel = f"plaud/audio/2026/08/{fid}{ext}"
            if same_audio_as:
                sha = hashlib.sha256(self._write(rel, (self.root / same_audio_as).read_bytes())
                                     .read_bytes()).hexdigest()
            else:
                sha = self._audio(rel, seconds)
        return self._row(uri=f"plaud/{fid}", source="plaud", file_id=fid,
                         recorded_at_local=recorded_at, timezone="America/Vancouver",
                         time_source="start_at", duration_ms=str(int(seconds * 1000)),
                         audio_rel=rel, audio_sha256=sha, dup_of=dup_of,
                         tags=";".join(sorted(tags)))

    def orphan(self, fid: str) -> dict:
        """A Plaud row build_catalog.py builds from the audio alone: no snapshot, and no time."""
        rel = f"plaud/audio/2026/08/{fid}.wav"
        return self._row(uri=f"plaud/{fid}", source="plaud", file_id=fid, audio_rel=rel,
                         audio_sha256=self._audio(rel, 2.0), ledger_status="rendered")

    def private(self, *, name: str = "Aug 3 at 10-00", text: str | None = "Speaker 1: Hello.\n",
                tags=("therapy",), recorded_at="2026-08-03T10:00:00-07:00") -> dict:
        """The private tier as audio-router's catalog/private.jsonl registers it: a Recorder export
        that keeps its export name (a title), registered by its audio's SHA-256, with Recorder's
        prose transcript beside it as a .txt of the same stem. No raw/ and no derived/ folders,
        and the row's audio columns are empty, as build_catalog.py leaves them."""
        self._seed += 1
        data = wav_bytes(self._seed, 2.0)
        fid = hashlib.sha256(data).hexdigest()
        self._write(f"private/recorder-exports/{name}.wav", data)
        if text is not None:
            self._write(f"private/recorder-exports/{name}.txt", text)
        return self._row(uri=f"private/{fid}", source="private", file_id=fid,
                         recorded_at_local=recorded_at, timezone="America/Vancouver",
                         time_source="filename", duration_ms="2000", access="restricted",
                         tags=";".join(sorted(tags)))

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
import hashlib
import json
from datetime import datetime, timezone

import pytest

from recordings import media
from recordings.archive import RawSource
from recordings.sources.audio_router import (
    DISPOSITIONS,
    CatalogError,
    map_tags,
    plan,
    read_catalog,
)

A = "a" * 32
B = "b" * 32
C = "c" * 32
D = "d" * 32
O = "8" * 32  # an orphan: audio, but no snapshot and no time
T = "9" * 32  # tagged private in audio-router's catalog
X = "7" * 32  # on the consent list
X2 = "6" * 32  # on the consent list, and only in audio-router's ledger
UUID = "11111111-1111-4111-8111-111111111111"
T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
NONE = ()  # an empty consent list, for the tests about everything else
JA = "会議メモ / Q&A"  # Review Focus 4: a title outside ASCII


def full(ar, plaud_env):
    """Every kind of row audio-router's catalog holds (spec §9.3's mapping table)."""
    ar.plaud(A, envelopes=[plaud_env(A, name="A PRIVATE TITLE"),
                           plaud_env(A, name="Interview: Grace")], tags=("lecture",))
    ar.plaud(B, envelopes=[plaud_env(B)], same_audio_as=f"plaud/audio/2026/08/{A}.wav",
             dup_of=f"plaud/{A}")
    ar.plaud(C, envelopes=[plaud_env(C, name=JA)], recorded_at="2026-08-03T11:00:00-07:00")
    ar.plaud(D, envelopes=[plaud_env(D)], audio=False)  # no audio: arrives through the sync
    ar.plaud(T, envelopes=[plaud_env(T)], recorded_at="2026-08-04T11:00:00-07:00",
             tags=("private",))
    ar.plaud(X, envelopes=[plaud_env(X)], recorded_at="2026-08-05T11:00:00-07:00")
    ar.orphan(O)
    ar.recorder()
    ar.pocket(UUID, title="A PRIVATE TITLE")
    ar.pocket_without_audio()
    ar.private()
    ar.ledger_only = ["f" * 32, X2]
    ar.held = 2
    ar.write_catalog()


def test_the_dry_run_accounts_for_every_catalog_row(ar_archive, plaud_env):
    full(ar_archive, plaud_env)
    the_plan = plan(ar_archive.root, consent={X, X2})
    assert sorted(i.row.uri for i in the_plan.items) == sorted(r["uri"] for r in ar_archive.rows)
    assert the_plan.counts() == {"new": 4, "new-private": 2, "duplicate": 1, "present": 0,
                                 "no-audio": 2, "deferred-to-2b": 1, "consent-excluded": 1,
                                 "unplaced": 0}
    assert sum(the_plan.counts().values()) == len(ar_archive.rows)
    assert set(the_plan.counts()) == set(DISPOSITIONS)
    assert the_plan.ok and the_plan.problems == ()  # deferred rows never block a real run
    report = the_plan.report()
    assert report["catalog_rows"] == 11 and report["private"] == 2
    assert report["deferred_to_2b"] == [O] and report["unprobed"] == []
    assert report["ledger_only"] == ["f" * 32] and report["held_on_plaud"] == 2


def test_a_consent_listed_recording_is_never_read_and_only_counted(ar_archive, plaud_env):
    # why: §9.1, §9.3. Dan's consent list is a hard rule: the recording isn't imported, and no
    # report names its ID, either form of which may be on the list.
    full(ar_archive, plaud_env)
    the_plan = plan(ar_archive.root, consent={X, "of_" + X2})
    (excluded,) = [i for i in the_plan.items if i.disposition == "consent-excluded"]
    assert excluded.row.file_id == X and excluded.audio is None and excluded.sha256 == ""
    text = json.dumps(the_plan.report(), ensure_ascii=False)
    assert X not in text and X2 not in text


def test_the_dry_run_never_names_a_title_and_never_changes_the_source(ar_archive, plaud_env):
    # Review Focus 4, and §9.3: it reads audio-router's archive and never changes it.
    full(ar_archive, plaud_env)
    before = ar_archive.tree_hash()
    text = json.dumps(plan(ar_archive.root, consent={X, X2}).report(), ensure_ascii=False)
    for title in ("A PRIVATE TITLE", "Interview: Grace", "Getting Started", "getting_started",
                  "Pocket chat", "Jul 15", JA, "会議メモ", "Aug 3 at 10-00"):
        assert title not in text
    assert ar_archive.tree_hash() == before


def test_audio_routers_privacy_labels_make_a_recording_private(ar_archive, plaud_env):
    # why: §9.3 (Dan, 2026-10-09). The catalog's tags and access columns are audio-router's own
    # privacy labels: every tag comes over, and a privacy label moves under private/.
    ar_archive.plaud(A, envelopes=[plaud_env(A)], tags=("lecture",))
    ar_archive.plaud(B, envelopes=[plaud_env(B)], tags=("private",))  # the main tier, tagged
    ar_archive.plaud(C, envelopes=[plaud_env(C)])["access"] = "restricted"
    ar_archive.plaud(D, envelopes=[plaud_env(D)], tags=("lecture", "therapy"))
    ar_archive.write_catalog()
    by_id = {i.row.file_id: i for i in plan(ar_archive.root, consent=NONE).items}
    assert (by_id[A].disposition, by_id[A].tags, by_id[A].private) == ("new", ("lecture",), False)
    assert (by_id[B].disposition, by_id[B].tags) == ("new-private", ("private",))
    assert (by_id[C].disposition, by_id[C].tags) == ("new-private", ("private",))
    assert (by_id[D].disposition, by_id[D].tags) == ("new-private", ("lecture", "private/therapy"))
    assert map_tags(["medical", "journal", "students", "private", "lecture"]) == (
        "private/medical", "private/journal", "private/students", "private", "lecture")
    # One tag per meaning: therapy and counselling fold together, and the generic markers are
    # `private`.
    assert map_tags(["counseling", "therapy", "counselling", "personal", "voiceprints", "private"]) == (
        "private/therapy", "private")


def test_the_private_tier_is_found_by_hash_whatever_its_files_are_called(ar_archive):
    # why: audio-router's catalog/private.jsonl registers a Recorder export by its audio's
    # SHA-256, and the file keeps its export name. There are no raw/ or derived/ folders.
    row = ar_archive.private()
    ar_archive.write_catalog()
    (item,) = plan(ar_archive.root, consent=NONE).items
    assert (item.disposition, item.kind, item.private) == ("new-private", "recorder", True)
    assert item.sha256 == row["file_id"] and item.audio.name == "Aug 3 at 10-00.wav"
    assert [p.name for p in item.texts] == ["Aug 3 at 10-00.txt"]
    assert item.snapshots == () and item.renditions == ()
    assert item.tags == ("private", "private/therapy")


def test_each_row_it_cannot_place_says_why(ar_archive, plaud_env):
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    (ar_archive.root / f"plaud/audio/2026/08/{A}.wav").unlink()
    ar_archive.plaud(B, envelopes=[plaud_env(B)])
    (ar_archive.root / f"plaud/audio/2026/08/{B}.wav").write_bytes(b"damaged in the copy")
    ar_archive.plaud(C, envelopes=[plaud_env(C)])["time_source"] = "a sundial"
    ar_archive.plaud(D, envelopes=[plaud_env(D)], dup_of="plaud/" + "0" * 32)
    ar_archive.plaud(T, envelopes=[plaud_env(T)])["tags"] = "Not A Slug"
    hidden = ar_archive.private()["file_id"]
    (ar_archive.root / "private/recorder-exports/Aug 3 at 10-00.wav").unlink()
    ar_archive.rows.append({**ar_archive.rows[0], "uri": "zoom/z1", "source": "zoom"})
    ar_archive.write_catalog()
    the_plan = plan(ar_archive.root, consent=NONE)
    reasons = {u["uri"]: u["reason"] for u in the_plan.report()["unplaced"]}
    assert "missing from the copy" in reasons[f"plaud/{A}"]
    assert "damaged" in reasons[f"plaud/{B}"]
    assert "a sundial" in reasons[f"plaud/{C}"]
    assert "dup_of" in reasons[f"plaud/{D}"]
    assert "lowercase slug" in reasons[f"plaud/{T}"] and "Not A Slug" not in reasons[f"plaud/{T}"]
    assert "0 media files" in reasons[f"private/{hidden}"]
    assert "unknown source" in reasons["zoom/z1"]
    assert not the_plan.ok


def test_a_short_copy_is_a_problem(ar_archive, plaud_env):
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.write_catalog(provenance_rows=5)
    the_plan = plan(ar_archive.root, consent=NONE)
    assert the_plan.problems and "incomplete" in the_plan.problems[0] and not the_plan.ok


def test_the_catalog_must_be_audio_routers(ar_archive):
    with pytest.raises(CatalogError, match="catalog.csv is missing"):
        plan(ar_archive.root, consent=NONE)
    (ar_archive.root / "catalog").mkdir(parents=True)
    (ar_archive.root / "catalog" / "catalog.csv").write_text("uri,source\nx,y\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="missing columns"):
        read_catalog(ar_archive.root)


def test_recordings_on_disk_that_the_catalog_misses_are_reported_by_id(ar_archive, plaud_env):
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.write_catalog()
    ar_archive.plaud(B, envelopes=[plaud_env(B)])  # on disk, after the catalog was built
    ar_archive.plaud(C, envelopes=[plaud_env(C)], audio=False)  # only in raw/
    (ar_archive.root / "recorder" / "derived" / ("9" * 64)).mkdir(parents=True)  # only in derived/
    ar_archive.plaud(X, envelopes=[plaud_env(X)])  # on the consent list: never named
    ar_archive.pocket_without_audio("my_secret_slug")
    (ar_archive.root / "plaud/audio/2026/08/My secret title.mp3").write_bytes(b"ID3")
    the_plan = plan(ar_archive.root, consent={X})

    def surrogate(name):
        return hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]

    assert set(the_plan.unlisted) == {
        f"plaud/{B}", f"plaud/{C}", f"recorder/{'9' * 64}",
        f"pocket/{surrogate('my_secret_slug')}", f"plaud/{surrogate('My secret title')}"}
    text = json.dumps(the_plan.report(), ensure_ascii=False)
    assert "my_secret_slug" not in text and "My secret title" not in text and X not in text


def test_snapshots_keep_audio_routers_order_within_one_second(ar_archive, plaud_env):
    # why: audio-router suffixes same-second snapshots -01, -02, and "-" sorts before "." .
    ar_archive.plaud(A, envelopes=[plaud_env(A), plaud_env(A, name="second"), plaud_env(A, name="third")],
                     stamps=["20260801T161000Z", "20260801T161000Z-01", "20260801T161000Z-02"])
    ar_archive.write_catalog()
    (item,) = plan(ar_archive.root, consent=NONE).items
    assert [p.name for p in item.snapshots] == [
        "20260801T161000Z.json", "20260801T161000Z-01.json", "20260801T161000Z-02.json"]


def test_media_that_cant_be_measured_is_reported(ar_archive, plaud_env, monkeypatch):
    # why: §9.1.1. Without ffprobe and ffmpeg, anything but WAV has no decoded duration.
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.plaud(B, envelopes=[plaud_env(B)], ext=".m4a",
                     recorded_at="2026-08-02T09:00:00-07:00")
    ar_archive.write_catalog()
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    the_plan = plan(ar_archive.root, consent=NONE)
    assert the_plan.report()["unprobed"] == [f"plaud/{B}"] and not the_plan.ok
    assert {i.row.file_id: i.probed.method for i in the_plan.items if i.probed} == {A: "wave"}


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
    by_uri = {i.row.uri: i for i in plan(ar_archive.root, writer.index, consent=NONE).items}
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
    private/…                                         the private tier: its files keep their
                                                      export names, and catalog/private.jsonl
                                                      registers each by its audio's SHA-256
    catalog/catalog.csv, catalog/provenance.json      catalog@5

`catalog/catalog.csv` is the checklist: every row gets exactly one disposition, so the dry run
accounts for every row. Nothing here writes to the source, and no report names a title: the
catalog holds none, and this module never puts one in a reason. A row on Dan's consent list
(§9.1) is never read past its ID, and is reported only as a count.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from recordings.archive import sha256_file
from recordings.index import Index
from recordings.media import MEDIA_SUFFIXES, MediaProbe, probe
from recordings.models import is_private_tag
from recordings.refs import canonical_plaud_id

CATALOG = Path("catalog") / "catalog.csv"
PROVENANCE = Path("catalog") / "provenance.json"
REQUIRED_COLUMNS = ("uri", "source", "file_id", "recorded_at_local", "timezone", "time_source",
                    "audio_rel", "audio_sha256", "dup_of", "tags", "access")
SOURCES = ("plaud", "recorder", "pocket")
# audio-router's time_source, per source, as recordings' (§6.2); anything else is unplaced.
TIME_SOURCES = {
    ("plaud", "start_at"): "plaud", ("plaud", "created_at"): "plaud",
    ("recorder", "filename"): "metadata", ("recorder", "file mtime (inferred)"): "mtime",
    ("pocket", "recording_at"): "metadata", ("pocket", "created_at"): "ingest",
    ("private", "filename"): "metadata",
}
DISPOSITIONS = ("new", "new-private", "duplicate", "present", "no-audio", "deferred-to-2b",
                "consent-excluded", "unplaced")
IMPORTED = ("new", "new-private", "duplicate", "present")  # the dispositions a run imports
# audio-router's privacy labels: the protected markers in its sources/local_.py at 64612df
# (PROTECTED_SEGMENTS, and the markers it checks in file names only), and the one tag each
# becomes (Dan, 2026-10-09; spec §7.4, §9.3). Every marker is recognised, so a recording carrying
# any of them is private. The tags are trimmed to one per meaning: therapy and both spellings of
# counselling are one tag, and the generic markers are plain `private`.
PRIVATE_TAGS: dict[str, str] = {
    "private": "private",
    "personal": "private",
    "voiceprints": "private",  # audio-router's marker for voice data, not a topic
    "therapy": "private/therapy",
    "counselling": "private/therapy",  # Dan: therapy and counselling are the same
    "counseling": "private/therapy",
    "journal": "private/journal",
    "medical": "private/medical",
    "students": "private/students",
}
PRIVACY_LABELS = frozenset(PRIVATE_TAGS)
_TAG = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")  # audio-router's rule for catalog/tags.jsonl
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
    tags: tuple[str, ...]  # audio-router's authored labels (catalog/tags.jsonl)
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
    texts: tuple[Path, ...] = ()  # prose exports kept as they came: the private tier's .txt
    present_id: str | None = None
    private: bool = False
    tags: tuple[str, ...] = ()  # recordings tags, privacy labels already under private/
    probed: MediaProbe | None = None  # None: its duration couldn't be measured


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

    def unprobed(self) -> list[str]:
        """Rows to import whose media couldn't be measured: a real run refuses them all."""
        return [i.row.uri for i in self.items if i.disposition in IMPORTED and i.probed is None]

    @property
    def ok(self) -> bool:
        return (not self.problems and not self.unprobed()
                and not any(i.disposition == "unplaced" for i in self.items))

    def report(self) -> dict:
        """Counts, URIs and IDs: never a title or a transcript, and never a consent-listed ID."""
        return {
            "catalog_rows": len(self.items),
            "by_disposition": self.counts(),
            "private": sum(i.private for i in self.items if i.disposition in IMPORTED),
            "unplaced": [{"uri": i.row.uri, "reason": i.reason}
                         for i in self.items if i.disposition == "unplaced"],
            "no_audio": [i.row.uri for i in self.items if i.disposition == "no-audio"],
            # Plaud IDs, so stage 2b's sync can match them (Dan, 2026-10-09).
            "deferred_to_2b": [i.row.file_id for i in self.items
                               if i.disposition == "deferred-to-2b"],
            "unprobed": self.unprobed(),
            "ledger_only": list(self.ledger_only),
            "unlisted": list(self.unlisted),
            "held_on_plaud": self.held_on_plaud,
            "problems": list(self.problems),
        }


def map_tags(labels: Iterable[str]) -> tuple[str, ...]:
    """audio-router's tags as recordings tags (Dan, 2026-10-09). Every label comes over, and a
    privacy label becomes its one tag in PRIVATE_TAGS: `therapy` and both spellings of
    counselling become `private/therapy`, and `personal` becomes `private`."""
    out: list[str] = []
    for label in labels:
        tag = PRIVATE_TAGS.get(label, label)
        if tag not in out:
            out.append(tag)
    return tuple(out)


def _row(cells: dict) -> Row:
    values = {c: (cells.get(c) or "").strip() for c in REQUIRED_COLUMNS}
    labels = tuple(t.strip() for t in values.pop("tags").split(";") if t.strip())
    return Row(**values, tags=labels)


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
        rows = [_row(r) for r in reader]
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
        return "recorder"  # a Recorder export, registered by its audio's SHA-256
    if _UUID.fullmatch(fid):
        return "pocket"
    return ""


def _private_media(root: Path) -> dict[str, list[Path]]:
    """The private tier's media files by SHA-256, hashed once per plan. Its files keep their
    export names, so a hash is the reliable way to find one."""
    found: dict[str, list[Path]] = {}
    tier = root / "private"
    if tier.is_dir():
        for path in sorted(tier.rglob("*")):
            if (path.is_file() and not path.name.startswith(".")
                    and path.suffix.lower() in MEDIA_SUFFIXES):
                found.setdefault(sha256_file(path), []).append(path)
    return found


def _find_private(root: Path, fid: str, media_by_sha: dict[str, list[Path]]):
    """One private row's files: the one media file whose SHA-256 is its file ID (or, for an ID
    that isn't a hash, whose name is), and any `.txt` with the same stem beside it, which is
    Recorder's prose transcript. Returns (kind, media, texts), or a reason string."""
    if not (root / "private").is_dir():
        return "the catalog lists a private recording but the copy has no private tier"
    media = set(media_by_sha.get(fid, ()))
    media |= {p for paths in media_by_sha.values() for p in paths if p.stem == fid}
    if len(media) != 1:
        return f"{len(media)} media files in the private tier match {fid} (expected 1)"
    (found,) = media
    kind = _kind_from_id(fid)
    if not kind:
        return f"can't tell which source private recording {fid} came from"
    texts = tuple(sorted(p for p in found.parent.iterdir()
                         if p.is_file() and p.stem == found.stem and p.suffix.lower() == ".txt"))
    return kind, found, texts


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


def _place(root: Path, row: Row, *, by_uri: dict[str, Row], index: Index | None,
           default_tz: str, consent: frozenset[str],
           private_media: dict[str, list[Path]]) -> Item:
    if canonical_plaud_id(row.file_id) in consent:
        return Item(row, "consent-excluded")  # never read past its ID (§9.1)
    if row.source not in (*SOURCES, "private"):
        return Item(row, "unplaced", f"unknown source {row.source!r}")
    if not all(_TAG.fullmatch(label) for label in row.tags):
        return Item(row, "unplaced",
                    "a tag isn't a lowercase slug, as audio-router's catalog/tags.jsonl requires")
    if row.source == "plaud" and not row.recorded_at_local:
        snapshots, _ = _store_files(root, "plaud", row.file_id)
        if not snapshots:  # build_catalog.py's orphans: audio alone (Dan, 2026-10-09)
            return Item(row, "deferred-to-2b", "no snapshot and no recording time: stage 2b's "
                        "sync matches it by its Plaud ID", kind="plaud")
    when, zone, time_source, why = _when(row, default_tz)
    if when is None:
        return Item(row, "unplaced", why)
    tags = map_tags(row.tags)
    if row.source == "private" or row.access:  # the private tier, or audio-router's access tier
        tags = tuple(dict.fromkeys(("private", *tags)))
    private = any(is_private_tag(t) for t in tags)
    texts: tuple[Path, ...] = ()
    if row.source == "private":
        found = _find_private(root, row.file_id, private_media)
        if isinstance(found, str):
            return Item(row, "unplaced", found)
        kind, audio, texts = found
        snapshots, renditions = (), ()
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
                renditions=renditions, texts=texts, present_id=present, private=private,
                tags=tags, probed=probe(audio))


def _ids_on_disk(root: Path, source: str) -> set[str]:
    """Every name with something on disk, as audio-router's store.archived_ids reads them:
    audio/…/<id>.<ext>, and the folders raw/<id>/ and derived/<id>/."""
    names: set[str] = set()
    audio = root / source / "audio"
    if audio.is_dir():
        names |= {p.stem for p in audio.rglob("*") if p.is_file()
                  and not p.name.startswith(".") and p.suffix not in (".part", ".tmp")}
    for folder in ("raw", "derived"):
        base = root / source / folder
        if base.is_dir():
            names |= {p.name for p in base.iterdir()
                      if p.is_dir() and not p.name.startswith((".", "_"))}
    return names


def _report_id(name: str) -> str:
    """An ID as it is. A name that isn't one (a Pocket title slug, a title-named file) becomes
    build_catalog.py's surrogate, sha256(name)[:32], so no title reaches a report."""
    if _HEX32.fullmatch(name) or _HEX64.fullmatch(name) or _UUID.fullmatch(name):
        return name
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]


def _unlisted(root: Path, rows: list[Row], consent: frozenset[str]) -> list[str]:
    """Recordings on disk with no catalog row, by ID only, leaving out consent-listed IDs."""
    listed = {(r.source, r.file_id) for r in rows}
    out: set[str] = set()
    for source in ("plaud", "recorder"):
        for name in _ids_on_disk(root, source):
            fid = _report_id(name)
            if (source, fid) not in listed and canonical_plaud_id(fid) not in consent:
                out.add(f"{source}/{fid}")
    pocket_raw = root / "pocket" / "raw"
    if pocket_raw.is_dir():
        for path in pocket_raw.glob("*.json"):
            if path.stem.startswith("_"):
                continue
            fid = _report_id(path.stem)
            if ("pocket", fid) not in listed:
                out.add(f"pocket/{fid}")
    return sorted(out)


def plan(root: Path, index: Index | None = None, *, consent: Collection[str],
         default_timezone: str = "America/Vancouver") -> Plan:
    """The dry run (§9.3). `consent` is Dan's consent list (§9.1), which the caller reads
    fail-closed with `recordings.plaud.consent`. It has no default, so no caller forgets it."""
    root = Path(root)
    excluded = frozenset(canonical_plaud_id(c) for c in consent)
    rows = read_catalog(root)
    provenance, problems = _provenance(root)
    if isinstance(provenance.get("rows"), int) and provenance["rows"] != len(rows):
        problems.append(f"catalog.csv has {len(rows)} rows but provenance.json says "
                        f"{provenance['rows']}: the copy may be incomplete")
    by_uri = {r.uri: r for r in rows}
    wanted = any(r.source == "private" and canonical_plaud_id(r.file_id) not in excluded
                 for r in rows)
    private_media = _private_media(root) if wanted else {}
    # Rows that are nobody's duplicate first, so a duplicate always merges into its original.
    items = [_place(root, row, by_uri=by_uri, index=index, default_tz=default_timezone,
                    consent=excluded, private_media=private_media)
             for row in sorted(rows, key=lambda r: (bool(r.dup_of), r.uri))]
    ledger = [str(x) for x in provenance.get("ledger_only_no_media") or []]
    held = ((provenance.get("per_source") or {}).get("plaud") or {}).get("held_count")
    return Plan(root=root, items=tuple(items),
                ledger_only=tuple(sorted(x for x in ledger
                                         if canonical_plaud_id(x) not in excluded)),
                unlisted=tuple(_unlisted(root, rows, excluded)),
                held_on_plaud=held if isinstance(held, int) else None, problems=tuple(problems))
```

- [ ] **Step 8: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_import_plan.py packages/core/tests/test_media.py -v && uv run pytest`
Expected: 17 passed (12 and 5), and the full suite stays green, its count above Task 7's.

- [ ] **Step 9: Commit**

```bash
git add packages/core/src/recordings/media.py packages/core/src/recordings/sources packages/core/tests/conftest.py packages/core/tests/test_media.py packages/core/tests/test_import_plan.py
git commit -m "feat(core): the audio-router import plan: every catalog row gets one disposition

A read-only dry run over audio-router's catalog@5 (spec §9.3), matching by Plaud ID before content
hash. Its tags come over, privacy labels under private/; the private tier is found by SHA-256;
orphan Plaud rows are deferred to 2b and consent-listed IDs only counted. Durations are decoded
(wave for WAV, ffmpeg otherwise), never a container's claim, and the method is kept. The
synthetic audio-router archive is modelled on its store.py and build_catalog.py.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `recordings import-audio-router`

**Checkpoint lens:** data integrity and write safety.

**Files:**
- Create: `packages/core/src/recordings/sources/ar_outputs.py`, `packages/core/src/recordings/disk.py`, `packages/core/src/recordings/plaud/consent.py`
- Modify:
  - `packages/core/src/recordings/sources/audio_router.py` (the run)
  - `archive.py` (`RawSource.suffix`)
  - `writer.py` (`Incoming.sha256`, and `_publish_raw` names a file by its suffix)
  - `cli.py` (`import-audio-router`)
  - `config.example.toml` (`[plaud] consent_list`, `[disk]`)
- Test: `packages/core/tests/test_import_run.py`, `packages/core/tests/test_disk.py`

**Interfaces:**
- Consumes:
  - from Task 8: `Plan`, `Item`, `IMPORTED`, `plan()`, `probe()`, `media_kind()`, `MediaProbe.method`
  - from Task 5 (5a): `Locks`, `LockTimeout`, `OPS_LOCK`
  - from Task 5 (5b): `Writer.add`, `Writer.write_rendition`, `Writer.mutate`, `Writer._write_rendition_locked`, `Incoming`, `MergeIncoming`, `WriterError`, `State.commit`, and the `writer` and `make_writer` fixtures; (5c): the CLI's `_writer`
  - from Task 7: `Writer.reconcile_plaud`, `Writer.auto_private`, and `recordings.plaud.reconcile.matches_auto_private(titles: Iterable[str], patterns: Sequence[str] = DEFAULT_AUTO_PRIVATE) -> bool`
  - from Task 6: `sha256_of`, `canonical_json`, and `recordings.plaud.normalise.meta_fetched_at(envelope) -> datetime | None`
  - from Task 4: `canonical_plaud_id`, `Index`, `IndexRefused` (which `Writer.add` can raise); from Task 3: `TagRef`, `Rendition`
  - from Task 2: `read_sentinel`, `copy_file_synced`
- Produces:
  - **`recordings.plaud.consent`:**
    - `ENTRY_RE`, `class ConsentListError(RuntimeError)`
    - `consent_list_path(data: dict) -> Path`
    - `load_consent_list(path: Path) -> frozenset[str]`
  - **`recordings.archive.RawSource`** gains `suffix: str = ".json"`. A `.txt` payload is published as `source/<kind>-<stamp>.txt`.
  - **`recordings.writer.Incoming`** gains `sha256: str | None = None`, and `Writer.add` refuses media whose bytes hash differently.
  - **`recordings.sources.ar_outputs`:**
    - `IMPORT_VERSION = 1`, `SKIPPED: dict[str, str]`
    - `@dataclass(frozen=True) Mapped(rendition: Rendition | None = None, skipped: str | None = None, keep_raw: bool = False)`
    - `ar_stamp(path: Path) -> datetime | None`
    - `map_rendition(path: Path, doc: object) -> Mapped`
    - `pocket_outputs(doc: dict, raw: str, fetched_at: datetime) -> list[Rendition]`
  - **`recordings.disk`:**
    - `WARN_FREE_PERCENT = 20.0`, `STOP_FREE_PERCENT = 5.0`
    - `@dataclass(frozen=True) DiskStatus(path, marker, free_bytes, total_bytes, level, message)`, with `.free_percent`
    - `disk_thresholds(cfg: Config) -> dict[str, float]`
    - `disk_status(archive_root: Path, *, expected_uuid: str | None, warn: float = 20.0, stop: float = 5.0) -> DiskStatus`
  - **`recordings.sources.audio_router`:**
    - it holds `recordings.locks.OPS_LOCK` (Task 5a) for a real run; Task 11's `ops.run_job` holds the same key
    - `class ImportStopped(RuntimeError)`, with `.report`
    - `@dataclass RunReport`, with `.to_dict()` and the keys `created`, `merged`, `sources_added`, `renditions_added`, `plaud_outputs`, `held_removals`, `skipped_snapshots`, `skipped_outputs`, `measured` and `failed`
    - `run(plan: Plan, writer: Writer, *, now: datetime, disk: Callable[[], DiskStatus] | None = None, lock_timeout: float = 600) -> RunReport`. It holds `Locks(writer.state.locks_dir, timeout=lock_timeout).hold("ops")` throughout, and raises `LockTimeout` when the lock stays busy. It raises `ImportStopped` when the disk guard says stop, when any media can't be measured (before writing anything), and when `add` raises `IndexRefused`.
  - **CLI:** `recordings import-audio-router PATH [--dry-run] [--allow-unplaced] [--json]`. It reads `[plaud] consent_list`, failing closed with exit 78.
  - **Config:** `[plaud] consent_list`, and `[disk] warn_free_percent` and `stop_free_percent`.

What the run guarantees:
- **Privacy first (§7.4):** every private tag is in `Incoming.tags` when the recording is created. That covers the catalog's privacy labels and `access`, the private tier, and a Plaud title (any snapshot's, under either ID) that matches the auto-private patterns.
- **Measured media:** a real run refuses to start while any recording's media can't be measured. Without ffmpeg, that means any non-WAV file.
- **The planned bytes:** each `Incoming` carries the plan's SHA-256, and `add` refuses other bytes.
- **The ops lock:** the run holds it throughout, so a backup or the mirror never runs half-way through an import.
- **Fixed failure text:** `failed[].reason` is the exception's type plus a fixed phrase, never its message, which can carry a title. The full exception goes only to the debug log.
- **An archive that reads as empty stops the run:** `add` raises `IndexRefused` when its index rebuild finds no recordings, most likely an unmounted disk. The run reports that item in fixed words and stops, rather than going on.

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
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from recordings import media
from recordings.archive import Archive
from recordings.cli import main
from recordings.disk import DiskStatus
from recordings.index import Index, IndexRefused
from recordings.locks import Locks, LockTimeout
from recordings.plaud.consent import ConsentListError, consent_list_path, load_consent_list
from recordings.selfdoc import validate
from recordings.sources import audio_router
from recordings.sources.ar_outputs import map_rendition, pocket_outputs
from recordings.sources.audio_router import ImportStopped, plan, run
from recordings.state import State
from recordings.writer import Writer

A = "a" * 32
B = "b" * 32
C = "c" * 32
J = "9" * 32  # a recording whose title is outside ASCII
X = "7" * 32  # on the consent list
UUID = "11111111-1111-4111-8111-111111111111"
NOW = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
JA = "会議メモ / Q&A"  # Review Focus 4: a title outside ASCII
CONSENT = f"# Never mirror\n- `{X}` — excluded\n"

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
HUMAN = {"kind": "speakers", "engine": "human", "version": "m2+x+y", "file_id": A,
         "inputs": {"audio_sha256": "x"}, "meta": {"labels": 1},
         "payload": {"names": {"SPEAKER_00": "Ada"}}}  # audio-router's relabel: names Dan typed
PLAUD_TURNS = {"kind": "turns", "engine": "plaud", "version": "v1", "inputs": {}, "payload": {}}
EMBEDDINGS = {"kind": "embeddings", "engine": "pyannote", "version": "x", "inputs": {},
              "payload": {}}
HUMAN_FILE = "speakers-human-m2+x+y-20260801T170600Z.json"


def build(ar, plaud_env):
    ar.plaud(A, envelopes=[plaud_env(A, name="Week 4"), plaud_env(A, name="Week 4, edited")],
             tags=("lecture",),
             derived={"words-mlx_whisper-large_v3_turbo@a4aaeec-20260801T170000Z.json": WORDS,
                      "turns-pyannote-community_1@abc-20260801T170100Z.json": TURNS,
                      "merged-audio_router-m2+x+y-20260801T170200Z.json": MERGED,
                      "summary-local_qwen3.6_35b_a3b-qwen+p3-20260801T170300Z.json": SUMMARY,
                      "turns-plaud-v1-20260801T170400Z.json": PLAUD_TURNS,
                      "embeddings-pyannote-x-20260801T170500Z.json": EMBEDDINGS,
                      HUMAN_FILE: HUMAN})
    ar.plaud(B, envelopes=[plaud_env(B)], same_audio_as=f"plaud/audio/2026/08/{A}.wav",
             dup_of=f"plaud/{A}")
    ar.plaud(C, envelopes=[plaud_env(C)], audio=False)
    ar.plaud(J, envelopes=[plaud_env(J, name=JA)], recorded_at="2026-08-04T09:00:00-07:00")
    ar.recorder()
    ar.pocket(UUID)
    ar.private()
    ar.write_catalog()


def _fid(ar, source):
    return next(r["file_id"] for r in ar.rows if r["source"] == source)


def _files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_the_import_maps_every_kind_of_recording(ar_archive, plaud_env, writer):
    build(ar_archive, plaud_env)
    before = ar_archive.tree_hash()
    report = run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    assert report.failed == []
    assert (report.created, report.merged) == (5, 1)  # B's audio is A's: one recording, two IDs
    assert report.measured == {"wave": 6}  # every duration measured, and how
    assert ar_archive.tree_hash() == before  # §9.3: it never changes the source
    assert validate(writer.root) == []
    recs = {s.ref: r for r in writer.archive.iter_recordings() for s in r.sources}

    a = recs[A]
    assert {(s.kind, s.ref) for s in a.sources} == {
        ("plaud", A), ("plaud", B), ("audio_router", f"plaud/{A}"), ("audio_router", f"plaud/{B}")}
    assert a.title == "Week 4, edited" and a.title_by == "plaud" and a.plaud.title_seen
    assert [(t.tag, t.by) for t in a.tags] == [("lecture", "you")]
    assert (a.media.duration_ms, a.media.sample_rate, a.media.channels) == (2600, 16000, 1)
    outputs = {(r.kind, r.engine, r.note_type) for _, r in writer.archive.renditions(a.id)}
    assert ("transcript", "mlx-whisper", None) in outputs
    assert ("transcript", "audio-router", None) in outputs
    assert ("speakers", "pyannote", None) in outputs
    assert ("notes", "local/qwen3.6-35b-a3b", "audio-router-summary") in outputs
    assert ("transcript", "plaud", None) in outputs and ("notes", "plaud", "plaud-summary") in outputs
    assert not any(r.kind == "speakers" and r.engine in ("plaud", "human")
                   for _, r in writer.archive.renditions(a.id))  # never outputs
    (typed,) = [s for s in a.sources if s.kind == "audio_router" and s.raw]
    assert typed.raw == "source/audio_router-20260801T170600Z.json"  # Dan's names, verbatim
    assert ((writer.archive.path_for(a.id) / typed.raw).read_bytes()
            == (ar_archive.root / "plaud" / "derived" / A / HUMAN_FILE).read_bytes())
    assert report.skipped_outputs == {
        "audio-router's Plaud outputs are rebuilt by reconcile": 1,
        "voice data never enters the archive (spec §7.6)": 1}

    assert (recs[J].title, recs[J].title_by) == (JA, "plaud")
    private = recs[_fid(ar_archive, "private")]
    assert [(t.tag, t.by) for t in private.tags] == [("private", "you"), ("private/therapy", "you")]
    assert (private.title, private.title_by) == ("Aug 3 at 10-00", "recorder")
    (txt,) = [s for s in private.sources if s.raw and s.raw.endswith(".txt")]
    assert txt.kind == "recorder" and txt.ref == private.media.sha256
    assert ((writer.archive.path_for(private.id) / txt.raw).read_bytes()
            == (ar_archive.root / "private/recorder-exports/Aug 3 at 10-00.txt").read_bytes())
    pocket = recs[UUID]
    assert (pocket.title, pocket.title_by) == ("Pocket chat", "pocket")
    assert pocket.time_source == "metadata"
    assert {r.engine for _, r in writer.archive.renditions(pocket.id)} == {"pocket"}
    recorder = recs[_fid(ar_archive, "recorder")]
    assert recorder.title_by == "recorder"
    assert writer.archive.renditions(recorder.id) == []  # its prose transcript stays in source/
    assert C not in recs  # no audio: not imported


def test_running_the_import_again_adds_nothing(ar_archive, plaud_env, writer):
    build(ar_archive, plaud_env)
    run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    files = _files(writer.root)
    again_plan = plan(ar_archive.root, writer.index, consent=())
    assert again_plan.counts()["present"] == 6
    again = run(again_plan, writer, now=datetime(2026, 10, 10, tzinfo=timezone.utc))
    assert (again.created, again.sources_added, again.renditions_added, again.plaud_outputs) == (0, 0, 0, 0)
    assert _files(writer.root) == files


KILL = r"""
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from recordings.index import Index
from recordings.sources.audio_router import plan, run
from recordings.state import State
from recordings.writer import MergeIncoming, Writer

root, state, index, source, point, now = sys.argv[1:7]
calls = []


def die():
    os._exit(137)  # as SIGKILL would: no except, no finally, no cleanup


def nth(n):
    calls.append(1)
    return len(calls) == n


if point == "copy":  # inside copy_file_synced, half-way through the second recording's media
    real_copy = shutil.copyfileobj

    def copy(fin, fout, length=0):
        if not nth(2):
            return real_copy(fin, fout, length)
        data = fin.read()
        fout.write(data[: len(data) // 2])
        fout.flush()
        die()

    shutil.copyfileobj = copy
elif point == "commit":  # State.commit, entered once os.replace has put a file in place
    real_commit = State.commit

    def commit(self, revision_id):
        if nth(2):
            die()
        return real_commit(self, revision_id)

    State.commit = commit
elif point == "merge":  # Writer.mutate for MergeIncoming: the merge's raw files are published
    real_mutate = Writer.mutate

    def mutate(self, recording_id, op):
        if isinstance(op, MergeIncoming):
            die()
        return real_mutate(self, recording_id, op)

    Writer.mutate = mutate
elif point == "reconcile":  # Writer.reconcile_plaud, once its first Plaud output is published
    real_write = Writer._write_rendition_locked

    def write(self, folder, rendition):
        written = real_write(self, folder, rendition)
        if rendition.engine == "plaud" and written is not None:
            die()
        return written

    Writer._write_rendition_locked = write

writer = Writer(Path(root), State.open(Path(state)), Index(Path(index)), writer_id="test")
run(plan(Path(source), writer.index, consent=()), writer, now=datetime.fromisoformat(now))
"""


@pytest.mark.parametrize("point", ["copy", "commit", "merge", "reconcile"])
def test_an_import_killed_anywhere_ends_as_a_clean_run_does(ar_archive, plaud_env, make_writer,
                                                             point):
    # Review Focus 2: killed inside the writer's own code, then run again with a fresh Writer and
    # the same `now`, the archive holds exactly the files a clean run gives, no source twice.
    build(ar_archive, plaud_env)
    clean = make_writer("clean")
    run(plan(ar_archive.root, clean.index, consent=()), clean, now=NOW)
    killed = make_writer("killed")
    child = subprocess.run(
        [sys.executable, "-c", KILL, str(killed.root), str(killed.state.path),
         str(killed.index.path), str(ar_archive.root), point, NOW.isoformat()],
        capture_output=True, timeout=300)
    assert child.returncode == 137, child.stderr.decode(errors="replace")  # it died there
    fresh = Writer(killed.root, State.open(killed.state.path), Index(killed.index.path),
                   writer_id="test")
    report = run(plan(ar_archive.root, fresh.index, consent=()), fresh, now=NOW)
    assert report.failed == []
    assert _files(fresh.root) == _files(clean.root)
    assert not (fresh.root / ".tmp").exists()
    for rec in fresh.archive.iter_recordings():
        keys = [(s.kind, s.ref, s.sha256) for s in rec.sources]
        assert len(keys) == len(set(keys)), rec.id
    assert validate(fresh.root) == []


def test_a_degraded_or_unreadable_snapshot_is_kept_but_never_used(ar_archive, plaud_env, writer):
    # Review Focus 3.
    ar_archive.plaud(A, envelopes=[plaud_env(A), plaud_env(A, link_error=True), "{not json"])
    ar_archive.write_catalog()
    report = run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
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
        run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW, disk=lambda: stop)
    assert caught.value.report.created == 0
    assert list(writer.archive.iter_recordings()) == []


def test_a_real_run_refuses_media_it_cannot_measure(ar_archive, plaud_env, writer, monkeypatch):
    # why: §9.1.1. Reconcile stores its timing check for good, so an import without a decoded
    # duration would store it without one. The run stops before it writes anything.
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.plaud(B, envelopes=[plaud_env(B)], ext=".m4a",
                     recorded_at="2026-08-02T09:00:00-07:00")
    ar_archive.write_catalog()
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    with pytest.raises(ImportStopped, match="can't be measured for 1 of 2"):
        run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    assert list(writer.archive.iter_recordings()) == []


def test_privacy_is_in_place_when_a_recording_is_created(ar_archive, plaud_env, writer,
                                                          monkeypatch):
    # why: §7.4. A private tag is never a second write after the content: a catalog label, and a
    # Plaud title that matches the auto-private patterns (in any snapshot, date prefix and all),
    # are both in Incoming.tags when the recording is created.
    ar_archive.plaud(A, envelopes=[plaud_env(A)], tags=("private",))  # the main tier, tagged
    ar_archive.plaud(B, envelopes=[plaud_env(B, name="10-06 Private catch-up"),
                                   plaud_env(B, name="Catch-up")],
                     recorded_at="2026-08-02T09:00:00-07:00")
    ar_archive.write_catalog()
    seen = {}
    real_add = Writer.add

    def add(self, incoming):
        seen[incoming.sources[0].ref] = [(t.tag, t.by) for t in incoming.tags]
        return real_add(self, incoming)

    monkeypatch.setattr(Writer, "add", add)
    report = run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    assert report.failed == []
    assert seen == {A: [("private", "you")], B: [("private", "auto")]}


def test_audio_that_changed_after_the_plan_is_refused(ar_archive, plaud_env, writer):
    # why: the plan hashed the audio. The run passes that hash, and add refuses other bytes.
    ar_archive.plaud(A, envelopes=[plaud_env(A)])
    ar_archive.write_catalog()
    the_plan = plan(ar_archive.root, writer.index, consent=())
    (ar_archive.root / f"plaud/audio/2026/08/{A}.wav").write_bytes(b"RIFF changed after the plan")
    report = run(the_plan, writer, now=NOW)
    assert report.failed == [{"uri": f"plaud/{A}", "reason": "WriterError: the writer refused it"}]
    assert list(writer.archive.iter_recordings()) == []


def test_a_failure_is_reported_in_fixed_words(ar_archive, plaud_env, writer, monkeypatch):
    # Review Focus 4: an exception's text can carry a title, so a report gives its type and a
    # fixed phrase, never its message.
    build(ar_archive, plaud_env)

    def refuse(self, incoming):
        raise ValueError(f"input_value={incoming.title!r}")

    monkeypatch.setattr(Writer, "add", refuse)
    report = run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    assert {f["reason"] for f in report.failed} == {"ValueError: a value did not validate"}
    text = json.dumps(report.to_dict(), ensure_ascii=False)
    for title in (JA, "会議メモ", "Week 4", "Pocket chat", "Aug 3 at 10-00", "Jul 15"):
        assert title not in text


def test_an_archive_that_reads_as_empty_stops_the_import(ar_archive, plaud_env, writer,
                                                          monkeypatch):
    # why: §6.7. add refuses when the archive reads as empty (its disk unmounted, most likely),
    # and the run stops there, in fixed words, rather than going on into it.
    build(ar_archive, plaud_env)

    def unmounted(self, incoming):
        raise IndexRefused(f"no recordings under the archive while adding {incoming.title!r}")

    monkeypatch.setattr(Writer, "add", unmounted)
    with pytest.raises(ImportStopped, match="reads as empty") as caught:
        run(plan(ar_archive.root, writer.index, consent=()), writer, now=NOW)
    assert [f["reason"] for f in caught.value.report.failed] == [
        "IndexRefused: the archive reads as empty or unreadable"]


def test_the_import_holds_the_ops_lock(ar_archive, plaud_env, writer):
    # why: the backup loop and the CLI's jobs share the "ops" lock (Task 11), so no snapshot or
    # mirror run lands half-way through an import.
    build(ar_archive, plaud_env)
    the_plan = plan(ar_archive.root, writer.index, consent=())
    with Locks(writer.state.locks_dir, timeout=0).hold("ops"):
        with pytest.raises(LockTimeout):
            run(the_plan, writer, now=NOW, lock_timeout=0.1)
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
    assert map_rendition(tmp_path / HUMAN_FILE, HUMAN).keep_raw
    assert map_rendition(tmp_path / "words-x-v-nostamp.json", WORDS).skipped
    assert map_rendition(tmp_path / "x-20260801T170000Z.json", ["not", "a", "dict"]).skipped
    assert map_rendition(tmp_path / "x-20260801T170000Z.json", {**WORDS, "kind": "novel"}).skipped


def test_untimed_turns_words_and_segments_are_kept(tmp_path):
    # why: audio-router keeps an untimed word where it arrived, and one can open a merged turn
    # (its MergedTurn has a nullable start and end). Its text must survive, as reconcile keeps
    # Plaud's untimed segments.
    merged = {**MERGED, "payload": {"turns": [
        {"speaker": "SPEAKER_UNKNOWN", "start": None, "end": None, "text": "Um",
         "words": [{"word": "Um", "start": None, "end": None}], "inferred": False},
        *MERGED["payload"]["turns"]]}}
    out = map_rendition(tmp_path / "merged-x-v-20260801T170200Z.json", merged).rendition
    assert [(s["text"], s["start"], s["end"]) for s in out.payload["segments"]] == [
        ("Um", 0.0, 0.0), ("Hello there.", 0.0, 1.5)]
    assert out.payload["segments"][0]["words"] == [{"word": "Um", "start": 0.0, "end": 0.0}]
    assert (out.meta["untimed_segments"], out.meta["untimed_words"]) == (1, 1)
    words = {**WORDS, "payload": {**WORDS["payload"], "segments": [
        *WORDS["payload"]["segments"], {"id": 2, "start": None, "end": None, "text": " Bye."}]}}
    out = map_rendition(tmp_path / "words-x-v-20260801T170000Z.json", words).rendition
    assert [(s["text"], s["start"]) for s in out.payload["segments"]] == [
        ("Hello there.", 0.0), ("Hi.", 1.5), ("Bye.", 2.6)]
    assert out.meta["untimed_segments"] == 1


def test_pocket_segments_in_milliseconds_are_recognised():
    doc = {"duration": 2.0, "transcript": {"segments": [{"start": 0, "end": 1500, "text": "Hi"}]},
           "summarizations": {}}
    (transcript,) = pocket_outputs(doc, "source/pocket-x.json", NOW)
    assert transcript.payload["segments"][0]["end"] == 1.5 and transcript.meta["time_unit"] == "ms"


def test_the_consent_list_is_read_as_audio_router_reads_it(tmp_path):
    path = tmp_path / "consent.md"
    path.write_text("# Never mirror\n"
                    f"- `{X}` — a recording Dan excluded\n"
                    f"{'6' * 32}\n"
                    f"We kept {'5' * 32} on purpose, so it is named in a sentence.\n"
                    f"# - {'4' * 32} is a comment\n", encoding="utf-8")
    assert load_consent_list(path) == {X, "6" * 32}
    assert consent_list_path({"plaud": {"consent_list": str(path)}}) == path


@pytest.mark.parametrize("content", [None, "", "No IDs here.\n"])
def test_the_consent_list_fails_closed(tmp_path, content):
    # why: §9.1. A missing, empty or ID-less list stops the import; it never means "exclude
    # nothing". So does an unset [plaud] consent_list.
    path = tmp_path / "consent.md"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(ConsentListError):
        load_consent_list(path)
    with pytest.raises(ConsentListError, match="consent_list"):
        consent_list_path({})


def _cli_config(tmp_path, monkeypatch, *, consent: str | None = CONSENT):
    text = (f'[archive]\npath = "{tmp_path / "archive"}"\nwriter_id = "test"\n'
            f'[state]\npath = "{tmp_path / "state"}"\n[index]\npath = "{tmp_path / "index.db"}"\n')
    if consent is not None:
        (tmp_path / "consent.md").write_text(consent, encoding="utf-8")
        text += f'[plaud]\nconsent_list = "{tmp_path / "consent.md"}"\n'
    cfg = tmp_path / "config.toml"
    cfg.write_text(text, encoding="utf-8")
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX"):
        monkeypatch.delenv(name, raising=False)


def test_the_cli_dry_run_then_import(ar_archive, plaud_env, tmp_path, monkeypatch, capsys):
    build(ar_archive, plaud_env)
    _cli_config(tmp_path, monkeypatch)
    assert main(["init", "--json"]) == 0
    capsys.readouterr()
    archive = tmp_path / "archive"
    before = sorted(p.relative_to(archive) for p in archive.rglob("*"))  # init's own files
    assert main(["import-audio-router", str(ar_archive.root), "--dry-run", "--json"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True and dry["by_disposition"]["new"] == 4
    assert sorted(p.relative_to(archive) for p in archive.rglob("*")) == before  # wrote nothing
    assert main(["import-audio-router", str(ar_archive.root), "--json"]) == 0
    done = json.loads(capsys.readouterr().out)
    assert done["imported"]["created"] == 5
    text = json.dumps(done, ensure_ascii=False)  # Review Focus 4
    for title in ("A PRIVATE TITLE", "Week 4", JA, "会議メモ", "Aug 3 at 10-00"):
        assert title not in text


def test_the_cli_reads_the_consent_list_and_fails_closed(ar_archive, plaud_env, tmp_path,
                                                         monkeypatch, capsys):
    build(ar_archive, plaud_env)
    ar_archive.plaud(X, envelopes=[plaud_env(X)], recorded_at="2026-08-05T09:00:00-07:00")
    ar_archive.write_catalog()
    _cli_config(tmp_path, monkeypatch, consent=None)
    assert main(["init", "--json"]) == 0
    capsys.readouterr()
    assert main(["import-audio-router", str(ar_archive.root), "--dry-run", "--json"]) == 78
    assert "consent_list" in json.loads(capsys.readouterr().out)["error"]
    _cli_config(tmp_path, monkeypatch, consent="")
    assert main(["import-audio-router", str(ar_archive.root), "--json"]) == 78
    assert "empty" in json.loads(capsys.readouterr().out)["error"]
    _cli_config(tmp_path, monkeypatch)
    assert main(["import-audio-router", str(ar_archive.root), "--dry-run", "--json"]) == 0
    dry = capsys.readouterr().out
    assert json.loads(dry)["by_disposition"]["consent-excluded"] == 1 and X not in dry
    assert main(["import-audio-router", str(ar_archive.root), "--json"]) == 0
    assert X not in capsys.readouterr().out
    refs = {s.ref for r in Archive(tmp_path / "archive").iter_recordings() for s in r.sources}
    assert X not in refs


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
    assert json.loads(capsys.readouterr().out)["imported"]["created"] == 5


def test_the_run_module_reports_counts_only():
    assert set(audio_router.RunReport().to_dict()) == {
        "created", "merged", "sources_added", "renditions_added", "plaud_outputs",
        "held_removals", "skipped_snapshots", "skipped_outputs", "measured", "failed"}
```

The kill-point test runs the import in a child process, which dies with `os._exit(137)` at one of
four points, each inside real code:
- in `copy_file_synced`, half-way through a recording's media
- in `State.commit`, entered once an `os.replace` has put a file in place
- in `Writer.mutate` for `MergeIncoming`, once the merge's raw files are published
- in `Writer.reconcile_plaud`, once its first Plaud output is published, after its `SetPlaudFields`

`os._exit` runs no `except` and no `finally`, just like a real kill, so a half-copied `.tmp/`
assembly and a pending revision are really left behind. The test asserts the child's exit code,
137, which proves the point was reached.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_import_run.py packages/core/tests/test_disk.py -v`
Expected: FAIL at collection:
- `test_import_run.py` with `ModuleNotFoundError: No module named 'recordings.disk'`
- `test_disk.py` with `ImportError: cannot import name 'disk' from 'recordings'`

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

In `config.example.toml`, **add** after the `[index]` section (after its line
`path = "/srv/recordings/index/index.db"`):
```toml
[disk]                                  # stage 2a: the disk guard (spec §12.5)
warn_free_percent = 20                  # Status warns, and an alert goes out, below this
stop_free_percent = 5                   # new imports stop below this
```

- [ ] **Step 4: Write the consent list, the output mapping and the run**

`packages/core/src/recordings/plaud/consent.py`:
```python
"""Dan's consent list, "never mirror" (spec §9.1): Plaud IDs that never enter the archive.

The parser is audio-router's (archive/gate.py at 64612df, itself ported from the vault's sync): an
ID counts only when it opens a bullet or stands alone on a line, because the list names a recording
Dan KEPT in a sentence, and a looser parser would exclude that one. Lines starting with `#` are
skipped. It is read fail-closed: a missing, unreadable or empty list, or one with no IDs, is an
error, never "exclude nothing". The import (stage 2a) and the sync (stage 2b) both read it here.
"""

from __future__ import annotations

import re
from pathlib import Path

from recordings.refs import canonical_plaud_id

ENTRY_RE = re.compile(r"^\s*(?:[-*]\s*)?`?([0-9a-f]{32})`?\s*(?:[—–-]|$)")


class ConsentListError(RuntimeError):
    """The consent list can't be trusted, so nothing that depends on it may run."""


def consent_list_path(data: dict) -> Path:
    """`[plaud] consent_list` from config.toml. Unset is an error: there is no default list."""
    value = (data.get("plaud") or {}).get("consent_list")
    if not isinstance(value, str) or not value.strip():
        raise ConsentListError(
            "[plaud] consent_list is not set in config.toml. It names Dan's consent list, and "
            "the import refuses to run without it (spec §9.1).")
    return Path(value).expanduser()


def load_consent_list(path: Path) -> frozenset[str]:
    """The IDs on the list, in canonical form. Raises ConsentListError rather than guess."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConsentListError(
            f"the consent list {path} is missing: refusing, because a missing list would "
            "exclude nothing") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConsentListError(
            f"the consent list {path} can't be read ({type(exc).__name__}): refusing") from None
    if not text.strip():
        raise ConsentListError(
            f"the consent list {path} is empty (a placeholder, or a truncated copy?): refusing")
    ids = frozenset(canonical_plaud_id(m.group(1)) for line in text.splitlines()
                    if not line.lstrip().startswith("#")
                    for m in [ENTRY_RE.match(line)] if m)
    if not ids:
        raise ConsentListError(
            f"the consent list {path} holds no IDs, so it is the wrong file: refusing")
    return ids
```

In `config.example.toml`, in `[plaud]`, **add** after the line
`auto_private_patterns = ["^\\s*private", "interview:"]`:
```toml
# Dan's consent list, "never mirror" (spec §9.1): Plaud IDs that never enter the archive, one per
# bullet or line, as audio-router reads them. Read fail-closed from stage 2a by the import: an
# unset, missing, empty or ID-less list stops it. In Docker it is mounted read-only (runbook 3).
consent_list = "/consent/consent-list.md"
```

In `packages/core/src/recordings/archive.py`, in `RawSource`:
1. **Replace**:
   ```python
       payload: bytes | None = None  # written verbatim as source/<kind>-<stamp>.json
       fetched_at: datetime | None = None  # when the source returned it; names the file
   ```
   with:
   ```python
       payload: bytes | None = None  # written verbatim as source/<kind>-<stamp><suffix>
       fetched_at: datetime | None = None  # when the source returned it; names the file
       suffix: str = ".json"  # ".txt" keeps a prose export as it came (spec §9.3)
   ```
2. **Replace**:
   ```python
       def __post_init__(self) -> None:
           for name in ("added_at", "fetched_at"):
   ```
   with:
   ```python
       def __post_init__(self) -> None:
           ext = self.suffix[1:]
           if not (self.suffix.startswith(".") and ext.isascii() and ext.isalnum()):
               raise ValueError(f"RawSource.suffix {self.suffix!r} must look like .json or .txt")
           for name in ("added_at", "fetched_at"):
   ```

In `packages/core/src/recordings/writer.py`:
1. In `Incoming`, **replace**:
   ```python
       my_notes: str | None = None
       excluded_note_types: tuple[str, ...] = ()
   ```
   with:
   ```python
       my_notes: str | None = None
       excluded_note_types: tuple[str, ...] = ()
       sha256: str | None = None  # what the caller hashed (the import's plan); add refuses others
   ```
2. In `add`, **replace**:
   ```python
           sha = sha256_file(incoming.media)
           rid = make_id(incoming.recorded_at, sha)
   ```
   with:
   ```python
           sha = sha256_file(incoming.media)
           if incoming.sha256 is not None and sha != incoming.sha256:
               raise WriterError("the media changed after it was planned: its SHA-256 differs")
           rid = make_id(incoming.recorded_at, sha)
   ```
3. In `_publish_raw`, **replace**:
   ```python
               target = folder / "source" / f"{_slug(source.kind)}-{utc_stamp(when)}.json"
   ```
   with:
   ```python
               target = folder / "source" / f"{_slug(source.kind)}-{utc_stamp(when)}{source.suffix}"
   ```
   and, in its identical-bytes check, **replace**:
   ```python
               same = next((p for p in sorted(target.parent.glob(f"{target.stem}*.json"))
   ```
   with:
   ```python
               same = next((p for p in sorted(target.parent.glob(f"{target.stem}*{target.suffix}"))
   ```

`packages/core/src/recordings/sources/ar_outputs.py`:
```python
"""audio-router's outputs, and Pocket's payload, as recordings outputs (spec §9.3).

| audio-router                           | recordings                                          |
|----------------------------------------|-----------------------------------------------------|
| words, merged                          | transcript, keeping engine and version              |
| turns (pyannote)                       | speakers                                            |
| summary                                | notes, note type audio-router-summary               |
| speakers by engine human               | kept verbatim in source/: the names Dan typed       |
| anything whose engine is plaud         | skipped: reconcile rebuilds Plaud's outputs (§9.1.1) |
| fingerprint, embeddings                | skipped: voice data never enters the archive (§7.6)  |

A segment, turn or word with no time is kept, never dropped with its text: it sits at the
previous one's end, as reconcile places Plaud's untimed segments, and `meta` counts them
(`untimed_segments`, `untimed_words`). audio-router's merge keeps untimed words where they arrived,
and one can open a turn (its stages/merge.py and archive/schemas.py MergedTurn).

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
    keep_raw: bool = False  # keep the file verbatim in source/ instead (typed speaker names)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _timed(item: Any) -> bool:
    return (isinstance(item, dict) and _number(item.get("start")) and _number(item.get("end"))
            and 0 <= item["start"] <= item["end"])


def _times(rows: list[dict], start: float = 0.0) -> tuple[list[tuple[float, float]], int]:
    """Each row's (start, end), and how many had none: such a row sits at the previous row's end,
    as reconcile places an untimed Plaud segment."""
    out, untimed, previous = [], 0, start
    for row in rows:
        if _timed(row):
            begin, end = float(row["start"]), float(row["end"])
        else:
            untimed += 1
            begin = end = previous
        previous = end
        out.append((begin, end))
    return out, untimed


def ar_stamp(path: Path) -> datetime | None:
    """The UTC stamp a rendition's file name ends with (audio-router's store._rendition_stamp)."""
    try:
        return datetime.strptime(path.stem.rsplit("-", 1)[-1], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def _words(items: Any, start: float) -> tuple[list[dict], int]:
    """Every word of a merged turn, in order, the untimed ones included and counted."""
    rows = [w for w in items or [] if isinstance(w, dict)]
    times, untimed = _times(rows, start)
    return [{"word": str(w.get("word") or ""), "start": begin, "end": end}
            for w, (begin, end) in zip(rows, times)], untimed


def _words_transcript(payload: dict) -> tuple[dict, int] | None:
    """whisper's segments, each with the timed words whose middle falls inside it. An untimed
    word can't be placed by time; its text is still in its segment's text."""
    rows = [s for s in payload.get("segments") or [] if isinstance(s, dict)]
    if not rows:
        return None
    times, untimed = _times(rows)
    words = sorted(({"word": str(w.get("word") or ""), "start": float(w["start"]),
                     "end": float(w["end"])} for w in payload.get("words") or [] if _timed(w)),
                   key=lambda w: (w["start"] + w["end"]) / 2)
    out, i = [], 0
    for n, (seg, (start, end)) in enumerate(zip(rows, times)):
        last = n == len(rows) - 1
        inside = []
        while i < len(words):
            middle = (words[i]["start"] + words[i]["end"]) / 2
            if middle < end or last:
                if middle >= start or n == 0:
                    inside.append(words[i])
                i += 1
            else:
                break
        out.append({"start": start, "end": end, "text": str(seg.get("text") or "").strip(),
                    "words": inside})
    return {"segments": out}, untimed


def _merged_transcript(payload: dict) -> tuple[dict, int, int] | None:
    turns = [t for t in payload.get("turns") or [] if isinstance(t, dict)]
    if not turns:
        return None
    times, untimed = _times(turns)
    segments, untimed_words = [], 0
    for turn, (start, end) in zip(turns, times):
        words, missing = _words(turn.get("words"), start)
        untimed_words += missing
        segments.append({"start": start, "end": end, "text": str(turn.get("text") or "").strip(),
                         **({"speaker": str(turn["speaker"])} if turn.get("speaker") else {}),
                         "words": words})
    return {"segments": segments}, untimed, untimed_words


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


def _counted(common: dict, **counts: int) -> dict:
    """`common` with any non-zero untimed counts added to its meta."""
    return {**common, "meta": {**common["meta"], **{k: v for k, v in counts.items() if v}}}


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
    if kind == "speakers" and engine == "human":
        return Mapped(keep_raw=True)  # the names Dan typed (§9.3): verbatim, never an output
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
            built = _words_transcript(payload)
            if built is None:
                return Mapped(skipped="a transcript with no segments")
            body, untimed = built
            model = meta_in.get("model") if isinstance(meta_in.get("model"), str) else None
            return Mapped(Rendition(kind="transcript", model=model, payload=body,
                                    **_counted(common, untimed_segments=untimed)))
        if kind == "merged":
            built = _merged_transcript(payload)
            if built is None:
                return Mapped(skipped="a transcript with no segments")
            body, untimed, untimed_words = built
            return Mapped(Rendition(kind="transcript", payload=body,
                                    **_counted(common, untimed_segments=untimed,
                                               untimed_words=untimed_words)))
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
    """Pocket's transcript and summaries, from its raw payload. Times are read as seconds, or as
    milliseconds when the last timed segment ends past twice the duration (stage-2a plan,
    answered question 5). A segment with no time is kept and counted."""
    out = []
    transcript = doc.get("transcript") if isinstance(doc.get("transcript"), dict) else {}
    rows = [s for s in transcript.get("segments") or [] if isinstance(s, dict)]
    version = f"pocket@{IMPORT_VERSION}"
    if rows:
        duration = doc.get("duration")
        ends = [r["end"] for r in rows if _timed(r)]
        in_ms = _number(duration) and duration > 0 and bool(ends) and max(ends) > 2 * duration
        scale = 1000.0 if in_ms else 1.0
        times, untimed = _times(rows)
        segments = [{"start": start / scale, "end": end / scale,
                     "text": str(r.get("text") or "").strip(),
                     **({"speaker": str(r["speaker"])} if r.get("speaker") else {})}
                    for r, (start, end) in zip(rows, times)]
        meta: dict[str, Any] = {"time_unit": "ms" if in_ms else "s"}
        if untimed:
            meta["untimed_segments"] = untimed
        out.append(Rendition(kind="transcript", engine="pocket", model="pocket", version=version,
                             created_at=fetched_at,
                             inputs={"source": raw, "part_sha256": sha256_of(rows)},
                             meta=meta, payload={"segments": segments}))
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

In `packages/core/src/recordings/sources/audio_router.py`, **replace** the import block (from
`import csv` to `from recordings.refs import canonical_plaud_id`) with:
```python
import csv
import hashlib
import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from recordings.archive import RawSource, sha256_file
from recordings.disk import DiskStatus
from recordings.index import Index, IndexRefused
from recordings.locks import OPS_LOCK, Locks, LockTimeout
from recordings.media import MEDIA_SUFFIXES, MediaProbe, media_kind, probe
from recordings.models import TagRef, is_private_tag
from recordings.plaud.normalise import meta_fetched_at
from recordings.plaud.reconcile import matches_auto_private
from recordings.refs import canonical_plaud_id
from recordings.sources.ar_outputs import ar_stamp, map_rendition, pocket_outputs
from recordings.writer import Incoming, Writer, WriterError
```

Then **append** to the same file:
```python
_log = logging.getLogger(__name__)
# A fixed phrase per kind of failure. An exception's own text can carry a title (pydantic's
# input_value, a path built from a tab name), so a report never shows it (Review Focus 4).
_FAILURES = (
    (IndexRefused, "the archive reads as empty or unreadable"),
    (LockTimeout, "a lock stayed busy"),
    (WriterError, "the writer refused it"),
    (KeyError, "a recording folder is missing"),
    (OSError, "a file could not be read or written"),
    (ValueError, "a value did not validate"),
)


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
    measured: dict[str, int] = field(default_factory=dict)  # durations, by how they were measured
    failed: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class ImportStopped(RuntimeError):
    """The run stopped: the disk guard said stop, or media couldn't be measured. `.report` is
    what was done before it did."""

    def __init__(self, message: str, report: RunReport) -> None:
        super().__init__(message)
        self.report = report


def _failure(exc: BaseException) -> str:
    phrase = next((p for kind, p in _FAILURES if isinstance(exc, kind)), "it failed")
    return f"{type(exc).__name__}: {phrase}"


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _fetched_at(path: Path) -> datetime:
    """When audio-router fetched a snapshot: a legacy snapshot's own `_meta.fetched_at`, else the
    file's stamp. Pocket's flat file and a private-tier `.txt` have only their mtime."""
    legacy = meta_fetched_at(_read_json(path)) if path.suffix == ".json" else None
    if legacy is not None:
        return legacy
    try:
        return datetime.strptime(path.stem.partition("-")[0], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


def _titles(item: Item) -> list[str]:
    """Every title the item's snapshots carry, oldest first. They go only into recording.json and
    the auto-private check, never into a report."""
    key = "title" if item.kind == "pocket" else "name"
    docs = [_read_json(path) for path in item.snapshots]
    return [d[key].strip() for d in docs
            if isinstance(d, dict) and isinstance(d.get(key), str) and d[key].strip()]


def _title(item: Item) -> tuple[str, bool]:
    """(the title, whether the source supplied it): the newest snapshot's, else a private-tier
    export's own name, which is how Recorder titles a recording."""
    titles = _titles(item)
    if titles:
        return titles[-1], True
    audio = item.audio
    if item.row.source == "private" and audio is not None and audio.stem != item.row.file_id:
        return audio.stem, True
    return "Untitled recording", False


def _import_one(item: Item, writer: Writer, *, now: datetime, report: RunReport) -> None:
    sources = [RawSource(kind=item.kind, ref=item.row.file_id, added_at=now,
                         fetched_at=_fetched_at(path), payload=path.read_bytes())
               for path in item.snapshots]
    sources += [RawSource(kind=item.kind, ref=item.row.file_id, added_at=now,
                          fetched_at=_fetched_at(path), payload=path.read_bytes(),
                          suffix=path.suffix.lower())
                for path in item.texts]  # Recorder's prose transcript, kept as it came (§9.3)
    if not sources:  # the source's own ID is always kept, so a later sync can match it
        sources.append(RawSource(kind=item.kind, ref=item.row.file_id, added_at=now))
    sources.append(RawSource(kind="audio_router", ref=item.row.uri, added_at=now))
    renditions = []
    for path in item.renditions:
        mapped = map_rendition(path, _read_json(path))
        if mapped.rendition is not None:
            renditions.append(mapped.rendition)
        elif mapped.keep_raw:  # the speaker names Dan typed: verbatim in source/ (§9.3)
            sources.append(RawSource(kind="audio_router", ref=item.row.uri, added_at=now,
                                     fetched_at=ar_stamp(path), payload=path.read_bytes()))
        else:
            report.skipped_outputs[mapped.skipped] = report.skipped_outputs.get(mapped.skipped, 0) + 1
    # Privacy first (§7.4): every private tag is in place when the recording is created, never
    # a second write after its content.
    tags = [TagRef(tag=tag, by="you") for tag in item.tags]
    if (item.kind == "plaud" and not item.private
            and matches_auto_private(_titles(item), writer.auto_private)):
        tags.append(TagRef(tag="private", by="auto"))
    title, from_source = _title(item)
    probed = item.probed
    added = writer.add(Incoming(
        media=item.audio, recorded_at=item.recorded_at, timezone_name=item.timezone,
        time_source=item.time_source, title=title, title_by=item.kind if from_source else None,
        kind=media_kind(item.audio, probed) or "audio", sources=tuple(sources),
        duration_ms=probed.duration_ms, sample_rate=probed.sample_rate, channels=probed.channels,
        tags=tuple(tags), renditions=tuple(renditions), sha256=item.sha256))
    report.measured[probed.method] = report.measured.get(probed.method, 0) + 1
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
        disk: Callable[[], DiskStatus] | None = None, lock_timeout: float = 600) -> RunReport:
    """Import every placeable row. Re-runnable: what is already there is matched and skipped.

    It holds the ops lock throughout, so a backup or the mirror never runs half-way through it,
    and it refuses to start while any recording's media can't be measured (§9.1.1). One
    recording's failure is reported and the run goes on, except an archive that reads as empty,
    which stops it."""
    report = RunReport()
    todo = [item for item in plan.items if item.disposition in IMPORTED]
    unmeasured = sum(item.probed is None for item in todo)
    if unmeasured:
        raise ImportStopped(
            f"media can't be measured for {unmeasured} of {len(todo)} recordings: anything but "
            "WAV needs ffprobe and ffmpeg (spec §9.1.1). Nothing was imported.", report)
    with Locks(writer.state.locks_dir, timeout=lock_timeout).hold(OPS_LOCK):
        for item in todo:
            if disk is not None:
                status = disk()
                if status.level == "stop":
                    raise ImportStopped(status.message, report)
            try:
                _import_one(item, writer, now=now, report=report)
            except IndexRefused as exc:  # the archive reads as empty: unmounted, most likely
                _log.debug("importing %s stopped the run", item.row.uri, exc_info=True)
                report.failed.append({"uri": item.row.uri, "reason": _failure(exc)})
                raise ImportStopped("the archive reads as empty or unreadable (is its disk "
                                    "mounted?): the import stopped", report) from None
            except (WriterError, LockTimeout, KeyError, OSError, ValueError) as exc:
                _log.debug("importing %s failed", item.row.uri, exc_info=True)
                report.failed.append({"uri": item.row.uri, "reason": _failure(exc)})
    return report
```

- [ ] **Step 5: Add the command**

In `packages/core/src/recordings/cli.py`:
1. **Replace** Task 5c's `from recordings.index import IndexRefused` with
   `from recordings.index import Index, IndexRefused`, and **add** the imports:
   ```python
   from datetime import datetime, timezone

   from recordings.disk import disk_status, disk_thresholds
   from recordings.plaud.consent import ConsentListError, consent_list_path, load_consent_list
   from recordings.sources.audio_router import CatalogError, ImportStopped, plan, run
   ```
   Task 5c already imports `LockTimeout`, and Task 2 `read_sentinel` and `SentinelError`.
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
       try:  # fail-closed (§9.1): no list, an unreadable one or an empty one stops both runs
           consent = load_consent_list(consent_list_path(cfg.data))
       except ConsentListError as exc:
           return _fail(str(exc), args.json, 78)
       if args.dry_run:
           index = _matching_index(cfg)
           try:
               the_plan = plan(args.path, index, consent=consent,
                               default_timezone=cfg.default_timezone)
           except CatalogError as exc:
               return _fail(str(exc), args.json, 1)
           _emit({**the_plan.report(), "dry_run": True,
                  "matched_against_archive": index is not None}, args.json)
           return 0 if the_plan.ok else 1
       writer = _writer(args.json)
       if isinstance(writer, int):
           return writer
       try:
           thresholds = disk_thresholds(cfg)
           the_plan = plan(args.path, writer.index, consent=consent,
                           default_timezone=cfg.default_timezone)
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
       except LockTimeout:
           return _fail("another job holds the ops lock (a backup, the mirror or a check): run "
                        "the import again when it finishes", args.json, 75)
       _emit({**the_plan.report(), "imported": result.to_dict()}, args.json)
       return 1 if result.failed else 0
   ```
4. **Add** `"import-audio-router": cmd_import_audio_router,` to `commands`.

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_import_run.py packages/core/tests/test_disk.py -v && uv run pytest`
Expected: 30 passed (25 and 5), and the full suite stays green, its count above Task 8's.

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/recordings packages/core/tests/test_import_run.py packages/core/tests/test_disk.py config.example.toml
git commit -m "feat(core): recordings import-audio-router, re-runnable, with the disk guard

Maps audio-router's recordings and outputs per spec §9.3: Plaud outputs rebuilt by reconcile,
voice data skipped, typed speaker names and a Recorder .txt kept verbatim in source/, untimed
turns kept, duplicates merged. Every private tag is in place at creation. Consent-listed IDs are
skipped, the list read fail-closed. The run holds the ops lock, refuses unmeasured media and
bytes that changed since the plan, and reports failures in fixed words, so no title reaches a
report. Killed at four points inside the writer, a re-run ends as a clean run does.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 10: Cross-site writes, host names and demo isolation

**Checkpoint lens:** security and privacy.

The Host allow-list (stage 1) stops DNS rebinding. It doesn't stop a foreign page posting to the
app's real name (§15), or a page on the same host name at another port: stage 1 compares the
`Origin`'s host name only, so a page at `http://<host>:9999` passes the websocket check. From 2a:
- every websocket handshake, and every request that could change data, comes from the app's own
  origin exactly (`scheme://` plus the `Host` header) or from no browser at all
- none of them is marked `Sec-Fetch-Site: cross-site` or `same-site`
- every request that could change data carries exactly `Content-Type: application/json`
- every response carries `Cross-Origin-Resource-Policy: same-origin`, so another site can't embed
  `/media` or load the API's answers with a no-cors request

2a adds no write route to the UI, so the guard wraps the whole app, and every later route gets it
without opting in. Shiny's own file-upload POST would be refused by it; the app uses none. Stage
5's upload route sends `application/octet-stream` and adds that one type for that one path. It is
an allow-list of routes, written down now in `WriteGuard`'s docstring: each listed route also needs
a custom header, which forces a CORS preflight, and `Sec-Fetch-Site: same-origin`.

This task also carries three stage-1 items:
- `allowed_hosts` entries with a port or scheme are refused
- the demo guard test (§17), which also watches SQLite, directory listings, other programs and
  name lookups
- demo mode's temporary folders are cleaned up

**Files:**
- Modify: `packages/ui/src/recordings_ui/hosts.py` (`same_origin`, `sent_by_this_site`, `WriteGuard`; `HostGuard`'s websocket check), `app.py`, `settings.py`
- Modify: `packages/core/src/recordings/config.py` (`normalise_host`)
- Test: `packages/ui/tests/test_writeguard.py`, `packages/ui/tests/test_demo_guard.py`, `packages/ui/tests/test_hosts.py`, `packages/core/tests/test_config.py`, `packages/ui/tests/test_settings.py`

**Interfaces:**
- Consumes: `recordings_ui.hosts.HostGuard`, `host_name`, `_header` (stage 1), and `Settings` (Task 5c).
- Produces:
  - **`recordings_ui.hosts`:**
    - `same_origin(scope: Scope, origin: str) -> bool`: the origin equals the request's scheme plus its `Host` header
    - `sent_by_this_site(scope: Scope) -> bool`: `Sec-Fetch-Site` is absent, `same-origin` or `none`
    - `SAFE_METHODS`, and `class WriteGuard(app: ASGIApp, allowed: frozenset[str])`, which answers 403 for a cross-site request and 415 for any content type that isn't JSON, and adds `Cross-Origin-Resource-Policy: same-origin` to every HTTP response
    - `HostGuard`'s websocket check now uses `same_origin` and `sent_by_this_site`. `origin_name` is removed.
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
    with TestClient(WriteGuard(app, LOCAL_HOSTS), base_url="http://localhost:8000") as c:
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
    {"Origin": "http://localhost:9999"},  # the same name at another port: maybe another program
    {"Origin": "https://localhost:8000"},  # another scheme
    {"Origin": "http://127.0.0.1:8000"},  # an allowed name, but not the page's own origin
    {"Origin": "http://localhost:8000", "Sec-Fetch-Site": "cross-site"},
], ids=["cross-site", "same-site", "foreign-origin", "null-origin", "other-port", "other-scheme",
        "other-allowed-name", "mixed"])
def test_a_cross_site_write_is_refused(client, headers):
    # why: §15. A page on another site, or another port of this host, can post to the app's name.
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


def test_every_response_keeps_its_resources_to_this_origin(client):
    # why: §15. Without it, another site could embed /media in an <audio> tag, or load the API's
    # answers with a no-cors request, by the app's real name.
    refused = client.post("/api/thing", content=b"{}", headers={**JSON, "Sec-Fetch-Site": "cross-site"})
    for response in (client.get("/api/thing"), refused):
        assert response.headers["cross-origin-resource-policy"] == "same-origin"


def test_the_real_app_is_wrapped(demo_archive, demo_ids):
    app = create_app(Settings(archive=demo_archive, demo=True))
    with TestClient(app, base_url="http://localhost:8000") as c:
        assert c.post("/", content=b"{}", headers={**JSON, "Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert c.post("/healthz", json={}, headers={"Origin": "http://localhost:9999"}).status_code == 403
        assert c.post("/healthz", content=b"x", headers={"Content-Type": "text/plain"}).status_code == 415
        media = c.get(f"/media/{demo_ids['fdr-fireside-1']}", headers={"Range": "bytes=0-9"})
        assert media.status_code == 206
        assert media.headers["cross-origin-resource-policy"] == "same-origin"
        assert c.get("/healthz").headers["cross-origin-resource-policy"] == "same-origin"
    runtime.configure(None)
```

In `packages/ui/tests/test_hosts.py`, **replace**:
```python
@pytest.mark.parametrize("origin", ["http://localhost:8000", "http://127.0.0.1:8000",
                                    "http://[::1]:8000", "HTTP://LOCALHOST"])
def test_a_websocket_from_an_allowed_origin_is_accepted(origin):
    with guarded().websocket_connect("ws://localhost:8000/ws", headers={"Origin": origin}) as ws:
        assert ws.receive_text() == "hello"
```
with:
```python
@pytest.mark.parametrize("url, origin", [
    ("ws://localhost:8000/ws", "http://localhost:8000"),
    ("ws://127.0.0.1:8000/ws", "http://127.0.0.1:8000"),
    ("ws://[::1]:8000/ws", "http://[::1]:8000"),
    ("ws://localhost/ws", "HTTP://LOCALHOST"),
])
def test_a_websocket_from_its_own_origin_is_accepted(url, origin):
    # The page's own origin: the scheme, host name and port of the Host it connects to.
    with guarded().websocket_connect(url, headers={"Origin": origin}) as ws:
        assert ws.receive_text() == "hello"


@pytest.mark.parametrize("origin", ["http://localhost:9999", "https://localhost:8000",
                                    "http://127.0.0.1:8000"])
def test_a_websocket_from_another_port_scheme_or_name_of_this_host_is_refused(origin):
    # why: an origin is a scheme, a host name and a port (RFC 6454). A page at
    # http://localhost:9999 is another program, though its host name is allowed.
    assert _refused(guarded(), "ws://localhost:8000/ws", {"Origin": origin}) == 1008


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_a_websocket_the_browser_marks_as_another_sites_is_refused(site):
    assert _refused(guarded(), "ws://localhost:8000/ws",
                    {"Origin": "http://localhost:8000", "Sec-Fetch-Site": site}) == 1008
```
and **add** after `test_the_app_refuses_a_cross_site_websocket`:
```python
def test_the_app_refuses_a_websocket_from_another_port_of_this_host(app_client):
    # The council's finding: a page at http://localhost:9999 passed the host-name-only check.
    assert _refused(app_client, "ws://localhost:8000/websocket/",
                    {"Origin": "http://localhost:9999"}) == 1008
```

`packages/ui/tests/test_demo_guard.py`:
```python
"""Demo mode never touches real data (spec §17): no real config, archive, state or secret is
opened, no other program runs, and nothing looks up or connects to another machine. Python's
audit hooks see these whoever does them. SQLite opens its files from C, which the "open" event
never sees, so "sqlite3.connect" is watched too."""

import os
import socket
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient

from recordings.archive import Archive
from recordings_ui import runtime, settings as settings_module, views
from recordings_ui.app import create_app
from recordings_ui.settings import from_env

PATH_EVENTS = ("open", "sqlite3.connect", "os.scandir", "os.listdir")
ANY_EVENTS = ("socket.connect", "socket.getaddrinfo", "subprocess.Popen")
_events: list[tuple[str, str]] | None = None


def _hook(event: str, args: tuple) -> None:
    if _events is None:
        return
    if event in PATH_EVENTS:
        if args and isinstance(args[0], (str, bytes, os.PathLike)):
            _events.append((event, os.fsdecode(args[0])))
    elif event in ANY_EVENTS:
        _events.append((event, repr(args[:2])))


sys.addaudithook(_hook)  # stays for the session; idle unless a test switches it on


@contextmanager
def _watching() -> Iterator[list[tuple[str, str]]]:
    global _events
    _events = events = []
    try:
        yield events
    finally:
        _events = None


def test_the_hook_sees_sqlite_listings_other_programs_and_name_lookups(tmp_path):
    # The guard below is only as good as what the hook sees.
    with _watching() as events:
        sqlite3.connect(tmp_path / "x.db").close()
        os.listdir(tmp_path)
        list(os.scandir(tmp_path))
        subprocess.run([sys.executable, "-c", "pass"], check=True)
        socket.getaddrinfo("localhost", 80)
    assert ("sqlite3.connect", str(tmp_path / "x.db")) in events
    assert {e[0] for e in events} >= {"os.listdir", "os.scandir", "subprocess.Popen",
                                      "socket.getaddrinfo"}


def test_demo_mode_reads_no_real_paths_secrets_or_network(tmp_path, demo_ids):
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
    with _watching() as events:
        try:
            settings = from_env(environ, demo=True)
            with TestClient(create_app(settings), base_url="http://localhost") as client:
                assert client.get("/healthz").json() == {"ok": True, "demo": True}
                for rid in demo_ids.values():
                    assert client.get(f"/media/{rid}", headers={"Range": "bytes=0-9"}).status_code == 206
                with client.websocket_connect("ws://localhost/websocket/",
                                              headers={"Origin": "http://localhost"}):
                    pass  # a Shiny session starts
            archive = Archive(settings.archive)
            views.library_view(archive)
            for rid in demo_ids.values():
                views.recording_view(archive, rid)
        finally:
            runtime.configure(None)
    assert [e for e in events if e[0] in PATH_EVENTS and str(real) in e[1]] == []
    assert [e for e in events if e[0] in ANY_EVENTS] == []


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

Run: `uv run pytest packages/ui/tests/test_writeguard.py packages/ui/tests/test_hosts.py packages/ui/tests/test_demo_guard.py packages/ui/tests/test_settings.py packages/core/tests/test_config.py -v`
Expected: FAIL. `test_writeguard.py` stops at `ImportError: cannot import name 'WriteGuard'`. In
`test_hosts.py`, the three refused-origin cases and both `Sec-Fetch-Site` cases fail with
`Failed: DID NOT RAISE WebSocketDisconnect`, and so does
`test_the_app_refuses_a_websocket_from_another_port_of_this_host`. The host-name and demo cleanup
tests fail on their assertions, except the `allowed_hosts` test's empty-name case, which stage 1
already refuses. The two demo isolation tests pass already: they guard stage-1 behaviour.

- [ ] **Step 3: Write the guard and the host checks**

In `packages/ui/src/recordings_ui/hosts.py`:
1. In the module docstring, **replace** the line
   ``HostGuard refuses both: an unknown `Host`, and a websocket whose `Origin` names another site.``
   with:
   ```text
   HostGuard refuses both: an unknown `Host`, and a websocket whose `Origin` isn't exactly the
   app's own (scheme, name and port), or that the browser marks as sent by another site. WriteGuard
   does the same for every request that could change data, and keeps every response to this origin.
   ```
2. **Delete** the line `from urllib.parse import urlsplit` and the blank line after it, and **replace**
   `from starlette.types import ASGIApp, Receive, Scope, Send` with
   `from starlette.types import ASGIApp, Message, Receive, Scope, Send`.
3. **Replace** `origin_name` (the whole function) with:
   ```python
   def same_origin(scope: Scope, origin: str) -> bool:
       """Whether `origin` is exactly this request's own: its scheme, then its Host header as sent.

       An origin is a scheme, a host name and a port (RFC 6454). A page at http://localhost:9999 is
       another origin than the app at http://localhost:8000, and may be another program, though its
       host name is allowed. Browsers leave a default port out of both headers, so those still match.
       """
       host = (_header(scope, b"host") or "").strip().lower()
       scheme = scope.get("scheme", "http")
       scheme = {"ws": "http", "wss": "https"}.get(scheme, scheme)
       return bool(host) and origin.strip().lower() == f"{scheme}://{host}"


   def sent_by_this_site(scope: Scope) -> bool:
       """False when the browser says another site sent the request: `Sec-Fetch-Site` is anything
       but `same-origin` or `none` (a typed address). `same-site` is refused too: a sibling name
       under the same domain is another origin. With no header, Origin decides."""
       site = (_header(scope, b"sec-fetch-site") or "").strip().lower()
       return site in ("", "same-origin", "none")
   ```
4. In `HostGuard.__call__`, **replace**:
   ```python
               # No Origin means no browser sent it, so no website is behind it.
               origin = _header(scope, b"origin")
               origin_ok = origin is None or origin_name(origin) in self.allowed
   ```
   with:
   ```python
               # A browser always sends Origin on a websocket. It must be the app's own, exactly,
               # and the handshake not marked as sent by another site. No Origin means no browser
               # sent it, so no website is behind it.
               origin = _header(scope, b"origin")
               origin_ok = (origin is None or same_origin(scope, origin)) and sent_by_this_site(scope)
   ```
5. **Add** at the end of the file:
   ```python
   SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
   CORP = (b"cross-origin-resource-policy", b"same-origin")


   def _keep_to_this_origin(send: Send) -> Send:
       """Every response says `Cross-Origin-Resource-Policy: same-origin`: another site can't embed
       /media in an <audio> tag, or load the API's answers with a no-cors request (spec §15)."""
       async def wrapped(message: Message) -> None:
           if message["type"] == "http.response.start":
               message = {**message, "headers": [*message.get("headers", []), CORP]}
           await send(message)
       return wrapped


   class WriteGuard:
       """Every request that could change data (spec §15): from this app's own page, or from no
       browser at all, and exactly JSON. Every response also keeps to this origin (above).

       - **Who sent it.** A browser names the sending page in `Origin`, and marks a request another
         site sent with `Sec-Fetch-Site: cross-site` or `same-site`. Either refuses it, and so does
         an `Origin` that isn't exactly the app's own (same_origin). Without both headers, no browser
         sent it, so no website is behind it.
       - **What it carries.** `Content-Type: application/json` can't be sent cross-site without a
         CORS preflight, which this app never answers.
       - **Stage 5's uploads** will be the one exception: an allow-list of routes, written down here
         now. The upload route accepts `application/octet-stream`, on that path only, and only with
         a custom header (`X-Recordings-Upload: 1`, which forces a preflight) and
         `Sec-Fetch-Site: same-origin`. Nothing else is exempt.
       """

       def __init__(self, app: ASGIApp, allowed: frozenset[str]) -> None:
           self.app = app
           self.allowed = frozenset(h.lower() for h in allowed)

       async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
           if scope["type"] != "http":
               await self.app(scope, receive, send)
               return
           send = _keep_to_this_origin(send)
           if scope["method"] in SAFE_METHODS:
               await self.app(scope, receive, send)
               return
           origin = _header(scope, b"origin")
           own = origin is None or (same_origin(scope, origin)
                                    and host_name(_header(scope, b"host") or "") in self.allowed)
           if not (own and sent_by_this_site(scope)):
               await PlainTextResponse("cross-site request refused", status_code=403)(scope, receive, send)
               return
           if (_header(scope, b"content-type") or "").strip().lower() != "application/json":
               await PlainTextResponse("send Content-Type: application/json",
                                       status_code=415)(scope, receive, send)
               return
           await self.app(scope, receive, send)
   ```

In `packages/ui/src/recordings_ui/app.py`, **replace** `from recordings_ui.hosts import HostGuard`
with `from recordings_ui.hosts import HostGuard, WriteGuard`, and **replace** the last two lines of
`create_app` (the comment and `return HostGuard(api, settings.allowed_hosts)`) with:
```python
    # Around everything, the Shiny app and its websocket included (recordings_ui.hosts): first the
    # host names, then the cross-site write check and the same-origin resource policy.
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
git commit -m "fix(ui): exact-origin websockets and writes, no cross-site writes, CORP; host names; demo guard

WriteGuard wraps the whole app (spec §15), so every route that writes gets it: a write must come
from the app's own origin (scheme plus Host) or from no browser, never marked cross-site or
same-site by Sec-Fetch-Site, and carry exactly application/json. HostGuard's websocket check uses
the same exact origin, so another port of the same host is refused. Every response sends
Cross-Origin-Resource-Policy: same-origin. allowed_hosts with a port or scheme are refused. A test
proves demo mode opens no real path, starts no SQLite on one, lists no real folder, runs no other
program and looks up no name (spec §17; Python audit hooks), and the demo's temporary copy is
removed at exit.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Backups with restic, and the restore test

**Checkpoint lens:** operations.

The transport is rest-server with `--append-only` on the NAS (spec §15.1; Dan, 2026-10-09: the
NAS is ext4, so there are no Btrfs snapshots to undo a compromised server). Retention runs on the
NAS, so the template sets `[backup] prune = false`; SFTP stays as the fallback. This task also
makes the jobs safe to run from the CLI and the loop alike:
- **One job at a time.** `run_job` holds the ops lock (key `"ops"`), the same one the import
  takes (Task 9). It waits up to 600 s for it, as the CLI does; the loop (Task 13) passes
  `lock_timeout=0`, and a busy turn is "skipped: busy", not a failure.
- **Stale restic locks.** restic never treats another process's lock as stale on its own; only
  `restic unlock` removes one. It runs before every backup, check, forget and restore. CLI runs
  also pass `--retry-lock 5m`.
- **SIGTERM reaches restic.** In a CLI run, SIGTERM goes on to the running restic or rsync, so it
  stops cleanly and removes its lock, and then to any handler that was there before. Task 14 gives
  the backup service `stop_grace_period: 2m`.
- **Fixed failure text.** A failed job is recorded as its error's type and exit code only
  (`"BackupError; exit 1"`), never the message, which can name paths or carry a server's words.
- **The restore test** clears old `restore-*` copies first, checks the space, restores with
  `--verify`, and fails if its copy can't be removed afterwards. The weekly `check` reads a
  different quarter of the data each week.
- **restic in CI.** The pinned restic is installed in CI here, and the real-restic tests fail
  rather than skip when `RECORDINGS_REQUIRE_RESTIC=1`.

**Files:**
- Create: `packages/core/src/recordings/ssh.py`, `packages/core/src/recordings/backup.py`, `packages/core/src/recordings/ops.py`, `scripts/fetch_restic.py`
- Modify: `packages/core/src/recordings/state.py` (`ops_runs`, `Run`, `record_run`, `last_run`, `backup_to`), `config.py` (secrets: `file_only`, `secret_path`), `selfdoc.py` (`validate(deep=)`), `cli.py` (`backup …`, `validate --deep`)
- Modify: `config.example.toml` (`[nas]`, `[backup]`, the secrets list), `Makefile` (`restic`), `.github/workflows/ci.yml` (restic for the tests)
- Test: `packages/core/tests/test_backup.py`

**Interfaces:**
- Consumes:
  - from Task 2: `read_sentinel`, `State` (and its private `_connect("rw")`), and `write_bytes_atomic`-style durability
  - from Task 3: `validate`
  - from Task 5a: `Locks`, `LockTimeout`; `utc_stamp`
  - from Task 9: `disk_thresholds`, `WARN_FREE_PERCENT`, `STOP_FREE_PERCENT`
- Produces:
  - **`recordings.config`:**
    - `SecretSpec` gains `file_only: bool = False` and `optional: bool = False`
    - `secret_path(name, environ) -> Path | None`
    - new `SECRETS` entries: `restic_password` (`RESTIC_PASSWORD`), `rest_password` (`RESTIC_REST_PASSWORD`), `nas_ssh_key` (`RECORDINGS_NAS_SSH_KEY`, file only), `nas_known_hosts` (`RECORDINGS_NAS_KNOWN_HOSTS`, file only)
  - **`recordings.ssh`:**
    - `ssh_options(key: Path | None, known_hosts: Path | None, port: int | None) -> list[str]`, with the port as `-o Port=N`, which ssh, sftp and `rsync -e` all read, and `-o ConnectTimeout=10`
    - `@dataclass(frozen=True) Nas(ssh: str | None, port: int | None, key: Path | None, known_hosts: Path | None)`, with `.options() -> list[str]`
    - `nas_from_config(cfg, environ) -> Nas`
    - `nas_free_bytes(nas: Nas, path: str, *, runner=subprocess.run) -> int | None`: `df -Pk` over ssh, then sftp's `df`
  - **`recordings.backup`:**
    - constants `TAG = "recordings"`, `MILESTONE_TAGS = ("pre-import", "post-import")`
    - errors: `class BackupError(RuntimeError)`, with `.returncode: int | None` (a failed restic raises it, with restic's exit status); its subclasses `BackupRefused` (and `NasLowOnSpace(BackupRefused)`), `RestoreTestFailed` and `RestoreCopyLeftBehind`
    - `@dataclass(frozen=True) Keep(hourly=24, daily=14, weekly=8, monthly=12)`
    - `@dataclass(frozen=True) BackupConfig(repository, host, archive, state, paths, keep, prune, restic, cache_dir, restore_scratch, nas, nas_repo_path, min_nas_free_bytes, rest_username=None, cacert: Path | None = None, retry_lock: str | None = None)`
    - `from_config(cfg, environ) -> BackupConfig`, which refuses a repository URL with a password in it
    - environment and commands: `restic_env(bc, environ) -> dict[str, str]`, `restic_base(bc) -> list[str]`, `backup_command(bc, state_copy: Path, *, tags=()) -> list[str]`, `forget_command(bc) -> list[str]`, `unlock_command(bc) -> list[str]`, `check_command(bc, *, subset: str | None = None) -> list[str]`, `check_subset(now: datetime) -> str`, `restore_command(bc, target: Path) -> list[str]`, `init_command(bc) -> list[str]`
    - results: `@dataclass(frozen=True) BackupResult(snapshot_id: str, files: int, data_added: int)` and `@dataclass(frozen=True) RestoreResult(recordings: int, problems: tuple[dict, ...])`, with `.ok`
    - runners, each taking `runner=subprocess.run`:

      | Function | Returns |
      |---|---|
      | `run_init(bc, environ, *, runner)` | `str` |
      | `run_backup(bc, state, environ, *, tags=(), runner)` | `BackupResult` |
      | `run_forget(bc, environ, *, runner)` | `str` |
      | `run_check(bc, environ, *, now: datetime \| None = None, runner)` | `str` |
      | `run_restore_test(bc, environ, *, expected_uuid: str, now: datetime, keep_free_percents=(WARN_FREE_PERCENT, STOP_FREE_PERCENT), runner)` | `RestoreResult` |
  - **`recordings.state`:**
    - `@dataclass(frozen=True) Run(job: str, started_at: datetime, finished_at: datetime, ok: bool, detail: str)`
    - `State.record_run(job, started_at, finished_at, ok, detail) -> None`
    - `State.last_run(job, *, ok: bool | None = None) -> Run | None`
    - `State.backup_to(dest: Path) -> Path`
  - **`recordings.ops`:**
    - `OPS_LOCK`, imported from `recordings.locks` (Task 5a); constants `CLI_WAIT = 600.0`, `CLI_RETRY_LOCK = "5m"`, `BUSY = "skipped: busy"`
    - `log = logging.getLogger(__name__)`; the module imports `Locks` and `LockTimeout`, and `from datetime import datetime, timezone` (Task 13 adds `timedelta` to that line)
    - `run_child(cmd, *, input=None, timeout=None, capture_output=False, **popen_kwargs) -> subprocess.CompletedProcess`, and the context manager `sigterm_forwarded()`, whose handler chains to the one before it
    - `@dataclass Context(cfg, environ, state, clock=…, runner=run_child, retry_lock: str | None = None)`
    - `@dataclass(frozen=True) JobResult(job: str, ok: bool, detail: str)`; a busy lock gives `JobResult(job, False, "skipped: busy")`
    - `failure_detail(exc: BaseException) -> str`: `f"{type(exc).__name__}; exit {exc.returncode}"` when the error carries an integer `returncode`, else the type's name; and `backup_config(ctx) -> BackupConfig`
    - `JOBS: dict[str, Callable[[Context, tuple[str, ...]], str]]`, with `backup`, `prune`, `check` and `restore-test`
    - `FAILURES: tuple[type[BaseException], ...]`
    - `run_job(ctx, job: str, *, tags: tuple[str, ...] = (), lock_timeout: float = 600.0) -> JobResult`, under the ops lock
  - **`recordings.selfdoc.validate(root, *, deep: bool = False) -> list[dict]`**
  - **CLI:**
    - `recordings backup {init,run,check,prune,restore-test} [--tag TAG …] [--json] [--debug]`; it exits 75 when the ops lock stayed busy for 600 s
    - `recordings validate ARCHIVE [--deep]`
    - `cli._ops_context(as_json) -> ops.Context | int`, with `retry_lock=CLI_RETRY_LOCK`; and the helpers `_debug_logging(on)` and `_job_result(result, as_json) -> int`
    - `make restic`

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_backup.py`:
```python
import importlib.util
import inspect
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from recordings import backup, cli, ops
from recordings.backup import BackupError, Keep
from recordings.config import ConfigError, load_config
from recordings.locks import Locks
from recordings.ops import Context, JobResult, run_job
from recordings.selfdoc import validate
from recordings.sentinel import SENTINEL
from recordings.ssh import Nas, nas_free_bytes, ssh_options

REPO = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
# Read once, at import, so the fixture that clears RECORDINGS_* (Task 14) can never hide them.
REQUIRE_RESTIC = os.environ.get("RECORDINGS_REQUIRE_RESTIC") == "1"
TEST_RESTIC = os.environ.get("RECORDINGS_TEST_RESTIC")


def restic_or_skip() -> str:
    """The pinned restic; else a skip, or in CI (RECORDINGS_REQUIRE_RESTIC=1) a failure, because
    a skip there would hide a broken backup."""
    for candidate in (TEST_RESTIC, str(REPO / ".cache" / "bin" / "restic"), shutil.which("restic")):
        if candidate and Path(candidate).is_file():
            return candidate
    if REQUIRE_RESTIC:
        pytest.fail("RECORDINGS_REQUIRE_RESTIC=1, but restic is not installed: run `make restic`")
    pytest.skip("restic is not installed: `make restic` puts the pinned one in .cache/bin")


def config(tmp_path, writer, **backup_keys) -> tuple:
    deploy = tmp_path / "deploy.env"
    deploy.write_text("RECORDINGS_PORT=8000\n", encoding="utf-8")
    (tmp_path / "scratch").mkdir(exist_ok=True)
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


VERBS = ("init", "unlock", "backup", "forget", "check", "restore")


def verb(cmd: list[str]) -> str:
    return next(a for a in cmd[1:] if a in VERBS)


class FakeRestic:
    """Records each restic call and answers like restic's --json output does. With `only`, the
    return code and stderr apply to those restic commands, and the rest succeed."""

    def __init__(self, returncode=0, stdout="", stderr="", on_call=None, only=None):
        self.calls, self.returncode, self.stdout, self.stderr = [], returncode, stdout, stderr
        self.on_call, self.only = on_call, only

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs["env"]))
        if self.on_call:
            self.on_call(cmd)
        if self.only is not None and verb(cmd) not in self.only:
            return subprocess.CompletedProcess(cmd, 0, "", "")
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
    (unlock, _), (cmd, env) = fake.calls
    assert unlock == [bc.restic, "unlock"]
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
    fake = FakeRestic(returncode=returncode, stderr=stderr, only=("backup",))
    with pytest.raises(BackupError) as caught:
        backup.run_backup(backup.from_config(cfg, environ), writer.state, environ, runner=fake)
    assert "hunter2" not in str(caught.value) and f"exit {returncode}" in str(caught.value)
    assert caught.value.returncode == returncode


@pytest.mark.parametrize("repository", ["rest:https://recordings:hunter2@nas:8000/recordings/",
                                        "sftp://backup:hunter2@nas//backups/recordings"])
def test_a_repository_with_a_password_in_it_is_refused(tmp_path, writer, repository):
    # why: config.toml is not a secret (§5). The password goes in RESTIC_REST_PASSWORD_FILE.
    cfg, environ = config(tmp_path, writer, repository=repository)
    with pytest.raises(ConfigError, match="carries credentials") as caught:
        backup.from_config(cfg, environ)
    assert "hunter2" not in str(caught.value)


def test_retention_keeps_the_spec_counts_and_the_milestones(tmp_path, writer):
    cfg, environ = config(tmp_path, writer)
    cmd = backup.forget_command(backup.from_config(cfg, environ))
    for flag, value in (("--keep-hourly", "24"), ("--keep-daily", "14"), ("--keep-weekly", "8"),
                        ("--keep-monthly", "12"), ("--host", "test"), ("--tag", "recordings")):
        assert cmd[cmd.index(flag) + 1] == value, flag
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--keep-tag"] == ["pre-import", "post-import"]
    assert "--prune" in cmd
    assert backup.from_config(cfg, environ).keep == Keep()


def test_unlock_runs_before_every_job_that_locks_the_repository(tmp_path, writer, make_incoming):
    # why: restic never treats another process's lock as stale on its own; only `restic unlock`
    # removes one. A redeploy mid-check would otherwise fail every later check and backup.
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer)
    bc = backup.from_config(cfg, environ)
    fake = FakeRestic(stdout=SUMMARY)
    backup.run_backup(bc, writer.state, environ, runner=fake)
    backup.run_check(bc, environ, now=NOW, runner=fake)
    backup.run_forget(bc, environ, runner=fake)
    assert [verb(cmd) for cmd, _ in fake.calls] == [
        "unlock", "backup", "unlock", "check", "unlock", "forget"]
    check = fake.calls[3][0]
    assert f"--read-data-subset={backup.check_subset(NOW)}" in check


def test_the_weekly_check_reads_a_different_quarter_each_week():
    # why: a plain `restic check` never reads the pack files back; a quarter a week reads them all
    # every four weeks.
    assert {backup.check_subset(NOW + timedelta(weeks=w)) for w in range(4)} == {
        "1/4", "2/4", "3/4", "4/4"}


def test_retry_lock_and_the_rest_server_certificate_reach_restic(tmp_path, writer):
    cert = tmp_path / "rest-server.crt"
    cert.write_text("not a real certificate\n", encoding="utf-8")
    cfg, environ = config(tmp_path, writer, repository="rest:https://nas:8000/recordings/",
                          rest_username="recordings", cacert=str(cert))
    bc = backup.from_config(cfg, environ)
    base = backup.restic_base(replace(bc, retry_lock="5m"))
    assert base[base.index("--cacert") + 1] == str(cert)
    assert base[base.index("--retry-lock") + 1] == "5m"
    assert "--retry-lock" not in backup.restic_base(bc)  # the loop's: it tries again later
    cert.write_text("", encoding="utf-8")  # left empty: http over the tailnet (runbook 1b)
    assert "--cacert" not in backup.restic_base(backup.from_config(cfg, environ))


def test_the_runbook_downloads_the_restic_the_tests_pin():
    # why: two copies of one checksum drift apart. The runbook's spike fetches restic itself.
    spec = importlib.util.spec_from_file_location("fetch_restic", REPO / "scripts" / "fetch_restic.py")
    fetch_restic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch_restic)
    pinned = dict(fetch_restic.PINNED.values())
    runbook = (REPO / "docs" / "runbooks" / "first-run.md").read_text(encoding="utf-8")
    url = f"github.com/restic/restic/releases/download/v{fetch_restic.VERSION}/"
    blocks = [b for b in runbook.split("```") if url in b]
    found = [pair for b in blocks for pair in re.findall(r"ARCH=(\w+) SUM=([0-9a-f]{64})", b)]
    assert blocks and found, "the runbook no longer downloads the pinned restic"
    for arch, sha in found:
        assert pinned.get(arch) == sha, arch


def test_sftp_takes_the_nas_key_and_host_key_from_secret_files(tmp_path, writer):
    (tmp_path / "key").write_text("not a real key\n", encoding="utf-8")
    (tmp_path / "known_hosts").write_text("nas ssh-ed25519 AAAAexample\n", encoding="utf-8")
    cfg, environ = config(tmp_path, writer, repository="sftp:backup@nas:/backups/r")
    environ |= {"RECORDINGS_NAS_SSH_KEY_FILE": str(tmp_path / "key"),
                "RECORDINGS_NAS_KNOWN_HOSTS_FILE": str(tmp_path / "known_hosts")}
    base = backup.restic_base(backup.from_config(cfg, environ))
    (option,) = [a for a in base if a.startswith("sftp.args=")]
    assert f"-i {tmp_path / 'key'}" in option and "StrictHostKeyChecking=yes" in option
    assert "not a real key" not in " ".join(base)


def test_the_port_is_an_ssh_option_so_sftp_and_rsync_read_it_too():
    # why: sftp's -p means "keep the times", and its port flag is -P. `-o Port=` means the port
    # to ssh, sftp and rsync -e alike.
    options = ssh_options(None, None, 2222)
    assert options[options.index("Port=2222") - 1] == "-o" and "-p" not in options


def test_a_nas_that_is_off_is_given_up_on_in_seconds():
    # why: without ConnectTimeout, ssh waits out TCP's own timeout (minutes) on a NAS that is off,
    # and doctor and every backup's free-space check wait with it, twice (ssh, then sftp).
    options = ssh_options(None, None, None)
    assert options[options.index("ConnectTimeout=10") - 1] == "-o"


def test_too_little_space_on_the_nas_stops_the_backup(tmp_path, writer):
    # why: §15.1. The NAS's free space is checked before backups start.
    cfg, environ = config(tmp_path, writer, nas_repo_path="/recordings-mirror", min_nas_free_gb=10)
    cfg.data["nas"] = {"ssh": "mirror@nas"}
    bc = backup.from_config(cfg, environ)

    def df(cmd, **kwargs):
        if cmd[0] == "ssh":
            return subprocess.CompletedProcess(cmd, 0, "Filesystem 1024-blocks Used Available "
                                               "Capacity Mounted on\n"
                                               "/dev/md2 100 90 1048576 90% /volume1\n", "")
        return subprocess.CompletedProcess(cmd, 0, SUMMARY, "")

    with pytest.raises(backup.NasLowOnSpace, match="free on the NAS"):
        backup.run_backup(bc, writer.state, environ, runner=df)


SFTP_DF = ('sftp> df "/recordings-mirror"\n'
           "        Size         Used        Avail       (root)    %Capacity\n"
           "  1048576000   1047527424      1048576      1048576          99%\n")


def test_without_a_shell_the_free_space_comes_from_sftp_df():
    # why: §15.1. The NAS accounts are not admins, so they have no shell; OpenSSH's sftp still
    # answers `df`, through the statvfs@openssh.com extension.
    nas = Nas(ssh="mirror@nas", port=2222, key=None, known_hosts=None)
    calls = []

    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs.get("input")))
        if cmd[0] == "ssh":
            return subprocess.CompletedProcess(cmd, 1, "", "This account is not available\n")
        return subprocess.CompletedProcess(cmd, 0, SFTP_DF, "")

    assert nas_free_bytes(nas, "/recordings-mirror", runner=runner) == 1048576 * 1024
    sftp, stdin = calls[1]
    assert sftp[:3] == ["sftp", "-b", "-"] and sftp[-1] == "mirror@nas" and "Port=2222" in sftp
    assert stdin == 'df "/recordings-mirror"\n'


def test_the_free_space_is_unknown_when_neither_df_answers():
    nas = Nas(ssh="mirror@nas", port=None, key=None, known_hosts=None)
    failing = lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 255, "", "")
    assert nas_free_bytes(nas, "/recordings-mirror", runner=failing) is None
    calls = []
    assert nas_free_bytes(nas, "/x\nrm -r /", runner=lambda cmd, **kw: calls.append(cmd)) is None
    assert calls == []  # a newline would start another sftp command


def test_the_backup_says_how_much_the_nas_has_free_or_that_it_is_unknown(tmp_path, writer,
                                                                          make_incoming):
    # why: §15.1. When neither ssh's df nor sftp's df answers, the NAS's free space is "unknown",
    # and the backup's recorded detail says so rather than nothing.
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer, nas_repo_path="/recordings-mirror")
    cfg.data["nas"] = {"ssh": "mirror@nas"}

    def runner(answers):
        def run(cmd, **kwargs):
            if cmd[0] in ("ssh", "sftp"):
                return subprocess.CompletedProcess(cmd, 0 if answers else 255,
                                                   answers if cmd[0] == "ssh" else "", "")
            return subprocess.CompletedProcess(cmd, 0, SUMMARY, "")
        return run

    df = ("Filesystem 1024-blocks Used Available Capacity Mounted on\n"
          "/dev/md2 100 90 20971520 10% /volume1\n")
    for answers, ending in (("", "; NAS free space unknown"), (df, "; 20 GB free on the NAS")):
        ctx = Context(cfg=cfg, environ=environ, state=writer.state, clock=lambda: NOW,
                      runner=runner(answers))
        result = run_job(ctx, "backup")
        assert result.ok and result.detail.endswith(ending), result.detail


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
            media.chmod(0o644)
            media.write_bytes(media.read_bytes()[:100])

    fake = FakeRestic(on_call=restore)
    result = backup.run_restore_test(backup.from_config(cfg, environ), environ,
                                     expected_uuid=writer.identity.uuid, now=NOW, runner=fake)
    assert not result.ok and "SHA-256" in result.problems[0]["message"]
    assert [verb(cmd) for cmd, _ in fake.calls] == ["unlock", "restore"]
    assert "--verify" in fake.calls[1][0]  # restic reads each restored file back too
    assert not any((tmp_path / "scratch").iterdir())  # the scratch copy is always removed
    ctx = Context(cfg=cfg, environ=environ, state=writer.state, clock=lambda: NOW, runner=fake)
    job = run_job(ctx, "restore-test")
    assert not job.ok and job.detail == "RestoreTestFailed"
    assert writer.state.last_run("restore-test").ok is False


def test_the_restore_test_clears_old_copies_then_checks_the_space(tmp_path, writer, make_incoming,
                                                                  monkeypatch):
    # why: a killed restore test leaves a full plaintext copy behind; and a restore onto the
    # archive's disk must not take it below the warning (or the stop) threshold.
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer)
    old = tmp_path / "scratch" / "restore-20261001T000000Z"
    (old / "archive").mkdir(parents=True)
    (tmp_path / "scratch" / "notes.txt").write_text("not ours\n", encoding="utf-8")
    gib = 1024 ** 3
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda path: SimpleNamespace(
        total=100 * gib, used=80 * gib - 1, free=20 * gib + 1))  # just above 20% free
    fake = FakeRestic()
    with pytest.raises(backup.BackupRefused, match="below 20% free"):
        backup.run_restore_test(backup.from_config(cfg, environ), environ,
                                expected_uuid=writer.identity.uuid, now=NOW, runner=fake)
    assert fake.calls == [] and not old.exists()
    assert (tmp_path / "scratch" / "notes.txt").is_file()


def test_a_restored_copy_left_behind_fails_the_restore_test(tmp_path, writer, make_incoming,
                                                            monkeypatch):
    # why: rmtree(ignore_errors=True) alone would hide a full plaintext copy left on the disk.
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer)

    def restore(cmd):
        if "restore" in cmd:
            target = Path(cmd[cmd.index("--target") + 1])
            shutil.copytree(writer.root, target / writer.root.relative_to(writer.root.anchor))

    monkeypatch.setattr(backup.shutil, "rmtree", lambda path, ignore_errors=False: None)
    with pytest.raises(backup.RestoreCopyLeftBehind):
        backup.run_restore_test(backup.from_config(cfg, environ), environ,
                                expected_uuid=writer.identity.uuid, now=NOW,
                                runner=FakeRestic(on_call=restore))


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


def test_a_failure_is_recorded_as_its_type_and_exit_code_only(tmp_path, writer, make_incoming):
    # why: Review Focus 4. restic's stderr can name a file, and a file name can carry a title.
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer)
    fake = FakeRestic(returncode=3, stderr="Fatal: /archive/会議メモ / Q&A.json: permission denied\n",
                      only=("backup",))
    ctx = Context(cfg=cfg, environ=environ, state=writer.state, clock=lambda: NOW, runner=fake)
    result = run_job(ctx, "backup")
    assert result.detail == "BackupError; exit 3" == writer.state.last_run("backup").detail


def test_a_job_holds_the_ops_lock_and_the_loop_skips_a_busy_turn(tmp_path, writer, make_incoming):
    # why: restic from the CLI and from the loop at once fails on restic's own lock, and `restic
    # unlock` in one container would remove the other's live lock: both containers have the same
    # host name. The loop skips a busy turn, which is not a failure and sends no alert.
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer)
    fake = FakeRestic(stdout=SUMMARY)
    ctx = Context(cfg=cfg, environ=environ, state=writer.state, clock=lambda: NOW, runner=fake)
    with Locks(writer.state.locks_dir, timeout=0).hold("ops"):
        busy = run_job(ctx, "backup", lock_timeout=0)  # the loop's: it never waits
        assert run_job(ctx, "backup", lock_timeout=0.2) == busy  # the CLI waits, then gives up
    assert busy == JobResult("backup", False, "skipped: busy")
    assert fake.calls == [] and writer.state.last_run("backup") is None
    assert run_job(ctx, "backup").ok  # free again


def test_the_cli_waits_for_the_loop_and_for_restic_locks(tmp_path, writer, monkeypatch):
    cfg, environ = config(tmp_path, writer)
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX"):
        monkeypatch.delenv(name, raising=False)
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    ctx = cli._ops_context(False)
    assert ops.backup_config(ctx).retry_lock == "5m" and ctx.runner is ops.run_child
    assert inspect.signature(run_job).parameters["lock_timeout"].default == 600
    assert Context(cfg=cfg, environ=environ, state=writer.state).retry_lock is None


def test_sigterm_reaches_the_running_child_before_the_job_stops(tmp_path):
    # why: `docker stop` sends SIGTERM. restic must get it too, so it stops cleanly and removes
    # its lock; killed instead, its lock fails every later check and backup until an unlock.
    ready, got = tmp_path / "ready", tmp_path / "got-sigterm"
    child = ("import pathlib, signal, sys, time\n"
             f"def stop(*_):\n    pathlib.Path({str(got)!r}).touch()\n    sys.exit(3)\n"
             "signal.signal(signal.SIGTERM, stop)\n"
             f"pathlib.Path({str(ready)!r}).touch()\n"
             "time.sleep(30)\n")
    main_thread = threading.main_thread().ident

    def send_sigterm():
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        signal.pthread_kill(main_thread, signal.SIGTERM)

    threading.Thread(target=send_sigterm, daemon=True).start()
    with pytest.raises(SystemExit) as stopped, ops.sigterm_forwarded():
        ops.run_child([sys.executable, "-c", child], capture_output=True, text=True, timeout=60)
    assert stopped.value.code == 128 + signal.SIGTERM
    assert got.exists()  # the child had the signal, and stopped on its own


def test_sigterm_still_reaches_the_handler_that_was_there_before():
    # why: a handler installed before ours (by a caller, or a test runner) must still run.
    earlier = []
    before = signal.signal(signal.SIGTERM, lambda signum, frame: earlier.append(signum))
    try:
        with ops.sigterm_forwarded():
            signal.raise_signal(signal.SIGTERM)  # no child running: the earlier handler decides
        assert earlier == [signal.SIGTERM]
        assert signal.getsignal(signal.SIGTERM) is not ops._on_sigterm  # put back afterwards
    finally:
        signal.signal(signal.SIGTERM, before)


def test_with_a_real_restic_backup_check_forget_and_restore(tmp_path, writer, make_incoming):
    exe = restic_or_skip()
    writer.add(make_incoming())
    cfg, environ = config(tmp_path, writer, restic=exe)
    bc = replace(backup.from_config(cfg, environ), retry_lock="1m")  # as the CLI runs it
    backup.run_init(bc, environ)
    first = backup.run_backup(bc, writer.state, environ, tags=("pre-import",))
    assert len(first.snapshot_id) == 64
    backup.run_check(bc, environ, now=NOW)  # unlock, then a quarter of the data read back
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
       detail: str  # a fixed text (ops.failure_detail): never an error's message
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
           another process writes it. Replaced atomically, so a half copy is never backed up. The
           source opens with mode=rw (_connect), so a missing state.db is never created here."""
           dest = Path(dest)
           dest.parent.mkdir(parents=True, exist_ok=True)
           tmp = temp_name(dest)
           try:
               with closing(self._connect("rw")) as src, closing(sqlite3.connect(tmp)) as dst:
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
"""SSH to the NAS (spec §15.1), shared by the rsync mirror, the NAS free-space check, and restic's
SFTP backend when SFTP is the fallback.

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
    """Options that ssh, sftp and `rsync -e ssh` all read. The port is `-o Port=`, because sftp's
    `-p` means "keep the times" and its port flag is `-P`."""
    # ConnectTimeout: a NAS that is off is given up on in seconds, not after TCP's own timeout
    # (minutes), which doctor and every backup's free-space check would otherwise wait out.
    out = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=60",
           "-o", "ServerAliveCountMax=240"]
    if key is not None:
        out += ["-i", str(key), "-o", "IdentitiesOnly=yes"]
    if known_hosts is not None:
        out += ["-o", f"UserKnownHostsFile={known_hosts}", "-o", "StrictHostKeyChecking=yes"]
    if port is not None:
        out += ["-o", f"Port={port}"]
    return out


@dataclass(frozen=True)
class Nas:
    ssh: str | None  # an ssh destination, user@host: the mirror's account (§15.1)
    port: int | None
    key: Path | None
    known_hosts: Path | None

    def options(self) -> list[str]:
        return ssh_options(self.key, self.known_hosts, self.port)


def nas_from_config(cfg: Config, environ: Mapping[str, str]) -> Nas:
    section = cfg.data.get("nas", {})
    ssh, port = section.get("ssh"), section.get("port")
    if ssh is not None and (not isinstance(ssh, str) or not ssh.strip()):
        raise ConfigError('[nas] ssh must be an ssh destination, such as "mirror@nas"')
    if port is not None and (not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536):
        raise ConfigError("[nas] port must be a port number")
    return Nas(ssh=ssh, port=port, key=secret_path("nas_ssh_key", environ),
               known_hosts=secret_path("nas_known_hosts", environ))


def _avail_kib(text: str) -> int | None:
    """The Avail column of `df -Pk` or of sftp's `df`, both in KiB: the number under the header
    word that starts with "Avail", on the line after that header."""
    lines = text.splitlines()
    for i, line in enumerate(lines[:-1]):
        words = line.split()
        column = next((j for j, word in enumerate(words) if word.startswith("Avail")), None)
        if column is not None:
            try:
                return int(lines[i + 1].split()[column])
            except (IndexError, ValueError):
                return None
    return None


def _sftp_quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def nas_free_bytes(nas: Nas, path: str, *, runner=subprocess.run) -> int | None:
    """Free bytes on the NAS at `path`; None when [nas] ssh isn't set or neither `df` answers.

    First `df -Pk` over ssh, for an account with a shell. Then sftp's own `df`, which an SFTP-only
    account answers through the statvfs@openssh.com extension (sftp(1)): the NAS accounts are not
    admins, so they have no shell. `path` is as that account sees it; over SFTP on Synology, paths
    start at the shared folders, not /volume1.
    """
    if not nas.ssh or any(ord(c) < 32 for c in path):  # a newline would start another sftp command
        return None
    attempts = (
        (["ssh", *nas.options(), nas.ssh, "df", "-Pk", "--", shlex.quote(path)], None),
        (["sftp", "-b", "-", *nas.options(), nas.ssh], f"df {_sftp_quote(path)}\n"),
    )
    for cmd, stdin in attempts:
        try:
            proc = runner(cmd, input=stdin, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        kib = _avail_kib(proc.stdout) if proc.returncode == 0 else None
        if kib is not None:
            return kib * 1024
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
argument, and nothing here prints it. The transport is rest-server with --append-only, over TLS
with `--cacert` or over the tailnet; SFTP is the fallback.

restic never treats another process's lock as stale on its own: only `restic unlock` removes one
(it removes stale locks only, and works in append-only mode). So every job that locks the
repository runs `unlock` first. That is safe only because the ops lock (recordings.ops) keeps
this server's restic runs one at a time: restic judges staleness by host name and PID, and the
CLI's container and the loop's share a host name but not their PIDs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from recordings.archive import Archive, utc_stamp
from recordings.config import Config, ConfigError, secret
from recordings.disk import STOP_FREE_PERCENT, WARN_FREE_PERCENT
from recordings.selfdoc import validate
from recordings.sentinel import SentinelError, read_sentinel
from recordings.ssh import Nas, nas_free_bytes, nas_from_config
from recordings.state import State

TAG = "recordings"
MILESTONE_TAGS = ("pre-import", "post-import")
_USERINFO = re.compile(r"(\w+://)[^/@\s]+@")
_PASSWORD_IN_URL = re.compile(r"://[^/@\s]*:[^/@\s]*@")


class BackupError(RuntimeError):
    """A backup job failed or refused; a failed restic raises this class itself, with restic's
    exit status as `returncode`. Only the type's name and `returncode` are recorded and alerted
    (ops.failure_detail). The message can name paths, so it goes only to the debug log."""

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class BackupRefused(BackupError):
    """Refused before restic ran: no sentinel, another archive, a missing file or password."""


class NasLowOnSpace(BackupRefused):
    pass


class RestoreTestFailed(BackupError):
    """The restored copy did not validate."""


class RestoreCopyLeftBehind(BackupError):
    """The restore test's copy could not be removed: a full plaintext copy is on the disk."""


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
    cacert: Path | None = None  # rest-server's self-signed certificate, for an https: repository
    retry_lock: str | None = None  # restic --retry-lock: set by ops.backup_config for the CLI


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
    if _PASSWORD_IN_URL.search(repository):  # the message never repeats the URL
        raise ConfigError("[backup] repository: the repository URL carries credentials; put the "
                          "password in the secret file (RESTIC_REST_PASSWORD_FILE) and the user "
                          "in [backup] rest_username")
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
    cacert = section.get("cacert")
    if cacert is not None and (not isinstance(cacert, str) or not cacert.strip()):
        raise ConfigError("[backup] cacert must be the path to rest-server's certificate")
    cacert_path = Path(cacert) if cacert else None
    try:
        if cacert_path is not None and cacert_path.stat().st_size == 0:
            cacert_path = None  # left empty: http over the tailnet, so no certificate (runbook 1b)
    except OSError:
        pass  # a missing file stays named, and restic says it can't read it
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
        rest_username=section.get("rest_username") or None,
        cacert=cacert_path)


def restic_env(bc: BackupConfig, environ: Mapping[str, str]) -> dict[str, str]:
    """restic's whole environment: nothing from ours but these, so no stray variable leaks in."""
    env = {"PATH": environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
           "HOME": environ.get("HOME", "/tmp"), "RESTIC_REPOSITORY": bc.repository}
    if environ.get("RESTIC_PASSWORD_FILE"):
        env["RESTIC_PASSWORD_FILE"] = environ["RESTIC_PASSWORD_FILE"]
    elif environ.get("RESTIC_PASSWORD"):
        env["RESTIC_PASSWORD"] = environ["RESTIC_PASSWORD"]
    else:
        raise BackupRefused("no restic password: set RESTIC_PASSWORD_FILE (a Docker secret)")
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
    if bc.cacert is not None:
        base += ["--cacert", str(bc.cacert)]
    if bc.retry_lock:
        base += ["--retry-lock", bc.retry_lock]
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


def unlock_command(bc: BackupConfig) -> list[str]:
    return [*restic_base(bc), "unlock"]  # stale locks only; never --remove-all


def check_subset(now: datetime) -> str:
    """Which quarter of the pack files this week's check reads back: 1/4 to 4/4 in turn, so
    four weekly checks read all of them. A plain `check` never reads the data itself."""
    return f"{now.toordinal() // 7 % 4 + 1}/4"


def check_command(bc: BackupConfig, *, subset: str | None = None) -> list[str]:
    return [*restic_base(bc), "check", *([f"--read-data-subset={subset}"] if subset else [])]


def restore_command(bc: BackupConfig, target: Path) -> list[str]:
    # --verify reads every restored file back and compares it with the repository.
    return [*restic_base(bc), "restore", "latest", "--host", bc.host, "--tag", TAG,
            "--target", str(target), "--verify"]


def init_command(bc: BackupConfig) -> list[str]:
    return [*restic_base(bc), "init"]


def _run(bc: BackupConfig, cmd: list[str], environ: Mapping[str, str], what: str, runner,
         timeout: float | None = None) -> subprocess.CompletedProcess:
    proc = runner(cmd, env=restic_env(bc, environ), capture_output=True, text=True,
                  timeout=timeout)
    if proc.returncode != 0:
        last = (proc.stderr or "").strip().splitlines()[-1:] or ["no message"]
        raise BackupError(f"restic {what} failed (exit {proc.returncode}): "
                          + _USERINFO.sub(r"\1***@", last[0])[:300], returncode=proc.returncode)
    return proc


def _unlock(bc: BackupConfig, environ: Mapping[str, str], runner) -> None:
    _run(bc, unlock_command(bc), environ, "unlock", runner, timeout=600)


def _check_archive(bc: BackupConfig, state: State) -> str:
    try:
        identity = read_sentinel(bc.archive)
    except SentinelError as exc:
        raise BackupRefused(f"refusing to back up: {exc}") from None
    if state.meta("archive_uuid") != identity.uuid:
        raise BackupRefused("refusing to back up: the archive's UUID changed since `recordings init`")
    return identity.uuid


@dataclass(frozen=True)
class BackupResult:
    snapshot_id: str
    files: int
    data_added: int
    nas_free: int | None = None  # bytes free on the NAS; None when neither df answered (§15.1)


def run_init(bc: BackupConfig, environ: Mapping[str, str], *, runner=subprocess.run) -> str:
    _run(bc, init_command(bc), environ, "init", runner, timeout=600)
    return "repository initialised"


def run_backup(bc: BackupConfig, state: State, environ: Mapping[str, str], *,
               tags: Sequence[str] = (), runner=subprocess.run) -> BackupResult:
    _check_archive(bc, state)
    for path in bc.paths:
        if not path.is_file():
            raise BackupRefused(f"refusing to back up: {path} is missing")
    free = None
    if bc.nas_repo_path and bc.nas.ssh:
        free = nas_free_bytes(bc.nas, bc.nas_repo_path, runner=runner)
        if free is not None and free < bc.min_nas_free_bytes:
            raise NasLowOnSpace(f"refusing to back up: only {free / 1024 ** 3:.1f} GB free on the NAS")
    state_copy = state.backup_to(bc.state / "backup" / "state.db")
    _unlock(bc, environ, runner)
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
                        data_added=int(summary.get("data_added") or 0), nas_free=free)


def run_forget(bc: BackupConfig, environ: Mapping[str, str], *, runner=subprocess.run) -> str:
    _unlock(bc, environ, runner)
    _run(bc, forget_command(bc), environ, "forget", runner)
    return "retention applied"


def run_check(bc: BackupConfig, environ: Mapping[str, str], *, now: datetime | None = None,
              runner=subprocess.run) -> str:
    """`restic check`; given `now`, it also reads back this week's quarter of the data."""
    subset = check_subset(now) if now is not None else None
    _unlock(bc, environ, runner)
    _run(bc, check_command(bc, subset=subset), environ, "check", runner)
    return f"repository checked, data {subset} read back" if subset else "repository checked"


@dataclass(frozen=True)
class RestoreResult:
    recordings: int
    problems: tuple[dict, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def _tree_bytes(root: Path) -> int:
    total = 0
    for folder, _, names in os.walk(root):
        for name in names:
            try:
                total += (Path(folder) / name).lstat().st_size
            except OSError:
                pass
    return total


def _check_scratch_space(bc: BackupConfig, keep_free_percents: Sequence[float]) -> None:
    """The restore fits, and doesn't take its disk below a threshold it is above now. The scratch
    folder sits on the archive's disk, where 20% free raises a warning and 5% stops imports
    (Task 9's [disk]); a disk already below one isn't made to fail the test for it."""
    needed = _tree_bytes(bc.archive) + sum(p.stat().st_size for p in bc.paths if p.is_file())
    usage = shutil.disk_usage(bc.restore_scratch)
    after = usage.free - needed
    crossed = [t for t in sorted(keep_free_percents, reverse=True)
               if usage.free * 100 >= t * usage.total > after * 100]
    if after <= 0 or crossed:
        where = f"below {crossed[0]:g}% free" if crossed else "past full"
        raise BackupRefused(f"refusing the restore test: restoring {needed / 1024 ** 3:.1f} GB "
                            f"would take the scratch folder's disk {where}")


def run_restore_test(bc: BackupConfig, environ: Mapping[str, str], *, expected_uuid: str,
                     now: datetime,
                     keep_free_percents: Sequence[float] = (WARN_FREE_PERCENT, STOP_FREE_PERCENT),
                     runner=subprocess.run) -> RestoreResult:
    """Restore the newest snapshot into scratch and prove it (§15.1): restic's --verify, then
    `recordings validate` with every hash checked, and the same archive UUID.

    Copies an earlier, killed test left behind are removed first. The new copy is always removed,
    and a copy that can't be is an error: it is the whole archive, unencrypted.
    """
    if bc.restore_scratch is None:
        raise BackupRefused("[backup] restore_scratch is not set")
    if not bc.restore_scratch.is_dir():
        raise BackupRefused(f"[backup] restore_scratch {bc.restore_scratch} doesn't exist")
    for old in sorted(bc.restore_scratch.glob("restore-*")):
        if old.is_dir() and not old.is_symlink():
            shutil.rmtree(old)  # errors propagate: a copy left behind must not be hidden
        else:
            old.unlink()
    _check_scratch_space(bc, keep_free_percents)
    target = bc.restore_scratch / f"restore-{utc_stamp(now)}"
    try:
        _unlock(bc, environ, runner)
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
    if target.exists():  # checked here, so the error a failed restore raised isn't masked above
        raise RestoreCopyLeftBehind(f"the restored copy at {target} could not be removed: "
                                    "delete it by hand")
    return RestoreResult(recordings=count, problems=tuple(problems))
```

`packages/core/src/recordings/ops.py`:
```python
"""Operations (spec §14, §15.1). Each scheduled job runs through run_job, which records the run
in state.db, so Status can show it and the schedule knows what is due.

- **One job at a time.** run_job holds the ops lock, a flock in the state folder that the CLI,
  the backup service's loop and the import (Task 9) share. The CLI waits for it (CLI_WAIT, the
  default); the loop passes lock_timeout=0, and a busy turn is BUSY: not run, not recorded, not
  a failure. It is also what makes `restic unlock` safe (recordings.backup).
- **A failure is a result, not a crash.** It is recorded as the error's type and exit code only
  (failure_detail). The message, which can name paths or carry a server's words, goes only to
  the debug log.
- **SIGTERM reaches the child.** `docker stop` sends it to this process. Inside
  sigterm_forwarded, it goes on to the running restic or rsync, which stops cleanly and removes
  its lock. Then the handler that was there before runs, if any; with none, this process stops
  once the child has. The loop (Task 13) runs inside sigterm_forwarded and sets no handler of
  its own.
"""

from __future__ import annotations

import logging
import signal
import subprocess
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from recordings import backup
from recordings.backup import BackupError, RestoreTestFailed
from recordings.config import Config, ConfigError
from recordings.disk import disk_thresholds
from recordings.locks import OPS_LOCK, Locks, LockTimeout
from recordings.sentinel import SentinelError
from recordings.state import State, StateError

log = logging.getLogger(__name__)
CLI_WAIT = 600.0  # seconds the CLI waits for a job of the loop's to finish
CLI_RETRY_LOCK = "5m"  # restic --retry-lock from the CLI: the NAS's retention task may hold it
BUSY = "skipped: busy"

_child: subprocess.Popen | None = None
_stopping = False
_previous: Callable[[int, object], object] | None = None  # the SIGTERM handler before ours


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run_child(cmd: list[str], *, input: str | bytes | None = None, timeout: float | None = None,
              capture_output: bool = False, **popen_kwargs) -> subprocess.CompletedProcess:
    """subprocess.run, except that a SIGTERM to this process, inside sigterm_forwarded, goes on to
    the child, and this process stops only once the child has."""
    global _child
    if capture_output:
        popen_kwargs["stdout"] = popen_kwargs["stderr"] = subprocess.PIPE
    if input is not None:
        popen_kwargs["stdin"] = subprocess.PIPE
    with subprocess.Popen(cmd, **popen_kwargs) as proc:
        _child = proc
        try:
            stdout, stderr = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise
        finally:
            _child = None
    if _stopping and _previous is None:  # no one else decides when to stop: stop now
        raise SystemExit(128 + signal.SIGTERM)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _on_sigterm(signum: int, frame) -> None:
    global _stopping
    _stopping = True
    if _child is not None:
        _child.send_signal(signal.SIGTERM)  # restic and rsync stop cleanly, dropping their locks
    if _previous is not None:
        _previous(signum, frame)  # chain to whatever Python handler was there before
    elif _child is None:
        raise SystemExit(128 + signum)


@contextmanager
def sigterm_forwarded() -> Iterator[None]:
    """While the CLI or the loop runs jobs, SIGTERM goes on to the running child (run_child), then
    to the handler that was there before, if it is a Python function. Without any handler, Python
    as a container's PID 1 ignores SIGTERM, and Docker kills both with SIGKILL after the grace
    period, leaving restic's lock behind. Only the main thread can set a handler, so elsewhere
    this does nothing."""
    global _stopping, _previous
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    before = signal.getsignal(signal.SIGTERM)
    _stopping, _previous = False, before if callable(before) else None
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, before)
        _stopping, _previous = False, None


@dataclass
class Context:
    cfg: Config
    environ: Mapping[str, str]
    state: State
    clock: Callable[[], datetime] = field(default=_now)
    runner: Callable[..., subprocess.CompletedProcess] = field(default=run_child)
    # restic's --retry-lock: none by default, so restic fails at once on a held lock. The CLI's
    # Context (cli._ops_context) sets CLI_RETRY_LOCK.
    retry_lock: str | None = None


@dataclass(frozen=True)
class JobResult:
    job: str
    ok: bool
    detail: str  # BUSY when the ops lock was held: not run, not recorded, not a failure


def failure_detail(exc: BaseException) -> str:
    """The fixed text a failed job is recorded and alerted with: the error's type, and its exit
    code when it carries one (`returncode`). Never str(exc), which can carry paths, titles or a
    server's words."""
    code = getattr(exc, "returncode", None)
    return f"{type(exc).__name__}; exit {code}" if isinstance(code, int) else type(exc).__name__


def backup_config(ctx: Context) -> backup.BackupConfig:
    return replace(backup.from_config(ctx.cfg, ctx.environ), retry_lock=ctx.retry_lock)


def _backup(ctx: Context, tags: tuple[str, ...]) -> str:
    bc = backup_config(ctx)
    result = backup.run_backup(bc, ctx.state, ctx.environ, tags=tags, runner=ctx.runner)
    detail = (f"snapshot {result.snapshot_id[:12]}: {result.files} files, "
              f"{result.data_added} bytes added")
    if bc.nas_repo_path and bc.nas.ssh:  # §15.1: the NAS's free space, or "unknown"
        detail += ("; NAS free space unknown" if result.nas_free is None
                   else f"; {result.nas_free / 1024 ** 3:.0f} GB free on the NAS")
    return detail


def _prune(ctx: Context, tags: tuple[str, ...]) -> str:
    bc = backup_config(ctx)
    if not bc.prune:
        return "prune is off: retention runs on the NAS"
    return backup.run_forget(bc, ctx.environ, runner=ctx.runner)


def _check(ctx: Context, tags: tuple[str, ...]) -> str:
    return backup.run_check(backup_config(ctx), ctx.environ, now=ctx.clock(), runner=ctx.runner)


def _restore_test(ctx: Context, tags: tuple[str, ...]) -> str:
    result = backup.run_restore_test(
        backup_config(ctx), ctx.environ, expected_uuid=ctx.state.meta("archive_uuid") or "",
        now=ctx.clock(), keep_free_percents=tuple(disk_thresholds(ctx.cfg).values()),
        runner=ctx.runner)
    if not result.ok:
        first = result.problems[0]
        raise RestoreTestFailed(f"restore test failed: {len(result.problems)} problems; first: "
                                f"{first['message']} ({first['path']})")
    return f"restored and validated {result.recordings} recordings"


JOBS: dict[str, Callable[[Context, tuple[str, ...]], str]] = {
    "backup": _backup, "prune": _prune, "check": _check, "restore-test": _restore_test,
}
FAILURES: tuple[type[BaseException], ...] = (
    BackupError, ConfigError, SentinelError, StateError, OSError, subprocess.TimeoutExpired)


def run_job(ctx: Context, job: str, *, tags: tuple[str, ...] = (),
            lock_timeout: float = CLI_WAIT) -> JobResult:
    """Run one job under the ops lock, and record it. The CLI waits for the lock (the default);
    the loop passes 0. A lock still held means another run is busy: BUSY, not recorded."""
    with ExitStack() as stack:
        try:
            stack.enter_context(Locks(ctx.state.locks_dir, timeout=lock_timeout).hold(OPS_LOCK))
        except LockTimeout:
            log.info("%s: %s", job, BUSY)
            return JobResult(job, False, BUSY)
        started = ctx.clock()
        try:
            detail, ok = JOBS[job](ctx, tags), True
        except FAILURES as exc:
            log.debug("%s failed", job, exc_info=True)  # the full error, for --debug only
            detail, ok = failure_detail(exc), False
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
1. **Add** the imports `import logging`, `from recordings import backup, ops` and
   `from recordings.state import State`.
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
       p.add_argument("--debug", action="store_true",
                      help="print a failed job's full error (Status and alerts carry its type only)")
   ```
4. **Add**:
   ```python
   def _ops_context(as_json: bool) -> ops.Context | int:
       """The jobs' Context, with restic waiting up to ops.CLI_RETRY_LOCK for its own lock (the
       NAS's weekly retention task may hold it). run_job waits ops.CLI_WAIT for the ops lock unless
       told otherwise; the loop (Task 13) passes lock_timeout=0."""
       cfg = _config(as_json)
       if isinstance(cfg, int):
           return cfg
       if cfg.state_path is None:
           return _fail("set [state] path in config.toml first", as_json, 78)
       try:
           state = State.open(cfg.state_path)
       except StateError as exc:
           return _fail(str(exc), as_json, 78)
       return ops.Context(cfg=cfg, environ=os.environ, state=state, retry_lock=ops.CLI_RETRY_LOCK)


   def _debug_logging(on: bool) -> None:
       """--debug: a failed job's full error, on stderr. Status and alerts never carry it."""
       if on:
           logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")


   def _job_result(result: ops.JobResult, as_json: bool) -> int:
       _emit({"job": result.job, "ok": result.ok, "detail": result.detail}, as_json)
       if result.detail == ops.BUSY:
           return 75  # EX_TEMPFAIL: another job held the ops lock for ops.CLI_WAIT; try again
       return 0 if result.ok else 1


   def cmd_backup(args: argparse.Namespace) -> int:
       _debug_logging(args.debug)
       ctx = _ops_context(args.json)
       if isinstance(ctx, int):
           return ctx
       with ops.sigterm_forwarded():
           if args.action == "init":
               try:
                   detail = backup.run_init(ops.backup_config(ctx), ctx.environ, runner=ctx.runner)
               except (backup.BackupError, config.ConfigError) as exc:
                   return _fail(str(exc), args.json, 1)
               _emit({"job": "backup init", "ok": True, "detail": detail}, args.json)
               return 0
           job = "backup" if args.action == "run" else args.action
           return _job_result(ops.run_job(ctx, job, tags=tuple(args.tag)), args.json)
   ```
   and `"backup": cmd_backup,` in `commands`.

In `config.example.toml`, **add** after `[disk]`:
```toml
[nas]                                   # stage 2a: the NAS over SSH (spec §15.1)
# The mirror's own account, not an admin ([mirror] target uses it), with
# RECORDINGS_NAS_SSH_KEY_FILE. It also answers the free-space check before each backup: `df` over
# ssh, or sftp's own `df` when the account has no shell, as the NAS accounts don't.
ssh = "recordings-mirror@nas"
port = 22

[backup]                                # stage 2a: restic to the NAS (spec §15.1)
# rest-server with --append-only on the NAS (the runbook's spike). The user is rest_username, and
# the password is the rest_password secret: a repository URL with a password in it is refused.
repository = "rest:https://nas:8000/recordings/"
rest_username = "recordings"
# rest-server's self-signed certificate (restic --cacert), for an https: repository. Over the
# tailnet with http:, the file is left empty, and no certificate is passed.
cacert = "/run/secrets/nas_cacert"
# The fallback, only if rest-server can't run: SFTP with [nas]'s key, and prune = true. Over SFTP
# on Synology, paths start at the shared folder, not /volume1.
# repository = "sftp:recordings-backup@nas:/backups/recordings"
restic = "restic"
# Besides the archive, config.toml and a copy of state.db. In Docker, deploy.env is mounted here.
extra_paths = ["/backup-extra/deploy.env"]
keep_hourly = 24
keep_daily = 14
keep_weekly = 8
keep_monthly = 12
# false with rest-server --append-only: it refuses forget --prune, so retention runs on the NAS,
# as a weekly scheduled task (the runbook has it).
prune = false
# Any folder on the backups' NAS volume, as [nas] ssh's sftp sees it: the free-space check's path.
nas_repo_path = "/recordings-mirror"
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

For CI and the Mac. The Docker image installs the same version itself (docker/Dockerfile), and the
runbook's NAS spike downloads it with the same checksums; tests check that all three agree. The
checksums are from restic's v0.19.1 release SHA256SUMS, checked 2026-10-08; the darwin_arm64 one
was corrected on 2026-10-09 (the draft had darwin_amd64's). Nothing is installed system-wide.
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
    ("darwin", "arm64"): ("darwin_arm64", "7be0a144ccc377880f294204aa271d76e4b79554b42a751151d425ce6ebac143"),
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

In `.github/workflows/ci.yml`'s `python` job, **replace** the line
`      - run: uv run --frozen pytest` (the one with nothing after `pytest`; the `e2e` job's has
`-m e2e`) with:
```yaml
      # The pinned restic (scripts/fetch_restic.py checks its SHA-256), so the backup and
      # restore-test tests run. With RECORDINGS_REQUIRE_RESTIC=1 they fail, never skip, without it.
      - run: uv run --frozen python scripts/fetch_restic.py
      - run: uv run --frozen pytest
        env: { RECORDINGS_REQUIRE_RESTIC: "1" }
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `make restic && uv run pytest packages/core/tests/test_backup.py -v && RECORDINGS_REQUIRE_RESTIC=1 uv run pytest`
Expected: restic 0.19.1 lands in `.cache/bin/`, then every test passes. The real-restic test runs
rather than skipping; with `RECORDINGS_REQUIRE_RESTIC=1`, a missing restic would fail it.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/recordings packages/core/tests/test_backup.py scripts/fetch_restic.py config.example.toml Makefile .github/workflows/ci.yml
git commit -m "feat(core): restic backups, retention, check and a byte-for-byte restore test

An explicit backup list (archive, config.toml, deploy.env, an SQLite backup copy of state.db) that
refuses an archive without its sentinel. rest-server --append-only is the transport, with an
optional --cacert; SFTP is the fallback, and the NAS's free space comes from ssh df or sftp df.
Every job runs under the ops lock, runs restic unlock first, records only its error's type and
exit code, and passes SIGTERM on to restic. The CLI waits for the lock and passes --retry-lock 5m.
The restore test clears old copies, checks the space, restores with --verify and refuses to leave
a copy behind; the weekly check reads a quarter of the data. restic 0.19.1 pinned by SHA-256
(darwin_arm64 corrected), and installed in CI, where a missing restic fails the tests. Checked:
restic docs (075_scripting --json, 030 SFTP and REST, 060 forget, unlock, check
--read-data-subset, restore --verify, --retry-lock), rest-server --append-only, sftp(1) df, and
docs.python.org sqlite3 Connection.backup and signal.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: The NAS mirror

**Checkpoint lens:** operations.

The mirror runs through its own non-admin NAS account, `[nas] ssh`, which Task 11's free-space
check also uses, through sftp's `df` (spec §15.1; Dan, 2026-10-09). A compromised server could wipe
the mirror, which is derived, but not the append-only backups; `--max-delete` bounds an accident.
On top of the sentinel check, the target must be empty or already this archive's mirror.

**Files:**
- Create: `packages/core/src/recordings/mirror.py`
- Modify: `packages/core/src/recordings/ops.py` (the `mirror` job), `cli.py` (`mirror`), `config.example.toml` (`[mirror]`)
- Test: `packages/core/tests/test_mirror.py`

**Interfaces:**
- Consumes: `read_sentinel` and `SENTINEL` (Task 2), `Nas` and `nas_from_config` (Task 11), and `ops.Context`, `JOBS`, `FAILURES`, `run_job`, `sigterm_forwarded`, and the CLI's `_ops_context`, `_debug_logging` and `_job_result` (Task 11).
- Produces:
  - **`recordings.mirror`:**
    - errors: `class MirrorError(RuntimeError)`, with `.returncode: int | None` (a failed rsync raises it, with rsync's exit status), and its subclass `MirrorRefused`
    - constants `MAX_DELETE = 50` and `DSM_FOLDERS = ("#recycle", "@eaDir")`
    - `@dataclass(frozen=True) MirrorConfig(target: str, archive: Path, rsync: str, nas: Nas, max_delete: int = 50, no_perms: bool = False)`
    - `from_config(cfg, environ) -> MirrorConfig`, reading `[mirror] target`, `rsync`, `max_delete` and `no_perms`
    - `is_remote(target: str) -> bool`
    - `rsync_command(mc: MirrorConfig) -> list[str]`
    - `run_mirror(mc, *, expected_uuid: str, runner=subprocess.run) -> str`, which refuses a target that is neither empty nor this archive's mirror
  - **`ops.JOBS["mirror"]`**, and `MirrorError` in `ops.FAILURES`.
  - **CLI:** `recordings mirror [--json] [--debug]`.

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_mirror.py`:
```python
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from recordings import mirror
from recordings.config import ConfigError, load_config
from recordings.mirror import MirrorError, MirrorRefused, is_remote, rsync_command, run_mirror
from recordings.ops import Context, run_job
from recordings.sentinel import SENTINEL

OTHER_UUID = "00000000-0000-4000-8000-000000000000"


def setup(tmp_path, writer, target, extra=""):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'[archive]\npath = "{writer.root}"\nwriter_id = "test"\n'
                        f'[state]\npath = "{writer.state.path}"\n'
                        f'[mirror]\ntarget = "{target}"\n{extra}', encoding="utf-8")
    environ = {"RECORDINGS_CONFIG": str(cfg_file), "PATH": os.environ["PATH"]}
    return load_config(environ), environ


def mirror_config(tmp_path, writer, target, extra=""):
    cfg, environ = setup(tmp_path, writer, target, extra)
    return mirror.from_config(cfg, environ)


class FakeRsync:
    """rsync against a local folder standing in for the target: it answers --list-only and the
    archive.json fetch from that folder, and records the mirror runs themselves."""

    def __init__(self, target: Path, returncode=0, stderr=""):
        self.target, self.returncode, self.stderr, self.mirrored = target, returncode, stderr, []

    def __call__(self, cmd, **kwargs):
        if "--list-only" in cmd:
            if not self.target.is_dir():
                return subprocess.CompletedProcess(cmd, 23, "", "change_dir failed\n")
            names = [".", *sorted(p.name for p in self.target.iterdir())]
            listing = "".join(f"drwxr-xr-x          4,096 2026/10/09 08:00:00 {n}\n" for n in names)
            return subprocess.CompletedProcess(cmd, 0, listing, "")
        if cmd[-2].endswith("/" + SENTINEL):
            shutil.copy(self.target / SENTINEL, cmd[-1])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.mirrored.append(cmd)
        return subprocess.CompletedProcess(cmd, self.returncode, "", self.stderr)


def test_the_command_deletes_late_and_little_puts_updates_in_place_together_and_skips_work_in_progress(
        tmp_path, writer):
    mc = mirror_config(tmp_path, writer, "recordings-mirror@nas:/volume1/recordings-mirror/")
    cmd = rsync_command(mc)
    for flag in ("-a", "--delete", "--delete-delay", "--delay-updates", "--max-delete=50",
                 "--exclude=/.tmp/", "--exclude=.*.tmp", "--exclude=.~tmp~/",
                 "--exclude=/#recycle/", "--exclude=@eaDir/"):
        assert flag in cmd, flag
    assert "--no-perms" not in cmd
    assert cmd[-2:] == [f"{writer.root}/", "recordings-mirror@nas:/volume1/recordings-mirror/"]
    assert cmd[cmd.index("-e") + 1].startswith("ssh -o BatchMode=yes")
    local = rsync_command(mirror_config(tmp_path, writer, str(tmp_path / "mirror")))
    assert "-e" not in local


def test_the_deletion_limit_and_no_perms_come_from_config(tmp_path, writer):
    mc = mirror_config(tmp_path, writer, str(tmp_path / "mirror"), "max_delete = 500\nno_perms = true\n")
    cmd = rsync_command(mc)
    assert "--max-delete=500" in cmd and "--no-perms" in cmd  # a share with Windows ACLs
    for bad in ("max_delete = -1\n", 'max_delete = "50"\n', 'no_perms = "yes"\n'):
        with pytest.raises(ConfigError, match=r"\[mirror\]"):
            mirror_config(tmp_path, writer, str(tmp_path / "mirror"), bad)


@pytest.mark.parametrize("target, remote", [("nas:/x", True), ("backup@nas:/x/", True),
                                            ("/mnt/mirror", False), ("./x:y", False)])
def test_is_remote(target, remote):
    assert is_remote(target) is remote


def test_the_mirror_refuses_without_the_sentinel_or_with_another_archive(tmp_path, writer):
    # why: §6.7, §15.1. rsync --delete from an unmounted, empty folder would empty the mirror.
    mc = mirror_config(tmp_path, writer, str(tmp_path / "mirror"))
    calls = []
    with pytest.raises(MirrorRefused, match="UUID"):
        run_mirror(mc, expected_uuid=OTHER_UUID, runner=lambda *a, **k: calls.append(a))
    (writer.root / SENTINEL).unlink()
    with pytest.raises(MirrorRefused, match="mounted"):
        run_mirror(mc, expected_uuid=writer.identity.uuid, runner=lambda *a, **k: calls.append(a))
    assert calls == []


def test_the_mirror_runs_only_into_an_empty_target_or_its_own_mirror(tmp_path, writer):
    # why: §15.1. --delete would empty a wrong folder, or a mirror made before a rollback to a fresh
    # `init` (a new UUID), and Synology Drive would then delete it on the Mac too.
    target = tmp_path / "mirror"
    mc = mirror_config(tmp_path, writer, str(target))
    fake = FakeRsync(target)
    with pytest.raises(MirrorRefused, match="create its folder"):
        run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake)
    (target / "#recycle").mkdir(parents=True)  # DSM's own folders don't count
    (target / "@eaDir").mkdir()
    assert run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake) == "mirrored"
    (target / "photos").mkdir()
    with pytest.raises(MirrorRefused, match="other files"):
        run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake)
    sentinel = (writer.root / SENTINEL).read_text(encoding="utf-8")
    (target / SENTINEL).write_text(sentinel.replace(writer.identity.uuid, OTHER_UUID),
                                   encoding="utf-8")
    with pytest.raises(MirrorRefused, match="another archive's mirror"):
        run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake)
    assert len(fake.mirrored) == 1  # rsync --delete ran only into the empty target
    (target / SENTINEL).write_text(sentinel, encoding="utf-8")
    assert run_mirror(mc, expected_uuid=writer.identity.uuid, runner=fake) == "mirrored"
    assert len(fake.mirrored) == 2


def test_a_failed_rsync_is_an_error(tmp_path, writer):
    target = tmp_path / "mirror"
    target.mkdir()
    mc = mirror_config(tmp_path, writer, str(target))
    with pytest.raises(MirrorError, match="exit 23") as caught:
        run_mirror(mc, expected_uuid=writer.identity.uuid,
                   runner=FakeRsync(target, returncode=23, stderr="rsync: some error\n"))
    assert caught.value.returncode == 23


def test_more_deletions_than_the_limit_stop_the_mirror(tmp_path, writer):
    # why: §15.1. --max-delete bounds an accident; rsync exits 25 when it is reached.
    target = tmp_path / "mirror"
    target.mkdir()
    cfg, environ = setup(tmp_path, writer, str(target))
    fake = FakeRsync(target, returncode=25)
    with pytest.raises(MirrorError, match="max_delete"):
        run_mirror(mirror.from_config(cfg, environ), expected_uuid=writer.identity.uuid, runner=fake)
    ctx = Context(cfg=cfg, environ=environ, state=writer.state, runner=fake)
    assert run_job(ctx, "mirror").detail == "MirrorError; exit 25"


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
    target.mkdir()
    (writer.root / ".tmp" / "x").mkdir(parents=True)
    mc = mirror_config(tmp_path, writer, f"{target}/")
    assert run_mirror(mc, expected_uuid=writer.identity.uuid) == "mirrored"  # into an empty target
    assert (target / SENTINEL).is_file()
    assert (target / "recordings" / "2026" / "10" / rec.id / "recording.json").is_file()
    assert not (target / ".tmp").exists()
    (target / "stale-file").write_text("from before", encoding="utf-8")
    assert run_mirror(mc, expected_uuid=writer.identity.uuid) == "mirrored"  # into its own mirror
    assert not (target / "stale-file").exists()
    assert not list(target.rglob(".~tmp~"))
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_mirror.py -v`
Expected: FAIL with `ImportError: cannot import name 'mirror' from 'recordings'`.

- [ ] **Step 3: Write the mirror**

`packages/core/src/recordings/mirror.py`:
```python
"""The read-only mirror for the Mac (spec §15.1): an hourly one-way rsync of the archive to a
share on the NAS, through the mirror's own NAS account ([nas] ssh, with its key).

- `--delay-updates` puts every changed file in place at the end, so a reader never sees half a
  copy.
- `--delete --delete-delay` removes what the archive no longer holds, after the transfer, and
  `--max-delete` bounds it: more deletions than [mirror] max_delete stop the run (rsync exit 25).
- `.tmp/` and `.*.tmp` (work in progress) are never copied. `.~tmp~/`, where `--delay-updates`
  stages files and which an interrupted run leaves behind, is excluded too; on the Mac, Synology
  Drive's sync filter must exclude it as well. DSM's own `#recycle` and `@eaDir` folders are left
  alone.
- It refuses to run when the archive's sentinel is missing or its UUID has changed (§6.7), so an
  unmounted archive can never empty the mirror through --delete.
- It refuses a target that is neither empty nor this archive's mirror (an `archive.json` with the
  same UUID). So a wrong path, or a mirror made before a rollback to a fresh `init` (a new UUID),
  is never emptied, and Synology Drive never deletes it on the Mac.
- `[mirror] no_perms = true` adds `--no-perms`, for a share with Windows ACLs where rsync's chmod
  fails (the runbook's spike).

The mirror holds private recordings too: on the Mac, keeping external agents out of them is
policy (§7.4).
"""

from __future__ import annotations

import re
import shlex
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from recordings.config import Config, ConfigError
from recordings.sentinel import SENTINEL, SentinelError, read_sentinel
from recordings.ssh import Nas, nas_from_config

MAX_DELETE = 50
DSM_FOLDERS = ("#recycle", "@eaDir")  # the NAS's recycle bin and its indexer's folders
# One line of `rsync --list-only`: permissions, size, date, time, then the name.
_LISTED = re.compile(r"^[-dlpscb][-rwxsStT]{9}\S*\s+[\d,.]+[KMGTP]?\s+\S+\s+\S+\s+(?P<name>.+)$")


class MirrorError(RuntimeError):
    """The mirror failed or refused; a failed rsync raises this class itself, with rsync's exit
    status as `returncode` (25: max_delete was reached). Only the type's name and `returncode`
    are recorded and alerted (ops.failure_detail)."""

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class MirrorRefused(MirrorError):
    """Refused before rsync copied anything: the archive, or the target, isn't the right one."""


@dataclass(frozen=True)
class MirrorConfig:
    target: str
    archive: Path
    rsync: str
    nas: Nas
    max_delete: int = MAX_DELETE
    no_perms: bool = False


def from_config(cfg: Config, environ: Mapping[str, str]) -> MirrorConfig:
    section = cfg.data.get("mirror", {})
    target = section.get("target")
    if not isinstance(target, str) or not target.strip():
        raise ConfigError("[mirror] target is not set (see config.example.toml)")
    if cfg.archive_path is None:
        raise ConfigError("set [archive] path before mirroring")
    max_delete = section.get("max_delete", MAX_DELETE)
    if not isinstance(max_delete, int) or isinstance(max_delete, bool) or max_delete < 0:
        raise ConfigError("[mirror] max_delete must be a whole number, 0 or more")
    no_perms = section.get("no_perms", False)
    if not isinstance(no_perms, bool):
        raise ConfigError("[mirror] no_perms must be true or false")
    return MirrorConfig(target=target.strip(), archive=cfg.archive_path,
                        rsync=str(section.get("rsync", "rsync")), nas=nas_from_config(cfg, environ),
                        max_delete=max_delete, no_perms=no_perms)


def is_remote(target: str) -> bool:
    """rsync's rule: `[user@]host:path` is remote; a path whose first part has no colon is local."""
    first = target.split("/", 1)[0]
    return ":" in first and not target.startswith((".", "/"))


def _ssh(mc: MirrorConfig) -> list[str]:
    return ["-e", shlex.join(["ssh", *mc.nas.options()])] if is_remote(mc.target) else []


def _folder(mc: MirrorConfig) -> str:
    return mc.target if mc.target.endswith("/") else mc.target + "/"


def rsync_command(mc: MirrorConfig) -> list[str]:
    cmd = [mc.rsync, "-a", "--delete", "--delete-delay", "--delay-updates",
           f"--max-delete={mc.max_delete}", "--exclude=/.tmp/", "--exclude=.*.tmp",
           "--exclude=.~tmp~/", "--exclude=/#recycle/", "--exclude=@eaDir/"]
    if mc.no_perms:
        cmd.append("--no-perms")
    return [*cmd, *_ssh(mc), f"{mc.archive}/", mc.target]


def _target_names(mc: MirrorConfig, runner) -> list[str]:
    """What the target folder holds at its top, from `rsync --list-only`, which needs no shell."""
    proc = runner([mc.rsync, "--list-only", *_ssh(mc), _folder(mc)], capture_output=True,
                  text=True, timeout=600)
    if proc.returncode != 0:
        raise MirrorRefused(f"refusing to mirror: the target can't be listed (rsync exit "
                            f"{proc.returncode}); create its folder first", returncode=proc.returncode)
    names = []
    for line in proc.stdout.splitlines():
        match = _LISTED.match(line)
        if match and match["name"] != "." and match["name"] not in DSM_FOLDERS:
            names.append(match["name"])
    return names


def _check_target(mc: MirrorConfig, expected_uuid: str, runner) -> None:
    names = _target_names(mc, runner)
    if not names:
        return  # empty: the first mirror
    if SENTINEL not in names:
        raise MirrorRefused("refusing to mirror: the target holds other files and no archive.json")
    with tempfile.TemporaryDirectory(prefix="recordings-mirror-") as tmp:
        proc = runner([mc.rsync, *_ssh(mc), _folder(mc) + SENTINEL, f"{tmp}/"],
                      capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise MirrorRefused(f"refusing to mirror: the target's {SENTINEL} can't be read (rsync "
                                f"exit {proc.returncode})", returncode=proc.returncode)
        try:
            uuid = read_sentinel(Path(tmp)).uuid
        except SentinelError:
            raise MirrorRefused(f"refusing to mirror: the target's {SENTINEL} is not valid") from None
    if uuid != expected_uuid:
        raise MirrorRefused("refusing to mirror: the target is another archive's mirror (its UUID "
                            "differs). If that archive is gone for good, empty the target first")


def run_mirror(mc: MirrorConfig, *, expected_uuid: str, runner=subprocess.run) -> str:
    try:
        identity = read_sentinel(mc.archive)
    except SentinelError as exc:
        raise MirrorRefused(f"refusing to mirror: {exc}") from None
    if identity.uuid != expected_uuid:
        raise MirrorRefused("refusing to mirror: the archive's UUID changed since `recordings init`")
    _check_target(mc, expected_uuid, runner)
    proc = runner(rsync_command(mc), capture_output=True, text=True, timeout=6 * 3600)
    if proc.returncode == 25:
        raise MirrorError(f"rsync stopped after {mc.max_delete} deletions ([mirror] max_delete). "
                          "If they are meant, raise it for one run", returncode=25)
    if proc.returncode != 0:
        last = (proc.stderr or "").strip().splitlines()[-1:] or ["no message"]
        raise MirrorError(f"rsync failed (exit {proc.returncode}): {last[0][:300]}",
                          returncode=proc.returncode)
    return "mirrored"
```

In `packages/core/src/recordings/ops.py`:
1. **Add** the imports `from recordings import mirror` and `from recordings.mirror import MirrorError`.
2. **Add** before `JOBS`:
   ```python
   def _mirror(ctx: Context, tags: tuple[str, ...]) -> str:
       return mirror.run_mirror(mirror.from_config(ctx.cfg, ctx.environ),
                                expected_uuid=ctx.state.meta("archive_uuid") or "", runner=ctx.runner)
   ```
3. **Add** `"mirror": _mirror,` to `JOBS`, after `"restore-test": _restore_test,`:
   ```python
   JOBS: dict[str, Callable[[Context, tuple[str, ...]], str]] = {
       "backup": _backup, "prune": _prune, "check": _check, "restore-test": _restore_test,
       "mirror": _mirror,
   }
   ```
4. **Replace**
   `    BackupError, ConfigError, SentinelError, StateError, OSError, subprocess.TimeoutExpired)`
   with:
   ```python
       BackupError, MirrorError, ConfigError, SentinelError, StateError, OSError,
       subprocess.TimeoutExpired)
   ```

In `packages/core/src/recordings/cli.py`, **add** to `build_parser`:
```python
    p = sub.add_parser("mirror", help="rsync the archive to the NAS's read-only mirror, now")
    p.add_argument("--json", action="store_true")
    p.add_argument("--debug", action="store_true",
                   help="print a failed run's full error (Status and alerts carry its type only)")
```
and:
```python
def cmd_mirror(args: argparse.Namespace) -> int:
    _debug_logging(args.debug)
    ctx = _ops_context(args.json)
    if isinstance(ctx, int):
        return ctx
    with ops.sigterm_forwarded():
        return _job_result(ops.run_job(ctx, "mirror"), args.json)
```
with `"mirror": cmd_mirror,` in `commands`.

In `config.example.toml`, **add** after `[backup]`:
```toml
[mirror]                                # stage 2a: the read-only mirror the Mac reads (spec §15.1)
# An rsync destination, through [nas] ssh's account and key. The folder must exist, and be empty
# or already this archive's mirror (an archive.json with the same UUID): anything else is refused,
# so --delete can't empty a wrong folder. DSM's #recycle and @eaDir folders are ignored. Over ssh,
# rsync on Synology sees /volume1/...; the runbook's spike confirms the form.
target = "recordings-mirror@nas:/volume1/recordings-mirror/"
rsync = "rsync"
max_delete = 50                         # more deletions in one run stop it (rsync exit 25); raise
                                        # it for one run when they are meant
no_perms = false                        # true adds --no-perms: for a share with Windows ACLs, when
                                        # the spike's rsync reports chmod errors
# An interrupted run can leave a `.~tmp~` folder behind (rsync --delay-updates). The mirror never
# copies one, but on the Mac, add `.~tmp~` to Synology Drive's sync filter too, so none is synced.
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_mirror.py -v && uv run pytest`
Expected: all pass. `test_a_real_local_mirror` runs wherever rsync has `--delay-updates`: CI, and macOS 26's openrsync (checked 2026-10-09); an older Mac's openrsync skips it.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/recordings packages/core/tests/test_mirror.py config.example.toml
git commit -m "feat(core): the NAS mirror: rsync --delete --delay-updates --max-delete, refusing a wrong target

It refuses without the archive's sentinel, and refuses a target that is neither empty nor this
archive's mirror (archive.json with the same UUID), ignoring DSM's #recycle and @eaDir. Deletions
are capped by [mirror] max_delete (50); .~tmp~ is excluded; [mirror] no_perms adds --no-perms for
ACL shares. It runs under the ops lock, and a CLI run passes SIGTERM on to rsync. Checked: rsync(1)
(--delay-updates, --delete-delay, --max-delete exit 25, --list-only, --partial-dir's .~tmp~).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 13: Alerts, the dead-man's switch and the ops loop

**Checkpoint lens:** security and privacy.

**Files:**
- Create: `packages/core/src/recordings/alerts.py`
- Modify: `packages/core/src/recordings/ops.py` (the schedule, `disk` job, alerting, `run_job`, `run_due`, `loop`), `state.py` (alert state), `config.py` (the `ntfy_topic`, `deadman_url` and `ntfy_token` secrets), `cli.py` (`ops`, `alert-test`), `config.example.toml` (`[alerts]`)
- Test: `packages/core/tests/test_alerts.py`, `packages/core/tests/test_ops.py`, `packages/core/tests/conftest.py` (the `ntfy` fixture)

**Interfaces:**
- Consumes:
  - from Task 11:
    - in `ops`: `Context`, `JobResult`, `JOBS`, `FAILURES`, `log`, `CLI_WAIT = 600.0`, `BUSY = "skipped: busy"`, `failure_detail(exc) -> str` (`f"{type(exc).__name__}; exit {exc.returncode}"` when the exception carries an integer `returncode`, else the class name), `sigterm_forwarded()` (SIGTERM goes on to the running restic or rsync, then this process stops), and the imports of `ExitStack`, `Locks`, `LockTimeout`, `OPS_LOCK` and `disk_thresholds`
    - `BackupError.returncode` (a failed restic raises `BackupError` itself), `SecretSpec.optional`, and the CLI's `_ops_context` and `import logging`
  - from Task 12: `MirrorError.returncode`
  - from Task 9: `disk_status`
  - from Task 5a: `OPS_LOCK = "ops"` (in `recordings.locks`); from Task 2: `State.locks_dir`, `config.secret`
- Produces:
  - **`recordings.alerts`:**
    - `TIMEOUT = 10.0`, `TOPIC_RE` (`[-_A-Za-z0-9]{1,64}`), `PINGS = {"success": "", "start": "/start", "fail": "/fail", "log": "/log"}`
    - `@dataclass(frozen=True) AlertConfig(ntfy_server: str | None, topic: str | None, deadman_url: str | None, token: str | None = None)`. It refuses, with a `ConfigError` that never repeats the value: a URL that isn't `https` (plain `http` only to the loopback), a URL with a user name or password, and a topic outside `TOPIC_RE`. It has `.enabled`, and a `__repr__` that hides the topic, the ping URL and the token.
    - `from_config(cfg, environ) -> AlertConfig`
    - `send(ac, *, title: str, message: str, priority: int = 4, tags: Sequence[str] = ("warning",)) -> bool`, with `Authorization: Bearer` when `ac.token` is set
    - `ping_deadman(ac, kind: str = "success", *, message: str = "") -> bool`: a POST to the ping URL plus `PINGS[kind]`
  - **`recordings.state.State`:** `alert_state(key) -> tuple[bool, datetime | None]` and `set_alert_state(key, *, failing: bool, last_sent_at: datetime | None) -> None`.
  - **`recordings.ops`:**
    - the schedule: `INTERVALS`, `ORDER`, `RETRY_AFTER = 15 min`, `REMIND_EVERY = 24 h`, `TITLES`
    - `class DiskWarning(RuntimeError)`
    - `enabled_jobs(cfg) -> tuple[str, ...]`
    - `due(state, now, jobs) -> list[str]`
    - `run_job(ctx, job, *, tags=(), lock_timeout: float = CLI_WAIT) -> JobResult`, replacing Task 11's with the same signature. It keeps the ops lock. A lock still held after `lock_timeout` gives `JobResult(job, False, BUSY)`, which is not recorded, alerted or pinged. A failure's `detail` is `failure_detail(exc)`, never `str(exc)`. A backup pings the dead-man's switch at `/start`, then at the base URL or `/fail`.
    - `run_due(ctx) -> list[JobResult]`, which never waits for the lock (`lock_timeout=0`)
    - `loop(ctx, *, tick: float = 60.0, sleep=time.sleep, stop=lambda: False) -> None`
    - `JOBS["disk"]`
  - **Alert text, fixed templates:** title `f"recordings: {TITLES[job]}"` and body `f"{job} failed ({detail}). See Status."`; on recovery, `f"recordings: {job} recovered"` and `f"{job} works again. See Status."`.
  - **New `SECRETS` entries:** `ntfy_topic` (`RECORDINGS_NTFY_TOPIC`), `deadman_url` (`RECORDINGS_DEADMAN_URL`) and `ntfy_token` (`RECORDINGS_NTFY_TOKEN`, optional).
  - **CLI:** `recordings ops [--loop] [--json]`, which runs inside `sigterm_forwarded()`, so `docker stop` stops a running restic or rsync cleanly and then the loop; and `recordings alert-test [--json]`, which pings the dead-man's switch at `/log`.

- [ ] **Step 1: Write the failing tests**

Add to `packages/core/tests/conftest.py` (with `import threading` and
`from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer` at the top). Both test files
use it, and with `--import-mode=importlib` a fixture in `conftest.py` is the way to share it:
```python
class FakeNtfy:
    """An ntfy server on a real local socket: it records each request and answers `status`.
    The dead-man's switch pings land here too, under the ping URL's own path."""

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
    ac = AlertConfig(ntfy_server=ntfy.url, topic="topic-do-not-print_1", deadman_url=None)
    assert send(ac, title="recordings: backup failed",
                message="backup failed (BackupError; exit 1). See Status.")
    (req,) = ntfy.requests
    assert (req["method"], req["path"]) == ("POST", "/topic-do-not-print_1")
    assert req["headers"]["Title"] == "recordings: backup failed"
    assert (req["headers"]["Priority"], req["headers"]["Tags"]) == ("4", "warning")
    assert req["body"] == "backup failed (BackupError; exit 1). See Status."
    assert "Authorization" not in req["headers"]


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


def test_the_dead_mans_switch_speaks_healthchecks(ntfy):
    # why: Healthchecks.io takes the URL alone as success, /start and /fail around a run, and /log
    # as a note that leaves the check's state alone (healthchecks.io/docs/http_api).
    ac = AlertConfig(None, None, f"{ntfy.url}/ping/abc-do-not-print/")
    assert ping_deadman(ac, "start") and ping_deadman(ac)
    assert ping_deadman(ac, "fail", message="backup failed (BackupError; exit 1). See Status.")
    assert ping_deadman(ac, "log", message="recordings alert-test")
    assert [(r["method"], r["path"]) for r in ntfy.requests] == [
        ("POST", "/ping/abc-do-not-print/start"), ("POST", "/ping/abc-do-not-print"),
        ("POST", "/ping/abc-do-not-print/fail"), ("POST", "/ping/abc-do-not-print/log")]
    assert ntfy.requests[2]["body"] == "backup failed (BackupError; exit 1). See Status."
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
    for server in ("ntfy.sh", "http://ntfy.example"):
        cfg_file.write_text(f'[alerts]\nntfy_server = "{server}"\n', encoding="utf-8")
        with pytest.raises(ConfigError, match="ntfy_server"):
            alerts.from_config(load_config(environ), environ)


def test_an_ntfy_token_goes_in_a_bearer_header_and_nowhere_else(tmp_path, ntfy):
    (tmp_path / "token").write_text("tk_do-not-print\n", encoding="utf-8")
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'[alerts]\nntfy_server = "{ntfy.url}"\n', encoding="utf-8")
    environ = {"RECORDINGS_CONFIG": str(cfg_file), "RECORDINGS_NTFY_TOPIC": "alerts-topic",
               "RECORDINGS_NTFY_TOKEN_FILE": str(tmp_path / "token")}
    ac = alerts.from_config(load_config(environ), environ)
    assert send(ac, title="recordings: test alert", message="m")
    assert ntfy.requests[0]["headers"]["Authorization"] == "Bearer tk_do-not-print"
    assert "do-not-print" not in repr(ac)


@pytest.mark.parametrize("field, value", [
    ("ntfy_server", "ntfy.sh"),
    ("ntfy_server", "http://ntfy.example"),
    ("ntfy_server", "https://user:do-not-print@ntfy.example"),
    ("deadman_url", "http://hc-ping.example/do-not-print"),
    ("deadman_url", "hc-ping.com/do-not-print"),
    ("topic", "do-not-print/1"),
    ("topic", "x" * 65),
])
def test_plain_http_credentials_in_a_url_and_odd_topics_are_refused(field, value):
    # why: https keeps the topic and the ping URL's token off the wire in clear (security review);
    # ntfy topics are [-_A-Za-z0-9]{1,64} (ops review). The error never repeats the value.
    fields = {"ntfy_server": None, "topic": None, "deadman_url": None, field: value}
    with pytest.raises(ConfigError) as caught:
        AlertConfig(**fields)
    assert "do-not-print" not in str(caught.value) and "x" * 65 not in str(caught.value)
    AlertConfig("http://127.0.0.1:8080", "alerts-topic", "http://localhost:1/p")  # loopback is fine
```

`packages/core/tests/test_ops.py`:
```python
import json
import sqlite3
import subprocess
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from recordings import disk, ops
from recordings.cli import main
from recordings.config import load_config
from recordings.locks import Locks
from recordings.ops import Context, due, enabled_jobs, loop, run_due, run_job

T0 = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
SUMMARY = json.dumps({"message_type": "summary", "snapshot_id": "f" * 64,
                      "total_files_processed": 1, "data_added": 1})
TITLE = "会議メモ / Q&A"  # Review Focus 4: a synthetic non-ASCII title


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now


def restic(returncode=0, stderr=""):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, SUMMARY if returncode == 0 else "", stderr)
    return run


def raising(exc):
    def run(cmd, **kwargs):
        raise exc
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


def pushes(ntfy):
    """The ntfy alerts: POSTs to the topic."""
    return [r for r in ntfy.requests if r["path"] == "/alerts-topic"]


def pings(ntfy):
    """The dead-man's switch pings, by path."""
    return [r["path"] for r in ntfy.requests if r["path"].startswith("/ping")]


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
    assert pings(ntfy) == ["/ping/start", "/ping"] and pushes(ntfy) == []


def test_a_killed_backup_alerts_once_reminds_daily_and_says_when_it_recovers(
        tmp_path, writer, make_incoming, ntfy):
    # why: §20, "a killed backup raises an alert". A killed restic exits -9.
    writer.add(make_incoming())
    clock = Clock()
    ctx = context(tmp_path, writer, ntfy, clock=clock, runner=restic(returncode=-9))
    result = run_job(ctx, "backup")
    assert (result.ok, result.detail) == (False, "BackupError; exit -9")
    clock.now += timedelta(hours=1)
    run_job(ctx, "backup")  # still failing: no second alert yet
    clock.now += timedelta(hours=24)
    run_job(ctx, "backup")  # a reminder
    ctx.runner = restic()
    clock.now += timedelta(hours=1)
    assert run_job(ctx, "backup").ok
    assert [(r["headers"]["Title"], r["body"]) for r in pushes(ntfy)] == [
        ("recordings: backup failed", "backup failed (BackupError; exit -9). See Status."),
        ("recordings: backup failed", "backup failed (BackupError; exit -9). See Status."),
        ("recordings: backup recovered", "backup works again. See Status.")]
    assert pings(ntfy) == ["/ping/start", "/ping/fail"] * 3 + ["/ping/start", "/ping"]


@pytest.mark.parametrize("runner, detail", [
    (restic(returncode=1, stderr=f"Fatal: open /archive/recordings/2026/10/x/{TITLE}.md: "
                                 "permission denied\n"), "BackupError; exit 1"),
    (raising(PermissionError(13, "Permission denied", f"/archive/{TITLE}.md")), "PermissionError"),
], ids=["restic-stderr", "oserror-path"])
def test_no_alert_or_stored_run_carries_an_exceptions_text(
        tmp_path, writer, ntfy, caplog, runner, detail):
    # why: Review Focus 4. An exception's text can hold a title, a path or a tool's own output.
    # Alerts go to third parties (ntfy.sh, Healthchecks.io), and Status shows the stored detail.
    # Only the container's debug log may carry the full text.
    ctx = context(tmp_path, writer, ntfy, runner=runner)
    with caplog.at_level("INFO"):
        result = run_job(ctx, "backup")
    assert (result.ok, result.detail) == (False, detail)
    assert len(pushes(ntfy)) == 1 and pings(ntfy) == ["/ping/start", "/ping/fail"]
    with closing(sqlite3.connect(writer.state.db_path)) as db:
        stored = "\n".join(db.iterdump())
    sent = json.dumps(ntfy.requests, ensure_ascii=False)
    for text in (stored, sent, caplog.text):
        assert "会議" not in text


def test_low_disk_space_raises_an_alert(tmp_path, writer, ntfy, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(disk.os, "statvfs", lambda p: SimpleNamespace(
        f_bavail=150, f_frsize=1024, f_blocks=1000))
    result = run_job(context(tmp_path, writer, ntfy), "disk")
    assert (result.ok, result.detail) == (False, "DiskWarning")
    (req,) = pushes(ntfy)
    assert req["headers"]["Title"] == "recordings: disk space low"
    assert req["body"] == "disk failed (DiskWarning). See Status."
    assert pings(ntfy) == []  # only a backup pings the dead-man's switch


def test_run_due_runs_what_is_due_and_beats_the_heart(tmp_path, writer, make_incoming, ntfy):
    writer.add(make_incoming())
    ctx = context(tmp_path, writer, ntfy)
    results = {r.job for r in run_due(ctx)}
    assert {"disk", "backup", "prune", "check"} <= results
    assert writer.state.meta("ops_heartbeat") == T0.isoformat()
    assert {r.job for r in run_due(ctx)} <= {"restore-test"}  # the rest ran a moment ago


def test_a_busy_ops_lock_skips_quietly(tmp_path, writer, make_incoming, ntfy):
    # why: the CLI and the loop share one flock in /state (ops review). The loop never waits, and
    # a skip is not a failure: nothing is recorded, alerted or pinged, and the next tick retries.
    writer.add(make_incoming())
    ctx = context(tmp_path, writer, ntfy)
    with Locks(writer.state.locks_dir, timeout=0).hold("ops"):
        results = run_due(ctx)
        assert results and all((r.ok, r.detail) == (False, "skipped: busy") for r in results)
        assert run_job(ctx, "backup", lock_timeout=0.2).detail == "skipped: busy"
    assert ntfy.requests == [] and writer.state.last_run("backup") is None
    assert "backup" in {r.job for r in run_due(ctx) if r.ok}  # free again: it runs


def test_the_loop_runs_until_told_to_stop(tmp_path, writer, ntfy):
    ctx = context(tmp_path, writer, ntfy)
    ticks = []
    loop(ctx, tick=0, sleep=lambda s: ticks.append(s), stop=lambda: len(ticks) >= 3)
    assert len(ticks) == 3


def test_the_loop_runs_with_sigterm_forwarded(tmp_path, writer, ntfy, monkeypatch):
    # why: the loop is PID 1 in its container, where SIGTERM does nothing without a handler, and
    # Docker would wait out stop_grace_period, then kill restic mid-run and leave its lock behind.
    # Inside sigterm_forwarded (Task 11), restic gets the SIGTERM, then the loop stops.
    ctx = context(tmp_path, writer, ntfy)
    for key, value in ctx.environ.items():
        monkeypatch.setenv(key, value)
    events = []

    @contextmanager
    def forwarded():
        events.append("forwarding")
        yield
        events.append("restored")

    monkeypatch.setattr(ops, "sigterm_forwarded", forwarded)
    monkeypatch.setattr(ops, "loop", lambda ctx, **kwargs: events.append("loop"))
    assert main(["ops", "--loop"]) == 0
    assert events == ["forwarding", "loop", "restored"]


def test_alert_test_sends_one_logs_the_check_and_never_prints_the_topic(
        tmp_path, writer, ntfy, monkeypatch, capsys):
    ctx = context(tmp_path, writer, ntfy)
    for key, value in ctx.environ.items():
        monkeypatch.setenv(key, value)
    assert main(["alert-test", "--json"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {"ntfy": True, "deadman": True}
    assert "alerts-topic" not in out
    assert [r["headers"]["Title"] for r in pushes(ntfy)] == ["recordings: test alert"]
    assert pings(ntfy) == ["/ping/log"]  # /log: the check's state stays as it is
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_alerts.py packages/core/tests/test_ops.py -v`
Expected: FAIL. Both files error at collection, with
`ImportError: cannot import name 'alerts' from 'recordings'` and
`ImportError: cannot import name 'due' from 'recordings.ops'`.

- [ ] **Step 3: Write the alerts**

In `packages/core/src/recordings/config.py`, **add** to `SECRETS`:
```python
    "ntfy_topic": SecretSpec(
        "RECORDINGS_NTFY_TOPIC", "the private ntfy topic that alerts go to", 2),
    "deadman_url": SecretSpec(
        "RECORDINGS_DEADMAN_URL", "the Healthchecks.io ping URL (it carries its own token)", 2),
    "ntfy_token": SecretSpec(
        "RECORDINGS_NTFY_TOKEN", "an ntfy access token, only for a server that needs one", 2,
        optional=True),
```

`packages/core/src/recordings/alerts.py`:
```python
"""Alerts (spec §14): push notifications through ntfy, and a dead-man's switch.

- **ntfy** (https://docs.ntfy.sh/publish/): a POST to <server>/<topic>, with the message as the
  body and Title, Priority and Tags headers. The topic is a secret, because anyone who knows it
  can read the alerts. It is never logged, printed, or put in an error. A server that needs an
  access token gets one as `Authorization: Bearer` (RECORDINGS_NTFY_TOKEN_FILE); ntfy.sh needs
  none.
- **The dead-man's switch** is a Healthchecks.io check (https://healthchecks.io/docs/http_api/).
  Each backup pings <url>/start, then <url> when it worked or <url>/fail when it didn't.
  `recordings alert-test` pings <url>/log, which leaves the check's state alone. Healthchecks
  alerts when the pings stop, and that is what notices a stopped container or a dead server. Its
  period is 1 hour and its grace at least 3 hours, because the loop runs one job at a time and a
  long check or restore test holds back the next backup (docs/runbooks/first-run.md).

Both URLs must be https, so the topic, the token and the ping URL never cross a network in clear;
plain http is allowed only to this machine's loopback (the tests' fake server). Alert text comes
from fixed templates (a job, an exception's class, an exit code), never a recording's title, a
path or a tool's own output (Review Focus 4). Sending never raises: a failed delivery is logged
without its URL.
"""

from __future__ import annotations

import http.client
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from recordings import __version__
from recordings.config import Config, ConfigError, secret

log = logging.getLogger(__name__)
TIMEOUT = 10.0
TOPIC_RE = re.compile(r"[-_A-Za-z0-9]{1,64}")
PINGS = {"success": "", "start": "/start", "fail": "/fail", "log": "/log"}
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def _check_url(url: object, what: str) -> None:
    """https, with a host and no user name or password in it; http only to the loopback. The
    error names the setting, never the URL, which may be a secret."""
    try:
        parts = urlsplit(url) if isinstance(url, str) else None
        host = parts.hostname if parts else None
    except ValueError:  # a malformed IPv6 address
        parts, host = None, None
    if parts is None or not host or "@" in parts.netloc or not (
            parts.scheme == "https" or (parts.scheme == "http" and host in _LOOPBACK)):
        raise ConfigError(f"{what} must be an https:// URL, with no user name or password in it")


@dataclass(frozen=True)
class AlertConfig:
    ntfy_server: str | None
    topic: str | None
    deadman_url: str | None
    token: str | None = None  # an ntfy access token, for a server that needs one

    def __post_init__(self) -> None:
        if self.ntfy_server is not None:
            _check_url(self.ntfy_server, "[alerts] ntfy_server")
        if self.deadman_url is not None:
            _check_url(self.deadman_url, "RECORDINGS_DEADMAN_URL")
        if self.topic is not None and not TOPIC_RE.fullmatch(self.topic):
            raise ConfigError("RECORDINGS_NTFY_TOPIC must be 1 to 64 letters, digits, '-' or '_'")

    @property
    def enabled(self) -> bool:
        return bool(self.ntfy_server and self.topic)

    def __repr__(self) -> str:  # never the topic, the ping URL or the token
        return (f"AlertConfig(ntfy_server={self.ntfy_server!r}, "
                f"topic={'set' if self.topic else None}, "
                f"deadman_url={'set' if self.deadman_url else None}, "
                f"token={'set' if self.token else None})")

    __str__ = __repr__


def from_config(cfg: Config, environ: Mapping[str, str]) -> AlertConfig:
    server = cfg.data.get("alerts", {}).get("ntfy_server")
    return AlertConfig(ntfy_server=server.rstrip("/") if isinstance(server, str) else server,
                       topic=secret("ntfy_topic", environ),
                       deadman_url=secret("deadman_url", environ),
                       token=secret("ntfy_token", environ))


def _deliver(request: urllib.request.Request) -> bool:
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        log.warning("delivery failed: HTTP %s", exc.code)
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        log.warning("delivery failed: %s", type(exc).__name__)
    return False


def send(ac: AlertConfig, *, title: str, message: str, priority: int = 4,
         tags: Sequence[str] = ("warning",)) -> bool:
    """Push one alert. The title and the message come from fixed templates (Review Focus 4)."""
    if not ac.enabled:
        return False
    headers = {"Title": title, "Priority": str(priority), "Tags": ",".join(tags),
               "User-Agent": f"recordings/{__version__}"}
    if ac.token:
        headers["Authorization"] = f"Bearer {ac.token}"
    delivered = _deliver(urllib.request.Request(
        f"{ac.ntfy_server}/{ac.topic}", data=message.encode("utf-8"), method="POST",
        headers=headers))
    if not delivered:
        log.warning("alert %r was not delivered", title)
    return delivered


def ping_deadman(ac: AlertConfig, kind: str = "success", *, message: str = "") -> bool:
    """One Healthchecks ping, `kind` being a key of PINGS. The body is a fixed text."""
    if not ac.deadman_url:
        return False
    return _deliver(urllib.request.Request(
        ac.deadman_url.rstrip("/") + PINGS[kind], data=message.encode("utf-8"), method="POST",
        headers={"User-Agent": f"recordings/{__version__}"}))
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

- [ ] **Step 4: Add the schedule, the disk job, alerting and the loop to `ops.py`**

In `packages/core/src/recordings/ops.py`:
1. The imports. Task 11 already brings `logging`, `ExitStack`, `Locks`, `LockTimeout` and
   `OPS_LOCK`.
   - **Add** `import time` after `import threading`.
   - **Change** `from datetime import datetime, timezone` to
     `from datetime import datetime, timedelta, timezone`.
   - **Change** `from recordings import backup` to `from recordings import alerts, backup`, and
     **add** `from recordings.alerts import AlertConfig` after it.
   - **Change** `from recordings.disk import disk_thresholds` to
     `from recordings.disk import disk_status, disk_thresholds`.
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
       except ConfigError as exc:  # its text names a setting, never a secret's value
           log.warning("alerts are not configured: %s", exc)
           return None


   def _alert(ctx: Context, ac: AlertConfig, job: str, ok: bool, detail: str) -> None:
       """One push when a job starts failing, a reminder every day while it fails, and one when it
       works again. The text is a fixed template: the job, the failure's fixed detail and "See
       Status", never a path, a title or a tool's own output (Review Focus 4)."""
       key, now = f"job:{job}", ctx.clock()
       failing, last_sent = ctx.state.alert_state(key)
       if ok:
           if failing:
               alerts.send(ac, title=f"recordings: {job} recovered",
                           message=f"{job} works again. See Status.", priority=3,
                           tags=("white_check_mark",))
               ctx.state.set_alert_state(key, failing=False, last_sent_at=now)
           return
       if not failing or last_sent is None or now - last_sent >= REMIND_EVERY:
           delivered = alerts.send(ac, title=f"recordings: {TITLES[job]}",
                                   message=f"{job} failed ({detail}). See Status.")
           last_sent = now if delivered else last_sent
       ctx.state.set_alert_state(key, failing=True, last_sent_at=last_sent)


   def _run_held(ctx: Context, job: str, tags: tuple[str, ...]) -> JobResult:
       ac = _alert_config(ctx)
       heartbeat = ac is not None and job == "backup"
       if heartbeat:
           alerts.ping_deadman(ac, "start")
       started = ctx.clock()
       try:
           detail, ok = JOBS[job](ctx, tags), True
       except FAILURES as exc:
           log.debug("%s failed", job, exc_info=True)  # the full error, for --debug only
           detail, ok = failure_detail(exc), False
       ctx.state.record_run(job, started, ctx.clock(), ok, detail)
       if ac is not None:
           _alert(ctx, ac, job, ok, detail)
           if heartbeat:
               alerts.ping_deadman(ac, "success" if ok else "fail",
                                   message="" if ok else f"{job} failed ({detail}). See Status.")
       return JobResult(job, ok, detail)
   ```
5. **Replace** `run_job` (Task 11's, with its ops lock and the same signature) with:
   ```python
   def run_job(ctx: Context, job: str, *, tags: tuple[str, ...] = (),
               lock_timeout: float = CLI_WAIT) -> JobResult:
       """Run one job under the ops lock, record it, alert, and for a backup ping the dead-man's
       switch: /start first, then the URL itself or /fail. The CLI waits for the lock (the default);
       the loop passes 0. A lock still held means another run is busy: BUSY, which is neither
       recorded nor alerted, and no ping is sent."""
       with ExitStack() as stack:
           try:
               stack.enter_context(Locks(ctx.state.locks_dir, timeout=lock_timeout).hold(OPS_LOCK))
           except LockTimeout:
               log.info("%s: %s", job, BUSY)
               return JobResult(job, False, BUSY)
           return _run_held(ctx, job, tags)


   def run_due(ctx: Context) -> list[JobResult]:
       """One pass of the loop: each job that is due, in ORDER, never waiting for the ops lock. A
       busy job is BUSY, and the next pass tries it again."""
       ctx.state.set_meta("ops_heartbeat", ctx.clock().isoformat())
       return [run_job(ctx, job, lock_timeout=0)
               for job in due(ctx.state, ctx.clock(), enabled_jobs(ctx.cfg))]


   def loop(ctx: Context, *, tick: float = 60.0, sleep=time.sleep, stop=lambda: False) -> None:
       """The backup service's main loop: what is due, then a minute's sleep, forever.
       `recordings ops --loop` runs it inside sigterm_forwarded: `docker stop` passes SIGTERM on to a
       running restic or rsync, and once that has stopped, the loop stops too."""
       while not stop():
           for result in run_due(ctx):
               log.info("%s: %s (%s)", result.job, "ok" if result.ok else "not ok", result.detail)
           sleep(tick)
   ```

In `packages/core/src/recordings/cli.py`, **change** `from recordings import backup, ops` (Task 11)
to `from recordings import alerts, backup, ops`, then add the parsers:
```python
    p = sub.add_parser("ops", help="run the scheduled jobs that are due (--loop: forever)")
    p.add_argument("--loop", action="store_true", help="the backup service's main loop")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("alert-test", help="send a test alert and log a dead-man's switch ping")
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
        # docker stop: SIGTERM goes on to a running restic or rsync, which stops cleanly and
        # drops its lock; then this process stops. The job it was in is neither recorded nor
        # alerted, and the next start runs it again.
        with ops.sigterm_forwarded():
            ops.loop(ctx)
        return 0
    with ops.sigterm_forwarded():
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
    # /log records the test without changing the check's state, so it can never hide an outage.
    logged = alerts.ping_deadman(ac, "log", message="recordings alert-test")
    _emit({"ntfy": sent, "deadman": logged}, args.json)
    return 0 if sent else 1
```
with `"ops": cmd_ops, "alert-test": cmd_alert_test,` in `commands`.

In `config.example.toml`, **add** after `[mirror]`:
```toml
[alerts]                                # stage 2a: push alerts (spec §14)
ntfy_server = "https://ntfy.sh"         # https only (stage-2a plan, decision 16)
# The topic and the dead-man's switch URL are secrets: RECORDINGS_NTFY_TOPIC(_FILE) and
# RECORDINGS_DEADMAN_URL(_FILE), below. The topic is 1 to 64 letters, digits, - or _.
# The dead-man's switch is a Healthchecks.io check with a period of 1 hour and a grace of at least
# 3 hours: the loop runs one job at a time, so a long check or restore test delays the next
# backup's ping. Each backup pings <url>/start, then <url> or <url>/fail; `recordings alert-test`
# pings <url>/log, which leaves the check's state alone.
```
and in the secrets comment block:
```toml
#   RECORDINGS_NTFY_TOPIC       the private ntfy topic alerts go to                   (stage 2)
#   RECORDINGS_DEADMAN_URL      the Healthchecks.io ping URL                          (stage 2)
#   RECORDINGS_NTFY_TOKEN       an ntfy access token, only for a server that needs one (stage 2)
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_alerts.py packages/core/tests/test_ops.py -v && uv run pytest`
Expected: 13 and 12 passed, then the whole suite passes, with a passed count above Task 12's.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/recordings packages/core/tests config.example.toml
git commit -m "feat(core): ntfy alerts, the Healthchecks dead-man's switch and the ops loop

One alert per failure, a daily reminder and a recovery message, all from fixed templates: an
exception's text never reaches an alert or state.db. A backup pings /start, then the check or
/fail; alert-test pings /log. Both URLs must be https, and ntfy topics are checked. The loop
skips a job while the CLI holds the ops lock, and on SIGTERM stops restic cleanly first.
Checked: docs.ntfy.sh/publish (POST /<topic>, Title/Priority/Tags, Bearer tokens) and
healthchecks.io/docs/http_api (/start, /fail, /log).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Docker and Compose for the first deploy

**Checkpoint lens:** operations.

**Files:**
- Modify: `docker/Dockerfile`, `docker/compose.yml` (whole files below), `Makefile` (`deploy`, `rollback`, `deploy-smoke`), `README.md` (the Docker and configuration sections), `.github/workflows/ci.yml` (the compose smoke job), `.github/dependabot.yml` (a comment)
- Rename: `docker/deploy.example.env` → `docker/deploy.example.conf` (whole file below)
- Create: `docker/recordings.service`, `docker/up-deployed.sh`, `scripts/compose_smoke.py`, `conftest.py` (at the repo root)
- Modify: `packages/core/tests/test_deploy_template.py` (whole file below)

**Interfaces:**
- Consumes:
  - the CLI commands from Tasks 2, 5, 9, 11, 12 and 13 (`recordings init`, `backup init|run|restore-test`, `mirror`, `ops --loop` and the rest), and the secrets' `_FILE` names (Tasks 11 and 13)
  - Task 13's SIGTERM handling in `ops --loop`, which `stop_grace_period` relies on
  - Task 11's `scripts/fetch_restic.py` (`VERSION`, and `PINNED[(system, machine)] = (name, sha256)`) and `[backup] cacert`
  - Task 1's runbook (`docs/runbooks/first-run.md`), which carries the same linux restic sums and installs `docker/recordings.service`
  - `recordings.config.DEFAULT_PATH` (stage 1)
- Produces:
  - **The deployment's shape:**
    - `docker/compose.yml` with the services `web` and `backup`, both `pull_policy: never`, each with `RECORDINGS_ROLE` set to `web` or `backup` (for `doctor --role`, Task 16); `backup` has `stop_grace_period: 2m`
    - the named volume `index`
    - the secrets `restic_password`, `rest_password`, `nas_cacert`, `nas_ssh_key`, `nas_known_hosts`, `ntfy_topic` and `deadman_url`, all mounted in `backup` only
    - the deploy variables `RECORDINGS_{ARCHIVE,STATE,RESTORE,SECRETS,CONFIG,DEPLOY_ENV}_HOST`, `RECORDINGS_BIND`, `RECORDINGS_PORT`, `RECORDINGS_UID` and `RECORDINGS_GID`
  - **The image:** base images pinned by digest; build args `APP_UID` and `APP_GID` (default 10001), which Compose takes from `RECORDINGS_UID`/`RECORDINGS_GID`, creating that user in `/etc/passwd`; `USER ${APP_UID}:${APP_GID}`.
  - **Image tags:** `RECORDINGS_IMAGE_TAG`, set by `make deploy` to `git rev-parse --short=12 HEAD`, with `-dirty` for uncommitted or untracked changes, which `make deploy` refuses.
  - **Make targets:** `make deploy`, `make rollback TAG=<tag>` (`up -d --no-build`), `make deploy-smoke`.
  - **Boot:** `docker/recordings.service` (systemd; `/srv/recordings` and the checkout `/srv/recordings/repo` named at its top) and `docker/up-deployed.sh ARGS…`, which runs `docker compose ARGS…` on the tag the web container already runs.
  - **Tests:** the root `conftest.py`, with `KEEP = frozenset({"RECORDINGS_REQUIRE_RESTIC", "RECORDINGS_TEST_RESTIC"})`, `host_settings(environ) -> list[str]` and the autouse fixture `no_settings_from_this_machine`, which returns `host_settings`. The fixture drops every `RECORDINGS_*` and `RESTIC_*` variable except those in `KEEP`, and points `config.DEFAULT_PATH` at a file that never exists. CI's `RECORDINGS_REQUIRE_RESTIC=1` survives it.

- [ ] **Step 1: Write the failing test**

Replace `packages/core/tests/test_deploy_template.py` with:
```python
import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from recordings import config

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "docker" / "compose.yml"
DOCKERFILE = REPO / "docker" / "Dockerfile"
TEMPLATE = REPO / "docker" / "deploy.example.conf"
UNIT = REPO / "docker" / "recordings.service"
SET_BY_MAKE = {"RECORDINGS_IMAGE_TAG"}


def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def make_dry_run(*args: str) -> subprocess.CompletedProcess:
    """`make -n` prints a recipe without running it; $(error ...) still stops it."""
    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    return subprocess.run(["make", "-n", *args], cwd=REPO, capture_output=True, text=True)


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


def test_the_image_has_a_passwd_entry_for_the_deploy_uid():
    # why: ssh exits 255 ("No user exists for uid") before it reads any option when its uid has
    # no passwd entry, and the containers run as deploy.env's UID. compose_smoke.py runs
    # `ssh -G nas` in the backup service to prove it for real.
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "ARG APP_UID=10001\n" in dockerfile and "ARG APP_GID=10001\n" in dockerfile
    assert 'useradd --uid "$APP_UID" --gid "$APP_GID"' in dockerfile
    assert "USER ${APP_UID}:${APP_GID}\n" in dockerfile
    assert compose()["services"]["web"]["build"]["args"] == {
        "APP_UID": "${RECORDINGS_UID:?}", "APP_GID": "${RECORDINGS_GID:?}"}


def test_each_service_names_its_role_and_backup_has_time_to_stop():
    services = compose()["services"]
    assert services["web"]["environment"]["RECORDINGS_ROLE"] == "web"
    assert services["backup"]["environment"]["RECORDINGS_ROLE"] == "backup"
    assert services["backup"]["stop_grace_period"] == "2m"  # restic stops cleanly on SIGTERM


def test_images_are_tagged_by_commit_and_never_pulled():
    for name, service in compose()["services"].items():
        assert service["image"].startswith("recordings:${RECORDINGS_IMAGE_TAG:?"), name
        assert service["pull_policy"] == "never", name
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "git rev-parse --short=12 HEAD" in makefile and "git status --porcelain" in makefile
    assert "RECORDINGS_IMAGE_TAG=$(IMAGE_TAG)" in makefile


def test_a_dirty_tree_is_never_deployed():
    # why: a -dirty image has no commit to roll back to (ops review).
    dirty = make_dry_run("deploy", "IMAGE_TAG=0123456789ab-dirty")
    assert dirty.returncode != 0 and "commit first" in dirty.stderr
    clean = make_dry_run("deploy", "IMAGE_TAG=0123456789ab")
    assert clean.returncode == 0, clean.stderr
    assert "RECORDINGS_IMAGE_TAG=0123456789ab docker compose" in clean.stdout
    assert "up -d --build" in clean.stdout


def test_a_rollback_starts_an_old_image_and_never_builds_one():
    # why: with build: in compose.yml, `up` builds a missing tag from today's code; --no-build
    # makes a missing tag an error instead (checked with Compose v5.5.1, 2026-10-08).
    assert make_dry_run("rollback").returncode != 0
    back = make_dry_run("rollback", "TAG=0123456789ab")
    assert back.returncode == 0, back.stderr
    assert "RECORDINGS_IMAGE_TAG=0123456789ab docker compose" in back.stdout
    assert "up -d --no-build" in back.stdout


def test_the_boot_unit_waits_for_docker_the_disk_and_tailscale():
    unit = UNIT.read_text(encoding="utf-8")
    for line in ("Requires=docker.service", "After=docker.service tailscaled.service",
                 "RequiresMountsFor=/srv/recordings", "Type=oneshot", "RemainAfterExit=yes",
                 "WorkingDirectory=/srv/recordings/repo", "WantedBy=multi-user.target",
                 "ExecStart=/bin/sh docker/up-deployed.sh up -d --no-build"):
        assert f"\n{line}\n" in unit, line
    (pre,) = [line for line in unit.splitlines() if line.startswith("ExecStartPre=")]
    assert "tailscale0" in pre and '-ge 120 ]' in pre


def test_at_boot_the_containers_start_on_the_tag_they_last_ran(tmp_path):
    # why: compose.yml requires RECORDINGS_IMAGE_TAG, and pull_policy: never can't fetch one. The
    # tag the web container runs is the deployed one, however it was started.
    fake = tmp_path / "docker"
    fake.write_text('#!/bin/sh\nif [ "$1" = ps ]; then echo "$FAKE_IMAGE"; exit 0; fi\n'
                    'echo "RECORDINGS_IMAGE_TAG=$RECORDINGS_IMAGE_TAG $*"\n', encoding="utf-8")
    fake.chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "FAKE_IMAGE": "recordings:0123456789ab"}
    script = str(REPO / "docker" / "up-deployed.sh")
    up = subprocess.run(["sh", script, "up", "-d", "--no-build"], env=env, capture_output=True,
                        text=True)
    assert up.returncode == 0, up.stderr
    assert up.stdout == ("RECORDINGS_IMAGE_TAG=0123456789ab compose --env-file docker/deploy.env "
                         "-f docker/compose.yml up -d --no-build\n")
    none = subprocess.run(["sh", script, "up", "-d", "--no-build"], env={**env, "FAKE_IMAGE": ""},
                          capture_output=True, text=True)
    assert none.returncode != 0 and "make deploy" in none.stderr and none.stdout == ""


def test_every_base_image_is_pinned_by_digest():
    # why: a tag can be moved to other bytes; a digest can't. Dependabot moves the digests.
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    froms = re.findall(r"^FROM\s+(\S+)\s+AS\s+(\S+)", dockerfile, re.M)
    assert len(froms) == len(re.findall(r"^FROM\s", dockerfile, re.M)), "a FROM without AS"
    for image, _ in froms:
        assert re.search(r":[\w.-]+@sha256:[0-9a-f]{64}$", image), image
    stages = {stage for _, stage in froms}
    for source in re.findall(r"--from=(\S+)", dockerfile):
        assert source in stages, f"COPY --from={source} takes an image no pinned FROM names"
    updates = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    docker = [u["directory"] for u in updates["updates"] if u["package-ecosystem"] == "docker"]
    assert docker == ["/docker"]


def test_the_images_restic_is_the_one_fetch_restic_pins():
    # why: one restic, checked against one release's SHA256SUMS, in CI, on the Mac, in the image
    # and in the runbook's spike (security review).
    spec = importlib.util.spec_from_file_location("fetch_restic", REPO / "scripts" / "fetch_restic.py")
    fetch_restic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch_restic)
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert f"ARG RESTIC_VERSION={fetch_restic.VERSION}\n" in dockerfile
    pinned = dict(re.findall(r"^\s+(amd64|arm64)\) sum=([0-9a-f]{64}) ;;", dockerfile, re.M))
    assert pinned == {"amd64": fetch_restic.PINNED[("linux", "x86_64")][1],
                      "arm64": fetch_restic.PINNED[("linux", "aarch64")][1]}
    runbook = (REPO / "docs" / "runbooks" / "first-run.md").read_text(encoding="utf-8")
    assert all(sha in runbook for sha in pinned.values()), "the runbook's spike checks other sums"


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
    assert "nas_cacert" in service["secrets"]  # rest-server's certificate, for [backup] cacert
    template = TEMPLATE.read_text(encoding="utf-8")
    assert all(name in template for name in compose()["secrets"]), "a secret file is undocumented"


def test_the_writer_identity_comes_from_config_never_the_host_name():
    config_text = (REPO / "config.example.toml").read_text(encoding="utf-8")
    assert 'writer_id = "homelab"' in config_text and "writer_host" not in config_text
    assert "WRITER" not in COMPOSE.read_text(encoding="utf-8")


def test_no_test_sees_this_machines_settings():
    # why: the server's checkout holds a real config.toml, and a shell can export RECORDINGS_* or
    # RESTIC_* variables; the root conftest.py hides both from every test (security review).
    leaked = [name for name in os.environ if name.startswith(("RECORDINGS_", "RESTIC_"))
              and name not in ("RECORDINGS_REQUIRE_RESTIC", "RECORDINGS_TEST_RESTIC")]
    assert leaked == []
    assert config.DEFAULT_PATH.is_absolute() and not config.DEFAULT_PATH.exists()


def test_the_ci_switches_survive_the_fixture(no_settings_from_this_machine):
    # why: CI sets RECORDINGS_REQUIRE_RESTIC=1 (Task 11), so a missing restic fails the
    # real-restic tests instead of skipping them. The fixture must never hide it.
    hide = no_settings_from_this_machine
    environ = {"RECORDINGS_REQUIRE_RESTIC": "1", "RECORDINGS_TEST_RESTIC": "/opt/restic",
               "RECORDINGS_ARCHIVE": "/srv/archive", "RESTIC_PASSWORD_FILE": "/run/pw",
               "PATH": "/usr/bin"}
    assert hide(environ) == ["RECORDINGS_ARCHIVE", "RESTIC_PASSWORD_FILE"]


def test_the_readme_names_every_secret_and_environment_setting():
    # why: a setting only the code knows about is one nobody sets. Every secret's variable, later
    # stages' included, and RECORDINGS_ALLOWED_HOSTS have a row in the README's settings table.
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    table = readme[readme.index("## Configuration and secrets"):]
    for spec in config.SECRETS.values():
        assert f"`{spec.env}`" in table, spec.env
    assert "`RECORDINGS_ALLOWED_HOSTS`" in table
```

- [ ] **Step 2: Run it to see it fail**

Run: `RECORDINGS_ARCHIVE=/nonexistent RESTIC_REPOSITORY=/nonexistent uv run pytest packages/core/tests/test_deploy_template.py -v`
Expected: FAIL. `deploy.example.conf` doesn't exist yet (`FileNotFoundError`), the old
`compose.yml` uses the short syntax, `test_no_test_sees_this_machines_settings` lists
`RECORDINGS_ARCHIVE` and `RESTIC_REPOSITORY`, and `test_the_ci_switches_survive_the_fixture`
errors with `fixture 'no_settings_from_this_machine' not found`.

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
# nas_cacert (empty over the tailnet), nas_ssh_key, nas_known_hosts, ntfy_topic and deadman_url.
RECORDINGS_SECRETS_HOST=/srv/recordings/secrets
# Your config.toml (copied from config.example.toml). Mounted read-only.
RECORDINGS_CONFIG_HOST=../config.toml
# This file, backed up with the archive (spec §15.1). Mounted read-only in the backup service.
RECORDINGS_DEPLOY_ENV_HOST=./deploy.env
# Where to publish the app: this machine's Tailscale address, so only the tailnet reaches it.
# 127.0.0.1 keeps it local while testing.
RECORDINGS_BIND=127.0.0.1
RECORDINGS_PORT=8000
# The owner of /srv/recordings (`id -u` and `id -g` on the homelab server). The containers run as
# it, and the image is built with a user of that UID and GID, because ssh refuses to run as a UID
# with no /etc/passwd entry. After changing either, `make deploy` rebuilds the image.
RECORDINGS_UID=1000
RECORDINGS_GID=1000
```

Replace `docker/compose.yml` with:
```yaml
# The real deployment (the homelab server). Every variable here comes from docker/deploy.env
# (git-ignored; documented in docker/deploy.example.conf), except RECORDINGS_IMAGE_TAG, which
# `make deploy` sets to the commit, so every image is tagged by the code it runs and a rollback is
# a tag.
#
# Binds use the long syntax with create_host_path: false. The long syntax's default is true
# (Compose docs, checked 2026-10-08), and a folder Docker made in place of an unmounted disk would
# look like an empty archive. With false, a missing folder or file stops the start instead.
#
# pull_policy: never, so a recordings: image is never fetched from a registry. A rollback also
# needs `up --no-build` (make rollback): with build: present, `up` builds a missing tag from
# today's code, which would put new code under an old commit's tag.
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
    build:
      context: ..
      dockerfile: docker/Dockerfile
      # The image's user is deploy.env's UID and GID, so ssh finds it in /etc/passwd.
      args: { APP_UID: "${RECORDINGS_UID:?}", APP_GID: "${RECORDINGS_GID:?}" }
    image: "recordings:${RECORDINGS_IMAGE_TAG:?run make deploy, which tags the image by commit}"
    pull_policy: never
    # A stable name for logs. The writer's identity is config.toml's writer_id, never this.
    hostname: recordings-web
    user: "${RECORDINGS_UID:?}:${RECORDINGS_GID:?}"
    <<: *hardening
    environment:
      <<: *environment
      RECORDINGS_ROLE: web  # `recordings doctor` checks what this service needs, and no more
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
    pull_policy: never
    hostname: recordings-backup
    user: "${RECORDINGS_UID:?}:${RECORDINGS_GID:?}"
    <<: *hardening
    command: ["recordings", "ops", "--loop"]
    # On SIGTERM the loop passes it on to a running restic, which stops cleanly, and then stops
    # (Tasks 11 and 13). That can take longer than Docker's default 10 s, and a kill would leave a
    # stale restic lock behind.
    stop_grace_period: 2m
    environment:
      <<: *environment
      RECORDINGS_ROLE: backup
      RESTIC_PASSWORD_FILE: /run/secrets/restic_password
      RESTIC_REST_PASSWORD_FILE: /run/secrets/rest_password
      RECORDINGS_NAS_SSH_KEY_FILE: /run/secrets/nas_ssh_key
      RECORDINGS_NAS_KNOWN_HOSTS_FILE: /run/secrets/nas_known_hosts
      RECORDINGS_NTFY_TOPIC_FILE: /run/secrets/ntfy_topic
      RECORDINGS_DEADMAN_URL_FILE: /run/secrets/deadman_url
    # nas_cacert is rest-server's TLS certificate, for [backup] cacert = "/run/secrets/nas_cacert";
    # the file always exists, and is empty when the NAS is reached over the tailnet.
    secrets: [restic_password, rest_password, nas_cacert, nas_ssh_key, nas_known_hosts, ntfy_topic,
              deadman_url]
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
  nas_cacert: { file: "${RECORDINGS_SECRETS_HOST:?set RECORDINGS_SECRETS_HOST in docker/deploy.env}/nas_cacert" }
  nas_ssh_key: { file: "${RECORDINGS_SECRETS_HOST:?set RECORDINGS_SECRETS_HOST in docker/deploy.env}/nas_ssh_key" }
  nas_known_hosts: { file: "${RECORDINGS_SECRETS_HOST:?set RECORDINGS_SECRETS_HOST in docker/deploy.env}/nas_known_hosts" }
  ntfy_topic: { file: "${RECORDINGS_SECRETS_HOST:?set RECORDINGS_SECRETS_HOST in docker/deploy.env}/ntfy_topic" }
  deadman_url: { file: "${RECORDINGS_SECRETS_HOST:?set RECORDINGS_SECRETS_HOST in docker/deploy.env}/deadman_url" }
```

Replace `docker/Dockerfile` with the file below. Against stage 1 it pins every base image by
digest (uv becomes a stage of its own, so Dependabot sees it), adds ffmpeg, rsync, ssh and restic,
and creates the user from `APP_UID`/`APP_GID`. The digests are the multi-arch index digests, looked
up from Docker Hub and ghcr.io on 2026-10-08 (`docker build --check` resolves them):
```dockerfile
# Spec §4: Node 22 builds the React client; a Python 3.14 runtime serves it, with ffmpeg, rsync, ssh and restic.
# Every base image is pinned by digest as well as by tag (digests looked up 2026-10-08), so a moved
# tag can't change what is built. Dependabot's docker updates move the digests
# (.github/dependabot.yml), and test_deploy_template.py checks that each FROM carries one.
FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

FROM node:22-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS frontend
WORKDIR /src/packages/ui/frontend
COPY packages/ui/frontend/package.json packages/ui/frontend/package-lock.json ./
COPY packages/ui/frontend/scripts ./scripts
RUN npm ci
COPY packages/ui/frontend/ ./
RUN npm run build

# libsass (shiny -> shinychat) ships Linux wheels for amd64 only, so on arm64 it compiles from
# source and needs a C++ toolchain. Build the venv here; the runtime stage stays toolchain-free.
FROM python:3.14-slim@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2 AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Never COPY .gitignore into the image: hatch honours it and would drop the built (git-ignored)
# www/ui.js and www/ui.css from the wheel.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/core packages/core
COPY packages/ui packages/ui
COPY --from=frontend /src/packages/ui/src/recordings_ui/www/ui.js /src/packages/ui/src/recordings_ui/www/ui.css packages/ui/src/recordings_ui/www/
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2 AS runtime
ARG TARGETARCH
ARG RESTIC_VERSION=0.19.1
# Stage 2a (spec §15.1, §9.1.1): ffprobe (ffmpeg) measures imported media; rsync and ssh run the
# NAS mirror and the NAS checks; restic makes the backups. restic comes from its GitHub release,
# checked against that release's SHA256SUMS (2026-10-08), the same pins as
# scripts/fetch_restic.py (a test keeps them equal); curl and bzip2 leave with the layer.
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
# The containers run as deploy.env's RECORDINGS_UID and RECORDINGS_GID, which compose.yml passes
# here as APP_UID and APP_GID. That user must exist in /etc/passwd: ssh exits before it reads any
# option when its uid has no entry ("No user exists for uid"), and the mirror and the NAS checks
# run ssh. 10001 is for images built without them (CI, the demo). The index's named volume copies
# /index's owner and mode when Docker first creates it.
ARG APP_UID=10001
ARG APP_GID=10001
RUN set -eu; \
    if [ "$APP_UID" = 0 ] || [ "$APP_GID" = 0 ]; then echo "APP_UID and APP_GID must not be 0" >&2; exit 1; fi; \
    getent group "$APP_GID" >/dev/null || groupadd --gid "$APP_GID" app; \
    getent passwd "$APP_UID" >/dev/null || useradd --uid "$APP_UID" --gid "$APP_GID" --no-log-init --create-home --shell /usr/sbin/nologin app; \
    install -d -o "$APP_UID" -g "$APP_GID" -m 0700 /index
WORKDIR /app
COPY --from=build /app/.venv .venv
COPY demo/archive demo/archive
USER ${APP_UID}:${APP_GID}
ENV PATH=/app/.venv/bin:$PATH RECORDINGS_DEMO_ARCHIVE=/app/demo/archive
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["recordings-ui", "--host", "0.0.0.0", "--port", "8000"]
```

`docker/up-deployed.sh`:
```sh
#!/bin/sh
# Runs `docker compose <args>` on the image tag the web container already runs, however it was
# started (make deploy, make rollback, or the runbook's `rc up -d`). recordings.service calls it at
# boot, so a restart never builds, pulls or switches code. It reads no secret.
set -eu
cd "$(dirname "$0")/.."
image=$(docker ps -a --filter label=com.docker.compose.project=recordings \
  --filter label=com.docker.compose.service=web --filter label=com.docker.compose.oneoff=False \
  --format '{{.Image}}' | head -n 1)
case "$image" in
  recordings:?*) ;;
  *) echo "up-deployed: no recordings web container to start again; run make deploy first" >&2
     exit 1 ;;
esac
RECORDINGS_IMAGE_TAG=${image#recordings:} exec docker compose --env-file docker/deploy.env \
  -f docker/compose.yml "$@"
```

`docker/recordings.service` (Task 1's runbook, step 2.5, installs it at
`/etc/systemd/system/recordings.service`):
```ini
# recordings.service: starts the recordings containers at boot, once Docker, the data disk and the
# Tailscale address are all ready (stage 2a). Docker's restart policy can't do this alone: web
# publishes on the Tailscale address, which tailscaled assigns a little after it reports ready,
# and Docker never retries a container whose first start failed. A unit of its own holds back no
# other container. It starts the image tag the containers last ran (docker/up-deployed.sh).
#
# Check the two paths below before installing it (docs/runbooks/first-run.md, step 2.5):
#   /srv/recordings        $RECORDINGS_HOME, the data disk        (RequiresMountsFor=)
#   /srv/recordings/repo   this repo's checkout on the server     (WorkingDirectory=)
# If yours differ, change the installed copy (sudo systemctl edit --full recordings.service), not
# this file: an edited checkout is -dirty, and `make deploy` refuses it.
# In this file, $$ is a literal $.
[Unit]
Description=recordings: the web and backup containers
Requires=docker.service
Wants=tailscaled.service
After=docker.service tailscaled.service
RequiresMountsFor=/srv/recordings

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/srv/recordings/repo
TimeoutStartSec=300
ExecStartPre=/bin/sh -c 'i=0; until ip -4 -o addr show dev tailscale0 2>/dev/null | grep -q inet; do i=$$((i + 1)); if [ "$$i" -ge 120 ]; then echo "tailscale0 has no IPv4 address after 120 s" >&2; exit 1; fi; sleep 1; done'
ExecStart=/bin/sh docker/up-deployed.sh up -d --no-build
# compose stop gives backup its stop_grace_period (2 minutes) to stop restic cleanly.
ExecStop=/bin/sh docker/up-deployed.sh stop
TimeoutStopSec=180

[Install]
WantedBy=multi-user.target
```

`conftest.py`, at the repo root, so it covers `packages/core/tests` and `packages/ui/tests`:
```python
"""Fixtures for every test in the repo: packages/core/tests and packages/ui/tests."""

import os
from pathlib import Path

import pytest

from recordings import config

# The test switches stay. CI sets RECORDINGS_REQUIRE_RESTIC=1, so a missing restic fails the
# real-restic tests instead of skipping them; RECORDINGS_TEST_RESTIC names a restic to use.
KEEP = frozenset({"RECORDINGS_REQUIRE_RESTIC", "RECORDINGS_TEST_RESTIC"})
NO_CONFIG = Path("/nonexistent/recordings-tests/config.toml")


def host_settings(environ) -> list[str]:
    """The variables to hide: every RECORDINGS_* and RESTIC_* one, except the switches in KEEP."""
    return sorted(name for name in environ
                  if name.startswith(("RECORDINGS_", "RESTIC_")) and name not in KEEP)


@pytest.fixture(autouse=True)
def no_settings_from_this_machine(monkeypatch):
    """No test reads this machine's settings or secrets. A shell can export RECORDINGS_* and
    RESTIC_* variables, and the server's checkout holds a real config.toml where load_config looks
    by default; either would point a test at the real archive or a real secret. It returns
    host_settings, so a test can check what it hides."""
    for name in host_settings(os.environ):
        monkeypatch.delenv(name)
    monkeypatch.setattr(config, "DEFAULT_PATH", NO_CONFIG)
    return host_settings
```

`scripts/compose_smoke.py`:
```python
"""Run the real docker/compose.yml once, against temporary folders: init, the backup service's
jobs and /healthz.

    uv run python scripts/compose_smoke.py        (or: make deploy-smoke)

It proves, in the hardened containers and as this machine's user:
- every bind compose.yml needs exists, because create_host_path is false
- the image's user has this machine's UID and GID and a passwd entry, so ssh runs
  (`ssh -G nas` exits 255, "No user exists for uid", without one)
- `recordings init` works in `web`
- in `backup`, restic initialises a repository, backs up and passes the restore test, and rsync
  mirrors the archive, all for real
- the web app answers /healthz

The repository and the mirror are folders under the restore mount, so nothing contacts a NAS, and
the secret files are placeholders. It never touches docker/deploy.env or a real archive. The
network transports (rest-server, SFTP, rsync over ssh) are proven against the NAS itself by the
runbook's spike (docs/runbooks/first-run.md, step 1b).
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
SECRETS = ("restic_password", "rest_password", "nas_cacert", "nas_ssh_key", "nas_known_hosts",
           "ntfy_topic", "deadman_url")
CONFIG = """\
[archive]
writer_id = "smoke"

[backup]
repository = "/restore/smoke-repo"
extra_paths = ["/backup-extra/deploy.env"]
restore_scratch = "/restore"
cache_dir = "/state/cache/restic"

[mirror]
target = "/restore/smoke-mirror/"
"""
BACKUP_COMMANDS = (("backup", "init"), ("backup", "run"), ("backup", "restore-test"), ("mirror",))


class SmokeFailed(Exception):
    pass


def compose_run(compose: list[str], env: dict[str, str], *args: str, quiet: bool = False) -> None:
    proc = subprocess.run([*compose, *args], env=env, capture_output=quiet, text=True)
    if proc.returncode != 0:
        if quiet:
            print(proc.stderr, file=sys.stderr)
        raise SmokeFailed(f"`docker compose {' '.join(args)}` exited {proc.returncode}")


def wait_for_healthz(compose: list[str], env: dict[str, str]) -> None:
    deadline = time.monotonic() + 90
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=3) as r:
                if json.loads(r.read()) == {"ok": True, "demo": False}:
                    return
        except OSError:
            pass
        if time.monotonic() > deadline:
            subprocess.run([*compose, "logs", "web"], env=env)
            raise SmokeFailed("/healthz never answered")
        time.sleep(2)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="recordings-smoke-") as tmp:
        base = Path(tmp)
        for name in ("archive", "state", "restore", "restore/smoke-mirror", "secrets"):
            (base / name).mkdir()
        for name in SECRETS:
            # Mode 600, as the runbook makes them. The ping URL has the form alerts and doctor check
            # (https): a closed port on the container's own loopback, so a ping fails at once and
            # nothing leaves the machine.
            value = ("https://127.0.0.1:9/smoke" if name == "deadman_url"
                     else "smoke-test-placeholder")
            path = base / "secrets" / name
            path.write_text(value + "\n", encoding="utf-8")
            path.chmod(0o600)
        (base / "config.toml").write_text(CONFIG, encoding="utf-8")
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
            compose_run(compose, env, "build", "web")
            compose_run(compose, env, "run", "--rm", "web", "recordings", "init", "--json")
            compose_run(compose, env, "run", "--rm", "backup", "ssh", "-G", "nas", quiet=True)
            for command in BACKUP_COMMANDS:
                compose_run(compose, env, "run", "--rm", "backup", "recordings", *command, "--json")
            compose_run(compose, env, "up", "-d", "web")
            wait_for_healthz(compose, env)
            for path, what in (
                    (base / "archive" / "archive.json", "recordings init wrote no sentinel"),
                    (base / "restore" / "smoke-repo" / "config", "restic made no repository"),
                    (base / "restore" / "smoke-mirror" / "archive.json", "the mirror copied nothing")):
                if not path.is_file():
                    raise SmokeFailed(what)
            if list((base / "restore").glob("restore-*")):
                raise SmokeFailed("the restore test left its scratch copy behind")
        except SmokeFailed as exc:
            print(f"compose_smoke: {exc}", file=sys.stderr)
            return 1
        finally:
            subprocess.run([*compose, "down", "-v"], env=env)
        print("compose smoke: init, ssh, backup, restore test and mirror ran, and web answered "
              "/healthz, as this machine's user")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

In the `Makefile`:
1. **Add**, below the `NVM :=` line:
   ```make
   # Images are tagged by the commit they run (spec §20, 2a). Uncommitted or untracked changes add
   # -dirty, and `make deploy` refuses those: a rollback needs a commit to go back to.
   IMAGE_TAG = $(shell git rev-parse --short=12 HEAD)$(shell test -z "$$(git status --porcelain)" || echo -dirty)
   COMPOSE_ARGS = --env-file docker/deploy.env -f docker/compose.yml
   COMPOSE = RECORDINGS_IMAGE_TAG=$(IMAGE_TAG) docker compose $(COMPOSE_ARGS)
   ```
2. **Replace** the `deploy` target:
   ```make
   deploy:
   	docker compose --env-file docker/deploy.env -f docker/compose.yml up -d --build
   ```
   with:
   ```make
   deploy:
   	$(if $(findstring -dirty,$(IMAGE_TAG)),$(error make deploy: commit first: a -dirty image has no commit to roll back to))
   	$(COMPOSE) up -d --build

   # Back to an image built before, never building one: make rollback TAG=<tag>, with a tag from
   # `docker image ls recordings`.
   rollback:
   	$(if $(TAG),,$(error usage: make rollback TAG=<a tag from docker image ls recordings>))
   	RECORDINGS_IMAGE_TAG=$(TAG) docker compose $(COMPOSE_ARGS) up -d --no-build

   deploy-smoke:
   	uv run python scripts/compose_smoke.py
   ```
   `$(error …)` stops make while it expands the recipe, before any line runs, even under
   `make -n`. Keep commas out of its text: `$(if …)` would split on them.
3. **Add** `deploy-smoke` and `rollback` to `.PHONY`.

In `.github/dependabot.yml`, the `docker` ecosystem already watches `/docker`. **Replace**:
```yaml
  - package-ecosystem: docker
    directory: /docker
```
with:
```yaml
  # The Dockerfile pins each base image by tag and digest. Dependabot moves a digest when its tag
  # is rebuilt (security fixes, so these PRs matter) and a tag within the ignore rules below.
  - package-ecosystem: docker
    directory: /docker
```

In `README.md`, **replace** the `## Docker` and `## Configuration and secrets` sections with:
````markdown
## Docker

```bash
make docker                                         # demo mode, http://127.0.0.1:8000
cp config.example.toml config.toml                  # app settings (git-ignored)
cp docker/deploy.example.conf docker/deploy.env     # host folders, bind address, UID/GID (git-ignored)
make deploy                                         # builds, tags the image by commit, starts web + backup
make rollback TAG=<tag>                             # starts an older image again, never building one
make deploy-smoke                                   # the real compose file, once, against temporary folders
```

- **Images are tagged by commit.** `make deploy` refuses a tree with uncommitted or untracked
  changes, because a `-dirty` image has no commit to go back to.
- **Rolling back** starts an image that is already on the machine: `docker image ls recordings`
  lists the tags. Keep a few: `docker image prune -a` deletes the ones no container uses.
- **At boot,** `docker/recordings.service` waits for the data disk and the Tailscale address,
  then starts the containers again on the tag they last ran (`docker/up-deployed.sh`). The
  runbook installs it.
- **Set `[server] allowed_hosts`** in `config.toml` to the names you reach the app by, such
  as its host name, Tailscale name and Tailscale IP. Localhost and `base_url`'s host are
  always allowed; the app refuses any other name.

The first real run, step by step, is `docs/runbooks/first-run.md`.

## Configuration and secrets

`config.toml` is read from the current directory, unless `RECORDINGS_CONFIG` names another
file. Docker sets it to `/config/config.toml`.

| Setting | Where | Needed from |
|---|---|---|
| Archive, state and index paths, the writer ID, backups, mirror, alerts | `config.toml` (template: `config.example.toml`) | stage 1 / 2a |
| The names the app answers to | `[server] allowed_hosts` in `config.toml`, plus `RECORDINGS_ALLOWED_HOSTS` (comma-separated) | stage 1 |
| Host folders, config path, bind address and port, UID/GID | `docker/deploy.env` (template: `docker/deploy.example.conf`) | stage 1 |
| `RESTIC_PASSWORD`; `RESTIC_REST_PASSWORD`, rest-server's; the NAS's SSH key and host key, `RECORDINGS_NAS_SSH_KEY` and `RECORDINGS_NAS_KNOWN_HOSTS` (files only); `RECORDINGS_NTFY_TOPIC`; `RECORDINGS_DEADMAN_URL`; and rest-server's certificate, the `nas_cacert` file that `[backup] cacert` names | secret files, as `…_FILE` (Docker secrets) | stage 2a |
| `RECORDINGS_NTFY_TOKEN`, only for an ntfy server that needs one | a secret file, as `…_FILE` | stage 2a |
| `RECORDINGS_PLAUD_TOKEN` | a secret file, as `RECORDINGS_PLAUD_TOKEN_FILE` | stage 2b |
| `RECORDINGS_SPARK_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` | environment, or `…_FILE` | stage 4 |

Secrets never go in `config.toml`, `deploy.env`, the image or the repo. `recordings doctor`
reports which are set, never their values. The containers run as a non-root user, with
read-only file systems.
````

In `.github/workflows/ci.yml`, **add** this job after `docker`:
```yaml
  compose-smoke:
    # The real compose file, once, as the runner's own user, against temporary folders (stage-1
    # carry-over): every bind must exist, init must work in the image, ssh must find the user,
    # restic and rsync must run in the backup service, and /healthz must answer.
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

Run: `RECORDINGS_ARCHIVE=/nonexistent RESTIC_REPOSITORY=/nonexistent RECORDINGS_REQUIRE_RESTIC=1 uv run pytest packages/core/tests/test_deploy_template.py -v && uv run pytest && make deploy-smoke`
Expected: 20 passed, then the whole suite passes. The smoke test builds the image with your own UID
and GID, runs `recordings init`, `ssh -G nas`, `backup init`, `backup run`,
`backup restore-test` and `mirror`, and prints
`compose smoke: init, ssh, backup, restore test and mirror ran, and web answered /healthz, as this machine's user`.
`backup run` also logs `delivery failed: URLError` twice: its dead-man's switch pings go to the
placeholder URL, a closed port on the loopback, as intended.

- [ ] **Step 5: Commit**

```bash
git add conftest.py docker scripts/compose_smoke.py Makefile README.md .github packages/core/tests/test_deploy_template.py
git commit -m "build(docker): the first deploy: web + backup, the deploy UID, boot and rollback

The image's user is deploy.env's UID and GID (build args), because ssh exits when its uid has no
passwd entry; the smoke test runs ssh -G, restic and the mirror in the backup service to prove it.
Base images are pinned by digest. pull_policy: never, make rollback (up --no-build) and a refusal
to deploy a -dirty tree; recordings.service starts the deployed tag at boot, after the Tailscale
address and the data disk. Long-syntax binds with create_host_path: false, log rotation,
read-only file systems with cap_drop ALL. A root conftest keeps this machine's settings out of
every test. Checked: Compose v5.5.1 (up builds a missing tag; --no-build refuses),
systemd-analyze verify, and OpenSSH's getpwuid check.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 15: The Status page (backups and disk), and the header's time with its offset

**Checkpoint lens:** operations.

**Before writing any UI code, load the `/shinyreact-build-app` skill** (after `make skills`), and
check shadcn's docs for anything the page reuses. Note what you checked in the commit.
`reactive.invalidate_later` is core Shiny for Python; confirm it in the skill's references.

The recording header's time with its UTC offset is a stage-1 carry-over (spec §12.1), pulled in
here by the council's ruling of 2026-10-09: the real archive spans several time zones, so the
header shows each recording's own local time and offset, never the browser's.

Status shows each job's last good run with its detail, as a muted line under "OK". A backup's
detail ends with `; <n> GB free on the NAS`, or `; NAS free space unknown` when neither `df`
answers (Task 11), so the NAS's free space reaches the page (spec §15.1).

In demo mode, which has no settings, Status shows no disk figure: the demo's disk is a temporary
copy, or on Pages, Pyodide's in-memory file system, so any figure would be made up.

**Files:**
- Create: `packages/ui/src/recordings_ui/status.py`, `packages/ui/frontend/src/lib/nav.ts`, `packages/ui/frontend/src/lib/nav.test.ts`, `packages/ui/frontend/src/lib/when.ts`, `packages/ui/frontend/src/lib/when.test.ts`, `packages/ui/frontend/src/components/StatusPage.tsx`, `packages/ui/frontend/src/components/StatusPage.test.ts`
- Modify: `packages/core/src/recordings/disk.py` (`disk_view`), `ops.py` (`when_label`, `status_summary`), `state.py` (`State.read_only`)
- Modify: `packages/ui/src/recordings_ui/runtime.py`, `views.py`, `shiny_app.py`, `app.py`
- Modify: `packages/ui/frontend/src/App.tsx`, `components/TopBar.tsx`, `components/DetailsTab.tsx`, `components/RecordingPane.tsx` (the header's time), `types.ts`, `app.css`
- Test: `packages/core/tests/test_ops.py` (the summary, the read-only state), `packages/ui/tests/test_status.py`, `test_shiny_server.py`, `test_demo_guard.py`, `e2e/test_library.py`, `pages/test_pages_site.py`

**Interfaces:**
- Consumes:
  - from Task 2: `State._connect(mode)`, and the line in `State.db()` that calls it, `with closing(self._connect("rw")) as conn:`
  - from Task 11: `State.last_run`
  - from Task 5b: `State.flags`
  - from Task 9: `disk_status`, `disk_thresholds`
  - from Tasks 11 and 13: `run_job`'s fixed failure detail, `"<exception name>; exit <code>"`
  - from Task 13: `ops.enabled_jobs`
  - from Task 10: `test_demo_guard.py` and its audit hook
- Produces:
  - **core:**
    - `recordings.disk.disk_view(status: DiskStatus) -> dict`
    - `recordings.ops.when_label(t: datetime, now: datetime, timezone_name: str) -> str`
    - `recordings.ops.status_summary(state: State, *, archive_root: Path, now: datetime, timezone_name: str, warn: float, stop: float, jobs: tuple[str, ...]) -> dict`. Each job's row has `last_ok`, `last_ok_detail` (the last good run's fixed detail), `failing`, `detail` and `attention`.
    - `State.read_only(path) -> State | None`: every connection it makes is `mode=ro`
  - **UI, Python:**
    - `recordings_ui.status`: `@dataclass(frozen=True) StatusSettings(state_path, timezone_name, warn, stop, jobs)`, `status_settings(cfg) -> StatusSettings` and `status_view(archive_root: Path, settings: StatusSettings | None, *, now: datetime | None = None) -> dict`. A `state.db` that can't be read shows as attention, not as an error. With no settings (demo mode) its `disk` is None: no figure.
    - `runtime.configure(archive, *, media_base=None, status=None)` and `runtime.status_settings()`
    - `views.status_view(archive, settings)`
    - the Shiny output `status`
  - **UI, TypeScript:**
    - `type Page = "library" | "status"`
    - `pageFromHash(hash: string): Page` and `hashFor(page: Page): string`
    - `headerWhen(recordedAt: string): string`, in `lib/when.ts`
    - `interface StatusView`
    - `<StatusPage />`, and `<TopBar page onPage />`
    - the header's `<time data-testid="recording-when">`

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
    # run_job's fixed failure template (Tasks 11 and 13): never the exception's own text
    state.record_run("restore-test", T0, T0, False, "BackupError; exit 1")
    summary = status_summary(state, archive_root=writer.root, now=T0,
                             timezone_name="America/Vancouver", warn=20.0, stop=5.0,
                             jobs=("disk", "backup", "restore-test"))
    jobs = {j["job"]: j for j in summary["jobs"]}
    assert set(jobs) == {"backup", "restore-test"}
    assert jobs["backup"]["last_ok"] == "2026-10-08 22:00 (3 h ago)"  # 05:00 UTC is 22:00 PDT
    assert jobs["backup"]["attention"] and not jobs["backup"]["failing"]
    assert jobs["restore-test"]["failing"] and jobs["restore-test"]["last_ok"] is None
    assert jobs["restore-test"]["detail"] == "BackupError; exit 1"
    assert summary["attention"][0] == "The last backup is more than 2 hours old."
    assert "Restore test failed: BackupError; exit 1" in summary["attention"]
    assert summary["disk"]["level"] == "warn" and summary["disk"]["free_percent"] == 15.0
    assert when_label(T0 - timedelta(seconds=20), T0, "UTC") == "2026-10-09 07:59 (just now)"
    assert when_label(T0 - timedelta(days=3), T0, "UTC") == "2026-10-06 08:00 (3 days ago)"


def test_status_shows_the_last_good_runs_detail_with_the_nas_free_space(writer):
    # why: §15.1. "NAS free space unknown" is in a good backup's detail (Task 11), and Status must
    # show it, not only a failing job's detail.
    from recordings.ops import status_summary
    detail = "snapshot 0123456789ab: 11 files, 4096 bytes added; NAS free space unknown"
    writer.state.record_run("backup", T0 - timedelta(minutes=5), T0 - timedelta(minutes=5), True,
                            detail)
    writer.state.record_run("restore-test", T0, T0, False, "BackupError; exit 1")
    summary = status_summary(writer.state, archive_root=writer.root, now=T0, timezone_name="UTC",
                             warn=20.0, stop=5.0, jobs=("backup", "restore-test"))
    jobs = {j["job"]: j for j in summary["jobs"]}
    assert jobs["backup"]["last_ok_detail"] == detail and jobs["backup"]["detail"] is None
    assert jobs["restore-test"]["last_ok_detail"] is None


def test_a_read_only_state_never_creates_or_writes(writer, tmp_path):
    # why: Status and doctor read state.db while the writer uses it. A reader must never make an
    # empty state.db where none exists, nor change one.
    import sqlite3

    import pytest

    from recordings.state import State

    assert State.read_only(tmp_path / "nowhere") is None
    assert not (tmp_path / "nowhere").exists()
    state = State.read_only(writer.state.path)
    assert state.meta("archive_uuid") == writer.state.meta("archive_uuid")
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        state.set_meta("x", "y")
```

`packages/ui/tests/test_status.py`:
```python
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from recordings_ui.status import StatusSettings, status_view

T0 = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)


def test_the_demo_shows_no_disk_figure(demo_archive):
    # why: demo mode has no settings. Its disk is a temporary copy, or on Pages, Pyodide's
    # in-memory file system: a free-space figure would be made up, on the public demo.
    view = status_view(demo_archive, None, now=T0)
    assert view == {"configured": False, "jobs": [], "attention": [], "flags": 0, "disk": None}


def test_without_a_state_folder_status_shows_disk_only(demo_archive):
    settings = StatusSettings(state_path=None, timezone_name="UTC", warn=20.0, stop=5.0,
                              jobs=("disk",))
    view = status_view(demo_archive, settings, now=T0)
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
    assert job["last_ok_detail"] == "snapshot x" and not job["attention"]


def test_a_damaged_state_db_needs_attention_rather_than_breaking_the_page(writer):
    # why: Status is where Dan looks when something is wrong; a traceback there hides the rest.
    writer.state.db_path.write_bytes(b"not a database, " * 64)
    settings = StatusSettings(state_path=writer.state.path, timezone_name="UTC", warn=20.0,
                              stop=5.0, jobs=("disk", "backup"))
    view = status_view(writer.root, settings, now=T0)
    assert view["configured"] is True and view["jobs"] == []
    assert any("restore it from the backup" in a for a in view["attention"])
    assert view["disk"] is not None


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
    assert status["configured"] is False and status["disk"] is None  # demo mode: no disk figure
```

In `packages/ui/tests/test_demo_guard.py`, **add** after the `views.recording_view` loop, at the
loop's own indentation (12 spaces: inside `with _watching() as events:` and its `try`, before
`finally: runtime.configure(None)`):
```python
            # What create_app configured for Status in demo mode: if it ever read the real config's
            # [state] path, opening the real state.db would show up in the audit hook's events.
            views.status_view(archive, runtime.status_settings())
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

`packages/ui/frontend/src/lib/when.test.ts`:
```ts
import { describe, expect, it } from "vitest";

import { headerWhen } from "./when";

describe("headerWhen", () => {
  it("shows the recording's own local time and UTC offset, never the browser's", () => {
    expect(headerWhen("1962-09-12T10:00:00-05:00")).toBe("1962-09-12 10:00 UTC-05:00");
    expect(headerWhen("2026-10-06T14:05:59.123456+09:00")).toBe("2026-10-06 14:05 UTC+09:00");
    expect(headerWhen("2026-10-06T23:30:00+05:30")).toBe("2026-10-06 23:30 UTC+05:30");
    expect(headerWhen("2026-10-06T08:00:00+00:00")).toBe("2026-10-06 08:00 UTC+00:00");
    expect(headerWhen("2026-10-06T08:00:00Z")).toBe("2026-10-06 08:00 UTC+00:00");
  });
  it("shows a string it can't read as it is", () => {
    expect(headerWhen("2026-10-06T08:00:00")).toBe("2026-10-06T08:00:00");
    expect(headerWhen("")).toBe("");
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
    expect(page.get_by_test_id("disk-unknown")).to_be_visible()  # the demo shows no disk figure
    expect(page).to_have_url(re.compile(r"#/status$"))
    page.get_by_role("button", name="Library").click()
    expect(page.get_by_test_id("status-page")).to_have_count(0)


def test_the_header_shows_the_local_time_with_its_offset(page: Page, server_url):
    # why: the archive spans several time zones, so the header shows each recording's own local
    # time and UTC offset (spec §12.1), whatever zone the browser is in.
    open_library(page, server_url)
    page.get_by_test_id("recording-row").filter(has_text="JFK").click()
    expect(page.get_by_test_id("recording-when")).to_have_text("1962-09-12 10:00 UTC-05:00")
```

`packages/ui/frontend/src/components/StatusPage.test.ts` (a `.ts` file, so Vitest's
`src/**/*.test.ts` finds it; it renders with `createElement`, and the shinyreact hook is mocked):
```ts
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { StatusJob, StatusView } from "../types";

const shown = vi.hoisted(() => ({ view: null as unknown }));
vi.mock("../sr", () => ({ useShinyOutputValue: () => shown.view }));

import { StatusPage } from "./StatusPage";

const DETAIL = "snapshot 0123456789ab: 11 files, 4096 bytes added; NAS free space unknown";
const backup: StatusJob = {
  job: "backup", label: "Backup", last_ok: "2026-10-09 07:55 (5 min ago)", failing: false,
  detail: null, attention: false, last_ok_detail: DETAIL,
};

function render(view: StatusView): string {
  shown.view = view;
  return renderToStaticMarkup(createElement(StatusPage));
}

describe("StatusPage", () => {
  it("shows a good run's detail under OK, so the NAS's free space reaches the page", () => {
    const html = render({ configured: true, jobs: [backup], attention: [], flags: 0, disk: null });
    expect(html).toContain(`<td>OK<div class="muted" data-testid="status-job-backup-detail">${DETAIL}</div></td>`);
  });
  it("shows a failing job's own detail instead", () => {
    const failing = { ...backup, failing: true, attention: true, detail: "BackupError; exit 1" };
    const html = render({ configured: true, jobs: [failing], attention: [], flags: 0, disk: null });
    expect(html).toContain("Failing: BackupError; exit 1");
    expect(html).not.toContain("status-job-backup-detail");
  });
  it("says the free space is unknown when there is no disk figure", () => {
    const html = render({ configured: false, jobs: [], attention: [], flags: 0, disk: null });
    expect(html).toContain('data-testid="disk-unknown"');
    expect(html).not.toContain('role="meter"');
  });
});
```

Add to `packages/ui/tests/pages/test_pages_site.py`:
```python
def test_the_status_page_shows_no_made_up_disk(page: Page, site_url, watch):
    # why: on Pages the "disk" is Pyodide's in-memory file system. Status opens, and says the free
    # space is unknown rather than showing a figure for it.
    app, _ = open_demo(page, site_url)
    app.get_by_role("button", name="Status").click()
    expect(app.get_by_test_id("status-page")).to_be_visible(timeout=RENDER_TIMEOUT)
    expect(app.get_by_test_id("backups-unconfigured")).to_be_visible(timeout=RENDER_TIMEOUT)
    expect(app.get_by_test_id("disk-unknown")).to_be_visible(timeout=RENDER_TIMEOUT)
    expect(app.get_by_role("meter")).to_have_count(0)
    watch.assert_clean()
```

- [ ] **Step 2: Run them to see them fail**

Run each, from the repo root:
```bash
uv run pytest packages/core/tests/test_ops.py -v
uv run pytest packages/ui/tests/test_shiny_server.py packages/ui/tests/test_demo_guard.py -v
uv run pytest packages/ui/tests/test_status.py -v
(cd packages/ui/frontend && npm test)
```
Expected:
- `test_ops.py`: FAIL with `ImportError: cannot import name 'status_summary' from 'recordings.ops'`
  and `AttributeError: type object 'State' has no attribute 'read_only'`.
- The Shiny and demo-guard tests: FAIL. The demo guard raises
  `AttributeError: module 'recordings_ui.views' has no attribute 'status_view'`, and the session
  has no `status` output.
- `test_status.py`: an error at collection, `ModuleNotFoundError: No module named 'recordings_ui.status'`.
- Vitest fails to resolve `./nav`, `./when` and `./StatusPage`.
- The new Pages test is checked in Step 5, once `make pages` has built the site.

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

In `packages/core/src/recordings/state.py`:
1. In `State.__init__`, **add** after the line `self.path = Path(path)`:
   ```python
           self._read_only = False  # read_only() sets it: every connection is then mode=ro
   ```
2. **Add** to `State`, just before `_connect`:
   ```python
       @classmethod
       def read_only(cls, path: Path) -> State | None:
           """For readers (Status, doctor). It never creates, migrates or writes anything, and
           returns None when there is no state.db to read."""
           state = cls(path)
           if not state.db_path.is_file():
               return None
           state._read_only = True
           return state
   ```
3. In `db()`, **replace** the line `with closing(self._connect("rw")) as conn:` with:
   ```python
           with closing(self._connect("ro" if self._read_only else "rw")) as conn:
   ```
   The next line, `PRAGMA synchronous=FULL`, is harmless on a read-only connection.

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
            "last_ok_detail": good.detail if good else None,  # the NAS's free space, for a backup
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
exists, so the Pages demo (Pyodide), which has none, never needs it. A state.db that can't be read
is shown as something needing attention, with the way back, rather than breaking the page. Demo
mode has no settings, and shows no disk figure.
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
    if settings is None:
        # Demo mode: its disk is a temporary copy, or on Pages, Pyodide's in-memory file system,
        # so any free-space figure would be made up (§17).
        return {"configured": False, "jobs": [], "attention": [], "flags": 0, "disk": None}
    from recordings.disk import STOP_FREE_PERCENT, WARN_FREE_PERCENT, disk_status, disk_view

    now = now or datetime.now(timezone.utc)
    warn = settings.warn if settings else WARN_FREE_PERCENT
    stop = settings.stop if settings else STOP_FREE_PERCENT
    state_db = settings.state_path / "state.db" if settings and settings.state_path else None
    if state_db is None or not state_db.is_file():
        return {"configured": False, "jobs": [], "attention": [], "flags": 0,
                "disk": disk_view(disk_status(archive_root, expected_uuid=None, warn=warn,
                                              stop=stop))}
    import sqlite3

    from recordings.ops import status_summary
    from recordings.state import State

    try:
        return status_summary(State.read_only(settings.state_path), archive_root=archive_root,
                              now=now, timezone_name=settings.timezone_name, warn=warn,
                              stop=stop, jobs=settings.jobs)
    except sqlite3.DatabaseError:
        return {"configured": True, "jobs": [], "flags": 0,
                "attention": ["state.db can't be read: restore it from the backup (runbook: "
                              "Restoring for real)."],
                "disk": disk_view(disk_status(archive_root, expected_uuid=None, warn=warn,
                                              stop=stop))}
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
  job: string; label: string; last_ok: string | null; last_ok_detail: string | null;
  failing: boolean; detail: string | null; attention: boolean;
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
                  <td>
                    {j.failing ? `Failing: ${j.detail}` : j.attention ? "Overdue" : "OK"}
                    {!j.failing && j.last_ok_detail && (
                      <div className="muted" data-testid={`status-job-${j.job}-detail`}>{j.last_ok_detail}</div>
                    )}
                  </td>
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
        ) : <p className="muted" data-testid="disk-unknown">Free space unknown.</p>}
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

`packages/ui/frontend/src/lib/when.ts`:
```ts
/** The recording header's time (spec §12, §12.1): the recording's own local date and 24-hour time
 * with its UTC offset, as `2026-10-06 14:05 UTC+09:00`. It is read from the stored ISO string and
 * never converted to the browser's time zone, because the archive spans several. */
export function headerWhen(recordedAt: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/.exec(recordedAt);
  if (!m) return recordedAt;
  return `${m[1]} ${m[2]} UTC${m[3] === "Z" ? "+00:00" : m[3]}`;
}
```

In `packages/ui/frontend/src/components/RecordingPane.tsx`:
1. **Replace** `import { useShinyOutputStatus, useShinyOutputValue } from "../sr";` with:
   ```tsx
   import { headerWhen } from "../lib/when";
   import { useShinyOutputStatus, useShinyOutputValue } from "../sr";
   ```
2. **Replace** the header's meta line,
   `<div className="meta">{[rec.when, rec.duration, rec.sources.map((s) => s.kind).join(", ")].filter(Boolean).join(" · ")} · <span className="mono">{rec.id}</span></div>`,
   with:
   ```tsx
           <div className="meta">
             <time dateTime={rec.recorded_at} data-testid="recording-when">{headerWhen(rec.recorded_at)}</time>
             {[rec.duration, [...new Set(rec.sources.map((s) => s.kind))].join(", ")].filter(Boolean).map((x) => ` · ${x}`).join("")}
             {" · "}<span className="mono">{rec.id}</span>
           </div>
   ```
   The source kinds are de-duplicated for the same reason as the Details table's keys: one source
   entry per snapshot would otherwise read "plaud, plaud, plaud". `rec.when` stays in the view,
   unused here; the Library's rows still show their own `when`.

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
Expected: pytest, Vitest (with `nav.test.ts`, `when.test.ts` and `StatusPage.test.ts`) and the
Playwright tests all pass, `test_the_status_page_shows_backups_and_disk` and
`test_the_header_shows_the_local_time_with_its_offset` included. The Pages smoke test passes,
`test_the_status_page_shows_no_made_up_disk` included: the static demo (Pyodide) now runs the
Status output too, with no state folder, no `sqlite3` and no disk figure.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/recordings packages/core/tests/test_ops.py packages/ui
git commit -m "feat(ui): the Status page's backup and disk panels, read-only; the header's offset

Backups need attention after 2 hours (spec §12.5), and the disk warns below 20% and stops imports
below 5%. Status reads state.db read-only (mode=ro), never loads sqlite3 without one, so the Pages
demo is unaffected, and shows a damaged state.db as needing attention. The recording header shows
the recording's own local time with its UTC offset (§12.1, a stage-1 carry-over). Checked: the
shinyreact-build-app skill (reactive_output, useShinyOutputValue) and Shiny's
reactive.invalidate_later.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: `recordings doctor` for 2a

**Checkpoint lens:** security and privacy.

`doctor` runs in each Compose service, and each has its own role (council round 1, 2026-10-09):
- **`web`** serves the Library and runs the import. It holds no backup, mirror or alert secret, so
  doctor checks that none is present, and skips the backup, mirror and alert checks.
- **`backup`** runs the backups, the mirror and the alerts. Its archive mount is read-only, so the
  hard-link probe is skipped there. ssh needs this user's passwd entry, so doctor checks it.
- **`all`**, the default outside Docker, checks everything, as before.

The role comes from `--role`, else `RECORDINGS_ROLE` (Task 14's Compose sets it per service),
else `all`. Two checks read config.toml alone, so every role reports them, as the runbook's step
2.4 expects:
- `[backup] prune = false` is a warning: retention must then run on the NAS.
- `[plaud] consent_list` is information only. In 2a it is mounted just for import runs, and the
  import reads it and fails closed by itself, so its absence in a long-running service is no
  problem.

**Files:**
- Create: `packages/core/src/recordings/doctor.py`
- Modify: `packages/core/src/recordings/config.py` (remove `doctor`), `cli.py` (`doctor --role`, using `recordings.doctor`)
- Modify: `packages/core/tests/test_config.py` (imports `doctor` from `recordings.doctor`; the presence test initialises its archive)
- Modify: `scripts/compose_smoke.py` (runs `doctor --role backup` in the hardened container)
- Test: `packages/core/tests/test_doctor.py`

**Interfaces:**
- Consumes:
  - from stage 1, extended by Tasks 11 and 13: `SECRETS` and `secret`
  - from Task 11: `secret_path`, `SecretSpec.file_only` and `.optional`, `ssh.nas_free_bytes` (ssh `df`, then sftp's `df`; None when neither answers), and `backup.from_config`, whose `BackupConfig` has `cacert: Path | None` (the optional `[backup] cacert`), `prune`, `rest_username`, `restore_scratch`, `nas` and `nas_repo_path`
  - from Task 2: `read_sentinel`, `init_archive`
  - from Task 9: the `[plaud] consent_list` setting, which the import itself reads, failing closed
  - from Task 15: `State.read_only`
  - from Task 4: `Index`
  - from Task 12: `mirror.from_config`, `is_remote`
  - from Task 13: `alerts.from_config`, whose every `ConfigError` (a bad URL or topic) names no value, and the optional `ntfy_token` secret (`RECORDINGS_NTFY_TOKEN`), which is never a config.toml key
  - from Task 8: the import measures media with ffprobe and, for anything but a plain WAV, ffmpeg
  - from Task 14: Compose sets `RECORDINGS_ROLE` to `web` or `backup` in each service, and the image creates the `APP_UID` user from deploy.env's `RECORDINGS_UID`; `scripts/compose_smoke.py`'s `BACKUP_COMMANDS` and docstring, and its placeholder secrets (mode 600, with an https ping URL)
- Produces:
  - `recordings.doctor.doctor(environ: Mapping[str, str], *, role: str | None = None) -> dict`, with the keys `role`, `config`, `user`, `archive`, `state`, `index`, `backup`, `mirror`, `alerts`, `consent_list`, `tools`, `secrets`, `problems` and `warnings`
  - `recordings.doctor.ROLES = ("web", "backup", "all")`
  - `recordings.doctor.JOB_SECRETS`: the backup service's secrets, which `web` must not hold
  - `recordings.doctor.SECRETS_DIR = Path("/run/secrets")`
  - `recordings.doctor.hard_links_work(root: Path) -> bool` and `read_only_mount(root: Path) -> bool`
  - `recordings.doctor.TOOLS`
  - CLI: `recordings doctor [--role web|backup|all] [--json]`

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_doctor.py`:
```python
import json
import subprocess

import pytest

from recordings import doctor as doctor_module
from recordings.cli import main
from recordings.config import SECRETS
from recordings.doctor import JOB_SECRETS, doctor, read_only_mount
from recordings.init import init_archive
from recordings.sentinel import SENTINEL
from recordings.ssh import nas_free_bytes

NAS_FREE = 500 * 1024 ** 3

SENTINELS = ("pw-do-not-print", "key-do-not-print", "topic-do-not-print", "ping-do-not-print",
             "rest-do-not-print", "hunter2")
SECRET_ENV = ("RESTIC_PASSWORD_FILE", "RESTIC_REST_PASSWORD_FILE", "RECORDINGS_NAS_SSH_KEY_FILE",
              "RECORDINGS_NAS_KNOWN_HOSTS_FILE", "RECORDINGS_NTFY_TOPIC_FILE",
              "RECORDINGS_NTFY_TOKEN_FILE", "RECORDINGS_DEADMAN_URL_FILE")


def server(tmp_path, *, repository="sftp:backup@nas:/volume1/backups/recordings",
           backup_extra=""):
    init_archive(tmp_path / "archive", tmp_path / "state", writer_id="homelab")
    (tmp_path / "scratch").mkdir()
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    for name, value in (("pw", "pw-do-not-print"), ("key", "key-do-not-print"),
                        ("known_hosts", "nas ssh-ed25519 AAAAexample"),
                        ("topic", "topic-do-not-print"),
                        ("ping", "https://hc.example/ping-do-not-print"),
                        ("rest_password", "rest-do-not-print")):
        (secrets / name).write_text(value + "\n", encoding="utf-8")
        (secrets / name).chmod(0o600)  # as the runbook makes them
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{tmp_path / "archive"}"\nwriter_id = "homelab"\n'
        f'[state]\npath = "{tmp_path / "state"}"\n[index]\npath = "{tmp_path / "index.db"}"\n'
        f'[nas]\nssh = "backup@nas"\n'
        f'[backup]\nrepository = "{repository}"\nrestore_scratch = "{tmp_path / "scratch"}"\n'
        f'nas_repo_path = "/volume1/backups/recordings"\n{backup_extra}'
        f'[mirror]\ntarget = "backup@nas:/volume1/recordings-mirror/"\n'
        f'[alerts]\nntfy_server = "https://ntfy.sh"\n', encoding="utf-8")
    return {"RECORDINGS_CONFIG": str(cfg), "RESTIC_PASSWORD_FILE": str(secrets / "pw"),
            "RECORDINGS_NAS_SSH_KEY_FILE": str(secrets / "key"),
            "RECORDINGS_NAS_KNOWN_HOSTS_FILE": str(secrets / "known_hosts"),
            "RECORDINGS_NTFY_TOPIC_FILE": str(secrets / "topic"),
            "RECORDINGS_DEADMAN_URL_FILE": str(secrets / "ping")}


def web_only(environ):
    """What the web service has: the same config, and no secrets."""
    return {k: v for k, v in environ.items() if k not in SECRET_ENV}


@pytest.fixture(autouse=True)
def tools_installed(monkeypatch):
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.fixture(autouse=True)
def no_docker_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor_module, "SECRETS_DIR", tmp_path / "run-secrets")


@pytest.fixture(autouse=True)
def nas_answers_df(monkeypatch):
    # No test reaches a real NAS: the free-space check answers from here.
    monkeypatch.setattr(doctor_module, "nas_free_bytes", lambda nas, path: NAS_FREE)


def test_a_fully_configured_writer_has_no_problems(tmp_path):
    report = doctor(server(tmp_path))
    assert report["problems"] == [] and report["warnings"] == []
    assert report["role"] == "all" and report["user"]["passwd_entry"] is True
    assert report["archive"]["sentinel"] and report["archive"]["hard_links"]
    assert report["archive"]["read_only"] is False
    assert report["state"]["matches"] is True
    assert report["backup"] == {"transport": "sftp", "password_set": True, "prune": True,
                                "cacert_exists": None, "restore_scratch_exists": True,
                                "nas_free_check": True, "nas_free_bytes": NAS_FREE,
                                "restic_installed": True}
    assert report["mirror"] == {"remote": True, "rsync_installed": True}
    assert report["alerts"] == {"ntfy_server_set": True, "topic_set": True, "deadman_set": True}
    for name in ("restic_password", "nas_ssh_key", "nas_known_hosts", "ntfy_topic", "deadman_url"):
        assert report["secrets"][name]["set"], name
    assert report["secrets"]["nas_ssh_key"]["env"] == "RECORDINGS_NAS_SSH_KEY_FILE"


def test_doctor_never_prints_a_secret_or_the_repository(tmp_path):
    environ = server(tmp_path, repository="rest:https://recordings:hunter2@nas:8000/recordings/")
    environ["RESTIC_REST_PASSWORD_FILE"] = str(tmp_path / "secrets" / "rest_password")
    report = doctor(environ)
    assert any("credentials" in p for p in report["problems"])  # Task 11 refuses user:pass@
    text = json.dumps(report, ensure_ascii=False)
    # A topic ntfy would refuse and a ping URL without https: their errors must not echo them.
    (tmp_path / "secrets" / "topic").write_text("topic-do-not-print!\n", encoding="utf-8")
    (tmp_path / "secrets" / "ping").write_text("http://hc.example/ping-do-not-print\n",
                                               encoding="utf-8")
    text += json.dumps(doctor(environ), ensure_ascii=False)
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
    text = cfg.read_text(encoding="utf-8")
    cfg.write_text(text.replace('writer_id = "homelab"', 'writer_id = "laptop"'), encoding="utf-8")
    report = doctor(environ)
    assert report["state"]["matches"] is False
    assert any("another archive or another writer_id" in p for p in report["problems"])


def test_a_damaged_state_db_is_a_problem_with_the_way_back(tmp_path):
    environ = server(tmp_path)
    (tmp_path / "state" / "state.db").write_bytes(b"not a database, " * 64)
    assert any("restore it from the backup" in p for p in doctor(environ)["problems"])


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


def test_a_secret_file_others_can_read_is_a_warning(tmp_path):
    # why: ssh refuses such a key outright, and any secret file should be 600.
    environ = server(tmp_path)
    (tmp_path / "secrets" / "key").chmod(0o644)
    report = doctor(environ)
    assert report["problems"] == []
    assert [w for w in report["warnings"] if "chmod 600" in w] == [
        "RECORDINGS_NAS_SSH_KEY_FILE names a file that group or others can open (mode 644): "
        "chmod 600 it"]
    assert "key-do-not-print" not in json.dumps(report)


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
    assert report["tools"] == {"restic": False, "rsync": False, "ssh": False, "ffprobe": False,
                               "ffmpeg": False}
    assert any("restic is not installed" in p for p in report["problems"])
    assert any("dead-man" in w for w in report["warnings"])
    assert any("ffprobe and ffmpeg not installed" in w for w in report["warnings"])


def test_retention_off_here_is_a_warning_in_every_role(tmp_path):
    # why: with rest-server --append-only, the server can't prune, so retention runs on the NAS.
    # If that task is missing, nothing else says so, and the repository grows without limit.
    environ = server(tmp_path, backup_extra="prune = false\n")
    warning = ("retention is off here: it must run on the NAS, or the repository grows without "
               "limit")
    for role, env in (("all", environ), ("backup", environ), ("web", web_only(environ))):
        report = doctor(env, role=role)
        assert report["problems"] == [] and report["warnings"] == [warning], role
    assert doctor(environ)["backup"]["prune"] is False


def test_a_consent_list_mounted_only_for_imports_is_information(tmp_path):
    # why: in 2a the list is mounted only for `import-audio-router` runs, which read it and fail
    # closed by themselves. The long-running services don't have it, and that is no problem.
    environ = server(tmp_path)
    cfg = tmp_path / "config.toml"
    base = cfg.read_text(encoding="utf-8")
    cfg.write_text(base + '[plaud]\nconsent_list = "/consent/consent-list.md"\n', encoding="utf-8")
    for role, env in (("web", web_only(environ)), ("backup", environ)):
        report = doctor(env, role=role)
        assert report["problems"] == [] and report["warnings"] == [], role
        assert report["consent_list"] == {
            "path": "/consent/consent-list.md", "here": False,
            "note": "not mounted here; the import reads it and fails closed"}
    listed = tmp_path / "consent-list.md"
    listed.write_text("- " + "d" * 32 + "\n", encoding="utf-8")
    cfg.write_text(base + f'[plaud]\nconsent_list = "{listed}"\n', encoding="utf-8")
    report = doctor(web_only(environ), role="web")
    assert report["consent_list"] == {"path": str(listed), "here": True, "note": None}
    assert "d" * 32 not in json.dumps(report)  # its IDs are never read


def test_the_rest_server_setup_has_no_problems(tmp_path):
    # why: rest-server --append-only is the primary transport (council ruling, 2026-10-09).
    (tmp_path / "rest-server.crt").write_text("a test certificate\n", encoding="utf-8")
    environ = server(tmp_path, repository="rest:https://nas:8000/recordings/", backup_extra=(
        f'rest_username = "recordings"\nprune = false\n'
        f'cacert = "{tmp_path / "rest-server.crt"}"\n'))
    environ["RESTIC_REST_PASSWORD_FILE"] = str(tmp_path / "secrets" / "rest_password")
    report = doctor(environ)
    assert report["problems"] == []
    assert report["backup"]["transport"] == "rest" and report["backup"]["cacert_exists"] is True
    assert "rest-do-not-print" not in json.dumps(report)


def test_a_missing_cacert_is_a_problem(tmp_path):
    environ = server(tmp_path, repository="rest:https://nas:8000/recordings/",
                     backup_extra=f'cacert = "{tmp_path / "missing.crt"}"\n')
    assert any("cacert" in p for p in doctor(environ)["problems"])


def test_a_rest_user_without_its_password_is_a_problem(tmp_path):
    environ = server(tmp_path, repository="rest:https://nas:8000/recordings/",
                     backup_extra='rest_username = "recordings"\n')
    assert any("RESTIC_REST_PASSWORD_FILE" in p for p in doctor(environ)["problems"])


def test_a_nas_whose_free_space_cant_be_read_is_a_warning(tmp_path, monkeypatch):
    # why: §15.1. When neither ssh's df nor sftp's df answers, the NAS's free space is unknown,
    # and doctor says so rather than nothing.
    calls = []

    def neither_answers(cmd, **kwargs):
        calls.append(cmd[0])
        return subprocess.CompletedProcess(cmd, 255, "", "")

    monkeypatch.setattr(doctor_module, "nas_free_bytes",
                        lambda nas, path: nas_free_bytes(nas, path, runner=neither_answers))
    report = doctor(server(tmp_path), role="backup")
    assert calls == ["ssh", "sftp"]
    assert report["problems"] == [] and report["backup"]["nas_free_bytes"] is None
    assert report["warnings"] == [
        "the NAS's free space is unknown: neither ssh df nor sftp df answered"]


def test_the_web_role_needs_no_secrets_and_refuses_any(tmp_path):
    full = server(tmp_path)
    report = doctor(web_only(full), role="web")
    assert report["problems"] == [] and report["role"] == "web" and report["user"] is None
    assert report["backup"] is None and report["mirror"] is None and report["alerts"] is None
    report = doctor(web_only(full) | {"RESTIC_PASSWORD_FILE": full["RESTIC_PASSWORD_FILE"]},
                    role="web")
    assert any("restic_password" in p and "web" in p for p in report["problems"])
    assert "pw-do-not-print" not in json.dumps(report)
    for name in JOB_SECRETS:  # the optional ntfy_token included
        held = web_only(full) | {f"{SECRETS[name].env}_FILE": full["RESTIC_PASSWORD_FILE"]}
        assert any(name in p for p in doctor(held, role="web")["problems"]), name
    (tmp_path / "run-secrets").mkdir()  # a secret Compose mounted into web by mistake
    (tmp_path / "run-secrets" / "nas_ssh_key").write_text("x\n", encoding="utf-8")
    assert any("nas_ssh_key" in p for p in doctor(web_only(full), role="web")["problems"])


def test_every_job_secret_is_a_known_secret():
    assert set(JOB_SECRETS) <= set(SECRETS)


def test_the_backup_role_skips_the_hard_link_probe_on_a_read_only_archive(tmp_path, monkeypatch):
    environ = server(tmp_path)
    monkeypatch.setattr(doctor_module, "read_only_mount", lambda root: True)
    monkeypatch.setattr(doctor_module, "hard_links_work",
                        lambda root: pytest.fail("probed a read-only archive"))
    report = doctor(environ, role="backup")
    assert report["problems"] == [] and report["index"] is None
    assert report["archive"]["read_only"] is True and report["archive"]["hard_links"] is None
    report = doctor(environ, role="all")  # a writer can't work on a read-only archive
    assert any("mounted read-only" in p for p in report["problems"])


def test_a_writable_folder_is_not_a_read_only_mount(tmp_path):
    assert read_only_mount(tmp_path) is False


def test_a_user_without_a_passwd_entry_is_a_problem(tmp_path, monkeypatch):
    # why: ssh exits at once when its uid has no passwd entry, so the mirror and the NAS's
    # free-space check would fail in the backup service.
    environ = server(tmp_path)

    def no_entry(uid):
        raise KeyError(f"getpwuid(): uid not found: {uid}")

    monkeypatch.setattr(doctor_module.pwd, "getpwuid", no_entry)
    report = doctor(environ, role="backup")
    assert report["user"]["passwd_entry"] is False
    assert any("passwd entry" in p for p in report["problems"])
    assert doctor(web_only(environ), role="web")["problems"] == []  # web runs no ssh


def test_the_role_comes_from_the_environment_and_an_unknown_one_checks_all(tmp_path):
    environ = server(tmp_path)
    assert doctor(environ | {"RECORDINGS_ROLE": "backup"})["role"] == "backup"
    report = doctor(environ | {"RECORDINGS_ROLE": "worker"})
    assert report["role"] == "all" and any("RECORDINGS_ROLE" in p for p in report["problems"])


def test_the_cli_exits_0_when_all_is_well(tmp_path, monkeypatch, capsys):
    for key, value in server(tmp_path).items():
        monkeypatch.setenv(key, value)
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX", "RECORDINGS_ROLE"):
        monkeypatch.delenv(name, raising=False)
    assert main(["doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["problems"] == []


def test_the_cli_takes_the_role(tmp_path, monkeypatch, capsys):
    for key, value in web_only(server(tmp_path)).items():
        monkeypatch.setenv(key, value)
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX", "RECORDINGS_ROLE",
                 *SECRET_ENV):
        monkeypatch.delenv(name, raising=False)
    assert main(["doctor", "--role", "web", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["role"] == "web"
    assert main(["doctor", "--role", "backup", "--json"]) == 78  # no restic password here
    assert "RESTIC_PASSWORD_FILE" in " ".join(json.loads(capsys.readouterr().out)["problems"])
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
Expected: errors at collection: `ImportError: cannot import name 'doctor' from 'recordings'` in
`test_doctor.py`, and `ModuleNotFoundError: No module named 'recordings.doctor'` in
`test_config.py`.

- [ ] **Step 3: Write `doctor`**

`packages/core/src/recordings/doctor.py`:
```python
"""`recordings doctor` (spec §5, §6.7, §15.1): what is configured, as presence only.

It never prints a secret's value, nor a repository URL, which can carry a password: only whether
each is set and which transport it is. It reports the secrets first, so a broken config.toml still
shows them (stage-1 carry-over). It probes hard links on the archive's disk, because write-once
publishing needs them, and it reports whether this machine's state.db matches the archive and
writer ID.

Each Compose service runs it in its own role (`--role`, else RECORDINGS_ROLE, else `all`):
- `web` serves the Library and runs the import. It must hold no backup, mirror or alert secret,
  and doctor checks that none is there.
- `backup` runs the backups, the mirror and the alerts. Its archive mount is read-only, so the
  hard-link probe is skipped there, and ssh needs this user's passwd entry.
- `all`, the default outside Docker, checks everything.
Every role warns when retention is off here, and reports where the consent list is, as
information: the import reads it and fails closed by itself.
"""

from __future__ import annotations

import contextlib
import os
import pwd
import shutil
import sqlite3
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from recordings import alerts, backup, mirror
from recordings.config import SECRETS, Config, ConfigError, load_config, secret, secret_path
from recordings.index import Index
from recordings.sentinel import SentinelError, read_sentinel
from recordings.ssh import nas_free_bytes
from recordings.state import State

TOOLS = ("restic", "rsync", "ssh", "ffprobe", "ffmpeg")
ROLES = ("web", "backup", "all")
# The backup service's secrets: docker/compose.yml gives them to it alone. The web service faces
# the browser and never backs up, mirrors or alerts, so it must hold none of them.
JOB_SECRETS = ("restic_password", "rest_password", "nas_ssh_key", "nas_known_hosts",
               "ntfy_topic", "ntfy_token", "deadman_url")
SECRETS_DIR = Path("/run/secrets")  # where Docker mounts a service's secrets, by name
OPEN_TO_OTHERS = 0o077  # ssh refuses a key with any of these bits set; doctor warns for any secret


def _secrets(environ: Mapping[str, str], problems: list[str], warnings: list[str]) -> dict:
    out = {}
    for name, spec in SECRETS.items():
        try:
            found = secret_path(name, environ) if spec.file_only else secret(name, environ)
            present = found is not None
        except ConfigError as exc:
            (warnings if spec.optional else problems).append(str(exc))
            present = False
        file_var = f"{spec.env}_FILE"
        if present and environ.get(file_var):
            with contextlib.suppress(OSError):
                mode = Path(environ[file_var]).stat().st_mode & 0o777
                if mode & OPEN_TO_OTHERS:
                    warnings.append(f"{file_var} names a file that group or others can open "
                                    f"(mode {mode:o}): chmod 600 it")
        out[name] = {"env": file_var if spec.file_only else spec.env, "set": present,
                     "needed_from_stage": spec.stage, "purpose": spec.purpose}
    return out


def _no_job_secrets(environ: Mapping[str, str], problems: list[str]) -> None:
    held = [name for name in JOB_SECRETS
            if environ.get(SECRETS[name].env) or environ.get(f"{SECRETS[name].env}_FILE")
            or (SECRETS_DIR / name).exists()]
    if held:
        problems.append("the web service must hold no backup, mirror or alert secret, but it "
                        "has " + ", ".join(held) + ": docker/compose.yml gives them to `backup` "
                        "only")


def _user(problems: list[str]) -> dict:
    uid = os.getuid()
    try:
        pwd.getpwuid(uid)
    except KeyError:
        problems.append(f"uid {uid} has no passwd entry, and ssh exits without one: rebuild the "
                        "image, whose APP_UID and APP_GID build args come from deploy.env's "
                        "RECORDINGS_UID and RECORDINGS_GID")
        return {"uid": uid, "passwd_entry": False}
    return {"uid": uid, "passwd_entry": True}


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


def read_only_mount(root: Path) -> bool:
    """True when the archive's file system is mounted read-only, as the backup service's is."""
    try:
        return bool(os.statvfs(root).f_flag & os.ST_RDONLY)
    except (OSError, AttributeError):
        return False


def _archive(cfg: Config, role: str, report: dict, problems: list[str]) -> str | None:
    if cfg.archive_path is None:
        problems.append("no archive path: set [archive] path in config.toml, or RECORDINGS_ARCHIVE")
        return None
    info = {"path": str(cfg.archive_path), "exists": cfg.archive_path.is_dir(),
            "sentinel": False, "read_only": None, "hard_links": None}
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
    info["read_only"] = read_only_mount(cfg.archive_path)
    if info["read_only"]:
        if role != "backup":  # the backup service mounts it read-only on purpose
            problems.append("the archive is mounted read-only here, so this machine can't "
                            "write it")
        return identity.uuid
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
    try:
        found_uuid, found_writer = state.meta("archive_uuid"), state.meta("writer_id")
    except sqlite3.DatabaseError:
        problems.append("state.db can't be read: restore it from the backup (runbook: Restoring "
                        "for real), or move it aside and run `recordings init --adopt`")
        return
    info["matches"] = found_uuid == archive_uuid and found_writer == cfg.writer_id
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
            "cacert_exists": bc.cacert.is_file() if bc.cacert is not None else None,
            "restore_scratch_exists": bool(bc.restore_scratch and bc.restore_scratch.is_dir()),
            "nas_free_check": bool(bc.nas.ssh and bc.nas_repo_path),
            "nas_free_bytes": None,
            "restic_installed": shutil.which(bc.restic) is not None}
    report["backup"] = info
    if not info["password_set"]:
        problems.append("RESTIC_PASSWORD_FILE is not set, so backups can't run")
    if info["transport"] == "sftp" and (bc.nas.key is None or bc.nas.known_hosts is None):
        problems.append("an SFTP repository needs RECORDINGS_NAS_SSH_KEY_FILE and "
                        "RECORDINGS_NAS_KNOWN_HOSTS_FILE")
    if (info["transport"] == "rest" and bc.rest_username
            and not report["secrets"]["rest_password"]["set"]):
        problems.append("a rest-server repository with [backup] rest_username needs "
                        "RESTIC_REST_PASSWORD_FILE")
    if info["cacert_exists"] is False:
        problems.append(f"[backup] cacert names {bc.cacert}, which is not a file, so restic "
                        "can't check rest-server's certificate")
    if not info["restic_installed"]:
        problems.append(f"{bc.restic} is not installed")
    if not info["restore_scratch_exists"]:
        warnings.append("[backup] restore_scratch doesn't exist, so the restore test can't run")
    if not info["nas_free_check"]:
        warnings.append("the NAS's free space isn't checked: set [nas] ssh and [backup] "
                        "nas_repo_path")
    else:  # §15.1: ssh df, else sftp df; if neither answers, it is unknown and doctor says so
        info["nas_free_bytes"] = nas_free_bytes(bc.nas, bc.nas_repo_path)
        if info["nas_free_bytes"] is None:
            warnings.append("the NAS's free space is unknown: neither ssh df nor sftp df answered")


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


def _retention(cfg: Config, warnings: list[str]) -> None:
    """Config only, so every role says it: the runbook expects it from web and backup alike."""
    section = cfg.data.get("backup", {})
    if section.get("repository") and section.get("prune") is False:
        warnings.append("retention is off here: it must run on the NAS, or the repository "
                        "grows without limit")


def _consent(cfg: Config, report: dict) -> None:
    """[plaud] consent_list, as information only. In 2a it is mounted just for import runs, and
    the import reads it and fails closed by itself. Its IDs are never read here."""
    path = cfg.data.get("plaud", {}).get("consent_list")
    if not isinstance(path, str) or not path:
        return
    here = Path(path).is_file()
    report["consent_list"] = {
        "path": path, "here": here,
        "note": None if here else "not mounted here; the import reads it and fails closed"}


def _alerts(cfg: Config, environ: Mapping[str, str], report: dict, problems: list[str],
            warnings: list[str]) -> None:
    try:
        ac = alerts.from_config(cfg, environ)
    except ConfigError as exc:  # each is a problem: a bad URL or topic, never its value
        if str(exc) not in problems and str(exc) not in warnings:  # a bad secret file is listed
            problems.append(str(exc))
        return
    report["alerts"] = {"ntfy_server_set": bool(ac.ntfy_server), "topic_set": bool(ac.topic),
                        "deadman_set": bool(ac.deadman_url)}
    if not ac.enabled:
        warnings.append("alerts are off: set [alerts] ntfy_server and RECORDINGS_NTFY_TOPIC_FILE")
    if not ac.deadman_url:
        warnings.append("no dead-man's switch, so a stopped server or backup goes unnoticed: "
                        "set RECORDINGS_DEADMAN_URL_FILE")


def doctor(environ: Mapping[str, str], *, role: str | None = None) -> dict[str, Any]:
    problems: list[str] = []
    warnings: list[str] = []
    if role is None:
        role = environ.get("RECORDINGS_ROLE") or "all"
        if role not in ROLES:
            problems.append(f"RECORDINGS_ROLE must be one of {', '.join(ROLES)}: checking all")
            role = "all"
    report: dict[str, Any] = {
        "role": role, "config": None, "user": None, "archive": None, "state": None,
        "index": None, "backup": None, "mirror": None, "alerts": None, "consent_list": None,
        "tools": {tool: shutil.which(tool) is not None for tool in TOOLS},
        "secrets": _secrets(environ, problems, warnings),
        "problems": problems, "warnings": warnings,
    }
    if role == "web":
        _no_job_secrets(environ, problems)
    else:
        report["user"] = _user(problems)
    try:
        cfg = load_config(environ)
    except ConfigError as exc:
        problems.append(str(exc))
        return report
    report["config"] = str(cfg.path) if cfg.path else None
    archive_uuid = _archive(cfg, role, report, problems)
    _state(cfg, archive_uuid, report, problems, warnings)
    if role != "backup" and cfg.index_path is not None:  # the backup service has no index
        index = Index(cfg.index_path)
        report["index"] = {"path": str(cfg.index_path), "exists": index.exists(),
                           "recordings": index.count()}
    if role != "web":
        _backup(cfg, environ, report, problems, warnings)
        _mirror(cfg, environ, report, problems, warnings)
        _alerts(cfg, environ, report, problems, warnings)
    _retention(cfg, warnings)
    _consent(cfg, report)
    missing = [tool for tool in ("ffprobe", "ffmpeg") if not report["tools"][tool]]
    if role != "backup" and missing:  # web runs the import, which decodes media to measure it
        warnings.append(" and ".join(missing) + " not installed, so the import can't measure "
                        "media that isn't a plain WAV file, and refuses to import it")
    return report
```

In `packages/core/src/recordings/config.py`, **delete** the `doctor` function.

In `packages/core/src/recordings/cli.py`:
1. **Add** `from recordings.doctor import ROLES, doctor` to the imports.
2. **Replace** the `doctor` parser's two lines,
   ```python
       p = sub.add_parser("doctor", help="check config.toml, the archive and secrets (presence only)")
       p.add_argument("--json", action="store_true")
   ```
   with:
   ```python
       p = sub.add_parser("doctor", help="check config.toml, the archive, state and secrets "
                          "(presence only)")
       p.add_argument("--role", choices=ROLES,
                      help="the service it checks: web, backup or all (default: $RECORDINGS_ROLE, "
                      "else all)")
       p.add_argument("--json", action="store_true")
   ```
3. **Replace** `report = config.doctor(os.environ)` with `report = doctor(os.environ, role=args.role)`.

In `scripts/compose_smoke.py`, so the smoke test proves doctor's backup role in the real, hardened
container: that it sees the read-only archive mount as one, and that ssh's uid has its passwd
entry.
1. In the docstring, **replace**
   ```
   - in `backup`, restic initialises a repository, backs up and passes the restore test, and rsync
     mirrors the archive, all for real
   ```
   with:
   ```
   - in `backup`, restic initialises a repository, backs up and passes the restore test, and rsync
     mirrors the archive, all for real
   - `recordings doctor --role backup` finds no problem there: it sees the read-only archive mount,
     and the uid's passwd entry
   ```
2. **Replace**
   `BACKUP_COMMANDS = (("backup", "init"), ("backup", "run"), ("backup", "restore-test"), ("mirror",))`
   with:
   ```python
   BACKUP_COMMANDS = (("backup", "init"), ("backup", "run"), ("backup", "restore-test"), ("mirror",),
                      ("doctor", "--role", "backup"))
   ```

Task 14 already writes the placeholder secrets with mode 600 and an https ping URL, so doctor
finds nothing to warn about in them.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_doctor.py packages/core/tests/test_config.py -v && uv run pytest`
Expected: all pass, and the suite's passed count is not lower than Task 15's.

Then run `make deploy-smoke`.
Expected: it passes, `recordings doctor --role backup --json` among its backup commands, which
exits 0. Its only warnings are about the smoke's own config, which has no `[nas]` or `[alerts]`:
the NAS's free space isn't checked, and alerts are off. If doctor reports "hard links don't
work", it didn't see the read-only mount: check `read_only_mount` against the container's
`/archive`.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/recordings packages/core/tests/test_doctor.py packages/core/tests/test_config.py scripts/compose_smoke.py
git commit -m "feat(core): doctor checks the state, sentinel, writer ID, backups, mirror and alerts

Presence only: no secret value and no repository URL ever reaches its output. It reports the
secrets even when config.toml is broken, and probes hard links on the archive's disk (stage-1
carry-overs). Each service runs it in its own role: web must hold no backup, mirror or alert
secret; backup skips the hard-link probe on its read-only archive and checks that ssh's uid has
a passwd entry. It warns when retention is off here, so it must run on the NAS, and when a secret
file is open to group or others or ffprobe or ffmpeg is missing; it checks [backup] cacert, and
reports the consent list, which only import runs mount in 2a, as information. The compose smoke
test runs doctor --role backup in the hardened container.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: The 2a flow end to end, every setting reaching its reader, CI and the docs

**Checkpoint lens:** operations.

**Files:**
- Create: `packages/core/tests/test_flow_2a.py`, `packages/ui/tests/test_template_settings.py`
- Modify: `packages/core/tests/test_runbook.py` (every `recordings` command it names parses), `.github/workflows/ci.yml` (`validate --deep`), `CLAUDE.md`, `README.md`
- Modify: `docs/runbooks/first-run.md`, only where the forward reviews found drift

**Interfaces:**
- Consumes:
  - every command, through `recordings.cli.main` and `build_parser`
  - the readers: `load_config` (Task 2), `patterns_from_config` (Task 7), `disk_thresholds` and `consent_list_path` (Task 9), `backup.from_config` and `restic_env` (Task 11, with `BackupConfig.cacert`), `mirror.from_config` (Task 12, with `max_delete` and `no_perms`), `alerts.from_config` (Task 13, with `AlertConfig.token` from the optional `ntfy_token` secret), `status_settings` (Task 15), and `secret` and `secret_path`
  - from Task 8: the `ar_archive` fixture's `plaud(..., tags=())`, which fills the catalog's `tags` column, `private(*, name=…)` (the private tier, tagged `therapy`) and `orphan(fid)`
  - from Task 9: the dry run's `by_disposition` (with `consent-excluded` and `new-private`), `deferred_to_2b` and `unprobed`; `[plaud] consent_list`, which the import requires, and its reader `recordings.plaud.consent.consent_list_path(data) -> Path`, whose parser takes `- <id>` lines
  - from Task 12: `MirrorConfig.max_delete` and `.no_perms`
  - from Task 11: CI's pinned restic step, with `RECORDINGS_REQUIRE_RESTIC=1`, and `test_backup.py`'s `restic_or_skip`, copied here (a test module can't import another)
  - from Task 14: the root conftest's `KEEP`, which leaves `RECORDINGS_REQUIRE_RESTIC` and `RECORDINGS_TEST_RESTIC` in place
  - from Task 1: `test_runbook.py`'s `_code_lines()`
- Produces:
  - a test of the 2a flow
  - `test_every_template_setting_reaches_its_reader` and `test_every_secret_reaches_its_reader`
  - a runbook test that parses every `recordings` command it names
  - project docs that describe the write path

- [ ] **Step 1: Write the end-to-end test**

`packages/core/tests/test_flow_2a.py`:
```python
"""Stage 2a end to end, in a temporary folder (spec §20). Through the CLI, in the runbook's order:
init, import a synthetic audio-router archive (a dry run first), validate, back up to a local
restic repository, run the restore test after the import, and doctor."""

import json
import os
import shutil
from pathlib import Path

import pytest

from recordings.archive import Archive
from recordings.cli import main
from recordings.models import is_private
from recordings.state import State

REPO = Path(__file__).resolve().parents[3]
A, B, C, D, O, P = "a" * 32, "b" * 32, "c" * 32, "d" * 32, "9" * 32, "e" * 32
UUID = "11111111-1111-4111-8111-111111111111"
# No report may carry a title (Review Focus 4), whatever its script. A private-tier export keeps its
# export name, which is a title too.
TITLES = ("A PRIVATE TITLE", "Week 4, edited", "会議メモ / Q&A", "A PRIVATE EXPORT")
# Read at import, before any fixture strips RECORDINGS_* from the environment.
RESTIC = os.environ.get("RECORDINGS_TEST_RESTIC")
REQUIRE_RESTIC = os.environ.get("RECORDINGS_REQUIRE_RESTIC") == "1"


def restic_or_skip() -> str:
    for candidate in (RESTIC, str(REPO / ".cache" / "bin" / "restic"), shutil.which("restic")):
        if candidate and Path(candidate).is_file():
            return candidate
    if REQUIRE_RESTIC:  # CI sets it (Task 11): there, a missing restic fails the test
        pytest.fail("RECORDINGS_REQUIRE_RESTIC=1, but restic is not installed: run `make restic`")
    pytest.skip("restic is not installed: `make restic` puts the pinned one in .cache/bin")


def setup(tmp_path, monkeypatch, ar, plaud_env, restic="restic"):
    ar.plaud(A, envelopes=[plaud_env(A), plaud_env(A, name="Week 4, edited")])
    ar.plaud(B, envelopes=[plaud_env(B, name="会議メモ / Q&A")],
             same_audio_as=f"plaud/audio/2026/08/{A}.wav", dup_of=f"plaud/{A}")
    ar.plaud(C, envelopes=[plaud_env(C)], audio=False)
    ar.plaud(D, envelopes=[plaud_env(D)], recorded_at="2026-08-03T11:00:00-07:00")
    ar.recorder()
    ar.pocket(UUID)
    # A main-tier row that audio-router's own catalog tags private (the council's Task 8 blocker).
    ar.plaud(P, envelopes=[plaud_env(P, name="A PRIVATE TITLE")],
             recorded_at="2026-08-04T15:00:00-07:00", tags=("private",))
    ar.private(name="A PRIVATE EXPORT")  # the private tier: matched by SHA-256, tagged therapy
    ar.orphan(O)  # audio, but no snapshot and no time: deferred to 2b
    ar.ledger_only = ["f" * 32]
    ar.write_catalog()
    (tmp_path / "consent.md").write_text(f"- {D}\n", encoding="utf-8")  # never mirror D
    (tmp_path / "pw").write_text("an end-to-end password\n", encoding="utf-8")
    (tmp_path / "deploy.env").write_text("RECORDINGS_PORT=8000\n", encoding="utf-8")
    (tmp_path / "scratch").mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{tmp_path / "archive"}"\nwriter_id = "homelab"\n'
        f'[state]\npath = "{tmp_path / "state"}"\n'
        f'[index]\npath = "{tmp_path / "index" / "index.db"}"\n'
        f'[plaud]\nconsent_list = "{tmp_path / "consent.md"}"\n'
        f'[backup]\nrepository = "{tmp_path / "repo"}"\nrestic = "{restic}"\n'
        f'extra_paths = ["{tmp_path / "deploy.env"}"]\nrestore_scratch = "{tmp_path / "scratch"}"\n'
        f'cache_dir = "{tmp_path / "cache"}"\n', encoding="utf-8")
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    monkeypatch.setenv("RESTIC_PASSWORD_FILE", str(tmp_path / "pw"))
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX", "RECORDINGS_ROLE",
                 "RESTIC_PASSWORD", "RESTIC_REPOSITORY"):
        monkeypatch.delenv(name, raising=False)


def run(capsys, *argv) -> tuple[int, dict]:
    code = main([*argv, "--json"])
    out = capsys.readouterr().out
    assert [t for t in TITLES if t in out] == [], f"`recordings {' '.join(argv)}` printed a title"
    return code, json.loads(out)


def test_init_import_and_validate(tmp_path, monkeypatch, capsys, ar_archive, plaud_env):
    setup(tmp_path, monkeypatch, ar_archive, plaud_env)
    assert run(capsys, "init")[0] == 0
    code, dry = run(capsys, "import-audio-router", str(ar_archive.root), "--dry-run")
    assert code == 0 and dry["unplaced"] == []
    assert sum(dry["by_disposition"].values()) == dry["catalog_rows"] == len(ar_archive.rows)
    assert dry["by_disposition"]["consent-excluded"] == 1 and D not in json.dumps(dry)
    assert dry["by_disposition"]["new-private"] == 2
    assert dry["deferred_to_2b"] == [O]  # its Plaud ID is kept, for 2b's sync
    assert dry["unprobed"] == []  # every fixture is a plain WAV
    code, done = run(capsys, "import-audio-router", str(ar_archive.root))
    assert code == 0 and done["imported"]["created"] == 5 and done["imported"]["failed"] == []
    recordings = list(Archive(tmp_path / "archive").iter_recordings())
    assert len(recordings) == 5 and sum(is_private(r) for r in recordings) == 2
    assert run(capsys, "validate", str(tmp_path / "archive"), "--deep") == (0, {"problems": []})
    code, again = run(capsys, "import-audio-router", str(ar_archive.root))
    assert code == 0 and again["imported"]["created"] == 0
    assert again["imported"]["sources_added"] == 0
    code, reindexed = run(capsys, "reindex")
    assert code == 0 and reindexed["recordings"] == 5 and reindexed["plaud"]["written"] == 0


def test_back_up_and_prove_the_restore(tmp_path, monkeypatch, capsys, ar_archive, plaud_env):
    exe = restic_or_skip()
    setup(tmp_path, monkeypatch, ar_archive, plaud_env, restic=exe)
    assert run(capsys, "init")[0] == 0
    assert run(capsys, "backup", "init")[0] == 0
    assert run(capsys, "backup", "run", "--tag", "pre-import")[1]["ok"] is True
    assert run(capsys, "import-audio-router", str(ar_archive.root))[0] == 0
    assert run(capsys, "backup", "run", "--tag", "post-import")[1]["ok"] is True
    code, restored = run(capsys, "backup", "restore-test")  # after the import, as the runbook does
    assert code == 0 and restored["detail"] == "restored and validated 5 recordings"
    state = State.open(tmp_path / "state")
    assert state.last_run("restore-test", ok=True) is not None
    assert not any((tmp_path / "scratch").iterdir())
    code, report = run(capsys, "doctor", "--role", "all")
    assert code == 0 and report["problems"] == []
```

`packages/ui/tests/test_template_settings.py`:
```python
"""Every setting in config.example.toml reaches the code that reads it (spec §5, §16).

The table gives each template key a distinctive value and the reader that must hand it back. A key
added to the template without a row fails the test, so a setting can't be documented and then
silently ignored. Settings for later stages are listed in LATER, with their stage.
"""

import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

from recordings import alerts, backup, mirror
from recordings.config import SECRETS, load_config, secret, secret_path
from recordings.disk import disk_thresholds
from recordings.plaud.consent import consent_list_path
from recordings.plaud.reconcile import patterns_from_config
from recordings_ui.status import status_settings

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / "config.example.toml"
LATER_SECTIONS = {"spark": "4", "models": "4", "prompts": "4", "watched_folder": "5"}
LATER = {("plaud", "schedule_minutes"): "2b", ("plaud", "auto_import"): "2b",
         ("plaud", "recheck_days"): "2b"}
LATER_SECRETS = {"plaud_token": "2b", "spark_api_key": "4", "claude_oauth_token": "4"}
SECRET_VALUES = {"ntfy_topic": "topic-7", "deadman_url": "https://hc-7.example/ping/7"}


def table(tmp: Path) -> dict:
    """(section, key) -> (the value written, how to read it back, what the reader must return)."""
    return {
        ("archive", "path"): (str(tmp / "archive-7"), lambda r: r.cfg.archive_path,
                              tmp / "archive-7"),
        ("archive", "writer_id"): ("writer-7", lambda r: r.cfg.writer_id, "writer-7"),
        ("archive", "default_timezone"): ("Asia/Kathmandu", lambda r: r.status.timezone_name,
                                          "Asia/Kathmandu"),
        ("server", "base_url"): ("https://rec-7.example:8443", lambda r: r.cfg.base_url,
                                 "https://rec-7.example:8443"),
        ("server", "allowed_hosts"): (["rec-7.example"], lambda r: r.cfg.allowed_hosts,
                                      ("rec-7.example",)),
        ("state", "path"): (str(tmp / "state-7"), lambda r: r.status.state_path, tmp / "state-7"),
        ("index", "path"): (str(tmp / "index-7.db"), lambda r: r.cfg.index_path,
                            tmp / "index-7.db"),
        ("disk", "warn_free_percent"): (37.5, lambda r: r.disk["warn"], 37.5),
        ("disk", "stop_free_percent"): (3.25, lambda r: r.status.stop, 3.25),
        ("nas", "ssh"): ("mirror-7@nas-7", lambda r: r.backup.nas.ssh, "mirror-7@nas-7"),
        ("nas", "port"): (2207, lambda r: r.mirror.nas.port, 2207),
        ("backup", "repository"): ("rest:https://nas-7:8007/rec-7/",
                                   lambda r: r.backup.repository, "rest:https://nas-7:8007/rec-7/"),
        ("backup", "rest_username"): ("rest-user-7", lambda r: r.backup.rest_username,
                                      "rest-user-7"),
        ("backup", "cacert"): (str(tmp / "rest-server-7.crt"), lambda r: r.backup.cacert,
                               tmp / "rest-server-7.crt"),
        ("backup", "restic"): ("/opt/restic-7", lambda r: r.backup.restic, "/opt/restic-7"),
        ("backup", "extra_paths"): ([str(tmp / "deploy-7.env")],
                                    lambda r: tmp / "deploy-7.env" in r.backup.paths, True),
        ("backup", "keep_hourly"): (23, lambda r: r.backup.keep.hourly, 23),
        ("backup", "keep_daily"): (13, lambda r: r.backup.keep.daily, 13),
        ("backup", "keep_weekly"): (7, lambda r: r.backup.keep.weekly, 7),
        ("backup", "keep_monthly"): (11, lambda r: r.backup.keep.monthly, 11),
        ("backup", "prune"): (False, lambda r: r.backup.prune, False),
        ("backup", "nas_repo_path"): ("/rec-7", lambda r: r.backup.nas_repo_path, "/rec-7"),
        ("backup", "min_nas_free_gb"): (17, lambda r: r.backup.min_nas_free_bytes, 17 * 1024 ** 3),
        ("backup", "restore_scratch"): (str(tmp / "restore-7"), lambda r: r.backup.restore_scratch,
                                        tmp / "restore-7"),
        ("backup", "cache_dir"): (str(tmp / "cache-7"), lambda r: r.backup.cache_dir,
                                  tmp / "cache-7"),
        ("mirror", "target"): ("mirror-7@nas-7:/mirror-7/", lambda r: r.mirror.target,
                               "mirror-7@nas-7:/mirror-7/"),
        ("mirror", "rsync"): ("/opt/rsync-7", lambda r: r.mirror.rsync, "/opt/rsync-7"),
        ("mirror", "max_delete"): (7, lambda r: r.mirror.max_delete, 7),
        ("mirror", "no_perms"): (True, lambda r: r.mirror.no_perms, True),
        ("alerts", "ntfy_server"): ("https://ntfy-7.example", lambda r: r.alerts.ntfy_server,
                                    "https://ntfy-7.example"),
        ("plaud", "auto_private_patterns"): (["^\\s*secret-7"], lambda r: r.patterns,
                                             ("^\\s*secret-7",)),
        ("plaud", "consent_list"): (str(tmp / "consent-7.md"), lambda r: r.consent,
                                    tmp / "consent-7.md"),
    }


def write_config(tmp: Path, rows: dict) -> Path:
    """config.toml from the table. JSON's strings, numbers, booleans and lists are valid TOML."""
    (tmp / "rest-server-7.crt").write_text("a test certificate\n", encoding="utf-8")
    sections: dict[str, list[str]] = {}
    for (section, key), (value, _read, _want) in rows.items():
        sections.setdefault(section, []).append(f"{key} = {json.dumps(value)}")
    path = tmp / "config.toml"
    path.write_text("".join(f"[{name}]\n" + "\n".join(lines) + "\n"
                            for name, lines in sections.items()), encoding="utf-8")
    return path


def read_all(config: Path, environ: dict[str, str]) -> SimpleNamespace:
    env = {"RECORDINGS_CONFIG": str(config), **environ}
    cfg = load_config(env)
    return SimpleNamespace(
        env=env, cfg=cfg, status=status_settings(cfg), disk=disk_thresholds(cfg),
        patterns=patterns_from_config(cfg.data), consent=consent_list_path(cfg.data),
        backup=backup.from_config(cfg, env),
        mirror=mirror.from_config(cfg, env), alerts=alerts.from_config(cfg, env))


def test_every_template_setting_reaches_its_reader(tmp_path):
    rows = table(tmp_path)
    template = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    keys = {(section, key) for section, body in template.items()
            if section not in LATER_SECTIONS for key in body}
    missing = sorted(keys - rows.keys() - LATER.keys())
    assert missing == [], "give each a row in table(), with its reader, or list it in LATER"
    r = read_all(write_config(tmp_path, rows), {})
    wrong = {f"[{section}] {key}": read(r) for (section, key), (_value, read, want) in rows.items()
             if read(r) != want}
    assert wrong == {}


def test_every_secret_reaches_its_reader(tmp_path):
    files, values = {}, {}
    for name in SECRETS:
        values[name] = SECRET_VALUES.get(name, f"{name}-7")
        files[name] = tmp_path / f"{name}.secret"
        files[name].write_text(values[name] + "\n", encoding="utf-8")
    env = {f"{spec.env}_FILE": str(files[name]) for name, spec in SECRETS.items()}
    for name, spec in SECRETS.items():  # config's own readers first
        if spec.file_only:
            assert secret_path(name, env) == files[name], name
        else:
            assert secret(name, env) == values[name], name
    r = read_all(write_config(tmp_path, table(tmp_path)), env)
    restic = backup.restic_env(r.backup, r.env)
    reaches = {
        "restic_password": restic.get("RESTIC_PASSWORD_FILE") == str(files["restic_password"]),
        "rest_password": restic.get("RESTIC_REST_PASSWORD") == values["rest_password"],
        "nas_ssh_key": r.backup.nas.key == files["nas_ssh_key"],
        "nas_known_hosts": r.mirror.nas.known_hosts == files["nas_known_hosts"],
        "ntfy_topic": r.alerts.topic == values["ntfy_topic"],
        "ntfy_token": r.alerts.token == values["ntfy_token"],
        "deadman_url": r.alerts.deadman_url == values["deadman_url"],
    }
    assert set(SECRETS) == reaches.keys() | LATER_SECRETS.keys(), (
        "give each secret a reader check here, or list it in LATER_SECRETS with its stage")
    assert [name for name, ok in reaches.items() if not ok] == []
```

In `packages/core/tests/test_runbook.py`, **add** at the end:
```python
def test_every_recordings_command_the_runbook_names_parses():
    # why: a command renamed, or an option dropped, in a fix round must not leave the runbook
    # naming one that doesn't exist. Each `recordings …` command is parsed by the real CLI parser,
    # options and all: `doctor --role`, `init --adopt` and the restore steps included.
    import shlex

    from recordings.cli import build_parser

    commands = []
    for line in "\n".join(_code_lines()).replace("\\\n", " ").splitlines():
        if not re.search(r"\brecordings [a-z]", line):
            continue
        tokens = shlex.split(line, comments=True)
        for i, token in enumerate(tokens):
            # The command itself: first on the line, or after a service name, `run` or `exec`.
            # Not `--tag recordings` or a path.
            if token != "recordings" or (i and tokens[i - 1] not in ("web", "backup", "run",
                                                                       "exec")):
                continue
            command = []
            for t in tokens[i + 1:]:
                if t in ("|", "||", "&&", ";", "&") or t.startswith((">", "2>")):
                    break
                command.append(t)
            if command and re.fullmatch(r"[a-z][a-z-]*", command[0]):
                commands.append(command)
    unparsed = []
    for command in commands:
        try:
            build_parser().parse_args(command)
        except SystemExit:
            unparsed.append("recordings " + shlex.join(command))
    assert unparsed == []
    named = {c[0] for c in commands}
    assert {"init", "doctor", "backup", "mirror", "import-audio-router", "validate", "reindex",
            "alert-test"} <= named, sorted(named)
    assert any(c[0] == "init" and "--adopt" in c for c in commands)
    assert {"web", "backup"} <= {c[c.index("--role") + 1] for c in commands
                                 if c[0] == "doctor" and "--role" in c[:-1]}
    assert any(c[:2] == ["backup", "restore-test"] for c in commands)
```

- [ ] **Step 2: Run them, and see the settings test fail when a row goes missing**

Run: `make restic && uv run pytest packages/core/tests/test_flow_2a.py packages/core/tests/test_runbook.py packages/ui/tests/test_template_settings.py -v`
Expected: all pass. Neither flow test is skipped, because `make restic` installed the pinned
restic. If the runbook test fails, a command or option was renamed in a fix round: amend the
runbook, the one place it is documented. If a settings test fails, a template key or secret has no
reader, or its reader ignores it: fix the reader, or give the key its row.

Then check that the settings test can fail: delete the `("disk", "stop_free_percent")` row from
`table()` and run `uv run pytest packages/ui/tests/test_template_settings.py -v`.
Expected: `test_every_template_setting_reaches_its_reader` FAILS with
`Left contains one more item: ('disk', 'stop_free_percent')`. Put the row back; it passes again.

- [ ] **Step 3: Validate the demo deeply in CI**

In `.github/workflows/ci.yml`'s `python` job, **change**
`uv run --frozen recordings validate demo/archive` to
`uv run --frozen recordings validate demo/archive --deep`.

The pinned restic reached CI in Task 11, with `RECORDINGS_REQUIRE_RESTIC=1`, so
`test_back_up_and_prove_the_restore` runs there and fails rather than skips.

- [ ] **Step 4: Bring the project docs up to date**

In `CLAUDE.md`, **replace** the `## Archive rules (spec §6)` section with:
```markdown
## Archive rules (spec §6)

- **Every write goes through `recordings.writer.Writer`.** Its `mutate` is the only thing that
  changes a `recording.json`: it bumps `rev` and refuses a file edited outside the app until
  `recordings reindex` accepts the edit. `my-notes.md` is only ever appended to, under the
  recording's lock. Media, `source/` and `renditions/` are published write-once. Never write
  archive files any other way, in code or in tests.
- **The sentinel:** writers, backups and the mirror refuse an archive without `archive.json`. Tests
  make archives with `recordings.init.init_archive` (or the `writer` fixture).
- **Privacy:** a recording is private if a tag is `private` or under `private/`, in any
  capitalisation (`recordings.models.is_private_tag`). The private tag lands before any source or
  output does. Don't read a private recording's transcript or notes. No report, alert or log
  carries a title.
- **Fixtures are synthetic:** `packages/core/tests/conftest.py` builds Plaud envelopes and an
  `audio-router` archive by hand. Never copy real data in.
- **The archive's own docs** come from `packages/core/src/recordings/format/`. Edit them there.
- **Settings:** a new key in `config.example.toml` needs its row in
  `packages/ui/tests/test_template_settings.py`, with the reader that uses it.
- **The first real run, and restoring for real,** are in `docs/runbooks/first-run.md`.
```

In `README.md`, **replace** the line `Stage 1 (this release) is the read-only Library over a demo archive.`
with:
```markdown
Stage 1 built the read-only Library over a demo archive. Stage 2a moves the real archive in: a
locked write path, `recordings import-audio-router`, restic backups to an append-only
rest-server on the NAS with a monthly restore test, the NAS mirror, alerts, and Status.
`docs/runbooks/first-run.md` is the first real run.
```

- [ ] **Step 5: Run everything and scan for secrets**

Run: `make test && make e2e && make deploy-smoke && gitleaks dir . --redact --no-banner && gitleaks git . --redact --no-banner`
Expected: every test passes, with a passed count not lower than Task 16's; the compose smoke test
passes; and both gitleaks scans report `no leaks found`.

- [ ] **Step 6: Commit**

```bash
git add packages/core/tests/test_flow_2a.py packages/core/tests/test_runbook.py packages/ui/tests/test_template_settings.py .github/workflows/ci.yml CLAUDE.md README.md docs/runbooks/first-run.md
git commit -m "test: stage 2a end to end; every template setting and secret reaches its reader

The flow runs init, the import (a dry run first), validate, a backup and the restore test after
the import, and checks that no command prints a title. The runbook test parses every recordings
command it names. CI validates the demo with --deep.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Stage 2a is done when

- **The automated checks pass.**
  - `make test`, `make e2e` and `make deploy-smoke` pass, and gitleaks is clean.
  - CI runs the restic tests with `RECORDINGS_REQUIRE_RESTIC=1`, so they fail rather than skip,
    and it runs the compose smoke test.
- **The first real run goes through,** steps 1–4 of `docs/runbooks/first-run.md`. Then, as §20
  requires and the council round of 2026-10-09 added:
  - the real archive is on the homelab server and readable in the Library, whose first load takes
    under 2 s
  - **the restore test passes after the import:** `backup restore-test` runs after the
    `post-import` snapshot and validates the restored copy (`validate --deep`), and only then is
    `import/` deleted
  - **the mirror is proven:** `recordings mirror` reports `ok: true` (step 2.6), and after the
    import Status shows a good mirror run, so the NAS's copy holds the imported archive
  - **`doctor` is clean in both roles:** `doctor --role web` in `web` and `doctor --role backup`
    in `backup` report no problems. Both warn only that retention is off here, which is right:
    it runs on the NAS.
  - **the Healthchecks ping is seen:** Healthchecks.io shows the check up, with `alert-test`'s
    log entry, and a killed backup service raises its alert once the period and grace (1 + 3 h)
    have passed (step 2.7)
- **Not in 2a:**
  - any call to Plaud: the client, the token, the sync, the compare (2b)
  - the review queue for held Plaud removals, and moving `acknowledged_up_to` (3a)
  - the Library reading from `index.db`, and search (3a, unless step 4's first load is slow)
  - writing from the UI (3a)
  - people files and speaker naming (3b)
  - processing (4)

## Carried to stage 2b

- **The token spike and the ID-form spike** (§9.1, §19 step 5; runbook 1c). Both call Plaud, and
  2a makes no Plaud calls, so they run before 2b's deploy.
- **The two orphan Plaud rows** that the import reports as `deferred-to-2b`. They have audio but
  no snapshot and no time, so the import doesn't guess one. Their Plaud IDs are in the import's
  report.
  - 2b's sync matches them by Plaud ID, and they come in like any other Plaud recording.
  - If Plaud no longer has them, 2b imports them from `audio-router`'s copy, timed by the audio
    file's mtime (`time_source` `mtime`). That copy is `audio-router`'s own archive on the Mac:
    the server's `import/` copy is deleted in step 4.
- **`ruff check` in CI,** with a rule set chosen for the repo (a stage-1 carry-over). Its first
  run will also wrap `cli.main`'s `commands` dict, which 2a grows one key per task into one line
  past `line-length = 100`. 2a leaves it alone, because about ten tasks quote that line as OLD
  text.
- **The Library onto `index.db`,** before 2b's deploy, only if step 4's first load took 2 s or
  more (decision 6). Otherwise it stays in 3a.
- **Considered, not built:** `validate` could flag a `source/` file that no `recording.json`
  references. `_publish_raw` now reuses a file with identical bytes, so a killed merge no longer
  leaves one behind.

## As built

When the last task's council is done, the controller adds the "As built" record here, in stage
1's shape:
- a table of where the committed code differs from each task's text, with commits
- the list of what is carried to 2b (extending the list above) and 3a

## Self-review (2026-10-08; updated 2026-10-09 after council round 1)

**Council round 1 (2026-10-09).** Six reviews (spec coverage, data integrity, import fidelity,
operations, security, executability) found five blockers, all fixed:
- the catalog's `tags` and `access` privacy labels were ignored (Task 8)
- the auto-private match missed Plaud's "MM-DD " title prefix (Task 7)
- ssh exits when its uid has no passwd entry (Tasks 14 and 16)
- the darwin_arm64 restic pin was the darwin_amd64 sum (Task 11)
- a CLI dry-run test could never pass (Task 9)

Dan's answers settled four questions:
- **The transport:** rest-server `--append-only`, with retention on the NAS. His Synology volume
  is ext4, which has no Btrfs snapshots to protect an SFTP repository.
- **The alert services:** Healthchecks.io and ntfy.sh.
- **The tags:** every `audio-router` tag comes over, with the privacy labels under `private/`.
- **The two orphan Plaud rows:** deferred to 2b (above).

Task 5 is split into 5a, 5b and 5c. The shared contract (`title_by`, privacy before content, the
ops lock, fixed alert and failure text, restic's flags, doctor's roles, the UID build args, the
boot unit) is applied the same way in every task. The header's time with its offset moved from 3a
into Task 15.

**Verified after the fixes (2026-10-09).** Eight agents applied the fixes in parallel, so two
more reviews checked the result:
- **A coverage review** checked 160 items against the plan text: 149 done, 10 partial, 1 missing,
  none wrong. The partial and missing ones are fixed: the NAS's free space reported as unknown,
  the disk needing room for three copies plus 20% free, "Restoring for real" runnable on a new
  server, `[mirror] no_perms` named in the runbook, and wording.
- **A dry run** executed every task in order in a clean clone, then again after its own fixes.
  - Every OLD anchor, every name shared between tasks, and every stated failure and count held.
  - Final: 614 pytest passed with real restic 0.19.1 and no skips, plus 21 Vitest, 9 e2e and 5
    Pages tests, `make deploy-smoke`, gitleaks and `validate --deep`.
  - Its fixes: Status shows a good backup's NAS free space, ssh gets a 10 s connect timeout, the
    Pages demo's Status reports its disk as unknown, two README rows, and the smoke test's secret
    files at mode 600.

**Spec coverage.** Each §20 2a item, and the sections behind it, maps to a task:

| Spec | Task |
|---|---|
| §6.4 write contract: `mutate`, `flock`, `rev`, content-hash stale check, merge bases, fsync, fixed lock order; `my-notes.md` is append-only, with no `rev` | 2 (fsync), 5a, 5b |
| §6.4 the duplicate merge keeps the second copy's outputs, decisions and notes; `_publish_raw` reuses identical bytes, so a killed merge leaves no duplicate | 5b |
| §6.3 `title_by` is `you` or the source kind; reconcile replaces the title only while it is unset or `plaud` | 3, 7, 9 |
| §7.6 the `speakers` shape and `unknown`; §9.1.1 Plaud segment fields; optional keys, schemas and demo docs regenerated | 3 |
| §6.8 `state.db` (opened `mode=rw`, refused when damaged or newer than the code, numbered migrations); `recordings init --adopt`; derived `index.db`; `recordings reindex`; refusing to empty | 2, 4, 5b, 5c |
| §6.7 the sentinel and `recordings init`; every writer and the mirror refuse without it | 2, 5b, 11, 12 |
| §9.1.1 the normaliser (versioned, the drop list, X-Amz query only, ordering, ID form, per-part hashes with `transaction_polish`, the property test) | 6 |
| §9.1.1 reconcile (pure, idempotent, every snapshot in fetch order, `version` in its identity, removals held, the title, auto-private after the date prefix and over every title and both IDs, the timing check) | 7 |
| §7.4 privacy before content: the private tag lands before any source or output | 5b, 7, 9 |
| §9.3 the import and its mapping table, Plaud ID before content hash, every catalog row accounted for; `audio-router`'s tags and `access`; the private tier by SHA-256; consent-listed IDs skipped; the two orphan rows deferred | 8, 9 |
| §20 Compose: the state volume, long-syntax binds with `create_host_path: false`, log rotation, SHA tags, `hostname`, the writer ID from config; the UID build args; `recordings.service` waits for Tailscale | 2, 14 |
| §5 rename `deploy.example.env`; `test_deploy_template.py` | 14 |
| §15.1 restic over rest-server `--append-only`, retention on the NAS, `[backup] cacert`, the restore test (after the import too), the mirror (`--max-delete`, a same-UUID target), the NAS's free space over sftp `df`, what is backed up | 1, 11, 12 |
| §12.5 the disk guard (marker and free space); Status's backup and disk panels | 9, 15 |
| §12.1 the header's time with its UTC offset (a stage-1 carry-over) | 15 |
| §14 ntfy (fixed text, a checked topic) and the dead-man's switch on Healthchecks (`/start`, `/fail`, `alert-test` on `/log`, grace of at least 3 h) | 13 |
| §15 cross-site writes: the exact origin, `Sec-Fetch-Site`, CORP on media and the API | 10 |
| §5 `doctor` (presence only; `--role web\|backup\|all`; the uid's passwd entry; retention off here; secret-file modes; `cacert`; the consent list as information) | 16 |
| §5, §16 every template setting and every secret reaches its reader | 17 |
| §19 steps 1–4, and "Restoring for real" | 1, 17 |
| §16 lost-update test with two processes; synthetic fixtures; kill points inside real code; the demo guard test | 5a, 5b, 6–9, 10 |

**Placeholder scan.** No "TBD", "TODO" or "similar to Task N". Every code step shows its code.
The runbook's shell variables (`NAS_HOST` and the rest) and placeholders such as
`<server tailnet IP>` are values Dan sets on his own machines, not gaps in the plan.

**Type consistency.** The names that cross tasks were checked against their definitions:
- `Writer`, `Incoming` (with `title_by` and its tags), `Added`, `ReconcileReport` and `ReindexReport`
- `Snapshot`, `Reconciled` and `Held`
- `Plan`, `Item` and `RunReport`, with the `consent-excluded` and `deferred-to-2b` dispositions
- `BackupConfig` (with `cacert`), `MirrorConfig`, `AlertConfig` (with `token`) and `Context`
- `recordings.locks.OPS_LOCK`, held through `Locks(state.locks_dir, timeout=…)` by the import and
  by `run_job(ctx, job, *, tags=(), lock_timeout=600.0)`; a busy lock is "skipped: busy"
- `StatusSettings`, `doctor(environ, *, role=None)` and `ROLES`
- `State._connect`, `State.last_run`, `State.read_only` and `Index.find_by_source`

**Review Focus.** Each line has its test in the owning task:
- 1 in Task 5b
- 2 in Tasks 5b and 9: kill points inside real code, checked against a clean run's file list
- 3 in Tasks 7 and 9
- 4 in Tasks 6–9 and 13, with a title in another script, and an exception that carries a title.
  Task 17's flow also checks that no command's output carries one.
- 5 in Task 11
