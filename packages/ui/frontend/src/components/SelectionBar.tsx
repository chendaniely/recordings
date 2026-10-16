/** "12 selected · Tag… (T) · Clear (Esc)". The selection stays after an action, so a second tag
 * can follow, and the bar adds "just tagged `work`" (spec §6). On phones it sits above the
 * bottom tab bar (app.css). */
export function SelectionBar({ count, lastTagged, onTag, onClear }: {
  count: number; lastTagged: string | null; onTag: () => void; onClear: () => void;
}) {
  if (count < 2) return null;
  return (
    <div className="selection-bar" role="region" aria-label="Selection" data-testid="selection-bar">
      <span>{count.toLocaleString("en")} selected</span>
      <button type="button" onClick={onTag}>Tag{"…"} <kbd>T</kbd></button>
      <button type="button" onClick={onClear}>Clear <kbd>Esc</kbd></button>
      {lastTagged && <span className="muted">just tagged <code>{lastTagged}</code></span>}
    </div>
  );
}
