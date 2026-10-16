import { useRef } from "react";

import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { confirmBody, confirmTitle } from "@/lib/toasts";

import { Rich } from "./ActionToast";
import { focusFallback, inToaster } from "./toaster";
import { useTaggingContext } from "./useTagging";

/** The privacy confirmation (spec §6). It opens only when the server replies `confirm`, over the
 * picker if one is open, without closing it. Cancel has focus and Esc means Cancel; Remove
 * resends with confirm_ids set to the IDs the server named.
 *
 * Focus goes back to what had it when the dialog opened (the picker's input, the title, a row's
 * next control). Radix's Dialog only returns focus to its Trigger, and this dialog has none: it
 * opens on a server reply. So it is remembered here, before Radix moves focus to Cancel; when it
 * has gone, or was a toast's button (a dialog opened from a toast's Undo), focus goes to the open
 * row or the header's title instead (toaster.ts). */
export function ConfirmDialog() {
  const tagging = useTaggingContext();
  const back = useRef<HTMLElement | null>(null);
  const c = tagging.confirm;
  const lp = c?.reply.loses_private ?? null;
  return (
    <AlertDialog open={c !== null && lp !== null} onOpenChange={(open) => { if (!open) tagging.answerConfirm(false); }}>
      {c && lp && (
        <AlertDialogContent
          data-testid="confirm-dialog"
          onOpenAutoFocus={() => {
            back.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
          }}
          onCloseAutoFocus={(e) => {
            e.preventDefault();
            const el = back.current;
            back.current = null;
            const to = el?.isConnected && !inToaster(el) ? el : focusFallback();
            to?.focus({ preventScroll: true });
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle className="confirm-title"><Rich text={confirmTitle(c.request, lp, tagging.isPrivate, c.redo)} /></AlertDialogTitle>
            <AlertDialogDescription>{confirmBody(lp)}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => tagging.answerConfirm(true)}>Remove</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      )}
    </AlertDialog>
  );
}
