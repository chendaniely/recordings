# Tagging, part A: applying tags (design)

- **Date:** 2026-10-15
- **Status:** agreed with Dan in conversation on 2026-10-15, then revised after a four-lens
  council (reverse, forward, risk, UX and accessibility). Dan asked for the council's changes to
  go in, followed by implementation. §12 records each ruling.
- **Parent:** `docs/superpowers/specs/2026-10-08-recordings-design.md` (the main spec). This spec
  builds part of its stage 3a: §12.2 (Tagging) and the grid of §12.3 (Tags page). §10 lists the
  changes to the main spec.

## 1. What this is for

**The first job is triage** (Dan, 2026-10-15). The `audio-router` import brings in a backlog of
recordings, most of them untagged, and they need tags quickly. Day-to-day tagging of new
recordings comes second. Reorganizing existing tags (rename, move, delete) is part B, a separate
spec.

**When:**
- Part A is built **ahead of stage 2b** (the Plaud sync), as the first part of stage 3a.
- **The first real tag ends the disposable window** of main spec §19: until then, rolling back
  means moving the archive aside and importing again, and that would throw the tags away.
- So the code can ship before 2b. When to start tagging the real archive is Dan's call: after
  2b's spikes and `sync --dry-run`, or earlier, knowing that a rollback by re-import loses the
  tags. The runbook says so (§10).

**Done means:**
- In the Tags grid, with Untagged only, hundreds of recordings can be tagged in minutes, by
  keyboard or mouse.
- A recording gets several tags from the **+** next to its chips without reopening anything.
- A selection of recordings can be dragged onto a tag in the sidebar.
- Every change can be undone while the page is open.
- **No recording loses its last private tag without a confirmation.**
- It works the same way in the real app, in `make demo` (saved to the demo's temporary copy),
  and on the Pages demo (kept in memory, lost on reload).

## 2. Scope

**In part A:**
- `RemoveTags`, and Undo for both operations
- the tag service and its two stores (§5)
- Finder-style selection in the Library list, with rows rebuilt to hold their own controls (the
  `RecordingList` half of the main spec's `<div>`-inside-`<button>` fix)
- the tag picker: **+** on rows and in the header, and `T`
- **×** on chips
- drag and drop onto sidebar tags and note types, and onto **+ New tag…**
- Undo toasts, Cmd/Ctrl+Z, and the privacy confirmation
- **Refresh**
- the Tags page grid (§7)

**Left for later:**
- **Part B (its own spec):** the Tags page tree, Rename, Move and Delete across recordings with a
  preview, and the tag rules form and its YAML view (`tags.yaml`). Part B's operations use part
  A's gate (§4).
- **The rest of stage 3a, unchanged:**
  - the Library on `index.db`, with search
  - the review queue
  - outside-edit detection in the UI, beyond reporting a refused write
  - the catalog, change log, JSON API and CLI. Their bulk tag changes reuse part A's operations
    and gate, with `dry_run` as their confirmation.
  - live updates across browser windows
  - the `?` help sheet (until then, a one-line key hint, §6)
  - the `TranscriptTab` half of the `<div>`-inside-`<button>` fix
- **Stage 4:** the tag gate. Tagging records tags only; a first tag starts nothing yet.

The row model must work whether the Library reads `recording.json` files, as now, or `index.db`
later.

## 3. Decisions (Dan, 2026-10-15)

1. **Order:** triage first, then day-to-day tagging, then reorganizing (part B).
2. **The privacy confirmation:**
   - Adding `private` or a `private/…` tag is always one action, with Undo.
   - A request that would remove the **last** private tag from any recording asks first, naming
     how many recordings. That includes clearing all of a private recording's tags, and an Undo
     that takes `private` away.
   - Tagging an untagged recording never asks, although main spec §7.4 counts untagged as hidden
     from external agents: choosing its tags is the triage decision. The toast says how many
     recordings became visible (§6), to inform, not to ask.
   - Everything else is Undo-only, as §12.2 says.
3. **The +** goes on every row, after its chips, and in the recording header. Chips get an ×.
4. **The Pages demo tags in the browser.** Nothing is saved there. Real saving happens in
   `make demo` and the real app.
5. **The write path is approach A:** tag requests over Shiny's existing connection, through one tag
   service with two stores. Rejected:
   - **B, a JSON API for writes:** Pages can't run the FastAPI layer.
   - **C, browser-held tags synced in batches:** the browser and the archive could drift, and
     outside edits would fight with it.
6. **"Sort"** in Dan's request is read as ordering the grid's rows (§7).
7. **Dan asked for the council's changes, then implementation** (§12).

## 4. The core: operations, effects and the privacy rule (`packages/core`)

- **The operations live in `recordings/tagops.py`.** That module imports only
  `recordings.models`, because the Pages demo can't import `sqlite3` or `fcntl` (stage-2a
  decision 6). `writer.py` re-exports `AddTags`.
- **The operations are pure:** each takes a `Recording` and returns the changed one. On the real
  archive they run through `Writer.mutate`, under the recording's lock, with `rev` and the
  refusal of files edited outside the app (main spec §6.4).
  - **`AddTags(tags)`** adds the tags the recording doesn't have, as `by: you` from the UI. A tag
    already there is left as it is, `by` included. Adding `notes/<type>` also takes `<type>` out
    of `excluded_note_types`.
  - **`RemoveTags(tags, may_expose=frozenset())`** removes the named tags; a tag the recording
    doesn't have is skipped. Removing any `notes/<type>`, `auto` or `you`, adds `<type>` to
    `excluded_note_types`, so stage 4's auto-pick never adds it back ("your choice wins", main
    spec §7.3).
  - **The privacy gate is inside the operation:** if the result would leave a recording that had
    a private tag with none, and the recording's ID isn't in `may_expose`, `apply` raises
    `WouldExpose`. `mutate` then writes nothing. The gate decides on the state that is written,
    read under the recording's lock. Every operation that can remove a tag uses it, part B's and
    the later CLI's included.
  - **`RestoreTags(effect)`** is Undo's operation. It puts lost tags back at their original
    index, with their original `by`. It removes gained tags, and reverses the exclusion changes.
