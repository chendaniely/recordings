"""`recordings` command line. Agents use this first (spec §10); commands take --json."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from recordings import __version__, alerts, backup, config, ops, schemas, selfdoc, worker
from recordings.disk import disk_status, disk_thresholds
from recordings.doctor import ROLES, doctor
from recordings.index import Index, IndexRefused
from recordings.init import InitError, adopt_archive, init_archive
from recordings.locks import OPS_LOCK, Locks, LockTimeout
from recordings.plaud import commands as plaud_commands
from recordings.plaud.consent import ConsentListError, consent_list_path, load_consent_list
from recordings.sentinel import SentinelError, read_sentinel
from recordings.sources.audio_router import CatalogError, ImportStopped, plan, run
from recordings.state import State, StateError
from recordings.writer import Writer, WriterError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recordings", description="Core tools for the recordings archive."
    )
    parser.add_argument("--version", action="version", version=f"recordings {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("init", help="set up a new, empty archive, once: its sentinel and state.db")
    p.add_argument(
        "--adopt",
        action="store_true",
        help="recreate a lost state.db for the existing archive, from its archive.json",
    )
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("schemas", help="regenerate or check the committed JSON Schemas")
    p.add_argument("--write", action="store_true", help="rewrite the package's schema files")
    p.add_argument("--check", action="store_true", help="exit 1 if they are stale")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("docs", help="write README/AGENTS/FORMAT and schemas into the archive")
    p.add_argument(
        "archive",
        type=Path,
        nargs="?",
        help="optional: it must be this machine's archive, [archive] path",
    )
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("validate", help="check every recording.json and rendition")
    p.add_argument("archive", type=Path)
    p.add_argument("--deep", action="store_true", help="also check every file's SHA-256")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser(
        "reindex",
        help="accept outside edits, rebuild the derived index.db and build any "
        "missing Plaud outputs",
    )
    p.add_argument(
        "--allow-empty",
        action="store_true",
        help="let the index become empty (refused by default: is the archive mounted?)",
    )
    p.add_argument(
        "--allow-missing",
        action="store_true",
        help="rebuild even though recordings state.db knows are gone from the archive "
        "(needed after a deliberate deletion, until stage 3 adds forget)",
    )
    p.add_argument("--json", action="store_true")

    p = sub.add_parser(
        "doctor", help="check config.toml, the archive, state and secrets (presence only)"
    )
    p.add_argument(
        "--role",
        choices=ROLES,
        help="the service it checks: web, backup or all (default: $RECORDINGS_ROLE, else all)",
    )
    p.add_argument("--json", action="store_true")

    p = sub.add_parser(
        "import-audio-router",
        help="import a read-only copy of audio-router's archive (dry run first)",
    )
    p.add_argument("path", type=Path, help="the copy of audio-router's media root")
    p.add_argument("--dry-run", action="store_true", help="report what would happen; write nothing")
    p.add_argument(
        "--allow-unplaced",
        action="store_true",
        help="import what can be placed even when some rows can't",
    )
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("backup", help="restic backups to the NAS (spec §15.1)")
    p.add_argument("action", choices=["init", "run", "check", "prune", "restore-test"])
    p.add_argument(
        "--tag",
        action="append",
        default=[],
        help="an extra snapshot tag, such as pre-import (with run)",
    )
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--debug",
        action="store_true",
        help="print a failed job's full error (Status and alerts carry its type only)",
    )

    p = sub.add_parser("mirror", help="rsync the archive to the NAS's read-only mirror, now")
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--debug",
        action="store_true",
        help="print a failed run's full error, and for a refused run the recordings it "
        "would delete from (Status and alerts carry the error's type only)",
    )
    p.add_argument(
        "--max-delete",
        type=int,
        metavar="N",
        help="allow up to N deletions in this run only ([mirror] max_delete stays)",
    )

    p = sub.add_parser("ops", help="run the scheduled jobs that are due (--loop: forever)")
    p.add_argument("--loop", action="store_true", help="the backup service's main loop")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("alert-test", help="send a test alert and log a dead-man's switch ping")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser(
        "worker", help="the Plaud worker's loop (stage 2b); --alive is its health check"
    )
    p.add_argument(
        "--alive",
        action="store_true",
        help="exit 0 if the worker's heartbeat and progress are recent, else 1",
    )

    plaud_commands.add_parser(sub)  # `recordings plaud …` (stage 2b): its own subcommands
    # No abbreviated flags: `doctor --rol web` would otherwise work until a second option shares
    # the prefix, and the runbook test could not see a flag renamed to an extension of the old one.
    # (A subparser's allow_abbrev is its own: it doesn't inherit the parent's.)
    for each in (parser, *sub.choices.values()):
        each.allow_abbrev = False
    return parser


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")


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
        return _fail(str(exc), as_json, 78)


def cmd_init(args: argparse.Namespace) -> int:
    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    missing = [
        name
        for name, value in (
            ("[archive] path", cfg.archive_path),
            ("[state] path", cfg.state_path),
            ("[archive] writer_id", cfg.writer_id),
        )
        if value is None
    ]
    if missing:
        return _fail("set " + ", ".join(missing) + " in config.toml first", args.json, 78)
    setup = adopt_archive if args.adopt else init_archive
    try:
        identity = setup(cfg.archive_path, cfg.state_path, writer_id=cfg.writer_id)
    except (InitError, StateError) as exc:
        return _fail(str(exc), args.json, 1)
    _emit(
        {
            "archive": str(cfg.archive_path),
            "uuid": identity.uuid,
            "writer_id": cfg.writer_id,
            "adopted": args.adopt,
        },
        args.json,
    )
    return 0


def _writer(as_json: bool, *, docs: bool = True, rebuild: bool = True) -> Writer | int:
    cfg = _config(as_json)
    if isinstance(cfg, int):
        return cfg
    try:
        return Writer.open(cfg, docs=docs, rebuild=rebuild)
    except LockTimeout as exc:  # busy, not misconfigured: 75 (EX_TEMPFAIL), as the import uses
        return _fail(str(exc), as_json, 75)
    except (
        WriterError,
        OSError,
        sqlite3.Error,
    ) as exc:  # docs write: a read-only mount, a full disk
        return _fail(str(exc), as_json, 78)


def cmd_reindex(args: argparse.Namespace) -> int:
    # The recovery command writes no docs, and rebuilds the index itself, on its own terms.
    writer = _writer(args.json, docs=False, rebuild=False)
    if isinstance(writer, int):
        return writer
    try:
        report = writer.reindex(allow_empty=args.allow_empty, allow_missing=args.allow_missing)
    except LockTimeout as exc:
        return _fail(str(exc), args.json, 75)
    except (IndexRefused, WriterError, OSError, sqlite3.Error) as exc:
        return _fail(str(exc), args.json, 1)
    _emit(report.to_dict(), args.json)
    # A recording the rebuild left out (not valid, or its id doesn't match its folder) is
    # dropped from the index: exit 1 so a script notices (§11).
    # A Plaud recording that could not be reconciled is a failure too: its outputs are missing.
    return (
        1 if report.invalid or report.problems or any(r.problem for r in report.reconciled) else 0
    )


def cmd_schemas(args: argparse.Namespace) -> int:
    target = schemas.package_dir()
    if args.write:
        written = schemas.write(target)
        _emit({"written": [p.name for p in written]}, args.json)
        return 0
    out_of_date = schemas.stale(target)
    _emit({"stale": out_of_date}, args.json)
    return 1 if (args.check and out_of_date) else 0


def cmd_docs(args: argparse.Namespace) -> int:
    """§6.7: the docs go only into the archive this machine writes, through its Writer. The Mac's
    mirror carries archive.json too, but it has no state.db, so it is refused."""
    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    if args.archive is not None and (
        cfg.archive_path is None or args.archive.resolve() != cfg.archive_path.resolve()
    ):
        return _fail(
            f"{args.archive} is not this machine's archive ([archive] path in "
            "config.toml): only the archive's writer writes its docs",
            args.json,
            78,
        )
    writer = _writer(args.json, docs=False)
    if isinstance(writer, int):
        return writer
    try:
        written = writer.write_docs()
    except LockTimeout as exc:
        return _fail(str(exc), args.json, 75)
    except OSError as exc:
        return _fail(str(exc), args.json, 1)
    _emit({"written": written}, args.json)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    problems = selfdoc.validate(args.archive, deep=args.deep)
    _emit({"problems": problems}, args.json)
    return 1 if problems else 0


def cmd_doctor(args: argparse.Namespace) -> int:
    try:
        report = doctor(os.environ, role=args.role)
    except Exception as exc:  # noqa: BLE001  a config shape no check expects: never a traceback
        # Its output is pasted into issues and chats, so the error's type and the check it
        # stopped in, never its text (as ops.failure_detail does).
        where = next(
            (
                frame.name
                for frame in reversed(traceback.extract_tb(exc.__traceback__))
                if frame.filename.endswith("doctor.py")
            ),
            "doctor",
        )
        return _fail(
            f"doctor stopped in {where} on {type(exc).__name__}: a setting in "
            "config.toml may have the wrong type (compare it with "
            "config.example.toml)",
            args.json,
            70,
        )
    _emit(report, args.json)
    return 78 if report["problems"] else 0  # 78 = EX_CONFIG, as audio-router's doctor uses


def _matching_index(cfg: config.Config) -> Index | None:
    """The archive's index, for a dry run's matches, when it is this archive's."""
    if cfg.index_path is None or cfg.archive_path is None:
        return None
    try:
        identity = read_sentinel(cfg.archive_path)
    except SentinelError:
        return None
    index = Index(cfg.index_path)
    return index if index.archive_uuid() == identity.uuid else None


