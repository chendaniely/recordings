import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sonner = vi.hoisted(() => ({ dismiss: vi.fn() }));
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { dismiss: sonner.dismiss }) }));

import { ActionToast, AlertToast, ProgressToast, Rich } from "./ActionToast";
import { watchToaster } from "./toaster";

describe("toast bodies (spec §6)", () => {
  it("draws a tag as code, as text and never as HTML", () => {
    const html = renderToStaticMarkup(createElement(Rich, { text: ["Added ", { code: "<b>x</b>" }, " to 1 recording"] }));
    expect(html).toBe("<span>Added </span><code>&lt;b&gt;x&lt;/b&gt;</code><span> to 1 recording</span>");
  });
  it("announces progress once, and keeps the moving count out of the live region", () => {
    const html = renderToStaticMarkup(createElement(ProgressToast, { announced: "Tagging 2,000 recordings…", shown: "Tagging 1,200 of 2,000…" }));
    expect(html).toContain('<span class="sr-only">Tagging 2,000 recordings…</span>');
    expect(html).toContain('<span aria-hidden="true">Tagging 1,200 of 2,000…</span>');
  });
  it("puts partial failures in an alert that stays until dismissed", () => {
    const html = renderToStaticMarkup(createElement(AlertToast, { id: "x", lines: ["3 the recording is gone"] }));
    expect(html).toContain('role="alert"');
    expect(html).toContain(">Dismiss</button>");
  });
});

// The tests run React's production build (vite.config's define), which has no act(): every
// render is made synchronous with flushSync, which also runs the effects it commits.
const act = (fn: () => void) => flushSync(fn);

describe("an action toast's own timer (plan decision 9)", () => {
  // The toast sits in a toaster, as Sonner draws it: the pause is the whole toaster's.
  let toaster: HTMLOListElement;
  let host: HTMLLIElement;
  let root: ReturnType<typeof createRoot>;
  let stop: () => void;
  const draw = (text: string) => act(() => root.render(createElement(ActionToast, { id: "grid-cells-1", text: [text], undo: null })));
  const pointer = (into: boolean) =>
    act(() => {
      const at = into ? host : document.body;
      at.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, relatedTarget: null }));
    });

  beforeEach(() => {
    vi.useFakeTimers();
    sonner.dismiss.mockClear();
    toaster = document.body.appendChild(document.createElement("ol"));
    toaster.setAttribute("data-sonner-toaster", "");
    host = toaster.appendChild(document.createElement("li"));
    root = createRoot(host);
    stop = watchToaster();
  });
  afterEach(() => {
    root.unmount();
    stop();
    toaster.remove();
    vi.useRealTimers();
  });

  it("dismisses itself after about 8 seconds", () => {
    draw("1 change");
    vi.advanceTimersByTime(7999);
    expect(sonner.dismiss).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(sonner.dismiss).toHaveBeenCalledWith("grid-cells-1");
  });
  it("starts its time again when its text changes, and never under a hovering pointer", () => {
    // why: the grid's merged toast ("4 changes") is updated in place; a fresh timer that forgot
    // the hover would dismiss it under the pointer (Task 9 review).
    draw("1 change");
    vi.advanceTimersByTime(6000);
    pointer(true); // on the toast's own <li>, outside its text: still the toaster
    draw("2 changes");
    vi.advanceTimersByTime(60_000);
    expect(sonner.dismiss).not.toHaveBeenCalled();
    pointer(false);
    vi.advanceTimersByTime(7999);
    expect(sonner.dismiss).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(sonner.dismiss).toHaveBeenCalledTimes(1);
  });
  it("a new text without a hover gets a fresh 8 seconds", () => {
    draw("1 change");
    vi.advanceTimersByTime(6000);
    draw("2 changes");
    vi.advanceTimersByTime(7999);
    expect(sonner.dismiss).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(sonner.dismiss).toHaveBeenCalledTimes(1);
  });
  it("gives focus back to the page before its Undo or Dismiss takes the toast away", () => {
    // why: from the keyboard, focus stayed on a button that went, so on <body> (Task 10 council).
    const before = document.body.appendChild(document.createElement("button"));
    const run = vi.fn();
    act(() => root.render(createElement(ActionToast, { id: "t", text: ["Removed"], undo: { label: "Undo", run } })));
    before.focus();
    const undo = host.querySelector("button")!;
    undo.focus();
    act(() => undo.click());
    expect(document.activeElement).toBe(before);
    expect(run).toHaveBeenCalledTimes(1);
    expect(sonner.dismiss).toHaveBeenCalledWith("t");
    act(() => root.render(createElement(AlertToast, { id: "a", lines: ["3 the recording is gone"] })));
    const dismiss = host.querySelector("button")!;
    dismiss.focus();
    act(() => dismiss.click());
    expect(document.activeElement).toBe(before);
    before.remove();
  });
});
