# Area A1: signing in, and the token store: report

Status: **DONE_WITH_CONCERNS** (two small departures from the spec's letter and one guess-heavy
area; nothing blocks A2 to A6).

Branch `stage-2b`, from `c57c0f9`. Not pushed (the controller pushes).

## Commits

| SHA | Subject |
|---|---|
| `1f7bea6` | feat(plaud): 🤖 the sign-in: the token store, make plaud-sign-in, token-import and plaud check |

One commit: the store, the client's POST, the pause, the CLI, the make target, doctor and the docs
are coupled (doctor's problem text, the smoke's list, the runbook's pinned lines and the template
all move together), and every file passed `make check` together. The message passed
`scripts/commitmsg.py`; its trailer is the overnight build's.

## What was built, by spec section and scenario

### §10.3, the token store (`packages/core/src/recordings/plaud/tokens.py`, new)

- **Format:** our own JSON, `version` 1, `access_token`, `refresh_token` (or null), `expires_at`,
  `refresh_expires_at` (UTC ISO, or null when unknown), `obtained_at`, `refreshed_at`, and
  `client_id` / `client_secret` only when the vendor's file held them (sent with the refresh).
  `Tokens`' repr hides every secret field.
- **Refresh before expiry:** `due()`: under 5 minutes or 10% of the token's life left. An expiry the
  file doesn't give is never due: the refresh then waits for a 401.
- **Refresh after a 401:** `TokenStore.refresh(rejected)`.
- **`tokens.lock`:** an exclusive `flock` beside the file, waited up to 90 s (a refresh takes at
  most the client's 70 s), then `LockTimeout`. The file is read again under the lock: if another
  process already refreshed (no longer due, or no longer the token refused), its result is used and
  nothing is sent. Proven across two real processes with rotation on (one refresh, both succeed).
- **Write-before-use:** the new file is written (exclusive 0600 temp in the folder, fsync, rename,
  fsync the folder) before the new token is returned; a failed write hands out nothing. A reply
  without a new refresh token keeps the old one.
- **Modes:** a folder wider than 0700, a file wider than 0600, or either not this user's, is
  `TokenStoreUnsafe` (a `ConfigError`) naming the path and the `chmod`, before the file is opened.
  The rule is one function (`signin.unsafe_modes`) shared with doctor.
- **`PlaudAuthError`:** no file, a file the store can't use, a refresh refused, or no refresh token
  to refresh with.
- **`check_sign_in(store, client, db, *, now, probe=False)`:** §11.1's "check the sign-in, refresh if
  due", for A5's run, and `plaud check`'s read (`probe`). While a refusal is on record (a Sync now,
  or a run after a new token file) it makes the current-user read first; a refusal records the
  pause and the blocker and is raised; a success ends both and writes the sign-in's facts.
- **No `tokens.prev.json`:** rotation with the old refresh token refused at once (S2's strictest
  answer, a guess); a crash between Plaud's reply and the write costs one sign-in. §21's
  "with rotation and no grace / with a grace" pair is tested.

### §10.1 and §10.3, the client (`plaud/client.py`)

- `post_refresh(token_url, fields)`: the one POST, form-encoded, no redirects, no `Authorization`,
  `Accept: application/json`, the client's `User-Agent`. Any 4xx but 408 and 429, or a 200 whose
  JSON names an `error`, is `PlaudAuthError`; 408 and 5xx `PlaudUnavailable`, 429
  `PlaudRateLimited`, a redirect `PlaudRedirectRefused`, an unreadable 200 `PlaudBadResponse`. Every
  error is the client's own, raised from None (C4.04).
