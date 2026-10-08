"""FastAPI app: media streaming and health here, the Shiny app mounted at "/" (spec §13)."""

from __future__ import annotations

import mimetypes

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from recordings.archive import Archive
from recordings.ids import ID_RE
from recordings_ui import runtime
from recordings_ui.hosts import HostGuard
from recordings_ui.settings import Settings


def create_app(settings: Settings) -> HostGuard:
    archive = Archive(settings.archive)
    runtime.configure(archive)
    from recordings_ui.shiny_app import app as shiny_app  # after configure()

    api = FastAPI(title="recordings", docs_url=None, redoc_url=None, openapi_url=None)

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
        return FileResponse(path, media_type=media_type, content_disposition_type="inline",
                            headers={"X-Content-Type-Options": "nosniff"})

    api.mount("/", shiny_app)  # last, so the routes above win
    # Around everything, the Shiny app and its websocket included (recordings_ui.hosts).
    return HostGuard(api, settings.allowed_hosts)
