import { describe, expect, it } from "vitest";

import { NO_SEL, prune, reduceSel, targetsOf, type Sel, type SelAction } from "./selection";

const order = ["a", "b", "c", "d", "e"];
const run = (...actions: SelAction[]): Sel => actions.reduce((s, a) => reduceSel(s, a, order), NO_SEL);
const ids = (s: Sel) => [...s.selected].sort();
const click = (id: string, meta = false, shift = false): SelAction => ({ type: "click", id, meta, shift });

describe("clicks follow Finder (spec §6)", () => {
  it("a plain click selects one row and moves the anchor", () => {
    const s = run(click("b"), click("d"));
    expect(ids(s)).toEqual(["d"]);
    expect(s.anchor).toBe("d");
    expect(s.focus).toBe("d");
  });
  it("Cmd-click adds or removes a row and moves the anchor", () => {
    const s = run(click("b"), click("d", true), click("b", true));
    expect(ids(s)).toEqual(["d"]);
    expect(s.anchor).toBe("b");
  });
  it("Shift-click selects anchor to row, in either direction, keeping the anchor", () => {
    expect(ids(run(click("b"), click("d", false, true)))).toEqual(["b", "c", "d"]);
    const back = run(click("d"), click("b", false, true));
    expect(ids(back)).toEqual(["b", "c", "d"]);
    expect(back.anchor).toBe("d");
  });
  it("Shift-click replaces the selection; Cmd-Shift-click adds the range", () => {
    expect(ids(run(click("a"), click("e", true), click("c", false, true)))).toEqual(["c", "d", "e"]);
    expect(ids(run(click("a"), click("d", true), click("e", true, true)))).toEqual(["a", "d", "e"]);
  });
  it("the checkbox acts as Cmd-click, and as an added range with Shift", () => {
    expect(ids(run(click("a"), { type: "checkbox", id: "c", shift: false }))).toEqual(["a", "c"]);
    expect(ids(run(click("a"), { type: "checkbox", id: "c", shift: true }))).toEqual(["a", "b", "c"]);
  });
  it("Shift-click without an anchor is a plain click", () => {
    expect(ids(run(click("c", false, true)))).toEqual(["c"]);
  });
});

describe("keys follow Finder (spec §6)", () => {
  const move = (to: "next" | "prev" | "pageDown" | "pageUp" | "first" | "last", shift = false, meta = false): SelAction =>
    ({ type: "move", to, shift, meta, page: 2 });
  it("Up and Down move focus and select only the focused row", () => {
    const s = run(click("b"), move("next"), move("next"));
    expect(s.focus).toBe("d");
    expect(ids(s)).toEqual(["d"]);
    expect(run(move("next")).focus).toBe("a"); // from nothing focused: the first row
  });
  it("Shift+Up and Down extend from the anchor", () => {
    expect(ids(run(click("b"), move("next", true), move("next", true)))).toEqual(["b", "c", "d"]);
    expect(ids(run(click("c"), move("prev", true), move("prev", true)))).toEqual(["a", "b", "c"]);
  });
  it("Cmd+Up and Down move focus without changing the selection; Cmd+Space toggles", () => {
    const s = run(click("b"), move("next", false, true), move("next", false, true));
    expect(s.focus).toBe("d");
    expect(ids(s)).toEqual(["b"]);
    expect(ids(reduceSel(s, { type: "toggleFocused" }, order))).toEqual(["b", "d"]);
  });
  it("pages, the first and the last row, clamped, with Shift extending", () => {
    expect(run(click("a"), move("pageDown")).focus).toBe("c");
    expect(run(click("e"), move("pageDown")).focus).toBe("e");
    expect(run(click("b"), move("pageUp")).focus).toBe("a");
    expect(ids(run(click("b"), move("last", true)))).toEqual(["b", "c", "d", "e"]);
    expect(ids(run(click("d"), move("first")))).toEqual(["a"]);
  });
  it("Cmd-A selects every row in the filter, drawn or not; Esc clears", () => {
    const all = run(click("c"), { type: "all" });
    expect(ids(all)).toEqual(order);
    expect(all.focus).toBe("c");
    expect(ids(reduceSel(all, { type: "clear" }, order))).toEqual([]);
  });
});

describe("pruning and targets", () => {
  it("a filter change drops hidden rows, counts them, and focuses the first row", () => {
    const { sel, hidden } = prune(run(click("a"), click("c", true), click("e", true)), ["b", "c", "d"]);
    expect(ids(sel)).toEqual(["c"]);
    expect(hidden).toBe(2);
    expect(sel.anchor).toBe(null); // "e" is hidden now
    expect(sel.focus).toBe("b");
  });
  it("T targets the selection in list order, or the focused row when nothing is selected", () => {
    expect(targetsOf(run(click("d"), click("b", true)), order)).toEqual(["b", "d"]);
    const focusedOnly = reduceSel(run(click("c")), { type: "clear" }, order);
    expect(targetsOf(focusedOnly, order)).toEqual(["c"]);
    expect(targetsOf(NO_SEL, order)).toEqual([]);
  });
});
