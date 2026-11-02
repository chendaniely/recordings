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
from recordings.plaud.checks import K_CONSENT, K_LAST_GOOD_LISTING, NoSyncYet, PlaudDB
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
        "keep-out words (before the catch-up)",
    )
    c.add_argument("--json", action="store_true")

    c = plaud.add_parser("keep-out-words", help="list the keep-out words enforced, or drop one")
    c.add_argument("--drop", metavar="WORD", help="stop enforcing an old word")
    c.add_argument("--json", action="store_true")

    c = plaud.add_parser(
        "check",
        help="one signed-in read: whether the Plaud sign-in works, its expiry, and whether it "
        "can refresh",
    )
    c.add_argument("--json", action="store_true")

    c = plaud.add_parser(
        "accept-listing",
        help="Accept this listing: clear a listing hold, as Status's button does, refused on the "
        "same terms (also before the first catch-up, for the catch-up's own listing)",
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
    """§14.5's privacy part. Holds `plaud`, waiting up to runlock.CLI_WAIT. Exit 1 when a
    recording couldn't be written, the enforced keep-out words can't be read (nothing is checked
    then), or an OSError or a sqlite3 error stopped the run outside its per-recording loop (a
    fixed phrase with the class, never the exception's text: §9.6); 75 when only locked ones were
    left."""
    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
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
    _emit(report.to_dict(), lines, args.json)
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


COMMANDS = {
    "consent": cmd_consent,
    "baseline": cmd_baseline,
    "keep-out-words": cmd_keep_out_words,
    "check": cmd_check,
    "token-import": cmd_token_import,
    "accept-listing": cmd_accept_listing,
    "release": cmd_release,
}


def run(args: argparse.Namespace) -> int:
    command = COMMANDS.get(getattr(args, "plaud_command", None) or "")
    if command is None:
        args.plaud_parser.print_help(sys.stdout)
        return EXIT_OK
    return command(args)
