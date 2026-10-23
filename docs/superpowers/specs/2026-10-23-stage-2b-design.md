# Stage 2b: Plaud flows in and keeps up (design)

- **Date:** 2026-10-23 (revision 2, after the council's second review)
- **Status:** draft for Dan's approval. Dan's seven decisions are binding and kept as he gave them.
  The rest is the controller's design, from a five-member research council, revised after two
  five-lens reviews. Calls made without asking Dan are listed in §5, by what he will notice. One of
  them needs his OK.
- **Parent:** `docs/superpowers/specs/2026-10-08-recordings-design.md` (the main spec), §20 stage 2b.
  §19 lists the changes this spec makes to it and to other docs.

**How to read this.** Part I is everything Dan will see, do or accept, in about 470 lines. Part II is
how it is built, for the builder. Part I is binding: Part II says the same thing in more detail, and
each part of Part I points into Part II.

# Part I: For Dan

## 1. What this is for

`recordings` is where Dan's recordings live for good: on his own hardware, as plain files he can
read, with Dan, not a vendor, deciding what each one is and who may read it (main spec §1). Plaud is
how he records today. Its cloud is rented until 2027-08-29 (main spec §22, decision 7). Stage 2b is
where Plaud's recordings start to flow into the archive on their own, and keep flowing.

**Why now.** `audio-router`'s Plaud sync has rejected every recording since about 2026-09-25, when
Plaud changed its IDs to `of_<32 hex>`. Nobody was told. By 2026-10-08 about 30 recordings were
missing, and Dan chose no stopgap (main spec §19). Meanwhile the relabelling period has begun: Dan
fixes speaker names in Plaud before the subscription ends (DAN-15), and expects them to come down.
And tagging part A has shipped, waiting for 2b's first import (decision 7).

**What success feels like.**
- Dan records a meeting. By the time he sits down, it is in the Library's Untagged list, with
  Plaud's title, and Plaud's transcript if Plaud has made one. He didn't click anything.
- He never opens Plaud's app to check that something arrived. Status says "All here", with the time
  it was checked.
- A private title arrives private. A recording he keeps out from his phone never arrives. A
  `private` tag he removed stays removed.
- A speaker he renames in Plaud shows up within a day and one sync (about 20 minutes more) while he
  relabels, and within a week and one sync after that. **Re-sync from Plaud** shows it at once.
- When something breaks, his phone tells him once, and says what to do. Otherwise it is quiet.
- Keeping it running is almost no work. If Plaud signs the server out, signing in again is one
  command on the Mac.

**What would make 2b a failure, even if every test passes.**
1. **A recording that must never be kept is kept,** in write-once media, the backups and the Mac's
   mirror, and Dan doesn't know.
2. **It stops again, and nobody notices:** an expired sign-in, a dead worker, a tripped breaker or a
   schedule left off, silent for weeks.
3. **Dan still checks Plaud by hand.**
4. **Alert fatigue:** pushes for routine blips teach him to mute the topic.
5. **His decisions don't hold:** a tag he removed comes back, or a recording he kept out arrives.
6. **Upkeep becomes a chore,** or the server's Plaud sign-in fights with his Mac's Plaud tools.
7. **Plaud's copy changes.** The client only ever reads.
8. **There is no way back:** a 2b deploy can't be rolled back by its tag.

## Dan's decisions (2026-10-23)

He answered each question as a scenario, choosing from three options. His choice is quoted as he picked it.
He gave no further reasons, so the reasons below come from the options as they were put to him.

1. **A new recording with a placeholder title: "Download now, tag later"** (not the recommended "wait up
   to 30 min").
   - A new recording is downloaded at the next sync even while its Plaud title is still a placeholder.
   - If Dan later renames it to a title that matches his private patterns, `private` lands at the next
     sync. Each sync re-checks the titles of recent recordings, so the rename lands within one sync.
   - This is a deliberate exception to "the private tag lands before any source or output": until the
     rename is synced, a placeholder-titled recording sits untagged in the archive, the backups and the
     mirror. An untagged recording is already hidden from external agents (main spec §7.4).
2. **Keeping a recording out from the phone: "Title marker"** (recommended).
   - A Plaud title that starts with a word Dan chooses (configurable, for example `noarchive`) is never
     downloaded. The Sync screen lists it as kept out by title.
   - If the sync downloaded it before the rename, the next sync tags it `private` at once and flags it
     for Dan to delete by hand.
   - Deleting the recording in Plaud's app, or adding its ID to the consent list, still work too.
3. **A `private` tag Dan removed: "Stays removed"** (recommended).
   - The sync remembers which Plaud title it last checked for each recording, and re-checks only when
     that recording's title changes in Plaud. Then it may re-tag the recording, with a note saying why
     ("re-tagged private: title changed").
   - This fixes the auto-private residual carried from stage 2a, which tagging part A made visible.
4. **A dead sync worker: "Second check"** (recommended).
   - The sync gets its own Healthchecks dead-man's check, pinged after every good run, with its own
     secret file. It isn't shared with the backup's check, so neither job can hide the other's death.
   - It is a one-time setup step.
5. **Plaud's routine errors: "Push if it lasts 2h"** (recommended).
   - There is no push for a short blip. A push comes when there has been no good sync for about 2 hours.
   - A refused sign-in (a refresh Plaud refuses), a tripped circuit breaker and a consent stop push at
     once.
   - Status shows every error the whole time.
6. **Speaker fixes in bulk: "Re-sync a selection"** (recommended).
   - Select rows in the Library (the selection from tagging part A) and press **Re-sync from Plaud**
     once: one request, with progress.
   - The background sweep, per-recording Re-sync and **Sync everything** stay.
7. **When tagging the real archive may start: "After import + backup"** (recommended).
   - This refines Dan's 2026-10-22 decision. The first real tag waits for step 7's first real Plaud import
     and the backup after it, not just for the dry run, because the dry run only reads.
   - A rollback of step 7 then loses no tags. The README and the runbook change to say so.

## 2. Your days with 2b

Each journey says what Dan does, what he sees, and what happens when it goes wrong. The Add page's
Plaud panel is the main spec's **Sync screen**, and this spec calls it that.

### J1. A meeting, then triage

1. Dan records a meeting. Plaud's phone app uploads it, with a placeholder title (a timestamp).
2. At the next sync, at most 20 minutes after Plaud lists it, the worker downloads it, placeholder
   title and all (decision 1). It arrives whole or not at all, with Plaud's transcript and notes if
   they exist yet. Those that come later arrive at the following syncs: a new recording is checked at
   every sync while Plaud is still working on it.
3. If the Library is open, nothing moves. The list's header says "2 new · 1 private from Plaud ·
   Refresh", and rows move only when he presses Refresh (tagging part A's rule).
4. The recording is in **Untagged**, newest first, with Plaud's title and a time in the default zone
   (Plaud gives none). He opens it: the player, Plaud's transcript and names, and in Details,
   "Plaud: checked 14:20 · next by 14:40" and **Re-sync from Plaud**.
5. `T`, `work/lander`, Enter. Nothing is processed until stage 4.

**When it goes wrong:** the Sync screen says why a recording isn't here yet, in one line: "imports at
the next sync", "failed 3 times: see Status", "paused: see Status", "the disk is nearly full".
Part II: §11, §15.

### J2. Something private, or something that must not be kept

There are three needs, and three tools.

**Private: keep it, local models only.** Dan titles it "Private: …" (or anything his patterns match,
such as `interview:`). It arrives tagged `private` before any of its content lands. If he renames an
arrived recording to a private title, `private` lands at the next sync (decision 1), even if Plaud
won't hand over its transcript. Until then it carries whatever tags he gave it; with none, external
agents already treat it as private. This is decision 1's deliberate exception to "the private tag
lands before any content does", so approving this spec changes CLAUDE.md's privacy rule to read
(§13.3):

> The private tag lands before any source or output does, judged on every title the app has seen
> at that moment. One deliberate exception (stage 2b, Dan, 2026-10-23): a Plaud recording renamed
> to a private title, or to the keep-out word, after it came in is tagged `private` at the next
> sync, so its source and outputs landed first. Until then it carries whatever tags Dan gave it;
> with none, external agents already treat it as private.

**Never keep it, decided in the moment: the keep-out word** (decision 2). Right after recording, on
his phone, Dan renames it to start with `noarchive` ("noarchive coffee chat"). The sync never
downloads it. The Sync screen lists it under **Kept out**, by date, time and duration, never by
title. To confirm, he presses **Compare**. The word is forgiving: "NoArchive:", "No archive" and
"[noarchive]" all count, but "noarchived" doesn't. A phone text replacement makes it one tap. Being
forgiving, it can also catch a title Plaud's AI writes, such as "No archive access: IT ticket". That
recording sits under Kept out until Dan releases it, which errs safe.
- **Kept out stays kept out** (controller's call). If the title later loses the word (Plaud's AI
  retitles it after he asks for a summary, or autocorrect mangles it), it stays out. Only Dan lets it
  in, with **Release** on its row in the Sync screen. Release asks first, showing the recording's
  title on screen (nowhere else): "Let this recording in? It downloads at the next sync (14:40), and
  then stays in the backups." Until that sync, the row offers **Undo**. If the title still starts with
  the word, Release is refused, with the reason.
- **Too late.** If the sync already downloaded it (the rename came after the next sync), the sync tags
  it `private` at once, never fetches anything of it again, and **pushes once**: "a kept-out
  recording was already archived". Status names it by its archive ID. He takes it out by hand
  (below).

**Never keep it, for good: the consent list.** Someone asks not to be recorded or kept. Dan adds the
recording's Plaud ID to the consent list, on the server: from the Mac, `make consent-add ID=…`
checks the ID and adds it, or he edits the file there. The Sync screen and Details show each ID with
a copy button. The list's IDs are never downloaded, and no title of theirs is ever stored or shown.
- **What the list means is unchanged:** a line that is an ID alone, or an ID then a dash and a note,
  keeps that recording out. An ID followed by other words ("ID kept: she agreed later") or inside a
  sentence names a recording Dan kept. `recordings plaud consent` lists those lines by number, so he
  can check each one.
- **A typo stops imports.** A line that looks like an ID but can't be read stops every import, and
  pushes at once. Status gives the line number, never the line. Nothing is imported until the list
  reads cleanly, so a typo can't let a recording in.
- **Too late.** An ID listed after its recording arrived works like a late keep-out: `private` at
  once, never fetched again, one push, and Status names the recording. It happens within seconds of
  the worker seeing the edited list, even if Plaud is down or the sign-in has lapsed, because it needs
  only the list and the archive. Every other recording keeps flowing.
- Status shows what the worker read: "Consent list: 14 IDs, 4 on Plaud now · file changed
  2026-10-23 09:12 · read 14:20". If an edit doesn't show there, the worker didn't see it.

**Deleting in Plaud** keeps a recording out only before the sync brings it in. After that, the
archive keeps it, never deleting anything on its own. Status and the Sync screen show it under
"Gone from Plaud", newest first, with a count of those gone in the last 7 days and a link to the
steps for taking a recording out (controller's call).

**Taking a recording out by hand** (one runbook section, used for every case above):
1. Put its Plaud ID on the consent list first, so no sync can bring it back. If the recording is
   already flagged, or gone from Plaud, this sends no new push. Otherwise the next sync pushes once
   about it, which is expected.
2. Stop the app, delete its folder for good (never into `trash/`, which is backed up and mirrored),
   rebuild the index, and start the app again.
3. Know what still holds it: the hourly to monthly backups until they age out (up to 12 months), the
   `pre-import` and `post-import` backups for good unless a NAS-side `restic rewrite` removes it, the
   NAS mirror until its next run, the Mac's copy and Synology Drive's versions, and Time Machine. These
   are the same places Forget reports (main spec §7.6).

Part II: §12, §13, §17.5.

### J3. A speaker rename, and a relabelling evening

1. Dan renames "Speaker 2" to "Alex Kim" in last week's recording, in Plaud's app. Plaud gives no
   signal that anything changed.
2. The sweep checks every recording within a day and one sync while he relabels
   (`full_sweep_days = 1`), and within a week and one sync after. The rename shows by then: a new
   Plaud transcript with "Alex Kim", and "speakers" in the Sync screen's **Updated on Plaud** list. Details says when the recording was
   checked and when it will be next. If spike S5 finds that Plaud's listing marks edits, renames show
   within one sync instead.
3. Impatient? **Re-sync from Plaud** in Details checks it now: "Updated: speakers".
4. **The relabelling evening:** he fixes 25 recordings, selects them in the Library, and presses
   **Re-sync from Plaud** in the selection bar once (decision 6). "Re-syncing 12 of 25…", then "25
   checked: 22 updated (speakers) · 3 up to date".
5. **Sync everything** on the Sync screen checks the whole account (`--full`). It says roughly how
   long, from the last run's pace.
6. A name changed back to "Speaker 2" is a removal. It isn't applied, and it is counted on Status
   ("3 changes held for your review") until stage 3a's review list arrives.

Part II: §14.

### J4. Something breaks

A push arrives on his phone, with a fixed title and "See Status". Status is complete on its own.

| What broke | Push | What Dan sees and does |
|---|---|---|
| Plaud refused to refresh the sign-in | at once: "Plaud sign-in needed" | Status: "sign-in needed since 23:00 · new recordings wait on Plaud: nothing is lost". At the Mac: `make plaud-sign-in`. One "works again" push follows. |
| Plaud is down, or the server is offline | only if no good sync for 2 hours (decision 5) | Status shows the error from the first failed run, with its reason: offline (check the network), Plaud unavailable or rate limited (it heals itself), Plaud refused access (check the subscription), or an error (a Claude session). |
| Every recording fails to fetch, though the listing works | as above, after 2 hours | The same: a run with fetches and not one success is not a good sync. |
| The edit breaker trips (Plaud's data starts changing everywhere) | at once | Status names the changed field names and prints the exact command to run once the cause is fixed. New recordings still arrive. |
| Plaud's IDs change form again, everything looks new, or many recordings vanish from Plaud at once | at once | Status says which, and what to check. Imports stop until he presses **Accept this listing** on Status or the Sync screen (or runs it on the server). Accept is refused while the consent list matches fewer Plaud IDs than before. This is the 2026-09-25 failure, caught. A few recordings vanishing settle by themselves, with no push, once the next listing an hour later is missing the same ones. |
| The consent list can't be read | at once, then daily | Status names the line numbers. He fixes the file; the next sync goes on. |
| A privacy intent didn't hold: a late keep-out, a late consent listing, or a `private` the sync couldn't write | once, at once (P15) | Status names the recording. For the first two, he takes it out by hand; for the third, he runs `recordings reindex`. |
| The sync's records were lost or rebuilt (a restored or replaced `state.db`) | once, at once | Status: "Plaud sync waits for you: its records were rebuilt." He runs a dry run, decides about anything it lists as missing (a recording kept out before may be among them), then presses **Accept this listing**. |
| The worker dies or hangs, or can't deliver a push | the sync's Healthchecks check, within about 1h20 | `rc logs worker`. |
| One recording fails every check | never | Status: "1 recording fails every check · Re-sync · Park". It never hides "All here". A recording that fails to import holds "All here" back until he parks it. |
| The schedule was left off | never (Needs attention after 24 hours) | Turn it back on. |

**Nothing pushes twice for the same cause.** Pushes about one recording count once per recording and
cause: putting an already-flagged recording on the consent list sends nothing new. The Healthchecks
check means "the worker is alive and deciding". The worker pings it at every scheduled turn it
handles, even one that stops at a lapsed sign-in, a stopped consent list, a hold, a rate limit or the
first catch-up. So Healthchecks speaks only when the worker itself is dead or stuck, or when the
worker owes Dan a push that ntfy won't take: then the worker holds back its ping, and Healthchecks
becomes the backstop for push delivery. Lasting failures push through the app's own alert, with the
reason. This refines decision 4's "pinged after every good run" (controller's call, needs Dan's OK;
§5).

Part II: §16.

### J5. "Is everything here?"

Status's sync panel answers it in order:
1. **One line, true or absent:** "All here · checked 14:20 · next sync 14:40". Under it: "Every
   recording on Plaud is in the archive or deliberately left out." It shows when the listing is
   fresh and nothing is waiting or missing (parked recordings aside). A recording that keeps failing
   its checks is a separate line beside it, and never hides it.
2. **Counts that add up:** "On Plaud 1,234 = here 1,227 + waiting 2 + excluded 4 + missing 1".
   **Waiting** will come in by itself. **Missing** won't without Dan (the schedule is off, imports
   are paused, the disk is nearly full, or it failed 3 times). **Excluded** is the consent list and
   the keep-out word.
3. **The archive's side:** "In the archive from Plaud 1,240 = here 1,227 + gone from Plaud 13".
4. **Freshness:** the last good listing, the next sync, and "every recording checked within the last
   21 hours".
5. **The monthly Sync everything:** "2026-10-01: all 1,234 checked (2 parked)".
6. Held removals, gone from Plaud, failing and parked recordings, each a count with a link.
7. **Folded away:** diagnostics for a debugging session.

### J6. The Sync screen

Dan rarely needs it while the schedule runs. He opens it:
- for the first real sync (J8), when only **Compare** is offered;
- when he wants something now: **Sync now**;
- to see what is kept out, copy an ID, or **Release** a recording he kept out;
- to press **Accept this listing** after a hold, once he has checked what Status says;
- to confirm a fix after a push;
- for **Sync everything** while relabelling.

From top to bottom: the summary line; any flags (late keep-outs, late consent listings, a `private`
the sync couldn't write), and any hold with its **Accept this listing**; **Sync now** and
**Compare**, with **Sync everything** under More; the equation; **Not here yet**, with checkboxes,
**Import selected** and **Import all 31**; **Kept out**; **Updated on Plaud**; **Gone from Plaud**,
newest first; and "Import your own audio: coming later". Importing never asks "are you sure?". A
toast says what came in: "31 imported · 4 arrived private · View". The panel keeps the last
result line, so a phone that slept mid-import still sees it. Every press gets an answer within a
second: "Queued", "Queued after the current sync (started 14:20)", or "The Plaud worker isn't
running (last reported 14:21)".

### J7. On the phone

- A push says "See Status", with no link (a link would send the app's address to ntfy.sh).
- Status and the Sync screen work at 320 pixels: the summary first, full-width buttons, 44-pixel
  targets.
- **From the phone, Dan can:** rename in Plaud (the keep-out word, a private title), Sync now,
  import, Re-sync, Release (after its question), Park, Accept this listing, and read Status.
- **He needs the Mac or the server to:** sign in again, edit the consent list, take a recording out
  by hand, or reset the breaker.

### J8. The first real sync, and when tagging starts

1. **Spikes, with Dan** (three sessions, about 2½ hours in all). They answer what only Plaud can: how
   the sign-in behaves, what the listing looks like, whether the `of_` hex equals the old IDs, and
   what an edit looks like. Each spike uses a throwaway recording made for it, or one Dan names in
   the session, never a consent-listed or private one. One question may follow: if signing in
   elsewhere signs the server out, who owns the sign-in (§18, S3).
2. **Deploy 2b.** The worker refuses scheduled syncs and imports until the first catch-up has run,
   whatever the config says (controller's call). Dan sets up the sync's Healthchecks check once, with
   its notifications going through Healthchecks' own channel (e-mail or its app), not ntfy. Then
   `make plaud-sign-in`, and checks.
3. **The dry run** (Compare, or the CLI). Dan checks that both equations add up, that "here" equals
   the archive's Plaud recordings, that what's waiting is about the ~30 newer recordings plus the held
   titles, and that the consent list's IDs are found on Plaud in their new form. The new parser reads
   `of_` and upper-case IDs that 2a's skipped, so any recording 2a imported despite such a line is
   listed, by archive ID only. Dan reviews each, and takes out by hand any that mustn't stay, before
   the next step's backup. If anything doesn't fit, he stops.
4. **The catch-up,** supervised on the server: a `pre-import` backup, the baseline, `recordings plaud
   sync --catch-up`, a validation, then a `post-import` backup.
5. **"You may start tagging now"** (decision 7). The runbook says it here, before the schedule is
   turned on. A recording that keeps failing doesn't hold it back.
6. Turn the schedule on, unpause the sync's Healthchecks check, and watch Status. If "All here"
   doesn't show, Status says why.

Part II: §17.5, §18.

## 3. How it should feel

| # | Principle | Where |
|---|---|---|
| P1 | **No silent failure.** Every way the flow can stop pushes once or shows on Status. | §16 |
| P2 | **Honest status.** Each sentence is true now or absent. Counts add up. Figures carry their time. | §11.3, §15.2 |
| P3 | **One place to look.** Pushes say "See Status", and Status is complete. The Sync screen acts. | §15 |
| P4 | **Calm alerts.** One push at onset, a daily reminder, one recovery. Routine errors only after 2 hours. A single recording's failure never pushes. | §16 |
| P5 | **Every alert has a next step,** in the runbook. | §16, §17.5 |
| P6 | **Every press gets an answer** within a second. | §9.3, §15.1 |
| P7 | **Dan's choice wins, and stays.** A removed `private` stays removed; a kept-out recording stays out. | §12, §13 |
| P8 | **Errs private and fails closed,** consent-list typos included. | §12.1 |
| P9 | **A window before the irreversible,** usable from the phone. | §12.2 |
| P10 | **Arrival is visible** in an open Library, and rows don't move until Refresh. | §15.3 |
| P11 | **Fresh by default, immediate on demand.** | §14 |
| P12 | **Plain sentences first, diagnostics folded away.** | §15.2 |
| P13 | **No titles outside the app's own views.** None ever for a consent-listed recording; for one kept out by title, only inside Release's own question. | §10.1, §12, §15 |
| P14 | **The app removes chores.** | §10.5, §12.1 |
| P15 | **A privacy intent that didn't hold is pushed once,** at once, once per recording and cause: a late keep-out, a late consent listing, a `private` the sync couldn't write. | §12, §13.2, §16 |

## 4. What 2b covers, and what waits

**In 2b** (main spec §20): the read-only Plaud client with the `of_` fix; the sign-in and its refresh;
a fake Plaud server for tests; the `worker` container; the compare and the import; the consent list,
the keep-out word and auto-private; edit-sync (the sweep, the breaker, the baseline, Re-sync of one
recording or a selection, Sync everything); the Add page and its Sync screen, Status's sync panel and
the arrivals cue; alerts and the sync's own Healthchecks check; operations; and the spikes.

**Later:**
- **Stage 3a:** the review queue for held removals, and the Library on `index.db` (sooner, if the
  runbook's 2-second check fails).
- **Stage 3b:** People, and linking Plaud's names to them.
- **Stage 4:** the processing queue. The 2b worker does only Plaud work; stage 4 builds on it.
- **Stage 5:** upload, URL and the watched folder.
- **Not in 2b:** signing in from the phone, writing anything to Plaud, deleting a recording from the
  app, importing `audio-router`'s deferred rows that Plaud no longer lists, and blocking network
  traffic from `web` and `backup` to Plaud.

**If 2b runs long,** it ships in two halves (main spec §20 allows it). The first half is the client,
the sign-in, the worker, the compare and the import, the consent list, the keep-out word,
auto-private with the baseline's privacy part, the alerts and the Sync screen. The second half is
the sweep, the breaker, Re-sync and Sync everything. Both deploy and roll back by their tag. §20
says which criteria belong to which half.

## 5. Calls made for you

Each call was made without asking Dan. It is listed by what he will notice. "(controller's call)"
means the controller made it; one needs his OK.

**Needs Dan's OK:**
1. **The sync's Healthchecks check speaks only when the worker can't.** This refines decision 4's
   "pinged after every good run". The worker pings the check at every scheduled turn it handles,
   good or bad, even a turn that stops because the sign-in lapsed, the consent list is stopped, a
   hold is on, Plaud asked it to wait, or the first catch-up hasn't run. A ping means "alive and
   deciding". So:
   - **What Dan gains:** a Plaud outage or a refused sign-in reaches him once, through the app's own
     push, with the reason. Healthchecks doesn't add a second, reasonless alert.
   - **What still reaches him through Healthchecks:** a worker that is dead or stuck; and a push the
     worker owes him but ntfy won't take, because then the worker holds back its ping.
   - **With decision 4 read literally** (pings only after good runs), every lasting failure would
     also bring Healthchecks' alert after about 1h20.

   (Controller's call, needs Dan's OK.)

**Privacy and keeping out:**
2. **A recording kept out by its title stays out,** even if Plaud or autocorrect later removes the
   word. **Release** on the Sync screen lets it in, after asking once, with the title shown in that
   question only. (Controller's call.)
3. **Deleting in Plaud after the sync** leaves the recording here, shown under "Gone from Plaud",
   newest first, with a link to the removal steps. Nothing is deleted automatically. (Controller's
   call.)
4. **A late keep-out or a late consent listing** gets `private` at once, is never fetched again,
   and pushes once per recording. A late consent listing is tagged within seconds of the worker
   seeing the edited list, even while Plaud is down. The sync goes on for everything else.
5. **A consent-list typo stops every import** until the line reads, and pushes, naming the line
   number. The list's meanings are unchanged; only `of_` and upper-case IDs are newly read.
6. **Taking a recording out by hand** deletes it for good, never into `trash/`, after its ID goes on
   the consent list, and the runbook lists what still holds a copy.
7. **A private title is tagged as soon as the listing shows it,** even if Plaud's transcript won't
   download. (Controller's call.)
8. **The consent list lives on the server,** in its own folder. The Mac's old copy is no longer read.
   `make consent-add` adds an ID from the Mac.
9. **Changing the keep-out word doesn't release anything.** Every word the worker has enforced keeps
   working until Dan drops it on the server.
10. **A new auto-private pattern reaches the archive.** Adding one checks every title against the
    new pattern only, so a `private` Dan removed stays removed for the patterns it already passed.
11. **After the sync's records are lost, rebuilt or restored from an older backup,** the schedule
    waits for Dan, with one push. A dry run lists everything on Plaud that isn't here (a recording he
    kept out before may be among them; consent-listed ones by ID only), and **Accept this listing**
    lets the schedule go on. A `private` tag he removed can come back once, with the note
    "re-tagged private: the sync's record was rebuilt". This errs private. (Controller's call.)

**The first run and imports:**
12. **Nothing imports before the supervised catch-up** on the server, whatever the config says. Until
    then the Sync screen offers Compare only. (Controller's call.)
13. **The catch-up holds the backups back** while it runs, between a `pre-import` and a `post-import`
    backup. After it, syncs never hold up a backup. (Controller's call.)
14. **Importing never asks "are you sure?".** A toast says what came in. (Controller's call.)
15. **A scheduled sync imports at most 20 recordings,** and the rest follow at the next one. A big
    backlog after a week away arrives in a few syncs, with no push.
16. **If Plaud's IDs change form, most of the account looks new, or many recordings vanish at once,**
    imports stop with one push until Dan checks what Status says and presses **Accept this listing**
    (on Status or the Sync screen, or on the server). Accept is refused while the consent list
    matches fewer Plaud IDs than before. (Controller's call.)
17. **A few recordings vanishing from Plaud** (up to 5, or up to 5% of the archive if that is more)
    are marked gone by themselves once the next listing, at least an hour later, is missing the same
    ones. More than that is a hold (call 16).
18. **`audio-router`'s deferred recordings that Plaud no longer has** stay in its frozen archive; 2b
    counts them and doesn't import them. (Controller's call.)

**Keeping up:**
19. **A speaker rename shows within a day and one sync while `full_sweep_days = 1`,** and within a
    week and one sync at the default of 7, as long as the sync's time allowance keeps up (Status
    shows the real figure). Re-sync shows it at once. (Controller's call.)
20. **Re-sync takes up to 1,000 recordings at a time;** beyond that, Sync everything. (Controller's
    call.)
21. **A recording that always fails can be parked.** Then it stops counting against "All here" and
    Sync everything, and stays listed as parked.
22. **The edit breaker is reset from the server, never from a button,** because a reset without a fix
    trips it again. Status prints the exact command. It trips on data that changes between two
    fetches a moment apart, never on Dan's renames, however many. (Controller's call.)
23. **Held removals are counted,** with no Accept button until stage 3a. (Controller's call.)

**Signing in:**
24. **Signing in again happens on the Mac,** with `make plaud-sign-in`. No phone sign-in in 2b.
    (Controller's call.)
25. **If signing in elsewhere signs the server out** (spike S3), Dan is asked who owns the sign-in.
    Not before. (Controller's call.)

Calls Dan won't notice (where the token lives, how the auto-private record is kept) are in §8.

## 6. Done, in your terms

2b is done when, on the real server:
1. **The dry run accounts for every recording on Plaud.** Both equations add up, Dan signs them off,
   and the consent list's IDs are found on Plaud.
2. **A speaker rename shows up:** at once with Re-sync, and within a day and one sync without it.
3. **Sync everything finishes clean,** apart from recordings Dan has parked (each one checked in
   Plaud's app and noted in the runbook) and recordings flagged to be taken out, which it names.
4. **A refused sign-in raises one push,** and signing in again brings one "works again". Proven
   with the fake server; on the real account, `alert-test` proves delivery.
5. **His decisions hold:** a placeholder-titled recording arrives at once and a later private title
   tags it; a keep-out title is never downloaded; a late one is tagged, flagged and pushed once;
   Release asks first; a removed `private` stays removed.
6. **Nothing stops silently:** an outage pushes after 2 hours and not before; a dead worker, or a
   push ntfy wouldn't take, reaches him through Healthchecks; a rebuilt sync state pushes once and
   waits; a schedule left off shows in Needs attention.
7. **The README, the runbook and the tagging spec** say tagging starts after step 7's import and its
   backup (decision 7).

§20 makes each testable.

## 7. Risks you accept

1. **The keep-out word races the first sync.** It works only if the rename reaches Plaud before the
   next sync, at most 20 minutes after Plaud lists the recording. Spike S9 measures whether a title
   can be set before the listing shows it. If it usually can't, the README says plainly that the
   word often arrives late, and late means a copy in the backups until it is taken out by hand.
2. **A late private title is briefly unprotected** (decision 1's exception): until the next sync, it
   sits in the archive, the backups and the mirror with whatever tags Dan gave it. With none,
   external agents already treat it as private. Separately, the forgiving keep-out word can keep out
   an ordinary title by mistake; it waits under Kept out until Dan releases it.
3. **The sign-in model depends on spikes S1 to S3.** If Plaud gives no refresh token, ties a sign-in
   to the Mac, or lets the Mac's Plaud tools sign the server out, "rare and one command" may not
   hold, and the spec stops to ask.
4. **A rotating sign-in can be lost** in a crash between Plaud's reply and the write. The cost is one
   push and one `make plaud-sign-in`.
5. **Copies outlive a removal by hand,** as J2 lists. Only a NAS-side `restic rewrite` removes the
   `pre-import` and `post-import` copies.

The full list is §23.


# Part II: For the builder

Everything here serves Part I. Where they seem to differ, Part I says what Dan was promised, and Part
II is wrong.

## 8. Settled, and the calls Dan won't notice

**Settled by the council's evidence,** each applied where named:
- The worker opens its own `Writer`. "One writer" means one machine (main spec §3, §6.4) (§9.1).
- The UI asks the worker through request rows in `state.db`, over the guarded websocket (§9.3).
- `state.db` gains tables only, with no `SCHEMA_VERSION` bump (§9.5).
- The breaker counts over a rolling window, and the listing's holds back it up (§14.4, §11.4).
- Only a refused refresh raises the sign-in alert (§10.3).
- The static `RECORDINGS_PLAUD_TOKEN` and `recheck_days` go (§17.4).
- The consent parser accepts the `of_` form and any case (§12.1).
- The worker has its own alert secrets, doctor role, `/index` mount and consent mount (§17).

**Calls Dan won't notice** (controller's calls):
- **The token's home** is its own host folder, `/srv/recordings/plaud`, mounted only in `worker`
  (§10.3). `web` and `backup` mount `/state` as the same user, so no file mode could keep them out of
  it there.
- **The auto-private record** (the marker) lives in `state.db`, as a plain SHA-256 of the title, and
  only for recordings in the archive, whose titles `recording.json` already holds (§13.2). It isn't
  in `recording.json` itself: the archive's models refuse unknown keys (`extra="forbid"`,
  `models.py:56`), so a new key would stop a pre-2b release from reading the archive after `make
  rollback`.
- **Titles of recordings not yet here** live in a cache file that no backup copies (§9.5).
- **The worker's `Writer` doesn't write the archive's docs;** `web` does (§9.2).

Every contradiction the council found is settled in the text, and listed with its amendment in §19.

## 9. Architecture

### 9.1 Three containers, one writing machine

```
 browser ─websocket─▶ web ──request row──▶ state.db ◀──claims── worker ──GET only──▶ Plaud
                      │ tags, own Writer    (WAL, locks)         │ own Writer
                      └────▶ archive, per-recording flock locks ◀┘
                                     ▲ reads only
                                  backup: restic, the mirror, checks
```

- **One image, three containers:** `web` (`recordings-ui`), `backup` (`recordings ops --loop`) and
  `worker` (`recordings worker`, new).
- **One writer means one machine.** Several processes on the homelab server may each hold one
  `Writer`, on one thread (tagging part A §5). They share the per-recording locks, `state.db` and
  `writer_id` (main spec §6.4). Main spec §20's "All Plaud calls and writes happen there" narrows to
  "every Plaud call, and every write that comes from Plaud".

| Process | Writes | Never |
|---|---|---|
| `web` | tags, the archive's docs at start, request rows | calls Plaud, or holds the token |
| `worker` | new recordings from Plaud, snapshots, Plaud's outputs, Plaud's title, the automatic `private` tag, the Plaud tables, its alerts and pings | writes any tag but `private`, writes the docs, or opens a port |
| `backup` | backups, the mirror, checks (archive read-only) | calls Plaud |
| the CLI in the `worker` container | as the worker | |

### 9.2 The worker process

- **`recordings worker`:** one process, one main thread that owns its `Writer`, and one heartbeat
  thread. The `Writer` is opened with `Writer.open(cfg, docs=False, lock_timeout=10)`.
- **The heartbeat thread** has its own short `state.db` connections. Every 30 seconds it writes
  `meta.worker_heartbeat` with `{at, phase, progress_counter, progress_at}`. The main thread bumps
  `progress_counter` on **every tick of its loop, idle included,** and also at every recording, every
  listing page, every MiB of a download, every slice of a wait or backoff, and as `Writer.open`
  rebuilds the index.
  - **Alive** means a heartbeat under 90 seconds old, and progress in the last 15 minutes (30 while
    the phase is `starting`). An idle worker (no token, the gate closed, the schedule off) is alive,
    because its loop ticks. A wedged main thread is not, because a tick that never returns bumps
    nothing.
  - `recordings worker --alive` (the compose health check) and Status read both.
  - A synchronous step (an index rebuild in `Writer.open`, a download, a `Retry-After` wait) no
    longer silences it. This replaces the draft's claim that "the heartbeat keeps the health check
    green" from the one thread, which it couldn't (`writer.py:333-345` rebuilds synchronously).
- **At start,** in this order:
  1. Start the heartbeat thread, phase `starting`.
  2. Sweep its own leftovers in the archive's `.tmp/plaud-dl/`, under a non-blocking try of the
     `plaud` lock. If a CLI run holds it, skip: the holder owns those files.
  3. Mark requests left `running` as failed ("interrupted"), and requests queued more than an hour
     ago as expired. An unfinished Sync everything is resumed (§14.6).
  4. Open the `Writer`.
  5. Check whether its records were lost or rebuilt (§11.6, "A rebuilt `state.db`").
- **Every 2 seconds** the main thread:
  - bumps its progress;
  - checks the consent list's file (inode, size, time), reads it again if it changed, and applies
    late consent listings (§12.1), whatever the sign-in, the listing, the schedule or the gate;
  - claims the oldest queued request and runs it;
  - handles a scheduled turn when one is due (§9.7). Due means `schedule_minutes` after
    `max(plaud_state.last_started, plaud_state.last_ended)`. Both persist, so a worker that crashes
    and restarts doesn't start a sync at every start;
  - runs Sync everything when 30 days have passed since the last finished one.
- **Inside a long run** (the catch-up, Sync everything, a large Re-sync, a `baseline --all`), between
  two recordings, it serves:
  - single-recording Re-syncs and Compare requests;
  - the **light turn** of a due scheduled sync: the listing, title checks (§13.1) and up to 20
    imports.

  Other requests wait, and say so: "waiting for Sync everything (412 of 1,234)". This holds for the
  CLI's long runs too (`rc exec worker …`), which run the same loop code.
- **On SIGTERM** it stops between recordings and exits within its 30-second grace. A download in
  flight is abandoned and swept at the next start.
- **No token, no consent list, or a bad `keep_out_word`:** the worker stays up, healthy and idle.
  Status and doctor say why. `make deploy-smoke` and the first deploy rely on this.
- **A `LockTimeout` on a recording is "busy":** the import or check is retried at the next run. It
  counts as neither a failure (§10.2's three strikes) nor an attempt (§11.7).
- It listens on no port.

### 9.3 Requests from the UI

- **The browser sends** an event input, `plaud_request`, mounted once in `App` with a `null` default,
  like `tag_request` (tagging part A §5). Only a person's action sends it: never on load, a URL, its
  hash or storage (main spec §15).
  ```json
  {"request_id": "<random>",
   "kind": "compare" | "sync" | "import" | "resync" | "full" | "accept" | "release-preview"
           | "release" | "unrelease" | "park" | "unpark",
   "plaud_ids": ["…"], "recording_ids": ["…"], "all": false}
  ```
- **The web inserts a row** in `plaud_requests` with `State.db()`'s own short connection, on
  `asyncio.to_thread`. Never on the tag service's writer or reader thread (`writing.py`), where it
  would wait behind a tag batch and break P6. Before inserting it checks:
  - `kind` is known;
  - IDs parse (`ids.parse_id` for recordings; non-empty strings under 128 characters for Plaud IDs);
  - the caps: 20,000 Plaud IDs, and 1,000 recording IDs for `resync`;
  - that it can write: with no `config.toml`, in the mirror's copy, or in the demos there is no
    writable `State` (`app.py:70-80`), so the Sync screen's buttons are disabled with the same kind
    of reason as `cannot_tag_reason`, and a crafted request is refused.

  A request never carries a title.
- **Coalescing:**
  - A `sync` subsumes a queued `compare`: the row is upgraded, and the reply is "Queued".
  - A `compare` or `sync` while a `sync` is queued answers "Already queued".
  - A `resync` merges into a queued one with `UPDATE … WHERE state = 'queued'`. If no row matched
    (the worker just claimed it), a new row is inserted.
  - A `full` is refused while one is queued or running, or within an hour of the last **finished**
    one. While an interrupted one is unfinished, a press answers "Resuming (started 14:20)".
  - A repeated `request_id` is ignored. An `import` of a recording already here skips it.
- **While the gate is closed** (§11.6), the worker refuses `sync`, `import`, `resync`, `full`,
  `release` and `park` with the gate's reason. `compare` runs. `accept` runs only to reopen a gate
  that a rebuilt `state.db` closed; before the very first catch-up it is refused with "The first
  import is a supervised run on the server (runbook)".
- **The worker claims a row** (`UPDATE … WHERE state = 'queued'`) and checks it again against fresh
  facts: the newest listing, the consent list as it is now, the keep-out words and the patterns. An
  `import` row causes downloads and archive writes, and is safe only because of this re-check. A
  `release` is refused if its ID is consent-listed, or if any enforced word matches its current
  title, and the reply says which (§12.2). An `accept` is refused while the consent list matches
  fewer Plaud IDs than in the last good listing (§11.4).
- **Replies:** while a session has a request pending, the server reads its row every 2 seconds and
  sends progress and the final reply (`send_message`). The Sync screen keeps the newest result line.
- **When the worker is down,** rows stay queued, and the screen says "The Plaud worker isn't running
  (last reported 14:21): your request runs when it's back, if that's within the hour." An hour later
  the row expires.
- **The CLI writes no rows.** `rc exec worker recordings plaud …` runs in its own process, under the
  same locks.
- **Trust.** Any device on the tailnet that passes the websocket's Host and exact-Origin checks
  (`hosts.py`) can press **Import all** or **Sync everything**: the same trust as tagging. The worker
  takes nothing in a row on trust.

### 9.4 Locks

- **`plaud` allows one sync at a time.**
  - It is taken through its own `Locks` instance, as `ops` is. In the `Writer`'s instance it would
    make every recording lock taken inside it raise `LockOrderError` (`locks.py:78`).
  - It is held for one run, never between runs, and announced with the run's kind: `plaud-sync`,
    `plaud-compare`, `plaud-import`, `plaud-resync`, `plaud-full`, `plaud-baseline` or
    `plaud-catch-up`.
  - A scheduled turn that finds it held is skipped: busy, neither good nor a failure. A request
    waits its turn. The CLI waits up to 60 seconds, then exits 75.
- **Scheduled runs, UI requests and every CLI command but `--catch-up` never take `ops`.** The hourly
  backup, the mirror and the long checks run beside them. A new recording is assembled in `.tmp/`,
  which both leave out (`backup.py:244-247`, `mirror.py:155`).
- **`recordings plaud sync --catch-up`** takes `ops` and `plaud` in one `hold()` call (both sort as
  "anything else", `ops` first), and announces `plaud-catch-up` on both, so the deploy guard, which
  asks `ops`, sees it. No backup or mirror runs while it imports.
- **The `audio-router` import also takes `plaud`,** beside `ops`.
- **Recording locks** are held only around local work: publishing a snapshot, a `mutate`, writing
  outputs, moving an assembled recording in. Never across a Plaud call, a download or a wait. Two
  holds are longer than a tag write's 2-second wait might expect:
  - `reconcile_plaud` holds the lock while it writes every output with an fsync each
    (`writer.py:812-862`). A test bounds it with 30 outputs.
  - `_create` holds the new ID's lock and `sha-<hash>` across the media copy (`writer.py:640-660`).
    Nobody can tag a recording that doesn't exist yet, so a test asserts that instead.

  "Busy" for a tag click is expected to be rare, not impossible.
- **`tokens.lock`** in `/plaud` guards a refresh (§10.3).
- **Later:** Forget (stage 3b) pauses the worker by taking `plaud`.

### 9.5 `state.db`, and the cache

**New tables in `state.db`,** each `CREATE TABLE IF NOT EXISTS`, and no 2a table changed:

| Table | Holds | Written by | Read by |
|---|---|---|---|
| `plaud_requests` | the UI's requests (§9.3), kept 7 days after they finish | web inserts; worker claims | both |
| `plaud_listing` | one row per Plaud ID: the ID as listed, its canonical form, first and last seen, kept-out state and release (§12.2), and, **only while its recording is here**, a SHA-256 of its title, its duration and any change marker S5 finds. A kept-out or consent-listed ID has no title hash, ever | worker | both |
| `plaud_checks` | per recording: last checked, last changed, next check, interval, consecutive failures, settled, baseline pending, held removals, parked, degraded, flagged | worker | both |
| `plaud_check_log` | one row per check: time, recording, result, parts changed, parts found volatile. Kept 7 days | worker | both |
| `plaud_private_checked` | the marker (§13.2): per recording and Plaud ID, a SHA-256 of the title last checked, and the version of the pattern list it passed | every `Writer` that reconciles | the same |
| `plaud_events` | what Dan sees: imported, arrived private, updated (with parts), re-tagged private (with why), kept out late, consent-listed late, couldn't tag private, gone from Plaud, released. Kept 90 days; open flags until resolved | worker | web |
| `plaud_state` | one row per key: the last good listing (its ID-set hash, census and time), holds (with the held listing's absent set), the breaker and its `window_start`, the sign-in state, `failing_since`, `last_started` and `last_ended`, `catch_up_done_at`, the keep-out words enforced, the auto-private pattern list by version, Sync everything's progress and pace | worker | both |

- **No `SCHEMA_VERSION` bump.** An older release's `State.open` ignores unknown tables, so `make
  rollback` keeps working. A test asserts `SCHEMA_VERSION == 1` and `MIGRATIONS == {}`, saying why.
- **Reused:** `ops_runs` (jobs `plaud-sync`, `plaud-full`, `plaud-catch-up`), `alerts` (with new
  keys, §16), and `meta` (`worker_heartbeat`).
- **A recording that leaves the archive** (`reindex --allow-missing`) takes its rows in
  `plaud_checks`, `plaud_private_checked`, `plaud_events` and its `plaud_listing` title hash with it.
  `State.forget` already deletes its revisions (`state.py:342-346`). Forget (stage 3b) deletes them
  too.
- **Titles in `state.db`.** Besides the merge-base revisions, which hold whole `recording.json`
  bodies (`state.py:26-29`; written by every create and `mutate`, `writer.py:564, 683`), 2b stores no
  title in `state.db`. Its title hashes are plain SHA-256, and only for recordings in the archive,
  whose titles `recording.json` and those revisions already hold, so a hash reveals nothing new. A
  kept-out, consent-listed, waiting or missing ID has no title and no title hash in `state.db`.
- **The cache, `state/plaud-cache.db`:** a separate SQLite file, derived and rebuilt by the next
  listing, that no backup copies (the backup copies `state.db` and `extra_paths` only, and doctor
  calls it a problem if `extra_paths` would include it, §17.2). It holds:
  - the newest compare's rows: titles for recordings not yet here, and the date, time and duration
    for the rest;
  - **never a title of a consent-listed recording,** and, during a consent stop, no title at all for
    any recording not here (the Sync screen says "titles hidden until the consent list reads");
  - a Release preview (§12.2): the one title Release's question shows, deleted once the question is
    answered, or after 10 minutes.

  Only the Sync screen reads it. So no title of a recording Dan then keeps out reaches any backup,
  the `pre-import` one included.
- **Reading a table that isn't there yet.** Status and `--alive` catch `OperationalError: no such
  table` and say "No sync has run yet". It must never reach `status_view`'s "restore `state.db`" text
  (`status.py:92-98`), which fires for any `DatabaseError` today.
- **`index.db` moves to WAL mode** (the 2a carry, 2a rulings item 72d).

### 9.6 Code and commands

- **Core (`packages/core/src/recordings/`):**
  - `plaud/client.py`, copied from `audio-router` with its commit recorded, relicensed MIT (main spec
    §19). A test fails on any 32-hex or `of_` string in it outside a short list of synthetic ones,
    comments and docstrings included.
  - `plaud/tokens.py`, `plaud/sync.py` (the listing, compare, import and checks), `plaud/checks.py`
    (the tables), `plaud/keepout.py` (the word match and the consent parser's entry rule).
  - `sources/base.py` and `sources/plaud.py`: main spec §9.0's `Source`, plus a `check()` that
    downloads no media.
  - `worker.py`: the loop and the heartbeat thread.
  - `Writer.add_snapshot(recording_id, raw)`: new. It publishes a write-once snapshot, then records it
    through `mutate`. An orphan from a crash between the two is adopted by the next `add_snapshot` of
    the same content, and `validate` reports it as a warning, never an error.
- **UI (`recordings_ui/plaud.py`):** request checks and inserts, and the views. It imports no client,
  token or sync module, and a test asserts it.
- **Commands** (each in the `worker` container):

  | Command | Does | Holds |
  |---|---|---|
  | `recordings worker [--alive]` | the daemon; or the health check | `plaud`, per run |
  | `recordings plaud sync --dry-run [--list]` | lists and classifies; prints both equations, the consent-list match, any hold and why. `--list` adds rows by ID, date and duration | `plaud` |
  | `recordings plaud sync --catch-up` | the first import, supervised (§11.6) | `ops`, `plaud` |
  | `recordings plaud sync` | a sync now, with no import cap; refused while the gate is closed or a hold is on | `plaud` |
  | `recordings plaud sync --full` | Sync everything (§14.6) | `plaud` |
  | `recordings plaud resync <id>…` | checks these now | `plaud` |
  | `recordings plaud baseline [--all]` | §14.5 | `plaud` |
  | `recordings plaud accept-listing` | the CLI's **Accept this listing** (§11.4), refused on the same terms | `plaud` |
  | `recordings plaud breaker reset` | §14.4 | `plaud` |
  | `recordings plaud park <id>… [--undo]` | §14.6 | none |
  | `recordings plaud release <plaud-id> [--undo]` | §12.2's Release, without its on-screen question: the CLI shows ID, date and duration, and asks y/N | none |
  | `recordings plaud keep-out-words [--drop <word>]` | lists or drops enforced words (§12.2) | none |
  | `recordings plaud consent` | what the worker reads from the consent list: the ID count, how many are on Plaud now, the file's time; unreadable lines and lines read as kept, by number; consent-listed recordings in the archive, by archive ID, marking those 2a's parser missed | none |
  | `recordings plaud held` | held removals, by recording ID and part | none |
  | `recordings plaud check [--volatility]` | one signed-in read; or §14.4's volatility check | none |
  | `recordings plaud token-import -` | installs a sign-in from stdin (§10.5) | `tokens.lock` |

  `make` targets (GNU make 3.81): `plaud-sign-in` and `consent-add` (run on the Mac); `e2e-live`
  (§21); and `help`, which the Makefile doesn't have yet (Dan's standing preferences ask for one).
- **Exit codes:** 0 ok; 1 failure; 2 partial (the listing was good, but some recordings failed or
  aren't here); 69 Plaud unreachable or rate limited; 75 busy; 77 sign-in needed; 78 configuration
  or the consent list. `--json` prints counts, IDs and fixed phrases, never a title.

### 9.7 Scheduled turns

A **scheduled turn** comes due every `schedule_minutes` (§9.2). The worker **handles** every turn
that comes due, in one of these ways, and each handled turn ends with the sync's ping (§16):
1. **The gate is closed** (§11.6): nothing runs. Status says why.
2. **The sign-in is paused** (§10.3): no Plaud call. Late consent listings have already been applied
   at the tick (§12.1).
3. **A rate limit's wait is still running** (§10.2): no Plaud call.
4. **`plaud` is held** by another run: skipped as busy. A long run serves the turn's light part
   itself (§9.2).
5. **Otherwise it runs** (§11.1), whatever it then meets: a consent stop, a hold, the breaker, a
   failure.

With `schedule_minutes = 0` no turn comes due, and nothing pings; the runbook pauses the check
(§17.5). The ping is withheld only when a push is owed and wasn't delivered (§16).

## 10. The Plaud client and the token store

### 10.1 The client: read-only, and leaks nothing

- **The `of_` fix.** The client never checks an ID against a pattern: Plaud IDs are opaque (main spec
  §6.3). Every request uses the ID exactly as the newest listing gave it, never `sources[].ref`, which
  holds `audio-router`'s bare form for older recordings. The canonical form is only for comparing.
  The fake's request log proves it.
- **Only reads.** One request function sends `GET`, plus the refresh `POST` to the token endpoint. A
  test drives every path and asserts nothing else reaches the fake.
- **Redirects and the token.** API calls refuse every redirect, as `alerts.py`'s `_NoRedirect` does
  (`alerts.py:49-55`). `Authorization` goes only to the API host. Presigned links (the audio, and
  blocks behind a `data_link`) are fetched without it, must be `https`, and may follow a redirect
  only to another `https` URL. A plain-`http` link is refused: it would send a signature that grants
  Dan's audio in clear.
- **stdlib `urllib`,** like `alerts.py`: no new dependency, nothing that logs URLs.
- **Timeouts:** 10 seconds to connect, 60 to read. A download has a stall rule instead: no bytes for
  60 seconds, or under 50 KB/s on average after its first minute, or longer than a ceiling scaled
  from `Content-Length`.
- **Nothing secret or personal in logs.** No token, header, refresh reply, presigned query
  (`X-Amz-…`), body or title. A Plaud ID appears as the first 8 hex characters of the SHA-256 of its
  canonical form. Every error the client raises is its own class with a fixed message, raised `from
  None`, so no chained exception carries a URL (some `urllib` errors put the whole URL in their
  text). Plaud jobs log class names only, even at `--debug`: no `exc_info`, unlike `ops._run_held`
  (`ops.py:569, 591`).
- **`[plaud] api_base`** must be `https://`, or `http://` to the loopback for tests. Its default
  comes from the copied client.

### 10.2 Errors, pacing and budgets

- **Pacing:** one call at a time, at most one a second on average. S10 may change it.
- **A scheduled run's budget** is 10 minutes of calls. Imports from the CLI and the Sync screen have
  no budget; a download under way finishes under its stall rule.
- **429:** a `Retry-After` within the budget is waited out, in 5-second slices (the heartbeat thread
  keeps writing). A longer one ends the run, "rate limited until 14:40", and the next run waits. The
  sweep's cap halves, then grows back by one per good run.
- **5xx and timeouts:** retried with backoff (30 seconds, doubling, with jitter). Three in a row of
  one kind end the run: "Plaud unavailable".
- **Connection failures** end the run: "offline".
- **One recording failing** is retried once, then counted. Its next attempt backs off. After 3
  failures in a row it joins Status's failing list (§15.2) and stops counting as an attempt (§11.7).
- **A link that answers 403 or has expired:** the envelope is fetched again, once. If its blocks
  still fail, the check is **degraded** (§14.1). Links are never stored. Both lifetimes the sources
  give (300 seconds for content, 24 hours for audio) work with this; S8 measures them.
- **A short body or broken JSON** fails that fetch, and nothing is written.
- **A 403 from the API itself** ends the run: "Plaud refused access (403)". It may mean the
  subscription lapsed.
- **A 401:** §10.3.

### 10.3 The token store

- **Where (controller's call):** `/srv/recordings/plaud` (mode 0700, owned by the app's user), named
  `RECORDINGS_PLAUD_HOST` in `deploy.env`, mounted read-write at `/plaud` in `worker` only. It holds
  `tokens.json` (0600) and `tokens.lock`. `[plaud] token_store` names the token file. The worker
  refuses a folder wider than 0700 or owned by someone else, and a token file wider than 0600, naming
  the path and the `chmod`.
- **Format:** our own JSON: `version`, `access_token`, `refresh_token`, `expires_at` (UTC),
  `obtained_at`, `refreshed_at`, and a client ID or secret if S1 shows the refresh needs one (never in
  `config.toml`, which every container mounts and the backup carries).
- **Every write is atomic:** a temporary file in the same folder, created exclusively at 0600;
  fsync; rename; fsync the folder.
- **Refresh:**
  - Before expiry: when under 5 minutes, or 10% of the token's life, is left. And once after a 401:
    refresh, retry the call once.
  - One at a time, under `tokens.lock`. The file is read again after the lock is taken, because the
    CLI and the daemon can both refresh.
  - The new file is written before the new access token is used. A reply without a new refresh token
    keeps the old one.
- **What a failure means:**
  - **Refused** (S4 gives the exact codes, typically 400 or 401 with `invalid_grant`; with no clearer
    answer, any 4xx except 408 and 429): `PlaudAuthError`. The run stops and pushes at once (decision
    5). Scheduled runs make no Plaud call until the token file changes (by inode, size and time). A
    **Sync now**, or `recordings plaud check`, tries once anyway, and a success clears the pause.
  - **Unreachable, 5xx, 408 or 429:** an outage (§10.2), never a sign-in problem.
  - **A 401 again after a successful refresh:** the worker makes the cheap current-user read. If that
    is also 401, `PlaudAuthError`. If it succeeds, the one call fails as that recording's failure.
- **Status:** "Plaud sign-in good", or "sign-in needed since 23:00 · new recordings wait on Plaud:
  nothing is lost · at the Mac: `make plaud-sign-in`". If S1 shows the refresh token has its own
  expiry, Needs attention shows it 7 days ahead.
- **`recordings plaud check`** makes one cheap signed-in read (the current user), and prints whether
  the sign-in works, its expiry, and whether a refresh token is present. Exit 0, or 77.

### 10.4 Secrets around the token

- `token-import` keeps only its own keys from the vendor's file, and drops the rest (an e-mail or a
  user ID may be in it).
- The token never passes as an argument or an environment variable, and is never printed.
- CLAUDE.md gains: "`tokens.json` is a secret: used by reference, never read or printed. A Claude
  session never runs `make plaud-sign-in` or `token-import`."

### 10.5 Signing in again, on the Mac (controller's call)

`make plaud-sign-in` runs on the Mac. `SERVER` and `REPO_DIR` are `make` variables, defaulted from a
git-ignored `local.mk` that the Makefile includes with `-include local.mk`, so Status can print the
whole command. Make expands them on the Mac: the server's non-interactive shell has no `REPO_DIR`.
It:
1. Makes a temporary `HOME` with `mktemp -d` under `$TMPDIR` (which Time Machine skips), mode 0700,
   removed by a `trap` whatever happens.
2. Runs the vendor CLI's login with that `HOME`. The browser opens and Dan signs in. The separate
   `HOME` keeps this sign-in apart from the Mac's own Plaud CLI and MCP sign-ins.
3. Pipes the token file over ssh. In the Makefile's recipe:
   ```make
   ssh "$(SERVER)" "cd '$(REPO_DIR)' && sh docker/up-deployed.sh exec -T worker \
     recordings plaud token-import -" < "$$tmphome/.plaud/tokens.json"
   ```
   `rc` is a shell function in the runbook, so it isn't available over ssh, and `up-deployed.sh`
   supplies the image tag that compose requires. `-T` passes stdin without a terminal.
4. `token-import` checks the keys, makes one cheap call, and installs atomically at 0600. It prints
   only "signed in: yes · access token expires 2026-10-23 16:20 · refresh token: present · account
   1a2b3c4d" (8 hex of the SHA-256 of the account's ID). If the call fails it installs nothing and
   exits 77.

It exits non-zero on any failure, naming the step. The new file clears the worker's sign-in pause,
and the next run sends one "works again". No phone sign-in: the vendor's login needs its client
secret and a callback on `localhost` (main spec §9.1).

### 10.6 The fake Plaud server

A test helper, `packages/core/tests/fake_plaud.py`, with a `plaud_server` fixture beside `ntfy`,
built like `FakeNtfy` (`conftest.py:483-539`): a stdlib `ThreadingHTTPServer` on `127.0.0.1:0`, in
a daemon thread. Every title, name and text is invented; field names come from spikes S5 and S8. It
is built in these steps, each its own task with its own tests:
1. **The account and the listing:** a synthetic account of recordings built like `plaud_env`'s
   envelopes, with tiny WAVs from `wav_bytes`; the paged list endpoint, in either ID form.
2. **Envelopes and storage:** `files/{id}`, and block bodies and audio on a second port standing in
   for the storage host, with presigned-style links.
3. **The sign-in:** the token endpoint and the current user, with token expiry read from the same
   injectable clock as the store.
4. **The request log:** method, path, host and header names for every request. Never a body.
5. **Faults:** every injection in §21's list, each switched on per test.
6. **Dan's edits:** a title, a speaker rename, regenerated notes, a deletion, a restore; and a hook
   that changes the account after request number K.

It is not a dev server: demo mode makes no network call (main spec §17).
- It is not a dev server: demo mode makes no network call (main spec §17).

## 11. A sync run: the listing, the compare and the import

### 11.1 One run, step by step

The same steps serve a scheduled run, **Sync now**, and the CLI:

```
take the plaud lock                      → held: busy, skip
read the consent list, fail closed       → unreadable: consent stop, no fetch or download (§12.1)
apply late consent listings              (§12.1; also at every tick, with no Plaud call)
check the sign-in, refresh if due        → refused: stop, push (§10.3)
list every page                          → bad, partial or complete (§11.2)
census of ID forms; field names
classify every listed ID                 (§11.3)
privacy from the listing alone           (§13.1, cases 2 and 3)
mark listing changes for a check now     (§14.2)
holds                                    → a hold stops imports and the sweep (§11.4)
import waiting recordings, up to the cap (§11.5)
a chunk of the sweep                     (§14.2)
the breaker                              (§14.4)
record the run; judge it good or not     (§11.7)
release the lock; the turn pings         (§9.7, §16)
```

- **Privacy from the listing runs whenever there is a listing**, whatever else is paused: auto-import
  off, a hold, the breaker, a consent stop. Only no listing at all (the sign-in, an outage) delays it,
  and Status says so. A late consent listing needs no listing at all.
- **A Compare** stops after "classify". It writes only `plaud_listing`, the cache, the next-check
  marks of §14.2 and the run's result. It writes nothing to the archive. So a Compare between syncs
  never hides a change from the next sync.

### 11.2 The listing, and when it is good

- **It pages until an empty page.** A short page isn't the end, because Plaud may clamp page sizes.
  Reaching the cap (500 pages) makes the listing bad.
- **Repeats** are de-duplicated by canonical ID, and counted.
- **Malformed rows** (no ID or no title field): up to 1%, and at least 2, are tolerated, counted and
  shown on Status. More make the listing bad.
- **An HTML page, or JSON of the wrong shape,** is a bad listing, never an empty account.
- **A census of ID forms** is kept per listing: `of_` hex, bare hex, other.
- **Each listing is one of:**
  - **bad:** an error, the cap, too many malformed rows. Nothing is decided from it.
  - **partial:** it ended on an error part-way. Its rows feed §13.1's privacy checks only.
  - **complete,** and then either **held** (§11.4) or **good.**
- **The last good listing** (its ID-set hash, its census and its time) is kept in `plaud_state`.

### 11.3 The buckets, and the equations

Every listed ID lands in exactly one bucket, in this order of precedence:
1. **here:** in the archive, matched by its Plaud ID in either form (canonical, through the index's
   `find_by_source`). A recording here that is consent-listed or kept out late stays "here", with its
   flag.
2. **excluded:** on the consent list, or kept out by title (§12.2). Shown by ID, date, time and
   duration, never by title.
3. **waiting:** not here, and the next scheduled run will bring it in.
4. **missing:** not here, and it won't come without Dan: the gate closed (§11.6), the schedule or
   auto-import off, a hold (§11.4), the disk guard, or it failed 3 times (or is parked).

**listed = here + waiting + excluded + missing,** with repeats counted once. Beside it:
- **In the archive from Plaud = here + gone from Plaud + absent from this listing** (the last term
  shows only when it isn't 0, and becomes "gone" once the next listing agrees, §12.3).
- **Of the waiting and missing, N arrive private.**
- **Consent list: 14 IDs, 4 on Plaud now.**
- **Updated on Plaud:** recordings here where a check in the last 7 days found a change.
- **Held titles** (main spec §7.4: `audio-router` never fetched them) are waiting or missing like any
  other, and arrive tagged `private`.

The dry run prints all of these, and the property test (§21) checks the first equation over random
accounts, overlaps included.

**Matching is by ID only.** The listing carries no audio hash (main spec §9.1). The hash match
happens at import: identical bytes join the existing recording as a new source reference
(`Writer.add`, main spec §6.4).

**`audio-router`'s deferred rows** (`deferred-to-2b`: audio but no snapshot) are ordinary Plaud IDs.
Listed, they are waiting and are imported from Plaud. Not listed, their audio exists only in
`audio-router`'s frozen archive, because the 2a runbook deleted the import copy. 2b doesn't import
them (controller's call). The runbook's dry-run step counts them from the 2a import's report.

### 11.4 Holds: changed IDs, a flood, many gone

Three conditions stop imports, and one action clears them. A fourth, smaller one settles by itself.
Each step below is a separate piece of work.

1. **Measure each complete listing against the last good one:**
   - **newly absent:** recordings here that the last good listing had, and this one doesn't (by any of
     their Plaud IDs);
   - **a census change:** a form appears that the last good listing didn't have; an ID present in
     both listings (by canonical form) is now in a different form; or a listed ID is in a form the
     consent parser can't read (§12.1). Shares of each form are not compared, so a backlog of new
     recordings in a form the last listing already had never trips it;
   - **overlap:** the share of the last good listing's IDs present now;
   - **waiting:** as in §11.3.

   A listing with no last good one to compare with (2b's first, or the first after a rebuild) holds
   nothing: the runbook's dry-run checks (§17.5) and Dan's Accept (§11.6) stand in for it.
2. **Classify it:**
   - **the ID-form hold:** a census change, or under 80% overlap (the `of_` lesson, aimed at the
     consent list as well as the archive);
   - **the flood guard:** more than 25 waiting, or 10% of those here (whichever is larger), **and**
     at least half that many newly absent. A backlog alone (40 new recordings after a week away)
     doesn't trip it: it arrives at 20 per run;
   - **a large shrink:** more than 5, or 5% of those here (whichever is larger), newly absent;
   - **a small shrink:** from 1 up to that many newly absent;
   - otherwise the listing is **good**.
3. **Effects:**
   - **The first three are holds.** Nothing is imported (scheduled, Sync screen or CLI), nothing is
     marked gone, and the sweep pauses. Privacy from the listing still runs, and late consent
     listings still apply at every tick. The hold pushes once, at once (§16, `plaud:listing`).
     **Sync now** lists and reports, imports nothing, and answers "Held: see Status". The Sync
     screen's import buttons are disabled, with the hold's line.
   - **A small shrink** holds back only the marking of those recordings as gone (§12.3). Imports and
     the sweep go on, and the listing counts as good.
4. **Clearing a hold takes one action: Accept this listing.** It is a button on Status and on the
   Sync screen (request `accept`), and `recordings plaud accept-listing` on the server. It records
   this listing as the last good one, marks the newly absent recordings gone, and lets imports
   resume.
   - **It is refused while the consent list matches fewer Plaud IDs than it did in the last good
     listing.** Status then says "Fix the consent list first: it matches 0 of Plaud's IDs, against 4
     before (runbook)". That is the sign that Plaud's IDs changed under the list.
   - A hold also clears by itself when a later listing no longer meets its condition (a fix release,
     or Plaud putting things back).
   - **Status says which hold, what to check, and offers Accept:**
     - the ID-form hold: "Plaud sync paused: Plaud's IDs changed form since the last good run.
       Check the runbook's steps, then Accept this listing."
     - the flood guard: "Plaud sync paused: 1,227 recordings look new, and 1,180 here are missing
       from the listing. If they are recordings you made, Accept this listing; if not, don't, and see
       the runbook."
     - a large shrink: "Plaud now lists 40 fewer of the recordings here. If you deleted them in
       Plaud, Accept this listing: they show as gone from Plaud."
5. **A small shrink settles by itself** when the next good listing, at least an hour later, is
   missing the same recordings (the same absent set), whatever else changed. If the absent set
   changes, the wait starts again. One still unsettled after 24 hours joins Needs attention.

The thresholds are constants, not settings.

### 11.5 Importing one recording

1. **Before any byte:**
   - The consent list as it is now: the run checks the file (inode, size, time) and reads it again if
     it changed. A change that makes it unreadable stops imports (§12.1).
   - The keep-out words, on the listing's title.
   - The ID must be in the newest listing. If that listing is older than `schedule_minutes` (always,
     with the schedule off), the run lists again.
2. **The envelope:** `files/{id}`, with the ID as listed. **As soon as it answers,** check its title
   against the keep-out words, and its ID against the consent list, before any block or audio is
   fetched. If either matches, nothing of it is written anywhere.
3. **The blocks:** each block behind a link, without `Authorization`, over `https`. Assemble the
   snapshot as `audio-router` did, stamping `_meta.tool` as `recordings` (the normaliser drops
   `_meta`, so no hash changes; §11.6 uses the stamp to tell a rebuilt `state.db`). Every snapshot
   2b writes, a check's included (§14.1), carries the stamp.
4. **Tags:** `private` if any title (the listing's or the envelope's) matches `auto_private_patterns`
   (§13.1). It goes in `Incoming.tags`, landing in the same rename as `recording.json`, before any
   source or output (main spec §7.4).
5. **The disk:** below `stop_free_percent`, or when twice the audio's `Content-Length` would leave less
   than that margin (`Writer.add` copies the download and hashes it again, `writer.py:649-654`),
   imports stop for this run, and the rest become missing ("the disk is nearly full").
6. **The audio:** streamed from its presigned URL into the archive's `.tmp/plaud-dl/`, never into
   memory or `/tmp` (a 1 GiB RAM tmpfs, `compose.yml:25`). `mkdir` is retried once, because another
   process's `_drop_empty_tmp` can remove an empty `.tmp/` between two levels (`writer.py:882-885`).
   The byte count must equal `Content-Length`. The stall rule applies (§10.1).
7. **Measure** with ffprobe, as the 2a import does. A duration that disagrees with Plaud's is a timing
   verdict (main spec §9.1.1), not a failure.
8. **Add:** `Writer.add(Incoming(…))`: a `RawSource` of kind `plaud`, its ref the ID as listed, its
   payload the snapshot. `recorded_at` comes from Plaud's start time in the default zone (main spec
   §6.2; S12 confirms the reading). Identical bytes merge (main spec §6.4). The origin is
   `plaud-import:<run ID>`.
9. **Reconcile:** `Writer.reconcile_plaud` writes Plaud's outputs. A snapshot with link errors builds
   nothing (main spec §9.1.1).
10. **Record:** a `plaud_checks` row (unsettled, §14.2), the marker (§13.2), the title hash, and an
    event: `imported` or `arrived private`.

- No recording lock is held across a network call (§9.4).
- One recording's failure is counted, and the run goes on. After 3 failures it is missing ("failed 3
  times"), and Dan can park it (§14.6). An archive-level error (the sentinel, `state.db`, the index,
  a full disk) stops the run.
- **A scheduled run imports at most 20.** The CLI and the Sync screen have no cap.
- **No confirmation dialog** (controller's call; main spec §12.2). Progress: "Importing 5 of 31…".
  The end: "31 imported · 4 arrived private · View". **View** opens Untagged, and "4 arrived private"
  opens the Private section.
- **A hash merge under a new Plaud ID** has no marker for that ID, so §13.1's case 4 runs, and can put
  back a `private` Dan removed. That errs private, and gets its note.
- **A hash merge into a flagged recording** (consent-listed or kept out late) writes nothing: the
  download is discarded, and the new ID counts as excluded. The consent list excludes the recording,
  not only its ID (main spec §9.1).

### 11.6 The first catch-up

- **The gate (controller's call).** Until `plaud_state.catch_up_done_at` is set, scheduled syncs
  don't run, whatever `schedule_minutes` says, and the worker refuses the requests §9.3 names. Only
  Compare runs. Before the very first catch-up, Status says "Scheduled sync waits for the first
  catch-up (runbook)"; this isn't a failure, and raises no alert. The template ships
  `schedule_minutes = 0` as well, and an unset value means 0.
- **A rebuilt `state.db`** (controller's call). At start (§9.2), the worker checks whether its records
  were lost:
  - **How it tells, with no new storage:** the archive holds snapshots that 2b's client wrote (their
    `_meta.tool` is `recordings`; `audio-router`'s say `audio-router`), but `catch_up_done_at` is
    missing, or `plaud_state.last_ended` is older than the newest of those snapshots (a `state.db`
    restored from a backup older than the archive).
  - **Then** it closes the gate (clearing `catch_up_done_at` if a stale one is there), pushes once,
    at once ("recordings: Plaud sync waits for you: its records were rebuilt"), and Status says so,
    in Needs attention.
  - **Reopening is the supervised path:**
    1. A dry run (Compare, or `sync --dry-run --list`) lists every recording on Plaud that isn't here,
       with no title for a consent-listed one. A recording kept out before the loss, whose title no
       longer carries a word the worker knows, shows up here as missing, for Dan to decide: add it to
       the consent list, or let it in.
    2. Dan presses **Accept this listing** (§11.4), which sets `catch_up_done_at` and the last good
       listing. The schedule then goes on, under the usual cap and holds.
  - `--catch-up` isn't used again: the archive already holds what the first one imported.
- **`recordings plaud sync --catch-up`** is a supervised CLI run. It holds `ops` and `plaud` (§9.4),
  and has no import cap and no flood guard, because Dan has just read the dry run. The ID-form hold
  and the consent list still apply. It sets `catch_up_done_at` when it ends without a run-level
  failure. A second `--catch-up` is refused: "already done on 2026-10-25; use `recordings plaud
  sync`".
- **The order** (main spec §19 step 7; runbook section "The catch-up"):
  1. The dry run is checked (step 6).
  2. **Compare again** if Dan edited the consent list since, and check the new IDs are excluded.
  3. `recordings plaud check --volatility` (§14.4; second half, so skipped if 2b ships in halves and
     only the first has shipped).
  4. A backup tagged `pre-import`.
  5. `recordings plaud baseline` (§14.5).
  6. `recordings plaud sync --catch-up`.
  7. `recordings validate`.
  8. A backup tagged `post-import`.
  9. **"You may start tagging now"** (decision 7).
  10. Turn the schedule on (`schedule_minutes = 20`, then `rc restart worker`), and unpause the
      sync's Healthchecks check. Watch Status. If "All here" doesn't show, Status says why.
- The NAS keeps `pre-import` and `post-import` for good (main spec §15.1), so retention needs no
  change.

### 11.7 A good run

- **One rule.** A run is good when its listing was good (a small shrink counts, §11.4) and, if it
  made any import, check or fetch attempts, at least one of them succeeded. A run with attempts and
  no success is never good, however few it made.
- **What counts as an attempt:** every import, check or fetch the run tried, **recordings on the
  failing list included.** So a run that can only retry failing recordings, all of which still fail,
  is not good. A systemic failure (a new envelope shape, every link refused) can't turn into a false
  recovery once every recording has reached the failing list.
- **Not attempts:** parked recordings, which aren't tried at all, and a busy recording (§9.2).
- **The 2-hour rule** (decision 5): `plaud_state.failing_since` is set at the first run that isn't
  good. The push goes at `failing_since + 2 hours`.
- **Recovery, by cause.** `plaud_state` keeps why the streak began.
  - A streak that began with no good listing (Plaud down, offline) clears at the next good run.
  - A streak in which attempts failed with no success clears only at a good run **with at least one
    successful import, check or fetch**. Only then is "works again" sent.
  - A good run with no attempts leaves a work streak as it is: it neither sets nor clears it.
  So a systemic failure can't flap between "failing" and "works again" as its recordings back off.
- **One push per cause.** While a condition that has already pushed explains the failure (the
  sign-in, a consent stop, a hold), the 2-hour push is suppressed.
- **A lone recording that always fails never pushes** (P4). A run that retries a recording on the
  failing list also checks at least one recording that isn't on it (the most overdue), so its
  outcome tells the recording's failure from the sync's. Parking stops even the retries.

## 12. Keeping out

### 12.1 The consent list

- **One list, on the server:** `$RECORDINGS_HOME/consent/consent-list.md`. The Mac's old copy is no
  longer read.
- **A folder mount, never a file.** `RECORDINGS_CONSENT_HOST` names the folder (mode 0700), mounted
  read-only at `/consent` in `worker`. `[plaud] consent_list` is `/consent/consent-list.md`. A
  single-file bind mount follows the file's inode, so after an editor, `sed -i` or `rsync` replaces
  the file, the container would go on reading the old one (the 2a runbook mounted the file alone,
  `first-run.md:717`; fine for a one-off container, wrong for a long-lived one). A missing file in the
  folder is "consent list missing", never a container that won't start.
- **Backed up:** the folder is mounted read-only in `backup` too, and listed in `[backup]
  extra_paths`. Doctor warns when it isn't, as it does for `deploy.env`. A restore then never brings
  back an older list.
- **Editing it:** any editor on the server works, because the folder mount sees a replaced file. Or
  from the Mac, `make consent-add ID=<id>`: it checks that the ID parses, appends `` - `<id>` — added
  2026-10-23 `` by writing a new file and renaming it, over ssh, then prints what the worker reads
  (`recordings plaud consent`).
- **The grammar, exactly.** The file's meanings are those of today's parser (`consent.py:17`, its
  docstring, and `test_the_consent_list_is_read_as_audio_router_reads_it`). 2b adds only the ID
  forms. Each line is one of four kinds:
  1. **An exclusion:** after optional spaces, an optional bullet (`-` or `*`, then spaces) and an
     optional backtick, a Plaud ID, then an optional backtick and spaces, then **a dash** (`—`, `–`
     or `-`, with a note after it if Dan likes) **or the end of the line.** The ID may be in any hex
     form `canonical_plaud_id` reads: 32 hex digits, bare or with `of_`, in any case. So
     `` - `of_3fa9…` — asked not to keep `` and a bare ID alone on its line are exclusions.
  2. **A kept line:** an ID in that position followed by anything other than a dash or the end, such
     as `3fa9… kept: she agreed later` or `- 3fa9… (fine to keep)`. It names a recording Dan kept,
     as `audio-router`'s rule intends, and is **not** an exclusion. Neither is an ID inside a
     sentence.
  3. **A comment:** a line starting `#`. Ignored.
  4. **A typo:** a line whose first word (after the bullet and backtick) holds 24 or more hex digits,
     with or without `of_`, but isn't a valid ID (31 or 33 hex, a stray character). It fails closed
     (P8): the list is unreadable.

  Every line that holds an ID but isn't an exclusion (a kept line, an ID in a sentence, or an ID
  after a `+` or `1.` bullet, which today's parser doesn't read either) is listed by line number by
  `recordings plaud consent` and the dry run, so Dan can check that each one meant "keep".
- **A consent stop** (a missing, unreadable or empty list, one with no exclusions, or a typo line):
  - no `files/{id}` call and no download for any recording;
  - the listing, §13.1's privacy from the listing, and late consent listings for the exclusions that
    do read, still run;
  - **no title of any recording not here is stored or shown**, because the list can't say which are
    consent-listed: the cache keeps none, and **Not here yet** shows ID, date, time and duration only,
    with "titles hidden until the consent list reads";
  - the CLI exits 78;
  - it pushes at once, then daily (`plaud:consent`);
  - Status, doctor and `recordings plaud consent` name the line numbers, never the text: "the consent
    list has 2 lines that look like IDs but can't be read (lines 14 and 22)".
- **Read** at every tick when the file has changed (by inode, size and time, §9.2), at the start of
  every run, and before each recording's first byte (§11.5).
- **A late consent listing** (an exclusion whose recording is already here, by any of its Plaud IDs):
  - **It is applied at the worker's next tick** (§9.2), from the consent list and the index alone,
    with no Plaud call: whatever the sign-in, the listing, the schedule or the gate. It is an
    automatic `private`, not a tag of Dan's, so decision 7 doesn't hold it back.
  - The recording gets `private` through `mutate`, found through the index (a strict lookup: an
    index that can't answer is retried at the next tick, and shown, never skipped).
  - It is flagged: "On the consent list, but in the archive: take it out by hand (runbook)".
  - Nothing of it is fetched again (§12.2's rule).
  - **It pushes once, at once (P15), unless the recording already has an open privacy flag or is
    gone from Plaud.** Then its flag changes to "On the consent list, but in the archive", with no new
    push. So step 1 of "Taking a recording out by hand" never pushes twice for the same recording.
  - Status, Compare and the dry run name it by archive ID.
  - Every other recording keeps flowing.
- **Recordings 2a imported despite an exclusion:** 2a's parser read only bare lower-case IDs, so an
  `of_` or upper-case exclusion was skipped, and its recording may have been imported. 2b's parser
  reads it, so that recording becomes a late consent listing at the first tick: tagged `private` and
  flagged. `recordings plaud consent` lists these by archive ID, marked "2a's parser missed it". The
  runbook's dry-run section has Dan review each one, and take out by hand any that mustn't stay,
  before the catch-up's `pre-import` backup (§17.5). 2a's `post-import` backup already holds them.
- **Status shows what the worker read:** "Consent list: 14 IDs, 4 on Plaud now · file changed
  2026-10-23 09:12 · read 14:20". If Dan's edit doesn't show, the worker didn't see it.
- **No title of a consent-listed recording is ever stored or shown:** not in the cache, the Sync
  screen, `state.db`, logs, alerts or pushes, at any time. It appears by ID, date and duration only.
  The worker drops its title from the listing before anything else reads the row.
- **ID forms:** the parser reads every hex form `canonical_plaud_id` folds. A listed ID in another
  form trips the ID-form hold (§11.4), because the list couldn't name it.

### 12.2 The keep-out word

- **The words:** `[plaud] keep_out_word` (default `noarchive`; 1 to 32 letters, digits, `-` or `_`),
  plus every word the worker has ever enforced, kept in `plaud_state`. Changing the word never
  releases what an older word kept out. `recordings plaud keep-out-words --drop <word>` removes an old
  one, and doctor lists them. An invalid word leaves the worker idle, with Status and doctor saying
  "configuration: `keep_out_word` is invalid".
- **How a title matches** (hardened like `is_private_tag`):
  1. Fold the title and each word: NFKC, drop format characters (category Cf) and
     `HIDDEN_CODEPOINTS`, then casefold.
  2. Skip leading characters that aren't letters or digits (spaces, punctuation, symbols, emoji), then
     Plaud's "MM-DD " prefix, then such characters again.
  3. Read on, letters and digits only, skipping spaces, `-`, `_` and `.` between them. If they spell
     the word, and the character after its last letter isn't a letter or digit, it matches.

  So `NoArchive: coffee`, `No archive`, `no-archive`, `[noarchive]`, `🔒 noarchive`, `10-21 noarchive`
  and a zero-width-prefixed `noarchive` match. `noarchived` and `No archives` don't.
- **Checked** on every listed title at every run, and on the envelope's title as soon as `files/{id}`
  answers (§11.5).
- **Kept out:** `plaud_listing` records when, and by which word. The recording is never fetched and
  never imported. The sweep skips it, and Re-sync answers "Not checked: kept out".
- **Kept out stays kept out (controller's call).** If the title later loses the word (Plaud's AI
  retitles it, autocorrect mangles it, Dan edits it), it stays out, shown as "was kept out by title".
  Only **Release** lets it in, at the next sync.
- **Release asks first** (controller's call), because it turns "never download" into a copy kept for
  good, like removing a last `private` tag in tagging part A. It is the fourth exception to main
  spec §12.2's "no confirmation dialogs".
  1. Pressing **Release** on a row sends a `release-preview`. The worker answers with the recording's
     current title, from its in-memory copy of the newest listing (or a fresh listing), through the
     cache (§9.5). The title is never written anywhere else, and the preview row is deleted once
     answered or after 10 minutes.
  2. The question shows it on screen: "Let 'Coffee chat about grant timelines' (2026-10-23 14:05,
     12 min) in? It downloads at the next sync (14:40), and then stays in the backups." Cancel, or
     Let it in.
  3. **Let it in** sends `release`. When the worker claims it, it re-checks the current title against
     every enforced word, and the ID against the consent list. If either matches, it refuses, and
     the reply says why ("its title still starts with `noarchive`", "it is on the consent list").
  4. Otherwise the row reads "Released · comes in at the next sync (14:40) · Undo". **Undo**
     (`unrelease`) keeps it out again, until a sync has fetched it.

  The CLI's `recordings plaud release` asks y/N on the terminal, showing ID, date and duration only.
- **Too late** (the recording was already here):
  - `private` at once (§13.1, case 3);
  - never fetched again, as above;
  - a flag: "Kept out by title, but already in the archive: take it out by hand (runbook)";
  - one push, at once (P15), unless the recording already has an open privacy flag.

  The flag clears when the recording leaves the archive, or when Dan presses **Release** (with its
  question) to keep it after all; `private` stays, because the sync never removes it.
- **The baseline** (§14.5) checks the keep-out words on every title of 2a's recordings too.
- **What Dan should expect,** written in the README: type the word first (a phone text replacement
  helps); the rename must reach Plaud before the next sync. S9 measures whether a title can be set
  before the listing shows the recording. If it can't, the README says the word usually arrives late,
  and what late means. The README also says the match is forgiving, so an ordinary title such as "No
  archive access: IT ticket" can be kept out too; it waits under Kept out until Dan releases it.
- **After a lost `state.db`,** the record of what was kept out, and of older words, is gone (§11.6).
  The rebuild's dry run shows such a recording as missing, for Dan to decide. The consent list is the
  durable way to keep a recording out.

### 12.3 Gone from Plaud (controller's call)

- **When:** every Plaud ID of a recording here is absent from two consecutive good listings at least
  an hour apart, and §11.4's limits hold.
- **What happens:** nothing is deleted, ever, and nothing is pushed. The recording is shown on Status
  and in the Sync screen under "Gone from Plaud: 13 · 2 in the last 7 days", newest first with "gone
  since", each linking to "Taking a recording out by hand" (§12.4), and in Details as "Gone from
  Plaud since 2026-10-23 (kept)". A fresh deletion made to honour someone's request stands out from
  Dan's routine clean-ups. The README says that decision 2's "deleting it in Plaud still works" holds
  before the sync.
- **The sweep stops checking it.** Nothing probes a recording Plaud no longer lists: Dan may have
  deleted it to keep it out.
- **If it returns** to the listing, it is here again, and nothing is imported twice.
- **Sync everything** counts it, and doesn't fail on it.

### 12.4 Taking a recording out by hand

One runbook section, "Taking a recording out by hand", serves a late consent listing, a late
keep-out, a recording gone from Plaud, and anything Dan must not keep:
1. **Put its Plaud ID on the consent list** (`make consent-add`). From the worker's next tick, the
   sync tags it `private`, never fetches it again, and can never bring it back. If the recording is
   already flagged, or gone from Plaud, no new push is sent. Otherwise one push follows, which is
   expected.
2. `rc stop worker web`.
3. **Delete its folder for good:** `rm -rf` on its folder under `$RECORDINGS_HOME/archive/recordings/`.
   Never move it to `trash/`: `trash/` is inside the archive, so it is backed up every hour and
   mirrored (`backup.py:244-247`, `mirror.py:155-160` leave out only `.tmp/`).
4. `rc run --rm worker recordings reindex --allow-missing`. Then `rc start web worker`.
5. After the next mirror run, check that the NAS mirror and the Mac no longer have the folder.
6. **What still holds it,** as Forget reports (main spec §7.6, step 6):
   - the restic snapshots taken since it arrived, until retention ages them out (up to 12 months);
     the `pre-import` and `post-import` snapshots never age out, and only a NAS-side `restic rewrite`,
     with Forget's steps, removes it from them;
   - the NAS mirror until its next run, and the Mac's copy of it;
   - Synology Drive's versions and Time Machine on the Mac;
   - Plaud's cloud, unless it is deleted there too.

This is a by-hand, permanent exception to main spec §6.4's "Delete moves the folder to `trash/`",
which describes the app's own Delete, like Forget.

## 13. Auto-private (decisions 1, 2 and 3)

### 13.1 When `private` is added, exactly

The patterns are `[plaud] auto_private_patterns` (main spec §7.4). Each title is tried as given and
folded as §12.2 folds it; more forms can only add `private`. The sync adds `private` (`by: auto`) in
these cases, and never removes it:

1. **At import,** when any title the listing or the envelope gives matches (§11.5, step 4).
2. **A title changes in Plaud.**
   - Every run, for every listed recording here, the listed title's hash is compared with the
     **marker** (§13.2), never with `plaud_listing`. Decision 1 asks for recent titles to be
     re-checked. Checking all of them includes the recent ones, and costs nothing more.
   - If they differ, the patterns run on the new title. A match adds `private` through `mutate` at
     once, from the listing alone (controller's call), so a fetch that keeps failing can't leave a
     private title untagged.
   - If the recording wasn't private, it gets the note "re-tagged private: title changed" (decision
     3), shown in Details and the Sync screen's Updated list.
   - The same rule runs inside reconcile, on new snapshots' titles, so a title first seen in a fetch
     is checked too.
3. **A late keep-out word** (decision 2): `private` at once, and §12.2's flag and push.
4. **The first check of a recording the marker has never seen:** 2a's imports, a hash merge under a
   new Plaud ID, and, after `state.db` is lost, any recording the rebuild couldn't seed (§13.2).
   - The patterns, and the keep-out words, run on every title of every snapshot, under every Plaud
     ID, erring private, as in 2a.
   - The baseline (§14.5) does this once for the whole archive, at step 7, before any tagging.
   - Any later re-add gets the note "re-tagged private: the sync's record was rebuilt", so it never
     looks as if Dan's decision was ignored.
5. **A new pattern.** `plaud_state` keeps the pattern list by version. When `auto_private_patterns`
   gains a pattern, a new version is recorded, and every recording's titles are checked against the
   patterns its marker's version didn't have, **and those only**. So a tag Dan removed stays removed
   for the patterns it already passed (decision 3). Status: "patterns changed: 1,234 titles checked,
   6 tagged private". A removed pattern re-checks nothing.

A **late consent listing** adds `private` too (§12.1).

The sync never removes `private`, and never runs a pattern again on a title it has already checked
against it. **The 30-minute grace is gone** (decision 1): a placeholder title doesn't match, so the
recording arrives untagged, and Dan's rename is checked at the next sync.

### 13.2 The marker

- **What:** for each recording in the archive and each of its canonical Plaud IDs, a plain SHA-256 of
  the newest checked title's UTF-8 bytes, exactly as Plaud gave it (so any change, case included,
  re-checks), and the version of the pattern list it passed. Only archived recordings have markers,
  and their titles are already in `recording.json` and the merge-base revisions, so the hash reveals
  nothing new. A kept-out or consent-listed ID has no marker, no title and no hash.
- **Due** when the newest title's hash differs, or the pattern list has a newer version.
- **It moves only once the result is written:** no match, or `private` committed. If the `mutate`
  fails (a file edited outside the app, a lock timeout, the sentinel), the marker stays, the next run
  tries again, and Needs attention shows it at once, by ID: "Should be private, but its file was
  edited outside the app: run `recordings reindex`". It pushes once (P15, §16). A lock timeout alone
  is retried quietly at the next run, and pushes only if the next run fails too.
- **A→B→A** re-checks A, because the marker holds only the newest title. That errs private.
- **Who judges:** every `Writer` that reconciles, in any container. `Writer.reconcile_plaud` passes
  reconcile the recording's markers from `state.db` and records the new ones, so a `reindex` in `web`
  follows the same rule as the worker. Nothing falls back to 2a's `title_seen` rule, which would
  re-add a tag Dan removed. This retires `title_seen` for privacy.
- **After `state.db` is lost,** the worker seeds each recording's marker for its primary Plaud ID
  from `plaud.title_seen`, when that equals the ID's newest title. Every other recording gets case 4,
  with its note. The gate closes and pushes (§11.6).
- **This closes the 2a residual** (2a rulings, Task 7, carried to 2b and 3a), in which a removed tag
  came back on every reconcile when `title_seen` couldn't advance. A re-run of `import-audio-router`
  still puts privacy tags back by design (main spec §9.3).

### 13.3 The deliberate exception, and CLAUDE.md

- **The rule** (CLAUDE.md; main spec §7.4; §22 decision 35): the private tag lands before any source
  or output does.
- **The exception** (decision 1): a Plaud recording whose title becomes private, or gains the keep-out
  word, after it came in. Its audio, source and outputs landed first. `private` follows at the next
  sync after the rename, from the listing alone. Until then it carries whatever tags Dan gave it, and
  with none, external agents already treat it as private (main spec §7.4). The backups and the mirror
  hold it either way.
- **What still holds:** whatever titles the app has seen when it imports decide the tag. It never
  imports first and checks after.
- **CLAUDE.md's Privacy bullet** changes from "The private tag lands before any source or output
  does." to:

  > The private tag lands before any source or output does, judged on every title the app has seen
  > at that moment. One deliberate exception (stage 2b, Dan, 2026-10-23): a Plaud recording renamed
  > to a private title, or to the keep-out word, after it came in is tagged `private` at the next
  > sync, so its source and outputs landed first. Until then it carries whatever tags Dan gave it;
  > with none, external agents already treat it as private.

## 14. Edit-sync

### 14.1 A check

- **A check of one recording here** (never one with any of its Plaud IDs kept out or consent-listed,
  never one gone, and a parked one only through Re-sync, §14.6):
  1. Fetch its envelope with one of its Plaud IDs that the newest listing holds, in the form listed.
  2. Normalise it, and compare it with the newest complete snapshot, recomputing both sides (main
     spec §9.1.1).
  3. If it changed, write a new snapshot (`Writer.add_snapshot`).
  4. **Always end with `reconcile_plaud`,** which is idempotent and writes nothing when the outputs
     are up to date. So a crash between a snapshot and its reconcile, or a reconcile that raised, is
     repaired by the next check, even when the envelope hasn't changed since.

  It downloads no audio: audio is fetched once, at import.
- **Its result:** "up to date"; "updated", with the parts that changed (title, transcript, speakers,
  notes, Dan's own notes, other); "degraded", when link errors persist after one re-fetch (it counts
  as checked; the snapshot is kept and never used as a baseline); "suppressed" (§14.4); or "failed",
  with its class. Each check is a row in `plaud_check_log`.
- **An unchanged check never writes `recording.json`** (main spec §9.1.1).
- **Locks:** the fetch happens outside every lock. Publishing and reconciling happen under the
  recording's.
- **An edit during a check** can produce a snapshot mixing old and new parts. It heals at the next
  check; nothing tries to prevent it. A title in the envelope that differs from the listing's goes
  through §13.1 like any other.

### 14.2 The rolling sweep

- **Each recording here** has a `next_check_at`, except those gone or parked, and those with any of
  their Plaud IDs kept out or consent-listed.
- **The interval:**
  - **Unsettled** (no complete transcript with at least one Plaud note, and recorded less than 48
    hours ago, by `recorded_at`): `schedule_minutes` for its first 2 hours after import, then 1 hour.
    A recording imported long after it was made is settled at once.
  - **Settled:** an unchanged check doubles the interval, capped at `full_sweep_days`.
  - **After a change:** `schedule_minutes` for two more checks, then the smaller of `full_sweep_days`
    and 6 hours, then doubling again.
  - **Failures** back off separately, doubling, with the same cap.
- **Each run's cold sweep** takes the most overdue recordings first, up to a cap of
  ceil(1.5 × N / (`full_sweep_days` × runs per day)), at least 1. Runs per day are 1,440 /
  `schedule_minutes`, and N is the number of recordings the sweep covers. Checks triggered by a
  listing change, an unsettled recording or a Re-sync are extra, within the run's call budget.
  - **Worked example:** N = 1,000, `full_sweep_days` = 1, `schedule_minutes` = 20: 72 runs a day, a
    cap of 21 checks per run, about 5 to 8 calls each, so 105 to 170 calls of a 600-call budget.
- **Listing changes:** when a run records a `plaud_listing` row whose title hash, duration or change
  marker differs from the stored one, it sets that recording's `next_check_at` to now, in the same
  transaction. A Compare does the same. The first sight of an ID is not a change, so 2b's first listing
  doesn't queue the whole archive. A changed duration means a trim in Plaud.
- **`full_sweep_days`** (controller's call): 1 while Dan relabels (the template's value), 7 when unset.
  The promise, which Part I gives in Dan's words, is: a rename shows within `full_sweep_days` plus one
  schedule interval, while the run's budget allows. Status shows the real oldest check, and the
  estimate it implies.
- **The sweep pauses** under any hold (§11.4).

### 14.3 Cheap change detection (pending spike S5)

This task is blocked on spikes S5 and S9; until they report, §14.2 is the default and is built
first. Then, in steps:
1. **Read the spikes' results:** does any listing field move when Dan edits (an edit time, a version,
   a flag), and on which edits (S9's table)? What page size does the listing clamp to (S5)?
2. **If such a field exists:** add it to `plaud_listing`, and to §14.2's listing-change rule, so a
   change triggers a check now. Renames then show within one sync, and the template's
   `full_sweep_days` can go back to 7 even while Dan relabels. The sweep stays as the safety net. A
   test with the fake server moves the field on a rename.
3. **If there is none,** nothing changes, and the task closes.
4. **If S5 shows the page size is clamped small,** the listing alone may cost 30 to 50 calls a run:
   recompute the budget and the recommended `schedule_minutes` from it, and update §10.2's numbers.

### 14.4 The circuit breaker

- **Volatility is found by fetching twice.** When a check of a settled recording finds a change, the
  run fetches the envelope again at once. For each changed part it compares the two fetches'
  normalised hashes:
  - **equal:** the change is stable, a real edit. It is written as usual. Dan renaming speakers one at
    a time, check after check, is always this.
  - **different:** that part is **volatile**: its content changes between two fetches a moment apart,
    which no human edit does. The check is logged with the part found volatile.
- **What counts,** over the window:
  - a check whose only changed part is "other" (everything outside the named parts, or a field the
    normaliser doesn't know);
  - a check that found a part volatile.
- **Never counted:** an import, a recording's first check after its baseline, a suppressed check,
  and a stable change to a named part, however often the same recording changes.
- **The window** is the last 24 hours, from `max(now − 24 h, window_start)`.
- **It trips** when, within the window, at least 10 recordings counted, and they are more than 5% of
  the recordings checked.
- **While it is open,** a check whose change lies only in what counted (only "other", or only a part
  that the double fetch found volatile) is **suppressed:** no snapshot is written, it is treated as
  unchanged for scheduling (the interval doesn't reset), and it isn't counted again. Everything else
  goes on: listings, imports, title checks, `private` tags, and snapshots with any other change. Noise
  never stops new recordings from arriving, and nothing is fetched again and again.
- **It is stored** in `plaud_state`, so a restart doesn't clear it.
- **What Dan sees:**
  - a push at once (decision 5), whose body says "Status has the steps";
  - Status: "Edit-sync paused since 14:20: 12 recordings changed only in fields Plaud doesn't usually
    change (`folder_id`, `share_state`). New recordings still arrive." Or, for a volatile part:
    "…: 12 recordings' transcripts change between two fetches a moment apart." Field and part names
    only, never values.
  - Status prints the exact commands, in the form the runbook uses.
- **The reset is from the CLI only** (controller's call), because a reset without a fix trips it
  again. Dan and a Claude session look at the field names, then either:
  1. ship a release that drops the field (a normaliser version bump, which writes zero snapshots, main
     spec §9.1.1), then `recordings plaud breaker reset`; or
  2. accept the field as content: `recordings plaud baseline --all`, then `breaker reset`.

  A reset sets `window_start` to now, so rows from before it never count again. It sends the "works
  again" push, and is recorded in `ops_runs`. A second trip alerts again.
- **The volatility check,** `recordings plaud check --volatility`: it fetches 3 recordings here (not
  private, not kept out, not consent-listed) twice, a minute apart, and prints for each part whether
  the normalised hash matched. It writes nothing. The runbook runs it before the baseline, so a
  volatile field is found before the breaker ever meets it.

### 14.5 The baseline, and rebaselining

Main spec §9.1.1 and §19 name the baseline without defining it.

- **A baseline seeds a recording's check state, and excuses its next check from the breaker:**
  - Last checked is set to its newest snapshot's fetch time (main spec §6.8's rebuild rule).
  - Its next check is spread evenly over `full_sweep_days`.
  - Its titles get their first private and keep-out check, where the marker has never seen them
    (§13.1, case 4).
- **Its first check** compares today's envelope with an older snapshot (`audio-router`'s may be weeks
  old). A change found then is written once, and doesn't count.
- **When:**
  - `recordings plaud baseline` before the catch-up (§11.6), so the existing archive is the base.
    Each new import is its own baseline.
  - **Automatically,** for any recording here with no `plaud_checks` row, after `state.db` is
    restored or rebuilt (the 2a carry, "seed last_checked on a freshly adopted state.db", 2a rulings
    item 39). A lost `state.db` can't trip the breaker.
  - `baseline --all` (a rebaseline): after a breaker trip accepted as content, or a normaliser change
    Dan accepts.
- **It prints counts:** seeded, already seeded, titles checked, `private` added, kept out late.
- **If 2b ships in halves,** the baseline's privacy part (the markers and case 4) is in the first half,
  because step 7 needs it. Seeding check state can wait for the sweep.

### 14.6 Re-sync, parking, and Sync everything

- **One recording:** Details' **Re-sync from Plaud** sends a `resync`. It is checked now, ahead of
  the sweep, between the recordings of a long run. The reply shows in place:
  - "Up to date", with "· Plaud can take about N seconds to show an edit" if S9 measures a delay;
  - "Updated: speakers";
  - "Not checked: kept out", "Not checked: on the consent list", or "Gone from Plaud";
  - "Edit-sync paused: see Status", when the only change was suppressed;
  - "Failed: Plaud unavailable".
- **A selection** (decision 6): the selection bar reads "12 selected · Tag… · Re-sync from Plaud ·
  Clear". One request; progress "Re-syncing 12 of 25…"; the end "25 checked: 7 updated (speakers 6,
  title 1) · 18 up to date". Recordings without a Plaud source are skipped and counted. The cap is
  1,000 (controller's call): "For more, use Sync everything". The open list re-reads the changed rows
  in place, as a tag reply does.
- **Parking** a recording that always fails:
  - **Park** appears on a recording on the failing list (3 failures in a row), in Status and the Sync
    screen, and as `recordings plaud park`.
  - A parked recording isn't checked by the sweep, and doesn't count against "All here", Sync
    everything or §11.7.
  - It is listed "Parked: 2", with **Unpark**. A Re-sync still tries it, and a success unparks it.
  - The manifest marks it `parked`.
- **Sync everything** (`--full`, main spec §9.1.1):
  - A button under the Sync screen's More menu, and `recordings plaud sync --full`.
  - It lists, imports what is waiting, checks every recording the sweep covers (oldest first), then
    retries its failures once.
  - **Exit 0** when the listing was good and every listed recording is excluded, parked, **flagged**
    (here, but consent-listed or kept out late, so never fetched), or here and checked (degraded
    counts as checked). **Exit 2** otherwise, with counts. A flagged recording is never a failing one:
    `--full` counts the flagged and names them by archive ID, so DAN-15's final run can finish while a
    removal by hand is still to do.
  - **It pushes** only for a run-level failure, or for a recording that wasn't already on the failing
    list when it started. Known failures are counted, not pushed.
  - **It resumes** after a restart: `plaud_state` keeps its start, and it continues with what isn't
    checked since. It runs by itself every 30 days while the schedule is on. A failed one runs again
    next month, or when Dan presses it, never sooner: it doesn't inherit the ops loop's 15-minute
    retry (`ops.py:390`).
  - **Its estimate** ("about 40 minutes") comes from the last run's measured pace, and says "unknown
    until the first run" before then.
  - **It writes the DAN-15 manifest** to `/state/manifests/plaud-<date>.json`, listed in `[backup]
    extra_paths`. It holds each Plaud ID with its archive ID, last snapshot, state (checked, degraded,
    parked, flagged, excluded, gone) and part hashes, leaving out the title and speakers parts, so
    Forget has nothing there to scrub. Consent-listed and kept-out IDs not here appear by ID only.
- **A long run** serves the light turn between recordings, the CLI's included (§9.2).

### 14.7 Removals wait, counted (controller's call)

- Reconcile holds removals back: a name going back to "Speaker N", or notes vanishing (main spec
  §9.1.1). 2b doesn't apply them. Stage 3a's review queue accepts them.
- Status: "3 changes from Plaud are held for your review (the review list comes with stage 3a)". The
  Sync screen's Updated list shows "removal held". `recordings plaud held` lists them by recording ID
  and part. There is no Accept button.

## 15. The interface

**The Sync screen acts, and Status reports** (P3). Both show the same summary line, from one
component, and link to each other.

**Build order,** each its own task:
1. **The view model** (§22): what each view reads from which table, and the reply shapes. Every UI
   task depends on it.
2. **The request input and replies** (§9.3): `plaud_request`, the insert, polling a pending row,
   `send_message`, and the reply states (Vitest).
3. **The Sync screen** (§15.1), section by section: the summary and flags; the buttons and holds;
   the equations; Not here yet; Kept out with Release and its question; Updated, Gone and Parked.
4. **Status's sync panel** (§15.2): the sentences, the numbers, Details, Needs attention.
5. **The Library and Details** (§15.3): the arrivals cue, the selection bar's Re-sync, the Details
   lines.
6. **Demo and Pages** (§15.5): the canned fixture and the disabled buttons.
7. **Phones and accessibility** (§15.4): 320 pixels, targets, axe.

### 15.1 The Sync screen

- **A new page,** `#/add`: "Library · Tags · Add · Status". At 320 pixels the four tabs stay one row
  of short text, with the logo's mark but not its name, and the theme switch as an icon; the e2e test
  at 320 pixels checks it. The Add page's Plaud panel is the main spec's Sync screen.
- **Top to bottom:**
  1. **The summary line,** shared with Status: "All here · checked 14:20 · next sync 14:40", or the
     problem in one sentence and "See Status".
  2. **Flags,** right under it, each by recording ID with its own next step: a late keep-out or a
     late consent listing, "take it out by hand (runbook)"; a `private` the sync couldn't write,
     "run `recordings reindex`".
  3. **A hold,** if one is on: its sentence (§11.4) and **Accept this listing**, or the reason Accept
     is refused.
  4. **Sync now** and **Compare**, with **Sync everything** (and its estimate) under **More**.
  5. **The equations** (§11.3), each number a link to its list, "from the listing at 14:20", and the
     consent-list line.
  6. **Not here yet** (waiting and missing): a checkbox, the date, time and duration, the title (a
     lock and "arrives private" when it will), and the state: "imports at the next sync", "schedule
     off", "auto-import off", "held: see Status", "the disk is nearly full", "failed 3 times · Park",
     "waits for the first catch-up". **Copy ID** copies the whole ID in the list's own form
     (`` - `of_…` — ``). **Import selected**, and **Import all 31** with its count. During a consent
     stop, no titles: "titles hidden until the consent list reads".
  7. **Kept out:** by the consent list, by title, and "was kept out by title" with **Release**. Each
     with the ID (and Copy ID), the date, time and duration. Never a title, except inside Release's
     own question (§12.2). A released row reads "Released · comes in at the next sync (14:40) · Undo".
  8. **Updated on Plaud (7 days):** the recording, what changed (title, transcript, speakers, notes,
     removal held, re-tagged private), and when.
  9. **Gone from Plaud,** newest first, with "gone since" and "N in the last 7 days", and the link to
     "Taking a recording out by hand"; **Parked**, with Unpark.
  10. **Import your own audio:** coming later (main spec §9.2).
- **While the gate is closed,** only **Compare** is enabled (and Accept, after a rebuild). The
  others say the gate's reason (§11.6).
- **Under any hold,** the import buttons are disabled, with the hold's line.
- **Every press gets an answer within a second** (P6). While a request is pending, the screen updates
  every 2 seconds. It keeps the newest result line ("Import all, 14:20: 31 imported · 4 arrived
  private"), so a phone that slept through the toast still sees it.
- **Sync now's replies:** "Synced: 2 imported (1 private) · All here", "Synced: nothing new · All
  here", "Synced: 1 failed · See Status", "Held: see Status."
- **Titles** come from the cache for recordings not yet here, and from `recording.json` for those
  here. They never appear in a log, an alert or a report.

### 15.2 Status: the sync panel

- **First, plain sentences** (P2, P12), each true now or not shown:
  - "All here · checked 14:20 · next sync 14:40", explained under it: "Every recording on Plaud is in
    the archive or deliberately left out." It shows when the last good listing is under two schedule
    intervals old and nothing is waiting or missing (parked recordings aside). Recordings that keep
    failing get their own line beside it, never hiding it: "2 recordings fail every check (since
    2026-10-23) · Re-sync · Park".
  - An error shows from the first failed run, though its push waits 2 hours (decision 5).
  - Or the condition, by name:
    - "Plaud sign-in needed since 23:00. New recordings wait on Plaud: nothing is lost. At the Mac:
      `make plaud-sign-in`."
    - "Plaud sync failing since 12:00 (Plaud unavailable). Last good listing 11:40."
    - the breaker (§14.4), with its commands
    - "Plaud imports stopped: the consent list has 2 lines that look like IDs but can't be read
      (lines 14 and 22)."
    - "Recording 20261023T140512-0700_3fa91c2e is on the consent list, but in the archive. Take it
      out by hand (runbook)."
    - a hold: §11.4's sentence for it, with **Accept this listing**, or the reason Accept is refused
    - "Scheduled sync waits for the first catch-up (runbook)."
    - "Plaud sync waits for you: its records were rebuilt. Run a dry run, decide about anything it
      lists as missing, then Accept this listing." (§11.6)
    - "Scheduled sync is off since 2026-10-23 14:20." Plain before the catch-up. After it, once off
      for 24 hours, it joins Needs attention.
    - "Auto-import is off: new recordings wait for you on the Sync screen."
    - a small shrink: "Plaud now lists 3 fewer: waiting for the next listing to agree."
- **Then the numbers:**
  - the equations and the consent-list line (§11.3, §12.1)
  - the last good listing (time and count), and the next sync
  - the sweep: "every recording checked within the last 21 hours (oldest 2026-10-22 17:10)", with
    the estimate for a full pass
  - the sign-in, and the refresh token's own expiry if it has one
  - the last Sync everything: "2026-10-01: all 1,234 checked (2 parked, 1 degraded)", or its failure
  - held for review; gone from Plaud; failing; parked
  - "The Plaud worker last reported 14:21 (checking, 412 of 1,234)"
- **Details, folded away:** the normaliser's version; the field names added or gone since the last
  good run; the census of ID forms; changes per part in 24 hours; the share of named segments; the
  last 20 runs with their results; the keep-out words enforced; malformed listing rows.
- **Needs attention gains:**
  - the sync failing for more than 2 hours
  - a sign-in needed, or one expiring within 7 days
  - the breaker, or any hold
  - a consent stop, a late consent listing, a late keep-out
  - a recording that should be private but couldn't be tagged
  - the sync's records rebuilt (§11.6)
  - the worker silent, or without progress (§9.2)
  - a `plaud` holder older than 2 hours (12 for Sync everything and the catch-up): "may be stuck"
  - a small shrink unsettled after 24 hours
  - the schedule off for 24 hours after the catch-up
  - an undelivered push (§16): "a push couldn't be sent: check ntfy"
- **"No sync has run yet"** when a table isn't there (§9.5).
- **It redraws** every 60 seconds (main spec §12.5), and when a request finishes.
- **No titles anywhere on Status.** Recordings are named by ID (P13).

### 15.3 The Library and Details

- **Arrivals** (P10): the Refresh button in the list's header reads "2 new · 1 private · 2 updated
  from Plaud · Refresh", beside tagging's "5 changed" (`HeadNotes.tsx`). At 320 pixels it shortens to
  "5 from Plaud · Refresh". The counts come from `plaud_events` (imported, arrived private, updated)
  since the page loaded, read every 60 seconds. Rows don't move until Refresh. The Tags grid's header
  has the same cue.
- **The selection bar** (`SelectionBar.tsx`) gains **Re-sync from Plaud** (§14.6).
- **Details** gains:
  - "Plaud: checked 2026-10-23 14:20 · next by 2026-10-24 14:20", with **Re-sync from Plaud** and its
    result inline
  - the recording's Plaud ID(s), each with Copy ID
  - "Gone from Plaud since 2026-10-23 (kept)"
  - "Tagged private by the sync on 2026-10-23: its Plaud title changed", "…: its title matched when it
    arrived", or "…: the sync's record was rebuilt"
  - decision 3 made visible, once Dan has removed an automatic `private`: "Tagged private by the sync
    on 2026-10-23. You removed it on 2026-10-24. It comes back only if its Plaud title changes to a
    private one." The removal date comes from the revision that removed it.
  - the flags, each with its own next step (§15.1)
- **Wiring** (CLAUDE.md): every new output (the Sync screen's view, the arrivals count, request
  replies) is subscribed in `App` with its `useShinyOutputStatus` line, and `plaud_request` is
  mounted once in `App`.

### 15.4 On a phone

Add and Status work at 320 pixels: the summary first, full-width buttons, targets of at least 44
pixels, rows stacked (date and duration on one line, title below). A push carries no click link.

### 15.5 Demo and Pages

- **No worker, no Plaud code, no network** (main spec §17). The Sync screen draws a canned view from a
  hand-written fixture in `demo/` (never generated from a real listing), with the buttons disabled and
  "Demo: nothing is synced". Status's sync panel says "Demo: Plaud sync isn't part of the demo."
- The demo's server refuses a `plaud_request` even if one is crafted, and its temporary `state.db`
  never gets a request row (`test_demo_guard`). Pyodide never imports the client, the token store or
  the sync.

### 15.6 Fixed phrases

Logs, Status, alerts, `ops_runs` and request results use fixed phrases, with counts, IDs and
exception class names. Never a title, a token, a URL from Plaud, or exception text.
- **A run:** "ok", "skipped: busy (`plaud-full`)", "offline", "Plaud unavailable", "rate limited until
  14:40", "Plaud refused access (403)", "sign-in needed", "every fetch failed", "error:
  `<ExceptionClass>`", "paused: unexpected changes", "held: Plaud's IDs changed", "held: everything
  looks new", "held: many gone from Plaud", "waiting: Plaud lists 3 fewer", "imports stopped: consent
  list", "gate closed", "partial: 3 recordings failed".
- **A recording:** "up to date", "updated: speakers, title", "degraded", "suppressed", "busy",
  "failed: Plaud unavailable", "not checked: kept out", "not checked: on the consent list", "gone from
  Plaud", "parked", "re-tagged private: title changed", "re-tagged private: the sync's record was
  rebuilt".
- **A reply:** "Queued", "Queued after the current sync (started 14:20)", "Already queued", "Resuming
  (started 14:20)", "waiting for Sync everything (412 of 1,234)", "Sync everything ran less than an
  hour ago", "The first import is a supervised run on the server (runbook)", "Held: see Status",
  "Accept refused: fix the consent list first", "Release refused: its title still starts with
  `noarchive`", "Release refused: it is on the consent list", "Released · comes in at the next sync
  (14:40) · Undo", "The Plaud worker isn't running (last reported 14:21): your request runs when it's
  back, if that's within the hour."

## 16. Alerts

**Each condition has its own alert key,** so a run that goes well can't announce "works again" while
the breaker is still open (`ops._alert`'s ok path today clears every key of a job, `ops.py:477-486`).

| Key | Pushes | Title ("recordings: …") | Cleared by | Next step (runbook) |
|---|---|---|---|---|
| `plaud:sign-in` | at once | "Plaud sign-in needed" | a new token and a good run | `make plaud-sign-in` |
| `plaud:consent` | at once | "Plaud imports stopped (consent list)" | a run that reads the list cleanly | fix the lines Status names |
| `plaud:listing` | at once | "Plaud sync paused (IDs changed)", "(everything looks new)" or "(many gone from Plaud)" | **Accept this listing** (Status, the Sync screen, or `accept-listing`), or a later listing that no longer meets the hold's condition | Status's check, then Accept (§11.4) |
| `plaud:rebuilt` | once, at once | "Plaud sync waits for you: its records were rebuilt" | Accept this listing, after the dry run | §11.6's supervised path |
| `plaud:breaker` | at once | "Plaud edits paused (unexpected changes)" | `breaker reset` | Status's two commands |
| `plaud:privacy` | once per recording and cause, at once, no reminder (P15) | "a kept-out recording was already archived"; "a consent-listed recording is in the archive"; "a recording couldn't be tagged private" | (one-shot; the flag stays on Status) | "Taking a recording out by hand"; for the third, `recordings reindex` |
| `plaud:sync` | after 2 hours without a good sync, unless a key above explains it | "Plaud sync failing" | §11.7's recovery | by Status's reason: offline, check the network; Plaud unavailable or rate limited, nothing (it heals); Plaud refused access, check the subscription; every fetch failed, or an error class, `recordings plaud sync --dry-run --debug`, then a Claude session |
| `plaud:full` | when the monthly run fails at run level, or finds a new failing recording | "Plaud full check failed" | the next good Sync everything | Status's count; Re-sync, then Park |

- **The machinery** (`ops._alert`, `_conclude`) is generalised, and the changes are named:
  - Sticky keys are cleared only by their own resolution. A run's ok path clears only `plaud:sync`.
  - The 2-hour rule needs an onset: `plaud_state.failing_since` (§11.7). The `alerts` row is marked
    failing only when the push is sent, or `_known` would suppress it.
  - `TITLES`, `DETAIL_TITLES` and `RUNNING_LABELS` gain the jobs and titles above, and labels for
    `plaud-sync`, `plaud-full`, `plaud-catch-up` and `plaud-baseline`.
  - The reminder every 24 hours applies to every key but `plaud:privacy` and `plaud:rebuilt`.
  - **`plaud:privacy` is deduplicated by recording and cause.** A recording with an open privacy
    flag, or gone from Plaud, gets no new push when a second cause appears (for example, Dan putting
    an already-flagged recording on the consent list as step 1 of the removal): its flag changes
    instead.
- **Bodies are fixed templates** (main spec §14): the job, the error class, the exit code, counts,
  then "See Status". Never a title, an ID, a path or error text.
- **The sync's Healthchecks check** (decision 4, refined; controller's call, needs Dan's OK):
  - `AlertConfig` gains `deadman_sync_url`, from the secret `RECORDINGS_DEADMAN_SYNC_URL`, mounted in
    `worker` only, checked by `_check_url` with its own message. A bad URL makes `from_config` raise,
    which turns every one of the worker's alerts off, so doctor reports it as a problem for `worker`.
    `ping_deadman` takes the URL to ping.
  - **The worker pings it, with an empty body, at the end of every scheduled turn it handles**
    (§9.7): a turn that runs, good or bad, and a turn that stops at the gate, a sign-in pause, a
    consent stop, a hold, a rate-limit wait, or a busy `plaud` lock. The light turns inside long runs
    ping too. Never `/start` or `/fail`. A ping means "the worker is alive and deciding".
  - **The one exception: a push owed and not delivered.** While a push that is due (any immediate
    one, or the 2-hour one) couldn't be delivered to ntfy (`alerts.send` reports it undelivered, as
    `ops._alert` already tracks), the worker withholds its ping, and Needs attention says "a push
    couldn't be sent: check ntfy". Healthchecks then speaks after its grace: the backstop for broken
    push delivery. Pings resume once the push goes through.
  - So the check speaks only when the worker is dead or stuck, or can't reach Dan. Lasting failures
    push through the app's own keys, with the reason. Nothing pushes twice for one cause.
  - **Period:** `schedule_minutes`. **Grace:** 1 hour, because the check is pinged at every turn.
    That is shorter than main spec §14's "at least 3 hours", which stays the rule for the backup's
    check (its long jobs share one loop) and is amended to say so (§19). The runbook changes the
    period whenever `schedule_minutes` changes. Dan routes its notifications through Healthchecks'
    own channel (e-mail or its app), not ntfy.
  - It is paused while the schedule is off (runbook). Before the first catch-up, with the schedule
    off, no turn comes due, so nothing pings.
  - Decision 4's text, "pinged after every good run", stays as Dan gave it. If Dan prefers it
    literal, the only change is pinging after good runs only; then a Plaud outage or a refused sign-in
    also brings a second, reasonless alert from Healthchecks after its grace.
- **`alert-test`** in each container pings the check that container holds (the backup's
  `deadman_url`, the worker's `deadman_sync_url`) with `/log`, and sends one push. The runbook runs it
  in both.
- **Delivery rules are unchanged:** https only, no redirects, a 10-second timeout.

## 17. Operations

### 17.1 The `worker` service

- **The same image and tag** as `web` and `backup`, with `pull_policy: never`; `hostname:
  recordings-worker`; `command: ["recordings", "worker"]`; `RECORDINGS_ROLE: worker`.
- **Hardened like the others:** a read-only root, `cap_drop: ALL`, `no-new-privileges`, `/tmp` as a
  1 GiB tmpfs (unused for media), log rotation, the deployment's user and group.
- **Process:** `init: true` (ffprobe runs as a child), `restart: unless-stopped`, `stop_grace_period:
  30s`, `cpu_shares: 512` (between `web`'s 1024 and `backup`'s 256), `oom_score_adj: 300`.
- **Mounts,** long syntax with `create_host_path: false`:
  - the config, read-only
  - the archive, read-write
  - `/state`, read-write
  - the `index` named volume, read-write
  - **the consent folder,** read-only, at `/consent` (§12.1)
  - `/plaud`, read-write
- **`backup` gains** the consent folder, read-only, for `extra_paths`.
- **Secrets:** the ntfy topic, the ntfy token if set, and `deadman_sync_url`. No restic, NAS,
  `known_hosts` or `deadman_url` secret.
- **Its own health check:** `recordings worker --alive` (§9.2), interval 30 seconds, start period 60.
  Never the image's `:8000/healthz` probe (`Dockerfile:80`), which would mark it unhealthy forever and
  fail `make deploy`'s `--wait`.
- **No ports.**
- **Not enforced:** traffic from `web` and `backup` to Plaud isn't blocked at the network layer
  (Docker can't, without host firewall rules or a proxy). They hold no token, which is the control.

### 17.2 Doctor

`doctor.py` today has three roles (`ROLES`, line 48), runs the backup, mirror, alert and ssh checks for
every role but `web` (lines 605-609), and forbids job secrets only in `web`. So a `worker` role needs
these changes:
- `ROLES` gains `worker`.
- **For `worker`:** skip `_backup`, `_mirror`, `_ssh` and the restic part of `_secrets`; keep
  `_alerts`, checking the ntfy secrets and `deadman_sync_url`. Its "no dead-man's switch" warning
  (`doctor.py:530-534`) names `RECORDINGS_DEADMAN_SYNC_URL_FILE` for the worker, and
  `RECORDINGS_DEADMAN_URL_FILE` for the others, and reports `deadman_set` from the right one.
- **`JOB_SECRETS` splits** into a backup set (restic, rest-server, the NAS key, `known_hosts`,
  `deadman_url`) and an alert set (the ntfy topic and token).
  - `web` forbids both sets, plus `deadman_sync_url`.
  - `worker` forbids the backup set.
  - `backup` forbids `deadman_sync_url`.
- **`web` and `backup`:** a mounted `/plaud`, or `RECORDINGS_PLAUD_TOKEN` set in the environment, is a
  problem. Read by name, because `plaud_token` leaves `SECRETS`.
- **`worker`'s problems:**
  - the consent list missing, unreadable, empty or with typo lines (by line number); lines read as
    kept are reported as a note, by number;
  - no token store ("sign-in needed: run `make plaud-sign-in`");
  - `/plaud` wider than 0700, or the token wider than 0600, or not the app's own;
  - an invalid `keep_out_word`;
  - a bad `deadman_sync_url`, which turns all the worker's alerts off.

  It also reports the token's expiry, whether a refresh token is present, the keep-out words enforced,
  and the consent list's ID count. Never a value, never a hash.
- **Every role:** an `extra_paths` entry that holds the token's folder, the whole of `/state`, or
  `plaud-cache.db` is a problem: the last two would send titles of recordings not yet here to the
  backup. For `backup`, an `extra_paths` without the consent folder or `/state/manifests` is a
  warning.
- **Doctor never calls Plaud.** `recordings plaud check` does.
- `test_doctor.py` gains a `worker` case for each.

### 17.3 Deploy, rollback and boot

- **`make deploy`** waits for the worker's health check, as for `web`'s.
- **The deploy guard:** `docker/ops-busy.py` becomes `ops-busy.py <locks dir> <lock name> [job …]`.
  It exits 3 only when the lock is held and, if jobs are given, the announced job is one of them. A
  holder that is held but can't be read still refuses. `deploy-guard.sh` asks `ops` (any job) and
  `plaud` (`plaud-full`, `plaud-catch-up`, `plaud-baseline`). A routine sync is safe to interrupt. The
  guard asks through the `backup` container, and a `plaud` hold is visible there through the shared
  `state/locks`. `FORCE=1` still overrides.
- **`make deploy-smoke`** (CI) runs the worker with no token and no consent list, and checks that it
  stays healthy and that doctor reports both.
- **Rollback stays a tag:**
  - `state.db` gains tables only (§9.5), and `recording.json` gains no field (§13.2);
  - `index.db` in WAL mode, and the cache file, open fine in an older release, or are ignored;
  - `load_config` has no unknown-key check, so an older release ignores the new `[plaud]` keys;
  - the token's folder is untouched.
- **Rolling back across 2b:** pause the sync's check; `rc stop worker && rc rm -f worker`, because
  the checkout's compose file would start a worker whose command the old image lacks; `git checkout`
  the tag's commit; `make rollback TAG=<tag>`.
- **The boot unit** needs no logic change: `up -d` starts every service. Its description becomes "the
  web, backup and worker containers".

### 17.4 Config, secrets and the settings test

- **`config.example.toml`'s `[plaud]` block:**
  ```toml
  [plaud]                            # stage 2b (spec §9.1)
  schedule_minutes = 0               # minutes between syncs; 0 is off. 20 after the first catch-up
  auto_import = true                 # import new recordings at each sync (nothing runs until tagged)
  full_sweep_days = 1                # check every recording against Plaud within this many days:
                                     # 1 while relabelling speakers in Plaud, then 7 (the default)
  keep_out_word = "noarchive"        # a Plaud title starting with this word is never downloaded
  auto_private_patterns = ["^\\s*private", "interview:"]
  consent_list = "/consent/consent-list.md"   # inside the consent folder; never a file mount
  token_store = "/plaud/tokens.json" # the Plaud sign-in (worker only; `make plaud-sign-in`)
  # api_base = "https://…"           # Plaud's API: the copied client's by default; tests set a fake
  ```
- **Unset means:** `schedule_minutes` 0, `auto_import` true, `full_sweep_days` 7, `keep_out_word`
  `noarchive`, `consent_list` none (a consent stop), `token_store` `/plaud/tokens.json`, `api_base`
  the copied client's host.
- `recheck_days` goes: the rolling sweep replaced its hot window (main spec §9.1.1).
- **Secrets:** `RECORDINGS_PLAUD_TOKEN` leaves `config.py`'s `SECRETS`, the template and the README.
  `RECORDINGS_DEADMAN_SYNC_URL` (stage 2b) joins them, with the backup URL's rules.
- **`docker/deploy.example.conf`** gains `RECORDINGS_PLAUD_HOST` and `RECORDINGS_CONSENT_HOST` (both
  folders), and the test that every compose variable is documented covers them.
- **`[backup] extra_paths`'s template comment** names the consent folder and `/state/manifests`.
- **The settings test** (CLAUDE.md, "Settings"): every new key gets its row in
  `test_template_settings.py`, with its reader. `api_base` is commented out in the template, so its
  row sets it explicitly. `schedule_minutes` and `auto_import` leave `LATER`; `recheck_days` leaves
  with its key; `plaud_token` leaves `LATER_SECRETS`; `deadman_sync_url` gets a `SECRET_VALUES` row.
- **`.gitignore` and `.dockerignore`** gain `tokens*.json`, `**/.plaud/` and `local.mk`.

### 17.5 The runbook

The main spec's first-run steps 5, 6 and 7 (§19) become new runbook sections, `## 6. Deploy 2b`, `##
7. The dry run` and `## 8. The catch-up`, after the existing `## 5. The mirror on the Mac`.
`test_runbook.py` follows if it checks headings. The runbook's `rc` lines run in a shell with
`RECORDINGS_IMAGE_TAG` exported, as its top block does; elsewhere, `sh docker/up-deployed.sh`.

- **1c, before stage 2b:** points to the spike sessions (§18). Their results table holds the session's
  date and each outcome, never a recording's date, time or duration.
- **6. Deploy 2b:**
  1. `install -d -m 700 $RECORDINGS_HOME/consent`, and move `consent-list.md` into it.
  2. `install -d -m 700 $RECORDINGS_HOME/plaud`.
  3. Set `RECORDINGS_PLAUD_HOST` and `RECORDINGS_CONSENT_HOST` in `deploy.env`, and add the consent
     folder and `/state/manifests` to `[backup] extra_paths`.
  4. Create the sync's Healthchecks check: period 20 minutes (the `schedule_minutes` to come), grace
     1 hour, notifying through Healthchecks' own channel. Put its URL in the secrets folder as
     `deadman_sync_url`, and pause it.
  5. Keep `schedule_minutes = 0` (the worker refuses scheduled syncs before the catch-up anyway), then
     `make deploy`.
  6. On the Mac: write `local.mk` (`SERVER`, `REPO_DIR`), then `make plaud-sign-in`. On the server:
     `rc exec worker recordings plaud check`.
  7. `rc exec worker recordings doctor --role worker`, `rc exec worker recordings plaud consent`, and
     `recordings alert-test` in `backup` and in `worker`.
- **7. The dry run:** `rc exec worker recordings plaud sync --dry-run --list`, or **Compare**. Check:
  - both equations add up;
  - "here" equals the archive's Plaud recordings, and anything absent is explained (recordings Dan
    deleted in Plaud);
  - waiting and missing are about the ~30 newer recordings, and "of which N arrive private" matches the
    held titles;
  - "Consent list: N IDs, M on Plaud now" is what Dan expects, and no ID-form hold is shown. If the
    hex itself changed (S7), stop: every entry must be found again in its new form first;
  - **the consent list's lines:** `rc exec worker recordings plaud consent` lists, by line number,
    the lines read as kept (Dan confirms each meant "keep"), and, by archive ID, any recording 2a
    imported whose exclusion 2a's parser missed (an `of_` or upper-case ID). The worker has already
    tagged those `private` and flagged them (§12.1). Dan reviews each by its ID in the app, and takes
    out by hand (§12.4) any that mustn't stay, **before section 8's `pre-import` backup**. 2a's
    `post-import` backup already holds them. Nothing in this step prints a title;
  - each `deferred-to-2b` ID from the 2a import's report is waiting; count any that aren't listed.

  **Stop if anything doesn't fit.**
- **8. The catch-up:** §11.6's order. Then, on a line of its own: **"You may start tagging now."**
  (decision 7). Then turn the schedule on, set the sync's Healthchecks period to `schedule_minutes`,
  unpause the check, and watch Status.
- **"Plaud sign-in":** when, `make plaud-sign-in`, what it prints, and `plaud check`.
- **"Keep a recording out for good":** `make consent-add ID=…` from the Mac, or editing the file on the
  server (write a new file and rename it). Then `recordings plaud consent` shows the new count.
- **"Taking a recording out by hand":** §12.4, word for word.
- **"When the Plaud alert comes":** one row per alert title (§16), plus "Healthchecks:
  recordings-sync is down: the worker is dead or stuck, or can't deliver its pushes. `rc ps`, `rc logs
  worker`, and Status's 'a push couldn't be sent'."
- **Recovery:** the breaker (§14.4); a hold and Accept this listing, with what to check first for each
  kind (§11.4); a consent stop; parking.
- **"After a lost or restored `state.db`":** restore `state.db` from the newest backup rather than
  rebuild it, if one exists. Either way the gate closes and pushes (§11.6). Then a dry run; decide
  about every recording it lists as missing (a recording kept out before may be among them: put it on
  the consent list, or let it in); then Accept this listing.
- **Changing `schedule_minutes`:** also change the sync's Healthchecks period to match.
- **"Rolling back across 2b"** (§17.3).
- **Decision 7's lines:**
  - Section 4's "Dan decided on 2026-10-22 that the first real tag waits until stage 2b's import dry
    run is done" becomes "Dan decided on 2026-10-23, refining 2026-10-22, that the first real tag
    waits until stage 2b's first real Plaud import (section 8) and the backup after it."
  - "Rolling back, until the first real tag" says the same. A rollback of the catch-up is a restore
    from `pre-import`, which loses no tags because none exist yet.
- **"Restoring for real", step 6** (the 2a carry): the token is outside `state/`, so moving the whole
  state folder aside stays right. Add that `state.db`'s `-wal` and `-shm` files move with it, and that
  `plaud-cache.db` is derived.
- **Every step that stops the worker or sets the schedule to 0** (re-imports, rolling back) pauses the
  sync's check first and ends by turning the schedule back on and unpausing it. Re-imports stop
  `worker` as they stop `backup`, and section 3's `IMPORT_MOUNTS` (`first-run.md:717`) mounts the
  consent **folder** (`-v "$RECORDINGS_HOME/consent:/consent:ro"`), not the old file path, which no
  longer exists once the list has moved.
- **DAN-15,** before 2027-08-15: a final Sync everything and its manifest, then `schedule_minutes = 0`
  and the check deleted. The builder chooses how "Scheduled sync is off" goes quiet after it (a
  `retired_at` in `plaud_state`, set by that step, is the suggestion), so a lapsed account doesn't
  fail every 20 minutes.

## 18. The spikes

The design depends on facts only Plaud can give. Each spike is a supervised test plan, run with Dan.
Main spec §9.1 and §19 named two; the council added the rest. S13 and S14 became fake-server tests
(§21).

**Ordering, for the plan.** Each spike runs before the tasks that use its result, not just before
deploy:
1. **The spike script first:** `scripts/plaud_spike.py`, with its refusal guard (below) and a unit
   test of that guard against fakes (a consent-listed ID, a private-tagged recording, a keep-out
   title, each refused and counted).
2. **Session A** (S1, S2, S4, S3) unblocks the token store, `token-import` and `make plaud-sign-in`
   (§10.3–§10.5), and settles whether the S3 question is asked.
3. **Session B** (S5, S7, S8, S10, S12, S11) unblocks the fake server's shapes (S5, S8), the client's
   page size and pacing (S5, S10), the ID-form and consent handling (S7, §11.4, §12.1),
   `recorded_at` (S12) and the download rules (S11).
4. **Session C** (S9) unblocks §14.3 and the README's keep-out text.

Tasks that use no spike result (the tables, the worker loop, the request path, the UI against the
view model) go ahead in parallel.

**Rules for every spike:**
- **It never fetches or downloads a consent-listed or private recording.** A spike that calls
  `files/{id}` or downloads audio (S7, S8, S9, S11) uses a throwaway recording made for it, or one Dan
  names in the session. Its script reads the consent list fail-closed and refuses any consent-listed
  ID, any recording tagged private in the archive, and any title the keep-out words or the patterns
  match (S9's own clip aside). It prints how many it refused.
- **Its script** (`scripts/plaud_spike.py`, run with `uv run`, reviewed before the session) holds each
  spike's exact print list: counts, yes or no, expiry times, status codes, field names, and hash
  prefixes (8 hex characters). Never a token, a title, transcript text or a whole Plaud ID.
- **Its sign-in** is made for the session in a temporary `HOME`, kept in `$RECORDINGS_HOME/spike`
  (0700, outside the archive, `state/`, `/plaud` and every backup path), and deleted at the end with
  that folder. The production sign-in comes after.
- **It only reads.** S9 alone changes anything in Plaud, by Dan's hands, on its own throwaway clip.
- **Sessions:** A, the sign-in (S1, S2, S4, S3), about 45 minutes; B, the data (S5, S7, S8, S10, S12,
  S11), about an hour; C, S9, about 45 minutes.

**S1. The token's shape and lifetime.** Dan signs in with the vendor CLI under a temporary `HOME`; the
file goes to the spike folder. Learn the key names, the token type, the access token's life, whether
there is a refresh token, its expiry, and whether it expires when unused (the schedule is off during
the spikes and the deploy).
- Its life sets the refresh threshold, and the format `token-import`'s parser and the fake.
- A refresh token with an expiry: Status warns 7 days ahead. One that expires unused: the runbook
  keeps the gap between sign-in and the catch-up short.
- **No refresh token:** the server would need a sign-in at every expiry. Stop and ask Dan.

**S2. Refresh and rotation.** One refresh from the server; then the old refresh token again; then one
listing page with the new access token.
- Rotation on: write-before-use and the single lock (§10.3) are essential, and designed.
- The old token rejected: a crash between reply and write costs a sign-in (one push). No `.prev`.
- The old token still accepted for a while: keep `tokens.prev.json` for one cycle.
- **Refused from the server's address:** the design fails. Stop and ask Dan.

**S4. A refused refresh.** A copy of the sign-in, with one character of the refresh token changed, is
refreshed. Learn the status and the OAuth `error` code (never its description).
- `PlaudAuthError` matches those codes exactly. With no clear answer, any 4xx except 408 and 429
  counts as refused.

**S3. Does a second sign-in sign the first out?** With the server's session sign-in working, Dan signs
in on Plaud's web app, then the Plaud MCP's `login` on the Mac, then the vendor CLI in another
temporary `HOME`. After each, one cheap call with the server's sign-in.
- All still signed in: nothing changes.
- Any signed out: record which action does it, and ask Dan this before the production sign-in.

> **The question (only if S3 finds a sign-out).**
>
> Scenario: signing in to Plaud anywhere else signs the server out. You use the class-notes and
> Plaud skills on the Mac most weeks. Each time one signs in, the server's sync stops, and your phone
> says "Plaud sign-in needed". What do you expect?
> - **A. The server owns the sign-in (Recommended).** You stop signing in to Plaud's tools on the
>   Mac. Class notes and Plaud lookups read from the archive and its mirror, which hold every
>   recording with Plaud's transcript and notes. Until those skills are changed, they don't work.
> - **B. The Mac owns it, and the server borrows it.** The server uses a copy of the Mac CLI's
>   sign-in. It works only if refresh tokens don't rotate (S2); if they do, whichever side refreshes
>   first signs the other out.
> - **C. Take turns.** Each time you use a Mac Plaud tool, the server stops until you run `make
>   plaud-sign-in`, with one push each time.
>
> Checked before recommending A: §1's sixth failure (C makes upkeep a chore); S2's rotation result
> (B fails if tokens rotate); main spec §7.4's "paths around the allow-list", which says the Plaud MCP
> and skills hand external agents every recording, private ones included. What could go wrong:
> class-notes uses Plaud's share links, and may need work before it reads the archive.

**S5. The listing's shape, a cheap change signal, and paging.** Page 1 at sizes 5, 20, 50, 100 and
500; then the whole listing twice, a minute apart (the council's S6 is folded in).
- The page size and its clamp set the client's page size, and, if small, the budget and
  `schedule_minutes` (§14.3).
- An empty page ends a listing; a total, if there is one, is compared too.
- Two passes that differ with nothing recorded meanwhile: the paging drifts. The client then lists
  twice when the count is within 5 of the last good listing, and takes the union.
- **A field that marks edits:** §14.3. S9 confirms which edits move it.

**S7. The ID form** (main spec §19's own spike). The canonical listing IDs against the archive's Plaud
references (through the index; no ID printed), and the count of consent-list IDs found in the listing.
For 3 recordings Dan names (none private, none consent-listed), the audio's hash against the
archive's. Then one of them fetched by its `of_` form and by its bare hex.
- Hex equal, audio identical: the compare by canonical ID stands, and the hash merge is a backstop.
- **Hex differs:** every old recording looks new, and **the consent list is void as written.** Stop
  and ask Dan before the dry run: every consent entry must be found again in its new form before
  anything is imported. The catch-up would then download each old recording once and merge it by
  hash (main spec §6.4).
- Whether the bare hex works doesn't matter (the client uses the listed form). It is recorded.

**S8. The envelope, and volatility.** Five recordings Dan names (a recent one, an old one, one with
renamed speakers, one without notes, a long one), none private or consent-listed, each fetched twice a
minute apart. Learn the envelope's and blocks' key sets, the data types, which blocks sit behind a
link, the links' expiry, segment counts, whether any `embeddingKey` repeats across them (main spec
§12.6a asks this of 2b's spike, for stage 3b), and whether each part's normalised hash matched.
- All matched: the drop list holds.
- **A part differed:** a volatile field. It joins the drop list, with a normaliser version bump,
  before the baseline.
- Either link lifetime works with §10.2. An unknown block type is handled before the baseline.

**S9. What an edit looks like** (Dan's hands, session C). On his phone, before the clip uploads, Dan
titles a short new clip that isn't private, starting with the keep-out word. Then:
- **(a)** Is the word already in the clip's first listing? How many minutes from the device transfer
  to the listing? This says whether the word can beat the first sync.
- **(b)** He generates a transcript and a summary: does Plaud's title keep the word? How long after
  upload do the transcript and the notes appear (for §14.2's settled rule)?
- **(c)** One at a time: renames a speaker; renames the title (keeping the word); regenerates a note
  and adds an in-app note; edits a transcript line; moves it to a folder; deletes it to the trash and
  restores it. After each, it is fetched and listed again: which parts changed, which listing fields
  changed, and how many seconds until it showed. After the deletion: is it listed, and what does
  `files/{id}` answer?
- **Outcomes:** a speaker rename changes the speakers part (§20's criterion 2 holds as designed); a
  listing field that moves on a rename (§14.3); a delay of seconds (Re-sync's reply says so); the
  trash removes it from the listing ("gone" works); a folder move that adds a field (the drop list,
  or accepted before the baseline). **If (a) shows the word can't beat the first sync,** the README
  says plainly that it usually arrives late, and what late means.
- At the end, Dan deletes the clip in Plaud.

**S10. Pacing.** Twenty calls at one a second: the names of any rate-limit headers, and whether
`Retry-After` appears. Limit headers set the pacing and the budget below them; with none, one call a
second and a 10-minute budget stand. Never provoke a 429.

**S12. Time zones.** Three recordings Dan names and remembers. Learn whether Plaud's start time is
UTC or local time marked `Z`, and the offset in hours from his memory. UTC: `recorded_at` is it in the
default zone, as designed. Local marked `Z`: read it as local. Settled before the catch-up, because an
ID's time never changes (main spec §6.2).

**S11. A large download.** A long recording Dan names, downloaded to the spike folder and deleted:
its size, time and throughput, whether `Content-Length` is present, whether `Range` is honoured, and
whether the link's expiry passed during the download. These set the stall rule and the disk guard. If
a link can expire mid-download, the client resumes with `Range` when honoured, and otherwise restarts
once with a fresh envelope.

## 19. Changes to the main spec and other docs

The plan's first task applies these, each marked "(Dan, 2026-10-23)" or "(stage 2b design,
2026-10-23)". Spike results are added as they arrive. In the main spec's list, a bold § is the main
spec's own section, and a § in brackets is this spec's. Each also settles the contradiction it names.

**The main spec:**
- **§2, "Decisions that replace earlier notes":** the grace period no longer carries over (decision 1).
- **§3:**
  - "One image, two containers" becomes three. The diagram gains `backup`.
  - After "Why one writer": one writer means one machine; several of its processes may each hold one
    `Writer` (§9.1). Settles "all writes in the worker" against tagging part A.
- **§5:**
  - Line 220's "mounted read-only into both containers" becomes "every container".
  - The `[plaud]` keys of (§17.4).
  - **The token's location:** "`tokens.json` in its own host folder (`RECORDINGS_PLAUD_HOST`,
    `/srv/recordings/plaud`, 0700), mounted read-write in `worker` only, at `/plaud`; never backed up
    or mirrored". Settles §5 and §6.8 against `config.py:33` and the shared `/state`.
  - Secrets gain the sync's dead-man URL; `deploy.env` gains `RECORDINGS_PLAUD_HOST` and
    `RECORDINGS_CONSENT_HOST` (a folder); doctor's roles gain `worker` (§17.2).
- **§6.4:**
  - The `plaud` lock sits beside `ops` (§9.4).
  - The Writer's operations gain `add_snapshot` (§9.6).
  - "Delete moves the folder to `trash/`" is the app's Delete. Taking a recording out by hand
    deletes it for good, a by-hand exception like Forget (§12.4).
- **§6.8:**
  - `state.db` is mounted in every container, and gains the tables of (§9.5), with no version bump.
  - "The same folder holds the Plaud token store" goes. The state folder gains `plaud-cache.db`
    (derived, never backed up) and `manifests/`.
  - The rebuild after losing `state.db` also seeds the auto-private marker and the baseline (§13.2,
    §14.5), and a lost, rebuilt or older `state.db` closes the sync's gate until Dan accepts a
    listing (§11.6).
  - `index.db` is in WAL mode.
- **§7.4:**
  - "Everything comes in, with one exception" becomes two: the consent list, and the keep-out word.
  - The automatic private tags are rewritten as (§13.1).
  - "`recording.json` is the single source of truth" stays true: the marker in `state.db` is the
    sync's bookkeeping about which titles it has judged, never the privacy state itself.
  - It gains (§13.3)'s exception sentence.
- **§9.0:** `Source` gains `check()`, which downloads no media.
- **§9.1:**
  - Its Missing, Updated on Plaud and Only in the archive lists become (§11.3)'s buckets, Updated on
    Plaud, and Gone from Plaud. The compare matches by ID; the hash match is at import. Settles "and
    by content hash".
  - "Recent recordings are checked again for a while, the way `audio-router`'s hot window does"
    points to the rolling sweep.
  - The worker bullet becomes: "Every Plaud call, and every write that comes from Plaud, happens in
    the `worker` container, through its own `Writer`. UI buttons ask it to act through request rows in
    `state.db`" (§9.3).
  - The consent list: one list in a folder mount; the file's meanings unchanged (an exclusion is an ID
    alone on its line, or an ID then a dash), now read in the `of_` form and any case; lines read as
    kept are listed for Dan; a typo fails closed; no title of a consent-listed recording is stored or
    shown; a late listing is tagged at the worker's next tick without a listing, flagged, and pushed
    once per recording (§12.1). Settles the single-file mount and `consent.py:17`.
  - It gains the keep-out word, and kept out stays kept out until Release (§12.2).
  - **The CLI:** beside `recordings plaud sync [--dry-run]` and `--full`, the commands of (§9.6):
    `sync --catch-up`, `resync`, `baseline [--all]`, `accept-listing`, `breaker reset`, `park`,
    `release`, `keep-out-words`, `consent`, `held`, `check [--volatility]` and `token-import`, and
    `recordings worker [--alive]`.
  - The grace paragraph is replaced by decision 1.
  - **The token paragraph:** the location; refresh before expiry; **"a refused refresh raises an
    alert"** instead of "a 401 raises an alert"; `make plaud-sign-in`.
  - The spike list points to (§18).
- **§9.1.1:**
  - the breaker's window, volatility found by fetching twice, suppression and reset (§14.4); the
    holds and **Accept this listing**, and a small shrink settling by itself (§11.4); the good-run
    rule (§11.7); the listing's rules (§11.2); the sweep's intervals and cap (§14.2); every check ending
    with reconcile (§14.1); the baseline (§14.5); Re-sync of a selection, parking and Sync everything's
    outcomes (§14.6); removals counted until 3a (§14.7)
  - "A 401 stops the run and raises an alert" becomes a refused refresh (§10.3)
  - Status's contents, with the census of ID forms. Settles "the field-name diff would have caught
    the `of_` change", which it couldn't: the field was still `id`.
  - **`full_sweep_days`:** 1 in the template while relabelling, 7 when unset. Settles `recheck_days`.
  - the fake server's injections (§21)
- **§9.3, the mapping:** deferred rows that Plaud no longer lists stay in `audio-router`'s frozen
  archive (§11.3). Settles the mapping row against the 2a runbook deleting `import/`.
- **§12, navigation:** the Add page exists from 2b: "Library, Tags, Add, Status" until 3b.
- **§12.1 and §12.3:** the arrivals cue, the selection bar's Re-sync and the Details lines (§15.3).
- **§12.2:** "No confirmation dialogs" gains a fourth exception: **Release** of a recording kept out
  by its title, which shows the recording's title in its question (§12.2).
- **§12.4:** replaced by (§15.1).
- **§12.5:** gains the sync panel and its Needs attention entries (§15.2). Its "backup service last
  reported" line stays the backup's; the worker has its own line. Settles sync state being on Add
  and on Status: the Sync screen acts, Status reports.
- **§14:**
  - "They fire on: a Plaud 401" becomes (§16)'s keys.
  - "A turn skipped because another job holds the `ops` lock … is neither a failure nor an alert"
    adds the `plaud` lock.
  - **The dead-man's switch** becomes two checks: the backup's, unchanged, and the sync's (decision
    4, as refined in §16). Neither shares the other's URL. Settles one check for two jobs.
  - **The grace:** "The grace is at least 3 hours, because long jobs … run in the same loop" becomes
    the backup check's rule. The sync's check has a grace of 1 hour and a period of
    `schedule_minutes`, because the worker pings it at every turn it handles, and withholds the ping
    only while a push is owed and undelivered (§16). This spec's 1 hour wins for the sync's check.
- **§15:** the worker publishes no port and adds no route; only `worker` holds the token; egress
  isn't blocked (§17.1); "Containers: both run as non-root" becomes all three.
- **§15.1:** "not … the Plaud token store" stays, now about its own folder. The consent folder and
  `/state/manifests` are backed up through `extra_paths`.
- **§16:** gains (§21)'s tests.
- **§17 and §17.1:** no worker in either demo; the panels are canned (§15.5).
- **§19:**
  - Step 5: (§18)'s spikes, the token's and the consent list's folders, and the sync's check.
  - Step 6: both equations and the consent-list match.
  - Step 7: (§11.6)'s order.
  - The disposable window ends at the first tag after step 7 and its backup (decision 7). Settles
    "the first real tag after the dry run".
- **§20, stage 2b:**
  - "auto-private, with the consent, hold and grace rules" loses "and grace".
  - The worker bullet narrows to Plaud's calls and writes.
  - A compose bullet (§17.1).
  - "2b is done when" becomes (§20)'s list.
  - "If 2b runs long" becomes (§4)'s two halves, noting that `state.db` only gains tables.
- **§22:**
  - Decision 19 is replaced by Dan's decision 1 of 2026-10-23.
  - Decision 20's "with a dead-man's switch" becomes "with two dead-man's checks".
  - Decision 31 gains the token's location.
  - Decision 35's "A private tag is always applied before any content is written" gains (§13.3)'s
    exception.

**Other files:**
- **`CLAUDE.md`:**
  - The Privacy bullet changes as §13.3 says.
  - The archive's docs are written "whenever a Writer opens" becomes "whenever the web app's or the
    CLI's Writer opens (the worker's doesn't)".
  - "Every write goes through `Writer`" gains: "The worker's downloads in `.tmp/plaud-dl/` are scratch,
    not archive data; they enter the archive only through `Writer.add`."
  - New: "The Plaud client is read-only and runs only in the worker. `recordings_ui` never imports
    the client, the token store or the sync. `tokens.json` is a secret: used by reference, never read
    or printed, and a Claude session never runs `make plaud-sign-in` or `token-import`."
  - Toolchain: `make e2e-live` runs the live e2e tests (`web`, `worker` and the fake Plaud server), and
    CI runs it beside `make e2e`.
- **`README.md`:**
  - A paragraph on stage 2b, with "Once a recording has come in, deleting it in Plaud, or renaming it,
    doesn't remove it here", S9's finding on the keep-out word, and that the forgiving match can keep
    an ordinary title out until Dan releases it.
  - **Decision 7's line** becomes "Decided 2026-10-23, refining 2026-10-22: the first real tag waits
    until stage 2b's first real Plaud import and the backup after it (runbook section 8)".
  - The config table loses `RECORDINGS_PLAUD_TOKEN`, and gains the Plaud sign-in (`make
    plaud-sign-in`) and `RECORDINGS_DEADMAN_SYNC_URL`.
  - The Docker section: `make deploy` starts web, backup and worker; the guard waits for Sync
    everything and the catch-up; `plaud-sign-in` and `consent-add`.
- **`docs/runbooks/first-run.md`:** §17.5.
- **`config.example.toml`, `config.py`, `docker/compose.yml`, `docker/deploy.example.conf`,
  `docker/recordings.service`, `docker/ops-busy.py`, `docker/deploy-guard.sh`,
  `test_template_settings.py`, `.gitignore`, `.dockerignore`:** §17.
- **`Makefile`:** `help`, `plaud-sign-in`, `consent-add`, `e2e-live`, and `-include local.mk` (GNU make
  3.81).
- **The tagging part A spec, §1:** a note that decision 7 refined when tagging starts.

## 20. Done when

Main spec §20's four criteria made testable, with what Dan's decisions add. "Fake" is the fake Plaud
server (§10.6); "real" is a runbook step. Each criterion says which half it belongs to (§4).

1. **The dry run accounts for every listed ID** (first half).
   - **Fake:** a property test over random accounts (repeats across pages, both ID forms, consent and
     keep-out rows, late keep-outs and late consent listings already here, partial listings): every
     ID in exactly one bucket by §11.3's precedence; the equation adds up.
   - **Real:** runbook section 7's checks, signed off by Dan.
2. **A speaker rename shows within the sweep window** (second half).
   - **Fake, with an injected clock:** at N = 1,000 with 40 renames at once, each shows within
     `full_sweep_days` plus one schedule interval, and at once with Re-sync.
   - **Real:** S9's clip; after the catch-up, one rename at once with Re-sync, and one within a day
     and one sync without it.
   - **Fake:** three speaker renames 20 minutes apart on 12 recordings never trip the breaker; a field
     that changes on every fetch does.
3. **Sync everything exits 0** (second half).
   - **Fake:** a sound account exits 0 and writes the manifest; a degraded recording still exits 0; a
     flagged one (consent-listed or kept out late) still exits 0 and is named; an unparked failing
     recording exits 2 with its count; killed part-way, it resumes.
   - **Real:** it exits 0, or exits 2 only for recordings Dan then parks, each checked in Plaud's app
     and recorded in the runbook's table by ID hash.
4. **A refused sign-in raises an alert** (first half), replacing "a 401 raises an alert".
   - **Fake:** an expired access token that refreshes sends no push; a 401 then a refused refresh sends
     exactly one; none again within 24 hours; no Plaud call until the token changes; a new token
     brings one "works again"; a 429 from the token endpoint is an outage; a 401 after a good refresh
     goes through the current-user read.
   - **Real:** `alert-test` proves delivery.
5. **Dan's decisions hold** (first half), on the fake:
   - A placeholder-titled recording arrives at the next run, untagged; renamed private, it is tagged
     at the next run even when its fetch fails (decision 1).
   - A keep-out title is never requested (the request log). A late one gets `private`, a flag and
     exactly one push, and nothing of it is fetched again even when its transcript appears. A title
     that loses the word stays kept out until **Release**; Release shows its question, and is refused
     while the title still carries the word or the ID is consent-listed (decision 2).
   - A consent-list typo stops every import and pushes; a bare `ID kept: …` line is not an exclusion,
     and is listed for review; a late consent listing is tagged at the next tick with the sign-in
     dead, pushed once, never fetched again, and the run goes on; adding an already-flagged recording
     sends no second push; a list replaced by rename is read at the next tick; during a consent stop
     the cache and the Sync screen hold no title of a recording not here.
   - A removed `private` stays removed through 100 runs and a `reindex` in a `web`-role process, and
     comes back only when the title changes to one that matches, with its note (decision 3).
   - The gate: before the first catch-up, a scheduled sync and an import request are refused; a
     `state.db` replaced by an older or empty one closes it, pushes once, and Accept reopens it.
6. **Nothing stops silently** (first half; the breaker's part, second half):
   - A run whose listing works and whose every fetch fails is not good, and pushes after 2 hours. Once
     every recording is on the failing list, runs that retry only them stay not good, and no "works
     again" is sent until a fetch succeeds.
   - An outage of 1 hour sends no push; 2 hours sends one; recovery sends one. Status shows it from
     the first failed run.
   - The sign-in, consent, listing and breaker keys push at once, and a good run doesn't clear them.
   - A hold clears with Accept this listing, which is refused while the consent list matches fewer
     IDs; a small shrink settles when the next listing an hour later is missing the same recordings.
   - The sync's check is pinged at every turn handled, including turns stopped by a dead sign-in, a
     hold or the gate; with the worker killed, no ping; with a due push undelivered, no ping.
   - An idle worker with no sign-in stays alive (`--alive` 0) for 20 minutes on a fake clock.
   - The schedule off for 24 hours after the catch-up shows in Needs attention.
7. **Nothing leaks** (first half): a whole sync at DEBUG, with the faults of §21, leaves no token,
   `X-Amz-` query or title in any log, alert, Status view, `ops_runs` row or request result; a link
   with a control character or an unknown scheme raises the client's own error, with no URL.
8. **Rollback stays a tag** (first half): `SCHEMA_VERSION` is 1 and `MIGRATIONS` empty; a recording
   written by the 2b import validates against 2a's model and schema; `make deploy-smoke` passes.
9. **The interface** (both halves): the Sync screen and Status at 1,280 and 320 pixels; Kept out and
   Flags rows with no title; Release; Park; Re-sync of a selection; the arrivals cue; demo and Pages
   with no network.
10. **Operations** (first half): `make deploy` waits for the worker; the worker stays healthy with no
    sign-in and no consent list; `doctor --role worker` passes on the server; `deadman_sync_url` is
    mounted in `worker` only and `deadman_url` in `backup` only (`test_deploy_template.py`).
11. **Decision 7's wording** (first half): a doc test checks the README, the runbook and the tagging
    part A spec. It fails if the old sentence ("until stage 2b's import dry run is done") is in any of
    them, **and** if the new one ("refining 2026-10-22") is missing from any of them.

## 21. Testing

**Every fixture is synthetic** (main spec §16; CLAUDE.md). The fake server's shapes come from spikes S5
and S8: field names only, with invented values.

- **The fake Plaud server injects:**
  - on the token endpoint: a refused refresh (S4's codes); rotation, and none; 5xx, a timeout, a 429,
    an HTML 200; a 401 after a good refresh
  - on the API: 401, 403 (`PlaudForbidden`), 429 with `Retry-After` within and beyond the budget,
    5xx, timeouts, short bodies, an HTML 200 on `files/{id}`
  - on the listing: a clamped page size, an empty listing, a shrink, repeats across pages, drift while
    paging, a failure on page K only, malformed rows (under and over 1%), the ID form flipping
    between bare and `of_`, the hex changing with the audio unchanged
  - on fetches: every `files/{id}` failing while the listing works; an unknown envelope shape on
    every fetch; every link 403ing; a link that expires once, then works; a link with a control
    character, a non-https link, an unknown scheme; a link that expires during a download; a trickle
    download; truncated audio
  - content: reordered blocks; content moving between inline and a link; a volatile field in "other"
    after run K; a transcript field that differs on every fetch (volatile); stable speaker renames,
    one speaker at a time, 20 minutes apart; a recording that always answers 500; 404 and 500 for a
    deleted ID; "trashed but fetchable"; a new ID whose audio matches a flagged recording
  - Dan's edits: a title, a speaker rename, regenerated notes, a deletion, a restore; a hook that
    changes the account after request K
  - kill points: between a snapshot's publish and its `mutate`, and between that and reconcile
- **Core, unit and property tests:**
  - the consent parser, against today's test file and more: `of_` and upper case read; `` - `of_…` —
    note `` an exclusion; `ID kept: …` and `- ID (fine to keep)` kept lines, listed; an ID
    mid-sentence, listed; `+ ID` listed; a 31-hex and a 33-hex line (stop); a list replaced by rename
    (read at the next tick)
  - the late consent listing: applied with the sign-in dead and the gate closed; no second push for
    an already-flagged recording or one gone from Plaud; the cache and Sync screen hold no title of a
    recording not here during a consent stop
  - the keep-out match: each example in §12.2, a fullwidth `ｐｒｉｖａｔｅ`, an earlier word, and
    `keep-out-words --drop`
  - Release: the preview's title lives only in the cache and is deleted after the answer; refused
    while the title carries a word, or the ID is consent-listed; Undo before the next sync
  - the marker: due and not due; a failed `mutate` retried after `reindex`; a new pattern, checked
    against that pattern only; a rebuilt `state.db`, with its note; a hash merge under a new ID; a
    hash merge into a flagged recording, discarded
  - the gate after a rebuild: a `state.db` emptied or older than the archive's newest 2b snapshot
    closes it and pushes once; Accept reopens it
  - the buckets, as a property (§20, criterion 1); each hold's thresholds (§11.4), the census measured
    over shared IDs, `accept-listing` and Accept's refusal; a small shrink settling on the same absent
    set; a 40-recording backlog importing over 2 runs with no push
  - the sweep's intervals and cap, with an injectable clock (`ops.Context.clock`'s pattern), at N =
    1,000; the breaker's window, the double fetch, suppression (no interval reset) and reset (no
    re-trip)
  - the good-run rule: failing-list recordings as attempts; recovery by cause; the companion check
    beside a failing recording; `LockTimeout` as busy; `failing_since`
  - each alert key's onset, 24-hour reminder, recovery, and `plaud:privacy`'s deduplication
  - the ping: at every handled turn (the gate, a dead sign-in, a hold, busy); withheld while a due
    push is undelivered
  - the token store: atomic writes and modes; one refresh across two processes; a crash between
    reply and write: with rotation and no grace, the next start finds the old file, its refresh is
    refused, and one push follows; with a grace, it recovers
  - the client: `GET` only plus the refresh `POST`; no redirects on API calls; no `Authorization` on
    links; the listed ID in every URL; the stall rule
  - `token-import`: a bad file installs nothing and exits 77
  - requests: coalescing, `request_id` repeats, the caps, a `full` within the hour, expiry,
    interrupted rows, the first-catch-up refusal
  - the import: the disk guard (2 × `Content-Length`), a short download, a hash merge, 3 failures to
    missing, the scheduled cap of 20
  - the exit codes, 0 to 78
- **Two real processes** (flock needs them): the worker importing beside a tag batch (no lost update;
  `reconcile_plaud`'s hold with 30 outputs bounded below 2 seconds; `_create` never contends with a
  tag); the worker's `private` landing between a tag dialog and its write; Undo after a worker write
  keeps both; `plaud` busy and the CLI's 60-second wait then exit 75; `ops` and `plaud` in one hold.
- **The worker:** the start order; the heartbeat during a 3-minute download and a 2-minute
  `Retry-After` (`--alive` stays 0); an idle worker with no sign-in for 20 minutes (`--alive` stays 0);
  a wedged main thread (`--alive` fails after 15 minutes); the next due time across a crash loop;
  SIGTERM between recordings.
- **Shell targets,** with a fake `ssh` on `PATH`: `make plaud-sign-in` expands `SERVER` and
  `REPO_DIR` on the Mac and removes its temporary `HOME` on failure; `make consent-add` refuses a bad
  ID and writes by rename.
- **Backups:** the backup's file list leaves out `plaud-cache.db`, and doctor reports an
  `extra_paths` that would include it.
- **The spike script's guard** (§18): refuses a consent-listed ID, a private-tagged recording and a
  keep-out title, and counts them.
- **The flow test,** `test_flow_2b.py` (as `test_flow_2a.py`): an archive with an `audio-router`
  fixture, then against the fake, `baseline`, `sync --dry-run`, `sync --catch-up`, the worker's loop
  for N ticks on a fake clock, and `--full`. It checks files through `Archive`, the Status view,
  pushes through `FakeNtfy`, pings through a fake Healthchecks, and scans every log for tokens,
  `X-Amz-` and titles.
- **Guard tests that must fail without their fix:** consent-listed and kept-out IDs never requested,
  and no block or audio of a recording whose envelope title is kept out; `recordings_ui` never imports
  the client, the token store or the sync, and Pyodide never does; the demo never writes a request
  row, refuses a crafted one, and starts no worker; no title in logs, alerts, Status, `ops_runs` or
  request results; `SCHEMA_VERSION == 1`; doctor's `web` and `backup` roles report `/plaud` as a
  problem; the client holds no real-looking ID (§9.6).
- **Doctor:** a `worker` case for each check of §17.2.
- **Deploy:** `ops-busy.py` with a lock name and jobs; `deploy-guard.sh` refusing for `plaud-full` and
  `plaud-catch-up` and letting `plaud-sync` through.
- **Settings and config:** every new key's row (§17.4); `keep_out_word` validation; `api_base` https
  or loopback.
- **Compose:** `test_deploy_template.py` covers the new service (hardened, a hostname, a role,
  long-syntax binds, the commit tag, its own health check, the consent folder as a folder, the
  dead-man URLs in one service each); `compose_smoke.py` runs the worker with no sign-in and no
  consent list.
- **Status reads:** a `state.db` without the new tables shows "No sync has run yet".
- **The UI, with Vitest:** the request states and coalesced replies; the equations; the summary
  sentence's rules; each Needs attention entry; Kept out, Flags, Release and Park rows; the Details
  lines; the arrivals cue's counts; the fixed phrases.
- **e2e against demo mode** (`make e2e`): the Sync screen and Status's sync panel draw the canned view
  at 1,280 and 320 pixels (four tabs on one row); the buttons are disabled with the demo's note; axe
  in light and dark; a clean console.
- **e2e, live** (`make e2e-live`, a new module with its own servers): `web` and `worker` against the
  fake and a synthetic archive.
  - Before the catch-up, only Compare is enabled.
  - Compare shows the equations; Import selected imports with progress and a toast.
  - Kept out and Flags show no title; Release opens its question with the title, and lets the
    recording in; Undo works until the next sync.
  - A hold disables the import buttons; Accept this listing clears it.
  - Re-sync on a selection updates rows.
  - The arrivals cue appears after a scheduled tick.
  - Status shows a refused sign-in.
  - With the worker killed, the screen says it isn't running.
- **Pages** (`make pages-test`): the canned Sync screen on Pyodide, no request, a clean console.
- **A doc test** for decision 7's wording (§20, criterion 11).
- **Real-run checks** live in the runbook, not in CI.

## 22. Left to the builder

**Builder's calls:**
- Module names and table columns, within §9.5 and §9.6. A "view model" task, defining what each view
  reads from which table, comes before every UI task (§15's build order).
- How the start-up check for a rebuilt `state.db` (§11.6) finds 2b's snapshots quickly (a pass over
  `source/` at start, or the index).
- The exact words, within §15.6's phrases.
- The Sync screen's look, within shadcn/ui, Tailwind and the brand (main spec §12.6).
- The vendor CLI's name and token format, from S1, in `make plaud-sign-in` and `token-import`.
- Whether the token endpoint needs an `auth_base` setting, if S1 shows it on another host.
- How "Scheduled sync is off" goes quiet after DAN-15 (§17.5).

**Numbers this spec suggests** (change one only with a stated reason): the tick (2 seconds), the
heartbeat (30 seconds) and `--alive`'s limits (90 seconds, 15 minutes); the request and event
retention (7 and 90 days); the backoff and budget (§10.2, adjusted by S10); the stall rule (§10.1);
the holds' thresholds (§11.4); the sweep's intervals (§14.2); the breaker's (§14.4); the Healthchecks
grace (§16).

**To check during implementation** (checked when first used, as in tagging part A §11):
- `urllib`'s `add_unredirected_header`, and a refusing opener like `alerts.py`'s.
- WAL for `index.db` on its named volume, opened by `web` and `worker`.
- `docker compose up --wait` honours the worker's health check.
- Healthchecks takes a period of `schedule_minutes` with a 1-hour grace, and `/log` leaves its state
  alone.
- Shiny: reading a pending request every 2 seconds for one session, and `send_message` for replies.
- `asyncio.to_thread` for the request insert, in the Shiny version pinned.
- ffprobe in the `worker` container.
- **The Library's first load is under 2 seconds** (runbook, after section 4) before 2b deploys;
  otherwise the Library moves onto `index.db` first.

**Carried from stage 2a** (2a rulings), and where 2b handles each:
- `index.db` in WAL mode (item 72d): §9.5.
- The auto-private residual (Task 7; Task 9 minor 1): §13.2.
- Seeding last checked on a freshly adopted `state.db` (item 39): §14.5.
- "Restoring for real" (item 35): §17.5.
- The Plaud items (item 94): `speakers.source` following a rename to the newest Plaud transcript, and
  degraded-only recordings counted on Status, are handled in 2b. Carried to 3a: the timing verdict
  after a trim (the duration compare surfaces it), ties on the newest transcript, note-type numbering
  moving with `data_id`, interleaved lineages from two Plaud IDs, and keeping Plaud's title for 3a.

## 23. Risks

Most serious first. §7 is Dan's short list.

1. **A recording that must not be kept is downloaded before Dan acts.** The window is until the next
   sync. Mitigations: the keep-out word, from the phone; a consent list that fails closed and is read
   before each recording; the ID-form hold. What remains: a recording not marked in time is in the
   backups and the mirror until taken out by hand, and the `pre-import` and `post-import` snapshots
   keep whatever the catch-up imported unless a NAS-side `restic rewrite` removes it.
2. **The keep-out word may not beat the first sync** (S9 measures it). If it usually can't, the late
   path is the main path, and the README must say so.
3. **The flow stops, and nobody notices.** Mitigations: the good-run rule, the 2-hour rule, the sticky
   keys, the sync's check (which also speaks when a push can't be delivered), "Scheduled sync is off"
   in Needs attention, and the push when the sync's records are rebuilt. What remains: ntfy and
   Healthchecks failing together, or Healthchecks' own notification not reaching Dan.
4. **A late private title sits untagged in the backups and the mirror** for one sync (decision 1's
   exception).
5. **The forgiving keep-out match can keep an ordinary title out,** such as "No archive access: IT
   ticket". That errs safe: the recording waits under Kept out, and Release brings it in.
6. **The sign-in model fails on contact** (S1 to S3). §18 says where to stop and ask Dan.
7. **The token chain is lost** in a rotating refresh. The cost is one push and one sign-in.
8. **Duplicates, or a void consent list, after an ID-form change.** The ID-form hold stops imports
   until Dan accepts, and Accept is refused while the consent list matches fewer of Plaud's IDs; S7
   checks the hex before the dry run.
9. **A volatile field mints snapshots** before the breaker trips: up to about 5% of a day's checks.
   S8, the volatility check and the double fetch catch it early.
10. **Rate limits, or a flag on the account.** Pacing, no parallel calls, a budget, and
    `schedule_minutes = 0` as the off switch.
11. **Tag clicks get "busy"** when the worker's hold passes 2 seconds. Locks only around local work,
    and tests that bound the holds.
12. **Losing `state.db`** loses the sync's memory: its markers, what was kept out by a word now
    gone from the title, and the keep-out words used before. The gate closes and pushes (§11.6); the
    dry run shows any formerly kept-out recording as missing, for Dan to decide; and a removed
    `private` can come back once, with its note. The consent list, in its own backed-up folder, is
    the durable way to keep a recording out.
13. **A wrong time zone is permanent in an ID.** S12 settles it before the catch-up.
14. **The subscription lapses on 2027-08-29.** DAN-15's runbook step turns the schedule off after the
    final Sync everything.
15. **The relabelling evening at scale.** With thousands of recordings, the budget may stretch a full
    pass past a day. Status shows the real oldest check; Re-sync of a selection is the way round it.
16. **The Library's first load** may pass 2 seconds as recordings arrive, pulling a 3a item forward.
