import type { VirtualItem } from "@tanstack/react-virtual";
import { Check, ChevronDown, Columns3, Lock, MoreHorizontal, TriangleAlert } from "lucide-react";
import {
  useEffect, useId, useMemo, useRef, useState, type CSSProperties, type FocusEvent, type KeyboardEvent, type MouseEvent,
} from "react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

import {
  columnOrder, defaultColumns, digitColumn, digitFor, foldersOf, layoutColumns, loadChoices, saveChoices, type RowSort,
} from "../lib/grid";
import { composing, isTextEntry, plainShortcut, toggleFocusedKey } from "../lib/keys";
import { shownTags } from "../lib/library";
import { openHref } from "../lib/nav";
import { targetsOf, type Move } from "../lib/selection";
import { browserStorage } from "../lib/theme";
import { useListShortcuts } from "../lib/useListShortcuts";
import { useGridOrder, useOrderedRows } from "../lib/useRowOrder";
import { useSelection, type Announcement } from "../lib/useSelection";
import { useVirtualRows } from "../lib/useVirtualRows";
import { useTaggingContext } from "../tagging/useTagging";
import type { LibraryRow, LibraryView } from "../types";
import { Announcer, KEY_HINT, openPickerFor, useJustTagged } from "./RecordingList";
import { SelectionBar } from "./SelectionBar";

const ROW_ESTIMATE = 84; // with a two-line snippet; rows are measured as drawn
const HEAD = 56; // the pinned header row: a column's digit, then its tag on up to two lines
const PIN = 300; // the first column: fixed, so the pinned `private` column sits right beside it
const COL = 84;
const STICKY = PIN + COL; // the first column and the `private` column, pinned while scrolling across
export const GRID_HINT = `${KEY_HINT} · 1–9, 0 columns`;
export const SELECT_ROWS = "Select rows first: click a title, or Shift-click for a range.";

type SortBy = Exclude<RowSort["by"], "has">;
const SORTS: { by: SortBy; label: string }[] = [
  { by: "newest", label: "Newest" }, { by: "oldest", label: "Oldest" }, { by: "title", label: "Title" },
  { by: "duration", label: "Duration" },
];

function allTags(view: LibraryView): string[] {
  return [...view.tags.map((t) => t.tag), ...view.note_types.map((n) => `notes/${n.note_type}`)];
}

/** The line under the title, with Untagged only (spec §7): the first words, as plain text; "…"
 * while they come; what there is to go on when there are none, and Open is the way to hear it.
 * None for a private recording, whose transcript is never read (spec §5). */
function snippetLine(r: LibraryRow, snippet: string | null | undefined): string | null {
  if (r.private) return null;
  if (snippet === undefined) return "…";
  return snippet ?? ["No transcript yet", r.duration ?? "length unknown", r.sources.join(", ") || "no source"].join(" · ");
}

/** The first column's aria-sort: Newest and Duration put the most first, Oldest and Title the least. */
function firstSort(sort: RowSort): "ascending" | "descending" | undefined {
  if (sort.by === "has") return undefined; // the tag's column carries it
  return sort.by === "oldest" || sort.by === "title" ? "ascending" : "descending";
}

const sortLabel = (sort: RowSort) => (sort.by === "has" ? `Has ${sort.tag} first` : SORTS.find((s) => s.by === sort.by)!.label);

/** A menu item that says which sort is on, in words (aria-pressed) as well as a check. */
function SortItem({ on, label, onPick }: { on: boolean; label: string; onPick: () => void }) {
  return (
    <button type="button" aria-pressed={on} onClick={onPick}>
      <Check aria-hidden="true" size={12} className={on ? undefined : "invisible"} /> {label}
    </button>
  );
}

/** The first column's menu: Newest (the default), Oldest, Title and Duration (spec §7). A menu,
 * not a <select>, so arrows move across the headers as they do across the cells. Radix gives focus
 * back to the trigger when it closes. */
