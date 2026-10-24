# Stage 2b: Plaud flows in and keeps up (design)

**Status:** spec approved 2026-10-23 · Phase 1 in progress on stage-2b (worktree .claude/worktrees/stage-2b) · Phase 2 planned · Phase 3 planned

- **Date:** 2026-10-23 (revision 4, after the council's review, its re-review, a final check, a
  builder check and a simplification pass)
- **Approval:** approved by Dan on 2026-10-23 (Revisions, at the end). Dan's eleven decisions are
  binding and kept as he gave them. The rest is the controller's design, built from a five-member
  research council's work. Calls made without asking Dan are listed in §5, by what he will notice.
- **Parent:** `docs/superpowers/specs/2026-10-08-recordings-design.md` (the main spec), §20 stage 2b.
  §19 lists the changes this spec makes to it and to other docs.

**How to read this.** Part I is everything Dan will see, do or accept, in about 550 lines. Part II
is how it is built, for the builder. Part I is binding: Part II says the same thing in more detail,
and each part of Part I points into Part II. The Scenarios table and the Revisions follow §23.

## Outline

2b is built in three phases, each its own plan, branch and pull request, split by the spikes (§18:
"Each spike runs before the tasks that use its result"). Phases 2 and 3 are §4's two halves. Each
phase deploys and rolls back by its tag.

| Phase | Goal | Done means |
|---|---|---|
| 1. The groundwork | Everything that needs no spike result, and the spike script, so Dan can run the spike sessions while the rest is built (`docs/superpowers/plans/2026-10-23-stage-2b-phase-1.md`) | The consent grammar and the keep-out word, the Plaud client copy, the 2b tables, the auto-private marker, late consent listings and their flags, the gate, the worker (idle and healthy, its ping rule tested), the request path, the worker's container, doctor and backups, and the whole interface against its view model are built and tested; the spike script passes its guard tests; Phase 1's scenario rows are built; the pull request is merged |
| 2. Plaud flows in | After spike sessions A and B: the sign-in, the fake server's shapes, the listing, the compare, the hold, the import and the first catch-up | The dry run accounts for every listed ID (C1); a refused sign-in stops the ping (C4); Dan's decisions hold through the import (C5); nothing leaks (C7); runbook sections 6 to 8 run on the real server, ending "You may start tagging now" (decision 7) |
| 3. Keeps up | After spike session C (S9): edit-sync | A speaker rename shows within the sweep window, and the breaker stays out of the way (C2); Sync everything exits 0 (C3); Re-sync of a selection works on the real server |

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
- When the sync stops for about 2 hours, he gets one email from Healthchecks, and Status says why
  and what to do (decision 10). Phone pushes can come later. Otherwise it is quiet.
- Keeping it running is almost no work. If Plaud signs the server out, signing in again is one
  command on the Mac.

**What would make 2b a failure, even if every test passes.**
1. **A recording that must never be kept is kept,** in write-once media, the backups and the Mac's
   mirror, and Dan doesn't know.
2. **It stops again, and nobody notices:** an expired sign-in, a dead worker, a tripped breaker or a
   schedule left off, silent for weeks.
3. **Dan still checks Plaud by hand.**
4. **Alert fatigue:** emails for routine blips teach him to ignore them.
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
8. **Decision 4, refined: "One push, with reason"** (recommended; asked after the builder check).
   - The worker pings its own Healthchecks check at every scheduled turn it handles, good or bad, so
     a ping means "the worker is alive and deciding".
   - A Plaud outage or a refused sign-in reaches Dan once, through the app's own push, with the
     reason.
   - Healthchecks speaks only when the worker itself is dead or stuck, or when a push it owes Dan
     can't be delivered. In that case the worker withholds its ping.
9. **Alerts while there's no ntfy: "3 + 1"**, the design as built plus Status and Healthchecks.
   Leave space for ntfy later. Dan doesn't have ntfy on his Synology setup yet, and doesn't much
   care about phone pushes for now: "there isn't much i can do with the recording on the phone".
   - The controller's reading of "3 + 1": keep the push design as built, so it works once ntfy is
     set up, and until then rely on Status and Healthchecks.
   - With no ntfy configured, every push the worker owes Dan counts as undelivered. The worker then
     withholds its Healthchecks ping, and Healthchecks emails him after its grace. Status says why.
   - Once ntfy is set up, the same events push to his phone instead, and Healthchecks goes quiet
     again. No other change is needed.
   - In the runbook, ntfy is an optional step for later. The sync's Healthchecks check is required.
10. **Alerts trimmed to one rule: "2 hours, same as all"** (recommended). Dan asked for this after
    asking for a simplification pass ("yes run that pass"). It changes how decisions 4, 5, 8 and 9 are
    carried out, not what they are for.
    - 2b sends no phone push and has no push code. The worker pings the sync's Healthchecks check
      only after a scheduled sync that is good and that nothing holds up for Dan: a sign-in, the
      consent list, a hold, the breaker, or a `private` it couldn't write.
    - Any lasting stop, whether a dead worker, a Plaud outage, a refused sign-in, a consent problem, a
      hold or the breaker, gives one Healthchecks email when the check's 2-hour grace runs out, and
      "up" when it works again. Nothing emails if the stop is fixed or heals within the 2 hours. That
      replaces decision 5's "at once" for a refused sign-in, the breaker and a consent stop.
    - Status always says why the check isn't being pinged.
    - Decision 8, one push with its reason, is deferred to the later change that adds ntfy for the
      sync. 2b builds one documented hook for that change, at the end of each sync turn. So, unlike
      decision 9's "no other change is needed", turning on phone pushes for the sync needs that small
      code change. The backup's 2a ntfy alerts are unchanged.
11. **The edit breaker: "Accept on Status"** (recommended).
    - When the breaker trips because Plaud added a field, Status names the field and offers **Accept
      these changes**, from any browser. Each recording then gets one new snapshot.
    - The button is offered only for fields that are the same on two fetches made a moment apart. If
      the same field trips the breaker again, the button goes, and Status says it needs a code fix.
      The breaker never needs the server.

_Decisions 10 and 11 change how decisions 4, 5, 8 and 9 are carried out. Where they differ, 10 and
11 hold._

12. **A consent-list ID split by a space or a line wrap: "Stop and tell me"** (recommended; asked
    2026-10-24, after the plan council found the gap). A line whose text before the dash is only hex
    digits (spaces, backticks and `of_` aside), 8 or more of them, and isn't one whole ID is a typo
    (§12.1, rule 4): downloads stop and Status names the line, as for any typo. An odd line such as
    `- 20261023 — asked` stops too, until it's reworded.

## 2. Your days with 2b

Each journey says what Dan does, what he sees, and what happens when it goes wrong. The Add page's
Plaud panel is the main spec's **Sync screen**, and this spec calls it that. 2b sends no phone push:
a stop reaches Dan as one email from the sync's Healthchecks check, with the reason on Status (J4).

### J1. A meeting, then triage

1. Dan records a meeting. Plaud's phone app uploads it, with a placeholder title (a timestamp).
2. At the next sync, at most 20 minutes after Plaud lists it, the worker downloads it, placeholder
   title and all (decision 1). It arrives whole or not at all, with Plaud's transcript and notes if
   they exist yet. Those that come later arrive at the following syncs: a new recording is checked at
   every sync for its first 2 hours, then hourly, until Plaud has finished with it (at most 48 hours).
3. If the Library is open, nothing moves. The list's header says "2 new · 1 private from Plaud ·
   Refresh", and rows move only when he presses Refresh (tagging part A's rule).
4. The recording is in **Untagged**, newest first, with Plaud's title and a time in the default zone
   (Plaud gives none). He opens it: the player, Plaud's transcript and names, and in Details,
   "Plaud: checked 14:20 · next by 14:40" and **Re-sync from Plaud**.
5. `T`, `work/lander`, Enter. Nothing is processed until stage 4.

**When it goes wrong:** the Sync screen says why a recording isn't here yet, in one line: "imports at
the next sync", "failed 3 times · Park", "held: see Status", "the disk is too full to import".
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
title. To confirm, he presses **Compare**. If it shows as here instead, the rename came too late:
the next sync tags it `private` and flags it (below). The word is forgiving: "NoArchive:", "No
archive" and "[noarchive]" all count, but "noarchived" doesn't. A phone text replacement makes it
one tap. Being forgiving, it can also catch a title Plaud's AI writes, such as "No archive access: IT
ticket". That recording sits under Kept out until Dan releases it, which errs safe.
- **Kept out stays kept out** (controller's call). If the title later loses the word (Plaud's AI
  retitles it after he asks for a summary, or autocorrect mangles it), it stays out. Only Dan lets it
  in, with **Release** on its row in the Sync screen. Release asks first, showing the recording's
  title on screen (nowhere else): "Let this recording in? It downloads at the next sync (14:40), and
  then stays in the backups." Until that sync, the row offers **Undo**. If the title still starts with
  the word, Release is refused, with the reason.
- **Too late.** If the sync already downloaded it (the rename came after the next sync), the sync tags
  it `private` at once, never fetches anything of it again, and flags it on Status, the Sync screen,
  Needs attention and the Library's header ("1 kept out too late"): "a kept-out recording was
  already archived", by its archive ID. He takes it out by hand (below), and soon, before the
  month's retained backup takes it for a year. If he decides to keep it after all, **Keep it here**
  clears the flag and changes nothing else: it stays `private`, and nothing more is fetched for it.
  Release doesn't apply to it, because it is already here.

**Never keep it, for good: the consent list.** Someone asks not to be recorded or kept. Dan adds the
recording's Plaud ID to the consent list, on the server: from the Mac, `make consent-add ID=…`
checks the ID and adds it, or he edits the file there. The Sync screen and Details show each ID with
a copy button. The list's IDs are never downloaded, and no title of theirs is ever stored or shown.
- **What the list means is unchanged:** a line that is an ID alone, or an ID then a dash and a note,
  keeps that recording out. An ID followed by other words ("ID kept: she agreed later") or inside a
  sentence names a recording Dan kept. `recordings plaud consent` lists those lines by number, so he
  can check each one.
- **A typo stops imports.** A line that looks like an ID but can't be read stops every import, and
  stops the sync's ping, so it emails if it isn't fixed within about 2 hours. Status gives the line
  number, never the line. Nothing is imported until the list
  reads cleanly, so a typo can't let a recording in.
- **Too late.** An ID listed after its recording arrived works like a late keep-out: `private` at
  once, never fetched again, and a flag naming the recording. It happens within seconds of
  the worker seeing the edited list, even if Plaud is down or the sign-in has lapsed, because it needs
  only the list and the archive. Every other recording keeps flowing.
- Status shows what the worker read: "Consent list: 14 IDs, 4 on Plaud now · file changed
  2026-10-23 09:12 · read 14:20". If an edit doesn't show there, the worker didn't see it.

**Deleting in Plaud** keeps a recording out only before the sync brings it in. After that, the
archive keeps it, never deleting anything on its own. Status and the Sync screen show it under
"Gone from Plaud", newest first, with a count of those gone in the last 7 days and a link to the
steps for taking a recording out (controller's call).

**Taking a recording out by hand** (one runbook section, used for every case above):
1. Put its Plaud ID on the consent list first, so no sync can bring it back. If it isn't flagged
   already, the worker flags it within seconds, which is expected.
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
   Plaud transcript with "Alex Kim", and "speakers" in the Sync screen's **Updated on Plaud** list.
   Details says when the recording was checked and when it will be next. If spike S5 finds that Plaud's listing marks edits, renames show
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

The sync's Healthchecks check is pinged only after a scheduled sync that is good and that nothing
holds up for Dan (decision 10). When the pings stop, Healthchecks waits out its 2-hour grace, then
emails him once: "recordings-sync is down". If the stop is fixed or heals within those 2 hours,
nothing emails. The email carries no reason. Status does: "The sync's Healthchecks check isn't being
pinged since 23:00: sign-in needed", with the next step below. "Up" follows once every cause has
cleared. One email, however many causes. Phone pushes can come later, with a small change (§16).

| What broke | Email | What Dan sees and does |
|---|---|---|
| Plaud refused to refresh the sign-in | about 2 hours after the sync stopped, unless fixed sooner | Status: "sign-in needed since 23:00 · new recordings wait on Plaud: nothing is lost". At the Mac: `make plaud-sign-in`. |
| Plaud is down, or the server is offline | the same (decision 5) | Status shows the error from the first failed run, with its reason: offline (check the network), Plaud unavailable or rate limited (it heals itself), Plaud refused access (check the subscription), or an error (a Claude session). |
| Every recording fails to fetch, though the listing works | the same | The same: a run with fetches and not one success is not a good sync. |
| The edit breaker trips (Plaud starts sending something new on many recordings) | the same | Status names the fields and offers **Accept these changes** (decision 11): each recording then gets one new snapshot. A field that differs between two fetches, or trips the breaker again after an Accept, gets no button: Status says it needs a code fix (a Claude session). New recordings, renames and `private` keep flowing. |
| Plaud's IDs look changed and recordings vanished, or many recordings vanish at once | the same | Status says which, and what to check. Imports stop until he presses **Accept this listing** on Status or the Sync screen, or until Plaud's next listing is back to normal. Accept is refused only when consent-listed or kept-out recordings vanished while Plaud's IDs look changed, because they could come back under new IDs. A change of form in which nothing vanished (the 2026-09-25 kind) holds nothing: Status's details note it. A few recordings vanishing settle by themselves once the next listing agrees. Privacy tagging never waits. |
| The consent list can't be read | the same | Status names the line numbers. He fixes the file; the next sync goes on. |
| A `private` the sync couldn't write | the same | Status names the recording. He runs `recordings reindex`. |
| The disk is too full to import | the same | Status: "the disk is too full to import". He frees some space. |
| The worker dies or hangs | about 2h20 after its last good sync | Status: "the Plaud worker is silent" or "may be stuck". `rc logs worker`. |
| A late keep-out, or a late consent listing | never: a flag (P15) | Already `private`. Status, the Sync screen, Needs attention and the Library's header show it, by ID. He takes it out by hand, or presses **Keep it here** on a late keep-out. |
| A recording may be a consent-listed or kept-out one back under a new ID (same start time and duration) | never: a flag | It is kept out, by ID, date and duration, with no title, until he confirms: `make consent-add` with the new ID, or **Release** if it is a different recording. |
| The sync's records were lost or rebuilt (a replaced `state.db`) | never: a flag | New recordings keep arriving. Anything on Plaud made before the rebuild that isn't here waits under **Not here yet**, "needs your review", for him to import or keep out, whenever it shows up. A recording he kept out before may be among them. |
| One recording fails every check, or the monthly Sync everything fails | never | Status: "1 recording fails every check · Re-sync · Park". It never hides "All here". A recording that fails to import holds "All here" back until he parks it. |
| The schedule was left off | about 2 hours later (the runbook pauses the check only when the sync is meant to stay off); Needs attention after 24 hours | Turn it back on. |

This is decision 4 as written, "pinged after every good run", with "good" meaning the sync is doing
its job (decision 10). Decision 8's one push with its reason waits for the later change that adds
ntfy.

Part II: §16.

### J5. "Is everything here?"

Status's sync panel answers it in order:
1. **One line, true or absent:** "All here · checked 14:20 · next sync 14:40". Under it: "Every
   recording on Plaud is in the archive or deliberately left out." It shows when the listing is
   fresh and nothing is waiting or missing (parked recordings aside). A recording that keeps failing
   its checks is a separate line beside it, and never hides it.
2. **Counts that add up:** "On Plaud 1,234 = here 1,227 + waiting 2 + excluded 4 + missing 1".
   **Waiting** will come in by itself. **Missing** won't without Dan (the schedule is off, imports
   are paused, the disk is too full, it failed 3 times, or it waits for review after a rebuild).
   **Excluded** is the consent list and the keep-out word.
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
- to press **Accept this listing** after a hold, once he has checked what Status says, or
  **Accept these changes** after the breaker trips;
