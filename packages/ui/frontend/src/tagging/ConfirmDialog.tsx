import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { confirmBody, confirmTitle } from "@/lib/toasts";

import { Rich } from "./ActionToast";
import { useReturnFocus } from "./useReturnFocus";
import { useTaggingContext } from "./useTagging";

/** The privacy confirmation (spec §6). It opens only when the server replies `confirm`, over the
 * picker if one is open, without closing it. Cancel has focus and Esc means Cancel; Remove
 * resends with confirm_ids set to the IDs the server named.
 *
 * Focus goes back to what had it when the dialog opened (the picker's input, the title, a row's
 * next control). This dialog has no Trigger, since it opens on a server reply, so useReturnFocus
 * remembers and restores it; when it has gone, or was a toast's button, focus goes to the open row
 * or the header's title instead. */
export function ConfirmDialog() {
  const tagging = useTaggingContext();
  const returnFocus = useReturnFocus();
  const c = tagging.confirm;
  const lp = c?.reply.loses_private ?? null;
  return (
    <AlertDialog open={c !== null && lp !== null} onOpenChange={(open) => { if (!open) tagging.answerConfirm(false); }}>
      {c && lp && (
        <AlertDialogContent data-testid="confirm-dialog" {...returnFocus}>
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
