# Tagging, part A: rulings made on Dan's behalf

Every `Ruling:` line from the SDD ledger, verbatim and in the order made (91 in all), grouped by the task under which it was made. Each records what was decided, why, and what it costs if wrong. Rework any you disagree with.

## Before Task 1 (pre-flight)

- WB1 — `_texts` accepts list|tuple (not str) and Task 5 gains a test that sends tuples — Shiny delivers JSON arrays inside input values as tuples; without it every real request is refused — costs nothing if wrong (lists still accepted).
- WB2 — the demo guard's allowed roots include the core package's read-only `recordings/format/` — the demo Writer writes the archive docs from there — if wrong, the guard is one folder looser, read-only package files only.
- WB3 — Task 9 adds `duration_ms: null` to `transcript.test.ts` — tsc fails otherwise — none.
- WB4 — `build.test.ts` starts with `// @vitest-environment node` — esbuild refuses jsdom — none.
- WB5–WB7 — `check=False` on the two `subprocess.run`s; split the 104-char f-string; `def active()` instead of the noqa'd lambda — `make lint` must pass — none.
- WB8 — the grid's onKeyDown returns early inside a columnheader (or any button/select that isn't a cell), plus an e2e for Enter on a header — spec §7 says Enter on a header adds to the selection — none.
- WM1 — logs carry the class name and a fixed phrase; `str(exc)` only feeds `cannot_tag_reason` — Global Constraints — none.
- WM2 — library load/Refresh read the view without awaiting the writer queue; `can_tag` comes from status() with a non-blocking retry — spec §5 "reads never queue behind writes" — if wrong, can_tag may lag one Refresh.
- WM3 — retry the open only on Refresh or after a TRANSIENT failure; otherwise return the cached status — spec §5 — none.
- WM4 — snippets() replies {request_id, snippets: {}, message} on an exception — a lost reply stalls snippet loading — none.
- WM5 — an ID with an impossible date is refused in `_check` as a bad ID (per-recording "gone" if it passes the pattern but can't resolve) — spec §5: a matching-but-missing ID is per-recording, never a batch stop — none.
- WM6 — "a type the archive already has" = any notes/* tag or any excluded_note_types entry in the session's view; noted as a planning decision — spec §4 names prompt files, which stage 4 brings — if wrong, a removed-everywhere type needs its prompt file before it can be re-added (stage 4).
- WM7–WM15 — wait on conditions instead of rAF timing and sleeps; strengthen the three weak tests as the table says (WM12: assert no request sent, WM13: assert a refusal message never contains the submitted tag) — flaky or can't-fail tests — none.
- WM16 — extract `useVirtualRows`, `useListShortcuts`, `useSelection(order)`; move the store fixture and run() to conftest.py; share `_ids_ok()` — verbatim duplication is a review defect — slightly more indirection.
- C1–C11 — apply the cosmetic fixes as listed (one phrase; file map; "existing" browserStorage; drop NO_HOVER; correct notes; rename "never" jobs; own constant for used Undo tokens; RecordingDetail props; "Already…" for removals; Undo progress via reply.total; rename the test) — none.
- U1 (CommandDialog nesting) and U2 (drag_to press point) — checked in Task 8 and Task 13 respectively, carried in those dispatches — if CommandDialog wraps its own Command, PickerBody renders without a second root; drag_to passes source_position on the title.
- WM6 refinement — the sidebar hides note types whose count is 0 (except the active filter's item, Review Focus 4); the picker and the grid's column picker still list them — a 0-count sidebar entry reads as a bug — if wrong, an excluded-only type is findable only through the picker. Carried into Task 13's dispatch.

## Task 1

- S2's scenario ("private " / " private" slipping through) is already refused by TagRef's space-padded-part rule (models.py:75-81, str.strip covers NBSP) — the spec wording becomes "line or paragraph separator" and Task 2 gains a test pinning padded/NBSP variants as refused — if wrong, a padded lookalike could be created; the test would show it.

## Task 2

- R1 — fix despite the plan text: take the lookalike test on the NFKC-folded tag's first folder (split after folding), map or refuse slash lookalikes, refuse the filler set, test each — spec §4 is binding ("doesn't look like private without being private") — costs a slightly stricter name rule.
- R2, plus add "PLE2502" to ruff's lint.select so bidi controls fail `make lint` — a Trojan Source guard; a one-line config change beyond the task — if wrong, one extra lint rule.
- C1 — (1) validate_new_tag refuses any new tag where is_private_tag(tag) differs from the plain rule (first ASCII folder casefolds to "private"), returning LOOKS_PRIVATE, plus a sweep test over every probe string (accepted ⇒ rules agree); (2) the archive's self-docs (format/AGENTS.md rule 2, FORMAT.md privacy) and CLAUDE.md's archive-rules line state the hardened rule now (moved up from the Task 17 carry) — the app and agents must share one rule (spec §7.4 "one function") — costs a docs change in this task.
- I1 + reviewer R1 — refuse Cn and an explicit Default_Ignorable set (U+034F, U+115F, U+1160, U+17B4–17B5, U+180B–180F, U+3164, U+FE00–FE0F, U+FFA0, U+E0100–E01EF) and U+2800; map slash lookalikes (U+2044, U+2215, U+29F8, U+2571, U+FF0F) to "/" before the lookalike test, run on the folded first folder; a test per character — spec §4 — slightly stricter names.
- M1, M3, M4, M5 and M6's core half (at most 3 consecutive combining marks per base) go into Task 2's fix round — privacy-adjacent and cheap — none.
- M2 carried to Task 5 (run the privacy name checks — the rule-mismatch and LOOKS_PRIVATE — on every add, existing tags included; removal accepts any spelling). M6's chip half carried to Tasks 10/12/14 (tag text in chips and headers isolated LTR: <bdi dir="ltr">, unicode-bidi: isolate, overflow hidden).
- the name rules' threat model is the owner's accidental input (IME width forms, a Cyrillic/Greek layout, invisible paste debris, common slash lookalikes), not an adversary — anyone who can send tag requests already holds the origin-guarded websocket, and the operation's gate still guards every real private tag. So: fix N1 (map the listed slash lookalikes U+27CB, U+2E4A, U+29F6, U+2AFD, U+1735, U+3033, U+30CE, U+31D3, U+4E3F, U+2F03, U+2CC6, U+1D23A, U+2041 to "/" for the lookalike test only; refuse the combining solidus overlays U+0337/U+0338), the small capitals of N2 (U+1D18, U+0280, U+026A, U+1D1B, U+1D07), N3 (refuse Zs other than U+0020), N4 (count only Mn/Me), N5, N6, N8 (PLE2510, PLE2512–PLE2515), and is_private_tag also ignores the fixed _HIDDEN set (errs private; docs say "invisible characters"); park N2's exotic scripts (Lisu, Coptic, APL, other Cherokee, assorted math) — not accidental inputs — and write the threat model into tagops.py's docstring and spec §4 — if wrong, a deliberately exotic tag can display like a private folder without being private; the UI's missing lock is then the only cue.
- N7 — refusing Cn ties validation to the runtime's Unicode version; on Pages (older tables) a newly encoded visible character is refused as invisible. Accepted, documented in the docstring — demo-only effect.

## Task 3

- V1 — MergeIncoming does a plain append (a private helper in tagops shared by AddTags and MergeIncoming), with a merge test proving an exclusion survives a duplicate carrying that notes tag; only the UI's AddTags clears exclusions — plan decision 1 assumed merged tags are `auto`, but the import sets `you`; the spec's "your choice wins" (parent §7.3) is binding — if wrong, a merge can't un-exclude a type, which nobody asked for.
- V1 refined per the integrity lens — MergeIncoming drops incoming notes/<type> tags whose type is excluded and builds exclusions from the original rec (union with incoming); test both by=you and by=auto. Also strengthen the WouldExpose test (revision row id unchanged, lock free, index_pending empty) and document Recorded.after's meaning in its docstring. Origin validation stays at the service (Task 5's _REQUEST_ID) — deferred.

## Task 4

- three cheap Task 4 minors go into Task 5's dispatch because Task 5 edits tagging.py and its tests would catch misuse: ArchiveStore.apply/ensure_open assert they run on the writer thread; ready() wraps the shared `opened` future in asyncio.shield; classify matches SQLITE_BUSY*/SQLITE_LOCKED* by prefix (index.py already does) — repeated by 2–3 reviewers, forward-relevant — none.

## Task 5

- F5-1 fixed in Task 5's fix round — shield the write future; on CancelledError await its completion (bounded), record the landed effect and row, mint the Undo token, best-effort "Stopped" reply, then re-raise; test cancelling run mid-write on both stores — Task 4's carry was to Task 7, but the write loop lives here — none.
- (1) and (2) join Task 5's fix round — they are departures from spec §5's text — none. (3): treat GONE as settled for an Undo (code) and amend spec §5/§8 that a partly applied Undo keeps its token for what it didn't settle (→ Task 17 carry) — if wrong, a gone recording's undo can't be retried, which nothing could do anyway. (4) → Task 17 carry (spec wording). (5), (6) deferred minors. (7) → Task 7 carry (never run `run` concurrently within one session; extended_task's queueing provides it).
- reviewer (1), (2), (3) join Task 5's fix round — (1) and (2) are correctness bugs in Undo and the landed check; (2) runs `_landed` through `store.write` (ordered, off the loop) — none.
- S5-I1 — expose = job.confirm_ids ∩ the pre-pass's losing set, passed to the gate; test the probe — spec §5 "nothing else exposes it" — none.
- M1 — _reads_as also drops HIDDEN_CODEPOINTS (validate_new_tag unchanged since INVISIBLE fires first); tests for U+3164/U+FFA0 — privacy, cheap — none.
- M2 — the session remembers the IDs it named in its last `confirm` reply (cleared at each _apply); confirmed = confirm_ids ∩ named; adjust the tests that pre-fill confirm_ids — guards a privacy path against client bugs (stale or carried-over confirm_ids); spec §6 says "the IDs the server named" — if wrong, a client must always see a confirm reply first, which the UI does anyway.

