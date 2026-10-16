import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { TaggingContext, type Tagging } from "../tagging/useTagging";
import type { LibraryRow, LibraryView, TagCount, TagRequest } from "../types";

const toasts = vi.hoisted(() => ({ showCannotTag: vi.fn(), showSelectFirst: vi.fn() }));
vi.mock("../tagging/toasts", () => toasts);
vi.mock("../sr", () => ({})); // useTagging's Shiny side: the grid only reads the context

import { TagsPage } from "./TagsPage";

const row = (id: string, day: number, over: Partial<LibraryRow> = {}): LibraryRow => ({
  id, title: `Week ${id}`, recorded_at: `2026-10-${String(day).padStart(2, "0")}T14:00:00-07:00`, when: `Oct ${day}`,
  duration: null, duration_ms: null, kind: "audio", sources: [], tags: [], private: false, untagged: true, ...over,
});
const tagged = (r: LibraryRow, ...tags: string[]): LibraryRow => ({
  ...r, tags: tags.map((tag) => ({ tag, by: "you" as const })), untagged: tags.length === 0,
  private: tags.some((t) => t === "private" || t.startsWith("private/")),
});
const count = (tag: string, n: number): TagCount => ({ tag, count: n, private: tag === "private" || tag.startsWith("private/") });
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
beforeEach(() => {
  toasts.showCannotTag.mockClear();
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(() => {
  root.unmount();
  host.remove();
});

async function draw(tagging: Tagging) {
  flushSync(() => root.render(createElement(TaggingContext.Provider, { value: tagging }, createElement(TagsPage))));
  await settle();
}
const $ = <T extends Element = HTMLElement>(css: string) => host.querySelector<T & HTMLElement>(css);
const $$ = (css: string) => [...host.querySelectorAll<HTMLElement>(css)];
const gridRow = (id: string) => $(`[data-testid=grid-row][data-id="${id}"]`)!;
const box = (id: string, tag: string) => gridRow(id).querySelector<HTMLElement>(`[data-testid=grid-cell][data-tag="${tag}"]`)!;
const pin = (id: string) => gridRow(id).querySelector<HTMLElement>("[role=rowheader]")!;
const header = (tag: string) => $(`[data-testid=grid-header][data-tag="${tag}"]`)!;
const key = async (el: Element, k: string, init: KeyboardEventInit = {}) => {
  flushSync(() => el.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true, ...init })));
  await settle();
};
const click = async (el: Element, init: MouseEventInit = {}) => {
  flushSync(() => el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, ...init })));
  await settle();
};
const focus = async (el: HTMLElement) => {
  flushSync(() => el.focus());
  await settle();
};

// a and b untagged; c tagged talks. Newest first: a, b, c.
const three = () => view([row("a", 6), row("b", 5), tagged(row("c", 4), "talks")], [count("talks", 1)]);

