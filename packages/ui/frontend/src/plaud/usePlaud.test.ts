import { createElement, type ReactElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PlaudRequest, PlaudResult, SyncView } from "../types";

// The Shiny side, faked as in useTagging.test.ts: each input's setter records what it sends; each
// message handler is kept so a test can deliver a reply.
const shiny = vi.hoisted(() => ({
  sent: {} as Record<string, unknown[]>,
  setters: {} as Record<string, (value: unknown) => void>,
  handlers: {} as Record<string, (data: unknown) => void>,
}));
vi.mock("../sr", () => ({
  useSetShinyInput: (id: string) => {
    shiny.setters[id] ??= (value: unknown) => {
      (shiny.sent[id] ??= []).push(value);
    };
    return shiny.setters[id];
  },
  useShinyMessageHandler: (id: string, handler: (data: unknown) => void) => {
    shiny.handlers[id] = handler;
  },
}));
const sonner = vi.hoisted(() => ({ toast: null as unknown as ReturnType<typeof vi.fn> }));
vi.mock("sonner", () => {
  sonner.toast = vi.fn();
  return { toast: Object.assign(sonner.toast, { dismiss: vi.fn() }) };
});

import { NO_PLAUD, kindReason, usePlaud, type Plaud } from "./usePlaud";

const NOT_YET = "Comes with the next part of stage 2b.";
const GATE = "Scheduled sync waits for the first catch-up (runbook)";
/** Phase 1's Sync view, as far as the hook reads it. */
const VIEW = {
  available: true, reason: null, last_result: null, handled: ["keep-here"], not_yet: NOT_YET, gate: GATE,
} as unknown as SyncView;
const reply = (request: PlaudRequest, over: Partial<PlaudResult> = {}): PlaudResult => ({
  request_id: request.request_id, kind: request.kind, state: "queued", message: "Queued", progress: null,
  result: null, preview: null, ...over,
});
type Toasted = ReactElement<{ undo: { label: string; run: () => void } | null }>;

let plaud: Plaud;
function Probe({ view, onView }: { view: SyncView | undefined; onView?: () => void }) {
  plaud = usePlaud(view, onView);
  return null;
}
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const act = (fn: () => void) => flushSync(fn);
const draw = (view: SyncView | undefined = VIEW, onView?: () => void) =>
  act(() => root.render(createElement(Probe, { view, onView })));
const hear = (r: PlaudResult) => act(() => shiny.handlers.plaud_result(r));
const sent = () => (shiny.sent.plaud_request ?? []) as PlaudRequest[];

