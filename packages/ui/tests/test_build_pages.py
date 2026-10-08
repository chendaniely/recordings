"""scripts/build_pages.py and pages/app.py, the static Pages demo (spec §17.1), without a browser.
The browser smoke test is tests/pages/ (`make pages-test`)."""

import importlib.util
import shutil
from pathlib import Path

import pytest

from recordings_ui import runtime

REPO = Path(__file__).resolve().parents[3]
UI = REPO / "packages" / "ui" / "src" / "recordings_ui"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build_pages():
    return load("build_pages", REPO / "scripts" / "build_pages.py")


def test_it_refuses_to_run_without_the_built_frontend(build_pages, tmp_path):
    with pytest.raises(SystemExit, match=r"ui\.js is missing.*make build"):
        build_pages.check_inputs(www=tmp_path)


def test_it_refuses_to_run_without_the_demo_archive(build_pages, tmp_path):
    (tmp_path / "ui.js").write_text("", encoding="utf-8")
    (tmp_path / "ui.css").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="demo archive .* is missing"):
        build_pages.check_inputs(www=tmp_path, demo=tmp_path / "no-archive")


def test_media_is_named_id_dot_ext(build_pages, demo_ids):
    names = set(build_pages.demo_media().values())
    assert names == {f"{rid}.mp4" if slug == "apollo11-first-steps" else f"{rid}.mp3"
                     for slug, rid in demo_ids.items()}


def test_font_urls_become_relative_and_nothing_else_may_be_root_absolute(build_pages):
    css = 'a{src:url(/fonts/A.woff2)}b{src:url("/fonts/B.woff2")}c{background:url(data:x)}'
    assert build_pages.relative_font_urls(css) == (
        'a{src:url(fonts/A.woff2)}b{src:url("fonts/B.woff2")}c{background:url(data:x)}')
    with pytest.raises(SystemExit, match="url"):
        build_pages.relative_font_urls("a{src:url(/fonts/A.woff2)}b{background:url(/img/x.png)}")
    with pytest.raises(SystemExit, match="url"):
        build_pages.relative_font_urls("a{color:red}")  # the fonts moved: check, don't guess


def _built_ui(tmp_path: Path) -> Path:
    """A copy of recordings_ui with a built frontend, without needing `make build` here."""
    ui = tmp_path / "recordings_ui"
    shutil.copytree(UI, ui, ignore=shutil.ignore_patterns("ui.js", "ui.css"))
    (ui / "www" / "ui.js").write_text("", encoding="utf-8")
    (ui / "www" / "ui.css").write_text("x{src:url(/fonts/A.woff2)}", encoding="utf-8")
    return ui


def test_the_app_holds_the_browser_parts_and_the_demo_without_media(build_pages, tmp_path):
    ui = _built_ui(tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    build_pages.assemble(app, build_pages.demo_media(), ui=ui)

    files = {p.relative_to(app).as_posix() for p in app.rglob("*") if p.is_file()}
    assert {"app.py", "requirements.txt", "recordings/archive.py", "recordings_ui/shiny_app.py",
            "recordings_ui/www/ui.js", "recordings_ui/www/fonts/OFL.txt",
            "shinyreact/__init__.py", "demo/archive/README.md"} <= files
    # No FastAPI app, uvicorn entry point or Playwright helper, no bytecode, no media.
    assert not files & {"recordings_ui/app.py", "recordings_ui/__main__.py", "shinyreact/playwright.py"}
    assert not [f for f in files if "__pycache__" in f or f.endswith((".mp3", ".mp4"))]
    assert {f.split("/")[0] for f in files} == {
        "app.py", "requirements.txt", "recordings", "recordings_ui", "shinyreact", "demo"}
    assert (app / "recordings_ui/www/ui.css").read_text(encoding="utf-8") == "x{src:url(fonts/A.woff2)}"
    assert (ui / "www/ui.css").read_text(encoding="utf-8") == "x{src:url(/fonts/A.woff2)}"


def test_a_symlink_in_the_sources_stops_the_build(build_pages, tmp_path):
    # copytree would follow it, and copy whatever it points at into the public site.
    secret = tmp_path / "outside.txt"
    secret.write_text("not for the site", encoding="utf-8")
    ui = _built_ui(tmp_path)
    (ui / "www" / "fonts" / "extra.woff2").symlink_to(secret)
    app = tmp_path / "app"
    app.mkdir()
    with pytest.raises(SystemExit, match=r"symlink.*extra\.woff2"):
        build_pages.assemble(app, build_pages.demo_media(), ui=ui)
    assert not any(app.iterdir())  # refused before anything was copied


def test_a_symlink_in_the_demo_archive_stops_the_build(build_pages, tmp_path, demo_archive):
    elsewhere = tmp_path / "real-archive"
    elsewhere.mkdir()
    (demo_archive / "recordings" / "more").symlink_to(elsewhere, target_is_directory=True)
    app = tmp_path / "app"
    app.mkdir()
    with pytest.raises(SystemExit, match=r"symlink.*recordings/more"):
        build_pages.assemble(app, build_pages.demo_media(demo_archive), ui=_built_ui(tmp_path),
                             demo=demo_archive)


def test_the_pages_entry_is_demo_only_and_links_media_files(tmp_path, monkeypatch):
    real = tmp_path / "real-archive"
    real.mkdir()
    monkeypatch.setenv("RECORDINGS_ARCHIVE", str(real))
    monkeypatch.setenv("RECORDINGS_DEMO_ARCHIVE", str(real))
    monkeypatch.setenv("RECORDINGS_CONFIG", str(tmp_path / "broken.toml"))
    (tmp_path / "broken.toml").write_text("path = [unclosed", encoding="utf-8")
    try:
        entry = load("pages_app", REPO / "pages" / "app.py")
        root = runtime.archive().root
        # A fresh copy of demo/archive/, whatever the environment says.
        assert (root / "README.md").is_file() and root.resolve() != real.resolve()
        assert root.resolve() != (REPO / "demo" / "archive").resolve()
        assert runtime.media_base() == "../media/"
        from recordings_ui.shiny_app import app

        assert entry.app is app
    finally:
        runtime.configure(None)
