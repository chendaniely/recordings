import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { NO_SEL, type Sel } from "../lib/selection";
import { TaggingContext, type Tagging } from "../tagging/useTagging";
import type { LibraryRow } from "../types";

const toasts = vi.hoisted(() => ({ showCannotTag: vi.fn(), showSelectFirst: vi.fn() }));
vi.mock("../tagging/toasts", () => toasts);
vi.mock("../sr", () => ({})); // useTagging's Shiny side: the list only reads the context

import { Announcer, KEY_HINT, RecordingList, dragImage, openNewTag, openPickerFor } from "./RecordingList";

const row = (id: string): LibraryRow => ({
  id, title: `Week ${id}`, recorded_at: "2026-10-06T14:00:00-07:00", when: "", duration: null, duration_ms: null,
  kind: "audio", sources: [], tags: [], private: false, untagged: true,
});
const rows = [row("a"), row("b"), row("c")];

/** The fields of Tagging the list reads; the rest are never touched here. */
const fake = (over: Partial<Tagging> = {}): Tagging => ({
  view: null, loadKey: 1, marks: {}, pending: [], confirm: null, revealed: { key: 0, ids: [] }, touched: 0,
  lastTagged: null, lastUndone: null, sessionTags: [], drag: { current: null }, canTag: true, opening: false, reason: null, saves: true,
  isPrivate: () => false, tag: vi.fn(), undo: vi.fn(), undoLast: vi.fn(), refresh: vi.fn(), answerConfirm: vi.fn(),
  picker: null, openPicker: vi.fn(), closePicker: vi.fn(), snippet: () => undefined, wantSnippets: vi.fn(),
  ...over,
});

const draw = (tagging: Tagging, list = rows) =>
  renderToStaticMarkup(
    createElement(
      TaggingContext.Provider,
      { value: tagging },
      createElement(RecordingList, {
        rows: list, sel: NO_SEL, act: () => {}, openId: null, onOpen: () => {}, announce: { text: "", key: 0 },
        fitted: { key: 0, reason: "load" },
      }),
    ),
  );

beforeEach(() => {
  toasts.showCannotTag.mockClear();
  toasts.showSelectFirst.mockClear();
});

describe("openPickerFor: T, the selection bar and a drop (spec §6)", () => {
  it("opens the picker's dialog form, titled for its targets", () => {
    const tagging = fake();
    openPickerFor(["a", "c"], rows, tagging);
    expect(tagging.openPicker).toHaveBeenCalledWith({ ids: ["a", "c"], title: "Tag 2 recordings" });
    openPickerFor(["b"], rows, tagging);
    expect(tagging.openPicker).toHaveBeenLastCalledWith({ ids: ["b"], title: "Tag Week b" });
  });
  it("does nothing with no targets", () => {
    const tagging = fake();
    openPickerFor([], rows, tagging);
    expect(tagging.openPicker).not.toHaveBeenCalled();
    expect(toasts.showCannotTag).not.toHaveBeenCalled();
  });
  it("says why when tagging is off, or still opening, instead of opening the picker", () => {
    const off = fake({ canTag: false, reason: "/Volumes/Mirror/a/very/long/path is read-only" });
    openPickerFor(["a"], rows, off);
    expect(off.openPicker).not.toHaveBeenCalled();
    expect(toasts.showCannotTag).toHaveBeenLastCalledWith({ canTag: false, opening: false, reason: off.reason });
    const opening = fake({ canTag: false, opening: true });
    openPickerFor(["a"], rows, opening);
    expect(toasts.showCannotTag).toHaveBeenLastCalledWith({ canTag: false, opening: true, reason: null });
  });
});

describe("+ New tag… (spec §6; Task 13 fix round 1)", () => {
  const order = ["a", "b", "c"];
  const sel = (selected: string[], focus: string | null = "b"): Sel => ({ selected: new Set(selected), anchor: focus, focus });
  it("opens the picker for the dragged recordings on a drop", () => {
    const tagging = fake();
    openNewTag(["c"], sel(["a", "b"]), order, rows, tagging);
    expect(tagging.openPicker).toHaveBeenCalledWith({ ids: ["c"], title: "Tag Week c" });
  });
  it("opens it for the selection on a click, in list order", () => {
    const tagging = fake();
    openNewTag(null, sel(["c", "a"]), order, rows, tagging);
    expect(tagging.openPicker).toHaveBeenCalledWith({ ids: ["a", "c"], title: "Tag 2 recordings" });
  });
  it("says so with nothing selected, never targeting the focused row the sidebar can't show", () => {
    const tagging = fake();
    openNewTag(null, sel([], "b"), order, rows, tagging);
    expect(tagging.openPicker).not.toHaveBeenCalled();
    expect(toasts.showSelectFirst).toHaveBeenCalledTimes(1);
  });
});

