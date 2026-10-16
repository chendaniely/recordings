import { useMemo, useRef } from "react";

import { focusFallback, inToaster } from "./toaster";

export interface ReturnFocus {
  onOpenAutoFocus: (event: Event) => void;
  onCloseAutoFocus: (event: Event) => void;
}

/** What had focus, and how to find it again in its row when a virtual list has drawn that row
 * anew: the row's data-id, and the element's own control kind and name. */
interface Spot { el: HTMLElement; id: string | null; wasRow: boolean; control: string | null; label: string | null }

function remember(el: HTMLElement): Spot {
  const row = el.closest<HTMLElement>("[data-id]");
  return {
    el, id: row?.dataset.id ?? null, wasRow: row === el, control: el.dataset.control ?? null, label: el.getAttribute("aria-label"),
  };
}

/** The element itself while it is still in the page; else, in a row with the same data-id, the
 * element of the same kind, control and name, or the row itself; else null. A virtual list
 * unmounts a row that scrolls away and draws new elements when it comes back. */
function refind(spot: Spot): HTMLElement | null {
  if (spot.el.isConnected) return spot.el;
  if (spot.id === null) return null;
  const row = [...document.querySelectorAll<HTMLElement>("[data-id]")].find((r) => r.dataset.id === spot.id);
  if (!row || spot.wasRow) return row ?? null;
  const same = [...row.querySelectorAll<HTMLElement>(spot.el.tagName)].find(
    (c) => (c.dataset.control ?? null) === spot.control && c.getAttribute("aria-label") === spot.label,
  );
  return same ?? row;
}

/** Focus goes back where it came from when a dialog closes (spec §6), for dialogs with no
 * Trigger: the picker's dialog form (opened by `T`, the selection bar or a drop) and the privacy
 * confirmation (opened by a server reply). Radix's Dialog and Alert Dialog return focus only to
 * their Trigger, so without this it lands on <body>.
 *
 * Spread the result on the dialog's Content. `onOpenAutoFocus` remembers what has focus, before
 * Radix moves it in; `onCloseAutoFocus` gives it back, or to the same control in its row drawn
 * again. When neither can take focus, or it was <body> or a toast's button (a dialog opened from a
 * toast's Undo), focus goes to the open row or the header's title instead (toaster.ts).
 *
 * `prefer`, read on close, names a place that wins when it finds one: the + a phone's sheet was
 * opened from, which a tap in Safari never focused. */
export function useReturnFocus(prefer?: () => HTMLElement | null): ReturnFocus {
  const back = useRef<Spot | null>(null);
  const preferred = useRef(prefer);
  preferred.current = prefer;
  return useMemo(() => ({
    onOpenAutoFocus: () => {
      const el = document.activeElement;
      back.current = el instanceof HTMLElement && el !== document.body ? remember(el) : null;
    },
    onCloseAutoFocus: (event: Event) => {
      event.preventDefault(); // Radix would focus the Trigger, which these dialogs don't have
      const spot = back.current;
      back.current = null;
      const found = preferred.current?.() ?? (spot ? refind(spot) : null);
      const to = found && !inToaster(found) ? found : null;
      to?.focus({ preventScroll: true });
      if (!to || document.activeElement !== to) focusFallback()?.focus({ preventScroll: true });
    },
  }), []);
}
