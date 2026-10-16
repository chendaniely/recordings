import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ShownTag } from "../lib/library";
import { TagChips, nextControl } from "./TagChips";

const draw = (tags: ShownTag[], canTag = true) =>
  renderToStaticMarkup(createElement(TagChips, { tags, title: "Week 4", canTag, place: "header", onRemove: () => {} }));

describe("chips (spec §6)", () => {
  it("names each × after its tag and the recording", () => {
    const html = draw([{ tag: "school/course-101", by: "you" }]);
    expect(html).toContain('aria-label="Remove tag school/course-101 from Week 4"');
    expect(html).toContain('tabindex="0"');
  });
  it("isolates a tag's text left to right, so a right-to-left name can't reorder what's around it", () => {
    const html = draw([{ tag: "people/אב", by: "you" }]);
    expect(html).toContain('<bdi dir="ltr">people/אב</bdi>');
    expect(html).toContain('title="people/אב"');
  });
  it("draws a tag as text, never as HTML", () => {
    expect(draw([{ tag: "<b>x</b>", by: "you" }])).toContain("&lt;b&gt;x&lt;/b&gt;");
  });
  it("draws no × while tagging is off, or while the chip's change is pending", () => {
    expect(draw([{ tag: "talks", by: "you" }], false)).not.toContain("<button");
    const pending = draw([{ tag: "talks", by: "you", pending: "remove" }]);
    expect(pending).not.toContain("<button");
    expect(pending).toContain("(removing)");
  });
});

describe("nextControl: where focus goes after × (spec §6)", () => {
  const row = (n: number): HTMLElement[] => {
    const box = document.body.appendChild(document.createElement("div"));
    box.setAttribute("data-controls", "");
    return Array.from({ length: n }, () => {
      const button = box.appendChild(document.createElement("button"));
      button.setAttribute("data-control", "remove");
      return button;
    });
  };
  it("is the next control in the row, or the one before when it was the last", () => {
    const [a, b] = row(2);
    expect(nextControl(a)).toBe(b);
    expect(nextControl(b)).toBe(a);
    const [only] = row(1);
    expect(nextControl(only)).toBe(null);
  });
});
