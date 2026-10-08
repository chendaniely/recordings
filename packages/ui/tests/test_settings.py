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


LOCAL = {"localhost", "127.0.0.1", "::1"}


def test_settings_default_to_the_local_names(tmp_path):
    from recordings_ui.settings import Settings

    assert Settings(archive=tmp_path, demo=True).allowed_hosts == LOCAL


def test_allowed_hosts_are_local_plus_base_url_config_and_environment(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{tmp_path}"\n'
        '[server]\nbase_url = "http://My-Homelab:8000"\n'
        'allowed_hosts = ["Homelab.Tailnet.ts.net", "100.64.0.1"]\n', encoding="utf-8")
    s = from_env({"RECORDINGS_CONFIG": str(cfg),
                  "RECORDINGS_ALLOWED_HOSTS": " extra.example, Other.example ,,"})
    assert s.allowed_hosts == LOCAL | {"my-homelab", "homelab.tailnet.ts.net", "100.64.0.1",
                                       "extra.example", "other.example"}


def test_without_base_url_or_lists_only_the_local_names_are_allowed(tmp_path):
    assert from_env({"RECORDINGS_ARCHIVE": str(tmp_path),
                     "RECORDINGS_CONFIG": str(_empty_config(tmp_path))}).allowed_hosts == LOCAL


def test_demo_allows_the_local_names_and_the_environment_never_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[server]\nbase_url = "http://my-homelab"\nallowed_hosts = ["x"]\n',
                   encoding="utf-8")
    s = from_env({"RECORDINGS_CONFIG": str(cfg), "RECORDINGS_ALLOWED_HOSTS": "demo-box"}, demo=True)
    assert s.allowed_hosts == LOCAL | {"demo-box"}


def test_a_bad_allowed_hosts_stops_the_app_with_the_reason(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[archive]\npath = "{tmp_path}"\n[server]\nallowed_hosts = "my-homelab"\n',
                   encoding="utf-8")
    with pytest.raises(SystemExit, match="allowed_hosts"):
        from_env({"RECORDINGS_CONFIG": str(cfg)})


def _empty_config(tmp_path):
    path = tmp_path / "empty.toml"
    path.write_text("", encoding="utf-8")
    return path
