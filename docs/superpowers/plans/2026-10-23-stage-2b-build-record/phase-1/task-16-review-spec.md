# Task 16 review: Spec axis (Tier 1), 7894104..e139f58

What I checked: I read spec §12.1, §17.1 to §17.5 and §21, plus plan decision 9 and Task 17's runbook text. I ran
`test_backup.py` and `test_doctor.py` with `RECORDINGS_REQUIRE_RESTIC=1` (142 passed) and ruff (clean).
I also ran probes in my scratchpad: the pinned `.cache/bin/restic` against synthetic folders, and doctor
against synthetic configs.

## Findings

**Important**
1. The suite never proves with the real restic that a folder comes back as a folder.
   `test_with_a_real_restic_backup_check_forget_and_restore` (`test_backup.py:719`) still
   backs up deploy.env alone. Every folder test runs through `restore_like_restic`, a fake
   that this change edited to copy a whole tree. If restic laid a folder out any other way,
   those tests would still pass (brief: "A backed-up folder must come back as a folder in the
   restore test"). My probe showed the code is correct today: the folder, an empty subfolder
   and a symlink come back, and the restore test passes. CI has no guard for it, though.
   Fix: put `consent_folder(tmp_path)` in that test's `extra_paths`.

**Minor**
2. `backup.py:487` `_came_back` takes the folder-or-file decision from the live path at the time of
   the restore test. If the folder has gone by then, the test says "is not in the snapshot"
   even though the snapshot holds it (probed). It fails closed, but the message is wrong.
   Fix: when the live path is gone, accept either kind.
3. A symlink in `extra_paths` is backed up as a link only. The restored absolute link points at
   the live folder, so `_came_back` passes with nothing actually backed up (probed). Files
   already had this problem before this change, and a Docker bind mount is not a symlink.
   Fix: `missing_paths` refuses a symlink.
4. `doctor.py:757` compares paths without resolving them, so the relative `plaud` and `//plaud`
   are not flagged (probed). A `..` inside `token_store` is never normalised either.
   `scripts/plaud_spike.py:285` resolves its paths. Fix: make both sides absolute and
   normalised, or refuse a relative extra path.
5. Gaps in the tests:
   - Spec §17.2 says "Every role", but the loop at `test_doctor.py:1189` leaves out `worker`.
     The code covers it (probed).
   - `test_the_manifests_folder_and_the_state_copy_are_not_problems` never lists the state copy.
   - The comment on `EXTRA_PATHS` (line 47) says `config.example.toml` lists `/consent`. Only
     the template's comment does.
6. Docs:
   - Spec §17.3 "Rolling back across 2b" is missing the step that takes `/consent` out of
     `extra_paths`, so 2a's `run_backup` would refuse. Task 17's runbook text has the step, in
     the right order, and its test pins it. Amend §17.3 in Task 17 so the spec says the same.
   - The `BackupRefused` docstring (`backup.py:66`) still says "a missing file".
   - "Runbook section 6" does not exist until Task 17 adds it.

The spec asks for `/state/manifests` (§17.2, §17.4) and `plaud-cache.db` (§21). Leaving them
out is correct: decision 9 and the spec's own phase table defer them, and a missing extra path
would refuse every backup. No scope creep. No Scenarios IDs are named. No names were renamed or
removed, and no "not yet" or "planned" wording is made untrue.

Verified: a missing folder refuses the backup with the 2a phrase and makes no restic call. The
free-space estimate measures links with `lstat` and does not follow them out of the folder: a
link to 100 kB counted as 52 bytes. Doctor flags the token's folder, a parent of it, anything
inside it, `/`, `/state` and the configured state path, in every role.

## Security checklist
1. Secrets: fine. No token is read and no value is printed. Messages name configured paths only.
2. Permissions: fine. Nothing new is created. Doctor now flags any extra path that would carry `/plaud`.
3. Network exposure: fine. Nothing listens. The tests use FakeRestic or a local restic repository. No new UI imports.
4. Failing closed: fine. A missing folder refuses the backup. An unreadable `[plaud]` falls back to `/plaud`. A folder gone at restore time fails the test (wrong label, finding 2).
5. Injection: fine. restic gets an argv list and no shell. There is no SQL change.
6. Logging: fine. `BackupRefused` goes to the debug log only. Doctor lines carry paths and no `str(exc)`.

Verdict: changes needed (0 Critical, 1 Important)
