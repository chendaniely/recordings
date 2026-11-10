# Task 3 re-review: fix round ef894b3..81136d1

Checked: the keepout, import_run, tagops and models tests pass (364), and ruff is clean. I also ran three probes: the old parser against the new, every code point through `visible()`, and a fuzz of 20,000 lines. They are in `<scratch>/t3rr/probe*.py`.

## Findings
1. **Addressed.** keepout.py:147 uses `.match`. All eight forms are typos (test_keepout.py:150-167), and `<id>:` stays kept (:59).
2. **Addressed, as worded.** keepout.py:157-161; pinned at test_keepout.py:107-147. But see N1.
3. **Addressed.** keepout.py:84-86, 166-167 and 182-185; the test is at :170-180. `visible()` never makes or drops a separator: I checked every code point.
4. **Addressed.** keepout.py:213 and 225; the test is at :183-187.
5. **Addressed.** tagops.py:303 gives the same results as the old inline copy.
6. **Addressed.** keepout.py:68-73, 77, 125-128, 201-202 and 249.
7. **Addressed.** keepout.py:1-3; spec :1400 and :2524.
8. **Addressed.** test_keepout.py:32.

## New problems
**N1. Important: keepout.py:160-161 fails open.** A single whole ID with stray decoration used to stop the list. Now it reads as a kept line, so the recording is downloaded and only listed. I probed these, and each was a typo before and is kept now:
- `` - `<id> ` — asked `` (a space inside the backticks);
- ``` - ``<id>`` — asked ```;
- `- of_ <id> — asked`;
- `` - of_`<id>` ``;
- `` - `<id>`` ``.

The fuzz found 1,191 distinct lines like these that moved from typo to kept. The refined wording ("one unbroken run … that isn't one whole ID") lets them through, and no ruling covers them. Each holds one ID and nothing else, unlike two IDs or `<id> added`.

Fix: after the run check, call a line a typo when its only non-empty run is 32 long. Exclusions are tried first, so such a line is one ID in a form the grammar doesn't read. I probed this fix, and every ruled case still reads as ruled. Pin the lines above as tests, and add a clause to spec §12.1 rule 4, or ask Dan.

**N2. Minor: keepout.py:81.** `_OF` strips `of_` anywhere in a chunk, so `- abc 3fa9of_<28 hex>` became text (it was a typo). Fix: match `^of_` only.

**N3. Minor: spec §12.1 rule 4 (:1414-1420)** doesn't mention the separator typo. Dan's preferences say docs that a change makes wrong are fixed in that change. Also, the stop phrase says a separator line "looks like an ID", even when it holds none.

## Out of scope
- The plan (:2094, :2232, :2511-2531) still says that two IDs and `<id> added` stop the list.
- An ID split into five pieces of 7 or fewer reads as text. The QA's §4 accepted this.

Verdict: changes needed (0 Critical, 1 Important)
