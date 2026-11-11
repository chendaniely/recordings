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

So the first server is the ThinkPad T440s, for now, and a ThinkCentre later. The move between them
is the runbook's "Restoring for real", from the NAS's backups. `audio-router`'s media folder was
about 2.4 GB on 2026-11-11 (measured at its example config's `media_root`), so the runbook's "three
times over, with 20% free" fits even a small disk.
