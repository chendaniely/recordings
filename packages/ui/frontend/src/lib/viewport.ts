import { useLayoutEffect, useState } from "react";

/** The room the picker has on a touch screen: `top`, how far below the layout viewport's top edge
 * the visible part starts; `bottom`, how far above its bottom edge the visible part ends (an
 * on-screen keyboard's height, or 0); and `height`, how tall the visible part is. An on-screen keyboard shrinks the visual viewport, not the layout viewport that
 * a fixed element is placed in (checked: MDN, VisualViewport), and the dynamic viewport units
 * follow only the browser's own bars (MDN, <length>: dvh), so neither CSS alone keeps a sheet's
 * box and list above the keyboard. */
export interface SheetRoom { top: number; bottom: number; height: number }

export function sheetRoom(layoutHeight: number, visual: { height: number; offsetTop: number }): SheetRoom {
  return {
    top: Math.max(0, Math.round(visual.offsetTop)),
    bottom: Math.max(0, Math.round(layoutHeight - visual.offsetTop - visual.height)),
    height: Math.max(0, Math.round(visual.height)),
  };
}

/** The room while `on`, following the visual viewport as a keyboard comes and goes (its resize and
 * scroll events). Read before the first paint, so an opening sheet never shows where the last one
 * stood. Kept once off, so a closing sheet slides out from where it stood (Task 15 fix round 1,
 * item 13). Null where there is no visualViewport (jsdom): the CSS then falls back to the edges
 * and the dynamic viewport's height. */
export function useSheetRoom(on: boolean): SheetRoom | null {
  const [room, setRoom] = useState<SheetRoom | null>(null);
  useLayoutEffect(() => {
    const visual = on && typeof window !== "undefined" ? window.visualViewport : null;
    if (!visual) return;
    const sync = () => {
      const next = sheetRoom(document.documentElement.clientHeight, visual);
      setRoom((r) => (r && r.top === next.top && r.bottom === next.bottom && r.height === next.height ? r : next));
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
