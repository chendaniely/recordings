import { describe, expect, it } from "vitest";

import { hashFor, openHref, pageFromHash, routeFromHash } from "./nav";

describe("pageFromHash", () => {
  it("reads the page from the hash, and anything else is the Library", () => {
    expect(pageFromHash("#/status")).toBe("status");
    expect(pageFromHash("#status")).toBe("status");
    expect(pageFromHash("#/status?x=1")).toBe("status");
    expect(pageFromHash("")).toBe("library");
    expect(pageFromHash("#/")).toBe("library");
    expect(pageFromHash("#/nonsense")).toBe("library");
  });
  it("round-trips", () => {
    expect(pageFromHash(hashFor("status"))).toBe("status");
    expect(pageFromHash(hashFor("library"))).toBe("library");
  });
});

describe("routeFromHash", () => {
  const id = "19620912T100000-0500_229ce3ea";
  it("reads the Tags page and a read-only recording link", () => {
    expect(routeFromHash("#/tags")).toEqual({ page: "tags", open: null });
    expect(routeFromHash(openHref(id))).toEqual({ page: "library", open: id });
    expect(routeFromHash(`#/library/${id}`)).toEqual({ page: "library", open: id });
  });
  it("ignores an ID that isn't one, and a broken escape", () => {
    expect(routeFromHash("#/library/..%2Fetc")).toEqual({ page: "library", open: null });
    expect(routeFromHash("#/library/%E0%A4%A")).toEqual({ page: "library", open: null });
  });
  it("round-trips the Tags page", () => {
    expect(routeFromHash(hashFor("tags")).page).toBe("tags");
  });
});
