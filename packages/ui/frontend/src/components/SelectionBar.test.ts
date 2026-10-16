import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SelectionBar } from "./SelectionBar";

const render = (count: number, lastTagged: string | null = null) =>
  renderToStaticMarkup(createElement(SelectionBar, { count, lastTagged, onTag: () => {}, onClear: () => {} }));

describe("the selection bar (spec §6)", () => {
  it("appears with two or more rows selected", () => {
    expect(render(1)).toBe("");
    expect(render(12)).toContain("12 selected");
    expect(render(1200)).toContain("1,200 selected");
  });
  it("offers Tag… (T) and Clear (Esc), and says what was just tagged", () => {
    const html = render(12, "work");
    expect(html).toContain("Tag…");
    expect(html).toContain("<kbd>T</kbd>");
    expect(html).toContain("<kbd>Esc</kbd>");
    expect(html).toContain("just tagged <code>work</code>");
  });
});
