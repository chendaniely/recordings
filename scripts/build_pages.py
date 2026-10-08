"""Build the static GitHub Pages demo (spec §17.1) into _site/.

    make pages        # = make build, then: uv run --group pages python scripts/build_pages.py

1. Assemble a Shinylive app in build/pages/app/ (git-ignored): pages/app.py and
   pages/requirements.txt, vendored copies of `recordings`, `recordings_ui` (with its built
   www/, without the FastAPI app) and `shinyreact`, and demo/archive/ without its media.
2. Export it with Shinylive (the `pages` dependency group, 0.8.12) into _site/, through
   scripts/shinylive_local.py, which keeps Shinylive's assets in .cache/shinylive/.
3. Copy each demo recording's media to _site/media/<id>.<ext>, where the app links it
   (`runtime.configure(media_base="../media/")`), and add _site/.nojekyll.

Only demo/archive/ is ever exported, and nothing here reads config.toml, RECORDINGS_ARCHIVE or
a secret: everything in the site is public, the source and the archive included.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import shinyreact

from recordings.archive import Archive

REPO = Path(__file__).resolve().parents[1]
DEMO_ARCHIVE = REPO / "demo" / "archive"  # the only archive this ever exports
CORE = REPO / "packages" / "core" / "src" / "recordings"
UI = REPO / "packages" / "ui" / "src" / "recordings_ui"
ENTRY = REPO / "pages"
BUILD = REPO / "build" / "pages"
SITE = REPO / "_site"
SHINYLIVE = REPO / "scripts" / "shinylive_local.py"
TEMPLATE_PARAMS = {"title": "Recordings demo"}
FONT_URL = re.compile(r"""url\((["']?)/fonts/""")
ROOT_URL = re.compile(r"""url\(\s*["']?/""")

# Never vendored: bytecode, the FastAPI app and the uvicorn entry point (the browser runs the
# Shiny app alone), and shinyreact's Playwright helpers (only its tests use them).
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".*")
UI_SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".*", "app.py", "__main__.py")
SHINYREACT_SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".*", "playwright.py")


def _shown(path: Path) -> str:
    return str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)


def check_inputs(www: Path = UI / "www", demo: Path = DEMO_ARCHIVE) -> None:
    for name in ("ui.js", "ui.css"):
        if not (www / name).is_file():
            raise SystemExit(
                f"build_pages: {_shown(www / name)} is missing. Build the frontend first with "
                "`make build`, or run `make pages`, which builds it.")
    if not (demo / "README.md").is_file():
        raise SystemExit(
            f"build_pages: the demo archive {_shown(demo)}/ is missing. It is committed: "
            "restore it from git, or rebuild it with `make demo-archive`.")


def demo_media(demo: Path = DEMO_ARCHIVE) -> dict[Path, str]:
    """Each demo recording's media file, and its name in _site/media/: <id>.<ext>."""
    archive = Archive(demo)
    media: dict[Path, str] = {}
    for rec in archive.iter_recordings():
        path = archive.media_path(rec.id)
        # The app links <media_base><media.file>, so media.file must be exactly <id>.<ext>.
        if rec.media.file != f"{rec.id}{path.suffix}" or not path.is_file():
            raise SystemExit(f"build_pages: {rec.id}: media.file must be <id>.<ext> and exist; "
                             f"it is {rec.media.file!r}")
        media[path] = rec.media.file
    if archive.problems or not media:
        raise SystemExit(f"build_pages: the demo archive has problems: {archive.problems}")
    return media


def relative_font_urls(css: str) -> str:
    """Shinylive serves the app under app_<id>/, where ui.css's root-absolute /fonts/ URLs 404.
    Relative to ui.css they resolve to www/fonts/, which page_react serves beside it."""
    text, n = FONT_URL.subn(r"url(\1fonts/", css)
    if n == 0 or ROOT_URL.search(text):
        raise SystemExit("build_pages: ui.css should load its fonts from url(/fonts/…) and "
                         "nothing else from the site root; check its url()s")
    return text


def refuse_symlinks(*roots: Path) -> None:
    """copytree and copy2 follow symlinks, so a link in a source could copy anything it points
    at, a real archive or a secret, into the public site. None is expected, so none is allowed."""
    found = [root for root in roots if root.is_symlink()]
    for root in roots:
        if root.is_dir() and not root.is_symlink():
            for folder, dirs, files in os.walk(root):  # never enters a linked folder
                found += [Path(folder) / n for n in dirs + files if (Path(folder) / n).is_symlink()]
    if found:
        raise SystemExit("build_pages: refusing to copy a symlink into the site, which would copy "
                         "what it points at: " + ", ".join(_shown(p) for p in sorted(found)))


def assemble(app: Path, media: dict[Path, str], ui: Path = UI, demo: Path = DEMO_ARCHIVE) -> None:
    """The Shinylive app dir. Its only archive is demo/archive/, without the media files."""
    shinyreact_dir = Path(shinyreact.__file__).parent
    refuse_symlinks(ENTRY / "app.py", ENTRY / "requirements.txt", CORE, ui, shinyreact_dir, demo)
    shutil.copy2(ENTRY / "app.py", app / "app.py")
    shutil.copy2(ENTRY / "requirements.txt", app / "requirements.txt")
    shutil.copytree(CORE, app / "recordings", ignore=SKIP)
    shutil.copytree(ui, app / "recordings_ui", ignore=UI_SKIP)
    shutil.copytree(shinyreact_dir, app / "shinyreact", ignore=SHINYREACT_SKIP)
    skip_media = {p.resolve() for p in media}

    def ignore(folder: str, names: list[str]) -> set[str]:
        return {n for n in names if n.startswith(".") or n == "__pycache__"
                or (Path(folder) / n).resolve() in skip_media}

    shutil.copytree(demo, app / "demo" / "archive", ignore=ignore)
    # Only this copy changes; the normal build keeps /fonts/, which the server serves.
    css = app / "recordings_ui" / "www" / "ui.css"
    css.write_text(relative_font_urls(css.read_text(encoding="utf-8")), encoding="utf-8")


def export(app: Path) -> None:
    subprocess.run(
        [sys.executable, str(SHINYLIVE), "export", str(app), str(SITE),
         "--template-params", json.dumps(TEMPLATE_PARAMS)],
        check=True)


def main() -> None:
    check_inputs()
    media = demo_media()
    if importlib.util.find_spec("shinylive") is None:  # before anything is deleted
        raise SystemExit("build_pages: Shinylive isn't installed here. Run `make pages`, or "
                         "`uv run --group pages python scripts/build_pages.py`.")
    for old in (BUILD, SITE):  # both are build output, git-ignored
        shutil.rmtree(old, ignore_errors=True)
    app = BUILD / "app"
    app.mkdir(parents=True)
    assemble(app, media)
    export(app)
    (SITE / "media").mkdir()
    for path, name in media.items():
        shutil.copy2(path, SITE / "media" / name)
    # Pages deployed from an Actions artifact skips Jekyll anyway; this keeps a branch deploy
    # from hiding files whose names start with "_".
    (SITE / ".nojekyll").touch()
    size = sum(f.stat().st_size for f in SITE.rglob("*") if f.is_file())
    # Not Shinylive's suggested `python3 -m http.server`: it ignores Range, so media can't seek.
    print(f"build_pages: wrote {SITE.relative_to(REPO)}/ ({size / 1e6:.1f} MB, "
          f"{len(media)} media files). Serve it as Pages does with `make pages-serve`.")


if __name__ == "__main__":
    main()
