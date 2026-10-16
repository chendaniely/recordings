import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { sheetRoom, useSheetRoom, type SheetRoom } from "./viewport";

describe("sheetRoom (a phone's bottom sheet above the on-screen keyboard)", () => {
  it("is the whole screen with no keyboard", () => {
    expect(sheetRoom(740, { height: 740, offsetTop: 0 })).toEqual({ top: 0, bottom: 0, height: 740 });
  });

  it("rises by the keyboard's height, and shrinks to what is left", () => {
    expect(sheetRoom(740, { height: 400, offsetTop: 0 })).toEqual({ top: 0, bottom: 340, height: 400 });
  });

  it("follows a visual viewport the browser scrolled down to the box", () => {
    expect(sheetRoom(740, { height: 400, offsetTop: 120 })).toEqual({ top: 120, bottom: 220, height: 400 });
  });

  it("never goes below the bottom edge, and rounds to whole pixels", () => {
    expect(sheetRoom(740, { height: 760.4, offsetTop: 0 })).toEqual({ top: 0, bottom: 0, height: 760 });
    expect(sheetRoom(740, { height: 399.6, offsetTop: 0.2 })).toEqual({ top: 0, bottom: 340, height: 400 });
  });
});

/** jsdom has no visualViewport: a stand-in that resizes as a keyboard would. */
function fakeVisual(height: number) {
  const target = new EventTarget();
  const visual = Object.assign(target, { height, offsetTop: 0 });
  vi.stubGlobal("visualViewport", visual);
  return {
    resize(next: number) {
      visual.height = next;
      flushSync(() => visual.dispatchEvent(new Event("resize")));
    },
  };
}

/** Effects run after the commit; a tick lets them, and the redraw they ask for, happen. */
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

let seen: (SheetRoom | null)[] = [];
function Probe({ on }: { on: boolean }) {
  seen.push(useSheetRoom(on));
  return null;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  seen = [];
});

describe("useSheetRoom", () => {
  it("follows the visual viewport while on, and stops listening when off", async () => {
    vi.spyOn(document.documentElement, "clientHeight", "get").mockReturnValue(740);
    const visual = fakeVisual(740);
    const root = createRoot(document.createElement("div"));
    flushSync(() => root.render(createElement(Probe, { on: true })));
    await tick();
    expect(seen.at(-1)).toEqual({ top: 0, bottom: 0, height: 740 });
    visual.resize(400); // the keyboard comes up
    expect(seen.at(-1)).toEqual({ top: 0, bottom: 340, height: 400 });
    flushSync(() => root.render(createElement(Probe, { on: false })));
    await tick();
    // Kept as it was, so a closing sheet slides out from where it stood (fix round 1, item 13).
    expect(seen.at(-1)).toEqual({ top: 0, bottom: 340, height: 400 });
    const before = seen.length;
    visual.resize(740); // no longer listening
    expect(seen.length).toBe(before);
    root.unmount();
  });

  it("is null where there is no visualViewport (jsdom): the CSS falls back", async () => {
    expect(window.visualViewport ?? null).toBe(null);
    const root = createRoot(document.createElement("div"));
    flushSync(() => root.render(createElement(Probe, { on: true })));
    await tick();
    expect(seen.at(-1)).toBe(null);
    root.unmount();
  });
});
