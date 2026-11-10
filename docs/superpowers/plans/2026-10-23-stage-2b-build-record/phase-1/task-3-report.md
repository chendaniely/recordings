# Task 3 report: consent grammar and keep-out word

Status: DONE. Commit 4971dd4.

## Implemented
- `models.py`: `visible`, `fold`; `is_private_tag` now `fold(tag).split("/", 1)[0] == "private"`.
- New `plaud/keepout.py` (verbatim from brief): grammar, `parse_consent`, `read_consent`, `signature`, `keep_out_match`, `valid_word`, stop phrases.
- `plaud/consent.py` replaced: `load_consent_list` reads via `keepout.read_consent`; `LEGACY_ENTRY_RE` kept.
- Tests: new `test_keepout.py`; `test_import_run.py` gets `OF` constant and the of_/upper-case test.

## TDD evidence
- RED 1: `pytest test_keepout.py test_import_run.py` -> collection error `ModuleNotFoundError: No module named 'recordings.plaud.keepout'`.
- RED 2: the new import test failed with `ConsentListError: the consent list .../consent.md holds no IDs, so it is the wrong file: refusing`.
- GREEN: the brief's Step 4 set (keepout, import_run, doctor, cli, models, tagops): 415 passed.
- `make check`: lint clean, 1431 passed (141 deselected), gitleaks no leaks.

## Notes / concerns
- My Write tool turned `\u...` escapes in the test file into literal invisible characters; ruff PLE2515 caught some, and I converted all literal Cf/U+3164/NBSP chars back to escapes (scratchpad script), matching the brief. Source files had no such escapes.
- A hook blocked one Bash command that mentioned a media path-like string in a Python heredoc; I redid those edits with Edit. No data was read.
- Base recorded in `task-3-base`. No spec/brief conflicts found.

## Files
packages/core/src/recordings/models.py, plaud/keepout.py, plaud/consent.py; packages/core/tests/test_keepout.py, test_import_run.py.

## Fix round (Tier 1 review), commit 81136d1
- Applied all 8 rulings. RED (7 groups): `pytest test_keepout.py` -> 17 failed (`assert () == (2,)`, `'typo' == 'text'`, `'typo' == 'kept'`, `IndexError`, `ValueError: embedded null byte`), 74 passed.
- GREEN: keepout, import_run, tagops, models tests: 364 passed. `make check`: lint clean, 1446 passed, gitleaks no leaks.
- Decision 12 refined: typo needs a hex chunk (split on whitespace/backticks, `of_` stripped) of length >= 8 and != 32; round 3's pins for two IDs / `<id> added` / dated note now expect `kept`.
- Lines split on \r\n, \r, \n; other separators make the line a typo. NUL path -> stop/None. tagops uses models.fold. Spec pointers now name `consent.LEGACY_ENTRY_RE`.
- Note: `_wide` literal U+3000 was converted to an escape.

## Fix round 2 (re-review), commit 52340fe
- N1: after the run check, a line whose only non-empty run is 32 hex long is a typo (exclusions tried first). Pinned: `` - `ID ` — asked ``, doubled backticks, `- of_ ID — asked`, `- of_`ID``, `` - `ID`` ``. Kept pins: `ID:`, `` - `ID` added ``, `ID ID`, `- ID, fine`.
- N2: `_OF` is `^of_`; test `- abc 3fa9of_<28 hex>` is a typo.
- N3: spec §12.1 rule 4 gained the two clauses.
- RED: new typo test failed with `assert () == (2,)` (all six forms); kept pins passed at once.
- `make check`: lint clean, 1456 passed, gitleaks no leaks.
