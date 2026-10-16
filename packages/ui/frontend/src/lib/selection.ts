/** Finder-style selection (spec §6), shared by the Library list and the Tags grid. `order` is the
 * rows in the current filter, drawn or not. */

export interface Sel { selected: ReadonlySet<string>; anchor: string | null; focus: string | null }
export const NO_SEL: Sel = { selected: new Set(), anchor: null, focus: null };

export type Move = "next" | "prev" | "pageDown" | "pageUp" | "first" | "last";
export type SelAction =
  | { type: "click"; id: string; meta: boolean; shift: boolean }
  | { type: "checkbox"; id: string; shift: boolean }
  | { type: "move"; to: Move; shift: boolean; meta: boolean; page?: number }
  | { type: "toggleFocused" }
  | { type: "focus"; id: string }
  | { type: "all" }
  | { type: "clear" };

function toggled(set: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(set);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

function span(order: readonly string[], a: string, b: string): string[] {
  const i = order.indexOf(a);
  const j = order.indexOf(b);
  if (i < 0 || j < 0) return [b];
  return order.slice(Math.min(i, j), Math.max(i, j) + 1);
}

export function reduceSel(sel: Sel, action: SelAction, order: readonly string[]): Sel {
  switch (action.type) {
    case "click": {
      const { id } = action;
      if (action.shift && sel.anchor !== null && order.includes(sel.anchor)) {
        const range = span(order, sel.anchor, id);
        const selected = action.meta ? new Set([...sel.selected, ...range]) : new Set(range);
        return { selected, anchor: sel.anchor, focus: id };
      }
      if (action.meta) return { selected: toggled(sel.selected, id), anchor: id, focus: id };
      return { selected: new Set([id]), anchor: id, focus: id };
    }
    case "checkbox": // as Cmd-click, and an added range with Shift
      return reduceSel(sel, { type: "click", id: action.id, meta: true, shift: action.shift }, order);
    case "move": {
      if (order.length === 0) return sel;
      const at = sel.focus === null ? -1 : order.indexOf(sel.focus);
      const page = Math.max(1, action.page ?? 10);
      const wanted = {
        next: at + 1, prev: at < 0 ? 0 : at - 1, pageDown: at + page, pageUp: at - page,
        first: 0, last: order.length - 1,
      }[action.to];
      const focus = order[Math.min(order.length - 1, Math.max(0, wanted))];
      if (action.meta) return { ...sel, focus };
      if (action.shift) {
        const anchor = sel.anchor !== null && order.includes(sel.anchor) ? sel.anchor : (sel.focus ?? focus);
        return { selected: new Set(span(order, anchor, focus)), anchor, focus };
      }
      return { selected: new Set([focus]), anchor: focus, focus };
    }
    case "toggleFocused":
      if (sel.focus === null) return sel;
      return { selected: toggled(sel.selected, sel.focus), anchor: sel.focus, focus: sel.focus };
    case "focus":
      return { ...sel, focus: action.id };
    case "all":
      return { selected: new Set(order), anchor: sel.anchor ?? order[0] ?? null, focus: sel.focus ?? order[0] ?? null };
    case "clear":
      return { ...sel, selected: new Set() };
  }
}

/** A filter change: hidden rows leave the selection (announced), focus goes to the first row. */
export function prune(sel: Sel, order: readonly string[]): { sel: Sel; hidden: number } {
  const keep = new Set(order);
  const selected = new Set([...sel.selected].filter((id) => keep.has(id)));
  return {
    // Focus and anchor both go to the first row, so Shift+Down then selects it and the next.
    sel: { selected, anchor: order[0] ?? null, focus: order[0] ?? null },
    hidden: sel.selected.size - selected.size,
  };
}

/** What `T`, the selection bar and Cmd-Z's neighbours act on: the selection in list order, or the
 * focused row when nothing is selected (tagging plan, decision 4). */
export function targetsOf(sel: Sel, order: readonly string[]): string[] {
  if (sel.selected.size === 0) return sel.focus !== null && order.includes(sel.focus) ? [sel.focus] : [];
  return order.filter((id) => sel.selected.has(id));
}

/** The live-region announcement after a filter change deselects rows. */
export function hiddenText(hidden: number): string {
  if (hidden <= 0) return "";
  return `${hidden.toLocaleString("en")} hidden row${hidden === 1 ? "" : "s"} deselected`;
}
