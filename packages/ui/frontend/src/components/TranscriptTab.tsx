import { useEffect, useMemo, useRef, useState } from "react";

import { activeIndex, activeWord, formatClock } from "../lib/transcript";
import type { TranscriptView } from "../types";

type Props = { transcripts: TranscriptView[]; chosen: string | null; time: number; onSeek: (t: number) => void };

export function TranscriptTab({ transcripts, chosen, time, onSeek }: Props) {
  const [which, setWhich] = useState<string | null>(chosen);
  const [follow, setFollow] = useState(true);
  useEffect(() => setWhich(chosen), [chosen]);
  const transcript = transcripts.find((t) => t.rendition === which) ?? transcripts[0];
  const turns = transcript?.turns ?? [];
  const now = useMemo(() => activeIndex(turns, time), [turns, time]);
  const nowRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (follow) nowRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [now, follow]);

  if (!transcript) return <p className="muted">No transcript yet. Transcription starts once the recording has a tag.</p>;
  return (
    <>
      <div className="tools">
        {transcripts.length > 1 ? (
          <select data-testid="transcript-select" value={transcript.rendition} onChange={(e) => setWhich(e.target.value)} aria-label="Transcript">
            {transcripts.map((t) => <option key={t.rendition} value={t.rendition}>{t.label}</option>)}
          </select>
        ) : <span>{transcript.label}</span>}
        <label className="r"><input type="checkbox" data-testid="follow" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Follow audio</label>
      </div>
      {turns.map((turn, i) => {
        const showSpeaker = turn.speaker && (i === 0 || turns[i - 1].speaker !== turn.speaker);
        const w = i === now ? activeWord(turn, time) : -1;
        return (
          <button key={`${turn.start}-${i}`} ref={i === now ? nowRef : undefined} type="button" data-testid="turn" data-start={turn.start}
            className={`turn${i === now ? " now" : ""}`} onClick={() => onSeek(turn.start)}>
            <span className="ts">{formatClock(turn.start)}</span>
            <span>
              {showSpeaker && <div className="sp">{turn.speaker}</div>}
              {w >= 0 ? turn.words.map((word, j) => <span key={j}>{j ? " " : ""}{j === w ? <mark>{word.word}</mark> : word.word}</span>) : turn.text}
            </span>
          </button>
        );
      })}
    </>
  );
}
