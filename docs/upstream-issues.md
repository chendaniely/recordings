# Upstream issues

Problems found in libraries this repo depends on, and how the repo works around them. They are
recorded here only. Nothing from this project is reported to the upstream projects: it is
AI-written, and it stays out of places where people do the real work.

Each entry says how to tell when the workaround can go. Check the entries when upgrading the
library (CLAUDE.md, "Upgrading shinyreact").

## shinyreact 0.1.0: an unmounted output stays bound to a removed element

Found 2026-10-15, after a Pages test failed once in CI with `Output recording not found`
logged twice. Fixed here in `packages/ui/frontend/src/App.tsx` (PR #17).

**What shinyreact does.** Shiny delivers an output's values only to an element it has bound
to that output's name. shinyreact gives every output a component uses a hidden placeholder
`<div>` for Shiny to bind, plus a registry of the components listening to it. A value goes
from Shiny to the placeholder, then to shinyreact, then through the registry to the components.

**The bug.** When the last component using an output unmounts, shinyreact cleans up on the next
animation frame, in this order:

1. It deletes the output from its registry.
2. It removes the placeholder from the page.
3. On a later frame, it unbinds and rebinds everything inside its hidden container.

Shiny's unbind (`unbindOutputs` in `shiny.js`) only looks inside that container. The placeholder
has already left it, so it is never unbound, and Shiny keeps sending that output's values to the
detached element. shinyreact finds no registry entry for them, logs `Output <id> not found` and
drops the value. This lasts until a component uses the output again, which binds a new
placeholder. Shiny also never reports the detached output as hidden, so the server keeps
computing it.

**Where it showed here.**
- Leaving the library unmounted the recording pane and left `recording` stale. In CI, the
  demo's first `recording` value arrived after the Status click, because Pyodide is slow, so
  the test failed only sometimes.
- Leaving Status left `status` stale. The server recomputes it every minute, so after one
  visit to Status the console logged the error every minute.

**The workaround.** `App` never unmounts, and it subscribes to every output
(`useShinyOutputStatus`), so no output's registry entry ever empties and the cleanup never
runs. The cost: Status is computed from the start of a session, not from its first visit.
CLAUDE.md makes this a rule: **a new output must also be subscribed in `App`.**

**The test.** `test_a_page_left_behind_keeps_no_binding_and_logs_nothing`
(`packages/ui/tests/e2e/test_library.py`) leaves each page and sends a value for the absent
output. It then asserts that no Shiny binding points at a detached element and that nothing
was logged. It fails without the workaround.

**When it can go.** The code is `scheduleCleanup` in shinyreact's
`pkg-js/src/shiny-react/output-registry.ts`, still unchanged on its main branch at e881235. A
version that unbinds the placeholder before removing it (`Shiny.unbindAll(el, true)`) fixes it.
To check a new version, remove the two `useShinyOutputStatus` lines from `App` and run
`make e2e`: if the test above passes, the bug is fixed. Keeping the lines is harmless either way.

## shinyreact 0.1.0: its ReactDOM global is `react-dom/client` only

Found 2026-10-15, while designing tagging part A (spec §6).

**What shinyreact does.** `window.shinyreact.ReactDOM` is React's `react-dom/client` module:
`createRoot` and `hydrateRoot`. It has no `createPortal` and no `flushSync`, which live in the
`react-dom` index module.

**Why it matters.** Radix (Popover, Alert Dialog, Dialog, Tooltip), Sonner and TanStack Virtual
import both from `react-dom`. Mapped to the global, as the shinyreact-build-app skill's Vite
config does for `react-dom`, they find `undefined` and throw on first use.

**The workaround.** `packages/ui/frontend/vite.config.js` maps only `react` and
`react-dom/client` to the globals, and bundles the `react-dom` index. That module requires
`react`, which stays external, so there is still one React. Its `createPortal` builds a plain
element, which the global copy renders. Its `flushSync` runs the callback but can't force the
other copy to flush synchronously; that is acceptable for these libraries, and TanStack Virtual
is set to `useFlushSync: false`. The bundled index's `Internals.d` is a no-op stub, so beyond
`flushSync` (which runs the callback without a synchronous flush), `preload`, `preinit`,
`preconnect` and `prefetchDNS` silently do nothing, and `requestFormReset` throws (Minified React
error #522). No library in use calls them today. `src/build.test.ts` pins the mapping, and the e2e test
`test_the_built_bundle_portals_toasts_dialogs_and_scrolls` checks the built bundle with a clean
console.

**When it can go.** When shinyreact's global gains `createPortal`. Check at runtime, in the browser
console on a running app: `typeof window.shinyreact.ReactDOM.createPortal === 'function'` (a grep
of the minified `shinyreact.js` for identifiers is unreliable). If it is `true`, map `react-dom`
back to it, delete the line in `vite.config.js` and `build.test.ts`'s expectation, and run
`make e2e`. Re-check on every shinyreact bump.

**The skill doesn't say this either.** Its Vite config maps `react-dom` to the global without
noting that the global lacks the index module's exports.

## cmdk 1.1.1: aria-activedescendant lags the highlight under a controlled `value`

Found 2026-10-16, by Task 11's council (a real-browser screen-reader probe of the tag picker).

**What cmdk does.** The input's (and the list's) `aria-activedescendant` is cmdk's
`selectedItemId`. cmdk sets it in a scheduled layout effect that reads the DOM's
`[aria-selected="true"]` item. With a controlled `value`, the root's scheduler effect runs before
the effect that applies the new value (`dist/index.mjs`: `ke()` is registered before the
controlled-value effect). So it reads the item highlighted before, or none. And a re-highlight to
an item whose value is unchanged never updates it at all. After opening, after typing, and after
the box is cleared, the input points at the old item, or at nothing, until an arrow key moves the
highlight.

**Why it matters.** A screen reader announces the wrong tag, or none, as the highlighted one,
while Enter acts on the item the eye sees.

**The workaround.** `TagPicker.tsx`'s `mirrorActiveItem` watches the list with a
`MutationObserver` (`aria-selected`, subtree, child list) and the input's own
`aria-activedescendant`. It copies the highlighted item's id onto the input and the list whenever
they differ, and removes it when nothing is highlighted. A Vitest test and the e2e test
`test_the_box_names_the_highlighted_item_as_it_moves` check it.

**When it can go.** When a cmdk release keeps `selectedItemId` in step with a controlled `value`.
To check, delete the `useLayoutEffect` that calls `mirrorActiveItem` and run that e2e test. If it
passes, the workaround can go. Re-check on every cmdk bump.

## Radix Popover 1.3.1: where focus goes on a close other than Esc isn't documented

Found 2026-10-16, writing the Tags grid's sort menus (tagging part A, Task 14).

**What the docs say.** Radix's Popover page (reached through `npx shadcn@4.21.4 docs popover`)
says only that Esc closes the popover and moves focus to the trigger. It doesn't say where focus
goes when the popover closes any other way, such as a controlled `open` set to false after an
item is picked.

**What Radix does.** The non-modal content's `onCloseAutoFocus` focuses the trigger on every
close, unless the close came from an interaction outside the popover
(`@radix-ui/react-popover` 1.3.1, `dist/index.mjs`, `PopoverContentNonModal`).

**What relies on it.** `TagsPage.tsx`'s `RowSortMenu` and `ColumnMenu` close themselves after a
pick, and focus must go back to their header button, so the grid's keys go on working. The e2e
test `test_the_sort_menus_reorder_the_rows_and_say_which_column_is_sorted` checks it. Nothing
works around it.

**When this note can go.** When Radix's docs say where focus goes on a controlled close. Re-run
that test on every `radix-ui` bump.

## shinyreact-build-app skill: what it doesn't cover

- **How long an output lives once its components unmount** (the first entry above). The skill doesn't
  say an output can outlive its last component, or that it then breaks.
- **`reactive.invalidate_later`**, for an output that refreshes on its own (Status's 60-second
  refresh, `shiny_app.py`). Stage 2a checked it against the installed Shiny's docstring instead.
- **Testing `send_message`.** The testing reference reads outputs and inputs through
  `local_server` and `test_server`, but says nothing about messages. Shiny 1.8.0's test server
  can't show them: its `MockConnection.send` (`shiny/_connection.py`) discards everything. The
  tests here monkeypatch `shinyreact.send_message`, which reaches the app only because
  `shiny_app.py` calls it through the module (`shinyreact.send_message(...)`), never as an
  imported name (`test_shiny_server.py`, `test_tag_sessions.py`; tagging part A, Task 7).
- **Event inputs with a `null` default.** The skill's action-button pattern reads its event with
  `ignore_init=True`, which suits a counter starting at 0. For a request mounted with a `null`
  default, `ignore_init` can swallow a first request that coalesced with the mount default and
  so reached the server as the input's first value. Shiny's default `ignore_none=True` skips the
  `null` instead (`reactive/_reactives.py`, `event`). `tag_request`, `refresh` and
  `snippet_request` use it (tagging spec §5;
  `test_a_request_that_is_the_inputs_first_value_still_runs`).
- **Long work behind an input.** The skill mentions `send_message` in one line ("for things that
  are events, not state") and gives no pattern for slow work that an input starts. An effect or
  an output that awaits that work holds Shiny's process-wide reactive lock across the flush, so
  every window waits for it. That includes an output that awaits a slow open before it draws:
  the Library did, until Task 7's council. A `reactive.extended_task` is the way out.
  - It runs outside the lock, refuses reactive reads, and queues a second invocation behind a
    running one.
  - When it ends, its `status` changes under the lock, with a flush, so an output that reads
    `status()` is drawn again then.
  - Its `send_message` still resolves the message id in the right namespace, a context variable
    the task inherits (`shiny/_namespaces.py`).

  Checked in shiny 1.8.0's `reactive/_extended_task.py` and `session/_session.py`, and
  shinyreact's `_send_message.py` (Task 7).
