import { useShinyOutputError, useShinyOutputValue } from "../sr";
import type { StatusView } from "../types";

/** Status's backup and disk panels (spec §12.5), read-only. Alerts are the push view (§14). */
export function StatusPage() {
  const status = useShinyOutputValue<StatusView>("status");
  const error = useShinyOutputError("status");  // without it, a server error is "Loading…" for ever
  if (error) return <main className="status"><div className="problems" role="alert" data-testid="status-error">Status can't be shown: {error.message}</div></main>;
  if (!status) return <main className="status"><p className="empty">Loading…</p></main>;
  const disk = status.disk;
  return (
    <main className="status" data-testid="status-page">
      <h1>Status</h1>
      {status.running && (
        <p className="muted status-running" data-testid="status-running">
          {status.running.label} is running, started {status.running.since}.
          {!["backup", "disk"].includes(status.running.job) && " The hourly backup waits for it."}
        </p>
      )}
      {status.attention.length > 0 && (
        <div className="problems" role="status" data-testid="status-attention">
          Needs attention:
          <ul>{status.attention.map((a) => <li key={a}>{a}</li>)}</ul>
        </div>
      )}
      <section className="card" aria-labelledby="status-backups">
        <h2 id="status-backups">Backups</h2>
        {status.configured ? (
          <table className="details">
            <thead><tr><th>Job</th><th>Last good run</th><th>State</th></tr></thead>
            <tbody>
              {status.jobs.map((j) => (
                <tr key={j.job} className={j.attention ? "attn" : undefined} data-testid={`status-job-${j.job}`}>
                  <td>{j.label}</td>
                  <td>{j.last_ok ?? "never"}</td>
                  <td>
                    {j.failing ? `Failing: ${j.detail}` : j.attention ? "Overdue" : j.waiting ? "Waiting" : "OK"}
                    {!j.failing && j.last_ok_detail && (
                      <div className="muted" data-testid={`status-job-${j.job}-detail`}>{j.last_ok_detail}</div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted" data-testid="backups-unconfigured">
            Backups aren't set up here. On the homelab server, the backup service runs them every hour.
          </p>
        )}
        {status.reported !== undefined && (
          <p className="muted" data-testid="status-reported">
            {status.reported ? `The backup service last reported ${status.reported}.` : "The backup service has not reported yet."}
          </p>
        )}
      </section>
      <section className="card" aria-labelledby="status-disk">
        <h2 id="status-disk">Disk</h2>
        {disk ? (
          <>
            {disk.free_percent !== null && (
              <div className={`meter ${disk.level}`} role="meter" aria-label="Disk in use" aria-valuemin={0}
                aria-valuemax={100} aria-valuenow={Math.round(100 - disk.free_percent)}>
                <span style={{ width: `${100 - disk.free_percent}%` }} />
              </div>
            )}
            <p>{disk.free_label ?? disk.message}</p>
            {disk.level !== "ok" && <p className="warn" data-testid="disk-warning">{disk.message}</p>}
          </>
        ) : <p className="muted" data-testid="disk-unknown">Free space unknown.</p>}
      </section>
    </main>
  );
}
