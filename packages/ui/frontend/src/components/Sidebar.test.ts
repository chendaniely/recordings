import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Filter, LibraryView } from "../types";

const tagging = vi.hoisted(() => ({
  canTag: true, drag: { current: null as string[] | null }, tag: (() => {}) as (...args: unknown[]) => void,
}));
vi.mock("../tagging/useTagging", () => ({ useTaggingContext: () => tagging }));

import { Sidebar } from "./Sidebar";

const library = {
  recordings: [], counts: { all: 2, untagged: 1 }, problems: [], can_tag: true, cannot_tag_reason: null, saves: true,
  tags: [{ tag: "talks", count: 1, private: false }, { tag: "private", count: 1, private: true }],
  note_types: [{ note_type: "lecture", count: 1 }],
} as LibraryView;
const render = (filter: Parameters<typeof Sidebar>[0]["filter"], canTag = true, view = library) => {
  tagging.canTag = canTag;
  return renderToStaticMarkup(createElement(Sidebar, { library: view, filter, onFilter: () => {}, onNewTag: () => {} }));
};

describe("the sidebar", () => {
  it("keeps the active tag filter's item when its tag has no recording left (Review Focus 4)", () => {
    const html = render({ kind: "tag", tag: "gone" });
    expect(html).toContain("gone");
    expect(html).toMatch(/class="si on"[^>]*>.*gone.*<span class="n">0<\/span>/s);
    expect(render({ kind: "all" })).not.toContain("gone");
  });
  it("offers + New tag… only when tagging works (spec §6)", () => {
    expect(render({ kind: "all" })).toContain('data-testid="new-tag"');
    expect(render({ kind: "all" }, false)).not.toContain('data-testid="new-tag"');
  });
  it("hides a note type at 0, unless it is the active filter (WM6 refinement)", () => {
    const view = { ...library, note_types: [{ note_type: "lecture", count: 1 }, { note_type: "summary", count: 0 }] };
    expect(render({ kind: "all" }, true, view)).not.toContain("summary");
    const active = render({ kind: "tag", tag: "notes/summary" }, true, view);
    expect(active).toMatch(/class="si on"[^>]*><span>summary<\/span><span class="n">0<\/span>/);
    // A note type gone from the view altogether is kept in its own group, by its name.
    const gone = render({ kind: "tag", tag: "notes/meeting" }, true, view);
    expect(gone).toMatch(/Note types<\/div>.*class="si on"[^>]*><span>meeting<\/span><span class="n">0<\/span>/s);
  });
});

/** Drag events as a browser sends them; jsdom has no DragEvent, and React reads dataTransfer
 * from the native event. */
function dragEvent(type: string, data: Record<string, string> = {}): Event {
  const event = new Event(type, { bubbles: true, cancelable: true });
  const transfer = { dropEffect: "none", getData: (t: string) => data[t] ?? "", types: Object.keys(data) };
  Object.defineProperty(event, "dataTransfer", { value: transfer });
  return event;
}

