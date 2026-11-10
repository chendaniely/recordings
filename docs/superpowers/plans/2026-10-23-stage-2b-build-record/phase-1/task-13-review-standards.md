# Task 13 review: Standards axis (5f30cb9..8ecb3d7)

Checked and clean: ruff, the trailers and `commitmsg.py`. Logs carry fixed phrases and class names only, with no `exc_info` and no titles. Fixtures are synthetic. `edited_by_hand` writes `recording.json` as earlier tests do, so I didn't flag it.

## Hard violations

1. **CLAUDE.md:127.** The new text says the docs are replaced "whenever the web app's or the CLI's Writer opens". Two CLI Writers write no docs: `recordings plaud …` (plaud/commands.py:242) and `reindex` (cli.py:232). The rule is in preferences.md: "Wrong docs are fixed in the change that made them wrong." Fix: name those two beside the worker's. **Important.**
2. **worker.py:713–715.** On a `ConfigError`, `main` prints `{exc}`, the exception's text, to the worker's stderr, which is its container log. The Global Constraints say never exception text. The handler at 729, in the same function, prints only the class. Fix: put one `try` around both. **Minor**, since the CLI already prints this text elsewhere.

## Judgement calls (smells)

3. **Possible Feature Envy and Duplicated Code, `_still_owed` (606–627).** It applies flags.py's rules, repeats `flags._plaud_ids`, and imports the private `_gone`, which nothing else in src does. Fix: make it a public `flags.still_owed(...)`. **Minor.**
4. **Duplicated Code, `_check_one` (420–423).** It repeats `baseline._plaud_recordings` and walks the archive a second time, so its total can drift from the pass's own count. Fix: have `patterns_pass` give `(done, total)` to `progress`. **Minor.**
5. **Middle Man, `cmd_worker` (cli.py:547).** It rebuilds argv for `worker.main`, which parses `--alive` a second time. The help text (cli.py:147) and `main`'s docstring say "else 1", but a config error exits 78. Fix: `worker.main(*, alive: bool)`, and docs that match the code. **Minor.**
6. **Data Clumps (326–329).** `_late`, `_late_failed`, `_index_down` and `_logged` always travel together. Fix: one small state object. **Minor.**
7. **Repeated Switches (199, 246–250, 262).** The test for `("starting", "failed")` appears three times. Fix: one constant. **Minor.**
8. **Stale docstrings.** The module's first line still reads "how each scheduled turn ends". `_open_writer` claims "a busy index … is tried again", but it retries only `LockTimeout`. **Minor.**
9. **CLAUDE.md:156–158 (from Task 10, not this task).** The list of `reconcile_plaud`'s callers leaves out the patterns pass and the baseline, which the worker now runs at start. **Minor.**

## Commit

The body doesn't give the red line of the added test (`AssertionError: assert 0 == 1`), and doesn't mention the `_gone` adaptation. **Minor.**

Verdict: changes needed (0 Critical, 1 Important)
