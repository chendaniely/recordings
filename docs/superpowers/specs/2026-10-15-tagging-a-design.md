# Tagging, part A: applying tags (design)

- **Date:** 2026-10-15
- **Status:** agreed with Dan in conversation on 2026-10-15. This file awaits his review.
- **Parent:** `docs/superpowers/specs/2026-10-08-recordings-design.md` (the main spec). This
  spec builds part of its stage 3a: §12.2 (Tagging) and the grid of §12.3 (Tags page). Section
  10 lists the changes to the main spec.

## 1. What this is for

**The first job is triage** (Dan, 2026-10-15). The `audio-router` import will bring in a
backlog of recordings, most of them untagged, and they need tags quickly. Day-to-day tagging of
new recordings comes second. Reorganizing existing tags (rename, move, delete) is part B, a
separate spec.

Part A is built **ahead of stage 2b** (the Plaud sync), as the first part of stage 3a.

**Done means:**
- In the Tags grid, with Untagged only, hundreds of recordings can be tagged in minutes, by
  keyboard or mouse.
- A recording gets several tags from the **+** next to its chips without reopening anything.
- A selection of recordings can be dragged onto a tag in the sidebar.
- Every change can be undone. Nothing private becomes visible to external agents without a
  confirmation.
- It works the same way in the real app, in `make demo` (saved to the demo's temporary copy),
  and on the Pages demo (kept in memory, lost on reload).

## 2. Scope

**In part A:**
- the `RemoveTags` operation, plus Undo for both operations
- the tag service and its two stores (§5)
- Finder-style selection in the Library list, with rows rebuilt to hold their own controls
  (the main spec's `<div>`-inside-`<button>` fix, for `RecordingList`)
- the tag picker: the **+** on rows and in the header, and `T`
- **×** on chips
- dragging onto sidebar tags and note types, and onto **+ New tag…**
- Undo toasts, and the privacy confirmation
- the Tags page grid (§7)

**Left for later:**
- **Part B (its own spec):** the Tags page tree, Rename, Move and Delete across recordings with
  a preview, and the tag rules form and its YAML view (`tags.yaml`).
- **The rest of stage 3a, unchanged:**
  - the Library on `index.db`, with search
  - the review queue
  - outside-edit detection in the UI, beyond reporting a refused write
  - the catalog, change log, JSON API and CLI
  - live updates across browser windows
  - the `?` help sheet
  - the `TranscriptTab` half of the `<div>`-inside-`<button>` fix
- **Stage 4:** the tag gate. Tagging records tags only; a first tag starts nothing yet.

## 3. Decisions (Dan, 2026-10-15)

1. **Order:** triage first, then day-to-day tagging, then reorganizing (part B).
2. **The privacy confirmation:**
   - Adding `private` or a `private/…` tag is always one action, with Undo.
   - A request that would remove the **last** private tag from any recording asks first, naming
     how many recordings. That includes clearing all of a private recording's tags, and an Undo
     that takes `private` away.
   - Tagging an untagged recording never asks, although §7.4 counts untagged as hidden from
     external agents: choosing its tags is the triage decision.
   - Everything else is Undo-only, as §12.2 says.
3. **The +** goes on every row, after its chips, and in the recording header. Chips get an ×.
4. **The Pages demo tags in the browser.** Nothing is saved there. Real saving happens in
   `make demo` and the real app.
5. **The write path is approach A:** tag requests over Shiny's existing connection, through one
   tag service with two stores. Rejected:
   - **B, a JSON API for writes:** Pages can't run the FastAPI layer.
   - **C, browser-held tags synced in batches:** the browser and the archive could drift, and
     edits made outside the app would fight with it.
6. **"Sort"** in Dan's request is read as ordering the rows of the grid (§7).

## 4. The core: operations and the privacy rule (`packages/core`)

- **Operations are pure:** each takes a `Recording` and returns the changed one. On the real
  archive they run through `Writer.mutate`, under the recording's lock, with `rev` and the
  refusal of a file edited outside the app (main spec §6.4).
  - **`AddTags(tags)`** exists. A tag the recording already has is left as it is, `by` included.
  - **`RemoveTags(tags)`** is new. A tag the recording doesn't have is skipped. Removing an
    auto-picked `notes/<type>` (`by: auto`) also adds `<type>` to `excluded_note_types`, so
    stage 4's auto-pick never adds it back (main spec §7.3).
- **Tags added in the UI are `by: you`.** Any tag can be removed, `auto` ones included.
- **Undo is the exact inverse, recording by recording.**
  - Each applied operation reports its effect on each recording: the `TagRef`s it gained, or
    the ones it lost (each with its original `by`), and any exclusion it added.
  - Undo applies the inverse of that effect only. A recording that already had a tag before an
    add keeps it after Undo. A restored tag keeps its original `by`. An exclusion added by the
    remove is taken away again.
- **Names:**
  - Tags use `TagRef`'s existing validation: no empty or space-padded folder parts, and `/`
    makes folders.
  - Tags are compared exactly. The picker matches case-insensitively and offers the existing
    spelling (§6).
- **The privacy rule:** `removes_last_private(before, after)` is true when `before` has a
  private tag and `after` has none. It uses `recordings.models.is_private_tag`, the archive's
  single rule.

## 5. The server: the tag service (`recordings_ui/tagging.py`)

- **Two stores behind one small interface** (read a recording, apply an operation and return
  its effect):
  - **`ArchiveStore`,** for the real app and `make demo`, applies operations with
    `Writer.mutate`.
  - **`MemoryStore`,** for Pages, applies the same operations to an in-memory copy of the demo
    archive.
- **One writer thread per process.** It owns the store's `Writer`, the one instance that
  everything in the process shares. That instance is required for two reasons:
  - the lock code's re-entrancy is per instance (see `write_docs_at_startup`)
  - a SQLite connection can't be shared across threads

  Every request from every window is queued to this thread, so the event loop never blocks on
  file writes. Pyodide has no threads, so on Pages the `MemoryStore` runs inline. It only
  touches memory.
- **Demo mode** creates a temporary state folder next to its temporary archive copy, with the
  `State` a `Writer` needs, and removes it at exit along with the copy, SIGTERM included (main
  spec §17).
- **`can_tag`:**
  - At start, the service opens its store. The library view carries `can_tag` and, when
    false, a `cannot_tag_reason` in plain words.
  - Tagging fails, for example, on the NAS mirror's copy, an archive without its sentinel, an
    unreadable or foreign `state.db`, or another machine's `writer_id`.
  - On Pages it is true. The view also carries `saves`, which is false only there, and the UI
    then shows "Demo: changes aren't saved".
- **The request** is an event input, sent with no debounce:
  ```json
  {"request_id": "<random>", "action": "add" | "remove" | "undo",
   "ids": ["…"], "tags": ["…"], "undo": "<token>", "confirm": false}
  ```
  - The server ignores the input's value at session start (`ignore_init`). Shiny re-sends
    input values when a window reconnects, and that must not re-apply a request.
  - Each `request_id` is applied at most once per process. A bounded set of the recent IDs
    is kept.
  - The browser makes `request_id` with `crypto.getRandomValues`. `crypto.randomUUID` needs a
    secure context, and the app may be served over plain HTTP on the tailnet.
- **The reply** is a one-off message (`send_message` → `useShinyMessageHandler`, which the
  shinyreact skill documents), sent as `tag_result`:
  ```json
  {"request_id": "…", "status": "done" | "confirm" | "failed" | "progress",
   "changed": 12, "unchanged": 3, "exposes": 0, "done_so_far": 0, "total": 15,
   "failures": [{"id": "…", "reason": "…"}], "undo": "<token>",
   "rows": [], "counts": {}, "tags": [], "note_types": []}
  ```
  - `exposes` is the number of recordings that would lose their last private tag.
  - `rows` holds the changed library rows. `counts`, `tags` and `note_types` are the library
    view's fields of the same names, recounted.
- **Checks, before anything is applied:**
  - Every ID must match the recording ID pattern and exist.
  - Every tag must validate.
  - There are at most 20 tags per request.
  - There is a sanity cap of 20,000 IDs per request.
  - **The confirmation gate:** if the request would remove the last private tag from any
    recording and `confirm` isn't true, nothing is applied. The reply is `confirm`, with the
    number in `exposes`. The gate is in the server, so the browser can't skip it.
- **Applied recording by recording, not all-or-nothing.** Each recording gets its own
  `mutate`.
  - A refused recording becomes a failure with a plain reason. The others are still applied.
  - Large batches send `progress` replies as they go.
  - This runs safely beside an import, which takes the same per-recording locks.
- **Undo tokens** live in the session: the last 20 actions, each holding its per-recording
  effects. An Undo is a request like any other, through the same gate (§3, decision 2).
- **Deltas, not reloads.**
  - The service keeps its session's library view and patches only the changed recordings'
    rows. It then recounts `counts`, the tag list and the note types from the rows, which is
    cheap. The reply carries just those changes.
  - The browser merges them into the `library` value it holds, and the full `library` output
    is sent only on load.
  - If the open recording changed, the `recording` output refreshes, which is one recording.
  - **Both views read recordings through the store,** not straight from the `Archive`. On Pages,
    the open recording then shows its in-memory tags, not the files'.
- **Logs** carry IDs, actions and reasons, never a title (the archive rules in CLAUDE.md).
- **Python here must parse as 3.12,** because the Pages demo vendors `recordings_ui` into
  Pyodide. ruff's `target-version` enforces this.

## 6. The browser: selection, picker, chips, drag and drop, toasts

- **Rows use the ARIA grid pattern,** in the Library list and the Tags grid alike. Rows are no
  longer `<button>`s, so a row can hold its own controls.
  - The list is a single tab stop.
  - Up and Down move between rows. Left and Right move across the focused row's own controls
    (its chips' × and its +).
  - Space toggles the row's selection, Shift+Up and Shift+Down extend it, and Enter opens the
    row.
  - With a mouse:
    - Click selects one row and opens it.
    - Cmd or Ctrl-click adds or removes a row.
    - Shift-click selects a range from the last plain click.
    - A checkbox is shown on hover, or once anything is selected.
  - Esc clears the selection. Cmd-A selects every row in the current filter, including rows
    not drawn on screen.
  - With two or more rows selected, a bar shows "12 selected · Tag… (T) · Clear".
  - Changing the filter drops hidden rows from the selection.
- **Rows stay put.** A row whose tags change keeps its place until **Refresh** or a filter
  change, in the list as in the grid. In Untagged, a tagged row stays, dimmed and ticked, so
  ranges and the cursor don't jump.
- **Rows are drawn virtually** (TanStack Virtual), because the backlog may run to thousands.
- **Chips** on rows and in the header get an **×**. It shows on hover or focus, and always when
  the device can't hover. It removes that tag from that one recording.
- **The +** after a row's chips opens the picker for that row only. In the header, it targets
  the open recording. **`T`** opens the picker for the selection, or for the open recording
  when nothing else is selected. A row's own controls always act on that row; the selection is
  acted on with `T`, the selection bar, or drag and drop.
- **The picker** (main spec §12.2):
  - Its title names the target, for example "Tag 12 recordings".
  - **Ranking:** an exact case-insensitive match comes first, then tags whose folder parts
    start with the typed text (`course` → `school/course-101`), then any other substring
    match. **Create "…"** comes last, and is hidden when the exact name exists. So Enter on a
    partial name picks the existing tag.
  - **Enter only ever adds.** It adds the highlighted tag, clears the box and keeps the
    picker open for the next tag. Esc closes it.
  - A tag every target has shows ✓, and one only some targets have shows –. Removing from the
    picker is a separate × on the ✓ or – item, never Enter.
  - Private tags are grouped under a lock.
- **Drag and drop:**
  - Drag a row, or the selection if the row is in it, onto a tag or note type in the sidebar
    to add it. The drag image reads "12 recordings".
  - Dropping onto **+ New tag…** (at the end of the sidebar's Tags group) opens the picker for
    those recordings.
  - It uses the browser's native drag and drop, with no new library. Phones, which don't
    support it, use the picker.
- **The request queue:** the browser sends one request at a time and queues the rest, because
  shinyreact merges input values set in the same tick. A chip or cell with a request in flight
  is dimmed.
- **Toasts:**
  - Each change shows one, for example "Added `school/course-101` to 12 recordings · Undo",
    for about 8 seconds, paused while hovered.
  - A batch shows progress ("Tagging 1,200 of 2,000…").
  - Partial failures list their reasons and stay until dismissed.
  - On Pages, the toasts carry "Demo: changes aren't saved".
- **The confirmation dialog** opens only when the server replies `confirm`.
  - It reads: "Make 3 recordings visible to external agents? Removing their last private tag
    lets Claude Code and other external agents read them."
  - Cancel has focus and applies nothing. **Make visible** resends the request with
    `confirm: true`.
- **When `can_tag` is false,** the + and × controls, drag and drop and `T` are disabled, and
  the reason shows as a tooltip and once in the list header.
- **Shortcuts** (`T`, Cmd-A, Esc, Space) work only when focus is in the list or the grid,
  never in a text box.
- **Components:** shadcn's Popover and Command for the picker, Sonner for toasts, and Alert
  Dialog for the confirmation. TanStack Virtual draws the rows. Each is checked against the
  shadcn skill or its docs before it is added (CLAUDE.md). Each must share the app's one React
  (`window.shinyreact.React`).

## 7. The Tags page grid

- **Where:** **Tags** joins the top bar between Library and Status, at `#/tags`. The grid
  reads the `library` data that `App` already holds (CLAUDE.md: every output is subscribed in
  `App`).
- **Layout:**
  - One row per recording. A pinned first column holds the selection checkbox, the title and
    the date.
  - One checkbox column per tag, `notes/*` included.
  - Private tag columns are grouped at the right, under a lock.
  - The header row stays pinned while scrolling, and rows are drawn virtually.
- **A click anywhere in a cell** toggles that tag on that recording. Removing a last private
  tag goes through the confirmation like anything else.
- **A tag column's header** adds the tag to the selected rows ("Add to 12 selected"). With no
  selection, it shows a hint instead.
- **Sorting rows:**
  - A menu on the first column offers Newest (the default), Oldest, Title and Duration.
  - Each tag column's menu offers "Has this tag first".
- **Triage:**
  - **Untagged only** is on by default, with a count ("37 left").
  - A title filter narrows the rows, in the browser.
  - Tagged rows stay, dimmed and ticked, until **Refresh**.
- **The snippet column** (Untagged only):
  - It shows the first ~160 characters of the first Plaud note, or else of the chosen
    transcript.
  - It is fetched only for the rows on screen, through a `snippets` request and reply like
    tagging's, and cached in the browser.
- **Columns:**
  - By default the grid shows the 15 most-used tags, counted across all tags, plus every tag
    used in this session. Private columns among them still sit at the right.
  - A column picker, by folder, shows or hides the rest. Its choice is kept in this browser
    (`localStorage`, read and written inside try/catch).
  - A tag created from the grid gets its column straight away.
- **Keyboard:**
  - Arrows move between cells, and Space toggles the focused cell.
  - The pinned column's checkbox selects rows, and `T` opens the picker for the selection.
- **Phones** scroll the grid sideways. The list and the picker are the phone path.

## 8. Errors

- **A refused write is a failure for that recording,** shown in the toast with its reason and
  logged without a title. The reasons:
  - edited outside the app: run `recordings reindex`
  - the file doesn't validate
  - the archive isn't writable here
  - the recording is gone
- **A bad request** (unknown ID, invalid tag, over a cap) is refused whole, with a plain
  message, before anything is applied.
- **A lost connection:** queued requests are dropped, and the toast says so. After a reconnect
  or a reload, the library is loaded fresh. A request already sent was either applied once or
  not at all (§5, `request_id`).
- **A server error inside the service** fails that request only, with "Couldn't tag: <reason>".
  The session stays up.

## 9. Testing

- **The core:**
  - `RemoveTags`, including the exclusion an auto `notes/*` tag adds.
  - Exact inverses: Undo after a partial overlap, a restored `by`, a removed exclusion.
  - `removes_last_private` across capitalisations (`Private/x`): true for removing the last
    private tag and for clearing every tag, false for tagging an untagged recording.
- **The service,** with both stores running the same parametrised tests on synthetic archives
  (`init_archive` and the `writer` fixture, never real data):
  - validation and the caps
  - the confirmation gate: nothing is applied without `confirm`, even for a hand-crafted request
  - partial failure: one recording edited outside the app is refused, and the rest are applied
    and undoable
  - at-most-once `request_id`s, and that the session-start value is ignored
  - the delta contents, and that the full library view is never rebuilt for a change
  - each `can_tag` reason: the mirror marker, no sentinel, a foreign `state.db`, another
    `writer_id`
- **The writer thread:** two sessions' requests are serialised, and a slow store doesn't hold
  up another session's outputs.
- **Vitest:**
  - picker ranking
  - the selection model: click, Cmd-click, Shift ranges, Cmd-A, pruning on a filter change
  - the request queue: one in flight, in order
  - merging deltas
  - that shortcuts don't fire in text boxes
- **e2e (`make e2e`, server demo):**
  - several tags from a row's +, with the picker staying open
  - × removes a tag
  - dragging a selection onto a sidebar tag
  - `T` on a selection
  - a grid cell toggle, and a header applied to the selection
  - Untagged-only rows staying until Refresh
  - Undo restoring exactly
  - removing a last private tag: the dialog, and Cancel applying nothing
  - `can_tag` false disabling the controls
  - the files on disk changed as expected (read through `Archive`: tags and `by` only)
  - a clean console
  - the stale-binding test still passing
- **Pages (`make pages-test`):** tagging works in the browser, a reload brings the demo back
  unchanged, and the console stays clean.
- **Accessibility:** a keyboard-only run through the grid, and the roles and `aria-checked` on
  cells.

## 10. Changes to the main spec

The plan's first task applies these to `2026-10-08-recordings-design.md`, each marked
"(Dan, 2026-10-15)":

- **§12.2:**
  - add the privacy confirmation as a third exception to "no confirmation dialogs" (§3,
    decision 2)
  - add **+** on rows, × on chips, and that Enter in the picker only ever adds
- **§12.3:**
  - the header click versus the sort menu
  - Untagged only by default
  - snippets for the rows on screen
  - the default columns
  - "rows stay put" now covers the Library list too
- **§17 and §17.1:**
  - demo mode's temporary state folder serves a `Writer`
  - the Pages demo tags in memory, unsaved
- **§20:** part A of tagging moves ahead of 2b, part B follows it, and the rest of 3a is
  unchanged.

## 11. To check during implementation

These are verified when they're first used, not assumed:
- Sonner, cmdk (shadcn's Command), Radix Popover and Alert Dialog, and TanStack Virtual each
  work with React external.
- Shinylive delivers `send_message` on Pages.
- Playwright's `drag_to` drives native HTML5 drag and drop.
- The demo's temporary `State` accepts the demo archive's UUID and a `writer_id`.
- The new Python modules parse as 3.12.
