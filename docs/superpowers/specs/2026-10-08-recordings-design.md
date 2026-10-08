# recordings: design

- **Status:** draft for review
- **Date:** 2026-10-08 (brainstorming began 2026-10-01)
- **Owner:** Dan
- **License:** MIT (public repo)

## 1. What this is

`recordings` is a self-hosted library for everything Dan records or wants to keep: Plaud
recordings, audio and video files, conference talks found online. It stores each recording
in one central archive. It transcribes and writes notes using local models on the DGX Spark,
and, when asked, Dan's Claude subscription. A web interface, modelled on the Plaud and
HeyPocket web apps, is for browsing, listening, reading and tagging.

It succeeds `audio-router`'s Plaud → archive → Obsidian loop. It runs all the time on a
homelab machine in Docker, so it no longer depends on the Mac being awake.

The repo holds two Python packages:

- **`recordings`**: the core. It is a library plus a command-line tool, with no web code,
  and it does all the work. An agent, a script or a notebook can do anything the UI can by
  calling it.
- **`recordings-ui`**: the web app. A FastAPI server provides the JSON API, uploads and media
  streaming, and a React interface through shinyreact. Every action calls the core. It holds
  no processing logic of its own.

### What success looks like

- Every Plaud recording reaches the archive, except those on Dan's consent list (§9.1), and
  **Sync** shows at a glance what's missing from the Plaud account.
- Every recording is in one place, listed newest first, with playback, a synchronized
  transcript and any number of note sets.
- Tagging is fast: drag and drop, a keyboard tag picker, and a grid of checkboxes. Untagged
  recordings are an obvious inbox.
- Tags decide what happens: which notes get written, which models may see a recording, and
  whether it is private.
- The archive is plain files with documentation, so other tools can read it directly: a
  vault listener, a course-repo skill, Pixeltable notebooks, agents.
- The public repo runs in a demo mode with bundled sample recordings, which makes it a
  reproducible reference for shinyreact bug reports to the Shiny team.

### Out of scope (separate projects, built against this archive)

- **Vault listener:** brings recordings into Obsidian vaults.
- **Course-repo skill:** copies class notes into course repos such as COURSE-101.
- **Suggested tags:** a model proposes tags for you to confirm. Agreed as a later addition.
- **Pixeltable inside the app.** Pixeltable is for Dan's own exploration of the archive and
  may move inside much later.
- **More than one user, and logins.** Logins arrive with the reverse proxy (see §15).

## 2. Context and prior art

| Thing | What it gives this project |
|---|---|
| `../audio-router` (public on GitHub, **no LICENSE**) | Plaud HTTP client, transcript parsing, YouTube source, the `audio-router/rendition@1` output format, and its hardened habits: archive first, dry run by default, guard tests that must fail without their fix. Its archive is documented by `docs/ARCHIVE.md`. |
| `audio-router-pixeltable/examples/shiny/app_recordings.py` | A read-only Shiny prototype of the Plaud-style layout, with click-a-timestamp-to-seek. It handles both transcript shapes (vendor JSON segments and merged text). |
| `../transcript_prompts/` (a folder, not a git repo) | 23 recording types, each a file with YAML frontmatter (detection signals) and a `## Prompt` body containing `{{TRANSCRIPT}}`, plus `classify/detect-meeting-type.md`. Moves into this repo's `prompts/` as the note-type library (§7.3). |
| `class-notes` skill (`class_notes.py`) | The `run_claude` / `run_local` backend pair. `run_claude` is the Claude call to reuse (§8.4). `run_local`'s docstring is the specification for the Spark backend. |
| `../local-ai` (DGX Spark) | llama-swap (keys required) serving `gemma-4-26b-a4b` (resident), `qwen3-embedding-0.6b` (resident), `whisper-large-v3-turbo` (resident, whisper.cpp at `/v1/audio/transcriptions`) and `qwen3.6-35b-a3b` (on demand). Everything binds to 127.0.0.1. Phase 3 plans a pyannote diarization wrapper and keys for "the audio pipeline". |
| Plaud and HeyPocket web apps | The interaction reference: list, player, transcript, and several note templates per recording. |

### Decisions that replace earlier notes

- `local-ai`'s Phase 3 note (2026-10-05) says the audio host "runs on the
  Mac" and is "built on Pixeltable". **Both are superseded.** It runs on a homelab machine
  in Docker and is a regular app (§3). That note needs a small fix in the `local-ai` repo.
- Early in brainstorming the app was going to write to Obsidian vaults and push to course
  repos. **It now writes only to its own archive.** Other tools read from it (§10).
- `audio-router`'s held status and protected-word import check are **replaced by private
  tags** (§7.4). Its consent list ("never mirror") and its grace period for placeholder
  titles carry over unchanged (§9.1; Dan, 2026-10-08).

## 3. Architecture

```
            ┌───────────────────────── homelab machine (Docker Compose) ─────────────────────────┐
 browser ──▶│ web: recordings-ui (FastAPI)                     worker: `recordings worker`       │
 (Tailscale)│   JSON API · uploads · media streaming            jobs: ingest · transcribe ·      │
            │   shinyreact UI (React client)                    speakers · pick · notes          │
            │            │  calls                                     │ calls                    │
            │            └──────────▶  recordings (core library)  ◀───┘                          │
            │                              │                │                                    │
            │                    state.db + index.db    archive (local disk) ◀── source of truth │
            │                    (local disk)           recordings/ catalog/ tags.yaml …         │
            │                                                 │ hourly restic (one-way, versioned)│
            └──────────────────────────────┬──────────────────┼───────────────────────────────────┘
                                           │                  ▼
                                           │        NAS (Synology): backups + read-only mirror
                                           │ HTTPS over Tailscale              `claude -p` (CLI in image)
                                           ▼                                          ▼
                                DGX Spark: whisper · llama-swap · pyannote      Claude (Max plan)
```

- **One image, two containers.** `web` runs `recordings-ui` and `worker` runs
  `recordings worker`. Long jobs never slow down the UI.
- **The archive is the source of truth.** It lives on the **homelab server's own disk** (decided
  2026-10-08), at `/srv/recordings/archive`, and is written first.
  - **Why not the NAS:** writing over an NFS or SMB mount works for one writer, but a
    dropped connection can leave stray temp files, file-change notifications don't cross
    the share, user IDs must match, and the app stops whenever the NAS does.
  - **The homelab server's disk** has room for thousands of hours of audio.
  - **The NAS gets one-way, versioned backups** (§15.1), never live writes.
- **Two SQLite files** sit on the same local disk (§6.8; revised 2026-10-08):
  - **`state.db`** holds operational state that can't be derived, such as the job queue
    and check state, and is backed up.
  - **`index.db`** is fully derived. It can be dropped, and `recordings reindex` rebuilds
    it.
- **Model backends sit behind one interface:** Spark (HTTP, OpenAI-style), Claude (the
  `claude` CLI) and Canned (demo and tests). §8.4 has the details.
- **The homelab server does all the work and is the only writer** (Dan, 2026-10-08).
  - **On the homelab server:** the app, the worker, Plaud sync, ffmpeg, and every call to the Spark
    and Claude. The Claude token exists only there.
  - **Why one writer:** if two machines changed the same `recording.json` at once, through
    a share or a two-way sync like Synology Drive, a change could be lost or a conflict copy
    made. `audio-router`'s host claim existed for the same reason. The core refuses to
    write on any machine other than the one named in config. The writer's identity comes
    from config or `state/`, never from the host name, because container host names don't
    match the server's.
  - **The Mac is a client.** Dan uses the UI in a browser over Tailscale. Anything the Mac
    or an agent on it wants done goes to the app, through the JSON API or
    `recordings --remote <url> …`, and **runs on the homelab server**.
  - **Reading from the Mac** uses the read-only mirror on the NAS (§15.1), for example
    from a Pixeltable notebook. Nothing but the homelab server ever writes. Reads never
    need `state/`, so the mirror works without it.
- **Dependencies on `local-ai` Phase 3:**
  - The Spark is reachable from the homelab machine over Tailscale, with this app's own
    key.
  - The two calls below, `diarize` and `embed`, exist.

  Until both are ready, development uses the Canned backend. Switching Whisper and notes to
  the Spark later means changing a base URL. Diarization needs the new calls.
- **The written request to `local-ai` Phase 3** (added 2026-10-08):
  - **`diarize`:**
    - **Input:** FLAC, or the original file when FLAC would exceed the upload limit (about
      2.5 h). The Spark decodes it with ffmpeg, because aarch64 has no torchcodec wheel.
    - **Parameters:** `return_embeddings`, and the minimum and maximum number of speakers.
    - **Returns:**
      - the overlapped turns and the exclusive turns
      - a centroid for each label
      - raw float32 embeddings for each turn, from the pipeline's own embedding model: for
        exclusive turns minus overlap, of 1.5 s or more, with long turns windowed at 10 s
        or less
      - the pipeline and embedding model repos, at their commits
      - the pyannote.audio and torch versions, the dimension and the sample rate
      - the decoded PCM's hash and duration
  - **`embed`:** audio plus a list of spans returns embeddings from the same model. It is
    needed for seeding from Plaud, Dan's own spans, the gold clips, lifting `voice: off`,
    and model upgrades without diarizing again.

## 4. Repo layout and toolchain

```
recordings/                      (repo root, uv workspace, MIT)
  pyproject.toml                 workspace definition; uv.lock committed
  .python-version                3.14
  packages/
    core/                        → distribution `recordings`, CLI `recordings`
      src/recordings/
        archive/  tags/  jobs/  backends/  ingest.py
        sources/                 one module per input type (§9.0)
          base.py  plaud.py  audio_router.py      (stage 2)
          upload.py  url.py  watched_folder.py    (stage 5)
    ui/                          → distribution `recordings-ui`
      frontend/                  React + TypeScript (Vite), node_modules/ (git-ignored)
        .nvmrc                   22
        package.json, package-lock.json
  format/                        FORMAT.md, AGENTS.md, folder READMEs, JSON Schemas (§6.7)
  prompts/                       note-type library + Dan's course prompts (§7.3)
  demo/archive/                  the demo archive, in the real format (§17)
  _brand.yml                     palette and fonts (§12.6)
  docker/                        Dockerfile (multi-stage), compose.yml, compose.demo.yml
  docs/superpowers/specs/        this spec and the brainstorming mockups
  CLAUDE.md                      includes the shinyreact docs rule (§13)
```

**Python, through uv only:**
- The virtual environment is `.venv`, created by `uv sync`. Everything runs through `uv run`.
- Dev tools (pytest, Playwright) go in `[dependency-groups]`, so a plain `uv sync` can't
  remove them.
- The image installs with `uv sync --frozen --no-dev`.
- yt-dlp is a Python dependency, not a system install.
- Playwright's browser installs with `uv run playwright install chromium`.

**JavaScript, through npm:**
- Node 22 is pinned in `.nvmrc`. That matches shinyreact's own CI, which reads
  `pkg-js/.nvmrc` = `22`. When shinyreact moves, we move.
- Dependencies are local to the frontend folder, and nothing is installed globally.
- `package-lock.json` is committed, and CI and Docker install with `npm ci`.
- `package.json` declares the required Node version, so a build under the wrong version
  fails immediately. Which mechanism to use (an `engines` check or `devEngines`) is decided
  during planning, after checking the npm docs.

**Docker:**
- A multi-stage build: Node 22 builds the frontend, then a Python 3.14 slim runtime runs it
  with ffmpeg and the `claude` CLI.
- Images are built for amd64, for the homelab server (Intel), and for arm64, so they run on the Mac
  for local testing and demo mode.
- It runs as a non-root user.

**Local machine (checked 2026-10-08):**
- Already there: uv 0.12.23, nvm 0.40.8 with Node 22.23.3 (installed today), Docker Desktop
  29.8.2 with Compose 5.5.1, ffmpeg 9.0.2 and gitleaks 8.30.1.
- Nothing needs installing system-wide.

## 5. Configuration and secrets

- **There are three places, and only templates are committed** (Dan, 2026-10-08):

  | What | File | Committed |
  |---|---|---|
  | App settings | `config.toml` at the repo root, or `RECORDINGS_CONFIG` | git-ignored. The template is `config.example.toml`. |
  | Docker host settings: host archive path, config path, bind IP and port, UID/GID | `docker/deploy.env`, passed with `--env-file` | git-ignored. The template is `docker/deploy.example.conf`. |
  | Secrets | Environment `NAME`, or `NAME_FILE` (Docker secrets under `/run/secrets`) | never |

  The templates avoid `.env` names so Claude can read them, because Dan's hooks block
  reading `.env` files. The template's current name, `docker/deploy.example.env`, breaks
  that rule; stage 2a renames it (§20). An environment variable beats `config.toml`. Demo
  mode reads none of the three.
- **Machine config** (`config.toml`) is mounted read-only into both containers at
  `/config/config.toml`. It holds:
  - the archive path
  - `[state] path`, the state folder (`/srv/recordings/state`; §6.8)
  - the writer host (the homelab server; §3)
  - the default time zone, for sources that give none (§6.2)
  - the Spark base URL
  - named model entries (such as `spark:default` and `claude:opus`), each with its own
    `local` flag (§8.4)
  - `[privacy] local_hosts`, the only hosts a `local = true` entry may point at (§8.4)
  - extra prompt folders beyond `prompts/`
  - auto-private title patterns
  - the Plaud schedule and `full_sweep_days` (§9.1.1)
  - `[plaud] consent_list`, the path to Dan's consent list (§9.1)
  - `[speakers]`: `auto_threshold` and `suggest_threshold` (§7.6)
  - `[calendar] terms`, the term presets in the date control (§12.6a)
  - the ntfy settings: the server and the dead-man's switch URL (§14)
  - the watched folder path
  - the app base URL (used for links back into the app)
- **Secrets are passed by reference only**, as environment variables or Docker secrets.
  In Docker they are secret files, so they never show in `docker inspect`:
  - **the Plaud token store,** `state/plaud/tokens.json` (stage 2b). It isn't a static
    secret: the worker refreshes it, so it is a writable file (mode 0600), not a Docker
    secret, and it is never backed up (§9.1).
  - `RECORDINGS_SPARK_API_KEY`, this app's llama-swap key (stage 4)
  - `CLAUDE_CODE_OAUTH_TOKEN`, from `claude setup-token`, lasting a year (stage 4)
  - `RESTIC_PASSWORD`, the backup repository key (stage 2a). Keep a copy in your password
    manager, because without it the backups can't be read.
  - the NAS SSH key and its `known_hosts`, for restic and the mirror (stage 2a; §15.1)
  - the ntfy topic (stage 2a; §14)
  - the local-scope API token for the full view, held only on local machines (stage 3a;
    §10)
