import type { Page } from "../types";

/** The page a URL's hash names: `#/status`, or the Library for anything else. */
export function pageFromHash(hash: string): Page {
  const first = hash.replace(/^#\/?/, "").split(/[/?]/)[0];
  return first === "status" ? "status" : "library";
}

export function hashFor(page: Page): string {
  return page === "status" ? "#/status" : "#/";
}
