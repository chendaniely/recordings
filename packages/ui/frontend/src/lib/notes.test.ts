import { describe, expect, it } from "vitest";

import type { NotesGroup, NotesOutput } from "../types";
import { flattenNotes, noteSelection } from "./notes";

const out = (rendition: string): NotesOutput => ({ rendition, model: rendition, engine: "canned", created_at: "2026-10-08T12:00:00+00:00", html: "" });
const groups: NotesGroup[] = [
  { note_type: "conference-talk", outputs: [out("a"), out("b")] },
  { note_type: "meeting", outputs: [out("c")] },
];

describe("flattenNotes", () => {
  it("keeps list order and remembers each output's note type", () => {
    expect(flattenNotes(groups).map((x) => [x.group, x.o.rendition])).toEqual([
      ["conference-talk", "a"], ["conference-talk", "b"], ["meeting", "c"],
    ]);
    expect(flattenNotes([])).toEqual([]);
  });
});

describe("noteSelection", () => {
  const all = flattenNotes(groups);
  it("shows the picked output, or the first when nothing valid is picked", () => {
    expect(noteSelection(all, "b", "").main?.o.rendition).toBe("b");
    expect(noteSelection(all, "gone", "").main?.o.rendition).toBe("a");
    expect(noteSelection(all, null, "").main?.o.rendition).toBe("a");
    expect(noteSelection([], null, "").main).toBeUndefined();
  });
  it("compares with another output", () => {
    expect(noteSelection(all, "a", "c").second?.o.rendition).toBe("c");
  });
  it("never compares an output with itself", () => {
    // pick a, compare b, then click b in the list: the comparison must drop, not show b twice
    expect(noteSelection(all, "b", "b").second).toBeUndefined();
    expect(noteSelection(all, "a", "gone").second).toBeUndefined();
  });
});
