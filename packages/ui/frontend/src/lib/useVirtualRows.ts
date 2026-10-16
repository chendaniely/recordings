import { defaultRangeExtractor, useVirtualizer, type Range } from "@tanstack/react-virtual";
import { useCallback, useState, type RefObject } from "react";

/** The same array while the rows are the same, though each reply's merge draws a new one. */
function useSameIds(ids: readonly string[]): readonly string[] {
  const [kept, setKept] = useState(ids);
  const same = kept === ids || (kept.length === ids.length && kept.every((id, i) => id === ids[i]));
  if (!same) setKept(ids); // React redraws at once, before committing (its documented pattern)
  return same ? kept : ids;
}

/** Rows drawn virtually, for the Library list and the Tags grid (spec §6, §7). The rows at the
 * indexes in `keep` (the focused row, the anchor, a row whose + picker is open, a dragged row)
 * always stay rendered, so focus, a picker and a drag survive their row scrolling out of view.
 * Rows are measured as drawn: give each `ref={virtualizer.measureElement}` (a stable function) and
 * `data-index`; a ResizeObserver re-measures a row that grows, as a focused long tag unwraps.
 * `getItemKey` changes only when the rows do: the virtualiser rebuilds its measurements whenever
 * it changes, and the spec expects thousands of rows. `useFlushSync` is off: the bundled
 * react-dom's flushSync can't flush the global React (spec §6). */
export function useVirtualRows(opts: {
  ids: readonly string[]; scrollRef: RefObject<HTMLDivElement | null>; rowSize: number;
  overscan: number; keep: readonly number[]; scrollPaddingStart?: number;
}) {
  const { scrollRef, rowSize, overscan, keep, scrollPaddingStart = 0 } = opts;
  const ids = useSameIds(opts.ids);
  const kept = keep.join(","); // a new array each render: its contents decide
  const rangeExtractor = useCallback((range: Range) => {
    const extra = kept ? kept.split(",").map(Number).filter((i) => i >= 0 && i < range.count) : [];
    return [...new Set([...defaultRangeExtractor(range), ...extra])].sort((a, b) => a - b);
  }, [kept]);
  const getItemKey = useCallback((i: number) => ids[i], [ids]);
  const estimateSize = useCallback(() => rowSize, [rowSize]);
  const getScrollElement = useCallback(() => scrollRef.current, [scrollRef]);
  return useVirtualizer({
    count: ids.length,
    getScrollElement,
    estimateSize,
    overscan,
    getItemKey,
    rangeExtractor,
    useFlushSync: false,
    scrollPaddingStart,
  });
}
