# Rules for agents working in this archive

1. **Read `README.md` and `FORMAT.md` first.**
2. **Privacy.** A recording is private when its `recording.json` has a
   `private` or `private/…` tag, in any capitalisation: any tag whose first folder is
   `private`, such as `Private` or `Private/health`. Treat every private recording as
   Spark-only, and never send its transcript or notes to a cloud model. You are one, so
   don't read them. The metadata file is the only source of truth for this, so there is no
   list to consult.
3. **Only edit `recording.json`, `my-notes.md` and `tags.yaml`.** Never edit media,
   `source/` or `renditions/`. They are write-once.
4. **Check edits** against `schemas/` (`recordings validate <archive>`).
5. **After a bulk edit, run `recordings reindex`** (available from stage 3). Jobs that the
   edit would start wait for approval in the app.
6. **Prefer the `recordings` CLI with `--json`** to editing files by hand.
