import type { TagRequest, TagResult } from "../types";
import type { Origin } from "./queue";

/** The browser's Undo tokens, in one place (spec §6; tagging plan, decision 7): Cmd/Ctrl+Z's
 * stack, and the Tags grid's running toast for single-cell toggles ("4 changes · Undo last"),
 * whose tokens are always also on the stack. A token leaves both only when the server has used
 * it up, so Cmd-Z and "Undo last" never send a used token, and an Undo that stopped part way can
 * be tried again, as the server allows. */

export const UNDO_STACK = 20; // the session keeps the last 20 actions (spec §5)

// The server's fixed phrases (spec §8) that decide whether a token is used up.
export const GONE = "the recording is gone";
export const ALREADY_UNDONE = "already undone";
export const UNDO_GONE = "Undo is no longer available (the page was reloaded)";

export interface CellToast { key: number; open: boolean; changes: number; tokens: readonly string[] }
export interface Undos { stack: readonly string[]; cells: CellToast }

export const NO_UNDOS: Undos = { stack: [], cells: { key: 0, open: false, changes: 0, tokens: [] } };

/** A single-cell change that went cleanly joins the running toast; anything else gets its own. */
export function mergesIntoCells(origin: Origin, request: TagRequest, reply: TagResult): boolean {
  return origin.kind === "cell" && request.action !== "undo" && reply.status === "done" && !reply.message
    && reply.failures.length === 0 && reply.undo !== null;
}

/** Whether an Undo used its token up, as the server decides (`_settle_undo`): applied everywhere
 * it could be, a recording gone since counting as settled. A token the server no longer has
 * ("already undone", or lost to a reload) is gone too. An Undo that stopped, or met a busy or
 * edited recording, keeps its token for what it didn't reach. */
export function usedUp(reply: TagResult): boolean {
  if (reply.status === "done" && !reply.message) return reply.failures.every((f) => f.reason === GONE);
  return reply.status === "failed" && (reply.message === ALREADY_UNDONE || reply.message === UNDO_GONE);
}

function drop(u: Undos, token: string): Undos {
  const inCells = u.cells.tokens.includes(token);
  return {
    stack: u.stack.filter((t) => t !== token),
    cells: inCells
      ? { ...u.cells, changes: Math.max(0, u.cells.changes - 1), tokens: u.cells.tokens.filter((t) => t !== token) }
      : u.cells,
  };
}

/** A reply that ends a request. An action's token goes on the stack (and, for a cell, into the
 * running toast); an Undo's own token, offered as Redo, never does (decision 7). */
export function heardUndo(u: Undos, request: TagRequest, origin: Origin, reply: TagResult): Undos {
  if (reply.status === "progress" || reply.status === "confirm") return u;
  if (request.action === "undo") return request.undo && usedUp(reply) ? drop(u, request.undo) : u;
  const token = reply.undo;
  if (!token) return u;
  const stack = [...u.stack.filter((t) => t !== token), token].slice(-UNDO_STACK);
  const onStack = new Set(stack);
  let cells: CellToast = { ...u.cells, tokens: u.cells.tokens.filter((t) => onStack.has(t)) };
  if (mergesIntoCells(origin, request, reply)) {
    cells = cells.open
      ? { ...cells, changes: cells.changes + 1, tokens: [...cells.tokens, token] }
      : { key: cells.key + 1, open: true, changes: 1, tokens: [token] };
  }
  return { stack, cells };
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
  return { ...u, cells: { key, open: false, changes: 0, tokens: [] } };
}

/** The tokens of Undo requests that are queued, in flight, or waiting on the confirmation. */
export function busyTokens(requests: readonly TagRequest[]): Set<string> {
  return new Set(requests.flatMap((r) => (r.action === "undo" && r.undo ? [r.undo] : [])));
}
