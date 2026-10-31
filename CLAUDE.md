# recordings

Read `docs/superpowers/specs/2026-10-08-recordings-design.md` before changing behaviour.

## Toolchain

- **Python only through uv.** `uv sync`, `uv run …`. Never pip, never the system Python.
- **Node 22 from `packages/ui/frontend/.nvmrc`.** Use `nvm use`, then `npm ci`. No global
  installs.
- **`make help`** lists every target. **`make check`** is what every commit runs first: `make
  lint`, `make test`, `make test-utc` (pytest again under `TZ=UTC`) and `make leaks` (gitleaks
  over the whole history, as CI's `leaks` job runs it).
- **`make test`** runs pytest and Vitest. **`make e2e`** runs the Playwright tests against
  demo mode.
- **Commit messages are checked by hand,** because the repo has no commit hooks: write the message
  to a file, run `uv run --script scripts/commitmsg.py <file>` (copied from Dan's
  plan-driven-development skill), then `git commit -F <file>`.
- **`make e2e`'s writing tests** (`packages/ui/tests/e2e/test_tagging.py`) get a server each,
  started with `TMPDIR=<tmp_path>/server`, and read what the app wrote through `Archive`. axe-core runs
  from `packages/ui/frontend/node_modules`, so run `npm ci` first.
  To debug one: after `make build`, run it alone with `uv run pytest -m e2e -k <name>`. Its server's log is
  `<tmp_path>/server/server.log` (pytest prints the folder; CI keeps them with
  `--basetemp=e2e-tmp`), and the demo's temporary copy of the archive is
  `<tmp_path>/server/recordings-demo-*/archive` (`Server.copy` finds it).
- **`make lint`** runs ruff (check and format --check). CI runs it too. The rule set is pinned in
  pyproject.toml.
- **`make pages`** builds the static GitHub Pages demo into `_site/` (spec §17.1). Only the demo
  archive is ever exported.

## Check the docs, never write from memory

These change quickly. Before writing or changing code that uses any of them, check the
current skill or docs. Say in the commit or PR what you checked. If the docs don't cover
it, say so instead of guessing, and record the gap in `docs/upstream-issues.md`. Never report
anything to an upstream project: issues, comments and PRs there are for Dan to write, if ever.

| Library | Skill (`make skills` installs both) | Docs |
|---|---|---|
| shinyreact | `/shinyreact-build-app` | https://posit-dev.github.io/shinyreact/ |
| shadcn/ui | the shadcn skill | https://ui.shadcn.com/docs |
| brand.yml | Posit's `brand-yml` skill, if installed | https://posit-dev.github.io/brand-yml/, plus https://quarto.org/docs/authoring/brand.html for `{light, dark}` colours |
| Tailwind v4 | none | https://tailwindcss.com/docs |

The rules the code depends on:
- **React stays external,** mapped to `window.shinyreact.React`, and `react-dom/client` to
  `window.shinyreact.ReactDOM`. Two React copies make every hook silently return nothing. The one
  exception is the `react-dom` index, which is bundled because shinyreact's `ReactDOM` global
  lacks `createPortal` and `flushSync` (`docs/upstream-issues.md`); it requires the external
  `react`. Never bundle `react` itself. `src/build.test.ts` pins this: the config, and the built
  `www/ui.js` (the IIFE's argument, no `react`, `react-dom/client` or `scheduler` copy). CI's
  `frontend` job builds first, so those run there; the `e2e` job also runs the check that the
  bundled `react-dom` has `shinyreact.js`'s React major.minor, because it is the only Vitest job with a `.venv`
  (`RECORDINGS_REQUIRE_SHINYREACT=1`, which only a missing `shinyreact.js` needs). Locally the
  built-bundle tests skip until `make build` (and `uv sync`); with `CI` set, a missing `ui.js`
  fails.
- **`www/ui.js` is build output.** Never edit it. Rebuild with `make build`.
- **`ReactApp(server)` in `shiny_app.py`** finds `www/` next to that file.
- **Every output is subscribed in `App`.** shinyreact 0.1.0 leaves an output bound to a removed
  element once its last component unmounts (`docs/upstream-issues.md`). A new output gets a
  `useShinyOutputStatus` line in `App.tsx`, even if only one page uses it.
- **Every message handler and input lives in `App`** (through `useTagging`), mounted once:
  shinyreact drops a message that has no handler. Tag requests are only ever sent by a person's
  action: never on load, from a URL or its hash, or from storage. List state (`useListState` for
  the Library, `useGridState` for the Tags grid) is held in `App` too, so it survives route
  switches and the switch from tablet to phone width. On a phone, `#/tags` is the Library with the
  Untagged filter (`App` replaces the hash and sets the filter); there is no phone Tags page.
- **`PHONE` is `(max-width: 760px)`** (`useMediaQuery.ts`), and the two `@media (max-width: 760px)`
  blocks in `app.css` must stay equal to it. So must PHONE's complement there, `@media (hover:
  none) and (min-width: 761px)` (the touch tablet's picker dialog): move all three together.
- **The sheet rules in `app.css` are unlayered on purpose,** so that they beat Tailwind's layered
  utilities without `!important`. Don't move them into a layer. The reduced-motion block's
  `animation: none !important` and `transition: none !important` are also deliberate: they must
  beat Sonner's own injected, unlayered stylesheet, whose rules come later in
  the document (its `[data-sonner-toast][data-styled=true] [data-button]` ones are also more
  specific). Keep that `!important`, and add no other.
- **Never `stopPropagation` a click** (one exception below). Radix's dismissable layer reads a
  click that never reaches the document as an interaction something else consumed, and leaves its popover open
  (`docs/upstream-issues.md`). Guard a row's or a cell's handler with `closest("[data-control]")`
  or `contains` instead, as `onRowClick` does. The one exception is the picker item's ×
  (`TagPicker.tsx`): a click inside the open layer that must not reach its own cmdk item, which
  selects on click and would add the tag.

## Upgrading shinyreact

The Python `shinyreact` and the npm `@posit-dev/shinyreact` (types only) are pinned
exactly. To upgrade:

1. Read the release notes.
2. Bump both pins.
3. `make skills`.
4. Check that `.nvmrc` still matches shinyreact's `pkg-js/.nvmrc`.
5. Check that its examples' Vite, plugin-react, TypeScript and Vitest versions still match
   ours. Move `react`, `react-dom` and their `@types` to the React minor version that
   `shinyreact.js` bundles (`grep -o '"19\.[0-9.]*"' …/shinyreact/www/shinyreact.js`). The bundled
   `react-dom` index must stay on that same minor: it reads the external React's internals, so a
   mismatch breaks portals and `flushSync` at runtime.
6. Check each shinyreact entry in `docs/upstream-issues.md`, which says how to tell when its
   workaround can go. In particular re-check the react-dom entry: if
   `typeof window.shinyreact.ReactDOM.createPortal === 'function'` in the console of a running app,
   the bundled `react-dom` index can go.
7. `make test e2e`.

## Upgrading Shinylive (the Pages demo)

`shinylive` is pinned exactly in the `pages` dependency group. Its version fixes the Pyodide,
Python, shiny and pydantic that run in visitors' browsers. A Dependabot bump fails the Pages
smoke test on purpose until someone does the steps below:

1. Read the release notes, and check which shiny it bundles. That shiny must still satisfy
   shinyreact.
2. Check that `shinylive/_assets.py` still has `shinylive_cache_dir()`, which
   `scripts/shinylive_local.py` overrides to keep its assets in the repo's `.cache/`. Then
   update `PINNED` there.
3. Set `[tool.ruff] target-version` in `pyproject.toml` to the Python of its Pyodide
   (`info.python` in `.cache/shinylive/shinylive-*/shinylive/pyodide/pyodide-lock.json`). The demo
   runs our source under that Python, so ruff must not write newer syntax.
4. Run `make pages pages-test`, and also `make pages-serve` to check it by hand.

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
  capitalisation, read after NFKC and without invisible characters, a fullwidth `／`
  counting as `/` (`recordings.models.is_private_tag`). The private tag lands before any source or
  output does, judged on every title the app has seen at that moment. One deliberate exception
  (stage 2b, Dan, 2026-10-23): a Plaud recording renamed to a private title, or to the keep-out
  word, after it came in is tagged `private` at the next sync, so its source and outputs landed
  first. Until then it carries whatever tags Dan gave it; with none, external agents already
  treat it as private. Don't read a private recording's transcript or notes. No report, alert or
  log carries a title.
- **The Plaud client is read-only and runs only in the worker.** `recordings_ui` never imports
  the client, the token store or the sync (`packages/ui/tests/test_plaud_imports.py`), not even
  at runtime: what reads settings takes `API_BASE` and `check_api_base` from the network-free
  `plaud/endpoint.py`, never from the client. No test reaches Plaud: the root `conftest.py`
  refuses every `plaud.ai` host and drops every `*_proxy` variable, for the whole run of pytest's
  own process (not for a subprocess a test starts), and the client's tests use
  the fake server in `packages/core/tests/fake_plaud.py` (the `plaud_server` fixture).
- **Tag changes from the app go through `recordings.tagops`** (`AddTags`, `RemoveTags`, `RestoreTags`),
  applied by `Writer.mutate` on the tag service's one writer thread
  (`recordings_ui/tagging.py`, `writing.py`). The privacy gate is inside the operations: a
  recording never loses its last private tag unless its ID was confirmed. `tagops.py` imports
  only `recordings.models`, because the Pages demo runs it on Pyodide, and Pages' `MemoryStore`
  rebuilds a record with `model_dump` and `model_validate`, never `dump_json`, because of
  Pyodide's pydantic. The other paths that set tags are the import's: `Writer.add` and
  `MergeIncoming` (`writer.py`, via `_append_tags`), and `SetPlaudFields`, which adds the automatic
  `private` tag through `AddTags` in `Writer.reconcile_plaud` (from `Writer.reindex` and the
  audio-router import). All run inside the Writer.
- **To add a tag operation or rule:** write the operation in `tagops.py` (its `apply`, and the
  `Effect` it records so `RestoreTags` can undo it), put it behind the privacy gate, then teach the
  service in `recordings_ui/tagging.py` (`classify` for any new refusal, the request checks, the
  Undo token) and the `Tagging` hook in `useTagging.tsx`. Test it against both stores, `ArchiveStore`
  and Pages' `MemoryStore` (spec §4–§5). The fake-failure tests (busy, full disk, read-only, a
  failed open, a stopped thread) are in `packages/ui/tests/test_tag_stores.py` and
  `test_tag_service.py`: copy one.
- **Tagging's logs carry fixed phrases,** IDs, counts and exception class names: never a tag
  name, a title or `str(exc)`. So do replies' `message` and failure reasons. Replies also carry
  `rows` (with titles) and `tags` by design, for the one person using the app. The one text that
  comes from an exception is `cannot_tag_reason` (and the "Stopped: …" that repeats it): the
  Writer's own refusal, written for display, which names paths and IDs, never a title. A log never
  carries even that.
- **Fixtures are synthetic:** `packages/core/tests/conftest.py` builds Plaud envelopes and an
  `audio-router` archive by hand. Never copy real data in.
- **The archive's own docs** come from `packages/core/src/recordings/format/`. Edit them there.
- **Settings:** a new key in `config.example.toml` needs its row in
  `packages/ui/tests/test_template_settings.py`, with the reader that uses it.
- **The first real run, and restoring for real,** are in `docs/runbooks/first-run.md`.
