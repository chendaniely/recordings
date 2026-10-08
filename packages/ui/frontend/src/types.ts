export interface TagRef { tag: string; by: "you" | "auto" }

export interface LibraryRow {
  id: string; title: string; recorded_at: string; when: string; duration: string | null;
  kind: "audio" | "video"; sources: string[]; tags: TagRef[]; private: boolean; untagged: boolean;
}

export interface LibraryView {
  recordings: LibraryRow[];
  counts: { all: number; untagged: number };
  tags: { tag: string; count: number; private: boolean }[];
  note_types: { note_type: string; count: number }[];
  problems: { path: string; message: string }[];
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