beforeEach(() => {
  shiny.sent = {};
  sonner.toast.mockClear();
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(() => {
  root.unmount();
  host.remove();
});

describe("usePlaud (2b spec §9.3)", () => {
  it("nothing is sent on mount", () => {
    draw();
    expect(shiny.sent).toEqual({});
    expect(Object.keys(shiny.setters)).toEqual(["plaud_request"]);
    expect(Object.keys(shiny.handlers)).toEqual(["plaud_result"]);
    expect(plaud.lastLine).toBeNull();
  });
  it("each send makes a new random request_id", () => {
    draw();
    let a = "";
    let b = "";
    act(() => {
      a = plaud.send("keep-here", { recording_ids: ["20261023T090500+0000_0000beef"] });
    });
    act(() => {
      b = plaud.send("import", { all: true });
    });
    expect(a).toMatch(/^[0-9a-f]{32}$/);
    expect(b).not.toBe(a);
    expect(sent()).toEqual([
      { request_id: a, kind: "keep-here", plaud_ids: [], recording_ids: ["20261023T090500+0000_0000beef"], all: false },
      { request_id: b, kind: "import", plaud_ids: [], recording_ids: [], all: true },
    ]);
  });
  it("queued, running with progress, then done, with a toast", () => {
    draw();
    act(() => void plaud.send("import", { all: true }));
    const request = sent()[0];
    hear(reply(request));
    expect(plaud.results[request.request_id].state).toBe("queued");
    hear(reply(request, { state: "running", message: "Importing 5 of 31…", progress: { done: 5, total: 31 } }));
    expect(plaud.results[request.request_id].progress).toEqual({ done: 5, total: 31 });
    expect(sonner.toast).not.toHaveBeenCalled(); // progress is no toast
    hear(reply(request, { state: "done", message: "31 imported · 4 arrived private" }));
    expect(plaud.results[request.request_id].state).toBe("done");
    expect(plaud.last?.message).toBe("31 imported · 4 arrived private");
    expect(sonner.toast).toHaveBeenCalledOnce();
    expect(sonner.toast.mock.calls[0][1]).toEqual({ duration: Infinity, id: `plaud-${request.request_id}` });
  });
  it("an already-queued reply is shown as such", () => {
    draw();
    act(() => void plaud.send("sync"));
    const request = sent()[0];
    hear(reply(request, { message: "Already queued" }));
    expect(plaud.results[request.request_id]).toMatchObject({ state: "queued", message: "Already queued" });
    expect(sonner.toast).not.toHaveBeenCalled();
  });
  it("a refused reply keeps its message, and its toast says it", () => {
    draw();
    act(() => void plaud.send("import", { plaud_ids: ["of_00000000000000000000000000000001"] }));
    const request = sent()[0];
    hear(reply(request, { state: "refused", message: NOT_YET }));
    expect(plaud.results[request.request_id]).toMatchObject({ state: "refused", message: NOT_YET });
    expect((sonner.toast.mock.calls[0][0] as ReactElement<{ text: string[] }>).props.text).toEqual([NOT_YET]);
  });
  it("the kept result line is the server's, whatever the replies say", () => {
    // The server writes it in its own zone, and redraws it when one of this window's requests
    // ends: the browser never words one of its own (the council's M7).
    draw({ ...VIEW, last_result: "Keep it here, 14:20: Kept here" } as SyncView);
    expect(plaud.lastLine).toBe("Keep it here, 14:20: Kept here");
    act(() => void plaud.send("keep-here", { recording_ids: ["20261023T090500+0000_0000beef"] }));
    hear(reply(sent()[0], { state: "done", message: "Nothing to keep" }));
    expect(plaud.lastLine).toBe("Keep it here, 14:20: Kept here");
    draw({ ...VIEW, last_result: "Keep it here, 14:21: Nothing to keep" } as SyncView);
    expect(plaud.lastLine).toBe("Keep it here, 14:21: Nothing to keep");
  });
  it("a Release preview's answer is no toast: it is its question", () => {
    draw();
    act(() => void plaud.send("release-preview", { plaud_ids: ["of_00000000000000000000000000000005"] }));
    const request = sent()[0];
    hear(reply(request, { state: "done", message: "Preview ready" }));
    expect(plaud.results[request.request_id].state).toBe("done");
    expect(sonner.toast).not.toHaveBeenCalled();
  });
  it("a reply to another window's request changes nothing", () => {
    draw();
    const other = { request_id: "f".repeat(32), kind: "sync", plaud_ids: [], recording_ids: [], all: false } as PlaudRequest;
    hear(reply(other, { state: "done", message: "Synced: nothing new · All here" }));
    expect(plaud.results).toEqual({});
    expect(plaud.last).toBeNull();
  });
  it("is unavailable until the view says otherwise", () => {
    act(() => root.render(createElement(Probe, { view: undefined })));
    expect([plaud.available, plaud.why("keep-here"), plaud.handles("keep-here")]).toEqual([false, NO_PLAUD, false]);
    draw({ ...VIEW, available: false, reason: "Demo: nothing is synced" } as SyncView);
    expect([plaud.available, plaud.reason]).toEqual([false, "Demo: nothing is synced"]);
    expect(plaud.why("keep-here")).toBe("Demo: nothing is synced");
  });
  it("only a kind the worker handles may be sent: the rest say it comes next", () => {
    // Phase 1's worker handles Keep it here alone, and the gate's reason is there for the
    // selection bars, which have no Sync view of their own.
    draw();
    expect(plaud.why("keep-here")).toBeNull();
    expect(["compare", "sync", "resync", "import", "release"].map((k) => plaud.why(k as never))).toEqual(Array(5).fill(NOT_YET));
    expect(plaud.gate).toBe(GATE);
    expect([plaud.handles("keep-here"), plaud.handles("resync")]).toEqual([true, false]);
    expect(kindReason("resync", null, { handled: ["resync"], not_yet: NOT_YET })).toBeNull();
    expect(kindReason("resync", "Demo: nothing is synced", { handled: ["resync"], not_yet: NOT_YET })).toBe("Demo: nothing is synced");
  });
  it("an import's toast offers View, which opens Untagged", () => {
    const onView = vi.fn();
    draw(VIEW, onView);
    act(() => void plaud.send("import", { all: true }));
    hear(reply(sent()[0], { state: "done", message: "31 imported · 4 arrived private" }));
    const { undo } = (sonner.toast.mock.calls[0][0] as Toasted).props;
    expect(undo?.label).toBe("View");
    undo?.run();
    expect(onView).toHaveBeenCalledOnce();
    act(() => void plaud.send("keep-here", { recording_ids: ["20261023T090500+0000_0000beef"] }));
    hear(reply(sent()[1], { state: "done", message: "Kept here" }));
    expect((sonner.toast.mock.calls[1][0] as Toasted).props.undo).toBeNull(); // only an import's
  });
});
