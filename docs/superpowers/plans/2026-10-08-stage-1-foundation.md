# recordings stage 1 (foundation): implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working, read-only `recordings` Library that runs in demo mode. It uses the archive format, its self-documentation and JSON Schemas, a Canned backend, and a demo archive of four public-domain recordings. It has a synchronized transcript, the Notes tab (layout B), and warm light and dark themes. It is packaged in Docker with CI.

**Architecture:** A uv workspace holds two packages. `packages/core` is the `recordings` library and CLI: IDs, pydantic models, the archive reader and writer, self-docs and backends. `packages/ui` is `recordings-ui`: FastAPI serves `/media/<id>` with Range support and mounts a shinyreact `ReactApp`. Its server publishes JSON built by pure view functions. The React client (TypeScript, Vite IIFE build into `www/`) reads that JSON through `window.shinyreact` hooks. Stage 1 never writes the archive from the UI.

**Tech Stack:**
- **Python and backend:** Python 3.14, uv 0.12.23, pydantic 2.13.5, FastAPI 0.142.3, uvicorn 0.54.0, shiny 1.8.0, shinyreact 0.1.0, markdown-it-py 4.2.0
- **Frontend:** Node 22, Vite 5.4.21, TypeScript 5.9.3, React 19.2 (from `window.shinyreact`), Radix Tabs and ToggleGroup, lucide-react
- **Testing:** pytest 9.1.1, pytest-playwright 0.9.0, Vitest 3.2.7 with jsdom 26.1.0
- **Delivery:** Docker, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-10-08-recordings-design.md` (approved 2026-10-08). This plan builds §20 stage 1 only.

## Decisions made while planning (review these)

1. **Format docs live in the core package**, as package data at
   `packages/core/src/recordings/format/`, instead of a top-level `format/`. That way the
   installed package, including inside Docker, can write them into an archive (§6.7).
2. **One `_brand.yml` holds both modes.** Any colour can be `{light: …, dark: …}`, as
   Quarto's brand docs describe; Posit's brand-yml skill dates it to Quarto 1.8. I first
   wrongly planned two files, from a summary of a stale page. `scripts/brand_css.py`
   turns the file into shadcn/ui's CSS variables for `:root` and `.dark`.
3. **The app reads hooks from `window.shinyreact`,** as the `shinyreact-build-app` skill
   prescribes for apps. The npm package `@posit-dev/shinyreact` (0.1.1) is installed only
   for its TypeScript types, through `import type`. React is never bundled.
4. **Build tools follow shinyreact's own examples** (Vite 5.4, `@vitejs/plugin-react` 4.7,
   TypeScript 5.9, Vitest 3.2), not the newest majors, so we use exactly what the Shiny
   team tests. They move when shinyreact's examples move.
5. **Components come from shadcn/ui + Tailwind v4** (Dan, 2026-10-08). The
   `shinyreact-build-app` skill calls it "the default" (SKILL.md line 126), and its
   examples 03 and 04 use it. The shinyreact website docs don't name a library.
   - **Radix base:** it is initialised with `--base radix`. shadcn made Base UI its
     default on 2026-07-02 but says Radix is "still fully supported", and Radix matches
     shinyreact's shadcn examples.
   - **Our CSS:** our own layout CSS stays in `app.css` and uses shadcn's tokens.
6. **No SQLite in stage 1.** The archive is scanned directly. The index arrives with
   tagging and jobs (stages 3 and 4).
7. **Demo transcripts are real Whisper output.** `audio-router`'s one-off transcriber
   (`scripts/transcribe_file.py`, local MLX) produced them, and they were committed as
   canned data, so the transcript sync demo has true timestamps. Demo notes are short and
   hand-written, labelled as Canned.
8. **Config lives in the repo folder, git-ignored, with committed templates** (Dan,
   2026-10-08). This replaces the spec's `~/.config/recordings/config.toml`:
   - `config.toml` (template: `config.example.toml`) holds app settings.
   - `docker/deploy.env` (template: `docker/deploy.example.env`) holds Docker host
     settings.
   - **Secrets** come only from environment variables, or `NAME_FILE` for Docker secrets.
   - **The templates avoid the `.env*` naming,** because Claude's rules forbid reading
     `.env` files, and the templates must stay readable.

## Global Constraints

- **Python runs only through uv:** `uv sync`, `uv run …`. Never pip, never the system or
  Homebrew Python. Python is `3.14` (`.python-version`).
- **Node 22**, from `packages/ui/frontend/.nvmrc` = `22`. Install with `npm ci`. No global
  npm installs.
- **shinyreact is pinned exactly:** `shinyreact==0.1.0` (Python) and `@posit-dev/shinyreact`
  `0.1.1` (npm, types only).
  - **React stays external,** mapped to `window.shinyreact.React` / `.ReactDOM`.
    **Never bundle `react`/`react-dom`.**
- **Check the docs before writing code** against any of these. Never write them from
  memory:
  - **shinyreact:** `/shinyreact-build-app` (after `make skills`), or
    https://posit-dev.github.io/shinyreact/
  - **brand.yml:** https://posit-dev.github.io/brand-yml/, plus
    https://quarto.org/docs/authoring/brand.html for light/dark
  - **shadcn/ui:** the shadcn skill (`npx skills add shadcn/ui`), or https://ui.shadcn.com/docs
  - **Tailwind v4:** https://tailwindcss.com/docs
- **Dark mode is the `dark` class on `<html>`** (shadcn's convention). Choosing light sets
  `light`.
- **Recording ID:** `YYYYMMDDTHHMMSS±HHMM_<8 hex>`, ISO 8601 basic local time with offset,
  plus the first 8 hex characters of the media SHA-256. The folder is
  `recordings/YYYY/MM/<id>/`.
- **Write once:** media, `source/` and `renditions/` files are never overwritten.
  `recording.json` and `my-notes.md` are replaced atomically (temp file plus `os.replace`).
  A new recording is assembled in `<archive>/.tmp/` and renamed into place.
- **Private:** a recording is private if any tag is `private` or starts with `private/`.
- **Colours:**
  - NYC blue is `#236192` in light mode and `#6CA6D9` in dark.
  - NYC orange is `#F26522`, used only as an accent: never as text colour in light mode.
- **Fonts:** Atkinson Hyperlegible and Atkinson Hyperlegible Mono (OFL), **self-hosted**.
  The app makes no external requests at runtime.
- **Stage 1 is read-only.** The UI never writes to the archive.
- **Demo mode** runs on a fresh copy of `demo/archive/` each time it starts. It ignores
  `RECORDINGS_ARCHIVE` and makes no network calls.
- **Secrets:** none in the repo, `config.toml`, `deploy.env` or the image, and gitleaks
  must be clean.
  - They are read only from environment variables `NAME`, or `NAME_FILE`.
  - A secret's value is never printed. `recordings doctor` reports presence only.
  - Real `config.toml` and `docker/deploy.env` are git-ignored. Only their
    `*.example.*` templates are committed.
- **Commits** use Conventional Commits and end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A broken `recording.json`** (hand-edited, truncated) must not take the Library down.
   The other recordings load, and the bad file is reported with its path. The test is in
   Task 4, and the view in Task 10.
2. **A seek request past the end of a media file** (`Range: bytes=<huge>-`) must answer
   **416**, not 500. Range requests must answer **206**. Tested in Task 11.
3. **A recording with no transcript yet** (untagged, not from Plaud) must show an empty
   Transcript tab, not crash. Tested in Task 10.
4. **Model-written notes containing raw HTML or `<script>`** must render as text, never as
   markup. Tested in Task 10.
5. **Non-ASCII titles and text** (`会議メモ 🎙`) must survive a write and read in UTF-8,
   with the file name unaffected. Tested in Task 4.

## Execution checkpoints (Dan, 2026-10-08)

After each task passes its review, and before the next task starts, run a **forward and
reverse review**:

- **Reverse:** compare what is now built against the spec, this plan and the earlier
  tasks. Fixes and rulings made during review count: they can change behaviour that
  earlier tasks or the spec assumed. The question is whether we implemented the right
  thing.
- **Forward:** read the remaining tasks against the code as it actually stands, including
  names, signatures, behaviour and rulings. Amend any task that no longer fits before it
  runs.

Plan amendments are committed as they happen (`docs(plan): …`), so the plan in git always
matches what is being built. Each checkpoint's findings are kept in the execution ledger.

## File map (created in stage 1)

```
recordings/
  pyproject.toml  uv.lock  .python-version  .gitignore  LICENSE  README.md  CLAUDE.md  Makefile
  config.example.toml                        committed template; config.toml is git-ignored
  _brand.yml                                 light + dark colours in one file ({light, dark})
  scripts/brand_css.py                       _brand.yml → packages/ui/frontend/src/theme.css (shadcn tokens)
  demo/
    sources.toml                             the 4 public-domain clips: URL, sha256, trim, metadata
    fetch.py                                 download → verify sha256 → trim/encode into demo/.cache/media
    convert_oneoff.py                        audio-router one-off JSON → canned TranscriptPayload
    build.py                                 cache media + canned → demo/archive (deterministic)
    canned/aliases.json                      recording id → slug (written by build.py)
    canned/transcripts/<slug>.json
    canned/notes/<slug>/<note_type>--<model>.md
    archive/                                 the committed demo archive
    CREDITS.md
  packages/core/
    pyproject.toml
    src/recordings/
      __init__.py  cli.py  ids.py  models.py  archive.py  selfdoc.py  schemas.py  config.py
      backends/__init__.py  backends/base.py  backends/canned.py
      format/README.md  AGENTS.md  FORMAT.md  recordings.README.md  catalog.README.md
      format/schemas/recording.schema.json  rendition.schema.json
    tests/  test_smoke.py  test_ids.py  test_models.py  test_archive.py  test_selfdoc.py
            test_config.py  test_deploy_template.py
            test_cli.py  test_canned.py  test_demo_archive.py  test_brand.py
  packages/ui/
    pyproject.toml
    src/recordings_ui/
      __init__.py  __main__.py  settings.py  runtime.py  views.py  app.py  shiny_app.py
      www/fonts/…                            committed woff2 + OFL
      www/ui.js  www/ui.css                  BUILD OUTPUT (git-ignored)
    frontend/
      .nvmrc  package.json  package-lock.json  vite.config.js  tsconfig.json  scripts/check-node.mjs
      components.json                        shadcn config (written by the shadcn CLI)
      src/ui.tsx  App.tsx  sr.ts  types.ts  index.css  theme.css  app.css
      src/lib/utils.ts  src/components/ui/tabs.tsx  toggle-group.tsx  toggle.tsx   (shadcn CLI)
      src/lib/theme.ts  theme.test.ts  transcript.ts  transcript.test.ts
      src/components/TopBar.tsx  ThemeSwitch.tsx  Sidebar.tsx  RecordingList.tsx  RecordingPane.tsx
                      Player.tsx  TranscriptTab.tsx  NotesTab.tsx  PlaudTab.tsx  MyNotesTab.tsx  DetailsTab.tsx
    tests/  conftest.py  test_smoke.py  test_settings.py  test_views.py  test_app.py  test_shiny_server.py
            e2e/test_library.py
  docker/Dockerfile  docker/compose.yml  docker/compose.demo.yml
  docker/deploy.example.env                  committed template; docker/deploy.env is git-ignored
  .github/workflows/ci.yml  .github/dependabot.yml
```

---

### Task 1: Workspace skeleton and tooling

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `LICENSE`, `README.md`, `CLAUDE.md`, `Makefile`
- Create: `packages/core/pyproject.toml`, `packages/core/src/recordings/__init__.py`, `packages/core/src/recordings/cli.py`, `packages/core/tests/test_smoke.py`
- Create: `packages/ui/pyproject.toml`, `packages/ui/src/recordings_ui/__init__.py`, `packages/ui/tests/test_smoke.py`

**Interfaces:**
- Produces: the `recordings` package with `__version__ = "0.1.0"` and `recordings.cli.main(argv: list[str] | None = None) -> int`. The `recordings` console script. The `recordings_ui` package with `__version__ = "0.1.0"`.

- [ ] **Step 1: Write the root workspace files**

`pyproject.toml`:
```toml
# uv workspace: the core library/CLI and the UI app (spec §4). The root is not a package.
[project]
name = "recordings-workspace"
version = "0.0.0"
requires-python = ">=3.14"
dependencies = ["recordings", "recordings-ui"]

[tool.uv]
package = false

[tool.uv.workspace]
members = ["packages/core", "packages/ui"]

[tool.uv.sources]
recordings = { workspace = true }
recordings-ui = { workspace = true }

# A dependency group, not an extra, so a plain `uv sync` can never silently remove pytest
# (the audio-router lesson).
[dependency-groups]
dev = [
    "pytest==9.1.1",
    "pytest-playwright==0.9.0",
    "httpx==0.28.1",
    "jsonschema==4.26.0",
    "pyyaml==6.0.3",
    "ruff==0.16.10",
]

[tool.pytest.ini_options]
testpaths = ["packages/core/tests", "packages/ui/tests"]
# importlib mode: both packages have a tests/ folder with same-named files.
addopts = "--import-mode=importlib -m 'not e2e'"
markers = ["e2e: browser tests against a running demo server (need the built frontend)"]

[tool.ruff]
line-length = 100
```

`.python-version`:
```
3.14
```

`.gitignore`:
```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
node_modules/
# Vite build output: rebuilt by `make build`; never edit it (shinyreact-build-app skill).
packages/ui/src/recordings_ui/www/ui.js
packages/ui/src/recordings_ui/www/ui.css
# Downloaded originals and encoded clips for rebuilding the demo archive.
demo/.cache/
# Skill symlinks into .venv, recreated by `make skills`.
.claude/skills/shinyreact-*
# shadcn's skill, installed by `make skills` (npx skills add shadcn/ui)
.claude/skills/shadcn/
.agents/
.superpowers/
test-results/
.playwright-mcp/
.DS_Store
```

`LICENSE`:
```
MIT License

Copyright (c) 2026 Daniel Chen

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

`README.md`:
````markdown
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
````

`CLAUDE.md`:
````markdown
# recordings

Read `docs/superpowers/specs/2026-10-08-recordings-design.md` before changing behaviour.

## Toolchain

- **Python only through uv.** `uv sync`, `uv run …`. Never pip, never the system Python.
- **Node 22 from `packages/ui/frontend/.nvmrc`.** Use `nvm use`, then `npm ci`. No global
  installs.
- **`make test`** runs pytest and Vitest. **`make e2e`** runs the Playwright tests against
  demo mode.

## Check the docs, never write from memory

These change quickly. Before writing or changing code that uses any of them, check the
current skill or docs. Say in the commit or PR what you checked. If the docs don't cover
it, say so instead of guessing; that gap is worth reporting upstream.

| Library | Skill (`make skills` installs both) | Docs |
|---|---|---|
| shinyreact | `/shinyreact-build-app` | https://posit-dev.github.io/shinyreact/ |
| shadcn/ui | the shadcn skill | https://ui.shadcn.com/docs |
| brand.yml | Posit's `brand-yml` skill, if installed | https://posit-dev.github.io/brand-yml/, plus https://quarto.org/docs/authoring/brand.html for `{light, dark}` colours |
| Tailwind v4 | none | https://tailwindcss.com/docs |

The rules the code depends on:
- **React stays external,** mapped to `window.shinyreact.React`. Two React copies make
  every hook silently return nothing.
- **`www/ui.js` is build output.** Never edit it. Rebuild with `make build`.
- **`ReactApp(server)` in `shiny_app.py`** finds `www/` next to that file.

## Upgrading shinyreact

The Python `shinyreact` and the npm `@posit-dev/shinyreact` (types only) are pinned
exactly. To upgrade:

1. Read the release notes.
2. Bump both pins.
3. `make skills`.
4. Check that `.nvmrc` still matches shinyreact's `pkg-js/.nvmrc`.
5. Check that its examples' Vite, plugin-react, TypeScript and Vitest versions still match
   ours.
6. `make test e2e`.

## Archive rules (spec §6)

- **Write once:** media, `source/` and `renditions/` are never overwritten. Only
  `recording.json`, `my-notes.md` and `tags.yaml` change, by atomic replace.
- **Privacy:** a recording is private if a tag is `private` or under `private/`. Don't read
  a private recording's transcript or notes.
- **The archive's own docs** come from `packages/core/src/recordings/format/`. Edit them
  there.
````

`Makefile`:
```make
# Each recipe line runs in its own shell, so nvm is loaded per line when it exists.
# In CI (no nvm) setup-node already provides Node 22.
FRONTEND := packages/ui/frontend
NVM := if [ -s "$$HOME/.nvm/nvm.sh" ]; then . "$$HOME/.nvm/nvm.sh" && nvm use --silent "$$(cat $(CURDIR)/$(FRONTEND)/.nvmrc)"; fi;

.PHONY: setup skills build test test-py test-js e2e demo demo-archive schemas brand docker

setup:
	uv sync
	cd $(FRONTEND) && $(NVM) npm ci
	$(MAKE) skills

skills:
	uvx library-skills --claude
	$(NVM) npx skills add shadcn/ui

build:
	cd $(FRONTEND) && $(NVM) npm run build

test: test-py test-js

test-py:
	uv run pytest

test-js:
	cd $(FRONTEND) && $(NVM) npm run typecheck && npm test

e2e: build
	uv run playwright install chromium
	uv run pytest -m e2e

demo: build
	uv run recordings-ui --demo

demo-archive:
	uv run python demo/build.py --media-dir demo/.cache/media --out demo/archive --force

schemas:
	uv run recordings schemas --write

brand:
	uv run scripts/brand_css.py

docker:
	docker compose -f docker/compose.demo.yml up --build
```

- [ ] **Step 2: Write the two package manifests**

`packages/core/pyproject.toml`:
```toml
[project]
name = "recordings"
version = "0.1.0"
description = "Core library and CLI for the recordings archive."
requires-python = ">=3.14"
license = "MIT"
dependencies = ["pydantic==2.13.5"]

[project.scripts]
recordings = "recordings.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/recordings"]
```

`packages/ui/pyproject.toml`:
```toml
[project]
name = "recordings-ui"
version = "0.1.0"
description = "Web interface for the recordings archive (FastAPI + shinyreact)."
requires-python = ">=3.14"
license = "MIT"
dependencies = [
    "recordings",
    "fastapi==0.142.3",
    "uvicorn==0.54.0",
    "shiny==1.8.0",
    # Pinned exactly and upgraded on purpose (CLAUDE.md "Upgrading shinyreact").
    "shinyreact==0.1.0",
    "markdown-it-py==4.2.0",
]

[project.scripts]
recordings-ui = "recordings_ui.__main__:main"

[tool.uv.sources]
recordings = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/recordings_ui"]
```

- [ ] **Step 3: Write the failing smoke tests**

`packages/core/tests/test_smoke.py`:
```python
import pytest

import recordings
from recordings.cli import main


def test_version():
    assert recordings.__version__ == "0.1.0"


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "recordings 0.1.0" in capsys.readouterr().out
```

`packages/ui/tests/test_smoke.py`:
```python
import recordings_ui


def test_version():
    assert recordings_ui.__version__ == "0.1.0"
```

- [ ] **Step 4: Run them to see them fail**

Run: `uv sync && uv run pytest packages/core/tests/test_smoke.py packages/ui/tests/test_smoke.py -v`
Expected: the tests FAIL. The modules don't exist yet, so `uv sync` may succeed or fail
while building the packages. Either way, nothing passes.

- [ ] **Step 5: Write the minimal packages**

`packages/core/src/recordings/__init__.py`:
```python
"""recordings: the core library and CLI for the recordings archive (spec §1)."""

__version__ = "0.1.0"
```

`packages/core/src/recordings/cli.py`:
```python
"""`recordings` command line. Agents use this first (spec §10); every command takes --json."""

from __future__ import annotations

import argparse

from recordings import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recordings", description="Core tools for the recordings archive."
    )
    parser.add_argument("--version", action="version", version=f"recordings {__version__}")
    parser.add_subparsers(dest="command")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
    return 0
```

`packages/ui/src/recordings_ui/__init__.py`:
```python
"""recordings-ui: the FastAPI + shinyreact interface over the recordings core (spec §13)."""

__version__ = "0.1.0"
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv sync && uv run pytest -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore LICENSE README.md CLAUDE.md Makefile packages
git commit -m "build: uv workspace with the recordings core and recordings-ui packages

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Recording IDs

**Files:**
- Create: `packages/core/src/recordings/ids.py`
- Test: `packages/core/tests/test_ids.py`

**Interfaces:**
- Produces:
  - `ID_RE: re.Pattern`
  - `make_id(recorded_at: datetime, sha256_hex: str) -> str`
  - `parse_id(recording_id: str) -> tuple[datetime, str]`
  - `relative_dir(recording_id: str) -> PurePosixPath`

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_ids.py`:
```python
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from recordings.ids import make_id, parse_id, relative_dir

SHA = "3fa91c2e" + "0" * 56


def test_make_id_uses_iso_basic_local_time_with_offset():
    t = datetime(2026, 10, 6, 14, 0, 3, tzinfo=ZoneInfo("America/Vancouver"))
    assert make_id(t, SHA) == "20261006T140003-0700_3fa91c2e"


def test_utc_is_plus_zero_not_z():
    t = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert make_id(t, SHA) == "20260102T030405+0000_3fa91c2e"


def test_parse_round_trips_instant_and_offset():
    t = datetime(2026, 10, 5, 20, 5, 15, tzinfo=timezone(timedelta(hours=9)))
    stamp, short = parse_id(make_id(t, SHA))
    assert stamp == t
    assert stamp.utcoffset() == timedelta(hours=9)
    assert short == "3fa91c2e"


def test_naive_datetime_is_refused():
    with pytest.raises(ValueError, match="timezone-aware"):
        make_id(datetime(2026, 1, 1), SHA)


def test_offset_with_seconds_is_refused():
    # Old tzdata local-mean-time offsets have seconds; an ID carries ±HHMM only.
    t = datetime(1900, 1, 1, tzinfo=timezone(timedelta(hours=-5, seconds=-36)))
    with pytest.raises(ValueError, match="seconds"):
        make_id(t, SHA)


@pytest.mark.parametrize("bad", ["abc", SHA.upper(), SHA[:-1]])
def test_sha_must_be_64_lowercase_hex(bad):
    with pytest.raises(ValueError, match="64 lowercase hex"):
        make_id(datetime(2026, 1, 1, tzinfo=timezone.utc), bad)


@pytest.mark.parametrize(
    "bad",
    [
        "2026-10-06T14:00:03-07:00_3fa91c2e",  # extended format
        "20261006T140003_3fa91c2e",  # no offset
        "20261006T140003-0700_3FA91C2E",  # upper-case hex
        "20261006T140003-0700_3fa91c2e/../x",  # path tricks
    ],
)
def test_parse_rejects_anything_else(bad):
    with pytest.raises(ValueError):
        parse_id(bad)


def test_relative_dir_uses_the_local_date_in_the_id():
    # 23:30 on Dec 31 in Vancouver is already Jan 1 in UTC; the folder follows the ID.
    t = datetime(2025, 12, 31, 23, 30, tzinfo=ZoneInfo("America/Vancouver"))
    rid = make_id(t, SHA)
    assert str(relative_dir(rid)) == f"recordings/2025/12/{rid}"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_ids.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings.ids'`.

- [ ] **Step 3: Implement**

`packages/core/src/recordings/ids.py`:
```python
"""Recording IDs: `20261006T140003-0700_3fa91c2e` (spec §6.2).

ISO 8601 *basic* local time with its UTC offset, then the first 8 hex of the media's
SHA-256. Basic format because the extended form's colons are rejected by SMB/Windows and
shown as "/" by Finder; mixing the two forms would not be valid ISO 8601. The offset stays
because Dan records in more than one time zone. An ID never changes once minted.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath

ID_RE = re.compile(r"(?P<stamp>\d{8}T\d{6}[+-]\d{4})_(?P<short>[0-9a-f]{8})")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def make_id(recorded_at: datetime, sha256_hex: str) -> str:
    offset = recorded_at.utcoffset()
    if offset is None:
        raise ValueError("recorded_at must be timezone-aware")
    if not _SHA256.fullmatch(sha256_hex):
        raise ValueError("sha256_hex must be 64 lowercase hex characters")
    if offset.total_seconds() % 60:
        raise ValueError(f"UTC offset {offset} has seconds; an ID carries ±HHMM only")
    return f"{recorded_at.strftime('%Y%m%dT%H%M%S%z')}_{sha256_hex[:8]}"


def parse_id(recording_id: str) -> tuple[datetime, str]:
    match = ID_RE.fullmatch(recording_id)
    if match is None:
        raise ValueError(f"not a recording id: {recording_id!r}")
    # Python 3.11+ fromisoformat() reads the basic format.
    return datetime.fromisoformat(match["stamp"]), match["short"]


def relative_dir(recording_id: str) -> PurePosixPath:
    """`recordings/YYYY/MM/<id>`, by the local date the ID itself carries."""
    stamp, _ = parse_id(recording_id)
    return PurePosixPath("recordings", f"{stamp:%Y}", f"{stamp:%m}", recording_id)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_ids.py -v`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): recording IDs in ISO 8601 basic format with offset and hash

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Models and JSON Schemas

