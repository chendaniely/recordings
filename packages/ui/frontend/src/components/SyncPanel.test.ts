import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PlaudKind, SyncPanel as Panel } from "../types";

vi.mock("../sr", () => ({ useSetShinyInput: () => () => {}, useShinyMessageHandler: () => {} }));

import { PlaudContext, type Plaud } from "../plaud/usePlaud";
import { SyncPanel } from "./SyncPanel";

const NOT_PINGED = "The sync's Healthchecks check isn't being pinged since 14:40: sign-in needed. It emails you about 2 hours after that.";
const NOT_YET = "Comes with the next part of stage 2b.";
const EVERY: PlaudKind[] = ["compare", "sync", "import", "resync", "full", "accept", "accept-changes",
  "release-preview", "release", "unrelease", "park", "unpark", "keep-here"];
/** A panel drawn from a fixture, with a worker that handles every kind, as Phase 3's will. */
const PANEL: Panel = {
  demo: false, no_sync_yet: false, handled: EVERY, not_yet: NOT_YET, listed: true,
  summary: { kind: "problem", sentence: "Sign-in needed", explain: null, see_status: true },
  failing: "Plaud sync failing since 14:40 (sign-in needed). Last good listing 14:20.",
  failing_checks: [],
  not_pinged: NOT_PINGED,
  conditions: ["Plaud sign-in needed since 14:40. New recordings wait on Plaud: nothing is lost. At the Mac: `make plaud-sign-in`."],
  hold: null, breaker: null,
  numbers: {
    equation: { listed: 9, here: 4, waiting: 2, excluded: 2, missing: 1, arrive_private: 1, listing_at: "14:20" },
    archive: { from_plaud: 4, here: 4, gone: 0, absent: 0 },
    consent: { ids: 14, on_plaud: 4, file_changed: "2026-10-23 09:12", read_at: "14:20", stop: null },
    last_good_listing: "14:20 (9 listed)", next_sync: null, sweep: null, sign_in: "sign-in needed since 14:40",
    full: null, held: 0, gone: 0, failing: 0, parked: 0, worker: "last reported 14:51 (idle)",
    healthchecks: "last ping failed 14:40 (HTTP 404)",
  },
  details: {
    normaliser: "1", fields: [], census: { "of_": 9 }, form_change: null, changes_24h: {}, named_share: null,
    runs: [{ at: "2026-10-23 14:40", result: "sign-in needed" }], keep_out_words: ["noarchive"], malformed: 0,
  },
  attention: [],
};

let sends: string[];
const fake = (over: Partial<Plaud> = {}): Plaud => {
  sends = [];
  return {
    send: (kind) => (sends.push(kind), "rid"), results: {}, last: null, lastLine: null, available: true, reason: null,
    why: () => null, handles: () => true, gate: null, ...over,
  };
};
/** The panel as HTML, its apostrophes as typed (React writes them as &#x27;). */
const markup = (panel: Panel, p: Plaud = fake()) =>
  renderToStaticMarkup(createElement(PlaudContext.Provider, { value: p }, createElement(SyncPanel, { panel })))
    .replaceAll("&#x27;", "'");

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const draw = (panel: Panel, p: Plaud) =>
  flushSync(() => root.render(createElement(PlaudContext.Provider, { value: p }, createElement(SyncPanel, { panel }))));
