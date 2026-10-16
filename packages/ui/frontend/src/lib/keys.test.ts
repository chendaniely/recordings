import { describe, expect, it } from "vitest";

import { commandShortcut, composing, isTextEntry, plainShortcut, toggleFocusedKey, type KeyLike } from "./keys";

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

describe("composing", () => {
  it("reads isComposing, a React event's nativeEvent, and keyCode 229", () => {
    expect(composing({ isComposing: true })).toBe(true);
    expect(composing({ nativeEvent: { isComposing: true } })).toBe(true);
    expect(composing({ keyCode: 229 })).toBe(true);
    expect(composing({})).toBe(false);
    expect(plainShortcut(key({ isComposing: undefined, nativeEvent: { isComposing: true } }))).toBe(false);
    expect(commandShortcut(key({ key: "a", metaKey: true, keyCode: 229 }))).toBe(false);
  });
});

describe("text-like inputs only", () => {
  it("lets shortcuts through on a checkbox, radio, button, range, colour and file input", () => {
    document.body.innerHTML = ["checkbox", "radio", "button", "range", "color", "file"].map((t) => `<input type="${t}" id="${t}">`).join("") +
      `<input type="search" id="search"><input type="email" id="email"><input id="plain">`;
    for (const t of ["checkbox", "radio", "button", "range", "color", "file"]) expect(isTextEntry(document.getElementById(t))).toBe(false);
    for (const t of ["search", "email", "plain"]) expect(isTextEntry(document.getElementById(t))).toBe(true);
  });
});

describe("toggling the focused row", () => {
  it("is plain X, or Cmd/Ctrl+Space, never in text", () => {
    document.body.innerHTML = `<input id="i">`;
    expect(toggleFocusedKey(key({ key: "x" }))).toBe(true);
    expect(toggleFocusedKey(key({ key: "X" }))).toBe(true);
    expect(toggleFocusedKey(key({ key: "x", metaKey: true }))).toBe(false);
    expect(toggleFocusedKey(key({ key: "x", target: document.getElementById("i") }))).toBe(false);
    expect(toggleFocusedKey(key({ key: " ", metaKey: true }))).toBe(true);
    expect(toggleFocusedKey(key({ key: " ", ctrlKey: true }))).toBe(true);
    expect(toggleFocusedKey(key({ key: " " }))).toBe(false);
  });
});