describe("the grid's roles (spec §6, §7; WAI-ARIA grid pattern)", () => {
  it("counts its rows and columns, and marks each header, row header and checkbox cell", async () => {
    await draw(fake(three()));
    const grid = $("[role=grid]")!;
    expect(grid.dataset.testid).toBe("tags-grid");
    expect(grid.getAttribute("aria-multiselectable")).toBe("true");
    // Untagged only by default: a and b, plus the header row. Columns: the pinned cell, private, talks.
    expect(grid.getAttribute("aria-rowcount")).toBe("3");
    expect(grid.getAttribute("aria-colcount")).toBe("3");
    const first = $("[role=columnheader][aria-colindex='1']")!;
    expect(first.getAttribute("aria-sort")).toBe("descending"); // Newest
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
    // One tab stop: every header control and cell other than the focused one is out of the tab order.
    expect($$("[role=grid] [tabindex='0']")).toHaveLength(1);
    expect($$("[role=columnheader] button").every((b) => b.tabIndex === -1)).toBe(true);
    expect($("[data-testid=left-count]")!.textContent).toBe("2 left");
  });

  it("names a row header by its title and state, not by its snippet or Open", async () => {
    await draw(fake(three(), { snippet: () => "Okay, Houston", marks: { a: { done: true } } }));
    const names = pin("a").getAttribute("aria-labelledby")!.split(" ").map((id) => document.getElementById(id)!.textContent);
    expect(names.join(" | ")).toBe("Week a | Oct 6 | ✓ Tagged");
    expect(document.getElementById(pin("a").getAttribute("aria-describedby")!)!.textContent).toBe("Okay, Houston");
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

  it("arrows move between cells, Up from the first row reaches the headers, and Down comes back", async () => {
    await draw(fake(three()));
    await focus(pin("a"));
    // Each key lands where the last one put focus, a frame later.
    const press = async (k: string) => {
      await key(document.activeElement!, k);
      await frame();
    };
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
    const add = header("talks").querySelector<HTMLElement>("button")!;
    expect(document.activeElement).toBe(add);
    await press("ArrowLeft"); // across the header's controls
    expect(document.activeElement).toBe(header("private").querySelector(`[aria-label="Sort by private"]`));
    await press("ArrowDown"); // back to the focused row, in that column
    expect(document.activeElement).toBe(box("a", "private"));
    await press("Home");
    expect(document.activeElement).toBe(pin("a"));
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
  it("add their tag to the selection; with nothing selected, they say so out loud", async () => {
    const tagging = fake(three());
    await draw(tagging);
    const add = header("talks").querySelector<HTMLElement>("button")!;
    await click(add);
    expect(tagging.tag).not.toHaveBeenCalled();
    await frame();
    expect($("[role=status]")!.textContent).toMatch(/^Select rows first/);
    expect($(".grid-hint")!.textContent).toMatch(/^Select rows first/);
    await click(gridRow("b").querySelector(".tg-title")!);
    await click(add);
    expect(tagging.tag).toHaveBeenLastCalledWith("add", ["b"], ["talks"], { kind: "column" });
    expect(add.getAttribute("aria-label")).toBe("Add talks to 1 selected");
  });

  it("keep Enter for their own buttons: the grid toggles no cell", async () => {
    const tagging = fake(three());
    await draw(tagging);
    await focus(pin("a"));
    const add = header("talks").querySelector<HTMLElement>("button")!;
    await focus(add);
    await key(add, "Enter");
    await key(add, " ");
    expect(tagging.tag).not.toHaveBeenCalled();
  });
});

describe("columns (spec §7)", () => {
  it("are fixed per load; this session's tags get theirs at once", async () => {
    const v = three();
    const tagging = fake(v);
    await draw(tagging);
    const tags = () => $$("[data-testid=grid-header]").map((h) => h.dataset.tag);
    expect(tags()).toEqual(["private", "talks"]);
    // Another window's tag, counted by a reply: no new column until Refresh.
    const more = view(v.recordings, [count("talks", 1), count("work", 9)]);
    await draw({ ...tagging, view: more });
    expect(tags()).toEqual(["private", "talks"]);
    await draw({ ...tagging, view: more, sessionTags: ["zebra"] });
    expect(tags()).toEqual(["private", "talks", "zebra"]);
    await draw({ ...tagging, view: more, sessionTags: ["zebra"], loadKey: 2 });
    expect(tags()).toEqual(["private", "work", "talks", "zebra"]);
  });

  it("keep a private column in its group when its last use goes", async () => {
    const v = view([row("a", 6), tagged(row("b", 5), "private/x")], [count("private/x", 1)]);
    const tagging = fake(v);
    await draw(tagging);
    await click($("[data-testid=untagged-only]")!);
    const tags = () => $$("[data-testid=grid-header]").map((h) => [h.dataset.tag, h.classList.contains("priv")]);
    expect(tags()).toEqual([["private", true], ["private/x", true]]);
    const gone = view([row("a", 6), row("b", 5)], []);
    await draw({ ...tagging, view: gone, isPrivate: () => false });
    expect(tags()).toEqual([["private", true], ["private/x", true]]);
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

  it("shows Refresh with how many rows were tagged, and the key hint with the digits", async () => {
    await draw(fake(three(), { touched: 5 }));
    expect($("[data-testid=grid-refresh]")!.textContent).toBe("5 tagged · Refresh");
    expect($("[data-testid=key-hint]")!.textContent).toBe("T tag · Cmd-A all · Esc clear · Cmd-Z undo · R refresh · 1–9, 0 columns");
  });
});
