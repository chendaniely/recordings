"""Stage 2a end to end, in a temporary folder (spec §20). Through the CLI, in the runbook's order:
init, import a synthetic audio-router archive (a dry run first), validate, back up to a local
restic repository, run the restore test after the import, and doctor."""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from recordings.archive import Archive
from recordings.cli import main
from recordings.models import is_private
from recordings.ops import status_summary
from recordings.state import State

REPO = Path(__file__).resolve().parents[3]
A, B, C, D, O, P = "a" * 32, "b" * 32, "c" * 32, "d" * 32, "9" * 32, "e" * 32
UUID = "11111111-1111-4111-8111-111111111111"
# No report may carry a title (Review Focus 4), whatever its script. A private-tier export keeps its
# export name, which is a title too.
TITLES = ("A PRIVATE TITLE", "Week 4, edited", "会議メモ / Q&A", "A PRIVATE EXPORT")
# Read at import, before any fixture strips RECORDINGS_* from the environment.
RESTIC = os.environ.get("RECORDINGS_TEST_RESTIC")
REQUIRE_RESTIC = os.environ.get("RECORDINGS_REQUIRE_RESTIC") == "1"


def restic_or_skip() -> str:
    for candidate in (RESTIC, str(REPO / ".cache" / "bin" / "restic"), shutil.which("restic")):
        if candidate and Path(candidate).is_file():
            return candidate
    if REQUIRE_RESTIC:  # CI sets it (Task 11): there, a missing restic fails the test
        pytest.fail("RECORDINGS_REQUIRE_RESTIC=1, but restic is not installed: run `make restic`")
    pytest.skip("restic is not installed: `make restic` puts the pinned one in .cache/bin")


def setup(tmp_path, monkeypatch, ar, plaud_env, restic="restic"):
    ar.plaud(A, envelopes=[plaud_env(A), plaud_env(A, name="Week 4, edited")])
    ar.plaud(B, envelopes=[plaud_env(B, name="会議メモ / Q&A")],
             same_audio_as=f"plaud/audio/2026/08/{A}.wav", dup_of=f"plaud/{A}")
    ar.plaud(C, envelopes=[plaud_env(C)], audio=False)
    ar.plaud(D, envelopes=[plaud_env(D)], recorded_at="2026-08-03T11:00:00-07:00")
    ar.recorder()
    ar.pocket(UUID)
    # A main-tier row that audio-router's own catalog tags private (the council's Task 8 blocker).
    ar.plaud(P, envelopes=[plaud_env(P, name="A PRIVATE TITLE")],
             recorded_at="2026-08-04T15:00:00-07:00", tags=("private",))
    ar.private(name="A PRIVATE EXPORT")  # the private tier: matched by SHA-256, tagged therapy
    ar.orphan(O)  # audio, but no snapshot and no time: deferred to 2b
    ar.ledger_only = ["f" * 32]
    ar.write_catalog()
    (tmp_path / "consent.md").write_text(f"- {D}\n", encoding="utf-8")  # never mirror D
    (tmp_path / "pw").write_text("an end-to-end password\n", encoding="utf-8")
    (tmp_path / "deploy.env").write_text("RECORDINGS_PORT=8000\n", encoding="utf-8")
    (tmp_path / "scratch").mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{tmp_path / "archive"}"\nwriter_id = "homelab"\n'
        f'[state]\npath = "{tmp_path / "state"}"\n'
        f'[index]\npath = "{tmp_path / "index" / "index.db"}"\n'
        f'[plaud]\nconsent_list = "{tmp_path / "consent.md"}"\n'
        f'[backup]\nrepository = "{tmp_path / "repo"}"\nrestic = "{restic}"\n'
        f'extra_paths = ["{tmp_path / "deploy.env"}"]\nrestore_scratch = "{tmp_path / "scratch"}"\n'
        f'cache_dir = "{tmp_path / "cache"}"\n', encoding="utf-8")
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    monkeypatch.setenv("RESTIC_PASSWORD_FILE", str(tmp_path / "pw"))
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX", "RECORDINGS_ROLE",
                 "RESTIC_PASSWORD", "RESTIC_REPOSITORY"):
        monkeypatch.delenv(name, raising=False)


