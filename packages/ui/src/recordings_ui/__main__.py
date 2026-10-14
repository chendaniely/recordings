"""`recordings-ui`: run the web app. `--demo` serves a fresh copy of the demo archive."""

from __future__ import annotations

import argparse
import os
import signal
import sys

import uvicorn

from recordings_ui.app import create_app
from recordings_ui.settings import from_env


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="recordings-ui")
    ap.add_argument("--demo", action="store_true", help="serve a fresh copy of the demo archive")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)
    settings = from_env(os.environ, demo=True if args.demo else None)
    # docker stop, kill and systemd send SIGTERM. uvicorn puts the default handler back and
    # re-raises it, which skips atexit and leaks the demo's temporary copy. A normal exit with the
    # same status (143) runs atexit.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(128 + signal.SIGTERM))
    uvicorn.run(create_app(settings), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
