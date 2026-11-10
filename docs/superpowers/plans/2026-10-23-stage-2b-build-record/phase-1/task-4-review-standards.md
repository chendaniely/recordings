# Task 4 review: Standards (5e51267..79f79d2)

The committed files match the brief exactly, ruff passes, and the 53 tests pass. The commit's subject and body are in the plan's format, `commitmsg.py` passes, and the trailers show `Plan:` and the implementer's own `Co-Authored-By` (Sonnet).

## Important

1. **`client.py:470-471`, `280-293`: a local disk error reads as "offline".** `download`'s catch-all `OSError` also catches `part.open`, `fh.write` and `os.replace`, and `_from_transport` turns any `OSError` into `PlaudOffline`. My loopback probe, with a read-only destination folder, printed `PlaudOffline | offline`. That contradicts the docstring ("refused, unreachable, no DNS"), and under §10.2 it ends the run with "offline" when the disk or its permissions are at fault. **Fix:** map only errors from the opener and `read1`, let file `OSError`s through, and add a read-only-folder test.
2. **`client.py:26-27`, `434-436`: the docs say a stall raises `IncompleteDownload`, but it raises `PlaudUnavailable`.** With no bytes for `READ_TIMEOUT`, `read1` raises `TimeoutError`, which becomes `PlaudUnavailable`. My probe (`READ_TIMEOUT=0.5`) confirmed it. Both docstrings and `IncompleteDownload.message` ("short, empty or stalled") say otherwise, and nothing tests this. This breaks the preferences.md rule "Wrong docs are fixed in the change that made them wrong." **Fix:** treat a timeout in the read loop as `IncompleteDownload`, and test it.

## Minor

3. **Duplicated Code** (judgement call). `_get` (330-341) and `download` (465-475) repeat the same `except` chain: extract it into one helper. `endpoint.check_api_base` duplicates `alerts._check_url`, and `_NoRedirect`/`_opener` duplicate alerts.py's. The two loopback checks already differ: alerts uses a fixed set of names, `is_loopback` any address in 127/8. Share one.
4. **`client.py:21-22`:** "Every error is one of this module's classes" is false. `mkdir` (441) raises `OSError`, `get_json` raises `ValueError`, and `__init__` raises `ConfigError`.
5. **`client.py:134` vs `388`:** `listed_id` is documented as "exactly as the listing gave it", but the code strips it.
6. **Mysterious Name:** `short` would read better as `on_short_body`.
7. **Commit body:** the plan wants each new test's failing line. The body lacks `test_plaud_imports.py`'s (`ModuleNotFoundError`) and the proxy test's (`AssertionError: assert not ['https_proxy']`).
8. **CLAUDE.md:146:** "No test reaches Plaud" holds only inside pytest's own process. The `getaddrinfo` guard does not reach subprocesses. Say so.

Verdict: changes needed (0 Critical, 2 Important)
