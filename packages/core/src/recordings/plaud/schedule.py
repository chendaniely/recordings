"""Edit-sync's schedule (stage 2b spec §14.2, §14.5, §11.7's companion check; area B1): when each
recording here is checked next, and which recordings a run checks.

Pure functions over `plaud_checks` rows (`checks.CheckRow`), with the time given, so a test can run
a thousand recordings through days of runs on an injected clock (§21). The check
(`plaud.editsync`) and the run's sweep (`plaud.sweep`) call them; nothing here reads or writes.

**The interval** (§14.2):
- **Unsettled** (no complete transcript with at least one Plaud note, and recorded less than 48
  hours ago): `schedule_minutes` for its first 2 hours after import, then 1 hour. A recording
  imported long after it was made is settled at once. Once settled, it stays settled.
- **Settled:** an unchanged check doubles the interval, capped at `full_sweep_days`.
- **After a change:** `schedule_minutes` for two more checks, then the smaller of
  `full_sweep_days` and 6 hours, then doubling again (`fast_checks_left` counts them down).
- **Failures** back off separately: `schedule_minutes`, doubling per failure in a row, with the
  same cap. The success interval is kept for when one works again.
- A degraded check (§14.1) fetched the envelope, so it is scheduled as an unchanged one, and ends
  a failure streak: it is "checked" (a guess: the spec says only that it counts as checked).

**A run's checks** (`plan`): every recording the sweep covers whose next check is due. Those due
because they are unsettled, or because this run's listing marked them (a changed title hash or
duration sets `next_check_at` to the listing's time, §14.2: `compare.Compare.marked`), are
extra; the rest,
the cold sweep, is taken most overdue first up to the cap: ceil(1.5 × N / (`full_sweep_days` ×
runs per day)), at least 1, with N the recordings the sweep covers and runs per day 1,440 /
`schedule_minutes` (20 minutes when the schedule is off). After a 429 the cap halves, then grows
back by one per good run (§10.2; `sweep.K_SWEEP`).

**The companion check** (§11.7, P4): a run that retries a recording on the failing list also checks
at least one that isn't on it, the most overdue the sweep covers, due or not, and checks it first.
Its outcome tells the recording's failure from the sync's.

**Seeding** (§14.5): a baseline sets a recording's last check to its newest snapshot's fetch time,
spreads its next check evenly over `full_sweep_days`, and excuses that check from the breaker
(`baseline_pending`).
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from datetime import datetime, timedelta
from typing import Any, Literal

from recordings.plaud.checks import FAILING_AFTER, CheckRow
from recordings.plaud.settings import DEFAULT_SCHEDULE_MINUTES

UNSETTLED_FOR = timedelta(hours=48)  # recorded more recently than this, and incomplete: unsettled
FAST_AFTER_IMPORT = timedelta(hours=2)  # an unsettled recording's checks at the schedule interval
UNSETTLED_EVERY = timedelta(hours=1)  # then hourly, until it settles
AFTER_CHANGE_FAST = 2  # checks at the schedule interval after a change
AFTER_CHANGE_STEP = timedelta(hours=6)  # then the smaller of this and full_sweep_days
CAP_FACTOR = 1.5
DAY = timedelta(days=1)

Outcome = Literal["changed", "unchanged", "degraded", "failed"]


def every(schedule_minutes: int) -> timedelta:
    """One schedule interval; with the schedule off, the default's (§9.7)."""
    return timedelta(minutes=schedule_minutes if schedule_minutes > 0 else DEFAULT_SCHEDULE_MINUTES)


def longest(full_sweep_days: int) -> timedelta:
    return timedelta(days=max(full_sweep_days, 1))


def runs_per_day(schedule_minutes: int) -> float:
    return DAY / every(schedule_minutes)


def sweep_cap(covered: int, *, full_sweep_days: int, schedule_minutes: int) -> int:
    """§14.2: ceil(1.5 × N / (full_sweep_days × runs per day)), at least 1."""
    runs = max(full_sweep_days, 1) * runs_per_day(schedule_minutes)
    return max(1, math.ceil(CAP_FACTOR * covered / runs))


def is_settled(*, recorded_at: datetime | None, complete_with_notes: bool, now: datetime) -> bool:
    """Settled: a complete transcript with at least one Plaud note, or recorded 48 hours ago or
    more (an old recording imported late is settled at once). With no time, settled: nothing
    says it is still being transcribed, and an unsettled one costs a check an hour."""
    if complete_with_notes or recorded_at is None:
        return True
    return now - recorded_at >= UNSETTLED_FOR


def unsettled_interval(
    *, now: datetime, imported_at: datetime | None, schedule_minutes: int
) -> timedelta:
    """The schedule interval for its first 2 hours after import, then 1 hour."""
    if imported_at is not None and now - imported_at < FAST_AFTER_IMPORT:
        return every(schedule_minutes)
    return UNSETTLED_EVERY


