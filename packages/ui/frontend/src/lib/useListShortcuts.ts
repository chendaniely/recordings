import type { KeyboardEvent } from "react";

import { useTaggingContext } from "../tagging/useTagging";
import { commandShortcut, plainShortcut } from "./keys";
import type { SelAction } from "./selection";

/** A key typed in a dialog, the confirmation or the picker (a portal whose React events bubble
 * through the row that opened it) belongs there: `t` in the picker's box is text, and Cmd-Z there
 * undoes typing, never a tag (Task 11 carry). */
function inLayer(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest("[role=dialog], [role=alertdialog], [cmdk-root]") !== null;
}

/** The keys the Library list and the Tags grid share (spec §6, §7): Cmd/Ctrl+A selects every row
 * in the filter (only here, with focus in the list or the grid), Cmd/Ctrl+Z undoes the last
 * action, T opens the picker for the targets, R refreshes. Never in text, an IME's composition, a
 * dialog or the picker. The handler says whether it took the key, so the grid can go on to its
 * digits. */
export function useListShortcuts(act: (action: SelAction) => void, openPicker: () => void): (e: KeyboardEvent) => boolean {
  const tagging = useTaggingContext();
  return (e: KeyboardEvent) => {
    if (inLayer(e.target)) return false;
    const key = e.key.toLowerCase();
    if (commandShortcut(e) && key === "a") act({ type: "all" });
    else if (commandShortcut(e) && key === "z" && !e.shiftKey) tagging.undoLast();
    else if (plainShortcut(e) && key === "t") openPicker();
    else if (plainShortcut(e) && key === "r") tagging.refresh();
    else return false;
    e.preventDefault();
    return true;
  };
}