## Task 6

- one Task 6 fix round, "demo isolation hardening": S6-1 (write-aware guard: write-mode opens + the os.* write events, every write under the demo's temp folder via commonpath, Shiny's fileupload-* allowed, bytecode writes excluded), (2) the lifespan drains the writer off the loop with a bounded wait and OneThread.shutdown(cancel_futures=True) fails queued futures with ThreadStopped, (3) Settings carries demo_folder (set only by from_env) and writer_opener refuses without it or on the committed demo, (4) commonpath, plus adopt_archive moves into open_writer so ensure_open classifies its errors (3 mentions; restores serve-read-only on unexpected errors) — the task's own checkpoint lens, repeated findings — costs one round.

## Task 7

- T7-1 — fix despite plan text: library() builds the view at once with can_tag false and the OPENING reason; a per-session extended_task awaits store.ready() once; library() depends on its status and re-renders when it ends; test with a first open that blocks on an Event (window B's selected_id answers; A shows OPENING then can_tag) — spec §5 "reads never queue behind writes" is binding — none.
- Task 7 fix round 1 = T7-1 + cheap hardening: log the class name if tag_job/snippet_job ever raise; caplog assertion on the failing-snippet test; tests pinning bookmark_store == "disable" and that no session calls allow_reconnect; a per-ID guard in _snippets; reword the upstream-issues send_message entry; snippet() returns None for a private recording (least privilege) — none.
- fix it as a separate interstitial commit (fake the disk reading in those two tests), with its own quick review, before Task 8 — the gate can't hold while the suite fails on this machine — none.

## Task 9

- a `failed` reply with request_id null ends the one in-flight request (the server sends null only for a malformed request, and the browser sends one at a time, so it can only answer the in-flight one); null with nothing in flight or another status changes nothing — a permanent stall is worse — if wrong, a stray null refusal could end an unrelated in-flight request, whose real reply is then ignored.
- U1 — a pure pickerGroups(tags, query) orders the groups by their best rank, ties putting the locked (private) group first — spec §4/§6 privacy intent — none.
- U4 — add plain `X` (Gmail's select key; unused in parent §12) to toggle the focused row, alongside Cmd/Ctrl+Space — Dan's Mac likely swallows both modifier+Space — if wrong, one redundant key.
- Task 9 fix round 1 = T9-R1/U2, T9-R2 (gridOrder frozen ids), U1, U3 (+ "N hidden rows deselected" helper), U4, plus cheap minors: heard sets paused on confirm; saveChoices persists only server-flagged non-private tags; dismiss done flag + restart keeping pauses; re-add while removal pending → pending add; Create hidden for structurally invalid names; composing(e) with keyCode 229; TEXT_ENTRY text-like inputs only; NFC in the grid title filter — none.

## Task 10

- T10-R1 — a cell reply with now_visible > 0 or a notes/ removal gets an ordinary showResult toast (not merged); the merged text appends "· Demo: changes aren't saved" when !saves; test in Vitest (undo.ts/toasts) — spec §6 toasts, privacy — none. Also fix tag() to add a session tag only after submit accepts (cheap).
- T10-V1 — the undo reducer remembers tokens that came back on Undo replies; a request whose `undo` is one of them is a Redo: text "Redone on N", button "Undo", and its reply token goes on the Cmd-Z stack (refines decision 7: Cmd-Z twice still never undo-then-redo) — a Redo the user can't undo with Cmd-Z, and a wrong toast, are real defects — if wrong, Cmd-Z after a Redo undoes the redo, which is the ordinary meaning.
- Task 10 fix round 1 = T10-R1, T10-V1, U1 (one toaster-level pause counted on mouseover/mouseout + focusin/focusout anywhere in [data-sonner-toaster]; e2e Alt+T, Tab, 9 s, still there), U2 + dialog fallback (remember the last focus outside the toaster; restore before dismissing; fall back to the open row or the header h1), U3 (row-gap 24px under (hover: none) or overhang ≤ half the gap; e2e with has_touch: no two × boxes intersect), .chip-x:focus visible, Redo wording in the dialog, the cannot-tag alert (fresh announcement each time; 8 s for the opening case), long tags readable on focus-within, progress wording per action and no re-announce, tag() session tag after accept, an alert for a confirm without loses_private, the comment fix, test hygiene — none.

## Task 11

- Enter on a highlighted Create item creates the tag — parent §12.2 ranks Create last precisely so Enter on a partial name prefers an existing tag; Create is highlighted only when nothing else matches — if wrong, a typo with no near match creates a tag (one Undo).
- Task 11 fix round 1 = T11-F1 (optional `onOpenChange?: (open: boolean) => void` on AddTagsButton, tested) + useReturnFocus remembers the element's nearest [data-id] and re-finds it by data-id at close when the node was remounted — cheap, forward-critical for the virtual list — none.
- U3 — with an empty box, Enter and the removal keys act only on an item the user moved to by arrows/Home/End/PageUp/PageDown or the pointer (a `chosen` flag reset on search change and after an add); otherwise Enter does nothing and the note says "Type a tag name, or use the arrows" — the spec's "Enter adds the highlighted tag" assumed a deliberate highlight — if wrong, a deliberate Enter on the top item after opening needs one arrow press first.
- Task 11 fix round 1 = T11-F1 (onOpenChange), useReturnFocus re-find by data-id, U1 (mirror the highlighted item's id onto the input's aria-activedescendant via a MutationObserver on [cmdk-list]; e2e after open, typing and Enter; log the cmdk 1.1.1 gap in docs/upstream-issues.md), U2 (an indicator ≥ 3:1 in both modes, e.g. inset 3px bar in var(--primary) or a var(--ring) outline), U3, Cmd/Ctrl+Backspace only on an empty box (moved forward from the Task 15 carry), the Mac keys line, the + dashed border in --muted-foreground, Esc ignored while composing, a note for plain Enter with no item, session-touched tags kept in the picker with state "none", and the screen-reader noise trimmed — none.
- with an empty box and nothing chosen, the picker shows no highlight indicator and the input has no aria-activedescendant; the first arrow press reveals the current (top) item without moving past it, Home/End/PageUp/PageDown act and choose, pointer movement chooses; typing shows the indicator as now — U3's point is that what you see and hear is what Enter does, and a 6:1 bar on an item Enter refuses breaks that — if wrong, sighted users lose the hint of where the arrows start (one keypress shows it).

## Task 12

- spec §"Where focus returns" "after a filter change, it moves to the first row" means the roving tab stop, not real focus — the filter control is in the sidebar and pulling focus out of a control the user is operating breaks arrowing through filters (WAI grid pattern) — if wrong, one Tab press.
- "just tagged `x`" describes the selection it was applied to — it clears when the selection changes (not on a tag change of the same selection) — fold into Task 12's fix round — if wrong, the note disappears a little early.
- T12-F1 fixed in Task 12's fix round (guard the initialiser and the effect, false without matchMedia) — cheaper than a setup stub in Task 15.
- R1 — T acts on the visible selection (with nothing selected, the focused row), as Task 9's reducer does; the dialog title names the count; the spec sentence is amended in Task 17's docs pass — explicit selection beats an unselected focus ring (Explorer/Gmail model) and the bar already says "N selected · Tag… (T)" — if wrong, the user presses Space/X on the focused row first.
- R2 and R3 go into Task 12's fix round — R2: clear the status text before setting it (or alternate a zero-width marker); R3: a pointerdown outside the list clears focusIn, so only a focus that was really in the list follows the first row.
- Task 12 fix round includes I1 (clear on selection change; set only by actions that targeted the selection — T, the bar, a drop; a count renders as plain text), I2 (scrollToIndex(0) on a filter reason, no .focus()), I3, M1 (clear the follow timer on every move; fix the masking test), M2 (Clear returns focus to the focused row), M3 (preventDefault Space on a row, a no-op until the player claims it — spec reserves it), M5 (useCallback for getItemKey and the ref) — M6 parked for the final review.
- (M3 detail) main spec key map gives Space to the player "everywhere a player is shown"; the list page shows none, so a row's Space does preventDefault only (no stopPropagation) — a future player key handler must not skip on defaultPrevented (note for the final review / the player key-map work) — if wrong, a row's Space neither scrolls nor plays until the player claims it.
- U1(b) — when the focused row leaves, focus goes to the first surviving row at or after its old position, else the nearest before; same for the anchor; scroll it into view (align auto) — a backlog of thousands can't send you to the top on every R — if wrong, the user loses nothing they had.
- the + picker whose row is scrolled away stays inside the viewport (Radix sticky/avoidCollisions per current docs) rather than closing — closing loses typed text; floating detached is honest since its title names the recording — if wrong, it looks detached from its row.
- "✓ Tagged" after a × removal / "N tagged · Refresh" is spec wording — parked for Dan's rulings list at the end, not changed now.
- Task 12 fix round 1 = task-12-fix1.md (17 items: U1, I1–I3, U3, U4, M1, M3, R3/M4, R2+Cmd-A, bar re-scroll, row names via aria-labelledby, picker in viewport, X scrolls, one "Recordings" name, T12-F1 matchMedia guard, M5 useCallback). Parked: M6 settle(), M7 wording, Task 15's duplicate lines (its carry).
- "just tagged" clears on an Undo that reverses the add it names (it would be false) — added to the earlier ruling — if wrong, the note disappears after an Undo.
- Task 12 fix round 2 = item 2 residue (compare selection members, not Set identity; remember the last add whose ids matched the selection and ignore non-matching adds; clear on an Undo of that add) + the pending-chip name — then a small re-review.

## Task 13

- the drop outline must meet 3:1 (WCAG 1.4.11, main spec §12.6) in both themes — not a brand question: keep --brand-orange where it passes (dark) and use a darker shade of the brand orange in light (a --drop token) that measures ≥ 3:1 against the sidebar and the active item; e2e measures it — if wrong, the light outline is a shade darker than the brand orange.
- drop handlers staying on with tagging off is right (spec §6: say why, don't silently refuse).
- R1 into the fix round — while an in-page drag is active, a capturing document `drop` listener preventDefaults drops outside the sidebar targets, so nothing is pasted; keep text/plain for Firefox. R2, R3, R4 parked for the final review.
- F2 and F3 into Task 13's fix round — rows are draggable only when canTag AND the device can hover (`(hover: hover)` via useMediaQuery; the picker is the touch path); "+ New tag…" with an empty target list says why ("Select recordings first, or drag them here") through the existing note/toast path, never silently.
- M2 — a click on + New tag… acts on the selection only; with nothing selected it says "Select recordings first, or drag them here" (merges forward F3) — from the sidebar the list's focus is invisible, unlike T inside the list — if wrong, keyboard users press Space/X on a row first.
- Task 13 fix round = the light outline (darker brand orange, e2e-measured ≥ 3:1 on --sidebar and .si.on), R1 (no paste into text fields), F2 (draggable only with hover), M1 (keep the last-listed private flag in App), M2/F3, M3 (a test), M4 (window-level dragend/drop clear + clear on the next pointerdown), M5 (waitFor) — plus whatever the security lens adds.
- adopt the per-drag marker AND window-level dragend/drop + next-pointerdown clears (M4) — if a target browser doesn't expose custom types during dragover, keep only the clears.

## Task 14

- the grid's session state (filter, sort, selection, the session-hidden private columns) survives a route switch, in memory (App or a context), as the Library's selection does — never in storage for private names (spec §7: only non-private column choices go to localStorage) — fold into Task 14's fix round — if wrong, a visit to the Library resets the grid.
- R1 into the fix round — verbatim duplicated logic is Important by the rubric and Task 15 touches both: extract a shared focus-handoff hook (incl. the bar re-scroll) and a shared header-notes component used by the list and the grid. R2 into the fix round (Cmd/Ctrl+Left/Right pass through; Shift too unless the spec wants column extension — it doesn't).
- T14-I1 — the column set (base AND the `all`-derived tags) freezes at load and on Refresh; a session tag is appended at the end of its group; a column whose last use goes stays (count 0) until Refresh; the digit mapping follows the frozen order — spec §7 "never live" — if wrong, stale 0-count columns linger until R.
- T14-I4 — Cmd/Ctrl+Home and Cmd/Ctrl+End move focus only (first/last row) without changing the selection, in BOTH the list and the grid (the "Cmd/Ctrl moves focus only" rule of spec §6); plain Home/End keep selecting; Up from the first row reaches the headers without touching the selection; an empty grid makes the header row the tab stop — if wrong, Cmd+Home differs from Finder's (which has none).
- U2 — revises the Task 10 decision: a clean single-cell add with now_visible>0 joins the running toast, which sums "· M now visible to external agents" (removals, confirmations, failures, non-cell adds unchanged) — spec §6 asks for one running toast AND that the toast says so; 21 stacked toasts and 20 identical announcements fail the first — if wrong, a visibility change is reported in a summed line instead of its own toast.
- Task 14 fix round 1 = task-14-fix1.md (16 items: columns frozen per load; the running toast; shared focus hook + header notes; route-switch state incl. column order; Cmd/Ctrl+Home/End focus-only in list and grid + headers keep selection + empty-grid tab stop + GRID_HINT; pending vs focus; horizontal reveal; aria-keyshortcuts + `0` test; e.repeat; SELECT_ROWS keys; row name; radio sort menu; tab stop when nothing focused (list too); no "…" description; Cmd/Ctrl+Left/Right pass; parametrize).
- the "now visible" sum stays (errs toward exposure) — parked. Everything else goes into Task 14 fix round 2 = task-14-fix2.md (5 items); a reveal puts back only rows passing the hearing view's own filter.

## Task 15

- the pinned-header e2e flake is fixed in Task 15's fix round (wait for the row before probing) — a known-flaky test is a defect.
- R3 confirmed — hide the key hint where there's no hover, not by width. R1 and R4 into the fix round (hover-aware text; call onOpenChange(true/false) around the sheet); R2 — .list max-height uses dvh in the fix round; whether the bar must follow .panes waits for the UX lens.
- the phone Tags page staying select-only is the spec as written — added to Dan's list at the end (a separate Open control would be the fix if he wants to listen while triaging on a phone). Top-center toasts on phones stay. The other minors go into the fix round.
- U1 — cap the phone sheet below the toast band and let toast taps through the modal (onPointerDownOutside guard) — U2 lighten .row.sel.done or darken light .chip.nt; axe after a selection-wide tag; toasts top-center under (hover: none) or ≤ 1000px; visual-viewport sizing under (hover: none); the rest as task-15-fix1.md.
- Task 15 fix round 2 = task-15-fix2.md — the popover keyboard double count (keep padding 8, let floating-ui's visual-viewport boundary work, reposition on visualViewport resize, real assertions), Sonner offset shared with the band, a 44px Close on the sheet form (it's now the phone's main path), wait_until message.

## Task 16

- Task 16's checkpoint lens (forward compatibility: Pyodide 3.12, older pydantic/markdown-it, Shinylive delivery) coincides with the forward lens — the council runs reverse + one forward/compat lens, with the reviewer; each must check that every new Pages test CAN fail (a mutation that saves, or drops send_message, turns it red) since there was no RED.
- the demo must behave like the real app (Dan) — on Pages the recording view reflects the store's in-memory tags (private hides transcript/notes/snippet); snippets asserted on Pages; reviewer's test-hygiene minors — Task 16 fix round 1 = task-16-fix1.md.
- (correction) my item-1 premise was wrong — the real app does NOT hide a private recording's transcript or notes from Dan (privacy is about external agents and logs; only snippets skip private recordings). The fix stands as "the demo's recording view reflects the in-memory tags/private flag", which is what "works like the real example" needs; no hiding asserted — if wrong, nothing is lost.

## Task 17

- Task 17 is last, so its forward lens has no later task — the council runs reverse + the operations checkpoint (which covers "what the next person reads"), with the reviewer on opus for docs accuracy.
- Task 17 fix round 1 = task-17-fix1.md (25 items + measure the e2e budget): CI runs the pin (swap + a .venv run + fail-on-skip under CI); the six docs corrections; the grid Open stop removed + a CLAUDE.md rule against stopPropagation on clicks + the Radix note; the operations wording fixes (1 recording busy; OSError phrase; mirror hint by code; de-dup alerts; grid cells on the mirror); a runbook "Tagging is off" guide; the first real tag placed in the README and at the runbook's import step; a debug recipe and extension/fake-failure pointers in CLAUDE.md.
- one fixed id for the cannot-tag alert (a new reason replaces the old; identical repeats only update) — Task 17 fix round 2 = task-17-fix2.md (10 items, docs plus two small code/test fixes).

## The final review

- final fix wave = final-fix.md (7 items): drop a refused add's tags from sessionTags and their session-only grid columns, and refuse \p{Zs}≠U+0020 and \p{Cf} in createRefusal; drop the impossible runbook clause; remember _keep's dropped tokens and say "Undo keeps only the last 20 actions"; snippet takes the store's read on Pages; the committed-demo test uses a temp copy; the Timer(0.3) test waits on a condition; CLAUDE.md tag-change wording.
- (residual adjudication) fix all residuals now in one short pass by the same implementer, verified by the controller's full gate and diff read rather than another review loop — wrong docs ship otherwise (Dan's docs rule), two assertions assert nothing, and the prune breaks Task 14's ruling; the privacy-display order fix rides along (cheap, privacy) — if wrong, one small extra commit set on the branch.

## Dan's answers (2026-10-22)

The questions the rulings left for Dan, asked one at a time after the final review.

1. **The phone's Tags page.** It was select-only: no way to open or hear a recording from it. Dan chose
   "Library's Untagged" (the recommended option): on a phone, the Tags tab opens the Library filtered to
   Untagged, where a tap opens the player and transcript, and + and the selection bar work. He gave no
   further reason.
2. **The label after a change.** A row read "✓ Tagged" even after a tag was removed. Dan chose
   "Tagged / Tag removed" over the recommended "✓ Changed": a row says "✓ Tagged" after an add and
   "✓ Tag removed" after a removal, and the header counts both. My reading is that the header says
   "N changed · Refresh", since "N tagged" would be wrong once it counts removals. He gave no further
   reason.
3. **When the real archive gets its first tag.** The first tag ends §19's disposable window. After it, a
   re-import loses the tags made in the app, and a rollback has to be a restore. Dan chose "After 2b's
   dry run" over the recommended "after a backup and `doctor`": the real archive gets no tag until stage
   2b's import dry run is done, so a re-import stays free until then. He gave no reason beyond the choice.
   The README and the runbook's import step now say so.
4. **Integration.** Dan chose "Push + PR", the recommended option: once answers 1 and 2 are built and green
   locally, push `tagging-a` and open a PR. CI and the Pages workflow then run on GitHub, which proves the
   new CI steps, and `main` is fast-forwarded to the branch once CI is green. Fast-forwarding keeps the
   SHAs that the ledger and `.git-blame-ignore-revs` quote.
