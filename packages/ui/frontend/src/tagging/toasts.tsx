import { toast } from "sonner";

import { cannotTagText, type TagStatus } from "@/lib/library";
import {
  STILL_WORKING, actionText, cellText, droppedText, failureLines, progressStart, progressText, selectFirstText,
  type ProgressKind, type Text,
} from "@/lib/toasts";
import type { TagRequest, TagResult } from "@/types";

import { ActionToast, AlertToast, ProgressToast } from "./ActionToast";

const forever = { duration: Infinity }; // our own timers dismiss them (plan decision 9)
const toastId = (request: TagRequest) => `tag-${request.request_id}`;

/** What each request's toast has said in the live region. Progress is announced at the start
 * and, by the result, at the end only (spec §6): the moving count stays out of it, and once
 * "Still working…" has been said, a later progress reply doesn't swap the text back. */
const announced = new Map<string, string>();

/** A request's own toast holds its progress, then "Still working…", then its result: one id, so
 * each replaces the one before, and dismissProgress closes whichever is showing. */
export function showProgress(request: TagRequest, done: number, total: number, kind: ProgressKind): void {
  const id = toastId(request);
  if (!announced.has(id)) announced.set(id, progressStart(total, kind));
  toast(<ProgressToast announced={announced.get(id)!} shown={progressText(done, total, kind)} />, { ...forever, id });
}

/** "Still working…" takes the progress toast's place: the same id (spec §6). */
export function showStillWorking(request: TagRequest): void {
  const id = toastId(request);
  announced.set(id, STILL_WORKING);
  toast(<ProgressToast announced={STILL_WORKING} shown={STILL_WORKING} />, { ...forever, id });
}

/** Closes a request's progress or "Still working…" toast: on a `confirm` reply, when the
 * connection is lost, and when a result has nothing to say. */
export function dismissProgress(request: TagRequest): void {
  announced.delete(toastId(request));
  toast.dismiss(toastId(request));
}

function showAction(id: string, text: Text, undo: { label: string; run: () => void } | null, onGone?: () => void): void {
  toast(<ActionToast id={id} text={text} undo={undo} />, { ...forever, id, onDismiss: onGone });
}

/** The toast an ending reply shows: the action with its Undo (an Undo's offers Redo, a Redo's
 * offers Undo; plan decision 7), and, when the batch stopped or was refused, an alert with the
 * fixed phrase. When nothing changed and nothing was already so, there is no action toast: the
 * alert speaks. */
export function showResult(request: TagRequest, reply: TagResult, saves: boolean, onUndo: (token: string) => void, redo = false): void {
  const id = toastId(request);
  announced.delete(id);
  const text = reply.status === "done" || reply.changed > 0 ? actionText(request, reply, saves, redo) : [];
  if (text.length > 0) {
    const token = reply.undo;
    const label = request.action === "undo" && !redo ? "Redo" : "Undo";
    showAction(id, text, token ? { label, run: () => onUndo(token) } : null);
  } else {
    toast.dismiss(id);
  }
  if (reply.status === "failed" && reply.message) showAlert(`stop-${request.request_id}`, [reply.message]);
  if (reply.failures.length > 0) showAlert(`failed-${request.request_id}`, failureLines(reply.failures));
}

/** Single-cell toggles in the grid share one running toast: "4 changes · Undo last". Each new
 * toast has its own key, so a late close of the last one can't swallow it. */
export function showCells(
  key: number, changes: number, visible: number, saves: boolean, onUndoLast: (() => void) | null, onGone: () => void,
): void {
  showAction(cellsId(key), [cellText(changes, saves, visible)], onUndoLast && { label: "Undo last", run: onUndoLast }, onGone);
}

export const cellsId = (key: number) => `grid-cells-${key}`;

export function showAlert(id: string, lines: string[]): void {
  toast(<AlertToast id={id} lines={lines} />, { ...forever, id });
}

export function showNote(id: string, text: Text): void {
  showAction(id, text, null);
}

let cannotTagShown = 0;

/** Why tagging is off, said again on each `T`, drop or Undo: a fresh toast each time, so it is
 * announced again, replacing the last. The archive still being opened is a moment, so an
 * ordinary toast that goes by itself; anything else is an alert that stays until dismissed. */
export function showCannotTag(status: TagStatus): void {
  toast.dismiss(`cannot-tag-${cannotTagShown}`);
  cannotTagShown += 1;
  const id = `cannot-tag-${cannotTagShown}`;
  if (status.opening) showNote(id, [cannotTagText(status)]);
  else showAlert(id, [cannotTagText(status)]);
}

let selectFirstShown = 0;

/** + New tag… clicked with nothing selected: it says so instead of opening nothing in silence. A
 * fresh toast each time, so it is announced again, replacing the last. */
export function showSelectFirst(): void {
  toast.dismiss(`select-first-${selectFirstShown}`);
  selectFirstShown += 1;
  const canHover = typeof window.matchMedia !== "function" || window.matchMedia("(hover: hover)").matches;
  showNote(`select-first-${selectFirstShown}`, [selectFirstText(canHover)]);
}

export function showDropped(dropped: number, inFlight: boolean): void {
  showAlert("connection-lost", [droppedText(dropped, inFlight)]);
}
