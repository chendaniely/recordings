# Setting up the Plaud sync: from a deploy to the first real sync

One page, in order, from deploying stage 2b on the homelab server to the first real import from
Plaud. Each step says where it runs, what it does to the archive, and where to stop and ask in a
Claude session. The commands and every message are in the runbook
([`docs/runbooks/first-run.md`](runbooks/first-run.md)); this page links to its sections rather than
copying them. What the screens show is in the user guide ([`docs/user-guide.md`](user-guide.md)).

**Dry run first** (your answer, 2026-11-01: "Server, dry run first"). Nothing comes in from Plaud
until step 5, and step 4 shows you everything step 5 would do.

## Before you start

- **The server runs stage 2a.** The runbook's sections 0 to 5 are done: the archive holds what
  `audio-router` had, backups run every hour, and the mirror reaches the Mac. If they aren't, do
  them first. On a server that has never run the app, at a 2b commit, do section 6's steps 1 to 4
  (this page's step 1) before section 2's `backup init` and `up -d`, as section 0 says.
- **The spikes are skipped** (runbook 1c). The build took Plaud's details from `audio-router`'s
  code instead, and each guess is a row in
  [`docs/superpowers/plans/2026-11-01-stage-2b-guesses.md`](superpowers/plans/2026-11-01-stage-2b-guesses.md),
  with how you would notice it's wrong. Steps 2 and 4 are where most of them would show.
- **Want to see it work first?** `make demo-live` on the Mac runs the real web app and worker
  against a fake Plaud, in a scratch folder ("Trying it first" in the user guide). Ten minutes there
  makes every screen below familiar.
- **No tagging yet.** The first real tag waits until step 5 ends with "You may start tagging now"
  (decision 7): until then, rolling back loses no tag of yours.

| Step | Where | What it does to the archive |
|---|---|---|
| 1. Deploy | the server | Imports nothing. The new `worker` starts idle and waits for the first catch-up; it only tags `private` a recording already here that the consent list names. |
| 2. `make plaud-sign-in` | the Mac | Nothing. It installs the sign-in on the server. |
| 3. The consent list and the keep-out word | the Mac or the server | Imports nothing. A recording already here whose ID you add is tagged `private` and flagged. |
| 4. `make plaud-dry-run` | the server | Nothing. It reads Plaud's list and says what would happen. |
| 5. `make plaud-catch-up` | the server | The first real import, between two backups. Then the schedule starts by itself. |

## 1. Deploy

**Where:** the homelab server, in `$REPO_DIR`, with the runbook's top block pasted.

**Do:** the runbook's [section 6, "Deploy 2b"](runbooks/first-run.md#6-deploy-2b), steps 1 to 5.
In short:
1. Check out stage 2b's merge commit (`git fetch`, then `git checkout`).
2. Open the Library with the browser's network panel open: it must load in under 2 seconds. If it
   doesn't, stop and ask.
3. Make the consent folder (and move the list into it), the sign-in's folder and the manifests
   folder; set `RECORDINGS_CONSENT_HOST` and `RECORDINGS_PLAUD_HOST` in `docker/deploy.env`; add
   `"/consent"` and `"/state/manifests"` to `[backup] extra_paths` in `config.toml`.
4. Create the sync's own Healthchecks check (a period of 20 minutes, a grace of 2 hours) and put its
   ping URL in the `deadman_sync_url` secret. Its email is the sync's only alert.
5. `make deploy`. It waits for `web` and the new `worker` to pass their health checks.

**You should see:** `rc ps` shows `web`, `backup` and `worker` up, and `worker` healthy. Status,
in the app, has a **Plaud sync** panel that says "Scheduled sync waits for the first catch-up
(runbook)", and the Sync screen (**Add**) has only **Compare** enabled. `rc exec worker recordings
doctor --role worker` names one problem, "sign-in needed: run `make plaud-sign-in` at the Mac
(runbook, Plaud sign-in)": step 2 clears it.

**What it does to the archive:** imports nothing. The worker reads the consent list at once, and
tags `private` (and flags) any recording 2a imported whose Plaud ID is on it: 2a's parser missed
some forms of ID, and `plaud consent` lists any it finds by archive ID.

**Stop and ask if:** `make deploy` fails its health checks (roll back as the runbook's "Rolling back
across 2b" says, not as 2.8); doctor reports a problem other than the sign-in; or the Library takes
more than 2 seconds to load.

## 2. Sign in to Plaud

**Where:** the Mac, at this repo's top. It needs `local.mk` (git-ignored) with `SERVER`, your ssh
name for the server, and `REPO_DIR`, the checkout's path there.

**Do:**
```bash
make plaud-sign-in
```
A browser opens: sign in to Plaud there. Then, on the server, `rc exec worker recordings plaud
check`. The details and every message are the runbook's
["Plaud sign-in"](runbooks/first-run.md#plaud-sign-in).

**You should see:** one line, on the Mac and again from `plaud check` on the server: "signed in:
yes · access token expires … · refresh token: present · account 1a2b3c4d" (the account is a short
code, never your e-mail). Status's numbers show the sign-in and when it expires.

**Then check the whole deploy:** the runbook's section 6, step 7: `rc ps`, `doctor --role worker`
(no problem now, exit 0), `plaud consent`, and `alert-test` in `worker` and `backup`. A day later,
step 8: the sync's Healthchecks check still says new, never down.

**What it does to the archive:** nothing. The sign-in lives only in the server's sign-in folder,
never in a backup or the mirror. It is a secret: you sign in yourself, in the browser, and never
copy its file by hand or ask a Claude session to run this.

**Stop and ask if:** it says "refresh token: missing" (the server would need a new sign-in every
time the access token expires); step 4 fails with a message naming a key such as `access_token`
(the vendor's file isn't shaped as the build guessed); or it worked, but Status says "sign-in
needed" again around when the access token was due to expire (the refresh, also a guess, failed:
the runbook's "Plaud sign-in" says which setting to look at).

## 3. The consent list and the keep-out word

These keep recordings out of the archive for good. Both fail closed: while the list can't be read,
nothing is imported.

**The consent list** names Plaud recordings that must never come in, one ID a line, in
`$RECORDINGS_HOME/consent/consent-list.md` on the server (step 1 moved it there).
- **To add one, from the Mac:** `make consent-add ID='<id>'`, the ID in single quotes. To find an
  ID before the first catch-up, press **Compare** on the Sync screen (**Add**): it is the one button
  that works now. Find the recording under **Not here yet** by its date, time and duration, and press
  **Copy ID**. The runbook's ["Keep a recording out for good"](runbooks/first-run.md#keep-a-recording-out-for-good)
  has the rest, and how to edit the file by hand.
- **Check what the worker read:** `rc exec worker recordings plaud consent` on the server. It
  prints the count ("consent list: 14 IDs, 4 on Plaud now"), when it was read, any line it reads as
  kept rather than excluded, by number ("lines read as kept, not excluded (check each meant keep):
  …"), and any recording already in the archive that the list names ("in the archive: <ID>").

**The keep-out word** keeps out a recording you start from your phone: begin its title in Plaud's
app with `noarchive` (the template's word) and it is never downloaded. It is `[plaud]
keep_out_word` in `config.toml`; to use another word, change it there and `rc restart worker`.
Status's Details shows "Keep-out words" in force. The word is forgiving ("NoArchive:", "No archive"),
so an ordinary title that starts with it stays out until you press **Release**.

**Private titles.** `[plaud] auto_private_patterns` in `config.toml` (by default titles starting
"private", and any with "interview:") are tagged `private` before any of their content lands. Check
the list says what you mean before step 5.

**What it does to the archive:** imports nothing. A recording already here whose ID you add is
tagged `private` within seconds and flagged on Status and the Sync screen: the worker never removes
anything, so take it out by hand if it mustn't stay (the runbook's
["Taking a recording out by hand"](runbooks/first-run.md#taking-a-recording-out-by-hand)).

**Stop and ask if:** Status says "Plaud imports stopped: the consent list …" and the runbook's
["When imports stop: the consent list"](runbooks/first-run.md#when-imports-stop-the-consent-list)
doesn't explain it; or `plaud consent` reads a line as kept that you meant as an exclusion and you
can't see why.

## 4. The dry run

**Where:** the server, in `$REPO_DIR`.

**Do:**
```bash
make plaud-dry-run
```
It lists Plaud's whole account and classifies every recording on it, then prints `plaud consent`
again. The runbook's [section 7](runbooks/first-run.md#7-the-dry-run) is the checklist.

**What it does to the archive:** nothing at all: no audio, no transcript, no tag, nothing in the
archive. It also doesn't count as a good listing for later. The Sync screen's **Compare** does the
same listing.

**Reading its output.** Every line is counts and IDs, never a title:

| It prints | Check |
|---|---|
| `listed 120 = here 98 + waiting 0 + excluded 14 + missing 8` | It adds up. **here** is what the archive already has from Plaud; **excluded** is what the consent list and the keep-out word keep out; **missing** is what the catch-up will bring in. |
| `in the archive from Plaud 98 = here 98 + gone from Plaud 0` (and `+ absent from this listing N`) | Anything absent is a recording you deleted in Plaud. |
| `of the waiting and missing, 3 arrive private` | Matches the private titles you expect among the new ones. |
| `consent list: 14 IDs, 4 on Plaud now` | The count you expect. |
| `missing 8: waits for the first catch-up` | The missing ones are the recordings made since 2a's import (about 30), plus any 2a held back. |
| `ID forms: of_ 120` | Plaud's ID forms. Several forms with something missing would be a hold. |
| `result: ok` and `the first catch-up would import 8` | The number step 5 will bring in. |
| one line per listed recording, by group: `missing  of_… · 2026-10-30 14:05 · 47 min 12 s · waits for the first catch-up · arrives private` (a `here` line names the archive's recording instead) | Spot-check a few by date and length in Plaud's app. |
| then `plaud consent`'s lines | As in step 3: each line it reads as kept must mean keep; any recording in the archive it names, review in the app (it is already `private`), and take out by hand any that mustn't stay, **before step 5's `pre-import` backup**. |

It exits 0 when the listing was complete and nothing holds it. Otherwise its last line says why: 1
held or unreadable, 69 Plaud couldn't be reached, 77 the sign-in is needed (step 2), 78 the consent
list stops it (step 3).

**Stop and ask if:** anything doesn't fit; the numbers don't add up; "here" isn't the archive's
Plaud recordings; or a line starts "Plaud sync paused: …". A hold before the first import usually
means Plaud's IDs differ from 2a's: don't accept it, ask.

## 5. The first real sync: the catch-up

**Where:** the server, in `$REPO_DIR`, inside `tmux` or `screen` (it can take a while).

**Do:** first the volatility check (the runbook's [section 8](runbooks/first-run.md#8-the-catch-up),
step 1): `rc exec worker recordings plaud check --volatility`. It fetches three recordings twice a
minute apart and writes nothing; exit 0 with "every part came back the same" means go on. Then:
```bash
make plaud-catch-up
```
It stops the backup service, then runs each step only if the one before it passed: a backup tagged
`pre-import`; the baseline (every title already here checked once against your private patterns
and the keep-out word); the catch-up itself, one line per recording ("importing 5 of 31"); a deep
validation; and a `post-import` backup. It starts the backup service again however it ends.

**You should see:** "make plaud-catch-up: done. You may start tagging now. Within 20 minutes the
sync's Healthchecks check should show up." Then, in the app: the new recordings in the Library's
**Untagged**, the private ones with their lock; Status's panel says "All here", or why not.

**What it does to the archive:** brings in every recording the dry run called missing, each whole
or not at all, with its audio, Plaud's transcript and notes; tags `private` what your patterns
match before any of its content lands; and keeps out everything the consent list and the keep-out
word name. The baseline may also tag `private` a recording already here whose title matches.
Nothing is ever deleted. The `pre-import` backup is the way back.

**If it stops part-way**, run `make plaud-catch-up` again: it imports only what isn't here yet. If
it says "Plaud sync paused: …" (held), read the runbook's section 8, step 4. "Some recordings
failed" means it is done anyway: the scheduled sync retries them.

**Stop and ask if:** the volatility check exits 1 (Plaud sends something that changes by itself);
validation fails (step 5 of the target); or the sync's Healthchecks check isn't up within 20
minutes.

## After the first sync

- **The schedule runs by itself** every 20 minutes. New recordings arrive in **Untagged**; you
  never confirm an import.
- **You may tag** now: the disposable window is over, and a rollback is a restore.
- **Status** is where to look when something seems off; Healthchecks emails you if the sync stops
  for about 2 hours. The user guide's "What Status's lines mean" says what each line asks of you.
- **Edits you make in Plaud** (speaker names, notes) follow within about a day; **Re-sync from
  Plaud** fetches one at once.
- **Set `[plaud] full_sweep_days`** back to 7 when you finish relabelling speakers in Plaud (the
  template has 1, for the relabelling period).
- **Set up ntfy** whenever you like: the runbook's "Setting up ntfy, later".
