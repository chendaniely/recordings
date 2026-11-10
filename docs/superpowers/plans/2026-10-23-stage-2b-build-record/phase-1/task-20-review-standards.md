# Task 20 review: Standards (Tier 2), 7984119..ee6cf9e

Ran: `uv run pytest packages/ui/tests -q` 441 passed; `make test-js` 391 passed; ruff clean.

## Important
1. **`docs/upstream-issues.md:54`, a doc this change made wrong.** It says "remove the two `useShinyOutputStatus` lines from `App`", but there are four now, and CLAUDE.md's shinyreact upgrade step 6 sends people to this entry. Fix: "remove the `useShinyOutputStatus` lines".

## Minor
- `follow` (lock): `stop_following` can cancel a task holding `reactive.lock()` in the middle of `flush()`. Shiny's `invalidate_later` makes its task uncancellable inside the lock. Fix: drop the task from `following` before taking the lock, and skip the set once the session has ended.
- `follow` (context): `plaud_ended.get()` works only because the task copied `reactive.event`'s isolate Context. Wrap the get and set in `with reactive.isolate():`.
- `follow*`: the comment "it needs sqlite3, which Pages lacks" is untrue (`requests` imports sqlite3 only for type checking, and is already imported at the top). Use `plaud.requests`.
- Duplicated Code: `"preview": None` appears three times. Primitive Obsession: the code branches on `message == NOT_SAVED`.
- `follow`: one failed read ends the poll, so the final reply never arrives (§9.3). Retry at the next tick.
- `usePlaud.tsx`: `FINAL`'s "nothing more comes" is false after a NOT_SAVED reply that `follow_unsaved` later follows. Name the `send` options type (Data Clump). The `App.tsx:31` comment should name usePlaud.

## Checked, no finding
- UI rules: React stays external. Four outputs are subscribed. `usePlaud` is mounted once, with a null default that `ignore_none` skips. No `stopPropagation`. No `ui.js` in the diff. CLAUDE.md's new text is true.
- Shiny 1.8.0:
  - `create_task` runs under `event`'s `isolate()` and inherits the session, so `resolve_id` works.
  - `on_ended` takes a sync function and runs before `destroy()`.
  - A send on a closed `StarletteConnection` is a no-op.
  - `following` is discarded when a task finishes, so nothing leaks.
  - Each `invalidate_later` timer is cancelled on invalidate or session end.
- Pages: importing `shiny_app` in a fresh interpreter loads no sqlite3, no `recordings.state`/`locks`/`worker`/`runlock`, and starts no thread. fcntl comes only from the stdlib (`asyncio`→`subprocess`). `submit` refuses before its `import sqlite3`. The Pages reason is Task 24's.
- The `ThreadState.db(*args, **kwargs)` change is right: `insert` calls `db(timeout=…)`, and `test_server_async` shares the loop thread, so the assertion holds.
- Commit: format, why, failing lines and `Plan:` are present. The Sonnet 5.5 co-author line is allowed.

Verdict: changes needed (0 Critical, 1 Important)
