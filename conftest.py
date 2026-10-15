"""Fixtures for every test in the repo: packages/core/tests and packages/ui/tests."""

import os
from pathlib import Path

import pytest

from recordings import config

# The test switches stay. CI sets RECORDINGS_REQUIRE_RESTIC=1, so a missing restic fails the
# real-restic tests instead of skipping them; RECORDINGS_TEST_RESTIC names a restic to use.
KEEP = frozenset({"RECORDINGS_REQUIRE_RESTIC", "RECORDINGS_TEST_RESTIC"})
NO_CONFIG = Path("/nonexistent/recordings-tests/config.toml")


def host_settings(environ) -> list[str]:
    """The variables to hide: every RECORDINGS_* and RESTIC_* one, except the switches in KEEP."""
    return sorted(name for name in environ
                  if name.startswith(("RECORDINGS_", "RESTIC_")) and name not in KEEP)


@pytest.fixture(autouse=True)
def no_settings_from_this_machine(monkeypatch):
    """No test reads this machine's settings or secrets. A shell can export RECORDINGS_* and
    RESTIC_* variables, and the server's checkout holds a real config.toml where load_config looks
    by default; either would point a test at the real archive or a real secret. It returns
    host_settings, so a test can check what it hides."""
    for name in host_settings(os.environ):
        monkeypatch.delenv(name)
    monkeypatch.setattr(config, "DEFAULT_PATH", NO_CONFIG)
    return host_settings
