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

import { AddTagsButton, PickerBody, TagPickerDialog, keysLine, pickerTitle } from "./TagPicker";

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
  const heading = createElement("div", { className: "picker-title" }, "Tag 2 recordings");
  const body = (ids = ["a", "b"], over: Record<string, unknown> = {}) =>
    draw(provide(tagging(over), createElement(PickerBody, { ids, heading })));
  const choose = (key = "ArrowDown") => press(key); // moving the highlight is a choice

  it("names its box once, apart from the title, and describes its keys", () => {
    body();
    const box = input();
    expect(document.getElementById(box.getAttribute("aria-labelledby")!)?.textContent).toBe("Find or create a tag");
    expect(document.getElementById(box.getAttribute("aria-describedby")!)?.textContent).toBe(keysLine(false));
    expect(document.body.textContent!.split("Tag 2 recordings")).toHaveLength(2); // the title, once
    const list = $("[cmdk-list]")!;
    const groups = $$("[cmdk-group-heading]").map((h) => h.textContent);
    expect(groups).not.toContain(list.getAttribute("aria-label")); // no group named as its listbox
  });

  it("says Shift+Delete, and ⌘⌫ on a Mac or Ctrl+Backspace elsewhere, removes", () => {
    expect(keysLine(true)).toBe("Enter add · Shift+Enter create · Shift+Delete or ⌘⌫ remove · Esc close");
    expect(keysLine(false)).toBe("Enter add · Shift+Enter create · Shift+Delete or Ctrl+Backspace remove · Esc close");
  });

  it("with an empty box, Enter adds only an item the user moved to (ruled: never the top one unasked)", () => {
    body();
    press("Enter"); // right after opening
    expect(calls).toEqual([]);
    expect(note()).toBe("Type a tag name, or use the arrows");
    choose(); // to the second item
    expect(highlighted()).toBe("school/course-101");
    press("Enter");
    expect(calls).toEqual([["add", ["a", "b"], ["school/course-101"]]]);
    press("Enter"); // after an add, the choice is spent
    expect(calls).toHaveLength(1);
    expect(note()).toBe("Type a tag name, or use the arrows");
  });

  it("Page Up and Down move a page at a time (cmdk has no paging), and are a choice", () => {
    body();
    choose("PageDown"); // fewer than a page: to the last item
    expect(highlighted()).toBe("private");
    choose("PageUp");
    expect(highlighted()).toBe("talks");
    choose("PageDown");
    press("Enter");
    expect(calls).toEqual([["add", ["a", "b"], ["private"]]]);
  });

  it("a second Enter on the box an add cleared adds nothing", () => {
    body();
    type("cours");
    press("Enter");
    press("Enter");
    expect(calls).toEqual([["add", ["a", "b"], ["school/course-101"]]]);
    type("x"); // a new search spends any choice too
    type("");
    press("Enter");
    expect(calls).toHaveLength(1);
  });

  it("pointer movement over the list is a choice too", async () => {
    body();
    const item = $("[data-tag='notes/lecture']")!;
    item.dispatchEvent(new PointerEvent("pointermove", { bubbles: true }));
    await tick(); // a pointer move's updates are continuous, not discrete: React renders them in a task
    expect(highlighted()).toBe("notes/lecture");
    press("Enter");
    expect(calls).toEqual([["add", ["a", "b"], ["notes/lecture"]]]);
  });

  it("plain Enter with nothing listed says why, as Shift+Enter does", () => {
    body();
    type("notes/summary");
    press("Enter");
    expect(note()).toBe("Note types come from prompt files: notes/<type> must name one the archive has.");
    type("a//b");
    press("Enter");
    expect(note()).toBe("A folder part is empty, or starts or ends with a space.");
    expect(calls).toEqual([]);
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

  it("Shift+Delete removes the highlighted ✓ or – tag, and nothing else", () => {
    body();
    type("talks");
    press("Delete", { shiftKey: true });
    type("course");
    expect($("[data-tag='school/course-101']")!.dataset.state).toBe("some");
    press("Delete", { shiftKey: true });
    type("private"); // on neither target: nothing to remove
    press("Delete", { shiftKey: true });
    press("Backspace"); // plain Backspace edits the box
    expect(calls).toEqual([
      ["remove", ["a", "b"], ["talks"]],
      ["remove", ["a", "b"], ["school/course-101"]],
    ]);
  });

  it("Cmd/Ctrl+Backspace edits text in the box, and removes only from an empty box", () => {
    body();
    type("course");
    const edit = new KeyboardEvent("keydown", { key: "Backspace", metaKey: true, bubbles: true, cancelable: true });
    flushSync(() => { input().dispatchEvent(edit); });
    expect(edit.defaultPrevented).toBe(false); // the browser deletes to the line's start
    expect(calls).toEqual([]);
    type("");
    press("Backspace", { metaKey: true }); // empty, but nothing chosen yet
    expect(calls).toEqual([]);
    choose("Home");
    expect(highlighted()).toBe("talks");
    press("Backspace", { metaKey: true });
    press("Backspace", { ctrlKey: true });
    expect(calls).toEqual([["remove", ["a", "b"], ["talks"]], ["remove", ["a", "b"], ["talks"]]]);
  });

  it("with an empty box, Shift+Delete removes only an item the user moved to", () => {
    body();
    press("Delete", { shiftKey: true }); // "talks" is highlighted, but nobody chose it
    expect(calls).toEqual([]);
    expect(note()).toBe("Type a tag name, or use the arrows");
    choose("End"); // talks, school/course-101, notes/lecture, then the locked group's private
    choose("ArrowUp");
    choose("ArrowUp");
    expect(highlighted()).toBe("school/course-101");
    press("Delete", { shiftKey: true });
    expect(calls).toEqual([["remove", ["a", "b"], ["school/course-101"]]]);
  });

  it("keeps a tag it removed listed, as none, when its last use goes (with the server's flag)", () => {
    body(["c"]);
    type("private");
    press("Delete", { shiftKey: true });
    expect(calls).toEqual([["remove", ["c"], ["private"]]]);
    // The server's reply: no recording has it now, so its recount drops the tag.
    const after: LibraryView = {
      ...VIEW,
      recordings: VIEW.recordings.map((r) => (r.id === "c" ? { ...r, tags: [], private: false } : r)),
      tags: VIEW.tags.filter((t) => t.tag !== "private"),
    };
    body(["c"], { view: after });
    const item = $("[data-tag=private]")!;
    expect(item.dataset.state).toBe("none");
    expect(item.closest("[cmdk-group]")?.querySelector("[cmdk-group-heading]")?.textContent).toBe("Private");
    expect(highlighted()).toBe("private"); // so a second Shift+Delete can't move on to another tag
    press("Delete", { shiftKey: true });
    expect(calls).toHaveLength(1);
  });

  it("points the box's aria-activedescendant at the highlighted item as it moves", async () => {
    body();
    const box = input();
    const active = async () => {
      await Promise.resolve(); // the mirror runs as a mutation observer, in a microtask
      return [box.getAttribute("aria-activedescendant"), $("[cmdk-item][aria-selected=true]")?.id ?? null];
    };
    const [opened, openedItem] = await active();
    expect(opened).toBe(openedItem);
    expect(opened).not.toBe(null);
    type("cours");
    const [typed, typedItem] = await active();
    expect(typed).toBe(typedItem);
    press("Enter");
    const [cleared, clearedItem] = await active();
    expect(cleared).toBe(clearedItem);
    type("zzzz/..");
    expect(await active()).toEqual([null, null]); // nothing listed: no active descendant
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

  it("is named by its visible title, once, and its body is the only cmdk root", () => {
    start(document.body.appendChild(document.createElement("button")));
    const dialog = $("[role=dialog]")!;
    const title = document.getElementById(dialog.getAttribute("aria-labelledby")!)!;
    expect(title.textContent).toBe("Tag 2 recordings");
    expect(title.closest("[aria-hidden=true], .sr-only")).toBe(null);
    expect(document.body.textContent!.split("Tag 2 recordings")).toHaveLength(2);
    expect(dialog.querySelectorAll("[cmdk-root]")).toHaveLength(1);
    expect(document.activeElement).toBe(input());
  });

  it("adds nothing on Enter right after opening", () => {
    start(document.body.appendChild(document.createElement("button")));
    press("Enter");
    expect(calls).toEqual([]);
    expect(note()).toBe("Type a tag name, or use the arrows");
  });

  it("stays open on an Esc an IME is composing with", () => {
    start(document.body.appendChild(document.createElement("button")));
    press("Escape", { isComposing: true });
    expect($("[role=dialog]")).not.toBe(null);
    press("Escape");
    expect($("[role=dialog]")).toBe(null);
  });

  it("finds a row a virtual list drew again, by its data-id, before falling back", async () => {
    const draw = () => {
      const row = document.body.appendChild(document.createElement("div"));
      row.dataset.id = "r1";
      const plus = row.appendChild(document.createElement("button"));
      plus.dataset.control = "add";
      plus.setAttribute("aria-label", "Add tags to Week 1");
      const other = row.insertBefore(document.createElement("button"), plus);
      other.dataset.control = "remove";
      other.setAttribute("aria-label", "Remove tag talks from Week 1");
      return { row, plus };
    };
    const current = document.body.appendChild(document.createElement("div"));
    current.setAttribute("aria-current", "true");
    current.tabIndex = -1;
    const first = draw();
    start(first.plus);
    first.row.remove(); // scrolled away and back: the same row, drawn again
    const again = draw();
    press("Escape");
    await tick();
    expect(document.activeElement).toBe(again.plus);
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
    const dialog = $("[role=dialog]")!;
    expect(document.getElementById(dialog.getAttribute("aria-labelledby")!)?.textContent).toBe("Tag Week a");
    expect(document.activeElement).toBe(input());
  });

  it("reports every open and close, so a virtual list can keep its row drawn", () => {
    const changes: boolean[] = [];
    draw(provide(tagging(), createElement(AddTagsButton, { id: "a", title: "Week a", place: "row", onOpenChange: (o: boolean) => changes.push(o) })));
    flushSync(() => $<HTMLButtonElement>("[data-testid=add-tag]")!.click());
    press("Escape", { isComposing: true }); // an IME's Esc: still open
    expect($("[data-testid=picker-input]")).not.toBe(null);
    press("Escape");
    expect($("[data-testid=picker-input]")).toBe(null);
    flushSync(() => $<HTMLButtonElement>("[data-testid=add-tag]")!.click());
    flushSync(() => $<HTMLButtonElement>("[data-testid=add-tag]")!.click());
    expect(changes).toEqual([true, false, true, false]);
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
