import { describe, expect, it } from "vitest";

import type { TagRequest, TagResult } from "../types";
import type { Origin } from "./queue";
import {
  ALREADY_UNDONE, GONE, NO_UNDOS, UNDO_GONE, UNDO_LIMIT, UNDO_STACK, busyTokens, cellsClosed, heardUndo, isRedo, mergesIntoCells,
  nextCellUndo, nextUndo, replyEffect, learnReversal, usedUp, type Undos,
} from "./undo";

const add = (id: string): TagRequest => ({ request_id: id, action: "add", ids: ["a"], tags: ["t"] });
const undoOf = (token: string): TagRequest => ({ request_id: `u-${token}`, action: "undo", ids: [], tags: [], undo: token });
const reply = (over: Partial<TagResult>): TagResult => ({
  request_id: "r", status: "done", changed: 1, unchanged: 0, done_so_far: 1, total: 1, loses_private: null,
  now_visible: 0, failures: [], undo: null, rows: [], counts: null, tags: null, note_types: null, message: null,
  ...over,
});
const picker: Origin = { kind: "picker" };
const cell: Origin = { kind: "cell" };
const none = new Set<string>();

function did(u: Undos, token: string, origin: Origin = picker): Undos {
  return heardUndo(u, add(`r-${token}`), origin, reply({ undo: token }));
}

describe("Cmd/Ctrl+Z's stack (spec §6; plan decision 7)", () => {
  it("keeps the tokens of actions that aren't undos, newest last, at most 20", () => {
    let u = NO_UNDOS;
    for (let i = 0; i < UNDO_STACK + 2; i++) u = did(u, `t${i}`);
    expect(u.stack).toHaveLength(UNDO_STACK);
    expect(u.stack[0]).toBe("t2");
    expect(nextUndo(u, none)).toBe(`t${UNDO_STACK + 1}`);
  });
  it("peeks: Cmd-Z doesn't drop a token before the server has used it", () => {
    const u = did(did(NO_UNDOS, "t1"), "t2");
    expect(nextUndo(u, none)).toBe("t2");
    expect(u.stack).toEqual(["t1", "t2"]);
  });
  it("a second Cmd-Z while the first Undo is pending undoes the action before it, never the same twice", () => {
    const u = did(did(NO_UNDOS, "t1"), "t2");
    expect(nextUndo(u, busyTokens([undoOf("t2")]))).toBe("t1");
    expect(nextUndo(u, busyTokens([undoOf("t2"), undoOf("t1")]))).toBe(null);
  });
  it("drops a token only once its Undo has settled everything it could", () => {
    const u = did(did(NO_UNDOS, "t1"), "t2");
    expect(heardUndo(u, undoOf("t2"), { kind: "undo" }, reply({ undo: "redo" })).stack).toEqual(["t1"]);
    // A recording gone since is settled: nothing more can be undone there (server, _settle_undo).
    const gone = reply({ undo: "redo", failures: [{ id: "x", reason: GONE }] });
    expect(heardUndo(u, undoOf("t2"), { kind: "undo" }, gone).stack).toEqual(["t1"]);
  });
  it("keeps the token of an Undo that stopped part way, or met a busy recording, as the server does", () => {
    const u = did(NO_UNDOS, "t1");
    const stopped = reply({ status: "failed", message: "Stopped: the disk is full", undo: "redo" });
    const busy = reply({ failures: [{ id: "x", reason: "busy: another job holds this recording; try again" }] });
    const refused = reply({ status: "failed", changed: 0, message: "Stopped: this app can't write the archive now" });
    for (const r of [stopped, busy, refused]) {
      expect(heardUndo(u, undoOf("t1"), { kind: "undo" }, r).stack).toEqual(["t1"]);
    }
  });
  it("drops a token the server no longer has, so Cmd-Z never sticks on it", () => {
    const u = did(did(NO_UNDOS, "t1"), "t2");
    for (const message of [ALREADY_UNDONE, UNDO_GONE, UNDO_LIMIT]) {
      const r = reply({ status: "failed", changed: 0, message });
      expect(heardUndo(u, undoOf("t2"), { kind: "undo" }, r).stack).toEqual(["t1"]);
    }
  });
  it("never puts an Undo's own (Redo) token on the stack", () => {
    const u = did(NO_UNDOS, "t1");
    expect(heardUndo(u, undoOf("t1"), { kind: "undo" }, reply({ undo: "redo" })).stack).toEqual([]);
  });
  it("keeps the token of a stopped batch, which covers what landed", () => {
    const u = heardUndo(NO_UNDOS, add("r"), picker, reply({ status: "failed", message: "Stopped: a disk error", undo: "t1" }));
    expect(u.stack).toEqual(["t1"]);
  });
  it("ignores progress and confirm replies", () => {
    const u = did(NO_UNDOS, "t1");
    expect(heardUndo(u, undoOf("t1"), { kind: "undo" }, reply({ status: "confirm" }))).toBe(u);
    expect(heardUndo(u, add("r"), picker, reply({ status: "progress", undo: "x" }))).toBe(u);
  });
  it("lists the tokens of queued, in-flight or confirming Undo requests", () => {
    expect([...busyTokens([add("r"), undoOf("a"), undoOf("b")])]).toEqual(["a", "b"]);
  });
  it("says which outcomes use a token up", () => {
    expect(usedUp(reply({}))).toBe(true);
    expect(usedUp(reply({ failures: [{ id: "x", reason: "the file doesn't validate" }] }))).toBe(false);
    expect(usedUp(reply({ status: "failed", message: ALREADY_UNDONE }))).toBe(true);
    expect(usedUp(reply({ status: "failed", message: UNDO_LIMIT }))).toBe(true);
    expect(usedUp(reply({ status: "failed", message: "Stopped: a disk error" }))).toBe(false);
  });
});