describe("the list's header (spec §6)", () => {
  it("is an ARIA grid counting every row in the filter and the header", () => {
    const html = draw(fake());
    expect(html).toContain('role="grid"');
    expect(html).toContain('aria-multiselectable="true"');
    expect(html).toContain('aria-rowcount="4"');
    expect(html).toContain("3 recordings");
  });
  it("shows the one-line key hint and Refresh, with how many rows were tagged", () => {
    expect(KEY_HINT).toBe("T tag · Cmd-A all · Esc clear · Cmd-Z undo · R refresh");
    expect(draw(fake())).toContain(KEY_HINT);
    expect(draw(fake())).toMatch(/data-testid="refresh"[^>]*>Refresh</);
    expect(draw(fake({ touched: 5 }))).toContain("5 tagged · Refresh");
  });
  it("says once why tagging is off, and that the archive is being opened while it is", () => {
    const off = draw(fake({ canTag: false, reason: "/Volumes/Mirror/archive is read-only" }));
    expect(off).toMatch(/data-testid="cannot-tag"[^>]*>Tagging is off: \/Volumes\/Mirror\/archive is read-only</);
    const opening = draw(fake({ canTag: false, opening: true }));
    expect(opening).toMatch(/data-testid="cannot-tag"[^>]*>Opening the archive…</);
    expect(opening).not.toContain("Tagging is off");
    expect(draw(fake())).not.toContain("cannot-tag");
  });
  it("says on the demo that changes aren't saved", () => {
    expect(draw(fake({ saves: false }))).toMatch(/Demo: changes aren(&#x27;|')t saved/);
    expect(draw(fake())).not.toContain("Demo:");
  });
});

describe("the list, fix round 1 (Task 12)", () => {
  it("is named once: the grid is Recordings, its section isn't a second Recordings", () => {
    const html = draw(fake());
    expect(html.match(/aria-label="Recordings"/g)).toHaveLength(1);
    expect(html).toMatch(/role="grid"[^>]*aria-label="Recordings"|aria-label="Recordings"[^>]*role="grid"/);
  });
  it("reads out an announcement again when the same words come round again", async () => {
    const host = document.body.appendChild(document.createElement("div"));
    const root = createRoot(host);
    const frame = () => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)));
    const say = (text: string, key: number) => flushSync(() => root.render(createElement(Announcer, { text, at: key })));
    const region = () => host.querySelector("[role=status]")!.textContent;
    say("2 hidden rows deselected", 1);
    await frame();
    expect(region()).toBe("2 hidden rows deselected");
    say("2 hidden rows deselected", 2);
    expect(region()).toBe(""); // emptied first, so the same words are a change
    await frame();
    expect(region()).toBe("2 hidden rows deselected");
    root.unmount();
    host.remove();
  });
});

