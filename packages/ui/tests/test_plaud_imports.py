"""`recordings_ui` never imports the Plaud client, the token store or the sync (stage 2b spec §9.6,
§10.1). Only the worker holds the sign-in, so `web` can't leak it, and the Pages demo's Pyodide
never loads them."""

import subprocess
import sys

FORBIDDEN = (
    "recordings.plaud.client",
    "recordings.plaud.tokens",
    "recordings.plaud.sync",
    "recordings.sources.plaud",
)
# Imports every module under recordings_ui, then prints what is loaded, one name per line. A fresh
# interpreter, so nothing another test imported counts.
PROBE = """
import importlib, pkgutil, sys
import recordings_ui
for info in pkgutil.walk_packages(recordings_ui.__path__, "recordings_ui."):
    importlib.import_module(info.name)
{extra}
print("\\n".join(sorted(sys.modules)))
"""


def _loaded(extra: str = "") -> set[str]:
    result = subprocess.run(
        [sys.executable, "-c", PROBE.format(extra=extra)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return set(result.stdout.split())


def test_recordings_ui_never_imports_the_client_the_token_store_or_the_sync():
    loaded = _loaded()
    assert {"recordings_ui.shiny_app", "recordings_ui.app", "recordings_ui.status"} <= loaded
    assert loaded.isdisjoint(FORBIDDEN), sorted(loaded & set(FORBIDDEN))
    # The probe sees an import when there is one: the client, imported on purpose, shows up.
    assert "recordings.plaud.client" in _loaded("import recordings.plaud.client")
