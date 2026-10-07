import type { RefObject, SyntheticEvent } from "react";

type Props = { url: string; kind: "audio" | "video"; mediaRef: RefObject<HTMLMediaElement | null>; onTime: (t: number) => void };

export function Player({ url, kind, mediaRef, onTime }: Props) {
  const common = {
    src: url,
    controls: true,
    preload: "metadata" as const,
    "data-testid": "media",
    onTimeUpdate: (e: SyntheticEvent<HTMLMediaElement>) => onTime(e.currentTarget.currentTime),
    onSeeked: (e: SyntheticEvent<HTMLMediaElement>) => onTime(e.currentTarget.currentTime),
  };
  return (
    <div className="player">
      {kind === "video"
        ? <video ref={mediaRef as RefObject<HTMLVideoElement>} {...common} playsInline />
        : <audio ref={mediaRef as RefObject<HTMLAudioElement>} {...common} />}
    </div>
  );
}