describe("dragging rows (spec §6)", () => {
  // jsdom lays nothing out: the virtualiser draws rows only for a scroll element with a size.
  const sizes = ["offsetHeight", "offsetWidth"] as const;
  const saved = sizes.map((k) => Object.getOwnPropertyDescriptor(HTMLElement.prototype, k));
  /** A device that can hover, or not: rows drag only where it can (fix round 1). */
  const hover = (can: boolean) => {
    window.matchMedia = ((query: string) => ({
      matches: query === "(hover: hover)" ? can : false, media: query, addEventListener() {}, removeEventListener() {},
    })) as unknown as typeof window.matchMedia;
  };
  beforeAll(() => {
    for (const k of sizes) Object.defineProperty(HTMLElement.prototype, k, { configurable: true, get: () => 800 });
  });
  beforeEach(() => hover(true));
  afterAll(() => {
    sizes.forEach((k, i) => Object.defineProperty(HTMLElement.prototype, k, saved[i]!));
    delete (window as { matchMedia?: unknown }).matchMedia;
  });

  const tagged = rows.map((r) => (r.id === "b" ? { ...r, tags: [{ tag: "talks", by: "you" as const }], untagged: false } : r));
  type Transfer = { effectAllowed: string; setData: ReturnType<typeof vi.fn>; setDragImage: ReturnType<typeof vi.fn> };
  const transfer = (): Transfer => ({ effectAllowed: "all", setData: vi.fn(), setDragImage: vi.fn() });

  function mount(tagging: Tagging, sel: Sel, first: LibraryRow[] = tagged) {
    const host = document.body.appendChild(document.createElement("div"));
    const root = createRoot(host);
    const draw = (list: LibraryRow[] = first) =>
      flushSync(() =>
        root.render(
          createElement(
            TaggingContext.Provider,
            { value: tagging },
            createElement(RecordingList, {
              rows: list, sel, act: () => {}, openId: null, onOpen: () => {}, announce: { text: "", key: 0 },
              fitted: { key: 0, reason: "load" },
            }),
          ),
        ),
      );
    draw();
    const row = (id: string) => host.querySelector<HTMLElement>(`[role=row][data-id="${id}"]`)!;
    const grid = () => host.querySelector<HTMLElement>("[role=grid]")!;
    /** A press on `on`, then a drag from its row: dragstart's target is the draggable row. */
    const drag = (id: string, on: (row: HTMLElement) => Element = (r) => r.querySelector(".row-title")!) => {
      const from = on(row(id));
      flushSync(() => from.dispatchEvent(new Event("pointerdown", { bubbles: true })));
      const start = new Event("dragstart", { bubbles: true, cancelable: true });
      const data = transfer();
      Object.defineProperty(start, "dataTransfer", { value: data });
      flushSync(() => row(id).dispatchEvent(start));
      return { start, data };
    };
    const end = (id: string) => flushSync(() => row(id).dispatchEvent(new Event("dragend", { bubbles: true })));
    const done = () => {
      root.unmount();
      host.remove();
    };
    return { host, draw, row, grid, drag, end, done };
  }

  it("drags the selection, in list order, when the row is in it; else that row alone", () => {
    const tagging = fake();
    const list = mount(tagging, { selected: new Set(["c", "a"]), anchor: "a", focus: "a" });
    expect(list.row("c").getAttribute("draggable")).toBe("true");
    const { start, data } = list.drag("c");
    expect(start.defaultPrevented).toBe(false);
    expect(tagging.drag.current?.ids).toEqual(["a", "c"]);
    expect(data.effectAllowed).toBe("copy");
    expect(data.setData).toHaveBeenCalledWith("text/plain", "2 recordings"); // never an ID or a title
    // The drag's own marker, which a drop target looks for among the event's types (fix round 1).
    const mark = tagging.drag.current!.mark;
    expect(mark).toMatch(/^application\/x-recordings-drag-[0-9a-f]{16}$/);
    expect(data.setData).toHaveBeenCalledWith(mark, "1");
    const [image] = data.setDragImage.mock.calls[0];
    expect(image.textContent).toBe("2 recordings");
    expect(image.isConnected).toBe(true); // setDragImage draws an element in the document
    list.end("c");
    expect(tagging.drag.current).toBeNull();
    const other = list.drag("b");
    expect(tagging.drag.current?.ids).toEqual(["b"]); // the selection is left alone
    expect(tagging.drag.current?.mark).not.toBe(mark); // a new marker for each drag
    expect(other.data.setData).toHaveBeenCalledWith("text/plain", "1 recording");
    list.done();
  });

  it("cancels a drag that starts on the checkbox, a chip's × or the +", () => {
    const tagging = fake();
    const list = mount(tagging, { selected: new Set(["b"]), anchor: "b", focus: "b" });
    for (const control of [".check", "[data-control=remove]", "[data-control=add]"]) {
      const { start } = list.drag("b", (r) => r.querySelector(control)!);
      expect(start.defaultPrevented, control).toBe(true);
      expect(tagging.drag.current, control).toBeNull();
    }
    // A chip's label is the row's, as a click on it is.
    list.drag("b", (r) => r.querySelector(".chip-label")!);
    expect(tagging.drag.current?.ids).toEqual(["b"]);
    list.done();
  });

  it("draws no row draggable while tagging is off", () => {
    const list = mount(fake({ canTag: false, reason: "read-only" }), NO_SEL);
    expect(list.row("a").getAttribute("draggable")).toBe("false");
    list.done();
  });

  it("draws no row draggable on a device that can't hover: the picker is the touch path", () => {
    hover(false);
    const list = mount(fake(), NO_SEL);
    expect(list.row("a").getAttribute("draggable")).toBe("false");
    list.done();
  });

  it("keeps the dragged row drawn while the list scrolls away from it, with focus and the anchor elsewhere", async () => {
    const many = Array.from({ length: 300 }, (_, i) => row(String(i).padStart(3, "0")));
    const tagging = fake();
    const list = mount(tagging, { selected: new Set(), anchor: "000", focus: "000" }, many);
    list.drag("005");
    // To the end of the list, far past the dragged row.
    Object.defineProperty(list.grid(), "scrollTop", { configurable: true, get: () => 10_000_000 });
    list.grid().dispatchEvent(new Event("scroll"));
    await vi.waitFor(() => expect(list.row("299")).not.toBeNull());
    expect(list.row("100")).toBeNull(); // rows far from the view aren't drawn
    expect(list.row("000")).not.toBeNull(); // focus and the anchor
    expect(list.row("005")).not.toBeNull(); // the dragged row: its dragend comes to it
    list.end("005");
    await vi.waitFor(() => expect(list.row("005")).toBeNull());
    list.done();
  });

  it("keeps the first row, the tab stop, drawn while nothing is focused, however far it scrolls", async () => {
    // why: Task 14 fix round 1. With no focused row, the tab stop is the first row; scrolled out of
    // the drawn range, nothing in the list had tabindex=0 and Tab skipped the list.
    const many = Array.from({ length: 300 }, (_, i) => row(String(i).padStart(3, "0")));
    const list = mount(fake(), NO_SEL, many);
    Object.defineProperty(list.grid(), "scrollTop", { configurable: true, get: () => 10_000_000 });
    list.grid().dispatchEvent(new Event("scroll"));
    await vi.waitFor(() => expect(list.row("299")).not.toBeNull());
    expect(list.row("000")?.tabIndex).toBe(0);
    list.done();
  });

  it("lets Cmd/Ctrl and Shift with Left and Right through on a row, for the browser", () => {
    // why: Task 14 fix round 2, item 4. Cmd+Left is history Back on macOS; the list swallowed it.
    const list = mount(fake(), { selected: new Set(), anchor: "b", focus: "b" });
    list.row("b").focus();
    for (const init of [{ metaKey: true }, { ctrlKey: true }, { shiftKey: true }]) {
      for (const key of ["ArrowLeft", "ArrowRight"]) {
        const e = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init });
        flushSync(() => list.row("b").dispatchEvent(e));
        expect(e.defaultPrevented, `${key} ${JSON.stringify(init)}`).toBe(false);
        expect(document.activeElement).toBe(list.row("b"));
      }
    }
    const plain = new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true });
    flushSync(() => list.row("b").dispatchEvent(plain));
    expect(plain.defaultPrevented).toBe(true); // plain Right still moves to the row's controls
    list.done();
  });

  it("forgets a drag whose row leaves the list, since no dragend comes for it, and on leaving the Library", () => {
    const tagging = fake();
    const list = mount(tagging, NO_SEL);
    list.drag("b");
    expect(tagging.drag.current?.ids).toEqual(["b"]);
    list.draw(tagged.filter((r) => r.id !== "b")); // a Refresh or a filter change mid-drag
    expect(tagging.drag.current).toBeNull();
    list.drag("a");
    list.done(); // the Status page, mid-drag
    expect(tagging.drag.current).toBeNull();
  });
});

describe("dragImage (spec §6)", () => {
  it("is one hidden element in the document, saying how many recordings", () => {
    const one = dragImage("12 recordings");
    expect(one.isConnected).toBe(true);
    expect(one.getAttribute("aria-hidden")).toBe("true");
    expect(one.textContent).toBe("12 recordings");
    expect(dragImage("1 recording")).toBe(one);
    expect(one.textContent).toBe("1 recording");
  });
});
