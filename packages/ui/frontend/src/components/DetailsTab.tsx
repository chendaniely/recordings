import type { RecordingView } from "../types";

export function DetailsTab({ rec }: { rec: RecordingView }) {
  return (
    <>
      {rec.problems.length > 0 && (
        <div className="problems" role="status" data-testid="recording-problems">
          {rec.problems.length} file{rec.problems.length > 1 ? "s" : ""} couldn't be read:
          <ul>{rec.problems.map((p) => <li key={p.path} className="mono">{p.path}: {p.message}</li>)}</ul>
        </div>
      )}
      <h3>Sources</h3>
      <table className="details"><thead><tr><th>Kind</th><th>Reference</th><th>Added</th></tr></thead>
        <tbody>{rec.sources.map((s) => <tr key={s.kind + s.ref}><td>{s.kind}</td><td className="mono">{s.ref}</td><td>{s.added_at}</td></tr>)}</tbody></table>
      <h3>Outputs</h3>
      <table className="details"><thead><tr><th>File</th><th>Kind</th><th>Engine</th><th>Model</th><th>Created</th></tr></thead>
        <tbody>{rec.renditions.map((r) => <tr key={r.rendition}><td className="mono">{r.rendition}</td><td>{r.note_type ? `notes · ${r.note_type}` : r.kind}</td><td>{r.engine}</td><td>{r.model ?? ""}</td><td>{r.created_at}</td></tr>)}</tbody></table>
      <p className="foot">Time zone: {rec.timezone}</p>
    </>
  );
}
