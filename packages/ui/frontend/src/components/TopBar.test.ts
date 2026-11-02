import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("./ThemeSwitch", () => ({ ThemeSwitch: () => null }));

import { TopBar } from "./TopBar";

describe("the top bar (spec §7; 2b spec §15.1)", () => {
  it("reads Library · Tags · Add · Status, and marks the current page", () => {
    const html = renderToStaticMarkup(createElement(TopBar, { page: "add", onPage: () => {} }));
    const order = [...html.matchAll(/>(Library|Tags|Add|Status)</g)].map((m) => m[1]);
    expect(order).toEqual(["Library", "Tags", "Add", "Status"]);
    expect(html).toMatch(/aria-current="page"[^>]*>Add</);
  });
  it("keeps the logo's name apart, so a phone can show the mark alone", () => {
    const html = renderToStaticMarkup(createElement(TopBar, { page: "library", onPage: () => {} }));
    expect(html).toContain('<span class="logo-name">Recordings</span>');
  });
});
