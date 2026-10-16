import { createElement, useRef } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";

import { useVirtualRows } from "./useVirtualRows";

type Virtualizer = ReturnType<typeof useVirtualRows>;
let virtualizer: Virtualizer;
function Probe({ ids }: { ids: string[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  virtualizer = useVirtualRows({ ids, scrollRef, rowSize: 10, overscan: 1, keep: [] });
  return null;
}

describe("useVirtualRows (fix round 1, Task 12)", () => {
  it("keeps getItemKey while the rows are the same, so measurements aren't rebuilt each render", () => {
    const root = createRoot(document.createElement("div"));
    const draw = (ids: string[]) => flushSync(() => root.render(createElement(Probe, { ids })));
    draw(["a", "b"]);
    const key = virtualizer.options.getItemKey;
    draw(["a", "b"]); // a new array with the same rows, as each reply's merge gives
    expect(virtualizer.options.getItemKey).toBe(key);
    draw(["a", "c"]);
    expect(virtualizer.options.getItemKey).not.toBe(key);
    expect(virtualizer.options.getItemKey(1)).toBe("c");
    root.unmount();
  });
});

describe("useVirtualRows under a pinned header (Task 14)", () => {
  it("passes the header's height on as the list's offset and scroll padding", () => {
    let grid: Virtualizer | undefined;
    function Grid() {
      const scrollRef = useRef<HTMLDivElement>(null);
      grid = useVirtualRows({ ids: ["a"], scrollRef, rowSize: 10, overscan: 1, keep: [], scrollMargin: 56, scrollPaddingStart: 56 });
      return null;
    }
    const root = createRoot(document.createElement("div"));
    flushSync(() => root.render(createElement(Grid)));
    expect(grid!.options.scrollMargin).toBe(56);
    expect(grid!.options.scrollPaddingStart).toBe(56);
    root.unmount();
  });
});
