import type { SnippetRequest, SnippetResult } from "../types";
import { snippetsToFetch } from "./grid";

/** The browser's snippet bookkeeping (spec §5): one request at a time, at most 200 IDs, held in
 * memory only, never in storage. */

export interface Snippets {
  have: ReadonlyMap<string, string | null>; // null: the recording has no snippet
  asked: ReadonlySet<string>;
  inFlight: { request_id: string; ids: readonly string[] } | null;
  wanted: readonly string[]; // the rows on screen
}

export const NO_SNIPPETS: Snippets = { have: new Map(), asked: new Set(), inFlight: null, wanted: [] };

export function wanted(s: Snippets, ids: readonly string[]): Snippets {
  return { ...s, wanted: ids };
}

/** The next batch, when none is in flight: on-screen rows not held or asked for yet. */
export function nextSnippets(s: Snippets, newId: () => string): { state: Snippets; send: SnippetRequest | null } {
  if (s.inFlight) return { state: s, send: null };
  const ids = snippetsToFetch(s.wanted, s.have, s.asked);
  if (ids.length === 0) return { state: s, send: null };
  const request_id = newId();
  return {
    state: { ...s, asked: new Set([...s.asked, ...ids]), inFlight: { request_id, ids } },
    send: { request_id, ids },
  };
}

/** A reply ends the batch in flight. A failed batch (`message` set) is forgotten as asked, so
 * the next want can ask again (Task 7 carry); not at once, so one that keeps failing never
 * loops. A refusal with a null request_id can only be the batch in flight (one at a time). */
export function heardSnippets(s: Snippets, reply: SnippetResult): { state: Snippets; retry: boolean } {
  const flight = s.inFlight;
  if (!flight) return { state: s, retry: false };
  const ours = reply.request_id === flight.request_id || (reply.request_id === null && reply.message !== null);
  if (!ours) return { state: s, retry: false };
  const have = new Map(s.have);
  for (const [id, text] of Object.entries(reply.snippets ?? {})) have.set(id, text);
  if (reply.message) {
    const asked = new Set(s.asked);
    for (const id of flight.ids) asked.delete(id);
    return { state: { ...s, have, asked, inFlight: null }, retry: false };
  }
  return { state: { ...s, have, inFlight: null }, retry: true };
}
