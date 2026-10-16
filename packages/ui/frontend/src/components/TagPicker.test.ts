import { createElement, useState, type Context, type ReactNode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { LibraryRow, LibraryView, TagRequest } from "../types";

// The tagging context, faked with a real React context of the test's own, so a test can change
// what the picker reads (and Radix's onOpenChange can close it) the way App does.
const fake = vi.hoisted(() => ({ Context: null as unknown as Context<unknown> }));
vi.mock("@/tagging/useTagging", async () => {
  const { createContext, useContext } = await import("react");
  fake.Context = createContext<unknown>(null);
  return { useTaggingContext: () => useContext(fake.Context) };
});

import { AddTagsButton, PickerBody, TagPickerDialog, pickerTitle } from "./TagPicker";

describe("pickerTitle (spec §6: the title names the target)", () => {
  it("names one recording, or counts several", () => {
    const titles: Record<string, string> = { a: "Week 4", b: "Week 5" };
    expect(pickerTitle(["a"], (id) => titles[id])).toBe("Tag Week 4");
    expect(pickerTitle(["a", "b"], (id) => titles[id])).toBe("Tag 2 recordings");
    expect(pickerTitle(Array(1200).fill("a"), (id) => titles[id])).toBe("Tag 1,200 recordings");
  });
});

// cmdk measures its list and scrolls the highlighted item into view, and the popover's positioning
// watches its anchor; jsdom has none of these.
beforeAll(() => {
  const Observer = class { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } };
  globalThis.ResizeObserver ??= Observer as unknown as typeof ResizeObserver;
  globalThis.IntersectionObserver ??= Observer as unknown as typeof IntersectionObserver;
  Element.prototype.scrollIntoView ??= function scrollIntoView() {};
});

const row = (id: string, tags: string[]): LibraryRow => ({
  id, title: `Week ${id}`, recorded_at: "2026-10-06T14:00:00-07:00", when: "", duration: null, duration_ms: null,
  kind: "audio", sources: [], tags: tags.map((tag) => ({ tag, by: "you" as const })), private: tags.includes("private"),
  untagged: tags.length === 0,
});
const VIEW: LibraryView = {
  recordings: [row("a", ["talks", "school/course-101"]), row("b", ["talks"]), row("c", ["private"])],
  counts: { all: 3, untagged: 0 },
  tags: [
    { tag: "talks", count: 2, private: false },
    { tag: "school/course-101", count: 1, private: false },
    { tag: "private", count: 1, private: true },
  ],
  note_types: [{ note_type: "lecture", count: 0 }],
  problems: [], can_tag: true, cannot_tag_reason: null, saves: true,
};

type Call = [action: "add" | "remove", ids: string[], tags: string[]];
let calls: Call[];
let pending: TagRequest[];
let host: HTMLDivElement;
let root: Root;

function tagging(over: Record<string, unknown> = {}) {
  return {
    view: VIEW, sessionTags: [] as string[], pending, confirm: null, canTag: true, picker: null,
    isPrivate: (t: string) => VIEW.tags.some((x) => x.tag === t && x.private),
    tag: (action: "add" | "remove", ids: string[], tags: string[]) => { calls.push([action, ids, tags]); },
    openPicker: () => {}, closePicker: () => {},
    ...over,
  };
}
const provide = (value: unknown, child: ReactNode) => createElement(fake.Context.Provider, { value }, child);
const draw = (node: ReactNode) => flushSync(() => root.render(node));
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

const $ = <T extends Element = HTMLElement>(sel: string) => document.querySelector<T>(sel);
const $$ = (sel: string) => [...document.querySelectorAll<HTMLElement>(sel)];
const input = () => $<HTMLInputElement>("[data-testid=picker-input]")!;
const highlighted = () => $("[data-testid=picker-item][aria-selected=true]")?.dataset.tag ?? null;
const note = () => $("[data-testid=picker-note]")!.textContent;
const items = () => $$("[data-testid=picker-item]").map((el) => el.dataset.tag);

function type(text: string) {
  const box = input();
  flushSync(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(box, text);
    box.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
function press(key: string, init: KeyboardEventInit = {}, at: Element = input()) {
  flushSync(() => { at.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init })); });
}

