"""Shared fixtures for the core tests. Every archive here is synthetic and lives in tmp_path."""

import pytest

from recordings.init import init_archive


@pytest.fixture
def new_archive(tmp_path):
    """An initialised, empty archive and its state folder: (root, state_path)."""
    root, state = tmp_path / "archive", tmp_path / "state"
    init_archive(root, state, writer_id="test")
    return root, state
