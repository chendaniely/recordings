import type { ReactNode } from "react";

import { NO_PLAUD, kindReason, usePlaudContext } from "../plaud/usePlaud";
import type { SyncPanel as Panel } from "../types";
import { CodeList, Reasons, SyncBreakerLine, SyncHoldLine, SyncSummary, archiveText, consentBody, equationText, n, reasonsFor } from "./SyncSummary";

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
  // Off while the gate is closed too: the worker refuses Accept then (spec §9.3), as on the Sync screen.
  const holdWhy = panel.hold?.accept ? (kindReason("accept", why, panel) ?? plaud.gate) : null;
  const breakerWhy = panel.breaker?.accept ? (kindReason("accept-changes", why, panel) ?? plaud.gate) : null;
  const held = reasonsFor("sync-panel-held", [holdWhy, breakerWhy]);
  const { numbers, details } = panel;
  // The records that couldn't be read leave the panel's details empty, with no normaliser version:
  // nothing was measured, so it draws no numbers and no Details.
  const measured = !panel.no_sync_yet && details.normaliser !== "";
  return (
    <section className="card sync-panel" aria-labelledby="status-sync" data-testid="sync-panel">
      <h2 id="status-sync">Plaud sync</h2>
      <SyncSummary summary={panel.summary} link={panel.demo || panel.no_sync_yet ? "none" : "add"} />
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
      {measured && (
        <>
          <dl className="sync-numbers" data-testid="sync-numbers">
            {numbers.equation && <Row label="Listing">{equationText(numbers.equation)}{numbers.equation.listing_at && ` · from the listing at ${numbers.equation.listing_at}`}</Row>}
            {numbers.equation && numbers.equation.arrive_private > 0 && <Row label="Arrive private">{n(numbers.equation.arrive_private)} of the waiting and missing</Row>}
            {panel.listed && numbers.archive && <Row label="Archive">{archiveText(numbers.archive)}</Row>}
            {numbers.consent && <Row label="Consent list">{consentBody(numbers.consent)}</Row>}
            {numbers.last_good_listing && <Row label="Last good listing">{numbers.last_good_listing}</Row>}
            {numbers.next_sync && <Row label="Next sync">{numbers.next_sync}</Row>}
            {numbers.sweep && <Row label="Sweep">{numbers.sweep}</Row>}
            {numbers.sign_in && <Row label="Sign-in">{numbers.sign_in}</Row>}
            {numbers.full && <Row label="Sync everything">{numbers.full}</Row>}
            {panel.listed && (
              <>
                <Row label="Held for review">{n(numbers.held)}</Row>
                <Row label="Gone from Plaud">{n(numbers.gone)}</Row>
                <Row label="Failing">{n(numbers.failing)}</Row>
                <Row label="Parked">{n(numbers.parked)}</Row>
              </>
            )}
            {numbers.worker && <Row label="Plaud worker">{numbers.worker}</Row>}
            {numbers.healthchecks && <Row label="Healthchecks">{numbers.healthchecks}</Row>}
          </dl>
          <details className="sync-details" data-testid="sync-details">
            <summary>Details</summary>
            <dl className="sync-numbers">
              <Row label="Normaliser">version {details.normaliser}</Row>
              {details.fields.length > 0 && <Row label="Fields added or gone"><CodeList items={details.fields} /></Row>}
              {Object.keys(details.census).length > 0 && (
                <Row label="ID forms">{Object.entries(details.census).map(([form, count]) => `${form}: ${n(count)}`).join(" · ")}</Row>
              )}
              {details.form_change && <Row label="Form change">{details.form_change}</Row>}
              {Object.keys(details.changes_24h).length > 0 && (
                <Row label="Changes in 24 hours">{Object.entries(details.changes_24h).map(([part, count]) => `${part} ${n(count)}`).join(" · ")}</Row>
              )}
              {details.named_share && <Row label="Named segments">{details.named_share}</Row>}
              <Row label="Keep-out words">{details.keep_out_words.length > 0 ? <CodeList items={details.keep_out_words} /> : "none"}</Row>
              {panel.listed && <Row label="Malformed listing rows">{n(details.malformed)}</Row>}
            </dl>
            {details.runs.length > 0 && (
              <>
                <h3>The last {n(details.runs.length)} runs</h3>
                <ul className="sync-runs">{details.runs.map((r, i) => <li key={`${r.at}-${i}`}>{r.at}: {r.result}</li>)}</ul>
              </>
            )}
          </details>
        </>
      )}
    </section>
  );
}
