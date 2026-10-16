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
    await draw({ revealed: { key: 1, ids: ["c"] } });
    expect(order).toMatchObject({ ids: ["d", "c", "b"], reason: "reveal" });
  });

  it("puts back nothing for an Undo heard before the page opened", async () => {
    // why: the grid mounts when the Tags page opens, long after an Undo in the Library; that
    // Undo's rows are no part of this page's order.
    await draw({ v: four, revealed: { key: 3, ids: ["a"] } });
    expect(order.ids).toEqual(["b", "d"]);
    await draw({ revealed: { key: 4, ids: ["c"] } });
    expect(order.ids).toEqual(["b", "c", "d"]);
  });
});
