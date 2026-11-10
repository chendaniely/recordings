## Council lens 3: security and privacy

Probes on synthetic archives are in the scratchpad (`council/test_probe_*.py`). The privacy tests pass (65).

### 1. The checklist
1. **Secrets: fine.**
   - Errors name the setting, never the URL (`alerts.py:100`).
   - Pings log a status or a class.
   - Authorization is unredirected (`client.py:374`).
   - The ignore files cover the sign-in.
2. **Permissions: fine.**
   - `deadman_sync_url` and `/plaud` are in `worker` only; `deadman_url` is in `backup` only (`compose.yml:107,165,191`).
   - Doctor forbids them per role (`doctor.py:62-77`).
3. **Network: fine.**
   - `worker` has no ports.
   - `test_plaud_imports.py` passes.
   - The root conftest refuses `plaud.ai` and drops proxies.
4. **Failing closed:** S1 and S2.
5. **Injection: fine.**
   - SQL is parameterised; only constants go into f-strings.
   - Commands run as argv.
   - IDs are checked against `ID_RE`.
6. **Logging: fine.**
   - Logs carry IDs, counts and classes, and nothing new uses `exc_info`.
   - The `str(exc)` leftovers are already listed.

### 2. Tiers
Every tier was right:
- Tasks 1 and 2 are tooling and wording.
- `check_request` belongs to Task 14, which was Tier 1.
- Task 19 shows titles only of recordings here, inside the app.

**Forward:** Phase 2's views of titles not here (the cache's readers, Not here yet, Release's preview) must be Tier 1, because decision 56's reason ends there.

### 3. The judged set
It holds on every path the app writes: a merge under a new ID, A–P–A, an older snapshot merged in, reindex, a restore with an older or newer `state.db`, a lost `state.db`, and `forget`. The gaps are S1 and S3.

### 4. Traces
- **Consent-listed:** `private`, then a flag by ID. Flags clear only while the list reads.
- **Kept out by title:** state.db keeps the word, never the title.
- **Renamed to private:** in Phase 1, only a reconcile catches it.
- **A typo or an unreadable list:** a stop, by line number, and the blocker opens.
- **The Healthchecks ping:** an empty body.

Apart from S1 and S2, no stop lifts silently and no title leaves the app.

### 5. Exposure
All fine except S4:
- **`make consent-add`:** `ID` arrives unexpanded and is canonicalised to 32 hex before ssh. `consent-add.sh:18-25` checks it again.
- **Pages:** only committed demo files.
- **The demo guard:** `cannot_request_reason`.
- **The request input:** an allowlist of keys, and `parse_id` and `_opaque` on the IDs.

### 6. Task 13's question
**Yes.** Stop the ping through the existing `private-unwritten` blocker; it needs no new one.
- §13.2 says a marker that can't move opens that blocker "until the tag lands".
- Under P8, a `private` that is possibly owed counts as owed. Decision 46 already treats a failed late-consent pass that way.
- Leave out `busy` recordings.
- Retry the pass at each turn, not only at start. Otherwise the email lasts until a restart.

### Findings

**S1. Important. Fix now.** `audio_router.py:1184-1186`, `reconcile.py:135`.
- **What happens:** the import's create-time check calls `matches_auto_private` unfolded. `privacy._hits` and the spike both fold.
- **Probed:**
  - `"Ｐrivate…"`, `"Pri​vate…"` and `"PRI­VATE…"` are each created with their envelope and media and no tag.
  - `private` lands only at the reconcile that follows.
  - If that reconcile fails, the run stops with `ImportStopped`. The recording stays untagged and unflagged.
- **What it breaks:** CLAUDE.md's "every title the app has seen at that moment", and §13.1 case 1.
- **History:** Task 9's spec review noted it (line 12); nobody ruled on it.
- **Fix:** fold inside `matches_auto_private`. Phase 2's import then folds too.

**S2. Important. Dan; S2a to the phase-end list.** `worker.py:447-459`.
- **What happens:** when the start's patterns pass raises as a whole, `_step` swallows it.
  - Only a log line is left: no `K_PATTERNS_PASS`, no Needs attention, no blocker.
  - Nothing retries it until a restart.
- **Probed:** with an `OperationalError`, the worker went idle with `K_PATTERNS_PASS` empty and no blockers. A new private pattern silently never runs.
- **S2a:** record the attempt.
- **The ping:** as in §6.

**S3. Minor. Phase-end list.** `writer.py:874`, `privacy.py:162-170`.
- **What happens:** the judged set trusts `recording.json`'s `sha256`, not the bytes it reads.
- **Probed:** two cases are never judged, when they sit between two titles the marker holds:
  - a `sha256: null` snapshot, which `reindex` accepts;
  - a file changed under a judged sha.
- **How it is reached:** only by an outside edit or by corruption.
- **Fix:** hash the bytes read.

**S4. Minor. Phase-end list.** `backup.py:357-388`.
- **What happens:** Task 16 allows folders in `extra_paths`.
  - `/state` then backs up `voice.db` and Phase 2's `plaud-cache.db`.
  - `/run/secrets` backs up the restic password and the NAS key.
- **Only doctor objects** (`doctor.py:751-776`).
- **Why now:** Phase 3 has Dan type `/state/manifests`.
- **Fix:** `run_backup` refuses what doctor reports, as it refuses a symbolic link.

0 Critical, 2 Important, 2 Minor.
