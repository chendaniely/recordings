import type { LibraryRow, LibraryView } from "../types";

const nfcLower = (s: string) => s.normalize("NFC").toLowerCase();

/** The Tags page grid's logic (spec §7). */

export const DEFAULT_COLUMNS = 15;
export const COLUMN_KEY = "recordings.tagColumns";

type Storage = { getItem(k: string): string | null; setItem(k: string, v: string): void };

/** The 15 most-used tags, counted across all tags, notes/* included. Worked out on load and on
 * Refresh, never live, so columns don't move under the cursor. */
export function defaultColumns(view: Pick<LibraryView, "tags" | "note_types">, limit = DEFAULT_COLUMNS): string[] {
  const all = [
    ...view.tags.map((t) => ({ tag: t.tag, count: t.count })),
    ...view.note_types.map((n) => ({ tag: `notes/${n.note_type}`, count: n.count })),
  ];
  return all.sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag)).slice(0, limit).map((t) => t.tag);
}

export interface Columns { pinned: string | null; normal: string[]; privateGroup: string[] }

/** `private` pinned next to the first column; other private tags grouped at the right; this
 * session's tags at the end of their group. Private columns are shown unless hidden for this
 * session, so no private tag name is ever stored (spec §7). */
export function layoutColumns(opts: {
  base: readonly string[]; session: readonly string[]; all: readonly string[];
  isPrivate: (tag: string) => boolean; choices: Readonly<Record<string, boolean>>; hidden: ReadonlySet<string>;
}): Columns {
  const { base, session, all, isPrivate, choices, hidden } = opts;
  const order = [...new Set([...base, ...session, ...[...all].sort((a, b) => a.localeCompare(b))])];
  const shown = (tag: string) =>
    !hidden.has(tag) && (isPrivate(tag) || (choices[tag] ?? (base.includes(tag) || session.includes(tag))));
  const visible = order.filter((tag) => tag !== "private" && shown(tag));
  return {
    pinned: hidden.has("private") ? null : "private",
    normal: visible.filter((tag) => !isPrivate(tag)),
    privateGroup: visible.filter((tag) => isPrivate(tag)),
  };
}

export function columnOrder(columns: Columns): string[] {
  return [...(columns.pinned ? [columns.pinned] : []), ...columns.normal, ...columns.privateGroup];
}

/** Every tag the view knows, `notes/*` included: the column picker's choices. */
export function tagsOf(view: Pick<LibraryView, "tags" | "note_types">): string[] {
  return [...view.tags.map((t) => t.tag), ...view.note_types.map((n) => `notes/${n.note_type}`)];
}

/** The grid's columns are frozen per load (spec §7, never live; Task 14 fix round 1). They are
 * worked out here once, on load and on Refresh. After that they are only added to, at the end of
 * a group (addColumn, withSessionTags), or dropped when the user unticks one in the picker
 * (dropColumn). Nothing reads the counts again, so a reply's recount, or a column falling to 0
 * uses, moves nothing, and the digits stay put. */
export function startColumns(
  view: Pick<LibraryView, "tags" | "note_types">,
  opts: { session: readonly string[]; isPrivate: (tag: string) => boolean; choices: Readonly<Record<string, boolean>>; hidden: ReadonlySet<string> },
): Columns {
  return layoutColumns({ base: defaultColumns(view), all: tagsOf(view), ...opts });
}

/** A tag seen for the first time (a session add, or ticked in the picker), at the end of its
 * group; the same columns when it has one already. */
export function addColumn(columns: Columns, tag: string, isPrivate: (tag: string) => boolean): Columns {
  if (tag === columns.pinned || columns.normal.includes(tag) || columns.privateGroup.includes(tag)) return columns;
  return isPrivate(tag)
    ? { ...columns, privateGroup: [...columns.privateGroup, tag] }
    : { ...columns, normal: [...columns.normal, tag] };
}

/** A column the user unticked in the picker. `private` stays pinned. */
export function dropColumn(columns: Columns, tag: string): Columns {
  if (tag === columns.pinned || (!columns.normal.includes(tag) && !columns.privateGroup.includes(tag))) return columns;
  return { ...columns, normal: columns.normal.filter((t) => t !== tag), privateGroup: columns.privateGroup.filter((t) => t !== tag) };
}