OPS_BUSY = (
    "another job holds the ops lock (a backup, the mirror or a check): run the import "
    "again when it finishes"
)


def cmd_import_audio_router(args: argparse.Namespace) -> int:
    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    try:  # fail-closed (§9.1): no list, an unreadable one or an empty one stops both runs
        consent = load_consent_list(consent_list_path(cfg.data))
    except ConsentListError as exc:
        return _fail(str(exc), args.json, 78)
    if args.dry_run:
        index = _matching_index(cfg)
        try:
            the_plan = plan(
                args.path, index, consent=consent, default_timezone=cfg.default_timezone
            )
        except CatalogError as exc:
            return _fail(str(exc), args.json, 1)
        _emit(
            {**the_plan.report(), "dry_run": True, "matched_against_archive": index is not None},
            args.json,
        )
        return 0 if the_plan.ok else 1
    try:
        thresholds = disk_thresholds(cfg)
    except config.ConfigError as exc:
        return _fail(str(exc), args.json, 78)
    writer = _writer(args.json)
    if isinstance(writer, int):
        return writer
    try:  # a peek, never a hold: plan() can take hours, and run() takes the lock itself (a
        # Locks is re-entrant only within one instance). A job that starts in between is run()'s.
        with Locks(writer.state.locks_dir, timeout=0).hold(OPS_LOCK):
            pass
    except LockTimeout:
        return _fail(OPS_BUSY, args.json, 75)
    try:
        the_plan = plan(
            args.path, writer.index, consent=consent, default_timezone=cfg.default_timezone
        )
    except CatalogError as exc:
        return _fail(str(exc), args.json, 1)
    if the_plan.problems:
        return _fail("; ".join(the_plan.problems), args.json, 1)
    unplaced = the_plan.counts()["unplaced"]
    if unplaced and not args.allow_unplaced:
        return _fail(
            f"{unplaced} catalog rows can't be placed (see --dry-run): fix them, or "
            "pass --allow-unplaced to import the rest",
            args.json,
            1,
        )

    def guard():
        return disk_status(writer.root, expected_uuid=writer.identity.uuid, **thresholds)

    try:
        result = run(
            the_plan,
            writer,
            now=datetime.now(UTC),
            disk=guard,
            stop_free_percent=thresholds["stop"],
        )
    except ImportStopped as exc:
        _emit(
            {**the_plan.report(), "imported": exc.report.to_dict(), "stopped": str(exc)}, args.json
        )
        return 1
    except LockTimeout:
        return _fail(OPS_BUSY, args.json, 75)
    _emit({**the_plan.report(), "imported": result.to_dict()}, args.json)
    return 1 if result.failed else 0


