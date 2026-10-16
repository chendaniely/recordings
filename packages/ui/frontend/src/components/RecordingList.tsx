import type { VirtualItem } from "@tanstack/react-virtual";
import { Check, Lock, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FocusEvent, type KeyboardEvent, type MouseEvent } from "react";

import { cn } from "@/lib/utils";

import { composing, isTextEntry, toggleFocusedKey } from "../lib/keys";
import { shownTags } from "../lib/library";
import { targetsOf, type Move, type Sel, type SelAction } from "../lib/selection";
import { useListShortcuts } from "../lib/useListShortcuts";
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

type Props = {
  rows: LibraryRow[]; sel: Sel; act: (action: SelAction) => void;
  openId: string | null; onOpen: (id: string) => void; announce: string;
};

/** The Library list (spec §6): an ARIA grid, one tab stop, drawn virtually. Up and Down move
 * focus and select; Shift extends from the anchor; Cmd/Ctrl moves focus only; X or Cmd/Ctrl+Space
 * toggles; Page Up and Down, Home and End; Enter opens; Left and Right move across the focused
 * row's controls. Space is left to the player. Selection changes on click, never on mousedown.
 * Keys, clicks and focus from a portal (the + picker), whose React events bubble through its row,
 * are never the list's. */
export function RecordingList({ rows, sel, act, openId, onOpen, announce }: Props) {
  const tagging = useTaggingContext();
  const scrollRef = useRef<HTMLDivElement>(null);
  const rowEls = useRef(new Map<string, HTMLDivElement>());
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

  // The focused row, the anchor, and the row whose + picker is open always stay rendered (in
  // Task 13, a dragged row too): the picker is anchored on that row's +.
  const virtualizer = useVirtualRows({
    ids: order, scrollRef, rowSize: ROW_ESTIMATE, overscan: 8, keep: [focusIdx, at(sel.anchor), at(pickerId)],
  });

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
    virtualizer.scrollToIndex(focusIdx, { align: "auto" });
    const id = sel.focus;
    requestAnimationFrame(() => { if (id) rowEls.current.get(id)?.focus({ preventScroll: true }); });
  }, [sel, focusIdx, order, virtualizer]);

  useEffect(() => () => { if (follow.current) window.clearTimeout(follow.current); }, []);

  const page = () => Math.max(1, Math.floor((scrollRef.current?.clientHeight ?? ROW_ESTIMATE * 10) / ROW_ESTIMATE) - 1);
  const openPicker = () => openPickerFor(targetsOf(selRef.current, order), rows, tagging);
  const shortcut = useListShortcuts(act, openPicker); // Cmd-A, Cmd-Z, T and R, as the grid's

  const move = (e: KeyboardEvent, to: Move) => {
    e.preventDefault();
    wantFocus.current = true;
    const meta = e.metaKey || e.ctrlKey;
    act({ type: "move", to, shift: e.shiftKey, meta, page: page() });
    if (!meta && !e.shiftKey) {
      if (follow.current) window.clearTimeout(follow.current);
      follow.current = window.setTimeout(() => { const id = selRef.current.focus; if (id) onOpen(id); }, FOLLOW_MS);
    }
  };

  const across = (e: KeyboardEvent) => {
    const row = sel.focus ? rowEls.current.get(sel.focus) : undefined;
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
        if (sel.focus && e.target === rowEls.current.get(sel.focus)) {
          e.preventDefault();
          onOpen(sel.focus);
        }
        return; // on a control, Enter is the control's
      case "Escape":
        if (!e.defaultPrevented) act({ type: "clear" }); // only an Esc nothing else handled
        return;
      case " ":
        return; // Space is the player's (main spec §12)
    }
    shortcut(e);
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
    // a row that left the list hands focus on.
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
    return (
      <div
        key={v.key}
        ref={(el) => {
          if (el) rowEls.current.set(r.id, el);
          else rowEls.current.delete(r.id);
          virtualizer.measureElement(el); // null: stops observing rows no longer drawn
        }}
        data-index={v.index}
        role="row"
        aria-rowindex={v.index + 2}
        aria-selected={selected}
        aria-current={r.id === openId ? "true" : undefined}
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
            <div className="t"><span className="row-title">{r.title}</span>{r.private && <Lock size={13} className="lock" aria-label="Private" />}</div>
            <div className="m">{[r.when, r.duration, r.sources.join(", "), r.kind === "video" ? "video" : null].filter(Boolean).join(" · ")}</div>
            <TagChips
              tags={shownTags(r, tagging.pending)}
              title={r.title}
              canTag={tagging.canTag}
              place="row"
              onRemove={(tag, next) => {
                (next ?? rowEls.current.get(r.id))?.focus();
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
            {mark?.done && <span className="done-label">{"✓"} Tagged</span>}
            {mark?.failed && <span className="failed-label"><TriangleAlert aria-hidden="true" size={13} /> {mark.failed}</span>}
          </div>
        </div>
      </div>
    );
  };

  return (
    <section className="list" aria-label="Recordings">
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
        <span className="sr-only" role="status">{announce}</span>
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
      <SelectionBar count={sel.selected.size} lastTagged={tagging.lastTagged} onTag={openPicker} onClear={() => act({ type: "clear" })} />
    </section>
  );
}
