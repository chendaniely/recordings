import { useEffect, useMemo, useState } from "react";

import { flattenNotes, noteSelection } from "../lib/notes";
import type { NotesGroup, NotesOutput } from "../types";

const label = (o: NotesOutput) => `${o.model ?? o.engine} · ${o.engine}`;
const when = (iso: string) => new Date(iso).toLocaleString();

function Output({ group, output }: { group: string; output: NotesOutput }) {
  return (
    <section>
      <div className="md" dangerouslySetInnerHTML={{ __html: output.html }} />
      <p className="foot">{group} · {output.model ?? "?"} · {output.engine} · {when(output.created_at)}</p>
    </section>
  );
}

/** Layout B (spec §12.1): one list of every notes output, grouped by note type. */
export function NotesTab({ groups }: { groups: NotesGroup[] }) {
  const all = useMemo(() => flattenNotes(groups), [groups]);
  const [picked, setPicked] = useState<string | null>(all[0]?.o.rendition ?? null);
  const [other, setOther] = useState<string>("");
  useEffect(() => { setPicked(all[0]?.o.rendition ?? null); setOther(""); }, [all]);
  const { main, second } = noteSelection(all, picked, other);

  if (!all.length || !main) return <p className="muted">No notes yet. Notes are written once the recording has a tag.</p>;
  return (
    <div className="notes-split">
      <div className="notes-list">
        {groups.map((g) => (
          <div key={g.note_type}>
            <div className="nl-h">{g.note_type}</div>
            {g.outputs.map((o) => (
              <button key={o.rendition} type="button" data-testid="notes-output" className={`nl-i${o.rendition === main.o.rendition ? " on" : ""}`} onClick={() => setPicked(o.rendition)}>
                <span>{o.model ?? o.engine}</span>
              </button>
            ))}
          </div>
        ))}
      </div>
      <div>
        <div className="tools">
          <span>{main.group} · {label(main.o)}</span>
          <label className="r">Compare with
            <select data-testid="compare-select" value={second ? other : ""} onChange={(e) => setOther(e.target.value)}>
              <option value="">nothing</option>
              {all.filter((x) => x.o.rendition !== main.o.rendition).map((x) => <option key={x.o.rendition} value={x.o.rendition}>{x.group} · {label(x.o)}</option>)}
            </select>
          </label>
        </div>
        {second ? (
          <div className="compare" data-testid="compare-view">
            <Output group={main.group} output={main.o} />
            <Output group={second.group} output={second.o} />
          </div>
        ) : <Output group={main.group} output={main.o} />}
      </div>
    </div>
  );
}
