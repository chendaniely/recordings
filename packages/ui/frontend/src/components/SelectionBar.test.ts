import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../sr", () => ({ useSetShinyInput: () => () => {}, useShinyMessageHandler: () => {} }));

import { NO_PLAUD, PlaudContext, type Plaud } from "../plaud/usePlaud";
import type { PlaudResult } from "../types";
import { MAX_RESYNC, SelectionBar, TOO_MANY_RESYNC, resyncReason } from "./SelectionBar";

const NOT_YET = "Comes with the next part of stage 2b.";
const GATE = "Scheduled sync waits for the first catch-up (runbook)";

const bar = { onTag: () => {}, onClear: () => {}, onResync: () => {}, resync: null };
const render = (count: number, lastTagged: string[] | null = null) =>
  renderToStaticMarkup(createElement(SelectionBar, { count, lastTagged, ...bar }));

describe("the selection bar (spec §6)", () => {
  it("appears with two or more rows selected", () => {
    expect(render(1)).toBe("");
    expect(render(12)).toContain("12 selected");
    expect(render(1200)).toContain("1,200 selected");
  });
  it("offers Tag… (T) and Clear (Esc), and says what was just tagged", () => {
    const html = render(12, ["work"]);
    expect(html).toContain("Tag…");
    expect(html).toContain("<kbd>T</kbd>");
    expect(html).toContain("<kbd>Esc</kbd>");
    expect(html).toContain("just tagged <code>work</code>");
  });
});

describe("the selection bar, fix round 1 (Task 12)", () => {
  it("says how many tags were just added as plain text, not as a tag name", () => {
    const html = render(12, ["work", "school/course-101", "review"]);
    expect(html).toContain("just tagged 3 tags");
    expect(html).not.toContain("<code>");
  });
  it("Esc inside the bar clears, as its Clear (Esc) says", () => {
    const onClear = vi.fn();
    const host = document.body.appendChild(document.createElement("div"));
    const root = createRoot(host);
    flushSync(() => root.render(createElement(SelectionBar, { count: 3, lastTagged: null, ...bar, onClear })));
    const tag = host.querySelector("button")!;
    tag.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(onClear).toHaveBeenCalledOnce();
    root.unmount();
    host.remove();
  });
});

describe("Re-sync from Plaud on a selection (2b spec §14.6)", () => {
  const plaud = (over: Partial<Plaud> = {}): Plaud => ({
    send: vi.fn(() => "rid-1"), results: {}, last: null, lastLine: null, available: true, reason: null,
    why: () => null, handles: () => true, gate: null, ...over,
  });
  const drawn = (p: Plaud, props: Partial<Parameters<typeof SelectionBar>[0]> = {}) => {
    const host = document.body.appendChild(document.createElement("div"));
    const root = createRoot(host);
    flushSync(() => root.render(createElement(PlaudContext.Provider, { value: p },
      createElement(SelectionBar, { count: 12, lastTagged: null, ...bar, ...props }))));
    return { host, done: () => { root.unmount(); host.remove(); } };
  };
  it("Re-sync from Plaud sends one request with the selection", () => {
    // C9.05. The bar's owner (RecordingList, TagsPage) sends the request with the selection's IDs.
    const onResync = vi.fn();
    const { host, done } = drawn(plaud(), { onResync });
    const resync = [...host.querySelectorAll("button")].find((b) => b.textContent === "Re-sync from Plaud")!;
    expect(resync.hasAttribute("disabled")).toBe(false);
    flushSync(() => resync.click());
    expect(onResync).toHaveBeenCalledOnce();
    done();
  });
  it("over 1,000 it says use Sync everything", () => {
    expect(resyncReason(plaud(), MAX_RESYNC)).toBeNull();
    expect(resyncReason(plaud(), MAX_RESYNC + 1)).toBe(TOO_MANY_RESYNC);
    expect(resyncReason(null, 12)).toBe(NO_PLAUD);
    expect(resyncReason(plaud({ why: () => "Demo: nothing is synced" }), 12)).toBe("Demo: nothing is synced");
    const { host, done } = drawn(plaud(), { count: 1_001, resync: TOO_MANY_RESYNC });
    const resync = [...host.querySelectorAll("button")].find((b) => b.textContent === "Re-sync from Plaud")!;
    expect(resync.hasAttribute("disabled")).toBe(true);
    expect(host.textContent).toContain("For more, use Sync everything");
    done();
  });
  it("draws no Re-sync while the worker can't handle one", () => {
    // Phase 1's bar has no dead button (the council's X6); the Sync screen keeps its own, off.
    const { host, done } = drawn(plaud({ handles: () => false, why: () => NOT_YET }), { resync: NOT_YET });
    expect(host.querySelector("[data-request=resync]")).toBeNull();
    expect(host.textContent).not.toContain(NOT_YET);
    done();
    expect(render(12)).not.toContain("Re-sync"); // nor without App's Plaud context
  });
  it("says it comes with the next part, and the gate's reason, as Details does", () => {
    // Phase 1's worker can't handle a re-sync yet; once it can, the gate holds it until the first
    // catch-up (§11.6). Either reason disables the button and describes it.
    expect(resyncReason(plaud({ why: () => NOT_YET, gate: GATE }), 12)).toBe(NOT_YET);
    expect(resyncReason(plaud({ gate: GATE }), 12)).toBe(GATE);
    const { host, done } = drawn(plaud(), { resync: NOT_YET });
    const resync = [...host.querySelectorAll("button")].find((b) => b.textContent === "Re-sync from Plaud")!;
    expect(resync.hasAttribute("disabled")).toBe(true);
    expect(host.querySelector(`#${resync.getAttribute("aria-describedby")}`)!.textContent).toBe(NOT_YET);
    done();
  });
  it("its reply shows Re-syncing 12 of 25 then the end line", () => {
    const running: PlaudResult = {
      request_id: "rid-1", kind: "resync", state: "running", message: "Re-syncing", progress: { done: 12, total: 25 },
      result: null, preview: null,
    };
    let view = drawn(plaud({ last: running }));
    expect(view.host.querySelector("[data-testid=resync-reply]")!.textContent).toBe("Re-syncing 12 of 25…");
    view.done();
    const end = "25 checked: 7 updated (speakers 6, title 1) · 18 up to date";
    view = drawn(plaud({ last: { ...running, state: "done", message: end, progress: { done: 25, total: 25 } } }));
    expect(view.host.querySelector("[data-testid=resync-reply]")!.textContent).toBe(end);
    view.done();
    // Another kind's reply isn't this bar's.
    view = drawn(plaud({ last: { ...running, kind: "sync" } }));
    expect(view.host.querySelector("[data-testid=resync-reply]")).toBeNull();
    view.done();
  });
});
