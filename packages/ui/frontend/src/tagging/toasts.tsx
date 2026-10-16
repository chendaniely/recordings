import { toast } from "sonner";

import { STILL_WORKING, actionText, cellText, droppedText, failureLines, progressText, type Text } from "@/lib/toasts";
import type { TagRequest, TagResult } from "@/types";

import { ActionToast, AlertToast, ProgressToast } from "./ActionToast";

const forever = { duration: Infinity }; // our own timers dismiss them (plan decision 9)
const toastId = (request: TagRequest) => `tag-${request.request_id}`;

/** A request's own toast holds its progress, then "Still working…", then its result: one id, so
 * each replaces the one before, and dismissProgress closes whichever is showing. */
export function showProgress(request: TagRequest, done: number, total: number): void {
  const announced = `Tagging ${total.toLocaleString("en")} recordings…`;
  toast(<ProgressToast announced={announced} shown={progressText(done, total)} />, { ...forever, id: toastId(request) });
}

/** "Still working…" takes the progress toast's place: the same id (spec §6). */
export function showStillWorking(request: TagRequest): void {
  toast(<ProgressToast announced={STILL_WORKING} shown={STILL_WORKING} />, { ...forever, id: toastId(request) });
}

/** Closes a request's progress or "Still working…" toast: on a `confirm` reply, when the
 * connection is lost, and when a result has nothing to say. */
export function dismissProgress(request: TagRequest): void {
  toast.dismiss(toastId(request));
}

function showAction(id: string, text: Text, undo: { label: string; run: () => void } | null, onGone?: () => void): void {
  toast(<ActionToast id={id} text={text} undo={undo} />, { ...forever, id, onDismiss: onGone });
}

/** The toast an ending reply shows: the action with its Undo (or Redo, for an Undo; plan
 * decision 7), and, when the batch stopped or was refused, an alert with the fixed phrase. When
 * nothing changed and nothing was already so, there is no action toast: the alert speaks. */
export function showResult(request: TagRequest, reply: TagResult, saves: boolean, onUndo: (token: string) => void): void {
  const id = toastId(request);
  const text = reply.status === "done" || reply.changed > 0 ? actionText(request, reply, saves) : [];
  if (text.length > 0) {
    const token = reply.undo;
    const undo = token ? { label: request.action === "undo" ? "Redo" : "Undo", run: () => onUndo(token) } : null;
    showAction(id, text, undo);
  } else {
    toast.dismiss(id);
  }
  if (reply.status === "failed" && reply.message) showAlert(`stop-${request.request_id}`, [reply.message]);
  if (reply.failures.length > 0) showAlert(`failed-${request.request_id}`, failureLines(reply.failures));
}

/** Single-cell toggles in the grid share one running toast: "4 changes · Undo last". Each new
 * toast has its own key, so a late close of the last one can't swallow it. */
export function showCells(key: number, changes: number, onUndoLast: (() => void) | null, onGone: () => void): void {
  showAction(cellsId(key), [cellText(changes)], onUndoLast && { label: "Undo last", run: onUndoLast }, onGone);
}

export const cellsId = (key: number) => `grid-cells-${key}`;

export function showAlert(id: string, lines: string[]): void {
  toast(<AlertToast id={id} lines={lines} />, { ...forever, id });
}

export function showNote(id: string, text: Text): void {
  showAction(id, text, null);
}

export function showDropped(dropped: number, inFlight: boolean): void {
  showAlert("connection-lost", [droppedText(dropped, inFlight)]);
}
