"""FastAPI app: media streaming and health here, the Shiny app mounted at "/" (spec §13)."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from recordings.archive import Archive
from recordings.ids import ID_RE
from recordings.index import Index
from recordings.init import adopt_archive
from recordings.state import State
from recordings.writer import NotTheWriter, Writer

from recordings_ui import runtime
from recordings_ui.hosts import HostGuard, WriteGuard
from recordings_ui.settings import Settings, find_demo_archive
from recordings_ui.status import status_settings
from recordings_ui.tagging import LOCK_WAIT, ArchiveStore, TagService

log = logging.getLogger(__name__)

NO_CONFIG = "there's no config.toml, so this app only reads the archive"


def demo_writer(copy: Path, folder: Path | None) -> Callable[[], Writer]:
    """Demo mode's Writer (tagging spec §5). Its state.db, index and locks live in the demo's
    temporary folder, beside the copy, and go with it at exit, SIGTERM included (§17). It never
    reads the config, RECORDINGS_STATE or RECORDINGS_INDEX, and writes beside nothing but the
    folder settings.from_env made: with none, or on the committed demo, it refuses. Called from
    create_app, never from settings.from_env, which the Pages demo also runs. Adopting the copy
    happens in the returned function, on the writer thread, so startup does no disk work and the
    thread classifies its errors."""

    def open_writer() -> Writer:
        if folder is None or copy.parent.resolve() != folder.resolve():
            raise NotTheWriter("demo mode writes only into its own temporary folder")
        try:
            committed = find_demo_archive({}).resolve()
        except SystemExit:
            committed = None
        if copy.resolve() == committed:
            raise NotTheWriter("this is the committed demo archive, which is never written")
        state = folder / "state"
        if not (state / "state.db").exists():  # tagging plan, decision 13
            adopt_archive(copy, state, writer_id="demo")  # InitError: the mirror's marker, etc.
        writer = Writer(
            copy,
            State.open(state),
            Index(folder / "index.db"),
            writer_id="demo",
            lock_timeout=LOCK_WAIT,
        )  # State and Index hold no connection between uses, so a failure leaves nothing to close
        writer.write_docs()  # §6.7, as Writer.open does for the real archive
        return writer

    return open_writer


def writer_opener(settings: Settings) -> Callable[[], Writer]:
    """How the writer thread opens the process's one Writer. It replaces write_docs_at_startup:
    opening writes the archive's docs (§6.7), and only into an archive this process may write;
    anywhere else the app still serves, read-only, with the reason as `cannot_tag_reason`."""
    if settings.demo:
        return demo_writer(settings.archive, settings.demo_folder)
    cfg = settings.config
    if cfg is None:

        def refuse() -> Writer:
            raise NotTheWriter(NO_CONFIG)

        return refuse
    return lambda: Writer.open(cfg, lock_timeout=LOCK_WAIT)


def create_app(settings: Settings) -> HostGuard:
    archive = Archive(settings.archive)
    tagging = TagService(ArchiveStore(archive, writer_opener(settings)))
    runtime.configure(
        archive,
        status=status_settings(settings.config) if settings.config else None,
        tagging=tagging,
    )
    from recordings_ui.shiny_app import app as shiny_app  # after configure()

    @asynccontextmanager
    async def lifespan(_api: FastAPI) -> AsyncIterator[None]:
        yield
        tagging.stop()  # a batch stops between recordings; every write is atomic
        # Queued writes fail instead of running; wait, bounded and off the event loop, for the
        # one running, so it can't re-create the demo's folder after atexit removes it.
        await asyncio.to_thread(tagging.store.join, 5.0)

    api = FastAPI(
        title="recordings", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )

    @api.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "demo": settings.demo}

    @api.get("/media/{recording_id}")
    def media(recording_id: str) -> FileResponse:
        # The ID picks the file via recording.json, so no client-supplied path is ever used.
        if not ID_RE.fullmatch(recording_id):
            raise HTTPException(status_code=404)
        # recording.json is hand-editable, so media.file must stay inside the recording's folder,
        # exist (FileResponse would 500 on a missing file), and be audio or video: never a page
        # or an SVG served from the app's own origin.
        try:
            path = archive.media_path(recording_id)
            inside = path.resolve().is_relative_to(archive.path_for(recording_id).resolve())
            found = inside and path.is_file()
        except (KeyError, ValueError, OSError):
            # unknown id, impossible date in the id, a broken or unreadable recording.json,
            # or a NUL byte in media.file
            raise HTTPException(status_code=404) from None
        media_type, _ = mimetypes.guess_type(path.name)
        if not found or not (media_type or "").startswith(("audio/", "video/")):
            raise HTTPException(status_code=404)
        # FileResponse answers Range with 206, and an unsatisfiable range with 416 (Starlette docs).
        return FileResponse(
            path,
            media_type=media_type,
            content_disposition_type="inline",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    api.mount("/", shiny_app)  # last, so the routes above win
    # Around everything, the Shiny app and its websocket included (recordings_ui.hosts): first the
    # host names, then the cross-site write check and the same-origin resource policy.
    return HostGuard(WriteGuard(api, settings.allowed_hosts), settings.allowed_hosts)
