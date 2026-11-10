# Task 5 review: Standards (b223c65..a642251, Tier 1)

The script and the test match the brief's listings exactly (diffed). 24 tests pass, ruff is clean and `commitmsg.py` accepts a642251. The commit's subject, its why-and-failing-line body, its `Plan:` trailer and its Sonnet 5.5 co-author line (per the report) are all right. These rules hold: no archive writes, sentinel, `is_private`, synthetic fixtures, no Plaud host, no personal paths, uv only.

## Hard violations

**Important: `docs/runbooks/first-run.md:519`** says "Exit 78 means the guard refused before any call". But `SpikeRefused` (exit 78) is also raised after calls:
- the page cap (`scripts/plaud_spike.py:422,433`)
- "stopped: files/{id} didn't answer an object" (`:440`)
- after the listing: a missing `--id` (`:472`), `_resolve` (`:464`), `_clip` (`:813`), and the own clip being in the archive (`:843`, which a test pins)

The doc is factually wrong (preferences: "Wrong docs are fixed in the change that made them wrong"), and §9.6 keeps 78 for configuration. Fix:
- add a `SpikeStopped` that exits 1
- check `--id` in `main()` before any call
- reword the runbook: only "refused before any call" means nothing was sent.

**Minor: `:1045-1046`** prints the `ConfigError` text, against the docstring at `:27` ("never its text, whatever the exception"). Print `_error(exc)`, or narrow the docstring.

**Minor: `:876,888`.** S9 prints part keys raw, so a note tab's slug (`notes:plaud-summary`) is printed, which S8 hides (`:795`, "a tab's name is Dan's"). Share one `_part_label`. The test's check (`test_plaud_spike.py:191`) is case-sensitive on `"Summary"`, so it misses the slug. Test `out.lower()` instead.

**Minor:** runbook `:496` runs to 125 columns; the section wraps at 100.

## Smells (judgement calls, all Minor)

- **Duplicated Code:**
  - `_LIST_KEYS` and `_batch` (`:98,382`) copy `client.py:73,414-419`.
  - `_NoRedirect` (`:487`) is a third copy (`client.py:232`, `alerts.py:49`).
  - `_listing` and `_raw_rows` repeat one paging loop.
  - S2's and S4's refresh-token preambles (`:625-629`, `:663-667`) are identical.
  - `check_api_base` (`:1038`) runs again in `PlaudClient.__init__`.
- **Data Clumps:** `guard.allows(row.listed_id, <title>, own_clip=session.args.own_clip)` plus "refused by the guard" repeats at `:450`, `:480` and `:858`. Make it one helper.
- **Primitive Obsession:** the reasons are bare strings, and `:124` counts a missing title as "pattern". Use a `Literal` type and a "no title" reason.
- **Mysterious Name:** `Guard.here` (a set) beside S7's `here` (a dict); `_named`, `_names` and `_names_seen`; `env` for an envelope.

Verdict: changes needed (0 Critical, 1 Important)
