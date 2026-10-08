import { Lock } from "lucide-react";
import { useRef, useState } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { useShinyOutputStatus, useShinyOutputValue } from "../sr";
import type { RecordingView } from "../types";
import { DetailsTab } from "./DetailsTab";
import { MyNotesTab } from "./MyNotesTab";
import { NotesTab } from "./NotesTab";
import { Player } from "./Player";
import { PlaudTab } from "./PlaudTab";
import { TranscriptTab } from "./TranscriptTab";

export function RecordingPane({ selectedId }: { selectedId: string | null }) {
  const rec = useShinyOutputValue<RecordingView | null>("recording");
  const status = useShinyOutputStatus("recording");

  if (!selectedId) return <section className="pane"><p className="empty">Choose a recording.</p></section>;
  if (!rec || rec.id !== selectedId) return <section className="pane"><p className="empty">Loading…</p></section>;
  // Keyed by id, so every recording mounts fresh: its playback time starts at 0 and can never
  // be another recording's position (which would highlight and scroll to the wrong line).
  return <RecordingDetail key={rec.id} rec={rec} dimmed={status === "recalculating"} />;
}

function RecordingDetail({ rec, dimmed }: { rec: RecordingView; dimmed: boolean }) {
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const [time, setTime] = useState(0);

  const seek = (t: number) => {
    if (mediaRef.current) mediaRef.current.currentTime = t;
    setTime(t);
  };
  return (
    <section className="pane" style={{ opacity: dimmed ? 0.6 : 1 }}>
      <div className="head">
        <h1>{rec.title} {rec.private && <Lock size={15} className="lock" data-testid="lock" aria-label="Private: Spark only" />}</h1>
        <div className="meta">{[rec.when, rec.duration, rec.sources.map((s) => s.kind).join(", ")].filter(Boolean).join(" · ")} · <span className="mono">{rec.id}</span></div>
        <div>{rec.tags.map((t) => <span key={t.tag} className={`chip${t.by === "auto" ? " auto" : ""}`}>{t.tag}</span>)}</div>
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
