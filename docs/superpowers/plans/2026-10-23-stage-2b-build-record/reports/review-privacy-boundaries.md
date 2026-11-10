# Review: security and privacy at the boundaries (stage-2b, 694b6a9 against 8deb039)

**Count:** Critical 0 · High 0 · Medium 0 · Low 2

Read-only review. I edited, staged and ran nothing in the worktree, opened no token, `.env` or
secrets file, and made no Plaud call.

---

## Findings

### L1. `api_base` and `token_url` accept any https host, so a setting can send the sign-in anywhere

- **Severity:** Low (defence in depth). Only someone who can write `config.toml` can trigger it,
  and `config.toml` is Dan's file, mounted read-only in every container.
- **Location:**
  - `packages/core/src/recordings/plaud/endpoint.py:58-81`: `_check_url`, `check_api_base` and
    `check_token_url` check the scheme only.
  - `packages/core/src/recordings/plaud/settings.py:70` and `:81` read the two settings.
  - `packages/core/src/recordings/plaud/client.py:700` sends the bearer token to `api_base`.
  - `packages/core/src/recordings/plaud/tokens.py:368-373` sends the refresh POST, with
    `refresh_token`, `client_id` and `client_secret`, to `token_url`.
- **Trigger:**
  1. `config.toml` gets `[plaud] api_base = "https://collector.example"`, or
     `token_url = "https://collector.example/t"`.
  2. `check_api_base` and `check_token_url` pass it: https, plain ASCII, no userinfo, `?` or
     `#`.
  3. At the worker's next run, `check_sign_in` calls `store.access_token()`, then a GET carries
     `Authorization: Bearer <access token>` to `collector.example`. `token-import`'s
     `current_user()` does the same with a newly imported token.
  4. With `token_url` set, the first refresh POSTs the refresh token and the client secret
     there.
  5. Nothing ties `token_url`'s host to `api_base`'s, or either one to `plaud.ai`.
- **What holds:** the scheme rule (https, or http to the loopback only) matches spec §10.1 as
  written. Userinfo, a query and a fragment are refused. Redirects are refused, and
  `Authorization` is an unredirected header. Presigned links never get the token.
- **Suggested fix:** accept only a host that is `plaud.ai` or one of its subdomains, or the
  loopback. Require `token_url` to be on the same allowed set.
- **Verdict:** CONFIRMED. I traced every step in the code.

### L2. The never-backed-up guard looks only at `[backup] extra_paths`, and falls back to the default when `[plaud]` can't be read

- **Severity:** Low. Both triggers need a non-default config by Dan. In the Docker layout, the
  backup container doesn't mount the token's folder, which blocks trigger (b).
- **Location:**
  - `packages/core/src/recordings/backup.py:393-401`: `token_folder` returns `/plaud` on any
    `ConfigError`.
  - `packages/core/src/recordings/backup.py:404-427`: `never_backed_up` is applied to
    `extra_paths` entries only.
  - `packages/core/src/recordings/doctor.py:159-168`: `_no_plaud_folder_named` returns silently
    on `ConfigError`.
  - `packages/core/src/recordings/plaud/settings.py:66-68`: `token_store` may be any absolute
    path.
- **Trigger (a), a token inside the archive:**
  1. Dan sets `[plaud] token_store = "/archive/.signin/tokens.json"` and makes the folder at
     0700. `unsafe_modes` is satisfied.
  2. `make plaud-sign-in` installs the token there through `TokenStore.install`.
  3. `run_backup` backs up the archive whole. It excludes only `<archive>/.tmp` and `.*.tmp`
     (`backup.py:272-275`), so `tokens.json` goes into every restic snapshot.
  4. The mirror's rsync excludes the same two patterns (`mirror.py:155-156`), so it copies the
     token to the NAS mirror too. External agents read the archive tree.
  5. What catches it: `doctor --role backup` or `--role web` reports it, through
     `_plaud_folder_here` ("is mounted here"). `run_backup` itself never refuses.
