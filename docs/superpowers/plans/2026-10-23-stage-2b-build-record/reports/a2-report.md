# Area A2: the Plaud client's throughput: report

Status: **DONE_WITH_CONCERNS** (every number is the spec's own default, since S5, S10 and S11
were skipped; two small builder's calls go slightly past the spec's letter, below. Nothing
blocks A3 to A5).

Branch `stage-2b`, from `19dd8bd`. Not pushed (the controller pushes).

## Commits

| SHA | Subject |
|---|---|
| `26fa571` | feat(plaud): 🤖 the client's throughput: pacing, the budget, backoff and Range resume |

One commit: the throttle, the download's resume, the fake's faults, the worker's rate-limit wait
and the docs move together (the spec's §10.1 and §10.2, CLAUDE.md, the user guide and the guesses
file describe the same code). The message passed `scripts/commitmsg.py`.

## What was built, by spec section

### §10.2, pacing, the budget and backoff (`plaud/client.py`: `Throttle`)

Every request the client sends goes through `Throttle.call(send, budgeted=…)`; a `PlaudClient`
built with no `throttle=` makes its own, paced, so nothing in production can forget it.
- **Pacing:** one call at a time (an `RLock`, so clients sharing one throttle never overlap), each
  starting at least `PACE` (1.0 s) after the one before. Presigned links count as calls, as §14.2's
  "600-call budget" counts them.
- **The budget:** `with throttle.budget(BUDGET):` (600 s) is a scheduled run's 10 minutes; nested
  (a light turn inside a long run) the earlier deadline holds. Once spent, an API call raises
  `BudgetSpent` and sends nothing; links are never stopped by it, so an envelope's blocks and a
  download under way finish (§10.2). `left()` and `spent()` let A5's loop stop between recordings.
- **5xx, 408, timeouts** (`PlaudUnavailable`): retried after 30 s, then 60 s, each lengthened by
  up to 25% at random (never shortened); the third in a row on one call raises, and so does a
  backoff that wouldn't fit in the budget. **Connection failures** (`PlaudOffline`) are never
  retried. A dropped connection with no answer (RemoteDisconnected) is a 5xx-like retry.
- **429:** a `Retry-After` (whole seconds) that fits in the budget is waited out; with no budget,
  up to `MAX_RETRY_AFTER` (600 s). A longer one, a missing one, or a third 429 in a row on one call
  raises `PlaudRateLimited` with `until` (UTC; None when Plaud gave no time).
- **Waits** run in 5-second slices, calling the throttle's `progress` after each (§9.2's "every
  slice of a wait or backoff"); `progress` may raise to stop the wait (SIGTERM). Counters `calls`,
  `retries`, `rate_limits` for the run's record and B1's sweep cap.
