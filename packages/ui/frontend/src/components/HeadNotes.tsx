import { useEffect, useState } from "react";

import type { Announcement } from "../lib/useSelection";
import { useTaggingContext } from "../tagging/useTagging";

/** A live region. It empties, then says the words a frame later, so the same words said again (a
 * second "2 hidden rows deselected") are read out again: a status region reads only a change.
 * `at` is new for each announcement. */
export function Announcer({ text, at }: { text: string; at: number }) {
  const [said, setSaid] = useState({ at, shown: text });
  // Emptied in the very commit that brings new words, never batched away with them.
  if (said.at !== at) setSaid({ at, shown: "" });
  useEffect(() => {
    if (said.shown === text) return;
    const frame = requestAnimationFrame(() => setSaid({ at, shown: text }));
    return () => cancelAnimationFrame(frame);
  }, [said, text, at]);
  return <span className="sr-only" role="status">{said.shown}</span>;
}

/** Refresh, in the list's and the grid's header: "5 tagged · Refresh" (spec §6). */
export function RefreshButton({ testId }: { testId: string }) {
  const tagging = useTaggingContext();
  return (
    <button type="button" className="refresh" data-testid={testId} onClick={tagging.refresh}>
      {tagging.touched > 0 ? `${tagging.touched.toLocaleString("en")} tagged · Refresh` : "Refresh"}
    </button>
  );
}

/** The notes under the list's and the grid's header (spec §6), each said once: on the demo, that
 * changes aren't saved; while the archive is being opened, that it is (a moment, not a fault);
 * when tagging is off, why (a long path wraps); the keys; and the live region. */
export function HeadNotes({ hint, announce }: { hint: string; announce: Announcement }) {
  const tagging = useTaggingContext();
  return (
    <>
      {!tagging.saves && <span className="demo-note">Demo: changes aren't saved</span>}
      {tagging.opening ? (
        <span className="cannot-tag opening" data-testid="cannot-tag">Opening the archive{"…"}</span>
      ) : !tagging.canTag && (
        <span className="cannot-tag" data-testid="cannot-tag">Tagging is off: {tagging.reason}</span>
      )}
      <span className="key-hint" data-testid="key-hint">{hint}</span>
      <Announcer text={announce.text} at={announce.key} />
    </>
  );
}
