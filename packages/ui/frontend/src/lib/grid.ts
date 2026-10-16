import type { LibraryRow, LibraryView } from "../types";

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
  const q = opts.title.trim().toLowerCase();
  return rows.filter((r) => (!opts.untaggedOnly || r.untagged) && (!q || r.title.toLowerCase().includes(q)));
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

export function saveChoices(storage: Storage | null, choices: Readonly<Record<string, boolean>>, isPrivate: (tag: string) => boolean): void {
  const kept = Object.fromEntries(Object.entries(choices).filter(([tag]) => !isPrivate(tag)));
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
