# Task 6 review: Standards axis (2f3f35c..7197dea, Tier 1)

**Commit.** Subject `feat(state): 🤖 …` fits the format; the body says why and quotes a failing line;
`%(trailers)` shows `Plan:`, and `Co-Authored-By` names Sonnet 5.5, the implementer's model, as the
Global Constraints ask. Ruff passes on every changed file. No hard violations of CLAUDE.md or the
Global Constraints: log and error texts are fixed phrases with class names, no title column can be
set, fixtures are synthetic, `SCHEMA_VERSION` stays 1 and `MIGRATIONS` stays empty.

## Findings (all Minor)

1. **`plaud/checks.py:172` `_utc`, `:190` `_decode`: Duplicated Code, and the two copies disagree.**
   This task adds `when.py` so a naive stamp is read one way (the council's M6), but `checks.py`
   converts times its own way. `naive.astimezone(UTC)` treats a naive time as local time. I checked
   with `TZ=America/Vancouver`: `_utc(datetime(2026,10,23,14,20))` gives `21:20Z`, while
   `parse_stamp` gives `14:20Z`. `make check`'s `TZ=UTC` run hides this. Fix: `_utc` treats a naive
   time as UTC, as `when.py` does (or calls into it). `when.py:29` repeats `:24`'s naive rule too.
   Fold the two into one private helper.
2. **`index.py:172` `wal`: when SQLite answers with a different mode instead of raising, it returns
   False and logs nothing.** The docstring says a switch that fails is logged, and both callers
   ignore the return value. Fix: log the same fixed phrase on that path too.
3. **`checks.py:266, 306, 357`: Duplicated Code.** The same `Cls(*(_decode(n, v) for n, v in
   zip(cols, row, strict=True)))` appears three times, and `:361` and `:379` build the same `?`
   list twice. Fix: a `_row(cls, cols, row)` helper and a `_marks(n)` helper.
4. **`checks.py:200` `_columns`: Mysterious Name.** It checks the names and returns nothing. Fix:
   rename it `_refuse_unknown_columns`.
5. **`state.py:399`: Duplicated Code.** `PurePosixPath(file).parent.name` is a fourth copy of the
   rule "a recording.json's folder name is its ID", after `writer.py:414, 947, 969`. Fix: a small
   helper in `ids.py`. `forget` knowing `plaud_listing`'s columns would be Feature Envy, but the
   brief puts it there, and `checks.py` imports `State`, so I don't count it.
6. **The commit message: "Checked SQLite's WAL page".** CLAUDE.md says to state what you checked.
   That line is the plan's own prescribed text, and the report doesn't show the check was made in
   this task. Its content is right (one host with shared memory; a mode change needs the file to
   itself). Fix: the close commit says who checked it.

Not counted, because the plan or brief requires them: the unused `K_*` constants and `BLOCKERS`,
the `plaud_requests` table (decision 3), event kinds as strings, and `Index.wal` as a name.

Verdict: approve
