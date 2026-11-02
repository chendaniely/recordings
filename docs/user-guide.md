# Using recordings

The guide to the app, for the one person who uses it: what each screen shows, what each task
asks of you, and what every Status line means. It covers stages 1 to 2b: the Library, tagging, the
Tags page, Status with its backups, and the Plaud sync.

Three other pages go with it:
- **[Setting up the Plaud sync](setup.md)**: from deploying stage 2b to the first real sync, dry
  run first.
- **[The runbook](runbooks/first-run.md)**: the server-side detail, every command and message.
  This guide links to it rather than copying it.
- **[The changelog](../CHANGELOG.md)**: what changed, stage by stage.

## Contents

- [Start here](#start-here)
- [Trying it first](#trying-it-first)
- The screens: [the top bar](#the-top-bar) · [the Library](#the-library) ·
  [a recording](#a-recording) · [the Tags page](#the-tags-page) ·
  [the Sync screen (Add)](#the-sync-screen-add) · [Status](#status)
- The tasks: [tagging](#tagging-recordings) · [signing in to Plaud](#signing-in-to-plaud) ·
  [the first catch-up](#the-first-catch-up) · [how a recording comes in](#how-a-recording-comes-in) ·
  [keeping a recording out](#keeping-a-recording-out) ·
  [renaming in Plaud after it came in](#renaming-a-recording-in-plaud-after-it-came-in) ·
  [edits in Plaud](#edits-you-make-in-plaud-the-checks) · [Re-sync](#re-sync-from-plaud) ·
  [a recording that fails every check](#when-a-recording-fails-every-check-re-sync-and-park) ·
  [Sync everything](#sync-everything) · [changes held for review](#changes-held-for-your-review) ·
  [a held sync](#when-the-sync-is-held-accept-this-listing) ·
  [the breaker](#when-edit-sync-pauses-the-breaker-and-accept-these-changes) ·
  [Plaud slow or down](#when-plaud-is-slow-busy-or-down) ·
  [after a rebuild](#after-the-syncs-records-were-rebuilt-needs-your-review) ·
  [taking a recording out](#taking-a-recording-out-of-the-archive)
- Reference: [what Status's lines mean](#what-statuss-lines-mean) ·
  [the equations](#what-the-sync-compares-and-the-equations) ·
  [the commands' exit codes](#what-the-sync-commands-exit-codes-mean)

## Start here

**What it is.** One archive of every recording you keep, on the homelab server, as plain files
you can read without the app. The app is a web page over it: you listen, read and tag there.
From stage 2b a background `worker` on the server brings your Plaud recordings in by itself, every
20 minutes, and keeps them up to date with the edits you make in Plaud. Backups go to the NAS every
hour, and a copy of the archive is mirrored to the Mac.

**Where it is.** Open the address you gave the app (`[server] allowed_hosts` in `config.toml`), for
example `http://my-homelab:8000` over Tailscale. The top bar has four tabs: **Library**, **Tags**,
**Add** and **Status**.

**The first time with Plaud,** follow [Setting up the Plaud sync](setup.md): deploy, sign in, the
consent list, a dry run that imports nothing, then the real first sync. Until that first sync has
run, the server syncs nothing by itself.

**A normal day:**
1. You record on Plaud. Within about 20 minutes of it reaching Plaud's cloud, it is in the
   Library's **Untagged** list, with Plaud's title, transcript and notes. A title that matches your
   private patterns arrives already locked `private`.
2. The Library's **Refresh** says "2 new · 1 private from Plaud · Refresh": press it (or `R`) to
   see them.
3. You tag them: select, `T`, type, Enter.
4. You rename speakers or regenerate notes in Plaud's app whenever you like; the archive follows
   within about a day, or at once with **Re-sync from Plaud**.
5. You never check Plaud's app to see that something arrived. If the sync stops for about 2
   hours, Healthchecks emails you once, and **Status** says why and what to do.

**What never happens.** Nothing is ever deleted from the archive by the app or the sync: a
recording you delete in Plaud stays here, as "gone from Plaud". Nothing of a recording you kept out
is ever fetched. No title appears in a log, an alert, an email or on Status: Status names
recordings by their ID.

## Trying it first

### The public demo

<https://chendaniely.github.io/recordings/> runs the app in your browser with four public-domain
recordings and invented Plaud data: the Library, tagging (kept in memory until you reload), the
Tags page, and the Sync screen and Status showing stage 2b's states (a hold, the breaker, kept-out
rows with Release, parked rows, the arrivals cue). Every Plaud button is off: "Demo: nothing is
synced". On the Mac, `make demo` serves the same demo at <http://127.0.0.1:8000>, saving tags to a
temporary copy.

### The live demo: `make demo-live`

Before the server's first real sync, or any time you want to try the Plaud screens for real without
touching Plaud or the archive: the real web app and worker on the Mac, against a fake Plaud server
with an invented account, in a scratch archive.

**What you see**

- `make demo-live` builds the UI, runs a first catch-up of seven invented recordings (about 25
  seconds: it paces one call a second, as the real sync does), then prints "Open
  http://127.0.0.1:8000/#/add" and what to try. `make demo-live PORT=8001` moves it.
- The fake account: six ordinary recordings and one private one, already here; one on the consent
  list and one whose title starts with `noarchive`, both kept out; and three new ones that arrive
  after the first scheduled sync (one private).
- Everything lives in one folder under `$TMPDIR`, removed when you stop it. It never reads your
  `config.toml`, your settings or a sign-in, and refuses to start if anything points elsewhere.
- `make demo-live ARGS=--before-catch-up` starts before the first catch-up, as a fresh server
  does: only **Compare** works until you type `catch-up`. `ARGS=--keep` keeps the scratch folder.

**What to do**

Type a command and Enter in the terminal, then look at the app (`help` lists them):

| Command | What happens | Then |
|---|---|---|
| (none) | Three recordings wait on Plaud | **Sync now**, or tick some and **Import selected** |
| `new` | Two more arrive (one private) | **Compare**, or **Sync now** |
| `hold` | Plaud stops listing six recordings that are here | **Sync now**: the sync holds; **Accept this listing**; `back` undoes it |
| `rename` | The kept-out recording's title loses `noarchive` | **Compare**, then **Release** on its row |
| `late` | A recording here gets `noarchive` in its title | **Sync now**: a late keep-out flag, with **Keep it here** |
| `signout`, `signin` | Plaud refuses the sign-in; a new one | `tick`, then Status |
| `tick` | The scheduled sync runs now, as if 20 minutes had passed | the Library's header counts what came in |
| `breaker` | Plaud starts sending a new field on every recording here (it needs 10: `new` and **Sync now** first if it says so) | `tick`, then Status: edit-sync paused, with the field's name and **Accept these changes** |
| `speakers` | Two recordings here get a speaker named in Plaud | In the Library select both, then **Re-sync from Plaud**: "2 checked: 2 updated (speakers 2)"; or one, from its Details |
| `fail`, `mend` | A recording here fails every check; `mend` makes it work again | Status: "1 recording fails every check" with **Re-sync** and **Park**; parked, it is under **Parked** on the Sync screen, with **Unpark** |
| (none) | **Sync everything**, under the Sync screen's **More** | Every recording checked; its line on Status, and the manifest in the scratch folder's `state/manifests` |
| `catch-up` | With `ARGS=--before-catch-up`: the first catch-up | before it, only **Compare** works |
| `dump` | Writes the screens' data as JSON (start with `ARGS=--keep` to keep it) | |

Ctrl-C (or `quit`) stops the web app, the worker and the fake server, and removes the folder.

## The top bar

"Library · Tags · Add · Status", and the theme switch (light, dark, or your system's). **Add** is
the Sync screen until stage 3b brings uploads. On a phone the four tabs stay one row; with the
phone's text size turned up, the row scrolls sideways within itself.

## The Library

The home page: every recording, newest first, with filters on the left and the open recording on
the right.

**What you see**

- **The sidebar's filters:** **All recordings**, **Untagged** (the inbox: everything new lands
  here), then **Tags** with counts, **+ New tag…**, a separate **Private** group with a lock, and
  **Note types** (`notes/…`). Picking a filter shows only those rows.
- **The list:** each row's title (with a lock if private); when it was recorded, its length and
  its sources; its tags as chips (with a mouse, each shows an **×** that removes it; on a touch
  screen, remove tags in the picker); and a **+** after the chips that opens the tag picker for
  that row. With a mouse, rows can be dragged onto the sidebar's tags.
- **The list's header:**
  - **Refresh**, which also counts what changed since you last pressed it (or loaded the page):
    "5 changed" (rows you tagged, which stay where they are until you refresh) and what came from
    Plaud: "2 new · 1 private · 2 updated from Plaud · Refresh" ("5 from Plaud · Refresh" on a
    phone).
  - Open privacy flags, by count only: "1 kept out too late · 1 consent-listed in the archive · See
    Status" (Status lists them by ID).
  - "Opening the archive…" for a moment while the app opens the archive for writing, or "Tagging
    is off: <reason>" when it can't (the runbook's ["Tagging"](runbooks/first-run.md#tagging)
    table says what each reason needs).
  - The keys: "T tag · Cmd-A all · Esc clear · Cmd-Z undo · R refresh".
- **The selection bar,** with two or more rows selected: "12 selected · Tag… · Re-sync from Plaud
  · Clear" ([Tagging recordings](#tagging-recordings), [Re-sync from Plaud](#re-sync-from-plaud)).
- "N recording files couldn't be read. Run `recordings validate`." under the filters, if any
  file is broken.

**What to do**

- Press **Untagged** to see what's new, tag it, and press **Refresh** to let tagged rows leave.
- Rows never move while you work: a tag you add or an arrival from Plaud waits for **Refresh**.

## A recording

Click a row to open it on the right. On a phone the filters, the list and the recording stack in
one column, the recording below.

**What you see**

- **The header:** its title (with a lock if private), when it was recorded in its own time zone,
  its length, its sources (`plaud`, `audio-router`…) and its ID; its tags, with **×** and **+**.
- **The player,** which stays visible as you scroll; a video shows as video.
- **The tabs:**
  - **Transcript:** the current line is highlighted and the view follows the audio (untick
    **Follow audio** to scroll freely); click any line to jump there. When there is more than one
    transcript (Plaud's, and later your own), a selector picks one.
  - **Notes (N):** every notes output, grouped by note type, with **Compare with** to see two side
    by side, and under each the type, model, engine and time.
  - **Plaud:** Plaud's own summary notes ("Nothing from Plaud for this recording." for one that
    didn't come from Plaud).
  - **My notes:** your own notes on it, from its `my-notes.md` (the app only ever adds to that
    file).
  - **Details:** for a Plaud recording, its Plaud lines first (below); then any file that couldn't
    be read, its **Sources**, every **Output** with the engine and model that made it, and its time
    zone.
- **Details' Plaud lines** (stage 2b): "Plaud: checked 2026-11-02 14:20 · next by 2026-11-03
  14:20" with **Re-sync from Plaud** and its answer beside it; its Plaud ID(s), each with **Copy
  ID**; "Gone from Plaud since 2026-10-23 (kept)"; why the sync tagged it private ("Tagged private
  by the sync on 2026-10-23: its Plaud title changed", or "…: its title matched when it arrived",
  "…: its title matched a private pattern", "…: kept out by title", "…: the sync's record was
  rebuilt"), and, once you removed that `private`, "…. You removed it on 2026-10-24. It comes back
  only if its Plaud title changes to a private one."; and its flags, each with its next step.

**What to do**

- Listen and read. Your own transcripts and notes from local models come with stage 4; until then
  a Plaud recording has Plaud's ("No transcript yet" or "No notes yet" otherwise).

## The Tags page

A grid for tagging many recordings fast: one row per recording, one checkbox column per tag.

**What you see**

- **Untagged only**, ticked by default, with a live "N left"; **Filter by title**; a sort menu
  (newest, oldest, duration, title); the column picker; **Refresh**, with the same counts as the
  Library's.
- The first column, pinned: the selection box, the title, the date, **Open** (the recording in the
  Library, in a new tab, read-only), and with Untagged only, the first words of its transcript.
  The `private` column is pinned next to it; other private tags sit at the right.
- On a phone there is no grid: **Tags** opens the Library filtered to Untagged.

**What to do**

- Click anywhere in a cell to toggle that tag on that row. Click a column's header to add that tag
  to every selected row; its menu sorts by it.
- With the keyboard: arrows move, `1` to `9` and `0` toggle the first ten columns on the focused row
  (or on the selection), Space toggles the focused cell, `T` opens the picker.
- Rows stay put until **Refresh** (`R`), so a row you just tagged doesn't jump away.

## The Sync screen (Add)

The **Add** tab (`#/add`) is the Sync screen: it acts on the Plaud sync, and Status reports on it.
Every press is answered within a second ("Queued"), and the screen follows it until it ends; the
newest result line stays on the screen, so a phone that slept through the toast still sees it. On
a phone it is the same, in one column, with buttons the width of the screen.

**What you see, top to bottom**

1. **The top line,** the same one Status shows:
   - "All here · checked 14:20 · next sync 14:40", with "Every recording on Plaud is in the archive
     or deliberately left out." under it, when the last good sync is recent and nothing waits.
   - "2 waiting · 1 missing · checked 14:20 · next sync 14:40" when something hasn't come in yet.
   - Or what stops the sync, in one sentence ("Sign-in needed", "Held: see Status", "Scheduled sync
     waits for the first catch-up (runbook)"), with **See Status**.
2. **Flags,** each by recording ID (or Plaud ID) with its next step: a recording kept out by title
   after it came in (**Keep it here**, or take it out by hand), one added to the consent list after
   it came in, a `private` the sync couldn't write, and a Plaud ID that "may be a consent-listed
   recording back under a new Plaud ID", with its date and duration. Never a title.
3. **Held,** when a listing is held: its sentence, and **Accept this listing** or why it can't be
   ([a held sync](#when-the-sync-is-held-accept-this-listing)). **Edit-sync paused**, when the
   breaker tripped: its sentence with the fields' names, and **Accept these changes** or why it
   needs a code fix ([the breaker](#when-edit-sync-pauses-the-breaker-and-accept-these-changes)).
4. **Sync:** **Sync now** and **Compare**, and under **More**, **Sync everything** with how long it
   takes ("Takes about 40 minutes", from the last one; "Its time: unknown until the first run"
   before any). The newest result line ("Sync now, 14:20: Synced: 2 imported (1 private) · All
   here"), and a running request's progress ("Importing (2 of 5)").
5. **From the listing:** the equation, each of its numbers a jump to its list; "from the listing
   at 14:20"; the archive's own equation; and the consent list's line ("Consent list: 14 IDs, 4 on
   Plaud now · file changed … · read 14:20") ([the equations](#what-the-sync-compares-and-the-equations)).
6. **Not here yet:** one row per recording, newest first: a box to tick, the date, time and
   duration, the title (with a lock and "arrives private" when its title matches your private
   patterns), and why it waits ("imports at the next sync", "failed 3 times" with **Park**, "needs
   your review: the sync's records were rebuilt" with **Show title**, "held: see Status", "waits
   for the first catch-up"…). **Copy ID** copies the ID the way the consent list takes it.
   **Import selected** and **Import all 31** bring them in now. While the consent list can't be
   read, or a held listing shows Plaud's IDs changed, no titles show: only dates and durations.
7. **Kept out:** "On the consent list", "Kept out by title", "Was kept out by title" (with
   **Release**), and "May be a consent-listed or kept-out recording back under a new ID: confirm
   (runbook)" (with **Release**, if it is a different recording). Each by Plaud ID, date, time and
   duration; never a title. A released row reads "Released · comes in at the next sync (14:40)"
   with **Undo**.
8. **Updated on Plaud (7 days):** recordings here that changed on Plaud, by ID and title, with what
   changed ("speakers", "title", "notes", "own notes", "other", "removal held", "re-tagged
   private") and when. Once a check has run this week and found nothing: "Nothing changed on Plaud
   this week."
9. **Gone from Plaud:** recordings you have that Plaud no longer lists, with "gone since", and how
   many in the last 7 days; nothing is ever deleted from the archive. **Parked:** what you parked,
   with **Unpark**.
10. **Import your own audio:** coming later.

**The buttons**

- **Compare** reads Plaud's whole list and fills in the equations and **Not here yet**. It imports
  nothing and changes nothing in the archive. It is the one button that works before the first
  catch-up. It answers "Compared: listed 120 = here 98 + waiting 3 + excluded 14 + missing 5".
- **Sync now** is the scheduled sync, now, with no limit on how many come in. It answers "Synced: 2
  imported (1 private) · All here", "Synced: nothing new · All here", "Synced: 1 imported (0
  private) · 1 failed · See Status", or why it couldn't run: "Held: see Status", "Rate limited
  until 14:40 · See Status", "Sign-in needed · See Status".
- **Import all** brings in every recording not here yet that can come in, those that failed 3
  times included, but not the ones that need your review after a rebuild. **Import selected**
  brings in the ones you ticked, review rows included. While it runs: "Importing (5 of 31)"; at
  the end: "31 imported · 4 arrived private", with **View**, which opens Untagged. Importing never
  asks "are you sure?".
- **Release** asks first, and only there shows the title; never for a possible re-keyed match,
  whose question has none ([keeping a recording out](#keeping-a-recording-out)).
- **Park**, on a row that "failed 3 times", stops it counting against "All here", and syncs leave
  it alone. **Unpark** puts it back; **Import selected** tries it again whenever you like.
- **Sync everything:** [below](#sync-everything).
- Pressing **Sync now** or **Compare** again while one is still queued answers "Already queued":
  one sync answers all the presses. **Sync now** pressed while a Compare is queued turns that
  Compare into the sync. Imports queued together run as one.
- While a long run holds the sync on the server (the first catch-up, Sync everything), a press
  waits its turn ("Queued after the current sync (started 14:20)", or "Waiting for Sync everything
  (412 of 1,234)"). If nothing runs it within the hour (the worker is down, say), it expires and
  says so: press it again then. "The Plaud worker isn't running (last reported 14:21): your request
  runs when it's back, if that's within the hour."
- A button that is off says why beside it: before the first catch-up only **Compare** works;
  under a hold, **Sync now** and the imports say "Held: Plaud's listing is held (see Held, above)";
  in the demos, "Demo: nothing is synced".

**What to do**

- Nothing, usually: the scheduled sync does all of this every 20 minutes.
- A recording that keeps failing: check it plays in Plaud's app, then **Park** it.

## Status

Status reports: what needs you, the Plaud sync, the backups and the disk. It redraws every minute,
and never names a recording by its title, only by its ID.

**What you see, top to bottom**

1. **"X is running, started 14:20."** while a long job holds the backups (a restore test, a
   check, an import); "The hourly backup waits for it."
2. **Needs attention:** every line that asks something of you, from the backups and the sync. Each
   is in [the table below](#what-statuss-lines-mean) with what to do.
3. **Plaud sync,** the panel:
   - The same top line as the Sync screen, with **Open the Sync screen**.
   - Recordings that fail every check, on their own line: "2 recordings fail every check (since
     2026-10-23)" with **Re-sync** and **Park**
     ([below](#when-a-recording-fails-every-check-re-sync-and-park)).
   - Plain sentences, each only while it is true: the sync failing, "isn't being pinged since …",
     the conditions (a late keep-out, the breaker's note, held removals, the schedule off…), a hold
     with **Accept this listing**, the breaker with **Accept these changes**.
   - **The numbers:** the listing's equation, how many arrive private, the archive's equation, the
     consent list; the last good listing ("14:20 (120 listed)") and the next sync; the sweep
     ("every recording checked within the last 21 hours (oldest 2026-11-01 17:10) · an edit in
     Plaud shows within about 25 hours"); the sign-in ("works (last used 14:20) · access token
     expires 15:20 · refresh token expires 2027-01-21 09:12"); the last Sync everything
     ("2026-10-01: all 1,234 checked (2 parked, 1 degraded)", or "running since 14:20 (412 of
     1,234)"); held for review, gone from Plaud, failing and parked; "Plaud worker: last reported
     14:21 (idle)"; and "Healthchecks: last pinged 14:40" (or "last ping failed 14:40 (HTTP
     404)").
   - **Details,** folded away: the normaliser's version, the fields outside the named parts that
     changed in the last 24 hours (names only), the ID forms Plaud uses (and a change of form that
     held nothing), the changes per part in 24 hours, the keep-out words in force, malformed
     listing rows, and the last 20 runs with their results.
4. **Backups:** one row each for **Backup** (hourly, with the NAS's free space under it),
   **Restore test** (monthly), **Repository check** (weekly), **Mirror for the Mac** (hourly) and
   **Retention** (daily): when it last worked, and its state: OK, Waiting (a long job holds it),
   Overdue, or "Failing: <reason>". Then "The backup service last reported 14:21."
5. **Disk:** a meter and "123.4 GB free of 1.0 TB"; below 20% free a warning, below 5% "new imports
   stop".

**What to do**

- Read Needs attention first: each line says its next step. The sync's lines are explained in
  [the table below](#what-statuss-lines-mean), and the runbook has the detail.
- Healthchecks emails come from two checks: the backup's and, from stage 2b, the sync's. Either
  email means: open Status.

## Tagging recordings

Tags are how you sort the archive, and what decides who may read a recording: a recording with no
tag stays hidden from outside agents, and one tagged `private` (or anything under `private/`) stays
on your own machines.

**First, the rule for the real archive:** the first real tag waits until the first catch-up ends
with "You may start tagging now" ([the first catch-up](#the-first-catch-up)), so that rolling
that import back (a restore from its `pre-import` backup) loses no tag of yours. After your first
tag, a rollback is a restore, and the runbook's "Rolling back, until the first real tag" no longer
applies.

**What to do**

1. **Select:** click a row; Cmd or Ctrl-click adds or removes one; Shift-click selects a range;
   the boxes work too. With the keyboard: Up and Down select as they move, Shift extends,
   Cmd/Ctrl+arrows move without selecting, `X` toggles the focused row, Cmd-A selects every row in
   the filter, Esc clears.
2. **Tag:** press `T` (or the **+** after a row's chips, or **Tag…** on the selection bar). The
   picker opens with "Find or create a tag": type, and the existing tags that match come first.
   - **Enter** adds the highlighted tag, and the picker stays open for more.
   - **Shift+Enter** creates exactly what you typed ("Create "…"" is offered last). A `/` makes a
     folder: `space/apollo`.
   - **Shift+Delete** removes the highlighted tag from the selection.
   - **Esc** closes it.
3. **Or drag** the selection onto a tag in the sidebar; dropped on **+ New tag…**, the picker opens
   to name a new one.
4. **Remove** a tag with the **×** on its chip, or in the picker: the **×** beside a tag the
   selection has, or Shift+Delete.
5. **Undo:** every action's toast has **Undo**, and Cmd/Ctrl+Z undoes the newest action still in
   this window (the last 20). A reload ends Undo for what came before.
6. **Refresh** (`R`) when you're done, so tagged rows leave Untagged.

**What you see**

- The toast says what happened and who can now read it: "Added work to 12 recordings · 12 now
  visible to external agents" (a first tag makes an untagged recording readable by agents such as
  Claude Code, unless it is private).
- **Removing a recording's last private tag asks first** ("Remove private from 1 recording?", with
  how many become visible), even by an Undo. Adding `private` never asks.
- When tagging is off, the header says "Tagging is off: <reason>", and `T` or a drop shows the
  reason in a toast. The runbook's ["Tagging"](runbooks/first-run.md#tagging) table has every
  reason and what to do; **Refresh** retries an open that failed.
- In the demos tags aren't saved for good: `make demo` keeps them in its temporary copy, the
  public demo in memory until you reload ("Demo: changes aren't saved").

## Signing in to Plaud

The server's sync reads your recordings from Plaud with a sign-in that only the server's `worker`
holds. You make it from the Mac, and the server keeps it fresh by itself.

**What you see**

- **Status** says "Plaud sign-in needed since 23:00. New recordings wait on Plaud: nothing is lost.
  At the Mac: `make plaud-sign-in`." when the server has no sign-in, or Plaud has refused it.
  Nothing is lost: new recordings wait on Plaud.
- "The Plaud sign-in expires 2027-01-21 09:12. At the Mac: `make plaud-sign-in`." a week before it
  runs out.
- About 2 hours after the sync stops, Healthchecks emails that the sync's check is down.
- `recordings doctor --role worker`, on the server, says "sign-in needed: run `make plaud-sign-in`",
  or warns a week before the sign-in's refresh token expires.

**What to do**

1. On the Mac, at the repo's top, check that `local.mk` names the server (`SERVER = …`) and the
   repo's checkout there (`REPO_DIR = …`). `make consent-add` uses the same file.
2. Run `make plaud-sign-in`. A browser opens: sign in to Plaud there.
3. It ends with one line: "signed in: yes · access token expires … · refresh token: present ·
   account 1a2b3c4d". The account is shown as a short code, never your e-mail.
4. To check from the server at any time: `rc exec worker recordings plaud check`.

If it stops with "step 2 failed", the browser sign-in didn't finish: run it again. "Step 3 failed"
means the Mac couldn't reach the server. "Step 4 failed" says why the server refused it, and that
nothing was installed. The runbook's ["Plaud sign-in"](runbooks/first-run.md#plaud-sign-in) has
every message.

The sign-in is a secret. Never copy its file by hand or paste it anywhere, and don't ask a Claude
session to run `make plaud-sign-in` for you: you sign in yourself, in the browser.

## The first catch-up

Until the first catch-up has run, the server syncs nothing by itself: Status says "Scheduled sync
waits for the first catch-up (runbook)", and only **Compare** works on the Sync screen. The
catch-up is the first real import from Plaud, and you run it yourself, on the server, after a dry
run that imports nothing. [Setting up the Plaud sync](setup.md), steps 4 and 5, walks through both
and how to read the dry run; the runbook's sections 7 and 8 have every step.

**What you see if it goes wrong**

- "Plaud sync paused: …" (held): Plaud's list looks very different from the archive's. Read the
  runbook's section 8, step 4: check, then `rc exec worker recordings plaud accept-listing`, and
  run `make plaud-catch-up` again.
- Stopped part-way (Plaud down, the connection dropped, a restart): Status says "The first
  catch-up didn't finish: run `--catch-up` again (runbook)". Run `make plaud-catch-up` again: it
  imports only what isn't here yet.
- "some recordings failed": the catch-up is done anyway. The scheduled sync tries them again;
  any that keep failing wait under **Not here yet** as "failed 3 times", with **Park**.

## How a recording comes in

Once the first catch-up has run, the server syncs by itself every 20 minutes (`[plaud]
schedule_minutes`). Each sync reads the consent list, checks the sign-in, reads Plaud's whole list,
checks every listed title against your private patterns and the keep-out word, compares, and brings
in what is waiting. You never confirm an import.

**What you see**

- A new recording in the Library's **Untagged** list, with Plaud's title (even a placeholder such
  as "2026-11-02 14:05:31"), Plaud's transcript and notes if it had them yet, and its time in your
  default time zone. The Library's **Refresh** counts it: "1 new from Plaud".
- A title that matches your private patterns ("Private: …", "interview: …") arrives already
  tagged `private`: the tag lands before any of its content does.
- A recording comes in whole or not at all. New ones arrive newest first, at most 20 per scheduled
  sync; a longer backlog comes over the next syncs.
- One that fails is tried again later (20 minutes, then 40, and so on), so one bad recording never
  slows the others; after 3 failures in a row it waits under **Not here yet** as "failed 3 times".
- A deploy or a restart in the middle of a sync stops it between two recordings; nothing is
  half-written, and the next sync carries on.

**What the sync checks first, every time**

- The consent list, read again just before each recording: an ID you added a moment ago is never
  fetched. If the list can't be read, nothing is imported until it can.
- The keep-out word, on the title Plaud lists and again on the title Plaud gives when the sync
  asks for the recording: a match fetches nothing more of it, and keeps it out.
- The disk: if the recording would leave less than the stop margin free, imports stop for that
  sync, and Status says "the disk is too full to import". Free some space; the next sync goes on.
- The same audio as a recording you have kept out (consent-listed, or kept out by its title) is
  never added: it is kept out too, and flagged as possibly that recording back under a new ID.

**What to do**

- Nothing, usually. To bring something in sooner, press **Sync now** on the Sync screen.
- A recording that keeps failing: check it plays in Plaud's app, then **Park** it so it stops
  counting against "All here".
- To turn the schedule off for a while (maintenance), set `[plaud] schedule_minutes = 0` and
  restart the worker; set it back to 20 afterwards. While it is off, the sync's check emails after
  2 hours.
- To bring new recordings in only when you press Import, set `[plaud] auto_import = false`: Status
  then says "Auto-import is off: new recordings wait for you on the Sync screen."

## Keeping a recording out

Two ways, both for good: the keep-out word, from your phone, and the consent list, for a recording
you name by ID. Both fail closed: if either can't be read, nothing is imported.

**The keep-out word.** Start a recording's title in Plaud's app with `noarchive` (the template's
word, `[plaud] keep_out_word`) and the sync never downloads it. The word is forgiving: "NoArchive:",
"No archive" and "[noarchive]" all count; "noarchived" doesn't. Rename it before it uploads, or at
once: the sync lists Plaud every 20 minutes.

**The consent list.** For a recording you name by ID: from the Mac, `make consent-add ID='<id>'`,
the ID in single quotes. On the Sync screen, **Copy ID** puts the ID on the clipboard in the list's
own form. The worker reads the list within seconds of a change; `rc exec worker recordings plaud
consent` shows what it read. The runbook's ["Keep a recording out for good"](runbooks/first-run.md#keep-a-recording-out-for-good)
has the rest.

**What you see**

- The Sync screen lists it under **Kept out** ("On the consent list", or "Kept out by title"), by
  date, time and duration, never by title.
- If a kept-out title later loses the word (Plaud's AI retitles it, or autocorrect), it stays out,
  as "Was kept out by title", with **Release**.
- If the sync downloaded it before the rename reached Plaud, see the next section: too late, so it
  is tagged `private` and flagged.

**Release: letting one in after all**

- Press **Release** on its row. It asks first, and only there shows the title: "Let '…' (date,
  length) in? It downloads at the next sync, and then stays in the backups." **Let it in**, or
  **Cancel**.
- Release is refused, with the reason, while the title still starts with the word ("Release
  refused: its title still starts with `noarchive`": rename it in Plaud first), while the
  recording is on the consent list, and while the consent list can't be read.
- While Plaud's IDs look changed (a held listing, or the last Compare, says so), no title can be
  shown: **Release** on a "Was kept out by title" row and **Show title** are off, saying "No title
  can be shown: titles hidden while Plaud's IDs look changed". Among the new IDs may be a recording
  you kept out. Once the hold clears (see [Accept this listing](#when-the-sync-is-held-accept-this-listing)),
  they work again.
- Until the next sync fetches it, the row reads "Released · comes in at the next sync (14:40)",
  with **Undo**, which keeps it out again.
- A row "May be a consent-listed or kept-out recording back under a new ID" that is really a
  different recording: press **Release** too. Its question shows **no title**: "Let this
  recording (date, time, length) in?". The sync suspects it is one you kept out, so it never
  fetches or shows its title; check the date and length against your own record first. **Let it
  in** is still checked against the consent list and the keep-out word as they are then.
- On the server, `rc exec worker recordings plaud release <Plaud ID>` does the same, showing only
  the ID, date and duration and asking y/N; `--undo` keeps it out again.

## Renaming a recording in Plaud after it came in

Each sync reads every title Plaud lists, and checks the ones that changed since it last looked.

**What you see**

- Renamed to a private title ("Private: …", or anything your patterns match): it is tagged
  `private` at the next sync, even if Plaud won't hand over its transcript. Details says "Tagged
  private by the sync on 2026-11-02: its Plaud title changed", and **Updated on Plaud** says
  "re-tagged private".
- Renamed to start with your keep-out word: too late to keep it out, so it is tagged `private` at
  once, nothing more is ever fetched for it, and it is flagged on Status ("Recording … was kept out
  by title after it came in. Take it out by hand (runbook), or Keep it here."), on the Sync screen
  and in the Library's header ("1 kept out too late").
- Added to the consent list after it came in: the same, "Recording … is on the consent list, but in
  the archive. Take it out by hand (runbook)."
- A `private` you removed stays removed. It comes back only if the Plaud title changes again to
  one that matches.
- "Should be private, but its file was edited outside the app: run `recordings reindex`": the tag
  couldn't be written. The sync's check stops being pinged until it lands.

**What to do**

- A late keep-out: take it out by hand ([below](#taking-a-recording-out-of-the-archive)), or press
  **Keep it here** to keep it ("Kept here: it stays private, and nothing more is fetched from Plaud
  for it"). It stays private either way, and a later title with the word won't flag it again.
- A late consent listing: take it out by hand, or keep it (it stays private).
- "Should be private …": on the server, `rc exec worker recordings reindex`. The next sync tags it.

## Edits you make in Plaud: the checks

Plaud tells the sync nothing when you edit a recording there, so the worker checks: it fetches a
recording again, compares it with what the archive already holds, part by part, and keeps a new
snapshot only when something changed. Your transcript, speaker names and notes then follow.

**What you see**

- **A title** you change in Plaud shows at the next sync (the listing says so at once).
- **Speaker names, regenerated notes, your highlights and memos** show when the recording's next
  check comes round: within `full_sweep_days` (1 in the template) plus one sync, so within about a
  day. A recording you made in the last two days, still being transcribed, is checked every sync
  for its first two hours, then hourly; one that just changed is checked again soon after.
- **Updated on Plaud (7 days),** on the Sync screen: each recording that changed, by its title,
  with what changed and when. With nothing changed: "Nothing changed on Plaud this week."
- **Details** of a recording: "Plaud: checked 2026-11-02 14:20 · next by 2026-11-03 14:20".
- **Status's numbers:** "every recording checked within the last 21 hours (oldest 2026-11-01
  17:10) · an edit in Plaud shows within about 25 hours". Its details add the changes per part in
  the last 24 hours, and the names of any fields outside the named parts that changed.
- **Never checked:** a recording kept out (by title, or by Keep it here) or on the consent list,
  one gone from Plaud, and one you parked. Nothing more is ever fetched for those.
- **A degraded check:** Plaud answered, but a note or the transcript behind one of its links
  didn't arrive, twice. The sync keeps that answer once, builds nothing from it, and tries again at
  the next check. One degraded recording is nothing to do; if every check is degraded, the sync
  stops being pinged ("every fetch failed").
- **A recording that fails every check** (3 in a row) gets its own line on Status. The sync keeps
  trying it, less often each time, and checks a healthy recording beside it, so one bad recording
  never stops the ping.

**What to do**

- Nothing: edits arrive by themselves.
- Want it now? **Re-sync from Plaud** (next section).
- A removal (a name going back to "Speaker 1", a note vanishing) is held for your review, never
  applied ([changes held for review](#changes-held-for-your-review)).
- When you finish relabelling speakers in Plaud, set `[plaud] full_sweep_days = 7` (the default):
  fewer calls to Plaud, and edits still arrive within a week.

## Re-sync from Plaud

When you have just renamed speakers or regenerated notes in Plaud and don't want to wait for the
sweep.

**What you see**

- In a recording's **Details:** "Plaud: checked … · next by …" with **Re-sync from Plaud**. Press
  it, and its answer shows beside it:
  - "Up to date": nothing changed since the archive's copy. Plaud may take a moment to show an edit
    you just made; press again in a minute.
  - "Updated: speakers" (or "title", "notes", "own notes", "other"): the new snapshot is kept, and
    the transcript, names and notes are rebuilt from it.
  - "Not checked: kept out", "Not checked: on the consent list", "Gone from Plaud", "Not checked: it
    didn't come from Plaud", "Not checked: Plaud doesn't list it now": nothing is fetched for these,
    by design.
  - "Edit-sync paused: see Status": the only change was the kind the breaker holds back.
  - "Failed: Plaud unavailable" (or another reason): try again later.
- In the **Library** (or the Tags grid), select two or more rows: the bar reads "12 selected ·
  Tag… · Re-sync from Plaud · Clear" ("Re-sync" on a phone). One press sends one request for the
  whole selection, up to 1,000 ("For more, use Sync everything"); the bar shows "Re-syncing (12 of
  25)", then "25 checked: 7 updated (speakers 6, title 1) · 18 up to date". The rows that changed
  are read again in place; the list's order waits for Refresh.
- Before the first catch-up Re-sync is off; while the sync is held it answers "Held: see Status".

**What to do**

- Press it after an edit you want now. On the server, `rc exec worker recordings plaud resync
  <id>…` does the same.

## When a recording fails every check: Re-sync and Park

A recording whose checks failed 3 times in a row is on Status's line "2 recordings fail every
check (since 2026-10-23)", with **Re-sync** and **Park**. It never stops the sync's check, and the
sync keeps trying it, less often each time.

**What you see**

- **Re-sync** on that line tries every recording it counts, now. One that works leaves the line.
- **Park** on that line parks every recording it counts: the sweep leaves them alone, they no
  longer count against Sync everything, and the Sync screen lists them under **Parked**, each with
  **Unpark**. A Re-sync still tries a parked recording, and one that works is unparked.

**What to do**

1. Press **Re-sync** first: a passing fault clears.
2. Still failing: open the recording in Plaud's app. If Plaud can't open it either, press
   **Park**. If it plays there, the fault may be the app's: the runbook's
   ["When a recording fails every check"](runbooks/first-run.md#when-a-recording-fails-every-check-re-sync-and-park)
   says what to send a Claude session.
3. **Unpark** (Sync screen, **Parked**) when you want it checked again.

## Sync everything

The sweep checks every recording over a day or so; **Sync everything** does it all at once, then
writes a manifest: the list of every recording on Plaud with its state, used for DAN-15's final
check before the Plaud account ends.

**What you see**

- On the Sync screen, under **More:** **Sync everything**, with "Takes about 40 minutes" (from the
  last run's pace) or "Its time: unknown until the first run". While one runs, or within an hour of
  the last, it is off and says why ("Sync everything is running (412 of 1,234)", "Sync everything
  ran less than an hour ago").
- Pressed: it lists, brings in what is waiting, checks every recording here oldest first, and tries
  its failures once more. Its answer: "Sync everything: all 1,234 checked (2 parked, 1 degraded)",
  or what is missing ("1,230 of 1,234 fine: 3 failed, 1 not here · See Status").
- Other presses meanwhile wait and say "Waiting for Sync everything (412 of 1,234)". A Re-sync of
  one recording, a Compare and the scheduled sync still run between two of its recordings.
- If the worker restarts in the middle, it picks up where it left off ("Resuming (started 14:20)").
- It also runs by itself every 30 days while the schedule is on.
- Status's numbers show the last one: "2026-10-01: all 1,234 checked (2 parked, 1 degraded)".

**What to do**

- Nothing, usually: the monthly run and the sweep keep up.
- After a long gap, or to be sure everything is here, press it.
- A recording it couldn't check: [above](#when-a-recording-fails-every-check-re-sync-and-park). A
  flagged recording (kept out or consent-listed after it came in) is named but never a failure.
- DAN-15, when the Plaud account ends: the runbook's
  ["Sync everything, and DAN-15's last run"](runbooks/first-run.md#sync-everything-and-dan-15s-last-run).

## Changes held for your review

Some changes from Plaud would take something away: a speaker's name going back to "Speaker 1", a
note vanishing. The sync never applies those by itself.

**What you see**

- Status: "3 changes from Plaud are held for your review (the review list comes with stage 3a).",
  and "Held for review: 3" in the numbers.
- The Sync screen's **Updated on Plaud** shows "removal held" on the recording that had one.

**What to do**

- Nothing yet: stage 3a brings the review list where you accept or refuse each. Until then the
  archive keeps what it had. On the server, `rc exec worker recordings plaud held` lists them by
  recording ID and part.

## When the sync is held: Accept this listing

If Plaud's list suddenly looks very different from the last good one, the sync stops importing
until you look. That protects the consent list: if Plaud changes its IDs again, a recording you
kept out could otherwise come back under a new ID.

**What you see**

- **Status** and the Sync screen show the hold's sentence, with **Accept this listing**:
  - "Plaud now lists 40 fewer of the recordings here (and 38 look new). If you deleted them in
    Plaud, Accept this listing: they show as gone from Plaud.": many recordings vanished at once.
  - "Plaud sync paused: only 15% of the IDs Plaud listed last time are listed now. Check the
    runbook's steps, then Accept this listing.", or "Plaud sync paused: Plaud's IDs changed form,
    and 12 recordings here vanished with them. …": Plaud changed its IDs. While such a hold is on,
    no title of a recording not here is shown: only dates and durations.
  - "Plaud sync paused: Plaud lists 3 IDs in a form the consent list can't name. It needs a code
    fix (runbook).": Accept can't help; ask a Claude session to look.
- **Sync now** and the import buttons are off, and say "Held: Plaud's listing is held (see Held,
  above)"; **Compare** still works.
- About 2 hours later, Healthchecks emails that the sync's check is down.
- A few recordings vanishing (5 or fewer, or under 5% of the archive's) isn't held: Status says
  "Plaud now lists 3 fewer: waiting for the next listing to agree.", and an hour later they show as
  gone from Plaud by themselves.

**What to do**

1. Check in Plaud's app why the recordings vanished: if you deleted them, that's the answer.
2. If Plaud's IDs changed, follow the runbook's steps first: every consent-list entry must still
   name its recording.
3. Press **Accept this listing** on Status (or the Sync screen). It answers "Accepted this
   listing: imports resume"; the vanished recordings show as gone from Plaud. On the server, `rc
   exec worker recordings plaud accept-listing` does the same; it is also how the first catch-up's
   own listing is accepted, if that one is held.
4. If Accept is refused with "Fix this first: 4 consent-listed or kept-out IDs vanished while
   Plaud's IDs changed (runbook)", don't work around it: some recordings you kept out may be back
   under IDs your consent list can't name. The runbook's
   ["When the sync is held"](runbooks/first-run.md#when-the-sync-is-held) says how to find them.

A hold also clears by itself if the next listing looks normal again (Plaud put things back).

## When edit-sync pauses: the breaker, and Accept these changes

If Plaud starts sending something new on many recordings at once (a new field, say, after an
update of theirs), every check would keep a new snapshot of every recording. The breaker stops
that: it trips when, within a day, at least 10 recordings (and more than 5% of those checked)
changed only outside the parts you edit, or changed between two fetches a moment apart, which no
edit does.

**What you see**

- On Status and the Sync screen: "Edit-sync paused since 14:20: Plaud started sending something
  new on 12 recordings (`folder_id`)", or "…: 12 recordings' transcripts change between two
  fetches a moment apart". Field names only, never their values. "New recordings still arrive."
- On Status: "While this is open, the sync's check stays down, so a new problem won't send another
  email: glance at Status until it's fixed." Needs attention says edit-sync is paused.
- **Accept these changes**, when the new fields came back the same on both fetches, with "If
  Plaud added a feature, accept it: each recording gets one new snapshot. If a name looks like a
  link or a key, don't: it needs a code fix (a Claude session)."
- "This keeps changing by itself: it needs a code fix", and no button, when a part differs
  between two fetches, or when a field you accepted before trips it again.
- Meanwhile everything else goes on: new recordings, title checks, `private` tags, and every
  real edit you make.
- After a release that changes how the app compares Plaud's answers, the breaker closes by
  itself, and Status says "Edit-sync's breaker was closed by the release on 2026-11-02; watching
  for a re-trip" for a day.

**What to do**

- Read the field names. If they look like a feature Plaud added (`folder_id`, `share_state`),
  press **Accept these changes** ("Accepted: each recording gets one new snapshot"): each
  recording gets one new snapshot over the next day, and the breaker closes.
- If a name looks like a link, a token or a key, or Status says it needs a code fix: open a Claude
  session with Status's sentence. Don't accept.
- On the server, `rc exec worker recordings plaud breaker` says the same as Status, and
  `recordings plaud breaker reset` closes it by hand (no runbook step needs it).

## When Plaud is slow, busy or down

The server asks Plaud for one thing a second at most, and a scheduled sync stops asking after 10
minutes: whatever is left comes at the next one. Nothing is lost when a sync stops early.

**What you see**

- **Status** says "rate limited until 14:40" when Plaud asked the server to slow down. Scheduled
  syncs wait until then, and carry on by themselves.
- "Plaud unavailable" means Plaud answered with errors three times in a row, a minute and a half
  apart in all, or didn't answer in time. "offline" means the server couldn't reach Plaud at all.
- "Plaud refused access (403)" means Plaud turned the account away: the subscription may have
  lapsed.
- If one of these lasts about 2 hours, Healthchecks emails that the sync's check is down.

**What to do**

- Rate limited, unavailable or offline: nothing. It heals itself at the next good sync. If the
  email comes, check that the server is online (the runbook's
  ["When the Plaud alert comes"](runbooks/first-run.md#when-the-plaud-alert-comes)).
- Refused access: open Plaud's app and check the subscription.
- A long recording that keeps failing to download (the server resumes a download that drops, a
  few times, then gives up until the next sync) is worth a note in a Claude session: the server's
  download rules were set without measuring Plaud's.

## After the sync's records were rebuilt: needs your review

The sync remembers what you kept out in its own records (`state.db`). If those are lost and
rebuilt, or restored from an older backup, it can't know what you kept out since, so it asks.

**What you see**

- Status and Needs attention: "The sync's records were rebuilt: 6 recordings wait for your review
  on the Sync screen." No email: the sync goes on.
- On the Sync screen's **Not here yet**, every Plaud recording made before the rebuild that isn't
  here reads "needs your review: the sync's records were rebuilt", by date, time and duration.
  Its title shows only when you press **Show title** (never while Plaud's IDs look changed).
- Recordings made after the rebuild arrive as usual. **Import all** and the scheduled syncs leave
  the review rows out ("Import all 31 · 6 more need your review").
- A `private` tag you removed may come back once, with "the sync's record was rebuilt" in
  Details. That errs private.
- Whatever lists Plaud first after the rebuild finds it: the worker, or a command you run on the
  server (`sync --dry-run`, `sync`, `--catch-up`, `resync`, `--full`). The command then says "the
  sync's records were rebuilt: what was made before now and isn't here waits for your review", and
  `--catch-up` adds "the first catch-up isn't needed: use `recordings plaud sync`". If it can't
  tell (state.db or the archive can't be read), it lists nothing and says "the check for a rebuilt
  state.db stopped (`OSError`)": fix what it names, then run it again.

**What to do**

- For each review row: tick it and press **Import selected** to bring it in, or keep it out (add
  its ID to the consent list with `make consent-add`).
- After restoring `state.db` from a backup, the runbook's "Restoring for real" runs
  `rc run --rm worker recordings plaud review`, which does the marking (the sync can't tell an
  older `state.db` by itself).

## Taking a recording out of the archive

The app never deletes a recording, and neither does the sync. To take one out for good (a late
keep-out or consent listing, a recording gone from Plaud you don't want, anything you must not
keep), follow the runbook's
["Taking a recording out by hand"](runbooks/first-run.md#taking-a-recording-out-by-hand): put its
Plaud ID on the consent list first, so the sync can never bring it back, then delete its folder on
the server, reindex, and check the mirror. It also lists what still holds a copy (the backups until
they age out, the Mac, Plaud's cloud).

## What Status's lines mean

Every line Status can show, what it means, and what to do. "Needs attention" collects the ones
that ask something of you; the rest show in the Plaud sync panel while they are true. Times and
counts here are examples.

### The Plaud sync

| Status says | It means | Do |
|---|---|---|
| All here · checked 14:20 · next sync 14:40 | Every recording on Plaud is in the archive or deliberately left out, as of a recent good sync | Nothing |
| 2 waiting · 1 missing · checked 14:20 · next sync 14:40 | Some haven't come in yet: waiting ones come at the next sync | Nothing; for a missing one, its row under **Not here yet** says why |
| No sync has run yet | The worker hasn't written its records yet (a new deploy) | Wait a minute; if it stays, see the worker lines below |
| Scheduled sync waits for the first catch-up (runbook). | Nothing syncs until you run the first catch-up | [Setting up](setup.md), steps 4 and 5 |
| The first catch-up didn't finish: run `--catch-up` again (runbook) | The catch-up stopped part-way | `make plaud-catch-up` again on the server |
| Plaud sign-in needed since 23:00. … At the Mac: `make plaud-sign-in`. | Plaud refused the sign-in, or there is none; new recordings wait on Plaud | `make plaud-sign-in` on the Mac ([signing in](#signing-in-to-plaud)) |
| The Plaud sign-in expires 2027-01-21 09:12. At the Mac: `make plaud-sign-in`. | The sign-in runs out within 7 days | `make plaud-sign-in` before then |
| Plaud sync failing since 12:00 (Plaud unavailable). Last good listing 11:40. | Syncs are failing; the reason is in brackets | Plaud unavailable, offline, rate limited: wait. "every fetch failed" or "error: `Class`": `rc logs worker`, and a Claude session with the class |
| Plaud sync has been failing since 12:00. | The same, for over 2 hours (Healthchecks has emailed) | As above |
| The sync's Healthchecks check isn't being pinged since 14:20: <cause>. It emails you about 2 hours after that. | The sync's check is, or soon will be, down; the cause is named | Fix the cause; the runbook's ["When the Plaud alert comes"](runbooks/first-run.md#when-the-plaud-alert-comes) has each |
| The sync's Healthchecks ping has failed for 2 hours. | The server can't reach Healthchecks | Check the server's network and the `deadman_sync_url` secret |
| Plaud imports stopped: the consent list … | The consent list is missing, empty, unreadable, or has a line that looks like an ID but can't be read; nothing is imported | Fix the lines it names; the runbook's ["When imports stop"](runbooks/first-run.md#when-imports-stop-the-consent-list) |
| Plaud imports stopped: the disk is too full to import. | Importing would leave less than the stop margin free | Free space (the runbook's "When the data disk fills") |
| Recording … is on the consent list, but in the archive. Take it out by hand (runbook). | You listed a recording that was already here; it is now `private` | [Take it out](#taking-a-recording-out-of-the-archive), or keep it |
| Recording … was kept out by title after it came in. Take it out by hand (runbook), or Keep it here. | Its Plaud title gained the keep-out word after it came in; it is now `private` | Take it out, or **Keep it here** |
| Recording … should be private, but its file was edited outside the app: run `recordings reindex`. | The sync owes it a `private` tag it couldn't write | `rc exec worker recordings reindex` |
| Recording … should be private, but its file couldn't be written (`Class`): check the archive disk and its permissions. | The same, for a disk or permission fault | Check the disk and the folder's owner, then `rc logs worker` |
| Recording … should be private, but its recording.json is missing: restore it from a backup, or run `recordings reindex`. | Its folder is there without its record | Restore that file from a backup, or reindex |
| Recording … wasn't checked against the private patterns: run `recordings reindex`, which says what stops it. | You changed the private patterns and this one couldn't be checked | `rc exec worker recordings reindex` |
| Checking titles against the private patterns stopped (`Class`): … / Late consent listings stopped (`Class`): … | That pass failed as a whole | Do the step it names, then `rc logs worker` |
| Plaud sync is held since 14:20: see the Plaud sync panel to Accept this listing, or why it can't be. (and the hold's sentence) | Plaud's list looks very different; imports wait | [A held sync](#when-the-sync-is-held-accept-this-listing) |
| Plaud now lists 3 fewer: waiting for the next listing to agree. | A few recordings vanished from Plaud | Nothing: an hour later they show as gone from Plaud |
| Plaud ID of_…: May be a consent-listed recording back under a new Plaud ID: it stays out until you confirm (runbook). | A new ID with the same start and length as one you kept out | If it is the same recording, add the new ID (`make consent-add`); if not, **Release** it on the Sync screen |
| Edit-sync is paused since 14:20: … (and the breaker's sentence, and "While this is open, the sync's check stays down …") | The breaker tripped | [The breaker](#when-edit-sync-pauses-the-breaker-and-accept-these-changes) |
| Edit-sync's breaker was closed by the release on 2026-11-02; watching for a re-trip | A release fixed what tripped it | Nothing |
| 3 changes from Plaud are held for your review (the review list comes with stage 3a). | Removals from Plaud wait for you | Nothing yet ([held for review](#changes-held-for-your-review)) |
| 2 recordings fail every check (since 2026-10-23) · Re-sync · Park | These failed 3 checks in a row | [Re-sync, then Park](#when-a-recording-fails-every-check-re-sync-and-park) |
| The sync's records were rebuilt: 6 recordings wait for your review on the Sync screen. | `state.db` was rebuilt or restored | [Review them](#after-the-syncs-records-were-rebuilt-needs-your-review) |
| Scheduled sync is off since 2026-10-23 14:20. / Scheduled sync has been off since …: set [plaud] schedule_minutes to turn it back on, or pause the sync's Healthchecks check (runbook). | `schedule_minutes` is 0 | Set it back to 20 and `rc restart worker`, if the pause is over |
| Auto-import is off: new recordings wait for you on the Sync screen. | `auto_import` is false | Import on the Sync screen, or set it back to true |
| Plaud sync is idle: configuration: `keep_out_word` is invalid. | The keep-out word in `config.toml` isn't valid | Fix `[plaud] keep_out_word` (1 to 32 letters, digits, `-` or `_`), then `rc restart worker` |
| The Plaud worker hasn't reported yet: check that the worker container is running. / The Plaud worker is silent (last reported 14:21): check that the worker container is running. / The Plaud worker may be stuck … / The Plaud worker couldn't start (`Class`): see `rc logs worker` (runbook). | The worker is down, silent, making no progress, or failed at start | `rc ps`, `rc logs worker` |
| Sync everything has run since 14:20: it may be stuck. (or another job) | A long Plaud run has held the sync past 2 hours (12 for Sync everything and the catch-up) | `rc logs worker`; `rc restart worker` stops it, and it resumes |
| The sync's records in state.db can't be read now (`Class`): check the worker's log. | `state.db` is busy or damaged | `rc logs worker`; the runbook's "Restoring for real" if it is damaged |
| Plaud sync retired on …: nothing more is fetched from Plaud (runbook, DAN-15) | You retired the sync when the account ended | Nothing |

### Backups and the disk

| Status says | It means | Do |
|---|---|---|
| The last backup is more than 2 hours old. / No backup has finished yet. | The hourly backup isn't landing | Status's Backup row says why; `rc logs backup` |
| Restore test (or Repository check, Mirror for the Mac, Retention) is overdue: it last worked … | That job hasn't run on time | `rc logs backup`; the runbook's sections 2 and 5 |
| Backup failed: <reason> (or another job) | Its newest run failed | Read the reason; the runbook has each job's section |
| Restore test has been running since …, and no backup runs until it ends: check that it isn't stuck. | A long job has held the backups past its limit | `rc ps`, `rc logs backup` |
| The backup service last reported …: check that it is running. | The backup service went quiet | `rc ps`; `rc restart backup` |
| There is no state.db yet, so no backup can be recorded: … | `recordings init` never ran, or the state folder isn't mounted | The runbook's section 2 |
| 2 files need attention: run recordings reindex. | Files edited outside the app that couldn't be merged | `rc run --rm web recordings reindex` |
| 12.3% free: below 20% / 4.1% free: below 5%, new imports stop | The data disk is filling | Free space (the runbook's ["When the data disk fills"](runbooks/first-run.md#when-the-data-disk-fills)) |

## What the sync compares, and the equations

Each sync, and each **Compare**, reads Plaud's whole list of recordings and puts every recording
in one of four groups. The numbers always add up.

**What you see**

- "Listed 120 = 98 here + 3 waiting + 14 excluded + 5 missing":
  - **here:** already in the archive, under either form of its Plaud ID.
  - **waiting:** comes in at the next sync, by itself.
  - **excluded:** never comes in. It is on your consent list, its Plaud title starts with your
    keep-out word, it was kept out by an earlier title, or it may be one of those back under a new
    Plaud ID. Shown by date, time and duration, never by title.
  - **missing:** won't come without you. Its line says why: the first catch-up hasn't run, the
    consent list can't be read, the sync is held, it needs your review, it failed 3 times, the
    disk is too full, or the schedule or auto-import is off.
- "In the archive from Plaud: 100 = 98 here + 1 gone from Plaud + 1 absent from this listing":
  recordings you have that Plaud no longer lists. "Absent" turns into "gone" once the next listing,
  an hour or more later, agrees. Nothing is ever deleted from the archive.
- "Titles hidden until the consent list reads." or "Titles hidden while Plaud's IDs look changed.":
  the Sync screen shows dates and durations only, so no title of a recording you may have kept out
  is ever stored.

**What to do**

- Nothing, usually. Waiting recordings come in at the next sync.

## What the sync commands' exit codes mean

`recordings plaud sync` (and `--dry-run`, `--catch-up`, `--full`) and `recordings plaud resync`,
run on the server as `rc exec worker recordings plaud …` or through the make targets, end with a
number that says how it went. The make targets turn each into a sentence. For `sync --full`
(Sync everything), 0 means every recording on Plaud is here and checked, parked, flagged or kept
out; 2 names the rest by count.

| Code | Meaning | What to do |
|---|---|---|
| 0 | Done: the listing was complete, and everything that should have come in did | Nothing |
| 1 | It didn't run, or stopped: a hold, a listing that couldn't be read, the first catch-up not run yet (or already done), or the archive's disk or `state.db` | Read the line above it; the runbook's section 8 has each |
| 2 | Partly: the listing was good, but some recordings failed or didn't come in | Nothing at once: the scheduled sync retries them. Check any that keep failing in Plaud's app, then Park them |
| 69 | Plaud couldn't be reached, or asked the server to slow down | Run it again later |
| 75 | Another Plaud run holds the sync (Status says which) | Run it again when that one ends |
| 77 | The Plaud sign-in is needed | At the Mac: `make plaud-sign-in` |
| 78 | The consent list stops imports, or a setting is wrong | Fix the lines or the setting it names |
