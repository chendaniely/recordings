from pathlib import Path

import pytest
from shiny.testserver import TestServerSession

from recordings.archive import Archive
from recordings_ui import runtime

SHINY_APP = Path(__file__).resolve().parents[1] / "src" / "recordings_ui" / "shiny_app.py"
pytestmark = pytest.mark.parametrize("local_server", [str(SHINY_APP)], indirect=True)


@pytest.fixture(autouse=True)
def _archive(demo_archive):
    runtime.configure(Archive(demo_archive))
    yield
    runtime.configure(None)


def test_library_is_published(local_server: TestServerSession):
    assert local_server.get_output("library").value["counts"] == {"all": 4, "untagged": 1}


def test_nothing_selected_is_silent(local_server: TestServerSession):
    assert local_server.get_output("recording").status == "silent"


def test_selecting_publishes_the_recording(local_server: TestServerSession, demo_ids):
    local_server.set_inputs(selected_id=demo_ids["jfk-rice"])
    assert local_server.get_output("recording").value["id"] == demo_ids["jfk-rice"]


def test_clearing_the_selection_publishes_none(local_server: TestServerSession):
    local_server.set_inputs(selected_id=None)
    assert local_server.get_output("recording").value is None
