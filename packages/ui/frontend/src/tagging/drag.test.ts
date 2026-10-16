import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DROP_TARGET, newDragMark, ours, watchDrags, type InPageDrag } from "./drag";

/** A drag event as a browser sends it; jsdom has no DragEvent. `types` are the formats set at
 * dragstart, readable in every drag event (protected mode). */
function dragEvent(type: string, types: string[] = []): Event {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, "dataTransfer", { value: { types } });
  return event;
}

describe("the in-page drag's marker (Task 13 fix round 1)", () => {
  it("is a custom type, lowercase as setData leaves it, new for each drag", () => {
    const a = newDragMark();
    expect(a).toMatch(/^application\/x-recordings-drag-[0-9a-f]{16}$/);
    expect(newDragMark()).not.toBe(a);
  });
  it("makes a drag ours only with the state set at dragstart and its marker in the event's types", () => {
    const drag: InPageDrag = { ids: ["a"], mark: newDragMark() };
    expect(ours(drag, dragEvent("dragover", ["text/plain", drag.mark]))).toBe(true);
    expect(ours(drag, dragEvent("dragover", ["Files"]))).toBe(false); // stale state, a file dropped
    expect(ours(drag, dragEvent("dragover", ["text/plain", newDragMark()]))).toBe(false); // another drag
    expect(ours(null, dragEvent("dragover", [drag.mark]))).toBe(false);
    expect(ours(drag, new Event("dragover"))).toBe(false); // no dataTransfer at all
  });
});

describe("watchDrags: the window forgets a drag (Task 13 fix round 1)", () => {
  const ref: { current: InPageDrag | null } = { current: null };
  let stop: () => void;
  let host: HTMLDivElement;
  const tick = () => new Promise((r) => setTimeout(r, 0));

  beforeEach(() => {
    ref.current = { ids: ["a", "b"], mark: newDragMark() };
    stop = watchDrags(ref);
    host = document.body.appendChild(document.createElement("div"));
    host.innerHTML = `<input id="box"><button id="target" ${DROP_TARGET}><span id="inner">talks</span></button>`;
  });
  afterEach(() => {
    stop();
    host.remove();
  });

  it("on a dragend anywhere, even one a handler below stops", () => {
    const box = host.querySelector("#box")!;
    box.addEventListener("dragend", (e) => e.stopPropagation());
    box.dispatchEvent(dragEvent("dragend"));
    expect(ref.current).toBeNull();
  });

  it("on the next pointerdown anywhere", () => {
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    expect(ref.current).toBeNull();
  });

  it("after a drop, once the drop target has read it", async () => {
    const target = host.querySelector("#target")!;
    let seen: InPageDrag | null = null;
    target.addEventListener("drop", () => { seen = ref.current; });
    const drop = dragEvent("drop", [ref.current!.mark]);
    target.querySelector("#inner")!.dispatchEvent(drop);
    expect(seen).not.toBeNull();
    expect(drop.defaultPrevented).toBe(false); // a drop target's own
    await tick();
    expect(ref.current).toBeNull();
  });

  it("keeps our drag off a text field: nothing is pasted there", () => {
    const drop = dragEvent("drop", ["text/plain", ref.current!.mark]);
    host.querySelector("#box")!.dispatchEvent(drop);
    expect(drop.defaultPrevented).toBe(true);
  });

  it("leaves a drop that isn't ours alone", () => {
    const drop = dragEvent("drop", ["Files"]);
    host.querySelector("#box")!.dispatchEvent(drop);
    expect(drop.defaultPrevented).toBe(false);
  });

  it("stops listening when stopped", () => {
    stop();
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    expect(ref.current).not.toBeNull();
    stop = () => {};
  });
});
