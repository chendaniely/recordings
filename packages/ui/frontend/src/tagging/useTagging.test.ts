import { createElement, type ReactElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OPENING } from "../lib/library";
import type { Origin } from "../lib/queue";
import { droppedText, STILL_WORKING, textOf, type Text } from "../lib/toasts";
import type { LibraryRow, LibraryView, SnippetRequest, SnippetResult, TagRequest, TagResult } from "../types";

// The Shiny side, faked: each input's setter records what it sends; each message handler is kept
// so a test can deliver a reply (shinyreact keeps handlers in a ref, so the latest one is live).
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
const sonner = vi.hoisted(() => ({ toast: null as unknown as ReturnType<typeof vi.fn>, dismiss: null as unknown as ReturnType<typeof vi.fn> }));
vi.mock("sonner", () => {
  sonner.toast = vi.fn();
  sonner.dismiss = vi.fn();
  return { toast: Object.assign(sonner.toast, { dismiss: sonner.dismiss }) };
});

import { useTagging, type Tagging } from "./useTagging";

type Options = { id: string; onDismiss?: () => void };
type Props = { text?: Text; undo?: { label: string; run: () => void } | null; lines?: string[]; announced?: string; shown?: string };

const row = (id: string, tags: string[] = []): LibraryRow => ({
  id, title: `Week ${id}`, recorded_at: "2026-10-06T14:00:00-07:00", when: "", duration: null, duration_ms: null,
  kind: "audio", sources: [], tags: tags.map((tag) => ({ tag, by: "you" as const })), private: tags.includes("private"),
  untagged: tags.length === 0,
});
const library = (over: Partial<LibraryView> = {}): LibraryView => ({
  recordings: [row("a", ["talks"]), row("b"), row("c", ["private"])],
  counts: { all: 3, untagged: 1 },
  tags: [{ tag: "talks", count: 1, private: false }, { tag: "private", count: 1, private: true }],
  note_types: [], problems: [], can_tag: true, cannot_tag_reason: null, saves: true, ...over,
});
const result = (request: TagRequest, over: Partial<TagResult> = {}): TagResult => ({
  request_id: request.request_id, status: "done", changed: request.ids.length, unchanged: 0,
  done_so_far: request.ids.length, total: request.ids.length, loses_private: null, now_visible: 0,
  failures: [], undo: null, rows: [], counts: null, tags: null, note_types: null, message: null, ...over,
});
const many = (n: number) => Array.from({ length: n }, (_, i) => `r${i}`);
const picker: Origin = { kind: "picker" };
const cell: Origin = { kind: "cell" };

// The tests run React's production build (vite.config's define), which has no act(): every
// render is made synchronous with flushSync, which also runs the effects it commits.
const act = (fn: () => void) => flushSync(fn);

let tagging: Tagging;
function Probe({ view }: { view: LibraryView | undefined }) {
  tagging = useTagging(view);
  return null;
}

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const jq: Record<string, () => void> = {};
const draw = (view: LibraryView | undefined) => act(() => root.render(createElement(Probe, { view })));
const reply = (r: TagResult) => act(() => shiny.handlers.tag_result(r));
const sent = () => (shiny.sent.tag_request ?? []) as TagRequest[];
const last = () => sent()[sent().length - 1];
const toasts = () => sonner.toast.mock.calls as [ReactElement<Props>, Options][];
const toastsFor = (id: string) => toasts().filter(([, o]) => o.id === id);
const latest = (id: string) => toastsFor(id).at(-1);
const said = (id: string) => textOf(latest(id)?.[0].props.text ?? []);
const cannotTag = () => toasts().filter(([, o]) => o.id.startsWith("cannot-tag"));

