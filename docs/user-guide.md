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
  new ID with the same start time and duration as one you kept out. It stays out.

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
