# Area A6: Phase 2's screens, the end-to-end tests, and the live demo: report

Status: **DONE_WITH_CONCERNS**. Everything in A6's scope that Phase 2's data can fill is built and
tested; `make demo-live` works end to end, through make, and stops cleanly on Ctrl-C. Phase 3's
parts of the screens (the breaker, the sweep, Sync everything, held removals, Re-sync) stay empty,
as they should until B1 and B2. The phase-end list's view refactors (rows marked → Phase 2 that
touch `recordings_ui/plaud.py`'s structure) were not done; they are listed under "What's left".

Branch `stage-2b`, from `fa53b79`. Not pushed (the controller pushes).

## Commits

| SHA | Subject |
|---|---|
| `c3038fa` | feat(ui): 🤖 the Sync screen and Status read what the listing writes |
| `ce4865f` | test(plaud): 🤖 stage 2b end to end on the fake, and the leak scan at DEBUG |
| `cef87c7` | feat(demo): 🤖 make demo-live and make e2e-live: the web app and worker on a fake Plaud |
| `c44afa6` | fix(demo): 🤖 make demo-live stops everything on a terminal's Ctrl-C, whatever repeats it |

Every message passed `scripts/commitmsg.py` and carries BUILD-FAST's trailer. `make check` was green
before each; `make e2e` (145 passed) before `c3038fa` and `cef87c7` (both change
`recordings_ui/plaud.py`); `make e2e-live` (11 passed) before `cef87c7` and the fourth commit.

## What was built, by spec section and scenario

### §15.1, §15.2, §15.3: the views' readers (`c3038fa`, `packages/ui/src/recordings_ui/plaud.py`)

All by the names the earlier reports gave; every reader still imported inside its function (Pyodide).
- **The equations** from `K_COMPARE` (with "from the listing at 14:20"), the archive's equation, the
  consent line's "N on Plaud now" (none during a consent stop). `listed` is true once a compare ran.
- **The summary**, shared by both screens: "All here · checked 14:20 · next sync 14:40" with its
  explanation, by §15.2's rule (`K_LAST_GOOD_LISTING` under two intervals old, nothing waiting or
  missing but parked rows); else "2 waiting · 1 missing · checked 14:20 · next sync 14:40" (a guess:
  the spec gives no line for that); else the problem, as before.