def _ops_context(as_json: bool) -> ops.Context | int:
    """The jobs' Context, with restic waiting up to ops.CLI_RETRY_LOCK for its own lock (the
    NAS's weekly retention task may hold it). run_job waits ops.CLI_WAIT for the ops lock unless
    told otherwise; the loop (Task 13) passes lock_timeout=0."""
    cfg = _config(as_json)
    if isinstance(cfg, int):
        return cfg
    if cfg.state_path is None:
        return _fail("set [state] path in config.toml first", as_json, 78)
    try:
        state = State.open(cfg.state_path)
    except StateError as exc:
        return _fail(str(exc), as_json, 78)
    return ops.Context(cfg=cfg, environ=os.environ, state=state, retry_lock=ops.CLI_RETRY_LOCK)


def _debug_logging(on: bool) -> None:
    """--debug: a failed job's full error, on stderr. Status and alerts never carry it."""
    if on:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")


def _job_result(result: ops.JobResult, as_json: bool) -> int:
    _emit({"job": result.job, "ok": result.ok, "detail": result.detail}, as_json)
    if result.detail.startswith(ops.BUSY):  # also "skipped: busy (repository locked)"
        return 75  # EX_TEMPFAIL: another job held the ops lock for ops.CLI_WAIT; try again
    return 0 if result.ok else 1


