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
  cannot_tag_mirror?: boolean; // the archive is the NAS mirror's copy (the UI adds its advice)
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
  plaud: RecordingPlaud | null; // Details' Plaud lines; null for a recording with no Plaud source
}

export type Filter = { kind: "all" } | { kind: "untagged" } | { kind: "tag"; tag: string };

export type Page = "library" | "tags" | "add" | "status";
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
  sync?: SyncPanel; // Status's Plaud sync panel (2b spec §15.2); absent where nothing reads it
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

// ---- Plaud (2b spec §9.3, §15; phase 1 plan, Task 19) ----------------------------------------
// The server formats every date and time, in the default zone. Every `string | null` button field
// is null when the button is enabled, else the reason it isn't. No title reaches the browser
// except in the Sync screen's own rows (Not here yet, Updated) and Release's question. Phase 1's
// views fill Phase 1's data and leave the rest empty; the canned view (demo/plaud-sync.json) fills
// every field, and is the contract Phase 2's and 3's readers write to.

export type PlaudKind = "compare" | "sync" | "import" | "resync" | "full" | "accept" | "accept-changes"
  | "release-preview" | "release" | "unrelease" | "park" | "unpark" | "keep-here";
/** The `plaud_request` input (spec §9.3). Never sent on load, from a URL, its hash or storage. */
export interface PlaudRequest { request_id: string; kind: PlaudKind; plaud_ids: string[]; recording_ids: string[]; all: boolean }
export interface ReleasePreview { plaud_id: string; title: string; date: string; time: string; duration: string | null; next_sync: string | null }
/** The `plaud_result` message. `result` holds counts and IDs, never a title. */
export interface PlaudResult {
  request_id: string; kind: PlaudKind | null;
  state: "queued" | "running" | "done" | "failed" | "expired" | "refused";
  message: string; progress: { done: number; total: number } | null;
  result: Record<string, number | string | string[]> | null;
  preview: ReleasePreview | null; // release-preview only, for the asking session: null until Phase 2 builds it
}
/** What the worker can do yet, on every view: a request button of any other kind is disabled with
 * `not_yet` as its reason (Phase 1 handles "keep-here" alone; each phase widens it). */
export interface Handled { handled: PlaudKind[]; not_yet: string }
export interface SyncSummaryView { kind: "all-here" | "problem" | "no-sync" | "demo"; sentence: string; explain: string | null; see_status: boolean }
export interface SyncFlag {
  kind: "kept-out-late" | "consent-late" | "private-unwritten" | "rekeyed";
  recording_id: string | null; plaud_id: string | null; copy_id: string | null;
  sentence: string; keep_here: boolean; date: string | null; duration: string | null;
}
export interface SyncHold { sentence: string; accept: boolean; refused: string | null }
export interface SyncBreaker { sentence: string; accept: boolean; code_fix: string | null; fields: string[] }
export interface SyncEquation { listed: number; here: number; waiting: number; excluded: number; missing: number; arrive_private: number; listing_at: string | null }
export interface ArchiveEquation { from_plaud: number; here: number; gone: number; absent: number }
export interface ConsentSummary { ids: number; on_plaud: number | null; file_changed: string | null; read_at: string | null; stop: string | null }
export interface NotHereRow { plaud_id: string; copy_id: string; date: string; time: string; duration: string | null; title: string | null; arrives_private: boolean; state: string; review: boolean; park: boolean }
export interface KeptOutRow { plaud_id: string; copy_id: string; date: string | null; time: string | null; duration: string | null; by: "consent" | "title" | "rekeyed" | "was-title"; release: boolean; released: string | null }
export interface UpdatedRow { recording_id: string; title: string | null; parts: string[]; at: string }
export interface GoneRow { recording_id: string; since: string }
export interface ParkedRow { id: string; kind: "recording" | "plaud" }
export interface SyncView extends Handled {
  demo: boolean; available: boolean; reason: string | null;
  gate: string | null; // the gate's reason while it is closed: every kind but Compare and Keep it here waits (§11.6)
  listed: boolean; // false until Phase 2 writes a listing: the screen then shows no counts from one
  summary: SyncSummaryView; flags: SyncFlag[]; hold: SyncHold | null; breaker: SyncBreaker | null;
  buttons: { sync: string | null; compare: string | null; full: string | null; import: string | null };
  full_estimate: string; // "about 40 minutes" | "unknown until the first run"
  equation: SyncEquation | null; archive: ArchiveEquation | null; consent: ConsentSummary | null;
  titles_hidden: string | null; // "titles hidden until the consent list reads" | "titles hidden while Plaud's IDs look changed"
  not_here: NotHereRow[]; import_all: { count: number; review_more: number };
  kept_out: KeptOutRow[]; updated: UpdatedRow[]; gone: { rows: GoneRow[]; last_7_days: number }; parked: ParkedRow[];
  last_result: string | null; next_sync: string | null;
}
export interface SyncPanel extends Handled {
  demo: boolean; no_sync_yet: boolean;
  listed: boolean; // false until Phase 2 writes a listing: Status then shows no zero counts
  summary: SyncSummaryView; failing: string | null; not_pinged: string | null; conditions: string[];
  hold: SyncHold | null; breaker: SyncBreaker | null;
  numbers: {
    equation: SyncEquation | null; archive: ArchiveEquation | null; consent: ConsentSummary | null;
    last_good_listing: string | null; next_sync: string | null; sweep: string | null; sign_in: string | null;
    full: string | null; held: number; gone: number; failing: number; parked: number;
    worker: string | null; healthchecks: string | null;
  };
  details: {
    normaliser: string; fields: string[]; census: Record<string, number>; form_change: string | null;
    changes_24h: Record<string, number>; named_share: string | null; runs: { at: string; result: string }[];
    keep_out_words: string[]; malformed: number;
  };
  attention: string[];
}
export interface Arrivals { new: number; private: number; updated: number; kept_out_late: number; consent_late: number }
export interface RecordingPlaud extends Handled {
  plaud_ids: { id: string; copy_id: string }[]; checked: string | null; next_by: string | null;
  gone_since: string | null; tagged_private: string | null; removed_private: string | null;
  flags: SyncFlag[]; resync: string | null;
}
