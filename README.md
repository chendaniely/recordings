# recordings

A self-hosted library for recordings: Plaud, audio and video files, talks. One central
archive, transcripts and notes from local models (and Claude when asked), and a web UI for
listening, reading and tagging. Design: `docs/superpowers/specs/2026-10-08-recordings-design.md`.

Stage 1 built the read-only Library over a demo archive. Stage 2a moves the real archive in: a
locked write path, `recordings import-audio-router`, restic backups to an append-only
rest-server on the NAS with a monthly restore test, the NAS mirror, alerts, and Status.
`docs/runbooks/first-run.md` is the first real run.

## Live demo

<https://chendaniely.github.io/recordings/> runs the bundled public-domain demo entirely in
your browser, with Python on WebAssembly (Shinylive). The first load is about 16 MB, and the
browser caches it. It never touches real data.

## Try the demo

```bash
make setup   # uv sync, npm ci, shinyreact skills
make demo    # builds the UI and serves the demo archive at http://127.0.0.1:8000
```

The demo uses four public-domain recordings (see `demo/CREDITS.md`) and never touches real data.

## Layout

- `packages/core`: `recordings`, the library and CLI that do the work.
- `packages/ui`: `recordings-ui`, the FastAPI + shinyreact interface over it.
- `demo/`: the demo archive and how it is built.
- `pages/`: the Live demo's entry, which `make pages` exports with Shinylive.

MIT licensed.

## Developing

```bash
make test         # pytest and Vitest
make e2e          # Playwright tests against `make demo`
make pages        # the static GitHub Pages demo, in _site/ (only demo/archive/ is exported)
make pages-serve  # serve _site/ like Pages, at http://127.0.0.1:8008/recordings/
make pages-test   # Playwright smoke test of _site/, after `make pages`
```

## Docker

```bash
make docker                                         # demo mode, http://127.0.0.1:8000
cp config.example.toml config.toml                  # app settings (git-ignored)
cp docker/deploy.example.conf docker/deploy.env     # host folders, bind address, UID/GID (git-ignored)
# first time only: run `recordings init` and `recordings backup init` before this (runbook step 2.4)
make deploy                                         # builds, tags the image by commit, starts web + backup
make rollback TAG=<tag>                             # starts an older image again, never building one
make prune-images                                   # removes old images, keeping the newest five
make deploy-smoke                                   # the real compose file, once, against temporary folders
```

- **First time: initialise before you deploy.** Do not run `make deploy` on a new archive. `recordings init`
  (the archive) and `recordings backup init` (the repository) come first, and the runbook's step 2.4
  runs them. Every deploy after that is `make deploy`.
- **Images are tagged by commit.** `make deploy` refuses a tree with uncommitted or untracked
  changes, because a `-dirty` image has no commit to go back to.
- **A deploy waits for a running job.** Recreating `backup` stops the job it runs, and the job
  starts again from the beginning: hours for a restore test or a check. So `make deploy` refuses
  while a job holds the ops lock, and says which; `make deploy FORCE=1` deploys anyway. It then
  waits up to 3 minutes for `web` to pass its health check, and fails if it doesn't: roll back.
- **Rolling back** starts an image that is already on the machine: `docker image ls recordings`
  lists the tags. Each deploy leaves an image on the system disk. `make prune-images` removes all
  but the newest five commit tags (`KEEP=<n>` for another number), never one a container was made
  from. Never `docker image prune -a` or `docker system prune -a`: they remove every rollback
  target. `docker system df` shows what images and the build cache take; `docker builder prune`
  empties the cache, which only makes the next build slower.
- **At boot,** `docker/recordings.service` waits for the data disk and the Tailscale address,
  then starts the containers again on the tag they last ran (`docker/up-deployed.sh`). The
  runbook installs it. It uses the `compose.yml` and `deploy.env` the checkout holds at boot, so
  pull only to deploy.
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
| Host folders, config path, bind address and port, the trusted proxy (`RECORDINGS_FORWARDED_ALLOW_IPS`), UID/GID | `docker/deploy.env` (template: `docker/deploy.example.conf`) | stage 1 |
| `RESTIC_PASSWORD`; `RESTIC_REST_PASSWORD`, rest-server's; the NAS's SSH key and host key, `RECORDINGS_NAS_SSH_KEY` and `RECORDINGS_NAS_KNOWN_HOSTS` (files only); `RECORDINGS_NTFY_TOPIC`; `RECORDINGS_DEADMAN_URL`; and rest-server's certificate, the `nas_cacert` file that `[backup] cacert` names | secret files, as `…_FILE` (Docker secrets) | stage 2a |
| `RECORDINGS_NTFY_TOKEN`, only for an ntfy server that needs one | a secret file, which `RECORDINGS_NTFY_AUTH_HOST` in `docker/deploy.env` names (unset: no token is mounted) | stage 2a |
| `RECORDINGS_PLAUD_TOKEN` | a secret file, as `RECORDINGS_PLAUD_TOKEN_FILE` | stage 2b |
| `RECORDINGS_SPARK_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` | environment, or `…_FILE` | stage 4 |

Secrets never go in `config.toml`, `deploy.env`, the image or the repo. `recordings doctor`
reports which are set, never their values. The containers run as a non-root user, with
read-only file systems.
