from types import SimpleNamespace

import pytest

from recordings import disk
from recordings.config import ConfigError, load_config
from recordings.disk import disk_status, disk_thresholds
from recordings.sentinel import SENTINEL


def fake_statvfs(free_percent):
    return lambda path: SimpleNamespace(
        f_bavail=int(free_percent * 10), f_frsize=1024, f_blocks=1000
    )


@pytest.mark.parametrize("free, level", [(50.0, "ok"), (19.9, "warn"), (4.9, "stop")])
def test_free_space_levels(new_archive, monkeypatch, free, level):
    root, _ = new_archive
    monkeypatch.setattr(disk.os, "statvfs", fake_statvfs(free))
    status = disk_status(root, expected_uuid=None)
    assert status.level == level and status.marker
    assert status.free_percent == pytest.approx(free, abs=0.1)


def test_a_missing_or_changed_marker_stops_everything(new_archive, monkeypatch):
    # why: §15.1. The disk guard checks the sentinel too: a full-looking empty mount point is
    # not the archive.
    root, _ = new_archive
    monkeypatch.setattr(disk.os, "statvfs", fake_statvfs(90.0))
    assert disk_status(root, expected_uuid="not-the-uuid").level == "stop"
    (root / SENTINEL).unlink()
    status = disk_status(root, expected_uuid=None)
    assert (status.level, status.marker) == ("stop", False) and "mounted" in status.message


def test_the_thresholds_come_from_config(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "[disk]\nwarn_free_percent = 30\nstop_free_percent = 10\n", encoding="utf-8"
    )
    assert disk_thresholds(load_config({"RECORDINGS_CONFIG": str(cfg_file)})) == {
        "warn": 30.0,
        "stop": 10.0,
    }
    cfg_file.write_text("[disk]\nwarn_free_percent = 5\nstop_free_percent = 10\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="stop_free_percent"):
        disk_thresholds(load_config({"RECORDINGS_CONFIG": str(cfg_file)}))
