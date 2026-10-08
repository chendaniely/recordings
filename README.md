# recordings

A self-hosted library for recordings: Plaud, audio and video files, talks. One central
archive, transcripts and notes from local models (and Claude when asked), and a web UI for
listening, reading and tagging. Design: `docs/superpowers/specs/2026-10-08-recordings-design.md`.

Stage 1 (this release) is the read-only Library over a demo archive.

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
```

To deploy for real, on the host:

```bash
cp config.example.toml config.toml                  # app settings (git-ignored)
cp docker/deploy.example.env docker/deploy.env      # host paths, bind IP, UID/GID (git-ignored)
# Before the first deploy, create the archive folder (RECORDINGS_ARCHIVE_HOST) owned by
# RECORDINGS_UID:RECORDINGS_GID. Otherwise Docker creates it, owned by root.
sudo mkdir -p /srv/recordings/archive && sudo chown <uid>:<gid> /srv/recordings/archive
make deploy
# Check the deployment from inside the container; secrets show as set or unset, never values.
docker compose --env-file docker/deploy.env -f docker/compose.yml exec web recordings doctor
```

- **Set `[server] allowed_hosts`** in `config.toml` to the names you reach the app by, such
  as its host name, Tailscale name and Tailscale IP. Localhost and `base_url`'s host are
  always allowed; the app refuses any other name.
- **The library starts empty.** A stage-1 deployment shows an empty library until stage 2
  imports recordings.

## Configuration and secrets

`config.toml` is read from the current directory, unless `RECORDINGS_CONFIG` names another
file. Docker sets it to `/config/config.toml`.

| Setting | Where | Needed from |
|---|---|---|
| Archive folder, schedules, Spark address, model names | `config.toml` (template: `config.example.toml`) | stage 1 |
| The names the app answers to | `[server] allowed_hosts` in `config.toml`, plus `RECORDINGS_ALLOWED_HOSTS` (comma-separated) | stage 1 |
| Host archive path, config path, bind IP and port, UID/GID | `docker/deploy.env` (template: `docker/deploy.example.env`) | stage 1 |
| `RECORDINGS_PLAUD_TOKEN` | environment, or `RECORDINGS_PLAUD_TOKEN_FILE` (a Docker secret) | stage 2 |
| `RECORDINGS_SPARK_API_KEY` | environment, or `…_FILE` | stage 4 |
| `CLAUDE_CODE_OAUTH_TOKEN` | environment, or `…_FILE` | stage 4 |

Secrets never go in `config.toml`, `deploy.env`, the image or the repo. Stage 1 mounts
the archive read-only, and the image runs as a non-root user.
