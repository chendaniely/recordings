# Task 7 review: Spec axis (c2f1d44..5dcf3fe), Tier 1

Matches the brief. The `[plaud]` block is §17.4's, word for word. Each new key has a row in `test_template_settings.py` whose value differs from its default. `deadman_sync_url` is checked under its own name, and a distinct-value row proves it never swaps with `deadman_url`. `ping_deadman` keeps its signature and its bool. The subprocess test for decision 31 works: run by hand, the same code prints `True` once `recordings.plaud.client` is imported. 271 tests pass across the task's files and doctor's; ruff is clean. **Scenarios:** the brief names none.

## Findings

1. **Important**: `plaud/settings.py:7`. The docstring says "a bad pattern raises ConfigError naming the key, never its value". `patterns_from_config` (`reconcile.py:115-117`) puts `{pattern!r}` and the `re.error` text into the message. Checked: `["(synthetic-name"]` gives an error that contains it. The dispatch makes this property one to check, and `test_a_wrong_value_names_its_key` would still pass with the value in the message. Fix: in `plaud_settings`, re-raise as `"[plaud] auto_private_patterns: pattern N is not a regular expression"` from None, or correct the docstring. Either way, add a `not in str(exc)` check.
2. **Minor**: `alerts.py:213-214`. `ping_url` says "Never raises … never with the URL", but it builds the `Request` outside its `try`. Checked: an unchecked `hc.example/ping/do-not-print` raises `ValueError` with the URL in its text. Fix: build the `Request` inside the `try`.
3. **Minor**: `settings.py:72`. A non-string word is stored as `repr(word)`, so `keep_out_word = 42` gives `"42"`, a valid-looking word (its `_ok` is False). A later consumer that records "the configured word" (decision 26) without reading `_ok` would enforce a word Dan never wrote. Fix: store `""` for a non-string.
4. **Minor**: stale comments that say the token lives in the state folder. `packages/core/tests/test_config.py:351` (a file this commit touched) still says "voice.db and the Plaud token". `backup.py:5` implies the same. Both make the claim the brief removed from `_index_apart`. Fix: drop it from both.
5. **Minor**: spec §19 says the README config table "gains the Plaud sign-in (`make plaud-sign-in`)". It is rightly not added yet (decision 10), but it is missing from Phase 2's carry list. Fix: add it there.
6. **Minor**: the template lost 2a's comments explaining `auto_private_patterns` (regular expressions, case ignored, only ever adds `private`) and the consent list's fail-closed reading. This is the spec's choice, so it is Dan's call.
7. **Minor**: `.dockerignore:29`. `local.mk` matches only at the build context's root, unlike its `**/` neighbours. This is harmless, because the Makefile includes it only at the root. Optional fix: `**/local.mk`.
8. **Minor**: no test shows `ping_deadman` sending nothing when only `deadman_sync_url` is set. One line would pin it.

## Security checklist

1. **Secrets:** fine. Test URLs are synthetic. `repr` shows only "set". `_check_url` names the variable, never the URL. The tokens and `.plaud/` are ignored by git and Docker. Token-file writing is not in this task. Finding 2 is the one exception.
2. **Permissions:** not applicable here. The compose mounts are Task 17's and doctor's roles Task 15's. Note: `from_config` reads the sync URL in every container, and a bad one mounted in `backup` would make `ops._alert_config` drop the backup's own ping. Task 17 should test that `backup` never gets it.
3. **Network exposure:** fine. The subprocess test pins that the client is never loaded. Tests reach only loopback (`FakeNtfy` and a closed port).
4. **Failing closed:** fine. An unset or blank consent list gives `None`, a consent stop. An invalid word is flagged, never raised (see finding 3). There is no title anywhere.
5. **Injection:** fine. There is no SQL and no shell. `token_store` must be absolute. `api_base` refuses anything but https (or http to loopback), a query, a fragment, credentials and non-printable characters.
6. **Logging:** `ping_url` logs only the status or the class. Finding 1 is the exception: a pattern reaches any caller that logs a ConfigError's text with `%s`, as `ops._alert_config` does.

Verdict: changes needed (0 Critical, 1 Important)
