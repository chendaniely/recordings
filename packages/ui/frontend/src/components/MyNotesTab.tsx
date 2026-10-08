export function MyNotesTab({ html }: { html: string | null }) {
  if (html === null) return <p className="muted">No notes of your own yet. Editing arrives with tagging (stage 3).</p>;
  return <div className="md" dangerouslySetInnerHTML={{ __html: html }} />;
}
