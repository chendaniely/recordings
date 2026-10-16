import { describe, expect, it } from "vitest";

import type { TagRequest, TagResult } from "../types";
import {
  actionText, cellText, confirmBody, confirmTitle, droppedText, failureLines, progressKind, progressStart, progressText, textOf,
} from "./toasts";

const req = (over: Partial<TagRequest>): TagRequest => ({ request_id: "r", action: "add", ids: [], tags: [], ...over });
const res = (over: Partial<TagResult>): TagResult => ({
  request_id: "r", status: "done", changed: 0, unchanged: 0, done_so_far: 0, total: 0,
  loses_private: null, now_visible: 0, failures: [], undo: "u", rows: [], counts: null, tags: null,
  note_types: null, message: null, ...over,
});

describe("toast wording (spec §6)", () => {
  it("names the tag and the count", () => {
    const text = actionText(req({ tags: ["school/course-101"], ids: Array(12).fill("x") }), res({ changed: 12 }), true);
    expect(textOf(text)).toBe("Added school/course-101 to 12 recordings");
    expect(text).toContainEqual({ code: "school/course-101" });
    expect(textOf(actionText(req({ action: "remove", tags: ["talks"] }), res({ changed: 1 }), true)))
      .toBe("Removed talks from 1 recording");
    expect(textOf(actionText(req({ tags: ["a", "b"] }), res({ changed: 3 }), true))).toBe("Added 2 tags to 3 recordings");
  });
  it("says when nothing changed, for an add and for a removal", () => {
    expect(textOf(actionText(req({ tags: ["t"], ids: Array(12).fill("x") }), res({ changed: 0, unchanged: 12 }), true)))
      .toBe("Already on all 12");
    expect(textOf(actionText(req({ action: "remove", tags: ["t"], ids: Array(3).fill("x") }), res({ changed: 0, unchanged: 3 }), true)))
      .toBe("Already off all 3");
    expect(textOf(actionText(req({ action: "remove", tags: ["t"], ids: ["x"] }), res({ changed: 0, unchanged: 1 }), true)))
      .toBe("Already off it");
  });
  it("adds the visibility, auto-pick and demo notes", () => {
    const visible = actionText(req({ tags: ["work"] }), res({ changed: 12, now_visible: 12 }), true);
    expect(textOf(visible)).toBe("Added work to 12 recordings · 12 now visible to external agents");
    const noted = actionText(req({ action: "remove", tags: ["notes/lecture"] }), res({ changed: 1 }), true);
    expect(textOf(noted)).toBe("Removed notes/lecture from 1 recording · auto-pick won't add it back");
    expect(textOf(actionText(req({ tags: ["t"] }), res({ changed: 1 }), false)))
      .toBe("Added t to 1 recording · Demo: changes aren't saved");
  });
  it("says an Undo, with counts grouped by thousands", () => {
    expect(textOf(actionText(req({ action: "undo", undo: "u" }), res({ changed: 1200 }), true))).toBe("Undone on 1,200 recordings");
    expect(progressText(1200, 2000)).toBe("Tagging 1,200 of 2,000…");
    expect(cellText(4)).toBe("4 changes");
    expect(cellText(1)).toBe("1 change");
  });
  it("groups failures by reason, with counts", () => {
    const stale = "edited outside the app: run `recordings reindex`";
    expect(failureLines([{ id: "a", reason: stale }, { id: "b", reason: stale }, { id: "c", reason: "the recording is gone" }]))
      .toEqual([`2 ${stale}`, "1 the recording is gone"]);
  });
  it("says what a lost connection dropped", () => {
    expect(droppedText(2, true)).toBe("Lost the connection: 2 queued changes were dropped. The change already sent was applied once or not at all; reload to see.");
    expect(droppedText(0, false)).toBe("Lost the connection. Reload to keep working.");
  });
});

