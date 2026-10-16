import { useMemo, useRef } from "react";

import { focusFallback, inToaster } from "./toaster";

export interface ReturnFocus {
  onOpenAutoFocus: (event: Event) => void;
  onCloseAutoFocus: (event: Event) => void;
}

/** Focus goes back where it came from when a dialog closes (spec §6), for dialogs with no
 * Trigger: the picker's dialog form (opened by `T`, the selection bar or a drop) and the privacy
 * confirmation (opened by a server reply). Radix's Dialog and Alert Dialog return focus only to
 * their Trigger, so without this it lands on <body>.
 *
 * Spread the result on the dialog's Content. `onOpenAutoFocus` remembers what has focus, before
 * Radix moves it in; `onCloseAutoFocus` gives it back. When that has gone, was <body>, or was a
 * toast's button (a dialog opened from a toast's Undo), focus goes to the open row or the
 * header's title instead (toaster.ts). */
export function useReturnFocus(): ReturnFocus {
  const back = useRef<HTMLElement | null>(null);
  return useMemo(() => ({
    onOpenAutoFocus: () => {
      const el = document.activeElement;
      back.current = el instanceof HTMLElement && el !== document.body ? el : null;
    },
    onCloseAutoFocus: (event: Event) => {
      event.preventDefault(); // Radix would focus the Trigger, which these dialogs don't have
      const el = back.current;
      back.current = null;
      const to = el?.isConnected && !inToaster(el) ? el : focusFallback();
      to?.focus({ preventScroll: true });
    },
  }), []);
}
