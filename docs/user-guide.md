# Using recordings

A plain how-to: one section per screen or task, with what you see and what to do. The runbook
(`docs/runbooks/first-run.md`) has the server-side detail; this guide links to it rather than
copying it. Stage 2b's sections are added as each part is built, and finished at its end.

## Trying the Plaud sync on the Mac: `make demo-live`

Before the server's first real sync, or any time you want to try the screens without touching
Plaud or the archive, run the real web app and worker on the Mac against a fake Plaud server.

**What you see**

- `make demo-live` builds the UI, runs a first catch-up of seven invented recordings (about 25
  seconds: it paces one call a second, as the real sync does), then prints "Open
  http://127.0.0.1:8000/#/add" and what to try. `make demo-live PORT=8001` moves it.
- The fake account: six ordinary recordings and one private one, already here; one on the consent
  list and one whose title starts with `noarchive`, both kept out; and three new ones waiting
  (one private).
- Everything lives in one folder under `$TMPDIR`, removed when you stop it. It never reads your
  `config.toml`, your settings or a sign-in, and refuses to start if anything points elsewhere.

**What to do**

Type a command and Enter in the terminal, then look at the app:

| Command | What happens | Then |
|---|---|---|
| (none) | Three recordings wait on Plaud | **Sync now**, or tick some and **Import selected** |
| `new` | Two more arrive (one private) | **Compare**, or **Sync now** |
| `hold` | Plaud stops listing six recordings that are here | **Sync now**: the sync holds; **Accept this listing**; `back` undoes it |
| `rename` | The kept-out recording's title loses `noarchive` | **Compare**, then **Release** on its row |
| `late` | A recording here gets `noarchive` in its title | **Sync now**: a late keep-out flag, with **Keep it here** |
| `signout`, `signin` | Plaud refuses the sign-in; a new one | `tick`, then Status |
| `tick` | The scheduled sync runs now, as if 20 minutes had passed | the Library's header counts what came in |
| `catch-up` | With `ARGS=--before-catch-up`: the first catch-up | before it, only **Compare** works |
| `dump` | Writes the screens' data as JSON (start with `ARGS=--keep` to keep it) | |

Ctrl-C (or `quit`) stops the web app, the worker and the fake server, and removes the folder.

## Signing in to Plaud

The server's sync reads your recordings from Plaud with a sign-in that only the server's `worker`
holds. You make it from the Mac, and the server keeps it fresh by itself.

**What you see**

- **Status** says "sign-in needed" when the server has no sign-in, or Plaud has refused it. New
  recordings wait on Plaud until you sign in again; nothing is lost.
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
nothing was installed. The runbook's "Plaud sign-in" has every message.

The sign-in is a secret. Never copy its file by hand or paste it anywhere, and don't ask a Claude
session to run `make plaud-sign-in` for you: you sign in yourself, in the browser.

## The first catch-up: dry run first

Until the first catch-up has run, the server syncs nothing by itself: Status says "Scheduled sync
waits for the first catch-up (runbook)", and only **Compare** works on the Sync screen. The
catch-up is the first real import from Plaud, and you run it yourself, on the server, after a dry
run. The runbook's sections 7 and 8 have every step; this is what they come to.

**What to do**

1. **The dry run.** On the server, in the repo's checkout: `make plaud-dry-run`. It reads Plaud's
   whole list and classifies every recording on it, and imports nothing. It prints both
   equations, why each missing recording is missing ("waits for the first catch-up"), the consent
   list's count and how many of its IDs Plaud lists, the consent list's lines read as kept, and
   any hold, then every listed recording by Plaud ID, date, time and duration (never a title).
   Check that the numbers make sense: the missing ones are the recordings made since the 2a
   import, and "here" is the archive's Plaud recordings. If anything doesn't fit, stop.
2. **Review what the consent list caught.** Any recording 2a imported that the consent list
   names is already tagged `private` and flagged: review each in the app, and take out by hand any
   that mustn't stay, before the next step.
3. **The catch-up.** Inside `tmux` on the server: `make plaud-catch-up`. It takes a backup tagged
   `pre-import`, checks every title once (the baseline), imports everything waiting, with one line
   per recording ("importing 5 of 31"), validates the archive, and takes a `post-import` backup.
