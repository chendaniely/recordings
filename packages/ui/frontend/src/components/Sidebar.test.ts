import { createElement, useState } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { newDragMark, type InPageDrag } from "../tagging/drag";
import type { Filter, LibraryView, TagCount } from "../types";

const tagging = vi.hoisted(() => ({
  canTag: true, drag: { current: null as InPageDrag | null }, tag: (() => {}) as (...args: unknown[]) => void,
}));
vi.mock("../tagging/useTagging", () => ({ useTaggingContext: () => tagging }));

import { Sidebar, useListedFilter } from "./Sidebar";

const library = {
  recordings: [], counts: { all: 2, untagged: 1 }, problems: [], can_tag: true, cannot_tag_reason: null, saves: true,
  tags: [{ tag: "talks", count: 1, private: false }, { tag: "private", count: 1, private: true }],
  note_types: [{ note_type: "lecture", count: 1 }],
} as LibraryView;
const render = (filter: Parameters<typeof Sidebar>[0]["filter"], canTag = true, view = library) => {
  tagging.canTag = canTag;
  return renderToStaticMarkup(
    createElement(Sidebar, { library: view, filter, onFilter: () => {}, onNewTag: () => {}, lastListed: null }),
  );
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
 * from the native event. `types` lists the formats set at dragstart, in every drag event. */
function dragEvent(type: string, data: Record<string, string> = {}): Event {
  const event = new Event(type, { bubbles: true, cancelable: true });
  const transfer = { dropEffect: "none", getData: (t: string) => data[t] ?? "", types: Object.keys(data) };
  Object.defineProperty(event, "dataTransfer", { value: transfer });
  return event;
}

/** App's part: it holds the active filter's last-listed tag, since the sidebar unmounts on the
 * Status page. `seen` counts the dragleaves that reached the sidebar: React draws it in the same
 * batch as the sidebar's own state from that event, so once it shows, that state has landed. */
function Harness({ view, filter, shown, onNewTag }: {
  view: LibraryView; filter: Filter; shown: boolean; onNewTag: (ids: string[] | null) => void;
}) {
  const lastListed = useListedFilter(filter, view);
  const [seen, setSeen] = useState(0);
  return createElement(
    "div",
    { "data-seen": seen, onDragLeave: () => setSeen((n) => n + 1) },
    shown ? createElement(Sidebar, { library: view, filter, onFilter: () => {}, onNewTag, lastListed }) : null,
  );
}

describe("dropping on the sidebar (spec §6)", () => {
  let host: HTMLDivElement;
  let root: Root;
  const onNewTag = vi.fn();
  const draw = (view: LibraryView, filter: Filter = { kind: "all" }, shown = true) =>
    flushSync(() => root.render(createElement(Harness, { view, filter, shown, onNewTag })));
  /** An in-page drag: the state set at dragstart, and its marker among the event's types. */
  const dragging = (ids: string[]) => {
    const mark = newDragMark();
    tagging.drag.current = { ids, mark };
    return { [mark]: "1" };
  };
  const seen = (n: number) => vi.waitFor(() => expect(host.firstElementChild!.getAttribute("data-seen")).toBe(String(n)));
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
    const ours = dragging(["a", "c"]);
    const target = item("talks");
    expect(target.hasAttribute("data-drop-target")).toBe(true);
    const over = dragEvent("dragover", ours);
    flushSync(() => target.dispatchEvent(over));
    expect(over.defaultPrevented).toBe(true);
    expect((over as unknown as { dataTransfer: { dropEffect: string } }).dataTransfer.dropEffect).toBe("copy");
    await outlined(target); // the 2px orange outline
    flushSync(() => target.dispatchEvent(dragEvent("drop", { ...ours, "text/plain": "b" })));
    expect(tagging.tag).toHaveBeenCalledWith("add", ["a", "c"], ["talks"], { kind: "drop" });
    expect(tagging.drag.current).toBeNull(); // used once
    await plain(target);
  });

  it("ignores a drop whose marker isn't among its types: stale state, and a file dropped from outside", () => {
    dragging(["a"]); // a drag whose dragend never reached the page
    const target = item("talks");
    const over = dragEvent("dragover", { Files: "" });
    flushSync(() => target.dispatchEvent(over));
    expect(over.defaultPrevented).toBe(false);
    const drop = dragEvent("drop", { Files: "", [newDragMark()]: "1" });
    flushSync(() => target.dispatchEvent(drop));
    expect(drop.defaultPrevented).toBe(false);
    expect(tagging.tag).not.toHaveBeenCalled();
  });

  it("adds a note type as its notes/ tag, and a private tag too", () => {
    flushSync(() => item("lecture").dispatchEvent(dragEvent("drop", dragging(["a"]))));
    expect(tagging.tag).toHaveBeenLastCalledWith("add", ["a"], ["notes/lecture"], { kind: "drop" });
    flushSync(() => item("private").dispatchEvent(dragEvent("drop", dragging(["b"]))));
    expect(tagging.tag).toHaveBeenLastCalledWith("add", ["b"], ["private"], { kind: "drop" });
  });

  it("opens the picker for the dragged recordings on + New tag…, and for the selection on a click", () => {
    expect(newTag().hasAttribute("data-drop-target")).toBe(true);
    flushSync(() => newTag().dispatchEvent(dragEvent("drop", dragging(["b"]))));
    expect(onNewTag).toHaveBeenLastCalledWith(["b"]);
    expect(tagging.tag).not.toHaveBeenCalled();
    flushSync(() => newTag().click());
    expect(onNewTag).toHaveBeenLastCalledWith(null);
  });

  it("isn't a drop target on All recordings or Untagged", () => {
    const all = host.querySelector<HTMLElement>("[data-testid=filter-all]")!;
    expect(all.hasAttribute("data-drop-target")).toBe(false);
    const over = dragEvent("dragover", dragging(["a"]));
    flushSync(() => all.dispatchEvent(over));
    expect(over.defaultPrevented).toBe(false);
  });

  it("drops the outline when the drag leaves, but not when it moves onto the item's own text", async () => {
    const ours = dragging(["a"]);
    const target = item("talks");
    flushSync(() => target.dispatchEvent(dragEvent("dragover", ours)));
    await outlined(target);
    const inner = dragEvent("dragleave", ours);
    Object.defineProperty(inner, "relatedTarget", { value: target.firstElementChild });
    flushSync(() => target.dispatchEvent(inner));
    await seen(1); // the sidebar's own update from that dragleave, if any, has landed too
    expect(target.className).toMatch(/\bdrop\b/);
    flushSync(() => target.dispatchEvent(dragEvent("dragleave", ours)));
    await seen(2);
    await plain(target);
  });

  it("keeps a private tag's item in the Private group when its last recording loses it, through a Status round trip", () => {
    const filter: Filter = { kind: "tag", tag: "private" };
    const without = { ...library, tags: [{ tag: "talks", count: 1, private: false }] as TagCount[] };
    const kept = () => {
      const html = host.innerHTML;
      expect(html).toMatch(/Private<\/div>.*class="si on"[^>]*><span><svg[^>]*lock.*private.*<span class="n">0<\/span>/s);
      expect(html.indexOf('data-testid="new-tag"')).toBeLessThan(html.indexOf("Private</div>"));
    };
    draw(library, filter);
    draw(without, filter);
    kept();
    draw(without, filter, false); // the Status page: the sidebar unmounts, App stays
    draw(without, filter, true);
    kept();
  });
});
