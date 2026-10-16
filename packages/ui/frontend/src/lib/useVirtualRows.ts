import { defaultRangeExtractor, useVirtualizer, type Range } from "@tanstack/react-virtual";
import { useCallback, type RefObject } from "react";

/** Rows drawn virtually, for the Library list and the Tags grid (spec §6, §7). The rows at the
 * indexes in `keep` (the focused row, the anchor, a row whose + picker is open, a dragged row)
 * always stay rendered, so focus, a picker and a drag survive their row scrolling out of view.
 * Rows are measured as drawn (`measureElement`, with a ResizeObserver), so a row that grows, as a
 * focused long tag unwraps, pushes the rows below it down. `useFlushSync` is off: the bundled
 * react-dom's flushSync can't flush the global React (spec §6). */
export function useVirtualRows(opts: {
  ids: readonly string[]; scrollRef: RefObject<HTMLDivElement | null>; rowSize: number;
  overscan: number; keep: readonly number[]; scrollPaddingStart?: number;
}) {
  const { ids, scrollRef, rowSize, overscan, keep, scrollPaddingStart = 0 } = opts;
  const kept = keep.join(","); // a new array each render: its contents decide
  const rangeExtractor = useCallback((range: Range) => {
    const extra = kept ? kept.split(",").map(Number).filter((i) => i >= 0 && i < range.count) : [];
    return [...new Set([...defaultRangeExtractor(range), ...extra])].sort((a, b) => a - b);
  }, [kept]);
  return useVirtualizer({
    count: ids.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowSize,
    overscan,
    getItemKey: (i) => ids[i],
    rangeExtractor,
    useFlushSync: false,
    scrollPaddingStart,
  });
}
