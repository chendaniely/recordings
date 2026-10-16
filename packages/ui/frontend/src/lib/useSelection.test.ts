import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Filter, LibraryRow, LibraryView } from "../types";
import { useOrderedRows, useRowOrder, type RowOrder } from "./useRowOrder";
import { useSelection, type Selection } from "./useSelection";

const row = (id: string, tags: string[] = []): LibraryRow => ({
  id, title: `Week ${id}`, recorded_at: "2026-10-06T14:00:00-07:00", when: "", duration: null, duration_ms: null,
  kind: "audio", sources: [], tags: tags.map((tag) => ({ tag, by: "you" as const })), private: false,
  untagged: tags.length === 0,
});
const view = (...recordings: LibraryRow[]): LibraryView => ({
  recordings, counts: { all: recordings.length, untagged: recordings.filter((r) => r.untagged).length },
  tags: [], note_types: [], problems: [], can_tag: true, cannot_tag_reason: null, saves: true,
});
const ALL: Filter = { kind: "all" };
const UNTAGGED: Filter = { kind: "untagged" };

// React's production build has no act(). flushSync makes a render synchronous, but what the
// hooks' effects set (the order, then the selection pruned to it) renders in a later Scheduler
// task: settle() lets those run.
const settle = async () => {
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
};
const act = async (fn: () => void) => {
  flushSync(fn);
  await settle();
};

let order: RowOrder;
let selection: Selection;
let rows: LibraryRow[];
type Props = { v: LibraryView | null; filter: Filter; loadKey: number; revealed: { key: number; ids: string[] } };
function Probe({ v, filter, loadKey, revealed }: Props) {
  order = useRowOrder(v, filter, loadKey, revealed);
  selection = useSelection(order);
  rows = useOrderedRows(order.ids, v);
  return null;
}

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let props: Props;
const draw = (over: Partial<Props>) => {
  props = { ...props, ...over };
  return act(() => root.render(createElement(Probe, props)));
};
const selected = () => [...selection.sel.selected].sort();

beforeEach(() => {
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
  props = { v: null, filter: ALL, loadKey: 0, revealed: { key: 0, ids: [] } };
});
afterEach(() => {
  root.unmount();
  host.remove();
});

describe("useRowOrder: rows stay put (spec §6)", () => {
  it("works out the filter's rows on load, focusing the first", async () => {
    await draw({ v: view(row("a", ["talks"]), row("b"), row("c")), loadKey: 1, filter: UNTAGGED });
    expect(order.ids).toEqual(["b", "c"]);
    expect(order.reason).toBe("load");
    expect(selection.sel.focus).toBe("b");
  });
  it("keeps a row whose tags change in place, drawn with its new tags, until Refresh", async () => {
    await draw({ v: view(row("a"), row("b")), loadKey: 1, filter: UNTAGGED });
    await draw({ v: view(row("a", ["work"]), row("b")) }); // a reply's merge: same load
    expect(order.ids).toEqual(["a", "b"]);
    expect(rows[0].tags.map((t) => t.tag)).toEqual(["work"]);
    await draw({ v: view(row("a", ["work"]), row("b")), loadKey: 2 }); // Refresh
    expect(order.ids).toEqual(["b"]);
    expect(order.reason).toBe("load");
  });
  it("puts an Undo's rows back at their place, in the current filter", async () => {
    await draw({ v: view(row("a"), row("b"), row("c")), loadKey: 1, filter: UNTAGGED });
    await draw({ v: view(row("a"), row("b", ["work"]), row("c")), loadKey: 2 });
    expect(order.ids).toEqual(["a", "c"]);
    await draw({ revealed: { key: 1, ids: ["b"] } });
    expect(order.ids).toEqual(["a", "b", "c"]);
    expect(order.reason).toBe("reveal");
  });
});

