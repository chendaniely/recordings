"""`recordings plaud …` (stage 2b spec §9.6): the sync's commands, run in the `worker` container.

The CLI there acts as the worker (§9.1): its Writer writes no docs. Every command prints counts,
IDs, line numbers and fixed phrases, never a title, and `--json` prints the same as one object.
Exit codes (§9.6): 0 ok; 1 failure; 2 partial; 69 Plaud unreachable or rate limited; 75 busy;
77 sign-in needed; 78 configuration or the consent list. `cli.py` registers `add_parser` and
dispatches to `run`; each command adds its parser block and its `COMMANDS` entry here.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import sys
from collections.abc import Callable
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from recordings import config
from recordings.archive import Archive
from recordings.index import Index
from recordings.locks import LockTimeout
from recordings.plaud import hold, runlock
from recordings.plaud.baseline import (
    DROPPED,
    INVALID_FILE,
    KeepOutWordsError,
    baseline_privacy,
    drop_word,
    enforced_words,
)
from recordings.plaud.checks import (
    K_CATCH_UP_DONE,
    K_CONSENT,
    K_LAST_GOOD_LISTING,
    NoSyncYet,
    PlaudDB,
)
from recordings.plaud.flags import listed_here, missed_by_2a
from recordings.plaud.keepout import kept_lines_note, read_consent, read_consent_text, signature
from recordings.plaud.settings import BAD_WORD, plaud_settings
from recordings.plaud.signin import TokenFacts
from recordings.plaud.when import local_hhmm, parse_stamp
from recordings.sentinel import SentinelError, read_sentinel
from recordings.state import State, StateError
from recordings.writer import Writer, WriterError

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_PARTIAL = 2  # the listing was good, but some recordings failed or aren't here
EXIT_UNREACHABLE = 69  # Plaud unreachable or rate limited
EXIT_BUSY = 75
EXIT_SIGN_IN = 77
EXIT_CONFIG = 78  # configuration, or the consent list
ON_PLAUD_UNKNOWN = "unknown until the first listing"
MISSED_BY_2A = "2a's parser missed it"
INDEX_UNAVAILABLE = "unknown: the index can't answer now (run `recordings reindex`)"


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("plaud", help="the Plaud sync's commands (run them in the worker container)")
    p.set_defaults(plaud_parser=p)
    plaud = p.add_subparsers(dest="plaud_command", metavar="<command>")

    c = plaud.add_parser(
        "consent",
        help="what the worker reads from the consent list: IDs, kept and unreadable lines, "
        "and consent-listed recordings already here",
    )
    c.add_argument("--json", action="store_true")

    c = plaud.add_parser(
        "baseline",
        help="check every title of the archive's Plaud recordings once, for privacy and the "
        "keep-out words, and seed edit-sync's check state (before the catch-up)",
    )
    c.add_argument(
        "--all",
        action="store_true",
        help="a rebaseline, after a normaliser change you accept: every recording's next check "
        "excused from the breaker and spread over full_sweep_days again",
    )
    c.add_argument("--json", action="store_true")

    c = plaud.add_parser("keep-out-words", help="list the keep-out words enforced, or drop one")
    c.add_argument("--drop", metavar="WORD", help="stop enforcing an old word")
    c.add_argument("--json", action="store_true")

    c = plaud.add_parser(
        "check",
        help="one signed-in read: whether the Plaud sign-in works, its expiry, and whether it "
        "can refresh; --volatility fetches 3 recordings twice, a minute apart, and says which "
        "parts differ (writes nothing)",
    )
    c.add_argument(
        "--volatility",
        action="store_true",
        help="fetch 3 recordings here (none private, kept out or consent-listed) twice, a "
        "minute apart, and say for each part whether it came back the same",
    )
    c.add_argument("--json", action="store_true")

    c = plaud.add_parser(
        "breaker",
        help="edit-sync's circuit breaker: whether it is open and why; `reset` closes it "
        "(Status's Accept these changes is the usual way; no runbook step needs reset)",
    )
    c.add_argument("action", nargs="?", choices=["reset"], help="close it, starting its window now")
    c.add_argument("--json", action="store_true")

    c = plaud.add_parser(
        "accept-listing",
        help="Accept this listing: clear a listing hold, as Status's button does, refused on the "
        "same terms (also before the first catch-up, for the catch-up's own listing)",
    )
    c.add_argument("--json", action="store_true")

    c = plaud.add_parser(
        "review",
        help="after restoring an older state.db: record rebuilt_at as now, so every recording "
        "made before it that isn't here waits for your review (runbook, Restoring for real)",
    )
    c.add_argument("--json", action="store_true")

    c = plaud.add_parser(
        "release",
        help="Release: let in a recording kept out by its title (or a possible re-keyed one). "
        "Shows its ID, date and duration, never its title, and asks y/N; --undo keeps it out "
        "again until a sync has fetched it",
    )
    c.add_argument("plaud_id", help="the Plaud ID, as the Sync screen's Copy ID gives it")
    c.add_argument("--undo", action="store_true", help="keep it out again (before the next sync)")

    c = plaud.add_parser(
        "sync",
        help="a sync now, with no import cap; --dry-run lists and classifies and imports nothing "
        "(the first catch-up's dry run); --catch-up runs the first import, supervised (runbook, "
        "sections 7 and 8)",
    )
    which = c.add_mutually_exclusive_group()
    which.add_argument(
        "--dry-run", action="store_true", help="list and classify, and import nothing (a Compare)"
    )
    which.add_argument(
        "--catch-up", action="store_true", help="the first import, once: holds ops and plaud"
    )
    which.add_argument(
        "--full",
        action="store_true",
        help="Sync everything: list, import what is waiting, check every recording, retry its "
        "failures once, and write the manifest; exit 0 only when everything listed is here and "
        "checked, parked, flagged or excluded (an unfinished one resumes)",
    )
    c.add_argument(
        "--list",
        action="store_true",
        help="with --dry-run: every listed ID by bucket, date and duration, never a title",
    )
    c.add_argument("--json", action="store_true")

    c = plaud.add_parser(
        "resync",
        help="Re-sync from Plaud: check these recordings now, ahead of the sweep (a parked one "
        "too: a success unparks it)",
    )
    c.add_argument("recording_ids", nargs="+", metavar="ID", help="archive IDs, up to 1,000")
    c.add_argument("--json", action="store_true")

    c = plaud.add_parser(
        "park",
        help="park recordings that fail every check: the sweep and Sync everything leave them "
        "alone (Re-sync still tries them); --undo unparks",
    )
    c.add_argument("recording_ids", nargs="+", metavar="ID", help="archive IDs")
    c.add_argument("--undo", action="store_true", help="unpark: checked again at the next sync")
    c.add_argument("--json", action="store_true")

    c = plaud.add_parser(
        "held",
        help="the removals from Plaud held for your review (a name back to Speaker N, notes "
        "gone), by recording ID and part; stage 3a's review list accepts them",
    )
    c.add_argument("--json", action="store_true")

    c = plaud.add_parser(
        "retire",
        help="DAN-15's end: the Plaud sync is retired (no scheduled sync, no monthly Sync "
        "everything), and Status says so; --undo brings it back (runbook)",
    )
    c.add_argument("--undo", action="store_true", help="the sync is no longer retired")
    c.add_argument("--json", action="store_true")

    c = plaud.add_parser(
        "token-import",
        help="install a Plaud sign-in from the vendor CLI's token file on stdin (`make "
        "plaud-sign-in` runs it; never by hand in a Claude session)",
    )
    c.add_argument("source", choices=["-"], help="-: read the token file from stdin")
    c.add_argument("--json", action="store_true")

    # No abbreviated flags, as cli.build_parser: its loop doesn't reach these subparsers.
    for each in (p, *plaud.choices.values()):
        each.allow_abbrev = False


def _emit(payload: dict, lines: list[str], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for line in lines:
            print(line)


def _fail(message: str, as_json: bool, code: int) -> int:
    if as_json:
        print(json.dumps({"error": message}, ensure_ascii=False))
    else:
        print(f"recordings: {message}", file=sys.stderr)
    return code


def _config(as_json: bool) -> config.Config | int:
    try:
        return config.load_config(os.environ)
    except config.ConfigError as exc:
        return _fail(str(exc), as_json, EXIT_CONFIG)


def _zone(cfg: config.Config) -> tzinfo:
    try:
        return ZoneInfo(cfg.default_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def _shown(when: datetime | None, tz: tzinfo) -> str | None:
    """A time as the CLI prints it: the local date, then the time as Status shows it."""
    if when is None:
        return None
    return f"{when.astimezone(tz):%Y-%m-%d} {local_hhmm(when, tz)}"


def _read_state(cfg: config.Config) -> State | None:
    if cfg.state_path is None:
        return None
    try:
        return State.read_only(cfg.state_path)  # a reader creates and migrates nothing
    except StateError:
        return None


def _here(
    cfg: config.Config, state: State | None, ids: frozenset[str]
) -> dict[str, set[str]] | None:
    """Each recording here that the list names, with the IDs that name it, as the worker finds
    them (`flags.listed_here`); None when the index can't answer (missing, another archive's,
    busy, or a pending update that can't be read)."""
    if cfg.archive_path is None or cfg.index_path is None:
        return None
    try:
        identity = read_sentinel(cfg.archive_path)
    except SentinelError:
        return None
    try:
        found, unavailable = listed_here(
            Index(cfg.index_path), identity.uuid, state, Archive(cfg.archive_path), ids
        )
    except sqlite3.Error:
        return None
    if unavailable:
        return None
    result: dict[str, set[str]] = {}
    for rid, pid in found:
        result.setdefault(rid, set()).add(pid)
    return result


