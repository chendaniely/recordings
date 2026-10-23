import type { LibraryView } from "../types";

/** The tag picker's logic (spec §6). Private flags come from the server, never from a rule here. */
export interface PickerTag { tag: string; private: boolean; count: number }
export interface TagState { on: number; of: number; state: "all" | "some" | "none" }

/** The view's tags and note types with the server's flags; then `extra`, this session's new tags
 * the server hasn't listed yet; then `kept`, tags the picker touched that the view has dropped
 * (their last use was removed), at 0 and with the flag the server last gave them. */
export function pickerTags(
  view: Pick<LibraryView, "tags" | "note_types">, extra: readonly string[] = [], kept: readonly PickerTag[] = [],
): PickerTag[] {
  const tags: PickerTag[] = view.tags.map((t) => ({ tag: t.tag, private: t.private, count: t.count }));
  for (const n of view.note_types) tags.push({ tag: `notes/${n.note_type}`, private: false, count: n.count });
  const have = new Set(tags.map((t) => t.tag));
  for (const t of [...extra.map((tag) => ({ tag, private: false })), ...kept]) {
    if (!have.has(t.tag)) {
      tags.push({ tag: t.tag, private: t.private, count: 0 });
      have.add(t.tag);
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
    .sort(([ra, a], [rb, b]) => ra - rb || Number(b.private) - Number(a.private) || byUse(a, b))
    .map(([, t]) => t);
}

export interface PickerGroup { locked: boolean; tags: PickerTag[] }

/** The picker's groups, open and locked (private), in the order of their best-ranked item. On a
 * rank tie the locked group sorts first inside `rankTags`, so it comes first here too: the first
 * item shown is always `rankTags()[0]`, and Enter never adds a lower-ranked tag, silently, in
 * place of a private one that matched as well. */
export function pickerGroups(tags: readonly PickerTag[], query: string): PickerGroup[] {
  const ranked = rankTags(tags, query);
  const groups: PickerGroup[] = [
    { locked: true, tags: ranked.filter((t) => t.private) },
    { locked: false, tags: ranked.filter((t) => !t.private) },
  ].filter((g) => g.tags.length > 0);
  return groups.sort((a, b) => ranked.indexOf(a.tags[0]) - ranked.indexOf(b.tags[0]));
}

const MAX_PART = 64;
const MAX_TAG = 200;

// Why Create isn't offered, in the server's words where it has them (recordings/tagops.py); none
// repeats the tag. Shift+Enter shows it.
const EXISTS = "That tag exists: Enter adds it.";
const CASE_VARIANT = "A tag with the same letters in another case exists already: Enter adds it.";
const NOTE_TYPE = "Note types come from prompt files: notes/<type> must name one the archive has.";
const EMPTY_PART = "A folder part is empty, or starts or ends with a space.";
const TOO_LONG = "Each folder part is at most 64 characters, and the whole tag at most 200.";
const SPACE = "Use a plain space, not another kind of space character.";
const INVISIBLE = "A tag can't hold invisible, control or line-separator characters.";
const PATH_LIKE = "A folder part can't be . or .., and a tag can't hold a backslash.";

/** Why Create isn't offered for the typed text, or null when it is (or the box is empty). An
 * existing tag in any case, a notes/ tag, and the names the server refuses anyway: an empty part
 * (a leading, trailing or double `/`) or one with a space at either end, a space character other
 * than a plain space (an NBSP pasted from a web page), a format character (a zero-width space), a part over 64
 * characters or a tag over 200, a `.` or `..` part, a backslash. The rest of §4's rules are the
 * server's to apply. */
export function createRefusal(tags: readonly PickerTag[], query: string): string | null {
  const text = query.trim().normalize("NFC");
  if (!text) return null;
  const folded = fold(text);
  const same = tags.find((t) => fold(t.tag) === folded);
  if (same) return same.tag === text ? EXISTS : CASE_VARIANT;
  if (/\p{Zs}/u.test(text.replace(/ /g, ""))) return SPACE;
  if (/\p{Cf}/u.test(text)) return INVISIBLE;
  const parts = text.split("/");
  if (parts.some((part) => part === "" || part !== part.trim())) return EMPTY_PART;
  if (Array.from(text).length > MAX_TAG || parts.some((part) => Array.from(part).length > MAX_PART)) return TOO_LONG;
  if (text.includes("\\") || parts.some((part) => part === "." || part === "..")) return PATH_LIKE;
  if (/^notes$/i.test(parts[0])) return NOTE_TYPE;
  return null;
}

/** What Create (and Shift+Enter) would make: the typed text, trimmed and composed (NFC; tagging
 * plan, decision 5). Never a notes/ tag, never a case-variant of an existing tag. */
export function createOffer(tags: readonly PickerTag[], query: string): string | null {
  const text = query.trim().normalize("NFC");
  return text && createRefusal(tags, query) === null ? text : null;
}

/** Every tag's state over the targets, counted once: the picker asks it of each item on every
 * keystroke, and the targets may be 20,000 recordings. A row counts once per tag. */
export function tagStates(targets: readonly { tags: readonly { tag: string }[] }[]): (tag: string) => TagState {
  const counts = new Map<string, number>();
  for (const r of targets) {
    for (const tag of new Set(r.tags.map((t) => t.tag))) counts.set(tag, (counts.get(tag) ?? 0) + 1);
  }
  const of = targets.length;
  return (tag) => {
    const on = counts.get(tag) ?? 0;
    return { on, of, state: on === 0 ? "none" : on === of ? "all" : "some" };
  };
}

export function tagState(tag: string, targets: readonly { tags: readonly { tag: string }[] }[]): TagState {
  return tagStates(targets)(tag);
}

const count = (n: number) => n.toLocaleString("en");

/** Each item's state in words as well as a glyph: ✓ "on all 12", – "on 5 of 12". */
export function stateWords(s: TagState): string {
  if (s.state === "all") return s.of === 1 ? "on it" : `on all ${count(s.of)}`;
  return s.state === "some" ? `on ${count(s.on)} of ${count(s.of)}` : "";
}

/** Enter on a ✓ item adds nothing, and says so. */
export function alreadyWords(s: TagState): string {
  return s.of === 1 ? "Already on it" : `Already on all ${count(s.of)}`;
}
