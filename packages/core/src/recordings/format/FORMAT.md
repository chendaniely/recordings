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
- **A recording is private** if `recording.json` has a tag `private` or any tag under
  `private/`. A private recording's content never goes to a cloud model, including agents.
- **Stamps** are ISO 8601 basic UTC, for example `20261008T143512Z`.