- **Effects are taken under the lock.** The store applies an operation through a wrapper op that
  records the `Recording` passed to `apply` (before) and the one returned (after). The effect,
  per recording, is their difference:
  - the tags gained
  - the tags lost, each with its index and `by`
  - the exclusions added and removed

  `mutate` itself is unchanged. A tag that another writer added between the browser's view and
  the write is therefore never in the effect, and Undo leaves it alone.
- **Names.** Removal accepts any existing spelling. A *new* tag must also meet these rules, which
  guard against the owner's accidental input (a paste, an autocorrect), not an adversary:
  - It is in Unicode normal form NFC, with no control, format, separator, private-use or
    surrogate or unassigned characters (categories Cc, Cf, Zl, Zp, Co, Cs, Cn), no other
    invisible code point (`HIDDEN_CODEPOINTS` in `models.py`: Hangul fillers, the Braille blank,
    variation selectors and the like; and the combining solidus overlays U+0337 and U+0338), no
    run of more than 3 combining marks, and no space other than U+0020.
  - Each folder part is at most 64 characters, and the whole tag at most 200.
  - No part is `.` or `..`, and there's no `\`.
  - `notes/<type>` names a type the archive already has, with `<type>` matching `[a-z0-9-]+`.
    The picker can't invent note types; they come from prompt files (main spec §7.3).
  - It doesn't **look like** "private" without being private. Two checks, both refused with "Did
    you mean `private`?":
    - A first part that reads `private` but isn't spelled `private` in plain letters, in any case.
      It is read after mapping the lookalikes of `/` to `/`, NFKC, dropping format characters and
      the hidden code points, `casefold()`, dropping accents, and mapping a small table of
      confusable letters (Cyrillic and Greek lookalikes of p, r, i, v, a, t, e, and the dotless
      i). So the fullwidth `ｐｒｉｖａｔｅ` is refused, although `is_private_tag` calls it private.
    - Any tag on which `is_private_tag` and the plain rule (the first folder, casefolded, is
      `private`) disagree. The app and the archive's own docs, which agents follow, therefore
      share one rule (main spec §7.4: "one function"). The tag service asks the same of a tag the
      archive already has, so a lookalike that got in by hand is never added again.
  - Tags are compared exactly. The picker matches case-insensitively and never offers a
    case-variant of an existing tag (§6).
- **`is_private_tag` hardens** (main spec §7.4 change, §10). It applies NFKC, and drops format
  characters and the code points in `HIDDEN_CODEPOINTS` (`models.py`: the Hangul fillers, the
  Braille blank, the Mongolian and ordinary variation selectors, and the supplementary variation
  selectors; not the solidus overlays, which only new tags refuse), before `casefold()`. A fullwidth solidus is a
  folder separator after NFKC. It still errs towards private, and it is still the one function.
- **`removes_last_private(before, after)`** is true when `before` has a private tag and `after`
  has none. The dialog splits those recordings in two:
  - **visible:** they keep other, non-private tags
  - **untagged:** they have no tags left, and stay hidden until their next tag (§7.4)

## 5. The server: the tag service (`recordings_ui/tagging.py`)

### Stores

- **`ArchiveStore`,** for the real app and `make demo`, writes with `Writer.mutate`. It imports
  `recordings.writer`, `recordings.state` and `recordings.index` only inside its constructor, as
  `status.py` does.
- **`MemoryStore`,** for Pages, holds the demo's `Recording` objects in memory.
  - It applies the same operations, through the same wrapper, then validates the result with
    `Recording.model_validate` on `model_dump(by_alias=True)`, as `mutate` does.
  - It never calls `dump_json`, never writes a file (Pyodide's file system included), and starts
    no thread (stage-2a decisions 6 and 7).
  - It is chosen only when running under Pyodide. Outside Pyodide the service never falls back to
    it, so `make demo` and the real app can't silently stop saving.
- **Reads:**
  - `ArchiveStore` reads go straight to the UI's `runtime.archive()`, on the event loop.
    `recording.json` is replaced atomically, so a read needs no lock, and reads never queue
    behind writes.
  - Only `MemoryStore` overlays reads with its in-memory recordings.

### The writer thread

- **One thread per process owns the process's one `Writer`.**
  - Lock re-entrancy is per `Locks` instance *and* per thread, so two instances nested on one
    thread deadlock until the timeout.
  - A lock must never be held across an `await`.
  - The event loop must never block on a file write.
  - Every window's writes are serialised.
- **The thread's `Writer.open(cfg)` replaces `write_docs_at_startup`,** so the process holds one
  instance. It writes the archive's docs as before.
- **The service's locks wait 2 seconds,** not the default 30. After 5 consecutive busy
  recordings, a batch stops with "busy".
- **The thread is a daemon.** At shutdown, a batch stops between recordings. Writes are atomic,
  so stopping mid-batch is safe.
- **The thread catches every exception per request.** If it ever exits, `can_tag` turns false
  with a reason, and queued requests fail.

### Demo mode

- **`create_app`** (never `settings.from_env`, which Pages also runs) calls
  `init.adopt_archive(copy, <tmp>/state, writer_id="demo")`.
- The writer thread then builds `Writer(copy, State.open(<tmp>/state), Index(<tmp>/index.db),
  writer_id="demo")` and writes the docs.
- The state, index and locks all live in the demo's temporary folder, beside the copy. They are
  removed at exit with it, SIGTERM included (main spec §17).
- Demo mode never reads the config, `RECORDINGS_STATE` or `RECORDINGS_INDEX`.

### `can_tag`

- The service opens its store at start. The library view carries:
  - `can_tag`
  - when it is false, `cannot_tag_reason`, in plain words, and `cannot_tag_mirror`, true when the
    archive is the NAS mirror's copy (the UI adds its advice from the flag, never by reading the
    words)
  - `saves`, which is false only on Pages, where the UI says "Demo: changes aren't saved"
- **Tagging is impossible on:**
  - the NAS mirror's copy
  - an archive without its sentinel
  - an unreadable or foreign `state.db`
  - another machine's `writer_id`
- **A failed open is retried** on Refresh, and on the next request after a transient failure such
  as a busy index. Its reason is a fixed phrase by class: "the archive's index or state.db was busy
  (<class>)" for a lock timeout or a `sqlite3` error, and "the archive couldn't be opened (<class>):
  check its permissions and the disk, then press Refresh" for an `OSError` (also retried on the next
  request), neither with the exception's text. A busy moment at start therefore doesn't disable tagging until a restart.