def cmd_consent(args: argparse.Namespace) -> int:
    """What the worker reads from the consent list (§12.1). Holds no lock."""
    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    try:
        settings = plaud_settings(cfg)
    except config.ConfigError as exc:
        return _fail(str(exc), args.json, EXIT_CONFIG)
    tz = _zone(cfg)
    consent, text = read_consent_text(settings.consent_list)
    sig = signature(settings.consent_list)
    changed = datetime.fromtimestamp(sig.mtime_ns / 1e9, UTC) if sig is not None else None
    on_plaud: int | None = None
    read_at, read_by, read_ids = datetime.now(UTC), "this command", len(consent.ids)
    state = _read_state(cfg)
    if state is not None:
        db = PlaudDB(state)
        try:
            if db.get(K_LAST_GOOD_LISTING) is not None:
                # A listed ID with no absent_since is in the newest listing.
                rows = [db.listing(pid) for pid in consent.ids]
                on_plaud = sum(1 for r in rows if r is not None and r.absent_since is None)
            # What the worker last read (Task 13: {"read_at", "count", …}): a count that differs
            # from the file's says the worker hasn't seen Dan's edit yet (§12.1).
            seen = db.get(K_CONSENT)
            worker_read = parse_stamp(seen.get("read_at")) if isinstance(seen, dict) else None
            if worker_read is not None:
                count = seen.get("count")
                read_at, read_by = worker_read, "the worker"
                read_ids = count if isinstance(count, int) and not isinstance(count, bool) else None
        except (NoSyncYet, sqlite3.Error, ValueError):
            pass  # what the file says still prints (a value that doesn't decode included)
    missed = missed_by_2a(text, consent)
    here = _here(cfg, state, consent.ids)
    in_archive = (
        None
        if here is None
        else [
            {"recording_id": rid, "missed_by_2a": bool(pids & missed)}
            for rid, pids in sorted(here.items())
        ]
    )
    payload = {
        "ids": len(consent.ids),
        "on_plaud": on_plaud,
        "on_plaud_note": None if on_plaud is not None else ON_PLAUD_UNKNOWN,
        "file_changed": _shown(changed, tz),
        "read_at": _shown(read_at, tz),
        "read_by": read_by,
        "read_ids": read_ids,
        "stop": consent.stop,
        "typo_lines": list(consent.typo_lines),
        "review_lines": list(consent.review_lines),
        "in_archive": in_archive,
        "in_archive_note": INDEX_UNAVAILABLE if in_archive is None else None,
    }
    on = f"{on_plaud:,} on Plaud now" if on_plaud is not None else f"on Plaud: {ON_PLAUD_UNKNOWN}"
    lines = [f"consent list: {len(consent.ids):,} IDs, {on}"]
    lines.append(f"file changed: {payload['file_changed'] or 'no file'}")
    counted = "" if read_ids is None else f": {read_ids:,} {'ID' if read_ids == 1 else 'IDs'}"
    lines.append(f"read: {payload['read_at']} (by {read_by}{counted})")
    if consent.stop:
        lines.append(f"imports stopped: {consent.stop}")
    if consent.review_lines:
        lines.append(kept_lines_note(consent.review_lines))
    if in_archive is None:
        lines.append(f"in the archive: {INDEX_UNAVAILABLE}")
    for row in in_archive or ():
        note = f" ({MISSED_BY_2A})" if row["missed_by_2a"] else ""
        lines.append(f"in the archive: {row['recording_id']}{note}")
    _emit(payload, lines, args.json)
    return EXIT_CONFIG if consent.stop else EXIT_OK


def _write_state(cfg: config.Config, as_json: bool) -> State | int:
    if cfg.state_path is None:
        return _fail("set [state] path in config.toml first", as_json, EXIT_CONFIG)
    try:
        return State.open(cfg.state_path)
    except StateError as exc:
        return _fail(str(exc), as_json, EXIT_CONFIG)


def _writer(cfg: config.Config, as_json: bool) -> Writer | int:
    """The CLI's Writer in the worker container: as the worker's, it writes no docs (§9.1)."""
    try:
        return Writer.open(cfg, docs=False, lock_timeout=10.0)
    except LockTimeout as exc:
        return _fail(str(exc), as_json, EXIT_BUSY)
    except WriterError as exc:  # the Writer's own refusal, worded for display
        return _fail(str(exc), as_json, EXIT_CONFIG)
    except (OSError, sqlite3.Error) as exc:  # never the exception's text: it names paths or rows
        return _fail(
            f"can't open the archive's writer now ({type(exc).__name__}): check the archive "
            "disk and its permissions",
            as_json,
            EXIT_CONFIG,
        )


def _busy(state: State) -> str:
    """§15.6's "skipped: busy (`plaud-full`)", naming the run that holds `plaud`."""
    holder = runlock.plaud_holder(state)
    return f"skipped: busy (`{holder[0]}`)" if holder else "skipped: busy"


def _fix_for(cause: str) -> str:
    """What to do about a recording the baseline couldn't write, by its cause (decision 58)."""
    if cause in ("StaleFile", INVALID_FILE):
        return "run `recordings reindex`, then the baseline again"
    if cause == "FileNotFoundError":
        return "restore the file or run `recordings reindex`, then the baseline again"
    return "check the archive disk and its permissions, then run the baseline again"


