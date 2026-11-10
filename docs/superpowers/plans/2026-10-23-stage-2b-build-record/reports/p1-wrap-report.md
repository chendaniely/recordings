# P1 wrap: report

Status: **DONE_WITH_CONCERNS** (one deliberate departure from the brief, below; nothing blocks
area A1).

Branch `stage-2b`, from `77b4d19`. Not pushed (the controller pushes).

## Commits

| SHA | Subject |
|---|---|
| `46ad104` | fix(doctor): 🤖 while Phase 1 runs, the worker's missing sign-in is a note, and doctor exits 0 |
| `69633c7` | fix(ui): 🤖 at 320 px a larger text size scrolls the tab row, never the page |
| `da6b10a` | feat(ui): 🤖 Status's "fails every check" line: its contract from plaud_checks, with Re-sync and Park |
| `0393e2c` | fix(ui): 🤖 a tab that takes focus scrolls fully into its row, focus ring and all |
| `8a3c79a` | docs(config): 🤖 the template's [plaud] block says again what the patterns and the consent list do |
| `49bb87d` | docs(spec): 🤖 the Scenarios table's 35 Phase 1 rows say built |
| `c57c0f9` | docs(plan): 🤖 phase end step 4: the phase-end list sorted, and Phases 2 and 3 next |

Every commit's trailer is `Plan: docs/superpowers/plans/2026-10-23-stage-2b-phase-1.md Phase end`,
each message passed `scripts/commitmsg.py`, and `make check` passed before each. The three UI
commits also passed `make test-js`, `make build` and `make e2e` (145 passed each time).

## 1. Dan's three phase-end answers

### §7, doctor's "sign-in needed" (`46ad104`; spec §17.2; C8.03, C10.02)

- `doctor.py`: a missing token store adds `SIGN_IN_NOTE` ("Plaud sign-in: comes with stage 2b's
  next phase; until then the worker stays idle (runbook section 6)") to `notes`, no problem, so
  `doctor --role worker` exits 0 for it. **The one-line switch for area A1:**
  `SIGN_IN_IS_A_PROBLEM = False` → `True`; `SIGN_IN_NEEDED` holds the problem's text, to be
  reworded to name `make plaud-sign-in` (spec §17.2). A test flips the switch and gets the problem.
- `scripts/compose_smoke.py`: `WORKER_PROBLEMS` loses "sign-in needed"; a new `WORKER_NOTES`
  looks for the note among doctor's notes. The smoke still expects exit 78 (the missing list and
  ping URL). `test_deploy_template.py` pins both lists to doctor's text (CI alone runs the smoke).
- Runbook section 6: the intro, step 6 and step 7's expected lines ("reports no problem and exits
  0", with the note's words); `test_runbook.py` pins them.
- Spec §17.2 says so for Phase 1, with a Revisions line.

### §5, 320 px under a larger text size (`69633c7`, `0393e2c`; spec §15.1)

- `app.css`, the phone block only: the tab row gets `overflow-x: auto`, `min-width: 0`, and the
  tabs `flex: none`. Because a scrolling row clips both ways (MDN: a `visible` overflow-y computes
  to `auto`), the tabs' 44 px no longer overhang by a negative margin: they sit in the row's 4 px
  padding with their focus rings, and the row's `-10px -4px` margins keep the bar's size. At the
  normal text size nothing moves (the existing 320 px test passes unchanged, and now also asserts
  the row has nothing to scroll).
- Found by eye after the first commit (screenshots and a probe): Chromium did not scroll the row
  when a partly visible tab took keyboard focus, so Tab to Status left "Sta" cut off with its ring
  clipped. `0393e2c` adds an `onFocus` `scrollIntoView({block, inline: "nearest"})` per tab (called
  with `?.`, since jsdom lacks it) and `scroll-padding-inline: 4px` on the row.
