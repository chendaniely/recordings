# Step 4 Tier 1 fixes: the Standards review (f0c079e..4718636)

**What I ran:** `make lint` passed; `uv run pytest -q` gave 1987 passed. All six commits have the subject format, a why, a failing line, `Plan:` and the right `Co-Authored-By:`.

## Important
1. **`worker.py:470-511` with `baseline._sweep`: after a whole-pass failure, every tick re-sweeps the whole archive (implementer's concern 1, upheld).**
   - Once `_pass_failed` is set, no `only` is passed. While the fault lasts (§13.1 names "a full disk"; a final `db.put` that fails is enough), each 2 s tick runs a full sweep, about 21 ms per recording.
   - The sweeps run back to back under `idle`, ahead of requests.
   - `_sweep(only=None)` logs every failing recording again each time, which breaks "logged once".
   - **Fix:** back off the whole-archive retry (for example once per schedule interval, a §22 number with its reason), or have `patterns_pass` return its sweep when only the record fails. Keep every retry quiet.

## Minor (judgement calls)
2. **`worker.py` structure: Divergent Change in a large class.**
   - `_patterns(again=)` is a flag argument that switches three behaviours.
   - `**only` hides a plain `only=None`.
   - The patterns-owed rule sits in `_owed_private`, while the consent rule is `flags.still_owed` (Feature Envy).
   - The `ID_RE` "unnamed-folder" filter is duplicated in `_sweep`.
   - **Fix:** move the rule to `flags.py`, and give `PatternsPass` its unchecked IDs. The rest goes with Simplicity 3's split.
3. **Primitive Obsession:** `OWED_PATTERNS` is a constant, but `"consent"` stays a bare literal (`worker.py:721`, `flags.py:225`).
4. **`writer.py:930`'s in-method import** avoids a cycle. Move the `K_KEEP_OUT_WORDS` reader, with its error, into `plaud/checks.py`.
5. **A restart whose start pass raises** leaves `_pass` as None, so open `"patterns"` flags resolve while still unchecked. The blocker still holds the ping.
6. **366517a's baseline and views tests have no failing line,** which the commit format asks for.
7. **`consent-add.sh:6` is 170 characters;** the header wraps at 100.
8. **`PATTERNS_STOPPED` names the disk** even for `KeepOutWordsError`.

## CLAUDE.md
Its new text is true: the retry goes through `patterns_pass` and `Writer.reconcile_plaud`. "Retries at each tick" would be more precise with "while the last attempt left recordings or raised".

## The `/run/secrets` widening (concern 2)
Accepted:
- The decision table names `/run/secrets`, and §17.2 has a Revisions line.
- No shipped path is refused: `/backup-extra/deploy.env`, `/consent` and `/state/manifests` all pass.
- Doctor's messages are unchanged.

Verdict: changes needed (0 Critical, 1 Important)
