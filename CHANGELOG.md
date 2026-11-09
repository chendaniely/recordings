# Changelog

What changed, newest first, for the one person who uses this app. The specs in
`docs/superpowers/specs/` say why; the user guide (`docs/user-guide.md`) says how.

## Stage 2b: Plaud flows in and keeps up (2026-11-02)

Built on branch `stage-2b`, not yet deployed. Plaud's API details are the build's guesses (the
spike sessions were skipped), each listed with how you would notice it's wrong in
`docs/superpowers/plans/2026-11-01-stage-2b-guesses.md`. Setting it up, dry run first, is
`docs/setup.md`.

### What you can do now

- **Leave Plaud to it.** Once the first catch-up has run, the server lists your Plaud account every
  20 minutes and brings in what is new, by itself: audio, Plaud's transcript and notes. New
  recordings land in the Library's **Untagged**, and the Library's Refresh says "2 new · 1 private
  from Plaud" until you press it.
- **Keep things out.** Start a title with `noarchive` in Plaud's app and it is never downloaded.
  Put an ID on the consent list (`make consent-add ID='…'` from the Mac) and it never comes in. A
  title that matches your private patterns arrives already tagged `private`.
- **Change your mind.** **Release** lets in a recording kept out only by its title, after showing
  you its title and asking. **Keep it here** keeps one that was kept out too late.
- **Edit in Plaud.** Rename speakers, regenerate notes or change a title in Plaud's app, and the
  archive follows: titles at the next sync, the rest within about a day. **Re-sync from Plaud**
  (a recording's Details, or a selection in the Library) fetches the edits at once.
- **See what's where.** The **Add** tab is the Sync screen: **Sync now**, **Compare**, what is
  waiting, what is kept out (never by title), what changed on Plaud this week, what Plaud no longer
  lists, and what you parked. Status's new **Plaud sync** panel says "All here", or why not, and
  what to do.
- **Check everything at once.** **Sync everything** (the Sync screen's **More**) checks every
  recording against Plaud and writes a manifest; it also runs by itself every 30 days.
- **Trust that it stops safely.** If Plaud's list suddenly looks very different, the sync holds
  until you look (**Accept this listing**). If Plaud starts sending something new on many
  recordings at once, edit-sync pauses (**Accept these changes**). A recording that keeps failing
  can be parked. A removal on Plaud's side (a name going back to "Speaker 1") is held for your
  review, never applied. Nothing is ever deleted from the archive.
- **Hear about it once.** The sync has its own Healthchecks check: if it stops for about 2 hours,
  you get one email, and Status says why.
- **Try it first.** `make demo-live` runs the real web app and worker on the Mac against a fake
  Plaud, with invented recordings, in a scratch folder. The public Pages demo shows the new screens
  with invented data.

### Notable changes

- A third container, `worker`, from the same image: the only one that talks to Plaud, read-only,
  pacing itself to one call a second, and the only one that holds the sign-in. `make deploy`
  waits for its health check.
- New make targets: `plaud-sign-in` (from the Mac), `plaud-dry-run` and `plaud-catch-up` (on the
  server), `demo-live` and `e2e-live`. `make demo-archive` now refuses, and leaves `demo/archive`
  as it was, when `demo/.cache/media` lacks the demo's media.
- New commands under `recordings plaud`: `sync` (`--dry-run`, `--catch-up`, `--full`), `resync`,
  `park`, `release`, `accept-listing`, `breaker`, `held`, `check`, `consent`, `baseline`, `review`,
  `retire`, `keep-out-words` and `token-import`. Their exit codes are in the user guide.
- New settings in `config.toml` (`[plaud]`: `schedule_minutes`, `auto_import`, `full_sweep_days`,
  `keep_out_word`, `auto_private_patterns`, `consent_list`, `token_store`) and in
  `docker/deploy.env` (the consent folder, the sign-in's folder, ntfy's topic file). Backups now
  include the consent folder and Sync everything's manifests (`[backup] extra_paths`).
- `recordings doctor --role worker` checks the sign-in, the consent list, the keep-out word and
  the sync's ping URL; `doctor` warns when the manifests folder isn't backed up.
- `recordings validate` warns about a Plaud snapshot no recording points to, without failing.
- On a phone the Library's header drops "· newest first", so the count and Refresh, with Plaud's
  arrivals cue in it, stay on one line.
- The runbook gains sections 6 to 8 (deploy, dry run, catch-up) and a section for each way the
  sync can stop. The user guide covers every screen, and `docs/setup.md` is new.

### From the end-of-branch privacy review

- **Release** on a row that may be a kept-out recording back under a new ID asks with no title
  (date, time and length only), and no title is fetched for it. While Plaud's IDs look changed,
  no title can be shown: **Release** on a title row and **Show title** are off until the hold
  clears.
- After a lost `state.db`, whatever lists Plaud first (the worker, or a dry run or catch-up on the
  server) finds the rebuild, so recordings you had kept out wait for your review instead of
  coming in; a run that can't tell lists nothing.
- An invalid `keep_out_word` stops everything that lists or fetches from Plaud, not only the
  scheduled syncs: Compare, Sync now, the imports, Re-sync, Sync everything and Release are off
  with "configuration: `keep_out_word` is invalid".
- A hold that comes while Sync everything runs stops its checks at once; it carries on by itself
  once the hold clears.

### Your answers to the six questions (2026-11-09)

- A new Plaud ID with the start time and length of a recording you kept out is kept out with it at
  once, whether the old ID has gone, is still listed, or is already in the archive: no title
  stored or shown, flagged until you confirm or **Release**. A recording you kept with **Keep it
  here** doesn't count.
- A title the sync drops from its cache is overwritten in the file, not left in its free space.
- A request that waits out its hour behind a long run says the run held the sync, and to press
  again when it ends, not that the worker is away.
- A keep-out word you add later also catches recordings already here whose Plaud titles start with
  it: tagged `private` and flagged at the next sync, once for each word.
- Backups and the NAS mirror refuse to run if `[plaud] token_store` points inside the archive, or
  can't be read, so the Plaud sign-in never lands in a backup; doctor says so too.

## Before stage 2b

Stage 1 (the read-only Library over a demo archive), stage 2a (the real archive, backups, the NAS
mirror, alerts and Status) and tagging part A (selection, the tag picker, the Tags grid, Undo) are
described in the README and their specs. This file starts with stage 2b.
