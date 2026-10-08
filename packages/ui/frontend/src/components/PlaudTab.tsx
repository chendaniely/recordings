import type { RecordingView } from "../types";

export function PlaudTab({ notes }: { notes: RecordingView["plaud_notes"] }) {
  if (!notes.length) return <p className="muted">Nothing from Plaud for this recording.</p>;
  return (
    <>
      {notes.map((n) => <div key={n.rendition} className="md" dangerouslySetInnerHTML={{ __html: n.html }} />)}
      <p className="foot">Plaud's transcript is in the Transcript tab's selector.</p>
    </>
  );
}
