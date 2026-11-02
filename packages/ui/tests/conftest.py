import asyncio
import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from recordings.archive import Archive, RawSource
from recordings.index import Index
from recordings.init import init_archive
from recordings.models import TagRef
from recordings.state import State
from recordings.writer import Incoming, Writer

REPO = Path(__file__).resolve().parents[3]
DEMO_ARCHIVE = REPO / "demo" / "archive"


@pytest.fixture(autouse=True)
def fresh_reactive_lock():
    """shiny's reactive lock is one asyncio.Lock per process, and a Lock binds to the first event
    loop it is contended on (shiny's own docstring on ReactiveEnvironment.lock says so). anyio
    gives every test its own loop, so without this a test that contends the lock fails once any
    earlier test has (test_reactive_lock.py). shiny makes the lock lazily, so dropping it makes a
    new one."""
    from shiny.reactive import _core

    _core._reactive_environment._lock = None
    yield
    _core._reactive_environment._lock = None


@pytest.fixture
def demo_archive(tmp_path) -> Path:
    """A fresh copy, so no test can change the committed demo."""
    dst = tmp_path / "archive"
    shutil.copytree(DEMO_ARCHIVE, dst)
    return dst


@pytest.fixture(scope="session")
def demo_ids() -> dict[str, str]:
    aliases = json.loads((REPO / "demo" / "canned" / "aliases.json").read_text())
    return {slug: rid for rid, slug in aliases.items()}


@pytest.fixture
def writer(tmp_path) -> Writer:
    """A new, empty archive and its Writer, in folders of its own, so a test can also take
    demo_archive (which is tmp_path/"archive")."""
    root, state = tmp_path / "written-archive", tmp_path / "written-state"
    init_archive(root, state, writer_id="test")
    return Writer(
        root, State.open(state), Index(tmp_path / "written-index" / "index.db"), writer_id="test"
    )


T0 = datetime.fromisoformat("2026-10-06T14:00:00-07:00")
# slug: (minutes after T0, tags as (tag, by)). Invented titles, synthetic media.
TAGGED: dict[str, tuple[int, list[tuple[str, str]]]] = {
    "plain": (0, [("talks", "you")]),
    "private": (1, [("private", "you")]),
    "journal": (2, [("private/journal", "you"), ("private", "you")]),
    "untagged": (3, []),
    "noted": (4, [("notes/lecture", "auto"), ("school/course-101", "you")]),
    "excluded": (5, [("talks", "you")]),
}


@dataclass
class TagArchive:
    """A synthetic archive for the tagging tests, made through a Writer, and the folders an
    ArchiveStore opens its own Writer on (tagging spec §9)."""

    root: Path
    state: Path
    index: Path
    ids: dict[str, str] = field(default_factory=dict)

    def open_writer(self, *, lock_timeout: float = 2.0, writer_id: str = "test") -> Writer:
        return Writer(
            self.root,
            State.open(self.state),
            Index(self.index),
            writer_id=writer_id,
            lock_timeout=lock_timeout,
        )

    def tags(self, slug: str) -> list[str]:
        return [t.tag for t in Archive(self.root).load(self.ids[slug]).tags]


@pytest.fixture
def tag_archive(tmp_path) -> TagArchive:
    made = TagArchive(tmp_path / "tag-archive", tmp_path / "tag-state", tmp_path / "tag-index.db")
    init_archive(made.root, made.state, writer_id="test")
    writer = made.open_writer(lock_timeout=30.0)  # setup only; each store opens its own
    media = tmp_path / "tag-media"
    media.mkdir()
    for slug, (minutes, tags) in TAGGED.items():
        path = media / f"{slug}.mp3"
        path.write_bytes(f"ID3 synthetic audio {slug}".encode())
        incoming = Incoming(
            media=path,
            recorded_at=T0 + timedelta(minutes=minutes),
            timezone_name="America/Vancouver",
            time_source="metadata",
            title=f"Week {minutes}",
            kind="audio",
            sources=(),
            tags=tuple(TagRef(tag=t, by=b) for t, b in tags),
            excluded_note_types=("summary",) if slug == "excluded" else (),
        )
        made.ids[slug] = writer.add(incoming).recording.id
    return made


