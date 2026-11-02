"""The buckets as a property (stage 2b spec §20 criterion 1, §21; C1.01): over random fake
accounts (repeats across pages, both ID forms, consent and keep-out rows, late keep-outs and late
consent listings already here, rows kept out before, released, under review, parked or failing,
and partial listings), every listed ID lands in exactly one bucket by §11.3's precedence, and both
equations add up. Seeded, so a failure names its seed and replays; the count stays modest so
`make check` stays quick. Every ID and title is synthetic."""

import random
from datetime import UTC, datetime, timedelta

import pytest
from fake_plaud import server_error
from recordings.plaud.client import LIST_PATH, PlaudAuthError, PlaudClient
from recordings.plaud.compare import (
    BUCKETS,
    EXCLUDED,
    HERE,
    MISSING,
    WAITING,
    Conditions,
    Here,
    classify,
    compare,
)
from recordings.plaud.keepout import keep_out_match, parse_consent
from recordings.sources.plaud import TOO_MANY_MALFORMED, PlaudSource

T0 = datetime(2026, 11, 2, 14, 20, tzinfo=UTC)
WORDS = ("noarchive", "keepaway")
SEEDS = range(40)


class Tokens:
    def access_token(self) -> str:
        return "synthetic-access-1"

    def refresh(self, rejected: str) -> str:
        raise PlaudAuthError()


def _row(pid: str, **fields):
    from recordings.plaud.checks import ListingRow

    base = {
        "canonical_plaud_id": pid,
        "listed_id": pid,
        "first_seen": T0 - timedelta(days=3),
        "last_seen": T0 - timedelta(days=1),
        "absent_since": None,
        "kept_out_at": None,
        "kept_out_word": None,
        "rekey_of": None,
        "released_at": None,
        "needs_review": False,
        "import_failures": 0,
        "next_import_at": None,
        "parked_at": None,
        "title_sha256": None,
        "duration_ms": None,
        "change_marker": None,
    }
    base.update(fields)
    return ListingRow(**base)


def _account(rng: random.Random, plaud_server):
    account = plaud_server.account(form=rng.choice(("of_", "hex")))
    count = rng.randint(0, 45)
    here: dict[str, frozenset[str]] = {}
    consented, rows = [], {}
    for n in range(count):
        title = rng.choice(
            ["Synthetic meeting", "Private: synthetic", "noarchive: x", "Keep-Away y", "Hi"]
        )
        title = None if rng.random() < 0.02 else title  # no title field: a malformed row
        other = rng.random() < 0.03
        rec = account.add(
            title,
            untitled=title is None,
            other_id=f"synthetic-other-{n}" if other else None,
        )
        pid = rec.other_id or rec.plaud_id
        roll = rng.random()
        if roll < 0.35:  # in the archive; one recording may hold two Plaud IDs
            owner = rng.choice([*{r for s in here.values() for r in s}, f"rec-{n:03d}"])
            here[pid] = frozenset({owner})
        if rng.random() < 0.15:
            consented.append(pid)
        if rng.random() < 0.4:
            kept = rng.random() < 0.15
            released = kept and rng.random() < 0.4
            rows[pid] = _row(
                pid,
                kept_out_at=T0 - timedelta(days=2) if kept else None,
                kept_out_word="noarchive" if kept else None,
                released_at=T0 - timedelta(days=1) if released else None,
                rekey_of=("f" * 32 if rng.random() < 0.08 else None),
                needs_review=rng.random() < 0.1,
                parked_at=T0 if rng.random() < 0.05 else None,
                import_failures=rng.randint(0, 4),
            )
        if rng.random() < 0.1:
            account.twice.add(rec.plaud_id)  # a repeat across pages
    for n in range(rng.randint(0, 4)):  # in the archive, never listed now
        here[f"{0xABC0000 + n:032x}"] = frozenset({f"absent-{n}"})
    owners = sorted({r for s in here.values() for r in s})
    gone = frozenset(rng.sample(owners, k=min(2, len(owners))))
    if rng.random() < 0.1:
        text = "- `" + "a" * 31 + "` — a typo: a consent stop\n"
    else:
        text = "".join(f"- `{p}` — synthetic\n" for p in consented) or "- `" + "e" * 32 + "`\n"
    return account, Here(by_id=here, gone=gone), parse_consent(text), rows


