export interface TagRef { tag: string; by: "you" | "auto" }

export interface LibraryRow {
  id: string; title: string; recorded_at: string; when: string; duration: string | null;
  duration_ms: number | null; kind: "audio" | "video"; sources: string[]; tags: TagRef[];
  private: boolean; untagged: boolean;
}

/** A tag and how many recordings carry it. `private` comes from the server, the browser's only
 * source for it: the browser never re-implements the privacy rule (spec §5). */
export interface TagCount { tag: string; count: number; private: boolean }

export interface LibraryView {
  recordings: LibraryRow[];
  counts: { all: number; untagged: number };
  tags: TagCount[];
  note_types: { note_type: string; count: number }[];
  problems: { path: string; message: string }[];
  can_tag: boolean; cannot_tag_reason: string | null;
  saves: boolean; // false only on the Pages demo: "Demo: changes aren't saved"
}

export interface Word { word: string; start: number; end: number }
export interface Turn { start: number; end: number; speaker: string | null; text: string; words: Word[] }

export interface TranscriptView {
  rendition: string; label: string; engine: string; model: string | null; created_at: string; turns: Turn[];
}

export interface NotesOutput { rendition: string; model: string | null; engine: string; created_at: string; html: string }
export interface NotesGroup { note_type: string; outputs: NotesOutput[] }

export interface RecordingView {
  id: string; title: string; recorded_at: string; when: string; timezone: string;
  duration: string | null; kind: "audio" | "video"; private: boolean;
  media_url: string | null; // null: media.file can't be linked (the reason is in problems)
  tags: TagRef[]; sources: { kind: string; ref: string; added_at: string }[];
  transcripts: TranscriptView[]; chosen_transcript: string | null;
  notes: NotesGroup[]; plaud_notes: { rendition: string; created_at: string; html: string }[];
  my_notes_html: string | null;
  renditions: { rendition: string; kind: string; note_type: string | null; engine: string; model: string | null; created_at: string }[];
  problems: { path: string; message: string }[]; // output files that could not be read (shown in Details)
}

export type Filter = { kind: "all" } | { kind: "untagged" } | { kind: "tag"; tag: string };

export type Page = "library" | "tags" | "status";
export interface Route { page: Page; open: string | null } // open: #/library/<id>, read-only

export interface StatusJob {
  job: string; label: string; last_ok: string | null; last_ok_detail: string | null;
  failing: boolean; detail: string | null; attention: boolean;
  waiting?: boolean;  // late only because a job holds "ops" (spec §12.5)
}

export interface StatusView {
  configured: boolean; jobs: StatusJob[]; attention: string[]; flags: number;
  running?: { job: string; label: string; since: string } | null;  // absent in the demo
  reported?: string | null;  // when the backup service last reported; absent in the demo
  disk: { level: "ok" | "warn" | "stop"; message: string; marker: boolean; free_percent: number | null; free_label: string | null } | null;
}

export type TagAction = "add" | "remove" | "undo";

/** The `tag_request` input (spec §5). Never sent on load, from a URL or from storage. */
export interface TagRequest {
  request_id: string; action: TagAction; ids: string[]; tags: string[];
  undo?: string; confirm_ids?: string[];
}

export interface LosesPrivate { visible: number; untagged: number; ids: string[] }

/** The `tag_result` message (spec §5). */
export interface TagResult {
  request_id: string | null; status: "done" | "confirm" | "failed" | "progress";
  changed: number; unchanged: number; done_so_far: number; total: number;
  loses_private: LosesPrivate | null; now_visible: number;
  failures: { id: string; reason: string }[]; undo: string | null;
  rows: LibraryRow[]; counts: LibraryView["counts"] | null; tags: TagCount[] | null;
  note_types: LibraryView["note_types"] | null; message: string | null;
}

export interface SnippetRequest { request_id: string; ids: string[] }
export interface SnippetResult { request_id: string | null; snippets: Record<string, string | null>; message: string | null }
