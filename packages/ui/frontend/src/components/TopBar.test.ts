import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("./ThemeSwitch", () => ({ ThemeSwitch: () => null }));

import { TopBar } from "./TopBar";

describe("the top bar (spec §7)", () => {
  it("puts Tags between Library and Status, and marks the current page", () => {
    const html = renderToStaticMarkup(createElement(TopBar, { page: "tags", onPage: () => {} }));
    const order = [...html.matchAll(/>(Library|Tags|Status)</g)].map((m) => m[1]);
    expect(order).toEqual(["Library", "Tags", "Status"]);
    expect(html).toMatch(/aria-current="page"[^>]*>Tags</);
  });
});
