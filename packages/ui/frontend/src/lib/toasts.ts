import type { LosesPrivate, TagRequest, TagResult } from "../types";

/** What toasts and the confirmation say (spec §6). A tag name is a `{ code }` part, drawn as
 * <code>, always as text, never as HTML. */
export type Text = (string | { code: string })[];

export const STILL_WORKING = "Still working…";
/** A click on the sidebar's + New tag… with nothing selected (Task 13 fix round 1). */
export const SELECT_FIRST = "Select recordings first, or drag them here";
const n = (count: number) => count.toLocaleString("en");
const recordings = (count: number) => `${n(count)} recording${count === 1 ? "" : "s"}`;

export function textOf(text: Text): string {
  return text.map((part) => (typeof part === "string" ? part : part.code)).join("");
}

function listed(tags: readonly string[]): Text {
  return tags.flatMap((tag, i) => (i === 0 ? [{ code: tag }] : [", ", { code: tag }]));
}

/** An Undo of an Undo's own token is a Redo (plan decision 7): "Redone on N". */
export function actionText(request: TagRequest, reply: TagResult, saves: boolean, redo = false): Text {
  let text: Text;
  const tags = request.tags;
  // Nothing changed and nothing was already so: the writes failed or were refused, and the
  // failure alert speaks. Never "Already on…" for a write that did not happen.
  if (reply.changed === 0 && (request.action === "undo" || reply.unchanged === 0)) return [];
  if (request.action === "undo") text = [`${redo ? "Redone" : "Undone"} on ${recordings(reply.changed)}`];
  else if (reply.changed === 0) {
    // Only what the server counted as already so: "Already on all 12", "Already off 3 of 12".
    const of = reply.unchanged;
    const where = request.action === "add" ? "on" : "off";
    if (of < request.ids.length) text = [`Already ${where} ${n(of)} of ${n(request.ids.length)}`];
    else text = [of === 1 ? `Already ${where} it` : `Already ${where} all ${n(of)}`];
  } else if (tags.length === 1) {
    text = request.action === "add"
      ? ["Added ", { code: tags[0] }, ` to ${recordings(reply.changed)}`]
      : ["Removed ", { code: tags[0] }, ` from ${recordings(reply.changed)}`];
  } else {
    text = [`${request.action === "add" ? "Added" : "Removed"} ${tags.length} tags ${request.action === "add" ? "to" : "from"} ${recordings(reply.changed)}`];
  }
  if (reply.now_visible > 0) text.push(` · ${n(reply.now_visible)} now visible to external agents`);
  if (request.action === "remove" && tags.some((t) => t.startsWith("notes/"))) text.push(" · auto-pick won't add it back");
  if (!saves) text.push(" · Demo: changes aren't saved");
  return text;
}

/** Partial failures, grouped by reason, with counts: "3 edited outside the app: run …". */
export function failureLines(failures: readonly { id: string; reason: string }[]): string[] {
  const counts = new Map<string, number>();
  for (const f of failures) counts.set(f.reason, (counts.get(f.reason) ?? 0) + 1);
  return [...counts].map(([reason, count]) => `${n(count)} ${reason}`);
}

export function confirmTitle(request: TagRequest, lp: LosesPrivate, isPrivate: (tag: string) => boolean, redo = false): Text {
  const count = recordings(lp.ids.length);
  if (request.action === "undo") return [`${redo ? "Redo" : "Undo"} will remove `, { code: "private" }, ` from ${count}?`];
  const named = request.tags.filter(isPrivate);
  return ["Remove ", ...listed(named.length ? named : ["private"]), ` from ${count}?`];
}

export function confirmBody(lp: LosesPrivate): string {
  const parts: string[] = [];
  if (lp.visible > 0) {
    parts.push(`${n(lp.visible)} ${lp.visible === 1 ? "becomes" : "become"} visible to external agents such as Claude Code.`);
  }
  if (lp.untagged > 0) {
    const one = lp.untagged === 1;
    parts.push(`${n(lp.untagged)} will have no tags left, and ${one ? "stays" : "stay"} hidden until ${one ? "its" : "their"} next tag.`);
  }
  return parts.join(" ");
}

/** What a batch is doing, for its progress toast. A removal never reads "Removing 2,000
 * recordings", which sounds like deleting them. */
export type ProgressKind = "add" | "remove" | "undo" | "redo";

export function progressKind(request: TagRequest, redo: boolean): ProgressKind {
  return request.action === "undo" ? (redo ? "redo" : "undo") : request.action;
}

const START: Record<ProgressKind, string> = { add: "Tagging", remove: "Removing tags from", undo: "Undoing on", redo: "Redoing on" };
const RUNNING: Record<ProgressKind, string> = { add: "Tagging", remove: "Removing tags:", undo: "Undoing:", redo: "Redoing:" };

/** What the live region says once, at the start: "Tagging 2,000 recordings…". */
export function progressStart(total: number, kind: ProgressKind): string {
  return `${START[kind]} ${recordings(total)}…`;
}

/** The moving count, shown outside the live region: "Tagging 1,200 of 2,000…". */
export function progressText(done: number, total: number, kind: ProgressKind = "add"): string {
  return `${RUNNING[kind]} ${n(done)} of ${n(total)}…`;
}

/** The Tags grid's merged toast for single-cell toggles: "4 changes · Undo last". */
export function cellText(changes: number, saves = true): string {
  return `${n(changes)} change${changes === 1 ? "" : "s"}${saves ? "" : " · Demo: changes aren't saved"}`;
}

export function droppedText(dropped: number, inFlight: boolean): string {
  if (dropped === 0 && !inFlight) return "Lost the connection. Reload to keep working.";
  const queued = dropped > 0 ? `: ${n(dropped)} queued change${dropped === 1 ? " was" : "s were"} dropped.` : ".";
  const sent = inFlight ? " The change already sent was applied once or not at all; reload to see." : " Reload to keep working.";
  return `Lost the connection${queued}${sent}`;
}