def cmd_baseline(args: argparse.Namespace) -> int:
    """§14.5: every title checked once (the privacy part), then edit-sync's check state seeded
    for each Plaud recording with none (its last check at its newest snapshot's fetch time, its
    next spread over `full_sweep_days`, excused from the breaker); `--all` rebaselines the rest
    too. Holds `plaud`, waiting up to runlock.CLI_WAIT. Exit 1 when a recording couldn't be
    written, the enforced keep-out words can't be read (nothing is checked then), or an OSError
    or a sqlite3 error stopped the run outside its per-recording loop (a fixed phrase with the
    class, never the exception's text: §9.6); 75 when only locked ones were left."""
    from recordings.plaud import sweep

    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    try:
        settings = plaud_settings(cfg)
    except config.ConfigError as exc:
        return _fail(str(exc), args.json, EXIT_CONFIG)
    state = _write_state(cfg, args.json)
    if isinstance(state, int):
        return state
    now = datetime.now(UTC)
    try:
        with runlock.plaud_run(state, "plaud-baseline", timeout=runlock.CLI_WAIT, now=now):
            writer = _writer(cfg, args.json)
            if isinstance(writer, int):
                return writer
            report = baseline_privacy(writer, PlaudDB(writer.state), now=now)
            plaud = [
                rec
                for rec in writer.archive.iter_recordings()
                if any(s.kind == "plaud" for s in rec.sources)
            ]
            seeded, already = sweep.seed(
                PlaudDB(writer.state),
                plaud,
                now=now,
                full_sweep_days=settings.full_sweep_days,
                again=args.all,
            )
    except LockTimeout:
        return _fail(_busy(state), args.json, EXIT_BUSY)
    except KeepOutWordsError as exc:  # a fixed phrase: the row is never read as no words
        return _fail(str(exc), args.json, EXIT_FAILED)
    except (OSError, sqlite3.Error) as exc:  # never the exception's text: it names paths or rows
        return _fail(
            f"the baseline stopped ({type(exc).__name__}): check the archive disk and its "
            "permissions, then run the baseline again",
            args.json,
            EXIT_FAILED,
        )
    lines = [
        f"titles checked: {report.titles_checked:,}",
        f"private added: {report.private_added:,}",
        f"kept out late: {report.kept_out_late:,}",
        f"already seen: {report.already_seen:,}",
        f"check state seeded: {seeded:,}",
        f"rebaselined: {already:,}" if args.all else f"already seeded: {already:,}",
    ]
    by_fix: dict[str, list[str]] = {}
    for rid in report.unwritten:
        by_fix.setdefault(_fix_for(report.causes.get(rid, "")), []).append(rid)
    for fix, ids in by_fix.items():
        lines.append(f"not written ({len(ids):,}): {', '.join(ids)}; {fix}")
    if report.busy:
        lines.append(
            f"busy ({len(report.busy):,}): {', '.join(report.busy)}; in use now, run the "
            "baseline again"
        )
    payload = report.to_dict() | {"seeded": seeded}
    payload["rebaselined" if args.all else "already_seeded"] = already
    _emit(payload, lines, args.json)
    if report.unwritten:
        return EXIT_FAILED
    return EXIT_BUSY if report.busy else EXIT_OK


def cmd_keep_out_words(args: argparse.Namespace) -> int:
    """§12.2: the words enforced, or `--drop` one. Holds no lock. Listing reads state.db and
    creates nothing; only `--drop` opens it to write."""
    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    try:
        settings = plaud_settings(cfg)
    except config.ConfigError as exc:
        return _fail(str(exc), args.json, EXIT_CONFIG)
    if args.drop is not None:
        state = _write_state(cfg, args.json)
        if isinstance(state, int):
            return state
    else:
        state = _read_state(cfg)
    ok = settings.keep_out_word_ok
    payload: dict = {}
    code = EXIT_OK
    try:
        db = PlaudDB(state) if state is not None else None
        if args.drop is not None and db is not None:
            result = drop_word(
                db,
                args.drop,
                configured=settings.keep_out_word if ok else "",
                now=datetime.now(UTC),
            )
            payload["result"] = result
            code = EXIT_OK if result == DROPPED else EXIT_FAILED
        try:
            enforced = list(enforced_words(db)) if db is not None else []
        except NoSyncYet:
            enforced = []  # no 2b process has opened this state.db yet: nothing is enforced
    except (NoSyncYet, KeepOutWordsError) as exc:  # fixed phrases
        return _fail(str(exc), args.json, EXIT_FAILED)
    except (sqlite3.Error, ValueError) as exc:  # never the exception's text
        return _fail(
            f"can't use state.db now ({type(exc).__name__}): try again", args.json, EXIT_FAILED
        )
    payload |= {
        "configured": settings.keep_out_word if ok else None,
        "configured_ok": ok,
        "enforced": enforced,
    }
    lines = [payload["result"]] if "result" in payload else []
    lines.append(f"configured: {settings.keep_out_word}" if ok else f"configured: {BAD_WORD}")
    lines.append(f"enforced: {', '.join(enforced) or 'none yet'}")
    _emit(payload, lines, args.json)
    return code


def _record(cfg: config.Config, write: Callable[[PlaudDB], None]) -> str | None:
    """Write the sign-in's state (signin.py) to state.db; the error's class when it couldn't be,
    which the command says without failing: the worker's next run records it again."""
    if cfg.state_path is None:
        return "no [state] path"
    try:
        write(PlaudDB(State.open(cfg.state_path)))
    except (StateError, NoSyncYet, sqlite3.Error, OSError) as exc:
        return type(exc).__name__
    return None


def _sign_in_payload(
    facts: TokenFacts | None, account: str | None, tz: tzinfo, *, signed_in: bool
) -> tuple[dict, str]:
    """§10.5's line, "signed in: yes · access token expires 2026-10-23 16:20 · refresh token:
    present · account 1a2b3c4d", and the same as JSON. Never a token or the account's ID."""
    payload = {
        "signed_in": signed_in,
        "expires_at": _shown(facts.expires_at, tz) if facts else None,
        "refresh_token": facts.refresh_token if facts else None,
        "refresh_expires_at": _shown(facts.refresh_expires_at, tz) if facts else None,
        "account": account,
    }
    parts = [f"signed in: {'yes' if signed_in else 'no'}"]
    if facts is not None:
        parts.append(f"access token expires {payload['expires_at'] or 'unknown'}")
        refresh = "present" if facts.refresh_token else "missing"
        if facts.refresh_expires_at is not None:
            refresh += f", expires {payload['refresh_expires_at']}"
        parts.append(f"refresh token: {refresh}")
    if signed_in:
        parts.append(f"account {account}" if account else "account unknown")
    return payload, " · ".join(parts)


def cmd_check(args: argparse.Namespace) -> int:
    """§10.3: one cheap signed-in read (the current user), refreshing first when due, and what it
    found. A success ends the sign-in's pause and its blocker; a refusal records them. Exit 0, or
    77 when the sign-in is needed. Holds no lock but `tokens.lock`, for a refresh."""
    from recordings.plaud import signin, tokens  # the worker's CLI only: web never runs this
    from recordings.plaud.client import PlaudAuthError, PlaudClient, PlaudError

    if args.volatility:
        return cmd_volatility(args)
    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    try:
        settings = plaud_settings(cfg)
    except config.ConfigError as exc:
        return _fail(str(exc), args.json, EXIT_CONFIG)
    tz, now = _zone(cfg), datetime.now(UTC)
    store = tokens.TokenStore(settings.token_store, token_url=settings.token_url)
    client = PlaudClient(store, api_base=settings.api_base)
    try:
        account = tokens.check_sign_in(store, client, None, now=now, probe=True)
        facts = store.facts()
    except PlaudAuthError:
        why = "Plaud refused the sign-in" if store.exists() else "no sign-in yet"
        unrecorded = _record(cfg, lambda db: signin.record_refused(db, store.path, now=now))
        payload, line = _sign_in_payload(None, None, tz, signed_in=False)
        payload |= {"sign_in_needed": why, "not_recorded": unrecorded}
        _emit(
            payload, [f"{line} · {why}: {signin.SIGN_IN_FIX}", *_unrecorded(unrecorded)], args.json
        )
        return EXIT_SIGN_IN
    except tokens.TokenStoreUnsafe as exc:  # names the path and the chmod, never the contents
        return _fail(str(exc), args.json, EXIT_CONFIG)
    except LockTimeout as exc:
        return _fail(str(exc), args.json, EXIT_BUSY)
    except PlaudError as exc:  # an outage: it says nothing about the sign-in
        return _fail(f"Plaud couldn't be reached: {exc.message}", args.json, EXIT_UNREACHABLE)
    unrecorded = _record(cfg, lambda db: signin.record_good(db, facts, now=now))
    payload, line = _sign_in_payload(facts, account, tz, signed_in=True)
    payload["not_recorded"] = unrecorded
    _emit(payload, [line, *_unrecorded(unrecorded)], args.json)
    return EXIT_OK


VOLATILITY_COUNT = 3  # §14.4: three recordings here
VOLATILITY_WAIT = 60.0  # seconds between the two fetches of each
VOLATILE_STOP = (
    "a part came back different a minute later, with no edit: don't run the baseline or the "
    "catch-up; it needs a code fix (a Claude session, with this output)"
)


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


