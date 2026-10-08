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

- Every Plaud recording reaches the archive, and **Sync** shows at a glance what's missing
  from the Plaud account.
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
  tags** (§7.4).

## 3. Architecture

```
            ┌───────────────────────── homelab machine (Docker Compose) ─────────────────────────┐
 browser ──▶│ web: recordings-ui (FastAPI)                     worker: `recordings worker`       │
 (Tailscale)│   JSON API · uploads · media streaming            jobs: ingest · transcribe ·      │
            │   shinyreact UI (React client)                    speakers · pick · notes          │
            │            │  calls                                     │ calls                    │
            │            └──────────▶  recordings (core library)  ◀───┘                          │
            │                              │                │                                    │
            │                    SQLite index + queue   archive (local disk) ◀── source of truth │
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
- **The SQLite index and job queue** sit on the same local disk. They are fully derived,
  and `recordings reindex` rebuilds them.
- **Model backends sit behind one interface:** Spark (HTTP, OpenAI-style), Claude (the
  `claude` CLI) and Canned (demo and tests). §8.4 has the details.
- **The homelab server does all the work and is the only writer** (Dan, 2026-10-08).
  - **On the homelab server:** the app, the worker, Plaud sync, ffmpeg, and every call to the Spark
    and Claude. The Claude token exists only there.
  - **Why one writer:** if two machines changed the same `recording.json` at once, through
    a share or a two-way sync like Synology Drive, a change could be lost or a conflict copy
    made. `audio-router`'s host claim existed for the same reason. The core refuses to
    write on any machine other than the one named in config.
  - **The Mac is a client.** Dan uses the UI in a browser over Tailscale. Anything the Mac
    or an agent on it wants done goes to the app, through the JSON API or
    `recordings --remote <url> …`, and **runs on the homelab server**.
  - **Reading from the Mac** uses the read-only mirror on the NAS (§15.1), for example
    from a Pixeltable notebook. Nothing but the homelab server ever writes.
- **Dependencies on `local-ai` Phase 3:**
  - The Spark is reachable from the homelab machine over Tailscale, with this app's own
    key.
  - The pyannote wrapper exists.

  Until both are ready, development uses the Canned backend. Switching to the Spark later
  means changing a base URL.

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
  | Docker host settings: host archive path, config path, bind IP and port, UID/GID | `docker/deploy.env`, passed with `--env-file` | git-ignored. The template is `docker/deploy.example.env`. |
  | Secrets | Environment `NAME`, or `NAME_FILE` (Docker secrets under `/run/secrets`) | never |

  The templates avoid `.env*` names so Claude can read them, because Claude's rules forbid
  reading `.env` files. An environment variable beats `config.toml`. Demo mode reads none
  of the three.
- **Machine config** (`config.toml`) is mounted read-only into both containers at
  `/config/config.toml`. It holds:
  - the archive path
  - which host writes
  - the Spark base URL
  - named model entries (such as `spark:default` and `claude:opus`)
  - the writer host (the homelab server; §3)
  - extra prompt folders beyond `prompts/`
  - auto-private title patterns
  - the Plaud schedule
  - the watched folder path
  - the app base URL (used for links back into the app)
- **Secrets are passed by reference only**, as environment variables or Docker secrets.
  In Docker they are secret files, so they never show in `docker inspect`:
  - `RECORDINGS_PLAUD_TOKEN`, the Plaud token (stage 2)
  - `RECORDINGS_SPARK_API_KEY`, this app's llama-swap key (stage 4)
  - `CLAUDE_CODE_OAUTH_TOKEN`, from `claude setup-token`, lasting a year (stage 4)
  - `RESTIC_PASSWORD`, the backup repository key (stage 2). Keep a copy in your password
    manager, because without it the backups can't be read.
- **Docker host settings** (`docker/deploy.env`):
  - `RECORDINGS_ARCHIVE_HOST` (`/srv/recordings/archive` on the homelab server)
  - `RECORDINGS_CONFIG_HOST`
  - `RECORDINGS_BIND` (the homelab server's Tailscale IP)
  - `RECORDINGS_PORT`
  - `RECORDINGS_UID` / `RECORDINGS_GID` (the owner of `/srv/recordings`)

  Later stages add the index volume, the watched folder path, the secrets folder and the
  backup target.

  No secret appears in the image, the repo, the archive, logs or `--json` output. CI runs
  gitleaks.
- **Validation:** `recordings doctor` checks the config and reports which settings are
  missing. It checks secrets for presence only, never their values. Every config setting
  has a test proving it reaches the code that uses it (an `audio-router` lesson).

## 6. The archive

### 6.1 Layout

```
<archive>/
  README.md                    full guide: layout, naming, how to find things, what is editable
  AGENTS.md                    rules for agents (§6.7)
  FORMAT.md                    the format, versioned
  tags.yaml                    tag tree + rules (§7)
  schemas/                     recording.schema.json, tags.schema.json
  catalog/                     rebuilt automatically after changes
    README.md
    recordings.csv  recording_tags.csv  notes.csv
    changes.jsonl              append-only change log (§10)
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
  "title": "COURSE 101: Week 4",
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
strings. Matching against `audio-router`'s archive accepts either form.

### 6.4 Write rules

- **Write once:**
  - The media and `source/` are never overwritten.
  - Outputs are never overwritten. Running a step again adds a new file.