beforeEach(() => {
  shiny.sent = {};
  sonner.toast.mockClear();
  sonner.dismiss.mockClear();
  (window as unknown as { jQuery: unknown }).jQuery = () => ({
    on: (event: string, fn: () => void) => { jq[event] = fn; },
    off: () => {},
  });
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(() => {
  root.unmount();
  host.remove();
  vi.useRealTimers();
});

describe("useTagging: inputs and the queue (spec §5, §6)", () => {
  it("mounts its inputs and handlers in App, and sends nothing on load", () => {
    draw(library());
    expect(shiny.sent).toEqual({});
    expect(Object.keys(shiny.handlers).sort()).toEqual(["snippets", "tag_result"]);
    expect(Object.keys(shiny.setters).sort()).toEqual(["refresh", "snippet_request", "tag_request"]);
  });
  it("sends one request at a time, shows both as pending, and merges the reply", () => {
    draw(library());
    act(() => {
      tagging.tag("add", ["a"], ["work"], picker);
      tagging.tag("add", ["b"], ["work"], picker);
    });
    expect(sent()).toHaveLength(1);
    expect(tagging.pending.map((r) => r.ids[0])).toEqual(["a", "b"]);
    reply(result(sent()[0], { undo: "t1", rows: [row("a", ["talks", "work"])] }));
    expect(sent()).toHaveLength(2);
    expect(tagging.view?.recordings[0].tags.map((t) => t.tag)).toEqual(["talks", "work"]);
    expect(tagging.marks.a).toEqual({ done: true });
    expect(tagging.touched).toBe(1);
    expect(tagging.lastTagged).toEqual({ tags: ["work"], ids: ["a"], key: 1, undo: "t1" }); // where it went
    expect(said(`tag-${sent()[0].request_id}`)).toBe("Added work to 1 recording");
  });
  it("shows a big request's progress when it is sent, not while it waits", () => {
    draw(library());
    act(() => {
      tagging.tag("add", many(60), ["w"], picker);
      tagging.tag("add", many(60), ["x"], picker);
    });
    const [first] = sent();
    expect(toasts().map(([, o]) => o.id)).toEqual([`tag-${first.request_id}`]);
    expect(latest(`tag-${first.request_id}`)?.[0].props.announced).toBe("Tagging 60 recordings…");
    reply(result(first, { status: "progress", done_so_far: 30 }));
    expect(latest(`tag-${first.request_id}`)?.[0].props.shown).toBe("Tagging 30 of 60…");
    reply(result(first, { undo: "t1" }));
    const second = sent()[1];
    expect(latest(`tag-${second.request_id}`)?.[0].props.shown).toBe("Tagging 0 of 60…");
  });
  it("shows no action toast when nothing changed and the failures speak", () => {
    draw(library());
    act(() => tagging.tag("add", many(3), ["w"], picker));
    const request = sent()[0];
    const failures = many(3).map((id) => ({ id, reason: "the recording is gone" }));
    reply(result(request, { changed: 0, failures }));
    expect(toastsFor(`tag-${request.request_id}`)).toHaveLength(0);
    expect(sonner.dismiss).toHaveBeenCalledWith(`tag-${request.request_id}`);
    expect(latest(`failed-${request.request_id}`)?.[0].props.lines).toEqual(["3 the recording is gone"]);
  });
});

describe("useTagging: the confirmation (spec §6)", () => {
  const confirmOf = (request: TagRequest) => result(request, {
    status: "confirm", changed: 0, done_so_far: 0, loses_private: { visible: 1, untagged: 0, ids: ["r1"] },
  });
  it("closes the progress toast, holds the queue, and Remove resends first with the IDs the server named", () => {
    draw(library());
    act(() => {
      tagging.tag("remove", many(60), ["private"], picker);
      tagging.tag("add", ["a"], ["w"], picker);
    });
    const first = sent()[0];
    reply(confirmOf(first));
    expect(sonner.dismiss).toHaveBeenCalledWith(`tag-${first.request_id}`);
    expect(tagging.confirm?.request).toBe(first);
    expect(sent()).toHaveLength(1); // the add waits behind the dialog
    expect(tagging.pending.map((r) => r.action)).toEqual(["add"]);
    act(() => tagging.answerConfirm(true));
    const resend = sent()[1];
    expect(resend).toMatchObject({ action: "remove", ids: many(60), tags: ["private"], confirm_ids: ["r1"] });
    expect(resend.request_id).not.toBe(first.request_id);
    expect(tagging.confirm).toBe(null);
    act(() => tagging.answerConfirm(false)); // Radix's close after Remove: only the first answer counts
    expect(sent()).toHaveLength(2);
  });
  it("Cancel applies nothing and lets the queue go on", () => {
    draw(library());
    act(() => {
      tagging.tag("remove", ["c"], ["private"], { kind: "header" });
      tagging.tag("add", ["a"], ["w"], picker);
    });
    reply(confirmOf(sent()[0]));
    act(() => tagging.answerConfirm(false));
    expect(sent()).toHaveLength(2);
    expect(sent()[1]).toMatchObject({ action: "add", ids: ["a"] });
  });
});

describe("useTagging: Undo (Task 9 carry)", () => {
  function twoActions(origin = picker) {
    act(() => tagging.tag("add", ["a"], ["one"], origin));
    reply(result(last(), { undo: "t1" }));
    act(() => tagging.tag("add", ["b"], ["two"], origin));
    reply(result(last(), { undo: "t2" }));
  }
  it("Cmd-Z peeks, a second press undoes the action before, and the same token is never queued twice", () => {
    draw(library());
    twoActions();
    act(() => tagging.undoLast());
    expect(last()).toMatchObject({ action: "undo", undo: "t2", ids: [], tags: [] });
    act(() => tagging.undoLast());
    act(() => tagging.undo("t2")); // the toast's Undo as well: already on its way
    expect(tagging.pending.map((r) => r.undo)).toEqual(["t2", "t1"]);
  });
  it("keeps a token whose Undo met a busy recording, and drops one the server used up", () => {
    draw(library());
    twoActions();
    act(() => tagging.undoLast());
    const busy = { id: "b", reason: "busy: another job holds this recording; try again" };
    reply(result(last(), { changed: 0, failures: [busy] }));
    act(() => tagging.undoLast());
    expect(last()).toMatchObject({ action: "undo", undo: "t2" }); // tried again
    expect(tagging.lastUndone).toBe(null); // a busy Undo reversed nothing
    reply(result(last(), { changed: 1, undo: "redo", rows: [row("b")] }));
    expect(tagging.revealed.ids).toEqual(["b"]);
    expect(tagging.lastUndone).toEqual({ token: "t2", key: 1 }); // which action an Undo reversed
    expect(latest(`tag-${last().request_id}`)?.[0].props.undo?.label).toBe("Redo");
    act(() => tagging.undoLast());
    expect(last()).toMatchObject({ action: "undo", undo: "t1" });
  });
  it("shares the grid's running toast with Cmd-Z, so Undo last never sends a used token", () => {
    draw(library());
    twoActions(cell);
    expect(toastsFor("tag-" + sent()[0].request_id)).toHaveLength(0); // merged, not one each
    expect(said("grid-cells-1")).toBe("2 changes");
    act(() => tagging.undoLast());
    expect(last()).toMatchObject({ action: "undo", undo: "t2" });
    reply(result(last(), { undo: "redo" }));
    expect(said("grid-cells-1")).toBe("1 change");
    act(() => latest("grid-cells-1")?.[0].props.undo?.run());
    expect(last()).toMatchObject({ action: "undo", undo: "t1" });
  });
  it("starts a new running toast once the old one has gone", () => {
    draw(library());
    act(() => tagging.tag("add", ["a"], ["one"], cell));
    reply(result(last(), { undo: "t1" }));
    act(() => latest("grid-cells-1")?.[1].onDismiss?.());
    act(() => tagging.tag("add", ["b"], ["two"], cell));
    reply(result(last(), { undo: "t2" }));
    expect(said("grid-cells-2")).toBe("1 change");
  });
});

describe("useTagging: lost connections and slow replies (spec §6, §8)", () => {
  it("drops the queue on a lost connection, closes the progress toast, and says so", () => {
    draw(library());
    act(() => {
      tagging.tag("add", many(60), ["w"], picker);
      tagging.tag("add", ["a"], ["x"], picker);
    });
    const first = sent()[0];
    act(() => jq["shiny:disconnected"]());
    expect(sonner.dismiss).toHaveBeenCalledWith(`tag-${first.request_id}`);
    expect(latest("connection-lost")?.[0].props.lines).toEqual([droppedText(1, true)]);
    expect(tagging.pending).toEqual([]);
    expect(tagging.canTag).toBe(false);
    act(() => tagging.tag("add", ["a"], ["y"], picker));
    expect(sent()).toHaveLength(1);
    expect(cannotTag().at(-1)?.[0].props.lines).toEqual(["Lost the connection. Reload to keep working."]);
  });
  it("says Still working after 30 seconds with no reply, in the progress toast's place, and never re-sends", () => {
    vi.useFakeTimers();
    draw(library());
    act(() => tagging.tag("add", ["a"], ["w"], picker));
    const request = sent()[0];
    act(() => vi.advanceTimersByTime(25_000));
    expect(latest(`tag-${request.request_id}`)).toBeUndefined();
    act(() => vi.advanceTimersByTime(10_000));
    expect(latest(`tag-${request.request_id}`)?.[0].props.announced).toBe(STILL_WORKING);
    expect(sent()).toHaveLength(1);
    reply(result(request, { undo: "t1" }));
    expect(said(`tag-${request.request_id}`)).toBe("Added w to 1 recording");
  });
});

describe("useTagging: the library (spec §5; Task 7 carry)", () => {
  it("treats the first draw's 'being opened' as passing, then takes the full view", () => {
    draw(library({ can_tag: false, cannot_tag_reason: OPENING }));
    expect(tagging).toMatchObject({ canTag: false, opening: true, reason: null, loadKey: 1 });
    act(() => tagging.tag("add", ["a"], ["w"], picker));
    expect(sent()).toHaveLength(0);
    // A moment, not tagging off: an ordinary toast that goes by itself, not an alert that stays.
    expect(textOf(cannotTag().at(-1)?.[0].props.text ?? [])).toBe("Opening the archive… try again in a moment.");
    expect(cannotTag().at(-1)?.[0].props.lines).toBeUndefined();
    expect(tagging.sessionTags).toEqual([]); // nothing was submitted
    draw(library());
    expect(tagging).toMatchObject({ canTag: true, opening: false, reason: null, loadKey: 2 });
  });
  it("a Refresh's full library replaces the merged view and its marks; Undo survives it", () => {
    draw(library());
    act(() => tagging.tag("add", ["b"], ["w"], picker));
    reply(result(last(), { undo: "t1", rows: [row("b", ["w"])] }));
    expect(tagging.marks.b).toEqual({ done: true });
    act(() => tagging.refresh());
    act(() => tagging.refresh());
    expect(shiny.sent.refresh).toEqual([1, 2]);
    draw(library());
    expect(tagging.marks).toEqual({});
    expect(tagging.view?.recordings[1].tags).toEqual([]);
    act(() => tagging.undoLast());
    expect(last()).toMatchObject({ action: "undo", undo: "t1" });
  });
  it("reads privacy only from the server's flags", () => {
    draw(library());
    expect(tagging.isPrivate("private")).toBe(true);
    expect(tagging.isPrivate("talks")).toBe(false);
    expect(tagging.isPrivate("Private/unknown")).toBe(false); // never guessed in the browser
  });
});

describe("useTagging: snippets (spec §5; Task 7 carry)", () => {
  const snippetsSent = () => (shiny.sent.snippet_request ?? []) as SnippetRequest[];
  const hear = (r: SnippetResult) => act(() => shiny.handlers.snippets(r));
  it("asks again for a batch that failed, at the next want, and keeps what arrives in memory", () => {
    draw(library());
    act(() => tagging.wantSnippets(["a", "b"]));
    expect(snippetsSent()).toHaveLength(1);
    expect(snippetsSent()[0].ids).toEqual(["a", "b"]);
    hear({ request_id: snippetsSent()[0].request_id, snippets: {}, message: "Snippets couldn't be read." });
    expect(snippetsSent()).toHaveLength(1);
    act(() => tagging.wantSnippets(["a", "b"]));
    expect(snippetsSent()[1].ids).toEqual(["a", "b"]);
    hear({ request_id: snippetsSent()[1].request_id, snippets: { a: "hello", b: null }, message: null });
    expect(tagging.snippet("a")).toBe("hello");
    expect(tagging.snippet("b")).toBe(null);
    expect(tagging.snippet("c")).toBeUndefined();
  });
});

describe("useTagging: Task 10 council fixes", () => {
  const confirmOf = (request: TagRequest) => result(request, {
    status: "confirm", changed: 0, done_so_far: 0, loses_private: { visible: 1, untagged: 0, ids: ["c"] },
  });
  it("folds a cell's add that makes recordings visible into the running toast, which adds them up", () => {
    // why: Task 14 fix round 1. One toast per row in Untagged only stacked 21 toasts for 20 rows.
    draw(library());
    act(() => tagging.tag("add", ["b"], ["work"], cell));
    const first = last();
    reply(result(first, { undo: "t1", now_visible: 1 }));
    act(() => tagging.tag("add", ["c"], ["work"], cell));
    reply(result(last(), { undo: "t2", now_visible: 1 }));
    expect(toastsFor(`tag-${first.request_id}`)).toHaveLength(0);
    expect(said("grid-cells-1")).toBe("2 changes · 2 now visible to external agents");
  });
  it("still gives a cell's removal that makes recordings visible a toast of its own", () => {
    draw(library());
    act(() => tagging.tag("remove", ["c"], ["private"], cell));
    const request = last();
    reply(result(request, { undo: "t1", now_visible: 1 }));
    expect(said(`tag-${request.request_id}`)).toBe("Removed private from 1 recording · 1 now visible to external agents");
    expect(toastsFor("grid-cells-1")).toHaveLength(0);
  });
  it("the running cell toast says the demo doesn't save, on Pages", () => {
    draw(library({ saves: false }));
    act(() => tagging.tag("add", ["a"], ["one"], cell));
    reply(result(last(), { undo: "t1" }));
    expect(said("grid-cells-1")).toBe("1 change · Demo: changes aren't saved");
  });
  it("Undo offers Redo; Redo says Redone, offers Undo, and Cmd-Z can then undo it again", () => {
    draw(library());
    act(() => tagging.tag("remove", ["a"], ["talks"], { kind: "header" }));
    reply(result(last(), { undo: "t1" }));
    act(() => latest(`tag-${last().request_id}`)?.[0].props.undo?.run());
    const undoRequest = last();
    reply(result(undoRequest, { changed: 1, undo: "r1", rows: [row("a", ["talks"])] })); // an Undo carries no ids
    expect(said(`tag-${undoRequest.request_id}`)).toBe("Undone on 1 recording");
    expect(latest(`tag-${undoRequest.request_id}`)?.[0].props.undo?.label).toBe("Redo");
    act(() => latest(`tag-${undoRequest.request_id}`)?.[0].props.undo?.run());
    const redo = last();
    expect(redo).toMatchObject({ action: "undo", undo: "r1" });
    reply(result(redo, { changed: 1, undo: "t1b", rows: [row("a")] }));
    expect(said(`tag-${redo.request_id}`)).toBe("Redone on 1 recording");
    expect(latest(`tag-${redo.request_id}`)?.[0].props.undo?.label).toBe("Undo");
    act(() => tagging.undoLast());
    expect(last()).toMatchObject({ action: "undo", undo: "t1b" });
  });
  it("a Redo that needs the confirmation says it is a Redo", () => {
    draw(library());
    act(() => tagging.tag("remove", ["c"], ["private"], { kind: "header" }));
    reply(confirmOf(last()));
    act(() => tagging.answerConfirm(true));
    reply(result(last(), { undo: "t1" }));
    act(() => tagging.undo("t1"));
    reply(result(last(), { undo: "r1" }));
    act(() => tagging.undo("r1"));
    reply(confirmOf(last()));
    expect(tagging.confirm?.redo).toBe(true);
  });
  it("says why it can't tag each time it's asked, in one alert: the same text reuses its toast", () => {
    draw(library({ can_tag: false, cannot_tag_reason: "this is the NAS mirror's copy" }));
    act(() => tagging.tag("add", ["a"], ["w"], picker));
    act(() => tagging.tag("add", ["a"], ["w"], picker));
    const [first, second] = cannotTag();
    expect(first[1].id).toBe(second[1].id); // updates in place: two identical alerts never stack
    expect(second[0].props.lines).toEqual(["this is the NAS mirror's copy"]);
    expect(tagging.sessionTags).toEqual([]);
  });
  it("words a big removal's progress as a removal, and never re-announces after Still working", () => {
    vi.useFakeTimers();
    draw(library());
    act(() => tagging.tag("remove", many(60), ["talks"], picker));
    const request = last();
    const id = `tag-${request.request_id}`;
    expect(latest(id)?.[0].props).toMatchObject({ announced: "Removing tags from 60 recordings…", shown: "Removing tags: 0 of 60…" });
    act(() => vi.advanceTimersByTime(35_000));
    expect(latest(id)?.[0].props).toMatchObject({ announced: STILL_WORKING, shown: STILL_WORKING });
    reply(result(request, { status: "progress", done_so_far: 40 }));
    // The count moves on screen; the live region keeps what it last said (start and end only).
    expect(latest(id)?.[0].props).toMatchObject({ announced: STILL_WORKING, shown: "Removing tags: 40 of 60…" });
  });
  it("says so when a confirmation names nothing, rather than dropping the change silently", () => {
    draw(library());
    act(() => tagging.tag("remove", ["c"], ["private"], picker));
    const request = last();
    reply(result(request, { status: "confirm", changed: 0, loses_private: null }));
    expect(tagging.confirm).toBe(null);
    expect(latest(`stop-${request.request_id}`)?.[0].props.lines).toEqual(["Nothing was changed: the confirmation named no recordings."]);
  });
});