- **Trigger (b), fail-open:**
  1. `token_store` names a non-default folder, such as `/srv/x/plaud/tokens.json`.
  2. Any other `[plaud]` key fails to parse, for example `schedule_minutes = "20"` or a bad
     `api_base`. `plaud_settings` then raises `ConfigError`.
  3. `token_folder()` returns `/plaud` instead.
  4. An `extra_paths` entry of `/srv/x/plaud`, or a parent folder that isn't the state folder,
     passes `never_backed_up`. Doctor's `_no_plaud_folder_named` says nothing.
  5. Outside Docker, `run_backup` would then back up `tokens.json`. In Docker, `missing_paths`
     refuses it, because the backup container doesn't mount that folder.
- **Suggested fix:**
  - Read `cfg.data["plaud"]["token_store"]` directly when it is a string, rather than through
    the whole of `plaud_settings`.
  - Fail closed: refuse the backup when the token's folder can't be determined.
  - Make `run_backup`, not just doctor, refuse when `token_store` lies under the archive, the
    config or any extra path.
- **Verdict:** CONFIRMED in code for both triggers. I did not run a backup to show the snapshot's
  contents.

---

## Checked, no defect found

### The Plaud sign-in

The token reaches no log, exception message, reply, screen, backup, `ps` command line or loose
file mode.

- **Token store, `tokens.py`:**
  - The repr hides every secret field.
  - `TokenImportError` and `TokenStoreUnsafe` texts name keys and paths only.
  - Writes create the file with `O_EXCL` at 0600, fsync it, rename it and fsync the folder.
  - `tokens.lock` is 0600.
  - The folder and the file are refused if wider than 0700 and 0600, or owned by another user.
- **Client, `client.py`:**
  - Every error is the module's own class, with a fixed message, raised `from None`.
  - API calls refuse redirects, and `Authorization` is an unredirected header.
  - Presigned links get no `Authorization` and must be https.
  - Proxies are https only, through a CONNECT tunnel.
- **`token-import`, `commands.py:805-876`:**
  - It reads stdin only, capped at 64 KiB, and never echoes the parser's error text.
  - It makes one cheap call before installing, and prints only facts and the account's 8-hex
    hash.
- **`make plaud-sign-in`, `Makefile:166-190`:**
  - It works in a `mktemp -d` HOME at 0700 under `$TMPDIR`, with an EXIT trap that removes it.
  - The token goes over ssh on stdin, never as an argument or an environment variable.
  - `up-deployed.sh` reads nothing from stdin before it `exec`s `docker compose exec -T`.
- **Repo and image:** `.gitignore` and `.dockerignore` exclude `tokens*.json`, `.plaud/` and
  `local.mk`.

### The UI boundary

- **No client, token store or sync, even at runtime:**
  - I traced every top-level and function-level import that `recordings_ui` reaches: `worker`,
    `baseline`, `flags`, `health`, `hold`, `cache`, `breaker`→`sweep`, `fullstate`, `schedule`,
    `runlock`, `writer`, `ops`→`backup`→`plaud.settings`.
  - None loads `plaud.client`, `plaud.tokens`, `plaud.sync` or `sources.plaud`. `compare.py`
    names them only under `TYPE_CHECKING`.
  - `test_plaud_views.py` also checks this at runtime.
- **Requests only from a person's action:**
  - Every `plaud.send` in the frontend is in an `onClick` handler: `SyncPage`, `SyncPanel`,
    `SelectionBar`, `PlaudDetails`.
  - The `plaud_request` input mounts with null, and the server's `@reactive.event` ignores None.
  - The URL hash only picks the route, and nothing Plaud-related is kept in browser storage.
- **Kinds the worker doesn't handle:** `check_request` refuses them with NOT_YET before any
  insert (`recordings_ui/plaud.py:156`). The worker refuses them again (`worker.py:1289`).
- **Cross-site websockets:** `HostGuard` refuses one whose `Origin` isn't the app's own.

### Logs and messages

- I grepped every `log.*` call in `worker.py`, `plaud/*`, `alerts.py`, `doctor.py`, `cli.py` and
  `recordings_ui/*`.