describe("useSelection: Finder selection over the order (spec §6)", () => {
  const four = view(row("a", ["talks"]), row("b"), row("c", ["talks"]), row("d"));

  it("a filter change drops hidden rows, says so, and focuses the first row", async () => {
    await draw({ v: four, loadKey: 1 });
    await act(() => selection.act({ type: "click", id: "b", meta: false, shift: false }));
    await act(() => selection.act({ type: "click", id: "d", meta: true, shift: false }));
    await act(() => selection.act({ type: "click", id: "c", meta: true, shift: false }));
    expect(selected()).toEqual(["b", "c", "d"]);
    await draw({ filter: { kind: "tag", tag: "talks" } });
    expect(selected()).toEqual(["c"]);
    expect(selection.announce.text).toBe("2 hidden rows deselected");
    expect(selection.sel.focus).toBe("a");
    expect(selection.sel.anchor).toBe("a");
    await draw({ filter: ALL });
    expect(selection.announce.text).toBe(""); // nothing hidden this time
  });
  it("a load or Refresh drops vanished rows quietly, keeping focus and anchor where it can", async () => {
    const five = ["a", "b", "c", "d", "e"].map((id) => row(id));
    await draw({ v: view(...five), loadKey: 1, filter: UNTAGGED });
    await act(() => selection.act({ type: "click", id: "c", meta: false, shift: false }));
    await act(() => selection.act({ type: "click", id: "e", meta: false, shift: true }));
    expect(selection.sel).toMatchObject({ anchor: "c", focus: "e" });
    await draw({ v: view(row("a"), row("b"), row("c"), row("d"), row("e", ["work"])), loadKey: 2 });
    expect(selected()).toEqual(["c", "d"]);
    // e has gone, and was last: focus goes to the nearest row before it, d; the anchor stays.
    expect(selection.sel).toMatchObject({ anchor: "c", focus: "d" });
    expect(selection.announce.text).toBe("");
    await draw({ v: view(row("a"), row("b"), row("c"), row("d"), row("e", ["work"])), loadKey: 3 });
    expect(selection.sel).toMatchObject({ anchor: "c", focus: "d" }); // nothing gone: nothing moves
  });
  it("an Undo's rows coming back change nothing", async () => {
    await draw({ v: four, loadKey: 1, filter: UNTAGGED });
    await act(() => selection.act({ type: "click", id: "d", meta: false, shift: false }));
    const before = selection.sel;
    await draw({ revealed: { key: 1, ids: ["a"] } });
    expect(order.ids).toEqual(["a", "b", "d"]);
    expect(selection.sel).toBe(before);
  });
  it("Cmd-A selects every row in the filter, drawn or not", async () => {
    await draw({ v: four, loadKey: 1, filter: UNTAGGED });
    await act(() => selection.act({ type: "all" }));
    expect(selected()).toEqual(["b", "d"]);
  });
});

describe("useSelection, fix round 1 (Task 12)", () => {
  const five = () => ["a", "b", "c", "d", "e"].map((id) => row(id));

  it("a Refresh that drops the focused row moves focus to the row that followed it", async () => {
    await draw({ v: view(...five()), loadKey: 1, filter: UNTAGGED });
    await act(() => selection.act({ type: "click", id: "c", meta: false, shift: false }));
    await draw({ v: view(row("a"), row("b"), row("c", ["work"]), row("d"), row("e")), loadKey: 2 });
    expect(order.ids).toEqual(["a", "b", "d", "e"]);
    expect(selection.sel).toMatchObject({ focus: "d", anchor: "d" }); // not back to the top
  });
  it("says which order the selection was last fitted to, and why, for the list to scroll", async () => {
    await draw({ v: view(...five()), loadKey: 1, filter: UNTAGGED });
    expect(selection.fitted).toEqual({ key: order.key, reason: "load" });
    await draw({ filter: ALL });
    expect(selection.fitted).toEqual({ key: order.key, reason: "filter" });
    await draw({ v: view(row("a", ["work"]), row("b"), row("c"), row("d"), row("e")), loadKey: 2, filter: UNTAGGED });
    expect(selection.fitted.reason).toBe("load");
    const before = selection.sel;
    await draw({ revealed: { key: 1, ids: ["a"] } });
    expect(selection.fitted).toEqual({ key: order.key, reason: "reveal" }); // the rows moved
    expect(selection.sel).toBe(before);
  });
  it("says the same thing again as a new announcement, and Cmd-A says how many", async () => {
    const four = view(row("a", ["talks"]), row("b"), row("c", ["talks"]), row("d"));
    const click = (id: string, meta = false) => act(() => selection.act({ type: "click", id, meta, shift: false }));
    await draw({ v: four, loadKey: 1 });
    await click("b");
    await click("d", true);
    await draw({ filter: { kind: "tag", tag: "talks" } }); // b and d hidden
    const first = selection.announce;
    expect(first.text).toBe("2 hidden rows deselected");
    await click("a");
    await click("c", true);
    await draw({ filter: UNTAGGED }); // a and c hidden: the very same words
    expect(selection.announce.text).toBe("2 hidden rows deselected");
    expect(selection.announce.key).toBeGreaterThan(first.key); // so it is read out again
    await act(() => selection.act({ type: "all" }));
    expect(selection.announce.text).toBe("2 selected");
  });
});
