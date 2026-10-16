import { Lock } from "lucide-react";
import { useRef, useState, type RefObject } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { shownTags } from "../lib/library";
import { headerWhen } from "../lib/when";
import { useShinyOutputStatus, useShinyOutputValue } from "../sr";
import { useTaggingContext } from "../tagging/useTagging";
import type { LibraryRow, RecordingView } from "../types";
import { DetailsTab } from "./DetailsTab";
import { MyNotesTab } from "./MyNotesTab";
import { NotesTab } from "./NotesTab";
import { Player } from "./Player";
import { PlaudTab } from "./PlaudTab";
import { TagChips } from "./TagChips";
import { AddTagsButton } from "./TagPicker";
import { TranscriptTab } from "./TranscriptTab";

export function RecordingPane({ selectedId, row }: { selectedId: string | null; row: LibraryRow | null }) {
  const rec = useShinyOutputValue<RecordingView | null>("recording");
  const status = useShinyOutputStatus("recording");

  if (!selectedId) return <section className="pane"><p className="empty">Choose a recording.</p></section>;
  if (!rec || rec.id !== selectedId) return <section className="pane"><p className="empty">Loading…</p></section>;
  // Keyed by id, so every recording mounts fresh: its playback time starts at 0 and can never
  // be another recording's position (which would highlight and scroll to the wrong line).
  return <RecordingDetail key={rec.id} rec={rec} row={row} dimmed={status === "recalculating"} />;
}

/** The header's chips read the open recording's row in the merged library, never the `recording`
 * output, which isn't re-sent for a tag change because it carries the whole transcript (spec §5).
 * After ×, focus goes to the next control (the next ×, or the +); with none left, to the title,
 * never to the page. The + opens the picker for the open recording (spec §6). */
function HeaderChips({ rec, row, heading }: { rec: RecordingView; row: LibraryRow | null; heading: RefObject<HTMLHeadingElement | null> }) {
  const tagging = useTaggingContext();
  const tags = row ? shownTags(row, tagging.pending) : rec.tags;
  return (
    <TagChips
      tags={tags}
      title={row?.title ?? rec.title}
      canTag={tagging.canTag && row !== null}
      place="header"
      onRemove={(tag, next) => {
        (next ?? heading.current)?.focus();
        tagging.tag("remove", [rec.id], [tag], { kind: "header" });
      }}
    >
      {tagging.canTag && row !== null && <AddTagsButton id={rec.id} title={row.title} place="header" />}
    </TagChips>
  );
}

function RecordingDetail({ rec, row, dimmed }: { rec: RecordingView; row: LibraryRow | null; dimmed: boolean }) {
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const heading = useRef<HTMLHeadingElement | null>(null);
  const [time, setTime] = useState(0);

  const seek = (t: number) => {
    if (mediaRef.current) mediaRef.current.currentTime = t;
    setTime(t);
  };
  return (
    <section className="pane" style={{ opacity: dimmed ? 0.6 : 1 }}>
      <div className="head" data-testid="recording-header">
        <h1 ref={heading} tabIndex={-1}>{row?.title ?? rec.title} {(row?.private ?? rec.private) && <Lock size={15} className="lock" data-testid="lock" aria-label="Private: Spark only" />}</h1>
        <div className="meta">
          <time dateTime={rec.recorded_at} data-testid="recording-when">{headerWhen(rec.recorded_at)}</time>
          {[rec.duration, [...new Set(rec.sources.map((s) => s.kind))].join(", ")].filter(Boolean).map((x) => ` · ${x}`).join("")}
          {" · "}<span className="mono">{rec.id}</span>
        </div>
        <HeaderChips rec={rec} row={row} heading={heading} />
      </div>
      {rec.media_url && <Player url={rec.media_url} kind={rec.kind} mediaRef={mediaRef} onTime={setTime} />}
      <Tabs defaultValue="transcript" className="flex min-h-0 flex-1 flex-col">
        <TabsList className="tabs-list" aria-label="Recording">
          <TabsTrigger value="transcript" data-testid="tab-transcript">Transcript</TabsTrigger>
          <TabsTrigger value="notes" data-testid="tab-notes">Notes ({rec.notes.reduce((n, g) => n + g.outputs.length, 0)})</TabsTrigger>
          <TabsTrigger value="plaud" data-testid="tab-plaud">Plaud</TabsTrigger>
          <TabsTrigger value="my-notes" data-testid="tab-my-notes">My notes</TabsTrigger>
          <TabsTrigger value="details" data-testid="tab-details">Details</TabsTrigger>
        </TabsList>
        <TabsContent value="transcript" className="tab">
          <TranscriptTab transcripts={rec.transcripts} chosen={rec.chosen_transcript} time={time} onSeek={seek} />
        </TabsContent>
        <TabsContent value="notes" className="tab"><NotesTab groups={rec.notes} /></TabsContent>
        <TabsContent value="plaud" className="tab"><PlaudTab notes={rec.plaud_notes} /></TabsContent>
        <TabsContent value="my-notes" className="tab"><MyNotesTab html={rec.my_notes_html} /></TabsContent>
        <TabsContent value="details" className="tab"><DetailsTab rec={rec} /></TabsContent>
      </Tabs>
    </section>
  );
}
