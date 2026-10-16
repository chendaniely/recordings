import { useRef } from "react";

import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { confirmBody, confirmTitle } from "@/lib/toasts";

import { Rich } from "./ActionToast";
import { useReturnFocus } from "./useReturnFocus";
import { useTaggingContext, type ConfirmState } from "./useTagging";

/** The × whose removal the server asked to confirm, found again: in the header, or in its row by
 * the row's id (a virtual list may have drawn it anew), by its name. It handed focus on to its
 * next control as it sent the removal (TagChips), so what had focus when the dialog opened is the
 * wrong place to go back to after Cancel (Task 15 fix round 1, item 8). After Remove the chip is
 * gone, and focus goes back to what had it. */
export function removalX(c: ConfirmState | null): HTMLElement | null {
  if (!c || c.request.action !== "remove" || c.request.ids.length !== 1 || c.request.tags.length !== 1) return null;
  const id = c.request.ids[0];
  const where = c.origin.kind === "header"
    ? document.querySelector<HTMLElement>('[data-testid="recording-header"]')
    : c.origin.kind === "chip"
      ? [...document.querySelectorAll<HTMLElement>("[role=row][data-id]")].find((r) => r.dataset.id === id)
      : null;
  const name = `Remove tag ${c.request.tags[0]} from `;
  const xs = [...(where?.querySelectorAll<HTMLElement>("[data-control=remove]") ?? [])];
  return xs.find((x) => x.getAttribute("aria-label")?.startsWith(name)) ?? null;
}

/** The privacy confirmation (spec §6). It opens only when the server replies `confirm`, over the
 * picker if one is open, without closing it. Cancel has focus and Esc means Cancel; Remove
 * resends with confirm_ids set to the IDs the server named.
 *
 * Focus goes back to what had it when the dialog opened (the picker's input, the title, a row's
 * next control). This dialog has no Trigger, since it opens on a server reply, so useReturnFocus
 * remembers and restores it; when it has gone, or was a toast's button, focus goes to the open row
 * or the header's title instead. A × whose removal was cancelled gets focus back itself (removalX). */
export function ConfirmDialog() {
  const tagging = useTaggingContext();
  const c = tagging.confirm;
  const last = useRef<ConfirmState | null>(null);
  if (c) last.current = c;
  const returnFocus = useReturnFocus(() => removalX(last.current));
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
