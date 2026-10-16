import { describe, expect, it } from "vitest";

import { commandShortcut, isTextEntry, plainShortcut, type KeyLike } from "./keys";

const key = (over: Partial<KeyLike> = {}): KeyLike => ({
  key: "t", metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, isComposing: false,
  target: document.body, ...over,
});

describe("isTextEntry", () => {
  it("is true in inputs, text areas, selects and editable content, and false elsewhere", () => {
    document.body.innerHTML = `<input id="i"><textarea id="t"></textarea><select id="s"></select>
      <div contenteditable="true"><span id="e">x</span></div><div contenteditable="false" id="n"></div>
      <div role="row" id="r"></div>`;
    for (const id of ["i", "t", "s", "e"]) expect(isTextEntry(document.getElementById(id))).toBe(true);
    for (const id of ["n", "r"]) expect(isTextEntry(document.getElementById(id))).toBe(false);
    expect(isTextEntry(null)).toBe(false);
  });
});

describe("the shortcut guard (spec §6)", () => {
  it("lets a bare key through only without Cmd, Ctrl or Alt, outside text, not composing", () => {
    expect(plainShortcut(key())).toBe(true);
    expect(plainShortcut(key({ metaKey: true }))).toBe(false);
    expect(plainShortcut(key({ ctrlKey: true }))).toBe(false);
    expect(plainShortcut(key({ altKey: true }))).toBe(false); // Alt+T is Sonner's
    expect(plainShortcut(key({ isComposing: true }))).toBe(false);
    document.body.innerHTML = `<input id="i">`;
    expect(plainShortcut(key({ target: document.getElementById("i") }))).toBe(false);
  });
  it("lets a Cmd/Ctrl shortcut through with either key, but not with Alt or in text", () => {
    expect(commandShortcut(key({ key: "a", metaKey: true }))).toBe(true);
    expect(commandShortcut(key({ key: "a", ctrlKey: true }))).toBe(true);
    expect(commandShortcut(key({ key: "a" }))).toBe(false);
    expect(commandShortcut(key({ key: "a", metaKey: true, altKey: true }))).toBe(false);
    document.body.innerHTML = `<textarea id="t"></textarea>`;
    expect(commandShortcut(key({ key: "a", metaKey: true, target: document.getElementById("t") }))).toBe(false);
  });
});
