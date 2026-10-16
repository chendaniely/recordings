import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sonner = vi.hoisted(() => ({ dismiss: vi.fn() }));
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { dismiss: sonner.dismiss }) }));

import { ActionToast, AlertToast, ProgressToast, Rich } from "./ActionToast";

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
  let host: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  const draw = (text: string) => act(() => root.render(createElement(ActionToast, { id: "grid-cells-1", text: [text], undo: null })));
  const hover = (type: "mouseover" | "mouseout") =>
    act(() => {
      host.firstElementChild!.dispatchEvent(new MouseEvent(type, { bubbles: true, relatedTarget: document.body }));
    });

  beforeEach(() => {
    vi.useFakeTimers();
    sonner.dismiss.mockClear();
    host = document.body.appendChild(document.createElement("div"));
    root = createRoot(host);
  });
  afterEach(() => {
    root.unmount();
    host.remove();
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
    hover("mouseover");
    draw("2 changes");
    vi.advanceTimersByTime(60_000);
    expect(sonner.dismiss).not.toHaveBeenCalled();
    hover("mouseout");
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
});
