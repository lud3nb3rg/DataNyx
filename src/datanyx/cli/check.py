"""`datanyx check`."""

from __future__ import annotations

import argparse

from ..check import LANGUAGES as CHECK_LANGUAGES
from ..check import check_code, detect_language, format_diagnostics, load_tables
from ..sources import SourceError, source_kinds
from .common import (
    _add_delimiter,
    _policy,
    _read_text,
)


def _run_check(args: argparse.Namespace) -> int:
    language = args.language or (detect_language(args.code) if args.code != "-" else None)
    if language is None:
        raise SourceError("can't tell the language of the code; pass --language sql or --language python")
    if args.code == "-" and "-" in args.against:
        raise SourceError("the code and the data can't both come from stdin")
    code = _read_text(args.code)
    policy = _policy(args)
    tables, guessed = load_tables(args.against, args.table, args.source, policy, args.schema, args.recursive)
    findings = check_code(code, tables, language, args.dialect or guessed)

    print(format_diagnostics(findings, "<stdin>" if args.code == "-" else args.code, args.format))
    failed = any(item.severity == "error" for item in findings) or (args.strict and bool(findings))
    return 1 if failed else 0


def add_check(commands: argparse._SubParsersAction) -> None:
    check = commands.add_parser(
        "check",
        help="check SQL or pandas code against a schema before running it",
        description=(
            "Find misspelled columns, unknown tables, ambiguous joins and type mistakes in SQL "
            "or pandas code, using the schema of the data it works on. Nothing is run and no "
            "data is read by the code. Exits with 1 if there are errors."
        ),
    )
    check.add_argument("code", help='file with the code (.sql or .py), or "-" for stdin')
    check.add_argument(
        "--against",
        action="append",
        required=True,
        metavar="INPUT",
        help="what the code works on: a file, folder, database URL or env:NAME (repeatable)",
    )
    check.add_argument(
        "--table",
        action="append",
        default=[],
        metavar="NAME",
        help="the table name for the matching --against file, in order; a file has no name of its own in a schema",
    )
    check.add_argument("--language", choices=CHECK_LANGUAGES, help="sql or python (default: from the file extension)")
    check.add_argument("--dialect", help="SQL dialect, such as postgres, mysql, tsql, sqlite (default: guessed from --against)")
    check.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: %(default)s)")
    check.add_argument("--strict", action="store_true", help="exit with 1 for warnings too")
    check.add_argument("-r", "--recursive", action="store_true", help="when an input is a folder, also read its subfolders")
    check.add_argument("--schema", help="database schema to use (default: the database's default)")
    check.add_argument("--source", choices=source_kinds(), help="input type for --against (default: detect)")
    _add_delimiter(check)
    check.add_argument("--views", action="store_true", help="also know the database's views (names and columns only)")
    check.add_argument("--map-path", action="append", default=[], metavar="PATH", help="hide all keys or columns at a path (repeatable)")
    check.add_argument("--keep-name", action="append", default=[], metavar="NAME", help="show this name even if it looks like data (repeatable)")
