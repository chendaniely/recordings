"""`recordings plaud …` (stage 2b spec §9.6). Every archive and Plaud ID here is synthetic."""

import json
import shutil
from datetime import UTC, datetime

from recordings.archive import RawSource
from recordings.cli import main
from recordings.plaud.keepout import typo_phrase

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


TITLE = "Synthetic 042: a title no command may print"


def pid(n: int) -> str:
    return f"{n:032x}"


def configure(tmp_path, monkeypatch, writer, *, consent=None, plaud: str = "") -> None:
    """config.toml for `writer`'s archive, with [plaud] consent_list naming `consent`."""
    lines = [
        f'[archive]\npath = "{writer.root}"\nwriter_id = "test"\n',
        f'[state]\npath = "{writer.state.path}"\n[index]\npath = "{writer.index.path}"\n',
        "[plaud]\n",
        f'consent_list = "{consent}"\n' if consent is not None else "",
        plaud,
    ]
    cfg = tmp_path / "config.toml"
    cfg.write_text("".join(lines), encoding="utf-8")
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))
    for name in ("RECORDINGS_ARCHIVE", "RECORDINGS_STATE", "RECORDINGS_INDEX"):
        monkeypatch.delenv(name, raising=False)


def plaud_rec(writer, make_incoming, ref: str):
    source = RawSource(kind="plaud", ref=ref, added_at=T0, payload=b'{"id": 1}')
    return writer.add(make_incoming(title=TITLE, sources=(source,))).recording


