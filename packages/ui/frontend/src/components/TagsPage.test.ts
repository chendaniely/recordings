import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { useGridState, type GridState } from "../lib/useGridState";
import { TaggingContext, useTaggingContext, type Tagging } from "../tagging/useTagging";
import type { LibraryRow, LibraryView, TagCount, TagRequest } from "../types";

const toasts = vi.hoisted(() => ({ showCannotTag: vi.fn(), showSelectFirst: vi.fn() }));
vi.mock("../tagging/toasts", () => toasts);
vi.mock("../sr", () => ({})); // useTagging's Shiny side: the grid only reads the context

import { GRID_HINT, SELECT_ROWS, TagsPage } from "./TagsPage";

const row = (id: string, day: number, over: Partial<LibraryRow> = {}): LibraryRow => ({
  id, title: `Week ${id}`, recorded_at: `2026-10-${String(day).padStart(2, "0")}T14:00:00-07:00`, when: `Oct ${day}`,
  duration: null, duration_ms: null, kind: "audio", sources: [], tags: [], private: false, untagged: true, ...over,
});
const isPrivateTag = (t: string) => t === "private" || t.toLowerCase().startsWith("private/");
const tagged = (r: LibraryRow, ...tags: string[]): LibraryRow => ({
  ...r, tags: tags.map((tag) => ({ tag, by: "you" as const })), untagged: tags.length === 0, private: tags.some(isPrivateTag),
});
const count = (tag: string, n: number): TagCount => ({ tag, count: n, private: isPrivateTag(tag) });
const view = (recordings: LibraryRow[], tags: TagCount[]): LibraryView => ({
  recordings, counts: { all: recordings.length, untagged: recordings.filter((r) => r.untagged).length },
  tags, note_types: [], problems: [], can_tag: true, cannot_tag_reason: null, saves: true,
});

/** The fields of Tagging the grid reads; the rest are never touched here. */
const fake = (v: LibraryView, over: Partial<Tagging> = {}): Tagging => {
  const flags = new Set(v.tags.filter((t) => t.private).map((t) => t.tag));
  return {
    view: v, loadKey: 1, marks: {}, pending: [], confirm: null, revealed: { key: 0, ids: [] }, touched: 0,
    lastTagged: null, lastUndone: null, sessionTags: [], drag: { current: null }, canTag: true, opening: false, reason: null,
    saves: true, isPrivate: (t) => flags.has(t), tag: vi.fn(), undo: vi.fn(), undoLast: vi.fn(), refresh: vi.fn(),
    answerConfirm: vi.fn(), picker: null, openPicker: vi.fn(), closePicker: vi.fn(), snippet: () => undefined,
    wantSnippets: vi.fn(), ...over,
  };
};

const settle = async () => {
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
};
const frame = () => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)));

