/** The shortcut guard (spec §6): shortcuts fire only when focus is in the list or the grid, and
 * never with Cmd, Ctrl or Alt unless the key map lists them, while composing, or in text. */

export interface KeyLike {
  key: string; metaKey: boolean; ctrlKey: boolean; altKey: boolean; shiftKey: boolean;
  isComposing?: boolean; target: EventTarget | null;
  nativeEvent?: { isComposing?: boolean }; keyCode?: number; // a React event has these, not isComposing
}

// Text-like inputs only: a checkbox, radio, button, range, colour or file input takes no typing.
const NON_TEXT = ["checkbox", "radio", "button", "submit", "reset", "range", "color", "file", "image"];
const TEXT_ENTRY = `input${NON_TEXT.map((t) => `:not([type='${t}'])`).join("")}, textarea, select, [contenteditable]:not([contenteditable='false'])`;

/** An IME is composing: the browser's own flag, a React event's nativeEvent, or keyCode 229. */
export function composing(e: Pick<KeyLike, "isComposing" | "nativeEvent" | "keyCode">): boolean {
  return Boolean(e.isComposing || e.nativeEvent?.isComposing || e.keyCode === 229);
}

export function isTextEntry(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest(TEXT_ENTRY) !== null;
}

interface NavigatorLike { platform?: string; userAgentData?: { platform?: string } }

/** A Mac, iPad or iPhone, where hints name the Cmd key ⌘. The newer userAgentData first, then
 * the older (deprecated, still everywhere) navigator.platform. */
export function isMac(nav: NavigatorLike = navigator as NavigatorLike): boolean {
  return /mac|iphone|ipad|ipod/i.test(nav.userAgentData?.platform || nav.platform || "");
}

/** Cmd on a Mac, Ctrl elsewhere: the key map's "Cmd/Ctrl". */
export function commandKey(e: Pick<KeyLike, "metaKey" | "ctrlKey">): boolean {
  return e.metaKey || e.ctrlKey;
}

/** A bare shortcut: `T`, `R`, the digits. */
export function plainShortcut(e: KeyLike): boolean {
  return !e.metaKey && !e.ctrlKey && !e.altKey && !composing(e) && !isTextEntry(e.target);
}

/** A key-map shortcut with Cmd/Ctrl: Cmd-A, Cmd-Z, Cmd+Space, Cmd+arrows. */
export function commandShortcut(e: KeyLike): boolean {
  return commandKey(e) && !e.altKey && !composing(e) && !isTextEntry(e.target);
}

/** Toggle the focused row: plain `X`, or Cmd/Ctrl+Space. On a Mac, Cmd+Space (Spotlight) and
 * Ctrl+Space (input source) usually never reach the page, so `X` is the dependable one. */
export function toggleFocusedKey(e: KeyLike): boolean {
  if (plainShortcut(e)) return !e.shiftKey && e.key.toLowerCase() === "x";
  return e.key === " " && commandShortcut(e);
}
