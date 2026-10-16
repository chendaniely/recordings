"""Shiny server for the React client. Server holds only reactive computation and returns JSON
(shinyreact-build-app skill). ReactApp discovers www/ui.js + www/ui.css next to THIS file, and
reads the immediate calling frame to do so, so ReactApp(...) must be called here, not in a helper.

Tagging (tagging spec §5): the browser sends `tag_request` and `snippet_request` as event
inputs, mounted once in App with null defaults, and `refresh`. Each request is checked here, on
the event loop, then run as this session's extended task, outside Shiny's process-wide reactive
lock, so one window's batch never freezes another. Replies go back as messages ("tag_result",
"snippets") through shinyreact's send_message. Nothing is ever sent from a URL. Shiny's reconnect
stays off: nothing here calls `session.allow_reconnect`.
"""

from __future__ import annotations

import logging

import shinyreact
from shiny import Inputs, Outputs, Session, reactive
from shinyreact import ReactApp, reactive_output

from recordings_ui import runtime, views
from recordings_ui.tagging import Job, Refusal, SnippetJob

# By name: the test server loads this file as a module of another name.
log = logging.getLogger("recordings_ui.shiny_app")


def server(input: Inputs, output: Outputs, session: Session) -> None:
    archive = runtime.archive()
    media_base = runtime.media_base()
    tags = runtime.tagging().session()
    refreshed = reactive.value(0)  # Refresh presses (decision 8)
    retried = 0  # the last press whose retry of a failed open was queued

    async def send_tag_result(reply: dict) -> None:
        await shinyreact.send_message(session, "tag_result", reply)

    async def send_snippets(reply: dict) -> None:
        await shinyreact.send_message(session, "snippets", reply)

    # The writer's first open can take as long as the archive is big (an index rebuild, demo
    # mode's adopt). Awaited inside an output, it would hold Shiny's process-wide reactive flush,
    # and every window with it. So this task waits for it, outside the flush, and the Library is
    # drawn at once, without tagging ("the archive is being opened"), then once more when the
    # open ends: the task's status changes then, under the lock, with a flush (spec §5).
    @reactive.extended_task
    async def first_open() -> None:
        try:
            await tags.store.ready()
        except Exception as exc:  # noqa: BLE001  its class only (Global Constraints)
            log.warning("waiting for the archive's first open failed (%s)", type(exc).__name__)

    if not tags.store.first_open_done():
        first_open.invoke()

    @reactive_output
    def library():
        # Refresh rebuilds the view, as does the end of the first open, and nothing else: deltas
        # arrive by message. It never waits, for the open or behind a write. A new Refresh also
        # queues a retry of a failed open (spec §5).
        nonlocal retried
        first_open.status()
        presses = refreshed()
        view = tags.library_now(refresh=presses > retried)
        retried = presses
        return view

    @reactive.effect
    @reactive.event(input.refresh)  # ignore_none: the mount default is null (decision 8)
    def _refresh() -> None:
        refreshed.set(refreshed.get() + 1)

    # The extended tasks are given everything as arguments (they may not read a reactive value),
    # and queue a second invocation behind a running one, so one window's requests never run at
    # once. They reply only by message and set no reactive value: one set here would have to be
    # set inside `async with reactive.lock():`, followed by `await reactive.flush()` (spec §5).
    # Nothing here cancels them: a cancellation (at shutdown) goes through the service, which
    # keeps what landed and lets it go on. Any other exception is logged by class: shiny would
    # keep it in the task's `.error`, which nothing reads.
    @reactive.extended_task
    async def tag_job(job: Job | Refusal) -> None:
        try:
            await tags.run(job, send_tag_result)
        except Exception as exc:  # noqa: BLE001  its class only (Global Constraints)
            log.warning("tag %s: the job failed (%s)", job.request_id, type(exc).__name__)

    @reactive.effect
    @reactive.event(input.tag_request)  # ignore_none, never ignore_init (spec §5)
    def _tag_request() -> None:
        tag_job.invoke(tags.check(input.tag_request()))  # check, enqueue, return

    @reactive.extended_task
    async def snippet_job(job: SnippetJob | Refusal) -> None:
        try:
            await tags.snippets(job, send_snippets)
        except Exception as exc:  # noqa: BLE001  its class only (Global Constraints)
            log.warning("snippets %s: the job failed (%s)", job.request_id, type(exc).__name__)

    @reactive.effect
    @reactive.event(input.snippet_request)
    def _snippet_request() -> None:
        snippet_job.invoke(tags.check_snippets(input.snippet_request()))

    @reactive_output
    def recording():
        # Unset (before the client's first message) is a silent exception, which the client
        # sees as "pending"; an explicit null clears the pane.
        rid = input.selected_id()
        return views.recording_view(archive, rid, media_base=media_base) if rid else None

    @reactive_output
    def status():
        reactive.invalidate_later(60)  # backups finish and disks fill without anyone clicking
        return views.status_view(archive, runtime.status_settings())


app = ReactApp(server)