- **Editable files:** only `recording.json`, `my-notes.md`, `tags.yaml`, `people.yaml` and
  `people.private.yaml` (§7.6).
  - Each save writes a temporary file and renames it into place.
  - **Every write is an operation, not a whole document.** The core's
    `mutate(recording_id, op)` runs under a per-recording lock. The lock files live in
    `/srv/recordings/state`, outside the archive, so backups and the mirror never copy them.
  - Changes are detected by content hash, not mtime.
  - `recording.json` carries a monotonic `rev`. An outside edit made from a stale read shows
    up as `rev` going backwards. It is merged three ways, or sent to Needs attention.
  - Undo applies the inverse operation.
  - This is what lets the web process, the worker, the CLI and Claude's edits write safely
    side by side. A plain changed-since-read check can be passed by two writers at once.
- **One exception to write-once:** forgetting a person (§7.6) rewrites the Plaud snapshots
  and outputs that hold their name. Each rewrite is logged.
- **Recordings appear complete or not at all.** A new recording is assembled in a temporary
  folder and moved in only when complete.
- **Duplicates merge.** If identical bytes arrive again (same SHA-256), the new source
  reference is added to the existing recording.
- **Delete** moves the folder to `trash/`. The core never deletes permanently.

### 6.5 Outputs (renditions)

- **The contract extends `audio-router/rendition@1`.** Each output is a JSON file:
  `{schema, kind, engine, version, inputs, meta, payload}`.
  - **Name pattern:** `<kind>-<engine>-<version>-<UTC stamp>.json`. For notes,
    `<kind>` is `notes-<note type>`.
  - **`version` changes whenever the output would change:** a model revision, a
    quantization or a prompt hash. Otherwise a re-run would silently report the old answer
    as new.
- **Kinds:**
  - `transcript`: segments with start and end times, speaker and text
  - `speakers`: diarization: who spoke when, as segments only. Voice fingerprints never
    enter the archive (§7.6).
  - `speakers-compare`: Plaud's named speakers against the home-built result (§7.6). Its
    `inputs` pin both outputs and a hash of the assignments, so it goes stale when any of
    them changes.
- **Outputs are found by their `kind` field,** not by file name. A `speakers-*` glob also
  matches `speakers-compare`.
  - `pick`: the note-type auto-pick result
  - `notes-<type>`: Markdown, with prompt ID and hash, model, and the model that actually
    answered
- **Plaud's own transcript and notes** are written as outputs with engine `plaud`, taken
  from `source/`.
- **Which transcript is shown:** `chosen.transcript` if set, otherwise the newest
  successful one.
- **Stale is calculated, never stored:** a notes output is stale when its prompt hash or
  model version differs from the current one.

### 6.6 Catalog

The catalog is a set of flat tables, rebuilt after every change. Pandas, DuckDB and
Pixeltable can all load them directly.

- **`recordings.csv`:** id, recorded_at, duration, source kinds, title, has_transcript,
  note counts, stale counts, `private` (calculated, §7.4). Private recordings are listed
  like any other. The `private` column tells readers to keep their content away from any
  model or agent that isn't local (§7.4).
- **`recording_tags.csv`:** one row per recording and tag, with who applied it.
- **`notes.csv`:** one row per notes output: recording, note type, model, path, stale.
- **`people.csv`** and **`recording_speakers.csv`** (§7.6) hold the resolved speakers:
  - your decisions, Plaud's names and voice matches, each with `by`, `from` and `score`
  - a `private` column

  Pixeltable and the vault then never re-implement overlap, precedence or merge resolution.

### 6.7 Self-documentation

- **`README.md`** is the full guide for people and agents.
- **`AGENTS.md`** sets the rules for agents:
  - read README first
  - edit only the three editable files
  - check edits against the schemas
  - do not read transcripts or notes of private recordings (§7.4)
  - run `recordings reindex` after a bulk edit
  - prefer the `recordings` CLI with `--json`
- **Short `README.md` files** sit in `recordings/` and `catalog/`.
- **The docs can't drift.** The core writes these files from `format/` in the repo at
  startup. Every `recording.json` carries `format`. A test fails if the documented layout
  and what the writer produces disagree. `audio-router` learned this when its prose drifted
  until `schemas.py` replaced it.

## 7. Tags, note types and privacy

### 7.1 Tags

- **Folders:** `/` makes them, for example `school/course-101` or `work/clients`. The folder
  tree is for browsing only. **Rules do not inherit** from parent folders.
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
    data they have is deleted.
  - `external_names: false`: external models (§7.4) see "Speaker 2" instead of names.

  Dan sets both on course and client tags (2026-10-08):

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

- **Everything comes in.** There is no import refusal and no held status.
- **The rule:** a recording is **private** if its `recording.json` has the tag `private`
  or any tag under `private/` (for example `private/journal` or `private/personal`), in any
  capitalisation: `Private` and `PRIVATE/journal` count too. The rule errs towards private.
  One function decides it (`recordings.models.is_private_tag`).
  - **`recording.json` is the single source of truth.** Privacy needs no other file, no
    marker files and no lists to keep up to date (Dan, 2026-10-08).
  - **The UI's Private section is the `private/` folder.**
- **Local and external, decided by an allow-list:**
  - **Local** means the model or agent runs on Dan's own hardware (the DGX Spark or the
    homelab server) and sends nothing off it.
  - **Everything else is external:** Claude, other hosted APIs (OpenAI, Gemini and the
    like), and any third-party agent. That holds whoever builds it and however trusted it
    is.
  - **A backend is local only if config marks it `local = true`** (§8.4). Anything unmarked
    is external, so a newly added backend is blocked until Dan says otherwise.
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
  - Plaud recordings matching the title patterns in config are tagged `private`.
  - Recordings `audio-router` held come in tagged `private`.
