"""`recordings` command line. Agents use this first (spec §10); commands take --json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from recordings import __version__, schemas, selfdoc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recordings", description="Core tools for the recordings archive."
    )
    parser.add_argument("--version", action="version", version=f"recordings {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("schemas", help="regenerate or check the committed JSON Schemas")
    p.add_argument("--write", action="store_true", help="rewrite the package's schema files")
    p.add_argument("--check", action="store_true", help="exit 1 if they are stale")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("docs", help="write README/AGENTS/FORMAT and schemas into an archive")
    p.add_argument("archive", type=Path)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("validate", help="check every recording.json and rendition")
    p.add_argument("archive", type=Path)
    p.add_argument("--json", action="store_true")
    return parser


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")


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
    _emit({"written": selfdoc.write_docs(args.archive)}, args.json)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    problems = selfdoc.validate(args.archive)
    _emit({"problems": problems}, args.json)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    commands = {"schemas": cmd_schemas, "docs": cmd_docs, "validate": cmd_validate}
    if args.command in commands:
        return commands[args.command](args)
    parser.print_help(sys.stdout)
    return 0
