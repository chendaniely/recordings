import type { VirtualItem } from "@tanstack/react-virtual";
import { Check, ChevronDown, Columns3, Lock, MoreHorizontal, TriangleAlert } from "lucide-react";
import {
  useEffect, useId, useMemo, useRef, useState, type CSSProperties, type FocusEvent, type KeyboardEvent, type MouseEvent,
} from "react";

import {
  DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

import { digitColumn, digitFor, foldersOf, tagsOf, type RowSort } from "../lib/grid";
import { plainShortcut } from "../lib/keys";
import { doneLabel, shownTags } from "../lib/library";
import { openHref } from "../lib/nav";
import { targetsOf, type Move } from "../lib/selection";
import type { GridState } from "../lib/useGridState";
import { useListShortcuts } from "../lib/useListShortcuts";
import type { ListState } from "../lib/useListState";
import { PHONE, useMediaQuery } from "../lib/useMediaQuery";
import { tabStopIndex, useRovingFocus } from "../lib/useRovingFocus";
import type { Announcement } from "../lib/useSelection";
import { useVirtualRows } from "../lib/useVirtualRows";
import { useTaggingContext } from "../tagging/useTagging";
import type { LibraryRow, LibraryView } from "../types";
import { HeadNotes, RefreshButton } from "./HeadNotes";
import { KEY_HINT, RecordingList, openPickerFor, useJustTagged } from "./RecordingList";
import { SelectionBar } from "./SelectionBar";

const ROW_ESTIMATE = 84; // with a two-line snippet; rows are measured as drawn
const HEAD = 56; // the pinned header row: a column's digit, then its tag on up to two lines
const PIN = 300; // the first column: fixed, so the pinned `private` column sits right beside it
const COL = 84;
const STICKY = PIN + COL; // the first column and the `private` column, pinned while scrolling across
export const GRID_HINT = `${KEY_HINT} · 1–9, 0 columns · ↑ from the top row: column menus`;
export const SELECT_ROWS = "Select rows first: X or Shift+arrows, or click a title.";

type SortBy = Exclude<RowSort["by"], "has">;
const SORTS: { by: SortBy; label: string }[] = [
  { by: "newest", label: "Newest" }, { by: "oldest", label: "Oldest" }, { by: "title", label: "Title" },
  { by: "duration", label: "Duration" },
];

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

/** A header's menu button: Down moves to the rows, as on the header's other controls, and the
 * button opens its menu with Enter or Space, once for a held key (fix round 2). Radix skips a key
 * the handler before it prevented. */
const menuButtonKeys = (e: KeyboardEvent) => {
  if (e.key === "ArrowDown" || (e.repeat && (e.key === "Enter" || e.key === " "))) e.preventDefault();
};

/** The first column's menu: Newest (the default), Oldest, Title and Duration (spec §7), as radio
 * items, Up and Down between them (fix round 1, item 12). A menu, never a <select>, which would
 * keep the arrows from crossing the headers. Radix gives focus back to the button on close. */
function RowSortMenu({ sort, onSort, tabStop }: { sort: RowSort; onSort: (sort: RowSort) => void; tabStop: boolean }) {
  const label = sortLabel(sort);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button type="button" className="tg-sort" data-head-col={0} tabIndex={tabStop ? 0 : -1} aria-label={`Sort rows: ${label}`} onKeyDown={menuButtonKeys}>
          <bdi dir="ltr">{label}</bdi> <ChevronDown aria-hidden="true" size={12} />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="tg-menu-list">
        <DropdownMenuRadioGroup value={sort.by === "has" ? "" : sort.by} onValueChange={(by) => onSort({ by: by as SortBy })}>
          {SORTS.map((s) => <DropdownMenuRadioItem key={s.by} value={s.by}>{s.label}</DropdownMenuRadioItem>)}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** A tag column's menu: "Has this tag first" (spec §7). The header's other button adds the tag. */
function ColumnMenu({ tag, col, sorted, onSort }: { tag: string; col: number; sorted: boolean; onSort: () => void }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button type="button" className="tg-menu" data-head-col={col} tabIndex={-1} aria-label={`Sort by ${tag}`} onKeyDown={menuButtonKeys}>
          <MoreHorizontal aria-hidden="true" size={14} />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="tg-menu-list">
        <DropdownMenuRadioGroup value={sorted ? "has" : ""} onValueChange={onSort}>
          <DropdownMenuRadioItem value="has">Has this tag first</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** The column picker, by folder (spec §7). `private` is always shown, pinned. A private column is
 * hidden for this session only; only the choices for the others are kept in this browser. A column
 * ticked here goes at the end of its group (useGridState). */
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

/** The Tags page grid (spec §7), for triage: a row per recording, a checkbox column per tag. Its
 * state (filters, sort, selection, the frozen columns) is App's (useGridState), so it survives a
 * visit to the Library.
 * - Rows: Untagged only by default, with a live "N left"; a title filter and a sort; drawn
 *   virtually; staying put until Refresh or a filter change (spec §6). The selection is Finder's,
 *   as the Library list's: a click on the pinned cell replaces it, Cmd and Shift add, the checkbox
 *   toggles.
 * - Columns: the 15 most-used tags, frozen per load; `private` pinned beside the first column,
 *   other private tags grouped at the right under a lock; a tag seen for the first time goes at the
 *   end of its group.
 * - Keys: one tab stop (WAI-ARIA grid pattern; with no rows, the first header). Arrows move
 *   between cells; Up from the first row reaches the headers, whose controls arrows cross and Down
 *   leaves. Cmd/Ctrl with Home and End moves focus to the first or last row only. Space or Enter
 *   toggles the focused tag cell; Enter on the pinned cell is Open, in a new tab. 1–9 and 0 toggle
 *   the first ten columns on the focused row, or add to a selection of two or more. A held key acts
 *   once. A header's buttons keep their own Enter and Space: the first adds its tag to the
 *   selection, the second opens its sort menu. T, Cmd-A, Cmd-Z and R are the Library's.
 * Focus follows keys, survives its row leaving, and comes back into view, as the list's does
 * (useRovingFocus). Keys, clicks and focus from a portal (a header's menu) are never the grid's. */
function TagsGrid({ grid }: { grid: GridState }) {
  const tagging = useTaggingContext();
  const view = tagging.view as LibraryView; // App draws this page only once the library is in
  const { untaggedOnly, title, sort, rows, sel, act, announce, fitted, col, columns, cols, isPrivateCol } = grid;
  const ids = useId();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [said, setSaid] = useState<Announcement>({ text: "", key: 0 });
  const [hint, setHint] = useState("");
  const justTagged = useJustTagged(sel.selected, tagging);
  const at = Math.min(col, cols.length);

  const rowIds = useMemo(() => rows.map((r) => r.id), [rows]);
  const index = useMemo(() => new Map(rowIds.map((id, i) => [id, i])), [rowIds]);
  const byId = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);
  const indexOf = (id: string | null) => (id === null ? -1 : (index.get(id) ?? -1));
  const focusIdx = indexOf(sel.focus);
  const barShown = sel.selected.size >= 2;

  // The focused row (or, with none, the first: the tab stop) and the anchor always stay rendered.
  const virtualizer = useVirtualRows({
    ids: rowIds, scrollRef, rowSize: ROW_ESTIMATE, overscan: 6, keep: [tabStopIndex(focusIdx), indexOf(sel.anchor)],
    scrollMargin: HEAD, scrollPaddingStart: HEAD,
  });
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
  const roving = useRovingFocus({
    scrollRef, virtualizer, ids: rowIds, index, sel, act, fitted, barShown, head: HEAD, across,
    target: (row) => row.querySelector<HTMLElement>(`[data-cell="${at}"]`),
  });
  const { reveal } = roving;
  const headControls = () => [...(scrollRef.current?.querySelectorAll<HTMLElement>("[data-head-col]") ?? [])];
  /** Puts focus on a cell of the focused row (the current one when `c` is left out), once drawn. */
  const toCell = (c?: number) => {
    if (c !== undefined) grid.setCol(Math.max(0, Math.min(cols.length, c)));
    roving.focusSoon();
  };
  const say = (text: string) => setSaid((s) => ({ text, key: s.key + 1 }));

  // Back from another page: the focused row and cell come into view, where they were left.
  useEffect(() => reveal(), []);
  // The selection's own words (hidden rows deselected, Cmd-A's count) go out through the grid's
  // one live region, with its hints; never again for words said before the page last opened.
  const heard = useRef(announce.key);
  useEffect(() => {
    if (announce.key === heard.current) return;
    heard.current = announce.key;
    say(announce.text);
  }, [announce.key]);
  // A hint is about the selection there was: a new one ends it.
  useEffect(() => setHint(""), [sel.selected]);

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
  const openPicker = () => openPickerFor(targetsOf(roving.selRef.current, rowIds), rows, tagging);
  const shortcut = useListShortcuts(act, openPicker); // Cmd-A, Cmd-Z, T and R, as the list's
  /** Clear, from the bar's button or Esc in the bar: the bar goes, so focus goes back to the cell. */
  const clear = () => {
    act({ type: "clear" });
    roving.focusSoon();
  };

  const page = () => Math.max(1, Math.floor(((scrollRef.current?.clientHeight ?? ROW_ESTIMATE * 10) - HEAD) / ROW_ESTIMATE) - 1);
  // Cmd/Ctrl moves focus only, Home and End included (spec §6); Shift extends.
  const move = (e: KeyboardEvent, to: Move) => {
    e.preventDefault();
    act({ type: "move", to, shift: e.shiftKey, meta: e.metaKey || e.ctrlKey, page: page() });
    roving.focusSoon();
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
        if (id === null) return true; // no rows: nowhere to go
        if (sel.focus === null) act({ type: "focus", id });
        toCell(Number(control?.dataset.headCol ?? 0));
        return true;
      }
      case "Enter":
      case " ":
        // The button's own, once: Chromium clicks a button again on each repeat of a held Enter.
        if (e.repeat) e.preventDefault();
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
    const mod = e.metaKey || e.ctrlKey;
    if (roving.toggleKey(e)) return true;
    switch (e.key) {
      case "ArrowDown":
        move(e, "next");
        return true;
      case "ArrowUp":
        if (focusIdx === 0 && !e.shiftKey && !mod) {
          e.preventDefault();
          toHeader(c); // the selection stays as it is
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
        // Home and End stay in the row; with Cmd or Ctrl, focus goes to the first or last row.
        if (mod) move(e, e.key === "Home" ? "first" : "last");
        else {
          e.preventDefault();
          toCell(e.key === "Home" ? 0 : cols.length);
        }
        return true;
      case "ArrowLeft":
      case "ArrowRight":
        if (mod || e.shiftKey) return true; // the browser's: Cmd+Left is Back on macOS
        e.preventDefault();
        toCell(c + (e.key === "ArrowRight" ? 1 : -1));
        return true;
      case " ":
        if (onButton) return true;
        e.preventDefault(); // the grid has no player: Space toggles a tag cell, and never scrolls
        if (e.repeat || !r || c === 0) return true; // a held key acts once
        reveal();
        toggle(r, cols[c - 1]);
        return true;
      case "Enter":
        if (onButton) return true;
        e.preventDefault();
        if (e.repeat || !r) return true;
        reveal();
        if (c === 0) openInNewTab(r.id);
        else toggle(r, cols[c - 1]);
        return true;
    }
    return false;
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (roving.ignores(e)) return;
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
    if (e.repeat) return; // a held digit acts once
    reveal();
    const row = sel.focus ? byId.get(sel.focus) : undefined;
    if (sel.selected.size >= 2) addToSelection(tag, "digit");
    else if (row) toggle(row, tag);
  };

  // Focus moving into a cell (by click, a key or Tab) makes it the focused cell.
  const onFocus = (e: FocusEvent<HTMLDivElement>) => {
    const el = roving.onFocus(e);
    const cell = el?.closest<HTMLElement>("[data-cell]");
    if (cell) grid.setCol(Number(cell.dataset.cell));
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
  const pickable = [...new Set([...tagsOf(view), ...cols])].filter((t) => t !== "private").sort((a, b) => a.localeCompare(b));
  const template = `${PIN}px repeat(${cols.length}, ${COL}px)`;
  const width = { "--tg-width": `${PIN + cols.length * COL}px` } as CSSProperties;
  const selectedCount = sel.selected.size;
  const empty = grid.order.key > 0 && rows.length === 0;

  const drawRow = (v: VirtualItem) => {
    const r = rows[v.index];
    const selected = sel.selected.has(r.id);
    const mark = tagging.marks[r.id];
    const shown = shownTags(r, tagging.pending);
    const snippet = tagging.snippet(r.id);
    const line = untaggedOnly ? snippetLine(r, snippet) : null;
    const tabStop = (c: number) => (focusIdx < 0 ? v.index === 0 && c === 0 : r.id === sel.focus && c === at);
    // The row's and its header's name: the title and the row's state, never its snippet (the
    // header's description, once it is in) or Open's name, or every column's tag (fix round 1).
    const id = `${ids}${v.index}-`;
    const names = [
      `${id}t`, ...(r.private ? [`${id}l`] : []), `${id}w`, ...(mark?.done ? [`${id}d`] : []), ...(mark?.failed ? [`${id}f`] : []),
    ].join(" ");
    return (
      <div
        key={v.key}
        ref={virtualizer.measureElement}
        data-index={v.index}
        role="row"
        aria-rowindex={v.index + 2}
        aria-selected={selected}
        aria-labelledby={names}
        data-testid="grid-row"
        data-id={r.id}
        className={cn("tg-row", selected && "sel", mark?.done && "done", mark?.failed && "failed")}
        style={{ gridTemplateColumns: template, transform: `translateY(${v.start - HEAD}px)` }}
      >
        <div
          role="rowheader"
          aria-colindex={1}
          aria-labelledby={names}
          aria-describedby={line !== null && snippet !== undefined ? `${id}s` : undefined}
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
              {mark?.done && <span className="done-label" id={`${id}d`}>{"✓"} {doneLabel(mark.done)}</span>}
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
              // No stopPropagation (Radix would take the click as consumed and keep a popover
              // open); onPinClick ignores [data-control=open], so the selection stays as it is.
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
                aria-keyshortcuts={digitFor(j) ?? undefined}
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
          <input type="checkbox" checked={untaggedOnly} onChange={(e) => grid.setUntaggedOnly(e.target.checked)} data-testid="untagged-only" />
          Untagged only
        </label>
        <span className="left-count" data-testid="left-count">{view.counts.untagged.toLocaleString("en")} left</span>
        <input
          type="search"
          className="title-filter"
          placeholder="Filter by title"
          aria-label="Filter by title"
          value={title}
          onChange={(e) => grid.setTitle(e.target.value)}
          data-testid="title-filter"
        />
        <ColumnPicker tags={pickable} visible={visible} isPrivate={isPrivateCol} onToggle={grid.toggleColumn} />
        <RefreshButton testId="grid-refresh" />
        <HeadNotes hint={GRID_HINT} announce={said} />
        {hint && <span className="grid-hint" aria-hidden="true">{hint}</span>}
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
        onBlur={roving.onBlur}
      >
        <div role="rowgroup" className="tg-headgroup">
          <div role="row" aria-rowindex={1} className="tg-row tg-head" style={{ gridTemplateColumns: template, height: HEAD }}>
            <div role="columnheader" aria-colindex={1} aria-label="Recording" aria-sort={firstSort(sort)} className="tg-pin tg-h0">
              <span aria-hidden="true">Recording</span>
              <RowSortMenu sort={sort} onSort={grid.setSort} tabStop={rows.length === 0} />
            </div>
            {cols.map((tag, j) => {
              const digit = digitFor(j);
              const priv = isPrivateCol(tag);
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
                  <ColumnMenu tag={tag} col={j + 1} sorted={sorted} onSort={() => grid.setSort({ by: "has", tag })} />
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

/** On a phone the Tags page shows the Untagged list with the picker, not the grid (spec §6): the
 * Library's list, over its Untagged filter, with the live "N left". A tap selects (there is no pane
 * to open); a row's + and the selection bar's Tag… open the picker, as a bottom sheet. Its order,
 * rows and selection are App's (useListState), so rows tagged here stay put, marked done, until
 * Refresh, as everywhere (spec §6). */
function PhoneUntagged({ list }: { list: ListState }) {
  const tagging = useTaggingContext();
  const view = tagging.view as LibraryView; // App draws this page only once the library is in
  return (
    <main className="tags-page phone" data-testid="tags-page">
      <div className="grid-head">
        <h1>Tags</h1>
        <span className="left-count" data-testid="left-count">{view.counts.untagged.toLocaleString("en")} left</span>
      </div>
      <RecordingList
        rows={list.rows} sel={list.sel} act={list.act} openId={null} onOpen={() => {}} announce={list.announce} fitted={list.fitted}
      />
    </main>
  );
}

/** The Tags page (spec §7): the grid, or on a phone the Untagged list (spec §6). Both states are
 * App's, so turning a tablet swaps one for the other without either losing its place. */
export function TagsPage({ grid, untagged }: { grid: GridState; untagged: ListState }) {
  const phone = useMediaQuery(PHONE);
  return phone ? <PhoneUntagged list={untagged} /> : <TagsGrid grid={grid} />;
}
