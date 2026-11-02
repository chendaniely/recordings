import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { KeptOutRow, PlaudKind, PlaudResult, SyncView } from "../types";

const shown = vi.hoisted(() => ({ view: null as unknown, error: null as unknown }));
vi.mock("../sr", () => ({
  useShinyOutputValue: () => shown.view,
  useShinyOutputError: () => shown.error,
  useSetShinyInput: () => () => {},
  useShinyMessageHandler: () => {},
}));

import { PlaudContext, type Plaud } from "../plaud/usePlaud";
import { SyncPage } from "./SyncPage";
import { CopyId } from "./SyncSummary";

const GATE = "Scheduled sync waits for the first catch-up (runbook)";
const NOT_YET = "Comes with the next part of stage 2b.";
const EVERY: PlaudKind[] = ["compare", "sync", "import", "resync", "full", "accept", "accept-changes", "release-preview",
  "release", "unrelease", "park", "unpark", "keep-here"];
const id = (k: number) => `of_${String(k).padStart(32, "0")}`; // synthetic
const copy = (k: number) => `- \`${id(k)}\` —`;

/** A Sync screen with something in every section (§15.1), and every button on: as the canned view
 * draws it, with a worker that handles every kind, as Phase 3's will. */
const VIEW: SyncView = {
  demo: false, available: true, reason: null, handled: EVERY, not_yet: NOT_YET, gate: null, listed: true,
  summary: { kind: "problem", sentence: "Sign-in needed", explain: null, see_status: true },
  flags: [{
    kind: "kept-out-late", recording_id: "20261023T140512-0700_0000beef", plaud_id: id(9), copy_id: copy(9),
    sentence: "Kept out by title, but already in the archive: take it out by hand (runbook)", keep_here: true,
    date: null, duration: null,
  }],
  hold: { sentence: "Plaud now lists 40 fewer of the recordings here (and 38 look new).", accept: true, refused: null },
  breaker: { sentence: "Edit-sync paused since 14:20: Plaud started sending something new on 12 recordings", accept: false,
    code_fix: "This keeps changing by itself: it needs a code fix", fields: ["folder_id"] },
  buttons: { sync: null, compare: null, full: null, import: null },
  full_estimate: "about 40 minutes",
  equation: { listed: 9, here: 4, waiting: 2, excluded: 2, missing: 1, arrive_private: 1, listing_at: "14:20" },
  archive: { from_plaud: 4, here: 4, gone: 0, absent: 0 },
  consent: { ids: 3, on_plaud: 1, file_changed: "2026-10-23 09:12", read_at: "14:20", stop: null },
  titles_hidden: null,
  not_here: [
    { plaud_id: id(1), copy_id: copy(1), date: "2026-10-23", time: "09:05", duration: "47 min", title: "Synthetic 042",
      arrives_private: true, state: "imports at the next sync", review: false, park: false },
    { plaud_id: id(2), copy_id: copy(2), date: "2026-10-21", time: "11:15", duration: "3 min", title: "Synthetic 043",
      arrives_private: false, state: "failed 3 times", review: false, park: true },
    { plaud_id: id(3), copy_id: copy(3), date: "2026-10-20", time: "08:00", duration: "9 min", title: null,
      arrives_private: false, state: "needs your review: the sync's records were rebuilt", review: true, park: false },
  ],
  import_all: { count: 31, review_more: 6 },
  kept_out: [
    { plaud_id: id(4), copy_id: copy(4), date: "2026-10-20", time: "10:00", duration: "25 min", by: "consent", release: false, released: null },
    { plaud_id: id(5), copy_id: copy(5), date: "2026-10-23", time: "14:05", duration: "12 min", by: "title", release: true, released: null },
    { plaud_id: id(6), copy_id: copy(6), date: "2026-10-18", time: "17:00", duration: "4 min", by: "was-title", release: false,
      released: "Released · comes in at the next sync (14:40)" },
  ],
  updated: [{ recording_id: "20261022T090000-0700_0000cafe", title: "Week 3", parts: ["speakers"], at: "13:55" }],
  gone: { rows: [{ recording_id: "20261001T090000-0700_0000dead", since: "2026-10-20" }], last_7_days: 1 },
  parked: [{ id: "20261002T090000-0700_0000f00d", kind: "recording" }, { id: id(7), kind: "plaud" }],
  last_result: null, next_sync: "14:40",
};