### Requests

- **The request is an event input,** `tag_request`. The browser mounts it once, in `App`, at
  startup, with a `null` default, no debounce and event priority:
  ```json
  {"request_id": "<random>", "action": "add" | "remove" | "undo",
   "ids": ["…"], "tags": ["…"], "undo": "<token>", "confirm_ids": ["…"]}
  ```
- **The effect** reads the input with `ignore_none`, not `ignore_init`. `ignore_init` can swallow
  a first request that coalesced with the mount default.
  - The effect only validates and enqueues, then returns.
  - The work runs as a per-session `reactive.extended_task`, outside Shiny's process-wide reactive
    lock. Awaiting a batch inside the effect would freeze every window.
  - The task applies the batch one recording at a time, with
    `await loop.run_in_executor(<writer thread>, …)`. That interleaves windows, so one window's
    20,000-recording batch doesn't hold up another window's single click.
  - It sends `progress` between recordings, and the final reply at the end.
  - Any reactive value it sets is set inside `reactive.lock()`, followed by a flush.
  - On Pyodide, the same task applies the batch inline, with no executor.
- **`request_id`:**
  - The browser makes it with `crypto.getRandomValues`. `crypto.randomUUID` needs a secure
    context, and the app may be served over plain HTTP on the tailnet.
  - The server records an ID once its request is applied or has failed, never when it is gated.
    A repeated ID is ignored. A bounded set of recent IDs is kept per process.
  - A `confirm` reply records nothing. The confirmed resend carries a new `request_id`, with the
    same `undo` token if it is an Undo.
- **Checks, before anything is applied:**
  - Every ID must parse as a recording ID (`recordings.ids.parse_id`, so an impossible date is
    refused too). Duplicates are removed. `confirm_ids` share the 20,000 cap.
  - An ID that matches but no longer exists, because it was moved or trashed, is a per-recording
    failure, "the recording is gone".
  - New tags must pass §4's rules. At most 20 tags per request.
  - `remove` with an empty `tags` is refused. There is no clear-all, and a bug must never turn
    into one.
  - At most 20,000 IDs per request, as a sanity cap.
- **The confirmation:**
  - A pre-pass, on the writer thread, reads the targets' files and counts the recordings that
    would lose their last private tag. If that set isn't already within `confirm_ids`, nothing is
    applied. The reply is `confirm`, with the counts and the IDs to confirm.
  - The pre-pass only counts. The operation's own gate (§4) decides, under the lock, on the state
    it writes. So a recording that newly qualifies between the dialog and the write, through
    another window, another request or an outside edit, fails as "not confirmed", and nothing
    else exposes it.
- **No request is ever sent on load, from the URL or its hash, or from storage.** The websocket's
  handshake check is the guard for these writes (main spec §15). It is enough only while nothing
  writes from a URL. No new HTTP route is added.
- **Shiny's reconnect stays off** (`allow_reconnect` isn't called). On `shiny:disconnected`, the
  browser drops its queue and says so.

### Applying and replying

- **Recording by recording, not all-or-nothing.** Each recording gets its own `mutate`, wrapped
  for every exception.
  - After an exception, the recording is re-read. If the change landed, which can happen when an
    fsync of the folder fails after the rename, it counts as applied.
  - A per-recording refusal becomes a failure with a fixed reason, and the batch goes on.
  - **An archive-level error stops the batch,** with "Stopped: <fixed phrase>":
    - `NotTheWriter`
    - a full or read-only disk, or an I/O error
    - a `sqlite3` error on `state.db`
- **The reply** is a one-off message, `tag_result`, sent through shinyreact's `send_message` to
  `useShinyMessageHandler`:
  ```json
  {"request_id": "…", "status": "done" | "confirm" | "failed" | "progress",
   "changed": 12, "unchanged": 3, "done_so_far": 0, "total": 15,
   "loses_private": {"visible": 2, "untagged": 1, "ids": ["…"]}, "now_visible": 0,
   "failures": [{"id": "…", "reason": "…"}], "undo": "<token>",
   "rows": [], "counts": {}, "tags": [], "note_types": []}
  ```
  - **Every** reply that ends a request, `failed` included, carries `rows` and an `undo` token
    covering the recordings already written.
  - `now_visible` counts the recordings that went from untagged to tagged without a private tag.
  - `rows` are the changed library rows. `counts`, `tags` and `note_types` are the library view's
    fields of the same names, recounted from the session's rows. `tags` carries each tag's
    `private` flag, and is the browser's only source for it: the browser never re-implements the
    privacy rule.
- **Undo tokens:**
  - The session keeps the last 20 actions, each with its per-recording effects.
  - A token is used up when its Undo is applied and nothing is left to settle. A second use
    replies `failed`, "already undone". A gated attempt doesn't use it up.
  - **A partly applied Undo keeps its token for what it didn't settle.** A recording where it
    failed (busy, say), or that the batch stopped before reaching, stays under the same token,
    so "try again" can reach it. A recording that is gone counts as settled, because nothing more
    can be undone there.
  - **The browser's side of the rule:** the Undo's token leaves the Cmd/Ctrl+Z stack when the
    reply is `done` with no failure but "gone", or `failed` with "already undone" or "Undo is no
    longer available"; any other failure, "busy" included, keeps it. The Undo's reply carries a
    token of its own, offered as **Redo**; Redo tokens are never on the Cmd/Ctrl+Z stack, so
    pressing it twice undoes two actions, never an undo and then its redo.
  - An unknown token, for example after a reload, replies `failed`: "Undo is no longer available
    (the page was reloaded)".
  - An Undo is a request like any other, through the same gate.
- **The record.** `revisions.origin` in `state.db` becomes `<op>:<request_id>`. A batch stopped
  by a crash or `docker stop` still leaves a record of the recordings it changed, even though the
  session's Undo is gone.
- **Deltas, not reloads.**
  - The service keeps each session's library view and patches only the changed rows, from the
    effects' `after` recordings.
  - The browser merges `rows` into the `library` value it holds. The full `library` is sent only
    on load and on Refresh.
- **Refresh** is an event input, `refresh`, and `library()` depends on it alone. The server
  rebuilds the session's view from the store and sends it once. The browser drops its merged copy
  and re-applies its filter and sort. Undo tokens survive a Refresh, though not a reload.
  Recordings that an import added meanwhile appear.
