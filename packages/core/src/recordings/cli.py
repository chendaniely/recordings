"""`recordings` command line. Agents use this first (spec §10); commands take --json."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from recordings import __version__, config, schemas, selfdoc
from recordings.index import IndexRefused
from recordings.init import InitError, adopt_archive, init_archive
from recordings.locks import LockTimeout
from recordings.sentinel import SentinelError, read_sentinel
from recordings.state import StateError
from recordings.writer import Writer, WriterError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recordings", description="Core tools for the recordings archive."
    )
    parser.add_argument("--version", action="version", version=f"recordings {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("init", help="set up a new, empty archive, once: its sentinel and state.db")
    p.add_argument("--adopt", action="store_true",
                   help="recreate a lost state.db for the existing archive, from its archive.json")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("schemas", help="regenerate or check the committed JSON Schemas")
    p.add_argument("--write", action="store_true", help="rewrite the package's schema files")
    p.add_argument("--check", action="store_true", help="exit 1 if they are stale")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("docs", help="write README/AGENTS/FORMAT and schemas into the archive")
    p.add_argument("archive", type=Path, nargs="?",
                   help="optional: it must be this machine's archive, [archive] path")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("validate", help="check every recording.json and rendition")
    p.add_argument("archive", type=Path)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("reindex", help="accept outside edits, rebuild the derived index.db and build any "
                        "missing Plaud outputs")
    p.add_argument("--allow-empty", action="store_true",
                   help="let the index become empty (refused by default: is the archive mounted?)")
    p.add_argument("--allow-missing", action="store_true",
                   help="rebuild even though recordings state.db knows are gone from the archive "
                        "(needed after a deliberate deletion, until stage 3 adds forget)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("doctor", help="check config.toml, the archive and secrets (presence only)")
    p.add_argument("--json", action="store_true")
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
    missing = [name for name, value in (("[archive] path", cfg.archive_path),
                                        ("[state] path", cfg.state_path),
                                        ("[archive] writer_id", cfg.writer_id)) if value is None]
    if missing:
        return _fail("set " + ", ".join(missing) + " in config.toml first", args.json, 78)
    setup = adopt_archive if args.adopt else init_archive
    try:
        identity = setup(cfg.archive_path, cfg.state_path, writer_id=cfg.writer_id)
    except (InitError, StateError) as exc:
        return _fail(str(exc), args.json, 1)
    _emit({"archive": str(cfg.archive_path), "uuid": identity.uuid,
           "writer_id": cfg.writer_id, "adopted": args.adopt}, args.json)
    return 0


def _writer(as_json: bool, *, docs: bool = True, rebuild: bool = True) -> Writer | int:
    cfg = _config(as_json)
    if isinstance(cfg, int):
        return cfg
    try:
        return Writer.open(cfg, docs=docs, rebuild=rebuild)
    except LockTimeout as exc:  # busy, not misconfigured: 75 (EX_TEMPFAIL), as the import uses
        return _fail(str(exc), as_json, 75)
    except (WriterError, OSError, sqlite3.Error) as exc:  # docs write: a read-only mount, a full disk
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
    return 1 if report.invalid or report.problems or any(
        r.problem for r in report.reconciled) else 0


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
            cfg.archive_path is None or args.archive.resolve() != cfg.archive_path.resolve()):
        return _fail(f"{args.archive} is not this machine's archive ([archive] path in "
                     "config.toml): only the archive's writer writes its docs", args.json, 78)
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
    problems = selfdoc.validate(args.archive)
    _emit({"problems": problems}, args.json)
    return 1 if problems else 0


def cmd_doctor(args: argparse.Namespace) -> int:
    report = config.doctor(os.environ)
    _emit(report, args.json)
    return 78 if report["problems"] else 0  # 78 = EX_CONFIG, as audio-router's doctor uses


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    commands = {"init": cmd_init, "schemas": cmd_schemas, "docs": cmd_docs, "reindex": cmd_reindex, "validate": cmd_validate, "doctor": cmd_doctor}
    if args.command in commands:
        return commands[args.command](args)
    parser.print_help(sys.stdout)
    return 0