**Files:**
- Create: `packages/core/src/recordings/models.py`, `packages/core/src/recordings/schemas.py`
- Create: `packages/core/src/recordings/format/schemas/recording.schema.json`, `…/rendition.schema.json` (generated)
- Modify: `packages/core/src/recordings/cli.py` (adds `schemas`)
- Test: `packages/core/tests/test_models.py`

**Interfaces:**
- Consumes: `recordings.ids.ID_RE`.
- Produces:
  - **Models:** `Recording`, `MediaInfo`, `SourceRef`, `TagRef`, `Chosen`, `Rendition`,
    `TranscriptPayload`, `Segment`, `Word`, `NotesPayload`
  - **Helpers:** `is_private(rec) -> bool`, `is_untagged(rec) -> bool`,
    `dump_json(model) -> str`
  - **Constants:** `FORMAT = "recordings-archive@1"`,
    `RENDITION_SCHEMA = "recordings/rendition@1"`, `SCHEMA_REF`
  - **Schemas:** `recordings.schemas.generate() -> dict[str, dict]` (file name → schema) and
    `write(dir: Path) -> list[Path]`
  - **CLI:** `recordings schemas [--write] [--check]`

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_models.py`:
```python
import json
from datetime import datetime, timezone
from importlib import resources

import pytest
from pydantic import ValidationError

from recordings import schemas
from recordings.cli import main
from recordings.models import (
    Recording,
    Rendition,
    TagRef,
    dump_json,
    is_private,
    is_untagged,
)

SHA = "3fa91c2e" + "0" * 56


def recording(**over) -> Recording:
    base = dict(
        id="20261006T140003-0700_3fa91c2e",
        title="COURSE 101: Week 4",
        recorded_at=datetime.fromisoformat("2026-10-06T14:00:03-07:00"),
        timezone="America/Vancouver",
        time_source="plaud",
        media={"file": "20261006T140003-0700_3fa91c2e.mp3", "sha256": SHA, "kind": "audio"},
    )
    base.update(over)
    return Recording.model_validate(base)


def test_round_trip_keeps_dollar_schema_and_offset():
    rec = recording(schema_ref="../../../../schemas/recording.schema.json")
    text = dump_json(rec)
    data = json.loads(text)
    assert data["$schema"] == "../../../../schemas/recording.schema.json"
    assert data["recorded_at"] == "2026-10-06T14:00:03-07:00"
    assert data["format"] == "recordings-archive@1"
    assert Recording.model_validate_json(text) == rec


def test_naive_recorded_at_is_rejected():
    with pytest.raises(ValidationError):
        recording(recorded_at=datetime(2026, 10, 6, 14, 0, 3))


def test_bad_id_is_rejected():
    with pytest.raises(ValidationError, match="not a recording id"):
        recording(id="2026-10-06_3fa91c2e")


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Recording.model_validate({**json.loads(dump_json(recording())), "surprise": 1})


@pytest.mark.parametrize("bad", ["", "/x", "x/", "a//b", " lead", "trail ", "a/ b"])
def test_tag_names_have_no_empty_or_padded_parts(bad):
    with pytest.raises(ValidationError):
        TagRef(tag=bad)


@pytest.mark.parametrize(
    "tags, private",
    [
        ([], False),
        ([{"tag": "private"}], True),
        ([{"tag": "private/journal"}], True),
        ([{"tag": "privateer"}], False),  # a prefix of the word is not the folder
        ([{"tag": "talks"}, {"tag": "private/personal"}], True),
    ],
)
def test_is_private(tags, private):
    assert is_private(recording(tags=tags)) is private


def test_is_untagged_means_no_tags_at_all():
    assert is_untagged(recording())
    assert not is_untagged(recording(tags=[{"tag": "notes/lecture", "by": "auto"}]))


def rendition(**over) -> Rendition:
    base = dict(
        kind="notes",
        note_type="lecture",
        engine="canned",
        model="claude-opus-5-5",
        version="claude-opus-5-5@demo",
        created_at=datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
        payload={"markdown": "# Notes"},
    )
    base.update(over)
    return Rendition.model_validate(base)


def test_rendition_dumps_schema_key():
    assert json.loads(dump_json(rendition()))["schema"] == "recordings/rendition@1"


def test_notes_need_a_note_type_and_markdown():
    with pytest.raises(ValidationError, match="note_type"):
        rendition(note_type=None)
    with pytest.raises(ValidationError):
        rendition(payload={"text": "wrong key"})


def test_transcript_payload_is_validated():
    ok = rendition(
        kind="transcript",
        note_type=None,
        payload={"segments": [{"start": 0, "end": 1.5, "text": "Hi", "words": []}]},
    )
    assert ok.payload["segments"][0]["text"] == "Hi"
    with pytest.raises(ValidationError):
        rendition(kind="transcript", note_type=None, payload={"segments": [{"start": 0}]})


def test_committed_schemas_match_the_models():
    committed = resources.files("recordings") / "format" / "schemas"
    for name, schema in schemas.generate().items():
        on_disk = json.loads((committed / name).read_text(encoding="utf-8"))
        assert on_disk == schema, f"{name} is stale: run `make schemas`"


def test_cli_schemas_check_passes_when_current():
    assert main(["schemas", "--check"]) == 0
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings.models'`.

- [ ] **Step 3: Implement the models**

`packages/core/src/recordings/models.py`:
```python
"""The archive's data model (spec §6.3, §6.5). JSON Schemas are generated from these."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from recordings.ids import ID_RE

FORMAT = "recordings-archive@1"
RENDITION_SCHEMA = "recordings/rendition@1"
# recording.json sits at recordings/YYYY/MM/<id>/, four levels below the archive root.
SCHEMA_REF = "../../../../schemas/recording.schema.json"

TimeSource = Literal["plaud", "metadata", "published", "mtime", "ingest"]


class _Model(BaseModel):
    # forbid: a typo in a hand-edited file is an error, not a silently ignored key (§11).
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class MediaInfo(_Model):
    file: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal["audio", "video"]
    duration_ms: int | None = Field(default=None, ge=0)
    audio_file: str | None = None  # extracted audio track, video only


class SourceRef(_Model):
    kind: str  # "plaud", "url", "upload", "watched_folder", "audio_router"
    ref: str  # opaque, exactly as the source returned it (Plaud now sends "of_…")
    added_at: AwareDatetime
    raw: str | None = None  # e.g. "source/plaud-20261008T143000Z.json"


class TagRef(_Model):
    tag: str
    by: Literal["you", "auto"] = "you"

    @field_validator("tag")
    @classmethod
    def _folders_are_clean(cls, value: str) -> str:
        parts = value.split("/")
        if any(not p or p != p.strip() for p in parts):
            raise ValueError(f"tag {value!r} has an empty or space-padded part")
        return value


class Chosen(_Model):
    transcript: str | None = None


class Recording(_Model):
    schema_ref: str | None = Field(default=None, alias="$schema")
    format: Literal["recordings-archive@1"] = FORMAT
    id: str
    title: str
    recorded_at: AwareDatetime
    timezone: str
    time_source: TimeSource
    media: MediaInfo
    sources: list[SourceRef] = Field(default_factory=list)
    tags: list[TagRef] = Field(default_factory=list)
    excluded_note_types: list[str] = Field(default_factory=list)
    speakers: dict[str, str] = Field(default_factory=dict)
    chosen: Chosen = Field(default_factory=Chosen)

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if not ID_RE.fullmatch(value):
            raise ValueError(f"not a recording id: {value!r}")
        return value


def is_private(rec: Recording) -> bool:
    """Spec §7.4: private iff a tag is `private` or sits under `private/`."""
    return any(t.tag == "private" or t.tag.startswith("private/") for t in rec.tags)


def is_untagged(rec: Recording) -> bool:
    """Spec §7.1: untagged means no tags at all (notes/* tags count as tags)."""
    return not rec.tags


class Word(_Model):
    word: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class Segment(_Model):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    speaker: str | None = None
    text: str
    words: list[Word] = Field(default_factory=list)


class TranscriptPayload(_Model):
    language: str | None = None
    segments: list[Segment]


class NotesPayload(_Model):
    markdown: str


class Rendition(_Model):
    """One output file in renditions/ (extends audio-router/rendition@1, spec §6.5)."""

    schema_: Literal["recordings/rendition@1"] = Field(default=RENDITION_SCHEMA, alias="schema")
    kind: Literal["transcript", "speakers", "pick", "notes"]
    note_type: str | None = None
    engine: str
    model: str | None = None  # the model that actually answered
    version: str  # changes whenever the output would change
    created_at: AwareDatetime
    inputs: dict[str, str] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> Rendition:
        if self.kind == "notes":
            if not self.note_type:
                raise ValueError("a notes rendition needs a note_type")
            NotesPayload.model_validate(self.payload)
        elif self.note_type is not None:
            raise ValueError("only notes renditions carry a note_type")
        if self.kind == "transcript":
            TranscriptPayload.model_validate(self.payload)
        return self


def dump_json(model: BaseModel) -> str:
    """The one serialisation used for every file the archive writes: aliases, no nulls, UTF-8."""
    return model.model_dump_json(by_alias=True, exclude_none=True, indent=2) + "\n"
```

- [ ] **Step 4: Implement schema generation and the CLI command**

`packages/core/src/recordings/schemas.py`:
```python
"""JSON Schemas for the archive files, generated from the models so they cannot drift."""

from __future__ import annotations

import json
from pathlib import Path

from recordings.models import Recording, Rendition


def generate() -> dict[str, dict]:
    return {
        "recording.schema.json": Recording.model_json_schema(by_alias=True),
        "rendition.schema.json": Rendition.model_json_schema(by_alias=True),
    }


def render(schema: dict) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def package_dir() -> Path:
    return Path(__file__).resolve().parent / "format" / "schemas"


def write(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, schema in generate().items():
        path = directory / name
        path.write_text(render(schema), encoding="utf-8")
        written.append(path)
    return written


def stale(directory: Path) -> list[str]:
    out = []
    for name, schema in generate().items():
        path = directory / name
        if not path.is_file() or path.read_text(encoding="utf-8") != render(schema):
            out.append(name)
    return out
```

Replace `packages/core/src/recordings/cli.py` with:
```python
"""`recordings` command line. Agents use this first (spec §10); commands take --json."""

from __future__ import annotations

import argparse
import json
import sys

from recordings import __version__, schemas


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recordings", description="Core tools for the recordings archive."
    )
    parser.add_argument("--version", action="version", version=f"recordings {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("schemas", help="regenerate or check the committed JSON Schemas")
    p.add_argument("--write", action="store_true", help="rewrite the package's schema files")
    p.add_argument("--check", action="store_true", help="exit 1 if they are stale")
    p.add_argument("--json", action="store_true")
    return parser


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")


def cmd_schemas(args: argparse.Namespace) -> int:
    target = schemas.package_dir()
    if args.write:
        written = schemas.write(target)
        _emit({"written": [p.name for p in written]}, args.json)
        return 0
    out_of_date = schemas.stale(target)
    _emit({"stale": out_of_date}, args.json)
    return 1 if (args.check and out_of_date) else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "schemas":
        return cmd_schemas(args)
    parser.print_help(sys.stdout)
    return 0
```

- [ ] **Step 5: Generate the schema files**

Run: `uv run recordings schemas --write`
Expected: `written: ['recording.schema.json', 'rendition.schema.json']`. The two files now
exist in `packages/core/src/recordings/format/schemas/`.

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest packages/core -v`
Expected: all pass (test_models: 22 passed).

- [ ] **Step 7: Commit**

```bash
git add packages/core
git commit -m "feat(core): archive models with generated JSON Schemas

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Reading and writing the archive

**Files:**
- Create: `packages/core/src/recordings/archive.py`
- Test: `packages/core/tests/test_archive.py`

**Interfaces:**
- Consumes: `make_id`, `relative_dir`, `parse_id` (Task 2); `Recording`, `Rendition`,
  `MediaInfo`, `SourceRef`, `TagRef`, `dump_json`, `SCHEMA_REF`, `TimeSource` (Task 3).
- Produces:
  - `sha256_file(path: Path) -> str`
  - `utc_stamp(t: datetime) -> str` → `"20261008T143512Z"`
  - `write_text_atomic(path: Path, text: str) -> None`
  - `RawSource(kind: str, ref: str, added_at: datetime, payload: bytes | None = None)`
  - `Problem(path: Path, message: str)`
  - `Archive(root: Path)`:
    - `.root`
    - `.problems: list[Problem]`
    - `.recording_dirs() -> Iterator[Path]`
    - `.iter_recordings() -> Iterator[Recording]`
    - `.path_for(rid) -> Path`
    - `.load(rid) -> Recording` (`KeyError` if missing)
    - `.media_path(rid) -> Path`
    - `.renditions(rid) -> list[tuple[str, Rendition]]` (path relative to the recording
      folder, sorted by `created_at` then name)
    - `.read_my_notes(rid) -> str | None`
    - `.find_by_sha256(sha) -> Recording | None`
    - `.add_recording(*, media: Path, recorded_at: datetime, timezone_name: str, time_source: TimeSource, title: str, kind: Literal["audio","video"], source: RawSource, duration_ms: int | None = None, tags: Sequence[TagRef] = (), renditions: Sequence[Rendition] = (), my_notes: str | None = None) -> Recording`
    - `.write_rendition(rid, rendition) -> str`

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_archive.py`:
```python
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from recordings.archive import Archive, RawSource, sha256_file, utc_stamp
from recordings.models import Rendition, TagRef

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
LOCAL = datetime.fromisoformat("2026-10-06T14:00:03-07:00")


def media(tmp_path: Path, content: bytes = b"ID3 fake audio", name: str = "clip.MP3") -> Path:
    p = tmp_path / "in" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def add(archive: Archive, path: Path, **over):
    kwargs = dict(
        media=path,
        recorded_at=LOCAL,
        timezone_name="America/Vancouver",
        time_source="plaud",
        title="Week 4",
        kind="audio",
        source=RawSource(kind="plaud", ref="of_" + "a" * 32, added_at=T0, payload=b'{"id": 1}'),
    )
    kwargs.update(over)
    return archive.add_recording(**kwargs)


def notes(created_at=T0, model="qwen3.6-35b-a3b") -> Rendition:
    return Rendition(
        kind="notes",
        note_type="lecture",
        engine="canned",
        model=model,
        version=f"{model}@demo",
        created_at=created_at,
        payload={"markdown": "# Notes"},
    )


def test_add_recording_lays_out_the_folder(tmp_path):
    archive = Archive(tmp_path / "archive")
    src = media(tmp_path)
    rec = add(archive, src)
    sha = sha256_file(src)
    assert rec.id == f"20261006T140003-0700_{sha[:8]}"
    folder = archive.root / "recordings" / "2026" / "10" / rec.id
    assert (folder / f"{rec.id}.mp3").read_bytes() == src.read_bytes()  # suffix lower-cased
    raw = folder / "source" / f"plaud-{utc_stamp(T0)}.json"
    assert raw.read_bytes() == b'{"id": 1}'
    data = json.loads((folder / "recording.json").read_text(encoding="utf-8"))
    assert data["$schema"] == "../../../../schemas/recording.schema.json"
    assert data["sources"][0]["raw"] == f"source/plaud-{utc_stamp(T0)}.json"
    assert not (archive.root / ".tmp").exists()  # assembled in .tmp, moved, cleaned up


def test_same_bytes_again_merge_into_one_recording(tmp_path):
    archive = Archive(tmp_path / "archive")
    first = add(archive, media(tmp_path))
    second = add(
        archive,
        media(tmp_path, name="other.mp3"),
        source=RawSource(kind="upload", ref="other.mp3", added_at=T0),
    )
    assert second.id == first.id
    assert [s.kind for s in archive.load(first.id).sources] == ["plaud", "upload"]
    assert len(list(archive.recording_dirs())) == 1


def test_renditions_are_write_once_and_ordered(tmp_path):
    archive = Archive(tmp_path / "archive")
    rec = add(archive, media(tmp_path))
    a = archive.write_rendition(rec.id, notes(T0.replace(minute=5), model="b-model"))
    b = archive.write_rendition(rec.id, notes(T0, model="a-model"))
    c = archive.write_rendition(rec.id, notes(T0, model="a-model"))  # same name → suffix
    assert c != b and c.endswith("-2.json")
    paths = [p for p, _ in archive.renditions(rec.id)]
    assert set(paths[:2]) == {b, c} and paths[2] == a  # ordered by created_at
    assert a.startswith("renditions/notes-lecture-canned-b-model@demo-")


def test_add_recording_writes_given_renditions_and_my_notes(tmp_path):
    archive = Archive(tmp_path / "archive")
    rec = add(archive, media(tmp_path), renditions=[notes()], my_notes="hello\n",
              tags=[TagRef(tag="talks")])
    assert len(archive.renditions(rec.id)) == 1
    assert archive.read_my_notes(rec.id) == "hello\n"
    assert archive.load(rec.id).tags[0].tag == "talks"


def test_non_ascii_title_round_trips_as_utf8(tmp_path):
    archive = Archive(tmp_path / "archive")
    rec = add(archive, media(tmp_path), title="会議メモ 🎙")
    raw = (archive.path_for(rec.id) / "recording.json").read_bytes()
    assert "会議メモ 🎙".encode() in raw  # stored as UTF-8, not \u escapes
    assert archive.load(rec.id).title == "会議メモ 🎙"
    assert rec.id.isascii()


def test_a_broken_recording_json_is_reported_not_fatal(tmp_path):
    archive = Archive(tmp_path / "archive")
    good = add(archive, media(tmp_path))
    bad = add(archive, media(tmp_path, b"other bytes", name="b.mp3"),
              recorded_at=datetime.fromisoformat("2026-10-07T09:00:00-07:00"))
    (archive.path_for(bad.id) / "recording.json").write_text("{ truncated", encoding="utf-8")
    assert [r.id for r in archive.iter_recordings()] == [good.id]
    assert len(archive.problems) == 1
    assert archive.problems[0].path.name == "recording.json"
    assert bad.id in str(archive.problems[0].path)


def test_load_unknown_id_raises_keyerror(tmp_path):
    with pytest.raises(KeyError):
        Archive(tmp_path).load("20261006T140003-0700_00000000")


def test_media_path_points_at_the_media_file(tmp_path):
    archive = Archive(tmp_path / "archive")
    rec = add(archive, media(tmp_path))
    assert archive.media_path(rec.id).name == f"{rec.id}.mp3"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_archive.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings.archive'`.

- [ ] **Step 3: Implement**

`packages/core/src/recordings/archive.py`:
```python
"""Reading and writing the archive (spec §6). The archive is the source of truth.

Write rules: media, source/ and renditions/ are write-once; recording.json and
my-notes.md are replaced atomically; a new recording is assembled under .tmp/ and
renamed into place, so a reader never sees half a recording.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from recordings.ids import make_id, relative_dir
from recordings.models import (
    SCHEMA_REF,
    MediaInfo,
    Recording,
    Rendition,
    SourceRef,
    TagRef,
    TimeSource,
    dump_json,
)

_CHUNK = 1 << 20
_UNSAFE = re.compile(r"[^A-Za-z0-9.@+_-]+")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def utc_stamp(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _slug(value: str) -> str:
    return _UNSAFE.sub("_", value).strip("_") or "x"


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while (candidate := path.with_name(f"{path.stem}-{n}{path.suffix}")).exists():
        n += 1
    return candidate


@dataclass(frozen=True)
class RawSource:
    kind: str
    ref: str
    added_at: datetime
    payload: bytes | None = None  # written verbatim as source/<kind>-<stamp>.json


@dataclass(frozen=True)
class Problem:
    path: Path
    message: str


@dataclass
class Archive:
    root: Path
    problems: list[Problem] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    # ---- reading -------------------------------------------------------------------
    def recording_dirs(self) -> Iterator[Path]:
        base = self.root / "recordings"
        if not base.is_dir():
            return
        for folder in sorted(base.glob("*/*/*")):
            if (folder / "recording.json").is_file():
                yield folder

    def iter_recordings(self) -> Iterator[Recording]:
        """Every readable recording. A broken file is recorded in .problems, not raised (§11)."""
        self.problems = []
        for folder in self.recording_dirs():
            path = folder / "recording.json"
            try:
                yield Recording.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, UnicodeDecodeError, OSError) as exc:
                self.problems.append(Problem(path=path, message=str(exc).splitlines()[0]))

    def path_for(self, rid: str) -> Path:
        return self.root / relative_dir(rid)

    def load(self, rid: str) -> Recording:
        path = self.path_for(rid) / "recording.json"
        if not path.is_file():
            raise KeyError(rid)
        return Recording.model_validate_json(path.read_text(encoding="utf-8"))

    def media_path(self, rid: str) -> Path:
        return self.path_for(rid) / self.load(rid).media.file

    def renditions(self, rid: str) -> list[tuple[str, Rendition]]:
        folder = self.path_for(rid)
        out = []
        for path in sorted((folder / "renditions").glob("*.json")):
            rendition = Rendition.model_validate_json(path.read_text(encoding="utf-8"))
            out.append((path.relative_to(folder).as_posix(), rendition))
        return sorted(out, key=lambda item: (item[1].created_at, item[0]))

    def read_my_notes(self, rid: str) -> str | None:
        path = self.path_for(rid) / "my-notes.md"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def find_by_sha256(self, sha: str) -> Recording | None:
        return next((r for r in self.iter_recordings() if r.media.sha256 == sha), None)

    # ---- writing -------------------------------------------------------------------
    def add_recording(
        self,
        *,
        media: Path,
        recorded_at: datetime,
        timezone_name: str,
        time_source: TimeSource,
        title: str,
        kind: Literal["audio", "video"],
        source: RawSource,
        duration_ms: int | None = None,
        tags: Sequence[TagRef] = (),
        renditions: Sequence[Rendition] = (),
        my_notes: str | None = None,
    ) -> Recording:
        sha = sha256_file(media)
        existing = self.find_by_sha256(sha)
        if existing is not None:  # same bytes again: one recording, one more source (§6.4)
            return self._merge_source(existing, source)

        rid = make_id(recorded_at, sha)
        final = self.path_for(rid)
        if final.exists():
            raise FileExistsError(f"{final} exists but holds different media")
        tmp = self.root / ".tmp" / uuid.uuid4().hex
        (tmp / "source").mkdir(parents=True)
        (tmp / "renditions").mkdir()
        media_name = f"{rid}{media.suffix.lower()}"
        shutil.copy2(media, tmp / media_name)
        rec = Recording(
            schema_ref=SCHEMA_REF,
            id=rid,
            title=title,
            recorded_at=recorded_at,
            timezone=timezone_name,
            time_source=time_source,
            media=MediaInfo(file=media_name, sha256=sha, kind=kind, duration_ms=duration_ms),
            sources=[self._write_raw(tmp, source)],
            tags=list(tags),
        )
        for rendition in renditions:
            self._write_rendition_into(tmp, rendition)
        if my_notes is not None:
            write_text_atomic(tmp / "my-notes.md", my_notes)
        write_text_atomic(tmp / "recording.json", dump_json(rec))
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, final)
        self._drop_empty_tmp()
        return rec

    def write_rendition(self, rid: str, rendition: Rendition) -> str:
        folder = self.path_for(rid)
        if not (folder / "recording.json").is_file():
            raise KeyError(rid)
        return self._write_rendition_into(folder, rendition)

    # ---- helpers -------------------------------------------------------------------
    def _write_raw(self, folder: Path, source: RawSource) -> SourceRef:
        raw = None
        if source.payload is not None:
            path = _unique(folder / "source" / f"{_slug(source.kind)}-{utc_stamp(source.added_at)}.json")
            path.write_bytes(source.payload)
            raw = path.relative_to(folder).as_posix()
        return SourceRef(kind=source.kind, ref=source.ref, added_at=source.added_at, raw=raw)

    def _write_rendition_into(self, folder: Path, rendition: Rendition) -> str:
        kind = f"notes-{rendition.note_type}" if rendition.kind == "notes" else rendition.kind
        name = "-".join(
            [_slug(kind), _slug(rendition.engine), _slug(rendition.version), utc_stamp(rendition.created_at)]
        )
        path = _unique(folder / "renditions" / f"{name}.json")
        path.parent.mkdir(exist_ok=True)
        # write-once: a fresh temp file, then a rename onto a name nobody holds yet
        write_text_atomic(path, dump_json(rendition))
        return path.relative_to(folder).as_posix()

    def _merge_source(self, rec: Recording, source: RawSource) -> Recording:
        if any(s.kind == source.kind and s.ref == source.ref for s in rec.sources):
            return rec
        folder = self.path_for(rec.id)
        updated = rec.model_copy(update={"sources": [*rec.sources, self._write_raw(folder, source)]})
        write_text_atomic(folder / "recording.json", dump_json(updated))
        return updated

    def _drop_empty_tmp(self) -> None:
        try:
            (self.root / ".tmp").rmdir()
        except OSError:
            pass  # not empty (another assembly in flight) or already gone
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_archive.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): archive reader and write-once writer with atomic assembly

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Archive self-documentation, `docs`, and `validate`

**Files:**
- Create: `packages/core/src/recordings/selfdoc.py`
- Create: `packages/core/src/recordings/format/README.md`, `AGENTS.md`, `FORMAT.md`, `recordings.README.md`, `catalog.README.md`
- Modify: `packages/core/src/recordings/cli.py` (adds `docs` and `validate`)
- Test: `packages/core/tests/test_selfdoc.py`, `packages/core/tests/test_cli.py`

**Interfaces:**
- Consumes: `Archive`, `write_text_atomic` (Task 4); `recordings.schemas` (Task 3).
- Produces:
  - `recordings.selfdoc.DOC_FILES: dict[str, str]` (archive path → package-data path)
  - `write_docs(root: Path) -> list[str]` (archive-relative paths it changed)
  - `layout_patterns() -> list[re.Pattern]`, parsed from `FORMAT.md`
  - `validate(root: Path) -> list[dict]`, problems as `{"path", "message"}`
  - **CLI:** `recordings docs <archive> [--json]` and `recordings validate <archive> [--json]`.
    `validate` exits 1 when there are problems.

- [ ] **Step 1: Write the format documents**

`packages/core/src/recordings/format/FORMAT.md`:
````markdown
# The recordings archive format (`recordings-archive@1`)

One folder per recording. The folder and its media file are both named by the recording
ID: ISO 8601 basic local time with its UTC offset, then the first 8 hex of the media's
SHA-256, e.g. `20261006T140003-0700_3fa91c2e`. IDs never change.

```
<archive>/
  README.md  AGENTS.md  FORMAT.md
  schemas/recording.schema.json  schemas/rendition.schema.json
  catalog/                       flat tables, rebuilt by the app (from stage 3)
  recordings/YYYY/MM/<id>/       one folder per recording (below)
  trash/                         deleted recording folders, moved here whole