- **The header's chips read the open recording's row** in the merged `library` value. The
  `recording` output isn't re-sent for a tag change, because it carries the whole transcript.
- **Reasons and logs are fixed phrases,** chosen by exception class, as in
  `audio_router._FAILURES`. They're never exception text, which can carry a title.
  - The `NotTheWriter` reasons shown for `can_tag` are the only exception text shown. They are
    written for display, and carry paths, not titles.
  - Logs record the exception's class name, IDs, the action and tag *counts*. They never record
    tag names (a `private/<person>` tag can say as much as a title), titles, or `str(exc)`.
  - The effect catches every exception, so a tagging error never closes the session.
- **The Python here must import on Pyodide,** not only parse as 3.12. ruff's `target-version`
  covers the syntax, and the Pages test proves the import.

### Snippets

- **`snippets` is a request with its own reply,** like tagging's. It reads on a separate
  one-thread reader, never the writer thread, at most one request at a time per session, and at
  most 200 IDs per request.
- For each recording it reads only the chosen transcript, or else the first Plaud note. It stops
  at 160 characters and returns plain text, with Markdown stripped on the server.
- The browser caches snippets in memory only, never in `localStorage` or `sessionStorage`, and
  draws them as text nodes, never as HTML.

## 6. The browser: selection, picker, chips, drag and drop, toasts

### React and the build

- **shinyreact's `ReactDOM` global is `react-dom/client` only:** it has no `createPortal` and no
  `flushSync`. Radix (Popover, Alert Dialog, Tooltip), Sonner and TanStack Virtual call both.
- **So the build maps only `react` and `react-dom/client` to the globals.** `react-dom`, the
  index module, is bundled. It requires `react`, which stays external.
  - Its `createPortal` builds a plain element, so it works across copies.
  - Its `flushSync` runs the callback without forcing a synchronous flush. That is acceptable for
    these libraries; TanStack Virtual is set to `useFlushSync: false`.
- **This goes in `docs/upstream-issues.md`,** with how to tell when the workaround can go: when
  shinyreact's global gains `createPortal`.
- **Two tests check the built bundle.**
  - `src/build.test.ts` checks the config, and, once `make build` has run, the built `www/ui.js`:
    the IIFE is called with `window.shinyreact.React`, it carries no `react`, `react-dom/client`
    or `scheduler` production banner, and its `react-dom` has the same major.minor as the React
    in `shinyreact.js`. CI runs the config check and the IIFE and banner checks in the `frontend` job (which builds first), and
    the React-version check in the `e2e` job, the only one with a `.venv`; where a piece is
    missing the tests skip locally and fail under `CI`.
  - The e2e test `test_the_built_bundle_portals_toasts_dialogs_and_scrolls` runs with a clean
    console (any console error or uncaught exception fails it):
    - the picker opens on screen, closes on an outside click, and closes on Esc with focus back
      on its +
    - opening a second row's + closes the first picker
    - a toast appears
    - the confirmation dialog opens, and its Cancel gives the page its pointer events back
    - a list longer than the viewport scrolls to its last row with End
- **shadcn's Sonner template imports `next-themes`.** That is dropped, and the app's own resolved
  theme (`lib/theme.ts`) is passed in instead.
- **The `tag_result` and `snippets` handlers, the request queue and the toasts live in `App`,**
  which never unmounts. shinyreact drops a message that has no handler. This is the message-side
  twin of "every output is subscribed in `App`".

### Rows, selection and focus

- **Both the Library list and the Tags grid are ARIA grids:** `role="grid"`,
  `aria-multiselectable="true"`, and `aria-rowcount` (every row in the filter, plus the header).
  - Each rendered row carries `aria-rowindex` and `aria-selected`. The open row also carries
    `aria-current="true"`.
  - The Tags grid also carries `aria-colcount` and `aria-colindex`.
  - Rows are no longer `<button>`s, so each can hold its own controls.
  - In the list, a row's checkbox is decorative (`aria-hidden`, `tabindex=-1`), because
    `aria-selected` carries the state.
  - × is a button named "Remove tag <tag> from <title>", and + is named "Add tags to <title>".
- **The keyboard follows Finder:**
  - Up and Down move focus **and** select only the focused row. In the Library, the pane follows
    after a short pause, as Mail's does.
  - Shift+Up and Shift+Down extend the selection from the anchor.
  - Cmd/Ctrl+Up and Cmd/Ctrl+Down move focus without changing the selection, and so do
    Cmd/Ctrl+Home and Cmd/Ctrl+End, in the list and the grid. Cmd/Ctrl+Space toggles the focused
    row, and so does **`X`**: on a Mac, Spotlight and the input-source switcher usually take
    Cmd+Space and Ctrl+Space before the page sees them.
  - Page Up and Page Down move a screenful. In the list, Home and End go to the first and last
    row, and Shift extends the selection with each of them. In the grid, Home and End stay in the
    row (first and last cell), and Ctrl/Cmd+Home and Ctrl/Cmd+End (with or without Shift) only
    move focus to the first and last row, never the selection.
  - Enter opens the focused row.
  - Left and Right move across the focused row's own controls (its chips' × and its +). Up or
    Down from a control moves to the next row itself.
  - **Space is left to the player,** as main spec §12's key map says. The list never uses it. In
    the Tags grid, which has no player, Space toggles the focused cell.
  - The list is a single tab stop.
- **With a mouse:**
  - Selection changes on `click`, never on `mousedown`, so pressing a row that is in the
    selection and dragging it drags the whole selection.
  - Click selects one row and opens it.
  - Cmd/Ctrl-click adds or removes a row.
  - Shift-click selects anchor…row. Cmd-Shift-click adds that range.
  - The checkbox acts as Cmd-click, and as a range with Shift.
  - The anchor moves on every plain click, Cmd-click and checkbox click.
  - Clicking × or + never changes the selection or opens the row. A click on a chip's label is a
    row click.
  - Rows are `user-select: none`.
- **A checkbox** shows on hover, once anything is selected, and always on devices that can't
  hover. Its column is reserved, so nothing shifts when it appears.
- **Esc** closes only the topmost layer. The list clears its selection only on an Esc that
  nothing else handled (`event.defaultPrevented` is false). Cmd-A selects every row in the
  current filter, including rows not drawn.