let plaud: Plaud;
let sends: [string, unknown][];
const fake = (over: Partial<Plaud> = {}): Plaud => {
  sends = [];
  return {
    send: (kind, opts) => {
      sends.push([kind, opts]);
      return `rid-${sends.length}`;
    },
    results: {}, last: null, lastLine: null, available: true, reason: null, why: () => null, handles: () => true,
    gate: null, ...over,
  };
};
const page = (p: Plaud) => createElement(PlaudContext.Provider, { value: p }, createElement(SyncPage));
const markup = (view: SyncView, p: Plaud = fake()) => {
  shown.view = view;
  return renderToStaticMarkup(page(p));
};

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const draw = (view: SyncView, p: Plaud) => {
  shown.view = view;
  plaud = p;
  flushSync(() => root.render(page(p)));
};
const $ = (sel: string) => document.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...document.querySelectorAll<HTMLElement>(sel)];
const button = (scope: string, name: string) => $$(`${scope} button`).find((b) => b.textContent?.trim() === name)!;
const click = (el: HTMLElement) => flushSync(() => el.click());
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const sectionsSaying = (text: string) => $$(".sync-why").filter((p) => p.textContent === text).map((p) => p.closest("section")!.id);
const preview = (next: string | null): PlaudResult => ({
  request_id: "rid-1", kind: "release-preview", state: "done", message: "Preview ready", progress: null, result: null,
  preview: { plaud_id: id(5), title: "Coffee chat synthetic", date: "2026-10-23", time: "14:05", duration: "12 min", next_sync: next },
});

