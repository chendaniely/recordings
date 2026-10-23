import type { Filter, LibraryRow, LibraryView, TagRef, TagRequest, TagResult } from "../types";
import { filterRows } from "./transcript";
import type { Effect } from "./undo";

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

/** The last add that changed something: its tags, the recordings it went to, and its Undo token.
 * `key` counts them, so the list can tell one it hasn't heard yet. */
export interface JustTagged { tags: string[]; ids: string[]; key: number; undo: string | null }
/** The last Undo that ran: the token of the action it reversed. `key` counts them. */
export interface Undone { token: string; key: number }

/** The selection bar's "just tagged …" (ruled, Task 12 fix rounds 1 and 2): the last add made to
 * exactly this selection since it last changed (T, the bar's Tag…, a drop of the selection),
 * kept while later adds go elsewhere (a +'s one row never matches two or more), and while a
 * Refresh or a filter change keeps every selected row; gone when the selection changes, or when
 * an Undo reverses that add (the note would be false). `tagKey` and `undoneKey` are the last ones
 * heard. */
export interface BarNote {
  selected: ReadonlySet<string>; tagKey: number; undoneKey: number; tags: string[] | null; undo: string | null;
}

const sameMembers = (a: ReadonlySet<string>, b: ReadonlySet<string> | readonly string[]) => {
  const ids = [...b];
  return a.size === ids.length && ids.every((id) => a.has(id));
};

/** A note for a new selection: names nothing, and nothing heard before it. */
export function startNote(selected: ReadonlySet<string>, last: JustTagged | null, undone: Undone | null): BarNote {
  return { selected, tagKey: last?.key ?? 0, undoneKey: undone?.key ?? 0, tags: null, undo: null };
}

/** The note after what has happened since; the same object when nothing has. */
export function nextNote(note: BarNote, selected: ReadonlySet<string>, last: JustTagged | null, undone: Undone | null): BarNote {
  let next = note;
  if (selected !== next.selected) {
    next = sameMembers(next.selected, selected) ? { ...next, selected } : startNote(selected, last, undone);
  }
  if (last && last.key > next.tagKey) {
    next = sameMembers(selected, last.ids)
      ? { ...next, tagKey: last.key, tags: last.tags, undo: last.undo }
      : { ...next, tagKey: last.key };
  }
  if (undone && undone.key > next.undoneKey) {
    const reversed = next.undo !== null && next.undo === undone.token;
    next = { ...next, undoneKey: undone.key, ...(reversed ? { tags: null, undo: null } : {}) };
  }
  return next;
}

/** `done`: what the reply that marked the row did, "add" or "remove" (a row added to and then
 * removed from reads as the last one). */
export interface Mark { done?: Effect; failed?: string }
export type Marks = Readonly<Record<string, Mark>>;

/** A done row's label: "Tagged" when its last change added a tag, "Tag removed" when it removed one. */
export const doneLabel = (done: Effect): string => (done === "add" ? "Tagged" : "Tag removed");

/** Done rows keep a "✓ Tagged" or "✓ Tag removed" mark, by the last change, failed rows their
 * reason, until Refresh (spec §6). */
export function markReply(marks: Marks, reply: TagResult, effect: Effect): Marks {
  const next: Record<string, Mark> = { ...marks };
  for (const r of reply.rows) next[r.id] = { done: effect };
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

/** The server's reason while the first open of the archive is still running (recordings_ui
 * writing.OPENING): the Library is drawn at once without tagging, then again when the open ends
 * (spec §5). A moment, not tagging off. */
export const OPENING = "the archive is being opened";
const LOST = "Lost the connection. Reload to keep working.";

const MIRROR_ADVICE = "Open the archive's own app to tag.";

export interface TagStatus { canTag: boolean; opening: boolean; reason: string | null }

/** Whether tagging is on. `reason` is why it is off for good (the server's words, which may be a
 * long path), null while the archive is still being opened or the library hasn't come yet. */
export function tagStatus(view: Pick<LibraryView, "can_tag" | "cannot_tag_reason" | "cannot_tag_mirror"> | null, lost: boolean): TagStatus {
  if (lost) return { canTag: false, opening: false, reason: LOST };
  if (!view) return { canTag: false, opening: true, reason: null };
  if (view.can_tag) return { canTag: true, opening: false, reason: null };
  if (view.cannot_tag_reason === OPENING) return { canTag: false, opening: true, reason: null };
  const reason = view.cannot_tag_reason ?? "Tagging is off.";
  // The server's words end with adopt's advice ("anywhere else, use the app"), which is no use to
  // someone already in the app; the flag, not the words, picks this case.
  return { canTag: false, opening: false, reason: view.cannot_tag_mirror ? `${reason} ${MIRROR_ADVICE}` : reason };
}

/** What `T`, a drop or an Undo say when tagging is off. */
export function cannotTagText(status: TagStatus): string {
  return status.opening ? "Opening the archive… try again in a moment." : status.reason ?? "Tagging is off.";
}