4. When it says "done", **you may start tagging**. The schedule starts by itself: within 20
   minutes the sync's Healthchecks check shows up, and Status shows "All here", or why not.

**What you see if it goes wrong**

- "Plaud sync paused: …" (held): Plaud's list looks very different from the archive's. Read the
  runbook's section 8, step 4: check, then `rc exec worker recordings plaud accept-listing`, and
  run `make plaud-catch-up` again.
- Stopped part-way (Plaud down, the connection dropped, Ctrl-C): Status says "The first catch-up
  didn't finish: run `--catch-up` again (runbook)". Run `make plaud-catch-up` again: it imports
  only what isn't here yet.
- "some recordings failed": the catch-up is done anyway. The scheduled sync tries them again;
  any that keep failing wait under **Not here yet** as "failed 3 times · Park".

## What the sync commands' exit codes mean

`recordings plaud sync` (and `--dry-run`, `--catch-up`), run on the server as `rc exec worker
recordings plaud …` or through the make targets, end with a number that says how it went. The
make targets turn each into a sentence.

| Code | Meaning | What to do |
|---|---|---|
| 0 | Done: the listing was complete, and everything that should have come in did | Nothing |
| 1 | It didn't run, or stopped: a hold, a listing that couldn't be read, the first catch-up not run yet (or already done), or the archive's disk or `state.db` | Read the line above it; the runbook's section 8 has each |
| 2 | Partly: the listing was good, but some recordings failed or didn't come in | Nothing at once: the scheduled sync retries them. Check any that keep failing in Plaud's app, then Park them |
| 69 | Plaud couldn't be reached, or asked the server to slow down | Run it again later |
| 75 | Another Plaud run holds the sync (Status says which) | Run it again when that one ends |
| 77 | The Plaud sign-in is needed | At the Mac: `make plaud-sign-in` |
| 78 | The consent list stops imports, or a setting is wrong | Fix the lines or the setting it names |

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
  email comes, check that the server is online (the runbook's "When the Plaud alert comes").
- Refused access: open Plaud's app and check the subscription.
- A long recording that keeps failing to download (the server resumes a download that drops, a
  few times, then gives up until the next sync) is worth a note in a Claude session: the server's
  download rules were set without measuring Plaud's.

## How a recording comes in

Every sync brings in what is waiting, by itself. You never confirm an import.

**What you see**

- A new recording in the Library's **Untagged** list, with Plaud's title (even a placeholder such
  as "2026-11-02 14:05:31"), Plaud's transcript and notes if it had them yet, and its time in your
  default time zone.
- A title that matches your private patterns ("Private: …", "interview: …") arrives already
  tagged `private`: the tag lands before any of its content does.
- A recording comes in whole or not at all. One that fails is tried again at the next sync; after
  3 failures in a row it waits under **Not here yet** as "failed 3 times · Park".

**What the sync checks first, every time**

- The consent list, read again just before each recording: an ID you added a moment ago is never
  fetched. If the list can't be read, nothing is imported until it can.
- The keep-out word, on the title Plaud lists and again on the title Plaud gives when the sync
  asks for the recording: a match fetches nothing more of it, and keeps it out.
- The disk: if the recording would leave less than the stop margin free, imports stop for that
  sync, and Status says "the disk is too full to import". Free some space; the next sync goes on.
- The same audio as a recording you have kept out (consent-listed, or kept out by its title) is
  never added: it is kept out too, and flagged "may be a … recording back under a new ID".

**What to do**

- Nothing, usually. To bring something in sooner, press **Sync now** on the Sync screen.
- A recording that keeps failing: check it plays in Plaud's app, then **Park** it so it stops
  counting against "All here".

## The scheduled sync

Once the first catch-up has run, the server syncs by itself every 20 minutes (`[plaud]
schedule_minutes`). Each sync reads the consent list, checks the sign-in, reads Plaud's whole list,
checks every listed title of what you already have against your private patterns and the keep-out
word, compares, and brings in what is waiting.

**What you see**

- New recordings arrive newest first, at most 20 per sync; a longer backlog comes over the next
  syncs, by itself.
- A recording that failed waits a little before the next try (20 minutes, then 40, and so on), so
  one bad recording never slows the others down.
- **Status** says what stopped a sync, in a fixed phrase: "sign-in needed", "Plaud unavailable",
  "offline", "rate limited until 14:40", "imports stopped: consent list", "imports stopped: the
  disk is too full to import", "held: see Status", "every fetch failed", or "partial: 2 recordings
  failed". None of them ever names a title.
- The sync's Healthchecks check is pinged only after a good sync that nothing holds up. A sync
  that met only failures, or none but recordings that failed before, doesn't ping until something
  comes in again; if that lasts about 2 hours, Healthchecks emails.
- A deploy or a restart in the middle of a sync stops it between two recordings; nothing is
  half-written, and the next sync carries on.

**What to do**

- Nothing, usually. Status's line says what to do for each stop (the runbook's "When the Plaud
  alert comes" has the rest).
- To turn the schedule off for a while (maintenance), set `[plaud] schedule_minutes = 0` and
  restart the worker; set it back to 20 afterwards. While it is off, the check emails after 2 hours.

## The Sync screen's buttons: Compare, Sync now, Import, Park

The Sync screen (**Add**) asks the server's worker to do something now. Every press is answered
within a second ("Queued"), and the screen follows it until it ends; the newest result line stays
on the screen, so a phone that slept through the toast still sees it.

**What you see**

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
  the end: "31 imported · 4 arrived private", with **View**, which opens Untagged.
- Pressing **Sync now** or **Compare** again while one is still queued answers "Already queued":
  one sync answers all the presses. **Sync now** pressed while a Compare is queued turns that
  Compare into the sync. Imports queued together run as one.
- While a long run holds the sync on the server (the first catch-up, say), a press waits its turn
  ("Queued after the current sync (started 14:20)"). If nothing runs it within the hour (the
  worker is down, or the long run takes longer), it expires and says so: press it again then.
- **Park**, on a row that "failed 3 times", stops it counting against "All here", and syncs leave
  it alone. **Unpark**, under Parked, puts it back as it was; **Import selected** tries it again
  whenever you like.

**What to do**

- Nothing, usually: the scheduled sync does all of this every 20 minutes.
- A recording that keeps failing: check it plays in Plaud's app, then **Park** it.

## The Sync screen, top to bottom

The **Add** tab is the Sync screen: it acts, and Status reports. Open it from the top bar
(`#/add`). On a phone it is the same, one column, with buttons the width of the screen.

**What you see**

1. **The top line**, the same one Status shows:
   - "All here · checked 14:20 · next sync 14:40", with "Every recording on Plaud is in the archive
     or deliberately left out." under it, when the last good sync is recent and nothing waits.
   - "2 waiting · 1 missing · checked 14:20 · next sync 14:40" when something hasn't come in yet.
   - Or what stops the sync, in one sentence ("Sign-in needed", "Held: see Status", "Scheduled sync
     waits for the first catch-up (runbook)"), with **See Status**.
2. **Flags**, each by recording ID (or Plaud ID) with its next step: a recording kept out by title
   after it came in (**Keep it here**, or take it out by hand), one added to the consent list after
   it came in, a `private` the sync couldn't write, and a Plaud ID that "may be a consent-listed
   recording back under a new Plaud ID", with its date and duration. Never a title.
3. **Held**, when a listing is held: its sentence, and **Accept this listing** or why it can't be.
   **Edit-sync paused**, when the breaker tripped: its sentence with the fields' names, and
   **Accept these changes** or why it needs a code fix ("When edit-sync pauses", below).
4. **Sync**: **Sync now** and **Compare**, and under **More**, **Sync everything** (it comes with
   the next part of 2b). The newest result line stays here ("Sync now, 14:20: Synced: 2 imported
   (1 private) · All here"), and a running request shows its progress ("Importing (2 of 5)").
5. **From the listing**: the equation, each of its numbers a jump to its list; "from the listing
   at 14:20"; the archive's own equation; and the consent list's line ("Consent list: 14 IDs, 1 on
   Plaud now · file changed … · read 14:20").
6. **Not here yet**: one row per recording, newest first: a box to tick, the date, time and
   duration, the title (with a lock and "arrives private" when its title matches your private
   patterns), and why it waits ("imports at the next sync", "failed 3 times" with **Park**, "needs
   your review", …). **Copy ID** copies the ID the way the consent list takes it. **Import
   selected** and **Import all 31** bring them in now. While the consent list can't be read, or a
   held listing shows Plaud's IDs changed, no titles show: only dates and durations.
7. **Kept out**: by the consent list, by title, "was kept out by title" (with **Release**), and
   "may be … back under a new ID" (with **Release**, if it is a different recording). Each by Plaud
   ID, date, time and duration; never a title. A released row reads "Released · comes in at the
   next sync (14:40)" with **Undo**.
8. **Updated on Plaud (7 days)**: recordings here that changed on Plaud, by title, with what
   changed ("speakers", "title", "notes", "own notes", "other", "removal held") and when, and
   those the sync re-tagged private. Once a check has run this week and found nothing: "Nothing
   changed on Plaud this week."
9. **Gone from Plaud**: recordings you have that Plaud no longer lists, newest first, with "gone
   since"; nothing is ever deleted from the archive. **Parked**: what you parked, with **Unpark**.
10. **Import your own audio**: coming later.

**What to do**

- Nothing, usually: the scheduled sync keeps it all up to date.
- Before the first catch-up only **Compare** works; every other button says why.

## Status's Plaud sync panel

Status reports; it never names a recording by its title, only by its ID.

**What you see**

- The same top line as the Sync screen, then plain sentences, each only while it is true: the sync
  failing ("Plaud sync failing since 12:00 (Plaud unavailable). Last good listing 11:40."), "isn't
  being pinged since …", the schedule or auto-import off, a small shrink ("Plaud now lists 3
  fewer: waiting for the next listing to agree"), and a hold with **Accept this listing**.
- **Needs attention**, at the top of Status: a sign-in needed ("Plaud sign-in needed since 23:00.
  New recordings wait on Plaud: nothing is lost. At the Mac: `make plaud-sign-in`."), a sign-in
  that expires within 7 days, a hold, a possible re-keyed recording to confirm, a consent stop, a
  late keep-out or consent listing, a `private` the sync couldn't write, recordings waiting for your
  review after a rebuild, edit-sync paused (the breaker), the worker silent, a long-held sync
  "may be stuck", and a ping failing.
- **The numbers**: the equations and the consent list's line; the last good listing ("14:20 (120
  listed)") and the next sync; the sweep ("every recording checked within the last 21 hours
  (oldest 2026-11-01 17:10) · an edit in Plaud shows within about 25 hours"); the sign-in ("works (last used 14:20) · access token expires 15:20 ·
  refresh token"); held, gone from Plaud, failing and parked; when the worker last reported; and
  Healthchecks' last ping.
- **Details**, folded away: the normaliser's version, the fields outside the named parts that
  changed in the last 24 hours (names only), the changes per part in 24 hours, the ID forms Plaud
  uses (and when they last changed form with nothing lost), the keep-out words in force, malformed
  listing rows, and the last 20 runs with their results.

**What to do**

- Each line says its own next step; the runbook has the detail.

## Renaming a recording in Plaud after it came in

Each sync reads every title Plaud lists, and checks the ones that changed since it last looked.

**What you see**

- Renamed to a private title ("Private: …", or anything your patterns match): it is tagged
  `private` at the next sync, even if Plaud won't hand over its transcript. Details says "Tagged
  private by the sync: its Plaud title changed".
- Renamed to start with your keep-out word: too late to keep it out, so it is tagged `private`
  at once, nothing more is ever fetched for it, and it is flagged on Status, the Sync screen and
  the Library's header ("1 kept out too late"), by its archive ID.
- A `private` you removed stays removed. It comes back only if the Plaud title changes again to
  one that matches.
- "Should be private, but its file was edited outside the app: run `recordings reindex`": the tag
  couldn't be written. The sync's check stops being pinged until it lands.

**What to do**

- A late keep-out: take it out by hand (the runbook's "Taking a recording out by hand"), or press
  **Keep it here** to keep it. It stays private either way, and a later title with the word won't
  flag it again.
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
- **Updated on Plaud (7 days)**, on the Sync screen: each recording that changed, by its title,
  with what changed ("speakers", "title", "notes", "own notes", "other", "removal held") and when.
  With nothing changed it says "Nothing changed on Plaud this week."
- **Details** of a recording: "Plaud: checked 2026-11-02 14:20 · next by 2026-11-03 14:20".
- **Status's numbers**: "every recording checked within the last 21 hours (oldest 2026-11-01
  17:10) · an edit in Plaud shows within about 25 hours". Its details add the changes per part in
  the last 24 hours, and the names of any fields outside the named parts that changed.
- **Never checked:** a recording kept out (by title, or by Keep it here) or on the consent list,
  one gone from Plaud, and one you parked. Nothing more is ever fetched for those.
- **A degraded check**: Plaud answered, but a note or the transcript behind one of its links
  didn't arrive, twice. The sync keeps that answer once, builds nothing from it, and tries again at
  the next check. One degraded recording is nothing to do; if every check is degraded, the sync
  stops being pinged ("every fetch failed") and the runbook's alert table says what to look at.
- **A recording that fails every check** (3 in a row) shows on Status's top lines: "1 recording
  fails every check (since 2026-11-02)". The sync keeps trying it, less often each time, and checks
  a healthy recording beside it, so one bad recording never stops the ping.

**What to do**

- Nothing: edits arrive by themselves.
- A removal (a name going back to "Speaker 1", a note vanishing) is held for your review, never
  applied: the review list comes with stage 3a.

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
  press **Accept these changes**: each recording gets one new snapshot over the next day, and the
  breaker closes.
- If a name looks like a link, a token or a key, or Status says it needs a code fix: open a Claude
  session with Status's sentence. Don't accept.
- On the server, `rc exec worker recordings plaud breaker` says the same as Status, and
  `recordings plaud breaker reset` closes it by hand (no runbook step needs it).

## Keeping a recording out from your phone, and Release

Start a recording's title in Plaud's app with your keep-out word (`noarchive` unless you chose
another) and the sync never downloads it. The word is forgiving: "NoArchive:", "No archive" and
"[noarchive]" all count; "noarchived" doesn't.

**What you see**

- The Sync screen lists it under **Kept out**, by date, time and duration, never by title.
- If the title later loses the word (Plaud's AI retitles it, or autocorrect), it stays out, as
  "was kept out by title", with **Release**.
- If the sync downloaded it before the rename reached Plaud, see "Renaming a recording in Plaud
  after it came in": too late, so it is tagged `private` and flagged.

**What to do**

- To let one in after all: press **Release** on its row. It asks first, and only there shows the
  title: "Let '…' (date, length) in? It downloads at the next sync, and then stays in the
  backups." **Let it in**, or Cancel.
- Release is refused, with the reason, while the title still starts with the word ("its title
  still starts with `noarchive`": rename it in Plaud first), while the recording is on the
  consent list, and while the consent list can't be read.
- Until the next sync fetches it, the row reads "Released · comes in at the next sync (14:40) ·
  Undo": **Undo** keeps it out again.
- A row "may be a … recording back under a new ID" that is really a different recording: press
  **Release** too. Its question shows the title Plaud lists now.
- On the server, `rc exec worker recordings plaud release <Plaud ID>` does the same, showing only
  the ID, date and duration and asking y/N; `--undo` keeps it out again.

## After the sync's records were rebuilt: needs your review

The sync remembers what you kept out in its own records (`state.db`). If those are lost and
rebuilt, or restored from an older backup, it can't know what you kept out since, so it asks.

**What you see**

- Status and Needs attention: "The sync's records were rebuilt: 6 recordings wait for your
  review on the Sync screen". No email: the sync goes on.
- On the Sync screen's **Not here yet**, every Plaud recording made before the rebuild that isn't
  here reads "needs your review: the sync's records were rebuilt", by date, time and duration.
  Its title shows only when you ask (the same question Release asks).
- Recordings made after the rebuild arrive as usual. **Import all** and the scheduled syncs leave
  the review rows out ("Import all 31 · 6 more need your review").
- A `private` tag you removed may come back once, with "the sync's record was rebuilt" in
  Details. That errs private.
- If the first catch-up stopped part-way, Status says "The first catch-up didn't finish: run
  `--catch-up` again (runbook)".

**What to do**

- For each review row: tick it and press **Import selected** to bring it in, or keep it out (add
  its ID to the consent list with `make consent-add`).
- After restoring `state.db` from a backup, the runbook's "Restoring for real" runs
  `rc run --rm worker recordings plaud review`, which does the marking (the sync can't tell an
  older `state.db` by itself).

## What the sync compares, and the equations

Each sync, and each **Compare**, reads Plaud's whole list of recordings and puts every recording
in one of four groups. The numbers always add up.

**What you see**

- "listed 120 = here 98 + waiting 3 + excluded 14 + missing 5":
  - **here:** already in the archive, under either form of its Plaud ID.
  - **waiting:** comes in at the next sync, by itself.
  - **excluded:** never comes in. It is on your consent list, its Plaud title starts with your
    keep-out word, it was kept out by an earlier title, or it may be one of those back under a new
    Plaud ID. Shown by date, time and duration, never by title.
  - **missing:** won't come without you. Its line says why: the first catch-up hasn't run, the
    consent list can't be read, the sync is held, it needs your review, it failed 3 times, the
    disk is too full, or the schedule or auto-import is off.
- "In the archive from Plaud 100 = here 98 + gone from Plaud 1 + absent from this listing 1":
  recordings you have that Plaud no longer lists. "Absent" turns into "gone" once the next listing,
  an hour or more later, agrees. Nothing is ever deleted from the archive.
- "titles hidden until the consent list reads" or "titles hidden while Plaud's IDs look changed":
  the Sync screen shows dates and durations only, so no title of a recording you may have kept out
  is ever stored.
- "may be a consent-listed (or kept-out) recording back under a new ID: confirm": Plaud lists a
  new ID with the same start time and duration as one you kept out. It stays out, even if it
  first appeared while the consent list couldn't be read.

**What to do**

- Nothing, usually. Waiting recordings come in at the next sync.
- A recording "back under a new ID": if it is the one you kept out, add the new ID to the consent
  list (`make consent-add ID='…'`; a recording kept out by its title simply stays out). If it is a
  different recording, press **Release** on its row.

## When the sync is held: Accept this listing

If Plaud's list suddenly looks very different from the last good one, the sync stops importing
until you look. That protects the consent list: if Plaud changes its IDs again, a recording you
kept out could otherwise come back under a new ID.

**What you see**

- **Status** says "Plaud sync paused", with what changed, and offers **Accept this listing**:
  - "Plaud's IDs changed form, and 12 recordings here vanished with them", or "only 15% of the IDs
    Plaud listed last time are listed now": Plaud changed its IDs.
  - "Plaud now lists 40 fewer of the recordings here (and 38 look new)": many recordings vanished
    at once.
  - "Plaud lists 3 IDs in a form the consent list can't name. It needs a code fix": Accept can't
    help; ask a Claude session to look.
- **Sync now** and the import buttons are off, and say "Held: Plaud's listing is held (see Held,
  above)"; **Compare** still works.
- About 2 hours later, Healthchecks emails that the sync's check is down.
- A few recordings vanishing (5 or fewer, or under 5% of the archive's) isn't held: Status says
  "Plaud now lists 3 fewer: waiting for the next listing to agree", and an hour later they show as
  gone from Plaud by themselves.

**What to do**

1. Check in Plaud's app why the recordings vanished: if you deleted them, that's the answer.
2. If Plaud's IDs changed, follow the runbook's steps first: every consent-list entry must still
   name its recording.
3. Press **Accept this listing** on Status (or the Sync screen). The vanished recordings show as
   gone from Plaud, and imports resume. On the server, `rc exec worker recordings plaud
   accept-listing` does the same; it is also how the first catch-up's own listing is accepted, if
   that one is held.
4. If Accept is refused with "Fix this first: 4 consent-listed or kept-out IDs vanished while
   Plaud's IDs changed (runbook)", don't work around it: some recordings you kept out may be back
   under IDs your consent list can't name. The runbook says how to find them.

A hold also clears by itself if the next listing looks normal again (Plaud put things back).
