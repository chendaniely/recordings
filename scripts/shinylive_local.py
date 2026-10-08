"""Run Shinylive's CLI with its assets cached in the repo, never in the user's cache.

    uv run --group pages python scripts/shinylive_local.py export APPDIR DESTDIR [OPTIONS]

Why: Shinylive keeps its web assets, about 400 MB per version, in
`appdirs.user_cache_dir("shinylive")` (`~/Library/Caches/shinylive/` on macOS), and has no option
or environment variable to move them (`shinylive._assets.shinylive_cache_dir()`). This points
that private function at `.cache/shinylive/` in the repo, which is git-ignored, and then runs
Shinylive's own CLI. CI uses it too, and caches that folder.

Because it patches a private function, it refuses any Shinylive but the pinned one: the `pages`
dependency group in pyproject.toml, `shinylive==0.8.12`. The assets version follows from that
pin (0.8.12 uses assets 0.10.15). A Shinylive bump must re-check `_assets.py`, where every
cache path goes through `shinylive_cache_dir()`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from shinylive import __version__ as shinylive_version
from shinylive import _assets, _main

PINNED = "0.8.12"
CACHE = Path(__file__).resolve().parents[1] / ".cache" / "shinylive"


def main(argv: list[str] | None = None) -> None:
    if shinylive_version != PINNED:
        raise SystemExit(
            f"shinylive {shinylive_version} is installed, but this wrapper patches shinylive "
            f"{PINNED}'s private cache function. Check shinylive/_assets.py, then update PINNED.")
    CACHE.mkdir(parents=True, exist_ok=True)
    _assets.shinylive_cache_dir = lambda: str(CACHE)
    _main.main(args=sys.argv[1:] if argv is None else argv, prog_name="shinylive")


if __name__ == "__main__":
    main()
