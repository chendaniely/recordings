"""Fixtures for every test in the repo: packages/core/tests and packages/ui/tests."""

import os
import socket
from pathlib import Path

import pytest
from recordings import config

# The test switches stay. CI sets RECORDINGS_REQUIRE_RESTIC=1, so a missing restic fails the
# real-restic tests instead of skipping them; RECORDINGS_TEST_RESTIC names a restic to use.
KEEP = frozenset({"RECORDINGS_REQUIRE_RESTIC", "RECORDINGS_TEST_RESTIC"})
NO_CONFIG = Path("/nonexistent/recordings-tests/config.toml")

# No test goes through a proxy. Through an https proxy, urllib hands a host name to the proxy, which
# looks it up itself, past the Plaud guard below; and the Plaud client builds its openers from
# the environment when it is imported. So every *_proxy variable goes here, before any test module
# is imported (the plan council's S6).
for _name in [name for name in os.environ if name.lower().endswith("_proxy")]:
    del os.environ[_name]


def _refuse_plaud(real):
    """No test reaches Plaud (stage 2b phase 1 plan, decision 30): looking up any host ending in
    plaud.ai fails, so a test that would call Plaud by mistake fails loudly instead. urllib, and
    so the client, resolves every host through socket.getaddrinfo."""

    def guarded(host, *args, **kwargs):
        name = host.decode("ascii", "replace") if isinstance(host, bytes) else str(host or "")
        if name.rstrip(".").lower().endswith("plaud.ai"):
            raise OSError("tests never reach Plaud")
        return real(host, *args, **kwargs)

    return guarded


# Installed once, when pytest loads this file: before any test module, and so before module- and
# session-scoped fixtures too, which a function-scoped fixture's patch would not cover. It lasts
# the whole run, in pytest's own process (a subprocess a test starts has no guard).
socket.getaddrinfo = _refuse_plaud(socket.getaddrinfo)


def host_settings(environ) -> list[str]:
    """The variables to hide: every RECORDINGS_* and RESTIC_* one, except the switches in KEEP."""
    return sorted(
        name for name in environ if name.startswith(("RECORDINGS_", "RESTIC_")) and name not in KEEP
    )


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