- The e2e test (`test_tagging.py`,
  `test_at_320_px_a_larger_text_size_scrolls_the_tab_row_never_the_page`) adds
  `html, body { font-size: 20px }` through `page.add_style_tag` (the product CSS untouched; the
  body's text is set in px, so only a text-zoom-like scale stresses the bar). It asserts: no
  horizontal page scroll (19 px before the fix), one row, the row really overflows, the theme icon
  whole, each tab scrolled into view inside the row and 320 px, 44 px, and pressable; Tab from
  Library brings Status fully inside the row's padding; Status and Add open their pages.

### §6, Status's "fails every check · Re-sync · Park" (`da6b10a`; P3, §15, §15.2; J4, J5)

- **Contract:** `SyncPanel.failing_checks: FailingCheck[]`, `{recording_id, since}` (types.ts and
  the view model), oldest first; `[]` when none, before the 2b tables, and when the records can't
  be read. A row whose `recording_id` isn't an archive ID raises ValueError, so `_guarded` makes
  every view say its records can't be read, by class only (the existing corrupt-row pattern),
  never a shorter list.
- **Departure from the brief (please confirm):** the brief said to choose a `plaud_state` key.
  I read `plaud_checks` instead, through a new `PlaudDB.failing_checks()`: `failures >=
  FAILING_AFTER` (3, a new constant in `checks.py`, spec §10.2 and §14.6's "3 failures in a row"),
  `parked_at IS NULL`, `gone_since IS NULL`, ordered by `failing_since`. Phase 1 created that table
  with exactly these columns (decision 3), and Phase 3's checks write them anyway, so a second copy
  in `plaud_state` could only drift from it (a parked recording still listed as failing). It is
  documented in the view model's module docstring, `sync_panel`'s docstring, `types.ts` and spec
  §15.2. If the controller wants the `plaud_state` key after all, only `_failing_checks` changes.
- **Buttons:** the kinds exist (`resync`, `park` in `requests.KINDS`), so both are drawn and send
  them with `recording_ids` (as the Sync screen's Unpark does for a parked recording). Neither is
  in `HANDLED`, so both are drawn off with `not_yet`, said once and named by aria-describedby, and
  off while the gate is closed (the worker refuses both then, §9.3), the same rule as Accept. The
  line is drawn only for a non-empty list, right under the summary.
- **Demo:** `demo/plaud-sync.json` lists one synthetic failing recording (the Apollo 13 demo
  recording, the one with a Plaud source), `numbers.failing` 2 (it and the Not here yet row that
  "failed 3 times"). It stays valid (the demo test checks every key); `test_sync.py`'s demo tests
  now cover the line's buttons (disabled with the demo's note, 44 px at 320, axe in both themes).
- Spec: P3's row, §15's intro and §15.2 name the exception and where the line reads, with a
  Revisions line.

## 2. The phase-end list (last commit)

All 57 rows now say where they are: 10 fixed (with commits), 4 answered (Dan's three, and the
council's yes to Task 13's question, each with what built it), 1 closed (as before), 30 → Phase 2,
3 → Phase 3, 8 → Later (each with its reason), 1 replaced (Simplicity 2: no Phase 2 plan). Two rows
fixed now, small and local, in their own commits: the template's `[plaud]` comments (`8a3c79a`)
and the Scenarios table's 35 Phase 1 rows set to `built` (`49bb87d`).

Task 25: step 4 ticked; steps 5 and 6 marked "replaced by building Phases 2 and 3 straight
through, Dan 2026-11-01, Q&A §8" (left unticked, since not done); 7 and 8 left open. Step 10's
text now says doctor notes the sign-in. The outlines gained two to-dos: Phase 2's sign-in bullet
names the doctor switch; Phase 3 gains "the checks write `plaud_checks.failures` and
`failing_since`, and the `resync`/`park` handlers enable Status's line". **Now:** "Phases 2 and 3,
built straight through (Q&A §8): area A1, open."

## Tests

Final runs: pytest 2,000 passed (twice, local and `TZ=UTC`), Vitest 458 passed, ruff clean,
gitleaks clean, `make e2e` 145 passed. New tests: doctor's note and exit 0, the switch, the smoke's
lists; runbook section 6's lines; the 20 px text e2e test (with its keyboard half); the failing
list's rule (`test_plaud_tables.py`); the panel's `failing_checks` and its corrupt row
(`test_plaud_views.py`); four Vitest cases for the line. No flaky test was seen in any run.

## From audio-router, and from the spec alone

Nothing from audio-router: this area touches no Plaud shape. "3 failures in a row" is the spec's
(§10.2, §14.6).

## Questions and guesses for Dan

- Re-sync and Park on Status's line act on every recording the line counts, in one press (the
  spec's "2 recordings fail every check · Re-sync · Park" reads that way). Phase 3 may prefer one
  row per recording with its own buttons if the list grows; the contract carries each ID, so
  either works.
- The `plaud_checks` departure above (for the controller rather than Dan; no decision changes).

## What's left

- Task 25 steps 7 (CHANGELOG) and 8 (PR), at the overnight build's end; step 9 (Dan's merge).
- Area A1 onwards: the 30 → Phase 2 and 3 → Phase 3 rows, and the outlines' bullets, including
  flipping `doctor.SIGN_IN_IS_A_PROBLEM` with `make plaud-sign-in`, and Phase 3's writes and
  handlers for the "fails every check" line.
