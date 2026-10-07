# recordings archive

This folder is a `recordings` archive: every recording, transcript and set of notes, as
plain files. The app writes it, and anything may read it: notebooks, Pixeltable, agents,
scripts.

- **What's where:** `FORMAT.md`.
- **Rules for agents:** `AGENTS.md`.
- **One recording:** `recordings/YYYY/MM/<id>/`, where the ID starts with the local date
  and time.
  - **Its transcript:** the newest `renditions/transcript-*.json`, unless `recording.json`
    names one in `chosen.transcript`.
  - **Its notes:** `renditions/notes-<note type>-*.json`, one per note type and model.
- **Checking the archive:** `recordings validate <archive>`.

These docs are written by the app from its own package, so they match the version that
wrote the files.
