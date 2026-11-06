"""`datanyx diff`."""

from __future__ import annotations

import argparse

from ..diff import diff_schemas, has_breaking, render_diff, snapshot
from ..sources import source_kinds
from .common import (
    _add_delimiter,
    _policy,
)


def _run_diff(args: argparse.Namespace) -> int:
    policy = _policy(args)
    old = snapshot(args.old, policy, args.source, args.schema, args.recursive)
    new = snapshot(args.new, policy, args.source, args.schema, args.recursive)
    differences = diff_schemas(old, new)
    print(render_diff(differences, args.format))
    failed = {"never": False, "any": bool(differences), "breaking": has_breaking(differences)}[args.fail_on]
    return 1 if failed else 0


def add_diff(commands: argparse._SubParsersAction) -> None:
    diff = commands.add_parser(
        "diff",
        help="compare two schemas and say what changed and what could break",
        description=(
            "Compare two schemas, each a saved one (from `datanyx schema -o schema.json`) or any input "
            "DataNyx reads, and list what was added, removed or changed, sorted by whether code written "
            "against the old one could break. Exits with 1 if anything breaking changed."
        ),
    )
    diff.add_argument("old", help="the earlier schema: a saved JSON Schema file, or any input")
    diff.add_argument("new", help="the later schema, in the same forms")
    diff.add_argument(
        "--fail-on",
        choices=("breaking", "any", "never"),
        default="breaking",
        help="when to exit with 1 (default: %(default)s)",
    )
    diff.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: %(default)s)")
    diff.add_argument("-r", "--recursive", action="store_true", help="when an input is a folder, also read its subfolders")
    diff.add_argument("--schema", help="database schema to use (default: the database's default)")
    diff.add_argument("--source", choices=source_kinds(), help="input type for live inputs (default: detect)")
    _add_delimiter(diff)
    diff.add_argument("--views", action="store_true", help="also compare the database's views (names and columns only)")
    diff.add_argument("--map-path", action="append", default=[], metavar="PATH", help="hide all keys or columns at a path (repeatable)")
    diff.add_argument("--keep-name", action="append", default=[], metavar="NAME", help="show this name even if it looks like data (repeatable)")
