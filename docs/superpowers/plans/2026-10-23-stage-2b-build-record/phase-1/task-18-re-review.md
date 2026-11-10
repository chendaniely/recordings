# Task 18 re-review: fixes 8789f9e and 8f3ec57 (scoped)

Ran: `uv run pytest packages/core/tests/test_make_targets.py test_deploy_template.py test_runbook.py -q`
gives 69 passed. The locale test ran (en_US.UTF-8 is present) and was not skipped. Ruff is clean on the
changed Python, and `make -n consent-add` works under /usr/bin/make 3.81. I probed docker/consent-add.sh
under /bin/sh and /bin/dash, in scratch checkouts with synthetic lists, and compared each list with
`keepout.read_consent` before and after the add. I probed `consent_id()` with 23 inputs.

## Original findings

Standards review:
1. (Important) upstream-issues entry: **addressed.** The entry is deleted. 8789f9e's body records what
   was read and says plainly that GNU make §5.7.2 and coreutils' mktemp page were not read (HTTP 429).
   Its claim that "the review's report quotes both" overstates it: the review states those points and
   doesn't quote them.
2. (Minor) bracket ranges: **addressed.** consent-add.sh:23 lists the digits one by one, and
   `test_a_letter_outside_ascii_is_not_a_hex_digit_in_any_locale` ran and passed.
3. (Minor, process) system `python3`: **not yet, as expected.** It is recorded at task-18-report.md:17
   and belongs in the close's Findings. Task 18 is still open, and both fix rounds used uv only.
4. (Minor) Makefile:127 blamed the ID: **addressed.** Makefile:128 now says "step 1 failed, checking the
   ID (above)".
5. (Minor) runbook "any editor … renames": **addressed.** first-run.md:1278-1282 now says the list is
   0400, says to `chmod u+w` it first, and says the watcher compares inode, size and mtime. That is
   true: worker.py:304-307 and keepout.signature. test_runbook checks the old text is gone.
6. (Minor) smells: **partly addressed, which is fine.** The duplication is gone (`fake_running_backup`,
   the `make()`/`run()`/`script()` helpers, `cannot_ask`). The `ops-busy.py`/`ops_lock_held` names stay,
   as the plan named them.

Spec review:
1. (Important) consent-add made or extended a missing or empty list: **partly addressed.** A missing
   list, an empty one, one of whitespace only and one of ASCII comments only are all refused
   (consent-add.sh:39-42). The refusal comes before `mktemp`, so nothing is written and no temporary
   file is left; the tests and my probes under sh and dash agree. But a list the parser reads as
   empty or comments-only because of a BOM or other invisible characters is still extended, and the
   stop lifts. See new finding N1.
2. (Important) consent_id.py dropped a second ID: **addressed** (consent_id.py:22-31).
   - Accepted, as they should be: a bare ID, an upper-case one, `of_`, `OF_`, the Copy ID form with
     and without `of_`, Copy ID plus a date note, an en dash, a `*` bullet, leading spaces, a trailing
     space, and a note holding a hex word or 23 hex digits.
   - Refused, as they should be: two Copy IDs with or without a space between them, a second ID
     (bare or `of_`) in the note, two bare IDs, two IDs run together, and 24 hex digits in a note.
   - The remaining gaps are contrived (N7).
3. (Minor) an ID already on the list is added again: **addressed by documenting it**, per the ruling
   (first-run.md:1287).
4. (Minor) the readback comes before the worker's next tick: **addressed by documenting it**, with no
   sleep (Makefile:139, first-run.md:1289-1291). Which count is meant is unclear (N5).
5. (Minor) one message for three failures: **addressed.** Makefile:129-138 gives separate messages for
   ssh 255, exit 3 and exit 4, with a fallback for any other code. `test_each_failure_names_its_step`
   covers each under GNU make 3.81 and /bin/sh. Two of the messages need rewording (N3, N4).
6. (Minor) deploy-guard said everything "starts again from the beginning": **addressed.**
   - deploy-guard.sh:61-65 matches the spec: Sync everything "resumes … continues with what isn't
     checked since" (§14.6), an interrupted first catch-up means "run `--catch-up` again" (§11.6), and
     the baseline is safe to run again (its "already seeded" count, §14.5).
   - The new `cannot_ask` sends both "could not ask" messages to stderr and exits 1.
7. (Minor) runbook "any editor" and decision 12's example: **addressed** (first-run.md:1278-1282, and
   `- 20261023 — asked` at 1299).
8. (Minor) the upstream-issues entry: **addressed** (same as standards item 1).

## New findings

