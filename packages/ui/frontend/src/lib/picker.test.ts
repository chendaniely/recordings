import { describe, expect, it } from "vitest";

import type { LibraryView } from "../types";
import {
  alreadyWords, createOffer, createRefusal, pickerGroups, pickerTags, rankTags, stateWords, tagState, tagStates, type PickerTag,
} from "./picker";

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
  it("writes big counts as the title does (\"Tag 1,200 recordings\")", () => {
    expect(stateWords({ on: 1200, of: 1200, state: "all" })).toBe("on all 1,200");
    expect(stateWords({ on: 1100, of: 20000, state: "some" })).toBe("on 1,100 of 20,000");
    expect(alreadyWords({ on: 1200, of: 1200, state: "all" })).toBe("Already on all 1,200");
  });
});

describe("pickerTags", () => {
  it("keeps a private tag whose last use went under the lock, though the session also holds it (residual 6)", () => {
    const view = { tags: [], note_types: [] } as unknown as Parameters<typeof pickerTags>[0];
    expect(pickerTags(view, ["hush/x"], [tag("hush/x", 0, true)])).toEqual([tag("hush/x", 0, true)]);
  });
  it("takes tags with the server's private flags, note types as notes/<type>, and this session's new tags", () => {
    const view = {
      tags: [{ tag: "talks", count: 2, private: false }, { tag: "Private/x", count: 1, private: true }],
      note_types: [{ note_type: "lecture", count: 3 }],
    } as unknown as LibraryView;
    expect(pickerTags(view, ["fresh", "talks"])).toEqual([
      tag("talks", 2), tag("Private/x", 1, true), tag("notes/lecture", 3), tag("fresh", 0),
    ]);
  });
  it("keeps tags the picker touched, at 0 and with the flag the server last gave, when the view drops them", () => {
    const view = { tags: [{ tag: "talks", count: 2, private: false }], note_types: [] } as unknown as LibraryView;
    expect(pickerTags(view, [], [tag("private", 1, true), tag("talks", 9)])).toEqual([tag("talks", 2), tag("private", 0, true)]);
  });
});

describe("pickerGroups (the first item shown is the first ranked)", () => {
  const set = [tag("people/alex", 9), tag("private/alex", 1, true), tag("talks", 4), tag("private", 2, true)];
  it("on a rank tie the private (locked) group comes first, and the first item shown is rankTags()[0]", () => {
    const groups = pickerGroups(set, "alex");
    expect(groups.map((g) => g.locked)).toEqual([true, false]);
    expect(groups[0].tags[0].tag).toBe("private/alex");
    expect(groups[0].tags[0]).toBe(rankTags(set, "alex")[0]);
  });
  it("orders groups by their best-ranked item, and drops an empty group", () => {
    expect(pickerGroups(set, "talks").map((g) => g.locked)).toEqual([false]);
    expect(pickerGroups(set, "ALEX").flatMap((g) => g.tags.map((t) => t.tag))).toEqual(["private/alex", "people/alex"]);
    const g = pickerGroups(set, "");
    expect(g[0].tags[0]).toBe(rankTags(set, "")[0]);
    expect(g.flatMap((x) => x.tags)).toHaveLength(4);
  });
});

describe("Create refuses structurally invalid names", () => {
  it("hides Create for leading, trailing and double slashes, dot parts, and over-long names", () => {
    for (const bad of ["/a", "a/", "a//b", "a/./b", "..", "a/../b", "a".repeat(65), `a/${"b".repeat(65)}`, Array(51).fill("abcd").join("/")]) {
      expect(createOffer(tags, bad)).toBe(null);
    }
    expect(createOffer(tags, "a".repeat(64))).toBe("a".repeat(64));
    expect(createOffer(tags, "work/2026.q1")).toBe("work/2026.q1");
  });
});

describe("why Create isn't offered (Shift+Enter says so, in the server's words)", () => {
  it("names an existing tag, a case-variant, a notes/ tag, and a malformed name", () => {
    expect(createRefusal(tags, "talks")).toBe("That tag exists: Enter adds it.");
    expect(createRefusal(tags, " TALKS ")).toBe("A tag with the same letters in another case exists already: Enter adds it.");
    expect(createRefusal(tags, "notes/summary")).toBe("Note types come from prompt files: notes/<type> must name one the archive has.");
    expect(createRefusal(tags, "notes/lecture")).toBe("That tag exists: Enter adds it."); // it exists: Enter adds it
    expect(createRefusal(tags, "a//b")).toBe("A folder part is empty, or starts or ends with a space.");
    expect(createRefusal(tags, "a /b")).toBe("A folder part is empty, or starts or ends with a space.");
    expect(createRefusal(tags, "a".repeat(65))).toBe("Each folder part is at most 64 characters, and the whole tag at most 200.");
    expect(createRefusal(tags, "a/../b")).toBe("A folder part can't be . or .., and a tag can't hold a backslash.");
    expect(createRefusal(tags, "a\\b")).toBe("A folder part can't be . or .., and a tag can't hold a backslash.");
  });
  it("refuses before sending a space that isn't U+0020 and a format character, in the server's words", () => {
    const space = "Use a plain space, not another kind of space character.";
    const invisible = "A tag can't hold invisible, control or line-separator characters.";
    expect(createRefusal(tags, "a\u00a0b")).toBe(space);
    expect(createRefusal(tags, "a\u2009b")).toBe(space);
    expect(createRefusal(tags, "a\u200bb")).toBe(invisible);
    expect(createRefusal(tags, "a\u00adb")).toBe(invisible);
    expect(createOffer(tags, "a\u00a0b")).toBe(null);
    expect(createRefusal(tags, "a b")).toBe(null);
  });
  it("is null exactly when Create is offered, or the box is empty", () => {
    for (const q of ["", "  ", "work", "course", "notebook", "café"]) {
      expect(createRefusal(tags, q)).toBe(null);
    }
    for (const q of ["talks", "Talks", "notes/x", "/a", "a/./b", "a /b", "a\\b"]) {
      expect(createOffer(tags, q)).toBe(null);
      expect(createRefusal(tags, q)).not.toBe(null);
    }
  });
});

describe("tagStates: every item's state, counted once over the targets", () => {
  it("agrees with tagState, and counts a row once", () => {
    const rows = [{ tags: [{ tag: "talks" }, { tag: "talks" }] }, { tags: [{ tag: "talks" }, { tag: "work" }] }, { tags: [] }];
    const stateOf = tagStates(rows);
    expect(stateOf("talks")).toEqual({ on: 2, of: 3, state: "some" });
    expect(stateOf("work")).toEqual(tagState("work", rows));
    expect(stateOf("nope")).toEqual({ on: 0, of: 3, state: "none" });
    expect(tagStates(rows.slice(1, 2))("work").state).toBe("all");
  });
});