- **Docker host settings** (`docker/deploy.env`):
  - `RECORDINGS_ARCHIVE_HOST` (`/srv/recordings/archive` on the homelab server)
  - `RECORDINGS_CONFIG_HOST`
  - `RECORDINGS_BIND` (the homelab server's Tailscale IP)
  - `RECORDINGS_PORT`
  - `RECORDINGS_UID` / `RECORDINGS_GID` (the owner of `/srv/recordings`)

  Stage 2a adds the state folder, the index volume, the secrets folder and the backup
  target. Stage 5 adds the watched folder path.

  No secret appears in the image, the repo, the archive, logs or `--json` output. CI runs
  gitleaks.
- **Validation:** `recordings doctor` checks the config and reports which settings are
  missing. It checks secrets for presence only, never their values. Every config setting
  has a test proving it reaches the code that uses it (an `audio-router` lesson).

## 6. The archive

### 6.1 Layout

```
<archive>/
  archive.json                 the sentinel, holding the archive's UUID (§6.7)
  README.md                    full guide: layout, naming, how to find things, what is editable
  AGENTS.md                    rules for agents (§6.7)
  FORMAT.md                    the format, versioned
  tags.yaml                    tag tree + rules (§7)
  people.yaml                  people, aliases and tombstones (§7.6)
  people.private.yaml          people seen only in private recordings (§7.6)
  schemas/                     recording.schema.json, tags.schema.json, people.schema.json
  catalog/                     rebuilt after changes, in the external view (§6.6)
    README.md
    recordings.csv  recording_tags.csv  notes.csv
    people.csv  recording_speakers.csv
    changes.jsonl              append-only change log (§10); survives rebuilds
    private/                   people_private.csv, private speaker rows; local readers only
  recordings/
    README.md
    2026/10/20261006T140003-0700_3fa91c2e/
      recording.json
      20261006T140003-0700_3fa91c2e.mp3          original media, never modified
      20261006T140003-0700_3fa91c2e.audio.m4a    extracted audio (video only)
      my-notes.md                                 Dan's own notes (optional)
      source/plaud-20261008T143000Z.json          raw source payload, exactly as received
      renditions/                                 outputs, write-once (§6.5)
  trash/                       deleted recording folders, moved here whole
```

### 6.2 Recording ID and naming

- **Format:** `YYYYMMDDTHHMMSS±HHMM_<8 hex>`. It is the recording's start time in **ISO 8601
  basic format**, in the local time zone with its offset, followed by the first 8 hex
  characters of the media file's SHA-256.
  - **Why basic format:** there are no colons, which network shares and Windows reject and
    Finder shows as `/`. Mixing extended and basic forms would not be valid ISO 8601.
    Python 3.14's `datetime.fromisoformat()` parses it (verified).
  - **Why the offset:** Dan records in several time zones.
  - **No time zone from the source:** Plaud gives none, so the configured default time zone
    applies (§5).
- **The folder name and the media file name are both the ID**, so a copied file still
  describes itself. Folders sit under `YYYY/MM/` by local date.
- **IDs never change.** If the recorded time is corrected later, `recording.json` changes
  and the folder name does not.
- **Where the time comes from** is recorded as `time_source`, one of: `plaud`, `metadata`
  (from the container), `published` (the URL's publish date), `mtime` or `ingest`.
- **Output file stamps** use ISO 8601 basic UTC, for example `20261008T143512Z`.

### 6.3 `recording.json` (sketch; the schema is authoritative)

```json
{
  "$schema": "../../../../schemas/recording.schema.json",
  "format": "recordings-archive@1",
  "id": "20261006T140003-0700_3fa91c2e",
  "rev": 7,
  "title": "COURSE 101: Week 4",
  "title_by": "plaud",
  "plaud": { "title_seen": "COURSE 101: Week 4", "acknowledged_up_to": "source/plaud-20261008T143000Z.json" },
  "recorded_at": "2026-10-06T14:00:03-07:00",
  "timezone": "America/Vancouver",
  "time_source": "plaud",
  "media": { "file": "20261006T140003-0700_3fa91c2e.mp3", "sha256": "3fa91c2e…", "kind": "audio", "duration_ms": 7083000 },
  "sources": [ { "kind": "plaud", "ref": "of_a2c0…", "added_at": "2026-10-08T14:30:00Z", "raw": "source/plaud-20261008T143000Z.json" } ],
  "tags": [ { "tag": "school/course-101", "by": "you" }, { "tag": "notes/lecture", "by": "auto" } ],
  "excluded_note_types": ["glossary"],
  "speakers": { "source": "renditions/transcript-plaud-…json",
                "labels": { "Speaker 1": { "person": "dan-chen", "by": "you" } } },
  "chosen": { "transcript": "renditions/transcript-whisper.cpp-large-v3-turbo@…-20261008T143512Z.json" }
}
```

**Source references hold IDs exactly as the source returns them.** As of late September
2026, Plaud's list endpoint returns `of_` plus 32 hex characters, where it used to return
the bare hex. This broke `audio-router`'s sync (§19). The core treats source IDs as opaque
strings. Matching against `audio-router`'s archive accepts either form, and the normaliser
compares them in one canonical form (§9.1.1).

**`rev`** is changed only by `mutate` (§6.4). **`title_by`** says who set the title
(`plaud` or `you`), and **`plaud.title_seen`** is the Plaud title last seen or dismissed.
**`plaud.acknowledged_up_to`** is the newest snapshot whose removals Dan has reviewed
(§6.8).

### 6.4 Write rules

- **Write once:**
  - The media and `source/` are never overwritten.
  - Outputs are never overwritten. Running a step again adds a new file.
- **Editable files:** only `recording.json`, `my-notes.md`, `tags.yaml`, `people.yaml` and
  `people.private.yaml` (§7.6).
  - Each save writes a temporary file, fsyncs it, renames it into place, then fsyncs the
    directory.
  - **Every write is an operation, not a whole document.** The core's `mutate(file, op)`
    runs under that file's lock. **Every editable file has a lock and a monotonic `rev`:**
    each `recording.json`, `tags.yaml` and both people files. The locks are `flock` locks,
    released if the process crashes (§6.8). Their files live in `/srv/recordings/state`,
    outside the archive, so backups and the mirror never copy them.
  - **Only `mutate` changes `rev`.** Outside editors leave it alone, and `AGENTS.md` says so.
  - **`mutate` refuses a stale file.** Changes are detected by content hash, not mtime: when
    the hash on disk differs from the hash `mutate` last wrote, someone else has edited the
    file. Recent revisions are kept in `state.db` as merge bases, and the edit is merged
    three ways. Until stage 3, a stale or invalid edit goes to Needs attention instead.
  - **Multi-file operations** (merge, forget) take their locks in a fixed order: the people
    files first, then recordings in sorted-ID order.
  - **Undo** is the inverse operation, applied through `mutate`.
  - This is what lets the web process, the worker, the CLI and Claude's edits write safely
    side by side. A plain changed-since-read check can be passed by two writers at once.
- **One exception to write-once:** forgetting a person (§7.6) rewrites the files that hold
  their name, Plaud snapshots and outputs included. Each rewrite is logged with its old and
  new hashes.
- **Recordings appear complete or not at all.** A new recording is assembled in a temporary
  folder and moved in only when complete.
- **Duplicates merge.** If identical bytes arrive again (same SHA-256), the new source
  reference is added to the existing recording, through `mutate`. The merge also keeps the
  second copy's outputs, decisions and notes. Stage 1's `_merge_source` moves onto `mutate`
  in stage 2a.
- **Delete** moves the folder to `trash/`. The core never deletes permanently.

### 6.5 Outputs (renditions)

- **The contract extends `audio-router/rendition@1`.** Each output is a JSON file:
  `{schema, kind, engine, version, inputs, meta, payload}`.
  - **Name pattern:** `<kind>-<engine>-<version>-<UTC stamp>.json`. For notes, the file
    name's `<kind>` part is `notes-<note type>`; the `kind` field itself stays `notes`.
  - **`version` changes whenever the output would change:** a model revision, a
    quantization or a prompt hash. Otherwise a re-run would silently report the old answer
    as new.
- **Kinds** (the `kind` field):
  - `transcript`: segments with start and end times, speaker and text
  - `speakers`: diarization: who spoke when, as segments only. Voice fingerprints never
    enter the archive (§7.6).
  - `speakers-compare`: Plaud's named speakers against the home-built result (§7.6). Its
    `inputs` pin both outputs and a hash of the assignments, so it goes stale when any of
    them changes.
  - `pick`: the note-type auto-pick result
  - `notes`: Markdown, with prompt ID and hash, model, and the model that actually
    answered. The note type is a separate field, `note_type`.
- **Outputs are found by their `kind` field,** not by file name. A `speakers-*` glob also
  matches `speakers-compare`.
- **Plaud's own transcript and notes** are written as outputs with engine `plaud`, rebuilt
  from `source/` by reconcile (§9.1.1):
  - Each Plaud note tab becomes a `notes` output with `note_type` `plaud-<tab>`, for
    example `plaud-summary`.
  - Each records the snapshot it came from (`inputs.source`) and the hash of its part
    (`inputs.part_sha256`).
  - Its `version` is `plaud@<normaliser version>`, so the output names the normaliser that
    produced it.
- **Which transcript is shown:** `chosen.transcript` if set, otherwise the newest
  successful one.
- **Stale is calculated, never stored:** a notes output is stale when its prompt hash or
  model version differs from the current one.

### 6.6 Catalog

The catalog is a set of flat tables, rebuilt after every change. Pandas, DuckDB and
Pixeltable can all load them directly. **Its top level is written in the external view**
(§10, revised 2026-10-08), so external agents may read it. `catalog/private/` is for local
readers only.

- **`recordings.csv`:** id, recorded_at, duration, source kinds, title, has_transcript,
  note counts, stale counts, `private` (calculated, §7.4) and `untagged`. Every recording
  is listed, but private and untagged recordings have blank titles. Readers keep the
  content of any row that is private or untagged away from any model or agent that isn't
  local (§7.4).
- **`recording_tags.csv`:** one row per recording and tag, with who applied it.
- **`notes.csv`:** one row per notes output: recording, note type, model, path, stale.
- **`people.csv`** and **`recording_speakers.csv`** (§7.6) hold the resolved speakers:
  - your decisions, Plaud's names and voice matches, each with `by`, `from` and `score`
  - a `private` column
  - `people.csv` lists only the people in `people.yaml`
  - `recording_speakers.csv` leaves out private and untagged recordings
- **`catalog/private/`** holds `people_private.csv` and the `recording_speakers.csv` rows
  for private and untagged recordings. `AGENTS.md` puts it off limits to external agents.
- **Terms:**
  - **`by`** is who made the call: `you`, `plaud` (a name given in Plaud and linked to a
    person) or `auto` (the app's inference).
  - **`from`** is how a derived name was reached: `plaud-name`, `voice` or `carried-over`.
    It is empty for your own decisions.
- **Redaction:** in the external view, the output of `recordings validate` and the people
  commands is redacted the same way.

Pixeltable and the vault then never re-implement overlap, precedence or merge resolution.

### 6.7 Self-documentation

- **`README.md`** is the full guide for people and agents.
- **`AGENTS.md`** sets the rules for agents:
  - read README first
  - edit only the editable files listed in §6.4: `recording.json`, `my-notes.md`,
    `tags.yaml`, `people.yaml` and `people.private.yaml`
  - never change `rev`; only `mutate` does (§6.4)
  - check edits against the schemas
  - **the allow-list (§7.4):** only agents whose models are local may open a private or
    untagged recording's `renditions/` or `source/`, read its speakers, or read
    `people.private.yaml` or `catalog/private/`. External agents never use the full view's
    token (§10).
  - run `recordings reindex` after a bulk edit
  - prefer the `recordings` CLI with `--json`
- **Short `README.md` files** sit in `recordings/` and `catalog/`.
- **The docs can't drift.** The core writes these files from `format/` in the repo at
  startup, but only into an archive that has its sentinel (below). A test fails if the
  documented layout and what the writer produces disagree. `audio-router` learned this when
  its prose drifted until `schemas.py` replaced it.
- **One version key:** every versioned file carries `format`, such as
  `recordings-archive@1` in `recording.json` and `recordings-people@1` in the people files.
  `$schema` is kept only as the JSON Schema reference.
- **The archive sentinel** (added 2026-10-08). An archive is never set up implicitly.
  Otherwise, writing the docs at startup into an unmounted, empty folder would create a new
  archive, and the mirror's `rsync --delete` would then wipe the NAS copy (§15.1).
  - `recordings init` writes `archive.json`, holding the archive's UUID.
  - Every writer, the sync and the mirror refuse to run when the sentinel is missing or its
    UUID has changed.
  - `recordings reindex` refuses to empty a non-empty index.

### 6.8 Where state lives (added 2026-10-08)

State has three homes:

- **Archive files hold decisions:**
  - your tags, titles and speaker decisions, in `recording.json`
  - dismissals: dismissing a difference sets `plaud_name_seen` or `plaud.title_seen` to the
    dismissed value
  - *Not a person* and groups, in the people files
  - the "acknowledged up to" snapshot marker in `recording.json`, for removals held for
    review (§9.1.1)
- **`state.db` holds operational state that can't be derived.** It lives in `[state] path`
  (`/srv/recordings/state`), mounted read-write in both containers. It holds:
  - check state: last checked, last changed, failures and `next_check_at` (§9.1.1)
  - the queue, held batches (§11), and the before-and-after records for batches and Undo
  - recent revisions, as merge bases (§6.4)
  - the writer ID (§3)
  - the salted hashes that scrub forgotten names from new fetches (§7.6)

  The same folder holds the Plaud token store (`plaud/tokens.json`; §9.1), the voice store
  (`voice.db`; §7.6) and the lock files (§6.4).
  - **Backups:** `state.db` is backed up; `voice.db` and the token are not (§15.1).
  - **After losing `state.db`,** a rebuild sets each recording's "last checked" from its
    newest snapshot.
- **`index.db` is derived** and can be dropped. `recordings reindex` rebuilds it.

Also:
- **Locks** use `flock`, which is released if the process crashes.
- **Reads never need `state/`.** The Mac's mirror has none and can't write.
- **Demo mode and tests** use a temporary state folder. The Pages demo gets a no-op lock;
  whether `flock` works under Emscripten is still to be checked.

## 7. Tags, note types and privacy

### 7.1 Tags

- **Folders:** `/` makes them, for example `school/course-101` or `work/clients`. The folder
  tree is for browsing only. **Rules do not inherit** from parent folders, or across tags
  in any other way: `voice: off` on `school/` does not cover `school/course-101`. Dan sets
  the rules on each course or client tag.
- **Who applied it:** each tag on a recording records `by: you` or `by: auto`.
- **Untagged** means a recording has no tags at all.

### 7.2 Tag rules (`tags.yaml`)

```yaml
school/course-101:
  vocabulary: [Codespaces, Quarto, tidyverse]   # sent to Whisper as its prompt
  notes: [lecture]                              # note types added (from prompts/)
  models: [spark:default, claude:opus]          # model names come from config
```

- **Rules combine** across all of a recording's tags.
- **Model and prompt names refer to config entries**, so `tags.yaml` contains no paths or
  secrets.
- **Privacy isn't a rule in this file.** It comes from the tag's name (§7.4).
- **Speaker rules for sensitive contexts** (§7.6). Like privacy, the strictest rule wins:
  - `voice: off`: these recordings are never fingerprinted or auto-matched, and any voice
    data they have is deleted. They are still diarized, without embeddings (§7.6).
  - `external_names: false`: external models (§7.4) see "Speaker 2" instead of names, as
    far as the app can manage (§7.6).

  Dan sets both on each course and client tag (2026-10-08):

  ```yaml
  school/course-101:
    voice: off
    external_names: false
  ```

### 7.3 Note types

- **Each note type is a prompt file**, in `transcript_prompts` format: frontmatter with
  detection signals, plus a `## Prompt` body containing `{{TRANSCRIPT}}`. A prompt can opt
  out of auto-pick with `auto: false`.
- **The library is `prompts/` in this repo.** It holds the 23 types moved in from
  `../transcript_prompts` (a folder with no git history, and generic, so safe to make
  public), plus Dan's own course prompts. Config may list extra folders on the homelab server.
- **Course prompts that students edit stay in their course repo.** They are used on
  request, not automatically:
  1. From the Mac: `recordings --remote notes <id> --prompt-file <course repo>/prompt-notes.md --model claude:opus`.
  2. The CLI sends the prompt text with the request.
  3. The homelab server runs the model and saves the notes. The output records the prompt's origin
     (the file name and repo) and its hash, so it can be regenerated when students change
     the prompt.

  The course-repo skill (out of scope) wraps this command.
- **Each note type appears as a tag**, `notes/<type>`. A recording can have any number.
  - **One output per pair:** each note type × model pair produces its own notes output.
  - **How note types get added:**
    - **Auto-pick:** after transcription, a small Spark model reads the library's
      frontmatter and adds one or more `notes/*` tags marked `auto`. The model is
      `gemma-4-26b-a4b` today, configurable. If it is not confident, it uses
      `general-fallback`.
    - **By hand:** in the UI or through the CLI.
    - **Tag rules:** a tag's `notes:` list adds types.
  - **Your choice wins.** Unchecking an auto-picked type records it in
    `excluded_note_types`, and re-running auto-pick never adds it back.

### 7.4 Privacy (tag-based)

- **Everything comes in,** with one exception: Plaud recordings on Dan's consent list are
  never downloaded (§9.1). There is no import refusal and no held status.
- **The rule:** a recording is **private** if its `recording.json` has the tag `private`
  or any tag under `private/` (for example `private/journal` or `private/personal`), in any
  capitalisation: `Private` and `PRIVATE/journal` count too. The rule errs towards private.
  One function decides it (`recordings.models.is_private_tag`).
  - **`recording.json` is the single source of truth.** Privacy needs no other file, no
    marker files and no lists to keep up to date (Dan, 2026-10-08).
  - **The UI's Private section is the `private/` folder.**
- **Local and external, decided by an allow-list:**
  - **Local** means every model that receives any content (file text, tool results, prompts,
    audio) runs on Dan's own hardware: the DGX Spark, the homelab server or Dan's Mac
    (Dan, 2026-10-08; the Mac was added then).
  - **Where the agent program runs doesn't matter; where its model runs does.** Claude Code
    is external on every machine, the Mac included, because its model is hosted.
  - **Everything else is external:** Claude, other hosted APIs (OpenAI, Gemini and the
    like), and any third-party agent. That holds whoever builds it and however trusted it
    is.
  - **A backend is local only if config marks it `local = true`** (§8.4). Anything unmarked
    is external, so a newly added backend is blocked until Dan says otherwise.
- **Untagged counts as private for external agents** (Dan, 2026-10-08). Until Dan tags a
  recording, the external view (§10) and `AGENTS.md` treat it as private. Local models are
  unaffected, and the app's own pipeline runs nothing on it until a tag anyway (§7.5). The
  cost: Claude can't read the inbox to help Dan triage it.
- **Private recordings only ever use local backends:**
  - Every external backend is skipped, even if another tag's rules name one, and the Details
    tab says "External models skipped: private".
  - "Generate with Claude", and any other external action, shows a lock and "Private: local
    models only".
- **Privacy is checked again when each job runs**, not only when it is queued. Adding a
  private tag drops any waiting external job.
- **It can't be recalled.** Adding a private tag cannot undo what an external model already
  processed. The Details tab shows which outputs came from external models.
- **Automatic private tags:**
  - Plaud recordings matching the title patterns in config are tagged `private`. The check
    runs after the grace period for placeholder titles, and again whenever Plaud's title
    changes, which may add `private` and never removes it (§9.1, §9.1.1).
  - **Held titles come in, tagged `private`** (Dan, 2026-10-08). The default patterns are
    `audio-router`'s hold patterns: a title starting `private`, or containing `interview:`.
    `audio-router` never fetched the recordings it held, so they arrive through the sync
    (§9.3).
- **Agents follow the same allow-list.** That covers Claude Code sessions, and any other
  agent or model not running on Dan's hardware. `AGENTS.md` states it once: "If a
  recording's `recording.json` has a `private` or `private/…` tag, or no tags at all, an
  agent or model that isn't local never opens its `renditions/` or `source/`, never reads
  its `speakers`, and never reads `people.private.yaml` or `catalog/private/`. Only agents
  driven by models on Dan's own hardware (the DGX Spark, the homelab server or the Mac)
  may; that is what the private tier is for."
  - `AGENTS.md` never lists private recordings, because the metadata file is the only
    source.
  - **The app needs no hook change.** Dan's existing tool hooks are a backstop: his
    PreToolUse hook already blocks reading media content.
  - Otherwise, external agents may read the folders of tagged, non-private recordings, and
    the catalog outside `catalog/private/`.
- **On the Mac, this is policy, not cryptography** (Dan, 2026-10-08). The Mac's read-only
  mirror may hold private recordings, and external agents such as Claude Code share the
  machine. Three things keep them out: `AGENTS.md`, Dan's tool hooks, and the external view
  that the CLI and API give by default (§10). Nothing on the Mac encrypts private
  recordings against them.
- **Paths around the allow-list** (added 2026-10-08). These rules govern this app and its
  archive, not every route to a recording:
  - **Plaud's MCP server and the Plaud and class-notes skills** give external agents every
    recording straight from Plaud. Don't use them for private or course recordings.
  - **Plaud runs its own AI and voice recognition,** so `voice: off` doesn't reach Plaud.
  - **The vault listener** (§1) must keep a contract: private recordings never go to a
    vault that external agents read, it honours `external_names`, and it records the paths
    of the notes it writes so forget can report them (§7.6).

### 7.5 The tag gate

- **Nothing is transcribed or summarized until a recording has a tag.** Before that, a
  recording is stored and playable, and Plaud's own transcript and notes are visible if
  they came with it.
- **The first tag starts the pipeline:**
  1. Check privacy.
  2. Choose the allowed models.
  3. Transcribe.
  4. Label speakers.
  5. Auto-pick note types.
  6. Write the notes.
- **Any tag counts**, including `notes/*` and auto-applied `private`.
- **Removing all tags undoes nothing.** The recording goes back to Untagged.

### 7.6 Speakers and people (added 2026-10-08, revised after the design council)

**The goal:**
- Every recording says who is speaking.
- People you name are remembered, and found again in your other recordings, past ones too.
- Mistakes are cheap to fix and stay fixed.
- The core package owns all of this. The web UI is the main place to name people. The files
  stay small and easy for Claude to change in bulk.

**The principle: files hold decisions; the index derives inferences.**
- **`recording.json` stores what *you* decided:**
  - who a speaker label is
  - a span of time that is someone else
  - a rejection
- **Derived, never written into recording files:**
  - names linked from Plaud
  - voice matches
  - names carried over from an older diarization

  These live in `index.db` (§6.8) and are published in the catalog.

  So a sweep or an alias edit never rewrites hundreds of recordings. Linking a Plaud name is
  one alias edit, and there are no false outside-edit alerts.
- **Rejections are stored.** The index can be rebuilt, and it would otherwise forget them.

**People files** (archive root; editable; replaced atomically like `tags.yaml`):

```yaml
format: recordings-people@1
people:
  dan-chen:
    name: Dan Chen
    aliases: [Daniel, Dan, Daniel Chen]   # names Plaud (or anyone) uses for them
  alex-kim:
    name: Alex Kim
    recognise: false                      # keep no voice data for them
  students:
    name: Students
    kind: group                           # a crowd, never fingerprinted
  daniel-c:
    merged_into: dan-chen                 # a duplicate merged away
```

- **`people.private.yaml`** has the same shape. It holds people who appear only in private
  recordings, or only in recordings where `external_names` is false. A person created from
  such a recording goes there by default. Local tools read it; external agents and models
  never do (§7.4).
- **Where an entry lives** (added 2026-10-08):
  - A non-private recording links only to people in `people.yaml`.
  - The survivor of a merge lives in `people.yaml`, after Dan confirms.
  - `recordings validate` warns about an entry in the wrong file, and never moves anyone
    automatically.
- **Slugs:**
  - **Shape:** they match `^[a-z0-9]+(-[a-z0-9]+)*$`, ASCII-folded. A name in a non-Latin
    script that folds to nothing gets `person-<n>`.
  - **Collisions** take the next free `-N` suffix (`alex-kim-2`), never a tombstoned slug.
  - **Stable:** they never change when the display name does. Renaming a slug is a merge.
  - **Never reused:** a merged person leaves a `merged_into` entry, and a forgotten person
    leaves a tombstone (below).
  - **Unique:** slugs and aliases are unique across both people files, ignoring case.
  - **Unknown slugs:** an unknown slug in a recording holds that file, like a schema error
    (§11). It never auto-creates a person.
- **Merging:**
  - A merged entry holds only `merged_into`. Chains resolve transitively; loops and dangling
    targets are errors.
  - Aliases are combined.
  - `recognise` stays true only if both people had it.
  - A person who ends up in their own `not` list is flagged.
- **Write order:** to create and assign, or to merge, write the people file first, then the
  recordings. An orphan person is harmless; a dangling reference is not. Undo runs in
  reverse.
- **`recognise: false`** means no voice data is kept for this person, and any already held
  is deleted (stage 6).

**Decisions in `recording.json`:**

```json
"speakers": {
  "source": "renditions/transcript-plaud-<stamp>.json",
  "labels": {
    "Speaker 1": { "person": "dan-chen", "by": "you" },
    "Speaker 2": { "person": "sam-lee", "by": "you", "plaud_name_seen": "Alex" },
    "Speaker 3": { "not": ["alex-kim"] }
  },
  "spans": [ { "start": 41.2, "end": 58.0, "person": "alex-kim", "by": "you" },
             { "start": 300.0, "end": 312.5, "person": "unknown", "by": "you" } ]
}
```

- **`source`:** labels belong to one diarization. Plaud's "Speaker 1" is not pyannote's
  `SPEAKER_00`. The core maps labels onto any transcript by time overlap with `source`, once,
  so every view agrees.
- **`spans`:** media time in seconds. A span survives a new transcript, a Plaud re-sync or
  Whisper arriving. Line numbers wouldn't.
  - **The reserved person `unknown`** means "not anyone known": a span set to `unknown`
    overrides every inferred name for that time. It is how "Not Alex" works for one line or
    a range (§12.6a). No real person can have the slug `unknown`.
- **The corrections:**
  - **One person split into two labels:** point both labels at the same person. For two
    *unnamed* labels, the UI creates a placeholder person ("Unknown 1", `recognise: false`)
    to rename later.
  - **Two people in one label:** use a span.
  - **A wrong tag:** delete the assignment. To stop an inferred name coming back, add the
    person to `not`.
- **`plaud_name_seen`:** recorded when you override a Plaud name. The UI shows "Plaud now
  says X" only when Plaud's name changes from the one seen. Dismissing that sets
  `plaud_name_seen` to the new name (§6.8).
- **Precedence**, highest first:
  1. your span
  2. your label
  3. a Plaud-named span
  4. a Plaud-named label
  5. a voice match
  6. a name carried over
  7. a generic label

  A Plaud name projected onto a pyannote label, by time overlap, keeps its rank: it is
  still a Plaud-named label or span, not a voice match.

**Derived assignments** (index and catalog; recomputed whenever their inputs change):
- **From Plaud.** Plaud's transcript keeps, for every segment, `original_speaker` (the
  generic label, stored as the segment's `speaker`), `speaker` (the name Dan gave, stored
  as `speaker_name`) and `embeddingKey` (stored as `embedding_key`, because our schema uses
  snake_case). Most segments in the current archive are named. Plaud can also rename a
  single line:
  - each label gets its majority name
  - minority names become Plaud-named spans
  - a name matching a person's `name` or `aliases`, **ignoring case and extra spaces**,
    links to them, and the person's properly cased `name` is what's shown. So "alex kim"
    links to Alex Kim (Dan, 2026-10-08)
  - unknown or ambiguous names go on the People page's *To link* list
  - **Plaud-named speech is prime voice data** (Dan, 2026-10-08). Labels named in Plaud
    count as confirmed when profiles are seeded (stage 6, below), within the timing check's
    limits
  - an `embedding_key` shared by different names is shown as evidence for a merge
- **By voice and carried over:** stage 6, below.
- **Catalog:**
  - `people.csv` (id, name, aliases, recognise, kind, merged_into)
  - `recording_speakers.csv` (recording_id, person_id resolved, label, source, talk_ms, by,
    from, score, private)
  - `catalog/private/` for private and untagged recordings and private people (§6.6)
- **Change events:** `changes.jsonl` gets "speakers changed" and "people changed" events.
  Events and logs carry recording IDs and counts, never person slugs, because slugs are
  names.

**Voice work is local only, for every recording** (Dan, 2026-10-08). Diarization, voice
fingerprints, profiles and matching run only on backends marked `local = true` (§8.4): today,
pyannote on the Spark. That holds for non-private recordings too.
- **External backends only ever receive text,** such as a transcript sent for notes, and only
  for non-private recordings. Claude can't diarize audio anyway.
- **An external audio service** (an OpenAI Whisper API key, if Dan ever adds one) would be
  limited to transcribing non-private recordings. Its speaker labels would be ignored, and
  its audio would never be fingerprinted from it.
- **Plaud is a source, not a backend.** Its own diarization comes in with the recording and
  is kept. The local-only rule governs what this app *sends*.

**Voice (stage 6, pyannote on the Spark):**
- **The calls** are `diarize` and `embed`, as requested of `local-ai` Phase 3 (§3). Uploads
  go as FLAC: a two-hour lecture as 16 kHz PCM is over the upload limit.
- **`voice: off` recordings are still diarized,** with `return_embeddings=false`, so
  who-spoke-when, Plaud names and naming by hand all work. Sweeps skip them. Lifting the
  rule runs `embed` on the existing turns.
- **`speakers` outputs** (write-once, in the archive) hold who spoke when, and nothing else.
- **Who is fingerprinted** (Dan, 2026-10-08): every voice heard for at least about 1.5 s in
  a recording that allows voice is fingerprinted, named or not. Speakers nobody has named
  keep their per-turn fingerprints, because retroactive matching needs them. The forget,
  `recognise: false` and `voice: off` rules still apply.
- **Voice fingerprints never enter the archive.**
  - **Where:** a derived store at `/srv/recordings/state/voice.db`, keyed by (diarization
    output, embedding model) and stored as float16.
  - **Not copied:** it is excluded from backups and from the read-only mirror. It can be
    rebuilt from the audio by a Spark job.
  - **Deleted** for a person with `recognise: false`, for a forgotten person, and for any
    recording whose tags say `voice: off` (§7.2).
- **Re-checked when each job runs** (added 2026-10-08). As with privacy, `voice` and
  `external_names` are checked again when a job runs, not only when it is queued.
  - A later `private` or `voice: off` tag removes that recording's exemplars and rebuilds
    the profiles it fed.
  - Embedding Plaud-named segments follows the tag gate (§7.5): nothing untagged is
    fingerprinted.
- **Voice profiles** are built from confirmed time only: yours, plus Plaud-named segments.
  - **Seeding from Plaud** is gated by the timing check (§9.1.1). From each Plaud-named
    segment, only the part held by a single pyannote label covering at least 80% of it is
    used, minus overlap, in windows of 10 s or less, embedded with `embed`. Outlier
    windows are dropped. If few segments in a recording have such a dominant label, the
    recording is treated as a timing failure. Dan's Plaud labelling therefore seeds the
    profiles.
  - **Exemplars:** one per person per recording, each from at least 10 s of speech. A
    label's embedding is the duration-weighted mean of its turns, with each turn capped at
    30 s. A person keeps at most 32 exemplars, chosen by clustering.
  - **Sources:** profiles learn only from non-private recordings that allow voice.
  - **Heard once:** a person heard in only one recording is suggest-only.
  - **Outliers:** an exemplar that looks unlike the rest of that person's is shown to Dan as
    a probable wrong tag.
  - **Versions:** profiles and thresholds are kept per embedding-model version, and matching
    happens only within one version. A model upgrade means re-embedding and recalibrating.
- **Matching:**
  - **The score:** the mean of the top-k exemplar similarities, with k = min(k, n) for a
    person with n exemplars, AS-normed against a cohort. The enrolment side of AS-norm uses
    the same top-k. The cohort is one embedding per unnamed label, leaving out the
    recording being tested.
  - **Requirements:** a margin between the best and second-best match, at least about 8 s
    of net speech, and never the same person on two labels that speak at once.
  - **What's stored:** each score, with its versions: the embedding model's revision, the
    pyannote.audio and torch versions, and the cohort and calibration versions.
- **One global rescore** (added 2026-10-08), keyed by (profile-set hash, cohort version,
  calibration version).
  - It is materialised in `index.db` as `speaker_assignment` (rank, by, from, score,
    generation) and `person_talk` (confirmed and inferred milliseconds), written in one
    transaction.
  - The Library, Insights and the catalog read these tables. An open recording pins its
    generation.
- **Thresholds** (`[speakers] auto_threshold`, `suggest_threshold`) are calibrated on the
  actual decision (the top match, the margin and the minimum speech), as an open-set
  problem: false assignments must be at most 1% of auto tags.
  - **Trials:** Plaud-named time, plus your confirmations and rejections. Impostors are
    taken leave-one-recording-out.
  - **Confidence:** bootstrap intervals, resampled by recording.
  - **Selection bias:** *Suggestions* also shows about 10% random lower-scored candidates.
  - **Recalibration** runs again as profiles grow.
  - **`auto_threshold` stays off** until calibration is good enough: about 50 or more
    target and 300 or more impostor label trials, over 10 or more recordings, with the upper
    bound at 2% or less. Until then every match goes to *Suggestions*.
  - **No model is trained on voices.** Plaud corrections are calibration and gold data.
- **Retroactive sweeps:**
  - **The trigger:** a profile change, such as a confirmation, a merge or a removed tag.
  - **Debouncing:** one pending sweep per (person, profile revision); a newer one replaces
    it. Each label is decided across all profiles, and the best match wins, so the order
    sweeps run in doesn't matter.
  - **Effects:** sweeps update the index only, so they never write recording files.
  - **In the UI:** High matches are applied as auto tags. Medium matches go to
    *Suggestions* (§12.6a). Profile rebuilds wait while *Suggestions* is open, so the list
    doesn't reshuffle.
- **Carry-over to a newer diarization:**
  - **The method:** an overlap matrix on non-overlapped speech. Each new label needs purity
    of about 0.7 or more, and at least 50% of its time covered.
  - **Conflicts:** if two different people would land on one label, neither is assigned and
    the label is flagged.
  - **Determinism:** ties break by label, sorted naturally ("Speaker 2" before "Speaker
    10"), and the algorithm version is recorded. The result is the same on every run.
  - **Your decisions:** when `source` changes, the core re-keys them the same way, but a
    decision carries over only at a purity of 0.9 or more. Below that, or when ambiguous,
    it goes to review.
- **Backfill:** a one-off job diarizes every recording past the tag gate (§7.5), on local
  backends only, with embeddings where the recording allows voice. Diarizing takes about
  180 s per 52 minutes of audio, measured on the Mac; the per-turn embedding pass is cheap.

**Plaud as the yardstick (stage 6; the subscription expires 2027-08-29):**
- **The comparison:** a `speakers-compare` output pins both of its inputs and a hash of the
  assignments, and goes stale when any of them changes (§6.5).
- **What it compares:** Plaud against a voice-only result (pyannote's labels plus
  leave-one-out matches), never the resolved assignments, which include Plaud's own names.
- **What it reports,** leading with confusion and word-level error:
  - **Word-level speaker error** on Whisper's words, which avoids mismatched segment
    boundaries.
  - **Diarization agreement:** DER split into miss, false alarm and confusion, with a 0.5 s
    collar (±0.25 s), scored inside Plaud's segments with overlap excluded. Plus JER.
    Against Plaud's sentence-length segments, miss and false alarm measure granularity,
    not errors.
  - **Identity on named time:** identification error rate, per-person precision and recall,
    and a coverage-against-precision curve over the threshold.
- **The timing check** (§9.1.1): recordings that fail it are kept out of calibration,
  seeding, Plaud spans and the yardstick.
- **Unnamed Plaud segments** count as unknown, not as negatives.
- **Resolving a disagreement:**
  - **"Plaud is right"** fixes our side.
  - **"Ours is right"** goes on the *Fix in Plaud* list, which is derived. The item clears
    once a re-sync shows the corrected name.
  - **Both answers** are logged as adjudications, kept separate from the gold set.
- **The gold set:** about 20 three-minute clips, hand-labelled once, score both systems
  against the truth and measure Plaud's own error rate.
  - one clip per recording, stratified by type, speaker count and overlap
  - random windows that contain at least two speakers
  - at least 3 recordings that failed the timing check
  - the list is frozen, and its recordings are held out of profiles
  - **The labelling screen** labels runs of Whisper's words with person keys, marks overlap
    and "unsure", is blind to both systems, and exports RTTM and UEM.
  - **Labelling takes about 10 hours,** not 1.
- **After the subscription:** Plaud's labels stay in the archive. The final full re-sync is
  YouTrack DAN-15, due 2027-08-15, with its tooling in §9.1.1. **Recommended after DAN-15,
  as an optional step on it:** delete from Plaud's cloud the recordings that matter most
  (courses, forgotten people).

**Sensitive contexts (tag rules, §7.2):**
- **`voice: off`:** these recordings are diarized, but never fingerprinted or auto-matched,
  and their voice data is deleted.
- **`external_names: false`:**
  - **It covers the app's own prompts** to external models. They see "Speaker 2" instead of
    names, and known names and aliases in the prompt text are replaced, best effort.
  - **Names can remain** in transcripts and notes, for example a name spoken aloud that
    isn't in the people files.
  - **People seen only in such recordings** go in `people.private.yaml` by default.
- **The strictest rule wins,** as with privacy. Rules don't inherit (§7.1), so Dan sets these
  on each course and client tag. Naming people by hand still works there.

**Names in prompts to external models:** confirmed names only (yours or Plaud's), never inferred ones,
and none at all on recordings where `external_names` is false.

**Bulk edits (Claude):**
- **Edit the files directly,** on the homelab server. The Mac's mirror is read-only.
- **Or use a patch file:** JSONL lines of the form `{recording, source, label | span, <op>,
  expect}`, applied with `recordings speakers apply --dry-run` and then `--apply`. It also
  works over `--remote`.
  - **Operations:** `set`, `not`, `unassign`, `remove-span` and `remove-not`.
  - **A span** is identified by its (start, end).
  - **`expect`** holds the target's current value and the file's `rev`. It refuses the
    change if either has moved on.
  - **Reverting:** `speakers apply` keeps a before and after record of the whole patch in
    `state.db` (§6.8), so it can be reverted as one. Confirmations queue a single profile
    rebuild.
- **The people CLI:** `recordings people list|rename|alias|merge|report|forget`. It takes
  names, refuses ambiguous ones, and shows display names in dry runs.
- **`recordings validate` checks:**
  - unknown slugs
  - `merged_into` loops and dangling targets
  - duplicate slugs or aliases, across both people files
  - a person in the wrong people file (a warning only)
  - labels missing from their `source`
  - spans outside the media
- **No label-wide bulk assignment.** "Assign SPEAKER_01 in every recording tagged X" is not
  offered: diarization labels are arbitrary in each recording.

**Forgetting a person (scrub everything; made complete 2026-10-08):**
`recordings people forget <id>` can't be undone, so it is the one exception to "no
confirmation dialogs" (§12.2): you type the person's name to confirm. It pauses the worker,
rewrites each file in place under that recording's lock, logs every old→new hash, and does
six things.
1. It deletes their voice data and profile.
2. It removes every assignment, span and `not` that refers to them.
3. It replaces their entry with a tombstone (below).
4. It scrubs their name and aliases, replacing them with the tombstone label, from: the
   Plaud snapshots in `source/`, the Plaud transcript and notes outputs, model-written
   notes, titles, `my-notes.md`, `plaud_name_seen` and `trash/`. This is a logged exception
   to write-once, recorded in `changes.jsonl` and an audit file.
5. It deletes their rows from `state.db`, `index.db` (FTS5 included) and `voice.db`, then
   runs `VACUUM`.
6. It reports what still holds the old data:
   - restic backups (up to 12 monthly snapshots). It prints the `restic rewrite` steps for
     older snapshots, and says plainly that otherwise they age out within 12 months.
   - the NAS mirror (cleared on its next `rsync --delete`), and the Mac's copy of it
   - Synology Drive versions and Time Machine
   - Pixeltable's database
   - vault notes, from the paths the vault listener recorded (§7.4)
   - what external models already received
   - Plaud's own cloud

- **Future fetches are scrubbed automatically.** Salted hashes of the forgotten person's
  names and aliases are kept in `state.db`, and each new Plaud snapshot is scrubbed before
  it is written. Sync keeps running, and the name never comes back from Plaud.
- **The tombstone** keeps a salted hash of the old slug and of each alias, never the slug
  itself, so the slug can't be reused and the name isn't stored:

  ```yaml
  forgotten-1:
    kind: tombstone
    hashes: [<salted SHA-256 of the slug>, <one per alias>]
  ```
- **The audit file** holds the tombstone ID, the file paths and each file's SHA-256 before
  and after, never the name.

`recordings people report <id> --json` is the matching audit and export.

**Privacy:**
- **Speakers are metadata,** like tags.
- **What external agents and models may not do:** open a private or untagged recording's
  `renditions/` or `source/`, read its speakers, or read `people.private.yaml` or
  `catalog/private/`.
- **Local agents and models** (on the Spark, the homelab server or the Mac) may do all of
  those. That is what the private tier is for.
- **Enforcement:**
  - The catalog is written in the external view, and has a `private` column (§6.6).
  - The app's backend layer enforces the rule in code for every backend not marked
    `local = true` (§8.4).
  - Outside agents are told by `AGENTS.md`, and the CLI and API give them the external view
    by default (§10). The app needs no hook change; Dan's existing tool hooks are a
    backstop. On the Mac this is policy, not cryptography (§7.4).
- **Test fixtures are synthetic,** never from the real archive. A local pre-commit hook
  checks staged files against the names in the people files.

## 8. Processing

### 8.1 Steps

Each step is a queued job. Each job writes one output.

1. **Ingest** (§9).
2. **Prepare:** for video, ffmpeg extracts the audio track.
3. **Transcribe:** Spark Whisper (`/v1/audio/transcriptions`, `verbose_json` with word
   times), with the tag vocabulary as `prompt`.
4. **Speakers:** Spark pyannote (local backends only, for every recording; §7.6), merged
   into the transcript. Skipped until `local-ai`
   Phase 3.
5. **Pick:** note-type auto-pick (§7.3).
6. **Notes:** one per note type × allowed model.

### 8.2 When things run again

- **A tag change** runs only what's missing.
- **A prompt or model change** marks notes stale. "Regenerate" (one recording) and
  "Regenerate all for this tag" (bulk) run them again.
- **A vocabulary change** does not re-transcribe automatically. There is a "Re-transcribe"
  button.
- **"Reprocess archive"** is a bulk job for tagged recordings. It runs only on local backends,
  never on external ones (§7.4).
- **Jobs can safely run twice.** A job's identity is its step, recording ID and input
  versions, and a job whose output already exists is skipped. After a crash, unfinished
  jobs go back on the queue.
- **One worker**, running one job at a time, in version 1.

### 8.3 Claude use

Claude runs only when:
- a tag rule names a Claude model, or
- Dan presses "Generate with Claude",

and never on a private recording.

Bulk reprocessing never uses Claude. This stays within the subscription's "ordinary,
individual usage". For unattended Claude at volume, an API key backend is the clean route
later.

### 8.4 Backends

- **Local or external** (added 2026-10-08):
  - Each model entry in config says `local = true` only if its model runs on Dan's hardware
    and sends nothing off it: the Spark, the homelab server or the Mac (§7.4).
  - **The flag goes on each model entry,** not on a backend as a whole, because llama-swap
    can pass a request through to a hosted model.
  - Anything without that flag is external: Claude today, and any hosted API added later.
  - The privacy rule (§7.4) and the `external_names` rule (§7.2) check this flag, never a
    backend's name. A new backend is therefore external until it's explicitly marked.
  - **`recordings doctor` checks the flag** (added 2026-10-08). It refuses `local = true` on
    a Claude backend, or on a URL whose host isn't in `[privacy] local_hosts`.
- **Claude** reuses `class_notes.py`'s `run_claude` as is:
  - **The command:**
    `claude -p --system-prompt <plain text-transformer prompt> --model <m> --tools ""
    --strict-mcp-config --setting-sources "" --output-format json`, with the message on
    stdin.
  - **Environment:** every `ANTHROPIC_*`, `CLAUDE_CODE_USE_BEDROCK` and
    `CLAUDE_CODE_USE_VERTEX` variable is removed, so only the subscription can bill.
  - **Authentication:** `CLAUDE_CODE_OAUTH_TOKEN`. **Never `--bare`**, because bare mode
    ignores that token.
  - **Which model answered:** taken from `modelUsage`, as the model with the most output
    tokens.
  - **Failures:** fail on `is_error`, an empty result, or `stop_reason == "max_tokens"`.
  - **A precondition** (added 2026-10-08): the Claude account's "use my chats for training"
    setting is off. The app can't check this; Dan keeps it so.
  - **Policy basis:** Anthropic's legal page says the restrictions do not "prevent an end
    user from signing in to the unmodified Claude Code binary with their own Claude
    subscription". The Agent SDK requires API keys and is **not** used.
- **Spark** follows `run_local`'s docstring as its specification:
  - **Context window:** size the context to the input, and fail when the prompt tokens the
    server reports don't match the input (no silent truncation).
  - **Output limit:** fail when the reply hits the output limit.
  - **Which model answered:** record it, with its digest.
  - **No Claude:** never inherit `ANTHROPIC_*`, and never fall back to Claude.
- **Canned** returns stored outputs keyed by recording, note type and model. It gives the
  same result every time and makes no network calls. Used by demo mode and tests.

## 9. Getting recordings in

### 9.0 One module per source

Each input type is its own module in the core, `recordings.sources.<name>`, built
separately and in any order. Each source only produces recordings. The shared ingest code
does the rest, the same way for every source:
- checking the content hash and merging duplicates
- naming (§6.2)
- assembling the recording in a temporary folder
- writing `recording.json`
- writing outputs the source supplies, such as Plaud's own transcript

```python
class Source(Protocol):
    name: str                                   # "plaud", "upload", "url", …
    def candidates(self) -> Iterable[Candidate]:
        """Everything the source currently holds. Account-style sources (Plaud) list it;
        one-shot sources (upload, URL) return nothing."""
    def fetch(self, ref: str, workdir: Path) -> Fetched:
        """Media + raw payload + recorded_at/time_source + title + any outputs the source
        already has (e.g. Plaud's transcript and notes)."""
```

- **Shared compare:** the compare in §9.1 (Missing, Updated, Only in archive) works for any
  source that can list `candidates()`. If a Zoom cloud module is built later, it gets the
  Sync screen for free.
- **Registration:** a source is a module plus one entry in `sources/__init__.py`. Its
  config section and secrets are its own.
- **In the UI:** each source has its own panel on the Add page, based on what it can do
  (list and compare, or one-shot).
- **Build order:** `audio_router` (the import) comes first, in stage 2a, then `plaud` in
  stage 2b. `upload`
  (local files, including Zoom recordings), `url` (yt-dlp: YouTube and talks) and
  `watched_folder` follow in stage 5. A `zoom` cloud module is possible later.

### 9.1 Plaud (the main source, built first)

Plaud is how Dan records today, so getting every Plaud recording into the archive comes
before any other source. It reuses `audio-router`'s Plaud client, copied in (§19).

- **Sync compares the account with the archive.** The **Sync** button lists every recording
  on the Plaud account and matches each one to the archive by its Plaud ID (either form)
  and by content hash, so recordings that arrived through the `audio-router` import are not
  shown as missing. It reports:
  - **Missing:** on Plaud, not in the archive. **Import all missing** or **Import
    selected**.
  - **Updated on Plaud:** the title changed, or Plaud's transcript or AI notes arrived
    after the recording first came in, or Dan added in-app notes later. A new
    `source/plaud-<stamp>.json` snapshot is saved, and nothing is overwritten. Recent
    recordings are checked again for a while, the way `audio-router`'s hot window does.
  - **Only in the archive:** gone from Plaud. Shown, never deleted.
- **The compare is a dry run**, and importing is a separate click. The CLI has the same
  split: `recordings plaud sync --dry-run` and `recordings plaud sync`.
- **The scheduled sync** runs the same comparison and imports missing recordings
  automatically, so Plaud recordings arrive without a click. It can be switched off in
  config. Auto-import is safe because nothing is processed until a recording is tagged
  (§7.5).
- **The worker does the Plaud work.** All Plaud calls and writes happen in the `worker`
  container, which runs the scheduler. UI buttons ask it to act, and a lock allows one sync
  at a time (§20, stage 2b).
- **What's kept:** the full payload, including Dan's own in-app notes, goes into
  `source/`. Plaud IDs are opaque strings, and the `of_` form is accepted.
- **The consent list ("never mirror") carries over as a hard rule** (Dan, 2026-10-08):
  - Plaud IDs on Dan's consent list are never downloaded.
  - The list is configured as a path (`[plaud] consent_list`) and mounted read-only.
  - It is read fail-closed: if it is missing or unreadable, the sync stops.
  - Entries follow `audio-router`'s parser: an ID counts only when it opens a bullet or
    stands alone on a line.
  - The Sync screen shows those IDs as "excluded by consent list". Removing an ID from the
    list is how Dan deliberately brings one in.
- **Held titles come in, tagged `private`** (§7.4).
- **The grace period carries over** (Dan, 2026-10-08). A recording whose Plaud title is
  still a timestamp placeholder waits about 30 minutes for its real title before the
  privacy check runs.
- **The Plaud token** (added 2026-10-08) isn't a static secret:
  - Refresh is a POST with the refresh token, and the reply may hand back a new one.
  - Logging in needs the vendor CLI's client secret and a callback on `localhost`.
  - **The plan:** Dan logs in once on the Mac with a separate `HOME`, so the CLI writes its
    own token file. That file is copied into `state/plaud/tokens.json` (mode 0600,
    writable, not backed up). The worker refreshes it under a file lock. A 401 raises an
    alert (§14), and logging in again by hand is the fallback.
  - **A spike settles the unknowns,** printing only expiry times and hashes, never token
    values: `expires_in`, whether the refresh token rotates, and whether a second sign-in
    cancels the Mac's.

#### 9.1.1 Keeping up with edits made in Plaud (added 2026-10-08, revised after the design council)

Dan keeps editing in Plaud: renaming speakers, fixing titles, regenerating notes. Those
edits must flow down, for old recordings too. Plaud's API has no change signal: no
`updated_at` and no etag (`audio-router`'s Plaud client documents this). So a change can only
be found by fetching again and comparing.

- **Two steps, so a crash can't hide an update:**
  - **Fetch** writes a new `source/plaud-<stamp>.json` snapshot, but only if the normalised
    content changed. Nothing is overwritten. A title change seen in the listing triggers a
    fetch of that recording, so the snapshot stays reconcile's only input and can't revert
    the title.
  - **Reconcile** is a pure function. In: every complete snapshot in fetch order, the
    "acknowledged up to" marker (§6.8), the people files and `recording.json`. Out: the
    Plaud outputs and the fields Plaud owns. Taking every snapshot is what lets removals
    wait for review. Snapshots are ordered by fetch time, never by file name, because
    `x-2.json` sorts before `x.json`. It runs on every check and on `reindex`, so an update
    interrupted after the snapshot is finished next time.
  - **Reconcile is idempotent.** Each Plaud output records its snapshot (`inputs.source`)
    and its part's hash (`inputs.part_sha256`), and its `version` includes the normaliser
    version (§6.5). Reconcile skips a part that already has an output. The import (§9.3)
    and the sync share the normaliser and reconcile, so they give identical Plaud outputs.
  - **Fields Plaud owns:** only `title`, and only while `title_by` is `plaud`. A Plaud title
    change re-runs the auto-private patterns (§7.4), which may add `private` and never
    remove it.
- **Normalising before hashing** (the normaliser is versioned):
  - **Dropped:**
    - the presigned audio URL
    - `data_link`
    - `_meta`
    - the fetcher's own fields (`_fetched_from_data_link`, `_data_link_error`)
  - **Inside strings,** only the X-Amz query part of a URL is replaced, never the whole
    string.
  - **Ordering:** `source_list` and `note_list` are sorted by (type, tab, id). The segment
    arrays inside them are never reordered. The transcript's inner JSON is re-serialised
    canonically.
  - **The ID form is canonical:** `of_<hex>` and the bare hex normalise to one form, so a
    change in form isn't a change.
  - **Unknown new fields** count as changes.
  - **Bumping the normaliser's version** recomputes both sides at compare time. A test checks
    that a version bump over the fixtures writes zero snapshots.
- **Per-part hashes:** title, transcript text, speaker names, each note tab, Dan's own notes,
  and everything else. The Sync screen can then say what changed. A new Plaud output is
  written only when its own part changed, so a title edit doesn't create a transcript
  version.
- **Rename or re-segmentation?** Each Plaud transcript gets a diarization fingerprint: a hash
  of each segment's start and end, in Plaud's integer milliseconds, and its
  `original_speaker` (or `speaker`, when `original_speaker` is empty).
  - **The same fingerprint** means only names changed. `source` moves in place, and the
    Plaud-derived names follow.
  - **A different fingerprint** means a new diarization, so carry-over runs (§7.6).
- **What a change updates:**
  - **Transcript or notes:** a new version of that output. Old versions stay.
  - **Speaker names:** derived Plaud names follow (§7.6). Your own assignments stay, and a
    difference is shown only when Plaud's name changes from `plaud_name_seen`. Dismissing
    it sets `plaud_name_seen` to the dismissed name.
  - **Title:** `title_by` and `plaud.title_seen` in `recording.json` record where the title
    came from. A Plaud title change applies unless you edited the title, and then the
    difference is shown. Dismissing it sets `plaud.title_seen`.
  - **Removals are never applied on their own:** a name reverting to "Speaker N", or notes
    vanishing, waits in the review queue (§12.1). Accepting it moves the "acknowledged up
    to" marker. This is most likely around the subscription lapse.
  - **Editing an alias** re-runs the linking from the stored transcripts, with no re-fetch.
- **Partial and degraded responses:**
  - A snapshot with link errors is never used as the baseline, and nothing is built from it.
    The whole envelope is fetched again (links expire within 300 s), or the check is marked
    failed.
  - **A circuit breaker:** if more than about 5% of a sweep, and at least 10 recordings,
    report changes, snapshot writing stops and an alert is raised (§14). It counts only
    changes to "everything else" or to unknown fields, never title, transcript, speaker or
    notes edits. The baseline taken after the import, and any rebaselining, don't count
    towards it. One new volatile field would otherwise mint thousands of permanent
    snapshots.
- **Cadence: a rolling sweep, with no freeze** (rewritten 2026-10-08).
  - **Check state** lives in `state.db` (§6.8): last checked, last changed, failures and
    `next_check_at`. An unchanged check never writes `recording.json`.
  - **Each recording has a `next_check_at`.** Its interval starts at the schedule interval
    (20 min), doubles after each unchanged check, is capped at `full_sweep_days`, and resets
    when a change is found. New recordings are therefore checked often at first, as with
    `audio-router`'s hot window, and the cap keeps its freeze's savings without its blind
    spot.
  - **Each run** takes the most overdue recordings first, up to a cap of
    N / (`full_sweep_days` × runs per day), rounded up, with a minimum of 1. Runs per day
    come from the configured schedule (72 at 20 minutes). So the whole account is covered
    every `full_sweep_days` (default 1 while relabelling; 7 later).
  - **Titles** come from the list endpoint on every run, which is cheap. A changed title
    triggers a fetch (above).
  - **Errors:**
    - 429 and Retry-After are respected, with exponential backoff.
    - A 401 stops the run and raises an alert (§14). The token and its refresh are in §9.1.
  - **Limits:**
    - Only one sync runs at a time, under the worker's lock, and sweeps run in chunks so
      the worker isn't blocked.
    - An empty or shrunken listing never marks recordings "Only in the archive". A listing
      has shrunk when it holds fewer than 90% of the last good listing's IDs and has also
      dropped by a minimum absolute number.
  - **On demand:** *Re-sync from Plaud* (one recording, in Details), and
    `recordings plaud sync --full` or the Sync screen button (everything).
- **DAN-15 tooling** (the final pass before the subscription lapses; §7.6):
  - `--full` exits non-zero unless every listed recording was checked successfully. It runs
    monthly, with an alert on failure (§14).
  - a final manifest: each Plaud ID with its archive ID, last snapshot and part hashes
  - the schedule is switched off after the subscription lapses
- **Keeping Plaud's speaker data intact (stage 2a):**
  - Segments store `original_speaker` as `speaker`, Plaud's name as `speaker_name`, and
    Plaud's `embeddingKey` as `embedding_key`.
  - **Captured now:** the decoded media's duration, sample rate and channel count, and each
    segment's `embedding_key`. An `embedding_key` shared by different names is evidence
    for a merge (§7.6).
  - **Plaud gives no time zone,** so the configured default applies (§6.2).
  - **The timing check** compares Plaud's envelope `duration` with the decoded
    `media.duration_ms`; a mismatch means a trim. `audio-router` found timings at up to
    135% of the file after a trim in Plaud.
    - It also checks the last segment's end against the media.
    - From stage 4, it estimates a start offset by aligning Plaud's text to Whisper's
      words, and requires it to be under 0.3 s.
    - **A failure gates calibration, seeding, Plaud spans and the yardstick** (§7.6).
- **What you see:**
  - **The Sync screen's "Updated on Plaud" list** says what changed: title, transcript, notes
    or speakers.
  - **Each recording** shows when it was last checked.
  - **Status** shows:
    - the last good listing, and how far the sweep has got (the oldest check)
    - counts for each part, and failures
    - the circuit breaker's state and the normaliser version
    - a diff of the payload's field names between runs (it would have caught the `of_` ID
      change)
    - the share of named segments, as a canary
- **Tests:**
  - **A fake Plaud server on a real socket,** injecting:
    - 401, 429 and 5xx responses, and timeouts
    - short bodies and expired links
    - a clamped page size, and an empty listing
    - a change in the ID form, and reordered blocks
    - content switching between inline and link
  - **A property test** that changing any field not on the drop list changes the hash.
  - **A lost-update test** with two writer processes.
  - **Reruns:** sweeps and carry-over produce byte-identical files.
  - **An injectable clock.**
  - **Synthetic fixtures only.**

### 9.2 Import your own audio (a placeholder until a later stage)

These sources share one place in the UI, **Import your own audio**. It is shown as "coming
later" until its build stage (§20):

- **Browser upload:** drag and drop several audio or video files, for example a Zoom
  recording made when Plaud wasn't running. The upload streams to disk through a FastAPI
  route, not Shiny.
- **URL:** yt-dlp downloads YouTube videos and conference talks. `time_source` is
  `published`.
- **Watched folder:** files dropped into a NAS folder are ingested. Failures go to its
  `failed/` folder with the reason.

### 9.3 The `audio-router` import

- **`recordings import-audio-router`** (stage 2a):
  - It runs as a dry run first. It reports counts, private recordings, and anything it
    can't place. **The dry run accounts for every catalog row.**
  - It reads `audio-router`'s archive through `ARCHIVE.md` and **never changes it**. It
    reads a read-only copy brought over with rsync and a SHA manifest (§19).
  - It is re-runnable, and matches by Plaud ID (either form) before content hash. A match
    adds a source reference to the existing recording instead of copying it again.
  - **Held recordings were never fetched,** so they aren't in `audio-router`'s archive.
    They arrive later through the sync, tagged `private` (§7.4).
  - Most imports arrive untagged, so their existing outputs are visible while they wait in
    the inbox.
- **The mapping** (added 2026-10-08):

  | In `audio-router` | In `recordings` |
  |---|---|
  | Plaud, Google Recorder and Pocket recordings | one recording each, with a source reference that keeps `audio-router`'s source and ID |
  | rows with no audio | reported by the dry run and not imported. A Plaud one arrives later through the sync. |
  | the `media/private/` tier | imported, tagged `private` |
  | several raw snapshots for one recording | all kept, in `source/` |
  | `words`, `turns`, `merged` and `summary` renditions | outputs, keeping their engine names |
  | `audio-router`'s Plaud renditions | **not copied.** Reconcile rebuilds them from the snapshots, so they can't be duplicated (§9.1.1). |
  | the ledger entry with no media | reported by the dry run and not imported |
  | the one byte-identical Plaud pair (two IDs, one audio blob) | one recording with both Plaud source references (§6.4) |

## 10. How other tools read the archive

- **Files:** `FORMAT.md`, `README.md`, `AGENTS.md` and the JSON Schemas.
- **The catalog CSVs** (§6.6).
- **`catalog/changes.jsonl`:** an append-only change log, with one numbered line per event:
  - recording added
  - tags changed
  - title changed
  - transcript added
  - notes added
  - notes now stale
  - speakers changed
  - people changed (counts only)
  - updated on Plaud
  - source updated
  - privacy changed
  - moved to trash

  A listener remembers the last number it handled. Lines carry IDs and event types, never
  titles or text. Numbering takes a global lock, and the file survives catalog rebuilds.
- **The external view** (added 2026-10-08) is defined once in the core, and every outside
  reader goes through it: the JSON API and `--remote` (by default), the catalog (§6.6), MCP
  servers and the vault listener. It leaves out:
  - the content and speakers of private and untagged recordings (§7.4)
  - `people.private.yaml` and `catalog/private/`
  - the titles of private and untagged recordings, which are blanked
- **The full view** needs a local-scope token, held as a secret on local machines only
  (§5). `AGENTS.md` forbids external agents to use it. That is policy (§7.4).
- **JSON API** (`recordings-ui`):
  - list or filter recordings by tag, date or note type
  - fetch a recording's transcript and notes
  - `changes?since=N`
  - bulk tag changes with `dry_run`

  It answers in the external view unless the request carries the local-scope token.
  Private and untagged recordings are still listed, with `private: true` or
  `untagged: true` and blank titles. The app itself never sends a private recording to an
  external backend (§7.4).
- **CLI:** `recordings …` with `--json`. Agents use this first.
- **Links:** every recording has a stable link, `<base>/r/<id>`.

## 11. Editing files by hand

- **Changes are noticed** by watching the archive, a periodic rescan (file watching misses
  changes made over network shares), and `recordings reindex`.
- **Schemas:** `recording.json` and `tags.yaml` reference their JSON Schemas, so editors
  and agents can check edits. An invalid file is never overwritten or "fixed". The index
  keeps the last good version, and the error is shown with its file and line.
- **Outside edits are previewed before anything runs.** If an outside edit would start
  jobs, they wait for approval, for example "37 notes, 2 external". Edits made in the UI
  apply immediately. *(Dan asked for UI actions to apply immediately. This preview is kept
  for outside edits only, as Dan confirmed on 2026-10-08.)*
  - **Batches:** held jobs are grouped into **batches**, one per rescan or `reindex` that
    found changes. Each batch records:
    - what changed, for example "Tags added: school/course-101 on 40 recordings"
    - when and how the change was detected
    - the jobs it would start, by kind and backend
  - **Approving them** is done in bulk from the Status page (§12.5), or with
    `recordings pending list | run | run --local-only | dismiss` (each with `--json` and
    batch IDs).
  - **Dismiss** runs nothing and keeps the edits. Those recordings show their missing
    outputs with a **Process** button.
- **Bulk tag changes** are also available through the CLI and API, with a dry run.

## 12. Interface

- **Times and dates follow ISO 8601** (Dan, 2026-10-08). The app uses no am/pm.
  - **Times are 24-hour:** "14:05", "Busiest: Tue 14:00–16:00".
  - **Weeks start on Monday,** everywhere: the date control, Insights and the punch card.
  - **Full dates read `2026-10-06`.** Where space is tight, a compact label may use the
    month name ("Oct 6", "Oct 1 – 15"). Stored values are ISO 8601 throughout (§6.2).
- **Navigation** (added 2026-10-08): the top bar holds Library, Tags, People, Insights,
  Add and Status.
  - **Badges:** at most two. People counts naming work (To link, Suggestions, Fix in
    Plaud). Review counts everything else waiting for Dan: the review queue and the
    approvals batches.
  - **On a phone** it becomes a bottom tab bar with five slots: Library, Tags, People,
    Insights, and More (Add, Status).
- **A keyboard map,** with a `?` help sheet that lists it on every page:

  | Key | Does | Where it works |
  |---|---|---|
  | `T` | opens the tag picker for the selection | Library list, Tags grid |
  | `P` | opens the speaker picker | the focused transcript line |
  | `D` | opens the date control | Library list header, Insights |
  | `1`–`9` | picks a person already in this recording | the speaker picker |
  | `Y` / `N` | accepts or rejects, and moves to the next clip | *Suggestions* |
  | `S` / `U` | skips, or undoes the last decision | *Suggestions* |
  | `[` `]` | jumps to the previous or next line of the selected label | Transcript |
  | Space | plays or pauses; in *Suggestions*, plays the clip | everywhere a player is shown |
  | `?` | opens the help sheet | everywhere |

### 12.1 Library (home)

- **Left sidebar:**
  - All, **Untagged** (the inbox, "N waiting for a tag") and Needs attention (failed or
    stale). All and Untagged act as filter chips (§12.6a): Untagged adds a chip, and All
    means Clear.
  - **Review (N)** (added 2026-10-08): one queue for every item awaiting Dan's review:
    Plaud removals, "Plaud now says X", title differences and *Fix in Plaud*
  - the tag tree with counts. Picking two tags combines them with AND.
  - a separate **Private** section with a lock icon
  - note types
  - the **People** facet (§12.6a)
- **Middle:** the recording list, newest first. It has search across titles, transcripts
  and notes (SQLite FTS5). Each row has tag chips, a status (transcribing, stale, failed or
  private), and who is in the recording ("Dan, Alex +2"), with inferred names dashed.
  Filtering by people and dates is in §12.6a.
- **Right:** the recording pane.
  - **Header:** a title you can edit in place, the date, time with offset, duration,
    source and ID, tags, and "+ tag".
  - **Player:** stays visible as you scroll. For video, it shows the video.
  - **Tabs:** **Transcript** · **Notes (N)** · **Plaud** · **My notes** · **Details**.
    - **Transcript:**
      - The current line is highlighted, with the current word marked, and the view
        follows the audio. A **Follow audio** switch lets you scroll freely.
      - Clicking any line seeks the audio there.
      - Clicking a speaker label names the speaker.
      - A selector chooses between transcripts, for example Whisper or Plaud.
    - **Notes (layout B, chosen):**
      - A list down the left shows every notes output, grouped by note type, with its
        models nested underneath and older versions under "⟲ N older".
      - The stale dot appears on the list.
      - Buttons: "+ Add note type", "Regenerate", and "Generate with Claude" (locked when
        private).
      - **Compare with…** shows any two outputs side by side.
      - A footer shows the note type, the model that answered, the prompt version and the
        time.
    - **Details:** sources, every output and its backend (including what went to external
      models), and jobs.

### 12.2 Tagging (applies immediately, with Undo)

- **Selection works like Finder:**
  - click selects one row
  - Cmd or Ctrl-click adds or removes a row
  - Shift-click selects a range
  - checkboxes still work
- **Drag:** drag the selection onto a tag or note type in the sidebar. Dropping onto
  **+ New tag…** opens the tag picker so you can type a new name.
- **The `T` key** opens the tag picker for the selection.
  - Existing tags come first, matched as you type. **Create "…"** comes last, so Enter on
    a partial name chooses the existing tag. (A bug found in testing.)
  - Enter adds a tag, and the picker stays open for more. Esc closes it.
  - `/` creates folders.
- **No confirmation dialogs** in the UI. Every action shows an Undo toast. There are two
  exceptions, both about privacy:
  - *Forget*, which can't be undone: you type the person's name to confirm (§7.6).
  - Moving a person from `people.private.yaml` into `people.yaml`, which makes their name
    visible to external readers (§7.6).

### 12.3 Tags page

- **Grid:**
  - one row per recording, one checkbox column per tag, including `notes/*`
  - **a click anywhere in a cell** toggles it
  - clicking a column header adds that tag to the selected rows
  - Finder-style row selection and `T` work here too
  - **Untagged only**, with a summary snippet column
  - tagged rows stay where they are until you refresh
  - a column picker by folder
- **Tree:**
  - tag folders with counts
  - the Private group, kept separate
  - selecting a tag shows its rules as a form, with a YAML view
  - Rename, Move and Delete. These rewrite recordings, with a preview, because they touch
    many files.

### 12.4 Add

- **Plaud panel (first):**
  - the time of the last sync and the next scheduled sync
  - **Sync**, which shows counts for Missing, Updated on Plaud, Only in archive and Up to
    date (§9.1), and lists the IDs "excluded by consent list"
  - the Missing list with checkboxes, plus **Import all missing** and **Import selected**
- **Import your own audio:** upload, paste a URL and the watched folder (§9.2). Shown as a
  "coming later" placeholder until its stage.

### 12.5 Status

- the job queue
- failed jobs, with reasons and Retry
- Spark and Claude reachability
- the date the Claude token expires, with a warning 30 days before
- **Backups** (§15.1): when the last backup finished, and the last restore test. Needs
  attention if the last backup is more than 2 hours old.
- **Disk space** on the homelab server: a warning below 20% free. Below 5% free, new imports stop,
  with the reason shown.
- **Status is the pull view.** Alerts push the urgent problems to Dan's phone (§14).
- **Waiting for approval** (batch processing of the outside-edit previews, §11):
  - **An orange badge in the top bar**, visible on every page, shows "N waiting · M jobs".
    Clicking it opens this panel.
  - **Each batch is a card** showing what changed, when it was detected, and chips for the
    jobs it would start. Jobs for external models are blue so they stand out. Private recordings show
    "🔒 External models skipped: private" and never list such jobs.
  - **Selecting:** tick whole batches, or **All**. Expand a batch to untick single
    recordings.
  - **A sticky action bar** shows "N batches · M jobs, K external", with three buttons:
    - **Run**
    - **Run local only**: jobs on local backends only. The notes from external models can
      still be generated later.
    - **Dismiss**
  - **Undo:** each action shows an Undo toast. Undoing a Run cancels jobs that haven't
    started yet.
- "Reprocess archive"

### 12.6 Look and feel

- **Light, Dark or System** switch in the top bar. It follows the OS until changed, and the
  browser remembers the choice.
- **Warm neutrals** from Dan's site's `_brand.yml` (chosen):
  - **Light:** warm-white `#F8F6F2`, surfaces `#FFFDFA`, sidebar `#EFE8DF`, text
    `#1C1A17`, muted `#6B6258`.
  - **Dark:** base `#171512`, surface `#232019`, border `#2E2B26`, text `#F8F6F2`, muted
    `#B8AEA2`.
- **NYC blue `#236192`** is the working colour: selection, links, the play button and
  primary buttons. It becomes `#6CA6D9` in dark mode, because `#236192` is only 2.8:1 there.
- **NYC orange `#F26522`** is the accent: the active page, the line playing now, the
  playhead, drop targets and the stale dot. **Orange never carries text in light mode** (it
  is 3.2:1 on white).
- **Other colours:**
  - success `#72994E` (note-type chips; text `#3D6123` in light mode, `#9CC27A` in dark).
    The light text was `#4E7A2E` until 2026-10-08. It reached only 3.5:1 on the chip's own
    tint, so it was darkened.
  - danger `#9A4665` (`#D27A9A` in dark)
- **Measured contrast:**
  - blue on white 6.6:1
  - dark-mode blue 6.3:1
  - muted text at least 4.5:1 in both modes
- **Font:** Atkinson Hyperlegible and Atkinson Hyperlegible Mono, **self-hosted**.
- **One `_brand.yml`** in the repo is the single source for these values, light and dark
  together. Any colour can be `{light: …, dark: …}`, the syntax Quarto's brand docs
  describe and that Posit's brand-yml skill dates to Quarto 1.8. I checked this on
  2026-10-08, after first wrongly saying it needed two files.
  - The build turns it into shadcn/ui's CSS variables (`--background`, `--primary` and so
    on) for `:root` and `.dark`.
  - The R and Python `brand_yml` packages don't read the light/dark form, but the app
    doesn't use them.
- **Phone width:** every page works on a phone. The list and recording stack, so you can
  play and read on the phone.

### 12.6a Speakers, people and finding recordings (2026-10-08, revised after the design council; mockups to follow)

- **In the transcript:**
  - **Speaker chips:** every line shows its speaker chip on hover or focus, so a wrong line
    in the middle of a run can be fixed.
  - **Chip styles:**
    - **Confirmed** (yours or Plaud's): solid.
    - **Inferred (auto):** dashed, as auto tags are, with "· auto" text as tag chips have.
      The dash is drawn in `--muted-foreground`, not `--border`, which at about 1.3:1 is
      nearly invisible.
    - **Unnamed:** plain muted text ("Speaker 2").
    - **Placeholders** ("Unknown 1"): an outlined chip with muted text, so a placeholder
      never looks like a real name.
    - **Named in Plaud:** the same solid chip as yours. Hovering it, and the speaker strip,
      say "named in Plaud". Plaud-named speech is treated as confirmed and feeds the voice
      profiles (§7.6).

    The transcript shows no scores.
  - **The picker:** clicking a chip, or pressing `P` on the focused line, opens it.
    - **"In this recording":** this recording's labels come first, named or not.
      - Picking an unnamed label for an unnamed one merges both into a placeholder ("Unknown
        1").
      - Picking an unnamed label for a named one assigns that label to the same person.
    - **Then people,** with search, and the people Dan tags most often listed first. Enter
      picks an existing match before "Create *Name*" (the same fix as the tag picker).
      Matching ignores case.
    - **Keys:** 1–9 pick people already in this recording.
    - **Scope** is a two-way toggle with counts: **All 42 lines from this voice** (the
      default) | **Only 3:12**.
    - **Range fixes:** Shift-click selects a range of lines, Finder-style, without seeking,
      and the scope then reads **These 7 lines**.
    - **On a phone,** tapping a line opens an action sheet.
    - **One verb, chosen by the tag's state:** "Not Alex" on an inferred tag (it removes the
      tag, records `not`, and offers the next-best match); "Unassign" on yours. A
      Plaud-named label is derived, so it gets "Not Alex", not "Unassign".
      - **For one line or a range,** "Not Alex" writes a span with the reserved person
        `unknown` (§7.6). Choosing a person instead writes a span for them.
    - **It closes on choice.** A label has one person.
  - **The toast:** "Speaker 2 → Alex Kim · 42 lines · Undo".
  - **The sweep notice** comes later and stays up until dismissed: "Found Alex in 23 older
    recordings · Review · Undo". It has its own Undo, because sweeps finish long after a
    toast is gone.
    - **Undo** reverses the confirmation that triggered the sweep. The profile is rebuilt
      without it, and the matches that depended on it are withdrawn.
  - **A sweep never silently changes the open recording.** The open recording stays pinned
    to the assignment generation it opened with (§7.6). A banner offers "Voice matching
    updated 2 labels · Show". *Show* moves the pin to the newest generation.
  - **Medium matches** appear in the speaker strip with Confirm (Dan, 2026-10-08), as well
    as in *Suggestions*.
  - **Each line** is restructured so a chip button no longer sits inside the line's button,
    which is invalid HTML. The built `TranscriptTab` lines and `RecordingList` rows still
    put a `<div>` inside a `<button>`; stage 3a fixes both.
  - **A single-speaker transcript** shows no speaker strip, and a note that only one voice
    was found.
- **The speaker strip** above the transcript has one row per label:
  - talk time, the person, and the confidence (High or Medium, with the number on hover)
  - Confirm and Reject
  - a ▶ button that plays a 5-second sample

  Clicking a label highlights its lines and adds ◀ ▶ to jump between them (keys `[` `]`).
  Speaker bands under the scrubber need a custom scrubber, because the built player is the
  browser's own.
- **The People page** (top navigation, beside Library and Tags; its badge counts what needs
  you). Tabs:
  - **People:** one row per person, with aliases, recordings, talk time, last heard, a "who
    is this" clip, and the *Recognise this voice* switch. A person's page lists their
    recordings, plus *Merge with…*, aliases and *Forget*. "Unknown N" placeholders are
    grouped as "Unnamed (N)". With nobody named yet, the empty state links to *To link*.
  - **To link (N):** Plaud names not yet linked.
    - Each name shows a count, a ▶ and a suggested link. "Accept all suggested links" takes
      every suggestion at once.
    - Multi-select, then "One person from these 3": you choose the display name, and the
      rest become aliases.
    - One Plaud name that means two people is split per recording.
    - "Not a person" or "Group" (for "Students" and the like).
    - Plaud's generic "Speaker N" names are hidden.
  - **Suggestions (N):** Medium matches only, since High ones are applied as auto tags
    (§7.6). They are grouped by person, with the person's reference clip pinned at the top
    and sorted by score, and "Accept all" sits over the top of the list. Space plays, Y/N
    decides and moves to the next clip, and Skip and Undo have keys (§12).
    - **"Accept all" skips the random lower-scored candidates** mixed in for calibration
      (§7.6). Those always need a single Y or N.
  - **Fix in Plaud (N),** which is also part of the review queue (§12.1).
    - **From stage 3b,** it lists every label where Dan's own assignment differs from
      Plaud's name for it.
    - **From stage 6,** it adds the "Ours is right" calls.
    - **One Plaud name, two voices:** evidence that a name covers two people comes from
      differing Plaud `embedding_key` values. Whether that key is stable across recordings
      is checked in the stage 2b spike before it's relied on.
  - **Plaud vs. home** statistics.

  The Suggestions and Plaud-vs-home tabs stay hidden until stage 6.
- **Finding recordings (Library):**
  - **Combining filters:** AND across kinds (tags, people, date, search, Untagged). Inside
    People, *all of* (the default) or *any of*. Untagged plus a person is allowed, and is
    handy for triage.
  - **Every active filter is a chip** above the list, search text and Untagged included.
    *Clear* clears them all. The all/any toggle sits inside the People chip: "Dan + Alex ·
    all ▾".
  - **The People facet** in the sidebar lists people with counts, inferred ones split out:
    "Alex 42 (8 unconfirmed)".
    - It sits **below Tags** (Dan, 2026-10-08). Below the fold is fine: Dan mostly picks a
      tag first, then names people.
    - Click to filter. ⌘-click on a Mac, Ctrl-click elsewhere, adds more people.
    - On a phone it collapses.
    - "Unknown N" placeholders are grouped as "Unnamed (N)" and left out of the filter.
    - **Counts** are for the whole library. The all/any menu's counts include the other
      active filters.
    - **Untagged and a tag** replace each other, since a recording can't be both.
    - **A "confirmed only" switch** in the list header narrows a person filter to
      confirmed matches.
  - **A person filter includes inferred matches by default** (Dan, 2026-10-08), and the count
    stays split, as above.
  - **Search** shows "Person: Alex Kim" as its own row, which turns into a chip, separate
    from transcript hits.
  - **The date control** is a button in the list header ("Any date", "Oct 1 – 15",
    "October 2026"; `D` opens it). It is built on shadcn's Calendar in range mode; check its
    current docs first.
    - **Presets on the left:** Today, Yesterday, Last 7 days, Last 30 days, This month, Last
      month, This year, All time. Term presets defined in config (`[calendar] terms`, such as
      2026W1) come after them.
    - **On the right,** a two-month calendar:
      - Range ends are solid NYC blue (`#236192`; `#6CA6D9` in dark), the days between take
        a blue tint so the selection forms one continuous band, and today has a ring.
      - The week starts on Monday (ISO 8601).
      - Days with recordings carry a muted dot, which turns white on selected days. On a
        range end in dark mode (`#6CA6D9`) the dot uses the dark text colour, because a
        white dot there measures about 2.3:1.
      - No orange: that marks the line playing now.
    - **The month caption** opens a custom month and year grid with counts, for jumping back
      years. react-day-picker's built-in dropdown only gives two plain selects.
    - **The footer** reads "Oct 1 – 15 · 9 recordings", with Clear and Apply.
      - **Presets** apply at once.
      - **Calendar clicks** are a draft until Apply.
    - **Keys:**
      - Arrows, and PgUp and PgDn.
      - Shift-arrows extend the range. react-day-picker uses Shift-arrows to move by month,
        so the build overrides `onDayKeyDown`.
      - Check which react-day-picker version shadcn pins: its docs now show v10, under
        `@daypicker/react`.
    - **Term presets** come from config:
      `[calendar] terms = [{ name = "2026W1", start = "2026-09-08", end = "2026-12-04" }]`.
    - **On a phone:** a bottom sheet with one month, and the presets as a scrolling row of
      chips.
    - **Ranges** use each recording's local date (`recorded_at` with its offset).
  - **Empty states** suggest ways to widen, with counts: "Any of: 12 · All dates: 3 · Clear".

### 12.6b Insights (added 2026-10-08, rewritten after the second design council)

A dashboard of the archive. It doubles as a showcase of the architecture: every number is
computed in Python on the Shiny side (`@reactive_output("insights")`) and drawn by React, and
it recalculates as the controls change.

- **Order, top to bottom:**
  1. four cards: Recordings, Hours, Days recorded and Words, each with an average
     underneath
  2. the heatmap at full width, with streaks in its caption
  3. the punch card, beside an "Averages and busiest times" list
  4. model usage, beside trends
  5. Speakers
  6. the footer
- **Controls:**
  - the same date control as the Library
  - **a tag filter** (Dan, 2026-10-08), reusing the Library's tag chips. Here the chips
    combine as *any of*, not the Library's AND, because Insights compares categories that
    rarely overlap, such as teaching, meetings and talks.
  - **private people** are counted but never named: they appear as one masked row ("1
    private person")
  - an hours or count switch, which changes the charts, not the cards
  - only days and months drill down, into the Library
  - hash routes, so Back returns to Insights
- **The heatmap,** in the style of GitHub's: one square per day.
  - **Colour:** a single blue scale, never orange, in six fixed steps.
    - **Hours mode:** 0, under ½, ½–1, 1–2, 2–4, 4+.
    - **Count mode:** 0, 1, 2, 3, 4, 5+.
  - **Empty and other days:**
    - Empty days use a `--heat-0` token, distinct from the card in both modes; `--muted` is
      darker than the card in dark mode and reads as holes.
    - Days outside the range are dimmed.
    - Future days are blank.
  - **A legend** ("Less ▢▢▢▢ More") with the values.
  - **Years:** the default view is the past 12 months, rolling. A year stepper switches to
    calendar years.
  - **Hover** shows "Tue 2026-10-06 · 3 recordings · 2.4 h". Clicking a day opens the
    Library filtered to it. On a phone, a tap shows the tooltip with "Open in Library →".
  - **Keys:** arrow keys move between days, and each cell has an aria-label.
- **The punch card:**
  - weekdays down, hours across, with circles sized by area
  - totals for each row and column, which back up the callout ("Busiest: Tue 14:00–16:00",
    in 24-hour time).
    It says *busiest*, not *most productive*: recording time measures meeting load.
  - in hours mode, a recording is split across the hours it covers
  - turned sideways on a phone
- **Model usage:** horizontal bars, for words per transcript engine and notes per notes
  model, with a stacked by-month view that shows the move from Plaud to Whisper. Words are
  counted from the chosen transcript only, so the Words card matches.
- **Trends:** hours per month, and the average per week.
- **Speakers** (once stage 3b is in place): who you meet most, talk time per person, and your
  share of talk time in lectures against meetings. By default, "who you meet most" leaves
  out people seen only in private recordings or under `external_names: false`, and masks
  names from `people.private.yaml`.
- **Showing off Shiny:**
  - the date, the metric and the tag filter go to Python through `useShinyInput`; hover
    stays in the browser
  - while Python recalculates, the old charts stay up under a thin "Recalculating in
    Python…" bar (`useShinyOutputStatus`)
  - the footer reads "Computed in Python by Shiny · 312 recordings · 41 ms · source ↗",
    plus "in your browser (Pyodide)" on Pages
- **On a phone:** the cards sit two by two, and the heatmap scrolls sideways, starting at the
  latest week.
- **Empty states:**
  - nothing in the range: "Widen to This year (38)"
  - no transcripts yet: "Words: waiting on 12"
  - no people yet: a link to *To link*
- **Private recordings** count in the totals. Their titles never appear here.
- **The demo year** (Dan, 2026-10-08): about 300 recording records with no audio, covering
  the last 12 months, rolling, so the date presets work. They are generated from a fixed
  seed with invented titles, never derived from real data. They appear only on Insights, so
  the Library keeps its 4 real recordings.
  - a banner that stays up ("Sample data, made up"), with "Sample" in every tooltip and the
    footer
  - drill-down is disabled, with an explanation
  - made-up names in Speakers
- **Building it:**
  - charts use shadcn's chart components (Recharts, with the brand tokens, so light and
    dark both work); the heatmap and punch card are plain SVG
  - `_brand.yml` must also generate `--chart-1…5`
  - check Recharts' `react-is` dependency under React 19, while React comes from shinyreact
  - the Python is plain standard library, so it runs on the Pages demo (Pyodide) too

### 12.7 Mockups

The brainstorming mockups are in
`docs/superpowers/specs/2026-10-08-recordings-mockups/`:
- `library.html` (layout)
- `library-v2.html` (drag-to-tag and the notes-layout options)
- `tagging-v3.html` (working drag, the `T` picker, the new-tag picker, Finder-style
  selection, the grid with whole-cell clicks, and the tree)
- `theme.html` (the light and dark colour options)
- `approvals.html` (approving outside-edit batches in bulk, in the chosen warm palette)

They are fragments written for the brainstorming companion's frame, so their CSS variables
come from that frame.

## 13. UI technology

- **Server:** **FastAPI** hosts the JSON API, uploads and media streaming (Starlette
  serves HTTP Range requests, so seeking works). It also mounts the **Shiny for Python**
  app, which uses **shinyreact** for a React 19 client written in TypeScript and built with
  Vite.
- **Data flow:**
  - shinyreact's `reactive_output` sends plain JSON to the client.
  - `useShinyInput`, `useShinyOutputValue` and `useShinyMessageHandler` connect client and
    server.
  - The server pushes job progress and new recordings without polling.
- **The pin:**
  - Python `shinyreact` and the npm `@posit-dev/shinyreact` package are pinned to exact
    versions and **upgraded together**. Whether the npm package is the recommended route
    for Vite builds gets checked in shinyreact's "TSX and build tools" doc.
  - Dependabot opens the upgrade pull requests.
  - **Each upgrade:**
    1. Read the release notes.
    2. Re-link the skills.
    3. Check the Node pin.
    4. Run all tests.
- **Components: shadcn/ui and Tailwind v4,** chosen 2026-10-08.
  - **Why:** the `shinyreact-build-app` skill calls this "the default" (SKILL.md line 126),
    and its examples 03 and 04 use it. The shinyreact website docs don't recommend any
    component library.
  - **How it works:** shadcn copies component source into
    `packages/ui/frontend/src/components/ui/`. It is set up with **`--base radix`**: shadcn
    switched its default to Base UI on 2026-07-02 and says Radix is "still fully
    supported", and Radix matches shinyreact's shadcn examples.
  - **React stays external,** from `window.shinyreact`, so shadcn's components share the
    hooks' React.
- **Docs in context.** `make skills` installs:
  - **shinyreact's skills,** via `uvx library-skills --claude` after `uv sync`. It links
    `shinyreact-build-app` with its references and `shinyreact-convert-app` from the
    installed version into `.claude/skills`, and git ignores the links.
  - **shadcn's official skill,** via `npx skills add shadcn/ui`. It reads
    `components.json` and runs `shadcn info`.
- **The project rule in `CLAUDE.md`:** check the current docs or skill before writing
  code against any of these. Never write them from memory.
  - **shinyreact:** load `/shinyreact-build-app`, then https://posit-dev.github.io/shinyreact/.
  - **brand.yml:** https://posit-dev.github.io/brand-yml/, plus
    https://quarto.org/docs/authoring/brand.html for light/dark.
  - **shadcn/ui:** the shadcn skill, then https://ui.shadcn.com/docs.
  - **Tailwind v4:** https://tailwindcss.com/docs.

  Note what was checked. If the docs don't cover it, say so and never guess. Such gaps
  are worth reporting upstream.
- **A head start:** `shinyreact-convert-app` can port `app_recordings.py` as the UI's
  starting point.
- **Assumption, checked again:** shinyreact was pre-release as of 2026-09-13 (Python 0.1.0).
  Pinning keeps lock-in low, because the React components belong to us and could be pointed
  at the JSON API instead.

## 14. Errors

- **Spark unreachable:** Spark jobs wait (they don't fail) and retry with backoff. The
  Status page says why.
- **Claude problems:** a usage limit or failed login postpones the job, with the reason
  shown. Token expiry is warned about 30 days ahead.
- **Too long:** context overflow or an output-limit stop is a **loud failure**, never a
  silent truncation.
- **Bad media:** an ingest failure. Watched-folder files go to `failed/` with the reason.
  Nothing appears half-written (§6.4).
- **Invalid edits:** shown, never overwritten (§11).
- **Visibility:** every failure appears in **Needs attention** with Retry.
- **Alerts** (Dan, 2026-10-08) are push notifications through ntfy: self-hosted, or ntfy.sh
  with a private topic. The topic is a secret (§5).
  - **They fire on:** a Plaud 401, a circuit-breaker trip, a disk warning, a failed backup
    or restore test, and a failed `--full`.
  - **A dead-man's switch:** each good sync and each good backup pings it, and a missed
    window raises an alert, so a stopped worker or backup is noticed too.
  - **Status keeps the pull view** (§12.5).

## 15. Security and backups

- **Access:** reachable only over Tailscale, published on a port. There is no app login in
  version 1.
- **Your own browser is not a trust boundary.** Any website Dan visits runs in a browser that
  can reach the tailnet. So the app answers only to the host names it knows: localhost, the
  host in `[server] base_url`, `[server] allowed_hosts`, and `RECORDINGS_ALLOWED_HOSTS`.
  Other Host headers get 400, which defeats DNS rebinding. A websocket whose `Origin` isn't
  one of those names is closed before Shiny accepts it. (Added 2026-10-08, after the stage-1
  final review showed a foreign page could read a private recording through the websocket.)
- **Before the app goes beyond Tailscale:** when the reverse proxy arrives, the proxy
  handles login and the app only accepts requests from the proxy. This is a requirement.
- **Cross-site writes:** the Host allow-list stops DNS rebinding, but not a foreign page
  posting to the app's real host name. So every route that changes data, from stage 2a
  because Import is a write (imports and syncs; later tags, people, merges, *Recognise*,
  *Forget*):
  - checks `Origin` or `Sec-Fetch-Site`
  - requires exactly `Content-Type: application/json`

  A tailnet ACL limits the app's port to Dan's devices.
- **Slugs never reach logs** (added 2026-10-08). `/people/<slug>` paths would land in
  access logs, so the routes the app logs use person IDs, or the people pages keep their
  routes in the URL's hash part, which servers don't log.
- **Containers:** both run as non-root users. Secrets follow §5.

### 15.1 Backups to the NAS (from stage 2a, the first stage that writes)

- **A backup, not a sync.** A two-way sync, like today's Synology Drive folder, faithfully
  copies a bad write or a deletion to the NAS. Versioned backups keep history, so mistakes
  can be rolled back.
- **The tool is restic,** encrypted and deduplicated. It runs on the homelab server as a `backup`
  service in the same Docker Compose project and writes only to its own repository on
  the NAS.
  - **Transport:** restic over SFTP, or rest-server with `--append-only`; a spike decides
    (§19). The NAS SSH key and its `known_hosts` are secrets (§5). An NFS mount is the
    fallback; the homelab server can mount a NAS share over NFS. Either way, restic writes
    only its own repository, with its own locking, and the app never writes to the NAS.
- **What is backed up:**
  - the archive
  - `config.toml`
  - `docker/deploy.env`
  - a consistent copy of `state.db`, taken with SQLite's own backup command first (§6.8)
  - **not** `index.db`, which is derived, nor `voice.db` or the Plaud token store
  - **not** the secrets folder: those live in your password manager
- **When, and for how long:** hourly. Keep 24 hourly, 14 daily, 8 weekly and 12 monthly
  snapshots.
- **Checks:**
  - a weekly `restic check`
  - a monthly automatic restore of the newest snapshot into a scratch folder, which must
    pass `recordings validate`, because a backup that has never been restored isn't proven
  - the results appear on the Status page (§12.5)
  - a failed backup or restore test raises an alert, and each good backup pings the
    dead-man's switch (§14)
- **A read-only mirror for the Mac:** an hourly one-way `rsync` of the archive to a share
  on the NAS. The Mac and Pixeltable read that, either mounted read-only or synced down
  one-way with Synology Drive on the Mac. **The homelab server itself doesn't run Synology Drive.**
  - **rsync runs with `--delay-updates`,** so a reader never sees a half-copied file.
  - **It refuses to run** when the archive's sentinel is missing or its UUID has changed
    (§6.7), so an unmounted archive can't wipe the mirror through `--delete`.
  - **The mirror holds private recordings too.** On the Mac, keeping external agents out of
    them is policy (§7.4).
- **Capacity:** The NAS's free space is checked before backups start. The
  backup repository grows with the archive, and deduplication plus the retention limits
  keep it close to the archive's size.
- **Later:** Synology's own snapshots of the backup share, plus Hyper Backup to an
  off-site target, complete a 3-2-1 setup.

## 16. Testing

- **Fixtures are synthetic.** Recorded payloads from the real Plaud account would carry real
  names into a public repo, and gitleaks can't see names. A local pre-commit hook checks
  staged files against the names in the people files.
- **pytest on the core:**
  - the archive writer and reader, and naming
  - tag rules and the privacy calculation
  - the job queue, and safe re-runs
  - the importer's dry run, with both Plaud ID forms
  - the catalog and change log
  - outside-edit detection
  - schema validation
  - the docs-versus-writer test
- **Guard tests that must fail without their fix:**
  - an untagged recording is never processed
  - a private recording never reaches any backend not marked `local = true`, including
    when the private tag is added after the job was queued
  - an unmarked backend is external
  - no audio goes to a non-local backend
  - `external_names` strips names from prompts
  - every config setting reaches the code that uses it
  - demo mode can't reach real paths, secrets or the network
- **Backend stand-ins:**
  - **Spark:** a fake server on a real local socket.
  - **Claude:** a fake `claude` executable on `PATH` that records its arguments and
    environment, proving the locked-down flags are used, `ANTHROPIC_*` is removed, and
    `--bare` is never passed.
- **Playwright against demo mode:**
  - drag-to-tag, the `T` picker and whole-cell grid clicks
  - click-to-seek, and following the transcript
  - Undo
  - light and dark mode

  Test setup follows shinyreact's testing docs.
- **CI (GitHub Actions):**
  - the tests
  - gitleaks
  - the frontend build with Node from `.nvmrc`
  - the Docker build for amd64 and arm64
  - Dependabot for pip, npm and Actions

## 17. Demo mode

- **Starting it:** `make demo`, or `docker compose -f docker/compose.demo.yml up`.
- **The demo archive:** `demo/archive/` holds 3–4 short recordings, each credited with its
  license in `CREDITS.md`:
  - a solo public-domain LibriVox reading (lecture-like)
  - a LibriVox reading with several readers (several speakers)
  - a public-domain NASA clip (video)
  - one recording left untagged, and one tagged private
- **Processing:** the Canned backend returns pre-written transcripts and notes, so tagging
  in the demo "processes" the same way every time.
- **Isolation:**
  - it ignores the real config and all secrets
  - every start uses a fresh copy of the demo archive and a temporary state folder (§6.8)
  - it makes no network calls

  A test enforces this.
- **Purpose:** a bug report reads "from `make demo`, do X". It also doubles as the format's
  worked example and the test data.
- **Speakers in the demo:**
  - only historical public figures, in `people.yaml`
  - one label split in two, one label holding two people, and one rejected inferred name,
    so every correction can be tried
  - synthetic embeddings, never voiceprints of real people (the Apollo 11 clip includes a
    living person)
- **Insights in the demo** use a generated, clearly labelled sample year with no audio:
  the last 12 months, rolling, from a fixed seed, never derived from real data (§12.6b).

### 17.1 The static demo on GitHub Pages (added 2026-10-08)

- **Where:** the same demo runs entirely in the browser at
  <https://chendaniely.github.io/recordings/>. It is built with Shinylive (Shiny for Python on
  Pyodide) and deployed by `.github/workflows/pages.yml` on every push to `main`. There is no
  `gh-pages` branch; the workflow uploads the site directly.
- **Demo only:** the Pages entry module runs demo mode and nothing else. It never reads
  `config.toml` or `RECORDINGS_ARCHIVE`, and the build exports only `demo/archive/`.
  Everything in the site is public, including the source and the archive.
- **No FastAPI in the browser:** Shinylive bundles Starlette 0.38, which can't run the FastAPI
  layer and can't answer Range requests. So Pages serves the media itself, as static files at
  `media/<id>.<ext>`, and the app links to them through a configurable media base URL. The
  server keeps `/media/{id}`.
- **Older runtime:** the browser runs Python 3.12, with Pyodide's pydantic 2.10 and
  markdown-it-py 3.0. The Pages smoke test in CI guards against code that only works on the
  server's newer versions.
- **Cost:** the first visit downloads about 16 MB, which is cached afterwards. The server demo
  (`make demo`, Docker) stays the reference for bug reports. AI-generated demo outputs (a tiny
  model on the runner, or the Copilot CLI) are a possible later addition.

## 18. Pixeltable

- **Not inside the app.** The archive and catalog are designed so Pixeltable can read them
  directly. Media is read where it sits, as `ARCHIVE.md` already shows for
  `audio-router`.
- **Why the app can't share its database:** Pixeltable's deployment guide says only one
  process may write its embedded Postgres ("Two pods on the same `pgdata` corrupt the
  database").
- **Later:** a docs page with a worked notebook that rebuilds part of the pipeline in
  Pixeltable, for learning.

## 19. Moving over from `audio-router`

- **`audio-router` sync is broken right now.** Since about 2026-09-25, Plaud's list
  endpoint returns IDs as `of_<32 hex>`. `audio-router` validates them against
  `[0-9a-f]{32}` (`src/audio_router/archive/sources/plaud.py:39`, used at :166), so every
  row is rejected.
  - About 30 recordings are not in the archive, but nothing is lost because they are still
    on Plaud.
  - The newest vault transcript note is from Sep 14.
  - (Diagnosed by another Claude session on 2026-10-08. It made no API calls, and whether
    `files/{bare hex}` still works is untested.)
  - **Dan's decision (2026-10-08): no stopgap.** `audio-router` is left failing while
    `recordings` is built. Every Plaud recording is safe on the account, which is paid
    for the year. `recordings`' Plaud sync (§9.1) fills the gap when it arrives.
- **Copying code:**
  - **What:** the Plaud client, transcript parsing, the YouTube source and the output
    format, copied into `packages/core` with their tests. The copied tests are re-checked
    for real data, and any they hold is replaced with synthetic data (§16).
  - **Provenance:** each copied file records the `audio-router` commit it came from.
  - **License:** `audio-router` has no LICENSE. It is Dan's own code, and he relicenses the
    copied files under MIT.
  - **Before going public:** check the copied code and its test data for anything personal.
- **Order of the first real run** (added 2026-10-08):
  1. **Spikes:**
     - **the token** (§9.1)
     - **the ID form:** does the hex in `of_<hex>` equal `audio-router`'s bare ID, and are
       the audio bytes identical? The spike prints counts only.
     - **server facts:** its own mount, filesystem, free space and UID, and whether Docker
       starts before Tailscale
     - **NAS transport:** restic over SFTP, or rest-server with `--append-only`, with the
       SSH key and `known_hosts` as secrets (§5)
  2. **Deploy 2a** against an empty, initialised archive, and prove backup and restore.
  3. **Bring the source over:** rsync the `audio-router` archive to
     `/srv/recordings/import/` with a SHA manifest, read-only. Never copy from the NAS's
     Drive copy, which may be mid-sync. Run the import dry run; every catalog row must be
     accounted for.
  4. **Import:** take a `pre-import` snapshot, import, validate, then take a `post-import`
     snapshot.
  5. **Deploy 2b** with `schedule_minutes = 0`.
  6. **`sync --dry-run`:** "matched" should equal the Plaud rows `audio-router` holds, and
     "missing" should be about the newer recordings plus the held titles. Stop if not.
  7. **Finish:** rebaseline, import the missing recordings, then turn the schedule on.

  Then Dan tags the imported recordings in the grid (stage 3), and local processing runs as
  tags land (stage 4).

  **Until stage 3's first tag, the deployment is disposable:** rolling back means moving
  the archive aside and importing again. A parallel run with `audio-router` is pointless,
  because its sync is broken; its frozen archive is the yardstick.
- **Obsidian waits.** Nothing new reaches the vaults until `recordings` and the separate
  vault listener exist. That is deliberate: deciding which recordings reach which vault is
  part of why this app exists. Once the listener runs, `audio-router`'s launchd jobs are
  switched off.
- **Elsewhere:** fix the `local-ai` Phase 3 note (§2), and send `local-ai` the written
  request for `diarize` and `embed` (§3).

## 20. Build order

Each stage leaves a working, demonstrable app.

1. **Foundation:**
   - the uv workspace and the two packages
   - the archive format, its docs and schemas
   - the demo archive and Canned backend
   - Docker and CI
   - a read-only Library: list, player, synced transcript, the Notes tab (layout B), Plaud
     tab
   - the warm light and dark themes
2. **The archive and Plaud,** in two parts (split 2026-10-08, after the second design
   council). All of Dan's real recordings arrive in the Library here, read-only until stage
   3. Stage 2a is the first deployment on the homelab server, with the archive and state on
   its local disk at `/srv/recordings`. The order of the first real run is in §19.

   **2a. The archive moves in, protected.** No calls to Plaud.
   - the locked write path (`mutate`, `rev`, `flock`, fsync; §6.4), because this is the
     first stage that writes real files. Stage 1's `_merge_source` and the duplicate merge
     move onto it.
   - the `speakers` field's new shape (§7.6), set now while the real archive is still empty,
     and Plaud's segment fields: `speaker` holds `original_speaker`, then `speaker_name`
     and `embedding_key` (snake_case in our schema; Plaud's field is `embeddingKey`)
   - `state.db` (§6.8), plus the derived index, with `[index]` moved here: stage 1 globs
     every folder on each load
   - the normaliser and reconcile, as pure functions, so the import and the sync give
     identical Plaud outputs (§9.1.1)
   - a re-runnable `import-audio-router`, with its mapping table (§9.3)
   - **Compose:** a state volume; long-syntax bind mounts with `create_host_path: false`;
     log rotation; images tagged by commit SHA; `hostname`
   - **archive setup:** an explicit `recordings init` and the archive sentinel (§6.7)
   - **operations:**
     - restic backups, the NAS mirror (`--delay-updates`) and the restore test (§15.1)
     - the disk guard (§12.5)
     - ntfy alerts and the dead-man's switch (§14)
     - Status: backups and disk
   - cross-site protection on every route that writes (§15), moved here from stage 3
     because Import is a write
   - renaming `docker/deploy.example.env` to `docker/deploy.example.conf` (§5), because
     Dan's hook blocks reading `.env` names. This is a code change, not yet made.

   **2a is done when:**
   - the real archive is on the homelab server and readable in the Library
   - a restore passes `recordings validate`
   - a killed backup raises an alert

   **2b. Plaud flows in and keeps up.**
   - the Plaud client, with the `of_` fix
   - the token store and its refresh (§9.1)
   - a fake Plaud server for tests
   - **a `worker` container that runs only the scheduler.** All Plaud calls and writes
     happen there; UI buttons ask it to act, and a lock allows one sync at a time. The
     queue and worker of stage 4 build on it.
   - the compare, and importing missing recordings
   - auto-private, with the consent, hold and grace rules (§7.4, §9.1)
   - edit-sync (§9.1.1): the rolling sweep, the circuit breaker, the baseline after the
     import, Re-sync from Plaud, and `sync --full`
   - the Add page's Plaud panel and the Status sync panel

   **2b is done when:**
   - the dry run accounts for every listed ID
   - a speaker rename in Plaud shows up within the sweep window
   - `--full` exits 0
   - a 401 raises an alert

   **If 2b runs long,** edit-sync can ship after the compare and import. The snapshots are
   raw and reconcile can be re-run, so no migration is needed.
3. **Tagging, then people,** in two parts (split 2026-10-08):

   **3a. Tagging:**
   - editing `tags.yaml` and `recording.json`
   - drag, `T`, the grid and the tree, with Undo
   - the Private section and privacy calculation
   - outside-edit detection and `reindex`
   - the catalog, change log, JSON API (with the external view; §10) and CLI
   - the `<div>`-inside-`<button>` fix in `TranscriptTab` and `RecordingList` (§12.6a)

   **3b. People and speakers** (§7.6):
   - `people.yaml` and `people.private.yaml`
   - naming and corrections in the transcript
   - the People page, with the Plaud names to link
   - the People and date filters, and people in search (§12.6a)
   - the `people` and `speakers` CLI, including the patch format, `report` and `forget`
   - the **Insights** page (§12.6b), with the demo's sample year
   - the `voice: off` and `external_names: false` tag rules
4. **Processing:**
   - the queue and worker, with the tag gate and privacy re-check, built on stage 2b's
     worker
   - Spark Whisper
   - the note-type library and auto-pick
   - notes per model, with stale marks and Regenerate
   - the Claude backend
   - the `local` flag on each model entry, and its enforcement, including `doctor`'s check
     (§8.4)
   - the rules for names in prompts to external models (§7.6)
   - guard tests (§16): an unmarked backend is external; no audio goes to a non-local
     backend; `external_names` strips names

   This needs the Spark reachable over Tailscale (`local-ai` Phase 3). Until then it is
   built against Canned.
5. **Import your own audio:** browser upload (Zoom recordings and other files), URL import
   with yt-dlp (YouTube, talks), the watched folder, and video handling. This replaces the
   placeholder from stage 2.
6. **Speakers by voice** (§7.6), with pyannote on the Spark (Phase 3):
   - the two calls requested of `local-ai` Phase 3, `diarize` and `embed` (§3)
   - the voice store outside the archive (`state/voice.db`, never backed up or mirrored)
   - profiles from confirmed and Plaud-named time, seeded only past the timing check, with
     outlier review
   - calibrated thresholds, with `auto_threshold` off until calibration is good enough
   - the global rescore (`speaker_assignment`, `person_talk`)
   - retroactive sweeps, and the People page's Suggestions and Plaud-vs-home tabs
   - carry-over by overlap matrix
   - backfilling the archive
   - the Plaud-vs-home comparison (confusion, word-level error, DER, JER, identity), with
     *Fix in Plaud*, while the subscription lasts (until 2027-08-29)
   - the gold-set labelling screen (about 20 three-minute clips, about 10 hours of
     labelling)

**Later, separately:** suggested tags, the vault listener, the course-repo skill and the
Pixeltable notebook.

## 21. Decisions from the spec review (2026-10-08)

1. **Privacy** (§7.4): a tag is private if it is `private` or under `private/`, so
   `recording.json` alone decides. `AGENTS.md` states the rule and never lists recordings.
   The app needs no hook change; Dan's existing tool hooks are a backstop (§7.4; corrected
   2026-10-08).
2. **The outside-edit preview** (§11): kept for outside edits. UI actions apply
   immediately.
3. **One writer** (§3): the homelab server does all the work and is the only machine that writes.
   The Mac is a client (the browser, the API, `--remote`), and reading from elsewhere is
   fine.
4. **Course prompts** (§7.3): student-editable prompts stay in their course repo and are
   sent from the Mac with `--prompt-file`. Dan's own prompt collection and the
   `transcript_prompts` types live in `prompts/` in this repo. *(The move of
   `transcript_prompts` and the `private/` naming were proposed and taken as accepted. Dan
   to object if not.)*
5. **The homelab machine** (§3): the homelab server (Ubuntu, x86_64).
6. **The archive lives on the homelab server's own disk** (§3). the NAS gets hourly versioned
   restic backups plus a read-only mirror for the Mac (§15.1). The homelab server doesn't run
   Synology Drive.
7. **Components** (§13): shadcn/ui and Tailwind v4, set up with `--base radix`. They are
   shinyreact's skill default, as opposed to its website docs.
8. **One `_brand.yml`** with `{light, dark}` colours (§12.6).
9. **Docs before code** (§13): the shinyreact, brand.yml, shadcn and Tailwind docs and
   skills are consulted before writing against them.

## 22. Decisions on speakers, people, sync and insights (2026-10-08)

Dan's decisions, together with the design council's review: five reviewers covering
diarization and voice ML, the data model, privacy and law, product and UX, and sync and
reliability.

1. **Names now, voice later** (§7.6). People, naming, corrections and the filters come in
   stage 3, using Plaud's names. Voice matching comes in stage 6, with pyannote on the Spark.
2. **Who gets fingerprinted** (reworded 2026-10-08): every voice heard for at least about
   1.5 s in a recording that allows voice is fingerprinted, named or not. Profiles learn
   from everyone Dan names, everywhere except private recordings, which never feed
   profiles. There are two exceptions.
   - **A person can opt out:** *Recognise this voice* (`recognise: false`) means no voice
     data is kept for them.
   - **A tag can opt out:** tags marked `voice: off` (courses, clients) are never
     fingerprinted. `external_names: false` keeps names out of prompts to external models there.

   The tool doesn't decide what FIPPA or PIPA require; it makes the cautious setting one tag
   rule.
3. **Files hold decisions; the index derives inferences.** Plaud-name links, voice matches
   and carry-overs are computed and published in the catalog, never written into recording
   files. Rejections are stored.
4. **The web UI is the main way to name people.** The files are shaped for bulk edits by
   Claude:
   - readable slugs
   - one shape per label
   - spans in media time
   - a patch format with `expect`
   - `recordings validate`
5. **Retroactive matching:** a profile change sweeps every diarized recording, using stored
   fingerprints. Sweeps update the index only.
6. **Voice fingerprints never enter the archive.** They sit in a deletable derived store,
   outside backups and the mirror, and can be rebuilt from the audio.
7. **Plaud is the yardstick** until the subscription expires (2027-08-29).
   - Its named segments seed the voice profiles, with outliers sent for review.
   - It is the reference for the comparison (DER, JER, identity).
   - The final full re-sync is YouTrack DAN-15, due 2027-08-15.
   - A hand-labelled gold set of about 20 clips scores both systems against the truth.
8. **Private recordings use an allow-list.** Only agents and models running on Dan's own
   hardware (the DGX Spark, the homelab server or, since decision 15, the Mac) may read a
   private recording's renditions, source or speakers, or `people.private.yaml`.
   - Every other agent or model is blocked, whoever makes it: Claude, other hosted APIs,
     third-party agents.
   - A backend counts as local only when config marks it `local = true`.
   - Speakers are metadata.
9. **Forgetting a person scrubs everything** they appear in. That includes rewriting the
   Plaud snapshots and outputs that hold their name, as a logged exception to write-once.
   What remains in backups, the mirror and Plaud's cloud is reported.
10. **Edits made in Plaud sync down** from stage 2, old recordings included (§9.1.1).
    - The sync is a fetch-and-reconcile pair: a versioned normaliser, a hash for each part,
      and a rolling sweep with no freeze.
    - Removals wait for review.
11. **Stage 2 (2a since decision 25) gets the foundations** that can't be added later
    without migrating live files:
    - the locked write path (`mutate`, `rev`)
    - the new `speakers` shape
    - Plaud's speaker fields
    - the sync machinery: the normaliser and reconcile in 2a, the sweep in 2b
12. **The format change** happens now, inside `recordings-archive@1`, with every new key
    optional. Every demo recording has `"speakers": {}`, and no real archive exists yet, so
    no dual reader is needed.
13. **Insights page** (§12.6b): computed in Python with Shiny, and drawn in React. The demo
    shows a generated, labelled sample year.
14. **Speaker and voice work is local only, for every recording,** private or not.
    - Diarization, fingerprints, profiles and matching run only on `local = true` backends
      (the Spark).
    - External backends get text only, and only for non-private recordings.
    - An external audio service, if one is ever added, could transcribe non-private
      recordings. Its speaker output is never used.
    - Plaud is a source, not a backend. Its own diarization is kept; the rule governs what
      this app *sends*.

Decisions 15 onward come from the second design council (six reviewers: consistency, data
and sync, privacy and security, voice ML, product and UX, and stage-2 readiness) and Dan's
answers to its questions, 2026-10-08.

15. **"Local" includes the Mac** (§7.4). Local means every model that receives any content
    runs on Dan's own hardware: the DGX Spark, the homelab server or the Mac. Where the
    agent program runs doesn't matter; where its model runs does, so Claude Code is
    external everywhere.
    - The Mac's read-only mirror may hold private recordings.
    - On the Mac, keeping external agents out relies on `AGENTS.md`, Dan's tool hooks and
      the default external view. It is policy, not cryptography.
    - The app needs no hook change; Dan's existing hooks are a backstop (§21, decision 1).
16. **Untagged counts as private for external agents** until Dan tags it (§7.4, §10). Local
    models are unaffected. The cost is that Claude can't read the inbox to help triage it.
17. **Held titles are imported, tagged `private`** (§7.4, §9.1): titles starting `private`,
    or containing `interview:`. This matches "everything comes in".
18. **The consent list ("never mirror") carries over as a hard rule** (§9.1). Its IDs are
    never downloaded, and the list is read fail-closed.
19. **The grace period carries over** (§9.1): a placeholder title waits about 30 minutes
    for its real title before the privacy check runs.
20. **Alerts are push notifications through ntfy** (§14), with a dead-man's switch. Status
    keeps the pull view.
21. **Speakers nobody has named keep their per-turn voice fingerprints** (§7.6), because
    retroactive matching needs them. The forget, `recognise: false` and `voice: off` rules
    still apply.
22. **A person filter includes inferred matches by default,** with a split count: "Alex 42
    (8 unconfirmed)" (§12.6a).
23. **The demo's Insights year is the last 12 months, rolling,** generated from a fixed seed
    with invented titles, never derived from real data (§12.6b).
24. **Insights gets a tag filter,** reusing the Library's tag chips (§12.6b).
25. **Stage 2 splits into 2a and 2b, and stage 3 into 3a and 3b** (§20). 2a moves the
    archive in, protected, with no calls to Plaud; 2b brings Plaud in. The first real run
    follows the order in §19.
26. **State has three homes** (§6.8): archive files hold decisions, `state.db` holds
    operational state and is backed up, and `index.db` is derived.
27. **Only `mutate` changes `rev`,** and it refuses a stale file (§6.4). Every editable file
    has a lock and a `rev`.
28. **An archive is never set up implicitly** (§6.7). `recordings init` writes a sentinel,
    and every writer, the sync and the mirror refuse without it.
29. **Every outside reader goes through one external view** (§10), and the catalog is
    written in it (§6.6). The full view needs a local-scope token.
30. **Forget is complete** (§7.6): future fetches are scrubbed, the databases are vacuumed,
    the tombstone keeps only salted hashes, and you type the name to confirm.
31. **The Plaud token is a refreshed store, not a static secret** (§9.1).
32. **Times and dates follow ISO 8601** (§12): 24-hour times, weeks starting on Monday,
    and full dates as `2026-10-06`.
33. **Plaud-named speech is prime voice data**, treated as confirmed when profiles are
    seeded (§7.6). Plaud names match people ignoring case, and the person's properly cased
    name is shown.
34. **From the mockups** (§12.6a, §12.6b):
    - Medium matches show in the speaker strip with Confirm.
    - The picker lists the people Dan tags most often first.
    - People sits below Tags in the sidebar.
    - The reserved person `unknown` makes "Not Alex" work for a single line.
    - The open recording stays pinned to its assignment generation.
    - Insights' tag chips combine as *any of*.
    - The heatmap has six steps.
    - Confirmation dialogs are kept only for Forget and for making a private person public.
