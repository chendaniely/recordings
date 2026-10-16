import { useEffect, useMemo, useRef, useState } from "react";

import type { Filter, LibraryRow, LibraryView } from "../types";
import { gridOrder, sortRows, type RowSort } from "./grid";
import { orderFor, withIds } from "./library";

export interface RowOrder { ids: string[]; key: number; reason: "load" | "filter" | "reveal" }

/** An order's IDs, worked out on load, on Refresh and when what decides it changes (`settings`,
 * compared as JSON), never on a tag change: a row whose tags change stays put, marked done (spec
 * §6). An Undo puts the rows it touched back, at their place in `all`; an Undo heard before the
 * order was first worked out (one made in the Library before the Tags page opened) puts nothing
 * back. */
function useOrder(
  view: LibraryView | null, settings: unknown, idsOf: (v: LibraryView) => string[], allOf: (v: LibraryView) => readonly LibraryRow[],
  loadKey: number, revealed: { key: number; ids: string[] },
): RowOrder {
  const [order, setOrder] = useState<RowOrder>({ ids: [], key: 0, reason: "load" });
  const viewRef = useRef(view);
  viewRef.current = view;
  const ways = useRef({ idsOf, allOf });
  ways.current = { idsOf, allOf };
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
    const all = ways.current.allOf(v);
    setOrder((o) => ({ ids: withIds(o.ids, revealed.ids, all), key: o.key + 1, reason: "reveal" }));
  }, [revealed]);

  return order;
}

/** The rows a Library filter shows. */
export function useRowOrder(view: LibraryView | null, filter: Filter, loadKey: number, revealed: { key: number; ids: string[] }): RowOrder {
  return useOrder(view, filter, (v) => orderFor(v.recordings, filter), (v) => v.recordings, loadKey, revealed);
}

/** What decides the Tags grid's rows (spec §7). */
export interface GridSettings { untaggedOnly: boolean; title: string; sort: RowSort }

/** The rows the Tags grid shows: Untagged only, the title filter and the sort. A change to any of
 * them is a filter change, so the new order shows from the top. */
export function useGridOrder(view: LibraryView | null, settings: GridSettings, loadKey: number, revealed: { key: number; ids: string[] }): RowOrder {
  return useOrder(view, settings, (v) => gridOrder(v.recordings, settings), (v) => sortRows(v.recordings, settings.sort), loadKey, revealed);
}

/** An order's rows, read from the merged library, so a tag change shows in place (spec §6). */
export function useOrderedRows(ids: readonly string[], view: LibraryView | null): LibraryRow[] {
  const byId = useMemo(() => new Map((view?.recordings ?? []).map((r) => [r.id, r])), [view]);
  return useMemo(() => ids.map((id) => byId.get(id)).filter((r): r is LibraryRow => r !== undefined), [ids, byId]);
}
