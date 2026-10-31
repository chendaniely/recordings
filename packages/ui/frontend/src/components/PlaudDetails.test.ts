import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PlaudKind, PlaudResult, RecordingPlaud } from "../types";

const RID = "20261023T140512-0700_0000beef"; // synthetic
vi.mock("../sr", () => ({
  useShinyOutputValue: (id: string) => (id === "recording" ? { id: "20261023T140512-0700_0000beef" } : undefined),
  useSetShinyInput: () => () => {},
  useShinyMessageHandler: () => {},
}));

import { PlaudContext, type Plaud } from "../plaud/usePlaud";
import { PlaudDetails } from "./PlaudDetails";

const PID = `of_${"0".repeat(31)}9`; // synthetic
const NOT_YET = "Comes with the next part of stage 2b.";
const EVERY: PlaudKind[] = ["compare", "sync", "import", "resync", "full", "accept", "accept-changes", "release-preview",
  "release", "unrelease", "park", "unpark", "keep-here"];
/** Details' lines from a fixture, with a worker that handles every kind, as Phase 3's will. */
const LINES: RecordingPlaud = {
  handled: EVERY, not_yet: NOT_YET,
  plaud_ids: [{ id: PID, copy_id: `- \`${PID}\` —` }],
  checked: "2026-10-23 14:20", next_by: "2026-10-24 14:20", gone_since: null,
  tagged_private: "Tagged private by the sync on 2026-10-23: its Plaud title changed", removed_private: null,
  flags: [], resync: null,
};

let sends: [string, unknown][];
const fake = (over: Partial<Plaud> = {}): Plaud => {
  sends = [];
  return {
    send: (kind, opts) => (sends.push([kind, opts]), "rid-1"), results: {}, last: null, lastLine: null,
    available: true, reason: null, why: () => null, handles: () => true, gate: null, ...over,
  };
};
const markup = (plaud: RecordingPlaud, p: Plaud = fake()) =>
  renderToStaticMarkup(createElement(PlaudContext.Provider, { value: p }, createElement(PlaudDetails, { plaud })));

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const draw = (plaud: RecordingPlaud, p: Plaud) =>
  flushSync(() => root.render(createElement(PlaudContext.Provider, { value: p }, createElement(PlaudDetails, { plaud }))));
const button = (name: string) => [...host.querySelectorAll<HTMLElement>("button")].find((b) => b.textContent === name)!;
beforeEach(() => {
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(() => {
  root.unmount();
  host.remove();
});

describe("Details' Plaud lines (2b spec §15.3)", () => {
  it("checked and next by", () => {
    expect(markup(LINES)).toContain("Plaud: checked 2026-10-23 14:20 · next by 2026-10-24 14:20");
    expect(markup({ ...LINES, checked: null, next_by: null })).toContain("Plaud: not checked yet");
  });
  it("each Plaud ID with Copy ID", () => {
    const html = markup({ ...LINES, plaud_ids: [...LINES.plaud_ids, { id: "0".repeat(31) + "8", copy_id: "- `" + "0".repeat(31) + "8` —" }] });
    expect(html.match(/data-testid="copy-id"/g)).toHaveLength(2);
    expect(html).toContain(`aria-label="Copy ID ${PID}"`);
  });
  it("gone from Plaud since", () => {
    expect(markup({ ...LINES, gone_since: "2026-10-23" })).toContain("Gone from Plaud since 2026-10-23 (kept)");
    expect(markup(LINES)).not.toContain("plaud-gone");
  });
  it("tagged private by the sync, and decision 3's line once Dan removed it", () => {
    expect(markup(LINES)).toContain("Tagged private by the sync on 2026-10-23: its Plaud title changed");
    const removed = "Tagged private by the sync on 2026-10-23. You removed it on 2026-10-24. It comes back only if its Plaud title changes to a private one.";
    const html = markup({ ...LINES, tagged_private: null, removed_private: removed }).replaceAll("&#x27;", "'");
    expect(html).toContain(removed);
    expect(html).not.toContain(": its Plaud title changed");
  });
  it("a late keep-out offers Keep it here", () => {
    const flag = {
      kind: "kept-out-late" as const, recording_id: RID, plaud_id: PID, copy_id: `- \`${PID}\` —`,
      sentence: "Kept out by title, but already in the archive: take it out by hand (runbook)", keep_here: true,
      date: null, duration: null,
    };
    draw({ ...LINES, flags: [flag] }, fake());
    expect(host.textContent).toContain("Kept out by title, but already in the archive: take it out by hand (runbook)");
    flushSync(() => button("Keep it here").click());
    expect(sends).toEqual([["keep-here", { recording_ids: [RID] }]]);
  });
  it("Re-sync from Plaud sends this recording, and its reply shows in place", () => {
    const p = fake();
    draw(LINES, p);
    flushSync(() => button("Re-sync from Plaud").click());
    expect(sends).toEqual([["resync", { recording_ids: [RID] }]]);
    const reply: PlaudResult = {
      request_id: "rid-1", kind: "resync", state: "done", message: "Updated: speakers", progress: null, result: null, preview: null,
    };
    draw(LINES, { ...p, results: { "rid-1": reply } });
    expect(host.querySelector("[data-testid=resync-reply]")!.textContent).toBe(" Updated: speakers");
  });
  it("in Phase 1 there is no Re-sync and no check line, and Keep it here is live", () => {
    // Phase 1's worker handles Keep it here alone, and checks come with Phase 3: Details draws
    // no dead button and no "not checked yet" (the council's X6).
    const flag = {
      kind: "kept-out-late" as const, recording_id: RID, plaud_id: PID, copy_id: `- \`${PID}\` —`,
      sentence: "Kept out by title, but already in the archive: take it out by hand (runbook)", keep_here: true,
      date: null, duration: null,
    };
    draw({ ...LINES, checked: null, next_by: null, flags: [flag], handled: ["keep-here"] }, fake());
    expect(button("Re-sync from Plaud")).toBeUndefined();
    expect(host.querySelector("[data-testid=plaud-checked]")).toBeNull();
    expect(host.textContent).not.toContain(NOT_YET);
    flushSync(() => button("Keep it here").click());
    expect(sends).toEqual([["keep-here", { recording_ids: [RID] }]]);
  });
  it("says why Re-sync is off: the gate, or no way to ask", () => {
    const gate = "Scheduled sync waits for the first catch-up (runbook)";
    draw({ ...LINES, resync: gate }, fake());
    expect(button("Re-sync from Plaud").hasAttribute("disabled")).toBe(true);
    expect(host.textContent).toContain(gate);
    draw(LINES, fake({ available: false, reason: "Demo: nothing is synced" }));
    expect(button("Re-sync from Plaud").hasAttribute("disabled")).toBe(true);
    expect(host.textContent).toContain("Demo: nothing is synced");
  });
});
