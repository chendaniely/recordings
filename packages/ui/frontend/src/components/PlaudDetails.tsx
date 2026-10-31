import { useContext, useId, useState } from "react";

import { Button } from "@/components/ui/button";

import { FINAL, NO_PLAUD, PlaudContext, kindReason } from "../plaud/usePlaud";
import { useShinyOutputValue } from "../sr";
import type { PlaudResult, RecordingPlaud, RecordingView } from "../types";
import { CopyId, FlagRow } from "./SyncSummary";

/** A Re-sync's reply in place (§14.6): its progress while it runs, then its fixed phrase. */
function replyText(r: PlaudResult): string {
  if (FINAL.has(r.state) || !r.progress) return r.message;
  return `${r.message} (${r.progress.done} of ${r.progress.total})`;
}

/** Details' Plaud lines (2b spec §15.3): when it was checked and when next, Re-sync from Plaud
 * with its result inline, each Plaud ID with Copy ID, gone from Plaud, why the sync tagged it
 * private (or decision 3, once Dan removed that), and its flags, each with its next step. The
 * recording is the one Details shows: the `recording` output's, which App holds. Re-sync is
 * drawn only once the worker handles it, and the checked line only once checks can run (both
 * Phase 3's; a checked time already recorded still shows): Phase 1's Details has no dead button.
 * When drawn, Re-sync says why it is off, once, and names that reason: this app can't ask, or the
 * server's reason (the gate, a consent listing, a keep-out). */
export function PlaudDetails({ plaud }: { plaud: RecordingPlaud }) {
  const ctx = useContext(PlaudContext);
  const recordingId = useShinyOutputValue<RecordingView | null>("recording")?.id ?? null;
  const [resyncId, setResyncId] = useState<string | null>(null);
  const whyId = useId();
  const why = !ctx ? NO_PLAUD : ctx.available ? null : (ctx.reason ?? NO_PLAUD);
  const offered = plaud.handled.includes("resync");
  const resyncWhy = kindReason("resync", why, plaud) ?? plaud.resync;
  const reply = resyncId && ctx ? ctx.results[resyncId] : undefined;
  return (
    <section className="plaud-details" data-testid="plaud-details" aria-labelledby={`${whyId}-h`}>
      <h3 id={`${whyId}-h`}>Plaud</h3>
      {(offered || plaud.checked) && (
        <p data-testid="plaud-checked">
          Plaud: {plaud.checked ? `checked ${plaud.checked}` : "not checked yet"}
          {plaud.next_by && ` · next by ${plaud.next_by}`}
        </p>
      )}
      {offered && (
        <p className="plaud-resync">
          <Button variant="outline" size="sm" data-request="resync" disabled={resyncWhy !== null || !recordingId}
            aria-describedby={resyncWhy ? whyId : undefined}
            onClick={() => {
              if (ctx && recordingId) setResyncId(ctx.send("resync", { recording_ids: [recordingId] }));
            }}>
            Re-sync from Plaud
          </Button>
          {resyncWhy && <span id={whyId} className="muted"> {resyncWhy}</span>}
          {reply && <span role="status" data-testid="resync-reply"> {replyText(reply)}</span>}
        </p>
      )}
      <ul className="plaud-ids">
        {plaud.plaud_ids.map((p) => (
          <li key={p.id}><span className="mono">{p.id}</span> <CopyId text={p.copy_id} label={p.id} /></li>
        ))}
      </ul>
      {plaud.gone_since && <p data-testid="plaud-gone">Gone from Plaud since {plaud.gone_since} (kept)</p>}
      {plaud.tagged_private && <p data-testid="plaud-private">{plaud.tagged_private}</p>}
      {plaud.removed_private && <p data-testid="plaud-private">{plaud.removed_private}</p>}
      {plaud.flags.length > 0 && (
        <ul className="sync-rows">
          {plaud.flags.map((f) => (
            <FlagRow key={`${f.kind}-${f.plaud_id ?? ""}`} flag={f} why={kindReason("keep-here", why, plaud)}
              onKeepHere={() => {
                const id = f.recording_id ?? recordingId;
                if (ctx && id) ctx.send("keep-here", { recording_ids: [id] });
              }} />
          ))}
        </ul>
      )}
    </section>
  );
}