```

## Inside a recording folder

The files a recording folder may contain. `tests/test_selfdoc.py` fails if the writer ever
produces a file that is not listed here.

<!-- layout:begin -->
recording.json
<id>.<ext>
<id>.audio.<ext>
my-notes.md
source/<name>-<utc-stamp>.json
renditions/<name>-<utc-stamp>.json
<!-- layout:end -->

- `recording.json`: identity and your edits. Its schema is `schemas/recording.schema.json`.
- `<id>.<ext>`: the original media, never modified.
- `<id>.audio.<ext>`: the audio track extracted from a video.
- `my-notes.md`: your own notes.
- `source/`: raw payloads exactly as each source returned them. Write-once.
- `renditions/`: outputs (transcripts, speakers, note-type picks, notes). Write-once.
  Re-running a step adds a file. The name is `<kind>-<engine>-<version>-<UTC stamp>.json`,
  where `<kind>` is `notes-<note type>` for notes. The schema is
  `schemas/rendition.schema.json`.

## Rules

- **Only three files are edited:** `recording.json`, `my-notes.md` and `tags.yaml`. Each
  save writes a temporary file and renames it into place.
- **A recording is private** if `recording.json` has a tag `private` or any tag under
  `private/`. A private recording's content never goes to a cloud model, including agents.
- **Stamps** are ISO 8601 basic UTC, for example `20261008T143512Z`.
````

`packages/core/src/recordings/format/README.md`:
````markdown
# recordings archive

This folder is a `recordings` archive: every recording, transcript and set of notes, as
plain files. The app writes it, and anything may read it: notebooks, Pixeltable, agents,
scripts.

- **What's where:** `FORMAT.md`.
- **Rules for agents:** `AGENTS.md`.
- **One recording:** `recordings/YYYY/MM/<id>/`, where the ID starts with the local date
  and time.
  - **Its transcript:** the newest `renditions/transcript-*.json`, unless `recording.json`
    names one in `chosen.transcript`.
  - **Its notes:** `renditions/notes-<note type>-*.json`, one per note type and model.
- **Checking the archive:** `recordings validate <archive>`.

These docs are written by the app from its own package, so they match the version that
wrote the files.
````

`packages/core/src/recordings/format/AGENTS.md`:
````markdown
# Rules for agents working in this archive

1. **Read `README.md` and `FORMAT.md` first.**
2. **Privacy.** If a recording's `recording.json` has a `private` or `private/…` tag,
   don't read its transcript or notes. The metadata file is the only source of truth for
   this, so there is no list to consult.
3. **Only edit `recording.json`, `my-notes.md` and `tags.yaml`.** Never edit media,
   `source/` or `renditions/`. They are write-once.
4. **Check edits** against `schemas/` (`recordings validate <archive>`).
5. **After a bulk edit, run `recordings reindex`** (available from stage 3). Jobs that the
   edit would start wait for approval in the app.
6. **Prefer the `recordings` CLI with `--json`** to editing files by hand.
````

`packages/core/src/recordings/format/recordings.README.md`:
````markdown
# recordings/

One folder per recording, by local date: `YYYY/MM/<id>/`. See `../FORMAT.md` for what each
folder holds, and `../AGENTS.md` before reading or editing anything.
````

`packages/core/src/recordings/format/catalog.README.md`:
````markdown
# catalog/

Flat tables rebuilt by the app after every change (from stage 3). Load them with pandas,
DuckDB or Pixeltable. Don't edit them, because they are regenerated. See `../FORMAT.md`.
````

- [ ] **Step 2: Write the failing tests**

`packages/core/tests/test_selfdoc.py`:
```python
from datetime import datetime, timezone

from recordings.archive import Archive, RawSource
from recordings.models import Rendition
from recordings.selfdoc import layout_patterns, validate, write_docs

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def build_one(tmp_path) -> Archive:
    media = tmp_path / "clip.mp3"
    media.write_bytes(b"ID3 fake")
    archive = Archive(tmp_path / "archive")
    rec = archive.add_recording(
        media=media,
        recorded_at=datetime.fromisoformat("2026-10-06T14:00:03-07:00"),
        timezone_name="America/Vancouver",
        time_source="plaud",
        title="Week 4",
        kind="audio",
        source=RawSource(kind="plaud", ref="of_x", added_at=T0, payload=b"{}"),
        my_notes="mine\n",
        renditions=[
            Rendition(kind="transcript", engine="canned", model="whisper-large-v3-turbo",
                      version="large-v3-turbo@a4aaeec", created_at=T0,
                      payload={"segments": [{"start": 0, "end": 1, "text": "hi"}]}),
            Rendition(kind="notes", note_type="lecture", engine="canned", model="m",
                      version="m@demo", created_at=T0, payload={"markdown": "x"}),
        ],
    )
    write_docs(archive.root)
    return archive, rec


def test_write_docs_installs_every_doc_and_schema(tmp_path):
    archive, _ = build_one(tmp_path)
    for rel in ["README.md", "AGENTS.md", "FORMAT.md", "recordings/README.md",
                "catalog/README.md", "schemas/recording.schema.json",
                "schemas/rendition.schema.json"]:
        assert (archive.root / rel).is_file(), rel


def test_write_docs_is_idempotent(tmp_path):
    archive, _ = build_one(tmp_path)
    assert write_docs(archive.root) == []


def test_agents_md_states_the_privacy_rule():
    from importlib import resources
    text = (resources.files("recordings") / "format" / "AGENTS.md").read_text(encoding="utf-8")
    assert "`private` or `private/…` tag" in text


def test_every_file_the_writer_produces_is_documented(tmp_path):
    archive, rec = build_one(tmp_path)
    patterns = layout_patterns()
    folder = archive.path_for(rec.id)
    files = [p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()]
    assert files, "the writer produced nothing"
    for rel in files:
        assert any(p.fullmatch(rel.replace(rec.id, "<id>")) for p in patterns), (
            f"{rel} is not described in FORMAT.md's layout block"
        )


def test_validate_reports_bad_files_with_their_path(tmp_path):
    archive, rec = build_one(tmp_path)
    assert validate(archive.root) == []
    bad = archive.path_for(rec.id) / "recording.json"
    bad.write_text('{"id": "nope"}', encoding="utf-8")
    problems = validate(archive.root)
    assert len(problems) == 1
    assert problems[0]["path"].endswith("recording.json")
```

`packages/core/tests/test_cli.py`:
```python
import json

from recordings.cli import main