def cmd_volatility(args: argparse.Namespace) -> int:
    """`recordings plaud check --volatility` (§14.4): three recordings here, none private, kept
    out or consent-listed, each fetched twice a minute apart, and for each part (title,
    transcript, speakers, notes, own notes, other) whether its normalised hash came back the
    same. It writes nothing: no snapshot, no state. The runbook runs it before the baseline, so a
    volatile field is found before the breaker meets it. Recordings by archive ID, parts and field
    names only, never a title or a value. Holds `plaud` (`plaud-volatility`). Exit 0 all the same
    (or nothing to fetch); 1 a part differed; 69 Plaud unreachable; 75 busy; 77 sign-in needed;
    78 configuration or the consent list."""
    from recordings.models import is_private
    from recordings.plaud import editsync, tokens
    from recordings.plaud.checks import CONSENT_LATE, KEPT_OUT_LATE
    from recordings.plaud.client import PlaudAuthError, PlaudClient, PlaudError
    from recordings.plaud.compare import _kept_out
    from recordings.plaud.keepout import keep_out_match
    from recordings.plaud.normalise import link_errors, part_hashes
    from recordings.refs import canonical_plaud_id
    from recordings.sources.plaud import PlaudSource

    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    try:
        settings = plaud_settings(cfg)
    except config.ConfigError as exc:
        return _fail(str(exc), args.json, EXIT_CONFIG)
    consent = read_consent(settings.consent_list)
    if consent.stop is not None:
        return _fail(f"imports stopped: consent list ({consent.stop})", args.json, EXIT_CONFIG)
    state = _write_state(cfg, args.json)
    if isinstance(state, int):
        return state
    if cfg.archive_path is None:
        return _fail("set [archive] path in config.toml first", args.json, EXIT_CONFIG)
    db, archive = PlaudDB(state), Archive(cfg.archive_path)
    try:
        words = [w for w in (settings.keep_out_word,) if settings.keep_out_word_ok]
        words += [w for w in enforced_words(db) if w not in words]
        flagged = {
            e.recording_id for e in db.open_flags((KEPT_OUT_LATE, CONSENT_LATE)) if e.recording_id
        }
    except (KeepOutWordsError, NoSyncYet, sqlite3.Error) as exc:
        return _fail(f"can't read state.db now ({type(exc).__name__})", args.json, EXIT_FAILED)
    store = tokens.TokenStore(settings.token_store, token_url=settings.token_url)
    client = PlaudClient(store, api_base=settings.api_base)
    results: list[dict] = []
    try:
        with runlock.plaud_run(
            state, "plaud-volatility", timeout=runlock.CLI_WAIT, now=datetime.now(UTC)
        ):
            listing = PlaudSource(client).list_all()
            if not listing.complete and listing.error is not None:
                raise listing.error
            listed = {row.canonical_id: row for row in listing.rows}
            picked = []
            for rec in archive.iter_recordings():
                ids = editsync.plaud_ids(rec)
                row = next((listed[pid] for pid in ids if pid in listed), None)
                if (
                    row is None
                    or is_private(rec)
                    or rec.id in flagged
                    or set(ids) & consent.ids
                    or any(_kept_out(db.listing(pid)) for pid in ids)
                    or keep_out_match(row.title, words) is not None
                ):
                    continue
                picked.append((rec.id, row))
                if len(picked) == VOLATILITY_COUNT:
                    break

            def fetch(row) -> dict | None:
                """The envelope bare, then its blocks, unless its ID is consent-listed or its
                title took a keep-out word since the listing: then never its blocks."""
                env = client.envelope(row.listed_id, enrich=False)
                env_id, title = env.get("id"), env.get("name")
                if isinstance(env_id, str) and canonical_plaud_id(env_id) in consent.ids:
                    return None
                if keep_out_match(title if isinstance(title, str) else None, words) is not None:
                    return None
                return client.enrich(env)

            first = {}
            for rid, row in picked:
                if (env := fetch(row)) is not None:
                    first[rid] = (row, env)
            if first:
                _sleep(VOLATILITY_WAIT)
            for rid, (row, env) in first.items():
                if (again := fetch(row)) is not None:
                    results.append(_compared(rid, env, again, link_errors, part_hashes))
    except LockTimeout:
        return _fail(_busy(state), args.json, EXIT_BUSY)
    except PlaudAuthError as exc:
        return _fail(f"{exc.message}: {signin_fix()}", args.json, EXIT_SIGN_IN)
    except PlaudError as exc:
        return _fail(f"Plaud couldn't be reached: {exc.message}", args.json, EXIT_UNREACHABLE)
    volatile = any(r["differ"] for r in results)
    lines = [
        f"{r['recording_id']}: "
        + " · ".join(f"{p} {'differs' if p in r['differ'] else 'same'}" for p in r["parts"])
        + (f" (fields: {', '.join(r['fields'])})" if r["fields"] else "")
        + (" · a link failed: not compared" if r["inconclusive"] else "")
        for r in results
    ]
    if len(results) < VOLATILITY_COUNT:
        lines.append(f"only {len(results)} of {VOLATILITY_COUNT} could be fetched")
    lines.append(VOLATILE_STOP if volatile else "every part came back the same")
    _emit({"recordings": results, "volatile": volatile}, lines, args.json)
    return EXIT_FAILED if volatile else EXIT_OK


def _compared(rid: str, first: dict, second: dict, link_errors, part_hashes) -> dict:
    """One recording's two fetches, part by part, by §14.1's names; field names only."""
    from recordings.plaud import editsync

    a, b = part_hashes(first), part_hashes(second)
    own = editsync.own_types(first, second)
    keys = sorted(set(a) | set(b))
    differ = editsync.part_names([k for k in keys if a.get(k) != b.get(k)], own)
    inconclusive = bool(link_errors(first) or link_errors(second))
    return {
        "recording_id": rid,
        "parts": list(editsync.part_names(keys, own)),
        "differ": [] if inconclusive else list(differ),
        "fields": list(editsync.other_fields(first, second)) if "other" in differ else [],
        "inconclusive": inconclusive,
    }


def cmd_breaker(args: argparse.Namespace) -> int:
    """`recordings plaud breaker [reset]` (§14.4): whether edit-sync's breaker is open, since
    when and why (field and part names only); `reset` closes it and starts its window now,
    excusing nothing (Status's Accept these changes is the usual way, and a release that bumps
    the normaliser closes it by itself). Reset holds `plaud` (`plaud-breaker`). Exit 0; 75 busy."""
    from recordings.plaud import breaker

    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    state = _write_state(cfg, args.json)
    if isinstance(state, int):
        return state
    db, now, tz = PlaudDB(state), datetime.now(UTC), _zone(cfg)
    message = None
    try:
        if args.action == "reset":
            with runlock.plaud_run(state, "plaud-breaker", timeout=runlock.CLI_WAIT, now=now):
                message = breaker.reset(db, now=now)
        value = breaker.record(db)
    except LockTimeout:
        return _fail(_busy(state), args.json, EXIT_BUSY)
    except (NoSyncYet, sqlite3.Error) as exc:
        return _fail(f"can't use state.db now ({type(exc).__name__})", args.json, EXIT_FAILED)
    view = breaker.view(value, tz=tz)
    payload = {
        "open": view is not None,
        "since": _shown(parse_stamp(value.get("since")), tz) if view is not None else None,
        "recordings": value.get("recordings") if view is not None else None,
        "fields": view["fields"] if view is not None else [],
        "accept": view["accept"] if view is not None else False,
        "accepted": [f for f in value.get("accepted") or [] if isinstance(f, str)],
    }
    if message is not None:
        payload["result"] = message
    lines = [message] if message is not None else []
    if view is None:
        lines.append("edit-sync's breaker is closed")
    else:
        fields = f" ({', '.join(view['fields'])})" if view["fields"] else ""
        lines.append(view["sentence"] + fields)
        lines.append(
            "Accept these changes is offered on Status" if view["accept"] else breaker.CODE_FIX
        )
    if payload["accepted"]:
        lines.append(f"accepted before: {', '.join(payload['accepted'])}")
    _emit(payload, lines, args.json)
    return EXIT_OK


