import { useCallback, useEffect, useRef, useState } from "react";

import { NO_SEL, hiddenText, prune, reduceSel, type Sel, type SelAction } from "./selection";
import type { RowOrder } from "./useRowOrder";

export interface Selection { sel: Sel; act: (action: SelAction) => void; announce: string }

/** Finder selection over a row order (spec §6), for the Library list and the phone's Untagged
 * list. A filter change drops hidden rows from the selection, says so ("9 hidden rows
 * deselected"), and puts focus and the anchor on the first row; a load or Refresh drops vanished
 * rows quietly and keeps focus and the anchor where it can; an Undo's rows coming back change
 * nothing. */
export function useSelection(order: RowOrder): Selection {
  const ids = useRef(order.ids);
  ids.current = order.ids;
  const [sel, setSel] = useState<Sel>(NO_SEL);
  const [announce, setAnnounce] = useState("");
  const act = useCallback((a: SelAction) => setSel((s) => reduceSel(s, a, ids.current)), []);
  const selNow = useRef(sel);
  selNow.current = sel;

  useEffect(() => {
    if (order.reason === "reveal") return;
    const before = selNow.current;
    const { sel: next, hidden } = prune(before, order.ids);
    if (order.reason === "filter") {
      setSel(next);
      setAnnounce(hiddenText(hidden));
      return;
    }
    const has = (id: string | null) => id !== null && order.ids.includes(id);
    const focus = has(before.focus) ? before.focus : next.focus;
    setSel({ ...next, focus, anchor: has(before.anchor) ? before.anchor : focus });
  }, [order.key]); // order.ids and order.reason change with order.key

  return { sel, act, announce };
}
