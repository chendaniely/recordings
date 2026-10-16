import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { SelectionBar } from "./SelectionBar";

const render = (count: number, lastTagged: string[] | null = null) =>
  renderToStaticMarkup(createElement(SelectionBar, { count, lastTagged, onTag: () => {}, onClear: () => {} }));

describe("the selection bar (spec §6)", () => {
  it("appears with two or more rows selected", () => {
    expect(render(1)).toBe("");
    expect(render(12)).toContain("12 selected");
    expect(render(1200)).toContain("1,200 selected");
  });
  it("offers Tag… (T) and Clear (Esc), and says what was just tagged", () => {
    const html = render(12, ["work"]);
    expect(html).toContain("Tag…");
    expect(html).toContain("<kbd>T</kbd>");
    expect(html).toContain("<kbd>Esc</kbd>");
    expect(html).toContain("just tagged <code>work</code>");
  });
});

describe("the selection bar, fix round 1 (Task 12)", () => {
  it("says how many tags were just added as plain text, not as a tag name", () => {
    const html = render(12, ["work", "school/course-101", "review"]);
    expect(html).toContain("just tagged 3 tags");
    expect(html).not.toContain("<code>");
  });
  it("Esc inside the bar clears, as its Clear (Esc) says", () => {
    const onClear = vi.fn();
    const host = document.body.appendChild(document.createElement("div"));
    const root = createRoot(host);
    flushSync(() => root.render(createElement(SelectionBar, { count: 3, lastTagged: null, onTag: () => {}, onClear })));
    const tag = host.querySelector("button")!;
    tag.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(onClear).toHaveBeenCalledOnce();
    root.unmount();
    host.remove();
  });
});
