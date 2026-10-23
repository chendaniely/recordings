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
`make e2e`. Re-check on every shinyreact bump. Also remove, from the built-bundle block in
`build.test.ts`, only the `react-dom.production` expectation and the test that compares the bundled
`react-dom`'s major.minor with `shinyreact.js`'s React (the e2e CI job runs that comparison).

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

## Radix DropdownMenu 2.2.1: where focus goes after an item is picked isn't documented

Found 2026-10-16, writing the Tags grid's sort menus (tagging part A, Task 14 and its fix round 1,
which moved them from a Popover to shadcn's DropdownMenu with radio items).

**What the docs say.** Radix's DropdownMenu page (reached through
`npx shadcn@4.21.4 docs dropdown-menu`) says only that Esc closes the menu and moves focus to the
trigger. It doesn't say where focus goes when an item is picked. It also doesn't say that the
trigger opens on `pointerdown`, not on `click`. Its Popover page has the same gap for a close
other than Esc.

**What Radix does.**
- The content's `onCloseAutoFocus` focuses the trigger on every close, unless the close came from
  an interaction outside the menu (`@radix-ui/react-dropdown-menu` 2.2.1, `dist/index.mjs`).
  Popover 1.3.1's non-modal content does the same.
- The trigger opens on a left-button `pointerdown` without Ctrl, and on Enter, Space or ArrowDown.
  A handler passed in that calls `preventDefault` stops it, through `composeEventHandlers`.

**What relies on it.**
- `TagsPage.tsx`'s `RowSortMenu` and `ColumnMenu` close when an item is picked, and focus must go
  back to their header button, so the grid's keys go on working. The e2e test
  `test_the_sort_menus_reorder_the_rows_and_say_which_column_is_sorted` checks it.
- Their buttons prevent ArrowDown, so Down moves to the rows from every header control, as the
  grid's keys do.
- Nothing works around either behaviour.

**When this note can go.** When Radix's docs say both. Re-run that test on every `radix-ui` bump.

## Sonner 2.0.8, tw-animate-css 1.4.0 and Tailwind v4: reduced motion and forced colours

Found 2026-10-16, making tagging part A work with `prefers-reduced-motion` and forced colours
(Task 15).

**What the docs say.** Neither Sonner's docs (context7 `/emilkowalski/sonner`) nor
tw-animate-css's README says what happens under `prefers-reduced-motion`. Tailwind's docs say
`outline-hidden` keeps an outline in forced colours mode, but not what that does to a list whose
items all carry it.

**What they do.**
- Sonner's stylesheet stills each toast under `prefers-reduced-motion` (`[data-sonner-toast]` and
  its children get `transition: none; animation: none`), but not the toaster's own lift
  (`[data-sonner-toaster] { transition: transform .4s }`).
- tw-animate-css's `animate-in` and `animate-out`, which shadcn's Popover, Dialog, Alert Dialog
  and DropdownMenu use, run whatever the preference.
- In forced colours, `outline-hidden` (Tailwind v4) gives every shadcn `CommandItem` a 2px
  outline, so the picker's highlighted item, whose ring is also an outline, can't be told apart.
  Box shadows, shadcn's rings and the open row's bar among them, are dropped.

**What relies on it, and the workarounds.** `app.css` stills the toaster, the toasts, the chips
and the overlays under `prefers-reduced-motion`, and under `forced-colors: active` takes the
outline off every picker item but the highlighted one, gives the dialogs and menus a border, and
draws the open row's bar as a border. The e2e tests
`test_reduced_motion_turns_off_overlay_and_toaster_animations` and
`test_forced_colours_keep_each_state_and_each_overlays_edge` check them.

**When this note can go.** When Sonner stills its toaster, tw-animate-css or shadcn's components
honour the preference, and shadcn's items stop outlining themselves in forced colours. Re-run
those tests on a bump of any of the three.

## floating-ui 1.8.0 (under Radix Popover): the visual viewport isn't documented

Found 2026-10-16, keeping the tag picker's + popover above a touch screen's on-screen keyboard
(tagging part A, Task 15 fix round 2).

**What the docs say.** floating-ui's `autoUpdate` page says `ancestorResize` updates the position
"when an overflow ancestor is resized", using the `resize` event. It doesn't say the visual
viewport is one of those ancestors, or that the default boundary is the visual viewport, not the
layout viewport.

**What it does** (`@floating-ui/dom` 1.8.0 and `@floating-ui/utils`):
- `getOverflowAncestors` adds `window.visualViewport` to the list `autoUpdate` listens to.
- `getViewportRect` measures the default `viewport` boundary with `visualViewport.width` and
  `height`.
- So a popover follows an on-screen keyboard by itself, and Radix's
  `--radix-popover-content-available-height` already leaves the keyboard out.

**What relies on it.** `TagPicker.tsx`'s `AddTagsButton` adds nothing for the keyboard. Fix
round 1 added the keyboard's height to the collision padding, which counted it twice: the
popover shrank to 56 px, or flipped off screen. The e2e test
`test_on_touch_the_plus_popover_keeps_its_box_and_an_option_above_the_keyboard` checks it on a
portrait tablet, a landscape tablet and a phone on its side.

**When this note can go.** When floating-ui's docs say both. Re-run that test on a `radix-ui` bump.

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

## Shinylive 0.8.12 (assets 0.10.15): the phone sheet inside its iframe

Local note only; nothing is reported upstream. The Pages demo runs the app in an iframe, and in a
subframe `100dvh` is the parent's large viewport height, and `window.visualViewport` doesn't
report the on-screen keyboard (see the floating-ui entry above). So on a phone the bottom sheet
can sit behind the keyboard. It degrades safely: the sheet is still reachable and scrolls, and
the Pages phone test (375×740, touch) checks the layout, the dialog and the top toast, but cannot
check a real keyboard. Dan should try the demo on a real phone once.

Delivery of `send_message` on Pages is confirmed: the Pages tagging test sees the reply land as a
toast and the chip leave "pending" (tagging part A, Task 16).

## Radix Dismissable Layer 1.2.1: a click stopped before the document counts as consumed

Local note only; nothing is reported upstream. Found 2026-10-22, by the built-bundle e2e test.

**What it does.** For a mouse press outside a layer, `usePointerDownOutside` waits for the click
(`deferPointerDownOutside`). Capture and bubble listeners on the document record whether the
click reached the document (`handleInteractionCapture`, `handleInteractionBubble`, `dist/index.mjs`
lines 216-240). If it was seen in the capture phase but never in the bubble phase, the interaction
counts as intercepted (`isOutsideInteractionIntercepted`, line 212) and the dismissal is skipped
(line 246). React's `e.stopPropagation()` stops the native event at the root container, so a
control that stops its click leaves any other open popover open.

**What we do.** No click that can reach another layer calls `stopPropagation`. The one exception
is the picker item's × (`TagPicker.tsx`): a click inside the open layer that must not reach its own
cmdk item (cmdk 1.1.1's Item selects on click, so without the stop a × click would also add the
tag). The + (`TagPicker.tsx`), the chip ×
(`TagChips.tsx`) and the grid's Open (`TagsPage.tsx`) used to; the row's and the pinned cell's
handlers now ignore a click inside `[data-control]` instead. The e2e tests
`test_the_built_bundle_portals_toasts_dialogs_and_scrolls` (a second + closes the first picker) and
`test_open_closes_an_open_columns_popover` check it.

**When it can go.** It isn't a workaround, only a rule (CLAUDE.md). Re-check the source lines on a
Radix bump, because the deferred handling is new.
