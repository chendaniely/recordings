"""Shiny server for the React client. Server holds only reactive computation and returns JSON
(shinyreact-build-app skill). ReactApp discovers www/ui.js + www/ui.css next to THIS file, and
reads the immediate calling frame to do so, so ReactApp(...) must be called here, not in a helper.

Tagging (tagging spec §5): the browser sends `tag_request` and `snippet_request` as event
inputs, mounted once in App with null defaults, and `refresh`. Each request is checked here, on
the event loop, then run as this session's extended task, outside Shiny's process-wide reactive
lock, so one window's batch never freezes another. Replies go back as messages ("tag_result",
"snippets") through shinyreact's send_message. Nothing is ever sent from a URL. Shiny's reconnect
stays off: nothing here calls `session.allow_reconnect`.

Plaud (2b spec §9.3; phase 1 plan, Task 20): the browser sends `plaud_request`, an event input
mounted once in App with a null default. Each one gets an asyncio task of this session's, outside
the reactive flush: recordings_ui.plaud's `submit` checks it and inserts its row on
asyncio.to_thread, never on the tag service's threads, and the reply goes back at once as
"plaud_result". While the row is pending the task reads it every POLL_SECONDS (2 s) and sends what
changed. Unlike the tag jobs these never queue behind each other, so a long import never delays
the next press's answer (P6). `plaud_sync` and `status` redraw every 60 s and when one of this
session's requests ends; `plaud_arrivals` counts from the later of the session's start and its
last Refresh (decision 22).
"""

from __future__ import annotations

import asyncio
import logging

import shinyreact
from shiny import Inputs, Outputs, Session, reactive
from shinyreact import ReactApp, reactive_output

from recordings_ui import plaud, runtime, views
from recordings_ui.tagging import Job, Refusal, SnippetJob

# By name: the test server loads this file as a module of another name.
log = logging.getLogger("recordings_ui.shiny_app")


