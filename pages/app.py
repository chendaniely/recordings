"""The static GitHub Pages demo's entry (spec §17.1): Shiny on Pyodide, through Shinylive.

`make pages` copies this file to the top of the app it exports, beside vendored copies of
`recordings`, `recordings_ui` (without its FastAPI app) and `shinyreact`, and `demo/archive/`
without its media. Pages serves the media itself, at `<site>/media/<id>.<ext>`.

Demo only, always: an empty environment, never os.environ, so nothing can point it at
config.toml, RECORDINGS_ARCHIVE or a secret. There is no FastAPI or uvicorn here, because
Shinylive's Starlette 0.38 can't run them; Shinylive serves the Shiny app itself.
"""

# Shinylive loads Pyodide packages only for imports in the app directory's top-level .py files,
# and the vendored packages import these, so they are imported here (and listed in
# requirements.txt). Without them the app fails to start.
import markdown_it  # noqa: F401
import pydantic  # noqa: F401

from recordings.archive import Archive
from recordings_ui import runtime
from recordings_ui.settings import from_env

# A fresh copy of the demo archive (in Pyodide's in-memory file system), as `make demo` makes.
settings = from_env({}, demo=True)
# The app runs at <site>/app_<id>/, so the site's media/ folder is one level up.
runtime.configure(Archive(settings.archive), media_base="../media/")

from recordings_ui.shiny_app import app  # noqa: E402  (after configure)

__all__ = ["app"]