- **The selection bar** appears with two or more rows selected: "12 selected · Tag… (T) · Clear
  (Esc)". **The selection stays after an action,** so a second tag can follow, and the bar then
  adds "just tagged `work`".
- **A filter change** drops hidden rows from the selection and announces it ("9 hidden rows
  deselected").
- **Rows are drawn virtually** (TanStack Virtual), because the backlog may run to thousands.
  - The virtualiser's `rangeExtractor` always keeps these rows rendered, composed with
    `defaultRangeExtractor`:
    - the focused row
    - the selection anchor
    - a row being dragged
  - Moving focus calls `scrollToIndex`, so the focused row is always in view.
- **Where focus returns:**
  - When the picker, the confirmation or a menu closes, it returns to the row or cell that had it.
  - After × removes a chip, it moves to the next control in the row (the next ×, or the +).
  - After a filter change, the list's tab stop moves to the first row. DOM focus stays where it
    was, in the filter control, so a second filter click or arrow key still works.

### Row states, and rows staying put

- **Each row state is distinct without colour:**
  - **focused:** the focus ring
  - **selected:** the `--sel` tint and the visible checkbox
  - **open:** the 3px inset bar that `.row.on` uses now
  - **done:** the `--muted` row background and a "✓ Tagged" label in `--muted-foreground`, with
    text at full colour. Never `opacity`, which drops muted text below 4.5:1 (main spec §12.6).
- **Rows stay put.** A row whose tags change keeps its place, marked done, until **Refresh** or a
  filter change, in the list as in the grid. The same goes for a row that no longer matches its
  filter, in any filter.
- **An Undo puts the rows it touched back into the current view.**
- **Pending changes show at once.** A chip or cell with a request in flight shows its new state
  straight away, with a pending marker (a dashed outline in `--muted-foreground`). It reverts if
  the request fails or needs confirmation.
- **A failed row** carries a ⚠ with its reason, as text and in its accessible name, until Refresh.
- **Refresh** is `R`, and a button in the list header and the grid header ("5 tagged · Refresh").
  It sends `refresh` (§5).

### Chips, +, `T` and the picker

- **Chips** on rows and in the header get an **×**.
  - It shows on hover or focus, with a hit area of at least 24×24px, padded beyond the glyph.
  - It removes that tag from that one recording.
  - On devices that can't hover, list rows show no ×. Tags are removed there from the recording
    header, with 44px targets, or from the picker.
- **The +** after a row's chips opens the picker for that row only. In the header, it targets the
  open recording. A row's own controls always act on that row.
- **`T`** opens the picker for the selection you can see; with nothing selected, for the focused
  row. Moving focus with Cmd/Ctrl adds no row to the selection, so `T` then targets only the
  selection. (An explicit selection beats an unselected focus ring, as in Explorer and Gmail; the
  bar already says "N selected · Tag… (T)". To tag the focused row too, select it first with
  Cmd/Ctrl+Space or `X`; plain Space does nothing in the list.) The dialog's title names the count. The selection bar's **Tag…** does the same
  as `T`.
- **The picker is one of two forms:**
  - opened by `T`, the selection bar or a drop: a centred dialog, built from shadcn's `Dialog`
    parts around one `Command`, not from `CommandDialog` (so the page has one cmdk root)
  - opened by a +: a Popover on that +, whose row stays rendered

  Its title names the target, for example "Tag 12 recordings". On close, focus returns to where
  it came from.
- **Ranking:**
  1. an exact case-insensitive match
  2. tags whose folder parts start with the typed text (`course` → `school/course-101`)
  3. any other substring match
  4. **Create "…"**, last

  Create is hidden when any tag equals the typed text (trimmed, in NFC) ignoring case, and it
  never offers a `notes/…` tag. So Enter on a partial name picks the existing tag. When Create
  isn't offered, Shift+Enter says why, in words that never repeat the tag.
- **Keys in the picker:**
  - **Enter only ever adds.** It adds the highlighted tag, clears the box and stays open for the
    next tag. On a ✓ item, it says "Already on all 12".
  - **Enter on a highlighted Create item** creates it.
  - **Shift+Enter** creates exactly the typed text, as long as §4's rules and the case-variant
    rule allow it.
  - **Shift+Delete** removes the highlighted ✓ or – item from the targets. So does
    Cmd/Ctrl+Backspace, but only while the box is empty, because with text in it the key edits
    the text. The × on the item does the same with the mouse.
  - **Esc** closes it.
- **Items show their state in words as well as glyphs.** A ✓ item is "on all 12", and a – item
  "on 5 of 12". Private tags are grouped under a lock, using the server's `private` flags.
- **The confirmation opens over the picker without closing it.** Focus returns to the picker's
  input afterwards.

### Drag and drop (the Library only)

- **Drag a row, or the selection if the row is in it,** onto a tag or note type in the sidebar to
  add it. The drag image reads "12 recordings".
  - Dragging a row outside the selection drags only that row, and leaves the selection alone.
  - A drag that starts on a checkbox, ×, + or the picker is cancelled.
- **The sidebar** lists a note type only while it has recordings or is the active filter (the
  picker still lists every note type). An active filter item that has gone from the library stays
  in the group it was last listed in.
- **+ New tag…** sits at the end of the sidebar's Tags group. A drop on it opens the picker for
  those recordings, and clicking it opens the picker for the selection. With nothing selected a
  click opens nothing and says "Select recordings first…" in a toast.
- **A drop target** shows a 2px orange outline, and its text keeps its colour. A drop that
  changes nothing says "Already on all 12".
- **A drop acts only on a drag that started in this page.**
  - The dragged IDs come from in-page state set at `dragstart`, never from `dataTransfer`.
  - Any other drop is ignored: files, text, or a drag from another window. Otherwise a foreign
    page could supply IDs that a drop would tag.
- **The non-drag path** is `T` or the selection bar. Phones, which lack native drag and drop, use
  those.

### Requests, toasts and the confirmation

- **The request queue:**
  - The browser sends one request at a time and queues the rest, because shinyreact merges input
    values set in the same tick.
  - It pauses while the confirmation is open.
  - After 30 seconds with no reply and no progress, the toast says "Still working…". The request
    is never re-sent, because it may still apply.
- **Toasts:**
  - Each action from the picker, a drop, a header or the selection bar shows one: "Added
    `school/course-101` to 12 recordings · Undo".
  - Single-cell toggles in the grid update one running toast ("4 changes · Undo last") instead of
    stacking new ones. A clean single-cell add that makes a recording visible joins it, and the
    toast totals "· M now visible to external agents".
  - A toast stays about 8 seconds, and pauses while hovered **or focused**.
  - When untagged recordings become visible, the toast says so: "· 12 now visible to external
    agents".
  - Removing a note type says "· auto-pick won't add it back".
  - On Pages, toasts carry "Demo: changes aren't saved".
  - Progress ("Tagging 1,200 of 2,000…") is announced at the start and at the end only. The
    visible count updates outside the live region.
  - Partial failures use `role="alert"` and stay until dismissed. They're grouped by reason, with
    counts: "3 edited outside the app: run `recordings reindex`".
- **Cmd/Ctrl+Z** undoes the newest action still among the session's 20, when focus is in the
  list or the grid. Alt+T is Sonner's "go to notifications".
- **The confirmation dialog** opens only when the server replies `confirm`.
  - Its title is "Remove `private` from 3 recordings?". For an Undo, it reads "Undo will remove
    `private` from 3 recordings?".
  - Its body: "2 become visible to external agents such as Claude Code. 1 will have no tags left,
    and stays hidden until its next tag."
  - Cancel has focus, and Esc means Cancel. **Remove** resends the request with `confirm_ids` set
    to the IDs the server named. If the server's count has grown by then, it asks again.

### Disabled tagging, shortcuts and hints

- **When `can_tag` is false,** the + and × controls aren't drawn, rows aren't draggable, and the
  reason shows once in the list header and the grid header ("Tagging is off: …"). `T` or a drop
  shows it in a toast; asking twice reuses the one toast, so identical alerts never stack. On the
  mirror's copy the reason ends "Open the archive's own app to tag.". While the archive is still
  being opened, the headers say "Opening the
  archive…" instead, which is a moment, not a fault.
- **Shortcuts** fire only when focus is in the list or the grid, and never:
  - with Cmd, Ctrl or Alt, except where the key map lists them
  - while `event.isComposing` is true
  - in an `input`, `textarea`, `select` or `contenteditable`

  Cmd-A calls `preventDefault` only when focus is in the list or the grid.
- **Until the `?` sheet exists,** the list header and the grid header show a one-line hint: "`T`
  tag · Cmd-A all · Esc clear · Cmd-Z undo · `R` refresh".

### Phones

- The selection bar sits above the bottom tab bar, where there is one, and is sticky.
- The picker is a full-width bottom sheet. A row's + opens this dialog form, not a popover. The
  sheet sits above an on-screen keyboard (it follows `visualViewport`), and has a Close button
  with a 44px target.
- Toasts come in at the top centre on phones, clear of the sheet and the keyboard.
- Rows aren't draggable on touch: the picker is the touch path (§6, drag and drop).
- On phones, the Tags page shows the Untagged list with the picker, not the grid. A tap there
  selects, and nothing opens. There are no key hints on touch.

### Components

shadcn's Command inside its Dialog (the picker's dialog form, built from the `Dialog` parts, not
`CommandDialog`) and Popover (the + form) for the picker, Dropdown Menu for the grid's sort menus,
a Popover for its column picker, Sonner for toasts, Alert Dialog for the confirmation, and
TanStack Virtual for rows. Each is checked against the shadcn skill or its docs before it is added
(CLAUDE.md). A gap goes in `docs/upstream-issues.md`.

