import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { StatusJob, StatusView } from "../types";

const shown = vi.hoisted(() => ({ view: null as unknown, error: null as unknown }));
vi.mock("../sr", () => ({ useShinyOutputValue: () => shown.view, useShinyOutputError: () => shown.error }));

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
});
