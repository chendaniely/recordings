import { createElement, type KeyboardEvent } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { TaggingContext, type Tagging } from "../tagging/useTagging";
import type { SelAction } from "./selection";

vi.mock("../sr", () => ({})); // useTagging's Shiny side: only the context is read here

import { useListShortcuts } from "./useListShortcuts";

function setup() {
  const tagging = { undoLast: vi.fn(), refresh: vi.fn() } as unknown as Tagging;
  const act = vi.fn<(a: SelAction) => void>();
  const openPicker = vi.fn();
  let handle!: (e: KeyboardEvent) => boolean;
  function Probe() {
    handle = useListShortcuts(act, openPicker);
    return null;
  }
  renderToStaticMarkup(createElement(TaggingContext.Provider, { value: tagging }, createElement(Probe)));
  return { tagging, act, openPicker, handle };
}

/** A keydown on `target`, as React hands it over. */
const key = (target: Element, k: string, mods: Partial<Record<"metaKey" | "ctrlKey" | "altKey" | "shiftKey", boolean>> = {}) => {
  const e = {
    key: k, target, metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, nativeEvent: {},
    defaultPrevented: false, preventDefault() { e.defaultPrevented = true; }, ...mods,
  };
  return e as unknown as KeyboardEvent & { defaultPrevented: boolean };
};
const el = (html: string, pick: string) => {
  const box = document.createElement("div");
  box.innerHTML = html;
  return box.querySelector(pick)!;
};

describe("useListShortcuts (spec §6)", () => {
  it("takes Cmd-A, Cmd-Z, T and R in the list, and says so", () => {
    const { tagging, act, openPicker, handle } = setup();
    const row = el('<div role="row" tabindex="0"></div>', "[role=row]");
    const t = key(row, "t");
    expect(handle(t)).toBe(true);
    expect(t.defaultPrevented).toBe(true);
    expect(openPicker).toHaveBeenCalledOnce();
    expect(handle(key(row, "a", { metaKey: true }))).toBe(true);
    expect(act).toHaveBeenCalledWith({ type: "all" });
    expect(handle(key(row, "z", { ctrlKey: true }))).toBe(true);
    expect(tagging.undoLast).toHaveBeenCalledOnce();
    expect(handle(key(row, "R"))).toBe(true);
    expect(tagging.refresh).toHaveBeenCalledOnce();
  });
  it("leaves other keys to the caller: Cmd-Shift-Z, T with Cmd, digits", () => {
    const { tagging, openPicker, handle } = setup();
    const row = el('<div role="row" tabindex="0"></div>', "[role=row]");
    expect(handle(key(row, "z", { metaKey: true, shiftKey: true }))).toBe(false);
    expect(handle(key(row, "t", { metaKey: true }))).toBe(false);
    expect(handle(key(row, "3"))).toBe(false);
    expect(tagging.undoLast).not.toHaveBeenCalled();
    expect(openPicker).not.toHaveBeenCalled();
  });
  it("never takes a key typed in the picker, a dialog or the confirmation (Task 11 carry)", () => {
    const { tagging, act, openPicker, handle } = setup();
    const inPicker = el('<div cmdk-root=""><div cmdk-item="">space/apollo</div></div>', "[cmdk-item]");
    const inDialog = el('<div role="dialog"><button>Close</button></div>', "button");
    const inConfirm = el('<div role="alertdialog"><button>Cancel</button></div>', "button");
    for (const target of [inPicker, inDialog, inConfirm]) {
      expect(handle(key(target, "t"))).toBe(false);
      expect(handle(key(target, "r"))).toBe(false);
      expect(handle(key(target, "z", { metaKey: true }))).toBe(false);
      expect(handle(key(target, "a", { ctrlKey: true }))).toBe(false);
    }
    expect(openPicker).not.toHaveBeenCalled();
    expect(tagging.undoLast).not.toHaveBeenCalled();
    expect(tagging.refresh).not.toHaveBeenCalled();
    expect(act).not.toHaveBeenCalled();
  });
});
