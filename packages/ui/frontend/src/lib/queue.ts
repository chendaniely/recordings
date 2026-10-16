import type { TagRequest, TagResult } from "../types";

/** The browser's request queue (spec §6): one request in flight at a time, because shinyreact
 * merges input values set in the same tick; paused while the confirmation is open; never re-sent. */

export type OriginKind = "picker" | "drop" | "header" | "bar" | "cell" | "chip" | "column" | "digit" | "undo";
export interface Origin { kind: OriginKind }
export interface Entry { request: TagRequest; origin: Origin; heardAt: number }
export interface Queue { inFlight: Entry | null; waiting: Entry[]; paused: boolean }

export const EMPTY_QUEUE: Queue = { inFlight: null, waiting: [], paused: false };
export const STILL_WORKING_MS = 30_000;

export function enqueue(q: Queue, request: TagRequest, origin: Origin): Queue {
  return { ...q, waiting: [...q.waiting, { request, origin, heardAt: 0 }] };
}

/** The confirmed resend goes first: it answers the request the dialog interrupted. */
export function enqueueFront(q: Queue, request: TagRequest, origin: Origin): Queue {
  return { ...q, waiting: [{ request, origin, heardAt: 0 }, ...q.waiting] };
}

export function takeNext(q: Queue, now: number): { queue: Queue; send: TagRequest | null } {
  if (q.inFlight || q.paused || q.waiting.length === 0) return { queue: q, send: null };
  const [next, ...rest] = q.waiting;
  return { queue: { ...q, inFlight: { ...next, heardAt: now }, waiting: rest }, send: next.request };
}

/** Progress keeps the request in flight and restarts the 30-second clock; any other status ends
 * it. A reply to another request changes nothing.
 *
 * A refusal with a null `request_id` (the server could not read the request, so cannot name it)
 * can only concern the one request in flight, since the browser sends one at a time. It ends that
 * request, so the queue never stalls behind a reply that will not come. */
export function heard(q: Queue, reply: TagResult, now: number): { queue: Queue; finished: Entry | null } {
  if (!q.inFlight) return { queue: q, finished: null };
  if (reply.request_id === null) {
    if (reply.status !== "failed") return { queue: q, finished: null };
    return { queue: { ...q, inFlight: null }, finished: q.inFlight };
  }
  if (reply.request_id !== q.inFlight.request.request_id) return { queue: q, finished: null };
  if (reply.status === "progress") return { queue: { ...q, inFlight: { ...q.inFlight, heardAt: now } }, finished: null };
  return { queue: { ...q, inFlight: null }, finished: q.inFlight };
}

export const pause = (q: Queue): Queue => ({ ...q, paused: true });
export const resume = (q: Queue): Queue => ({ ...q, paused: false });

/** "Still working…": the request is never re-sent, because it may still apply. */
export function stillWorking(q: Queue, now: number): boolean {
  return q.inFlight !== null && now - q.inFlight.heardAt >= STILL_WORKING_MS;
}

/** On shiny:disconnected: the queue is dropped. A request already sent was applied once or not
 * at all (spec §8). */
export function dropAll(q: Queue): { queue: Queue; dropped: number; inFlight: boolean } {
  return { queue: EMPTY_QUEUE, dropped: q.waiting.length, inFlight: q.inFlight !== null };
}

export function pendingRequests(q: Queue): TagRequest[] {
  return [...(q.inFlight ? [q.inFlight.request] : []), ...q.waiting.map((e) => e.request)];
}

/** 128 random bits from crypto.getRandomValues: crypto.randomUUID needs a secure context, and the
 * app may be served over plain HTTP on the tailnet (spec §5). */
export function newRequestId(fill: (a: Uint8Array) => Uint8Array = (a) => crypto.getRandomValues(a)): string {
  return Array.from(fill(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
}