## 7. The Tags page grid

- **Where:** **Tags** joins the top bar between Library and Status, at `#/tags`. The grid reads
  the `library` data that `App` already holds.
- **Layout:**
  - **A pinned first column** holds the selection checkbox, the title, the date, an **Open**
    button and, with Untagged only, a two-line snippet. It is the row header (`rowheader`).
  - **The `private` column** is pinned next to it, because "is this private?" is the first triage
    decision. The other private tag columns are grouped at the right, under a lock.
  - **Every other tag** gets a checkbox column, `notes/*` included. Tag headers are
    `columnheader`s named with the full tag path, and the sorted column carries `aria-sort`.
  - The header row stays pinned while scrolling, and rows are drawn virtually.
- **Cells:**
  - Each tag cell is a `gridcell` holding one `role="checkbox"` with `aria-checked`, named by its
    tag. Focus goes to the checkbox.
  - A click anywhere in the cell toggles that tag on that recording. Removing a last private tag
    goes through the confirmation like anything else.
- **The pinned cell:**
  - A click outside the checkbox is Finder-style: it replaces the selection. Cmd- and Shift-click
    work as in the list.
  - The checkbox toggles the row, with Shift for a range.
  - The grid opens nothing on click. **Open** opens the recording in the Library in a new tab
    (`#/library/<id>`, a read-only route), so the grid keeps its place.
- **A tag column's header** adds the tag to the selected rows ("Add to 12 selected"). With no
  selection, it announces a hint instead of only showing one.
- **Sorting rows:**
  - A menu on the first column offers Newest (the default), Oldest, Title and Duration.
  - Each tag column's menu offers "Has this tag first".
- **Triage:**
  - **Untagged only** is on by default, with a live "37 left".
  - A title filter narrows the rows, in the browser.
  - Rows stay put until Refresh (§6).
- **Snippets** are fetched for the rows on screen (§5) and shown under the title. A recording with
  no note or transcript shows "No transcript yet · duration · source", and **Open** is the way
  to hear it.
- **Columns:**
  - The default set is the 15 most-used tags, counted across all tags. The set and its order are
    worked out when the page loads and on Refresh, never live, so columns don't move under the
    cursor. Until Refresh the set is frozen per load and only grows.
  - A tag used or created in this session is added at the end of its group, and gets its column
    straight away.
  - A column picker, by folder, shows or hides the rest. Only the choices for non-private columns
    are kept in this browser (`localStorage`, in try/catch). Private columns are shown by
    default, so no private tag name is stored on disk.
- **Keyboard:**
  - Arrows move between cells, and cross the column headers: Up from the first row reaches the
    header row. With no rows to show, the header row is the grid's tab stop.
  - Cmd/Ctrl+Home and Cmd/Ctrl+End move focus to the first and last row without changing the
    selection, as in the list.
  - Space or Enter toggles the focused tag cell. On the pinned cell, Enter is **Open**.
  - `1`–`9` and `0` toggle the first ten visible tag columns on the focused row, or add the tag
    to the selection when two or more rows are selected. Each header shows its number. Main spec
    §12 uses `1`–`9` only inside the speaker picker, so nothing collides.
  - `T` opens the picker for the selection, or for the focused row when nothing is selected.
  - A header activated with Enter adds its tag to the selection.