def after_check(
    row: CheckRow | None,
    outcome: Outcome,
    *,
    now: datetime,
    settled: bool,
    imported_at: datetime | None,
    schedule_minutes: int,
    full_sweep_days: int,
) -> dict[str, Any]:
    """The `plaud_checks` columns a check's outcome writes (the module's notes). `settled` is the
    check's own finding; a row already settled stays so."""
    sched, cap = every(schedule_minutes), longest(full_sweep_days)
    settled = settled or (row is not None and row.settled)
    previous = (
        timedelta(seconds=row.interval_seconds)
        if row is not None and row.interval_seconds
        else sched
    )
    fast = row.fast_checks_left if row is not None else 0
    if outcome == "failed":
        failures = (row.failures if row is not None else 0) + 1
        since = row.failing_since if row is not None and row.failing_since else now
        wait = min(sched * 2 ** min(failures - 1, 20), cap)
        return {
            "failures": failures,
            "failing_since": since,
            "next_check_at": now + wait,
            "settled": settled,
        }
    fields: dict[str, Any] = {
        "last_checked_at": now,
        "failures": 0,
        "failing_since": None,
        "settled": settled,
        "degraded": outcome == "degraded",
    }
    if outcome == "changed":
        fields["last_changed_at"] = now
        fast = AFTER_CHANGE_FAST
        interval = sched
    elif fast > 0:
        fast -= 1
        interval = sched if fast > 0 else min(cap, AFTER_CHANGE_STEP)
    else:
        interval = min(previous * 2, cap)
    if not settled:
        interval = unsettled_interval(
            now=now, imported_at=imported_at, schedule_minutes=schedule_minutes
        )
    fields |= {
        "fast_checks_left": fast,
        "interval_seconds": int(interval.total_seconds()),
        "next_check_at": now + interval,
    }
    return fields


def plan(
    rows: Sequence[CheckRow],
    *,
    covered: Collection[str],
    now: datetime,
    cap: int,
    marked: Collection[str] = (),
) -> list[str]:
    """The recordings a run checks, in order (the module's notes): the companion check first, if
    there is one; then the extra checks (unsettled, or `marked` by this run's listing); then the
    cold sweep, most overdue first, up to `cap`. Only covered recordings, each at most once."""
    mine = [r for r in rows if r.recording_id in covered]
    due = [r for r in mine if r.next_check_at is None or r.next_check_at <= now]
    listed = frozenset(marked)

    def extra(r: CheckRow) -> bool:
        return not r.settled or r.recording_id in listed

    def overdue(r: CheckRow) -> tuple:
        return (r.next_check_at is not None, r.next_check_at or now, r.recording_id)

    extras = sorted((r for r in due if extra(r)), key=overdue)
    cold = sorted((r for r in due if not extra(r)), key=overdue)[: max(cap, 0)]
    picked = [r.recording_id for r in (*extras, *cold)]
    by_id = {r.recording_id: r for r in mine}
    failing = [rid for rid in picked if by_id[rid].failures >= FAILING_AFTER]
    if failing and len(failing) == len(picked):
        others = sorted((r for r in mine if r.failures < FAILING_AFTER), key=overdue)
        if others:
            picked.insert(0, others[0].recording_id)
    return picked


def seed_fields(
    *,
    index: int,
    count: int,
    last_fetched: datetime | None,
    settled: bool,
    now: datetime,
    full_sweep_days: int,
    keep_last_checked: bool = False,
) -> dict[str, Any]:
    """A baseline's check state for the `index`-th of `count` recordings (§14.5): last checked at
    its newest snapshot's fetch time, its next check spread evenly over `full_sweep_days`, and
    that check excused from the breaker. `keep_last_checked` (a rebaseline, `baseline --all`, and
    Accept these changes) leaves a real check's time as it is."""
    span = longest(full_sweep_days)
    fields: dict[str, Any] = {
        "next_check_at": now + span * index / max(count, 1),
        "interval_seconds": int(span.total_seconds()),
        "fast_checks_left": 0,
        "baseline_pending": True,
    }
    if not keep_last_checked:
        fields |= {"last_checked_at": last_fetched, "settled": settled}
    return fields


def effective_cap(full: int, stored: object) -> int:
    """The cap after a 429 halved it (`sweep.K_SWEEP`'s `cap`), never above the full one."""
    if isinstance(stored, int) and not isinstance(stored, bool) and stored >= 1:
        return min(full, stored)
    return full


def next_cap(full: int, current: int, *, rate_limited: bool, good: bool) -> int | None:
    """§10.2: a 429 halves the cap; each good run after it grows it back by one. None: back at the
    full cap, nothing to keep."""
    if rate_limited:
        return max(1, current // 2)
    if good and current < full:
        current += 1
    return None if current >= full else current


__all__ = [
    "AFTER_CHANGE_FAST",
    "AFTER_CHANGE_STEP",
    "after_check",
    "effective_cap",
    "every",
    "is_settled",
    "longest",
    "next_cap",
    "plan",
    "seed_fields",
    "sweep_cap",
]
