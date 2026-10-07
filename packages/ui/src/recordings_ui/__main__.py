"""`recordings-ui`: run the web app. `--demo` serves a fresh copy of the demo archive."""

from __future__ import annotations

import argparse
import os

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
    uvicorn.run(create_app(settings), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
