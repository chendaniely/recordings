# Using recordings

A plain how-to: one section per screen or task, with what you see and what to do. The runbook
(`docs/runbooks/first-run.md`) has the server-side detail; this guide links to it rather than
copying it. Stage 2b's sections are added as each part is built, and finished at its end.

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
- **Sync now** answers "Held: see Status", and the import buttons are off.
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