describe("the grid's running toast shares the stack's tokens (Task 9 carry)", () => {
  it("gives a removal that makes recordings visible, or removes a note type, a toast of its own", () => {
    // why: the running toast's text would drop "· auto-pick won't add it back" (Task 10 council).
    const remove: TagRequest = { request_id: "r", action: "remove", ids: ["a"], tags: ["private"] };
    expect(mergesIntoCells(cell, remove, reply({ undo: "t", now_visible: 1 }))).toBe(false);
    const notes: TagRequest = { request_id: "r", action: "remove", ids: ["a"], tags: ["notes/lecture"] };
    expect(mergesIntoCells(cell, notes, reply({ undo: "t" }))).toBe(false);
    expect(mergesIntoCells(cell, { ...notes, action: "add" }, reply({ undo: "t" }))).toBe(true);
  });
  it("lets a cell's add that makes a recording visible join the running toast, which counts them", () => {
    // why: with Untagged only on, every row's first tag makes it visible, so tagging 20 rows left
    // 21 toasts, each read out (Task 14 fix round 1, revising Task 10's ruling for adds only).
    expect(mergesIntoCells(cell, add("r"), reply({ undo: "t", now_visible: 1 }))).toBe(true);
    let u = heardUndo(NO_UNDOS, add("r1"), cell, reply({ undo: "c1", now_visible: 1 }));
    u = heardUndo(u, add("r2"), cell, reply({ undo: "c2" }));
    u = heardUndo(u, add("r3"), cell, reply({ undo: "c3", now_visible: 1 }));
    expect(u.cells).toMatchObject({ changes: 3, visible: 2, tokens: ["c1", "c2", "c3"] });
    // An Undo of one of them takes its change, and what it made visible, off the toast.
    u = heardUndo(u, undoOf("c3"), { kind: "undo" }, reply({ undo: "redo" }));
    expect(u.cells).toMatchObject({ changes: 2, visible: 1 });
    expect(cellsClosed(u, u.cells.key).cells).toMatchObject({ open: false, changes: 0, visible: 0 });
  });
  it("merges only a clean, finished single-cell change", () => {
    expect(mergesIntoCells(cell, add("r"), reply({ undo: "t" }))).toBe(true);
    expect(mergesIntoCells(picker, add("r"), reply({ undo: "t" }))).toBe(false);
    expect(mergesIntoCells(cell, add("r"), reply({ undo: null, changed: 0, unchanged: 1 }))).toBe(false);
    expect(mergesIntoCells(cell, add("r"), reply({ undo: "t", failures: [{ id: "a", reason: GONE }] }))).toBe(false);
    expect(mergesIntoCells(cell, add("r"), reply({ undo: "t", status: "failed", message: "Stopped: a disk error" }))).toBe(false);
  });
  it("counts the changes in one running toast, and starts a new one after it closes", () => {
    let u = did(did(NO_UNDOS, "c1", cell), "c2", cell);
    expect(u.cells).toMatchObject({ key: 1, open: true, changes: 2, tokens: ["c1", "c2"] });
    expect(u.stack).toEqual(["c1", "c2"]);
    u = cellsClosed(u, 1);
    expect(u.cells).toMatchObject({ key: 1, open: false, changes: 0, tokens: [] });
    expect(u.stack).toEqual(["c1", "c2"]); // Cmd-Z still has them
    u = did(u, "c3", cell);
    expect(u.cells).toMatchObject({ key: 2, open: true, changes: 1, tokens: ["c3"] });
  });
  it("a close of an older toast doesn't empty a newer one", () => {
    const u = did(cellsClosed(did(NO_UNDOS, "c1", cell), 1), "c2", cell);
    expect(cellsClosed(u, 1)).toBe(u);
  });
  it("Cmd-Z's Undo of a cell's change takes it off the toast too, so Undo last never sends a used token", () => {
    let u = did(did(NO_UNDOS, "c1", cell), "c2", cell);
    expect(nextCellUndo(u, busyTokens([undoOf("c2")]))).toBe("c1");
    u = heardUndo(u, undoOf("c2"), { kind: "undo" }, reply({ undo: "redo" }));
    expect(u.cells.tokens).toEqual(["c1"]);
    expect(u.cells.changes).toBe(1);
    expect(nextCellUndo(u, none)).toBe("c1");
    expect(u.stack).toEqual(["c1"]);
  });
  it("Undo last never reaches past the toast's own changes", () => {
    const u = did(did(NO_UNDOS, "p1"), "c1", cell);
    expect(nextCellUndo(u, busyTokens([undoOf("c1")]))).toBe(null);
    expect(nextUndo(u, busyTokens([undoOf("c1")]))).toBe("p1");
  });
  it("a token pushed off the stack leaves the toast as well", () => {
    let u = did(NO_UNDOS, "c0", cell);
    for (let i = 0; i < UNDO_STACK; i++) u = did(u, `p${i}`);
    expect(u.stack).not.toContain("c0");
    expect(u.cells.tokens).toEqual([]);
    expect(nextCellUndo(u, none)).toBe(null);
  });
});

