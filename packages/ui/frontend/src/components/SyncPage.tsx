import { Lock } from "lucide-react";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";

import { FINAL, NO_PLAUD, kindReason, usePlaudContext } from "../plaud/usePlaud";
import { useShinyOutputError, useShinyOutputValue } from "../sr";
import type { KeptOutRow, NotHereRow, PlaudKind, PlaudResult, SyncView } from "../types";
import { ReleaseDialog } from "./ReleaseDialog";
import {
  CopyId, FlagRow, Reasons, SyncBreakerLine, SyncHoldLine, SyncSummary, archiveText, consentText, equationParts, n, reasonsFor,
} from "./SyncSummary";

const FULL_UNKNOWN = "unknown until the first run";
const BY: Record<KeptOutRow["by"], string> = {
  consent: "On the consent list",
  title: "Kept out by title",
  rekeyed: "May be a consent-listed or kept-out recording back under a new ID: confirm (runbook)",
  "was-title": "Was kept out by title",
};

/** A section of the Sync screen: a card with its heading, in §15.1's order. */
function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section className="card sync-section" id={id} data-testid={id} aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`} tabIndex={-1}>{title}</h2>
      {children}
    </section>
  );
}

/** A number in an equation that takes you to its list (§15.1). A button, never a hash link: the
 * hash chooses the page (CLAUDE.md). */
function Jump({ to, children }: { to: string; children: ReactNode }) {
  return (
    <button type="button" className="sync-jump" onClick={() => document.getElementById(to)?.scrollIntoView({ block: "start" })}>
      {children}
    </button>
  );
}

const when = (r: { date: string | null; time: string | null; duration: string | null }) =>
  [[r.date, r.time].filter(Boolean).join(" "), r.duration].filter(Boolean).join(" · ");

/** What a request of this page's is doing now: "Importing 5 of 31…", or how it ended. */
function pendingText(r: PlaudResult): string {
  return r.progress && r.state === "running" ? `${r.message} (${n(r.progress.done)} of ${n(r.progress.total)})` : r.message;
}

/** The Sync screen, `#/add` (2b spec §15.1, J6): the Add page's Plaud panel. It acts; Status
 * reports. Every button sends one request, only when pressed; importing never asks "are you
 * sure?" (§11.5), and only Release asks first (§12.2). Titles show only in Not here yet and
 * Updated, and in Release's own question; never in Kept out or the flags (P13).
 *
 * Before any listing (`listed` false, all of Phase 1) it shows no counts: "No sync has run yet.",
 * the consent list's line and the flags, and none of Not here yet, Kept out, Gone or Parked.
 *
 * A button is off, with its reason said once in its section, when this app can't ask, when the
 * worker can't handle its kind yet (`not_yet`: in Phase 1 every kind but Keep it here), when the
 * gate holds it (every kind but Compare and Keep it here, §11.6), or for its own reason (a hold,
 * the keep-out word). */