// jsdom lays nothing out: the virtualiser draws rows only for a scroll element with a size.
const sizes = ["offsetHeight", "offsetWidth"] as const;
const saved = sizes.map((k) => Object.getOwnPropertyDescriptor(HTMLElement.prototype, k));
beforeAll(() => {
  for (const k of sizes) Object.defineProperty(HTMLElement.prototype, k, { configurable: true, get: () => 800 });
});
afterAll(() => sizes.forEach((k, i) => Object.defineProperty(HTMLElement.prototype, k, saved[i]!)));

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let grid: GridState;
beforeEach(() => {
  toasts.showCannotTag.mockClear();
  try {
    window.localStorage.clear();
  } catch {
    /* none in this environment */
  }
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(() => {
  root.unmount();
  host.remove();
});

/** As App: the grid's state lives above the page, which comes and goes with the route. */
function Harness({ shown }: { shown: boolean }) {
  grid = useGridState(useTaggingContext());
  return shown ? createElement(TagsPage, { grid }) : null;
}
async function draw(tagging: Tagging, shown = true) {
  flushSync(() => root.render(createElement(TaggingContext.Provider, { value: tagging }, createElement(Harness, { shown }))));
  await settle();
}
const $ = (css: string) => host.querySelector<HTMLElement>(css);
const $$ = (css: string) => [...host.querySelectorAll<HTMLElement>(css)];
const gridRow = (id: string) => $(`[data-testid=grid-row][data-id="${id}"]`)!;
const box = (id: string, tag: string) => gridRow(id).querySelector<HTMLElement>(`[data-testid=grid-cell][data-tag="${tag}"]`)!;
const pin = (id: string) => gridRow(id).querySelector<HTMLElement>("[role=rowheader]")!;
const header = (tag: string) => $(`[data-testid=grid-header][data-tag="${tag}"]`)!;
const headers = () => $$("[data-testid=grid-header]").map((h) => h.dataset.tag);
const key = async (el: Element, k: string, init: KeyboardEventInit = {}) => {
  const e = new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true, ...init });
  flushSync(() => el.dispatchEvent(e));
  await settle();
  return e;
};
const click = async (el: Element, init: MouseEventInit = {}) => {
  flushSync(() => el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, ...init })));
  await settle();
};
const focus = async (el: HTMLElement) => {
  flushSync(() => el.focus());
  await settle();
};
/** A key where focus is, then a frame, as typing gives: focus moves to a cell once drawn. */
const press = async (k: string, init: KeyboardEventInit = {}) => {
  const e = await key(document.activeElement!, k, init);
  await frame();
  return e;
};

// a and b untagged; c tagged talks. Newest first: a, b, c.
const three = () => view([row("a", 6), row("b", 5), tagged(row("c", 4), "talks")], [count("talks", 1)]);

describe("the grid's roles (spec §6, §7; WAI-ARIA grid pattern)", () => {
  it("counts its rows and columns, and marks each header, row header and checkbox cell", async () => {
    await draw(fake(three()));
    const g = $("[role=grid]")!;
    expect(g.dataset.testid).toBe("tags-grid");
    expect(g.getAttribute("aria-multiselectable")).toBe("true");
    // Untagged only by default: a and b, plus the header row. Columns: the pinned cell, private, talks.
    expect(g.getAttribute("aria-rowcount")).toBe("3");
    expect(g.getAttribute("aria-colcount")).toBe("3");
    expect($("[role=columnheader][aria-colindex='1']")!.getAttribute("aria-sort")).toBe("descending"); // Newest
    expect($$("[data-testid=grid-header]").map((h) => [h.dataset.tag, h.getAttribute("aria-colindex"), h.getAttribute("aria-label")]))
      .toEqual([["private", "2", "private"], ["talks", "3", "talks"]]);
    expect($$("[data-testid=grid-row]").map((r) => [r.dataset.id, r.getAttribute("aria-rowindex")])).toEqual([["a", "2"], ["b", "3"]]);
    expect(pin("a").getAttribute("aria-colindex")).toBe("1");
    expect(pin("a").tabIndex).toBe(0); // the grid's one tab stop
    const cell = box("a", "talks");
    expect(cell.getAttribute("role")).toBe("checkbox");
    expect(cell.getAttribute("aria-checked")).toBe("false");
    expect(cell.getAttribute("aria-label")).toBe("talks");
    expect(cell.tabIndex).toBe(-1);
    expect(cell.closest("[role=gridcell]")!.getAttribute("aria-colindex")).toBe("3");
    expect($$("[role=grid] [tabindex='0']")).toHaveLength(1);
    expect($$("[role=columnheader] button").every((b) => b.tabIndex === -1)).toBe(true);
    expect($("[data-testid=left-count]")!.textContent).toBe("2 left");
  });

  it("names a row and its row header by the title and state, never by every column's tag", async () => {
    // why: fix round 1, item 11. The row's name was all its checkboxes' tags.
    await draw(fake(three(), { snippet: () => "Okay, Houston", marks: { a: { done: true } } }));
    const names = (el: HTMLElement) =>
      el.getAttribute("aria-labelledby")!.split(" ").map((id) => document.getElementById(id)!.textContent).join(" | ");
    expect(names(pin("a"))).toBe("Week a | Oct 6 | ✓ Tagged");
    expect(names(gridRow("a"))).toBe("Week a | Oct 6 | ✓ Tagged");
    expect(document.getElementById(pin("a").getAttribute("aria-describedby")!)!.textContent).toBe("Okay, Houston");
  });

  it("describes a row header by its snippet only once the snippet is in", async () => {
    // why: fix round 1, item 14. The loading "…" was read out as the description.
    await draw(fake(three()));
    expect(gridRow("a").querySelector(".tg-snippet")!.textContent).toBe("…");
    expect(pin("a").hasAttribute("aria-describedby")).toBe(false);
  });

  it("names each of the first ten columns' checkboxes with its digit (aria-keyshortcuts)", async () => {
    // why: fix round 1, item 8. The header's digit is aria-hidden.
    await draw(fake(three()));
    expect(box("a", "private").getAttribute("aria-keyshortcuts")).toBe("1");
    expect(box("a", "talks").getAttribute("aria-keyshortcuts")).toBe("2");
  });
});

