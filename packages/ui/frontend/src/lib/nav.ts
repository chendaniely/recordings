import type { Page, Route } from "../types";

const ID = /^[0-9]{8}T[0-9]{6}[+-][0-9]{4}_[0-9a-f]{8}$/;

/** What a URL's hash shows: `#/status`, `#/tags`, `#/library/<id>`, or the Library. The hash only
 * ever chooses what to show; nothing is written from it (spec §15). */
export function routeFromHash(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("?")[0].split("/");
  if (parts[0] === "status") return { page: "status", open: null };
  if (parts[0] === "tags") return { page: "tags", open: null };
  if (parts[0] === "library" && parts[1]) {
    let id = "";
    try {
      id = decodeURIComponent(parts[1]);
    } catch {
      id = "";
    }
    return { page: "library", open: ID.test(id) ? id : null };
  }
  return { page: "library", open: null };
}

export function pageFromHash(hash: string): Page {
  return routeFromHash(hash).page;
}

export function hashFor(page: Page): string {
  return page === "status" ? "#/status" : page === "tags" ? "#/tags" : "#/";
}

/** On a phone the Tags tab is the Library filtered to Untagged (spec §6): the Tags route shows the
 * Library, and App moves the address and the filter to match. Other routes, and any route on a wider
 * screen, are as they are. */
export function phoneRoute(route: Route, phone: boolean): Route {
  return phone && route.page === "tags" ? { page: "library", open: null } : route;
}

/** The Tags grid's Open: the recording in the Library, in a new tab (spec §7). */
export function openHref(id: string): string {
  return `#/library/${encodeURIComponent(id)}`;
}
