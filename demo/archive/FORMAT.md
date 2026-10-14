# The recordings archive format (`recordings-archive@1`)

One folder per recording. The folder and its media file are both named by the recording
ID: ISO 8601 basic local time with its UTC offset, then the first 8 hex of the media's
SHA-256, e.g. `20261006T140003-0700_3fa91c2e`. IDs never change.

```
<archive>/
  archive.json                   the sentinel: the archive's UUID, written once by `recordings init`
  README.md  AGENTS.md  FORMAT.md
  schemas/recording.schema.json  schemas/rendition.schema.json
  catalog/                       flat tables, rebuilt by the app (from stage 3)
  recordings/YYYY/MM/<id>/       one folder per recording (below)
  trash/                         deleted recording folders, moved here whole
```

## Inside a recording folder

The files a recording folder may contain. `tests/test_selfdoc.py` fails if the writer ever
produces a file that is not listed here.

<!-- layout:begin -->
recording.json
<id>.<ext>
<id>.audio.<ext>
my-notes.md
source/<name>-<utc-stamp>.json
source/<name>-<utc-stamp>.txt
renditions/<name>-<utc-stamp>.json
<!-- layout:end -->

- `recording.json`: identity and your edits. Its schema is `schemas/recording.schema.json`.
- `<id>.<ext>`: the original media, never modified.
- `<id>.audio.<ext>`: the audio track extracted from a video.
- `my-notes.md`: your own notes.
- `source/`: raw payloads exactly as each source returned them. Write-once. A `.json` file is a
  JSON payload (a Plaud snapshot, a Pocket payload, or speaker names typed in `audio-router`). A
  `.txt` file is a prose transcript kept as the source gave it (a Google Recorder export). Both
  hold what was said, so the privacy rule below covers them.
- `renditions/`: outputs (transcripts, speakers, note-type picks, notes). Write-once.
  Re-running a step adds a file. The name is `<kind>-<engine>-<version>-<UTC stamp>.json`,
  where `<kind>` is `notes-<note type>` for notes. The schema is
  `schemas/rendition.schema.json`.

## recording.json

The schema is `schemas/recording.schema.json`. Keys added in stage 2a are optional, so older
files read unchanged.

- `rev`: the file's revision. It goes up by one with every change the app makes, and **only
  `mutate`** changes it. Edit the file by hand and leave `rev` alone: the app notices an outside
  edit by its content, not by `rev`. A file without `rev` is at revision 0.
- `title_by`: who set `title`: `you`, or the source kind that supplied it (`plaud`, `recorder` or
  `pocket`). When Plaud's title changes, the title is replaced only while `title_by` is `plaud` or
  absent; for any other value Plaud's new title is reported as a difference instead (showing and
  dismissing it is stage 3a).
- `plaud.title_seen`: the Plaud title last seen or dismissed. `plaud.acknowledged_up_to`: the
  newest `source/` snapshot whose removals have been reviewed. Until then, a Plaud note that
  vanished, or names that reverted to "Speaker N", are held, not applied. The review that
  moves the marker is stage 3a, so until then they stay held.
- `tags`: each is `{"tag": …, "by": …}`. `by` is `you`, or `auto` for a `private` tag added
  because a Plaud title matched the auto-private patterns. The patterns run on a recording's
  first Plaud snapshot, and again whenever a Plaud title isn't the one in `plaud.title_seen`.
  They only ever add the tag: one you remove stays removed until then.
- `sources`: one entry per raw payload in `source/`, and one per ID the recording is known by (an
  entry with no `raw` is only a reference). `kind` is `plaud`, `recorder`, `pocket` or
  `audio_router`. An `audio_router` entry's `ref` is the row's `audio-router` URI, such as
  `plaud/<id>`, which is how a re-run of the import recognises the recording. Each entry has the
  source's own `ref`, `added_at`, `fetched_at` (when the source returned it) and the file's
  `sha256`. Snapshots are ordered by `fetched_at`, then by their place in this list, never by
  file name.
- `speakers`: decisions only, never inferences. `source` is the output the labels belong to.
  `labels` maps a label to `{"person": "<slug>", "by": "you"}`, optionally with
  `plaud_name_seen`, or to `{"not": ["<slug>", …]}`. `spans` are `{start, end, person, by}` in
  media seconds. The reserved person `unknown` means "not anyone known" and overrides every
  inferred name for that time. Slugs match `^[a-z0-9]+(-[a-z0-9]+)*$`.

## Transcript segments

Each segment has `start` and `end` (seconds), `text`, optional `words`, and `speaker`: the
generic diarization label (for Plaud, its `original_speaker`). A Plaud transcript also carries
`speaker_name`, the name given in Plaud, and `embedding_key`, Plaud's `embeddingKey`.
`speaker_name` is set for a real name, and for any name that differs from the label. It is left
out when Plaud's name is just the label. A Plaud row with no `original_speaker` has its name as
`speaker` too, so a named row then has `speaker_name` equal to `speaker`.

## Plaud's outputs

Plaud's own transcript and notes are outputs with engine `plaud`, rebuilt from `source/`. Each
records its snapshot in `inputs.source` and the hash of what it carries in
`inputs.part_sha256`, so a difference it can't show is never a new file, and its `version`
(`plaud@<n>`) names the normaliser that produced it.