describe("snippets (spec §5, §7; Task 7 carry)", () => {
  it("are literal text, never markup", async () => {
    const tagging = fake(three(), { snippet: (id) => (id === "a" ? '<img src=x onerror="alert(1)">' : null) });
    await draw(tagging);
    expect(host.querySelector("img")).toBeNull();
    expect(gridRow("a").querySelector(".tg-snippet")!.textContent).toBe('<img src=x onerror="alert(1)">');
    expect(gridRow("b").querySelector(".tg-snippet")!.textContent).toBe("No transcript yet · length unknown · no source");
    expect(tagging.wantSnippets).toHaveBeenLastCalledWith(["a", "b"]);
  });

  it("show none for a row made private since, and ask for none", async () => {
    const v = three();
    const tagging = fake(v, { snippet: () => "first words" });
    await draw(tagging);
    vi.mocked(tagging.wantSnippets).mockClear();
    // A reply's merge: b is private now, and stays put until Refresh.
    const next = view([v.recordings[0], tagged(v.recordings[1], "private"), v.recordings[2]], [count("private", 1), count("talks", 1)]);
    await draw({ ...tagging, view: next });
    expect(gridRow("b").querySelector(".tg-snippet")).toBeNull();
    expect(gridRow("a").querySelector(".tg-snippet")!.textContent).toBe("first words");
    expect(tagging.wantSnippets).toHaveBeenLastCalledWith(["a"]);
  });

  it("show none without Untagged only", async () => {
    await draw(fake(three(), { snippet: () => "first words" }));
    await click($("[data-testid=untagged-only]")!);
    expect($$("[data-testid=grid-row]")).toHaveLength(3);
    expect($(".tg-snippet")).toBeNull();
  });
});

