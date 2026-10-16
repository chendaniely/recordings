import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { pausableTimer } from "@/lib/dismiss";

import { createToasterPause, focusFallback, leaveToaster, watchToaster } from "./toaster";

/** A page with a row, a header title, and a toaster holding two toasts, each with a button. */
function page() {
  const root = document.body.appendChild(document.createElement("div"));
  const row = root.appendChild(document.createElement("button"));
  row.className = "row on";
  const head = root.appendChild(document.createElement("div"));
  head.dataset.testid = "recording-header";
  const h1 = head.appendChild(document.createElement("h1"));
  h1.tabIndex = -1;
  const toaster = root.appendChild(document.createElement("ol"));
  toaster.setAttribute("data-sonner-toaster", "");
  const toasts = [0, 1].map(() => {
    const li = toaster.appendChild(document.createElement("li"));
    li.tabIndex = 0;
    const button = li.appendChild(document.createElement("button"));
    return { li, button };
  });
  return { root, row, h1, toaster, toasts };
}

const over = (el: Element, from: Element | null = document.body) =>
  el.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, relatedTarget: from }));
const out = (el: Element) => el.dispatchEvent(new MouseEvent("mouseout", { bubbles: true, relatedTarget: null }));

describe("one pause for the whole toaster (Task 10 council)", () => {
  let p: ReturnType<typeof page>;
  let pause: ReturnType<typeof createToasterPause>;
  let stop: () => void;
  beforeEach(() => {
    vi.useFakeTimers();
    p = page();
    pause = createToasterPause();
    stop = watchToaster(document, pause);
  });
  afterEach(() => {
    stop();
    p.root.remove();
    vi.useRealTimers();
  });

  it("holds every toast's timer while the pointer is anywhere in the toaster, an older toast's padding included", () => {
    const a = vi.fn();
    const b = vi.fn();
    pause.watch(pausableTimer(8000, a));
    pause.watch(pausableTimer(8000, b));
    over(p.toasts[1].li); // the older toast, not the one about to go
    vi.advanceTimersByTime(60_000);
    expect(a).not.toHaveBeenCalled();
    expect(b).not.toHaveBeenCalled();
    over(p.row, p.toasts[1].li); // the pointer moved on to the page
    vi.advanceTimersByTime(8000);
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
  });
  it("holds them while focus is anywhere in the toaster: the list (Alt+T) or a toast's own <li>", () => {
    const a = vi.fn();
    pause.watch(pausableTimer(8000, a));
    p.toaster.tabIndex = -1;
    p.toaster.focus();
    p.toasts[0].li.focus();
    vi.advanceTimersByTime(60_000);
    expect(a).not.toHaveBeenCalled();
    p.row.focus();
    vi.advanceTimersByTime(8000);
    expect(a).toHaveBeenCalledTimes(1);
  });
  it("hover and focus together need both to end; leaving the window ends the hover", () => {
    const a = vi.fn();
    pause.watch(pausableTimer(1000, a));
    over(p.toasts[0].button);
    p.toasts[0].button.focus();
    out(p.toasts[0].button); // the pointer left the window
    vi.advanceTimersByTime(5000);
    expect(a).not.toHaveBeenCalled();
    p.row.focus();
    vi.advanceTimersByTime(1000);
    expect(a).toHaveBeenCalledTimes(1);
  });
  it("a toast that arrives while the toaster is held starts held", () => {
    over(p.toasts[0].li);
    const a = vi.fn();
    pause.watch(pausableTimer(1000, a));
    vi.advanceTimersByTime(5000);
    expect(a).not.toHaveBeenCalled();
  });
  it("forgets a toast that has gone", () => {
    const timer = pausableTimer(1000, vi.fn());
    const spy = vi.spyOn(timer, "pause");
    const unwatch = pause.watch(timer);
    unwatch();
    over(p.toasts[0].li);
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("focus after a toast's Undo, Redo or Dismiss (Task 10 council)", () => {
  let p: ReturnType<typeof page>;
  let stop: () => void;
  beforeEach(() => {
    p = page();
    stop = watchToaster(document, createToasterPause());
  });
  afterEach(() => {
    stop();
    p.root.remove();
  });

  it("goes back to what had it before the toaster, not to the page", () => {
    p.h1.focus();
    p.toasts[0].button.focus();
    leaveToaster();
    expect(document.activeElement).toBe(p.h1);
  });
  it("falls back to the open row, then the header's title, when that has gone", () => {
    const gone = p.root.appendChild(document.createElement("button"));
    gone.focus();
    p.toasts[0].button.focus();
    gone.remove();
    leaveToaster();
    expect(document.activeElement).toBe(p.row);
    expect(focusFallback()).toBe(p.row);
    p.row.remove();
    expect(focusFallback()).toBe(p.h1);
  });
  it("falls back to the Tags grid's focused cell, where there is no open row or header (Task 14)", () => {
    p.row.remove();
    p.h1.remove();
    const grid = p.root.appendChild(document.createElement("div"));
    grid.setAttribute("role", "grid");
    grid.innerHTML = '<div role="row"><span role="checkbox" tabindex="-1"></span><span role="checkbox" tabindex="0"></span></div>';
    expect(focusFallback()).toBe(grid.querySelector("[tabindex='0']"));
  });
  it("leaves focus alone when it isn't in the toaster (a mouse user elsewhere)", () => {
    p.h1.focus();
    p.row.focus();
    leaveToaster();
    expect(document.activeElement).toBe(p.row);
  });
});