- **Phones:** see §6.

## 8. Errors

- **Per recording, with fixed reasons:**
  - "edited outside the app: run `recordings reindex`"
  - "the file doesn't validate"
  - "busy: another job holds this recording; try again", for a lock timeout or a busy `state.db`
    (the toast says "1 recording busy: another job holds it; try again" for one recording, and
    "3 busy: …" for several)
  - "the recording is gone"
  - "not confirmed: would become visible"
- **A batch stops** with "Stopped: <fixed phrase>" on an archive-level error (§5). Every reply
  still carries the rows and the Undo token for what was applied.
  - **"Stopped: the request was cancelled"** is the phrase when the session's window goes or the
    app stops mid-batch. The write in flight is awaited for up to 5 seconds and recorded, and the
    reply is sent if the connection allows. A write still running after that isn't recorded.
- **A bad request** (an invalid tag, an empty removal, over a cap) is refused whole, with a plain
  message, before anything is applied.
- **Undo:** "already undone", or "Undo is no longer available (the page was reloaded)".
- **A lost connection:**
  - Queued requests are dropped, and the toast says so.
  - A request already sent was either applied once or not at all (§5, `request_id`).
  - After a reload, the library is loaded fresh, and the old toasts' Undo no longer applies.
- **No reply for 30 seconds:** "Still working…" (§6).
- **A server error inside the service** fails that request only, with a fixed phrase. The session
  stays up.
- **The writer thread exiting** turns `can_tag` false, with a reason.
- **A re-run of `import-audio-router`** puts back a privacy tag that part A removed with
  confirmation, because a re-run adds privacy tags (main spec §9.3). That is by design, not a bug.

## 9. Testing

All test archives are synthetic (`init_archive`, the `writer` fixture). No real data is read.

- **The core:**
  - `AddTags`, `RemoveTags` and `RestoreTags`, including exclusions both ways, and tag order
    restored.
  - The privacy gate: `WouldExpose` without `may_expose`, across capitalisations and NFKC and
    format-character variants.
  - Tag validation: zero-width, RTL, fullwidth, Cyrillic and `İ` lookalikes of "private", `..`,
    NUL, newline, and over-long names are refused. A removal still accepts an existing odd name.
  - `tagops.py` imports with `sqlite3` and `fcntl` blocked.
- **The service** is driven directly, with an injected `send(reply)` that records each reply. Both
  stores run the same parametrised tests:
  - validation and the caps
  - the confirmation: nothing is applied without `confirm_ids`, even for a hand-crafted request
  - **the races,** each against both stores:
    - two sessions with complementary removals
    - a stale session view
    - a set that grows between the `confirm` reply and the resend
    - an `AddTags` that lands between the browser's view and the write
  - partial failure: one recording edited outside the app is refused, and the rest are applied
    and undoable
  - an archive-level stop, with rows and an Undo token in the reply
  - `request_id`s applied at most once, and a gated request not recorded
  - Undo: single use, and an unknown token
  - delta contents, without the full view being rebuilt
  - each `can_tag` reason, and the retry after a transient failure
  - logs: no tag names, no titles, no exception text
- **The wiring:** one test through `local_server`, with `shinyreact.send_message` monkeypatched to
  record.
- **The writer thread:** `shiny.testserver.test_server_async` runs two sessions on one loop,
  under `@pytest.mark.anyio`. A store whose `apply` blocks on an `Event` shows that session B's
  `selected_id` still produces its `recording` output.
- **Demo isolation:** `test_demo_guard` covers a tag write. Every file it opens, and every
  `sqlite3.connect`, stays under the demo's temporary folder.
- **Vitest:**
  - The keyboard and selection logic live in pure `.ts` modules:
    - `isTextEntry(target)`
    - the selection reducer: click, Cmd-click, Shift ranges, Cmd-A, pruning, the anchor rules
    - picker ranking and the Create rules
    - the request queue: one in flight, in order, paused for the confirmation
    - merging deltas
  - Any React test is a `.test.ts` that uses `createElement`, as `StatusPage.test.ts` does.
- **e2e writing tests** run in their own module. Each test gets its own server, started with
  `TMPDIR=<tmp_path>` so it can find `recordings-demo-*/archive`.
  - several tags from a row's +, with the picker staying open
  - × removes a tag
  - dragging a selection onto a sidebar tag, and a foreign drop ignored
  - `T` on a selection, Cmd-Z after its toast has gone, and Esc closing the picker without
    clearing the selection
  - a grid cell toggle, a header applied to the selection, and the digit keys
  - Refresh, and rows staying put until it
  - removing a last private tag: the dialog, Cancel applying nothing, and Remove applying
  - the files on disk changed as expected, read through `Archive` (tags and `by` only)
  - the built-bundle checks from §6: picker, toast, dialog, scroll
  - a clean console
- **A larger synthetic archive** of 300 recordings is built by a fixture with the demo's UUID
  (`init_archive` and `Writer.add`) and served through `RECORDINGS_DEMO_ARCHIVE`. It covers:
  - virtualisation, and focus kept after a wheel scroll
  - Cmd-A over rows not drawn
  - keyboard paging
  - progress toasts
  - the default columns and the live "left" count
- **`can_tag` false:** the 300-recording archive copied, with `MIRROR.md` added through the
  sentinel's own constant (`recordings.sentinel.MIRROR`), which the writer refuses. Demo mode
  serves it, because it checks only the sentinel's UUID. The controls aren't drawn, rows aren't
  draggable, `T` shows the reason in a toast, and the reason shows once in each header.
- **Accessibility:**
  - an axe-core scan (a dev dependency) of the list and the grid, in light and dark
  - keyboard-only runs through the list and the grid
  - focus returning after the picker, the dialog and ×
  - the roles and attributes in §6 and §7
- **Phones:** a 375px layout, with checkboxes visible, × targets of at least 24px, and the Tags
  page showing the list.
