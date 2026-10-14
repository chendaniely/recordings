# Rules for agents working in this archive

0. **If this folder holds `MIRROR.md`, it is a read-only copy of the archive.** Edit, create and
   delete nothing here, and do not run `recordings reindex` or any writer on it: the next
   hourly run overwrites it, and the tools refuse it. Everything else below, privacy included,
   applies to the copy as to the archive.
1. **Read `README.md` and `FORMAT.md` first.**
2. **Privacy.** A recording is private when its `recording.json` has a
   `private` or `private/…` tag, in any capitalisation: any tag whose first folder is
   `private`, such as `Private` or `Private/health`. A private recording is for local
   models only: models running on the owner's own hardware (the DGX Spark, the homelab
   server or the owner's Mac). What counts is where the *model* runs, not where the agent
   program runs, so Claude Code is external on every machine. If you are any other agent or
   model (Claude, another hosted API, a third-party agent), never read a private
   recording's transcript, notes, `renditions/`, `source/` or speakers, never read
   `people.private.yaml` or `catalog/private/`, and never send them anywhere. **Treat an
   untagged recording as private too,** until the owner tags it. When unsure, treat yourself
   as external. The metadata file is the only source of truth for this, so there is no list
   to consult.
3. **Only edit `recording.json`, `my-notes.md` and `tags.yaml`** (and, from stage 3b, the people
   files). Never edit media, `source/` or `renditions/`: they are write-once. **Never change
   `rev`:** only the app's `mutate` changes it.
4. **Never edit, create or delete `archive.json`.** It is the archive's sentinel (see `FORMAT.md`).
5. **Check edits** against `schemas/` (`recordings validate <archive>`).
6. **After editing any file, run `recordings reindex`** on the homelab server, or ask the
   owner to run it. It accepts your edits as the base for the app's next write. Until then, the
   app refuses to change a file you edited, rather than overwrite your edit. (From stage 3a, jobs
   that the edit would start also wait for approval in the app.)
7. **Prefer the `recordings` CLI with `--json`** to editing files by hand.
8. **Never open, copy or index `.tmp/` or a file named `.*.tmp`.** They are writes in
   progress. A recording being assembled in `.tmp/` has its transcripts and source files before
   its `recording.json`, so nothing there yet says whether it is private: treat all of it as
   private.