- `meta.timing`, on a transcript: Plaud's timing checked against the media, by `rule`
  (`timing@1`). `plaud_duration_ms` is Plaud's duration, `last_segment_end_ms` the latest end
  among its segments, and `media_duration_ms` the media's. `ok` is `false` when Plaud's duration
  differs from the media's, or its last segment ends after the media does, by more than 1 s or
  1% of the media (whichever is larger): a sign of a trim in Plaud. A missing `ok` means the
  check didn't run (the media's duration is unknown, or there was nothing to compare), never
  that it passed. The verdict is as of the snapshot in `inputs.source`.
- `meta.diarization`, on a transcript: a hash of each segment's start and end in Plaud's
  milliseconds, and its generic label. The same value means only names changed. `null` means
  no fingerprint could be taken: read it as a new diarization, never as unchanged.
- `meta.untimed_segments`, on a transcript: how many rows had no usable timing. Each is kept,
  with `start` and `end` both at the previous segment's end.
- On a notes output, `meta.data_type` is Plaud's block type, and `payload.markdown` is
  rendered from the block as Plaud delivered it, so its links stay as Plaud wrote them. A
  highlight's picture link is presigned, and expires within minutes.

## Outputs imported from `audio-router`, and Pocket's

- An output imported from `audio-router` keeps its engine and version. Its `meta` carries
  `imported_from` (`audio-router`), `file` (its original file name) and `audio_router_meta`
  (its original `meta`, as it was). Its `inputs` keep the original values, as strings.
  `meta.untimed_segments` and `meta.untimed_words` count rows that had no time; each is kept
  at the previous row's end, as Plaud's untimed segments are. Each file is imported once.
- `audio-router`'s `words` and `merged` are `transcript` outputs, `turns` is a `speakers` output
  (`payload.turns`, and `payload.overlap_turns`), and `summary` is a `notes` output with note
  type `audio-router-summary`. Its Plaud outputs are not copied: reconcile rebuilds them. The
  speaker names you typed in `audio-router` are kept as they were in
  `source/audio_router-<stamp>.json`, never as an output. Voice fingerprints and embeddings are never kept.
- Pocket's transcript and summaries are outputs with engine `pocket` and `version`
  `pocket@<n>`, built from `source/`. Each records its snapshot in `inputs.source` and the hash
  of what it carries in `inputs.part_sha256`, so an unchanged output is not written again. A
  transcript's `meta.time_unit` is `s` or `ms`: the times in `source/` were read as that, and
  `payload` is always in seconds. The note types are `pocket-summary`, then `pocket-summary-2`,
  and so on.

## Rules

- **The editable files:** `recording.json`, `my-notes.md` and `tags.yaml`, and from stage 3b
  `people.yaml` and `people.private.yaml`. Each save writes a temporary file, fsyncs it, and
  renames it into place. The app changes the structured ones (`recording.json`, and later
  `tags.yaml` and the people files) only through `mutate`, under that file's lock. It only
  appends to `my-notes.md`, under the recording's lock: `my-notes.md` is free Markdown with no
  `rev`.
- **Work in progress:** writers assemble a new recording in `.tmp/<id>.<hex>/` at the archive root,
  and write temporary files named `.*.tmp`. Readers and mirrors skip both.
- **The sentinel.** `archive.json` holds the archive's `format` and UUID, and is written once,
  by `recordings init`. Every writer, the backups and the mirror refuse to run without it, or when
  its UUID has changed, so an unmounted, empty folder can never pass for the archive. Never edit
  or copy it into another archive. (The NAS mirror holds the one copy of it that exists: see the
  next item.)
- **The mirror.** A folder holding `MIRROR.md` is the read-only mirror of an archive, not the
  archive. `MIRROR.md` names the source archive's UUID (`archive-uuid: <uuid>`), and the mirror
  refuses a target that is not empty and carries no mark of this archive. Everything else in
  it is a copy: the same files, including this one and `archive.json`, updated every hour and
  emptied of anything the archive no longer holds. Read it freely; change nothing in it. Writers
  and `recordings init --adopt` refuse it. The copy holds private recordings too, so the privacy
  rule below applies to it unchanged. Work in the app, or on the homelab server's archive.
- **Versions:** every `recording.json` names its format in `format`
  (`recordings-archive@1`, major version 1). A reader must refuse a major version it
  doesn't know.
- **A recording is private** when any tag in its `recording.json` has `private` as its first
  folder, in any capitalisation: `private`, `private/journal`, `Private/health` and so on,
  but not `privateer` or `notes/private`. Private recordings are for local models only,
  meaning models running on the owner's own hardware (where the model runs counts, not
  where the agent program runs). No other agent or model reads or receives their
  transcripts, notes or speakers. External agents treat untagged recordings as private too,
  until the owner tags them.
- **Stamps** are ISO 8601 basic UTC, for example `20261008T143512Z`. When two outputs would
  get the same name, the later one gets a `-2`, `-3`, … suffix before `.json`, and nothing
  is ever overwritten.
