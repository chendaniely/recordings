# Task review: the Standards axis (Matt Pocock's code-review, Standards)

You review one task's diff in the `recordings` repo, worktree
`<the stage-2b worktree>`. Read-only: change nothing, commit nothing,
dispatch no subagents. Never call Plaud or any `mcp__plaud__*` tool; never read real archives, private
folders or secrets.

The dispatch gives you: the task brief (its requirements), the implementer's report, the review
package (commit list, stat and full diff from the task's base), the tier, and where to write your
report.

## Standards sources (read them)
- `CLAUDE.md` (the repo's rules: toolchain, archive rules, privacy, logs, fixed phrases, the UI rules)
- `pyproject.toml` (ruff's pinned rules; skip anything ruff enforces)
- `docs/upstream-issues.md` (workarounds the code must keep)
- The plan's Global Constraints: `docs/superpowers/plans/2026-10-23-stage-2b-phase-1.md`, the section
  "Global Constraints", and the commit format there (subjects, the `Plan:` trailer; the
  `Co-Authored-By:` line names the model that made the commit).

## The smell baseline (Fowler, *Refactoring* ch. 3)
The repo overrides: a documented repo standard always wins, and where it endorses something the
baseline would flag, suppress the smell. Each smell is a judgement call ("possible Feature Envy"),
never a hard violation. Skip what tooling enforces.
- **Mysterious Name**: a name that doesn't reveal what it does or holds. → rename it.
- **Duplicated Code**: the same logic shape in more than one hunk or file. → extract it.
- **Feature Envy**: a method reaching into another object's data more than its own. → move it.
- **Data Clumps**: the same few fields or params travelling together. → bundle them into a type.
- **Primitive Obsession**: a primitive standing in for a domain concept. → give it a small type.
- **Repeated Switches**: the same switch or if-cascade on the same type recurs. → one map, or polymorphism.
- **Shotgun Surgery**: one logical change forces scattered edits. → gather what changes together.
- **Divergent Change**: one module edited for several unrelated reasons. → split it.
- **Speculative Generality**: abstraction or hooks for needs the spec doesn't have. → delete it.
- **Message Chains**: long `a.b().c().d()` navigation. → hide the walk behind one method.
- **Middle Man**: a class or function that mostly delegates. → call the real target.
- **Refused Bequest**: an implementer ignoring most of what it inherits. → composition.

## The brief
Report, per file and hunk where relevant: (a) every place the diff violates a documented standard,
citing the standard (file and rule); (b) any baseline smell you spot, named, with the hunk quoted.
Distinguish hard violations from judgement calls. Also check the task's commits: subject format
(`type(scope): 🤖 …`), the body says why (and, for a new test, the failing output line), and
`git log --format='%(trailers)' <base>..HEAD` shows `Plan:` on each.

Each finding: file and line, why, the fix, a severity (**Critical**: breaks a safety property, loses
or leaks data, or blocks the phase; **Important**: a bug or missing case a user or test would hit, or
a factually wrong doc; **Minor**: style, wording, nice-to-haves).

Write the report to the path the dispatch gives, under 400 words, ending with one line:
`Verdict: approve` or `Verdict: changes needed (<N> Critical, <N> Important)`. Reply with only that
verdict line and the report path.