- **A 401 after a good refresh** (`get_json`'s `_probe`): the current-user read with the new token;
  a 401 there too is `PlaudAuthError`; if it works, the new `PlaudCallRefused` ("Plaud refused this
  call (401), though the sign-in works"), that recording's failure; any other failure of the read is
  raised as itself (an outage proves nothing about the sign-in) (C4.05).
- `current_user()`: `GET USER_PATH`, the account as 8 hex of the SHA-256 of its ID
  (`account_hash`). A 404 there (`PlaudNotFound`, a subclass of `PlaudBadResponse` with the same
  message, so nothing else changed) falls back to one listing page of 10, which proves the sign-in
  but names no account.
- `test_plaud_client.py`'s 401 test now expects the probe (`PlaudCallRefused`, then
  `PlaudAuthError` when the read is refused too); "only GET plus the refresh POST, POST only to the
  token endpoint, no Authorization on it" is a new test.

### §10.3, the pause and the blocker (`plaud/signin.py`, new, network-free)

- `K_SIGN_IN` (`sign_in` in `plaud_state`) holds `refused_at` and `file` (the token file's
  `[inode, size, mtime_ns]`) while a refusal stands, and `ok_at`, `expires_at`, `refresh_token`
  (bool) and `refresh_expires_at` once a call works. Never a token, a hash of one, or the account.
- `paused(db, token_store)`: `"no-sign-in"` with no file; `"refused"` while the refusal stands and
  the file is the one refused; else None. `record_refused` (keeps the first time for the same
  file; opens the `sign-in` blocker) and `record_good` (clears both, writes the facts).
- The worker's `scheduled_turn` (`worker.py`): after the bad word and the gate, a paused sign-in is
  outcome `"paused"` with `{"why": …}`, no Plaud call, no ping (§9.7 case 2); a new token file ends
  the wait at the next tick, as the gate's opening does. The worker still imports no token store
  at module level (only `signin`).
- **C4.01 to C4.04** are proven at the worker level in `test_worker.py`, against the fake, through a
  stand-in turn (`SignInTurn`: `check_sign_in`, one listing page, `record_refused` on any
  `PlaudAuthError`): the hour's token refreshed and the pings going on; a revoked token and a
  refused refresh stopping the pings, no Plaud call while paused, then a new sign-in pinging again;
  a 429 from the token endpoint an outage, no blocker, no pause. The Scenarios rows say so; A6's
  flow test re-proves them with A5's real turn.

### §10.4 and §10.5, signing in (`commands.py`, `Makefile`)

- **`recordings plaud token-import -`:** reads at most 64 KiB of stdin; keeps only our keys
  (`from_vendor`: the vendor's `email`, `user_id`, `token_type` and the rest are dropped); checks
  the folder (missing: `install -d -m 700`; too open: the `chmod`) before any call; makes one
  cheap call (`current_user`) with the new token and no refresh (`StaticTokens`); installs under
  `tokens.lock`; records the good sign-in (best effort: a state.db it can't write is said, never
  fatal). Prints only "signed in: yes · access token expires 2026-10-23 16:20 · refresh token:
  present · account 1a2b3c4d" (`--json` too). A bad file or a refused sign-in installs nothing and
  exits 77; unreachable, 69 (see the questions).
- **`recordings plaud check`:** one current-user read, refreshing first when due; 0 with the same
  line, 77 "signed in: no · no sign-in yet / Plaud refused the sign-in: at the Mac: `make
  plaud-sign-in`", 69 for an outage (which records nothing), 78 for the modes, 75 for a held
  `tokens.lock`. A success ends the pause and the blocker; a refusal records them.
- **`make plaud-sign-in`:** `SERVER` and `REPO_DIR` from `-include local.mk`, expanded on the Mac;
  a temporary `HOME` from `mktemp -d` under `$TMPDIR`, `chmod 700`, removed by `trap … EXIT` (and
  `HUP INT TERM` exit through it); `HOME=… $(PLAUD_CLI) login` (`PLAUD_CLI ?= plaud`); the token
  file over ssh's stdin into `sh docker/up-deployed.sh exec -T worker recordings plaud
  token-import -`; each failure names its step (2 login, 3 ssh 255, 4 the server's 77/69/78/other).
  A `make help` line. Tested (`test_plaud_sign_in_target.py`) with a fake `plaud` and a fake `ssh`
  on `PATH`: the HOME's place and mode during login and its removal on success and on each
  failure, the literal command ssh carries, the stdin the server receives, no token in any
  argument or output, and `local.mk`.

### §17.2 and §17.4, doctor and the settings

- Doctor: no token store is a problem again, `SIGN_IN_NEEDED` = "sign-in needed: run `make
  plaud-sign-in` at the Mac (runbook, Plaud sign-in)" (exit 78). I removed the Phase 1 switch
  (`SIGN_IN_IS_A_PROBLEM`) and `SIGN_IN_NOTE` rather than flipping the switch, so no dead branch is
  left; `compose_smoke.WORKER_PROBLEMS` gains the problem and `WORKER_NOTES` goes;
  `test_deploy_template.py` pins it. Doctor also reports `token_expires`, `refresh_token` and
  `refresh_expires` (in `[archive] default_timezone`, never a value or a hash); a file the store
  can't use is a sign-in problem; no refresh token is a warning, or a problem once the access token
  has expired; a refresh token expired is a problem, one expiring within 7 days a warning.
- `[plaud] token_url` (§22's setting for the token endpoint): unset, `api_base` + `/oauth/token`;
  https or loopback http, named by key in errors. Its template line (commented, like `api_base`),
  its row in `test_template_settings.py`, spec §17.4 and a Revisions line. The template's
  `token_store` comment now names `make plaud-sign-in`.

### The fake server's sign-in (§10.6 step 3, `fake_plaud.py`)

`plaud_server.signed_in(clock)` turns on `FakeSignIn`: the token endpoint (`POST TOKEN_PATH`,
form parsed, never logged) and the current user (`GET USER_PATH`), API routes needing a good
bearer, token expiry on the store's clock. `issue()` returns a vendor-like file (with an invented
e-mail and user ID for the importer to drop). Faults: `refuse_refresh()` / `refuse`, `fault` (any
Response, including `delay` for a timeout), `rotate`, `reject_new`, `refused_paths`,
`user_path_missing`, `revoke()`, `delay`. It stays importable from outside tests (no pytest at
module level; it imports `USER_PATH` and `TOKEN_PATH` from the code).

### Docs

CLAUDE.md's `tokens.json` rule (§19); the README's config row and a `make plaud-sign-in` bullet and
command line; the runbook's section 6 (intro, step 6 is now the sign-in and `plaud check`, step 7's
doctor line) and a new "## Plaud sign-in" section (when, how, every failure by step, then, the
guesses), and "When the Plaud alert comes" and "Restoring for real" lines; `docs/user-guide.md`
created with "Signing in to Plaud"; the spec's §17.2 wording, §17.4, the C4.01 to C4.05 rows and two
Revisions lines; the plan's Now line; `docs/superpowers/plans/2026-11-01-stage-2b-guesses.md`
created (header per AREAS.md, the P1 wrap's row, and A1's seven rows).

## Tests

- New: `test_plaud_tokens.py` (51), `test_plaud_sign_in_cli.py` (15), `test_plaud_sign_in_target.py`
  (8); in `test_worker.py` the pause (2) and C4.01 to C4.04 (3); in `test_doctor.py` the problem
  and the expiries (5, replacing the two Phase 1 note tests); `test_runbook.py` (1 new, 1 updated);
  `test_plaud_settings.py` (token_url, and the target test replacing the one that skipped once the
  target existed); `test_template_settings.py`'s row.
- `make check` before the commit: pytest 2,086 passed (and again under `TZ=UTC`), Vitest 458 passed, ruff clean, gitleaks clean (451 commits). No flaky test seen.
- No UI file changed, so `make test-js`, `make build` and `make e2e` weren't needed beyond
  `make check`'s Vitest run.

## From audio-router, and from the spec alone

- **From audio-router** (`src/audio_router/archive/sources/plaud.py`, `cli.py`, and
  `tests/test_scaffold.py`; read only): the vendor CLI is `plaud`; its sign-in is `plaud login`
  (its error text); it writes `~/.plaud/tokens.json` (`TOKEN_FILE`) with the access token under
  `access_token`; it refreshes with `plaud me`, so audio-router shows no token URL, no refresh
  request and no other key.
- **From the spec alone, or guessed** (each a row in the guesses file): the refresh token's key and
  every expiry key; the token endpoint, its form and its reply; the refusal codes (the spec's own
  default: any 4xx but 408 and 429); rotation with no grace; the current-user path and its account
  key; "no expiry means refresh only after a 401". The thresholds (5 minutes, 10%, 7 days) and the
  file format are the spec's.

## Questions for Dan

1. **`token-import` when Plaud can't be reached:** §10.5 says "If the call fails it installs
   nothing and exits 77." I install nothing but exit **69** (Plaud unreachable) for an outage, and
   77 only for a file it can't use or a sign-in Plaud refuses, so `make plaud-sign-in` says "Plaud
   could not be reached… run it again later" instead of telling him his sign-in was refused. Keep
   69, or 77 for every failure as the spec words it?
2. **No refresh token** (S1's "stop and ask Dan"): doctor warns, and makes it a problem only once
   the access token has expired; the runbook's step 6 says to stop and ask on "refresh token:
   missing". Should `token-import` refuse such a sign-in outright instead?
3. **A missing token file at a run's sign-in step** (a Sync now, say, before any sign-in) opens the
   `sign-in` blocker, as a refusal does (the spec names only a refused refresh). Fail-closed, and
   Status says "sign-in needed" either way; a scheduled turn with no file is "paused", no blocker.

## What's left (for later areas)

- **A5** (the sync turn): at the run's start, `tokens.check_sign_in(store, client, worker.db,
  now=…)` with `TokenStore(settings.token_store, token_url=settings.token_url, clock=worker.clock)`
  and `PlaudClient(store, api_base=settings.api_base)`; on any `PlaudAuthError` later in the run,
  `signin.record_refused(worker.db, store.path, now=…)`; `PlaudCallRefused` is that recording's
  failure; `TokenStoreUnsafe` and `LockTimeout` end the run as errors. The scheduled turn's pause is
  already in `scheduled_turn`; a Sync now must bypass it (it tries once anyway).
- **A6** (the views): Status's sign-in number and line ("Plaud sign-in needed since 23:00 · New
  recordings wait on Plaud: nothing is lost · At the Mac: `make plaud-sign-in`") from the
  `sign-in` blocker's `since` and `K_SIGN_IN`; Needs attention's "sign-in expiring within 7 days"
  from `K_SIGN_IN.refresh_expires_at` (BD3); the flow test's C4 rows with the real turn; the main
  spec's §9.1 token paragraph and §5 (A6's list). `make demo-live` can sign its worker in with
  `FakeSignIn.issue()` through `token-import`.
- The runbook's section 6 still checks out "Phase 1's merge commit" (B3's setup page).
