import type { TagRequest, TagResult } from "../types";
import type { Origin } from "./queue";

/** The browser's Undo tokens, in one place (spec §6; tagging plan, decision 7): Cmd/Ctrl+Z's
 * stack, the Tags grid's running toast for single-cell toggles ("4 changes · Undo last"), whose
 * tokens are always also on the stack, and the tokens Undos sent back, offered as Redo. A token leaves both only when the server has used
 * it up, so Cmd-Z and "Undo last" never send a used token, and an Undo that stopped part way can
 * be tried again, as the server allows. */

export const UNDO_STACK = 20; // the session keeps the last 20 actions (spec §5)

// The server's fixed phrases (spec §8) that decide whether a token is used up.
export const GONE = "the recording is gone";
export const ALREADY_UNDONE = "already undone";
export const UNDO_GONE = "Undo is no longer available (the page was reloaded)";
export const UNDO_LIMIT = "Undo keeps only the last 20 actions"; // the server's 20 slots also hold Redo tokens

/** The running toast: its changes, the undoable ones' tokens, and how many recordings its changes
 * made visible to external agents (`visibleBy`, per token, so an Undo takes its share off). */
export interface CellToast {
  key: number; open: boolean; changes: number; tokens: readonly string[]; visible: number;
  visibleBy: Readonly<Record<string, number>>;
}
/** `redos`: tokens that came back on Undo replies. An Undo of one of them is a Redo: its toast
 * says "Redone" and offers Undo, and its own token goes on the stack, so Cmd-Z can undo the
 * redone action again. Never on the stack themselves, so Cmd-Z twice after an action undoes two
 * actions, never undo then redo. */
export interface Undos { stack: readonly string[]; cells: CellToast; redos: readonly string[] }

const CLOSED = { open: false, changes: 0, tokens: [], visible: 0, visibleBy: {} } as const;
export const NO_UNDOS: Undos = { stack: [], cells: { key: 0, ...CLOSED }, redos: [] };

/** A single-cell change that went cleanly joins the running toast. Anything else gets its own,
 * and so does a removal whose toast has more to say than "N changes": recordings made visible to
 * external agents, or a note type removed (auto-pick won't add it back). An add that makes
 * recordings visible joins it, and the toast adds them up ("5 changes · 5 now visible to external
 * agents"): in Untagged only, every row's first tag does, and a toast each stacked them up (Task 14
 * fix round 1, revising Task 10's ruling for adds). */
export function mergesIntoCells(origin: Origin, request: TagRequest, reply: TagResult): boolean {
  const notes = request.action === "remove" && request.tags.some((t) => t.startsWith("notes/"));
  const says = request.action !== "add" && reply.now_visible > 0;
  return origin.kind === "cell" && request.action !== "undo" && reply.status === "done" && !reply.message
    && reply.failures.length === 0 && reply.undo !== null && !says && !notes;
}

/** An Undo whose token an earlier Undo sent back: a Redo. */
export function isRedo(u: Undos, request: TagRequest): boolean {
  return request.action === "undo" && !!request.undo && u.redos.includes(request.undo);
}

/** Whether an Undo used its token up, as the server decides (`_settle_undo`): applied everywhere
 * it could be, a recording gone since counting as settled. A token the server no longer has
 * ("already undone", lost to a reload, or pushed out by the last-20 rule) is gone too. An Undo that stopped, or met a busy or
 * edited recording, keeps its token for what it didn't reach. */
export function usedUp(reply: TagResult): boolean {
  if (reply.status === "done" && !reply.message) return reply.failures.every((f) => f.reason === GONE);
  return reply.status === "failed" && (reply.message === ALREADY_UNDONE || reply.message === UNDO_GONE || reply.message === UNDO_LIMIT);
}

function drop(u: Undos, token: string): Undos {
  const inCells = u.cells.tokens.includes(token);
  return {
    redos: u.redos.filter((t) => t !== token),
    stack: u.stack.filter((t) => t !== token),
    cells: inCells
      ? {
        ...u.cells, changes: Math.max(0, u.cells.changes - 1), tokens: u.cells.tokens.filter((t) => t !== token),
        visible: Math.max(0, u.cells.visible - (u.cells.visibleBy[token] ?? 0)),
      }
      : u.cells,
  };
}

