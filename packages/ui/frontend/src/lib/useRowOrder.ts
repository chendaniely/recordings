import { useEffect, useMemo, useRef, useState } from "react";

import type { Filter, LibraryRow, LibraryView } from "../types";
import { orderFor, withIds } from "./library";

export interface RowOrder { ids: string[]; key: number; reason: "load" | "filter" | "reveal" }

/** The rows a filter shows, worked out on load, on Refresh and on a filter change only, so a row
 * whose tags change stays put, marked done (spec §6). An Undo puts the rows it touched back. */
export function useRowOrder(view: LibraryView | null, filter: Filter, loadKey: number, revealed: { key: number; ids: string[] }): RowOrder {
  const [order, setOrder] = useState<RowOrder>({ ids: [], key: 0, reason: "load" });
  const viewRef = useRef(view);
  viewRef.current = view;
  const lastLoad = useRef(-1);
  const filterKey = JSON.stringify(filter);

  useEffect(() => {
    const v = viewRef.current;
    if (!v) return;
    const reason = lastLoad.current !== loadKey ? "load" : "filter";
    lastLoad.current = loadKey;
    const ids = orderFor(v.recordings, JSON.parse(filterKey) as Filter);
    setOrder((o) => ({ ids, key: o.key + 1, reason }));
  }, [loadKey, filterKey]);

  useEffect(() => {
    const v = viewRef.current;
    if (!v || revealed.ids.length === 0) return;
    setOrder((o) => ({ ids: withIds(o.ids, revealed.ids, v.recordings), key: o.key + 1, reason: "reveal" }));
  }, [revealed]);

  return order;
}

/** An order's rows, read from the merged library, so a tag change shows in place (spec §6). */
export function useOrderedRows(ids: readonly string[], view: LibraryView | null): LibraryRow[] {
  const byId = useMemo(() => new Map((view?.recordings ?? []).map((r) => [r.id, r])), [view]);
  return useMemo(() => ids.map((id) => byId.get(id)).filter((r): r is LibraryRow => r !== undefined), [ids, byId]);
}
