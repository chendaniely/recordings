# recordings

A self-hosted library for recordings: Plaud, audio and video files, talks. One central
archive, transcripts and notes from local models (and Claude when asked), and a web UI for
listening, reading and tagging. Design: `docs/superpowers/specs/2026-10-08-recordings-design.md`.

**Using it:**
- [`docs/user-guide.md`](docs/user-guide.md): start here. Every screen and task, in plain words,
  and what each Status line means and asks of you.
- [`docs/setup.md`](docs/setup.md): setting up the Plaud sync, from a deploy to the first real
  sync, dry run first.
- [`docs/runbooks/first-run.md`](docs/runbooks/first-run.md): the server-side detail, every
  command and message.
- [`CHANGELOG.md`](CHANGELOG.md): what changed, stage by stage.

Stage 1 built the read-only Library over a demo archive. Stage 2a moves the real archive in: a
locked write path, `recordings import-audio-router`, restic backups to an append-only
rest-server on the NAS with a monthly restore test, the NAS mirror, alerts, and Status.
`docs/runbooks/first-run.md` is the first real run.

Tagging, part A (`docs/superpowers/specs/2026-10-15-tagging-a-design.md`) adds the triage tools:
Finder-style selection, the tag picker (`T`, or the **+** on a row), × on chips, drag and drop
onto sidebar tags, the Tags page grid at `#/tags`, Undo for everything (Cmd/Ctrl+Z), and a
confirmation before any recording loses its last private tag. `make demo` saves tags to its
temporary copy; the Live demo keeps them in memory until you reload. On a phone, the Tags tab opens
the Library filtered to Untagged and the **+** opens a bottom sheet; there are no key hints on touch.

The first tag you apply to the real archive ends the disposable window (main spec §19): after it,
rolling back by re-import loses your tags, and the rollback is a restore
(`docs/runbooks/first-run.md`, "Rolling back, until the first real tag"). Decided 2026-10-23,
refining 2026-10-22: the first real tag waits until stage 2b's first real Plaud import and the
backup after it (the runbook's import section, added in stage 2b's second phase). If tagging is
off, that runbook's "Tagging" section says why and what to do.

