import { useEffect, useRef, useState, type FocusEvent, type KeyboardEvent, type RefObject } from "react";

import { composing, isTextEntry, toggleFocusedKey } from "./keys";
import type { Sel, SelAction } from "./selection";
import type { Fitted } from "./useSelection";
import type { useVirtualRows } from "./useVirtualRows";

type Virtualizer = ReturnType<typeof useVirtualRows>;

/** The index the virtualiser keeps rendered for the tab stop: the focused row, or the first row
 * while nothing is focused, so the list or grid never loses its one tab stop to a scroll (Task 14
 * fix round 1). */
export const tabStopIndex = (focusIdx: number) => (focusIdx < 0 ? 0 : focusIdx);

export interface RovingFocusOptions {
  scrollRef: RefObject<HTMLDivElement | null>;
  virtualizer: Virtualizer;
  /** The rows drawn, in order, and their indexes. */
  ids: readonly string[];
  index: ReadonlyMap<string, number>;
  sel: Sel;
  act: (action: SelAction) => void;
  fitted: Fitted;
  /** The selection bar is showing: it takes room from the list or grid as it comes. */
  barShown: boolean;
  /** What takes focus in a drawn row: the row itself (the list), or its focused cell (the grid). */
  target: (row: HTMLElement) => HTMLElement | null;
  /** After a row is revealed, or focus lands: the grid scrolls the cell across into view. */
  across?: (el: HTMLElement) => void;
  /** A pinned header's height inside the scroll element (the grid's). */
  head?: number;
}

export interface RovingFocus {
  /** A drawn row, found in the page: the virtualiser draws and drops them as it scrolls. */
  rowEl: (id: string | null) => HTMLElement | undefined;
  /** Brings a row into view (the focused one by default, and then its target across too): before
   * a key acts on it, and after the order changes. */
  reveal: (i?: number) => void;
  /** Puts focus on the focused row's target once it is drawn: after a key moves it, or a bar goes. */
  focusSoon: () => void;
  /** The shortcut guard (spec §6): a key from a portal, in text, while composing, or with Alt. */
  ignores: (e: KeyboardEvent) => boolean;
  /** X or Cmd/Ctrl+Space toggles the focused row, shown first; true when it took the key. */
  toggleKey: (e: KeyboardEvent) => boolean;
  /** Focus moving in (a click, a key, Tab): the row it went to becomes the focused row. Returns
   * the element when it is in a row, null for a portal or a header. */
  onFocus: (e: FocusEvent<HTMLElement>) => HTMLElement | null;
  onBlur: (e: FocusEvent<HTMLElement>) => void;
  /** The selection as of the last render, for handlers called later. */
  selRef: RefObject<Sel>;
}

/** Roving focus over virtual rows, shared by the Library list and the Tags grid (spec §6;
 * decision 17: the list focuses rows, the grid cells). One tab stop, kept rendered.
 * - A key that moves focus scrolls the row into view, then focuses its target once drawn.
 * - When the row holding focus leaves (Refresh, a filter change), focus goes to the row the
 *   selection now focuses, so the keys go on working, unless focus has gone somewhere else.
 * - Once the selection is fitted to a new order: a filter change shows the rows from the top and
 *   leaves real focus where it is (in the control being used); otherwise (a Refresh, an Undo's
 *   rows coming back) the focused row comes back into view.
 * - The selection bar takes room as it comes: a frame later, a focused row it now cuts into is
 *   shown whole.
 * - Safari and Firefox on macOS don't focus a button on click, so a pointer pressed outside means
 *   focus left. */
