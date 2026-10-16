import { describe, expect, it } from "vitest";

import type { LibraryView } from "../types";
import { alreadyWords, createOffer, pickerTags, rankTags, stateWords, tagState, type PickerTag } from "./picker";

const tag = (name: string, count = 1, priv = false): PickerTag => ({ tag: name, count, private: priv });
const tags = [tag("school/course-101", 5), tag("courses", 2), tag("talks", 9), tag("private", 3, true), tag("notes/lecture", 4)];
const names = (q: string) => rankTags(tags, q).map((t) => t.tag);

describe("ranking (spec §6)", () => {
  it("puts an exact match first, then folder parts that start with it, then substrings", () => {
    expect(names("course")).toEqual(["school/course-101", "courses"]);
    expect(names("courses")).toEqual(["courses"]);
    // a folder part's start first, then substrings by use ("notes" holds an s)
    expect(names("S")).toEqual(["school/course-101", "talks", "notes/lecture", "courses"]);
    expect(names("ours")).toEqual(["school/course-101", "courses"]);
  });
  it("matches case-insensitively, and lists every tag by use when the box is empty", () => {
    expect(names("TALKS")).toEqual(["talks"]);
    expect(names("")).toEqual(["talks", "school/course-101", "notes/lecture", "private", "courses"]);
  });
});

describe("Create (spec §6)", () => {
  it("offers the typed text, trimmed, unless a tag matches it case-insensitively", () => {
    expect(createOffer(tags, "  work ")).toBe("work");
    expect(createOffer(tags, "Talks")).toBe(null); // a case-variant: never offered
    expect(createOffer(tags, "course")).toBe("course"); // a partial match still offers Create, last
    expect(createOffer(tags, "   ")).toBe(null);
  });
  it("never makes a notes/ tag", () => {
    expect(createOffer(tags, "notes/summary")).toBe(null);
    expect(createOffer(tags, "Notes/x")).toBe(null);
    expect(createOffer(tags, "notes")).toBe(null);
    expect(createOffer(tags, "notebook")).toBe("notebook");
  });
  it("creates a decomposed name in its composed form (Review Focus 1)", () => {
    const decomposed = "café";
    expect(createOffer(tags, decomposed)).toBe("café");
    expect(rankTags([tag("café")], decomposed).map((t) => t.tag)).toEqual(["café"]);
  });
});

describe("item states, in words as well as glyphs (spec §6)", () => {
  const rows = [{ tags: [{ tag: "talks" }] }, { tags: [{ tag: "talks" }, { tag: "work" }] }, { tags: [] }];
  it("counts how many targets carry the tag", () => {
    expect(tagState("talks", rows)).toEqual({ on: 2, of: 3, state: "some" });
    expect(stateWords(tagState("talks", rows))).toBe("on 2 of 3");
    expect(stateWords(tagState("talks", rows.slice(0, 2)))).toBe("on all 2");
    expect(stateWords(tagState("x", rows))).toBe("");
    expect(alreadyWords(tagState("talks", rows.slice(0, 2)))).toBe("Already on all 2");
    expect(alreadyWords(tagState("talks", rows.slice(0, 1)))).toBe("Already on it");
  });
});

describe("pickerTags", () => {
  it("takes tags with the server's private flags, note types as notes/<type>, and this session's new tags", () => {
    const view = {
      tags: [{ tag: "talks", count: 2, private: false }, { tag: "Private/x", count: 1, private: true }],
      note_types: [{ note_type: "lecture", count: 3 }],
    } as unknown as LibraryView;
    expect(pickerTags(view, ["fresh", "talks"])).toEqual([
      tag("talks", 2), tag("Private/x", 1, true), tag("notes/lecture", 3), tag("fresh", 0),
    ]);
  });
});
