import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { useReturnFocus } from "@/tagging/useReturnFocus";

import type { ReleasePreview } from "../types";

/** "Let 'Coffee chat about grant timelines' (2026-10-23 14:05, 12 min) in?" The question's
 * title. A re-keyed match's preview has none, so it asks about "this recording" (§11.4). */
export function releaseQuestion(p: ReleasePreview): string {
  const when = [`${p.date} ${p.time}`.trim(), p.duration].filter(Boolean).join(", ");
  const what = p.title ? `'${p.title}'` : "this recording";
  return `Let ${what}${when ? ` (${when})` : ""} in?`;
}

/** The Release button this question is about (`data-release-for`, on the Sync screen's Kept out
 * row), where focus goes back when the dialog closes. */
export function releaseButton(plaudId: string): HTMLElement | null {
  return [...document.querySelectorAll<HTMLElement>("[data-release-for]")].find((b) => b.dataset.releaseFor === plaudId) ?? null;
}

/** Release asks first (2b spec §12.2; the fourth exception to "no confirmation dialogs"): it
 * turns "never download" into a copy kept for good. The title shows here and nowhere else, from
 * the preview this window alone was sent, as text: Phase 2's worker builds that preview, and
 * until then the dialog is drawn from fixtures only. Cancel has focus, and Esc means Cancel. It
 * opens on a reply, not from a Trigger, so useReturnFocus gives focus back to its Release button
 * when it closes (Radix alone would leave it on <body>). */
export function ReleaseDialog({ preview, onCancel, onLetIn }: { preview: ReleasePreview; onCancel: () => void; onLetIn: () => void }) {
  const returnFocus = useReturnFocus(() => releaseButton(preview.plaud_id));
  const next = preview.next_sync ? `at the next sync (${preview.next_sync})` : "at the next sync";
  return (
    <AlertDialog open onOpenChange={(open) => { if (!open) onCancel(); }}>
      <AlertDialogContent data-testid="release-dialog" {...returnFocus}>
        <AlertDialogHeader>
          <AlertDialogTitle className="confirm-title">{releaseQuestion(preview)}</AlertDialogTitle>
          <AlertDialogDescription>It downloads {next}, and then stays in the backups.</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction data-request="release" onClick={onLetIn}>Let it in</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
