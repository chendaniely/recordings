import type { LibraryView } from "../types";

/** The tag picker's logic (spec §6). Private flags come from the server, never from a rule here. */
export interface PickerTag { tag: string; private: boolean; count: number }
export interface TagState { on: number; of: number; state: "all" | "some" | "none" }

export function pickerTags(view: Pick<LibraryView, "tags" | "note_types">, extra: readonly string[] = []): PickerTag[] {
  const tags: PickerTag[] = view.tags.map((t) => ({ tag: t.tag, private: t.private, count: t.count }));
  for (const n of view.note_types) tags.push({ tag: `notes/${n.note_type}`, private: false, count: n.count });
  const have = new Set(tags.map((t) => t.tag));
  for (const tag of extra) {
    if (!have.has(tag)) {
      tags.push({ tag, private: false, count: 0 });
      have.add(tag);
    }
  }
  return tags;
}

const fold = (s: string) => s.normalize("NFC").toLowerCase();

function rank(tag: string, q: string): number {
  const t = fold(tag);
  if (t === q) return 0;
  if (t.startsWith(q) || t.split("/").some((part) => part.startsWith(q))) return 1;
  return t.includes(q) ? 2 : -1;
}

const byUse = (a: PickerTag, b: PickerTag) => b.count - a.count || a.tag.localeCompare(b.tag);

/** An exact case-insensitive match, then folder parts that start with the text, then any other
 * substring; within each, the most used first. Create comes after all of them. */
export function rankTags(tags: readonly PickerTag[], query: string): PickerTag[] {
  const q = fold(query.trim());
  if (!q) return [...tags].sort(byUse);
  return tags
    .map((t) => [rank(t.tag, q), t] as const)
    .filter(([r]) => r >= 0)
    .sort(([ra, a], [rb, b]) => ra - rb || byUse(a, b))
    .map(([, t]) => t);
}

/** What Create (and Shift+Enter) would make: the typed text, trimmed and composed (NFC; tagging
 * plan, decision 5). Never a notes/ tag, never a case-variant of an existing tag. */
export function createOffer(tags: readonly PickerTag[], query: string): string | null {
  const text = query.trim().normalize("NFC");
  if (!text || /^notes(\/|$)/i.test(text)) return null;
  const folded = fold(text);
  return tags.some((t) => fold(t.tag) === folded) ? null : text;
}

export function tagState(tag: string, targets: readonly { tags: readonly { tag: string }[] }[]): TagState {
  const on = targets.filter((r) => r.tags.some((t) => t.tag === tag)).length;
  return { on, of: targets.length, state: on === 0 ? "none" : on === targets.length ? "all" : "some" };
}

/** Each item's state in words as well as a glyph: ✓ "on all 12", – "on 5 of 12". */
export function stateWords(s: TagState): string {
  if (s.state === "all") return s.of === 1 ? "on it" : `on all ${s.of}`;
  return s.state === "some" ? `on ${s.on} of ${s.of}` : "";
}

/** Enter on a ✓ item adds nothing, and says so. */
export function alreadyWords(s: TagState): string {
  return s.of === 1 ? "Already on it" : `Already on all ${s.of}`;
}