- **N1, Important (privacy check fails open). docker/consent-add.sh:42.** The comment-and-blank check
  works on ASCII bytes: `grep -v '^[[:space:]]*#'`, then `tr -d ' \t\r\n'`. The parser reads the list
  differently. It first runs `visible()`, which applies NFKC and drops Cf characters such as the BOM
  and the zero-width space (models.py:179). It then uses Python's Unicode `strip()` and `lstrip()`
  (keepout.py:182-184).
  - So the script does not refuse these lists, although the parser reads each one as "the consent
    list is empty" or "holds no IDs":
    - a BOM alone;
    - a BOM followed by `# comments`;
    - a line holding only a no-break space;
    - a zero-width space before `#`;
    - a fullwidth `＃ header`;
    - a line holding only U+3000;
    - a line holding only `\x1f`.
  - Probed under sh and dash: every one of these exits 0, and after the add the parser reports no stop
    (`stop=None`). So the stop lifts with one exclusion, which is what the ruling forbids.
  - A BOM before a comment header is the realistic case, and keepout handles a BOM on purpose. The
    script's header says it reads comments "as the parser reads them", which isn't so for these lists.
  - Fix: count only printable ASCII. I tested this in scratch against every case above and against
    the seed list, a kept line, a text line, comments plus a kept line, and a BOM before a real list:
    `LC_ALL=C grep -v -e '^[^!-~]*#' -e "^[^!-~]*＃" -e "^[^!-~]*﹟" "$list" | LC_ALL=C grep -q '[!-~]'`.
    - The `-e` patterns drop any line whose first printable ASCII character is `#`, or whose first
      visible character is a number sign that NFKC folds to `#`.
    - The second grep then needs a printable ASCII character somewhere in what is left.
    - It refuses too much only for a list whose text is all non-ASCII, and refusing fails closed.
  - Add a BOM-plus-comments list and a no-break-space-only list to the refusal loop in
    `test_consent_add_never_makes_a_list_or_extends_an_empty_one`.
- **N2, Minor. docker/consent-add.sh:42.** An unreadable list (I probed mode 000) gets "missing, empty
  or only comments … Put the real list back first", printed under grep's "Permission denied". It fails
  closed, but following that advice could put an older list over a newer one. Fix: add
  `[ -r "$list" ] || refuse "cannot read $list"` before the check.
- **N3, Minor. Makefile:133.** ssh also exits 255 when the connection drops, or the remote shell dies,
  after the add. "Nothing was added" can then be false. That is the safe direction, because running it
  again only adds a harmless duplicate. Suggested wording: "could not be reached, or the connection
  dropped: check the list".
- **N4, Minor. Makefile:135.** `recordings plaud consent` exits 78 whenever the list is still a stop
  (commands.py:226), for example because of a typo line elsewhere. That reaches the Mac as exit 4,
  "reading the list back (above)" failed, although the readback worked and printed "imports stopped: …"
  above. The non-zero exit is right; only the wording is off.
- **N5, Minor. Makefile:139 and first-run.md:1291.** The text says "its count" and "the count", but the
  readback prints two counts:
  - the file's count, `consent list: N IDs`, which the command reads fresh;
  - the worker's count, `read: … (by the worker: N IDs)` (commands.py:176-183, 212-215).
  Only the worker's count lags, so the text should name that line.
- **N6, Minor. docker/deploy-guard.sh:64.** It says to run the first catch-up and the baseline again "(the
  runbook)", but the runbook has no section for either yet: first-run.md:4 says they come with later
  phases. Put it on that phase's list.
- **N7, Minor. scripts/consent_id.py:28.** `_ID_LIKE` runs on the raw text, while the parser reads
  `visible(text)`. So a second ID in fullwidth digits, or split by a zero-width space, still passes; I
  probed both. This is contrived. For parity, apply the regex to `visible(text)`. Also, the comment
  that ruff wrapped inside `return (None  # …)` would read better as a line above it.
- **N8, Minor (process). Body of 8789f9e.** "New tests failed first, against the earlier script" names
  failing lines for the script's behaviour only. It doesn't show the Makefile's step-message
  assertions failing against the earlier Makefile, or the new runbook test failing.
  - Otherwise both commit messages are well formed. Each has the `type(scope): 🤖 …` subject, a
    `Plan:` line and `Co-Authored-By`, and both pass `scripts/commitmsg.py`. The rest of the docs
    record is true.

## Checked and fine

- **Temporary files, the list's content and its mode:**
  - On a refusal, and when `mv`, `cat` (standing in for a full disk) or `chmod` fails, no temporary
    file is left and the list is unchanged byte for byte, under sh and dash.
  - Modes 400, 600, 640, 444 and 604 are all kept.
- **Comment rule:** for ASCII input, the sh check matches `lstrip().startswith("#")`. It differs from
  the parser in only two ways, and both are safe:
  - A list with CR-only line endings and a `\v` before a `#` are refused where the parser would allow
    the add.
  - A list holding only `\v` is extended, but it stays stopped on a typo line.
- **Makefile and ID:**
  - The exit codes are mapped correctly: `a && b || exit 3; c || exit 4`.
  - `ID` reaches the shell only as `"$$ID"` from the environment (`override ID := $(value ID)`, then
    `export ID`), and `$(shell …)` in it never runs.
  - ssh carries only the checked 32-hex `$$id`.
- **Docs:** I grepped for "new list", "0600", "any editor" and "renam". Nothing is stale. README:99-105,
  first-run.md:1270-1293 and Makefile:119-122 match the scripts.

Verdict: changes needed (0 Critical, 1 Important)
