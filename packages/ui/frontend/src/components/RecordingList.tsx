import { Lock } from "lucide-react";

import type { LibraryRow } from "../types";

type Props = { rows: LibraryRow[]; selectedId: string | null; onSelect: (id: string) => void };

export function RecordingList({ rows, selectedId, onSelect }: Props) {
  return (
    <section className="list" aria-label="Recordings">
      <div className="list-head">{rows.length} recording{rows.length === 1 ? "" : "s"} · newest first</div>
      {rows.map((r) => (
        <button key={r.id} type="button" data-testid="recording-row" className={`row${r.id === selectedId ? " on" : ""}`} onClick={() => onSelect(r.id)}>
          <div className="t"><span>{r.title}</span>{r.private && <Lock size={13} className="lock" aria-label="Private" />}</div>
          <div className="m">{[r.when, r.duration, r.sources.join(", "), r.kind === "video" ? "video" : null].filter(Boolean).join(" · ")}</div>
          {r.tags.map((t) => (
            <span key={t.tag} className={`chip${t.by === "auto" ? " auto" : ""}${t.tag.startsWith("notes/") ? " nt" : ""}`}>{t.tag}{t.by === "auto" ? " · auto" : ""}</span>
          ))}
        </button>
      ))}
    </section>
  );
}