export function SyncPage() {
  const view = useShinyOutputValue<SyncView>("plaud_sync");
  const error = useShinyOutputError("plaud_sync");
  const plaud = usePlaudContext();
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  const [asking, setAsking] = useState<{ request: string; plaud_id: string } | null>(null);
  const [titles, setTitles] = useState<Record<string, string>>({}); // review rows' titles, on demand
  const [kept, setKept] = useState<Record<string, string>>({}); // flag -> its Keep it here request

  // Focus must not fall to <body> when what has it goes: a Keep it here whose flag a redraw drops,
  // or Import selected, which turns itself off once it has sent. It goes to the heading of the
  // section the control was in, else the page's own.
  const focused = useRef<{ el: HTMLElement; home: string | null } | null>(null);
  useLayoutEffect(() => {
    const last = focused.current;
    if (!last) return;
    const gone = !last.el.isConnected || (last.el instanceof HTMLButtonElement && last.el.disabled);
    const now = document.activeElement;
    if (!gone || (now && now !== document.body && now !== last.el)) return;
    focused.current = null;
    const home = last.home && document.getElementById(`${last.home}-h`);
    (home || document.getElementById("sync-page-h"))?.focus({ preventScroll: true });
  });

  if (error) return <main className="sync-page"><div className="problems" role="alert" data-testid="sync-error">The Sync screen can't be shown: {error.message}</div></main>;
  if (!view) return <main className="sync-page"><p className="empty">Loading…</p></main>;

  const b = view.buttons;
  const why = plaud.available ? null : (plaud.reason ?? NO_PLAUD);
  const off = (kind: PlaudKind, own: string | null = null) => kindReason(kind, why, view) ?? own;
  const gated = (kind: PlaudKind) => off(kind, view.gate);
  const answer = asking ? plaud.results[asking.request] : undefined;
  const preview = answer?.state === "done" ? answer.preview : null;
  const ended = answer !== undefined && FINAL.has(answer.state) && !preview;
  const pending = plaud.last && !FINAL.has(plaud.last.state) ? plaud.last : null;
  const sync = off("sync", b.sync);
  const compare = off("compare", b.compare);
  const full = off("full", b.full);
  const importing = off("import", b.import);
  const parking = gated("park");
  const previewing = gated("release-preview"); // Release's question, and a review row's Show title
  const unreleasing = gated("unrelease");
  const unparking = gated("unpark");
  const actions = reasonsFor("sync-actions", [sync, compare, full]);
  const imports = reasonsFor("sync-not-here", [
    importing, view.not_here.some((r) => r.park) ? parking : null, view.not_here.some((r) => r.review) ? previewing : null,
  ]);
  const keptOut = reasonsFor("sync-kept-out", [
    view.kept_out.some((r) => r.release && !r.released) ? previewing : null, view.kept_out.some((r) => r.released) ? unreleasing : null,
  ]);
  const parked = reasonsFor("sync-parked", [view.parked.length > 0 ? unparking : null]);
  const holdWhy = view.hold?.accept ? gated("accept") : null;
  const breakerWhy = view.breaker?.accept ? gated("accept-changes") : null;
  const held = reasonsFor("sync-held", [holdWhy, breakerWhy]); // said once, when both have one
  // What is ticked and still listed: a redraw that drops a row drops it from the count.
  const listed = new Set(view.not_here.map((r) => r.plaud_id));
  const equation = view.equation ? equationParts(view.equation) : null;
  const chosen = [...picked].filter((id) => listed.has(id));
  const ready = view.not_here.filter((r) => !r.review);
  const choose = (id: string, on: boolean) => setPicked((p) => {
    const next = new Set(p);
    if (on) next.add(id); else next.delete(id);
    return next;
  });
  const importChosen = () => {
    plaud.send("import", { plaud_ids: chosen });
    setPicked(new Set());
  };
  const release = (r: KeptOutRow) => setAsking({ request: plaud.send("release-preview", { plaud_ids: [r.plaud_id] }), plaud_id: r.plaud_id });
  const showTitle = (r: NotHereRow) => {
    const request = plaud.send("release-preview", { plaud_ids: [r.plaud_id] });
    setTitles((t) => ({ ...t, [r.plaud_id]: request }));
  };

  return (
    <main className="sync-page" data-testid="sync-page"
      onFocus={(e) => { focused.current = { el: e.target as HTMLElement, home: e.target.closest("section")?.id ?? null }; }}>
      <h1 id="sync-page-h" tabIndex={-1}>Add</h1>
      <SyncSummary summary={view.summary} link="status" />

      {view.flags.length > 0 && (
        <Section id="sync-flags" title="Flags">
          <ul className="sync-rows">
            {view.flags.map((f) => {
              const key = `${f.kind}-${f.recording_id ?? f.plaud_id}`;
              return (
                <FlagRow key={key} flag={f} why={off("keep-here")}
                  reply={kept[key] ? plaud.results[kept[key]]?.message ?? null : null}
                  onKeepHere={() => {
                    const id = f.recording_id;
                    if (id) setKept((k) => ({ ...k, [key]: plaud.send("keep-here", { recording_ids: [id] }) }));
                  }} />
              );
            })}
          </ul>
        </Section>
      )}

      {(view.hold || view.breaker) && (
        <Section id="sync-held" title="Held">
          {view.hold && <SyncHoldLine hold={view.hold} why={holdWhy} whyId={held.idOf(holdWhy)} onAccept={() => plaud.send("accept")} />}
          {view.breaker && (
            <SyncBreakerLine breaker={view.breaker} why={breakerWhy} whyId={held.idOf(breakerWhy)} onAccept={() => plaud.send("accept-changes")} />
          )}
          <Reasons prefix="sync-held" list={held.list} />
        </Section>
      )}

      <Section id="sync-actions" title="Sync">
        <div className="sync-buttons">
          <Button data-request="sync" disabled={sync !== null} aria-describedby={actions.idOf(sync)} onClick={() => plaud.send("sync")}>
            Sync now
          </Button>
          <Button variant="outline" data-request="compare" disabled={compare !== null} aria-describedby={actions.idOf(compare)}
            onClick={() => plaud.send("compare")}>
            Compare
          </Button>
        </div>
        <details className="sync-more">
          <summary>More</summary>
          <div className="sync-buttons">
            <Button variant="outline" data-request="full" disabled={full !== null} aria-describedby={actions.idOf(full)}
              onClick={() => plaud.send("full")}>
              Sync everything
            </Button>
            <span className="muted">{view.full_estimate === FULL_UNKNOWN ? "Its time: unknown until the first run" : `Takes ${view.full_estimate}`}</span>
          </div>
        </details>
        <Reasons prefix="sync-actions" list={actions.list} />
        {view.next_sync && <p className="muted">Next sync {view.next_sync}</p>}
        {pending && <p className="sync-pending" role="status" data-testid="sync-pending">{pendingText(pending)}</p>}
        {plaud.lastLine && <p className="sync-last" role="status" data-testid="sync-last-result">{plaud.lastLine}</p>}
      </Section>

      <Section id="sync-equations" title="From the listing">
        {!view.listed ? (
          // Said here unless the summary already says it: the gate's reason is also kind "no-sync"
          // (Task 19's fix round), and under it the section would be empty.
          view.summary.sentence !== "No sync has run yet" && <p data-testid="sync-no-listing">No sync has run yet.</p>
        ) : view.equation && equation ? (
          <>
            <p>
              {equation.listed} = {equation.here} + <Jump to="sync-not-here">{equation.waiting}</Jump> +{" "}
              <Jump to="sync-kept-out">{equation.excluded}</Jump> + <Jump to="sync-not-here">{equation.missing}</Jump>
              {view.equation.listing_at && <span className="muted"> {"·"} from the listing at {view.equation.listing_at}</span>}
            </p>
            {view.equation.arrive_private > 0 && (
              <p>Of the waiting and missing, {n(view.equation.arrive_private)} arrive{view.equation.arrive_private === 1 ? "s" : ""} private.</p>
            )}
          </>
        ) : compare === null && <p className="muted">No listing yet: Compare reads one.</p>}
        {view.listed && view.archive && <p>{archiveText(view.archive)}</p>}
        {view.consent && <p>{consentText(view.consent)}</p>}
      </Section>

      {view.listed && (
        <Section id="sync-not-here" title="Not here yet">
          {view.titles_hidden && <p className="muted" data-testid="titles-hidden">{view.titles_hidden[0].toUpperCase() + view.titles_hidden.slice(1)}.</p>}
          {view.not_here.length === 0 ? <p className="muted">Nothing is waiting.</p> : (
            <ul className="sync-rows">
              {view.not_here.map((r) => {
                const shown = titles[r.plaud_id] ? plaud.results[titles[r.plaud_id]]?.preview?.title : undefined;
                // A review row's title comes only on demand, and none shows while titles are hidden,
                // whatever the server sent (P13, as Kept out and the flags).
                const title = r.review || view.titles_hidden ? null : r.title;
                return (
                  <li key={r.plaud_id} className="sync-row" data-testid="not-here-row">
                    {/* A review row has its box too (§11.6): Import selected brings it in, Import all
                        leaves it out. */}
                    <label className="sync-check">
                      <input type="checkbox" checked={picked.has(r.plaud_id)} onChange={(e) => choose(r.plaud_id, e.target.checked)}
                        aria-label={`Choose the recording of ${when(r)}`} />
                    </label>
                    <span className="sync-when">{when(r)}</span>
                    <span className="sync-title">
                      {title ?? shown ?? <span className="muted">{r.review ? "title on demand" : "title hidden"}</span>}
                      {r.arrives_private && <> <Lock size={13} className="lock" aria-hidden="true" /> arrives private</>}
                    </span>
                    <span className="sync-state">{r.state}</span>
                    {r.park && (
                      <Button variant="outline" data-request="park" disabled={parking !== null} aria-describedby={imports.idOf(parking)}
                        onClick={() => plaud.send("park", { plaud_ids: [r.plaud_id] })}>Park</Button>
                    )}
                    {r.review && !shown && (
                      <Button variant="outline" data-request="release-preview" disabled={previewing !== null} aria-describedby={imports.idOf(previewing)}
                        onClick={() => showTitle(r)}>Show title</Button>
                    )}
                    <CopyId text={r.copy_id} label={r.plaud_id} />
                  </li>
                );
              })}
            </ul>
          )}
          <div className="sync-buttons">
            <Button data-request="import" data-testid="import-selected" disabled={importing !== null || chosen.length === 0}
              aria-describedby={imports.idOf(importing)} onClick={importChosen}>
              Import selected{chosen.length > 0 ? ` (${n(chosen.length)})` : ""}
            </Button>
            <Button variant="outline" data-request="import" data-testid="import-all" disabled={importing !== null || ready.length === 0}
              aria-describedby={imports.idOf(importing)} onClick={() => plaud.send("import", { all: true })}>
              Import all {n(view.import_all.count)}
            </Button>
            {view.import_all.review_more > 0 && <span className="muted">{"·"} {n(view.import_all.review_more)} more need your review</span>}
          </div>
          <Reasons prefix="sync-not-here" list={imports.list} />
        </Section>
      )}

      {view.listed && (
        <Section id="sync-kept-out" title="Kept out">
          {view.kept_out.length === 0 ? <p className="muted">Nothing is kept out.</p> : (
            <ul className="sync-rows">
              {view.kept_out.map((r) => (
                <li key={r.plaud_id} className="sync-row" data-testid="kept-out-row">
                  <span className="sync-by">{BY[r.by]}</span>
                  <span className="mono">{r.plaud_id}</span>
                  {when(r) && <span className="sync-when">{when(r)}</span>}
                  <CopyId text={r.copy_id} label={r.plaud_id} />
                  {r.released ? (
                    <span className="sync-released">
                      {r.released} {"·"}{" "}
                      <Button variant="outline" data-request="unrelease" disabled={unreleasing !== null} aria-describedby={keptOut.idOf(unreleasing)}
                        onClick={() => plaud.send("unrelease", { plaud_ids: [r.plaud_id] })}>Undo</Button>
                    </span>
                  ) : r.release && (
                    <Button variant="outline" data-request="release-preview" data-release-for={r.plaud_id} disabled={previewing !== null}
                      aria-describedby={keptOut.idOf(previewing)} onClick={() => release(r)}>Release</Button>
                  )}
                  {asking?.plaud_id === r.plaud_id && ended && answer && <span className="sync-why" role="status">{answer.message}</span>}
                </li>
              ))}
            </ul>
          )}
          <Reasons prefix="sync-kept-out" list={keptOut.list} />
        </Section>
      )}

      {/* Drawn only with rows: nothing reads Plaud's edits until edit-sync (Phase 3), so "Nothing
          changed on Plaud this week" would be a sentence nothing measured (P2). */}
      {view.updated.length > 0 && (
        <Section id="sync-updated" title="Updated on Plaud (7 days)">
          <ul className="sync-rows">
            {view.updated.map((u, i) => (
              <li key={`${u.recording_id}-${i}`} className="sync-row" data-testid="updated-row">
                <span className="mono">{u.recording_id}</span>
                {u.title && <span className="sync-title">{u.title}</span>}
                <span>{u.parts.join(", ")}</span>
                <span className="muted">{u.at}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {view.listed && (
        <Section id="sync-gone" title="Gone from Plaud">
          <p>Gone from Plaud: {n(view.gone.rows.length)} {"·"} {n(view.gone.last_7_days)} in the last 7 days</p>
          {view.gone.rows.length > 0 && (
            <ul className="sync-rows">
              {view.gone.rows.map((g) => (
                <li key={g.recording_id} className="sync-row" data-testid="gone-row">
                  <span className="mono">{g.recording_id}</span> <span className="muted">gone since {g.since}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="muted">To take one out of the archive: "Taking a recording out by hand" in the runbook.</p>
        </Section>
      )}

      {view.listed && (
        <Section id="sync-parked" title="Parked">
          <p>Parked: {n(view.parked.length)}</p>
          {view.parked.length > 0 && (
            <ul className="sync-rows">
              {view.parked.map((p) => (
                <li key={p.id} className="sync-row" data-testid="parked-row">
                  <span className="mono">{p.id}</span>
                  <Button variant="outline" data-request="unpark" disabled={unparking !== null} aria-describedby={parked.idOf(unparking)}
                    onClick={() => plaud.send("unpark", p.kind === "recording" ? { recording_ids: [p.id] } : { plaud_ids: [p.id] })}>
                    Unpark
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <Reasons prefix="sync-parked" list={parked.list} />
        </Section>
      )}

      <Section id="sync-own-audio" title="Import your own audio">
        <p className="muted">Coming later.</p>
      </Section>

      {preview && asking && (
        <ReleaseDialog preview={preview} onCancel={() => setAsking(null)}
          onLetIn={() => {
            plaud.send("release", { plaud_ids: [asking.plaud_id] });
            setAsking(null);
          }} />
      )}
    </main>
  );
}
