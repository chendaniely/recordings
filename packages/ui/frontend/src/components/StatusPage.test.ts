import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { StatusJob, StatusView } from "../types";

const shown = vi.hoisted(() => ({ view: null as unknown, error: null as unknown }));
vi.mock("../sr", () => ({
  useShinyOutputValue: () => shown.view,
  useShinyOutputError: () => shown.error,
  useSetShinyInput: () => () => {},
  useShinyMessageHandler: () => {},
}));

import { PlaudContext, type Plaud } from "../plaud/usePlaud";
import { StatusPage } from "./StatusPage";

const DETAIL = "snapshot 0123456789ab: 11 files, 4096 bytes added; NAS free space unknown";
const backup: StatusJob = {
  job: "backup", label: "Backup", last_ok: "2026-10-09 07:55 (5 min ago)", failing: false,
  detail: null, attention: false, last_ok_detail: DETAIL,
};

function render(view: StatusView): string {
  shown.view = view;
  shown.error = null;
  return renderToStaticMarkup(createElement(StatusPage));
}

describe("StatusPage", () => {
  it("shows a good run's detail under OK, so the NAS's free space reaches the page", () => {
    const html = render({ configured: true, jobs: [backup], attention: [], flags: 0, disk: null });
    expect(html).toContain(`<td>OK<div class="muted" data-testid="status-job-backup-detail">${DETAIL}</div></td>`);
  });
  it("shows a failing job's own detail instead", () => {
    const failing = { ...backup, failing: true, attention: true, detail: "BackupError; exit 1" };
    const html = render({ configured: true, jobs: [failing], attention: [], flags: 0, disk: null });
    expect(html).toContain("Failing: BackupError; exit 1");
    expect(html).not.toContain("status-job-backup-detail");
  });
  it("says which job is running and since when", () => {
    const running = { job: "restore-test", label: "Restore test", since: "2026-10-09 07:10 (50 min ago)" };
    const html = render({ configured: true, jobs: [backup], attention: [], flags: 0, disk: null, running });
    expect(html).toContain('data-testid="status-running"');
    expect(html).toContain("Restore test is running, started 2026-10-09 07:10 (50 min ago)");
    expect(html).toContain("The hourly backup waits for it.");
  });
  it("doesn't say the hourly backup waits for the backup or the disk check", () => {
    for (const [job, label] of [["backup", "Backup"], ["disk", "Disk check"]]) {
      const running = { job, label, since: "2026-10-09 07:10 (50 min ago)" };
      const html = render({ configured: true, jobs: [backup], attention: [], flags: 0, disk: null, running });
      expect(html).toContain(`${label} is running, started 2026-10-09 07:10 (50 min ago).`);
      expect(html).not.toContain("waits for it");
    }
  });
  it("has a page heading, and the running line spans the grid", () => {
    const running = { job: "check", label: "Repository check", since: "2026-10-09 07:10 (50 min ago)" };
    const html = render({ configured: true, jobs: [backup], attention: [], flags: 0, disk: null, running });
    expect(html).toContain("<h1>Status</h1>");
    expect(html).toContain('class="muted status-running"');
  });
  it("says a backup that waits for a running job is waiting, not OK", () => {
    const late = { ...backup, last_ok: "2026-10-09 05:00 (3 h ago)", waiting: true };
    const html = render({ configured: true, jobs: [late], attention: [], flags: 0, disk: null });
    expect(html).toContain("<td>Waiting<div");
    expect(html).not.toContain("<td>OK");
  });
  it("says when the backup service last reported", () => {
    const html = render({ configured: true, jobs: [backup], attention: [], flags: 0, disk: null,
      reported: "2026-10-09 07:59 (1 min ago)" });
    expect(html).toContain("The backup service last reported 2026-10-09 07:59 (1 min ago).");
  });
  it("says the free space is unknown when there is no disk figure", () => {
    const html = render({ configured: false, jobs: [], attention: [], flags: 0, disk: null });
    expect(html).toContain('data-testid="disk-unknown"');
    expect(html).not.toContain('role="meter"');
  });
  it("shows the server's error instead of loading for ever", () => {
    shown.view = null;
    shown.error = { message: "No time zone found", call: null, type: "error" };
    const html = renderToStaticMarkup(createElement(StatusPage));
    expect(html).toContain('data-testid="status-error"');
    expect(html).toContain("No time zone found");
  });
  it("shows the sync panel after Needs attention, which holds its lines", () => {
    // 2b spec §15.2: the panel's Needs attention lines join Status's (status_view does it).
    const line = "Recording 20261023T140512-0700_0000beef is on the consent list, but in the archive. Take it out by hand (runbook).";
    const sync: StatusView["sync"] = {
      demo: false, no_sync_yet: false,
      summary: { kind: "problem", sentence: "Imports stopped: consent list", explain: null, see_status: true },
      failing: null, failing_checks: [], not_pinged: null, conditions: [], hold: null, breaker: null, // Needs attention holds it
      handled: ["keep-here"], not_yet: "Comes with the next part of stage 2b.", listed: false,
      numbers: {
        equation: null, archive: null, consent: null, last_good_listing: null, next_sync: null, sweep: null,
        sign_in: null, full: null, held: 0, gone: 0, failing: 0, parked: 0, worker: null, healthchecks: null,
      },
      details: {
        normaliser: "1", fields: [], census: {}, form_change: null, changes_24h: {}, named_share: null, runs: [],
        keep_out_words: [], malformed: 0,
      },
      attention: [line],
    };
    shown.view = { configured: true, jobs: [backup], attention: [line], flags: 0, disk: null, sync };
    shown.error = null;
    const plaud: Plaud = {
      send: () => "rid", results: {}, last: null, lastLine: null, available: true, reason: null, why: () => null,
      handles: () => false, gate: null,
    };
    const html = renderToStaticMarkup(createElement(PlaudContext.Provider, { value: plaud }, createElement(StatusPage)));
    const attention = html.indexOf('data-testid="status-attention"');
    const panel = html.indexOf('data-testid="sync-panel"');
    expect(attention).toBeGreaterThan(0);
    expect(panel).toBeGreaterThan(attention);
    expect(panel).toBeLessThan(html.indexOf('aria-labelledby="status-backups"'));
    expect(html).toContain("Imports stopped: consent list");
    // A line Needs attention holds is said once (the view model keeps it out of the conditions).
    expect(html.split(line).length - 1).toBe(1);
  });
  it("draws no sync panel where the view has none", () => {
    const html = render({ configured: true, jobs: [backup], attention: [], flags: 0, disk: null });
    expect(html).not.toContain("sync-panel");
  });
});