beforeEach(() => {
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(() => {
  root.unmount();
  host.remove();
});

describe("Status's sync panel (2b spec §15.2)", () => {
  it("sentences come first, then the numbers, with Details folded", () => {
    const html = markup(PANEL);
    const at = ["sync-summary", "sync-failing", "sync-not-pinged", "sync-conditions", "sync-numbers", "sync-details"]
      .map((t) => html.indexOf(`data-testid="${t}"`));
    expect(at.every((i) => i >= 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(html).toMatch(/<details class="sync-details" data-testid="sync-details">/); // no `open`
    expect(html).toContain("Listed 9 = 4 here + 2 waiting + 2 excluded + 1 missing · from the listing at 14:20");
    expect(html).toContain("14 IDs, 4 on Plaud now · file changed 2026-10-23 09:12 · read 14:20");
    expect(html).toContain('href="#/add">Open the Sync screen');
  });
  it("the not-pinged line names its cause", () => {
    // C6.06.
    expect(markup(PANEL)).toContain(NOT_PINGED);
  });
  it("a failed ping shows its time and HTTP status", () => {
    // C6.09.
    expect(markup(PANEL)).toContain("<dt>Healthchecks</dt><dd>last ping failed 14:40 (HTTP 404)</dd>");
  });
  it("links to the Sync screen for the gate and a missing listing, not for the demo or no sync yet", () => {
    // Compare and Keep it here live only on the Sync screen.
    const gate = { ...PANEL, summary: { kind: "no-sync" as const, sentence: "Waiting for the first catch-up", explain: null, see_status: true } };
    expect(markup(gate)).toContain('href="#/add">Open the Sync screen');
    expect(markup({ ...PANEL, demo: true })).not.toContain("Open the Sync screen");
    expect(markup({ ...PANEL, no_sync_yet: true })).not.toContain("Open the Sync screen");
  });
  it("draws no numbers and no Details when the sync's records couldn't be read", () => {
    const empty = { ...PANEL.details, normaliser: "", census: {}, runs: [], keep_out_words: [] };
    const html = markup({ ...PANEL, listed: false, details: empty, numbers: { ...PANEL.numbers, equation: null,
      archive: null, consent: null, last_good_listing: null, sign_in: null, worker: null, healthchecks: null } });
    expect(html).not.toContain("<dl");
    expect(html).not.toContain("Normaliser");
    expect(html).not.toContain("Keep-out words");
    expect(html).not.toContain("<details");
  });
  it("Accept waits for the gate as well as the worker", () => {
    const hold = { sentence: "Plaud sync paused.", accept: true, refused: null };
    const breaker = { sentence: "Edit-sync paused since 14:20", accept: true, code_fix: null, fields: ["folder_id"] };
    const closed = "Waiting for the first catch-up";
    draw({ ...PANEL, hold, breaker }, fake({ gate: closed }));
    expect([...host.querySelectorAll("button")].every((b) => b.hasAttribute("disabled"))).toBe(true);
    expect([...host.querySelectorAll(".sync-why")].map((p) => p.textContent)).toEqual([closed]);
    draw({ ...PANEL, hold, breaker }, fake({ gate: null }));
    expect([...host.querySelectorAll("button")].some((b) => b.hasAttribute("disabled"))).toBe(false);
  });
  it("no sync yet says so", () => {
    const html = markup({ ...PANEL, no_sync_yet: true, failing: null, not_pinged: null, conditions: [],
      summary: { kind: "no-sync", sentence: "No sync has run yet", explain: null, see_status: false } });
    expect(html).toContain("No sync has run yet");
    expect(html).not.toContain("sync-numbers");
    expect(html).not.toContain("Open the Sync screen");
  });
  it("Accept this listing and Accept these changes send their requests, or show why not", () => {
    const hold = { sentence: "Plaud sync paused: Plaud's IDs changed form, and 12 recordings here vanished with them.", accept: true, refused: null };
    const breaker = { sentence: "Edit-sync paused since 14:20", accept: true, code_fix: null, fields: ["folder_id"] };
    draw({ ...PANEL, hold, breaker }, fake());
    const buttons = [...host.querySelectorAll<HTMLButtonElement>("button")];
    buttons.forEach((b) => flushSync(() => b.click()));
    expect(sends).toEqual(["accept", "accept-changes"]);
    const refused = "Fix this first: 4 consent-listed or kept-out IDs vanished while Plaud's IDs changed (runbook)";
    const fix = "This keeps changing by itself: it needs a code fix";
    const html = markup({ ...PANEL, hold: { ...hold, accept: false, refused }, breaker: { ...breaker, accept: false, code_fix: fix } });
    expect(html).not.toContain("<button");
    expect(html).toContain(refused);
    expect(html).toContain(fix);
    // Where no request can be made, the buttons are off and say why.
    draw({ ...PANEL, hold, breaker }, fake({ available: false, reason: "Demo: nothing is synced" }));
    expect([...host.querySelectorAll("button")].every((b) => b.hasAttribute("disabled"))).toBe(true);
    expect(host.textContent).toContain("Demo: nothing is synced");
  });
  it("in Phase 1 the Accept buttons say they come with the next part", () => {
    // Phase 1's worker handles Keep it here alone: Accept is off, with the reason (G7).
    const hold = { sentence: "Plaud now lists 40 fewer of the recordings here (and 38 look new).", accept: true, refused: null };
    const breaker = { sentence: "Edit-sync paused since 14:20", accept: true, code_fix: null, fields: ["folder_id"] };
    draw({ ...PANEL, hold, breaker, handled: ["keep-here"] }, fake());
    expect([...host.querySelectorAll("button")].every((b) => b.hasAttribute("disabled"))).toBe(true);
    // Said once, and named by each button's aria-describedby (Task 21's lines).
    expect([...host.querySelectorAll(".sync-why")].map((p) => p.textContent)).toEqual([NOT_YET]);
    const ids = [...host.querySelectorAll("button")].map((b) => b.getAttribute("aria-describedby"));
    expect(ids.every((id) => id !== null && host.querySelector(`#${id}`)?.textContent === NOT_YET)).toBe(true);
  });
  it("before any listing it shows no zero counts", () => {
    // Held, gone, failing, parked and malformed rows wait for the phase that measures them (X5).
    const html = markup({ ...PANEL, listed: false, numbers: { ...PANEL.numbers, equation: null, archive: null,
      last_good_listing: null } });
    for (const label of ["Held for review", "Gone from Plaud", "Failing", "Parked", "Malformed listing rows"]) {
      expect(html).not.toContain(`<dt>${label}</dt>`);
    }
    expect(html).toContain("<dt>Plaud worker</dt>");
    // The Archive row waits for a listing too, as on the Sync screen.
    expect(markup({ ...PANEL, listed: false })).not.toContain("<dt>Archive</dt>");
    expect(markup(PANEL)).toContain("<dt>Archive</dt>");
    expect(markup(PANEL)).toContain("<dt>Held for review</dt><dd>0</dd>");
  });
  it("the demo says Plaud sync isn't part of the demo", () => {
    const html = markup({ ...PANEL, demo: true, failing: null, not_pinged: null, conditions: [],
      summary: { kind: "demo", sentence: "Demo: Plaud sync isn't part of the demo.", explain: null, see_status: false } });
    expect(html).toContain("Demo: Plaud sync isn't part of the demo.");
    expect(html).not.toContain("Open the Sync screen");
  });
  it("recordings that fail every check get their own line, with Re-sync and Park", () => {
    // Dan, Phase 1's Q&A §6 (§15.2, J4, J5): beside the summary and never hiding it, before the
    // other sentences; a count and the oldest date, never a title (P13).
    const failing_checks = [
      { recording_id: "20261020T090000-0700_0000beef", since: "2026-10-20" },
      { recording_id: "20261021T090000-0700_0000cafe", since: "2026-10-21" },
    ];
    const ids = failing_checks.map((f) => f.recording_id);
    const sent: { kind: PlaudKind; ids: string[] | undefined }[] = [];
    draw({ ...PANEL, failing_checks }, fake({ send: (kind, opts) => (sent.push({ kind, ids: opts?.recording_ids }), "rid") }));
    const line = host.querySelector('[data-testid="sync-failing-checks"]');
    expect(line?.querySelector("p")?.textContent).toBe("2 recordings fail every check (since 2026-10-20)");
    const buttons = [...(line?.querySelectorAll<HTMLButtonElement>("button") ?? [])];
    expect(buttons.map((b) => b.textContent)).toEqual(["Re-sync", "Park"]);
    buttons.forEach((b) => flushSync(() => b.click()));
    expect(sent).toEqual([{ kind: "resync", ids }, { kind: "park", ids }]);
    const html = markup({ ...PANEL, failing_checks });
    const at = ["sync-summary", "sync-failing-checks", "sync-failing", "sync-not-pinged"].map((t) => html.indexOf(`data-testid="${t}"`));
    expect(at.every((i) => i >= 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(markup({ ...PANEL, failing_checks: [failing_checks[1]] })).toContain("<p>1 recording fails every check (since 2026-10-21)</p>");
    expect(markup({ ...PANEL, failing_checks: [{ ...failing_checks[0], since: null }] })).toContain("<p>1 recording fails every check</p>");
  });
  it("draws no such line when no recording fails every check", () => {
    // All of Phase 1, and Phase 3 once every check passes.
    const html = markup(PANEL);
    expect(html).not.toContain("sync-failing-checks");
    expect(html).not.toContain("every check");
  });
  it("Re-sync and Park are off with the reason until the worker handles them, and wait for the gate", () => {
    const failing_checks = [{ recording_id: "20261020T090000-0700_0000beef", since: "2026-10-20" }];
    const line = () => host.querySelector('[data-testid="sync-failing-checks"]');
    const buttons = () => [...(line()?.querySelectorAll<HTMLButtonElement>("button") ?? [])];
    const reasons = () => [...(line()?.querySelectorAll(".sync-why") ?? [])].map((p) => p.textContent);
    draw({ ...PANEL, failing_checks, handled: ["keep-here"] }, fake());
    expect(buttons().length).toBe(2);
    expect(buttons().every((b) => b.hasAttribute("disabled"))).toBe(true);
    expect(reasons()).toEqual([NOT_YET]); // said once, named by each button's aria-describedby
    const described = buttons().map((b) => b.getAttribute("aria-describedby"));
    expect(described.every((id) => id !== null && host.querySelector(`#${id}`)?.textContent === NOT_YET)).toBe(true);
    const closed = "Waiting for the first catch-up";
    draw({ ...PANEL, failing_checks }, fake({ gate: closed }));
    expect(buttons().every((b) => b.hasAttribute("disabled"))).toBe(true);
    expect(reasons()).toEqual([closed]);
    draw({ ...PANEL, failing_checks }, fake({ available: false, reason: "Demo: nothing is synced" }));
    expect(buttons().every((b) => b.hasAttribute("disabled"))).toBe(true);
    expect(reasons()).toEqual(["Demo: nothing is synced"]);
    draw({ ...PANEL, failing_checks }, fake());
    expect(buttons().some((b) => b.hasAttribute("disabled"))).toBe(false);
    expect(reasons()).toEqual([]);
  });
  it("no title is drawn", () => {
    // P13. A title smuggled into a field the server never fills with one is still not drawn.
    const leaky = { ...PANEL, numbers: { ...PANEL.numbers, title: "Leaked synthetic title" } } as unknown as Panel;
    expect(markup(leaky)).not.toContain("Leaked synthetic title");
  });
});