Stage 2b (`docs/superpowers/specs/2026-10-23-stage-2b-design.md`) brings Plaud in and keeps it
up: a `worker` container signs in (`make plaud-sign-in`, from the Mac), lists the account every 20
minutes, imports what is new, tags `private` what your patterns say before any of its content
lands, and never fetches what your consent list or the keep-out word keeps out. Edits you make in
Plaud (a title at the next sync; speaker names and notes within about a day) follow, and a breaker
pauses that if Plaud starts sending something new on many recordings at once. **Re-sync from
Plaud** (a recording's Details, or a selection in the Library) checks edits at once, and **Sync
everything** checks the whole account and writes a manifest (monthly by itself). The **Add** tab
is the Sync screen (Compare, Sync now, Import, Release, Accept this listing, Sync everything,
Park), and Status reports. The first real import is a supervised catch-up on the server, dry run
first: `docs/setup.md` walks through it, and the runbook's sections 6 to 8 have the detail.
`docs/user-guide.md` says what each screen shows and what to do. Plaud's API details are the
build's guesses (the spike sessions were skipped), each listed in
`docs/superpowers/plans/2026-11-01-stage-2b-guesses.md` with how you would notice it's wrong.

Keeping a recording out: start its title in Plaud's app with the keep-out word (`noarchive`), or
put its ID on the consent list. Once a recording has come in, deleting it in Plaud, or renaming
it, doesn't remove it here: a rename to the keep-out word tags it `private` and flags it for you
to take out by hand (the runbook). Whether a rename on the phone usually beats the first sync
(spike S9) hasn't been measured; the sync lists Plaud every 20 minutes, so rename it before it
uploads, or at once. The word is forgiving ("No archive:", "NOARCHIVE"), so an ordinary title that
happens to start with it stays out until you press **Release** on the Sync screen.

## Live demo

<https://chendaniely.github.io/recordings/> runs the bundled public-domain demo entirely in
your browser, with Python on WebAssembly (Shinylive). The first load is about 16 MB, and the
browser caches it. It never touches real data. Its **Add** and **Status** tabs show stage 2b's
Plaud screens from a hand-written, invented view (`demo/plaud-sync.json`): a hold, the breaker,
kept-out rows with Release, parked rows and the arrivals cue, every button off.

## Try the demo

```bash
make setup   # uv sync, npm ci, shinyreact skills
make demo    # builds the UI and serves the demo archive at http://127.0.0.1:8000
```

The demo uses four public-domain recordings (see `demo/CREDITS.md`) and never touches real data.

To try the Plaud sync (stage 2b) on the Mac, with the real web app and worker against a fake Plaud
server and a synthetic account:

```bash
make demo-live   # prints the URL and the commands to type; Ctrl-C stops everything
```

It works in a scratch folder under `$TMPDIR`, removed when it stops, and refuses to start if any
setting points at a real archive, a real sign-in or Plaud itself.

## Layout

- `packages/core`: `recordings`, the library and CLI that do the work.
- `packages/ui`: `recordings-ui`, the FastAPI + shinyreact interface over it.
- `demo/`: the demo archive and how it is built.
- `pages/`: the Live demo's entry, which `make pages` exports with Shinylive.

MIT licensed.

## Developing

```bash
make lint         # ruff check and ruff format --check
make test         # pytest and Vitest
make e2e          # Playwright tests against `make demo`
make e2e-live     # Playwright tests against `make demo-live` (a few minutes)
make pages        # the static GitHub Pages demo, in _site/ (only demo/archive/ and demo/plaud-sync.json)
make pages-serve  # serve _site/ like Pages, at http://127.0.0.1:8008/recordings/
make pages-test   # Playwright smoke test of _site/, after `make pages`
```

## Docker

```bash
make docker                                         # demo mode, http://127.0.0.1:8000
cp config.example.toml config.toml                  # app settings (git-ignored)
cp docker/deploy.example.conf docker/deploy.env     # host folders, bind address, UID/GID (git-ignored)
# first time only: run `recordings init` and `recordings backup init` before this (runbook step 2.4)
make deploy                                         # builds, tags the image by commit, starts web, backup and worker
make rollback TAG=<tag>                             # starts an older image again, never building one
make prune-images                                   # removes old images, keeping the newest five
make deploy-smoke                                   # the real compose file, once, against temporary folders
make consent-add ID='<id>'                          # from the Mac: adds a Plaud ID to the server's consent list
make plaud-sign-in                                  # from the Mac: signs the server's worker in to Plaud
make plaud-dry-run                                  # on the server: the first catch-up's dry run (imports nothing)
make plaud-catch-up                                 # on the server: the first catch-up, supervised
```

- **First time: initialise before you deploy.** Do not run `make deploy` on a new archive. `recordings init`
  (the archive) and `recordings backup init` (the repository) come first, and the runbook's step 2.4
  runs them. Every deploy after that is `make deploy`.
- **Images are tagged by commit.** `make deploy` refuses a tree with uncommitted or untracked
  changes, because a `-dirty` image has no commit to go back to.
- **A deploy waits for a running job.** Recreating `backup` stops the job it runs, and the job
  starts again from the beginning: hours for a restore test or a check. Recreating `worker` stops
  a Plaud run: a routine sync is safe to interrupt, but Sync everything, the first catch-up and
  the baseline are long. So `make deploy` refuses while a job holds the ops lock, or one of those
  three holds the sync's `plaud` lock, and says which; `make deploy FORCE=1` deploys anyway. It then
  waits up to 3 minutes for `web` and `worker` to pass their health checks (the worker's is
  `recordings worker --alive`), and fails if either doesn't: roll back.
- **Rolling back** starts an image that is already on the machine: `docker image ls recordings`
  lists the tags. Each deploy leaves an image on the system disk. `make prune-images` removes all
  but the newest five commit tags (`KEEP=<n>` for another number), never one a container was made
  from. Never `docker image prune -a` or `docker system prune -a`: they remove every rollback
  target. `docker system df` shows what images and the build cache take; `docker builder prune`
  empties the cache, which only makes the next build slower. To a release from before stage 2b,
  follow the runbook's "Rolling back across 2b" instead: this checkout's `compose.yml` would start
  a `worker` the older image can't run.
- **At boot,** `docker/recordings.service` waits for the data disk and the Tailscale address,
  then starts the containers again on the tag they last ran (`docker/up-deployed.sh`). The
  runbook installs it. It uses the `compose.yml` and `deploy.env` the checkout holds at boot, so
  pull only to deploy.
- **`make consent-add ID='<id>'`,** run on the Mac, adds a Plaud ID to the server's consent list
  (stage 2b). It needs `local.mk` at the repo's top (git-ignored), with `SERVER`, your ssh name
  for the homelab server, and `REPO_DIR`, the checkout there. Keep the single quotes: the Sync
  screen's Copy ID holds backticks, which a shell would run. It checks the ID first (a bare ID,
  the `of_` form, or the Copy ID form), appends the line by writing a new file and renaming it (it
  refuses a missing, empty or comments-only list), and prints what the worker reads. The runbook's
  "Keep a recording out for good" has the rest.
- **`make plaud-sign-in`,** run on the Mac with the same `local.mk`, signs the server's worker in
  to Plaud (stage 2b): the vendor CLI's `plaud login` under a temporary `HOME`, so the Mac's own
  Plaud sign-ins are untouched, then its token file over ssh into `recordings plaud token-import
  -`, which makes one cheap call and installs it. It prints one line, never the token. The worker
  refreshes the sign-in by itself; when Plaud refuses a refresh, Status says "sign-in needed" and
  this is the way back. The runbook's "Plaud sign-in" has the rest.