@pytest.fixture(params=["archive", "memory"])
def store(request, tag_archive):
    """Each store the tag service runs on (tagging spec §5), so a test that takes it runs against
    both. Imported here, not at the top: this conftest must load before tagging.py exists."""
    from recordings_ui.tagging import ArchiveStore, MemoryStore

    if request.param == "archive":
        made = ArchiveStore(Archive(tag_archive.root), tag_archive.open_writer)
    else:
        made = MemoryStore(Archive(tag_archive.root))
    yield made
    made.close()


@pytest.fixture
def run():
    """Runs a coroutine to its end: the tag tests drive the async service without an async
    plugin. A module helper, which can't take a fixture, calls asyncio.run itself."""
    return asyncio.run


PLAUD_T0 = datetime(2026, 10, 23, 14, 20, tzinfo=UTC)


@dataclass
class PlaudScene:
    """A synthetic archive with a state.db holding the 2b tables, and the views' settings for it
    (2b phase 1, Task 19). Rows go in through PlaudDB and the flags module, as the worker writes
    them; recordings through the Writer. Synthetic IDs and titles only."""

    writer: Writer
    tmp: Path
    schedule_minutes: int = 20
    auto_import: bool = True
    keep_out_word_ok: bool = True
    added: dict[int, str] = field(default_factory=dict)

    @property
    def db(self):
        from recordings.plaud.checks import PlaudDB

        return PlaudDB(self.writer.state)

    @property
    def settings(self):
        from recordings_ui.plaud import PlaudViewSettings

        return PlaudViewSettings(
            state_path=self.writer.state.path,
            timezone_name="UTC",
            schedule_minutes=self.schedule_minutes,
            full_sweep_days=7,
            auto_import=self.auto_import,
            keep_out_word_ok=self.keep_out_word_ok,
            demo_view=None,
        )

    @staticmethod
    def plaud_id(n: int) -> str:
        return "of_" + f"{n:032x}"  # synthetic

    def add(self, n: int, title: str, tags: tuple[TagRef, ...] = ()) -> str:
        """A recording whose one source is Plaud ID `n`."""
        media = self.tmp / f"plaud-{n}.mp3"
        media.write_bytes(f"ID3 synthetic plaud audio {n}".encode())
        rec = self.writer.add(
            Incoming(
                media=media,
                recorded_at=PLAUD_T0 - timedelta(days=1, minutes=n),
                timezone_name="UTC",
                time_source="metadata",
                title=title,
                kind="audio",
                sources=(
                    RawSource(
                        kind="plaud", ref=self.plaud_id(n), added_at=PLAUD_T0 - timedelta(days=1)
                    ),
                ),
                tags=tags,
            )
        ).recording
        self.added[n] = rec.id
        return rec.id

    def open_gate(self, at: datetime = PLAUD_T0 - timedelta(days=2)) -> None:
        """As the first catch-up (Phase 2) leaves it: catch_up_done_at, which opens the gate."""
        from recordings.plaud.checks import K_CATCH_UP_DONE

        self.db.put(K_CATCH_UP_DONE, at.isoformat(), now=at)

    def kept_out_late(self, n: int, at: datetime = PLAUD_T0) -> str:
        """Recording `n`, kept out by title after it came in, as Tasks 8 and 9 leave it: its
        listing row kept out, an open flag, and the marker's `retagged-private` note."""
        from recordings.plaud.flags import record_late_keep_out

        rid = self.added[n]
        record_late_keep_out(self.db, rid, [self.plaud_id(n)], "noarchive", now=at)
        self.db.add_event(
            "retagged-private",
            now=at,
            recording_id=rid,
            plaud_id=self.plaud_id(n),
            detail={"why": "kept out by title"},
        )
        return rid


@pytest.fixture
def plaud_scene(writer, tmp_path) -> PlaudScene:
    return PlaudScene(writer, tmp_path)
