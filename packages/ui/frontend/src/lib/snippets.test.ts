import { describe, expect, it } from "vitest";

import type { SnippetResult } from "../types";
import { NO_SNIPPETS, heardSnippets, nextSnippets, wanted } from "./snippets";

const ids = (n: number, from = 0) => Array.from({ length: n }, (_, i) => `id${from + i}`);
let count = 0;
const newId = () => `s${++count}`;
const result = (over: Partial<SnippetResult>): SnippetResult => ({ request_id: "s1", snippets: {}, message: null, ...over });

describe("snippets (spec §5): one request at a time, at most 200 IDs, in memory only", () => {
  it("asks for what is on screen and not held or asked for, 200 at most", () => {
    count = 0;
    const first = nextSnippets(wanted(NO_SNIPPETS, ids(250)), newId);
    expect(first.send).toEqual({ request_id: "s1", ids: ids(200) });
    expect(nextSnippets(first.state, newId).send).toBe(null); // one in flight
  });
  it("keeps what a reply brings, and asks for the rest after it", () => {
    count = 0;
    const sent = nextSnippets(wanted(NO_SNIPPETS, ids(250)), newId).state;
    const heard = heardSnippets(sent, result({ snippets: Object.fromEntries(ids(200).map((id) => [id, `text ${id}`])) }));
    expect(heard.retry).toBe(true);
    expect(heard.state.have.get("id0")).toBe("text id0");
    expect(nextSnippets(heard.state, newId).send?.ids).toEqual(ids(50, 200));
  });
  it("ignores a reply to another request", () => {
    count = 0;
    const sent = nextSnippets(wanted(NO_SNIPPETS, ids(2)), newId).state;
    const heard = heardSnippets(sent, result({ request_id: "other", snippets: { id0: "x" } }));
    expect(heard.state).toBe(sent);
  });
  it("forgets it asked for a batch that failed, so it can be asked again (Task 7 carry)", () => {
    count = 0;
    const sent = nextSnippets(wanted(NO_SNIPPETS, ids(3)), newId).state;
    const heard = heardSnippets(sent, result({ message: "Snippets couldn't be read." }));
    // Not at once: a batch that keeps failing must not loop. The next want asks again.
    expect(heard.retry).toBe(false);
    expect(heard.state.inFlight).toBe(null);
    expect(nextSnippets(wanted(heard.state, ids(3)), newId).send?.ids).toEqual(ids(3));
  });
  it("a refusal with a null request_id ends the batch in flight, so snippets never stall", () => {
    count = 0;
    const sent = nextSnippets(wanted(NO_SNIPPETS, ids(2)), newId).state;
    const heard = heardSnippets(sent, result({ request_id: null, message: "The request couldn't be read." }));
    expect(heard.state.inFlight).toBe(null);
    expect(nextSnippets(wanted(heard.state, ids(2)), newId).send?.ids).toEqual(ids(2));
    expect(heardSnippets(NO_SNIPPETS, result({ request_id: null, message: "x" })).state).toBe(NO_SNIPPETS);
  });
  it("holds a recording with no snippet as null, and never asks for it again", () => {
    count = 0;
    const sent = nextSnippets(wanted(NO_SNIPPETS, ids(1)), newId).state;
    const heard = heardSnippets(sent, result({ snippets: { id0: null } }));
    expect(heard.state.have.has("id0")).toBe(true);
    expect(nextSnippets(heard.state, newId).send).toBe(null);
  });
});