- **`ends_the_run(exc)` / `RUN_ENDING`:** `PlaudAuthError`, `PlaudForbidden` (the API's 403),
  `PlaudUnavailable`, `PlaudOffline`, `PlaudRateLimited`, `BudgetSpent`. Everything else
  (`PlaudBadResponse`, `PlaudNotFound`, `PlaudCallRefused`, `PlaudLinkExpired`,
  `PlaudLinkRefused`, `IncompleteDownload`) is that recording's failure.
- **`envelope()`:** a block whose fetch raises a run-ending error now re-raises it (a storage
  outage ends the run instead of costing 90 s of backoff per block and recording every block as
  failed); a block's expiry or refusal is still recorded in place by class name.
- **Links' 4xx:** a storage host's 400, 401, 403 or 410 is the new `PlaudLinkExpired`, never the
  API's `PlaudForbidden` (which ends the run), so A4 can refetch the envelope once. The only
  existing test that named the old class for a link's 403 now says `PlaudLinkExpired`.
  `_data_link_error`'s value is dropped by the normaliser, so no stored shape changes.

### §10.1 and §18's S11 rule, the download (`_Download`)

`download(url, dest, *, fresh_link=None, admit=None)`:
- **Resume:** a connection that drops (premature end, reset, IncompleteRead) resumes from the
  bytes in `.part` with `Range: bytes=N-`, plus `If-Range` when the first answer gave a strong
  ETag, up to `MAX_RESUMES` (3). A 206 must continue exactly where the file ends
  (`Content-Range: bytes N-(L-1)/L`, its length `L-N`).
- **Range not honoured, or the object changed:** a 200 to a resume restarts from zero, once; a
  different length is refused. A fresh link to changed audio is restarted, never spliced (If-Range).
- **An expired link** (`PlaudLinkExpired`, on the first open or a resume) is swapped once for the
  caller's `fresh_link()`; without one, `PlaudLinkExpired` goes up for A4 to refetch the
  envelope. A resume refused any other way (416…) is `IncompleteDownload`.
- **The stall rule:** no bytes for 60 s (`READ_TIMEOUT`; never resumed), or under 50 KB/s on
  average past the first minute, counted from the first answer across resumes. The spec's third
  rule, a ceiling scaled from `Content-Length`, is implied by the average (past the first minute,
  elapsed > L / 50 KB/s means under 50 KB/s): I wrote it as its own check first, the mutation run
  showed it could never fire, so it went, with a docstring saying why.
- **`admit(length)`** sees `Content-Length` before the first byte or folder is written (§11.5 step
  5's disk guard raises there). **Progress** at every MiB (§9.2).

### §9.7 case 2, the rate limit's wait (`health.py`, `checks.py`, `worker.py`)

`plaud_state.rate_limit` (`K_RATE_LIMIT`) = `{"until", "at"}`. `record_rate_limit(db, until, now=)`
for A5's run when it catches `PlaudRateLimited` with an `until`; `rate_limit_wait(db, now=)` is the
`until` while it is in the future (an unreadable value is no wait: at worst one more 429). The
worker's `scheduled_turn` checks it after the sign-in pause: outcome `"rate-limited"` with
`{"until": …}`, no Plaud call, no ping, and the next try at `until` (or an interval later, if
sooner) rather than a whole interval after it. Status's existing `cause()` already words it
("rate limited until 12:45").

### §10.6 steps 4 and 5, and step 6's hook (`packages/core/tests/fake_plaud.py`)

- **Faults** (`fault(match, *responses, host="api", times=None) -> Fault`): requests on a host
  whose path equals `match`, or that `match(path, query)` accepts, take `responses` in turn, the
  last repeating, `times` in all; tried before the sign-in and the routes. Helpers:
  `server_error`, `rate_limited(retry_after)`, `html_page`, `short_body`, `timeout`, `dropped`
  (no answer at all), `expired_link`. `Response` gains `cut`, `trickle`, `reset`.
- **`FakeLink`** (`link()` now makes one; `plaud_server.links[name]`): `Range`/`If-Range` with a
  206 and its ETag, `ranges=False`, `cuts` per answer, `expire_after`, `trickle`, `hold`; records
  `served` and `starts` (byte offsets, never a header value). Old `link()` calls behave as before.
- **`after(k, hook)`**: runs once before request k+1 (step 6's "changes the account after request
  K").
- The request log was already step 4 (method, path, host, header names); a test now pins that no
  value (token, `bytes=`, ETag, query) reaches it. Still importable outside tests, no pytest.
- §21's faults now injectable: on the API, 401, 403, 429 within and past the budget, 5xx,
  timeouts, short bodies, an HTML 200 on `files/{id}`, a dropped connection; on fetches, every
  `files/{id}` failing while the listing works, every link 403ing, a link that expires once then
  works (`fault(…, host="storage", times=1)`), a link that expires during a download, a trickle,
  truncated audio, a recording that always answers 500, 404 and 500 for a deleted ID, a failure
  on page K only. **Not built here** (they need the account, step 1, which A3 owns): a clamped
  page size, an empty listing, a shrink, repeats, drift, malformed rows, the ID form flipping,
  re-keys; and the content-shaped ones (reordered blocks, volatile fields, renames) for A4 and B1.
  Each is a route plus, where needed, a `fault` predicate.

### Tests and the autouse fixture

`packages/core/tests/conftest.py` gains `plaud_waits_skipped` (autouse): a `Throttle` given no
`sleep` skips its waits in the core tests, so A1's tests and every later area's that build a
default client never really pace or back off. Production keeps `time.sleep`
(`test_the_numbers_are_the_specs` pins `Throttle().sleep is None` and the numbers).

## Tests

- New: `test_plaud_throughput.py` (65: the throttle with fake clocks, the API and links against
  the fake's faults, the download's resume, restart, fresh link, stall, progress and disk guard,
  the request log, the hook); `test_worker.py` +2 (the rate-limit wait end to end through a
  stand-in turn, and an unreadable record); `test_plaud_client.py` 1 assertion renamed.
- Mutation check (a scratch script, each guard removed in turn, its test run): resume, the block
  outage, the budget, If-Range, pacing, the third 429, the link's 4xx, the clock across resumes,
  progress per MiB and the disk guard's order were each caught.
- `make check` before the commit: pytest 2,153 passed (and again under `TZ=UTC`), Vitest 458
  passed, ruff clean, gitleaks clean. Its one warning is the existing `test_demo_guard.py` fork
  warning. No flaky test seen. (A first mutation run hung on the "unlimited 429s" mutation, which
  loops for ever by design; the script was stopped, the one mutated line restored by hand and
  checked, and the run repeated with a per-test timeout before `make check`.)
- No UI file changed: `make test-js`, `make build` and `make e2e` weren't needed beyond
  `make check`'s Vitest run.

## From audio-router, and from the spec alone

- **From audio-router** (`src/audio_router/archive/sources/plaud.py`, `tests/test_transport.py`;
  read only): `PAGE_SIZE = 100` and "the API rejects anything under 10"; paging past a short page
  (a clamp). Nothing else: audio-router has no pacing, no retry but the 401's, no 429 handling,
  no Range (its download has a 900 s timeout and one try).
- **From the spec alone** (six A2 rows in the guesses file): the pace (links counted), the budget
  and what it stops, `Retry-After` in seconds only, the no-budget wait ceiling, three 429s, 5xx
  and timeouts as one kind, the storage host's 4xx as expiry, the resume rule and its counts, the
  stall numbers.
- **Docs checked:** CPython 3.14.3's installed `urllib.request` (`do_open` wraps only
  `h.request`'s OSError; `HTTPErrorProcessor` passes any 2xx) and `http.client` (`read1` returns
  `b""` at a premature end; `RemoteDisconnected` is a `ConnectionResetError`); RFC 9110 §13.1.5,
  §14.2, §14.4, §15.5.17 (never a weak tag in If-Range; a mismatch sends the whole body; a server
  may ignore Range; Content-Range's grammar; 416).

## Questions for Dan

None that change a decision or a scenario. Two builder's calls worth a glance while testing:
1. **With no budget** (an import from the CLI or the Sync screen), a `Retry-After` longer than 10
   minutes ends the run instead of being waited out, because the worker's tick (late consent
   listings, requests) waits with it. §10.2 says those imports "have no budget". Keep the 10
   minutes, or wait out any `Retry-After` there?
2. **A storage host down three times in a row ends the run** ("Plaud unavailable"), as the API
   does, rather than failing each block in place.

## What's left, and what A3 to A5 need from me, by name

- **A3 (the listing):** `PlaudClient.list_page(page, PAGE_SIZE)` through the throttle; on any
  `ends_the_run(exc)` the listing is partial or bad. The fake's account and listing (step 1) and
  the listing-shaped faults are A3's to add: `plaud_server.route(LIST_PATH, …)` for the account,
  `plaud_server.fault(lambda p, q: q.get("page") == ["K"], …)` for page K, `after(k, …)` for drift.
- **A4 (importing):** `client.download(link, dest, fresh_link=…, admit=…)`: `admit` is the disk
  guard (2 × length against `stop_free_percent`), raising to refuse; `fresh_link` fetches the
  envelope again (with the consent and keep-out re-checks) and returns its `presigned_url`, or
  raises to stop. A `PlaudLinkExpired` out of `download` (no fresh link, or the fresh one expired
  too) and a block's `_data_link_error == "PlaudLinkExpired"` mean "fetch the envelope again,
  once" (§10.2). `ends_the_run(exc)` tells a run-ending error from that recording's failure.
- **A5 (the run and the worker):** one `Throttle` per worker process (`Throttle(progress=…)`, the
  hook bumping `worker.progress` and raising on `worker.stopping` for SIGTERM), shared by every
  client the run makes; `with throttle.budget():` around a scheduled run and a light turn, none
  for the CLI and Sync screen imports; `throttle.spent()` to stop between recordings. On
  `PlaudRateLimited` with an `until`: `health.record_rate_limit(worker.db, exc.until, now=…)` and
  the run's detail "rate limited until 14:40" (`local_hhmm`); the scheduled turn's wait is
  already in `scheduled_turn`. A Sync now during the wait: A5's call (it would meet the 429 again;
  I'd refuse it, saying "rate limited until 14:40"). `BudgetSpent` ends a run cleanly: the rest
  waits for the next run.
- **B1 (the sweep):** `throttle.rate_limits` (or `plaud_state.rate_limit.at`) for "the sweep's cap
  halves" after a 429.
- The token endpoint's refresh (`post_refresh`) is not throttled or retried: it runs under
  `tokens.lock`, which other processes wait 90 s for, so its outage ends the run and the next run
  tries again.
