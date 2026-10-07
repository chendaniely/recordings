import pytest

from recordings_ui.settings import from_env


def test_demo_works_on_a_fresh_copy_and_ignores_the_real_archive(tmp_path):
    real = tmp_path / "real-archive"
    real.mkdir()
    s = from_env({"RECORDINGS_ARCHIVE": str(real)}, demo=True)
    assert s.demo is True
    assert s.archive != real and s.archive.resolve() != real.resolve()
    assert (s.archive / "README.md").is_file()  # a copy of the demo archive
    second = from_env({}, demo=True)
    assert second.archive != s.archive  # every start gets its own copy


def test_demo_can_be_switched_on_by_environment():
    assert from_env({"RECORDINGS_DEMO": "1"}).demo is True


def test_demo_never_reads_config_even_a_broken_one(tmp_path):
    bad = tmp_path / "config.toml"
    bad.write_text("path = [unclosed", encoding="utf-8")
    assert from_env({"RECORDINGS_CONFIG": str(bad)}, demo=True).demo is True


def test_real_mode_reads_the_archive_from_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[archive]\npath = "{tmp_path}"\n', encoding="utf-8")
    assert from_env({"RECORDINGS_CONFIG": str(cfg)}).archive == tmp_path


def test_real_mode_needs_an_archive_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no config.toml here, whatever the repo root holds
    with pytest.raises(SystemExit, match="archive"):
        from_env({})
    assert from_env({"RECORDINGS_ARCHIVE": str(tmp_path)}).archive == tmp_path
