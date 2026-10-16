import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { LibraryRow, LibraryView } from "../types";
import type { RowSort } from "./grid";
import { useGridOrder, type GridSettings, type RowOrder } from "./useRowOrder";

const row = (id: string, day: number, tags: string[] = [], title = `Week ${id}`): LibraryRow => ({
  id, title, recorded_at: `2026-10-${String(day).padStart(2, "0")}T14:00:00-07:00`, when: "", duration: null,
  duration_ms: null, kind: "audio", sources: [], tags: tags.map((tag) => ({ tag, by: "you" as const })), private: false,
  untagged: tags.length === 0,
});
const view = (...recordings: LibraryRow[]): LibraryView => ({
  recordings, counts: { all: recordings.length, untagged: recordings.filter((r) => r.untagged).length },
  tags: [], note_types: [], problems: [], can_tag: true, cannot_tag_reason: null, saves: true,
});
const NEWEST: RowSort = { by: "newest" };

// As useSelection.test.ts: the hooks' effects render in later Scheduler tasks.
const settle = async () => {
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
};

let order: RowOrder;
type Props = { v: LibraryView; settings: GridSettings; loadKey: number; revealed: { key: number; ids: string[] } };
function Probe({ v, settings, loadKey, revealed }: Props) {
  order = useGridOrder(v, settings, loadKey, revealed);
  return null;
}

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let props: Props;
const draw = async (over: Partial<Props>) => {
  props = { ...props, ...over };
  flushSync(() => root.render(createElement(Probe, props)));
  await settle();
};

beforeEach(() => {
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
  props = {
    v: view(), settings: { untaggedOnly: true, title: "", sort: NEWEST }, loadKey: 1, revealed: { key: 0, ids: [] },
  };
});
afterEach(() => {
  root.unmount();
  host.remove();
});

describe("useGridOrder: the Tags grid's rows stay put (spec §6, §7)", () => {
  const four = view(row("a", 4, ["talks"]), row("b", 3), row("c", 2, ["talks"], "Apple"), row("d", 1));

  it("works out the rows on load: Untagged only, the title filter and the sort", async () => {
    await draw({ v: four });
    expect(order).toMatchObject({ ids: ["b", "d"], reason: "load" });
    await draw({ settings: { untaggedOnly: false, title: "", sort: NEWEST } });
    expect(order).toMatchObject({ ids: ["a", "b", "c", "d"], reason: "filter" });
    await draw({ settings: { untaggedOnly: false, title: "", sort: { by: "title" } } });
    expect(order.ids).toEqual(["c", "a", "b", "d"]); // Apple, then Week a, b, d
    expect(order.reason).toBe("filter"); // a new order shows from the top
    await draw({ settings: { untaggedOnly: false, title: "week", sort: { by: "oldest" } } });
    expect(order.ids).toEqual(["d", "b", "a"]);
  });

  it("keeps a row whose tags change in place until Refresh", async () => {
    await draw({ v: four });
    const key = order.key;
    await draw({ v: view(row("a", 4, ["talks"]), row("b", 3, ["work"]), row("c", 2, ["talks"]), row("d", 1)) });
    expect(order.ids).toEqual(["b", "d"]);
    expect(order.key).toBe(key); // no new order at all
    await draw({ loadKey: 2 }); // Refresh
    expect(order).toMatchObject({ ids: ["d"], reason: "load" });
  });

  it("puts an Undo's rows back at their place in the sorted order", async () => {
    await draw({ v: four, settings: { untaggedOnly: true, title: "", sort: { by: "oldest" } } });
    expect(order.ids).toEqual(["d", "b"]);
    // The Undo's reply has c untagged again by the time its rows come back.
    const undone = view(row("a", 4, ["talks"]), row("b", 3), row("c", 2, [], "Apple"), row("d", 1));
    await draw({ v: undone, revealed: { key: 1, ids: ["c"] } });
    expect(order).toMatchObject({ ids: ["d", "c", "b"], reason: "reveal" });
  });

  it("puts back only the Undo's rows that pass the grid's own filter", async () => {
    // why: fix round 2, item 2. An Undo made in the Library put its rows into the grid unfiltered:
    // a tagged recording under Untagged only, or one its title filter leaves out.
    const v = view(row("a", 6), row("b", 5, ["talks"]), row("c", 4, [], "Apple"), row("d", 3), row("e", 2));
    await draw({ v, settings: { untaggedOnly: true, title: "week", sort: NEWEST } });
    await draw({ settings: { untaggedOnly: true, title: "week", sort: NEWEST }, v: view(row("a", 6), row("b", 5, ["talks"]), row("c", 4, [], "Apple"), row("d", 3, ["work"]), row("e", 2)), loadKey: 2 });
    expect(order.ids).toEqual(["a", "e"]); // d tagged since, and gone on Refresh
    const key = order.key;
    await draw({ revealed: { key: 1, ids: ["b", "c"] } }); // tagged; and failing the title filter
    expect(order.ids).toEqual(["a", "e"]);
    expect(order.key).toBe(key); // no new order at all
    await draw({ v, revealed: { key: 2, ids: ["b", "d"] } }); // d untagged again by the Undo
    expect(order).toMatchObject({ ids: ["a", "d", "e"], reason: "reveal" });
  });

  it("makes no new order for an Undo heard before it was mounted", async () => {
    // why: an order mounted after an Undo (a view opened later) works its rows out on load; that
    // Undo's rows are already there if they pass its filter.
    await draw({ v: four, revealed: { key: 3, ids: ["a"] } });
    expect(order).toMatchObject({ ids: ["b", "d"], key: 1, reason: "load" });
    const undone = view(row("a", 4, ["talks"]), row("b", 3), row("c", 2, [], "Apple"), row("d", 1)); // c untagged again
    await draw({ v: undone, revealed: { key: 4, ids: ["c"] } });
    expect(order.ids).toEqual(["b", "c", "d"]);
  });
});
