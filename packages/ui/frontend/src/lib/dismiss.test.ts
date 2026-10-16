import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { pausableTimer } from "./dismiss";

describe("pausableTimer (spec §6: about 8 s, paused while hovered or focused)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());
  it("expires after its time, minus the time it was paused", () => {
    const expired = vi.fn();
    const timer = pausableTimer(8000, expired);
    vi.advanceTimersByTime(5000);
    timer.pause();
    vi.advanceTimersByTime(60_000);
    expect(expired).not.toHaveBeenCalled();
    timer.resume();
    vi.advanceTimersByTime(2999);
    expect(expired).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(expired).toHaveBeenCalledTimes(1);
  });
  it("counts nested pauses (hover and focus together) and can be cancelled", () => {
    const expired = vi.fn();
    const timer = pausableTimer(1000, expired);
    timer.pause();
    timer.pause();
    timer.resume();
    vi.advanceTimersByTime(5000);
    expect(expired).not.toHaveBeenCalled();
    timer.resume();
    timer.cancel();
    vi.advanceTimersByTime(5000);
    expect(expired).not.toHaveBeenCalled();
  });
});

describe("pausableTimer after expiry, and restart", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());
  it("never fires again once expired, whatever is paused or resumed", () => {
    const expired = vi.fn();
    const timer = pausableTimer(1000, expired);
    vi.advanceTimersByTime(1000);
    timer.pause();
    timer.resume();
    timer.restart(500);
    vi.advanceTimersByTime(5000);
    expect(expired).toHaveBeenCalledTimes(1);
  });
  it("restart gives a fresh time, and keeps the pause count", () => {
    const expired = vi.fn();
    const timer = pausableTimer(1000, expired);
    vi.advanceTimersByTime(900);
    timer.restart(1000);
    vi.advanceTimersByTime(999);
    expect(expired).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(expired).toHaveBeenCalledTimes(1);

    const held = vi.fn();
    const t2 = pausableTimer(1000, held);
    t2.pause();
    t2.restart(2000);
    vi.advanceTimersByTime(10_000);
    expect(held).not.toHaveBeenCalled();
    t2.resume();
    vi.advanceTimersByTime(1999);
    expect(held).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(held).toHaveBeenCalledTimes(1);
  });
});