def _unrecorded(error: str | None) -> list[str]:
    if error is None:
        return []
    return [f"not recorded in state.db ({error}): the worker's next run records it"]


def cmd_token_import(args: argparse.Namespace) -> int:
    """§10.5 step 4: the vendor CLI's token file on stdin. Its keys are checked and only ours
    kept (§10.4); one cheap call with it, which must work; then it is installed atomically at
    0600, under `tokens.lock`. Prints only §10.5's line. Anything that fails installs nothing:
    a file it can't use, or a sign-in Plaud refuses, exits 77; Plaud unreachable, 69."""
    from recordings.plaud import signin, tokens  # the worker's CLI only: web never runs this
    from recordings.plaud.client import PlaudAuthError, PlaudClient, PlaudError

    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    try:
        settings = plaud_settings(cfg)
    except config.ConfigError as exc:
        return _fail(str(exc), args.json, EXIT_CONFIG)
    tz, now = _zone(cfg), datetime.now(UTC)
    nothing = "nothing installed"
    raw = sys.stdin.buffer.read(tokens.MAX_IMPORT + 1)
    if len(raw) > tokens.MAX_IMPORT:
        return _fail(f"the token file is larger than 64 KiB: {nothing}", args.json, EXIT_SIGN_IN)
    data: object = None
    with contextlib.suppress(ValueError, RecursionError):  # never the parser's text: it quotes
        data = json.loads(raw)
    if data is None:
        return _fail(f"the token file isn't JSON: {nothing}", args.json, EXIT_SIGN_IN)
    try:
        new = tokens.from_vendor(data, now=now)
    except tokens.TokenImportError as exc:  # names the key, never a value
        return _fail(f"{exc}: {nothing}", args.json, EXIT_SIGN_IN)
    store = tokens.TokenStore(settings.token_store, token_url=settings.token_url)
    if not store.path.parent.is_dir():
        return _fail(
            f"{store.path.parent} is missing: create it with `install -d -m 700` (runbook "
            f"section 6 step 2): {nothing}",
            args.json,
            EXIT_CONFIG,
        )
    if unsafe := signin.unsafe_modes(store.path):
        return _fail("; ".join(unsafe) + f": {nothing}", args.json, EXIT_CONFIG)
    try:  # the one cheap call, before anything is written
        account = PlaudClient(tokens.StaticTokens(new), api_base=settings.api_base).current_user()
    except PlaudAuthError:
        return _fail(
            f"signed in: no · Plaud refused this sign-in: {nothing}; run `make plaud-sign-in` "
            "again",
            args.json,
            EXIT_SIGN_IN,
        )
    except PlaudError as exc:
        return _fail(
            f"Plaud couldn't be reached ({exc.message}): {nothing}; run `make plaud-sign-in` again",
            args.json,
            EXIT_UNREACHABLE,
        )
    try:
        store.install(new)
    except tokens.TokenStoreUnsafe as exc:
        return _fail(f"{exc}: {nothing}", args.json, EXIT_CONFIG)
    except LockTimeout as exc:
        return _fail(f"{exc}: {nothing}", args.json, EXIT_BUSY)
    except OSError as exc:  # never the exception's text: it names the path
        return _fail(
            f"the sign-in couldn't be written ({type(exc).__name__}): {nothing}; check the "
            "Plaud sign-in's folder and its disk",
            args.json,
            EXIT_FAILED,
        )
    unrecorded = _record(cfg, lambda db: signin.record_good(db, new.facts(), now=now))
    payload, line = _sign_in_payload(new.facts(), account, tz, signed_in=True)
    payload |= {"installed": True, "not_recorded": unrecorded}
    _emit(payload, [line, *_unrecorded(unrecorded)], args.json)
    return EXIT_OK


def cmd_accept_listing(args: argparse.Namespace) -> int:
    """§11.4 step 4's Accept this listing, as `recordings plaud accept-listing`: the held listing
    becomes the last good one, its newly absent recordings show as gone from Plaud, and imports
    resume. Refused on Status's terms, checked against the consent list as it reads now. Holds
    `plaud` (waiting up to runlock.CLI_WAIT). Unlike the UI's button, it works before the first
    catch-up: that is how a held catch-up listing is accepted (§11.6). Exit 0 accepted or nothing
    held; 1 refused; 75 busy; 78 refused because the consent list can't be read."""
    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    try:
        settings = plaud_settings(cfg)
    except config.ConfigError as exc:
        return _fail(str(exc), args.json, EXIT_CONFIG)
    state = _write_state(cfg, args.json)
    if isinstance(state, int):
        return state
    consent = read_consent(settings.consent_list)
    now = datetime.now(UTC)
    try:
        with runlock.plaud_run(state, "plaud-accept", timeout=runlock.CLI_WAIT, now=now):
            done = hold.accept(PlaudDB(state), consent=consent, now=now)
    except LockTimeout:
        return _fail(_busy(state), args.json, EXIT_BUSY)
    except NoSyncYet:
        done = hold.Accepted("nothing", hold.NOTHING_HELD)
    except (OSError, sqlite3.Error, ValueError) as exc:  # never the exception's text
        return _fail(
            f"Accept stopped ({type(exc).__name__}): check state.db's disk, then try again",
            args.json,
            EXIT_FAILED,
        )
    payload = {"result": done.state, "message": done.message, "why": done.why}
    payload["gone"] = list(done.gone)
    lines = [done.message]
    if done.why is not None and done.why not in done.message:
        lines.append(done.why)
    if done.gone:
        lines.append(f"gone from Plaud: {', '.join(done.gone)}")
    _emit(payload, lines, args.json)
    if done.state != "refused":
        return EXIT_OK
    return EXIT_CONFIG if done.why == hold.CONSENT_FIRST else EXIT_FAILED


def cmd_review(args: argparse.Namespace) -> int:
    """§11.6's `recordings plaud review`: after a restore from an older backup (which still has
    mark rows, so the worker can't tell), `rebuilt_at` is now, and every listed ID that isn't here
    is marked for review at once; every ID first listed later and made before now is marked when
    it is listed. Holds `plaud` (waiting up to runlock.CLI_WAIT). Exit 0; 1 when the index can't
    say what is here (nothing is marked); 75 busy."""
    from recordings.plaud.compare import IndexUnavailable, here_from_archive

    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    state = _write_state(cfg, args.json)
    if isinstance(state, int):
        return state
    now = datetime.now(UTC)
    try:
        with runlock.plaud_run(state, "plaud-review", timeout=runlock.CLI_WAIT, now=now):
            writer = _writer(cfg, args.json)
            if isinstance(writer, int):
                return writer
            db = PlaudDB(writer.state)
            here = here_from_archive(
                writer.index, writer.identity.uuid, writer.state, writer.archive, db
            )
            review = sorted(pid for pid in db.listing_rows() if pid not in here.by_id)
            db.record_rebuilt(at=now, by="plaud review", review=review, now=now)
    except LockTimeout:
        return _fail(_busy(state), args.json, EXIT_BUSY)
    except IndexUnavailable as exc:  # a fixed phrase
        return _fail(f"{exc}: nothing was marked", args.json, EXIT_FAILED)
    except (OSError, sqlite3.Error) as exc:  # never the exception's text
        return _fail(
            f"the review mark stopped ({type(exc).__name__}): check state.db's disk, then run it "
            "again",
            args.json,
            EXIT_FAILED,
        )
    shown = _shown(now, _zone(cfg))
    payload = {"rebuilt_at": shown, "review": len(review)}
    lines = [
        f"recorded: the sync's records were rebuilt at {shown}",
        (
            f"waiting for your review now: {len(review):,} listed recordings that aren't here; any "
            "made before now and listed later waits too (the Sync screen's Not here yet)"
        ),
    ]
    _emit(payload, lines, args.json)
    return EXIT_OK


