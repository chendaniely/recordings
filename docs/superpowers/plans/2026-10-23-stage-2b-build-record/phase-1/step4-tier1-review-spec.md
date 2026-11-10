# Step 4, Tier 1 fixes: Spec review (f0c079e..4718636)

Probes: scratchpad `probes/`. Targeted pytest: 343 passed.

## Findings

1. **Important.** `worker.py` `_owed_private`, the `OWED_PATTERNS` branch. A flagged recording that is busy at a retry moves to `busy`, and its flag resolves. Probe `test_probe_busy_flip.py`: a StaleFile title matching the new pattern is flagged. Next tick, another holder keeps its lock past the wait. The flag and the blocker clear, and the due turn pings `/sync`, though the recording isn't private. This breaks §13.1 case 5 and §16's row ("until the recording is checked, or is private, or is gone"). **Fix:** keep an open patterns flag owed while its ID is `unwritten` or `busy`, and add the test.
2. **Minor.** After a restart whose pass raises, `_pass` is None, so open patterns flags resolve at the first tick. `patterns_error` still holds the ping. **Fix:** keep them while `_pass_failed` is set.
3. **Minor.** After a raise, every tick retries the whole archive: 2.1 s per tick at 150 recordings, so about 17 s at ~1,240. Late consent, requests and turns wait a sweep; the heartbeat is fine. **Fix:** retry the whole archive once per turn (the council's wording), and keep `only` at every tick.
4. **Minor.** `PATTERNS_STOPPED`'s "check the archive disk" is wrong for `KeepOutWordsError`.
5. **Minor.** `consent-add.sh:6` is 170 characters.
6. **Minor, unrelated.** `recordings_ui/plaud.py` `_Read.words` iterates a row that isn't a list: a string shows as letters.

## Requirements

- **S1:** done. Eleven fold forms are tagged; the audio-router import is the only path that tags at creation.
- **keep_out_words:** fails closed in the Writer, the sweep and the CLI.
- **G1/S2:** done, apart from finding 1.
- **R2:** done. A bare `sync` covers more than `sync "$tmp"`.
- **S4:** done. `/consent` and `/state/manifests` are never refused.
- **cmd_baseline:** done, exit 1 (§9.6).
- **Spec edits:** they agree with P8, P15 and decision 10, and change no decision or scenario.
- **Scenarios and docs:** none named; none stale.

## Security checklist

1. Secrets: fine. Synthetic fixtures; refusals name a path, never a value.
2. Permissions: fine. No new modes or mounts.
3. Network: fine. Nothing listens; `test_plaud_imports` passes.
4. Failing closed: finding 1. A whole-pass raise never pings.
5. Injection: fine. IDs are checked against `ID_RE` before `path_for`; the shell builds nothing from data.
6. Logging: fine. The class and counts only; no `exc_info`.

Verdict: changes needed (0 Critical, 1 Important)