def test_plaud_consent_lists_kept_lines_by_number_and_2a_misses_by_archive_id(
    writer, make_incoming, tmp_path, monkeypatch, capsys
):
    # why: C5.10, §12.1. A kept line is listed for Dan by number, never read as an exclusion; a
    # recording 2a imported despite an `of_` exclusion is named by archive ID, marked.
    missed = plaud_rec(writer, make_incoming, f"of_{pid(0xA1)}")
    read_by_2a = plaud_rec(writer, make_incoming, pid(0xA2))
    kept = plaud_rec(writer, make_incoming, pid(0xA3))
    consent = tmp_path / "consent" / "consent-list.md"
    consent.parent.mkdir()
    consent.write_text(
        "# consent list (synthetic)\n"
        f"- `of_{pid(0xA1)}` — asked not to keep\n"
        f"- `{pid(0xA2)}` — asked not to keep\n"
        f"{pid(0xA3)} kept: she agreed later\n",
        encoding="utf-8",
    )
    configure(tmp_path, monkeypatch, writer, consent=consent)
    assert main(["plaud", "consent", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ids"] == 2 and out["stop"] is None
    assert out["review_lines"] == [4] and out["typo_lines"] == []
    assert out["on_plaud"] is None and out["on_plaud_note"] == "unknown until the first listing"
    assert out["in_archive_note"] is None
    assert (out["read_by"], out["read_ids"]) == ("this command", 2)
    assert out["in_archive"] == sorted(
        [
            {"recording_id": missed.id, "missed_by_2a": True},
            {"recording_id": read_by_2a.id, "missed_by_2a": False},
        ],
        key=lambda r: r["recording_id"],
    )
    assert kept.id not in json.dumps(out)
    assert main(["plaud", "consent"]) == 0
    text = capsys.readouterr().out
    assert "lines read as kept, not excluded (check each meant keep): 4" in text
    assert f"in the archive: {missed.id} (2a's parser missed it)" in text


def test_plaud_consent_exits_78_on_a_typo_and_names_line_numbers_only(
    writer, tmp_path, monkeypatch, capsys
):
    # why: §12.1, P8. A typo fails closed; Status, doctor and this command name the line numbers,
    # never the text.
    consent = tmp_path / "consent-list.md"
    typo = f"- `{pid(0xB1)[:31]}` — asked not to keep\n"
    consent.write_text(f"# synthetic\n- `{pid(0xB2)}` — asked not to keep\n{typo}", "utf-8")
    configure(tmp_path, monkeypatch, writer, consent=consent)
    assert main(["plaud", "consent", "--json"]) == 78
    raw = capsys.readouterr().out
    out = json.loads(raw)
    assert out["stop"] == typo_phrase([3]) and out["typo_lines"] == [3]
    assert pid(0xB1)[:31] not in raw


def test_plaud_consent_names_a_missing_list_and_exits_78(writer, tmp_path, monkeypatch, capsys):
    configure(tmp_path, monkeypatch, writer)  # no consent_list: a consent stop
    assert main(["plaud", "consent", "--json"]) == 78
    assert json.loads(capsys.readouterr().out)["stop"] == "the consent list is missing"


def test_plaud_consent_says_when_the_worker_last_read_it_and_how_many_ids(
    writer, tmp_path, monkeypatch, capsys
):
    # why: §12.1, "If Dan's edit doesn't show, the worker didn't see it": the worker's count (Task
    # 13's K_CONSENT holds the count, never the IDs) beside what the file holds now.
    from recordings.plaud.checks import K_CONSENT, PlaudDB

    consent = tmp_path / "consent-list.md"
    consent.write_text(f"- `{pid(0xC1)}` — asked not to keep\n", encoding="utf-8")
    seen = {"read_at": "2026-10-23T21:20:00+00:00", "count": 3}
    PlaudDB(writer.state).put(K_CONSENT, seen, now=datetime.now(UTC))
    configure(tmp_path, monkeypatch, writer, consent=consent)
    assert main(["plaud", "consent", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["read_at"], out["read_by"]) == ("2026-10-23 14:20", "the worker")  # Vancouver
    assert (out["ids"], out["read_ids"]) == (1, 3)  # the file changed since the worker read it
    assert main(["plaud", "consent"]) == 0
    assert "read: 2026-10-23 14:20 (by the worker: 3 IDs)" in capsys.readouterr().out


def test_no_printed_line_carries_a_title(writer, make_incoming, tmp_path, monkeypatch, capsys):
    plaud_rec(writer, make_incoming, pid(0xD1))
    consent = tmp_path / "consent-list.md"
    consent.write_text(f"- `{pid(0xD1)}` — asked not to keep\n", encoding="utf-8")
    configure(tmp_path, monkeypatch, writer, consent=consent)
    for argv in (["plaud", "consent"], ["plaud", "consent", "--json"]):
        main(argv)
        assert "Synthetic 042" not in capsys.readouterr().out


def test_plaud_consent_sees_pending_index_updates_as_the_worker_does(
    writer, make_incoming, tmp_path, monkeypatch, capsys
):
    # why: §12.1, §17.5. The dry run reads this list: a recording the worker would tag is on it.
    rec = plaud_rec(writer, make_incoming, pid(0xE1))
    with writer.locks.hold("index"), writer.index._db() as conn:
        conn.execute("DELETE FROM source_refs WHERE recording_id = ?", (rec.id,))
    writer.state.mark_index_pending(rec.id)
    consent = tmp_path / "consent-list.md"
    consent.write_text(f"- `{pid(0xE1)}` — asked not to keep\n", encoding="utf-8")
    configure(tmp_path, monkeypatch, writer, consent=consent)
    assert main(["plaud", "consent", "--json"]) == 0
    assert [r["recording_id"] for r in json.loads(capsys.readouterr().out)["in_archive"]] == [
        rec.id
    ]


def test_plaud_consent_creates_nothing(tmp_path, monkeypatch, capsys):
    # why: a reader (§9.6, "holds no lock"): State.read_only, never State.open.
    from recordings.init import init_archive

    root, state = tmp_path / "archive", tmp_path / "state"
    init_archive(root, state, writer_id="test")
    shutil.rmtree(state / "locks", ignore_errors=True)  # State.open would make it again
    consent = tmp_path / "consent-list.md"
    consent.write_text(f"- `{pid(0xE2)}` — asked not to keep\n", encoding="utf-8")
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[archive]\npath = "{root}"\nwriter_id = "test"\n[state]\npath = "{state}"\n'
        f'[plaud]\nconsent_list = "{consent}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RECORDINGS_CONFIG", str(cfg))

    def names():  # a reader makes WAL's -wal and -shm (State.read_only says so), nothing else
        return sorted(p.name for p in state.rglob("*") if not p.name.endswith(("-wal", "-shm")))

    before = names()
    assert main(["plaud", "consent", "--json"]) == 0
    capsys.readouterr()
    assert names() == before
    assert not (state / "locks").exists()
