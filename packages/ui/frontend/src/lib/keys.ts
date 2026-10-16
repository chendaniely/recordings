/** The shortcut guard (spec §6): shortcuts fire only when focus is in the list or the grid, and
 * never with Cmd, Ctrl or Alt unless the key map lists them, while composing, or in text. */

export interface KeyLike {
  key: string; metaKey: boolean; ctrlKey: boolean; altKey: boolean; shiftKey: boolean;
  isComposing?: boolean; target: EventTarget | null;
}

const TEXT_ENTRY = "input, textarea, select, [contenteditable]:not([contenteditable='false'])";

export function isTextEntry(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest(TEXT_ENTRY) !== null;
}

/** Cmd on a Mac, Ctrl elsewhere: the key map's "Cmd/Ctrl". */
export function commandKey(e: Pick<KeyLike, "metaKey" | "ctrlKey">): boolean {
  return e.metaKey || e.ctrlKey;
}

/** A bare shortcut: `T`, `R`, the digits. */
export function plainShortcut(e: KeyLike): boolean {
  return !e.metaKey && !e.ctrlKey && !e.altKey && !e.isComposing && !isTextEntry(e.target);
}

/** A key-map shortcut with Cmd/Ctrl: Cmd-A, Cmd-Z, Cmd+Space, Cmd+arrows. */
export function commandShortcut(e: KeyLike): boolean {
  return commandKey(e) && !e.altKey && !e.isComposing && !isTextEntry(e.target);
}
