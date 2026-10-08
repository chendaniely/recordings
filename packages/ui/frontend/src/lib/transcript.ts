import type { Filter, LibraryRow, Turn } from "../types";

/** Index of the last turn that has started by time t (binary search); -1 before the first. */
export function activeIndex(turns: Turn[], t: number): number {
  let lo = 0;
  let hi = turns.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (turns[mid].start <= t) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

/** The word being spoken at t, or -1 in the gaps between words. */
export function activeWord(turn: Turn, t: number): number {
  return turn.words.findIndex((w) => w.start <= t && t < w.end);
}

export function formatClock(seconds: number): string {
  const s = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export function filterRows(rows: LibraryRow[], filter: Filter): LibraryRow[] {
  if (filter.kind === "untagged") return rows.filter((r) => r.untagged);
  if (filter.kind === "tag") return rows.filter((r) => r.tags.some((t) => t.tag === filter.tag));
  return rows;
}
