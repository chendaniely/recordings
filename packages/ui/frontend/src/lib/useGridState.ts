import { useMemo, useState } from "react";

import type { Tagging } from "../tagging/useTagging";
import type { LibraryRow } from "../types";
import {
  addColumn, columnOrder, dropColumn, loadChoices, saveChoices, startColumns, withSessionTags, type Columns, type RowSort,
} from "./grid";
import type { Sel, SelAction } from "./selection";
import { browserStorage } from "./theme";
import { useGridOrder, useOrderedRows, type RowOrder } from "./useRowOrder";
import { useSelection, type Announcement, type Fitted } from "./useSelection";

/** The Tags grid's state, held by App so it survives a visit to the Library or Status (Task 14
 * fix round 1, ruled): Untagged only, the title filter, the sort, the selection and the focused
 * cell's column, the columns frozen for this load, and the private columns hidden for the
 * session. Kept in memory only; only non-private column choices go to localStorage (spec §7). */
export interface GridState {
  untaggedOnly: boolean;
  setUntaggedOnly: (on: boolean) => void;
  title: string;
  setTitle: (title: string) => void;
  sort: RowSort;
  setSort: (sort: RowSort) => void;
  order: RowOrder;
  rows: LibraryRow[];
  sel: Sel;
  act: (action: SelAction) => void;
  announce: Announcement;
  fitted: Fitted;
  /** The focused cell's column: 0, the pinned cell; 1…, the tag columns. */
  col: number;
  setCol: (col: number) => void;
  /** The columns, frozen per load: `private` pinned, then the others, then the private group. */
  columns: Columns;
  cols: string[];
  /** A column in the private group, or a tag the view flags private (it gets the lock). */
  isPrivateCol: (tag: string) => boolean;
  /** The column picker: shows a column at the end of its group, or hides it. */
  toggleColumn: (tag: string) => void;
}

const NO_COLUMNS: Columns = { pinned: "private", normal: [], privateGroup: [] };

export function useGridState(tagging: Tagging): GridState {
  const { view } = tagging;
  const [untaggedOnly, setUntaggedOnly] = useState(true);
  const [title, setTitle] = useState("");
  const [sort, setSort] = useState<RowSort>({ by: "newest" });
  const settings = useMemo(() => ({ untaggedOnly, title, sort }), [untaggedOnly, title, sort]);
  const order = useGridOrder(view, settings, tagging.loadKey, tagging.revealed);
  const { sel, act, announce, fitted } = useSelection(order);
  const rows = useOrderedRows(order.ids, view);
  const [col, setCol] = useState(0);
  const [choices, setChoices] = useState(() => loadChoices(browserStorage()));
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());

  // The columns are worked out once per load, then only added to (spec §7, never live; Task 14 fix
  // round 1): a tag seen for the first time this session goes at the end of its group, unless
  // the user unticked it; nothing else moves them until Refresh.
  const [frozen, setFrozen] = useState<{ key: number; columns: Columns } | null>(null);
  let next = frozen;
  if (view && (next === null || next.key !== tagging.loadKey)) {
    const columns = startColumns(view, { session: tagging.sessionTags, isPrivate: tagging.isPrivate, choices, hidden });
    next = { key: tagging.loadKey, columns };
  }
  if (next) {
    const off = (tag: string) => hidden.has(tag) || choices[tag] === false;
    const columns = withSessionTags(next.columns, tagging.sessionTags, off, tagging.isPrivate);
    if (columns !== next.columns) next = { ...next, columns };
  }
  if (next !== frozen) setFrozen(next); // React redraws at once, before committing
  const columns = next?.columns ?? NO_COLUMNS;
  const isPrivateCol = (tag: string) => tag === "private" || tagging.isPrivate(tag) || columns.privateGroup.includes(tag);

  const toggleColumn = (tag: string) => {
    const shown = columns.normal.includes(tag) || columns.privateGroup.includes(tag);
    if (isPrivateCol(tag)) {
      // A private column: for this session only, so no private tag name is stored (spec §7).
      setHidden((h) => {
        const nextHidden = new Set(h);
        if (shown) nextHidden.add(tag);
        else nextHidden.delete(tag);
        return nextHidden;
      });
    } else {
      const nextChoices = { ...choices, [tag]: !shown };
      setChoices(nextChoices);
      if (view) saveChoices(browserStorage(), nextChoices, view);
    }
    setFrozen((f) => f && { ...f, columns: shown ? dropColumn(f.columns, tag) : addColumn(f.columns, tag, isPrivateCol) });
  };

  return {
    untaggedOnly, setUntaggedOnly, title, setTitle, sort, setSort, order, rows, sel, act, announce, fitted, col, setCol,
    columns, cols: columnOrder(columns), isPrivateCol, toggleColumn,
  };
}
