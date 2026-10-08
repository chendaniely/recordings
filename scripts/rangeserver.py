"""A local static server that answers Range requests, to stand in for GitHub Pages.

    uv run python scripts/rangeserver.py _site --prefix /recordings/ --port 8008

Python's http.server ignores Range, so the browser can't seek media it serves; GitHub Pages
answers Range with 206. `--prefix` serves the folder under a subpath, as Pages serves a project
site (https://<user>.github.io/<repo>/), so root-absolute URLs fail here as they would there.
Binds 127.0.0.1 only. The Pages smoke test imports `serve()`.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import threading
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


class RangeHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler, plus a single `bytes=` range and a path prefix."""

    prefix = "/"  # always ends in "/"
    quiet = False

    def translate_path(self, path: str) -> str:
        base = self.prefix.rstrip("/")
        url_path = urlsplit(path).path
        if url_path != base and not url_path.startswith(base + "/"):
            return ""  # outside the prefix: 404
        return super().translate_path(path[len(base):] or "/")

    def send_head(self):
        path = self.translate_path(self.path)
        match = _RANGE.fullmatch((self.headers.get("Range") or "").strip())
        if not match or not os.path.isfile(path) or match.group(1) == match.group(2) == "":
            return super().send_head()
        size = os.path.getsize(path)
        if match.group(1) == "":  # the last N bytes
            start, end = max(0, size - int(match.group(2))), size - 1
        else:
            start = int(match.group(1))
            end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
        if start >= size or start > end:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        f = open(path, "rb")  # noqa: SIM115  (closed by the caller, as in http.server)
        f.seek(start)
        self._remaining = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(self._remaining))
        self.end_headers()
        return f

    def end_headers(self) -> None:
        # Advertise ranges on every response, as Pages does.
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def copyfile(self, source, outputfile) -> None:
        remaining, self._remaining = getattr(self, "_remaining", None), None
        if remaining is None:
            return super().copyfile(source, outputfile)
        try:
            while remaining > 0 and (chunk := source.read(min(64 * 1024, remaining))):
                outputfile.write(chunk)
                remaining -= len(chunk)
        except ConnectionError:
            pass  # the browser cancelled a media request it no longer needs

    def log_message(self, format: str, *args) -> None:
        if not self.quiet:
            super().log_message(format, *args)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        # The browser drops media connections it no longer needs, mid-response; that's normal.
        if not isinstance(sys.exception(), ConnectionError):
            super().handle_error(request, client_address)


def normalize_prefix(prefix: str) -> str:
    """"recordings", "/recordings" and "/recordings/" all mean "/recordings/"."""
    return f"/{prefix.strip('/')}/" if prefix.strip("/") else "/"


def serve(root: Path, *, prefix: str = "/", port: int = 0, quiet: bool = False) -> Server:
    """Start serving `root` under `prefix` on 127.0.0.1 in a background thread.

    Port 0 picks a free port (`server.server_address[1]`). Stop with `server.shutdown()`, then
    `server.server_close()`.
    """
    prefix = normalize_prefix(prefix)
    handler = type("Handler", (RangeHandler,), {"prefix": prefix, "quiet": quiet})
    server = Server(("127.0.0.1", port), partial(handler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", type=Path)
    ap.add_argument("--prefix", default="/")
    ap.add_argument("--port", type=int, default=8008)
    args = ap.parse_args()
    if not args.root.is_dir():
        raise SystemExit(f"{args.root}/ does not exist; run `make pages` first")
    server = serve(args.root, prefix=args.prefix, port=args.port)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}{normalize_prefix(args.prefix)}"
    print(f"Serving {args.root}/ at {url} (Ctrl-C stops it)", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