- **`make plaud-dry-run`, then `make plaud-catch-up`,** run on the server in its checkout, are
  stage 2b's first real Plaud import (the runbook's sections 7 and 8): the dry run lists and
  classifies everything on Plaud and imports nothing; the catch-up takes a `pre-import` backup,
  checks every title once, imports what is waiting (`recordings plaud sync --catch-up`), validates
  the archive and takes a `post-import` backup. After it the scheduled sync starts by itself.
- **Set `[server] allowed_hosts`** in `config.toml` to the names you reach the app by, such
  as its host name, Tailscale name and Tailscale IP. Localhost and `base_url`'s host are
  always allowed; the app refuses any other name. Give each name alone, with no port or
  scheme (`my-homelab`, not `my-homelab:8000`), using letters, digits, hyphens and dots only (no
  underscores); the app refuses to start otherwise.
- **Behind a TLS-terminating reverse proxy** (for example one that serves `https://…`), the
  proxy must pass the `Host` header through unchanged (nginx: `proxy_set_header Host $host;`).
  nginx's default sends `127.0.0.1:8000`, a name the app always allows, which would let a
  DNS-rebinding page past the Host check. The proxy must also set `X-Forwarded-Proto` itself
  and be trusted for it: set uvicorn's `FORWARDED_ALLOW_IPS` to the one address the app sees the
  proxy connect from (the client address in the app's access log; the default trusts only
  localhost). Under Docker the app does not see the proxy's LAN address: it sees the Compose
  network's gateway, the address the bridge gives the host (the first address of the network's
  subnet, for example `172.18.0.1`). Find it with
  `docker network inspect recordings_default --format '{{(index .IPAM.Config 0).Gateway}}'`, or from
  the client address in the web container's access log, and put it in `docker/deploy.env` as
  `RECORDINGS_FORWARDED_ALLOW_IPS=<that address>`, then `make deploy`. The address can change when
  the network is recreated, so check it after `docker compose down`. Never `*` or a range: every machine in it could then set the scheme, and the client
  address the app logs. The app compares a websocket's or write's `Origin` with the scheme and
  `Host` it sees. Without this, every websocket and every write is refused. Plain
  `http://my-homelab:8000` needs nothing.

The first real run, step by step, is `docs/runbooks/first-run.md`.

## Configuration and secrets

`config.toml` is read from the current directory, unless `RECORDINGS_CONFIG` names another
file. Docker sets it to `/config/config.toml`.

| Setting | Where | Needed from |
|---|---|---|
| Archive, state and index paths, the writer ID, backups, mirror, alerts | `config.toml` (template: `config.example.toml`) | stage 1 / 2a |
| The names the app answers to | `[server] allowed_hosts` in `config.toml`, plus `RECORDINGS_ALLOWED_HOSTS` (comma-separated) | stage 1 |
| Host folders, config path, bind address and port, the trusted proxy (`RECORDINGS_FORWARDED_ALLOW_IPS`), UID/GID; and, from stage 2b, the consent folder (`RECORDINGS_CONSENT_HOST`), the Plaud sign-in's folder (`RECORDINGS_PLAUD_HOST`) and the ntfy topic's file (`RECORDINGS_ALERTS_TOPIC_HOST`) | `docker/deploy.env` (template: `docker/deploy.example.conf`) | stage 1 / 2b |
| `RESTIC_PASSWORD`; `RESTIC_REST_PASSWORD`, rest-server's; the NAS's SSH key and host key, `RECORDINGS_NAS_SSH_KEY` and `RECORDINGS_NAS_KNOWN_HOSTS` (files only); `RECORDINGS_DEADMAN_URL`; and rest-server's certificate, the `nas_cacert` file that `[backup] cacert` names | secret files, as `…_FILE` (Docker secrets) | stage 2a |
| `RECORDINGS_NTFY_TOPIC`, once ntfy is set up, and `RECORDINGS_NTFY_TOKEN`, only for an ntfy server that needs one | secret files, which `RECORDINGS_ALERTS_TOPIC_HOST` and `RECORDINGS_NTFY_AUTH_HOST` in `docker/deploy.env` name (unset: none is mounted) | stage 2a; optional from stage 2b |
| `RECORDINGS_DEADMAN_SYNC_URL`, the Plaud sync's own Healthchecks ping URL, for the `worker` container only | a secret file, as `RECORDINGS_DEADMAN_SYNC_URL_FILE` | stage 2b |
| The Plaud sign-in, for the `worker` container only (never an environment variable) | `tokens.json` in the sign-in's folder (`RECORDINGS_PLAUD_HOST`), installed from the Mac by `make plaud-sign-in` | stage 2b |
| `RECORDINGS_SPARK_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` | environment, or `…_FILE` | stage 4 |

Secrets never go in `config.toml`, `deploy.env`, the image or the repo. `recordings doctor`
reports which are set, never their values. The containers run as a non-root user, with
read-only file systems.
