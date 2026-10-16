import { describe, expect, it } from "vitest";

import type { TagRequest, TagResult } from "../types";
import {
  EMPTY_QUEUE, STILL_WORKING_MS, dropAll, enqueue, enqueueFront, heard, newRequestId, pause,
  pendingRequests, resume, stillWorking, takeNext,
} from "./queue";

const request = (id: string): TagRequest => ({ request_id: id, action: "add", ids: ["a"], tags: ["t"] });
const reply = (id: string | null, status: TagResult["status"]): TagResult => ({
  request_id: id, status, changed: 0, unchanged: 0, done_so_far: 0, total: 0, loses_private: null,
  now_visible: 0, failures: [], undo: null, rows: [], counts: null, tags: null, note_types: null,
  message: null,
});
const origin = { kind: "picker" as const };

describe("the request queue (spec §6)", () => {
  it("sends one request at a time, in order", () => {
    let q = enqueue(enqueue(EMPTY_QUEUE, request("r1"), origin), request("r2"), origin);
    const first = takeNext(q, 0);
    expect(first.send?.request_id).toBe("r1");
    q = first.queue;
    expect(takeNext(q, 1).send).toBe(null); // r1 is in flight
    const done = heard(q, reply("r1", "done"), 2);
    expect(done.finished?.request.request_id).toBe("r1");
    expect(takeNext(done.queue, 3).send?.request_id).toBe("r2");
  });
  it("progress keeps a request in flight and resets the 30-second clock", () => {
    let q = takeNext(enqueue(EMPTY_QUEUE, request("r1"), origin), 0).queue;
    expect(stillWorking(q, STILL_WORKING_MS - 1)).toBe(false);
    q = heard(q, reply("r1", "progress"), 20_000).queue;
    expect(q.inFlight?.request.request_id).toBe("r1");
    expect(stillWorking(q, 20_000 + STILL_WORKING_MS - 1)).toBe(false);
    expect(stillWorking(q, 20_000 + STILL_WORKING_MS)).toBe(true);
  });
  it("ignores a reply to another request", () => {
    const q = takeNext(enqueue(EMPTY_QUEUE, request("r1"), origin), 0).queue;
    expect(heard(q, reply("other", "done"), 1).finished).toBe(null);
  });
  it("a refusal with a null request_id ends the one request in flight, so the queue never stalls", () => {
    // The server cannot name a request it could not parse, and only one is ever in flight.
    const q = takeNext(enqueue(enqueue(EMPTY_QUEUE, request("r1"), origin), request("r2"), origin), 0).queue;
    const refused = heard(q, reply(null, "failed"), 5);
    expect(refused.finished?.request.request_id).toBe("r1");
    expect(refused.queue.inFlight).toBe(null);
    expect(takeNext(refused.queue, 6).send?.request_id).toBe("r2");
  });
  it("hears a null request_id with nothing in flight, or a non-refusal, without throwing or changing anything", () => {
    expect(heard(EMPTY_QUEUE, reply(null, "failed"), 5)).toEqual({ queue: EMPTY_QUEUE, finished: null });
    const q = takeNext(enqueue(EMPTY_QUEUE, request("r1"), origin), 0).queue;
    for (const status of ["done", "progress", "confirm"] as const) {
      const out = heard(q, reply(null, status), 5);
      expect(out.finished).toBe(null);
      expect(out.queue).toBe(q);
    }
  });
  it("pauses while the confirmation is open, and sends the confirmed resend first", () => {
    let q = enqueue(enqueue(EMPTY_QUEUE, request("r1"), origin), request("r2"), origin);
    q = takeNext(q, 0).queue;
    q = pause(heard(q, reply("r1", "confirm"), 1).queue);
    expect(takeNext(q, 2).send).toBe(null);
    q = resume(enqueueFront(q, { ...request("r1b"), confirm_ids: ["a"] }, origin));
    expect(takeNext(q, 3).send?.request_id).toBe("r1b");
  });
  it("drops what is queued when the connection is lost, and says whether one was in flight", () => {
    const q = takeNext(enqueue(enqueue(EMPTY_QUEUE, request("r1"), origin), request("r2"), origin), 0).queue;
    expect(pendingRequests(q).map((r) => r.request_id)).toEqual(["r1", "r2"]);
    const lost = dropAll(q);
    expect(lost).toEqual({ queue: EMPTY_QUEUE, dropped: 1, inFlight: true });
  });
  it("makes request IDs from getRandomValues, never randomUUID", () => {
    const id = newRequestId((a) => a.fill(171));
    expect(id).toBe("ab".repeat(16));
    expect(newRequestId()).toMatch(/^[0-9a-f]{32}$/);
  });
});