def server(input: Inputs, output: Outputs, session: Session) -> None:
    archive = runtime.archive()
    media_base = runtime.media_base()
    tags = runtime.tagging().session()
    refreshed = reactive.value(0)  # Refresh presses (decision 8)
    retried = 0  # the last press whose retry of a failed open was queued
    plaud_views = runtime.plaud_settings()
    plaud_state = runtime.plaud_state()
    plaud_reason = runtime.plaud_reason()
    zone = plaud_views.timezone_name if plaud_views is not None else "UTC"
    started = plaud.clock()
    refreshed_at = reactive.value(started)  # the arrivals cue counts from here (decision 22)
    plaud_ended = reactive.value(0)  # this session's Plaud requests that have ended
    following: set[asyncio.Task] = set()
    closed = False  # the session has ended: no task sets a value or takes the lock after it

    async def send_tag_result(reply: dict) -> None:
        await shinyreact.send_message(session, "tag_result", reply)

    async def send_snippets(reply: dict) -> None:
        await shinyreact.send_message(session, "snippets", reply)

    async def send_plaud_result(reply: dict) -> None:
        await shinyreact.send_message(session, "plaud_result", reply)

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
        refreshed_at.set(plaud.clock())

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

    requests = plaud.requests

    def row_reply(row, down: str | None) -> dict:
        """A row as a reply. While it is still queued the message follows the worker's heartbeat
        (spec §9.3): the insert's words said it once, and the worker can stop or come back after."""
        reply = {**row.to_dict(), "preview": None}
        if reply["state"] == "queued":
            if down is not None:
                reply["message"] = down
            elif requests.is_worker_down(reply["message"]):
                reply["message"] = requests.QUEUED
        return reply

    async def poll(request_id: str) -> tuple[dict | None, bool]:
        """One read of the row: (its reply or None, whether it was read). A failed read is not an
        end: sqlite3.Error and OSError (a busy or full disk, a state.db moved) are tried again."""
        import sqlite3  # here: Pages' Pyodide has none, and never gets this far

        try:
            row = await asyncio.to_thread(requests.read, plaud_state, request_id)
            if row is None:
                return None, True
            down = None
            if row.state == "queued":
                down = await asyncio.to_thread(
                    requests.worker_notice, plaud_state, now=plaud.clock(), timezone_name=zone
                )
            return row_reply(row, down), True
        except (sqlite3.Error, OSError) as exc:
            return {"error": type(exc).__name__}, False

    async def follow(first: dict, *, quiet: bool = False) -> None:
        """While the row is pending, read it every POLL_SECONDS and send what changed (§9.3)."""
        shown = first
        failing = False
        while True:
            await asyncio.sleep(plaud.POLL_SECONDS)
            reply, read = await poll(first["request_id"])
            if not read:
                if not failing and not quiet:
                    log.warning("plaud request: reading its row failed (%s)", reply["error"])
                failing = True
                continue
            if failing and not quiet:
                log.warning("plaud request: reading its row works again")
            failing = False
            if reply is None:
                return  # pruned: nothing of its own to follow
            # `preview` stays None: Release's question and its title come in Phase 2, with the
            # worker's release-preview handler (types.ts's PlaudResult keeps the key).
            ended = reply["state"] in plaud.FINAL_STATES
            if reply != shown:
                await send_plaud_result(reply)
                shown = reply
            if ended:
                # Out of `following` before the lock: stop_following (the session's end) must not
                # cancel this task while it holds the lock in the middle of flush(), where
                # shiny's invalidate_later tasks are uncancellable (shiny 1.8.0, _core.py).
                following.discard(asyncio.current_task())
                if closed:
                    return
                async with reactive.lock():
                    with reactive.isolate():  # no Context of an effect's to lean on
                        plaud_ended.set(plaud_ended.get() + 1)
                    await reactive.flush()
                return

    async def follow_unsaved(first: dict) -> None:
        """`submit`'s backstop answers NOT_SAVED for a commit that hung past its deadline, and
        that one row may still land (Task 14): look for it for UNSAVED_WINDOW, and follow it. The
        state.db that couldn't be used may still not be: reads that fail are quiet here."""
        for _ in range(max(1, int(plaud.UNSAVED_WINDOW / plaud.POLL_SECONDS))):
            await asyncio.sleep(plaud.POLL_SECONDS)
            reply, read = await poll(first["request_id"])
            if read and reply is not None:
                await send_plaud_result(reply)
                if reply["state"] not in plaud.FINAL_STATES:
                    await follow(reply)
                return

    async def plaud_job(raw: object) -> None:
        try:
            reply = await plaud.submit(
                plaud_state, raw, now=plaud.clock(), timezone_name=zone, reason=plaud_reason
            )
            reply = {**reply, "preview": None}
            await send_plaud_result(reply)  # within a second of the press (P6)
            if plaud_state is not None and reply["state"] in ("queued", "running"):
                await follow(reply)
            elif plaud_state is not None and reply["message"] == plaud.NOT_SAVED:
                await follow_unsaved(reply)
        except Exception as exc:  # noqa: BLE001  its class only (Global Constraints)
            log.warning("plaud request: the task failed (%s)", type(exc).__name__)

    @reactive.effect
    @reactive.event(input.plaud_request)  # ignore_none: the mount default is null (spec §9.3)
    def _plaud_request() -> None:
        task = asyncio.create_task(plaud_job(input.plaud_request()))
        following.add(task)  # held, so it isn't collected while it waits
        task.add_done_callback(following.discard)

    def stop_following() -> None:
        nonlocal closed
        closed = True
        for task in list(following):
            task.cancel()  # its row stays: the worker runs it, and another window can ask again

    session.on_ended(stop_following)

    @reactive_output
    def recording():
        # Unset (before the client's first message) is a silent exception, which the client
        # sees as "pending"; an explicit null clears the pane.
        rid = input.selected_id()
        if not rid:
            return None
        plaud_ended()  # Details' Plaud lines follow a Keep it here of this window's
        # Pages keeps its tags in memory: its record comes from the store. Elsewhere, the files.
        read = None if tags.store.saves else tags.store.read
        return views.recording_view(
            archive, rid, media_base=media_base, read=read, plaud=plaud_views, now=plaud.clock()
        )

    @reactive_output
    def status():
        reactive.invalidate_later(60)  # backups finish and disks fill without anyone clicking
        plaud_ended()
        return views.status_view(archive, runtime.status_settings(), plaud=plaud_views)

    @reactive_output
    def plaud_sync():
        reactive.invalidate_later(60)  # the worker's turns end without anyone clicking
        plaud_ended()  # and its newest result line (last_result) follows this window's requests
        return plaud.sync_view(plaud_views, now=plaud.clock(), can_request=plaud_reason)

    @reactive_output
    def plaud_arrivals():
        reactive.invalidate_later(60)  # read every 60 seconds (§15.3)
        plaud_ended()  # and when this window's request ends: a Keep it here clears a flag it counts
        since = max(started, refreshed_at())
        return plaud.arrivals(plaud_views, since=since, now=plaud.clock())


app = ReactApp(server)