/** A reply that ends a request. An action's token, or a Redo's, goes on the stack (and, for a
 * cell, into the running toast); an Undo's own token is kept apart, to be offered as Redo. */
export function heardUndo(u: Undos, request: TagRequest, origin: Origin, reply: TagResult): Undos {
  if (reply.status === "progress" || reply.status === "confirm") return u;
  const token = reply.undo;
  if (request.action === "undo") {
    const redo = isRedo(u, request);
    const settled = request.undo && usedUp(reply) ? drop(u, request.undo) : u;
    if (!token) return settled;
    if (redo) return kept(settled, token, origin, request, reply);
    return { ...settled, redos: [...settled.redos.filter((t) => t !== token), token].slice(-UNDO_STACK) };
  }
  if (!token) return u;
  return kept(u, token, origin, request, reply);
}

function kept(u: Undos, token: string, origin: Origin, request: TagRequest, reply: TagResult): Undos {
  const stack = [...u.stack.filter((t) => t !== token), token].slice(-UNDO_STACK);
  const onStack = new Set(stack);
  let cells: CellToast = { ...u.cells, tokens: u.cells.tokens.filter((t) => onStack.has(t)) };
  if (mergesIntoCells(origin, request, reply)) {
    const shown = reply.now_visible;
    cells = cells.open
      ? {
        ...cells, changes: cells.changes + 1, tokens: [...cells.tokens, token], visible: cells.visible + shown,
        visibleBy: { ...cells.visibleBy, [token]: shown },
      }
      : { key: cells.key + 1, open: true, changes: 1, tokens: [token], visible: shown, visibleBy: { [token]: shown } };
  }
  return { ...u, stack, cells };
}

function newest(tokens: readonly string[], busy: ReadonlySet<string>): string | null {
  for (let i = tokens.length - 1; i >= 0; i--) if (!busy.has(tokens[i])) return tokens[i];
  return null;
}

/** Cmd/Ctrl+Z: the newest action not already being undone. It peeks; the reply decides. So a
 * second press before the first reply undoes a second action, never the same one twice. */
export function nextUndo(u: Undos, busy: ReadonlySet<string>): string | null {
  return newest(u.stack, busy);
}

/** The running toast's "Undo last": the newest of its own changes not already being undone. */
export function nextCellUndo(u: Undos, busy: ReadonlySet<string>): string | null {
  return newest(u.cells.tokens, busy);
}

/** The running toast went (its timer, or dismissed): the next cell change starts a new one. A
 * late close of an older toast leaves a newer one alone. */
export function cellsClosed(u: Undos, key: number): Undos {
  if (u.cells.key !== key || !u.cells.open) return u;
  return { ...u, cells: { key, ...CLOSED } };
}

/** The tokens of Undo requests that are queued, in flight, or waiting on the confirmation. */
export function busyTokens(requests: readonly TagRequest[]): Set<string> {
  return new Set(requests.flatMap((r) => (r.action === "undo" && r.undo ? [r.undo] : [])));
}

/** What a reply did to its rows' tags: the row's done label says it ("✓ Tagged", "✓ Tag removed"). */
export type Effect = "add" | "remove";
/** Per Undo token, the effect of undoing it (the opposite of what the action it reverses did). */
export type Reversals = Readonly<Record<string, Effect>>;

const opposite = (e: Effect): Effect => (e === "add" ? "remove" : "add");

/** What a request did: an add adds, a removal removes, and an Undo (or Redo) does the opposite of
 * the action it reverses, whichever button was pressed. A token this window never saw (the reply
 * is a failure with no rows) reads as a removal. */
export function replyEffect(known: Reversals, request: TagRequest): Effect {
  if (request.action !== "undo") return request.action;
  return (request.undo && known[request.undo]) || "remove";
}

/** Remember what undoing the token in `reply` would do, so its own Undo (or Redo) is read right. */
export function learnReversal(known: Reversals, request: TagRequest, reply: TagResult): Reversals {
  return reply.undo ? { ...known, [reply.undo]: opposite(replyEffect(known, request)) } : known;
}
