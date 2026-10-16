import { describe, expect, it } from "vitest";

import type { LibraryRow, LibraryView, TagResult } from "../types";
import { filterIsShown, markReply, mergeReply, orderFor, shownTags, withIds } from "./library";

const row = (id: string, tags: string[] = []): LibraryRow => ({
  id, title: id, recorded_at: "2026-10-06T14:00:00-07:00", when: "", duration: null, duration_ms: null,
  kind: "audio", sources: [], tags: tags.map((tag) => ({ tag, by: "you" as const })), private: false,
  untagged: tags.length === 0,
});
const view: LibraryView = {
  recordings: [row("a", ["talks"]), row("b"), row("c")],
  counts: { all: 3, untagged: 2 }, tags: [{ tag: "talks", count: 1, private: false }],
  note_types: [], problems: [], can_tag: true, cannot_tag_reason: null, saves: true,
};
const result = (over: Partial<TagResult>): TagResult => ({
  request_id: "r", status: "done", changed: 0, unchanged: 0, done_so_far: 0, total: 0,
  loses_private: null, now_visible: 0, failures: [], undo: null, rows: [], counts: null, tags: null,
  note_types: null, message: null, ...over,
});

describe("merging replies (spec §5, deltas)", () => {
  it("patches the changed rows in place and takes the recounted counts and tags", () => {
    const merged = mergeReply(view, result({
      rows: [row("b", ["work"])], counts: { all: 3, untagged: 1 },
      tags: [{ tag: "talks", count: 1, private: false }, { tag: "work", count: 1, private: false }],
    }));
    expect(merged.recordings.map((r) => r.id)).toEqual(["a", "b", "c"]);
    expect(merged.recordings[1].tags).toEqual([{ tag: "work", by: "you" }]);
    expect(merged.counts.untagged).toBe(1);
    expect(merged.tags.map((t) => t.tag)).toEqual(["talks", "work"]);
  });
  it("keeps its counts when a reply carries none (a refusal)", () => {
    expect(mergeReply(view, result({ status: "failed" }))).toBe(view);
  });
});

describe("rows stay put (spec §6)", () => {
  it("orders by the filter once; a later tag change keeps the row", () => {
    const untagged = orderFor(view.recordings, { kind: "untagged" });
    expect(untagged).toEqual(["b", "c"]);
    const merged = mergeReply(view, result({ rows: [row("b", ["work"])] }));
    expect(untagged.map((id) => merged.recordings.find((r) => r.id === id)?.id)).toEqual(["b", "c"]);
  });
  it("an Undo puts the rows it touched back, at their place in the view", () => {
    expect(withIds(["c"], ["a"], view.recordings)).toEqual(["a", "c"]);
  });
});

describe("pending changes show at once (spec §6)", () => {
  it("adds and removes optimistically, marked pending, only on the request's rows", () => {
    const pending = [
      { request_id: "1", action: "add" as const, ids: ["a"], tags: ["work"] },
      { request_id: "2", action: "remove" as const, ids: ["a"], tags: ["talks"] },
      { request_id: "3", action: "add" as const, ids: ["b"], tags: ["other"] },
    ];
    expect(shownTags(view.recordings[0], pending)).toEqual([
      { tag: "talks", by: "you", pending: "remove" },
      { tag: "work", by: "you", pending: "add" },
    ]);
  });
});

describe("marks", () => {
  it("marks changed rows done and failed rows with their reason", () => {
    const marks = markReply({}, result({ rows: [row("a")], failures: [{ id: "b", reason: "the recording is gone" }] }));
    expect(marks).toEqual({ a: { done: true }, b: { failed: "the recording is gone" } });
  });
});

describe("filterIsShown (Review Focus 4)", () => {
  it("is false for a tag filter whose tag no longer has a count", () => {
    expect(filterIsShown({ kind: "tag", tag: "talks" }, view)).toBe(true);
    expect(filterIsShown({ kind: "tag", tag: "gone" }, view)).toBe(false);
    expect(filterIsShown({ kind: "untagged" }, view)).toBe(true);
  });
});
