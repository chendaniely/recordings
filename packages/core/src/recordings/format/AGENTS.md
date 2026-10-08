# Rules for agents working in this archive

1. **Read `README.md` and `FORMAT.md` first.**
2. **Privacy.** A recording is private when its `recording.json` has a
   `private` or `private/…` tag, in any capitalisation: any tag whose first folder is
   `private`, such as `Private` or `Private/health`. A private recording is for local
   models only: those running on the owner's own hardware (the DGX Spark or the homelab
   server). If you are any other agent or model (Claude, another hosted API, a third-party
   agent), never read its transcript, notes, `renditions/`, `source/` or speakers, and never
   send them anywhere. When unsure, treat yourself as external. The metadata file is the
   only source of truth for this, so there is no list to consult.
3. **Only edit `recording.json`, `my-notes.md` and `tags.yaml`.** Never edit media,
   `source/` or `renditions/`. They are write-once.
4. **Check edits** against `schemas/` (`recordings validate <archive>`).
5. **After a bulk edit, run `recordings reindex`** (available from stage 3). Jobs that the
   edit would start wait for approval in the app.
6. **Prefer the `recordings` CLI with `--json`** to editing files by hand.
