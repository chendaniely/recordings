import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TaggingContext, type Tagging } from "../tagging/useTagging";
import type { Arrivals } from "../types";

const shown = vi.hoisted(() => ({ arrivals: undefined as unknown }));
vi.mock("../sr", () => ({ useShinyOutputValue: (id: string) => (id === "plaud_arrivals" ? shown.arrivals : undefined) }));

import { HeadNotes, RefreshButton, arrivalsCue, flagsLine } from "./HeadNotes";

const NONE: Arrivals = { new: 0, private: 0, updated: 0, kept_out_late: 0, consent_late: 0 };
const tagging = (touched = 0) => ({ touched, saves: true, opening: false, canTag: true, reason: null, refresh: vi.fn() }) as unknown as Tagging;
const head = (node: ReturnType<typeof createElement>, t: Tagging = tagging()) =>
  renderToStaticMarkup(createElement(TaggingContext.Provider, { value: t }, node));

/** jsdom has no matchMedia: one that answers the phone query as told. */
const phone = (on: boolean) => vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: on, addEventListener() {}, removeEventListener() {} })));

afterEach(() => {
  vi.unstubAllGlobals();
  shown.arrivals = undefined;
});

describe("the arrivals cue (2b spec §15.3)", () => {
  it("the arrivals cue counts new, private and updated, and shortens on a phone", () => {
    // C9.06.
    const a = { ...NONE, new: 2, private: 1, updated: 2 };
    expect(arrivalsCue(a, false)).toBe("2 new · 1 private · 2 updated from Plaud");
    expect(arrivalsCue(a, true)).toBe("5 from Plaud");
    expect(arrivalsCue({ ...NONE, updated: 1 }, false)).toBe("1 updated from Plaud");
    expect(arrivalsCue(NONE, false)).toBeNull();
    expect(arrivalsCue(undefined, false)).toBeNull();
    shown.arrivals = a;
    expect(head(createElement(RefreshButton, { testId: "refresh" }), tagging(5)))
      .toContain(">5 changed · 2 new · 1 private · 2 updated from Plaud · Refresh</button>");
    phone(true);
    const host = document.body.appendChild(document.createElement("div"));
    const root = createRoot(host);
    flushSync(() => root.render(createElement(TaggingContext.Provider, { value: tagging() }, createElement(RefreshButton, { testId: "refresh" }))));
    expect(host.textContent).toBe("5 from Plaud · Refresh");
    root.unmount();
    host.remove();
  });
  it("is plain Refresh when nothing came in", () => {
    shown.arrivals = NONE;
    expect(head(createElement(RefreshButton, { testId: "refresh" }))).toContain(">Refresh</button>");
  });
  it("privacy flags show as counts with no title", () => {
    const a = { ...NONE, kept_out_late: 1, consent_late: 1 };
    expect(flagsLine(a)).toBe("1 kept out too late · 1 consent-listed in the archive");
    expect(flagsLine({ ...NONE, consent_late: 2 })).toBe("2 consent-listed in the archive");
    expect(flagsLine(NONE)).toBeNull();
    shown.arrivals = a;
    const html = head(createElement(HeadNotes, { hint: "", announce: { text: "", key: 0 } }));
    expect(html).toContain('data-testid="plaud-flags">1 kept out too late · 1 consent-listed in the archive · <a href="#/status">See Status</a>');
    shown.arrivals = NONE;
    expect(head(createElement(HeadNotes, { hint: "", announce: { text: "", key: 0 } }))).not.toContain("plaud-flags");
  });
});
