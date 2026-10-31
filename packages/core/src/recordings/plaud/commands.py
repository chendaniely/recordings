"""`recordings plaud …` (stage 2b spec §9.6): the sync's commands, run in the `worker` container.

The CLI there acts as the worker (§9.1): its Writer writes no docs. Every command prints counts,
IDs, line numbers and fixed phrases, never a title, and `--json` prints the same as one object.
Exit codes (§9.6): 0 ok; 1 failure; 2 partial; 69 Plaud unreachable or rate limited; 75 busy;
77 sign-in needed; 78 configuration or the consent list. `cli.py` registers `add_parser` and
dispatches to `run`; each command adds its parser block and its `COMMANDS` entry here.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from recordings import config
from recordings.archive import Archive
from recordings.index import Index
from recordings.locks import LockTimeout
from recordings.plaud import runlock
from recordings.plaud.baseline import DROPPED, baseline_privacy, drop_word, enforced_words
from recordings.plaud.checks import K_CONSENT, K_LAST_GOOD_LISTING, NoSyncYet, PlaudDB
from recordings.plaud.flags import listed_here, missed_by_2a
from recordings.plaud.keepout import read_consent_text, signature
from recordings.plaud.settings import plaud_settings
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
        "keep-out words (before the catch-up)",
    )
    c.add_argument("--json", action="store_true")

    c = plaud.add_parser("keep-out-words", help="list the keep-out words enforced, or drop one")
    c.add_argument("--drop", metavar="WORD", help="stop enforcing an old word")
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


def _state(cfg: config.Config) -> State | None:
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
    state = _state(cfg)
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
        numbers = ", ".join(str(n) for n in consent.review_lines)
        lines.append(f"lines read as kept, not excluded (check each meant keep): {numbers}")
    if in_archive is None:
        lines.append(f"in the archive: {INDEX_UNAVAILABLE}")
    for row in in_archive or ():
        note = f" ({MISSED_BY_2A})" if row["missed_by_2a"] else ""
        lines.append(f"in the archive: {row['recording_id']}{note}")
    _emit(payload, lines, args.json)
    return EXIT_CONFIG if consent.stop else EXIT_OK


def _open_state(cfg: config.Config, as_json: bool) -> State | int:
    if cfg.state_path is None:
        return _fail("set [state] path in config.toml first", as_json, EXIT_CONFIG)
    try:
        return State.open(cfg.state_path)
    except StateError as exc:
        return _fail(str(exc), as_json, EXIT_CONFIG)


def _writer(cfg: config.Config, as_json: bool) -> Writer | int:
    """The CLI's Writer in the worker container: as the worker's, it writes no docs (§9.1)."""
    try:
        return Writer.open(cfg, docs=False)
    except LockTimeout as exc:
        return _fail(str(exc), as_json, EXIT_BUSY)
    except (WriterError, OSError, sqlite3.Error) as exc:
        return _fail(str(exc), as_json, EXIT_CONFIG)


def _busy(state: State) -> str:
    """§15.6's "skipped: busy (`plaud-full`)", naming the run that holds `plaud`."""
    holder = runlock.plaud_holder(state)
    return f"skipped: busy (`{holder[0]}`)" if holder else "skipped: busy"


def cmd_baseline(args: argparse.Namespace) -> int:
    """§14.5's privacy part. Holds `plaud`, waiting up to runlock.CLI_WAIT."""
    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    state = _open_state(cfg, args.json)
    if isinstance(state, int):
        return state
    now = datetime.now(UTC)
    try:
        with runlock.plaud_run(state, "plaud-baseline", timeout=runlock.CLI_WAIT, now=now):
            writer = _writer(cfg, args.json)
            if isinstance(writer, int):
                return writer
            report = baseline_privacy(writer, PlaudDB(writer.state), now=now)
    except LockTimeout:
        return _fail(_busy(state), args.json, EXIT_BUSY)
    lines = [
        f"titles checked: {report.titles_checked:,}",
        f"private added: {report.private_added:,}",
        f"kept out late: {report.kept_out_late:,}",
        f"already seen: {report.already_seen:,}",
    ]
    if report.unwritten:
        lines.append(
            f"not written ({len(report.unwritten):,}): {', '.join(report.unwritten)}; run "
            "`recordings reindex`, then the baseline again"
        )
    _emit(report.to_dict(), lines, args.json)
    return EXIT_FAILED if report.unwritten else EXIT_OK


def cmd_keep_out_words(args: argparse.Namespace) -> int:
    """§12.2: the words enforced, or `--drop` one. Holds no lock."""
    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    try:
        settings = plaud_settings(cfg)
    except config.ConfigError as exc:
        return _fail(str(exc), args.json, EXIT_CONFIG)
    state = _open_state(cfg, args.json)
    if isinstance(state, int):
        return state
    db, code, payload = PlaudDB(state), EXIT_OK, {}
    if args.drop is not None:
        result = drop_word(db, args.drop, configured=settings.keep_out_word, now=datetime.now(UTC))
        payload["result"] = result
        code = EXIT_OK if result == DROPPED else EXIT_FAILED
    payload |= {
        "configured": settings.keep_out_word,
        "configured_ok": settings.keep_out_word_ok,
        "enforced": list(enforced_words(db)),
    }
    lines = [payload["result"]] if "result" in payload else []
    state_of = "" if settings.keep_out_word_ok else " (invalid: the worker idles)"
    lines.append(f"configured: {settings.keep_out_word}{state_of}")
    lines.append(f"enforced: {', '.join(payload['enforced']) or 'none yet'}")
    _emit(payload, lines, args.json)
    return code


COMMANDS = {
    "consent": cmd_consent,
    "baseline": cmd_baseline,
    "keep-out-words": cmd_keep_out_words,
}


def run(args: argparse.Namespace) -> int:
    command = COMMANDS.get(getattr(args, "plaud_command", None) or "")
    if command is None:
        args.plaud_parser.print_help(sys.stdout)
        return EXIT_OK
    return command(args)
