import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../sr", () => ({ useSetShinyInput: () => () => {}, useShinyMessageHandler: () => {} }));

import { NO_PLAUD, PlaudContext, type Plaud } from "../plaud/usePlaud";
import type { PlaudResult } from "../types";
import { MAX_RESYNC, SelectionBar, TOO_MANY_RESYNC, resyncProps, resyncReason } from "./SelectionBar";

const NOT_YET = "Comes with the next part of stage 2b.";
const GATE = "Scheduled sync waits for the first catch-up (runbook)";

const bar = { onTag: () => {}, onClear: () => {}, onResync: () => undefined as string | undefined, resync: null, selection: "a,b" };
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
    const again = (q: Plaud, more: Partial<Parameters<typeof SelectionBar>[0]> = {}) =>
      flushSync(() => root.render(createElement(PlaudContext.Provider, { value: q },
        createElement(SelectionBar, { count: 12, lastTagged: null, ...bar, ...props, ...more }))));
    again(p);
    return { host, again, done: () => { root.unmount(); host.remove(); } };
  };
  const named = (host: HTMLElement, name: string) =>
    [...host.querySelectorAll("button")].find((b) => b.textContent?.startsWith(name))!;
  it("Re-sync from Plaud sends one request with the selection", () => {
    // C9.05. The bar's owner (RecordingList, TagsPage) sends the request with the selection's IDs.
    const onResync = vi.fn(() => "rid-1");
    const { host, done } = drawn(plaud(), { onResync });
    const resync = [...host.querySelectorAll("button")].find((b) => b.textContent === "Re-sync from Plaud")!;
    expect(resync.hasAttribute("disabled")).toBe(false);
    flushSync(() => resync.click());
    expect(onResync).toHaveBeenCalledOnce();
    done();
  });
  it("the request carries the bar's targets, in list order, and nothing without a context", () => {
    // C9.05: the bar acts on targetsOf, which both owners pass to resyncProps.
    const send = vi.fn(() => "rid-1");
    const props = resyncProps(plaud({ send }), ["20261023T140512-0700_0000beef", "20261023T150000-0700_0000cafe"]);
    expect(props.resync).toBeNull();
    expect(props.selection).toBe("20261023T140512-0700_0000beef,20261023T150000-0700_0000cafe");
    expect(props.onResync()).toBe("rid-1");
    expect(send).toHaveBeenCalledExactlyOnceWith("resync", {
      recording_ids: ["20261023T140512-0700_0000beef", "20261023T150000-0700_0000cafe"],
    });
    const none = resyncProps(null, ["a", "b"]);
    expect(none.resync).toBe(NO_PLAUD);
    expect(none.onResync()).toBeUndefined();
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
  it("says the gate's reason, as Details does, once, named by the button", () => {
    // Once a re-sync is handled, the gate holds it until the first catch-up (§11.6).
    expect(resyncReason(plaud({ gate: GATE }), 12)).toBe(GATE);
    // An invalid keep-out word refuses a Re-sync too (§12.2; the end-of-branch privacy review).
    const word = "configuration: `keep_out_word` is invalid";
    expect(resyncReason(plaud({ word }), 12)).toBe(word);
    const { host, done } = drawn(plaud(), { resync: GATE });
    const resync = named(host, "Re-sync");
    expect(resync.hasAttribute("disabled")).toBe(true);
    expect(host.querySelector(`#${resync.getAttribute("aria-describedby")}`)!.textContent).toBe(GATE);
    done();
  });
  it("shows only the reply to the request this bar sent, and drops it on Clear and a new selection", () => {
    const running: PlaudResult = {
      request_id: "rid-1", kind: "resync", state: "running", message: "Re-syncing", progress: { done: 12, total: 25 },
      result: null, preview: null,
    };
    const end = "25 checked: 7 updated (speakers 6, title 1) · 18 up to date";
    const reply = (host: HTMLElement) => host.querySelector("[data-testid=resync-reply]")?.textContent;
    const onClear = vi.fn();
    // Another request's (or another kind's) newest reply isn't this bar's, before any press.
    const view = drawn(plaud({ last: running, results: { "rid-1": running } }), { onResync: () => "rid-1", onClear });
    expect(reply(view.host)).toBeUndefined();
    flushSync(() => named(view.host, "Re-sync").click());
    expect(reply(view.host)).toBe("Re-syncing (12 of 25)");
    view.again(plaud({ results: { "rid-1": { ...running, state: "done", message: end, progress: { done: 25, total: 25 } } } }),
      { onResync: () => "rid-1", onClear });
    expect(reply(view.host)).toBe(end);
    // A new selection: the reply was about the old one.
    view.again(plaud({ results: { "rid-1": running } }), { onResync: () => "rid-1", onClear, selection: "a,c" });
    expect(reply(view.host)).toBeUndefined();
    // Clear drops it too.
    flushSync(() => named(view.host, "Re-sync").click());
    expect(reply(view.host)).toBe("Re-syncing (12 of 25)");
    flushSync(() => named(view.host, "Clear").click());
    expect(onClear).toHaveBeenCalledOnce();
    expect(reply(view.host)).toBeUndefined();
    view.done();
  });
});
