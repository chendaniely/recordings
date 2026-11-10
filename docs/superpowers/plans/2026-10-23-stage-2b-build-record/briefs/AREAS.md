# The overnight build's areas (stage 2b, Phases 2 and 3)

Read `BUILD-FAST.md` (same folder) first: its rules bind every area. Then the area you were dispatched
for, below. Each area's scope is: its spec sections, plus every bullet of the plan's "Phase 2 and Phase
3 outlines" and every phase-end-list row marked `→ Phase 2` / `→ Phase 3` that falls in it (the plan
is `docs/superpowers/plans/2026-10-23-stage-2b-phase-1.md`; read its outlines and list, not the whole
file). Earlier areas' reports are in `reports/` beside this folder: read the ones before yours for what
they built and named.

## Shared for every area

- **The spikes are skipped.** Where the spec says "pending spike Sn" or leaves a Plaud detail to a
  spike, take audio-router's behaviour (its `src/` and `tests/` only), or the spec's own default when
  audio-router shows nothing, and choose the reading that is most private and fails closed. Record
  each such guess as a row in `docs/superpowers/plans/2026-11-01-stage-2b-guesses.md` (create it if
  it doesn't exist, with a one-paragraph header saying these are the build's guesses for Dan to check
  while testing, from Q&A §8): `| Area | Spec § / spike | What was guessed | Where in code | How Dan
  would notice it's wrong |`. Commit it with the code that relies on the guess.
- **The fake Plaud server** (`packages/core/tests/fake_plaud.py`) grows with each area to speak what
  the code needs (spec §10.6). The live demo (area A6) reuses it, so keep it importable from outside
  tests (no pytest import at module level) and every title invented.
- **Docs as you go:** the area's runbook, README, CLAUDE.md and spec lines change in the commit that
  changes the behaviour. Also add or extend the area's section of `docs/user-guide.md` (create it if
  missing: a plain how-to for Dan, one section per screen or task, "what you see" and "what to do",
  no internals); area B3 polishes it at the end.
- **Commands** become `Makefile` targets with `make help` lines (GNU make 3.81).
- **Never run anything against the real Plaud,** real server or real archive. `make plaud-sign-in` and
  any vendor CLI login are tested with fakes on `PATH` only; never run the real ones.
- **Sizing:** you have one area. If it is too big to finish well, finish a coherent part (tests
  green, docs true, committed), and list what's left in your report; the controller dispatches the
  rest. Don't leave a half-built feature reachable from the UI or the worker's turn.

## A1. Signing in, and the token store

Spec §10.3, §10.4, §10.5, §17.2's sign-in and token-expiry lines, §17.4, §19's README sign-in row.
`plaud/tokens.py` (the token store: refresh before expiry and after a 401, `tokens.lock`,
write-before-use, `PlaudAuthError`, the sign-in pause, the current-user read), the client's refresh
`POST`, `recordings plaud token-import -`, `make plaud-sign-in` (`SERVER`, `REPO_DIR`, `-include
local.mk`, the temp `HOME` and its `trap`, tested with a fake vendor CLI and a fake `ssh` on `PATH`),
CLAUDE.md's `tokens.json` rule, doctor's sign-in problem back as a problem naming `make
plaud-sign-in` (the P1 wrap made it a note while no target existed) and doctor's token expiry. The
fake server's sign-in (§10.6 step 3). For the vendor CLI's login command and its token file's keys,
use what audio-router's `src/` shows about the Plaud token file it reads; record the guess.

## A2. The client's throughput