describe("dropping on the sidebar (spec §6)", () => {
  let host: HTMLDivElement;
  let root: Root;
  const onNewTag = vi.fn();
  const draw = (view: LibraryView, filter: Filter = { kind: "all" }) =>
    flushSync(() => root.render(createElement(Sidebar, { library: view, filter, onFilter: () => {}, onNewTag })));
  const item = (text: string) =>
    [...host.querySelectorAll<HTMLElement>("[data-testid=filter-tag]")].find((el) => el.textContent?.includes(text))!;
  const newTag = () => host.querySelector<HTMLElement>("[data-testid=new-tag]")!;

  beforeEach(() => {
    tagging.canTag = true;
    tagging.drag.current = null;
    tagging.tag = vi.fn();
    onNewTag.mockClear();
    host = document.body.appendChild(document.createElement("div"));
    root = createRoot(host);
    draw(library);
  });
  afterEach(() => {
    root.unmount();
    host.remove();
  });

  it("ignores a drag that didn't start in this page: files, text, another window's", () => {
    for (const target of [item("talks"), newTag()]) {
      const over = dragEvent("dragover", { "text/plain": "a" });
      flushSync(() => target.dispatchEvent(over));
      expect(over.defaultPrevented).toBe(false); // so the browser refuses the drop
      expect(target.className).not.toContain("drop");
      const drop = dragEvent("drop", { "text/plain": "a" });
      flushSync(() => target.dispatchEvent(drop));
      expect(drop.defaultPrevented).toBe(false);
    }
    expect(tagging.tag).not.toHaveBeenCalled();
    expect(onNewTag).not.toHaveBeenCalled();
  });

  // A drag's state updates at React's continuous priority, which flushSync doesn't flush.
  const outlined = (el: HTMLElement) => vi.waitFor(() => expect(el.className).toMatch(/\bdrop\b/));
  const plain = (el: HTMLElement) => vi.waitFor(() => expect(el.className).not.toMatch(/\bdrop\b/));

  it("adds the tag to the IDs set at dragstart, never to what dataTransfer says", async () => {
    tagging.drag.current = ["a", "c"];
    const target = item("talks");
    const over = dragEvent("dragover");
    flushSync(() => target.dispatchEvent(over));
    expect(over.defaultPrevented).toBe(true);
    expect((over as unknown as { dataTransfer: { dropEffect: string } }).dataTransfer.dropEffect).toBe("copy");
    await outlined(target); // the 2px orange outline
    flushSync(() => target.dispatchEvent(dragEvent("drop", { "text/plain": "b" })));
    expect(tagging.tag).toHaveBeenCalledWith("add", ["a", "c"], ["talks"], { kind: "drop" });
    expect(tagging.drag.current).toBeNull(); // used once
    await plain(target);
  });

  it("adds a note type as its notes/ tag, and a private tag too", () => {
    tagging.drag.current = ["a"];
    flushSync(() => item("lecture").dispatchEvent(dragEvent("drop")));
    expect(tagging.tag).toHaveBeenLastCalledWith("add", ["a"], ["notes/lecture"], { kind: "drop" });
    tagging.drag.current = ["b"];
    flushSync(() => item("private").dispatchEvent(dragEvent("drop")));
    expect(tagging.tag).toHaveBeenLastCalledWith("add", ["b"], ["private"], { kind: "drop" });
  });

  it("opens the picker for the dragged recordings on + New tag…, and for the selection on a click", () => {
    tagging.drag.current = ["b"];
    flushSync(() => newTag().dispatchEvent(dragEvent("drop")));
    expect(onNewTag).toHaveBeenLastCalledWith(["b"]);
    expect(tagging.tag).not.toHaveBeenCalled();
    flushSync(() => newTag().click());
    expect(onNewTag).toHaveBeenLastCalledWith(null);
  });

  it("isn't a drop target on All recordings or Untagged", () => {
    tagging.drag.current = ["a"];
    const all = host.querySelector<HTMLElement>("[data-testid=filter-all]")!;
    const over = dragEvent("dragover");
    flushSync(() => all.dispatchEvent(over));
    expect(over.defaultPrevented).toBe(false);
  });

  it("drops the outline when the drag leaves, but not when it moves onto the item's own text", async () => {
    tagging.drag.current = ["a"];
    const target = item("talks");
    flushSync(() => target.dispatchEvent(dragEvent("dragover")));
    await outlined(target);
    const inner = dragEvent("dragleave");
    Object.defineProperty(inner, "relatedTarget", { value: target.firstElementChild });
    flushSync(() => target.dispatchEvent(inner));
    await new Promise((r) => setTimeout(r, 20)); // time for a wrong update to land
    expect(target.className).toMatch(/\bdrop\b/);
    flushSync(() => target.dispatchEvent(dragEvent("dragleave")));
    await plain(target);
  });

  it("keeps a private tag's item in the Private group when its last recording loses it", () => {
    const filter: Filter = { kind: "tag", tag: "private" };
    draw(library, filter);
    draw({ ...library, tags: [{ tag: "talks", count: 1, private: false }] }, filter);
    const html = host.innerHTML;
    expect(html).toMatch(/Private<\/div>.*class="si on"[^>]*>.*private.*<span class="n">0<\/span>/s);
    expect(html.indexOf('data-testid="new-tag"')).toBeLessThan(html.indexOf("Private</div>"));
  });
});
