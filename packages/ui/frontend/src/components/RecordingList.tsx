import type { VirtualItem } from "@tanstack/react-virtual";
import { Check, Lock, TriangleAlert } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type FocusEvent, type KeyboardEvent, type MouseEvent } from "react";

import { cn } from "@/lib/utils";

import { composing, isTextEntry, toggleFocusedKey } from "../lib/keys";
import { justTaggedTags, shownTags } from "../lib/library";
import { targetsOf, type Move, type Sel, type SelAction } from "../lib/selection";
import { useListShortcuts } from "../lib/useListShortcuts";
import type { Announcement, Fitted } from "../lib/useSelection";
import { useVirtualRows } from "../lib/useVirtualRows";
import { showCannotTag } from "../tagging/toasts";
import { useTaggingContext, type Tagging } from "../tagging/useTagging";
import type { LibraryRow } from "../types";
import { SelectionBar } from "./SelectionBar";
import { TagChips } from "./TagChips";
import { AddTagsButton, pickerTitle } from "./TagPicker";

const ROW_ESTIMATE = 84;
const FOLLOW_MS = 300; // the pane follows focus after a short pause, as Mail's does (spec §6)
export const KEY_HINT = "T tag · Cmd-A all · Esc clear · Cmd-Z undo · R refresh";
// A row's own controls, reached with Left and Right: its chips' × and its +. Those a device
// doesn't show (no × where it can't hover, app.css) are skipped.
const CONTROLS = "[data-control]:not([data-control=checkbox])";

/** `T`, the selection bar and a drop open the picker's dialog form; when tagging is off, or the
 * archive is still being opened, a toast says so instead (spec §6). */
export function openPickerFor(ids: string[], rows: readonly LibraryRow[], tagging: Tagging): void {
  if (ids.length === 0) return;
  if (!tagging.canTag) {
    showCannotTag({ canTag: tagging.canTag, opening: tagging.opening, reason: tagging.reason });
    return;
  }
  const titles = new Map(rows.map((r) => [r.id, r.title]));
  tagging.openPicker({ ids, title: pickerTitle(ids, (id) => titles.get(id) ?? id) });
}

/** The list's live region. It empties, then says the words a frame later, so the same words
 * said again (a second "2 hidden rows deselected") are read out again: a status region reads only
 * a change. `at` is new for each announcement. */
export function Announcer({ text, at }: { text: string; at: number }) {
  const [said, setSaid] = useState({ at, shown: text });
  // Emptied in the very commit that brings new words, never batched away with them.
  if (said.at !== at) setSaid({ at, shown: "" });
  useEffect(() => {
    if (said.shown === text) return;
    const frame = requestAnimationFrame(() => setSaid({ at, shown: text }));
    return () => cancelAnimationFrame(frame);
  }, [said, text, at]);
  return <span className="sr-only" role="status">{said.shown}</span>;
}

/** The tags "just tagged" names: the last add to exactly this selection since it last changed
 * (ruled, Task 12 fix round 1). A new selection forgets what the one before was tagged with. */
function useJustTagged(selected: ReadonlySet<string>, tagging: Tagging): string[] | null {
  const now = tagging.lastTagged?.key ?? 0;
  const [since, setSince] = useState({ selected, key: now });
  if (since.selected !== selected) setSince({ selected, key: now }); // React redraws at once
  return justTaggedTags(tagging.lastTagged, selected, since.selected === selected ? since.key : now);
}

type Props = {
  rows: LibraryRow[]; sel: Sel; act: (action: SelAction) => void;
  openId: string | null; onOpen: (id: string) => void; announce: Announcement; fitted: Fitted;
};

/** The Library list (spec §6): an ARIA grid, one tab stop, drawn virtually. Up and Down move
 * focus and select; Shift extends from the anchor; Cmd/Ctrl moves focus only; X or Cmd/Ctrl+Space
 * toggles; Page Up and Down, Home and End; Enter opens; Left and Right move across the focused
 * row's controls. Space is left to the player. Selection changes on click, never on mousedown.
 * Keys, clicks and focus from a portal (the + picker), whose React events bubble through its row,
 * are never the list's. Each row is named by its title, details, tags and state, not by its
 * controls, which keep their own names when focused. */
