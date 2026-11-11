# The first real run: Dan's answers (from 2026-11-11)

local-ai's session asked for a recordings session that runs the runbook's sections 0 to 5
([`first-run.md`](../../runbooks/first-run.md)), then [`docs/setup.md`](../../setup.md)'s steps 1
to 5. That session merged `chendaniely/uses-the-spark` into `main` first (fast-forward to
`d2ecc3a`), so CLAUDE.md's pointer to local-ai is on `main`, and read recordings' three open
notices (local-ai #45, #46 and #50). None of them blocks this run: all three are about stage 4,
when recordings first calls the Spark, so they stay open.

Each answer below is in Dan's words, with the question as it was put to him.

## 1. Which machine runs recordings? (asked 2026-11-11)

Scenario: the runbook needs a Linux homelab server, and on 2026-11-09 there wasn't one; the Mac and
the Synology had been ruled out. Options: A (recommended), a separate Linux box; B, the Spark;
C, no server yet, so stop.

Dan: "the new homelabe server is not ready. i only have my widows-wail t440s on my home network at
the moment, i don't mind deploying this there and then potentially plan for a migration to a
thinkcentre pc later on".

So the first server is the ThinkPad T440s, `widows-wail`, for now, and a ThinkCentre later. The move between them
is the runbook's "Restoring for real", from the NAS's backups. `audio-router`'s media folder was
about 2.4 GB on 2026-11-11 (measured at its example config's `media_root`), so the runbook's "three
times over, with 20% free" fits even a small disk.

## 2. Does the T440s run Linux itself? (asked 2026-11-11)

The session misread "widows-wail" as Windows and asked whether to install Linux, use WSL2 or wait.
Dan: "no the t440s runs ubuntu server, widows-wail". `widows-wail` is its hostname, and it already
runs Ubuntu Server, so the runbook applies as written.

## 3. Who runs the commands on the server? (asked 2026-11-11)

Options: A (recommended), Dan runs each step and pastes the output; B, the session runs read-only
checks over ssh; C, the session runs everything that needs neither sudo nor a secret.

Dan: "oh i don't want to install and deploy this right now. i'm not ready for that at the moment."

So the runbook's sections 0 to 5 and `docs/setup.md`'s steps 1 to 5 wait, as they did on
2026-11-09. When Dan is ready, start again at question 3; the server will be `widows-wail` unless he
says otherwise. Nothing ran on any server, Plaud or the archive in this session.

## 4. Push `main`? (asked 2026-11-11)

Options: A (recommended), push once `make check` passes; B, keep it local. Dan chose **A**, with no
further reason given. The push also carries the fix that keeps ruff out of the build record: CI's
`python` job on `main` had failed since `86f50ef` (2026-11-10), because ruff linted
`task-4-client-as-copied.py`, which is kept exactly as copied.
