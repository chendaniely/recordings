import type { Filter, LibraryRow, LibraryView, TagRef, TagRequest, TagResult } from "../types";
import { filterRows } from "./transcript";

/** Merge a reply's delta into the library the browser holds (spec §5): the changed rows in
 * place, and the recounted counts, tags and note types. The full view comes only on load and
 * Refresh. */
export function mergeReply(view: LibraryView, reply: TagResult): LibraryView {
  if (reply.rows.length === 0 && reply.counts === null) return view;
  const byId = new Map(reply.rows.map((r) => [r.id, r]));
  const recordings = view.recordings.map((r) => byId.get(r.id) ?? r);
  const known = new Set(view.recordings.map((r) => r.id));
  for (const r of reply.rows) if (!known.has(r.id)) recordings.push(r);
  return {
    ...view,
    recordings,
    counts: reply.counts ?? view.counts,
    tags: reply.tags ?? view.tags,
    note_types: reply.note_types ?? view.note_types,
  };
}

/** The rows a filter shows, in the view's order. Worked out on load, on Refresh and on a filter
 * change, never on a tag change: rows stay put, marked done, until then (spec §6). */
export function orderFor(rows: readonly LibraryRow[], filter: Filter): string[] {
  return filterRows([...rows], filter).map((r) => r.id);
}

/** An Undo puts the rows it touched back into the current view, at their place in `all`. */
export function withIds(order: readonly string[], ids: readonly string[], all: readonly LibraryRow[]): string[] {
  const want = new Set([...order, ...ids]);
  return all.map((r) => r.id).filter((id) => want.has(id));
}

export type ShownTag = TagRef & { pending?: "add" | "remove" };
const idSets = new WeakMap<TagRequest, Set<string>>();

function idsOf(request: TagRequest): Set<string> {
  let ids = idSets.get(request);
  if (!ids) {
    ids = new Set(request.ids);
    idSets.set(request, ids);
  }
  return ids;
}

/** A row's tags as they will be: a pending add shows at once, a pending removal shows struck
 * through, both marked pending, until the reply (spec §6). */
export function shownTags(row: LibraryRow, pending: readonly TagRequest[]): ShownTag[] {
  const tags: ShownTag[] = row.tags.map((t) => ({ ...t }));
  for (const request of pending) {
    if (request.action === "undo" || !idsOf(request).has(row.id)) continue;
    for (const tag of request.tags) {
      const at = tags.findIndex((t) => t.tag === tag);
      if (request.action === "add") {
        if (at < 0) tags.push({ tag, by: "you", pending: "add" });
        else if (tags[at].pending === "remove") tags[at] = { ...tags[at], pending: "add" };
      } else if (at >= 0) {
        // A removal of a tag only a pending add shows leaves nothing to show.
        if (tags[at].pending === "add") tags.splice(at, 1);
        else tags[at] = { ...tags[at], pending: "remove" };
      }
    }
  }
  return tags;
}

export interface Mark { done?: boolean; failed?: string }
export type Marks = Readonly<Record<string, Mark>>;

/** Done rows keep a "✓ Tagged" mark, failed rows their reason, until Refresh (spec §6). */
export function markReply(marks: Marks, reply: TagResult): Marks {
  const next: Record<string, Mark> = { ...marks };
  for (const r of reply.rows) next[r.id] = { done: true };
  for (const f of reply.failures) next[f.id] = { ...next[f.id], failed: f.reason };
  return next;
}

/** Whether the sidebar lists the filter's item. A tag filter whose tag lost its last recording
 * would vanish while still applied (Review Focus 4): the sidebar keeps it, at 0. */
export function filterIsShown(filter: Filter, view: Pick<LibraryView, "tags" | "note_types">): boolean {
  if (filter.kind !== "tag") return true;
  if (filter.tag.startsWith("notes/")) return view.note_types.some((n) => `notes/${n.note_type}` === filter.tag);
  return view.tags.some((t) => t.tag === filter.tag);
}
