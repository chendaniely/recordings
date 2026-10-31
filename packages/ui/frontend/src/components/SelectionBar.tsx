import { useContext, useId, useState } from "react";

import { NO_PLAUD, PlaudContext, replyText, type Plaud } from "../plaud/usePlaud";

export const MAX_RESYNC = 1_000; // a resync request's cap (2b spec §9.3)
export const TOO_MANY_RESYNC = "For more, use Sync everything";

/** Why Re-sync from Plaud is off for this selection, or null (2b spec §14.6): too many; this app
 * can't ask, or the worker can't handle a re-sync yet (`Plaud.why`); or the gate, which holds
 * every re-sync until the first catch-up (§11.6). */
export function resyncReason(plaud: Plaud | null, count: number): string | null {
  if (count > MAX_RESYNC) return TOO_MANY_RESYNC;
  if (!plaud) return NO_PLAUD;
  return plaud.why("resync") ?? plaud.gate;
}

/** What both owners of the bar (the Library, the grid) pass for Re-sync, from the bar's own
 * targets (`targetsOf`, in list order): why it is off, the press (it returns the request's ID, or
 * nothing without a Plaud context), and the selection's key, by which the bar knows a reply is
 * about another selection. */
export function resyncProps(plaud: Plaud | null, targets: readonly string[]) {
  return {
    resync: resyncReason(plaud, targets.length),
    onResync: (): string | undefined => plaud?.send("resync", { recording_ids: [...targets] }),
    selection: targets.join(","),
  };
}

/** "12 selected · Tag… (T) · Re-sync from Plaud · Clear (Esc)". The selection stays after an
 * action, so a second tag can follow, and the bar adds "just tagged `work`" (spec §6): the tags
 * last added to this very selection, a tag's name as code, several as a count ("just tagged 3
 * tags"). Re-sync sends one request for the selection, and the reply to that request shows here
 * (not another's, and not past Clear or a new selection): "Re-syncing (12 of 25)", then the end
 * line (2b spec §14.6). Re-sync is drawn only once the
 * worker handles it (`Plaud.handles`): until then (all of Phase 1) the bar has no dead button. Esc
 * inside the bar clears, as it says. On phones it sits above the bottom tab bar (app.css). */
export function SelectionBar({ count, lastTagged, onTag, onClear, onResync, resync, selection }: {
  count: number; lastTagged: string[] | null; onTag: () => void; onClear: () => void;
  onResync: () => string | undefined; // sends the request, and returns its ID
  resync: string | null; // null: enabled; else why not
  selection: string; // changes with the selection, which drops the reply
}) {
  const plaud = useContext(PlaudContext);
  const whyId = useId();
  const [sent, setSent] = useState<{ id: string; selection: string } | null>(null);
  const reply = sent && sent.selection === selection ? plaud?.results[sent.id] : undefined;
  const offered = plaud?.handles("resync") ?? false;
  if (count < 2) return null;
  return (
    <div
      className="selection-bar"
      role="region"
      aria-label="Selection"
      data-testid="selection-bar"
      onKeyDown={(e) => {
        if (e.key !== "Escape" || e.defaultPrevented) return;
        e.preventDefault();
        onClear();
        setSent(null);
      }}
    >
      <span>{count.toLocaleString("en")} selected</span>
      <button type="button" onClick={onTag}>Tag{"…"} <kbd>T</kbd></button>
      {/* On a phone "from Plaud" is dropped (app.css), so the bar stays one line. */}
      {offered && (
        <button type="button" data-request="resync" disabled={resync !== null} aria-describedby={resync ? whyId : undefined}
          onClick={() => {
            const id = onResync();
            if (id) setSent({ id, selection });
          }}>Re-sync<span className="resync-long"> from Plaud</span></button>
      )}
      <button type="button" onClick={() => { onClear(); setSent(null); }}>Clear <kbd>Esc</kbd></button>
      {/* Too many is said aloud; any other reason (the demo, no config.toml, the gate)
          only to a screen reader and as the button's description, so the bar stays one line where
          nothing can be synced. */}
      {offered && resync && <span className={resync === TOO_MANY_RESYNC ? "muted" : "sr-only"} id={whyId}>{resync}</span>}
      {reply && (
        <span className="muted" role="status" data-testid="resync-reply">
          {replyText(reply)}
        </span>
      )}
      {lastTagged && lastTagged.length > 0 && (
        <span className="muted">
          just tagged {lastTagged.length === 1 ? <code>{lastTagged[0]}</code> : `${lastTagged.length} tags`}
        </span>
      )}
    </div>
  );
}