/** This session's tags get their columns straight away (spec §7), unless the user unticked them. */
export function withSessionTags(
  columns: Columns, session: readonly string[], off: (tag: string) => boolean, isPrivate: (tag: string) => boolean,
): Columns {
  return session.reduce((cols, tag) => (off(tag) ? cols : addColumn(cols, tag, isPrivate)), columns);
}

export type RowSort = { by: "newest" | "oldest" | "title" | "duration" } | { by: "has"; tag: string };

export function sortRows(rows: readonly LibraryRow[], sort: RowSort): LibraryRow[] {
  const time = (r: LibraryRow) => Date.parse(r.recorded_at);
  const copy = [...rows];
  switch (sort.by) {
    case "newest": return copy.sort((a, b) => time(b) - time(a));
    case "oldest": return copy.sort((a, b) => time(a) - time(b));
    case "title": return copy.sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base" }));
    case "duration": return copy.sort((a, b) => (b.duration_ms ?? -1) - (a.duration_ms ?? -1));
    case "has": {
      const has = (r: LibraryRow) => (r.tags.some((t) => t.tag === sort.tag) ? 0 : 1);
      return copy.sort((a, b) => has(a) - has(b) || time(b) - time(a));
    }
  }
}

export function gridRows(rows: readonly LibraryRow[], opts: { untaggedOnly: boolean; title: string }): LibraryRow[] {
  const q = nfcLower(opts.title.trim());
  return rows.filter((r) => (!opts.untaggedOnly || r.untagged) && (!q || nfcLower(r.title).includes(q)));
}

/** The IDs the grid shows, in order. Taken on load, on Refresh and when the filter, title or sort
 * changes, never on a tag change: a row tagged afterwards stays where it is (spec §6, §7). */
export function gridOrder(rows: readonly LibraryRow[], opts: { untaggedOnly: boolean; title: string; sort: RowSort }): string[] {
  return sortRows(gridRows(rows, opts), opts.sort).map((r) => r.id);
}

/** Draw the rows from a frozen order, with their current tags; an ID no longer in the view is skipped. */
export function drawRows(order: readonly string[], rows: readonly LibraryRow[]): LibraryRow[] {
  const byId = new Map(rows.map((r) => [r.id, r]));
  return order.flatMap((id) => byId.get(id) ?? []);
}

/** `1`–`9` and `0`: the first ten visible tag columns. */
export function digitColumn(key: string, columns: readonly string[]): string | null {
  if (!/^[0-9]$/.test(key)) return null;
  return columns[key === "0" ? 9 : Number(key) - 1] ?? null;
}

export function digitFor(index: number): string | null {
  return index < 9 ? String(index + 1) : index === 9 ? "0" : null;
}

export function loadChoices(storage: Storage | null): Record<string, boolean> {
  try {
    const parsed: unknown = JSON.parse(storage?.getItem(COLUMN_KEY) ?? "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(Object.entries(parsed).filter(([, v]) => typeof v === "boolean")) as Record<string, boolean>;
  } catch {
    return {};
  }
}

/** Persist only tags the server's view flags `private: false`: an unknown tag, such as one made
 * this session, counts as private until the reply, so no private tag name reaches storage. */
export function saveChoices(storage: Storage | null, choices: Readonly<Record<string, boolean>>, view: Pick<LibraryView, "tags" | "note_types">): void {
  const open = new Set([...view.tags.filter((t) => !t.private).map((t) => t.tag), ...view.note_types.map((n) => `notes/${n.note_type}`)]);
  const kept = Object.fromEntries(Object.entries(choices).filter(([tag]) => open.has(tag)));
  try {
    storage?.setItem(COLUMN_KEY, JSON.stringify(kept));
  } catch {
    /* a per-browser convenience only */
  }
}

/** Snippets for rows on screen (spec §5): ones not yet held or asked for, at most 200. */
export function snippetsToFetch(onScreen: readonly string[], have: ReadonlyMap<string, unknown>, asked: ReadonlySet<string>, max = 200): string[] {
  return onScreen.filter((id) => !have.has(id) && !asked.has(id)).slice(0, max);
}

/** The column picker's groups: by first folder; "" for tags with no folder. */
export function foldersOf(tags: readonly string[]): Map<string, string[]> {
  const groups = new Map<string, string[]>();
  for (const tag of tags) {
    const folder = tag.includes("/") ? tag.split("/")[0] : "";
    groups.set(folder, [...(groups.get(folder) ?? []), tag]);
  }
  return groups;
}