export function RecordingList({ rows, sel, act, openId, onOpen, announce, fitted }: Props) {
  const tagging = useTaggingContext();
  const ids = useId();
  const scrollRef = useRef<HTMLDivElement>(null);
  const wantFocus = useRef(false);
  const focusIn = useRef<string | null>(null); // the row holding focus, while the list holds it
  const follow = useRef<number | null>(null);
  const selRef = useRef(sel);
  selRef.current = sel;
  const [pickerId, setPickerId] = useState<string | null>(null); // the row whose + picker is open
  const order = useMemo(() => rows.map((r) => r.id), [rows]);
  const index = useMemo(() => new Map(order.map((id, i) => [id, i])), [order]);
  const at = (id: string | null) => (id === null ? -1 : (index.get(id) ?? -1));
  const focusIdx = at(sel.focus);
  const barShown = sel.selected.size >= 2;
  const justTagged = useJustTagged(sel.selected, tagging);
  /** A drawn row, found in the page: the virtualiser draws and drops them as the list scrolls. */
  const rowEl = (id: string | null) =>
    id === null ? undefined : [...(scrollRef.current?.querySelectorAll<HTMLElement>("[role=row][data-id]") ?? [])].find((el) => el.dataset.id === id);

  // The focused row, the anchor, and the row whose + picker is open always stay rendered (in
  // Task 13, a dragged row too): the picker is anchored on that row's +.
  const virtualizer = useVirtualRows({
    ids: order, scrollRef, rowSize: ROW_ESTIMATE, overscan: 8, keep: [focusIdx, at(sel.anchor), at(pickerId)],
  });
  /** Brings the focused row into view: before a key acts on it, and after the order changes. */
  const reveal = (i = focusIdx) => {
    if (i >= 0) virtualizer.scrollToIndex(i, { align: "auto" });
  };

  // A picker's row that leaves the list (a filter change, Refresh) takes the picker with it, and
  // the + reports no close when it unmounts: forget it here (Task 11 carry).
  useEffect(() => {
    if (pickerId !== null && !index.has(pickerId)) setPickerId(null);
  }, [index, pickerId]);

  // The row that had focus left the list (Refresh, a filter change): focus goes to the row the
  // selection now focuses, so the keys go on working, unless focus has gone somewhere else.
  useEffect(() => {
    const id = focusIn.current;
    if (id === null || index.has(id)) return;
    focusIn.current = null;
    const active = document.activeElement;
    if (order.length > 0 && (active === null || active === document.body)) wantFocus.current = true;
  }, [index, order]);

  // Moving focus by key scrolls the row into view, then focuses it once it is drawn. After a new
  // order, the selection catches up a render later.
  useEffect(() => {
    if (!wantFocus.current) return;
    if (order.length === 0) {
      wantFocus.current = false;
      return;
    }
    if (focusIdx < 0) return;
    wantFocus.current = false;
    reveal();
    const id = sel.focus;
    requestAnimationFrame(() => rowEl(id)?.focus({ preventScroll: true }));
  }, [sel, focusIdx, order, virtualizer]);

  // Once the selection is fitted to a new order: a filter change shows the list from the top,
  // where its first row (the tab stop) is, and leaves real focus where it is (in the sidebar).
  // Otherwise (a Refresh, an Undo's rows coming back) the focused row may have moved: it comes
  // back into view, so the user keeps their place.
  useEffect(() => {
    if (fitted.reason === "filter") virtualizer.scrollToOffset(0);
    else reveal();
  }, [fitted.key]);

  // The selection bar takes 43 px from the list as it comes, after the focused row was scrolled
  // into view: a frame later, with the list's real height laid out, a focused row it now cuts
  // into is shown whole (the virtualiser's own pass still has the old height).
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const list = scrollRef.current?.getBoundingClientRect();
      const row = rowEl(selRef.current.focus);
      const box = row?.getBoundingClientRect();
      if (!list || !row || !box) return;
      const partly = box.bottom > list.top && box.top < list.bottom;
      if (partly && (box.bottom > list.bottom || box.top < list.top)) row.scrollIntoView({ block: "nearest" });
    });
    return () => cancelAnimationFrame(frame);
  }, [barShown]);

  // Focus that only seemed to stay: Safari and Firefox on macOS don't focus a button on click,
  // so a click outside blurs the row with no relatedTarget, as a removed row does. A pointer
  // pressed outside the list means focus left it.
  useEffect(() => {
    const down = (e: PointerEvent) => {
      if (!scrollRef.current?.contains(e.target as Node)) focusIn.current = null;
    };
    document.addEventListener("pointerdown", down, true);
    return () => document.removeEventListener("pointerdown", down, true);
  }, []);

  useEffect(() => () => { if (follow.current) window.clearTimeout(follow.current); }, []);

  const page = () => Math.max(1, Math.floor((scrollRef.current?.clientHeight ?? ROW_ESTIMATE * 10) / ROW_ESTIMATE) - 1);
  const openPicker = () => openPickerFor(targetsOf(selRef.current, order), rows, tagging);
  const shortcut = useListShortcuts(act, openPicker); // Cmd-A, Cmd-Z, T and R, as the grid's
  /** Clear, from the bar's button or Esc in the bar: the bar goes, so focus goes back to the
   * list's focused row, not to <body>. */
  const clear = () => {
    act({ type: "clear" });
    wantFocus.current = true;
  };

  const move = (e: KeyboardEvent, to: Move) => {
    e.preventDefault();
    wantFocus.current = true;
    const meta = e.metaKey || e.ctrlKey;
    act({ type: "move", to, shift: e.shiftKey, meta, page: page() });
    // Every move ends the last one's pause; only a plain move starts one (Cmd/Ctrl and Shift
    // never open a row).
    if (follow.current) window.clearTimeout(follow.current);
    follow.current = null;
    if (!meta && !e.shiftKey) {
      follow.current = window.setTimeout(() => { const id = selRef.current.focus; if (id) onOpen(id); }, FOLLOW_MS);
    }
  };

  const across = (e: KeyboardEvent) => {
    const row = rowEl(sel.focus);
    if (!row) return;
    e.preventDefault();
    const controls = [...row.querySelectorAll<HTMLElement>(CONTROLS)].filter((c) => c.getClientRects().length > 0);
    const i = controls.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowRight") (controls[i + 1] ?? controls[i])?.focus();
    else if (i <= 0) row.focus();
    else controls[i - 1].focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!e.currentTarget.contains(e.target as Node) || isTextEntry(e.target) || composing(e) || e.altKey) return;
    if (toggleFocusedKey(e)) {
      e.preventDefault();
      reveal(); // a key acts on the focused row: show it, wherever the wheel left the list
      act({ type: "toggleFocused" });
      return;
    }
    switch (e.key) {
      case "ArrowDown": return move(e, "next");
      case "ArrowUp": return move(e, "prev");
      case "PageDown": return move(e, "pageDown");
      case "PageUp": return move(e, "pageUp");
      case "Home": return move(e, "first");
      case "End": return move(e, "last");
      case "ArrowLeft":
      case "ArrowRight": return across(e);
      case "Enter":
        if (sel.focus && e.target === rowEl(sel.focus)) {
          e.preventDefault();
          reveal();
          onOpen(sel.focus);
        }
        return; // on a control, Enter is the control's
      case "Escape":
        if (!e.defaultPrevented) act({ type: "clear" }); // only an Esc nothing else handled
        return;
      case " ":
        // Space is the player's wherever one is shown (main spec §12); the list shows none, and
        // a row mustn't scroll the list a page. A control's own Space is left alone.
        if ((e.target as Element).matches("[role=row]")) e.preventDefault();
        return;
    }
    if (shortcut(e) && e.key.toLowerCase() === "t") reveal();
  };

  const onFocus = (e: FocusEvent<HTMLDivElement>) => {
    if (!e.currentTarget.contains(e.target as Node)) return; // focus in the + picker's portal
    const id = (e.target as Element).closest<HTMLElement>("[data-id]")?.dataset.id ?? null;
    focusIn.current = id;
    if (id !== null && selRef.current.focus !== id) act({ type: "focus", id });
  };
  const onBlur = (e: FocusEvent<HTMLDivElement>) => {
    // Focus left for somewhere named: the list no longer holds it. A null relatedTarget is a
    // removed row (Chromium says so as it goes), another window, or a click on nothing: kept, so
    // a row that left the list hands focus on (a pointer pressed outside clears it, above).
    const to = e.relatedTarget;
    if (to !== null && !e.currentTarget.contains(to as Node)) focusIn.current = null;
  };

  const onRowClick = (e: MouseEvent<HTMLDivElement>, id: string) => {
    const target = e.target as Element;
    if (!e.currentTarget.contains(target)) return; // a click in a portal (the picker)
    if (target.closest(CONTROLS)) return; // × and + never select
    if (target.closest("[data-control=checkbox]")) {
      act({ type: "checkbox", id, shift: e.shiftKey });
      return;
    }
    const meta = e.metaKey || e.ctrlKey;
    act({ type: "click", id, meta, shift: e.shiftKey });
    if (!meta && !e.shiftKey) onOpen(id);
  };

  const drawRow = (v: VirtualItem) => {
    const r = rows[v.index];
    const selected = sel.selected.has(r.id);
    const mark = tagging.marks[r.id];
    const tabStop = r.id === sel.focus || (focusIdx < 0 && v.index === 0);
    const tags = shownTags(r, tagging.pending);
    // The row's name: title, lock, details, tags and state; never its controls' names (with the
    // title again in each), which made it about 330 characters read on every arrow.
    const id = `${ids}${v.index}-`;
    const names = [
      `${id}t`, ...(r.private ? [`${id}l`] : []), `${id}m`, ...tags.map((_, i) => `${id}c${i}`),
      ...(mark?.done ? [`${id}d`] : []), ...(mark?.failed ? [`${id}f`] : []),
    ];
    return (
      <div
        key={v.key}
        ref={virtualizer.measureElement}
        data-index={v.index}
        role="row"
        aria-rowindex={v.index + 2}
        aria-selected={selected}
        aria-current={r.id === openId ? "true" : undefined}
        aria-labelledby={names.join(" ")}
        tabIndex={tabStop ? 0 : -1}
        data-testid="recording-row"
        data-id={r.id}
        className={cn("row", selected && "sel", r.id === openId && "on", mark?.done && "done", mark?.failed && "failed")}
        style={{ transform: `translateY(${v.start}px)` }}
        onClick={(e) => onRowClick(e, r.id)}
      >
        <div role="gridcell" className="row-cell" data-controls="">
          <span className="check" aria-hidden="true" data-control="checkbox">{selected ? <Check /> : null}</span>
          <div className="row-body">
            <div className="t">
              <span className="row-title" id={`${id}t`}>{r.title}</span>
              {r.private && <Lock size={13} className="lock" aria-label="Private" id={`${id}l`} />}
            </div>
            <div className="m" id={`${id}m`}>{[r.when, r.duration, r.sources.join(", "), r.kind === "video" ? "video" : null].filter(Boolean).join(" · ")}</div>
            <TagChips
              tags={tags}
              title={r.title}
              canTag={tagging.canTag}
              place="row"
              labelIds={`${id}c`}
              onRemove={(tag, next) => {
                (next ?? rowEl(r.id))?.focus();
                tagging.tag("remove", [r.id], [tag], { kind: "chip" });
              }}
            >
              {tagging.canTag && (
                <AddTagsButton
                  id={r.id}
                  title={r.title}
                  place="row"
                  onOpenChange={(open) => setPickerId((p) => (open ? r.id : p === r.id ? null : p))}
                />
              )}
            </TagChips>
            {mark?.done && <span className="done-label" id={`${id}d`}>{"✓"} Tagged</span>}
            {mark?.failed && (
              <span className="failed-label" id={`${id}f`}><TriangleAlert aria-hidden="true" size={13} /> {mark.failed}</span>
            )}
          </div>
        </div>
      </div>
    );
  };

  // One name: the grid is "Recordings"; its section adds no second region of the same name.
  return (
    <section className="list">
      <div className="list-head">
        <span>{rows.length.toLocaleString("en")} recording{rows.length === 1 ? "" : "s"} {"·"} newest first</span>
        <button type="button" className="refresh" data-testid="refresh" onClick={tagging.refresh}>
          {tagging.touched > 0 ? `${tagging.touched.toLocaleString("en")} tagged · Refresh` : "Refresh"}
        </button>
        {!tagging.saves && <span className="demo-note">Demo: changes aren't saved</span>}
        {tagging.opening ? (
          <span className="cannot-tag opening" data-testid="cannot-tag">Opening the archive{"…"}</span>
        ) : !tagging.canTag && (
          <span className="cannot-tag" data-testid="cannot-tag">Tagging is off: {tagging.reason}</span>
        )}
        <span className="key-hint" data-testid="key-hint">{KEY_HINT}</span>
        <Announcer text={announce.text} at={announce.key} />
      </div>
      <div
        ref={scrollRef}
        className={cn("vlist", sel.selected.size > 0 && "has-selection")}
        role="grid"
        aria-multiselectable="true"
        aria-rowcount={rows.length + 1}
        aria-label="Recordings"
        data-testid="recording-list"
        onKeyDown={onKeyDown}
        onFocus={onFocus}
        onBlur={onBlur}
      >
        <div role="rowgroup" className="sr-only">
          <div role="row" aria-rowindex={1}><div role="columnheader">Recording</div></div>
        </div>
        <div role="rowgroup" className="vrows" style={{ height: virtualizer.getTotalSize() }}>
          {virtualizer.getVirtualItems().map(drawRow)}
        </div>
      </div>
      <SelectionBar count={sel.selected.size} lastTagged={justTagged} onTag={openPicker} onClear={clear} />
    </section>
  );
}
