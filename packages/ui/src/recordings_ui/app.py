"""FastAPI app: media streaming and health here, the Shiny app mounted at "/" (spec §13)."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from recordings.archive import Archive
from recordings.ids import ID_RE
from recordings_ui import runtime
from recordings_ui.settings import Settings


def create_app(settings: Settings) -> FastAPI:
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
        try:
            path = archive.media_path(recording_id)
        except (KeyError, ValueError):  # unknown id, impossible date in the id, or a broken file
            raise HTTPException(status_code=404) from None
        # recording.json is hand-editable: media.file must stay inside the recording's folder,
        # and must exist (FileResponse would 500 on a missing file).
        if not path.resolve().is_relative_to(archive.path_for(recording_id).resolve()):
            raise HTTPException(status_code=404)
        if not path.is_file():
            raise HTTPException(status_code=404)
        # FileResponse answers Range with 206, and an unsatisfiable range with 416 (Starlette docs).
        return FileResponse(path, content_disposition_type="inline")

    api.mount("/", shiny_app)  # last, so the routes above win
    return api