- **Motion:** `prefers-reduced-motion` turns off the toast and chip animations, and those of the
  overlays too (the picker's popover and dialog, the confirmation, the menus) and the toaster.
- **Forced colours:** under `forced-colors: active`, the open row keeps its bar as a border, the
  highlighted picker and sort-menu item is the only one with a ring, and a focused grid cell keeps
  its ring and its pending marker.
- **Pages (`make pages-test`):**
  - tagging works in the browser, and a reload brings the demo back unchanged
  - `recordings_ui.tagging` imports on Pyodide
  - the console stays clean
- **The existing stale-binding test** still passes.

## 10. Changes to the main spec and the runbook

The plan's first task applies these to `2026-10-08-recordings-design.md`, each marked "(Dan,
2026-10-15)", and to `docs/runbooks/first-run.md`:

- **§6.8:**
  - Tagging's Undo records live in the session's memory, not in `state.db`.
  - `revisions.origin` carries `<op>:<request_id>`.
  - The Pages demo takes no lock at all.
- **§7.3:** removing any `notes/<type>` records the exclusion, and adding it back clears the
  exclusion.
- **§7.4:**
  - `is_private_tag` applies NFKC and drops format characters before `casefold()`.
  - New tag names follow part A's rules (§4).
- **§12, the key map:**
  - Up and Down select as they move.
  - Shift and Cmd/Ctrl with arrows, Page Up and Page Down, Home and End, and Enter (open).
  - Cmd/Ctrl+Space toggles a row.
  - Cmd-A selects all, and Esc clears.
  - Cmd/Ctrl+Z undoes.
  - `R` refreshes.
  - `1`–`9` and `0` work in the Tags grid.
  - `T` targets the selection, or in the grid the focused row.
  - Space toggles a cell in the Tags grid. It still plays or pauses wherever a player is shown.
- **§12.2:**
  - The privacy confirmation is a third exception to "no confirmation dialogs" (§3, decision 2).
  - + on rows, and × on chips.
  - Enter in the picker only ever adds. Shift+Enter creates, and Shift+Delete removes.
  - Create never makes a `notes/` tag or a case-variant.
- **§12.3:**
  - the pinned `private` column, and the snippet under the title
  - the header click versus the sort menu
  - Untagged only by default, with a live count
  - Refresh
  - default columns fixed per load
  - the digit keys
  - Open in a new tab
  - "rows stay put" covers the Library list and every filter
- **§15:**
  - Tag and snippet requests are never sent from a URL, a hash or storage.
  - Drag and drop acts only on in-page drags.
- **§17 and §17.1:**
  - Demo mode's temporary folder holds a `Writer`'s state, index and locks.
  - The Pages demo tags in memory, unsaved.
- **§19 and the runbook:** the first real tag ends the disposable window. Part A may come before
  2b's spikes and dry run, so the runbook says that tagging the real archive is Dan's call, and
  that a rollback by re-import before then loses tags.
- **§20:** part A moves ahead of 2b, part B follows it, and the rest of 3a is unchanged.

## 11. To check during implementation

These are checked when first used, not assumed:
- Shiny 1.8's `reactive.extended_task`, from its own docs and source, before the service relies
  on it.
- The `react-dom` bundling (§6), with the built-bundle e2e test.
- Shinylive delivers `send_message` on Pages.
- Playwright's `drag_to` drives native drag and drop.
- Where `revisions.origin` is read. Its new form must not break `reindex` or the merge bases.
- The `Writer`'s lock timeout can be set for the service alone.

## 12. Council rulings (2026-10-15)

Each finding is listed by its lens and number: R (reverse), F (forward), K (risk), U (UX and
accessibility). The findings files were scratch; the rulings are here.

- **Accepted as written or near it:**
  - R2/F3 (`tagops.py`; Pages imports)
  - R3/F5/K4 (Undo in the session, listed in §10; Refresh defined; `revisions.origin`)
  - R4/F6/K-I2 (effects under the lock)
  - R5/U-M3 (dialog counts and wording)
  - R6/F13/U-B1 (keyboard model; Space left to the player)
  - R7/K-M4 (in-page drags only; no URL writes)
  - R8/F10/K-M6 (the real reasons for one thread; one `Writer`)
  - R9/F9/K-M5 (the demo's `Writer`, the isolation test, `can_tag` retry)
  - R10/F11 (busy reasons)
  - R11 (handlers in `App`)
  - R12 (no privacy rule in TypeScript)
  - R13 (Create never makes `notes/`)
  - R14 and U-I7 (states without opacity; contrast; orange outlines)
  - R15/U-I4 (grid selection; selection stays; Open)
  - R16/K-I6 (tag-name rules, and the hardened `is_private_tag`)
  - R17 (housekeeping)
  - F1 (bundle `react-dom`)
  - F2/K-I1 (`extended_task` outside the reactive lock)
  - F4/K-M1 (`request_id` and token lifecycle; `ignore_none`; no reconnect)
  - F7 (a test plan the tools can run)
  - F8/U-I1/U-B2 (ARIA grid; focus kept through virtualisation)
  - F12 (chips from the row, not the `recording` output)
  - F14/K-M2 (snippets on their own reader, plain text, capped)
  - K-B1 (the gate inside the operation; `confirm_ids`)
  - K-I3 (partial batches always reply with rows and Undo)
  - K-I5 (fixed phrases; no tag names in logs)
  - K-I7 (short lock waits; thread liveness; "Still working…")
  - K-M3 (no private tag names in `localStorage`)
  - K-M7 (`now_visible` in the toast)
  - K-M8 (exclusions for any note type)
  - U-I2 (Cmd-Z; merged cell toasts; focus pauses toasts)
  - U-I3 (picker forms, removal keys, Esc layering)
  - U-I5 (click and drag precedence)
  - U-I6 (pinned `private`; snippet under the title; columns fixed per load)
  - U-I8 (phones)
  - U-I9 (digit keys; live count; Refresh)
  - U-M1, U-M2, U-M4 to U-M9
- **R1, the disposable window:** accepted as a rule, not a code change. Part A's code ships ahead
  of 2b, and Dan chooses when the real archive gets its first tag (§1).
- **K-I4, journalling every batch in `state.db`:** only partly taken. Undo stays in the session
  (listed in §10), and `revisions.origin` gains the request ID, so an interrupted batch leaves a
  record. A full journal, with Undo across reloads, waits for the CLI in the rest of 3a.
