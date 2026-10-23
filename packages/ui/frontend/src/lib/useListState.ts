import type { Tagging } from "../tagging/useTagging";
import type { Filter, LibraryRow } from "../types";
import { useOrderedRows, useRowOrder, type RowOrder } from "./useRowOrder";
import { useSelection, type Selection } from "./useSelection";

/** The Library's Untagged filter, which a phone's Tags tab opens (spec §6). */
export const UNTAGGED: Filter = { kind: "untagged" };

/** A list over a Library filter: its order, its rows and its Finder selection (spec §6). */
export interface ListState extends Selection {
  order: RowOrder;
  rows: LibraryRow[];
}

/** The Library list's state. App holds it, as it holds the grid's (useGridState), so each keeps its
 * rows in place, its selection and every Undo it hears through a visit to another page (Task 14
 * ruling). */
export function useListState(tagging: Pick<Tagging, "view" | "loadKey" | "revealed">, filter: Filter): ListState {
  const order = useRowOrder(tagging.view, filter, tagging.loadKey, tagging.revealed);
  const selection = useSelection(order);
  const rows = useOrderedRows(order.ids, tagging.view);
  return { ...selection, order, rows };
}
