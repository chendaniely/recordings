import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PHONE, useMediaQuery } from "./useMediaQuery";

/** jsdom has no matchMedia: a stand-in whose answer a test can change. */
function fakeMedia(initial: boolean) {
  const listeners = new Set<() => void>();
  const media = {
    matches: initial,
    addEventListener: (_: string, fn: () => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
  };
  vi.stubGlobal("matchMedia", vi.fn(() => media));
  return {
    listeners,
    set(on: boolean) {
      media.matches = on;
      flushSync(() => listeners.forEach((fn) => fn()));
    },
  };
}

let seen: boolean[] = [];
function Probe() {
  seen.push(useMediaQuery(PHONE));
  return null;
}

afterEach(() => {
  vi.unstubAllGlobals();
  seen = [];
});

describe("useMediaQuery", () => {
  it("answers at once, follows changes, and stops listening when unmounted", () => {
    const media = fakeMedia(true);
    const root = createRoot(document.createElement("div"));
    flushSync(() => root.render(createElement(Probe)));
    expect(window.matchMedia).toHaveBeenCalledWith("(max-width: 760px)");
    expect(seen[0]).toBe(true); // no first frame drawn for the wrong device
    media.set(false);
    expect(seen.at(-1)).toBe(false);
    root.unmount();
    expect(media.listeners.size).toBe(0);
  });
});

describe("useMediaQuery without matchMedia (fix round 1, Task 12)", () => {
  it("answers false, and doesn't throw, where there is no matchMedia (jsdom)", () => {
    expect(typeof window.matchMedia).toBe("undefined"); // jsdom, unstubbed
    const root = createRoot(document.createElement("div"));
    flushSync(() => root.render(createElement(Probe)));
    expect(seen).toEqual([false]);
    root.unmount();
  });
});
