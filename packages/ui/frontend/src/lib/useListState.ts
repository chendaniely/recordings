import type { Tagging } from "../tagging/useTagging";
import type { Filter, LibraryRow } from "../types";
import { useOrderedRows, useRowOrder, type RowOrder } from "./useRowOrder";
import { useSelection, type Selection } from "./useSelection";

/** The phone's Tags page lists what the Library's Untagged filter does (spec §6). */
export const UNTAGGED: Filter = { kind: "untagged" };

/** A list over a Library filter: its order, its rows and its Finder selection (spec §6). */
export interface ListState extends Selection {
  order: RowOrder;
  rows: LibraryRow[];
}

/** The Library list's state, and the phone's Untagged list's on the Tags page. App holds both, as
 * it holds the grid's (useGridState), so each keeps its rows in place, its selection and every Undo
 * it hears through a visit to another page (Task 14 ruling), and the phone's list and the grid can
 * swap as a tablet turns without either losing its place. */
export function useListState(tagging: Pick<Tagging, "view" | "loadKey" | "revealed">, filter: Filter): ListState {
  const order = useRowOrder(tagging.view, filter, tagging.loadKey, tagging.revealed);
  const selection = useSelection(order);
  const rows = useOrderedRows(order.ids, tagging.view);
  return { ...selection, order, rows };
}
