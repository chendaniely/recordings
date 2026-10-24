# Stage 2b, Phase 1 (the groundwork): implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build everything in stage 2b that needs no spike result, and the spike script, so Dan can run the spike sessions while the rest is built: the consent grammar and the keep-out word, the Plaud client copied from `audio-router`, the 2b tables, the auto-private marker, late consent listings and their flags, the gate, the `worker` process with its ping rule, the request path, the worker's container, doctor and backup changes, and the whole interface against a view model.

**Architecture:**
- **Core (`recordings`):** pure modules first (`plaud/keepout.py`, `plaud/privacy.py`, `plaud/health.py`'s rules), then the tables (`state.py`'s schema, `plaud/checks.py`, `plaud/cache.py`), then what writes through the `Writer` (`plaud/flags.py`, `plaud/baseline.py`, `Writer.add_snapshot`, the marker inside `Writer.reconcile_plaud`), then `worker.py`, which ticks every 2 seconds, applies late consent listings, claims request rows and concludes each scheduled turn. Its sync turn is a stand-in until Phase 2; the gate stays closed in Phase 1, because nothing can run the first catch-up yet.
- **UI (`recordings_ui`):** `recordings_ui/plaud.py` checks and inserts request rows (on `asyncio.to_thread`, never on the tag threads) and builds the views from `state.db` and `plaud-cache.db`, read-only. It imports no client, token or sync module. The browser gets a Sync screen (`#/add`), Status's sync panel, the Library's arrivals cue and the Details lines, each drawn from the view model; demo mode and Pages draw a hand-written canned view.
- **Operations:** a third container, `worker`, from the same image; doctor gains its role; backups carry the consent folder; the deploy guard also asks the `plaud` lock.

**Tech Stack:** Python 3.14 on the server and 3.12 on Pyodide 0.27.7 (Pages), stdlib `urllib` and `sqlite3` (no new Python dependency), shiny 1.8.0, shinyreact 0.1.0 (exact pins), React 19.2 (external), Tailwind v4, shadcn/ui, Vitest 3.2.7, pytest 9.1.1, pytest-playwright 0.10.0, Docker Compose, gitleaks 8.30.1.