function RowSortMenu({ sort, onSort }: { sort: RowSort; onSort: (sort: RowSort) => void }) {
  const [open, setOpen] = useState(false);
  const label = sortLabel(sort);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button type="button" className="tg-sort" data-head-col={0} tabIndex={-1} aria-label={`Sort rows: ${label}`}>
          <bdi dir="ltr">{label}</bdi> <ChevronDown aria-hidden="true" size={12} />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="tg-menu-list" aria-label="Sort rows">
        {SORTS.map((s) => (
          <SortItem key={s.by} on={sort.by === s.by} label={s.label} onPick={() => { onSort({ by: s.by }); setOpen(false); }} />
        ))}
      </PopoverContent>
    </Popover>
  );
}

/** A tag column's menu: "Has this tag first" (spec §7). The header's other button adds the tag. */
function ColumnMenu({ tag, col, sorted, onSort }: { tag: string; col: number; sorted: boolean; onSort: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button type="button" className="tg-menu" data-head-col={col} tabIndex={-1} aria-label={`Sort by ${tag}`}>
          <MoreHorizontal aria-hidden="true" size={14} />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="tg-menu-list" aria-label={`Sort by ${tag}`}>
        <SortItem on={sorted} label="Has this tag first" onPick={() => { onSort(); setOpen(false); }} />
      </PopoverContent>
    </Popover>
  );
}

/** The column picker, by folder (spec §7). `private` is always shown, pinned. A private column is
 * hidden for this session only; only the choices for the others are kept in this browser. */
function ColumnPicker({ tags, visible, isPrivate, onToggle }: {
  tags: string[]; visible: ReadonlySet<string>; isPrivate: (tag: string) => boolean; onToggle: (tag: string) => void;
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type="button" className="grid-btn" data-testid="columns"><Columns3 aria-hidden="true" size={14} /> Columns</button>
      </PopoverTrigger>
      <PopoverContent align="start" className="columns-picker" aria-label="Columns">
        {[...foldersOf(tags)].map(([folder, inIt]) => (
          <fieldset key={folder || "-"}>
            <legend>{folder || "No folder"}</legend>
            {inIt.map((tag) => (
              <label key={tag} className="column-choice">
                <input type="checkbox" checked={visible.has(tag)} onChange={() => onToggle(tag)} data-testid="column-choice" data-tag={tag} />
                {isPrivate(tag) && <Lock aria-hidden="true" size={12} />}
                <bdi dir="ltr">{tag}</bdi>
              </label>
            ))}
          </fieldset>
        ))}
      </PopoverContent>
    </Popover>
  );
}

/** The Tags page grid (spec §7), for triage: a row per recording, a checkbox column per tag.
 * - Rows: Untagged only by default, with a live "N left"; a title filter and a sort; drawn
 *   virtually; staying put until Refresh or a filter change (spec §6). The selection is Finder's,
 *   shared with the Library list (useSelection): a click on the pinned cell replaces it, Cmd and
 *   Shift add, the checkbox toggles.
 * - Columns: the 15 most-used tags, fixed per load, then this session's tags; `private` pinned
 *   beside the first column, other private tags grouped at the right under a lock; more from the
 *   column picker.
 * - Keys: one tab stop (WAI-ARIA grid pattern). Arrows move between cells; Up from the first row
 *   reaches the headers, whose controls arrows cross and Down leaves. Space or Enter toggles the
 *   focused tag cell; Enter on the pinned cell is Open, in a new tab. 1–9 and 0 toggle the first ten
 *   columns on the focused row, or add to a selection of two or more. A header's buttons keep their
 *   own Enter and Space: the first adds its tag to the selection, the second opens its sort menu.
 *   T, Cmd-A, Cmd-Z and R are the Library's (useListShortcuts).
 * Keys, clicks and focus from a portal (a header's menu), whose React events bubble through the
 * grid, are never the grid's. */