def _expected(listed, *, here, consent, rows, conditions):
    """§11.3's precedence, written out again from the spec."""
    pid = listed.canonical_id
    if pid in here.by_id:
        return HERE
    stop = consent.stop is not None
    if pid in consent.ids:
        return EXCLUDED
    row = rows.get(pid)
    released = row is not None and row.released_at is not None
    if keep_out_match(listed.title, WORDS) is not None:
        return EXCLUDED
    if row is not None and not released and (row.kept_out_at or row.rekey_of):
        return EXCLUDED
    blocked = (
        not conditions.gate_open
        or stop
        or conditions.held
        or conditions.disk_full
        or not conditions.schedule_on
        or not conditions.auto_import
        or (row is not None and (row.needs_review or row.parked_at or row.import_failures >= 3))
    )
    return MISSING if blocked else WAITING


@pytest.mark.parametrize("seed", SEEDS)
def test_every_listed_id_is_in_exactly_one_bucket_and_the_equations_add_up(seed, plaud_server):
    rng = random.Random(seed)
    account, here, consent, rows = _account(rng, plaud_server)
    page_size = rng.choice((10, 25, 100))
    account.clamp = rng.choice((None, 10, 7))
    partial_at = rng.choice((None, None, None, 2))
    if partial_at is not None:
        plaud_server.fault(
            lambda p, q: p == LIST_PATH and q.get("page") == [str(partial_at)], server_error()
        )
    source = PlaudSource(PlaudClient(Tokens(), api_base=plaud_server.api_url), page_size=page_size)
    listing = source.list_all()
    served = {r["id"] for r in account.rows() if "id" in r}
    if not listing.complete:
        # A partial listing feeds privacy only (§11.2): its rows are real, and nothing is
        # classified from it.
        assert listing.status in ("partial", "bad")
        assert partial_at is not None or listing.why == TOO_MANY_MALFORMED
        assert {r.listed_id for r in listing.rows} <= served
        return
    assert {r.listed_id for r in listing.rows} == served
    assert listing.repeats == len(account.twice)
    conditions = Conditions(
        gate_open=rng.random() < 0.8,
        held=rng.random() < 0.15,
        ids_changed=rng.random() < 0.5,
        schedule_on=rng.random() < 0.85,
        auto_import=rng.random() < 0.85,
        disk_full=rng.random() < 0.1,
    )
    classified = classify(
        listing.rows,
        here=here,
        consent=consent,
        words=WORDS,
        listing=rows,
        keyed_out={},
        patterns=(r"^\s*private",),
        conditions=conditions,
    )
    result = compare(classified, at=T0, here=here, consent=consent, conditions=conditions)
    ids = [r.canonical for r in result.rows]
    assert len(ids) == len(set(ids)) == len(listing.rows)  # each once, repeats counted once
    for row in result.rows:
        assert row.bucket in BUCKETS
        want = _expected(row.listed, here=here, consent=consent, rows=rows, conditions=conditions)
        assert row.bucket == want, (seed, row.canonical)
        if row.bucket == EXCLUDED and row.canonical in consent.ids:
            assert row.listed.title is None  # dropped before anything else reads the row
        if row.title_shown:
            assert row.bucket in (WAITING, MISSING) and consent.stop is None
            assert not (conditions.held and conditions.ids_changed)
    e = result.equation
    assert e["listed"] == e["here"] + e["waiting"] + e["excluded"] + e["missing"]
    a = result.archive
    assert a["from_plaud"] == a["here"] + a["gone"] + a["absent"]
    assert a["from_plaud"] == len(here.recordings)
    assert result.consent["on_plaud"] == len(consent.ids & set(ids))
