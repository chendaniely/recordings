# The recordings archive format (`recordings-archive@1`)

One folder per recording. The folder and its media file are both named by the recording
ID: ISO 8601 basic local time with its UTC offset, then the first 8 hex of the media's
SHA-256, e.g. `20261006T140003-0700_3fa91c2e`. IDs never change.

```
<archive>/
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

## Rules

- **Only three files are edited:** `recording.json`, `my-notes.md` and `tags.yaml`. Each
  save writes a temporary file and renames it into place.
- **Work in progress:** writers assemble a new recording in `.tmp/` at the archive root and
  write temporary files named `.*.tmp`; readers and mirrors should ignore both.
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