def _release_client(settings):
    """The CLI's Plaud client for Release's listing (the worker container's sign-in)."""
    from recordings.plaud import tokens
    from recordings.plaud.client import PlaudClient

    store = tokens.TokenStore(settings.token_store, token_url=settings.token_url)
    return PlaudClient(store, api_base=settings.api_base)


def _duration(ms: int | None) -> str:
    if ms is None:
        return "duration unknown"
    minutes, seconds = divmod(round(ms / 1000), 60)
    return f"{minutes} min {seconds:02d} s" if minutes else f"{seconds} s"


def cmd_release(args: argparse.Namespace) -> int:
    """§12.2's Release without its on-screen question (§9.6): the ID, date and duration, never
    the title, and y/N on the terminal; then checked as the worker checks it (the consent list as
    it reads now, and the current title, from a fresh listing, against every enforced word).
    `--undo` keeps it out again until a sync has fetched it. Holds no lock. Exit 0 released or
    undone; 1 refused, answered no, or nothing to do; 69 Plaud unreachable; 77 sign-in needed;
    78 the consent list can't be read."""
    from recordings.plaud import release
    from recordings.plaud.cache import Cache
    from recordings.plaud.client import PlaudAuthError, PlaudError
    from recordings.refs import canonical_plaud_id
    from recordings.sources.plaud import PlaudSource

    cfg = _config(False)
    if isinstance(cfg, int):
        return cfg
    try:
        settings = plaud_settings(cfg)
    except config.ConfigError as exc:
        return _fail(str(exc), False, EXIT_CONFIG)
    state = _write_state(cfg, False)
    if isinstance(state, int):
        return state
    writer = _writer(cfg, False)
    if isinstance(writer, int):
        return writer
    db, pid, now, tz = (
        PlaudDB(state),
        canonical_plaud_id(args.plaud_id),
        datetime.now(UTC),
        _zone(cfg),
    )
    found, unavailable = listed_here(
        writer.index, writer.identity.uuid, writer.state, writer.archive, frozenset({pid})
    )
    if unavailable:
        return _fail(INDEX_UNAVAILABLE, False, EXIT_FAILED)
    here = bool(found)
    if args.undo:
        done = release.unrelease(db, args.plaud_id, here=here, now=now)
        print(done)
        return EXIT_OK if done == release.UNDONE else EXIT_FAILED
    consent = read_consent(settings.consent_list)
    early = release.may_preview(pid, consent, db, here=here, review_ok=False)
    if not early.ok:
        code = EXIT_CONFIG if early.message == release.REFUSED_STOP else EXIT_FAILED
        return _fail(early.message, False, code)
    known = None
    with contextlib.suppress(OSError, sqlite3.Error):
        known = Cache(cfg.state_path).keyed_out().get(pid)
    when = _shown(known.start_at, tz) if known and known.start_at else "date unknown"
    print(f"Plaud ID {args.plaud_id} · {when} · {_duration(known.duration_ms if known else None)}")
    print("It downloads at the next sync, and then stays in the backups.")
    try:
        answer = input("Let it in? [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        print("Nothing changed.")
        return EXIT_FAILED
    try:
        listing = PlaudSource(_release_client(settings)).list_all()
    except PlaudError as exc:  # a client built without a sign-in file, say
        listing = None
        error: PlaudError | None = exc
    else:
        error = listing.error if isinstance(listing.error, PlaudError) else None
    row = (
        None if listing is None else next((r for r in listing.rows if r.canonical_id == pid), None)
    )
    if row is None and error is not None:
        if isinstance(error, PlaudAuthError):
            return _fail(f"{error.message}: {signin_fix()}", False, EXIT_SIGN_IN)
        return _fail(f"Plaud couldn't be reached: {error.message}", False, EXIT_UNREACHABLE)
    if row is None:
        return _fail(release.NOT_LISTED, False, EXIT_FAILED)
    try:
        words = writer.keep_out_words()
    except KeepOutWordsError as exc:
        return _fail(str(exc), False, EXIT_FAILED)
    verdict = release.may_release(pid, row.title, consent, words, db, here=here)
    if not verdict.ok:
        return _fail(verdict.message, False, EXIT_FAILED)
    release.release(db, row.listed_id, now=now)
    print(release.released_line(None))
    print(f"To keep it out again before then: `recordings plaud release {args.plaud_id} --undo`")
    return EXIT_OK


def signin_fix() -> str:
    from recordings.plaud import signin

    return signin.SIGN_IN_FIX


def cmd_sync(args: argparse.Namespace) -> int:
    """§9.6's three syncs, each in the worker container, never by a request row:
    - `--dry-run [--list]`: lists and classifies (a Compare: it imports nothing and never moves
      the last good listing), and prints both equations, the consent-list match, why the missing
      ones are missing, the census, and any hold and why; `--list` adds each listed ID by bucket,
      date and duration, never a title. Holds `plaud` (`plaud-compare`).
    - `--catch-up`: the first import, supervised (§11.6): `ops` and `plaud` in one hold
      (`plaud-catch-up`), no import cap; a held listing stops it (then `accept-listing` and run
      it again); refused once done. It sets `catch_up_done_at` when it ends without a run-level
      failure, and the schedule starts by itself.
    - plain `sync`: a sync now, with no cap, refused before the first catch-up or while a hold
      is on. Holds `plaud` (`plaud-sync`).
    Exit codes (§9.6): 0 ok; 1 failure (a hold, a bad listing, a refusal); 2 partial (the listing
    was good, but some recordings failed or aren't here); 69 Plaud unreachable or rate limited;
    75 busy; 77 sign-in needed; 78 configuration or the consent list."""
    from recordings.disk import disk_thresholds
    from recordings.plaud import sync
    from recordings.plaud.client import PlaudClient
    from recordings.plaud.gate import gate_now
    from recordings.plaud.tokens import TokenStore

    if args.list and not args.dry_run:
        return _fail("--list goes with --dry-run", args.json, EXIT_FAILED)
    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    try:
        settings = plaud_settings(cfg)
        stop_free = disk_thresholds(cfg)["stop"]
    except config.ConfigError as exc:
        return _fail(str(exc), args.json, EXIT_CONFIG)
    if not settings.keep_out_word_ok:
        return _fail(BAD_WORD, args.json, EXIT_CONFIG)
    state = _write_state(cfg, args.json)
    if isinstance(state, int):
        return state
    tz = _zone(cfg)
    if args.full:
        return _sync_full(args, cfg, settings, state, stop_free)
    mode = "compare" if args.dry_run else "catch-up" if args.catch_up else "sync"
    job = {"compare": "plaud-compare", "catch-up": "plaud-catch-up", "sync": "plaud-sync"}[mode]
    db = PlaudDB(state)
    try:
        gate = gate_now(db)
        done_at = parse_stamp(db.get(K_CATCH_UP_DONE))
    except (NoSyncYet, sqlite3.Error) as exc:
        return _fail(f"can't read state.db now ({type(exc).__name__})", args.json, EXIT_FAILED)
    if mode == "catch-up" and gate.open:
        when = _shown(done_at, tz) or "an earlier run"
        return _fail(
            f"the first catch-up is already done ({when}): use `recordings plaud sync`",
            args.json,
            EXIT_FAILED,
        )
    if mode == "sync" and not gate.open:
        return _fail(sync.NOT_DONE, args.json, EXIT_FAILED)
    now = datetime.now(UTC)
    printer = None if args.json or mode == "compare" else _progress_printer()
    try:
        with runlock.plaud_run(
            state, job, timeout=runlock.CLI_WAIT, now=now, with_ops=mode == "catch-up"
        ):
            writer = _writer(cfg, args.json)
            if isinstance(writer, int):
                return writer
            store = TokenStore(settings.token_store, token_url=settings.token_url)
            client = PlaudClient(store, api_base=settings.api_base)
            # A long plain sync serves the light turn and single-recording requests between two
            # recordings, as the worker's long runs do (§9.2); the dry run and the catch-up don't
            # (the gate holds every turn while the catch-up runs).
            between = (
                _long_run(cfg, writer=writer, client=client).long_between
                if mode == "sync"
                else None
            )
            run = sync.Run(
                writer=writer,
                client=client,
                settings=settings,
                timezone_name=cfg.default_timezone,
                stop_free_percent=stop_free,
                late_consent=True,
                progress=printer,
                between=between,
            )
            report = sync.run_sync(run, mode=mode)
            if mode != "compare":
                sync.record_ops_run(state, job, report, finished=datetime.now(UTC))
    except LockTimeout:
        return _fail(_busy(state), args.json, EXIT_BUSY)
    except (OSError, sqlite3.Error) as exc:  # never the exception's text: it names paths or rows
        return _fail(
            f"the sync stopped ({type(exc).__name__}): check the archive disk and state.db, then "
            "run it again",
            args.json,
            EXIT_FAILED,
        )
    lines = _sync_lines(report, tz, listed=args.list, mode=mode)
    _emit(_sync_payload(report, tz, listed=args.list), lines, args.json)
    return report.exit_code


def _long_run(cfg: config.Config, *, writer: Writer, client):
    """The worker's loop code for this CLI run (`worker.Worker.for_cli`): its `long_between`."""
    from recordings.worker import Worker

    return Worker.for_cli(cfg, os.environ, writer=writer, client=client)


def _cli_run(cfg, settings, stop_free: float, *, writer: Writer, between: bool = True, **kw):
    """A sync.Run for a CLI long run: its own client, and the worker's `long_between`."""
    from recordings.plaud import sync
    from recordings.plaud.client import PlaudClient
    from recordings.plaud.tokens import TokenStore

    store = TokenStore(settings.token_store, token_url=settings.token_url)
    client = PlaudClient(store, api_base=settings.api_base)
    long_run = _long_run(cfg, writer=writer, client=client) if between else None
    return sync.Run(
        writer=writer,
        client=client,
        settings=settings,
        timezone_name=cfg.default_timezone,
        stop_free_percent=stop_free,
        late_consent=True,
        between=long_run.long_between if long_run is not None else None,
        **kw,
    )


def _sync_full(args: argparse.Namespace, cfg, settings, state: State, stop_free: float) -> int:
    """`recordings plaud sync --full` (§14.6): Sync everything, under `plaud` (`plaud-full`),
    refused before the first catch-up. An unfinished one resumes. Exit 0 when the listing was good
    and every listed recording is excluded, parked, flagged or here and checked; 2 otherwise,
    with counts (the flagged named by archive ID); a run-level failure exits as a sync's would."""
    from recordings.plaud import full, sync
    from recordings.plaud.gate import gate_now
    from recordings.worker import record_full

    try:
        if not gate_now(PlaudDB(state)).open:
            return _fail(sync.NOT_DONE, args.json, EXIT_FAILED)
    except (NoSyncYet, sqlite3.Error) as exc:
        return _fail(f"can't read state.db now ({type(exc).__name__})", args.json, EXIT_FAILED)
    now = datetime.now(UTC)
    try:
        with runlock.plaud_run(state, "plaud-full", timeout=runlock.CLI_WAIT, now=now):
            writer = _writer(cfg, args.json)
            if isinstance(writer, int):
                return writer
            run = _cli_run(cfg, settings, stop_free, writer=writer)
            report = full.run_full(run, by="cli")
            record_full(state, report, finished=datetime.now(UTC))
    except LockTimeout:
        return _fail(_busy(state), args.json, EXIT_BUSY)
    except (OSError, sqlite3.Error) as exc:  # never the exception's text: it names paths or rows
        return _fail(
            f"Sync everything stopped ({type(exc).__name__}): check the archive disk and "
            "state.db, then run it again (it resumes)",
            args.json,
            EXIT_FAILED,
        )
    lines = [f"result: {report.line()}"]
    if report.resumed:
        lines.append(f"resumed the run started {_shown(report.started, _zone(cfg))}")
    counts = report.counts()
    if counts:
        lines.append("listed: " + ", ".join(f"{state_} {n:,}" for state_, n in counts.items()))
    lines.append(f"checked {report.checked:,} of {report.covered:,} the sweep covers")
    if report.flagged:
        lines.append(
            f"flagged ({len(report.flagged):,}): {', '.join(report.flagged)}; take each out by "
            "hand, or keep it (runbook, Taking a recording out by hand)"
        )
    failed = report.to_dict()["failed_ids"]
    if failed:
        lines.append(f"failed ({len(failed):,}): {', '.join(failed)}")
    if report.manifest:
        lines.append(f"manifest: {full.MANIFESTS}/{report.manifest}")
    _emit(report.to_dict(), lines, args.json)
    return report.exit_code


def cmd_resync(args: argparse.Namespace) -> int:
    """`recordings plaud resync <id>…` (§14.6): these recordings checked now, under `plaud`
    (`plaud-resync`), a parked one too (a success unparks it). Refused before the first catch-up
    and while a hold is on. Exit 0 when each was checked (or skipped by design: kept out,
    consent-listed, gone, not from Plaud); 2 when one failed, was busy or degraded; a run-level
    stop exits as a sync's would."""
    from recordings.disk import disk_thresholds
    from recordings.ids import parse_id
    from recordings.plaud import requests, resync

    for rid in args.recording_ids:
        try:
            parse_id(rid)
        except (TypeError, ValueError):
            return _fail(f"not an archive ID: {rid}", args.json, EXIT_FAILED)
    if len(set(args.recording_ids)) > requests.MAX_RESYNC_IDS:
        return _fail(resync.TOO_MANY, args.json, EXIT_FAILED)
    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    try:
        settings = plaud_settings(cfg)
        stop_free = disk_thresholds(cfg)["stop"]
    except config.ConfigError as exc:
        return _fail(str(exc), args.json, EXIT_CONFIG)
    if not settings.keep_out_word_ok:
        return _fail(BAD_WORD, args.json, EXIT_CONFIG)
    state = _write_state(cfg, args.json)
    if isinstance(state, int):
        return state
    now = datetime.now(UTC)
    try:
        with runlock.plaud_run(state, "plaud-resync", timeout=runlock.CLI_WAIT, now=now):
            writer = _writer(cfg, args.json)
            if isinstance(writer, int):
                return writer
            run = _cli_run(cfg, settings, stop_free, writer=writer)
            report = resync.resync(run, args.recording_ids)
    except LockTimeout:
        return _fail(_busy(state), args.json, EXIT_BUSY)
    except (OSError, sqlite3.Error) as exc:  # never the exception's text: it names paths or rows
        return _fail(
            f"the re-sync stopped ({type(exc).__name__}): check the archive disk and state.db",
            args.json,
            EXIT_FAILED,
        )
    lines = [f"result: {report.line()}"]
    if len(report.requested) > 1:
        lines += [f"{rid}: {line}" for rid, line in report.each().items()]
    if report.unparked:
        lines.append(f"unparked: {', '.join(report.unparked)}")
    _emit(report.to_dict(), lines, args.json)
    return report.exit_code


def cmd_park(args: argparse.Namespace) -> int:
    """`recordings plaud park <id>… [--undo]` (§14.6): park recordings here that came from Plaud
    (the sweep and Sync everything leave them alone, Re-sync still tries them), or unpark them.
    Only state.db changes; no lock. Exit 0 when one changed, 1 when none did."""
    from recordings.ids import parse_id
    from recordings.plaud import resync

    for rid in args.recording_ids:
        try:
            parse_id(rid)
        except (TypeError, ValueError):
            return _fail(f"not an archive ID: {rid}", args.json, EXIT_FAILED)
    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    if cfg.archive_path is None:
        return _fail("set [archive] path in config.toml first", args.json, EXIT_CONFIG)
    state = _write_state(cfg, args.json)
    if isinstance(state, int):
        return state
    park = not args.undo
    try:
        found = resync.park_recordings(
            Archive(cfg.archive_path),
            PlaudDB(state),
            args.recording_ids,
            park=park,
            now=datetime.now(UTC),
        )
    except (NoSyncYet, sqlite3.Error) as exc:
        return _fail(f"can't use state.db now ({type(exc).__name__})", args.json, EXIT_FAILED)
    key = "parked" if park else "unparked"
    lines = []
    if found.done:
        lines.append(f"{key} ({len(found.done):,}): {', '.join(found.done)}")
    if found.nothing:
        why = resync.NOTHING_TO_PARK if park else resync.NOTHING_TO_UNPARK
        lines.append(f"{why} ({len(found.nothing):,}): {', '.join(found.nothing)}")
    _emit({key: list(found.done), "nothing": list(found.nothing)}, lines, args.json)
    return EXIT_OK if found.done else EXIT_FAILED


def cmd_held(args: argparse.Namespace) -> int:
    """`recordings plaud held` (§14.7): the removals reconcile holds back for review, by
    recording ID and part, as each recording's last check found them. Reads state.db only."""
    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    state = _read_state(cfg)
    if state is None:
        _emit({"held": []}, ["No sync has run yet"], args.json)
        return EXIT_OK
    try:
        held = PlaudDB(state).held_removals()
    except NoSyncYet:
        held = []
    except sqlite3.Error as exc:
        return _fail(f"can't read state.db now ({type(exc).__name__})", args.json, EXIT_FAILED)
    rows = [
        {
            "recording_id": rid,
            "part": str(h.get("slot") or ""),
            "reason": str(h.get("reason") or ""),
            "snapshot": str(h.get("snapshot") or ""),
        }
        for rid, h in held
    ]
    lines = [f"{r['recording_id']} · {r['part']} ({r['reason']})" for r in rows]
    lines.append(
        f"{len(rows):,} change{'s' if len(rows) != 1 else ''} from Plaud held for your review "
        "(the review list comes with stage 3a)"
        if rows
        else "no change from Plaud is held for review"
    )
    _emit({"held": rows}, lines, args.json)
    return EXIT_OK


def cmd_retire(args: argparse.Namespace) -> int:
    """`recordings plaud retire [--undo]` (§17.5, DAN-15's end): the sync retired, so no
    scheduled sync or monthly Sync everything runs, and Status says "retired" instead of
    "Scheduled sync is off" for good. A lapsed account then fails nothing every 20 minutes."""
    from recordings.plaud import full

    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    state = _write_state(cfg, args.json)
    if isinstance(state, int):
        return state
    db, now = PlaudDB(state), datetime.now(UTC)
    try:
        changed = full.retire(db, now=now, undo=args.undo)
        since = full.retired(db)
    except (NoSyncYet, sqlite3.Error) as exc:
        return _fail(f"can't use state.db now ({type(exc).__name__})", args.json, EXIT_FAILED)
    if since is None:
        line = "the Plaud sync isn't retired" + (" (no longer)" if changed else "")
    else:
        line = (
            f"the Plaud sync is retired since {_shown(since, _zone(cfg))}: no scheduled sync or "
            "Sync everything runs (runbook, DAN-15)"
        )
    _emit({"retired": since.isoformat() if since else None, "changed": changed}, [line], args.json)
    return EXIT_OK


def _progress_printer() -> Callable[[int, int], None]:
    """A sync's or the catch-up's progress on stderr, by number only: "importing 5 of 31"."""

    def show(done: int, total: int) -> None:
        if done < total:
            print(f"importing {done + 1} of {total}", file=sys.stderr, flush=True)

    return show


_BUCKET_ORDER = ("here", "waiting", "missing", "excluded")


def _missing_by_state(report) -> dict[str, int]:
    from collections import Counter

    if report.compare is None:
        return {}
    counted = Counter(r.state for r in report.compare.rows if r.bucket == "missing")
    return dict(sorted(counted.items(), key=lambda kv: (-kv[1], kv[0] or "")))


def _rows(report, tz: tzinfo) -> list[dict]:
    """Every listed ID, by bucket then time: its ID as listed, date, time and duration, and its
    state, why it is excluded or the recordings holding it. Never a title."""
    if report.compare is None:
        return []
    order = {b: n for n, b in enumerate(_BUCKET_ORDER)}
    rows = sorted(
        report.compare.rows,
        key=lambda r: (
            order.get(r.bucket, 9),
            r.listed.recorded_at or datetime.min.replace(tzinfo=UTC),
        ),
    )
    return [
        {
            "bucket": r.bucket,
            "plaud_id": r.listed.listed_id,
            "at": _shown(r.listed.recorded_at, tz),
            "duration": _duration(r.listed.duration_ms),
            "why": r.state if r.bucket in ("waiting", "missing") else r.excluded_by,
            "recording_ids": list(r.recording_ids),
            "arrives_private": r.arrives_private,
        }
        for r in rows
    ]


def _sync_payload(report, tz: tzinfo, *, listed: bool) -> dict:
    payload = report.to_dict()
    if report.compare is not None:
        payload["missing_by_state"] = _missing_by_state(report)
        payload["census"] = dict(report.compare.census)
        payload["repeats"], payload["malformed"] = report.compare.repeats, report.compare.malformed
    payload["kept_lines"] = list(report.kept_lines)
    if report.measured is not None and report.measured.held:
        payload["accept_refused"] = report.measured.refused
    if listed:
        payload["rows"] = _rows(report, tz)
    return payload


def _sync_lines(report, tz: tzinfo, *, listed: bool, mode: str) -> list[str]:
    from recordings.plaud import compare

    lines: list[str] = []
    if report.compare is not None:
        c = report.compare
        lines += compare.lines(c)
        for state, n in _missing_by_state(report).items():
            lines.append(f"missing {n:,}: {state}")
        if c.census:
            lines.append("ID forms: " + ", ".join(f"{form} {n:,}" for form, n in c.census.items()))
        if c.repeats or c.malformed:
            lines.append(f"repeats {c.repeats:,} · malformed rows {c.malformed:,}")
    if report.kept_lines:
        lines.append(kept_lines_note(report.kept_lines))
    m = report.measured
    if m is not None and m.held:
        lines.append(m.sentence())
        if m.refused:
            lines.append(f"Accept refused: {m.refused}")
    if mode != "compare":
        lines.append(f"result: {report.phrase}")
        lines.append(
            f"imported {report.imported:,} ({report.private:,} arrived private) · failed "
            f"{report.failed:,} · busy {report.busy:,} · not tried {report.left:,}"
        )
        failed = [r.plaud_id for r in report.results if r.outcome == "failed"]
        if failed:
            lines.append(f"failed: {', '.join(failed)}")
    else:
        lines.append(f"result: {report.phrase}")
        if report.compare is not None:
            first = sum(1 for r in report.compare.rows if r.state == compare.GATE_WAIT)
            if first:
                lines.append(f"the first catch-up would import {first:,}")
            else:
                lines.append(f"the next sync would import {report.compare.equation['waiting']:,}")
    lines += report.notes
    if mode == "catch-up":
        lines.append(
            "the first catch-up is done: the schedule starts by itself"
            if report.catch_up_done
            else "the first catch-up didn't finish: run `recordings plaud sync --catch-up` again "
            "(runbook, section 8)"
        )
    if listed:
        for row in _rows(report, tz):
            held_by = ", ".join(row["recording_ids"]) or row["why"] or ""
            lock = " · arrives private" if row["arrives_private"] else ""
            lines.append(
                f"{row['bucket']:<8} {row['plaud_id']} · {row['at'] or 'time unknown'} · "
                f"{row['duration']} · {held_by}{lock}"
            )
    return lines


COMMANDS = {
    "consent": cmd_consent,
    "baseline": cmd_baseline,
    "keep-out-words": cmd_keep_out_words,
    "check": cmd_check,
    "breaker": cmd_breaker,
    "token-import": cmd_token_import,
    "accept-listing": cmd_accept_listing,
    "release": cmd_release,
    "review": cmd_review,
    "sync": cmd_sync,
    "resync": cmd_resync,
    "park": cmd_park,
    "held": cmd_held,
    "retire": cmd_retire,
}


def run(args: argparse.Namespace) -> int:
    command = COMMANDS.get(getattr(args, "plaud_command", None) or "")
    if command is None:
        args.plaud_parser.print_help(sys.stdout)
        return EXIT_OK
    return command(args)