- **Agents follow the same allow-list.** That covers Claude Code sessions, and any other
  agent or model not running on Dan's hardware. `AGENTS.md` states it once: "If a
  recording's `recording.json` has a `private` or `private/…` tag, an agent or model that
  isn't local never opens its `renditions/` or `source/`, never reads its `speakers`, and
  never reads `people.private.yaml`. Only agents driven by models on Dan's DGX Spark or
  homelab server may; that is what the private tier is for."
  - `AGENTS.md` never lists private recordings, because the metadata file is the only
    source.
  - There is no hook change.
  - Otherwise, external agents may read the recording folders and the catalog. Catalog rows
    for private recordings carry `private`, and external agents skip their content.

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

  These live in the SQLite index and are published in the catalog.

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
  recordings, and a person created from a private recording goes there by default. Local
  tools read it; external agents and models never do (§7.4).
- **Slugs:**
  - **Shape:** they match `^[a-z0-9]+(-[a-z0-9]+)*$`, ASCII-folded.
  - **Stable:** they never change when the display name does. Renaming a slug is a merge.
  - **Never reused:** a forgotten or merged person leaves a tombstone.
  - **Unique aliases:** aliases are unique across people, ignoring case.
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
  "spans": [ { "start": 41.2, "end": 58.0, "person": "alex-kim", "by": "you" } ]
}
```

- **`source`:** labels belong to one diarization. Plaud's "Speaker 1" is not pyannote's
  `SPEAKER_00`. The core maps labels onto any transcript by time overlap with `source`, once,
  so every view agrees.
- **`spans`:** media time in seconds. A span survives a new transcript, a Plaud re-sync or
  Whisper arriving. Line numbers wouldn't.
- **The corrections:**
  - **One person split into two labels:** point both labels at the same person. For two
    *unnamed* labels, the UI creates a placeholder person ("Unknown 1", `recognise: false`)
    to rename later.
  - **Two people in one label:** use a span.
  - **A wrong tag:** delete the assignment. To stop an inferred name coming back, add the
    person to `not`.
- **`plaud_name_seen`:** recorded when you override a Plaud name. The UI shows "Plaud now
  says X" only when Plaud's name changes from the one seen.
- **Precedence**, highest first:
  1. your span
  2. your label
  3. a Plaud-named span
  4. a Plaud-named label
  5. a voice match
  6. a name carried over
  7. a generic label

**Derived assignments** (index and catalog; recomputed whenever their inputs change):
- **From Plaud.** Plaud's transcript keeps, for every segment, `original_speaker` (the
  generic label, stored as the segment's `speaker`) and `speaker` (the name Dan gave,
  stored as `speaker_name`). In the current archive, 2,866 of 4,305 segments are named. Plaud
  can also rename a single line:
  - each label gets its majority name
  - minority names become Plaud-named spans
  - a name matching a person's `name` or `aliases` links to them
  - unknown or ambiguous names go on the People page's *To link* list
- **By voice and carried over:** stage 6, below.
- **Catalog:**
  - `people.csv` (id, name, aliases, recognise, kind, merged_into)
  - `recording_speakers.csv` (recording_id, person_id resolved, label, source, talk_ms, by,
    from, score, private)
- **Change events:** `changes.jsonl` gets "speakers changed" and "people changed" events.
  Events and logs carry recording IDs and counts, never person slugs, because slugs are
  names.

**Voice (stage 6, pyannote on the Spark):**
- **The diarization wrapper** (`local-ai` Phase 3) must return:
  - exclusive (non-overlapping) diarization
  - one embedding per non-overlapped turn of about 1.5 s or more
  - the model, revision, dimension and sample rate

  Uploads go as FLAC: a two-hour lecture as 16 kHz PCM is over the upload limit.
- **`speakers` outputs** (write-once, in the archive) hold who spoke when, and nothing else.
- **Voice fingerprints never enter the archive.**
  - **Where:** a derived store at `/srv/recordings/state/voice.db`, keyed by (diarization
    output, embedding model) and stored as float16.
  - **Not copied:** it is excluded from backups and from the read-only mirror. It can be
    rebuilt from the audio by a Spark job.
  - **Deleted** for a person with `recognise: false`, for a forgotten person, and for any
    recording whose tags say `voice: off` (§7.2).
- **Voice profiles** are built from confirmed time only: yours, plus Plaud-named segments.
  - **Plaud-named segments** are embedded directly: the audio is cut at Plaud's times,
    keeping segments of 2 s or more and skipping overlapped speech. Dan's Plaud labelling
    therefore seeds the profiles.
  - **Shape:** one exemplar per person per recording, weighted by duration.
  - **Sources:** profiles learn only from non-private recordings that allow voice.
  - **Outliers:** an exemplar that looks unlike the rest of that person's is shown to Dan as
    a probable wrong tag.
  - **Versions:** profiles and thresholds are kept per embedding-model version, and matching
    happens only within one version. A model upgrade means re-embedding and recalibrating.
- **Matching:**
  - **The score:** the mean of the top-k exemplar similarities, AS-normed against a cohort.
  - **Requirements:** a margin between the best and second-best match, at least about 8 s
    of net speech, and never the same person on two labels that speak at once.
  - **What's stored:** each score, with the model ID and the profile revision.
- **Thresholds** (`[speakers] auto_threshold`, `suggest_threshold`) are calibrated
  leave-one-recording-out. The trials are Plaud-named, plus your confirmations and
  rejections. `auto_threshold` is set at a false-accept rate of 1% or less. Statistics carry
  confidence intervals.
- **Retroactive sweeps:**
  - **The trigger:** a profile change, such as a confirmation, a merge or a removed tag.
  - **Debouncing:** one pending sweep per (person, profile revision); a newer one replaces
    it. Each label is decided across all profiles, and the best match wins, so the order
    sweeps run in doesn't matter.
  - **Effects:** sweeps update the index only, so they never write recording files.
  - **In the UI:** high matches show as auto tags. Medium matches go to *Suggestions*.
    Profile rebuilds wait while *Suggestions* is open, so the list doesn't reshuffle.
- **Carry-over to a newer diarization:**
  - **The method:** an overlap matrix on non-overlapped speech. Each new label needs purity
    of about 0.7 or more, and at least 50% of its time covered.
  - **Conflicts:** if two different people would land on one label, neither is assigned and
    the label is flagged.
  - **Determinism:** ties break by sorted label, and the algorithm version is recorded. The
    result is the same on every run.
  - **Your decisions:** when `source` changes, the core re-keys them the same way.
    Ambiguous ones go to review.
- **Backfill:** a one-off job diarizes every recording past the tag gate (§7.5) that allows
  voice, on the Spark only.

**Plaud as the yardstick (stage 6; the subscription expires 2027-08-29):**
- **The comparison:** a `speakers-compare` output pins both of its inputs and a hash of the
  assignments, and goes stale when any of them changes (§6.5).
- **What it reports:**
  - **Diarization agreement:** DER split into miss, false alarm and confusion, with a 0.5 s
    collar, scored inside Plaud's segments with overlap excluded. Plus JER.
  - **Identity on named time:** identification error rate, per-person precision and recall,
    and a coverage-against-precision curve over the threshold.
  - **Word-level speaker error** on Whisper's words, which avoids mismatched segment
    boundaries.
- **The timing check:** Plaud's segment times are checked against the media duration (§9.1).
  Recordings that fail are kept out of calibration.
- **Unnamed Plaud segments** count as unknown, not as negatives.
- **Resolving a disagreement:**
  - **"Plaud is right"** fixes our side.
  - **"Ours is right"** goes on the *Fix in Plaud* list, which is derived. The item clears
    once a re-sync shows the corrected name.
  - **Both answers** are logged as gold labels.
- **A gold set:** about 20 three-minute clips, hand-labelled once in a small labelling screen,
  scores both systems against the truth.
- **After the subscription:** Plaud's labels stay in the archive. The final full re-sync is
  YouTrack DAN-15, due 2027-08-15.

**Sensitive contexts (tag rules, §7.2):**
- **`voice: off`:** these recordings are never fingerprinted or auto-matched, and their voice
  data is deleted.
- **`external_names: false`:** external models see "Speaker 2" instead of names.
- **The strictest rule wins,** as with privacy. Dan sets these on course and client tags.
  Naming people by hand still works there.

**Names in prompts to external models:** confirmed names only (yours or Plaud's), never inferred ones,
and none at all on recordings where `external_names` is false.

**Bulk edits (Claude):**
- **Edit the files directly,** on the homelab server. The Mac's mirror is read-only.
- **Or use a patch file:** JSONL lines of the form `{recording, source, label | span,
  set | not, expect}`, applied with `recordings speakers apply --dry-run` and then
  `--apply`. It also works over `--remote`.
  - `expect` refuses a change if the file has moved on.
  - Confirmations queue one profile rebuild as a batch job (§11). The batch keeps a before
    and after record, so it can be reverted.
- **The people CLI:** `recordings people list|rename|alias|merge|report|forget`. It takes
  names, refuses ambiguous ones, and shows display names in dry runs.
- **`recordings validate` checks:**
  - unknown slugs
  - `merged_into` loops and dangling targets
  - duplicate aliases
  - labels missing from their `source`
  - spans outside the media
- **No label-wide bulk assignment.** "Assign SPEAKER_01 in every recording tagged X" is not
  offered: diarization labels are arbitrary in each recording.

**Forgetting a person (scrub everything):** `recordings people forget <id>` does five things.
1. It deletes their voice data and profile.
2. It removes every assignment, span and `not` that refers to them.
3. It replaces their entry with an anonymous tombstone.
4. It rewrites the Plaud snapshots in `source/`, and the Plaud transcript and notes outputs,
   to replace their name with the tombstone label. This is a logged exception to write-once,
   recorded in `changes.jsonl` and an audit file.
5. It reports what still holds the old data:
   - restic backups (up to 12 monthly snapshots; it prints the `restic rewrite` steps)
   - the NAS mirror (cleared on its next `rsync --delete`)
   - Plaud's own cloud

`recordings people report <id> --json` is the matching audit and export.

**Privacy:**
- **Speakers are metadata,** like tags.
- **What external agents and models may not do:** open a private recording's `renditions/` or `source/`,
  read its speakers, or read `people.private.yaml`.
- **Local agents and models** (on the Spark or the homelab server) may do all of those. That
  is what the private tier is for.
- **Enforcement:**
  - The catalog has a `private` column.
  - The app's backend layer enforces the rule in code for every backend not marked
    `local = true` (§8.4).
  - Outside agents are told by `AGENTS.md`, backed by Dan's tool hooks.
- **Test fixtures are synthetic,** never from the real archive. A local pre-commit hook
  checks staged files against the names in the people files.

## 8. Processing

### 8.1 Steps

Each step is a queued job. Each job writes one output.

1. **Ingest** (§9).
2. **Prepare:** for video, ffmpeg extracts the audio track.
3. **Transcribe:** Spark Whisper (`/v1/audio/transcriptions`, `verbose_json` with word
   times), with the tag vocabulary as `prompt`.
4. **Speakers:** Spark pyannote, merged into the transcript. Skipped until `local-ai`
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
  - Each backend entry in config says `local = true` only if it runs on Dan's hardware and
    sends nothing off it: the Spark, or a model on the homelab server.
  - Anything without that flag is external: Claude today, and any hosted API added later.
  - The privacy rule (§7.4) and the `external_names` rule (§7.2) check this flag, never a
    backend's name. A new backend is therefore external until it's explicitly marked.
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
- **Build order:** `plaud` and `audio_router` (the import) come first, in stage 2. `upload`
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
- **What's kept:** the full payload, including Dan's own in-app notes, goes into
  `source/`. Plaud IDs are opaque strings, and the `of_` form is accepted.

#### 9.1.1 Keeping up with edits made in Plaud (added 2026-10-08, revised after the design council)

Dan keeps editing in Plaud: renaming speakers, fixing titles, regenerating notes. Those
edits must flow down, for old recordings too. Plaud's API has no change signal: no
`updated_at` and no etag (`audio-router`'s Plaud client documents this). So a change can only
be found by fetching again and comparing.

- **Two steps, so a crash can't hide an update:**
  - **Fetch** writes a new `source/plaud-<stamp>.json` snapshot, but only if the normalised
    content changed. Nothing is overwritten.
  - **Reconcile** is a pure function: the latest snapshot, the people files and
    `recording.json` in, the Plaud outputs and the fields Plaud owns out. It is keyed by the
    hash of each part. It runs on every check and on `reindex`, so an update interrupted
    after the snapshot is finished next time.
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
  - **Unknown new fields** count as changes.
  - **Bumping the normaliser's version** recomputes both sides at compare time. A test checks
    that a version bump over the fixtures writes zero snapshots.
- **Per-part hashes:** title, transcript text, speaker names, each note tab, Dan's own notes,
  and everything else. The Sync screen can then say what changed. A new Plaud output is
  written only when its own part changed, so a title edit doesn't create a transcript
  version.
- **Rename or re-segmentation?** Each Plaud transcript gets a diarization fingerprint: a hash
  of each segment's start, end and `original_speaker`.
  - **The same fingerprint** means only names changed. `source` moves in place, and the
    Plaud-derived names follow.
  - **A different fingerprint** means a new diarization, so carry-over runs (§7.6).
- **What a change updates:**
  - **Transcript or notes:** a new version of that output. Old versions stay.
  - **Speaker names:** derived Plaud names follow (§7.6). Your own assignments stay, and a
    difference is shown only when Plaud's name changes from `plaud_name_seen`. A dismissed
    difference is stored per (recording, label, name).
  - **Title:** `title_by` and `plaud.title_seen` in `recording.json` record where the title
    came from. A Plaud title change applies unless you edited the title, and then the
    difference is shown.
  - **Removals are never applied on their own:** a name reverting to "Speaker N", or notes
    vanishing, waits for review. This is most likely around the subscription lapse.
  - **Editing an alias** re-runs the linking from the stored transcripts, with no re-fetch.
- **Partial and degraded responses:**
  - A snapshot with link errors is never used as the baseline, and nothing is built from it.
    The whole envelope is fetched again (links expire within 300 s), or the check is marked
    failed.
  - **A circuit breaker:** if more than about 5% of a sweep reports changes, snapshot writing
    stops and an alarm is raised. One new volatile field would otherwise mint thousands of
    permanent snapshots.
- **Cadence: a rolling sweep, with no freeze.**
  - **Check state** lives in a SQLite table that `reindex` keeps: last checked, last changed,
    failures. An unchanged check never writes `recording.json`.
  - **Every scheduled run** (20 min) checks:
    - recordings from the last `recheck_days` (14)
    - plus the K recordings checked longest ago, where K = N / (`full_sweep_days` × 72)

    So the whole account is covered every `full_sweep_days` (default 1 while relabelling;
    7 later).
  - **The interval adapts:** after each unchanged check it doubles, capped at
    `full_sweep_days`. That keeps the savings of `audio-router`'s freeze without its blind
    spot.
  - **Titles** come from the list endpoint on every run, which is cheap.
  - **Errors:**
    - 429 and Retry-After are respected, with exponential backoff.
    - A 401 stops the run and raises an alarm.
    - How the token is refreshed inside Docker is defined in the stage 2 plan.
  - **Limits:**
    - Only one sync runs at a time, and sweeps run in chunks so the worker isn't blocked.
    - An empty or shrunken listing never marks recordings "Only in the archive".
  - **On demand:** *Re-sync from Plaud* (one recording, in Details), and
    `recordings plaud sync --full` or the Sync screen button (everything).
  - **DAN-15's final pass:** `--full` exits non-zero unless every listed recording was
    checked successfully.
- **Keeping Plaud's speaker data intact (stage 2):**
  - Segments store `original_speaker` as `speaker`, Plaud's name as `speaker_name`, and
    Plaud's `embeddingKey`.
  - **A timing check** compares the last segment's end with the media duration. `audio-router`
    found timings at up to 135% of the file, after a trim in Plaud.
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
- **`recordings import-audio-router`:**
  - It runs as a dry run first. It reports counts, private recordings, and anything it
    can't place.
  - It reads `audio-router`'s archive through `ARCHIVE.md` and **never changes it**.
  - It copies the media and raw source data, and brings across the existing transcripts
    and summaries as outputs, keeping their engine names.
  - Held recordings come in tagged `private`.
  - Most imports arrive untagged, so their existing outputs are visible while they wait in
    the inbox.

## 10. How other tools read the archive

- **Files:** `FORMAT.md`, `README.md`, `AGENTS.md` and the JSON Schemas.
- **The catalog CSVs** (§6.6).
- **`catalog/changes.jsonl`:** an append-only change log, with one numbered line per event:
  - recording added
  - tags changed
  - transcript added
  - notes added
  - notes now stale
  - privacy changed
  - moved to trash

  A listener remembers the last number it handled. Lines carry IDs and event types, never
  titles or text.
- **JSON API** (`recordings-ui`):
  - list or filter recordings by tag, date or note type
  - fetch a recording's transcript and notes
  - `changes?since=N`
  - bulk tag changes with `dry_run`

  It returns private recordings like any other, with `private: true` in every response. The
  caller is responsible for honouring it, as `AGENTS.md` says. The app itself never sends a
  private recording to an external backend (§7.4).
- **CLI:** `recordings …` with `--json`. Agents use this first.
- **Links:** every recording has a stable link, `<base>/r/<id>`.

## 11. Editing files by hand

- **Changes are noticed** by watching the archive, a periodic rescan (file watching misses
  changes made over network shares), and `recordings reindex`.
- **Schemas:** `recording.json` and `tags.yaml` reference their JSON Schemas, so editors
  and agents can check edits. An invalid file is never overwritten or "fixed". The index
  keeps the last good version, and the error is shown with its file and line.
- **Outside edits are previewed before anything runs.** If an outside edit would start
  jobs, they wait for approval, for example "37 notes, 2 Claude". Edits made in the UI
  apply immediately. *(Dan asked for UI actions to apply immediately. This preview is kept
  for outside edits only, as Dan confirmed on 2026-10-08.)*
  - **Batches:** held jobs are grouped into **batches**, one per rescan or `reindex` that
    found changes. Each batch records:
    - what changed, for example "Tags added: school/course-101 on 40 recordings"
    - when and how the change was detected
    - the jobs it would start, by kind and backend
  - **Approving them** is done in bulk from the Status page (§12.5), or with
    `recordings pending list | run | run --no-claude | dismiss` (each with `--json` and
    batch IDs).
  - **Dismiss** runs nothing and keeps the edits. Those recordings show their missing
    outputs with a **Process** button.
- **Bulk tag changes** are also available through the CLI and API, with a dry run.

## 12. Interface

### 12.1 Library (home)

- **Left sidebar:**
  - All, **Untagged** (the inbox, "N waiting for a tag") and Needs attention (failed or
    stale)
  - the tag tree with counts
  - a separate **Private** section with a lock icon
  - note types
- **Middle:** the recording list, newest first. It has search across titles, transcripts
  and notes (SQLite FTS5). Each row has tag chips and a status: transcribing, stale, failed
  or private.
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
    - **Details:** sources, every output and its backend (including what went to Claude),
      and jobs.

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
- **No confirmation dialogs** in the UI. Every action shows an Undo toast.

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
    date (§9.1)
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
- **Waiting for approval** (batch processing of the outside-edit previews, §11):
  - **An orange badge in the top bar**, visible on every page, shows "N waiting · M jobs".
    Clicking it opens this panel.
  - **Each batch is a card** showing what changed, when it was detected, and chips for the
    jobs it would start. Jobs for external models are blue so they stand out. Private recordings show
    "🔒 External models skipped: private" and never list such jobs.
  - **Selecting:** tick whole batches, or **All**. Expand a batch to untick single
    recordings.
  - **A sticky action bar** shows "N batches · M jobs, K with Claude", with three buttons:
    - **Run**
    - **Run without Claude**: Spark jobs only. The Claude notes can still be generated
      later.
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
    - **Inferred (auto):** dashed, as auto tags are.
    - **Unnamed:** plain muted text ("Speaker 2").

    The transcript shows no scores.
  - **The picker:** clicking a chip, or pressing `P` on the focused line, opens it.
    - **"In this recording":** this recording's labels come first, named or not. Picking an
      unnamed label merges into a placeholder ("Unknown 1").
    - **Then people,** with search. Enter picks an existing match before "Create *Name*"
      (the same fix as the tag picker).
    - **Keys:** 1–9 pick people already in this recording.
    - **Scope** is a two-way toggle with counts: **Every Speaker 2 line (42)** | **Only
      3:12**.
    - **Range fixes:** Shift-click selects a range of lines, Finder-style, and the scope then
      reads **These 7 lines**.
    - **One verb, chosen by the tag's state:** "Not Alex" on an inferred tag (it removes the
      tag, records `not`, and offers the next-best match); "Unassign" on yours.
    - **It closes on choice.** A label has one person.
  - **The toast:** "Speaker 2 → Alex Kim · 42 lines · Undo". After a voice sweep it adds
    "…and found Alex in 23 older recordings · Review". Undo withdraws those too.
  - **A sweep never silently changes the open recording.** A banner offers "Voice matching
    updated 2 labels · Review".
  - **Each line** is restructured so a chip button no longer sits inside the line's button,
    which is invalid HTML.
- **The speaker strip** above the transcript has one row per label:
  - talk time, the person, and the confidence (High or Medium, with the number on hover)
  - Confirm and Reject
  - a ▶ button that plays a 5-second sample

  Clicking a label highlights its lines and adds ◀ ▶ to jump between them (keys `[` `]`).
  Speaker bands run under the player's scrubber.
- **The People page** (top navigation, beside Library and Tags; its badge counts what needs
  you). Tabs:
  - **People:** one row per person, with aliases, recordings, talk time, last heard, a "who
    is this" clip, and the *Recognise this voice* switch. A person's page lists their
    recordings, plus *Merge with…*, aliases and *Forget*.
  - **To link (N):** Plaud names not yet linked.
    - Each name shows a count, a ▶ and a suggested link.
    - Multi-select, then "One person from these 3": the first becomes the name, the rest
      aliases.
    - "Not a person" or "Group" (for "Students" and the like).
    - Plaud's generic "Speaker N" names are hidden.
  - **Suggestions (N):** grouped by person, with the person's reference clip pinned at the
    top and sorted by score. Space plays, Y/N decides and moves to the next clip, and
    "Accept all High" clears the top.
  - **Fix in Plaud (N).**
  - **Plaud vs. home** statistics, once stage 6 arrives.
- **Finding recordings (Library):**
  - **Combining filters:** AND across kinds (tags, people, date, search, Untagged). Inside
    People, *all of* (the default) or *any of*. Untagged plus a person is allowed, and is
    handy for triage.
  - **Every active filter is a chip** above the list, search text and Untagged included.
    *Clear* clears them all. The all/any toggle sits inside the People chip: "Dan + Alex ·
    all ▾".
  - **The People facet** in the sidebar lists people with counts, inferred ones split out:
    "Alex 42 (8 unconfirmed)". Click to filter; Cmd/Ctrl-click adds more people. On a phone
    it collapses.
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
        a blue tint, and today has a ring.
      - Days with recordings carry a muted dot, which turns white on selected days.
      - No orange: that marks the line playing now.
    - **The month caption** opens a month and year grid with counts, for jumping back years.
    - **The footer** reads "Oct 1 – 15 · 9 recordings", with Clear.
    - **Keys:** arrows, PgUp and PgDn, and Shift to extend the range.
    - **On a phone:** a bottom sheet with one month, and the presets as a scrolling row of
      chips.
    - **Ranges** use each recording's local date (`recorded_at` with its offset).
  - **Empty states** suggest ways to widen, with counts: "Any of: 12 · All dates: 3 · Clear".

### 12.6b Insights (added 2026-10-08)

A dashboard of the archive. It doubles as a showcase of the architecture: every number is
computed in Python on the Shiny side (`@reactive_output("insights")`) and drawn by React, and
it recalculates as the controls change.

- **Controls:** the same date control as the Library, and an hours or count switch.
- **Cards:**
  - recordings, total hours, and days with a recording
  - words transcribed, notes written, and average length
  - longest streak and busiest month
  - people met (from stage 3)
- **A year heatmap**, in the style of GitHub's: one square per day, shaded by hours. Hover
  shows "Tue Oct 6 · 3 recordings · 2.4 h". Clicking a day opens the Library filtered to it.
- **A punch card:** weekday by hour of day, with a callout ("Busiest: Tuesdays 2–4 pm"). It
  says *busiest*, not *most productive*: recording time measures meeting load.
- **Model usage:** words per transcript engine (Plaud against Whisper), and notes and words
  per notes model.
- **Trends:** hours per month, and the average per week.
- **Speakers** (once stage 3 is in place): who you meet most, talk time per person, and your
  share of talk time in lectures against meetings.
- **Building it:** charts use shadcn's chart components (Recharts, with the brand tokens, so
  light and dark both work). The heatmap and punch card are plain SVG. The Python is plain
  standard library, so it runs on the Pages demo (Pyodide) too.
- **Private recordings** count in the totals. Their titles never appear here.
- **The demo** ships a generated, clearly labelled sample year: about 300 recording records
  with no audio. It appears only on Insights, so the Library keeps its 4 real recordings.

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
  posting to the app's real host name. So every route that changes data (from stage 3:
  tags, people, merges, *Recognise*, *Forget*):
  - checks `Origin` or `Sec-Fetch-Site`
  - requires a JSON body

  A tailnet ACL limits the app's port to Dan's devices.
- **Containers:** both run as non-root users. Secrets follow §5.

### 15.1 Backups to the NAS (from stage 2, the first stage that writes)

- **A backup, not a sync.** A two-way sync, like today's Synology Drive folder, faithfully
  copies a bad write or a deletion to the NAS. Versioned backups keep history, so mistakes
  can be rolled back.
- **The tool is restic,** encrypted and deduplicated. It runs on the homelab server as a `backup`
  service in the same Docker Compose project and writes only to its own repository on
  the NAS.
  - **Transport:** SFTP to the NAS is preferred. An NFS mount is the fallback; the
    homelab server can mount a NAS share over NFS. Either way, restic writes
    only its own repository, with its own locking, and the app never writes to the NAS.
- **What is backed up:**
  - the archive
  - `config.toml`
  - `docker/deploy.env`
  - a consistent copy of the SQLite index, taken with SQLite's own backup command first
  - **not** the secrets folder: those live in your password manager
- **When, and for how long:** hourly. Keep 24 hourly, 14 daily, 8 weekly and 12 monthly
  snapshots.
- **Checks:**
  - a weekly `restic check`
  - a monthly automatic restore of the newest snapshot into a scratch folder, which must
    pass `recordings validate`, because a backup that has never been restored isn't proven
  - the results appear on the Status page (§12.5)
- **A read-only mirror for the Mac:** an hourly one-way `rsync` of the archive to a share
  on the NAS. The Mac and Pixeltable read that, either mounted read-only or synced down
  one-way with Synology Drive on the Mac. **The homelab server itself doesn't run Synology Drive.**
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
  - a private recording never reaches the Claude backend, including when the private tag
    is added after the job was queued
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
  - every start uses a fresh copy of the demo archive
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
- **Insights in the demo** use a generated, clearly labelled sample year with no audio
  (§12.6b).

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
    format, copied into `packages/core` with their tests.
  - **Provenance:** each copied file records the `audio-router` commit it came from.
  - **Before going public:** check the copied code and its test data for anything personal.
- **Order:**
  1. `import-audio-router --dry-run`, then the real import.
  2. Dan tags the imported recordings in the grid.
  3. Spark processing runs as tags land.
- **Obsidian waits.** Nothing new reaches the vaults until `recordings` and the separate
  vault listener exist. That is deliberate: deciding which recordings reach which vault is
  part of why this app exists. Once the listener runs, `audio-router`'s launchd jobs are
  switched off.
- **Elsewhere:** fix the `local-ai` Phase 3 note (§2).

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
2. **Plaud:**
   - the `audio-router` import
   - Plaud sync with its compare (Missing, Updated, Only in archive) and the scheduled run
   - keeping up with edits made in Plaud (§9.1.1):
     - the fetch and reconcile split
     - the versioned normaliser, with a hash for each part
     - the diarization fingerprint
     - the rolling sweep, with check state in SQLite
     - the circuit breaker and the listing floor
     - Re-sync from Plaud, and `sync --full`
     - the Status sync panel
     - a fake Plaud server for tests
   - the locked write path (`mutate`, `rev`; §6.4), because this is the first stage that
     writes real files
   - the `speakers` field's new shape (§7.6), set now while the real archive is still empty
   - Plaud's speaker data kept (`speaker` as the label, `speaker_name`, `embeddingKey`, and
     the timing check), and the names shown in the transcript (read-only)
   - the Add page's Plaud panel

   All of Dan's real recordings arrive in the Library here, read-only until stage 3.
   This stage is the first deployment on the homelab server:
   - the archive and SQLite on its local disk, at `/srv/recordings`
   - the Plaud token as a secret
   - **backups to the NAS** (§15.1) and the disk-space guard (§12.5), because this is
     the first stage that writes
3. **Tagging:**
   - editing `tags.yaml` and `recording.json`
   - drag, `T`, the grid and the tree, with Undo
   - the Private section and privacy calculation
   - outside-edit detection and `reindex`
   - the catalog, change log, JSON API and CLI
   - **people and speakers** (§7.6):
     - `people.yaml`
     - naming and corrections in the transcript
     - the People page, with the Plaud names to link
     - the People and date filters, and people in search (§12.6a)
     - the `people` and `speakers` CLI, including the patch format, `report` and `forget`
     - the `voice: off` and `external_names: false` tag rules
   - the **Insights** page (§12.6b), with the demo's sample year
   - cross-site protection on every route that writes (§15)
4. **Processing:**
   - the queue and worker, with the tag gate and privacy re-check
   - Spark Whisper
   - the note-type library and auto-pick
   - notes per model, with stale marks and Regenerate
   - the Claude backend

   This needs the Spark reachable over Tailscale (`local-ai` Phase 3). Until then it is
   built against Canned.
5. **Import your own audio:** browser upload (Zoom recordings and other files), URL import
   with yt-dlp (YouTube, talks), the watched folder, and video handling. This replaces the
   placeholder from stage 2.
6. **Speakers by voice** (§7.6), with pyannote on the Spark (Phase 3):
   - the wrapper requirements on `local-ai` Phase 3:
     - exclusive diarization
     - an embedding for each turn
     - model revisions
     - FLAC upload
   - the voice store outside the archive (`state/voice.db`, never backed up or mirrored)
   - profiles from confirmed and Plaud-named time, with outlier review
   - calibrated thresholds
   - retroactive sweeps and the Suggestions review
   - carry-over by overlap matrix
   - backfilling the archive
   - the Plaud-vs-home comparison (DER, JER, identity), with *Fix in Plaud*, while the
     subscription lasts (until 2027-08-29)
   - the gold-set labelling screen (about 20 three-minute clips)

**Later, separately:** suggested tags, the vault listener, the course-repo skill and the
Pixeltable notebook.

## 21. Decisions from the spec review (2026-10-08)

1. **Privacy** (§7.4): a tag is private if it is `private` or under `private/`, so
   `recording.json` alone decides. `AGENTS.md` states the rule and never lists recordings.
   There is no hook change.
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
5. **The homelab machine** (§20): the homelab server (Ubuntu, x86_64).
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
2. **Who gets fingerprinted:** everyone Dan names, everywhere, with two exceptions.
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
7. **Plaud is the yardstick** until the subscription expires (2027-08-29 22:01).
   - Its named segments seed the voice profiles, with outliers sent for review.
   - It is the reference for the comparison (DER, JER, identity).
   - The final full re-sync is YouTrack DAN-15, due 2027-08-15.
   - A hand-labelled gold set of about 20 clips scores both systems against the truth.
8. **Private recordings use an allow-list.** Only agents and models running on Dan's own
   hardware (the DGX Spark or the homelab server) may read a private recording's renditions,
   source or speakers, or `people.private.yaml`.
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
11. **Stage 2 gets the foundations** that can't be added later without migrating live files:
    - the locked write path (`mutate`, `rev`)
    - the new `speakers` shape
    - Plaud's speaker fields
    - the sync machinery
12. **The format change** happens now, inside `recordings-archive@1`, with every new key
    optional. Every demo recording has `"speakers": {}`, and no real archive exists yet, so
    no dual reader is needed.
13. **Insights page** (§12.6b): computed in Python with Shiny, and drawn in React. The demo
    shows a generated, labelled sample year.
