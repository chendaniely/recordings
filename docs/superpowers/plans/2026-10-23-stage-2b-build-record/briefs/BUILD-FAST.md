# Building stage 2b's rest straight through (from 2026-11-01)

Dan, 2026-11-01: "can we keep going and just have you one-shot the build? we can then just have me
go through the app to test it out." and "i rather have something working we can build on later than
try to have something perfect the first time". So the rest of stage 2b (Phase 2, "Plaud flows in",
and Phase 3, "keeps up") is built straight through, on branch `stage-2b`, without per-task plans or
review rounds. Dan tests the real app and asks for changes.

Worktree: <the stage-2b worktree>. Run every command from there;
never `cd` elsewhere (`make -C …`, `npm --prefix packages/ui/frontend …`).

## What to build from

- **The spec is the requirements:** `docs/superpowers/specs/2026-10-23-stage-2b-design.md`. Part I
  is what Dan was promised; Dan's decisions 1–12 bind; the Scenarios table gives each `C…` ID's
  expected outcome. Phase 1's Q&A answers (`docs/superpowers/plans/2026-10-23-stage-2b-phase-1-qa.md`)
  bind too.
- **What Phase 1 built:** the code itself, and in the plan
  `docs/superpowers/plans/2026-10-23-stage-2b-phase-1.md` each task's "Built differently" note (what
  was actually built) and its "Interfaces" blocks (names). Read the code over the plan's listings:
  the listings are the plan as first written.
- **The Phase 2 and Phase 3 outlines** at the plan's end, and the phase-end list's rows marked
  "Phase 2's outline", "Phase 2's start" or "Phase 3's outline": your area's lines are your to-do.
- **Plaud's real API shapes:** the spike sessions are skipped. Use what `audio-router` already does
  against Plaud: read only its `src/` and `tests/`, at `<audio-router's checkout>/src/` and
  `<audio-router's checkout>/tests/` (Task 4's client was copied from its
  `src/audio_router/archive/sources/plaud.py` at 64612df; never read anything else there, and never
  run it). Where audio-router doesn't show a shape, take the spec's, say so in a code comment
  and in the commit, and make the fake server (`packages/core/tests/fake_plaud.py`) speak it.

## The rules that stay (never relaxed)

- CLAUDE.md, all of it: the archive rules (every write through the Writer; the private tag lands
  before any source or output), privacy (no title in any log, reply message or failure reason;
  never read a private recording's transcript or notes), `recordings_ui` never importing the Plaud
  client, the token store or the sync, the UI rules (every output subscribed in App, every handler in
  App, requests only from a person's action, PHONE's media blocks together, unlayered sheet rules,
  no `stopPropagation`), and "check the docs, never write from memory".
- The consent list and the keep-out word fail closed. A stop never silently lifts.
- **Never call Plaud,** or any `mcp__plaud__*` tool. Tests use the fake server only; the root
  conftest refuses `plaud.ai` hosts.
- Never read secrets (`~/.secrets`, `~/.ssh/**`, any `.env`), a real archive, or anything outside
  this worktree; never ssh; never touch the real server (`make deploy`, `rc`).
- Python only through uv (never the system `python3` or pip); Node through nvm; no global installs.
- The repo is public: synthetic data only; no personal paths, names, hosts or tokens.

## How to work

- Test first where behaviour is new; `make check` green before every commit (and `make test-js`,
  `make build`, `make e2e` when the UI changes).
- Commits: `type(scope): 🤖 what`, a body with the why and what you checked in docs, ending
  `Plan: docs/superpowers/specs/2026-10-23-stage-2b-design.md (built straight through, Dan 2026-11-01)`
  and `Co-Authored-By: Claude <Model> 5.5 <noreply@anthropic.com>` (your model). Write the message to
  `.superpowers/sdd/2026-10-23-stage-2b-phase-1/commit-msg.txt` (read it first), check it with
  `uv run --script scripts/commitmsg.py <file>`, stage by path, `git commit -F`. Several commits per
  area are fine; never amend, reset, rebase or push.
- Git as plain single commands: no `cd … &&`, no pipes into git, no `$(…)` in git or gh arguments.
  If `.git/worktrees/stage-2b/index.lock` exists, wait and retry; never delete it.
- Don't dispatch subagents.
- Keep docs true as you go: CLAUDE.md, README, `docs/runbooks/first-run.md`, the archive's format
  docs, `config.example.toml` (a new key gets its row in `packages/ui/tests/test_template_settings.py`).
- If something would change one of Dan's decisions or a scenario's expected outcome, don't build it:
  write it in your report as a question for Dan, and build the rest.
- Report to the file the dispatch names: what you built, by spec section and scenario ID; the
  commits; the tests; what you took from audio-router and what from the spec alone; questions for
  Dan; what's left.
