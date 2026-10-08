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
renditions/<name>-<utc-stamp>.json
<!-- layout:end -->

- `recording.json`: identity and your edits. Its schema is `schemas/recording.schema.json`.
- `<id>.<ext>`: the original media, never modified.
- `<id>.audio.<ext>`: the audio track extracted from a video.
- `my-notes.md`: your own notes.
- `source/`: raw payloads exactly as each source returned them. Write-once.
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
  vanished, or names that reverted to "Speaker N", are held, not applied. The review that moves the marker is stage 3a, so until then they stay
  held.
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

## Rules

- **The editable files:** `recording.json`, `my-notes.md` and `tags.yaml`, and from stage 3b
  `people.yaml` and `people.private.yaml`. Each save writes a temporary file, fsyncs it, and
  renames it into place. The app changes the structured ones (`recording.json`, and later
  `tags.yaml` and the people files) only through `mutate`, under that file's lock. It only
  appends to `my-notes.md`, under the recording's lock: `my-notes.md` is free Markdown with no
  `rev`.
- **Work in progress:** writers assemble a new recording in `.tmp/` at the archive root and
  write temporary files named `.*.tmp`; readers and mirrors should ignore both.
- **The sentinel.** `archive.json` holds the archive's `format` and UUID, and is written once,
  by `recordings init`. Every writer, the backups and the mirror refuse to run without it, or when
  its UUID has changed, so an unmounted, empty folder can never pass for the archive. Never edit
  or copy it into another archive.
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
