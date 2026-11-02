import { useEffect, useState } from "react";

import { PHONE, useMediaQuery } from "../lib/useMediaQuery";
import type { Announcement } from "../lib/useSelection";
import { useShinyOutputValue } from "../sr";
import { useTaggingContext } from "../tagging/useTagging";
import type { Arrivals } from "../types";

const n = (x: number) => x.toLocaleString("en");

/** The arrivals cue (2b spec §15.3, P10): "2 new · 1 private · 2 updated from Plaud", or on a
 * phone "5 from Plaud"; null when nothing came in since the page loaded or its last Refresh
 * (decision 22). Counts only: no title, in this or any alert. */
export function arrivalsCue(a: Arrivals | undefined, short: boolean): string | null {
  if (!a) return null;
  const total = a.new + a.private + a.updated;
  if (total === 0) return null;
  if (short) return `${n(total)} from Plaud`;
  const parts = [a.new && `${n(a.new)} new`, a.private && `${n(a.private)} private`, a.updated && `${n(a.updated)} updated`];
  return `${parts.filter(Boolean).join(" · ")} from Plaud`;
}

/** "1 kept out too late · 1 consent-listed in the archive", by count only (§15.3); null with no
 * open privacy flag. Its "See Status" leads to the list by archive ID that §15.3 calls the
 * Library's Needs attention: the Library has none of its own, so Status's is the one (a reading,
 * 2b phase 1 plan, Task 23). */
export function flagsLine(a: Arrivals | undefined): string | null {
  if (!a) return null;
  const parts = [
    a.kept_out_late && `${n(a.kept_out_late)} kept out too late`,
    a.consent_late && `${n(a.consent_late)} consent-listed in the archive`,
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(" · ") : null;
}

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

/** Refresh, in the list's and the grid's header: "5 changed · 2 new · 1 private · 2 updated from
 * Plaud · Refresh", every done row and every arrival counted (spec §6; 2b spec §15.3). Rows don't
 * move until it is pressed. */
export function RefreshButton({ testId }: { testId: string }) {
  const tagging = useTaggingContext();
  const cue = arrivalsCue(useShinyOutputValue<Arrivals>("plaud_arrivals"), useMediaQuery(PHONE));
  const parts = [tagging.touched > 0 && `${n(tagging.touched)} changed`, cue, "Refresh"].filter(Boolean);
  return (
    <button type="button" className="refresh" data-testid={testId} onClick={tagging.refresh}>
      {parts.join(" · ")}
    </button>
  );
}

/** The notes under the list's and the grid's header (spec §6), each said once: on the demo, that
 * changes aren't saved; open privacy flags, by count only, with the way to Status (2b spec §15.3);
 * while the archive is being opened, that it is (a moment, not a fault); when tagging is off, why
 * (a long path wraps); the keys; and the live region. */
export function HeadNotes({ hint, announce }: { hint: string; announce: Announcement }) {
  const tagging = useTaggingContext();
  const flags = flagsLine(useShinyOutputValue<Arrivals>("plaud_arrivals"));
  return (
    <>
      {!tagging.saves && <span className="demo-note">Demo: changes aren't saved</span>}
      {flags && (
        <span className="plaud-flags" data-testid="plaud-flags">{flags} {"·"} <a href="#/status">See Status</a></span>
      )}
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
