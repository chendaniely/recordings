# Task 10 review: Standards (e3a9dfa..6bbb949, Tier 1)

Ruff is clean and the tests pass (31). The commit's subject, why, failing line and `Plan:` trailer are right, and `Co-Authored-By` correctly names Sonnet, the implementer.

## Important

1. **`plaud/baseline.py:43-52`, Duplicated Code that has drifted (a hard bug).** `RECONCILE_ERRORS` says "as Writer.reindex catches it", but `reindex` catches `sqlite3.Error` first (`writer.py:1006`, Task 9's "a state.db refusal never stops a whole reindex"). The copy leaves it out. A stub probe confirmed that an `OperationalError` from `reconcile_plaud` escapes `baseline_privacy`. `cmd_baseline` (`commands.py:257-264`) catches only `LockTimeout`, so a busy or full `state.db` stops the baseline part way with a traceback. The same error stops `patterns_pass` at the worker's start. **Fix:** keep one tuple in `writer.py`, with `sqlite3.Error` in it, used by both `reindex` and `baseline.py`. Add a test that a refusal lists the recording in `unwritten`.

## Minor

2. **`commands.py:280-307`:** `cmd_keep_out_words` doesn't catch `sqlite3.Error` or `ValueError` from `drop_word` or `enforced_words`, though `cmd_consent:178` and `cli.cmd_reindex` do. Listing also uses `State.open`, which creates and migrates the database, where the module's reader `_state` (`State.read_only`, "a reader creates and migrates nothing") fits. Use the reader unless `--drop` is given.
3. **Mysterious Name:** `_state` (line 112) and `_open_state` (223) both open `state.db`, and neither name says which one writes. Rename them, for example `_read_state` and `_write_state`.
4. **`commands.py:232-235`:** `_writer` says "as the worker's", but its Writer waits 30 s for a lock. The Global Constraints and §9.2 give the worker's Writer `lock_timeout=10`, and §9.1 says the CLI there acts "as the worker". Pass `lock_timeout=10`.
5. **A busy recording** (`LockTimeout`) counts as `unwritten`, so the run exits 1 and advises `recordings reindex`. Global Constraints: "A `LockTimeout` on a recording is 'busy' … neither a failure nor an attempt." A judgement call, since the brief set exit 1.
6. **Duplicated Code (judgement):** the loop that iterates, reconciles, catches, logs and counts appears twice (`baseline_privacy` 90-105, `patterns_pass` 163-173). `_writer` and `_open_state` mirror `cli._writer` and `cli._ops_context`.
7. **Speculative Generality:** `baseline_privacy`'s `now` is unused (the brief's signature; the implementer flagged it). The literal `1` at line 161 is the first pattern version, so give it a name.
8. **`drop_word` (123-132)** reads and writes `K_KEEP_OUT_WORDS` in two transactions. A worker's `enforce_word` that lands between them is lost, and a later change of word then releases it. Do both in one transaction.

Verdict: changes needed (0 Critical, 1 Important)
