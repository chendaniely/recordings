import { describe, expect, it } from "vitest";

import type { LibraryRow, LibraryView } from "../types";
import {
  columnOrder, defaultColumns, digitColumn, digitFor, foldersOf, gridRows, layoutColumns, loadChoices,
  saveChoices, snippetsToFetch, sortRows,
} from "./grid";

const view = {
  tags: Array.from({ length: 20 }, (_, i) => ({ tag: `t${String(i).padStart(2, "0")}`, count: 20 - i, private: false }))
    .concat([{ tag: "private", count: 5, private: true }, { tag: "Private/health", count: 1, private: true }]),
  note_types: [{ note_type: "lecture", count: 30 }],
} as unknown as LibraryView;
const isPrivate = (t: string) => t === "private" || t === "Private/health";

describe("columns (spec §7)", () => {
  it("defaults to the 15 most-used tags, counted across all tags, notes/* included", () => {
    const cols = defaultColumns(view);
    expect(cols).toHaveLength(15);
    expect(cols[0]).toBe("notes/lecture");
    expect(cols).toContain("t00");
    expect(cols).not.toContain("t19");
  });
  it("pins private, groups other private tags at the right, and adds this session's tags at the end", () => {
    const all = [...view.tags.map((t) => t.tag), "notes/lecture"];
    const columns = layoutColumns({ base: defaultColumns(view), session: ["fresh"], all: [...all, "fresh"], isPrivate, choices: {}, hidden: new Set() });
    expect(columns.pinned).toBe("private");
    expect(columns.privateGroup).toEqual(["Private/health"]);
    expect(columns.normal.at(-1)).toBe("fresh");
    expect(columnOrder(columns)[0]).toBe("private");
  });
  it("shows and hides columns by the stored choices, and hides a private one for the session only", () => {
    const all = view.tags.map((t) => t.tag);
    const columns = layoutColumns({ base: ["t00"], session: [], all, isPrivate, choices: { t19: true, t00: false }, hidden: new Set(["Private/health"]) });
    expect(columns.normal).toEqual(["t19"]);
    expect(columns.privateGroup).toEqual([]);
    expect(columns.pinned).toBe("private");
  });
});

describe("stored choices never name a private tag (spec §7, K-M3)", () => {
  it("saves only non-private choices, and reads back defensively", () => {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    saveChoices(storage, { t01: true, "Private/health": false, private: false }, isPrivate);
    expect(JSON.parse(store.get("recordings.tagColumns") ?? "{}")).toEqual({ t01: true });
    expect(loadChoices(storage)).toEqual({ t01: true });
    store.set("recordings.tagColumns", "not json");
    expect(loadChoices(storage)).toEqual({});
    const throwing = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
    expect(loadChoices(throwing)).toEqual({});
    expect(() => saveChoices(throwing, { t01: true }, isPrivate)).not.toThrow();
  });
});

const row = (id: string, over: Partial<LibraryRow> = {}): LibraryRow => ({
  id, title: id, recorded_at: "2026-10-06T14:00:00-07:00", when: "", duration: null, duration_ms: null,
  kind: "audio", sources: [], tags: [], private: false, untagged: true, ...over,
});

describe("rows (spec §7)", () => {
  const rows = [
    row("b", { title: "Beta", recorded_at: "2026-10-02T10:00:00Z", duration_ms: 50 }),
    row("a", { title: "alpha", recorded_at: "2026-10-03T10:00:00Z", duration_ms: 900, untagged: false, tags: [{ tag: "talks", by: "you" }] }),
    row("c", { title: "Gamma", recorded_at: "2026-10-01T10:00:00Z", duration_ms: null }),
  ];
  it("sorts by newest, oldest, title, duration and has-this-tag", () => {
    expect(sortRows(rows, { by: "newest" }).map((r) => r.id)).toEqual(["a", "b", "c"]);
    expect(sortRows(rows, { by: "oldest" }).map((r) => r.id)).toEqual(["c", "b", "a"]);
    expect(sortRows(rows, { by: "title" }).map((r) => r.id)).toEqual(["a", "b", "c"]);
    expect(sortRows(rows, { by: "duration" }).map((r) => r.id)).toEqual(["a", "b", "c"]);
    expect(sortRows(rows, { by: "has", tag: "talks" }).map((r) => r.id)).toEqual(["a", "b", "c"]);
  });
  it("shows Untagged only, and filters by title, in the browser", () => {
    expect(gridRows(rows, { untaggedOnly: true, title: "" }).map((r) => r.id)).toEqual(["b", "c"]);
    expect(gridRows(rows, { untaggedOnly: false, title: "AMM" }).map((r) => r.id)).toEqual(["c"]);
  });
});

describe("digit keys (spec §7)", () => {
  it("1 to 9 and 0 are the first ten columns", () => {
    const cols = Array.from({ length: 12 }, (_, i) => `c${i}`);
    expect(digitColumn("1", cols)).toBe("c0");
    expect(digitColumn("0", cols)).toBe("c9");
    expect(digitColumn("a", cols)).toBe(null);
    expect(digitColumn("3", ["x"])).toBe(null);
    expect([digitFor(0), digitFor(8), digitFor(9), digitFor(10)]).toEqual(["1", "9", "0", null]);
  });
});

describe("snippets and folders", () => {
  it("asks for on-screen rows it hasn't got or asked for, at most 200", () => {
    const ids = Array.from({ length: 300 }, (_, i) => `r${i}`);
    const got = snippetsToFetch(ids, new Map([["r0", "x"]]), new Set(["r1"]));
    expect(got).toHaveLength(200);
    expect(got[0]).toBe("r2");
  });
  it("groups tags by their first folder", () => {
    expect([...foldersOf(["school/a", "school/b", "talks"]).entries()]).toEqual([
      ["school", ["school/a", "school/b"]], ["", ["talks"]],
    ]);
  });
});
