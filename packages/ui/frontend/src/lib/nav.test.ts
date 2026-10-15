import { describe, expect, it } from "vitest";

import { hashFor, pageFromHash } from "./nav";

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
