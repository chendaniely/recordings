"""Shiny server for the React client. Server holds only reactive computation and returns JSON
(shinyreact-build-app skill). ReactApp discovers www/ui.js + www/ui.css next to THIS file, and
reads the immediate calling frame to do so, so ReactApp(...) must be called here, not in a helper.
"""

from __future__ import annotations

from shiny import Inputs, Outputs, Session
from shinyreact import ReactApp, reactive_output

from recordings_ui import runtime, views


def server(input: Inputs, output: Outputs, session: Session) -> None:
    archive = runtime.archive()
    media_base = runtime.media_base()

    @reactive_output
    def library():
        return views.library_view(archive)

    @reactive_output
    def recording():
        # Unset (before the client's first message) is a silent exception, which the client
        # sees as "pending"; an explicit null clears the pane.
        rid = input.selected_id()
        return views.recording_view(archive, rid, media_base=media_base) if rid else None


app = ReactApp(server)