- **Not here yet** from `Cache.read_only` (A3), each title checked again before it leaves (P13,
  `_Read.shown_title`): none for a review row, during a consent stop the worker has read (even before
  the tick's purge), under a hold with Plaud's IDs changed, for an ID `plaud_listing` keeps out or
  re-keyed, an ID the cache last saw consent-listed, or a title an enforced keep-out word starts.
  **C5.15's screen half: built.** Park on "failed 3 times" (`compare.FAILED`); "Import all N" and "N
  more need your review" from the compare.
- **Kept out** from the cache's excluded rows: by consent, title, "was kept out by title", re-keyed;
  Release only on was-title and re-keyed rows (`release.kept_by`'s kinds); a released row reads
  "Released · comes in at the next sync (14:40)" with Undo (`compare._released`). Never a title.
- **A hold** (`K_HOLD`: sentence, Accept, or why refused; `CONSENT_FIRST` under a consent stop with
  Plaud's IDs changed). **Under a hold, and during a rate limit's wait, Sync now and the imports are
  off** with the reason (A5's handoff). Spec §15.1 now says Sync now too.
- **Gone from Plaud** (new `PlaudDB.gone_rows`; "N in the last 7 days") and **Parked**
  (`plaud_listing.parked_at`, and `PlaudDB.parked_recordings` for B2's); Details' "Gone from Plaud
  since".
- **Flags** gain the re-key match (`rekeyed`, detail `of` and A4's `by: "audio"`), by Plaud ID with
  date and duration.
- **Status:** the failing line's "Last good listing 11:40."; a small shrink (`hold.shrink_line`); the
  sign-in number ("works (last used …) · access token expires … · refresh token expires …", or
  "needed since …: At the Mac: `make plaud-sign-in`."); Needs attention's sign-in needed (A1's
  `K_SIGN_IN` and blocker) and **"expiring within 7 days"** (BD3: the refresh token's own expiry, else
  the access token's), a hold, each re-keyed flag by Plaud ID, a rebuild's review count (`K_REBUILT`
  and the review rows not here); the last good listing, gone, failing (failing checks plus "failed 3
  times") and parked counts; Details' census, form change, malformed rows, the last 20 runs
  (`ops_runs`).
- **The stored compare after a sync's imports** (`compare.after_imports`, from `sync._settle`): what
  came in moves to here and what the import kept out to excluded, with no title, so the screens show
  what the run left. Without it, the equation said "3 waiting" after Sync now had brought them in. The
  report keeps the compare it ran on, so the replies count as before.
- Found by `make e2e-live` (`cef87c7`): once the catch-up opens the gate, the worker's last `gate`
  turn kept the summary on "waits for the first catch-up" until the first sync ended; the views now
  drop it.

### §20 criteria 4 and 7, §21's flow test (`ce4865f`, `packages/core/tests/test_flow_2b.py`)

An audio-router fixture imported as 2a does, then `plaud baseline`, `sync --dry-run --list`, `sync
--catch-up` and the worker's real loop on a fake clock, with §21's faults (a 503 on the listing, an
HTML 200 envelope, a link expiring once, a note link dropping, a 429 waited out), the Sync screen's
requests (Compare, Import selected, Keep it here, Release's preview and Let it in), a late keep-out
and Release. **C4.01 to C4.04 with the worker's real turn** (refresh keeps pings; a 429 from the token
endpoint is an outage; revoked plus a refused refresh stops pings with no Plaud call while paused;
a new sign-in pings again). **C7.01 built:** every log record at DEBUG, the CLI's output, the pings,
Status's views, the replies, and state.db's 2b tables and `ops_runs` scanned for the fake tokens,
`Bearer`, `X-Amz-` and every title; the Sync screen's view for tokens and links. The worker sends no
push. A mutation (a title in the importer's log line) fails it. `--full` waits for B2.

### `make demo-live` (`cef87c7`, fixed in the fourth commit; `scripts/demo_live.py`)

- The fake Plaud server and a fake Healthchecks in its own process; `token-import -` with the fake's
  sign-in; the first catch-up (`--before-catch-up` skips it; the `catch-up` command runs it);
  `recordings worker` and `recordings-ui` in sessions of their own.
- Account: six ordinary + one private (imported by the catch-up), one consent-listed, one
  `noarchive …`; three arrivals (one private) added after the worker's first scheduled sync.
- **Never real:** one folder from `mkdtemp` under `$TMPDIR`, its own `config.toml`, the children's
  environment without `RECORDINGS_*`, `RESTIC_*` or proxies; `refuse_unless_scratch` loads that config
  as the children will and refuses unless every path is inside the folder and Plaud, the token URL and
  the ping are its own loopback fakes.
- Prints the URL and what to try. Commands: `new`, `hold` (six recordings here vanish: the next sync
  holds), `back`, `rename` (Release offered), `late` (a late keep-out), `signout`/`signin`, `tick`
  (the scheduled sync now), `catch-up`, `dump`, `help`, `quit`.
- Ctrl-C: web, then worker, SIGTERM (SIGKILL after 15 s), the fakes closed, the folder removed unless
  `--keep`. Tried by hand through `make demo-live PORT=…` with Ctrl-C to the whole process group: the
  first version left the worker running (uv forwards SIGINT a second time); fixed and tested.
- `make help` lines for `demo-live` and `e2e-live`; `PORT` and `ARGS` pass through.

### `make e2e-live` and its CI job (`cef87c7`)

`packages/ui/tests/e2e_live/` (marker `e2e_live`, out of the default run), driving the same script:
before the catch-up only Compare works and the catch-up opens the gate; Compare lists what waits and
Kept out shows no title; Import selected with its toast; the arrivals cue after a scheduled tick;
Release's question with the title, Let it in, Undo, then Sync now; a hold turns Sync now and the
imports off and Accept clears it (six gone rows); Status shows a refused sign-in; the worker killed
("The Plaud worker isn't running"); Ctrl-C leaves no stray process. CI's `e2e-live` job (actions
pinned as `e2e`'s) uploads the demo's logs on failure. The breaker and Re-sync items wait for B1/B2.

### Docs

README (stage 2b paragraph, `make demo-live`, `make e2e-live`); CLAUDE.md's Toolchain lines; the user
guide's "Trying the Plaud sync on the Mac", "The Sync screen, top to bottom", "Status's Plaud sync
panel" and the hold's buttons; the runbook's **"When the sync is held"** and **"When imports stop:
the consent list"**, linked from the alert table (pinned by `test_runbook.py` against `hold.py`'s
phrases); main spec §9.1's buckets and token paragraphs and §9.3's deferred row; the 2b spec's §15.1,
§21, C4.01–C4.04, C5.15 and C7.01 rows, three Revisions lines; the plan's Now line; eight guesses
rows. Runbook section 6's sign-in step and sections 7 and 8 were already written by A1 and A5.

## Tests

- New: `packages/core/tests/test_plaud_screens.py` (11, a worker on the fake), `test_flow_2b.py` (1),
  `test_demo_live.py` (3: the guard's refusals, the children's environment, a start and a double
  Ctrl-C); `test_plaud_views.py` (+1 gate test; the copied constants pinned); `test_runbook.py` (+1);
  Vitest +1 (Sync now off under a hold); `packages/ui/tests/e2e_live/` (11).
- Last runs: `make check` pytest 2,405+ passed twice (local and `TZ=UTC`), Vitest 459, ruff clean,
  gitleaks clean; `make e2e` 145 passed; `make e2e-live` 11 passed (about 5½ minutes: the real
  pacing). The one warning is the existing `test_demo_guard.py` fork warning. No flaky test seen.
- Mutation checks: a title in a log line fails the flow test; the second-Ctrl-C guard removed fails
  the start/stop test.

## Guesses (rows in `docs/superpowers/plans/2026-11-01-stage-2b-guesses.md`)

The waiting summary line; Sync now off under a hold and its reason's words; Release only on was-title
and re-keyed rows; a re-keyed match shown three times (flag, Kept out row, Needs attention); the
stored compare refreshed after imports; BD3's expiry choice; demo-live running the catch-up at start
and adding the arrivals after the first scheduled sync; the gate turn dropped once the gate is open.

## Questions for Dan

1. **Decision 16's gap** (known, untouched): a new ID listed beside a still-listed consent-listed or
   kept-out ID with the same start and duration is not caught.
2. **Sync now under a hold** is off (A5's run refuses it anyway); §15.1's reply list still has "Held:
   see Status" for it. Keep it off, or enabled so the press answers?
3. **The arrivals cue** counts new and private apart (Phase 1's rule): two arrivals, one private, read
   "1 new · 1 private from Plaud". Would "2 new (1 private)" read better?
4. **Release on a row still kept out by its title:** not offered (the preview is refused). Show it so
   the refusal explains itself?
5. **`make demo-live`'s start:** it runs the catch-up for you (about 25 s). Start before the catch-up
   by default instead (`ARGS=--before-catch-up` does it now)?

## What's left

- **The phase-end list's view refactors** (→ Phase 2, not done): splitting the views into
  `plaud_views.py` with one `_from_state` helper; fields instead of display text
  (`summary.says_failing`, `health.cause_kind`, `panel.unreadable`); conditions vs attention
  (Simplicity 5: the consent stop is still in both); `arrivals` and `recording_plaud` saying when
  state.db can't be read (P15); `error.message` on the Sync screen and Status; G2 (Details for a late
  consent listing); the Sync screen's split by section and one `RequestButton`; one e2e helpers module
  (`e2e_live/conftest.py` copies `console` again); `usePlaud`'s local pending entry.
- Phase 3's parts of the screens (below), `--full` in the flow test, and e2e-live's breaker and
  Re-sync items.
- The worker-killed e2e test waits on the heartbeat's 90 seconds; e2e-live takes ~5½ minutes.

## What B1, B2 and B3 need from me, by name

### B1 (checks, the sweep, the breaker)

- **The breaker on both screens:** `SyncView.breaker` / `SyncPanel.breaker` (types.ts `SyncBreaker`:
  sentence, accept, code_fix, fields) are still `None` in `_sync_from` and `_panel_from`; add a
  `_Read.breaker_view()` beside `_Read.hold_view()`, reading `K_BREAKER`. The UI's Accept these
  changes is wired and gated by `HANDLED`.
- **Status's numbers and details:** `numbers["sweep"]` ("every recording checked within the last 21
  hours (oldest …)"), `details["fields"]`, `["changes_24h"]`, `["named_share"]` are set in
  `_panel_from`'s `panel["numbers"].update` / `panel["details"].update`.
- **Updated on Plaud:** `_updated` already reads `updated` events' `detail.parts`.
- **Details:** `recording_plaud`'s `checked` and `next_by` from `PlaudDB.check_row` (it already reads
  `gone_since` there).
- **The flow test** (`test_flow_2b.py`): add a check pass and its faults (volatile fields, renames);
  the leak scan covers whatever it logs. **e2e-live's breaker item:** add a command to
  `scripts/demo_live.py`'s `Demo.command` (e.g. a field that appears on every envelope, through the
  fake account) and a test in `packages/ui/tests/e2e_live/test_live.py`.
- `compare.after_imports` runs only after imports; a check's `add_snapshot` doesn't touch the compare.

### B2 (Re-sync, parking, Sync everything, held removals)

- `full_estimate` (still `FULL_UNKNOWN`) and `numbers["full"]` from `K_FULL`; `numbers["held"]` (held
  removals, §14.7) in `_panel_from`.
- **Parked recordings** already show: `_parked` reads `PlaudDB.parked_recordings()` (`plaud_checks.
  parked_at`) as `{"id": recording_id, "kind": "recording"}`, and Unpark sends `recording_ids`; only
  `check_request` and the worker's `_parking` need to take recording IDs.
- **`--full` in the flow test**, and **e2e-live's Re-sync on a selection** (a `demo_live.py` command
  that renames a speaker on the fake account's envelope, then the Library's selection bar).

### B3 (the guide, the setup page, the Pages demo)

- **The canned 2b screens from demo-live's account:** `make demo-live ARGS=--keep`, play the scenario
  wanted (`new`, `hold` then Sync now for a hold, `rename` then Compare for a Release row, `late` then
  Sync now for a flag), type `dump`: it writes `<scratch>/plaud-sync.json` in exactly
  `demo/plaud-sync.json`'s shape (`{"sync": sync_view, "panel": sync_panel, "arrivals": arrivals}`).
  Every title in it is the demo's own invention. Then edit by hand (§15.5 wants it hand-written):
  replace archive recording IDs (Updated, Gone, failing checks, flags) with `demo/archive`'s, set
  times to taste, keep `demo_view`'s overrides in mind (it forces every button off, `handled`, the
  demo notes, `keep_here`/`park`/`release` false, Accept off), and run `make pages pages-test`.
- The user guide now has the screens' sections and "Trying the Plaud sync on the Mac"; B3 polishes.
- The README's stage 2b paragraph is in; the setup page can link the runbook's new hold and
  consent-stop sections.
