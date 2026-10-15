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
        'allowed_hosts = ["Homelab.Tailnet.ts.net", "100.64.0.1"]\n',
        encoding="utf-8",
    )
    s = from_env(
        {
            "RECORDINGS_CONFIG": str(cfg),
            "RECORDINGS_ALLOWED_HOSTS": " extra.example, Other.example ,,",
        }
    )
    assert s.allowed_hosts == LOCAL | {
        "my-homelab",
        "homelab.tailnet.ts.net",
        "100.64.0.1",
        "extra.example",
        "other.example",
    }


def test_without_base_url_or_lists_only_the_local_names_are_allowed(tmp_path):
    assert (
        from_env(
            {"RECORDINGS_ARCHIVE": str(tmp_path), "RECORDINGS_CONFIG": str(_empty_config(tmp_path))}
        ).allowed_hosts
        == LOCAL
    )


def test_demo_allows_the_local_names_and_the_environment_never_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[server]\nbase_url = "http://my-homelab"\nallowed_hosts = ["x"]\n', encoding="utf-8"
    )
    s = from_env({"RECORDINGS_CONFIG": str(cfg), "RECORDINGS_ALLOWED_HOSTS": "demo-box"}, demo=True)
    assert s.allowed_hosts == LOCAL | {"demo-box"}


def test_a_bad_allowed_hosts_stops_the_app_with_the_reason(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{tmp_path}"\n[server]\nallowed_hosts = "my-homelab"\n',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="allowed_hosts"):
        from_env({"RECORDINGS_CONFIG": str(cfg)})


def _empty_config(tmp_path):
    path = tmp_path / "empty.toml"
    path.write_text("", encoding="utf-8")
    return path


def test_recordings_allowed_hosts_entries_are_checked_too():
    import pytest
    from recordings_ui.settings import env_hosts

    assert env_hosts({"RECORDINGS_ALLOWED_HOSTS": "A.example, b.example"}) == {
        "a.example",
        "b.example",
    }
    with pytest.raises(SystemExit, match="RECORDINGS_ALLOWED_HOSTS"):
        env_hosts({"RECORDINGS_ALLOWED_HOSTS": "host:8000"})


def test_demo_mode_serves_no_archive_but_the_demo(tmp_path, demo_archive):
    import json
    import shutil

    import pytest
    from recordings.sentinel import read_sentinel
    from recordings_ui.settings import DEMO_UUID, from_env

    # why: spec §17. RECORDINGS_DEMO_ARCHIVE could name the real archive, which demo mode would
    # copy to the temp folder and serve.
    assert read_sentinel(demo_archive).uuid == DEMO_UUID
    other = tmp_path / "other"
    shutil.copytree(demo_archive, other)
    sentinel = json.loads((other / "archive.json").read_text(encoding="utf-8"))
    sentinel["uuid"] = "11111111-2222-4333-8444-555555555555"
    (other / "archive.json").write_text(json.dumps(sentinel), encoding="utf-8")
    for path in (other, tmp_path):  # another archive's UUID, and no archive.json at all
        with pytest.raises(SystemExit, match="not the demo archive"):
            from_env({"RECORDINGS_DEMO_ARCHIVE": str(path)}, demo=True)
    assert from_env({"RECORDINGS_DEMO_ARCHIVE": str(demo_archive)}, demo=True).demo is True


def test_base_urls_host_is_checked_like_an_allowed_hosts_entry(tmp_path):
    import pytest
    from recordings_ui.settings import from_env

    # why: browsers send the canonical form in Host, so that is what the allow-list must hold; and
    # a name the other lists refuse (an underscore) should stop the app the same way.
    def settings_for(url):
        cfg = tmp_path / "config.toml"
        cfg.write_text(
            f'[archive]\npath = "{tmp_path}"\n[server]\nbase_url = "{url}"\n', encoding="utf-8"
        )
        return from_env({"RECORDINGS_CONFIG": str(cfg)})

    assert "fd7a::1" in settings_for("http://[fd7a:0:0::1]:8000").allowed_hosts
    with pytest.raises(SystemExit, match="base_url"):
        settings_for("http://my_host:8000")