beforeEach(() => {
  calls = [];
  pending = [];
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(() => {
  flushSync(() => root.unmount());
  document.body.replaceChildren();
});

describe("the picker's body (spec §6)", () => {
  const body = (ids = ["a", "b"], over: Record<string, unknown> = {}) =>
    draw(provide(tagging(over), createElement(PickerBody, { ids, title: "Tag 2 recordings" })));

  it("names its box after the target and describes its keys", () => {
    body();
    const box = input();
    expect(document.getElementById(box.getAttribute("aria-labelledby")!)?.textContent).toBe("Tag 2 recordings");
    expect(document.getElementById(box.getAttribute("aria-describedby")!)?.textContent).toContain("Shift+Delete remove");
  });

  it("highlights the first-ranked tag, and Enter on a partial name adds it, never Create", () => {
    body();
    type("cours");
    expect(highlighted()).toBe("school/course-101");
    expect($("[data-testid=picker-create]")).not.toBe(null); // offered, last
    press("Enter");
    expect(calls).toEqual([["add", ["a", "b"], ["school/course-101"]]]);
    expect(input().value).toBe(""); // the box clears, and the picker stays for the next tag
  });

  it("Enter right after typing acts on the new list, not the last one", async () => {
    // As a browser delivers them: the input event, its microtasks, then the key. The highlight is
    // cmdk's; React 19 renders the input and cmdk's re-highlight before the key arrives.
    body();
    const box = input();
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(box, "s");
    box.dispatchEvent(new Event("input", { bubbles: true }));
    await Promise.resolve();
    await Promise.resolve();
    expect(items()[0]).toBe("school/course-101"); // "talks", first before, is still listed, second
    box.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    expect(calls).toEqual([["add", ["a", "b"], ["school/course-101"]]]);
  });

  it("on a ✓ item, Enter adds nothing and says so", () => {
    body();
    type("talks");
    expect($("[data-tag=talks]")!.dataset.state).toBe("all");
    press("Enter");
    expect(calls).toEqual([]);
    expect(note()).toBe("Already on all 2");
  });

  it("counts a pending change at once, so a second Enter doesn't send it again", () => {
    pending = [{ request_id: "r1", action: "add", ids: ["a", "b"], tags: ["school/course-101"], confirm_ids: [] }];
    body();
    type("course");
    expect($("[data-tag='school/course-101']")!.dataset.state).toBe("all");
    press("Enter");
    expect(calls).toEqual([]);
  });

  it("Shift+Enter creates exactly the typed text, composed (NFC)", () => {
    body();
    type("  café ");
    press("Enter", { shiftKey: true });
    expect(calls).toEqual([["add", ["a", "b"], ["café"]]]);
    expect(input().value).toBe("");
  });

  it("Shift+Enter makes nothing while an IME is composing", () => {
    body();
    type("work");
    press("Enter", { shiftKey: true, isComposing: true });
    expect(calls).toEqual([]);
  });

  it("Shift+Enter on a name Create doesn't offer says why, and sends nothing", () => {
    body();
    type("Talks");
    expect($("[data-testid=picker-create]")).toBe(null); // never a case-variant
    press("Enter", { shiftKey: true });
    expect(note()).toBe("A tag with the same letters in another case exists already: Enter adds it.");
    type("notes/summary");
    press("Enter", { shiftKey: true });
    expect(note()).toBe("Note types come from prompt files: notes/<type> must name one the archive has.");
    expect(calls).toEqual([]);
  });

  it("Shift+Delete, or Cmd/Ctrl+Backspace, removes the highlighted ✓ or – tag, and nothing else", () => {
    body();
    type("talks");
    press("Delete", { shiftKey: true });
    type("course");
    expect($("[data-tag='school/course-101']")!.dataset.state).toBe("some");
    press("Backspace", { metaKey: true });
    press("Backspace", { ctrlKey: true });
    type("private"); // on neither target: nothing to remove
    press("Delete", { shiftKey: true });
    press("Backspace"); // plain Backspace edits the box
    expect(calls).toEqual([
      ["remove", ["a", "b"], ["talks"]],
      ["remove", ["a", "b"], ["school/course-101"]],
      ["remove", ["a", "b"], ["school/course-101"]],
    ]);
  });

  it("the × on an item removes with the mouse, keeps focus in the box, and never adds", () => {
    body();
    input().focus();
    const x = $("[data-tag=talks] [data-testid=picker-x]")!;
    expect(x.getAttribute("aria-hidden")).toBe("true"); // the keyboard's way is Shift+Delete
    const down = new MouseEvent("mousedown", { bubbles: true, cancelable: true });
    x.dispatchEvent(down);
    expect(down.defaultPrevented).toBe(true);
    flushSync(() => x.click());
    expect(calls).toEqual([["remove", ["a", "b"], ["talks"]]]);
    expect($("[data-tag=private] [data-testid=picker-x]")).toBe(null); // nothing to remove there
  });

  it("states each item in words, isolates its text left to right, and names it in full", () => {
    body();
    const talks = $("[data-tag=talks]")!;
    expect(talks.getAttribute("aria-label")).toBe("talks, on all 2");
    expect($("[data-tag='school/course-101']")!.getAttribute("aria-label")).toBe("school/course-101, on 1 of 2");
    expect($("[data-tag=private]")!.getAttribute("aria-label")).toBe("private");
    expect(talks.querySelector("bdi")?.getAttribute("dir")).toBe("ltr");
    expect(talks.querySelector(".picker-tag")?.getAttribute("title")).toBe("talks");
    type("אב/x");
    expect($("[data-testid=picker-create] bdi")?.textContent).toBe("אב/x");
  });

  it("groups private tags under a lock by the server's flags only", () => {
    body(["a", "b"], { sessionTags: ["private/new"] }); // made this session: the server hasn't said
    const groups = $$("[cmdk-group]").map((g) => [
      g.querySelector("[cmdk-group-heading]")?.textContent,
      [...g.querySelectorAll<HTMLElement>("[data-testid=picker-item]")].map((el) => el.dataset.tag),
    ]);
    expect(groups).toEqual([
      ["Tags", ["talks", "school/course-101", "notes/lecture", "private/new"]],
      ["Private", ["private"]],
    ]);
    type("priv"); // a rank tie goes to the locked group, so the first shown is the first ranked
    expect(items()).toEqual(["private", "private/new"]);
    expect(highlighted()).toBe("private");
  });
});

describe("the dialog form (T, the selection bar, a drop)", () => {
  type Target = { ids: string[]; title: string } | null;
  let open: (target: Target) => void;
  function Harness() {
    const [picker, setPicker] = useState<Target>(null);
    open = setPicker;
    return provide(tagging({ picker, closePicker: () => setPicker(null) }), createElement(TagPickerDialog));
  }
  const start = (focused: HTMLElement) => {
    draw(createElement(Harness));
    focused.focus();
    flushSync(() => open({ ids: ["a", "b"], title: "Tag 2 recordings" }));
  };

  it("is named by its title, and its body is the only cmdk root", () => {
    start(document.body.appendChild(document.createElement("button")));
    const dialog = $("[role=dialog]")!;
    expect(document.getElementById(dialog.getAttribute("aria-labelledby")!)?.textContent).toBe("Tag 2 recordings");
    expect(dialog.querySelectorAll("[cmdk-root]")).toHaveLength(1);
    expect(document.activeElement).toBe(input());
  });

  it("gives focus back to what had it when Esc closes it: Radix returns it only to a Trigger", async () => {
    const before = document.body.appendChild(document.createElement("button"));
    start(before);
    press("Escape");
    await tick(); // Radix's FocusScope hands focus on after a 0 ms timer
    expect($("[role=dialog]")).toBe(null);
    expect(document.activeElement).toBe(before);
  });

  it("falls back to the open row when what had focus has gone", async () => {
    const current = document.body.appendChild(document.createElement("div"));
    current.setAttribute("aria-current", "true");
    current.tabIndex = -1;
    const before = document.body.appendChild(document.createElement("button"));
    start(before);
    before.remove();
    press("Escape");
    await tick();
    expect(document.activeElement).toBe(current);
  });
});

describe("the + and its popover", () => {
  const plus = (onRowClick = () => {}) =>
    draw(provide(tagging(), createElement("div", { onClick: onRowClick },
      createElement(AddTagsButton, { id: "a", title: "Week a", place: "row" }))));

  it("is named for its recording, and a click opens the picker without reaching the row", () => {
    const rowClick = vi.fn();
    plus(rowClick);
    const button = $<HTMLButtonElement>("[data-testid=add-tag]")!;
    expect(button.getAttribute("aria-label")).toBe("Add tags to Week a");
    expect(button.dataset.control).toBe("add");
    expect(button.tabIndex).toBe(-1); // a row's controls are reached with Left and Right
    flushSync(() => button.click());
    expect(rowClick).not.toHaveBeenCalled();
    expect($("[role=dialog]")?.getAttribute("aria-label")).toBe("Tag Week a");
    expect(document.activeElement).toBe(input());
  });

  it("stays open while focus is in the confirmation, and closes on a focus anywhere else", () => {
    plus();
    flushSync(() => $<HTMLButtonElement>("[data-testid=add-tag]")!.click());
    const confirmation = document.body.appendChild(document.createElement("div"));
    confirmation.setAttribute("role", "alertdialog");
    const cancel = confirmation.appendChild(document.createElement("button"));
    flushSync(() => cancel.focus());
    expect($("[data-testid=picker-input]")).not.toBe(null);
    const elsewhere = document.body.appendChild(document.createElement("button"));
    flushSync(() => elsewhere.focus());
    expect($("[data-testid=picker-input]")).toBe(null);
  });
});
