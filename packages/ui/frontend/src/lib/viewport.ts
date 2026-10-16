import { useEffect, useState } from "react";

/** The room a bottom sheet has on a phone: `bottom`, how far above the layout viewport's bottom
 * edge the visible part ends (an on-screen keyboard's height, or 0), and `height`, how tall the
 * visible part is. An on-screen keyboard shrinks the visual viewport, not the layout viewport that
 * a fixed element is placed in (checked: MDN, VisualViewport), and the dynamic viewport units
 * follow only the browser's own bars (MDN, <length>: dvh), so neither CSS alone keeps a sheet's
 * box and list above the keyboard. */
export interface SheetRoom { bottom: number; height: number }

export function sheetRoom(layoutHeight: number, visual: { height: number; offsetTop: number }): SheetRoom {
  return {
    bottom: Math.max(0, Math.round(layoutHeight - visual.offsetTop - visual.height)),
    height: Math.max(0, Math.round(visual.height)),
  };
}

/** The sheet's room while `on`, following the visual viewport as a keyboard comes and goes (its
 * resize and scroll events). Null while off, or where there is no visualViewport (jsdom): the
 * sheet's CSS then falls back to the bottom edge and the dynamic viewport's height. */
export function useSheetRoom(on: boolean): SheetRoom | null {
  const [room, setRoom] = useState<SheetRoom | null>(null);
  useEffect(() => {
    const visual = on && typeof window !== "undefined" ? window.visualViewport : null;
    if (!visual) {
      setRoom(null);
      return;
    }
    const sync = () => {
      const next = sheetRoom(document.documentElement.clientHeight, visual);
      setRoom((r) => (r && r.bottom === next.bottom && r.height === next.height ? r : next));
    };
    sync();
    visual.addEventListener("resize", sync);
    visual.addEventListener("scroll", sync);
    return () => {
      visual.removeEventListener("resize", sync);
      visual.removeEventListener("scroll", sync);
    };
  }, [on]);
  return room;
}