Spec §10.1, §10.2, §10.6 steps 4–5 (the request log, §21's faults). Page size, pacing, the budget,
429 and 5xx backoff, the stall rule's numbers, `Range` resume, every fault injectable in the fake
server. The client stays read-only (spec §10.1); its tests use the fake only.

## A3. The listing, the compare, and the hold

Spec §11.2, §11.3, §11.4, §9.5's cache. `sources/base.py` and `sources/plaud.py` (decision 4), the
listing (pages until empty, repeats, malformed rows, the census; bad, partial or complete), the
buckets and both equations, `plaud/cache.py` (the compare's rows, titles hidden in a consent stop or
an ID-change hold, no title for an ID consent-listed or matching an enforced word, the tick purging
those titles), the property test, the listing hold, **Accept this listing** (the request kind and
`recordings plaud accept-listing`), a small shrink settling, the re-key match.

## A4. Importing, keeping out, Release, and gone

Spec §11.5, §12.1–§12.4 at listing time, §13.1 case 2, §11.6's review marks. Importing one recording
(the consent and keep-out re-checks, the title checked before any block via `envelope(…,
enrich=False)` or a split, the `_meta` stamp, auto-private at import, the disk guard,
`.tmp/plaud-dl/` and its sweep at the worker's start, ffprobe, `Writer.add`, reconcile, the consent
list re-read before each download trusting only a file at least one tick old, the snapshot mark before
every 2b snapshot by every path), keep-out at listing time, **Release** (preview, release,
unrelease, `plaud release`, its title path per the outline's BD1/P6/P8), a late keep-out from the
listing, a title change re-tagged, gone from Plaud (§12.3), the rebuild's review marks and `plaud
review`. CLAUDE.md's scratch-download sentence; main spec §6.1.

## A5. The sync run, the handlers, and the first catch-up

Spec §11.1, §11.6, §11.7, §9.3's handlers, §9.4 (the audio-router import takes `plaud` too), §9.7.
The sync run as the worker's turn and light turn, the good-run rule's attempts, a turn that raised
reaching `conclude_turn` as a run that isn't good, the compare, sync and import request handlers with
their re-checks and coalescing (one "Already queued" per kind), park for a Plaud ID that failed to
import, `HANDLED` widened per kind. The first catch-up: `recordings plaud sync --catch-up`, `sync
--dry-run [--list]`, `sync`, the exit codes, `ops` and `plaud` in one hold, the audio-router import
taking `plaud`. Make targets for the catch-up's dry run and real run that work over `rc`/compose on
the server (documented, never run against the server here).

## A6. Phase 2's screens, the end-to-end tests, and the live demo

Spec §15.1–§15.5 for what Phases 2's data now fills, §20's Phase 2 criteria, §21. The views' readers
of the listing, the hold, the sign-in (Needs attention's "sign-in expiring within 7 days"), the
compare, the re-keyed and review rows; Release's screen path; `test_flow_2b.py` and the leak scan at
DEBUG (C7.01); the sign-in criterion on the fake (C4); `make e2e-live` and its CI job (`web` and
`worker` against the fake); CLAUDE.md's Toolchain line. **`make demo-live`** (Dan's answer, Q&A §8):
the web app and the worker on the Mac against the fake Plaud server with a synthetic account and a
scratch archive under `$TMPDIR` (never a real one), so Dan can press Sync now and watch recordings
arrive, apply the consent list and keep-out word, see a hold, and try Release; it prints the URL and
stops everything cleanly on Ctrl-C. The runbook's section 6 sign-in step, sections 7 and 8, "Plaud
sign-in", the hold and consent-stop recovery; the README's 2b paragraph; main spec §9.1's buckets and
token paragraph, §9.3's deferred rows.

## B1. Checks, the sweep, and the breaker

Spec §14.1–§14.5, §11.7's companion check. A check (fetch, normalise, compare, `add_snapshot` with
`validate`'s orphan warning and FORMAT.md's line, always reconcile, degraded, the check log), the
rolling sweep, the baseline's check-state seeding and `baseline --all`, cheap change detection (S5
and S9 skipped: guess and record), the breaker (the double fetch, suppression, **Accept these
changes**, the normaliser-version close, `breaker reset`, `check --volatility`).

## B2. Re-sync, parking, Sync everything, and held removals

Spec §14.6, §14.7, §15.2's failing-checks line (the P1 wrap added its data field). Re-sync of one
recording and of a selection (`resync`), parking's effects, **Sync everything** (`--full`, resume, the
monthly run, the manifest), `/state/manifests` in `extra_paths` and doctor's warning (decision 9),
held removals counted and `recordings plaud held`, Status's "fails every check · Re-sync · Park" line
wired to real requests, `make e2e-live`'s breaker and Re-sync items, the Library and Details at 320 px
with Re-sync drawn. The README's keep-out paragraph, Re-sync's delay note, the runbook's DAN-15 step
and `retired_at`, main spec §9.1.1.

## B3. The guide, the setup, and the Pages demo

`docs/user-guide.md` finished: every screen and task Dan will meet in stages 1–2b, in plain words,
with what each Status line and flag means and what to do; linked from the README. **Setup
instructions** (Dan's answer, Q&A §8: "Server, dry run first"): one page from a fresh deploy to the
first real sync: deploy, `make plaud-sign-in` from the Mac, the consent list and keep-out word, the
first catch-up's dry run and reading its output, then the real run; link the runbook's details rather
than copying them. **The Pages demo:** `demo/plaud-sync.json` and whatever else the canned view
reads, so the public demo shows 2b's screens (the Sync screen, Status's panel, a hold, a keep-out,
Release's preview, the arrivals) with invented data; `make pages pages-test` pass. A dated
`CHANGELOG.md` entry for stage 2b (create the file). The spec's status line.
