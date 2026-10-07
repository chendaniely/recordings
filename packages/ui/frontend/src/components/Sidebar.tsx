import { Lock } from "lucide-react";
import type { ReactNode } from "react";

import type { Filter, LibraryView } from "../types";

type Props = { library: LibraryView; filter: Filter; onFilter: (f: Filter) => void };

const keyOf = (f: Filter) => (f.kind === "tag" ? `tag:${f.tag}` : f.kind);

export function Sidebar({ library, filter, onFilter }: Props) {
  const item = (f: Filter, label: ReactNode, n: number, testid: string) => (
    <button key={keyOf(f)} type="button" className={`si${keyOf(f) === keyOf(filter) ? " on" : ""}`} data-testid={testid} onClick={() => onFilter(f)}>
      <span>{label}</span><span className="n">{n}</span>
    </button>
  );
  const open = library.tags.filter((t) => !t.private);
  const hidden = library.tags.filter((t) => t.private);
  return (
    <nav className="sidebar" aria-label="Filters">
      {item({ kind: "all" }, "All recordings", library.counts.all, "filter-all")}
      {item({ kind: "untagged" }, "Untagged", library.counts.untagged, "filter-untagged")}
      {library.problems.length > 0 && (
        <div className="problems" role="status">
          {library.problems.length} recording file{library.problems.length > 1 ? "s" : ""} couldn't be read. Run <code>recordings validate</code>.
        </div>
      )}
      <div className="grp">Tags</div>
      {open.map((t) => item({ kind: "tag", tag: t.tag }, t.tag, t.count, "filter-tag"))}
      {hidden.length > 0 && <div className="grp">Private</div>}
      {hidden.map((t) => item({ kind: "tag", tag: t.tag }, <><Lock size={12} /> {t.tag}</>, t.count, "filter-tag"))}
      {library.note_types.length > 0 && <div className="grp">Note types</div>}
      {library.note_types.map((n) => item({ kind: "tag", tag: `notes/${n.note_type}` }, n.note_type, n.count, "filter-tag"))}
    </nav>
  );
}
