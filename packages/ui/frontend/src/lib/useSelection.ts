import { useCallback, useEffect, useRef, useState } from "react";

import { NO_SEL, hiddenText, prune, reduceSel, refit, type Sel, type SelAction } from "./selection";
import type { RowOrder } from "./useRowOrder";

/** Words for the live region. `key` is new each time, so the same words said twice are read out
 * twice (fix round 1). */
export interface Announcement { text: string; key: number }
/** The order the selection was last fitted to, and why it changed: the list scrolls to the top on
 * a filter change, and back to the focused row otherwise. */
export interface Fitted { key: number; reason: RowOrder["reason"] }

export interface Selection { sel: Sel; act: (action: SelAction) => void; announce: Announcement; fitted: Fitted }

/** Finder selection over a row order (spec §6), for the Library list.
 * - A filter change drops hidden rows from the selection, says so ("9 hidden rows deselected"),
 *   and puts focus and the anchor on the first row.
 * - A load or Refresh drops vanished rows quietly. Focus and the anchor stay, or go to the row
 *   that took their place (refit), so the user keeps their place.
 * - An Undo's rows coming back change nothing but the order.
 * - Cmd-A says how many it selected. */
export function useSelection(order: RowOrder): Selection {
  const ids = useRef(order.ids);
  ids.current = order.ids;
  const before = useRef<string[]>([]); // the order the selection was last fitted to
  const [sel, setSel] = useState<Sel>(NO_SEL);
  const [announce, setAnnounce] = useState<Announcement>({ text: "", key: 0 });
  const [fitted, setFitted] = useState<Fitted>({ key: order.key, reason: order.reason });
  const say = useCallback((text: string) => setAnnounce((a) => ({ text, key: a.key + 1 })), []);
  const act = useCallback((a: SelAction) => {
    setSel((s) => reduceSel(s, a, ids.current));
    if (a.type === "all") say(`${ids.current.length.toLocaleString("en")} selected`);
  }, [say]);
  const selNow = useRef(sel);
  selNow.current = sel;

  useEffect(() => {
    const was = before.current;
    before.current = order.ids;
    setFitted({ key: order.key, reason: order.reason });
    if (order.reason === "reveal") return;
    if (order.reason === "filter") {
      const { sel: next, hidden } = prune(selNow.current, order.ids);
      setSel(next);
      say(hiddenText(hidden));
      return;
    }
    setSel(refit(selNow.current, was, order.ids));
  }, [order.key]); // order.ids and order.reason change with order.key

  return { sel, act, announce, fitted };
}
