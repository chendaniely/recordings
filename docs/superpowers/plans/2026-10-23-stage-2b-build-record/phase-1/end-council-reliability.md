## Council lens 2: reliability

**What I ran.** Core and UI pytest: all passed. Five probes on synthetic archives in the session scratchpad. Nothing in the repo changed apart from this file.

### What holds
- **Crash and restart.** Each of these recovers or fails closed:
  - a mutate (2a's pending revision);
  - late consent (the tag, then the flag);
  - `_record_privacy` (the markers go last);
  - the patterns pass (not recorded as complete);
  - a consent list replaced by rename;
  - `at_start`, which runs before `Writer.open`.

  A flock dies with its process.
- **Concurrency.** No `state.db` transaction takes a flock inside it. The locks are never taken in reverse order, and every wait has a timeout. The poller only reads, under WAL.
- **Time.** Every stamp is read through `parse_stamp`, so the `isoformat()` drift (already listed) has no live effect. The containers share one clock.

### Findings

1. **Minor. `worker.py:766-776`, `requests.py:358-373`, `shiny_app.py:134`: a `running` row ends only when the worker starts again.**
   - **Sequence:** the handler resolves the flag, then `requests.finish` raises (`state.db` busy, or a full disk). The row stays `running`, because `expire` and `prune` skip it.
   - **Probe:** still `running` after 8 simulated days.
   - **What the session sees:** it polls for ever, and `row_reply` gives a running row no worker-down notice. So `rc stop worker` in the middle of a request also shows "Running" indefinitely.
   - **Decision:** phase-end list. Retry `finish` at the next tick; add the notice for running rows.

2. **Minor. `docker/consent-add.sh:53-63`: the list is renamed into place without an fsync, unlike `files.write_bytes_atomic`.**
   - **Sequence:** on a filesystem without ext4's flush on rename (XFS, say), a power cut soon after can leave either an empty list (a stop, which fails closed) or a zero-filled tail.
   - **Probe:** 7 exclusions, zeroed from mid-file, parse cleanly as 3, with no stop. The worker then resolves the lost IDs' consent-late flags (`flags.py:277-287`), and Phase 2 would import their recordings.
   - **Also:** two runs at once lose one ID.
   - **Decision:** fix now. `sync "$tmp"` before the `mv`, then sync the folder.

3. **Minor. `Makefile:102-103`: the deploy guard answers before the build, which takes minutes.**
   - **Sequence:** a Sync everything (Phase 2) or a CLI baseline that starts during the build is stopped part-way, with no refusal.
   - **Decision:** phase-end list. Build, then run the guard, then `up -d --no-build --wait`.

4. **Minor. `plaud/flags.py:164`: every tick opens one `index.db` connection per consent-list ID, even when nothing changed.**
   - **Measured cost of an idle tick:**

     | Consent list | Idle tick |
     |---|---|
     | 1 ID | 10 ms |
     | 500 IDs | 404 ms |
     | 2,000 IDs | 1.5 s |

     99% of the time is in `find_by_source`.
   - **Decision:** phase-end list. One `IN (…)` query, or skip while the list's signature and the index's `data_version` hold.

5. **Minor. `worker.py:281-284`: clock jumps.**
   - **Clock put back during a start:** `starting_since` is then in the future, and `beat_alive` reads STUCK until the start ends (probed).
   - **Clock put forward over an hour:** requests queued just before the jump expire at the next tick.
   - **The heartbeat** heals within 30 s.
   - **Decision:** Later.

6. **Minor. Tests: wall-clock upper bounds with only 0.3 to 0.7 s to spare.**
   - `test_plaud_requests_ui.py:259`: `< 1.3` against a 1.0 s backstop.
   - `test_plaud_requests_ui.py:159` and `:174`: `< 1.5`.
   - `test_worker.py:291`: a 0.3 s hold against a 1.0 s wait.

   These are the load-sensitive shapes behind the flakes that `964a412` fixed.
   - **Decision:** phase-end list. Assert on the deadline given, not on time elapsed.

### Data for decision 48's existing item
- **Cost:** the patterns pass takes 20.8 ms and 18 `state.db` connections per recording, measured over 1,000 synthetic recordings.
- **At 10,000 recordings:** about 3.5 min here, and much longer on a slow disk.
- **Meanwhile:** no late consent applies.
