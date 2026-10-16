import type { LosesPrivate, TagRequest, TagResult } from "../types";

/** What toasts and the confirmation say (spec §6). A tag name is a `{ code }` part, drawn as
 * <code>, always as text, never as HTML. */
export type Text = (string | { code: string })[];

export const STILL_WORKING = "Still working…";
const n = (count: number) => count.toLocaleString("en");
const recordings = (count: number) => `${n(count)} recording${count === 1 ? "" : "s"}`;

export function textOf(text: Text): string {
  return text.map((part) => (typeof part === "string" ? part : part.code)).join("");
}

function listed(tags: readonly string[]): Text {
  return tags.flatMap((tag, i) => (i === 0 ? [{ code: tag }] : [", ", { code: tag }]));
}

export function actionText(request: TagRequest, reply: TagResult, saves: boolean): Text {
  let text: Text;
  const tags = request.tags;
  if (request.action === "undo") text = [`Undone on ${recordings(reply.changed)}`];
  else if (reply.changed === 0) {
    // Nothing changed: an add says "Already on…", a removal "Already off…", never "0 recordings".
    const of = reply.unchanged || request.ids.length;
    const where = request.action === "add" ? "on" : "off";
    text = [of === 1 ? `Already ${where} it` : `Already ${where} all ${n(of)}`];
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

export function confirmTitle(request: TagRequest, lp: LosesPrivate, isPrivate: (tag: string) => boolean): Text {
  const count = recordings(lp.ids.length);
  if (request.action === "undo") return ["Undo will remove ", { code: "private" }, ` from ${count}?`];
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

export function progressText(done: number, total: number): string {
  return `Tagging ${n(done)} of ${n(total)}…`;
}

/** The Tags grid's merged toast for single-cell toggles: "4 changes · Undo last". */
export function cellText(changes: number): string {
  return `${n(changes)} change${changes === 1 ? "" : "s"}`;
}

export function droppedText(dropped: number, inFlight: boolean): string {
  if (dropped === 0 && !inFlight) return "Lost the connection. Reload to keep working.";
  const queued = dropped > 0 ? `: ${n(dropped)} queued change${dropped === 1 ? " was" : "s were"} dropped.` : ".";
  const sent = inFlight ? " The change already sent was applied once or not at all; reload to see." : " Reload to keep working.";
  return `Lost the connection${queued}${sent}`;
}