export function TagsPage() {
  const tagging = useTaggingContext();
  const view = tagging.view as LibraryView; // App draws this page only once the library is in
  const ids = useId();
  const scrollRef = useRef<HTMLDivElement>(null);
  const wantFocus = useRef(false); // a key or a closing bar moved focus: put it on the cell once drawn
  const focusIn = useRef<string | null>(null); // the row holding focus, while the grid holds it
  const [untaggedOnly, setUntaggedOnly] = useState(true);
  const [title, setTitle] = useState("");
  const [sort, setSort] = useState<RowSort>({ by: "newest" });
  const [choices, setChoices] = useState(() => loadChoices(browserStorage()));
  const [hiddenPrivate, setHiddenPrivate] = useState<ReadonlySet<string>>(new Set());
  const [col, setCol] = useState(0); // the focused cell's column: 0, the pinned cell; 1…, the tags
  const [focusAsk, setFocusAsk] = useState(0);
  const [said, setSaid] = useState<Announcement>({ text: "", key: 0 });
  const [hint, setHint] = useState("");

  const settings = useMemo(() => ({ untaggedOnly, title, sort }), [untaggedOnly, title, sort]);
  const order = useGridOrder(view, settings, tagging.loadKey, tagging.revealed);
  const { sel, act, announce, fitted } = useSelection(order);
  const rows = useOrderedRows(order.ids, view);
  const selRef = useRef(sel);
  selRef.current = sel;
  const justTagged = useJustTagged(sel.selected, tagging);

  // The default columns are worked out when the page opens, and again on each load and Refresh,
  // never live, so columns don't move under the cursor (spec §7).
  const [base, setBase] = useState(() => ({ key: tagging.loadKey, tags: defaultColumns(view) }));
  const fresh = base.key === tagging.loadKey ? base : { key: tagging.loadKey, tags: defaultColumns(view) };
  if (fresh !== base) setBase(fresh); // React redraws at once, before committing
  // A private tag seen since the last load stays private for the columns, so its column keeps its
  // place in the private group when its last use goes (and the view stops listing it).
  const seen = useRef({ key: -1, tags: new Set<string>() });
  if (seen.current.key !== tagging.loadKey) seen.current = { key: tagging.loadKey, tags: new Set() };
  for (const t of view.tags) if (t.private) seen.current.tags.add(t.tag);
  const privateCol = (tag: string) => tagging.isPrivate(tag) || seen.current.tags.has(tag);
  const columns = layoutColumns({
    base: fresh.tags, session: tagging.sessionTags, all: allTags(view), isPrivate: privateCol, choices, hidden: hiddenPrivate,
  });
  const cols = columnOrder(columns);
  const at = Math.min(col, cols.length);

  const rowIds = useMemo(() => rows.map((r) => r.id), [rows]);
  const index = useMemo(() => new Map(rowIds.map((id, i) => [id, i])), [rowIds]);
  const byId = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);
  const indexOf = (id: string | null) => (id === null ? -1 : (index.get(id) ?? -1));
  const focusIdx = indexOf(sel.focus);
  const barShown = sel.selected.size >= 2;

  // The focused row and the anchor always stay rendered, as in the Library list.
  const virtualizer = useVirtualRows({
    ids: rowIds, scrollRef, rowSize: ROW_ESTIMATE, overscan: 6, keep: [focusIdx, indexOf(sel.anchor)],
    scrollMargin: HEAD, scrollPaddingStart: HEAD,
  });
  /** Brings the focused row into view: before a key acts on it, and after the order changes. */
  const reveal = (i = focusIdx) => {
    if (i >= 0) virtualizer.scrollToIndex(i, { align: "auto" });
  };
  const rowEl = (id: string | null) =>
    id === null ? undefined : [...(scrollRef.current?.querySelectorAll<HTMLElement>("[role=row][data-id]") ?? [])].find((el) => el.dataset.id === id);
  const cellEl = (id: string | null, c: number) => rowEl(id)?.querySelector<HTMLElement>(`[data-cell="${c}"]`) ?? undefined;
  const headControls = () => [...(scrollRef.current?.querySelectorAll<HTMLElement>("[data-head-col]") ?? [])];
  /** Scrolls a cell or header across into view, clear of the pinned columns. */
  const across = (el: HTMLElement) => {
    const box = scrollRef.current;
    const cell = el.closest<HTMLElement>("[role=gridcell], [role=columnheader]");
    if (!box || !cell || cell.matches(".tg-pin, .pinned")) return;
    const b = box.getBoundingClientRect();
    const c = cell.getBoundingClientRect();
    if (c.left < b.left + STICKY) box.scrollLeft -= b.left + STICKY - c.left;
    else if (c.right > b.left + box.clientWidth) box.scrollLeft += c.right - (b.left + box.clientWidth);
  };
  /** Puts focus on a cell of the focused row (the current one when `c` is left out), once drawn. */
  const toCell = (c?: number) => {
    wantFocus.current = true;
    if (c !== undefined) setCol(Math.max(0, Math.min(cols.length, c)));
    setFocusAsk((n) => n + 1);
  };
  const say = (text: string) => setSaid((s) => ({ text, key: s.key + 1 }));

  // The selection's own words (hidden rows deselected, Cmd-A's count) go out through the grid's
  // one live region, with its hints.
  useEffect(() => {
    if (announce.key > 0) say(announce.text);
  }, [announce.key]);
  // A hint is about the selection there was: a new one ends it.
  useEffect(() => setHint(""), [sel.selected]);

  // The row that had focus left the grid (Refresh, a filter change): focus goes to the row the
  // selection now focuses, so the keys go on working, unless focus has gone somewhere else.
  useEffect(() => {
    const id = focusIn.current;
    if (id === null || index.has(id)) return;
    focusIn.current = null;
    const active = document.activeElement;
    if (rowIds.length > 0 && (active === null || active === document.body)) wantFocus.current = true;
  }, [index, rowIds]);

  // A key moved focus: the row scrolls into view, then its cell takes focus once drawn, and
  // scrolls across clear of the pinned columns. After a new order, the selection catches up a
  // render later.
  useEffect(() => {
    if (!wantFocus.current) return;
    if (rowIds.length === 0) {
      wantFocus.current = false;
      return;
    }
    if (focusIdx < 0) return;
    wantFocus.current = false;
    reveal();
    const id = sel.focus;
    const c = at;
    requestAnimationFrame(() => {
      const el = cellEl(id, c);
      if (!el) return;
      el.focus({ preventScroll: true });
      across(el);
    });
  }, [sel, focusIdx, rowIds, at, focusAsk, virtualizer]);

  // Once the selection is fitted to a new order: a filter change (Untagged only, the title, the
  // sort) shows the grid from the top, and leaves real focus in the control being used. Otherwise
  // (a Refresh, an Undo's rows coming back) the focused row comes back into view.
  useEffect(() => {
    if (fitted.reason === "filter") virtualizer.scrollToOffset(0);
    else reveal();
  }, [fitted.key]);

  // The selection bar takes room from the grid as it comes: a frame later, a focused row it now
  // cuts into is shown whole (as the Library list does).
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const box = scrollRef.current?.getBoundingClientRect();
      const row = rowEl(selRef.current.focus);
      const r = row?.getBoundingClientRect();
      if (!box || !row || !r) return;
      const top = box.top + HEAD;
      const partly = r.bottom > top && r.top < box.bottom;
      if (partly && (r.bottom > box.bottom || r.top < top)) row.scrollIntoView({ block: "nearest" });
    });
    return () => cancelAnimationFrame(frame);
  }, [barShown]);

  // Safari and Firefox on macOS don't focus a button on click: a pointer pressed outside the grid
  // means focus left it.
  useEffect(() => {
    const down = (e: PointerEvent) => {
      if (!scrollRef.current?.contains(e.target as Node)) focusIn.current = null;
    };
    document.addEventListener("pointerdown", down, true);
    return () => document.removeEventListener("pointerdown", down, true);
  }, []);

  // Snippets for the rows on screen, with Untagged only (spec §5, §7); never a private row's.
  const onScreen = untaggedOnly
    ? virtualizer.getVirtualItems().map((v) => rows[v.index]).filter((r) => r && !r.private).map((r) => r.id).join(",")
    : "";
  useEffect(() => {
    if (onScreen) tagging.wantSnippets(onScreen.split(","));
  }, [onScreen]);

  const toggle = (r: LibraryRow, tag: string) => {
    const shown = shownTags(r, tagging.pending).find((t) => t.tag === tag);
    if (shown?.pending) return; // a change is already on its way: the cell waits for its reply
    tagging.tag(shown ? "remove" : "add", [r.id], [tag], { kind: "cell" });
  };
  /** A header or a digit adds its tag to the selection; with none, it says so (spec §7). */
  const addToSelection = (tag: string, kind: "column" | "digit") => {
    const targets = rowIds.filter((id) => sel.selected.has(id));
    if (targets.length === 0) {
      setHint(SELECT_ROWS);
      say(SELECT_ROWS);
      return;
    }
    tagging.tag("add", targets, [tag], { kind });
  };
  const openInNewTab = (id: string) => {
    window.open(new URL(openHref(id), window.location.href).href, "_blank", "noopener");
  };
  const openPicker = () => openPickerFor(targetsOf(selRef.current, rowIds), rows, tagging);
  const shortcut = useListShortcuts(act, openPicker); // Cmd-A, Cmd-Z, T and R, as the list's
  /** Clear, from the bar's button or Esc in the bar: the bar goes, so focus goes back to the cell. */
  const clear = () => {
    act({ type: "clear" });
    toCell();
  };

  const page = () => Math.max(1, Math.floor(((scrollRef.current?.clientHeight ?? ROW_ESTIMATE * 10) - HEAD) / ROW_ESTIMATE) - 1);
  const move = (e: KeyboardEvent, to: Move, meta = e.metaKey || e.ctrlKey) => {
    e.preventDefault();
    act({ type: "move", to, shift: e.shiftKey, meta, page: page() });
    toCell();
  };
  const toHeader = (c: number) => {
    const el = headControls().find((h) => Number(h.dataset.headCol) === c);
    if (!el) return;
    el.focus({ preventScroll: true });
    across(el);
  };

  /** A key on a header's control: arrows cross the headers, Down goes back to the focused row;
   * Enter and Space are the button's own (spec §7: Enter adds the tag to the selection). */
  const headerKey = (e: KeyboardEvent, target: HTMLElement): boolean => {
    const controls = headControls();
    const control = target.closest<HTMLElement>("[data-head-col]");
    const i = control ? controls.indexOf(control) : -1;
    const go = (el: HTMLElement | undefined) => {
      e.preventDefault();
      if (el) {
        el.focus({ preventScroll: true });
        across(el);
      }
      return true;
    };
    switch (e.key) {
      case "ArrowLeft": return go(controls[Math.max(0, i - 1)]);
      case "ArrowRight": return go(controls[Math.min(controls.length - 1, i + 1)]);
      case "Home": return go(controls[0]);
      case "End": return go(controls[controls.length - 1]);
      case "ArrowUp":
        e.preventDefault();
        return true;
      case "ArrowDown": {
        e.preventDefault();
        const id = sel.focus ?? rowIds[0] ?? null;
        if (id === null) return true;
        if (sel.focus === null) act({ type: "focus", id });
        toCell(Number(control?.dataset.headCol ?? 0));
        return true;
      }
      case "Enter":
      case " ":
        return true;
    }
    return false;
  };

  /** A key in a row acts on the focused cell, as the grid holds it: keys typed faster than a frame
   * arrive before focus has moved to the cell the last one chose (it moves once drawn). */
  const cellKey = (e: KeyboardEvent, target: HTMLElement): boolean => {
    const c = at;
    const r = sel.focus ? byId.get(sel.focus) : undefined;
    const onButton = target.closest("button") !== null; // Open keeps its own Enter and Space
    if (toggleFocusedKey(e)) {
      e.preventDefault();
      reveal(); // a key acts on the focused row: show it, wherever the wheel left the grid
      act({ type: "toggleFocused" });
      return true;
    }
    switch (e.key) {
      case "ArrowDown":
        move(e, "next");
        return true;
      case "ArrowUp":
        if (focusIdx === 0 && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
          e.preventDefault();
          toHeader(c);
        } else {
          move(e, "prev");
        }
        return true;
      case "PageDown":
        move(e, "pageDown");
        return true;
      case "PageUp":
        move(e, "pageUp");
        return true;
      case "Home":
      case "End":
        // Home and End stay in the row; with Ctrl or Cmd they go to the first or last row (spec §6).
        if (e.ctrlKey || e.metaKey) move(e, e.key === "Home" ? "first" : "last", false);
        else {
          e.preventDefault();
          toCell(e.key === "Home" ? 0 : cols.length);
        }
        return true;
      case "ArrowLeft":
      case "ArrowRight":
        e.preventDefault();
        toCell(c + (e.key === "ArrowRight" ? 1 : -1));
        return true;
      case " ":
        if (onButton) return true;
        e.preventDefault(); // the grid has no player: Space toggles a tag cell, and never scrolls
        if (r && c > 0) {
          reveal();
          toggle(r, cols[c - 1]);
        }
        return true;
      case "Enter":
        if (onButton) return true;
        e.preventDefault();
        if (!r) return true;
        reveal();
        if (c === 0) openInNewTab(r.id);
        else toggle(r, cols[c - 1]);
        return true;
    }
    return false;
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!e.currentTarget.contains(e.target as Node) || isTextEntry(e.target) || composing(e) || e.altKey) return;
    const target = e.target as HTMLElement;
    if (target.closest("[role=columnheader]") ? headerKey(e, target) : target.closest("[role=row]") && cellKey(e, target)) return;
    if (e.key === "Escape") {
      if (!e.defaultPrevented) act({ type: "clear" }); // only an Esc nothing else handled
      return;
    }
    if (shortcut(e)) {
      if (e.key.toLowerCase() === "t") reveal();
      return;
    }
    const tag = plainShortcut(e) ? digitColumn(e.key, cols) : null;
    if (tag === null) return;
    e.preventDefault();
    reveal();
    const row = sel.focus ? byId.get(sel.focus) : undefined;
    if (sel.selected.size >= 2) addToSelection(tag, "digit");
    else if (row) toggle(row, tag);
  };

  // Focus moving into a cell (by click, a key or Tab) makes it the focused cell.
  const onFocus = (e: FocusEvent<HTMLDivElement>) => {
    if (!e.currentTarget.contains(e.target as Node)) return; // focus in a header's menu
    const target = e.target as HTMLElement;
    const id = target.closest<HTMLElement>("[data-id]")?.dataset.id ?? null;
    focusIn.current = id;
    if (id === null) return; // a header's control
    const cell = target.closest<HTMLElement>("[data-cell]");
    if (cell) setCol(Number(cell.dataset.cell));
    if (selRef.current.focus !== id) act({ type: "focus", id });
  };
  const onBlur = (e: FocusEvent<HTMLDivElement>) => {
    // As the list's: focus left for somewhere named. A null relatedTarget is a removed row.
    const to = e.relatedTarget;
    if (to !== null && !e.currentTarget.contains(to as Node)) focusIn.current = null;
  };

  /** The pinned cell: Finder-style, and it opens nothing (spec §7). Open opens, in a new tab. */
  const onPinClick = (e: MouseEvent<HTMLDivElement>, id: string) => {
    const target = e.target as Element;
    if (!e.currentTarget.contains(target) || target.closest("[data-control=open]")) return;
    if (target.closest("[data-control=checkbox]")) act({ type: "checkbox", id, shift: e.shiftKey });
    else act({ type: "click", id, meta: e.metaKey || e.ctrlKey, shift: e.shiftKey });
  };
  /** A click anywhere in a tag cell toggles that tag on that recording, and focuses the cell. */
  const onCellClick = (e: MouseEvent<HTMLDivElement>, r: LibraryRow, tag: string) => {
    if (!e.currentTarget.contains(e.target as Node)) return;
    e.currentTarget.querySelector<HTMLElement>("[role=checkbox]")?.focus({ preventScroll: true });
    toggle(r, tag);
  };

  const visible = new Set(cols);
  const pickable = allTags(view).filter((t) => t !== "private").sort((a, b) => a.localeCompare(b));
  const toggleColumn = (tag: string) => {
    if (privateCol(tag)) {
      // A private column: for this session only, so no private tag name is stored (spec §7).
      setHiddenPrivate((h) => {
        const next = new Set(h);
        if (next.has(tag)) next.delete(tag);
        else next.add(tag);
        return next;
      });
      return;
    }
    const next = { ...choices, [tag]: !visible.has(tag) };
    setChoices(next);
    saveChoices(browserStorage(), next, view);
  };

  const template = `${PIN}px repeat(${cols.length}, ${COL}px)`;
  const width = { "--tg-width": `${PIN + cols.length * COL}px` } as CSSProperties;
  const selectedCount = sel.selected.size;
  const empty = order.key > 0 && rows.length === 0;

  const drawRow = (v: VirtualItem) => {
    const r = rows[v.index];
    const selected = sel.selected.has(r.id);
    const mark = tagging.marks[r.id];
    const shown = shownTags(r, tagging.pending);
    const line = untaggedOnly ? snippetLine(r, tagging.snippet(r.id)) : null;
    const tabStop = (c: number) => (focusIdx < 0 ? v.index === 0 && c === 0 : r.id === sel.focus && c === at);
    // The row header's name: the title and the row's state, never its snippet (its description)
    // or Open's name, so a screen reader moving down a column hears little more than the title.
    const id = `${ids}${v.index}-`;
    const names = [
      `${id}t`, ...(r.private ? [`${id}l`] : []), `${id}w`, ...(mark?.done ? [`${id}d`] : []), ...(mark?.failed ? [`${id}f`] : []),
    ];
    return (
      <div
        key={v.key}
        ref={virtualizer.measureElement}
        data-index={v.index}
        role="row"
        aria-rowindex={v.index + 2}
        aria-selected={selected}
        data-testid="grid-row"
        data-id={r.id}
        className={cn("tg-row", selected && "sel", mark?.done && "done", mark?.failed && "failed")}
        style={{ gridTemplateColumns: template, transform: `translateY(${v.start - HEAD}px)` }}
      >
        <div
          role="rowheader"
          aria-colindex={1}
          aria-labelledby={names.join(" ")}
          aria-describedby={line === null ? undefined : `${id}s`}
          className="tg-pin"
          data-cell={0}
          tabIndex={tabStop(0) ? 0 : -1}
          onClick={(e) => onPinClick(e, r.id)}
        >
          <span className="check" aria-hidden="true" data-control="checkbox">{selected ? <Check /> : null}</span>
          <span className="tg-meta">
            <span className="tg-title-line">
              <span className="tg-title" id={`${id}t`} title={r.title}>{r.title}</span>
              {r.private && <Lock size={12} className="lock" aria-label="Private" id={`${id}l`} />}
            </span>
            <span className="tg-sub">
              <span id={`${id}w`}>{r.when}</span>
              {mark?.done && <span className="done-label" id={`${id}d`}>{"✓"} Tagged</span>}
              {mark?.failed && (
                <span className="failed-label" id={`${id}f`}><TriangleAlert aria-hidden="true" size={13} /> {mark.failed}</span>
              )}
            </span>
            {line !== null && <span className="tg-snippet" id={`${id}s`}>{line}</span>}
          </span>
          <button
            type="button"
            className="tg-open"
            data-control="open"
            tabIndex={-1}
            aria-label={`Open ${r.title} in a new tab`}
            onClick={(e) => {
              e.stopPropagation(); // never a pinned-cell click: the selection stays as it is
              openInNewTab(r.id);
            }}
          >
            Open
          </button>
        </div>
        {cols.map((tag, j) => {
          const t = shown.find((s) => s.tag === tag);
          const on = t !== undefined && t.pending !== "remove";
          return (
            <div
              key={tag}
              role="gridcell"
              aria-colindex={j + 2}
              className={cn("tg-cell", tag === columns.pinned && "pinned")}
              onClick={(e) => onCellClick(e, r, tag)}
            >
              <span
                role="checkbox"
                aria-checked={on}
                aria-label={t?.pending ? `${tag} (${t.pending === "add" ? "adding" : "removing"})` : tag}
                aria-disabled={!tagging.canTag || undefined}
                tabIndex={tabStop(j + 1) ? 0 : -1}
                data-cell={j + 1}
                data-testid="grid-cell"
                data-tag={tag}
                className={cn("tg-box", t?.pending && "pending")}
              >
                {on ? <Check aria-hidden="true" /> : null}
              </span>
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <main className="tags-page" data-testid="tags-page">
      <div className="grid-head">
        <h1>Tags</h1>
        <label className="grid-opt">
          <input type="checkbox" checked={untaggedOnly} onChange={(e) => setUntaggedOnly(e.target.checked)} data-testid="untagged-only" />
          Untagged only
        </label>
        <span className="left-count" data-testid="left-count">{view.counts.untagged.toLocaleString("en")} left</span>
        <input
          type="search"
          className="title-filter"
          placeholder="Filter by title"
          aria-label="Filter by title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          data-testid="title-filter"
        />
        <ColumnPicker tags={pickable} visible={visible} isPrivate={privateCol} onToggle={toggleColumn} />
        <button type="button" className="refresh" data-testid="grid-refresh" onClick={tagging.refresh}>
          {tagging.touched > 0 ? `${tagging.touched.toLocaleString("en")} tagged · Refresh` : "Refresh"}
        </button>
        {!tagging.saves && <span className="demo-note">Demo: changes aren't saved</span>}
        {tagging.opening ? (
          <span className="cannot-tag opening" data-testid="cannot-tag">Opening the archive{"…"}</span>
        ) : !tagging.canTag && (
          <span className="cannot-tag" data-testid="cannot-tag">Tagging is off: {tagging.reason}</span>
        )}
        <span className="key-hint" data-testid="key-hint">{GRID_HINT}</span>
        {hint && <span className="grid-hint" aria-hidden="true">{hint}</span>}
        <Announcer text={said.text} at={said.key} />
      </div>
      {empty && (
        <p className="tg-empty" data-testid="grid-empty">
          {title.trim() ? "No recording matches the filter." : untaggedOnly ? "No untagged recordings left." : "No recordings."}
        </p>
      )}
      <div
        ref={scrollRef}
        className={cn("tgrid", selectedCount > 0 && "has-selection")}
        style={{ ...width, scrollPaddingTop: HEAD }}
        role="grid"
        aria-multiselectable="true"
        aria-label="Tags"
        aria-rowcount={rows.length + 1}
        aria-colcount={cols.length + 1}
        data-testid="tags-grid"
        onKeyDown={onKeyDown}
        onFocus={onFocus}
        onBlur={onBlur}
      >
        <div role="rowgroup" className="tg-headgroup">
          <div role="row" aria-rowindex={1} className="tg-row tg-head" style={{ gridTemplateColumns: template, height: HEAD }}>
            <div role="columnheader" aria-colindex={1} aria-label="Recording" aria-sort={firstSort(sort)} className="tg-pin tg-h0">
              <span aria-hidden="true">Recording</span>
              <RowSortMenu sort={sort} onSort={setSort} />
            </div>
            {cols.map((tag, j) => {
              const digit = digitFor(j);
              const priv = tag === "private" || privateCol(tag);
              const sorted = sort.by === "has" && sort.tag === tag;
              return (
                <div
                  key={tag}
                  role="columnheader"
                  aria-colindex={j + 2}
                  aria-label={tag}
                  aria-sort={sorted ? "descending" : undefined}
                  className={cn("tg-h", priv && "priv", tag === columns.pinned && "pinned", sorted && "sorted")}
                  data-testid="grid-header"
                  data-tag={tag}
                >
                  <button
                    type="button"
                    className="tg-add"
                    data-head-col={j + 1}
                    tabIndex={-1}
                    title={selectedCount > 0 ? `Add ${tag} to ${selectedCount.toLocaleString("en")} selected` : `${tag}: select rows first`}
                    aria-label={selectedCount > 0 ? `Add ${tag} to ${selectedCount.toLocaleString("en")} selected` : `Add ${tag} to the selected rows`}
                    onClick={() => addToSelection(tag, "column")}
                  >
                    <span className="tg-hkeys" aria-hidden="true">
                      {digit && <kbd>{digit}</kbd>}
                      {priv && <Lock size={11} />}
                      {sorted && <span className="tg-sorted">{"↓"}</span>}
                    </span>
                    <span className="tg-hname"><bdi dir="ltr">{tag}</bdi></span>
                  </button>
                  <ColumnMenu tag={tag} col={j + 1} sorted={sorted} onSort={() => setSort({ by: "has", tag })} />
                </div>
              );
            })}
          </div>
        </div>
        <div role="rowgroup" className="tg-body" style={{ height: virtualizer.getTotalSize() }}>
          {virtualizer.getVirtualItems().map(drawRow)}
        </div>
      </div>
      <SelectionBar count={selectedCount} lastTagged={justTagged} onTag={openPicker} onClear={clear} />
    </main>
  );
}
