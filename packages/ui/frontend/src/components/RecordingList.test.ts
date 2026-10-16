import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NO_SEL } from "../lib/selection";
import { TaggingContext, type Tagging } from "../tagging/useTagging";
import type { LibraryRow } from "../types";

const toasts = vi.hoisted(() => ({ showCannotTag: vi.fn() }));
vi.mock("../tagging/toasts", () => toasts);
vi.mock("../sr", () => ({})); // useTagging's Shiny side: the list only reads the context

import { KEY_HINT, RecordingList, openPickerFor } from "./RecordingList";

const row = (id: string): LibraryRow => ({
  id, title: `Week ${id}`, recorded_at: "2026-10-06T14:00:00-07:00", when: "", duration: null, duration_ms: null,
  kind: "audio", sources: [], tags: [], private: false, untagged: true,
});
const rows = [row("a"), row("b"), row("c")];

/** The fields of Tagging the list reads; the rest are never touched here. */
const fake = (over: Partial<Tagging> = {}): Tagging => ({
  view: null, loadKey: 1, marks: {}, pending: [], confirm: null, revealed: { key: 0, ids: [] }, touched: 0,
  lastTagged: null, sessionTags: [], drag: { current: null }, canTag: true, opening: false, reason: null, saves: true,
  isPrivate: () => false, tag: vi.fn(), undo: vi.fn(), undoLast: vi.fn(), refresh: vi.fn(), answerConfirm: vi.fn(),
  picker: null, openPicker: vi.fn(), closePicker: vi.fn(), snippet: () => undefined, wantSnippets: vi.fn(),
  ...over,
});

const draw = (tagging: Tagging, list = rows) =>
  renderToStaticMarkup(
    createElement(
      TaggingContext.Provider,
      { value: tagging },
      createElement(RecordingList, { rows: list, sel: NO_SEL, act: () => {}, openId: null, onOpen: () => {}, announce: "" }),
    ),
  );

beforeEach(() => toasts.showCannotTag.mockClear());

describe("openPickerFor: T, the selection bar and a drop (spec §6)", () => {
  it("opens the picker's dialog form, titled for its targets", () => {
    const tagging = fake();
    openPickerFor(["a", "c"], rows, tagging);
    expect(tagging.openPicker).toHaveBeenCalledWith({ ids: ["a", "c"], title: "Tag 2 recordings" });
    openPickerFor(["b"], rows, tagging);
    expect(tagging.openPicker).toHaveBeenLastCalledWith({ ids: ["b"], title: "Tag Week b" });
  });
  it("does nothing with no targets", () => {
    const tagging = fake();
    openPickerFor([], rows, tagging);
    expect(tagging.openPicker).not.toHaveBeenCalled();
    expect(toasts.showCannotTag).not.toHaveBeenCalled();
  });
  it("says why when tagging is off, or still opening, instead of opening the picker", () => {
    const off = fake({ canTag: false, reason: "/Volumes/Mirror/a/very/long/path is read-only" });
    openPickerFor(["a"], rows, off);
    expect(off.openPicker).not.toHaveBeenCalled();
    expect(toasts.showCannotTag).toHaveBeenLastCalledWith({ canTag: false, opening: false, reason: off.reason });
    const opening = fake({ canTag: false, opening: true });
    openPickerFor(["a"], rows, opening);
    expect(toasts.showCannotTag).toHaveBeenLastCalledWith({ canTag: false, opening: true, reason: null });
  });
});

describe("the list's header (spec §6)", () => {
  it("is an ARIA grid counting every row in the filter and the header", () => {
    const html = draw(fake());
    expect(html).toContain('role="grid"');
    expect(html).toContain('aria-multiselectable="true"');
    expect(html).toContain('aria-rowcount="4"');
    expect(html).toContain("3 recordings");
  });
  it("shows the one-line key hint and Refresh, with how many rows were tagged", () => {
    expect(KEY_HINT).toBe("T tag · Cmd-A all · Esc clear · Cmd-Z undo · R refresh");
    expect(draw(fake())).toContain(KEY_HINT);
    expect(draw(fake())).toMatch(/data-testid="refresh"[^>]*>Refresh</);
    expect(draw(fake({ touched: 5 }))).toContain("5 tagged · Refresh");
  });
  it("says once why tagging is off, and that the archive is being opened while it is", () => {
    const off = draw(fake({ canTag: false, reason: "/Volumes/Mirror/archive is read-only" }));
    expect(off).toMatch(/data-testid="cannot-tag"[^>]*>Tagging is off: \/Volumes\/Mirror\/archive is read-only</);
    const opening = draw(fake({ canTag: false, opening: true }));
    expect(opening).toMatch(/data-testid="cannot-tag"[^>]*>Opening the archive…</);
    expect(opening).not.toContain("Tagging is off");
    expect(draw(fake())).not.toContain("cannot-tag");
  });
  it("says on the demo that changes aren't saved", () => {
    expect(draw(fake({ saves: false }))).toMatch(/Demo: changes aren(&#x27;|')t saved/);
    expect(draw(fake())).not.toContain("Demo:");
  });
});
