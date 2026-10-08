import type { NotesGroup, NotesOutput } from "../types";

export type NotesEntry = { group: string; o: NotesOutput };

/** Every notes output in list order: grouped by note type, each group as the server sends it. */
export function flattenNotes(groups: NotesGroup[]): NotesEntry[] {
  return groups.flatMap((g) => g.outputs.map((o) => ({ group: g.note_type, o })));
}

/** The output shown and the one compared with it. A comparison with the shown output itself is
 * dropped, so the "Compare with" select and the view can never disagree. */
export function noteSelection(all: NotesEntry[], picked: string | null, other: string): { main?: NotesEntry; second?: NotesEntry } {
  const main = all.find((x) => x.o.rendition === picked) ?? all[0];
  const second = other && other !== main?.o.rendition ? all.find((x) => x.o.rendition === other) : undefined;
  return { main, second };
}