describe("cells and keys (spec §7)", () => {
  it("a click anywhere in a cell toggles its tag; a cell with a change on its way waits", async () => {
    const tagging = fake(three());
    await draw(tagging);
    await click(box("a", "talks").closest("[role=gridcell]")!);
    expect(tagging.tag).toHaveBeenLastCalledWith("add", ["a"], ["talks"], { kind: "cell" });
    expect(document.activeElement).toBe(box("a", "talks")); // focus goes to the cell's checkbox
    vi.mocked(tagging.tag).mockClear();
    const pending: TagRequest[] = [{ request_id: "r", action: "add", ids: ["a"], tags: ["talks"], confirm_ids: [] }];
    await draw({ ...tagging, pending });
    expect(box("a", "talks").getAttribute("aria-checked")).toBe("true");
    expect(box("a", "talks").classList.contains("pending")).toBe(true);
    await click(box("a", "talks"));
    expect(tagging.tag).not.toHaveBeenCalled();
  });

  it("Space and Enter toggle the focused tag cell; Enter on the pinned cell opens the recording in a new tab", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const tagging = fake(three());
    await draw(tagging);
    await focus(box("b", "talks"));
    await key(box("b", "talks"), " ");
    expect(tagging.tag).toHaveBeenLastCalledWith("add", ["b"], ["talks"], { kind: "cell" });
    await focus(box("b", "private"));
    await key(box("b", "private"), "Enter");
    expect(tagging.tag).toHaveBeenLastCalledWith("add", ["b"], ["private"], { kind: "cell" });
    vi.mocked(tagging.tag).mockClear();
    await focus(pin("a"));
    await key(pin("a"), "Enter");
    expect(open).toHaveBeenCalledWith(expect.stringMatching(/#\/library\/a$/), "_blank", "noopener");
    expect(tagging.tag).not.toHaveBeenCalled();
    open.mockRestore();
  });

  it("ignores a held key's repeats: Space, Enter and the digits act once", async () => {
    // why: fix round 1, item 9. Holding Space re-toggled the cell; holding a digit queued adds.
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const tagging = fake(three());
    await draw(tagging);
    await focus(box("b", "talks"));
    for (const k of [" ", "Enter", "1"]) await key(box("b", "talks"), k, { repeat: true });
    await focus(pin("b"));
    await key(pin("b"), "Enter", { repeat: true });
    expect(tagging.tag).not.toHaveBeenCalled();
    expect(open).not.toHaveBeenCalled();
    open.mockRestore();
  });

  it("digits toggle the focused row's columns, or add to a selection of two or more", async () => {
    const tagging = fake(three());
    await draw(tagging);
    await focus(pin("b"));
    await key(pin("b"), "1"); // the first column: the pinned private
    expect(tagging.tag).toHaveBeenLastCalledWith("add", ["b"], ["private"], { kind: "cell" });
    await click(gridRow("a").querySelector(".tg-title")!);
    await click(gridRow("b").querySelector(".tg-title")!, { ctrlKey: true });
    expect($$("[data-testid=grid-row][aria-selected=true]")).toHaveLength(2);
    await key(pin("b"), "2");
    expect(tagging.tag).toHaveBeenLastCalledWith("add", ["a", "b"], ["talks"], { kind: "digit" });
    expect($$("[data-testid=grid-header] kbd").map((k) => k.textContent)).toEqual(["1", "2"]);
  });

  it("0 is the tenth column", async () => {
    // why: fix round 1, item 8.
    const tags = Array.from({ length: 10 }, (_, i) => count(`t${i}`, 10 - i));
    const tagging = fake(view([row("a", 6)], tags));
    await draw(tagging);
    expect(headers()[9]).toBe("t8"); // private, then t0…t8
    await focus(pin("a"));
    await key(pin("a"), "0");
    expect(tagging.tag).toHaveBeenLastCalledWith("add", ["a"], ["t8"], { kind: "cell" });
    expect(box("a", "t8").getAttribute("aria-keyshortcuts")).toBe("0");
    expect(box("a", "t9").hasAttribute("aria-keyshortcuts")).toBe(false);
  });

  it("arrows move between cells, Up from the first row reaches the headers, and Down comes back", async () => {
    await draw(fake(three()));
    await focus(pin("a"));
    await press("ArrowRight");
    expect(document.activeElement).toBe(box("a", "private"));
    await press("ArrowRight");
    expect(document.activeElement).toBe(box("a", "talks"));
    await press("ArrowDown");
    expect(document.activeElement).toBe(box("b", "talks"));
    expect(gridRow("b").getAttribute("aria-selected")).toBe("true"); // Down selects as it moves
    await press("ArrowUp");
    expect(document.activeElement).toBe(box("a", "talks"));
    await press("ArrowUp"); // from the first row: the column's header
    const add = header("talks").querySelector<HTMLElement>(".tg-add")!;
    expect(document.activeElement).toBe(add);
    await press("ArrowLeft"); // across the header's controls
    expect(document.activeElement).toBe(header("private").querySelector(".tg-menu"));
    await press("ArrowDown"); // back to the focused row, in that column
    expect(document.activeElement).toBe(box("a", "private"));
    await press("Home");
    expect(document.activeElement).toBe(pin("a"));
  });

  it("lets Cmd/Ctrl and Shift with Left and Right through, for the browser", async () => {
    // why: fix round 1, item 15. Cmd+Left is history Back on macOS.
    await draw(fake(three()));
    await focus(box("a", "private"));
    for (const init of [{ metaKey: true }, { ctrlKey: true }, { shiftKey: true }]) {
      for (const k of ["ArrowLeft", "ArrowRight"]) {
        const e = await press(k, init);
        expect(e.defaultPrevented, `${k} ${JSON.stringify(init)}`).toBe(false);
        expect(document.activeElement).toBe(box("a", "private"));
      }
    }
  });

  it("Cmd/Ctrl+Home moves focus only, and Up from the first row reaches a header that adds to the whole selection", async () => {
    // why: fix round 1, item 5 (spec §6: Cmd/Ctrl moves focus only). Ctrl+Home was a plain move:
    // it replaced the selection, so a header's add went to one row.
    const tagging = fake(view([row("a", 6), row("b", 5), row("c", 4)], [count("talks", 1)]));
    await draw(tagging);
    await click(gridRow("b").querySelector(".tg-title")!);
    await click(gridRow("c").querySelector(".tg-title")!, { shiftKey: true });
    await focus(box("c", "talks"));
    await press("Home", { ctrlKey: true });
    expect(document.activeElement).toBe(box("a", "talks"));
    expect($$("[data-testid=grid-row][aria-selected=true]").map((r) => r.dataset.id)).toEqual(["b", "c"]);
    await press("ArrowUp");
    const add = header("talks").querySelector<HTMLElement>(".tg-add")!;
    expect(document.activeElement).toBe(add);
    await click(add);
    expect(tagging.tag).toHaveBeenLastCalledWith("add", ["b", "c"], ["talks"], { kind: "column" });
    await focus(box("a", "talks"));
    await press("End", { metaKey: true });
    expect(document.activeElement).toBe(box("c", "talks"));
    expect($$("[data-testid=grid-row][aria-selected=true]").map((r) => r.dataset.id)).toEqual(["b", "c"]);
  });
});

describe("keys typed faster than a frame (Task 14)", () => {
  it("each moves on from where the last one went, before focus has followed", async () => {
    // why: focus moves to a cell a frame after the key that chose it; read from the key's target,
    // a quick second Right moved from the old cell, so Right Right landed one column short.
    const tagging = fake(three());
    await draw(tagging);
    await focus(pin("a"));
    await key(pin("a"), "ArrowRight");
    await key(pin("a"), "ArrowRight"); // still on the pinned cell: no frame yet
    await key(pin("a"), " ");
    expect(tagging.tag).toHaveBeenLastCalledWith("add", ["a"], ["talks"], { kind: "cell" });
    await frame();
    expect(document.activeElement).toBe(box("a", "talks"));
  });
});

describe("headers (spec §7)", () => {
  it("add their tag to the selection; with nothing selected, they say so out loud, keys included", async () => {
    const tagging = fake(three());
    await draw(tagging);
    const add = header("talks").querySelector<HTMLElement>(".tg-add")!;
    await click(add);
    expect(tagging.tag).not.toHaveBeenCalled();
    await frame();
    expect(SELECT_ROWS).toBe("Select rows first: X or Shift+arrows, or click a title.");
    expect($("[role=status]")!.textContent).toBe(SELECT_ROWS);
    expect($(".grid-hint")!.textContent).toBe(SELECT_ROWS);
    await click(gridRow("b").querySelector(".tg-title")!);
    await click(add);
    expect(tagging.tag).toHaveBeenLastCalledWith("add", ["b"], ["talks"], { kind: "column" });
    expect(add.getAttribute("aria-label")).toBe("Add talks to 1 selected");
  });

  it("keep Enter for their own buttons: the grid toggles no cell", async () => {
    const tagging = fake(three());
    await draw(tagging);
    await focus(pin("a"));
    const add = header("talks").querySelector<HTMLElement>(".tg-add")!;
    await focus(add);
    await key(add, "Enter");
    await key(add, " ");
    expect(tagging.tag).not.toHaveBeenCalled();
  });

  it("act once for a held Enter or Space: the add and the menus ignore the key's repeats", async () => {
    // why: fix round 2, item 1. Chromium clicks a button once per repeated Enter: a held Enter on
    // "Add … to N selected" sent the add four times.
    await draw(fake(three()));
    const add = header("talks").querySelector<HTMLElement>(".tg-add")!;
    const menu = header("talks").querySelector<HTMLElement>(".tg-menu")!;
    for (const button of [add, menu]) {
      await focus(button);
      for (const k of ["Enter", " "]) {
        const e = await key(button, k, { repeat: true });
        expect(e.defaultPrevented, `${button.className} ${k}`).toBe(true);
      }
    }
    expect(menu.getAttribute("aria-expanded")).toBe("false"); // the repeats opened no menu
    const first = await key(add, "Enter");
    expect(first.defaultPrevented).toBe(false); // the first press is the button's own
  });

  it("open their sort menus as menus (fix round 1, item 12)", async () => {
    await draw(fake(three()));
    expect($("[role=columnheader][aria-colindex='1'] button")!.getAttribute("aria-haspopup")).toBe("menu");
    expect(header("talks").querySelector(".tg-menu")!.getAttribute("aria-haspopup")).toBe("menu");
  });

  it("with no rows, are the grid's tab stop, so the sort and the menus stay reachable", async () => {
    // why: fix round 1, item 5. With Untagged only and nothing untagged, the grid had no tab stop.
    await draw(fake(view([tagged(row("c", 4), "talks")], [count("talks", 1)])));
    expect($$("[data-testid=grid-row]")).toHaveLength(0);
    const stops = $$("[role=grid] [tabindex='0']");
    expect(stops).toHaveLength(1);
    expect(stops[0].closest("[role=columnheader]")!.getAttribute("aria-colindex")).toBe("1");
  });

  it("the key hint says how to reach them", async () => {
    await draw(fake(three()));
    expect(GRID_HINT).toBe("T tag · Cmd-A all · Esc clear · Cmd-Z undo · R refresh · 1–9, 0 columns · ↑ from the top row: column menus");
    expect($("[data-testid=key-hint]")!.textContent).toBe(GRID_HINT);
  });
});

describe("columns, frozen per load (spec §7; fix round 1, item 1)", () => {
  const many = () =>
    view([row("a", 6), row("b", 5)], [count("talks", 3), count("work", 2), count("zoo", 1), count("Private/x", 1)]);

  it("work out the most-used once; this session's tags get theirs at the end of their group", async () => {
    const v = three();
    const tagging = fake(v);
    await draw(tagging);
    expect(headers()).toEqual(["private", "talks"]);
    // Another window's tag, counted by a reply: no new column until Refresh.
    const more = view(v.recordings, [count("talks", 1), count("work", 9)]);
    await draw({ ...tagging, view: more });
    expect(headers()).toEqual(["private", "talks"]);
    await draw({ ...tagging, view: more, sessionTags: ["zebra"] });
    expect(headers()).toEqual(["private", "talks", "zebra"]);
    await draw({ ...tagging, view: more, sessionTags: ["zebra"], loadKey: 2 });
    expect(headers()).toEqual(["private", "work", "talks", "zebra"]);
  });

  it("a picker column ticked, then tagged, keeps its place, and so does every other", async () => {
    // why: a tag used this session moved from the picker's place to the session's: t17 jumped
    // ahead of t15.
    const tags = Array.from({ length: 18 }, (_, i) => count(`t${String(i).padStart(2, "0")}`, 30 - i));
    const tagging = fake(view([row("a", 6)], tags));
    await draw(tagging);
    const defaults = headers();
    expect(defaults).toHaveLength(16); // private and the 15 most used, t00…t14
    flushSync(() => grid.toggleColumn("t15"));
    flushSync(() => grid.toggleColumn("t17"));
    await settle();
    const before = headers();
    expect(before).toEqual([...defaults, "t15", "t17"]);
    await draw({ ...tagging, sessionTags: ["t17"] }); // a cell click in the t17 column
    expect(headers()).toEqual(before);
    await draw({ ...tagging, sessionTags: ["t17", "zz"] }); // and a new tag: at the end of its group
    expect(headers()).toEqual([...before, "zz"]);
  });

  it("keeps a column whose last use goes, and doesn't move on a recount", async () => {
    const v = many();
    const tagging = fake(v);
    await draw(tagging);
    const before = headers();
    expect(before).toEqual(["private", "talks", "work", "zoo", "Private/x"]);
    // A recount: zoo and Private/x at 0 uses (gone from the view's tags), work now the most used.
    const recount = view(v.recordings, [count("work", 9), count("talks", 3)]);
    await draw({ ...tagging, view: recount, isPrivate: () => false });
    expect(headers()).toEqual(before);
    expect(header("Private/x").classList.contains("priv")).toBe(true); // still in the private group, locked
  });

  it("are hidden and shown again by the picker, at the end of their group", async () => {
    const tagging = fake(many());
    await draw(tagging);
    flushSync(() => grid.toggleColumn("talks"));
    await settle();
    expect(headers()).toEqual(["private", "work", "zoo", "Private/x"]);
    flushSync(() => grid.toggleColumn("talks"));
    await settle();
    expect(headers()).toEqual(["private", "work", "zoo", "talks", "Private/x"]);
    expect(window.localStorage.getItem("recordings.tagColumns")).toBe('{"talks":true}');
    flushSync(() => grid.toggleColumn("Private/x"));
    await settle();
    expect(headers()).not.toContain("Private/x");
    expect(window.localStorage.getItem("recordings.tagColumns")).toBe('{"talks":true}'); // never a private name
  });
});

describe("the grid's state survives a route switch (fix round 1, item 4)", () => {
  it("keeps the filter, sort, selection, hidden private columns and the column order", async () => {
    const v = view([row("a", 6), row("b", 5), tagged(row("c", 4), "talks")], [count("talks", 1), count("Private/x", 1)]);
    const tagging = fake(v);
    await draw(tagging);
    await click($("[data-testid=untagged-only]")!);
    flushSync(() => {
      grid.setSort({ by: "oldest" });
      grid.toggleColumn("Private/x");
    });
    await settle();
    await click(gridRow("a").querySelector(".tg-title")!);
    await click(gridRow("b").querySelector(".tg-title")!, { metaKey: true });
    const order = headers();
    const rows = $$("[data-testid=grid-row]").map((r) => r.dataset.id);
    expect(rows).toEqual(["c", "b", "a"]);
    await draw(tagging, false); // to the Library
    await draw(tagging, true); // and back
    expect(($("[data-testid=untagged-only]") as HTMLInputElement).checked).toBe(false);
    expect($("[role=columnheader][aria-colindex='1']")!.getAttribute("aria-sort")).toBe("ascending");
    expect($$("[data-testid=grid-row]").map((r) => r.dataset.id)).toEqual(rows);
    expect($$("[data-testid=grid-row][aria-selected=true]").map((r) => r.dataset.id)).toEqual(["b", "a"]);
    expect(headers()).toEqual(order);
    expect(headers()).not.toContain("Private/x");
  });
});

describe("the grid's header line (spec §6, §7)", () => {
  it("says once why tagging is off, or that the archive is being opened", async () => {
    await draw(fake(three(), { canTag: false, reason: "/Volumes/Mirror/archive is read-only" }));
    expect($("[data-testid=cannot-tag]")!.textContent).toBe("Tagging is off: /Volumes/Mirror/archive is read-only");
    expect(box("a", "talks").getAttribute("aria-disabled")).toBe("true");
    await draw(fake(three(), { canTag: false, opening: true }));
    expect($("[data-testid=cannot-tag]")!.textContent).toBe("Opening the archive…");
  });

  it("shows Refresh with how many rows were tagged", async () => {
    await draw(fake(three(), { touched: 5 }));
    expect($("[data-testid=grid-refresh]")!.textContent).toBe("5 tagged · Refresh");
  });
});
