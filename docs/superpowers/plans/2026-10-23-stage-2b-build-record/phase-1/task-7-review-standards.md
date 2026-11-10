# Task 7 review: Standards (c2f1d44..5dcf3fe, Tier 1)

Checked: CLAUDE.md, pyproject's ruff rules, the plan's Global Constraints and commit format. The task's six test files pass (240) and ruff is clean. `git ls-files -ci --exclude-standard` is empty, so the new ignore lines hide no tracked file. The template's `[plaud]` block matches spec §17.4 word for word.

**These meet the standards:** CLAUDE.md's Settings rule (every new key has a row read through `plaud_settings`; `api_base` is set explicitly; `LATER` is empty; `deadman_sync_url` has a `SECRET_VALUES` row). The log lines use fixed phrases (a status or a class name, never a URL). `__repr__` hides the sync URL. The subject format and the `Plan:` trailer are right. `Co-Authored-By` correctly names the implementer's model.

## Important

1. **`plaud/settings.py:7-8`, a factually wrong doc** (Dan's Docs rule: a doc is fixed in the change that made it wrong). The docstring says a bad pattern "raises ConfigError naming the key, never its value". But `patterns_from_config` (`reconcile.py:116`) quotes the pattern, verified: `[plaud] auto_private_patterns: 'value-7(' is not a regular expression (…)`. **Fix:** reword it, for example "…naming the key (reconcile's error for a bad pattern also quotes the pattern)".

## Minor

2. **`alerts.py:18-19`:** "Both URLs… Neither may hold…". There are three URLs now. Say "the ntfy server and both ping URLs".
3. **`alerts.py:210`:** "(checked as AlertConfig checks it)" reads as if `ping_url` checks the URL itself. It doesn't: it sends to any `str`. This is possible Primitive Obsession (a bare `str` standing for a checked ping URL). **Fix:** state the precondition ("`url` must come from an AlertConfig").
4. **`alerts.py:15` and `alerts.py:112`** name `worker.conclude_turn`, which does not exist until Task 11 (plan line 11427). That is fine inside the phase. The forward review should check that Task 11 keeps the name.
5. **Duplicated Code** (judgement calls):
   - `ping_url` (`alerts.py:209-229`) repeats `_deliver`'s opener call and exception tuple. Building `_deliver` on `ping_url`'s status would remove the copy.
   - `_whole` (`settings.py:47`; Mysterious Name, maybe `_int_at_least`) repeats the integer checks at `backup.py:115` and `mirror.py:111`.
   - `token_store`'s absolute-path check repeats the rule in `config._path`.
6. **`settings.py:75`:** the `consent and consent.strip()` guard repeats `consent_list_path`'s own check and reads the section a second time. A possible fix: `Path(consent).expanduser()`.
7. **Tests, `test_plaud_settings.py`:** `test_a_wrong_value_names_its_key` asserts "[plaud] must be a table" again in all 8 cases. `test_an_invalid_keep_out_word…` asserts the `BAD_WORD` pin 6 times. Give each check its own test.
8. **Commit body:** the only failing line recorded is `ModuleNotFoundError`. pytest stops when a file fails to collect, so the two new `test_alerts` tests never ran in RED. Their failing lines (`TypeError`, `AttributeError`) are missing. The commit format asks for "the output line that showed it failing" for each new test.

Verdict: changes needed (0 Critical, 1 Important)
