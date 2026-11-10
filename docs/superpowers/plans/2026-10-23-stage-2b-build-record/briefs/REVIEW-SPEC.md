# Task review: the Spec axis (Matt Pocock's code-review, Spec)

You review one task's diff in the `recordings` repo, worktree
`<the stage-2b worktree>`. Read-only: change nothing, commit nothing,
dispatch no subagents. Never call Plaud or any `mcp__plaud__*` tool; never read real archives, private
folders or secrets.

The dispatch gives you: the task brief (its requirements, including its Scenarios IDs), the
implementer's report, the review package (commit list, stat and full diff from the task's base), the
tier, and where to write your report.

## The spec
- `docs/superpowers/specs/2026-10-23-stage-2b-design.md`: binding. Part I is what Dan was promised;
  Part II is how. Dan's decisions 1–12 bind (12 as refined 2026-10-30); 10 and 11 win over 4, 5, 8
  and 9 where they differ. Its Scenarios table gives each `C…` ID's expected outcome. Read the
  sections the task brief cites, and any section the diff touches.
- The plan's task text (the brief) is the spec's argument; its decisions 1–60 are the controller's
  readings. Where the brief and the spec disagree, the spec wins: report it.

## The brief
Report: (a) requirements the spec or the brief asked for that are missing or partial; (b) behaviour
in the diff that wasn't asked for (scope creep); (c) requirements that look implemented but where the
implementation looks wrong, including tests that would pass with the behaviour broken. Quote the spec
line (section number) for each finding. Also: for each Scenarios ID the brief names, say whether a
test in the diff proves it. And grep the docs for every name the change renamed or removed, and for
"not yet", "planned", "unverified" near what it touched: a doc the change made untrue is Important.

Each finding: file and line, why, the fix, a severity (**Critical**: breaks a safety property, loses
or leaks data, or blocks the phase; **Important**: a bug or missing case a user or test would hit, or
a factually wrong doc; **Minor**: style, wording, nice-to-haves).

Write the report to the path the dispatch gives, under 400 words, ending with one line:
`Verdict: approve` or `Verdict: changes needed (<N> Critical, <N> Important)`. Reply with only that
verdict line and the report path.
