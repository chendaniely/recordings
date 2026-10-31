import { useId, useState } from "react";

import { Button } from "@/components/ui/button";

import type { ArchiveEquation, ConsentSummary, SyncBreaker, SyncEquation, SyncFlag, SyncHold, SyncSummaryView } from "../types";

const n = (x: number) => x.toLocaleString("en");

/** The one summary line the Sync screen and Status's panel share (2b spec §15): "All here ·
 * checked 14:20 · next sync 14:40", explained under it, or the problem in one sentence. On the
 * Sync screen a problem links to Status; on Status the line links to the Sync screen. */
export function SyncSummary({ summary, link }: { summary: SyncSummaryView; link: "status" | "add" }) {
  return (
    <div className={`sync-summary ${summary.kind}`} data-testid="sync-summary">
      <p className="sync-sentence">
        {summary.sentence}
        {link === "status" && summary.see_status && <> {"·"} <a href="#/status">See Status</a></>}
        {link === "add" && summary.kind !== "demo" && summary.kind !== "no-sync" && (
          <> {"·"} <a href="#/add">Open the Sync screen</a></>
        )}
      </p>
      {summary.explain && <p className="muted">{summary.explain}</p>}
    </div>
  );
}

/** "Listed 9 = 4 here + 2 waiting + 2 excluded + 1 missing" (§11.3). */
export function equationText(eq: SyncEquation): string {
  return `Listed ${n(eq.listed)} = ${n(eq.here)} here + ${n(eq.waiting)} waiting + ${n(eq.excluded)} excluded + ${n(eq.missing)} missing`;
}

/** "In the archive from Plaud: 4 = 4 here + 0 gone from Plaud", and the absent term only when it
 * isn't 0 (§11.3). */
export function archiveText(a: ArchiveEquation): string {
  const absent = a.absent ? ` + ${n(a.absent)} absent from this listing` : "";
  return `In the archive from Plaud: ${n(a.from_plaud)} = ${n(a.here)} here + ${n(a.gone)} gone from Plaud${absent}`;
}

/** "Consent list: 14 IDs, 4 on Plaud now · file changed 2026-10-23 09:12 · read 14:20" (§12.1). */
export function consentText(c: ConsentSummary): string {
  if (c.stop) return `Consent list: imports stopped: ${c.stop}`;
  const on = c.on_plaud === null ? "on Plaud: unknown until the first listing" : `${n(c.on_plaud)} on Plaud now`;
  const ids = `${n(c.ids)} ID${c.ids === 1 ? "" : "s"}`;
  return [`Consent list: ${ids}, ${on}`, c.file_changed && `file changed ${c.file_changed}`, c.read_at && `read ${c.read_at}`]
    .filter(Boolean).join(" · ");
}

/** Puts `text` on the clipboard: through the Clipboard API where the page may use it (a secure
 * context), else from a selected, hidden textarea with execCommand("copy"), which a page served
 * over plain HTTP on the tailnet still may. Focus goes back where it was. False when neither
 * worked. */
export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // refused (no permission, or not focused): try the old way
    }
  }
  const back = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  try {
    return typeof document.execCommand === "function" && document.execCommand("copy");
  } catch {
    return false;
  } finally {
    area.remove();
    back?.focus({ preventScroll: true });
  }
}

/** Copy ID: the whole ID in the consent list's own form (`` - `of_…` — ``), which `make
 * consent-add` and the list read as an exclusion (§15.1). Copying sends no request. Where the
 * browser lets nothing copy, the text shows, one tap selecting all of it (app.css), to copy by
 * hand. */
export function CopyId({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  return (
    <>
      <button type="button" className="sync-copy" data-testid="copy-id" aria-label={`Copy ID ${label}`}
        onClick={() => void copyText(text).then((ok) => setState(ok ? "copied" : "failed"))}>
        {state === "copied" ? "Copied" : "Copy ID"}
      </button>
      {state === "failed" && (
        <span className="sync-copy-failed" role="status" data-testid="copy-failed">
          Copy failed: select it <code className="sync-copy-text">{text}</code>
        </span>
      )}
    </>
  );
}

/** A hold (§11.4): its sentence and Accept this listing, or the reason Accept is refused. */
export function SyncHoldLine({ hold, why, onAccept }: { hold: SyncHold; why: string | null; onAccept: () => void }) {
  const refused = hold.refused ?? (hold.accept ? why : null);
  return (
    <div className="sync-hold" data-testid="sync-hold">
      <p>{hold.sentence}</p>
      {hold.accept && <Button data-request="accept" disabled={why !== null} onClick={onAccept}>Accept this listing</Button>}
      {refused && <p className="sync-why">{refused}</p>}
    </div>
  );
}

/** A breaker trip (§14.4): its sentence, the field names (never values), and Accept these
 * changes, or why it needs a code fix. */
export function SyncBreakerLine({ breaker, why, onAccept }: { breaker: SyncBreaker; why: string | null; onAccept: () => void }) {
  return (
    <div className="sync-breaker" data-testid="sync-breaker">
      <p>
        {breaker.sentence}
        {breaker.fields.length > 0 && <> ({breaker.fields.map((f, i) => <span key={f}>{i > 0 && ", "}<code>{f}</code></span>)})</>}
      </p>
      {breaker.accept && <Button data-request="accept-changes" disabled={why !== null} onClick={onAccept}>Accept these changes</Button>}
      {breaker.code_fix && <p className="sync-why">{breaker.code_fix}</p>}
      {breaker.accept && why && <p className="sync-why">{why}</p>}
    </div>
  );
}

/** One privacy flag (P15), by recording ID with its own next step; never a title (§15.1). A
 * sentence that names its recording itself (a `private` that couldn't be written, with its cause)
 * isn't prefixed with the ID again. Keep it here, when it is off, says why beside it. */
export function FlagRow({ flag, why, onKeepHere }: { flag: SyncFlag; why: string | null; onKeepHere: () => void }) {
  const whyId = useId();
  const prefix = flag.recording_id && !flag.sentence.includes(flag.recording_id);
  return (
    <li className="sync-row" data-testid="flag-row">
      <span className="sync-flag">
        {prefix && <><span className="mono">{flag.recording_id}</span>: </>}
        {flag.sentence}
      </span>
      {flag.plaud_id && <span className="mono">{flag.plaud_id}</span>}
      {flag.date && <span className="sync-when">{flag.date}{flag.duration && ` · ${flag.duration}`}</span>}
      {flag.copy_id && flag.plaud_id && <CopyId text={flag.copy_id} label={flag.plaud_id} />}
      {flag.keep_here && (
        <Button variant="outline" data-request="keep-here" disabled={why !== null} aria-describedby={why ? whyId : undefined}
          onClick={onKeepHere}>
          Keep it here
        </Button>
      )}
      {flag.keep_here && why && <span id={whyId} className="sync-why">{why}</span>}
    </li>
  );
}

/** Why some buttons are off, each reason said once, for the buttons' aria-describedby. */
export function reasonsFor(prefix: string, reasons: (string | null)[]): { list: string[]; idOf: (r: string | null) => string | undefined } {
  const list = [...new Set(reasons.filter((r): r is string => r !== null))];
  return { list, idOf: (r) => (r === null ? undefined : `${prefix}-why-${list.indexOf(r)}`) };
}

export function Reasons({ prefix, list }: { prefix: string; list: string[] }) {
  return <>{list.map((r, i) => <p key={r} id={`${prefix}-why-${i}`} className="sync-why">{r}</p>)}</>;
}
