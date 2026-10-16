/** "12 selected · Tag… (T) · Clear (Esc)". The selection stays after an action, so a second tag
 * can follow, and the bar adds "just tagged `work`" (spec §6): the tags last added to this very
 * selection, a tag's name as code, several as a count ("just tagged 3 tags"). Esc inside the bar
 * clears, as it says. On phones it sits above the bottom tab bar (app.css). */
export function SelectionBar({ count, lastTagged, onTag, onClear }: {
  count: number; lastTagged: string[] | null; onTag: () => void; onClear: () => void;
}) {
  if (count < 2) return null;
  return (
    <div
      className="selection-bar"
      role="region"
      aria-label="Selection"
      data-testid="selection-bar"
      onKeyDown={(e) => {
        if (e.key !== "Escape" || e.defaultPrevented) return;
        e.preventDefault();
        onClear();
      }}
    >
      <span>{count.toLocaleString("en")} selected</span>
      <button type="button" onClick={onTag}>Tag{"…"} <kbd>T</kbd></button>
      <button type="button" onClick={onClear}>Clear <kbd>Esc</kbd></button>
      {lastTagged && lastTagged.length > 0 && (
        <span className="muted">
          just tagged {lastTagged.length === 1 ? <code>{lastTagged[0]}</code> : `${lastTagged.length} tags`}
        </span>
      )}
    </div>
  );
}