export function useRovingFocus(opts: RovingFocusOptions): RovingFocus {
  const { scrollRef, virtualizer, ids, index, sel, act, fitted, barShown, head = 0 } = opts;
  const latest = useRef(opts);
  latest.current = opts;
  const selRef = useRef(sel);
  selRef.current = sel;
  const wantFocus = useRef(false);
  const focusIn = useRef<string | null>(null); // the row holding focus, while the list or grid holds it
  const [ask, setAsk] = useState(0);
  const focusIdx = sel.focus === null ? -1 : (index.get(sel.focus) ?? -1);

  const rowEl = (id: string | null) =>
    id === null ? undefined : [...(scrollRef.current?.querySelectorAll<HTMLElement>("[role=row][data-id]") ?? [])].find((el) => el.dataset.id === id);
  const reveal = (i = focusIdx) => {
    if (i < 0) return;
    virtualizer.scrollToIndex(i, { align: "auto" });
    const { across, target } = latest.current;
    if (!across || i !== focusIdx) return;
    const row = rowEl(sel.focus);
    const el = row ? target(row) : null;
    if (el) across(el);
  };
  const focusSoon = () => {
    wantFocus.current = true;
    setAsk((n) => n + 1);
  };

  useEffect(() => {
    const id = focusIn.current;
    if (id === null || index.has(id)) return;
    focusIn.current = null;
    const active = document.activeElement;
    if (ids.length > 0 && (active === null || active === document.body)) wantFocus.current = true;
  }, [index, ids]);

  useEffect(() => {
    if (!wantFocus.current) return;
    if (ids.length === 0) {
      wantFocus.current = false;
      return;
    }
    if (focusIdx < 0) return;
    wantFocus.current = false;
    reveal();
    const id = sel.focus;
    requestAnimationFrame(() => {
      const row = rowEl(id);
      const el = row ? latest.current.target(row) : null;
      if (!el) return;
      el.focus({ preventScroll: true });
      latest.current.across?.(el);
    });
  }, [sel, focusIdx, ids, ask, virtualizer]);

  useEffect(() => {
    if (fitted.reason === "filter") virtualizer.scrollToOffset(0);
    else reveal();
  }, [fitted.key]);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const box = scrollRef.current?.getBoundingClientRect();
      const row = rowEl(selRef.current.focus);
      const r = row?.getBoundingClientRect();
      if (!box || !row || !r) return;
      const top = box.top + head;
      const partly = r.bottom > top && r.top < box.bottom;
      if (partly && (r.bottom > box.bottom || r.top < top)) row.scrollIntoView({ block: "nearest" });
    });
    return () => cancelAnimationFrame(frame);
  }, [barShown]);

  useEffect(() => {
    const down = (e: PointerEvent) => {
      if (!scrollRef.current?.contains(e.target as Node)) focusIn.current = null;
    };
    document.addEventListener("pointerdown", down, true);
    return () => document.removeEventListener("pointerdown", down, true);
  }, []);

  return {
    rowEl,
    reveal,
    focusSoon,
    selRef,
    ignores: (e) => !e.currentTarget.contains(e.target as Node) || isTextEntry(e.target) || composing(e) || e.altKey,
    toggleKey: (e) => {
      if (!toggleFocusedKey(e)) return false;
      e.preventDefault();
      reveal(); // a key acts on the focused row: show it, wherever the wheel left it
      act({ type: "toggleFocused" });
      return true;
    },
    onFocus: (e) => {
      if (!e.currentTarget.contains(e.target as Node)) return null; // focus in a portal (a picker, a menu)
      const el = e.target as HTMLElement;
      const id = el.closest<HTMLElement>("[data-id]")?.dataset.id ?? null;
      focusIn.current = id;
      if (id !== null && selRef.current.focus !== id) act({ type: "focus", id });
      return id === null ? null : el;
    },
    onBlur: (e) => {
      // Focus left for somewhere named: it no longer holds it. A null relatedTarget is a removed
      // row (Chromium says so as it goes), another window, or a click on nothing: kept, so a row
      // that left hands focus on (a pointer pressed outside clears it, above).
      const to = e.relatedTarget;
      if (to !== null && !e.currentTarget.contains(to as Node)) focusIn.current = null;
    },
  };
}