def test_docs_then_validate_on_an_empty_archive(tmp_path, capsys):
    assert main(["docs", str(tmp_path), "--json"]) == 0
    written = json.loads(capsys.readouterr().out)["written"]
    assert "FORMAT.md" in written
    assert main(["validate", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"problems": []}


def test_validate_exits_1_on_problems(tmp_path, capsys):
    folder = tmp_path / "recordings" / "2026" / "10" / "20261006T140003-0700_3fa91c2e"
    folder.mkdir(parents=True)
    (folder / "recording.json").write_text("{", encoding="utf-8")
    assert main(["validate", str(tmp_path), "--json"]) == 1
    assert len(json.loads(capsys.readouterr().out)["problems"]) == 1
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_selfdoc.py packages/core/tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings.selfdoc'`.

- [ ] **Step 4: Implement `selfdoc.py`**

`packages/core/src/recordings/selfdoc.py`:
```python
"""The archive documents itself (spec §6.7): docs and schemas come from this package."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from pydantic import ValidationError

from recordings.archive import Archive, write_text_atomic
from recordings.models import Rendition

DOC_FILES = {
    "README.md": "README.md",
    "AGENTS.md": "AGENTS.md",
    "FORMAT.md": "FORMAT.md",
    "recordings/README.md": "recordings.README.md",
    "catalog/README.md": "catalog.README.md",
    "schemas/recording.schema.json": "schemas/recording.schema.json",
    "schemas/rendition.schema.json": "schemas/rendition.schema.json",
}

_TOKENS = {
    "<id>": r"<id>",  # the caller substitutes the real ID with this literal first
    "<ext>": r"[a-z0-9]+",
    "<name>": r"[A-Za-z0-9.@+_-]+",
    "<utc-stamp>": r"\d{8}T\d{6}Z(?:-\d+)?",
}


def _package_text(rel: str) -> str:
    return (resources.files("recordings") / "format" / rel).read_text(encoding="utf-8")


def write_docs(root: Path) -> list[str]:
    changed = []
    for dest, src in DOC_FILES.items():
        target = Path(root) / dest
        text = _package_text(src)
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(target, text)
        changed.append(dest)
    return changed


def layout_patterns() -> list[re.Pattern]:
    block = re.search(r"<!-- layout:begin -->\n(.*?)<!-- layout:end -->",
                      _package_text("FORMAT.md"), re.S)
    if block is None:
        raise RuntimeError("FORMAT.md has no layout block")
    patterns = []
    for line in block.group(1).split():
        rx = re.escape(line)
        for token, sub in _TOKENS.items():
            rx = rx.replace(re.escape(token), sub)
        patterns.append(re.compile(rx))
    return patterns


def validate(root: Path) -> list[dict]:
    archive = Archive(Path(root))
    problems = []
    recordings = list(archive.iter_recordings())
    problems += [{"path": str(p.path), "message": p.message} for p in archive.problems]
    for rec in recordings:
        for path in sorted((archive.path_for(rec.id) / "renditions").glob("*.json")):
            try:
                Rendition.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, UnicodeDecodeError) as exc:
                problems.append({"path": str(path), "message": str(exc).splitlines()[0]})
    return problems
```

- [ ] **Step 5: Add `docs` and `validate` to the CLI**

In `packages/core/src/recordings/cli.py`, change the import line to:
```python
from pathlib import Path

from recordings import __version__, schemas, selfdoc
```
In `build_parser()`, add these lines before `return parser`:
```python
    p = sub.add_parser("docs", help="write README/AGENTS/FORMAT and schemas into an archive")
    p.add_argument("archive", type=Path)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("validate", help="check every recording.json and rendition")
    p.add_argument("archive", type=Path)
    p.add_argument("--json", action="store_true")
```
Add these functions above `main`:
```python
def cmd_docs(args: argparse.Namespace) -> int:
    _emit({"written": selfdoc.write_docs(args.archive)}, args.json)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    problems = selfdoc.validate(args.archive)
    _emit({"problems": problems}, args.json)
    return 1 if problems else 0
```
In `main`, replace the dispatch with:
```python
    commands = {"schemas": cmd_schemas, "docs": cmd_docs, "validate": cmd_validate}
    if args.command in commands:
        return commands[args.command](args)
    parser.print_help(sys.stdout)
    return 0
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest packages/core -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add packages/core
git commit -m "feat(core): archive self-documentation, layout guard, docs and validate commands

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Backend interface and the Canned backend

**Files:**
- Create: `packages/core/src/recordings/backends/__init__.py`, `base.py`, `canned.py`
- Test: `packages/core/tests/test_canned.py`

**Interfaces:**
- Consumes: `TranscriptPayload` (Task 3).
- Produces:
  - `CompletionRequest(prompt: str, model: str, recording_id: str, note_type: str)`
  - `Completion(text: str, model: str, backend: str)`
  - `BackendError(RuntimeError)`
  - `Backend` (Protocol):
    - `name: str`
    - `transcribe(audio: Path, *, recording_id: str, vocabulary: Sequence[str] = ()) -> TranscriptPayload`
    - `complete(request: CompletionRequest) -> Completion`
  - `CannedBackend(root: Path, aliases: Mapping[str, str] | None = None)`
  - `CannedBackend.from_dir(root) -> CannedBackend`, which reads `root/aliases.json` if
    present
  - `model_slug(model: str) -> str`
  - **Canned layout:** `transcripts/<key>.json` and
    `notes/<key>/<note_type>--<model_slug>.md`, where `key = aliases.get(recording_id,
    recording_id)`.

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_canned.py`:
```python
import json
from pathlib import Path

import pytest

from recordings.backends import Backend, BackendError, CannedBackend, CompletionRequest, model_slug

RID = "20261006T140003-0700_3fa91c2e"


def canned(tmp_path: Path) -> Path:
    root = tmp_path / "canned"
    (root / "transcripts").mkdir(parents=True)
    (root / "transcripts" / "jfk.json").write_text(json.dumps(
        {"language": "en", "segments": [{"start": 0, "end": 2, "text": "We choose"}]}))
    (root / "notes" / "jfk").mkdir(parents=True)
    (root / "notes" / "jfk" / "conference-talk--claude-opus-5-5.md").write_text("# Notes\n")
    (root / "aliases.json").write_text(json.dumps({RID: "jfk"}))
    return root


def test_it_satisfies_the_backend_protocol(tmp_path):
    backend: Backend = CannedBackend.from_dir(canned(tmp_path))
    assert backend.name == "canned"


def test_transcribe_returns_the_stored_transcript_by_alias(tmp_path):
    backend = CannedBackend.from_dir(canned(tmp_path))
    payload = backend.transcribe(Path("unused.mp3"), recording_id=RID)
    assert payload.segments[0].text == "We choose"


def test_complete_returns_stored_notes_and_the_model_asked(tmp_path):
    backend = CannedBackend.from_dir(canned(tmp_path))
    req = CompletionRequest(prompt="ignored", model="claude-opus-5-5", recording_id=RID,
                            note_type="conference-talk")
    out = backend.complete(req)
    assert (out.text, out.model, out.backend) == ("# Notes\n", "claude-opus-5-5", "canned")


def test_missing_canned_output_is_a_clear_error(tmp_path):
    backend = CannedBackend.from_dir(canned(tmp_path))
    with pytest.raises(BackendError, match="no canned notes"):
        backend.complete(CompletionRequest("p", "qwen3.6-35b-a3b", RID, "conference-talk"))
    with pytest.raises(BackendError, match="no canned transcript"):
        backend.transcribe(Path("x"), recording_id="20200101T000000+0000_00000000")


def test_model_slug_is_filename_safe():
    assert model_slug("mlx-community/whisper large") == "mlx-community_whisper_large"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_canned.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings.backends'`.

- [ ] **Step 3: Implement**

`packages/core/src/recordings/backends/base.py`:
```python
"""One interface for every model backend: Spark, Claude, Canned (spec §8.4)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from recordings.models import TranscriptPayload


@dataclass(frozen=True)
class CompletionRequest:
    prompt: str
    model: str
    recording_id: str
    note_type: str


@dataclass(frozen=True)
class Completion:
    text: str
    model: str  # the model that actually answered, which may differ from the one asked
    backend: str


class BackendError(RuntimeError):
    """A backend could not produce an output. Always loud, never a silent truncation."""


@runtime_checkable
class Backend(Protocol):
    name: str

    def transcribe(
        self, audio: Path, *, recording_id: str, vocabulary: Sequence[str] = ()
    ) -> TranscriptPayload: ...

    def complete(self, request: CompletionRequest) -> Completion: ...


def model_slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9.@+_-]+", "_", model).strip("_")
```

`packages/core/src/recordings/backends/canned.py`:
```python
"""Stored outputs, keyed by recording, note type and model: the same answer every time, with
no network. Demo mode and tests use it (spec §8.4, §17)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from recordings.backends.base import BackendError, Completion, CompletionRequest, model_slug
from recordings.models import TranscriptPayload


class CannedBackend:
    name = "canned"

    def __init__(self, root: Path, aliases: Mapping[str, str] | None = None) -> None:
        self.root = Path(root)
        # Demo outputs are filed by slug ("jfk-rice"); aliases map a recording ID to it.
        self.aliases = dict(aliases or {})

    @classmethod
    def from_dir(cls, root: Path) -> CannedBackend:
        path = Path(root) / "aliases.json"
        aliases = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        return cls(root, aliases)

    def _key(self, recording_id: str) -> str:
        return self.aliases.get(recording_id, recording_id)

    def transcribe(
        self, audio: Path, *, recording_id: str, vocabulary: Sequence[str] = ()
    ) -> TranscriptPayload:
        path = self.root / "transcripts" / f"{self._key(recording_id)}.json"
        if not path.is_file():
            raise BackendError(f"no canned transcript for {recording_id} ({path})")
        return TranscriptPayload.model_validate_json(path.read_text(encoding="utf-8"))

    def complete(self, request: CompletionRequest) -> Completion:
        key = self._key(request.recording_id)
        path = self.root / "notes" / key / f"{request.note_type}--{model_slug(request.model)}.md"
        if not path.is_file():
            raise BackendError(f"no canned notes for {request.recording_id} ({path})")
        return Completion(text=path.read_text(encoding="utf-8"), model=request.model, backend=self.name)
```

`packages/core/src/recordings/backends/__init__.py`:
```python
from recordings.backends.base import (
    Backend,
    BackendError,
    Completion,
    CompletionRequest,
    model_slug,
)
from recordings.backends.canned import CannedBackend

__all__ = ["Backend", "BackendError", "CannedBackend", "Completion", "CompletionRequest", "model_slug"]
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/core/tests/test_canned.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): backend interface and the Canned backend

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Demo media, canned transcripts and canned notes

This task makes data, not code paths. Its tests come in Task 8, which builds the archive
from it.

**Files:**
- Create: `demo/sources.toml`, `demo/fetch.py`, `demo/convert_oneoff.py`, `demo/CREDITS.md`
- Create: `demo/canned/transcripts/{jfk-rice,apollo13-problem,apollo11-first-steps,fdr-fireside-1}.json` (generated)
- Create: `demo/canned/notes/jfk-rice/conference-talk--qwen3.6-35b-a3b.md`, `demo/canned/notes/jfk-rice/conference-talk--claude-opus-5-5.md`, `demo/canned/notes/apollo11-first-steps/general-fallback--qwen3.6-35b-a3b.md`, `demo/canned/notes/fdr-fireside-1/general-fallback--qwen3.6-35b-a3b.md`, `demo/canned/notes/apollo13-problem/plaud.md`, `demo/canned/my-notes/jfk-rice.md`

**Interfaces:**
- Consumes: `TranscriptPayload`, `Segment`, `Word` (Task 3).
- Produces:
  - `demo/.cache/media/<slug>.<ext>`, git-ignored, used by Task 8
  - `demo/canned/…`, committed, read by `CannedBackend` and `demo/build.py`
  - `demo/sources.toml` entries with the fields: `slug`, `title`, `url`, `page`, `sha256`,
    `license`, `kind`, `ext`, `start`, `duration`, `duration_ms`, `speakers`,
    `recorded_at`, `timezone`, `time_source`, `source_kind`, `source_ref`, `tags`,
    `notes`, `plaud`

- [ ] **Step 1: Write `demo/sources.toml`**

These are the originals' SHA-256 hashes as downloaded on 2026-10-08.

```toml
# Public-domain recordings from Wikimedia Commons, trimmed and re-encoded for demo mode
# (spec §17). `recorded_at` is local wall time in `timezone`; zoneinfo supplies the offset.

[[recording]]
slug = "jfk-rice"
title = "JFK at Rice University: We choose to go to the Moon (opening)"
url = "https://upload.wikimedia.org/wikipedia/commons/5/50/Jfk_rice_university_we_choose_to_go_to_the_moon.ogg"
page = "https://commons.wikimedia.org/wiki/File:Jfk_rice_university_we_choose_to_go_to_the_moon.ogg"
sha256 = "7486fba805c20bde4d43750bd9c34aee86ab7acfd3854a1d0f3d64f62ae68b27"
license = "Public domain (John F. Kennedy Presidential Library & Museum)"
kind = "audio"
ext = "mp3"
start = 0
duration = 90
duration_ms = 90009
speakers = 1
recorded_at = "1962-09-12T10:00:00"
timezone = "America/Chicago"
time_source = "published"
source_kind = "url"
source_ref = "https://commons.wikimedia.org/wiki/File:Jfk_rice_university_we_choose_to_go_to_the_moon.ogg"
tags = [{ tag = "talks", by = "you" }, { tag = "notes/conference-talk", by = "auto" }]
notes = [
  { note_type = "conference-talk", model = "qwen3.6-35b-a3b" },
  { note_type = "conference-talk", model = "claude-opus-5-5" },
]

[[recording]]
slug = "apollo13-problem"
title = "Apollo 13: Houston, we've had a problem"
url = "https://upload.wikimedia.org/wikipedia/commons/1/12/Apollo13-wehaveaproblem_edit_1.ogg"
page = "https://commons.wikimedia.org/wiki/File:Apollo13-wehaveaproblem_edit_1.ogg"
sha256 = "0177b4a26bc7efa792beced4d82375669a8080be3412fcb3996b9b4c84ce55b9"
license = "Public domain (NASA)"
kind = "audio"
ext = "mp3"
start = 0
duration = 0            # 0 = whole file
duration_ms = 16550
speakers = 2
recorded_at = "1970-04-13T21:07:56"
timezone = "America/Chicago"
time_source = "published"
source_kind = "plaud"   # shows the Plaud tab; the payload is written for the demo
source_ref = "demo-apollo13"
tags = []               # the untagged demo recording
notes = []
plaud = true

[[recording]]
slug = "apollo11-first-steps"
title = "Apollo 11: first steps on the Moon"
url = "https://upload.wikimedia.org/wikipedia/commons/a/a6/Apollo_11_Landing_-_first_steps_on_the_moon.ogv"
page = "https://commons.wikimedia.org/wiki/File:Apollo_11_Landing_-_first_steps_on_the_moon.ogv"
sha256 = "92712e9fddf6c30a6df8828ab1c30ee91d90a7503feb0198f8c9ab15320682cd"
license = "Public domain (NASA)"
kind = "video"
ext = "mp4"
start = 0
duration = 0
duration_ms = 61518
speakers = 2
recorded_at = "1969-07-20T21:56:15"
timezone = "America/Chicago"
time_source = "published"
source_kind = "url"
source_ref = "https://commons.wikimedia.org/wiki/File:Apollo_11_Landing_-_first_steps_on_the_moon.ogv"
tags = [{ tag = "space/apollo", by = "you" }, { tag = "notes/general-fallback", by = "auto" }]
notes = [{ note_type = "general-fallback", model = "qwen3.6-35b-a3b" }]

[[recording]]
slug = "fdr-fireside-1"
title = "Fireside Chat 1: On the Banking Crisis (opening)"
url = "https://upload.wikimedia.org/wikipedia/commons/9/9b/Fireside_Chat_1_On_the_Banking_Crisis_%28March_12%2C_1933%29_Franklin_Delano_Roosevelt.ogg"
page = "https://commons.wikimedia.org/wiki/File:Fireside_Chat_1_On_the_Banking_Crisis_(March_12,_1933)_Franklin_Delano_Roosevelt.ogg"
sha256 = "6e4eaa46910ca6b72c8cbc35415d785d5ffe0f6964ad3f5eaadff9453e9ebefc"
license = "Public domain (Franklin D. Roosevelt Presidential Library)"
kind = "audio"
ext = "mp3"
start = 0
duration = 60
duration_ms = 60000
speakers = 1
recorded_at = "1933-03-12T22:00:00"
timezone = "America/New_York"
time_source = "published"
source_kind = "url"
source_ref = "https://commons.wikimedia.org/wiki/File:Fireside_Chat_1_On_the_Banking_Crisis_(March_12,_1933)_Franklin_Delano_Roosevelt.ogg"
tags = [{ tag = "private", by = "you" }]   # the private demo recording: Spark only
notes = [{ note_type = "general-fallback", model = "qwen3.6-35b-a3b" }]
```

- [ ] **Step 2: Write `demo/fetch.py`**

```python
"""Download the demo originals, check their SHA-256, and trim/encode them into
demo/.cache/media/<slug>.<ext>. Needs network and ffmpeg; run only to refresh the demo.

    uv run python demo/fetch.py
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
# Wikimedia asks for an identifying User-Agent on downloads.
UA = "recordings-demo-fetch/0.1 (https://github.com/chendaniely/recordings)"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as out:
        out.write(resp.read())


def encode(src: Path, entry: dict, dest: Path) -> None:
    trim = ["-ss", str(entry["start"])] + (["-t", str(entry["duration"])] if entry["duration"] else [])
    if entry["kind"] == "audio":
        codec = ["-ac", "1", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "64k"]
    else:
        codec = ["-vf", "scale=-2:360", "-c:v", "libx264", "-preset", "slow", "-crf", "30",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-ac", "1",
                 "-movflags", "+faststart"]
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), *trim,
                    *codec, str(dest)], check=True)


def main() -> int:
    entries = tomllib.loads((HERE / "sources.toml").read_text())["recording"]
    (CACHE / "originals").mkdir(parents=True, exist_ok=True)
    (CACHE / "media").mkdir(parents=True, exist_ok=True)
    for entry in entries:
        original = CACHE / "originals" / f"{entry['slug']}{Path(entry['url']).suffix}"
        if not original.is_file() or sha256(original) != entry["sha256"]:
            print(f"downloading {entry['slug']}", file=sys.stderr)
            download(entry["url"], original)
        if sha256(original) != entry["sha256"]:
            print(f"{entry['slug']}: checksum mismatch, refusing to continue", file=sys.stderr)
            return 1
        dest = CACHE / "media" / f"{entry['slug']}.{entry['ext']}"
        encode(original, entry, dest)
        print(f"wrote {dest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Fetch and encode**

Run: `uv run python demo/fetch.py && ls -la demo/.cache/media`
Expected: four files: `jfk-rice.mp3` (about 720 KB), `apollo13-problem.mp3` (about
130 KB), `apollo11-first-steps.mp4` (about 1.5 MB) and `fdr-fireside-1.mp3` (about
480 KB). A harmless ffmpeg "non monotonically increasing dts" warning on `jfk-rice` is
expected.

- [ ] **Step 4: Transcribe each clip with audio-router's one-off transcriber**

This runs local MLX Whisper and pyannote and never touches `audio-router`'s archive
(`scripts/transcribe_file.py` writes next to `--out` only). It was verified on 2026-10-08:
16.5 s of audio took 31 s.

Run:
```bash
cd ../audio-router
for s in jfk-rice:1 apollo13-problem:2 apollo11-first-steps:2 fdr-fireside-1:1; do
  mkdir -p ../recordings/demo/.cache/oneoff
  slug=${s%%:*}; n=${s##*:}; f=$(ls ../recordings/demo/.cache/media/$slug.*)
  uv run scripts/transcribe_file.py "$f" --json --speakers "$n" --out ../recordings/demo/.cache/oneoff
done
cd ../recordings && ls demo/.cache/oneoff/*.json
```
Expected: four `<slug>.json` files. If pyannote fails, for example because the gated model
was never fetched on this machine, re-run that clip with `--no-diarize`. Speakers then
come out unlabelled, which the app handles.

- [ ] **Step 5: Write `demo/convert_oneoff.py` and convert**

```python
"""Turn audio-router one-off transcriber JSON into canned TranscriptPayload files.

Segments are Whisper's own (sentence-sized, good for highlighting); each gets the
diarization speaker it overlaps most and the words whose midpoint falls inside it.

    uv run python demo/convert_oneoff.py
"""

from __future__ import annotations

import json
from pathlib import Path

from recordings.models import Segment, TranscriptPayload, Word

HERE = Path(__file__).resolve().parent


def speaker_for(start: float, end: float, turns: list[dict]) -> str | None:
    best, best_overlap = None, 0.0
    for t in turns:
        overlap = min(end, t["end"]) - max(start, t["start"])
        if overlap > best_overlap:
            best, best_overlap = t["speaker"], overlap
    return best


def convert(data: dict) -> TranscriptPayload:
    words = [w for w in data["words"] if w.get("start") is not None and w.get("end") is not None]
    segments = []
    for seg in data["segments"]:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        inside = [w for w in words if seg["start"] <= (w["start"] + w["end"]) / 2 < seg["end"]]
        segments.append(Segment(
            start=round(seg["start"], 3),
            end=round(seg["end"], 3),
            speaker=speaker_for(seg["start"], seg["end"], data.get("turns") or []),
            text=text,
            words=[Word(word=w["word"].strip(), start=round(w["start"], 3), end=round(w["end"], 3))
                   for w in inside],
        ))
    return TranscriptPayload(language=data.get("language"), segments=segments)


def main() -> int:
    out = HERE / "canned" / "transcripts"
    out.mkdir(parents=True, exist_ok=True)
    for path in sorted((HERE / ".cache" / "oneoff").glob("*.json")):
        payload = convert(json.loads(path.read_text(encoding="utf-8")))
        (out / path.name).write_text(
            payload.model_dump_json(exclude_none=True, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out / path.name}: {len(payload.segments)} segments")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run: `uv run python demo/convert_oneoff.py`
Expected: four lines, `wrote …/demo/canned/transcripts/<slug>.json: N segments`, with
N ≥ 1 for each.

- [ ] **Step 6: Write the canned notes**

These are demo content, labelled Canned in the app. After writing them, read each
transcript in `demo/canned/transcripts/` and delete any bullet the clip doesn't support.
They summarize only what the trimmed clip contains.

`demo/canned/notes/jfk-rice/conference-talk--qwen3.6-35b-a3b.md`:
```markdown
# JFK at Rice University (opening)

## TL;DR
The opening of President Kennedy's September 1962 address at Rice University: thanks to his
hosts, then a framing of the moment as an age of change and challenge.

## Key points
- Opens by greeting the university's leaders, officials and the audience.
- Describes the present as an hour of change and challenge, and a decade of hope and fear.
- Sets up the argument that knowledge and the unknown grow together.

## Questions asked
_None noted._
```

`demo/canned/notes/jfk-rice/conference-talk--claude-opus-5-5.md`:
```markdown
# Opening of "We choose to go to the Moon" (Rice University, 1962)

## TL;DR
Kennedy begins his Rice University speech with formal greetings and a framing of the era:
progress is accelerating, and with it the sense of how much is still unknown.

## Key points
- Formal thanks to the university, state and federal officials present.
- The era is cast as one of change and challenge, of hope and fear.
- The framing builds toward the speech's later case for space exploration.

## Action items
_None noted._
```

`demo/canned/notes/apollo11-first-steps/general-fallback--qwen3.6-35b-a3b.md`:
```markdown
# Apollo 11: first steps

## TL;DR
Neil Armstrong descends the lunar module's ladder and steps onto the Moon while talking with
Mission Control.

## Key points
- Armstrong describes the surface as he reaches the foot of the ladder.
- He speaks the "one small step" line as he steps off.

## Action items
_None noted._
```

`demo/canned/notes/fdr-fireside-1/general-fallback--qwen3.6-35b-a3b.md`:
```markdown
# Fireside Chat 1 (opening)

## TL;DR
Roosevelt opens his first fireside chat by explaining he will talk plainly about banking
to the many people who use banks, not only the few who understand their mechanics.

## Key points
- Addresses the public directly, as "my friends".
- Promises a plain explanation of what happened in the banking crisis and what comes next.

## Action items
_None noted._
```

`demo/canned/notes/apollo13-problem/plaud.md`:
```markdown
# Apollo 13 anomaly call

**Summary:** The crew reports a problem to Houston: a main B bus undervolt. Mission Control
acknowledges and asks them to stand by while it investigates.

**Action items**
- Houston to look into the main B bus undervolt.
```

`demo/canned/my-notes/jfk-rice.md`:
```markdown
Demo of **My notes**: your own notes on a recording live in `my-notes.md` next to it.
```

- [ ] **Step 7: Write `demo/CREDITS.md`**

```markdown
# Demo recordings

All four are public domain, from Wikimedia Commons. They were trimmed and re-encoded by
`demo/fetch.py`. The transcripts are local Whisper output, and the notes are short demo
text written for this repo.

| Recording | Source | License |
|---|---|---|
| JFK at Rice University (first 90 s) | https://commons.wikimedia.org/wiki/File:Jfk_rice_university_we_choose_to_go_to_the_moon.ogg | Public domain (John F. Kennedy Presidential Library & Museum) |
| Apollo 13: Houston, we've had a problem | https://commons.wikimedia.org/wiki/File:Apollo13-wehaveaproblem_edit_1.ogg | Public domain (NASA) |
| Apollo 11: first steps on the Moon | https://commons.wikimedia.org/wiki/File:Apollo_11_Landing_-_first_steps_on_the_moon.ogv | Public domain (NASA) |
| Fireside Chat 1 (first 60 s) | https://commons.wikimedia.org/wiki/File:Fireside_Chat_1_On_the_Banking_Crisis_(March_12,_1933)_Franklin_Delano_Roosevelt.ogg | Public domain (FDR Presidential Library) |

The Apollo 13 recording carries a Plaud-shaped payload and Plaud notes written for the
demo, so the Plaud tab has something to show.
```

- [ ] **Step 8: Commit**

```bash
git add demo/sources.toml demo/fetch.py demo/convert_oneoff.py demo/CREDITS.md demo/canned
git commit -m "feat(demo): public-domain demo sources, canned Whisper transcripts and notes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Build the demo archive deterministically

**Files:**
- Create: `demo/build.py`
- Create: `demo/archive/…` (generated), `demo/canned/aliases.json` (generated)
- Test: `packages/core/tests/test_demo_archive.py`

**Interfaces:**
- Consumes: `Archive`, `RawSource`, `sha256_file` (Task 4); `make_id` (Task 2);
  `write_docs`, `validate` (Task 5); `CannedBackend`, `CompletionRequest` (Task 6);
  `Rendition`, `TagRef` (Task 3).
- Produces:
  - `demo/build.py` with `build(media_dir: Path, out: Path, canned_dir: Path) -> dict[str, str]`
    (recording ID → slug)
  - **The committed demo archive**, four recordings:
    - `jfk-rice`: audio, two conference-talk notes, My notes
    - `apollo13-problem`: audio, untagged, Plaud transcript and notes
    - `apollo11-first-steps`: video
    - `fdr-fireside-1`: audio, `private`
  - `demo/canned/aliases.json`

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_demo_archive.py`:
```python
import importlib.util
import json
import shutil
import tomllib
from pathlib import Path

from recordings.archive import Archive
from recordings.models import is_private, is_untagged
from recordings.selfdoc import validate

REPO = Path(__file__).resolve().parents[3]
DEMO = REPO / "demo"


def load_build():
    spec = importlib.util.spec_from_file_location("demo_build", DEMO / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def aliases() -> dict[str, str]:
    return json.loads((DEMO / "canned" / "aliases.json").read_text())


def test_the_committed_demo_archive_is_valid():
    assert validate(DEMO / "archive") == []


def test_the_demo_covers_what_the_ui_needs():
    archive = Archive(DEMO / "archive")
    by_slug = {aliases()[r.id]: r for r in archive.iter_recordings()}
    assert set(by_slug) == {"jfk-rice", "apollo13-problem", "apollo11-first-steps", "fdr-fireside-1"}
    assert is_untagged(by_slug["apollo13-problem"])
    assert is_private(by_slug["fdr-fireside-1"])
    assert by_slug["apollo11-first-steps"].media.kind == "video"
    jfk_notes = [r for _, r in archive.renditions(by_slug["jfk-rice"].id) if r.kind == "notes"]
    assert {r.model for r in jfk_notes} == {"qwen3.6-35b-a3b", "claude-opus-5-5"}
    plaud = [r for _, r in archive.renditions(by_slug["apollo13-problem"].id) if r.engine == "plaud"]
    assert {r.kind for r in plaud} == {"transcript", "notes"}
    assert archive.read_my_notes(by_slug["jfk-rice"].id)


def test_rebuilding_from_the_archives_own_media_is_byte_identical(tmp_path):
    """The build is deterministic: same media + canned → same archive, byte for byte."""
    entries = tomllib.loads((DEMO / "sources.toml").read_text())["recording"]
    ext = {e["slug"]: e["ext"] for e in entries}
    archive = Archive(DEMO / "archive")
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    for rid, slug in aliases().items():
        shutil.copy(archive.media_path(rid), media_dir / f"{slug}.{ext[slug]}")
    out = tmp_path / "archive"
    built = load_build().build(media_dir, out, DEMO / "canned")
    assert built == aliases()
    committed = {p.relative_to(DEMO / "archive"): p.read_bytes()
                 for p in (DEMO / "archive").rglob("*") if p.is_file()}
    rebuilt = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert rebuilt.keys() == committed.keys()
    for rel in committed:
        assert rebuilt[rel] == committed[rel], f"{rel} differs: run `make demo-archive`"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_demo_archive.py -v`
Expected: FAIL, because `demo/archive` and `demo/canned/aliases.json` don't exist and
`demo/build.py` is missing.

- [ ] **Step 3: Implement `demo/build.py`**

```python
"""Build demo/archive from encoded media + demo/canned. Deterministic: fixed stamps, no clock.

    uv run python demo/build.py --media-dir demo/.cache/media --out demo/archive --force
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from recordings.archive import Archive, RawSource, sha256_file
from recordings.backends import CannedBackend, CompletionRequest
from recordings.ids import make_id
from recordings.models import Rendition, TagRef
from recordings.selfdoc import write_docs

HERE = Path(__file__).resolve().parent
# Every stamp in the demo derives from this instant, so a rebuild is byte-identical.
BUILD_AT = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
WHISPER = {"model": "mlx-community/whisper-large-v3-turbo", "version": "large-v3-turbo@a4aaeec"}


def build(media_dir: Path, out: Path, canned_dir: Path) -> dict[str, str]:
    entries = tomllib.loads((HERE / "sources.toml").read_text())["recording"]
    archive = Archive(out)
    ids: dict[str, str] = {}
    for n, entry in enumerate(entries):
        media = Path(media_dir) / f"{entry['slug']}.{entry['ext']}"
        recorded_at = datetime.fromisoformat(entry["recorded_at"]).replace(
            tzinfo=ZoneInfo(entry["timezone"]))
        sha = sha256_file(media)
        rid = make_id(recorded_at, sha)
        ids[rid] = entry["slug"]
        backend = CannedBackend(canned_dir, {rid: entry["slug"]})
        stamp = BUILD_AT + timedelta(minutes=n)
        renditions = [Rendition(
            kind="transcript", engine="canned", model=WHISPER["model"],
            version=WHISPER["version"], created_at=stamp,
            inputs={"audio_sha256": sha}, meta={"demo": True},
            payload=backend.transcribe(media, recording_id=rid).model_dump(exclude_none=True),
        )]
        for i, note in enumerate(entry.get("notes", []), start=1):
            done = backend.complete(CompletionRequest(
                prompt="(canned)", model=note["model"], recording_id=rid,
                note_type=note["note_type"]))
            renditions.append(Rendition(
                kind="notes", note_type=note["note_type"], engine="canned", model=done.model,
                version=f"{done.model}@demo", created_at=stamp + timedelta(seconds=i),
                inputs={"audio_sha256": sha}, meta={"demo": True},
                payload={"markdown": done.text}))
        payload = None
        if entry.get("plaud"):
            transcript = renditions[0].payload
            renditions.append(Rendition(
                kind="transcript", engine="plaud", model="plaud", version="plaud@demo",
                created_at=stamp + timedelta(seconds=30), meta={"demo": True},
                payload=transcript))
            renditions.append(Rendition(
                kind="notes", note_type="plaud-summary", engine="plaud", model="plaud",
                version="plaud@demo", created_at=stamp + timedelta(seconds=31),
                meta={"demo": True},
                payload={"markdown": (canned_dir / "notes" / entry["slug"] / "plaud.md")
                         .read_text(encoding="utf-8")}))
            payload = json.dumps({"id": entry["source_ref"], "demo": True,
                                  "name": entry["title"]}, indent=2).encode() + b"\n"
        my_notes_path = canned_dir / "my-notes" / f"{entry['slug']}.md"
        archive.add_recording(
            media=media,
            recorded_at=recorded_at,
            timezone_name=entry["timezone"],
            time_source=entry["time_source"],
            title=entry["title"],
            kind=entry["kind"],
            duration_ms=entry["duration_ms"],
            source=RawSource(kind=entry["source_kind"], ref=entry["source_ref"],
                             added_at=BUILD_AT, payload=payload),
            tags=[TagRef(**t) for t in entry.get("tags", [])],
            renditions=renditions,
            my_notes=my_notes_path.read_text(encoding="utf-8") if my_notes_path.is_file() else None,
        )
    write_docs(archive.root)
    return dict(sorted(ids.items()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--media-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--canned", type=Path, default=HERE / "canned")
    ap.add_argument("--force", action="store_true", help="replace an existing --out")
    args = ap.parse_args(argv)
    if args.out.exists():
        if not args.force:
            print(f"{args.out} exists; pass --force to rebuild it", file=sys.stderr)
            return 1
        shutil.rmtree(args.out)
    ids = build(args.media_dir, args.out, args.canned)
    (args.canned / "aliases.json").write_text(json.dumps(ids, indent=2) + "\n")
    print(f"built {len(ids)} recordings into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Build the demo archive**

Run: `make demo-archive && uv run recordings validate demo/archive`
Expected: `built 4 recordings into demo/archive`, then `problems: []`.

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest packages/core -v`
Expected: all pass, including the 3 demo-archive tests.

- [ ] **Step 6: Commit**

```bash
git add demo/build.py demo/archive demo/canned/aliases.json
git commit -m "feat(demo): deterministic demo archive with four public-domain recordings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Configuration and secrets

Settings live in three places, and only one of them is ever committed with values in it:

| What | Where | Committed? |
|---|---|---|
| App settings (archive path, schedules, endpoints, model names) | `config.toml` at the repo root, or wherever `RECORDINGS_CONFIG` points | **No.** Git ignores it. `config.example.toml` is the committed template. |
| Secrets (Plaud token, Spark key, Claude token) | Environment variables `NAME`, or `NAME_FILE` pointing at a file (how Docker secrets arrive, at `/run/secrets/…`) | **Never.** Not in the repo, `config.toml`, the image or logs. |
| Docker host settings (host paths, bind IP, port, UID/GID, secrets folder) | `docker/deploy.env`, passed to compose with `--env-file` | **No.** Git ignores it. `docker/deploy.example.env` is the template (Task 16). |

**Precedence:** an environment variable beats `config.toml`, which beats the built-in
default. **Demo mode reads none of these** (spec §17).

**Files:**
- Create: `packages/core/src/recordings/config.py`, `config.example.toml`
- Modify: `packages/core/src/recordings/cli.py` (adds `doctor`), `.gitignore`
- Test: `packages/core/tests/test_config.py`

**Interfaces:**
- Produces:
  - **Types:** `ConfigError(RuntimeError)`;
    `Config(path: Path | None, archive_path: Path | None, writer_host: str | None, default_timezone: str, base_url: str | None, data: dict)`
  - **Functions:** `config_path(environ) -> Path`, `load_config(environ) -> Config`,
    `secret(name, environ) -> str | None` and `doctor(environ) -> dict`, which reports
    presence only
  - **Secret registry:** `SECRETS: dict[str, SecretSpec(env, purpose, stage)]` with the
    names `plaud_token`, `spark_api_key` and `claude_oauth_token`
  - **CLI:** `recordings doctor [--json]` exits 78 (EX_CONFIG) on problems

- [ ] **Step 1: Write the template**

`config.example.toml`:
```toml
# recordings configuration: the committed TEMPLATE. Copy it and edit the copy:
#   cp config.example.toml config.toml        (config.toml is git-ignored)
# Never put secrets here; they come from the environment (bottom of this file).
# A section marked (stage N) is read from that build stage on, and ignored before it.

[archive]
# The archive folder. In Docker the container sees it at /archive, and docker/compose.yml sets
# RECORDINGS_ARCHIVE=/archive, which overrides this value.
path = "/srv/recordings/archive"
# The only machine allowed to write the archive (stage 2): that machine's hostname.
writer_host = "my-homelab"
# Time zone for recordings whose source doesn't say.
default_timezone = "America/Vancouver"

[server]
# How you reach the app; used to build stable /r/<id> links (stage 3).
base_url = "http://my-homelab:8000"

[index]
# SQLite index and job queue (stage 3), on the same local disk as the archive.
path = "/data/index.sqlite"

[plaud]                                 # stage 2
schedule_minutes = 20                   # scheduled sync interval; 0 turns it off
auto_import = true                      # import missing recordings (safe: nothing runs until tagged)
recheck_days = 14                       # keep re-checking recent recordings for Plaud's late notes
auto_private_patterns = ["^private"]    # titles matching these are tagged `private`

[spark]                                 # stage 4
base_url = "https://spark.your-tailnet.ts.net"   # llama-swap, reached over Tailscale
whisper_model = "whisper-large-v3-turbo"
pick_model = "gemma-4-26b-a4b"                   # small model that auto-picks note types

[models]                                # stage 4: the names tags.yaml refers to
"spark:default" = { backend = "spark", model = "qwen3.6-35b-a3b" }
"claude:opus" = { backend = "claude", model = "opus" }

[prompts]                               # stage 4
extra_dirs = []                         # prompt folders beyond the repo's prompts/

[watched_folder]                        # stage 5
path = "/watch"

# ---- Secrets: environment only, never in this file --------------------------------------
# Each may instead be given as NAME_FILE=<path to a file holding it>; Docker secrets arrive
# that way, under /run/secrets/. `recordings doctor` says which are set, never their values.
#   RECORDINGS_PLAUD_TOKEN      Plaud API token                              (stage 2)
#   RECORDINGS_SPARK_API_KEY    this app's llama-swap key (`spark keys create`)   (stage 4)
#   CLAUDE_CODE_OAUTH_TOKEN     Claude subscription token (`claude setup-token`)  (stage 4)
```

Add to `.gitignore`:
```gitignore
# Real settings and deploy values; the committed templates are *.example.*
/config.toml
docker/deploy.env
secrets/
```

- [ ] **Step 2: Write the failing tests**

`packages/core/tests/test_config.py`:
```python
import json
import tomllib
from pathlib import Path

import pytest

from recordings.cli import main
from recordings.config import SECRETS, ConfigError, doctor, load_config, secret

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / "config.example.toml"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_template_parses_and_loads():
    tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    cfg = load_config({"RECORDINGS_CONFIG": str(TEMPLATE)})
    assert cfg.archive_path == Path("/srv/recordings/archive")
    assert cfg.data["models"]["claude:opus"]["backend"] == "claude"


def test_the_template_documents_every_secret_and_sets_none():
    text = TEMPLATE.read_text(encoding="utf-8")
    for spec in SECRETS.values():
        assert spec.env in text, f"{spec.env} missing from config.example.toml"
        assert f"{spec.env}=" not in text and f"{spec.env} =" not in text


def test_environment_overrides_the_file(tmp_path):
    cfg_file = write(tmp_path, '[archive]\npath = "/from/file"\n')
    env = {"RECORDINGS_CONFIG": str(cfg_file), "RECORDINGS_ARCHIVE": "/from/env"}
    assert load_config(env).archive_path == Path("/from/env")


def test_no_file_means_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config({})
    assert cfg.path is None and cfg.archive_path is None
    assert cfg.default_timezone == "America/Vancouver"


def test_a_broken_file_names_itself(tmp_path):
    with pytest.raises(ConfigError, match="config.toml"):
        load_config({"RECORDINGS_CONFIG": str(write(tmp_path, "path = [unclosed"))})


def test_a_missing_explicit_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_config({"RECORDINGS_CONFIG": str(tmp_path / "nope.toml")})


def test_secret_from_env_or_file(tmp_path):
    assert secret("plaud_token", {}) is None
    assert secret("plaud_token", {"RECORDINGS_PLAUD_TOKEN": "abc"}) == "abc"
    f = tmp_path / "plaud_token"
    f.write_text("from-file\n")
    assert secret("plaud_token", {"RECORDINGS_PLAUD_TOKEN_FILE": str(f)}) == "from-file"
    with pytest.raises(ConfigError, match="not both"):
        secret("plaud_token", {"RECORDINGS_PLAUD_TOKEN": "a", "RECORDINGS_PLAUD_TOKEN_FILE": str(f)})


def test_doctor_reports_presence_never_values(tmp_path, capsys):
    archive = tmp_path / "archive"
    archive.mkdir()
    env = {
        "RECORDINGS_CONFIG": str(write(tmp_path, f'[archive]\npath = "{archive}"\n')),
        "RECORDINGS_PLAUD_TOKEN": "sekrit-value-123",
    }
    report = doctor(env)
    assert report["problems"] == []
    assert report["secrets"]["plaud_token"]["set"] is True
    assert report["secrets"]["claude_oauth_token"]["set"] is False
    assert "sekrit-value-123" not in json.dumps(report)


def test_cli_doctor_exits_78_when_the_archive_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RECORDINGS_ARCHIVE", raising=False)
    monkeypatch.delenv("RECORDINGS_CONFIG", raising=False)
    assert main(["doctor", "--json"]) == 78
    assert "no archive path" in json.loads(capsys.readouterr().out)["problems"][0]
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest packages/core/tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings.config'`.

- [ ] **Step 4: Implement**

`packages/core/src/recordings/config.py`:
```python
"""Machine settings and secrets (spec §5).

config.toml holds settings and never secrets. It is git-ignored at the repo root;
config.example.toml is the committed template; RECORDINGS_CONFIG points elsewhere (Docker:
/config/config.toml). Secrets come only from the environment: NAME, or NAME_FILE naming a
file (Docker secrets live in /run/secrets/). A secret's value is never printed or logged.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("config.toml")


@dataclass(frozen=True)
class SecretSpec:
    env: str
    purpose: str
    stage: int  # the build stage that first needs it


SECRETS: dict[str, SecretSpec] = {
    "plaud_token": SecretSpec("RECORDINGS_PLAUD_TOKEN", "Plaud API token (Plaud sync)", 2),
    "spark_api_key": SecretSpec(
        "RECORDINGS_SPARK_API_KEY", "this app's llama-swap key on the Spark", 4),
    "claude_oauth_token": SecretSpec(
        "CLAUDE_CODE_OAUTH_TOKEN", "Claude subscription token from `claude setup-token`", 4),
}


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    path: Path | None  # the file actually read, or None
    archive_path: Path | None
    writer_host: str | None
    default_timezone: str
    base_url: str | None
    data: dict[str, Any] = field(default_factory=dict)  # every section, for later stages


def config_path(environ: Mapping[str, str]) -> Path:
    return Path(environ.get("RECORDINGS_CONFIG") or DEFAULT_PATH)


def load_config(environ: Mapping[str, str]) -> Config:
    path = config_path(environ)
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from None
    elif environ.get("RECORDINGS_CONFIG"):
        raise ConfigError(f"RECORDINGS_CONFIG points at {path}, which does not exist")
    archive = data.get("archive", {})
    # The environment wins, so one config.toml works on the host and inside Docker.
    archive_path = environ.get("RECORDINGS_ARCHIVE") or archive.get("path")
    return Config(
        path=path if path.is_file() else None,
        archive_path=Path(archive_path).expanduser() if archive_path else None,
        writer_host=archive.get("writer_host"),
        default_timezone=archive.get("default_timezone", "America/Vancouver"),
        base_url=data.get("server", {}).get("base_url"),
        data=data,
    )


def secret(name: str, environ: Mapping[str, str]) -> str | None:
    spec = SECRETS[name]
    direct = environ.get(spec.env)
    file_var = environ.get(f"{spec.env}_FILE")
    if direct and file_var:
        raise ConfigError(f"set {spec.env} or {spec.env}_FILE, not both")
    if file_var:
        path = Path(file_var)
        if not path.is_file():
            raise ConfigError(f"{spec.env}_FILE points at {path}, which does not exist")
        return path.read_text(encoding="utf-8").strip() or None
    return direct or None


def doctor(environ: Mapping[str, str]) -> dict[str, Any]:
    """What is configured, as presence only: never a secret's value."""
    report: dict[str, Any] = {"config": None, "archive": None, "secrets": {}, "problems": []}
    try:
        cfg = load_config(environ)
    except ConfigError as exc:
        report["problems"].append(str(exc))
        return report
    report["config"] = str(cfg.path) if cfg.path else None
    if cfg.archive_path is None:
        report["problems"].append(
            "no archive path: set [archive] path in config.toml, or RECORDINGS_ARCHIVE")
    else:
        report["archive"] = {"path": str(cfg.archive_path), "exists": cfg.archive_path.is_dir()}
        if not cfg.archive_path.is_dir():
            report["problems"].append(f"archive folder {cfg.archive_path} does not exist")
    for name, spec in SECRETS.items():
        try:
            present = secret(name, environ) is not None
        except ConfigError as exc:
            report["problems"].append(str(exc))
            present = False
        report["secrets"][name] = {
            "env": spec.env, "set": present, "needed_from_stage": spec.stage,
            "purpose": spec.purpose,
        }
    return report
```

In `packages/core/src/recordings/cli.py`:
- Add `import os` to the imports.
- Change the package import to `from recordings import __version__, config, schemas, selfdoc`.
- In `build_parser()`, before `return parser`, add:
```python
    p = sub.add_parser("doctor", help="check config.toml, the archive and secrets (presence only)")
    p.add_argument("--json", action="store_true")
```
- Above `main`, add:
```python
def cmd_doctor(args: argparse.Namespace) -> int:
    report = config.doctor(os.environ)
    _emit(report, args.json)
    return 78 if report["problems"] else 0  # 78 = EX_CONFIG, as audio-router's doctor uses
```
- Add `"doctor": cmd_doctor` to the `commands` dict in `main`.

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest packages/core -v`
Expected: all pass (test_config: 9 passed).

- [ ] **Step 6: Commit**

```bash
git add config.example.toml .gitignore packages/core
git commit -m "feat(core): config.toml with a committed template, env/_FILE secrets, doctor

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Library views (pure functions)

**Files:**
- Create: `packages/ui/src/recordings_ui/views.py`
- Create: `packages/ui/tests/conftest.py`
- Test: `packages/ui/tests/test_views.py`

**Interfaces:**
- Consumes: `Archive` (Task 4); `is_private`, `is_untagged`, `Rendition` (Task 3).
- Produces: `library_view(archive) -> dict`, `recording_view(archive, rid) -> dict | None`
  and `render_markdown(text) -> str`. These JSON shapes are the client contract, mirrored
  in `frontend/src/types.ts` (Task 12):

```text
LibraryView = {
  recordings: [{id, title, recorded_at, when, duration, kind, sources: [str],
                tags: [{tag, by}], private: bool, untagged: bool}],   # newest first
  counts: {all: int, untagged: int},
  tags: [{tag, count, private}],          # user tags (not notes/*), sorted by name
  note_types: [{note_type, count}],
  problems: [{path, message}]
}
RecordingView = {
  id, title, recorded_at, when, timezone, duration, kind, private, media_url,
  tags: [{tag, by}], sources: [{kind, ref, added_at}],
  transcripts: [{rendition, label, engine, model, created_at,
                 turns: [{start, end, speaker, text, words: [{word, start, end}]}]}],
  chosen_transcript: str | null,
  notes: [{note_type, outputs: [{rendition, model, engine, created_at, html}]}],  # newest first
  plaud_notes: [{rendition, created_at, html}],
  my_notes_html: str | null,
  renditions: [{rendition, kind, note_type, engine, model, created_at}]
}
```

- [ ] **Step 1: Write the shared fixtures**

`packages/ui/tests/conftest.py`:
```python
import json
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DEMO_ARCHIVE = REPO / "demo" / "archive"


@pytest.fixture
def demo_archive(tmp_path) -> Path:
    """A fresh copy, so no test can change the committed demo."""
    dst = tmp_path / "archive"
    shutil.copytree(DEMO_ARCHIVE, dst)
    return dst


@pytest.fixture(scope="session")
def demo_ids() -> dict[str, str]:
    aliases = json.loads((REPO / "demo" / "canned" / "aliases.json").read_text())
    return {slug: rid for rid, slug in aliases.items()}
```

- [ ] **Step 2: Write the failing tests**

`packages/ui/tests/test_views.py`:
```python
from datetime import datetime, timezone

from recordings.archive import Archive, RawSource
from recordings.models import Rendition
from recordings_ui.views import library_view, recording_view, render_markdown


def test_library_lists_the_demo_newest_first(demo_archive, demo_ids):
    view = library_view(Archive(demo_archive))
    assert [r["id"] for r in view["recordings"]] == [
        demo_ids["apollo13-problem"], demo_ids["apollo11-first-steps"],
        demo_ids["jfk-rice"], demo_ids["fdr-fireside-1"]]
    assert view["counts"] == {"all": 4, "untagged": 1}
    assert {t["tag"] for t in view["tags"]} == {"talks", "space/apollo", "private"}
    assert next(t for t in view["tags"] if t["tag"] == "private")["private"] is True
    assert {n["note_type"] for n in view["note_types"]} == {"conference-talk", "general-fallback"}
    assert view["problems"] == []


def test_library_reports_a_broken_file_and_keeps_the_rest(demo_archive, demo_ids):
    archive = Archive(demo_archive)
    (archive.path_for(demo_ids["jfk-rice"]) / "recording.json").write_text("{", encoding="utf-8")
    view = library_view(archive)
    assert view["counts"]["all"] == 3
    assert len(view["problems"]) == 1 and view["problems"][0]["path"].endswith("recording.json")


def test_recording_view_for_jfk(demo_archive, demo_ids):
    view = recording_view(Archive(demo_archive), demo_ids["jfk-rice"])
    assert view["media_url"] == f"/media/{demo_ids['jfk-rice']}"
    assert view["kind"] == "audio" and view["private"] is False
    assert view["chosen_transcript"] == view["transcripts"][0]["rendition"]
    turns = view["transcripts"][0]["turns"]
    assert turns and all(t["end"] >= t["start"] for t in turns)
    (group,) = view["notes"]
    assert group["note_type"] == "conference-talk"
    assert {o["model"] for o in group["outputs"]} == {"qwen3.6-35b-a3b", "claude-opus-5-5"}
    assert "<h1>" in group["outputs"][0]["html"]
    assert "My notes" in view["my_notes_html"]


def test_plaud_outputs_go_to_the_plaud_tab_not_notes(demo_archive, demo_ids):
    view = recording_view(Archive(demo_archive), demo_ids["apollo13-problem"])
    assert view["notes"] == []
    assert len(view["plaud_notes"]) == 1
    assert {t["engine"] for t in view["transcripts"]} == {"canned", "plaud"}


def test_unknown_or_malformed_id_is_none(demo_archive):
    archive = Archive(demo_archive)
    assert recording_view(archive, "20200101T000000+0000_00000000") is None
    assert recording_view(archive, "../../etc/passwd") is None
    assert recording_view(archive, "20261399T256199+0000_deadbeef") is None  # regex-valid, impossible date


def test_a_recording_with_no_outputs_has_empty_tabs(tmp_path):
    media = tmp_path / "x.mp3"
    media.write_bytes(b"ID3")
    archive = Archive(tmp_path / "archive")
    rec = archive.add_recording(
        media=media, recorded_at=datetime(2026, 10, 1, 9, tzinfo=timezone.utc),
        timezone_name="UTC", time_source="ingest", title="Fresh upload", kind="audio",
        source=RawSource(kind="upload", ref="x.mp3", added_at=datetime(2026, 10, 1, tzinfo=timezone.utc)))
    view = recording_view(archive, rec.id)
    assert view["transcripts"] == [] and view["chosen_transcript"] is None
    assert view["notes"] == [] and view["plaud_notes"] == [] and view["my_notes_html"] is None


def test_model_written_html_is_escaped(tmp_path):
    assert "<script>" not in render_markdown("hi <script>alert(1)</script>")
    assert "&lt;script&gt;" in render_markdown("hi <script>alert(1)</script>")
    media = tmp_path / "x.mp3"
    media.write_bytes(b"ID3")
    archive = Archive(tmp_path / "archive")
    t = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rec = archive.add_recording(
        media=media, recorded_at=t, timezone_name="UTC", time_source="ingest", title="x",
        kind="audio", source=RawSource(kind="upload", ref="x", added_at=t),
        renditions=[Rendition(kind="notes", note_type="lecture", engine="canned", model="m",
                              version="m@1", created_at=t,
                              payload={"markdown": "<img src=x onerror=alert(1)>"})])
    html = recording_view(archive, rec.id)["notes"][0]["outputs"][0]["html"]
    assert "<img" not in html
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest packages/ui/tests/test_views.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings_ui.views'`.

- [ ] **Step 4: Implement**

`packages/ui/src/recordings_ui/views.py`:
```python
"""Pure functions from the archive to the JSON the React client draws (spec §12.1).

Kept out of shiny_app.py so they are testable without a session (shinyreact-build-app
skill, "Verify it": factor pure logic out of the app file).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from markdown_it import MarkdownIt

from recordings.archive import Archive
from recordings.ids import ID_RE
from recordings.models import Recording, Rendition, is_private, is_untagged

# html=False: any HTML in model-written notes is escaped, never rendered (review focus #4).
_md = MarkdownIt("commonmark", {"html": False}).enable("table")


def render_markdown(text: str) -> str:
    return _md.render(text)


def when_label(t: datetime) -> str:
    return f"{t:%a %b} {t.day} {t.year} · {t:%H:%M}"


def duration_label(ms: int | None) -> str | None:
    if ms is None:
        return None
    seconds = round(ms / 1000)
    if seconds < 60:
        return f"{seconds} s"
    minutes = seconds // 60
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def _tags(rec: Recording) -> list[dict]:
    return [{"tag": t.tag, "by": t.by} for t in rec.tags]


def _row(rec: Recording) -> dict:
    return {
        "id": rec.id,
        "title": rec.title,
        "recorded_at": rec.recorded_at.isoformat(),
        "when": when_label(rec.recorded_at),
        "duration": duration_label(rec.media.duration_ms),
        "kind": rec.media.kind,
        "sources": sorted({s.kind for s in rec.sources}),
        "tags": _tags(rec),
        "private": is_private(rec),
        "untagged": is_untagged(rec),
    }


def library_view(archive: Archive) -> dict:
    recordings = sorted(archive.iter_recordings(), key=lambda r: r.recorded_at, reverse=True)
    counts = Counter(t.tag for rec in recordings for t in rec.tags)
    return {
        "recordings": [_row(r) for r in recordings],
        "counts": {"all": len(recordings), "untagged": sum(is_untagged(r) for r in recordings)},
        "tags": [
            {"tag": tag, "count": n, "private": tag == "private" or tag.startswith("private/")}
            for tag, n in sorted(counts.items()) if not tag.startswith("notes/")
        ],
        "note_types": [
            {"note_type": tag.removeprefix("notes/"), "count": n}
            for tag, n in sorted(counts.items()) if tag.startswith("notes/")
        ],
        "problems": [{"path": str(p.path), "message": p.message} for p in archive.problems],
    }


def _turns(r: Rendition, speakers: dict[str, str]) -> list[dict]:
    out = []
    for seg in r.payload.get("segments", []):
        speaker = seg.get("speaker")
        out.append({
            "start": seg["start"],
            "end": seg["end"],
            "speaker": speakers.get(speaker, speaker) if speaker else None,
            "text": seg["text"],
            "words": [{"word": w["word"], "start": w["start"], "end": w["end"]}
                      for w in seg.get("words", [])],
        })
    return out


def _label(r: Rendition) -> str:
    return "Plaud" if r.engine == "plaud" else f"{(r.model or r.engine).split('/')[-1]} · {r.engine}"


def recording_view(archive: Archive, rid: str) -> dict | None:
    if not ID_RE.fullmatch(rid or ""):
        return None
    try:
        rec = archive.load(rid)
    except (KeyError, ValueError):  # unknown id, impossible date in the id, or a broken file
        return None
    outputs = archive.renditions(rid)
    transcripts = [
        {"rendition": path, "label": _label(r), "engine": r.engine, "model": r.model,
         "created_at": r.created_at.isoformat(), "turns": _turns(r, rec.speakers)}
        for path, r in outputs if r.kind == "transcript"
    ]
    names = [t["rendition"] for t in transcripts]
    chosen = rec.chosen.transcript if rec.chosen.transcript in names else (names[-1] if names else None)

    groups: dict[str, list[dict]] = {}
    for path, r in reversed(outputs):  # newest first inside each group
        if r.kind == "notes" and r.engine != "plaud":
            groups.setdefault(r.note_type, []).append({
                "rendition": path, "model": r.model, "engine": r.engine,
                "created_at": r.created_at.isoformat(),
                "html": render_markdown(r.payload["markdown"]),
            })
    my_notes = archive.read_my_notes(rid)
    return {
        "id": rec.id,
        "title": rec.title,
        "recorded_at": rec.recorded_at.isoformat(),
        "when": when_label(rec.recorded_at),
        "timezone": rec.timezone,
        "duration": duration_label(rec.media.duration_ms),
        "kind": rec.media.kind,
        "private": is_private(rec),
        "media_url": f"/media/{rec.id}",
        "tags": _tags(rec),
        "sources": [{"kind": s.kind, "ref": s.ref, "added_at": s.added_at.isoformat()}
                    for s in rec.sources],
        "transcripts": transcripts,
        "chosen_transcript": chosen,
        "notes": [{"note_type": k, "outputs": v} for k, v in sorted(groups.items())],
        "plaud_notes": [
            {"rendition": path, "created_at": r.created_at.isoformat(),
             "html": render_markdown(r.payload["markdown"])}
            for path, r in outputs if r.kind == "notes" and r.engine == "plaud"
        ],
        "my_notes_html": render_markdown(my_notes) if my_notes is not None else None,
        "renditions": [
            {"rendition": path, "kind": r.kind, "note_type": r.note_type, "engine": r.engine,
             "model": r.model, "created_at": r.created_at.isoformat()}
            for path, r in outputs
        ],
    }
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest packages/ui/tests/test_views.py -v`
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add packages/ui
git commit -m "feat(ui): library and recording views as pure JSON functions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: The UI server: settings, FastAPI, media route, Shiny server

Before Step 3, load `/shinyreact-build-app` (run `make skills` first) and check `ReactApp`
and `reactive_output` against it. Only `shiny_app.py` touches shinyreact in this task.

**Files:**
- Create: `packages/ui/src/recordings_ui/settings.py`, `runtime.py`, `app.py`, `shiny_app.py`, `__main__.py`
- Test: `packages/ui/tests/test_settings.py`, `test_app.py`, `test_shiny_server.py`

**Interfaces:**
- Consumes: `Archive`, `parse_id` (Tasks 2 and 4); `load_config`, `ConfigError` (Task 9);
  `library_view`, `recording_view` (Task 10).
- Produces:
  - `Settings(archive: Path, demo: bool)`
  - `settings.from_env(environ: Mapping[str, str], *, demo: bool | None = None) -> Settings`
  - `runtime.configure(archive)` and `runtime.archive() -> Archive`
  - `app.create_app(settings) -> FastAPI`
  - **Routes:** `GET /healthz` returns `{"ok": true, "demo": bool}`.
    `GET /media/{recording_id}` serves the media file with Range support.
  - **Shiny contract:** input `selected_id: str | None`. Outputs `library`
    (`LibraryView`) and `recording` (`RecordingView | None`).
  - **Command:** `recordings-ui [--demo] [--host H] [--port P]`, also `python -m recordings_ui`.

- [ ] **Step 1: Write the failing tests**

`packages/ui/tests/test_settings.py`:
```python
import pytest

from recordings_ui.settings import from_env


def test_demo_works_on_a_fresh_copy_and_ignores_the_real_archive(tmp_path):
    real = tmp_path / "real-archive"
    real.mkdir()
    s = from_env({"RECORDINGS_ARCHIVE": str(real)}, demo=True)
    assert s.demo is True
    assert s.archive != real and s.archive.resolve() != real.resolve()
    assert (s.archive / "README.md").is_file()  # a copy of the demo archive
    second = from_env({}, demo=True)
    assert second.archive != s.archive  # every start gets its own copy


def test_demo_can_be_switched_on_by_environment():
    assert from_env({"RECORDINGS_DEMO": "1"}).demo is True


def test_demo_never_reads_config_even_a_broken_one(tmp_path):
    bad = tmp_path / "config.toml"
    bad.write_text("path = [unclosed", encoding="utf-8")
    assert from_env({"RECORDINGS_CONFIG": str(bad)}, demo=True).demo is True


def test_real_mode_reads_the_archive_from_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[archive]\npath = "{tmp_path}"\n', encoding="utf-8")
    assert from_env({"RECORDINGS_CONFIG": str(cfg)}).archive == tmp_path


def test_real_mode_needs_an_archive_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no config.toml here, whatever the repo root holds
    with pytest.raises(SystemExit, match="archive"):
        from_env({})
    assert from_env({"RECORDINGS_ARCHIVE": str(tmp_path)}).archive == tmp_path
```

`packages/ui/tests/test_app.py`:
```python
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recordings_ui.app import create_app
from recordings_ui.settings import Settings

WWW = Path(__file__).resolve().parents[1] / "src" / "recordings_ui" / "www"


@pytest.fixture
def client(demo_archive):
    with TestClient(create_app(Settings(archive=demo_archive, demo=True))) as c:
        yield c


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True, "demo": True}


def test_media_is_served_with_ranges(client, demo_ids):
    url = f"/media/{demo_ids['jfk-rice']}"
    full = client.get(url)
    assert full.status_code == 200 and full.headers["content-type"] == "audio/mpeg"
    part = client.get(url, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 0-99/{len(full.content)}"
    assert len(part.content) == 100


def test_a_range_past_the_end_is_416_not_500(client, demo_ids):
    r = client.get(f"/media/{demo_ids['jfk-rice']}", headers={"Range": "bytes=999999999-"})
    assert r.status_code == 416


@pytest.mark.parametrize(
    "bad", ["20200101T000000+0000_00000000", "not-an-id", "20261399T256199+0000_deadbeef"])
def test_unknown_or_malformed_media_is_404(client, bad):
    assert client.get(f"/media/{bad}").status_code == 404


def test_video_media_type(client, demo_ids):
    r = client.get(f"/media/{demo_ids['apollo11-first-steps']}", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.headers["content-type"] == "video/mp4"


@pytest.mark.skipif(not (WWW / "ui.js").is_file(), reason="frontend not built (make build)")
def test_the_page_and_the_fonts_are_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "ui.js" in page.text
    font = client.get("/fonts/AtkinsonHyperlegible-Regular.woff2")
    assert font.status_code == 200
```

`packages/ui/tests/test_shiny_server.py`:
```python
from pathlib import Path

import pytest
from shiny.testserver import TestServerSession

from recordings.archive import Archive
from recordings_ui import runtime

SHINY_APP = Path(__file__).resolve().parents[1] / "src" / "recordings_ui" / "shiny_app.py"
pytestmark = pytest.mark.parametrize("local_server", [str(SHINY_APP)], indirect=True)


@pytest.fixture(autouse=True)
def _archive(demo_archive):
    runtime.configure(Archive(demo_archive))
    yield
    runtime.configure(None)


def test_library_is_published(local_server: TestServerSession):
    assert local_server.get_output("library").value["counts"] == {"all": 4, "untagged": 1}


def test_nothing_selected_is_silent(local_server: TestServerSession):
    assert local_server.get_output("recording").status == "silent"


def test_selecting_publishes_the_recording(local_server: TestServerSession, demo_ids):
    local_server.set_inputs(selected_id=demo_ids["jfk-rice"])
    assert local_server.get_output("recording").value["id"] == demo_ids["jfk-rice"]


def test_clearing_the_selection_publishes_none(local_server: TestServerSession):
    local_server.set_inputs(selected_id=None)
    assert local_server.get_output("recording").value is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/ui -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recordings_ui.settings'`.

- [ ] **Step 3: Implement the settings, runtime, Shiny server and app**

`packages/ui/src/recordings_ui/settings.py`:
```python
"""Where the UI reads from. Demo mode never touches real data (spec §17)."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from recordings.config import ConfigError, load_config


@dataclass(frozen=True)
class Settings:
    archive: Path
    demo: bool


def find_demo_archive(environ: Mapping[str, str]) -> Path:
    if environ.get("RECORDINGS_DEMO_ARCHIVE"):
        return Path(environ["RECORDINGS_DEMO_ARCHIVE"])
    # Running from the repo (editable install): walk up to demo/archive.
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "demo" / "archive"
        if (candidate / "README.md").is_file():
            return candidate
    raise SystemExit("demo archive not found; set RECORDINGS_DEMO_ARCHIVE")


def from_env(environ: Mapping[str, str], *, demo: bool | None = None) -> Settings:
    if demo is None:
        demo = environ.get("RECORDINGS_DEMO") == "1"
    if demo:
        # A fresh copy every start: the same state for every bug report, and the committed
        # demo is never modified. config.toml, RECORDINGS_ARCHIVE and secrets are never read.
        copy = Path(tempfile.mkdtemp(prefix="recordings-demo-")) / "archive"
        shutil.copytree(find_demo_archive(environ), copy)
        return Settings(archive=copy, demo=True)
    try:
        cfg = load_config(environ)
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None
    if cfg.archive_path is None:
        raise SystemExit(
            "no archive path: set [archive] path in config.toml (see config.example.toml), "
            "set RECORDINGS_ARCHIVE, or run with --demo")
    return Settings(archive=cfg.archive_path, demo=False)
```

`packages/ui/src/recordings_ui/runtime.py`:
```python
"""The archive the running app serves. Set once by create_app (or by tests)."""

from __future__ import annotations

from recordings.archive import Archive

_archive: Archive | None = None


def configure(archive: Archive | None) -> None:
    global _archive
    _archive = archive


def archive() -> Archive:
    if _archive is None:
        raise RuntimeError("recordings_ui.runtime.configure() was not called")
    return _archive
```

`packages/ui/src/recordings_ui/shiny_app.py`:
```python
"""Shiny server for the React client. Server holds only reactive computation and returns JSON
(shinyreact-build-app skill). ReactApp discovers www/ui.js + www/ui.css next to THIS file, and
reads the immediate calling frame to do so, so ReactApp(...) must be called here, not in a helper.
"""

from __future__ import annotations

from shiny import Inputs, Outputs, Session
from shinyreact import ReactApp, reactive_output

from recordings_ui import runtime, views


def server(input: Inputs, output: Outputs, session: Session) -> None:
    archive = runtime.archive()

    @reactive_output
    def library():
        return views.library_view(archive)

    @reactive_output
    def recording():
        # Unset (before the client's first message) is a silent exception, which the client
        # sees as "pending"; an explicit null clears the pane.
        rid = input.selected_id()
        return views.recording_view(archive, rid) if rid else None


app = ReactApp(server)
```

`packages/ui/src/recordings_ui/app.py`:
```python
"""FastAPI app: media streaming and health here, the Shiny app mounted at "/" (spec §13)."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from recordings.archive import Archive
from recordings.ids import ID_RE
from recordings_ui import runtime
from recordings_ui.settings import Settings


def create_app(settings: Settings) -> FastAPI:
    archive = Archive(settings.archive)
    runtime.configure(archive)
    from recordings_ui.shiny_app import app as shiny_app  # after configure()

    api = FastAPI(title="recordings", docs_url=None, redoc_url=None, openapi_url=None)

    @api.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "demo": settings.demo}

    @api.get("/media/{recording_id}")
    def media(recording_id: str) -> FileResponse:
        # The ID picks the file via recording.json, so no client-supplied path is ever used.
        if not ID_RE.fullmatch(recording_id):
            raise HTTPException(status_code=404)
        try:
            path = archive.media_path(recording_id)
        except (KeyError, ValueError):  # unknown id, impossible date in the id, or a broken file
            raise HTTPException(status_code=404) from None
        # FileResponse answers Range with 206, and an unsatisfiable range with 416 (Starlette docs).
        return FileResponse(path, content_disposition_type="inline")

    api.mount("/", shiny_app)  # last, so the routes above win
    return api
```

`packages/ui/src/recordings_ui/__main__.py`:
```python
"""`recordings-ui`: run the web app. `--demo` serves a fresh copy of the demo archive."""

from __future__ import annotations

import argparse
import os

import uvicorn

from recordings_ui.app import create_app
from recordings_ui.settings import from_env


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="recordings-ui")
    ap.add_argument("--demo", action="store_true", help="serve a fresh copy of the demo archive")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)
    settings = from_env(os.environ, demo=True if args.demo else None)
    uvicorn.run(create_app(settings), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/ui -v`
Expected: all pass. `test_the_page_and_the_fonts_are_served` is SKIPPED until Task 12
builds the frontend.

- [ ] **Step 5: Commit**

```bash
git add packages/ui
git commit -m "feat(ui): FastAPI app with ranged media streaming and the shinyreact server

Checked against the shinyreact-build-app skill: ReactApp discovery from the calling
module, reactive_output in a Core server, and local_server for server tests.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Frontend scaffold (shadcn/ui + Tailwind), brand theme and fonts

**Check the docs before writing anything in this task** (CLAUDE.md, "check the docs"):
- **shinyreact:** load `/shinyreact-build-app`. Step 1 covers the Vite tier and its
  `external`/`globals` config, and Step 4 the hooks.
- **shadcn:** run `npx skills add shadcn/ui` and load the shadcn skill. Read
  https://ui.shadcn.com/docs/installation/vite and https://ui.shadcn.com/docs/theming.
- **brand.yml:** read https://posit-dev.github.io/brand-yml/ and the light/dark section of
  https://quarto.org/docs/authoring/brand.html.

Record what you checked in the commit message.

**Files:**
- Create: `_brand.yml`, `scripts/brand_css.py`, `packages/core/tests/test_brand.py`
- Create: `packages/ui/frontend/.nvmrc`, `package.json`, `vite.config.js`, `tsconfig.json`, `scripts/check-node.mjs`, `src/index.css`
- Create, by the shadcn CLI: `packages/ui/frontend/components.json`, `src/lib/utils.ts`, `src/components/ui/tabs.tsx`, `src/components/ui/toggle-group.tsx`, `src/components/ui/toggle.tsx`
- Create: `packages/ui/frontend/src/ui.tsx`, `App.tsx`, `sr.ts`, `types.ts`, `theme.css` (generated), `app.css`
- Create: `packages/ui/frontend/src/lib/theme.ts`, `lib/theme.test.ts`
- Create: `packages/ui/frontend/src/components/TopBar.tsx`, `ThemeSwitch.tsx`
- Create: `packages/ui/src/recordings_ui/www/fonts/` (copied woff2 + OFL)

**Interfaces:**
- Consumes: the Shiny contract from Task 11; the JSON shapes from Task 10.
- Produces:
  - **Hooks:** `sr.ts` exports `useShinyInput`, `useShinyOutputValue`,
    `useShinyOutputStatus` and `useShinyInitialized`, typed from `@posit-dev/shinyreact`
    and read from `window.shinyreact`.
  - **Types:** `types.ts` exports `LibraryView`, `LibraryRow`, `RecordingView`,
    `TranscriptView`, `Turn`, `Word`, `NotesGroup`, `NotesOutput`, `TagRef` and `Filter`.
  - **Theme helpers:** `lib/theme.ts` exports `ThemeChoice`,
    `resolveTheme(choice, prefersDark)`, `loadChoice(storage)`, `saveChoice(storage, choice)`
    and `applyTheme(root, resolved)`. Applying a theme sets the class `dark` or `light` on
    `<html>`, as shadcn's dark-mode convention expects.
  - **shadcn components:** `@/components/ui/tabs` (`Tabs`, `TabsList`, `TabsTrigger`,
    `TabsContent`) and `@/components/ui/toggle-group` (`ToggleGroup`, `ToggleGroupItem`),
    built on Radix.
  - **CSS custom properties:** shadcn's tokens (`--background --foreground --card --muted
    --muted-foreground --primary --primary-foreground --accent --border --ring --sidebar
    --destructive` and so on), plus `--brand-orange` and `--brand-success`, all generated
    from `_brand.yml`.

- [ ] **Step 1: Write `_brand.yml`**

There is one file, with light and dark together: any colour can be `{light: …, dark: …}`.
Quarto's brand docs describe this, and Posit's brand-yml skill dates it to Quarto 1.8.
```yaml
# recordings' theme. NYC blue is the working colour and NYC orange a small accent, on warm
# neutrals from chendaniely.github.io/_brand.yml. Every colour that differs between modes
# is {light, dark}. scripts/brand_css.py turns this into shadcn/ui's CSS variables.
meta:
  name: recordings
color:
  palette:
    nyc-blue: "#236192"
    nyc-blue-light: "#6CA6D9"   # #236192 is only 2.8:1 on the dark base
    nyc-orange: "#F26522"
    warm-white: "#F8F6F2"
    paper: "#FFFDFA"
    linen: "#EFE8DF"
    sand: "#E8DDD2"
    hairline: "#E0D6CB"
    warm-gray: "#6B6258"
    near-black: "#1C1A17"
    green-text: "#4E7A2E"
    burgundy: "#9A4665"
    dark-base: "#171512"
    dark-surface: "#232019"
    dark-side: "#1C1A16"
    dark-hairline: "#2E2B26"
    stone: "#B8AEA2"
    green-light: "#9CC27A"
    rose: "#D27A9A"
  foreground: { light: near-black, dark: warm-white }
  background: { light: warm-white, dark: dark-base }
  primary: { light: nyc-blue, dark: nyc-blue-light }
  secondary: { light: warm-gray, dark: stone }
  tertiary: { light: linen, dark: dark-side }
  success: { light: green-text, dark: green-light }
  warning: nyc-orange
  danger: { light: burgundy, dark: rose }
  light: { light: paper, dark: dark-surface }
  dark: { light: near-black, dark: warm-white }
typography:
  base: Atkinson Hyperlegible
  monospace: Atkinson Hyperlegible Mono
```

- [ ] **Step 2: Write the failing brand tests**

`packages/core/tests/test_brand.py`:
```python
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def brand_css():
    spec = importlib.util.spec_from_file_location("brand_css", REPO / "scripts" / "brand_css.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_theme_css_is_up_to_date():
    committed = (REPO / "packages/ui/frontend/src/theme.css").read_text(encoding="utf-8")
    assert committed == brand_css().render(REPO), "theme.css is stale: run `make brand`"


def contrast(a: str, b: str) -> float:
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted([lum(a), lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_text_colours_meet_wcag_aa_in_both_modes():
    module = brand_css()
    for mode in ("light", "dark"):
        t = module.tokens(REPO, mode)
        for fg in ("foreground", "muted-foreground", "primary"):
            for bg in ("background", "card", "muted", "sidebar"):
                assert contrast(t[fg], t[bg]) >= 4.5, f"{mode}: {fg} on {bg}"
        assert contrast(t["primary-foreground"], t["primary"]) >= 4.5, f"{mode}: button text"


def test_orange_is_never_a_light_mode_text_colour():
    # why: 3.2:1 on white, so markers and outlines only
    assert contrast(brand_css().tokens(REPO, "light")["brand-orange"], "#FFFFFF") < 4.5
```

- [ ] **Step 3: Write `scripts/brand_css.py` and generate**

```python
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""Generate packages/ui/frontend/src/theme.css (shadcn/ui CSS variables) from _brand.yml.

    uv run scripts/brand_css.py

_brand.yml uses brand.yml's {light, dark} colour objects; this reads them directly, so the
R/Python brand_yml packages (which don't read that form) are not involved.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "packages" / "ui" / "frontend" / "src" / "theme.css"

# Roles brand.yml has no theme key for: a palette name per mode.
EXTRA = {
    "chip": {"light": "sand", "dark": "dark-hairline"},
    "hairline": {"light": "hairline", "dark": "dark-hairline"},
}
# shadcn token -> brand.yml theme key, or extra:<role>
TOKENS = {
    "background": "background", "foreground": "foreground",
    "card": "light", "card-foreground": "foreground",
    "popover": "light", "popover-foreground": "foreground",
    "primary": "primary", "primary-foreground": "background",
    "secondary": "extra:chip", "secondary-foreground": "foreground",
    "muted": "tertiary", "muted-foreground": "secondary",
    "accent": "extra:chip", "accent-foreground": "foreground",
    "destructive": "danger",
    "border": "extra:hairline", "input": "extra:hairline", "ring": "primary",
    "sidebar": "tertiary", "sidebar-foreground": "foreground",
    "sidebar-primary": "primary", "sidebar-primary-foreground": "background",
    "sidebar-accent": "extra:chip", "sidebar-accent-foreground": "foreground",
    "sidebar-border": "extra:hairline", "sidebar-ring": "primary",
    "brand-orange": "warning", "brand-success": "success",
}


def _resolve(palette: dict, value, mode: str) -> str:
    if isinstance(value, dict):
        value = value[mode]
    seen = set()
    while value in palette and value not in seen:  # palette entries may alias each other
        seen.add(value)
        value = palette[value]
    return value


def tokens(repo: Path, mode: str) -> dict[str, str]:
    color = yaml.safe_load((repo / "_brand.yml").read_text(encoding="utf-8"))["color"]
    out = {}
    for name, ref in TOKENS.items():
        value = EXTRA[ref.removeprefix("extra:")][mode] if ref.startswith("extra:") else color[ref]
        out[name] = _resolve(color["palette"], value, mode)
    return out


def _block(selector: str, values: dict[str, str], scheme: str, indent: str = "") -> str:
    lines = [f"{indent}{selector} {{", f"{indent}  color-scheme: {scheme};", f"{indent}  --radius: 0.5rem;"]
    lines += [f"{indent}  --{k}: {v};" for k, v in values.items()]
    return "\n".join(lines + [f"{indent}}}"])


def render(repo: Path) -> str:
    light, dark = tokens(repo, "light"), tokens(repo, "dark")
    return "\n".join([
        "/* Generated by scripts/brand_css.py from _brand.yml. Do not edit. */",
        _block(":root", light, "light"),
        _block(".dark", dark, "dark"),
        "/* Before the app picks a mode (class light/dark on <html>), follow the OS. */",
        "@media (prefers-color-scheme: dark) {",
        _block(":root:not(.light):not(.dark)", dark, "dark", indent="  "),
        "}",
        "",
    ])


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(REPO), encoding="utf-8")
    print(f"wrote {OUT}", file=sys.stderr)
```

Run: `make brand && uv run pytest packages/core/tests/test_brand.py -v`
Expected: `wrote …/theme.css`, then 3 passed.

- [ ] **Step 4: Copy the self-hosted fonts**

```bash
mkdir -p packages/ui/src/recordings_ui/www/fonts
cp ../chendaniely.github.io/fonts/atkinson-hyperlegible/*.woff2 \
   ../chendaniely.github.io/fonts/atkinson-hyperlegible-mono/*.woff2 \
   packages/ui/src/recordings_ui/www/fonts/
cp ../chendaniely.github.io/fonts/atkinson-hyperlegible/OFL.txt \
   packages/ui/src/recordings_ui/www/fonts/OFL.txt
ls packages/ui/src/recordings_ui/www/fonts
```
Expected: six `.woff2` files and `OFL.txt`.

- [ ] **Step 5: Write the frontend tooling**

`packages/ui/frontend/.nvmrc`:
```
22
```

`packages/ui/frontend/scripts/check-node.mjs`:
```js
// Fail fast on the wrong Node: shinyreact builds and tests its JS with Node 22 (pkg-js/.nvmrc).
const major = Number(process.versions.node.split(".")[0]);
if (major !== 22) {
  console.error(`Node 22 is required (found ${process.versions.node}). Run: nvm use`);
  process.exit(1);
}
```

`packages/ui/frontend/package.json`. Vite, plugin-react, TypeScript and Vitest match
shinyreact's own examples and `pkg-js`. Tailwind 4.3.3 and `@tailwindcss/vite` 4.3.3
support Vite 5 (their peer range is `^5.2.0 || …`). React is a devDependency only, for
types and the JSX transform, and is never bundled. The shadcn CLI adds its own runtime
dependencies in Step 6.
```json
{
  "name": "recordings-ui-frontend",
  "private": true,
  "type": "module",
  "engines": { "node": "22.x" },
  "scripts": {
    "preinstall": "node scripts/check-node.mjs",
    "prebuild": "node scripts/check-node.mjs",
    "build": "vite build",
    "dev": "vite build --watch",
    "typecheck": "tsc --noEmit",
    "test": "vitest run"
  },
  "devDependencies": {
    "@posit-dev/shinyreact": "0.1.1",
    "@tailwindcss/vite": "4.3.3",
    "@types/node": "22.20.5",
    "@types/react": "19.2.18",
    "@types/react-dom": "19.2.7",
    "@vitejs/plugin-react": "4.7.0",
    "jsdom": "26.1.0",
    "react": "19.2.8",
    "react-dom": "19.2.8",
    "tailwindcss": "4.3.3",
    "typescript": "5.9.3",
    "vite": "5.4.21",
    "vitest": "3.2.7"
  }
}
```

`packages/ui/frontend/vite.config.js` is shinyreact's Vite tier (as in its example 04) plus
the Tailwind plugin, as shadcn's Vite install page shows:
```js
import path from "node:path";
import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// React/ReactDOM are externalized and read from window.shinyreact at runtime, so this bundle
// (shadcn's components included) shares the React instance that owns the shinyreact hooks.
// Two copies = hooks silently empty.
export default defineConfig({
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  build: {
    // Next to shiny_app.py, where ReactApp discovers www/ui.js and www/ui.css.
    outDir: path.resolve(__dirname, "../src/recordings_ui/www"),
    emptyOutDir: false, // www/ also holds the committed fonts
    cssCodeSplit: false,
    lib: {
      entry: path.resolve(__dirname, "src/ui.tsx"),
      formats: ["iife"],
      name: "RecordingsUI",
      fileName: () => "ui.js",
    },
    rollupOptions: {
      external: ["react", "react-dom", "react-dom/client"],
      output: {
        assetFileNames: "ui.[ext]",
        globals: {
          react: "window.shinyreact.React",
          "react-dom": "window.shinyreact.ReactDOM",
          "react-dom/client": "window.shinyreact.ReactDOM",
        },
      },
    },
  },
  test: { environment: "jsdom", include: ["src/**/*.test.ts"] },
});
```

`packages/ui/frontend/tsconfig.json`. This uses a single config with the `@/*` alias that
shadcn's CLI looks for:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "baseUrl": ".",
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["src"]
}
```

`packages/ui/frontend/src/index.css` (the file shadcn's CLI writes its theme scaffold
into):
```css
@import "tailwindcss";
```

Run: `cd packages/ui/frontend && nvm use && npm install && cd -`
Expected: `package-lock.json` is created, and the preinstall check passes on Node 22.

- [ ] **Step 6: Initialise shadcn and add the components**

The CLI version is pinned so a re-run gives the same result. `--base radix` is
deliberate: shadcn made Base UI the default on 2026-07-02, but says Radix is "still fully
supported", and Radix matches shinyreact's shadcn examples and the props this plan uses
(`type="single"`, `data-state`).

```bash
cd packages/ui/frontend
npx shadcn@4.21.4 init --template vite --base radix --css-variables --no-rtl --no-monorepo
npx shadcn@4.21.4 add tabs toggle-group
npx shadcn@4.21.4 info --json
cd -
```
If `init` asks for a preset or base colour, take the default. `theme.css` overrides every
colour token after it.

Expected:
- **`info` reports** Tailwind v4, base `radix`, CSS variables `true` and the aliases
  `@/components` and `@/lib/utils`.
- **New files:**
  - `components.json`
  - `src/lib/utils.ts` (exports `cn`)
  - `src/components/ui/tabs.tsx`, `toggle-group.tsx` and `toggle.tsx`
- **`src/index.css`** gains shadcn's scaffold: `@import "tailwindcss";`, its theme import,
  `@custom-variant dark (&:is(.dark *));`, `@theme inline { … }`, default `:root`/`.dark`
  tokens and a `@layer base`.
- **`package.json`** gains shadcn's runtime dependencies (`radix-ui`,
  `class-variance-authority`, `clsx`, `tailwind-merge`, `lucide-react` and its CSS
  packages).

Check with `npm ls react`. It must list `react` only as this project's devDependency and as
peers. If any package depends on a second `react` copy, stop and fix that first: the
`external`/`globals` config keeps React out of the bundle, but a mismatched peer would
still show up here.

- [ ] **Step 7: Write the failing theme test**

`packages/ui/frontend/src/lib/theme.test.ts`:
```ts
import { describe, expect, it } from "vitest";

import { applyTheme, loadChoice, resolveTheme, saveChoice } from "./theme";

describe("theme", () => {
  it("follows the OS only in system mode", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("remembers a choice and ignores junk", () => {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    expect(loadChoice(storage)).toBe("system");
    saveChoice(storage, "dark");
    expect(loadChoice(storage)).toBe("dark");
    store.set("recordings.theme", "purple");
    expect(loadChoice(storage)).toBe("system");
  });

  it("survives storage that throws (private windows, blocked site data)", () => {
    const broken = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); } };
    expect(loadChoice(broken)).toBe("system");
    expect(() => saveChoice(broken, "light")).not.toThrow();
  });

  it("sets exactly one of the classes dark/light on <html>", () => {
    const root = document.documentElement;
    applyTheme(root, "dark");
    expect(root.classList.contains("dark")).toBe(true);
    expect(root.classList.contains("light")).toBe(false);
    applyTheme(root, "light");
    expect(root.classList.contains("light")).toBe(true);
    expect(root.classList.contains("dark")).toBe(false);
  });
});
```

Run: `cd packages/ui/frontend && npm test`
Expected: FAIL with `Failed to resolve import "./theme"`.

- [ ] **Step 8: Implement the hooks, types, theme and shell**

`packages/ui/frontend/src/lib/theme.ts`:
```ts
export type ThemeChoice = "light" | "dark" | "system";
export type Resolved = "light" | "dark";

const KEY = "recordings.theme";
type Storage = { getItem(k: string): string | null; setItem(k: string, v: string): void };

export function resolveTheme(choice: ThemeChoice, prefersDark: boolean): Resolved {
  return choice === "system" ? (prefersDark ? "dark" : "light") : choice;
}

export function loadChoice(storage: Storage | null): ThemeChoice {
  try {
    const v = storage?.getItem(KEY);
    return v === "light" || v === "dark" || v === "system" ? v : "system";
  } catch {
    return "system"; // storage can throw in private windows; the theme must still work
  }
}

export function saveChoice(storage: Storage | null, choice: ThemeChoice): void {
  try {
    storage?.setItem(KEY, choice);
  } catch {
    /* a per-browser convenience only; nothing else depends on it */
  }
}

/** shadcn's convention: the `dark` class on <html>. `light` is set too, so theme.css can tell
 * "the user chose light" from "nothing chosen yet, follow the OS". */
export function applyTheme(root: HTMLElement, resolved: Resolved): void {
  root.classList.toggle("dark", resolved === "dark");
  root.classList.toggle("light", resolved === "light");
}
```

`packages/ui/frontend/src/sr.ts`:
```ts
// shinyreact hooks for an app: read from the window.shinyreact global the server loads
// (shinyreact-build-app skill, Step 1). The npm package is imported for its TYPES only;
// `import type` is erased, so no second copy of React or the hooks can enter the bundle.
import type * as SR from "@posit-dev/shinyreact";

type Hooks = Pick<
  typeof SR,
  "useShinyInput" | "useShinyOutputValue" | "useShinyOutputStatus" | "useShinyInitialized"
>;

const shinyreact = (window as unknown as { shinyreact: Hooks }).shinyreact;

export const { useShinyInput, useShinyOutputValue, useShinyOutputStatus, useShinyInitialized } =
  shinyreact;
```

`packages/ui/frontend/src/types.ts`. This mirrors `recordings_ui/views.py`. Change both
together:
```ts
export interface TagRef { tag: string; by: "you" | "auto" }

export interface LibraryRow {
  id: string; title: string; recorded_at: string; when: string; duration: string | null;
  kind: "audio" | "video"; sources: string[]; tags: TagRef[]; private: boolean; untagged: boolean;
}

export interface LibraryView {
  recordings: LibraryRow[];
  counts: { all: number; untagged: number };
  tags: { tag: string; count: number; private: boolean }[];
  note_types: { note_type: string; count: number }[];
  problems: { path: string; message: string }[];
}

export interface Word { word: string; start: number; end: number }
export interface Turn { start: number; end: number; speaker: string | null; text: string; words: Word[] }

export interface TranscriptView {
  rendition: string; label: string; engine: string; model: string | null; created_at: string; turns: Turn[];
}

export interface NotesOutput { rendition: string; model: string | null; engine: string; created_at: string; html: string }
export interface NotesGroup { note_type: string; outputs: NotesOutput[] }

export interface RecordingView {
  id: string; title: string; recorded_at: string; when: string; timezone: string;
  duration: string | null; kind: "audio" | "video"; private: boolean; media_url: string;
  tags: TagRef[]; sources: { kind: string; ref: string; added_at: string }[];
  transcripts: TranscriptView[]; chosen_transcript: string | null;
  notes: NotesGroup[]; plaud_notes: { rendition: string; created_at: string; html: string }[];
  my_notes_html: string | null;
  renditions: { rendition: string; kind: string; note_type: string | null; engine: string; model: string | null; created_at: string }[];
}

export type Filter = { kind: "all" } | { kind: "untagged" } | { kind: "tag"; tag: string };
```

`packages/ui/frontend/src/components/ThemeSwitch.tsx` (shadcn's ToggleGroup, built on
Radix):
```tsx
import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { applyTheme, loadChoice, resolveTheme, saveChoice, type ThemeChoice } from "@/lib/theme";

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function ThemeSwitch() {
  const [choice, setChoice] = useState<ThemeChoice>(() => loadChoice(storage()));

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const sync = () => applyTheme(document.documentElement, resolveTheme(choice, media.matches));
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, [choice]);

  return (
    <ToggleGroup
      type="single"
      size="sm"
      variant="outline"
      className="ml-auto"
      value={choice}
      aria-label="Colour theme"
      onValueChange={(v) => {
        if (!v) return; // Radix sends "" when the active item is clicked again
        setChoice(v as ThemeChoice);
        saveChoice(storage(), v as ThemeChoice);
      }}
    >
      <ToggleGroupItem value="light" aria-label="Light" data-testid="theme-light"><Sun /></ToggleGroupItem>
      <ToggleGroupItem value="system" aria-label="System" data-testid="theme-system"><Monitor /></ToggleGroupItem>
      <ToggleGroupItem value="dark" aria-label="Dark" data-testid="theme-dark"><Moon /></ToggleGroupItem>
    </ToggleGroup>
  );
}
```

`packages/ui/frontend/src/components/TopBar.tsx`:
```tsx
import { ThemeSwitch } from "./ThemeSwitch";

export function TopBar() {
  return (
    <header className="topbar">
      <span className="logo"><i aria-hidden />Recordings</span>
      <nav><span className="nav on">Library</span></nav>
      <ThemeSwitch />
    </header>
  );
}
```

`packages/ui/frontend/src/App.tsx` (the shell; Task 13 fills in the panes):
```tsx
import { TopBar } from "./components/TopBar";
import { useShinyInitialized, useShinyOutputValue } from "./sr";
import type { LibraryView } from "./types";

export default function App() {
  const initialized = useShinyInitialized();
  const library = useShinyOutputValue<LibraryView>("library");
  if (!initialized) return null; // no flash of empty defaults during connection
  return (
    <div className="app">
      <TopBar />
      <main className="panes">
        {library ? <p className="muted">{library.counts.all} recordings</p> : <p className="muted">Loading…</p>}
      </main>
    </div>
  );
}
```

`packages/ui/frontend/src/ui.tsx` is the entry point from the skill. The page has no mount
container, so the app creates its own. The CSS order matters: shadcn's scaffold comes
first, then our brand tokens override its defaults, then our layout.
```tsx
import "@/index.css";
import "@/theme.css";
import "@/app.css";

import App from "@/App";

const { ReactDOM } = (window as unknown as { shinyreact: { ReactDOM: typeof import("react-dom/client") } }).shinyreact;

ReactDOM.createRoot(document.body.appendChild(document.createElement("div"))).render(<App />);
```

`packages/ui/frontend/src/app.css` holds the layout and our own components. Colours come
only from shadcn's tokens and the two `--brand-*` tokens. Tailwind's preflight resets
lists and headings, so notes Markdown gets its typography back here:
```css
@font-face { font-family: "Atkinson Hyperlegible"; src: url("/fonts/AtkinsonHyperlegible-Regular.woff2") format("woff2"); font-weight: 400; font-style: normal; font-display: swap; }
@font-face { font-family: "Atkinson Hyperlegible"; src: url("/fonts/AtkinsonHyperlegible-Italic.woff2") format("woff2"); font-weight: 400; font-style: italic; font-display: swap; }
@font-face { font-family: "Atkinson Hyperlegible"; src: url("/fonts/AtkinsonHyperlegible-Bold.woff2") format("woff2"); font-weight: 700; font-style: normal; font-display: swap; }
@font-face { font-family: "Atkinson Hyperlegible"; src: url("/fonts/AtkinsonHyperlegible-BoldItalic.woff2") format("woff2"); font-weight: 700; font-style: italic; font-display: swap; }
@font-face { font-family: "Atkinson Hyperlegible Mono"; src: url("/fonts/AtkinsonHyperlegibleMono-Regular.woff2") format("woff2"); font-weight: 400; font-display: swap; }
@font-face { font-family: "Atkinson Hyperlegible Mono"; src: url("/fonts/AtkinsonHyperlegibleMono-Bold.woff2") format("woff2"); font-weight: 700; font-display: swap; }

:root {
  --sel: color-mix(in srgb, var(--primary) 12%, transparent);
  --orange-tint: color-mix(in srgb, var(--brand-orange) 12%, transparent);
  --mark: color-mix(in srgb, var(--brand-orange) 32%, transparent);
  --success-tint: color-mix(in srgb, var(--brand-success) 16%, transparent);
}
html, body { margin: 0; height: 100%; }
body { background: var(--background); color: var(--foreground); font-family: "Atkinson Hyperlegible", system-ui, sans-serif; font-size: 14px; line-height: 1.45; }
.mono, code { font-family: "Atkinson Hyperlegible Mono", ui-monospace, monospace; }
.muted { color: var(--muted-foreground); }
:focus-visible { outline: 2px solid var(--ring); outline-offset: 2px; }

.app { display: flex; flex-direction: column; height: 100vh; }
.topbar { display: flex; align-items: center; gap: 16px; padding: 8px 16px; background: var(--card); border-bottom: 1px solid var(--border); }
.logo { font-weight: 700; display: flex; align-items: center; gap: 6px; }
.logo i { width: 10px; height: 10px; border-radius: 50%; background: var(--brand-orange); box-shadow: 4px 0 0 0 var(--primary); margin-right: 4px; }
.nav { color: var(--muted-foreground); padding-bottom: 2px; border-bottom: 2px solid transparent; }
.nav.on { color: var(--foreground); border-bottom-color: var(--brand-orange); font-weight: 700; }

.panes { flex: 1; min-height: 0; display: grid; grid-template-columns: 220px 340px 1fr; }
.sidebar { background: var(--sidebar); border-right: 1px solid var(--border); padding: 10px 8px; overflow: auto; }
.grp { font-size: 10.5px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted-foreground); margin: 14px 8px 4px; }
.si { display: flex; justify-content: space-between; width: 100%; text-align: left; padding: 5px 8px; border-radius: 6px; cursor: pointer; }
.si .n { color: var(--muted-foreground); font-variant-numeric: tabular-nums; }
.si.on { background: var(--sel); color: var(--primary); font-weight: 700; }
.si.on .n { color: var(--primary); }
.problems { margin: 8px; padding: 8px; border-radius: 8px; border: 1px solid var(--destructive); color: var(--destructive); font-size: 12.5px; }

.list { border-right: 1px solid var(--border); overflow: auto; background: var(--card); }
.list-head { padding: 9px 12px; color: var(--muted-foreground); border-bottom: 1px solid var(--border); }
.row { display: block; width: 100%; text-align: left; border-bottom: 1px solid var(--border); padding: 10px 12px; cursor: pointer; }
.row:hover { background: var(--background); }
.row.on { background: var(--sel); box-shadow: inset 3px 0 0 var(--primary); }
.row .t { font-weight: 700; display: flex; justify-content: space-between; gap: 8px; }
.row .m { color: var(--muted-foreground); font-size: 12px; margin: 2px 0 5px; }
.chip { display: inline-block; font-size: 11px; padding: 0 8px; border-radius: 999px; background: var(--accent); margin: 0 4px 4px 0; }
.chip.auto { background: transparent; border: 1px dashed var(--border); color: var(--muted-foreground); }
.chip.nt { background: var(--success-tint); color: var(--brand-success); font-weight: 700; }
.lock { color: var(--muted-foreground); display: inline; }

.pane { display: flex; flex-direction: column; min-width: 0; min-height: 0; background: var(--card); }
.empty { margin: auto; color: var(--muted-foreground); }
.head { padding: 14px 18px 6px; }
.head h1 { margin: 0 0 4px; font-size: 19px; font-weight: 700; }
.head .meta { color: var(--muted-foreground); font-size: 12.5px; }
.player { margin: 6px 18px 0; }
.player audio, .player video { width: 100%; max-height: 38vh; background: var(--background); border-radius: 8px; }
.tabs-list { margin: 8px 18px 0; }
.tab { flex: 1; min-height: 0; overflow: auto; padding: 12px 18px 24px; }
.tools { display: flex; gap: 10px; align-items: center; color: var(--muted-foreground); font-size: 12.5px; margin-bottom: 10px; flex-wrap: wrap; }
.tools .r { margin-left: auto; display: flex; gap: 6px; align-items: center; }
select { font: inherit; color: var(--foreground); background: var(--card); border: 1px solid var(--input); border-radius: 6px; padding: 2px 6px; }

.turn { display: grid; grid-template-columns: 56px 1fr; gap: 10px; padding: 6px 8px; border-radius: 8px; cursor: pointer; width: 100%; text-align: left; }
.turn:hover { background: var(--background); }
.turn .ts { color: var(--primary); font-variant-numeric: tabular-nums; }
.turn .sp { font-weight: 700; font-size: 12.5px; margin-bottom: 2px; }
.turn.now { background: var(--orange-tint); box-shadow: inset 3px 0 0 var(--brand-orange); }
.turn.now mark { background: var(--mark); color: inherit; border-radius: 3px; }

.notes-split { display: grid; grid-template-columns: 200px 1fr; gap: 14px; }
.notes-list { border-right: 1px solid var(--border); padding-right: 8px; }
.nl-h { font-weight: 700; padding: 6px 6px 2px; }
.nl-i { display: flex; justify-content: space-between; width: 100%; padding: 4px 6px 4px 14px; border-radius: 6px; cursor: pointer; color: var(--muted-foreground); text-align: left; }
.nl-i.on { background: var(--sel); color: var(--primary); font-weight: 700; }
.compare { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.compare > section { border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; }
.foot { color: var(--muted-foreground); font-size: 11.5px; margin-top: 12px; }
.md h1 { font-size: 17px; font-weight: 700; margin: 10px 0 6px; }
.md h2 { font-size: 15px; font-weight: 700; margin: 12px 0 4px; }
.md h3 { font-size: 14px; font-weight: 700; margin: 10px 0 4px; }
.md p { margin: 6px 0; }
.md ul { list-style: disc; padding-left: 1.4em; margin: 6px 0; }
.md ol { list-style: decimal; padding-left: 1.4em; margin: 6px 0; }
.md a { color: var(--primary); text-decoration: underline; }
table.details { border-collapse: collapse; width: 100%; font-size: 12.5px; }
table.details td, table.details th { text-align: left; border-bottom: 1px solid var(--border); padding: 5px 8px; }
h3 { font-weight: 700; margin: 12px 0 6px; }

@media (max-width: 760px) {
  .panes { grid-template-columns: 1fr; grid-auto-rows: auto; overflow: auto; }
  .sidebar, .list { border-right: 0; border-bottom: 1px solid var(--border); max-height: 40vh; }
  .notes-split, .compare { grid-template-columns: 1fr; }
}
```

- [ ] **Step 9: Run the checks, then build**

Run: `make test-js && make build && ls packages/ui/src/recordings_ui/www`
Expected:
- the typecheck is clean, and Vitest shows 4 passed
- `www/` lists `ui.js`, `ui.css` and `fonts/`
- `grep -c "createElement(\"div\"" packages/ui/src/recordings_ui/www/ui.js` prints at least
  1
- `grep -c "react.production" packages/ui/src/recordings_ui/www/ui.js` prints **0**, which
  proves no React was bundled

Then run `uv run pytest packages/ui/tests/test_app.py -v`. Everything passes, and
`test_the_page_and_the_fonts_are_served` now runs and passes.

- [ ] **Step 10: Commit**

```bash
git add _brand.yml scripts packages/core/tests/test_brand.py packages/ui/frontend packages/ui/src/recordings_ui/www/fonts
git commit -m "feat(ui): React client on shadcn/ui + Tailwind with the brand theme and self-hosted fonts

Checked: shinyreact-build-app skill (Vite tier, externals, hooks); shadcn skill and
ui.shadcn.com Vite install + theming; brand-yml docs and Quarto's light/dark brand syntax.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Library UI: sidebar, list, player and synchronized transcript

Load `/shinyreact-build-app` again for this task's hooks, and the shadcn skill for `Tabs`: `useShinyInput` for
`selected_id`, and the `useShinyOutputStatus` loading-versus-recalculating pattern.

**Files:**
- Create: `packages/ui/frontend/src/lib/transcript.ts`, `lib/transcript.test.ts`
- Create: `packages/ui/frontend/src/components/Sidebar.tsx`, `RecordingList.tsx`, `RecordingPane.tsx`, `Player.tsx`, `TranscriptTab.tsx`
- Modify: `packages/ui/frontend/src/App.tsx`

**Interfaces:**
- Consumes: `sr.ts` and `types.ts` (Task 12); outputs `library` and `recording`, and input
  `selected_id` (Task 11).
- Produces:
  - `activeIndex(turns, t)`, `activeWord(turn, t)`, `formatClock(seconds)` and
    `filterRows(rows, filter)`
  - **Components:** `<Sidebar>`, `<RecordingList>`, `<RecordingPane>` (which renders the
    tabs; Task 14 adds the other tabs), `<Player>` and `<TranscriptTab>`
  - **`data-testid` hooks for E2E:** `recording-row`, `filter-all`, `filter-untagged`,
    `filter-tag`, `media`, `turn`, `follow`, `transcript-select`, `lock`

- [ ] **Step 1: Write the failing logic tests**

`packages/ui/frontend/src/lib/transcript.test.ts`:
```ts
import { describe, expect, it } from "vitest";

import type { LibraryRow, Turn } from "../types";
import { activeIndex, activeWord, filterRows, formatClock } from "./transcript";

const turns: Turn[] = [
  { start: 0, end: 2, speaker: "A", text: "one", words: [{ word: "one", start: 0, end: 0.5 }] },
  { start: 2.5, end: 5, speaker: "B", text: "two three", words: [{ word: "two", start: 2.5, end: 3 }, { word: "three", start: 3.2, end: 4 }] },
  { start: 6, end: 8, speaker: "B", text: "four", words: [] },
];

describe("activeIndex", () => {
  it("is -1 before the first turn and with no turns", () => {
    expect(activeIndex([], 3)).toBe(-1);
    expect(activeIndex([{ ...turns[0], start: 1 }], 0.5)).toBe(-1);
  });
  it("keeps the last started turn through gaps", () => {
    expect(activeIndex(turns, 0)).toBe(0);
    expect(activeIndex(turns, 2.2)).toBe(0); // gap after turn 0
    expect(activeIndex(turns, 2.5)).toBe(1);
    expect(activeIndex(turns, 99)).toBe(2);
  });
});

describe("activeWord", () => {
  it("marks the word being spoken, or none between words", () => {
    expect(activeWord(turns[1], 2.7)).toBe(0);
    expect(activeWord(turns[1], 3.1)).toBe(-1);
    expect(activeWord(turns[1], 3.5)).toBe(1);
  });
});

describe("formatClock", () => {
  it("formats minutes and hours", () => {
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(65.4)).toBe("1:05");
    expect(formatClock(3725)).toBe("1:02:05");
  });
});

describe("filterRows", () => {
  const row = (id: string, tags: string[]): LibraryRow => ({
    id, title: id, recorded_at: "", when: "", duration: null, kind: "audio", sources: [],
    tags: tags.map((tag) => ({ tag, by: "you" as const })), private: false, untagged: tags.length === 0,
  });
  const rows = [row("a", []), row("b", ["school/course-101"]), row("c", ["school"])];
  it("filters by all, untagged and exact tag", () => {
    expect(filterRows(rows, { kind: "all" }).map((r) => r.id)).toEqual(["a", "b", "c"]);
    expect(filterRows(rows, { kind: "untagged" }).map((r) => r.id)).toEqual(["a"]);
    expect(filterRows(rows, { kind: "tag", tag: "school" }).map((r) => r.id)).toEqual(["c"]);
  });
});
```

Run: `cd packages/ui/frontend && npm test`
Expected: FAIL with `Failed to resolve import "./transcript"`.

- [ ] **Step 2: Implement the logic**

`packages/ui/frontend/src/lib/transcript.ts`:
```ts
import type { Filter, LibraryRow, Turn } from "../types";

/** Index of the last turn that has started by time t (binary search); -1 before the first. */
export function activeIndex(turns: Turn[], t: number): number {
  let lo = 0;
  let hi = turns.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (turns[mid].start <= t) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

/** The word being spoken at t, or -1 in the gaps between words. */
export function activeWord(turn: Turn, t: number): number {
  return turn.words.findIndex((w) => w.start <= t && t < w.end);
}

export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export function filterRows(rows: LibraryRow[], filter: Filter): LibraryRow[] {
  if (filter.kind === "untagged") return rows.filter((r) => r.untagged);
  if (filter.kind === "tag") return rows.filter((r) => r.tags.some((t) => t.tag === filter.tag));
  return rows;
}
```

Run: `npm test`
Expected: all pass, 4 from `theme` and 5 from `transcript`.

- [ ] **Step 3: Write the components**

`packages/ui/frontend/src/components/Sidebar.tsx`:
```tsx
import { Lock } from "lucide-react";
import type { ReactNode } from "react";

import type { Filter, LibraryView } from "../types";

type Props = { library: LibraryView; filter: Filter; onFilter: (f: Filter) => void };

const keyOf = (f: Filter) => (f.kind === "tag" ? `tag:${f.tag}` : f.kind);

export function Sidebar({ library, filter, onFilter }: Props) {
  const item = (f: Filter, label: ReactNode, n: number, testid: string) => (
    <button key={keyOf(f)} type="button" className={`si${keyOf(f) === keyOf(filter) ? " on" : ""}`} data-testid={testid} onClick={() => onFilter(f)}>
      <span>{label}</span><span className="n">{n}</span>
    </button>
  );
  const open = library.tags.filter((t) => !t.private);
  const hidden = library.tags.filter((t) => t.private);
  return (
    <nav className="sidebar" aria-label="Filters">
      {item({ kind: "all" }, "All recordings", library.counts.all, "filter-all")}
      {item({ kind: "untagged" }, "Untagged", library.counts.untagged, "filter-untagged")}
      {library.problems.length > 0 && (
        <div className="problems" role="status">
          {library.problems.length} recording file{library.problems.length > 1 ? "s" : ""} couldn't be read. Run <code>recordings validate</code>.
        </div>
      )}
      <div className="grp">Tags</div>
      {open.map((t) => item({ kind: "tag", tag: t.tag }, t.tag, t.count, "filter-tag"))}
      {hidden.length > 0 && <div className="grp">Private</div>}
      {hidden.map((t) => item({ kind: "tag", tag: t.tag }, <><Lock size={12} /> {t.tag}</>, t.count, "filter-tag"))}
      {library.note_types.length > 0 && <div className="grp">Note types</div>}
      {library.note_types.map((n) => item({ kind: "tag", tag: `notes/${n.note_type}` }, n.note_type, n.count, "filter-tag"))}
    </nav>
  );
}
```

`packages/ui/frontend/src/components/RecordingList.tsx`:
```tsx
import { Lock } from "lucide-react";

import type { LibraryRow } from "../types";

type Props = { rows: LibraryRow[]; selectedId: string | null; onSelect: (id: string) => void };

export function RecordingList({ rows, selectedId, onSelect }: Props) {
  return (
    <section className="list" aria-label="Recordings">
      <div className="list-head">{rows.length} recording{rows.length === 1 ? "" : "s"} · newest first</div>
      {rows.map((r) => (
        <button key={r.id} type="button" data-testid="recording-row" className={`row${r.id === selectedId ? " on" : ""}`} onClick={() => onSelect(r.id)}>
          <div className="t"><span>{r.title}</span>{r.private && <Lock size={13} className="lock" aria-label="Private" />}</div>
          <div className="m">{[r.when, r.duration, r.sources.join(", "), r.kind === "video" ? "video" : null].filter(Boolean).join(" · ")}</div>
          {r.tags.map((t) => (
            <span key={t.tag} className={`chip${t.by === "auto" ? " auto" : ""}${t.tag.startsWith("notes/") ? " nt" : ""}`}>{t.tag}{t.by === "auto" ? " · auto" : ""}</span>
          ))}
        </button>
      ))}
    </section>
  );
}
```

`packages/ui/frontend/src/components/Player.tsx`:
```tsx
import type { RefObject, SyntheticEvent } from "react";

type Props = { url: string; kind: "audio" | "video"; mediaRef: RefObject<HTMLMediaElement | null>; onTime: (t: number) => void };

export function Player({ url, kind, mediaRef, onTime }: Props) {
  const common = {
    src: url,
    controls: true,
    preload: "metadata" as const,
    "data-testid": "media",
    onTimeUpdate: (e: SyntheticEvent<HTMLMediaElement>) => onTime(e.currentTarget.currentTime),
    onSeeked: (e: SyntheticEvent<HTMLMediaElement>) => onTime(e.currentTarget.currentTime),
  };
  return (
    <div className="player">
      {kind === "video"
        ? <video ref={mediaRef as RefObject<HTMLVideoElement>} {...common} playsInline />
        : <audio ref={mediaRef as RefObject<HTMLAudioElement>} {...common} />}
    </div>
  );
}
```

`packages/ui/frontend/src/components/TranscriptTab.tsx`:
```tsx
import { useEffect, useMemo, useRef, useState } from "react";

import { activeIndex, activeWord, formatClock } from "../lib/transcript";
import type { TranscriptView } from "../types";

type Props = { transcripts: TranscriptView[]; chosen: string | null; time: number; onSeek: (t: number) => void };

export function TranscriptTab({ transcripts, chosen, time, onSeek }: Props) {
  const [which, setWhich] = useState<string | null>(chosen);
  const [follow, setFollow] = useState(true);
  useEffect(() => setWhich(chosen), [chosen]);
  const transcript = transcripts.find((t) => t.rendition === which) ?? transcripts[0];
  const turns = transcript?.turns ?? [];
  const now = useMemo(() => activeIndex(turns, time), [turns, time]);
  const nowRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (follow) nowRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [now, follow]);

  if (!transcript) return <p className="muted">No transcript yet. Transcription starts once the recording has a tag.</p>;
  return (
    <>
      <div className="tools">
        {transcripts.length > 1 ? (
          <select data-testid="transcript-select" value={transcript.rendition} onChange={(e) => setWhich(e.target.value)} aria-label="Transcript">
            {transcripts.map((t) => <option key={t.rendition} value={t.rendition}>{t.label}</option>)}
          </select>
        ) : <span>{transcript.label}</span>}
        <label className="r"><input type="checkbox" data-testid="follow" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Follow audio</label>
      </div>
      {turns.map((turn, i) => {
        const showSpeaker = turn.speaker && (i === 0 || turns[i - 1].speaker !== turn.speaker);
        const w = i === now ? activeWord(turn, time) : -1;
        return (
          <button key={`${turn.start}-${i}`} ref={i === now ? nowRef : undefined} type="button" data-testid="turn" data-start={turn.start}
            className={`turn${i === now ? " now" : ""}`} onClick={() => onSeek(turn.start)}>
            <span className="ts">{formatClock(turn.start)}</span>
            <span>
              {showSpeaker && <div className="sp">{turn.speaker}</div>}
              {w >= 0 ? turn.words.map((word, j) => <span key={j}>{j ? " " : ""}{j === w ? <mark>{word.word}</mark> : word.word}</span>) : turn.text}
            </span>
          </button>
        );
      })}
    </>
  );
}
```

`packages/ui/frontend/src/components/RecordingPane.tsx`:
```tsx
import { Lock } from "lucide-react";
import { useRef, useState } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { useShinyOutputStatus, useShinyOutputValue } from "../sr";
import type { RecordingView } from "../types";
import { Player } from "./Player";
import { TranscriptTab } from "./TranscriptTab";

export function RecordingPane({ selectedId }: { selectedId: string | null }) {
  const rec = useShinyOutputValue<RecordingView | null>("recording");
  const status = useShinyOutputStatus("recording");
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const [time, setTime] = useState(0);

  if (!selectedId) return <section className="pane"><p className="empty">Choose a recording.</p></section>;
  if (!rec || rec.id !== selectedId) return <section className="pane"><p className="empty">Loading…</p></section>;

  const seek = (t: number) => {
    if (mediaRef.current) mediaRef.current.currentTime = t;
    setTime(t);
  };
  return (
    <section className="pane" style={{ opacity: status === "recalculating" ? 0.6 : 1 }} key={rec.id}>
      <div className="head">
        <h1>{rec.title} {rec.private && <Lock size={15} className="lock" data-testid="lock" aria-label="Private: Spark only" />}</h1>
        <div className="meta">{[rec.when, rec.duration, rec.sources.map((s) => s.kind).join(", ")].filter(Boolean).join(" · ")} · <span className="mono">{rec.id}</span></div>
        <div>{rec.tags.map((t) => <span key={t.tag} className={`chip${t.by === "auto" ? " auto" : ""}`}>{t.tag}</span>)}</div>
      </div>
      <Player url={rec.media_url} kind={rec.kind} mediaRef={mediaRef} onTime={setTime} />
      <Tabs defaultValue="transcript" className="flex min-h-0 flex-1 flex-col">
        <TabsList className="tabs-list" aria-label="Recording">
          <TabsTrigger value="transcript" data-testid="tab-transcript">Transcript</TabsTrigger>
        </TabsList>
        <TabsContent value="transcript" className="tab">
          <TranscriptTab transcripts={rec.transcripts} chosen={rec.chosen_transcript} time={time} onSeek={seek} />
        </TabsContent>
      </Tabs>
    </section>
  );
}
```

Replace `packages/ui/frontend/src/App.tsx` with:
```tsx
import { useMemo, useState } from "react";

import { RecordingList } from "./components/RecordingList";
import { RecordingPane } from "./components/RecordingPane";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { filterRows } from "./lib/transcript";
import { useShinyInitialized, useShinyInput, useShinyOutputValue } from "./sr";
import type { Filter, LibraryView } from "./types";

export default function App() {
  const initialized = useShinyInitialized();
  const library = useShinyOutputValue<LibraryView>("library");
  const [selectedId, setSelectedId] = useShinyInput<string | null>("selected_id", null);
  const [filter, setFilter] = useState<Filter>({ kind: "all" });
  const rows = useMemo(() => (library ? filterRows(library.recordings, filter) : []), [library, filter]);

  if (!initialized) return null; // no flash of empty defaults during connection
  return (
    <div className="app">
      <TopBar />
      {library ? (
        <main className="panes">
          <Sidebar library={library} filter={filter} onFilter={setFilter} />
          <RecordingList rows={rows} selectedId={selectedId} onSelect={setSelectedId} />
          <RecordingPane selectedId={selectedId} />
        </main>
      ) : <p className="empty">Loading…</p>}
    </div>
  );
}
```

- [ ] **Step 4: Typecheck, test and build**

Run: `make test-js && make build`
Expected: a clean typecheck, all Vitest tests passing, and `ui.js` rebuilt.

- [ ] **Step 5: Check by hand in demo mode**

Run: `make demo`, then open http://127.0.0.1:8000.
Expected:
- The four demo recordings appear newest first, with Apollo 13 on top.
- **Untagged** shows only Apollo 13.
- The Private section shows `private`, and the FDR row has a lock.
- Clicking JFK and pressing play highlights the current line, and the view follows it.
- Clicking a line jumps the audio there.

Stop the server with Ctrl-C.

- [ ] **Step 6: Commit**

```bash
git add packages/ui/frontend
git commit -m "feat(ui): library sidebar, list, player and synchronized transcript

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Notes (layout B), Plaud, My notes and Details tabs

**Files:**
- Create: `packages/ui/frontend/src/components/NotesTab.tsx`, `PlaudTab.tsx`, `MyNotesTab.tsx`, `DetailsTab.tsx`
- Modify: `packages/ui/frontend/src/components/RecordingPane.tsx` (adds four tabs)

**Interfaces:**
- Consumes: `RecordingView` (Task 12).
- Produces: the tab test IDs `tab-notes`, `tab-plaud`, `tab-my-notes` and `tab-details`,
  plus `notes-output`, `compare-select` and `compare-view`.

- [ ] **Step 1: Write the tabs**

`packages/ui/frontend/src/components/NotesTab.tsx`:
```tsx
import { useEffect, useMemo, useState } from "react";

import type { NotesGroup, NotesOutput } from "../types";

const label = (o: NotesOutput) => `${o.model ?? o.engine} · ${o.engine}`;
const when = (iso: string) => new Date(iso).toLocaleString();

function Output({ group, output }: { group: string; output: NotesOutput }) {
  return (
    <section>
      <div className="md" dangerouslySetInnerHTML={{ __html: output.html }} />
      <p className="foot">{group} · {output.model ?? "?"} · {output.engine} · {when(output.created_at)}</p>
    </section>
  );
}

/** Layout B (spec §12.1): one list of every notes output, grouped by note type, newest first. */
export function NotesTab({ groups }: { groups: NotesGroup[] }) {
  const all = useMemo(() => groups.flatMap((g) => g.outputs.map((o) => ({ group: g.note_type, o }))), [groups]);
  const [picked, setPicked] = useState<string | null>(all[0]?.o.rendition ?? null);
  const [other, setOther] = useState<string>("");
  useEffect(() => { setPicked(all[0]?.o.rendition ?? null); setOther(""); }, [all]);

  if (!all.length) return <p className="muted">No notes yet. Notes are written once the recording has a tag.</p>;
  const main = all.find((x) => x.o.rendition === picked) ?? all[0];
  const second = all.find((x) => x.o.rendition === other);
  return (
    <div className="notes-split">
      <div className="notes-list">
        {groups.map((g) => (
          <div key={g.note_type}>
            <div className="nl-h">{g.note_type}</div>
            {g.outputs.map((o) => (
              <button key={o.rendition} type="button" data-testid="notes-output" className={`nl-i${o.rendition === main.o.rendition ? " on" : ""}`} onClick={() => setPicked(o.rendition)}>
                <span>{o.model ?? o.engine}</span>
              </button>
            ))}
          </div>
        ))}
      </div>
      <div>
        <div className="tools">
          <span>{main.group} · {label(main.o)}</span>
          <label className="r">Compare with
            <select data-testid="compare-select" value={other} onChange={(e) => setOther(e.target.value)}>
              <option value="">nothing</option>
              {all.filter((x) => x.o.rendition !== main.o.rendition).map((x) => <option key={x.o.rendition} value={x.o.rendition}>{x.group} · {label(x.o)}</option>)}
            </select>
          </label>
        </div>
        {second ? (
          <div className="compare" data-testid="compare-view">
            <Output group={main.group} output={main.o} />
            <Output group={second.group} output={second.o} />
          </div>
        ) : <Output group={main.group} output={main.o} />}
      </div>
    </div>
  );
}
```

`packages/ui/frontend/src/components/PlaudTab.tsx`:
```tsx
import type { RecordingView } from "../types";

export function PlaudTab({ notes }: { notes: RecordingView["plaud_notes"] }) {
  if (!notes.length) return <p className="muted">Nothing from Plaud for this recording.</p>;
  return (
    <>
      {notes.map((n) => <div key={n.rendition} className="md" dangerouslySetInnerHTML={{ __html: n.html }} />)}
      <p className="foot">Plaud's transcript is in the Transcript tab's selector.</p>
    </>
  );
}
```

`packages/ui/frontend/src/components/MyNotesTab.tsx`:
```tsx
export function MyNotesTab({ html }: { html: string | null }) {
  if (html === null) return <p className="muted">No notes of your own yet. Editing arrives with tagging (stage 3).</p>;
  return <div className="md" dangerouslySetInnerHTML={{ __html: html }} />;
}
```

`packages/ui/frontend/src/components/DetailsTab.tsx`:
```tsx
import type { RecordingView } from "../types";

export function DetailsTab({ rec }: { rec: RecordingView }) {
  return (
    <>
      <h3>Sources</h3>
      <table className="details"><thead><tr><th>Kind</th><th>Reference</th><th>Added</th></tr></thead>
        <tbody>{rec.sources.map((s) => <tr key={s.kind + s.ref}><td>{s.kind}</td><td className="mono">{s.ref}</td><td>{s.added_at}</td></tr>)}</tbody></table>
      <h3>Outputs</h3>
      <table className="details"><thead><tr><th>File</th><th>Kind</th><th>Engine</th><th>Model</th><th>Created</th></tr></thead>
        <tbody>{rec.renditions.map((r) => <tr key={r.rendition}><td className="mono">{r.rendition}</td><td>{r.note_type ? `notes · ${r.note_type}` : r.kind}</td><td>{r.engine}</td><td>{r.model ?? ""}</td><td>{r.created_at}</td></tr>)}</tbody></table>
      <p className="foot">Time zone: {rec.timezone}</p>
    </>
  );
}
```

- [ ] **Step 2: Add the tabs to `RecordingPane.tsx`**

Add these imports:
```tsx
import { DetailsTab } from "./DetailsTab";
import { MyNotesTab } from "./MyNotesTab";
import { NotesTab } from "./NotesTab";
import { PlaudTab } from "./PlaudTab";
```
Replace the `<TabsList>…</TabsList>` element with:
```tsx
        <TabsList className="tabs-list" aria-label="Recording">
          <TabsTrigger value="transcript" data-testid="tab-transcript">Transcript</TabsTrigger>
          <TabsTrigger value="notes" data-testid="tab-notes">Notes ({rec.notes.reduce((n, g) => n + g.outputs.length, 0)})</TabsTrigger>
          <TabsTrigger value="plaud" data-testid="tab-plaud">Plaud</TabsTrigger>
          <TabsTrigger value="my-notes" data-testid="tab-my-notes">My notes</TabsTrigger>
          <TabsTrigger value="details" data-testid="tab-details">Details</TabsTrigger>
        </TabsList>
```
After the transcript `<TabsContent>`, add:
```tsx
        <TabsContent value="notes" className="tab"><NotesTab groups={rec.notes} /></TabsContent>
        <TabsContent value="plaud" className="tab"><PlaudTab notes={rec.plaud_notes} /></TabsContent>
        <TabsContent value="my-notes" className="tab"><MyNotesTab html={rec.my_notes_html} /></TabsContent>
        <TabsContent value="details" className="tab"><DetailsTab rec={rec} /></TabsContent>
```

- [ ] **Step 3: Typecheck, test, build and check by hand**

Run: `make test-js && make build && make demo`
Expected:
- **JFK:** the Notes tab shows `conference-talk` with two models. "Compare with" shows both
  side by side.
- **Apollo 13:** the Plaud tab shows its notes, and the Notes tab says "No notes yet".
- **Details:** lists the sources and outputs.

Stop the server with Ctrl-C.

- [ ] **Step 4: Commit**

```bash
git add packages/ui/frontend
git commit -m "feat(ui): notes tab (layout B with compare), Plaud, My notes and Details tabs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: End-to-end tests in demo mode

**Files:**
- Create: `packages/ui/tests/e2e/test_library.py`

**Interfaces:**
- Consumes: the `recordings-ui --demo` server (Task 11), the built client and its
  `data-testid` hooks (Tasks 13 and 14), and `demo_ids` (Task 10 conftest).

- [ ] **Step 1: Write the tests**

`packages/ui/tests/e2e/test_library.py`:
```python
"""Browser tests against a real `recordings-ui --demo` server. Run with `make e2e`.

Chromium builds from Playwright have no H.264/AAC decoder, so the video test checks the element,
not playback; MP3 plays, so seeking is tested on the JFK audio.
"""

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url():
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k != "RECORDINGS_ARCHIVE"}
    proc = subprocess.Popen([sys.executable, "-m", "recordings_ui", "--demo", "--port", str(port)], env=env)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{url}/healthz", timeout=1)
            break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("demo server did not start")
    yield url
    proc.terminate()
    proc.wait(timeout=10)


def open_library(page: Page, url: str) -> None:
    page.goto(url)
    expect(page.get_by_test_id("recording-row")).to_have_count(4)


def test_untagged_filter_shows_only_apollo_13(page: Page, server_url):
    open_library(page, server_url)
    page.get_by_test_id("filter-untagged").click()
    rows = page.get_by_test_id("recording-row")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("Apollo 13")


def test_clicking_a_line_seeks_the_audio(page: Page, server_url):
    open_library(page, server_url)
    page.get_by_test_id("recording-row").filter(has_text="JFK").click()
    page.wait_for_function("document.querySelector('[data-testid=media]')?.readyState >= 1")
    turn = page.get_by_test_id("turn").nth(2)
    start = float(turn.get_attribute("data-start"))
    turn.click()
    current = page.evaluate("document.querySelector('[data-testid=media]').currentTime")
    assert abs(current - start) < 0.5
    expect(turn).to_have_class(re.compile(r"\bnow\b"))


def test_private_recording_shows_the_lock(page: Page, server_url):
    open_library(page, server_url)
    page.get_by_test_id("recording-row").filter(has_text="Fireside").click()
    expect(page.get_by_test_id("lock")).to_be_visible()


def test_video_recording_renders_a_video_element(page: Page, server_url):
    open_library(page, server_url)
    page.get_by_test_id("recording-row").filter(has_text="Apollo 11").click()
    expect(page.locator("video[data-testid=media]")).to_have_count(1)


def test_notes_compare_side_by_side(page: Page, server_url):
    open_library(page, server_url)
    page.get_by_test_id("recording-row").filter(has_text="JFK").click()
    page.get_by_test_id("tab-notes").click()
    expect(page.get_by_test_id("notes-output")).to_have_count(2)
    page.get_by_test_id("compare-select").select_option(index=1)
    expect(page.get_by_test_id("compare-view").locator("section")).to_have_count(2)


def test_dark_mode_sticks_across_reloads(page: Page, server_url):
    open_library(page, server_url)
    page.get_by_test_id("theme-dark").click()
    expect(page.locator("html")).to_have_class(re.compile(r"\bdark\b"))
    page.reload()
    expect(page.get_by_test_id("recording-row")).to_have_count(4)
    expect(page.locator("html")).to_have_class(re.compile(r"\bdark\b"))
```

- [ ] **Step 2: Run them**

Run: `make e2e`
Expected: 6 passed. A failure here is a real bug in Tasks 11–14. Fix it there, not in the
test.

- [ ] **Step 3: Commit**

```bash
git add packages/ui/tests/e2e
git commit -m "test(ui): end-to-end library tests against demo mode

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Docker, Compose, CI and Dependabot

**Files:**
- Create: `docker/Dockerfile`, `docker/compose.yml`, `docker/compose.demo.yml`, `docker/deploy.example.env`, `.dockerignore`
- Create: `.github/workflows/ci.yml`, `.github/dependabot.yml`, `packages/core/tests/test_deploy_template.py`
- Modify: `README.md` (adds a Docker section), `Makefile` (adds `deploy`)

**Interfaces:**
- Consumes: everything above, including `config.toml` and the secret conventions from
  Task 9. The image runs `recordings-ui`.
- Produces:
  - `docker/deploy.example.env`, the committed template for the git-ignored
    `docker/deploy.env`
  - `make deploy`
  - **The rule:** every `${VAR}` in `docker/compose.yml` is documented in the template.

- [ ] **Step 1: Write the Docker files**

`.dockerignore`:
```
.git
.venv
**/node_modules
**/__pycache__
demo/.cache
packages/ui/src/recordings_ui/www/ui.js
packages/ui/src/recordings_ui/www/ui.css
.superpowers
```

`docker/Dockerfile`:
```dockerfile
# Stage 1 (spec §4): Node 22 builds the React client; a Python 3.14 runtime serves it.
FROM node:22-slim AS frontend
WORKDIR /src/packages/ui/frontend
COPY packages/ui/frontend/package.json packages/ui/frontend/package-lock.json ./
COPY packages/ui/frontend/scripts ./scripts
RUN npm ci
COPY packages/ui/frontend/ ./
RUN npm run build

FROM python:3.14-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY packages/core packages/core
COPY packages/ui packages/ui
COPY --from=frontend /src/packages/ui/src/recordings_ui/www/ui.js /src/packages/ui/src/recordings_ui/www/ui.css packages/ui/src/recordings_ui/www/
RUN uv sync --frozen --no-dev --no-editable
COPY demo/archive demo/archive
RUN useradd --create-home --uid 10001 app
USER app
ENV PATH=/app/.venv/bin:$PATH RECORDINGS_DEMO_ARCHIVE=/app/demo/archive
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["recordings-ui", "--host", "0.0.0.0", "--port", "8000"]
```

`docker/compose.demo.yml`:
```yaml
# Demo mode: the bundled public-domain archive, no real data, no secrets. `make docker`.
services:
  web:
    build: { context: .., dockerfile: docker/Dockerfile }
    image: recordings:dev
    command: ["recordings-ui", "--demo", "--host", "0.0.0.0", "--port", "8000"]
    ports: ["127.0.0.1:8000:8000"]
```

`docker/compose.yml`:
```yaml
# The real deployment (the homelab server). Every ${VAR} comes from docker/deploy.env, which is
# git-ignored, via `make deploy`; docker/deploy.example.env documents them. Nothing
# machine-specific and no secret lives in this file. The archive is read-only in stage 1.
# Secrets arrive in stage 2 as Docker secrets (files under /run/secrets, read through
# the *_FILE variables), so they never show up in `docker inspect`.
services:
  web:
    build: { context: .., dockerfile: docker/Dockerfile }
    image: recordings:dev
    user: "${RECORDINGS_UID:?}:${RECORDINGS_GID:?}"
    environment:
      RECORDINGS_CONFIG: /config/config.toml
      RECORDINGS_ARCHIVE: /archive
    volumes:
      - ${RECORDINGS_CONFIG_HOST:?set RECORDINGS_CONFIG_HOST in docker/deploy.env}:/config/config.toml:ro
      - ${RECORDINGS_ARCHIVE_HOST:?set RECORDINGS_ARCHIVE_HOST in docker/deploy.env}:/archive:ro
    ports: ["${RECORDINGS_BIND:?}:${RECORDINGS_PORT:?}:8000"]
    restart: unless-stopped
```

`docker/deploy.example.env`:
```sh
# Host-side settings for the Docker deployment. No secrets here.
#   cp docker/deploy.example.env docker/deploy.env      (deploy.env is git-ignored)
#   make deploy
# Relative paths are relative to docker/, where compose.yml lives.

# The archive folder on this machine: the homelab server's own disk (spec §3). Mounted at /archive.
RECORDINGS_ARCHIVE_HOST=/srv/recordings/archive
# Your config.toml (copied from config.example.toml). Mounted read-only.
RECORDINGS_CONFIG_HOST=../config.toml
# Where to publish the app. Use this machine's Tailscale IP so only the tailnet reaches it;
# 127.0.0.1 keeps it local while testing.
RECORDINGS_BIND=127.0.0.1
RECORDINGS_PORT=8000
# The user the container runs as: the owner of /srv/recordings, so that stage 2 (the first
# stage that writes) can write to it. `id -u` and `id -g` on the homelab server.
RECORDINGS_UID=1000
RECORDINGS_GID=1000
```

`packages/core/tests/test_deploy_template.py`:
```python
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def test_every_compose_variable_is_documented_in_the_template():
    compose = (REPO / "docker" / "compose.yml").read_text(encoding="utf-8")
    template = (REPO / "docker" / "deploy.example.env").read_text(encoding="utf-8")
    used = set(re.findall(r"\$\{(\w+)", compose))
    documented = set(re.findall(r"^(\w+)=", template, re.M))
    assert used, "compose.yml uses no variables?"
    assert used <= documented, f"undocumented: {sorted(used - documented)}"


def test_the_template_holds_no_secret_names():
    template = (REPO / "docker" / "deploy.example.env").read_text(encoding="utf-8")
    for name in ("TOKEN", "API_KEY", "PASSWORD", "SECRET="):
        assert name not in template
```

Add to the `Makefile`'s `.PHONY` line: `deploy`. Then add the target:
```make
deploy:
	docker compose --env-file docker/deploy.env -f docker/compose.yml up -d --build
```

- [ ] **Step 2: Build and run the demo image**

Run: `docker compose -f docker/compose.demo.yml up --build -d && sleep 5 && curl -s http://127.0.0.1:8000/healthz && curl -s -o /dev/null -w "%{http_code}\n" -H "Range: bytes=0-9" "http://127.0.0.1:8000/media/$(uv run python -c "import json;print(next(k for k,v in json.load(open('demo/canned/aliases.json')).items() if v=='jfk-rice'))")"; docker compose -f docker/compose.demo.yml down`
Expected: `{"ok":true,"demo":true}`, then `206`.

- [ ] **Step 3: Write CI**

The actions are pinned by SHA, in the same style as `local-ai` (SHAs checked on
2026-10-08). gitleaks is downloaded and checksummed as `local-ai` does.

`.github/workflows/ci.yml`:
```yaml
name: ci
on:
  push:
  pull_request:
permissions:
  contents: read

jobs:
  python:
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with: { persist-credentials: false }
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with: { version: "0.12.23" }
      - uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7.0.0
        with: { node-version-file: packages/ui/frontend/.nvmrc, cache: npm, cache-dependency-path: packages/ui/frontend/package-lock.json }
      - run: npm ci && npm run build
        working-directory: packages/ui/frontend
      - run: uv run --frozen pytest
      - run: uv run --frozen recordings schemas --check
      - run: uv run --frozen recordings validate demo/archive

  frontend:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    defaults: { run: { working-directory: packages/ui/frontend } }
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with: { persist-credentials: false }
      - uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7.0.0
        with: { node-version-file: packages/ui/frontend/.nvmrc, cache: npm, cache-dependency-path: packages/ui/frontend/package-lock.json }
      - run: npm ci
      - run: npm run typecheck
      - run: npm test
      - run: npm run build

  e2e:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with: { persist-credentials: false }
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with: { version: "0.12.23" }
      - uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7.0.0
        with: { node-version-file: packages/ui/frontend/.nvmrc, cache: npm, cache-dependency-path: packages/ui/frontend/package-lock.json }
      - run: npm ci && npm run build
        working-directory: packages/ui/frontend
      - run: uv run --frozen playwright install --with-deps chromium
      - run: uv run --frozen pytest -m e2e

  leaks:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with: { fetch-depth: 0, persist-credentials: false }
      - name: download gitleaks
        run: curl -fsSL --retry 3 --max-time 120 -o gitleaks.tar.gz "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz"
      - name: check gitleaks against its pinned checksum, then unpack it
        run: |
          echo "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb  gitleaks.tar.gz" | sha256sum -c -
          tar -xzf gitleaks.tar.gz gitleaks
      - name: gitleaks over the whole history
        run: ./gitleaks git --redact --no-banner -v .

  docker:
    runs-on: ubuntu-24.04
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with: { persist-credentials: false }
      - uses: docker/setup-qemu-action@99012661954931238ded8c8b007157a8430204e1 # v4.4.0
      - uses: docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069 # v4.4.1
      # amd64 for the homelab server, arm64 so the image runs on the Mac too. Built, never pushed.
      - uses: docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc # v7.4.0
        with:
          context: .
          file: docker/Dockerfile
          platforms: linux/amd64,linux/arm64
          push: false
```

`.github/dependabot.yml`:
```yaml
# Update proposals, a few at a time, on Fridays. Actions stay SHA-pinned: Dependabot moves a
# SHA pin and its version comment together. shinyreact's Python and npm pins are upgraded
# together on purpose (CLAUDE.md "Upgrading shinyreact"), so review those two PRs as a pair.
version: 2
updates:
  - package-ecosystem: github-actions
    directory: /
    schedule: { interval: weekly, day: friday }
    open-pull-requests-limit: 3
    commit-message: { prefix: "ci" }
  - package-ecosystem: uv
    directory: /
    schedule: { interval: weekly, day: friday }
    open-pull-requests-limit: 3
    commit-message: { prefix: "build" }
  - package-ecosystem: npm
    directory: /packages/ui/frontend
    schedule: { interval: weekly, day: friday }
    open-pull-requests-limit: 3
    commit-message: { prefix: "build(ui)" }
  - package-ecosystem: docker
    directory: /docker
    schedule: { interval: weekly, day: friday }
    open-pull-requests-limit: 2
    commit-message: { prefix: "build(docker)" }
```

- [ ] **Step 4: Add Docker to the README**

Append to `README.md`:
````markdown
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
````

- [ ] **Step 5: Run everything and scan for secrets**

Run: `make test && make e2e && gitleaks dir . --no-banner && gitleaks git . --no-banner`
Expected: all tests pass, and both gitleaks scans report `no leaks found`.

- [ ] **Step 6: Commit**

```bash
git add .dockerignore docker .github README.md Makefile packages/core/tests/test_deploy_template.py
git commit -m "build: Docker image, compose files, CI and Dependabot

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Stage 1 is done when

- `make test` and `make e2e` pass, `gitleaks` is clean, and `docker compose -f
  docker/compose.demo.yml up` serves the demo.
- Opening the demo shows the warm themes and all four recordings. The transcript follows
  the audio, the Notes tab compares Spark against Claude, and the Plaud, My notes and
  Details tabs all render.
- **Not in stage 1:**
  - writing from the UI
  - tags and the Tags page
  - Plaud sync and the `audio-router` import
  - the job queue
  - the SQLite index and search
  - the single-writer host check
  - `config.toml`

  Those arrive in stages 2–4, each with its own plan.
