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
   ours. Move `react`, `react-dom` and their `@types` to the React minor version that
   `shinyreact.js` bundles (`grep -o '"19\.[0-9.]*"' …/shinyreact/www/shinyreact.js`).
6. `make test e2e`.

## Archive rules (spec §6)

- **Write once:** media, `source/` and `renditions/` are never overwritten. Only
  `recording.json`, `my-notes.md` and `tags.yaml` change, by atomic replace.
- **Privacy:** a recording is private if a tag is `private` or under `private/`, in any
  capitalisation (`recordings.models.is_private_tag`). Don't read a private recording's
  transcript or notes.
- **The archive's own docs** come from `packages/core/src/recordings/format/`. Edit them
  there.
