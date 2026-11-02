"""The rolling sweep's state (stage 2b spec §14.2, §14.5, §10.2; area B1): which recordings it
covers, seeding their check state, rebaselining, the cap a 429 halved, and which recordings a run
checks. The run's loop is `plaud.sync`'s (it joins §11.1's step "a chunk of the sweep", after the
imports); a check is `plaud.editsync`'s; the intervals are `plaud.schedule`'s.

- **Covered** (§14.2): every recording here, except those gone from Plaud or parked, and those with
  any of their Plaud IDs kept out or consent-listed (a late keep-out's or late consent listing's
  open flag, Keep it here, `plaud_listing.kept_out_at`, or the consent list in force).
- **Seeding** (§14.5): a covered recording with no `plaud_checks` row (2a's, before `plaud
  baseline`; or after `state.db` was restored or rebuilt) is seeded before the run picks: last
  checked at its newest snapshot's fetch time, its next check spread evenly over
  `full_sweep_days`, and excused from the breaker. So a lost `state.db` can't trip it.
- **Rebaselining** (`baseline --all`, and Accept these changes): every row's next check excused
  and spread again over `full_sweep_days`, its real last-checked time kept.
- **The cap** halves after a run that met a 429, and grows back by one per good run (§10.2):
  `K_SWEEP` keeps it while it is below the full cap.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from recordings.models import Recording
from recordings.plaud import schedule
from recordings.plaud.checks import CONSENT_LATE, KEPT_OUT_LATE, CheckRow, PlaudDB
from recordings.plaud.compare import Here, _kept_out
from recordings.refs import canonical_plaud_id

K_SWEEP = "sweep"  # {"cap": int, "at": iso}: the cap after a 429 halved it (§10.2)


def plaud_ids_of(rec: Recording) -> frozenset[str]:
    return frozenset(canonical_plaud_id(s.ref) for s in rec.sources if s.kind == "plaud")


def covered(
    here: Here,
    rows: Mapping[str, CheckRow],
    db: PlaudDB,
    *,
    consent_ids: Collection[str],
) -> frozenset[str]:
    """The recordings the sweep covers (the module's notes), from the run's `here`."""
    flagged = {
        e.recording_id
        for e in db.open_flags((KEPT_OUT_LATE, CONSENT_LATE))
        if e.recording_id is not None
    }
    listing = db.listing_rows()
    out = set()
    for rid, pids in here.ids_of().items():
        row = rows.get(rid)
        if rid in here.gone or rid in flagged:
            continue
        if row is not None and (
            row.gone_since is not None or row.parked_at is not None or row.kept_here_at is not None
        ):
            continue
        if set(pids) & set(consent_ids) or any(_kept_out(listing.get(pid)) for pid in pids):
            continue
        out.add(rid)
    return frozenset(out)


def _newest_fetch(rec: Recording) -> datetime | None:
    times = [s.fetched_at or s.added_at for s in rec.sources if s.kind == "plaud"]
    return max(times) if times else None


def _seed_one(rec: Recording, *, now: datetime) -> tuple[datetime | None, bool]:
    """(its newest snapshot's fetch time, settled) for a baseline: settled unless recorded in the
    last 48 hours (no file is read: a recent one is checked often until it settles)."""
    return _newest_fetch(rec), schedule.is_settled(
        recorded_at=rec.recorded_at, complete_with_notes=False, now=now
    )


def seed(
    db: PlaudDB,
    recordings: Iterable[Recording],
    *,
    now: datetime,
    full_sweep_days: int,
    again: bool = False,
) -> tuple[int, int]:
    """Seed these recordings' check state (§14.5), in one transaction: those with no row, and with
    `again` (`baseline --all`) every one, a row kept but its next check excused and spread again.
    Returns (seeded, already seeded or rebaselined)."""
    have = {row.recording_id: row for row in db.check_rows()}
    fresh, again_ids, already = [], [], 0
    for rec in recordings:
        if rec.id not in have:
            fresh.append(rec)
            continue
        already += 1
        if again and have[rec.id].gone_since is None:
            again_ids.append(rec.id)
    order = sorted(fresh, key=lambda r: (_newest_fetch(r) or now, r.id))
    total = len(order) + len(again_ids)
    fields: dict[str, dict] = {}
    for i, rec in enumerate(order):
        last, settled = _seed_one(rec, now=now)
        fields[rec.id] = schedule.seed_fields(
            index=i,
            count=total,
            last_fetched=last,
            settled=settled,
            now=now,
            full_sweep_days=full_sweep_days,
        )
    for j, rid in enumerate(sorted(again_ids)):
        fields[rid] = schedule.seed_fields(
            index=len(order) + j,
            count=total,
            last_fetched=None,
            settled=True,
            now=now,
            full_sweep_days=full_sweep_days,
            keep_last_checked=True,
        )
    if fields:
        db.set_checks(fields)
    return len(order), len(again_ids) if again else already


def seed_missing(
    db: PlaudDB, writer, recording_ids: Iterable[str], *, now: datetime, full_sweep_days: int
) -> int:
    """§14.5's automatic baseline: the covered recordings with no `plaud_checks` row."""
    have = {row.recording_id for row in db.check_rows()}
    missing = []
    for rid in sorted(set(recording_ids) - have):
        try:
            missing.append(writer.archive.load(rid))
        except (KeyError, OSError, ValueError):
            continue  # can't be read now: seeded at a later run
    if not missing:
        return 0
    return seed(db, missing, now=now, full_sweep_days=full_sweep_days)[0]


def rebaseline(db: PlaudDB, *, now: datetime, full_sweep_days: int) -> int:
    """Every row not gone from Plaud: its next check excused from the breaker and spread again over
    `full_sweep_days` (`baseline --all`'s check-state part; Accept these changes). Returns how
    many."""
    rows = sorted(r.recording_id for r in db.check_rows() if r.gone_since is None)
    fields = {
        rid: schedule.seed_fields(
            index=i,
            count=len(rows),
            last_fetched=None,
            settled=True,
            now=now,
            full_sweep_days=full_sweep_days,
            keep_last_checked=True,
        )
        for i, rid in enumerate(rows)
    }
    if fields:
        db.set_checks(fields)
    return len(fields)


@dataclass(frozen=True)
class Plan:
    recordings: tuple[str, ...]  # in the order the run checks them
    covered: int  # N, for the cap
    cap: int  # the cold sweep's cap this run (after a 429 halved it)
    full_cap: int


def plan(
    db: PlaudDB,
    here: Here,
    *,
    consent_ids: Collection[str],
    now: datetime,
    marked: Collection[str] = (),
    schedule_minutes: int,
    full_sweep_days: int,
    writer=None,
) -> Plan:
    """This run's checks (`schedule.plan`): covered recordings seeded first when they have no row
    (with a Writer to read them), then the extras, the cold sweep up to the cap, and the companion
    check."""
    rows = {r.recording_id: r for r in db.check_rows()}
    mine = covered(here, rows, db, consent_ids=consent_ids)
    if writer is not None and mine - set(rows):
        seed_missing(db, writer, mine, now=now, full_sweep_days=full_sweep_days)
        rows = {r.recording_id: r for r in db.check_rows()}
    full = schedule.sweep_cap(
        len(mine), full_sweep_days=full_sweep_days, schedule_minutes=schedule_minutes
    )
    stored = db.get(K_SWEEP)
    cap = schedule.effective_cap(full, stored.get("cap") if isinstance(stored, dict) else None)
    picked = schedule.plan(list(rows.values()), covered=mine, now=now, cap=cap, marked=marked)
    return Plan(tuple(picked), len(mine), cap, full)


def record_cap(db: PlaudDB, sweep: Plan, *, rate_limited: bool, good: bool, now: datetime) -> None:
    """After a run's sweep (§10.2): a 429 halves the cap, a good run grows it back by one."""
    after = schedule.next_cap(sweep.full_cap, sweep.cap, rate_limited=rate_limited, good=good)
    stored = db.get(K_SWEEP)
    if after is None:
        if stored is not None:
            db.delete(K_SWEEP)
        return
    if not isinstance(stored, dict) or stored.get("cap") != after:
        db.put(K_SWEEP, {"cap": after, "at": now.isoformat()}, now=now)


__all__ = [
    "K_SWEEP",
    "Plan",
    "covered",
    "plan",
    "rebaseline",
    "record_cap",
    "seed",
    "seed_missing",
]