- They interpolate fixed phrases, counts, recording IDs, `id_hash`, part and field names, job
  names and `type(exc).__name__`. None has `str(exc)` or `exc_info`.
- CLI `_fail(str(exc))` is used only for exceptions with fixed texts: `ConfigError`,
  `TokenStoreUnsafe`, `KeepOutWordsError`, `NoSyncYet`, `IndexUnavailable`. The rest print the
  class name.
- The Healthchecks sync ping (`worker.py:139`) sends an empty body.

### Never backed up, never exported

- **The cache:** `never_backed_up` refuses `plaud-cache.db`, `/state` itself, `/plaud` and
  `/run/secrets`, apart from L2.
- **The manifests** (`plaud/full.py:535-588`) hold Plaud IDs, recording IDs, snapshot paths
  (`source/plaud-<stamp>.json`), state phrases, counts and part hashes. The `title` and
  `speakers` parts are left out. No title.
- **The Pages build** (`scripts/build_pages.py`) exports `demo/archive` and `demo/plaud-sync.json`
  only, plus the vendored public source. Its symlinks are refused, and the canned view is
  synthetic.

### `make demo-live` (`scripts/demo_live.py`)

- The children's environment drops `RECORDINGS_*`, `RESTIC_*` and `*_proxy`, then sets its own
  `RECORDINGS_CONFIG` and ping URL.
- `refuse_unless_scratch` loads the config as the children will. It refuses unless the archive,
  state, index, consent list and token store are all inside the scratch folder under `$TMPDIR`,
  and `api_base`, `token_url` and the ping all point at this process's own loopback fakes.
- The web app, the fake Plaud and the fake Healthchecks all bind to 127.0.0.1.

### Docker and deploy

- No secret appears on a command line.
- Only `worker` mounts `/plaud`.
- The worker has no ports. Its egress is open, and spec §17.1 accepts this ("Not enforced").
- Manifests are written at 0600 in a 0700 folder, and the runbook makes `state` 0700.
- `consent-add.sh` checks for 32 hex digits before using the ID in a remote shell.
- `deploy-guard.sh` and `ops-busy.py` interpolate only constants and docker output.

### The public repo

- **Searched:** the whole diff's added lines, including tests, fixtures, docs and `demo/`. I
  looked for `/Users/` and `/home/` paths, emails, IP addresses other than 127.0.0.1, tailnet and
  Synology names, `hc-ping.com` and ntfy URLs, `Bearer` and `eyJ` tokens, and hex runs of 20 or
  more.
- **Found:** only synthetic or commit-pin values. The `of_000…0N` IDs, `0123…`, `fedc…` and the
  GitHub Action SHAs are synthetic or pins. `3fa91c2e` and `2528c39b` already exist on main as
  format examples.
- **Names:** "Alex Kim", "Synthetic …" and "Ada" are invented examples. "work/lander" is a tag
  example.

---

## Notes, not reported

- **Titles left in the cache's free pages:** the cache nulls and deletes titles
  (`cache.py:257-285`, `take_preview`) without `PRAGMA secure_delete`. Whether old titles stay in
  free pages depends on the SQLite build. I believe Debian, which the `python:3.14-slim` runtime
  uses, compiles with `SQLITE_SECURE_DELETE`, but I did not verify it. The file is never backed
  up, and the state folder is 0700.
- **The web app can't see a consent-list change while the worker is down:** the web's title
  re-check (`recordings_ui/plaud.py:879-898`) uses the consent IDs the cache recorded at the last
  compare, and the worker's last read of the list. The web never mounts `/consent`, by design.
  So a list change made while the worker is stopped isn't seen until the worker's tick runs
  `purge_titles`. Until then a newly consent-listed recording's title can still show on the Sync
  screen. This is spec-level (§9.5), and only Dan's own browser sees it.
- **Presigned URLs in the archive's snapshots:** they are written into
  `source/plaud-<stamp>.json` (`importer.py:490`). The design stores the raw envelope, and the
  links expire (300 s for blocks, 24 h for audio) and grant only audio already in the archive.
