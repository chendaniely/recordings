import { describe, expect, it, vi } from "vitest";

import { applyTheme, browserStorage, loadChoice, resolveTheme, saveChoice } from "./theme";

describe("theme", () => {
  it("follows the OS only in system mode", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("remembers a choice and ignores junk", () => {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    expect(loadChoice(storage)).toBe("system");
    saveChoice(storage, "dark");
    expect(loadChoice(storage)).toBe("dark");
    store.set("recordings.theme", "purple");
    expect(loadChoice(storage)).toBe("system");
  });

  it("survives storage that throws (private windows, blocked site data)", () => {
    const broken = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); } };
    expect(loadChoice(broken)).toBe("system");
    expect(() => saveChoice(broken, "light")).not.toThrow();
  });

  it("sets exactly one of the classes dark/light on <html>", () => {
    const root = document.documentElement;
    applyTheme(root, "dark");
    expect(root.classList.contains("dark")).toBe(true);
    expect(root.classList.contains("light")).toBe(false);
    applyTheme(root, "light");
    expect(root.classList.contains("light")).toBe(true);
    expect(root.classList.contains("dark")).toBe(false);
  });

  it("browserStorage is localStorage, or null where the browser forbids it", () => {
    expect(browserStorage()).toBe(window.localStorage);
    const spy = vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new Error("blocked");
    });
    try {
      expect(browserStorage()).toBeNull();
    } finally {
      spy.mockRestore();
    }
  });
});
