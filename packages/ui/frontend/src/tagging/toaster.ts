/** The toaster as a whole (spec §6; plan decision 9). Sonner draws each toast as an `<li
 * tabindex=0>` in an `<ol data-sonner-toaster>`, which Alt+T focuses. So a pointer or focus
 * anywhere in that list (an older toast in an expanded stack, a toast's padding, the list
 * itself) holds every toast's timer: one shared, counted pause that every ActionToast obeys. And
 * the toaster remembers what had focus before it, so a toast's Undo, Redo or Dismiss can give
 * focus back to the page instead of leaving it on <body> when the toast goes. */

const TOASTER = "[data-sonner-toaster]";

export interface Pausable { pause(): void; resume(): void }

export function inToaster(node: EventTarget | null): boolean {
  return node instanceof Element && node.closest(TOASTER) !== null;
}

export function createToasterPause() {
  let hovered = false;
  let focused = false;
  const timers = new Set<Pausable>();
  const held = () => hovered || focused;
  const set = (change: () => void) => {
    const before = held();
    change();
    const now = held();
    if (now === before) return;
    for (const timer of timers) {
      if (now) timer.pause();
      else timer.resume();
    }
  };
  return {
    hover(on: boolean) { set(() => { hovered = on; }); },
    focus(on: boolean) { set(() => { focused = on; }); },
    /** A toast's timer, held at once if the toaster is held; the returned function forgets it. */
    watch(timer: Pausable): () => void {
      timers.add(timer);
      if (held()) timer.pause();
      return () => { timers.delete(timer); };
    },
  };
}

export const toasterPause = createToasterPause();

let outside: HTMLElement | null = null; // what last had focus outside the toaster

/** Where focus goes when what had it has gone: the open row, then the recording header's title. */
export function focusFallback(): HTMLElement | null {
  return document.querySelector<HTMLElement>('[aria-current="true"], .row.on')
    ?? document.querySelector<HTMLElement>('[data-testid="recording-header"] h1');
}

/** Before a toast's Undo, Redo or Dismiss takes it away: if focus is in the toaster, give it back
 * to what had it before, or to the fallback when that has gone. A mouse user elsewhere keeps
 * their focus. */
export function leaveToaster(): void {
  if (!inToaster(document.activeElement)) return;
  const back = outside?.isConnected ? outside : focusFallback();
  back?.focus({ preventScroll: true });
}

/** Document listeners for the pause and the focus memory; returns their removal. Mounted once, by
 * useTagging, which App calls. */
export function watchToaster(doc: Document = document, pause = toasterPause): () => void {
  const over = (e: MouseEvent) => pause.hover(inToaster(e.target));
  const out = (e: MouseEvent) => {
    if (e.relatedTarget === null) pause.hover(false); // the pointer left the window
  };
  const focusIn = (e: FocusEvent) => {
    const inside = inToaster(e.target);
    pause.focus(inside);
    if (!inside && e.target instanceof HTMLElement) outside = e.target;
  };
  const focusOut = (e: FocusEvent) => pause.focus(inToaster(e.relatedTarget));
  doc.addEventListener("mouseover", over);
  doc.addEventListener("mouseout", out);
  doc.addEventListener("focusin", focusIn);
  doc.addEventListener("focusout", focusOut);
  return () => {
    doc.removeEventListener("mouseover", over);
    doc.removeEventListener("mouseout", out);
    doc.removeEventListener("focusin", focusIn);
    doc.removeEventListener("focusout", focusOut);
  };
}
