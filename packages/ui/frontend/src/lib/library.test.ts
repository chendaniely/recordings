import { describe, expect, it } from "vitest";

import type { LibraryRow, LibraryView, TagResult } from "../types";
import {
  OPENING, cannotTagText, filterIsShown, markReply, mergeReply, nextNote, orderFor, shownTags, startNote, tagStatus, withIds,
} from "./library";

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
    const marks = markReply({}, result({ rows: [row("a")], failures: [{ id: "b", reason: "the recording is gone" }] }), "add");
    expect(marks).toEqual({ a: { done: "add" }, b: { failed: "the recording is gone" } });
  });
  it("records the effect of the reply that marked the row; the last one wins", () => {
    const added = markReply({}, result({ rows: [row("a")] }), "add");
    expect(added.a).toEqual({ done: "add" });
    expect(markReply(added, result({ rows: [row("a")] }), "remove").a).toEqual({ done: "remove" });
  });
});

describe("filterIsShown (Review Focus 4)", () => {
  it("is false for a tag filter whose tag no longer has a count", () => {
    expect(filterIsShown({ kind: "tag", tag: "talks" }, view)).toBe(true);
    expect(filterIsShown({ kind: "tag", tag: "gone" }, view)).toBe(false);
    expect(filterIsShown({ kind: "untagged" }, view)).toBe(true);
  });
});

describe("pending add after a pending remove", () => {
  it("shows the tag pending add, not struck", () => {
    const pending = [
      { request_id: "1", action: "remove" as const, ids: ["a"], tags: ["talks"] },
      { request_id: "2", action: "add" as const, ids: ["a"], tags: ["talks"] },
    ];
    expect(shownTags(view.recordings[0], pending)).toEqual([{ tag: "talks", by: "you", pending: "add" }]);
  });
  it("an add then a remove of a new tag shows nothing", () => {
    const pending = [
      { request_id: "1", action: "add" as const, ids: ["b"], tags: ["x"] },
      { request_id: "2", action: "remove" as const, ids: ["b"], tags: ["x"] },
    ];
    expect(shownTags(view.recordings[1], pending)).toEqual([]);
  });
});

describe("whether tagging is on (spec §5 can_tag; Task 7 carry)", () => {
  it("is on when the server says so", () => {
    expect(tagStatus(view, false)).toEqual({ canTag: true, opening: false, reason: null });
  });
  it("treats the first draw's 'being opened' as a moment, not tagging off for good", () => {
    const opening = { ...view, can_tag: false, cannot_tag_reason: OPENING };
    const status = tagStatus(opening, false);
    expect(status).toEqual({ canTag: false, opening: true, reason: null });
    expect(cannotTagText(status)).toBe("Opening the archive… try again in a moment.");
    expect(tagStatus(null, false)).toEqual({ canTag: false, opening: true, reason: null });
  });
  it("gives the server's reason, a long path included, when tagging is off", () => {
    const reason = "this is the NAS mirror's copy (/volume1/archives/recordings-mirror/archive)";
    const status = tagStatus({ ...view, can_tag: false, cannot_tag_reason: reason }, false);
    expect(status).toEqual({ canTag: false, opening: false, reason });
    expect(cannotTagText(status)).toBe(reason);
    expect(cannotTagText(tagStatus({ ...view, can_tag: false, cannot_tag_reason: null }, false))).toBe("Tagging is off.");
  });
  it("adds the mirror's advice from the server's flag, never from its words", () => {
    const reason = "/x holds MIRROR.md: it is the NAS mirror's copy. Use the app.";
    const mirror = tagStatus({ ...view, can_tag: false, cannot_tag_reason: reason, cannot_tag_mirror: true }, false);
    expect(mirror.reason).toBe(`${reason} Open the archive's own app to tag.`);
    const other = tagStatus({ ...view, can_tag: false, cannot_tag_reason: reason, cannot_tag_mirror: false }, false);
    expect(other.reason).toBe(reason); // same words, no flag: nothing added
  });
  it("is off once the connection is lost: nothing more can reach the server", () => {
    const status = tagStatus(view, true);
    expect(status.canTag).toBe(false);
    expect(cannotTagText(status)).toBe("Lost the connection. Reload to keep working.");
  });
});

describe('"just tagged" describes the selection it was applied to (ruled, Task 12 fix rounds 1 and 2)', () => {
  const ab = new Set(["a", "b"]);
  const add = (key: number, ids: string[], tags = ["work"], undo: string | null = `t${key}`) => ({ tags, ids, key, undo });
  const started = startNote(ab, add(2, ["x", "y"]), null); // what was added before is never named

  it("names an add made to exactly the selection, since it last changed", () => {
    expect(started.tags).toBe(null);
    const named = nextNote(started, ab, add(3, ["b", "a"]), null);
    expect(named.tags).toEqual(["work"]);
    expect(nextNote(named, ab, add(3, ["b", "a"]), null)).toBe(named); // nothing new: the same note
  });
  it("(a) keeps naming it when a later add went elsewhere: a row's or the header's +", () => {
    const named = nextNote(started, ab, add(3, ["a", "b"]), null);
    const plus = nextNote(named, ab, add(4, ["a"], ["space/apollo"]), null);
    expect(plus.tags).toEqual(["work"]);
    expect(nextNote(plus, ab, add(5, ["a", "b"], ["review"]), null).tags).toEqual(["review"]); // a second tag
  });
  it("(b) keeps it through a Refresh or a filter change that keeps every selected row (a new Set)", () => {
    const named = nextNote(started, ab, add(3, ["a", "b"]), null);
    const same = nextNote(named, new Set(["b", "a"]), add(3, ["a", "b"]), null);
    expect(same.tags).toEqual(["work"]);
    expect(nextNote(same, new Set(["a"]), add(3, ["a", "b"]), null).tags).toBe(null); // a row went
    expect(nextNote(named, new Set(["a", "b", "c"]), add(3, ["a", "b"]), null).tags).toBe(null);
  });
  it("(c) forgets it when an Undo reverses that add, and only that one", () => {
    const named = nextNote(started, ab, add(3, ["a", "b"]), null);
    expect(nextNote(named, ab, add(3, ["a", "b"]), { token: "t9", key: 1 }).tags).toEqual(["work"]);
    expect(nextNote(named, ab, add(3, ["a", "b"]), { token: "t3", key: 1 }).tags).toBe(null);
    const tokenless = nextNote(started, ab, add(3, ["a", "b"], ["work"], null), null);
    expect(nextNote(tokenless, ab, add(3, ["a", "b"], ["work"], null), { token: "t3", key: 1 }).tags).toEqual(["work"]);
  });
  it("starts over for a new selection, and never names an Undo heard before it", () => {
    const fresh = startNote(ab, add(3, ["a", "b"]), { token: "t3", key: 4 });
    expect(nextNote(fresh, ab, add(3, ["a", "b"]), { token: "t3", key: 4 })).toBe(fresh);
    expect(fresh.tags).toBe(null);
  });
});