beforeAll(() => {
  Element.prototype.scrollIntoView ??= function scrollIntoView() {};
});
beforeEach(() => {
  shown.error = null;
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(() => {
  root.unmount();
  host.remove();
});

describe("the Sync screen (2b spec §15.1)", () => {
  it("the sections render in the spec's order", () => {
    const html = markup(VIEW);
    const order = ["sync-summary", "sync-flags", "sync-hold", "sync-breaker", "sync-actions", "sync-equations",
      "sync-not-here", "sync-kept-out", "sync-updated", "sync-gone", "sync-parked", "sync-own-audio"]
      .map((t) => html.indexOf(`data-testid="${t}"`));
    expect(order.every((at) => at >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    expect(html).toContain("Sign-in needed");
    expect(html).toContain('href="#/status">See Status');
  });

  it("in Phase 1 only Keep it here is live: every other button says it comes next", () => {
    // The worker handles Keep it here alone (the council's G7): a press of any other button
    // would come back an error, so each is off, with the reason said once in its section.
    draw({ ...VIEW, handled: ["keep-here"] }, fake());
    const live = $$("[data-request]").filter((b) => !b.hasAttribute("disabled")).map((b) => b.dataset.request);
    expect(live).toEqual(["keep-here"]);
    expect(sectionsSaying(NOT_YET)).toEqual(["sync-held", "sync-actions", "sync-not-here", "sync-kept-out", "sync-parked"]);
    expect($("[data-request=compare]")!.getAttribute("aria-describedby")).toBe($("#sync-actions .sync-why")!.id);
    click(button("[data-testid=sync-flags]", "Keep it here"));
    expect(sends).toEqual([["keep-here", { recording_ids: ["20261023T140512-0700_0000beef"] }]]);
  });

  it("with the gate closed only Compare and Keep it here are enabled, and the others say why", () => {
    draw({ ...VIEW, hold: null, breaker: null, gate: GATE, buttons: { sync: GATE, compare: null, full: GATE, import: GATE } }, fake());
    expect($("[data-request=sync]")!.hasAttribute("disabled")).toBe(true);
    expect($("[data-request=compare]")!.hasAttribute("disabled")).toBe(false);
    expect($("[data-request=keep-here]")!.hasAttribute("disabled")).toBe(false);
    expect($("[data-request=full]")!.hasAttribute("disabled")).toBe(true);
    expect($("[data-testid=import-selected]")!.hasAttribute("disabled")).toBe(true);
    expect($("[data-testid=import-all]")!.hasAttribute("disabled")).toBe(true);
    expect($$("[data-request=park], [data-request=unpark], [data-request=release-preview], [data-request=unrelease]")
      .every((b) => b.hasAttribute("disabled"))).toBe(true);
    expect(sectionsSaying(GATE)).toEqual(["sync-actions", "sync-not-here", "sync-kept-out", "sync-parked"]);
    expect($("[data-request=sync]")!.getAttribute("aria-describedby")).toBe($("#sync-actions .sync-why")!.id);
  });

  it("under a hold the import buttons are disabled with the hold's line", () => {
    const held = VIEW.hold!.sentence;
    draw({ ...VIEW, buttons: { sync: null, compare: null, full: null, import: held } }, fake());
    expect($("[data-testid=import-all]")!.hasAttribute("disabled")).toBe(true);
    expect($("[data-request=sync]")!.hasAttribute("disabled")).toBe(false);
    expect($$("#sync-not-here .sync-why").map((p) => p.textContent)).toEqual([held]);
    click(button("[data-testid=sync-hold]", "Accept this listing"));
    expect(sends).toEqual([["accept", undefined]]);
    // A breaker that needs a code fix offers no Accept, and says why.
    expect($("[data-request=accept-changes]")).toBeNull();
    expect($("[data-testid=sync-breaker]")!.textContent).toContain("it needs a code fix");
  });

  it("under a hold the server turns Sync now off too, and each section says why once", () => {
    // Area A5's handoff: the worker refuses Sync now and the imports during a hold ("Held: see
    // Status"), so the view gives both the hold's reason; Compare stays on.
    const why = "Held: Plaud's listing is held (see Held, above)";
    draw({ ...VIEW, buttons: { sync: why, compare: null, full: why, import: why } }, fake());
    expect($("[data-request=sync]")!.hasAttribute("disabled")).toBe(true);
    expect($("[data-request=compare]")!.hasAttribute("disabled")).toBe(false);
    expect($("[data-testid=import-all]")!.hasAttribute("disabled")).toBe(true);
    expect($$("#sync-actions .sync-why").map((p) => p.textContent)).toEqual([why]);
    expect($$("#sync-not-here .sync-why").map((p) => p.textContent)).toEqual([why]);
  });

  it("kept-out and flag rows show no title", () => {
    // C9.02. A title smuggled into a row the server should never send one in is still not drawn.
    const leaky = { ...VIEW.kept_out[1], title: "Leaked synthetic title" } as KeptOutRow;
    const html = markup({ ...VIEW, kept_out: [leaky] });
    expect(html).not.toContain("Leaked synthetic title");
    const kept = html.slice(html.indexOf('data-testid="sync-kept-out"'), html.indexOf('data-testid="sync-updated"'));
    const flags = html.slice(html.indexOf('data-testid="sync-flags"'), html.indexOf('data-testid="sync-held"'));
    expect(kept).not.toMatch(/Synthetic 04/);
    expect(flags).not.toMatch(/Synthetic 04/);
    expect(kept).toContain(id(5));
    expect(kept).toContain("2026-10-23 14:05 · 12 min");
  });

  it("Release asks first, with the title from its preview, and Let it in sends release", () => {
    // C5.06, C9.03, from a fixture: Phase 2's worker builds the preview.
    const p = fake();
    draw(VIEW, p);
    click(button("[data-testid=sync-kept-out]", "Release"));
    expect(sends).toEqual([["release-preview", { plaud_ids: [id(5)] }]]);
    expect($("[data-testid=release-dialog]")).toBeNull(); // nothing to ask until the preview comes
    draw(VIEW, { ...p, results: { "rid-1": preview("14:40") } });
    const dialog = $("[data-testid=release-dialog]")!;
    expect(dialog.textContent).toContain("Let 'Coffee chat synthetic' (2026-10-23 14:05, 12 min) in?");
    expect(dialog.textContent).toContain("It downloads at the next sync (14:40), and then stays in the backups.");
    expect($("[data-testid=sync-kept-out]")!.textContent).not.toContain("Coffee chat synthetic"); // only in the question
    click(button("[data-testid=release-dialog]", "Let it in"));
    expect(sends).toEqual([["release-preview", { plaud_ids: [id(5)] }], ["release", { plaud_ids: [id(5)] }]]);
    expect($("[data-testid=release-dialog]")).toBeNull();
  });

  it("Release's Cancel sends nothing, and gives focus back to its Release button", async () => {
    const p = fake();
    draw(VIEW, p);
    const release = button("[data-testid=sync-kept-out]", "Release");
    release.focus();
    click(release);
    draw(VIEW, { ...p, results: { "rid-1": preview(null) } });
    await tick();
    click(button("[data-testid=release-dialog]", "Cancel"));
    await tick(); // Radix's FocusScope hands focus on after a 0 ms timer
    expect(sends).toEqual([["release-preview", { plaud_ids: [id(5)] }]]);
    expect($("[data-testid=release-dialog]")).toBeNull();
    expect(document.activeElement).toBe(button("[data-testid=sync-kept-out]", "Release"));
  });

  it("a released row reads Released · comes in at the next sync · Undo, and Undo sends unrelease", () => {
    draw(VIEW, fake());
    const row = $$("[data-testid=kept-out-row]").find((r) => r.textContent?.includes(id(6)))!;
    expect(row.textContent).toContain("Released · comes in at the next sync (14:40) · Undo");
    click([...row.querySelectorAll<HTMLElement>("button")].find((b) => b.textContent === "Undo")!);
    expect(sends).toEqual([["unrelease", { plaud_ids: [id(6)] }]]);
  });

  it("Park and Unpark send their requests", () => {
    // C9.04.
    draw(VIEW, fake());
    click(button("[data-testid=sync-not-here]", "Park"));
    const unparks = $$("[data-testid=parked-row] button");
    unparks.forEach(click);
    expect(sends).toEqual([
      ["park", { plaud_ids: [id(2)] }],
      ["unpark", { recording_ids: ["20261002T090000-0700_0000f00d"] }],
      ["unpark", { plaud_ids: [id(7)] }],
    ]);
  });

  it("Import all shows its count and leaves the review rows out; Import selected takes them", () => {
    draw(VIEW, fake());
    expect($("[data-testid=import-all]")!.textContent).toBe("Import all 31");
    expect($("#sync-not-here")!.textContent).toContain("· 6 more need your review");
    click($("[data-testid=import-all]")!);
    // A review row has its checkbox (§11.6: Import selected brings it in), and Show title.
    const review = $$("[data-testid=not-here-row]")[2];
    expect(review.textContent).toContain("title on demand");
    click(review.querySelector<HTMLInputElement>("input[type=checkbox]")!);
    click($$("[data-testid=not-here-row]")[0].querySelector<HTMLInputElement>("input[type=checkbox]")!);
    expect($("[data-testid=import-selected]")!.textContent).toBe("Import selected (2)");
    click($("[data-testid=import-selected]")!);
    expect(sends).toEqual([["import", { all: true }], ["import", { plaud_ids: [id(3), id(1)] }]]);
    // Sent: the ticks clear, so the count never names rows that went.
    expect($("[data-testid=import-selected]")!.textContent).toBe("Import selected");
  });

  it("a ticked row the next redraw drops leaves the count", () => {
    const p = fake();
    draw(VIEW, p);
    click($$("[data-testid=not-here-row]")[0].querySelector<HTMLInputElement>("input[type=checkbox]")!);
    click($$("[data-testid=not-here-row]")[1].querySelector<HTMLInputElement>("input[type=checkbox]")!);
    expect($("[data-testid=import-selected]")!.textContent).toBe("Import selected (2)");
    draw({ ...VIEW, not_here: VIEW.not_here.slice(1) }, p); // id(1) came in
    expect($("[data-testid=import-selected]")!.textContent).toBe("Import selected (1)");
    click($("[data-testid=import-selected]")!);
    expect(sends).toEqual([["import", { plaud_ids: [id(2)] }]]);
  });

  it("Keep it here sends keep-here", () => {
    draw(VIEW, fake());
    click(button("[data-testid=sync-flags]", "Keep it here"));
    expect(sends).toEqual([["keep-here", { recording_ids: ["20261023T140512-0700_0000beef"] }]]);
  });

  it("Keep it here answers in its row: queued, the worker-down line, then Kept here (P6)", () => {
    const reply = (state: PlaudResult["state"], message: string): PlaudResult => ({
      request_id: "rid-1", kind: "keep-here", state, message, progress: null, result: null, preview: null,
    });
    const p = fake();
    draw(VIEW, p);
    const said = () => $("[data-testid=sync-flags] [data-testid=keep-reply]")!.textContent;
    expect(said()).toBe(""); // a live region exists before anything is said
    click(button("[data-testid=sync-flags]", "Keep it here"));
    draw(VIEW, { ...p, results: { "rid-1": reply("queued", "Queued") } });
    expect(said()).toBe("Queued");
    const down = "The Plaud worker isn't running: it will be done within the hour";
    draw(VIEW, { ...p, results: { "rid-1": reply("queued", down) } });
    expect(said()).toBe(down);
    draw(VIEW, { ...p, results: { "rid-1": reply("done", "Kept here") } });
    expect(said()).toBe("Kept here");
    expect($("[data-testid=keep-reply]")!.getAttribute("role")).toBe("status");
  });

  it("the newest result line stays after the toast", () => {
    draw(VIEW, fake({ lastLine: "Import all, 14:20: 31 imported · 4 arrived private" }));
    expect($("[data-testid=sync-last-result]")!.textContent).toBe("Import all, 14:20: 31 imported · 4 arrived private");
  });

  it("with no way to ask, every request button is off and Copy ID still works", () => {
    const off = "Demo: nothing is synced";
    draw({ ...VIEW, available: false, reason: off, buttons: { sync: off, compare: off, full: off, import: off } },
      fake({ available: false, reason: off }));
    expect($$("[data-request]").filter((b) => !b.hasAttribute("disabled"))).toEqual([]);
    expect($$("[data-testid=copy-id]").every((b) => !b.hasAttribute("disabled"))).toBe(true);
  });

  it("before any listing it shows no counts: one line, the consent list and the flags", () => {
    // Phase 1 lists nothing: no equation, no "Nothing is waiting", no "Import all 0", no "Gone
    // from Plaud: 0", no "Nothing is kept out" (the council's X5).
    const html = markup({ ...VIEW, listed: false, equation: null, archive: null, not_here: [], kept_out: [],
      gone: { rows: [], last_7_days: 0 }, parked: [], import_all: { count: 0, review_more: 0 } });
    expect(html).toContain('data-testid="sync-no-listing">No sync has run yet.</p>');
    // The gate's reason is kind "no-sync" too (Task 19): the line still shows under it.
    const gated = markup({ ...VIEW, listed: false, equation: null,
      summary: { kind: "no-sync", sentence: "Scheduled sync waits for the first catch-up (runbook)", explain: null, see_status: true } });
    expect(gated).toContain('data-testid="sync-no-listing">No sync has run yet.</p>');
    expect(html).toContain("Consent list: 3 IDs");
    expect(html).toContain('data-testid="sync-flags"');
    for (const gone of ["sync-not-here", "sync-kept-out", "sync-gone", "sync-parked"]) {
      expect(html).not.toContain(`data-testid="${gone}"`);
    }
    for (const said of ["Listed ", "Nothing is waiting", "Import all", "Gone from Plaud:", "Nothing is kept out", "Compare reads one"]) {
      expect(html).not.toContain(said);
    }
    // Once listed, an empty listing says so, and "Compare reads one" only while Compare is live.
    const listed = markup({ ...VIEW, equation: null, not_here: [], kept_out: [] });
    expect(listed).toContain("Nothing is waiting.");
    expect(listed).toContain("No listing yet: Compare reads one.");
    expect(markup({ ...VIEW, equation: null, handled: ["keep-here"] })).not.toContain("Compare reads one");
  });

  it("a flag whose sentence names its recording isn't prefixed with the ID again", () => {
    const rid = "20261023T140512-0700_0000beef";
    const io = `Recording ${rid} should be private, but its file couldn't be written (OSError): check the archive disk and its permissions.`;
    draw({ ...VIEW, flags: [{ ...VIEW.flags[0], kind: "private-unwritten", sentence: io, keep_here: false }] }, fake());
    expect($("[data-testid=flag-row]")!.textContent!.split(rid)).toHaveLength(2); // the ID once
  });

  it("Updated says nothing changed only once a check ran this week, and never before", () => {
    const none = markup({ ...VIEW, updated: [] });
    expect(none).not.toContain('data-testid="sync-updated"');
    expect(none).not.toContain("Nothing changed on Plaud");
    const quiet = markup({ ...VIEW, updated: [], checked_week: true });
    expect(quiet).toContain('data-testid="sync-updated"');
    expect(quiet).toContain("Nothing changed on Plaud this week.");
    expect(markup(VIEW)).toContain('data-testid="sync-updated"');
    expect(markup({ ...VIEW, checked_week: true })).not.toContain("Nothing changed on Plaud");
  });

  it("a breaker's note says what to know meanwhile, beside its fields", () => {
    const note = "New recordings still arrive.";
    draw({ ...VIEW, breaker: { ...VIEW.breaker!, note } }, fake());
    const line = $("[data-testid=sync-breaker]")!;
    expect(line.textContent).toContain("folder_id");
    expect($("[data-testid=sync-breaker-note]")!.textContent).toBe(note);
  });

  it("a hold and a breaker with one reason say it once, and both Accept buttons name it", () => {
    draw({ ...VIEW, handled: ["keep-here"], breaker: { ...VIEW.breaker!, accept: true, code_fix: null } }, fake());
    const said = $$("#sync-held .sync-why").filter((p) => p.textContent === NOT_YET);
    expect(said).toHaveLength(1);
    for (const kind of ["accept", "accept-changes"]) {
      const accept = $(`[data-request=${kind}]`)!;
      expect(accept.hasAttribute("disabled")).toBe(true);
      expect(accept.getAttribute("aria-describedby")).toBe(said[0].id);
    }
  });

  it("focus doesn't fall to the page when the flag it was on goes, or when Import selected turns itself off", () => {
    const p = fake();
    draw(VIEW, p);
    button("[data-testid=sync-flags]", "Keep it here").focus();
    draw({ ...VIEW, flags: [] }, p); // the redraw drops the flag
    expect(document.activeElement).toBe($("#sync-page-h"));
    click($$("[data-testid=not-here-row]")[0].querySelector<HTMLInputElement>("input[type=checkbox]")!);
    const selected = $("[data-testid=import-selected]")!;
    selected.focus();
    click(selected); // sends, clears the ticks, and is off
    expect(selected.hasAttribute("disabled")).toBe(true);
    expect(document.activeElement).toBe($("#sync-not-here-h"));
  });

  it("Not here yet shows no title for a review row, nor any while titles are hidden", () => {
    // P13: the server sends none, and a title sent anyway is still not drawn.
    const leaky = VIEW.not_here.map((r) => (r.review ? { ...r, title: "Leaked review title" } : r));
    expect(markup({ ...VIEW, not_here: leaky })).not.toContain("Leaked review title");
    expect(markup({ ...VIEW, not_here: leaky })).toContain("Synthetic 042"); // a ready row's title shows
    const hidden = markup({ ...VIEW, titles_hidden: "titles hidden until the consent list reads" });
    expect(hidden).not.toContain("Synthetic 042");
    expect(hidden).toContain("Titles hidden until the consent list reads.");
  });

  it("Gone from Plaud points to the steps for taking a recording out", () => {
    expect(markup(VIEW)).toContain("To take one out of the archive: &quot;Taking a recording out by hand&quot; in the runbook.");
  });
});

describe("Copy ID on a page served over plain HTTP (2b spec §15.1)", () => {
  // jsdom, like a page that isn't a secure context, may have no navigator.clipboard: Copy ID
  // falls back to execCommand("copy") on a selected textarea, and else shows the text to select.
  const real = { clipboard: Object.getOwnPropertyDescriptor(navigator, "clipboard"), exec: document.execCommand };
  const setClipboard = (value: unknown) => Object.defineProperty(navigator, "clipboard", { value, configurable: true });
  afterEach(() => {
    if (real.clipboard) Object.defineProperty(navigator, "clipboard", real.clipboard);
    else delete (navigator as { clipboard?: unknown }).clipboard;
    document.execCommand = real.exec;
  });
  const copyIt = async () => {
    flushSync(() => root.render(createElement(CopyId, { text: copy(5), label: id(5) })));
    const copyButton = $("[data-testid=copy-id]")!;
    copyButton.focus();
    click(copyButton);
    await tick();
    await tick();
    return copyButton;
  };

  it("with no Clipboard API it copies through a selected textarea, then puts focus back", async () => {
    setClipboard(undefined);
    let copied: string | undefined;
    let field: { from: number; to: number; size: string } | null | undefined;
    document.execCommand = vi.fn(() => {
      const area = document.querySelector("textarea");
      copied = area?.value;
      field = area && { from: area.selectionStart, to: area.selectionEnd, size: area.style.fontSize };
      return true;
    });
    const copyButton = await copyIt();
    expect(document.execCommand).toHaveBeenCalledWith("copy");
    expect(copied).toBe(copy(5));
    expect(field).toEqual({ from: 0, to: copy(5).length, size: "16px" }); // all selected (iOS); no zoom into it
    expect(document.querySelector("textarea")).toBeNull(); // the textarea goes again
    expect(copyButton.textContent).toBe("Copied");
    expect(document.activeElement).toBe(copyButton);
  });

  it("when nothing can copy, it shows the text to select by hand", async () => {
    setClipboard(undefined);
    document.execCommand = vi.fn(() => false);
    const copyButton = await copyIt();
    expect(copyButton.textContent).toBe("Copy ID");
    expect($("[data-testid=copy-failed]")!.textContent).toBe(`Copy failed: select it ${copy(5)}`);
  });

  it("uses the Clipboard API where the page may", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    setClipboard({ writeText });
    document.execCommand = vi.fn(() => true);
    const copyButton = await copyIt();
    expect(writeText).toHaveBeenCalledWith(copy(5));
    expect(document.execCommand).not.toHaveBeenCalled();
    expect(copyButton.textContent).toBe("Copied");
    // The button's name is fixed, so a polite live region says it.
    expect($("[data-testid=copy-status]")!.getAttribute("aria-live")).toBe("polite");
    expect($("[data-testid=copy-status]")!.textContent).toBe("Copied");
  });
});
