import { useEffect, useState } from "react";

export const PHONE = "(max-width: 760px)";

/** False where there is no matchMedia (jsdom, so every Vitest that draws a component using this
 * keeps working). */
const media = (query: string): MediaQueryList | null =>
  typeof window.matchMedia === "function" ? window.matchMedia(query) : null;

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => media(query)?.matches ?? false);
  useEffect(() => {
    const list = media(query);
    if (!list) return;
    const sync = () => setMatches(list.matches);
    sync();
    list.addEventListener("change", sync);
    return () => list.removeEventListener("change", sync);
  }, [query]);
  return matches;
}
