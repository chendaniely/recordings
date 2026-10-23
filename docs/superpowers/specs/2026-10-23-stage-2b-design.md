# Stage 2b: Plaud flows in and keeps up (design)

Status: draft. The decisions below are Dan's, from 2026-10-23. The rest of the design is being written by
the controller, using a five-member research council (spec archaeology, the codebase, Plaud's API and
reliability, the project's spirit and Dan's experience, architecture/ops/security), and will be reviewed
by that council.

Parent: `docs/superpowers/specs/2026-10-08-recordings-design.md` (the main spec), §20 stage 2b.

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
