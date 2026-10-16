import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { markReply, mergeReply, tagStatus, type JustTagged, type Marks } from "@/lib/library";
import {
  EMPTY_QUEUE, dropAll, enqueue, enqueueFront, heard, newRequestId, pendingRequests, resume, stillWorking,
  takeNext, type Origin, type Queue,
} from "@/lib/queue";
import { NO_SNIPPETS, heardSnippets, nextSnippets, wanted, type Snippets } from "@/lib/snippets";
import { progressKind } from "@/lib/toasts";
import {
  NO_UNDOS, busyTokens, cellsClosed, heardUndo, isRedo, mergesIntoCells, nextCellUndo, nextUndo, type CellToast, type Undos,
} from "@/lib/undo";
import { useSetShinyInput, useShinyMessageHandler } from "@/sr";
import type { LibraryView, SnippetRequest, SnippetResult, TagRequest, TagResult } from "@/types";

import { watchToaster } from "./toaster";
import {
  cellsId, dismissProgress, showAlert, showCannotTag, showCells, showDropped, showProgress, showResult, showStillWorking,
} from "./toasts";

export interface PickerTarget { ids: string[]; title: string } // the dialog form: T, the selection bar, a drop
export interface ConfirmState { request: TagRequest; origin: Origin; reply: TagResult; redo: boolean }

export interface Tagging {
  view: LibraryView | null;
  loadKey: number; // bumps on each full library: load and Refresh
  marks: Marks;
  pending: TagRequest[];
  confirm: ConfirmState | null;
  revealed: { key: number; ids: string[] }; // rows an Undo put back into the view
  touched: number;
  lastTagged: JustTagged | null; // the last add that changed something, and where it went
  sessionTags: string[];
  drag: { current: string[] | null }; // set at dragstart, never read from dataTransfer
  canTag: boolean;
  opening: boolean; // the archive's first open is still running: tagging is off for a moment only
  reason: string | null; // why tagging is off for good (may be a long path); null while opening
  saves: boolean;
  isPrivate: (tag: string) => boolean;
  tag: (action: "add" | "remove", ids: string[], tags: string[], origin: Origin) => void;
  undo: (token: string) => void;
  undoLast: () => void;
  refresh: () => void;
  answerConfirm: (remove: boolean) => void;
  picker: PickerTarget | null;
  openPicker: (target: PickerTarget) => void;
  closePicker: () => void;
  snippet: (id: string) => string | null | undefined;
  wantSnippets: (ids: string[]) => void;
}

export const TaggingContext = createContext<Tagging | null>(null);

export function useTaggingContext(): Tagging {
  const tagging = useContext(TaggingContext);
  if (!tagging) throw new Error("useTaggingContext is used outside App");
  return tagging;
}

const PROGRESS_FROM = 50; // a request this big shows a progress toast once it is sent
const event = { priority: "event" as const, debounceMs: 0 };
type JQuery = (el: Document) => { on(e: string, f: () => void): void; off(e: string, f: () => void): void };

const sameCells = (a: CellToast, b: CellToast) =>
  a.key === b.key && a.open === b.open && a.changes === b.changes && a.tokens.length === b.tokens.length;

/** Every input, message handler, queue and toast for tagging. Called by App, which never
 * unmounts: shinyreact drops a message that has no handler (spec §6). */
