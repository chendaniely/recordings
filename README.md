# recordings

A self-hosted library for recordings: Plaud, audio and video files, talks. One central
archive, transcripts and notes from local models (and Claude when asked), and a web UI for
listening, reading and tagging. Design: `docs/superpowers/specs/2026-10-08-recordings-design.md`.

Stage 1 (this release) is the read-only Library over a demo archive.

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

MIT licensed.

## Docker

```bash
make docker                                         # demo mode, http://127.0.0.1:8000
cp config.example.toml config.toml                  # app settings (git-ignored)
cp docker/deploy.example.env docker/deploy.env      # host paths, bind IP, UID/GID (git-ignored)
make deploy
uv run recordings doctor                            # what's set; secrets shown as set/unset only
```

## Configuration and secrets

| Setting | Where | Needed from |
|---|---|---|
| Archive folder, schedules, Spark address, model names | `config.toml` (template: `config.example.toml`) | stage 1 |
| Host archive path, config path, bind IP and port, UID/GID | `docker/deploy.env` (template: `docker/deploy.example.env`) | stage 1 |
| `RECORDINGS_PLAUD_TOKEN` | environment, or `RECORDINGS_PLAUD_TOKEN_FILE` (a Docker secret) | stage 2 |
| `RECORDINGS_SPARK_API_KEY` | environment, or `…_FILE` | stage 4 |
| `CLAUDE_CODE_OAUTH_TOKEN` | environment, or `…_FILE` | stage 4 |

Secrets never go in `config.toml`, `deploy.env`, the image or the repo. Stage 1 mounts
the archive read-only, and the image runs as a non-root user.