**Spec:** `docs/superpowers/specs/2026-10-23-stage-2b-design.md` (approved 2026-10-23; Part I binding, Dan's decisions 1 to 11, with 10 and 11 winning over 4, 5, 8 and 9 where they differ), with its parent `docs/superpowers/specs/2026-10-08-recordings-design.md`. Read the spec alongside this plan.

**Now:** Task 1: Project tooling, open.

***

## Decisions made while planning (review these)

Each is a reading of the spec where it leaves a choice open. Code comments cite them as "2b phase 1 plan, decision N".

1. **Stage 2b ships as three phases, each its own plan, branch and PR, split by the spikes** (the controller's ruling, from spec §18: "Each spike runs before the tasks that use its result", and §4's two halves). **Phase 1, the groundwork (this plan):** everything that uses no spike result, plus the spike script, so Dan can run the sessions whenever he likes, on branch `stage-2b`. **Phase 2, Plaud flows in:** begins with spike sessions A and B, then its plan is written against their results (the sign-in, the fake server's shapes, the listing, compare, hold, import, first catch-up); it ends §4's first half. **Phase 3, keeps up:** begins with spike session C (S9), then edit-sync (§14); it is §4's second half. Each phase deploys and rolls back by its tag.
2. **Phase 1 builds the whole interface against the view model** (§15's build order; §18: "the UI against the view model" goes ahead in parallel). Buttons whose worker handler comes in Phase 2 or 3 send their request as built. The worker refuses them with the gate's reply ("The first import is a supervised run on the server (runbook)"), and the gate can't open in Phase 1, since nothing can run `--catch-up` yet. A kind that passes the gate with no handler yet (`compare`) is answered "error: `NotImplementedError`" until Phase 2 registers it.
3. **Every 2b table is created in Phase 1 with every column the spec names, Phase 2's and 3's included** (Task 6). §9.5 allows tables only and no `SCHEMA_VERSION` bump, and `CREATE TABLE IF NOT EXISTS` can't add a column, so a later column would need a migration and break `make rollback`. A column a spike may leave unused (`change_marker`, S5) is nullable. A later phase that needs more adds a table.
4. **`sources/base.py` moves to Phase 2,** with `sources/plaud.py`. Main spec §9.0's `Source` (`candidates()`, `fetch()`, and 2b's `check()`) has no implementation or caller in Phase 1, so a protocol alone would be untested code.
5. **Phase 1's CLI** is `recordings worker [--alive]`, `plaud consent`, `plaud keep-out-words [--drop]`, `plaud park [--undo]`, `plaud review` and `plaud baseline` (its privacy part, §14.5). `plaud release` needs the listing's current title (Phase 2); `plaud held` reads held removals that checks write (Phase 3); `baseline --all`, `check`, `token-import`, `sync …`, `resync`, `accept-listing` and `breaker reset` wait for their phases.
6. **The fake Plaud server's transport is built in Phase 1, its shapes in Phase 2.** Task 4's client tests need an HTTP server, so `packages/core/tests/fake_plaud.py` gets §10.6's storage port (step 2's links) and request log (step 4) now, with routes each test sets. The account, listing, envelope and sign-in shapes (steps 1, 3, 5 and 6) need S1, S5 and S8. Phase 1's client tests use the field names the copied client reads today.
7. **Links may be plain `http` only to the loopback, and only while `api_base` is the loopback too** (Task 4). §10.1 refuses every non-`https` link, but the fake's storage port is plain `http` on `127.0.0.1`. With an `https` API base, an `http` link is refused whatever its host, and a test proves it.
8. **Every main spec amendment goes in Task 2; the other files change with their code** (the controller's ruling). Spec §19 says "The plan's first task applies these". The main spec is a design document, and approving the 2b spec is what makes its old text wrong, so Task 2 applies all of §19's "The main spec" list, each marked "(Dan, 2026-10-23)" or "(stage 2b design, 2026-10-23)" as §19 says. §19's "Other files" (the README, the runbook, CLAUDE.md, the archive's format docs, the config template) describe the running app, so each changes in the task whose code makes the old text wrong, Phase 2's and 3's in theirs; decision 7's wording and CLAUDE.md's privacy rule are in Task 2, because approval alone makes them true. Spike results are added to the main spec as they arrive (§19).
9. **`backup.py` accepts folders in `[backup] extra_paths`** (Task 16). `run_backup` refuses any extra path that isn't a file (`backup.py:375`), and the restore test and the free-space estimate assume files. Spec §12.1 and §17 put the consent folder there. `/state/manifests` joins `extra_paths`, the runbook and doctor's warning in Phase 3, when Sync everything first writes a manifest: a missing extra path refuses every backup.
10. **Phase 1 can be deployed.** The runbook's section 6 ("Deploy 2b") is written in Task 17 without its sign-in step. A deployed Phase 1 worker stays idle and healthy, says "sign-in needed", applies late consent listings, and keeps the gate closed. Phase 2 adds the sign-in step and sections 7 and 8.
11. **`FakeNtfy` is the fake Healthchecks.** Its docstring already says the dead-man pings land there (`conftest.py:483`), so the ping tests use the `ntfy` fixture.
12. **The ping's HTTP status comes from a new `alerts.ping_url(url, kind="success", *, message="") -> int | None`.** Spec §16 asks for "a `ping_deadman` that takes the URL", and Status shows "last ping failed 14:40 (HTTP 404)", which needs the status. `ping_deadman(ac, …)` keeps its signature and calls `ping_url` with `ac.deadman_url`, so 2a's callers are untouched.
13. **The worker's tick opens and clears the `consent` blocker as it re-reads the list,** as well as each run does (§16's table names "a consent stop" and "a run that reads the list cleanly"). Status then shows a typo within seconds, and the check stops being pinged at once, gate or no gate.
14. **`Writer.reconcile_plaud` records the marker, the `retagged-private` event and a late keep-out's flag itself** (Task 9). §13.2 has every `Writer` that reconciles judge privacy, so a `web` reindex must leave the same note as the worker; §9.5's "written by: worker" for `plaud_events` describes the usual writer.
15. **The reasons an automatic `private` records** (`why` in the event's detail, fixed phrases): "title changed" (§13.1 case 2), "the sync's record was rebuilt" (case 4 once `rebuilt_at` is set), "first check" (case 4 before any rebuild, which is the baseline over 2a's imports), "patterns changed" (case 5). Details maps them to "its Plaud title changed", "the sync's record was rebuilt", and "its title matched a private pattern". After the 90 days events are kept, Details says "Tagged private by the sync" without a reason.
16. **Open privacy flags live in `plaud_events`** (`open = 1`), one per recording through a partial unique index (P15). "Never fetched again" comes from `plaud_listing.kept_out_at` on any of a recording's canonical IDs, or the consent list as it is now. **Keep it here** is kept durably in `plaud_checks.kept_here_at`, so a later title with the word never flags again, even after its event ages out.
17. **Blockers are `plaud_state` rows named `blocker:<name>`** (`sign-in`, `consent`, `hold`, `breaker`, `private-unwritten`), each `{"since", "detail"}`; a blocker clears by deleting its row. **The snapshot mark is one upserted key, `snapshot_mark`** (`{"count", "last_at"}`): "mark rows exist" means the key exists.
18. **The rebuild check scans the archive only when no snapshot mark exists,** reading each recording's `source/plaud-*.json` and stopping at the first whose `_meta.tool` is `"recordings"` (§22 leaves the method to the builder).
19. **Park applies to a recording here** (`plaud_checks.parked_at`) **or to a Plaud ID that failed to import** (`plaud_listing.parked_at`), since §11.5 lets Dan park an import that "failed 3 times".
20. **`recordings_ui/plaud.py` holds the request checks and inserts and the views** (as §9.6 says); the SQL that both `web` and `worker` run on `plaud_requests` lives in core's `recordings/plaud/requests.py`, which imports no client, token or sync module either.
21. **A Release preview's title reaches only the asking session.** When a `release-preview` request is done, `web` reads that one title from the cache's preview row and sends it in that session's `plaud_result` message. It is never written to `plaud_requests` or logged (§9.3, §12.2).
22. **Arrivals count from the page's load or its last Refresh,** whichever is later, so the cue empties when Refresh brings the rows in.
23. **The canned demo view is `demo/plaud-sync.json`,** hand-written. `recordings_ui.settings.from_env(demo=True)` copies it beside the archive's temporary copy, and `scripts/build_pages.py` copies it beside the vendored `demo/archive`, so both find it as `<archive>/../plaud-sync.json`.
24. **The spike script runs as `uv run python scripts/plaud_spike.py <spike>`,** with no PEP 723 block, because it imports `recordings`. S2 and S4 take `--token-url` (https only), which session A's S1 finds; the script never guesses it. `--own-clip` (S9 only) skips the keep-out check and nothing else: consent-listed, private-tagged and pattern-matching recordings are refused even then.
25. **`PLAUD_LOCK = "plaud"` sits in `locks.py` beside `OPS_LOCK`,** and `plaud/runlock.py` holds it through its own `Locks` instance. `plaud review` announces `plaud-review`, a job §9.4's list lacks; the deploy guard doesn't wait for it.
26. **The worker records the configured keep-out word as enforced at every start** (`plaud_state.keep_out_words`), so changing the word releases nothing (§12.2). `plaud keep-out-words --drop` refuses the configured word: "that is the configured keep_out_word: change [plaud] keep_out_word first".
27. **New auto-private patterns are found by version.** Whichever process judges privacy (the worker, or a `Writer` reconciling) records a new pattern version whenever `[plaud] auto_private_patterns` differs from the newest recorded one. The worker runs the case 5 pass over the archive at start when a new version was recorded.
28. **Doctor reports the token file's presence only in Phase 1;** its expiry and refresh token need S1's format (Phase 2).
29. **`validate` reports an orphan snapshot as a warning** through a new `warnings` list (`selfdoc.validate(root, *, deep=False, warnings=None)`). `recordings validate` prints warnings and still exits 0, and the restore test ignores them.
30. **No test can reach Plaud.** A root `conftest.py` autouse fixture makes `socket.getaddrinfo` refuse any host ending in `plaud.ai` (Task 4), so a mistake fails loudly instead of calling Plaud.

## Global Constraints

Every task's requirements include these.

- **Never call Plaud.** No `mcp__plaud__*` tool, and nothing that reaches `platform.plaud.ai`, in tests, scripts or by hand. Every test uses a fake (`fake_plaud.py`, `FakeNtfy`). `scripts/plaud_spike.py` is written and tested against fakes, and runs only in Dan's supervised sessions.
- **Never read a real archive, a `private/` folder, `~/.secrets`, any `.env*`, `~/.ssh`, or a token file.** Never read a private recording's transcript or notes. **Fixtures are synthetic:** "`packages/core/tests/conftest.py` builds Plaud envelopes and an `audio-router` archive by hand. Never copy real data in." (CLAUDE.md). Synthetic Plaud IDs only, such as `f"{n:032x}"` and `"of_" + f"{n:032x}"`.
- **The repo is public:** no personal paths, names, tokens or real IDs in code, tests, docs or commit messages.
- **Python only through uv** (`uv sync`, `uv run …`); never pip, never the system or Homebrew Python. This phase adds no Python dependency. **Node 22** from `packages/ui/frontend/.nvmrc` (`nvm use`, `npm ci`); no global installs; npm packages pinned exactly.
- **Archive rules (CLAUDE.md, spec §6):** "Every write goes through `recordings.writer.Writer`." `recording.json` changes only through `Writer.mutate`; media, `source/` and `renditions/` are write-once. "Writers, backups and the mirror refuse an archive without `archive.json`." Tests make archives with `recordings.init.init_archive` (or the `writer` fixture).
- **Privacy (CLAUDE.md):** "a recording is private if a tag is `private` or under `private/`, in any capitalisation, read after NFKC and without invisible characters" (`recordings.models.is_private_tag`). "The private tag lands before any source or output does", with decision 1's one exception (Task 2 writes it into CLAUDE.md). The sync only ever adds `private` (`by: auto`), never removes it.
- **No title outside the app's own views** (P13): "None ever for a consent-listed recording; for one kept out by title, or waiting for review after a rebuild, only inside Release's own question or its preview." No title in `state.db` beyond the merge-base revisions (§9.5): title hashes only, and only for recordings in the archive.
- **Fixed phrases (§15.6):** "Logs, Status, alerts, `ops_runs` and request results use fixed phrases, with counts, IDs and exception class names. Never a title, a token, a URL from Plaud, or exception text." "Plaud jobs log class names only, even at `--debug`: no `exc_info`." "A Plaud ID appears as the first 8 hex characters of the SHA-256 of its canonical form." The phrases are §15.6's, word for word.
- **Rollback stays a tag (§17.3):** `state.db` gains tables only, with `SCHEMA_VERSION == 1` and `MIGRATIONS == {}`; `recording.json` gains no field (`extra="forbid"`, `models.py:56`).
- **Tables are keyed by canonical ID** (§9.5): `plaud_listing` by `canonical_plaud_id` (`recordings.refs.canonical_plaud_id`), and every other Plaud ID a table keys on likewise. Requests to Plaud use the ID exactly as the newest listing gave it.
- **Locks (§9.4):** "`plaud` allows one sync at a time", "taken through its own `Locks` instance, as `ops` is"; held for one run, never between runs; the CLI waits up to 60 seconds, then exits 75. Recording locks only around local work, never across a Plaud call, a download or a wait.
- **The worker's `Writer`:** `Writer.open(cfg, docs=False, lock_timeout=10)`. "A `LockTimeout` on a recording is 'busy'": retried at the next run, neither a failure nor an attempt.
- **Exit codes (§9.6):** "0 ok; 1 failure; 2 partial (the listing was good, but some recordings failed or aren't here); 69 Plaud unreachable or rate limited; 75 busy; 77 sign-in needed; 78 configuration or the consent list. `--json` prints counts, IDs and fixed phrases, never a title."
- **Numbers (§22, change one only with a stated reason):** the tick 2 s; the heartbeat 30 s; `--alive` 90 s and 15 minutes (30 while `starting`); request expiry 1 hour; requests and the check log kept 7 days, events 90 days (open flags until resolved); caps 20,000 Plaud IDs and 1,000 recording IDs for `resync`; Healthchecks period 20 minutes and grace 2 hours; the Release preview 10 minutes.
- **The UI (CLAUDE.md):** "React stays external", `www/ui.js` is build output; "Every output is subscribed in `App`" with a `useShinyOutputStatus` line; "Every message handler and input lives in `App`", mounted once; requests "only ever sent by a person's action: never on load, from a URL or its hash, or from storage"; "Never `stopPropagation` a click"; `PHONE` is `(max-width: 760px)` and the CSS blocks move with it. New Python under `recordings_ui` and `recordings` must import on Pyodide 3.12 (ruff `target-version = "py312"`).
- **Check the docs, never write from memory** (CLAUDE.md's table: shinyreact, shadcn/ui, brand.yml, Tailwind v4). Say in the commit what you checked; a gap goes in `docs/upstream-issues.md`. Never report anything upstream. Spec §22's "to check during implementation", by owning task: `urllib`'s `add_unredirected_header` and a refusing opener like `alerts.py`'s (Task 4); WAL for `index.db` on its named volume, opened by `web` and `worker` (Task 6); `docker compose up --wait` honouring the worker's health check (Task 17); Shiny reading a pending request every 2 seconds for one session, and `send_message` (Task 20); `asyncio.to_thread` in the pinned Shiny (Tasks 14, 20).
- **The checks (the template's *Each task's loop*, step 2):** `make check` before every commit that touches code: lint, the suite (pytest and Vitest), the Python suite again under `TZ=UTC`, and `make leaks`. Tasks that change the browser also run `make e2e`; tasks that change the Pages path run `make pages pages-test`. Format first: `uv run ruff format .`.
- **Shell:** pytest's temporary folders on macOS resolve under `/private/var`; never paste one into a command. In a `grep` pattern write `privat`, not the full word followed by a slash.
- **Tiers and reviews** (the template's *Reviews*): Tier 1, if wrong it could take the system down, lose or leak data, or silently stop alerting: Matt Pocock's `code-review`, two axes in two subagents, plus the security checklist, at most one re-review. Tier 2, feature code: the same two axes; fix Critical and Important. Tier 3, docs, display, configs, test-only: a self-review under the two headings.

### The commit format

Every commit uses one of these; each commit step in the tasks gives its message in full.

- **Build or fix:** subject `type(scope): 🤖 what changed` (types: build, chore, ci, docs, feat, fix, perf, refactor, revert, style, test). The body says what changed and why, and for a new test the output line that showed it failing.
- **Close:** `docs(plan): 🤖 close Task N: <title>`, with the lines `Drift:`, `Rulings:`, `Phase-end:`, `Forward:`, `Tier:`, `Rounds:`, `Findings:`, each filled or `none`.
- **The trailer paragraph ends the message, trailers only, no blank line between:**

  ```text
  Plan: docs/superpowers/plans/2026-10-23-stage-2b-phase-1.md Task N
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

- **How:** write the message to `.superpowers/sdd/2026-10-23-stage-2b-phase-1/commit-msg.txt` (git-ignored; agents can't write under `.git/`), check it with `uv run --script scripts/commitmsg.py .superpowers/sdd/2026-10-23-stage-2b-phase-1/commit-msg.txt` (from Task 1 on; Task 1's own first commit checks after the copy), commit with `git commit -F .superpowers/sdd/2026-10-23-stage-2b-phase-1/commit-msg.txt`, then `git log -1 --format='%(trailers)'` must show `Plan:`. Stage by path; never `git add -A` or `.`, never `--no-verify`, never amend. This repo has no commit hooks, so the check runs by hand at every commit.

## Review Focus

The five inputs the spec implies but its tests don't exercise, most likely first. Each has its test in the task that owns the code.

1. **A consent list saved with CRLF line endings, a UTF-8 BOM, or a non-breaking space around the dash** (a Windows editor, a phone, a paste). A person expects the IDs read exactly as from a plain file; a BOM must not turn line 1's ID into a typo or a kept line. Test: Task 3, `test_crlf_bom_and_nbsp_lines_read_as_plain_ones`.
2. **The consent list changing while the worker reads it** (an editor or `sed -i` writing in place, a copy still landing). A half-written file must never be read as "exclude fewer": a read counts only when the file's inode, size and time are the same before and after it, else the previous list stays in force and the file is read again at the next tick. Test: Task 13, `test_a_list_that_changes_while_read_is_read_again_next_tick`.
3. **A clock put right after running ahead** (an NTP correction): a `last_started` in the future must not hold back every scheduled turn. A stamp in the future counts as long ago, as `ops.due` does. Test: Task 13, `test_a_last_started_in_the_future_counts_as_long_ago`.
4. **The Sync screen's Copy ID pasted into `make consent-add ID=…`.** Copy ID copies the list's own form (`` - `of_…` — ``); a person expects that to add the ID, never a typo line that stops every import. Test: Task 18, `test_consent_add_accepts_the_copy_id_form`.
5. **A request that can't be saved** (a full or read-only state disk). P6 promises an answer within a second: the press gets "The request couldn't be saved: state.db can't be used" at once, never a spinner or a traceback. Test: Task 14, `test_a_request_that_cant_be_saved_answers_at_once`.

## File map

```
Makefile, CLAUDE.md, README.md, config.example.toml, .gitignore, .dockerignore   (Tasks 1, 2, 7, 18)
scripts/commitmsg.py (1), scripts/plaud_spike.py (5), scripts/consent_id.py (18), scripts/build_pages.py (24)
docker/compose.yml, deploy.example.conf, recordings.service (17); ops-busy.py, deploy-guard.sh, consent-add.sh (18)
scripts/compose_smoke.py (17)
demo/plaud-sync.json (19)
docs/runbooks/first-run.md (2, 5, 8, 11, 12, 17, 18); main spec and tagging-a spec (2 and as named)
packages/core/src/recordings/
  plaud/keepout.py (3)  plaud/client.py (4)  plaud/checks.py, plaud/cache.py (6)  plaud/settings.py (7)
  plaud/flags.py, plaud/commands.py (8)  plaud/privacy.py (9)  plaud/baseline.py, plaud/runlock.py (10)
  plaud/health.py (11)  plaud/gate.py (12)  plaud/requests.py (14)
  plaud/consent.py (3)  plaud/reconcile.py (9)  state.py (6)  index.py (6)  locks.py (10)  config.py, alerts.py (7)
  writer.py (9, 12)  selfdoc.py (12)  worker.py (11, 13, 14)  cli.py (8, 13)  doctor.py (15, 16)  backup.py (16)
  format/FORMAT.md (9)
packages/core/tests/
  fake_plaud.py (4)  test_repo_tools.py (1)  test_docs.py (2)  test_keepout.py (3)  test_plaud_client.py (4)
  test_plaud_spike.py (5)  test_plaud_tables.py (6)  test_plaud_settings.py (7)  test_plaud_flags.py (8)
  test_plaud_cli.py (8, 10, 12, 14)  test_plaud_marker.py (9)  test_plaud_baseline.py (10)
  test_plaud_health.py (11)  test_plaud_gate.py (12)  test_worker.py (13)  test_plaud_requests.py (14)
  test_make_targets.py (18); and the existing test_doctor.py, test_backup.py, test_deploy_template.py, ...
conftest.py (root: no Plaud network, 4)
packages/ui/src/recordings_ui/plaud.py (14, 19), status.py, runtime.py, app.py, settings.py, views.py (19, 23),
  shiny_app.py (20)
packages/ui/tests/ test_plaud_imports.py (4), test_plaud_requests_ui.py (14), test_plaud_views.py (19),
  test_plaud_session.py (20), test_template_settings.py (7), test_demo_guard.py, test_build_pages.py,
  e2e/test_sync.py, pages/test_pages_site.py (24)
packages/ui/frontend/src/ types.ts (19), plaud/usePlaud.tsx (20), components/SyncPage.tsx, SyncSummary.tsx,
  ReleaseDialog.tsx, TopBar.tsx, lib/nav.ts (21), components/SyncPanel.tsx, StatusPage.tsx (22),
  components/HeadNotes.tsx, SelectionBar.tsx, PlaudDetails.tsx, TagsPage.tsx (23), App.tsx, app.css (20 to 24)
```

## Tasks

### Task 1: Project tooling

**Tier:** 3 · **Scenarios:** none

Dan's standing preferences ask for `make help`; the template's mechanical checks are `make check`, `make test-utc` and `make leaks`; every commit in this plan is checked by `scripts/commitmsg.py`.

**Files:**
- Create: `scripts/commitmsg.py` (copied from Dan's plan-driven-development skill's `scripts/commitmsg.py`; the controller gives its location at dispatch, and no path is written anywhere)
- Create: `packages/core/tests/test_repo_tools.py`
- Modify: `Makefile` (a `## ` description on every target; `help`, `check`, `test-utc`, `leaks`; `.PHONY`)
- Modify: `CLAUDE.md` (Toolchain: `make help`, `make check`, `make test-utc`, `make leaks`, and the commit-message check)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `make help`: every target with its `## ` line, GNU make 3.81 only (a `grep`/`awk` over `$(MAKEFILE_LIST)`).
  - `make check` = `lint test test-utc leaks`. `make test-utc` = `TZ=UTC uv run pytest`. `make leaks` = `gitleaks git --redact --no-banner .`
  - `uv run --script scripts/commitmsg.py <message file>`: exit 0, 1 (problems, each printed), 2 (usage). Its header docstring says "copied from Dan's plan-driven-development skill", unchanged otherwise but for ruff's formatting. `problems(message: str) -> list[str]` as in the skill.

**Steps outline:**
- [ ] **Step 0: Record the base:** `mkdir -p .superpowers/sdd/2026-10-23-stage-2b-phase-1 && git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-1-base`
- [ ] **Step 1: Copy the script** into `scripts/commitmsg.py`, add the origin line to its header, `uv run ruff format scripts/commitmsg.py`, `uv run ruff check scripts/commitmsg.py`.
- [ ] **Step 2: Commit the copy,** checked by itself: `chore(repo): 🤖 copy the commit-message check from the plan-driven-development skill`.
- [ ] **Step 3: Failing tests,** `packages/core/tests/test_repo_tools.py` (the script loaded with `importlib.util.spec_from_file_location`, as `test_rangeserver.py` does):
  - `test_commitmsg_accepts_a_task_commit`: a `feat(plaud): 🤖 …` message with this plan's trailer paragraph has no problems.
  - `test_commitmsg_refuses_a_plan_line_after_a_blank_line`: a blank line between `Plan:` and `Co-Authored-By:` gives "isn't parsed as a trailer".
  - `test_commitmsg_refuses_a_close_without_its_headings`: a close subject missing `Forward:` is refused, naming it.
  - `test_commitmsg_names_its_origin_and_no_path`: the header holds "copied from Dan's plan-driven-development skill" and no `/Users/` or `~/`.
  - `test_make_help_lists_every_phony_target`: `make help`'s output names each `.PHONY` target.
  - `test_make_check_runs_lint_the_suites_utc_and_leaks`: `make -n check` shows `ruff check`, `pytest`, `TZ=UTC` and `gitleaks git --redact --no-banner .`
- [ ] **Step 4: Run them; watch the Makefile ones fail:** `uv run pytest packages/core/tests/test_repo_tools.py -q`. Expected: the two `make` tests fail (`No rule to make target 'help'`).
- [ ] **Step 5: Code:** the Makefile's `help`, `check`, `test-utc`, `leaks` and a `## ` line on every target; CLAUDE.md's Toolchain lines.
- [ ] **Step 6: Checks:** `make check`.
- [ ] **Step 7: Commit:** `build(make): 🤖 make help, make check, test-utc and leaks`.
- [ ] **Step 8: Review, look forward, close** (the controller runs it).

### Task 2: Decision 7's wording, and what approving the spec changes

**Tier:** 3 · **Scenarios:** C11.01

Decision 7 refines when tagging may start; approving the spec also changes CLAUDE.md's privacy rule (§13.3) and makes the main spec's records of Dan's decisions wrong. These change now, so every later task argues from documents that agree.

**Files:**
- Create: `packages/core/tests/test_docs.py`
- Modify: `README.md` (decision 7's line, §19), `docs/runbooks/first-run.md` (section 4's 2026-10-22 sentence; "Rolling back, until the first real tag"; section 2 step 2's ntfy bullets, its token bullet and the `ntfy_topic` line marked optional for later, the expected file list and the checklist row saying "the ntfy topic, if set up", and a short "Setting up ntfy, later" section, all per spec §17.5 and decision 9)
- Modify: `docs/superpowers/specs/2026-10-15-tagging-a-design.md` (§1: a note that decision 7 refined when tagging starts, with "refining 2026-10-22")
- Modify: `docs/superpowers/specs/2026-10-08-recordings-design.md`: **every item of spec §19's "The main spec" list**, in §19's own words, each marked "(Dan, 2026-10-23)" or "(stage 2b design, 2026-10-23)" as §19 says (decision 8). Among them: §2 (the grace period no longer carries over); §3 (three containers; one writer means one machine); §5; §6.1 (the `_meta` stamp); §7.4 ("Everything comes in, with one exception" becomes two, and §13.3's exception sentence); §9.1 (the grace paragraph replaced by decision 1); §14 (the dead-man's switch becomes two checks, per decisions 4, 5, 8, 9 and 10, as §19 words them); §19 (step 7's disposable window ends at the first tag after step 7 and its backup); §20 stage 2b ("and grace" goes, "2b is done when" becomes spec §20's list, "If 2b runs long" becomes the three phases); §22 (decision 19 replaced, 20 and 35 amended, 31 gains the token's location); and the rest of the list. Go through §19's list item by item and tick each off in the commit body.
- Modify: `CLAUDE.md` (the Privacy bullet, word for word from spec §13.3)

**Interfaces:**
- Consumes: nothing.
- Produces: the documents every later task cites. No code.

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-2-base`
- [ ] **Step 1: Failing tests,** `packages/core/tests/test_docs.py` (each file's text with whitespace collapsed to single spaces, because the README's old sentence spans a line break):
  - `test_decision_7_is_worded_as_refined` (parametrised over `README.md`, the runbook and the tagging part A spec): "until stage 2b's import dry run is done" is absent, and "refining 2026-10-22" is present.
  - `test_claude_md_carries_decision_1s_exception`: CLAUDE.md holds "One deliberate exception (stage 2b, Dan, 2026-10-23)".
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_docs.py -q`. Expected: the README case fails on the old sentence.
- [ ] **Step 3: Docs:** every change listed under Files, in the spec's own words.
- [ ] **Step 4: Checks:** `make check`.
- [ ] **Step 5: Commit:** `docs: 🤖 decision 7's refined wording, and the main spec, runbook and CLAUDE.md as the 2b spec's approval changes them`.
- [ ] **Step 6: Review, look forward, close** (the controller runs it).

### Task 3: The consent grammar and the keep-out word

**Tier:** 1 · **Scenarios:** C5.10

Spec §12.1's grammar (2a's meanings, plus the `of_` and upper-case forms, kept lines listed, typos failing closed) and §12.2's forgiving word match. Pure functions, used by the worker, doctor, the CLI, the spike script and, from now on, the 2a import.

**Files:**
- Create: `packages/core/src/recordings/plaud/keepout.py`
- Modify: `packages/core/src/recordings/plaud/consent.py` (`load_consent_list` reads through `keepout.parse_consent`; 2a's `ENTRY_RE` kept as `LEGACY_ENTRY_RE`, only to name lines 2a's parser missed)
- Test: `packages/core/tests/test_keepout.py`; `packages/core/tests/test_import_run.py` (the existing consent tests still pass; one more for the new forms)

**Interfaces:**
- Consumes: `recordings.models.HIDDEN_CODEPOINTS`; `recordings.refs.canonical_plaud_id`.
- Produces (`recordings/plaud/keepout.py`, standard library and `recordings.models`/`recordings.refs` only):
  ```python
  WORD_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
  DEFAULT_WORD = "noarchive"
  def fold(text: str) -> str  # NFKC, drop category Cf and HIDDEN_CODEPOINTS, then casefold()
  def valid_word(word: object) -> bool  # a str matching WORD_RE in full
  def keep_out_match(title: str | None, words: Iterable[str]) -> str | None  # the first word (as given) the title matches, by §12.2's three steps; None for None or ""
  LineKind = Literal["exclusion", "kept", "typo", "comment", "blank", "text"]
  @dataclass(frozen=True)
  class ConsentLine:
      number: int                 # 1-based
      kind: LineKind
      canonical_id: str | None    # exclusion and kept lines: the ID, canonical; otherwise None
  @dataclass(frozen=True)
  class ConsentList:
      ids: frozenset[str]         # the exclusions' canonical IDs
      lines: tuple[ConsentLine, ...]
      stop: str | None            # None, or the fixed phrase of a consent stop
      @property
      def readable(self) -> bool: ...          # stop is None
      @property
      def typo_lines(self) -> tuple[int, ...]: ...
      @property
      def review_lines(self) -> tuple[int, ...]: ...   # the kept lines: every line holding an ID that isn't an exclusion
  STOP_MISSING = "the consent list is missing"
  STOP_UNREADABLE = "the consent list can't be read"
  STOP_EMPTY = "the consent list is empty"
  STOP_NO_IDS = "the consent list holds no IDs"
  def typo_phrase(numbers: Sequence[int]) -> str  # "the consent list has 2 lines that look like IDs but can't be read (lines 14 and 22)"; one line: "has 1 line that looks like an ID but can't be read (line 14)"
  def parse_consent(text: str) -> ConsentList
  def read_consent(path: Path | None) -> ConsentList   # never raises: unset, missing, unreadable, empty, no exclusions or a typo is a stop
  @dataclass(frozen=True)
  class FileSignature:
      inode: int
      size: int
      mtime_ns: int
  def signature(path: Path | None) -> FileSignature | None   # None when it can't be stat'ed
  ```
  - **Line precedence:** comment (`#` after optional spaces), then exclusion, then kept, then typo, then text. A line's "first word" is what follows the optional bullet (`-` or `*`, then spaces) and backtick. An exclusion is an ID (`(of_)?` and 32 hex, any case) then an optional backtick and spaces, then a dash (`—`, `–` or `-`) or the end. A kept line holds an ID that isn't an exclusion: one followed by other words, by a non-hex, non-dash character in the same word (`3fa9…:`), mid-sentence, or after a `+` or `1.` bullet. A typo's first word holds 24 or more hex digits, with or without `of_`, but isn't a valid ID (31 or 33 hex digits, or a non-hex character among them).
  - `consent.load_consent_list(path) -> frozenset[str]` raises `ConsentListError(list.stop)` on a stop, else returns `ids`. `consent.LEGACY_ENTRY_RE` is 2a's `ENTRY_RE`, unchanged.

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-3-base`
- [ ] **Step 1: Failing tests,** `packages/core/tests/test_keepout.py`:
  - `test_an_id_alone_or_then_a_dash_is_an_exclusion_in_any_form`: bare, `of_`, `OF_`, upper case, in backticks, after `-` or `*`, then `—`, `–`, `-` or a note, are all exclusions with the same canonical ID.
  - `test_an_id_followed_by_words_is_a_kept_line_and_listed`: `ID kept: she agreed later`, `- ID (fine to keep)`, `ID:`, `ID,` are kept lines, absent from `ids`, present in `review_lines`.
  - `test_an_id_in_a_sentence_or_after_plus_or_number_bullets_is_listed_not_excluded`.
  - `test_31_or_33_hex_or_a_non_hex_among_them_is_a_typo_that_stops_the_list`: `stop == typo_phrase([2, 5])`, naming line numbers only, never the text.
  - `test_comments_and_blank_lines_are_ignored`.
  - `test_missing_unreadable_empty_or_idless_lists_stop`: each gives its `STOP_*`; `read_consent(None)` stops too.
  - `test_crlf_bom_and_nbsp_lines_read_as_plain_ones` (Review Focus 1): the same list with CRLF, a leading BOM and NBSP around the dash gives the same `ids` and no typo.
  - `test_the_keep_out_word_matches_the_spec_examples`: `NoArchive: coffee`, `No archive`, `no-archive`, `[noarchive]`, `🔒 noarchive`, `10-21 noarchive` and a zero-width-prefixed `noarchive` match; `noarchived` and `No archives` don't.
  - `test_fold_drops_format_and_hidden_characters_and_casefolds`: `fold("ｐｒｉｖａｔｅ") == "private"`; a fullwidth `ｎｏａｒｃｈｉｖｅ` title matches.
  - `test_an_earlier_word_still_matches`: with words `("noarchive", "skipme")`, a title starting `skipme` returns `"skipme"`.
  - `test_valid_word_is_1_to_32_letters_digits_dash_or_underscore`.
  - In `test_import_run.py`: `test_the_2a_import_now_reads_of_and_upper_case_exclusions`.
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_keepout.py -q`. Expected: `ModuleNotFoundError: recordings.plaud.keepout`.
- [ ] **Step 3: Code:** `keepout.py` as above; `consent.py` re-pointed, its docstring saying the grammar moved and what `LEGACY_ENTRY_RE` is for.
- [ ] **Step 4: Checks:** `make check`.
- [ ] **Step 5: Commit:** `feat(plaud): 🤖 the consent grammar reads of_ and upper-case IDs, lists kept lines and fails closed on typos; the keep-out word match`.
- [ ] **Step 6: Review, look forward, close** (the controller runs it).

### Task 4: The Plaud client, copied

**Tier:** 1 · **Scenarios:** C7.02

Spec §10.1: a copy of `audio-router`'s client with the `of_` fix and the leak-proof rules. Page size and pacing stay as copied, marked for Phase 2 (S5, S10); the refresh `POST` and the current-user read come with the token store in Phase 2 (S1, S2, S4).

**Files:**
- Create: `packages/core/src/recordings/plaud/client.py` (copied from `audio-router`'s `src/audio_router/archive/sources/plaud.py`; its header names the `audio-router` commit actually copied, 7254bae or newer, and the MIT relicensing, main spec §19; the controller gives the checkout's location at dispatch)
- Create: `packages/core/tests/fake_plaud.py` (decision 6)
- Modify: `packages/core/tests/conftest.py` (the `plaud_server` fixture)
- Modify: `conftest.py` at the root (decision 30's `no_plaud_network` autouse fixture)
- Create: `packages/core/tests/test_plaud_client.py`
- Create: `packages/ui/tests/test_plaud_imports.py`
- Modify: `CLAUDE.md` (a new bullet: "The Plaud client is read-only and runs only in the worker. `recordings_ui` never imports the client, the token store or the sync."; Phase 2 adds the `tokens.json` sentence)

**Interfaces:**
- Consumes: `recordings.refs.canonical_plaud_id`; `recordings.__version__`.
- Produces (`recordings/plaud/client.py`, stdlib `urllib` only):
  ```python
  API_BASE = "https://platform.plaud.ai/developer/api"   # as copied
  PAGE_SIZE = 100            # as copied; Phase 2 sets it from S5
  CONNECT_TIMEOUT = 10.0
  READ_TIMEOUT = 60.0
  STALL_SECONDS = 60.0       # no bytes for this long
  MIN_RATE = 50_000          # bytes per second on average after the first minute
  USER_AGENT: str            # f"recordings/{__version__}"
  class PlaudError(Exception): ...           # every client error; a fixed message per class, raised `from None`
  class PlaudAuthError(PlaudError): ...      # "sign-in needed"
  class PlaudForbidden(PlaudError): ...      # "Plaud refused access (403)"
  class PlaudUnavailable(PlaudError): ...    # "Plaud unavailable" (5xx, a timeout)
  class PlaudOffline(PlaudError): ...        # "offline" (no connection)
  class PlaudRateLimited(PlaudError):        # "rate limited"
      retry_after: float | None
  class PlaudBadResponse(PlaudError): ...    # a short body, broken JSON, HTML, the wrong shape
  class PlaudRedirectRefused(PlaudError): ...  # an API call answered with a redirect
  class PlaudLinkRefused(PlaudError): ...    # a link that isn't https, holds a control character, or has an unknown scheme
  class IncompleteDownload(PlaudError): ...  # short, empty or stalled
  class TokenSource(Protocol):
      def access_token(self) -> str: ...            # PlaudAuthError when there is none
      def refresh(self, rejected: str) -> str: ...  # after a 401: a new access token, or PlaudAuthError
  @dataclass(frozen=True)
  class Listed:
      listed_id: str             # exactly as the listing gave it
      title: str | None          # None: the row had no title field
      created_at: datetime | None
      start_at: datetime | None
      duration_ms: int | None
      serial: str = ""
      @property
      def canonical_id(self) -> str: ...
      @property
      def recorded_at(self) -> datetime | None: ...   # start_at, else created_at
  @dataclass(frozen=True)
  class Page:
      rows: tuple[Listed, ...]
      malformed: int             # rows with no ID or no title field
  class PlaudClient:
      def __init__(self, tokens: TokenSource, *, api_base: str = API_BASE,
                   monotonic: Callable[[], float] = time.monotonic,
                   stall_seconds: float = STALL_SECONDS, min_rate: int = MIN_RATE) -> None: ...
      def get_json(self, path: str) -> Any: ...       # GET with Authorization to the API host only; refuses every redirect; a 401 calls tokens.refresh(rejected) once and retries once
      def list_page(self, page: int, page_size: int = PAGE_SIZE) -> Page: ...   # no ID pattern check: IDs are opaque
      def envelope(self, listed_id: str) -> dict: ...  # files/{listed_id}, the ID exactly as given; blocks behind a data_link inlined without Authorization; a failed block gets "_data_link_error": "<ErrorClass>"
      def fetch_link(self, url: str) -> bytes: ...     # presigned: checked by check_link; no Authorization; may follow a redirect only to another https URL
      def download(self, url: str, dest: Path) -> int: ...   # streams to dest + ".part", byte count must equal Content-Length, the stall rule, then renames
  def check_api_base(url: object) -> str: ...   # https://, or http:// to the loopback; else ConfigError naming "[plaud] api_base", never the URL
  def check_link(url: object, *, api_base: str) -> str: ...   # PlaudLinkRefused; plain http only to the loopback while api_base is the loopback (decision 7)
  def id_hash(plaud_id: str) -> str: ...        # first 8 hex of the SHA-256 of the canonical form
  def stamp_meta(listed: Listed, tz_name: str) -> dict: ...   # audio-router's keys (recorded_at_utc, recorded_at_local, stamp, timezone, time_source) plus "tool": "recordings"
  ```
- Produces (`packages/core/tests/fake_plaud.py`):
  ```python
  @dataclass
  class Response:
      status: int = 200
      body: bytes = b""
      headers: dict[str, str] = field(default_factory=dict)
  @dataclass(frozen=True)
  class LoggedRequest:
      method: str
      path: str                       # without the query
      host: str                       # "api" or "storage"
      header_names: tuple[str, ...]   # lower-case, sorted; never a value, never a body
  class FakePlaud:
      api_url: str                    # http://127.0.0.1:<port>, the client's api_base
      storage_url: str                # http://127.0.0.1:<port2>, for presigned-style links
      requests: list[LoggedRequest]
      def route(self, method: str, path: str, handler: Callable[[dict[str, list[str]]], Response]) -> None: ...
      def link(self, name: str, body: bytes, *, status: int = 200, headers: Mapping[str, str] | None = None) -> str: ...   # "<storage_url>/<name>?X-Amz-Signature=…&X-Amz-Expires=300"
      def close(self) -> None: ...
  # conftest fixture: plaud_server -> FakePlaud
  ```
- Produces (root `conftest.py`): autouse `no_plaud_network`: `socket.getaddrinfo` raises `OSError("tests never reach Plaud")` for any host ending in `plaud.ai`.

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-4-base`
- [ ] **Step 1: Failing tests,** `packages/core/tests/test_plaud_client.py` and `packages/ui/tests/test_plaud_imports.py`:
  - `test_only_get_reaches_the_fake`: every client path drives the fake; the request log's methods are `{"GET"}`.
  - `test_api_calls_refuse_every_redirect`: a 302 raises `PlaudRedirectRefused`, and the other host gets no request.
  - `test_authorization_goes_only_to_the_api_host`: link requests' `header_names` lack `authorization`.
  - `test_the_listed_id_is_used_in_every_url`: an `of_` listing and a bare one each produce `files/<that exact form>`.
  - `test_a_link_with_a_control_character_or_an_unknown_scheme_raises_without_its_url` (C7.02): `str(exc)` and `repr(exc)` hold no URL, and `exc.__suppress_context__` is True.
  - `test_a_plain_http_link_is_refused_unless_the_api_is_the_loopback`.
  - `test_presigned_links_may_redirect_only_to_https`.
  - `test_errors_are_fixed_classes_raised_from_none`: a 500 and a timeout give `PlaudUnavailable` with a message holding no `http`.
  - `test_a_401_refreshes_once_and_retries_once`: a counting `TokenSource`; a second 401 raises `PlaudAuthError`.
  - `test_a_short_download_or_a_stall_raises_incomplete_download`: a short body, and a trickle under a small `min_rate`; no file left.
  - `test_a_failed_block_records_its_error_class_never_its_text`.
  - `test_listing_rows_without_an_id_or_title_are_counted_malformed`: an `of_` ID passes; no 32-hex filter.
  - `test_check_api_base_accepts_https_and_loopback_http_only`.
  - `test_the_client_holds_no_real_looking_id`: no `(?i)(of_)?[0-9a-f]{32}` or `of_[0-9a-f]+` in `client.py`'s source, comments and docstrings included, outside a short allowed list of synthetic ones (empty if possible).
  - `test_no_test_can_reach_plaud`: `socket.getaddrinfo("platform.plaud.ai", 443)` raises.
  - `test_recordings_ui_never_imports_the_client_the_token_store_or_the_sync`: in a subprocess, every module under `recordings_ui` (walked with `pkgutil`) is imported, and `sys.modules` holds none of `recordings.plaud.client`, `recordings.plaud.tokens`, `recordings.plaud.sync`, `recordings.sources.plaud`.
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_plaud_client.py -q`. Expected: `ModuleNotFoundError: recordings.plaud.client`.
- [ ] **Step 3: Copy the client verbatim, then commit the copy alone,** so the edits are reviewable as a diff: `feat(plaud): 🤖 copy audio-router's Plaud client at <commit>, relicensed MIT`.
- [ ] **Step 4: Code:** the edits: no `FILE_ID_RE` filter; the vendor CLI's refresh replaced by `TokenSource`; one GET request function; the redirect, Authorization and link rules; the fixed error classes `from None`; timeouts 10 and 60; the stall rule; `_data_link_error` as a class name; `PAGE_SIZE` and the absence of pacing marked "Phase 2: S5, S10"; `fake_plaud.py`; the two conftest fixtures; the CLAUDE.md bullet.
- [ ] **Step 5: Checks:** `make check`.
- [ ] **Step 6: Commit:** `feat(plaud): 🤖 the client reads the of_ form, only reads, keeps tokens off links and raises fixed errors`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 5: The spike script and its guard

**Tier:** 1 · **Scenarios:** none (§18's guard test)

Spec §18, "Ordering", step 1. Dan can run sessions A, B and C once this task closes.

**Files:**
- Create: `scripts/plaud_spike.py`
- Create: `packages/core/tests/test_plaud_spike.py`
- Modify: `docs/runbooks/first-run.md` (section 1c points to the spike sessions and holds their results table: the session's date and each outcome, never a recording's date, time or duration)
- Modify: main spec §9.1 (the spike list points to the 2b spec's §18, "(stage 2b design, 2026-10-23)")

**Interfaces:**
- Consumes: `keepout.read_consent`, `keepout.keep_out_match`, `keepout.DEFAULT_WORD`; `reconcile.matches_auto_private`, `reconcile.patterns_from_config`; `client.PlaudClient`, `client.Listed`, `client.id_hash`, `client.TokenSource`; `recordings.archive.Archive`; `recordings.models.is_private`; `recordings.config.load_config`.
- Produces (`scripts/plaud_spike.py`, run as `uv run python scripts/plaud_spike.py <spike> [options]`):
  ```python
  SPIKES = ("s1", "s2", "s3", "s4", "s5", "s7", "s8", "s9", "s10", "s11", "s12")
  REASONS = ("consent", "private", "keep-out", "pattern")
  class SpikeRefused(Exception): ...   # main() prints its fixed phrase and exits 78
  @dataclass
  class Guard:
      consent: frozenset[str]           # canonical IDs; a consent stop raises SpikeRefused before any call
      private_ids: frozenset[str]       # canonical Plaud IDs of archive recordings tagged private
      words: tuple[str, ...]
      patterns: tuple[str, ...]
      refused: collections.Counter[str] = field(default_factory=collections.Counter)
      def allows(self, plaud_id: str, title: str | None, *, own_clip: bool = False) -> bool: ...   # own_clip skips the keep-out check only (decision 24)
      def report(self) -> str: ...      # "refused: 2 (consent 1, private 1)"
  def build_guard(cfg: Config, archive: Archive) -> Guard: ...
  def short(value: str) -> str: ...     # 8 hex of the SHA-256: the only form an ID or any value is printed in
  def spike_folder(home: Path) -> Path: ...   # $RECORDINGS_HOME/spike at 0700; refuses a folder inside the archive, the state folder, /plaud or any backup path
  def main(argv: list[str] | None = None) -> int: ...
  ```
  - Options: `--home <RECORDINGS_HOME>` (the spike folder's parent), `--token-file <path>` (inside the spike folder), `--id <Plaud ID or archive recording ID>` (repeatable; S7, S8, S11, S12), `--token-url <https URL>` (S2, S4 only), `--own-clip` (S9 only).
  - Each spike prints only its §18 print list: counts, yes or no, expiry times, status codes, field names and `short()` prefixes. Never a token, a title, transcript text or a whole Plaud ID. Every spike ends by printing `guard.report()`.

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-5-base`
- [ ] **Step 1: Failing tests,** `packages/core/tests/test_plaud_spike.py` (the script loaded by path; a synthetic archive from `init_archive` and the `writer` fixture; `plaud_server` for every call):
  - `test_the_guard_refuses_a_consent_listed_id_and_counts_it`.
  - `test_the_guard_refuses_a_private_tagged_recording_and_counts_it`.
  - `test_the_guard_refuses_a_keep_out_title_unless_it_is_the_own_clip`.
  - `test_the_guard_refuses_a_pattern_title_even_for_the_own_clip`.
  - `test_a_consent_stop_refuses_before_any_call`: the fake's request log is empty; exit 78.
  - `test_nothing_printed_holds_a_title_a_token_or_a_whole_id`: S5 and S8 against the fake with synthetic titles and tokens; `capsys` output holds none of them.
  - `test_the_spike_folder_must_be_0700_and_outside_the_archive_state_and_plaud`.
  - `test_s2_and_s4_refuse_a_token_url_that_isnt_https`.
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_plaud_spike.py -q`. Expected: the script file is missing.
- [ ] **Step 3: Code:** the script, each spike's steps and print list from §18, S1 to S12.
- [ ] **Step 4: Docs:** runbook section 1c; the main spec's §9.1 spike line.
- [ ] **Step 5: Checks:** `make check`.
- [ ] **Step 6: Commit:** `feat(spike): 🤖 the Plaud spike script, whose guard refuses consent-listed, private and kept-out recordings and counts them`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it). The close tells Dan the spike sessions can be scheduled.

### Task 6: The 2b tables, the cache and `index.db`'s WAL

**Tier:** 1 · **Scenarios:** C8.01, C5.33

Spec §9.5, with decision 3: every table, every column, `SCHEMA_VERSION` still 1.

**Files:**
- Modify: `packages/core/src/recordings/state.py` (`SCHEMA` gains the tables below; `State.forget` takes the recording's Plaud rows; its comment)
- Create: `packages/core/src/recordings/plaud/checks.py`
- Create: `packages/core/src/recordings/plaud/cache.py`
- Modify: `packages/core/src/recordings/index.py` (WAL)
- Test: `packages/core/tests/test_plaud_tables.py`; `packages/core/tests/test_index.py`
- Modify: main spec §6.8 (`state.db` gains the 2b tables with no version bump; the state folder gains `plaud-cache.db`, derived and never backed up; `index.db` is in WAL mode), "(stage 2b design, 2026-10-23)"

**Interfaces:**
- Consumes: `recordings.state.State`; `recordings.refs.canonical_plaud_id`.
- Produces, the schema (appended to `state.SCHEMA`, every statement `IF NOT EXISTS`):
  ```sql
  CREATE TABLE IF NOT EXISTS plaud_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
    plaud_ids TEXT NOT NULL DEFAULT '[]', recording_ids TEXT NOT NULL DEFAULT '[]',
    select_all INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL,                     -- queued | running | done | failed | expired
    queued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
    progress TEXT,                           -- JSON {"done", "total"}
    message TEXT,                            -- a §15.6 reply phrase
    result TEXT);                            -- JSON: counts and IDs, never a title
  CREATE INDEX IF NOT EXISTS plaud_requests_by_state ON plaud_requests (state, id);
  CREATE TABLE IF NOT EXISTS plaud_listing (
    canonical_plaud_id TEXT PRIMARY KEY, listed_id TEXT NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, absent_since TEXT,
    kept_out_at TEXT, kept_out_word TEXT,    -- by a word (§12.2); a late keep-out's too
    rekey_of TEXT,                           -- §11.4 step 6: the absent ID it matched, or '*' for several
    released_at TEXT,                        -- §12.2
    needs_review INTEGER NOT NULL DEFAULT 0, -- §11.6
    import_failures INTEGER NOT NULL DEFAULT 0, next_import_at TEXT, parked_at TEXT,
    title_sha256 TEXT, duration_ms INTEGER, change_marker TEXT);   -- only while its recording is here
  CREATE TABLE IF NOT EXISTS plaud_checks (
    recording_id TEXT PRIMARY KEY, last_checked_at TEXT, last_changed_at TEXT, next_check_at TEXT,
    interval_seconds INTEGER, fast_checks_left INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0, failing_since TEXT,
    settled INTEGER NOT NULL DEFAULT 0, baseline_pending INTEGER NOT NULL DEFAULT 0,
    held_removals TEXT NOT NULL DEFAULT '[]', parked_at TEXT, degraded INTEGER NOT NULL DEFAULT 0,
    gone_since TEXT, kept_here_at TEXT);
  CREATE TABLE IF NOT EXISTS plaud_check_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, recording_id TEXT NOT NULL,
    result TEXT NOT NULL, parts_changed TEXT NOT NULL DEFAULT '[]',
    parts_volatile TEXT NOT NULL DEFAULT '[]', counted INTEGER NOT NULL DEFAULT 0, failure_class TEXT);
  CREATE INDEX IF NOT EXISTS plaud_check_log_by_at ON plaud_check_log (at);
  CREATE TABLE IF NOT EXISTS plaud_private_checked (
    recording_id TEXT NOT NULL, canonical_plaud_id TEXT NOT NULL, title_sha256 TEXT NOT NULL,
    patterns_version INTEGER NOT NULL, checked_at TEXT NOT NULL,
    PRIMARY KEY (recording_id, canonical_plaud_id));
  CREATE TABLE IF NOT EXISTS plaud_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL,
    recording_id TEXT, canonical_plaud_id TEXT,
    detail TEXT NOT NULL DEFAULT '{}',       -- JSON: parts, a fixed "why"; never a title
    open INTEGER NOT NULL DEFAULT 0, resolved_at TEXT);
  CREATE INDEX IF NOT EXISTS plaud_events_by_at ON plaud_events (at);
  CREATE UNIQUE INDEX IF NOT EXISTS plaud_events_one_flag ON plaud_events (recording_id)
    WHERE open = 1 AND kind IN ('kept-out-late', 'consent-late');
  CREATE UNIQUE INDEX IF NOT EXISTS plaud_events_one_unwritten ON plaud_events (recording_id)
    WHERE open = 1 AND kind = 'private-unwritten';
  CREATE TABLE IF NOT EXISTS plaud_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
  ```
- Produces (`recordings/plaud/checks.py`):
  ```python
  NO_SYNC_YET = "No sync has run yet"
  class NoSyncYet(Exception): ...
  def no_such_table(exc: BaseException) -> bool: ...   # sqlite3.OperationalError "no such table"
  EVENT_KINDS = ("imported", "arrived-private", "updated", "retagged-private", "kept-out-late",
                 "consent-late", "private-unwritten", "kept-here", "gone", "released", "rekeyed")
  FLAG_KINDS = ("kept-out-late", "consent-late")
  BLOCKERS = ("sign-in", "consent", "hold", "breaker", "private-unwritten")
  BLOCKER_PREFIX = "blocker:"
  # plaud_state keys (decision 17)
  K_LAST_GOOD_LISTING = "last_good_listing"; K_HOLD = "hold"; K_BREAKER = "breaker"; K_SIGN_IN = "sign_in"
  K_FAILING = "failing"; K_LAST_PING = "last_ping"; K_LAST_STARTED = "last_started"; K_LAST_ENDED = "last_ended"
  K_LAST_TURN = "last_turn"; K_CATCH_UP_DONE = "catch_up_done_at"; K_SNAPSHOT_MARK = "snapshot_mark"
  K_REBUILT = "rebuilt_at"; K_KEEP_OUT_WORDS = "keep_out_words"; K_PATTERNS = "patterns"
  K_PATTERNS_PASS = "patterns_pass"; K_CONSENT = "consent"; K_FULL = "full_sync"; K_RETIRED = "retired_at"
  @dataclass(frozen=True)
  class Marker:
      canonical_plaud_id: str
      title_sha256: str
      patterns_version: int
  @dataclass(frozen=True)
  class ListingRow: ...   # one field per plaud_listing column, same names
  @dataclass(frozen=True)
  class CheckRow: ...     # one field per plaud_checks column, same names
  @dataclass(frozen=True)
  class Event:
      id: int; at: datetime; kind: str; recording_id: str | None; canonical_plaud_id: str | None
      detail: dict; open: bool; resolved_at: datetime | None
  class PlaudDB:
      def __init__(self, state: State) -> None: ...
      # plaud_state (values are JSON)
      def get(self, key: str) -> Any | None: ...
      def put(self, key: str, value: Any, *, now: datetime) -> None: ...
      def put_many(self, values: Mapping[str, Any], *, now: datetime) -> None: ...   # one transaction
      def delete(self, key: str) -> None: ...
      def with_prefix(self, prefix: str) -> dict[str, Any]: ...
      # plaud_listing, by any form of the ID
      def listing(self, plaud_id: str) -> ListingRow | None: ...
      def upsert_listed(self, listed_id: str, *, now: datetime) -> ListingRow: ...   # first sight sets first_seen; listed_id follows the newest form
      def set_listing(self, plaud_id: str, **fields: Any) -> None: ...   # plaud_listing column names only (ValueError otherwise)
      # plaud_checks
      def check_row(self, recording_id: str) -> CheckRow | None: ...
      def set_check(self, recording_id: str, **fields: Any) -> None: ...
      # plaud_events
      def add_event(self, kind: str, *, now: datetime, recording_id: str | None = None, plaud_id: str | None = None,
                    detail: Mapping[str, Any] | None = None, open_flag: bool = False) -> int: ...
      def open_flags(self, kinds: Iterable[str] = FLAG_KINDS) -> list[Event]: ...
      def resolve(self, event_id: int, *, now: datetime) -> None: ...
      def change_kind(self, event_id: int, kind: str) -> None: ...
      def events_since(self, since: datetime, kinds: Iterable[str]) -> list[Event]: ...
      # plaud_private_checked
      def markers(self, recording_id: str) -> dict[str, Marker]: ...
      def set_markers(self, recording_id: str, markers: Iterable[Marker], *, now: datetime) -> None: ...
      def prune(self, *, now: datetime) -> None: ...   # check log 7 days; events 90 days, open flags kept
  ```
  Every reader raises `NoSyncYet` instead of `sqlite3.OperationalError: no such table`.
- Produces (`recordings/plaud/cache.py`), a separate SQLite file Phase 2 writes:
  ```python
  CACHE_NAME = "plaud-cache.db"
  PREVIEW_LIFE = timedelta(minutes=10)
  CACHE_SCHEMA = """
  CREATE TABLE IF NOT EXISTS compare_rows (
    canonical_plaud_id TEXT PRIMARY KEY, listed_id TEXT NOT NULL,
    bucket TEXT NOT NULL,                 -- waiting | missing | excluded
    reason TEXT NOT NULL,                 -- a §15.1 state phrase
    start_at TEXT, duration_ms INTEGER,
    title TEXT,                           -- waiting or missing only; never kept out, consent-listed or under review
    arrives_private INTEGER NOT NULL DEFAULT 0, review INTEGER NOT NULL DEFAULT 0, excluded_by TEXT);
  CREATE TABLE IF NOT EXISTS keyed_out (     -- consent-listed and kept-out IDs, for §11.4 step 6
    canonical_plaud_id TEXT PRIMARY KEY, kind TEXT NOT NULL, start_at TEXT, duration_ms INTEGER,
    last_listed_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS release_preview (
    canonical_plaud_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);   -- "compare": the equations and titles_hidden, JSON
  """
  @dataclass(frozen=True)
  class CompareRow: ...   # one field per compare_rows column
  class Cache:
      def __init__(self, state_dir: Path) -> None: ...      # <state_dir>/plaud-cache.db
      @classmethod
      def read_only(cls, state_dir: Path) -> Cache | None: ...   # None when the file isn't there
      def db(self) -> AbstractContextManager[sqlite3.Connection]: ...   # creates the file and schema on first write
      def compare_rows(self) -> list[CompareRow]: ...
      def summary(self) -> dict | None: ...                 # meta "compare", decoded
      def release_title(self, plaud_id: str, *, now: datetime) -> str | None: ...   # None once older than PREVIEW_LIFE
      def drop_preview(self, plaud_id: str) -> None: ...
  ```
- `Index`: every connection that creates or rebuilds sets `PRAGMA journal_mode=WAL`.
- `State.forget(file)`: also deletes the recording's `plaud_checks`, `plaud_private_checked` and `plaud_events` rows, and clears `title_sha256`, `duration_ms` and `change_marker` on the `plaud_listing` rows of its canonical IDs (read from `plaud_private_checked` before deleting).

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-6-base`
- [ ] **Step 1: Failing tests,** `packages/core/tests/test_plaud_tables.py` and `test_index.py`:
  - `test_schema_version_stays_1_and_migrations_stay_empty` (C8.01): with a `# why:` naming `make rollback`.
  - `test_a_2a_state_db_gains_the_2b_tables_on_open`: its revisions and runs untouched.
  - `test_a_state_db_with_the_2b_tables_still_reads_as_2a_did`: `State.read_only` and 2a's queries work.
  - `test_listing_rows_follow_the_canonical_id_across_a_form_flip` (C5.33): `of_` then bare upper case is one row; `listed_id` follows; `kept_out_at`, `needs_review` and an open flag stay.
  - `test_one_open_privacy_flag_per_recording`: a second open `consent-late` row raises `sqlite3.IntegrityError`.
  - `test_reading_a_missing_2b_table_raises_no_sync_yet`.
  - `test_forget_takes_the_recordings_plaud_rows_and_title_hash`.
  - `test_prune_keeps_open_flags_and_drops_old_rows`.
  - `test_put_many_is_one_transaction`: an error mid-way writes none.
  - `test_the_cache_makes_its_own_schema_and_a_release_preview_expires_after_10_minutes`.
  - In `test_index.py`: `test_index_db_is_in_wal_mode`.
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_plaud_tables.py -q`. Expected: `ModuleNotFoundError: recordings.plaud.checks`.
- [ ] **Step 3: Code:** the schema, `checks.py`, `cache.py`, `State.forget`, the index's WAL.
- [ ] **Step 4: Docs:** main spec §6.8.
- [ ] **Step 5: Checks:** `make check`.
- [ ] **Step 6: Commit:** `feat(state): 🤖 the 2b tables, keyed by canonical Plaud ID, with no schema version bump; the Plaud cache file; index.db in WAL mode`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 7: Plaud settings and secrets

**Tier:** 1 · **Scenarios:** none

Spec §17.4. The `[plaud]` keys gain their readers; `RECORDINGS_PLAUD_TOKEN` and `recheck_days` go; the sync's own dead-man URL arrives, kept apart from the backup's (decision 4).

**Files:**
- Create: `packages/core/src/recordings/plaud/settings.py`
- Modify: `config.example.toml` (the `[plaud]` block word for word from spec §17.4; the secrets comment drops `RECORDINGS_PLAUD_TOKEN` and gains `RECORDINGS_DEADMAN_SYNC_URL`)
- Modify: `packages/core/src/recordings/config.py` (`SECRETS` drops `plaud_token`, gains `deadman_sync_url`; `_index_apart`'s docstring no longer says the token lives in the state folder)
- Modify: `packages/core/src/recordings/alerts.py` (decision 12)
- Modify: `README.md` (the config table: no `RECORDINGS_PLAUD_TOKEN`; `RECORDINGS_DEADMAN_SYNC_URL`)
- Modify: `.gitignore`, `.dockerignore` (`tokens*.json`, `**/.plaud/`, `local.mk`)
- Test: `packages/core/tests/test_plaud_settings.py`; `packages/ui/tests/test_template_settings.py`; `packages/core/tests/test_alerts.py`; `packages/core/tests/test_config.py` (its generic secret tests move from `plaud_token` to `spark_api_key`)
- Modify: main spec §5 (the `[plaud]` keys; secrets gain the sync's dead-man URL), "(stage 2b design, 2026-10-23)"

**Interfaces:**
- Consumes: `keepout.valid_word`, `keepout.DEFAULT_WORD`; `client.API_BASE`, `client.check_api_base`; `reconcile.patterns_from_config`; `consent.consent_list_path`.
- Produces (`recordings/plaud/settings.py`):
  ```python
  DEFAULT_SCHEDULE_MINUTES = 20
  DEFAULT_FULL_SWEEP_DAYS = 7
  DEFAULT_TOKEN_STORE = Path("/plaud/tokens.json")
  BAD_WORD = "configuration: `keep_out_word` is invalid"
  @dataclass(frozen=True)
  class PlaudSettings:
      schedule_minutes: int                 # 0 is off
      auto_import: bool
      full_sweep_days: int
      keep_out_word: str                    # as configured
      keep_out_word_ok: bool                # False: the worker idles, and Status and doctor say BAD_WORD
      auto_private_patterns: tuple[str, ...]
      consent_list: Path | None             # None: unset, a consent stop
      token_store: Path
      api_base: str
  def plaud_settings(cfg: Config) -> PlaudSettings: ...   # ConfigError for a wrong type, a bad api_base or a bad pattern; an invalid keep_out_word is flagged, never raised
  ```
- Produces (`alerts.py`, 2a's code otherwise untouched):
  ```python
  # config.SECRETS["deadman_sync_url"] = SecretSpec("RECORDINGS_DEADMAN_SYNC_URL", "the Plaud sync's own Healthchecks ping URL", 2)
  @dataclass(frozen=True)
  class AlertConfig: ...   # gains deadman_sync_url: str | None = None, checked by _check_url as "RECORDINGS_DEADMAN_SYNC_URL"; __repr__ shows only "set"
  def from_config(cfg: Config, environ: Mapping[str, str]) -> AlertConfig: ...   # also reads deadman_sync_url
  def ping_url(url: str, kind: str = "success", *, message: str = "") -> int | None: ...   # the HTTP status, None when unreachable; never raises
  def ping_deadman(ac: AlertConfig, kind: str = "success", *, message: str = "") -> bool: ...   # unchanged signature: ping_url(ac.deadman_url, …)
  ```

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-7-base`
- [ ] **Step 1: Failing tests:**
  - `test_plaud_settings.py`: `test_unset_plaud_keys_take_the_spec_defaults` (20, true, 7, `noarchive`, None, `/plaud/tokens.json`, the client's API base); `test_an_invalid_keep_out_word_is_flagged_not_raised`; `test_api_base_must_be_https_or_loopback_http`; `test_recheck_days_and_the_static_token_are_gone` (the template has no `recheck_days`; `SECRETS` has no `plaud_token`).
  - `test_alerts.py`: `test_deadman_sync_url_is_checked_under_its_own_name` (a bad URL's `ConfigError` names `RECORDINGS_DEADMAN_SYNC_URL`, never the value); `test_ping_url_returns_the_status` (against `ntfy`: 200, then 404 with `status = 404`); `test_ping_deadman_is_unchanged`.
  - `test_template_settings.py`: rows for `schedule_minutes` (and its unset value 20), `auto_import`, `full_sweep_days`, `keep_out_word`, `token_store`, `api_base` (set explicitly, since the template comments it out), all read through `plaud_settings`; `LATER` loses its Plaud rows; `LATER_SECRETS` loses `plaud_token`; `SECRET_VALUES` gains `deadman_sync_url`.
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_plaud_settings.py packages/ui/tests/test_template_settings.py -q`. Expected: `ModuleNotFoundError: recordings.plaud.settings`.
- [ ] **Step 3: Code:** `settings.py`, `config.py`, `alerts.py`, the template, the ignore files.
- [ ] **Step 4: Docs:** the README's config table; main spec §5.
- [ ] **Step 5: Checks:** `make check`.
- [ ] **Step 6: Commit:** `feat(config): 🤖 the [plaud] settings and their readers; the sync's own dead-man URL; the static Plaud token and recheck_days go`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 8: Late consent listings and the privacy flags

**Tier:** 1 · **Scenarios:** C5.04, C5.10, C5.13

Spec §12.1's late consent listing (applied from the list and the index alone, no Plaud call), P15's one flag per recording, and §12.2's **Keep it here**. The `plaud` CLI begins here.

**Files:**
- Create: `packages/core/src/recordings/plaud/flags.py`
- Create: `packages/core/src/recordings/plaud/commands.py`
- Modify: `packages/core/src/recordings/cli.py` (the `plaud` subcommand, delegating to `commands`)
- Test: `packages/core/tests/test_plaud_flags.py`, `packages/core/tests/test_plaud_cli.py`, `packages/core/tests/test_runbook.py` (the new heading)
- Modify: `docs/runbooks/first-run.md` (a section "Taking a recording out by hand", spec §12.4 word for word)
- Modify: main spec §6.4 ("Delete moves the folder to `trash/`" is the app's Delete; taking a recording out by hand deletes it for good, a by-hand exception like Forget) and §9.1 (the consent list's paragraph as spec §19 words it), "(stage 2b design, 2026-10-23)"

**Interfaces:**
- Consumes: `keepout.ConsentList`, `keepout.read_consent`; `consent.LEGACY_ENTRY_RE`; `checks.PlaudDB`, `checks.FLAG_KINDS`; `writer.Writer`, `writer.SetPlaudFields`, `writer.StaleFile`, `writer.InvalidFile`, `writer.NotTheWriter`; `locks.LockTimeout`; `index.Index.find_by_source(..., strict=True)`; `settings.plaud_settings`.
- Produces (`recordings/plaud/flags.py`):
  ```python
  KEPT_OUT_LATE = "kept-out-late"; CONSENT_LATE = "consent-late"; PRIVATE_UNWRITTEN = "private-unwritten"
  SENTENCES: dict[str, str] = {
      CONSENT_LATE: "On the consent list, but in the archive: take it out by hand (runbook)",
      KEPT_OUT_LATE: "Kept out by title, but already in the archive: take it out by hand (runbook)",
      PRIVATE_UNWRITTEN: "Should be private, but its file was edited outside the app: run `recordings reindex`",
  }
  KEPT_HERE = "Kept here: it stays private, and nothing more is fetched from Plaud for it"
  NOTHING_TO_KEEP = "Nothing to keep: there is no open late keep-out for it"
  @dataclass(frozen=True)
  class LateReport:
      tagged: tuple[str, ...]        # recording IDs that got `private`
      flagged: tuple[str, ...]       # newly flagged, or changed to consent-late
      cleared: tuple[str, ...]       # consent-late flags cleared because the line went (`private` stays)
      unwritten: tuple[str, ...]     # a `private` owed but not written: the private-unwritten blocker opens
      busy: tuple[str, ...]          # LockTimeout: retried at the next tick
      index_unavailable: bool        # the strict lookup couldn't answer: retried at the next tick, and shown
  def flag(db: PlaudDB, kind: str, recording_id: str, *, plaud_id: str | None, now: datetime) -> bool: ...   # one open privacy flag per recording: an open kept-out-late, or a recording gone from Plaud, becomes consent-late; True when added or changed
  def apply_late_consent(writer: Writer, db: PlaudDB, consent: ConsentList, *, now: datetime) -> LateReport: ...   # no Plaud call; nothing when consent isn't readable
  def record_late_keep_out(db: PlaudDB, recording_id: str, plaud_ids: Iterable[str], word: str, *, now: datetime) -> bool: ...   # kept_out_at/word on each canonical ID and one flag, unless plaud_checks.kept_here_at is set; the caller has written `private`
  def keep_here(db: PlaudDB, recording_id: str, *, now: datetime) -> str: ...   # resolves the open kept-out-late flag, a kept-here event, kept_here_at; KEPT_HERE or NOTHING_TO_KEEP; changes no archive file
  def never_fetch(db: PlaudDB, plaud_ids: Iterable[str], consent: ConsentList) -> bool: ...   # any canonical ID consent-listed now, or with kept_out_at
  def missed_by_2a(text: str, consent: ConsentList) -> frozenset[str]: ...   # exclusion IDs 2a's LEGACY_ENTRY_RE didn't read
  ```
- Produces (`recordings/plaud/commands.py`):
  ```python
  EXIT_OK, EXIT_FAILED, EXIT_PARTIAL, EXIT_UNREACHABLE, EXIT_BUSY, EXIT_SIGN_IN, EXIT_CONFIG = 0, 1, 2, 69, 75, 77, 78
  def add_parser(sub: argparse._SubParsersAction) -> None: ...   # `recordings plaud <command>`; each later task adds its command here
  def run(args: argparse.Namespace) -> int: ...
  def cmd_consent(args: argparse.Namespace) -> int: ...
  ```
  `recordings plaud consent [--json]`: holds no lock; prints the ID count, "on Plaud now" (or "unknown until the first listing"), the file's time, when the worker last read it (`plaud_state.consent`, else read now), unreadable lines and lines read as kept, by number, and consent-listed recordings in the archive by archive ID, each marked `missed_by_2a`. Exit 0, or 78 on a consent stop.

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-8-base`
- [ ] **Step 1: Failing tests,** `test_plaud_flags.py` and `test_plaud_cli.py` (synthetic archive, `writer` fixture):
  - `test_a_late_consent_listing_is_tagged_private_and_flagged_without_a_plaud_call`: no token, gate closed, `plaud_server` request log empty.
  - `test_an_already_flagged_or_gone_recording_gets_no_second_flag` (C5.13): its open `kept-out-late` becomes `consent-late`.
  - `test_removing_the_line_clears_the_flag_and_keeps_private`.
  - `test_an_index_that_cant_answer_is_retried_and_shown`: a strict lookup that raises gives `index_unavailable`, and nothing is skipped silently.
  - `test_a_file_edited_outside_the_app_is_reported_unwritten`: `StaleFile` lands in `unwritten`.
  - `test_keep_here_clears_the_flag_and_changes_no_file` (C5.04): the archive's tree hash is unchanged; the flag resolved; `kept_here_at` set.
  - `test_keep_here_refuses_without_an_open_late_keep_out`.
  - `test_a_later_keep_out_title_after_keep_here_never_flags_again`.
  - `test_plaud_consent_lists_kept_lines_by_number_and_2a_misses_by_archive_id` (C5.10).
  - `test_plaud_consent_exits_78_on_a_typo_and_names_line_numbers_only`.
  - `test_no_flag_or_printed_line_carries_a_title`.
  - In `test_runbook.py`: `test_the_runbook_says_how_to_take_a_recording_out_by_hand` (the heading, `rm -rf`, never `trash/`, `reindex --allow-missing`).
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_plaud_flags.py -q`. Expected: `ModuleNotFoundError: recordings.plaud.flags`.
- [ ] **Step 3: Code:** `flags.py`, `commands.py`, `cli.py`'s registration.
- [ ] **Step 4: Docs:** the runbook section; main spec §6.4 and §9.1.
- [ ] **Step 5: Checks:** `make check`.
- [ ] **Step 6: Commit:** `feat(plaud): 🤖 late consent listings are tagged private and flagged once, with no Plaud call; Keep it here; recordings plaud consent`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 9: The auto-private marker

**Tier:** 1 · **Scenarios:** C5.16

Spec §13 and decision 3: the marker replaces 2a's `title_seen` rule for privacy, in every `Writer` that reconciles, so a removed `private` stays removed.

**Files:**
- Create: `packages/core/src/recordings/plaud/privacy.py`
- Modify: `packages/core/src/recordings/plaud/reconcile.py` (`reconcile` takes `privacy: PrivacyInputs | None`; auto-private through `privacy.judge`; `title_seen` keeps only its title role; the docstring's Auto-private bullet rewritten)
- Modify: `packages/core/src/recordings/writer.py` (decision 14: `reconcile_plaud` reads markers, patterns and words, records the markers once the result is written, the `retagged-private` event and a late keep-out; `ReconcileReport` gains two fields; `Writer` and `Writer.open` gain the keep-out word)
- Modify: `packages/core/src/recordings/format/FORMAT.md` (lines 61-64: §13.1's rule, once for each new Plaud title and for a new pattern)
- Test: `packages/core/tests/test_plaud_marker.py`; `packages/core/tests/test_reconcile.py`, `packages/core/tests/test_writer.py` (callers updated)
- Modify: main spec §7.4 (the automatic private tags as spec §13.1; "`recording.json` is the single source of truth" stays true, the marker being the sync's bookkeeping), "(stage 2b design, 2026-10-23)"

**Interfaces:**
- Consumes: `checks.PlaudDB`, `checks.Marker`, `checks.K_PATTERNS`, `checks.K_REBUILT`, `checks.K_KEEP_OUT_WORDS`; `keepout.fold`, `keepout.keep_out_match`, `keepout.DEFAULT_WORD`; `reconcile.matches_auto_private`; `flags.record_late_keep_out`; `settings.plaud_settings`.
- Produces (`recordings/plaud/privacy.py`, no `sqlite3` at import):
  ```python
  def title_sha256(title: str) -> str: ...   # SHA-256 of the UTF-8 bytes exactly as Plaud gave them
  @dataclass(frozen=True)
  class PatternSet:
      version: int
      patterns: tuple[str, ...]
      by_version: Mapping[int, tuple[str, ...]]
      def added_since(self, version: int) -> tuple[str, ...]: ...   # in this set, absent from that version
  def current_patterns(db: PlaudDB, configured: Sequence[str], *, now: datetime) -> PatternSet: ...   # records a new version when the configured list differs from the newest recorded (decision 27)
  Why = Literal["title changed", "first check", "new pattern", "kept out late"]
  @dataclass(frozen=True)
  class Verdict:
      add_private: bool
      why: Why | None
      kept_out_word: str | None
      markers: tuple[Marker, ...]   # every canonical ID's newest title, to record once the result is written
      titles_checked: int
  def judge(titles_by_id: Mapping[str, Sequence[str]], markers: Mapping[str, Marker], patterns: PatternSet,
            words: Sequence[str], *, private_now: bool) -> Verdict: ...
  @dataclass(frozen=True)
  class PrivacyInputs:
      markers: Mapping[str, Marker]
      patterns: PatternSet
      words: tuple[str, ...]
  def why_phrase(why: Why, *, rebuilt: bool) -> str: ...   # decision 15: "title changed", "the sync's record was rebuilt", "first check", "patterns changed"
  ```
  - `judge`'s rules: an ID with no marker is case 4 (every pattern and word over every title of every ID); a marker whose hash differs from the newest title's is case 2 (patterns and words on the new title); a marker of an older pattern version is case 5 (only `added_since(marker.version)`, over every title). Each title is tried as given and folded. A keep-out match also adds `private`. Nothing ever removes `private`.
- Produces (`reconcile.py`, `writer.py`):
  ```python
  def reconcile(rec: Recording, snapshots: Sequence[Snapshot], existing: Sequence[Rendition], *,
                auto_private: Sequence[str] = DEFAULT_AUTO_PRIVATE, privacy: PrivacyInputs | None = None) -> Reconciled: ...
      # privacy None: no markers (case 4), a version-1 PatternSet of auto_private, no words; Reconciled gains `verdict: Verdict`
  @dataclass(frozen=True)
  class ReconcileReport: ...   # gains private_added: str | None (the why phrase) and kept_out_word: str | None
  class Writer:
      def __init__(self, ..., keep_out_word: str | None = DEFAULT_WORD) -> None: ...
      def keep_out_words(self) -> tuple[str, ...]: ...   # the configured word, if valid, plus plaud_state's enforced words
  ```
  `Writer.open` passes `plaud_settings(cfg).keep_out_word` when `keep_out_word_ok`, else None.

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-9-base`
- [ ] **Step 1: Failing tests,** `packages/core/tests/test_plaud_marker.py`:
  - `test_a_removed_private_stays_removed_through_a_reindex_in_a_web_writer` (C5.16): reconciled once; `private` removed (confirmed); a second `Writer` (the `web` role) reindexes; no `private`; a snapshot whose title changes to "Private: synthetic" brings it back with a `retagged-private` event, why "title changed".
  - `test_a_title_change_that_doesnt_match_moves_the_marker_without_a_tag`.
  - `test_a_failed_mutate_leaves_the_marker_and_retries_after_reindex`: `StaleFile`; the marker unchanged; `recordings reindex` accepts the edit; the next reconcile tags.
  - `test_a_new_pattern_is_checked_against_that_pattern_only`.
  - `test_a_removed_pattern_rechecks_nothing`.
  - `test_a_recording_the_marker_never_saw_is_checked_over_every_title_and_id`.
  - `test_case_4_after_a_rebuild_notes_the_record_was_rebuilt`.
  - `test_a_to_b_to_a_rechecks_a`.
  - `test_a_keep_out_title_found_by_reconcile_is_tagged_and_flagged_once`.
  - `test_the_marker_hashes_the_title_exactly_as_given`: a change of case is due.
  - `test_title_seen_no_longer_decides_privacy`.
  - `test_a_marker_is_kept_only_for_recordings_in_the_archive_and_holds_no_title`.
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_plaud_marker.py -q`. Expected: `ModuleNotFoundError: recordings.plaud.privacy`.
- [ ] **Step 3: Code:** `privacy.py`; `reconcile`'s privacy path; `Writer.reconcile_plaud`, `ReconcileReport`, `Writer.keep_out_words`, `Writer.open`.
- [ ] **Step 4: Docs:** `FORMAT.md`; main spec §7.4.
- [ ] **Step 5: Checks:** `make check`.
- [ ] **Step 6: Commit:** `feat(plaud): 🤖 the auto-private marker: a removed private stays removed until the Plaud title changes, in every writer`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 10: The baseline's privacy part, new patterns, and the `plaud` lock

**Tier:** 1 · **Scenarios:** none

Spec §14.5's privacy part (Phase 3 adds the check-state seeding and `--all`), §13.1 case 5's pass, §12.2's enforced words, and the `plaud` lock (§9.4).

**Files:**
- Create: `packages/core/src/recordings/plaud/baseline.py`, `packages/core/src/recordings/plaud/runlock.py`
- Modify: `packages/core/src/recordings/locks.py` (`PLAUD_LOCK`)
- Modify: `packages/core/src/recordings/plaud/commands.py` (`baseline`, `keep-out-words`)
- Test: `packages/core/tests/test_plaud_baseline.py`, `packages/core/tests/test_plaud_cli.py`
- Modify: main spec §6.4 (the `plaud` lock beside `ops`), "(stage 2b design, 2026-10-23)"

**Interfaces:**
- Consumes: `writer.Writer.reconcile_plaud`, `ReconcileReport.private_added`, `ReconcileReport.kept_out_word`; `privacy.current_patterns`; `checks.PlaudDB`, `checks.K_KEEP_OUT_WORDS`, `checks.K_PATTERNS_PASS`; `locks.Locks`, `locks.announce`, `locks.running`, `locks.OPS_LOCK`; `commands.EXIT_*`.
- Produces:
  ```python
  # locks.py
  PLAUD_LOCK = "plaud"
  # plaud/runlock.py
  JOBS = ("plaud-sync", "plaud-compare", "plaud-import", "plaud-resync", "plaud-full", "plaud-baseline",
          "plaud-catch-up", "plaud-review")
  CLI_WAIT = 60.0
  @contextmanager
  def plaud_run(state: State, job: str, *, timeout: float, now: datetime, with_ops: bool = False) -> Iterator[None]: ...
      # its own Locks instance; with_ops takes ops and plaud in one hold() and announces job on both; LockTimeout propagates
  def plaud_holder(state: State) -> tuple[str, datetime] | None: ...   # locks.running(state.locks_dir, PLAUD_LOCK)
  # plaud/baseline.py
  @dataclass(frozen=True)
  class BaselineReport:
      titles_checked: int
      private_added: int
      kept_out_late: int
      already_seen: int
      unwritten: tuple[str, ...]
      def to_dict(self) -> dict: ...   # counts and IDs only
  def baseline_privacy(writer: Writer, db: PlaudDB, *, now: datetime) -> BaselineReport: ...   # reconcile_plaud on every recording with a Plaud source
  def enforce_word(db: PlaudDB, word: str, *, now: datetime) -> tuple[str, ...]: ...
  def drop_word(db: PlaudDB, word: str, *, configured: str, now: datetime) -> str: ...   # a §15.6-style reply; refuses the configured word (decision 26)
  @dataclass(frozen=True)
  class PatternsPass:
      version: int
      checked: int
      tagged: int
      def sentence(self) -> str: ...   # "patterns changed: 1,234 titles checked, 6 tagged private"
  def patterns_pass(writer: Writer, db: PlaudDB, *, now: datetime) -> PatternsPass | None: ...   # only when a version newer than the last pass's exists; records K_PATTERNS_PASS
  ```
  - `recordings plaud baseline [--json]`: holds `plaud` (`plaud-baseline`), waits `CLI_WAIT` then exits 75; prints the counts; exit 0, or 1 when anything is unwritten.
  - `recordings plaud keep-out-words [--drop <word>] [--json]`: holds nothing; lists the enforced words, or drops one.

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-10-base`
- [ ] **Step 1: Failing tests,** `test_plaud_baseline.py` and `test_plaud_cli.py`:
  - `test_the_baseline_checks_every_title_of_2a_recordings_once`: a second run checks no new title.
  - `test_the_baseline_tags_a_2a_keep_out_title_private_and_flags_it`.
  - `test_the_baseline_prints_counts_and_no_title`.
  - `test_the_baseline_waits_for_a_held_plaud_lock_then_exits_75`: `CLI_WAIT` patched small; another process holds `plaud`.
  - `test_ops_and_plaud_in_one_hold_announce_the_job_on_both`.
  - `test_changing_the_keep_out_word_releases_nothing`.
  - `test_keep_out_words_drop_removes_an_old_word_and_refuses_the_configured_one`.
  - `test_a_new_pattern_is_checked_once_over_the_archive`: the pass's sentence; a second start runs no pass.
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_plaud_baseline.py -q`. Expected: `ModuleNotFoundError: recordings.plaud.baseline`.
- [ ] **Step 3: Code:** `PLAUD_LOCK`, `runlock.py`, `baseline.py`, the two commands.
- [ ] **Step 4: Docs:** main spec §6.4.
- [ ] **Step 5: Checks:** `make check`.
- [ ] **Step 6: Commit:** `feat(plaud): 🤖 recordings plaud baseline checks every title once; enforced keep-out words; the plaud lock`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 11: Sync health and the ping rule

**Tier:** 1 · **Scenarios:** C5.08, C6.01, C6.02, C6.03, C6.09, C6.10, C6.12

Spec §16 and §11.7, with decision 10: one Healthchecks check, pinged only after a scheduled sync that is good and that nothing holds up. If this is wrong, alerting stops silently, or cries wolf.

**Files:**
- Create: `packages/core/src/recordings/plaud/health.py`
- Create: `packages/core/src/recordings/worker.py` (`TurnContext` and `conclude_turn` only; Task 13 adds the loop)
- Test: `packages/core/tests/test_plaud_health.py`, `packages/core/tests/test_runbook.py`
- Modify: `docs/runbooks/first-run.md` ("When the Plaud alert comes", spec §17.5 word for word, with its optional Healthchecks Email Reports line)
- Modify: main spec §9.1.1 (the good-run rule, spec §11.7), "(stage 2b design, 2026-10-23)"

**Interfaces:**
- Consumes: `checks.PlaudDB`, `checks.NoSyncYet`, `checks.BLOCKERS`, `checks.BLOCKER_PREFIX`, `checks.K_FAILING`, `checks.K_LAST_PING`, `checks.K_LAST_TURN`, `checks.K_CATCH_UP_DONE`; `alerts.AlertConfig`, `alerts.ping_url`; `settings.BAD_WORD`.
- Produces (`recordings/plaud/health.py`):
  ```python
  Cause = Literal["listing", "disk", "work"]
  Listing = Literal["good", "small-shrink", "held", "partial", "bad", "none"]   # none: no listing (sign-in, outage)
  @dataclass(frozen=True)
  class RunOutcome:
      listing: Listing
      attempts: int        # imports, checks and fetches tried, failing-list recordings included
      successes: int       # a degraded check, or an import whose audio couldn't be fetched, is not one
      disk_refused: bool   # the disk guard refused an import
      detail: str          # a §15.6 run phrase
  def judge_run(outcome: RunOutcome) -> bool: ...   # listing good or small-shrink, and (no attempts or a success), and not disk_refused
  def record_run(db: PlaudDB, outcome: RunOutcome, *, now: datetime) -> bool: ...   # sets or clears K_FAILING by cause (§11.7's recovery by cause); returns judge_run
  def open_blocker(db: PlaudDB, name: str, *, now: datetime, detail: Mapping[str, Any] | None = None) -> None: ...   # keeps the first since
  def clear_blocker(db: PlaudDB, name: str) -> None: ...
  TurnOutcome = Literal["ran", "gate", "paused", "rate-limited", "busy", "off", "idle-config", "error"]
  def record_turn(db: PlaudDB, outcome: TurnOutcome, *, now: datetime, detail: Mapping[str, Any] | None = None) -> None: ...
  @dataclass(frozen=True)
  class SyncHealth:
      blockers: Mapping[str, datetime]                      # name -> since
      failing: tuple[datetime, Cause] | None
      last_turn: tuple[TurnOutcome, datetime, Mapping[str, Any]] | None
      last_ping: tuple[datetime, bool, int | None] | None   # at, ok, HTTP status
      catch_up_done: datetime | None
      schedule_minutes: int
      def may_ping(self) -> bool: ...    # no blocker and no failing streak
      def cause(self) -> str | None: ... # the first that applies, as a fixed phrase: a blocker, failing, the disk too full to import, the schedule off, BAD_WORD, a rate-limit wait (until when), busy (the holder, since when), the gate; None when nothing stops the ping
  BLOCKER_PHRASES = {"sign-in": "sign-in needed", "consent": "imports stopped: consent list", "hold": "held: see Status",
                     "breaker": "paused: unexpected changes", "private-unwritten": "should be private: run `recordings reindex`"}
  def sync_health(state: State, *, schedule_minutes: int) -> SyncHealth: ...   # plaud_state only; raises NoSyncYet without the table
  ```
- Produces (`recordings/worker.py`):
  ```python
  @dataclass
  class TurnContext:
      state: State
      db: PlaudDB
      alerts: AlertConfig
      schedule_minutes: int
      clock: Callable[[], datetime]
  def conclude_turn(ctx: TurnContext, ran: RunOutcome | None) -> bool: ...
      # ran None: the turn didn't run a sync, so no ping. Otherwise record_run, then sync_health; pings
      # alerts.deadman_sync_url (the plain URL, an empty body, never /start or /fail) only when the run was good
      # and may_ping(); records K_LAST_PING {at, ok, status}; any error while deciding sends nothing. Its
      # docstring says it is the one place the sync tells the outside how it is, where ntfy's pushes go later.
  ```

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-11-base`
- [ ] **Step 1: Failing tests,** `packages/core/tests/test_plaud_health.py` (the `ntfy` fixture as the fake Healthchecks, decision 11):
  - `test_the_ping_rule_table` (C6.01): parametrised rows of (run good or not, streak open or not, each blocker, a turn that was busy, the gate, a sign-in pause or a rate-limit wait) each ping or don't.
  - `test_every_blocker_stops_the_ping_even_with_a_good_run` (C6.02, C5.08): a consent stop with no attempts; the breaker with working imports.
  - `test_flags_the_review_list_a_failing_recording_and_a_failed_full_never_stop_it` (C6.02).
  - `test_attempts_without_success_are_never_good_and_failing_recordings_count` (C6.03).
  - `test_recovery_follows_the_cause` (C6.03, C6.10): a listing streak clears at the next good run; a disk streak at the first run the guard doesn't refuse; a work streak only at a good run with a success; a good run with no attempts leaves a work streak.
  - `test_the_cause_names_the_schedule_off_a_bad_word_a_rate_limit_busy_the_gate_and_each_blocker` (C6.12).
  - `test_a_failed_ping_is_recorded_with_its_status` (C6.09).
  - `test_an_unreadable_state_db_sends_no_ping`.
  - `test_sync_health_reads_plaud_state_only_and_raises_no_sync_yet_without_it`.
  - `test_a_restart_neither_drops_nor_repeats_a_ping`: two `TurnContext`s on one state answer alike.
  - In `test_runbook.py`: `test_the_runbook_says_what_to_do_when_the_plaud_alert_comes`.
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_plaud_health.py -q`. Expected: `ModuleNotFoundError: recordings.plaud.health`.
- [ ] **Step 3: Code:** `health.py`; `worker.py`'s `TurnContext` and `conclude_turn`.
- [ ] **Step 4: Docs:** the runbook section; main spec §9.1.1.
- [ ] **Step 5: Checks:** `make check`.
- [ ] **Step 6: Commit:** `feat(worker): 🤖 sync health and conclude_turn: the sync's check is pinged only after a good sync that nothing holds up`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 12: 2b snapshots and the gate

**Tier:** 1 · **Scenarios:** C5.19, C5.24, C5.27

Spec §9.6's `Writer.add_snapshot`, §11.6's snapshot mark and the gate's states, the rebuild record and its marker seeding (§13.2), and `recordings plaud review`.

**Files:**
- Modify: `packages/core/src/recordings/writer.py` (`AddSnapshot`, `Writer.add_snapshot`)
- Modify: `packages/core/src/recordings/selfdoc.py`, `packages/core/src/recordings/cli.py` (decision 29: an orphan snapshot is a warning)
- Create: `packages/core/src/recordings/plaud/gate.py`
- Modify: `packages/core/src/recordings/plaud/commands.py` (`review`)
- Test: `packages/core/tests/test_writer.py`, `packages/core/tests/test_selfdoc.py`, `packages/core/tests/test_plaud_gate.py`, `packages/core/tests/test_plaud_cli.py`
- Modify: `docs/runbooks/first-run.md` ("After a lost or restored `state.db`", spec §17.5; "Restoring for real", step 6: `state.db`'s `-wal` and `-shm` move with it, and `plaud-cache.db` is derived)
- Modify: main spec §6.4 (the Writer gains `add_snapshot`) and §6.8 (the rebuild seeds the marker; what wasn't here waits for review), "(stage 2b design, 2026-10-23)"

**Interfaces:**
- Consumes: `archive.RawSource`, `archive.Archive`; `checks.PlaudDB`, `checks.K_SNAPSHOT_MARK`, `checks.K_CATCH_UP_DONE`, `checks.K_REBUILT`, `checks.Marker`; `privacy.title_sha256`, `privacy.current_patterns`; `runlock.plaud_run`; `commands.EXIT_*`.
- Produces:
  ```python
  # writer.py
  @dataclass(frozen=True)
  class AddSnapshot:                  # an Op
      source: SourceRef
      name: str = "plaud-snapshot"
      def apply(self, rec: Recording) -> Recording: ...   # appends unless a source with the same raw path is there
  class Writer:
      def add_snapshot(self, recording_id: str, raw: RawSource) -> Recording: ...
          # publishes raw write-once in the recording's source/ (a file with the same bytes is reused, so an orphan
          # from a crash is adopted), then records it through mutate; never across a Plaud call
  # selfdoc.py
  def validate(root: Path, *, deep: bool = False, warnings: list[dict] | None = None) -> list[dict]: ...   # an orphan snapshot (a source/ file no recording.json names) goes to warnings
  # plaud/gate.py
  TOOL = "recordings"   # _meta.tool on every 2b snapshot
  GATE_FIRST = "Scheduled sync waits for the first catch-up (runbook)"
  GATE_INTERRUPTED = "The first catch-up didn't finish: run `--catch-up` again"
  GATE_REFUSAL = "The first import is a supervised run on the server (runbook)"
  GateState = Literal["open", "first-time", "interrupted"]
  @dataclass(frozen=True)
  class Gate:
      state: GateState
      rebuilt_now: bool        # this start recorded a rebuild
      @property
      def open(self) -> bool: ...
      @property
      def reason(self) -> str | None: ...   # GATE_FIRST or GATE_INTERRUPTED while closed
  def has_2b_snapshot(archive: Archive) -> bool: ...   # stops at the first source/plaud-*.json whose _meta.tool == TOOL
  def gate_at_start(archive: Archive, db: PlaudDB, writer: Writer, *, now: datetime) -> Gate: ...
      # a mark: open if catch_up_done_at, else interrupted; no mark: a 2b snapshot means a rebuild, recorded
      # (with markers seeded) and open; otherwise first-time (decision 18)
  def gate_now(db: PlaudDB) -> Gate: ...                 # per tick, no archive scan
  def mark_snapshot(db: PlaudDB, *, now: datetime) -> None: ...   # its own transaction, before every 2b snapshot
  def record_rebuild(db: PlaudDB, *, now: datetime) -> None: ...  # rebuilt_at, catch_up_done_at and the mark in one put_many
  def seed_markers_after_rebuild(writer: Writer, db: PlaudDB, *, now: datetime) -> int: ...   # the primary Plaud ID's marker from plaud.title_seen when it equals that ID's newest title
  def review_from_now(db: PlaudDB, *, now: datetime) -> None: ...  # `plaud review`: rebuilt_at = now
  ```
  `recordings plaud review [--json]`: holds `plaud` (`plaud-review`); records `rebuilt_at` as now; exit 0, or 75 when busy.

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-12-base`
- [ ] **Step 1: Failing tests:**
  - `test_writer.py`: `test_add_snapshot_publishes_then_records`; `test_add_snapshot_adopts_an_orphan_of_the_same_bytes` (publish alone, as a crash leaves it; the next call records one file); `test_add_snapshot_of_the_same_bytes_twice_records_once`.
  - `test_selfdoc.py`: `test_an_orphan_snapshot_is_a_warning_not_a_problem`.
  - `test_plaud_gate.py`: `test_the_gate_is_closed_the_first_time`; `test_an_interrupted_catch_up_keeps_the_gate_closed_and_says_so` (C5.19); `test_a_rebuilt_state_db_is_recorded_in_one_transaction_and_opens_the_gate`; `test_a_restart_after_a_recorded_rebuild_doesnt_find_it_again` (C5.24); `test_seeding_uses_title_seen_only_when_it_equals_the_newest_title`; `test_has_2b_snapshot_stops_at_the_first`; `test_with_a_mark_the_archive_is_never_scanned`.
  - `test_plaud_cli.py`: `test_plaud_review_records_rebuilt_at_as_now_holding_plaud` (C5.27).
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_plaud_gate.py -q`. Expected: `ModuleNotFoundError: recordings.plaud.gate`.
- [ ] **Step 3: Code:** `AddSnapshot`, `add_snapshot`, `validate`'s warnings and `recordings validate`'s output, `gate.py`, the `review` command.
- [ ] **Step 4: Docs:** the runbook section; main spec §6.4 and §6.8.
- [ ] **Step 5: Checks:** `make check`.
- [ ] **Step 6: Commit:** `feat(plaud): 🤖 Writer.add_snapshot, the snapshot mark and the gate's states; recordings plaud review`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 13: The worker process

**Tier:** 1 · **Scenarios:** C5.08, C5.11, C5.14, C5.18, C6.08, C6.13

Spec §9.2 and §9.7: one process, a main thread that owns its `Writer`, a heartbeat thread, the 2-second tick, scheduled turns, `--alive`. Its sync turn is a stand-in until Phase 2.

**Files:**
- Modify: `packages/core/src/recordings/worker.py`
- Modify: `packages/core/src/recordings/cli.py` (`recordings worker [--alive]`)
- Test: `packages/core/tests/test_worker.py`
- Modify: `CLAUDE.md` (the archive's docs are written "whenever the web app's or the CLI's Writer opens (the worker's doesn't)")

**Interfaces:**
- Consumes: Task 11's `TurnContext`, `conclude_turn`, `health.RunOutcome`, `health.record_turn`, `health.open_blocker`, `health.clear_blocker`; Task 12's `gate_at_start`, `gate_now`; Task 8's `apply_late_consent`; Task 10's `enforce_word`, `patterns_pass`, `plaud_run`, `plaud_holder`; `keepout.read_consent`, `keepout.signature`; `settings.plaud_settings`; `alerts.from_config`; `checks.PlaudDB` and its keys; `writer.Writer.open(cfg, docs=False, lock_timeout=10)`.
- Produces (`recordings/worker.py`):
  ```python
  TICK = 2.0
  HEARTBEAT = 30.0
  ALIVE_HEARTBEAT = timedelta(seconds=90)
  ALIVE_PROGRESS = timedelta(minutes=15)
  ALIVE_PROGRESS_STARTING = timedelta(minutes=30)
  Phase = Literal["starting", "idle", "turn", "request", "consent", "stopping"]
  SILENT = "the Plaud worker is silent"; STUCK = "the Plaud worker may be stuck"
  @dataclass
  class Progress:                     # shared by the two threads under a lock
      phase: Phase
      counter: int
      at: datetime
      def bump(self, now: datetime, phase: Phase | None = None) -> None: ...
  class SyncTurn(Protocol):
      def __call__(self, worker: Worker, *, light: bool) -> RunOutcome: ...
  def stand_in_turn(worker: Worker, *, light: bool) -> RunOutcome: ...   # raises NotImplementedError; Phase 2 replaces it
  class Worker:
      def __init__(self, cfg: Config, environ: Mapping[str, str], *, clock: Callable[[], datetime] = ...,
                   turn: SyncTurn = stand_in_turn, heartbeat: float = HEARTBEAT) -> None: ...
      state: State; db: PlaudDB; settings: PlaudSettings; writer: Writer | None; gate: Gate | None
      consent: ConsentList | None; progress: Progress; stopping: threading.Event
      def start(self) -> None: ...      # §9.2's order: heartbeat thread (phase starting); sweep .tmp/plaud-dl/ under a non-blocking try of plaud; [Task 14 adds requests.at_start here]; Writer.open; gate_at_start; enforce_word; patterns_pass
      def tick(self) -> None: ...       # bump; the consent watch (read when the signature changed, trusted only if unchanged across the read; K_CONSENT records what was read: ids, file_changed, read_at, stop, typo_lines, review_lines); apply_late_consent, the consent blocker (decision 13), and the private-unwritten blocker and flag while a `private` it owes isn't written; [Task 14 adds: claim and serve one request]; a due scheduled turn; prune once a day
      def due(self) -> bool: ...        # schedule_minutes > 0 and now ≥ max(last_started, last_ended) + schedule_minutes; a stamp in the future counts as long ago
      def blocked_before_call(self) -> tuple[TurnOutcome, Mapping[str, Any]] | None: ...   # Phase 1: None; Phase 2 adds the sign-in pause and the rate-limit wait
      def scheduled_turn(self) -> None: ...   # off → "off"; a bad keep_out_word → "idle-config"; the gate closed → "gate"; blocked_before_call; plaud held → "busy"; else plaud_run("plaud-sync"), self.turn, last_started and last_ended, conclude_turn
      def run(self) -> int: ...         # start, then tick every TICK until SIGTERM (stops between ticks); 0
  def heartbeat_once(state: State, progress: Progress, *, now: datetime) -> None: ...   # meta.worker_heartbeat = {"at", "phase", "progress_counter", "progress_at"}
  def alive(state: State, *, now: datetime) -> tuple[bool, str | None]: ...   # a heartbeat under 90 s, and progress within 15 minutes (30 while starting); else SILENT or STUCK
  def main(argv: list[str] | None = None) -> int: ...   # `recordings worker [--alive]`; --alive exits 0 or 1
  ```

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-13-base`
- [ ] **Step 1: Failing tests,** `packages/core/tests/test_worker.py` (an injected clock; real threads only where a test says so):
  - `test_start_runs_in_the_spec_order`: the heartbeat's `starting` comes first, then the sweep, the writer, the gate (Task 14 adds the requests between the sweep and the writer).
  - `test_the_plaud_dl_sweep_is_skipped_while_a_cli_run_holds_plaud`.
  - `test_a_scheduled_turn_before_the_catch_up_makes_no_plaud_call` (C5.18): the turn spy isn't called; the last turn is "gate"; no ping.
  - `test_a_held_plaud_lock_skips_the_turn_as_busy_without_a_ping` (C6.08).
  - `test_due_reads_last_started_and_last_ended_across_a_crash_loop`.
  - `test_a_last_started_in_the_future_counts_as_long_ago` (Review Focus 3).
  - `test_an_idle_worker_with_no_sign_in_stays_alive_for_20_minutes` (C6.13).
  - `test_a_wedged_main_thread_fails_alive_after_15_minutes`.
  - `test_the_heartbeat_keeps_writing_during_a_long_synchronous_step`: a real heartbeat thread at a short interval while the main thread blocks.
  - `test_a_late_consent_listing_lands_at_the_next_tick_with_no_sign_in` (C5.11).
  - `test_a_list_replaced_by_rename_is_read_at_the_next_tick` (C5.14).
  - `test_a_list_that_changes_while_read_is_read_again_next_tick` (Review Focus 2).
  - `test_a_consent_typo_opens_the_consent_blocker_at_the_tick_and_a_clean_read_clears_it` (C5.08, decision 13).
  - `test_a_private_that_cant_be_written_opens_its_blocker_and_flag_until_it_lands`.
  - `test_a_bad_keep_out_word_leaves_the_worker_idle_and_alive`.
  - `test_sigterm_stops_between_ticks`.
  - `test_recordings_worker_alive_exits_0_or_1`.
  - `test_the_workers_writer_writes_no_docs`.
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_worker.py -q`. Expected: `AttributeError: module 'recordings.worker' has no attribute 'Worker'`.
- [ ] **Step 3: Code:** the worker as above; `cli.py`'s `worker` command.
- [ ] **Step 4: Docs:** CLAUDE.md's docs line.
- [ ] **Step 5: Checks:** `make check`.
- [ ] **Step 6: Commit:** `feat(worker): 🤖 recordings worker: the tick, the heartbeat thread, scheduled turns and --alive; late consent listings at every tick`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 14: Requests from the UI

**Tier:** 1 · **Scenarios:** C5.18, C5.21

Spec §9.3: request rows in `state.db`, inserted by `web` off the event loop, claimed and re-checked by the worker. The worker's Phase 1 handlers are `keep-here`, `park` and `unpark` (decision 2).

**Files:**
- Create: `packages/core/src/recordings/plaud/requests.py`
- Create: `packages/ui/src/recordings_ui/plaud.py` (the checks and inserts; Task 19 adds the views)
- Modify: `packages/core/src/recordings/worker.py` (claiming and serving; `HANDLERS`)
- Modify: `packages/core/src/recordings/plaud/commands.py` (`park`)
- Test: `packages/core/tests/test_plaud_requests.py`, `packages/ui/tests/test_plaud_requests_ui.py`, `packages/core/tests/test_plaud_cli.py`
- Modify: main spec §9.1 (the worker bullet as spec §19 words it; the CLI paragraph naming the commands Phase 1 built: `worker [--alive]`, `plaud consent`, `keep-out-words`, `park`, `review`, `baseline`), "(stage 2b design, 2026-10-23)"

**Interfaces:**
- Consumes: `checks.PlaudDB`; `gate.gate_now`, `gate.GATE_REFUSAL`; `flags.keep_here`; `runlock.plaud_holder`; `worker.alive`; `ids.parse_id`; `refs.canonical_plaud_id`.
- Produces (`recordings/plaud/requests.py`):
  ```python
  KINDS = ("compare", "sync", "import", "resync", "full", "accept", "accept-changes", "release-preview",
           "release", "unrelease", "park", "unpark", "keep-here")
  GATE_ALLOWED = frozenset({"compare", "keep-here"})
  MAX_PLAUD_IDS = 20_000; MAX_RESYNC_IDS = 1_000; MAX_PLAUD_ID_LEN = 128
  EXPIRE_AFTER = timedelta(hours=1); KEEP_FINISHED = timedelta(days=7); FULL_COOLDOWN = timedelta(hours=1)
  REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")
  QUEUED = "Queued"; ALREADY_QUEUED = "Already queued"; FULL_TOO_SOON = "Sync everything ran less than an hour ago"
  INTERRUPTED = "interrupted"; NOT_BUILT = "error: `NotImplementedError`"
  def queued_after(started: str) -> str: ...   # "Queued after the current sync (started 14:20)"
  def resuming(started: str) -> str: ...       # "Resuming (started 14:20)"
  def worker_down(last: str) -> str: ...       # "The Plaud worker isn't running (last reported 14:21): your request runs when it's back, if that's within the hour."
  @dataclass(frozen=True)
  class PlaudRequest:
      request_id: str
      kind: str
      plaud_ids: tuple[str, ...] = ()
      recording_ids: tuple[str, ...] = ()
      select_all: bool = False
  ReplyState = Literal["queued", "running", "done", "failed", "expired", "refused"]
  @dataclass(frozen=True)
  class Reply:
      request_id: str
      kind: str | None
      state: ReplyState
      message: str
      progress: Mapping[str, int] | None = None   # {"done", "total"}
      result: Mapping[str, Any] | None = None     # counts and IDs, never a title
      def to_dict(self) -> dict: ...
  @dataclass(frozen=True)
  class Claimed:
      id: int
      request: PlaudRequest
      queued_at: datetime
  def insert(state: State, req: PlaudRequest, *, now: datetime, timezone_name: str) -> Reply: ...   # §9.3's coalescing in one BEGIN IMMEDIATE transaction; worker_down from the heartbeat, queued_after from the plaud holder
  def read(state: State, request_id: str) -> Reply | None: ...
  def claim_next(state: State, *, now: datetime) -> Claimed | None: ...   # UPDATE … WHERE state = 'queued', oldest first
  def progress(state: State, request_id: str, *, done: int, total: int) -> None: ...
  def finish(state: State, request_id: str, *, ok: bool, message: str, result: Mapping[str, Any] | None = None, now: datetime) -> None: ...
  def at_start(state: State, *, now: datetime) -> tuple[int, int]: ...   # running → failed INTERRUPTED; queued older than an hour → expired
  def expire(state: State, *, now: datetime) -> int: ...
  def prune(state: State, *, now: datetime) -> int: ...
  ```
- Produces (`recordings_ui/plaud.py`):
  ```python
  UNREADABLE = "The request couldn't be read."
  UNKNOWN_KIND = "That isn't a Plaud request."
  BAD_ID = "An ID isn't valid."
  TOO_MANY_PLAUD = f"At most {MAX_PLAUD_IDS:,} Plaud recordings at a time."
  TOO_MANY_RESYNC = "For more, use Sync everything"
  NOT_SAVED = "The request couldn't be saved: state.db can't be used"
  @dataclass(frozen=True)
  class Refusal:
      request_id: str | None
      message: str
  def check_request(raw: object) -> PlaudRequest | Refusal: ...   # keys request_id, kind, plaud_ids, recording_ids, all, and no other (a request never carries a title)
  def cannot_request_reason(settings: Settings, state: State | None) -> str | None: ...   # demo, no config.toml, the mirror's copy: why, in cannot_tag_reason's terms
  async def submit(state: State | None, raw: object, *, now: datetime, timezone_name: str, reason: str | None) -> dict: ...   # Refusal → a "refused" reply; else asyncio.to_thread(requests.insert); a sqlite3.Error → NOT_SAVED at once
  ```
- Produces (`worker.py`):
  ```python
  Handler = Callable[[Worker, Claimed], Reply]
  HANDLERS: dict[str, Handler]   # Phase 1: "keep-here", "park", "unpark"
  class Worker:
      def serve_request(self, claimed: Claimed) -> None: ...   # the gate first (GATE_REFUSAL unless kind in GATE_ALLOWED); no handler → NOT_BUILT; finish()
      # start() gains requests.at_start between the sweep and Writer.open; tick() claims and serves one request per tick
  ```
  `recordings plaud park <id>… [--undo] [--json]`: holds nothing; a recording ID sets `plaud_checks.parked_at`, a Plaud ID `plaud_listing.parked_at` (decision 19); exit 0, or 1 when an ID is unknown.

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-14-base`
- [ ] **Step 1: Failing tests:**
  - `test_plaud_requests.py`: `test_a_sync_takes_over_a_queued_compare`; `test_a_compare_or_sync_while_a_sync_is_queued_is_already_queued`; `test_a_resync_merges_into_a_queued_one_or_inserts_after_a_claim`; `test_a_full_is_refused_while_queued_running_or_within_an_hour`; `test_a_repeated_request_id_is_ignored`; `test_two_sessions_inserting_at_once_make_one_row` (two processes); `test_the_worker_claims_the_oldest_and_rechecks_the_gate` (C5.21, C5.18: an `import` row is refused with `GATE_REFUSAL`; a `keep-here` row runs); `test_at_start_running_rows_fail_as_interrupted_and_old_queued_rows_expire`; `test_with_the_worker_down_the_reply_says_so_and_the_row_expires_after_an_hour`; `test_a_kind_with_no_handler_yet_is_answered_not_built`; `test_park_unpark_and_keep_here_handlers`.
  - `test_plaud_requests_ui.py`: `test_check_request_refuses_unknown_kinds_bad_ids_caps_and_extra_keys`; `test_no_writable_state_refuses_with_a_reason`; `test_a_request_that_cant_be_saved_answers_at_once` (Review Focus 5); `test_submit_inserts_off_the_event_loop_never_on_the_tag_threads`.
  - `test_plaud_cli.py`: `test_plaud_park_and_undo`.
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_plaud_requests.py -q`. Expected: `ModuleNotFoundError: recordings.plaud.requests`.
- [ ] **Step 3: Code:** `requests.py`, `recordings_ui/plaud.py`'s checks and `submit`, the worker's claiming and handlers, the `park` command.
- [ ] **Step 4: Docs:** main spec §9.1.
- [ ] **Step 5: Checks:** `make check`.
- [ ] **Step 6: Commit:** `feat(plaud): 🤖 request rows: web checks and inserts them, the worker claims them and re-checks the gate; park, unpark and keep-here`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 15: Doctor's worker role

**Tier:** 1 · **Scenarios:** none (§21's doctor cases; C10.03 is proved on the server in Phase 2)

Spec §17.2.

**Files:**
- Modify: `packages/core/src/recordings/doctor.py`
- Test: `packages/core/tests/test_doctor.py`
- Modify: main spec §5 (doctor's roles gain `worker`, for which a missing dead-man URL is a problem) and §14 ("The doctor warns … except the worker's, which is a problem"), "(stage 2b design, 2026-10-23)"

**Interfaces:**
- Consumes: `keepout.read_consent`; `settings.plaud_settings`, `settings.BAD_WORD`; `alerts.from_config`; `checks.PlaudDB`, `checks.K_KEEP_OUT_WORDS`.
- Produces:
  ```python
  ROLES = ("web", "backup", "worker", "all")
  BACKUP_SECRETS = ("restic_password", "rest_password", "nas_ssh_key", "nas_known_hosts", "deadman_url")
  ALERT_SECRETS = ("ntfy_topic", "ntfy_token")
  PLAUD_MOUNT = Path("/plaud")
  def _forbid(environ: Mapping[str, str], names: Sequence[str], role: str, problems: list[str]) -> None: ...   # web: both sets plus deadman_sync_url; worker: the backup set; backup: deadman_sync_url
  def _no_plaud_here(environ: Mapping[str, str], problems: list[str]) -> None: ...   # web and backup: a mounted /plaud, or RECORDINGS_PLAUD_TOKEN set (read by name), is a problem
  def _worker(cfg: Config, environ: Mapping[str, str], report: dict, problems: list[str], warnings: list[str]) -> None: ...
  # report["plaud"] = {"consent_ids": int | None, "consent_stop": str | None, "review_lines": list[int],
  #                    "token_store": bool, "keep_out_words": list[str], "deadman_sync_set": bool}
  ```
  For `worker`: `_backup`, `_mirror`, `_ssh` and the restic part of `_secrets` are skipped; no alerts-off warning. Its problems: a consent stop (by line numbers; kept lines are a note); no token store ("sign-in needed: run `make plaud-sign-in`"); `/plaud` wider than 0700, the token wider than 0600, or either not this user's; `BAD_WORD`; a missing or bad `deadman_sync_url`, naming `RECORDINGS_DEADMAN_SYNC_URL_FILE`. Never a value or a hash. Doctor never calls Plaud. Token expiry waits for Phase 2 (decision 28).

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-15-base`
- [ ] **Step 1: Failing tests,** in `test_doctor.py` (`PLAUD_MOUNT` patched to a temporary folder):
  - `test_the_worker_role_skips_backup_mirror_ssh_and_restic`.
  - `test_the_worker_has_no_alerts_off_warning`.
  - one test per `worker` problem: the consent stop by line numbers (kept lines a note), no token store, `/plaud` too wide, the token too wide, someone else's, a bad `keep_out_word`, a missing and a bad `deadman_sync_url`.
  - `test_web_forbids_both_secret_sets_and_the_sync_url`; `test_worker_forbids_the_backup_set`; `test_backup_forbids_the_sync_url`.
  - `test_web_and_backup_report_a_mounted_plaud_or_a_plaud_token`.
  - `test_doctor_never_calls_plaud`: `urlopen` and `getaddrinfo` patched to fail the test.
  - `test_doctor_prints_no_value_or_hash`.
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_doctor.py -q -k worker`. Expected: `RECORDINGS_ROLE must be one of web, backup, all`.
- [ ] **Step 3: Code:** the role, the split sets, `_worker`, `_no_plaud_here`.
- [ ] **Step 4: Docs:** main spec §5 and §14.
- [ ] **Step 5: Checks:** `make check`.
- [ ] **Step 6: Commit:** `feat(doctor): 🤖 the worker role: the consent list, the sign-in, /plaud's modes, the keep-out word and the sync's dead-man URL`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 16: Backups carry the consent folder

**Tier:** 1 · **Scenarios:** none (§21's backup test)

Spec §12.1 ("Backed up") and §17.2's `extra_paths` checks, with decision 9.

**Files:**
- Modify: `packages/core/src/recordings/backup.py` (an extra path may be a folder: the presence check, the free-space estimate, the restore test)
- Modify: `packages/core/src/recordings/doctor.py` (`_extra_paths` for every role)
- Modify: `config.example.toml` (`[backup] extra_paths`'s comment names the consent folder, `/consent` in the containers)
- Test: `packages/core/tests/test_backup.py`, `packages/core/tests/test_doctor.py`
- Modify: main spec §15.1 (the consent folder is backed up through `extra_paths`; "not … the Plaud token store" stays, about its own folder), "(stage 2b design, 2026-10-23)"

**Interfaces:**
- Consumes: `cache.CACHE_NAME`; `doctor.PLAUD_MOUNT`.
- Produces:
  ```python
  # backup.py
  def missing_paths(bc: BackupConfig) -> list[Path]: ...   # files and folders that aren't there; run_backup refuses on any, as before
  # doctor.py
  CONSENT_MOUNT = Path("/consent")
  def _extra_paths(cfg: Config, role: str, problems: list[str], warnings: list[str]) -> None: ...
      # every role: an entry holding the token's folder, the whole of /state, or plaud-cache.db is a problem;
      # backup, with a repository set: no consent folder (or no deploy.env, as now) is a warning
  ```

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-16-base`
- [ ] **Step 1: Failing tests:**
  - `test_backup.py`: `test_a_folder_in_extra_paths_is_backed_up`; `test_a_missing_extra_folder_still_refuses_the_backup`; `test_the_free_space_estimate_counts_a_folders_files`; `test_the_restore_test_checks_a_folder_came_back`; `test_the_backup_never_carries_plaud_cache_db`.
  - `test_doctor.py`: `test_extra_paths_holding_the_token_folder_the_whole_state_or_the_cache_is_a_problem`; `test_backup_without_the_consent_folder_is_a_warning`.
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_backup.py -q -k folder`. Expected: `BackupRefused: refusing to back up: … is missing` for an existing folder.
- [ ] **Step 3: Code:** `backup.py`'s folder support; doctor's `_extra_paths`; the template's comment.
- [ ] **Step 4: Docs:** main spec §15.1.
- [ ] **Step 5: Checks:** `make check`.
- [ ] **Step 6: Commit:** `feat(backup): 🤖 extra paths may be folders, so the consent folder is backed up; doctor refuses extra paths that would carry titles or the token`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 17: The worker service, and deploying it

**Tier:** 1 · **Scenarios:** C8.03, C10.01, C10.02, C10.04

Spec §17.1 and §17.3, with decision 10: Phase 1 is deployable, the worker idle and healthy.

**Files:**
- Modify: `docker/compose.yml` (the `worker` service; `backup` gains the consent folder; the `deadman_sync_url` secret)
- Modify: `docker/deploy.example.conf` (`RECORDINGS_PLAUD_HOST` and `RECORDINGS_CONSENT_HOST`, both folders; the secrets list gains `deadman_sync_url`)
- Modify: `docker/recordings.service` (its description: "the web, backup and worker containers")
- Modify: `scripts/compose_smoke.py` (the worker with no token and no consent list stays healthy, and doctor reports both and the empty `deadman_sync_url`)
- Test: `packages/core/tests/test_deploy_template.py`, `packages/core/tests/test_runbook.py`
- Modify: `docs/runbooks/first-run.md` (a section `## 6. Deploy 2b` after `## 5. The mirror on the Mac`: first spec §22's check that the Library's first load is under 2 seconds, then §17.5's steps 1 to 5 and 7, with only the consent folder added to `extra_paths` (decision 9); step 6, the sign-in, says the worker stays idle and reports "sign-in needed" until the sign-in exists)
- Modify: `README.md` (the Docker section: `make deploy` starts web, backup and worker)
- Modify: main spec §3 (three containers; one writer means one machine), §5 (line 220's "every container"; the token's location; `deploy.env`'s two folders), §6.8 (`state.db` mounted in every container; "the same folder holds the Plaud token store" goes) and §15 (the worker publishes no port; only `worker` holds the token; egress isn't blocked; all three run as non-root), "(stage 2b design, 2026-10-23)"

**Interfaces:**
- Consumes: `recordings worker [--alive]` (Task 13); `recordings doctor --role worker` (Task 15).
- Produces, the `worker` service: the same image and tag, `pull_policy: never`, `hostname: recordings-worker`, `command: ["recordings", "worker"]`, `RECORDINGS_ROLE: worker`, `RECORDINGS_DEADMAN_SYNC_URL_FILE: /run/secrets/deadman_sync_url`, `<<: *hardening`, `init: true`, `restart: unless-stopped`, `stop_grace_period: 30s`, `cpu_shares: 512`, `oom_score_adj: 300`, the deployment's user, log rotation; long-syntax binds with `create_host_path: false`: the config read-only, the archive, `/state`, the `index` volume, `${RECORDINGS_CONSENT_HOST}` read-only at `/consent`, `${RECORDINGS_PLAUD_HOST}` at `/plaud`; `secrets: [deadman_sync_url]`; `healthcheck: {test: ["CMD", "recordings", "worker", "--alive"], interval: 30s, start_period: 60s}`; no `ports`. `backup` gains `${RECORDINGS_CONSENT_HOST}` read-only at `/consent`.

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-17-base`
- [ ] **Step 1: Failing tests,** in `test_deploy_template.py` and `test_runbook.py`:
  - `test_the_worker_is_hardened_named_with_a_role_and_its_own_health_check`: never the image's `:8000/healthz`.
  - `test_the_consent_folder_is_a_read_only_folder_bind_in_worker_and_backup`.
  - `test_each_dead_man_url_is_mounted_in_one_service` (C10.04).
  - `test_plaud_is_mounted_in_worker_only`.
  - `test_the_worker_publishes_no_port`.
  - `test_make_deploy_waits_for_every_health_check` (C10.01): `make -n deploy` has `--wait`, and no service but `backup` disables its health check.
  - `test_every_compose_variable_is_documented_in_the_template_or_set_by_make` (existing) covers the two new folders.
  - `test_the_runbook_has_a_deploy_2b_section` (the heading list gains `## 6. Deploy 2b`).
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_deploy_template.py -q`. Expected: `KeyError: 'worker'`.
- [ ] **Step 3: Code:** compose, the template, the unit's description, the smoke script.
- [ ] **Step 4: Docs:** runbook section 6; the README's Docker section; main spec §3, §5, §6.8, §15.
- [ ] **Step 5: Checks:** `make check`, then `make deploy-smoke` (C8.03, C10.02; CI's compose-smoke job runs it too).
- [ ] **Step 6: Commit:** `feat(docker): 🤖 the worker service, hardened, with its own health check, the consent folder and the sync's dead-man URL`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 18: The deploy guard asks `plaud`, and `make consent-add`

**Tier:** 1 · **Scenarios:** none (§21's deploy and shell-target tests)

Spec §17.3's guard, §12.1's `make consent-add`, and the runbook sections they need.

**Files:**
- Modify: `docker/ops-busy.py` (`ops-busy.py <locks dir> <lock name> [job …]`)
- Modify: `docker/deploy-guard.sh` (asks `ops`, any job, and `plaud` for `plaud-full`, `plaud-catch-up` and `plaud-baseline`)
- Create: `docker/consent-add.sh` (server side), `scripts/consent_id.py` (Mac side)
- Modify: `Makefile` (`-include local.mk`; `consent-add` with its `## ` line)
- Create: `packages/core/tests/test_make_targets.py`
- Test: `packages/core/tests/test_deploy_template.py`
- Modify: `docs/runbooks/first-run.md` ("Keep a recording out for good"; "Rolling back across 2b", spec §17.3; re-imports stop `worker` as they stop `backup`, and section 3's `IMPORT_MOUNTS` mounts the consent folder, `-v "$RECORDINGS_HOME/consent:/consent:ro"`; "Planned work doesn't pause the sync's check")
- Modify: `README.md` (the Docker section: the guard waits for Sync everything and the catch-up; `make consent-add`)

**Interfaces:**
- Consumes: `keepout.parse_consent`'s exclusion rule; `recordings plaud consent` (Task 8); the `worker` service (Task 17).
- Produces:
  - `python - <locks dir> <lock name> [job …] < docker/ops-busy.py`: exits 3, printing the announcement, when the lock is held and, if jobs are given, the announced job is one of them; a held lock whose announcement can't be read still exits 3; else 0. Standard library only.
  - `scripts/consent_id.py <text>`: prints the one ID found in `<text>` (a bare ID, `of_…`, or the Copy ID form `` - `of_…` — ``) exactly as typed, or exits 1 with "not a Plaud ID".
  - `docker/consent-add.sh <id>` (POSIX `sh`, on the server): refuses an ID not matching `^(of_)?[0-9a-fA-F]{32}$`; reads `RECORDINGS_CONSENT_HOST` from `docker/deploy.env`; writes the old list plus `` - `<id>` — added <YYYY-MM-DD> `` to a temporary file in the same folder at the old file's mode, then renames it over.
  - `make consent-add ID=<id>`: `SERVER` and `REPO_DIR` from `local.mk` (git-ignored, `-include`d, expanded on the Mac); runs `scripts/consent_id.py`, then `ssh "$(SERVER)" "cd '$(REPO_DIR)' && sh docker/consent-add.sh '<id>' && sh docker/up-deployed.sh exec -T worker recordings plaud consent"`; non-zero on any failure, naming the step.

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-18-base`
- [ ] **Step 1: Failing tests:**
  - `test_deploy_template.py`: `test_the_probe_takes_a_lock_name_and_jobs`; `test_the_guard_refuses_for_plaud_full_and_catch_up_and_lets_plaud_sync_through`; `test_a_held_plaud_lock_that_cant_be_read_still_refuses`.
  - `test_make_targets.py` (a fake `ssh` on `PATH` that runs the remote command in a temporary server folder): `test_consent_add_refuses_a_bad_id_before_ssh`; `test_consent_add_writes_by_rename_and_keeps_the_mode`; `test_consent_add_accepts_the_copy_id_form` (Review Focus 4); `test_consent_add_expands_server_and_repo_dir_on_the_mac`.
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/core/tests/test_make_targets.py -q`. Expected: `No rule to make target 'consent-add'`.
- [ ] **Step 3: Code:** `ops-busy.py`, `deploy-guard.sh`, `consent-add.sh`, `consent_id.py`, the Makefile.
- [ ] **Step 4: Docs:** the runbook sections; the README.
- [ ] **Step 5: Checks:** `make check`.
- [ ] **Step 6: Commit:** `feat(deploy): 🤖 the deploy guard waits for Sync everything, the catch-up and the baseline; make consent-add`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 19: The view model

**Tier:** 2 · **Scenarios:** C6.06, C6.14

Spec §15's build order, step 1, and §22: what each view reads from which table. Every later UI task reads these shapes.

**Files:**
- Modify: `packages/ui/src/recordings_ui/plaud.py` (the views)
- Modify: `packages/ui/src/recordings_ui/status.py` (`status_view` gains the sync panel and its Needs attention lines; a missing 2b table never reaches the "restore `state.db`" text)
- Modify: `packages/ui/src/recordings_ui/runtime.py` (`configure(plaud=…)`, `plaud_settings()`), `packages/ui/src/recordings_ui/app.py` (passes them), `packages/ui/src/recordings_ui/settings.py` (decision 23: demo mode copies the canned view)
- Modify: `packages/ui/src/recordings_ui/views.py` (`recording_view` gains `plaud`)
- Create: `demo/plaud-sync.json` (hand-written, never generated from a listing: a SyncView, a SyncPanel and Arrivals for the demo)
- Modify: `packages/ui/frontend/src/types.ts`
- Test: `packages/ui/tests/test_plaud_views.py`, `packages/ui/tests/test_status.py`

**Interfaces:**
- Consumes: `checks.PlaudDB`, its keys, `checks.NoSyncYet`, `checks.NO_SYNC_YET`; `cache.Cache.read_only`, `Cache.compare_rows`, `Cache.summary`; `health.sync_health`, `SyncHealth.cause`; `gate.gate_now`; `flags.SENTENCES`; `worker.alive`; `runlock.plaud_holder`; `privacy.why_phrase`'s phrases; `settings.PlaudSettings`.
- Produces (Python, `recordings_ui/plaud.py`):
  ```python
  DEMO_NOTE = "Demo: nothing is synced"
  DEMO_STATUS = "Demo: Plaud sync isn't part of the demo."
  DEMO_VIEW_NAME = "plaud-sync.json"
  @dataclass(frozen=True)
  class PlaudViewSettings:
      state_path: Path | None
      timezone_name: str
      schedule_minutes: int
      full_sweep_days: int
      auto_import: bool
      keep_out_word_ok: bool
      demo_view: Path | None        # demo mode and Pages: the canned view's file
  def plaud_view_settings(cfg: Config) -> PlaudViewSettings: ...
  def sync_view(settings: PlaudViewSettings | None, *, now: datetime, can_request: str | None) -> dict: ...   # SyncView
  def sync_panel(settings: PlaudViewSettings | None, *, now: datetime) -> dict: ...   # SyncPanel
  def arrivals(settings: PlaudViewSettings | None, *, since: datetime, now: datetime) -> dict: ...   # Arrivals
  def recording_plaud(settings: PlaudViewSettings | None, recording_id: str, *, now: datetime) -> dict | None: ...   # RecordingPlaud; None with no Plaud source
  def demo_view(path: Path) -> dict: ...   # {"sync": SyncView, "panel": SyncPanel, "arrivals": Arrivals}
  # status.py
  def status_view(archive_root: Path, settings: StatusSettings | None, *, now: datetime | None = None,
                  plaud: PlaudViewSettings | None = None) -> dict: ...   # gains "sync": SyncPanel; its attention lines join "attention"
  ```
- Produces (TypeScript, `types.ts`; dates and times are formatted by the server in the default zone; every `string | null` button field is null when enabled, else the reason):
  ```ts
  export type PlaudKind = "compare" | "sync" | "import" | "resync" | "full" | "accept" | "accept-changes"
    | "release-preview" | "release" | "unrelease" | "park" | "unpark" | "keep-here";
  /** The `plaud_request` input (spec §9.3). Never sent on load, from a URL, its hash or storage. */
  export interface PlaudRequest { request_id: string; kind: PlaudKind; plaud_ids: string[]; recording_ids: string[]; all: boolean }
  export interface ReleasePreview { plaud_id: string; title: string; date: string; time: string; duration: string | null; next_sync: string | null }
  /** The `plaud_result` message. `result` holds counts and IDs, never a title. */
  export interface PlaudResult {
    request_id: string; kind: PlaudKind | null;
    state: "queued" | "running" | "done" | "failed" | "expired" | "refused";
    message: string; progress: { done: number; total: number } | null;
    result: Record<string, number | string | string[]> | null;
    preview: ReleasePreview | null;   // release-preview only, read from the cache for this session (decision 21)
  }
  export interface SyncSummaryView { kind: "all-here" | "problem" | "no-sync" | "demo"; sentence: string; explain: string | null; see_status: boolean }
  export interface SyncFlag {
    kind: "kept-out-late" | "consent-late" | "private-unwritten" | "rekeyed";
    recording_id: string | null; plaud_id: string | null; copy_id: string | null;
    sentence: string; keep_here: boolean; date: string | null; duration: string | null;
  }
  export interface SyncHold { sentence: string; accept: boolean; refused: string | null }
  export interface SyncBreaker { sentence: string; accept: boolean; code_fix: string | null; fields: string[] }
  export interface SyncEquation { listed: number; here: number; waiting: number; excluded: number; missing: number; arrive_private: number; listing_at: string | null }
  export interface ArchiveEquation { from_plaud: number; here: number; gone: number; absent: number }
  export interface ConsentSummary { ids: number; on_plaud: number | null; file_changed: string | null; read_at: string | null; stop: string | null }
  export interface NotHereRow { plaud_id: string; copy_id: string; date: string; time: string; duration: string | null; title: string | null; arrives_private: boolean; state: string; review: boolean; park: boolean }
  export interface KeptOutRow { plaud_id: string; copy_id: string; date: string | null; time: string | null; duration: string | null; by: "consent" | "title" | "rekeyed" | "was-title"; release: boolean; released: string | null }
  export interface UpdatedRow { recording_id: string; title: string | null; parts: string[]; at: string }
  export interface GoneRow { recording_id: string; since: string }
  export interface ParkedRow { id: string; kind: "recording" | "plaud" }
  export interface SyncView {
    demo: boolean; available: boolean; reason: string | null;
    summary: SyncSummaryView; flags: SyncFlag[]; hold: SyncHold | null; breaker: SyncBreaker | null;
    buttons: { sync: string | null; compare: string | null; full: string | null; import: string | null };
    full_estimate: string;                      // "about 40 minutes" | "unknown until the first run"
    equation: SyncEquation | null; archive: ArchiveEquation | null; consent: ConsentSummary | null;
    titles_hidden: string | null;               // "titles hidden until the consent list reads" | "titles hidden while Plaud's IDs look changed"
    not_here: NotHereRow[]; import_all: { count: number; review_more: number };
    kept_out: KeptOutRow[]; updated: UpdatedRow[]; gone: { rows: GoneRow[]; last_7_days: number }; parked: ParkedRow[];
    last_result: string | null; next_sync: string | null;
  }
  export interface SyncPanel {
    demo: boolean; no_sync_yet: boolean;
    summary: SyncSummaryView; failing: string | null; not_pinged: string | null; conditions: string[];
    hold: SyncHold | null; breaker: SyncBreaker | null;
    numbers: {
      equation: SyncEquation | null; archive: ArchiveEquation | null; consent: ConsentSummary | null;
      last_good_listing: string | null; next_sync: string | null; sweep: string | null; sign_in: string | null;
      full: string | null; held: number; gone: number; failing: number; parked: number;
      worker: string | null; healthchecks: string | null;
    };
    details: {
      normaliser: string; fields: string[]; census: Record<string, number>; form_change: string | null;
      changes_24h: Record<string, number>; named_share: string | null; runs: { at: string; result: string }[];
      keep_out_words: string[]; malformed: number;
    };
    attention: string[];
  }
  export interface Arrivals { new: number; private: number; updated: number; kept_out_late: number; consent_late: number }
  export interface RecordingPlaud {
    plaud_ids: { id: string; copy_id: string }[]; checked: string | null; next_by: string | null;
    gone_since: string | null; tagged_private: string | null; removed_private: string | null;
    flags: SyncFlag[]; resync: string | null;
  }
  // StatusView gains sync?: SyncPanel; RecordingView gains plaud: RecordingPlaud | null; Page gains "add" (Task 21).
  ```

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-19-base`
- [ ] **Step 1: Failing tests,** `packages/ui/tests/test_plaud_views.py` and `test_status.py` (synthetic `state.db` rows and cache rows, written through `PlaudDB` and `Cache.db()`):
  - `test_all_here_shows_only_when_true`: a listing under two intervals old, nothing waiting or missing, parked aside; a failing recording is its own line and never hides it.
  - `test_status_never_carries_a_title`: synthetic titles in `recording.json` and the cache appear nowhere in `sync_panel` or `status_view`.
  - `test_kept_out_and_flag_rows_carry_no_title`.
  - `test_not_here_titles_come_from_the_cache_and_hide_with_its_reason`.
  - `test_the_not_pinged_line_names_the_cause_from_sync_health` (C6.06): a refused sign-in from the first failed run.
  - `test_the_schedule_off_for_24_hours_after_the_catch_up_needs_attention` (C6.14).
  - `test_each_needs_attention_entry_phase_1_can_show`: a consent stop, a late consent listing, a late keep-out, a `private` that couldn't be written, the review list, the worker silent or stuck, a `plaud` holder past 2 hours (12 for the full and the catch-up), the schedule off, the ping failing for 2 hours.
  - `test_without_the_2b_tables_it_says_no_sync_has_run_yet_never_the_restore_text`.
  - `test_with_the_gate_closed_every_button_but_compare_says_why`.
  - `test_the_demo_view_is_the_hand_written_fixture_with_the_demo_notes`.
  - `test_the_details_lines`: checked and next, the Plaud IDs in Copy ID form, gone since, tagged private with its reason, the removal date from the revision that removed it, the flags.
  - `test_arrivals_count_events_since_and_flags_by_count_only`.
- [ ] **Step 2: Run them; watch them fail:** `uv run pytest packages/ui/tests/test_plaud_views.py -q`. Expected: `AttributeError: module 'recordings_ui.plaud' has no attribute 'sync_view'`.
- [ ] **Step 3: Code:** the views; `status_view`; `runtime`, `app`, `settings`, `views.recording_view`; `demo/plaud-sync.json`; `types.ts`.
- [ ] **Step 4: Checks:** `make check`.
- [ ] **Step 5: Commit:** `feat(ui): 🤖 the Plaud view model: the Sync screen, Status's sync panel, arrivals and Details, read-only from state.db and the cache`.
- [ ] **Step 6: Review, look forward, close** (the controller runs it).

### Task 20: The request input and its replies

**Tier:** 2 · **Scenarios:** none (§21's request-state tests)

Spec §15's build order, step 2: `plaud_request`, the insert, polling a pending row, `send_message`, and the reply states.

**Files:**
- Modify: `packages/ui/src/recordings_ui/shiny_app.py`
- Create: `packages/ui/frontend/src/plaud/usePlaud.tsx`, `packages/ui/frontend/src/plaud/usePlaud.test.ts`
- Modify: `packages/ui/frontend/src/App.tsx` (`useShinyOutputStatus("plaud_sync")`, `useShinyOutputStatus("plaud_arrivals")`; `usePlaud` mounted once; `PlaudContext`)
- Test: `packages/ui/tests/test_plaud_session.py`

**Interfaces:**
- Consumes: Task 14's `recordings_ui.plaud.submit`, `cannot_request_reason`, `requests.read`; Task 19's `sync_view`, `arrivals`, `PlaudViewSettings`, the TypeScript types; `cache.Cache.release_title`, `Cache.drop_preview`.
- Produces (`shiny_app.py`):
  - input `plaud_request`: an event input, mounted once with `null`, read with `@reactive.event` (its default `ignore_none`); `submit` runs on `asyncio.to_thread`, never on the tag service's threads.
  - output `plaud_sync`: `sync_view(...)`, with `reactive.invalidate_later(60)` and again when one of the session's requests finishes.
  - output `plaud_arrivals`: `arrivals(since=<the later of the session's start and its last Refresh>)` (decision 22), every 60 seconds.
  - message `plaud_result`: `Reply.to_dict()` plus `preview` (decision 21). While the session has a pending request, its row is read every 2 seconds and progress and the final reply are sent.
- Produces (`src/plaud/usePlaud.tsx`):
  ```ts
  export interface Plaud {
    send(kind: PlaudKind, opts?: { plaud_ids?: string[]; recording_ids?: string[]; all?: boolean }): string;   // the new request_id
    results: Record<string, PlaudResult>;   // by request_id, the newest reply each
    last: PlaudResult | null;
    lastLine: string | null;                // the Sync screen's kept result line
    available: boolean; reason: string | null;
  }
  export const PlaudContext: React.Context<Plaud | null>;
  export function usePlaudContext(): Plaud;
  export function usePlaud(view: SyncView | undefined): Plaud;   // mounted once in App; the plaud_result handler lives here
  ```

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-20-base`
- [ ] **Step 1: Failing tests:**
  - `usePlaud.test.ts`: `queued, running with progress, then done`; `an already-queued reply is shown as such`; `a refused reply keeps its message`; `the last result line survives later progress`; `nothing is sent on mount`; `each send makes a new random request_id`.
  - `test_plaud_session.py` (shiny's test server, as `test_tag_sessions.py`): `test_a_request_is_inserted_off_the_event_loop`; `test_a_pending_row_is_read_every_two_seconds_and_its_replies_sent`; `test_a_release_preview_reply_carries_its_title_to_that_session_only`; `test_a_crafted_request_without_a_writable_state_is_refused`.
- [ ] **Step 2: Run them; watch them fail:** `make test-js` and `uv run pytest packages/ui/tests/test_plaud_session.py -q`. Expected: the hook's module is missing.
- [ ] **Step 3: Code:** the server's input, outputs and polling; the hook; App's lines.
- [ ] **Step 4: Checks:** `make check`, `make e2e` (App changed).
- [ ] **Step 5: Commit:** `feat(ui): 🤖 the plaud_request input and plaud_result replies, polled every 2 seconds while a request is pending`.
- [ ] **Step 6: Review, look forward, close** (the controller runs it).

### Task 21: The Sync screen

**Tier:** 2 · **Scenarios:** C5.06, C9.02, C9.03, C9.04

Spec §15.1, J6: a new page, `#/add`, "Library · Tags · Add · Status".

**Files:**
- Create: `packages/ui/frontend/src/components/SyncPage.tsx`, `SyncSummary.tsx`, `ReleaseDialog.tsx`, and `SyncPage.test.ts`
- Modify: `packages/ui/frontend/src/components/TopBar.tsx` (the Add tab; at 320 pixels four short tabs on one row, the logo's mark without its name, the theme switch as an icon), `src/lib/nav.ts` (+ `nav.test.ts`), `src/types.ts` (`Page` gains `"add"`), `src/App.tsx` (the route), `src/app.css`
- Modify: main spec §12 (navigation: "Library, Tags, Add, Status" until 3b), §12.2 (a fourth exception to "No confirmation dialogs": Release), §12.4 (replaced by the 2b spec's §15.1), "(stage 2b design, 2026-10-23)"

**Interfaces:**
- Consumes: Task 19's `SyncView`, `SyncFlag`, `NotHereRow`, `KeptOutRow`, `ReleasePreview`; Task 20's `usePlaudContext`, `PlaudResult`.
- Produces:
  ```ts
  export function SyncPage(): JSX.Element;   // reads useShinyOutputValue<SyncView>("plaud_sync") and the PlaudContext
  export function SyncSummary({ summary, link }: { summary: SyncSummaryView; link: "status" | "add" }): JSX.Element;   // shared with Status's panel
  export function ReleaseDialog({ preview, onCancel, onLetIn }: { preview: ReleasePreview; onCancel: () => void; onLetIn: () => void }): JSX.Element;
  // nav.ts: routeFromHash("#/add") → { page: "add", open: null }; hashFor("add") === "#/add"
  ```
  Sections, top to bottom, as §15.1 lists them; Copy ID copies `copy_id`; importing never asks "are you sure?"; a toast says what came in; the newest result line stays.

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-21-base`
- [ ] **Step 1: Failing tests,** `SyncPage.test.ts` and `nav.test.ts` (a SyncView fixture):
  - `the sections render in the spec's order`.
  - `with the gate closed only Compare is enabled, and the others say why`.
  - `under a hold the import buttons are disabled with the hold's line`.
  - `kept-out and flag rows show no title` (C9.02).
  - `Release asks first, with the title from its preview, and Let it in sends release` (C5.06, C9.03).
  - `a released row reads Released · comes in at the next sync · Undo, and Undo sends unrelease`.
  - `Park and Unpark send their requests` (C9.04).
  - `Import all shows its count and the review rows left out`.
  - `Keep it here sends keep-here`.
  - `the newest result line stays after the toast`.
  - `#/add routes to the Sync screen`.
- [ ] **Step 2: Run them; watch them fail:** `make test-js`. Expected: `SyncPage` is missing.
- [ ] **Step 3: Code:** the components, the tab, the route, the styles (shadcn/ui and Tailwind, checked against their docs; the commit says what was checked).
- [ ] **Step 4: Docs:** main spec §12, §12.2, §12.4.
- [ ] **Step 5: Checks:** `make check`, `make e2e`.
- [ ] **Step 6: Commit:** `feat(ui): 🤖 the Sync screen at #/add: summary, flags, holds, equations, Not here yet, Kept out with Release's question, Updated, Gone and Parked`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 22: Status's sync panel

**Tier:** 2 · **Scenarios:** C6.06, C6.09

Spec §15.2, J5: plain sentences first, then the numbers, Details folded away; no titles.

**Files:**
- Create: `packages/ui/frontend/src/components/SyncPanel.tsx`, `SyncPanel.test.ts`
- Modify: `packages/ui/frontend/src/components/StatusPage.tsx` (the panel; Needs attention already holds its lines), `StatusPage.test.ts`
- Modify: main spec §12.5 (the sync panel and its Needs attention entries; the backup's "last reported" line stays the backup's; the Sync screen acts, Status reports), "(stage 2b design, 2026-10-23)"

**Interfaces:**
- Consumes: Task 19's `SyncPanel`, `StatusView.sync`; Task 21's `SyncSummary`; Task 20's `usePlaudContext` (Accept this listing, Accept these changes).
- Produces: `export function SyncPanel({ panel }: { panel: SyncPanel }): JSX.Element`.

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-22-base`
- [ ] **Step 1: Failing tests,** `SyncPanel.test.ts`: `sentences come first, then the numbers, with Details folded`; `the not-pinged line names its cause` (C6.06); `a failed ping shows its time and HTTP status` (C6.09); `no sync yet says so`; `Accept this listing and Accept these changes send their requests, or show why not`; `the demo says Plaud sync isn't part of the demo`; `no title is drawn`.
- [ ] **Step 2: Run them; watch them fail:** `make test-js`. Expected: `SyncPanel` is missing.
- [ ] **Step 3: Code:** the panel, its place on Status.
- [ ] **Step 4: Docs:** main spec §12.5.
- [ ] **Step 5: Checks:** `make check`, `make e2e`.
- [ ] **Step 6: Commit:** `feat(ui): 🤖 Status's sync panel: true sentences first, numbers that add up, details folded away`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 23: The Library and Details

**Tier:** 2 · **Scenarios:** C9.05, C9.06

Spec §15.3: the arrivals cue, the selection bar's **Re-sync from Plaud**, and the Details lines.

**Files:**
- Modify: `packages/ui/frontend/src/components/HeadNotes.tsx` (the Refresh button: "2 new · 1 private · 2 updated from Plaud · Refresh", beside "5 changed"; at 320 pixels "5 from Plaud · Refresh"; the flags line "1 kept out too late · 1 consent-listed in the archive · See Status", by count only), `TagsPage.tsx` (the same cue in the grid's header), `SelectionBar.tsx` (+ test), `RecordingPane.tsx` or `DetailsTab.tsx` (the Plaud section)
- Create: `packages/ui/frontend/src/components/PlaudDetails.tsx`, `PlaudDetails.test.ts`; `HeadNotes.test.ts`
- Modify: main spec §12.1 and §12.3 (the arrivals cue, the selection bar's Re-sync, the Details lines), "(stage 2b design, 2026-10-23)"

**Interfaces:**
- Consumes: Task 19's `Arrivals`, `RecordingPlaud`, `RecordingView.plaud`; Task 20's `usePlaudContext`; `useShinyOutputValue<Arrivals>("plaud_arrivals")`.
- Produces:
  ```ts
  export function PlaudDetails({ plaud }: { plaud: RecordingPlaud }): JSX.Element;
  export function SelectionBar(props: { count: number; lastTagged: string[] | null; onTag: () => void; onClear: () => void;
    onResync: () => void; resync: string | null /* null: enabled; else why not */ }): JSX.Element | null;
  ```
  The selection bar reads "12 selected · Tag… · Re-sync from Plaud · Clear"; Re-sync sends one `resync` with the selection's recording IDs (a cap of 1,000: "For more, use Sync everything").

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-23-base`
- [ ] **Step 1: Failing tests:** `HeadNotes.test.ts`: `the arrivals cue counts new, private and updated, and shortens on a phone` (C9.06); `privacy flags show as counts with no title`. `SelectionBar.test.ts`: `Re-sync from Plaud sends one request with the selection` (C9.05); `over 1,000 it says use Sync everything`; `its reply shows Re-syncing 12 of 25 then the end line`. `PlaudDetails.test.ts`: `checked and next by`; `each Plaud ID with Copy ID`; `gone from Plaud since`; `tagged private by the sync, and decision 3's line once Dan removed it`; `a late keep-out offers Keep it here`.
- [ ] **Step 2: Run them; watch them fail:** `make test-js`. Expected: `PlaudDetails` is missing.
- [ ] **Step 3: Code:** the cue, the selection bar, the Details section, `views.recording_view`'s `plaud` shown.
- [ ] **Step 4: Docs:** main spec §12.1, §12.3.
- [ ] **Step 5: Checks:** `make check`, `make e2e`.
- [ ] **Step 6: Commit:** `feat(ui): 🤖 the Library's arrivals cue and privacy flag counts, Re-sync from Plaud on a selection, and Details' Plaud lines`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 24: Demo, Pages, phones and accessibility

**Tier:** 2 · **Scenarios:** C9.01, C9.07

Spec §15.4, §15.5 and §21's e2e and Pages lines.

**Files:**
- Create: `packages/ui/tests/e2e/test_sync.py`
- Modify: `packages/ui/tests/test_demo_guard.py`, `packages/ui/tests/pages/test_pages_site.py`, `packages/ui/tests/test_build_pages.py`
- Modify: `scripts/build_pages.py` (copies `demo/plaud-sync.json` beside the vendored `demo/archive`, decision 23)
- Modify: `packages/ui/frontend/src/app.css` (320-pixel rules: the summary first, full-width buttons, 44-pixel targets, rows stacked with date and duration on one line and the title below)
- Modify: main spec §17 and §17.1 (no worker in either demo; the panels are canned), "(stage 2b design, 2026-10-23)"

**Interfaces:**
- Consumes: everything Tasks 19 to 23 built; `recordings_ui.plaud.DEMO_NOTE`, `DEMO_STATUS`.
- Produces: tests and CSS only.

**Steps outline:**
- [ ] **Step 0: Record the base:** `git rev-parse HEAD > .superpowers/sdd/2026-10-23-stage-2b-phase-1/task-24-base`
- [ ] **Step 1: Failing tests:**
  - `e2e/test_sync.py` (demo mode): `test_the_sync_screen_and_status_panel_draw_the_canned_view_at_1280_and_320` (C9.01); `test_the_four_tabs_stay_on_one_row_at_320`; `test_every_sync_button_is_disabled_with_the_demo_note`; `test_targets_are_at_least_44_pixels_at_320`; `test_axe_finds_nothing_in_light_and_dark`; `test_the_console_stays_clean`.
  - `test_demo_guard.py`: `test_the_demo_refuses_a_crafted_plaud_request`; `test_the_demos_state_db_never_gets_a_request_row`; `test_the_demo_starts_no_worker`.
  - `test_build_pages.py`: `test_the_canned_view_is_vendored`; `test_pyodide_never_imports_the_client_the_token_store_or_the_sync` (the vendored app's import graph, from its entry).
  - `pages/test_pages_site.py`: `test_the_canned_sync_screen_on_pyodide_sends_no_request_and_logs_nothing` (C9.07).
- [ ] **Step 2: Run them; watch them fail:** `make e2e`. Expected: no Add tab at 320 pixels on one row, or the canned view missing.
- [ ] **Step 3: Code:** `build_pages.py`'s copy; the CSS; anything the tests find.
- [ ] **Step 4: Docs:** main spec §17 and §17.1.
- [ ] **Step 5: Checks:** `make check`, `make e2e`, `make pages pages-test`.
- [ ] **Step 6: Commit:** `test(ui): 🤖 the Sync screen and Status's sync panel in demo mode and on Pages, at 1,280 and 320 pixels, with axe and no network`.
- [ ] **Step 7: Review, look forward, close** (the controller runs it).

### Task 25: Phase end

From `phase-end.md`, the large-project version. Each step ends with its commit (`docs(plan): 🤖 phase end step K: <what it did>`, its trailer `Plan: docs/superpowers/plans/2026-10-23-stage-2b-phase-1.md Phase end`), which ticks its box and sets **Now** to the next step.

- [ ] **1. The final whole-branch review:** Matt Pocock's two-axis `code-review` from `git merge-base main HEAD`; superpowers' one fix dispatch and one scoped re-review; a ruling on whatever is left. Each fix is its own commit.
- [ ] **2. The rest of the ledger copied into the plan:** whatever `.superpowers/sdd/2026-10-23-stage-2b-phase-1/progress.md` holds that no closing commit carries, under the task it belongs to.
- [ ] **3. The phase-end council:** four read-only reviewers (goal fit and scenarios, reliability, security, simplicity) on the whole branch; their reports committed first, in `docs/superpowers/plans/2026-10-23-stage-2b-phase-1-end-council.md`; each claim checked against the code before Dan sees it; each finding given a severity and a decision. The security reviewer re-checks every task's tier.
- [ ] **4. The phase-end list worked through:** each item fixed in its own commit at the tier of what it touches, or moved to the spec's Later with a reason; missing docs written now.
- [ ] **5. The forward look:** the Phase 2 and Phase 3 outlines below rewritten to fit what was built (every Consumes line they will need, by its built name).
- [ ] **6. The retro:** `docs/superpowers/plans/2026-10-23-stage-2b-phase-1-retro.md`: departures, items carried forward, metrics per tier (from the closing commits' `Tier:`, `Rounds:` and `Findings:` lines), starting over; the retuning rules applied to Phase 2's tiers.
- [ ] **7. A dated `CHANGELOG.md` entry** (the file is created here), and the spec's status line set to `PR open (#N)` once step 8 opens it.
- [ ] **8. The PR:** title `chore(repo): 🤖 merge Phase 1: stage 2b's groundwork`; the body summarises each task, the council's findings and what became of them, the retro's link, and every file a scan can't read (`git log --format= --numstat origin/main..HEAD | awk '$1 == "-" { print "not scanned, check by eye: " $3 }'`), ending `Plan: docs/superpowers/plans/2026-10-23-stage-2b-phase-1.md Phase end`. This repo has no `leakcheck.py`, `.githooks/` or denylist, so the PR text is scanned by `gitleaks stdin --redact --no-banner` alone: with the title and body in `.superpowers/sdd/2026-10-23-stage-2b-phase-1/PR_TITLE` and `PR_BODY.md`, `cat` both into `gitleaks stdin --redact --no-banner`, and only if it passes `gh pr create --base main --head stage-2b --title "$(cat …/PR_TITLE)" --body-file …/PR_BODY.md`; a finding stops everything, and Dan is told its kind and where, never the matched text. Never `--admin`, `--delete-branch`, squash or rebase.
- [ ] **9. [Dan] The merge** (attended: Dan merges the PR on GitHub). Then the Pages site serves the new demo (`pages.yml`); the status line's `merged` entry comes with Phase 2's start.

## Phase 2 and Phase 3 outlines

Their full plans are written at their phase start, against the code as built and the spikes' results.

**Phase 2: Plaud flows in** (branch and plan at its start; ends spec §4's first half)

- [Dan] **Spike sessions A and B** (runbook 1c, with `scripts/plaud_spike.py`): A is S1, S2, S4, S3; B is S5, S7, S8, S10, S12, S11. Results recorded in the runbook's table; S3's question asked only if a sign-out is found; S7's "hex differs" stops and asks Dan.
- The fake Plaud server's shapes: the account and listing, envelopes and storage, the sign-in, §21's faults, Dan's edits and the request-K hook (S1, S5, S8).
- `plaud/tokens.py`: the token store, refresh before expiry and after a 401, `tokens.lock`, write-before-use, `PlaudAuthError` on S4's codes, the sign-in pause, the current-user read; the client's refresh `POST` (S1, S2, S4).
- `recordings plaud token-import -` and `make plaud-sign-in` (with `SERVER`, `REPO_DIR` and a fake `ssh`); CLAUDE.md's `tokens.json` rule; doctor's token expiry (S1, S3).
- The client's page size, pacing, budget, 429 and 5xx backoff, the stall rule's numbers and `Range` resume (S5, S10, S11).
- `sources/base.py` and `sources/plaud.py` (decision 4); the listing: pages until empty, repeats, malformed rows, the census, bad, partial or complete (S5, S7).
- The buckets and both equations, the compare and the cache's writes (titles hidden in a consent stop or an ID-change hold), the property test (S7).
- The listing hold, **Accept this listing** (request and `accept-listing`), a small shrink settling, the re-key match (S7).
- Importing one recording: the consent and keep-out re-checks, the envelope's title checked before any block, the `_meta` stamp, auto-private at import, the disk guard, `.tmp/plaud-dl/`, ffprobe, `Writer.add`, reconcile, the snapshot mark (S8, S11, S12); CLAUDE.md's scratch-download sentence; main spec §6.1.
- Keep-out at listing time, **Release** (preview, release, unrelease, `plaud release`), a late keep-out from the listing, a title change re-tagged from the listing (§13.1 case 2).
- The sync run (§11.1) as the worker's turn and light turn, the good-run rule's attempts, the compare, sync and import handlers and their re-checks (S10's budget).
- Gone from Plaud (§12.3); the rebuild's review marks at listing time (§11.6).
- The first catch-up: `sync --catch-up`, `sync --dry-run [--list]`, `sync`, the exit codes, `ops` and `plaud` in one hold, the `audio-router` import taking `plaud` (S12 settled first).
- `test_flow_2b.py` and the leak scan at DEBUG (C7.01); the sign-in criterion on the fake (C4).
- `make e2e-live` and its CI job: `web` and `worker` against the fake; CLAUDE.md's Toolchain line for it.
- The runbook: section 6's sign-in step, sections 7 and 8, "Plaud sign-in", the hold and consent-stop recovery; the README's 2b paragraph; main spec §9.1's buckets and token paragraph, §9.3's deferred rows.

**Phase 3: keeps up** (spec §4's second half)

- [Dan] **Spike session C** (S9), with its clip deleted at the end.
- A check (§14.1): fetch, normalise, compare, `add_snapshot`, always reconcile; degraded; the check log.
- The rolling sweep (§14.2), the baseline's check-state seeding and `baseline --all` (§14.5), the companion check (§11.7).
- Cheap change detection (§14.3), from S5 and S9.
- The breaker (§14.4): the double fetch, suppression, **Accept these changes**, the normaliser-version close, `breaker reset`, `check --volatility`.
- Re-sync of one recording and of a selection (`resync`), parking's effects, **Sync everything** (`--full`, resume, the monthly run, the manifest), `/state/manifests` in `extra_paths` and doctor's warning (decision 9).
- Held removals counted (§14.7) and `recordings plaud held`.
- The README's keep-out paragraph with S9's finding; Re-sync's delay note; the runbook's DAN-15 step and `retired_at`; main spec §9.1.1's edit-sync amendments.

## The phase-end list

| Source task | Severity | File | Item | Status |
|---|---|---|---|---|
