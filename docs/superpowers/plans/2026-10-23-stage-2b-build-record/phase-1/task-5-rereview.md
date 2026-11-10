# Task 5 re-review: fix round a642251..2aecd27

45 tests pass (spike and runbook), and ruff is clean. Section 1c has no line over 100 columns. I
probed the script against the fake on 127.0.0.1 from a scratch test.

## Findings
1. Addressed. `scripts/plaud_spike.py:782,793,801-808`: the ID-form step uses `passed` only.
2. Addressed. `:893-908,931-932,971`. A future, missing or 25-hour-old start time stops with exit 1,
   and offsets are compared correctly (probed).
3. Addressed. `:819-825,866,965,978`.
4. Addressed. `:547,600-613,688-691,726`. The one "stop and ask Dan" left (`:622`, no refresh
   token) matches 1c step 2.
5. Addressed. `:494-502`, called at `:781,832,933,1009` before any `files/{id}`; runbook `:561-564`.
6. Addressed. `:1022-1026`. See new problem C.
7. Partly addressed. `SpikeStopped` and `NEEDS_ID` (`:115,1120,1144`) are in. But `_clip` (`:884`)
   still raises `SpikeRefused` after the listing: when I forced a name collision, it read two
   listing pages and exited 78.
8. Partly addressed. `:837-843,948,955-959` check before each `client.envelope`. But
   `client.envelope` fetches `files/{id}` again and follows that answer's links unchecked
   (`client.py:449`). In the probe, a title of "Private: synthetic" on that fetch alone still got
   its transcript block fetched.
9. Addressed. `:217-225`.
10. Addressed. Runbook `:499,522-526,545,556,561-570,595-596`.
11. Addressed. `:93-94,431-440,477-482,616-623`.
12. Addressed. `:26-27`. Nit: "never values" isn't quite true, because `load_config`'s texts name
    paths (`config.py:187,201,243`). Say "names keys and paths".

## New problems
- **A, Minor.** `:837-843,955-959` (finding 8). The race window is now one round trip, not 60 s.
  The envelope whose links are followed is still never checked itself, and the test at
  `test_plaud_spike.py:547` claims more than it proves. Fix: follow the links of the envelope
  `_checked` returned, with a local helper that copies `client.envelope`'s loop and uses
  `session.client.fetch_link`. Test a private title on the second fetch only.
- **B, Minor.** `plaud_spike.py:884` (finding 7). Fix: raise `SpikeStopped`.
- **C, Minor.** `:1022-1026`. When the Range probe gets no answer or an error status, the script
  prints "Content-Length: absent". It doesn't know. Fix: print "unknown" unless the status is 200
  or 206.
- **D, Minor.** Runbook `:523` says exit 1 means at least one call was made. It can come with
  nothing sent. In the probe, `s10` with no access token exited 1 after 0 requests. So can a
  `PermissionError` from `spike_folder`. The error is on the safe side. Fix: "Exit 1 means it
  stopped; calls may have been made."

## Out of scope
- S10 sends `User-Agent: recordings-spike` (`:994`), not `USER_AGENT`. If Plaud filters by user
  agent (`client.py:70`), S10's statuses would measure that, not pacing.
- The 24-hour window reads a naive `start_at` as UTC (`client.py:172`), which widens it east of
  UTC. For an uploaded file it is the upload time. Ruling 2 accepts both.

Verdict: approve
