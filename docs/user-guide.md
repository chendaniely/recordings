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
