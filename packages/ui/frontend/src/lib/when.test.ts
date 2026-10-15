import { describe, expect, it } from "vitest";

import { headerWhen } from "./when";

describe("headerWhen", () => {
  it("shows the recording's own local time and UTC offset, never the browser's", () => {
    expect(headerWhen("1962-09-12T10:00:00-05:00")).toBe("1962-09-12 10:00 UTC-05:00");
    expect(headerWhen("2026-10-06T14:05:59.123456+09:00")).toBe("2026-10-06 14:05 UTC+09:00");
    expect(headerWhen("2026-10-06T23:30:00+05:30")).toBe("2026-10-06 23:30 UTC+05:30");
    expect(headerWhen("2026-10-06T08:00:00+00:00")).toBe("2026-10-06 08:00 UTC+00:00");
    expect(headerWhen("2026-10-06T08:00:00Z")).toBe("2026-10-06 08:00 UTC+00:00");
  });
  it("shows a string it can't read as it is", () => {
    expect(headerWhen("2026-10-06T08:00:00")).toBe("2026-10-06T08:00:00");
    expect(headerWhen("")).toBe("");
  });
});