describe("the confirmation's wording (spec §6)", () => {
  const isPrivate = (t: string) => t === "private" || t.startsWith("private/");
  it("names the private tag and how many recordings", () => {
    const lp = { visible: 2, untagged: 1, ids: ["a", "b", "c"] };
    expect(textOf(confirmTitle(req({ action: "remove", tags: ["private", "talks"] }), lp, isPrivate)))
      .toBe("Remove private from 3 recordings?");
    expect(textOf(confirmTitle(req({ action: "undo", undo: "u" }), lp, isPrivate)))
      .toBe("Undo will remove private from 3 recordings?");
    expect(confirmBody(lp)).toBe("2 become visible to external agents such as Claude Code. 1 will have no tags left, and stays hidden until its next tag.");
  });
  it("uses the singular and plural forms", () => {
    expect(confirmBody({ visible: 1, untagged: 0, ids: ["a"] })).toBe("1 becomes visible to external agents such as Claude Code.");
    expect(confirmBody({ visible: 0, untagged: 2, ids: ["a", "b"] })).toBe("2 will have no tags left, and stay hidden until their next tag.");
    const both = { visible: 0, untagged: 1, ids: ["a"] };
    expect(textOf(confirmTitle(req({ action: "remove", tags: ["private/a", "private/b"] }), both, isPrivate)))
      .toBe("Remove private/a, private/b from 1 recording?");
  });
});

describe("never says Already for writes that failed", () => {
  const ids12 = Array(12).fill("x");
  it("says nothing when nothing changed and nothing was unchanged", () => {
    const failed = res({ changed: 0, unchanged: 0, failures: [{ id: "x", reason: "the recording is gone" }] });
    expect(actionText(req({ tags: ["t"], ids: ids12 }), failed, true)).toEqual([]);
    expect(actionText(req({ action: "remove", tags: ["t"], ids: ids12 }), res({ status: "failed" }), true)).toEqual([]);
    expect(actionText(req({ tags: ["t"], ids: ids12 }), res({ status: "confirm" }), true)).toEqual([]);
    expect(actionText(req({ action: "undo", undo: "u" }), res({ changed: 0 }), true)).toEqual([]);
  });
  it("counts only reply.unchanged, and says 3 of 12 when some failed", () => {
    const partial = res({ changed: 0, unchanged: 3, failures: Array(9).fill({ id: "y", reason: "busy" }) });
    expect(textOf(actionText(req({ tags: ["t"], ids: ids12 }), partial, true))).toBe("Already on 3 of 12");
    expect(textOf(actionText(req({ action: "remove", tags: ["t"], ids: ids12 }), partial, true))).toBe("Already off 3 of 12");
  });
});

describe("Redo (Task 10 council)", () => {
  it("says Redone for an Undo of an Undo", () => {
    expect(textOf(actionText(req({ action: "undo", undo: "r" }), res({ changed: 3 }), true, true))).toBe("Redone on 3 recordings");
    expect(textOf(actionText(req({ action: "undo", undo: "r" }), res({ changed: 1 }), false, true)))
      .toBe("Redone on 1 recording · Demo: changes aren't saved");
  });
  it("asks before a Redo removes private, naming it a Redo", () => {
    const lp = { visible: 1, untagged: 0, ids: ["a"] };
    expect(textOf(confirmTitle(req({ action: "undo", undo: "r" }), lp, () => true, true))).toBe("Redo will remove private from 1 recording?");
  });
});

describe("the grid's running toast on Pages (Task 10 council)", () => {
  it("says the demo doesn't save", () => {
    expect(cellText(4, false)).toBe("4 changes · Demo: changes aren't saved");
    expect(cellText(4, true)).toBe("4 changes");
  });
});

describe("progress follows the action (Task 10 council)", () => {
  it("says what is being done, and never 'Removing N recordings'", () => {
    expect(progressStart(2000, "add")).toBe("Tagging 2,000 recordings…");
    expect(progressStart(2000, "remove")).toBe("Removing tags from 2,000 recordings…");
    expect(progressStart(300, "undo")).toBe("Undoing on 300 recordings…");
    expect(progressStart(300, "redo")).toBe("Redoing on 300 recordings…");
    expect(progressText(1200, 2000, "remove")).toBe("Removing tags: 1,200 of 2,000…");
    expect(progressText(10, 300, "undo")).toBe("Undoing: 10 of 300…");
    expect(progressText(10, 300, "redo")).toBe("Redoing: 10 of 300…");
  });
  it("knows the kind from the request", () => {
    expect(progressKind(req({ action: "add" }), false)).toBe("add");
    expect(progressKind(req({ action: "remove" }), false)).toBe("remove");
    expect(progressKind(req({ action: "undo", undo: "u" }), false)).toBe("undo");
    expect(progressKind(req({ action: "undo", undo: "u" }), true)).toBe("redo");
  });
});
