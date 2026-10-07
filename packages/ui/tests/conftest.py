import json
import shutil
from pathlib import Path

import pytest

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
