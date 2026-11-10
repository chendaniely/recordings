# Privacy review fixes: the end-of-branch review's findings

Read `BUILD-FAST.md` (same folder) first. Two read-only reviewers looked at the branch at 694b6a9:
`reports/review-privacy-data.md` and `reports/review-privacy-boundaries.md`. Read both: they hold
the `file:line` traces. The controller checked the High against the code and the spec (§11.4 step
6: a re-keyed match's "title is never stored or shown", and "Release, whose question then shows no
title"). Fix the items below, **test first**: each test fails before its fix, and the commit quotes
the failing line. One commit per numbered item, or per pair where they share code. `make check`
before each; `make e2e` when `recordings_ui` or the frontend changes; `make e2e-live` when the run or
the screens it drives change. Every fix fails closed: when in doubt, keep out, refuse, or hold.

## 1. Release's preview on a re-keyed row (High)

`release.may_preview` accepts `kept_by == "rekeyed"`, so `worker._release_preview` fetches the
current title, writes it to `plaud-cache.db` and the dialog shows it. Fix:
- `may_preview` refuses a re-keyed row (no title fetched, nothing cached); the refusal phrase is
  fixed, never a title.
- The UI's Release on a re-keyed row asks its question **with no title** (by ID, date and duration,
  as §11.4 step 6's flag shows it) and goes straight to Let it in, never through the preview.
  `ReleaseDialog.tsx` already expects no title for that case: check it and wire it.
- `may_release` for a re-keyed row still re-checks the consent list as it is now and the current
  title against every enforced keep-out word, as §12.2 step 3 does, without returning or storing the
  title.
- Pin it: a re-keyed row's preview request writes no cache row and its reply carries no title; the
  Release path for it still works end to end on the fake.

## 2. No preview during an IDs-changed hold (High, same code)

§11.4 step 3 stores and shows no title while a held listing shows Plaud's IDs changed. The preview
path (and a review row's Show title) never reads `K_HOLD`. Refuse the preview while that hold is
on, with a fixed phrase pointing at Status. Pin it.

## 3. A rebuilt state.db detected at any run's start (Medium)

`gate.detect_rebuild` runs only at the worker's start (`worker.py` ~516, inside `_step`, which
swallows a failure and never retries). A CLI `sync --dry-run` or `sync --catch-up` run first writes
`plaud_listing` rows (or marks), so the review marks never happen and kept-out, re-keyed and
un-released IDs import. Fix: every run that lists (the CLI's `sync` in all its forms, `resync`,
`--full`, and the worker's turns) runs the same detection before its first listing, under the same
hold, and a run whose detection fails (raises) does not list: it ends with the run's failure phrase
and a non-zero exit. Recording the rebuild stays one transaction, so it is found once. Pin both
orders: dry run first, catch-up first.

## 4. An invalid keep-out word refuses the importing requests too (Low)

Today it idles only the scheduled syncs; Sync now, Import, Re-sync, Sync everything and Release
still run with no word enforced, and Compare stays enabled. Refuse those requests (and the CLI's
importing runs) while the words can't be read, with the same phrase the scheduled sync's idle
uses; disable the buttons with that reason. Compare may still run only if it writes nothing and
stores no title it couldn't judge; otherwise refuse it too.

## 5. The run-start consent read uses the guarded read (Low)

`sync.py` ~482 reads the consent list once without the mid-write guard the worker's and the
import's reads have. Use the same guarded read; a read that fails the guard counts as a consent
stop for the run.

## 6. Sync everything stops checking on a mid-run hold (Low)

`full.py` ~354-386: `_may_check` never reads `K_HOLD`. A hold recorded mid-run (by a light turn)
stops further checks and fetches in that run; it resumes after the hold clears, as its resume
already allows.

## 7. Tokens never in a backup (Low)

`backup.py` ~393-427, `doctor.py` ~159-168. A backup (and the mirror) refuses to run when
`[plaud] token_store` resolves inside the archive or inside any `extra_paths` entry, naming the
setting, never the token. If a `[plaud]` setting can't be read, refuse instead of assuming the
default `/plaud`. Doctor's check says the same.

## Not to build (for Dan)

- `check_api_base` / `check_token_url` allowing only `*.plaud.ai` and the loopback (the host is a
  guess; a wrong allow-list blocks the first real run).
- Decision 16's known gap; a new keep-out word re-checking titles already here; the re-key cache for
  late-listed recordings.

## Docs

Keep the spec true where a fix changes behaviour (a Revisions line dated 2026-11-02), and the user
guide's Release section (a re-keyed row's question shows no title, and why). Add any new phrase to
the guide where it explains Status.

## Report

`reports/privacy-review-fix-report.md`: commits, each test's failing line before its fix, what
changed, anything you couldn't fix and why. Commit trailer: BUILD-FAST's.
