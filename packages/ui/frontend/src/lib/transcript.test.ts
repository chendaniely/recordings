import { describe, expect, it } from "vitest";

import type { LibraryRow, Turn } from "../types";
import { activeIndex, activeWord, filterRows, formatClock } from "./transcript";

const turns: Turn[] = [
  { start: 0, end: 2, speaker: "A", text: "one", words: [{ word: "one", start: 0, end: 0.5 }] },
  { start: 2.5, end: 5, speaker: "B", text: "two three", words: [{ word: "two", start: 2.5, end: 3 }, { word: "three", start: 3.2, end: 4 }] },
  { start: 6, end: 8, speaker: "B", text: "four", words: [] },
];

describe("activeIndex", () => {
  it("is -1 before the first turn and with no turns", () => {
    expect(activeIndex([], 3)).toBe(-1);
    expect(activeIndex([{ ...turns[0], start: 1 }], 0.5)).toBe(-1);
  });
  it("keeps the last started turn through gaps", () => {
    expect(activeIndex(turns, 0)).toBe(0);
    expect(activeIndex(turns, 2.2)).toBe(0); // gap after turn 0
    expect(activeIndex(turns, 2.5)).toBe(1);
    expect(activeIndex(turns, 99)).toBe(2);
  });
});

describe("activeWord", () => {
  it("marks the word being spoken, or none between words", () => {
    expect(activeWord(turns[1], 2.7)).toBe(0);
    expect(activeWord(turns[1], 3.1)).toBe(-1);
    expect(activeWord(turns[1], 3.5)).toBe(1);
  });
});

describe("formatClock", () => {
  it("formats minutes and hours", () => {
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(65.4)).toBe("1:05");
    expect(formatClock(3725)).toBe("1:02:05");
  });
});

describe("filterRows", () => {
  const row = (id: string, tags: string[]): LibraryRow => ({
    id, title: id, recorded_at: "", when: "", duration: null, kind: "audio", sources: [],
    tags: tags.map((tag) => ({ tag, by: "you" as const })), private: false, untagged: tags.length === 0,
  });
  const rows = [row("a", []), row("b", ["school/course-101"]), row("c", ["school"])];
  it("filters by all, untagged and exact tag", () => {
    expect(filterRows(rows, { kind: "all" }).map((r) => r.id)).toEqual(["a", "b", "c"]);
    expect(filterRows(rows, { kind: "untagged" }).map((r) => r.id)).toEqual(["a"]);
    expect(filterRows(rows, { kind: "tag", tag: "school" }).map((r) => r.id)).toEqual(["c"]);
  });
});