export function useTagging(library: LibraryView | undefined): Tagging {
  // Mounted once, here, with null defaults. shinyreact sends that null at mount, and the server's
  // @reactive.event (ignore_none) skips it, so no request is ever made on load (spec §5).
  const sendTag = useSetShinyInput<TagRequest | null>("tag_request", null, event);
  const sendRefresh = useSetShinyInput<number | null>("refresh", null, event);
  const sendSnippets = useSetShinyInput<SnippetRequest | null>("snippet_request", null, event);

  const [view, setView] = useState<LibraryView | null>(null);
  const [loadKey, setLoadKey] = useState(0);
  const [marks, setMarks] = useState<Marks>({});
  const [pending, setPending] = useState<TagRequest[]>([]);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  const [revealed, setRevealed] = useState({ key: 0, ids: [] as string[] });
  const [lastTagged, setLastTagged] = useState<JustTagged | null>(null);
  const [sessionTags, setSessionTags] = useState<string[]>([]);
  const [picker, setPicker] = useState<PickerTarget | null>(null);
  const [snippetVersion, setSnippetVersion] = useState(0);
  const [lost, setLost] = useState(false);

  const queue = useRef<Queue>(EMPTY_QUEUE);
  const confirmRef = useRef<ConfirmState | null>(null);
  const viewRef = useRef<LibraryView | null>(null);
  const lostRef = useRef(false);
  const undos = useRef<Undos>(NO_UNDOS); // Cmd-Z's stack and the grid's running toast, together
  const refreshes = useRef(0);
  const drag = useRef<string[] | null>(null);
  const stillShown = useRef<string | null>(null);
  const snippets = useRef<Snippets>(NO_SNIPPETS);

  // The full library arrives on load (twice in a fresh process: first while the archive is still
  // being opened, then with can_tag) and on Refresh. Each replaces the merged copy and its marks,
  // during this render, so no frame draws the old one; Undo tokens survive it (spec §5).
  // Known race, accepted: a Refresh snapshot the server built before a newer tag_result, but
  // delivered after it, overwrites that reply's delta until the next Refresh. Refresh again fixes it.
  const [base, setBase] = useState<LibraryView | undefined>(undefined);
  if (library !== base) {
    setBase(library);
    if (library) {
      setView(library);
      setMarks({});
      setLoadKey((k) => k + 1);
    }
  }
  viewRef.current = view;

  /** The Undo tokens already on their way: queued, in flight, or waiting on the confirmation. */
  const busy = useCallback(
    () => busyTokens([...pendingRequests(queue.current), ...(confirmRef.current ? [confirmRef.current.request] : [])]),
    [],
  );

  const pump = useCallback(() => {
    const { queue: next, send } = takeNext(queue.current, Date.now());
    queue.current = next;
    if (send) {
      sendTag(send);
      // Shown when sent, never while it waits behind another (Task 9 carry).
      if (send.ids.length >= PROGRESS_FROM) {
        showProgress(send, 0, send.ids.length, progressKind(send, isRedo(undos.current, send)));
      }
    }
    setPending(pendingRequests(next));
  }, [sendTag]);

  /** Queues a request, or says why tagging is off; true when it was queued. */
  const submit = useCallback((request: TagRequest, origin: Origin): boolean => {
    const status = tagStatus(viewRef.current, lostRef.current);
    if (!status.canTag) {
      showCannotTag(status);
      return false;
    }
    queue.current = enqueue(queue.current, request, origin);
    pump();
    return true;
  }, [pump]);

  const tag = useCallback((action: "add" | "remove", ids: string[], tags: string[], origin: Origin) => {
    if (ids.length === 0 || tags.length === 0) return;
    const accepted = submit({ request_id: newRequestId(), action, ids, tags, confirm_ids: [] }, origin);
    if (accepted && action === "add") setSessionTags((s) => [...s, ...tags.filter((t) => !s.includes(t))]);
  }, [submit]);

  const undo = useCallback((token: string) => {
    if (busy().has(token)) return; // already on its way: never queued twice (Task 9 carry)
    submit({ request_id: newRequestId(), action: "undo", ids: [], tags: [], undo: token, confirm_ids: [] }, { kind: "undo" });
  }, [busy, submit]);

  const undoLast = useCallback(() => {
    const token = nextUndo(undos.current, busy());
    if (token) undo(token);
  }, [busy, undo]);

  /** The grid's running toast, drawn from the shared Undo state. "Undo last" closes it at once,
   * so no reply can bring it back after the click dismissed it. */
  const drawCells = useCallback(() => {
    const { key, open, changes, tokens } = undos.current.cells;
    if (!open) return;
    if (changes === 0) {
      toast.dismiss(cellsId(key));
      return;
    }
    const undoLastCell = () => {
      const token = nextCellUndo(undos.current, busy());
      undos.current = cellsClosed(undos.current, key);
      if (token) undo(token);
    };
    showCells(key, changes, viewRef.current?.saves ?? true, tokens.length > 0 ? undoLastCell : null, () => {
      undos.current = cellsClosed(undos.current, key);
    });
  }, [busy, undo]);

  const refresh = useCallback(() => {
    refreshes.current += 1;
    sendRefresh(refreshes.current);
  }, [sendRefresh]);

  const answerConfirm = useCallback((remove: boolean) => {
    const c = confirmRef.current;
    if (!c) return; // Radix also reports the close after Remove: only the first answer counts
    confirmRef.current = null;
    setConfirm(null);
    if (remove) {
      // A new request_id, the same undo token for an Undo, and the IDs the server named.
      const resend = { ...c.request, request_id: newRequestId(), confirm_ids: c.reply.loses_private?.ids ?? [] };
      queue.current = enqueueFront(queue.current, resend, c.origin);
    }
    queue.current = resume(queue.current);
    pump();
  }, [pump]);

  useShinyMessageHandler<TagResult>("tag_result", (reply) => {
    const { queue: next, finished } = heard(queue.current, reply, Date.now());
    queue.current = next;
    if (reply.status === "progress") {
      const flight = next.inFlight;
      if (!flight || flight.request.request_id !== reply.request_id) return;
      // By the server's total, not the request's ids: an Undo carries none, so a 300-row Undo
      // shows its progress from the first progress reply on.
      if (reply.total >= PROGRESS_FROM) {
        const kind = progressKind(flight.request, isRedo(undos.current, flight.request));
        showProgress(flight.request, reply.done_so_far, reply.total, kind);
      }
      if (stillShown.current === flight.request.request_id) stillShown.current = null; // a new stall says so again
      return;
    }
    if (!finished) return; // another window's, or a request already ended
    const { request, origin } = finished;
    const redo = isRedo(undos.current, request); // before the reply settles its token
    if (reply.status === "confirm") {
      // heard() has paused the queue: nothing is sent behind the dialog.
      dismissProgress(request);
      if (!reply.loses_private) {
        // Never expected: a confirmation that names nothing can't be answered. Say so, and go on.
        showAlert(`stop-${request.request_id}`, ["Nothing was changed: the confirmation named no recordings."]);
        queue.current = resume(queue.current);
        pump();
        return;
      }
      confirmRef.current = { request, origin, reply, redo };
      setConfirm(confirmRef.current);
      setPending(pendingRequests(queue.current));
      return;
    }
    setView((v) => (v ? mergeReply(v, reply) : v));
    setMarks((m) => markReply(m, reply));
    const cellsBefore = undos.current.cells;
    undos.current = heardUndo(undos.current, request, origin, reply);
    if (request.action === "undo") {
      setRevealed((r) => ({ key: r.key + 1, ids: reply.rows.map((row) => row.id) }));
    }
    if (request.action === "add" && reply.changed > 0) {
      setLastTagged((last) => ({ tags: request.tags, ids: request.ids, key: (last?.key ?? 0) + 1 }));
    }
    if (mergesIntoCells(origin, request, reply)) {
      dismissProgress(request); // "Still working…", if a cell was that slow
      drawCells();
    } else {
      showResult(request, reply, viewRef.current?.saves ?? true, undo, redo);
      // Cmd-Z undid one of the running toast's changes: it says so, or goes.
      if (!sameCells(cellsBefore, undos.current.cells)) drawCells();
    }
    pump();
  });

  // "Still working…" after 30 s with no reply and no progress, in the progress toast's place;
  // never a re-send (spec §6).
  useEffect(() => {
    const timer = window.setInterval(() => {
      const flight = queue.current.inFlight;
      if (flight && stillShown.current !== flight.request.request_id && stillWorking(queue.current, Date.now())) {
        stillShown.current = flight.request.request_id;
        showStillWorking(flight.request);
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, []);

  // The toaster's one shared pause, and what had focus before it (toaster.ts).
  useEffect(() => watchToaster(), []);

  // Shiny's reconnect stays off: on a lost connection the queue is dropped, the progress toast
  // closes, the toast says so, and nothing more is sent (spec §5, §8).
  useEffect(() => {
    const $ = (window as unknown as { jQuery?: JQuery }).jQuery;
    if (!$) return;
    const gone = () => {
      const flight = queue.current.inFlight;
      const asking = confirmRef.current !== null; // a change waiting on the dialog is dropped too
      const { queue: next, dropped, inFlight } = dropAll(queue.current);
      queue.current = next;
      if (flight) dismissProgress(flight.request);
      lostRef.current = true;
      setLost(true);
      confirmRef.current = null;
      setConfirm(null);
      setPending([]);
      showDropped(dropped + (asking ? 1 : 0), inFlight);
    };
    $(document).on("shiny:disconnected", gone);
    return () => $(document).off("shiny:disconnected", gone);
  }, []);

  // Snippets: one request at a time, at most 200 IDs, kept in memory only (spec §5).
  const pumpSnippets = useCallback(() => {
    const { state, send } = nextSnippets(snippets.current, newRequestId);
    snippets.current = state;
    if (send) sendSnippets(send);
  }, [sendSnippets]);

  const wantSnippets = useCallback((ids: string[]) => {
    snippets.current = wanted(snippets.current, ids);
    pumpSnippets();
  }, [pumpSnippets]);

  useShinyMessageHandler<SnippetResult>("snippets", (reply) => {
    const { state, retry } = heardSnippets(snippets.current, reply);
    if (state === snippets.current) return; // another window's, or one already ended
    snippets.current = state;
    setSnippetVersion((v) => v + 1);
    if (retry) pumpSnippets();
  });

  // Private only from the server's flags: the browser never re-implements the rule (spec §5).
  const privateTags = useMemo(() => new Set((view?.tags ?? []).filter((t) => t.private).map((t) => t.tag)), [view]);
  const isPrivate = useCallback((t: string) => privateTags.has(t), [privateTags]);
  // A new function per reply, so components that read it draw the new snippets.
  const snippet = useMemo(() => {
    const have = snippets.current.have;
    return (id: string) => have.get(id);
  }, [snippetVersion]);
  const touched = useMemo(() => Object.values(marks).filter((m) => m.done).length, [marks]);
  const openPicker = useCallback((target: PickerTarget) => setPicker(target), []);
  const closePicker = useCallback(() => setPicker(null), []);
  const status = useMemo(() => tagStatus(view, lost), [view, lost]);

  return useMemo(() => ({
    view, loadKey, marks, pending, confirm, revealed, touched, lastTagged, sessionTags, drag,
    canTag: status.canTag, opening: status.opening, reason: status.reason, saves: view?.saves ?? true,
    isPrivate, tag, undo, undoLast, refresh, answerConfirm, picker, openPicker, closePicker, snippet, wantSnippets,
  }), [view, loadKey, marks, pending, confirm, revealed, touched, lastTagged, sessionTags, status, isPrivate, tag, undo, undoLast, refresh, answerConfirm, picker, openPicker, closePicker, snippet, wantSnippets]);
}