- to import or keep out what waits for review after a rebuild;
- to confirm a fix after an email;
- for **Sync everything** while relabelling.

From top to bottom: the summary line; any flags (late keep-outs with **Keep it here**, late consent
listings, a `private` the sync couldn't write), any hold with its **Accept this listing**, and a
breaker trip with **Accept these changes**; **Sync now** and
**Compare**, with **Sync everything** under More; the equation; **Not here yet**, with checkboxes,
**Import selected** and **Import all 31**; **Kept out**; **Updated on Plaud**; **Gone from Plaud**,
newest first; and "Import your own audio: coming later". Importing never asks "are you sure?". A
toast says what came in: "31 imported · 4 arrived private · View". The panel keeps the last
result line, so a phone that slept mid-import still sees it. Every press gets an answer within a
second: "Queued", "Queued after the current sync (started 14:20)", or "The Plaud worker isn't
running (last reported 14:21)".

### J7. On the phone

- An alert is Healthchecks' email. Phone pushes for the sync come with a later change (§16).
- Status and the Sync screen work at 320 pixels: the summary first, full-width buttons, 44-pixel
  targets.
- **From the phone, Dan can:** rename in Plaud (the keep-out word, a private title), Sync now,
  import, Re-sync, Release (after its question), Park, Accept this listing, Accept these changes,
  Keep it here, and read Status.
- **He needs the Mac or the server to:** sign in again, edit the consent list, take a recording out
  by hand, run `recordings reindex`, or fix a breaker that needs code (a Claude session).

### J8. The first real sync, and when tagging starts

1. **Spikes, with Dan** (three sessions, about 2½ hours in all). They answer what only Plaud can: how
   the sign-in behaves, what the listing looks like, whether the `of_` hex equals the old IDs, and
   what an edit looks like. Each spike uses a throwaway recording made for it, or one Dan names in
   the session, never a consent-listed or private one. One question may follow: if signing in
   elsewhere signs the server out, who owns the sign-in (§18, S3).
2. **Deploy 2b.** The worker refuses scheduled syncs and imports until the first catch-up has run,
   whatever the config says (controller's call). Dan creates the sync's Healthchecks check once,
   emailing him. It is required (decision 9), and stays silent until its first ping. ntfy is
   optional, for later. Then `make plaud-sign-in`, and checks.
3. **The dry run** (Compare, or the CLI). Dan checks that both equations add up, that "here" equals
   the archive's Plaud recordings, that what's waiting is about the ~30 newer recordings plus the held
   titles, and that the consent list's IDs are found on Plaud in their new form. The new parser reads
   `of_` and upper-case IDs that 2a's skipped, so any recording 2a imported despite such a line is
   listed, by archive ID only. The worker tagged each `private` within seconds of deploy and flagged
   it on Status. Dan reviews each, and takes out by hand any that mustn't stay, before the next
   step's backup. If anything doesn't fit, he stops.
4. **The catch-up,** supervised on the server: a `pre-import` backup, the baseline, `recordings plaud
   sync --catch-up`, a validation, then a `post-import` backup. If it is interrupted, Status says so,
   and running it again picks up where it stopped.
5. **"You may start tagging now"** (decision 7). The runbook says it here, after the `post-import`
   backup. A recording that keeps failing doesn't hold it back.
6. **The schedule starts by itself** when the catch-up ends (controller's call): no config edit, no
   restart. Within 20 minutes Healthchecks shows the sync's check up. If it doesn't, the ping isn't
   arriving, and he stops. If "All here" doesn't show, Status says why.

Part II: §17.5, §18.

**Your year with 2b.** At the Mac or the server: signing in again when Plaud asks (spikes S1 to S3
decide how often, from never to about monthly); `make consent-add` when someone asks not to be kept;
a removal by hand for each keep-out that comes too late, which S9 may show is most of them;
`recordings reindex` if a `private` couldn't be written; and, if Plaud starts sending something that
changes by itself, a code fix with a Claude session (perhaps once or twice before the subscription
ends). Before 2027-08-15, DAN-15's last steps. Everything else is a press on Status or the Sync
screen, from any browser, or nothing.

## 3. How it should feel

| # | Principle | Where |
|---|---|---|
| P1 | **No silent failure.** Every way the flow can stop reaches Dan as the sync's Healthchecks email, or shows on Status. | §16 |
| P2 | **Honest status.** Each sentence is true now or absent. Counts add up. Figures carry their time. | §11.3, §15.2 |
| P3 | **One place to look.** The email gives no reason; Status is complete, and says why the check isn't being pinged. The Sync screen acts. | §15 |
| P4 | **Calm alerts.** One email when the sync has been stopped for about 2 hours, whatever the cause; one "up" when it works again. A blip, a one-time flag or a single recording never emails. | §16 |
| P5 | **Every alert has a next step,** in the runbook. | §16, §17.5 |
| P6 | **Every press gets an answer** within a second. | §9.3, §15.1 |
| P7 | **Dan's choice wins, and stays.** A removed `private` stays removed; a kept-out recording stays out. | §12, §13 |
| P8 | **Errs private and fails closed,** consent-list typos included. | §12.1 |
| P9 | **A window before the irreversible,** usable from the phone. | §12.2 |
| P10 | **Arrival is visible** in an open Library, and rows don't move until Refresh. | §15.3 |
| P11 | **Fresh by default, immediate on demand.** | §14 |
| P12 | **Plain sentences first, diagnostics folded away.** | §15.2 |
| P13 | **No titles outside the app's own views.** None ever for a consent-listed recording; for one kept out by title, or waiting for review after a rebuild, only inside Release's own question or its preview. | §10.1, §12, §15 |
| P14 | **The app removes chores.** | §10.5, §12.1 |
| P15 | **A privacy intent that didn't hold is flagged at once,** once per recording, on Status, the Sync screen, Needs attention and the Library's header: a late keep-out, a late consent listing. A `private` the sync couldn't write also stops the sync's ping until it lands. | §12, §13.2, §16 |

## 4. What 2b covers, and what waits

**In 2b** (main spec §20): the read-only Plaud client with the `of_` fix; the sign-in and its refresh;
a fake Plaud server for tests; the `worker` container; the compare and the import; the consent list,
the keep-out word and auto-private; edit-sync (the sweep, the breaker, the baseline, Re-sync of one
recording or a selection, Sync everything); the Add page and its Sync screen, Status's sync panel and
the arrivals cue; the sync's own Healthchecks check, its one alert; operations; and the spikes.

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
auto-private with the baseline's privacy part, the sync's check and the Sync screen. The second
half is the sweep, the breaker, Re-sync and Sync everything. Both deploy and roll back by their tag.
§20 says which criteria belong to which half.

## 5. Calls made for you

Each call was made without asking Dan. It is listed by what he will notice. "(controller's call)"
means the controller made it. Calls 1 and 22 Dan then decided himself, as decisions 10 and 11.

**Decided by Dan (decisions 10 and 11):**
1. **One alert rule.** The sync's Healthchecks check is pinged only after a scheduled sync that is
   good and that nothing holds up for Dan: a sign-in, the consent list, a hold, the breaker, or a
   `private` it couldn't write. Any other turn, and a dead or stuck worker, sends no ping. After the
   check's 2-hour grace, Healthchecks emails him once, and "up" when the pings return. Status always
   says why the check isn't being pinged. 2b sends no phone push: decision 8's push with its reason
   comes with the later change that adds ntfy (decision 10).

**Privacy and keeping out:**
2. **A recording kept out by its title stays out,** even if Plaud or autocorrect later removes the
   word. **Release** on the Sync screen lets it in, after asking once, with the title shown in that
   question only. (Controller's call.)
3. **Deleting in Plaud after the sync** leaves the recording here, shown under "Gone from Plaud",
   newest first, with a link to the removal steps. Nothing is deleted automatically. (Controller's
   call.)
4. **A late keep-out or a late consent listing** gets `private` at once, is never fetched again,
   and is flagged once per recording, with no email; the Library's header counts the flags. **Keep
   it here** clears a late keep-out's flag and changes nothing else. A late consent listing is
   tagged within seconds of the worker seeing the edited list, even while Plaud is down. The sync
   goes on for everything else.
5. **A consent-list typo stops every import** until the line reads, and stops the sync's ping;
   Status names the line number. The list's meanings are unchanged; only `of_` and upper-case IDs
   are newly read.
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
11. **After the sync's records are lost or rebuilt,** new recordings keep arriving. Anything on
    Plaud made before the rebuild that isn't here waits on the Sync screen, "needs your review",
    whenever it shows up, for Dan to import or keep out: a recording he kept out before may be among
    them (consent-listed ones are never shown). Nothing imports it by itself, Import all included.
    It is a flag on Status, with no email. A `state.db` restored from an older backup isn't
    detected, so after a restore the runbook marks everything made before it for review the same
    way. A `private` tag he removed can come back once, with the note "re-tagged
    private: the sync's record was rebuilt". This errs private. (Controller's call.)

**The first run and imports:**
12. **Nothing imports before the supervised catch-up** on the server, whatever the config says. Until
    then the Sync screen offers Compare only. When it ends, the schedule starts by itself.
    (Controller's call.)
13. **The catch-up holds the backups back** while it runs, between a `pre-import` and a `post-import`
    backup. After it, syncs never hold up a backup. (Controller's call.)
14. **Importing never asks "are you sure?".** A toast says what came in. (Controller's call.)
15. **A scheduled sync imports at most 20 recordings,** and the rest follow at the next one. A big
    backlog after a week away arrives in a few syncs, with no email.
16. **If Plaud's IDs look changed and recordings vanished with them, or many recordings vanish at
    once,** imports stop until Dan checks what Status says and presses **Accept this listing** (on
    Status or the Sync screen, or on the server), or until Plaud's next listing is back to normal.
    Accept is refused only when consent-listed or kept-out recordings vanished while Plaud's IDs
    look changed. A change of form with nothing vanished, the 2026-09-25 kind, holds nothing:
    Status's details note it. A new ID with the same start time and duration as a vanished
    consent-listed or kept-out one is treated as that recording until Dan confirms. (Controller's
    call.)
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
22. **The edit breaker is accepted on Status** (decision 11). For fields that are the same on two
    fetches, **Accept these changes**; if the same field trips it again, the button goes and Status
    says it needs a code fix. A release that changes the normaliser closes it by itself. It trips on
    data that changes between two fetches a moment apart, or on fields Plaud doesn't usually change,
    never on Dan's renames, however many.
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
4. **A refused sign-in stops the sync's ping,** so Healthchecks emails once if he doesn't sign in
   within about 2 hours, and signing in again brings the pings back. Proven with the fake server; on
   the real server, the check going green proves the ping.
5. **His decisions hold:** a placeholder-titled recording arrives at once and a later private title
   tags it; a keep-out title is never downloaded; a late one is tagged and flagged, and Keep it here
   clears the flag; Release asks first; a removed `private` stays removed.
6. **Nothing stops silently:** an outage, each blocker, a full disk and a dead worker stop the ping,
   so Healthchecks emails after its 2-hour grace, and a shorter blip sends nothing; Status says why;
   a rebuilt sync state keeps importing new recordings and holds the rest for review; a schedule
   left off shows in Needs attention.
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
   email and one `make plaud-sign-in`.
5. **Copies outlive a removal by hand,** as J2 lists. Only a NAS-side `restic rewrite` removes the
   `pre-import` and `post-import` copies.
6. **Alerts are late, and say only "down"** (decision 10): one Healthchecks email about 2 hours
   after the sync stops, with the reason on Status. While one cause keeps the check down, a later
   one, a dead worker included, sends nothing new; Status shows each. A late keep-out sends no
   email: it waits on Status and in the Library's header, already `private`. And Healthchecks' email
   is the single channel: if it doesn't reach him, only Status shows the problem.

The full list is §23.


# Part II: For the builder

Everything here serves Part I. Where they seem to differ, Part I says what Dan was promised, and Part
II is wrong.

## 8. Settled, and the calls Dan won't notice

**Settled by the council's evidence,** each applied where named:
- The worker opens its own `Writer`. "One writer" means one machine (main spec §3, §6.4) (§9.1).
- The UI asks the worker through request rows in `state.db`, over the guarded websocket (§9.3).
- `state.db` gains tables only, with no `SCHEMA_VERSION` bump (§9.5).
- The breaker counts over a rolling window, and the listing's hold backs it up (§14.4, §11.4).
- Only a refused refresh opens the sign-in blocker (§10.3).
- The static `RECORDINGS_PLAUD_TOKEN` and `recheck_days` go (§17.4).
- The consent parser accepts the `of_` form and any case (§12.1).
- The worker has its own dead-man secret, doctor role, `/index` mount and consent mount (§17).

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
| `worker` | new recordings from Plaud, snapshots, Plaud's outputs, Plaud's title, the automatic `private` tag, the Plaud tables, the sync's ping | writes any tag but `private`, writes the docs, or opens a port |
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
  5. Work out the gate's state: first time, or an interrupted catch-up. A rebuilt `state.db` is
     recorded and passed at once (§11.6).
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
  Status and doctor say why. `make deploy-smoke` and the first deploy rely on this. Late consent
  listings still apply at every tick whenever the list reads.
- **A `LockTimeout` on a recording is "busy":** the import or check is retried at the next run. It
  counts as neither a failure (§10.2's three strikes) nor an attempt (§11.7).
- It listens on no port.

### 9.3 Requests from the UI

- **The browser sends** an event input, `plaud_request`, mounted once in `App` with a `null` default,
  like `tag_request` (tagging part A §5). Only a person's action sends it: never on load, a URL, its
  hash or storage (main spec §15).
  ```json
  {"request_id": "<random>",
   "kind": "compare" | "sync" | "import" | "resync" | "full" | "accept" | "accept-changes"
           | "release-preview" | "release" | "unrelease" | "park" | "unpark" | "keep-here",
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
- **While the gate is closed** (before the first catch-up, or with it interrupted, §11.6), the
  worker refuses every kind but `compare` and `keep-here`, with "The first import is a supervised
  run on the server (runbook)".
- **The worker claims a row** (`UPDATE … WHERE state = 'queued'`) and checks it again against fresh
  facts: the newest listing, the consent list as it is now, the keep-out words and the patterns. An
  `import` row causes downloads and archive writes, and is safe only because of this re-check. A
  `release` is refused if its ID is consent-listed, or if any enforced word matches its current
  title, and the reply says which (§12.2). An `accept` is refused only on §11.4's terms:
  consent-listed or kept-out IDs that the last good listing held are missing from this one, and the
  listing shows a census change or under 80% overlap. An `import` with `all` skips IDs that need
  review after a rebuild (§11.6); only named IDs bring one in. An `accept-changes` is refused unless
  the breaker is open and every field it covers is still eligible (§14.4). A `keep-here` needs an
  open late keep-out flag.
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
| `plaud_listing` | one row per Plaud ID: the ID as listed, its canonical form, first and last seen, kept-out state (by a word, or as a re-keyed match, §11.4) and release (§12.2), "needs your review" after a rebuild (§11.6), and, **only while its recording is here**, a SHA-256 of its title, its duration and any change marker S5 finds. A kept-out or consent-listed ID has no title hash, ever | worker | both |
| `plaud_checks` | per recording: last checked, last changed, next check, interval, consecutive failures, settled, baseline pending, held removals, parked, degraded, flagged | worker | both |
| `plaud_check_log` | one row per check: time, recording, result, parts changed, parts found volatile. Kept 7 days | worker | both |
| `plaud_private_checked` | the marker (§13.2): per recording and Plaud ID, a SHA-256 of the title last checked, and the version of the pattern list it passed | every `Writer` that reconciles | the same |
| `plaud_events` | what Dan sees: imported, arrived private, updated (with parts), re-tagged private (with why), kept out late, consent-listed late, couldn't tag private, kept here, gone from Plaud, released. Kept 90 days; open flags until resolved | worker | web |
| `plaud_state` | one row per key: the last good listing (its ID-set hash, census and time), holds (with the held listing's absent set), the breaker (its `window_start`, the normaliser version it tripped under, and the fields accepted), the sign-in state, `failing_since` and its cause, `last_ping_at` and `last_ping_ok` (§16), `last_started` and `last_ended`, `catch_up_done_at`, the snapshot mark rows and `rebuilt_at` (§11.6), the keep-out words enforced, the auto-private pattern list by version, Sync everything's progress and pace | worker | both |

- **Keyed by canonical ID.** `plaud_listing` is keyed by `canonical_plaud_id`, never by the ID as
  listed, which is a column updated at each listing; kept-out state, release, review marks, first
  and last seen and the late flags follow the canonical ID, and so does every Plaud ID another table
  keys on. So a change of form carries every state over.
- **No `SCHEMA_VERSION` bump.** An older release's `State.open` ignores unknown tables, so `make
  rollback` keeps working. A test asserts `SCHEMA_VERSION == 1` and `MIGRATIONS == {}`, saying why.
- **Reused:** `ops_runs` (jobs `plaud-sync`, `plaud-full`, `plaud-catch-up`) and `meta`
  (`worker_heartbeat`). The `alerts` table stays 2a's, untouched (§16).
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
  - the newest compare's rows: titles for recordings not yet here and not kept out, and the date,
    time and duration for the rest (so no kept-out title, and none for a recording that needs
    review after a rebuild, apart from Release's preview below);
  - **never a title of a consent-listed recording,** and no title at all for any recording not here
    during a consent stop ("titles hidden until the consent list reads"), or while a listing is held
    with a census change or under 80% overlap ("titles hidden while Plaud's IDs look changed"),
    since a consent-listed or kept-out recording could then sit among them under a new ID (§11.4);
  - a consent-listed or kept-out ID's date, time and duration, kept until it has been absent for 30
    days, for the re-key match (§11.4 step 6);
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
  - `worker.py`: the loop, the heartbeat thread and `conclude_turn`; `plaud/health.py`:
    `sync_health` (§16).
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
  | `recordings plaud breaker reset` | §14.4; a tool only, which no runbook step needs | `plaud` |
  | `recordings plaud park <id>… [--undo]` | §14.6 | none |
  | `recordings plaud release <plaud-id> [--undo]` | §12.2's Release, without its on-screen question: the CLI shows ID, date and duration, and asks y/N | none |
  | `recordings plaud keep-out-words [--drop <word>]` | lists or drops enforced words (§12.2) | none |
  | `recordings plaud review` | after restoring an older `state.db`: records `rebuilt_at` as now, so everything made before it that isn't here needs review (§11.6) | `plaud` |
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

A **scheduled turn** comes due every `schedule_minutes` (§9.2). Every turn, and every light turn
inside a long run, ends in `conclude_turn` (§16), which pings only after a good sync that nothing
holds up. A turn ends in one of these ways:
1. **The gate is closed** (before the first catch-up, §11.6): no Plaud call, nothing written, and no
   ping; the check has never been pinged, so it stays silent. Late consent listings still apply at
   every tick (§12.1).
2. **The sign-in is paused** (§10.3), or **a rate limit's wait is still running** (§10.2): no Plaud
   call, no ping. Late consent listings have already been applied at the tick (§12.1).
3. **`plaud` is held** by another run: skipped as busy, with no ping. A long run serves the turn's
   light part itself (§9.2), and that light turn can ping. A wedged holder serves none, so the pings
   stop.
4. **Otherwise it runs** (§11.1), whatever it then meets: a consent stop, a hold, the breaker, a
   failure. It pings only if the run was good and no blocker is open (§16).

With `schedule_minutes = 0` no turn comes due, and nothing pings, so the check emails after its
grace unless the runbook paused it because the sync is meant to stay off (§17.5).

## 10. The Plaud client and the token store

### 10.1 The client: read-only, and leaks nothing

- **A copy, with edits.**
  - **Source:** `audio-router`'s `src/audio_router/archive/sources/plaud.py` (282 lines; Dan's own
    code). It holds `PlaudClient`, `API_BASE = "https://platform.plaud.ai/developer/api"`,
    `IncompleteDownload`, `AuthError` and `stamp_meta`. It already uses stdlib `urllib`, pages the
    listing, fetches `files/{id}`, inlines blocks behind links, and downloads audio.
  - **Record:** its header names the `audio-router` commit it was copied from (7254bae is the newest
    commit touching that file today; the builder records the one actually copied) and the MIT
    relicensing (main spec §19).
  - **Edits:** the `of_` fix (below); refresh through the token store (§10.3), replacing its call to
    the vendor CLI; a read-only request function; and the leak-proof rules below. Among them, its
    `_data_link_error` holds a fixed error class, never `str(exc)`, which can carry a URL.
  - **Size, for planning:** about 300 lines of client, plus its tests.
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
    answer, any 4xx except 408 and 429): `PlaudAuthError`. The run stops and opens the `sign-in`
    blocker (§16). Scheduled runs make no Plaud call until the token file changes (by inode, size
    and time). A **Sync now**, or `recordings plaud check`, tries once anyway, and a success clears
    the pause.
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
and the next good run pings the sync's check again. No phone sign-in: the vendor's login needs its
client secret and a callback on `localhost` (main spec §9.1).

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

## 11. A sync run: the listing, the compare and the import

### 11.1 One run, step by step

The same steps serve a scheduled run, **Sync now**, and the CLI:

```
take the plaud lock                      → held: busy, skip
read the consent list, fail closed       → unreadable: consent stop, no fetch or download (§12.1)
apply late consent listings              (§12.1; also at every tick, with no Plaud call)
check the sign-in, refresh if due        → refused: stop, sign-in blocker (§10.3)
list every page                          → bad, partial or complete (§11.2)
census of ID forms; field names
classify every listed ID                 (§11.3)
privacy from the listing alone           (§13.1, cases 2 and 3)
mark listing changes for a check now     (§14.2)
the hold                                 → a hold stops imports and the sweep (§11.4)
import waiting recordings, up to the cap (§11.5)
a chunk of the sweep                     (§14.2)
the breaker                              (§14.4)
record the run; judge it good or not     (§11.7)
release the lock; conclude_turn          (§9.7, §16)
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
   auto-import off, a hold (§11.4), the disk guard, review after a rebuild (§11.6), or it failed 3
   times (or is parked).

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

### 11.4 The listing hold

One hold stops imports, and one action clears it. A small shrink settles by itself. Each step below
is a separate piece of work, and the thresholds are constants, not settings.

1. **Measure each complete listing against the last good one:**
   - **newly absent:** recordings here that the last good listing had, and this one doesn't (by any of
     their Plaud IDs); and, counted apart, consent-listed and kept-out IDs that it had;
   - **a census change:** a form appears that the last good listing didn't have, or an ID present in
     both listings (by canonical form) is now in a different form. Shares of each form are not
     compared, so a backlog of new recordings in a form the last listing already had never trips it;
   - **an unreadable form:** a listed ID in a form the consent parser can't read (§12.1);
   - **overlap:** the share of the last good listing's IDs present now.

   **A listing with no last good one** (2b's first, which is the catch-up's, or the first after a
   rebuild) is measured against the archive instead: its IDs, through `canonical_plaud_id`, against
   the Plaud IDs the archive already holds (from 2a). Only an unreadable form or under 80% overlap
   can hold it then; the shrinks need a previous listing, and the runbook's dry-run checks (§17.5)
   stand in for them.
2. **It is held** when any of these is true:
   - under 80% overlap (the `of_` lesson, aimed at the consent list as well as the archive);
   - an unreadable form;
   - **a large shrink:** more than 5, or 5% of those here (whichever is larger), newly absent;
   - **a census change with something newly absent** in the same listing: a recording here, a
     consent-listed ID or a kept-out ID.

   Otherwise it is **good**:
   - **A census change with nothing newly absent** is a line in Status's details ("Plaud's IDs
     changed form on 2026-11-02; every ID still matches"), and its census becomes the baseline. So
     the 2026-09-25 change, replayed against 2b with the same hex, holds nothing.
   - **A small shrink,** from 1 up to the large shrink's limit newly absent, holds back only the
     marking of those recordings as gone (§12.3). Imports and the sweep go on.
3. **Effects of a hold:** nothing is imported (scheduled, Sync screen or CLI), nothing is marked
   gone, and the sweep pauses; privacy from the listing and late consent listings still run. It
   opens the `hold` blocker (§16). **Sync now** answers "Held: see Status", and the import buttons
   are disabled, with the hold's line. **While the listing shows a census change or under 80%
   overlap, no title of a recording not here is stored or shown** (§9.5): a consent-listed or
   kept-out recording may be among them under a new ID.
4. **Clearing a hold takes one action: Accept this listing.** It is a button on Status and on the
   Sync screen (request `accept`), and `recordings plaud accept-listing` on the server. It records
   this listing as the last good one, marks the newly absent recordings gone, and lets imports
   resume.
   - **It is refused only if a consent-listed or kept-out recording could be imported.** That is the
     case when consent-listed or kept-out IDs that the last good listing held are missing from this
     one **and** the listing shows a census change or under 80% overlap: those recordings may be
     listed again under new IDs that neither the list nor the kept-out records can name. Status then
     says "Fix this first: 4 consent-listed or kept-out IDs vanished while Plaud's IDs changed
     (runbook)". One that Plaud simply no longer lists, with neither signal, never blocks Accept; if
     it comes back under a new ID, step 6 catches it. §9.3, §20 and §23 use these terms, and no
     other. **For an unreadable form, Accept is refused too**, since every later listing would hold
     again: "needs a code fix: the consent list can't name these IDs".
   - A hold also clears by itself when a later listing no longer meets its condition (a fix release,
     or Plaud putting things back).
   - **Status says what to check, and offers Accept:**
     - IDs changed: "Plaud sync paused: Plaud's IDs changed form, and 12 recordings here vanished
       with them. Check the runbook's steps, then Accept this listing."
     - a large shrink: "Plaud now lists 40 fewer of the recordings here (and 38 look new). If you
       deleted them in Plaud, Accept this listing: they show as gone from Plaud."
5. **A small shrink settles by itself** when the next good listing, at least an hour later, is
   missing the same recordings (the same absent set), whatever else changed. If the absent set
   changes, the wait starts again. It is a line among Status's numbers, never in Needs attention:
   there is nothing for Dan to do.
6. **A re-keyed recording,** in any listing, held or not. When an ID neither the archive nor
   `plaud_listing` has seen appears while a consent-listed or kept-out ID is absent, the worker
   compares its start time and duration with each absent one's (from the cache, §9.5), before any
   title of the new ID is stored. **Within 1 second on both** is a match:
   - **One match:** the new ID is that recording. It is excluded, never fetched or imported, and its
     title is never stored or shown. Status and the Sync screen flag it, by ID, date and duration:
     "may be a consent-listed (or kept-out) recording back under a new ID: confirm". Dan confirms
     with `make consent-add` and the new ID (a kept-out match simply stays kept out), or, if it is a
     different recording, presses **Release**, whose question then shows no title.
   - **More than one candidate, either way:** the new ID is held the same way until Dan decides.

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
   snapshot as `audio-router` did, with its `_meta` block:
   - the time fields `stamp_meta` wrote (`recorded_at_utc`, `recorded_at_local`, `stamp`, `timezone`,
     `time_source`), worked out as it did (Plaud's start time, else its creation time, in the
     default zone), plus `tool: "recordings"`, which §11.6 uses;
   - never `data_link_errors`, which `link_errors` reads (`normalise.py:183-186`) and which would mark
     the snapshot incomplete for every release.

   **How `_meta` is read today:** the normaliser drops it from everything it hashes (`DROPPED_KEYS`,
   at every depth, `normalise.py:37-39`), so it changes no hash. Two readers look inside it:
   `link_errors` (`_meta.data_link_errors`) and `meta_fetched_at` (`_meta.fetched_at`, which the 2a
   import uses to order legacy snapshots). Nothing in this repo reads the time fields: 2a took each
   recording's time from `audio-router`'s catalog, and 2b takes it from Plaud's listing (step 8), so
   they stay a record, and nothing that reads the time can break. Every snapshot 2b writes, a check's
   included (§14.1), carries the stamp.
4. **Tags:** `private` if any title (the listing's or the envelope's) matches `auto_private_patterns`
   (§13.1). It goes in `Incoming.tags`, landing in the same rename as `recording.json`, before any
   source or output (main spec §7.4).
5. **The disk:** below `stop_free_percent`, or when twice the audio's `Content-Length` would leave less
   than that margin (`Writer.add` copies the download and hashes it again, `writer.py:649-654`),
   imports stop for this run, and the rest become missing ("the disk is too full to import"). The
   run isn't good (§11.7).
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
  Compare runs. Every request or command that would write a snapshot (import, Re-sync, Sync
  everything) is refused while the gate is closed. Late consent listings, which only add `private`
  or a flag, still apply. Status says "Scheduled sync waits for the first catch-up (runbook)"; this
  isn't a failure, and the check, never pinged, stays silent.
- **The schedule starts by itself (controller's call).** `schedule_minutes` is 20 in the template
  and when unset, so the turn after `--catch-up` ends runs as a sync, with no config edit or
  restart. 0 still turns the schedule off, for maintenance and DAN-15. The gate is the only thing
  between a fresh deploy and an import, and §20 criterion 5's tests guard it.
- **The snapshot mark.** Before every 2b snapshot is published, by every path (a scheduled run, a
  request, the CLI, the catch-up), the worker commits a mark row in `plaud_state`, in its own
  transaction. A crash after the mark and before the snapshot leaves a mark with no snapshot, which
  is harmless.
- **The gate's states,** checked at start (§9.2):
  - **First time:** no 2b snapshot in the archive (none has `_meta.tool` = `recordings`), and no
    `catch_up_done_at`. The gate is closed until `--catch-up` finishes.
  - **Interrupted first catch-up:** mark rows exist, but `catch_up_done_at` is unset. The gate stays
    closed, and Status says "The first catch-up didn't finish: run `--catch-up` again". Dan is at
    the server for it.
  - **Rebuilt `state.db`** (controller's call): the archive holds 2b-stamped snapshots, but
    `plaud_state` has **no mark rows** at all. The worker records `rebuilt_at`, sets
    `catch_up_done_at` and commits a mark row, in one transaction, so a restart doesn't find the
    rebuild again. Nothing stops. **Needs your review** applies to every listed ID that isn't here
    or excluded and whose Plaud creation time is earlier than `rebuilt_at`, whenever it is first
    listed: in the first listing after the rebuild, or later (restored from Plaud's trash, or missed
    through page drift or a malformed row). It is marked in `plaud_listing`. A scheduled run, **Sync
    now**, the CLI's `sync`, Sync everything and **Import all** skip it ("Import all 31 · 6 more
    need your review"); it waits in **Not here yet**, by date, time and duration (its title only on
    demand, through Release's preview, §12.2), until **Import selected** brings it in or the consent
    list keeps it out. It counts as missing, so "All here" waits and says why. A recording created
    after the rebuild flows as usual. A flag on Status and in Needs attention ("The sync's records
    were rebuilt: 6 recordings wait for your review") lasts until the list is empty; no email,
    because the sync isn't stopped. **Why the protection holds:** only a recording made before the
    rebuild could have been kept out before it, and every one of them waits for Dan, whenever it
    appears.
  - **Not detected:** a `state.db` restored from an older backup still has mark rows, so it passes.
    A keep-out recorded after that backup is then forgotten. That is inherent to detecting with no
    new storage (§23 risk 12). So the runbook's restore section runs `recordings plaud review`,
    which records `rebuilt_at` as now: everything made before it that isn't here waits for review,
    as above.
- **Which runs advance which timestamps:**
  - `last_started` and `last_ended`: scheduled turns that run (§9.7 item 4), and the light turns
    inside long runs. Only scheduling reads them.
  - the snapshot mark: before every 2b snapshot, by every path, and when a rebuild is recorded.
  - `catch_up_done_at`: only `--catch-up` ending without a run-level failure, or a rebuild being
    recorded.
  - the last good listing: a good listing in a scheduled run, Sync now, the CLI's `sync`, or
    `--catch-up`; and Accept. Compare never moves it.
  - `failing_since`: scheduled turns and light turns only (§11.7).
- **`recordings plaud sync --catch-up`** is a supervised CLI run. It holds `ops` and `plaud` (§9.4),
  and has no import cap, because Dan has just read the dry run.
  - **Its holds:** the listing hold and the consent list still apply. Its listing has no last good
    one, so it is measured against the Plaud IDs already in the archive (§11.4 step 1). If it is held,
    it stops and says so; after the runbook's check, `recordings plaud accept-listing` (the CLI only:
    the UI's Accept stays refused before the first catch-up) records that listing as the baseline,
    and `--catch-up` is run again.
  - **Its listing becomes the last good listing,** so the first scheduled sync has a baseline for
    the hold.
  - **It is idempotent:** it imports only what isn't here, so an interrupted one is simply run again.
    It sets `catch_up_done_at` when it ends without a run-level failure.
  - **It is refused** once `catch_up_done_at` is set ("already done on 2026-10-25; use `recordings
    plaud sync`").
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
  10. The schedule has started by itself since step 6. Within 20 minutes Healthchecks shows the
      sync's check up; if it doesn't, stop: the ping isn't arriving. If "All here" doesn't show,
      Status says why. A turn may run during steps 7 and 8: it adds no tag, and what it imports is
      in the next hourly backup.
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
- **Not a success:** a check that ends degraded (§14.1), and an import whose audio can't be fetched.
  Each is an attempt without success, so "every link refused" makes runs not good, and the pings
  stop.
- **A full disk:** a run in which the disk guard refused an import (§11.5 step 5) isn't good, with
  the cause "the disk is too full to import". So the pings stop, and Healthchecks emails after its
  grace; decision 10's blockers stay as they are.
- **Not attempts:** parked recordings, which aren't tried at all; a busy recording (§9.2); and a
  recording absent from the newest listing but not yet gone, which has no listed ID to fetch with
  (§14.1), so it is skipped.
- **`failing_since`** is set at the first run that isn't good, and cleared by recovery. It is a
  Status figure and the streak marker. Decision 5's 2 hours are the check's grace (§16), not app
  code.
- **Recovery, by cause.** `plaud_state` keeps why the streak began.
  - A streak that began with no good listing (Plaud down, offline) clears at the next good run.
  - A streak that began with the disk guard clears at the first run it doesn't refuse.
  - A streak in which attempts failed with no success clears only at a good run **with at least one
    successful import, check or fetch**. Only then does the ping resume.
  - A good run with no attempts leaves a work streak as it is: it neither sets nor clears it.
  So a systemic failure can't flip the check up and down as its recordings back off.
- **A lone recording that always fails never stops the ping** (P4). A run that retries a recording
  on the failing list also checks at least one recording that isn't on it: the most overdue
  recording the sweep covers (§14.2), so never a kept-out, consent-listed, gone or parked one. Its
  outcome tells the recording's failure from the sync's. Parking stops even the retries.
- **Accepted limits of that companion check:** it covers only recordings already on the failing
  list. A recording's first three failures, in a run with no other attempt, can still start a
  streak; in a small, quiet archive that may stop the ping. And if 2b ships in halves, the first
  half has no sweep, so no companion: a lone failing import then stops the ping until it is parked.

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
  1. **An exclusion:** after optional spaces, an optional bullet (`-` or `*`, then any spaces) and an
     optional backtick, a Plaud ID, then an optional backtick and any spaces, then **a dash** (`—`, `–`
     or `-`, with a note after it if Dan likes) **or the end of the line.** The ID may be in any hex
     form `canonical_plaud_id` reads: 32 hex digits, bare or with `of_`, in any case. So
     `` - `of_3fa9…` — asked not to keep `` and a bare ID alone on its line are exclusions.
  2. **A kept line:** an ID in that position followed by anything other than a dash or the end, such
     as `3fa9… kept: she agreed later` or `- 3fa9… (fine to keep)`. That includes a full 32-hex ID
     followed in the same word by a character that is neither hex nor a dash (`3fa9…:` or `3fa9…,`),
     as today. It names a recording Dan kept, as `audio-router`'s rule intends, and is **not** an
     exclusion. Neither is an ID inside a sentence.
  3. **A comment:** a line starting `#`. Ignored.
  4. **A typo:** a line whose first word (after the bullet and backtick) holds 24 or more hex digits,
     with or without `of_`, but isn't a valid ID (31 or 33 hex digits, or a non-hex character among
     them); or (decision 12) a line whose text before the dash, or the whole line when it has no
     dash, is only hex digits once spaces, backticks and `of_` are set aside, 8 or more of them, and
     isn't one whole ID (an ID split by a space, or wrapped over two lines). It fails closed (P8):
     the list is unreadable.

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
  - it opens the `consent` blocker (§16);
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
  - **One flag per recording** (P15): one that already has an open privacy flag, or is gone from
    Plaud, has its flag changed to "On the consent list, but in the archive", never a second. No
    email: the recording is already `private`. The Library's header counts it (§15.3).
  - **If Dan removes the line to keep the recording,** the flag clears at the next tick, `private`
    stays, and checks resume, because it is no longer consent-listed.
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
- **No title of a consent-listed recording not in the archive is ever stored or shown:** not in the
  cache, the Sync screen, `state.db`, logs or alerts, at any time. It appears by ID, date and
  duration only. (One already in the archive, a late listing, keeps its title in `recording.json`
  and its merge-base revisions until it is taken out by hand; it is never shown outside the app.)
  The worker drops its title from the listing before anything else reads the row.
- **ID forms:** the parser reads every hex form `canonical_plaud_id` folds. A listed ID in another
  form holds the listing (§11.4), because the list couldn't name it. A consent-listed recording
  listed again under new hex in the same form is caught by its start time and duration (§11.4
  step 6).

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
- **Kept out:** `plaud_listing` records when, and by which word, under the canonical ID (§9.5). The
  recording is never fetched and never imported. The sweep skips it, and Re-sync answers "Not
  checked: kept out". One listed again under new hex is caught by its start time and duration (§11.4
  step 6).
- **Kept out stays kept out (controller's call).** If the title later loses the word (Plaud's AI
  retitles it, autocorrect mangles it, Dan edits it), it stays out, shown as "was kept out by title".
  Only **Release** lets it in, at the next sync.
- **Release asks first** (controller's call), because it turns "never download" into a copy kept for
  good, like removing a last `private` tag in tagging part A. It is the fourth exception to main
  spec §12.2's "no confirmation dialogs".
  1. Pressing **Release** on a row sends a `release-preview`. The worker answers with the recording's
     current title, from its in-memory copy of the newest listing (or a fresh listing), through the
     cache (§9.5). The title is never written anywhere else, and the preview row is deleted once
     answered or after 10 minutes. The preview is refused, with no title fetched, if the ID is
     consent-listed, during a consent stop, or while the gate is closed.
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
  - a flag: "Kept out by title, but already in the archive: take it out by hand (runbook)", one per
    recording (P15), with no email; the Library's header counts it (§15.3).

  Release doesn't apply to it: it is already here. The flag clears when the recording leaves the
  archive, or when Dan presses **Keep it here** (request `keep-here`, on the Sync screen and in
  Details). That clears the flag and its Needs attention line and changes no data: it stays
  `private`, because the sync never removes it, and nothing more is fetched from Plaud for it. A
  later title that still carries the word doesn't flag it again.
- **The baseline** (§14.5) checks the keep-out words on every title of 2a's recordings too.
- **What Dan should expect,** written in the README: type the word first (a phone text replacement
  helps); the rename must reach Plaud before the next sync. S9 measures whether a title can be set
  before the listing shows the recording. If it can't, the README says the word usually arrives late,
  and what late means. The README also says the match is forgiving, so an ordinary title such as "No
  archive access: IT ticket" can be kept out too; it waits under Kept out until Dan releases it.
- **After a lost `state.db`,** the record of what was kept out, and of older words, is gone (§11.6).
  Such a recording waits in the rebuild's review list (§11.6), for Dan to decide. The consent list
  is the durable way to keep a recording out.

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
   sync tags it `private`, never fetches it again, and can never bring it back. If it isn't flagged
   already, it gets a flag, which is expected.
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
3. **A late keep-out word** (decision 2): `private` at once, and §12.2's flag.
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
  edited outside the app: run `recordings reindex`". It opens the `private-unwritten` blocker (§16)
  until the tag lands. A lock timeout alone is retried at the next run, well within the check's
  grace.
- **A→B→A** re-checks A, because the marker holds only the newest title. That errs private.
- **Who judges:** every `Writer` that reconciles, in any container. `Writer.reconcile_plaud` passes
  reconcile the recording's markers from `state.db` and records the new ones, so a `reindex` in `web`
  follows the same rule as the worker. Nothing falls back to 2a's `title_seen` rule, which would
  re-add a tag Dan removed. This retires `title_seen` for privacy.
- **After `state.db` is lost,** the worker seeds each recording's marker for its primary Plaud ID
  from `plaud.title_seen`, when that equals the ID's newest title. Every other recording gets case 4,
  with its note. Nothing stops (§11.6).
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
     A recording with none (absent from the newest listing, not yet gone) is skipped, and isn't an
     attempt.
  2. Normalise it, and compare it with the newest complete snapshot, recomputing both sides (main
     spec §9.1.1).
  3. If it changed, write a new snapshot (`Writer.add_snapshot`).
  4. **Always end with `reconcile_plaud`,** which is idempotent and writes nothing when the outputs
     are up to date. So a crash between a snapshot and its reconcile, or a reconcile that raised, is
     repaired by the next check, even when the envelope hasn't changed since.

  It downloads no audio: audio is fetched once, at import.
- **Its result:** "up to date"; "updated", with the parts that changed (title, transcript, speakers,
  notes, Dan's own notes, other); "degraded", when link errors persist after one re-fetch (it counts
  as checked for Sync everything's completeness, but never as a success for the good-run rule,
  §11.7; the snapshot is kept and never used as a baseline); "suppressed" (§14.4); or "failed",
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
- **What Dan sees:** the `breaker` blocker (§16), so the check emails if the breaker is still open
  after its grace; and on Status: "Edit-sync paused since 14:20: Plaud started sending something new
  on 12 recordings (`folder_id`, `share_state`). New recordings still arrive." Or, for a volatile
  part: "…: 12 recordings' transcripts change between two fetches a moment apart." Field and part
  names only, never values.
- **Accept these changes** (decision 11), on Status and the Sync screen (request `accept-changes`),
  for fields that counted only in "other" and were the same on both fetches. It does what `baseline
  --all` then `breaker reset` do: each recording's next check is excused, `window_start` moves to
  now, and each recording gets one new snapshot over the next pass. The fields accepted go in
  `plaud_state`, the press in `ops_runs`. Its sentence: "If Plaud added a feature, accept it: each
  recording gets one new snapshot. If a name looks like a link or a key, don't: it needs a code fix
  (a Claude session)." **No button** for a part that differs between two fetches, or a field that
  trips the breaker again after an Accept: "This keeps changing by itself: it needs a code fix".
- **A release that bumps `NORMALISER_VERSION` closes the breaker by itself** at start (the trip
  records the version it saw). Status then says "closed by the release on 2026-11-02; watching for
  a re-trip", since a release that didn't drop the field brings "up", then red again.
  `breaker reset` stays as a tool no runbook step needs.
- **While it is open** Status adds: "While this is open, the sync's check stays down, so a new
  problem won't send another email: glance at Status until it's fixed."
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
  - `baseline --all` (a rebaseline): after a normaliser change Dan accepts. **Accept these changes**
    does the same for a breaker trip (§14.4).
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
  - **A failure** (a run-level one, or a recording not already on the failing list) shows on
    Status's Sync everything line. It never stops the ping (§16).
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
     late consent listing, "take it out by hand (runbook)", and a late keep-out also **Keep it
     here**; a `private` the sync couldn't write, "run `recordings reindex`".
  3. **A hold,** if one is on: its sentence (§11.4) and **Accept this listing**, or the reason Accept
     is refused. **A breaker trip:** its sentence and **Accept these changes**, or why it needs a
     code fix (§14.4).
  4. **Sync now** and **Compare**, with **Sync everything** (and its estimate) under **More**.
  5. **The equations** (§11.3), each number a link to its list, "from the listing at 14:20", and the
     consent-list line.
  6. **Not here yet** (waiting and missing): a checkbox, the date, time and duration, the title (a
     lock and "arrives private" when it will), and the state: "imports at the next sync", "schedule
     off", "auto-import off", "held: see Status", "the disk is too full to import", "failed 3 times
     · Park", "waits for the first catch-up", "needs your review: the sync's records were rebuilt"
     (by date, time and duration; its title on demand, §11.6). **Copy ID** copies the whole ID in
     the list's own form (`` - `of_…` — ``). **Import selected**, and **Import all 31** with its
     count, which leaves review rows out ("· 6 more need your review"). During a consent stop, or a
     hold while Plaud's IDs look changed, no titles (§9.5).
  7. **Kept out:** by the consent list, by title, as a possible re-keyed match (§11.4 step 6, to
     confirm), and "was kept out by title" with **Release**. Each
     with the ID (and Copy ID), the date, time and duration. Never a title, except inside Release's
     own question (§12.2). A released row reads "Released · comes in at the next sync (14:40) · Undo".
  8. **Updated on Plaud (7 days):** the recording, what changed (title, transcript, speakers, notes,
     removal held, re-tagged private), and when.
  9. **Gone from Plaud,** newest first, with "gone since" and "N in the last 7 days", and the link to
     "Taking a recording out by hand"; **Parked**, with Unpark.
  10. **Import your own audio:** coming later (main spec §9.2).
- **While the gate is closed,** only **Compare** is enabled. The others say the gate's reason
  (§11.6).
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
  - An error shows from the first failed run, though Healthchecks emails only after about 2 hours.
  - From `sync_health()` (§16), whenever the last ping is older than one schedule interval plus 10
    minutes: "The sync's Healthchecks check isn't being pinged since 14:20: sign-in needed. It
    emails you about 2 hours after that." The first cause that applies fills it: a blocker, failing,
    the disk too full to import, the schedule off, idle on a configuration fault (a bad
    `keep_out_word`), a rate-limit wait (until when), busy (the holder, since when), the gate, or
    the worker silent.
  - Or the condition, by name:
    - "Plaud sign-in needed since 23:00. New recordings wait on Plaud: nothing is lost. At the Mac:
      `make plaud-sign-in`."
    - "Plaud sync failing since 12:00 (Plaud unavailable). Last good listing 11:40."
    - the breaker (§14.4), with **Accept these changes**, or why it needs a code fix
    - "Plaud imports stopped: the consent list has 2 lines that look like IDs but can't be read
      (lines 14 and 22)."
    - "Recording 20261023T140512-0700_3fa91c2e is on the consent list, but in the archive. Take it
      out by hand (runbook)."
    - "Recording 20261023T140512-0700_3fa91c2e was kept out by title after it came in. Take it out
      by hand (runbook), or Keep it here."
    - "Plaud imports stopped: the disk is too full to import."
    - a hold: §11.4's sentence for it, with **Accept this listing**, or the reason Accept is refused
    - "Scheduled sync waits for the first catch-up (runbook)."
    - "The sync's records were rebuilt: 6 recordings wait for your review on the Sync screen."
      (§11.6)
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
  - "Healthchecks: last pinged 14:40", or "last ping failed 14:40 (HTTP 404)"
- **Details, folded away:** the normaliser's version; the field names added or gone since the last
  good run; the census of ID forms, and a change of form that held nothing ("Plaud's IDs changed
  form on 2026-11-02; every ID still matches"); changes per part in 24 hours; the share of named
  segments; the last 20 runs with their results; the keep-out words enforced; malformed listing
  rows.
- **Needs attention gains:**
  - the sync failing for more than 2 hours
  - imports stopped: the disk is too full to import
  - a possible re-keyed consent-listed or kept-out recording, to confirm (§11.4 step 6)
  - a sign-in needed, or one expiring within 7 days
  - the breaker, or any hold
  - a consent stop, a late consent listing, a late keep-out
  - a recording that should be private but couldn't be tagged
  - recordings waiting for review after a rebuild (§11.6)
  - the worker silent, or without progress (§9.2)
  - a `plaud` holder older than 2 hours (12 for Sync everything and the catch-up): "may be stuck"
  - the schedule off for 24 hours after the catch-up
  - "The sync's Healthchecks ping has failed for 2 hours" (§16)
- **"No sync has run yet"** when a table isn't there (§9.5).
- **It redraws** every 60 seconds (main spec §12.5), and when a request finishes.
- **No titles anywhere on Status.** Recordings are named by ID (P13).

### 15.3 The Library and Details

- **Arrivals** (P10): the Refresh button in the list's header reads "2 new · 1 private · 2 updated
  from Plaud · Refresh", beside tagging's "5 changed" (`HeadNotes.tsx`). At 320 pixels it shortens to
  "5 from Plaud · Refresh". The counts come from `plaud_events` (imported, arrived private, updated)
  since the page loaded, read every 60 seconds. Rows don't move until Refresh. The Tags grid's header
  has the same cue. It also counts open privacy flags, by count only: "1 kept out too late · 1
  consent-listed in the archive · See Status", and the Library's Needs attention lists them by
  archive ID (main spec §12.1). No title, in this or any alert.
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
pixels, rows stacked (date and duration on one line, title below).

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
  `<ExceptionClass>`", "paused: unexpected changes", "held: Plaud's IDs changed", "held: many gone
  from Plaud", "waiting: Plaud lists 3 fewer", "imports stopped: consent list", "imports stopped:
  the disk is too full to import", "gate closed", "partial: 3 recordings failed".
- **A blocker** (§16; a later push's title): "sign-in needed", "imports stopped: consent list",
  "held: see Status", "paused: unexpected changes", "should be private: run `recordings reindex`".
- **A recording:** "up to date", "updated: speakers, title", "degraded", "suppressed", "busy",
  "failed: Plaud unavailable", "not checked: kept out", "not checked: on the consent list", "gone from
  Plaud", "parked", "re-tagged private: title changed", "re-tagged private: the sync's record was
  rebuilt".
- **A reply:** "Queued", "Queued after the current sync (started 14:20)", "Already queued", "Resuming
  (started 14:20)", "waiting for Sync everything (412 of 1,234)", "Sync everything ran less than an
  hour ago", "The first import is a supervised run on the server (runbook)", "Held: see Status",
  "Accept refused: fix this first (runbook)", "Accepted: each recording gets one new snapshot",
  "Kept here: it stays private, and nothing more is fetched from Plaud for it", "Release refused:
  its title still starts with `noarchive`", "Release refused: it is on the consent list", "Released ·
  comes in at the next sync (14:40) · Undo", "The Plaud worker isn't running (last reported 14:21):
  your request runs when it's back, if that's within the hour."

## 16. Alerts

**One rule** (decision 10): the sync's Healthchecks check is pinged only after a scheduled sync that
is good and that nothing holds up for Dan. Its 2-hour grace does the rest: any lasting stop, a dead
or wedged worker included, brings one email about 2 hours in, and "up" at the next ping. This is
main spec §14's "each good sync … pings it", with "good" meaning the sync is doing its job. 2b's
worker sends no push.

**`worker.conclude_turn(ctx, ran_good)`** ends every scheduled turn and every light turn inside a
long run (§9.7). It pings `deadman_sync_url` (the plain URL, an empty body, never `/start` or
`/fail`) only when the turn ran a sync and it was good (§11.7), no failing streak is open (so a
quiet run can't flip the check up during a systemic failure), and no blocker is open. A turn stopped
by the gate, a sign-in pause, a rate-limit wait or a busy lock sends nothing; so does any error
while deciding. The answer is worked out afresh each turn from `sync_health()`, with no alert state
of its own, so a restart or a rebuilt `state.db` can't drop an alert or send one twice. It records
`last_ping_at` and `last_ping_ok` in `plaud_state`.

**The blockers, a closed list.** "Good" alone doesn't see them (a consent-stopped run with no
attempts, or working imports with the breaker open, can be good), so each stops the ping:

| Blocker | Opens when | Clears when | Status gives the step |
|---|---|---|---|
| `sign-in` | Plaud refuses a refresh (§10.3) | a good run after the token file changes | `make plaud-sign-in` |
| `consent` | a consent stop (§12.1) | a run that reads the list cleanly | fix the lines named |
| `hold` | the listing hold (§11.4) | Accept, or a later listing that no longer meets it | Status's check, then Accept |
| `breaker` | the breaker trips (§14.4) | Accept these changes, a release that closes it, or `breaker reset` | Accept these changes, or a code fix |
| `private-unwritten` | a `private` the sync owes fails to land (§13.2) | the tag lands | `recordings reindex` |

**Never a blocker:** late keep-outs and late consent listings (already `private`, and flagged once
per recording); a rebuild's review list (§11.6); failing or parked recordings (P4); a failed Sync
everything; a small shrink; auto-import off. A full disk isn't a blocker either, but a run whose
import the disk guard refused isn't good (§11.7), so it stops the ping too. Before the first
catch-up no turn pings, and a check never pinged stays silent.

**`plaud/health.py`: `sync_health(state)`** reads `plaud_state` only: the open blockers (key and
since), `failing_since` with its cause, and the other reasons a turn didn't ping (the schedule off,
idle on a configuration fault, a rate-limit wait, busy, the gate). Status and `conclude_turn` both
call it, so what Status says is what stopped the ping (§15.2); Status adds "the worker silent" from
the heartbeat (§9.2).

**The check:** period 20 minutes, grace 2 hours (any `schedule_minutes` up to an hour fits), email
to Dan, "manual resume" off. "Down" comes about 2h20 after the last good sync. The backup's check
keeps main spec §14's 3 hours (§19).

**Code.** `alerts.py` gains only `deadman_sync_url` (from `RECORDINGS_DEADMAN_SYNC_URL`, `worker`
only, checked by `_check_url` under its own name; a bad one makes `from_config` raise, a problem in
doctor) and a `ping_deadman` that takes the URL. 2a's alert code is untouched: `ops.py`'s `_alert`,
`_known`, `_remember`, `_conclude`, `TITLES` and the backup's pings, and `alerts.send`, which the
worker never calls. The backup's ntfy alerts work as in 2a whenever Dan sets them up. `alert-test`
pings its container's check with `/log`, and in `backup` sends a push if ntfy is set up.

**ntfy later: the one hook.** `conclude_turn`'s docstring says it is the one place the sync tells
the outside how it is, and that pushes go there. 2b builds no stub, setting or ntfy secret for it.
The later change, about one task, mounts the ntfy secrets in `worker` (with doctor's check and
`alert-test`'s push), and adds inside `conclude_turn`: a push per blocker at onset, titled with its
§15.6 phrase and "See Status"; one when `failing_since` turns 2 hours old; one "works again"; one
per new flag in `plaud_events` past a stored rowid; reminders only if Dan wants them. Sent state
reuses 2a's `alerts` table under `plaud:<key>` names. It then chooses between Healthchecks as a
second, reasonless channel and decision 8's rule (ping also while every open blocker's push was
delivered), one condition, spelt out in §16 of this spec at commit fab3d9d. Its tests use
`FakeNtfy`.

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
- **Secrets:** `deadman_sync_url` only. The ntfy secrets arrive with the later change (§16). No
  restic, NAS, `known_hosts` or `deadman_url` secret.
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
- **For `worker`:** skip `_backup`, `_mirror`, `_ssh` and the restic part of `_secrets`; check
  `deadman_sync_url`, and report `deadman_set` from it. A missing or bad one is a problem, naming
  `RECORDINGS_DEADMAN_SYNC_URL_FILE`: the sync's check is its only alert. There is no "alerts are
  off" warning for `worker`, which sends no push by design. The other roles' `_alerts` is unchanged.
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
  - a missing or bad `deadman_sync_url`.

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
  stays healthy and that doctor reports both (and the missing `deadman_sync_url`).
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
  schedule_minutes = 20              # minutes between syncs, from the first catch-up on; 0 is off
  auto_import = true                 # import new recordings at each sync (nothing runs until tagged)
  full_sweep_days = 1                # check every recording against Plaud within this many days:
                                     # 1 while relabelling speakers in Plaud, then 7 (the default)
  keep_out_word = "noarchive"        # a Plaud title starting with this word is never downloaded
  auto_private_patterns = ["^\\s*private", "interview:"]
  consent_list = "/consent/consent-list.md"   # inside the consent folder; never a file mount
  token_store = "/plaud/tokens.json" # the Plaud sign-in (worker only; `make plaud-sign-in`)
  # api_base = "https://…"           # Plaud's API: the copied client's by default; tests set a fake
  ```
- **Unset means:** `schedule_minutes` 20, `auto_import` true, `full_sweep_days` 7, `keep_out_word`
  `noarchive`, `consent_list` none (a consent stop), `token_store` `/plaud/tokens.json`, `api_base`
  the copied client's host.
- `recheck_days` goes: the rolling sweep replaced its hot window (main spec §9.1.1).
- **Secrets:** `RECORDINGS_PLAUD_TOKEN` leaves `config.py`'s `SECRETS`, the template and the README.
  `RECORDINGS_DEADMAN_SYNC_URL` (stage 2b) joins them, with the backup URL's rules. The template's
  `schedule_minutes` stays 20, as 2a shipped it.
- **`docker/deploy.example.conf`** gains `RECORDINGS_PLAUD_HOST` and `RECORDINGS_CONSENT_HOST` (both
  folders), and the test that every compose variable is documented covers them.
- **`[backup] extra_paths`'s template comment** names the consent folder and `/state/manifests`.
- **The settings test** (CLAUDE.md, "Settings"): every new key gets its row in
  `test_template_settings.py`, with its reader. `api_base` is commented out in the template, so its
  row sets it explicitly. `schedule_minutes` and `auto_import` leave `LATER`, the first with its
  unset value of 20 tested; `recheck_days` leaves with its key; `plaud_token` leaves
  `LATER_SECRETS`; `deadman_sync_url` gets a `SECRET_VALUES` row.
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
  4. **Required** (decision 9): create the sync's Healthchecks check: period 20 minutes, grace 2
     hours, emailing Dan, "manual resume" off (the default). Its email is the sync's only alert. Put
     its URL in the secrets folder as `deadman_sync_url`. No pause is needed: a check never pinged
     stays silent, and nothing pings before the catch-up. **ntfy is optional, for later** (below).
  5. Leave `schedule_minutes` at 20 (the gate holds every sync until the catch-up), then `make
     deploy`.
  6. On the Mac: write `local.mk` (`SERVER`, `REPO_DIR`), then `make plaud-sign-in`. On the server:
     `rc exec worker recordings plaud check`.
  7. `rc exec worker recordings doctor --role worker`, `rc exec worker recordings plaud consent`, and
     `recordings alert-test` in `backup` and in `worker`. In `worker` it pings `/log`, which proves
     the URL; the email channel is the account and integration the backup's check already proved.
- **7. The dry run:** `rc exec worker recordings plaud sync --dry-run --list`, or **Compare**. Check:
  - both equations add up;
  - "here" equals the archive's Plaud recordings, and anything absent is explained (recordings Dan
    deleted in Plaud);
  - waiting and missing are about the ~30 newer recordings, and "of which N arrive private" matches the
    held titles;
  - "Consent list: N IDs, M on Plaud now" is what Dan expects, and no hold is shown. If the
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
  (decision 7). The schedule has started by itself. **Within 20 minutes, Healthchecks shows the
  sync's check up. If it doesn't, stop: the ping isn't arriving.** Then watch Status.
- **"Plaud sign-in":** when, `make plaud-sign-in`, what it prints, and `plaud check`.
- **"Keep a recording out for good":** `make consent-add ID=…` from the Mac, or editing the file on the
  server (write a new file and rename it). Then `recordings plaud consent` shows the new count.
- **"Taking a recording out by hand":** §12.4, word for word.
- **"When the Plaud alert comes":** one section. "Healthchecks: recordings-sync is down. Open
  Status: its line 'isn't being pinged since …' names the cause, and §16's blocker table gives the
  step. If Status says the worker is silent or may be stuck: `rc ps`, `rc logs worker`."
- **Optional:** Healthchecks › Account Settings › Email Reports, a daily reminder while any check is
  down. It covers the backup's check too.
- **Recovery:** the breaker and Accept these changes, and when it needs a code fix instead (§14.4);
  a hold and Accept this listing, with what to check first, and what to do when Accept is refused
  (§11.4); a consent stop; parking.
- **"After a lost or restored `state.db`":** restore `state.db` from the newest backup rather than
  rebuild it, if one exists.
  - **A rebuilt one** (no backup used) has no snapshot marks, so the worker records the rebuild and
    goes on (§11.6). On the Sync screen, decide about every recording marked "needs your review" (a
    recording kept out before may be among them): put it on the consent list, or import it.
  - **A restored one** still has marks, so it isn't detected, and the schedule goes on. So: `rc stop
    worker` before restoring; after it, `rc run --rm worker recordings plaud review`, so everything
    made before now that isn't here waits for review (§11.6); then `rc start worker`, and decide
    about each on the Sync screen.
- **Section 2 step 2 (2a's alerts):** its ntfy.sh bullet, its token bullet and the `ntfy_topic` line
  are marked optional, for later (decision 9); the Healthchecks bullet stays required, and so does
  the sync's check (section 6 step 4). The expected file list and the checklist's "ntfy topic and
  ping URL" row say "the ntfy topic, if set up".
- **"Setting up ntfy, later":** the backup's pushes work as soon as section 2 step 2's ntfy parts
  are done. The sync's pushes need the later change of §16. Until then 2a's low-disk warning reaches
  no one; a full disk reaches Dan through the sync's check (§11.7).
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
- **Planned work doesn't pause the sync's check** (re-imports, stopping the worker, setting the
  schedule to 0 for a while). If it lasts more than 2 hours, the check's email is the reminder to
  turn the schedule back on, and "up" follows at the first good sync. Only rolling back across 2b
  and DAN-15's end pause or delete the check, because the sync is then meant to stay off. Re-imports
  stop `worker` as they stop `backup`, and section 3's `IMPORT_MOUNTS`
  (`first-run.md:717`) mounts the consent **folder** (`-v "$RECORDINGS_HOME/consent:/consent:ro"`),
  not the old file path, which no longer exists once the list has moved.
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
- The old token rejected: a crash between reply and write costs a sign-in. No `.prev`.
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
> Plaud skills on the Mac most weeks. Each time one signs in, the server's sync stops, and Status
> says "Plaud sign-in needed". What do you expect?
> - **A. The server owns the sign-in (Recommended).** You stop signing in to Plaud's tools on the
>   Mac. Class notes and Plaud lookups read from the archive and its mirror, which hold every
>   recording with Plaud's transcript and notes. Until those skills are changed, they don't work.
> - **B. The Mac owns it, and the server borrows it.** The server uses a copy of the Mac CLI's
>   sign-in. It works only if refresh tokens don't rotate (S2); if they do, whichever side refreshes
>   first signs the other out.
> - **C. Take turns.** Each time you use a Mac Plaud tool, the server stops until you run `make
>   plaud-sign-in`, and an email whenever you take longer than 2 hours.
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
  `files/{id}` answer? While a regenerated note is still in progress, it is also fetched twice a
  moment apart, to see whether §14.4's double fetch would call the note volatile.
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
- **§6.1, the layout:** `source/plaud-….json`'s "raw source payload, exactly as received" becomes
  "raw source payload, exactly as received, plus the fetcher's `_meta` stamp and its own `_`-prefixed
  fields, which are dropped before hashing" (§11.5 step 3).
- **§5:**
  - Line 220's "mounted read-only into both containers" becomes "every container".
  - The `[plaud]` keys of (§17.4).
  - **The token's location:** "`tokens.json` in its own host folder (`RECORDINGS_PLAUD_HOST`,
    `/srv/recordings/plaud`, 0700), mounted read-write in `worker` only, at `/plaud`; never backed up
    or mirrored". Settles §5 and §6.8 against `config.py:33` and the shared `/state`.
  - Secrets gain the sync's dead-man URL; `deploy.env` gains `RECORDINGS_PLAUD_HOST` and
    `RECORDINGS_CONSENT_HOST` (a folder); doctor's roles gain `worker` (§17.2), for which a missing
    dead-man URL is a problem, with no alerts-off warning.
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
    §14.5); after it, what wasn't here at the first listing waits for Dan's review, and new
    recordings flow (§11.6).
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
    shown; a late listing is tagged at the worker's next tick without a listing, and flagged once
    per recording (§12.1). Settles the single-file mount and `consent.py:17`.
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
  - the breaker's window, volatility found by fetching twice, suppression, **Accept these changes**
    and closing on a normaliser release (§14.4); the hold and **Accept this listing**, and a small
    shrink settling by itself (§11.4); the good-run rule (§11.7); the listing's rules (§11.2); the
    sweep's intervals and cap (§14.2); every check ending with reconcile (§14.1); the baseline
    (§14.5); Re-sync of a selection, parking and Sync everything's outcomes (§14.6); removals counted
    until 3a (§14.7)
  - "A 401 stops the run and raises an alert" becomes a refused refresh, which stops the sync's
    ping (§10.3, §16)
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
  - "They fire on: a Plaud 401, a circuit-breaker trip, … and a failed `--full`" loses its Plaud
    items, and gains "The Plaud sync's alert is its own Healthchecks check (stage 2b §16)".
  - "A turn skipped because another job holds the `ops` lock … is neither a failure nor an alert"
    adds the `plaud` lock.
  - **The dead-man's switch** becomes two checks: the backup's, unchanged, and the sync's, pinged
    only after a good sync that nothing holds up (decisions 4 and 10, §16). Neither shares the
    other's URL. Settles one check for two jobs.
  - **The grace:** "The grace is at least 3 hours, because long jobs … run in the same loop" becomes
    the backup check's rule. The sync's check has a period of 20 minutes and a grace of 2 hours,
    which is decision 5's 2 hours (§16).
  - **Pushes** (decisions 9 and 10): "Alerts … are push notifications through ntfy.sh" gains "for
    the backup. In stage 2b the Plaud sync sends no push: its alert is its own Healthchecks check,
    and Status says why". "The doctor warns, without failing, when … no dead-man's switch [is] set
    up" gains "except the worker's, which is a problem (stage 2b §17.2)".
- **§15:** the worker publishes no port and adds no route; only `worker` holds the token; egress
  isn't blocked (§17.1); "Containers: both run as non-root" becomes all three.
- **§15.1:** "not … the Plaud token store" stays, now about its own folder. The consent folder and
  `/state/manifests` are backed up through `extra_paths`.
- **§16:** gains (§21)'s tests.
- **§17 and §17.1:** no worker in either demo; the panels are canned (§15.5).
- **§19:**
  - Step 5: (§18)'s spikes, the token's and the consent list's folders, and the sync's check
    (required; ntfy optional, for later).
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
  - Decision 20's "with a dead-man's switch" becomes "with two dead-man's checks", and it gains
    "the Plaud sync's alert is its own check; its pushes come with a later change (Dan, 2026-10-23,
    stage 2b decisions 9 and 10)".
  - Decision 31 gains the token's location.
  - Decision 35's "A private tag is always applied before any content is written" gains (§13.3)'s
    exception.

**Other files:**
- **`packages/core/src/recordings/format/FORMAT.md`** (the archive's own docs are edited there):
  - line 36's "`source/`: raw payloads exactly as each source returned them" gains "plus the
    fetcher's `_meta` stamp and its own `_`-prefixed fields, which are dropped before hashing";
  - lines 61-64's auto-private paragraph (the patterns run "whenever a Plaud title isn't the one in
    `plaud.title_seen`") becomes §13.1's rule: once for each new Plaud title, and for a new pattern.
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
2. **A speaker rename shows within the sweep window, and the breaker stays out of the way** (second
   half).
   - **Fake, with an injected clock:** at N = 1,000 with 40 renames at once, each shows within
     `full_sweep_days` plus one schedule interval, and at once with Re-sync.
   - **Real:** S9's clip; after the catch-up, one rename at once with Re-sync, and one within a day
     and one sync without it.
   - **Fake:** three speaker renames 20 minutes apart on 12 recordings never trip the breaker; a field
     that changes on every fetch does, with no Accept button. A new field that is the same on both
     fetches trips it with **Accept these changes**: pressed, each recording gets one new snapshot
     and the breaker closes; the same field tripping it again offers no button. A release with a new
     normaliser version closes it at start.
3. **Sync everything exits 0** (second half).
   - **Fake:** a sound account exits 0 and writes the manifest; a degraded recording still exits 0; a
     flagged one (consent-listed or kept out late) still exits 0 and is named; an unparked failing
     recording exits 2 with its count; killed part-way, it resumes.
   - **Real:** it exits 0, or exits 2 only for recordings Dan then parks, each checked in Plaud's app
     and recorded in the runbook's table by ID hash.
4. **A refused sign-in raises an alert** (first half), replacing "a 401 raises an alert".
   - **Fake:** an expired access token that refreshes keeps the pings going; a 401 then a refused
     refresh stops them from that turn on, with no Plaud call until the token changes; a new token,
     then a good turn, pings again; a 429 from the token endpoint is an outage, not `sign-in`; a 401
     after a good refresh goes through the current-user read.
   - **Real:** the check goes green within 20 minutes of the catch-up (runbook section 8).
5. **Dan's decisions hold** (first half), on the fake:
   - A placeholder-titled recording arrives at the next run, untagged; renamed private, it is tagged
     at the next run even when its fetch fails (decision 1).
   - A keep-out title is never requested (the request log). A late one gets `private` and one flag,
     and nothing of it is fetched again even when its transcript appears; **Keep it here** clears
     the flag and changes no file. A title that loses the word stays kept out until **Release**;
     Release shows its question, and is refused while the title still carries the word or the ID is
     consent-listed (decision 2).
   - A consent-list typo stops every import and the ping; a bare `ID kept: …` line is not an
     exclusion, and is listed for review; a late consent listing is tagged at the next tick with the
     sign-in dead, flagged, never fetched again, and the run goes on; adding an already-flagged
     recording makes no second flag; a list replaced by rename is read at the next tick; during a
     consent stop, and during a hold with an ID change, the cache and the Sync screen hold no title
     of a recording not here.
   - A removed `private` stays removed through 100 runs and a `reindex` in a `web`-role process, and
     comes back only when the title changes to one that matches, with its note (decision 3).
   - The gate: with `schedule_minutes` 20 from deploy, before the first catch-up a scheduled turn
     makes no Plaud call, and an import request is refused; an interrupted catch-up says so and can
     be run again; the turn after `--catch-up` ends runs a sync. While the gate is closed, every
     request that would write a snapshot is refused. A SIGTERM mid-run, a Re-sync then a restart and
     a catch-up then a restart never read as "rebuilt".
   - A rebuilt `state.db` (2b snapshots, no marks): recordings created after it keep importing; one
     created before it that isn't here needs review, whether it is in the first listing or appears
     later (absent from that listing, then back); a scheduled run, Sync now, the CLI's `sync`, Sync
     everything and Import all leave it out, and Import selected brings it in; its ID on the consent
     list keeps it out; a restart after the rebuild doesn't find it again. `plaud review` after a
     restore marks the same way.
   - **Re-keyed in the same form:** one consent-listed ID listed again under new hex, with no hold,
     is never requested and shows no title; 10% re-keyed is a large shrink, and Accept then imports
     none of the matched IDs; a kept-out one likewise; two candidates hold the new ID.
   - **A form flip with nothing absent** keeps every kept-out ID out, every review mark and every
     consent exclusion (the tables are keyed by canonical ID).
6. **Nothing stops silently** (first half; the breaker's part, second half):
   - **The ping rule, as a table test** against a fake Healthchecks: good or not, a streak or not,
     each blocker, and busy, the gate, a sign-in pause or a rate-limit wait. Each case pings or
     doesn't. Every blocker stops the ping even with a good run (a consent stop with no attempts;
     the breaker with working imports); privacy flags, a rebuild's review list, a failing recording
     and a failed Sync everything never do.
   - Every fetch failing while the listing works, or every link refusing (403), means no pings; once
     every recording is on the failing list, runs that retry only them stay not good, and pings
     resume only when a fetch succeeds. An outage of 1 hour stops the pings, which then resume.
     Status shows it from the first failed run, with "isn't being pinged since …: <cause>".
   - A hold clears with Accept this listing, which is refused only when consent-listed or kept-out
     IDs vanish while Plaud's IDs look changed; a change of form with nothing absent holds nothing;
     a small shrink settles when the next listing an hour later is missing the same recordings.
   - A wedged `plaud` holder: no pings. A ping that fails shows "last ping failed" on Status.
   - A run whose import the disk guard refused doesn't ping, and Status says "the disk is too full
     to import"; the pings resume at the first run it doesn't refuse.
   - "Isn't being pinged" names the cause for the schedule off, a bad `keep_out_word` and a long
     rate-limit wait, as well as for each blocker.
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
    between bare and `of_`, the hex changing with the audio unchanged, some IDs re-keyed in the same
    form
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
  - the late consent listing: applied with the sign-in dead and the gate closed; no second flag for
    an already-flagged recording or one gone from Plaud; the cache and Sync screen hold no title of a
    recording not here during a consent stop, or a hold with an ID change
  - the keep-out match: each example in §12.2, a fullwidth `ｐｒｉｖａｔｅ`, an earlier word, and
    `keep-out-words --drop`
  - Release: the preview's title lives only in the cache and is deleted after the answer; refused
    while the title carries a word, or the ID is consent-listed; Undo before the next sync
  - Keep it here: clears a late keep-out's flag and its Needs attention line; `private` stays, and
    nothing is fetched again; a later title with the word doesn't flag it again
  - the re-key match: within 1 second on start time and duration, one match, none and two; the match
    runs before any title of the new ID reaches the cache
  - the Library's header counts late keep-outs and late consent listings, with no title
  - the marker: due and not due; a failed `mutate` retried after `reindex`; a new pattern, checked
    against that pattern only; a rebuilt `state.db`, with its note; a hash merge under a new ID; a
    hash merge into a flagged recording, discarded
  - the gate's states: the snapshot mark written before every 2b snapshot, on every path; an
    interrupted catch-up (marks, no `catch_up_done_at`) re-run idempotently; a rebuilt `state.db`
    (2b snapshots, no marks) recorded with a mark in one transaction, and its review rule;
    import, Re-sync and Sync everything refused while the gate is closed; `--catch-up` refused once
    done; the catch-up's listing becoming the last good one; a catch-up held against the archive's
    2a IDs
  - Accept's refusal: refused when consent-listed or kept-out IDs vanish with a census change or
    under 80% overlap; allowed when one is simply gone from Plaud, and for a large shrink without
    those signals
  - Release's preview refused for a consent-listed ID, during a consent stop, and with the gate closed
  - the buckets, as a property (§20, criterion 1); the hold's conditions (§11.4), the census
    measured over shared IDs, a census change with nothing absent passing as a details line,
    `accept-listing` and Accept's refusal; a small shrink settling on the same absent set; a
    40-recording backlog importing over 2 runs with no hold
  - the sweep's intervals and cap, with an injectable clock (`ops.Context.clock`'s pattern), at N =
    1,000; the breaker's window, the double fetch, suppression (no interval reset), Accept these
    changes (offered only for stable fields, gone after a second trip), the normaliser-version
    close, and reset
  - the good-run rule: failing-list recordings as attempts; recovery by cause; the companion check
    beside a failing recording; `LockTimeout` as busy; `failing_since`
  - `sync_health` and `conclude_turn`: each blocker's opening and clearing; an unreadable `state.db`
    sends no ping; `last_ping_at` and `last_ping_ok`; Status's line from the same answer. "Down
    after the grace" is Healthchecks' behaviour, so it isn't simulated: the tests check what is sent
  - the token store: atomic writes and modes; one refresh across two processes; a crash between
    reply and write: with rotation and no grace, the next start finds the old file, its refresh is
    refused, and the `sign-in` blocker opens; with a grace, it recovers
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
  for N ticks on a fake clock, and `--full`. It checks files through `Archive`, the Status view and
  pings through a fake Healthchecks (none while a sign-in refused mid-flow is open, then again after
  the sign-in), and scans every log for tokens, `X-Amz-` and titles. The worker sends no push.
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
  - A hold disables the import buttons; Accept this listing clears it. A breaker trip offers
    Accept these changes, which clears it.
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
the hold's thresholds (§11.4), and the re-key match's 1 second; the sweep's intervals (§14.2); the
breaker's (§14.4); the Healthchecks period and grace (§16).

**To check during implementation** (checked when first used, as in tagging part A §11):
- `urllib`'s `add_unredirected_header`, and a refusing opener like `alerts.py`'s.
- WAL for `index.db` on its named volume, opened by `web` and `worker`.
- `docker compose up --wait` honours the worker's health check.
- Healthchecks, beyond what its docs say (checked 2026-10-23: `/log` leaves the state alone, and a
  paused check resumes at its next ping unless "manual resume" is on): that a check never pinged
  (`new`) never goes down, and that the email integration sends "up" as well as "down".
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
   before each recording; the listing hold. What remains: a recording not marked in time is in the
   backups and the mirror until taken out by hand, and the `pre-import` and `post-import` snapshots
   keep whatever the catch-up imported unless a NAS-side `restic rewrite` removes it. A late
   keep-out sends no email: it is already `private`, and Status and the Library's header show it.
   Taking it out soon matters: a removal before the month's retained snapshot keeps it out of the
   12-month copies.
2. **The keep-out word may not beat the first sync** (S9 measures it). If it usually can't, the late
   path is the main path, and the README must say so.
3. **The flow stops, and nobody notices.** Mitigations: the good-run rule and its streak; a check
   pinged only after a good sync that nothing holds up, so an outage, every blocker and a dead
   worker stop it; Status's "isn't being pinged since …"; "Scheduled sync is off" in Needs
   attention. What remains: Healthchecks' email is the only channel, about 2 hours late and without
   its reason; one open cause masks a later one, a dead worker included, until it clears (Status
   still shows each); a check never pinged never alerts (doctor, `alert-test`'s `/log`, "last ping
   failed" and the runbook's "green within 20 minutes" cover it); a bug in the good-run rule is a
   bug in alerting (the ping's table test). The backup's alerts are 2a's, unchanged.
4. **A late private title sits untagged in the backups and the mirror** for one sync (decision 1's
   exception).
5. **The forgiving keep-out match can keep an ordinary title out,** such as "No archive access: IT
   ticket". That errs safe: the recording waits under Kept out, and Release brings it in.
6. **The sign-in model fails on contact** (S1 to S3). §18 says where to stop and ask Dan.
7. **The token chain is lost** in a rotating refresh. The cost is one sign-in.
8. **Duplicates, or a void consent list, after an ID-form change.** The listing hold stops imports
   when Plaud's IDs change and recordings vanish with them, and Accept is refused only when
   consent-listed or kept-out IDs vanished with that change (§11.4); S7 checks the hex first. A
   change of form with nothing vanished passes unseen, because the tables follow the canonical ID. A
   re-key in the same form shows no signal, so a consent-listed or kept-out recording that comes
   back under new hex is recognised by its start time and duration (§11.4 step 6). Two recordings
   started within the same second with the same duration would hold, not import.
9. **A volatile field mints snapshots** before the breaker trips: up to about 5% of a day's checks.
   S8, the volatility check and the double fetch catch it early.
10. **Rate limits, or a flag on the account.** Pacing, no parallel calls, a budget, and
    `schedule_minutes = 0` as the off switch.
11. **Tag clicks get "busy"** when the worker's hold passes 2 seconds. Locks only around local work,
    and tests that bound the holds.
12. **Losing `state.db`** loses the sync's memory: its markers, what was kept out by a word now
    gone from the title, and the keep-out words used before. New recordings keep flowing, and
    anything made before the rebuild that isn't here waits for Dan's review whenever it appears
    (§11.6), so a formerly kept-out recording can't come in without him. A removed `private` can
    come back once, with its note. A `state.db` restored from an older backup still has snapshot
    marks, so it isn't detected: a keep-out recorded after that backup is forgotten unless Dan runs
    the runbook's restore step, `recordings plaud review`. The consent list, in its
    own backed-up folder, is the durable way to keep a recording out.
13. **A wrong time zone is permanent in an ID.** S12 settles it before the catch-up.
14. **The subscription lapses on 2027-08-29.** DAN-15's runbook step turns the schedule off after the
    final Sync everything.
15. **The relabelling evening at scale.** With thousands of recordings, the budget may stretch a full
    pass past a day. Status shows the real oldest check; Re-sync of a selection is the way round it.
16. **The Library's first load** may pass 2 seconds as recordings arrive, pulling a 3a item forward.
17. **The schedule starts by itself after the catch-up,** so the gate alone stands between a fresh
    deploy and an import (§20 criterion 5 tests it).
18. **A careless Accept these changes** costs about one snapshot per recording, with no privacy
    cost. A field carrying a link would be kept in each, so Status says not to accept one that looks
    like a link or a key.

## Scenarios

One row per testable bullet of §20, by criterion (`C<criterion>.<nn>`; the spikes own S1 to S14).
Where a bullet has a part Phase 1 can prove without Plaud, that part has its own row. "Fake" is the
fake Plaud server (§10.6); a "real" bullet is proved by a runbook step, in the phase that runs it.
Rows for Phases 2 and 3 name the phase only: their plans assign tasks and tests.

| ID | Situation | Expected | Phase and task | Proof | Status |
|---|---|---|---|---|---|
| C1.01 | Random fake accounts: repeats across pages, both ID forms, consent and keep-out rows, late keep-outs and late consent listings already here, partial listings | Every listed ID lands in exactly one bucket by §11.3's precedence, and the equation adds up | Phase 2 | a property test (Phase 2's plan) | planned |
| C1.02 | The real dry run | Both equations add up and Dan signs them off; the consent list's IDs are found on Plaud | Phase 2 | runbook section 7 | planned |
| C2.01 | Fake, with an injected clock: 1,000 recordings, 40 speaker renames at once | Each shows within `full_sweep_days` plus one schedule interval, and at once with Re-sync | Phase 3 | Phase 3's plan | planned |
| C2.02 | Real: S9's clip; after the catch-up, a rename | One shows at once with Re-sync, and one within a day and one sync without it | Phase 3 | runbook (Phase 3) | planned |
| C2.03 | Fake: three speaker renames 20 minutes apart on 12 recordings | The breaker never trips | Phase 3 | Phase 3's plan | planned |
| C2.04 | Fake: a field that changes on every fetch | It trips the breaker, with no Accept button | Phase 3 | Phase 3's plan | planned |
| C2.05 | Fake: a new field that is the same on both fetches | It trips the breaker with **Accept these changes**; pressed, each recording gets one new snapshot and the breaker closes; the same field tripping it again offers no button | Phase 3 | Phase 3's plan | planned |
| C2.06 | A release with a new normaliser version | It closes the breaker at start | Phase 3 | Phase 3's plan | planned |
| C3.01 | Fake: a sound account, `sync --full` | Exits 0 and writes the manifest | Phase 3 | Phase 3's plan | planned |
| C3.02 | Fake: a degraded recording | Still exits 0 | Phase 3 | Phase 3's plan | planned |
| C3.03 | Fake: a flagged recording (consent-listed or kept out late) | Still exits 0, and is named | Phase 3 | Phase 3's plan | planned |
| C3.04 | Fake: an unparked failing recording | Exits 2 with its count | Phase 3 | Phase 3's plan | planned |
| C3.05 | Fake: Sync everything killed part-way | It resumes | Phase 3 | Phase 3's plan | planned |
| C3.06 | Real: Sync everything | Exits 0, or 2 only for recordings Dan then parks, each checked in Plaud's app and recorded in the runbook's table by ID hash | Phase 3 | runbook (Phase 3) | planned |
| C4.01 | Fake: an expired access token that refreshes | The pings go on | Phase 2 | Phase 2's plan | planned |
| C4.02 | Fake: a 401, then a refused refresh | The pings stop from that turn on, with no Plaud call until the token changes | Phase 2 | Phase 2's plan | planned |
| C4.03 | Fake: a new token, then a good turn | It pings again | Phase 2 | Phase 2's plan | planned |
| C4.04 | Fake: a 429 from the token endpoint | An outage, not `sign-in` | Phase 2 | Phase 2's plan | planned |
| C4.05 | Fake: a 401 after a good refresh | It goes through the current-user read | Phase 2 | Phase 2's plan | planned |
| C4.06 | Real: the catch-up ends | The sync's check goes green within 20 minutes | Phase 2 | runbook section 8 | planned |
| C5.01 | Fake: a placeholder-titled recording, later renamed to a private title | It arrives at the next run, untagged; renamed, it is tagged at the next run even when its fetch fails | Phase 2 | Phase 2's plan | planned |
| C5.02 | Fake: a keep-out title | It is never requested (the request log) | Phase 2 | Phase 2's plan | planned |
| C5.03 | Fake: the keep-out word added after the recording came in | `private` and one flag; nothing of it is fetched again, even when its transcript appears | Phase 2 | Phase 2's plan | planned |
| C5.04 | **Keep it here** on a late keep-out | The flag clears and no archive file changes | Phase 1, Task 8 | `packages/core/tests/test_plaud_flags.py` | planned |
| C5.05 | A kept-out title that later loses the word | It stays kept out until **Release** | Phase 2 | Phase 2's plan | planned |
| C5.06 | **Release** on a row kept out by title | It asks first, with the title in its question only | Phase 1, Task 21 | `packages/ui/frontend/src/components/SyncPage.test.ts` | planned |
| C5.07 | **Release** while the title carries the word, or the ID is consent-listed | Refused, saying why | Phase 2 | Phase 2's plan | planned |
| C5.08 | A consent-list typo | The `consent` blocker opens and the sync's check isn't pinged | Phase 1, Tasks 11 and 13 | `packages/core/tests/test_plaud_health.py`, `packages/core/tests/test_worker.py` | planned |
| C5.09 | A consent-list typo during syncs | Every import stops | Phase 2 | Phase 2's plan | planned |
| C5.10 | A bare `ID kept: …` line | Not an exclusion; listed for review by line number | Phase 1, Tasks 3 and 8 | `packages/core/tests/test_keepout.py`, `packages/core/tests/test_plaud_cli.py` | planned |
| C5.11 | An ID added to the consent list after its recording came in, with the sign-in dead | Tagged `private` at the worker's next tick, and flagged | Phase 1, Task 13 | `packages/core/tests/test_worker.py` | planned |
| C5.12 | A late consent listing while syncs run | Never fetched again, and the run goes on | Phase 2 | Phase 2's plan | planned |
| C5.13 | An already-flagged recording added to the consent list | No second flag: its flag changes | Phase 1, Task 8 | `packages/core/tests/test_plaud_flags.py` | planned |
| C5.14 | The consent list replaced by a rename | Read at the next tick | Phase 1, Task 13 | `packages/core/tests/test_worker.py` | planned |
| C5.15 | A consent stop, or a hold with an ID change | The cache and the Sync screen hold no title of a recording not here | Phase 2 | Phase 2's plan | planned |
| C5.16 | A removed `private`, then a `reindex` in a `web`-role process | It stays removed, and comes back only when the title changes to one that matches, with its note | Phase 1, Task 9 | `packages/core/tests/test_plaud_marker.py` | planned |
| C5.17 | A removed `private` through 100 sync runs | It stays removed | Phase 2 | Phase 2's plan | planned |
| C5.18 | `schedule_minutes` 20 from deploy, before the first catch-up | A scheduled turn makes no Plaud call, and an import request is refused | Phase 1, Tasks 13 and 14 | `packages/core/tests/test_worker.py`, `packages/core/tests/test_plaud_requests.py` | planned |
| C5.19 | An interrupted first catch-up (marks, no `catch_up_done_at`) | The gate stays closed, and Status says so | Phase 1, Task 12 | `packages/core/tests/test_plaud_gate.py` | planned |
| C5.20 | The interrupted catch-up run again, then ending | It picks up where it stopped, and the turn after it runs a sync | Phase 2 | Phase 2's plan | planned |
| C5.21 | The gate closed | Every request that would write a snapshot is refused | Phase 1, Task 14 | `packages/core/tests/test_plaud_requests.py` | planned |
| C5.22 | A SIGTERM mid-run; a catch-up, then a restart | Neither reads as "rebuilt" | Phase 2 | Phase 2's plan | planned |
| C5.23 | A Re-sync, then a restart | Doesn't read as "rebuilt" | Phase 3 | Phase 3's plan | planned |
| C5.24 | A restart after a rebuild was recorded | The rebuild isn't found again | Phase 1, Task 12 | `packages/core/tests/test_plaud_gate.py` | planned |
| C5.25 | A rebuilt `state.db` (2b snapshots, no marks) | Recordings created after it keep importing; one created before it that isn't here needs review, in the first listing or later; scheduled runs, Sync now, the CLI's `sync` and Import all leave it out; Import selected brings it in; its ID on the consent list keeps it out | Phase 2 | Phase 2's plan | planned |
| C5.26 | A rebuilt `state.db`, then Sync everything | It leaves out what needs review | Phase 3 | Phase 3's plan | planned |
| C5.27 | `recordings plaud review` after a restore | `rebuilt_at` is recorded as now, holding `plaud` | Phase 1, Task 12 | `packages/core/tests/test_plaud_cli.py` | planned |
| C5.28 | Syncs after `plaud review` | Everything made before it that isn't here waits for review | Phase 2 | Phase 2's plan | planned |
| C5.29 | Fake: one consent-listed ID listed again under new hex, with no hold | Never requested, and no title shown | Phase 2 | Phase 2's plan | planned |
| C5.30 | Fake: 10% of IDs re-keyed | A large shrink; Accept then imports none of the matched IDs | Phase 2 | Phase 2's plan | planned |
| C5.31 | Fake: a kept-out ID re-keyed | Likewise | Phase 2 | Phase 2's plan | planned |
| C5.32 | Fake: two candidates for a re-keyed ID | The new ID is held | Phase 2 | Phase 2's plan | planned |
| C5.33 | A Plaud ID's form flips between `of_` and bare | Its kept-out state, review mark and flag stay, keyed by canonical ID | Phase 1, Task 6 | `packages/core/tests/test_plaud_tables.py` | planned |
| C5.34 | Fake: a form flip with nothing absent, through syncs | Every kept-out ID stays out; every review mark and consent exclusion holds | Phase 2 | Phase 2's plan | planned |
| C6.01 | The ping rule as a table: good or not, a streak or not, each blocker, busy, the gate, a sign-in pause, a rate-limit wait | Each case pings or doesn't, against a fake Healthchecks | Phase 1, Task 11 | `packages/core/tests/test_plaud_health.py` | planned |
| C6.02 | Each blocker with a good run (a consent stop with no attempts; the breaker with working imports); privacy flags, a rebuild's review list, a failing recording, a failed Sync everything | Every blocker stops the ping; the others never do | Phase 1, Task 11 | `packages/core/tests/test_plaud_health.py` | planned |
| C6.03 | Runs with attempts and no success, failing-list recordings counted | Never good; the pings resume only after a run with a success | Phase 1, Task 11 | `packages/core/tests/test_plaud_health.py` | planned |
| C6.04 | Fake: every fetch failing while the listing works, or every link 403ing | No pings; once every recording is on the failing list, runs that retry only them stay not good | Phase 2 | Phase 2's plan | planned |
| C6.05 | Fake: an outage of 1 hour | The pings stop, then resume | Phase 2 | Phase 2's plan | planned |
| C6.06 | A failed run | Status shows it from the first failed run, with "isn't being pinged since …: <cause>" | Phase 1, Tasks 19 and 22 | `packages/ui/tests/test_plaud_views.py`, `packages/ui/frontend/src/components/SyncPanel.test.ts` | planned |
| C6.07 | A hold; **Accept this listing**; a change of form with nothing absent; a small shrink | Accept clears the hold, refused only when consent-listed or kept-out IDs vanish while Plaud's IDs look changed; the change of form holds nothing; the small shrink settles when the next listing an hour later is missing the same recordings | Phase 2 | Phase 2's plan | planned |
| C6.08 | A wedged `plaud` holder | No pings | Phase 1, Task 13 | `packages/core/tests/test_worker.py` | planned |
| C6.09 | A ping that fails | Status shows "last ping failed", with its time and HTTP status | Phase 1, Tasks 11 and 22 | `packages/core/tests/test_plaud_health.py`, `packages/ui/frontend/src/components/SyncPanel.test.ts` | planned |
| C6.10 | A run whose import the disk guard refused | Not good, so no ping; the streak clears at the first run the guard doesn't refuse | Phase 1, Task 11 | `packages/core/tests/test_plaud_health.py` | planned |
| C6.11 | Fake: the disk guard refusing an import | Status says "the disk is too full to import", and the pings resume at the first run it doesn't refuse | Phase 2 | Phase 2's plan | planned |
| C6.12 | The schedule off, a bad `keep_out_word`, a long rate-limit wait, each blocker | "Isn't being pinged" names the cause | Phase 1, Task 11 | `packages/core/tests/test_plaud_health.py` | planned |
| C6.13 | An idle worker with no sign-in, for 20 minutes on a fake clock | `--alive` stays 0 | Phase 1, Task 13 | `packages/core/tests/test_worker.py` | planned |
| C6.14 | The schedule off for 24 hours after the catch-up | It shows in Needs attention | Phase 1, Task 19 | `packages/ui/tests/test_plaud_views.py` | planned |
| C7.01 | Fake: a whole sync at DEBUG, with §21's faults | No token, `X-Amz-` query or title in any log, alert, Status view, `ops_runs` row or request result | Phase 2 | `test_flow_2b.py` (Phase 2's plan) | planned |
| C7.02 | A link with a control character or an unknown scheme | The client raises its own error, with no URL | Phase 1, Task 4 | `packages/core/tests/test_plaud_client.py` | planned |
| C8.01 | `state.db` with the 2b tables | `SCHEMA_VERSION` is 1 and `MIGRATIONS` empty | Phase 1, Task 6 | `packages/core/tests/test_plaud_tables.py` | planned |
| C8.02 | A recording written by the 2b import | It validates against 2a's model and schema | Phase 2 | Phase 2's plan | planned |
| C8.03 | `make deploy-smoke` | It passes | Phase 1, Task 17 | `scripts/compose_smoke.py` (CI's compose-smoke job) | planned |
| C9.01 | The Sync screen and Status | Drawn at 1,280 and 320 pixels | Phase 1, Task 24 | `packages/ui/tests/e2e/test_sync.py` | planned |
| C9.02 | Kept out and Flags rows | No title | Phase 1, Task 21 | `packages/ui/frontend/src/components/SyncPage.test.ts` | planned |
| C9.03 | **Release** | Its question, then Let it in | Phase 1, Task 21 | `packages/ui/frontend/src/components/SyncPage.test.ts` | planned |
| C9.04 | **Park** | It sends `park`, and **Unpark** undoes it | Phase 1, Task 21 | `packages/ui/frontend/src/components/SyncPage.test.ts` | planned |
| C9.05 | **Re-sync from Plaud** on a selection | One request for the selection, with its progress and end line (the worker's handling: Phase 3) | Phase 1, Task 23 | `packages/ui/frontend/src/components/SelectionBar.test.ts` | planned |
| C9.06 | The arrivals cue | It counts what came in since the page loaded, shortened at 320 pixels | Phase 1, Task 23 | `packages/ui/frontend/src/components/HeadNotes.test.ts` | planned |
| C9.07 | Demo and Pages | The canned view, with no network and no request | Phase 1, Task 24 | `packages/ui/tests/test_demo_guard.py`, `packages/ui/tests/pages/test_pages_site.py` | planned |
| C10.01 | `make deploy` | It waits for the worker's health check | Phase 1, Task 17 | `packages/core/tests/test_deploy_template.py` | planned |
| C10.02 | The worker with no sign-in and no consent list | It stays healthy | Phase 1, Task 17 | `scripts/compose_smoke.py` | planned |
| C10.03 | Real: `doctor --role worker` on the server, after the sign-in | It passes | Phase 2 | runbook section 6 | planned |
| C10.04 | The two dead-man URLs | `deadman_sync_url` is mounted in `worker` only, and `deadman_url` in `backup` only | Phase 1, Task 17 | `packages/core/tests/test_deploy_template.py` | planned |
| C11.01 | The README, the runbook and the tagging part A spec | A doc test fails if the old sentence ("until stage 2b's import dry run is done") is in any of them, or the new one ("refining 2026-10-22") is missing from any | Phase 1, Task 2 | `packages/core/tests/test_docs.py` | planned |

### Later

None: every bullet of §20 is planned in a phase.

## Revisions

- 2026-10-23: Dan's decision 1, a placeholder-titled recording: "Download now, tag later" (`c9cfc75`).
- 2026-10-23: Dan's decision 2, keeping a recording out from the phone: "Title marker" (`c9cfc75`).
- 2026-10-23: Dan's decision 3, a `private` tag he removed: "Stays removed" (`c9cfc75`).
- 2026-10-23: Dan's decision 4, a dead sync worker: "Second check" (`c9cfc75`).
- 2026-10-23: Dan's decision 5, Plaud's routine errors: "Push if it lasts 2h" (`c9cfc75`).
- 2026-10-23: Dan's decision 6, speaker fixes in bulk: "Re-sync a selection" (`c9cfc75`).
- 2026-10-23: Dan's decision 7, when tagging the real archive may start: "After import + backup"
  (`c9cfc75`).
- 2026-10-23: Dan's decision 8, decision 4 refined: "One push, with reason" (`392b67e`).
- 2026-10-23: Dan's decision 9, alerts while there's no ntfy: "3 + 1" (`06f8c81`; carried through
  in `fab3d9d`).
- 2026-10-23: Dan's decision 10, alerts trimmed to one rule: "2 hours, same as all" (`8016ed3`).
- 2026-10-23: Dan's decision 11, the edit breaker: "Accept on Status" (`8016ed3`).
- 2026-10-23: spec approved; 2b split into three phases by the spikes (committed with Phase 1's
  plan).
- 2026-10-24: Dan's decision 12, a consent-list ID split by a space or a line wrap: "Stop and tell
  me" (§12.1 rule 4 widened; Phase 1, Task 3).
