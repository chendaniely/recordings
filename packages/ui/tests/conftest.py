import json
import shutil
from pathlib import Path

import pytest

from recordings.index import Index
from recordings.init import init_archive
from recordings.state import State
from recordings.writer import Writer

REPO = Path(__file__).resolve().parents[3]
DEMO_ARCHIVE = REPO / "demo" / "archive"


@pytest.fixture
def demo_archive(tmp_path) -> Path:
    """A fresh copy, so no test can change the committed demo."""
    dst = tmp_path / "archive"
    shutil.copytree(DEMO_ARCHIVE, dst)
    return dst


@pytest.fixture(scope="session")
def demo_ids() -> dict[str, str]:
    aliases = json.loads((REPO / "demo" / "canned" / "aliases.json").read_text())
    return {slug: rid for rid, slug in aliases.items()}


@pytest.fixture
def writer(tmp_path) -> Writer:
    """A new, empty archive and its Writer, in folders of its own, so a test can also take
    demo_archive (which is tmp_path/"archive")."""
    root, state = tmp_path / "written-archive", tmp_path / "written-state"
    init_archive(root, state, writer_id="test")
    return Writer(
        root, State.open(state), Index(tmp_path / "written-index" / "index.db"), writer_id="test"
    )
