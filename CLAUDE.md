# recordings

Read `docs/superpowers/specs/2026-10-08-recordings-design.md` before changing behaviour.

## Toolchain

- **Python only through uv.** `uv sync`, `uv run …`. Never pip, never the system Python.
- **Node 22 from `packages/ui/frontend/.nvmrc`.** Use `nvm use`, then `npm ci`. No global
  installs.
- **`make test`** runs pytest and Vitest. **`make e2e`** runs the Playwright tests against
  demo mode.
- **`make pages`** builds the static GitHub Pages demo into `_site/` (spec §17.1). Only the demo
  archive is ever exported.

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
   ours. Move `react`, `react-dom` and their `@types` to the React minor version that
   `shinyreact.js` bundles (`grep -o '"19\.[0-9.]*"' …/shinyreact/www/shinyreact.js`).
6. `make test e2e`.

## Upgrading Shinylive (the Pages demo)

`shinylive` is pinned exactly in the `pages` dependency group. Its version fixes the Pyodide,
Python, shiny and pydantic that run in visitors' browsers. A Dependabot bump fails the Pages
smoke test on purpose until someone does the steps below:

1. Read the release notes, and check which shiny it bundles. That shiny must still satisfy
   shinyreact.
2. Check that `shinylive/_assets.py` still has `shinylive_cache_dir()`, which
   `scripts/shinylive_local.py` overrides to keep its assets in the repo's `.cache/`. Then
   update `PINNED` there.
3. Run `make pages pages-test`, and also `make pages-serve` to check it by hand.

## Archive rules (spec §6)

- **Every write goes through `recordings.writer.Writer`.** Of the archive's own data, only three
  files ever change: `recording.json`, only through `Writer.mutate`, which bumps `rev` and
  refuses a file edited outside the app until `recordings reindex` accepts the edit;
  `my-notes.md`, only appended to, under the recording's lock; and `tags.yaml`, by atomic
  replace. The archive's docs (`README.md`, `AGENTS.md`, `FORMAT.md`, `recordings/README.md`,
  `catalog/README.md` and `schemas/`) are replaced from `packages/core/src/recordings/format/`,
  by atomic replace, whenever a Writer opens or the web app starts against an archive it may
  write (`selfdoc.write_docs`). Media, `source/` and `renditions/` are published write-once and
  never overwritten. Never write archive files any other way, in code or in tests.
- **The sentinel:** writers, backups and the mirror refuse an archive without `archive.json`. Tests
  make archives with `recordings.init.init_archive` (or the `writer` fixture).
- **Privacy:** a recording is private if a tag is `private` or under `private/`, in any
  capitalisation (`recordings.models.is_private_tag`). The private tag lands before any source or
  output does. Don't read a private recording's transcript or notes. No report, alert or log
  carries a title.
- **Fixtures are synthetic:** `packages/core/tests/conftest.py` builds Plaud envelopes and an
  `audio-router` archive by hand. Never copy real data in.
- **The archive's own docs** come from `packages/core/src/recordings/format/`. Edit them there.
- **Settings:** a new key in `config.example.toml` needs its row in
  `packages/ui/tests/test_template_settings.py`, with the reader that uses it.
- **The first real run, and restoring for real,** are in `docs/runbooks/first-run.md`.
