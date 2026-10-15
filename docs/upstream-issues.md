# Upstream issues

Problems found in libraries this repo depends on, and how the repo works around them. They are
recorded here only. Nothing from this project is reported to the upstream projects: it is
AI-written, and it stays out of places where people do the real work.

Each entry says how to tell when the workaround can go. Check the entries when upgrading the
library (CLAUDE.md, "Upgrading shinyreact").

## shinyreact 0.1.0: an unmounted output stays bound to a removed element

Found 2026-10-15, after a Pages test failed once in CI with `Output recording not found`
logged twice. Fixed here in `packages/ui/frontend/src/App.tsx` (PR #17).

**What shinyreact does.** Shiny delivers an output's values only to an element it has bound
to that output's name. shinyreact gives every output a component uses a hidden placeholder
`<div>` for Shiny to bind, plus a registry of the components listening to it. A value goes
from Shiny to the placeholder, then to shinyreact, then through the registry to the components.

**The bug.** When the last component using an output unmounts, shinyreact cleans up on the next
animation frame, in this order:

1. It deletes the output from its registry.
2. It removes the placeholder from the page.
3. On a later frame, it unbinds and rebinds everything inside its hidden container.

Shiny's unbind (`unbindOutputs` in `shiny.js`) only looks inside that container. The placeholder
has already left it, so it is never unbound, and Shiny keeps sending that output's values to the
detached element. shinyreact finds no registry entry for them, logs `Output <id> not found` and
drops the value. This lasts until a component uses the output again, which binds a new
placeholder. Shiny also never reports the detached output as hidden, so the server keeps
computing it.

**Where it showed here.**
- Leaving the library unmounted the recording pane and left `recording` stale. In CI, the
  demo's first `recording` value arrived after the Status click, because Pyodide is slow, so
  the test failed only sometimes.
- Leaving Status left `status` stale. The server recomputes it every minute, so after one
  visit to Status the console logged the error every minute.

**The workaround.** `App` never unmounts, and it subscribes to every output
(`useShinyOutputStatus`), so no output's registry entry ever empties and the cleanup never
runs. The cost: Status is computed from the start of a session, not from its first visit.
CLAUDE.md makes this a rule: **a new output must also be subscribed in `App`.**

**The test.** `test_a_page_left_behind_keeps_no_binding_and_logs_nothing`
(`packages/ui/tests/e2e/test_library.py`) leaves each page and sends a value for the absent
output. It then asserts that no Shiny binding points at a detached element and that nothing
was logged. It fails without the workaround.

**When it can go.** The code is `scheduleCleanup` in shinyreact's
`pkg-js/src/shiny-react/output-registry.ts`, still unchanged on its main branch at e881235. A
version that unbinds the placeholder before removing it (`Shiny.unbindAll(el, true)`) fixes it.
To check a new version, remove the two `useShinyOutputStatus` lines from `App` and run
`make e2e`: if the test above passes, the bug is fixed. Keeping the lines is harmless either way.

## shinyreact-build-app skill: what it doesn't cover

- **How long an output lives once its components unmount** (the entry above). The skill doesn't
  say an output can outlive its last component, or that it then breaks.
- **`reactive.invalidate_later`**, for an output that refreshes on its own (Status's 60-second
  refresh, `shiny_app.py`). Stage 2a checked it against the installed Shiny's docstring instead.