def cmd_backup(args: argparse.Namespace) -> int:
    _debug_logging(args.debug)
    ctx = _ops_context(args.json)
    if isinstance(ctx, int):
        return ctx
    with ops.sigterm_forwarded():
        if args.action == "init":
            try:
                detail = backup.run_init(ops.backup_config(ctx), ctx.environ, runner=ctx.runner)
            except config.ConfigError as exc:  # our own text about config.toml: it names no secret
                return _fail(str(exc), args.json, 1)
            except (backup.BackupError, OSError) as exc:
                # Fixed text, never restic's own words (a path, a server's reply); a restic that
                # isn't installed is an OSError, not a traceback. --debug prints the full error.
                logging.getLogger(__name__).debug("backup init failed", exc_info=True)
                return _fail(
                    f"backup init failed ({ops.failure_detail(exc)}); run with --debug "
                    "for the full error",
                    args.json,
                    1,
                )
            _emit({"job": "backup init", "ok": True, "detail": detail}, args.json)
            return 0
        job = "backup" if args.action == "run" else args.action
        return _job_result(ops.run_job(ctx, job, tags=tuple(args.tag)), args.json)


def cmd_mirror(args: argparse.Namespace) -> int:
    _debug_logging(args.debug)
    if args.max_delete is not None and args.max_delete < 0:
        return _fail("--max-delete must be a whole number, 0 or more", args.json, 2)  # as argparse
    ctx = _ops_context(args.json)
    if isinstance(ctx, int):
        return ctx
    # Run from a terminal, it may run as long as it needs: a first mirror can outlast the loop's
    # cap (mirror.TIMEOUT), and rsync's own --timeout still ends a stalled one.
    ctx.mirror_timeout, ctx.mirror_max_delete = None, args.max_delete
    with ops.sigterm_forwarded():
        return _job_result(ops.run_job(ctx, "mirror"), args.json)


def cmd_ops(args: argparse.Namespace) -> int:
    ctx = _ops_context(args.json)
    if isinstance(ctx, int):
        return ctx
    if args.loop:
        ctx.retry_lock = None  # §15.1: the loop never waits for restic's lock; the CLI does
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )  # the container clock is UTC: say so
        # docker stop: SIGTERM goes on to a running restic or rsync, which stops cleanly and
        # drops its lock; then this process stops. The job it was in is neither recorded nor
        # alerted, and the next start runs it again.
        with ops.sigterm_forwarded():
            ops.loop(ctx)
        return 0
    with ops.sigterm_forwarded():
        results = ops.run_due(ctx)
    _emit({"ran": [{"job": r.job, "ok": r.ok, "detail": r.detail} for r in results]}, args.json)
    failed = [r for r in results if not r.ok]
    if failed and all(r.detail.startswith(ops.BUSY) for r in failed):
        return 75  # EX_TEMPFAIL, as _job_result: every turn was busy (the ops lock, or restic's)
    return 1 if failed else 0


def cmd_alert_test(args: argparse.Namespace) -> int:
    cfg = _config(args.json)
    if isinstance(cfg, int):
        return cfg
    try:
        ac = alerts.from_config(cfg, os.environ)
    except config.ConfigError as exc:
        return _fail(str(exc), args.json, 78)
    if ac.topic and not ac.ntfy_server:
        return _fail("RECORDINGS_NTFY_TOPIC is set but [alerts] ntfy_server is not", args.json, 78)
    if not ac.enabled and not ac.deadman_url:
        return _fail(
            "set [alerts] ntfy_server and RECORDINGS_NTFY_TOPIC_FILE, or "
            "RECORDINGS_DEADMAN_URL_FILE, first",
            args.json,
            78,
        )
    # Each part is tested when it is set up: null is "not configured", false is "failed".
    sent = (
        alerts.send(
            ac,
            title="recordings: test alert",
            message="If you see this, alerts work.",
            priority=3,
            tags=("white_check_mark",),
        )
        if ac.enabled
        else None
    )
    # /log records the test without changing the check's state, so it can never hide an outage.
    logged = (
        alerts.ping_deadman(ac, "log", message="recordings alert-test") if ac.deadman_url else None
    )
    _emit({"ntfy": sent, "deadman": logged}, args.json)
    return 0 if sent is not False and logged is not False else 1


def cmd_worker(args: argparse.Namespace) -> int:
    """`recordings worker [--alive]` (spec §9.2): the worker container's command, and its compose
    health check."""
    return worker.main(["--alive"] if args.alive else [])


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    commands = {
        "init": cmd_init,
        "schemas": cmd_schemas,
        "docs": cmd_docs,
        "reindex": cmd_reindex,
        "validate": cmd_validate,
        "doctor": cmd_doctor,
        "import-audio-router": cmd_import_audio_router,
        "backup": cmd_backup,
        "mirror": cmd_mirror,
        "ops": cmd_ops,
        "alert-test": cmd_alert_test,
        "worker": cmd_worker,
        "plaud": plaud_commands.run,
    }
    if args.command in commands:
        return commands[args.command](args)
    parser.print_help(sys.stdout)
    return 0