describe("Redo (plan decision 7; Task 10 council)", () => {
  const undone = (u: Undos, token: string, redoToken: string) =>
    heardUndo(u, undoOf(token), { kind: "undo" }, reply({ undo: redoToken }));
  it("knows an Undo of an Undo's token is a Redo, and puts the Redo's token on the stack", () => {
    let u = did(NO_UNDOS, "t1");
    u = undone(u, "t1", "r1");
    expect(u.stack).toEqual([]);
    expect(isRedo(u, undoOf("r1"))).toBe(true);
    expect(isRedo(u, undoOf("t1"))).toBe(false);
    u = heardUndo(u, undoOf("r1"), { kind: "undo" }, reply({ undo: "t1b" }));
    expect(u.stack).toEqual(["t1b"]); // Cmd-Z can undo the redone action again
    expect(isRedo(u, undoOf("r1"))).toBe(false); // used up
    expect(isRedo(u, undoOf("t1b"))).toBe(false);
  });
  it("Cmd-Z twice after an action undoes two actions, never undo then redo", () => {
    let u = did(did(NO_UNDOS, "t1"), "t2");
    u = undone(u, "t2", "r2");
    expect(nextUndo(u, none)).toBe("t1");
  });
  it("keeps a Redo token whose Redo stopped part way", () => {
    let u = undone(did(NO_UNDOS, "t1"), "t1", "r1");
    u = heardUndo(u, undoOf("r1"), { kind: "undo" }, reply({ status: "failed", message: "Stopped: a disk error", undo: "t1b" }));
    expect(isRedo(u, undoOf("r1"))).toBe(true);
    expect(u.stack).toEqual(["t1b"]);
  });
  it("remembers at most 20 Redo tokens", () => {
    let u = NO_UNDOS;
    for (let i = 0; i < UNDO_STACK + 3; i++) u = undone(did(u, `t${i}`), `t${i}`, `r${i}`);
    expect(u.redos).toHaveLength(UNDO_STACK);
    expect(isRedo(u, undoOf("r0"))).toBe(false);
    expect(isRedo(u, undoOf(`r${UNDO_STACK + 2}`))).toBe(true);
  });
});

describe("what a reply did, for the row's done label (Dan, 2026-10-22)", () => {
  it("an add adds and a removal removes", () => {
    expect(replyEffect({}, add("r1"))).toBe("add");
    expect(replyEffect({}, { ...add("r2"), action: "remove" })).toBe("remove");
  });
  it("an Undo does the opposite of the action it reverses, and its Redo the opposite of that", () => {
    let known = learnReversal({}, add("r1"), reply({ undo: "t1" }));
    expect(replyEffect(known, undoOf("t1"))).toBe("remove"); // Undo of an add removes
    known = learnReversal(known, undoOf("t1"), reply({ undo: "redo1" }));
    expect(replyEffect(known, undoOf("redo1"))).toBe("add"); // Redo adds again
    const removal = learnReversal(known, { ...add("r3"), action: "remove" }, reply({ undo: "t3" }));
    expect(replyEffect(removal, undoOf("t3"))).toBe("add"); // Undo of a removal adds
  });
  it("learns nothing from a reply with no token", () => {
    expect(learnReversal({}, add("r1"), reply({ undo: null }))).toEqual({});
  });
});
