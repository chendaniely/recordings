import type { ReactNode } from "react";

import { NO_PLAUD, kindReason, usePlaudContext } from "../plaud/usePlaud";
import type { SyncPanel as Panel } from "../types";
import { Reasons, SyncBreakerLine, SyncHoldLine, SyncSummary, archiveText, consentText, equationText, n, reasonsFor } from "./SyncSummary";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return <><dt>{label}</dt><dd>{children}</dd></>;
}

/** Status's sync panel (2b spec §15.2, J5): plain sentences first, each true now or not shown;
 * then the numbers, which add up; then Details, folded away. Status reports, and the Sync screen
 * and Details act: a line here offers no Re-sync or Park (a recording that fails every check is
 * re-synced or parked there). The one exception is Accept this listing and Accept these changes,
 * which a hold and a breaker trip offer on both, off with the reason while the worker can't
 * handle them yet. No title anywhere: recordings are named by ID (P13). Needs attention already
 * holds the panel's lines (StatusView.attention). Before any listing (`listed` false, all of
 * Phase 1) it shows no zero counts: held, gone, failing, parked and malformed rows wait for the
 * phase that measures them. */
export function SyncPanel({ panel }: { panel: Panel }) {
  const plaud = usePlaudContext();
  const why = plaud.available ? null : (plaud.reason ?? NO_PLAUD);
  const holdWhy = panel.hold?.accept ? kindReason("accept", why, panel) : null;
  const breakerWhy = panel.breaker?.accept ? kindReason("accept-changes", why, panel) : null;
  const held = reasonsFor("sync-panel-held", [holdWhy, breakerWhy]);
  const { numbers: x, details: d } = panel;
  return (
    <section className="card sync-panel" aria-labelledby="status-sync" data-testid="sync-panel">
      <h2 id="status-sync">Plaud sync</h2>
      <SyncSummary summary={panel.summary} link="add" />
      {panel.failing && <p className="warn" data-testid="sync-failing">{panel.failing}</p>}
      {panel.not_pinged && <p className="warn" data-testid="sync-not-pinged">{panel.not_pinged}</p>}
      {panel.conditions.length > 0 && (
        <ul className="sync-conditions" data-testid="sync-conditions">{panel.conditions.map((c) => <li key={c}>{c}</li>)}</ul>
      )}
      {panel.hold && <SyncHoldLine hold={panel.hold} why={holdWhy} whyId={held.idOf(holdWhy)} onAccept={() => plaud.send("accept")} />}
      {panel.breaker && (
        <SyncBreakerLine breaker={panel.breaker} why={breakerWhy} whyId={held.idOf(breakerWhy)}
          onAccept={() => plaud.send("accept-changes")} />
      )}
      {/* Task 21's lines don't say their reason: it is said once here, as on the Sync screen. */}
      {(panel.hold || panel.breaker) && <Reasons prefix="sync-panel-held" list={held.list} />}
      {!panel.no_sync_yet && (
        <>
          <dl className="sync-numbers" data-testid="sync-numbers">
            {x.equation && <Row label="Listing">{equationText(x.equation)}{x.equation.listing_at && ` · from the listing at ${x.equation.listing_at}`}</Row>}
            {x.equation && x.equation.arrive_private > 0 && <Row label="Arrive private">{n(x.equation.arrive_private)} of the waiting and missing</Row>}
            {x.archive && <Row label="Archive">{archiveText(x.archive)}</Row>}
            {x.consent && <Row label="Consent list">{consentText(x.consent).replace(/^Consent list: /, "")}</Row>}
            {x.last_good_listing && <Row label="Last good listing">{x.last_good_listing}</Row>}
            {x.next_sync && <Row label="Next sync">{x.next_sync}</Row>}
            {x.sweep && <Row label="Sweep">{x.sweep}</Row>}
            {x.sign_in && <Row label="Sign-in">{x.sign_in}</Row>}
            {x.full && <Row label="Sync everything">{x.full}</Row>}
            {panel.listed && (
              <>
                <Row label="Held for review">{n(x.held)}</Row>
                <Row label="Gone from Plaud">{n(x.gone)}</Row>
                <Row label="Failing">{n(x.failing)}</Row>
                <Row label="Parked">{n(x.parked)}</Row>
              </>
            )}
            {x.worker && <Row label="Plaud worker">{x.worker}</Row>}
            {x.healthchecks && <Row label="Healthchecks">{x.healthchecks}</Row>}
          </dl>
          <details className="sync-details" data-testid="sync-details">
            <summary>Details</summary>
            <dl className="sync-numbers">
              <Row label="Normaliser">version {d.normaliser}</Row>
              {d.fields.length > 0 && <Row label="Fields added or gone">{d.fields.map((f, i) => <span key={f}>{i > 0 && ", "}<code>{f}</code></span>)}</Row>}
              {Object.keys(d.census).length > 0 && (
                <Row label="ID forms">{Object.entries(d.census).map(([form, count]) => `${form}: ${n(count)}`).join(" · ")}</Row>
              )}
              {d.form_change && <Row label="Form change">{d.form_change}</Row>}
              {Object.keys(d.changes_24h).length > 0 && (
                <Row label="Changes in 24 hours">{Object.entries(d.changes_24h).map(([part, count]) => `${part} ${n(count)}`).join(" · ")}</Row>
              )}
              {d.named_share && <Row label="Named segments">{d.named_share}</Row>}
              <Row label="Keep-out words">{d.keep_out_words.length > 0 ? d.keep_out_words.map((w, i) => <span key={w}>{i > 0 && ", "}<code>{w}</code></span>) : "none"}</Row>
              {panel.listed && <Row label="Malformed listing rows">{n(d.malformed)}</Row>}
            </dl>
            {d.runs.length > 0 && (
              <>
                <h3>The last {n(d.runs.length)} runs</h3>
                <ul className="sync-runs">{d.runs.map((r, i) => <li key={`${r.at}-${i}`}>{r.at}: {r.result}</li>)}</ul>
              </>
            )}
          </details>
        </>
      )}
    </section>
  );
}
