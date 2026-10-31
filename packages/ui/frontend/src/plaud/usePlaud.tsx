import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { newRequestId } from "@/lib/queue";
import { useSetShinyInput, useShinyMessageHandler } from "@/sr";
import { ActionToast } from "@/tagging/ActionToast";
import type { Handled, PlaudKind, PlaudRequest, PlaudResult, SyncView } from "@/types";

/** Why a button can't ask when nothing more particular says: before the Sync view has come, or a
 * view that is unavailable and gives no reason. */
export const NO_PLAUD = "Plaud sync isn't available here";

/** Why a button of `kind` is off, before its own reasons (2b spec §9.3): `why`, this app's reason
 * it can't ask at all (the demo, no config.toml, the mirror's copy), else the view's `not_yet`
 * while the worker has no handler for the kind; null when neither stops it. The server refuses in
 * the same order, so a button left enabled is one the worker will answer. */
export function kindReason(kind: PlaudKind, why: string | null, view: Handled): string | null {
  return why ?? (view.handled.includes(kind) ? null : view.not_yet);
}

/** What the Plaud screens use (2b spec §9.3): send a request, and the replies to this window's. */
export interface Plaud {
  send(kind: PlaudKind, opts?: { plaud_ids?: string[]; recording_ids?: string[]; all?: boolean }): string; // the new request_id
  results: Record<string, PlaudResult>; // by request_id, the newest reply each
  last: PlaudResult | null;
  lastLine: string | null; // the server's newest result line (SyncView.last_result)
  available: boolean; reason: string | null;
  /** kindReason against the Sync view, for what has no Plaud view of its own (the selection
   * bars); NO_PLAUD before the view comes. */
  why(kind: PlaudKind): string | null;
  /** Whether the worker handles this kind yet: outside the Sync screen and Status, a button for a
   * kind it doesn't is not drawn at all (the Library's Re-sync). False before the view comes. */
  handles(kind: PlaudKind): boolean;
  gate: string | null; // the gate's reason while it is closed (SyncView.gate)
}

export const PlaudContext = createContext<Plaud | null>(null);

export function usePlaudContext(): Plaud {
  const plaud = useContext(PlaudContext);
  if (!plaud) throw new Error("usePlaudContext is used outside App");
  return plaud;
}

/** A reply after which nothing more comes for its request. */
export const FINAL: ReadonlySet<PlaudResult["state"]> = new Set(["done", "failed", "expired", "refused"]);

const event = { priority: "event" as const, debounceMs: 0 };

/** The `plaud_request` input and the `plaud_result` handler, mounted once in App: shinyreact
 * drops a message that has no handler (CLAUDE.md). Mounted with a null default, which the
 * server's @reactive.event (ignore_none) skips, so nothing is ever sent on load, from a URL, its
 * hash or storage; only `send`, from a person's press, sends. Each press gets a new random
 * request_id, and the server answers within a second (P6), then while the row is pending every
 * 2 seconds. A final reply's message is a toast, and an import's offers View, which opens
 * Untagged (`onView`; §11.5's "31 imported · 4 arrived private · View"); a Release preview's
 * answer is its question, never a toast. The kept result line is the server's (`last_result`,
 * which redraws when one of this window's requests ends), in the server's zone. */
export function usePlaud(view: SyncView | undefined, onView?: () => void): Plaud {
  const sendRequest = useSetShinyInput<PlaudRequest | null>("plaud_request", null, event);
  const [results, setResults] = useState<Record<string, PlaudResult>>({});
  const [last, setLast] = useState<PlaudResult | null>(null);
  const sent = useRef(new Map<string, PlaudRequest>());
  const viewer = useRef(onView);
  viewer.current = onView;

  const send = useCallback((kind: PlaudKind, opts: { plaud_ids?: string[]; recording_ids?: string[]; all?: boolean } = {}) => {
    const request: PlaudRequest = {
      request_id: newRequestId(), kind, plaud_ids: opts.plaud_ids ?? [], recording_ids: opts.recording_ids ?? [],
      all: opts.all ?? false,
    };
    sent.current.set(request.request_id, request);
    sendRequest(request);
    return request.request_id;
  }, [sendRequest]);

  useShinyMessageHandler<PlaudResult>("plaud_result", (reply) => {
    const request = sent.current.get(reply.request_id);
    if (!request) return; // not this window's
    setResults((r) => ({ ...r, [reply.request_id]: reply }));
    setLast(reply);
    if (FINAL.has(reply.state) && request.kind !== "release-preview") {
      const id = `plaud-${reply.request_id}`;
      const opens = request.kind === "import" && reply.state === "done" && viewer.current;
      const viewIt = opens ? { label: "View", run: () => viewer.current?.() } : null;
      toast(<ActionToast id={id} text={[reply.message]} undo={viewIt} />, { duration: Infinity, id });
    }
  });

  const why = useCallback((kind: PlaudKind) => {
    if (!view) return NO_PLAUD;
    return kindReason(kind, view.available ? null : (view.reason ?? NO_PLAUD), view);
  }, [view]);

  const handles = useCallback((kind: PlaudKind) => view?.handled.includes(kind) ?? false, [view]);

  return useMemo(() => ({
    send, results, last, lastLine: view?.last_result ?? null,
    available: view?.available ?? false, reason: view?.reason ?? null, why, handles, gate: view?.gate ?? null,
  }), [send, results, last, view, why, handles]);
}