def run(capsys, *argv) -> tuple[int, dict]:
    code = main([*argv, "--json"])
    out = capsys.readouterr().out
    assert [t for t in TITLES if t in out] == [], f"`recordings {' '.join(argv)}` printed a title"
    return code, json.loads(out)


def test_init_import_and_validate(tmp_path, monkeypatch, capsys, ar_archive, plaud_env):
    setup(tmp_path, monkeypatch, ar_archive, plaud_env)
    assert run(capsys, "init")[0] == 0
    code, dry = run(capsys, "import-audio-router", str(ar_archive.root), "--dry-run")
    assert code == 0 and dry["unplaced"] == []
    assert sum(dry["by_disposition"].values()) == dry["catalog_rows"] == len(ar_archive.rows)
    assert dry["by_disposition"]["consent-excluded"] == 1 and D not in json.dumps(dry)
    assert dry["by_disposition"]["new-private"] == 2
    assert dry["deferred_to_2b"] == [O]  # its Plaud ID is kept, for 2b's sync
    assert dry["unprobed"] == []  # every fixture is a plain WAV
    code, done = run(capsys, "import-audio-router", str(ar_archive.root))
    assert code == 0 and done["imported"]["created"] == 5 and done["imported"]["failed"] == []
    recordings = list(Archive(tmp_path / "archive").iter_recordings())
    assert len(recordings) == 5 and sum(is_private(r) for r in recordings) == 2
    assert run(capsys, "validate", str(tmp_path / "archive"), "--deep") == (0, {"problems": []})
    code, again = run(capsys, "import-audio-router", str(ar_archive.root))
    assert code == 0 and again["imported"]["created"] == 0
    assert again["imported"]["sources_added"] == 0
    code, reindexed = run(capsys, "reindex")
    assert code == 0 and reindexed["recordings"] == 5 and reindexed["plaud"]["written"] == 0


def test_back_up_and_prove_the_restore(tmp_path, monkeypatch, capsys, ar_archive, plaud_env):
    exe = restic_or_skip()
    setup(tmp_path, monkeypatch, ar_archive, plaud_env, restic=exe)
    assert run(capsys, "init")[0] == 0
    assert run(capsys, "backup", "init")[0] == 0
    assert run(capsys, "backup", "run", "--tag", "pre-import")[1]["ok"] is True
    assert run(capsys, "import-audio-router", str(ar_archive.root))[0] == 0
    assert run(capsys, "backup", "run", "--tag", "post-import")[1]["ok"] is True
    code, restored = run(capsys, "backup", "restore-test")  # after the import, as the runbook does
    assert code == 0 and restored["detail"] == "restored and validated 5 recordings"
    state = State.open(tmp_path / "state")
    assert state.last_run("restore-test", ok=True) is not None
    assert not any((tmp_path / "scratch").iterdir())
    # Status (spec §12.5) reads what the runs just recorded, as the web service does: read-only.
    summary = status_summary(State.read_only(tmp_path / "state"), archive_root=tmp_path / "archive",
                             now=datetime.now(timezone.utc), timezone_name="UTC", warn=0.0, stop=0.0,
                             jobs=("disk", "backup", "restore-test"))
    jobs = {j["job"]: j for j in summary["jobs"]}
    assert jobs["backup"]["last_ok"] and jobs["backup"]["last_ok_detail"].startswith("snapshot ")
    assert jobs["restore-test"]["last_ok_detail"] == "restored and validated 5 recordings"
    assert summary["attention"] == [] and summary["running"] is None
    code, report = run(capsys, "doctor", "--role", "all")
    assert code == 0 and report["problems"] == []
