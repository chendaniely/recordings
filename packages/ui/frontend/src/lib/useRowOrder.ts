import { useEffect, useMemo, useRef, useState } from "react";

import type { Filter, LibraryRow, LibraryView } from "../types";
import { gridOrder, gridRows, sortRows, type RowSort } from "./grid";
import { orderFor, withIds } from "./library";
import { filterRows } from "./transcript";

export interface RowOrder { ids: string[]; key: number; reason: "load" | "filter" | "reveal" }

/** An order's IDs, worked out on load, on Refresh and when what decides it changes (`settings`,
 * compared as JSON), never on a tag change: a row whose tags change stays put, marked done (spec
 * §6). An Undo puts the rows it touched back into the current view, at their place in `all`:
 * only those that pass this view's own filter (`passes`, read after the Undo's reply; Task 14 fix
 * round 2), so the Library's Untagged never gets a tagged row back from a grid Undo, nor the grid
 * one its title filter leaves out. Both orders live in App, which never unmounts, so each hears
 * every Undo, wherever it was made. An Undo heard before the order was mounted puts nothing back:
 * that guard only matters to an order mounted later. */
function useOrder(
  view: LibraryView | null, settings: unknown, idsOf: (v: LibraryView) => string[], allOf: (v: LibraryView) => readonly LibraryRow[],
  passes: (row: LibraryRow) => boolean, loadKey: number, revealed: { key: number; ids: string[] },
): RowOrder {
  const [order, setOrder] = useState<RowOrder>({ ids: [], key: 0, reason: "load" });
  const viewRef = useRef(view);
  viewRef.current = view;
  const ways = useRef({ idsOf, allOf, passes });
  ways.current = { idsOf, allOf, passes };
  const lastLoad = useRef(-1);
  const heard = useRef(revealed.key); // the last Undo whose rows were put back, or were older
  const settingsKey = JSON.stringify(settings);

  useEffect(() => {
    const v = viewRef.current;
    if (!v) return;
    const reason = lastLoad.current !== loadKey ? "load" : "filter";
    lastLoad.current = loadKey;
    const ids = ways.current.idsOf(v);
    setOrder((o) => ({ ids, key: o.key + 1, reason }));
  }, [loadKey, settingsKey]);

  useEffect(() => {
    const v = viewRef.current;
    if (revealed.key === heard.current) return;
    heard.current = revealed.key;
    if (!v || revealed.ids.length === 0) return;
    const touched = new Set(revealed.ids);
    const back = v.recordings.filter((r) => touched.has(r.id) && ways.current.passes(r)).map((r) => r.id);
    if (back.length === 0) return; // nothing of this Undo belongs in this view
    const all = ways.current.allOf(v);
    setOrder((o) => ({ ids: withIds(o.ids, back, all), key: o.key + 1, reason: "reveal" }));
  }, [revealed]);

  return order;
}

/** The rows a Library filter shows. */
export function useRowOrder(view: LibraryView | null, filter: Filter, loadKey: number, revealed: { key: number; ids: string[] }): RowOrder {
  const passes = (r: LibraryRow) => filterRows([r], filter).length > 0;
  return useOrder(view, filter, (v) => orderFor(v.recordings, filter), (v) => v.recordings, passes, loadKey, revealed);
}

/** What decides the Tags grid's rows (spec §7). */
export interface GridSettings { untaggedOnly: boolean; title: string; sort: RowSort }

/** The rows the Tags grid shows: Untagged only, the title filter and the sort. A change to any of
 * them is a filter change, so the new order shows from the top. */
export function useGridOrder(view: LibraryView | null, settings: GridSettings, loadKey: number, revealed: { key: number; ids: string[] }): RowOrder {
  const passes = (r: LibraryRow) => gridRows([r], settings).length > 0;
  return useOrder(
    view, settings, (v) => gridOrder(v.recordings, settings), (v) => sortRows(v.recordings, settings.sort), passes, loadKey, revealed,
  );
}

/** An order's rows, read from the merged library, so a tag change shows in place (spec §6). */
export function useOrderedRows(ids: readonly string[], view: LibraryView | null): LibraryRow[] {
  const byId = useMemo(() => new Map((view?.recordings ?? []).map((r) => [r.id, r])), [view]);
  return useMemo(() => ids.map((id) => byId.get(id)).filter((r): r is LibraryRow => r !== undefined), [ids, byId]);
}
